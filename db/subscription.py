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
from datetime import date, datetime, timedelta, timezone

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
    """1 в день старта, 2 на следующий день, и т.д. Считаем от users.created_at; у аккаунта в режиме «сценарий триала» (db/access.py) —
    от users.trial_started_at. Дни — по местному календарю человека (db/trial.py::trial_day)."""
    from .trial import trial_day
    return trial_day(user_id)


def is_in_trial(user_id):
    """Идут ли пробные дни (или человек ещё не открывал приложение — триал не расходуется): db/access_state.py."""
    from .access_state import STATE_NOT_STARTED, STATE_TRIAL, access_state
    info = access_state(user_id)
    return bool(info and info["state"] in (STATE_NOT_STARTED, STATE_TRIAL))


def has_active_subscription(user_id):
    user = get_user(user_id)
    if not user or "subscription_paid_until" not in user.keys():
        return False
    until = _parse_dt(user["subscription_paid_until"])
    return bool(until and until > datetime.now(timezone.utc).replace(tzinfo=None))


def has_ever_paid(user_id):
    user = get_user(user_id)
    if not user or "subscription_first_payment_at" not in user.keys():
        return False
    return bool(user["subscription_first_payment_at"])


def get_subscription_price_stars(user_id):
    """Первая оплата — по вводной цене (1.99$), но только до дня после триала включительно (ТЗ владельца: на 5-й день уже 5.99$); продление — полная."""
    from .trial import price_stars
    return price_stars(user_id)


