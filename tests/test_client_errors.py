"""
Улучшение #70: логирование клиентских JS-ошибок (window.onerror /
unhandledrejection на фронте -> POST /api/client-error -> db.client_errors).
"""
from db import add_user, log_client_error, get_recent_client_errors, get_client_error_stats

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



# =====================================
# "Script error." — непрозрачная заглушка браузера, а не баг приложения
# =====================================

async def test_client_error_route_drops_opaque_script_error(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/client-error", headers=headers, data='{"message": "Script error."}')
    assert r.status == 204
    assert not any(row["user_id"] == uid for row in get_recent_client_errors(limit=5, include_opaque=True))


async def test_client_error_route_keeps_script_error_when_it_has_a_stack(client, uid):
    # Со стеком в записи уже есть за что зацепиться — это не заглушка.
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/client-error", headers=headers,
        data='{"message": "Script error.", "stack": "https://x/app.js:10:5"}',
    )
    assert r.status == 204
    assert any(row["user_id"] == uid for row in get_recent_client_errors(limit=5))


def test_opaque_errors_hidden_from_recent_list_by_default(uid):
    # Старые записи, сохранённые до фильтра, не должны засорять админ-панель.
    log_client_error(uid, "Script error.")
    log_client_error(uid, "TypeError: x is null")
    messages = [r["message"] for r in get_recent_client_errors(limit=10) if r["user_id"] == uid]
    assert messages == ["TypeError: x is null"]
    assert any(
        r["message"] == "Script error." for r in get_recent_client_errors(limit=10, include_opaque=True)
    )


def test_log_client_error_strips_tg_init_data_from_url(uid):
    log_client_error(uid, "boom", url="https://host/path#tgWebAppData=user%3D%7B%22id%22%7D&hash=abc")
    row = next(r for r in get_recent_client_errors(limit=5) if r["user_id"] == uid)
    assert row["url"] == "https://host/path"
    assert "tgWebAppData" not in row["url"]


def test_get_client_error_stats_groups_by_message_and_counts_users(uid, clean_error_tables):
    other = uid + 1
    log_client_error(uid, "TypeError: a")
    log_client_error(uid, "TypeError: a")
    log_client_error(other, "TypeError: a")
    log_client_error(uid, "RangeError: b")
    log_client_error(uid, "Script error.")  # не считается

    stats = get_client_error_stats(hours=24)

    assert stats["total"] == 4
    assert stats["users"] == 2
    assert stats["top"][0] == {"message": "TypeError: a", "cnt": 3, "users": 2}
    assert stats["top"][1]["message"] == "RangeError: b"


def test_get_client_error_stats_empty(clean_error_tables):
    assert get_client_error_stats(hours=24) == {"total": 0, "users": 0, "top": []}


# =====================================
# crossorigin на Telegram SDK: без него браузер зачищает любые ошибки из
# скрипта telegram.org до "Script error." (см. db/client_errors.py)
# =====================================

def test_telegram_sdk_script_tags_have_crossorigin():
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "webapp" / "static"
    for name in ("index.html", "ai_miniapp_styled.html", "admin_panel.html"):
        html_text = (static / name).read_text(encoding="utf-8")
        tags = re.findall(r"<script[^>]*telegram-web-app\.js[^>]*>", html_text)
        assert tags, f"{name}: нет подключения Telegram SDK"
        assert all('crossorigin="anonymous"' in t for t in tags), f"{name}: нет crossorigin"


async def test_client_error_route_never_errors_on_bad_auth(client):
    # Невалидный initData не должен превращаться в 401/500 — репортер ошибок
    # не должен уметь сам ронять что-то ещё.
    r = await client.post(
        "/api/client-error",
        headers={"Authorization": "tma garbage", "Content-Type": "application/json"},
        data='{"message": "whatever"}',
    )
    assert r.status == 204
