"""
Адам «не видел» привычку (14.10): человек добавил вторую привычку, а Адам по-прежнему отвечал «у тебя одна». Причины, найденные в коде:
кэш ответов в Mini App (12 часов) не учитывал данные человека (в боте это уже было сделано), а список привычек для модели не называл
их число — модель принимала «привычки со временем» за все привычки и потом держалась за своё же прошлое сообщение.
"""
import itertools

import pytest

from db import add_habit, add_user
from db.daily_plan import set_daily_main_goal
from tests.conftest import sign_init_data
from webapp.services.ai_utils import build_user_context, context_fingerprint

_counter = itertools.count(880_000_000, 10)


@pytest.fixture
def uid():
    return next(_counter)


def _person(uid_):
    add_user(uid_, f"u{uid_}", "Александр")


def test_the_context_lists_every_habit_and_says_how_many_there_are(uid):
    _person(uid)
    add_habit(uid, "Проверка", category="other", priority=2)
    add_habit(uid, "Ежедневный шаг к цели", planned_time="23:00")
    context = build_user_context(uid)
    assert "• Проверка — нет" in context and "• Ежедневный шаг к цели — нет — время: 23:00" in context
    assert "Всего привычек: 2 (выполнено 0, не выполнено 2)" in context and "в том числе без времени" in context
    assert "Привычек пока не добавлено" in build_user_context(_empty(uid + 1))


def _empty(uid_):
    _person(uid_)
    return uid_


def test_the_fingerprint_follows_the_data_and_the_hour_but_not_the_minute(uid):
    _person(uid)
    add_habit(uid, "Зарядка")
    one = build_user_context(uid)
    add_habit(uid, "Чтение")
    two = build_user_context(uid)
    assert context_fingerprint(one) != context_fingerprint(two), "новая привычка — другой отпечаток"
    base = "Текущее локальное время пользователя: 14:05\nУровень: 3\nСерия дней подряд: 4"
    assert context_fingerprint(base) == context_fingerprint(base.replace("14:05", "14:48")), "минуты кэш не ломают"
    assert context_fingerprint(base) != context_fingerprint(base.replace("14:05", "20:05")), "а вечерний ответ не берётся из дневного кэша"
    assert context_fingerprint(base) != context_fingerprint(base.replace("Серия дней подряд: 4", "Серия дней подряд: 5"))
    set_daily_main_goal(uid, "План 1.1")
    assert context_fingerprint(build_user_context(uid)) != context_fingerprint(two), "план дня тоже влияет"


async def test_a_cached_answer_is_not_served_after_the_habits_changed(client, uid, monkeypatch):
    import webapp.routes_ai_miniapp as route_mod
    _person(uid)
    add_habit(uid, "Ежедневный шаг к цели", planned_time="23:00")
    monkeypatch.setattr(route_mod, "try_handle_habit_intent", lambda *a, **k: None)
    monkeypatch.setattr(route_mod, "_looks_like_habit_action", lambda *a, **k: False)
    monkeypatch.setattr(route_mod, "_is_throttled", lambda *a, **k: None)
    seen = []

    async def fake_solve(**kwargs):
        seen.append(kwargs["user_context"])
        return {"answer": f"Привычек в списке: {kwargs['user_context'].count('• ')}", "is_crisis": False, "suggested_habit": None, "complexity": "просто"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", fake_solve)
    body = {"init_data": sign_init_data(uid), "message": "сколько у меня привычек"}
    first = await (await client.post("/api/ai/chat", json=body)).json()
    again = await (await client.post("/api/ai/chat", json=body)).json()
    assert len(seen) == 1 and again["answer"] == first["answer"], "те же данные — ответ из кэша, как и раньше"
    add_habit(uid, "Проверка")
    after = await (await client.post("/api/ai/chat", json=body)).json()
    assert len(seen) == 2 and "Проверка" in seen[1], "привычка добавлена — Адам отвечает заново и видит её"
    assert after["answer"] != first["answer"]


def test_the_chat_rules_forbid_talking_about_the_data_and_ask_to_count_every_habit():
    import multi_agent
    persona = multi_agent.BASE_PERSONA
    for needle in ("ВСЕ привычки из списка", "он прав: проверь список", "верь данным", "в переданных данных", "в трекере", "не попала в"):
        assert needle in persona, needle


def test_the_snapshot_says_the_timed_habit_is_not_the_only_one():
    from db import adam_checkin as ac
    from webapp.services.chat_brain import situation_lines
    _t, _slot, state = ac.lab_scenarios()["zero_done"]
    state = dict(state, habits=[{"id": 1, "title": "Проверка", "done": False, "planned": None},
                                {"id": 2, "title": "Ежедневный шаг к цели", "done": False, "planned": "23:00"}])
    ac.fill_counts(state)
    text = "\n".join(situation_lines(state))
    assert "Ближайшая по времени привычка: «Ежедневный шаг к цели» в 23:00 (это не единственная привычка на сегодня" in text
    one = dict(state, habits=[state["habits"][1]])
    assert "не единственная" not in "\n".join(situation_lines(ac.fill_counts(one)))
