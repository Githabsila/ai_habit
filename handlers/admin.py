from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_IDS
from keyboards import admin_keyboard, pending_keyboard, back_menu_keyboard, broadcast_target_keyboard, tag_search_results_keyboard

from db import (
    get_users_count,
    get_all_users,
    get_all_users_info,
    give_premium_admin,
    give_xp_admin,
    ban_user,
    unban_user,
    get_pending_users,
    set_access_status,
    get_survey,
    get_survey_tags,
    get_ai_history,
    get_achievements,
    get_progress,
    search_users_by_tag,
    get_users_by_tags,
)

from handlers.onboarding import notify_approved

router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class AdminState(StatesGroup):
    broadcast = State()
    broadcast_tag_input = State()
    premium = State()
    xp = State()
    ban = State()
    unban = State()
    user_card = State()
    tag_search = State()


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

    await callback.message.answer(
        "📢 Кому отправить рассылку?",
        reply_markup=broadcast_target_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "broadcast_all")
async def broadcast_all(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.broadcast)
    await state.update_data(tag=None)
    await callback.message.answer("📢 Отправьте сообщение для рассылки всем пользователям.")
    await callback.answer()


@router.callback_query(F.data == "broadcast_tag")
async def broadcast_tag_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminState.broadcast_tag_input)
    await callback.message.answer("🏷 Введите тег (например: бизнес).")
    await callback.answer()


