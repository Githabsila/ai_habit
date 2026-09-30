"""
/api/ai/clear (webapp/routes_ai_miniapp.py) — жалоба пользователя:
"очистка сообщений не работает". Кнопка 🧹 "Новый диалог" в ai_coach.js
раньше чистила только экран (sessionStorage) — db.clear_ai_history уже
существовала, но её нигде не вызывали, поэтому история на сервере
оставалась и подтягивалась обратно при следующей загрузке.
"""
from db import add_user, add_ai_message, get_ai_history

from tests.conftest import sign_init_data


async def test_clear_route_requires_auth(client):
    r = await client.post("/api/ai/clear", json={"init_data": ""})
    assert r.status in (401, 403)


async def test_clear_route_deletes_history(client, uid):
    add_user(uid, "tester", "Test")
    add_ai_message(uid, "user", "Привет")
    add_ai_message(uid, "assistant", "Привет! Чем помочь?")
    assert len(get_ai_history(uid)) == 2

    r = await client.post("/api/ai/clear", json={"init_data": sign_init_data(uid)})

    assert r.status == 200
    assert get_ai_history(uid) == []


async def test_clear_route_only_clears_own_history(client, uid):
    add_user(uid, "tester", "Test")
    other_user = uid + 1
    add_user(other_user, "other", "Other")
    add_ai_message(uid, "user", "Моё сообщение")
    add_ai_message(other_user, "user", "Чужое сообщение")

    await client.post("/api/ai/clear", json={"init_data": sign_init_data(uid)})

    assert get_ai_history(uid) == []
    assert len(get_ai_history(other_user)) == 1
