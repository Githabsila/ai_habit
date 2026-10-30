"""Ответ бота про привычки уходит в HTML-режиме: «<название>» из подсказки и «<», «&» из названий привычек должны экранироваться, иначе Telegram отвечает «can't parse entities»."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from db import add_habit, add_user
from handlers import ai as ai_handler


def _message(user_id, text):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id), text=text, voice=None, chat=SimpleNamespace(id=user_id),
        answer=AsyncMock(), bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )


async def test_the_hint_about_several_habits_is_escaped_for_html(uid):
    add_user(uid, f"u{uid}", "Аня")
    add_habit(uid, "Зарядка")
    add_habit(uid, "Вода")
    message = _message(uid, "выполни её")
    await ai_handler.ai_chat(message, SimpleNamespace())
    sent = message.answer.call_args.args[0]
    assert "&lt;название&gt;" in sent and "<название>" not in sent


async def test_a_habit_title_with_angle_brackets_does_not_break_the_reply(uid):
    add_user(uid, f"u{uid}", "Аня")
    add_habit(uid, "Бег <5 км & стретчинг")
    message = _message(uid, "удали её")
    await ai_handler.ai_chat(message, SimpleNamespace())
    sent = message.answer.call_args.args[0]
    assert "Бег &lt;5 км &amp; стретчинг" in sent and "<5" not in sent


def test_scheduled_reminders_escape_what_the_person_typed():
    from adam_messages import format_plan_task_reminder_message, format_planned_time_reminder_message
    for text in (format_planned_time_reminder_message("Читать <20 мин & писать"), format_plan_task_reminder_message("Доклад <срочно> & слайды")):
        assert "&lt;" in text and "&amp;" in text and "<20" not in text and "<срочно" not in text
