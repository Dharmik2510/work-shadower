"""Event log -> skill content.

Pipeline (orchestrated by jobs.generate_skill):
  1. deterministic cleanup (drop noise, merge typing bursts, collapse repeated clicks,
     de-dup app activations, fold window/url changes into the previous step's expectation)
  2. relevance filter (filtering.py): keep / review / drop per event, task segments
  3. ONE LLM call per task segment returning skill-content JSON via a forced tool call,
     validated against `SkillContent`. Dropped events still become steps, marked `excluded`
     so a reviewer can restore them; they are removed on publish.
  4. on any LLM failure / budget exhaustion / disabled provider -> heuristic generator
Either way the result is re-redacted and safety-checked (irreversible steps are never excluded).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from urllib.parse import urlsplit

from pydantic import ValidationError

from .llm import LLMError, LLMProvider
from .models import SkillContent, StepFilter
from .redact import redact_obj

log = logging.getLogger("app.skillgen")

MAX_LLM_EVENTS = 600

_IRREVERSIBLE = re.compile(
    r"\b(submit|send|delete|remove|pay|purchase|buy|place order|confirm|approve|publish|transfer|"
    r"sign|finali[sz]e|close (?:claim|account|ticket)|cancel (?:policy|order|subscription)|deactivate|reset|"
    r"wire|refund|terminate|revoke|post)\b",
    re.IGNORECASE,
)
_NOISE_KEYS = {"tab", "shift+tab", "up", "down", "left", "right", "shift", "cmd", "ctrl", "alt", "option",
               "capslock", "fn", "pageup", "pagedown", "home", "end"}
_KEY_NAMES = {
    "cmd+s": "save", "cmd+c": "copy", "cmd+v": "paste", "cmd+x": "cut", "cmd+z": "undo",
    "cmd+a": "select all", "cmd+f": "find", "cmd+n": "create a new item", "cmd+t": "open a new tab",
    "cmd+w": "close the window", "cmd+p": "print", "cmd+return": "send", "return": "confirm", "enter": "confirm",
    "escape": "dismiss", "cmd+l": "focus the address bar", "cmd+r": "reload",
}
_ROLE_WORDS = {
    "AXButton": "button", "AXLink": "link", "AXMenuItem": "menu item", "AXCheckBox": "checkbox",
    "AXRadioButton": "option", "AXTab": "tab", "AXTextField": "field", "AXTextArea": "text area",
    "AXSearchField": "search field", "AXComboBox": "field", "AXPopUpButton": "dropdown", "AXRow": "row",
    "AXCell": "cell", "AXMenuButton": "menu", "AXStaticText": "text", "AXImage": "image",
}
_STOP = {"the", "and", "for", "with", "from", "into", "this", "that", "your", "a", "an", "in", "on", "to", "of",
         "new", "workflow", "using", "open", "click", "look", "find", "check", "make", "then", "after"}


# ============================================================ cleanup
def _app_name(ev: dict) -> str | None:
    app = ev.get("app") or {}
    return app.get("name") or app.get("bundle_id")


def _el(ev: dict) -> dict:
    return ev.get("element") or {}


def _el_key(ev: dict) -> tuple | None:
    el = _el(ev)
    if not el:
        return None
    if el.get("identifier"):
        return ("id", _app_name(ev), el["identifier"])
    if el.get("path"):
        return ("path", _app_name(ev), tuple(el["path"]))
    if el.get("label") or el.get("role"):
        return ("label", _app_name(ev), el.get("role"), el.get("label"))
    return None


def _ts(ev: dict) -> float:
    ts = ev.get("ts")
    if isinstance(ts, datetime):
        return ts.timestamp()
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _norm_key(k: str | None) -> str:
    return (k or "").strip().lower().replace(" ", "")


def cleanup(events: list[dict]) -> list[dict]:
    evs = sorted((dict(e) for e in events), key=lambda e: e.get("seq", 0))
    out: list[dict] = []
    current_app: str | None = None
    for ev in evs:
        t = ev.get("type")
        app = _app_name(ev)
        prev = out[-1] if out else None

        if t == "scroll":
            continue
        if t == "key" and _norm_key(ev.get("key")) in _NOISE_KEYS | {""}:
            continue
        if t == "app_activate":
            if app == current_app:
                continue
            if prev and prev.get("type") == "app_activate":
                out.pop()  # app flicking (A -> B -> C): keep only the last activation of a burst
                if out and current_app_before(out) == app:
                    current_app = app
                    continue
            current_app = app
            out.append(ev)
            continue
        if app:
            current_app = app
        if t == "window_open":
            title = (ev.get("window") or {}).get("title")
            if prev is not None and title:
                prev.setdefault("_expect_window", title)
            continue
        if t == "url_change":
            if prev is not None and prev.get("type") in ("click", "key", "type", "menu") \
                    and _app_name(prev) == app and _ts(ev) - _ts(prev) <= 8:
                prev["_expect_url"] = ev.get("url")
                prev.setdefault("_urls", []).append(ev.get("url"))  # the full trail (round trips)
                if (ev.get("window") or {}).get("title"):
                    prev.setdefault("_expect_window", ev["window"]["title"])
                continue
            if prev is not None and prev.get("type") == "url_change" and _app_name(prev) == app:
                out[-1] = ev  # redirect chain: keep the final URL
                continue
            out.append(ev)
            continue
        if t == "type":
            # focus-click on the same field is implied by typing into it
            if prev is not None and prev.get("type") == "click" and _el_key(prev) == _el_key(ev) and _el_key(ev):
                ev.setdefault("screenshot_sha256", prev.get("screenshot_sha256"))
                out.pop()
                prev = out[-1] if out else None
            if prev is not None and prev.get("type") == "type" and _el_key(prev) == _el_key(ev):
                a, b = prev.get("text") or "", ev.get("text") or ""
                if _el(ev).get("value_kind") == "secure" or _el(prev).get("value_kind") == "secure":
                    prev["text"] = None
                elif b.startswith(a):
                    prev["text"] = b  # cumulative field value
                elif not a.startswith(b):
                    prev["text"] = a + b  # keystroke burst
                prev["screenshot_sha256"] = prev.get("screenshot_sha256") or ev.get("screenshot_sha256")
                continue
            out.append(ev)
            continue
        if t == "click":
            if prev is not None and prev.get("type") == "click" and _el_key(prev) == _el_key(ev) and _el_key(ev):
                if _ts(ev) - prev.get("_last_click_ts", _ts(prev)) <= 0.6:
                    prev["_count"] = prev.get("_count", 1) + 1  # genuine double/triple click
                prev["_last_click_ts"] = _ts(ev)
                continue  # otherwise an impatient repeat click: collapse silently
            out.append(ev)
            continue
        # key, menu, and anything else
        out.append(ev)
    return out


def current_app_before(out: list[dict]) -> str | None:
    for ev in reversed(out):
        if _app_name(ev):
            return _app_name(ev)
    return None


# ============================================================ heuristic generator
def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:40] or "value"


def _label(ev: dict) -> str:
    el = _el(ev)
    return (el.get("label") or el.get("identifier") or _ROLE_WORDS.get(el.get("role") or "", "") or "element").strip()


def _target(ev: dict) -> dict:
    el = _el(ev)
    return {
        "role": el.get("role"),
        "label": el.get("label"),
        "identifier": el.get("identifier"),
        "path": list(el.get("path") or []),
        "window_title": (ev.get("window") or {}).get("title"),
    }


def _expect(ev: dict) -> dict | None:
    w = ev.get("_expect_window")
    return {"window_title_contains": w, "element_present": None} if w else None


def _pretty_key(k: str) -> str:
    parts = [p for p in re.split(r"\+", k) if p]
    names = {"cmd": "Cmd", "ctrl": "Ctrl", "alt": "Option", "option": "Option", "shift": "Shift",
             "return": "Return", "enter": "Enter", "escape": "Esc", "delete": "Delete"}
    return "+".join(names.get(p.lower(), p.upper() if len(p) == 1 else p.capitalize()) for p in parts)


def _menu_path(ev: dict) -> str:
    path = [re.sub(r"^AX\w+:?", "", p).strip() for p in (_el(ev).get("path") or [])]
    path = [p for p in path if p and p.lower() not in ("menubar", "menu bar")]
    if not path and _el(ev).get("label"):
        path = [_el(ev)["label"]]
    return " > ".join(path)


def _is_irreversible(*texts: str | None) -> bool:
    return any(t and _IRREVERSIBLE.search(t) for t in texts)


@dataclass
class _Builder:
    steps: list[dict] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    field_inputs: dict[tuple, str] = field(default_factory=dict)
    apps: list[str] = field(default_factory=list)
    secure_apps: list[str] = field(default_factory=list)

    decisions: dict = field(default_factory=dict)

    def add(self, ev: dict | None, title: str, instruction: str, app: str | None, action: dict,
            irreversible: bool = False, source: dict | None = None) -> None:
        if app and app not in self.apps:
            self.apps.append(app)
        src = ev or source
        seqs = [src["seq"]] if src and src.get("seq") is not None else []
        flt = _filter_for(seqs, self.decisions)
        if flt and flt["decision"] == "drop" and irreversible:
            flt = {**flt, "decision": "review"}
        self.steps.append({
            "index": len(self.steps) + 1,
            "title": title[:300],
            "instruction": instruction,
            "app": app,
            "action": {"type": action["type"], "target": action.get("target"), "text": action.get("text"),
                       "key": action.get("key"), "url": action.get("url")},
            "expect": _expect(ev) if ev else None,
            "screenshot_sha256": (ev or {}).get("screenshot_sha256"),
            "irreversible": irreversible or _p_irreversible(seqs, self.decisions),
            "excluded": bool(flt and flt["decision"] == "drop"),
            "filter": flt,
            "source_seqs": seqs,
        })

    def input_for(self, ev: dict, label: str, text: str | None) -> str:
        key = _el_key(ev) or ("label", label)
        if key in self.field_inputs:
            return self.field_inputs[key]
        name = base = _slug(label)
        n = 2
        while name in self.inputs:
            name, n = f"{base}_{n}", n + 1
        example = text if text and "[REDACTED:" not in text else None
        self.inputs[name] = {"name": name, "description": f"Value for “{label}”", "example": example}
        self.field_inputs[key] = name
        return name


def _decision(decisions: dict, seq) -> dict | None:
    d = decisions.get(seq) if decisions else None
    if d is None:
        return None
    return d if isinstance(d, dict) else d.__dict__


def _filter_for(seqs: list[int], decisions: dict) -> dict | None:
    """Combine the decisions of the events a step came from: all dropped -> drop; any review
    (or a mix of drop and keep) -> review; otherwise keep."""
    ds = [d for d in (_decision(decisions, q) for q in seqs) if d]
    if not ds:
        return None
    kinds = {d["decision"] for d in ds}
    worst = max(ds, key=lambda d: d["p_drop"])
    if kinds == {"drop"}:
        decision = "drop"
    elif "review" in kinds or ("drop" in kinds and "keep" in kinds):
        decision = "review"
    else:
        decision = "keep"
    return {"decision": decision, "reason": worst["reason"], "p_drop": round(float(worst["p_drop"]), 4),
            "source": worst["source"]}


def _p_irreversible(seqs: list[int], decisions: dict) -> bool:
    for q in seqs:
        d = _decision(decisions, q)
        if d and (d.get("p_irreversible") or 0.0) >= 0.5:
            return True
    return False


def heuristic_skill(events: list[dict], title_hint: str | None = None, *, cleaned: bool = False,
                    intent: str | None = None, decisions: dict | None = None, use_intent_title: bool = True) -> dict:
    evs = events if cleaned else cleanup(events)
    b = _Builder(decisions=decisions or {})
    current_app: str | None = None
    for ev in evs:
        t = ev.get("type")
        app = _app_name(ev)
        win = (ev.get("window") or {}).get("title")
        if app and app != current_app:
            b.add(ev if t == "app_activate" else None, f"Open {app}", f"Open or switch to {app}.", app,
                  {"type": "open_app", "target": None}, source=ev)
            current_app = app
        if t == "app_activate":
            continue
        if t == "url_change":
            url = ev.get("url") or ""
            parts = urlsplit(url)
            page = (parts.netloc + (parts.path if parts.path not in ("", "/") else "")) or url
            b.add(ev, f"Go to {page[:80]}", f"In {app or 'the browser'}, open {url}.", app,
                  {"type": "open_url", "url": url})
        elif t == "click":
            label = _label(ev)
            role_word = _ROLE_WORDS.get(_el(ev).get("role") or "", "element")
            where = f" in the “{win}” window" if win else ""
            times = ev.get("_count", 1)
            extra = " (double-click)" if times == 2 else (f" ({times} times)" if times > 2 else "")
            irr = _is_irreversible(label)
            instr = f"Click the “{label}” {role_word}{where}{extra}."
            if irr:
                instr += " This step can't be undone — double-check before clicking."
            b.add(ev, f"Click “{label}”", instr, app, {"type": "click", "target": _target(ev)}, irr)
        elif t == "type":
            label = _label(ev)
            secure = _el(ev).get("value_kind") == "secure"
            if secure:
                where_cred = win or app
                if where_cred and where_cred not in b.secure_apps:
                    b.secure_apps.append(where_cred)
                b.add(ev, f"Enter your {label.lower()}",
                      f"Type your own {label.lower()} into the “{label}” field (never recorded).", app,
                      {"type": "type", "target": _target(ev), "text": None})
            else:
                name = b.input_for(ev, label, ev.get("text"))
                b.add(ev, f"Enter {label}", f"Type {{{{{name}}}}} into the “{label}” field.",
                      app, {"type": "type", "target": _target(ev), "text": f"{{{{{name}}}}}"})
        elif t == "key":
            k = _norm_key(ev.get("key"))
            meaning = _KEY_NAMES.get(k)
            pretty = _pretty_key(k)
            irr = k in ("cmd+return", "cmd+enter") or _is_irreversible(meaning)
            instr = f"Press {pretty}" + (f" to {meaning}." if meaning else ".")
            b.add(ev, f"Press {pretty}", instr, app, {"type": "key", "key": k}, irr)
        elif t == "menu":
            path = _menu_path(ev) or _label(ev)
            irr = _is_irreversible(path)
            b.add(ev, f"Choose {path}", f"From the menu bar, choose {path}.", app,
                  {"type": "menu", "target": _target(ev)}, irr)

    included = [s for s in b.steps if not s["excluded"]] or b.steps
    apps = [a for a in b.apps if any(s["app"] == a for s in included)] or b.apps
    title = pick_title(title_hint, intent if use_intent_title else None) or _derive_title(included, apps)
    goal = _goal_from_intent(intent) or _derive_goal(included, apps)
    prereqs = [f"Access to {a}" for a in apps]
    prereqs += [f"Your own sign-in credentials for {a}" for a in b.secure_apps]
    tags = _derive_tags(title, apps)
    content = {
        "title": title[:200] or "Recorded workflow",
        "goal": goal,
        "apps": apps,
        "prerequisites": prereqs,
        "inputs": list(b.inputs.values()),
        "steps": b.steps,
        "tags": tags,
    }
    return SkillContent.model_validate(redact_obj(content)).to_json()


def is_generic_hint(hint: str | None) -> bool:
    return not (hint or "").strip() or bool(_GENERIC_HINT.match(hint.strip()))


def _title_from_intent(intent: str | None) -> str | None:
    t = re.split(r"(?<=[.!?])\s|\n", (intent or "").strip(), maxsplit=1)[0].strip().rstrip(".!")
    if len(t) < 4:
        return None
    if len(t) > 80:
        t = t[:80].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return t[:1].upper() + t[1:]


def pick_title(title_hint: str | None, intent: str | None) -> str | None:
    """Author's own title > their stated intent > None (derive from steps)."""
    if not is_generic_hint(title_hint):
        return title_hint.strip()[:200]
    return _title_from_intent(intent)


