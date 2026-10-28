"""
Оплата картой и криптовалютой (решение владельца 28.10.2026): заказ → ссылка сервиса → подтверждение (вебхук с подписью) → покупка выдаётся ровно один раз.
Проверяем подпись Heleket по его документации, цены, все отказы, выдачу подписки и товаров магазина, повтор вебхука, неверную сумму и сбой начисления.
"""
import itertools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import pay_providers
import payments_ext
from db import add_user, get_user, pay_orders
from db.core import connect
from db.subscription import has_active_subscription, record_subscription_payment
from tests.conftest import sign_init_data

ADMIN = 777
_counter = itertools.count(869_100_000, 100)
KEY = "test-payment-key"


@pytest.fixture
def uid():
    return next(_counter)


@pytest.fixture
def heleket(monkeypatch):
    """Heleket «подключён» (ключи тестовые), создание счёта у сервиса подменено."""
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(config, "HELEKET_API_KEY", KEY)
    provider = pay_providers.PROVIDERS["heleket"]
    calls = []

    async def fake_create(order, amount_cents):
        calls.append((order["order_id"], amount_cents))
        return {"uuid": "uuid-" + order["order_id"], "url": "https://pay.example/" + order["order_id"]}

    monkeypatch.setattr(provider, "create_invoice", fake_create)
    provider.calls = calls
    return provider


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _user(uid_):
    add_user(uid_, f"p{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    return uid_


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _webhook(provider, order, status="paid", amount=None, **extra):
    """Тело подтверждения как у Heleket: подпись внутри, считается по телу без неё."""
    data = {"type": "payment", "uuid": order["provider_uuid"] or "u", "order_id": order["order_id"], "amount": amount or f"{order['amount_cents'] / 100:.2f}",
            "status": status, "is_final": status in ("paid", "paid_over"), "currency": "USD", "additional_data": str(order["user_id"]), **extra}
    data["sign"] = provider.sign_text(provider.dump(data))
    return json.dumps(data)


async def _post_webhook(client, body):
    return await client.post("/api/pay/webhook/heleket", data=body.encode("utf-8"), headers={"Content-Type": "application/json"})


@pytest.fixture
def bot(client):
    fake = SimpleNamespace(send_message=AsyncMock(), token="")
    client.app["bot"] = fake
    return fake


# ---------------------------------------------------------------------------------------------------------------- цены и способы

def test_prices_follow_the_intro_rule_and_the_shop_table(uid):
    person = _user(uid)
    assert pay_orders.price_cents("subscription", user_id=person) == 199, "первая покупка — вводная цена"
    record_subscription_payment(person, months=1)
    assert pay_orders.price_cents("subscription", user_id=person) == 599, "дальше — полная"
    assert pay_orders.price_cents("item", 27) == 199 and pay_orders.price_cents("item", 28) == 799 and pay_orders.price_cents("item", 999) is None
    assert pay_orders.price_cents("item", "boom") is None and pay_orders.price_cents("boom") is None
    assert pay_orders.format_usd(199) == "$1.99" and pay_orders.format_usd(5) == "$0.05"
    assert set(pay_orders.USD_PRICES_CENTS) == {7, 22, 23, 24, 27, 28}, "каждому товару за звёзды в магазине есть цена и в долларах"


def test_without_keys_only_stars_are_offered_and_with_keys_the_crypto_method_appears(uid, monkeypatch):
    person = _user(uid)
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "")
    assert [m["method"] for m in payments_ext.methods_for(person, "subscription")] == ["stars"]
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "id")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "key")
    methods = payments_ext.methods_for(person, "subscription")
    assert [m["method"] for m in methods] == ["stars", "crypto"]
    assert methods[0]["price_text"] == "153 ⭐" and methods[1]["price_text"] == "$1.99" and methods[1]["label"] == "Криптой"
    item_methods = payments_ext.methods_for(person, "item", 28)
    assert item_methods[0]["price_text"] == "600 ⭐" and item_methods[1]["price_text"] == "$7.99"


