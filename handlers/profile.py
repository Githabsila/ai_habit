from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import get_user, get_habits
from keyboards import back_menu_keyboard
from handlers.helpers import day_phrase

router = Router()


# ==========================
# ПРОФИЛЬ
# ==========================

@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery):

    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "❌ Профиль не найден. Нажмите /start",
            show_alert=True
        )
        return

    habits = get_habits(callback.from_user.id)

    completed = sum(
        1 for habit in habits
        if habit["completed"]
    )

    premium_text = (
        "⭐ Premium"
        if user["premium"]
        else "🆓 Free"
    )

    username_text = (
        f"@{user['username']}"
        if user["username"]
        else "Не указан"
    )

    await callback.message.edit_text(
        f"""
👤 <b>Ваш профиль</b>

📝 Имя:
{user["first_name"]}

👤 Username:
{username_text}

🆔 ID:
<code>{callback.from_user.id}</code>

🎯 Привычек:
{len(habits)}

✅ Выполнено сегодня:
{completed}

🔥 Серия:
{day_phrase(user["streak"])}

⭐ Adam Coin:
{user["xp"]}

🏆 Уровень:
{user["level"]}

💎 Статус:
{premium_text}

📅 Регистрация:
{user["created_at"]}
""",
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()
