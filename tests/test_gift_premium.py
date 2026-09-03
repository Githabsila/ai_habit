"""
Подарить Premium другу (handlers/payments.py) — раньше Premium можно было
только купить себе. Платит giver, Premium получает recipient_id из payload
"gift_premium:<recipient_id>:<giver_id>", поэтому successful_payment для
этой ветки НЕ проверяет paid_user_id == message.from_user.id, в отличие
от остальных веток (там это была бы защита от подмены, здесь наоборот —
суть фичи).
"""
from types import SimpleNamespace

import handlers.payments as payments_mod
from db import add_user, get_user, give_premium_admin, is_payment_processed


class FakeAnswerTarget:
    def __init__(self):
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def _fake_payment_message(giver_id, recipient_id, charge_id, bot):
    payment = SimpleNamespace(
        invoice_payload=f"gift_premium:{recipient_id}:{giver_id}",
        telegram_payment_charge_id=charge_id,
        total_amount=150,
    )
    msg = FakeAnswerTarget()
    msg.successful_payment = payment
    msg.from_user = SimpleNamespace(id=giver_id)
    msg.bot = bot
    return msg


# ===================== _offer_gift_invoice (выбор получателя) =====================

async def test_cannot_gift_to_self(uid):
    add_user(uid, "tester", "Test")
    target = FakeAnswerTarget()

    await payments_mod._offer_gift_invoice(target, uid, uid)

    assert "себе" in target.answers[0][0]


async def test_cannot_gift_to_unknown_user(uid):
    target = FakeAnswerTarget()
    unknown_id = uid + 1

    await payments_mod._offer_gift_invoice(target, unknown_id, uid)

    assert "/start" in target.answers[0][0]


async def test_cannot_gift_to_already_premium_user(uid):
    add_user(uid, "tester", "Test")
    recipient = uid + 1
    add_user(recipient, "friend", "Friend")
    give_premium_admin(recipient)
    target = FakeAnswerTarget()

    await payments_mod._offer_gift_invoice(target, recipient, uid)

    assert "уже есть Premium" in target.answers[0][0]


async def test_valid_gift_shows_confirm_button(uid):
    add_user(uid, "tester", "Test")
    recipient = uid + 1
    add_user(recipient, "friend", "Friend")
    target = FakeAnswerTarget()

    await payments_mod._offer_gift_invoice(target, recipient, uid)

    text, markup = target.answers[0]
    assert "Дарим Premium" in text
    assert markup is not None


# ===================== successful_payment: gift_premium ветка =====================

async def test_gift_premium_grants_to_recipient_not_payer(uid):
    giver = uid
    recipient = uid + 1
    add_user(giver, "giver", "Giver")
    add_user(recipient, "recipient", "Recipient")
    bot = FakeBot()
    msg = _fake_payment_message(giver, recipient, f"charge_{uid}_1", bot)

    await payments_mod.successful_payment(msg)

    assert get_user(recipient)["premium"] == 1
    assert get_user(giver)["premium"] == 0


async def test_gift_premium_notifies_recipient(uid):
    giver = uid
    recipient = uid + 1
    add_user(giver, "giver", "Giver")
    add_user(recipient, "recipient", "Recipient")
    bot = FakeBot()
    msg = _fake_payment_message(giver, recipient, f"charge_{uid}_2", bot)

    await payments_mod.successful_payment(msg)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == recipient
    assert "подарил" in bot.sent[0][1]


async def test_gift_premium_marks_charge_processed(uid):
    giver = uid
    recipient = uid + 1
    add_user(giver, "giver", "Giver")
    add_user(recipient, "recipient", "Recipient")
    bot = FakeBot()
    charge_id = f"charge_{uid}_3"
    msg = _fake_payment_message(giver, recipient, charge_id, bot)

    await payments_mod.successful_payment(msg)

    assert is_payment_processed(charge_id) is True


async def test_gift_premium_survives_recipient_blocked_bot(uid):
    """Получатель мог заблокировать бота — send_message упадёт, но сама
    выдача Premium должна была уже произойти и не откатываться."""
    giver = uid
    recipient = uid + 1
    add_user(giver, "giver", "Giver")
    add_user(recipient, "recipient", "Recipient")

    class BrokenBot(FakeBot):
        async def send_message(self, chat_id, text):
            raise Exception("bot was blocked by the user")

    bot = BrokenBot()
    msg = _fake_payment_message(giver, recipient, f"charge_{uid}_4", bot)

    await payments_mod.successful_payment(msg)  # не должно бросить исключение

    assert get_user(recipient)["premium"] == 1
