"""Event log -> skill content.

Pipeline:
  1. deterministic cleanup (drop noise, merge typing bursts, collapse repeated clicks,
     de-dup app activations, fold window/url changes into the previous step's expectation)
  2. ONE LLM call returning skill-content JSON, validated against `SkillContent`
  3. on any LLM failure / budget exhaustion / disabled provider -> heuristic generator
Either way the result is re-redacted and safety-checked (irreversible steps).
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
from .models import SkillContent
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

    def add(self, ev: dict | None, title: str, instruction: str, app: str | None, action: dict,
            irreversible: bool = False) -> None:
        if app and app not in self.apps:
            self.apps.append(app)
        self.steps.append({
            "index": len(self.steps) + 1,
            "title": title[:300],
            "instruction": instruction,
            "app": app,
            "action": {"type": action["type"], "target": action.get("target"), "text": action.get("text"),
                       "key": action.get("key"), "url": action.get("url")},
            "expect": _expect(ev) if ev else None,
            "screenshot_sha256": (ev or {}).get("screenshot_sha256"),
            "irreversible": irreversible,
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


def heuristic_skill(events: list[dict], title_hint: str | None = None, *, cleaned: bool = False) -> dict:
    evs = events if cleaned else cleanup(events)
    b = _Builder()
    current_app: str | None = None
    for ev in evs:
        t = ev.get("type")
        app = _app_name(ev)
        win = (ev.get("window") or {}).get("title")
        if app and app != current_app:
            b.add(ev if t == "app_activate" else None, f"Open {app}", f"Open or switch to {app}.", app,
                  {"type": "open_app", "target": None})
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

    hint = (title_hint or "").strip()
    # The Mac sends a generic "Workflow in <apps>" hint; a title from the steps reads better.
    if not hint or _GENERIC_HINT.match(hint):
        hint = _derive_title(b.steps, b.apps)
    title = hint
    goal = _derive_goal(b.steps, b.apps)
    prereqs = [f"Access to {a}" for a in b.apps]
    prereqs += [f"Your own sign-in credentials for {a}" for a in b.secure_apps]
    tags = _derive_tags(title, b.apps)
    content = {
        "title": title[:200] or "Recorded workflow",
        "goal": goal,
        "apps": b.apps,
        "prerequisites": prereqs,
        "inputs": list(b.inputs.values()),
        "steps": b.steps,
        "tags": tags,
    }
    return SkillContent.model_validate(content).to_json()


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
Reply with ONE JSON object only (no prose, no code fences) with exactly this shape:
{"title": str (imperative, <= 80 chars), "goal": str (one sentence),
 "apps": [str], "prerequisites": [str],
 "inputs": [{"name": snake_case str, "description": str, "example": str|null}],
 "steps": [{"index": int, "title": str, "instruction": str, "app": str|null,
            "action": {"type": "open_app"|"open_url"|"click"|"type"|"key"|"menu"|"wait",
                       "target": {"role": str|null, "label": str|null, "identifier": str|null, "path": [str], "window_title": str|null}|null,
                       "text": str|null, "key": str|null, "url": str|null},
            "expect": {"window_title_contains": str|null, "element_present": {"role": str|null, "label": str|null}|null}|null,
            "screenshot_sha256": str|null, "irreversible": bool}],
 "tags": [str]}
Rules:
- One step per user action, in order. Merge nothing that changes meaning; drop nothing the user must do.
- Copy target role/label/identifier/path/window_title and screenshot_sha256 verbatim from the event the step came from.
- Values that would differ between runs (names, numbers, search terms) become inputs; reference them as {{input_name}} in "text" and "instruction".
- Never output secrets or anything shown as [REDACTED:...]; for secure fields write an instruction asking the user to type their own value and set text to null.
- Set irreversible=true for steps that submit, send, delete, pay, approve, publish or otherwise cannot be undone.
- Instructions are plain, friendly English a new employee can follow. Tags: 2-6 lowercase keywords."""


def _compact(ev: dict) -> dict:
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
    return {k: v for k, v in out.items() if v not in (None, [], "")}


def build_prompt(cleaned: list[dict], title_hint: str | None) -> str:
    lines = "\n".join(json.dumps(_compact(e), ensure_ascii=False) for e in cleaned)
    hint = f'The author titled this recording: "{title_hint}".\n' if title_hint else ""
    return f"{hint}Event log ({len(cleaned)} events, one JSON per line):\n{lines}\n\nReturn the skill JSON now."


def postprocess(content: dict, cleaned: list[dict]) -> dict:
    """Validate, re-redact and safety-check LLM output. Raises ValueError if unusable."""
    try:
        model = SkillContent.model_validate(redact_obj(content))
    except ValidationError as e:
        raise ValueError(f"schema validation failed: {e.error_count()} errors") from None
    if not model.steps:
        raise ValueError("model returned no steps")
    shas = {e.get("screenshot_sha256") for e in cleaned if e.get("screenshot_sha256")}
    secure_labels = {(_el(e).get("label") or "") for e in cleaned if _el(e).get("value_kind") == "secure"}
    for s in model.steps:
        if s.screenshot_sha256 and s.screenshot_sha256 not in shas:
            s.screenshot_sha256 = None
        label = s.action.target.label if s.action.target else None
        if _is_irreversible(label, s.title) and s.action.type in ("click", "menu", "key"):
            s.irreversible = True  # never let the model downgrade safety
        if s.action.type == "type" and label and label in secure_labels:
            s.action.text = None
    return model.to_json()


@dataclass
class GenResult:
    content: dict
    method: str  # "llm" | "heuristic"
    error: str | None = None


UsageCallback = Callable[[int, int, bool], None]


def generate(
    events: list[dict],
    title_hint: str | None,
    llm: LLMProvider | None,
    *,
    allow_llm: bool = True,
    on_usage: UsageCallback | None = None,
) -> GenResult:
    cleaned = cleanup(events)
    error: str | None = None
    if llm is not None and llm.enabled and allow_llm and 0 < len(cleaned) <= MAX_LLM_EVENTS:
        try:
            res = llm.complete_json(SYSTEM_PROMPT, build_prompt(cleaned, title_hint))
            if on_usage:
                on_usage(res.input_tokens, res.output_tokens, True)
            content = postprocess(res.data, cleaned)
            if title_hint and title_hint.strip():
                content["title"] = title_hint.strip()[:200]
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
    return GenResult(heuristic_skill(cleaned, title_hint, cleaned=True), "heuristic", error)
