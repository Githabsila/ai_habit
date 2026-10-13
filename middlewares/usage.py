import re

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from db import usage

_DIGITS = re.compile(r"\d+")


def action_label(event):
    """Что сделал человек в боте — стабильная метка без текста сообщений и без id: cmd:/start, cb:habit_done_#, msg:text, msg:voice.
    Новая команда или кнопка бота появляется в аналитике сама, как только её нажмёт первый человек."""
    if isinstance(event, CallbackQuery):
        return "cb:" + _DIGITS.sub("#", event.data or "")[:34] if event.data else None
    if isinstance(event, Message):
        text = event.text or ""
        if text.startswith("/"):
            return "cmd:" + text.split()[0].split("@")[0][:30]
        if text:
            return "msg:text"
        for kind in ("voice", "photo", "video", "video_note", "document", "sticker", "location", "contact", "successful_payment"):
            if getattr(event, kind, None):
                return "msg:" + kind
        return "msg:other"
    return None


class UsageMiddleware(BaseMiddleware):
    """Считает действия людей в боте для аналитики (db/usage.py → /analytics). Ничего не меняет в обработке и не падает."""

    async def __call__(self, handler, event, data):
        try:
            user = getattr(event, "from_user", None)
            label = action_label(event)
            if user is not None and label:
                usage.bot_action(user.id, label)
        except Exception:
            pass
        return await handler(event, data)
