"""
Доступ под контролем админа (db/access.py): свободный режим как у первых тестировщиков, временный доступ на 3/7/14 дней,
подарочные дни 3/7/14/30 (у платящего сдвигают срок подписки), сценарий триала для отдельных аккаунтов, и никаких сообщений об
оплате тем, кто в триал не включён.
"""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import subscription_scheduler as sub_sched
from db import (
    add_user, bot_access_allowed, get_access, get_access_log, get_subscription_status, get_user, gift_days, grant_access,
    public_access, record_subscription_payment, set_unlimited, start_trial_mode, trial_scenario_active,
)
from db.access import access_overview
from db.core import connect
from tests.conftest import sign_init_data

_uid_counter = itertools.count(870_000_000, 100)
ADMIN = 777


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid_, created_days_ago=10):
    add_user(uid_, f"u{uid_}", "Аня")
    conn = connect()
    conn.execute("UPDATE users SET created_at=datetime('now', ?), access_status='approved' WHERE telegram_id=?", (f"-{created_days_ago} days", uid_))
    conn.commit()
    conn.close()
    return uid_


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_from_now(iso):
    return (datetime.fromisoformat(iso) - _now()).total_seconds() / 86400


# ---------------------------------------------------------------------------------------------------------------- по умолчанию

def test_everyone_is_open_by_default_with_no_trial_and_no_paywall(uid):
    _user(uid, created_days_ago=1)
    info = get_access(uid)
    assert info["mode"] == "open" and info["allowed"] is True and info["paywall"] is False and info["days_left"] is None
    assert bot_access_allowed(uid) is True and trial_scenario_active(uid) is False
    assert public_access(uid) == {"mode": "open", "paywall": False, "allowed": True, "days_left": None, "free_active": False}


# ---------------------------------------------------------------------------------------------------------------- временный доступ

def test_grant_turns_an_open_account_into_timed_with_the_requested_days(uid):
    _user(uid)
    info = grant_access(uid, 7, ADMIN)
    assert info["mode"] == "timed" and info["days_left"] == 7 and info["allowed"] is True
    assert 6.9 < _days_from_now(info["access_until"]) < 7.1
    assert trial_scenario_active(uid) is False, "тестировщику с временным доступом триал и оплата не показываются"


def test_a_second_grant_extends_from_the_end_not_from_today(uid):
    _user(uid)
    grant_access(uid, 7, ADMIN)
    info = grant_access(uid, 3, ADMIN)
    assert 9.9 < _days_from_now(info["access_until"]) < 10.1


def test_only_3_7_14_are_allowed_for_a_grant_and_3_7_14_30_for_a_gift(uid):
    _user(uid)
    for bad in (0, 1, 5, 30, 400, "x", None):
        assert grant_access(uid, bad, ADMIN) == {"error": "invalid_days"}, bad
    grant_access(uid, 3, ADMIN)
    for bad in (1, 5, 365, "x"):
        assert gift_days(uid, bad, ADMIN) == {"error": "invalid_days"}, bad
    assert gift_days(uid, 30, ADMIN)["days_left"] >= 32
    assert grant_access(uid + 99999, 7, ADMIN) == {"error": "user_not_found"}


def test_access_is_closed_when_the_time_is_over_and_reopened_by_the_admin(uid):
    _user(uid)
    grant_access(uid, 3, ADMIN)
    _sql("UPDATE users SET access_until=? WHERE telegram_id=?", ((_now() - timedelta(hours=1)).isoformat(), uid))
    info = get_access(uid)
    assert info["allowed"] is False and info["free_active"] is False and info["days_left"] is None
    assert bot_access_allowed(uid) is False
    assert grant_access(uid, 7, ADMIN)["allowed"] is True, "новый срок считается от сегодня, а не от давно прошедшей даты"
    assert 6.9 < _days_from_now(get_access(uid)["access_until"]) < 7.1


def test_unlimited_removes_every_restriction(uid):
    _user(uid)
    grant_access(uid, 3, ADMIN)
    start_trial_mode(uid, ADMIN)
    info = set_unlimited(uid, ADMIN)
    assert info["mode"] == "open" and info["access_until"] is None and info["trial_started_at"] is None and info["allowed"] is True


# ---------------------------------------------------------------------------------------------------------------- подарочные дни

def test_a_gift_to_an_unlimited_account_is_refused(uid):
    _user(uid)
    assert gift_days(uid, 7, ADMIN) == {"error": "unlimited"}


def test_a_gift_lengthens_the_free_period_of_a_timed_account(uid):
    _user(uid)
    grant_access(uid, 7, ADMIN)
    info = gift_days(uid, 14, ADMIN)
    assert 20.9 < _days_from_now(info["access_until"]) < 21.1


