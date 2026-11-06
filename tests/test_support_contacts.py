"""
Возвраты — только через администратора в Telegram или Instagram (решение 05.11). Одна точка контактов (support_contacts.py) — для бота
и для соглашения (/terms в браузере); Instagram появляется, когда владелец задаст SUPPORT_INSTAGRAM.
"""
import config
import support_contacts


def test_contacts_text_with_and_without_instagram(monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_CONTACT", "@a, @b")
    monkeypatch.setattr(config, "SUPPORT_EMAIL", "x@y.z")
    monkeypatch.setattr(config, "SUPPORT_INSTAGRAM", "")
    assert support_contacts.contacts_text() == "<b>@a, @b</b> в Telegram или почта <b>x@y.z</b>"
    monkeypatch.setattr(config, "SUPPORT_INSTAGRAM", "adam_support")
    assert "Instagram https://instagram.com/adam_support" in support_contacts.contacts_text()


async def test_the_terms_page_sends_refunds_to_the_admin(client, monkeypatch):
    monkeypatch.setattr(config, "SUPPORT_CONTACT", "@floydddo, @AlxManagement")
    monkeypatch.setattr(config, "SUPPORT_INSTAGRAM", "adam_support")
    page = await (await client.get("/terms")).text()
    assert "<!--REFUND-->" not in page and "через администратора" in page
    assert 'href="https://t.me/floydddo"' in page and 'href="https://instagram.com/adam_support"' in page
    assert "правилами Telegram Stars" not in page
    assert "ровно 30 дней" in page and "в последний день месяца" in page and "обновлено 06.11.2026" in page
