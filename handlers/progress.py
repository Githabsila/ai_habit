import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_progress,
    get_statistics,
    get_weekly_summary,
    get_ai_style,
    cache_get,
    cache_set,
    log_error,
)

from keyboards import progress_keyboard, back_menu_keyboard
from multi_agent import generate_progress_analysis
from handlers.ai import build_user_context
from handlers.helpers import send_long_message

router = Router()
logger = logging.getLogger("handlers.progress")


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


# =====================================
# AI-АНАЛИЗ ПРОГРЕССА (этап 3 AI Coach)
# =====================================

@router.callback_query(F.data == "progress_ai_analysis")
async def progress_ai_analysis(callback: CallbackQuery):

    user_id = callback.from_user.id
    await callback.answer("🤖 Анализирую...")

    cache_key = f"panalysis:{user_id}:{date.today()}"
    text = cache_get(cache_key)

    if text is None:
        weekly = get_weekly_summary(user_id)
        weekly_text = (
            f"Выполнено привычек: {weekly['completed']}, "
            f"активных дней: {weekly['active_days']}/7, "
            f"получено Adam Coin: {weekly['xp']}."
        )
        user_context = build_user_context(user_id)
        style = get_ai_style(user_id)

        try:
            text = await generate_progress_analysis(user_context, weekly_text, style)
        except Exception as e:
            logger.exception(f"Не удалось сформировать AI-анализ прогресса для {user_id}")
            log_error("progress_analysis", e, user_id)
            text = "❌ Не получилось сформировать анализ, попробуйте позже."

        if text and "[ошибка агента" not in text:
            cache_set(cache_key, text)

    await send_long_message(
        callback.message,
        text,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard(),
        header="🤖 <b>AI-анализ прогресса</b>",
    )