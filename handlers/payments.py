from aiogram import Router, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
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
    try_grant_channel_access, activate_xp_booster, get_timezone,
    is_payment_processed, mark_payment_processed, log_error,
    get_user, get_referred_users,
)
from keyboards import (
    premium_buy_keyboard, back_menu_keyboard, subscription_buy_keyboard,
    gift_premium_candidates_keyboard, gift_premium_confirm_keyboard,
)

router = Router()


class GiftState(StatesGroup):
    waiting_recipient = State()


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
# ПОДАРИТЬ PREMIUM ДРУГУ
# =====================================
# Раньше подарить Premium было нельзя вообще — только купить себе.
# Получателя выбираем из уже приглашённых пользователей (не нужно вводить
# ID руками) либо через пересланное от друга сообщение (message.forward_from —
# работает, только если у друга не скрыты пересылки в настройках приватности).

def _display_name(row):
    if not row:
        return "друг"
    return f"@{row['username']}" if row["username"] else (row["first_name"] or str(row["telegram_id"]))


@router.callback_query(F.data == "gift_premium_start")
async def gift_premium_start(callback: CallbackQuery, state: FSMContext):
    candidates = get_referred_users(callback.from_user.id)

    text = "🎁 <b>Подарить Premium другу</b>\n\n"
    if candidates:
        text += "Выбери из приглашённых тобой — или перешли мне любое сообщение от другого друга."
    else:
        text += (
            "Перешли мне любое сообщение от друга, кому хочешь подарить "
            "(должно быть разрешено пересылками в его настройках приватности)."
        )

    await state.set_state(GiftState.waiting_recipient)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=gift_premium_candidates_keyboard(candidates),
    )
    await callback.answer()


async def _offer_gift_invoice(message_target, recipient_id: int, giver_id: int):
    """message_target — объект с .answer()/.answer_invoice() (Message или
    CallbackQuery.message), общий код для обоих путей выбора получателя."""
    if recipient_id == giver_id:
        await message_target.answer("Самому себе Premium уже можно просто купить кнопкой выше 🙂")
        return
    recipient = get_user(recipient_id)
    if not recipient:
        await message_target.answer(
            "Этот пользователь ещё не запускал ADAM — попроси его сначала написать /start боту, "
            "а потом попробуй подарить снова."
        )
        return
    if recipient["premium"]:
        await message_target.answer(f"У {_display_name(recipient)} уже есть Premium — дарить не нужно 🎉")
        return

    await message_target.answer(
        f"Дарим Premium для {_display_name(recipient)} — подтверди оплату:",
        reply_markup=gift_premium_confirm_keyboard(recipient_id, PREMIUM_PRICE_STARS),
    )


@router.callback_query(F.data.startswith("gift_premium_to_"))
async def gift_premium_pick_candidate(callback: CallbackQuery, state: FSMContext):
    recipient_id = int(callback.data.removeprefix("gift_premium_to_"))
    await state.clear()
    await _offer_gift_invoice(callback.message, recipient_id, callback.from_user.id)
    await callback.answer()


@router.message(GiftState.waiting_recipient, F.forward_from)
async def gift_premium_from_forward(message: Message, state: FSMContext):
    await state.clear()
    await _offer_gift_invoice(message, message.forward_from.id, message.from_user.id)


@router.message(GiftState.waiting_recipient)
async def gift_premium_no_forward(message: Message):
    # Пересылки от друга скрыты его настройками приватности, forward_from
    # пуст — Bot API в этом случае не даёт узнать его ID вообще никак.
    await message.answer(
        "Не получилось узнать, кто это — у пересланного сообщения скрыт автор "
        "(настройки приватности друга). Выбери его из списка приглашённых выше "
        "или попроси на время разрешить пересылки в настройках Telegram."
    )


@router.callback_query(F.data.startswith("gift_premium_confirm_"))
async def gift_premium_confirm(callback: CallbackQuery):
    recipient_id = int(callback.data.removeprefix("gift_premium_confirm_"))
    await callback.message.answer_invoice(
        title="Project ADAM Premium — подарок",
        description="Подарок другу: еженедельный AI-разбор цели, доступ без очереди, метка Premium.",
        payload=f"gift_premium:{recipient_id}:{callback.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Premium в подарок", amount=PREMIUM_PRICE_STARS)],
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

    # Защита от двойного начисления: Telegram изредка может повторно
    # доставить update с successful_payment (сетевой сбой/рестарт бота
    # между получением апдейта и обработкой) — без этой проверки
    # пользователь получил бы награду дважды за одну и ту же оплату.
    # telegram_payment_charge_id уникален для каждой реальной транзакции
    # Stars, в отличие от invoice_payload (тот может повторяться).
    charge_id = payment.telegram_payment_charge_id
    if is_payment_processed(charge_id):
        log_error("duplicate_payment", f"charge_id={charge_id} payload={payload}", message.from_user.id)
        return
    mark_payment_processed(charge_id, message.from_user.id, payload, payment.total_amount)

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

    # Roadmap #32: разовый бустер x2 Adam Coin за Telegram Stars.
    if payload.startswith("booster:"):
        parts = payload.split(":")
        try:
            item_id = int(parts[1])
            paid_user_id = int(parts[2])
        except (IndexError, ValueError):
            return
        if paid_user_id != message.from_user.id:
            return
        item = get_shop_item(item_id)
        if item and item["item_type"] == "booster_stars":
            try:
                hours = int(item["payload"] or 24)
            except (TypeError, ValueError):
                hours = 24
            until = activate_xp_booster(message.from_user.id, hours)
            log_stars_purchase(message.from_user.id, item_id)
            try:
                from datetime import timezone as _tz
                from zoneinfo import ZoneInfo
                until_local_dt = until.replace(tzinfo=_tz.utc).astimezone(ZoneInfo(get_timezone(message.from_user.id)))
                until_local = until_local_dt.strftime("%H:%M %d.%m")
            except Exception:
                until_local = until.strftime("%H:%M %d.%m") + " UTC"
            await message.answer(
                f"⚡ Бустер x2 Adam Coin активирован до {until_local}! Все привычки в этом окне приносят вдвое больше.",
                reply_markup=back_menu_keyboard()
            )
        return

    # Подарок Premium другу — платит giver (message.from_user.id), Premium
    # получает recipient_id, поэтому здесь НЕТ обычной проверки
    # paid_user_id == message.from_user.id, как в остальных ветках выше.
    if payload.startswith("gift_premium:"):
        parts = payload.split(":")
        try:
            recipient_id = int(parts[1])
            giver_id = int(parts[2])
        except (IndexError, ValueError):
            return
        if giver_id != message.from_user.id:
            return
        give_premium_admin(recipient_id)
        await message.answer(
            "🎁 Готово! Premium подарен — друг уже получил уведомление.",
            reply_markup=back_menu_keyboard()
        )
        try:
            giver = get_user(giver_id)
            await message.bot.send_message(
                recipient_id,
                f"🎁 {_display_name(giver)} подарил(а) тебе Premium в Project ADAM! "
                "Доступны еженедельный AI-разбор цели, доступ без очереди и метка Premium.",
            )
        except Exception:
            pass  # получатель мог заблокировать бота — сама выдача уже прошла, это не критично
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
