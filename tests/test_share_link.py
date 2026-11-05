"""/api/share-link: без имени бота и без WEBAPP_URL ссылка должна быть адресом сервера, а не ошибкой 500.

Найдено при прогоне Mini App в браузере: request.url.origin() — объект yarl.URL, json.dumps его не принимал."""
from db import add_user
from db.core import connect
from tests.conftest import sign_init_data
import webapp.webapp_server as server


async def test_share_link_without_bot_name_or_webapp_url_falls_back_to_the_server_address(client, monkeypatch):
    person = 870_990_001
    add_user(person, "share_t", "Аня")
    conn = connect()
    try:
        conn.execute("UPDATE users SET access_status='approved' WHERE telegram_id=?", (person,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(server, "WEBAPP_URL", "")
    response = await client.get("/api/share-link", headers={"Authorization": f"tma {sign_init_data(person)}"})
    assert response.status == 200
    body = await response.json()
    assert body["ok"] is False and body["bot_username"] is None
    assert body["url"].startswith("http://127.0.0.1") or body["url"].startswith("http://localhost")
