"""
Конец оплаченного срока (решение владельца 05.11: «автопродление и автосписание очень важно, на удержании всё строим»).

Экран «Подписка» обещал «когда срок подойдёт к концу, я напомню», а напоминаний не было: при продлении вручную доступ просто закрывался.
Раз в несколько минут, в 12:00 по местному времени человека:
  • без автопродления — за 7, 3 и 1 день до конца и в день окончания: «Подписка до …», кнопка «Включить автопродление» (если оно доступно)
    или «Продлить на 30 дней»; новый срок добавляется к концу текущего — дни не пропадают;
  • с автопродлением — одно честное предупреждение за 3 дня: «спишется N ⭐, ничего делать не нужно, отключить можно в …»;
  • автопродление не прошло (срок кончился, платежа нет) — «Не получилось продлить» и кнопка: доступ держится ещё сутки
    (db/subscription.py::RENEWAL_GRACE_HOURS), чтобы человек успел пополнить звёзды.
Каждое сообщение — один раз на конкретный срок (ключ включает дату окончания). Тихие часы и выключенные «Сводки и отчёты» не трогаем:
это сообщения о деньгах и доступе, их нельзя пропускать (как и «доступ закрыт» в триале).
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

REMIND_DAYS = (7, 3, 1, 0)
AUTO_NOTICE_DAYS = 3
SEND_HOUR = 12
FAILED_AFTER_HOURS = 3          # подтверждение продления от Telegram может прийти чуть позже конца срока — не пугаем раньше времени


def _until_text(until, tz):
    from db.access_state import fmt_local
    return fmt_local(until, tz)


def renewal_keyboard(autorenew_enabled, price, user_id=None):
    """Звёзды — всегда; «Картой» — второй строкой, когда оплата картой включена и товар Tribute привязан (pay_providers.card_link)."""
    text = f"🔁 Включить автопродление · {price} ⭐" if autorenew_enabled else f"⭐ Продлить на 30 дней · {price} ⭐"
    rows = [[InlineKeyboardButton(text=text, callback_data="buy_subscription")]]
    if user_id is not None:
        from pay_providers import card_link
        card = card_link("subscription", user_id=user_id)
        if card:
            rows.append([InlineKeyboardButton(text=f"💳 Картой · {card[1]}", url=card[0])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_text(days_left, until_text, autorenew_enabled):
    when = {0: "сегодня", 1: "завтра"}.get(days_left, f"через {days_left} дн.")
    head = f"⏳ Подписка ADAM Pro заканчивается {when} — {until_text}."
    if autorenew_enabled:
        tail = ("Включи автопродление — и больше не думай об этом: каждые 30 дней спишется полная цена, отключить можно в любой момент. "
                "Оставшиеся дни не пропадут — новый срок начнётся после текущего.")
    else:
        tail = "Продли заранее — оставшиеся дни не пропадут, новый срок начнётся после текущего."
    if days_left == 0:
        tail += " Не продлишь — завтра привычки, план и разговор со мной закроются (история сохранится)."
    return f"{head}\n\n{tail}"


def auto_notice_text(until_text, price):
    return (f"🔁 Напоминаю честно: {until_text} подписка продлится сама — спишется {price} ⭐ за следующие 30 дней. Ничего делать не нужно.\n\n"
            "Отключить продление можно в приложении (раздел «Подписка») или в настройках Telegram.")


def failed_text(price):
    return (f"⚠️ Не получилось продлить подписку: Telegram не списал {price} ⭐ — возможно, не хватило звёзд.\n\n"
            "Доступ держу ещё сутки. Пополни звёзды и продли — серия и история сохранятся.")


def _paid_people():
    from db.core import connect
    conn = connect()
    try:
        return conn.execute("SELECT telegram_id FROM users WHERE subscription_paid_until IS NOT NULL AND COALESCE(bot_blocked_at, '') = '' "
                            "AND COALESCE(banned, 0) = 0").fetchall()
    finally:
        conn.close()


def plan_for(user, now_utc):
    """Что отправить человеку сейчас: (ключ, текст, клавиатура) или None. Чистая логика без отправки — её проверяют тесты."""
    from config import SUBSCRIPTION_AUTORENEW_ENABLED, SUBSCRIPTION_RENEWAL_PRICE_STARS
    from db.streak import get_timezone
    from db.subscription import _parse_dt, auto_renew_active
    until = _parse_dt(user["subscription_paid_until"])
    if until is None:
        return None
    tz_name = get_timezone(user["telegram_id"])
    tz = ZoneInfo(tz_name)
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    local_until = until.replace(tzinfo=timezone.utc).astimezone(tz)
    days_left = (local_until.date() - local_now.date()).days
    until_text = _until_text(until, tz_name)
    price = int(SUBSCRIPTION_RENEWAL_PRICE_STARS)
    stamp = local_until.date().isoformat()
    auto = auto_renew_active(user)
    if auto:
        from datetime import timedelta
        if until <= now_utc - timedelta(hours=FAILED_AFTER_HOURS):   # срок прошёл, продления нет — идут сутки запаса
            return f"sub_renew_failed:{stamp}", failed_text(price), renewal_keyboard(False, price, user["telegram_id"])
        if until <= now_utc:
            return None                                        # ждём подтверждение продления
        if days_left == AUTO_NOTICE_DAYS:
            return f"sub_auto_notice:{stamp}", auto_notice_text(until_text, price), None
        return None
    if until <= now_utc or days_left not in REMIND_DAYS:
        return None
    return (f"sub_end_{days_left}:{stamp}", manual_text(days_left, until_text, SUBSCRIPTION_AUTORENEW_ENABLED),
            renewal_keyboard(SUBSCRIPTION_AUTORENEW_ENABLED, price, user["telegram_id"]))


async def run_renewal_reminders(bot, now_utc=None):
    from db import claim_notification, in_time_window, notification_scope, release_notification
    from db.streak import get_timezone
    from db.users import get_user, is_bot_forbidden_error, mark_bot_blocked
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    scope = notification_scope(bot)
    sent = 0
    for row in _paid_people():
        user_id = row["telegram_id"]
        try:
            user = get_user(user_id)
            local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(get_timezone(user_id)))
            plan = plan_for(user, now_utc)
            if plan is None:
                continue
            key, text, keyboard = plan
            if not key.startswith("sub_renew_failed") and not in_time_window(local_now, hour=SEND_HOUR):
                continue                                       # обычные напоминания — в полдень; «не получилось продлить» — сразу
            kind, stamp = key.split(":", 1)
            if not claim_notification(user_id, stamp, kind, scope):
                continue
            try:
                await bot.send_message(user_id, text, reply_markup=keyboard)
            except Exception as exc:
                release_notification(user_id, stamp, kind, scope)
                if is_bot_forbidden_error(exc):
                    mark_bot_blocked(user_id)
                    continue
                raise
            sent += 1
        except Exception:
            logger.exception("Напоминание о конце подписки не ушло %s", user_id)
    return sent
