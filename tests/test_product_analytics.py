"""
Аналитика (db/usage.py, db/stats.py, db/analyst.py, webapp/routes_analytics.py, tracker.js v2): автоподхват кликов, экранов, версий и вызовов API,
честные расчёты (настоящие люди, удержание по дням, «мало данных»), карта функций, ИИ-аналитик с памятью и доступ только админам.

Расчёты глобальные, поэтому каждый тест работает в своей чистой базе (fixture fresh), а не в общей тестовой.
"""
import itertools
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db import tracking, usage, stats, analyst
from db.core import connect
from tests.conftest import sign_init_data


@pytest.fixture(scope="module")
def _template(tmp_path_factory):
    """Пустая база со всеми таблицами — создаётся один раз на файл (create_tables небыстрая), каждый тест получает её копию."""
    import db.core as core
    folder = tmp_path_factory.mktemp("analytics_template")
    patch = pytest.MonkeyPatch()
    patch.setattr(core, "DATA_DIR", str(folder))
    patch.setattr(core, "DB_PATH", str(folder / "users.db"))
    core.create_tables()
    conn = core.connect()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    patch.undo()
    return folder / "users.db"


@pytest.fixture
def fresh(tmp_path, monkeypatch, _template):
    import shutil
    import db.core as core
    shutil.copy(_template, tmp_path / "users.db")
    monkeypatch.setattr(core, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "users.db"))
    usage._known.clear()
    usage._buffer.clear()
    usage._bot_buffer.clear()
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    from webapp import routes_analytics
    routes_analytics.invalidate_cache()
    yield


_ids = itertools.count(1000)


def today():
    return stats._today()


def day(offset=0):
    return (today() - timedelta(days=offset)).isoformat()


def make_user(days_ago=0, source=None, **columns):
    uid = next(_ids)
    conn = connect()
    conn.execute("INSERT INTO users(telegram_id, username, first_name, created_at, access_status, source_code) VALUES (?,?,?,?,?,?)",
                 (uid, f"user{uid}", "Аня", f"{day(days_ago)} 12:00:00", "approved", source))
    for column, value in columns.items():
        conn.execute(f"UPDATE users SET {column}=? WHERE telegram_id=?", (value, uid))
    conn.commit()
    conn.close()
    return uid


def active(uid, *offsets):
    conn = connect()
    for offset in offsets:
        conn.execute("INSERT OR IGNORE INTO daily_active(day, user_id) VALUES (?,?)", (day(offset), uid))
    conn.commit()
    conn.close()


def click(uid, label, screen="home", offset=0, n=1):
    conn = connect()
    usage.record_ui(conn, uid, clicks=[[label, screen, n]], day=day(offset))
    conn.commit()
    conn.close()


def view(uid, screen, offset=0, views=1, seconds=20):
    conn = connect()
    usage.record_ui(conn, uid, screens={screen: {"v": views, "s": seconds}}, day=day(offset))
    conn.commit()
    conn.close()


def rows(sql, params=()):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- автоподхват

def test_clicks_and_screens_accumulate_and_new_ones_enter_the_catalog(fresh):
    conn = connect()
    usage.record_ui(conn, 7, clicks=[["shopBtn", "home", 2], ["shopBtn", "home", 3], ["bad", "home", "x"], ["", "home", 1], ["huge", "home", 9999], "garbage"],
                    screens={"shop": {"v": 1, "s": 30}, "broken": {"v": -1, "s": 5}, "weird": "text"}, version="20261013_V9", day="2026-10-13")
    conn.commit()
    conn.close()
    assert rows("SELECT label, clicks FROM ui_clicks") == [{"label": "shopBtn", "clicks": 5}]
    assert rows("SELECT screen, views, seconds FROM ui_screens") == [{"screen": "shop", "views": 1, "seconds": 30}]
    catalog = {(r["kind"], r["key"]) for r in rows("SELECT kind, key FROM ui_catalog")}
    assert catalog == {("click", "shopBtn"), ("screen", "shop"), ("version", "20261013_V9")}
    assert rows("SELECT first_version, first_screen FROM ui_catalog WHERE key='shopBtn'") == [{"first_version": "20261013_V9", "first_screen": "home"}]


def test_a_broken_layout_cannot_flood_the_catalog(fresh, monkeypatch):
    monkeypatch.setattr(usage, "MAX_CATALOG", 3)
    conn = connect()
    usage.record_ui(conn, 7, clicks=[[f"dyn-{i}", "home", 1] for i in range(10)])
    conn.commit()
    conn.close()
    assert rows("SELECT COUNT(*) AS n FROM ui_catalog WHERE kind='click'")[0]["n"] == 3
    assert rows("SELECT COUNT(*) AS n FROM ui_clicks")[0]["n"] == 3