def test_a_gift_to_a_paying_user_moves_the_paid_until_date_and_the_billing_date(uid):
    _user(uid)
    grant_access(uid, 3, ADMIN)
    paid_until = record_subscription_payment(uid, months=1)
    before_free = get_access(uid)["access_until"]
    info = gift_days(uid, 7, ADMIN)
    assert datetime.fromisoformat(info["paid_until"]) - paid_until == timedelta(days=7), "срок списания переехал на неделю"
    assert info["access_until"] == before_free, "бесплатный период при этом не тронут"


def test_a_gift_during_the_trial_is_added_after_the_trial_ends(uid):
    _user(uid)
    start_trial_mode(uid, ADMIN)
    info = gift_days(uid, 7, ADMIN)
    trial_days_left = 3 - 1 + 1                                   # триал: сегодня и ещё два дня (до начала суток UTC)
    assert 7 + trial_days_left - 1 < _days_from_now(info["free_end"]) < 7 + trial_days_left + 0.01


# ---------------------------------------------------------------------------------------------------------------- триал для аккаунта

def test_trial_mode_starts_from_day_one_and_shows_the_paywall(uid):
    _user(uid, created_days_ago=30)
    info = start_trial_mode(uid, ADMIN)
    assert info["mode"] == "trial" and info["trial_day"] == 1 and info["allowed"] is True and info["paywall"] is True
    assert trial_scenario_active(uid) is True
    assert get_subscription_status(uid)["trial_day"] == 1, "день триала считается от включения режима, а не от регистрации"


def test_trial_expires_after_three_days_and_a_restart_starts_over(uid):
    _user(uid, created_days_ago=30)
    start_trial_mode(uid, ADMIN)
    _sql("UPDATE users SET trial_started_at=? WHERE telegram_id=?", ((_now() - timedelta(days=4)).isoformat(), uid))
    assert bot_access_allowed(uid) is False and get_access(uid)["trial_day"] == 5
    info = start_trial_mode(uid, ADMIN, restart=True)
    assert info["allowed"] is True and info["trial_day"] == 1


def test_paid_user_keeps_access_in_any_mode(uid):
    _user(uid)
    grant_access(uid, 3, ADMIN)
    _sql("UPDATE users SET access_until=? WHERE telegram_id=?", ((_now() - timedelta(days=1)).isoformat(), uid))
    assert bot_access_allowed(uid) is False
    record_subscription_payment(uid, months=1)
    assert bot_access_allowed(uid) is True


# ---------------------------------------------------------------------------------------------------------------- журнал и обзор

def test_every_change_is_logged_with_before_and_after(uid):
    _user(uid)
    grant_access(uid, 7, ADMIN)
    gift_days(uid, 3, ADMIN)
    set_unlimited(uid, ADMIN)
    log = get_access_log(uid)
    assert [row["action"] for row in log] == ["unlimited", "gift", "grant"]
    grant = log[2]
    assert (grant["admin_id"], grant["days"], grant["mode_before"], grant["mode_after"]) == (ADMIN, 7, "open", "timed")
    assert grant["until_before"] is None and grant["until_after"]


def test_overview_lists_limited_accounts_soonest_first(uid):
    a, b, c = _user(uid), _user(uid + 1), _user(uid + 2)
    grant_access(a, 14, ADMIN)
    grant_access(b, 3, ADMIN)
    start_trial_mode(c, ADMIN)
    overview = access_overview()
    ids = [item["telegram_id"] for item in overview["items"] if item["telegram_id"] in (a, b, c)]
    assert ids.index(b) < ids.index(a), "кто раньше закончит — выше"
    assert overview["counts"]["timed"] >= 2 and overview["counts"]["trial"] >= 1


# ---------------------------------------------------------------------------------------------------------------- триал-сообщения

class _Noon(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)


async def test_trial_and_payment_messages_go_only_to_accounts_in_the_trial_scenario(uid, monkeypatch):
    open_user = _user(uid, created_days_ago=1)                    # день 2 по дате регистрации — раньше ему уходило «Второй день триала»
    timed_user = _user(uid + 1, created_days_ago=1)
    grant_access(timed_user, 7, ADMIN)
    trial_user = _user(uid + 2, created_days_ago=30)
    start_trial_mode(trial_user, ADMIN)
    _sql("UPDATE users SET trial_started_at=? WHERE telegram_id=?", ((_now() - timedelta(days=1)).isoformat(), trial_user))
    users = [get_user(open_user), get_user(timed_user), get_user(trial_user)]
    monkeypatch.setattr(sub_sched, "get_all_users", lambda **kw: users)
    monkeypatch.setattr(sub_sched, "datetime", _Noon)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await sub_sched.run_trial_reminders(bot)
    sent_to = {call.args[0] for call in bot.send_message.call_args_list}
    assert sent_to == {trial_user}, "про триал и оплату пишем только тому, у кого включён сценарий триала"
    assert "сегодня второй день" in bot.send_message.call_args_list[0].args[1].lower()      # день 2, отметки нет — цель на два дня подряд


# ---------------------------------------------------------------------------------------------------------------- приложение

def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


