"""
Roadmap #22 (AI предлагает снизить планку), #23/#36 (AI подбирает
оптимальное время напоминания), #25 (AI помнит долгосрочные цели).
"""
from db.core import connect

from db import (
    add_user, add_habit, get_habits, complete_habit,
    get_struggling_habits, suggest_optimal_reminder_time,
    set_long_term_goals, get_long_term_goals, MAX_LONG_TERM_GOALS_LENGTH,
)


# =====================================
# ПОСТОЯННО ПРОВАЛИВАЕМЫЕ ПРИВЫЧКИ (roadmap #22)
# =====================================

def test_no_struggling_habits_without_history(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    assert get_struggling_habits(uid) == []


def test_struggling_habit_detected_after_repeated_misses(uid):
    add_user(uid, "u", "Test")
    habit_id = 555001
    conn = connect()
    for i in range(4):
        conn.execute(
            "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
            "VALUES (?,?,?,date('now', ?),0,0)",
            (uid, habit_id, "Медитация", f"-{i} days"),
        )
    conn.commit()
    conn.close()

    struggling = get_struggling_habits(uid)
    assert len(struggling) == 1
    assert struggling[0]["title"] == "Медитация"
    assert struggling[0]["missed"] == 4


def test_skipped_days_dont_count_as_struggling(uid):
    add_user(uid, "u", "Test")
    habit_id = 555002
    conn = connect()
    for i in range(4):
        conn.execute(
            "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
            "VALUES (?,?,?,date('now', ?),0,1)",
            (uid, habit_id, "Бег", f"-{i} days"),
        )
    conn.commit()
    conn.close()
    assert get_struggling_habits(uid) == []


def test_below_threshold_not_flagged(uid):
    add_user(uid, "u", "Test")
    habit_id = 555003
    conn = connect()
    for i in range(2):
        conn.execute(
            "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
            "VALUES (?,?,?,date('now', ?),0,0)",
            (uid, habit_id, "Чтение", f"-{i} days"),
        )
    conn.commit()
    conn.close()
    assert get_struggling_habits(uid) == []


# =====================================
# ОПТИМАЛЬНОЕ ВРЕМЯ НАПОМИНАНИЯ (roadmap #23/#36)
# =====================================

def test_no_suggestion_with_too_few_completions(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка")
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)
    assert suggest_optimal_reminder_time(habit_id, uid) is None


def test_suggestion_after_enough_consistent_completions(uid):
    add_user(uid, "u", "Test")
    habit_id = 555004
    conn = connect()
    conn.execute("INSERT INTO habits(id, user_id, title, completed) VALUES (?,?,?,0)", (habit_id, uid, "Зарядка"))
    for _ in range(5):
        conn.execute(
            "INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?,?,'2026-01-01 08:15:00')",
            (uid, habit_id),
        )
    conn.commit()
    conn.close()
    suggestion = suggest_optimal_reminder_time(habit_id, uid)
    assert suggestion == "08:00"


def test_no_suggestion_when_times_too_scattered(uid):
    add_user(uid, "u", "Test")
    habit_id = 555005
    conn = connect()
    conn.execute("INSERT INTO habits(id, user_id, title, completed) VALUES (?,?,?,0)", (habit_id, uid, "Зарядка"))
    hours = ["06:00", "10:00", "14:00", "18:00", "22:00", "02:00"]
    for h in hours:
        conn.execute(
            "INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?,?,?)",
            (uid, habit_id, f"2026-01-01 {h}:00"),
        )
    conn.commit()
    conn.close()
    assert suggest_optimal_reminder_time(habit_id, uid) is None


# =====================================
# ДОЛГОСРОЧНЫЕ ЦЕЛИ (roadmap #25)
# =====================================

def test_long_term_goals_empty_by_default(uid):
    add_user(uid, "u", "Test")
    assert get_long_term_goals(uid) is None


def test_set_and_get_long_term_goals(uid):
    add_user(uid, "u", "Test")
    set_long_term_goals(uid, "Пробежать марафон к декабрю")
    assert get_long_term_goals(uid) == "Пробежать марафон к декабрю"


def test_long_term_goals_truncated(uid):
    add_user(uid, "u", "Test")
    set_long_term_goals(uid, "x" * 1000)
    assert len(get_long_term_goals(uid)) == MAX_LONG_TERM_GOALS_LENGTH


def test_clear_long_term_goals(uid):
    add_user(uid, "u", "Test")
    set_long_term_goals(uid, "Что-то")
    set_long_term_goals(uid, "")
    assert get_long_term_goals(uid) is None
