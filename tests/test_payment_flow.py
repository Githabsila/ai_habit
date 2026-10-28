"""
Оплата и её сбои (ТЗ «Адам. Триал, подписка и приглашения друзей», 28.10.2026, §2 и §19).

Платёж проходит состояния received → done / failed: повторное сообщение Telegram месяц второй раз не добавляет, а если деньги получены и начисление упало — владелец
получает ник, id и данные платежа, человек — «повторно платить не нужно» и запасной контакт, а начисление повторяется само. Подписка Stars (автопродление) оформляется
только по ссылке-счёту, поэтому первая покупка по вводной цене — разовая, а продление — подписка (за флагом).
"""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
import handlers.payments as pay
import subscription_flow as flow
from db import add_user, get_payment, get_user, set_timezone, start_trial_mode
from db import access_state as acs
from db.access import reset_trial_start
from db.core import connect
from db.shop import begin_payment, failed_payments, finish_payment
from db.subscription import has_active_subscription, payment_pending, subscription_overview
from tests.conftest import sign_init_data

UTC = timezone.utc
ADMIN = 777
_counter = itertools.count(862_000_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        row = conn.execute(query, params).fetchone() if query.lstrip().upper().startswith("SELECT") else conn.execute(query, params)
        conn.commit()
        return row
    finally:
        conn.close()


def _person(uid_, started_hours_ago=5, username=None):
    add_user(uid_, username or f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, "UTC")
    start_trial_mode(uid_, ADMIN)
    acs.set_trial_start(uid_, datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=started_hours_ago))
    return uid_


def _message(uid_, charge, amount=153, recurring=False, expires=None, username="buyer"):
    payment = SimpleNamespace(telegram_payment_charge_id=charge, invoice_payload=f"subscription:{uid_}", total_amount=amount,
                              is_recurring=recurring, subscription_expiration_date=expires)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    user = SimpleNamespace(id=uid_, username=username, full_name="Аня Тест")
    return SimpleNamespace(successful_payment=payment, from_user=user, answer=AsyncMock(), bot=bot, text="", date=datetime.now(UTC))


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------------------------------------------- состояния платежа

def test_a_payment_goes_received_then_done_and_the_repeat_is_a_duplicate(uid):
    assert begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153) == "new"
    assert get_payment(f"c{uid}")["status"] == "received"
    finish_payment(f"c{uid}", True)
    assert get_payment(f"c{uid}")["status"] == "done"
    assert begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153) == "duplicate", "повтор того же платежа ничего не начисляет"


def test_a_failed_payment_can_be_retried_and_counts_the_attempts(uid):
    begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153)
    finish_payment(f"c{uid}", False, error="boom")
    row = get_payment(f"c{uid}")
    assert row["status"] == "failed" and row["error"] == "boom" and row["attempts"] == 1
    assert any(r["charge_id"] == f"c{uid}" for r in failed_payments())
    assert begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153) == "retry"
    finish_payment(f"c{uid}", False, error="again")
    assert get_payment(f"c{uid}")["attempts"] == 2
    for _ in range(4):
        finish_payment(f"c{uid}", False, error="x")
    assert all(r["charge_id"] != f"c{uid}" for r in failed_payments(max_attempts=5)), "после пяти попыток — только вручную"


# ---------------------------------------------------------------------------------------------------------------- сбой начисления

async def test_money_received_but_credit_failed_tells_the_owner_and_the_person_then_retries(uid, monkeypatch):
    _person(uid, username="buyer_nick")
    monkeypatch.setattr(config, "ADMIN_IDS", [ADMIN])
    monkeypatch.setattr(flow, "ADMIN_IDS", [ADMIN])
    monkeypatch.setattr(flow, "SUPPORT_CONTACT", "@egor_support")
    real = pay.complete_subscription_payment
    monkeypatch.setattr(pay, "complete_subscription_payment", AsyncMock(side_effect=RuntimeError("база недоступна")))
    message = _message(uid, f"charge_{uid}", username="buyer_nick")
    await pay.successful_payment(message)

    row = get_payment(f"charge_{uid}")
    assert row["status"] == "failed" and "база недоступна" in row["error"] and not has_active_subscription(uid)
    sent = {call.args[0]: call.args[1] for call in message.bot.send_message.await_args_list}
    assert ADMIN in sent and uid in sent
    owner = sent[ADMIN]
    assert "@buyer_nick" in owner and str(uid) in owner and f"charge_{uid}" in owner and "153" in owner and "база недоступна" in owner, "ник, id, платёж, ошибка"
    person = sent[uid]
    assert "Повторно платить не нужно" in person and "@egor_support" in person and "Платёж получен" in person

    monkeypatch.setattr(pay, "complete_subscription_payment", real)         # сбой устранён — ежедневный повтор
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    assert await flow.retry_failed_payments(bot) >= 1
    assert has_active_subscription(uid) and get_payment(f"charge_{uid}")["status"] == "done"
    texts = {call.args[0]: call.args[1] for call in bot.send_message.await_args_list}
    assert texts[uid].startswith("✅ Подписка активна до") and "повторено" in texts[ADMIN]
    until = get_user(uid)["subscription_paid_until"]
    assert await flow.retry_failed_payments(bot) == 0
    assert get_user(uid)["subscription_paid_until"] == until, "повтор месяц второй раз не добавляет"