def _goal_from_intent(intent: str | None) -> str | None:
    t = (intent or "").strip()
    if len(t) < 4:
        return None
    t = t[:1].upper() + t[1:]
    return (t if t.endswith((".", "!", "?")) else t + ".")[:2000]


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


_GENERIC_HINT = re.compile(r"^(workflow|recording)( in .*)?$", re.I)


def _key_actions(steps: list[dict]) -> list[str]:
    out: list[str] = []
    for s in steps:
        if s["irreversible"] and s["action"]["type"] in ("click", "menu"):
            label = ((s["action"].get("target") or {}).get("label") or s["title"]).strip("“”\" ")
            if label and label not in out:
                out.append(label)
    return out


def _derive_goal(steps: list[dict], apps: list[str]) -> str:
    n = len(steps)
    where = f" in {_join(apps)}" if apps else ""
    goal = f"{n} recorded step{'s' if n != 1 else ''}{where}."
    keys = _key_actions(steps)
    if keys:
        goal += " Key actions: " + ", ".join(f"“{k}”" for k in keys[:3]) + "."
    return goal


def _derive_title(steps: list[dict], apps: list[str]) -> str:
    keys = _key_actions(steps)
    if keys:
        # The first committing action is usually the point of the workflow.
        first = next(s for s in steps if s["irreversible"] and s["action"]["type"] in ("click", "menu"))
        app = first.get("app") or (apps[0] if apps else None)
        label = keys[0]
        return f"{label[:1].upper()}{label[1:]}" + (f" in {app}" if app else "")
    main_app = apps[-1] if apps else None
    for s in reversed(steps):
        if s["irreversible"] and s["action"]["type"] in ("click", "menu"):
            label = (s["action"].get("target") or {}).get("label") or s["title"]
            label = label.strip("“”\" ")
            return f"{label[:1].upper()}{label[1:]} in {main_app}" if main_app else label
    for s in steps:
        if s["action"]["type"] == "click":
            label = ((s["action"].get("target") or {}).get("label") or "").strip()
            if label:
                return f"{label[:1].upper()}{label[1:]} workflow" + (f" in {main_app}" if main_app else "")
    return f"Workflow in {_join(apps)}" if apps else "Recorded workflow"


