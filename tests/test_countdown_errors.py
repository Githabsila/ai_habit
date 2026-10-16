"""
Финишный таймер серии (streak_scheduler._run_countdown) раз в 30 секунд правит одно сообщение. Раньше каждая «ошибка» правки — в том числе безобидное
«message is not modified» — писала в лог полный traceback, и в логах раз в минуту висели десятки ложных ошибок (13–15.10), мешая заметить настоящие.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import streak_scheduler as ss


def _bad_request(text):
    return TelegramBadRequest(method=SimpleNamespace(), message=text)


async def _run(monkeypatch, edit_side_effect, done_after_sleeps=3):
    sleeps = []
    done = {"n": 0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        done["n"] += 1

    monkeypatch.setattr(ss.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ss, "has_completed_today", lambda uid: done["n"] >= done_after_sleeps)
    bot = SimpleNamespace(edit_message_text=AsyncMock(side_effect=edit_side_effect))
    await ss._run_countdown(bot, 848_000_001, 77, "UTC", datetime.now(timezone.utc).date())
    return bot, sleeps


async def test_message_is_not_modified_is_harmless_and_silent(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    bot, sleeps = await _run(monkeypatch, _bad_request("Bad Request: message is not modified: specified new message content and reply markup are exactly the same"))
    assert len(sleeps) == 3 and bot.edit_message_text.await_count >= 3          # таймер жив и продолжает считать
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_deleted_timer_message_stops_the_countdown(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    bot, sleeps = await _run(monkeypatch, _bad_request("Bad Request: message to edit not found"))
    assert bot.edit_message_text.await_count == 1 and sleeps == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_blocked_bot_stops_the_countdown(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    bot, sleeps = await _run(monkeypatch, TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked by the user"))
    assert bot.edit_message_text.await_count == 1 and sleeps == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_other_errors_are_still_reported(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    bot, sleeps = await _run(monkeypatch, RuntimeError("что-то новое"), done_after_sleeps=1)
    assert [r for r in caplog.records if r.levelno >= logging.ERROR and "таймер" in r.getMessage()]
