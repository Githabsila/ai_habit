from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import (
    find_match_by_tags, get_referrals, get_referred_users,
    create_challenge, get_active_challenge_for_user, get_challenge_progress,
    get_user,
)
from keyboards import community_keyboard, back_menu_keyboard, challenge_partner_keyboard

router = Router()


# =====================================
# СООБЩЕСТВО (главный экран)
# =====================================

@router.callback_query(F.data == "community")
async def show_community(callback: CallbackQuery):

    await callback.message.edit_text(
        "👥 <b>Сообщество</b>\n\n"
        "Общайтесь с другими участниками, находите единомышленников "
        "и приглашайте друзей.",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# ОБЩИЙ ЧАТ
# =====================================

@router.callback_query(F.data == "community_chat")
async def community_chat(callback: CallbackQuery):

    await callback.message.edit_text(
        "🌍 <b>Общий чат</b>\n\n"
        "Ссылка на общий чат пока не настроена администратором.",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# НАЙТИ ЕДИНОМЫШЛЕННИКА
# =====================================

@router.callback_query(F.data == "find_match")
async def find_match(callback: CallbackQuery):

    user_id = callback.from_user.id

    matches = find_match_by_tags(user_id, limit=1)

    if not matches:
        await callback.message.edit_text(
            "🔎 <b>Найти единомышленника</b>\n\n"
            "Пока никого не нашлось с похожими интересами — "
            "попробуйте позже, сообщество растёт каждый день.",
            parse_mode="HTML",
            reply_markup=community_keyboard()
        )
        await callback.answer()
        return

    match = matches[0]
    name = match["username"] and f"@{match['username']}" or match["first_name"]

    await callback.message.edit_text(
        "🔎 <b>Найден единомышленник!</b>\n\n"
        f"👤 {name}\n"
        f"💬 {match['ai_summary'] or 'Общие интересы с вами'}",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# ПРИГЛАСИТЬ ДРУГА
# =====================================

@router.callback_query(F.data == "invite_friend")
async def invite_friend(callback: CallbackQuery):

    bot_user = await callback.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={callback.from_user.id}"

    await callback.message.edit_text(
        "🤝 <b>Пригласить друга</b>\n\n"
        "Отправьте другу вашу персональную ссылку — за каждого "
        "приглашённого вы получите 100 Adam Coin:\n\n"
        f"<code>{link}</code>",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# МОИ ПРИГЛАШЕНИЯ
# =====================================

@router.callback_query(F.data == "my_referrals")
async def my_referrals(callback: CallbackQuery):

    count = get_referrals(callback.from_user.id)

    await callback.message.edit_text(
        "👥 <b>Мои приглашения</b>\n\n"
        f"Вы пригласили: <b>{count}</b> чел.",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# НЕДЕЛЬНЫЙ ЧЕЛЛЕНДЖ С ДРУГОМ
# =====================================
# Следующий шаг поверх уже существующей рефералки: не просто "пригласи
# друга ради бонуса", а совместный челлендж на 7 дней — реальная соц.
# механика вместо голого счётчика приглашений.

def _display_name(row):
    if not row:
        return "друг"
    return f"@{row['username']}" if row["username"] else (row["first_name"] or str(row["telegram_id"]))


@router.callback_query(F.data == "start_challenge")
async def start_challenge(callback: CallbackQuery):
    user_id = callback.from_user.id

    active = get_active_challenge_for_user(user_id)
    if active:
        progress = get_challenge_progress(active)
        i_am_owner = progress["user_id"] == user_id
        partner_id = progress["partner_id"] if i_am_owner else progress["user_id"]
        my_days = progress["user_days"] if i_am_owner else progress["partner_days"]
        their_days = progress["partner_days"] if i_am_owner else progress["user_days"]
        partner_name = _display_name(get_user(partner_id))
        if my_days > their_days:
            verdict = "Ты впереди! 🔥"
        elif their_days > my_days:
            verdict = "Друг впереди — успей подтянуться 💪"
        else:
            verdict = "Идёте вровень 🤝"
        await callback.message.edit_text(
            "🏁 <b>Недельный челлендж</b>\n\n"
            f"С {partner_name} — день {progress['days_elapsed']}/{progress['total_days']}\n\n"
            f"Ты: <b>{my_days}</b>/{progress['total_days']} активных дней\n"
            f"{partner_name}: <b>{their_days}</b>/{progress['total_days']} активных дней\n\n"
            f"{verdict}",
            parse_mode="HTML",
            reply_markup=community_keyboard(),
        )
        await callback.answer()
        return

    referred = get_referred_users(user_id)
    if not referred:
        await callback.message.edit_text(
            "🏁 <b>Недельный челлендж</b>\n\n"
            "Пока некого позвать — сначала пригласите хотя бы одного друга "
            "(«🤝 Пригласить друга»), а потом сможете начать с ним недельный "
            "челлендж.",
            parse_mode="HTML",
            reply_markup=community_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🏁 <b>Недельный челлендж</b>\n\n"
        "Выберите, с кем из приглашённых друзей начать 7-дневный челлендж — "
        "у кого больше активных дней к концу недели, тот победил:",
        parse_mode="HTML",
        reply_markup=challenge_partner_keyboard(referred),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("challenge_with:"))
async def challenge_with(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = int(callback.data.split(":", 1)[1])

    ok, error = create_challenge(user_id, partner_id)
    if not ok:
        text = (
            "У вас уже есть активный челлендж — посмотрите прогресс в "
            "«🏁 Недельный челлендж»." if error == "already_active"
            else "Что-то пошло не так, попробуйте ещё раз."
        )
        await callback.message.edit_text(text, reply_markup=community_keyboard())
        await callback.answer()
        return

    partner_name = _display_name(get_user(partner_id))
    await callback.message.edit_text(
        f"🏁 Челлендж с {partner_name} начался! 7 дней — у кого больше активных "
        "дней к концу недели, тот победил. Прогресс смотрите здесь же, в "
        "«🏁 Недельный челлендж».",
        reply_markup=community_keyboard(),
    )
    await callback.answer("Челлендж начат!")

    try:
        my_name = _display_name(get_user(user_id))
        await callback.bot.send_message(
            partner_id,
            f"🏁 {my_name} позвал(а) тебя на недельный челлендж в Project ADAM! "
            "Открой «Сообщество» → «🏁 Недельный челлендж», чтобы увидеть прогресс.",
        )
    except Exception:
        pass  # партнёр мог заблокировать бота — не критично
