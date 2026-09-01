"""
HTTP-роуты для roadmap #12 (GET /api/quests, POST /api/quests/{key}/claim)
и #32 (POST /api/shop/stars/{id} для booster_stars).
"""
from db import add_user, add_habit, get_habits

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_quests_route_returns_three(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/quests", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert len(body["quests"]) == 3


async def test_claim_quest_route_rejects_unfinished(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/quests", headers=headers)
    body = await r.json()
    unfinished = next((q for q in body["quests"] if not q["completed"]), None)
    if unfinished is None:
        return
    r2 = await client.post(f"/api/quests/{unfinished['key']}/claim", headers=headers)
    assert r2.status == 400


async def test_claim_quest_route_awards_after_completion(client, uid, monkeypatch):
    # Квесты дня выбираются детерминированно по (user_id, день) из 6
    # возможных — не все 3 обязательно satisfiable двумя обычными
    # привычками (например "before_noon" зависит от времени суток), так
    # что фиксируем набор явно, чтобы тест не был случайно нестабильным.
    import db.quests as quests_module
    monkeypatch.setattr(quests_module, "_pick_quests_for_day", lambda user_id, day: ["two_habits", "all_done", "priority_habit"])

    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    ra = await client.post("/api/habits", headers=headers, json={"title": "Habit A"})
    rb = await client.post("/api/habits", headers=headers, json={"title": "Habit B"})
    assert ra.status == 200, await ra.text()
    assert rb.status == 200, await rb.text()
    for h in get_habits(uid):
        await client.post(f"/api/habits/{h['id']}/complete", headers=headers)

    r = await client.get("/api/quests", headers=headers)
    body = await r.json()
    claimable = next((q for q in body["quests"] if q["completed"] and not q["claimed"]), None)
    assert claimable is not None

    r2 = await client.post(f"/api/quests/{claimable['key']}/claim", headers=headers)
    assert r2.status == 200
    data = await r2.json()
    assert data["reward"] == claimable["reward"]


async def test_stars_invoice_route_accepts_booster_item(client, uid, monkeypatch):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    class FakeBot:
        async def create_invoice_link(self, **kwargs):
            return "https://t.me/fake_invoice"

    r = await client.post("/api/shop/stars/24", headers=headers)
    # Без bot в app — 503, но НЕ 404: подтверждает, что booster_stars
    # прошёл валидацию item_type и не был отклонён как неизвестный тип.
    assert r.status in (503,)
