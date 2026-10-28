"""
Защита серии в дополнительном окне после триала (ТЗ «Триал, подписка и приглашения», 28.10.2026, §7–§8).

После трёх бесплатных дней основные действия закрываются: человек не может отметить привычку, и за день окна серия «погасла» бы сама. Поэтому:
  • оплата В ОКНЕ (до `access_state.grace_ends_at`) сохраняет заработанные дни: день окна записывается статусом «freeze» с пометкой 🛡 — серия не обрывается и
    заморозка при этом НЕ тратится; платёж сам по себе привычку не засчитывает и нового выполненного дня не добавляет;
  • окно закончилось без оплаты — специальная защита снимается, дальше работают обычные правила (заморозка, потом бесплатное восстановление/помощь друга); история не удаляется;
  • если подтверждение оплаты пришло ПОЗЖЕ полуночи (день окна уже закрылся как пропущенный), серия возвращается задним числом по времени покупки (`apply_on_payment`),
    а запись «последний срыв» очищается — тот же день нельзя восстановить второй раз через друга или бесплатное восстановление.

Порядок защит (по умолчанию, ждёт согласования с владельцем): 1) защита после триала (если оплатил в окне) → 2) обычная заморозка → 3) бесплатное восстановление / помощь друга.
Один пропущенный день получает ровно одну запись в streak_days, поэтому двойного списания заморозки и двойного восстановления быть не может.
"""
from datetime import datetime, timedelta, timezone

from .access_state import GRACE_HOURS, UTC, _parse, _zone, trial_bounds
from .core import connect
from .users import get_user

PROTECTED_MESSAGE = "🛡 День сохранён защитой после пробного периода."


def grace_window(user):
    """(начало, конец) дополнительного окна в UTC без tzinfo или None, если у аккаунта нет бесплатного периода, после которого оно бывает."""
    from .access import trial_scenario_for_user
    if user is None or not trial_scenario_for_user(user):
        return None
    bounds = trial_bounds(user)
    if bounds is None:
        return None
    ends = [bounds["ends"]]
    access_until = _parse(user["access_until"]) if "access_until" in user.keys() else None
    if access_until:
        ends.append(access_until)
    start = max(ends)
    return start, start + timedelta(hours=GRACE_HOURS)


def protected_days(user):
    """Местные даты (ГГГГ-ММ-ДД), чьё начало (местная полночь) приходится на окно: у обычного триала это ровно один день — «четвёртый»."""
    window = grace_window(user)
    if window is None:
        return []
    bounds = trial_bounds(user)
    zone = _zone(bounds["tz"])
    start, end = window
    day = start.replace(tzinfo=UTC).astimezone(zone).date()
    if datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC).replace(tzinfo=None) < start:
        day += timedelta(days=1)
    days = []
    while datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC).replace(tzinfo=None) < end:
        days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def paid_in_window(user_id, window_end):
    """Была ли за подписку оплата (не возвращённая), сделанная не позже конца окна — по времени покупки, а не по времени обработки."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT COALESCE(purchased_at, created_at) AS at FROM stars_payments "
            "WHERE user_id=? AND payload LIKE 'subscription:%' AND refunded_at IS NULL AND COALESCE(status,'done') != 'failed'",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        moment = _parse(row["at"])
        if moment and moment <= window_end:
            return True
    return False


def covers(user_id, day_key_str):
    """True, если этот местный день защищён: он в окне после триала, а человек оплатил в срок."""
    user = get_user(user_id)
    window = grace_window(user)
    if window is None or day_key_str not in protected_days(user):
        return False
    return paid_in_window(user_id, window[1])


def apply_on_payment(user_id, purchased_at=None):
    """Оплата подтверждена: если она сделана в окне, а день окна уже закрылся как пропущенный, возвращаем серию задним числом.
    Возвращает {"applied": bool, "restored": int|None, "reason": ...}."""
    user = get_user(user_id)
    window = grace_window(user)
    if window is None:
        return {"applied": False, "reason": "no_window"}
    moment = purchased_at or datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(moment, "tzinfo", None) is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    if not (window[0] <= moment <= window[1]):
        return {"applied": False, "reason": "outside_window"}
    days = protected_days(user)
    if not days:
        return {"applied": False, "reason": "no_days"}
    conn = connect()
    try:
        meta = conn.execute("SELECT last_broken_streak, last_broken_date FROM streak_meta WHERE user_id=?", (user_id,)).fetchone()
        current = conn.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if meta is None or current is None:
            return {"applied": False, "reason": "no_streak"}
        broken_day = meta["last_broken_date"]
        restored = int(meta["last_broken_streak"] or 0)
        if broken_day not in days or restored <= 0:
            return {"applied": False, "reason": "nothing_to_restore"}
        if int(current["streak"] or 0) > 0:
            return {"applied": False, "reason": "streak_restarted"}      # человек уже начал новую серию: не склеиваем две
        conn.execute(
            "INSERT OR REPLACE INTO streak_days(user_id,day,status,streak_after,ai_message) VALUES(?,?,?,?,?)",
            (user_id, broken_day, "freeze", restored, PROTECTED_MESSAGE),
        )
        conn.execute("UPDATE users SET streak=? WHERE telegram_id=?", (restored, user_id))
        conn.execute("UPDATE streak_meta SET last_broken_streak=0, last_broken_date=NULL WHERE user_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    try:
        from .tracking import track_server
        track_server(user_id, "trial_streak_protected", meta={"streak": restored, "day": broken_day})
    except Exception:
        pass
    return {"applied": True, "restored": restored, "day": broken_day}
