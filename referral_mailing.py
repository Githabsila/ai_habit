"""
Мини-рассылка «позови друга» для новичков первой недели и уведомления о наградах за приглашения.

Правила рассылки (решение владельца, 16.10): раз в два дня в первую неделю — на 2-й, 4-й и 6-й день с регистрации (при пропуске — на следующий,
но не чаще раза в REPEAT_GAP_HOURS часов). Текст зовёт отправить приглашение и честно называет условия: 100 Адам коинов за первых двух приглашённых.
Под текстом кнопка «Позвать друга» — она открывает выбор чата в Telegram с готовым приглашением.

Когда НЕ пишем: пока человек не прошёл анкету или заблокировал бота; если выключены напоминания или идут тихие часы; если награды уже все получены
(слотов не осталось) или выплаты на паузе; в 13:00–14:00 местного времени мы пишем только если недавно (за час) бот ничего не присылал и Адам не писал
за полтора часа. Выключатель — флаг referral_mailing (записи нет — включено всем; «выключено» — никому; процент — раскатка).

Второй job (run_referral_notices) сообщает пригласившему о награде, которая выплачена ПОСЛЕ первой отметки друга: награда «сразу» объявляется в /start.
"""
import html
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import (
    claim_notification, get_settings, get_timezone, in_quiet_hours, log_error, mark_bot_blocked, notification_scope, release_notification,
)
from db import referrals, tracking
from db.adam_checkin import adam_wrote_minutes_ago
from db.core import connect

logger = logging.getLogger(__name__)

KIND = "referral_invite"
STEP_DAYS = (2, 4, 6)              # дни с регистрации (по местному календарю человека); +1 день запаса на случай пропуска
SEND_FROM_MIN = 13 * 60            # окно отправки: обеденный перерыв, между 12:00-контролем и днём Адама (с 14:00)
SEND_TO_MIN = 14 * 60
BOT_GAP_MIN = 60                   # после любого сообщения бота
ADAM_GAP_MIN = 90                  # после сообщения Адама
REPEAT_GAP_HOURS = 40              # между двумя письмами этой рассылки
SHARE_TEXT = "Давай держать привычки вместе в ADAM — добавляйся в друзья:"
BUTTON_TEXT = "Позвать друга 👥"

# Тексты нейтральные (без родов), каждый шаг — свой; {reward} — «100 Адам коинов сразу» либо «…, когда друг отметит первую привычку».
FIRST_TEXTS = (
    "Наше приложение интереснее использовать вместе с друзьями 🤝\n\n"
    "Пригласи друга и получи {reward}. Награда даётся за первых двух приглашённых.\n\n"
    "Отправить приглашение 👇",
    "Друзья в ADAM видят, кто сегодня отметился, и подталкивают друг друга — вместе привычки держатся легче 💪\n\n"
    "Позови друга и получи {reward}. Награда — за первых двух приглашённых 👇",
    "Почти неделя с ADAM позади 🎉 Если нравится, позови друга: вместе проще не сорваться.\n\n"
    "Награда — {reward}, она даётся за первых двух приглашённых 👇",
)
SECOND_TEXTS = (
    "Одно приглашение уже сработало 👏 Позови ещё одного друга и получи {reward}. Это последняя награда за приглашения.\n\n"
    "Отправить приглашение 👇",
    "С друзьями привычки держатся легче 💪 Осталось одно приглашение с наградой: {reward}.\n\n"
    "Позвать друга 👇",
    "Ещё один друг в ADAM — ещё один человек, который подтолкнёт отметиться 🤝 Последнее приглашение с наградой: {reward} 👇",
)


def reward_phrase(mode="now"):
    coins = referrals.REWARD_COINS
    if mode == "after_mark":
        return f"{coins} Адам коинов, когда друг отметит первую привычку"
    return f"{coins} Адам коинов сразу"


def compose_text(step, used=0, mode="now"):
    texts = SECOND_TEXTS if used >= 1 else FIRST_TEXTS
    return texts[min(max(step, 0), len(texts) - 1)].format(reward=reward_phrase(mode))


def share_url(bot_username, user_id):
    """Готовое приглашение: подпись и ссылка (t.me/share/url кладёт первым параметр url, поэтому подпись едет в нём, а ссылка — в text)."""
    link = f"https://t.me/{bot_username}?start=friend_{user_id}"
    return f"https://t.me/share/url?url={quote(SHARE_TEXT, safe='')}&text={quote(link, safe='')}"


def share_markup(bot_username, user_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=BUTTON_TEXT, url=share_url(bot_username, user_id))]])


def step_for_age(age_days):
    for index, day in enumerate(STEP_DAYS):
        if day <= age_days <= day + 1:
            return index
    return None


