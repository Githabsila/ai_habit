from aiogram import Router, F
from aiogram.types import CallbackQuery

from db import find_match_by_tags, get_referrals
from keyboards import community_keyboard, back_menu_keyboard

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
