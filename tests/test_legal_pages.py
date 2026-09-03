"""
Telegram требует ссылку на privacy policy у ботов с платежами (BotFather).
Эти страницы отдаются публично, без авторизации — проверяем, что роуты
реально смонтированы и отдают ожидаемый документ, а не 404/пустой ответ.
"""


async def test_privacy_page_is_served(client):
    resp = await client.get("/privacy")
    assert resp.status == 200
    body = await resp.text()
    assert "Политика конфиденциальности" in body


async def test_terms_page_is_served(client):
    resp = await client.get("/terms")
    assert resp.status == 200
    body = await resp.text()
    assert "Пользовательское соглашение" in body
