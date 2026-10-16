"""
Парное задание: напарник пропал (16.10, п.16). Если второй участник 2 и больше ПОЛНЫХ дня не заходит в приложение и не отмечается, из начавшегося
задания можно выйти раньше срока и позвать другого друга — не ждать, пока кончится недельное окно. Пока напарник живой, начавшееся задание по-прежнему
отменить нельзя (иначе он остался бы без награды из-за чужого настроения).
"""
import asyncio
import itertools
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import webapp.webapp_server as ws
from db import get_pair_quest, send_pair_invite
from db.core import connect
from db.pair_lab import build_scenario, lab_scenarios
from db.pair_quests import LEAVE_IDLE_DAYS, _build_view, cancel_pair_quest, partner_idle_days
from tests.conftest import sign_init_data
from tests.test_pair_quests import _clean, _day, _pair, _sql, _start, _status, _user, _window

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(851_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _running(a, b, started_days_ago=4):
    """Начавшееся задание (правила v2) без «вчерашних» отметок фикстуры."""
    quest_id = _start(a, b, rules="habits")
    _window(quest_id, start_offset=-started_days_ago)
    _clean(a, b)                                                                  # «вчерашние» отметки фикстуры попали бы в сдвинутое окно
    return quest_id


def _seen(user_id, hours_ago):
    _sql("UPDATE users SET last_seen=datetime('now', ?) WHERE telegram_id=?", (f"-{hours_ago} hours", user_id))


def _mark_at(user_id, hours_ago):
    _sql("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?, 1, datetime('now', ?))", (user_id, f"-{hours_ago} hours"))


def _view_of(user_id):
    return get_pair_quest(user_id)["quest"]


# ------------------------------------------------------------------------------------------------ когда можно выйти

def test_a_living_partner_keeps_the_quest_locked(uid):
    a, b = _pair(uid)
    quest_id = _running(a, b)
    _seen(b, 1)
    view = _view_of(a)
    assert view["partner_idle_days"] == 0 and view["can_leave"] is False
    assert cancel_pair_quest(a, quest_id) == {"error": "cannot_cancel"} and _status(quest_id) == "active"


def test_two_full_days_of_silence_open_the_exit_and_both_are_free_afterwards(uid):
    a, b = _pair(uid)
    other = _user(uid + 2, "Вера")
    from db import make_friends
    assert make_friends(a, other)
    _day(other, -1)                                                             # приглашать можно только «живого» друга
    quest_id = _running(a, b)
    _seen(b, 72)
    _mark_at(b, 72)
    view = _view_of(a)
    assert view["partner_idle_days"] == 3 and view["can_leave"] is True and LEAVE_IDLE_DAYS == 2
    result = cancel_pair_quest(a, quest_id)
    assert result == {"ok": True, "partner_id": b, "left_early": True, "idle_days": 3} and _status(quest_id) == "cancelled"
    assert _view_of(a) is None and _view_of(b) is None                          # задания больше нет ни у кого — оба свободны
    assert send_pair_invite(a, other).get("ok"), "после выхода можно позвать другого друга"


def test_the_threshold_is_two_full_days(uid):
    a, b = _pair(uid)
    quest_id = _running(a, b)
    _seen(b, 47)
    _mark_at(b, 47)
    assert _view_of(a)["partner_idle_days"] == 1 and _view_of(a)["can_leave"] is False
    assert cancel_pair_quest(a, quest_id) == {"error": "cannot_cancel"}
    _sql("DELETE FROM habit_completion_events WHERE user_id=?", (b,))
    _seen(b, 49)
    _mark_at(b, 49)
    assert _view_of(a)["partner_idle_days"] == 2 and _view_of(a)["can_leave"] is True


def test_a_recent_mark_counts_even_if_the_last_visit_looks_old(uid):
    a, b = _pair(uid)
    _running(a, b)
    _seen(b, 24 * 5)
    _mark_at(b, 1)                                                              # отметка через бота: заходов в приложение нет, а человек живой
    assert _view_of(a)["partner_idle_days"] == 0 and _view_of(a)["can_leave"] is False


def test_a_recent_visit_without_marks_also_counts(uid):
    a, b = _pair(uid)
    _running(a, b)
    _mark_at(b, 24 * 6)
    _seen(b, 2)
    assert _view_of(a)["partner_idle_days"] == 0


def test_without_any_trace_the_days_are_counted_from_the_quest_start(uid):
    a, b = _pair(uid)
    quest_id = _running(a, b, started_days_ago=1)
    _sql("UPDATE users SET last_seen=NULL WHERE telegram_id=?", (b,))
    _sql("DELETE FROM habit_completion_events WHERE user_id=?", (b,))
    assert _view_of(a)["partner_idle_days"] == 1 and _view_of(a)["can_leave"] is False
    _window(quest_id, start_offset=-3)
    assert _view_of(a)["partner_idle_days"] == 3 and _view_of(a)["can_leave"] is True


def test_the_helper_reads_both_signals_and_handles_unknown_people(uid):
    a, b = _pair(uid)
    conn = connect()
    try:
        assert partner_idle_days(conn, uid + 9999, since_day=None) is None
        assert partner_idle_days(conn, uid + 9999, since_day="2020-01-01") >= 1
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------ прежние правила не сломаны

def test_before_the_start_the_old_exit_still_works_and_shows_no_idle_data(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b, rules="habits")
    view = _view_of(a)
    assert view["phase"] == "scheduled" and view["can_cancel"] is True and view["can_leave"] is False and view["partner_idle_days"] is None
    assert cancel_pair_quest(b, quest_id)["ok"] is True and "left_early" not in cancel_pair_quest(b, quest_id).keys()


def test_strangers_and_finished_quests_cannot_be_left(uid):
    a, b = _pair(uid)
    stranger = _user(uid + 5)
    quest_id = _running(a, b)
    _seen(b, 100)
    _mark_at(b, 100)
    assert cancel_pair_quest(stranger, quest_id) == {"error": "not_found"}
    assert cancel_pair_quest(a, "oops") == {"error": "not_found"}
    assert cancel_pair_quest(a, quest_id)["ok"]
    assert cancel_pair_quest(a, quest_id) == {"error": "cannot_cancel"}          # уже закончено


def test_the_pure_view_defaults_to_no_exit():
    view = build_scenario("mid")["quest"]
    assert view["can_leave"] is False and view["partner_idle_days"] is None


def test_the_lab_shows_the_vanished_partner(uid):
    assert "partner_gone" in {s["key"] for s in lab_scenarios()}
    quest = build_scenario("partner_gone")["quest"]
    assert quest["can_leave"] is True and quest["partner_idle_days"] == 3 and quest["phase"] == "active"
    assert build_scenario("lost")["quest"]["can_leave"] is False


# ------------------------------------------------------------------------------------------------ HTTP

def _headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}", "Content-Type": "application/json"}


