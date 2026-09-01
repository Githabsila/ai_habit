"""
API админ-панели Mini App (webapp/routes_admin.py) — каждый роут должен
сам проверять is_admin на сервере, а не полагаться на то, что фронт
спрятал кнопку. Проверяем и обычного пользователя (403), и админа (200),
и что действия реально меняют данные.
"""
from db import add_user, get_user

from tests.conftest import sign_init_data


async def _admin_headers(client, uid):
    init_data = sign_init_data(uid)
    return {"Authorization": f"tma {init_data}"}


async def test_regular_user_gets_403_on_admin_stats(client, uid):
    add_user(uid, "tester", "Test")
    headers = await _admin_headers(client, uid)

    r = await client.get("/api/admin/stats", headers=headers)

    assert r.status == 403
    body = await r.json()
    assert body["error"] == "not_admin"


async def test_admin_gets_200_on_admin_stats(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    add_user(uid, "tester", "Test")
    headers = await _admin_headers(client, uid)

    r = await client.get("/api/admin/stats", headers=headers)

    assert r.status == 200
    data = await r.json()
    assert "report_html" in data
    assert "total_users" in data


async def test_regular_user_gets_403_on_export_db(client, uid):
    add_user(uid, "tester", "Test")
    headers = await _admin_headers(client, uid)

    r = await client.get("/api/admin/export-db", headers=headers)

    assert r.status == 403


async def test_admin_export_db_returns_valid_sqlite_file(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    add_user(uid, "tester", "Test")
    headers = await _admin_headers(client, uid)

    r = await client.get("/api/admin/export-db", headers=headers)

    assert r.status == 200
    assert r.headers["Content-Type"] == "application/octet-stream"
    assert "attachment" in r.headers["Content-Disposition"]
    body = await r.read()
    # Магическая строка заголовка файла SQLite — подтверждает, что это
    # действительно рабочая база, а не что попало.
    assert body[:16] == b"SQLite format 3\x00"


async def test_admin_can_ban_and_unban_another_user(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    add_user(uid, "admin", "Admin")
    target = uid + 1
    add_user(target, "victim", "Victim")
    headers = await _admin_headers(client, uid)

    r1 = await client.post(f"/api/admin/user/{target}/ban", headers=headers)
    assert r1.status == 200
    assert get_user(target)["banned"] == 1

    r2 = await client.post(f"/api/admin/user/{target}/unban", headers=headers)
    assert r2.status == 200
    assert get_user(target)["banned"] == 0


async def test_admin_can_give_xp(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    add_user(uid, "admin", "Admin")
    target = uid + 2
    add_user(target, "user", "User")
    before = get_user(target)["xp"]
    headers = await _admin_headers(client, uid)

    r = await client.post(
        f"/api/admin/user/{target}/xp",
        headers={**headers, "Content-Type": "application/json"},
        data='{"amount": 50}',
    )

    assert r.status == 200
    assert get_user(target)["xp"] == before + 50


async def test_regular_user_cannot_ban_anyone(client, uid):
    add_user(uid, "tester", "Test")
    target = uid + 3
    add_user(target, "victim", "Victim")
    headers = await _admin_headers(client, uid)

    r = await client.post(f"/api/admin/user/{target}/ban", headers=headers)

    assert r.status == 403
    assert get_user(target)["banned"] == 0
