"""
Парное задание (db/pair_quests.py): двое друзей вместе набирают PAIR_GOAL дней
за неделю, награда — сундук каждому и очки «Заданий месяца».
"""
import asyncio
import itertools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import db.friends as friends_mod
import db.pair_quests as pq
import streak_scheduler as sched
import webapp.webapp_server as ws
from db import (
    add_user, get_user, get_month_quests, make_friends, remove_mutual, block_user, add_habit,
    get_pair_quest, list_pair_candidates, send_pair_invite, accept_pair_invite, decline_pair_invite,
    cancel_pair_quest, claim_pair_chest, pair_on_day_completed, settle_pair_quests,
    get_active_pair_quests, pair_last_day_reminder,
    PAIR_GOAL, PAIR_WINDOW_DAYS, PAIR_REWARD_COINS, PAIR_REWARD_DIAMONDS, PAIR_MONTH_POINTS,
)
from db.core import connect
from db.pair_quests import LEGACY_PAIR_GOAL
from db.streak import local_today
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
_uid_counter = itertools.count(720_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _one(query, params=()):
    conn = connect()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def _user(uid_, name="Игрок", habit=True):
    add_user(uid_, f"u{uid_}", name)
    if habit:
        add_habit(uid_, "Бег")
    return uid_


def _day(uid_, offset=0, status="completed"):
    day = str(local_today(uid_) + timedelta(days=offset))
    _sql(
        "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, ?, 1)",
        (uid_, day, status),
    )
    return day


def _pair(uid_, active=True):
    """Два друга с привычками; оба отмечались вчера (то есть «живые»)."""
    a, b = _user(uid_, "Анна"), _user(uid_ + 1, "Борис")
    assert make_friends(a, b)
    if active:
        _day(a, -1)
        _day(b, -1)
    return a, b


def _start(a, b, rules="days"):
    """Приглашение принято; возвращает id задания (окно ещё в будущем). По умолчанию это СТАРОЕ задание (rules='days',
    цель 10, вклад — дни с отметкой): на нём проверяются жизненный цикл и правила старых заданий; правила v2
    (привычки, потолок 3 в день, оба в день завершения) — tests/test_pair_habits.py."""
    invited = send_pair_invite(a, b)
    assert invited.get("ok"), invited
    accepted = accept_pair_invite(b, invited["quest_id"])
    assert accepted.get("ok"), accepted
    if rules == "days":
        _sql("UPDATE pair_quests SET rules='days', goal=? WHERE id=?", (LEGACY_PAIR_GOAL, invited["quest_id"]))
    return invited["quest_id"]


def _window(quest_id, start_offset=-2, length=PAIR_WINDOW_DAYS):
    """Сдвигает окно задания относительно «сегодня» — имитирует ход времени."""
    row = _one("SELECT inviter_id FROM pair_quests WHERE id=?", (quest_id,))
    today = local_today(row["inviter_id"])
    start = today + timedelta(days=start_offset)
    _sql(
        "UPDATE pair_quests SET start_day=?, end_day=? WHERE id=?",
        (str(start), str(start + timedelta(days=length - 1)), quest_id),
    )
    return start


def _fill(uid_, quest_id, n):
    """n отметок подряд внутри окна задания (с его первого дня)."""
    row = _one("SELECT start_day FROM pair_quests WHERE id=?", (quest_id,))
    start = datetime.strptime(row["start_day"], "%Y-%m-%d").date()
    for i in range(n):
        _sql(
            "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', 1)",
            (uid_, str(start + timedelta(days=i))),
        )


def _clean(*users):
    """Убирает отметки, которые фикстура _pair поставила на вчера (они попадают
    в окно, сдвинутое в прошлое, и искажают счёт)."""
    for u in users:
        _sql("DELETE FROM streak_days WHERE user_id=?", (u,))


def _status(quest_id):
    return _one("SELECT status FROM pair_quests WHERE id=?", (quest_id,))["status"]


def _complete(a, b):
    quest_id = _start(a, b)
    _window(quest_id)
    _fill(a, quest_id, 5)
    _fill(b, quest_id, 5)
    assert get_pair_quest(a)["quest"]["phase"] == "completed"
    return quest_id


# ---------------------------------------------------------------------------
# ПРИГЛАШЕНИЕ
# ---------------------------------------------------------------------------

def test_invite_creates_pending_quest(uid):
    a, b = _pair(uid)
    result = send_pair_invite(a, b)
    assert result["ok"] and _status(result["quest_id"]) == "pending"
    state = get_pair_quest(a)
    assert state["outgoing"]["to"]["telegram_id"] == b and state["quest"] is None
    assert state["can_choose"] is False                          # одно приглашение за раз
    assert [i["from"]["telegram_id"] for i in get_pair_quest(b)["incoming"]] == [a]
    assert get_pair_quest(b)["can_choose"] is True               # получателю это не мешает


@pytest.mark.parametrize("who, error", [("stranger", "not_friends"), ("self", "invalid_target"), ("junk", "invalid_target")])
def test_invite_rejects_bad_targets(uid, who, error):
    a, b = _pair(uid)
    stranger = _user(uid + 5)
    target = {"stranger": stranger, "self": a, "junk": "abc"}[who]
    assert send_pair_invite(a, target) == {"error": error}


def test_invite_needs_a_habit(uid):
    a, b = _pair(uid)
    _sql("DELETE FROM habits WHERE user_id=?", (a,))
    assert send_pair_invite(a, b) == {"error": "needs_habit"}
    assert get_pair_quest(a)["needs_habit"] is True


def test_invite_to_inactive_friend_is_refused(uid):
    a, b = _pair(uid)
    _sql("DELETE FROM streak_days WHERE user_id=?", (b,))
    assert send_pair_invite(a, b) == {"error": "partner_inactive"}
    _day(b, -pq.ACTIVE_FRIEND_DAYS - 2)                          # давно — всё ещё «неактивен»
    assert send_pair_invite(a, b) == {"error": "partner_inactive"}
    _day(b, -3)
    assert send_pair_invite(a, b)["ok"]


def test_second_invite_and_reverse_invite(uid):
    a, b = _pair(uid)
    c = _user(uid + 2)
    make_friends(a, c)
    _day(c, -1)
    first = send_pair_invite(a, b)
    assert send_pair_invite(a, c) == {"error": "already_invited"}
    assert send_pair_invite(b, a) == {"error": "invited_you", "quest_id": first["quest_id"]}


def test_cannot_invite_while_in_a_quest_or_invite_a_busy_friend(uid):
    a, b = _pair(uid)
    c = _user(uid + 2)
    make_friends(a, c)
    make_friends(b, c)
    _day(c, -1)
    _start(a, b)
    assert send_pair_invite(a, c) == {"error": "busy"}
    assert send_pair_invite(c, a) == {"error": "partner_busy"}
    assert send_pair_invite(c, b) == {"error": "partner_busy"}


def test_stale_invite_lapses(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    _sql("UPDATE pair_quests SET created_at=datetime('now', ?) WHERE id=?", (f"-{pq.INVITE_TTL_DAYS + 1} days", quest_id))
    assert accept_pair_invite(b, quest_id) == {"error": "not_pending"}
    assert _status(quest_id) == "expired"
    assert get_pair_quest(a)["can_choose"] is True and get_pair_quest(a)["recent_fail"] is None


# ---------------------------------------------------------------------------
# ПРИНЯТЬ / ОТКАЗАТЬ / ОТМЕНИТЬ
# ---------------------------------------------------------------------------

def test_accept_schedules_the_quest_for_tomorrow(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    row = _one("SELECT * FROM pair_quests WHERE id=?", (quest_id,))
    tomorrow = local_today(a) + timedelta(days=1)
    assert row["status"] == "active" and row["start_day"] == str(tomorrow)
    assert row["end_day"] == str(tomorrow + timedelta(days=PAIR_WINDOW_DAYS - 1))
    quest = get_pair_quest(b)["quest"]
    assert quest["phase"] == "scheduled" and quest["starts_in"] == 1 and quest["partner"]["telegram_id"] == a
    assert quest["can_cancel"] is True and len(quest["days"]) == PAIR_WINDOW_DAYS


def test_accept_cancels_other_pending_invites_of_both(uid):
    a, b = _pair(uid)
    c = _user(uid + 2)
    d = _user(uid + 3)
    for x in (c, d):
        make_friends(a, x)
        _day(x, -1)
    make_friends(b, d)
    invited_c = send_pair_invite(c, a)["quest_id"]
    invited_d = send_pair_invite(d, b)["quest_id"]
    _start(a, b)
    assert _status(invited_c) == "cancelled" and _status(invited_d) == "cancelled"


def test_accept_rules(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    stranger = _user(uid + 5)
    assert accept_pair_invite(a, quest_id) == {"error": "not_found"}          # приглашающий не может принять сам
    assert accept_pair_invite(stranger, quest_id) == {"error": "not_found"}
    assert accept_pair_invite(b, "x") == {"error": "not_found"}
    _sql("DELETE FROM habits WHERE user_id=?", (b,))
    assert accept_pair_invite(b, quest_id) == {"error": "needs_habit"}
    add_habit(b, "Чтение")
    assert accept_pair_invite(b, quest_id)["ok"]
    assert accept_pair_invite(b, quest_id) == {"error": "not_pending"}


def test_decline(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    assert decline_pair_invite(a, quest_id) == {"error": "not_found"}
    assert decline_pair_invite(b, quest_id) == {"ok": True}
    assert _status(quest_id) == "declined"
    assert get_pair_quest(a)["can_choose"] is True and get_pair_quest(a)["outgoing"] is None


def test_cancel_rules(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    assert cancel_pair_quest(b, quest_id) == {"error": "cannot_cancel"}        # получатель отказывается, а не отзывает
    assert cancel_pair_quest(a, quest_id)["ok"] and _status(quest_id) == "cancelled"

    started = _start(a, b)
    assert cancel_pair_quest(b, started)["ok"]                                  # до старта — можно любому
    again = _start(a, b)
    _window(again, start_offset=0)
    assert cancel_pair_quest(a, again) == {"error": "cannot_cancel"}            # начавшееся — нельзя
    assert cancel_pair_quest(_user(uid + 5), again) == {"error": "not_found"}


def test_unfriending_or_blocking_cancels_the_quest(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    remove_mutual(a, b)
    assert get_pair_quest(b)["quest"] is None and _status(quest_id) == "cancelled"

    c, d = _pair(uid + 10)
    second = _start(c, d)
    block_user(d, c)
    assert get_pair_quest(c)["quest"] is None and _status(second) == "cancelled"


# ---------------------------------------------------------------------------
# ПРОГРЕСС, ВЫПОЛНЕНИЕ, ПРОВАЛ
# ---------------------------------------------------------------------------

def test_progress_counts_days_of_both_inside_the_window(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)                                                  # окно: позавчера … +4 дня
    _fill(a, quest_id, 3)
    _fill(b, quest_id, 2)
    start = datetime.strptime(_one("SELECT start_day FROM pair_quests WHERE id=?", (quest_id,))["start_day"], "%Y-%m-%d").date()
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'freeze', 1)",
         (b, str(start + timedelta(days=4))))                          # заморозка — не вклад
    _day(b, -10)                                                       # вне окна — не считается
    quest = get_pair_quest(a)["quest"]
    assert quest["phase"] == "active"
    assert (quest["progress"], quest["mine"], quest["partner_count"]) == (5, 3, 2)
    assert quest["goal"] == LEGACY_PAIR_GOAL and quest["days_left"] == 5
    mine = [d["me"] for d in quest["days"]]
    assert mine[:3] == [True, True, True] and not any(mine[3:])
    assert [d["state"] for d in quest["days"]][:4] == ["past", "past", "today", "future"]
    assert quest["partner_state"] in ("done", "can_remind", "reminded", "unavailable")
    view_b = get_pair_quest(b)["quest"]
    assert (view_b["mine"], view_b["partner_count"]) == (2, 3)         # у напарника «я» и «он» меняются местами


def test_goal_reached_completes_at_once_and_clamps_progress(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)
    _fill(a, quest_id, 7)
    _fill(b, quest_id, 4)                                              # 11 > 10
    quest = get_pair_quest(b)["quest"]
    assert quest["phase"] == "completed" and quest["progress"] == LEGACY_PAIR_GOAL and quest["claimable"] is True
    assert _status(quest_id) == "completed"


def test_one_player_cannot_carry_alone(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id, start_offset=-6)
    _fill(a, quest_id, 7)
    assert get_pair_quest(a)["quest"]["phase"] == "active"             # 7 из 10, напарник молчит
    assert PAIR_WINDOW_DAYS < LEGACY_PAIR_GOAL


def test_window_end_closes_an_unfinished_quest(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id, start_offset=-8)
    _fill(a, quest_id, 4)
    _fill(b, quest_id, 3)
    state = get_pair_quest(a)
    assert state["quest"] is None and _status(quest_id) == "expired"
    assert state["recent_fail"]["partner"]["telegram_id"] == b and state["can_choose"] is True
    assert claim_pair_chest(a, quest_id) == {"error": "not_completed"}


def test_window_is_not_closed_while_the_partner_day_is_still_running(uid, monkeypatch):
    """Часовые пояса: задание закрывается, когда ОБА прожили последний день."""
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id, start_offset=-6)                                 # последний день — сегодня
    assert get_pair_quest(a)["quest"]["phase"] == "active"
    _window(quest_id, start_offset=-7)                                 # последний день — вчера
    monkeypatch.setattr(pq, "_local_today", lambda user: local_today(a) - timedelta(days=1) if user == b else local_today(a))
    assert get_pair_quest(a)["quest"]["phase"] == "active"             # у b вчера ещё идёт
    monkeypatch.undo()
    assert get_pair_quest(a)["quest"] is None


# ---------------------------------------------------------------------------
# СУНДУК
# ---------------------------------------------------------------------------

def test_claim_gives_reward_once_to_each_member(uid):
    a, b = _pair(uid)
    _sql("UPDATE users SET xp=0, diamonds=0 WHERE telegram_id IN (?, ?)", (a, b))
    quest_id = _complete(a, b)
    assert claim_pair_chest(a, quest_id) == {
        "ok": True, "coins": PAIR_REWARD_COINS, "diamonds": PAIR_REWARD_DIAMONDS, "month_points": PAIR_MONTH_POINTS,
    }
    assert claim_pair_chest(a, quest_id) == {"error": "already_claimed"}
    assert (get_user(a)["xp"], get_user(a)["diamonds"]) == (PAIR_REWARD_COINS, PAIR_REWARD_DIAMONDS)
    assert get_user(b)["xp"] == 0                                      # у напарника свой сундук
    assert claim_pair_chest(b, quest_id)["ok"] and get_user(b)["diamonds"] == PAIR_REWARD_DIAMONDS
    assert claim_pair_chest(_user(uid + 5), quest_id) == {"error": "not_found"}
    assert claim_pair_chest(a, "x") == {"error": "not_found"}


def test_concurrent_claims_pay_once(uid):
    a, b = _pair(uid)
    _sql("UPDATE users SET xp=0, diamonds=0 WHERE telegram_id=?", (a,))
    quest_id = _complete(a, b)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: claim_pair_chest(a, quest_id), range(8)))
    assert sum(1 for r in results if r.get("ok")) == 1
    assert get_user(a)["diamonds"] == PAIR_REWARD_DIAMONDS


def test_claimed_pair_quest_adds_month_points(uid):
    a, b = _pair(uid)
    before = get_month_quests(a)["points"]
    quest_id = _complete(a, b)
    assert get_month_quests(a)["points"] == before                     # пока не забрал — очков нет
    claim_pair_chest(a, quest_id)
    assert get_month_quests(a)["points"] == before + PAIR_MONTH_POINTS
    assert get_month_quests(b)["points"] == before                     # у напарника — после его сундука


def test_next_quest_waits_for_chest_and_window_end(uid):
    a, b = _pair(uid)
    quest_id = _complete(a, b)                                          # окно ещё идёт (до +4 дня)
    assert get_pair_quest(a)["can_choose"] is False
    claim_pair_chest(a, quest_id)
    state = get_pair_quest(a)
    assert state["can_choose"] is False and state["quest"]["claimed"] is True
    assert state["choose_from_day"] == str(local_today(a) + timedelta(days=5))
    assert send_pair_invite(a, b) == {"error": "busy"}
    _window(quest_id, start_offset=-10)                                 # окно давно закончилось
    state = get_pair_quest(a)
    assert state["can_choose"] is True and state["quest"] is None and state["completed_total"] == 1
    assert get_pair_quest(b)["quest"]["claimable"] is True              # у напарника сундук ещё ждёт
    assert get_pair_quest(b)["can_choose"] is False


# ---------------------------------------------------------------------------
# КАНДИДАТЫ
# ---------------------------------------------------------------------------

def test_candidates_list_marks_busy_and_inactive_friends(uid):
    a, b = _pair(uid)
    c, d = _pair(uid + 10)
    e = _user(uid + 20)
    for x in (b, c, d, e):
        make_friends(a, x)
    _day(e, -1)
    _start(c, d)                                                        # c и d заняты
    _sql("DELETE FROM streak_days WHERE user_id=?", (b,))                # b давно не отмечался
    data = list_pair_candidates(a)
    by_id = {f["telegram_id"]: f for f in data["friends"]}
    assert by_id[e]["available"] is True and by_id[e]["reason"] is None
    assert by_id[b]["reason"] == "inactive" and by_id[c]["reason"] == "busy" and by_id[d]["reason"] == "busy"
    assert [f["telegram_id"] for f in data["friends"]][0] == e           # свободные — сверху
    assert data["can_choose"] is True and data["goal"] == PAIR_GOAL


# ---------------------------------------------------------------------------
# ПУШ-СОБЫТИЯ
# ---------------------------------------------------------------------------

def test_partner_done_event_is_sent_once_a_day(uid, monkeypatch):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: True)
    _day(a, 0)
    events = pair_on_day_completed(a)
    assert len(events) == 1 and events[0]["type"] == "partner_done"
    assert (events[0]["to"], events[0]["from"], events[0]["goal"]) == (b, a, LEGACY_PAIR_GOAL)
    assert pair_on_day_completed(a) == []                              # повторно в тот же день — нет


def test_no_partner_event_when_partner_done_or_unwilling_or_not_started(uid, monkeypatch):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _day(a, 0)
    assert pair_on_day_completed(a) == []                              # окно ещё не началось
    _window(quest_id)
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: False)
    assert pair_on_day_completed(a) == []                              # напарник отключил напоминания
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: True)
    _day(b, 0)
    assert pair_on_day_completed(a) == []                              # он уже отметился
    assert pair_on_day_completed(_user(uid + 5)) == []                 # у человека нет задания


