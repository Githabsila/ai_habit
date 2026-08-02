from aiogram import Router, F
from aiogram.types import CallbackQuery
from db import get_referrals
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