def _minutes_since(moment, now_utc):
    parsed = tracking._parse(moment)
    if parsed is None:
        return None
    return max(0, int((now_utc.replace(tzinfo=None) - parsed).total_seconds() // 60))


def _last_log_minutes(user_id, now_utc, category=None):
    conn = connect()
    try:
        if category:
            row = conn.execute("SELECT MAX(sent_at) AS t FROM notification_log WHERE user_id=? AND category=?", (user_id, category)).fetchone()
        else:
            row = conn.execute("SELECT MAX(sent_at) AS t FROM notification_log WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _minutes_since(row["t"], now_utc) if row and row["t"] else None


def _skip(reason):
    return {"send": False, "reason": reason}


def mailing_plan(user_id, created_at, now_utc=None):
    """Решение для одного человека: {'send': True, 'step', 'text', 'used'} или {'send': False, 'reason'}. Ничего не отправляет и не резервирует."""
    now_utc = now_utc or datetime.now(timezone.utc)
    created = tracking._parse(created_at)
    if created is None:
        return _skip("no_created_at")
    tz = ZoneInfo(get_timezone(user_id))
    now_local = now_utc.astimezone(tz)
    age_days = (now_local.date() - created.replace(tzinfo=timezone.utc).astimezone(tz).date()).days
    step = step_for_age(age_days)
    if step is None:
        return _skip("not_due")
    if not SEND_FROM_MIN <= now_local.hour * 60 + now_local.minute < SEND_TO_MIN:
        return _skip("outside_window")
    settings = get_settings(user_id)
    if not settings or not settings["reminders"]:
        return _skip("reminders_off")
    if in_quiet_hours(settings, now_local):
        return _skip("quiet_hours")
    if not referrals.mailing_enabled_for(user_id):
        return _skip("disabled")
    summary = referrals.reward_summary(user_id)
    if summary["paused"]:
        return _skip("paused")
    if summary["left"] <= 0:
        return _skip("slots_used")
    since_bot = _last_log_minutes(user_id, now_utc)
    if since_bot is not None and since_bot < BOT_GAP_MIN:
        return _skip("bot_wrote_recently")
    since_adam = adam_wrote_minutes_ago(user_id, now_local.date().isoformat(), now_utc)
    if since_adam is not None and since_adam < ADAM_GAP_MIN:
        return _skip("adam_wrote_recently")
    since_mail = _last_log_minutes(user_id, now_utc, KIND)
    if since_mail is not None and since_mail < REPEAT_GAP_HOURS * 60:
        return _skip("too_soon_after_previous")
    return {"send": True, "step": step, "used": summary["used"], "text": compose_text(step, summary["used"], summary["mode"]), "age_days": age_days}


async def _bot_username(bot):
    try:
        return (await bot.get_me()).username
    except Exception:
        logger.warning("Рассылка «позови друга»: не удалось узнать имя бота", exc_info=True)
        return None


async def run_referral_mailing(bot, now_utc=None):
    """Тикает каждые пять минут; берёт людей первой недели и пишет тем, у кого сейчас подходящее окно."""
    if not bot:
        return
    scope = notification_scope(bot)
    now_utc = now_utc or datetime.now(timezone.utc)
    username = None
    sent = 0
    for telegram_id, created_at in referrals.mailing_candidates(now_utc):
        try:
            plan = mailing_plan(telegram_id, created_at, now_utc)
            if not plan["send"]:
                continue
            day_key = f"step{plan['step']}"
            if not claim_notification(telegram_id, day_key, KIND, scope):
                continue
            try:
                username = username or await _bot_username(bot)
                if not username:
                    raise RuntimeError("имя бота неизвестно")
                await bot.send_message(telegram_id, plan["text"], reply_markup=share_markup(username, telegram_id))
            except Exception:
                release_notification(telegram_id, day_key, KIND, scope)
                raise
            sent += 1
            tracking.track_server(telegram_id, "referral_mail", step=day_key, meta={"used": plan["used"]})
        except TelegramForbiddenError as exc:
            mark_bot_blocked(telegram_id)
            log_error("referral_mailing", exc, telegram_id)
        except Exception as exc:
            log_error("referral_mailing", exc, telegram_id)
    if sent:
        logger.info("Рассылка «позови друга»: отправлено %s сообщений", sent)


def notice_text(name, left):
    tail = f" Осталось приглашений с наградой: {left}." if left > 0 else " Это была последняя награда за приглашения."
    return f"🎉 У <b>{html.escape(name or 'твоего друга')}</b> первая отметка в ADAM — тебе +{referrals.REWARD_COINS} Адам коинов за приглашение.{tail}"


async def run_referral_notices(bot):
    """Сообщает пригласившему о награде, выплаченной после первой отметки друга. Одна попытка: сбой доставки не должен повторяться бесконечно."""
    if not bot:
        return
    for payment in referrals.unnotified_payments():
        try:
            left = referrals.reward_summary(payment["inviter_id"])["left"]
            await bot.send_message(payment["inviter_id"], notice_text(payment["invitee_name"], left), parse_mode="HTML")
        except TelegramForbiddenError as exc:
            mark_bot_blocked(payment["inviter_id"])
            log_error("referral_notice", exc, payment["inviter_id"])
        except Exception as exc:
            log_error("referral_notice", exc, payment["inviter_id"])
        finally:
            referrals.mark_notified(payment["id"])
