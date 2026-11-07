"""
Оплата картой через Tribute (pay_tribute.py, решение владельца 28.10.2026). Ссылка у товара своя, человека называет вебхук: проверяем подпись, привязку товаров с проверкой цены, выдачу
один раз по purchase_id, оплату по ссылке не из приложения, неизвестные товары и суммы, возврат и админские маршруты.
"""
import hashlib
import hmac
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

KEY = "tribute-test-key"
_counter = itertools.count(870_100_000, 100)
_purchase = itertools.count(5000)


@pytest.fixture
def uid():
    return next(_counter)


@pytest.fixture(autouse=True)
def _clean_mapping():
    """База общая на весь прогон: привязка товаров из соседнего теста не должна влиять на следующий."""
    conn = connect()
    try:
        conn.execute("DELETE FROM pay_tribute_products")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def tribute(monkeypatch):
    monkeypatch.setattr(config, "TRIBUTE_API_KEY", KEY)
    return pay_providers.PROVIDERS["tribute"]


@pytest.fixture
def bot(client, monkeypatch):
    admin = 777000111
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    fake = SimpleNamespace(send_message=AsyncMock(), token="")
    client.app["bot"] = fake
    fake.admin = admin
    return fake


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
    add_user(uid_, f"t{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    return uid_


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _map(key, product_id, cents=None):
    purchase = pay_orders.key_to_purchase(key)
    pay_orders.set_tribute_product(key, product_id, f"Товар {key}", cents or purchase[2], "USD", f"https://t.me/tribute_bot/app?startapp=p{product_id}", f"https://web.tribute.tg/p/{product_id}")


def _event(user_id, product_id, amount, currency="usd", name="new_digital_product", purchase_id=None):
    return {"name": name, "created_at": "2026-10-28T10:00:00Z", "sent_at": "2026-10-28T10:00:01Z",
            "payload": {"product_id": product_id, "product_name": "Товар", "amount": amount, "currency": currency, "telegram_user_id": user_id,
                        "purchase_id": purchase_id if purchase_id is not None else next(_purchase), "transaction_id": 1, "purchase_created_at": "2026-10-28T10:00:00Z"}}


async def _post(client, event, key=KEY, sign=True):
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["trbt-signature"] = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    return await client.post("/api/pay/webhook/tribute", data=body, headers=headers)


# ---------------------------------------------------------------------------------------------------------------- подпись и доступность

def test_the_signature_is_hmac_sha256_of_the_body_with_the_api_key(tribute):
    import base64
    body = b'{"name":"new_digital_product"}'
    digest = hmac.new(KEY.encode(), body, hashlib.sha256)
    assert tribute.parse_webhook(body, {"trbt-signature": digest.hexdigest()}) == {"name": "new_digital_product"}
    assert tribute.parse_webhook(body, {"trbt-signature": digest.hexdigest().upper()}) is not None
    assert tribute.parse_webhook(body, {"trbt-signature": base64.b64encode(digest.digest()).decode()}) is not None
    assert tribute.parse_webhook(body, {"trbt-signature": "0" * 64}) is None
    assert tribute.parse_webhook(body + b" ", {"trbt-signature": digest.hexdigest()}) is None, "тело изменили — подпись не сходится"
    assert tribute.parse_webhook(body, {}) is None
    assert tribute.parse_webhook(b"[1]", {"trbt-signature": hmac.new(KEY.encode(), b"[1]", hashlib.sha256).hexdigest()}) is None


def test_the_card_method_appears_only_for_what_is_linked_to_a_tribute_product(uid, tribute, monkeypatch):
    person = _user(uid)
    monkeypatch.setattr(config, "HELEKET_MERCHANT_ID", "")
    monkeypatch.setattr(config, "HELEKET_API_KEY", "")
    assert [m["method"] for m in payments_ext.methods_for(person, "item", 27)] == ["stars"]
    _map("item_27", 901)
    methods = payments_ext.methods_for(person, "item", 27)
    assert [m["method"] for m in methods] == ["stars", "card"] and methods[1]["price_text"] == "$1.99" and methods[1]["label"] == "Картой"
    assert [m["method"] for m in payments_ext.methods_for(person, "item", 28)] == ["stars"], "для других цен товара в Tribute ещё нет"
    assert [m["method"] for m in payments_ext.methods_for(person, "subscription")] == ["stars"]
    _map("sub_first", 902)
    assert [m["method"] for m in payments_ext.methods_for(person, "subscription")] == ["stars", "card"]
    monkeypatch.setattr(config, "TRIBUTE_API_KEY", "")
    assert [m["method"] for m in payments_ext.methods_for(person, "item", 27)] == ["stars"], "нет ключа API — карты нет"


async def test_choosing_the_card_returns_the_product_link_and_makes_an_order(uid, tribute, monkeypatch):
    person = _user(uid)
    monkeypatch.setattr(config, "HELEKET_API_KEY", "")
    with pytest.raises(payments_ext.PayError) as err:
        await payments_ext.start_order(person, "card", "item", 27)
    assert err.value.code == "method_unavailable"
    _map("item_27", 901)
    created = await payments_ext.start_order(person, "card", "item", 27)
    assert created["pay_url"] == "https://t.me/tribute_bot/app?startapp=p901"
    order = pay_orders.get_order(created["order_id"])
    assert (order["provider"], order["method"], order["price_key"], order["status"]) == ("tribute", "card", "item_27", "created")


# ---------------------------------------------------------------------------------------------------------------- покупка

async def test_a_purchase_gives_the_item_once_even_if_the_webhook_repeats(client, uid, tribute, bot):
    person = _user(uid)
    _map("item_27", 901)
    created = await payments_ext.start_order(person, "card", "item", 27)
    event = _event(person, 901, 199)
    before = get_user(person)["diamonds"]
    assert (await _post(client, event)).status == 200
    assert get_user(person)["diamonds"] == before + 10
    again = await _post(client, event)
    assert again.status == 200 and (await again.json()).get("duplicate") is True and get_user(person)["diamonds"] == before + 10, "повтор того же purchase_id ничего не добавляет"
    order = pay_orders.get_order(created["order_id"])
    assert order["status"] == "paid" and order["fulfilled_at"] and order["provider_uuid"] == str(event["payload"]["purchase_id"])
    status = await (await client.get(f"/api/pay/order/{created['order_id']}", headers=_headers(person))).json()
    assert status["fulfilled"] is True


async def test_a_retry_after_a_failed_grant_reuses_the_same_order(client, uid, tribute, bot, monkeypatch):
    """Начисление упало → вебхук ответил 500 → Tribute повторил. Повтор должен довести до конца ТОТ ЖЕ заказ, а не завести второй
    (иначе первый висит «оплачен, не начислен», и ручная выдача из админки даёт покупку дважды)."""
    person = _user(uid)
    _map("item_28", 903)
    event = _event(person, 903, 799)
    before = get_user(person)["diamonds"]
    real_grant = payments_ext._grant_item
    monkeypatch.setattr(payments_ext, "_grant_item", lambda *a: (_ for _ in ()).throw(RuntimeError("база занята")))
    assert (await _post(client, event)).status == 500
    monkeypatch.setattr(payments_ext, "_grant_item", real_grant)
    assert (await _post(client, event)).status == 200
    assert get_user(person)["diamonds"] == before + 50
    count = _sql("SELECT COUNT(*) AS n FROM pay_orders WHERE user_id=? AND provider='tribute'", (person,))["n"]
    assert count == 1, "повтор вебхука не должен создавать второй заказ"
    unfulfilled = _sql("SELECT COUNT(*) AS n FROM pay_orders WHERE user_id=? AND status='paid' AND fulfilled_at IS NULL", (person,))["n"]
    assert unfulfilled == 0


async def test_two_identical_webhooks_at_once_give_the_purchase_once(client, uid, tribute, bot):
    import asyncio
    person = _user(uid)
    _map("sub_first", 902)
    event = _event(person, 902, 199)
    first, second = await asyncio.gather(_post(client, event), _post(client, event))
    assert first.status == second.status == 200
    count = _sql("SELECT COUNT(*) AS n FROM pay_orders WHERE user_id=? AND provider='tribute'", (person,))["n"]
    assert count == 1, "одновременный повтор не должен заводить второй заказ"
    texts = [c.args[1] for c in bot.send_message.call_args_list if c.args[0] == person and "Подписка активна" in c.args[1]]
    assert len(texts) == 1


async def test_a_purchase_from_a_link_outside_the_app_is_still_given(client, uid, tribute, bot):
    person = _user(uid)
    _map("item_28", 903)
    before = get_user(person)["diamonds"]
    assert (await _post(client, _event(person, 903, 799))).status == 200
    assert get_user(person)["diamonds"] == before + 50
    order = _sql("SELECT * FROM pay_orders WHERE user_id=? AND provider='tribute'", (person,))
    assert order["status"] == "paid" and order["fulfilled_at"] and order["kind"] == "item" and order["item_id"] == 28


async def test_a_paid_subscription_opens_access_and_the_text_goes_to_the_person(client, uid, tribute, bot):
    person = _user(uid)
    _map("sub_first", 902)
    assert has_active_subscription(person) is False
    assert (await _post(client, _event(person, 902, 199))).status == 200
    assert has_active_subscription(person) is True
    assert "Подписка активна" in bot.send_message.call_args_list[-1].args[1]
    record_subscription_payment(person, months=1)
    _map("sub_renewal", 904)
    assert pay_orders.price_key("subscription", user_id=person) == "sub_renewal", "после первой покупки — полная цена"


async def test_unknown_products_wrong_amounts_and_strangers_give_nothing_but_warn_the_admin(client, uid, tribute, bot):
    person = _user(uid)
    _map("item_27", 901)
    before = get_user(person)["diamonds"]
    unknown = await _post(client, _event(person, 99999, 199))
    assert unknown.status == 200 and (await unknown.json()).get("ignored") is True
    assert (await _post(client, _event(person, 901, 99))).status == 200, "оплатили меньше, чем стоит"
    assert (await _post(client, _event(person, 901, 199, currency="eur"))).status == 200, "другая валюта"
    assert (await _post(client, _event(uid + 77, 901, 199))).status == 200, "человека нет в базе"
    no_id = _event(person, 901, 199)
    no_id["payload"].pop("purchase_id")
    assert (await _post(client, no_id)).status == 200
    assert get_user(person)["diamonds"] == before
    assert bot.send_message.await_count == 5 and all(c.args[0] == bot.admin for c in bot.send_message.call_args_list)


async def test_a_bad_signature_is_refused_and_other_events_are_ignored(client, uid, tribute, bot):
    person = _user(uid)
    _map("item_27", 901)
    before = get_user(person)["diamonds"]
    assert (await _post(client, _event(person, 901, 199), key="wrong-key")).status == 400
    assert (await _post(client, _event(person, 901, 199), sign=False)).status == 400
    assert get_user(person)["diamonds"] == before
    other = await _post(client, {"name": "new_subscription", "created_at": "x", "sent_at": "y", "payload": {"subscription_id": 1}})
    assert other.status == 200 and (await other.json()).get("ignored") is True


async def test_a_refund_marks_the_order_and_tells_the_admin_but_does_not_take_access_back(client, uid, tribute, bot):
    person = _user(uid)
    _map("item_27", 901)
    event = _event(person, 901, 199)
    await _post(client, event)
    refund = _event(person, 901, 199, name="digital_product_refund", purchase_id=event["payload"]["purchase_id"])
    assert (await _post(client, refund)).status == 200
    order = _sql("SELECT status FROM pay_orders WHERE user_id=? AND provider='tribute'", (person,))
    assert order["status"] == "refunded"
    assert "вернул оплату" in bot.send_message.call_args_list[-1].args[1]
    sent = bot.send_message.await_count
    await _post(client, refund)
    assert bot.send_message.await_count == sent, "повтор возврата второй раз не сообщает"
    assert get_user(person)["diamonds"] >= 10


# ---------------------------------------------------------------------------------------------------------------- админка

async def test_registering_a_product_checks_price_currency_and_status(uid, tribute, monkeypatch):
    async def fake(product_id):
        products = {
            1: {"id": 1, "name": "Алмазы", "amount": 199, "currency": "USD", "status": "approved", "link": "https://t.me/tribute_bot/app?startapp=p1", "webLink": "https://web.tribute.tg/p/1"},
            2: {"id": 2, "name": "Дорого", "amount": 299, "currency": "USD", "status": "approved", "link": "l", "webLink": "w"},
            3: {"id": 3, "name": "Рубли", "amount": 199, "currency": "RUB", "status": "approved", "link": "l", "webLink": "w"},
            4: {"id": 4, "name": "Ждёт", "amount": 199, "currency": "USD", "status": "pending", "link": "l", "webLink": "w"},
        }
        return (products[product_id], None) if product_id in products else (None, "такого товара нет")

    monkeypatch.setattr(tribute, "fetch_product", fake)
    assert await tribute.register_product("item_27", 1) == {"ok": True}
    assert pay_orders.tribute_product("item_27")["link"].endswith("startapp=p1")
    assert (await tribute.register_product("item_27", 2))["error"] == "amount_mismatch"
    assert (await tribute.register_product("item_27", 3))["error"] == "amount_mismatch"
    assert (await tribute.register_product("item_27", 4))["error"] == "not_approved"
    assert (await tribute.register_product("item_27", 9))["error"] == "product_not_found"
    assert (await tribute.register_product("item_999", 1))["error"] == "unknown_key"


async def test_the_admin_routes_list_what_is_needed_and_link_or_unlink_products(client, uid, tribute, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin, person = _user(uid), _user(uid + 1)
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])

    async def fake_fetch(product_id):
        return {"id": product_id, "name": "Подписка", "amount": 199, "currency": "USD", "status": "approved", "link": "https://t.me/x", "webLink": "https://web/x"}, None

    async def fake_list(size=100):
        return [{"id": 7, "name": "Подписка", "amount": 199, "currency": "USD", "status": "approved"}]

    monkeypatch.setattr(tribute, "fetch_product", fake_fetch)
    monkeypatch.setattr(tribute, "list_products", fake_list)
    overview = await (await client.get("/api/admin/pay/tribute", headers=_headers(admin))).json()
    assert overview["configured"] is True and overview["webhook_url"].endswith("/api/pay/webhook/tribute")
    assert [n["key"] for n in overview["needed"]][:2] == ["sub_first", "sub_renewal"] and len(overview["needed"]) == 8
    assert overview["tribute_products"][0]["price"] == "1.99 USD"
    ok = await client.post("/api/admin/pay/tribute/products", headers=_headers(admin), json={"key": "sub_first", "product_id": 7})
    assert ok.status == 200 and pay_orders.tribute_product("sub_first")["product_id"] == 7
    removed = await client.post("/api/admin/pay/tribute/products", headers=_headers(admin), json={"key": "sub_first", "product_id": ""})
    assert (await removed.json())["removed"] is True and pay_orders.tribute_product("sub_first") is None
    assert (await client.post("/api/admin/pay/tribute/products", headers=_headers(admin), json={"key": "boom", "product_id": 1})).status == 400
    assert (await client.post("/api/admin/pay/tribute/products", headers=_headers(admin), json={"key": "sub_first", "product_id": "x"})).status == 400
    assert (await client.get("/api/admin/pay/tribute", headers=_headers(person))).status == 403
    assert (await client.post("/api/admin/pay/tribute/products", headers=_headers(person), json={"key": "sub_first", "product_id": 7})).status == 403


