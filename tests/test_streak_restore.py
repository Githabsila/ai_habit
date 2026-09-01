"""
Улучшение #50: бесплатное восстановление сорванной серии — не чаще раза в
календарный месяц, и только пока снимок срыва свежий (см.
FREE_RESTORE_GRACE_DAYS в db/streak.py).
"""
from datetime import date, timedelta

from db import add_user, get_free_restore_status, restore_streak_free
from db.core import connect
from db.streak import rollover_user, ensure_tables, day_key

from tests.conftest import sign_init_data  # noqa: F401


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def _seed_broken_streak(uid_, lost_streak=5, broken_day=None):
    """Пишет снимок срыва напрямую в streak_meta — минуя реальный rollover
    (тот покрыт отдельным интеграционным тестом ниже), чтобы не завязываться
    на часовые пояса/точные даты в остальных тестах этого файла."""
    add_user(uid_, "u", "Test")
    ensure_tables()
    broken_day = broken_day or day_key(date.today())
    conn = connect()
    # streak_meta пока не имеет строки для свежего пользователя — без неё
    # UPDATE ниже молча затронул бы 0 строк.
    conn.execute("INSERT OR IGNORE INTO streak_meta(user_id) VALUES(?)", (uid_,))
    conn.execute(
        "UPDATE streak_meta SET last_broken_streak=?, last_broken_date=? WHERE user_id=?",
        (lost_streak, broken_day, uid_),
    )
    conn.commit()
    conn.close()


# =====================================
# get_free_restore_status / restore_streak_free
# =====================================

def test_restore_available_right_after_break(uid):
    _seed_broken_streak(uid, lost_streak=7)
    status = get_free_restore_status(uid)
    assert status == {"available": True, "lost_streak": 7}


def test_restore_unavailable_when_no_break_recorded(uid):
    add_user(uid, "u", "Test")
    ensure_tables()
    status = get_free_restore_status(uid)
    assert status == {"available": False, "lost_streak": 0}


def test_restore_unavailable_after_grace_period_expires(uid):
    stale_day = day_key(date.today() - timedelta(days=10))
    _seed_broken_streak(uid, lost_streak=7, broken_day=stale_day)
    status = get_free_restore_status(uid)
    assert status["available"] is False


def test_restore_actually_restores_streak_and_users_row(uid):
    _seed_broken_streak(uid, lost_streak=7)
    result = restore_streak_free(uid)
    assert result == {"ok": True, "streak": 7}

    conn = connect()
    row = conn.execute("SELECT streak FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()
    assert row["streak"] == 7


def test_restore_not_available_twice_in_same_month(uid):
    _seed_broken_streak(uid, lost_streak=7)
    first = restore_streak_free(uid)
    assert first["ok"] is True

    # Второй срыв в этом же месяце — снимок есть, но месячный лимит уже
    # использован.
    _seed_broken_streak(uid, lost_streak=3)
    second = restore_streak_free(uid)
    assert second == {"ok": False, "error": "not_available"}


def test_restore_fails_gracefully_with_no_snapshot(uid):
    add_user(uid, "u", "Test")
    ensure_tables()
    result = restore_streak_free(uid)
    assert result == {"ok": False, "error": "not_available"}


# =====================================
# rollover_user — снимок срыва пишется по-настоящему
# =====================================

def test_rollover_break_writes_snapshot_for_restore(uid):
    add_user(uid, "u", "Test")
    ensure_tables()
    today = date.today()
    yesterday = day_key(today - timedelta(days=1))
    stale_rollover_day = day_key(today - timedelta(days=2))

    conn = connect()
    conn.execute("UPDATE users SET streak=5 WHERE telegram_id=?", (uid,))
    conn.execute(
        "INSERT OR REPLACE INTO streak_meta(user_id, rollover_day, freeze_balance) VALUES (?,?,0)",
        (uid, stale_rollover_day),
    )
    conn.commit()
    conn.close()
    # Ни одной записи streak_days на "вчера" -> rollover_user() сочтёт день
    # пропущенным (нет заморозки в запасе) и обнулит серию.

    changed = rollover_user(uid)
    assert changed is True

    status = get_free_restore_status(uid)
    assert status == {"available": True, "lost_streak": 5}

    conn = connect()
    row = conn.execute("SELECT streak FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()
    assert row["streak"] == 0


# =====================================
# POST /api/streak/restore-free
# =====================================

async def test_restore_free_route_success(client, uid):
    _seed_broken_streak(uid, lost_streak=4)
    headers = await _headers(uid)
    r = await client.post("/api/streak/restore-free", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body == {"ok": True, "streak": 4}


async def test_restore_free_route_400_when_not_available(client, uid):
    add_user(uid, "u", "Test")
    ensure_tables()
    headers = await _headers(uid)
    r = await client.post("/api/streak/restore-free", headers=headers)
    assert r.status == 400
    body = await r.json()
    assert body["error"] == "not_available"


async def test_streak_status_route_includes_free_restore(client, uid):
    _seed_broken_streak(uid, lost_streak=6)
    headers = await _headers(uid)
    r = await client.get("/api/streak/status", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body["free_restore"] == {"available": True, "lost_streak": 6}