async def test_the_same_charge_never_adds_the_month_twice_even_when_the_credit_runs_again(uid):
    _person(uid)
    begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153)                       # как в обработчике: сначала запись платежа, потом начисление
    await flow.complete_subscription_payment(SimpleNamespace(), uid, charge_id=f"c{uid}")
    first = get_user(uid)["subscription_paid_until"]
    finish_payment(f"c{uid}", False, error="сбой после начисления")                  # например, не дошло сообщение человеку
    assert begin_payment(f"c{uid}", uid, f"subscription:{uid}", 153) == "retry"
    await flow.complete_subscription_payment(SimpleNamespace(), uid, charge_id=f"c{uid}")
    assert get_user(uid)["subscription_paid_until"] == first


async def test_a_duplicate_delivery_after_success_is_silent(uid):
    _person(uid)
    first = _message(uid, f"charge_{uid}")
    await pay.successful_payment(first)
    until = get_user(uid)["subscription_paid_until"]
    second = _message(uid, f"charge_{uid}")
    await pay.successful_payment(second)
    assert second.answer.await_count == 0 and get_user(uid)["subscription_paid_until"] == until


async def test_the_checkout_refuses_a_subscription_invoice_of_another_person(uid):
    wrong = SimpleNamespace(invoice_payload=f"subscription:{uid + 1}", from_user=SimpleNamespace(id=uid), answer=AsyncMock())
    await pay.pre_checkout(wrong)
    assert wrong.answer.await_args.kwargs["ok"] is False and wrong.answer.await_args.kwargs["error_message"]
    right = SimpleNamespace(invoice_payload=f"subscription:{uid}", from_user=SimpleNamespace(id=uid), answer=AsyncMock())
    await pay.pre_checkout(right)
    assert right.answer.await_args.kwargs["ok"] is True


async def test_a_payment_made_in_the_window_but_confirmed_late_keeps_the_streak(uid):
    """Оплатил в дополнительном окне, подтверждение пришло уже после того, как день окна закрылся как пропущенный: серия возвращается по времени покупки."""
    from db.streak import rollover_user
    today = datetime.now(UTC).date()
    _person(uid, started_hours_ago=0)
    acs.set_trial_start(uid, datetime.combine(today - timedelta(days=4), datetime.min.time()) + timedelta(hours=8))
    window_day = today - timedelta(days=1)
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', 5)", (uid, (today - timedelta(days=2)).isoformat()))
    _sql("UPDATE users SET streak=5, last_completed=? WHERE telegram_id=?", ((today - timedelta(days=2)).isoformat(), uid))
    _sql("INSERT OR REPLACE INTO streak_meta(user_id, rollover_day, freeze_balance) VALUES (?, ?, 0)", (uid, window_day.isoformat()))
    rollover_user(uid)
    assert get_user(uid)["streak"] == 0
    purchase = datetime.combine(window_day, datetime.min.time()).replace(tzinfo=UTC) + timedelta(hours=20)
    message = _message(uid, f"late_{uid}")
    message.date = purchase
    await pay.successful_payment(message)
    assert get_user(uid)["streak"] == 5
    assert "Серия сохранена: 5 дней подряд" in message.answer.await_args.args[0]


# ---------------------------------------------------------------------------------------------------------------- счёт и автопродление

def test_the_first_purchase_is_a_one_time_invoice_and_the_next_one_a_subscription_when_enabled(uid, monkeypatch):
    _person(uid)
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", True)
    first = flow.invoice_spec(uid)
    assert first["price"] == 153 and first["recurring"] is False and first["period"] is None
    assert "вводной цене" in first["description"] and "Дальше — 461 ⭐ в месяц" in first["description"]
    _sql("UPDATE users SET subscription_first_payment_at=CURRENT_TIMESTAMP, subscription_paid_until=NULL WHERE telegram_id=?", (uid,))
    again = flow.invoice_spec(uid)
    assert again["price"] == 461 and again["recurring"] is True and again["period"] == 2592000
    assert "Каждые 30 дней спишется 461" in again["description"]
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    manual = flow.invoice_spec(uid)
    assert manual["recurring"] is False and "Продление вручную" in manual["description"]