def test_the_admin_panel_has_the_payment_card_with_tribute_products_and_promos():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")
    for needle in ("Оплата: карта, крипта, акции", 'id="payAdminBox"', "loadPayAdmin", "/api/admin/pay/tribute", "/api/admin/pay/tribute/products", "/api/admin/pay/promos", "data-tribute-save", "promoAddBtn", "tributeAutoLinkBtn", "/api/admin/pay/tribute/auto-link"):
        assert needle in panel, needle


async def test_auto_link_matches_each_price_to_one_product_by_price_and_name(client, uid, tribute, monkeypatch):
    """«Привязать автоматически»: 8 товаров владельца (как на скриншоте 06.11) встают на свои цены, хотя у трёх одна цена $1.99."""
    import webapp.routes_admin as routes_admin
    admin = _user(uid)
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    for key, *_ in pay_orders.all_price_keys():
        pay_orders.remove_tribute_product(key)
    remote = [
        {"id": 101, "name": "ADAM PRO - доступ первый месяц", "amount": 199},
        {"id": 102, "name": "ADAM Pro - 30 дней", "amount": 599},
        {"id": 103, "name": "+50 ответов Adam", "amount": 199},
        {"id": 104, "name": "+100 ответов Adam", "amount": 399},
        {"id": 105, "name": "Бустер X2 Adam coin на 24 часа", "amount": 129},
        {"id": 106, "name": "10 алмазов", "amount": 199},
        {"id": 107, "name": "50 алмазов", "amount": 799},
        {"id": 108, "name": "Рамка Double Gold", "amount": 399},
        {"id": 109, "name": "Черновик без цены", "amount": 999},
    ]
    for p in remote:
        p.update(currency="USD", status="approved", link=f"https://t.me/tribute/app?startapp=p{p['id']}", webLink="https://web")

    async def fake_list(size=100):
        return remote

    async def fake_fetch(product_id):
        found = [p for p in remote if p["id"] == product_id]
        return (found[0], None) if found else (None, "нет")

    monkeypatch.setattr(tribute, "list_products", fake_list)
    monkeypatch.setattr(tribute, "fetch_product", fake_fetch)
    resp = await client.post("/api/admin/pay/tribute/auto-link", headers=_headers(admin))
    assert resp.status == 200
    report = {row["key"]: row for row in (await resp.json())["results"]}
    expected = {"sub_first": 101, "sub_renewal": 102, "item_22": 103, "item_23": 104, "item_24": 105, "item_27": 106, "item_28": 107, "item_7": 108}
    for key, product_id in expected.items():
        assert report[key]["status"] == "linked", (key, report[key])
        assert pay_orders.tribute_product(key)["product_id"] == product_id
    # повторный запуск ничего не трогает
    again = {row["key"]: row["status"] for row in (await (await client.post("/api/admin/pay/tribute/auto-link", headers=_headers(admin))).json())["results"]}
    assert set(again.values()) == {"already"}
    assert (await client.post("/api/admin/pay/tribute/auto-link", headers=_headers(_user(uid + 1)))).status == 403


