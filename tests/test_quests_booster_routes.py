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


async def test_a_finished_quest_pays_out_by_itself_and_the_claim_route_has_nothing_left(client, uid):
    """П.14: награда за квест выдаётся сама в момент выполнения (раньше была кнопка «Забрать»)."""
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
    habits_quest = next(q for q in body["quests"] if q["key"] == "two_habits")
    assert habits_quest["completed"] and habits_quest["claimed"], "награда уже выдана"

    r2 = await client.post(f"/api/quests/{habits_quest['key']}/claim", headers=headers)
    assert r2.status == 400 and (await r2.json())["error"] == "quest_not_claimable", "забирать больше нечего"


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
