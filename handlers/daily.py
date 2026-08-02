from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_daily_tasks,
    update_daily_task,
    get_user
)

from keyboards import daily_keyboard, back_menu_keyboard

router = Router()


# =====================================
# ЕЖЕДНЕВНЫЕ ЗАДАНИЯ
# =====================================

@router.callback_query(F.data == "daily")
async def daily(callback: CallbackQuery):

    tasks = get_daily_tasks(callback.from_user.id)

    if not tasks:

        await callback.message.edit_text(
            """
📅 Сегодня задания ещё не созданы.

Попробуйте позже.
""",
            reply_markup=back_menu_keyboard()
        )

        await callback.answer()
        return

    text = "📅 <b>Ежедневные задания</b>\n\n"

    for task in tasks:

        status = "✅" if task["completed"] else "❌"

        text += (
            f"{status} <b>{task['task']}</b>\n"
            f"Прогресс: {task['progress']}/{task['goal']}\n"
            f"Награда: ⭐ {task['reward']} Adam Coin\n\n"
        )

    user = get_user(callback.from_user.id)

    text += (
        "━━━━━━━━━━━━━━\n"
        f"⭐ Adam Coin: {user['xp']}\n"
        f"🏆 Уровень: {user['level']}\n"
        f"🔥 Серия: {user['streak']} дней"
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=daily_keyboard()
    )

    await callback.answer()


# =====================================
# ОБНОВИТЬ ЗАДАНИЯ
# =====================================

from aiogram.exceptions import TelegramBadRequest


@router.callback_query(F.data == "refresh_daily")
async def refresh(callback: CallbackQuery):

    print("Нажата кнопка обновить")

    try:
        await daily(callback)
    except TelegramBadRequest:
        await callback.answer(
            "✅ Задания уже актуальны",
            show_alert=False
        )