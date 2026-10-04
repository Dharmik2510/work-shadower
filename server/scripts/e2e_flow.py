"""End-to-end check against a running server, acting exactly like the Mac app would.

    python scripts/e2e_flow.py [BASE_URL]

1. Mac signs in, uploads a screenshot (presign → PUT → dedup on 2nd try),
   posts a recording with an Idempotency-Key (and retries it)
2. Worker turns it into a draft skill (PII redacted)
3. Author publishes; a colleague on another team finds it in search
4. Colleague runs it (run → steps → finish) and skill health updates
5. Admin flips the kill switch; new recordings are refused
"""
from __future__ import annotations

import hashlib
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
API = BASE + "/api/v1"


def login(c: httpx.Client, email: str, name: str, team: str) -> dict:
    r = c.post(f"{API}/auth/dev-login", json={"email": email, "name": name, "team": team})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def ev(seq, type_, app="Google Chrome", bundle="com.google.Chrome", window="ClaimCenter", **kw):
    e = {"seq": seq, "ts": f"2026-10-03T14:00:{seq:02d}.000Z", "type": type_,
         "app": {"bundle_id": bundle, "name": app}, "window": {"title": window}}
    e.update(kw)
    return e


def el(role, label, ident=None, value_kind="none"):
    return {"role": role, "subrole": None, "label": label, "identifier": ident,
            "path": ["AXWindow:ClaimCenter", "AXGroup", f"{role}:{label}"], "value_kind": value_kind}


