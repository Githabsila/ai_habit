"""
Каналы трафика (db/traffic.py): отдельная ссылка на каждый канал/блогера, запоминание «откуда пришёл» по первому касанию,
статистика качества (дошли до привычки, возвращаются, «мёртвые», всплески регистраций, друзья, оплаты) и экран в админке.
"""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from db import add_habit, add_user, complete_habit, get_habits, get_user
from db import traffic
from db.core import connect
from tests.conftest import sign_init_data

_uid_counter = itertools.count(895_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _user(uid_, minutes_ago=0, source=None):
    add_user(uid_, f"u{uid_}", "Аня")
    stamp = (_now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE users SET created_at=?, access_status='approved', source_code=? WHERE telegram_id=?", (stamp, source, uid_))
    conn.commit()
    conn.close()
    return uid_


def _code(prefix):
    return f"{prefix}{next(_uid_counter) % 100000}"


# ---------------------------------------------------------------------------------------------------------------- ссылки и коды

@pytest.mark.parametrize("title,expected", [
    ("Фитнес-канал Аня", "fitnes_kanal_anya"), ("Run Club #1", "run_club_1"), ("Щука и Ёж", "schuka_i_ezh"), ("日本語", ""), ("", ""),
])
def test_slug(title, expected):
    assert traffic.slug(title) == expected


@pytest.mark.parametrize("payload,expected", [
    ("src_fit_1", ("src", "fit_1")), ("src_A!", (None, None)), ("src_ab", (None, None)), ("123456", ("ref", 123456)),
    ("friend_55", ("friend", 55)), ("friend_x", (None, None)), ("", (None, None)), (None, (None, None)), ("hello", (None, None)),
])
def test_parse_payload(payload, expected):
    assert traffic.parse_payload(payload) == expected


def test_link_and_create_source():
    title = f"Канал про бег {next(_uid_counter)}"
    created = traffic.create_source(title, "channel", "реклама 20 октября")
    assert created["ok"] and created["code"].startswith("kanal_pro_beg_") and created["kind"] == "channel"
    assert traffic.link(created["code"], "adam_bot") == f"https://t.me/adam_bot?start=src_{created['code']}"
    assert traffic.link(created["code"], None) is None
    same_title = traffic.create_source(title)
    assert same_title["ok"] and same_title["code"] != created["code"], "одинаковые названия не сливаются в один канал"
    assert any(s["code"] == created["code"] and s["note"] == "реклама 20 октября" for s in traffic.list_sources())


def test_codes_are_validated_and_manual_codes_are_never_silently_changed():
    code = _code("manual")
    assert traffic.create_source("Ручной", code=code)["code"] == code
    assert traffic.create_source("Ручной 2", code=code) == {"error": "code_taken"}
    assert traffic.create_source("Плохой", code="Bad Code!") == {"error": "invalid_code"}
    assert traffic.create_source("Служебный", code="ref") == {"error": "invalid_code"}
    assert traffic.create_source("x") == {"error": "invalid_title"}
    hieroglyphs = traffic.create_source("日本語日本語")
    assert hieroglyphs["ok"] and hieroglyphs["code"].startswith("ch_")


def test_archived_sources_disappear_from_the_list_but_keep_working():
    code = traffic.create_source(f"Архивный {next(_uid_counter)}")["code"]
    assert traffic.archive_source(code) and not traffic.archive_source("nope_nope")
    assert code not in [s["code"] for s in traffic.list_sources()]
    assert code in [s["code"] for s in traffic.list_sources(include_archived=True)]
    person = _user(next(_uid_counter))
    assert traffic.attribute(person, code), "ссылка в старой рекламе продолжает приводить людей"


# ---------------------------------------------------------------------------------------------------------------- «откуда пришёл»

def test_the_first_touch_wins_and_old_accounts_are_never_reattributed(uid):
    a, b = traffic.create_source(f"Первый {uid}")["code"], traffic.create_source(f"Второй {uid}")["code"]
    person = _user(uid, minutes_ago=2)
    assert traffic.attribute(person, a) is True
    assert traffic.attribute(person, b) is False and get_user(person)["source_code"] == a
    old = _user(uid + 1, minutes_ago=traffic.ATTRIBUTION_WINDOW_MIN + 5)
    assert traffic.attribute(old, a) is False and get_user(old)["source_code"] is None, "старому аккаунту канал не приписываем"
    assert traffic.attribute(person, "no_such_code") is False and traffic.attribute(_user(uid + 2), "no_such_code") is False
    assert traffic.attribute(_user(uid + 3), "Bad Code") is False
    ref = _user(uid + 4)
    assert traffic.attribute(ref, "ref") is True and get_user(ref)["source_code"] == "ref"


def test_attribution_is_recorded_as_an_event(uid):
    code = traffic.create_source(f"Событие {uid}")["code"]
    person = _user(uid)
    traffic.attribute(person, code, via="app")
    conn = connect()
    row = conn.execute("SELECT step, meta FROM user_events WHERE user_id=? AND name='source_attributed'", (person,)).fetchone()
    conn.close()
    assert row["step"] == code and "app" in row["meta"]


def test_the_code_is_read_from_the_mini_app_start_param(uid):
    code = traffic.create_source(f"Приложение {uid}")["code"]
    person = _user(uid)
    assert traffic.attribute_from_init_data(person, f"query_id=x&start_param=src_{code}&auth_date=1") is True
    assert get_user(person)["source_code"] == code
    other = _user(uid + 1)
    assert traffic.attribute_from_init_data(other, "start_param=hello") is False
    assert traffic.attribute_from_init_data(other, "") is False and traffic.attribute_from_init_data(other, None) is False


async def test_start_command_with_a_channel_link_remembers_the_channel_and_still_runs_the_survey(uid, monkeypatch):
    import handlers.start as start_mod
    code = traffic.create_source(f"Старт {uid}")["code"]
    monkeypatch.setattr(start_mod, "begin_survey", AsyncMock())
    message = SimpleNamespace(text=f"/start src_{code}", from_user=SimpleNamespace(id=uid, username="u", first_name="Аня"), answer=AsyncMock(), bot=None)
    await start_mod.start(message, SimpleNamespace())
    assert get_user(uid)["source_code"] == code and get_user(uid)["referrer_id"] is None
    start_mod.begin_survey.assert_awaited()


async def test_a_friend_invite_is_its_own_channel_and_still_gives_the_bonus(uid, monkeypatch):
    import handlers.start as start_mod
    inviter = _user(uid)
    monkeypatch.setattr(start_mod, "begin_survey", AsyncMock())
    monkeypatch.setattr(start_mod, "_announce_friendship", AsyncMock())
    message = SimpleNamespace(text=f"/start {inviter}", from_user=SimpleNamespace(id=uid + 1, username="u2", first_name="Боря"), answer=AsyncMock(), bot=None)
    await start_mod.start(message, SimpleNamespace())
    invited = get_user(uid + 1)
    assert invited["referrer_id"] == inviter and invited["source_code"] == "ref"


# ---------------------------------------------------------------------------------------------------------------- статистика качества

def _set_created(user_ids, minutes_ago_list):
    conn = connect()
    for user_id, minutes in zip(user_ids, minutes_ago_list):
        conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", ((_now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S"), user_id))
    conn.commit()
    conn.close()


