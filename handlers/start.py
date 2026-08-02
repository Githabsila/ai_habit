from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from config import ADMIN_ID
from keyboards import main_menu

from db import (
    add_user,
    get_user,
    set_referrer,
    add_referral,
    add_xp
)

router = Router()


@router.message(CommandStart())
async def start(message: Message):

    # Регистрируем пользователя
    add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    # Проверяем реферальную ссылку
    args = message.text.split()

    if len(args) > 1:

        try:
            referrer_id = int(args[1])

            # Нельзя пригласить самого себя
            if referrer_id != message.from_user.id:

                user = get_user(message.from_user.id)

                # Если пригласивший ещё не записан
                if user["referrer_id"] is None:

                    set_referrer(
                        message.from_user.id,
                        referrer_id
                    )

                    # Засчитываем приглашение
                    add_referral(referrer_id)

                    # Начисляем бонус пригласившему
                    add_xp(referrer_id, 100)

        except ValueError:
            pass

    # Проверяем бан ДО отправки меню
    user = get_user(message.from_user.id)

    if user and user["banned"] == 1:
        await message.answer(
            "🚫 Ваш аккаунт заблокирован администрацией."
        )
        return

    # Приветственное сообщение
    await message.answer(
        """
👋 Добро пожаловать!

Это Project ADAM.

Вместе мы будем:

- 🎯 Развивать привычки
- 🤖 Общаться с ИИ
- 📈 Отслеживать прогресс
- 👥 Искать единомышленников

Выберите раздел 👇
        """,
        reply_markup=main_menu()
    )

    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👑 Админ-панель",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="👑 Открыть админку",
                            callback_data="admin"
                        )
                    ]
                ]
            )
        )