"""
Триал ADAM (ТЗ владельца: сообщения от 30.08 п.13 и 12.10): первые 3 дня бесплатно, потом Pro — первый месяц по скидке (153 ⭐ ≈ $1.99),
дальше полная цена (461 ⭐ ≈ $5.99); закрытый канал — только с двумя днями ударного режима подряд.

Здесь — состояние человека в триале (`snapshot`), на которое смотрят и сообщения (trial_messages.py), и окна приложения, и админка:
• день триала считается по ЛОКАЛЬНОМУ календарю человека (первый день — день старта, до его полуночи; раньше шёл по UTC и у вечерних
  регистраций первый день заканчивался через пару часов);
• скидка $1.99 действует до дня «после триала» включительно (день 4), с 5-го дня — только полная цена;
• тумблеры сообщений триала (все сразу и по одному) и служебные действия для проверки с тестовых аккаунтов: «обнулить старт»,
  стереть тестовую оплату.
Триал идёт только у аккаунтов в режиме «trial» (админка) или при общем гейте подписки — см. db/access.py::trial_scenario_for_user.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect
from .users import get_user

UTC = timezone.utc
ALL = "*"                                   # ключ «все сообщения триала сразу» в таблице тумблеров
INTRO_USD, RENEWAL_USD = "$1.99", "$5.99"


def _parse(value):
    """Строка из базы (UTC) → наивный datetime в UTC."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _tz(user_id):
    from .streak import get_timezone
    try:
        return ZoneInfo(get_timezone(user_id))
    except Exception:
        return ZoneInfo("UTC")


def _aware(now_utc):
    now = now_utc or datetime.now(UTC)
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def trial_started_utc(user):
    """Начало триала (UTC без tzinfo): у аккаунта в режиме «trial» — trial_started_at, иначе — регистрация."""
    if user is None:
        return None
    keys = user.keys()
    started = None
    if "access_mode" in keys and user["access_mode"] == "trial" and "trial_started_at" in keys:
        started = _parse(user["trial_started_at"])
    if started is None and "created_at" in keys:
        started = _parse(user["created_at"])
    return started


def trial_day(user_id, now_utc=None):
    """1 — в день старта (по местному времени человека), 2 — на следующий и так далее."""
    user = get_user(user_id)
    if not user:
        return None
    started = trial_started_utc(user)
    if started is None:
        return 1
    tz = _tz(user_id)
    start_day = started.replace(tzinfo=UTC).astimezone(tz).date()
    today = _aware(now_utc).astimezone(tz).date()
    return max(1, (today - start_day).days + 1)


def window_end_utc(user):
    """Когда кончается бесплатный триал: местная полночь после последнего дня (UTC без tzinfo). None — триал не идёт."""
    from config import SUBSCRIPTION_TRIAL_DAYS
    if user is None or "access_mode" not in user.keys() or user["access_mode"] != "trial":
        return None
    started = _parse(user["trial_started_at"] if "trial_started_at" in user.keys() else None)
    if started is None:
        return None
    tz = _tz(user["telegram_id"])
    first_day = started.replace(tzinfo=UTC).astimezone(tz).date()
    end_local = datetime.combine(first_day + timedelta(days=int(SUBSCRIPTION_TRIAL_DAYS)), time.min, tzinfo=tz)
    return end_local.astimezone(UTC).replace(tzinfo=None)


def intro_price_open(user_id, day=None, ever_paid=None):
    """Скидка на первый месяц: не платил раньше и не позже дня после триала (день 4 — «последний день скидки»)."""
    from config import SUBSCRIPTION_TRIAL_DAYS
    from .subscription import has_ever_paid
    if (has_ever_paid(user_id) if ever_paid is None else ever_paid):
        return False
    day = trial_day(user_id) if day is None else day
    return day is not None and day <= int(SUBSCRIPTION_TRIAL_DAYS) + 1


def price_stars(user_id):
    from config import SUBSCRIPTION_INTRO_PRICE_STARS, SUBSCRIPTION_RENEWAL_PRICE_STARS
    return SUBSCRIPTION_INTRO_PRICE_STARS if intro_price_open(user_id) else SUBSCRIPTION_RENEWAL_PRICE_STARS


def _day_marks(user_id, days):
    """{день: статус} из streak_days: 'completed' — в тот день была отметка привычки."""
    conn = connect()
    try:
        marks = ",".join("?" for _ in days)
        rows = conn.execute(f"SELECT day, status, streak_after FROM streak_days WHERE user_id=? AND day IN ({marks})", (user_id, *days)).fetchall()
    finally:
        conn.close()
    return {r["day"]: (r["status"], int(r["streak_after"] or 0)) for r in rows}


