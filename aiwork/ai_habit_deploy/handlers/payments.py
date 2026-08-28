from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
)

from config import PREMIUM_PRICE_STARS
from db import give_premium_admin
from keyboards import premium_buy_keyboard, back_menu_keyboard

router = Router()


# =====================================
# ЧТО ДАЁТ PREMIUM
# =====================================

PREMIUM_PERKS_TEXT = (
    "💎 <b>Project ADAM Premium</b>\n\n"
    "Что даёт:\n"
    "🧠 Еженедельный AI-разбор цели — честная сверка того, что вы написали "
    "в анкете, с реальным прогрессом\n"
    "🚀 Доступ открывается сразу, без очереди на модерацию\n"
    "⭐ Метка Premium в рейтинге и профиле\n\n"
    f"Цена: <b>{PREMIUM_PRICE_STARS} ⭐</b> (Telegram Stars)"
)


@router.callback_query(F.data == "premium_info")
async def premium_info(callback: CallbackQuery):

    await callback.message.edit_text(
        PREMIUM_PERKS_TEXT,
        parse_mode="HTML",
        reply_markup=premium_buy_keyboard()
    )
    await callback.answer()


# =====================================
# ПОКУПКА ЧЕРЕЗ TELEGRAM STARS
# =====================================
# Stars — встроенная валюта Telegram (currency="XTR"), не требует
# подключения внешнего платёжного провайдера (в отличие от ЮKassa/Stripe) —
# работает сразу после публикации бота, без отдельной интеграции.

@router.callback_query(F.data == "buy_premium")
async def buy_premium(callback: CallbackQuery):

    await callback.message.answer_invoice(
        title="Project ADAM Premium",
        description="Еженедельный AI-разбор цели, доступ без очереди, метка Premium.",
        payload=f"premium_{callback.from_user.id}",
        provider_token="",  # для Stars всегда пусто
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=PREMIUM_PRICE_STARS)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    give_premium_admin(message.from_user.id)
    await message.answer(
        "✅ Спасибо! Premium активирован — доступны еженедельный AI-разбор "
        "цели и остальные плюшки.",
        reply_markup=back_menu_keyboard()
    )
