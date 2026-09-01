"""
Roadmap #43 (сегментированная рассылка), #44 (карточка пользователя для
поддержки), #45 (авто-детект риска оттока).
"""
from datetime import date, timedelta

from db import (
    add_user, get_user, add_habit, complete_habit, get_habits,
    get_users_by_segment, get_user_support_card, get_churn_risk_report,
)
from db.core import connect
from db.streak import register_completion, rollover_user


# =====================================
# СЕГМЕНТИРОВАННАЯ РАССЫЛКА (roadmap #43)
# =====================================

def test_segment_all_returns_everyone(uid):
    add_user(uid, "u", "Test")
    ids = get_users_by_segment("all")
    assert uid in ids


def test_segment_none_falls_back_to_all(uid):
    add_user(uid, "u", "Test")
    ids = get_users_by_segment(None)
    assert uid in ids


def test_segment_premium_filters_correctly(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET premium=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert uid in get_users_by_segment("premium")
    assert uid not in get_users_by_segment("no_premium")


def test_segment_new_7d_includes_freshly_created_user(uid):
    add_user(uid, "u", "Test")
    assert uid in get_users_by_segment("new_7d")


def test_segment_unknown_returns_none(uid):
    add_user(uid, "u", "Test")
    assert get_users_by_segment("not_a_real_segment") is None


def test_segment_banned_excluded_from_premium_and_new(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET premium=1, banned=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert uid not in get_users_by_segment("premium")


# =====================================
# КАРТОЧКА ПОЛЬЗОВАТЕЛЯ ДЛЯ ПОДДЕРЖКИ (roadmap #44)
# =====================================

def test_support_card_none_for_unknown_user():
    assert get_user_support_card(999999999999) is None


def test_support_card_includes_key_fields(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    complete_habit(get_habits(uid)[0]["id"])

    card = get_user_support_card(uid)
    assert card["telegram_id"] == uid
    assert card["streak"] >= 0
    assert len(card["habits"]) == 1
    assert card["habits"][0]["completed"] is True
    assert "subscription" in card
    assert card["purchases_count"] == 0


def test_support_card_includes_recent_logs(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute(
        "INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) "
        "VALUES (?,1,'Бег',date('now'),1,0)",
        (uid,),
    )
    conn.commit()
    conn.close()
    card = get_user_support_card(uid)
    assert len(card["recent_logs"]) == 1
    assert card["recent_logs"][0]["title"] == "Бег"


# =====================================
# РИСК ОТТОКА (roadmap #45)
# =====================================

def test_churn_risk_report_has_all_tiers(uid):
    add_user(uid, "u", "Test")
    report = get_churn_risk_report()
    assert set(report["tiers"].keys()) == {"healthy", "watch", "at_risk", "churned", "lost"}


def test_churn_risk_flags_inactive_user(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Бег")
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)  # создаёт streak_days запись на сегодня

    # Симулируем, что последнее выполнение было 10 дней назад.
    old_day = str(date.today() - timedelta(days=10))
    conn = connect()
    conn.execute("UPDATE streak_days SET day=? WHERE user_id=?", (old_day, uid))
    conn.commit()
    conn.close()

    report = get_churn_risk_report()
    at_risk_ids = [r["telegram_id"] for r in report["at_risk"]]
    assert uid in at_risk_ids
    entry = next(r for r in report["at_risk"] if r["telegram_id"] == uid)
    assert entry["tier"] == "churned"  # 10 дней = 8-30
