"""
HTTP-роуты для roadmap #17 (публичный профиль) и #19 (реакции).
"""
from db import add_user

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_public_profile_route_404_when_disabled(client, uid):
    add_user(uid, "u", "Test")
    r = await client.get(f"/api/public/profile/{uid}")
    assert r.status == 404


async def test_public_profile_route_200_when_enabled(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r0 = await client.post("/api/settings/public-profile", headers=headers, json={"enabled": True})
    assert r0.status == 200

    r = await client.get(f"/api/public/profile/{uid}")
    assert r.status == 200
    body = await r.json()
    assert body["telegram_id"] == uid


async def test_public_profile_page_serves_html(client, uid):
    r = await client.get(f"/u/{uid}")
    assert r.status == 200
    assert "text/html" in r.headers["Content-Type"]


async def test_react_route_requires_valid_target(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/friends/999999999999/react", headers=headers, json={"emoji": "🔥"})
    assert r.status == 404


async def test_react_route_rejects_self(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(f"/api/friends/{uid}/react", headers=headers, json={"emoji": "🔥"})
    assert r.status == 400


async def test_react_route_succeeds_and_notifies(client, uid):
    add_user(uid, "u", "Test")
    target = uid + 30_000_000
    add_user(target, "target", "Target")
    headers = await _headers(uid)

    r = await client.post(f"/api/friends/{target}/react", headers=headers, json={"emoji": "🔥"})
    assert r.status == 200

    r2 = await client.get("/api/reactions", headers=await _headers(target))
    body2 = await r2.json()
    assert len(body2["reactions"]) == 1
    assert body2["reactions"][0]["emoji"] == "🔥"


async def test_react_route_rejects_second_same_day(client, uid):
    add_user(uid, "u", "Test")
    target = uid + 40_000_000
    add_user(target, "target", "Target")
    headers = await _headers(uid)

    r1 = await client.post(f"/api/friends/{target}/react", headers=headers, json={"emoji": "🔥"})
    assert r1.status == 200
    r2 = await client.post(f"/api/friends/{target}/react", headers=headers, json={"emoji": "💪"})
    assert r2.status == 400
