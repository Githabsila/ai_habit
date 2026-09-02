"""
Дневной лимит AI-ответов: расходуется, не превышается, сбрасывается на
новый день (регрессия на баг "вчерашний used/bonus переносился на
сегодня" — см. db/shop.py::_ensure_ai_quota_day).
"""
from db import add_user
from db.shop import consume_ai_answer, get_ai_quota

from tests.conftest import sign_init_data


def test_consume_respects_daily_limit(uid):
    add_user(uid, "tester", "Test")
    quota = get_ai_quota(uid, is_pro=False)
    limit = quota["limit"]

    for _ in range(limit):
        assert consume_ai_answer(uid, is_pro=False, cost=1) is True

    assert consume_ai_answer(uid, is_pro=False, cost=1) is False
    final = get_ai_quota(uid, is_pro=False)
    assert final["remaining"] == 0
    assert final["used"] == limit


def test_pro_user_gets_higher_daily_limit(uid):
    add_user(uid, "tester", "Test")
    free_quota = get_ai_quota(uid, is_pro=False)
    pro_quota = get_ai_quota(uid, is_pro=True)

    assert pro_quota["limit"] >= free_quota["limit"]


# =====================================
# POST /api/ai/chat — кэшированный ответ ТОЖЕ расходует лимит
# =====================================
# Раньше consume_ai_answer() вызывался только когда ответ НЕ пришёл из кэша
# (db/ai.py::cache_get, TTL 12 часов, ключ — нормализованный текст без учёта
# регистра). Пользователь видел полноценный ответ ADAM, но счётчик "15/15"
# не двигался — с его стороны выглядело как "лимит не расходуется вообще".

def _mock_ai_chat_deps(monkeypatch):
    import webapp.routes_ai_miniapp as route_mod

    monkeypatch.setattr(route_mod, "try_handle_habit_intent", lambda *a, **k: None)
    monkeypatch.setattr(route_mod, "_looks_like_habit_action", lambda *a, **k: False)
    monkeypatch.setattr(route_mod, "_is_throttled", lambda *a, **k: None)

    async def fake_solve(**kwargs):
        return {"answer": "Начни с 10 минут в день.", "is_crisis": False, "suggested_habit": None, "complexity": "просто"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", fake_solve)


async def test_cached_answer_still_consumes_quota(client, uid, monkeypatch):
    add_user(uid, "tester", "Test")
    _mock_ai_chat_deps(monkeypatch)
    init_data = sign_init_data(uid)

    before = get_ai_quota(uid, is_pro=False)

    r1 = await client.post("/api/ai/chat", json={"init_data": init_data, "message": "Как начать бегать?"})
    assert r1.status == 200
    body1 = await r1.json()
    assert body1["quota"]["used"] == before["used"] + 1

    # Второй, идентичный (без учёта регистра) вопрос — должен попасть в кэш
    # (см. webapp/services/ai_utils.py::_cache_key), но всё равно списать лимит.
    r2 = await client.post("/api/ai/chat", json={"init_data": init_data, "message": "как начать бегать?"})
    assert r2.status == 200
    body2 = await r2.json()
    assert body2["quota"]["used"] == before["used"] + 2