def test_channel_stats_tell_good_traffic_from_dead_traffic(uid, monkeypatch):
    from db import tracking
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    good, dead = traffic.create_source(f"Живой {uid}")["code"], traffic.create_source(f"Мёртвый {uid}")["code"]
    day = 24 * 60 + 600
    good_users = [_user(uid + i, source=good) for i in range(5)]
    _set_created(good_users, [day + i * 120 for i in range(5)])
    for user_id in good_users[:3]:
        add_habit(user_id, "Зарядка")
        complete_habit(get_habits(user_id)[0]["id"])
    conn = connect()
    conn.execute("INSERT INTO app_opens(user_id, at) VALUES (?, datetime('now'))", (good_users[0],))
    conn.execute("INSERT INTO stars_payments(charge_id, user_id, payload, amount) VALUES (?,?,?,?)", (f"ch{uid}", good_users[0], "x", 153))
    conn.execute("UPDATE users SET referrals=2 WHERE telegram_id=?", (good_users[1],))
    conn.commit()
    conn.close()
    dead_users = [_user(uid + 50 + i, source=dead) for i in range(14)]
    _set_created(dead_users, [day + i for i in range(14)])                                  # 14 регистраций за 14 минут, больше ничего
    stats = {s["code"]: s for s in traffic.channel_stats(days=3)}
    g, d = stats[good], stats[dead]
    assert g["joined"] == 5 and g["habit_done"] == 60.0 and g["paid"] == 1 and g["revenue_stars"] == 153 and g["friends_invited"] == 2
    assert g["dead"] == 2 and g["suspicious"] is False and g["quality"] > d["quality"]
    assert d["joined"] == 14 and d["dead_pct"] == 100.0 and d["burst_pct"] == 78.6 and d["suspicious"] is True
    assert stats["ref"]["title"] == "Приглашения друзей" and stats["none"]["title"] == "Без канала"


