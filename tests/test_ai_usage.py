"""
Расход ИИ по людям (db/ai_usage.py): каждый вызов модели пишется на человека и функцию, админ видит кто сколько тратит и может
временно отключить ИИ для человека или задать ему дневной лимит токенов — действует на чат, утро, «Адам пишет первым» и всё остальное.
"""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import multi_agent
from db import add_user
from db import ai_usage
from db.analytics import log_ai_tokens
from db.core import connect
from tests.conftest import sign_init_data

_uid_counter = itertools.count(880_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid_):
    add_user(uid_, f"u{uid_}", "Аня")
    conn = connect()
    conn.execute("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid_,))
    conn.commit()
    conn.close()
    return uid_


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------------------------------------------- запись

def test_a_call_is_recorded_on_the_person_and_the_feature_in_scope(uid):
    _user(uid)
    with ai_usage.scope(uid, "chat"):
        log_ai_tokens(1200, "groq", "oss-120b", 900, 300)
        with ai_usage.scope(uid, "intent"):                       # вложенный scope перекрывает внешний
            log_ai_tokens(80, "groq", "oss-120b", 70, 10)
    log_ai_tokens(500, "openai", "gpt")                            # вне scope — «без владельца»
    usage = ai_usage.user_usage(uid)
    assert usage["today"] == 1280 and usage["today_calls"] == 2 and usage["total"] == 1280
    assert {f["feature"]: f["tokens"] for f in usage["by_feature"]} == {"chat": 1200, "intent": 80}
    conn = connect()
    row = conn.execute("SELECT * FROM ai_token_log WHERE user_id=? AND feature='chat'", (uid,)).fetchone()
    conn.close()
    assert (row["model"], row["prompt_tokens"], row["completion_tokens"], row["provider"]) == ("oss-120b", 900, 300, "groq")


def test_scope_is_restored_after_the_block_and_survives_await(uid):
    async def work():
        with ai_usage.scope(uid, "morning"):
            await asyncio.sleep(0)
            return dict(ai_usage.current())

    assert asyncio.run(work()) == {"user_id": uid, "feature": "morning"}
    assert ai_usage.current() == {}


def test_overview_ranks_people_by_tokens_and_counts_unowned_calls(uid):
    a, b = _user(uid), _user(uid + 1)
    with ai_usage.scope(a, "chat"):
        log_ai_tokens(5000, "groq")
    with ai_usage.scope(b, "checkin"):
        log_ai_tokens(1000, "groq")
    log_ai_tokens(300, "groq")
    data = ai_usage.overview(days=7, limit=500)
    top = [x["telegram_id"] for x in data["top"] if x["telegram_id"] in (a, b)]
    assert top == [a, b] and data["unowned"] >= 300 and data["tokens"] >= 6300
    assert {f["feature"] for f in data["by_feature"]} >= {"chat", "checkin"}
    assert all(f["label"] for f in data["by_feature"])


# ---------------------------------------------------------------------------------------------------------------- ограничения

def test_block_and_unblock(uid):
    _user(uid)
    assert ai_usage.check_allowed(uid) == {"ok": True}
    result = ai_usage.block_ai(uid, 24)
    assert result["ok"] and ai_usage.check_allowed(uid)["ok"] is False
    assert ai_usage.check_allowed(uid)["reason"] == "blocked" and ai_usage.user_usage(uid)["blocked_until"]
    ai_usage.unblock_ai(uid)
    assert ai_usage.check_allowed(uid) == {"ok": True}
    assert ai_usage.block_ai(uid, 5) == {"error": "invalid_hours"} and ai_usage.block_ai(uid + 999999, 1) == {"error": "user_not_found"}


def test_an_expired_block_stops_working(uid):
    _user(uid)
    _sql("UPDATE users SET ai_blocked_until=? WHERE telegram_id=?", ((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)).isoformat(), uid))
    assert ai_usage.check_allowed(uid)["ok"] is True and ai_usage.user_usage(uid)["blocked_until"] is None


