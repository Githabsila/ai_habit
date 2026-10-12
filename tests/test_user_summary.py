"""
ИИ-сводки для админа (db/user_summary.py) и лаборатория «Ответы Адама»: досье человека без чужих данных и ответов Адама, сводка по запросу
(коротко/подробно), признак «данные изменились», защита от двойного нажатия, общий разбор и проверка ответов Адама на живой модели.
"""
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db import add_ai_message, add_daily_task, add_habit, add_user, complete_habit, get_habits, set_daily_main_goal
from db import tracking
from db import user_summary as us
from db.core import connect
from tests.conftest import sign_init_data

_uid_counter = itertools.count(897_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid_, minutes_ago=300):
    add_user(uid_, f"u{uid_}", "Аня")
    stamp = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("UPDATE users SET created_at=?, access_status='approved', archetype='Дисциплинированный' WHERE telegram_id=?", (stamp, uid_))
    conn.commit()
    conn.close()
    return uid_


def _fake_model(monkeypatch, text="Кто это: Аня.\nРиск ухода: низкий."):
    calls = []

    async def fake(system, user, max_tokens):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return text

    monkeypatch.setattr(us, "_ask_model", fake)
    return calls


def _rich_user(uid_):
    _user(uid_)
    add_habit(uid_, "Зарядка")
    complete_habit(get_habits(uid_)[0]["id"])
    set_daily_main_goal(uid_, "Подготовить презентацию")
    add_daily_task(uid_, "Позвонить в банк")
    add_ai_message(uid_, "user", "С чего мне начать утро?")
    add_ai_message(uid_, "assistant", "СЕКРЕТНЫЙ ОТВЕТ АДАМА")
    tracking.ingest(uid_, {"sid": f"sid-{uid_}", "started": 1_000_000, "active": 200, "actions": 5, "screen": "home", "hidden": True,
                           "last": [{"l": "addHabitTrigger", "s": "home", "t": 1}]}, [{"n": "tour_skip", "s": "2", "t": 1_099_000}], 1_100_000)
    return uid_


# ---------------------------------------------------------------------------------------------------------------- досье

def test_the_dossier_has_the_path_habits_tasks_and_what_the_person_wrote(uid):
    _rich_user(uid)
    text = us.build_dossier(uid)
    for expected in ("Имя: Аня", "Архетип: Дисциплинированный", "✓ Первая привычка", "✗ Первый друг", "пропуски: tour_skip(2)",
                     "Чаще всего выходит после: «Добавить привычку»", "• Зарядка", "«Подготовить презентацию»", "«Позвонить в банк»", "— С чего мне начать утро?"):
        assert expected in text, expected
    assert "СЕКРЕТНЫЙ ОТВЕТ АДАМА" not in text, "ответы Адама в досье не попадают — только слова самого человека"


def test_the_dossier_of_an_unknown_person_is_none_and_long_ones_are_cut(uid):
    assert us.build_dossier(1) is None
    _user(uid)
    for i in range(30):
        add_ai_message(uid, "user", "очень длинное сообщение " * 30)
    assert len(us.build_dossier(uid, max_chars=2000)) <= 2000


def test_the_dossier_of_a_newcomer_says_so_plainly(uid):
    _user(uid)
    text = us.build_dossier(uid)
    assert "Привычек нет" in text and "Адаму не писал" in text and "✗ Первая привычка" in text


# ---------------------------------------------------------------------------------------------------------------- сводка по человеку

async def test_a_short_and_an_extended_summary_are_stored_and_returned(uid, monkeypatch):
    _rich_user(uid)
    calls = _fake_model(monkeypatch)
    short = await us.generate(uid, "short")
    assert short["ok"] and short["summary"]["text"].startswith("Кто это") and short["summary"]["stale"] is False
    assert calls[0]["max_tokens"] == 350 and "КОРОТКАЯ версия" in calls[0]["system"] and "Подготовить презентацию" in calls[0]["user"]
    extended = await us.generate(uid, "extended")
    assert extended["ok"] and calls[1]["max_tokens"] == 800 and "КОРОТКАЯ версия" not in calls[1]["system"]
    both = us.get_summaries(uid)
    assert both["short"] and both["extended"] and len(us.history(uid)) == 2


