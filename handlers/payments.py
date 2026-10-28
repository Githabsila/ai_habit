import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import PREMIUM_PRICE_STARS, ADMIN_IDS, SUPPORT_EMAIL, WEBAPP_URL
from db import (
    give_premium_admin, set_cosmetic, get_shop_item, add_ai_bonus_answers, log_stars_purchase,
    get_subscription_price_stars, record_subscription_payment, get_subscription_status,
    try_grant_channel_access, activate_xp_booster, get_timezone, add_diamonds,
    is_payment_processed, mark_payment_processed, log_error,
    get_user, get_referred_users,
)
from keyboards import (
    premium_buy_keyboard, back_menu_keyboard, subscription_buy_keyboard, trial_keyboard,
    gift_premium_candidates_keyboard, gift_premium_confirm_keyboard,
)
from db.shop import begin_payment, finish_payment
from subscription_flow import complete_subscription_payment, refund_payment, report_payment_failure

router = Router()
logger = logging.getLogger(__name__)


class GiftState(StatesGroup):
    waiting_recipient = State()


# =====================================
# ЧТО ДАЁТ PREMIUM
# =====================================

PREMIUM_PERKS_TEXT = (
    "💎 <b>Project ADAM Premium</b>\n\n"
    "Что даёт:\n"
    "🧠 Мой еженедельный разбор твоей цели — честная сверка того, что записано "
    "в анкете, с реальным прогрессом\n"
    "🚀 Доступ открывается сразу, без очереди на модерацию\n"
    "⭐ Метка Premium в рейтинге и профиле\n\n"
    f"Цена: <b>{PREMIUM_PRICE_STARS} ⭐</b> (Telegram Stars)"
)


# Единая версия Pro (24.10): отдельный Premium пока не продаётся — всё, что он давал, входит в Pro для всех (config.PREMIUM_TIER_ENABLED, db/plans.py).
PREMIUM_CLOSED_TEXT = (
    "Всё, что раньше давал Premium — мой еженедельный разбор твоей цели и другое, — теперь входит в Pro для всех. "
    "Отдельно покупать ничего не нужно."
)


def premium_closed() -> bool:
    import config
    return not config.PREMIUM_TIER_ENABLED



