"""
Пром 13: проактивные напоминания триала → подписки.

Выключено по умолчанию — если SUBSCRIPTION_GATE_ENABLED=False, эти
сообщения всё ещё информационные (напоминают, что покупка открывает
закрытый канал), но никого не блокируют (см. middlewares/access_control.py
и db/subscription.py bot_access_allowed).

Не пытается покрыть дословно каждый из сценариев исходного ТЗ — только
содержательно разные состояния. Тексты — adam_messages.py, там же их
проще всего переформулировать.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from db import (
    get_all_users, get_timezone, get_settings,
    claim_notification, notification_scope,
    get_trial_day, is_in_trial, has_active_subscription, get_subscription_status,
)
from adam_messages import (
    format_trial_day2_nudge, format_trial_last_day_banner, format_trial_channel_progress_nudge,
)
from keyboards import subscription_buy_keyboard

logger = logging.getLogger("subscription_scheduler")

# В какой локальный час пользователя слать дневное напоминание триала.
TRIAL_REMINDER_HOUR = 12


async def run_trial_reminders(bot):
    """Раз в день (в TRIAL_REMINDER_HOUR по локальному времени пользователя)
    отправляет ОДНО сообщение, релевантное текущему состоянию триала/подписки."""
    users = get_all_users()
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        try:
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
        except Exception:
            continue
        if now_local.hour != TRIAL_REMINDER_HOUR or now_local.minute != 0:
            continue

        try:
            status = get_subscription_status(telegram_id)
            day = status["trial_day"]
            if day is None:
                continue

            text = None
            keyboard = None

            if status["has_paid"]:
                # Уже оплатил — единственное, что имеет смысл напоминать:
                # прогресс к доступу в закрытый канал, и только пока он ещё
                # не выдан.
                if not status["channel_granted"] and not status["channel_eligible"]:
                    text = format_trial_channel_progress_nudge(
                        status["streak"], status["streak_needed_for_channel"]
                    )
            elif day == 2:
                text = format_trial_day2_nudge(status["streak"])
            elif day == status["trial_days_total"]:
                # Последний бесплатный день.
                text = format_trial_last_day_banner(status["price_stars"])
                keyboard = subscription_buy_keyboard(status["price_stars"])
            else:
                continue

            if not text:
                continue

            day_key = now_local.date().isoformat()
            if not claim_notification(telegram_id, day_key, "trial_reminder", notification_scope(bot)):
                continue

            await bot.send_message(telegram_id, text, reply_markup=keyboard)
            sent += 1
        except Exception:
            logger.exception("Ошибка триал-напоминания для %s", telegram_id)

    if sent:
        logger.info("Напоминания триала/подписки: отправлено %s", sent)
