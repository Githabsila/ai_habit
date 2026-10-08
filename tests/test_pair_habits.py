"""
Парное задание, правила v2 (по просьбе владельца, 08.10): вклад — закрытые привычки, 20 на двоих за 7 дней, с человека
в день не больше 3, цель закрывается только днём, когда отметились ОБА (последняя привычка ждёт напарника).
Старые задания (rules='days') — tests/test_pair_quests.py.
"""
import itertools
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from db import (
    add_habit, claim_pair_chest, get_habits, get_pair_quest, pair_last_day_reminder, pair_on_day_completed,
    settle_pair_quests,
)
from db.pair_quests import (
    LEGACY_PAIR_GOAL, PAIR_DAY_CAP, PAIR_GOAL, PAIR_WINDOW_DAYS, RULES_DAYS, RULES_HABITS, compute_progress,
)
from db.streak import local_today, set_timezone
from tests.conftest import sign_init_data
from tests.test_pair_quests import _clean, _one, _pair, _sql, _start, _status, _user, _window

DAYS = [f"2026-10-{d:02d}" for d in range(1, 8)]            # окно из семи дней
_habit_ids = itertools.count(50_000_000)
_uid_counter = itertools.count(750_000_000, 100)           # у каждого теста свой «пакет» id: пара использует uid и uid+1


@pytest.fixture
def uid():
    return next(_uid_counter)


def _counts(per_day):
    return {DAYS[i]: n for i, n in enumerate(per_day)}


def _progress(a, b, goal=PAIR_GOAL):
    return compute_progress(RULES_HABITS, goal, DAYS, _counts(a), _counts(b))


# ---------------------------------------------------------------------------
# ПРАВИЛА (чистая функция)
# ---------------------------------------------------------------------------

def test_constants_match_the_agreed_rules():
    assert (PAIR_GOAL, PAIR_WINDOW_DAYS, PAIR_DAY_CAP, LEGACY_PAIR_GOAL) == (20, 7, 3, 10)
    assert 2 * PAIR_DAY_CAP * 3 < PAIR_GOAL <= 2 * PAIR_DAY_CAP * 4, "за три дня максимум 18 — цель закрывается не раньше 4-го"


def test_at_most_three_habits_per_person_per_day_count():
    calc = _progress([5, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 0])
    assert [(r["a"], r["b"]) for r in calc["rows"]][0] == (3, 1)
    assert calc["total"] == 4


def test_active_pair_finishes_on_the_fourth_day_with_two_habits_left():
    calc = _progress([3, 3, 3, 1, 0, 0, 0], [3, 3, 3, 1, 0, 0, 0])
    assert calc["total"] == 20 and calc["done_day"] == DAYS[3]
    assert [r["a"] + r["b"] for r in calc["rows"][:4]] == [6, 6, 6, 2], "6 + 6 + 6 + 2: на четвёртый день хватает по одной с каждого"


def test_three_left_and_one_friend_closes_three_in_the_morning_only_two_count():
    # 6 + 6 + 5 = 17 → осталось 3. Утром A закрыл три, B пока ничего: зачтутся 2 из 3, последняя ждёт B.
    calc = _progress([3, 3, 2, 3], [3, 3, 3, 0])
    assert calc["total"] == 19 and calc["done_day"] is None
    assert calc["held_by_day"] == {DAYS[3]: 1}
    # B закрыл одну привычку в тот же день — цель набрана
    done = _progress([3, 3, 2, 3], [3, 3, 3, 1])
    assert done["total"] == 20 and done["done_day"] == DAYS[3] and done["held_by_day"] == {}


def test_two_left_only_one_habit_per_person_is_needed():
    calc = _progress([3, 3, 3, 2], [3, 3, 3, 0])                 # осталось 2, A закрыл 2, B ничего
    assert calc["total"] == 19 and calc["held_by_day"] == {DAYS[3]: 1}
    assert _progress([3, 3, 3, 2], [3, 3, 3, 1])["total"] == 20, "B закрыл одну — достаточно"
    # хоть пять привычек у одного (потолок 3) — без напарника цель не закрывается
    lonely = _progress([3, 3, 3, 5], [3, 3, 3, 0])
    assert lonely["total"] == 19 and lonely["held_by_day"] == {DAYS[3]: 2}


def test_one_left_needs_both_friends_the_same_day():
    # день 4: у A три, цель ждёт; день 5: снова только A; день 6: только B; день 7: оба — вот тогда закрывается
    calc = _progress([3, 3, 3, 3, 3, 0, 1], [3, 3, 3, 0, 0, 1, 1])
    assert calc["done_day"] == DAYS[6] and calc["total"] == 20
    only_one_each_day = _progress([3, 3, 3, 3, 3, 0, 0], [3, 3, 3, 0, 0, 1, 0])
    assert only_one_each_day["total"] == 19 and only_one_each_day["done_day"] is None


