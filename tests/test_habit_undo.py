"""
Отмена отметки привычки (правка владельца, 24.10.2026): двойной тап по зелёной галочке → «Убрать отметку о выполнении?» → привычка снова не
выполнена. Откат точный: монеты, место в дневном потолке, серия, окно «×2», счётчики; и без накрутки — отметил/убрал по кругу даёт ноль.
"""
import itertools
from datetime import timedelta

import pytest

import db.habit_undo as undo
from db import add_habit, add_user, complete_habit, get_habits, get_user
from db.core import connect
from db.habits import increment_habit_progress
from db.statistics import get_weekly_summary
from db.streak import day_key, local_today
from tests.conftest import sign_init_data

_counter = itertools.count(861_700_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _setup(uid_, *titles, target_count=1):
    add_user(uid_, f"u{uid_}", "Аня")
    ids = []
    for title in titles:
        ids.append(add_habit(uid_, title, target_count=target_count))
    return ids


def _row(sql, *args):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _habit(habit_id):
    return _row("SELECT * FROM habits WHERE id=?", habit_id)


def _slot(uid_):
    row = _row("SELECT n FROM habit_reward_days WHERE user_id=? AND day=?", uid_, day_key(local_today(uid_)))
    return row["n"] if row else 0


def _today(uid_):
    return day_key(local_today(uid_))


def _streak_day(uid_):
    return _row("SELECT status FROM streak_days WHERE user_id=? AND day=?", uid_, _today(uid_))


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------------------------------------------- откат одной отметки

def test_the_mark_is_taken_back_with_everything_it_gave(uid):
    habit_id, = _setup(uid, "Зарядка")
    before = get_user(uid)
    done = complete_habit(habit_id)
    assert done and done["coins"] > 0
    assert _habit(habit_id)["completed"] == 1 and get_user(uid)["streak"] == 1
    gained = get_user(uid)["xp"] - before["xp"]

    result = undo.undo_mark(habit_id)
    assert result["ok"] and result["streak_restored"] is True
    assert result["coins_back"] == gained, "возвращается всё, что отметка принесла: монеты за привычку и награда закрытого ею задания дня"
    assert gained >= done["coins"]

    after = get_user(uid)
    assert _habit(habit_id)["completed"] == 0
    assert after["xp"] == before["xp"] and after["total_xp"] == before["total_xp"] and after["level"] == before["level"]
    assert after["total_completed"] == before["total_completed"]
    assert after["streak"] == 0 and after["last_completed"] is None and not after["best_streak"]
    assert _streak_day(uid) is None, "день без отметок серии не засчитан"
    assert _slot(uid) == 0, "место в дневном потолке наград освобождено"
    assert _row("SELECT COUNT(*) AS n FROM habit_completion_events WHERE user_id=?", uid)["n"] == 0
    assert _row("SELECT completed FROM calendar WHERE user_id=?", uid)["completed"] == 0
    week = get_weekly_summary(uid)
    assert week["completed"] == 0 and week["xp"] == 0, "статистика недели сошлась в ноль"


def test_marking_again_pays_the_same_and_counts_the_day_again(uid):
    habit_id, = _setup(uid, "Зарядка")
    first = complete_habit(habit_id)
    undo.undo_mark(habit_id)
    second = complete_habit(habit_id)
    assert second and second["coins"] == first["coins"], "после отмены монеты за повторную отметку те же, ничего не потеряно"
    assert get_user(uid)["streak"] == 1 and _streak_day(uid)["status"] == "completed"
    assert _slot(uid) == 1


def test_a_second_undo_of_the_same_mark_does_nothing(uid):
    habit_id, = _setup(uid, "Зарядка")
    complete_habit(habit_id)
    assert undo.undo_mark(habit_id)["ok"]
    assert undo.undo_mark(habit_id) == {"error": undo.ERROR_NOT_COMPLETED}
    assert get_user(uid)["xp"] == 0


def test_an_unmarked_habit_cannot_be_undone(uid):
    habit_id, = _setup(uid, "Зарядка")
    assert undo.undo_mark(habit_id) == {"error": undo.ERROR_NOT_COMPLETED}
    assert undo.undo_mark(987_654_321) == {"error": undo.ERROR_NOT_FOUND}


# ---------------------------------------------------------------------------------------------------------------- серия

def test_the_day_stays_counted_while_another_habit_is_done(uid):
    first, second = _setup(uid, "А", "Б")
    complete_habit(first)
    complete_habit(second)
    assert get_user(uid)["streak"] == 1

    result = undo.undo_mark(first)
    assert result["ok"] and result["streak_restored"] is False
    assert get_user(uid)["streak"] == 1 and _streak_day(uid)["status"] == "completed", "вторая привычка ещё держит день"

    result = undo.undo_mark(second)
    assert result["ok"] and result["streak_restored"] is True
    assert get_user(uid)["streak"] == 0 and _streak_day(uid) is None


def test_the_streak_goes_back_to_what_it_was_before_the_day(uid):
    habit_id, = _setup(uid, "Чтение")
    yesterday = day_key(local_today(uid) - timedelta(days=1))
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?,?,?)", (uid, yesterday, "completed", 13))
    conn.execute("UPDATE users SET streak=13, best_streak=13, last_completed=? WHERE telegram_id=?", (yesterday, uid))
    conn.execute("INSERT OR REPLACE INTO streak_meta(user_id, rollover_day, temp_frame, temp_status) VALUES (?,?,?,?)",
                 (uid, _today(uid), "streak_flame", "В ударе 13 дн."))
    conn.commit()
    conn.close()

    done = complete_habit(habit_id)
    assert done and get_user(uid)["streak"] == 14 and get_user(uid)["best_streak"] == 14

    assert undo.undo_mark(habit_id)["streak_restored"] is True
    user = get_user(uid)
    assert user["streak"] == 13 and user["best_streak"] == 13 and user["last_completed"] == yesterday
    meta = _row("SELECT temp_frame, temp_status FROM streak_meta WHERE user_id=?", uid)
    assert meta["temp_frame"] == "streak_flame" and meta["temp_status"] == "В ударе 13 дн."
    assert _row("SELECT 1 FROM streak_rewards WHERE user_id=? AND milestone=14", uid), "рамка за рубеж уже выдана и остаётся"

    complete_habit(habit_id)
    assert get_user(uid)["streak"] == 14, "повторная отметка снова доводит до рубежа, награда не дублируется"
    assert _row("SELECT COUNT(*) AS n FROM streak_rewards WHERE user_id=? AND milestone=14", uid)["n"] == 1


