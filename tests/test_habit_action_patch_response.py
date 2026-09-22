"""
/api/habits/{id}/complete и /progress теперь возвращают достаточно данных
(user/habit/daily_quests), чтобы клиент мог точечно обновить состояние
(app.js::applyActionPatch) вместо await loadBootstrap() — полного запроса
за ВСЕМ главным экраном и полной перерисовки всех секций ради отметки
одной привычки. См. _shape_user/_shape_habit в webapp_server.py.
"""
from db import add_user, add_habit, get_habits

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_complete_route_returns_user_habit_and_quests(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/habits", headers=headers, json={"title": "Пить воду"})
    habit_id = (await r.json())["habit"]["id"]

    r2 = await client.post(f"/api/habits/{habit_id}/complete", headers=headers)
    assert r2.status == 200
    body = await r2.json()

    assert body["habit"]["id"] == habit_id
    assert body["habit"]["completed"] is True

    assert body["user"]["telegram_id"] == uid
    assert body["user"]["xp"] >= 0
    assert "level" in body["user"]
    assert "diamonds" in body["user"]

    assert len(body["daily_quests"]) == 3


async def test_progress_route_returns_habit_before_target_reached(client, uid):
    # POST /api/habits больше не принимает target_count от клиента (форма
    # добавления привычки лишилась степпера "Сколько раз в день") — но
    # /progress обязан продолжать работать для привычки со счётчиком,
    # заданным напрямую через db.add_habit.
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=4)
    habit_id = get_habits(uid)[0]["id"]
    headers = await _headers(uid)

    r2 = await client.post(f"/api/habits/{habit_id}/progress", headers=headers, json={})
    body = await r2.json()

    assert body["just_completed"] is False
    # Ещё не выполнена целиком — не должно быть user/квестов, только
    # сама привычка с обновлённым progress_count (единственное, что
    # реально изменилось этим нажатием).
    assert "user" not in body
    assert "daily_quests" not in body
    assert body["habit"]["id"] == habit_id
    assert body["habit"]["progress_count"] == 1
    assert body["habit"]["completed"] is False


async def test_progress_route_returns_user_and_quests_on_target_reached(client, uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=2)
    habit_id = get_habits(uid)[0]["id"]
    headers = await _headers(uid)

    await client.post(f"/api/habits/{habit_id}/progress", headers=headers, json={})
    r2 = await client.post(f"/api/habits/{habit_id}/progress", headers=headers, json={})
    body = await r2.json()

    assert body["just_completed"] is True
    assert body["habit"]["completed"] is True
    assert body["user"]["telegram_id"] == uid
    assert len(body["daily_quests"]) == 3


async def test_bootstrap_habits_match_shape_used_by_action_patch(client, uid):
    """_shape_habit используется и в /api/bootstrap, и в /complete|/progress —
    поля должны совпадать, иначе точечный патч на фронте перезатрёт привычку
    объектом другой формы."""
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/habits", headers=headers, json={"title": "Пить воду"})

    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    bootstrap_habit = body["habits"][0]

    r2 = await client.post(f"/api/habits/{bootstrap_habit['id']}/complete", headers=headers)
    action_habit = (await r2.json())["habit"]

    assert set(bootstrap_habit.keys()) == set(action_habit.keys())