async def test_a_summary_becomes_stale_when_the_person_does_something_new(uid, monkeypatch):
    _rich_user(uid)
    _fake_model(monkeypatch)
    await us.generate(uid, "short")
    assert us.get_summaries(uid)["short"]["stale"] is False
    add_ai_message(uid, "user", "а как отметить привычку?")
    assert us.get_summaries(uid)["short"]["stale"] is True, "после новой сводки данные изменились — админ видит, что пора обновить"


async def test_double_clicks_do_not_spend_tokens_twice(uid, monkeypatch):
    _rich_user(uid)
    calls = _fake_model(monkeypatch)
    await us.generate(uid, "short")
    again = await us.generate(uid, "short")
    assert again["error"] == "too_soon" and again["summary"] and len(calls) == 1
    other_kind = await us.generate(uid, "extended")
    assert other_kind["ok"], "другая версия — отдельный запрос"


async def test_errors_are_returned_not_raised(uid, monkeypatch):
    _user(uid)
    assert await us.generate(uid, "weird") == {"error": "invalid_kind"}
    assert await us.generate(1, "short") == {"error": "user_not_found"}

    async def broken(system, user, max_tokens):
        raise RuntimeError("provider down")

    monkeypatch.setattr(us, "_ask_model", broken)
    failed = await us.generate(uid, "short")
    assert failed["error"] == "model_failed" and "provider down" in failed["message"] and us.latest(uid, "short") is None
    _fake_model(monkeypatch, text="   ")
    assert (await us.generate(uid, "short"))["error"] == "model_failed", "пустой ответ не сохраняем"


async def test_summary_tokens_are_not_charged_to_the_person(uid, monkeypatch):
    from db import ai_usage
    _user(uid)
    seen = []

    async def fake_ask(system, user, **kwargs):
        seen.append(dict(ai_usage.current()))
        return "Кто это: Аня."

    import multi_agent
    monkeypatch.setattr(multi_agent, "_ask", fake_ask)
    assert (await us.generate(uid, "short"))["ok"]
    assert seen == [{"user_id": None, "feature": "summary"}]


# ---------------------------------------------------------------------------------------------------------------- общий разбор

def test_the_insights_dossier_collects_funnel_questions_ratings_errors_and_channels(uid, monkeypatch):
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    _rich_user(uid)
    add_ai_message(uid, "user", "я так устал, нет сил")
    conn = connect()
    conn.execute("INSERT INTO client_errors(user_id, message) VALUES (?, ?)", (uid, "TypeError: x is undefined"))
    conn.commit()
    conn.close()
    text = us.build_insights_dossier(days=3)
    for expected in ("ВОРОНКА", "Зарегистрировались:", "Что пропускают: tour_skip шаг 2", "О чём пишут", "усталость", "Повторяющиеся ошибки на устройствах: «TypeError: x is undefined»"):
        assert expected in text, expected
    assert "СЕКРЕТНЫЙ" not in text


async def test_insights_are_stored_with_the_period(uid, monkeypatch):
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    _user(uid)
    _fake_model(monkeypatch, text="Что работает: ...")
    result = await us.generate_insights(days=7)
    assert result["ok"] and result["insights"]["stamp"] == "7d" and us.latest(0, "insights")["text"].startswith("Что работает")


# ---------------------------------------------------------------------------------------------------------------- админ-API

