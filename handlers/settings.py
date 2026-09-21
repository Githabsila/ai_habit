from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    get_settings,
    toggle_reminders,
    toggle_reminder_category,
    REMINDER_CATEGORY_LABELS,
)

from keyboards import reminders_keyboard


router = Router()


# =====================================
# УМНЫЕ НАПОМИНАНИЯ
# =====================================
# Единственный раздел, оставшийся в панели бота (см. keyboards.main_menu):
# вкл/выкл + гранулярные категории. Та же логика теперь есть и в Mini App
# (Profile -> Настройки, /api/settings/reminders/... в webapp/webapp_server.py) —
# бот оставлен как запасной вариант. Стиль AI-наставника и сброс прогресса
# уже только в Mini App. "Изменить время" убрано отдельно: reminder_hour/
# reminder_minute сохранялись, но ни один job-планировщик их не читал —
# все напоминания идут по фиксированным cron-часам в main.py, так что
# кнопка ничего реально не меняла.

@router.callback_query(F.data == "reminders_menu")
async def reminders_menu(callback: CallbackQuery):

    settings_data = get_settings(callback.from_user.id)

    reminders = (
        "🟢 Включены"
        if settings_data["reminders"]
        else "🔴 Выключены"
    )

    await callback.message.edit_text(
        f"""
🔔 <b>Умные напоминания</b>

Общий статус:
{reminders}

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