def test_ingest_keeps_the_version_in_the_session_and_garbage_does_not_drop_events(fresh):
    uid = make_user()
    session = {"sid": "sid-aaaaaa", "started": 1_000_000, "active": 10, "actions": 1, "screen": "home", "hidden": False, "platform": "ios", "tz": 180, "last": []}
    accepted = tracking.ingest(uid, session, [{"n": "screen", "s": "shop", "t": 1_000_000}], 1_000_000,
                               usage={"clicks": [{"a": 1}, ["openShopBtn", "home", 2]], "screens": {"shop": {"v": 1, "s": 12}}}, version="20261013_TRACK_V2")
    assert accepted == 1
    assert rows("SELECT version FROM app_sessions WHERE id='sid-aaaaaa'") == [{"version": "20261013_TRACK_V2"}]
    assert rows("SELECT label, clicks FROM ui_clicks WHERE user_id=?", (uid,)) == [{"label": "openShopBtn", "clicks": 2}]
    # мусор вместо словаря счётчиков и версия с недопустимыми знаками — события всё равно приняты, версия отброшена
    accepted = tracking.ingest(uid, session, [{"n": "screen", "s": "stats", "t": 1_000_000}], 1_000_000, usage={"clicks": "no", "screens": ["x"]}, version="<script>")
    assert accepted == 1


def test_api_calls_are_buffered_and_flushed_in_one_batch(fresh):
    for status, ms in ((200, 10), (200, 30), (200, 20), (500, 90), (404, 5)):
        usage.record_api("GET /api/demo/{id}", status, ms, 55, day="2026-10-13")
    assert rows("SELECT * FROM api_usage") == []          # горячий путь в базу не пишет
    assert usage.flush() == 1
    assert rows("SELECT route, user_id, calls, errors, client_errors, total_ms, max_ms FROM api_usage") == [
        {"route": "GET /api/demo/{id}", "user_id": 55, "calls": 5, "errors": 1, "client_errors": 1, "total_ms": 155, "max_ms": 90}]
    assert usage.flush() == 0
    assert ("api", "GET /api/demo/{id}") in {(r["kind"], r["key"]) for r in rows("SELECT kind, key FROM ui_catalog")}


def test_bot_actions_have_stable_labels_without_message_text_or_ids(fresh):
    from aiogram.types import CallbackQuery, Chat, Message, User
    from middlewares.usage import UsageMiddleware, action_label
    person = User(id=77, is_bot=False, first_name="Аня")
    chat = Chat(id=77, type="private")
    now = datetime.now()
    message = lambda **kw: Message(message_id=1, date=now, chat=chat, from_user=person, **kw)  # noqa: E731
    assert action_label(CallbackQuery(id="1", from_user=person, chat_instance="c", data="complete_12345")) == "cb:complete_#"
    assert action_label(message(text="/start@adam_bot src_abc")) == "cmd:/start"
    assert action_label(message(text="расскажи секрет про меня")) == "msg:text"      # текст сообщения не сохраняется
    calls = []

    async def handler(event, data):
        calls.append(event)

    import asyncio
    asyncio.run(UsageMiddleware()(handler, message(text="/habits"), {}))
    asyncio.run(UsageMiddleware()(handler, CallbackQuery(id="2", from_user=person, chat_instance="c", data="shop"), {}))
    assert len(calls) == 2                                  # обработка не тронута
    usage.flush()
    assert {r["label"] for r in rows("SELECT label FROM ui_clicks WHERE user_id=77 AND screen='bot'")} == {"cmd:/habits", "cb:shop"}


async def test_api_middleware_counts_calls_per_person_and_skips_admin_and_tracking_routes(fresh, client, monkeypatch):
    import webapp.routes_admin as routes_admin
    person = 4242
    admin = 4243
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    headers = {"Authorization": f"tma {sign_init_data(person)}"}
    assert (await client.get("/api/shop", headers=headers)).status == 200
    assert (await client.get("/api/shop")).status == 401                                   # без подписи: ошибка клиента, человека нет
    await client.get("/api/admin/stats", headers={"Authorization": f"tma {sign_init_data(admin)}"})
    await client.post("/api/track", json={"init_data": sign_init_data(person), "session": None, "events": []})
    usage.flush()
    got = {(r["route"], r["user_id"]): r for r in rows("SELECT * FROM api_usage")}
    assert got[("GET /api/shop", person)]["calls"] == 1 and got[("GET /api/shop", person)]["errors"] == 0
    assert got[("GET /api/shop", 0)]["client_errors"] == 1
    assert not [route for route, _ in got if route.startswith(("GET /api/admin", "POST /api/track"))]
    # мусорные пути не плодят маршруты в учёте
    await client.get("/api/definitely-not-a-route-1")
    await client.get("/api/definitely-not-a-route-2")
    usage.flush()
    routes = {r["route"] for r in rows("SELECT route FROM api_usage")}
    assert "GET (нет такого маршрута)" in routes and not [r for r in routes if "definitely" in r]


