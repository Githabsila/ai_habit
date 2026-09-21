"""
"Умные напоминания" — раньше единственный раздел настроек, доступный
только из панели бота (см. handlers/settings.py, keyboards.reminders_keyboard).
Бэкенд (db/settings.py::toggle_reminders/toggle_reminder_category) общий
с ботом; эти тесты проверяют Mini App API поверх него.
"""
from db import add_user, get_settings

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_api_toggle_reminders_master_switch(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post("/api/settings/reminders/toggle", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body["reminders"] is False
    assert get_settings(uid)["reminders"] == 0

    r = await client.post("/api/settings/reminders/toggle", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body["reminders"] is True
    assert get_settings(uid)["reminders"] == 1


async def test_api_toggle_reminder_category(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post(
        "/api/settings/reminders/category", headers=headers,
        data='{"category": "streak"}',
    )
    assert r.status == 200
    body = await r.json()
    assert body["category"] == "streak"
    assert body["enabled"] is False
    assert get_settings(uid)["reminders_streak"] == 0
    # Соседние категории не затронуты.
    assert get_settings(uid)["reminders_habits"] == 1
    assert get_settings(uid)["reminders_digests"] == 1


async def test_api_toggle_reminder_category_rejects_unknown_category(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/settings/reminders/category", headers=headers,
        data='{"category": "not_a_real_category"}',
    )
    assert r.status == 400


async def test_bootstrap_exposes_reminder_categories(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.get("/api/bootstrap", headers=headers)
    assert r.status == 200
    data = await r.json()
    assert data["settings"]["reminders_habits"] is True
    assert data["settings"]["reminders_streak"] is True
    assert data["settings"]["reminders_digests"] is True

    await client.post(
        "/api/settings/reminders/category", headers=headers,
        data='{"category": "habits"}',
    )
    r = await client.get("/api/bootstrap", headers=headers)
    data = await r.json()
    assert data["settings"]["reminders_habits"] is False
    assert data["settings"]["reminders_streak"] is True
