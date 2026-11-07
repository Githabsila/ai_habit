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


# Автопродление: Telegram присылает подтверждение списания примерно в момент окончания срока. Чтобы доступ не «моргнул», пока оно идёт,
# у подписки с автопродлением доступ держится ещё RENEWAL_GRACE_HOURS после конца срока. Не пришло — через сутки доступ закрывается как обычно.
RENEWAL_GRACE_HOURS = 24


def auto_renew_active(user):
    """У подписки включено автопродление Stars (есть платёж, по которому Telegram продлевает, и человек его не отключил)."""
    if user is None:
        return False
    keys = user.keys()
    return bool("subscription_auto_renew" in keys and user["subscription_auto_renew"]
                and "subscription_charge_id" in keys and user["subscription_charge_id"])


def paid_access_until(user):
    """До какого момента открыт платный доступ: конец срока, а при автопродлении — плюс RENEWAL_GRACE_HOURS на подтверждение списания."""
    if user is None or "subscription_paid_until" not in user.keys():
        return None
    until = _parse_dt(user["subscription_paid_until"])
    if until and auto_renew_active(user):
        until = until + timedelta(hours=RENEWAL_GRACE_HOURS)
    return until


def has_active_subscription(user_id):
    until = paid_access_until(get_user(user_id))
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


