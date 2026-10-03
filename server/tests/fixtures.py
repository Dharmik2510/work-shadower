"""A realistic recording: log into ClaimCenter (secure password field), look up a policy,
note the claim, then email the adjuster in Outlook (an email address in typed text)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

T0 = datetime(2026, 10, 3, 14, 0, 0, tzinfo=timezone.utc)

CHROME = {"bundle_id": "com.google.Chrome", "name": "Google Chrome"}
OUTLOOK = {"bundle_id": "com.microsoft.Outlook", "name": "Microsoft Outlook"}


def _el(role, label, ident=None, kind="none", window="ClaimCenter"):
    return {"role": role, "subrole": None, "label": label, "identifier": ident,
            "path": [f"AXWindow:{window}", "AXGroup", f"{role}:{label}"], "value_kind": kind}


def realistic_events(shot: str | None = None) -> list[dict]:
    raw = [
        ("app_activate", CHROME, "New Tab", None, {}),
        ("url_change", CHROME, "ClaimCenter Login", None, {"url": "https://claimcenter.example.com/cc/Login.do?session=abc123"}),
        ("click", CHROME, "ClaimCenter Login", _el("AXTextField", "Username", "user", "text", "ClaimCenter Login"), {}),
        ("type", CHROME, "ClaimCenter Login", _el("AXTextField", "Username", "user", "text", "ClaimCenter Login"), {"text": "dsoni"}),
        ("key", CHROME, "ClaimCenter Login", None, {"key": "tab"}),
        ("type", CHROME, "ClaimCenter Login", _el("AXSecureTextField", "Password", "pass", "secure", "ClaimCenter Login"), {"text": "hunter2!"}),
        ("click", CHROME, "ClaimCenter Login", _el("AXButton", "Log In", "loginBtn", "none", "ClaimCenter Login"), {}),
        ("url_change", CHROME, "ClaimCenter", None, {"url": "https://claimcenter.example.com/cc/ClaimCenter.do"}),
        ("scroll", CHROME, "ClaimCenter", None, {}),
        ("click", CHROME, "ClaimCenter", _el("AXLink", "Search Claims", "claimSearch"), {"screenshot_sha256": shot}),
        ("click", CHROME, "ClaimCenter", _el("AXLink", "Search Claims", "claimSearch"), {}),
        ("click", CHROME, "ClaimCenter", _el("AXTextField", "Policy Number", "policyNo", "text"), {}),
        ("type", CHROME, "ClaimCenter", _el("AXTextField", "Policy Number", "policyNo", "text"), {"text": "P-12"}),
        ("type", CHROME, "ClaimCenter", _el("AXTextField", "Policy Number", "policyNo", "text"), {"text": "P-1234"}),
        ("type", CHROME, "ClaimCenter", _el("AXTextField", "Policy Number", "policyNo", "text"), {"text": "P-123456"}),
        ("click", CHROME, "ClaimCenter", _el("AXButton", "Search", "searchBtn"), {}),
        ("window_open", CHROME, "Claim Search Results", None, {}),
        ("app_activate", OUTLOOK, "Inbox", None, {}),
        ("app_activate", CHROME, "ClaimCenter", None, {}),
        ("app_activate", OUTLOOK, "Inbox", None, {}),
        ("click", OUTLOOK, "Inbox", _el("AXButton", "New Email", "newMail", "none", "Inbox"), {}),
        ("window_open", OUTLOOK, "Untitled Message", None, {}),
        ("type", OUTLOOK, "Untitled Message", _el("AXTextField", "To", "toField", "text", "Untitled Message"),
         {"text": "adjuster.team@intact.example.com"}),
        ("type", OUTLOOK, "Untitled Message", _el("AXTextArea", "Message body", "body", "text", "Untitled Message"),
         {"text": "Claim for policy P-123456 is ready. Call me at 416-555-0199."}),
        ("click", OUTLOOK, "Untitled Message", _el("AXButton", "Send", "sendBtn", "none", "Untitled Message"), {}),
        ("scroll", OUTLOOK, "Inbox", None, {}),
    ]
    events = []
    for i, (typ, app, win, el, extra) in enumerate(raw, start=1):
        ev = {"seq": i, "ts": (T0 + timedelta(seconds=2 * i)).isoformat().replace("+00:00", "Z"),
              "type": typ, "app": app, "window": {"title": win}, "element": el,
              "text": None, "key": None, "url": None, "screenshot_sha256": None}
        ev.update({k: v for k, v in extra.items()})
        events.append(ev)
    return events


def recording_body(events=None, title_hint="Look up a claim and email the adjuster") -> dict:
    events = events if events is not None else realistic_events()
    return {
        "title_hint": title_hint,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(minutes=2)).isoformat(),
        "client": {"app_version": "0.1.0", "os_version": "macOS 15.1", "device_id": "dev-1"},
        "events": events,
    }
