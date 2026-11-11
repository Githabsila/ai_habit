"""
Lava.top (pay_lava.py, 07.11): карты Visa/Mastercard в долларах, «Мир» и СБП в рублях, подписка с автосписанием. Запросы к Lava подменяем —
проверяем наш путь: заказ, почта для чека, вебхук (секрет, сумма, повтор), автопродление и отмена, привязка офферов.
"""
import itertools
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import pay_providers
from db import add_user, get_user, pay_orders
from db.core import connect
from db.subscription import has_active_subscription, record_subscription_payment
from tests.conftest import sign_init_data

SECRET = "lava-webhook-secret-for-tests"
_counter = itertools.count(873_000_000, 100)
_contract = itertools.count(1)


@pytest.fixture
def uid():
    return next(_counter)


@pytest.fixture(autouse=True)
def _clean():
    conn = connect()
    try:
        conn.execute("DELETE FROM pay_lava_offers")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def lava(monkeypatch):
    monkeypatch.setattr(config, "LAVA_API_KEY", "test-key")
    monkeypatch.setattr(config, "LAVA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", True)
    calls = []

    async def fake_request(self, method, path, body=None, params=None):
        calls.append((self.name, method, path, body, params))
        if path == "/api/v3/invoice":
            return 201, {"id": f"c-{next(_contract)}", "paymentUrl": "https://pay.lava.top/x", "status": "new"}
        if path == "/api/v1/subscriptions":
            return 204, None
        return 404, None

    from pay_lava import Lava
    monkeypatch.setattr(Lava, "_request", fake_request)
    return calls


@pytest.fixture
def bot(client, monkeypatch):
    fake = SimpleNamespace(send_message=AsyncMock(), token="")
    client.app["bot"] = fake
    monkeypatch.setattr(config, "ADMIN_IDS", [777000222])
    return fake


def _user(uid_):
    add_user(uid_, f"l{uid_}", "Аня")
    conn = connect()
    try:
        conn.execute("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
        conn.commit()
    finally:
        conn.close()
    return uid_


def _link_all():
    pay_orders.set_lava_offer("sub_first", "p1", "o-first", "ADAM Pro первый месяц", {"USD": 199, "RUB": 19900})
    pay_orders.set_lava_offer("sub_renewal", "p2", "o-month", "ADAM Pro 30 дней", {"USD": 599, "RUB": 59900}, "MONTHLY")
    pay_orders.set_lava_offer("item_27", "p3", "o-10d", "10 алмазов", {"USD": 199, "RUB": 19900})


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


async def _hook(client, payload, secret=SECRET):
    return await client.post("/api/pay/webhook/lava", data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json", "X-Api-Key": secret})


async def test_the_card_asks_for_an_email_once_then_creates_the_invoice(client, uid, lava, bot):
    _user(uid)
    _link_all()
    methods = (await (await client.get("/api/pay/methods?kind=item&item_id=27", headers=_headers(uid))).json())["methods"]
    by = {m["method"]: m for m in methods}
    assert by["card"]["price_text"] == "$1.99" and by["rub"]["price_text"] == "199 ₽" and by["sbp"]["price_text"] == "199 ₽"
    assert by["card"]["needs_email"] is True and by["card"]["email"] is None
    no_email = await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "item", "item_id": 27})
    assert no_email.status == 400 and (await no_email.json())["error"] == "email_required"
    bad = await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "item", "item_id": 27, "email": "не почта"})
    assert (await bad.json())["error"] == "email_required"
    ok = await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "item", "item_id": 27, "email": "anya@example.com"})
    body = await ok.json()
    assert ok.status == 200 and body["pay_url"] == "https://pay.lava.top/x"
    sent = lava[-1][3]
    assert sent["email"] == "anya@example.com" and sent["offerId"] == "o-10d" and sent["currency"] == "USD" and sent["paymentProvider"] == "UNLIMINT"
    assert sent["clientUtm"]["utm_content"] == body["order_id"]
    again = await client.post("/api/pay/order", headers=_headers(uid), json={"method": "rub", "kind": "item", "item_id": 27})
    assert again.status == 200, "почта запомнена — второй раз не спрашиваем"
    rub = pay_orders.get_order((await again.json())["order_id"])
    assert rub["currency"] == "RUB" and rub["amount_cents"] == 19900 and lava[-1][3]["paymentProvider"] == "SMART_GLOCAL"


