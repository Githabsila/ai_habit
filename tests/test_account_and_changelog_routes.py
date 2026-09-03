"""
HTTP-роуты самообслуживания аккаунта и «Что нового» (webapp_server.py):
/api/account/export, /api/account/delete, /api/changelog/unseen,
/api/changelog/seen. См. db/account.py, db/changelog.py.
"""
from db import add_user, add_habit, get_user, add_changelog_entry

from tests.conftest import sign_init_data


async def _headers(uid):
    return {"Authorization": f"tma {sign_init_data(uid)}"}


async def test_account_export_requires_auth(client):
    r = await client.get("/api/account/export")
    assert r.status == 401


async def test_account_export_returns_profile_and_habits(client, uid):
    add_user(uid, "tester", "Test")
    add_habit(uid, "Медитация")
    headers = await _headers(uid)

    r = await client.get("/api/account/export", headers=headers)

    assert r.status == 200
    data = await r.json()
    assert data["profile"]["telegram_id"] == uid
    assert data["habits"][0]["title"] == "Медитация"


async def test_account_delete_requires_auth(client):
    r = await client.post("/api/account/delete")
    assert r.status == 401


async def test_account_delete_bans_and_scrubs_user(client, uid):
    add_user(uid, "realname", "Реальное Имя")
    headers = await _headers(uid)

    r = await client.post("/api/account/delete", headers=headers)

    assert r.status == 200
    user = get_user(uid)
    assert user["banned"] == 1
    assert user["username"] is None


async def test_changelog_unseen_requires_auth(client):
    r = await client.get("/api/changelog/unseen")
    assert r.status == 401


async def test_changelog_unseen_then_seen_flow(client, uid):
    add_changelog_entry(f"Фича для {uid}", "Описание")
    add_user(uid, "tester", "Test")
    headers = await _headers(uid)

    r = await client.get("/api/changelog/unseen", headers=headers)
    assert r.status == 200
    data = await r.json()
    assert len(data["entries"]) >= 1

    r2 = await client.post("/api/changelog/seen", headers=headers)
    assert r2.status == 200

    r3 = await client.get("/api/changelog/unseen", headers=headers)
    data3 = await r3.json()
    assert data3["entries"] == []
