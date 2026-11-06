"""
«Просмотр на телефонах» (/admin/devices): любой экран или сцену — сразу в пяти рамках (Android, iPhone SE/15/Pro Max, Telegram на ПК).
Вход внутрь рамок — #tgWebAppData, как Telegram передаёт его во встроенное окно; рамки грузятся по очереди, админам выше лимит запросов.
"""
from pathlib import Path

import webapp.webapp_server as server

PAGE = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "devices.html").read_text(encoding="utf-8")


async def test_the_page_is_served_without_cache(client):
    response = await client.get("/admin/devices")
    assert response.status == 200 and "Просмотр на телефонах" in await response.text()
    assert "no-store" in response.headers["Cache-Control"]


def test_the_page_has_five_devices_and_passes_the_login_like_telegram():
    for size in ("360×800", "375×667", "393×852", "430×932", "480×700"):
        assert size in PAGE
    assert '"tgWebAppData=" + encodeURIComponent(initData)' in PAGE
    assert "i * 1500" in PAGE, "рамки грузятся по очереди — без всплеска запросов"
    assert "/api/admin/lab/sims" in PAGE


def test_admins_get_a_higher_rate_limit(monkeypatch):
    monkeypatch.setattr(server, "_rate_limit_buckets", server.defaultdict(server.deque))
    user, admin = 871_100_001, 871_100_002
    allowed_user = sum(server._check_rate_limit(user) for _ in range(server.RATE_LIMIT_MAX_REQUESTS + 10))
    allowed_admin = sum(server._check_rate_limit(admin, is_admin=True) for _ in range(server.RATE_LIMIT_MAX_REQUESTS * 3))
    assert allowed_user == server.RATE_LIMIT_MAX_REQUESTS
    assert allowed_admin == server.RATE_LIMIT_MAX_REQUESTS * 3