def test_completed_event_fires_exactly_once(uid, monkeypatch):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: True)
    _fill(b, quest_id, 5)
    _fill(a, quest_id, 5)
    _day(a, 0)
    events = pair_on_day_completed(a)
    assert [e["type"] for e in events] == ["completed"] and sorted(events[0]["users"]) == sorted([a, b])
    assert pair_on_day_completed(a) == [] and settle_pair_quests() == []


def test_settle_active_quests_reports_completion_from_other_channels(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)
    _fill(a, quest_id, 5)
    _fill(b, quest_id, 5)
    events = settle_pair_quests()
    assert [e["quest_id"] for e in events if e["quest_id"] == quest_id] == [quest_id]
    assert settle_pair_quests() == [] and quest_id not in [q["id"] for q in get_active_pair_quests()]


def test_last_day_reminder_only_when_goal_is_still_reachable(uid):
    a, b = _pair(uid)
    quest = _start(a, b)
    _window(quest, start_offset=-6)                                    # сегодня — последний день
    _clean(a, b)
    _fill(a, quest, 6)
    _fill(b, quest, 3)                                                  # 9 из 10: не хватает одного дня
    row = dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest,)))
    info = pair_last_day_reminder(row, b)
    assert info == {"left": 1, "partner_name": "Анна"}
    _day(b, 0)                                                         # b сегодня отметился — напоминать нечего
    assert pair_last_day_reminder(row, b) is None
    _sql("DELETE FROM streak_days WHERE user_id=? AND day>=?", (a, row["start_day"]))
    assert pair_last_day_reminder(row, b) is None                       # цель уже не достать: далеко
    _window(quest, start_offset=-3)
    row = dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest,)))
    assert pair_last_day_reminder(row, b) is None                       # ещё не последний день


