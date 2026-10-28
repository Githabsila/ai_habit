"""
ТЗ 28.10.2026 §25: один пропуск — одно восстановление. Помощь друга (db/rescue.py), защита дня после триала (db/trial_protection.py) и собственный бесплатный возврат
(db/streak.py::restore_streak_free) работают с одним снимком срыва; кто первым вернул серию, тот его и очистил — остальные уже не найдут, что восстанавливать.
"""
import itertools
from datetime import datetime, timedelta

import pytest

from db import add_user, get_user, rescue, streak
from db import trial_protection as protection
from db.core import connect
from db.follows import make_friends

_counter = itertools.count(866_100_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _today():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("UTC")).date()


def _person(uid_, name):
    add_user(uid_, f"r{uid_}", name)
    _sql("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (uid_,))
    return uid_


def _broken(owner, lost=6):
    day = (_today() - timedelta(days=1)).isoformat()
    _sql("UPDATE users SET streak=0 WHERE telegram_id=?", (owner,))
    _sql("UPDATE streak_meta SET last_broken_streak=?, last_broken_date=?, free_restore_month=NULL WHERE user_id=?", (lost, day, owner))
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after, ai_message) VALUES (?, ?, 'missed', 0, 'x')", (owner, day))
    return day


def test_after_a_friend_saved_the_streak_the_same_gap_cannot_be_restored_again(uid):
    helper, owner = _person(uid, "Спасатель"), _person(uid + 1, "Мария")
    make_friends(helper, owner)
    _broken(owner)
    first = rescue.rescue_streak(helper, owner)
    assert first["ok"] and get_user(owner)["streak"] == 6
    second_helper = _person(uid + 2, "Второй")
    make_friends(second_helper, owner)
    assert rescue.rescue_streak(second_helper, owner) == {"error": "not_available"}, "второй друг тот же пропуск уже не спасёт"
    assert streak.get_free_restore_status(owner)["available"] is False, "свой бесплатный возврат на этот пропуск тоже не тратится дважды"
    assert protection.apply_on_payment(owner, datetime.utcnow()) == {"applied": False, "reason": "no_window"}
    assert get_user(owner)["streak"] == 6
    meta = _sql("SELECT last_broken_streak, last_broken_date FROM streak_meta WHERE user_id=?", (owner,))
    assert meta["last_broken_streak"] == 0 and meta["last_broken_date"] is None


def test_the_own_free_restore_clears_the_gap_for_friends_too(uid):
    helper, owner = _person(uid, "Спасатель"), _person(uid + 1, "Мария")
    make_friends(helper, owner)
    _broken(owner)
    own = streak.restore_streak_free(owner)
    assert own.get("ok") or own.get("restored") or get_user(owner)["streak"] > 0
    assert rescue.rescue_streak(helper, owner) == {"error": "not_available"}, "серию уже вернули — друзьям спасать нечего"


def test_a_gap_of_the_window_is_covered_by_the_protection_not_by_a_second_restore(uid):
    """Для дня окна после триала действует особая защита; после подтверждённой оплаты тот же пропуск не восстанавливается ни другом, ни ещё раз."""
    owner, helper = _person(uid, "Мария"), _person(uid + 1, "Спасатель")
    make_friends(helper, owner)
    _broken(owner)
    _sql("UPDATE streak_meta SET last_broken_streak=0, last_broken_date=NULL WHERE user_id=?", (owner,))
    assert rescue.rescue_streak(helper, owner) == {"error": "not_available"}, "серия не погасла (день защищён) — спасать нечего"
