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


async def test_the_terms_describe_the_renewal_the_way_payment_really_works(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", False)
    manual = await (await client.get("/terms")).text()
    assert "не продлевается автоматически" in manual and "<!--RENEWAL-->" not in manual
    assert "первые 3 календарных дня" in manual.lower() and "дополнительное окно в 24 часа" in manual and "добавляются к оплаченному сроку" in manual
    monkeypatch.setattr(config, "SUBSCRIPTION_AUTORENEW_ENABLED", True)
    auto = await (await client.get("/terms")).text()
    assert "автоматическим списанием" in auto and "не продлевается автоматически" not in auto
