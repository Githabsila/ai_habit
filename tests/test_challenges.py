"""
Недельные челленджи с другом (db/challenges.py) — следующий шаг поверх
рефералки: совместный 7-дневный челлендж, прогресс считается на лету
из calendar (день "активен" == completed > 0), без отдельного хранения.
"""
from datetime import date

from db import add_user, create_challenge, get_active_challenge_for_user, get_challenge_progress
from db.core import connect


def _mark_active_day(user_id, day, completed=1):
    conn = connect()
    conn.execute(
        "INSERT INTO calendar(user_id, day, completed, total) VALUES (?, ?, ?, ?)",
        (user_id, day, completed, completed),
    )
    conn.commit()
    conn.close()


def test_create_challenge_rejects_self(uid):
    add_user(uid, "u", "Test")
    ok, error = create_challenge(uid, uid)
    assert ok is False
    assert error == "self_challenge"


def test_create_challenge_succeeds_between_two_users(uid):
    partner = uid * 10  # вне диапазона общего sequential-счётчика uid
    add_user(uid, "u1", "Test")
    add_user(partner, "u2", "Test")

    ok, error = create_challenge(uid, partner)

    assert ok is True
    assert error is None
    active = get_active_challenge_for_user(uid)
    assert active is not None
    assert active["user_id"] == uid
    assert active["partner_id"] == partner


def test_create_challenge_rejects_duplicate_while_active(uid):
    partner = uid * 10  # вне диапазона общего sequential-счётчика uid
    add_user(uid, "u1", "Test")
    add_user(partner, "u2", "Test")
    create_challenge(uid, partner)

    ok, error = create_challenge(uid, partner)

    assert ok is False
    assert error == "already_active"

    # И с обратной стороны (партнёр -> пользователь) — та же пара.
    ok2, error2 = create_challenge(partner, uid)
    assert ok2 is False
    assert error2 == "already_active"


def test_challenge_progress_counts_active_days_from_calendar(uid):
    partner = uid * 10  # вне диапазона общего sequential-счётчика uid
    add_user(uid, "u1", "Test")
    add_user(partner, "u2", "Test")
    create_challenge(uid, partner)
    today = str(date.today())
    _mark_active_day(uid, today, completed=2)

    active = get_active_challenge_for_user(uid)
    progress = get_challenge_progress(active)

    assert progress["user_days"] == 1
    assert progress["partner_days"] == 0
    assert progress["total_days"] == 7
    assert progress["days_elapsed"] == 1
