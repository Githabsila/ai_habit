"""
Регрессия на баг "не могу применить тему" из этой сессии: bootstrap
раньше всегда отдавал theme_owned=False (захардкожено), и картинка не
могла обновиться, даже если тема реально куплена. Полный HTTP-путь:
bootstrap → купить тему → bootstrap снова (owned=True) → применить цвет
→ он реально сохраняется.
"""
import json

from db import connect
from tests.conftest import sign_init_data


async def test_theme_apply_flow(client, uid):
    init_data = sign_init_data(uid)
    auth = {"Authorization": f"tma {init_data}"}

    r = await client.get("/api/bootstrap", headers=auth)
    assert r.status == 200
    data = await r.json()
    assert data["settings"]["theme_owned"] is False
    assert data["settings"]["theme"] == "violet"

    conn = connect()
    conn.execute("UPDATE users SET xp=9999 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    r_buy = await client.post("/api/buy/2", headers=auth)
    assert r_buy.status == 200

    r2 = await client.get("/api/bootstrap", headers=auth)
    data2 = await r2.json()
    assert data2["settings"]["theme_owned"] is True

    r3 = await client.post(
        "/api/settings/theme",
        headers={**auth, "Content-Type": "application/json"},
        data=json.dumps({"theme": "blue"}),
    )
    assert r3.status == 200

    r4 = await client.get("/api/bootstrap", headers=auth)
    data4 = await r4.json()
    assert data4["settings"]["theme"] == "blue"


async def test_theme_apply_rejected_without_ownership(client, uid):
    init_data = sign_init_data(uid)
    auth = {"Authorization": f"tma {init_data}"}
    await client.get("/api/bootstrap", headers=auth)  # создаёт пользователя

    r = await client.post(
        "/api/settings/theme",
        headers={**auth, "Content-Type": "application/json"},
        data=json.dumps({"theme": "blue"}),
    )

    assert r.status == 403
    body = await r.json()
    assert body["error"] == "theme_not_owned"
