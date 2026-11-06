"""
Возраст 16+ (решение владельца 05.11, db/age_gate.py): в стартовой анкете «Мне нет 16» закрывает приложение, бот отвечает одним текстом,
рассылки не идут; если человек ошибся — админ возвращает доступ кнопкой в веб-админке.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
from db import add_user, get_access_status
from db.age_gate import AGE_RESTRICTED, AGE_RESTRICTED_TEXT
from db.core import connect
from db.push_access import push_allowed_sql
from middlewares.access_control import AccessControlMiddleware
from tests.conftest import sign_init_data


def _headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}"}


def _push_allowed(user_id):
    conn = connect()
    try:
        return conn.execute(f"SELECT 1 FROM users WHERE telegram_id=? AND {push_allowed_sql('users')}", (user_id,)).fetchone() is not None
    finally:
        conn.close()


async def test_under_16_closes_the_app_the_ai_chat_and_the_reminders(client, uid):
    assert (await client.get("/api/bootstrap", headers=_headers(uid))).status == 200
    response = await client.post("/api/start-quiz/seen", json={"age_range": "under16", "goal": None}, headers=_headers(uid))
    assert (await response.json()) == {"ok": True, "age_restricted": True}
    assert get_access_status(uid) == AGE_RESTRICTED and not _push_allowed(uid)

    closed = await client.get("/api/bootstrap", headers=_headers(uid))
    body = await closed.json()
    assert closed.status == 403 and body["error"] == "access_age_restricted" and body["message"] == AGE_RESTRICTED_TEXT
    chat = await client.get("/api/ai/history", params={"init_data": sign_init_data(uid)})
    assert chat.status == 403, "чат с Адамом проверяет доступ тем же способом"


async def test_16_17_and_older_keep_access(client, uid):
    for age in ("16-17", "18-24"):
        person = uid + (1 if age == "18-24" else 0)
        await client.get("/api/bootstrap", headers=_headers(person))
        response = await client.post("/api/start-quiz/seen", json={"age_range": age}, headers=_headers(person))
        assert (await response.json()) == {"ok": True}
        assert (await client.get("/api/bootstrap", headers=_headers(person))).status == 200


async def test_the_bot_answers_everything_with_the_age_text(uid):
    add_user(uid, "kid", "Петя")
    connect_ = connect()
    try:
        connect_.execute("UPDATE users SET access_status=? WHERE telegram_id=?", (AGE_RESTRICTED, uid))
        connect_.commit()
    finally:
        connect_.close()
    handler = AsyncMock()
    message = SimpleNamespace(from_user=SimpleNamespace(id=uid, username="kid"), text="/start", successful_payment=None, answer=AsyncMock())
    await AccessControlMiddleware()(handler, message, {})
    assert handler.await_count == 0 and message.answer.await_args.args[0] == AGE_RESTRICTED_TEXT


async def test_the_admin_sees_them_and_can_give_access_back(client, uid, monkeypatch):
    admin = uid + 500
    add_user(admin, "adm", "Админ")
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [admin])
    await client.get("/api/bootstrap", headers=_headers(uid))
    await client.post("/api/start-quiz/seen", json={"age_range": "under16"}, headers=_headers(uid))
    listed = await (await client.get("/api/admin/pending", headers=_headers(admin))).json()
    row = next(u for u in listed["users"] if u["telegram_id"] == uid)
    assert row["age_restricted"] is True
    assert (await client.post(f"/api/admin/approve/{uid}", headers=_headers(admin))).status == 200
    assert get_access_status(uid) == "approved" and (await client.get("/api/bootstrap", headers=_headers(uid))).status == 200
