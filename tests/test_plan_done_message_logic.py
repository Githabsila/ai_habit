"""
Сообщение "все задачи готовы" / "главная задача закрыта" должно учитывать
ВЕСЬ план дня (главная цель + второстепенные), а не только второстепенные.

Фидбек пользователя:
1. Отметил вторую задачу, а первую (главную цель) нет — а система написала
   "все задачи готовы".
2. Закрыл сначала второстепенную, потом главную — написало "главная задача
   закрыта, самое важное позади", хотя это был последний пункт плана и
   правильнее "всё готово".
"""
from db import add_user, add_daily_task, set_daily_main_goal, toggle_daily_task, get_daily_plan

from tests.conftest import sign_init_data

from adam_messages import ALL_TASKS_DONE_TEMPLATES, MAIN_GOAL_DONE_TEMPLATES


def _is_all_done(msg):
    return msg is not None and any(msg.startswith(t.split("{")[0][:20]) for t in ALL_TASKS_DONE_TEMPLATES)


def _is_main_goal_done(msg):
    return msg is not None and any(msg.startswith(t.split("{")[0][:20]) for t in MAIN_GOAL_DONE_TEMPLATES)


async def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def test_secondary_done_but_main_goal_open_is_not_all_done(client, uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Главная цель")
    task_id = add_daily_task(uid, "Второстепенная")
    headers = await _headers(uid)

    r = await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": task_id})
    msg = (await r.json())["message"]
    # Все второстепенные закрыты, но главная цель ещё открыта — НЕ "всё готово"
    assert not _is_all_done(msg), msg


async def test_last_item_is_main_goal_gives_all_done_message(client, uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Главная цель")
    task_id = add_daily_task(uid, "Второстепенная")
    headers = await _headers(uid)

    # сначала закрываем второстепенную
    await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": task_id})
    # потом — главную цель (последний открытый пункт)
    r = await client.post("/api/plan/main/toggle", headers=headers)
    msg = (await r.json())["message"]
    assert _is_all_done(msg), msg


async def test_main_goal_first_while_tasks_open_gives_main_goal_message(client, uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Главная цель")
    add_daily_task(uid, "Ещё открытая задача")
    headers = await _headers(uid)

    r = await client.post("/api/plan/main/toggle", headers=headers)
    msg = (await r.json())["message"]
    # Главная закрыта первой, но остались задачи — "главная закрыта", не "всё готово"
    assert _is_main_goal_done(msg), msg
    assert not _is_all_done(msg), msg


async def test_last_secondary_with_main_goal_done_gives_all_done(client, uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Главная цель")
    t1 = add_daily_task(uid, "Задача 1")
    t2 = add_daily_task(uid, "Задача 2")
    headers = await _headers(uid)

    await client.post("/api/plan/main/toggle", headers=headers)
    await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": t1})
    r = await client.post("/api/plan/task/toggle", headers=headers, json={"task_id": t2})
    msg = (await r.json())["message"]
    assert _is_all_done(msg), msg
