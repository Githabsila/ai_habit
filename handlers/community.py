import json

from aiogram import Router, F
from aiogram.types import CallbackQuery
from db import get_referrals, find_match_by_tags, get_survey_tags
from keyboards import (
    community_keyboard,
    back_menu_keyboard,
)

router = Router()


# =====================================
# СООБЩЕСТВО
# =====================================

@router.callback_query(F.data == "community")
async def community(callback: CallbackQuery):

    await callback.message.edit_text(
        """
👥 <b>Сообщество</b>

Добро пожаловать!

Здесь вы сможете:

🌍 Общаться с другими пользователями

🔎 Находить единомышленников по интересам

🤝 Приглашать друзей

🎁 Получать бонусы за приглашения
""",
        parse_mode="HTML",
        reply_markup=community_keyboard()
    )

    await callback.answer()


# =====================================
# ОБЩИЙ ЧАТ
# =====================================

@router.callback_query(F.data == "community_chat")
async def community_chat(callback: CallbackQuery):

    await callback.answer(
        "🚧 Общий чат скоро появится.",
        show_alert=True
    )


# =====================================
# НАЙТИ ЕДИНОМЫШЛЕННИКА (мэтчинг по тегам анкеты)
# =====================================

@router.callback_query(F.data == "find_match")
async def find_match(callback: CallbackQuery):

    user_id = callback.from_user.id
    my_tags = get_survey_tags(user_id)

    if not my_tags:
        await callback.answer(
            "Сначала нужно пройти анкету — по ней подбираются совпадения по интересам.",
            show_alert=True
        )
        return

    matches = find_match_by_tags(user_id, limit=1)

    if not matches:
        await callback.message.answer(
            "🔎 Пока не нашлось никого с похожими интересами — попробуйте позже, "
            "аудитория растёт.",
            reply_markup=back_menu_keyboard()
        )
        await callback.answer()
        return

    match = matches[0]
    their_tags = []
    try:
        their_tags = json.loads(match["ai_tags"]) if match["ai_tags"] else []
    except (TypeError, ValueError):
        pass

    shared = ", ".join(set(my_tags) & set(their_tags)) or "общие интересы"
    username_line = f"@{match['username']}" if match["username"] else "username закрыт — совпадение чисто по интересам"

    await callback.message.answer(
        f"🔎 <b>Похожий человек нашёлся!</b>\n\n"
        f"👤 {username_line}\n"
        f"🏷 Общее: {shared}\n"
        + (f"🧠 {match['ai_summary']}\n" if match["ai_summary"] else ""),
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()


# =====================================
# ПРИГЛАСИТЬ ДРУГА
# =====================================

@router.callback_query(F.data == "invite_friend")
async def invite_friend(callback: CallbackQuery):

    # Без символа @
    bot_username = "aihabit_bot"

    link = f"https://t.me/{bot_username}?start={callback.from_user.id}"

    await callback.message.answer(
    f"""
🤝 <b>Пригласи друга</b>

Отправь другу эту ссылку:

<code>{link}</code>
""",
    parse_mode="HTML",
    reply_markup=back_menu_keyboard()
)

    await callback.answer()


# =====================================
# МОИ ПРИГЛАШЕНИЯ
# =====================================

@router.callback_query(F.data == "my_referrals")
async def my_referrals(callback: CallbackQuery):

    count = get_referrals(callback.from_user.id)

    await callback.message.answer(
        f"""
👥 <b>Мои приглашения</b>

Вы пригласили:

<b>{count}</b> человек(а)

🎁 За каждого друга вы получаете <b>100 Adam Coin</b>.
""",
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )

    await callback.answer()