async def test_a_paid_webhook_grants_once_and_checks_the_secret_and_the_sum(client, uid, lava, bot):
    from db.users import get_user as _get
    _user(uid)
    _link_all()
    pay_orders.set_pay_email(uid, "a@example.com")
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "item", "item_id": 27})).json()
    order = pay_orders.get_order(created["order_id"])
    before = int(_get(uid)["diamonds"] or 0)
    event = {"eventType": "payment.success", "contractId": order["provider_uuid"], "amount": 1.99, "currency": "USD", "status": "completed",
             "buyer": {"email": "a@example.com"}, "clientUtm": {"utm_content": order["order_id"]}}
    assert (await _hook(client, event, secret="wrong")).status == 400, "чужой секрет — отказ"
    assert (await _hook(client, event)).status == 200
    assert int(_get(uid)["diamonds"] or 0) == before + 10
    assert (await _hook(client, event)).status == 200
    assert int(_get(uid)["diamonds"] or 0) == before + 10, "повтор вебхука второй раз не начисляет"
    created2 = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "item", "item_id": 27})).json()
    order2 = pay_orders.get_order(created2["order_id"])
    cheap = dict(event, contractId=order2["provider_uuid"], amount=0.5)
    assert (await (await _hook(client, cheap)).json()).get("ignored") is True
    assert int(_get(uid)["diamonds"] or 0) == before + 10, "другая сумма — не выдаём"


async def test_a_monthly_subscription_turns_on_autorenew_renews_by_calendar_month_and_can_be_cancelled(client, uid, lava, bot):
    _user(uid)
    _link_all()
    record_subscription_payment(uid, months=1)                     # уже платил — дальше продление (sub_renewal, ежемесячный оффер)
    pay_orders.set_pay_email(uid, "a@example.com")
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ("2026-11-07T10:00:00", uid))
        conn.commit()
    finally:
        conn.close()
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "subscription"})).json()
    order = pay_orders.get_order(created["order_id"])
    assert lava[-1][3]["periodicity"] == "MONTHLY" and order["price_key"] == "sub_renewal"
    first = {"eventType": "payment.success", "contractId": order["provider_uuid"], "amount": 5.99, "currency": "USD", "status": "subscription-active"}
    assert (await _hook(client, first)).status == 200
    user = get_user(uid)
    assert user["subscription_auto_renew"] == 1 and user["subscription_charge_id"] == f"lava:{order['provider_uuid']}"
    until1 = datetime.fromisoformat(user["subscription_paid_until"])
    renew = {"eventType": "subscription.recurring.payment.success", "contractId": "c-renew-1", "parentContractId": order["provider_uuid"],
             "amount": 5.99, "currency": "USD", "status": "subscription-active"}
    assert (await _hook(client, renew)).status == 200
    assert (await _hook(client, renew)).status == 200                  # повтор
    until2 = datetime.fromisoformat(get_user(uid)["subscription_paid_until"])
    assert 28 <= (until2 - until1).days <= 31, "продление — один календарный месяц, повтор не добавляет"
    off = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": False})
    assert off.status == 200 and not get_user(uid)["subscription_auto_renew"]
    assert lava[-1][1] == "DELETE" and lava[-1][4]["contractId"] == order["provider_uuid"]
    on = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": True})
    assert on.status in (409,)


async def test_cancel_webhook_turns_autorenew_off_and_refund_alerts_the_admins(client, uid, lava, bot):
    _user(uid)
    _link_all()
    pay_orders.set_pay_email(uid, "a@example.com")
    record_subscription_payment(uid, months=1)
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ("2026-11-07T10:00:00", uid))
        conn.commit()
    finally:
        conn.close()
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "subscription"})).json()
    order = pay_orders.get_order(created["order_id"])
    await _hook(client, {"eventType": "payment.success", "contractId": order["provider_uuid"], "amount": 5.99, "currency": "USD", "status": "subscription-active"})
    await _hook(client, {"eventType": "subscription.cancelled", "contractId": order["provider_uuid"]})
    assert not get_user(uid)["subscription_auto_renew"]
    await _hook(client, {"event_type": "refund.success", "data": {"amount": 5.99, "currency": "USD", "product": {"product_name": "ADAM Pro"}, "customer_email": "a@example.com"}})
    assert any("возврат" in c.args[1] for c in bot.send_message.await_args_list)
    assert has_active_subscription(uid), "возврат доступ автоматически не снимает — решает владелец"


