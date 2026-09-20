"""
bot_middlewares.py

Request-мидлвари уровня Bot API (aiogram session.middleware) — в отличие от
обычных middleware диспетчера (перехватывают входящие апдейты), эти
оборачивают КАЖДЫЙ исходящий вызов методов Bot API (send_message,
edit_message_text и т.д.) из любого места кода: хендлеров, джобов
планировщика, вебхуков.
"""

import asyncio
import logging

from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger("bot_middlewares")

# Планировщик рассылает напоминания по всем пользователям в цикле (см.
# coach.py/streak_scheduler.py) — при росте базы или совпадении часового
# пояса у многих пользователей это может упереться в flood control
# Telegram (429 Too Many Requests). Раньше такой ответ просто попадал в
# обычный except Exception у вызывающего кода и сообщение молча терялось
# (после этого claim_notification снимался, а release_notification давал
# шанс на повтор только на следующем тике — то есть до минуты задержки,
# а иногда и позже, если окно in_time_window уже закрылось).
MAX_RETRY_ATTEMPTS = 2


async def retry_flood_control(make_request, bot, method):
    """Единая точка повтора при TelegramRetryAfter — работает для ЛЮБОГО
    метода Bot API, вызванного из любого места, без необходимости
    оборачивать каждый отдельный bot.send_message() вручную."""
    attempt = 0
    while True:
        try:
            return await make_request(bot, method)
        except TelegramRetryAfter as e:
            attempt += 1
            if attempt > MAX_RETRY_ATTEMPTS:
                raise
            wait_s = e.retry_after + 0.5
            logger.warning(
                "Flood control (попытка %s/%s): жду %.1fс перед повтором %s",
                attempt, MAX_RETRY_ATTEMPTS, wait_s, type(method).__name__,
            )
            await asyncio.sleep(wait_s)
