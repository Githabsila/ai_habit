"""
Каталог сообщений бота в админке (bot_catalog.py, просьба владельца 05.11): каждое сообщение, которое бот шлёт сам, видно текстом
и приходит админу в Telegram по «Прислать мне» — только ему.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot_catalog
import config
import keyboards
from db import add_habit, add_user
from tests.conftest import sign_init_data


def _headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}"}


def test_every_message_renders_without_errors(uid, monkeypatch):
    monkeypatch.setattr(keyboards, "WEBAPP_URL", "https://example.test")
    add_user(uid, "a", "Аня")
    add_habit(uid, "Зарядка")
    items = bot_catalog.catalog_for(uid)
    assert len(items) == len(bot_catalog.CATALOG)
    broken = [(i["key"], i["error"]) for i in items if "error" in i]
    assert broken == []
    by_key = {i["key"]: i for i in items}
    assert "«Зарядка»" in by_key["checkpoint_17"]["text"], "точка дня — на привычках того, кто смотрит"
    assert "ФИНИШНЫЙ ТАЙМЕР" in by_key["timer"]["text"] and by_key["timer"]["buttons"]
    assert "Время вышло" in by_key["timer_expired"]["text"]


def test_without_habits_it_shows_demo_ones(uid):
    add_user(uid, "a", "Аня")
    text, _ = bot_catalog.render("checkpoint_12", uid)
    assert "Пить воду" in text


async def test_the_admin_routes_are_admin_only_and_send_only_to_the_admin(client, uid, monkeypatch):
    admin, person = uid + 700, uid
    add_user(admin, "adm", "Админ")
    add_user(person, "p", "Петя")
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [admin])
    assert (await client.get("/api/admin/bot-messages", headers=_headers(person))).status == 403
    listed = await (await client.get("/api/admin/bot-messages", headers=_headers(admin))).json()
    assert {i["key"] for i in listed["items"]} == set(bot_catalog.BY_KEY)

    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    try:
        assert (await client.post("/api/admin/bot-messages/send", json={"key": "risk23"}, headers=_headers(person))).status == 403
        assert (await client.post("/api/admin/bot-messages/send", json={"key": "nope"}, headers=_headers(admin))).status == 400
        ok = await client.post("/api/admin/bot-messages/send", json={"key": "risk23"}, headers=_headers(admin))
        assert ok.status == 200 and bot.send_message.await_count == 1
        assert bot.send_message.await_args.args[0] == admin, "приходит только самому админу"
    finally:
        client.app["bot"] = None
