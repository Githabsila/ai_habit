from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    claim_daily_bonus,
    get_user
)

from keyboards import back_menu_keyboard

router = Router()


# =====================================
# ЕЖЕДНЕВНЫЙ БОНУС
# =====================================

@router.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: CallbackQuery):

    success = claim_daily_bonus(
        callback.from_user.id
    )

    user = get_user(
        callback.from_user.id
    )

    if success:

        text = f"""
🎁 <b>Ежедневный бонус</b>

Поздравляем!

Вы получили

⭐ +20 Adam Coin

━━━━━━━━━━━━━━

⭐ Adam Coin: {user["xp"]}

🏆 Уровень: {user["level"]}
"""

    else:

        text = """
🎁 <b>Ежедневный бонус</b>

Сегодня бонус уже получен.

Возвращайтесь завтра! 😊
"""

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()