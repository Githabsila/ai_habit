"""
Парное задание после завершения (как в Duolingo): рекомендации самых активных друзей в окне выбора
союзника, пуши «начни новое задание», понятные «дни до конца / очков ещё нужно» и новая раскладка в Mini App.
"""
import itertools
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import streak_scheduler as sched
from db import (
    claim_pair_chest, get_pair_quest, list_pair_candidates, make_friends, send_pair_invite, accept_pair_invite,
    pair_users_due_for_new_quest,
)
from db.pair_quests import MAX_RECOMMENDED, RESTART_NUDGE_DAYS
from tests.test_pair_quests import _day, _fill, _one, _sql, _start, _user, _window

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")

_counter = itertools.count(730_000_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


class _Noon(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)


class _Evening(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=18, minute=0, second=0, microsecond=0)


def _marks(user, days):
    """Друг отмечался `days` дней подряд, начиная со вчера."""
    for i in range(1, days + 1):
        _day(user, -i)


def _friend(owner, offset, name, days):
    friend = _user(owner + offset, name)
    assert make_friends(owner, friend)
    _marks(friend, days)
    return friend


def _finished_quest(a, b, *, ended_days_ago=1, claim=True):
    """Выполненное задание a+b, окно закончилось `ended_days_ago` дней назад."""
    _day(a, -1)
    _day(b, -1)                                 # оба «живые» — иначе приглашение не уйдёт
    quest_id = _start(a, b)
    _window(quest_id, start_offset=-(6 + ended_days_ago))
    _fill(a, quest_id, 5)
    _fill(b, quest_id, 5)
    assert get_pair_quest(a)["quest"]["phase"] == "completed"
    if claim:
        assert claim_pair_chest(a, quest_id).get("ok") and claim_pair_chest(b, quest_id).get("ok")
    return quest_id


# --- рекомендации -----------------------------------------------------------------

def test_candidates_recommend_the_most_active_and_the_previous_partner_goes_last(uid):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _marks(a, 1)
    _marks(b, 6)
    _finished_quest(a, b, ended_days_ago=2)
    d = _friend(a, 3, "Дима", 5)
    c = _friend(a, 2, "Вера", 3)
    e = _friend(a, 4, "Егор", 1)
    f = _friend(a, 5, "Фёкла", 0)

    result = list_pair_candidates(a)
    by_name = {x["first_name"]: x for x in result["friends"]}
    assert [x["first_name"] for x in result["friends"] if x["recommended"]] == ["Дима", "Вера", "Егор"][:MAX_RECOMMENDED]
    assert by_name["Дима"]["active_days"] == 5 and by_name["Вера"]["active_days"] == 3
    assert by_name["Борис"]["was_partner"] is True and by_name["Борис"]["recommended"] is False, "прежний напарник — не первым"
    assert by_name["Фёкла"]["recommended"] is False and by_name["Фёкла"]["active_days"] == 0
    assert result["active_window"] == 7
    assert [x["first_name"] for x in result["friends"]][:3] == ["Дима", "Вера", "Егор"], "рекомендованные — сверху"
    assert {d, c, e, f}


def test_the_only_friend_is_recommended_even_if_he_was_the_partner(uid):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _marks(a, 2)
    _marks(b, 4)
    _finished_quest(a, b, ended_days_ago=2)
    friend = list_pair_candidates(a)["friends"][0]
    assert friend["recommended"] is True and friend["was_partner"] is True


def test_busy_and_inactive_friends_are_never_recommended(uid):
    a = _user(uid, "Анна")
    _marks(a, 1)
    _friend(a, 1, "Давно", 0)
    busy_pair = _friend(a, 2, "Занят", 6)
    other = _user(uid + 50, "Другой")
    assert make_friends(busy_pair, other)
    _marks(other, 2)
    _start(busy_pair, other)
    names = {x["first_name"]: x for x in list_pair_candidates(a)["friends"]}
    assert names["Занят"]["reason"] == "busy" and names["Занят"]["recommended"] is False
    assert names["Давно"]["reason"] == "inactive" and names["Давно"]["recommended"] is False


# --- кому пора предложить новое задание -------------------------------------------------

def test_users_due_for_a_new_quest(uid):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    quest_id = _finished_quest(a, b, ended_days_ago=1)
    due = {item["user_id"]: item for item in pair_users_due_for_new_quest()}
    assert due[a]["quest_id"] == due[b]["quest_id"] == quest_id and due[a]["status"] == "completed"


def test_unopened_chest_comes_first(uid):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _finished_quest(a, b, ended_days_ago=1, claim=False)
    assert a not in {item["user_id"] for item in pair_users_due_for_new_quest()}


def test_expired_quest_counts_and_old_ones_do_not(uid):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _day(a, -1)
    _day(b, -1)
    quest_id = _start(a, b)
    _window(quest_id, start_offset=-7)                       # цель не набрана, окно закончилось вчера
    assert get_pair_quest(a)["quest"] is None
    assert a in {item["user_id"] for item in pair_users_due_for_new_quest()}
    _sql("UPDATE pair_quests SET end_day=? WHERE id=?",
         (str(datetime.now().date() - timedelta(days=RESTART_NUDGE_DAYS + 20)), quest_id))
    assert a not in {item["user_id"] for item in pair_users_due_for_new_quest()}


# --- пуши -----------------------------------------------------------------------------------

def test_restart_text_names_the_most_active_friends_and_escapes_html():
    text = sched.pair_restart_text("pairnew1", [{"first_name": "Аня<b>", "active_days": 6}, {"first_name": "Боб", "active_days": 5}])
    assert "Новое парное задание" in text and "Аня&lt;b&gt;" in text and "6 из 7 дней" in text and "Боб" in text
    assert "ждёт напарника" in sched.pair_restart_text("pairnew2", [{"first_name": "Боб", "active_days": 5}])
    assert sched.pair_restart_text("pairnew1", []) is None


async def test_scheduler_invites_to_a_new_quest_next_day_at_noon_and_again_on_day_four(uid, monkeypatch):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _marks(a, 1)
    friend = _friend(a, 2, "Дима", 5)
    quest_id = _finished_quest(a, b, ended_days_ago=1)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")

    monkeypatch.setattr(sched, "datetime", _Evening)
    await sched.run_pair_quest_notifications(bot)
    assert not [c for c in bot.send_message.call_args_list if c.args[0] == a], "в 18:00 первого дня — рано"

    monkeypatch.setattr(sched, "datetime", _Noon)
    await sched.run_pair_quest_notifications(bot)
    to_a = [c.args[1] for c in bot.send_message.call_args_list if c.args[0] == a]
    assert len(to_a) == 1 and "Новое парное задание" in to_a[0] and "Дима" in to_a[0]
    await sched.run_pair_quest_notifications(bot)
    assert len([c for c in bot.send_message.call_args_list if c.args[0] == a]) == 1, "то же окно — не повторяем"

    _sql("UPDATE pair_quests SET end_day=? WHERE id=?", (str(datetime.now().date() - timedelta(days=4)), quest_id))
    monkeypatch.setattr(sched, "datetime", _Evening)
    await sched.run_pair_quest_notifications(bot)
    second = [c.args[1] for c in bot.send_message.call_args_list if c.args[0] == a]
    assert len(second) == 2 and "ждёт напарника" in second[1]
    assert friend


async def test_no_invite_while_a_new_quest_or_invitation_is_open_or_without_friends_to_suggest(uid, monkeypatch):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _marks(a, 1)
    friend = _friend(a, 2, "Дима", 5)
    _finished_quest(a, b, ended_days_ago=1)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    monkeypatch.setattr(sched, "datetime", _Noon)

    invited = send_pair_invite(a, friend)                     # уже позвал нового напарника
    assert invited.get("ok")
    await sched.run_pair_quest_notifications(bot)
    assert not [c for c in bot.send_message.call_args_list if c.args[0] == a]
    assert accept_pair_invite(friend, invited["quest_id"]).get("ok")


async def test_no_invite_if_reminders_are_off(uid, monkeypatch):
    a, b = _user(uid, "Анна"), _user(uid + 1, "Борис")
    assert make_friends(a, b)
    _marks(a, 1)
    _friend(a, 2, "Дима", 5)
    _finished_quest(a, b, ended_days_ago=1)
    _sql("UPDATE settings SET reminders=0 WHERE user_id=?", (a,))
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    monkeypatch.setattr(sched, "datetime", _Noon)
    await sched.run_pair_quest_notifications(bot)
    assert not [c for c in bot.send_message.call_args_list if c.args[0] == a]
    assert _one("SELECT 1 FROM users WHERE telegram_id=?", (a,))


# --- Mini App --------------------------------------------------------------------------------

def test_active_card_compares_two_numbers_in_the_same_unit():
    """«7 дней до конца» рядом с «8 очков ещё» — разные единицы: непонятно, хватит ли дней. Теперь обе
    плашки в очках: сколько нужно набрать и сколько ещё можно набрать до конца."""
    body = APP_JS[APP_JS.index("function pairQuestBodyHtml("):][:4200]
    assert '<div class="pair-facts">' in body and "нужно набрать" in body and "можно набрать до конца" in body
    assert "до конца задания" not in body and "ещё набрать вместе" not in body
    assert "вместе нужно ещё" not in body, "старая фраза «Осталось 7 дней, вместе нужно ещё 9» путала"
    assert "из ${q.goal} очков" in body and "pairPointsWord(q.mine)" in body
    assert "pairPaceHint(q, pace)" in body


def test_pace_counts_what_can_still_be_earned_and_names_the_reserve():
    pace = APP_JS[APP_JS.index("function pairPace("):APP_JS.index("function pairDateLabel(")]
    assert 'd.state === "future") can += 2' in pace
    assert 'd.state === "today") can += (d.me ? 0 : 1) + (d.partner ? 0 : 1)' in pace
    assert "cap: (q.days || []).length * 2" in pace
    assert "Вдвоём за ${q.days.length}" in pace and "до ${pace.cap} очков, цель — ${q.goal}" in pace
    assert "Запас сейчас" in pace and "Запаса не осталось" in pace and "Цель уже не набрать" in pace


def test_goal_is_always_shown_next_to_the_maximum_possible():
    """Цель 10 из 14: 7 дней × 2 человека — максимум, 4 очка — запас на пропуски. Об этом говорим везде, где есть цель."""
    assert "из ${2 * (pq.window_days || 7)} возможных" in APP_JS
    assert "из ${2 * pairCandidates.window_days} возможных" in APP_JS


def test_pair_pushes_count_points_not_days():
    src = (ROOT / "streak_scheduler.py").read_text(encoding="utf-8")
    assert "вы набрали {PAIR_GOAL} очков" in src and "вы набрали {PAIR_GOAL} дней" not in src
    assert "plural_ru(left, 'очка', 'очков', 'очков')" in src and "'дня' if left == 1" not in src


def test_home_card_collapses_by_default_and_toggles_with_chevrons():
    assert "let pairHomeExpanded = false;" in APP_JS
    collapsed = APP_JS[APP_JS.index("function pairCollapsedHtml("):][:1800]
    assert "осталось" in APP_JS[APP_JS.index("function pairSummary("):][:2600]
    assert "PAIR_CHEVRON_DOWN" in collapsed and 'aria-expanded="false"' in collapsed and "pair-chip--${s.tone}" in collapsed
    assert "PAIR_CHEVRON_UP" in APP_JS[APP_JS.index("function renderPairCardInto("):][:3600]
    assert "togglePairHome()" in APP_JS[APP_JS.index("function initPairQuest()"):][:900]


def test_today_status_glows_only_while_it_is_the_users_turn():
    summary = APP_JS[APP_JS.index("function pairSummary("):][:2600]
    assert '"⚡ Твой ход", tone: "glow"' in summary
    assert '"🔥 Оба сегодня", tone: "ok"' in summary and '"✓ Ты отметился", tone: "ok"' in summary
    assert '"🎁 Сундук ждёт", tone: "glow"' in summary
    glow = CSS[CSS.index(".pair-chip--glow::after"):][:300]
    assert "animation:pairChipGlow" in glow
    frames = CSS[CSS.index("@keyframes pairChipGlow"):][:140]
    assert "transform" in frames and "filter" not in frames


def test_rating_tab_has_the_full_card_right_after_the_league_and_friends_go_before_the_team_challenge():
    rating = INDEX[INDEX.index('data-tab="rating"'):][:3600]
    order = [rating.index(m) for m in ('class="rating-hero"', 'id="pairQuestCardRating"', 'id="friendsCard"', 'id="teamCard"',
                                       'id="ratingScopeSwitch"')]
    assert order == sorted(order), order
    assert "renderPairCardInto(document.getElementById(\"pairQuestCardRating\"), false)" in APP_JS


def test_quests_modal_has_the_short_version_after_the_month_card():
    modal = INDEX[INDEX.index('id="dailyQuestsOverlay"'):][:3200]
    assert modal.index('id="monthCard"') < modal.index('id="pairQuestMini"')
    assert "function renderPairMini()" in APP_JS and "renderPairMini();" in APP_JS[APP_JS.index("function renderPairCard()"):][:300]


def test_choose_sheet_shows_recommended_friends_first():
    sheet = APP_JS[APP_JS.index("function renderPairSheet()"):][:5200]
    assert "⭐ Рекомендуем — самые активные" in sheet and "Остальные друзья" in sheet
    assert "отмечался ${Number(f.active_days)} из" in sheet and "уже были вместе" in sheet
