"""
Регрессия на баг из этой сессии: /api/ai/* раньше проверяли только
подпись Telegram initData, без бана/статуса доступа — забаненный
пользователь мог продолжать бесплатно жечь платную AI-квоту.
Теперь все AI-маршруты идут через тот же webapp/auth_helpers.authenticate,
что и остальной Mini App (см. webapp/routes_ai_miniapp.py).
"""
from db import add_user, ban_user

from tests.conftest import sign_init_data


async def test_banned_user_blocked_from_ai_quota_route(client, uid):
    add_user(uid, "tester", "Test")
    ban_user(uid)
    init_data = sign_init_data(uid)

    r = await client.get(
        "/api/ai/quota", headers={"X-Telegram-Init-Data": init_data}
    )

    assert r.status == 403
    body = await r.json()
    assert body["error"] == "banned"


async def test_normal_user_allowed_on_ai_quota_route(client, uid):
    add_user(uid, "tester", "Test")
    init_data = sign_init_data(uid)

    r = await client.get(
        "/api/ai/quota", headers={"X-Telegram-Init-Data": init_data}
    )

    assert r.status == 200
