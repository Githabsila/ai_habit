from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_rating
from keyboards import rating_keyboard

router = Router()


@router.callback_query(F.data == "rating")
async def rating(callback: CallbackQuery):

    users = get_rating()

    if not users:

        await callback.message.edit_text(
            "🏆 Пока рейтинг пуст.",
            reply_markup=rating_keyboard()
        )

        await callback.answer()
        return

    text = "🏆 <b>Рейтинг пользователей</b>\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for i, user in enumerate(users):

        if i < 3:
            place = medals[i]
        else:
            place = f"{i + 1}."

        text += (
            f"{place} "
            f"{user['first_name']} — "
            f"{user['xp']} Adam Coin\n"
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=rating_keyboard()
    )

    await callback.answer()