async def test_auto_link_skips_prices_with_no_or_two_matching_products(tribute, monkeypatch):
    for key, *_ in pay_orders.all_price_keys():
        pay_orders.remove_tribute_product(key)
    remote = [{"id": 201, "name": "10 алмазов", "amount": 199}, {"id": 202, "name": "10 алмазов (копия)", "amount": 199},
              {"id": 203, "name": "Рамка Double Gold", "amount": 499}]
    for p in remote:
        p.update(currency="USD", status="approved")

    async def fake_list(size=100):
        return remote

    monkeypatch.setattr(tribute, "list_products", fake_list)
    report = {row["key"]: row["status"] for row in await tribute.auto_link()}
    assert report["item_27"] == "ambiguous" and report["item_7"] == "not_found" and report["sub_first"] == "not_found"
    assert pay_orders.tribute_product("item_27") is None and pay_orders.tribute_product("item_7") is None


def test_purchases_window_and_pay_choice_open_above_the_shop_screen():
    """07.11, владелец: «нажимаю баннер — ничего не происходит». Баннер «Подписка и покупки» (и баланс 💎) лежат на экране «Магазин»
    (.subpage-overlay, z-index 1850), а окно «Покупки» открывалось под ним (1655) — его просто не было видно. Окно выбора оплаты — тоже."""
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "webapp" / "static"
    css = (static / "style.css").read_text(encoding="utf-8")
    index = (static / "index.html").read_text(encoding="utf-8")
    z = lambda sel: int(re.search(re.escape(sel) + r"\{[^}]*z-index:(\d+)", css).group(1))
    shop = max(z(".subpage-overlay"), z(".subpage-overlay--nested"))       # «Магазин» открывается вложенным слоем (1860)
    assert index.index('id="shopOverlay"') < index.index('id="shopHubBtn"') < index.index('id="shopHubOverlay"'), "баннер — внутри «Магазина»"
    assert z(".hub-overlay") > shop and z(".pay-overlay") > z(".hub-overlay"), "«Покупки» над «Магазином», выбор оплаты над «Покупками»"
    assert z(".pay-overlay") < 1880, "уведомления (.toast, 1880) остаются поверх — ошибки оплаты видны"


async def test_review_the_first_month_product_does_not_renew_someone_who_already_paid(client, uid, tribute, bot):
    """Прогон 07.11, находка P2: товар «первый месяц» ($1.99) — одна ссылка на всех. Уже платившему он продлевал подписку на месяц по
    вводной цене сколько угодно раз. Теперь такую покупку не выдаём, а пишем админам (вернуть деньги или выдать вручную)."""
    _user(uid)
    _map("sub_first", 902)
    record_subscription_payment(uid, months=1)                     # уже платил
    before = get_user(uid)["subscription_paid_until"]
    response = await _post(client, _event(uid, 902, 199))
    assert response.status == 200 and (await response.json()).get("ignored") is True
    assert get_user(uid)["subscription_paid_until"] == before, "вводная цена второй раз не продлевает"
    assert "первый месяц" in bot.send_message.await_args.args[1]


async def test_review_the_first_month_product_still_works_for_a_newcomer(client, uid, tribute, bot):
    _user(uid)
    _map("sub_first", 902)
    response = await _post(client, _event(uid, 902, 199))
    assert response.status == 200 and has_active_subscription(uid)