def test_personal_day_limit_closes_the_ai_when_reached(uid):
    _user(uid)
    assert ai_usage.set_day_limit(uid, 20_000)["limit"] == 20_000
    with ai_usage.scope(uid, "chat"):
        log_ai_tokens(19_000)
    assert ai_usage.check_allowed(uid)["ok"] is True
    with ai_usage.scope(uid, "chat"):
        log_ai_tokens(1_500)
    verdict = ai_usage.check_allowed(uid)
    assert verdict["ok"] is False and verdict["reason"] == "day_limit"
    assert ai_usage.set_day_limit(uid, None)["limit"] is None and ai_usage.check_allowed(uid)["ok"] is True
    assert ai_usage.set_day_limit(uid, 12345) == {"error": "invalid_limit"}


def test_the_global_default_limit_applies_when_no_personal_limit(uid, monkeypatch):
    import config
    _user(uid)
    monkeypatch.setattr(config, "AI_USER_DAY_TOKENS", 1000)
    with ai_usage.scope(uid, "chat"):
        log_ai_tokens(1200)
    assert ai_usage.check_allowed(uid)["reason"] == "day_limit"


def test_messages_for_the_person():
    assert "отдыхает" in ai_usage.restriction_message("blocked") and "Завтра" in ai_usage.restriction_message("day_limit")


# ---------------------------------------------------------------------------------------------------------------- единая точка вызовов

def test_the_model_is_never_called_for_a_restricted_person(uid, monkeypatch):
    _user(uid)
    ai_usage.block_ai(uid, 1)
    calls = []
    monkeypatch.setattr(multi_agent, "GROQ_API_KEY", "x")
    monkeypatch.setattr(multi_agent, "_get_groq_client", lambda: calls.append("groq") or None)

    async def run():
        with ai_usage.scope(uid, "morning"):
            return await multi_agent._ask("sys", "user")

    with pytest.raises(ai_usage.AIRestricted) as caught:
        asyncio.run(run())
    assert caught.value.reason == "blocked" and calls == [], "до провайдера дело не дошло"


def test_usage_is_extracted_from_both_provider_formats(uid):
    _user(uid)
    groq = SimpleNamespace(usage=SimpleNamespace(total_tokens=700, prompt_tokens=500, completion_tokens=200), model="oss")
    openai = SimpleNamespace(usage=SimpleNamespace(total_tokens=None, input_tokens=300, output_tokens=100), model="gpt")
    with ai_usage.scope(uid, "tip"):
        multi_agent._log_usage(groq, "groq", "oss-120b")
        multi_agent._log_usage(openai, "openai", "gpt-5")
    conn = connect()
    rows = conn.execute("SELECT provider, model, tokens, prompt_tokens, completion_tokens FROM ai_token_log WHERE user_id=? ORDER BY id", (uid,)).fetchall()
    conn.close()
    assert [tuple(r) for r in rows] == [("groq", "oss-120b", 700, 500, 200), ("openai", "gpt-5", 400, 300, 100)]


# ---------------------------------------------------------------------------------------------------------------- чат и «Адам пишет первым»

def _mock_chat(monkeypatch):
    import webapp.routes_ai_miniapp as route_mod
    from tests.test_ai_quota import _mock_ai_chat_deps
    _mock_ai_chat_deps(monkeypatch)
    seen = []

    async def fake_solve(**kwargs):
        seen.append(ai_usage.current())
        return {"answer": "Хорошо.", "is_crisis": False, "suggested_habit": None, "complexity": "сложно"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", fake_solve)
    return seen


async def test_chat_runs_inside_the_persons_scope(client, uid, monkeypatch):
    _user(uid)
    seen = _mock_chat(monkeypatch)
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "Как лучше засыпать?"})
    assert resp.status == 200 and seen == [{"user_id": uid, "feature": "chat"}]


