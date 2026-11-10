"""
Остаток прогона 20 проверяющих (09.11), исправлено 10.11. Каждый тест падал до исправления.
"""
import itertools
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
from db import add_user, get_user, ban_user, pay_orders
from db.core import connect
from tests.test_pay_lava import SECRET, _hook, _headers, _link_all, _user, bot  # noqa: F401 — фикстуры Lava

_counter = itertools.count(874_000_000, 100)
_contract = itertools.count(1)


@pytest.fixture
def lava(monkeypatch):
    """Как в test_pay_lava, но свои номера договоров (s-…): тот модуль может импортироваться дважды, и счётчики совпали бы."""
    monkeypatch.setattr(config, "LAVA_API_KEY", "test-key")
    monkeypatch.setattr(config, "LAVA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "EXTERNAL_PAYMENTS_ENABLED", True)
    calls = []

    async def fake_request(self, method, path, body=None, params=None):
        calls.append((self.name, method, path, body, params))
        if path == "/api/v3/invoice":
            return 201, {"id": f"s-{next(_contract)}", "paymentUrl": "https://pay.lava.top/x", "status": "new"}
        if path == "/api/v1/subscriptions":
            return 204, None
        return 404, None

    from pay_lava import Lava
    monkeypatch.setattr(Lava, "_request", fake_request)
    return calls


@pytest.fixture
def uid():
    return next(_counter)


def _exec(sql, args=()):
    conn = connect()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------ /start с огромным числом

async def test_start_with_a_huge_number_still_answers(uid, monkeypatch):
    import handlers.start as start_module
    monkeypatch.setattr(start_module, "begin_survey", AsyncMock())
    message = SimpleNamespace(text="/start 99999999999999999999", from_user=SimpleNamespace(id=uid, username=f"u{uid}", first_name="Новичок"),
                              answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()))
    await start_module.start(message, None)                          # раньше OverflowError в register_invite
    message.bot.send_message.assert_not_awaited()


# ------------------------------------------------------------------------------------------------ анкета и автоодобрение

