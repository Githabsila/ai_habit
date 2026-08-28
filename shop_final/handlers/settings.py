from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from db import (
    get_settings,
    update_reminder_time,
    reset_progress,
    connect,
    get_ai_style,
    update_ai_style,
)

from keyboards import ai_style_keyboard, settings_keyboard, back_menu_keyboard





router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class SettingsState(StatesGroup):
    waiting_time = State()


# =====================================
# НАСТРОЙКИ
# =====================================

@router.callback_query(F.data == "settings")
async def settings(callback: CallbackQuery):

    settings_data = get_settings(callback.from_user.id)

    reminders = (
        "🟢 Включены"
        if settings_data["reminders"]
        else "🔴 Выключены"
    )

    hour = settings_data["reminder_hour"]
    minute = settings_data["reminder_minute"]

    await callback.message.edit_text(
        f"""
⚙️ <b>Настройки</b>

🔔 Напоминания:
{reminders}

🕒 Время:
{hour:02}:{minute:02}
""",
        parse_mode="HTML",
        reply_markup=settings_keyboard()
    )

    await callback.answer()


# =====================================
# ВКЛ / ВЫКЛ НАПОМИНАНИЙ
# =====================================

@router.callback_query(F.data == "toggle_reminders")
async def toggle(callback: CallbackQuery):

    conn = connect()
    cursor = conn.cursor()

    settings_data = get_settings(callback.from_user.id)

    new_value = 0 if settings_data["reminders"] else 1

    cursor.execute(
        """
        UPDATE settings
        SET reminders = ?
        WHERE user_id = ?
        """,
        (
            new_value,
            callback.from_user.id
        )
    )

    conn.commit()
    conn.close()

    await callback.answer("✅ Настройки сохранены")

    await settings(callback)


# =====================================
# СТИЛЬ AI-НАСТАВНИКА
# =====================================

@router.callback_query(F.data == "ai_style_menu")
async def ai_style_menu(callback: CallbackQuery):

    current = get_ai_style(callback.from_user.id)

    await callback.message.edit_text(
        """
🎭 <b>Стиль AI-наставника</b>

Выбери, как с тобой должен говорить AI-коуч:

🌿 <b>Мягкий</b> — бережно, с поддержкой, без давления
⚖️ <b>Нейтральный</b> — сбалансированный тон (по умолчанию)
🔥 <b>Жёсткий тренер</b> — прямо, требовательно, без сюсюканья
""",
        parse_mode="HTML",
        reply_markup=ai_style_keyboard(current)
    )

    await callback.answer()


@router.callback_query(F.data.in_({"ai_style_soft", "ai_style_neutral", "ai_style_strict"}))
async def ai_style_select(callback: CallbackQuery):

    style = callback.data.removeprefix("ai_style_")
    update_ai_style(callback.from_user.id, style)

    await callback.answer("✅ Стиль сохранён")

    await ai_style_menu(callback)


# =====================================
# ИЗМЕНИТЬ ВРЕМЯ
# =====================================

@router.callback_query(F.data == "change_time")
async def change_time(callback: CallbackQuery, state: FSMContext):

    await state.set_state(SettingsState.waiting_time)

    await callback.message.answer(
        """
🕒 Введите новое время.

Пример:

09:00
18:30
21:45
"""
    )

    await callback.answer()


# =====================================
# СОХРАНЕНИЕ ВРЕМЕНИ
# =====================================

@router.message(SettingsState.waiting_time)
async def save_time(message: Message, state: FSMContext):

    try:
        hour, minute = map(int, message.text.split(":"))

        if hour not in range(24):
            raise ValueError

        if minute not in range(60):
            raise ValueError

    except ValueError:

        await message.answer(
            "❌ Неверный формат.\n\nПример: 09:30"
        )
        return

    update_reminder_time(
        message.from_user.id,
        hour,
        minute
    )

    await state.clear()

    await message.answer(
        f"""
✅ Время сохранено

🕒 Новое время:

{hour:02}:{minute:02}
""",
        reply_markup=back_menu_keyboard()
    )


# =====================================
# СБРОС ПРОГРЕССА
# =====================================

@router.callback_query(F.data == "reset_progress")
async def reset(callback: CallbackQuery):

    reset_progress(callback.from_user.id)

    await callback.answer(
        "✅ Прогресс успешно сброшен.",
        show_alert=True
    )


# ==============================