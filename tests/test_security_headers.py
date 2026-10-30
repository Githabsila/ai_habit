"""Безопасные заголовки на ответах сервера (проверка на нарушения 30.10): и на страницах, и на API, и на ошибках."""


async def test_every_response_carries_the_safe_headers(client):
    for path in ("/health", "/", "/api/bootstrap", "/static/app.js", "/no-such-page"):
        response = await client.get(path)
        assert response.headers.get("X-Content-Type-Options") == "nosniff", path
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin", path
        assert "max-age=" in response.headers.get("Strict-Transport-Security", ""), path


async def test_the_app_can_still_be_framed_by_web_telegram(client):
    response = await client.get("/")
    assert "X-Frame-Options" not in response.headers and "frame-ancestors" not in response.headers.get("Content-Security-Policy", "")