def _derive_tags(title: str, apps: list[str]) -> list[str]:
    tags: list[str] = []
    for a in apps:
        t = re.sub(r"^(google|microsoft|apple)\s+", "", a.lower()).strip()
        if t:
            tags.append(t)
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title.lower()):
        if w not in _STOP and w not in tags:
            tags.append(w)
    return tags[:6]


# ============================================================ LLM path
SYSTEM_PROMPT = """You turn a cleaned macOS accessibility event log into a reusable, human-readable "skill" (how-to).
Answer by calling the tool once. The skill JSON has this shape:
{"title": str (imperative, <= 80 chars), "goal": str (one sentence),
 "apps": [str], "prerequisites": [str],
 "inputs": [{"name": snake_case str, "description": str, "example": str|null}],
 "steps": [{"index": int, "title": str, "instruction": str, "app": str|null,
            "action": {"type": "open_app"|"open_url"|"click"|"type"|"key"|"menu"|"wait",
                       "target": {"role": str|null, "label": str|null, "identifier": str|null, "path": [str], "window_title": str|null}|null,
                       "text": str|null, "key": str|null, "url": str|null},
            "expect": {"window_title_contains": str|null, "element_present": {"role": str|null, "label": str|null}|null}|null,
            "screenshot_sha256": str|null, "irreversible": bool,
            "source_seqs": [int], "exclude": bool, "exclude_reason": str|null}],
 "tags": [str]}
Rules:
- The author's stated goal (if given) is what the skill is for. Title and goal must reflect it.
- One step per user action, in order. "source_seqs" lists the seq numbers of the events each step came from.
- Every event becomes part of some step. Events marked "filter": "drop" are probably not part of the task:
  still write their step, set "exclude": true and copy the filter reason into "exclude_reason".
- If YOU are confident a step does not serve the goal (a side trip, an undone mistake, aimless looking around),
  set "exclude": true with a short "exclude_reason". Never exclude a step needed to reach a later step.
- Never exclude a step that submits, sends, deletes, pays, approves or publishes.
- Copy target role/label/identifier/path/window_title and screenshot_sha256 verbatim from the event the step came from.
- Values that would differ between runs (names, numbers, search terms) become inputs; reference them as {{input_name}} in "text" and "instruction".
- Never output secrets or anything shown as [REDACTED:...]; for secure fields write an instruction asking the user to type their own value and set text to null.
- Set irreversible=true for steps that submit, send, delete, pay, approve, publish or otherwise cannot be undone.
- Title, goal, apps, prerequisites and tags describe only the steps that are NOT excluded.
- Instructions are plain, friendly English a new employee can follow. Tags: 2-6 lowercase keywords."""

