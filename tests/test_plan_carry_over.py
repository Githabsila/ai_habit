"""
Перенос незакрытого на завтра (план, волна 1, п.4): копии незакрытого в завтрашний план, сегодняшний не меняется, повтор не
дублирует, главная задача становится главной на завтра, лимит 9 задач. Кнопка — под вечерней сверкой 19:00 (у админов и по флагу).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
import handlers.plan_bot as plan_bot
from db import add_user
from db.daily_plan import (
    add_daily_task, carry_over_unfinished, get_daily_plan, set_daily_main_goal, toggle_daily_task, tomorrow_plan_date,
)
from tests.conftest import sign_init_data


def _plan(uid):
    add_user(uid, "a", "Аня")
    set_daily_main_goal(uid, "Сдать отчёт")
    done = add_daily_task(uid, "Зарядка")
    add_daily_task(uid, "Позвонить маме")
    add_daily_task(uid, "Купить продукты")
    toggle_daily_task(done)


def test_unfinished_items_are_copied_to_tomorrow_once(uid):
    _plan(uid)
    result = carry_over_unfinished(uid)
    assert result == {"moved": 3, "skipped": 0, "main": True}
    tomorrow = get_daily_plan(uid, tomorrow_plan_date(uid))
    assert tomorrow["main_goal"] == "Сдать отчёт"
    assert [t["text"] for t in tomorrow["tasks"]] == ["Позвонить маме", "Купить продукты"], "закрытая «Зарядка» не переносится"
    today = get_daily_plan(uid)
    assert len(today["tasks"]) == 3 and today["main_goal"] == "Сдать отчёт", "сегодняшний план не меняется"
    assert carry_over_unfinished(uid)["moved"] == 0, "повтор не дублирует"


def test_main_goal_becomes_a_task_when_tomorrow_already_has_one(uid):
    _plan(uid)
    tomorrow = tomorrow_plan_date(uid)
    add_daily_task(uid, "Уже запланировано", plan_date=tomorrow)
    from db.core import connect
    conn = connect()
    try:
        conn.execute("UPDATE daily_plans SET main_goal='Главное завтра' WHERE user_id=? AND plan_date=?", (uid, tomorrow))
        conn.commit()
    finally:
        conn.close()
    result = carry_over_unfinished(uid)
    plan = get_daily_plan(uid, tomorrow)
    assert result["main"] is False and plan["main_goal"] == "Главное завтра"
    assert "Сдать отчёт" in [t["text"] for t in plan["tasks"]]


def test_the_limit_of_9_is_kept(uid):
    add_user(uid, "a", "Аня")
    for i in range(3):
        add_daily_task(uid, f"Сегодня {i}")
    tomorrow = tomorrow_plan_date(uid)
    for i in range(8):
        add_daily_task(uid, f"Завтра {i}", plan_date=tomorrow)
    result = carry_over_unfinished(uid)
    assert result == {"moved": 1, "skipped": 2, "main": False}
    assert len(get_daily_plan(uid, tomorrow)["tasks"]) == 9


async def test_the_bot_button_carries_and_reports(uid, monkeypatch):
    _plan(uid)
    message = SimpleNamespace(edit_reply_markup=AsyncMock(), answer=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=uid), message=message, answer=AsyncMock())
    await plan_bot.plan_carry(callback)
    text = message.answer.await_args.args[0]
    assert "Перенёс на завтра: 3" in text and "главной на завтра" in text
    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


async def test_the_evening_check_shows_the_button_only_where_enabled(uid, monkeypatch):
    import coach
    monkeypatch.setattr(plan_bot, "ADMIN_IDS", [uid])
    assert coach._carry_markup(uid).inline_keyboard[0][0].callback_data == "plan_carry"
    assert coach._carry_markup(uid + 1) is None


async def test_the_api_route_is_behind_the_flag(client, uid, monkeypatch):
    _plan(uid)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    assert (await client.post("/api/plan/carry-over", headers=headers)).status == 403
    monkeypatch.setattr(plan_bot, "ADMIN_IDS", [uid])
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    response = await client.post("/api/plan/carry-over", headers=headers)
    assert response.status == 200 and (await response.json())["moved"] == 3