def test_nothing_is_offered_for_unknown_items_or_after_the_purchase_is_done(uid, heleket):
    person = _user(uid)
    for kind, item in (("item", 99999), ("item", "boom"), ("boom", None)):
        with pytest.raises(payments_ext.PayError):
            payments_ext.methods_for(person, kind, item)
    record_subscription_payment(person, months=1)
    with pytest.raises(payments_ext.PayError) as err:
        payments_ext.methods_for(person, "subscription")
    assert err.value.code == "already_paid"
    _sql("UPDATE users SET frame_id='paid_double_gold' WHERE telegram_id=?", (person,))
    with pytest.raises(payments_ext.PayError) as err:
        payments_ext.methods_for(person, "item", 7)
    assert err.value.code == "already_owned"


# ---------------------------------------------------------------------------------------------------------------- подпись

def test_the_signature_is_md5_of_base64_body_plus_key_and_slashes_are_escaped_like_in_php(heleket):
    import base64
    import hashlib
    text = heleket.dump({"amount": "1.99", "url": "https://a.b/c", "name": "Аня"})
    assert text == '{"amount":"1.99","url":"https:\\/\\/a.b\\/c","name":"Аня"}'
    assert heleket.sign_text(text) == hashlib.md5((base64.b64encode(text.encode()).decode() + KEY).encode()).hexdigest()


def test_a_webhook_is_accepted_only_with_a_valid_signature(heleket):
    order = {"provider_uuid": "u1", "order_id": "o1", "amount_cents": 199, "user_id": 5}
    good = _webhook(heleket, order)
    assert heleket.parse_webhook(good.encode())["order_id"] == "o1"
    assert heleket.parse_webhook(good.replace("1.99", "0.01").encode()) is None, "изменили сумму — подпись не сходится"
    data = json.loads(good)
    data["sign"] = "0" * 32
    assert heleket.parse_webhook(json.dumps(data).encode()) is None
    data.pop("sign")
    assert heleket.parse_webhook(json.dumps(data).encode()) is None and heleket.parse_webhook(b"not json") is None and heleket.parse_webhook(b"[1]") is None


def test_paid_and_paid_over_mean_money_and_everything_else_does_not(heleket):
    assert heleket.outcome({"status": "paid"}) == ("paid", "paid") and heleket.outcome({"status": "paid_over"})[0] == "paid"
    for status in ("wrong_amount", "confirm_check", "process", "locked", ""):
        assert heleket.outcome({"status": status})[0] == "pending", status
    for status in ("cancel", "fail", "system_fail"):
        assert heleket.outcome({"status": status})[0] == "failed", status


# ---------------------------------------------------------------------------------------------------------------- заказ и вебхук

async def test_the_route_creates_an_order_and_returns_the_pay_link(client, uid, heleket):
    person = _user(uid)
    response = await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "item", "item_id": 27})
    body = await response.json()
    assert response.status == 200 and body["pay_url"].startswith("https://pay.example/") and heleket.calls == [(body["order_id"], 199)]
    order = pay_orders.get_order(body["order_id"])
    assert (order["user_id"], order["kind"], order["item_id"], order["amount_cents"], order["status"]) == (person, "item", 27, 199, "created")
    stranger = _user(uid + 1)
    assert (await client.get(f"/api/pay/order/{body['order_id']}", headers=_headers(stranger))).status == 404, "чужой заказ не показываем"
    mine = await (await client.get(f"/api/pay/order/{body['order_id']}", headers=_headers(person))).json()
    assert mine == {"status": "created", "fulfilled": False, "kind": "item"}


async def test_the_route_refuses_unavailable_methods_bad_items_and_needs_a_signed_in_person(client, uid, monkeypatch):
    person = _user(uid)
    monkeypatch.setattr(config, "HELEKET_API_KEY", "")
    refused = await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "subscription"})
    assert refused.status == 503 and (await refused.json())["error"] == "method_unavailable"
    assert (await client.post("/api/pay/order", json={"method": "crypto", "kind": "subscription"})).status == 401
    assert (await client.get("/api/pay/methods?kind=subscription")).status == 401
    methods = await (await client.get("/api/pay/methods?kind=subscription", headers=_headers(person))).json()
    assert [m["method"] for m in methods["methods"]] == ["stars"]