def test_nothing_is_counted_after_the_goal_is_reached():
    calc = _progress([3, 3, 3, 1, 3, 3, 3], [3, 3, 3, 1, 3, 3, 3])
    assert calc["total"] == 20 and calc["done_day"] == DAYS[3]
    assert calc["rows"][6] == {"day": DAYS[6], "a": 3, "b": 3}, "сами отметки на экране остаются, но в счёт не идут"


def test_minimum_pace_for_an_inactive_pair_is_two_plus_one_every_day():
    assert _progress([2] * 7, [1] * 7)["done_day"] == DAYS[6], "(2 + 1) × 7 = 21 ≥ 20"
    assert _progress([1] * 7, [1] * 7)["total"] == 14, "по одной на двоих в день — этого мало"
    assert _progress([3] * 7, [0] * 7)["total"] == 19, "один человек в одиночку задание не закроет никогда"


def test_a_day_without_one_of_the_friends_still_adds_the_other_ones_habits_until_the_finish():
    calc = _progress([3, 0, 3, 0, 0, 0, 0], [0, 3, 0, 0, 0, 0, 0])
    assert calc["total"] == 9, "пока до цели далеко, привычки считаются сразу, а не ждут напарника"


def test_legacy_rules_are_plain_sum_of_days():
    calc = compute_progress(RULES_DAYS, LEGACY_PAIR_GOAL, DAYS, {d: 1 for d in DAYS[:5]}, {d: 1 for d in DAYS[:5]})
    assert calc["total"] == 10 and calc["done_day"] == DAYS[4] and calc["cap"] == 1


# ---------------------------------------------------------------------------
# С БАЗОЙ: журнал выполнений -> счёт
# ---------------------------------------------------------------------------

def _event(user_id, day, habit_id=None, hour=12, tz=None):
    """Привычка закрыта в `hour`:00 по локальному времени человека в день `day` (дата-строка)."""
    zone = ZoneInfo(tz or "UTC")
    local = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), time(hour, 0), tzinfo=zone)
    stamp = local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?, ?, ?)",
         (user_id, habit_id or next(_habit_ids), stamp))


def _mark(user_id, quest_id, per_day):
    """per_day[i] привычек в i-й день окна."""
    start = datetime.strptime(_one("SELECT start_day FROM pair_quests WHERE id=?", (quest_id,))["start_day"], "%Y-%m-%d").date()
    for i, n in enumerate(per_day):
        for _ in range(n):
            _event(user_id, str(start + timedelta(days=i)))


def _quest(uid_):
    a, b = _pair(uid_)
    quest_id = _start(a, b, rules="habits")
    _window(quest_id)                                       # окно: начато 2 дня назад, сегодня — третий день
    _clean(a, b)
    return a, b, quest_id


def test_new_quests_use_the_habit_rules(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b, rules="habits")
    row = _one("SELECT rules, goal FROM pair_quests WHERE id=?", (quest_id,))
    assert (row["rules"], row["goal"]) == (RULES_HABITS, PAIR_GOAL)
    quest = get_pair_quest(a)["quest"]
    assert quest["rules"] == RULES_HABITS and quest["cap"] == PAIR_DAY_CAP and quest["goal"] == PAIR_GOAL
    assert get_pair_quest(a)["day_cap"] == PAIR_DAY_CAP


def test_view_shows_counts_today_and_what_is_left(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1])
    _mark(b, quest_id, [1, 2, 0])
    view = get_pair_quest(a)["quest"]
    assert view["phase"] == "active" and view["progress"] == 9 and view["need"] == 11
    assert [d["me"] for d in view["days"][:3]] == [2, 3, 1] and [d["partner"] for d in view["days"][:3]] == [1, 2, 0]
    assert [d["both"] for d in view["days"][:3]] == [True, True, False]
    assert view["today"] == {"me": 1, "partner": 0, "held": 0} and view["joint_days"] == 2
    partner_view = get_pair_quest(b)["quest"]
    assert partner_view["today"]["me"] == 0 and partner_view["today"]["partner"] == 1, "у напарника картинка зеркальная"
    # остаток возможностей: сегодня 2 + 3 (у A уже одна, у B ноль), впереди 4 дня по 6
    assert view["can"] == 2 + 3 + 4 * 6 and view["pace"] == "ok"


