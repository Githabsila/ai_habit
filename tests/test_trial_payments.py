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

async def test_payment_on_day_one_adds_the_free_days_and_says_so(uid):
    _person(uid, 1)
    text, snap, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert has_active_subscription(uid) and link is None and snap["phase"] == "paid" and snap["early"] is True
    assert text.startswith("✅ Подписка активна до ") and "Оставшиеся бесплатные дни пробного периода добавлены к сроку" in text
    # ранняя покупка: платный доступ включился сразу, а оставшиеся бесплатные дни добавлены к нему (ТЗ §6) — месяц считается от конца триала
    trial_end = datetime.fromisoformat(get_user(uid)["trial_ends_at"])
    assert (trial_end + timedelta(days=30)).strftime("%d.%m.%Y") in text


async def test_payment_after_the_trial_has_no_free_days_note(uid):
    _person(uid, 4)
    text, snap, _ = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert snap["early"] is False and "бесплатные дни" not in text and "Продление вручную" in text


async def test_a_ready_streak_gets_the_channel_link_at_once_when_the_channel_is_connected(uid, monkeypatch):
    _person(uid, 3)
    _mark(uid, 2, 1)
    _mark(uid, 1, 2)
    _mark(uid, 0, 3)                                                          # решение 05.11: канал с 3 дней серии
    monkeypatch.setattr(config, "CLOSED_CHANNEL_ID", "-100123")
    grant = AsyncMock(return_value="https://t.me/+abc")
    monkeypatch.setattr(flow, "try_grant_channel_access", grant)
    text, snap, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert link == "https://t.me/+abc" and "https://t.me/+abc" in text
    grant.assert_awaited_once()
    grant.side_effect = RuntimeError("telegram down")                         # оплата не должна ломаться из-за канала
    _sql("UPDATE users SET subscription_paid_until=NULL WHERE telegram_id=?", (uid,))
    text, _, link = await flow.complete_subscription_payment(SimpleNamespace(), uid)
    assert link is None and has_active_subscription(uid)


async def test_the_real_payment_handler_sends_the_confirmation_once(uid):
    _person(uid, 3)
    message = _message(uid, f"charge_{uid}")
    await pay.successful_payment(message)
    assert message.answer.await_count == 1
    text = message.answer.await_args.args[0]
    assert text.startswith("✅ Подписка активна до") and "Продление вручную" in text
    assert get_payment(f"charge_{uid}")["amount"] == 153 and get_payment(f"charge_{uid}")["status"] == "done"
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
    monkeypatch.setattr(config, "SUPPORT_EMAIL", "help@example.com")
    monkeypatch.setattr(config, "SUPPORT_INSTAGRAM", "adam_support")          # решение 05.11: возвраты — через админа в Telegram или Instagram
    monkeypatch.setattr(pay, "WEBAPP_URL", "https://example.com/")
    support, terms = SimpleNamespace(answer=AsyncMock()), SimpleNamespace(answer=AsyncMock())
    await pay.paysupport(support)
    await pay.terms(terms)
    assert "help@example.com" in support.answer.await_args.args[0] and "/terms" in support.answer.await_args.args[0]
    text = terms.answer.await_args.args[0]
    assert "Первые 3 календарных дня — бесплатно" in text and "вводной цене" in text and "https://example.com/terms" in text and "help@example.com" in text
    assert "Подписка не продлевается сама" in text, "пока автопродление не включено, условия честно говорят про продление вручную"
    assert "instagram.com/adam_support" in support.answer.await_args.args[0] and "30 дней" in text and "через администратора" in text


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
    # Найдено в живом чате: обычный человек писал /refund и не получал ничего. Теперь — куда обратиться; сам возврат по-прежнему только у админа.
    assert bot.refund_star_payment.await_count == 0 and has_active_subscription(uid)
    assert stranger.answer.await_count == 1 and "/paysupport" in stranger.answer.await_args.args[0]
    admin = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN), text=f"/refund charge_c{uid}", answer=AsyncMock(), bot=bot)
    await pay.refund_command(admin)
    assert "Возвращено 153 ⭐" in admin.answer.await_args.args[0] and not has_active_subscription(uid)
    wrong = SimpleNamespace(from_user=SimpleNamespace(id=ADMIN), text="/refund", answer=AsyncMock(), bot=bot)
    await pay.refund_command(wrong)
    hint = wrong.answer.await_args.args[0]
    assert "Формат: /refund" in hint and "<" not in hint, "бот шлёт в HTML-режиме: угловые скобки в подсказке экранируются"


# ---------------------------------------------------------------------------------------------------------------- закрытый доступ

