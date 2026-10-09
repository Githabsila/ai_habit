"""
ТЗ «AI-наставник, привычки и планировщик дня» (222.md) поверх «Адам пишет первым»:
окна 12:30–16:30 / 18:30–21:30 и время рядом с привычным для САМОГО человека; решение «писать или промолчать» через повод → приоритет →
cooldown; задачи как отдельный слой (привычки — ядро, «отдых» — полноценный план, задач нет — про них молчим); положительные отклонения;
привычка, которую обычно уже закрывают; допуск (человек в приложении / сам писал Адаму); продолжение разговора в чате с учётом причины.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

import adam_proactive
from db import add_user, get_habits
from db import adam_checkin as ac
from db.analytics import touch_last_seen
from db.core import connect
from tests.test_adam_checkin import FakeBot, TZ, _due, _events, _generate_returns, _habits, _local, _log, _ready_user, _user, flag_on  # noqa: F401
from webapp.services.ai_utils import build_user_context

_uid_counter = itertools.count(790_000_000, 100)


@pytest.fixture
def uid():
    ac._PREFERRED_CACHE.clear()
    return next(_uid_counter)


def _tstate(habits, tasks=(), hour=15, minute=0, pace=None, **extra):
    """Состояние для выбора повода: habits — [(название, выполнена)], tasks — [(название, выполнена, главная)]."""
    state = {
        "user_id": 1, "first_name": "Анна", "gender": "f", "hour": hour, "minute": minute, "time": f"{hour:02d}:{minute:02d}",
        "part": ac.part_of_day(hour), "weekday": 2, "hours_left": 7.0, "streak": 4, "best_streak": 4, "struggling": None,
        "struggling_done": None, "missed": None, "mood_hint": "", "main_age_min": None, "language": "ru", "style": "neutral",
        "habits": [{"id": i + 1, "title": t, "done": d, "planned": None} for i, (t, d) in enumerate(habits)],
        "tasks": [{"title": t, "done": d, "main": m} for t, d, m in tasks],
        "main_goal": next((t for t, _d, m in tasks if m), ""),
        "pace": pace or {"days": 8, "avg_by_now": 2.0, "avg_total": 3.5, "yesterday": 3, "active_7": 6},
        "task_hist": {"days_with_main": 0, "main_done_days": 0, "tasks_set": 0, "tasks_done": 0},
        "last_check_in_days": None, "recent_scenarios": [],
    }
    state.update(extra)
    return ac.fill_counts(state)


def _scenarios(state, slot, chat_day=False):
    return [c["scenario"] for c in ac.list_candidates(state, slot, chat_day)]


H3_DONE = [("Зарядка", True), ("Чтение", True), ("Английский", True)]


# --- заходы и привычное время ---------------------------------------------------------------------------------------

def test_app_opens_are_recorded_once_per_session_and_old_ones_are_dropped(uid):
    add_user(uid, "u", "Аня")
    touch_last_seen(uid)
    touch_last_seen(uid)                                    # тот же заход: ещё один запрос, не новый заход
    conn = connect()
    assert conn.execute("SELECT COUNT(*) AS n FROM app_opens WHERE user_id=?", (uid,)).fetchone()["n"] == 1
    conn.execute("UPDATE users SET last_seen=? WHERE telegram_id=?", ((datetime.now(timezone.utc) - timedelta(minutes=40)).replace(tzinfo=None).isoformat(), uid))
    conn.execute("INSERT INTO app_opens(user_id, at) VALUES (?, ?)", (uid, "2020-01-01 10:00:00"))
    conn.commit(); conn.close()
    touch_last_seen(uid)                                    # пауза 40 минут — новый заход, старые записи (45+ дней) удалены
    conn = connect()
    rows = conn.execute("SELECT at FROM app_opens WHERE user_id=?", (uid,)).fetchall()
    conn.close()
    assert len(rows) == 2 and all(r["at"] > "2021" for r in rows)


def _opens(uid_, hhmm_list, days=range(1, 6)):
    conn = connect()
    for k in days:
        day = (datetime.now(TZ) - timedelta(days=k)).date()
        for hour, minute in hhmm_list:
            moment = datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute), TZ).astimezone(timezone.utc)
            conn.execute("INSERT INTO app_opens(user_id, at) VALUES (?, ?)", (uid_, moment.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()


def test_profile_learns_the_persons_own_usual_time_and_needs_enough_days(uid):
    _user(uid)
    _opens(uid, [(13, 40), (19, 25)], days=range(1, 6))
    profile = ac.activity_profile(uid, _local(15, 0))
    assert profile["usual"] == {"day": 13 * 60 + 40, "evening": 19 * 60 + 25}
    few = uid + 1
    _user(few)
    _opens(few, [(13, 40)], days=range(1, 3))              # всего два дня — «обычно» говорить рано
    assert ac.activity_profile(few, _local(15, 0))["usual"] == {"day": None, "evening": None}


def test_profile_knows_the_usual_time_of_each_habit_and_the_first_habit_of_the_day(uid):
    _user(uid)
    rows = _habits(uid, ["Зарядка", "Тренировка"])
    _events(uid, [rows[0]["id"]], hours=(9,), days=range(1, 6))
    _events(uid, [rows[1]["id"]], hours=(18,), days=range(1, 6))
    profile = ac.activity_profile(uid, _local(15, 0))
    assert profile["first_habit"] == 9 * 60
    assert profile["habit_times"][rows[1]["id"]] == {"minute": 18 * 60, "days": 5}
    rare = uid + 1
    _user(rare)
    r = _habits(rare, ["Редкая"])
    _events(rare, [r[0]["id"]], hours=(18,), days=[1, 2, 3])        # три дня из 14 — «обычного времени» нет
    assert ac.activity_profile(rare, _local(15, 0))["habit_times"] == {}


def test_the_moment_is_next_to_the_usual_time_with_a_jitter_and_stays_inside_the_window(uid):
    """ТЗ §8: не ровно в 14:00 каждый день, а рядом с привычным временем человека ±20–40 минут."""
    lo, hi = ac.SLOT_WINDOWS["day"]
    shifts = set()
    for n in range(60):
        day = f"2026-10-{(n % 28) + 1:02d}"
        planned = ac.planned_minute(uid + n, day, "day", 13 * 60 + 40)
        assert lo <= planned < hi - ac.DELIVERY_MARGIN_MIN
        assert 20 <= abs(planned - (13 * 60 + 40)) <= 40, planned
        assert planned == ac.planned_minute(uid + n, day, "day", 13 * 60 + 40), "стабильно в течение дня"
        shifts.add(planned - (13 * 60 + 40))
    assert len(shifts) > 15, "разброс настоящий"
    assert ac.planned_minute(uid, "2026-10-08", "day", 12 * 60 + 35) >= lo, "у края окна — не выходим за окно"
    assert ac.planned_minute(uid, "2026-10-08", "day", 16 * 60 + 28) < hi - ac.DELIVERY_MARGIN_MIN
    assert ac.planned_minute(uid, "2026-10-08", "day") == ac.planned_minute(uid, "2026-10-08", "day", None), "нет данных — случайный в окне"


def test_slot_due_uses_the_persons_usual_time_and_the_profile_is_computed_once_a_day(uid, monkeypatch):
    now = _local(14, 0)
    planned = ac.planned_minute(uid, now.date().isoformat(), "day", 14 * 60)
    at = lambda minute: now.replace(hour=minute // 60, minute=minute % 60)
    preferred = {"day": 14 * 60, "evening": None}
    assert ac.slot_due(uid, at(planned - 1), preferred) is None and ac.slot_due(uid, at(planned), preferred) == "day"
    calls = []
    real = ac.activity_profile
    monkeypatch.setattr(ac, "activity_profile", lambda *a: calls.append(1) or real(*a))
    for _ in range(5):
        ac.preferred_minutes(uid, now)
    assert len(calls) == 1


# --- повод → приоритет ---------------------------------------------------------------------------------------------

def test_habits_are_the_core_the_main_task_becomes_a_reason_only_when_habits_are_fine():
    tasks = [("Закончить презентацию", False, True), ("Ответить клиенту", False, False)]
    fine = _tstate(H3_DONE, tasks, main_age_min=300)
    assert _scenarios(fine, "day")[0] == "task_progress", "ТЗ §14: с привычками хорошо, а главная стоит"
    behind = _tstate([("Зарядка", False), ("Чтение", False), ("Английский", False)], tasks, main_age_min=300)
    assert _scenarios(behind, "day")[0] == "zero_done" and "task_progress" not in _scenarios(behind, "day"), "отстающие привычки перекрывают задачу"
    lag = _tstate([("Зарядка", True), ("Чтение", False), ("Английский", False), ("Вода", False), ("Сон", False)], tasks, main_age_min=300,
                  pace={"days": 8, "avg_by_now": 3.0, "avg_total": 4.5, "yesterday": 4, "active_7": 7})
    assert "behind" in _scenarios(lag, "day") and "task_progress" not in _scenarios(lag, "day")
    done_main = _tstate([("Зарядка", False), ("Чтение", False), ("Английский", False)], [("Закончить презентацию", True, True)], main_age_min=300)
    assert _scenarios(done_main, "day")[0] == "zero_done", "главная закрыта, привычки отстают — внимание обратно на привычки"


def test_a_main_task_that_was_just_planned_is_not_nagged_about():
    tasks = [("Закончить презентацию", False, True)]
    steady = {"days": 8, "avg_by_now": 3.0, "avg_total": 3.5, "yesterday": 3, "active_7": 6}
    assert _scenarios(_tstate(H3_DONE, tasks, main_age_min=20, pace=steady), "day") == []
    assert _scenarios(_tstate(H3_DONE, tasks, main_age_min=180, pace=steady), "day") == ["task_progress"]
    only_main = _tstate(H3_DONE, tasks, main_age_min=180, pace=steady)
    assert ac.list_candidates(only_main, "day")[0]["priority"] == 78, "последнее, что осталось, — главная задача: она важнее общего «остался один пункт»"


def test_rest_is_a_valid_plan_and_never_a_reason_to_press():
    """ТЗ §3, §14: «отдохнуть» в главной задаче — не низкая продуктивность."""
    for title in ("Отдохнуть", "Съездить к другу", "Погулять с семьёй", "Выходной", "Восстановиться после недели"):
        assert ac.rest_like(title), title
    for title in ("Закончить отчёт", "Позвонить клиенту", "Сделать лендинг"):
        assert not ac.rest_like(title), title
    state = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", False), ("Вода", False)], [("Отдохнуть и восстановиться", False, True)],
                    main_age_min=400, last_check_in_days=2)
    assert state["main_is_rest"] and state["left"] == 2, "открытый «отдых» не считается оставшейся работой"
    assert state["pending_tasks"] == [] and "Отдохнуть" not in " ".join(state["pending"])
    assert _scenarios(state, "day", True) == []
    assert "task_progress" not in _scenarios(state, "day") and "task_remaining" not in _scenarios(state, "evening")
    scenario, reason = ac.pick_scenario(state, "day", False)
    assert scenario is None and "отдых" in reason
    closed = _tstate(H3_DONE, [("Отдохнуть", False, True)], hour=20)
    assert closed["left"] == 0 and _scenarios(closed, "evening", True) == ["all_done"], "привычки закрыты — день закрыт, отдых не мешает"
    only_rest = _tstate([], [("Отдохнуть", False, True)])
    assert only_rest["total"] == 0 and ac.pick_scenario(only_rest, "day", False) == (None, "главная задача дня — отдых: это нормальный план, торопить незачем")
    prompt = ac.build_checkin_prompt(_tstate(H3_DONE, [("Отдохнуть", False, True)]), "positive", "no_greeting")
    assert "полноценный план" in prompt and "не требуй работы" in prompt


def test_no_tasks_means_the_assistant_is_told_not_to_mention_them():
    """ТЗ §4: задач нет — не спрашивать, не придумывать, не замечать их отсутствие."""
    habits_only = _tstate([("Зарядка", False), ("Чтение", False)])
    prompt = ac.build_checkin_prompt(habits_only, "zero_done", "no_greeting")
    assert "Задач на сегодня у человека нет — НЕ упоминай задачи, планирование" in prompt
    assert "Привычек на сегодня выполнено: 0 из 2" in prompt and "задачи 0/0" not in prompt
    with_tasks = ac.build_checkin_prompt(_tstate([("Зарядка", False)], [("Позвонить", False, True)]), "zero_done", "no_greeting")
    assert "НЕ упоминай задачи" not in with_tasks and "Главная задача дня: «Позвонить» — не выполнена" in with_tasks
    for _ in range(5):
        text = ac.fallback_text(habits_only, "zero_done", 3, "2026-10-08")
        assert "задач" not in text.lower() and "план" not in text.lower(), text
    tasks_only = _tstate([], [("Закончить отчёт", False, True)], main_age_min=300)
    assert _scenarios(tasks_only, "day") == ["task_progress"] and "Задач на сегодня выполнено: 0 из 1" in ac.build_checkin_prompt(tasks_only, "task_progress", "no_greeting")


def test_evening_task_reason_wins_only_when_habits_are_closed():
    tasks = [("Закончить презентацию", False, True), ("Ответить клиенту", True, False)]
    closed = _tstate(H3_DONE, tasks, hour=20, main_age_min=600)
    assert ac.list_candidates(closed, "evening")[0]["scenario"] == "task_remaining"
    still = _tstate([("Зарядка", True), ("Чтение", False), ("Английский", False)], tasks, hour=20, main_age_min=600)
    top = ac.list_candidates(still, "evening")[0]["scenario"]
    assert top == "evening_left" and "task_remaining" not in _scenarios(still, "evening"), "привычки ещё не закрыты — задача не перетягивает внимание"
    nothing = _tstate([("Зарядка", False), ("Чтение", False)], tasks, hour=20)
    assert ac.list_candidates(nothing, "evening")[0]["scenario"] == "zero_done_evening", "серия держится на привычках: задача её не спасёт"


def test_good_days_are_reasons_too():
    """ТЗ §9, §20: не только провалы — «сегодня уже впереди своего обычного темпа», «сделал ту, что чаще пропускаешь»."""
    ahead = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", True), ("Вода", False), ("Сон", False)],
                    pace={"days": 8, "avg_by_now": 1.5, "avg_total": 3.5, "yesterday": 3, "active_7": 6})
    assert ahead["ahead"] and _scenarios(ahead, "day") == ["positive"]
    usual = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", False), ("Вода", False), ("Сон", False)])
    assert not usual["ahead"] and _scenarios(usual, "day") == []
    rare = _tstate([("Зарядка", True), ("Чтение", False), ("Английский", False), ("Вода", False)], struggling_done="Зарядка")
    assert ac.pick_scenario(rare, "day")[0] == "positive"
    text = ac.fallback_text(rare, "positive", 5, "2026-10-08")
    assert "Зарядка" in text and ac.is_acceptable(text, "positive")
    prompt = ac.build_checkin_prompt(rare, "positive", "no_greeting")
    assert "Сегодня выполнена «Зарядка» — привычка, которую человек чаще всего пропускает" in prompt


def test_a_habit_that_is_usually_done_by_now_is_noticed_and_planned_times_count_too():
    habits = [{"id": 7, "title": "Тренировка", "done": False, "planned": None}, {"id": 8, "title": "Зарядка", "done": True, "planned": None}]
    profile = {"habit_times": {7: {"minute": 18 * 60 + 20, "days": 6}}}
    late = ac._missed_habit(habits, profile, 20 * 60)
    assert late == {"title": "Тренировка", "usual_minute": 18 * 60 + 20, "late_min": 100, "kind": "typical", "days": 6}
    assert ac._missed_habit(habits, profile, 19 * 60 + 30) is None, "опоздание меньше 90 минут — ещё рано"
    assert ac._missed_habit(habits, profile, 22 * 60 + 30) is None, "после 22:00 уже не пишем"
    planned = [{"id": 9, "title": "Лекарство", "done": False, "planned": "13:00"}]
    assert ac._missed_habit(planned, {"habit_times": {}}, 15 * 60)["kind"] == "planned"
    state = _tstate([("Зарядка", True), ("Чтение", True), ("Тренировка", False), ("Английский", False), ("Вода", False)], hour=20, missed=late)
    assert ac.list_candidates(state, "evening")[0]["scenario"] == "missed_habit"
    assert "обычно выполняет около 18:20" in ac.build_checkin_prompt(state, "missed_habit", "no_greeting")
    text = ac.fallback_text(state, "missed_habit", 4, "2026-10-08")
    assert "Тренировка" in text and ac.is_acceptable(text, "missed_habit")


def test_check_in_is_rare_and_only_on_chat_days_when_there_is_nothing_else():
    quiet = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", False), ("Вода", False)])
    assert _scenarios(quiet, "day", chat_day=False) == []
    assert _scenarios(quiet, "day", chat_day=True) == ["check_in"]
    assert _scenarios(dict(quiet, last_check_in_days=5), "day", chat_day=True) == [], "раз в две недели, не чаще"
    assert _scenarios(dict(quiet, last_check_in_days=20), "day", chat_day=True) == ["check_in"]
    assert ac.is_acceptable("Как проходит день?", "check_in") and not ac.is_acceptable("Как проходит день?", "zero_done")
    assert not ac.is_acceptable("Привет! Как дела?", "check_in"), "«как дела» запрещено везде"


def test_every_scenario_has_an_intent_and_the_demo_scenarios_pick_what_they_are_named_after():
    assert set(ac.INTENT_LABELS) == set(ac.SCENARIOS)
    labs = ac.lab_scenarios()
    expected = {"positive": "positive", "positive_habit": "positive", "missed_habit": "missed_habit", "task_progress": "task_progress",
                "task_remaining": "task_remaining", "check_in": "check_in", "rest_day": None, "on_pace": None}
    for key, scenario in expected.items():
        _title, slot, state = labs[key]
        assert ac.pick_scenario(state, slot, chat_day=True)[0] == scenario, key
    assert ac.pick_scenario(labs["rest_day"][2], "day", True)[1].startswith("главная задача дня — отдых")
    assert ac.pick_scenario(labs["on_pace"][2], "day", True)[1] == "темп в норме"


# --- cooldown и тишина ---------------------------------------------------------------------------------------------

def test_the_same_reason_is_not_repeated_and_silence_is_a_valid_answer():
    """ТЗ §9, §17, §23: не крутить один повод по кругу; нет повода — не писать."""
    zero = _tstate([("Зарядка", False), ("Чтение", False), ("Английский", False)])
    assert ac.choose_scenario(zero, "day", False, [])[0] == "zero_done"
    scenario, reason, candidates = ac.choose_scenario(zero, "day", False, ["zero_done"])
    assert scenario is None and "уже был" in reason and candidates[0]["scenario"] == "zero_done"
    assert ac.choose_scenario(zero, "day", False, ["behind", "one_left"])[0] == "zero_done", "в последних двух его не было"
    assert ac.choose_scenario(zero, "day", False, ["behind", "check_in", "zero_done"])[0] == "zero_done", "три сообщения назад — уже можно"
    evening = _tstate([("Зарядка", False), ("Чтение", False)], hour=20, streak=12)
    assert ac.choose_scenario(evening, "evening", False, ["zero_done_evening"])[0] == "zero_done_evening", "защита длинной серии важнее cooldown"
    low = _tstate([("Зарядка", False), ("Чтение", False)], hour=20, streak=1)
    assert ac.choose_scenario(low, "evening", False, ["zero_done_evening"])[0] is None
    two = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", False), ("Вода", False), ("Сон", False)], hour=20,
                  missed={"title": "Английский", "usual_minute": 17 * 60, "late_min": 180, "kind": "typical", "days": 6})
    assert [c["scenario"] for c in ac.list_candidates(two, "evening")][:2] == ["missed_habit", "evening_left"]
    assert ac.choose_scenario(two, "evening", False, ["missed_habit"])[0] == "evening_left", "первый повод был — берём следующий"
    fine = _tstate([("Зарядка", True), ("Чтение", True), ("Английский", False), ("Вода", False)])
    assert ac.choose_scenario(fine, "day", False, [])[:2] == (None, "темп в норме")


def test_numbers_and_toxic_wording_are_kept_out_of_messages():
    for bad in ("Ты опять ничего не сделал.", "Ты уже потерял полдня. Что случилось?", "Почему ты ленишься?", "Лентяй, вставай."):
        assert not ac.is_acceptable(bad, "zero_done"), bad
    for good in (
        "Сегодня ты заметно выбился из своего обычного ритма. Всё нормально?",
        "Уже середина дня, а у тебя пока ни одной отметки. Это не похоже на твой обычный ритм. Что-то сегодня изменилось?",
        "День почти закрыт. Осталась одна привычка — получится закончить её сегодня?",
        "Сегодня ты закрыл всё. Что помогло так хорошо держать темп?",
        "Главная задача пока осталась открытой. Получится вернуться к ней сегодня?",
    ):
        assert ac.is_acceptable(good, "anomaly_like"), good


# --- допуск планировщика -------------------------------------------------------------------------------------------

async def test_no_push_when_the_person_is_in_the_app_or_started_the_dialog_themselves(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()
    chatty = uid
    _ready_user(chatty)
    from db.ai import add_ai_message
    add_ai_message(chatty, "user", "привет, Адам")
    assert "сам уже общался" in await adam_proactive.process_user(bot, chatty, "s", _due(chatty, "day"))

    online = uid + 1
    _ready_user(online)
    conn = connect()
    conn.execute("UPDATE users SET last_seen=? WHERE telegram_id=?", ((datetime.now(timezone.utc) - timedelta(minutes=3)).replace(tzinfo=None).isoformat(), online))
    conn.commit(); conn.close()
    assert "прямо сейчас в приложении" in await adam_proactive.process_user(bot, online, "s", _due(online, "day"))

    away = uid + 2
    _ready_user(away)
    conn = connect()
    conn.execute("UPDATE users SET last_seen=? WHERE telegram_id=?", ((datetime.now(timezone.utc) - timedelta(minutes=50)).replace(tzinfo=None).isoformat(), away))
    conn.commit(); conn.close()
    assert (await adam_proactive.process_user(bot, away, "s", _due(away, "day"))).startswith("sent")


async def test_another_push_an_hour_ago_holds_adam_back_in_both_windows(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    _ready_user(uid)
    conn = connect(); conn.execute("INSERT INTO notification_log(user_id, category, title) VALUES (?, 'habit_checkpoint_12', 'x')", (uid,)); conn.commit(); conn.close()
    assert "другой пуш" in await adam_proactive.process_user(FakeBot(), uid, "s", _due(uid, "day"))


async def test_the_planner_does_not_repeat_the_last_reason_and_records_why(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    _ready_user(uid, with_history=False)                    # без истории: из поводов есть только «ни одной привычки»
    conn = connect()
    for back in (1, 2):
        day = (datetime.now(TZ).date() - timedelta(days=back)).isoformat()
        conn.execute("INSERT INTO adam_checkins(user_id, day, slot, status, scenario) VALUES (?,?, 'day', 'sent', 'zero_done')", (uid, day))
    conn.commit(); conn.close()
    outcome = await adam_proactive.process_user(FakeBot(), uid, "s", _due(uid, "day"))
    assert outcome.startswith("skipped") and "уже был" in outcome
    row = [r for r in _log(uid) if r["status"] == "skipped"][0]
    assert "уже был" in row["note"], "причина тишины записана в журнал"


async def test_the_tick_uses_the_persons_usual_time(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    _ready_user(uid)
    _opens(uid, [(13, 40)], days=range(1, 6))
    now = _local(13, 0)
    planned = ac.planned_minute(uid, now.date().isoformat(), "day", 13 * 60 + 40)
    assert 13 * 60 <= planned <= 14 * 60 + 20
    bot = FakeBot()
    before = now.replace(hour=(planned - 1) // 60, minute=(planned - 1) % 60)
    assert await adam_proactive.process_user(bot, uid, "s", before) == "not_due"
    at = now.replace(hour=planned // 60, minute=planned % 60)
    assert (await adam_proactive.process_user(bot, uid, "s", at)).startswith("sent")


# --- разговор в чате: Адам помнит, почему написал -------------------------------------------------------------------

async def test_the_chat_knows_why_adam_wrote_and_what_he_said(uid, flag_on, monkeypatch):
    _ready_user(uid)
    assert ac.last_proactive_context(uid) == "" and "Ты сам начал этот разговор" not in build_user_context(uid)
    _generate_returns(monkeypatch, "Тихо сегодня — всё в порядке? Начни с «Зарядки».")
    assert (await adam_proactive.process_user(FakeBot(), uid, "s", _due(uid, "day"))).startswith("sent")
    context = build_user_context(uid)
    assert "Ты сам начал этот разговор: сообщением в Telegram написал человеку «Тихо сегодня — всё в порядке? Начни с «Зарядки».»" in context
    assert "повод ANOMALY; причина: до сих пор ничего не выполнено" in context
    assert "веди ту же нить" in context and "предложи одно самое простое действие" in context

    conn = connect()
    conn.execute("UPDATE adam_checkins SET created_at=datetime('now','-12 hours') WHERE user_id=?", (uid,))
    conn.commit(); conn.close()
    assert ac.last_proactive_context(uid) == "", "через 10 часов нить разговора уже не держим"


async def test_the_first_message_in_the_chat_is_remembered_the_same_way(client, uid, flag_on, monkeypatch):
    from tests.test_ai_nudge import _user as nudge_user
    from tests.conftest import sign_init_data
    nudge_user(uid, chatted_before=True)
    _habits(uid, ["Зарядка", "Чтение"], done=1)
    _generate_returns(monkeypatch, "Остался один шаг — «Чтение». Найдёшь время до сна?")
    real_pick = ac.pick_scenario                                      # до 12:00 UTC (пояс по умолчанию) — «утро» без вопросов: час фиксируем
    monkeypatch.setattr(ac, "pick_scenario", lambda state, slot, chat_day=False: real_pick({**state, "hour": 15}, slot, chat_day))
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    assert (await reply.json())["greeting"]["message"].startswith("Остался один шаг")
    assert "в чате при заходе написал человеку «Остался один шаг" in ac.last_proactive_context(uid)


def test_the_prompt_lists_recent_reasons_the_task_history_and_the_time_left():
    state = _tstate(H3_DONE, [("Закончить презентацию", False, True)], hour=19, minute=30, main_age_min=600,
                    task_hist={"days_with_main": 5, "main_done_days": 4, "tasks_set": 9, "tasks_done": 7}, hours_left=2.5)
    prompt = ac.build_checkin_prompt(state, "task_remaining", "no_greeting", ["Вчера ты хорошо шёл."], ["zero_done", "check_in"])
    assert "до конца дня около 2.5 ч" in prompt
    assert "Главную задачу человек обычно закрывает: 4 из 5 последних дней" in prompt
    assert "План дня составлен около 10 ч назад, главная задача всё это время без отметки" in prompt
    assert "Поводы последних сообщений (не повторяй тот же): ANOMALY, CHECK_IN" in prompt
    assert "Сценарий: " + ac.SCENARIO_GUIDE["task_remaining"] in prompt


def test_task_history_counts_the_last_seven_days(uid):
    _user(uid)
    conn = connect()
    for back, (main, done, n, d) in enumerate([("Отчёт", 1, 3, 2), ("Лендинг", 0, 2, 0), ("", 0, 1, 1), ("Презентация", 1, 0, 0)], start=1):
        day = (datetime.now(TZ).date() - timedelta(days=back)).isoformat()
        cur = conn.execute("INSERT INTO daily_plans(user_id, plan_date, main_goal, main_goal_completed) VALUES (?,?,?,?)", (uid, day, main, done))
        for i in range(n):
            conn.execute("INSERT INTO daily_plan_tasks(plan_id, text, completed) VALUES (?,?,?)", (cur.lastrowid, f"Задача {i}", 1 if i < d else 0))
    conn.commit(); conn.close()
    from db.daily_plan import _effective_plan_date
    hist = ac._task_history(uid, _effective_plan_date())
    assert hist["days_with_main"] == 3 and hist["main_done_days"] == 2 and hist["tasks_set"] == 6 and hist["tasks_done"] == 3


def test_documented_in_the_module_and_wired_to_the_job():
    assert "ТЗ 222.md" in (ac.__doc__ or "") and "12:30–16:30" in ac.__doc__
    import inspect
    source = inspect.getsource(adam_proactive.process_user)
    assert "choose_scenario(" in source and "preferred_minutes(" in source and "window_slot(" in source
    skip = inspect.getsource(adam_proactive.skip_reason)
    assert "chatted_today(" in skip and "ACTIVE_NOW_MINUTES" in skip
