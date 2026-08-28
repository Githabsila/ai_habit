from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import check_achievements, get_achievements
from keyboards import achievements_keyboard

router = Router()


# =====================================
# ДОСТИЖЕНИЯ
# =====================================

@router.callback_query(F.data == "achievements")
async def show_achievements(callback: CallbackQuery):

    user_id = callback.from_user.id

    # Пересчитываем — вдруг разблокировалось что-то новое с прошлого визита
    check_achievements(user_id)

    items = get_achievements(user_id)

    if not items:

        await callback.message.edit_text(
            "🏆 <b>Достижения</b>\n\n"
            "Пока пусто — выполняйте привычки и заходите почаще, "
            "здесь появятся первые награды.",
            parse_mode="HTML",
            reply_markup=achievements_keyboard()
        )

        await callback.answer()
        return

    text = f"🏆 <b>Достижения</b> ({len(items)})\n\n"

    for item in items:
        text += f"▫️ <b>{item['title']}</b>\n{item['description']}\n\n"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=achievements_keyboard()
    )

    await callback.answer()
