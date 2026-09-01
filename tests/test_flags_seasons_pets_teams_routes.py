"""
HTTP-роуты для roadmap #41 (feature flags admin), #9 (сезон), #11
(питомец), #16 (команды), #18 (лента активности).
"""
from db import add_user

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def _admin_headers(uid_, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid_])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid_])
    return await _headers(uid_)


async def test_pet_route_returns_egg_by_default(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/pet", headers=await _headers(uid))
    assert r.status == 200
    body = await r.json()
    assert body["emoji"] == "🥚"


async def test_season_route_returns_leaderboard(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/season", headers=await _headers(uid))
    assert r.status == 200
    body = await r.json()
    assert "leaderboard" in body


async def test_team_route_null_when_not_in_team(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/team", headers=await _headers(uid))
    body = await r.json()
    assert body["team"] is None


async def test_team_create_join_leave_flow(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r1 = await client.post("/api/team/create", headers=headers, json={"name": "Утро"})
    assert r1.status == 200
    body1 = await r1.json()
    code = body1["team"]["invite_code"]

    other = uid + 80_000_000
    add_user(other, "other", "Other")
    other_headers = await _headers(other)
    r2 = await client.post("/api/team/join", headers=other_headers, json={"invite_code": code})
    assert r2.status == 200

    r3 = await client.get("/api/team", headers=headers)
    body3 = await r3.json()
    assert len(body3["team"]["members"]) == 2

    r4 = await client.post("/api/team/leave", headers=other_headers)
    assert r4.status == 200
    r5 = await client.get("/api/team", headers=other_headers)
    body5 = await r5.json()
    assert body5["team"] is None


async def test_team_join_invalid_code_404(client, uid):
    add_user(uid, "u", "Test")
    r = await client.post("/api/team/join", headers=await _headers(uid), json={"invite_code": "NOPE99"})
    assert r.status == 404


async def test_activity_feed_route_empty_by_default(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/activity-feed", headers=await _headers(uid))
    assert r.status == 200
    body = await r.json()
    assert body["events"] == []


async def test_regular_user_403_on_flags(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/admin/flags", headers=await _headers(uid))
    assert r.status == 403


async def test_admin_can_manage_flags(client, uid, monkeypatch):
    add_user(uid, "admin", "Admin")
    headers = await _admin_headers(uid, monkeypatch)

    r1 = await client.post("/api/admin/flags", headers=headers, json={"key": "new_ui", "enabled": True, "rollout_pct": 50})
    assert r1.status == 200

    r2 = await client.get("/api/admin/flags", headers=headers)
    body2 = await r2.json()
    assert any(f["key"] == "new_ui" for f in body2["flags"])

    r3 = await client.delete("/api/admin/flags/new_ui", headers=headers)
    assert r3.status == 200
    r4 = await client.get("/api/admin/flags", headers=headers)
    body4 = await r4.json()
    assert not any(f["key"] == "new_ui" for f in body4["flags"])


async def test_bootstrap_includes_pet(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get("/api/bootstrap", headers=await _headers(uid))
    body = await r.json()
    assert "pet" in body
    assert body["pet"]["emoji"] == "🥚"