def snapshot(user_id, now_utc=None):
    """Всё, что нужно знать о человеке в триале. None — человека нет."""
    from config import (
        CLOSED_CHANNEL_ID, SUBSCRIPTION_INTRO_PRICE_STARS, SUBSCRIPTION_RENEWAL_PRICE_STARS, SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL,
        SUBSCRIPTION_TRIAL_DAYS,
    )
    from .access import trial_scenario_for_user
    from .subscription import has_active_subscription, has_ever_paid
    user = get_user(user_id)
    if user is None:
        return None
    now = _aware(now_utc)
    tz = _tz(user_id)
    local = now.astimezone(tz)
    total = int(SUBSCRIPTION_TRIAL_DAYS)
    day = trial_day(user_id, now)
    paid, ever = has_active_subscription(user_id), has_ever_paid(user_id)
    today_s, yesterday_s = local.date().isoformat(), (local.date() - timedelta(days=1)).isoformat()
    marks = _day_marks(user_id, (today_s, yesterday_s))
    marked_today = marks.get(today_s, ("", 0))[0] == "completed"
    marked_yesterday = marks.get(yesterday_s, ("", 0))[0] == "completed"
    streak = int(user["streak"] or 0)
    if marked_today:
        streak = max(streak, marks[today_s][1])
    needed = int(SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL)
    intro = intro_price_open(user_id, day, ever)
    if paid:
        phase = "paid"
    elif day <= total:
        phase = f"day{day}"
    elif day == total + 1:
        phase = "expired_intro"
    else:
        phase = "expired_full"
    return {
        "user_id": user_id, "name": (user["first_name"] or "").strip(), "scenario": trial_scenario_for_user(user),
        "mode": user["access_mode"] if "access_mode" in user.keys() and user["access_mode"] else "open",
        "day": day, "days_total": total, "phase": phase, "paid": paid, "ever_paid": ever,
        "intro_active": intro, "price_stars": SUBSCRIPTION_INTRO_PRICE_STARS if intro else SUBSCRIPTION_RENEWAL_PRICE_STARS,
        "intro_price_stars": SUBSCRIPTION_INTRO_PRICE_STARS, "renewal_price_stars": SUBSCRIPTION_RENEWAL_PRICE_STARS,
        "intro_usd": INTRO_USD, "renewal_usd": RENEWAL_USD,
        "streak": streak, "needed": needed, "marked_today": marked_today, "marked_yesterday": marked_yesterday,
        "channel_configured": bool(CLOSED_CHANNEL_ID), "channel_granted": bool("channel_access_granted_at" in user.keys() and user["channel_access_granted_at"]),
        "channel_eligible": streak >= needed,
        "local_hour": local.hour, "local_date": today_s, "timezone": str(tz),
    }


def public_trial(user_id):
    """Что показывает сам Mini App (карточка триала, окно «триал закончился»): без служебных полей."""
    snap = snapshot(user_id)
    if snap is None:
        return None
    keys = ("scenario", "day", "days_total", "phase", "paid", "intro_active", "price_stars", "intro_price_stars", "renewal_price_stars",
            "intro_usd", "renewal_usd", "streak", "needed", "marked_today", "marked_yesterday", "channel_configured", "channel_granted",
            "channel_eligible")
    return {key: snap[key] for key in keys}


# ---------------------------------------------------------------------------------------------------------------- тумблеры сообщений

def message_enabled(key):
    """Сообщение включено, если не выключены все сообщения триала разом и оно само. По умолчанию всё включено."""
    conn = connect()
    try:
        rows = conn.execute("SELECT key, enabled FROM trial_message_settings WHERE key IN (?, ?)", (ALL, key)).fetchall()
    finally:
        conn.close()
    states = {r["key"]: bool(r["enabled"]) for r in rows}
    return states.get(ALL, True) and states.get(key, True)


def set_message_enabled(key, enabled):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO trial_message_settings(key, enabled, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP",
            (key, 1 if enabled else 0),
        )
        conn.commit()
    finally:
        conn.close()


def message_states():
    """{ключ: включено} для всех записей таблицы тумблеров (ключа нет — значит включено)."""
    conn = connect()
    try:
        rows = conn.execute("SELECT key, enabled FROM trial_message_settings").fetchall()
    finally:
        conn.close()
    return {r["key"]: bool(r["enabled"]) for r in rows}


# ---------------------------------------------------------------------------------------------------------------- журнал и проверка

def sent_log(user_id, limit=30):
    """Что из сообщений триала человеку уже ушло (из общего журнала уведомлений): [{key, sent_at}] от новых к старым."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT category, sent_at FROM notification_log WHERE user_id=? AND category LIKE 'trial:%' ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [{"key": r["category"].split(":", 1)[1], "sent_at": r["sent_at"]} for r in rows]


def sent_today_count(user_id, local_date):
    """Сколько сообщений триала ушло человеку за этот местный день (для дневного лимита)."""
    conn = connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM streak_notifications WHERE user_id=? AND day=? AND kind LIKE 'trial:%'", (user_id, local_date)).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0)


def clear_trial_messages(user_id):
    """«Обнулить старт» для сообщений: стираем записи об отправленных сообщениях триала — сценарий можно пройти заново."""
    conn = connect()
    try:
        conn.execute("DELETE FROM streak_notifications WHERE user_id=? AND kind LIKE 'trial:%'", (user_id,))
        conn.execute("DELETE FROM notification_log WHERE user_id=? AND category LIKE 'trial:%'", (user_id,))
        conn.commit()
    finally:
        conn.close()


def clear_test_payment(user_id):
    """Стереть оплату, ссылку в канал и «платил раньше» — только для проверки триала с тестового аккаунта (админка)."""
    conn = connect()
    try:
        conn.execute(
            "UPDATE users SET subscription_paid_until=NULL, subscription_first_payment_at=NULL, channel_access_granted_at=NULL WHERE telegram_id=?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()
