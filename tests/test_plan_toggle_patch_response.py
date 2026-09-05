"""
/api/plan/task/toggle и /api/plan/main/toggle теперь возвращают обновлённый
daily_plan (та же форма, что и /api/bootstrap) — app.js::applyPlanPatch
точечно перерисовывает план дня вместо await loadBootstrap() (полного
запроса и полной перерисовки всего главного экрана ради одной галочки в
плане, которая вообще не трогает XP/монеты/streak/квесты).
"""
from db import add_user, add_daily_task, set_daily_main_goal

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_toggle_task_route_returns_updated_daily_plan(client, uid):
    add_user(uid, "u", "Test")
    task_id = add_daily_task(uid, "Прочитать 10 страниц")
    headers = await _headers(uid)

    r = await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": task_id})
    assert r.status == 200
    body = await r.json()

    plan = body["daily_plan"]
    task = next(t for t in plan["tasks"] if t["id"] == task_id)
    assert task["completed"] is True
    assert task["text"] == "Прочитать 10 страниц"


async def test_toggle_main_goal_route_returns_updated_daily_plan(client, uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Закрыть спринт")
    headers = await _headers(uid)

    r = await client.post("/api/plan/main/toggle", headers=headers)
    assert r.status == 200
    body = await r.json()

    plan = body["daily_plan"]
    assert plan["main_goal"] == "Закрыть спринт"
    assert plan["main_goal_completed"] is True


async def test_bootstrap_daily_plan_matches_shape_used_by_plan_patch(client, uid):
    """_shape_daily_plan используется и в /api/bootstrap, и в toggle-роутах —
    поля должны совпадать, иначе точечный патч перезатрёт план объектом
    другой формы."""
    add_user(uid, "u", "Test")
    task_id = add_daily_task(uid, "Задача")
    headers = await _headers(uid)

    r = await client.get("/api/bootstrap", headers=headers)
    bootstrap_plan = (await r.json())["daily_plan"]

    r2 = await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": task_id})
    toggle_plan = (await r2.json())["daily_plan"]

    assert set(bootstrap_plan.keys()) == set(toggle_plan.keys())
    assert set(bootstrap_plan["tasks"][0].keys()) == set(toggle_plan["tasks"][0].keys())
