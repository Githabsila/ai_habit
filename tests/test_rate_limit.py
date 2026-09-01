"""
Rate limiting API Mini App'а (webapp/webapp_server.py::_check_rate_limit).

Раньше ни один API-роут не имел вообще никакой защиты от частоты запросов —
/api/feedback можно было спамить без ограничений и заваливать админов
сообщениями. Лимит — общий для всех авторизованных роутов через
_authenticate(), in-memory sliding-window на telegram_id.
"""
from db import add_user

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_requests_within_limit_all_succeed(client, uid, monkeypatch):
    import webapp.webapp_server as webapp_server

    monkeypatch.setattr(webapp_server, "RATE_LIMIT_MAX_REQUESTS", 3)

    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    for _ in range(3):
        r = await client.get("/api/streak/status", headers=headers)
        assert r.status == 200


async def test_request_over_limit_gets_429(client, uid, monkeypatch):
    import webapp.webapp_server as webapp_server

    monkeypatch.setattr(webapp_server, "RATE_LIMIT_MAX_REQUESTS", 3)

    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    for _ in range(3):
        r = await client.get("/api/streak/status", headers=headers)
        assert r.status == 200

    r = await client.get("/api/streak/status", headers=headers)
    assert r.status == 429
    body = await r.json()
    assert body["error"] == "rate_limited"


async def test_rate_limit_is_per_user_not_global(client, uid, monkeypatch):
    import webapp.webapp_server as webapp_server

    monkeypatch.setattr(webapp_server, "RATE_LIMIT_MAX_REQUESTS", 2)

    other_uid = uid * 10
    add_user(uid, "u", "Test")
    add_user(other_uid, "u2", "Test2")
    headers = await _headers(uid)
    other_headers = await _headers(other_uid)

    for _ in range(2):
        r = await client.get("/api/streak/status", headers=headers)
        assert r.status == 200
    # Первый пользователь исчерпал лимит...
    r = await client.get("/api/streak/status", headers=headers)
    assert r.status == 429

    # ...но у второго свой независимый резерв — его запросы не блокируются.
    r = await client.get("/api/streak/status", headers=other_headers)
    assert r.status == 200