async def test_a_blocked_person_gets_a_friendly_answer_and_no_quota_is_spent(client, uid, monkeypatch):
    from db.shop import get_ai_quota
    _user(uid)
    seen = _mock_chat(monkeypatch)
    ai_usage.block_ai(uid, 24)
    before = get_ai_quota(uid, is_pro=False)["used"]
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "Как лучше засыпать?"})
    body = await resp.json()
    assert resp.status == 429 and body["error"] == "ai_limited" and body["reason"] == "blocked" and "отдыхает" in body["message"]
    assert seen == [] and get_ai_quota(uid, is_pro=False)["used"] == before


async def test_habit_commands_still_work_when_the_ai_is_blocked(client, uid, monkeypatch):
    _user(uid)
    _mock_chat(monkeypatch)
    import webapp.routes_ai_miniapp as route_mod
    monkeypatch.setattr(route_mod, "try_handle_habit_intent", lambda user_id, text: "✅ Привычка «Бег» добавлена!")
    ai_usage.block_ai(uid, 1)
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "добавь привычку бег"})
    assert resp.status == 200 and "добавлена" in (await resp.json())["answer"], "команды без модели не зависят от ограничения"


def test_adam_does_not_write_first_to_a_person_with_the_ai_blocked(uid, monkeypatch):
    import adam_proactive
    from db import adam_checkin as ac
    _user(uid)
    monkeypatch.setattr(ac, "account_old_enough", lambda *a, **k: True)
    monkeypatch.setattr(ac, "is_dormant", lambda *a, **k: False)
    monkeypatch.setattr(adam_proactive, "reminder_category_enabled", lambda settings, category: True)
    monkeypatch.setattr(adam_proactive, "in_quiet_hours", lambda settings, now: False)
    ai_usage.block_ai(uid, 1)
    reason = adam_proactive.skip_reason(uid, datetime.now().astimezone(), {})
    assert reason == "ИИ для человека ограничен админом"
    ai_usage.unblock_ai(uid)
    assert adam_proactive.skip_reason(uid, datetime.now().astimezone(), {}) != "ИИ для человека ограничен админом"


# ---------------------------------------------------------------------------------------------------------------- админ-API

async def test_admin_api_shows_usage_and_limits_the_ai(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    admin = uid
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [admin])
    person = _user(uid + 1)
    with ai_usage.scope(person, "chat"):
        log_ai_tokens(4200)
    headers = {"Authorization": f"tma {sign_init_data(admin)}"}

    overview = await (await client.get("/api/admin/ai-usage?days=7", headers=headers)).json()
    assert any(x["telegram_id"] == person and x["tokens"] == 4200 for x in overview["top"])
    mine = await (await client.get(f"/api/admin/user/{person}/ai-usage", headers=headers)).json()
    assert mine["today"] == 4200 and mine["by_feature"][0]["label"] == "Чат с Адамом"

    blocked = await client.post(f"/api/admin/user/{person}/ai-limit", json={"action": "block", "hours": 24}, headers=headers)
    assert blocked.status == 200 and (await blocked.json())["usage"]["blocked_until"]
    limit = await client.post(f"/api/admin/user/{person}/ai-limit", json={"action": "day_limit", "tokens": 50000}, headers=headers)
    assert (await limit.json())["usage"]["day_limit"] == 50000
    assert (await client.post(f"/api/admin/user/{person}/ai-limit", json={"action": "block", "hours": 3}, headers=headers)).status == 400
    assert (await client.post(f"/api/admin/user/{person}/ai-limit", json={"action": "unblock"}, headers=headers)).status == 200
    card = await (await client.get(f"/api/admin/user/{person}", headers=headers)).json()
    assert card["ai_usage"]["blocked_until"] is None and card["ai_usage"]["day_limit"] == 50000

    stranger = await client.post(f"/api/admin/user/{person}/ai-limit", json={"action": "block", "hours": 1}, headers={"Authorization": f"tma {sign_init_data(person)}"})
    assert stranger.status == 403
