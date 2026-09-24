"""
Просьба пользователя: покупка алмазов (премиальная валюта) за Telegram
Stars — первая платная покупка алмазов, правила расходования продумаем
отдельно. Тот же паттерн, что и у существующих Stars-покупок
(handlers/payments.py::successful_payment, см. tests/test_gift_premium.py).
"""
from types import SimpleNamespace

import handlers.payments as payments_mod
from db import add_user, get_diamonds, get_shop_item, is_payment_processed

from tests.conftest import sign_init_data


class FakeAnswerTarget:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


def _fake_payment_message(user_id, item_id, charge_id, amount=150):
    payment = SimpleNamespace(
        invoice_payload=f"diamond_pack_stars:{item_id}:{user_id}",
        telegram_payment_charge_id=charge_id,
        total_amount=amount,
    )
    msg = FakeAnswerTarget()
    msg.successful_payment = payment
    msg.from_user = SimpleNamespace(id=user_id)
    return msg


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# ТОВАРЫ В МАГАЗИНЕ
# =====================================

def test_diamond_pack_items_seeded():
    small = get_shop_item(27)
    large = get_shop_item(28)
    assert small["item_type"] == "diamond_pack_stars"
    assert small["payload"] == "10"
    assert large["item_type"] == "diamond_pack_stars"
    assert large["payload"] == "50"


# =====================================
# /api/buy/{id} — алмазы нельзя купить за Adam Coin
# =====================================

async def test_buy_route_rejects_diamond_pack_with_adam_coin(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/buy/27", headers=headers)
    assert r.status == 400
    body = await r.json()
    assert body["error"] == "use_stars_checkout"


# =====================================
# /api/shop/stars/{id} — создание инвойса
# =====================================

async def test_stars_invoice_route_accepts_diamond_pack(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    class FakeBot:
        async def create_invoice_link(self, **kwargs):
            assert kwargs["currency"] == "XTR"
            assert kwargs["payload"] == f"diamond_pack_stars:27:{uid}"
            return "https://t.me/fake_invoice"

    client.app["bot"] = FakeBot()
    r = await client.post("/api/shop/stars/27", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body["invoice_url"] == "https://t.me/fake_invoice"


async def test_stars_invoice_route_rejects_regular_shop_item(client, uid):
    """Обычный товар за Adam Coin (id=3, значок) не должен приниматься
    Stars-роутом — он не в списке разрешённых item_type."""
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/shop/stars/3", headers=headers)
    assert r.status == 404
    body = await r.json()
    assert body["error"] == "stars_item_not_found"


# =====================================
# successful_payment: начисление алмазов
# =====================================

async def test_successful_payment_grants_diamonds(uid):
    add_user(uid, "u", "Test")
    assert get_diamonds(uid) == 0
    msg = _fake_payment_message(uid, 27, f"charge_{uid}_1")

    await payments_mod.successful_payment(msg)

    assert get_diamonds(uid) == 10


async def test_successful_payment_grants_correct_amount_for_large_pack(uid):
    add_user(uid, "u", "Test")
    msg = _fake_payment_message(uid, 28, f"charge_{uid}_2")

    await payments_mod.successful_payment(msg)

    assert get_diamonds(uid) == 50


async def test_successful_payment_diamonds_marks_charge_processed(uid):
    add_user(uid, "u", "Test")
    charge_id = f"charge_{uid}_3"
    msg = _fake_payment_message(uid, 27, charge_id)

    await payments_mod.successful_payment(msg)

    assert is_payment_processed(charge_id) is True


async def test_successful_payment_diamonds_idempotent_on_duplicate_delivery(uid):
    """Telegram изредка повторно доставляет апдейт — тот же charge_id не
    должен начислить алмазы дважды (is_payment_processed guard)."""
    add_user(uid, "u", "Test")
    charge_id = f"charge_{uid}_4"
    msg1 = _fake_payment_message(uid, 27, charge_id)
    msg2 = _fake_payment_message(uid, 27, charge_id)

    await payments_mod.successful_payment(msg1)
    await payments_mod.successful_payment(msg2)

    assert get_diamonds(uid) == 10


async def test_successful_payment_diamonds_ignores_spoofed_recipient(uid):
    """payload содержит telegram_id получателя — если он не совпадает с
    реальным плательщиком (message.from_user.id), начисление не
    происходит (та же защита, что и у answer_pack_stars/booster)."""
    other_uid = uid + 1
    add_user(uid, "u", "Test")
    add_user(other_uid, "u2", "Other")
    # payload утверждает, что платил other_uid, а по факту платит uid.
    msg = _fake_payment_message(uid, 27, f"charge_{uid}_5")
    msg.successful_payment = SimpleNamespace(
        invoice_payload=f"diamond_pack_stars:27:{other_uid}",
        telegram_payment_charge_id=f"charge_{uid}_5",
        total_amount=150,
    )

    await payments_mod.successful_payment(msg)

    assert get_diamonds(uid) == 0
    assert get_diamonds(other_uid) == 0


async def test_successful_payment_diamonds_thanks_message(uid):
    add_user(uid, "u", "Test")
    msg = _fake_payment_message(uid, 27, f"charge_{uid}_6")

    await payments_mod.successful_payment(msg)

    assert len(msg.answers) == 1
    assert "💎" in msg.answers[0][0]