async def test_the_locked_message_tells_the_deadline_and_both_prices(uid):
    _person(uid, 4)
    snap = trial_db.snapshot(uid)
    text = tm.locked_text(snap)
    assert "Пробный доступ завершён" in text and "Твоя история сохранена" in text and snap["ends_at_text"] in text, "конкретный срок окна"
    assert "Первый месяц — <b>$1.99</b>" in text and "дальше $5.99 в месяц" in text
    assert "только сегодня" not in text.lower() and "последний день" not in text, "вводная цена не «только сегодня»: она действует и позже"
    shift_trial_day(uid, 5, ADMIN)
    text = tm.locked_text(trial_db.snapshot(uid))
    assert "Доступ пока закрыт" in text and "Твоя история сохранена" in text and "Первый месяц — <b>$1.99</b>" in text


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
    assert "Пробный доступ завершён" in text and "<b>$1.99</b>" in text and "дальше $5.99 в месяц" in text
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "buy_subscription" and "153" in kwargs["reply_markup"].inline_keyboard[0][0].text
    shift_trial_day(uid, 2, ADMIN)                                              # вернулся в триал — снова пускает
    await AccessControlMiddleware()(handler, event, {})
    assert handler.await_count == 1


async def test_review_refunding_one_payment_keeps_the_other_paid_month(uid):
    """Прогон 07.11, находка P3: возврат одного платежа стирал весь срок — человек, оплативший два месяца (по ошибке дважды), терял оба."""
    from datetime import datetime as _dt
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_a{uid}"))
    await pay.successful_payment(_message(uid, f"charge_b{uid}"))
    two = _dt.fromisoformat(get_user(uid)["subscription_paid_until"])
    await flow.refund_payment(_bot(), f"charge_b{uid}")
    left = get_user(uid)["subscription_paid_until"]
    assert left and has_active_subscription(uid), "второй, честно оплаченный месяц остаётся"
    assert abs((two - _dt.fromisoformat(left)).days - 30) <= 1, "снят ровно один период"


async def test_review_refunding_an_autorenew_payment_turns_autorenew_off(uid):
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_s{uid}"))
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_auto_renew=1, subscription_charge_id=? WHERE telegram_id=?", (f"charge_s{uid}", uid))
        conn.commit()
    finally:
        conn.close()
    await flow.refund_payment(_bot(), f"charge_s{uid}")
    assert not get_user(uid)["subscription_auto_renew"], "после возврата Telegram не должен продолжать списания"


async def test_review_a_banned_person_cannot_use_the_bot(uid):
    """Прогон 07.11, находка A2: бан проверялся только в /start — забаненный продолжал жать кнопки и говорить с ИИ (платные вызовы)."""
    _person(uid, 1)
    connect_ = connect()
    try:
        connect_.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (uid,))
        connect_.commit()
    finally:
        connect_.close()
    shown = []

    class _Msg(Message):
        async def answer(self, text, **kwargs):
            shown.append(text)

    handler = AsyncMock()
    await AccessControlMiddleware()(handler, _Msg.model_construct(message_id=1, from_user=SimpleNamespace(id=uid, username=None), text="привет", successful_payment=None), {})
    assert handler.await_count == 0 and "заблокирован" in shown[0]


async def test_review_a_star_payment_is_credited_even_under_the_age_block(uid):
    """Прогон 07.11, A-low: проверка 16+ стояла раньше пропуска successful_payment — списанные звёзды (в т.ч. автопродление) терялись."""
    from db.age_gate import AGE_RESTRICTED
    _person(uid, 1)
    connect_ = connect()
    try:
        connect_.execute("UPDATE users SET access_status=? WHERE telegram_id=?", (AGE_RESTRICTED, uid))
        connect_.commit()
    finally:
        connect_.close()

    class _Msg(Message):
        async def answer(self, text, **kwargs):
            pass

    handler = AsyncMock()
    await AccessControlMiddleware()(handler, _Msg.model_construct(message_id=1, from_user=SimpleNamespace(id=uid, username=None), text=None,
                                                                    successful_payment=SimpleNamespace(total_amount=153)), {})
    assert handler.await_count == 1


async def test_review_refunding_a_payment_that_was_never_credited_keeps_the_paid_month(uid):
    """Прогон 09.11: платёж B получен, но начисление упало (доступ не открылся). Возврат B не должен снимать месяц, оплаченный платежом A."""
    from db.shop import begin_payment, finish_payment
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_a{uid}"))
    until = get_user(uid)["subscription_paid_until"]
    begin_payment(f"charge_x{uid}", uid, f"subscription:{uid}", 461)
    finish_payment(f"charge_x{uid}", False, error="сбой начисления")
    await flow.refund_payment(_bot(), f"charge_x{uid}")
    assert get_user(uid)["subscription_paid_until"] == until and has_active_subscription(uid)


async def test_review_a_failed_star_autorenewal_can_be_paid_by_hand_in_the_grace_hours(uid):
    """Прогон 09.11: Telegram не списал звёзды, срок уже кончился, идут сутки запаса. Бот пишет «пополни звёзды и продли» —
    значит, оплата вручную должна быть доступна, а не «оплата не нужна»."""
    _person(uid, 2)
    await pay.successful_payment(_message(uid, f"charge_r{uid}"))
    _sql("UPDATE users SET subscription_auto_renew=1, subscription_charge_id=?, subscription_paid_until=? WHERE telegram_id=?",
         (f"charge_r{uid}", (datetime.utcnow() - timedelta(hours=3)).isoformat(timespec="seconds"), uid))
    spec = flow.invoice_spec(uid)
    assert spec is not None and spec["price"] > 0
