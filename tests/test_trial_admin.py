"""
Админка триала (ТЗ владельца 12.10–16.10: «проверять триал с новых аккаунтов», включать/выключать сообщения, «обнулить старт»):
машина времени по дням, обнуление, тестовая оплата тем же путём, что настоящая, отправка любого сообщения сценария себе или человеку,
тумблеры — только для админов, только для аккаунтов в режиме «триал».
"""
import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import trial_messages as tm
from db import add_user, get_access_log, get_user, start_trial_mode
from db import trial as trial_db
from db.core import connect
from db.subscription import has_active_subscription
from tests.conftest import sign_init_data

_counter = itertools.count(859_800_000, 100)
STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
PANEL = (STATIC / "admin_panel.html").read_text(encoding="utf-8")


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _person(uid_):
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    return uid_


@pytest.fixture
def admin(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    client.app["bot"] = SimpleNamespace(send_message=AsyncMock(), token="")
    return uid


async def _act(client, admin_id, person, **body):
    return await client.post(f"/api/admin/user/{person}/trial", json=body, headers=_headers(admin_id))


async def test_the_shift_moves_the_account_to_the_chosen_day(client, uid, admin):
    person = _person(uid + 1)
    refused = await _act(client, admin, person, action="shift", day=3)
    assert refused.status == 409 and (await refused.json())["error"] == "not_in_trial", "машина времени — только для аккаунтов в триале"
    start_trial_mode(person, admin)
    for day, phase in ((3, "day3"), (4, "expired_intro"), (5, "expired_full"), (1, "day1")):
        body = await (await _act(client, admin, person, action="shift", day=day)).json()
        assert body["ok"] and body["snapshot"]["day"] == day and body["snapshot"]["phase"] == phase and body["access"]["trial_day"] == day
    assert (await _act(client, admin, person, action="shift", day=11)).status == 400
    assert get_access_log(person)[0]["action"] == "trial_shift"


async def test_reset_starts_over_forgets_messages_and_the_test_payment(client, uid, admin):
    person = _person(uid + 1)
    start_trial_mode(person, admin)
    await _act(client, admin, person, action="shift", day=3)
    _sql("INSERT INTO streak_notifications(user_id, day, kind) VALUES (?, 'd', 'trial:d3_midday:default')", (person,))
    _sql("INSERT INTO notification_log(user_id, category, title) VALUES (?, 'trial:d3_midday', 'x')", (person,))
    paid = await (await _act(client, admin, person, action="pay_test")).json()
    assert paid["snapshot"]["paid"] is True and has_active_subscription(person)
    _sql("UPDATE users SET app_tour_seen=1 WHERE telegram_id=?", (person,))
    body = await (await _act(client, admin, person, action="reset", reset_onboarding=True)).json()
    snap = body["snapshot"]
    assert snap["day"] == 1 and snap["paid"] is False and snap["ever_paid"] is False and snap["intro_active"] is True
    assert body["sent"] == [] and get_user(person)["app_tour_seen"] == 0


async def test_the_test_payment_goes_the_real_way_and_tells_the_person(client, uid, admin):
    person = _person(uid + 1)
    start_trial_mode(person, admin)
    await _act(client, admin, person, action="shift", day=2)
    body = await (await _act(client, admin, person, action="pay_test")).json()
    assert body["delivered"] is True and "Оплаты мало, чтобы попасть в закрытый канал" in body["text"]
    bot = client.app["bot"]
    assert bot.send_message.await_args.args[0] == person and body["text"] == bot.send_message.await_args.args[1]
    assert body["snapshot"]["phase"] == "paid"
    cleared = await (await _act(client, admin, person, action="unpay")).json()
    assert cleared["snapshot"]["paid"] is False
    outsider = _person(uid + 2)
    assert (await _act(client, admin, outsider, action="pay_test")).status == 409, "тест-оплата — только для аккаунтов в триале"


async def test_any_message_of_the_scenario_can_be_previewed_or_sent(client, uid, admin):
    person = _person(uid + 1)
    start_trial_mode(person, admin)
    await _act(client, admin, person, action="shift", day=3)
    bot = client.app["bot"]
    body = await (await _act(client, admin, person, action="send", key="d3_evening", to="me")).json()
    assert body["sent_to"] == "me"
    target, text = bot.send_message.await_args.args[:2]
    assert target == admin and text.startswith("🧪 <b>Превью</b>") and "день 3" in text and "лишь слова" in text
    keyboard = bot.send_message.await_args.kwargs["reply_markup"]
    assert [b.callback_data for row in keyboard.inline_keyboard for b in row] == ["buy_subscription"]
    await _act(client, admin, person, action="send", key="d3_midday", to="user")
    target, text = bot.send_message.await_args.args[:2]
    assert target == person and not text.startswith("🧪") and "завтра доступ ко мне закроется" in text
    assert (await _act(client, admin, person, action="send", key="channel_link", to="user")).status == 400, "ссылку в канал человеку «просто так» не шлём"
    assert (await _act(client, admin, person, action="send", key="channel_link", to="me")).status == 200
    assert (await _act(client, admin, person, action="send", key="nope", to="me")).status == 400
    assert (await _act(client, admin, person, action="boom")).status == 400
    bot.send_message.side_effect = RuntimeError("blocked")
    failed = await _act(client, admin, person, action="send", key="d3_midday", to="user")
    assert failed.status == 502 and (await failed.json())["error"] == "send_failed"


async def test_the_switches_work_for_all_and_for_one_message(client, uid, admin):
    overview = await (await client.get("/api/admin/trial", headers=_headers(admin))).json()
    assert overview["enabled"] is True and [m["key"] for m in overview["messages"]] == [m.key for m in tm.CATALOG]
    assert all(m["enabled"] for m in overview["messages"])
    assert {"trial_days", "intro_price_stars", "renewal_price_stars", "streak_needed", "channel_configured", "gate_enabled"} <= set(overview["config"])
    body = await (await client.post("/api/admin/trial/toggle", json={"key": "d3_midday", "enabled": False}, headers=_headers(admin))).json()
    assert [m["enabled"] for m in body["messages"] if m["key"] == "d3_midday"] == [False] and trial_db.message_enabled("d3_midday") is False
    body = await (await client.post("/api/admin/trial/toggle", json={"key": "*", "enabled": False}, headers=_headers(admin))).json()
    assert body["enabled"] is False and trial_db.message_enabled("d3_evening") is False
    await client.post("/api/admin/trial/toggle", json={"key": "*", "enabled": True}, headers=_headers(admin))
    await client.post("/api/admin/trial/toggle", json={"key": "d3_midday", "enabled": True}, headers=_headers(admin))
    assert trial_db.message_enabled("d3_midday") is True
    assert (await client.post("/api/admin/trial/toggle", json={"key": "boom", "enabled": True}, headers=_headers(admin))).status == 400


async def test_the_user_card_carries_the_trial_state(client, uid, admin):
    person = _person(uid + 1)
    start_trial_mode(person, admin)
    await _act(client, admin, person, action="shift", day=2)
    card = await (await client.get(f"/api/admin/user/{person}", headers=_headers(admin))).json()
    trial = card["trial"]
    assert trial["snapshot"]["phase"] == "day2" and isinstance(trial["due"], list)
    state = await (await client.get(f"/api/admin/user/{person}/trial", headers=_headers(admin))).json()
    assert state["snapshot"]["day"] == 2 and state["sent"] == [] and state["payments"] == []
    assert (await client.get(f"/api/admin/user/{uid + 999}/trial", headers=_headers(admin))).status == 404


async def test_the_trial_api_is_for_admins_only(client, uid):
    person = _person(uid + 1)
    for method, path, body in (("get", "/api/admin/trial", None), ("post", "/api/admin/trial/toggle", {"key": "*", "enabled": False}),
                               ("get", f"/api/admin/user/{person}/trial", None), ("post", f"/api/admin/user/{person}/trial", {"action": "reset"})):
        resp = await getattr(client, method)(path, headers=_headers(uid), **({"json": body} if body else {}))
        assert resp.status == 403, path
    assert trial_db.message_enabled("*") is True


def test_the_panel_has_the_trial_blocks_and_calls_the_api():
    assert "Триал · сообщения" in PANEL and 'id="trialOverviewBox"' in PANEL and "loadTrialOverview();" in PANEL
    assert "'<div id=\"trialSection\">' + trialHtml(u) + '</div>'" in PANEL and "bindTrial(u);" in PANEL
    for needle in ("/api/admin/trial'", "/api/admin/trial/toggle", "'/api/admin/user/' + id + '/trial'", "data-trial-shift", "data-trial-act=\"pay_test\"",
                   "data-trial-act=\"reset\"", "data-trial-send", "Машина времени", "Обнулить старт", "Оплатить тестово", "CLOSED_CHANNEL_ID"):
        assert needle in PANEL, needle