_NULLABLE_STR = {"type": ["string", "null"]}
_TARGET_SCHEMA = {
    "type": ["object", "null"],
    "properties": {"role": _NULLABLE_STR, "label": _NULLABLE_STR, "identifier": _NULLABLE_STR,
                   "path": {"type": "array", "items": {"type": "string"}}, "window_title": _NULLABLE_STR},
}


def skill_tool_schema() -> dict:
    """JSON schema for the forced tool call (structured output)."""
    step = {
        "type": "object",
        "properties": {
            "index": {"type": "integer"},
            "title": {"type": "string"},
            "instruction": {"type": "string"},
            "app": _NULLABLE_STR,
            "action": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["open_app", "open_url", "click", "type", "key", "menu", "wait"]},
                    "target": _TARGET_SCHEMA, "text": _NULLABLE_STR, "key": _NULLABLE_STR, "url": _NULLABLE_STR,
                },
                "required": ["type"],
            },
            "expect": {
                "type": ["object", "null"],
                "properties": {"window_title_contains": _NULLABLE_STR,
                               "element_present": {"type": ["object", "null"],
                                                   "properties": {"role": _NULLABLE_STR, "label": _NULLABLE_STR}}},
            },
            "screenshot_sha256": _NULLABLE_STR,
            "irreversible": {"type": "boolean"},
            "source_seqs": {"type": "array", "items": {"type": "integer"}},
            "exclude": {"type": "boolean"},
            "exclude_reason": _NULLABLE_STR,
        },
        "required": ["title", "instruction", "action", "irreversible", "source_seqs", "exclude"],
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "goal": {"type": "string"},
            "apps": {"type": "array", "items": {"type": "string"}},
            "prerequisites": {"type": "array", "items": {"type": "string"}},
            "inputs": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "description": {"type": "string"}, "example": _NULLABLE_STR},
                "required": ["name"]}},
            "steps": {"type": "array", "items": step},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "goal", "apps", "steps", "tags"],
    }


