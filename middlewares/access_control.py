from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import ADMIN_IDS
from db import get_user


PENDING_TEXT = (
    "🕓 Ваша анкета ещё на проверке модератором — доступ откроется, "
    "как только заявку одобрят."
)
NOT_STARTED_TEXT = "🔒 Сначала нужно ответить на анкету — нажмите /start."
NOT_REGISTERED_TEXT = "Нажмите /start, чтобы начать."


class AccessControlMiddleware(BaseMiddleware):
    """
    Закрытый доступ ("Project ADAM"): пока access_status пользователя не
    'approved', любые сообщения/кнопки кроме /start и самой анкеты (FSM
    состояния Onboarding.*) блокируются здесь — централизованно, а не в
    каждом хендлере отдельно. Админы (ADMIN_IDS) всегда проходят.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:

        user = event.from_user
        if user is None:
            return await handler(event, data)

        if user.id in ADMIN_IDS:
            return await handler(event, data)

        # /start сам разруливает статус (анкета / ожидание / меню)
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        # Пока идёт анкета — её собственные хендлеры должны получать сообщения
        state = data.get("state")
        current_state = await state.get_state() if state else None
        if current_state and current_state.startswith("Onboarding:"):
            return await handler(event, data)

        db_user = get_user(user.id)

        if db_user is None:
            await self._reject(event, NOT_REGISTERED_TEXT)
            return

        status = db_user["access_status"] or "approved"

        if status == "approved":
            return await handler(event, data)

        text = PENDING_TEXT if status == "pending" else NOT_STARTED_TEXT
        await self._reject(event, text)
        return

    @staticmethod
    async def _reject(event: TelegramObject, text: str):
        if isinstance(event, Message):
            await event.answer(text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
