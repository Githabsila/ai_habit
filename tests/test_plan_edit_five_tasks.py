import pytest

from db import add_user, add_daily_task, set_daily_main_goal, get_daily_plan

from tests.conftest import sign_init_data


@pytest.mark.asyncio
async def test_editing_fifth_task_is_supported_by_the_plan_api(client, uid):
    """Редактирование 5-й задачи не должно считаться добавлением 6-й."""
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Главная задача")
    task_ids = [add_daily_task(uid, f"Задача {i}") for i in range(1, 6)]

    headers = {"Authorization": f"tma {sign_init_data(uid)}", "Content-Type": "application/json"}
    r = await client.put(
        f"/api/plan/task/{task_ids[-1]}",
        headers=headers,
        json={"text": "Пятая задача изменена"},
    )

    assert r.status == 200
    body = await r.json()
    assert body["ok"] is True
    assert body["daily_plan"]["tasks"][-1]["text"] == "Пятая задача изменена"
    assert len(body["daily_plan"]["tasks"]) == 5