# ---------------------------------------------------------------------------
# ПЛАНИРОВЩИК
# ---------------------------------------------------------------------------

class _EighteenOclock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz).replace(hour=18, minute=0, second=0, microsecond=0)


async def test_scheduler_sends_completion_and_last_day_pushes(uid, monkeypatch):
    a, b = _pair(uid)
    c, d = _pair(uid + 10)
    done = _start(a, b)
    _window(done)
    _fill(a, done, 5)
    _fill(b, done, 5)                                                    # выполнено «через бота»
    last = _start(c, d)
    _window(last, start_offset=-6)
    _clean(c, d)
    _fill(c, last, 6)
    _fill(d, last, 3)                                                    # d не хватает дня и сегодня он не отмечался
    monkeypatch.setattr(sched, "datetime", _EighteenOclock)
    bot = SimpleNamespace(send_message=AsyncMock(), token="")
    await sched.run_pair_quest_notifications(bot)
    sent = {(call.args[0], call.args[1]) for call in bot.send_message.call_args_list}
    texts = {chat: text for chat, text in sent}
    assert "Парное задание выполнено" in texts[a] and "Парное задание выполнено" in texts[b]
    assert "Последний день" in texts[d] and "Анна" in texts[d]
    assert "Последний день" in texts[c] and "Борис" in texts[c]          # оба, кто сегодня ещё не отмечался
    bot.send_message.reset_mock()
    await sched.run_pair_quest_notifications(bot)
    assert not [1 for call in bot.send_message.call_args_list if call.args[0] in (c, d)]   # повторно не шлём


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def _flush_background():
    tasks = list(ws._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_state_and_candidates_routes(client, uid):
    a, b = _pair(uid)
    state = await (await client.get("/api/bootstrap", headers=_headers(a))).json()
    assert state["pair_quest"]["can_choose"] is True and state["pair_quest"]["goal"] == PAIR_GOAL
    data = await (await client.get("/api/pair-quest/candidates", headers=_headers(a))).json()
    assert [f["telegram_id"] for f in data["friends"]] == [b] and data["friends"][0]["available"] is True
    assert (await client.get("/api/pair-quest", headers=_headers(a))).status == 200
    assert (await client.get("/api/pair-quest")).status == 401


async def test_invite_accept_claim_flow_over_http(client, uid):
    a, b = _pair(uid)
    _sql("UPDATE users SET xp=0, diamonds=0 WHERE telegram_id=?", (a,))
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())

    r = await client.post("/api/pair-quest/invite", json={"friend_id": b}, headers=_headers(a))
    assert r.status == 200 and (await r.json())["pair_quest"]["outgoing"]["to"]["telegram_id"] == b
    await _flush_background()
    (chat_id, text), _ = bot.send_message.call_args
    assert chat_id == b and "Анна" in text and "парное задание" in text

    quest_id = (await (await client.get("/api/pair-quest", headers=_headers(b))).json())["pair_quest"]["incoming"][0]["id"]
    bot.send_message.reset_mock()
    r = await client.post(f"/api/pair-quest/{quest_id}/accept", headers=_headers(b))
    assert r.status == 200 and (await r.json())["pair_quest"]["quest"]["phase"] == "scheduled"
    await _flush_background()
    assert bot.send_message.call_args.args[0] == a and "Борис" in bot.send_message.call_args.args[1]

    _sql("UPDATE pair_quests SET rules='days', goal=? WHERE id=?", (LEGACY_PAIR_GOAL, quest_id))   # маршруты те же; здесь — старый счёт
    _window(quest_id)
    _fill(a, quest_id, 5)
    _fill(b, quest_id, 5)
    r = await client.post(f"/api/pair-quest/{quest_id}/claim", headers=_headers(a))
    data = await r.json()
    assert r.status == 200 and data["reward"] == {
        "coins": PAIR_REWARD_COINS, "diamonds": PAIR_REWARD_DIAMONDS, "month_points": PAIR_MONTH_POINTS,
    }
    assert data["user"]["diamonds"] == PAIR_REWARD_DIAMONDS and data["pair_quest"]["quest"]["claimed"] is True
    assert data["month_quests"]["points"] >= PAIR_MONTH_POINTS
    again = await client.post(f"/api/pair-quest/{quest_id}/claim", headers=_headers(a))
    assert again.status == 400 and (await again.json())["error"] == "already_claimed"


