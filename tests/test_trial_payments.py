"""
Триал, оплата (ТЗ владельца 30.08 п.13): после платежа Адам говорит по дню и отметкам — оплаты мало, закрытый канал только с двумя днями
ударного режима подряд; серия набрана — ссылка сразу. Плюс требования Telegram к ботам с платежами: /paysupport, /terms и возврат Stars.
"""
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import config
import subscription_flow as flow
import trial_messages as tm
from db import add_user, get_payment, get_user, list_user_payments, set_timezone, start_trial_mode
from db import trial as trial_db
from db.access import shift_trial_day
from db.core import connect
from db.subscription import has_active_subscription
from aiogram.types import Message
from handlers import payments as pay
from middlewares.access_control import AccessControlMiddleware

_counter = itertools.count(859_500_000, 100)
ADMIN = 777


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _person(uid_, day):
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    start_trial_mode(uid_, ADMIN)
    shift_trial_day(uid_, day, ADMIN)
    return uid_


def _mark(uid_, days_ago, streak_after):
    day = (datetime.now(ZoneInfo("UTC")).date() - timedelta(days=days_ago)).isoformat()
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', ?)", (uid_, day, streak_after))
    if days_ago == 0:
        _sql("UPDATE users SET streak=? WHERE telegram_id=?", (streak_after, uid_))


def _message(uid_, charge, payload=None, amount=153):
    payment = SimpleNamespace(telegram_payment_charge_id=charge, invoice_payload=payload or f"subscription:{uid_}", total_amount=amount)
    return SimpleNamespace(successful_payment=payment, from_user=SimpleNamespace(id=uid_), answer=AsyncMock(), bot=SimpleNamespace(token=""), text="")


# ---------------------------------------------------------------------------------------------------------------- сообщение после оплаты