def test_pace_is_lost_when_the_remaining_days_cannot_cover_it(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-5)                      # шестой день из семи
    _mark(a, quest_id, [1, 0, 0, 0, 0, 0]); _mark(b, quest_id, [0, 0, 0, 0, 0, 1])
    view = get_pair_quest(a)["quest"]
    assert view["progress"] == 2 and view["need"] == 18
    assert view["can"] == (3 - 0) + (3 - 1) + 6 + 0 and view["pace"] == "lost"


def test_goal_is_reached_with_both_friends_and_the_chest_opens(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)                      # четвёртый день
    _mark(a, quest_id, [3, 3, 3, 1]); _mark(b, quest_id, [3, 3, 3, 1])
    view = get_pair_quest(a)["quest"]
    assert view["phase"] == "completed" and view["progress"] == PAIR_GOAL and view["claimable"] is True
    assert _status(quest_id) == "completed"
    assert view["days"][3]["done"] is True
    assert claim_pair_chest(a, quest_id)["ok"] and claim_pair_chest(b, quest_id)["ok"]


def test_while_the_last_habit_waits_for_the_partner_the_quest_stays_active(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 2, 3]); _mark(b, quest_id, [3, 3, 3, 0])      # 17 + 3 → зачтено 19
    mine = get_pair_quest(a)["quest"]
    assert mine["phase"] == "active" and mine["progress"] == 19 and mine["need"] == 1
    assert mine["waiting_for"] == "partner" and mine["today"] == {"me": 3, "partner": 0, "held": 1}
    theirs = get_pair_quest(b)["quest"]
    assert theirs["waiting_for"] == "me" and theirs["today"]["partner"] == 3
    _mark(b, quest_id, [0, 0, 0, 1])                                           # B наконец закрыл одну
    done = get_pair_quest(a)["quest"]
    assert done["phase"] == "completed" and done["progress"] == PAIR_GOAL and done["waiting_for"] is None


def test_habits_of_the_same_id_count_once_a_day_and_extra_ones_are_capped(uid):
    a, b, quest_id = _quest(uid)
    today = str(local_today(a))
    _event(a, today, habit_id=77); _event(a, today, habit_id=77)             # дубль одной привычки
    for _ in range(5):
        _event(b, today)                                                       # пять разных — засчитаются три
    view = get_pair_quest(a)["quest"]
    assert view["today"]["me"] == 1 and view["today"]["partner"] == PAIR_DAY_CAP and view["progress"] == 4


def test_day_boundaries_follow_the_local_timezone_of_each_friend(uid):
    a, b, quest_id = _quest(uid)
    set_timezone(a, "Asia/Tokyo")                                              # UTC+9: 23:30 UTC — уже следующий день
    start = _one("SELECT start_day FROM pair_quests WHERE id=?", (quest_id,))["start_day"]
    day = datetime.strptime(start, "%Y-%m-%d").date()
    stamp = datetime.combine(day, time(23, 30), tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?, ?, ?)", (a, 90_000_001, stamp))
    counts = get_pair_quest(a)["quest"]["days"]
    assert counts[0]["me"] == 0 and counts[1]["me"] == 1, "23:30 UTC = 08:30 следующего дня в Токио"


def test_legacy_quest_view_has_the_same_shape(uid):
    a, b = _pair(uid)
    quest_id = _start(a, b)                                  # старое задание
    _window(quest_id)
    view = get_pair_quest(a)["quest"]
    assert view["rules"] == RULES_DAYS and view["cap"] == 1 and view["goal"] == LEGACY_PAIR_GOAL
    assert {"today", "need", "can", "pace", "waiting_for", "joint_days"} <= set(view)


# ---------------------------------------------------------------------------
# ПУШИ И НАПОМИНАНИЯ
# ---------------------------------------------------------------------------

def test_partner_push_after_a_mark_and_final_flag_for_the_last_habit(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 0, 1])
    events = pair_on_day_completed(a)
    assert [e["type"] for e in events] == ["partner_done"] and events[0]["to"] == b
    assert events[0]["progress"] == 3 and events[0]["goal"] == PAIR_GOAL and events[0]["final"] is False
    _sql("DELETE FROM pair_quest_pings WHERE quest_id=?", (quest_id,))
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 2, 3]); _mark(b, quest_id, [3, 3, 3, 0])
    again = pair_on_day_completed(a)
    assert again and again[0]["final"] is True and again[0]["progress"] == 19


def test_no_push_when_the_partner_is_already_in_action_today(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [0, 0, 1]); _mark(b, quest_id, [0, 0, 1])
    assert pair_on_day_completed(a) == []


def test_completion_event_when_the_last_habit_closes_the_quest(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 3, 1]); _mark(b, quest_id, [3, 3, 3, 1])
    events = pair_on_day_completed(a)
    assert events and events[0]["type"] == "completed" and set(events[0]["users"]) == {a, b}
    assert settle_pair_quests() == [], "повторно не засчитывается"


