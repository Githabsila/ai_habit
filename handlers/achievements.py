from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import check_achievements, get_achievements
from keyboards import achievements_keyboard

router = Router()


ACHIEVEMENT_COPY = {
    "Первый шаг": ("Первый шаг", "Первая привычка выполнена. Начало положено."),
    "Целеустремлённый": ("Держишь курс", "10 привычек выполнены. Ты уже создаёшь систему."),
    "Мастер привычек": ("Мастер привычек", "50 привычек выполнены. Дисциплина становится твоей силой."),
    "Серия 3 дня": ("Три дня в ударе", "3 дня подряд без сбоя. Ритм набран."),
    "Серия 7 дней": ("Неделя в ударе", "7 дней подряд. Ты закрепил сильный ритм."),
    "Опытный": ("Первые 100", "100 Adam Coin заработаны. Твой прогресс уже заметен."),
}


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
            "Здесь будут появляться твои сильные результаты. "
            "Выполняй привычки — и открывай новые награды.",
            parse_mode="HTML",
            reply_markup=achievements_keyboard()
        )

        await callback.answer()
        return

    text = f"🏆 <b>Твои достижения</b> · {len(items)}\n\n"

    for item in items:
        title, description = ACHIEVEMENT_COPY.get(
            item["title"], (item["title"], item["description"])
        )
        text += f"🏅 <b>{title}</b>\n{description}\n\n"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=achievements_keyboard()
    )

    await callback.answer()
