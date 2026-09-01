from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from db import (
    get_settings,
    update_reminder_time,
    toggle_reminders,
    toggle_reminder_category,
    REMINDER_CATEGORY_LABELS,
)

from keyboards import reminders_keyboard, back_menu_keyboard


router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class SettingsState(StatesGroup):
    waiting_time = State()


# =====================================
# УМНЫЕ НАПОМИНАНИЯ
# =====================================
# Единственный раздел, оставшийся в панели бота (см. keyboards.main_menu):
# вкл/выкл + время. Стиль AI-наставника и сброс прогресса теперь только
# в Mini App (Profile -> Настройки, /api/settings/ai-style и
# /api/settings/reset-progress в webapp/webapp_server.py).

@router.callback_query(F.data == "reminders_menu")
async def reminders_menu(callback: CallbackQuery):

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
🔔 <b>Умные напоминания</b>

Общий статус:
{reminders}

🕒 Время:
{hour:02}:{minute:02}

Ниже — тонкая настройка: можно, например, оставить напоминания по \
привычкам, но отключить только пуши про ударный режим. Общий тумблер \
выше выключает всё разом, независимо от того, что выбрано ниже.
""",
        parse_mode="HTML",
        reply_markup=reminders_keyboard(settings_data)
    )

    await callback.answer()


# =====================================
# ВКЛ / ВЫКЛ НАПОМИНАНИЙ
# =====================================

@router.callback_query(F.data == "toggle_reminders")
async def toggle(callback: CallbackQuery):

    toggle_reminders(callback.from_user.id)

    await callback.answer("✅ Настройки сохранены")

    await reminders_menu(callback)


# =====================================
# ВКЛ / ВЫКЛ ОДНОЙ ИЗ КАТЕГОРИЙ НАПОМИНАНИЙ
# =====================================

@router.callback_query(F.data.startswith("toggle_reminder_category:"))
async def toggle_category(callback: CallbackQuery):

    category = callback.data.split(":", 1)[1]

    try:
        new_value = toggle_reminder_category(callback.from_user.id, category)
    except ValueError:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    label = REMINDER_CATEGORY_LABELS.get(category, category)
    status = "включены" if new_value else "выключены"
    await callback.answer(f"✅ «{label}»: {status}")

    await reminders_menu(callback)


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
