"""
Конец оплаченного срока и автопродление (решение 05.11, subscription_renewal.py, subscription_flow._renewal_spec,
db/subscription.py::paid_access_until): напоминания за 7/3/1/0 дней без автопродления, честное предупреждение за 3 дня при нём,
«не получилось продлить» через 3 часа после конца, сутки запаса доступа при автопродлении, продление заранее без потери дней.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import subscription_flow as flow
import subscription_renewal as sr
from db import add_user, get_user
from db.core import connect
from db.streak import set_timezone
from db.subscription import has_active_subscription, record_subscription_payment


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _paid(uid, until, auto=False):
    add_user(uid, "p", "Аня")
    set_timezone(uid, "UTC")
    _sql("UPDATE users SET subscription_paid_until=?, subscription_first_payment_at=CURRENT_TIMESTAMP, subscription_auto_renew=?, "
         "subscription_charge_id=? WHERE telegram_id=?", (until.isoformat(), 1 if auto else 0, "ch_1" if auto else None, uid))
    return get_user(uid)


NOW = datetime(2026, 12, 1, 12, 1)


@pytest.mark.parametrize("days,expect", [(7, "sub_end_7"), (3, "sub_end_3"), (1, "sub_end_1"), (0, "sub_end_0"), (5, None), (2, None)])
def test_manual_renewal_reminders_go_7_3_1_and_0_days_before(uid, days, expect, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", True)
    user = _paid(uid, NOW + timedelta(days=days, hours=3))
    plan = sr.plan_for(user, NOW)
    if expect is None:
        assert plan is None
    else:
        key, text, keyboard = plan
        assert key.startswith(expect + ":") and "Подписка ADAM Pro заканчивается" in text and "не пропадут" in text
        assert keyboard.inline_keyboard[0][0].callback_data == "buy_subscription" and "автопродление" in keyboard.inline_keyboard[0][0].text


def test_without_autorenew_the_button_says_extend(uid, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    key, text, keyboard = sr.plan_for(_paid(uid, NOW + timedelta(days=1, hours=2)), NOW)
    assert "Продлить на 30 дней" in keyboard.inline_keyboard[0][0].text and "завтра" in text


def test_with_autorenew_one_honest_notice_and_failure_after_3_hours(uid):
    until = NOW + timedelta(days=3, hours=2)
    user = _paid(uid, until, auto=True)
    key, text, keyboard = sr.plan_for(user, NOW)
    assert key.startswith("sub_auto_notice:") and "продлится сама" in text and keyboard is None
    assert sr.plan_for(user, NOW + timedelta(days=1)) is None, "только одно предупреждение"
    assert sr.plan_for(user, until + timedelta(hours=1)) is None, "подтверждение может прийти чуть позже — не пугаем"
    key, text, _ = sr.plan_for(user, until + timedelta(hours=4))
    assert key.startswith("sub_renew_failed:") and "Не получилось продлить" in text


def test_autorenew_keeps_access_for_a_day_after_the_term(uid):
    past = datetime.utcnow() - timedelta(hours=2)
    _paid(uid, past, auto=True)
    assert has_active_subscription(uid), "сутки запаса на подтверждение списания"
    _paid(uid + 1, past, auto=False)
    assert not has_active_subscription(uid + 1)
    _paid(uid + 2, datetime.utcnow() - timedelta(hours=25), auto=True)
    assert not has_active_subscription(uid + 2)


def test_a_paying_person_can_turn_on_autorenew_and_keeps_their_days(uid, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", True)
    until = datetime.utcnow() + timedelta(days=5)
    _paid(uid, until)
    spec = flow.invoice_spec(uid)
    assert spec["recurring"] and spec["period"] == flow.SUBSCRIPTION_PERIOD_SECONDS and spec["price"] == config.SUBSCRIPTION_RENEWAL_PRICE_STARS
    assert "не пропадут" in spec["description"]
    new_until = record_subscription_payment(uid)
    assert abs((new_until - (until + timedelta(days=30))).total_seconds()) < 5, "новый срок — от конца текущего"
    _sql("UPDATE users SET subscription_auto_renew=1, subscription_charge_id='ch' WHERE telegram_id=?", (uid,))
    assert flow.invoice_spec(uid) is None, "автопродление уже включено — платить нечего"


async def test_the_job_sends_once_per_term(uid, monkeypatch):
    monkeypatch.setattr(sr, "_paid_people", lambda: [{"telegram_id": uid}])
    _paid(uid, NOW + timedelta(days=7, hours=3))
    bot = SimpleNamespace(send_message=AsyncMock(), token="t")
    assert await sr.run_renewal_reminders(bot, now_utc=NOW) == 1
    assert await sr.run_renewal_reminders(bot, now_utc=NOW + timedelta(minutes=2)) == 0
    assert bot.send_message.await_count == 1


def _card_ready(monkeypatch, key="sub_renewal", link="https://t.me/tribute/app?startapp=p102"):
    """Оплата картой включена, ключ Tribute задан, товар под цену продления привязан."""
    from db import pay_orders
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", True)
    monkeypatch.setattr(config, "TRIBUTE_API_KEY", "test-key")
    pay_orders.set_tribute_product(key, 102 if key == "sub_renewal" else 101, "ADAM Pro", 599 if key == "sub_renewal" else 199, "USD", link, "https://web")


def test_with_card_payments_on_the_reminder_also_offers_the_card(uid, monkeypatch):
    """07.11: в Mini App можно платить картой, а напоминание в боте предлагало только звёзды — продлить картой из бота было нельзя."""
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    _card_ready(monkeypatch)
    key, text, keyboard = sr.plan_for(_paid(uid, NOW + timedelta(days=3, hours=2)), NOW)
    rows = keyboard.inline_keyboard
    assert rows[0][0].callback_data == "buy_subscription", "звёзды остаются первой кнопкой"
    assert rows[1][0].url == "https://t.me/tribute/app?startapp=p102" and "Картой" in rows[1][0].text and "$5.99" in rows[1][0].text


def test_without_external_payments_the_reminder_has_only_stars(uid, monkeypatch):
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    _card_ready(monkeypatch)
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", False)
    key, text, keyboard = sr.plan_for(_paid(uid, NOW + timedelta(days=3, hours=2)), NOW)
    assert len(keyboard.inline_keyboard) == 1


async def test_the_bot_pay_button_offers_the_card_before_the_stars_invoice(uid, monkeypatch):
    """Кнопка «Оплатить» в боте: при включённой карте сначала сообщение с кнопкой «Картой», потом обычный счёт в звёздах."""
    import handlers.payments as hp
    from db import pay_orders
    add_user(uid, "n", "Новичок")
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    spec = {"recurring": False, "title": "ADAM Pro", "description": "30 дней", "payload": "p", "price": 153, "snapshot": {"phase": "x", "day": 1}}
    monkeypatch.setattr(flow, "invoice_spec", lambda user_id: spec)
    key = pay_orders.price_key("subscription", user_id=uid)
    _card_ready(monkeypatch, key=key, link=f"https://t.me/tribute/app?startapp={key}")
    message = SimpleNamespace(answer=AsyncMock(), answer_invoice=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=uid), message=message, answer=AsyncMock(), bot=None)
    await hp.buy_subscription(callback)
    card_markup = message.answer.await_args.kwargs["reply_markup"]
    assert card_markup.inline_keyboard[0][0].url == f"https://t.me/tribute/app?startapp={key}" and "Картой" in card_markup.inline_keyboard[0][0].text
    message.answer_invoice.assert_awaited_once()