def test_old_daily_counters_are_cleaned_but_the_catalog_stays(fresh):
    person = make_user()
    click(person, "oldBtn", "home", offset=400)
    click(person, "freshBtn", "home", offset=1)
    conn = connect()
    conn.execute("INSERT INTO api_usage(day, route, user_id, calls) VALUES (?,?,?,?)", (day(400), "GET /x", 1, 5))
    conn.commit()
    conn.close()
    assert usage.cleanup(180) == 2
    assert [r["label"] for r in rows("SELECT label FROM ui_clicks")] == ["freshBtn"]
    assert ("click", "oldBtn") in {(r["kind"], r["key"]) for r in rows("SELECT kind, key FROM ui_catalog")}


def test_a_failed_flush_keeps_the_counters_for_the_next_try(fresh, monkeypatch):
    usage.record_api("GET /api/x", 200, 10, 1, day="2026-10-13")
    usage.bot_action(1, "cmd:/start")
    real_connect = usage.connect

    def locked():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(usage, "connect", locked)
    assert usage.flush() == 0
    monkeypatch.setattr(usage, "connect", real_connect)
    assert usage.flush() == 2                                   # ничего не потеряно и не задвоено
    assert rows("SELECT calls FROM api_usage") == [{"calls": 1}]
    assert rows("SELECT clicks FROM ui_clicks WHERE label='cmd:/start'") == [{"clicks": 1}]


def test_the_funnel_counts_returning_people_from_the_permanent_activity_days(fresh):
    person = make_user(5)
    active(person, 4, 3)                                        # вернулся на 1-й и 2-й день после регистрации
    steps = {s["key"]: s["count"] for s in tracking.funnel(10)["steps"]}
    assert steps["registered"] == 1 and steps["d1"] == 1 and steps["d7"] == 0


# ---------------------------------------------------------------------------------------------------------------- кто считается человеком

def test_only_real_people_are_counted(fresh, monkeypatch):
    real = make_user()
    make_user(stats_excluded=1)
    make_user(banned=1)
    admin = make_user()
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [admin])
    assert stats.overview(7)["tiles"]["users_total"] == 1
    assert real  # единственный настоящий
    stats.set_excluded(real, True)
    assert stats.overview(7)["tiles"]["users_total"] == 0 and stats.is_excluded(real)
    stats.set_excluded(real, False)
    assert stats.overview(7)["tiles"]["users_total"] == 1


def test_excluded_people_disappear_from_the_funnel_and_the_channels_too(fresh):
    make_user(source="chan")
    skipped = make_user(source="chan", stats_excluded=1)
    assert skipped
    assert tracking.funnel(7, "chan")["total"] == 1


# ---------------------------------------------------------------------------------------------------------------- обзор и удержание

def test_overview_series_and_tiles(fresh):
    people = [make_user(0) for _ in range(3)] + [make_user(3) for _ in range(2)]
    for person in people[:3]:
        active(person, 0)
    for person in people[3:]:
        active(person, 3, 1)
    tiles = stats.overview(7)["tiles"]
    assert tiles["users_total"] == 5 and tiles["new_users"] == 5
    assert tiles["dau_today"] == 3 and tiles["dau_yesterday"] == 2 and tiles["wau"] == 5 and tiles["mau"] == 5
    assert tiles["stickiness"] is None                        # меньше 8 человек в месяце — процент не показываем
    series = stats.overview(7)["series"]
    assert series["new_users"][-1] == 3 and series["new_users"][-4] == 2 and series["dau"][-1] == 3 and len(series["days"]) == 7


def test_retention_counts_exact_days_and_leaves_immature_cohorts_out(fresh):
    people = [make_user(10) for _ in range(10)]
    for person in people[:5]:
        active(person, 9)                                      # день 1 (10 − 9)
    for person in people[:3]:
        active(person, 3)                                      # день 7
    fresh_one = make_user(1)
    active(fresh_one, 0)
    result = stats.retention(days=30)
    overall = result["overall"]
    assert overall["d1"] == {"pct": 50.0, "n": 5, "of": 10}    # вчерашний не созрел: его день 1 ещё идёт
    assert overall["d7"] == {"pct": 30.0, "n": 3, "of": 10}
    assert overall["d14"]["of"] == 0 and overall["d14"]["pct"] is None
    assert sum(c["n"] for c in result["cohorts"]) == 11
    assert [p["day"] for p in result["curve"]][:2] == [1, 2]


