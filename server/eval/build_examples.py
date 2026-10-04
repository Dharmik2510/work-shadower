"""Regenerates the starter eval set in eval/recordings/. Real labelled recordings (exported with
GET /api/v1/admin/filter/export and hand-checked) should be added next to these over time.

Each file: {"name", "intent", "events", "labels": {seq: "keep"|"drop"}, "expected_segments"}
Labels are for the RAW event seqs; events removed by deterministic cleanup are not scored.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

T0 = datetime(2026, 10, 3, 14, 0, 0, tzinfo=timezone.utc)
CHROME = {"bundle_id": "com.google.Chrome", "name": "Google Chrome"}
SLACK = {"bundle_id": "com.tinyspeck.slackmacgap", "name": "Slack"}
OUTLOOK = {"bundle_id": "com.microsoft.Outlook", "name": "Microsoft Outlook"}
EXCEL = {"bundle_id": "com.microsoft.Excel", "name": "Microsoft Excel"}
WORD = {"bundle_id": "com.microsoft.Word", "name": "Microsoft Word"}
OUT = Path(__file__).parent / "recordings"


def el(role, label, ident=None, kind="none", window="Window"):
    return {"role": role, "subrole": None, "label": label, "identifier": ident,
            "path": [f"AXWindow:{window}", "AXGroup", f"{role}:{label}"], "value_kind": kind}


def build(rows):
    """rows: (seconds_offset, type, app, window, element|None, extra, label)"""
    events, labels = [], {}
    for i, (t, typ, app, win, e, extra, label) in enumerate(rows, start=1):
        ev = {"seq": i, "ts": (T0 + timedelta(seconds=t)).isoformat().replace("+00:00", "Z"), "type": typ,
              "app": app, "window": {"title": win}, "element": e, "text": None, "key": None, "url": None,
              "screenshot_sha256": None}
        ev.update(extra)
        events.append(ev)
        labels[str(i)] = label
    return events, labels


def scenarios():
    K, D = "keep", "drop"
    out = []

    w = "Guidewire PolicyCenter"
    ev, lb = build([
        (0, "app_activate", CHROME, w, None, {}, K),
        (2, "url_change", CHROME, w, None, {"url": "https://pc.example.com/pc/Policy.do"}, K),
        (5, "click", CHROME, w, el("AXTextField", "Policy Number", "policyNo", "text", w), {}, K),
        (7, "type", CHROME, w, el("AXTextField", "Policy Number", "policyNo", "text", w), {"text": "P-778812"}, K),
        (9, "click", CHROME, w, el("AXButton", "Search", "searchBtn", window=w), {}, K),
        (14, "app_activate", SLACK, "general", None, {}, D),
        (16, "click", SLACK, "general", el("AXLink", "#random", "chan-random", window="general"), {}, D),
        (24, "app_activate", CHROME, w, None, {}, D),
        (27, "click", CHROME, w, el("AXButton", "Endorse", "endorseBtn", window=w), {}, K),
        (31, "click", CHROME, w, el("AXPopUpButton", "Change Type", "chgType", window=w), {}, K),
        (34, "click", CHROME, w, el("AXMenuItem", "Add Driver", "addDriver", window=w), {}, K),
        (38, "click", CHROME, w, el("AXTextField", "Driver Name", "drvName", "text", w), {}, K),
        (40, "type", CHROME, w, el("AXTextField", "Driver Name", "drvName", "text", w), {"text": "Sam Lee"}, K),
        (45, "click", CHROME, w, el("AXButton", "Submit", "submitBtn", window=w), {}, K),
    ])
    out.append({"name": "slack_detour", "intent": "Added a driver to an auto policy in PolicyCenter",
                "events": ev, "labels": lb, "expected_segments": 1})

    w = "Notice.docx"
    ev, lb = build([
        (0, "app_activate", WORD, w, None, {}, K),
        (3, "click", WORD, w, el("AXTextArea", "Document", "doc", "text", w), {}, K),
        (5, "type", WORD, w, el("AXTextArea", "Document", "doc", "text", w), {"text": "Dear policyholder,"}, K),
        (9, "click", WORD, w, el("AXButton", "Bold", "boldBtn", window=w), {}, D),
        (10, "key", WORD, w, None, {"key": "cmd+z"}, D),
        (14, "click", WORD, w, el("AXButton", "Insert Signature", "sigBtn", window=w), {}, K),
        (18, "key", WORD, w, None, {"key": "cmd+s"}, K),
    ])
    out.append({"name": "undo_mistake", "intent": "Wrote a renewal notice letter and signed it",
                "events": ev, "labels": lb, "expected_segments": 1})

    w = "ClaimCenter"
    ev, lb = build([
        (0, "app_activate", CHROME, w, None, {}, K),
        (2, "url_change", CHROME, w, None, {"url": "https://cc.example.com/cc/Claims.do"}, K),
        (5, "click", CHROME, w, el("AXButton", "Filters", "filters", window=w), {}, D),
        (7, "key", CHROME, w, None, {"key": "escape"}, D),
        (9, "click", CHROME, w, el("AXLink", "Help", "help", window=w), {}, D),
        (10, "url_change", CHROME, "Help", None, {"url": "https://cc.example.com/cc/Help.do"}, D),
        (15, "url_change", CHROME, w, None, {"url": "https://cc.example.com/cc/Claims.do"}, D),
        (18, "click", CHROME, w, el("AXRow", "Claim 000-12-3344", "row1", window=w), {}, K),
        (22, "click", CHROME, w, el("AXButton", "Assign to Me", "assign", window=w), {}, K),
    ])
    out.append({"name": "explore_then_work", "intent": "Assigned a claim to myself",
                "events": ev, "labels": lb, "expected_segments": 1})

    w1, w2 = "Q3 Losses.xlsx", "Inbox"
    ev, lb = build([
        (0, "app_activate", EXCEL, w1, None, {}, K),
        (3, "click", EXCEL, w1, el("AXCell", "B2", "B2", window=w1), {}, K),
        (5, "type", EXCEL, w1, el("AXCell", "B2", "B2", "text", w1), {"text": "1250"}, K),
        (8, "click", EXCEL, w1, el("AXCell", "B3", "B3", window=w1), {}, K),
        (10, "type", EXCEL, w1, el("AXCell", "B3", "B3", "text", w1), {"text": "980"}, K),
        (13, "key", EXCEL, w1, None, {"key": "cmd+s"}, K),
        (400, "app_activate", OUTLOOK, w2, None, {}, K),
        (403, "click", OUTLOOK, w2, el("AXButton", "New Meeting", "newMeeting", window=w2), {}, K),
        (406, "type", OUTLOOK, "Meeting", el("AXTextField", "Title", "title", "text", "Meeting"), {"text": "Q3 review"}, K),
        (410, "click", OUTLOOK, "Meeting", el("AXButton", "Add Room", "room", window="Meeting"), {}, K),
        (414, "click", OUTLOOK, "Meeting", el("AXButton", "Send", "send", window="Meeting"), {}, K),
    ])
    out.append({"name": "two_tasks", "intent": None, "events": ev, "labels": lb, "expected_segments": 2})

    w = "Billing"
    ev, lb = build([
        (0, "app_activate", CHROME, w, None, {}, K),
        (2, "click", CHROME, w, el("AXButton", "Refresh", "refresh", window=w), {}, K),
        (4, "click", CHROME, w, el("AXLink", "Invoices", "inv", window=w), {}, K),
        (6, "click", CHROME, w, el("AXButton", "Refresh", "refresh", window=w), {}, D),
        (9, "click", CHROME, w, el("AXRow", "Invoice 4471", "inv4471", window=w), {}, K),
        (12, "click", CHROME, w, el("AXButton", "Download PDF", "pdf", window=w), {}, K),
    ])
    out.append({"name": "duplicate_click", "intent": "Downloaded an invoice PDF from billing",
                "events": ev, "labels": lb, "expected_segments": 1})

    w = "Outlook"
    ev, lb = build([
        (0, "app_activate", CHROME, "Portal", None, {}, K),
        (2, "click", CHROME, "Portal", el("AXButton", "Export Report", "export", window="Portal"), {}, K),
        (8, "app_activate", OUTLOOK, w, None, {}, K),
        (10, "click", OUTLOOK, w, el("AXButton", "New Email", "new", window=w), {}, K),
        (12, "type", OUTLOOK, "Message", el("AXTextField", "To", "to", "text", "Message"), {"text": "Broker team"}, K),
        (16, "click", OUTLOOK, "Message", el("AXButton", "Attach File", "attach", window="Message"), {}, K),
        (20, "click", OUTLOOK, "Message", el("AXButton", "Send", "send", window="Message"), {}, K),
    ])
    out.append({"name": "two_apps_no_detour", "intent": "Exported the weekly report and emailed it to the broker team",
                "events": ev, "labels": lb, "expected_segments": 1})
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for sc in scenarios():
        (OUT / f"{sc['name']}.json").write_text(json.dumps(sc, indent=1) + "\n")
        print("wrote", sc["name"])
