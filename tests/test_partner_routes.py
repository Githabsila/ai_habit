"""
Партнёрская программа в приложении и админке (ТЗ 28.10.2026, §20–§24): цифры участника, ссылки, карточка пригласившего, выплаты и исправления только для админа,
начисление при подтверждении платежа и сторно при возврате, стартовая ссылка p_<код>, один сигнал участнику о новом начислении, экраны и симуляции.
"""
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import partner_scheduler
from db import add_user, partners
from db.core import connect
from db.shop import begin_payment, finish_payment, mark_payment_refunded
from db.sim_lab import SCENES
from db.subscription import record_subscription_payment
from db.traffic import parse_payload
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
PANEL = (STATIC / "admin_panel.html").read_text(encoding="utf-8")
_counter = itertools.count(865_700_000, 100)
UTC = timezone.utc


@pytest.fixture
def uid():
    return next(_counter)


@pytest.fixture
def admin(uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    add_user(uid, "admin", "Админ")
    return uid


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _user(uid_, name="Аня"):
    add_user(uid_, f"u{uid_}", name)
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    return uid_


def _partner(uid_):
    _user(uid_)
    assert partners.add_partner(uid_, 1) == {"ok": True}
    record_subscription_payment(uid_, months=1)
    return uid_


# ---------------------------------------------------------------------------------------------------------------- маршруты участника

async def test_a_participant_sees_his_numbers_and_makes_labeled_links_others_get_404(client, uid):
    owner, stranger = _partner(uid), _user(uid + 1)
    assert (await client.get("/api/partner/stats", headers=_headers(stranger))).status == 404
    stats = await (await client.get("/api/partner/stats", headers=_headers(owner))).json()
    assert stats["share_percent"] == 40 and stats["window_days"] == 30 and len(stats["links"]) == 1 and stats["links"][0]["label"] == "Личная"
    made = await client.post("/api/partner/link", headers=_headers(owner), json={"label": "Канал про привычки"})
    body = await made.json()
    assert made.status == 200 and [l["label"] for l in body["links"]] == ["Личная", "Канал про привычки"]
    assert (await client.post("/api/partner/link", headers=_headers(owner), json={"label": " "})).status == 400
    assert (await client.post("/api/partner/link", headers=_headers(stranger), json={"label": "Тест"})).status == 404


async def test_the_newcomer_sees_the_inviter_card_and_can_add_him_as_a_friend_only_by_himself(client, uid):
    owner = _partner(uid)
    _sql("UPDATE users SET handle=? WHERE telegram_id=?", (f"route{owner}", owner))
    friend = _user(uid + 1, "Боря")
    assert (await (await client.get("/api/partner/inviter", headers=_headers(friend))).json())["inviter"] is None
    assert (await client.post("/api/partner/inviter/friend", headers=_headers(friend))).status == 404, "без привязки дружить с пригласившим не с кем"
    partners.register_touch(friend, partners.ensure_personal_link(owner)["code"])
    card = (await (await client.get("/api/partner/inviter", headers=_headers(friend))).json())["inviter"]
    assert card["name"] == "Аня" and card["handle"] == f"@route{owner}"
    boot = await (await client.get("/api/bootstrap", headers=_headers(friend))).json()
    assert boot["inviter_card"]["handle"] == f"@route{owner}" and boot["is_partner"] is False
    assert (await (await client.get("/api/bootstrap", headers=_headers(owner))).json())["is_partner"] is True
    assert _sql("SELECT COUNT(*) AS n FROM follows WHERE follower_id=? AND followee_id=?", (friend, owner))["n"] == 0, "пока человек сам не нажал, дружбы нет"
    assert (await client.post("/api/partner/inviter/friend", headers=_headers(friend))).status == 200
    assert _sql("SELECT COUNT(*) AS n FROM follows WHERE follower_id=? AND followee_id=?", (friend, owner))["n"] == 1
    assert _sql("SELECT COUNT(*) AS n FROM follows WHERE follower_id=? AND followee_id=?", (owner, friend))["n"] == 1


# ---------------------------------------------------------------------------------------------------------------- админка

async def test_the_admin_routes_are_for_admins_only(client, uid):
    person = _user(uid)
    for method, path in (("get", "/api/admin/partners"), ("post", "/api/admin/partners"), ("post", f"/api/admin/partners/{person}/payout"),
                         ("post", "/api/admin/partners/accrual/1"), ("post", "/api/admin/partners/settings")):
        response = await getattr(client, method)(path, headers=_headers(person), **({"json": {}} if method == "post" else {}))
        assert response.status == 403, path


async def test_the_admin_adds_a_partner_sets_conditions_pays_and_corrects_with_a_journal(client, uid, admin):
    person = _user(uid + 1)
    record_subscription_payment(person, months=1)
    bad = await client.post("/api/admin/partners", headers=_headers(admin), json={"user_id": 999999999})
    assert bad.status == 404
    added = await client.post("/api/admin/partners", headers=_headers(admin), json={"user_id": person, "share_percent": 50, "note": "пилот"})
    assert added.status == 200 and (await client.post("/api/admin/partners", headers=_headers(admin), json={"user_id": person})).status == 409
    overview = await (await client.get("/api/admin/partners", headers=_headers(admin))).json()
    mine = [p for p in overview["partners"] if p["user_id"] == person][0]
    assert mine["share_percent"] == 50 and mine["window_days"] == 30 and mine["subscription_active"] is True and overview["settings"]["attribution_rule"] == "first"
    cond = await client.post(f"/api/admin/partners/{person}/conditions", headers=_headers(admin), json={"window_days": 90, "valid_until": "2099-01-01 00:00:00"})
    assert cond.status == 200
    detail = await (await client.get(f"/api/admin/partners/{person}", headers=_headers(admin))).json()
    assert detail["window_days"] == 90 and detail["valid_until"].startswith("2099") and detail["payable_items"] == []
    assert (await client.post(f"/api/admin/partners/{person}/payout", headers=_headers(admin), json={})).status == 409, "выплачивать нечего"
    friend = uid + 2
    _user(friend)
    partners.register_touch(friend, partners.ensure_personal_link(person)["code"])
    charge = f"adm{friend}"
    _sql("INSERT INTO stars_payments(charge_id, user_id, payload, amount, purchased_at, status) VALUES (?, ?, 'x', 153, datetime('now'), 'done')", (charge, friend))
    accrual = partners.accrue_payment(charge)
    fix = await client.post(f"/api/admin/partners/accrual/{accrual['id']}", headers=_headers(admin), json={"action": "hold", "reason": "проверить аккаунт"})
    assert fix.status == 200
    assert (await client.post(f"/api/admin/partners/accrual/{accrual['id']}", headers=_headers(admin), json={"action": "hold"})).status == 400, "причина обязательна"
    changed = await client.post("/api/admin/partners/settings", headers=_headers(admin), json={"key": "attribution_rule", "value": "last"})
    assert (await changed.json())["settings"]["attribution_rule"] == "last"
    assert (await client.post("/api/admin/partners/settings", headers=_headers(admin), json={"key": "attribution_rule", "value": "x"})).status == 400
    await client.post("/api/admin/partners/settings", headers=_headers(admin), json={"key": "attribution_rule", "value": "first"})
    log = (await (await client.get("/api/admin/partners", headers=_headers(admin))).json())["audit"]
    assert {"add_partner", "conditions", "accrual_hold", "setting"} <= {row["action"] for row in log}


# ---------------------------------------------------------------------------------------------------------------- платёж, возврат, ссылка, сигнал

def test_a_confirmed_payment_accrues_at_once_and_a_refund_reverses_it(uid):
    owner = _partner(uid)
    friend = uid + 1
    _user(friend)
    partners.register_touch(friend, partners.ensure_personal_link(owner)["code"])
    assert begin_payment("hook1", friend, "subscription:x", 153) == "new"
    assert _sql("SELECT COUNT(*) AS n FROM partner_accruals WHERE charge_id='hook1'")["n"] == 0, "пока платёж не подтверждён, начислять нельзя"
    finish_payment("hook1", True)
    row = _sql("SELECT * FROM partner_accruals WHERE charge_id='hook1'")
    assert row["amount"] == 61 and row["status"] == "accrued"
    mark_payment_refunded("hook1")
    assert _sql("SELECT status FROM partner_accruals WHERE charge_id='hook1'")["status"] == "reversed"


def test_a_failed_payment_gives_nothing_and_a_payment_error_never_breaks_the_hook(uid, monkeypatch):
    owner = _partner(uid)
    friend = uid + 1
    _user(friend)
    partners.register_touch(friend, partners.ensure_personal_link(owner)["code"])
    begin_payment("hook2", friend, "subscription:x", 153)
    finish_payment("hook2", False, "ошибка")
    assert _sql("SELECT COUNT(*) AS n FROM partner_accruals WHERE charge_id='hook2'")["n"] == 0
    monkeypatch.setattr(partners, "accrue_payment", lambda charge: (_ for _ in ()).throw(RuntimeError("boom")))
    begin_payment("hook3", friend, "subscription:x", 153)
    finish_payment("hook3", True)                                     # не бросает: деньги человека важнее учёта программы
    assert _sql("SELECT status FROM stars_payments WHERE charge_id='hook3'")["status"] == "done"


def test_the_start_link_of_a_participant_is_recognised():
    assert parse_payload("p_ab12cd34") == ("partner", "ab12cd34")
    assert parse_payload("p_") == (None, None) and parse_payload("p_ab!c") == (None, None)
    assert parse_payload("src_abc")[0] == "src" and parse_payload("friend_5") == ("friend", 5) and parse_payload("123") == ("ref", 123)


async def test_the_participant_gets_one_signal_for_each_new_accrual(uid):
    owner = _partner(uid)
    friend = uid + 1
    _user(friend)
    partners.register_touch(friend, partners.ensure_personal_link(owner)["code"])
    _sql("INSERT INTO stars_payments(charge_id, user_id, payload, amount, purchased_at, status) VALUES ('sig1', ?, 'x', 153, datetime('now'), 'done')", (friend,))
    bot = SimpleNamespace(send_message=AsyncMock())
    await partner_scheduler.run_partner_jobs(bot)
    sent = [c for c in bot.send_message.call_args_list if c.args[0] == owner]
    assert len(sent) == 1 and "61 ⭐" in sent[0].args[1] and "Приглашённый" not in sent[0].args[1] and str(friend) not in sent[0].args[1]
    await partner_scheduler.run_partner_jobs(bot)
    assert len([c for c in bot.send_message.call_args_list if c.args[0] == owner]) == 1, "второй проход сигнала не повторяет"


# ---------------------------------------------------------------------------------------------------------------- экраны

def test_the_screens_scenes_and_admin_card_exist():
    for marker in ('id="partnerOverlay"', 'id="inviterCard"', 'id="partnerSettingsRow"', 'id="partnerLinks"', 'id="inviterFriend"', 'id="inviterFollow"', 'id="inviterCopy"'):
        assert marker in INDEX, marker
    for needle in ("function openPartnerScreen", "function renderInviterCard", "function initPartner", "/api/partner/stats", "/api/partner/inviter/friend", "start=p_"):
        assert needle in APP_JS, needle
    assert {"partner_screen", "inviter_card"} <= {scene[0] for scene in SCENES}
    assert "partner_screen:" in APP_JS and "inviter_card:" in APP_JS
    for needle in ("loadPartners", "/api/admin/partners", "data-partner-pay", "partnersBox"):
        assert needle in PANEL, needle
    section = APP_JS[APP_JS.index("function renderPartnerScreen"):][:2600]
    assert "(а)" not in section and "(ась)" not in section, "в тексте раздела нет рода"