# ---------------------------------------------------------------------------------------------------------------- окно ×2

def test_undoing_the_latest_mark_brings_the_double_window_back(uid):
    first, second = _setup(uid, "А", "Б", "В")[:2]
    complete_habit(first)
    twice = complete_habit(second)
    assert twice["doubled"] is True

    assert undo.undo_mark(second)["ok"]
    again = complete_habit(second)
    assert again["doubled"] is True and again["coins"] == twice["coins"], "окно от первой отметки осталось: отметка снова ×2, монеты не потеряны"


def test_undoing_an_earlier_mark_leaves_the_window_alone(uid):
    first, second, third = _setup(uid, "А", "Б", "В")
    complete_habit(first)
    complete_habit(second)
    assert undo.undo_mark(first)["ok"]
    mark = _row("SELECT window_before FROM habit_marks WHERE habit_id=? AND undone_at IS NULL", second)
    assert mark is not None
    assert complete_habit(third)["doubled"] is True, "окно открыла вторая отметка, она осталась — окно тоже"


# ---------------------------------------------------------------------------------------------------------------- защита от накрутки

def test_mark_and_undo_in_a_loop_never_earns_anything(uid):
    habit_id, = _setup(uid, "Зарядка")
    start = get_user(uid)
    for _ in range(undo.UNDO_DAILY_LIMIT):
        assert complete_habit(habit_id)
        assert undo.undo_mark(habit_id)["ok"]
    now = get_user(uid)
    assert (now["xp"], now["total_xp"], now["level"], now["total_completed"]) == (start["xp"], start["total_xp"], start["level"], start["total_completed"])
    assert _slot(uid) == 0
    assert get_weekly_summary(uid)["xp"] == 0


