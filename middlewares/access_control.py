from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS
from db import bot_access_allowed, get_subscription_status
from keyboards import subscription_buy_keyboard

# Пром 13: колбэки, которые обязаны проходить даже для заблокированного
# пользователя — иначе он физически не сможет оформить/продлить подписку.
_EXEMPT_CALLBACKS = {"buy_subscription", "back_menu"}


class AccessControlMiddleware(BaseMiddleware):
    """Гейт триал → платная подписка (пром 13).

    Выключен по умолчанию (config.SUBSCRIPTION_GATE_ENABLED=False) и даже
    включённый не трогает пользователей, зарегистрированных до
    SUBSCRIPTION_GATE_CUTOVER — см. db/subscription.py bot_access_allowed.
    Пока это так, `bot_access_allowed()` всегда возвращает True и
    middleware ведёт себя как раньше — прозрачный passthrough.
    """

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        user_id = user.id
        if user_id in ADMIN_IDS:
            return await handler(event, data)

        # Сама оплата и переход к ней должны работать всегда — иначе
        # заблокированный пользователь не сможет купить подписку.
        if isinstance(event, Message) and event.successful_payment:
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data in _EXEMPT_CALLBACKS:
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith("/start"):
            return await handler(event, data)

        if bot_access_allowed(user_id):
            return await handler(event, data)

        status = get_subscription_status(user_id)
        text = (
            "⏳ <b>Пробный период закончился.</b>\n\n"
            f"Чтобы продолжить пользоваться ботом и мини-приложением, оформи доступ "
            f"за <b>{status['price_stars']} ⭐</b> (Telegram Stars)."
        )
        keyboard = subscription_buy_keyboard(status["price_stars"])
        try:
            if isinstance(event, CallbackQuery):
                await event.answer()
                await event.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
            else:
                await event.answer(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            pass
        return None
