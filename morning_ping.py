"""
morning_ping.py
Ежедневное утреннее сообщение, персонализированное под стиль общения
(settings.ai_style) и то, что AI уже знает о пользователе (user_ai_profile).
Падает мягко на статический текст, если AI недоступен.
"""

import logging

from db import get_all_users, get_settings, get_ai_style, get_user_profile, log_error
from multi_agent import generate_morning_message
from alerts import notify_admins

logger = logging.getLogger("morning_ping")

FALLBACK_TEXT = (
    "☀️ Доброе утро! Новый день — новая возможность продвинуться к цели. "
    "Загляните в привычки, когда будет минутка 💪"
)


async def run_morning_ping(bot):
    users = get_all_users()
    sent = 0
    failed = 0

    for user in users:
        telegram_id = user["telegram_id"]

        settings = get_settings(telegram_id)
        if not settings or settings["reminders"] == 0:
            continue

        try:
            style = get_ai_style(telegram_id) or "neutral"
            # Утренний проактивный пинг не читает долгую память чата.
            # Старые темы не должны всплывать сами по себе.
            text = await generate_morning_message(style, "", user["streak"])
            if not text:
                text = FALLBACK_TEXT

            await bot.send_message(chat_id=telegram_id, text=text)
            sent += 1

        except Exception as e:
            failed += 1
            logger.warning(f"Не удалось отправить утреннее сообщение {telegram_id}: {e}")
            log_error("morning_ping", e, telegram_id)

    if failed > max(5, sent // 5):
        # Много ошибок относительно успешных отправок — стоит посмотреть.
        await notify_admins(
            bot,
            f"morning_ping: {sent} отправлено, {failed} ошибок за прогон."
        )