async def test_finishing_the_survey_again_does_not_downgrade_open_access(uid, monkeypatch):
    import handlers.onboarding as ob
    add_user(uid, f"u{uid}", "Аня")
    _exec("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid,))
    monkeypatch.setattr(ob, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr(ob, "_notify_admins_about_application", AsyncMock())
    state = SimpleNamespace(get_data=AsyncMock(return_value={"business": "IT", "life_goal": "цель"}), clear=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    await ob._finish(SimpleNamespace(), SimpleNamespace(id=uid), state, "бегать", message, False)
    assert get_user(uid)["access_status"] == "approved"
    ob._notify_admins_about_application.assert_not_awaited()


async def test_auto_approve_skips_the_banned_and_does_not_approve_twice(uid, monkeypatch):
    import onboarding_auto
    banned, normal = uid, uid + 1
    for person in (banned, normal):
        add_user(person, f"u{person}", "Аня")
        _exec("UPDATE users SET access_status='pending', survey_completed_at=datetime('now', '-10 hours') WHERE telegram_id=?", (person,))
    ban_user(banned)
    notified = []

    async def fake_notify(_bot, user_id):
        notified.append(user_id)
    monkeypatch.setattr(onboarding_auto, "notify_approved", fake_notify)
    monkeypatch.setattr(onboarding_auto, "get_users_pending_since", lambda hours: [banned, normal, normal])   # гонка: тот же человек дважды
    await onboarding_auto.run_auto_approve(SimpleNamespace())
    assert notified == [normal]
    assert get_user(banned)["access_status"] == "pending"

    from db import get_users_pending_since
    assert banned not in get_users_pending_since(1)


# ------------------------------------------------------------------------------------------------ старый магазин в боте

async def test_old_bot_shop_buttons_take_no_coins_and_lead_to_the_app(uid):
    import handlers.shop as shop
    add_user(uid, f"u{uid}", "Аня")
    _exec("UPDATE users SET xp=1000 WHERE telegram_id=?", (uid,))
    callback = SimpleNamespace(data="buy_27", from_user=SimpleNamespace(id=uid), answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
    await shop.buy_item(callback)
    assert get_user(uid)["xp"] == 1000
    assert "приложении" in callback.message.answer.await_args.args[0]


# ------------------------------------------------------------------------------------------------ разбор цели

def test_goal_feedback_skips_those_who_blocked_the_bot(uid):
    from db import get_surveys_due_for_feedback, save_survey_answers
    add_user(uid, f"u{uid}", "Аня")
    _exec("UPDATE users SET access_status='approved', bot_blocked_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (uid,))
    save_survey_answers(uid, "IT", "", "цель", "бегать")
    assert uid not in [row["user_id"] for row in get_surveys_due_for_feedback(days=7)]


def test_goal_feedback_goes_on_monday_morning_local_time(uid):
    import goal_feedback
    from db.streak import set_timezone
    add_user(uid, f"u{uid}", "Аня")
    set_timezone(uid, "America/Los_Angeles")
    monday_10_utc = datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc)          # в Лос-Анджелесе 2:15 ночи
    assert goal_feedback._due_local(uid, monday_10_utc) is None
    monday_18_utc = datetime(2026, 11, 9, 18, 15, tzinfo=timezone.utc)          # 10:15 там
    assert goal_feedback._due_local(uid, monday_18_utc) is not None


async def test_goal_feedback_marks_the_blocked_and_does_not_alert(uid, monkeypatch):
    import goal_feedback
    from aiogram.exceptions import TelegramForbiddenError
    add_user(uid, f"u{uid}", "Аня")
    monkeypatch.setattr(goal_feedback, "get_surveys_due_for_feedback", lambda days=7: [{"user_id": uid, "life_goal": "", "bot_goal": "x"}])
    monkeypatch.setattr(goal_feedback, "_due_local", lambda _uid, now_utc=None: object())
    monkeypatch.setattr(goal_feedback, "analyze_goal_progress", AsyncMock(return_value="разбор"))
    alerts = AsyncMock()
    monkeypatch.setattr(goal_feedback, "notify_admins", alerts)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked")))
    await goal_feedback.run_goal_feedback(bot)
    assert get_user(uid)["bot_blocked_at"]
    alerts.assert_not_awaited()


# ------------------------------------------------------------------------------------------------ серии и пуши

def test_a_day_saved_by_a_freeze_is_not_a_lost_streak(uid):
    from db.streak import get_streak_reengagement_state, local_today
    add_user(uid, f"u{uid}", "Аня")
    today = local_today(uid)
    _exec("INSERT INTO streak_days(user_id, day, status, streak_after) VALUES(?,?,?,?)", (uid, (today - timedelta(days=2)).isoformat(), "completed", 5))
    _exec("INSERT INTO streak_days(user_id, day, status, streak_after) VALUES(?,?,?,?)", (uid, (today - timedelta(days=1)).isoformat(), "freeze", 5))
    assert get_streak_reengagement_state(uid)["inactive_days"] == 0


def test_pushes_skip_the_banned(uid):
    import streak_scheduler
    add_user(uid, f"u{uid}", "Аня")
    assert streak_scheduler._push_allowed(uid)
    ban_user(uid)
    assert not streak_scheduler._push_allowed(uid)


# ------------------------------------------------------------------------------------------------ смена дня

def test_rollover_touches_only_those_whose_day_changed(uid):
    from db.streak import _users_due_for_rollover, rollover_user, set_timezone
    add_user(uid, f"u{uid}", "Аня")
    rollover_user(uid)                                                   # первый раз — создаёт streak_meta с сегодняшним днём
    assert uid not in _users_due_for_rollover()
    _exec("UPDATE streak_meta SET rollover_day=? WHERE user_id=?", ((date.today() - timedelta(days=3)).isoformat(), uid))
    assert uid in _users_due_for_rollover()
    rollover_user(uid)
    assert uid not in _users_due_for_rollover()
    # перевёл пояс назад — день «в будущем», повторной смены дня нет
    _exec("UPDATE streak_meta SET rollover_day=? WHERE user_id=?", ((date.today() + timedelta(days=1)).isoformat(), uid))
    set_timezone(uid, "UTC")
    assert rollover_user(uid) is False


# ------------------------------------------------------------------------------------------------ админка

def _admin_msg(text, admin=777000333):
    return SimpleNamespace(text=text, html_text=text, from_user=SimpleNamespace(id=admin), answer=AsyncMock(),
                           bot=SimpleNamespace(send_message=AsyncMock()))


class _State:
    def __init__(self, data=None):
        self.data, self.state = dict(data or {}), None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def set_state(self, value):
        self.state = value

    async def clear(self):
        self.data, self.state = {}, None


async def test_broadcast_waits_for_confirmation_and_a_command_cancels_it(monkeypatch):
    import handlers.admin as admin
    monkeypatch.setattr(admin, "ADMIN_IDS", [777000333])
    state = _State({"tag": None})
    message = _admin_msg("/start")
    await admin.send_broadcast(message, state)
    message.bot.send_message.assert_not_awaited()
    assert state.state is None and "отменена" in message.answer.await_args.args[0]

    state = _State({"tag": None})
    message = _admin_msg("Привет всем")
    await admin.send_broadcast(message, state)
    message.bot.send_message.assert_not_awaited()                     # пока не подтвердили — никому
    assert state.state == admin.AdminState.broadcast_confirm and state.data["body"] == "Привет всем"


async def test_broadcast_skips_banned_and_blocked(uid, monkeypatch):
    import handlers.admin as admin
    ok, banned, blocked = uid, uid + 1, uid + 2
    for person in (ok, banned, blocked):
        add_user(person, f"u{person}", "Аня")
    ban_user(banned)
    _exec("UPDATE users SET bot_blocked_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (blocked,))
    recipients = admin._broadcast_recipients(None)
    assert ok in recipients and banned not in recipients and blocked not in recipients


async def test_ban_asks_for_confirmation_first(uid, monkeypatch):
    import handlers.admin as admin
    monkeypatch.setattr(admin, "ADMIN_IDS", [777000333])
    add_user(uid, f"u{uid}", "Аня")
    message = _admin_msg(str(uid))
    await admin.ban(message, _State())
    assert not get_user(uid)["banned"]
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"ban_yes:{uid}"
    unknown = _admin_msg("123")
    await admin.ban(unknown, _State())
    assert "нет в базе" in unknown.answer.await_args.args[0]


# ------------------------------------------------------------------------------------------------ Lava

async def test_lava_cancel_uses_the_email_of_the_subscription(client, uid, lava, bot):
    _user(uid)
    _link_all()
    from db.subscription import record_subscription_payment
    record_subscription_payment(uid, months=1)
    _exec("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ("2026-11-07T10:00:00", uid))
    pay_orders.set_pay_email(uid, "first@example.com")
    created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "subscription"})).json()
    assert "order_id" in created, created
    order = pay_orders.get_order(created["order_id"])
    await _hook(client, {"eventType": "payment.success", "contractId": order["provider_uuid"], "amount": 5.99, "currency": "USD",
                         "status": "subscription-active"})
    pay_orders.set_pay_email(uid, "other@example.com")               # потом купил алмазы с другой почтой
    off = await client.post("/api/subscription/auto-renew", headers=_headers(uid), json={"enabled": False})
    assert off.status == 200
    assert lava[-1][1] == "DELETE" and lava[-1][4]["email"] == "first@example.com"


async def test_a_second_lava_subscription_cancels_the_first(client, uid, lava, bot):
    _user(uid)
    _link_all()
    from db.subscription import record_subscription_payment
    record_subscription_payment(uid, months=1)
    _exec("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ("2026-11-07T10:00:00", uid))
    pay_orders.set_pay_email(uid, "a@example.com")
    orders = []
    for _ in range(2):
        created = await (await client.post("/api/pay/order", headers=_headers(uid), json={"method": "card", "kind": "subscription"})).json()
        orders.append(pay_orders.get_order(created["order_id"]))
    for order in orders:
        assert order["price_key"] == "sub_renewal", dict(order)
        await _hook(client, {"eventType": "payment.success", "contractId": order["provider_uuid"], "amount": 5.99, "currency": "USD",
                             "status": "subscription-active"})
    deletes = [call for call in lava if call[1] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][4]["contractId"] == orders[0]["provider_uuid"]
    assert get_user(uid)["subscription_charge_id"] == f"lava:{orders[1]['provider_uuid']}"


async def test_lava_404_with_a_guessed_email_is_not_success(lava, monkeypatch):
    import pay_providers
    from pay_lava import Lava

    async def not_found(self, method, path, body=None, params=None):
        return 404, None
    monkeypatch.setattr(Lava, "_request", not_found)
    provider = pay_providers.PROVIDERS["lava"]
    assert await provider.cancel_subscription("c-x", "a@example.com", missing_ok=True) is True
    assert await provider.cancel_subscription("c-x", "a@example.com", missing_ok=False) is False
