"""
retry_flood_control — единая точка повтора при TelegramRetryAfter (429) для
всех вызовов Bot API, см. bot_middlewares.py.
"""
import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

from bot_middlewares import retry_flood_control


def _retry_after_error(seconds=1):
    method = SendMessage(chat_id=1, text="x")
    return TelegramRetryAfter(method=method, message="flood", retry_after=seconds)


async def test_succeeds_immediately_without_retry(monkeypatch):
    calls = []

    async def make_request(bot, method):
        calls.append(1)
        return "ok"

    monkeypatch.setattr("bot_middlewares.asyncio.sleep", lambda *_: pytest.fail("не должен спать"))

    result = await retry_flood_control(make_request, bot=None, method=None)

    assert result == "ok"
    assert len(calls) == 1


async def test_retries_once_after_flood_control_then_succeeds(monkeypatch):
    attempts = []
    slept = []

    async def make_request(bot, method):
        attempts.append(1)
        if len(attempts) == 1:
            raise _retry_after_error(2)
        return "ok"

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("bot_middlewares.asyncio.sleep", fake_sleep)

    result = await retry_flood_control(make_request, bot=None, method=None)

    assert result == "ok"
    assert len(attempts) == 2
    assert slept == [2.5]  # retry_after + 0.5s буфера


async def test_raises_after_exceeding_max_attempts(monkeypatch):
    attempts = []

    async def make_request(bot, method):
        attempts.append(1)
        raise _retry_after_error(1)

    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr("bot_middlewares.asyncio.sleep", fake_sleep)

    with pytest.raises(TelegramRetryAfter):
        await retry_flood_control(make_request, bot=None, method=None)

    # 1 первая попытка + MAX_RETRY_ATTEMPTS (2) повтора = 3 попытки всего
    assert len(attempts) == 3