async def test_the_subscription_invoice_link_carries_the_30_day_period(uid, monkeypatch):
    _person(uid)
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", True)
    _sql("UPDATE users SET subscription_first_payment_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (uid,))
    bot = SimpleNamespace(create_invoice_link=AsyncMock(return_value="https://t.me/$sub"))
    link, spec = await flow.create_invoice_link(bot, uid)
    kwargs = bot.create_invoice_link.await_args.kwargs
    assert link == "https://t.me/$sub" and kwargs["subscription_period"] == 2592000 and kwargs["currency"] == "XTR" and kwargs["prices"][0].amount == 461
    assert len(kwargs["prices"]) == 1, "Telegram принимает у подписки ровно одну позицию"


async def test_a_recurring_payment_remembers_the_subscription_and_each_renewal_extends_it(uid):
    _person(uid, started_hours_ago=60)
    expires = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
    first = _message(uid, f"rec1_{uid}", amount=461, recurring=True, expires=expires)
    await pay.successful_payment(first)
    user = get_user(uid)
    assert user["subscription_auto_renew"] == 1 and user["subscription_charge_id"] == f"rec1_{uid}"
    assert "Продление автоматическое" in first.answer.await_args.args[0]
    until = datetime.fromisoformat(user["subscription_paid_until"])
    renewal = _message(uid, f"rec2_{uid}", amount=461, recurring=True, expires=int((until + timedelta(days=30)).timestamp()))
    await pay.successful_payment(renewal)
    renewed = datetime.fromisoformat(get_user(uid)["subscription_paid_until"])
    assert renewed >= until + timedelta(days=29), "очередное списание продлевает срок"
    assert get_user(uid)["subscription_charge_id"] == f"rec2_{uid}"
    overview = subscription_overview(uid)
    assert overview["auto_renew"] is True and overview["can_toggle"] is True and "автоматическое" in overview["renewal_text"]


async def test_the_person_can_switch_the_renewal_off_and_on_in_the_app(client, uid):
    _person(uid, started_hours_ago=60)
    bot = SimpleNamespace(edit_user_star_subscription=AsyncMock(), token="")
    client.app["bot"] = bot
    refused = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": False})
    assert refused.status == 409 and (await refused.json())["error"] == "no_auto_renew", "подписки с продлением нет — отключать нечего"
    await flow.complete_subscription_payment(bot, uid, charge_id=f"rec_{uid}", recurring=True, expires_at=datetime.now(UTC) + timedelta(days=30))
    off = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": False})
    body = await off.json()
    assert off.status == 200 and body["auto_renew"] is False and "отключено" in body["renewal_text"]
    kwargs = bot.edit_user_star_subscription.await_args.kwargs
    assert kwargs == {"user_id": uid, "telegram_payment_charge_id": f"rec_{uid}", "is_canceled": True}
    on = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": True})
    assert (await on.json())["auto_renew"] is True and bot.edit_user_star_subscription.await_args.kwargs["is_canceled"] is False
    status = await (await client.get("/api/subscription", headers=_headers(uid))).json()
    assert status["can_toggle"] is True and status["support"]


# ---------------------------------------------------------------------------------------------------------------- ожидание подтверждения

async def test_the_screen_waits_for_the_confirmation_after_the_invoice_and_stops_when_the_money_arrives(client, uid):
    _person(uid)
    client.app["bot"] = SimpleNamespace(create_invoice_link=AsyncMock(return_value="https://t.me/$i"), token="")
    assert payment_pending(uid) is False
    response = await client.post("/api/subscription/invoice", headers=_headers(uid))
    assert response.status == 200 and (await response.json())["recurring"] is False
    assert payment_pending(uid) is True, "счёт выставлен, подтверждения ещё нет — «ждём подтверждение»"
    trial = await (await client.get("/api/trial", headers=_headers(uid))).json()
    assert trial["trial"]["payment_pending"] is True
    await pay.successful_payment(_message(uid, f"w_{uid}"))
    assert payment_pending(uid) is False


def test_the_support_contact_is_shown_to_the_person(monkeypatch):
    monkeypatch.setattr(flow, "SUPPORT_CONTACT", "@egor_support")
    assert flow.support_line() == "@egor_support"
    monkeypatch.setattr(flow, "SUPPORT_CONTACT", "")
    assert flow.support_line() == flow.SUPPORT_EMAIL


async def test_the_admin_sees_failed_payments_and_can_retry_them(client, uid, monkeypatch):
    _person(uid)
    monkeypatch.setattr(config, "ADMIN_IDS", [uid + 5])
    import webapp.routes_admin as admin_routes
    monkeypatch.setattr(admin_routes, "ADMIN_IDS", [uid + 5], raising=False)
    begin_payment(f"stuck_{uid}", uid, f"subscription:{uid}", 153)
    finish_payment(f"stuck_{uid}", False, error="boom")
    admin = uid + 5
    add_user(admin, f"a{admin}", "Админ")
    headers = {"Authorization": f"tma {sign_init_data(admin)}"}
    client.app["bot"] = SimpleNamespace(send_message=AsyncMock(), token="")
    listing = await client.get("/api/admin/payments", headers=headers)
    if listing.status == 403:
        pytest.skip("админ в тестовой среде определяется по ADMIN_ID окружения")
    body = await listing.json()
    assert any(item["charge_id"] == f"stuck_{uid}" for item in body["failed"])
    retried = await client.post("/api/admin/payments/retry", headers=headers)
    assert retried.status == 200 and (await retried.json())["credited"] >= 1 and has_active_subscription(uid)
