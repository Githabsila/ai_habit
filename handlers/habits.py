from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from db import (
    add_habit,
    edit_habit,
    delete_habit,
    get_habit,
    get_habits,
    complete_habit,
    update_daily_task,
    get_user
)

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

    add_habit(
        message.from_user.id,
        title
    )

    if gcal_is_connected(message.from_user.id):
        sync_habit_reminder(message.from_user.id)

    await state.clear()

    await message.answer(
        f"""
✅ Привычка успешно добавлена!

🎯 <b>{title}</b>
""",
        parse_mode="HTML",
        reply_markup=habits_keyboard()
    )


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

    if gcal_is_connected(message.from_user.id):
        sync_habit_reminder(message.from_user.id)

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

🔥 Серия: {user['streak']} дней
"""

    await callback.message.edit_text(
        text,
        parse_mode="HTML"
    )

    await callback.answer(
        "🔥 Отличная работа!"
    )

    # =====================================
# УДАЛИТЬ ПРИВЫЧКУ
# =====================================

@router.callback_query(F.data.startswith("delete_"))
async def delete(callback: CallbackQuery):

    habit_id = int(callback.data.split("_")[1])

    habit = get_habit(habit_id)

    delete_habit(habit_id)

    if habit and gcal_is_connected(habit["user_id"]):
        sync_habit_reminder(habit["user_id"])

    await callback.message.edit_text(
        "🗑 Привычка удалена!"
    )

    await callback.answer(
        "Удалено ✅"
    )