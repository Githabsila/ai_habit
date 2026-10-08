"""
Анкета при входе: три вопроса, ответ одним нажатием (или текстом), карточка меняется на месте,
заявка сразу уходит админу (раньше падало на sqlite3.Row.get), ИИ-разбор — в фоне.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import handlers.onboarding as ob
from db import add_user, get_access_status, get_survey, set_access_status, survey_variant


def _state(user_id):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))


def _bot():
    return SimpleNamespace(send_message=AsyncMock())


def _message(user_id, text=None, bot=None):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id, username="tester", full_name="Тест Тестов"),
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        bot=bot or _bot(),
    )


def _tap(user_id, data, bot, card=None):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, username="tester", full_name="Тест Тестов"),
        message=card or _message(user_id, bot=bot),
        answer=AsyncMock(),
        bot=bot,
    )


def _buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    monkeypatch.setattr(ob, "analyze_onboarding_survey", AsyncMock(return_value={"summary": "Резюме", "tags": ["спорт"]}))
    monkeypatch.setattr(ob, "suggest_first_step", AsyncMock(return_value={"habit": "Бег", "milestones": ["1", "2", "3"]}))


async def _flush():
    await asyncio.gather(*list(ob._background_tasks))


async def test_survey_has_three_questions_each_answerable_by_one_tap(uid):
    add_user(uid, "u", "Аня")
    state = _state(uid)
    first = _message(uid)
    await ob.begin_survey(first, state)
    text = first.answer.call_args.args[0]
    markup = first.answer.call_args.kwargs["reply_markup"]
    assert "три вопроса" in text.lower() or "три быстрых вопроса" in text.lower()
    assert "4 " not in text and "1/4" not in text
    assert [b.callback_data for b in _buttons(markup)] == [f"survey:role:{i}" for i in range(len(ob.SURVEY_ROLES))]
    assert await state.get_state() == ob.Onboarding.business.state

    bot = _bot()
    card = _message(uid, bot=bot)
    await ob.survey_role_tap(_tap(uid, "survey:role:1", bot, card), state)
    assert await state.get_state() == ob.Onboarding.focus.state
    focus_markup = card.edit_text.call_args.kwargs["reply_markup"]
    assert "●●○" in card.edit_text.call_args.args[0]
    assert len(_buttons(focus_markup)) == len(ob.SURVEY_FOCUS)
    card.answer.assert_not_called()  # карточка перелистнулась на месте, ленты вопросов нет

    await ob.survey_focus_tap(_tap(uid, "survey:focus:2", bot, card), state)
    assert await state.get_state() == ob.Onboarding.goal.state
    goal_buttons = _buttons(card.edit_text.call_args.kwargs["reply_markup"])
    assert [b.text for b in goal_buttons] == list(ob.SURVEY_GOAL_IDEAS[2])
    assert "●●●" in card.edit_text.call_args.args[0]


async def test_taps_alone_complete_the_application_and_ping_the_admin(uid, monkeypatch):
    monkeypatch.setattr(ob, "ADMIN_IDS", [111])
    add_user(uid, "u", "Аня")
    state = _state(uid)
    bot = _bot()
    card = _message(uid, bot=bot)
    await ob.begin_survey(_message(uid, bot=bot), state)
    await ob.survey_role_tap(_tap(uid, "survey:role:1", bot, card), state)
    await ob.survey_focus_tap(_tap(uid, "survey:focus:2", bot, card), state)
    await ob.survey_goal_tap(_tap(uid, "survey:goal:0", bot, card), state)
    await _flush()

    survey = get_survey(uid)
    assert survey["business"] == "Свой бизнес"
    assert survey["life_goal"] == "Здоровье и спорт"
    assert survey["bot_goal"] == "Тренироваться 3 раза в неделю"
    assert survey["hobbies"] == ""
    assert survey["ai_summary"] == "Резюме", "ИИ-разбор досчитывается в фоне"
    assert get_access_status(uid) == "pending"
    assert await state.get_state() is None

    assert "Заявка принята" in card.edit_text.call_args.args[0]
    assert card.edit_text.call_args.kwargs["reply_markup"] is None

    bot.send_message.assert_awaited()  # раньше падало на survey.get(...) у sqlite3.Row — админ не узнавал о заявке
    sent = bot.send_message.call_args.kwargs
    assert sent["chat_id"] == 111
    assert "Свой бизнес" in sent["text"] and "Здоровье и спорт" in sent["text"] and "Тренироваться 3 раза" in sent["text"]
    assert f"admin_approve_{uid}" in sent["reply_markup"].inline_keyboard[0][0].callback_data


async def test_typed_answers_work_too_and_open_new_messages(uid, monkeypatch):
    monkeypatch.setattr(ob, "ADMIN_IDS", [])
    add_user(uid, "u", "Аня")
    state = _state(uid)
    bot = _bot()
    await ob.begin_survey(_message(uid, bot=bot), state)

    step1 = _message(uid, "  Веду   бар  ", bot)
    await ob.survey_role_text(step1, state)
    assert (await state.get_data())["business"] == "Веду бар"
    assert await state.get_state() == ob.Onboarding.focus.state

    step2 = _message(uid, "Читать больше", bot)
    await ob.survey_focus_text(step2, state)
    ideas = _buttons(step2.answer.call_args.kwargs["reply_markup"])
    assert [b.text for b in ideas] == list(ob.SURVEY_GOAL_IDEAS_DEFAULT)

    step3 = _message(uid, "Дочитать 12 книг за год", bot)
    await ob.survey_goal_text(step3, state)
    await _flush()
    survey = get_survey(uid)
    assert (survey["business"], survey["life_goal"], survey["bot_goal"]) == ("Веду бар", "Читать больше", "Дочитать 12 книг за год")
    assert "Заявка принята" in step3.answer.call_args.args[0]


async def test_a_sticker_instead_of_an_answer_gets_a_nudge_not_an_empty_answer(uid):
    add_user(uid, "u", "Аня")
    state = _state(uid)
    await ob.begin_survey(_message(uid), state)
    sticker = _message(uid, None)
    await ob.survey_role_text(sticker, state)
    assert await state.get_state() == ob.Onboarding.business.state
    assert sticker.answer.call_args.args[0] == ob.SURVEY_TEXT_NUDGE


async def test_premium_users_get_access_right_away(uid):
    add_user(uid, "u", "Аня")
    from db.core import connect
    conn = connect()
    conn.execute("UPDATE users SET premium=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    state = _state(uid)
    bot = _bot()
    card = _message(uid, bot=bot)
    await state.set_state(ob.Onboarding.goal)
    await state.update_data(business="Учусь", life_goal="Дисциплина", focus_index=0)
    await ob.survey_goal_tap(_tap(uid, "survey:goal:1", bot, card), state)
    assert get_access_status(uid) == "approved"
    assert "Premium" in card.edit_text.call_args.args[0]
    assert "Доступ открыт" in bot.send_message.call_args.kwargs["text"]
    assert "Бег" in bot.send_message.call_args.kwargs["text"]


async def test_stale_buttons_do_not_write_anything(uid):
    add_user(uid, "u", "Аня")
    set_access_status(uid, "pending")
    callback = _tap(uid, "survey:goal:0", _bot())
    await ob.survey_stale_tap(callback)
    callback.answer.assert_awaited_once_with(ob.SURVEY_STALE_TOAST)
    assert get_survey(uid) is None


async def test_garbage_callback_data_is_ignored(uid):
    add_user(uid, "u", "Аня")
    state = _state(uid)
    await ob.begin_survey(_message(uid), state)
    bad = _tap(uid, "survey:role:99", _bot())
    await ob.survey_role_tap(bad, state)
    assert await state.get_state() == ob.Onboarding.business.state
    bad.answer.assert_awaited_once_with()


def test_callback_data_fits_telegram_limit_and_texts_are_short():
    for markup in (ob._chips_keyboard("role", ob.SURVEY_ROLES), ob._chips_keyboard("focus", ob.SURVEY_FOCUS)):
        assert all(len(b.callback_data.encode()) <= 64 for b in _buttons(markup))
    for ideas in ob.SURVEY_GOAL_IDEAS + (ob.SURVEY_GOAL_IDEAS_DEFAULT,):
        assert len(ideas) == 3 and all(len(i) <= 40 for i in ideas)
    assert len(ob.SURVEY_GOAL_IDEAS) == len(ob.SURVEY_FOCUS)
    for variant, text in ob.SURVEY_INTRO_VARIANTS.items():
        assert len(text) < 260, variant


def test_both_ab_variants_are_kept_for_the_funnel():
    assert set(ob.SURVEY_INTRO_VARIANTS) == {"A", "B"}
    assert {survey_variant(900000002), survey_variant(900000003)} == {"A", "B"}


async def test_restarting_with_start_resets_a_half_filled_survey(uid):
    add_user(uid, "u", "Аня")
    state = _state(uid)
    await ob.begin_survey(_message(uid), state)
    await state.update_data(business="Учусь")
    await ob.begin_survey(_message(uid), state)
    assert await state.get_data() == {}
    assert await state.get_state() == ob.Onboarding.business.state


# --- демо для админа -----------------------------------------------------------------------------------

async def test_admin_preview_walks_through_without_saving_anything(uid, monkeypatch):
    monkeypatch.setattr(ob, "ADMIN_IDS", [uid])
    add_user(uid, "u", "Админ")
    set_access_status(uid, "approved")
    state = _state(uid)
    bot = _bot()
    start = _message(uid, "/survey_preview B", bot)
    await ob.survey_preview(start, state)
    assert "Привет, я ADAM" in start.answer.call_args.args[0], "второй вариант вступления по просьбе"
    card = _message(uid, bot=bot)
    await ob.survey_role_tap(_tap(uid, "survey:role:0", bot, card), state)
    await ob.survey_focus_tap(_tap(uid, "survey:focus:0", bot, card), state)
    await ob.survey_goal_tap(_tap(uid, "survey:goal:0", bot, card), state)
    await _flush()
    text = card.edit_text.call_args.args[0]
    assert "Заявка принята" in text and "Демо" in text
    assert get_survey(uid) is None
    assert get_access_status(uid) == "approved"
    bot.send_message.assert_not_called()


async def test_preview_is_for_admins_only(uid, monkeypatch):
    monkeypatch.setattr(ob, "ADMIN_IDS", [])
    add_user(uid, "u", "Аня")
    state = _state(uid)
    msg = _message(uid, "/survey_preview")
    await ob.survey_preview(msg, state)
    msg.answer.assert_not_called()
    assert await state.get_state() is None
