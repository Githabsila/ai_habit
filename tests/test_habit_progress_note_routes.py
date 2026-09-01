"""
HTTP-роуты для roadmap #1 (POST /api/habits/{id}/progress) и #3
(POST /api/habits/{id}/note, GET /api/habits/notes).
"""
from db import add_user

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_create_habit_with_counter_and_progress_route(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post("/api/habits", headers=headers, json={"title": "Пить воду", "target_count": 4})
    assert r.status == 200
    data = await r.json()
    habit_id = data["habit"]["id"]
    assert data["habit"]["target_count"] == 4

    r2 = await client.post(f"/api/habits/{habit_id}/progress", headers=headers, json={})
    body2 = await r2.json()
    assert body2["just_completed"] is False
    assert body2["progress_count"] == 1

    r3 = await client.post(f"/api/habits/{habit_id}/progress", headers=headers, json={"amount": 3})
    body3 = await r3.json()
    assert body3["just_completed"] is True
    assert "coins" in body3


async def test_progress_route_404_for_foreign_habit(client, uid):
    add_user(uid, "u", "Test")
    other = uid + 1
    add_user(other, "u2", "Test2")
    headers_owner = await _headers(other)
    r = await client.post("/api/habits", headers=headers_owner, json={"title": "Чужая", "target_count": 3})
    data = await r.json()
    habit_id = data["habit"]["id"]

    headers_attacker = await _headers(uid)
    r2 = await client.post(f"/api/habits/{habit_id}/progress", headers=headers_attacker, json={})
    assert r2.status == 404


async def test_note_route_saves_and_lists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/habits", headers=headers, json={"title": "Медитация"})
    data = await r.json()
    habit_id = data["habit"]["id"]

    r2 = await client.post(f"/api/habits/{habit_id}/note", headers=headers, json={"note": "Отлично прошло"})
    assert r2.status == 200

    r3 = await client.get("/api/habits/notes", headers=headers)
    body3 = await r3.json()
    assert len(body3["notes"]) == 1
    assert body3["notes"][0]["note"] == "Отлично прошло"


async def test_note_route_rejects_empty(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/habits", headers=headers, json={"title": "Медитация"})
    data = await r.json()
    habit_id = data["habit"]["id"]

    r2 = await client.post(f"/api/habits/{habit_id}/note", headers=headers, json={})
    assert r2.status == 400