def _compact(ev: dict, decision: dict | None = None) -> dict:
    el = _el(ev)
    out = {
        "seq": ev.get("seq"), "type": ev.get("type"), "app": _app_name(ev),
        "window": (ev.get("window") or {}).get("title"),
        "role": el.get("role"), "label": el.get("label"), "identifier": el.get("identifier"),
        "path": el.get("path") or None, "secure": el.get("value_kind") == "secure" or None,
        "text": ev.get("text"), "key": ev.get("key"), "url": ev.get("url"),
        "then_window": ev.get("_expect_window"), "then_url": ev.get("_expect_url"),
        "clicks": ev.get("_count"), "screenshot_sha256": ev.get("screenshot_sha256"),
    }
    if decision and decision.get("decision") in ("drop", "review"):
        out["filter"] = decision["decision"]
        out["filter_reason"] = decision.get("reason")
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def build_prompt(cleaned: list[dict], title_hint: str | None, intent: str | None = None,
                 decisions: dict | None = None, part: tuple[int, int] | None = None) -> str:
    lines = "\n".join(json.dumps(_compact(e, _decision(decisions or {}, e.get("seq"))), ensure_ascii=False)
                      for e in cleaned)
    head = ""
    if intent and intent.strip():
        head += f'The author says this recording is: "{intent.strip()[:1000]}".\n'
    if not is_generic_hint(title_hint):
        head += f'The author titled this recording: "{title_hint.strip()}".\n'
    if part and part[1] > 1:
        head += (f"The recording contained {part[1]} separate tasks; this is task {part[0]} of {part[1]}. "
                 "Write the skill for THIS task only; title it for what these events do.\n")
    return f"{head}Event log ({len(cleaned)} events, one JSON per line):\n{lines}\n\nReturn the skill now."


