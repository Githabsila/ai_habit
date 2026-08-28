from aiogram import Router, F
from aiogram.types import CallbackQuery
from keyboards import (
    habits_keyboard,
    main_menu,
)

router = Router()


# ==========================
# ПРИВЫЧКИ
# ==========================

@router.callback_query(F.data == "habits")
async def habits(callback: CallbackQuery):

    await callback.message.edit_text(
        """
🎯 Управление привычками

Выберите действие:
""",
        reply_markup=habits_keyboard()
    )

    await callback.answer()


# ==========================
# В ГЛАВНОЕ МЕНЮ
# ==========================

@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await callback.answer()
