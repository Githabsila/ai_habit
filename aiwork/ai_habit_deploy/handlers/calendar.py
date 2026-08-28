from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_calendar
from keyboards import back_menu_keyboard

router = Router()


# =====================================
# КАЛЕНДАРЬ
# =====================================

@router.callback_query(F.data == "calendar")
async def calendar(callback: CallbackQuery):

    calendar_data = get_calendar(callback.from_user.id)

    if not calendar_data:

        await callback.message.edit_text(
            """
📅 <b>Календарь активности</b>

Пока нет выполненных привычек.
""",
            parse_mode="HTML",
            reply_markup=back_menu_keyboard()
        )

        await callback.answer()
        return

    text = "📅 <b>Календарь активности</b>\n\n"

    total = 0

    for day in calendar_data:

        text += (
            f"🗓 {day['day']} — "
            f"✅ {day['completed']} привычек\n"
        )

        total += day["completed"]

    text += f"""

━━━━━━━━━━━━━━

🔥 Всего выполнено: {total}
"""

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()