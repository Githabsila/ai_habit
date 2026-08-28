from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_rating, get_item_owner_ids
from keyboards import rating_keyboard

router = Router()

BADGE_ITEM_ID = 3  # 🏅 Особый значок — тот же id, что и в webapp/server.py


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

    badge_owner_ids = get_item_owner_ids(BADGE_ITEM_ID)

    text = "🏆 <b>Рейтинг пользователей</b>\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for i, user in enumerate(users):

        if i < 3:
            place = medals[i]
        else:
            place = f"{i + 1}."

        badge = " 🏅" if user["telegram_id"] in badge_owner_ids else ""

        text += (
            f"{place} "
            f"{user['first_name']}{badge} — "
            f"{user['xp']} Adam Coin\n"
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=rating_keyboard()
    )

    await callback.answer()