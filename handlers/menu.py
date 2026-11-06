from aiogram import Router, F
from aiogram.types import CallbackQuery
from config import ADMIN_IDS
from keyboards import (
    habits_keyboard,
    welcome_menu,
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

Выбери действие:
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
        # welcome_menu, а не main_menu: иначе в меню только «Умные напоминания» и в приложение отсюда не попасть.
        reply_markup=welcome_menu(is_admin=callback.from_user.id in ADMIN_IDS)
    )

    await callback.answer()
