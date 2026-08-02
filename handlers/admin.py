from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from keyboards import admin_keyboard

from db import (
    get_users_count,
    get_all_users,
    get_all_users_info,
    give_premium_admin,
    give_xp_admin,
    ban_user,
    unban_user
)

router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class AdminState(StatesGroup):
    broadcast = State()
    premium = State()
    xp = State()
    ban = State()
    unban = State()


# =====================================
# АДМИН-ПАНЕЛЬ
# =====================================

@router.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    users = get_users_count()

    await callback.message.edit_text(
        f"""
👑 <b>Админ-панель</b>

👥 Пользователей: <b>{users}</b>

Выберите действие:
""",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =====================================
# РАЗБАНИТЬ ПОЛЬЗОВАТЕЛЯ
# =====================================

@router.callback_query(F.data == "admin_unban")
async def start_unban(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.unban)
    await callback.message.answer("✅ Введите Telegram ID пользователя:")
    await callback.answer()


@router.message(AdminState.unban)
async def do_unban(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Нужно ввести Telegram ID.")
        return

    unban_user(user_id)
    await state.clear()

    await message.answer(
        f"""
✅ Пользователь разблокирован.

👤 ID:
<code>{user_id}</code>
""",
        parse_mode="HTML"
    )


# =====================================
# ЗАБАНИТЬ ПОЛЬЗОВАТЕЛЯ
# =====================================

@router.callback_query(F.data == "admin_ban")
async def start_ban(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.ban)
    await callback.message.answer("🚫 Введите Telegram ID пользователя:")
    await callback.answer()


@router.message(AdminState.ban)
async def ban(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Нужно ввести Telegram ID.")
        return

    ban_user(user_id)
    await state.clear()

    await message.answer(
        f"""
🚫 Пользователь заблокирован.

👤 ID:
<code>{user_id}</code>
""",
        parse_mode="HTML"
    )


# =====================================
# РАССЫЛКА
# =====================================

@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.broadcast)
    await callback.message.answer("📢 Отправьте сообщение для рассылки всем пользователям.")
    await callback.answer()


@router.message(AdminState.broadcast)
async def send_broadcast(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    users = get_all_users()

    success = 0
    failed = 0

    for user in users:
        try:
            await message.bot.send_message(
                chat_id=user["telegram_id"],
                text=message.text,
                parse_mode="HTML"
            )
            success += 1
        except Exception:
            failed += 1

    await state.clear()

    await message.answer(
        f"""
✅ Рассылка завершена!

👥 Отправлено: {success}

❌ Ошибок: {failed}
"""
    )


# =====================================
# ВЫДАТЬ PREMIUM
# =====================================

@router.callback_query(F.data == "admin_premium")
async def premium_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.premium)
    await callback.message.answer("💎 Введите Telegram ID пользователя:")
    await callback.answer()


@router.message(AdminState.premium)
async def premium_user(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Нужно ввести числовой Telegram ID.")
        return

    give_premium_admin(user_id)
    await state.clear()

    await message.answer(
        f"""
✅ Premium успешно выдан!

👤 Пользователь:
<code>{user_id}</code>
""",
        parse_mode="HTML"
    )


# =====================================
# ВЫДАТЬ Adam Coin
# =====================================

@router.callback_query(F.data == "admin_xp")
async def xp_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.xp)
    await callback.message.answer("⭐ Введите:\n\nTelegram_ID Adam Coin\n\nПример:\n123456789 500")
    await callback.answer()


@router.message(AdminState.xp)
async def give_xp(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        user_id, xp = message.text.split()
        user_id = int(user_id)
        xp = int(xp)
    except ValueError:
        await message.answer("❌ Неверный формат.\n\nПример:\n123456789 500")
        return

    give_xp_admin(user_id, xp)
    await state.clear()

    await message.answer(
        f"""
✅ Adam Coin успешно выдан!

👤 Пользователь:
<code>{user_id}</code>

⭐ Начислено:
<b>{xp} Adam Coin</b>
""",
        parse_mode="HTML"
    )


# =====================================
# СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# =====================================

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        return

    users = get_all_users_info()

    if not users:
        await callback.message.answer("Пользователей нет.")
        await callback.answer()
        return

    text = "<b>👥 Пользователи</b>\n\n"

    for user in users:

        premium = "✅" if user["premium"] else "❌"
        banned = "🚫" if user["banned"] else "✅"

        text += (
            f"🆔 <code>{user['telegram_id']}</code>\n"
            f"👤 @{user['username'] or '-'}\n"
            f"⭐ Уровень: {user['level']}\n"
            f"✨ Adam Coin: {user['xp']}\n"
            f"🔥 Серия: {user['streak']}\n"
            f"✅ Выполнено: {user['total_completed']}\n"
            f"💎 Premium: {premium}\n"
            f"🚫 Бан: {banned}\n"
            "────────────────\n"
        )

    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await callback.message.answer(text[i:i + 4000], parse_mode="HTML")
    else:
        await callback.message.answer(text, parse_mode="HTML")

    await callback.answer()


# =====================================
# СТАТИСТИКА (единственный обработчик)
# =====================================

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        return

    users = get_all_users_info()
    total = len(users)

    premium = sum(1 for u in users if u["premium"])
    banned = sum(1 for u in users if u["banned"])
    total_xp = sum(u["xp"] for u in users)
    total_level = sum(u["level"] for u in users)
    avg_level = round(total_level / total, 2) if total else 0

    await callback.message.answer(
        f"""
📊 <b>Статистика бота</b>

👥 Пользователей: <b>{total}</b>

💎 Premium: <b>{premium}</b>

🚫 Заблокировано: <b>{banned}</b>

⭐ Всего Adam Coin: <b>{total_xp}</b>

🏅 Средний уровень: <b>{avg_level}</b>
""",
        parse_mode="HTML"
    )

    await callback.answer()