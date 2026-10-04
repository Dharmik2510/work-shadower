"""Personal avatar (dot character)."""
from __future__ import annotations

from .conftest import login
from .test_skills_flow import _content


def test_avatar_default_update_and_owner_ref(client):
    h = login(client, "ava@example.com")
    assert client.get("/api/v1/me", headers=h).json()["avatar"] == "orb"
    r = client.patch("/api/v1/me", headers=h, json={"avatar": "sprout"})
    assert r.status_code == 200 and r.json()["avatar"] == "sprout"
    assert client.get("/api/v1/me", headers=h).json()["avatar"] == "sprout"
    assert client.patch("/api/v1/me", headers=h, json={"avatar": "dragon"}).status_code == 422
    assert client.patch("/api/v1/me", headers=h, json={"avatar": "orb", "role": "admin"}).status_code == 422
    assert client.patch("/api/v1/me", json={"avatar": "orb"}).status_code == 401
    sk = client.post("/api/v1/skills", headers=h, json={"content": _content(), "visibility": "private"}).json()
    assert sk["owner"]["avatar"] == "sprout"
    items = client.get("/api/v1/skills", headers=h, params={"mine": True}).json()["items"]
    assert items[0]["owner"]["avatar"] == "sprout"
