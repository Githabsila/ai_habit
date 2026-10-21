"""
Доставка сообщений триала (каталог и тексты — trial_messages.py, состояние человека — db/trial.py).

Раз в минуту проходит по людям, у которых идёт СЦЕНАРИЙ триала (режим «trial» у аккаунта или общий гейт подписки; у тестировщиков со
свободным или временным доступом триал-сообщений нет), и шлёт то, что подходит им по дню триала, местному времени и отметкам.

Защита от спама: сообщение можно выключить в админке (все сразу или по одному), не больше трёх сообщений триала в день, не в тихие часы
и не человеку, выключившему напоминания (ссылка в закрытый канал — исключение: человек за неё заплатил); от повторов защищает
claim_notification (повторный тик в то же окно ничего не отправит); при ошибке отправки claim откатывается и следующий тик повторит.

Выключено по умолчанию на уровне гейта (SUBSCRIPTION_GATE_ENABLED=false): без режима «trial» у аккаунта сообщений нет.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from db import (
    get_all_users, get_timezone, get_settings,
    claim_notification, release_notification, notification_scope,
)
from db.settings import in_quiet_hours
from db.access import trial_scenario_for_user
from db import trial as trial_db
from db.subscription import try_grant_channel_access
from keyboards import trial_keyboard
import trial_messages

logger = logging.getLogger("subscription_scheduler")

# Не больше стольких сообщений триала человеку за местный день (не считая ссылки в закрытый канал).
DAILY_CAP = 3


def build_message(key, snap, link=None):
    """(текст, клавиатура) сообщения триала для данного состояния человека — и для рассылки, и для «отправить» из админки."""
    message = trial_messages.BY_KEY[key]
    text = trial_messages.render(key, snap, link=link)
    return text, trial_keyboard(message.buttons, snap["price_stars"])


async def run_trial_reminders(bot):
    """Один проход по людям в сценарии триала."""
    users = get_all_users(include_blocked=False)
    scope = notification_scope(bot)
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        if not trial_scenario_for_user(user):
            continue

        try:
            settings = get_settings(telegram_id)
            if not settings:
                continue
            try:
                now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            except Exception:
                continue
            snap = trial_db.snapshot(telegram_id, now_local.astimezone(timezone.utc))
            due = trial_messages.due_messages(snap)
            if not due:
                continue
            reminders_off = settings["reminders"] == 0
            quiet = in_quiet_hours(settings, now_local)
            sent_today = trial_db.sent_today_count(telegram_id, snap["local_date"])

            for message in due:
                if not trial_db.message_enabled(message.key):
                    continue
                if message.action != "channel_link" and (reminders_off or quiet or sent_today >= DAILY_CAP):
                    continue
                kind = f"trial:{message.key}"
                if not claim_notification(telegram_id, snap["local_date"], kind, scope):
                    continue
                try:
                    link = None
                    if message.action == "channel_link":
                        link = await try_grant_channel_access(bot, telegram_id)
                        if not link:
                            release_notification(telegram_id, snap["local_date"], kind, scope)
                            continue
                    text, keyboard = build_message(message.key, snap, link=link)
                    await bot.send_message(telegram_id, text, reply_markup=keyboard)
                except Exception:
                    release_notification(telegram_id, snap["local_date"], kind, scope)
                    raise
                sent += 1
                if message.action != "channel_link":
                    sent_today += 1
        except Exception:
            logger.exception("Ошибка сообщения триала для %s", telegram_id)

    if sent:
        logger.info("Сообщения триала/подписки: отправлено %s", sent)
