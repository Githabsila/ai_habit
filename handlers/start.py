from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config import ADMIN_IDS
from keyboards import main_menu

from db import (
    add_user,
    get_user,
    set_referrer,
    add_referral,
    add_xp,
    get_access_status,
)

from handlers.onboarding import begin_survey

router = Router()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):

    # Регистрируем пользователя (для уже существующих — no-op благодаря
    # INSERT OR IGNORE внутри add_user)
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

    # Админов анкета не касается — сразу в меню
    is_admin = message.from_user.id in ADMIN_IDS
    status = get_access_status(message.from_user.id)

    if not is_admin and status == "new":
        await begin_survey(message, state)
        return

    if not is_admin and status == "pending":
        await message.answer(
            "🕓 Ваша анкета уже на проверке модератором.\n\n"
            "Как только доступ к <b>Project ADAM</b> откроется — мы напишем сразу.",
            parse_mode="HTML"
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
        reply_markup=main_menu(is_admin=message.from_user.id in ADMIN_IDS)
    )