"""
Короткая личная сводка (ТЗ «Триал, подписка и приглашения», 28.10.2026, §11): только реальные данные, честно про «данных мало», не называется недельным анализом,
после семи дней объясняет, что появится подробный разбор.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from db import add_habit, add_user, set_timezone, start_trial_mode
from db import access_state as acs
from db.core import connect
from db.trial_summary import build_summary
from tests.conftest import sign_init_data

UTC = timezone.utc
ADMIN = 777
_counter = itertools.count(862_200_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    """Выполняет запрос; для SELECT возвращает первую строку."""
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _person(uid_, started_days_ago=2):
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, "UTC")
    start_trial_mode(uid_, ADMIN)
    acs.set_trial_start(uid_, datetime.now(UTC).replace(tzinfo=None) - timedelta(days=started_days_ago, hours=1))
    return uid_


def _mark(uid_, habit_id, days_ago=0):
    moment = (datetime.now(UTC) - timedelta(days=days_ago, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?, ?, ?)", (uid_, habit_id, moment))


def _task(uid_, text, done=False, days_ago=0, goal=None, goal_done=False):
    day = (datetime.now(UTC) - timedelta(days=days_ago)).date().isoformat()
    _sql("INSERT OR IGNORE INTO daily_plans(user_id, plan_date, main_goal, main_goal_completed) VALUES (?, ?, ?, ?)", (uid_, day, goal or "", 1 if goal_done else 0))
    row = _sql("SELECT id FROM daily_plans WHERE user_id=? AND plan_date=?", (uid_, day))
    _sql("INSERT INTO daily_plan_tasks(plan_id, text, completed) VALUES (?, ?, ?)", (row["id"], text, 1 if done else 0))


def test_with_almost_no_data_the_summary_says_so_and_suggests_a_first_step(uid):
    _person(uid)
    add_habit(uid, "Зарядка")
    summary = build_summary(uid)
    assert summary["enough"] is False
    assert summary["lines"][0] == "Пока данных мало для выводов: привычек добавлено — 1."
    assert "Начни с одного посильного действия сегодня" in summary["lines"][1]
    assert "недельн" not in summary["title"].lower() and summary["title"].startswith("Коротко за 3 дня")
    assert "После семи и больше дней использования появится больше материала для подробного недельного разбора" in summary["note"]


def test_the_summary_counts_real_marks_tasks_goals_and_chats_and_names_the_difficulty(uid):
    _person(uid, started_days_ago=2)
    exercise, reading, water = add_habit(uid, "Зарядка"), add_habit(uid, "Читать 20 минут"), add_habit(uid, "Вода")
    for days_ago in (0, 1, 2):
        _mark(uid, exercise, days_ago)
    _mark(uid, reading, 1)
    _task(uid, "Позвонить маме", done=True, days_ago=1, goal="Закончить отчёт", goal_done=True)
    _task(uid, "Купить продукты", done=False, days_ago=0, goal="Записаться к врачу")
    _sql("INSERT INTO ai_messages(user_id, role, message) VALUES (?, 'user', 'привет')", (uid,))
    _sql("UPDATE users SET streak=3 WHERE telegram_id=?", (uid,))
    summary = build_summary(uid)
    text = " ".join(summary["lines"])
    assert summary["enough"] is True
    assert "Привычки: отмечено 4 раза в 3 из 3 дней." in text
    assert "Чаще всего повторялась «Зарядка» — 3 раза." in text
    assert "Главная задача была записана в 2 из 3 дней, выполнена в 1." in text
    assert "План дня: добавлено задач — 2, выполнено — 1." in text
    assert "Мы поговорили: 1 сообщение от тебя." in text
    assert "Пока без отметок: «Вода»." in text and "посильный первый шаг" in text, "трудность названа по реальным данным"
    assert "Ударный режим сейчас: 3 дня подряд." in text
    assert "недельн" not in summary["title"].lower()


def test_what_happened_before_the_trial_started_is_not_counted(uid):
    _person(uid, started_days_ago=1)
    habit = add_habit(uid, "Зарядка")
    _mark(uid, habit, days_ago=10)
    _mark(uid, habit, days_ago=0)
    assert build_summary(uid)["data"]["marks_total"] == 1


def test_after_a_week_there_is_no_promise_about_a_later_review(uid):
    _person(uid, started_days_ago=8)
    summary = build_summary(uid)
    assert summary["note"] is None and summary["title"].startswith("Коротко за 9 дней")


async def test_the_summary_is_available_in_the_extra_window_and_tracked(client, uid):
    _person(uid, started_days_ago=3)                                  # день 4 — дополнительное окно
    habit = add_habit(uid, "Зарядка")
    for days_ago in (0, 1, 2):
        _mark(uid, habit, days_ago)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    response = await client.get("/api/trial/summary", headers=headers)
    body = await response.json()
    assert response.status == 200 and body["enough"] is True and body["lines"][0].startswith("Привычки: отмечено 3 раза")
    assert _sql("SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND name='trial_summary_shown'", (uid,))["n"] == 1
    assert set(body) == {"title", "lines", "note", "enough"}