def record_subscription_payment(user_id, months=1, paid_at=None, expires_at=None):
    """Продлевает подписку на `months` месяцев от текущего срока (если он ещё не истёк) или от момента оплаты (если истёк/не было).
    Если человек платит во время пробных дней, платный доступ включается сразу, а оставшееся бесплатное время добавляется к нему: ранняя покупка
    не отнимает бесплатных часов (ТЗ §6). paid_at — когда человек реально заплатил (по часам Telegram): запоздалое подтверждение не отнимает условий.
    Возвращает новую дату окончания."""
    user = get_user(user_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    moment = paid_at or now
    if getattr(moment, "tzinfo", None) is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    current_until = _parse_dt(user["subscription_paid_until"]) if user else None
    base = current_until if (current_until and current_until > moment) else moment
    from .access_state import STATE_TRIAL, access_state
    state = access_state(user_id, moment)
    if state and state["state"] == STATE_TRIAL and state["trial_ends_at"]:
        free_left_from = _parse_dt(state["trial_ends_at"])
        if free_left_from and free_left_from > base:
            base = free_left_from
    new_until = base + timedelta(days=30 * months)
    if expires_at is not None and expires_at > new_until:
        new_until = expires_at                         # подписка Stars: срок, который назвал Telegram, не короче того, что посчитали мы

    conn = connect()
    cursor = conn.cursor()
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


def revoke_subscription(user_id):
    """После возврата Stars: оплаченный срок и ссылка в канал снимаются (отметка «платил раньше» остаётся — решение по цене за админом)."""
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_paid_until=NULL WHERE telegram_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def gate_applies_to(user_id):
    """Гейт применяется ТОЛЬКО к пользователям, зарегистрированным после
    SUBSCRIPTION_GATE_CUTOVER — уже существующие пользователи бота никогда
    не блокируются этой функцией задним числом, даже если гейт включён.

    Исключение — аккаунты, которым админ выдал временный доступ или включил сценарий триала (db/access.py): для них гейт
    работает всегда, независимо от общего выключателя."""
    return gate_applies_to_user(get_user(user_id))


def gate_applies_to_user(user):
    """То же по уже загруженной строке пользователя (планировщик перебирает всех — лишний запрос на каждого не нужен)."""
    from config import SUBSCRIPTION_GATE_ENABLED, SUBSCRIPTION_GATE_CUTOVER
    if not user:
        return False
    if "access_mode" in user.keys() and user["access_mode"] in ("timed", "trial"):
        return True
    if not SUBSCRIPTION_GATE_ENABLED:
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
    """True, если пользователю можно полноценно пользоваться ботом/мини-аппом прямо сейчас: гейт не применяется к нему, ИЛИ он ещё в триале
    (или ещё не открывал приложение), ИЛИ идут подарочные дни, ИЛИ у него активна оплаченная подписка. В дополнительном окне после триала
    (grace) основные действия закрыты — оно «только чтение»: см. db/access_state.py."""
    if not gate_applies_to(user_id):
        return True
    from .access_state import access_state
    info = access_state(user_id)
    return bool(info and info["allowed"])


def get_auto_renew_charge(user_id):
    """Номер платежа, по которому можно отключить автопродление, или None, если подписка оформлена без продления."""
    user = get_user(user_id)
    if not user or "subscription_charge_id" not in user.keys():
        return None
    return user["subscription_charge_id"] or None


def set_auto_renew(user_id, enabled):
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_auto_renew=? WHERE telegram_id=?", (1 if enabled else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def payment_pending(user_id, minutes=30):
    """Человек открыл оплату (счёт выставлен недавно), а подтверждения ещё нет — экран показывает «ждём подтверждение», а не «оплати» (ТЗ §19)."""
    conn = connect()
    try:
        asked = conn.execute("SELECT MAX(at) AS at FROM user_events WHERE user_id=? AND name='subscription_invoice'", (user_id,)).fetchone()["at"]
        if not asked:
            return False
        asked_at = _parse_dt(asked)
        if asked_at is None or datetime.now(timezone.utc).replace(tzinfo=None) - asked_at > timedelta(minutes=minutes):
            return False
        paid = conn.execute(
            "SELECT 1 FROM stars_payments WHERE user_id=? AND payload LIKE 'subscription:%' AND created_at >= ? LIMIT 1", (user_id, asked)).fetchone()
    finally:
        conn.close()
    return paid is None


def subscription_overview(user_id):
    """Для экрана «Подписка» (ТЗ §9, §19): срок, как продлевается, можно ли отключить продление, ждём ли подтверждение, как связаться при сбое."""
    from config import (SUBSCRIPTION_AUTORENEW_ENABLED, SUBSCRIPTION_INTRO_PRICE_STARS, SUBSCRIPTION_RENEWAL_PRICE_STARS, SUPPORT_CONTACT, SUPPORT_EMAIL)
    from .access_state import access_state, fmt_local
    from .trial import INTRO_USD, RENEWAL_USD, intro_price_open
    user = get_user(user_id)
    if user is None:
        return None
    info = access_state(user_id)
    charge = get_auto_renew_charge(user_id)
    auto = bool(user["subscription_auto_renew"]) if "subscription_auto_renew" in user.keys() else False
    paid = bool(info and info["state"] == "paid")
    if paid and charge:
        renewal_text = ("Продление автоматическое: каждые 30 дней спишется полная цена, пока ты не отключишь его." if auto
                        else "Автопродление отключено: после окончания срока доступ закроется, пока ты не продлишь подписку.")
    elif paid:
        renewal_text = "Продление вручную: когда срок подойдёт к концу, я напомню — само ничего не спишется."
    else:
        renewal_text = ("Первая покупка — разовая, на 30 дней. Дальше можно оформить подписку с автопродлением; отключить её можно в любой момент."
                        if SUBSCRIPTION_AUTORENEW_ENABLED else "Продление вручную: само ничего не спишется.")
    return {
        "state": info["state"], "reason_text": info["reason_text"], "paid": paid, "paid_until": info["paid_until"],
        "paid_until_text": fmt_local(_parse_dt(info["paid_until"]), info["tz"]) if info["paid_until"] else "",
        "auto_renew": auto and bool(charge), "can_toggle": bool(charge), "autorenew_available": bool(SUBSCRIPTION_AUTORENEW_ENABLED),
        "renewal_text": renewal_text, "payment_pending": payment_pending(user_id),
        "intro_active": intro_price_open(user_id), "intro_price_stars": SUBSCRIPTION_INTRO_PRICE_STARS, "renewal_price_stars": SUBSCRIPTION_RENEWAL_PRICE_STARS,
        "intro_usd": INTRO_USD, "renewal_usd": RENEWAL_USD, "support": SUPPORT_CONTACT or SUPPORT_EMAIL,
    }


def get_subscription_status(user_id):
    """Единая сводка для UI/сообщений."""
    from config import SUBSCRIPTION_TRIAL_DAYS, SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL
    user = get_user(user_id)
    streak = int(user["streak"] or 0) if user else 0
    trial_day = get_trial_day(user_id)
    access_mode = (user["access_mode"] if user and "access_mode" in user.keys() else None) or "open"
    from .access_state import access_state
    state = access_state(user_id) or {}
    return {
        "state": state.get("state"), "reason_text": state.get("reason_text"), "ends_at": state.get("ends_at"), "read_only": bool(state.get("read_only")),
        "access_mode": access_mode,
        "trial_day": trial_day,
        "trial_days_total": SUBSCRIPTION_TRIAL_DAYS,
        "in_trial": is_in_trial(user_id),
        "trial_expired": state.get("state") in ("grace", "expired") if state.get("scenario") else (trial_day or 0) > SUBSCRIPTION_TRIAL_DAYS,
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
