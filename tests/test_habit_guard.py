"""
Защита от накрутки Adam Coin через привычки (16.10, п.1 владельца): «добавил → отметил → удалил → добавил ещё».
Монеты за день — не больше числа привычек в списке прямо сейчас; много удалений/созданий за день отключают монеты и ставят подозрение (админам
уходит уведомление); создавать привычки пачкой нельзя; админ может заморозить награды. Обычный человек этого не замечает.
"""
import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import abuse_alerts
from db import add_habit, add_user, complete_habit, delete_habit, get_habits, get_user, habit_guard
from db.core import connect
from db.habits import reward_cap_text, reward_line
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")

_uid_counter = itertools.count(852_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(user_id, name="Игрок"):
    add_user(user_id, f"u{user_id}", name)
    return user_id


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _row(query, params=()):
    conn = connect()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def _today(user_id):
    from db.streak import day_key, local_today
    return day_key(local_today(user_id))


def _coins(user_id):
    return int(get_user(user_id)["xp"] or 0)


def _pay(habit_id):
    return complete_habit(habit_id)


def _slow(user_id):
    """Сдвигаем записи о создании привычек «в прошлое», чтобы честный набор из нескольких привычек не упирался в защиту от пачки."""
    _sql("UPDATE habit_churn SET at=datetime('now','-10 minutes') WHERE user_id=?", (user_id,))


# ------------------------------------------------------------------------------------------------ потолок по числу привычек

def test_an_honest_person_is_paid_for_every_habit_in_the_list(uid):
    user = _user(uid)
    ids = [add_habit(user, f"Привычка {i}") for i in range(5)]
    results = [_pay(i) for i in ids]
    assert all(r["coins"] > 0 and not r["reward_capped"] and r["reward_cap_reason"] is None and r["reward_cap_text"] is None for r in results)


def test_the_loop_add_mark_delete_pays_for_the_first_round_only(uid):
    user = _user(uid)
    paid = []
    for i in range(6):
        habit_id = add_habit(user, f"Круг {i}")
        paid.append(_pay(habit_id))
        delete_habit(habit_id)
    assert [bool(r["coins"]) for r in paid] == [True] + [False] * 5
    assert all(r["reward_cap_reason"] == "limit" for r in paid[1:2])


def test_fixing_a_typo_and_cleaning_up_do_not_cost_an_honest_person_anything(uid):
    user = _user(uid)
    a, b, c = (add_habit(user, t) for t in ("Читать", "Спорт", "Вода"))
    assert _pay(a)["coins"] > 0 and _pay(b)["coins"] > 0                          # отметил две
    delete_habit(a)                                                                # «опечатка в названии»
    fixed = add_habit(user, "Читать 20 минут")                                     # создал заново
    assert _pay(fixed)["coins"] > 0                                                # третья выплата: привычек в списке снова три
    third = _pay(c)                                                                # а вот четвёртая отметка за день — сверх списка
    assert third["coins"] == 0 and third["reward_cap_reason"] == "limit"
    assert len(get_habits(user)) == 3 and _coins(user) > 0


def test_adding_a_habit_during_the_day_raises_the_allowance(uid):
    user = _user(uid)
    a, b = add_habit(user, "Утро"), add_habit(user, "Вечер")
    assert _pay(a)["coins"] > 0 and _pay(b)["coins"] > 0
    extra = add_habit(user, "Днём")
    assert _pay(extra)["coins"] > 0


def test_a_full_list_of_ten_is_still_fully_paid(uid):
    user = _user(uid)
    ids = []
    for i in range(10):
        ids.append(add_habit(user, f"П{i}"))
    assert all(_pay(i)["coins"] > 0 for i in ids)


def test_the_message_says_what_happened(uid):
    assert "все твои привычки" in reward_cap_text("limit") and reward_cap_text(None) == reward_cap_text("limit")
    assert "слишком много изменений" in reward_cap_text("churn") and "отключены" in reward_cap_text("frozen")
    assert reward_line({"reward_capped": True, "reward_cap_reason": "churn", "coins": 0, "doubled": False}).startswith("⭐ Сегодня слишком много")


# ------------------------------------------------------------------------------------------------ много удалений за день

def test_many_deletions_switch_the_coins_off_for_the_day_but_not_the_mark(uid):
    user = _user(uid)
    keep = add_habit(user, "Настоящая")
    for i in range(habit_guard.DELETE_SOFT_LIMIT):
        delete_habit(add_habit(user, f"Лишняя {i}"))
    assert habit_guard.reward_lock(user, _today(user)) == "churn"
    control = _user(uid + 1)                                                        # такой же человек без лишних удалений
    control_habit = add_habit(control, "Настоящая")
    before, control_before = _coins(user), _coins(control)
    result, control_result = _pay(keep), _pay(control_habit)
    assert result["reward_capped"] and result["coins"] == 0 and result["reward_cap_reason"] == "churn" and "слишком много" in result["reward_cap_text"]
    assert any(h["id"] == keep and h["completed"] for h in get_habits(user))
    # за привычку монет нет (то, что приходит за ежедневное задание «выполнить привычку», у обоих одинаково и раз в день)
    assert _coins(user) - before == (_coins(control) - control_before) - control_result["coins"]


def test_just_below_the_threshold_nothing_changes(uid):
    user = _user(uid)
    keep = add_habit(user, "Настоящая")
    for i in range(habit_guard.DELETE_SOFT_LIMIT - 1):
        delete_habit(add_habit(user, f"Лишняя {i}"))
    assert habit_guard.reward_lock(user, _today(user)) is None
    assert _pay(keep)["coins"] > 0 and not _row("SELECT 1 FROM abuse_flags WHERE user_id=?", (user,))


def test_a_new_day_starts_clean(uid):
    user = _user(uid)
    for i in range(habit_guard.DELETE_SOFT_LIMIT):
        delete_habit(add_habit(user, f"Лишняя {i}"))
    assert habit_guard.reward_lock(user, _today(user)) == "churn"
    _sql("UPDATE habit_churn SET day='2000-01-01' WHERE user_id=?", (user,))
    assert habit_guard.reward_lock(user, _today(user)) is None


def test_creating_many_habits_in_a_day_is_the_same_signal(uid):
    user = _user(uid)
    day = _today(user)
    for _ in range(habit_guard.ADD_SOFT_LIMIT):
        _sql("INSERT INTO habit_churn(user_id, day, kind, at) VALUES (?,?,'add',datetime('now','-1 hour'))", (user, day))
    assert habit_guard.reward_lock(user, day) == "churn"
    habit_guard.record(user, day, "add")                                           # следующее создание ставит подозрение
    assert _row("SELECT kind FROM abuse_flags WHERE user_id=?", (user,))["kind"] == "churn_add"


# ------------------------------------------------------------------------------------------------ пачка

def test_creating_ten_habits_inside_a_minute_is_blocked_and_flagged_once(uid):
    user = _user(uid)
    day = _today(user)
    for _ in range(habit_guard.ADD_BURST):
        _sql("INSERT INTO habit_churn(user_id, day, kind, at) VALUES (?,?,'add',datetime('now'))", (user, day))
    with pytest.raises(ValueError, match="habit_too_fast"):
        add_habit(user, "Одиннадцатая подряд")
    with pytest.raises(ValueError, match="habit_too_fast"):
        add_habit(user, "И ещё")
    assert _row("SELECT COUNT(*) AS n FROM abuse_flags WHERE user_id=? AND kind='burst'", (user,))["n"] == 1
    _slow(user)                                                                     # минута прошла
    assert add_habit(user, "Теперь можно")


async def test_the_route_answers_429_and_the_bot_intent_explains(client, uid):
    user = _user(uid)
    for _ in range(habit_guard.ADD_BURST):
        _sql("INSERT INTO habit_churn(user_id, day, kind, at) VALUES (?,?,'add',datetime('now'))", (user, _today(user)))
    headers = {"Authorization": f"tma {sign_init_data(user)}", "Content-Type": "application/json"}
    reply = await client.post("/api/habits", json={"title": "Йога"}, headers=headers)
    assert reply.status == 429 and (await reply.json())["error"] == "habit_too_fast"
    from habit_intents import try_handle_habit_intent
    text = try_handle_habit_intent(user, "добавь привычку Йога")
    assert text and text.startswith("⚠️ Слишком быстро")


def test_a_failing_guard_never_blocks_the_person(uid, monkeypatch):
    user = _user(uid)

    def broken():
        raise RuntimeError("база недоступна")
    monkeypatch.setattr(habit_guard, "connect", broken)
    habit_id = add_habit(user, "Работает")                                          # защита упала — привычку всё равно можно добавить и отметить
    assert complete_habit(habit_id)["coins"] > 0
    delete_habit(habit_id)


# ------------------------------------------------------------------------------------------------ подозрения и уведомления

def test_a_flag_is_one_per_person_day_and_kind_and_lands_in_analytics(uid):
    user = _user(uid)
    for i in range(habit_guard.DELETE_SOFT_LIMIT + 3):
        delete_habit(add_habit(user, f"Лишняя {i}"))
        _slow(user)
    flags = _row("SELECT COUNT(*) AS n FROM abuse_flags WHERE user_id=? AND kind='churn_delete'", (user,))["n"]
    assert flags == 1
    event = _row("SELECT step FROM user_events WHERE user_id=? AND name='abuse_flagged'", (user,))
    assert event["step"] == "churn_delete"


async def test_admins_get_one_message_per_flag(uid, monkeypatch):
    admin, user = uid + 50, _user(uid, "Накрутчик")
    monkeypatch.setattr(abuse_alerts, "ADMIN_IDS", [admin])
    habit_guard.flag(user, _today(user), "churn_delete", "удалено привычек за день: 9, создано: 9")
    bot = SimpleNamespace(send_message=AsyncMock())
    sent = await abuse_alerts.run_abuse_alerts(bot)
    mine = [c for c in bot.send_message.await_args_list if str(user) in c.args[1]]
    assert sent >= 1 and len(mine) == 1 and mine[0].args[0] == admin
    assert "Накрутчик" in mine[0].args[1] and "Подозрение на накрутку" in mine[0].args[1] and "Накрутка наград" in mine[0].args[1]
    bot.send_message.reset_mock()
    await abuse_alerts.run_abuse_alerts(bot)
    assert not [c for c in bot.send_message.await_args_list if str(user) in c.args[1]]       # второй раз — тишина


async def test_a_delivery_failure_to_an_admin_is_not_retried_forever(uid, monkeypatch):
    admin, user = uid + 50, _user(uid)
    monkeypatch.setattr(abuse_alerts, "ADMIN_IDS", [admin])
    habit_guard.flag(user, _today(user), "burst", "создано 10+ привычек меньше чем за минуту")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("сеть")))
    await abuse_alerts.run_abuse_alerts(bot)
    assert _row("SELECT notified_at FROM abuse_flags WHERE user_id=?", (user,))["notified_at"]