@router.callback_query(F.data == "premium_info")
async def premium_info(callback: CallbackQuery):
    if premium_closed():
        await callback.answer(PREMIUM_CLOSED_TEXT, show_alert=True)
        return

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
    if premium_closed():
        await callback.answer(PREMIUM_CLOSED_TEXT, show_alert=True)
        return

    await callback.message.answer_invoice(
        title="Project ADAM Premium",
        description="Мой еженедельный разбор твоей цели, доступ без очереди, метка Premium.",
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
    if premium_closed():
        await callback.answer(PREMIUM_CLOSED_TEXT, show_alert=True)
        return
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
            "Этот пользователь ещё не заходил ко мне — попроси его сначала написать мне /start, "
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
    if premium_closed():
        await callback.answer(PREMIUM_CLOSED_TEXT, show_alert=True)
        return
    await _offer_gift_invoice(callback.message, recipient_id, callback.from_user.id)
    await callback.answer()


@router.message(GiftState.waiting_recipient, F.forward_from)
async def gift_premium_from_forward(message: Message, state: FSMContext):
    await state.clear()
    if premium_closed():
        await message.answer(PREMIUM_CLOSED_TEXT)
        return
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
    if premium_closed():
        await callback.answer(PREMIUM_CLOSED_TEXT, show_alert=True)
        return
    await callback.message.answer_invoice(
        title="Project ADAM Premium — подарок",
        description="Подарок другу: мой еженедельный разбор цели, доступ без очереди, метка Premium.",
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
    """Оплата доступа: схема и сумма — subscription_flow.invoice_spec (первая покупка разовая по вводной цене, дальше подписка с автопродлением, если она включена).
    Подписку Telegram принимает только по ссылке-счёту, поэтому для неё — кнопка со ссылкой, для разовой — обычный счёт."""
    from subscription_flow import create_invoice_link, invoice_spec
    spec = invoice_spec(callback.from_user.id)
    if spec is None:
        await callback.answer("Сейчас оплата не нужна: доступ открыт.", show_alert=True)
        return
    if spec["recurring"]:
        link, spec = await create_invoice_link(callback.bot, callback.from_user.id)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"⭐ Оформить за {spec['price']} Stars", url=link)]])
        await callback.message.answer(spec["description"], reply_markup=keyboard)
    else:
        await callback.message.answer_invoice(
            title=spec["title"],
            description=spec["description"],
            payload=spec["payload"],
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Доступ на 30 дней", amount=spec["price"])],
        )
        from db.tracking import track_server
        track_server(callback.from_user.id, "subscription_invoice", step=spec["snapshot"]["phase"],
                     meta={"price": spec["price"], "day": spec["snapshot"]["day"], "recurring": False, "surface": "bot"})
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Telegram спрашивает, можно ли брать оплату. Подписку берём только у того, на кого выписан счёт (иначе деньги ушли бы мимо)."""
    payload = str(pre_checkout_query.invoice_payload or "")
    if payload.startswith("subscription:"):
        try:
            owner = int(payload.split(":")[1])
        except (IndexError, ValueError):
            owner = None
        if owner != pre_checkout_query.from_user.id:
            await pre_checkout_query.answer(ok=False, error_message="Не получилось проверить счёт. Открой оплату в приложении ещё раз.")
            return
    await pre_checkout_query.answer(ok=True)


_payment_locks = {}


def _payment_lock(charge_id):
    """Один и тот же платёж не обрабатывается двумя апдейтами одновременно."""
    return _payment_locks.setdefault(str(charge_id), asyncio.Lock())


def _expiration(payment):
    """Дата окончания периода у подписки Stars (Telegram присылает unix-время) → naive UTC или None."""
    raw = getattr(payment, "subscription_expiration_date", None)
    if not raw:
        return None
    try:
        if isinstance(raw, datetime):
            return raw.astimezone(timezone.utc).replace(tzinfo=None) if raw.tzinfo else raw
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    """Деньги дошли. Платёж проходит состояния received → done / failed (db/shop.py::begin_payment): повторное сообщение Telegram о той же покупке
    ничего не начисляет второй раз, а если начисление упало — об этом узнают владелец и человек, и обработку можно повторить (subscription_flow.retry_failed_payments)."""
    payment = message.successful_payment
    payload = str(payment.invoice_payload or "")
    charge_id = payment.telegram_payment_charge_id
    recurring = bool(getattr(payment, "is_recurring", False))
    expires_at = _expiration(payment)
    async with _payment_lock(charge_id):
        state = begin_payment(charge_id, message.from_user.id, payload, payment.total_amount, purchased_at=getattr(message, "date", None),
                              is_recurring=recurring, expires_at=expires_at)
        if state == "duplicate":
            log_error("duplicate_payment", f"charge_id={charge_id} payload={payload}", message.from_user.id)
            return
        try:
            await _credit_payment(message, payment, payload)
        except Exception as exc:
            logger.exception("Оплата получена, но начисление не прошло: charge_id=%s payload=%s", charge_id, payload)
            finish_payment(charge_id, False, error=f"{type(exc).__name__}: {exc}")
            try:
                await report_payment_failure(message.bot, message.from_user, charge_id, payload, payment.total_amount, exc)
            except Exception:
                logger.exception("Не удалось сообщить о сбое оплаты (%s)", charge_id)
            return
        finish_payment(charge_id, True)


async def _credit_payment(message: Message, payment, payload: str):
    """Начисление за один платёж: что именно куплено, определяет payload."""

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

    # Алмазы (премиальная валюта) за Telegram Stars — первая и пока
    # единственная платная покупка алмазов; правила, на что их можно
    # тратить, продумаем отдельно.
    if payload.startswith("diamond_pack_stars:"):
        parts = payload.split(":")
        try:
            item_id = int(parts[1])
            paid_user_id = int(parts[2])
        except (IndexError, ValueError):
            return
        if paid_user_id != message.from_user.id:
            return
        item = get_shop_item(item_id)
        if item and item["item_type"] == "diamond_pack_stars":
            try:
                amount = int(item["payload"] or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                add_diamonds(message.from_user.id, amount)
            log_stars_purchase(message.from_user.id, item_id)
        await message.answer(
            f"💎 Спасибо! {item['name'] if item else 'Алмазы'} зачислены на баланс.",
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
                "Теперь тебе доступны мой еженедельный разбор цели, доступ без очереди и метка Premium.",
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
        # Что сказать человеку, зависит от дня триала и отметок (ТЗ владельца): subscription_flow / trial_messages.paid_welcome_text.
        text, snap, link = await complete_subscription_payment(
            message.bot, message.from_user.id, paid_at=getattr(message, "date", None), charge_id=payment.telegram_payment_charge_id,
            recurring=bool(getattr(payment, "is_recurring", False)), expires_at=_expiration(payment))
        await message.answer(text, reply_markup=trial_keyboard(("open",), snap["price_stars"]) or back_menu_keyboard())
        return

    give_premium_admin(message.from_user.id)
    await message.answer(
        "✅ Спасибо! Premium активирован — доступны мой еженедельный разбор "
        "твоей цели и остальные плюшки.",
        reply_markup=back_menu_keyboard()
    )

# =====================================
# ПОДДЕРЖКА ОПЛАТЫ, УСЛОВИЯ, ВОЗВРАТ
# =====================================
# Telegram требует у ботов с платежами команды /paysupport (куда писать, если оплата не сработала или нужен возврат) и /terms.

@router.message(Command("paysupport"))
async def paysupport(message: Message):
    from config import SUPPORT_CONTACT
    contact = f"<b>{SUPPORT_CONTACT}</b> или на <b>{SUPPORT_EMAIL}</b>" if SUPPORT_CONTACT else f"<b>{SUPPORT_EMAIL}</b>"
    await message.answer(
        "💳 <b>Вопросы по оплате</b>\n\n"
        "Оплата идёт через Telegram Stars. Если Stars списались, а доступ не открылся, или платёж прошёл по ошибке — "
        f"напиши на {contact}: укажи свой @ник и время оплаты. Я проверю и, если нужно, верну Stars. Повторно платить не нужно.\n\n"
        "Условия оплаты и возврата — /terms."
    )


@router.message(Command("terms"))
async def terms(message: Message):
    from config import SUBSCRIPTION_AUTORENEW_ENABLED
    link = f"{WEBAPP_URL.rstrip('/')}/terms" if WEBAPP_URL else ""
    renewal = ("Первый месяц — разовая оплата по вводной цене, дальше — подписка Stars с автопродлением: отключить её можно в приложении "
               "(раздел «Подписка») или в настройках Telegram."
               if SUBSCRIPTION_AUTORENEW_ENABLED else "Подписка не продлевается сама: срок заканчивается, и нужно оплатить заново.")
    await message.answer(
        "📄 <b>Условия</b>\n\n"
        "Первые 3 календарных дня — бесплатно, считая день первого открытия приложения. Потом — подписка Pro: первый месяц по вводной цене для первой покупки "
        f"(в любой день), затем полная. {renewal} Если оплатить во время пробных дней, оставшиеся бесплатные дни добавятся к сроку. "
        f"Возврат — по правилам Telegram Stars или через поддержку: {SUPPORT_EMAIL} (/paysupport)."
        + (f"\n\nПолный текст: {link}" if link else "")
    )


@router.message(Command("refund"))
async def refund_command(message: Message):
    """Админ: /refund <номер операции Stars> — вернуть платёж (подписка при этом снимается)."""
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Формат: /refund <номер операции Stars (charge_id)>")
        return
    result = await refund_payment(message.bot, parts[1])
    if result.get("ok"):
        await message.answer(f"✅ Возвращено {result['amount']} ⭐ пользователю {result['user_id']}.")
    else:
        await message.answer(f"Не получилось: {result['error']} {result.get('detail', '')}".strip())
