"""
«Александр поддержал(а) тебя!» (14.10): после имени — ник ADAM, чтобы одинаковые имена разных людей различались; имя и ник экранируются
(сообщение уходит с parse_mode=HTML), а ник не превращается Telegram в ссылку на чужой настоящий @username.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from db import add_user
from db.core import connect
from tests.conftest import sign_init_data
from webapp.webapp_server import _name_with_handle

ZWSP = "​"


def _set(uid_, first_name=None, handle=None):
    conn = connect()
    if first_name is not None:
        conn.execute("UPDATE users SET first_name=? WHERE telegram_id=?", (first_name, uid_))
    if handle is not None:
        conn.execute("UPDATE users SET handle=? WHERE telegram_id=?", (handle, uid_))
    conn.commit()
    conn.close()


def _row(uid_):
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (uid_,)).fetchone()
    conn.close()
    return row


async def _react(client, uid_, target, monkeypatch=None):
    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    headers = {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}
    response = await client.post(f"/api/friends/{target}/react", headers=headers, json={"emoji": "🔥"})
    assert response.status == 200
    return bot.send_message.call_args


async def test_the_support_push_names_the_sender_with_the_adam_handle(client, uid):
    add_user(uid, "tg_name", "Александр")
    _set(uid, handle="aleksandr13")
    target = uid + 50_000_000
    add_user(target, "target", "Получатель")
    call = await _react(client, uid, target)
    text = call.args[1]
    assert text == f"🔥 Александр (@{ZWSP}aleksandr13) поддержал(а) тебя!"
    assert call.kwargs["parse_mode"] == "HTML"
    assert "@aleksandr13" not in text, "без невидимого символа Telegram сделал бы ник ссылкой на чужой @username"


async def test_two_people_with_the_same_name_are_told_apart(client, uid):
    first, second, target = uid, uid + 60_000_000, uid + 61_000_000
    for person, handle in ((first, "aleks_one"), (second, "aleks_two")):
        add_user(person, f"u{person}", "Александр")
        _set(person, handle=handle)
    add_user(target, "target", "Получатель")
    one = (await _react(client, first, target)).args[1]
    two = (await _react(client, second, target)).args[1]
    assert one != two and "aleks_one" in one and "aleks_two" in two


async def test_a_name_with_markup_cannot_break_or_hijack_the_message(client, uid):
    add_user(uid, "tg_name", '<a href="https://evil.example">Нажми</a> & Ко')
    target = uid + 52_000_000
    add_user(target, "target", "Получатель")
    text = (await _react(client, uid, target)).args[1]
    assert "<a href" not in text and "&lt;a href=" in text and "&amp; Ко" in text


def test_a_person_without_a_handle_keeps_the_plain_name(uid):
    add_user(uid, "tg_name", "Анна")
    conn = connect()
    conn.execute("UPDATE users SET handle=NULL WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert _name_with_handle(_row(uid)) == "Анна"
    assert _name_with_handle(None, "Кто-то") == "Кто-то"