def test_young_accounts_are_not_judged_dead_yet(uid, monkeypatch):
    from db import tracking
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    code = traffic.create_source(f"Свежий {uid}")["code"]
    for i in range(12):
        _user(uid + i, minutes_ago=30 + i * 60, source=code)                              # все моложе суток
    stats = {s["code"]: s for s in traffic.channel_stats(days=3)}[code]
    assert stats["joined"] == 12 and stats["judged"] == 0 and stats["dead_pct"] == 0.0 and stats["suspicious"] is False


def test_an_empty_channel_is_listed_with_zeros(uid):
    code = traffic.create_source(f"Пустой {uid}")["code"]
    stats = {s["code"]: s for s in traffic.channel_stats(days=7)}[code]
    assert stats["joined"] == 0 and stats["quality"] == 0.0 and stats["title"].startswith("Пустой")


def test_funnel_can_be_cut_by_channel(uid, monkeypatch):
    from db import tracking
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    code = traffic.create_source(f"Воронка {uid}")["code"]
    person = _user(uid, source=code)
    add_habit(person, "Зарядка")
    only = tracking.funnel(days=1, source=code)
    assert only["total"] == 1 and {s["key"]: s["count"] for s in only["steps"]}["habit"] == 1


# ---------------------------------------------------------------------------------------------------------------- админ-API

async def test_admin_creates_a_link_and_sees_the_channel_with_stats(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    monkeypatch.setattr(routes_admin, "_ADMIN_BOT_USERNAME", {"value": None})
    client.app["bot"] = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="adam_test_bot")))
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    title = f"Рекламный канал {uid}"
    created = await client.post("/api/admin/traffic", json={"title": title, "kind": "channel", "note": "тест"}, headers=headers)
    body = await created.json()
    assert created.status == 200 and body["link"] == f"https://t.me/adam_test_bot?start=src_{body['code']}"
    person = _user(uid + 1, source=body["code"])
    data = await (await client.get("/api/admin/traffic?days=7", headers=headers)).json()
    mine = next(c for c in data["channels"] if c["code"] == body["code"])
    assert mine["joined"] >= 0 and mine["link"] == body["link"] and data["bot_username"] == "adam_test_bot"
    assert next(c for c in data["channels"] if c["code"] == "ref")["link"] is None, "у «приглашений друзей» своей ссылки нет"
    assert person
    assert (await client.post("/api/admin/traffic", json={"title": title, "code": body["code"]}, headers=headers)).status == 409
    assert (await client.post("/api/admin/traffic", json={"title": "x"}, headers=headers)).status == 400
    assert (await client.post(f"/api/admin/traffic/{body['code']}/archive", headers=headers)).status == 200
    assert (await client.post("/api/admin/traffic/unknown_code/archive", headers=headers)).status == 404
    assert (await client.get("/api/admin/traffic", headers={"Authorization": f"tma {sign_init_data(uid + 1)}"})).status == 403
