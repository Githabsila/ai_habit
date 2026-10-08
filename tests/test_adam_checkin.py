"""
«Адам пишет первым»: контекстные сообщения наставника (db/adam_checkin.py, adam_proactive.py).

Проверяем по пунктам ТЗ: состояние человека включает и привычки, и ЗАДАЧИ; окна 14:00–16:30 и 19:30–21:30 со случайным (но стабильным)
моментом; «поболтать» 2–3 дня в неделю не подряд; утром вопросов нет; днём пишем только тем, кто отстаёт от СВОЕГО темпа; не больше
одного сообщения в день; никаких «Как дела?»; уважение к тихим часам, тумблеру напоминаний и блокировке бота; раскатка по флагу.
"""
import asyncio
import itertools
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import adam_proactive
from db import add_habit, add_user, complete_habit, get_habits, set_feature_flag
from db import adam_checkin as ac
from db.ai import add_ai_message, get_ai_history
from db.core import connect
from db.daily_plan import add_daily_task, set_daily_main_goal, toggle_daily_main_goal
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Moscow")

_uid_counter = itertools.count(780_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture
def flag_on():
    set_feature_flag(ac.FLAG_KEY, True, 100)
    yield
    set_feature_flag(ac.FLAG_KEY, False, 100)


class FakeBot:
    token = "adam:test-token"

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append((chat_id, text, reply_markup))


def _user(uid_, name="Анна", days_old=3):
    add_user(uid_, f"u{uid_}", name)
    conn = connect()
    conn.execute("UPDATE users SET created_at=datetime('now', ?), access_status='approved' WHERE telegram_id=?", (f"-{days_old} days", uid_))
    conn.commit()
    conn.close()


def _habits(uid_, titles, done=0):
    for title in titles:
        add_habit(uid_, title)
    rows = list(reversed(get_habits(uid_)))               # get_habits отдаёт новые первыми
    for row in rows[:done]:
        complete_habit(row["id"])
    return rows


def _local(hour, minute=0, day_offset=0):
    return (datetime.now(TZ) + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _due(uid_, slot):
    """Местное время, когда запланированный момент окна уже наступил (и окно ещё не закончилось)."""
    now = datetime.now(TZ)
    minute = ac.planned_minute(uid_, now.date().isoformat(), slot)
    return now.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)


def _events(uid_, habit_ids, hours, days=range(1, 6)):
    """Журнал отметок: каждый из последних дней — habit_ids в указанные местные часы."""
    conn = connect()
    for k in days:
        day = (datetime.now(TZ) - timedelta(days=k)).date()
        for habit_id, hour in zip(habit_ids, hours):
            moment = datetime.combine(day, time(hour, 0), TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?,?,?)", (uid_, habit_id, moment))
    conn.commit()
    conn.close()


def _generate_returns(monkeypatch, text, calls=None):
    async def fake(prompt, style):
        if calls is not None:
            calls.append(prompt)
        return text
    monkeypatch.setattr(adam_proactive, "_generate", fake)


def _log(uid_):
    conn = connect()
    rows = conn.execute("SELECT * FROM adam_checkins WHERE user_id=? ORDER BY id", (uid_,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- состояние: привычки И задачи ------------------------------------------------------------------------------

def test_state_counts_habits_and_tasks_together_and_names_what_is_left(uid):
    _user(uid)
    _habits(uid, ["Зарядка", "Чтение"], done=1)
    set_daily_main_goal(uid, "Подготовить презентацию")
    add_daily_task(uid, "Позвонить клиенту")
    toggle_daily_main_goal(uid)                                   # главная задача выполнена

    state = ac.build_checkin_state(uid, _local(15, 10))
    assert (state["habits_done"], state["habits_total"], state["tasks_done"], state["tasks_total"]) == (1, 2, 1, 2)
    assert (state["done"], state["total"], state["left"]) == (2, 4, 2)
    assert state["pending_habits"] == ["Чтение"] and state["pending_tasks"] == ["Позвонить клиенту"]
    assert state["pending"] == ["Позвонить клиенту", "Чтение"], "задачи называются раньше привычек"
    assert state["first_name"] == "Анна" and state["time"] == "15:10" and state["part"] == "день"


def test_main_task_is_named_before_other_tasks_and_habits(uid):
    state = ac.fill_counts({
        "habits": [{"title": "Зарядка", "done": False}],
        "tasks": [{"title": "Мелкая", "done": False, "main": False}, {"title": "Главная", "done": False, "main": True}],
    })
    assert state["pending"] == ["Главная", "Мелкая", "Зарядка"]


def test_the_prompt_carries_tasks_counts_scenario_and_opener(uid):
    _user(uid)
    _habits(uid, ["Зарядка", "Чтение"], done=1)
    set_daily_main_goal(uid, "Подготовить презентацию")
    add_daily_task(uid, "Позвонить клиенту")
    state = ac.build_checkin_state(uid, _local(15, 10))
    prompt = ac.build_checkin_prompt(state, "behind", "no_greeting", ["Сегодня идёшь бодро!"])
    assert "Дел на сегодня выполнено: 1 из 4 (привычки 1/2, задачи 0/2)" in prompt
    assert "Осталось из задач: «Подготовить презентацию», «Позвонить клиенту»" in prompt
    assert "Осталось из привычек: «Чтение»" in prompt
    assert "Главная задача дня: «Подготовить презентацию» — не выполнена" in prompt
    assert "Сценарий: " + ac.SCENARIO_GUIDE["behind"] in prompt and "Подача: " + ac.OPENER_GUIDE["no_greeting"] in prompt
    assert "Недавно ты уже писал" in prompt and "Сегодня идёшь бодро!" in prompt


def test_system_prompt_counts_tasks_and_forbids_the_template_questions():
    from multi_agent import ADAM_CHECKIN_SYSTEM
    assert "Привычки И ЗАДАЧИ" in ADAM_CHECKIN_SYSTEM and "выполнено X из Y" in ADAM_CHECKIN_SYSTEM
    assert "НИКОГДА" in ADAM_CHECKIN_SYSTEM and "Как дела?" in ADAM_CHECKIN_SYSTEM
    assert "1–3 предложения" in ADAM_CHECKIN_SYSTEM and "без markdown" in ADAM_CHECKIN_SYSTEM and "иногда без приветствия" in ADAM_CHECKIN_SYSTEM


# --- сценарии ---------------------------------------------------------------------------------------------------

def _state(done, total, hour=15, habits_total=None, habits_done=None, pace=None):
    habits_total = total if habits_total is None else habits_total
    habits_done = done if habits_done is None else habits_done
    habits = [{"title": f"Привычка {i}", "done": i < habits_done} for i in range(habits_total)]
    tasks = [{"title": f"Задача {i}", "done": i < done - habits_done, "main": i == 0} for i in range(total - habits_total)]
    state = {"habits": habits, "tasks": tasks, "hour": hour, "streak": 4, "first_name": "Анна", "language": "ru", "style": "neutral",
             "pace": pace or {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0}}
    return ac.fill_counts(state)


KNOWN_PACE = {"days": 8, "avg_by_now": 2.5, "avg_total": 4.0}


def test_daytime_scenarios_follow_the_spec():
    assert ac.pick_scenario(_state(0, 4), "day")[0] == "zero_done", "15:00 и ничего не выполнено"
    assert ac.pick_scenario(_state(3, 4), "day")[0] == "one_left"
    assert ac.pick_scenario(_state(4, 4), "day")[0] is None
    assert ac.pick_scenario(_state(0, 0), "day")[0] is None
    assert ac.pick_scenario(_state(1, 6), "day")[0] == "behind", "без истории — по доле: меньше трети"
    assert ac.pick_scenario(_state(3, 6), "day")[0] is None, "половина дня сделана — не трогаем"


def test_daytime_nudge_compares_with_the_persons_own_pace():
    assert ac.pick_scenario(_state(0, 4, pace=KNOWN_PACE), "day")[0] == "zero_done"
    late_bird = {"days": 8, "avg_by_now": 0.1, "avg_total": 3.0}
    scenario, reason = ac.pick_scenario(_state(0, 4, pace=late_bird), "day")
    assert scenario is None and "позже" in reason, "кто обычно делает привычки вечером, того днём не дёргаем"
    assert ac.pick_scenario(_state(1, 5, pace=KNOWN_PACE), "day")[0] == "behind", "1 при обычных 2.5 — отстаёт"
    assert ac.pick_scenario(_state(2, 5, pace=KNOWN_PACE), "day")[0] is None, "2 при обычных 2.5 — в норме"


def test_evening_scenarios_and_chat_days():
    assert ac.pick_scenario(_state(0, 4, hour=20), "evening")[0] == "zero_done_evening"
    assert ac.pick_scenario(_state(3, 4, hour=20), "evening")[0] == "one_left"
    assert ac.pick_scenario(_state(1, 4, hour=20), "evening")[0] == "evening_left"
    assert ac.pick_scenario(_state(4, 4, hour=20), "evening", chat_day=True)[0] == "all_done"
    assert ac.pick_scenario(_state(4, 4, hour=20), "evening", chat_day=False)[0] is None, "«поболтать» — не каждый день"
    assert ac.pick_scenario(_state(0, 0, hour=20), "evening", chat_day=True)[0] == "free_chat"
    assert ac.pick_scenario(_state(0, 0, hour=20), "evening", chat_day=False)[0] is None


def test_morning_is_statements_only_and_the_chat_greeting_follows_the_state():
    for done, total in ((0, 3), (1, 3), (3, 3), (0, 0)):
        scenario, _ = ac.pick_scenario(_state(done, total, hour=9), "app")
        assert scenario == "morning", (done, total)
    assert ac.pick_scenario(_state(0, 3, hour=14), "app")[0] == "zero_done"
    assert ac.pick_scenario(_state(0, 3, hour=19), "app")[0] == "zero_done_evening"
    assert ac.pick_scenario(_state(2, 3, hour=14), "app")[0] == "one_left"
    assert ac.pick_scenario(_state(3, 3, hour=14), "app")[0] == "all_done"
    assert ac.pick_scenario(_state(1, 4, hour=14), "app")[0] == "behind"
    assert ac.pick_scenario(_state(1, 4, hour=20), "app")[0] == "evening_left"


# --- расписание -------------------------------------------------------------------------------------------------

def test_moment_inside_the_window_is_random_but_stable_for_a_day():
    minutes = set()
    for u in range(300):
        for slot, (lo, hi) in ac.SLOT_WINDOWS.items():
            m = ac.planned_minute(u, "2026-10-08", slot)
            assert lo <= m < hi - ac.DELIVERY_MARGIN_MIN
            assert m == ac.planned_minute(u, "2026-10-08", slot), "повторный вызов даёт то же время"
            minutes.add((slot, m))
    assert len(minutes) > 150, "разные люди — разные моменты, а не «ровно в 15:00 для всех»"
    assert len({ac.planned_minute(7, f"2026-10-{d:02d}", "day") for d in range(1, 29)}) > 10, "по дням время плавает"


def test_slot_is_due_only_after_the_planned_moment_and_inside_the_window(uid):
    day = datetime.now(TZ).date()
    planned = ac.planned_minute(uid, day.isoformat(), "day")
    at = lambda minute: datetime.combine(day, time(minute // 60, minute % 60), TZ)
    assert ac.slot_due(uid, at(planned - 1)) is None
    assert ac.slot_due(uid, at(planned)) == "day"
    assert ac.slot_due(uid, at(16 * 60 + 29)) == "day"
    assert ac.slot_due(uid, at(16 * 60 + 30)) is None, "окно закончилось"
    assert ac.slot_due(uid, at(9 * 60)) is None, "утром Адам не пишет"
    assert ac.slot_due(uid, at(18 * 60)) is None and ac.slot_due(uid, at(23 * 60)) is None
    evening = ac.planned_minute(uid, day.isoformat(), "evening")
    assert 19 * 60 + 30 <= evening < 21 * 60 + 30 and ac.slot_due(uid, at(evening)) == "evening"


def test_chat_days_are_two_or_three_a_week_never_adjacent_and_stable():
    seen = set()
    for u in range(200):
        for week in range(10):
            monday = date(2026, 1, 5) + timedelta(weeks=week)
            days = ac.chat_days(u, monday)
            assert len(days) in (2, 3) and all(0 <= d <= 6 for d in days)
            assert all(b - a >= 2 for a, b in zip(sorted(days), sorted(days)[1:])), "не два дня подряд"
            assert days == ac.chat_days(u, monday + timedelta(days=3)), "внутри недели набор один"
            assert sum(ac.is_chat_day(u, monday + timedelta(days=i)) for i in range(7)) == len(days)
            seen.add(tuple(sorted(days)))
    assert len(seen) > 5, "у каждого человека и недели свои дни"


# --- тексты -----------------------------------------------------------------------------------------------------

def test_template_questions_long_and_markdown_replies_are_rejected():
    ok = lambda text, scenario="zero_done": ac.is_acceptable(ac.clean_text(text), scenario)
    assert ok("Тихо сегодня 🙂 Начнём с «Зарядки»?")
    for banned in ("Привет! Как дела?", "Как настроение сегодня?", "Как прошёл твой день?", "Как ты? Давай начнём.", "Как ПРОШЕЛ день?"):
        assert not ok(banned), banned
    assert not ok("Что получилось? Что мешало?"), "не больше одного вопроса"
    assert not ok("Утро. Что планируешь сегодня?", "morning"), "утром вопросов нет"
    assert not ok("а" * (ac.TEXT_MAX_CHARS + 1)) and not ok("")
    assert not ok("Раз. Два. Три. Четыре. Пять. Шесть.")
    assert ac.clean_text("**Хорошо** идёшь\n\nпродолжай `так`") == "Хорошо идёшь продолжай так"
    assert ac.clean_text("«Просто сообщение»") == "Просто сообщение" and ac.clean_text("ADAM: привет") == "привет"


def test_fallback_texts_are_acceptable_name_the_task_and_never_ask_how_are_you():
    for key, (title, slot, state) in ac.lab_scenarios().items():
        scenario, _ = ac.pick_scenario(state, slot, chat_day=True)
        for day in range(1, 25):
            text = ac.fallback_text(state, scenario, 5, f"2026-10-{day:02d}")
            assert ac.is_acceptable(text, scenario), (key, text)
            assert "None" not in text and "{" not in text and "**" not in text, text
    # осталась только задача — её и называем
    _t, slot, state = ac.lab_scenarios()["one_left"]
    assert "Созвон с командой" in ac.fallback_text(state, "one_left", 9, "2026-10-08")
    # «самое лёгкое» — привычка, а не главная задача
    _t, slot, state = ac.lab_scenarios()["zero_done"]
    for day in range(1, 20):
        assert "Отправить предложение клиенту" not in ac.fallback_text(state, "zero_done", 3, f"2026-10-{day:02d}")


def test_greetings_vary_between_days_and_sometimes_skip_the_name():
    _t, slot, state = ac.lab_scenarios()["evening_left"]
    texts = [ac.fallback_text(state, "evening_left", 11, f"2026-10-{d:02d}") for d in range(1, 29)]
    assert len(set(texts)) > 3
    named = sum(t.startswith("Анна, ") for t in texts)
    assert 4 <= named <= 24, "обращение по имени — не в каждом сообщении"
    assert {ac.opener_style(11, f"2026-10-{d:02d}", "evening_left") for d in range(1, 29)} == {"no_greeting", "by_name", "short_greeting"}


# --- обычный темп человека --------------------------------------------------------------------------------------

def test_pace_history_averages_what_the_person_usually_closes_by_this_time(uid):
    _user(uid)
    rows = _habits(uid, ["А", "Б", "В"])
    _events(uid, [r["id"] for r in rows], hours=(9, 11, 20), days=range(1, 6))        # утром две привычки, одна вечером
    pace = ac.pace_history(uid, _local(15, 0))
    assert pace["days"] == 5 and pace["avg_by_now"] == 2 and pace["avg_total"] == 3
    assert ac.pace_history(uid, _local(8, 0))["avg_by_now"] == 0, "к 08:00 обычно ничего"


# --- планировщик ------------------------------------------------------------------------------------------------

def _ready_user(uid_, *, done=0, titles=("Зарядка", "Чтение", "Английский"), with_history=True):
    """Человек, которому Адам уместен: не новый, активный, привычки и (по желанию) история отметок."""
    _user(uid_)
    rows = _habits(uid_, list(titles), done=done)
    if with_history:
        _events(uid_, [r["id"] for r in rows], hours=(9, 10, 11))
    return rows


async def test_adam_writes_once_with_the_context_and_the_chat_continues_from_it(uid, flag_on, monkeypatch):
    _ready_user(uid)
    set_daily_main_goal(uid, "Подготовить презентацию")
    prompts = []
    _generate_returns(monkeypatch, "Тихо сегодня — всё в порядке? Начни с «Зарядки», остальное подтянется.", prompts)
    bot, now = FakeBot(), _due(uid, "day")

    outcome = await adam_proactive.process_user(bot, uid, "scope", now)
    assert outcome == "sent: zero_done/llm"
    chat_id, text, markup = bot.sent[0]
    assert chat_id == uid and text.startswith("Тихо сегодня")
    assert markup.inline_keyboard[0][0].web_app.url.endswith("/coach"), "кнопка открывает чат с Адамом"
    assert "Подготовить презентацию" in prompts[0] and "Дел на сегодня выполнено: 0 из 4" in prompts[0], "в промпте и привычки, и задача"
    history = get_ai_history(uid)
    assert history[-1]["role"] == "assistant" and history[-1]["message"] == text, "сообщение — реплика Адама в переписке"
    row = _log(uid)[0]
    assert (row["slot"], row["status"], row["scenario"], row["source"]) == ("day", "sent", "zero_done", "llm")
    assert row["message_id"] == history[-1]["id"]
    assert await adam_proactive.process_user(bot, uid, "scope", now) == "already_handled", "окно обработано — второго не будет"
    assert len(bot.sent) == 1


async def test_a_template_answer_from_the_model_is_replaced_by_a_fallback(uid, flag_on, monkeypatch):
    _ready_user(uid)
    _generate_returns(monkeypatch, "Привет! Как дела? Что нового?")
    bot = FakeBot()
    assert await adam_proactive.process_user(bot, uid, "scope", _due(uid, "day")) == "sent: zero_done/fallback"
    assert "дела" not in bot.sent[0][1].lower() and _log(uid)[0]["source"] == "fallback"


async def test_model_outage_falls_back_and_english_users_get_nothing_in_russian(uid, flag_on, monkeypatch):
    _ready_user(uid)

    async def broken(prompt, style):
        raise RuntimeError("нет сети")
    monkeypatch.setattr(adam_proactive, "_generate", broken)
    bot = FakeBot()
    assert (await adam_proactive.process_user(bot, uid, "scope", _due(uid, "day"))).startswith("sent")
    en = uid + 1
    _ready_user(en)
    conn = connect(); conn.execute("UPDATE users SET language='en' WHERE telegram_id=?", (en,)); conn.commit(); conn.close()
    assert await adam_proactive.process_user(bot, en, "scope", _due(en, "day")) == "skipped: нет текста"


async def test_nothing_is_sent_for_a_person_who_is_on_pace_or_has_nothing_to_do(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Не должно отправиться.")
    bot = FakeBot()
    rows = _ready_user(uid, done=3)                                   # всё выполнено
    assert (await adam_proactive.process_user(bot, uid, "scope", _due(uid, "day"))).startswith("skipped: всё уже выполнено")
    late = uid + 1
    _user(late)
    rows = _habits(late, ["А", "Б"])
    _events(late, [r["id"] for r in rows], hours=(20, 21))            # обычно делает вечером
    assert "позже" in await adam_proactive.process_user(bot, late, "scope", _due(late, "day"))
    assert bot.sent == [] and {r["status"] for r in _log(uid) + _log(late)} == {"skipped"}


async def test_evening_all_done_is_written_only_on_chat_days(uid, flag_on, monkeypatch):
    _ready_user(uid, done=3)
    _generate_returns(monkeypatch, "Все дела закрыты — что из сегодняшнего получилось лучше всего?")
    bot = FakeBot()
    monkeypatch.setattr(ac, "is_chat_day", lambda *a: False)
    assert (await adam_proactive.process_user(bot, uid, "scope", _due(uid, "evening"))).startswith("skipped")
    other = uid + 1
    _ready_user(other, done=3)
    monkeypatch.setattr(ac, "is_chat_day", lambda *a: True)
    assert await adam_proactive.process_user(bot, other, "scope", _due(other, "evening")) == "sent: all_done/llm"


async def test_gates_account_age_dormant_settings_quiet_hours_and_blocks(uid, flag_on, monkeypatch):
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()

    young = uid
    _ready_user(young)
    conn = connect(); conn.execute("UPDATE users SET created_at=datetime('now','-2 hours') WHERE telegram_id=?", (young,)); conn.commit(); conn.close()
    assert "новый" in await adam_proactive.process_user(bot, young, "s", _due(young, "day"))

    dormant = uid + 1
    _ready_user(dormant, with_history=False)
    conn = connect(); conn.execute("UPDATE users SET created_at=datetime('now','-30 days') WHERE telegram_id=?", (dormant,)); conn.commit(); conn.close()
    assert "давно" in await adam_proactive.process_user(bot, dormant, "s", _due(dormant, "day"))

    muted = uid + 2
    _ready_user(muted)
    conn = connect(); conn.execute("UPDATE settings SET reminders=0 WHERE user_id=?", (muted,)); conn.commit(); conn.close()
    assert "напоминания выключены" in await adam_proactive.process_user(bot, muted, "s", _due(muted, "day"))

    quiet = uid + 3
    _ready_user(quiet)
    conn = connect(); conn.execute("UPDATE settings SET quiet_hours_start=0, quiet_hours_end=23 WHERE user_id=?", (quiet,)); conn.commit(); conn.close()
    assert "тихие часы" in await adam_proactive.process_user(bot, quiet, "s", _due(quiet, "day"))
    assert bot.sent == []


async def test_one_message_a_day_five_a_week_and_not_right_after_another_push(uid, flag_on, monkeypatch):
    _ready_user(uid)
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()

    day_now = _due(uid, "day")
    assert (await adam_proactive.process_user(bot, uid, "s", day_now)).startswith("sent")
    evening = _due(uid, "evening")
    assert "уже писал сегодня" in await adam_proactive.process_user(bot, uid, "s", evening), "второе за день — нет"

    capped = uid + 1
    _ready_user(capped)
    conn = connect()
    for back in range(1, 6):
        day = (datetime.now(TZ).date() - timedelta(days=back)).isoformat()
        conn.execute("INSERT INTO adam_checkins(user_id, day, slot, status) VALUES (?,?, 'day', 'sent')", (capped, day))
    conn.commit(); conn.close()
    assert "лимит" in await adam_proactive.process_user(bot, capped, "s", _due(capped, "day"))

    busy = uid + 2
    _ready_user(busy)
    conn = connect(); conn.execute("INSERT INTO notification_log(user_id, category, title) VALUES (?, 'day_progress_19', 'x')", (busy,)); conn.commit(); conn.close()
    assert "другой пуш" in await adam_proactive.process_user(bot, busy, "s", _due(busy, "evening"))


async def test_recent_chat_with_adam_means_no_push_and_wrote_today_marks_the_in_app_greeting_done(uid, flag_on, monkeypatch):
    _ready_user(uid)
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()
    assert (await adam_proactive.process_user(bot, uid, "s", _due(uid, "day"))).startswith("sent")
    from db.ai_nudge import get_ai_nudge_state
    assert get_ai_nudge_state(uid)["pending"] is False, "в приложении второе «новое сообщение от Адама» не появится"


async def test_rollout_flag_admins_always_and_a_percentage_of_the_rest(uid, monkeypatch):
    import config
    _ready_user(uid)
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()
    assert await adam_proactive.process_user(bot, uid, "s", _due(uid, "day")) == "disabled", "флаг выключен — обычным людям нельзя"
    monkeypatch.setattr(config, "ADMIN_IDS", [uid])
    assert (await adam_proactive.process_user(bot, uid, "s", _due(uid, "day"))).startswith("sent"), "админам — всегда"
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    set_feature_flag(ac.FLAG_KEY, True, 0)
    try:
        assert not ac.is_enabled_for(uid + 5)
        set_feature_flag(ac.FLAG_KEY, True, 100)
        assert ac.is_enabled_for(uid + 5)
    finally:
        set_feature_flag(ac.FLAG_KEY, False, 100)


async def test_telegram_failure_retries_next_tick_and_a_blocked_bot_is_remembered(uid, flag_on, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
    from db import is_bot_blocked
    _ready_user(uid)
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    now = _due(uid, "day")

    assert await adam_proactive.process_user(FakeBot(fail=TelegramNetworkError(method=None, message="timeout")), uid, "s", now) == "error"
    assert _log(uid) == [], "сбой отправки не сжигает окно"
    good = FakeBot()
    assert (await adam_proactive.process_user(good, uid, "s", now)).startswith("sent") and len(good.sent) == 1

    blocked = uid + 1
    _ready_user(blocked)
    outcome = await adam_proactive.process_user(FakeBot(fail=TelegramForbiddenError(method=None, message="blocked")), blocked, "s", _due(blocked, "day"))
    assert outcome == "skipped: blocked" and is_bot_blocked(blocked)
    assert get_ai_history(blocked) == [], "недоставленное сообщение в переписку не попадает"


async def test_the_tick_goes_through_everyone_and_survives_one_bad_user(uid, flag_on, monkeypatch):
    _ready_user(uid)
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    real = adam_proactive.process_user

    async def flaky(bot, telegram_id, scope, now_local=None):
        if telegram_id != uid:
            raise RuntimeError("чужой сбой")
        return await real(bot, telegram_id, scope, _due(uid, "day"))
    monkeypatch.setattr(adam_proactive, "process_user", flaky)
    bot = FakeBot()
    await adam_proactive.run_adam_checkins(bot)
    assert any(chat == uid for chat, _t, _m in bot.sent)


# --- первое сообщение при заходе в чат ---------------------------------------------------------------------------

async def test_chat_greeting_is_contextual_when_enabled_and_static_otherwise(client, uid, flag_on, monkeypatch):
    from tests.test_ai_nudge import _user as nudge_user
    nudge_user(uid, chatted_before=True)
    _habits(uid, ["Зарядка", "Чтение"], done=1)
    add_daily_task(uid, "Позвонить клиенту")
    prompts = []
    _generate_returns(monkeypatch, "Остались «Чтение» и звонок клиенту — с чего начнёшь?", prompts)
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    greeting = (await reply.json())["greeting"]
    assert greeting["message"].startswith("Остались «Чтение»") and "Позвонить клиенту" in prompts[0]
    assert get_ai_history(uid)[-1]["message"] == greeting["message"]
    assert [(r["slot"], r["status"], r["source"]) for r in _log(uid)] == [("app", "sent", "llm")]
    again = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    assert (await again.json())["greeting"] is None, "в день одно первое сообщение"


async def test_chat_greeting_without_the_flag_stays_the_old_static_text(client, uid, monkeypatch):
    from db.ai_nudge import GREETINGS_DAY, GREETINGS_MORNING
    from tests.test_ai_nudge import _user as nudge_user
    nudge_user(uid, chatted_before=True)
    called = []
    _generate_returns(monkeypatch, "не должно вызваться", called)
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    text = (await reply.json())["greeting"]["message"]
    assert called == [] and any(text == t.format(name="Аня") for t in GREETINGS_DAY + GREETINGS_MORNING)


async def test_chat_greeting_falls_back_to_a_contextual_text_when_the_model_is_down(client, uid, flag_on, monkeypatch):
    from tests.test_ai_nudge import _user as nudge_user
    nudge_user(uid, chatted_before=True)
    _habits(uid, ["Зарядка"], done=0)

    async def broken(prompt, style):
        raise RuntimeError("модель недоступна")
    monkeypatch.setattr(adam_proactive, "_generate", broken)
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    text = (await reply.json())["greeting"]["message"]
    assert text and "дела" not in text.lower() and [r["source"] for r in _log(uid)] == ["fallback"]


# --- админка ----------------------------------------------------------------------------------------------------

async def test_admin_adam_lab_routes_are_admin_only(client, uid):
    _user(uid)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    assert (await client.get("/api/admin/lab/adam")).status == 401
    assert (await client.get("/api/admin/lab/adam", headers=headers)).status == 403
    for path in ("/api/admin/lab/adam/preview", "/api/admin/lab/adam/send"):
        assert (await client.post(path, json={})).status == 401
        assert (await client.post(path, json={}, headers=headers)).status == 403


async def test_admin_previews_every_scenario_and_sends_the_text_only_to_themselves(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    _user(uid)
    monkeypatch.setattr(ra, "ADMIN_IDS", {uid})
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    _generate_returns(monkeypatch, "Остался последний шаг — «Созвон с командой».")

    overview = await (await client.get("/api/admin/lab/adam", headers=headers)).json()
    assert overview["windows"] == {"day": "14:00–16:30", "evening": "19:30–21:30"}
    assert overview["limits"] == {"per_day": 1, "per_week": 5, "chat_days": [2, 3]} and overview["flag"]["enabled"] is False
    assert {s["key"] for s in overview["scenarios"]} >= {"zero_done", "behind", "one_left", "zero_done_evening", "evening_left", "all_done", "free_chat", "morning"}

    for scenario in overview["scenarios"]:
        for source in ("llm", "fallback"):
            resp = await client.post("/api/admin/lab/adam/preview", json={"scenario": scenario["key"], "source": source}, headers=headers)
            data = await resp.json()
            assert resp.status == 200 and data["text"] and data["scenario"], scenario["key"]
            assert data["source"] == source, (scenario["key"], source, data["source"])
            assert ac.is_acceptable(data["text"], data["scenario"])
            assert "Дел на сегодня выполнено" in data["prompt"] or scenario["key"] == "free_chat"
    mine = await (await client.post("/api/admin/lab/adam/preview", json={"scenario": "mine", "source": "fallback"}, headers=headers)).json()
    assert set(mine["decisions"]) == {"day", "evening", "chat_day"}
    assert (await client.post("/api/admin/lab/adam/preview", json={"scenario": "нет-такого"}, headers=headers)).status == 404

    bot = FakeBot()
    client.app["bot"] = bot
    sent = await client.post("/api/admin/lab/adam/send", json={"text": "Привет из лаборатории", "chat_id": 1}, headers=headers)
    assert sent.status == 200 and [c for c, _t, _m in bot.sent] == [uid], "получатель — только сам админ, чужой chat_id игнорируется"
    assert bot.sent[0][1].startswith("🧪 Тест · ")
    assert get_ai_history(uid) == [], "тест в переписку не пишется"
    assert (await client.post("/api/admin/lab/adam/send", json={"text": ""}, headers=headers)).status == 400


# --- проводка ---------------------------------------------------------------------------------------------------

def test_wiring_job_label_flag_seed_and_admin_card():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from adam_proactive import run_adam_checkins" in main
    assert 'scheduler.add_job(run_adam_checkins, "interval", minutes=3, args=[bot], max_instances=1, coalesce=True)' in main
    from db.streak import NOTIFICATION_KIND_LABELS
    assert NOTIFICATION_KIND_LABELS["adam_checkin"] == "💬 Сообщение от Адама"
    conn = connect()
    flag = conn.execute("SELECT enabled, rollout_pct FROM feature_flags WHERE key='adam_checkin'").fetchone()
    conn.close()
    assert flag is not None, "флаг виден в админке сразу"
    admin = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")
    assert 'id="adamLabBox"' in admin and "/api/admin/lab/adam/preview" in admin and "/api/admin/lab/adam/send" in admin
    assert admin.index("Тесты и нововведения") < admin.index("Адам пишет первым") < admin.index("Рейтинг пар (тест)")


# --- примеры владельца проекта и история дней ------------------------------------------------------------------

OWNER_EXAMPLES = {
    "owner_a": ("zero_done", "Александр, смотрю на твой дашборд — уже середина дня, а галочек пока нет. День выдался сумасшедшим или просто нет сил? Давай попробуем закрыть хотя бы самую лёгкую задачу на сегодня."),
    "owner_b": ("one_left", "Вижу отличный прогресс! Две привычки уже в копилке. Осталось только «Чтение 15 минут». Найдёшь на это время до сна, или сегодня ставим на паузу?"),
    "owner_v": ("all_done", "Идеальный день, все привычки закрыты! Закинул тебе в статистику отличный результат. Раз уж мы всё успели, расскажи, что сегодня вообще было классного помимо рутины?"),
}


def test_the_owners_three_examples_are_lab_scenarios_with_the_same_time_and_score():
    labs = ac.lab_scenarios()
    for key, (scenario, example) in OWNER_EXAMPLES.items():
        _title, slot, state = labs[key]
        assert ac.pick_scenario(state, slot, chat_day=True)[0] == scenario, key
        assert ac.is_acceptable(ac.clean_text(example), scenario), f"эталон {key} проходит нашу же проверку текста"
        assert ac.REFERENCE_EXAMPLES[scenario].format(name="Александр") == example, "эталон в коде — слово в слово текст владельца"
    a, b, v = (labs[k][2] for k in ("owner_a", "owner_b", "owner_v"))
    assert (a["time"], a["done"], a["total"]) == ("14:45", 0, 3)
    assert (b["time"], b["done"], b["total"], b["pending"]) == ("19:20", 2, 3, ["Чтение 15 минут"])
    assert (v["time"], v["done"], v["total"]) == ("20:15", 3, 3)


def test_every_scenario_has_a_reference_example_that_goes_into_the_prompt_without_the_name():
    extras = {"morning_progress", "morning_done", "morning_empty"}
    assert set(ac.REFERENCE_EXAMPLES) == set(ac.SCENARIOS) == set(ac.SCENARIO_GUIDE) == set(ac.FALLBACKS) - extras
    for scenario, text in ac.REFERENCE_EXAMPLES.items():
        assert ac.is_acceptable(text.format(name="Александр"), scenario), scenario
    _t, _slot, state = ac.lab_scenarios()["owner_a"]
    prompt = ac.build_checkin_prompt(state, "zero_done", "no_greeting")
    assert "Ориентир по тону" in prompt and "<имя>, смотрю на твой дашборд" in prompt
    assert "Александр, смотрю" not in prompt and "не копируй" in prompt, "имя из примера в промпт не попадает"


def test_pace_history_reports_yesterday_and_the_active_days_of_the_week(uid):
    _user(uid)
    rows = _habits(uid, ["А", "Б", "В"])
    _events(uid, [r["id"] for r in rows], hours=(9, 10, 11), days=[1, 2, 4])
    pace = ac.pace_history(uid, _local(15, 0))
    assert (pace["days"], pace["yesterday"], pace["active_7"]) == (3, 3, 3)
    other = uid + 1
    _user(other)
    rows = _habits(other, ["А"])
    _events(other, [rows[0]["id"]], hours=(9,), days=[3])
    assert ac.pace_history(other, _local(15, 0))["yesterday"] == 0


def test_the_prompt_gets_history_facts_only_when_they_exist(uid):
    _user(uid)
    rows = _habits(uid, ["Зарядка", "Чтение"])
    _events(uid, [r["id"] for r in rows], hours=(9, 10))
    conn = connect(); conn.execute("UPDATE users SET streak=19, best_streak=21 WHERE telegram_id=?", (uid,)); conn.commit(); conn.close()
    prompt = ac.build_checkin_prompt(ac.build_checkin_state(uid, _local(20, 0)), "zero_done_evening", "no_greeting")
    assert "Вчера закрыто привычек: 2; дней с отметками за последнюю неделю: 5 из 7" in prompt
    assert "До личного рекорда серии (21 дн.) осталось 2 дн." in prompt
    assert "Серия: 19 дн. подряд (под угрозой" in prompt

    fresh = uid + 1
    _user(fresh)
    _habits(fresh, ["Зарядка"])
    plain = ac.build_checkin_prompt(ac.build_checkin_state(fresh, _local(20, 0)), "zero_done_evening", "no_greeting")
    assert "Вчера закрыто" not in plain and "рекорда" not in plain and "не выполнена" not in plain.replace("Главная", "")


def _miss_logs(uid_, habit_id, title, missed=4):
    conn = connect()
    for back in range(1, missed + 1):
        conn.execute("INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped) VALUES (?,?,?, date('now', ?), 0, 0)",
                     (uid_, habit_id, title, f"-{back} days"))
    conn.commit()
    conn.close()


def test_a_habit_that_keeps_failing_gets_its_own_scenario_on_chat_days(uid):
    _user(uid)
    rows = _habits(uid, ["Зарядка", "Чтение"], done=1)
    _miss_logs(uid, rows[1]["id"], "Чтение")
    state = ac.build_checkin_state(uid, _local(20, 30))
    assert state["struggling"] == {"title": "Чтение", "missed": 4}
    assert ac.pick_scenario(state, "evening", chat_day=True)[0] == "struggling"
    assert ac.pick_scenario(state, "evening", chat_day=False)[0] == "one_left", "в обычный день — как раньше"
    prompt = ac.build_checkin_prompt(state, "struggling", "no_greeting")
    assert "Привычка «Чтение» не выполнена 4 из последних 5 дней" in prompt and ac.SCENARIO_GUIDE["struggling"] in prompt
    for day in range(1, 12):
        text = ac.fallback_text(state, "struggling", uid, f"2026-10-{day:02d}")
        assert "Чтение" in text and ac.is_acceptable(text, "struggling"), text
    assert ac.pick_scenario(dict(state, done=0, habits_done=0, left=2), "evening", chat_day=True)[0] == "zero_done_evening", "сначала защита серии"
    complete_habit(rows[1]["id"])
    assert ac.build_checkin_state(uid, _local(20, 30))["struggling"] is None, "сделал сегодня — повода нет"


async def test_adam_yields_to_the_reminders_that_bring_a_person_back_into_the_streak(uid, flag_on, monkeypatch):
    from db.streak import local_today
    _generate_returns(monkeypatch, "Один маленький шаг — «Зарядка».")
    bot = FakeBot()
    away = uid
    _ready_user(away)
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?, 'completed', 5)", (away, str(local_today(away) - timedelta(days=3))))
    conn.commit(); conn.close()
    assert "возвращают в серию" in await adam_proactive.process_user(bot, away, "s", _due(away, "day"))

    active = uid + 1
    _ready_user(active)
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?, 'completed', 5)", (active, str(local_today(active) - timedelta(days=1))))
    conn.commit(); conn.close()
    assert (await adam_proactive.process_user(bot, active, "s", _due(active, "day"))).startswith("sent"), "вчера всё было — пропуска нет, Адам пишет"


def test_system_prompt_allows_one_history_fact_and_treats_examples_as_style_only():
    from multi_agent import ADAM_CHECKIN_SYSTEM
    assert "ОДИН факт" in ADAM_CHECKIN_SYSTEM and "без запугивания потерей серии" in ADAM_CHECKIN_SYSTEM
    assert "Ориентир по тону" in ADAM_CHECKIN_SYSTEM and "не копируй" in ADAM_CHECKIN_SYSTEM


async def test_lab_shows_the_owners_reference_next_to_what_adam_wrote(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    _user(uid)
    monkeypatch.setattr(ra, "ADMIN_IDS", {uid})
    _generate_returns(monkeypatch, "Тихо сегодня — всё в порядке? Начни с «Зарядки».")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    data = await (await client.post("/api/admin/lab/adam/preview", json={"scenario": "owner_a"}, headers=headers)).json()
    assert data["reference"].startswith("Александр, смотрю на твой дашборд") and data["scenario"] == "zero_done"
    assert "Ориентир по тону" in data["prompt"]
    admin = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")
    assert "📌 Эталон: " in admin and "r.reference" in admin
