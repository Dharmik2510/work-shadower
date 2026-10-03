"""Server-side re-redaction of recorded events (contract: client AND server re-check).

Rules:
  * never keep `text` for element.value_kind == "secure"
  * replace emails, card-like digit runs (13-19 digits), SIN-like 3-3-3 digit groups and
    phone numbers with "[REDACTED:<kind>]"
  * URLs: query string and fragment stripped
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 13-19 digits, optionally separated by single spaces/dashes (e.g. 4111 1111 1111 1111)
_CARD = re.compile(r"(?<![\w])(?:\d[ \-]?){12,18}\d(?![\w])")
# North-American style phone: optional +1, area code (optionally in parens), 3-4 digits
_PHONE = re.compile(r"(?<![\w+(])(?:\+?1[ .\-]?)?(?:\(\d{3}\)|\d{3})[ .\-]?\d{3}[ .\-]?\d{4}(?![\w])")
_SIN = re.compile(r"(?<![\w])\d{3}[- ]?\d{3}[- ]?\d{3}(?![\w])")

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL),
    ("card", _CARD),
    ("phone", _PHONE),
    ("sin", _SIN),
]


def redact_text(s: str | None) -> str | None:
    if not s:
        return s
    for kind, pat in _RULES:
        s = pat.sub(f"[REDACTED:{kind}]", s)
    return s


def strip_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def redact_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of one event dict (input is not mutated)."""
    out = dict(ev)
    element = dict(out.get("element") or {}) if out.get("element") is not None else None
    secure = bool(element and element.get("value_kind") == "secure")
    if secure:
        out["text"] = None
    elif out.get("text") is not None:
        out["text"] = redact_text(str(out["text"]))
    if element is not None:
        if element.get("label"):
            element["label"] = redact_text(element["label"])
        if isinstance(element.get("path"), list):
            element["path"] = [redact_text(p) if isinstance(p, str) else p for p in element["path"]]
        out["element"] = element
    if isinstance(out.get("window"), dict) and out["window"].get("title"):
        out["window"] = {**out["window"], "title": redact_text(out["window"]["title"])}
    if out.get("url"):
        out["url"] = redact_text(strip_url(out["url"]))
    return out


def redact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [redact_event(e) for e in events]


_NO_REDACT_KEYS = {"screenshot_sha256", "index"}


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string in a JSON-like object (used on LLM output)."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in _NO_REDACT_KEYS else redact_obj(v)) for k, v in obj.items()}
    return obj
