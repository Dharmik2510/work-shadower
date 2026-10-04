"""Relevance filter: decide, for every cleaned event, whether it is part of the task.

    cleaned events ──► local rules (always) ──► Jev (optional) ──► combine ──► keep / review / drop
                                                                         └──► task segments

* **Local rules** are free and deterministic: undone actions, quick round-trip detours to an
  unrelated app, open-then-dismiss exploration, back-navigation, duplicates, long idle gaps.
* **Jev** (TypeSafe System One model) answers typed questions per event with calibrated
  probabilities: is it needed for the goal, why not, is it irreversible, does a new task start.
  Calls are parallel, retried with backoff, guarded by a circuit breaker, and any event Jev
  can't answer falls back to the local rules. Jev never blocks a recording from becoming a skill.
* **Thresholds** come from admin flags: p_drop >= drop -> drop (excluded, restorable);
  >= review -> review (kept, flagged); else keep.
* **Safety**: an irreversible action or a credential entry is never auto-dropped (max: review).
"""
from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from .config import Settings
from .skillgen import _app_name, _compact, _el, _el_key, _is_irreversible, _norm_key, _ts

log = logging.getLogger("app.filtering")

REASONS: dict[str, str] = {
    "on_task": "Directly does part of the goal (enter data, press the button that does the work).",
    "needed_navigation": "Gets to where the work happens: opening the app, page, tab or menu the next steps use.",
    "detour": "A side trip unrelated to the goal, e.g. glancing at chat, email or another app and coming back.",
    "mistake_undone": "An action that was immediately undone, reverted or corrected.",
    "exploration": "Looking around: opening something and closing/escaping it without using it, going back.",
    "duplicate": "Repeats an action that was already done with the same effect.",
    "idle_or_noise": "Accidental or meaningless input with no effect on the task.",
}
DROP_REASONS = {"detour", "mistake_undone", "exploration", "duplicate", "idle_or_noise"}
DECISIONS = ("keep", "review", "drop")


# ====================================================================== result types
@dataclass
class EventDecision:
    seq: int
    decision: str  # keep | review | drop
    reason: str
    p_drop: float
    source: str  # local | jev | jev+local
    p_irreversible: float | None = None
    p_new_task: float | None = None

    def to_step_filter(self) -> dict:
        return {"decision": self.decision, "reason": self.reason, "p_drop": round(self.p_drop, 4),
                "source": self.source}


@dataclass
class FilterResult:
    decisions: dict[int, EventDecision]
    segments: list[list[int]]  # seqs per detected task, in order
    source: str  # none | local | jev | jev+local
    model: str | None = None
    task_type: str | None = None
    jev_calls: int = 0
    jev_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        counts = {d: 0 for d in DECISIONS}
        for d in self.decisions.values():
            counts[d.decision] += 1
        return {"source": self.source, "model": self.model, "task_type": self.task_type,
                "segments": len(self.segments), "counts": counts, "jev_calls": self.jev_calls,
                "jev_failures": self.jev_failures, "duration_ms": self.duration_ms, "notes": self.notes[:10]}


@dataclass
class Thresholds:
    drop: float = 0.9
    review: float = 0.6
    split: float = 0.85
    min_segment_steps: int = 3

    def classify(self, p_drop: float) -> str:
        if p_drop >= self.drop:
            return "drop"
        if p_drop >= self.review:
            return "review"
        return "keep"


def keep_all(cleaned: list[dict]) -> FilterResult:
    decisions = {e["seq"]: EventDecision(e["seq"], "keep", _default_reason(e), 0.0, "none") for e in cleaned}
    return FilterResult(decisions, [[e["seq"] for e in cleaned]] if cleaned else [], "none")


def _default_reason(ev: dict) -> str:
    return "needed_navigation" if ev.get("type") in ("app_activate", "url_change") else "on_task"


def _is_protected(ev: dict) -> bool:
    """Never auto-drop: actions that commit something, and credential entry."""
    el = _el(ev)
    if el.get("value_kind") == "secure":
        return True
    if ev.get("type") == "key" and _norm_key(ev.get("key")) in ("cmd+return", "cmd+enter"):
        return True
    return ev.get("type") in ("click", "menu") and _is_irreversible(el.get("label"), " ".join(el.get("path") or []))


# ====================================================================== local rules
def _is_undo(ev: dict) -> bool:
    if ev.get("type") == "key" and _norm_key(ev.get("key")) == "cmd+z":
        return True
    if ev.get("type") == "menu":
        label = (_el(ev).get("label") or "").lower()
        return label.startswith("undo")
    return False