async def test_bootstrap_carries_the_access_mode_and_hides_the_paywall_for_testers(client, uid):
    _user(uid)
    data = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert data["access"] == {"mode": "open", "paywall": False, "allowed": True, "days_left": None, "free_active": False}
    trial = _user(uid + 1)
    start_trial_mode(trial, ADMIN)
    data = await (await client.get("/api/bootstrap", headers=_headers(trial))).json()
    assert data["access"]["mode"] == "trial" and data["access"]["paywall"] is True


async def test_an_expired_timed_account_gets_a_clear_403_but_an_extension_opens_it_again(client, uid):
    _user(uid)
    grant_access(uid, 3, ADMIN)
    _sql("UPDATE users SET access_until=? WHERE telegram_id=?", ((_now() - timedelta(hours=2)).isoformat(), uid))
    resp = await client.get("/api/bootstrap", headers=_headers(uid))
    body = await resp.json()
    assert resp.status == 403 and body["error"] == "access_expired" and "Тестовый доступ" in body["message"]
    chat = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "привет"})
    assert chat.status == 403 and (await chat.json())["error"] == "access_expired", "чат Адама тоже закрыт, а не только Главная"
    grant_access(uid, 7, ADMIN)
    assert (await client.get("/api/bootstrap", headers=_headers(uid))).status == 200


async def test_an_expired_trial_account_still_gets_the_trial_message(client, uid):
    _user(uid)
    start_trial_mode(uid, ADMIN)
    _sql("UPDATE users SET trial_started_at=? WHERE telegram_id=?", ((_now() - timedelta(days=5)).isoformat(), uid))
    resp = await client.get("/api/bootstrap", headers=_headers(uid))
    assert resp.status == 403 and (await resp.json())["error"] == "trial_expired"


def test_the_start_quiz_shows_the_price_screen_only_in_the_trial_scenario():
    from pathlib import Path
    app = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "const START_QUIZ_STEPS_ALL = [" in app and "let START_QUIZ_STEPS = START_QUIZ_STEPS_ALL;" in app
    assert 'START_QUIZ_STEPS_ALL.filter((step) => step.type !== "paywall" || state?.access?.paywall)' in app
    assert "access_expired:" in app


# ---------------------------------------------------------------------------------------------------------------- админ-API

async def test_admin_can_grant_gift_and_see_the_log_through_the_api(client, uid, monkeypatch):
    import config
    import webapp.routes_admin as routes_admin
    admin = uid
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    person = _user(uid + 1)
    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    headers = _headers(admin)

    resp = await client.post(f"/api/admin/user/{person}/access", json={"action": "grant", "days": 7}, headers=headers)
    body = await resp.json()
    assert resp.status == 200 and body["access"]["mode"] == "timed" and body["access"]["days_left"] == 7 and body["notified"] is True
    text = bot.send_message.call_args.args[1]
    assert "7 дней" in text and "тестовый доступ" in text and "оплат" not in text.lower()

    resp = await client.post(f"/api/admin/user/{person}/access", json={"action": "gift", "days": 3, "notify": False}, headers=headers)
    assert resp.status == 200 and (await resp.json())["notified"] is False
    assert bot.send_message.call_count == 1

    assert (await client.post(f"/api/admin/user/{person}/access", json={"action": "grant", "days": 5}, headers=headers)).status == 400
    assert (await client.post(f"/api/admin/user/{person}/access", json={"action": "boom"}, headers=headers)).status == 400
    free = _user(uid + 2)
    gift = await client.post(f"/api/admin/user/{free}/access", json={"action": "gift", "days": 7}, headers=headers)
    assert gift.status == 409 and (await gift.json())["error"] == "unlimited"

    card = await (await client.get(f"/api/admin/user/{person}", headers=headers)).json()
    assert card["access"]["mode"] == "timed" and [row["action"] for row in card["access_log"]] == ["gift", "grant"]

    overview = await (await client.get("/api/admin/access/overview", headers=headers)).json()
    assert any(item["telegram_id"] == person for item in overview["items"])
    assert config  # конфиг подключён


async def test_admin_trial_switch_can_also_reset_the_onboarding(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    person = _user(uid + 1)
    _sql("UPDATE users SET app_tour_seen=1, start_quiz_seen=1 WHERE telegram_id=?", (person,))
    resp = await client.post(f"/api/admin/user/{person}/access", json={"action": "trial_on", "reset_onboarding": True}, headers=_headers(uid))
    body = await resp.json()
    assert resp.status == 200 and body["access"]["mode"] == "trial" and body["access"]["paywall"] is True
    row = get_user(person)
    assert row["app_tour_seen"] == 0 and row["start_quiz_seen"] == 0


async def test_the_access_api_is_for_admins_only(client, uid):
    person = _user(uid + 1)
    resp = await client.post(f"/api/admin/user/{person}/access", json={"action": "grant", "days": 7}, headers=_headers(uid))
    assert resp.status == 403
    assert get_access(person)["mode"] == "open"
