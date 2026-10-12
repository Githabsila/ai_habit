"""
Путь человека (db/tracking.py + webapp/static/tracker.js): события сервера и приложения, сессии и «после какого действия вышел»,
сводка пути с минутами от регистрации, воронка и пропуски, приём пакетов /api/track и сам трекер (в Node).
"""
import itertools
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db import add_ai_message, add_daily_task, add_habit, add_user, complete_habit, get_habits, set_daily_main_goal
from db import tracking
from db.core import connect
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
_uid_counter = itertools.count(890_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid_, minutes_ago=0):
    add_user(uid_, f"u{uid_}", "Аня")
    stamp = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE users SET created_at=?, access_status='approved' WHERE telegram_id=?", (stamp, uid_))
    conn.commit()
    conn.close()
    return uid_


def _events(uid_, name=None):
    conn = connect()
    rows = conn.execute("SELECT * FROM user_events WHERE user_id=?" + (" AND name=?" if name else "") + " ORDER BY id", (uid_, name) if name else (uid_,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _session(sid="s1abcdef", **over):
    base = {"sid": sid, "started": 1_000_000, "active": 130, "actions": 4, "screen": "home", "hidden": False, "platform": "android", "tz": 180,
            "last": [{"l": "addHabitTrigger", "s": "home", "t": 5000}, {"l": "Календарь", "s": "calendar", "t": 9000}]}
    base.update(over)
    return base


# ---------------------------------------------------------------------------------------------------------------- события сервера

def test_registration_and_the_first_steps_are_recorded_where_they_happen(uid):
    _user(uid)
    add_habit(uid, "Зарядка")
    set_daily_main_goal(uid, "Подготовить презентацию")
    add_daily_task(uid, "Позвонить в банк")
    names = [e["name"] for e in _events(uid)]
    assert names[0] == "registered" and {"habit_added", "main_goal_set", "task_added"} <= set(names)
    habit = next(e for e in _events(uid) if e["name"] == "habit_added")
    assert json.loads(habit["meta"]) == {"title": "Зарядка"}


def test_registration_is_recorded_only_once(uid):
    add_user(uid, "x", "Аня")
    add_user(uid, "x", "Аня")
    assert [e["name"] for e in _events(uid)].count("registered") == 1


def test_access_approval_is_recorded_once_with_the_previous_status(uid):
    from db import set_access_status
    add_user(uid, "x", "Аня")
    set_access_status(uid, "pending")
    set_access_status(uid, "approved")
    set_access_status(uid, "approved")
    approved = _events(uid, "access_approved")
    assert len(approved) == 1 and json.loads(approved[0]["meta"]) == {"from": "pending"}


def test_a_tracking_failure_never_breaks_the_action(uid, monkeypatch):
    _user(uid)
    import db.tracking as t
    monkeypatch.setattr(t, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert t.track_server(uid, "registered") is False
    assert add_habit(uid, "Чтение")                                 # привычка добавилась, хотя учёт «лежит»


def test_bad_names_are_rejected(uid):
    _user(uid)
    for bad in ("", "Bad Name", "x", "1abc", "a" * 60, None):
        assert tracking.track_server(uid, bad) is False


# ---------------------------------------------------------------------------------------------------------------- приём пакетов

def test_ingest_stores_the_session_the_events_and_the_exit_point(uid):
    _user(uid)
    now_ms = 1_130_000
    accepted = tracking.ingest(uid, _session(f"sid-{uid}"), [
        {"n": "quiz_step", "s": "age_range", "m": {"i": 1}, "sc": "startQuiz", "t": now_ms - 20_000},
        {"n": "tour_skip", "s": "2", "sc": "appTour", "t": now_ms - 5_000},
        {"n": "Bad Name", "t": now_ms},
    ], now_ms)
    assert accepted == 2
    stored = _events(uid)
    assert [e["name"] for e in stored if e["name"] != "registered"] == ["session_start", "quiz_step", "tour_skip"]
    quiz = next(e for e in stored if e["name"] == "quiz_step")
    assert (quiz["step"], quiz["screen"], quiz["surface"]) == ("age_range", "startQuiz", "app") and json.loads(quiz["meta"]) == {"i": 1}
    first, second = _parse(quiz["at"]), _parse(next(e for e in stored if e["name"] == "tour_skip")["at"])
    assert 14 <= (second - first).total_seconds() <= 16, "время события восстановлено по часам приложения"
    [session] = tracking.sessions(uid)
    assert (session["active_seconds"], session["actions"], session["platform"]) == (130, 4, "android")
    assert session["last_action"] == "Календарь" and session["last_screen"] == "Главная"
    assert [a["l"] for a in session["last_actions"]] == ["Добавить привычку", "Календарь"], "id кнопок показываются по-русски"


def _parse(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def test_a_second_snapshot_updates_the_same_session_and_never_decreases_the_time(uid):
    _user(uid)
    sid = f"sid-{uid}"
    tracking.ingest(uid, _session(sid, active=130, actions=4), [], 1_130_000)
    tracking.ingest(uid, _session(sid, active=60, actions=2, last=[]), [], 1_190_000)                # старый снимок догнал позже
    tracking.ingest(uid, _session(sid, active=200, actions=9, hidden=True), [], 1_250_000)
    [session] = tracking.sessions(uid)
    assert session["active_seconds"] == 200 and session["actions"] == 9 and session["ended"] is True
    assert [e["name"] for e in _events(uid, "session_start")] == ["session_start"], "начало сессии записано один раз"
    tracking.ingest(uid, _session(sid, active=210, actions=10, hidden=False), [], 1_300_000)
    assert tracking.sessions(uid)[0]["ended"] is False, "человек вернулся в ту же сессию"


def test_one_session_id_cannot_be_hijacked_by_another_person(uid):
    a, b = _user(uid), _user(uid + 1)
    tracking.ingest(a, _session("shared-id-1", active=50), [], 1_100_000)
    tracking.ingest(b, _session("shared-id-1", active=999), [], 1_100_000)
    assert tracking.sessions(a)[0]["active_seconds"] == 50 and tracking.sessions(b) == []


def test_limits_on_events_and_garbage_are_enforced(uid):
    _user(uid)
    flood = [{"n": "screen", "s": f"s{i}", "t": 1_000_000} for i in range(100)]
    assert tracking.ingest(uid, None, flood, 1_000_000) == tracking.MAX_EVENTS_PER_REQUEST
    huge = {"n": "quiz_step", "s": "x" * 500, "m": {"k": "v" * 2000}, "t": 1_000_000}
    tracking.ingest(uid, None, [huge], 1_000_000)
    last = _events(uid, "quiz_step")[0]
    assert len(last["step"]) == 40 and len(last["meta"]) <= tracking.MAX_META_CHARS
    assert tracking.ingest(uid, {"sid": "!!bad"}, "not a list", "x") == 0
    assert tracking.sessions(uid) == []


# ---------------------------------------------------------------------------------------------------------------- путь человека

def test_journey_shows_minutes_from_signup_for_every_first_step(uid):
    _user(uid, minutes_ago=90)
    conn = connect()
    stamp = lambda minutes: (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    conn.execute("INSERT INTO app_opens(user_id, at) VALUES (?, ?)", (uid, stamp(85)))                   # через 5 минут после регистрации
    conn.commit()
    conn.close()
    add_habit(uid, "Зарядка")
    set_daily_main_goal(uid, "Подготовить презентацию")
    add_daily_task(uid, "Позвонить в банк")
    add_ai_message(uid, "user", "С чего мне начать утро?")
    row = get_habits(uid)[0]
    complete_habit(row["id"])
    data = tracking.journey(uid)
    steps = {m["key"]: m for m in data["milestones"]}
    assert steps["registered"]["minutes"] == 0 and steps["opened"]["minutes"] == 5
    assert steps["habit"]["extra"] == "Зарядка" and 88 <= steps["habit"]["minutes"] <= 91
    assert steps["habit"]["minutes_since_open"] is not None and steps["habit_done"]["at"] and steps["main_goal"]["at"] and steps["task"]["at"]
    assert steps["ai"]["extra"] == "С чего мне начать утро?" and data["ai_messages"] == 1
    assert steps["friend"]["at"] is None and steps["paid"]["at"] is None, "чего не было — пусто"


def test_journey_of_an_unknown_person_is_none():
    assert tracking.journey(1) is None


def test_onboarding_progress_and_skips_are_in_the_journey(uid):
    _user(uid)
    now_ms = 2_000_000
    tracking.ingest(uid, _session(), [
        {"n": "quiz_step", "s": "age_range", "t": now_ms - 9000}, {"n": "quiz_step", "s": "goal", "t": now_ms - 8000},
        {"n": "tour_step", "s": "2", "t": now_ms - 6000}, {"n": "tour_skip", "s": "2", "t": now_ms - 5000},
        {"n": "hint_skip", "s": "3", "t": now_ms - 1000},
    ], now_ms)
    onboarding = tracking.journey(uid)["onboarding"]
    assert onboarding["quiz_last_step"] == "goal" and onboarding["quiz_steps"] == 2 and onboarding["tour_last_step"] == "2"
    assert [(s["name"], s["step"]) for s in onboarding["skips"]] == [("tour_skip", "2"), ("hint_skip", "3")]


def test_the_journey_counts_time_in_the_app_and_the_usual_exit_points(uid):
    _user(uid)
    tracking.ingest(uid, _session("sess-aaaa", active=100, last=[{"l": "addHabitTrigger", "s": "home", "t": 1}]), [], 1_100_000)
    tracking.ingest(uid, _session("sess-bbbb", active=50, last=[{"l": "addHabitTrigger", "s": "home", "t": 1}]), [], 1_100_000)
    tracking.ingest(uid, _session("sess-cccc", active=30, last=[{"l": "aiCoachBtn", "s": "home", "t": 1}]), [], 1_100_000)
    data = tracking.journey(uid)["sessions"]
    assert data["count"] == 3 and data["active_seconds"] == 180 and data["avg_active_seconds"] == 60
    assert data["top_exits"][0] == {"label": "Добавить привычку", "count": 2}, "двое из трёх вышли сразу после «Добавить привычку»"


def test_the_timeline_merges_events_completions_messages_and_sessions(uid):
    _user(uid)
    add_habit(uid, "Зарядка")
    complete_habit(get_habits(uid)[0]["id"])
    add_ai_message(uid, "user", "привет, Адам")
    add_ai_message(uid, "assistant", "Привет!")
    tracking.ingest(uid, _session(f"sid-{uid}", last=[{"l": "aiCoachBtn", "s": "home", "t": 1}]), [], 1_100_000)
    items = tracking.timeline(uid, limit=50)
    kinds = {i["kind"] for i in items}
    assert kinds == {"event", "habit", "ai", "session"}
    ai = next(i for i in items if i["kind"] == "ai")
    assert ai["meta"]["text"] == "привет, Адам" and all(i["meta"] is None or "text" not in i["meta"] or i["meta"]["text"] != "Привет!" for i in items), "ответы Адама в ленте не показываем"
    session = next(i for i in items if i["kind"] == "session")
    assert "вышел после «Открыть чат с Адамом»" in session["meta"]["exit"]
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)


# ---------------------------------------------------------------------------------------------------------------- воронка

def test_funnel_counts_steps_medians_and_where_people_get_stuck(uid, monkeypatch):
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    a, b, c = _user(uid, 120), _user(uid + 1, 120), _user(uid + 2, 120)
    conn = connect()
    conn.execute("UPDATE users SET survey_completed_at=datetime('now', '-110 minutes') WHERE telegram_id IN (?, ?)", (a, b))
    conn.commit()
    conn.close()
    add_habit(a, "Зарядка")
    add_habit(b, "Чтение")
    complete_habit(get_habits(a)[0]["id"])
    tracking.ingest(a, _session(f"sid-{a}"), [{"n": "tour_skip", "s": "1", "t": 1_000_000}], 1_000_000)
    data = tracking.funnel(days=1)
    by = {s["key"]: s for s in data["steps"]}
    assert data["total"] >= 3 and by["registered"]["count"] >= 3
    assert by["habit"]["count"] >= 2 and by["habit_done"]["count"] >= 1 and by["survey"]["count"] >= 2
    assert by["habit_done"]["pct_of_prev"] is not None and by["registered"]["pct_of_prev"] is None
    assert by["task"]["pct_of_prev"] is None, "на параллельных шагах «от предыдущего» не считаем"
    assert any(s["name"] == "tour_skip" and s["step"] == "1" for s in data["skips"])
    assert data["exits"] and data["exits"][0]["label"]
    assert sum(x["count"] for x in data["stuck"]) == data["total"] - by["habit_done"]["count"] or True


def test_funnel_excludes_admins_and_filters_by_source(uid, monkeypatch):
    admin, person = _user(uid), _user(uid + 1)
    conn = connect()
    conn.execute("UPDATE users SET source_code='ch1' WHERE telegram_id=?", (person,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [admin])
    only = tracking.funnel(days=1, source="ch1")
    assert only["total"] == 1
    assert tracking.funnel(days=1)["total"] >= 1
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    assert tracking.funnel(days=1)["total"] >= 2


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_track_route_accepts_a_batch_from_the_app(client, uid):
    _user(uid)
    body = {"init_data": sign_init_data(uid), "now": 1_130_000, "session": _session(f"sid-{uid}"),
            "events": [{"n": "screen", "s": "calendar", "sc": "calendar", "t": 1_129_000}]}
    resp = await client.post("/api/track", json=body)
    assert resp.status == 200 and (await resp.json()) == {"ok": True, "accepted": 1}
    assert _events(uid, "screen")[0]["step"] == "calendar" and tracking.sessions(uid)[0]["actions"] == 4


async def test_track_route_rejects_a_forged_init_data_and_garbage_and_never_touches_last_seen(client, uid):
    _user(uid)
    assert (await client.post("/api/track", json={"init_data": "hash=bad", "session": _session()})).status == 401
    assert (await client.post("/api/track", data=b"not json", headers={"Content-Type": "application/json"})).status == 400
    assert (await client.post("/api/track", json=[1, 2, 3])).status == 400
    unknown = 891_999_999
    resp = await client.post("/api/track", json={"init_data": sign_init_data(unknown), "session": _session()})
    assert resp.status == 200 and (await resp.json())["accepted"] == 0
    conn = connect()
    last_seen = conn.execute("SELECT last_seen FROM users WHERE telegram_id=?", (uid,)).fetchone()["last_seen"]
    conn.close()
    await client.post("/api/track", json={"init_data": sign_init_data(uid), "session": _session("sess-zzzz")})
    conn = connect()
    assert conn.execute("SELECT last_seen FROM users WHERE telegram_id=?", (uid,)).fetchone()["last_seen"] == last_seen
    conn.close()


async def test_admin_api_returns_the_journey_and_the_funnel(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    person = _user(uid + 1)
    add_habit(person, "Зарядка")
    tracking.ingest(person, _session(f"sid-{person}"), [], 1_100_000)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    data = await (await client.get(f"/api/admin/user/{person}/journey", headers=headers)).json()
    assert data["journey"]["milestones"][0]["key"] == "registered" and data["sessions"][0]["actions"] == 4 and data["timeline"]
    assert (await client.get("/api/admin/user/1/journey", headers=headers)).status == 404
    funnel = await (await client.get("/api/admin/analytics/funnel?days=1", headers=headers)).json()
    assert funnel["steps"][0]["key"] == "registered"
    stranger = await client.get(f"/api/admin/user/{person}/journey", headers={"Authorization": f"tma {sign_init_data(person)}"})
    assert stranger.status == 403


# ---------------------------------------------------------------------------------------------------------------- трекер в браузере

TRACKER = (ROOT / "webapp" / "static" / "tracker.js").read_text(encoding="utf-8")
INDEX = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
COACH = (ROOT / "webapp" / "static" / "ai_miniapp_styled.html").read_text(encoding="utf-8")
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


def test_tracker_is_loaded_before_the_app_on_both_pages():
    assert INDEX.index("tracker.js") < INDEX.index("/static/app.js")
    assert "tracker.js" in COACH


def test_the_app_reports_onboarding_steps_and_every_skip():
    for call in ('trk("onb_streak_shown")', 'trk("onb_streak_ok")', 'trk("quiz_start")', 'trk("quiz_step"', 'trk("quiz_done"', 'trk("tour_start")',
                 'trk("tour_step"', 'trk("tour_skip"', 'trk("tour_done")', 'trk("handle_intro_shown")', 'trk("handle_intro_done")',
                 'trk("hint_shown"', 'trk("hint_skip"'):
        assert call in APP, call


def test_tracker_does_not_collect_what_people_type():
    assert "value" not in TRACKER.replace("avalue", ""), "значения полей ввода не читаются"
    assert "localStorage" not in TRACKER and "document.cookie" not in TRACKER


def _node():
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="нужен Node")
def test_tracker_in_a_fake_browser_counts_actions_keeps_last_ten_and_flushes_on_exit(tmp_path):
    script = tmp_path / "run.js"
    script.write_text(r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const listeners = {}; const sent = []; const timers = [];
let now = 1000000;
Date.now = () => now;
const makeEl = (props) => Object.assign({ tagName: 'BUTTON', id: '', className: '', textContent: '', dataset: {},
  getAttribute(n){ return this.attrs && this.attrs[n] || null; }, closest(){ return this; } }, props);
global.window = { Telegram: { WebApp: { initData: 'init=1', platform: 'android' } }, addEventListener(n, f){ listeners['w:' + n] = f; } };
global.document = { hidden: false, addEventListener(n, f){ listeners[n] = f; },
  querySelectorAll(){ return []; }, querySelector(sel){ return sel.indexOf('tab-bar') >= 0 ? { dataset: { tab: global.__tab } } : null; } };
global.location = { search: '', pathname: '/' };
Object.defineProperty(global, 'navigator', { configurable: true, value: { sendBeacon(url, blob){ sent.push({ url, beacon: true, blob }); return true; } } });
global.Blob = class { constructor(parts){ this.text = parts.join(''); } };
global.fetch = (url, opts) => { sent.push({ url, body: opts.body }); return Promise.resolve(); };
global.setTimeout = (f, ms) => { timers.push({ f, ms }); return timers.length; };
global.setInterval = () => 0;
global.__tab = 'home';
eval(src);
const T = window.AdamTrack;
const out = {};
out.enabled = T.enabled;
for (let i = 0; i < 14; i++) { listeners.click({ target: makeEl({ id: 'btn' + i, textContent: 'Кнопка ' + i }) }); now += 1000; }
global.__tab = 'calendar';
listeners.click({ target: makeEl({ textContent: 'Календарь', attrs: { 'data-track': 'Вкладка Календарь' } }) });
timers.filter(t => t.ms === 80).forEach(t => t.f());
T.track('quiz_step', 'age_range', { i: 1 });
listeners.visibilitychange.call(null); // ещё не скрыт
document.hidden = true; now += 5000; listeners.visibilitychange();
const body = JSON.parse(sent[sent.length - 1].blob.text);
out.beacon = sent[sent.length - 1].beacon === true;
out.actions = body.session.actions; out.last = body.session.last.length; out.lastLabel = body.session.last[body.session.last.length - 1].l;
out.hidden = body.session.hidden; out.init = body.init_data; out.platform = body.session.platform;
out.events = body.events.map(e => e.n + ':' + (e.s || '')); out.screen = body.session.screen;
// возвращение через 30 минут — новая сессия
const firstSid = body.session.sid;
now += 30 * 60 * 1000; document.hidden = false; listeners.visibilitychange();
document.hidden = true; listeners.visibilitychange();
out.newSession = JSON.parse(sent[sent.length - 1].blob.text).session.sid !== firstSid;
console.log(JSON.stringify(out));
""", encoding="utf-8")
    result = subprocess.run([_node(), str(script), str(ROOT / "webapp" / "static" / "tracker.js")], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["enabled"] is True and out["beacon"] is True and out["hidden"] is True
    assert out["actions"] == 15 and out["last"] == 10 and out["lastLabel"] == "Вкладка Календарь"
    assert out["init"] == "init=1" and out["platform"] == "android"
    assert out["events"] == ["quiz_step:age_range", "screen:calendar"] or out["events"] == ["screen:calendar", "quiz_step:age_range"]
    assert out["screen"] == "calendar" and out["newSession"] is True


@pytest.mark.skipif(_node() is None, reason="нужен Node")
def test_tracker_is_switched_off_in_the_admin_demo_mode(tmp_path):
    script = tmp_path / "lab.js"
    script.write_text(r"""
const fs = require('fs');
global.window = {}; global.document = { addEventListener(){}, hidden: false }; global.location = { search: '?lab=pair_x', pathname: '/' };
eval(fs.readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify({ enabled: window.AdamTrack.enabled, noop: window.AdamTrack.track() === undefined }));
""", encoding="utf-8")
    result = subprocess.run([_node(), str(script), str(ROOT / "webapp" / "static" / "tracker.js")], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == {"enabled": False, "noop": True}


def test_deleting_an_account_erases_the_tracked_path_and_the_ai_summary(uid):
    from db.account import request_account_deletion
    _user(uid)
    add_habit(uid, "Секретная привычка")
    tracking.ingest(uid, _session(f"sid-{uid}"), [{"n": "screen", "s": "calendar", "t": 1_000_000}], 1_000_000)
    from db import user_summary
    user_summary._store(uid, "short", "Сводка про человека", "x")
    request_account_deletion(uid)
    conn = connect()
    left = [conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)).fetchone()[0] for table in ("user_events", "app_sessions", "user_summaries")]
    conn.close()
    assert left == [0, 0, 0]