def test_scheduler_settles_a_quest_finished_through_the_bot(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 3, 1]); _mark(b, quest_id, [3, 3, 3, 1])
    events = settle_pair_quests()
    assert [e["quest_id"] for e in events if e["quest_id"] == quest_id] == [quest_id]
    assert _status(quest_id) == "completed"


def test_last_day_reminder_counts_habits_and_checks_that_the_goal_is_reachable(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-6)                       # сегодня последний, седьмой день
    _mark(a, quest_id, [3, 3, 3, 0, 0, 0]); _mark(b, quest_id, [3, 3, 0, 0, 0, 0])      # 15 из 20, осталось 5
    row = dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest_id,)))
    assert pair_last_day_reminder(row, a) == {"left": 5, "partner_name": "Борис"}
    assert pair_last_day_reminder(row, b) == {"left": 5, "partner_name": "Анна"}
    # у A сегодня уже три привычки — свой потолок закрыт, ему напоминать нечего (напарнику — по-прежнему)
    today = str(local_today(a))
    for _ in range(3):
        _event(a, today)
    row = dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest_id,)))
    assert pair_last_day_reminder(row, a) is None
    assert pair_last_day_reminder(row, b) == {"left": 2, "partner_name": "Анна"}


def test_last_day_reminder_is_silent_when_the_goal_is_out_of_reach(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-6)
    _mark(a, quest_id, [1, 0, 0, 0, 0, 0]); _mark(b, quest_id, [0, 1, 0, 0, 0, 0])
    row = dict(_one("SELECT * FROM pair_quests WHERE id=?", (quest_id,)))
    assert pair_last_day_reminder(row, a) is None


# ---------------------------------------------------------------------------
# МАРШРУТЫ: отметка привычки в Mini App попадает в журнал и в счёт
# ---------------------------------------------------------------------------

def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


async def test_marking_habits_in_the_app_moves_the_pair_counter(client, uid):
    import webapp.webapp_server as ws
    ws._background_tasks.clear()
    a, b = _pair(uid)
    for title in ("Вода", "Зарядка"):
        add_habit(a, title)
        add_habit(b, title)
    quest_id = _start(a, b, rules="habits")
    _window(quest_id, start_offset=0)                         # сегодня — первый день
    _clean(a, b)
    for habit in get_habits(a):
        r = await client.post(f"/api/habits/{habit['id']}/complete", headers=_headers(a))
        assert r.status == 200
    payload = (await r.json())["pair_quest"]["quest"]
    assert payload["progress"] == 3 and payload["today"]["me"] == 3 and payload["today"]["partner"] == 0
    for habit in get_habits(b):
        r = await client.post(f"/api/habits/{habit['id']}/complete", headers=_headers(b))
    payload = (await r.json())["pair_quest"]["quest"]
    assert payload["progress"] == 6 and payload["today"]["me"] == 3 and payload["joint_days"] == 1


# ---------------------------------------------------------------------------
# КРАЙНИЕ СЛУЧАИ
# ---------------------------------------------------------------------------

def test_a_quest_that_ran_out_of_days_without_the_goal_expires(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-8)                       # окно закончилось вчера
    _mark(a, quest_id, [3, 3, 3, 3, 0, 0, 0]); _mark(b, quest_id, [3, 3, 3, 0, 0, 0, 0])      # 18 + 3 → зачтено 19, напарник не пришёл
    assert get_pair_quest(a)["quest"] is None
    assert _status(quest_id) == "expired"
    assert get_pair_quest(a)["recent_fail"] is not None


def test_deleting_a_habit_afterwards_does_not_take_the_credit_back(uid):
    a, b, quest_id = _quest(uid)
    today = str(local_today(a))
    for habit_id in (81, 82):
        _event(a, today, habit_id=habit_id)
    before = get_pair_quest(a)["quest"]["progress"]
    _sql("DELETE FROM habits WHERE user_id=?", (a,))          # привычки удалены — журнал выполнений остаётся
    assert get_pair_quest(a)["quest"]["progress"] == before == 2


def test_progress_is_the_same_for_both_friends_and_the_goal_is_never_exceeded(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 3, 3]); _mark(b, quest_id, [3, 3, 3, 3])      # 24 отметки на 20 цели
    mine, theirs = get_pair_quest(a)["quest"], get_pair_quest(b)["quest"]
    assert mine["progress"] == theirs["progress"] == PAIR_GOAL and mine["phase"] == theirs["phase"] == "completed"
    assert mine["need"] == 0 and mine["pace"] == "done"
