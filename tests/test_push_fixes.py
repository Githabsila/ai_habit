"""
Правки по скринам бота (07.10): рассылки не уходят тем, кто ещё в анкете; «первая победа» не дублируется;
утреннее приветствие знает о привычках; узкая свёрнутая карточка парного задания.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import multi_agent
import webapp.webapp_server as ws
from db import (
    add_habit, add_user, ban_user, get_all_users, get_streak_users, set_access_status,
)
from db.ai import get_users_needing_ai_welcome_nudge
from db.core import connect
from db.push_access import push_allowed_sql

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _recipients(uid):
    return {u["telegram_id"] for u in get_all_users(include_blocked=False)} & {uid}


# --- рассылки только после анкеты --------------------------------------------------------

def test_users_in_the_questionnaire_get_no_broadcasts(uid):
    add_user(uid, "u", "Аня")                       # новичок: access_status='new', анкета не заполнена
    assert _recipients(uid) == set()
    set_access_status(uid, "pending")                # анкета на проверке
    assert _recipients(uid) == set()
    set_access_status(uid, "approved")
    assert _recipients(uid) == {uid}


def test_banned_users_get_no_broadcasts_either(uid):
    add_user(uid, "u", "Аня")
    set_access_status(uid, "approved")
    ban_user(uid)
    assert _recipients(uid) == set()


def test_admins_skip_the_questionnaire_so_they_keep_getting_messages(uid, monkeypatch):
    import config
    add_user(uid, "u", "Админ")                      # анкета админов не касается (handlers/start.py)
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    assert _recipients(uid) == {uid}


def test_streak_jobs_and_the_welcome_nudge_use_the_same_rule(uid):
    add_user(uid, "u", "Аня")
    add_habit(uid, "Бег")
    assert uid not in get_streak_users(include_blocked=False)
    assert uid in get_streak_users(include_blocked=True), "админские выборки видят всех"
    conn = connect()
    conn.execute("UPDATE users SET created_at=datetime('now','-10 minutes') WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert uid not in get_users_needing_ai_welcome_nudge()
    set_access_status(uid, "approved")
    assert uid in get_users_needing_ai_welcome_nudge()
    assert uid in get_streak_users(include_blocked=False)


def test_sql_condition_is_a_single_parenthesised_expression():
    sql = push_allowed_sql("u")
    assert sql.startswith("((COALESCE(u.access_status, 'approved') = 'approved'") and "u.banned" in sql


# --- «первая победа» без дублей ------------------------------------------------------------

async def _wins(uid, order):
    add_user(uid, "u", "Аня")
    bot = SimpleNamespace(send_message=AsyncMock())
    app = {"bot": bot}
    results = [await ws._maybe_push_first_win(app, uid, kind) for kind in order]
    await asyncio.gather(*list(ws._background_tasks))
    return results, bot.send_message.call_args_list


async def test_second_first_win_is_one_short_line_without_the_chat_invite(uid):
    results, calls = await _wins(uid, ["habit", "task"])
    assert results == [True, False]
    texts = [c.args[1] for c in calls]
    assert len(texts) == 2
    assert "Красиво. Первый результат закрыт" in texts[0] and "Вижу, ты не теряешь времени зря" in texts[0]
    assert "И первая задача закрыта" in texts[1]
    assert "Красиво" not in texts[1] and "Вижу, ты не теряешь" not in texts[1] and "в чате" not in texts[1]
    assert calls[1].kwargs.get("reply_markup") is None


async def test_the_other_order_is_short_too(uid):
    results, calls = await _wins(uid, ["task", "habit"])
    assert results == [True, False]
    texts = [c.args[1] for c in calls]
    assert "Первая задача закрыта" in texts[0] and "И первая привычка закрыта" in texts[1]


async def test_the_same_win_is_never_sent_twice(uid):
    results, calls = await _wins(uid, ["habit", "habit"])
    assert results == [True, False] and len(calls) == 1


def test_streak_message_is_not_added_to_the_first_win_in_both_completion_routes():
    assert APP_JS and True
    src = Path(ws.__file__).read_text(encoding="utf-8")
    assert src.count("first_win_sent = await _maybe_push_first_win(request.app, telegram_id, \"habit\")") == 2
    assert src.count("if event and not first_win_sent:") == 2


# --- утреннее приветствие ----------------------------------------------------------------------

async def test_morning_prompt_lists_the_habits_or_says_there_are_none(monkeypatch):
    seen = []

    async def fake_ask(system, user, **kwargs):
        seen.append((system, user))
        return "Хорошего дня!"

    monkeypatch.setattr(multi_agent, "_ask", fake_ask)
    await multi_agent.generate_morning_message("neutral", "", 1, habits=["Читать 20 минут"])
    await multi_agent.generate_morning_message("neutral", "", 0)
    system, with_habit = seen[0]
    assert "Привычки пользователя: Читать 20 минут" in with_habit
    assert "Привычек пока нет" in seen[1][1]
    assert "не предлагай «выбрать привычку»" in system


def test_morning_ping_passes_the_habit_titles():
    src = Path(multi_agent.__file__).with_name("morning_ping.py").read_text(encoding="utf-8")
    assert "generate_morning_message(style, \"\", user[\"streak\"], habits=habit_titles)" in src


# --- узкая карточка парного задания ----------------------------------------------------------------

def test_collapsed_pair_card_is_a_single_row():
    html = APP_JS[APP_JS.index("function pairCollapsedHtml("):][:1500]
    assert "pair-card__head--row" in html and "pair-mini" not in html, "прогресс и дни — в той же строке, второй строки нет"
    assert "осталось" in APP_JS[APP_JS.index("function pairSummary("):][:2600]
    assert ".pair-card__head--row{display:flex;align-items:center" in CSS