def test_the_daily_undo_limit(uid):
    habit_id, = _setup(uid, "Зарядка")
    for _ in range(undo.UNDO_DAILY_LIMIT):
        complete_habit(habit_id)
        assert undo.undo_mark(habit_id)["ok"]
    complete_habit(habit_id)
    assert undo.undo_mark(habit_id) == {"error": undo.ERROR_LIMIT}
    assert _habit(habit_id)["completed"] == 1, "лимит не трогает отметку"


def test_spent_coins_block_the_undo(uid):
    habit_id, = _setup(uid, "Зарядка")
    complete_habit(habit_id)
    conn = connect()
    conn.execute("UPDATE users SET xp=0 WHERE telegram_id=?", (uid,))     # монеты ушли в магазин
    conn.commit()
    conn.close()
    assert undo.undo_mark(habit_id) == {"error": undo.ERROR_COINS_SPENT}
    assert _habit(habit_id)["completed"] == 1 and get_user(uid)["streak"] == 1


def test_a_capped_mark_with_no_coins_can_still_be_undone_even_with_an_empty_wallet(uid):
    habit_id, = _setup(uid, "Зарядка")
    conn = connect()
    conn.execute("UPDATE users SET rewards_frozen_until=datetime('now', '+1 day') WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    done = complete_habit(habit_id)
    assert done["coins"] == 0 and done["reward_capped"]
    assert undo.undo_mark(habit_id)["ok"]


def test_an_old_or_unrecorded_mark_cannot_be_undone(uid):
    habit_id, = _setup(uid, "Зарядка")
    complete_habit(habit_id)
    conn = connect()
    conn.execute("DELETE FROM habit_marks WHERE habit_id=?", (habit_id,))      # отметка из времён до журнала
    conn.commit()
    conn.close()
    assert undo.undo_mark(habit_id) == {"error": undo.ERROR_TOO_LATE}

    other, = _setup(uid + 1, "Чтение")
    complete_habit(other)
    conn = connect()
    conn.execute("UPDATE habit_marks SET day='2000-01-01' WHERE habit_id=?", (other,))   # отметка вчерашнего дня
    conn.commit()
    conn.close()
    assert undo.undo_mark(other) == {"error": undo.ERROR_TOO_LATE}


# ---------------------------------------------------------------------------------------------------------------- прочее

def test_a_counter_habit_goes_back_one_step(uid):
    habit_id, = _setup(uid, "Выпить воду", target_count=3)
    for _ in range(3):
        increment_habit_progress(habit_id)
    assert _habit(habit_id)["completed"] == 1 and _habit(habit_id)["progress_count"] == 3
    assert undo.undo_mark(habit_id)["ok"]
    row = _habit(habit_id)
    assert row["completed"] == 0 and row["progress_count"] == 2, "из 3/3 в 2/3: одно нажатие снова закрывает цель"
    assert increment_habit_progress(habit_id)["just_completed"] is True


def test_the_pet_and_the_coin_task_follow_the_undo(uid):
    habit_id, = _setup(uid, "Зарядка")
    conn = connect()
    conn.execute("INSERT INTO daily_tasks(user_id, task, progress, goal, reward, completed, task_date) VALUES (?,?,?,?,?,0,date('now'))",
                 (uid, "Получить 20 Adam Coin", 0, 20, 20))
    conn.commit()
    conn.close()
    done = complete_habit(habit_id)
    assert _row("SELECT progress FROM daily_tasks WHERE user_id=? AND task=?", uid, "Получить 20 Adam Coin")["progress"] == done["coins"]
    assert _row("SELECT care_points FROM virtual_pets WHERE user_id=?", uid)["care_points"] == 1
    undo.undo_mark(habit_id)
    assert _row("SELECT progress FROM daily_tasks WHERE user_id=? AND task=?", uid, "Получить 20 Adam Coin")["progress"] == 0
    assert _row("SELECT care_points FROM virtual_pets WHERE user_id=?", uid)["care_points"] == 0


def test_the_level_follows_the_coins(uid):
    habit_id, = _setup(uid, "Зарядка")
    conn = connect()
    conn.execute("UPDATE users SET xp=95, total_xp=95, level=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    done = complete_habit(habit_id)
    assert get_user(uid)["level"] == 2 and done["coins"] >= 5
    undo.undo_mark(habit_id)
    user = get_user(uid)
    assert user["level"] == 1 and user["total_xp"] == 95 and user["xp"] == 95


# ---------------------------------------------------------------------------------------------------------------- HTTP

async def test_the_route_takes_the_mark_back_and_answers_with_a_patch(client, uid):
    habit_id, = _setup(uid, "Зарядка")
    marked = await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(uid))
    assert marked.status == 200
    coins = (await marked.json())["coins"]
    xp_after_mark = get_user(uid)["xp"]
    assert xp_after_mark >= coins > 0

    r = await client.post(f"/api/habits/{habit_id}/uncomplete", headers=_headers(uid))
    assert r.status == 200
    body = await r.json()
    assert body["ok"] and body["undone"] and body["coins_back"] == xp_after_mark and body["streak_restored"] is True
    assert body["habit"]["completed"] is False and body["user"]["xp"] == 0
    for key in ("daily_quests", "month_quests", "progress", "streak", "pet", "hero", "pair_quest"):
        assert key in body, key
    assert body["streak"]["streak"] == 0 if isinstance(body["streak"], dict) and "streak" in body["streak"] else True


async def test_the_route_refuses_with_a_code(client, uid):
    habit_id, = _setup(uid, "Зарядка")
    r = await client.post(f"/api/habits/{habit_id}/uncomplete", headers=_headers(uid))
    assert r.status == 409 and (await r.json())["error"] == "habit_not_done"

    await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(uid))
    conn = connect()
    conn.execute("UPDATE users SET xp=0 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    r = await client.post(f"/api/habits/{habit_id}/uncomplete", headers=_headers(uid))
    assert r.status == 409 and (await r.json())["error"] == "undo_coins_spent"


async def test_someone_elses_habit_cannot_be_undone(client, uid):
    habit_id, = _setup(uid, "Зарядка")
    complete_habit(habit_id)
    add_user(uid + 1, f"u{uid + 1}", "Боря")
    r = await client.post(f"/api/habits/{habit_id}/uncomplete", headers=_headers(uid + 1))
    assert r.status in (403, 404)
    assert _habit(habit_id)["completed"] == 1


def test_the_journal_keeps_the_ids_of_the_habits(uid):
    first, second = _setup(uid, "А", "Б")
    complete_habit(first)
    complete_habit(second)
    rows = get_habits(uid)
    assert len(rows) == 2
    marks = _row("SELECT COUNT(*) AS n FROM habit_marks WHERE user_id=? AND undone_at IS NULL", uid)["n"]
    assert marks == 2


async def test_the_streak_push_is_not_repeated_when_the_day_is_counted_again(client, uid, monkeypatch):
    import webapp.webapp_server as server
    pushes = []
    monkeypatch.setattr(server, "_push_in_background", lambda app, telegram_id, text: pushes.append(text))
    habit_id, = _setup(uid, "Зарядка")
    conn = connect()
    conn.execute("UPDATE users SET first_win_push_sent=1 WHERE telegram_id=?", (uid,))     # «первая победа» уже была — остаётся пуш про серию
    conn.commit()
    conn.close()

    first = await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(uid))
    assert first.status == 200 and len(pushes) == 1 and "+1 день" in pushes[0]
    assert (await client.post(f"/api/habits/{habit_id}/uncomplete", headers=_headers(uid))).status == 200
    again = await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(uid))
    body = await again.json()
    assert again.status == 200 and body["streak_event"], "в приложении праздник серии покажется снова"
    assert len(pushes) == 1, "а пуш про «+1 день» второй раз не уходит"
