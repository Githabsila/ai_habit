"""
Roadmap #1 (счётчик — "выпить 4 стакана"), #2 (гибкая периодичность —
3 раза в неделю), #3 (заметка/фото к выполненной привычке) и #7 (цепочки
привычек — "сделал А → предложи Б").
"""
from db import (
    add_user, add_habit, get_habits, get_habit, edit_habit,
    increment_habit_progress, get_weekly_progress,
    add_habit_note, get_habit_note, get_recent_habit_notes,
    complete_habit, get_incomplete_habits, get_habits_needing_reminder,
    reset_habits, log_daily_habits,
)
from db.core import connect


# =====================================
# СЧЁТЧИК (roadmap #1)
# =====================================

def test_add_habit_persists_target_count(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=4)
    habit = get_habits(uid)[0]
    assert habit["target_count"] == 4
    assert habit["progress_count"] == 0


def test_target_count_clamped_to_max(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=999)
    habit = get_habits(uid)[0]
    assert habit["target_count"] == 20


def test_increment_progress_does_not_complete_before_target(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=4)
    habit_id = get_habits(uid)[0]["id"]

    result = increment_habit_progress(habit_id)

    assert result["just_completed"] is False
    assert result["progress_count"] == 1
    assert result["target_count"] == 4
    assert get_habit(habit_id)["completed"] == 0


def test_increment_progress_completes_habit_on_reaching_target(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=2)
    habit_id = get_habits(uid)[0]["id"]

    increment_habit_progress(habit_id)
    result = increment_habit_progress(habit_id)

    assert result["just_completed"] is True
    assert "coins" in result
    assert get_habit(habit_id)["completed"] == 1


def test_increment_progress_does_not_exceed_target(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=2)
    habit_id = get_habits(uid)[0]["id"]
    increment_habit_progress(habit_id, amount=10)
    assert get_habit(habit_id)["progress_count"] == 2


def test_direct_complete_habit_fills_progress_count(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=4)
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)
    assert get_habit(habit_id)["progress_count"] == 4


def test_reset_habits_clears_progress_count(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду", target_count=4)
    habit_id = get_habits(uid)[0]["id"]
    increment_habit_progress(habit_id)
    reset_habits()
    assert get_habit(habit_id)["progress_count"] == 0


# =====================================
# ГИБКАЯ ПЕРИОДИЧНОСТЬ (roadmap #2)
# =====================================

def test_add_habit_persists_frequency_per_week(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Бег", frequency_per_week=3)
    habit = get_habits(uid)[0]
    assert habit["frequency_per_week"] == 3


def test_frequency_per_week_clamped_to_max(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Бег", frequency_per_week=50)
    habit = get_habits(uid)[0]
    assert habit["frequency_per_week"] == 6


def test_weekly_progress_counts_today_completion(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Бег", frequency_per_week=3)
    habit_id = get_habits(uid)[0]["id"]
    assert get_weekly_progress(habit_id, uid) == 0
    complete_habit(habit_id)
    assert get_weekly_progress(habit_id, uid) == 1


def test_flexible_habit_excluded_from_incomplete_once_quota_met(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Бег", frequency_per_week=1)
    habit_id = get_habits(uid)[0]["id"]

    # До выполнения — в списке невыполненных.
    assert any(h["id"] == habit_id for h in get_incomplete_habits(uid))

    complete_habit(habit_id)
    reset_habits()  # имитируем новый день — completed=0, но неделя не сброшена

    # Норма (1 раз в неделю) уже выполнена (через habit_logs), поэтому
    # привычка больше не считается "невыполненной сегодня".
    conn = connect()
    conn.execute(
        "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
        "VALUES (?,?,?,date('now'),1,0)",
        (uid, habit_id, "Бег"),
    )
    conn.commit()
    conn.close()

    assert not any(h["id"] == habit_id for h in get_incomplete_habits(uid))
    assert not any(h["id"] == habit_id for h in get_habits_needing_reminder(uid, hours=0))


def test_daily_habit_without_frequency_unaffected(uid):
    """Привычки без frequency_per_week (обычные, ежедневные) продолжают
    как раньше считаться невыполненными, пока не отмечены сегодня."""
    add_user(uid, "u", "Test")
    add_habit(uid, "Обычная")
    habit_id = get_habits(uid)[0]["id"]
    assert any(h["id"] == habit_id for h in get_incomplete_habits(uid))


# =====================================
# ЗАМЕТКА/ФОТО (roadmap #3)
# =====================================

def test_add_habit_note_text_only(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    ok = add_habit_note(uid, habit_id, note="Было спокойно")
    assert ok is True
    row = get_recent_habit_notes(uid)[0]
    assert row["note"] == "Было спокойно"
    assert row["photo_data_url"] is None


def test_add_habit_note_rejects_non_image_data_url(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    ok = add_habit_note(uid, habit_id, photo_data_url="data:text/plain;base64,aGVsbG8=")
    assert ok is False


def test_add_habit_note_rejects_oversized_photo(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    huge = "data:image/png;base64," + ("A" * 200_000)
    ok = add_habit_note(uid, habit_id, note="ok", photo_data_url=huge)
    row = get_recent_habit_notes(uid)[0]
    assert row["note"] == "ok"
    assert row["photo_data_url"] is None


def test_add_habit_note_truncates_long_text(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    add_habit_note(uid, habit_id, note="x" * 1000)
    row = get_recent_habit_notes(uid)[0]
    assert len(row["note"]) == 300


def test_add_habit_note_same_day_overwrites(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    add_habit_note(uid, habit_id, note="первая")
    add_habit_note(uid, habit_id, note="вторая")
    notes = get_recent_habit_notes(uid)
    assert len(notes) == 1
    assert notes[0]["note"] == "вторая"


def test_add_habit_note_empty_returns_false(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    assert add_habit_note(uid, habit_id) is False


# =====================================
# ЦЕПОЧКИ ПРИВЫЧЕК (roadmap #7)
# =====================================

def test_complete_habit_suggests_chained_habit(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка")
    trigger_id = get_habits(uid)[0]["id"]
    add_habit(uid, "Растяжка", chain_trigger_habit_id=trigger_id)

    result = complete_habit(trigger_id)

    assert result["chain_suggestion"] is not None
    assert result["chain_suggestion"]["title"] == "Растяжка"


def test_complete_habit_no_suggestion_when_chained_already_done(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка")
    trigger_id = get_habits(uid)[0]["id"]
    add_habit(uid, "Растяжка", chain_trigger_habit_id=trigger_id)
    chained_id = [h for h in get_habits(uid) if h["title"] == "Растяжка"][0]["id"]
    complete_habit(chained_id)

    result = complete_habit(trigger_id)

    assert result["chain_suggestion"] is None


def test_chain_trigger_from_other_user_is_ignored(uid):
    add_user(uid, "u", "Test")
    other_uid = uid + 1
    add_user(other_uid, "u2", "Test2")
    add_habit(other_uid, "Чужая привычка")
    foreign_id = get_habits(other_uid)[0]["id"]

    add_habit(uid, "Своя", chain_trigger_habit_id=foreign_id)
    habit = get_habits(uid)[0]
    assert habit["chain_trigger_habit_id"] is None
