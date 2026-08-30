"""
Триал (3 дня) → платная подписка (1.99$/5.99$ через Stars) → доступ в
закрытый канал по 2 дням ударного режима подряд (пром 13).

ВАЖНО: это НЕ то же самое, что "Premium" (db/users.py give_premium) —
Premium остаётся отдельным косметическим тарифом (еженедельный AI-разбор,
метка в рейтинге). Подписка отсюда — доступ к самому боту.

Блокировка доступа управляется config.SUBSCRIPTION_GATE_ENABLED (по
умолчанию выключена) + SUBSCRIPTION_GATE_CUTOVER, чтобы НИКОГДА не
заблокировать задним числом уже существующих пользователей — см.
gate_applies_to().
"""
from datetime import date, datetime, timedelta

from .core import connect
from .users import get_user


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def get_trial_day(user_id):
    """1 в день регистрации, 2 на следующий день, и т.д. Считаем от
    users.created_at (UTC) — отдельного поля для старта триала не нужно."""
    user = get_user(user_id)
    if not user:
        return None
    created = _parse_dt(user["created_at"] if "created_at" in user.keys() else None)
    if not created:
        return 1
    return max(1, (datetime.utcnow().date() - created.date()).days + 1)


def is_in_trial(user_id):
    from config import SUBSCRIPTION_TRIAL_DAYS
    day = get_trial_day(user_id)
    return day is not None and day <= SUBSCRIPTION_TRIAL_DAYS


def has_active_subscription(user_id):
    user = get_user(user_id)
    if not user or "subscription_paid_until" not in user.keys():
        return False
    until = _parse_dt(user["subscription_paid_until"])
    return bool(until and until > datetime.utcnow())


def has_ever_paid(user_id):
    user = get_user(user_id)
    if not user or "subscription_first_payment_at" not in user.keys():
        return False
    return bool(user["subscription_first_payment_at"])


def get_subscription_price_stars(user_id):
    """Первая оплата — по вводной цене (1.99$), продление — полная (5.99$)."""
    from config import SUBSCRIPTION_INTRO_PRICE_STARS, SUBSCRIPTION_RENEWAL_PRICE_STARS
    return SUBSCRIPTION_RENEWAL_PRICE_STARS if has_ever_paid(user_id) else SUBSCRIPTION_INTRO_PRICE_STARS


def record_subscription_payment(user_id, months=1):
    """Продлевает подписку на `months` месяцев от текущего срока (если он
    ещё не истёк) или от сегодня (если истёк/не было). Возвращает новую
    дату окончания."""
    user = get_user(user_id)
    now = datetime.utcnow()
    current_until = _parse_dt(user["subscription_paid_until"]) if user else None
    base = current_until if (current_until and current_until > now) else now
    new_until = base + timedelta(days=30 * months)

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT subscription_first_payment_at FROM users WHERE telegram_id=?",
        (user_id,),
    )
    row = cursor.fetchone()
    first_payment_at = row["subscription_first_payment_at"] if row else None
    cursor.execute(
        """UPDATE users
           SET subscription_paid_until=?,
               subscription_first_payment_at=COALESCE(subscription_first_payment_at, CURRENT_TIMESTAMP)
           WHERE telegram_id=?""",
        (new_until.isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    return new_until


def gate_applies_to(user_id):
    """Гейт применяется ТОЛЬКО к пользователям, зарегистрированным после
    SUBSCRIPTION_GATE_CUTOVER — уже существующие пользователи бота никогда
    не блокируются этой функцией задним числом, даже если гейт включён."""
    from config import SUBSCRIPTION_GATE_ENABLED, SUBSCRIPTION_GATE_CUTOVER
    if not SUBSCRIPTION_GATE_ENABLED:
        return False
    user = get_user(user_id)
    if not user:
        return False
    created = _parse_dt(user["created_at"] if "created_at" in user.keys() else None)
    if not created:
        return False
    try:
        cutover = date.fromisoformat(SUBSCRIPTION_GATE_CUTOVER)
    except ValueError:
        return False
    return created.date() >= cutover


def bot_access_allowed(user_id):
    """True, если пользователю можно пользоваться ботом/мини-аппом прямо
    сейчас: гейт не применяется к нему, ИЛИ он ещё в триале, ИЛИ у него
    активна оплаченная подписка."""
    if not gate_applies_to(user_id):
        return True
    if is_in_trial(user_id):
        return True
    return has_active_subscription(user_id)


def get_subscription_status(user_id):
    """Единая сводка для UI/сообщений."""
    from config import SUBSCRIPTION_TRIAL_DAYS, SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL
    user = get_user(user_id)
    streak = int(user["streak"] or 0) if user else 0
    trial_day = get_trial_day(user_id)
    return {
        "trial_day": trial_day,
        "trial_days_total": SUBSCRIPTION_TRIAL_DAYS,
        "in_trial": is_in_trial(user_id),
        "trial_expired": (trial_day or 0) > SUBSCRIPTION_TRIAL_DAYS,
        "has_paid": has_active_subscription(user_id),
        "ever_paid": has_ever_paid(user_id),
        "access_allowed": bot_access_allowed(user_id),
        "streak": streak,
        "streak_needed_for_channel": SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL,
        "channel_eligible": streak >= SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL,
        "channel_granted": bool(user and user["channel_access_granted_at"]) if user and "channel_access_granted_at" in user.keys() else False,
        "price_stars": get_subscription_price_stars(user_id),
    }


def mark_channel_access_granted(user_id):
    conn = connect()
    conn.execute(
        "UPDATE users SET channel_access_granted_at=CURRENT_TIMESTAMP WHERE telegram_id=?",
        (user_id,),
    )
    conn.commit()
    conn.close()


async def try_grant_channel_access(bot, user_id):
    """Если пользователь оплатил подписку И набрал нужный стрик, И ещё не
    получал ссылку — создаёт одноразовую инвайт-ссылку в закрытый канал и
    отправляет её. Ничего не делает (без ошибки), если CLOSED_CHANNEL_ID
    не настроен — канал ещё можно подключить позже без изменений кода."""
    from config import CLOSED_CHANNEL_ID

    status = get_subscription_status(user_id)
    if status["channel_granted"] or not status["channel_eligible"] or not status["has_paid"]:
        return None
    if not CLOSED_CHANNEL_ID:
        return None

    invite = await bot.create_chat_invite_link(
        chat_id=CLOSED_CHANNEL_ID,
        member_limit=1,
        name=f"user_{user_id}",
    )
    mark_channel_access_granted(user_id)
    return invite.invite_link
