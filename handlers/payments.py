from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
)

from config import PREMIUM_PRICE_STARS
from db import (
    give_premium_admin, set_cosmetic, get_shop_item, add_ai_bonus_answers, log_stars_purchase,
    get_subscription_price_stars, record_subscription_payment, get_subscription_status,
    try_grant_channel_access,
)
from keyboards import premium_buy_keyboard, back_menu_keyboard, subscription_buy_keyboard

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


# =====================================
# ПОДПИСКА НА БОТА: ТРИАЛ → ОПЛАТА (пром 13)
# =====================================
# Отдельно от Premium (косметика выше) — это доступ к самому боту после
# 3-дневного триала. См. db/subscription.py.

@router.callback_query(F.data == "buy_subscription")
async def buy_subscription(callback: CallbackQuery):
    price = get_subscription_price_stars(callback.from_user.id)
    await callback.message.answer_invoice(
        title="Project ADAM — доступ на месяц",
        description="Продлевает доступ к боту и мини-приложению на 30 дней.",
        payload=f"subscription:{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка на месяц", amount=price)],
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    payload = str(payment.invoice_payload or "")

    # Покупка платной рамки из Mini App через Telegram Stars.
    if payload.startswith("avatar_frame:"):
        parts = payload.split(":")
        try:
            paid_user_id = int(parts[-1])
        except (TypeError, ValueError):
            paid_user_id = message.from_user.id
        if paid_user_id == message.from_user.id:
            set_cosmetic(message.from_user.id, "frame", "paid_double_gold")
            await message.answer(
                "👑 Рамка Double Gold активирована! Теперь она доступна на твоей аватарке в профиле и рейтинге.",
                reply_markup=back_menu_keyboard()
            )
        return

    # Пром 9: пакеты +50/+100 ответов ADAM за Telegram Stars.
    if payload.startswith("answer_pack_stars:"):
        parts = payload.split(":")
        try:
            item_id = int(parts[1])
            paid_user_id = int(parts[2])
        except (IndexError, ValueError):
            return
        if paid_user_id != message.from_user.id:
            return
        item = get_shop_item(item_id)
        if item and item["item_type"] == "answer_pack_stars":
            try:
                add_ai_bonus_answers(message.from_user.id, int(item["payload"] or 0))
            except (TypeError, ValueError):
                pass
            log_stars_purchase(message.from_user.id, item_id)
        await message.answer(
            f"✅ Спасибо! {item['name'] if item else 'Пакет ответов'} добавлен к твоему дневному лимиту.",
            reply_markup=back_menu_keyboard()
        )
        return

    # Пром 13: оплата доступа к боту (триал → подписка).
    if payload.startswith("subscription:"):
        parts = payload.split(":")
        try:
            paid_user_id = int(parts[1])
        except (IndexError, ValueError):
            paid_user_id = message.from_user.id
        if paid_user_id != message.from_user.id:
            return
        until = record_subscription_payment(message.from_user.id, months=1)
        status = get_subscription_status(message.from_user.id)
        until_str = until.strftime("%d.%m.%Y")
        text = f"✅ Доступ в бота продлён до {until_str}."
        if status["channel_eligible"]:
            invite = await try_grant_channel_access(message.bot, message.from_user.id)
            if invite:
                text += f"\n\n🔑 Ты выполнил {status['streak_needed_for_channel']}+ дней ударного режима подряд — вот ссылка в закрытый канал: {invite}"
        else:
            needed = status["streak_needed_for_channel"]
            text += (
                f"\n\nЧтобы получить доступ в закрытый канал, выполни привычку "
                f"{needed} дня подряд (сейчас серия: {status['streak']}) — "
                "как наберёшь, ссылка придёт автоматически."
            )
        await message.answer(text, reply_markup=back_menu_keyboard())
        return

    give_premium_admin(message.from_user.id)
    await message.answer(
        "✅ Спасибо! Premium активирован — доступны еженедельный AI-разбор "
        "цели и остальные плюшки.",
        reply_markup=back_menu_keyboard()
    )