def postprocess(content: dict, cleaned: list[dict], decisions: dict | None = None) -> dict:
    """Validate, re-redact and safety-check LLM output. Raises ValueError if unusable."""
    content = dict(content)
    raw_steps = list(content.get("steps") or [])
    try:
        model = SkillContent.model_validate(redact_obj(content))
    except ValidationError as e:
        raise ValueError(f"schema validation failed: {e.error_count()} errors") from None
    if not model.steps:
        raise ValueError("model returned no steps")
    decisions = decisions or {}
    seqs_known = {e.get("seq") for e in cleaned}
    shas = {e.get("screenshot_sha256") for e in cleaned if e.get("screenshot_sha256")}
    secure_labels = {(_el(e).get("label") or "") for e in cleaned if _el(e).get("value_kind") == "secure"}
    for s, raw in zip(model.steps, raw_steps):
        if s.screenshot_sha256 and s.screenshot_sha256 not in shas:
            s.screenshot_sha256 = None
        label = s.action.target.label if s.action.target else None
        if _is_irreversible(label, s.title) and s.action.type in ("click", "menu", "key"):
            s.irreversible = True  # never let the model downgrade safety
        if s.action.type == "type" and label and label in secure_labels:
            s.action.text = None
        s.source_seqs = [q for q in dict.fromkeys(s.source_seqs) if q in seqs_known]
        s.irreversible = s.irreversible or _p_irreversible(s.source_seqs, decisions)
        flt = _filter_for(s.source_seqs, decisions)
        llm_excl = isinstance(raw, dict) and raw.get("exclude") is True
        if llm_excl and (flt is None or flt["decision"] == "keep"):
            # the model alone never auto-drops: it flags for review
            reason = str(raw.get("exclude_reason") or "not needed for the goal")[:200]
            flt = {"decision": "review", "reason": reason, "p_drop": max(flt["p_drop"] if flt else 0.0, 0.6),
                   "source": "llm"}
        if flt and flt["decision"] == "drop" and s.irreversible:
            flt = {**flt, "decision": "review"}
        s.filter = StepFilter.model_validate(flt) if flt else None
        s.excluded = bool(flt and flt["decision"] == "drop")
    out = model.to_json()
    included = [s for s in out["steps"] if not s["excluded"]]
    if not included:
        raise ValueError("every step was excluded")
    used_apps = {s["app"] for s in included if s.get("app")}
    out["apps"] = [a for a in out["apps"] if a in used_apps] or [a for a in dict.fromkeys(
        s["app"] for s in included if s.get("app"))]
    return SkillContent.model_validate(out).to_json()