def test_small_cohorts_show_no_percent(fresh):
    for _ in range(3):
        active(make_user(10), 9)
    assert stats.retention(days=30)["overall"]["d1"] == {"pct": None, "n": 3, "of": 3}


def test_retention_by_channel(fresh):
    for _ in range(8):
        active(make_user(10, source="blog"), 9)
    for _ in range(8):
        make_user(10, source="other")
    assert stats.retention(source="blog", days=30)["overall"]["d1"]["pct"] == 100.0
    assert stats.retention(source="other", days=30)["overall"]["d1"]["pct"] == 0.0


# ---------------------------------------------------------------------------------------------------------------- функции

def test_features_are_counted_through_the_map_and_new_things_are_flagged(fresh):
    people = [make_user(5) for _ in range(10)]
    for i, person in enumerate(people):
        active(person, 0, 1)
        view(person, "shop", offset=0)
        if i < 4:
            click(person, "openShopBtn", "home", 1)                       # кнопка на главной → магазин (по шаблону)
            click(person, "openShopBtn", "home", 0)                       # во второй день → повторный
        if i < 2:
            click(person, "brand-new-button", "home")                     # нигде не описана → в список «не привязано»
    shop = next(f for f in stats.features(7)["features"] if f["id"] == "shop")
    assert shop["users"] == 10 and shop["adoption"]["pct"] == 100.0
    assert shop["repeat"] == {"pct": 40.0, "n": 4, "of": 10}          # во второй день вернулись только те четверо, что нажимали кнопку
    unmapped = stats.features(7)["unmapped"]
    assert [u["key"] for u in unmapped] == ["brand-new-button"] and unmapped[0]["users"] == 2


def test_events_and_api_actions_count_as_use(fresh):
    people = [make_user(2) for _ in range(9)]
    for person in people:
        active(person, 0)
        usage.record_api("POST /api/habits", 200, 20, person, day=day(0))
    conn = connect()
    for person in people:
        conn.execute("INSERT INTO user_events(user_id, at, name, surface) VALUES (?,?,?,?)", (person, f"{day(0)} 10:00:00", "habit_added", "server"))
    conn.commit()
    conn.close()
    usage.flush()
    habits = next(f for f in stats.features(7)["features"] if f["id"] == "habits")
    assert habits["users"] == 9 and habits["adoption"]["pct"] == 100.0 and habits["uses"] >= 18


def test_passive_loads_and_unknown_events_are_not_use(fresh):
    person = make_user()
    active(person, 0)
    usage.record_api("GET /api/bootstrap", 200, 50, person, day=day(0))
    conn = connect()
    conn.execute("INSERT INTO user_events(user_id, at, name, surface) VALUES (?,?,?,?)", (person, f"{day(0)} 10:00:00", "brand_new_event", "server"))
    conn.commit()
    conn.close()
    usage.flush()
    result = stats.features(7)
    assert all(f["users"] == 0 for f in result["features"])
    assert [e["name"] for e in result["unmapped_events"]] == ["brand_new_event"]


def test_feature_impact_compares_returning_people_only_with_enough_data(fresh):
    tried = [make_user(20) for _ in range(10)]
    others = [make_user(20) for _ in range(10)]
    for i, person in enumerate(tried):
        view(person, "shop", offset=19)                      # попробовали на второй день после регистрации
        if i < 8:
            active(person, 13)                                # день 7
    for i, person in enumerate(others):
        if i < 2:
            active(person, 13)
    shop = next(f for f in stats.features(30)["features"] if f["id"] == "shop")
    assert shop["impact"] == {"tried": {"n": 10, "d7": 80.0}, "not_tried": {"n": 10, "d7": 20.0}}
    few = next(f for f in stats.features(30)["features"] if f["id"] == "streak")
    assert few["impact"] is None


def test_buttons_flag_new_ones_and_list_buttons_nobody_pressed(fresh, monkeypatch):
    monkeypatch.setattr(stats, "_declared_buttons", lambda: ["usedBtn", "deadBtn"])
    person = make_user()
    click(person, "usedBtn", "shop", n=4)
    click(person, "habit-item__check=complete", "home", n=2)
    result = stats.buttons(14)
    by_label = {b["label"]: b for b in result["items"]}
    assert by_label["usedBtn"]["clicks"] == 4 and by_label["usedBtn"]["new"] is True and by_label["usedBtn"]["feature"] == "Магазин и косметика"
    assert by_label["habit-item__check=complete"]["title"] == "Отметить привычку"
    assert [b["label"] for b in result["never_clicked"]] == ["deadBtn"]


