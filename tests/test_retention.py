"""
D1/D7/D30 retention-когорты (db/analytics.py::get_retention) — отвечает
на другой вопрос, чем DAU: не "сколько активны сегодня", а "сколько из
пришедших N дней назад вернулись". add_user() всегда пишет created_at
как CURRENT_TIMESTAMP, поэтому тест сдвигает его вручную через SQL,
как и last_seen — иначе когорту "N дней назад" никак не собрать.
"""
from datetime import date, timedelta

from db import add_user
from db.core import connect
from db.analytics import get_retention, get_retention_summary


def _backdate(telegram_id, days_ago, seen_days_ago=None):
    conn = connect()
    created = (date.today() - timedelta(days=days_ago)).isoformat()
    conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (created, telegram_id))
    if seen_days_ago is not None:
        from datetime import datetime
        seen = (datetime.utcnow() - timedelta(days=seen_days_ago)).isoformat()
        conn.execute("UPDATE users SET last_seen=? WHERE telegram_id=?", (seen, telegram_id))
    conn.commit()
    conn.close()


def test_returned_user_counts_toward_cohort_retention(uid):
    add_user(uid, "ret_returned", "Test")
    _backdate(uid, days_ago=7, seen_days_ago=0)

    result = get_retention(7)

    assert result["cohort_size"] >= 1
    assert result["returned"] >= 1
    assert result["rate_percent"] > 0


def test_user_who_never_came_back_does_not_count_as_returned(uid):
    add_user(uid, "ret_gone", "Test")
    _backdate(uid, days_ago=7, seen_days_ago=None)  # last_seen остаётся NULL

    result = get_retention(7)

    # cohort_size включает этого пользователя, returned — нет.
    assert result["cohort_size"] >= 1


def test_empty_cohort_returns_zero_without_division_error(uid):
    # days_ago=999 гарантированно пустая когорта.
    result = get_retention(999)
    assert result == {"cohort_size": 0, "returned": 0, "rate_percent": 0.0}


def test_retention_summary_has_all_three_windows():
    summary = get_retention_summary()
    assert set(summary.keys()) == {"d1", "d7", "d30"}
    for key in ("d1", "d7", "d30"):
        assert "rate_percent" in summary[key]