def _words(s: str | None) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower())}


def local_scores(cleaned: list[dict], goal: str | None) -> dict[int, tuple[float, str]]:
    """seq -> (p_drop, reason). Conservative: only strong patterns reach drop-level scores."""
    out: dict[int, tuple[float, str]] = {e["seq"]: (0.05, _default_reason(e)) for e in cleaned}

    def bump(seq: int, p: float, reason: str) -> None:
        if p > out[seq][0]:
            out[seq] = (p, reason)

    n = len(cleaned)
    goal_words = _words(goal)

    # 1. undo: the undo and the action it undid
    for i, ev in enumerate(cleaned):
        if _is_undo(ev):
            bump(ev["seq"], 0.93, "mistake_undone")
            for j in range(i - 1, -1, -1):
                prev = cleaned[j]
                if prev.get("type") in ("type", "click", "key", "menu") and not _is_undo(prev):
                    if _app_name(prev) == _app_name(ev):
                        bump(prev["seq"], 0.92, "mistake_undone")
                    break

    # 2. open-then-escape: a click/menu dismissed with Esc within 6s, nothing else in between
    for i in range(1, n):
        ev, prev = cleaned[i], cleaned[i - 1]
        if ev.get("type") == "key" and _norm_key(ev.get("key")) in ("escape", "esc") \
                and prev.get("type") in ("click", "menu") and _app_name(prev) == _app_name(ev) \
                and 0 <= _ts(ev) - _ts(prev) <= 6:
            bump(prev["seq"], 0.72, "exploration")
            bump(ev["seq"], 0.72, "exploration")

    # 3. back-navigation: U0 -> U1 -> back to U0 with no data entry on U1
    # a click that navigated carries the URL it led to (cleanup folds the url_change into it)
    urls = [(i, e, e.get("url") if e.get("type") == "url_change" else e.get("_expect_url"))
            for i, e in enumerate(cleaned)]
    urls = [(i, e, u) for i, e, u in urls if u]
    for (i0, a, ua), (i1, b, ub), (i2, c, uc) in zip(urls, urls[1:], urls[2:]):
        if ua == uc and ub != ua and _app_name(a) == _app_name(c):
            between = cleaned[i1:i2]
            if not any(e.get("type") == "type" for e in between):
                for e in between:
                    bump(e["seq"], 0.7, "exploration")
                bump(c["seq"], 0.7, "exploration")

    # 3b. a click that went somewhere and came straight back to the page it started on
    last_url: str | None = None
    for e in cleaned:
        trail = e.get("_urls") or []
        if e.get("type") in ("click", "menu") and len(trail) >= 2 and last_url and trail[-1] == last_url \
                and any(u != last_url for u in trail[:-1]):
            bump(e["seq"], 0.72, "exploration")
        nav = e.get("url") if e.get("type") == "url_change" else (trail[-1] if trail else None)
        last_url = nav or last_url

    # 4. detours: a short round trip A -> X -> A where X is a minor app not named in the goal
    app_counts: dict[str, int] = {}
    for e in cleaned:
        a = _app_name(e)
        if a:
            app_counts[a] = app_counts.get(a, 0) + 1
    runs: list[tuple[str | None, int, int]] = []  # (app, start, end_exclusive)
    for i, e in enumerate(cleaned):
        a = _app_name(e)
        if runs and runs[-1][0] == a:
            runs[-1] = (a, runs[-1][1], i + 1)
        else:
            runs.append((a, i, i + 1))
    for k in range(1, len(runs) - 1):
        app, s, t = runs[k]
        before, after = runs[k - 1][0], runs[k + 1][0]
        if not app or before != after or before is None:
            continue
        if _words(app) & goal_words:
            continue  # the author said this app is part of the task
        seg = cleaned[s:t]
        actions = [e for e in seg if e.get("type") in ("click", "type", "key", "menu")]
        duration = _ts(seg[-1]) - _ts(seg[0])
        minor = app_counts.get(app, 0) <= max(4, 0.25 * n)
        if not minor or len(actions) > 3 or duration > 90:
            continue
        if any(_is_protected(e) for e in seg):
            continue
        p = 0.93 if not actions else 0.8
        for e in seg:
            bump(e["seq"], p, "detour")
        back = cleaned[t]  # re-activation of the original app is redundant once the detour goes
        if back.get("type") == "app_activate":
            bump(back["seq"], p, "detour")

    # 5. duplicates: same action on the same element within 10s, not adjacent (adjacent handled by cleanup)
    seen: dict[tuple, float] = {}
    for e in cleaned:
        if e.get("type") not in ("click", "menu", "key") or not (_el_key(e) or e.get("key")):
            continue
        key = (e.get("type"), _el_key(e), _norm_key(e.get("key")))
        last = seen.get(key)
        if last is not None and 0 <= _ts(e) - last <= 10:
            bump(e["seq"], 0.75, "duplicate")
        seen[key] = _ts(e)

    # safety cap
    for e in cleaned:
        if _is_protected(e) and out[e["seq"]][0] >= 0.6:
            out[e["seq"]] = (min(out[e["seq"]][0], 0.6), out[e["seq"]][1])
    return out