async def test_the_provider_failing_marks_the_order_and_tells_the_app(client, uid, heleket, monkeypatch):
    person = _user(uid)

    async def broken(order, amount_cents):
        raise pay_providers.ProviderError("сервис отказал")

    monkeypatch.setattr(heleket, "create_invoice", broken)
    response = await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "subscription"})
    assert response.status == 502 and (await response.json())["error"] == "provider_error"
    assert _sql("SELECT status, error FROM pay_orders WHERE user_id=?", (person,))["status"] == "failed"


async def test_a_paid_webhook_gives_the_item_exactly_once(client, uid, heleket, bot):
    person = _user(uid)
    created = await (await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    order = pay_orders.get_order(created["order_id"])
    before = get_user(person)["diamonds"]
    first = await _post_webhook(client, _webhook(heleket, order))
    assert first.status == 200 and get_user(person)["diamonds"] == before + 10
    again = await _post_webhook(client, _webhook(heleket, order))
    assert again.status == 200 and get_user(person)["diamonds"] == before + 10, "повторный вебхук ничего не добавляет"
    done = pay_orders.get_order(order["order_id"])
    assert done["status"] == "paid" and done["fulfilled_at"] and done["raw_status"] == "paid"
    assert [c.args[0] for c in bot.send_message.call_args_list] == [person] and "зачислены" in bot.send_message.call_args_list[0].args[1]
    status = await (await client.get(f"/api/pay/order/{order['order_id']}", headers=_headers(person))).json()
    assert status["status"] == "paid" and status["fulfilled"] is True


async def test_a_paid_subscription_webhook_opens_the_subscription_and_marks_the_provider(client, uid, heleket, bot):
    person = _user(uid)
    created = await (await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "subscription"})).json()
    order = pay_orders.get_order(created["order_id"])
    assert has_active_subscription(person) is False
    assert (await _post_webhook(client, _webhook(heleket, order, status="paid_over", amount="1.99"))).status == 200
    assert has_active_subscription(person) is True
    row = _sql("SELECT provider, status, amount FROM stars_payments WHERE charge_id=?", (f"heleket:{order['order_id']}",))
    assert (row["provider"], row["status"], row["amount"]) == ("heleket", "done", 0), "в журнале платежей видно, чем заплатили; звёзд тут нет"
    assert "Подписка активна" in bot.send_message.call_args_list[0].args[1]
    assert (await _post_webhook(client, _webhook(heleket, order))).status == 200
    assert _sql("SELECT COUNT(*) AS n FROM stars_payments WHERE charge_id=?", (f"heleket:{order['order_id']}",))["n"] == 1


async def test_a_bad_signature_a_wrong_amount_and_unfinished_payments_give_nothing(client, uid, heleket, bot):
    person = _user(uid)
    created = await (await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    order = pay_orders.get_order(created["order_id"])
    before = get_user(person)["diamonds"]
    forged = _webhook(heleket, order).replace('"status":"paid"', '"status":"paid"')
    data = json.loads(forged)
    data["sign"] = "f" * 32
    assert (await _post_webhook(client, json.dumps(data))).status == 400
    assert (await _post_webhook(client, _webhook(heleket, order, status="wrong_amount"))).status == 200
    assert (await _post_webhook(client, _webhook(heleket, order, status="confirm_check"))).status == 200
    assert get_user(person)["diamonds"] == before and pay_orders.get_order(order["order_id"])["status"] == "created"
    cheap = await _post_webhook(client, _webhook(heleket, order, amount="0.01"))
    assert cheap.status == 200 and get_user(person)["diamonds"] == before, "оплатили меньше, чем стоит заказ, — покупку не выдаём"
    assert pay_orders.get_order(order["order_id"])["status"] == "failed"
    assert bot.send_message.await_count >= 1, "админам ушло сообщение о неверной сумме"
    assert (await client.post("/api/pay/webhook/nobody", data=b"{}")).status == 404
    unknown = await _post_webhook(client, _webhook(heleket, {"provider_uuid": "u", "order_id": "no-such", "amount_cents": 199, "user_id": 1}))
    assert unknown.status == 200 and (await unknown.json()).get("ignored") is True


async def test_a_cancelled_payment_closes_the_order(client, uid, heleket, bot):
    person = _user(uid)
    created = await (await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "item", "item_id": 22})).json()
    order = pay_orders.get_order(created["order_id"])
    assert (await _post_webhook(client, _webhook(heleket, order, status="cancel"))).status == 200
    assert pay_orders.get_order(order["order_id"])["status"] == "failed"


