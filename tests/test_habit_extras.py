"""
Категории/приоритет/пропуск с причиной у привычек + "проценты за верность"
(бонус монет за длинную серию) — пункты 4, 5, 6, 14 из roadmap улучшений.
"""
from db import (
    add_user, add_habit, get_habits, get_habit, edit_habit, skip_habit, unskip_habit,
    get_incomplete_habits, get_habits_needing_reminder, reset_habits, complete_habit,
    get_weekly_habit_breakdown, log_daily_habits, can_add_habit, MAX_HABITS,
)
from db.core import connect

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# КАТЕГОРИЯ И ПРИОРИТЕТ
# =====================================

def test_add_habit_persists_category_and_priority(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", category="health", priority=2)
    habit = get_habits(uid)[0]
    assert habit["category"] == "health"
    assert habit["priority"] == 2


def test_add_habit_rejects_unknown_category(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", category="not_a_real_category")
    habit = get_habits(uid)[0]
    assert habit["category"] is None


def test_max_habits_limit_is_ten():
    # По просьбе пользователя лимит подняли с 7 до 10 — этот тест фиксирует
    # актуальное значение, чтобы случайный откат константы не прошёл незамеченным.
    assert MAX_HABITS == 10


def test_can_add_up_to_ten_habits_then_blocked(uid):
    add_user(uid, "u", "Test")
    for i in range(MAX_HABITS):
        add_habit(uid, f"Привычка {i}")
    assert len(get_habits(uid)) == MAX_HABITS
    ok, reason = can_add_habit(uid)
    assert ok is False
    assert reason == "habit_limit"


def test_add_habit_defaults_priority_to_one(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit = get_habits(uid)[0]
    assert habit["priority"] == 1


def test_edit_habit_updates_category_and_priority(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    edit_habit(habit_id, "Пить воду", category="health", priority=2)
    habit = get_habit(habit_id)
    assert habit["category"] == "health"
    assert habit["priority"] == 2


def test_get_incomplete_habits_orders_priority_first(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Обычная", priority=1)
    add_habit(uid, "Важная", priority=2)
    incomplete = get_incomplete_habits(uid)
    assert incomplete[0]["title"] == "Важная"


# =====================================
# ПРОПУСК С ПРИЧИНОЙ
# =====================================

def test_skip_habit_sets_reason(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    assert skip_habit(habit_id, "Болею") is True
    habit = get_habit(habit_id)
    assert habit["skip_reason"] == "Болею"


def test_skip_habit_rejects_empty_reason(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    assert skip_habit(habit_id, "  ") is False
    assert get_habit(habit_id)["skip_reason"] is None


def test_skip_habit_fails_if_already_completed(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)
    assert skip_habit(habit_id, "Болею") is False


def test_skipped_habit_excluded_from_incomplete_and_reminders(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    skip_habit(habit_id, "Болею")

    assert get_incomplete_habits(uid) == []
    assert get_habits_needing_reminder(uid, hours=0) == []


def test_unskip_habit_clears_reason(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    skip_habit(habit_id, "Болею")
    unskip_habit(habit_id)
    assert get_habit(habit_id)["skip_reason"] is None
    assert len(get_incomplete_habits(uid)) == 1


def test_reset_habits_clears_skip_reason(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    skip_habit(habit_id, "Болею")
    reset_habits()
    assert get_habit(habit_id)["skip_reason"] is None


def test_weekly_breakdown_excludes_skipped_from_missed(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    skip_habit(habit_id, "Болею")

    # log_daily_habits() снимает срез ТЕКУЩЕГО состояния (обычно вызывается
    # в scheduler.new_day() до сброса) — день с датой "вчера".
    log_daily_habits()

    breakdown = get_weekly_habit_breakdown(uid)
    row = next(r for r in breakdown if r["habit_title"] == "Пить воду")
    assert row["missed"] == 0
    assert row["skipped"] == 1


# =====================================
# ПРОЦЕНТЫ ЗА ВЕРНОСТЬ (бонус монет от длины серии)
# =====================================

def test_complete_habit_awards_priority_bonus(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Важная", priority=2)
    habit_id = get_habits(uid)[0]["id"]
    result = complete_habit(habit_id)
    assert result["priority_bonus"] == 5
    assert result["coins"] >= 10 + 5


def test_complete_habit_no_priority_bonus_for_normal_habit(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Обычная", priority=1)
    habit_id = get_habits(uid)[0]["id"]
    result = complete_habit(habit_id)
    assert result["priority_bonus"] == 0


def test_complete_habit_loyalty_bonus_scales_with_streak(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Привычка")
    habit_id = get_habits(uid)[0]["id"]

    conn = connect()
    conn.execute("UPDATE users SET streak=25 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    result = complete_habit(habit_id)
    # 25 // 10 = 2
    assert result["loyalty_bonus"] == 2


def test_complete_habit_loyalty_bonus_is_capped(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Привычка")
    habit_id = get_habits(uid)[0]["id"]

    conn = connect()
    conn.execute("UPDATE users SET streak=999 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    result = complete_habit(habit_id)
    assert result["loyalty_bonus"] == 5


# =====================================
# API
# =====================================

async def test_api_create_habit_with_category_and_priority(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/habits", headers=headers,
        data='{"title": "Пить воду", "category": "health", "priority": 2}',
    )
    assert r.status == 200
    data = await r.json()
    assert data["habit"]["category"] == "health"
    assert data["habit"]["priority"] == 2


async def test_api_skip_habit_requires_reason(client, uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    headers = await _headers(uid)

    r = await client.post(f"/api/habits/{habit_id}/skip", headers=headers, data='{"reason": ""}')
    assert r.status == 400

    r = await client.post(f"/api/habits/{habit_id}/skip", headers=headers, data='{"reason": "Болею"}')
    assert r.status == 200
    assert get_habit(habit_id)["skip_reason"] == "Болею"


async def test_api_unskip_habit(client, uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    headers = await _headers(uid)
    await client.post(f"/api/habits/{habit_id}/skip", headers=headers, data='{"reason": "Болею"}')

    r = await client.post(f"/api/habits/{habit_id}/unskip", headers=headers)
    assert r.status == 200
    assert get_habit(habit_id)["skip_reason"] is None


async def test_api_skip_habit_rejects_other_users_habit(client, uid):
    add_user(uid, "u", "Test")
    other_uid = uid * 10
    add_user(other_uid, "u2", "Test2")
    add_habit(other_uid, "Чужая привычка")
    other_habit_id = get_habits(other_uid)[0]["id"]

    headers = await _headers(uid)
    r = await client.post(f"/api/habits/{other_habit_id}/skip", headers=headers, data='{"reason": "Болею"}')
    assert r.status == 404