async def test_route_errors(client, uid):
    a, b = _pair(uid)
    stranger = _user(uid + 5)
    r = await client.post("/api/pair-quest/invite", json={"friend_id": stranger}, headers=_headers(a))
    assert r.status == 400 and (await r.json())["error"] == "not_friends"
    r = await client.post("/api/pair-quest/invite", data="{", headers=_headers(a))
    assert r.status == 400 and (await r.json())["error"] == "invalid_json"
    r = await client.post("/api/pair-quest/999999/accept", headers=_headers(a))
    assert r.status == 404
    first = await client.post("/api/pair-quest/invite", json={"friend_id": b}, headers=_headers(a))
    assert first.status == 200
    r = await client.post("/api/pair-quest/invite", json={"friend_id": a}, headers=_headers(b))
    body = await r.json()
    assert r.status == 400 and body["error"] == "invited_you" and body["quest_id"]
    quest_id = body["quest_id"]
    assert (await client.post(f"/api/pair-quest/{quest_id}/decline", headers=_headers(b))).status == 200
    assert (await client.post(f"/api/pair-quest/{quest_id}/cancel", headers=_headers(a))).status == 400


async def test_invite_push_respects_opt_out(client, uid):
    a, b = _pair(uid)
    _sql("UPDATE settings SET reminders=0 WHERE user_id=?", (b,))
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    r = await client.post("/api/pair-quest/invite", json={"friend_id": b}, headers=_headers(a))
    assert r.status == 200
    await _flush_background()
    bot.send_message.assert_not_called()


