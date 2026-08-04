from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import create_daily_tasks, get_daily_tasks, claim_daily_bonus
from keyboards import daily_keyboard, back_menu_keyboard

router = Router()


def _render_tasks_text(tasks):

    text = "📋 <b>Ежедневные задания</b>\n\n"

    for task in tasks:
        mark = "✅" if task["completed"] else "▫️"
        text += (
            f"{mark} {task['task']} "
            f"({task['progress']}/{task['goal']}) "
            f"— {task['reward']} Adam Coin\n"
        )

    return text


# =====================================
# ЕЖЕДНЕВНЫЕ ЗАДАНИЯ
# =====================================

@router.callback_query(F.data == "daily")
async def show_daily_tasks(callback: CallbackQuery):

    user_id = callback.from_user.id

    tasks = get_daily_tasks(user_id)

    if not tasks:
        create_daily_tasks(user_id)
        tasks = get_daily_tasks(user_id)

    await callback.message.edit_text(
        _render_tasks_text(tasks),
        parse_mode="HTML",
        reply_markup=daily_keyboard()
    )

    await callback.answer()


@router.callback_query(F.data == "refresh_daily")
async def refresh_daily_tasks(callback: CallbackQuery):

    user_id = callback.from_user.id

    tasks = get_daily_tasks(user_id)

    if not tasks:
        create_daily_tasks(user_id)
        tasks = get_daily_tasks(user_id)

    try:
        await callback.message.edit_text(
            _render_tasks_text(tasks),
            parse_mode="HTML",
            reply_markup=daily_keyboard()
        )
    except Exception:
        # Текст не изменился с прошлого обновления — Telegram ругается,
        # это не ошибка, просто нечего перерисовывать
        pass

    await callback.answer("Обновлено")


# =====================================
# ЕЖЕДНЕВНЫЙ БОНУС
# =====================================

@router.callback_query(F.data == "daily_bonus")
async def daily_bonus(callback: CallbackQuery):

    user_id = callback.from_user.id

    claimed = claim_daily_bonus(user_id)

    if claimed:
        text = "🎁 Бонус получен! +20 Adam Coin.\n\nВозвращайтесь завтра за новым."
    else:
        text = "🎁 Вы уже забирали бонус сегодня.\n\nЗаходите завтра!"

    await callback.message.edit_text(
        text,
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()