async def test_payment_on_day_one_asks_for_two_days_of_rhythm(uid):
    _person(uid, 1)
    text, snap, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert has_active_subscription(uid) and link is None and snap["phase"] == "paid"
    assert text.startswith("✅ Доступ продлён до ") and "Закрытый канал открывается за 2 дня ударного режима подряд" in text
    until = (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y")
    assert until in text or (datetime.now() + timedelta(days=29)).strftime("%d.%m.%Y") in text or (datetime.now() + timedelta(days=31)).strftime("%d.%m.%Y") in text


async def test_payment_on_day_two_depends_on_yesterdays_and_todays_marks(uid):
    _person(uid, 2)
    text, _, _ = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert "Оплаты мало, чтобы попасть в закрытый канал" in text and "сегодня и завтра" in text
    _sql("UPDATE users SET subscription_paid_until=NULL WHERE telegram_id=?", (uid,))
    _mark(uid, 1, 1)
    text, _, _ = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert "Сначала отметь привычку сегодня" in text


async def test_a_ready_streak_gets_the_channel_link_at_once(uid, monkeypatch):
    _person(uid, 2)
    _mark(uid, 1, 1)
    _mark(uid, 0, 2)
    monkeypatch.setattr(config, "CLOSED_CHANNEL_ID", "-100123")
    grant = AsyncMock(return_value="https://t.me/+abc")
    monkeypatch.setattr(flow, "try_grant_channel_access", grant)
    text, snap, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert link == "https://t.me/+abc" and "https://t.me/+abc" in text and "2 дня ударного режима подряд — вот ссылка" in text
    grant.assert_awaited_once()
    grant.side_effect = RuntimeError("telegram down")                         # оплата не должна ломаться из-за канала
    _sql("UPDATE users SET subscription_paid_until=NULL WHERE telegram_id=?", (uid,))
    text, _, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert link is None and "Ссылку пришлю чуть позже" in text and has_active_subscription(uid)


async def test_the_real_payment_handler_sends_the_scenario_text_once(uid):
    _person(uid, 3)
    message = _message(uid, f"charge_{uid}")
    await pay.successful_payment(message)
    assert message.answer.await_count == 1
    text = message.answer.await_args.args[0]
    assert text.startswith("✅ Доступ продлён") and "нужно 2 дня ударного режима подряд: отметь привычки сегодня и завтра" in text
    assert get_payment(f"charge_{uid}")["amount"] == 153
    await pay.successful_payment(_message(uid, f"charge_{uid}"))             # повторная доставка того же платежа
    again = _message(uid, f"charge_{uid}")
    await pay.successful_payment(again)
    assert again.answer.await_count == 0, "тот же платёж второй раз ничего не продлевает и не пишет"


async def test_someone_elses_payload_changes_nothing(uid):
    _person(uid, 2)
    message = _message(uid, f"charge_x{uid}", payload=f"subscription:{uid + 1}")
    await pay.successful_payment(message)
    assert not has_active_subscription(uid) and message.answer.await_count == 0


# ---------------------------------------------------------------------------------------------------------------- команды и возврат

async def test_paysupport_and_terms_commands_answer(monkeypatch):
    monkeypatch.setattr(pay, "SUPPORT_EMAIL", "help@example.com")
    monkeypatch.setattr(pay, "WEBAPP_URL", "https://example.com/")
    support, terms = SimpleNamespace(answer=AsyncMock()), SimpleNamespace(answer=AsyncMock())
    await pay.paysupport(support)
    await pay.terms(terms)
    assert "help@example.com" in support.answer.await_args.args[0] and "/terms" in support.answer.await_args.args[0]
    text = terms.answer.await_args.args[0]
    assert "Первые 3 дня — бесплатно" in text and "https://example.com/terms" in text and "help@example.com" in text


def _bot(refund=None):
    return SimpleNamespace(refund_star_payment=refund or AsyncMock(), token="")


async def test_refund_returns_stars_and_takes_the_subscription_back(uid):
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_r{uid}"))
    assert has_active_subscription(uid)
    bot = _bot()
    result = await flow.refund_payment(bot, f"charge_r{uid}")
    assert result == {"ok": True, "user_id": uid, "amount": 153, "payload": f"subscription:{uid}"}
    bot.refund_star_payment.assert_awaited_once_with(user_id=uid, telegram_payment_charge_id=f"charge_r{uid}")
    assert not has_active_subscription(uid) and get_payment(f"charge_r{uid}")["refunded_at"]
    assert (await flow.refund_payment(bot, f"charge_r{uid}")) == {"error": "already_refunded"}
    assert (await flow.refund_payment(bot, "nope")) == {"error": "not_found"}
    assert [p["charge_id"] for p in list_user_payments(uid)] == [f"charge_r{uid}"]


async def test_a_failed_refund_changes_nothing(uid):
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_f{uid}"))
    broken = _bot(AsyncMock(side_effect=RuntimeError("CHARGE_ALREADY_REFUNDED")))
    result = await flow.refund_payment(broken, f"charge_f{uid}")
    assert result["error"] == "telegram_failed" and "CHARGE_ALREADY_REFUNDED" in result["detail"]
    assert has_active_subscription(uid) and not get_payment(f"charge_f{uid}")["refunded_at"]


async def test_the_refund_command_is_for_admins_only(uid, monkeypatch):
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_c{uid}"))
    monkeypatch.setattr(pay, "ADMIN_IDS", [ADMIN])
    bot = _bot()
    stranger = SimpleNamespace(from_user=SimpleNamespace(id=uid), text=f"/refund charge_c{uid}", answer=AsyncMock(), bot=bot)
    await pay.refund_command(stranger)
    assert stranger.answer.await_count == 0 and bot.refund_star_payment.await_count == 0
    admin = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN), text=f"/refund charge_c{uid}", answer=AsyncMock(), bot=bot)
    await pay.refund_command(admin)
    assert "Возвращено 153 ⭐" in admin.answer.await_args.args[0] and not has_active_subscription(uid)
    wrong = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN), text="/refund", answer=AsyncMock(), bot=bot)
    await pay.refund_command(wrong)
    assert "Формат: /refund" in wrong.answer.await_args.args[0]


# ---------------------------------------------------------------------------------------------------------------- закрытый доступ

async def test_the_locked_message_tells_the_price_of_today(uid):
    _person(uid, 4)
    text = tm.locked_text(trial_db.snapshot(uid))
    assert "Пробный период закончился" in text and "Сегодня последний день, когда месяц стоит <b>153 ⭐</b>" in text and "461 ⭐" in text
    shift_trial_day(uid, 5, ADMIN)
    text = tm.locked_text(trial_db.snapshot(uid))
    assert "<b>461 ⭐</b> в месяц" in text and "153" not in text


async def test_the_bot_blocks_an_expired_trial_account_with_the_scenario_text(uid):
    _person(uid, 4)
    shown = []

    class _Msg(Message):
        async def answer(self, text, **kwargs):
            shown.append((text, kwargs))

    event = _Msg.model_construct(message_id=1, from_user=SimpleNamespace(id=uid, username=None), text="привет", successful_payment=None)
    handler = AsyncMock()
    await AccessControlMiddleware()(handler, event, {})
    assert handler.await_count == 0 and len(shown) == 1, "закрытому аккаунту бот отвечает сам и дальше не пускает"
    text, kwargs = shown[0]
    assert "Пробный период закончился" in text and "<b>153 ⭐</b>" in text and "завтра — 461 ⭐" in text
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "buy_subscription" and "153" in kwargs["reply_markup"].inline_keyboard[0][0].text
    shift_trial_day(uid, 2, ADMIN)                                              # вернулся в триал — снова пускает
    await AccessControlMiddleware()(handler, event, {})
    assert handler.await_count == 1