@router.message(AdminState.broadcast_tag_input)
async def broadcast_tag_input(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    tag = (message.text or "").strip()
    matching = get_users_by_tags([tag])

    if not matching:
        await message.answer(f"❌ По тегу «{tag}» никого не нашлось. Введите другой тег.")
        return

    await state.set_state(AdminState.broadcast)
    await state.update_data(tag=tag)
    await message.answer(
        f"🏷 Найдено {len(matching)} пользователей с тегом «{tag}».\n\n"
        "📢 Отправьте сообщение для рассылки только им."
    )


@router.message(AdminState.broadcast)
async def send_broadcast(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    tag = data.get("tag")

    if tag:
        telegram_ids = get_users_by_tags([tag])
    else:
        telegram_ids = [u["telegram_id"] for u in get_all_users()]

    success = 0
    failed = 0

    for telegram_id in telegram_ids:
        try:
            await message.bot.send_message(
                chat_id=telegram_id,
                text=message.text,
                parse_mode="HTML"
            )
            success += 1
        except Exception:
            failed += 1

    await state.clear()

    target_line = f"по тегу «{tag}»" if tag else "всем пользователям"
    await message.answer(
        f"""
✅ Рассылка завершена ({target_line})!

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

    # Общий текст со scheduler'ом ежедневной автосводки — одна и та же
    # логика для кнопки "по запросу" и для проактивной рассылки, см.
    # admin_digest_scheduler.py.
    from admin_digest_scheduler import build_stats_report

    await callback.message.answer(build_stats_report(), parse_mode="HTML")
    await callback.answer()


# =====================================
# ЗАЯВКИ НА ДОСТУП (pending) — "Project ADAM"
# =====================================

@router.callback_query(F.data == "admin_pending")
async def admin_pending(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    users = get_pending_users()

    if not users:
        await callback.message.edit_text(
            "🕓 <b>Заявки на доступ</b>\n\nСейчас заявок на рассмотрении нет.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        await callback.answer()
        return

    text = "🕓 <b>Заявки на доступ</b>\n\nНажмите на пользователя, чтобы одобрить:\n\n"

    for user in users:
        tags = get_survey_tags(user["telegram_id"])
        tags_line = ", ".join(tags) if tags else "—"
        text += (
            f"🆔 <code>{user['telegram_id']}</code> (@{user['username'] or '-'})\n"
            f"🏷 Теги: {tags_line}\n"
            "────────────────\n"
        )

    try:
        await callback.message.edit_text(
            text[:4000],
            parse_mode="HTML",
            reply_markup=pending_keyboard(users)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await callback.answer()


@router.callback_query(F.data.startswith("admin_approve_"))
async def admin_approve(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.removeprefix("admin_approve_"))

    set_access_status(user_id, "approved")
    await notify_approved(callback.bot, user_id)

    await callback.answer(f"✅ Доступ открыт для {user_id}", show_alert=True)

    # Обновляем список заявок (одобренный уже не должен в нём числиться)
    users = get_pending_users()
    if not users:
        await callback.message.edit_text(
            "🕓 <b>Заявки на доступ</b>\n\nСейчас заявок на рассмотрении нет.",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        return

    text = "🕓 <b>Заявки на доступ</b>\n\nНажмите на пользователя, чтобы одобрить:\n\n"
    for user in users:
        tags = get_survey_tags(user["telegram_id"])
        tags_line = ", ".join(tags) if tags else "—"
        text += (
            f"🆔 <code>{user['telegram_id']}</code> (@{user['username'] or '-'})\n"
            f"🏷 Теги: {tags_line}\n"
            "────────────────\n"
        )

    try:
        await callback.message.edit_text(
            text[:4000],
            parse_mode="HTML",
            reply_markup=pending_keyboard(users)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# =====================================
# КАРТОЧКА ПОЛЬЗОВАТЕЛЯ (профиль + анкета + статистика)
# =====================================

@router.callback_query(F.data == "admin_user_card")
async def user_card_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminState.user_card)
    await callback.message.answer("🔎 Введите Telegram ID пользователя:")
    await callback.answer()


@router.message(AdminState.user_card)
async def user_card_show(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        user_id = int(message.text)
    except ValueError:
        await message.answer("❌ Нужно ввести Telegram ID.")
        return

    await state.clear()
    await _send_user_card(message, user_id)


@router.callback_query(F.data.startswith("admin_card_"))
async def user_card_from_button(callback: CallbackQuery):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    user_id = int(callback.data.removeprefix("admin_card_"))
    await _send_user_card(callback.message, user_id)
    await callback.answer()


async def _send_user_card(message: Message, user_id: int):

    users = {u["telegram_id"]: u for u in get_all_users_info()}
    user = users.get(user_id)

    if not user:
        await message.answer("❌ Пользователь с таким ID не найден.")
        return

    premium = "✅" if user["premium"] else "❌"
    banned = "🚫" if user["banned"] else "✅"

    text = (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 <code>{user['telegram_id']}</code>\n"
        f"👤 @{user['username'] or '-'} ({user['first_name'] or '-'})\n"
        f"⭐ Уровень: {user['level']} · Adam Coin: {user['xp']}\n"
        f"🔥 Серия: {user['streak']}\n"
        f"✅ Выполнено привычек: {user['total_completed']}\n"
        f"💎 Premium: {premium} · 🚫 Бан: {banned}\n"
        f"📅 Регистрация: {user['created_at']}\n"
    )

    # Анкета + AI-разбор
    survey = get_survey(user_id)
    if survey:
        tags = get_survey_tags(user_id)
        text += (
            "\n📋 <b>Анкета</b>\n"
            f"💼 Дело: {survey['business'] or '-'}\n"
            f"🎨 Увлечения: {survey['hobbies'] or '-'}\n"
            f"🎯 Цель в жизни: {survey['life_goal'] or '-'}\n"
            f"🤖 Цель в боте: {survey['bot_goal'] or '-'}\n"
        )
        if tags:
            text += f"🏷 Теги (AI): {', '.join(tags)}\n"
        if survey["ai_summary"]:
            text += f"🧠 AI-резюме: {survey['ai_summary']}\n"
    else:
        text += "\n📋 Анкету ещё не заполнял.\n"

    # Прогресс по привычкам сегодня
    progress = get_progress(user_id)
    if progress and progress["total"]:
        text += f"\n📈 Сегодня выполнено: {progress['completed']}/{progress['total']} привычек\n"

    # Достижения
    achievements = get_achievements(user_id)
    if achievements:
        text += f"\n🏆 Достижения ({len(achievements)}): " + ", ".join(
            a["title"] for a in achievements[:5]
        ) + "\n"

    # Последние сообщения AI-наставнику
    history = get_ai_history(user_id, limit=6)
    if history:
        text += "\n💬 <b>Последние сообщения AI-наставнику</b>\n"
        for row in history[-6:]:
            role = "🧑" if row["role"] == "user" else "🤖"
            snippet = row["message"][:120]
            text += f"{role} {snippet}\n"

    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await message.answer(text[i:i + 4000], parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=back_menu_keyboard())


# =====================================
# ПОИСК ПО ТЕГУ (сегментация пользователей)
# =====================================

@router.callback_query(F.data == "admin_tag_search")
async def tag_search_start(callback: CallbackQuery, state: FSMContext):

    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    await state.set_state(AdminState.tag_search)
    await callback.message.answer("🏷 Введите тег для поиска (например: бизнес, спорт, здоровье).")
    await callback.answer()


@router.message(AdminState.tag_search)
async def tag_search_run(message: Message, state: FSMContext):

    if message.from_user.id not in ADMIN_IDS:
        return

    tag = (message.text or "").strip()
    await state.clear()

    users = search_users_by_tag(tag)

    if not users:
        await message.answer(f"❌ По тегу «{tag}» никого не нашлось.")
        return

    text = f"🏷 <b>По тегу «{tag}»</b> — найдено {len(users)}:\n\n"
    for user in users:
        text += f"🆔 <code>{user['telegram_id']}</code> (@{user['username'] or '-'})\n"

    await message.answer(
        text[:4000],
        parse_mode="HTML",
        reply_markup=tag_search_results_keyboard(users)
    )