"""
Новую привычку можно добавить всегда, даже если сегодня уже была отметка и
удаление другой привычки.

Жалоба с телефона: удалил одну-две привычки, одну отметил — «Сегодня уже была
отметка и удаление привычки — добавление новых открыто с 00:00», и новую не
добавить. Особенно обидно новичку в первый день. Накрутку Adam Coin («отметить
→ удалить → добавить → отметить...») теперь гасит дневной потолок наград:
монеты платят только за первые MAX_HABITS отметок за день.
"""
from pathlib import Path

from db import (
    add_user, add_habit, get_habits, delete_habit, complete_habit, get_user, can_add_habit,
    MAX_HABITS, MAX_REWARDED_COMPLETIONS_PER_DAY, REWARD_CAPPED_TEXT, reward_line,
)
from db.core import connect
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _coins(user_id):
    return int(get_user(user_id)["xp"] or 0)


def _farm_round(user_id, title="Накрутка"):
    """Добавить привычку, отметить, удалить; вернуть результат отметки."""
    habit_id = add_habit(user_id, title)
    result = complete_habit(habit_id)
    delete_habit(habit_id)
    return result


# ---------------------------------------------------------------------------
# ДОБАВЛЕНИЕ БОЛЬШЕ НЕ БЛОКИРУЕТСЯ
# ---------------------------------------------------------------------------

def test_can_add_a_habit_after_completing_one_and_deleting_another(uid):
    """Сценарий из жалобы: одну привычку отметил, пару удалил — новую добавить можно."""
    add_user(uid, "u", "Новичок")
    done_id = add_habit(uid, "Читать 20 минут")
    for title in ("Лишняя 1", "Лишняя 2"):
        delete_habit(add_habit(uid, title))
    assert complete_habit(done_id)

    assert can_add_habit(uid) == (True, None)
    new_id = add_habit(uid, "Новая привычка")
    assert {h["id"] for h in get_habits(uid)} == {done_id, new_id}


def test_deleting_a_habit_that_was_already_completed_does_not_lock_adding_either(uid):
    add_user(uid, "u", "Новичок")
    habit_id = add_habit(uid, "Пробная")
    assert complete_habit(habit_id)
    delete_habit(habit_id)
    assert can_add_habit(uid) == (True, None)
    add_habit(uid, "Настоящая")
    assert [h["title"] for h in get_habits(uid)] == ["Настоящая"]


def test_the_ten_habit_limit_still_applies(uid):
    add_user(uid, "u", "Test")
    for i in range(MAX_HABITS):
        add_habit(uid, f"Привычка {i}")
    assert can_add_habit(uid) == (False, "habit_limit")
    delete_habit(get_habits(uid)[0]["id"])
    assert can_add_habit(uid) == (True, None)


# ---------------------------------------------------------------------------
# ДНЕВНОЙ ПОТОЛОК НАГРАД ВМЕСТО ЗАПРЕТА
# ---------------------------------------------------------------------------

def test_cap_equals_the_number_of_habits_one_can_honestly_close():
    assert MAX_REWARDED_COMPLETIONS_PER_DAY == MAX_HABITS == 10


def test_honest_user_is_paid_for_all_ten_habits(uid):
    add_user(uid, "u", "Честный")
    ids = [add_habit(uid, f"Привычка {i}") for i in range(MAX_HABITS)]
    for habit_id in ids:
        result = complete_habit(habit_id)
        assert result["reward_capped"] is False and result["coins"] > 0


def test_add_complete_delete_loop_stops_paying_after_the_cap(uid):
    add_user(uid, "u", "Накрутчик")
    rounds = [_farm_round(uid, f"Привычка {i}") for i in range(MAX_REWARDED_COMPLETIONS_PER_DAY + 3)]
    paid, capped = rounds[:MAX_REWARDED_COMPLETIONS_PER_DAY], rounds[MAX_REWARDED_COMPLETIONS_PER_DAY:]
    assert all(r["coins"] > 0 and not r["reward_capped"] for r in paid)
    assert all(r["coins"] == 0 and r["reward_capped"] for r in capped)
    # Сверх потолка баланс и рейтинг (total_xp) не двигаются вовсе — проверяем
    # на том же пользователе, повторив «накрутку» ещё раз.
    coins_before, total_before = _coins(uid), int(get_user(uid)["total_xp"])
    for i in range(5):
        assert _farm_round(uid, f"Ещё {i}")["reward_capped"] is True
    assert _coins(uid) == coins_before and int(get_user(uid)["total_xp"]) == total_before


