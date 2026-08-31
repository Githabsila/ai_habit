"""
Недельные челленджи с другом — следующий шаг поверх уже существующей
рефералки (users.referrer_id / get_referrals): не просто "пригласи
друга ради бонуса", а совместный челлендж на 7 дней, у кого серия не
прервётся. Прогресс каждого участника считается на лету из calendar
(день "активен" == completed > 0 в этот день) — отдельного состояния
для челленджа почти не храним, только сам факт и его границы по датам.
"""
from datetime import date, timedelta

from .core import connect

DEFAULT_CHALLENGE_DAYS = 7


def create_challenge(user_id, partner_id, days=DEFAULT_CHALLENGE_DAYS):
    """Возвращает (ok, error). error — 'self_challenge' (нельзя с самим
    собой) либо 'already_active' (у этой пары уже есть незавершённый
    челлендж)."""
    if user_id == partner_id:
        return False, "self_challenge"

    existing = get_active_challenge_between(user_id, partner_id)
    if existing:
        return False, "already_active"

    start = date.today()
    end = start + timedelta(days=days - 1)
    conn = connect()
    conn.execute(
        "INSERT INTO challenges(user_id, partner_id, start_day, end_day) VALUES (?, ?, ?, ?)",
        (user_id, partner_id, start.isoformat(), end.isoformat()),
    )
    conn.commit()
    conn.close()
    return True, None


def get_active_challenge_between(user_id, partner_id):
    conn = connect()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("""
        SELECT * FROM challenges
        WHERE ((user_id=? AND partner_id=?) OR (user_id=? AND partner_id=?))
          AND end_day >= ?
        ORDER BY id DESC LIMIT 1
    """, (user_id, partner_id, partner_id, user_id, today))
    row = cursor.fetchone()
    conn.close()
    return row


def get_active_challenge_for_user(user_id):
    """Активный (ещё не закончившийся) челлендж пользователя — с любым
    партнёром, он либо есть один, либо нет вообще (простая MVP-модель,
    без параллельных челленджей)."""
    conn = connect()
    cursor = conn.cursor()
    today = date.today().isoformat()
    cursor.execute("""
        SELECT * FROM challenges
        WHERE (user_id=? OR partner_id=?) AND end_day >= ?
        ORDER BY id DESC LIMIT 1
    """, (user_id, user_id, today))
    row = cursor.fetchone()
    conn.close()
    return row


def _active_days_in_range(user_id, start_day, end_day):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) AS n FROM calendar
        WHERE user_id=? AND day >= ? AND day <= ? AND completed > 0
    """, (user_id, start_day, end_day))
    n = int(cursor.fetchone()["n"] or 0)
    conn.close()
    return n


def get_challenge_progress(challenge):
    """(my_days, their_days, total_days, days_elapsed) для карточки
    сравнения — «ты: 4/7, друг: 2/7»."""
    start = date.fromisoformat(challenge["start_day"])
    end = date.fromisoformat(challenge["end_day"])
    today = date.today()
    total_days = (end - start).days + 1
    days_elapsed = min((min(today, end) - start).days + 1, total_days)
    days_elapsed = max(days_elapsed, 0)

    user_days = _active_days_in_range(challenge["user_id"], challenge["start_day"], challenge["end_day"])
    partner_days = _active_days_in_range(challenge["partner_id"], challenge["start_day"], challenge["end_day"])
    return {
        "user_id": challenge["user_id"],
        "partner_id": challenge["partner_id"],
        "user_days": user_days,
        "partner_days": partner_days,
        "total_days": total_days,
        "days_elapsed": days_elapsed,
        "start_day": challenge["start_day"],
        "end_day": challenge["end_day"],
    }