async def test_auto_link_matches_offers_by_usd_price_name_and_periodicity(lava, monkeypatch):
    from pay_lava import Lava
    products = [
        {"id": "p1", "title": "ADAM Pro — первый месяц", "offers": [{"id": "o1", "name": "", "prices": [{"amount": 1.99, "currency": "USD", "periodicity": "ONE_TIME"}, {"amount": 199, "currency": "RUB", "periodicity": "ONE_TIME"}]}]},
        {"id": "p2", "title": "ADAM Pro — 30 дней", "offers": [{"id": "o2", "name": "", "prices": [{"amount": 5.99, "currency": "USD", "periodicity": "MONTHLY"}, {"amount": 599, "currency": "RUB", "periodicity": "MONTHLY"}]}]},
        {"id": "p3", "title": "10 алмазов", "offers": [{"id": "o3", "name": "", "prices": [{"amount": 1.99, "currency": "USD", "periodicity": "ONE_TIME"}]}]},
        {"id": "p4", "title": "+50 ответов Adam", "offers": [{"id": "o4", "name": "", "prices": [{"amount": 1.99, "currency": "USD", "periodicity": "ONE_TIME"}]}]},
    ]

    async def fake_list(self):
        return products

    monkeypatch.setattr(Lava, "list_products", fake_list)
    report = {r["key"]: r for r in await pay_providers.PROVIDERS["lava"].auto_link()}
    assert report["sub_first"]["status"] == "linked" and pay_orders.lava_offer("sub_first")["offer_id"] == "o1"
    assert report["sub_renewal"]["status"] == "linked" and pay_orders.lava_offer("sub_renewal")["periodicity"] == "MONTHLY"
    assert report["item_27"]["status"] == "linked" and pay_orders.lava_price("item_27", "RUB") is None, "нет цены в рублях — «Мир» и СБП не покажутся"
    assert report["item_22"]["status"] == "linked" and report["item_28"]["status"] == "not_found"
    assert pay_orders.lava_price("sub_first", "RUB") == 19900


def test_lava_comes_first_among_cards_and_the_bot_link_stays_tribute_only(lava):
    assert list(pay_providers.PROVIDERS)[:5] == ["cryptobot", "heleket", "lava", "lava_rub", "lava_sbp"]   # 11.11: Crypto Bot — первой среди крипты
    _link_all()
    assert pay_providers.provider_for("card", "item_27").name == "lava"
    assert pay_providers.card_link("item", item_id=27) is None, "у Lava нет общей ссылки — в боте кнопка «Картой» не показывается"


async def test_review_the_intro_price_pays_for_only_one_month(client, uid, lava, bot):
    """Прогон 09.11: до первой оплаты можно открыть несколько счетов «первый месяц» ($1.99) и оплатить все — месяц по вводной цене только один,
    остальные не продлевают подписку и попадают админам (как P2 у Tribute)."""
    _user(uid)
    _link_all()
    pay_orders.set_pay_email(uid, "a@example.com")
    orders = []
    for _ in range(2):
        created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "subscription"})).json()
        orders.append(pay_orders.get_order(created["order_id"]))
    assert all(o["price_key"] == "sub_first" for o in orders)
    for o in orders:
        event = {"eventType": "payment.success", "contractId": o["provider_uuid"], "amount": 1.99, "currency": "USD", "status": "completed"}
        assert (await _hook(client, event)).status == 200
    until = datetime.fromisoformat(get_user(uid)["subscription_paid_until"])
    assert (until - datetime.now()).days <= 31, "второй счёт по вводной цене не добавил месяц"
    assert any("вводн" in c.args[1] for c in bot.send_message.await_args_list if c.args[0] == 777000222)
