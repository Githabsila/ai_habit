"""
Оплата картой и криптовалютой: создание заказа и начисление покупки (db/pay_orders.py, pay_providers.py, webapp/routes_pay.py).

Путь: человек выбирает способ → start_order создаёт заказ и счёт у платёжного сервиса → человек платит на странице сервиса → сервис присылает подтверждение (вебхук) →
fulfil начисляет покупку ровно один раз и пишет человеку в бота. Звёзды идут прежним путём (handlers/payments.py) и здесь не участвуют.

Покупка — это либо подписка (30 дней, цена вводная для первой покупки, потом полная; те же правила и тот же код, что у звёзд: subscription_flow.complete_subscription_payment —
защита серии, добавленные бесплатные дни, текст подтверждения), либо товар магазина (ответы ADAM, алмазы, бустер, рамка).
"""
import logging

import pay_providers
from db import pay_orders, shop, trial as trial_db
from db.subscription import has_active_subscription
from db.tracking import track_server

logger = logging.getLogger(__name__)

ITEM_TYPES = ("frame_stars", "answer_pack_stars", "booster_stars", "diamond_pack_stars")
PROVIDER_ERRORS = ("method_unavailable", "provider_error", "already_paid", "already_owned", "daily_limit_reached", "item_not_found", "invalid_request")


class PayError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _stars_price(user_id, kind, item):
    if kind == "subscription":
        return int(trial_db.price_stars(user_id))
    return int(item["price"])


def _check_item(user_id, item_id):
    """Тот же набор проверок, что у счёта звёзд (webapp_server.create_stars_invoice): рамка уже есть, дневной лимит пакета ответов."""
    item = shop.get_shop_item(int(item_id)) if str(item_id).lstrip("-").isdigit() else None
    if not item or item["item_type"] not in ITEM_TYPES or pay_orders.price_cents("item", item["id"]) is None:
        raise PayError("item_not_found")
    if item["item_type"] == "frame_stars":
        from db.users import get_user
        user = get_user(user_id)
        if user and user["frame_id"] == "paid_double_gold":
            raise PayError("already_owned")
    elif item["item_type"] == "answer_pack_stars" and shop.has_reached_daily_limit(user_id, item["id"], item):
        raise PayError("daily_limit_reached")
    return item


def methods_for(user_id, kind, item_id=None):
    """Что показать в окне выбора способа: звёзды (всегда) и настроенные способы с ценой. Бросает PayError, если покупать нечего."""
    if kind == "subscription":
        if has_active_subscription(user_id):
            raise PayError("already_paid")
        item = None
    elif kind == "item":
        item = _check_item(user_id, item_id)
    else:
        raise PayError("invalid_request")
    methods = [{"method": "stars", "label": "Звёздами", "price_text": f"{_stars_price(user_id, kind, item)} ⭐"}]
    cents = pay_orders.price_cents(kind, item["id"] if item else None, user_id)
    if cents:
        key = pay_orders.price_key(kind, item["id"] if item else None, user_id)
        for entry in pay_providers.available_methods(key):
            methods.append({"method": entry["method"], "label": entry["label"], "price_text": pay_orders.format_usd(cents)})
    return methods


async def start_order(user_id, method, kind, item_id=None):
    """Создаёт заказ и счёт у платёжного сервиса. {"order_id", "pay_url"} либо PayError(код)."""
    if kind == "subscription":
        key = pay_orders.price_key("subscription", user_id=user_id)
    else:
        key = pay_orders.price_key("item", item_id) if str(item_id).lstrip("-").isdigit() else None
    provider = pay_providers.provider_for(method, key)
    if provider is None:
        raise PayError("method_unavailable")
    if kind == "subscription":
        if has_active_subscription(user_id):
            raise PayError("already_paid")
        item = None
    elif kind == "item":
        item = _check_item(user_id, item_id)
    else:
        raise PayError("invalid_request")
    cents = pay_orders.price_cents(kind, item["id"] if item else None, user_id)
    if not cents:
        raise PayError("item_not_found")
    order = pay_orders.create_order(user_id, provider.name, method, kind, item["id"] if item else None, cents, price_key=key)
    try:
        invoice = await provider.create_invoice(order, cents)
    except pay_providers.ProviderError as exc:
        pay_orders.mark_failed(order["order_id"], str(exc))
        logger.warning("Счёт %s не создан: %s", order["order_id"], exc)
        raise PayError("provider_error")
    pay_orders.set_provider_info(order["order_id"], invoice["uuid"], invoice["url"])
    track_server(user_id, "pay_order_created", method, meta={"kind": kind, "item": item["id"] if item else None, "cents": cents, "provider": provider.name})
    return {"order_id": order["order_id"], "pay_url": invoice["url"]}


def _support_text():
    import config
    return str(getattr(config, "SUPPORT_CONTACT", "") or getattr(config, "SUPPORT_EMAIL", "") or "")