def main() -> None:
    c = httpx.Client(timeout=20)
    author = login(c, "maya@example.com", "Maya Chen", "Claims")
    colleague = login(c, "sam@example.com", "Sam Patel", "UBI")
    admin = login(c, "dharmik@example.com", "Dharmik", "UBI")

    # 1. screenshot upload with content-hash dedup
    img = b"\xff\xd8\xff\xe0" + uuid.uuid4().bytes * 64 + b"\xff\xd9"
    sha = hashlib.sha256(img).hexdigest()
    p = c.post(f"{API}/assets/presign", headers=author,
               json={"sha256": sha, "content_type": "image/jpeg", "bytes": len(img)}).json()
    assert p["exists"] is False and p["upload"], p
    up = p["upload"]
    r = c.request(up["method"], up["url"], content=img, headers={**up.get("headers", {}), **author})
    assert r.status_code < 300, r.text
    again = c.post(f"{API}/assets/presign", headers=author,
                   json={"sha256": sha, "content_type": "image/jpeg", "bytes": len(img)}).json()
    assert again["exists"] is True, again
    print("✓ screenshot uploaded, second presign deduplicated")

    events = [
        ev(1, "app_activate"),
        ev(2, "url_change", url="https://claimcenter.example.com/claims/new"),
        ev(3, "click", element=el("AXButton", "New claim", "newClaim"), screenshot_sha256=sha),
        ev(4, "type", element=el("AXTextField", "Policy number", "policyNo", "text"), text="P-123456"),
        ev(5, "type", element=el("AXTextField", "Reporter email", "email", "text"), text="jane.doe@gmail.com"),
        ev(6, "type", element=el("AXTextField", "Password", None, "secure"), text="hunter2"),
        ev(7, "click", element=el("AXPopUpButton", "Loss type")),
        ev(8, "click", element=el("AXMenuItem", "Collision")),
        ev(9, "key", key="cmd+s"),
        ev(10, "click", element=el("AXButton", "Submit claim", "submit"), screenshot_sha256=sha),
        ev(11, "app_activate", app="Microsoft Outlook", bundle="com.microsoft.Outlook", window="Inbox"),
        ev(12, "click", app="Microsoft Outlook", bundle="com.microsoft.Outlook", window="Inbox",
           element=el("AXButton", "New Email")),
        ev(13, "type", app="Microsoft Outlook", bundle="com.microsoft.Outlook", window="New message",
           element=el("AXTextField", "To", None, "text"), text="claims-team@example.com"),
        ev(14, "click", app="Microsoft Outlook", bundle="com.microsoft.Outlook", window="New message",
           element=el("AXButton", "Send")),
    ]
    body = {"title_hint": "Workflow in Google Chrome, Microsoft Outlook",
            "intent": "Filed a new auto claim and emailed the claims team",
            "started_at": "2026-10-03T14:00:00Z",
            "ended_at": "2026-10-03T14:02:00Z",
            "client": {"app_version": "0.1.0", "os_version": "macOS 15.0", "device_id": str(uuid.uuid4())},
            "events": events}
    key = str(uuid.uuid4())
    r1 = c.post(f"{API}/recordings", headers={**author, "Idempotency-Key": key}, json=body)
    r2 = c.post(f"{API}/recordings", headers={**author, "Idempotency-Key": key}, json=body)
    assert r1.status_code == 202 and r2.json()["id"] == r1.json()["id"], (r1.text, r2.text)
    rec_id = r1.json()["id"]
    print(f"✓ recording accepted; retry with same Idempotency-Key returned the same id ({r2.status_code})")

    # 2. worker → draft skill
    for _ in range(60):
        rec = c.get(f"{API}/recordings/{rec_id}", headers=author).json()
        if rec["status"] in ("ready", "failed"):
            break
        time.sleep(0.5)
    assert rec["status"] == "ready", rec
    skill = c.get(f"{API}/skills/{rec['skill_id']}", headers=author).json()
    draft = skill["draft"]
    blob = str(draft)
    assert "jane.doe@gmail.com" not in blob and "hunter2" not in blob, "PII leaked into skill"
    assert rec["intent"] == body["intent"] and rec["filter"] is not None, rec
    print(f"✓ worker drafted “{draft['title']}” with {len(draft['steps'])} steps; email + password not present")
    print(f"✓ filter ({rec['filter']['source']}): {rec['filter']['counts']}")
    for s in draft["steps"]:
        flag = "  ⚠ irreversible" if s.get("irreversible") else ""
        flag += "  (left out)" if s.get("excluded") else ""
        print(f"    {s['index']:>2}. {s['title']}{flag}")

    # colleague can't see the draft
    assert c.get(f"{API}/skills/{skill['id']}", headers=colleague).status_code in (403, 404)

    # 3. publish org-wide, colleague searches
    r = c.patch(f"{API}/skills/{skill['id']}", headers=author,
                json={"visibility": "org", "content": {**draft, "title": "File a new auto claim in ClaimCenter",
                                                         "tags": ["claims", "onboarding"]}})
    r.raise_for_status()
    pub = c.post(f"{API}/skills/{skill['id']}/publish", headers=author).json()
    assert pub["current_version"] == 1 and pub["status"] == "published", pub
    hits = c.get(f"{API}/search", headers=colleague, params={"q": "how do I file an auto claim"}).json()["items"]
    assert any(h["id"] == skill["id"] for h in hits), [h["title"] for h in hits]
    print(f"✓ published v1; colleague on another team finds it in search (rank {[h['id'] for h in hits].index(skill['id']) + 1})")

    # 4. replay telemetry
    run = c.post(f"{API}/runs", headers={**colleague, "Idempotency-Key": str(uuid.uuid4())},
                 json={"skill_id": skill["id"], "version": 1, "mode": "guided", "inputs": {}}).json()
    for s in draft["steps"]:
        strategy = "human" if s.get("irreversible") else "deterministic"
        status = "confirmed" if s.get("irreversible") else "ok"
        c.post(f"{API}/runs/{run['id']}/steps", headers=colleague,
               json={"step_index": s["index"], "status": status, "strategy": strategy, "duration_ms": 800}).raise_for_status()
    c.post(f"{API}/runs/{run['id']}/finish", headers=colleague, json={"status": "succeeded"}).raise_for_status()
    health = c.get(f"{API}/skills/{skill['id']}", headers=colleague).json()["health"]
    assert health["runs"] == 1 and health["success_rate"] == 1.0, health
    print(f"✓ colleague ran it; health = {health['runs']} run, {health['success_rate']:.0%} success")

    # 5. kill switch
    flags = c.get(f"{API}/admin/flags", headers=admin).json()
    c.put(f"{API}/admin/flags", headers=admin, json={**flags, "recording_enabled": False}).raise_for_status()
    assert c.get(f"{API}/config", headers=author).json()["recording_enabled"] is False
    r = c.post(f"{API}/recordings", headers={**author, "Idempotency-Key": str(uuid.uuid4())}, json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "recording_disabled", r.text
    c.put(f"{API}/admin/flags", headers=admin, json={**flags, "recording_enabled": True}).raise_for_status()
    print("✓ kill switch: recordings refused with 403 recording_disabled, then re-enabled")

    usage = c.get(f"{API}/admin/usage", headers=admin).json()
    print(f"✓ admin usage: {usage['skills_created']} skills created, {usage['runs']} runs, "
          f"{usage['llm_calls']} LLM calls, ${usage['est_cost_usd']:.4f}")
    print("\nEND-TO-END OK")


if __name__ == "__main__":
    main()