def test_capped_completion_still_counts_for_the_habit_and_the_streak(uid):
    add_user(uid, "u", "Test")
    for i in range(MAX_REWARDED_COMPLETIONS_PER_DAY):
        _farm_round(uid, f"Привычка {i}")
    habit_id = add_habit(uid, "Одиннадцатая")
    result = complete_habit(habit_id)
    assert result["reward_capped"] is True and result["coins"] == 0
    assert any(h["id"] == habit_id and h["completed"] for h in get_habits(uid))
    assert complete_habit(habit_id) is False, "повторная отметка по-прежнему невозможна"


def test_reward_counter_does_not_go_down_when_a_habit_is_deleted(uid):
    add_user(uid, "u", "Test")
    _farm_round(uid)
    conn = connect()
    n = conn.execute("SELECT n FROM habit_reward_days WHERE user_id=?", (uid,)).fetchone()["n"]
    conn.close()
    assert n == 1


def test_cap_is_per_day_and_per_user(uid):
    add_user(uid, "u", "Первый")
    other = uid + 1
    add_user(other, "o", "Второй")
    for i in range(MAX_REWARDED_COMPLETIONS_PER_DAY):
        _farm_round(uid, f"П{i}")
    assert _farm_round(uid)["reward_capped"] is True
    assert _farm_round(other)["reward_capped"] is False          # у другого игрока свой счётчик

    conn = connect()
    conn.execute("UPDATE habit_reward_days SET day='2000-01-01' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    result = _farm_round(uid)                                     # «наступил новый день»
    assert result["reward_capped"] is False and result["coins"] > 0


def test_bot_message_explains_the_cap():
    paid = {"coins": 10, "doubled": False, "reward_capped": False}
    capped = {"coins": 0, "doubled": False, "reward_capped": True}
    assert reward_line(paid) == "⭐ +10 Adam Coin"
    assert reward_line({**paid, "doubled": True}).startswith("⭐ +10 Adam Coin (×2")
    assert reward_line(capped) == f"⭐ {REWARD_CAPPED_TEXT}" and "+0" not in reward_line(capped)


# ---------------------------------------------------------------------------
# HTTP И КЛИЕНТ
# ---------------------------------------------------------------------------

async def test_create_habit_route_after_mark_and_delete(client, uid):
    add_user(uid, "u", "Новичок")
    headers = _headers(uid)
    first = await (await client.post("/api/habits", json={"title": "Читать 20 минут"}, headers=headers)).json()
    extra = await (await client.post("/api/habits", json={"title": "Лишняя привычка"}, headers=headers)).json()
    assert (await client.post(f"/api/habits/{first['habit']['id']}/complete", headers=headers)).status == 200
    assert (await client.delete(f"/api/habits/{extra['habit']['id']}", headers=headers)).status == 200
    r = await client.post("/api/habits", json={"title": "Новая привычка"}, headers=headers)
    assert r.status == 200 and (await r.json())["ok"] is True


async def test_complete_route_reports_the_cap(client, uid):
    add_user(uid, "u", "Test")
    for i in range(MAX_REWARDED_COMPLETIONS_PER_DAY):
        _farm_round(uid, f"П{i}")
    created = await (await client.post("/api/habits", json={"title": "Одиннадцатая"}, headers=_headers(uid))).json()
    data = await (await client.post(f"/api/habits/{created['habit']['id']}/complete", headers=_headers(uid))).json()
    assert data["ok"] and data["reward_capped"] is True and data["coins"] == 0

    fresh = uid + 1
    add_user(fresh, "f", "Свежий")
    created = await (await client.post("/api/habits", json={"title": "Первая"}, headers=_headers(fresh))).json()
    data = await (await client.post(f"/api/habits/{created['habit']['id']}/complete", headers=_headers(fresh))).json()
    assert data["reward_capped"] is False and data["coins"] > 0


def test_toast_does_not_show_plus_ten_for_a_capped_completion():
    body = APP_JS[APP_JS.index("async function celebrateHabitCompletion"):][:900]
    assert "result.reward_capped" in body
    # `result.coins || 10` для 0 дал бы «+10 Adam Coin»
    assert body.index("if (result.reward_capped)") < body.index("${result.coins || 10}")
