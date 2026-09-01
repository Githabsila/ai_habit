"""
Roadmap #12 (ежедневные микро-квесты), #13 (лиги/тиры в рейтинге),
#32 (разовый бустер x2 Adam Coin за Stars).
"""
from datetime import datetime, timedelta, timezone

from db import (
    add_user, add_habit, get_habits, complete_habit,
    get_daily_quests, claim_daily_quest,
    get_league_tier, get_league_progress,
    activate_xp_booster, is_xp_booster_active, get_user,
)
from db.core import connect


# =====================================
# МИКРО-КВЕСТЫ (roadmap #12)
# =====================================

def test_get_daily_quests_returns_three_quests(uid):
    add_user(uid, "u", "Test")
    quests = get_daily_quests(uid)
    assert len(quests) == 3
    assert all("key" in q and "title" in q for q in quests)


def test_daily_quests_stable_across_calls_same_day(uid):
    add_user(uid, "u", "Test")
    first = [q["key"] for q in get_daily_quests(uid)]
    second = [q["key"] for q in get_daily_quests(uid)]
    assert first == second


def test_two_habits_quest_progresses_with_completions(uid, monkeypatch):
    import db.quests as quests_module
    monkeypatch.setattr(quests_module, "_pick_quests_for_day", lambda user_id, day: ["two_habits", "all_done", "priority_habit"])

    add_user(uid, "u", "Test")
    add_habit(uid, "A")
    add_habit(uid, "B")
    habit_ids = [h["id"] for h in get_habits(uid)]

    quests = get_daily_quests(uid)
    two_habits = next(q for q in quests if q["key"] == "two_habits")
    assert two_habits["progress"] == 0

    complete_habit(habit_ids[0])
    quests = get_daily_quests(uid)
    two_habits = next(q for q in quests if q["key"] == "two_habits")
    assert two_habits["progress"] == 1
    assert two_habits["completed"] is False

    complete_habit(habit_ids[1])
    quests = get_daily_quests(uid)
    two_habits = next(q for q in quests if q["key"] == "two_habits")
    assert two_habits["progress"] == 2
    assert two_habits["completed"] is True


def test_claim_daily_quest_awards_coins_once(uid, monkeypatch):
    # Фиксируем набор квестов дня явно — не все 6 возможных квестов
    # satisfiable двумя обычными привычками (например "before_noon"
    # зависит от времени суток), иначе тест был бы скрыто нестабильным
    # в зависимости от того, какие 3 из 6 выпали этому uid сегодня.
    import db.quests as quests_module
    monkeypatch.setattr(quests_module, "_pick_quests_for_day", lambda user_id, day: ["two_habits", "all_done", "priority_habit"])

    add_user(uid, "u", "Test")
    add_habit(uid, "A")
    add_habit(uid, "B")
    for h in get_habits(uid):
        complete_habit(h["id"])

    quests = get_daily_quests(uid)
    completed_quest = next((q for q in quests if q["completed"] and not q["claimed"]), None)
    assert completed_quest is not None

    before = get_user(uid)["xp"]
    reward = claim_daily_quest(uid, completed_quest["key"])
    assert reward == completed_quest["reward"]
    assert get_user(uid)["xp"] == before + reward

    # Повторный claim того же квеста в тот же день — ничего не даёт.
    again = claim_daily_quest(uid, completed_quest["key"])
    assert again is None


def test_claim_unfinished_quest_returns_none(uid):
    add_user(uid, "u", "Test")
    quests = get_daily_quests(uid)
    unfinished = next((q for q in quests if not q["completed"]), None)
    if unfinished is None:
        return
    assert claim_daily_quest(uid, unfinished["key"]) is None


def test_claim_unknown_quest_key_returns_none(uid):
    add_user(uid, "u", "Test")
    assert claim_daily_quest(uid, "not_a_real_quest") is None


# =====================================
# ЛИГИ (roadmap #13)
# =====================================

def test_league_tier_bronze_by_default():
    assert get_league_tier(0) == "🥉 Бронза"


def test_league_tier_progression():
    assert get_league_tier(500) == "🥈 Серебро"
    assert get_league_tier(2000) == "🥇 Золото"
    assert get_league_tier(5000) == "💎 Платина"
    assert get_league_tier(15000) == "👑 Легенда"
    assert get_league_tier(499) == "🥉 Бронза"


def test_league_progress_reports_xp_needed():
    progress = get_league_progress(100)
    assert progress["next_tier"] == "🥈 Серебро"
    assert progress["xp_needed"] == 400


def test_league_progress_none_at_max_tier():
    assert get_league_progress(999999) is None


# =====================================
# БУСТЕР x2 (roadmap #32)
# =====================================

def test_booster_inactive_by_default(uid):
    add_user(uid, "u", "Test")
    assert is_xp_booster_active(uid) is False


def test_activate_booster_makes_it_active(uid):
    add_user(uid, "u", "Test")
    activate_xp_booster(uid, 24)
    assert is_xp_booster_active(uid) is True


def test_repurchase_booster_extends_not_resets(uid):
    add_user(uid, "u", "Test")
    first_until = activate_xp_booster(uid, 24)
    second_until = activate_xp_booster(uid, 24)
    # Второе окно должно НАЧИНАТЬСЯ с конца первого, а не с "сейчас".
    assert second_until >= first_until + timedelta(hours=23, minutes=59)


def test_expired_booster_is_not_active(uid):
    add_user(uid, "u", "Test")
    past = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)).isoformat()
    conn = connect()
    conn.execute("UPDATE users SET bonus_2x_xp_until=? WHERE telegram_id=?", (past, uid))
    conn.commit()
    conn.close()
    assert is_xp_booster_active(uid) is False


def test_complete_habit_doubles_coins_with_active_booster(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "A")
    habit_id = get_habits(uid)[0]["id"]
    activate_xp_booster(uid, 24)
    result = complete_habit(habit_id)
    assert result["xp_boosted"] is True
    assert result["coins"] == 20  # BASE_HABIT_COINS(10) * 2 бустер


def test_complete_habit_no_boost_without_booster(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "A")
    habit_id = get_habits(uid)[0]["id"]
    result = complete_habit(habit_id)
    assert result["xp_boosted"] is False
    assert result["coins"] == 10
