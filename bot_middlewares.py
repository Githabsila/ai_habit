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


# ---------------------------------------------------------------------------------------------------------------- журнал отправок
# 11.11 (владелец): «видеть все отработанные сообщения — когда, почему и при каких условиях». Каждое сообщение человеку в личку пишем
# в db/outbox.py вместе с тем, КТО его отправил: первые функции нашего кода в стеке вызова (рассылка, обработчик). По ним админка
# находит рассылку в карте сообщений (bot_messages_map.py). Ни запись, ни поиск источника не могут сорвать отправку.
import os as _os
import sys as _sys

_OUTBOX_METHODS = {"sendMessage", "sendPhoto", "sendDocument", "sendVoice", "sendAudio", "sendVideo", "sendAnimation",
                   "sendSticker", "sendInvoice", "sendMediaGroup", "sendVideoNote"}
_SKIP_PARTS = (f"{_os.sep}aiogram{_os.sep}", f"{_os.sep}asyncio{_os.sep}", "site-packages")
# Сравниваем ИМЯ файла, а не подстроку пути: «alerts.py» совпадало с abuse_alerts.py, и рассылка о подозрениях теряла отправителя (прогон 11.11).
_SKIP_FILES = {"contextlib.py", "functools.py", "bot_middlewares.py", "bot_style.py", "alerts.py"}


def _outbox_chain(depth=3):
    """«модуль.функция» первых функций нашего кода в стеке (ближайшая первой)."""
    names = []
    try:
        frame = _sys._getframe(2)
        while frame is not None and len(names) < depth:
            path = frame.f_code.co_filename or ""
            if not any(part in path for part in _SKIP_PARTS) and _os.path.basename(path) not in _SKIP_FILES and not path.startswith("<"):
                name = f"{frame.f_globals.get('__name__', '?')}.{frame.f_code.co_name}"
                if name not in names:
                    names.append(name)
            frame = frame.f_back
    except Exception:
        pass
    return names


async def log_outbox(make_request, bot, method):
    api_method = getattr(method, "__api_method__", "")
    chat_id = getattr(method, "chat_id", None)
    if api_method not in _OUTBOX_METHODS or not isinstance(chat_id, int) or chat_id <= 0:
        return await make_request(bot, method)
    chain = _outbox_chain()
    text = getattr(method, "text", None) or getattr(method, "caption", None) or getattr(method, "title", None) or ""
    from db import outbox
    try:
        result = await make_request(bot, method)
    except Exception as exc:
        outbox.record(chat_id, api_method, chain[0] if chain else None, " < ".join(chain), text, ok=False, error=type(exc).__name__)
        raise
    outbox.record(chat_id, api_method, chain[0] if chain else None, " < ".join(chain), text)
    return result


# ---------------------------------------------------------------------------------------------------------------- правка фото-сообщения
# Прогон 11.11: приветствие /start (и другие сообщения с баннером, bot_style.send_banner) уходят ФОТО с подписью. Кнопки под ними
# («Умные напоминания», «Админ-панель», меню) делают edit_message_text, а у фото нет текста — Telegram отвечает «there is no text in the
# message to edit», обработчик падает, кнопка «крутится». Здесь одно место на все такие кнопки: правка не удалась именно поэтому —
# отправляем тот же текст с теми же кнопками новым сообщением.
async def edit_photo_fallback(make_request, bot, method):
    try:
        return await make_request(bot, method)
    except Exception as exc:
        from aiogram.methods import EditMessageText, SendMessage
        if not isinstance(method, EditMessageText) or method.chat_id is None or "no text in the message to edit" not in str(exc):
            raise
        logger.info("Правка фото-сообщения %s: отвечаю новым сообщением", method.chat_id)
        return await bot(SendMessage(chat_id=method.chat_id, text=method.text, parse_mode=method.parse_mode,
                                     entities=method.entities, reply_markup=method.reply_markup,
                                     link_preview_options=method.link_preview_options))