async def test_a_failed_grant_asks_the_service_to_retry_and_the_admin_can_repeat_it(client, uid, heleket, bot, monkeypatch):
    import webapp.routes_admin as routes_admin
    person, admin = _user(uid), _user(uid + 1)
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    created = await (await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    order = pay_orders.get_order(created["order_id"])
    real = payments_ext._grant_item

    def boom(user_id, item_id):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(payments_ext, "_grant_item", boom)
    before = get_user(person)["diamonds"]
    failed = await _post_webhook(client, _webhook(heleket, order))
    assert failed.status == 500, "сервис получит ошибку и повторит подтверждение позже"
    stuck = pay_orders.get_order(order["order_id"])
    assert stuck["status"] == "paid" and stuck["fulfilled_at"] is None and "база недоступна" in stuck["error"]
    assert order["order_id"] in [o["order_id"] for o in pay_orders.recent_unfulfilled()]
    monkeypatch.setattr(payments_ext, "_grant_item", real)
    listing = await (await client.get("/api/admin/pay/orders", headers=_headers(admin))).json()
    assert any(o["order_id"] == order["order_id"] and not o["fulfilled"] for o in listing["orders"])
    repeated = await client.post(f"/api/admin/pay/orders/{order['order_id']}/fulfil", headers=_headers(admin))
    assert repeated.status == 200 and (await repeated.json())["fulfilled_now"] is True
    assert get_user(person)["diamonds"] == before + 10
    assert (await client.post(f"/api/admin/pay/orders/{order['order_id']}/fulfil", headers=_headers(admin))).status == 200
    assert get_user(person)["diamonds"] == before + 10, "второй раз не начисляется"
    assert (await client.get("/api/admin/pay/orders", headers=_headers(person))).status == 403
    assert (await client.post(f"/api/admin/pay/orders/{order['order_id']}/fulfil", headers=_headers(person))).status == 403


async def test_every_shop_item_can_be_granted(uid, bot):
    person = _user(uid)
    for item_id, check in ((22, lambda: True), (23, lambda: True), (24, lambda: True), (27, lambda: True), (28, lambda: True), (7, lambda: get_user(person)["frame_id"] == "paid_double_gold")):
        text = payments_ext._grant_item(person, item_id)
        assert text and check(), item_id
    with pytest.raises(ValueError):
        payments_ext._grant_item(person, 20)         # товар за монеты платежом не выдаётся


# ---------------------------------------------------------------------------------------------------------------- приложение

from pathlib import Path as _Path

_STATIC = _Path(__file__).resolve().parent.parent / "webapp" / "static"
_APP_JS = (_STATIC / "app.js").read_text(encoding="utf-8")
_INDEX = (_STATIC / "index.html").read_text(encoding="utf-8")


def test_the_app_asks_for_the_method_before_paying_for_a_subscription_and_in_the_shop():
    for marker in ('id="payMethodOverlay"', 'id="payMethodList"', 'id="payMethodWait"', 'id="payMethodReopen"', 'id="payMethodClose"'):
        assert marker in _INDEX, marker
    for needle in ("async function startPurchase", "/api/pay/methods", "/api/pay/order", "async function choosePayMethod", "async function pollPayOrder", "function initPayChoice",
                   "initPayChoice();", "pay_methods:"):
        assert needle in _APP_JS, needle
    pay = _APP_JS[_APP_JS.index("async function payTrial(source)"):][:700]
    assert 'startPurchase({ kind: "subscription"' in pay and "starsFlow: () => payTrialStars(source)" in pay
    shop = _APP_JS[_APP_JS.index('if (action === "stars")'):][:700]
    assert 'startPurchase({ kind: "item", itemId: Number(itemId)' in shop and "buyShopItemStars(itemId" in shop, "в магазине сначала выбор, звёзды — прежним путём"
    stars = _APP_JS[_APP_JS.index("async function buyShopItemStars"):][:700]
    assert "/api/shop/stars/${itemId}" in stars and "tg.openInvoice" in stars
    start = _APP_JS[_APP_JS.index("async function startPurchase"):][:1100]
    assert "methods.length <= 1" in start and "await starsFlow()" in start, "без других способов оплата звёздами идёт сразу, как раньше"


def test_the_pay_window_copy_has_no_repeated_words_and_no_gendered_forms():
    from tests.test_window_copy import shared
    title, sub, close = "Как оплатить", "Выбери удобный способ", "Не сейчас"
    assert not shared(title, sub, close)
    section = _APP_JS[_APP_JS.index("const PAY_ERRORS"):][:900] + _APP_JS[_APP_JS.index("async function choosePayMethod"):][:1500]
    assert "(а)" not in section and "(ась)" not in section


async def test_anyone_can_pay_for_a_subscription_even_when_access_is_open(client, uid, heleket):
    person = _user(uid)
    trial = await (await client.get("/api/trial", headers=_headers(person))).json()
    assert trial["trial"]["scenario"] is False and trial["trial"]["paid"] is False
    created = await client.post("/api/pay/order", headers=_headers(person), json={"method": "crypto", "kind": "subscription"})
    assert created.status == 200


def test_the_locked_message_offers_card_and_crypto_only_when_they_are_set_up(monkeypatch):
    import keyboards
    monkeypatch.setattr(keyboards, "WEBAPP_URL", "https://adam.example")
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "")
    plain = keyboards.subscription_buy_keyboard(153).inline_keyboard
    assert [b.text for row in plain for b in row] == ["⭐ Оплатить 153 Stars"]
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "id")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "key")
    both = keyboards.subscription_buy_keyboard(153).inline_keyboard
    assert [b.text for row in both for b in row] == ["⭐ Оплатить 153 Stars", "💳 Картой или криптой"]
    assert both[1][0].web_app.url == "https://adam.example/?sub=1&m=bot_locked"