async def test_admin_api_summary_flow(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    person = _rich_user(uid + 1)
    _fake_model(monkeypatch)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    empty = await (await client.get(f"/api/admin/user/{person}/summary", headers=headers)).json()
    assert empty["short"] is None and empty["extended"] is None
    made = await client.post(f"/api/admin/user/{person}/summary", json={"kind": "short"}, headers=headers)
    assert made.status == 200 and (await made.json())["summary"]["text"].startswith("Кто это")
    again = await client.post(f"/api/admin/user/{person}/summary", json={"kind": "short"}, headers=headers)
    assert again.status == 429
    assert (await client.post(f"/api/admin/user/{person}/summary", json={"kind": "boom"}, headers=headers)).status == 400
    assert (await client.post("/api/admin/user/1/summary", json={"kind": "short"}, headers=headers)).status == 404
    assert (await client.get("/api/admin/user/1/summary", headers=headers)).status == 404
    got = await (await client.get(f"/api/admin/user/{person}/summary", headers=headers)).json()
    assert got["short"]["stale"] is False and got["history"]
    stranger = await client.post(f"/api/admin/user/{person}/summary", json={"kind": "short"}, headers={"Authorization": f"tma {sign_init_data(person)}"})
    assert stranger.status == 403


async def test_admin_api_insights_flow(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    monkeypatch.setattr(tracking, "_admin_ids", lambda: [])
    _user(uid + 1)
    _fake_model(monkeypatch, text="Что мешает: ...")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    made = await client.post("/api/admin/analytics/insights", json={"days": 7}, headers=headers)
    assert made.status == 200 and (await made.json())["insights"]["stamp"] == "7d"
    got = await (await client.get("/api/admin/analytics/insights", headers=headers)).json()
    assert got["insights"]["text"].startswith("Что мешает")


# ---------------------------------------------------------------------------------------------------------------- лаборатория «Ответы Адама»

async def test_lab_runs_the_real_pipeline_without_touching_the_chat_history_or_the_quota(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    from db import get_ai_history
    from db.shop import get_ai_quota
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    _user(uid)
    add_habit(uid, "Зарядка")
    seen = {}

    async def fake_solve(**kwargs):
        seen.update(kwargs)
        return {"answer": "Отдохни пять минут и сделай зарядку.", "is_crisis": False, "suggested_habit": None, "complexity": "сложно"}

    import multi_agent
    monkeypatch.setattr(multi_agent, "solve_task_multiagent", fake_solve)
    before = (len(get_ai_history(uid, limit=50)), get_ai_quota(uid, is_pro=False)["used"])
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    resp = await client.post("/api/admin/lab/adam-chat", json={"message": "я так устал, нет сил"}, headers=headers)
    body = await resp.json()
    assert resp.status == 200 and body["answer"].startswith("Отдохни") and body["scenarios"] == ["tired"] and body["mood"] == "устал"
    assert "Снимок ситуации" in body["situation"] and "Человек устал" in body["scenario_note"]
    assert "Снимок ситуации" in seen["user_context"] and seen["mood"] == "устал" and seen["history"] == ""
    assert (len(get_ai_history(uid, limit=50)), get_ai_quota(uid, is_pro=False)["used"]) == before


async def test_lab_validates_input_and_works_for_a_chosen_person(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    import multi_agent
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    _user(uid)
    person = _user(uid + 1)
    add_habit(person, "Чтение")
    seen = {}

    async def fake_solve(**kwargs):
        seen.update(kwargs)
        return {"answer": "Ок.", "is_crisis": False, "suggested_habit": "Читать 10 минут", "complexity": "сложно"}

    monkeypatch.setattr(multi_agent, "solve_task_multiagent", fake_solve)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    assert (await client.post("/api/admin/lab/adam-chat", json={"message": "  "}, headers=headers)).status == 400
    assert (await client.post("/api/admin/lab/adam-chat", json={"message": "привет", "user_id": "x"}, headers=headers)).status == 400
    assert (await client.post("/api/admin/lab/adam-chat", json={"message": "привет", "user_id": 12345}, headers=headers)).status == 404
    ok = await client.post("/api/admin/lab/adam-chat", json={"message": "что делать сейчас?", "user_id": person}, headers=headers)
    body = await ok.json()
    assert ok.status == 200 and body["user_id"] == person and body["suggested_habit"] == "Читать 10 минут" and "Чтение" in seen["user_context"]

    async def broken(**kwargs):
        raise RuntimeError("no key")

    monkeypatch.setattr(multi_agent, "solve_task_multiagent", broken)
    failed = await client.post("/api/admin/lab/adam-chat", json={"message": "привет"}, headers=headers)
    assert failed.status == 502 and (await failed.json())["error"] == "model_failed"
    assert (await client.post("/api/admin/lab/adam-chat", json={"message": "привет"}, headers={"Authorization": f"tma {sign_init_data(person)}"})).status == 403


async def test_lab_presets_cover_the_chat_buttons_and_the_scenarios(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    from webapp.services import chat_brain
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    presets = (await (await client.get("/api/admin/lab/adam-chat", headers=headers)).json())["presets"]
    messages = [p["message"] for p in presets]
    for button in ("План на день", "Итоги дня", "Главный фокус", "Разбор ситуации", "Помощь с задачей", "Новая идея"):
        assert button in messages, button
    detected = {key for message in messages for key in chat_brain.detect_scenarios(message)}
    assert {"tired", "lazy", "slip", "win", "quit_habit", "overload", "pair", "app_help", "thanks", "smalltalk", "plan", "review", "focus"} <= detected
    assert set(chat_brain.SCENARIO_LABELS) == set(chat_brain.GUIDE), "у каждого сценария есть название для отчётов"