# ------------------------------------------------------------------------------------------------ заморозка админом

def test_a_freeze_stops_the_coins_and_lifting_it_brings_them_back(uid):
    user = _user(uid)
    habit_id, other = add_habit(user, "Первая"), add_habit(user, "Вторая")
    assert habit_guard.freeze_rewards(user, 24)["found"]
    assert habit_guard.frozen_until(user) is not None
    frozen = _pay(habit_id)
    assert frozen["coins"] == 0 and frozen["reward_cap_reason"] == "frozen" and "отключены" in frozen["reward_cap_text"]
    assert habit_guard.freeze_rewards(user, 0)["until"] is None and habit_guard.frozen_until(user) is None
    assert _pay(other)["coins"] > 0
    assert habit_guard.freeze_rewards(uid + 999, 24)["found"] is False


def _admin_headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}", "Content-Type": "application/json"}


async def test_admin_routes_list_flags_and_freeze(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin, user = uid + 50, _user(uid, "Подозреваемый")
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    habit_guard.flag(user, _today(user), "churn_delete", "удалено привычек за день: 9, создано: 9")

    assert (await client.get("/api/admin/abuse", headers=_admin_headers(user))).status == 403
    assert (await client.post(f"/api/admin/abuse/{user}/freeze", headers=_admin_headers(user), json={"hours": 24})).status == 403
    data = await (await client.get("/api/admin/abuse", headers=_admin_headers(admin))).json()
    item = next(i for i in data["items"] if i["user_id"] == user)
    assert item["name"] == "Подозреваемый" and item["label"] == "Много удалений привычек за день" and item["frozen_until"] is None
    assert data["limits"]["delete_per_day"] == habit_guard.DELETE_SOFT_LIMIT and data["limits"]["freeze_hours"] == [24, 168]

    assert (await client.post(f"/api/admin/abuse/{user}/freeze", headers=_admin_headers(admin), json={"hours": 5})).status == 400
    assert (await client.post(f"/api/admin/abuse/{user}/freeze", headers=_admin_headers(admin), data="{")).status == 400
    assert (await client.post(f"/api/admin/abuse/{uid + 999}/freeze", headers=_admin_headers(admin), json={"hours": 24})).status == 404
    ok = await client.post(f"/api/admin/abuse/{user}/freeze", headers=_admin_headers(admin), json={"hours": 168})
    assert ok.status == 200 and (await ok.json())["frozen_until"]
    card = await (await client.get(f"/api/admin/user/{user}", headers=_admin_headers(admin))).json()
    assert card["rewards_frozen_until"]
    after = await (await client.get("/api/admin/abuse", headers=_admin_headers(admin))).json()
    assert next(i for i in after["items"] if i["user_id"] == user)["frozen_until"]
    off = await client.post(f"/api/admin/abuse/{user}/freeze", headers=_admin_headers(admin), json={"hours": 0})
    assert off.status == 200 and (await off.json())["frozen_until"] is None
    assert (await (await client.get(f"/api/admin/user/{user}", headers=_admin_headers(admin))).json())["rewards_frozen_until"] is None


# ------------------------------------------------------------------------------------------------ приложение и админка

def test_the_app_shows_the_reason_and_knows_the_too_fast_error():
    body = APP[APP.index("async function celebrateHabitCompletion"):][:1000]
    assert "if (result.reward_capped)" in body and "result.reward_cap_text" in body
    assert body.index("if (result.reward_capped)") < body.index("${result.coins || 10}")
    assert APP.count("habit_too_fast:") == 2                                         # русская и английская подписи


def test_the_admin_panel_has_the_abuse_card_and_the_user_card_buttons():
    for needle in ("abuseBox", "/api/admin/abuse", "data-freeze", "freeze24Btn", "freeze168Btn", "freezeOffBtn", "Накрутка наград"):
        assert needle in ADMIN_HTML, needle


def test_the_job_is_scheduled():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "run_abuse_alerts" in main and "interval" in main.split("run_abuse_alerts, ")[1][:30]