@dataclass
class GenResult:
    content: dict
    method: str  # "llm" | "heuristic"
    error: str | None = None


UsageCallback = Callable[[int, int, bool], None]


def llm_request(cleaned: list[dict], title_hint: str | None, intent: str | None = None,
                decisions: dict | None = None, part: tuple[int, int] | None = None) -> tuple[str, str, dict]:
    return SYSTEM_PROMPT, build_prompt(cleaned, title_hint, intent, decisions, part), skill_tool_schema()


def from_llm_output(data: dict, cleaned: list[dict], title_hint: str | None, intent: str | None = None,
                    decisions: dict | None = None, use_intent_title: bool = True) -> dict:
    content = postprocess(data, cleaned, decisions)
    forced = pick_title(title_hint, None)  # an explicit (non-generic) author title always wins
    if forced:
        content["title"] = forced
    if use_intent_title and intent and not (content.get("goal") or "").strip():
        content["goal"] = _goal_from_intent(intent) or ""
    return content


def generate(
    events: list[dict],
    title_hint: str | None,
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    on_usage: UsageCallback | None = None,
    intent: str | None = None,
    decisions: dict | None = None,
    cleaned: bool = False,
    part: tuple[int, int] | None = None,
) -> GenResult:
    evs = events if cleaned else cleanup(events)
    whole = not part or part[1] <= 1
    hint = title_hint if whole else None
    error: str | None = None
    if llm is not None and llm.enabled and allow_llm and 0 < len(evs) <= MAX_LLM_EVENTS:
        try:
            system, prompt, schema = llm_request(evs, hint, intent, decisions, part)
            res = llm.complete_json(system, prompt, schema=schema)
            if on_usage:
                on_usage(res.input_tokens, res.output_tokens, True)
            content = from_llm_output(res.data, evs, hint, intent, decisions, use_intent_title=whole)
            return GenResult(content, "llm")
        except LLMError as e:
            if on_usage:
                on_usage(e.input_tokens, e.output_tokens, False)
            error = str(e)
        except ValueError as e:
            error = str(e)
        log.warning("llm skillgen failed, using heuristic", extra={"error": error})
    elif llm is not None and llm.enabled and not allow_llm:
        error = "llm not allowed (budget or flag)"
    content = heuristic_skill(evs, hint, cleaned=True, intent=intent if whole else None, decisions=decisions,
                              use_intent_title=whole)
    return GenResult(content, "heuristic", error)
