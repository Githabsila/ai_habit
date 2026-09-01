"""
HTTP-роуты для roadmap #43 (сегментированная рассылка), #44 (карточка
пользователя для поддержки), #45 (риск оттока).
"""
from db import add_user

from tests.conftest import sign_init_data


async def _admin_headers(client, uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid])
    init_data = sign_init_data(uid)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_regular_user_gets_403_on_user_card(client, uid):
    add_user(uid, "u", "Test")
    init_data = sign_init_data(uid)
    r = await client.get(f"/api/admin/user/{uid}", headers={"Authorization": f"tma {init_data}"})
    assert r.status == 403


async def test_admin_gets_support_card(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    target = uid + 50_000_000
    add_user(target, "victim", "Victim")

    r = await client.get(f"/api/admin/user/{target}", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert body["telegram_id"] == target
    assert "habits" in body
    assert "subscription" in body


async def test_admin_user_card_404_for_unknown(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    r = await client.get("/api/admin/user/999999999999", headers=headers)
    assert r.status == 404


async def test_regular_user_gets_403_on_churn_risk(client, uid):
    add_user(uid, "u", "Test")
    init_data = sign_init_data(uid)
    r = await client.get("/api/admin/churn-risk", headers={"Authorization": f"tma {init_data}"})
    assert r.status == 403


async def test_admin_gets_churn_risk_report(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    r = await client.get("/api/admin/churn-risk", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert "tiers" in body
    assert "at_risk" in body


async def test_admin_gets_broadcast_segments(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    r = await client.get("/api/admin/broadcast/segments", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert any(s["key"] == "all" for s in body["segments"])


async def test_broadcast_with_unknown_segment_rejected(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")

    class FakeBot:
        async def send_message(self, **kwargs):
            pass

    client.app["bot"] = FakeBot()
    r = await client.post("/api/admin/broadcast", headers=headers, json={"text": "hi", "segment": "not_real"})
    assert r.status == 400


async def test_broadcast_with_known_segment_sends(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    target = uid + 60_000_000
    add_user(target, "premium_user", "PremiumUser")
    from db.core import connect
    conn = connect()
    conn.execute("UPDATE users SET premium=1 WHERE telegram_id=?", (target,))
    conn.commit()
    conn.close()

    sent_to = []

    class FakeBot:
        async def send_message(self, chat_id, **kwargs):
            sent_to.append(chat_id)

    client.app["bot"] = FakeBot()
    r = await client.post("/api/admin/broadcast", headers=headers, json={"text": "hi", "segment": "premium"})
    assert r.status == 200
    body = await r.json()
    assert body["success"] >= 1
    assert target in sent_to


async def test_broadcast_no_bot_returns_503(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    r = await client.post("/api/admin/broadcast", headers=headers, json={"text": "hi", "segment": "premium"})
    assert r.status == 503  # test app has no bot attached