def catalog(user_id):
    """Всё для окна «Покупки» в магазине (GET /api/pay/catalog): подписка, товары с ценой в долларах и в звёздах, способы оплаты, поддержка и акции.
    Цены в долларах — основные (решение владельца 28.10); звёзды — только как цена в счёте Telegram."""
    from db.users import get_user
    user = get_user(user_id)
    paid_until = None
    active = has_active_subscription(user_id)
    if active and user is not None and "subscription_paid_until" in user.keys():
        paid_until = str(user["subscription_paid_until"])[:10]
    sub_cents = pay_orders.price_cents("subscription", user_id=user_id)
    groups = {"diamond_pack_stars": "diamonds", "answer_pack_stars": "answers", "booster_stars": "booster", "frame_stars": "frame"}
    items = []
    for item_id in pay_orders.USD_PRICES_CENTS:
        item = shop.get_shop_item(item_id)
        if not item or item["item_type"] not in ITEM_TYPES:
            continue
        available, note = True, None
        try:
            _check_item(user_id, item_id)
        except PayError as exc:
            available, note = False, {"already_owned": "Уже у тебя", "daily_limit_reached": "На сегодня куплено"}.get(exc.code, "Недоступно")
        items.append({"item_id": item_id, "group": groups.get(item["item_type"], "other"), "name": item["name"], "description": item["description"],
                      "usd_text": pay_orders.format_usd(pay_orders.USD_PRICES_CENTS[item_id]), "stars_text": f"{int(item['price'])} ⭐", "available": available, "note": note})
    methods = [{"method": "stars", "label": "Звёздами Telegram"}] + [{"method": m["method"], "label": m["label"]} for m in pay_providers.available_methods()]
    return {
        "subscription": {"active": active, "until": paid_until, "price_text": pay_orders.format_usd(sub_cents),
                         "next_text": f"дальше {pay_orders.format_usd(pay_orders.SUBSCRIPTION_RENEWAL_CENTS)} в месяц" if sub_cents == pay_orders.SUBSCRIPTION_FIRST_CENTS else "в месяц"},
        "items": items, "methods": methods, "support": _support_text(),
        "promos": [{"id": p["id"], "title": p["title"], "text": p["text"], "until": p["until"]} for p in pay_orders.list_promos()],
    }


# ---------------------------------------------------------------------------------------------------------------- начисление

def _grant_item(user_id, item_id):
    """Выдаёт товар магазина. Возвращает текст для человека. Те же действия, что в handlers/payments.py после оплаты звёздами."""
    from db.users import activate_xp_booster, add_diamonds
    item = shop.get_shop_item(int(item_id))
    if not item:
        raise ValueError(f"нет товара {item_id}")
    kind = item["item_type"]
    if kind == "answer_pack_stars":
        shop.add_ai_bonus_answers(user_id, int(item["payload"] or 0))
        shop.log_stars_purchase(user_id, item["id"])
        return f"✅ Спасибо! {item['name']} добавлен к твоему дневному лимиту."
    if kind == "diamond_pack_stars":
        amount = int(item["payload"] or 0)
        if amount > 0:
            add_diamonds(user_id, amount)
        shop.log_stars_purchase(user_id, item["id"])
        return f"💎 Спасибо! {item['name']} зачислены на баланс."
    if kind == "booster_stars":
        activate_xp_booster(user_id, int(item["payload"] or 24))
        shop.log_stars_purchase(user_id, item["id"])
        return "⚡ Бустер x2 Adam Coin активирован! Все привычки в ближайшие часы приносят вдвое больше."
    if kind == "frame_stars":
        shop.set_cosmetic(user_id, "frame", "paid_double_gold")
        return "👑 Рамка Double Gold активирована! Теперь она на твоей аватарке в профиле и рейтинге."
    raise ValueError(f"тип товара не оплачивается: {kind}")


async def _grant_subscription(bot, order):
    """Подписка тем же путём, что у звёзд: запись платежа (received → done), продление срока, защита серии, текст подтверждения."""
    import subscription_flow
    charge_id = f"{order['provider']}:{order['order_id']}"
    state = shop.begin_payment(charge_id, order["user_id"], f"subscription:{order['user_id']}", 0)
    if state == "duplicate":
        return None
    try:
        text, _snap, _link = await subscription_flow.complete_subscription_payment(bot, order["user_id"], charge_id=charge_id)
    except Exception as exc:
        shop.finish_payment(charge_id, False, error=f"{type(exc).__name__}: {exc}")
        raise
    shop.finish_payment(charge_id, True)
    try:
        from db.core import connect
        conn = connect()
        try:
            conn.execute("UPDATE stars_payments SET provider=? WHERE charge_id=?", (order["provider"], charge_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Не удалось пометить способ оплаты у %s", charge_id)
    return text


async def fulfil(bot, order):
    """Начисляет оплаченный заказ один раз. True — начислили сейчас; False — уже было. Ошибка начисления возвращает заказ в очередь и пробрасывается выше
    (вебхук ответит ошибкой, сервис повторит; админ видит такие заказы в списке)."""
    if not pay_orders.claim_fulfilment(order["order_id"]):
        return False
    try:
        if order["kind"] == "subscription":
            text = await _grant_subscription(bot, order)
        else:
            text = _grant_item(order["user_id"], order["item_id"])
    except Exception as exc:
        pay_orders.release_fulfilment(order["order_id"], f"{type(exc).__name__}: {exc}")
        logger.exception("Оплата подтверждена, а начисление не прошло: %s", order["order_id"])
        raise
    track_server(order["user_id"], "pay_order_paid", order["method"], meta={"kind": order["kind"], "item": order["item_id"], "cents": order["amount_cents"], "provider": order["provider"]})
    if text and bot is not None:
        try:
            await bot.send_message(order["user_id"], text)
        except Exception:
            logger.info("Не удалось написать человеку о покупке %s", order["order_id"])
    return True
