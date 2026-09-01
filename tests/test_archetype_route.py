"""Roadmap #39 — тест на архетип личности."""
from db import add_user, get_user, ARCHETYPES

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_set_archetype_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/archetype", headers=headers, json={"archetype": "strategist"})
    assert r.status == 200
    assert get_user(uid)["archetype"] == "strategist"


async def test_set_archetype_rejects_unknown_key(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/archetype", headers=headers, json={"archetype": "wizard"})
    assert r.status == 400


async def test_bootstrap_reflects_archetype_label(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/archetype", headers=headers, json={"archetype": "explorer"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["user"]["archetype"] == ARCHETYPES["explorer"]


async def test_bootstrap_archetype_null_by_default(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["user"]["archetype"] is None
