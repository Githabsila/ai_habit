from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from pathlib import Path

from db import (
    add_habit,
    edit_habit,
    delete_habit,
    get_habits,
    complete_habit,
    update_daily_task,
    get_user,
    consume_completion_event,
    get_streak_status,
    onboarding_message, mark_onboarding_seen,
)

from handlers.helpers import day_phrase

from keyboards import (
    habits_keyboard,
    complete_keyboard
)


router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class HabitState(StatesGroup):
    title = State()
    edit_title = State()


@router.callback_query(F.data == "streak_continue")
async def streak_continue(callback: CallbackQuery):
    """Закрывает праздничное сообщение после осознанного нажатия."""
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await callback.answer("Продолжаем 🚀")


# =====================================
# ДОБАВИТЬ ПРИВЫЧКУ
# =====================================

@router.callback_query(F.data == "add_habit")
async def add_habit_start(callback: CallbackQuery, state: FSMContext):

    await state.set_state(HabitState.title)

    await callback.message.edit_text(
        "✍️ Введите название новой привычки:"
    )

    await callback.answer()


# =====================================
# СОХРАНЕНИЕ ПРИВЫЧКИ
# =====================================

@router.message(HabitState.title)
async def save_habit(message: Message, state: FSMContext):

    title = message.text.strip()

    if len(title) < 2:
        await message.answer("❌ Название слишком короткое.")
        return

    was_first_habit = len(get_habits(message.from_user.id)) == 0

    add_habit(
        message.from_user.id,
        title
    )

    await state.clear()

    await message.answer(
        f"""
✅ Привычка успешно добавлена!

🎯 <b>{title}</b>
""",
        parse_mode="HTML",
        reply_markup=habits_keyboard()
    )

    if was_first_habit:
        await message.answer(
            "🔥 УДАРНЫЙ РЕЖИМ\n\n"
            "Каждый день выполняй хотя бы одну привычку, чтобы не потерять серию. "
            "За рубежи ты получишь уникальные рамки и статусы, которые нельзя купить.\n\n"
            f"🤖 Адам: {onboarding_message(message.from_user.id)}"
        )
        mark_onboarding_seen(message.from_user.id)


# =====================================
# МОИ ПРИВЫЧКИ
# =====================================

@router.callback_query(F.data == "my_habits")
async def my_habits(callback: CallbackQuery):

    habits = get_habits(callback.from_user.id)

    if not habits:

        await callback.message.edit_text(
            """
📭 У вас пока нет привычек.

Нажмите
<b>➕ Добавить привычку</b>
""",
            parse_mode="HTML",
            reply_markup=habits_keyboard()
        )

        await callback.answer()
        return

    await callback.message.delete()

    for habit in habits:

        status = (
            "✅ Выполнено"
            if habit["completed"]
            else "❌ Не выполнено"
        )

        await callback.message.answer(
            f"""
🎯 <b>{habit['title']}</b>

{status}
""",
            parse_mode="HTML",
            reply_markup=complete_keyboard(habit["id"])
        )

    await callback.message.answer(
        "⚙️ Управление привычками",
        reply_markup=habits_keyboard()
    )

    await callback.answer()


# =====================================
# ИЗМЕНИТЬ ПРИВЫЧКУ
# =====================================

@router.callback_query(F.data.startswith("edit_"))
async def edit_habit_start(callback: CallbackQuery, state: FSMContext):

    habit_id = int(callback.data.split("_")[1])

    await state.update_data(habit_id=habit_id)

    await state.set_state(HabitState.edit_title)

    await callback.message.answer(
        "✏️ Введите новое название привычки:"
    )

    await callback.answer()


# =====================================
# СОХРАНИТЬ НОВОЕ НАЗВАНИЕ
# =====================================

@router.message(HabitState.edit_title)
async def save_new_title(message: Message, state: FSMContext):

    data = await state.get_data()

    habit_id = data["habit_id"]

    new_title = message.text.strip()

    if len(new_title) < 2:
        await message.answer(
            "❌ Название слишком короткое."
        )
        return

    edit_habit(
        habit_id,
        new_title
    )

    await state.clear()

    await message.answer(
        f"""
✅ Привычка успешно изменена!

🎯 <b>{new_title}</b>
""",
        parse_mode="HTML",
        reply_markup=habits_keyboard()
    )


# =====================================
# ПРАЗДНИЧНОЕ ОКНО НОВОГО УДАРНОГО ДНЯ
# =====================================

async def send_streak_celebration(message: Message, event: dict, user: dict | None = None):
    """Показывает красивое сообщение о новом ударном дне в Telegram."""
    streak = int(event.get("streak") or (user["streak"] if user else 1) or 1)
    first_name = ((user["first_name"] if user else "") or "").strip()
    name_line = f" {first_name}," if first_name else ""
    phrase = (event.get("message") or "День закрыт. Продолжай в том же духе.").strip()

    caption = (
        "⚡️ <b>НОВЫЙ УДАРНЫЙ ДЕНЬ</b>\n\n"
        f"<b>+1 ДЕНЬ</b> твоей серии\n\n"
        f"{name_line} ты только что сделал ещё один шаг вперёд.\n"
        f"🔥 <b>Серия: {streak} дн.</b>\n\n"
        f"{phrase}\n\n"
        "Не сбавляй темп. Завтра продолжаем."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Продолжить", callback_data="streak_continue")]
    ])
    asset = Path(__file__).resolve().parents[1] / "webapp" / "static" / "assets" / "adam-impact-flame.png"
    if asset.exists():
        await message.answer_photo(
            photo=FSInputFile(asset),
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)


# =====================================
# ВЫПОЛНИТЬ ПРИВЫЧКУ
# =====================================

@router.callback_query(F.data.startswith("complete_"))
async def complete(callback: CallbackQuery):

    habit_id = int(callback.data.split("_")[1])

    print("=" * 40)
    print("Нажали кнопку:", callback.data)
    print("ID привычки:", habit_id)

    success = complete_habit(habit_id)

    print("Результат complete_habit:", success)

    if not success:

        await callback.answer(
            "⚠️ Эта привычка уже выполнена.",
            show_alert=True
        )
        return

    update_daily_task(
        callback.from_user.id,
        "Выполнить привычку"
    )

    user = get_user(callback.from_user.id)

    text = callback.message.text.replace(
        "❌ Не выполнено",
        "✅ Выполнено"
    )

    text += f"""

━━━━━━━━━━━━━━

⭐ +10 Adam Coin

🏆 Уровень: {user['level']}

🔥 Серия: {day_phrase(user['streak'])}
"""

    await callback.message.edit_text(
        text,
        parse_mode="HTML"
    )

    event = consume_completion_event(callback.from_user.id)
    if event:
        try:
            await send_streak_celebration(callback.message, event, user)
        except Exception as exc:
            print("Не удалось показать красивое окно ударного дня:", exc)

    await callback.answer(
        "🔥 Отличная работа!"
    )

    # =====================================
# УДАЛИТЬ ПРИВЫЧКУ
# =====================================

@router.callback_query(F.data.startswith("delete_"))
async def delete(callback: CallbackQuery):

    habit_id = int(callback.data.split("_")[1])

    delete_habit(habit_id)

    await callback.message.edit_text(
        "🗑 Привычка удалена!"
    )

    await callback.answer(
        "Удалено ✅"
    )