async def _flush():
    tasks = list(ws._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_leaving_over_http_tells_the_partner_and_records_the_event(client, uid):
    a, b = _pair(uid)
    quest_id = _running(a, b)
    _seen(b, 72)
    _mark_at(b, 72)
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())

    state = await (await client.get("/api/pair-quest", headers=_headers(a))).json()
    assert state["pair_quest"]["quest"]["can_leave"] is True
    reply = await client.post(f"/api/pair-quest/{quest_id}/cancel", headers=_headers(a))
    data = await reply.json()
    assert reply.status == 200 and data["left_early"] is True and data["pair_quest"]["quest"] is None
    await _flush()
    chat_id, text = bot.send_message.call_args.args[:2]
    assert chat_id == b and "Анна" in text and "3 дн." in text and "можно взять новое" in text
    conn = connect()
    try:
        row = conn.execute("SELECT meta FROM user_events WHERE user_id=? AND name='pair_left_early'", (a,)).fetchone()
    finally:
        conn.close()
    assert row and str(b) in row["meta"] and '"idle_days":3' in row["meta"]


async def test_leaving_a_living_partners_quest_is_refused_over_http(client, uid):
    a, b = _pair(uid)
    quest_id = _running(a, b)
    _seen(b, 1)
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    reply = await client.post(f"/api/pair-quest/{quest_id}/cancel", headers=_headers(a))
    assert reply.status == 400 and (await reply.json())["error"] == "cannot_cancel"
    await _flush()
    assert bot.send_message.await_count == 0 and _status(quest_id) == "active"


async def test_a_normal_cancel_before_the_start_does_not_claim_an_early_exit(client, uid):
    a, b = _pair(uid)
    quest_id = _start(a, b, rules="habits")
    reply = await client.post(f"/api/pair-quest/{quest_id}/cancel", headers=_headers(a))
    data = await reply.json()
    assert reply.status == 200 and data["left_early"] is False


# ------------------------------------------------------------------------------------------------ приложение

def test_the_card_offers_the_exit_next_to_the_reminder_and_explains_why():
    block = APP.split("function pairActionsHtml(q)")[1].split("function pairQuestBodyHtml")[0]
    assert "q.can_leave" in block and 'data-pair-act="cancel"' in block and 'data-leave="1"' in block and "pq-idle-note" in block
    assert "не заходит уже" in block and "pairDaysWord(idle)" in block and "data-confirm=" in block
    assert block.index("nudge") < block.index("leave") and "pq-actions" in block


def test_the_app_confirms_with_a_toast_and_explains_the_old_error_text():
    assert 'act === "cancel" && result.left_early' in APP and "теперь можно выбрать нового союзника" in APP
    assert "2 дня и больше не заходит" in APP


def test_the_css_is_light_and_keeps_the_admin_button_off_the_follow_pills():
    block = CSS[CSS.index("/* Парное задание: напарник пропал"):]
    assert ".pq-idle-note" in block and ".pq-actions .pq-leave" in block
    assert "filter" not in block and "animation" not in block
    assert re.search(r"\.player-card__admin-btn\{bottom:74px !important\}", block)                  # админская кнопка не налезает на «Подписки/Подписчики»
    assert ".player-card.has-admin-btn .xp-bar" in block and "margin-right:0" in block
