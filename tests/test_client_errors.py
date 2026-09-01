"""
Улучшение #70: логирование клиентских JS-ошибок (window.onerror /
unhandledrejection на фронте -> POST /api/client-error -> db.client_errors).
"""
from db import add_user, log_client_error, get_recent_client_errors

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# db.client_errors
# =====================================

def test_log_client_error_persists_and_truncates(uid):
    log_client_error(uid, "x" * 1000, stack="y" * 5000, url="z" * 400, user_agent="ua" * 200)
    rows = get_recent_client_errors(limit=10)
    row = next(r for r in rows if r["user_id"] == uid)
    assert len(row["message"]) == 500
    assert len(row["stack"]) == 4000
    assert len(row["url"]) == 300


def test_get_recent_client_errors_orders_newest_first(uid):
    log_client_error(uid, "first")
    log_client_error(uid, "second")
    rows = get_recent_client_errors(limit=2)
    assert rows[0]["message"] == "second"
    assert rows[1]["message"] == "first"


# =====================================
# POST /api/client-error
# =====================================

async def test_client_error_route_accepts_and_stores(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/client-error", headers=headers,
        data='{"message": "TypeError: x is null", "stack": "at foo (app.js:1)", "url": "/index.html"}',
    )
    assert r.status == 204

    rows = get_recent_client_errors(limit=5)
    row = next(r for r in rows if r["user_id"] == uid)
    assert row["message"] == "TypeError: x is null"
    assert row["url"] == "/index.html"


async def test_client_error_route_ignores_empty_message(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/client-error", headers=headers, data='{"message": "  "}')
    assert r.status == 204
    rows = get_recent_client_errors(limit=5)
    assert not any(row["user_id"] == uid for row in rows)


async def test_client_error_route_never_errors_on_bad_auth(client):
    # Невалидный initData не должен превращаться в 401/500 — репортер ошибок
    # не должен уметь сам ронять что-то ещё.
    r = await client.post(
        "/api/client-error",
        headers={"Authorization": "tma garbage", "Content-Type": "application/json"},
        data='{"message": "whatever"}',
    )
    assert r.status == 204
