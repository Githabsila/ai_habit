from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_achievements
from keyboards import achievements_keyboard

router = Router()


# =====================================
# ДОСТИЖЕНИЯ
# =====================================

@router.callback_query(F.data == "achievements")
async def achievements(callback: CallbackQuery):

    achievements = get_achievements(
        callback.from_user.id
    )

    if not achievements:

        await callback.message.edit_text(
            """
🏅 <b>Достижения</b>

У вас пока нет достижений.

Начните выполнять привычки,
получайте Adam Coin и открывайте новые награды! 🚀
""",
            parse_mode="HTML",
            reply_markup=achievements_keyboard()
        )

        await callback.answer()
        return

    text = "🏅 <b>Ваши достижения</b>\n\n"

    for achievement in achievements:
        text += f"🏆 {achievement['title']}\n"

    text += f"\n\nВсего достижений: <b>{len(achievements)}</b>"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=achievements_keyboard()
    )

    await callback.answer()