async def test_marking_a_habit_returns_pair_progress_and_pings_the_partner(client, uid, monkeypatch):
    a, b = _pair(uid)
    quest_id = _start(a, b)
    _window(quest_id)
    _sql("DELETE FROM streak_days WHERE user_id IN (?, ?) AND day=?", (a, b, str(local_today(a))))
    monkeypatch.setattr(friends_mod, "_can_receive_nudge", lambda row: True)
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    habit_id = _one("SELECT id FROM habits WHERE user_id=?", (a,))["id"]
    r = await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(a))
    assert r.status == 200
    data = await r.json()
    assert data["pair_quest"]["quest"]["mine"] >= 1 and data["pair_quest"]["quest"]["phase"] == "active"
    await _flush_background()
    pushed = [(call.args[0], call.args[1]) for call in bot.send_message.call_args_list if call.args[0] == b]
    assert pushed and "Анна" in pushed[0][1] and "Твой ход" in pushed[0][1]


# ---------------------------------------------------------------------------
# ФРОНТЕНД: разметка и подключение
# ---------------------------------------------------------------------------

def test_markup_and_script_wiring():
    html_text = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert 'id="pairQuestCard"' in html_text and 'id="pairSheet"' in html_text
    for route in ("/api/pair-quest/candidates", "/api/pair-quest/invite", "/api/pair-quest/${id}/${endpoint}"):
        assert route in js
    assert 'accept: "accept", decline: "decline", cancel: "cancel", claim: "claim"' in js
    assert "function renderPairCard" in js and "pair_quest" in js
    assert ".pair-card" in css and ".pair-sheet" in css


def test_user_without_any_quest_gets_the_default_state_cheaply(uid):
    me = _user(uid)
    state = get_pair_quest(me)
    assert state["quest"] is None and state["outgoing"] is None and state["incoming"] == []
    assert state["can_choose"] is True and state["completed_total"] == 0 and state["needs_habit"] is False
    assert set(state) == set(get_pair_quest(_pair(uid + 10)[0]))      # те же поля, что и у человека с заданиями