def test_screens_time_share(fresh):
    person = make_user()
    view(person, "shop", views=2, seconds=60)
    view(person, "stats", views=1, seconds=20)
    result = stats.screens(14)
    first = result["items"][0]
    assert first["screen"] == "shop" and first["avg_seconds_per_view"] == 30.0 and first["share"] == 75.0 and result["total_seconds"] == 80


# ---------------------------------------------------------------------------------------------------------------- версии, здоровье, ритм

def test_versions_show_how_newcomers_behaved_on_each_release(fresh):
    conn = connect()
    old, new = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=12)), (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=6))
    conn.execute("INSERT INTO ui_catalog(kind, key, first_seen, last_seen) VALUES ('version','V1',?,?)", (old.strftime("%Y-%m-%d %H:%M:%S"),) * 2)
    conn.execute("INSERT INTO ui_catalog(kind, key, first_seen, last_seen) VALUES ('version','V2',?,?)", (new.strftime("%Y-%m-%d %H:%M:%S"),) * 2)
    conn.commit()
    conn.close()
    for _ in range(8):
        active(make_user(9), 8)                               # пришли при V1, вернулись на день 1
    for _ in range(8):
        make_user(4)                                          # пришли при V2, не вернулись
    result = stats.versions(5)["items"]
    assert [v["version"] for v in result] == ["V2", "V1"]
    assert result[1]["newcomers"] == 8 and result[1]["newcomers_d1"]["pct"] == 100.0
    assert result[0]["newcomers"] == 8 and result[0]["newcomers_d1"]["pct"] == 0.0


def test_health_lists_failing_and_slow_routes(fresh):
    for _ in range(6):
        usage.record_api("POST /api/habits", 500, 30, 1, day=day(0))
    for _ in range(6):
        usage.record_api("GET /api/shop", 200, 900, 2, day=day(0))
    usage.flush()
    conn = connect()
    conn.execute("INSERT INTO client_errors(user_id, message) VALUES (1,'TypeError: x is undefined')")
    conn.commit()
    conn.close()
    result = stats.health(7)
    assert result["errors"] == 6 and result["routes"][0]["route"] == "POST /api/habits" and result["slowest"][0]["route"] == "GET /api/shop"
    assert result["crash_total"] == 1 and result["crashes"][0]["message"].startswith("TypeError")


