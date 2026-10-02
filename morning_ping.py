"""
morning_ping.py
Ежедневное утреннее сообщение, персонализированное под стиль общения
(settings.ai_style) и то, что AI уже знает о пользователе (user_ai_profile).
Падает мягко на статический текст, если AI недоступен.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError

from db import get_all_users, get_settings, get_ai_style, get_user_profile, log_error, get_timezone, claim_notification, release_notification, notification_scope, in_time_window, reminder_category_enabled, in_quiet_hours, is_bot_blocked, mark_bot_blocked
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

        # Найдено при разборе мониторинга ошибок: без этой проверки каждый
        # прогон заново пытался писать уже заблокировавшим бота пользователям —
        # Telegram отвечает Forbidden на КАЖДУЮ такую попытку, это не
        # временный сбой. См. db/users.py::mark_bot_blocked/is_bot_blocked.
        if is_bot_blocked(telegram_id):
            continue

        settings = get_settings(telegram_id)
        if not reminder_category_enabled(settings, "habits"):
            continue

        try:
            # Утренняя рассылка привязана к локальному времени пользователя.
            # in_time_window (не "== ровно эта минута") даёт запас на случай,
            # если тик планировщика задержался или процесс был недоступен
            # ровно в 06:00 (Railway передеплой и т.п.) — claim_notification
            # всё равно гарантирует ровно одно утреннее сообщение в день,
            # даже если окно "поймано" несколько тиков подряд.
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if in_quiet_hours(settings, now_local):
                continue
            if not in_time_window(now_local, hour=6, minute=0):
                continue
            day_key = now_local.date().isoformat()
            scope = notification_scope(bot)
            if not claim_notification(telegram_id, day_key, "morning_6", scope):
                continue

            try:
                style = get_ai_style(telegram_id) or "neutral"
                # Утренний проактивный пинг не читает долгую память чата.
                # Старые темы не должны всплывать сами по себе.
                text = await generate_morning_message(style, "", user["streak"])
                if not text:
                    text = FALLBACK_TEXT

                await bot.send_message(chat_id=telegram_id, text=text)
            except Exception:
                # Резерв освобождаем, только если реально не отправили —
                # иначе временный сбой (AI/сеть) молча "съедал" всё утреннее
                # сообщение на весь день без единой попытки повтора.
                release_notification(telegram_id, day_key, "morning_6", scope)
                raise
            sent += 1

        except TelegramForbiddenError as e:
            # Постоянный отказ (бот заблокирован/пользователь удалён) — не
            # временный сбой, повторять нет смысла. Помечаем один раз, чтобы
            # следующие прогоны (и остальные job'ы — habit_checkpoint_* и
            # т.д.) больше не тратили запрос и не писали ту же ошибку снова.
            mark_bot_blocked(telegram_id)
            log_error("morning_ping", e, telegram_id)
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
