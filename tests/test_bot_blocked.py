"""
Найдено при разборе ежедневного мониторинга ошибок: 29 ошибок/24ч, все —
"Forbidden: bot was blocked by the user" от одних и тех же двух
пользователей, повторявшиеся каждый тик в окне каждого из 5 рассылочных
job'ов, потому что ничего не запоминало постоянный отказ. db/users.py::
mark_bot_blocked/is_bot_blocked/clear_bot_blocked — см. также
tests/test_habit_checkpoints.py для интеграционных сценариев coach.py.
"""
from db import add_user, mark_bot_blocked, is_bot_blocked, clear_bot_blocked

from middlewares.access_control import AccessControlMiddleware


def test_mark_bot_blocked_sets_flag(uid):
    add_user(uid, "u", "Test")
    assert not is_bot_blocked(uid)
    mark_bot_blocked(uid)
    assert is_bot_blocked(uid)


def test_mark_bot_blocked_is_idempotent(uid):
    add_user(uid, "u", "Test")
    mark_bot_blocked(uid)
    mark_bot_blocked(uid)
    assert is_bot_blocked(uid)


def test_clear_bot_blocked_unsets_flag(uid):
    add_user(uid, "u", "Test")
    mark_bot_blocked(uid)
    assert is_bot_blocked(uid)
    clear_bot_blocked(uid)
    assert not is_bot_blocked(uid)


def test_clear_bot_blocked_noop_when_not_blocked(uid):
    add_user(uid, "u", "Test")
    clear_bot_blocked(uid)
    assert not is_bot_blocked(uid)


def test_is_bot_blocked_false_for_unknown_user(uid):
    assert not is_bot_blocked(uid)


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class _FakeMessage:
    """Достаточно атрибутов, чтобы пройти через AccessControlMiddleware —
    гейт подписки выключен по умолчанию (SUBSCRIPTION_GATE_ENABLED), так
    middleware просто вызывает побочные эффекты (touch_last_seen/
    clear_bot_blocked) и пропускает обработчик дальше."""

    def __init__(self, user_id):
        self.from_user = _FakeUser(user_id)
        self.successful_payment = None
        self.text = "привет"


async def test_any_incoming_message_clears_bot_blocked_flag(uid):
    add_user(uid, "u", "Test")
    mark_bot_blocked(uid)
    assert is_bot_blocked(uid)

    middleware = AccessControlMiddleware()
    called = []

    async def handler(event, data):
        called.append(event)
        return "ok"

    result = await middleware(handler, _FakeMessage(uid), {})

    assert result == "ok"
    assert called
    assert not is_bot_blocked(uid)