def test_rhythm_uses_each_persons_local_time(fresh):
    person = make_user()
    conn = connect()
    stamp = datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0, tzinfo=None)
    conn.execute("INSERT INTO app_sessions(id, user_id, started_at, last_at, tz_offset) VALUES ('rh-aaaaaa',?,?,?,180)", (person, stamp.strftime("%Y-%m-%d %H:%M:%S"),) * 1 + (stamp.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.execute("INSERT INTO app_sessions(id, user_id, started_at, last_at, tz_offset) VALUES ('rh-bbbbbb',?,?,?,NULL)", (person, stamp.strftime("%Y-%m-%d %H:%M:%S"), stamp.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    result = stats.rhythm(30)
    assert result["peak_hour"] == 23 and result["unknown_tz"] == 1 and result["sessions"] == 2   # 20:00 UTC + 3 ч


def test_quality_reports_coverage_exclusions_and_caveats(fresh):
    for _ in range(8):
        person = make_user()
        active(person, 0)
    tracked = make_user()
    active(tracked, 0)
    conn = connect()
    now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO app_sessions(id, user_id, started_at, last_at, ended) VALUES ('q-aaaaaa',?,?,?,1)", (tracked, now, now))
    conn.commit()
    conn.close()
    make_user(stats_excluded=1)
    result = stats.quality()
    assert result["coverage"] == {"active_week": 9, "with_sessions": 1, "pct": 11.1}
    assert len(result["excluded"]) == 1 and result["notes"] and result["tracking_since"]


def test_quiet_new_features_raise_a_few_calm_signals_not_a_flood(fresh):
    for _ in range(10):
        active(make_user(3), 0)
    signals = [s for s in stats.signals()["items"] if s["key"].startswith("low_adoption")]
    assert 1 <= len([s for s in signals if s["key"] != "low_adoption:more"]) <= 3
    assert all(s["level"] == "info" and "ни один из 10" in s["text"] for s in signals if s["key"] != "low_adoption:more")


def test_signals_point_at_errors_and_unmapped_things(fresh):
    person = make_user()
    active(person, 0)
    click(person, "brand-new-button", "home", n=3)
    for _ in range(60):
        usage.record_api("POST /api/habits", 500, 10, person, day=day(0))
    usage.flush()
    keys = {s["key"] for s in stats.signals()["items"]}
    assert {"errors", "unmapped"} <= keys and "route:POST /api/habits" in keys


# ---------------------------------------------------------------------------------------------------------------- ИИ-аналитик

def test_the_analyst_answer_is_parsed_even_when_the_model_wraps_it(fresh):
    wrapped = 'Вот разбор:\n```json\n{"headline": "Люди не доходят до привычки", "findings": [{"title": "Застревают", "evidence": "3 из 14", "area": "funnel", "impact": "high", "action": "упростить"}, "мусор"], "watch": [{"text": "D1 после V64"}, "второе"], "resolved": ["#2", 5, "abc"], "questions": ["Что меняли?"]}\n```'
    parsed = analyst.parse_answer(wrapped)
    assert parsed["headline"] == "Люди не доходят до привычки" and len(parsed["findings"]) == 1 and parsed["findings"][0]["impact"] == "high"
    assert parsed["watch"] == ["D1 после V64", "второе"] and parsed["resolved"] == [2, 5] and parsed["questions"] == ["Что меняли?"]
    assert analyst.parse_answer("просто текст") is None and analyst.parse_answer("{не json}") is None
    assert "Люди не доходят до привычки" in analyst.render(parsed) and "Следить:" in analyst.render(parsed)


async def test_the_analyst_remembers_its_findings_and_closes_them_next_time(fresh, monkeypatch):
    seen_prompts = []

    async def first(system, user, max_tokens):
        seen_prompts.append(user)
        return json.dumps({"headline": "Главное", "findings": [{"title": "Застревают на анкете", "evidence": "5 из 12", "area": "funnel", "impact": "high", "action": "сократить"},
                                                               {"title": "Мелочь", "evidence": "1", "area": "data", "impact": "low", "action": "-"}],
                           "watch": [{"text": "Следить за D1"}], "resolved": [], "questions": []})

    monkeypatch.setattr(analyst, "_ask_model", first)
    monkeypatch.setattr(analyst, "MIN_SECONDS_BETWEEN", 0)
    note = analyst.add_note("Выкатили новый онбординг 13.10")
    assert analyst.add_note("Выкатили новый онбординг 13.10") == note and analyst.add_note("  ") is None       # дубль не плодится
    result = await analyst.run(7)
    assert result["ok"] and result["report"]["report"]["headline"] == "Главное"
    notes = analyst.list_notes("open")
    kinds = {n["kind"] for n in notes}
    assert kinds == {"owner", "finding", "watch"} and len(notes) == 3          # важная находка и «следить» запомнены, мелочь — нет
    assert "Выкатили новый онбординг" in seen_prompts[0]                         # заметка владельца дошла до модели
    finding = next(n for n in notes if n["kind"] == "finding")

    async def second(system, user, max_tokens):
        seen_prompts.append(user)
        return json.dumps({"headline": "Стало лучше", "findings": [], "watch": [], "resolved": [finding["id"]], "questions": []})

    monkeypatch.setattr(analyst, "_ask_model", second)
    await analyst.run(7)
    assert f"#{finding['id']}" in seen_prompts[1] and "Застревают на анкете" in seen_prompts[1]        # прошлое наблюдение было в памяти
    assert finding["id"] not in {n["id"] for n in analyst.list_notes("open")}
    assert analyst.set_note_status(note, "dismissed") and not analyst.set_note_status(999999, "resolved") and not analyst.set_note_status(note, "oops")
    assert len(analyst.history()) == 2 and analyst.latest()["report"]["headline"] == "Стало лучше"


async def test_the_analyst_survives_model_failures_and_plain_text(fresh, monkeypatch):
    monkeypatch.setattr(analyst, "MIN_SECONDS_BETWEEN", 0)

    async def broken(system, user, max_tokens):
        raise RuntimeError("нет ключа")

    monkeypatch.setattr(analyst, "_ask_model", broken)
    assert (await analyst.run(7))["error"] == "model_failed" and analyst.latest() is None

    async def plain(system, user, max_tokens):
        return "Просто текст без разметки"

    monkeypatch.setattr(analyst, "_ask_model", plain)
    result = await analyst.run(7)
    assert result["ok"] and result["report"]["report"] is None and result["report"]["text"] == "Просто текст без разметки"

    monkeypatch.setattr(analyst, "MIN_SECONDS_BETWEEN", 3600)
    assert (await analyst.run(7))["error"] == "too_soon"


def test_the_digest_carries_the_numbers_the_model_needs(fresh):
    for _ in range(9):
        person = make_user(5)
        active(person, 0, 1)
        view(person, "shop")
    text = analyst.build_digest(14)
    for part in ("ПЕРИОД", "ЛЮДИ", "УДЕРЖАНИЕ", "ВОРОНКА", "ФУНКЦИИ", "Магазин и косметика", "ЗДОРОВЬЕ", "ДОСТОВЕРНОСТЬ"):
        assert part in text, part


# ---------------------------------------------------------------------------------------------------------------- доступ и страница

ENDPOINTS = ("overview", "retention", "funnel", "features", "screens", "buttons", "versions", "health", "rhythm", "engagement", "quality", "signals",
             "sources", "ai", "analyst")


async def test_every_analytics_endpoint_is_admin_only_and_works(fresh, client, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin, person = 5001, 5002
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    make_user()
    admin_headers = {"Authorization": f"tma {sign_init_data(admin)}"}
    for name in ENDPOINTS:
        stranger = await client.get(f"/api/analytics/{name}", headers={"Authorization": f"tma {sign_init_data(person)}"})
        assert stranger.status == 403, name
        assert (await client.get(f"/api/analytics/{name}")).status == 401, name
        ok = await client.get(f"/api/analytics/{name}", headers=admin_headers)
        assert ok.status == 200, (name, await ok.text())
    page = await client.get("/analytics")
    assert page.status == 200 and "no-store" in page.headers["Cache-Control"]


async def test_exclusion_by_nick_and_the_analyst_notes_over_http(fresh, client, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin = 5101
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    headers = {"Authorization": f"tma {sign_init_data(admin)}"}
    person = make_user()
    assert stats.overview(7)["tiles"]["users_total"] == 1
    resp = await client.post("/api/analytics/exclude", json={"query": f"@user{person}", "excluded": True}, headers=headers)
    assert resp.status == 200 and (await resp.json())["telegram_id"] == person
    assert (await (await client.get("/api/analytics/overview?days=7", headers=headers)).json())["tiles"]["users_total"] == 0     # кэш сброшен
    assert (await client.post("/api/analytics/exclude", json={"query": "nobody_here_xyz"}, headers=headers)).status == 404
    assert (await client.post("/api/analytics/exclude", json={"query": ""}, headers=headers)).status == 400
    created = await (await client.post("/api/analytics/analyst/notes", json={"text": "Запустили парное задание v2"}, headers=headers)).json()
    assert created["ok"] and created["notes"][0]["kind"] == "owner"
    closed = await client.post(f"/api/analytics/analyst/notes/{created['id']}", json={"status": "resolved"}, headers=headers)
    assert closed.status == 200 and (await closed.json())["notes"] == []
    assert (await client.post("/api/analytics/analyst/notes/abc", json={"status": "resolved"}, headers=headers)).status == 400


async def test_analyst_run_over_http(fresh, client, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin = 5201
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    monkeypatch.setattr(analyst, "MIN_SECONDS_BETWEEN", 0)
    headers = {"Authorization": f"tma {sign_init_data(admin)}"}

    async def fake(system, user, max_tokens):
        return json.dumps({"headline": "Ок", "findings": [], "watch": [], "resolved": [], "questions": []})

    monkeypatch.setattr(analyst, "_ask_model", fake)
    resp = await client.post("/api/analytics/analyst/run", json={"days": 7}, headers=headers)
    assert resp.status == 200 and (await resp.json())["report"]["report"]["headline"] == "Ок"
    state = await (await client.get("/api/analytics/analyst", headers=headers)).json()
    assert state["latest"]["report"]["headline"] == "Ок" and state["history"][0]["headline"] == "Ок"


def test_tracker_v2_counts_buttons_by_stable_labels_and_time_on_screens(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("нужен Node")
    root = Path(__file__).resolve().parent.parent
    script = tmp_path / "usage.js"
    script.write_text(r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const listeners = {}; const sent = []; const timers = []; const intervals = [];
let now = 5000000;
Date.now = () => now;
const makeEl = (props) => Object.assign({ tagName: 'BUTTON', id: '', className: '', textContent: '', dataset: {}, attrs: {},
  getAttribute(n){ return this.attrs[n] || null; }, closest(){ return this; } }, props);
global.window = { Telegram: { WebApp: { initData: 'init=1', platform: 'ios' } }, addEventListener(n, f){ listeners['w:' + n] = f; } };
let openOverlay = null;
global.document = { hidden: false, addEventListener(n, f){ listeners[n] = f; },
  querySelectorAll(){ return openOverlay ? [{ id: openOverlay + 'Overlay', hidden: false, classList: { contains: () => true } }] : []; },
  querySelector(sel){
    if (sel.indexOf('tab-bar') >= 0) return { dataset: { tab: 'home' } };
    if (sel.indexOf('script[src*="/static/app.js"]') >= 0) return { getAttribute: () => '/static/app.js?v=20261013_TEST_V7' };
    return null; } };
global.location = { search: '', pathname: '/' };
Object.defineProperty(global, 'navigator', { configurable: true, value: { sendBeacon(url, blob){ sent.push(JSON.parse(blob.text)); return true; } } });
global.Blob = class { constructor(parts){ this.text = parts.join(''); } };
global.fetch = (url, opts) => { sent.push(JSON.parse(opts.body)); return Promise.resolve(); };
global.setTimeout = (f, ms) => { timers.push({ f, ms }); return timers.length; };
global.setInterval = (f, ms) => { intervals.push({ f, ms }); return intervals.length; };
eval(src);
const tick = () => intervals.filter(i => i.ms === 5000).forEach(i => i.f());
// три нажатия: кнопка с id, кнопка без id с data-action (текст с именем человека не должен попасть в счётчик), кнопка с data-track
listeners.click({ target: makeEl({ id: 'openShopBtn', textContent: 'Магазин' }) });
listeners.click({ target: makeEl({ className: 'habit-item__check is-done', textContent: 'Бегать по утрам', attrs: { 'data-action': 'complete' } }) });
listeners.click({ target: makeEl({ className: 'habit-item__check', textContent: 'Бегать по утрам', attrs: { 'data-action': 'complete' } }) });
listeners.click({ target: makeEl({ textContent: 'Особая', attrs: { 'data-track': 'special-offer' } }) });
// человек открывает магазин и сидит там три тика (активность поддерживаем касанием)
openOverlay = 'shop'; listeners.pointerdown(); tick(); tick(); tick();
document.hidden = true; listeners.visibilitychange();
const body = sent[sent.length - 1];
console.log(JSON.stringify({ v: body.v, clicks: body.usage.clicks, screens: body.usage.screens }));
""", encoding="utf-8")
    result = subprocess.run([node, str(script), str(root / "webapp" / "static" / "tracker.js")], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["v"] == "20261013_TEST_V7"
    labels = {(c[0], c[1]): c[2] for c in out["clicks"]}
    assert labels[("openShopBtn", "home")] == 1 and labels[("habit-item__check=complete", "home")] == 2 and labels[("special-offer", "home")] == 1
    assert not any("Бегать" in c[0] for c in out["clicks"])                       # текст кнопки в счётчик не попадает
    assert out["screens"]["shop"]["s"] == 15 and out["screens"]["shop"]["v"] == 1  # три тика по 5 с на открытом экране «shop»
    assert out["screens"]["home"]["v"] == 1


# ---------------------------------------------------------------------------------------------------------------- еженедельный разбор и сводка

async def test_the_weekly_analysis_goes_to_every_admin_and_a_model_failure_stays_quiet(fresh, monkeypatch):
    import analyst_scheduler as sched
    from unittest.mock import AsyncMock
    monkeypatch.setattr(sched, "ADMIN_IDS", [11, 12])
    monkeypatch.setattr(analyst, "MIN_SECONDS_BETWEEN", 0)

    async def good(system, user, max_tokens):
        return json.dumps({"headline": "Рост <б>", "findings": [{"title": "Застревают", "evidence": "3 из 14", "area": "funnel", "impact": "high", "action": "сократить анкету"}],
                           "watch": [], "resolved": [], "questions": []})

    monkeypatch.setattr(analyst, "_ask_model", good)
    bot = SimpleNamespace(send_message=AsyncMock())
    await sched.run_weekly_analyst(bot)
    assert [call.args[0] for call in bot.send_message.await_args_list] == [11, 12]
    text = bot.send_message.await_args_list[0].args[1]
    assert "Рост &lt;б&gt;" in text and "сократить анкету" in text and "/analytics" in text         # текст экранирован для HTML-разметки Telegram
    assert analyst.latest()["trigger"] == "weekly"

    async def broken(system, user, max_tokens):
        raise RuntimeError("нет ключа")

    monkeypatch.setattr(analyst, "_ask_model", broken)
    quiet = SimpleNamespace(send_message=AsyncMock())
    await sched.run_weekly_analyst(quiet)
    quiet.send_message.assert_not_awaited()


def test_the_daily_digest_gets_only_serious_signals(fresh):
    import analyst_scheduler as sched
    assert sched.build_analytics_block() == ""                     # спокойно — в сводку ничего не добавляется
    person = make_user()
    active(person, 0)
    for _ in range(60):
        usage.record_api("POST /api/habits", 500, 10, person, day=day(0))
    usage.flush()
    block = sched.build_analytics_block()
    assert "Аналитика заметила" in block and "Много ошибок сервера" in block and "/analytics" in block
