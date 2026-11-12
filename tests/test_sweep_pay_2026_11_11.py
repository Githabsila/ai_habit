"""Прогон 11.11 (оплата): устаревший якорь календарного месяца; оплата «продления» Tribute не цепляется к заказу «первый месяц»."""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
import pay_providers
from db import add_user, get_user, pay_orders
from db.core import connect
from db.subscription import add_calendar_months, record_subscription_payment

_ids = itertools.count(861_500_000, 7)


def _set(uid, **cols):
    conn = connect()
    try:
        for k, v in cols.items():
            conn.execute(f"UPDATE users SET {k}=? WHERE telegram_id=?", (v, uid))
        conn.commit()
    finally:
        conn.close()


def test_stale_anchor_gives_one_day_for_paid_month():
    """Карта когда-то (якорь 1), потом звёзды сдвинули срок на 31-е; продление картой (Tribute по ссылке из напоминания) пока срок идёт."""
    uid = next(_ids)
    add_user(uid, "u", "U")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # конец текущего срока через 10 дней, день месяца не совпадает с якорем
    until = now + timedelta(days=10)
    anchor = 1 if until.day != 1 else 2
    _set(uid, subscription_paid_until=until.isoformat(), subscription_anchor_day=anchor,
         subscription_first_payment_at="2026-01-01 00:00:00", access_mode="open")
    new_until = record_subscription_payment(uid, months=1, calendar_months=True)
    gained = (new_until - until).days
    print("было", until, "якорь", anchor, "стало", new_until, "добавлено дней", gained)
    assert gained >= 28, f"за оплаченный месяц добавлено {gained} дн."


async def test_tribute_renewal_matched_to_open_first_month_order(monkeypatch):
    """Открытый заказ «первый месяц» (Tribute) + потом оплата товара «продление» ($5.99) уже платившим человеком → подписка не продлена."""
    key = "k-a1"
    monkeypatch.setattr(config, "TRIBUTE_API_KEY", key)
    admin = 777000222
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    tribute = pay_providers.PROVIDERS["tribute"]
    conn = connect()
    conn.execute("DELETE FROM pay_tribute_products")
    conn.commit()
    conn.close()
    pay_orders.set_tribute_product("sub_first", 9101, "first", 199, "USD", "https://t.me/x", "https://web/x")
    pay_orders.set_tribute_product("sub_renewal", 9102, "pro", 599, "USD", "https://t.me/y", "https://web/y")
    uid = next(_ids)
    add_user(uid, "u", "U")
    # 1) человек ещё не платил: в приложении нажал «Картой» — заказ sub_first, но не оплатил
    order = pay_orders.create_order(uid, "tribute", "card", "subscription", None, 199, "USD", price_key="sub_first")
    # 2) оплатил звёздами — вводная цена закрыта, подписка идёт (срок истёк — чтобы не мешал «already_paid»; Tribute по ссылке его и не проверяет)
    record_subscription_payment(uid, months=1)
    before = get_user(uid)["subscription_paid_until"]
    # 3) по кнопке «💳 Картой · $5.99» из бота платит продление
    result = await tribute._on_purchase({"purchase_id": 777123, "product_id": 9102, "telegram_user_id": uid, "amount": 599, "currency": "usd"}, bot)
    after = get_user(uid)["subscription_paid_until"]
    row = pay_orders.get_order(order["order_id"])
    print(result, dict(row), before, after, [c.args[1][:80] for c in bot.send_message.call_args_list])
    assert after != before, "человек заплатил $5.99, а срок не продлён"