# ---------------------------------------------------------------------------------------------------------------- окно «Покупки»

async def test_the_catalog_lists_everything_with_dollar_prices_methods_support_and_promos(client, uid, heleket):
    person = _user(uid)
    pay_orders.add_promo("Неделя алмазов", "Алмазы дешевле до воскресенья", None)
    catalog = await (await client.get("/api/pay/catalog", headers=_headers(person))).json()
    assert catalog["subscription"] == {"active": False, "until": None, "price_text": "$1.99", "next_text": "дальше $5.99 в месяц"}
    by_id = {i["item_id"]: i for i in catalog["items"]}
    assert set(by_id) == {7, 22, 23, 24, 27, 28}
    assert by_id[27]["usd_text"] == "$1.99" and by_id[27]["stars_text"] == "150 ⭐" and by_id[28]["usd_text"] == "$7.99" and by_id[27]["group"] == "diamonds"
    assert by_id[22]["group"] == "answers" and by_id[24]["group"] == "booster" and by_id[7]["group"] == "frame" and all(i["available"] for i in catalog["items"])
    assert [m["method"] for m in catalog["methods"]] == ["stars", "crypto"] and catalog["support"] == "@floydddo, @AlxManagement"
    assert "Неделя алмазов" in [p["title"] for p in catalog["promos"]]
    assert (await client.get("/api/pay/catalog")).status == 401