def add_calendar_months(moment, months, anchor_day):
    """Календарные месяцы с «якорным» числом: дата переезжает на тот же день через `months` месяцев, а если такого дня нет
    (31 → февраль) — на последний день месяца. Якорь хранится отдельно, поэтому после короткого месяца срок возвращается к нему:
    31.01 → 28.02 → 31.03, а не 28.03. Время суток сохраняется."""
    import calendar
    total = moment.month - 1 + int(months)
    year, month = moment.year + total // 12, total % 12 + 1
    day = min(int(anchor_day), calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def record_subscription_payment(user_id, months=1, paid_at=None, expires_at=None, calendar_months=False):
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
    anchor_day = None
    if calendar_months:
        # Оплата картой — по календарным месяцам (решение 05.11: «в коротком месяце доступ не обрезать»). Подписка продолжается —
        # держим прежний якорь; началась заново (срока не было или он истёк) — якорь = число, с которого считаем.
        stored = user["subscription_anchor_day"] if user and "subscription_anchor_day" in user.keys() else None
        continuing = bool(current_until and current_until > moment)
        anchor_day = int(stored) if (continuing and stored) else base.day
        new_until = add_calendar_months(base, months, anchor_day)
    else:
        new_until = base + timedelta(days=30 * months)  # звёзды Telegram: подписка бывает только на 30 дней
    if expires_at is not None and expires_at > new_until:
        new_until = expires_at                         # подписка Stars: срок, который назвал Telegram, не короче того, что посчитали мы

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE users
           SET subscription_paid_until=?,
               subscription_first_payment_at=COALESCE(subscription_first_payment_at, CURRENT_TIMESTAMP),
               subscription_anchor_day=COALESCE(?, subscription_anchor_day)
           WHERE telegram_id=?""",
        (new_until.isoformat(), anchor_day, user_id),
    )
    conn.commit()
    conn.close()
    return new_until


def take_back_one_period(user_id, days=30):
    """Возврат одного платежа Stars (прогон 07.11, P3): снимаем один оплаченный период, а не весь срок — у человека, оплатившего два месяца,
    второй остаётся. Срок не уходит в прошлое: если остаётся меньше нуля — подписки нет."""
    user = get_user(user_id)
    until = _parse_dt(user["subscription_paid_until"]) if user is not None else None
    left = until - timedelta(days=days) if until else None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?",
                     (left.isoformat() if left and left > now else None, user_id))
        conn.commit()
    finally:
        conn.close()


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
    return registered_after_cutover(user["created_at"] if "created_at" in user.keys() else None, SUBSCRIPTION_GATE_CUTOVER)


def registered_after_cutover(created_at, cutover):
    """Попадает ли аккаунт под общий гейт по дате регистрации. cutover = "all" (или "*") — под гейт попадают все, и уже зарегистрированные: каждый получает свежий
    триал с первого открытия приложения (решение владельца 28.10). Иначе — дата ГГГГ-ММ-ДД: гейт только для зарегистрированных в этот день и позже."""
    if str(cutover or "").strip().lower() in ("all", "*"):
        return True
    created = _parse_dt(created_at)
    if not created:
        return False
    try:
        return created.date() >= date.fromisoformat(str(cutover).strip())
    except ValueError:
        return False


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
    from config import SUBSCRIPTION_TRIAL_DAYS
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
        "streak_needed_for_channel": channel_streak_needed(user),
        "channel_eligible": streak >= channel_streak_needed(user),
        "channel_granted": bool(user and user["channel_access_granted_at"]) if user and "channel_access_granted_at" in user.keys() else False,
        "price_stars": get_subscription_price_stars(user_id),
    }


def channel_streak_needed(user):
    """Сколько дней серии нужно для входа в закрытый канал: обычно SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL (3), а если бот уже убирал человека
    из канала — CHANNEL_REENTRY_STREAK_DAYS (5)."""
    from config import CHANNEL_REENTRY_STREAK_DAYS, SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL
    removed = user["channel_removed_at"] if user is not None and "channel_removed_at" in user.keys() else None
    return CHANNEL_REENTRY_STREAK_DAYS if removed else SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL


def channel_removal_reason(user_id):
    """Почему человеку больше не место в канале: 'subscription' — подписка кончилась, 'streak' — серия сгорела (после заморозок, защиты
    и помощи друга), None — всё в порядке или он и не в канале."""
    user = get_user(user_id)
    if user is None or not ("channel_access_granted_at" in user.keys() and user["channel_access_granted_at"]):
        return None
    if not has_active_subscription(user_id):
        return "subscription"
    if int(user["streak"] or 0) <= 0:
        return "streak"
    return None


def mark_channel_removed(user_id):
    conn = connect()
    try:
        conn.execute("UPDATE users SET channel_access_granted_at=NULL, channel_removed_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def mark_channel_access_granted(user_id):
    conn = connect()
    conn.execute(
        "UPDATE users SET channel_access_granted_at=CURRENT_TIMESTAMP WHERE telegram_id=?",
        (user_id,),
    )
    conn.commit()
    conn.close()


async def try_grant_channel_access(bot, user_id):
    """Если пользователь оплатил подписку И набрал нужный стрик, И ещё не получал ссылку — создаёт личную ссылку в закрытый канал и
    возвращает её. Ничего не делает (без ошибки), если CLOSED_CHANNEL_ID не настроен.

    Прогон 07.11: (A3) выдача занимается атомарно до запроса к Telegram — параллельные отметки привычек давали несколько ссылок;
    (A1) ссылка — заявка (creates_join_request) с именем user_<id>: войти по ней может только сам получатель и только пока доступ
    действует (handlers/channel_join.py) — раньше её можно было отдать другу или потратить после конца подписки."""
    from config import CLOSED_CHANNEL_ID

    status = get_subscription_status(user_id)
    if status["channel_granted"] or not status["channel_eligible"] or not status["has_paid"]:
        return None
    if not CLOSED_CHANNEL_ID:
        return None
    conn = connect()
    try:
        claimed = conn.execute("UPDATE users SET channel_access_granted_at=CURRENT_TIMESTAMP WHERE telegram_id=? AND channel_access_granted_at IS NULL",
                               (user_id,)).rowcount
        conn.commit()
    finally:
        conn.close()
    if not claimed:
        return None                                            # параллельный вызов уже выдаёт ссылку
    try:
        invite = await bot.create_chat_invite_link(chat_id=CLOSED_CHANNEL_ID, name=f"user_{user_id}", creates_join_request=True)
    except Exception:
        conn = connect()
        try:
            conn.execute("UPDATE users SET channel_access_granted_at=NULL WHERE telegram_id=?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        raise
    conn = connect()
    try:
        conn.execute("UPDATE users SET channel_invite_link=? WHERE telegram_id=?", (invite.invite_link, user_id))
        conn.commit()
    finally:
        conn.close()
    return invite.invite_link
