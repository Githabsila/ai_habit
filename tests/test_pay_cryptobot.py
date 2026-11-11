"""
Crypto Bot (pay_cryptobot.py, 11.11): запасная оплата криптой прямо в Telegram. Запрос к Crypto Pay API подменяем — проверяем наш путь:
счёт в долларах с нашим номером заказа, подпись вебхука (HMAC-SHA256 с ключом SHA256(токен)), выдача ровно один раз, неверная сумма.
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
from db import add_user, get_user, pay_orders
from db.core import connect
from tests.conftest import sign_init_data

TOKEN = "12345:test-crypto-pay-token"
_counter = itertools.count(875_000_000, 100)
_invoice = itertools.count(1)


@pytest.fixture
def uid():
    return next(_counter)


@pytest.fixture
def cryptobot(monkeypatch):
    monkeypatch.setattr(config, "CRYPTOBOT_API_TOKEN", TOKEN)
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", True)
    calls = []
    provider = pay_providers.PROVIDERS["cryptobot"]

    async def fake_call(method, body):
        calls.append((method, body))
        n = next(_invoice)
        return {"ok": True, "result": {"invoice_id": 9000 + n, "status": "active", "bot_invoice_url": f"https://t.me/CryptoBot?start=IV{n}"}}

    monkeypatch.setattr(provider, "_call", fake_call)
    return calls


@pytest.fixture
def bot(client, monkeypatch):
    fake = SimpleNamespace(send_message=AsyncMock(), token="")
    client.app["bot"] = fake
    monkeypatch.setattr(config, "ADMIN_IDS", [777000444])
    return fake


def _user(uid_):
    add_user(uid_, f"c{uid_}", "Аня")
    conn = connect()
    try:
        conn.execute("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
        conn.commit()
    finally:
        conn.close()
    return uid_


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _body(order, amount=None, status="paid", update_type="invoice_paid"):
    return json.dumps({"update_id": 1, "update_type": update_type, "request_date": "2026-11-11T10:00:00.000Z", "payload": {
        "invoice_id": 1, "status": status, "currency_type": "fiat", "fiat": "USD", "amount": amount or f"{order['amount_cents'] / 100:.2f}",
        "paid_asset": "USDT", "paid_amount": "1.99", "payload": order["order_id"]}}).encode()


def _sign(raw, token=TOKEN):
    return hmac.new(hashlib.sha256(token.encode()).digest(), raw, hashlib.sha256).hexdigest()


async def _hook(client, raw, signature=None):
    return await client.post("/api/pay/webhook/cryptobot", data=raw,
                              headers={"Content-Type": "application/json", "crypto-pay-api-signature": signature or _sign(raw)})


async def test_crypto_goes_to_crypto_bot_with_our_order_number(client, uid, cryptobot, bot):
    _user(uid)
    methods = (await (await client.get("/api/pay/methods?kind=item&item_id=27", headers=_headers(uid))).json())["methods"]
    crypto = [m for m in methods if m["method"] == "crypto"]
    assert crypto and crypto[0]["price_text"] == "$1.99"
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    assert created["pay_url"].startswith("https://t.me/CryptoBot")          # счёт открывается прямо в Telegram
    method, body = cryptobot[-1]
    assert method == "createInvoice" and body["currency_type"] == "fiat" and body["fiat"] == "USD" and body["amount"] == "1.99"
    assert body["payload"] == created["order_id"]
    assert pay_orders.get_order(created["order_id"])["provider"] == "cryptobot"


async def test_paid_webhook_grants_once_and_checks_the_signature_and_sum(client, uid, cryptobot, bot):
    _user(uid)
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    order = pay_orders.get_order(created["order_id"])
    before = int(get_user(uid)["diamonds"] or 0)
    raw = _body(order)
    assert (await _hook(client, raw, signature=_sign(raw, "чужой токен"))).status == 400, "чужая подпись — отказ"
    assert int(get_user(uid)["diamonds"] or 0) == before
    assert (await _hook(client, raw)).status == 200
    assert int(get_user(uid)["diamonds"] or 0) == before + 10
    assert (await _hook(client, raw)).status == 200
    assert int(get_user(uid)["diamonds"] or 0) == before + 10, "повтор вебхука не начисляет второй раз"

    created2 = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "crypto", "kind": "item", "item_id": 27})).json()
    order2 = pay_orders.get_order(created2["order_id"])
    cheap = _body(order2, amount="0.50")
    assert (await (await _hook(client, cheap)).json()).get("ignored") is True
    assert int(get_user(uid)["diamonds"] or 0) == before + 10, "другая сумма — не выдаём"
    assert pay_orders.get_order(order2["order_id"])["status"] == "failed"


async def test_crypto_bot_is_hidden_without_a_token(client, uid, monkeypatch):
    monkeypatch.setattr(config, "CRYPTOBOT_API_TOKEN", "")
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", True)
    assert pay_providers.provider_for("crypto") is None or pay_providers.provider_for("crypto").name != "cryptobot"
    raw = b'{"update_type": "invoice_paid", "payload": {}}'
    assert pay_providers.PROVIDERS["cryptobot"].parse_webhook(raw, {"crypto-pay-api-signature": "x"}) is None
