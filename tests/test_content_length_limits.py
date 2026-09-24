"""
Просьба пользователя: раньше название привычки/текст задачи ничем не
ограничивались по длине. Обрезаем молча (тот же приём, что и
db.users.set_long_term_goals) — с maxlength на инпуте пользователь в
обычном сценарии с обрезкой на лету вообще не столкнётся.

Заодно расширили количество второстепенных задач дня с 5 до 9 (с главной
задачей — 10 пунктов плана всего).
"""
from db import add_user, add_habit, edit_habit, get_habit, get_habits
from db.habits import MAX_HABIT_TITLE_LENGTH
from db.daily_plan import (
    add_daily_task, update_daily_plan_task, set_daily_main_goal, get_daily_plan,
    MAX_DAILY_TASKS, MAX_DAILY_TASK_TEXT_LENGTH,
)

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# ДЛИНА НАЗВАНИЯ ПРИВЫЧКИ
# =====================================

def test_add_habit_truncates_long_title(uid):
    add_user(uid, "u", "Test")
    long_title = "A" * 200
    habit_id = add_habit(uid, long_title)
    habit = get_habit(habit_id)
    assert len(habit["title"]) == MAX_HABIT_TITLE_LENGTH
    assert habit["title"] == "A" * MAX_HABIT_TITLE_LENGTH


def test_edit_habit_truncates_long_title(uid):
    add_user(uid, "u", "Test")
    habit_id = add_habit(uid, "Коротко")
    edit_habit(habit_id, "B" * 200)
    habit = get_habit(habit_id)
    assert len(habit["title"]) == MAX_HABIT_TITLE_LENGTH


async def test_create_habit_route_truncates_and_returns_created_habit(client, uid):
    """Регрессия: раньше созданная привычка искалась по совпадению title —
    после обрезки длинного названия это совпадение сломалось бы."""
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    long_title = "Очень длинное название привычки, которое раньше можно было писать бесконечно " * 3
    r = await client.post("/api/habits", headers=headers, json={"title": long_title})
    assert r.status == 200
    body = await r.json()
    assert body["habit"] is not None
    assert len(body["habit"]["title"]) == MAX_HABIT_TITLE_LENGTH
    assert body["habit"]["title"] == long_title.strip()[:MAX_HABIT_TITLE_LENGTH]


# =====================================
# ДЛИНА ТЕКСТА ЗАДАЧИ / ГЛАВНОЙ ЦЕЛИ ДНЯ
# =====================================

def test_add_daily_task_truncates_long_text(uid):
    add_user(uid, "u", "Test")
    add_daily_task(uid, "T" * 300)
    plan = get_daily_plan(uid)
    assert len(plan["tasks"][0]["text"]) == MAX_DAILY_TASK_TEXT_LENGTH


def test_update_daily_plan_task_truncates_long_text(uid):
    add_user(uid, "u", "Test")
    task_id = add_daily_task(uid, "Коротко")
    update_daily_plan_task(uid, task_id, "U" * 300)
    plan = get_daily_plan(uid)
    task = next(t for t in plan["tasks"] if t["id"] == task_id)
    assert len(task["text"]) == MAX_DAILY_TASK_TEXT_LENGTH


def test_set_daily_main_goal_truncates_long_text(uid):
    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "G" * 300)
    plan = get_daily_plan(uid)
    assert len(plan["main_goal"]) == MAX_DAILY_TASK_TEXT_LENGTH


# =====================================
# ЛИМИТ ВТОРОСТЕПЕННЫХ ЗАДАЧ: 5 -> 9
# =====================================

def test_daily_task_limit_is_now_nine(uid):
    add_user(uid, "u", "Test")
    assert MAX_DAILY_TASKS == 9
    for i in range(9):
        assert add_daily_task(uid, f"Задача {i}") is not None
    try:
        add_daily_task(uid, "Десятая задача")
        assert False, "ожидалась ValueError('task_limit')"
    except ValueError as exc:
        assert str(exc) == "task_limit"


async def test_plan_task_route_allows_nine_and_rejects_tenth(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    for i in range(9):
        r = await client.post("/api/plan/task", headers=headers, json={"text": f"Задача {i}"})
        assert r.status == 200

    r = await client.post("/api/plan/task", headers=headers, json={"text": "Десятая"})
    assert r.status == 400
    body = await r.json()
    assert body["error"] == "task_limit"