async def test_the_catalog_marks_owned_limited_items_and_an_active_subscription(client, uid, heleket):
    person = _user(uid)
    _sql("UPDATE users SET frame_id='paid_double_gold' WHERE telegram_id=?", (person,))
    record_subscription_payment(person, months=1)
    catalog = await (await client.get("/api/pay/catalog", headers=_headers(person))).json()
    frame = [i for i in catalog["items"] if i["item_id"] == 7][0]
    assert frame["available"] is False and frame["note"] == "Уже у тебя"
    assert catalog["subscription"]["active"] is True and catalog["subscription"]["until"] and catalog["subscription"]["next_text"] == "в месяц"


def test_promos_show_only_while_active_and_not_expired():
    first = pay_orders.add_promo("Короткая", "Текст", "2000-01-01 00:00:00")
    second = pay_orders.add_promo("Живая", "Текст", "2999-01-01 00:00:00")
    third = pay_orders.add_promo("Убранная", "Текст", None)
    pay_orders.remove_promo(third)
    shown = [p["id"] for p in pay_orders.list_promos()]
    assert second in shown and first not in shown and third not in shown
    all_ids = [p["id"] for p in pay_orders.list_promos(only_current=False)]
    assert {first, second, third} <= set(all_ids)
    assert pay_orders.add_promo("", "x") is None and pay_orders.add_promo("x", " ") is None


async def test_the_admin_manages_promos_and_others_cannot(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin, person = _user(uid), _user(uid + 1)
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    created = await client.post("/api/admin/pay/promos", headers=_headers(admin), json={"title": "Скидка недели", "text": "Пакеты ответов дешевле", "until": "2999-01-01 00:00:00"})
    promo_id = (await created.json())["id"]
    catalog = await (await client.get("/api/pay/catalog", headers=_headers(person))).json()
    assert promo_id in [p["id"] for p in catalog["promos"]]
    assert (await client.post("/api/admin/pay/promos", headers=_headers(admin), json={"title": "", "text": "x"})).status == 400
    assert (await client.post(f"/api/admin/pay/promos/{promo_id}/remove", headers=_headers(admin), json={})).status == 200
    catalog = await (await client.get("/api/pay/catalog", headers=_headers(person))).json()
    assert promo_id not in [p["id"] for p in catalog["promos"]]
    assert (await client.get("/api/admin/pay/promos", headers=_headers(person))).status == 403
    assert (await client.post("/api/admin/pay/promos", headers=_headers(person), json={"title": "a", "text": "b"})).status == 403


def test_the_shop_has_the_purchases_window_with_its_entry_and_the_demo_scene():
    from db.sim_lab import SCENES
    for marker in ('id="shopHubOverlay"', 'id="shopHubBtn"', 'id="shopHubItems"', 'id="shopHubSubscription"', 'id="shopHubPromos"', 'id="shopHubMethods"', 'id="shopHubSupport"', 'id="payMethodPaid"'):
        assert marker in _INDEX, marker
    for needle in ("async function openShopHub", "/api/pay/catalog", "function renderShopHub", "async function hubBuy", "function initShopHub", "initShopHub();", "shop_hub:", "HUB_GROUPS",
                   "buyShopItemStars", "pollPayOrder(true)"):
        assert needle in _APP_JS, needle
    assert "shop_hub" in {scene[0] for scene in SCENES}
    banner = _INDEX[_INDEX.index('id="shopHubBtn"'):][:400]
    assert "Цены в долларах" in banner
    hub = _APP_JS[_APP_JS.index("async function hubBuy"):][:900]
    assert 'startPurchase({ kind: "subscription"' in hub and 'startPurchase({ kind: "item"' in hub and "if (hubDemo)" in hub, "покупка идёт через выбор способа, в демо ничего не открывается"


def test_the_purchases_window_copy_is_clean():
    from tests.test_window_copy import shared
    assert not shared("Покупки", "", "Закрыть")
    section = _APP_JS[_APP_JS.index("const HUB_METHOD_HINTS"):][:1400] + _APP_JS[_APP_JS.index("function renderShopHub"):][:2400]
    assert "(а)" not in section and "(ась)" not in section
