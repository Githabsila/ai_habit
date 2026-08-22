
import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import WEBAPP_URL

from db import (
    rollover_all_users, get_streak_users, get_timezone, create_daily_tasks,
    has_completed_today, claim_notification, RISK_15, RISK_23,
    get_weekly_bonus_available,
)

logger = logging.getLogger("streak_scheduler")

async def run_streak_rollover(bot=None):
    try:
        changed = rollover_all_users()
        for uid in changed:
            try:
                create_daily_tasks(uid)
            except Exception:
                logger.exception("Не удалось создать ежедневные задания для %s", uid)
        if changed:
            logger.info("Streak rollover: %s users", len(changed))
    except Exception:
        logger.exception("Ошибка rollover ударного режима")

# В памяти держим активные таймеры, чтобы не создавать второй таймер на
# одного пользователя при следующем минутном проходе планировщика.
_active_countdowns = {}


RISK_23_FIRST = [
    "⚡️ До полуночи осталось 60 минут. У тебя ещё есть время удержать ударный режим — закрой хотя бы одну привычку.",
    "🔥 Финишный час. Не отдавай сегодня свою серию так близко к полуночи — выполни хотя бы одну привычку.",
    "😠 23:00. Ты уже дошёл до последнего часа дня. Остался один шаг — закрой хотя бы одну привычку и сохрани ударный режим.",
]


RISK_23_30 = [
    "😡 30 минут до сброса! Закрой хотя бы 1 привычку и спаси свой ударный режим. Время пошло! ⏱️",
    "⚡️ Твой ударный режим сгорит через 30 минут. Сделаешь хотя бы 1 привычку или сдашься❓️",
    "❗️ Не сливай весь свой прогресс за 30 минут! Закрой одну привычку прямо сейчас. 🔥",
    "😡 Осталось 30 минут. Все твои слова про дисциплину — правда или пустой звук? Докажи! 🎯",
    "🚨 Критический момент: 30 минут до обнуления! Закрывай 1 привычку и сохраняй статус. ⏳",
    "❗️ Ты реально готов сжечь ударный режим? У тебя 30 минут, чтобы сделать хотя бы 1 шаг! 🚨",
    "⏱️ 30 минут. Выполни 1 привычку! Сохрани свой результат или начнёшь с полного нуля завтра! 💥",
    "⚡️ Не смей бросать день на финише! 30 минут — закрой одну привычку и с чистой совестью отдыхай! 🏆",
    "😡 30 минут до сброса. Покажи характер или признай, что лень сегодня победила! 🥊",
    "🚨 Осталось 30 минут! Зайди и отметь 1 привычку, чтобы удержать ударный режим! ⚡️",
]


def _countdown_keyboard():
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Открыть ADAM и закрыть привычку", url=WEBAPP_URL)]
        ]
    )


async def _run_countdown(bot, uid, message_id, tz_name, deadline):
    """Редактирует одно сообщение таймера раз в ~30 секунд до локальной полуночи.
    Редактирование не создаёт новые Telegram-уведомления, поэтому пользователь
    не получает спам из 60 отдельных сообщений."""
    key = (uid, message_id)
    try:
        tz = ZoneInfo(tz_name)
        while True:
            now = datetime.now(tz)
            end_dt = datetime.combine(deadline, time(0, 0), tzinfo=tz) + timedelta(days=1)
            remaining = int((end_dt - now).total_seconds())
            if remaining <= 0:
                break

            minutes, seconds = divmod(remaining, 60)
            # Чтобы сообщение не менялось слишком часто, округляем секунды до
            # ближайших 30 секунд. Пользователь всё равно видит живой отсчёт.
            shown_seconds = 30 if seconds > 15 else 0
            text = (
                "🚨 <b>ФИНИШНЫЙ ТАЙМЕР</b>\n\n"
                "😡 Ударный режим под угрозой.\n"
                f"⏱️ Осталось: <b>{minutes:02d}:{shown_seconds:02d}</b>\n\n"
                "Закрой хотя бы одну привычку — и серия будет спасена."
            )
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=_countdown_keyboard(),
                )
            except Exception:
                logger.exception("Не удалось обновить таймер для %s", uid)

            await asyncio.sleep(30)

            # Если человек выполнил хотя бы одну привычку, прекращаем таймер:
            # серия уже спасена.
            if has_completed_today(uid):
                try:
                    await bot.edit_message_text(
                        chat_id=uid,
                        message_id=message_id,
                        text="🔥 <b>Ударный режим спасён!</b>\n\nТы успел закрыть привычку. Хорошая работа.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Ошибка countdown для %s", uid)
    finally:
        _active_countdowns.pop(key, None)


async def run_streak_risk_notifications(bot):
    if not bot:
        return

    for uid in get_streak_users():
        try:
            tz_name = get_timezone(uid)
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)

            # Только одно напоминание — ровно в 23:00 по локальному времени
            # пользователя. После полуночи уведомления об ударном режиме не отправляем.
            if now.hour != 23 or now.minute != 0:
                continue
            if has_completed_today(uid):
                continue

            day = now.date().isoformat()
            if not claim_notification(uid, day, "risk23"):
                continue

            await bot.send_message(
                uid,
                random.choice(RISK_23_FIRST),
                parse_mode="HTML",
                reply_markup=_countdown_keyboard(),
            )
        except Exception:
            logger.exception("Ошибка streak-risk для %s", uid)

async def run_weekly_streak_bonus(bot):
    if not bot:
        return
    for uid in get_streak_users():
        try:
            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if now.weekday() != 6 or now.hour != 10 or now.minute != 0:
                continue
            day = now.date().isoformat()
            if not get_weekly_bonus_available(uid):
                continue
            if not claim_notification(uid, day, "weekly_bonus"):
                continue
            await bot.send_message(
                uid,
                "🎁 Неделя в огне!\n\nТы прошёл предыдущие 7 дней без пропусков. "
                "Открой ADAM и выбери награду: 200 Adam Coin или временную рамку на 7 дней."
            )
        except Exception:
            logger.exception("Ошибка weekly streak bonus для %s", uid)