def local_boundaries(cleaned: list[dict], gap_seconds: float = 180.0) -> dict[int, float]:
    """seq -> P(a new task starts at this event). Long idle gap + disjoint apps on each side."""
    out: dict[int, float] = {}
    for i in range(1, len(cleaned)):
        if _ts(cleaned[i]) - _ts(cleaned[i - 1]) < gap_seconds:
            continue
        before = {_app_name(e) for e in cleaned[:i]} - {None}
        after = {_app_name(e) for e in cleaned[i:]} - {None}
        out[cleaned[i]["seq"]] = 0.9 if before.isdisjoint(after) else 0.5
    return out


# ====================================================================== Jev client
class JevError(Exception):
    pass


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; half-opens (one trial) after `cooldown`."""

    def __init__(self, threshold: int, cooldown: float, clock=time.monotonic):
        self.threshold, self.cooldown, self._clock = max(1, threshold), cooldown, clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            return "half_open" if self._clock() - self._opened_at >= self.cooldown else "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at >= self.cooldown and not self._trial:
                self._trial = True
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._failures, self._opened_at, self._trial = 0, None, False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._trial = False
            if self._failures >= self.threshold:
                if self._opened_at is None:
                    log.warning("jev circuit breaker opened", extra={"failures": self._failures})
                self._opened_at = self._clock()


_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class JevClient:
    """Minimal client for POST /v1/systemone (https://docs.typesafe.ai/api)."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 breaker: CircuitBreaker | None = None, sleep=time.sleep):
        self.settings = settings
        self.model = settings.jev_model
        self._transport = transport
        self._sleep = sleep
        self.breaker = breaker or CircuitBreaker(settings.jev_breaker_failures, settings.jev_breaker_cooldown_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.jev_api_key) or self._transport is not None

    def client(self) -> httpx.Client:
        limits = httpx.Limits(max_connections=max(2, self.settings.jev_concurrency * 2))
        return httpx.Client(timeout=self.settings.jev_timeout_seconds, transport=self._transport, limits=limits)

    def ask(self, http: httpx.Client, state: Any, questions: dict) -> tuple[dict, dict]:
        """Returns (answers, usage). Raises JevError when the breaker is open or retries run out."""
        if not self.breaker.allow():
            raise JevError("circuit open")
        url = self.settings.jev_base_url.rstrip("/") + "/v1/systemone"
        body = {"state": state, "model": self.model, "questions": questions}
        headers = {"authorization": f"Bearer {self.settings.jev_api_key}", "content-type": "application/json"}
        attempts = max(0, self.settings.jev_max_retries) + 1
        last = "unknown error"
        for attempt in range(attempts):
            retry_after = None
            try:
                r = http.post(url, json=body, headers=headers)
            except httpx.HTTPError as e:
                last = f"transport: {type(e).__name__}"
            else:
                if r.status_code == 200:
                    try:
                        payload = r.json()
                        answers = payload["answers"]
                        if not isinstance(answers, dict):
                            raise TypeError("answers is not an object")
                    except (ValueError, KeyError, TypeError) as e:
                        self.breaker.failure()
                        raise JevError(f"bad response: {e}") from None
                    self.breaker.success()
                    return answers, payload.get("usage") or {}
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if r.status_code not in _RETRYABLE:
                    self.breaker.failure()
                    raise JevError(last)
                retry_after = _retry_after(r.headers.get("retry-after"))
            if attempt + 1 < attempts:
                base = self.settings.jev_retry_base_seconds * (2 ** attempt)
                self._sleep(min(retry_after if retry_after is not None else base * random.uniform(0.8, 1.2), 10.0))
        self.breaker.failure()
        raise JevError(last)


def _retry_after(v: str | None) -> float | None:
    try:
        return max(0.0, float(v)) if v else None
    except ValueError:
        return None


# ====================================================================== Jev questions
def _event_questions(ask_new_task: bool) -> dict:
    q: dict[str, dict] = {
        "needed": {
            "type": "noul",
            "instructions": "Is `event` a step someone must do to accomplish `goal` (including navigation needed "
                            "to reach where the work happens)? Use `before` and `after` for context.",
            "criteria": {"true": "Needed to do the task, or needed to get to where the task is done",
                         "false": "Not needed: a side trip, a mistake that was undone, looking around, or a repeat"},
        },
        "reason": {
            "type": "choice",
            "instructions": "Which best describes the role of `event` in the recorded task `goal`?",
            "criteria": dict(REASONS),
        },
        "irreversible": {
            "type": "noul",
            "instructions": "Does `event` commit something that cannot be undone (submit, send, delete, pay, "
                            "approve, publish, transfer)?",
        },
    }
    if ask_new_task:
        q["new_task"] = {
            "type": "noul",
            "instructions": "Does `event` start a different, unrelated task from the events in `before`?",
            "criteria": {"true": "A new, unrelated piece of work begins here",
                         "false": "Continues the same piece of work"},
        }
    return q


def _event_state(cleaned: list[dict], i: int, goal: str | None, apps: list[str], ctx: int) -> dict:
    ev = cleaned[i]
    gap = _ts(ev) - _ts(cleaned[i - 1]) if i else 0.0
    return {
        "goal": goal or "(not stated: infer the task from the whole sequence)",
        "apps_in_recording": apps,
        "position": f"{i + 1} of {len(cleaned)}",
        "seconds_since_previous": round(max(gap, 0.0), 1),
        "before": [_compact(e) for e in cleaned[max(0, i - ctx):i]],
        "event": _compact(ev),
        "after": [_compact(e) for e in cleaned[i + 1:i + 1 + ctx]],
    }


# ====================================================================== the filter
class RelevanceFilter:
    """Process-wide object (owns the Jev circuit breaker). Thread-safe."""

    def __init__(self, settings: Settings, jev: JevClient | None = None):
        self.settings = settings
        self.jev = jev if jev is not None else (JevClient(settings) if settings.filter_provider == "jev" else None)
        if settings.filter_provider == "jev" and self.jev is not None and not self.jev.enabled:
            log.warning("FILTER_PROVIDER=jev but JEV_API_KEY is empty; using the local rules only")

    @property
    def name(self) -> str:
        return "jev" if self.jev is not None and self.jev.enabled else "local"

    def run(self, cleaned: list[dict], goal: str | None, thresholds: Thresholds,
            split_tasks: bool = True) -> FilterResult:
        started = time.perf_counter()
        if not cleaned:
            return FilterResult({}, [], "local")
        local = local_scores(cleaned, goal)
        boundaries = local_boundaries(cleaned)
        jev_answers: dict[int, dict] = {}
        res = FilterResult({}, [], "local")

        if self.jev is not None and self.jev.enabled:
            res.model = self.jev.model
            jev_answers = self._ask_jev(cleaned, goal, res)

        protected = {e["seq"] for e in cleaned if _is_protected(e)}
        for e in cleaned:
            seq = e["seq"]
            p_local, r_local = local[seq]
            ans = jev_answers.get(seq)
            if ans is None:
                p, reason, src, p_irr, p_new = p_local, r_local, "local", None, boundaries.get(seq)
            else:
                p_needed = _noul(ans.get("needed"))
                p = 1.0 - p_needed if p_needed is not None else p_local
                reason = _choice(ans.get("reason")) or r_local
                if p >= thresholds.review and reason not in DROP_REASONS:
                    reason = r_local if r_local in DROP_REASONS else "unclear_relevance"
                src = "jev"
                if p_local >= thresholds.drop and p_local > p:  # strong local evidence (e.g. undo) wins
                    p, reason, src = p_local, r_local, "jev+local"
                p_irr = _noul(ans.get("irreversible"))
                p_new = max(_noul(ans.get("new_task")) or 0.0, boundaries.get(seq, 0.0)) or None
            if seq in protected or (p_irr is not None and p_irr >= 0.5):
                p = min(p, max(thresholds.drop - 0.01, 0.0))  # never auto-drop a committing step
            res.decisions[seq] = EventDecision(seq, thresholds.classify(p), reason, float(min(max(p, 0.0), 1.0)),
                                               src, p_irr, p_new)

        srcs = {d.source for d in res.decisions.values()}
        res.source = "jev" if srcs == {"jev"} else ("local" if not jev_answers else "jev+local")
        res.segments = segment(cleaned, res.decisions, thresholds) if split_tasks else [[e["seq"] for e in cleaned]]
        if self.jev is not None and self.jev.enabled and self.settings.jev_task_types.strip():
            res.task_type = self._task_type(cleaned, goal, res)
        res.duration_ms = int((time.perf_counter() - started) * 1000)
        return res

    # -------------------------------------------------------------- jev plumbing
    def _ask_jev(self, cleaned: list[dict], goal: str | None, res: FilterResult) -> dict[int, dict]:
        s = self.settings
        apps = list(dict.fromkeys(a for a in (_app_name(e) for e in cleaned) if a))
        idx = list(range(min(len(cleaned), max(0, s.jev_max_events))))
        if len(cleaned) > len(idx):
            res.notes.append(f"jev limited to first {len(idx)} of {len(cleaned)} events")
        out: dict[int, dict] = {}
        lock = threading.Lock()

        def one(i: int, http: httpx.Client) -> None:
            state = _event_state(cleaned, i, goal, apps, s.jev_context_events)
            try:
                answers, usage = self.jev.ask(http, state, _event_questions(ask_new_task=i > 0))
            except JevError as e:
                with lock:
                    res.jev_failures += 1
                    if len(res.notes) < 10 and str(e) not in res.notes:
                        res.notes.append(str(e)[:200])
                return
            with lock:
                res.jev_calls += 1
                res.input_tokens += int(usage.get("input_tokens") or 0)
                res.output_tokens += int(usage.get("output_tokens") or 0)
                out[cleaned[i]["seq"]] = answers

        with self.jev.client() as http, ThreadPoolExecutor(max_workers=max(1, s.jev_concurrency)) as pool:
            list(pool.map(lambda i: one(i, http), idx))
        if res.jev_failures:
            log.warning("jev partial failure, local rules used for the rest",
                        extra={"failures": res.jev_failures, "ok": res.jev_calls})
        return out

    def _task_type(self, cleaned: list[dict], goal: str | None, res: FilterResult) -> str | None:
        types = [t.strip() for t in self.settings.jev_task_types.split(",") if t.strip()][:255]
        if not types:
            return None
        kept = [_compact(e) for e in cleaned if res.decisions[e["seq"]].decision != "drop"][:80]
        q = {"task_type": {"type": "choice", "instructions": "What kind of work is this recorded task?",
                           "criteria": {t: t for t in types}}}
        try:
            with self.jev.client() as http:
                answers, usage = self.jev.ask(http, {"goal": goal, "steps": kept}, q)
        except JevError as e:
            res.notes.append(f"task_type: {e}"[:200])
            return None
        res.jev_calls += 1
        res.input_tokens += int(usage.get("input_tokens") or 0)
        ans = answers.get("task_type") or {}
        conf = ans.get("confidence")
        choice = _choice(ans)
        return choice if choice in types and (conf is None or conf >= 0.6) else None

    def estimate_cost(self, res: FilterResult) -> float:
        return round(res.input_tokens * self.settings.jev_input_cost_per_mtok / 1e6, 6)


def _noul(ans: Any) -> float | None:
    if not isinstance(ans, dict):
        return None
    v = ans.get("noul")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


def _choice(ans: Any) -> str | None:
    if not isinstance(ans, dict):
        return None
    c = ans.get("choice")
    return c if isinstance(c, str) else None


# ====================================================================== segmentation
def segment(cleaned: list[dict], decisions: dict[int, EventDecision], th: Thresholds) -> list[list[int]]:
    """Split at events where a new task likely starts, if both sides have enough kept steps."""
    if not cleaned:
        return []
    cuts = [i for i, e in enumerate(cleaned) if i > 0 and (decisions[e["seq"]].p_new_task or 0.0) >= th.split]

    def kept(lo: int, hi: int) -> int:
        return sum(1 for e in cleaned[lo:hi] if decisions[e["seq"]].decision != "drop"
                   and e.get("type") in ("click", "type", "key", "menu", "url_change"))

    bounds = [0]
    for c in cuts:
        nxt = next((x for x in cuts if x > c), len(cleaned))
        if kept(bounds[-1], c) >= th.min_segment_steps and kept(c, nxt) >= th.min_segment_steps:
            bounds.append(c)
    bounds.append(len(cleaned))
    return [[e["seq"] for e in cleaned[a:b]] for a, b in zip(bounds, bounds[1:]) if b > a]


def decisions_to_rows(res: FilterResult, cleaned: list[dict]) -> list[dict]:
    seg_of = {seq: i for i, seg in enumerate(res.segments) for seq in seg}
    rows = []
    for e in cleaned:
        d = res.decisions.get(e["seq"])
        if d is None:
            continue
        rows.append({**asdict(d), "segment": seg_of.get(e["seq"], 0), "event": _compact(e)})
    return rows
