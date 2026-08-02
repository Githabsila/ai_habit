from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_progress,
    get_statistics
)

from keyboards import progress_keyboard

router = Router()


# =====================================
# ПРОГРЕСС
# =====================================

@router.callback_query(F.data == "progress")
async def progress(callback: CallbackQuery):

    progress = get_progress(callback.from_user.id)
    stats = get_statistics(callback.from_user.id)

    percent = 0

    if progress["total"] > 0:
        percent = round(
            progress["completed"] /
            progress["total"] * 100
        )

    text = f"""
📊 <b>Ваш прогресс</b>

⭐ Adam Coin:
{progress["xp"]}

🏆 Уровень:
{progress["level"]}

🔥 Серия:
{progress["streak"]} дней

🎯 Выполнено привычек:
{progress["completed"]}/{progress["total"]}

📈 Прогресс:
{percent}%
"""

    if stats:

        text += "\n📅 <b>Последние 30 дней</b>\n\n"

        total_completed = sum(
            row["completed"]
            for row in stats
        )

        total_xp = sum(
            row["gained_xp"]
            for row in stats
        )

        text += (
            f"✅ Выполнено: {total_completed}\n"
            f"⭐ Получено Adam Coin: {total_xp}\n"
            f"📆 Записей: {len(stats)}"
        )

    else:

        text += "\nПока статистики нет."

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=progress_keyboard()
    )

    await callback.answer()