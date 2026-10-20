"""
Адам и ритм человека (14.10): первое сообщение в чате учитывает, когда человек обычно заходит и когда пришёл сегодня («Что случилось,
сегодня тебя не было весь день?» — тому, кто обычно здесь с утра; «Я ждал тебя» — вечернему человеку), у всех, а не только при флаге;
сообщения не повторяются; ритм виден и в обычном чате, но только когда в нём есть что заметить.
"""
import asyncio
import itertools
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import adam_proactive
from db import add_user
from db import adam_checkin as ac
from db.ai import add_ai_message
from db.core import connect
from tests.conftest import sign_init_data
from tests.test_adam_checkin import _events, _generate_returns, _habits, _local, _log

TZ = ZoneInfo("Europe/Moscow")
_counter = itertools.count(870_000_000, 10)
LATE = "Что случилось — сегодня тебя не было весь день. День пошёл не по плану?"


@pytest.fixture
def uid():
    return next(_counter)


def _person(uid_):
    add_user(uid_, f"u{uid_}", "Анна")
    conn = connect()
    conn.execute("UPDATE users SET created_at=datetime('now','-30 days'), access_status='approved' WHERE telegram_id=?", (uid_,))
    conn.commit()
    conn.close()


def _open_at(uid_, day_offset, hour, minute=0):
    day = (datetime.now(TZ) + timedelta(days=day_offset)).date()
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    conn.execute("INSERT INTO app_opens(user_id, at) VALUES (?,?)", (uid_, moment))
    conn.commit()
    conn.close()


def _lab(key):
    return ac.lab_scenarios()[key][2]


def _with(state, **fields):
    rhythm = dict(state["rhythm"], **{k: fields.pop(k) for k in list(fields) if k in state["rhythm"]})
    return {**state, **fields, "rhythm": rhythm}


# ---------------------------------------------------------------------------------------------------------------- ритм по данным

def test_the_rhythm_learns_the_usual_time_and_how_long_the_person_was_away(uid):
    _person(uid)
    for k, (hour, minute) in enumerate([(8, 30), (8, 50), (9, 10), (8, 40), (9, 0), (8, 20)], start=1):
        _open_at(uid, -k, hour, minute)
    rhythm = ac.visit_rhythm(uid, _local(19, 40))
    assert rhythm["days"] == 6 and rhythm["usual_part"] == "утро" and 8 * 60 + 20 <= rhythm["usual_first"] <= 9 * 60 + 10
    assert rhythm["gap_days"] == 1 and rhythm["first_today"] is None and rhythm["arrived_now"] is True
    _open_at(uid, 0, 19, 30)
    arrived = ac.visit_rhythm(uid, _local(19, 40))
    assert arrived["first_today"] == 19 * 60 + 30 and arrived["arrived_now"] is True
    _open_at(uid, 0, 18, 0)
    assert ac.visit_rhythm(uid, _local(19, 40))["arrived_now"] is False, "в приложении с 18:00 — «пришёл только что» уже нет"


def test_two_days_of_history_are_not_enough_to_say_usually(uid):
    _person(uid)
    _open_at(uid, -1, 9)
    _open_at(uid, -2, 9)
    rhythm = ac.visit_rhythm(uid, _local(19, 40))
    assert rhythm["days"] == 2 and rhythm["usual_first"] is None and rhythm["usual_part"] is None
    assert ac.rhythm_lines(dict(_lab("late_visit"), rhythm=rhythm)) == [], "про ритм ни слова, пока он не известен"


def test_the_first_mark_time_and_a_night_owl_are_understood(uid):
    _person(uid)
    habits = _habits(uid, ["Зарядка"])
    _events(uid, [habits[0]["id"]], hours=(8,), days=range(1, 6))
    assert ac.visit_rhythm(uid, _local(19, 0))["first_mark"] == 8 * 60
    owl = uid + 1
    _person(owl)
    for k in range(1, 6):
        _open_at(owl, -k, 0, 40)                  # после полуночи — это хвост вечера, а не «раннее утро»
    rhythm = ac.visit_rhythm(owl, _local(20, 0))
    assert rhythm["usual_part"] == "вечер" and ac._hhmm(rhythm["usual_first"]) == "00:40"


# ---------------------------------------------------------------------------------------------------------------- что Адам выберет

def test_the_first_message_notices_a_late_arrival_a_regular_evening_and_a_pause():
    assert ac.pick_scenario(_lab("late_visit"), "app")[0] == "late_visit"
    assert ac.pick_scenario(_lab("evening_regular"), "app")[0] == "evening_regular"
    assert ac.pick_scenario(_lab("back_after_pause"), "app") == ("back_after_pause", "не было в приложении 4 дн.")
    late = _lab("late_visit")
    assert ac.pick_scenario(_with(late, arrived_now=False), "app")[0] == "zero_done_evening", "был в приложении раньше — не «пришёл поздно»"
    assert ac.pick_scenario(_with(late, hour=11, minute=0), "app")[0] == "morning", "утром вопросов нет"
    assert ac.pick_scenario(_with(late, hour=13, minute=30), "app")[0] == "zero_done", "до 14:00 — ещё не опоздание"
    assert ac.pick_scenario(_with(late, hour=15, minute=0), "app")[0] == "late_visit"
    assert ac.pick_scenario(_with(late, days=2, usual_first=None, usual_part=None), "app")[0] == "zero_done_evening", "истории мало — как раньше"
    assert ac.pick_scenario(dict(late, rhythm=dict(ac.EMPTY_RHYTHM)), "app")[0] == "zero_done_evening"
    evening = _lab("evening_regular")
    assert ac.pick_scenario(_with(evening, hour=14, minute=0), "app")[0] == "zero_done", "вечерний человек днём — обычный сценарий"
    assert ac.pick_scenario(_with(evening, hour=23, minute=40), "app")[0] == "zero_done_evening", "далеко от своего времени — не «ждал тебя»"


def test_something_already_done_today_means_no_late_arrival_message():
    state = _with(_lab("late_visit"))
    state["habits"][0]["done"] = True
    ac.fill_counts(state)
    assert ac.pick_scenario(state, "app")[0] in ("evening_left", "one_left")
    done = _lab("late_visit")
    for habit in done["habits"]:
        habit["done"] = True
    for task in done["tasks"]:
        task["done"] = True
    assert ac.pick_scenario(ac.fill_counts(done), "app")[0] == "all_done"


# ---------------------------------------------------------------------------------------------------------------- что видит модель

def test_the_prompt_carries_the_rhythm_the_angle_and_the_no_snooping_rule():
    state = _lab("late_visit")
    prompt = ac.build_checkin_prompt(state, "late_visit", "no_greeting", angle=ac.ANGLES[0])
    assert "обычно заходит в приложение утром (около 08:40" in prompt and "пришёл только сейчас" in prompt
    assert "Первую привычку за день обычно отмечает около 09:00" in prompt
    assert "Сценарий: " + ac.SCENARIO_GUIDE["late_visit"] in prompt and "Подход на этот раз: " + ac.ANGLES[0] in prompt
    assert "без слов «слежу»" in prompt and "НЕ пересказывай человеку, когда он обычно заходит" in prompt
    quiet = ac.build_checkin_prompt(dict(state, rhythm=dict(ac.EMPTY_RHYTHM)), "zero_done_evening", "no_greeting")
    assert "Ритм человека" not in quiet and "Подход на этот раз" not in quiet
    pause = ac.build_checkin_prompt(_lab("back_after_pause"), "back_after_pause", "short_greeting")
    assert "не было в приложении 4 дн." in pause


def test_the_evening_regular_may_be_asked_about_the_mood_but_only_about_the_plans():
    mood = "Привет, я ждал тебя. Как настроение — возьмёмся сегодня за наши планы?"
    assert ac.is_acceptable(mood, "evening_regular")
    assert not ac.is_acceptable(mood, "zero_done_evening") and not ac.is_acceptable("Привет! Как дела?", "evening_regular")
    for scenario in ("late_visit", "evening_regular", "back_after_pause"):
        assert ac.is_acceptable(ac.REFERENCE_EXAMPLES[scenario], scenario), scenario


def test_adam_knows_the_usual_visit_time_but_does_not_tell_it_to_the_person():
    """Владелец (20.10): «Вечером ты обычно заглядываешь сюда» — лишняя информация; Адам использует её молча."""
    told = [
        "Привет! Вечером ты обычно заглядываешь сюда — рад тебя видеть. Как по энергии: начнём с «Читать 20 минут»?",
        "Что случилось — сегодня тебя не было весь день, а обычно ты здесь раньше.",
        "Вот и ты — обычно раньше. Что-то поменяло планы?",
        "Ты обычно приходишь утром, а сегодня только сейчас.",
        "Заглядываешь сюда обычно вечером — вот и сейчас.",
        "В твоё привычное время ты заходишь реже.",
    ]
    for text in told:
        assert ac.tells_the_visit_habit(text), text
        for scenario in ("evening_regular", "late_visit", "back_after_pause", "check_in", "free_chat"):
            assert not ac.is_acceptable(text, scenario), (scenario, text)
    quiet = [
        "Привет! Рад тебя видеть. Как по энергии: начнём с «Читать 20 минут»?",
        "Я ждал тебя. Как настроение — возьмёмся за наши планы?",
        "Две из шести — для тебя это медленнее обычного. Что отъедает время?",
        "Сегодня не увидел твоей обычной вечерней тренировки. День просто пошёл иначе?",
        "Я всегда здесь, если нужно.",
        "Тебя не было 4 дня — с чего удобнее вернуться?",
    ]
    for text in quiet:
        assert not ac.tells_the_visit_habit(text), text
    # Модель получает правило в самом промпте и в снимке для чата; сценарии не просят «покажи, что замечаешь время захода».
    prompt = ac.build_checkin_prompt(_lab("evening_regular"), "evening_regular", "short_greeting")
    assert "НЕ пересказывай человеку, когда он обычно заходит" in prompt and "вечером ты обычно заглядываешь сюда" in prompt
    assert "Покажи, что замечаешь привычное время" not in prompt
    for scenario in ("late_visit", "evening_regular", "back_after_pause"):
        assert "не называй" in ac.SCENARIO_GUIDE[scenario] or "НЕ называй" in ac.SCENARIO_GUIDE[scenario], scenario


def test_no_prepared_text_retells_the_visit_habit():
    """Эталоны, запасные тексты и шаблоны (то, что пользователь увидит, если модель недоступна) не рассказывают про привычное время."""
    texts = list(ac.REFERENCE_EXAMPLES.values())
    for options in ac.FALLBACKS.values():
        texts.extend([options] if isinstance(options, str) else options)
    assert len(texts) > 40
    for text in texts:
        assert not ac.tells_the_visit_habit(text), text


async def test_a_model_text_that_retells_the_habit_is_rewritten(uid, monkeypatch):
    _person(uid)
    told = "Привет! Вечером ты обычно заглядываешь сюда — рад тебя видеть. Как по энергии: начнём с «Читать 20 минут»?"
    answers = [told, "Привет! Рад тебя видеть. Как по энергии: начнём с «Читать 20 минут»?"]
    prompts = []

    async def fake(prompt, style):
        prompts.append(prompt)
        return answers.pop(0)
    monkeypatch.setattr(adam_proactive, "_generate", fake)
    text, source = await adam_proactive.compose_message(_lab("evening_regular"), "evening_regular", uid, "2026-10-20")
    assert source == "llm" and "обычно" not in text and len(prompts) == 2
    assert "не прошёл проверку" in prompts[1] and "когда человек обычно заходит" in prompts[1]


def test_the_chat_snapshot_mentions_the_rhythm_only_when_there_is_something_to_notice():
    from webapp.services.chat_brain import situation_lines
    late = "\n".join(situation_lines(_lab("late_visit")))
    assert "обычно заходит в приложение утром" in late
    ordinary = "\n".join(situation_lines(_with(_lab("late_visit"), arrived_now=False)))
    assert "Ритм человека" not in ordinary, "обычный разговор не должен каждый раз упираться в «ты обычно заходишь утром»"
    assert "Перед этим человека не было в приложении 4 дн." in "\n".join(situation_lines(_lab("back_after_pause")))


# ---------------------------------------------------------------------------------------------------------------- без повторов

def test_similar_texts_are_caught_and_different_ones_are_not():
    assert ac.too_similar(LATE, [LATE])
    assert ac.too_similar("Что случилось — сегодня тебя не было весь день. День пошёл не по плану?", [LATE])
    assert ac.too_similar("Что случилось, ты сегодня пропал? Расскажи подробнее.", ["Что случилось, ты сегодня исчез? Расскажи больше."])
    assert not ac.too_similar("Вот и ты. Что сегодня пошло иначе?", [LATE])
    assert not ac.too_similar("Давно не виделись — что изменилось?", [LATE, "Возьмём «Зарядку»: пять минут, и ритм вернётся."])
    assert not ac.too_similar("Любой текст", [])


def test_the_fallback_picks_a_variant_that_does_not_repeat_the_recent_message():
    state = _lab("late_visit")
    for day in range(1, 25):
        first = ac.fallback_text(state, "late_visit", 5, f"2026-10-{day:02d}")
        second = ac.fallback_text(state, "late_visit", 5, f"2026-10-{day:02d}", avoid=[first])
        assert second != first and not ac.too_similar(second, [first]), (day, first, second)


def test_the_angle_changes_with_the_last_message_and_is_stable_for_the_same_one():
    assert ac.pick_angle(1, "2026-10-14", "late_visit", "a") == ac.pick_angle(1, "2026-10-14", "late_visit", "a")
    seen = {ac.pick_angle(1, "2026-10-14", "late_visit", f"сообщение {i}") for i in range(30)}
    assert len(seen) >= 4, "подряд идущие сообщения не должны писаться по одной схеме"


async def test_a_repeating_model_text_is_rewritten_once_and_a_template_one_too(uid, monkeypatch):
    _person(uid)
    add_ai_message(uid, "assistant", LATE)
    answers = [LATE, "Странно видеть тебя только сейчас. Что-то пошло иначе?"]
    prompts = []

    async def fake(prompt, style):
        prompts.append(prompt)
        return answers.pop(0)
    monkeypatch.setattr(adam_proactive, "_generate", fake)
    text, source = await adam_proactive.compose_message(_lab("late_visit"), "late_visit", uid, "2026-10-14")
    assert source == "llm" and text.startswith("Странно видеть") and len(prompts) == 2
    assert "Подход на этот раз" in prompts[0] and "ВНИМАНИЕ" not in prompts[0] and "слишком похож" in prompts[1]

    prompts.clear()
    answers[:] = ["Привет! Как дела?", "Ты сегодня пришёл так поздно — что-то случилось?"]
    text, source = await adam_proactive.compose_message(_lab("late_visit"), "late_visit", uid, "2026-10-15")
    assert source == "llm" and "не прошёл проверку" in prompts[1]


async def test_when_the_model_keeps_repeating_the_text_is_a_fresh_fallback(uid, monkeypatch):
    _person(uid)
    add_ai_message(uid, "assistant", LATE)
    calls = []
    _generate_returns(monkeypatch, LATE, calls)
    text, source = await adam_proactive.compose_message(_lab("late_visit"), "late_visit", uid, "2026-10-14")
    assert source == "fallback" and len(calls) == 2 and not ac.too_similar(text, [LATE])


async def test_no_time_left_means_no_second_attempt(uid, monkeypatch):
    _person(uid)
    add_ai_message(uid, "assistant", LATE)
    calls = []
    _generate_returns(monkeypatch, LATE, calls)
    _text, source = await adam_proactive.compose_message(_lab("late_visit"), "late_visit", uid, "2026-10-14", timeout=1.0)
    assert source == "fallback" and len(calls) == 1, "первое сообщение в чате не должно заставлять человека ждать две попытки"


async def test_a_limited_ai_user_still_gets_a_contextual_text_without_the_model(uid, monkeypatch):
    _person(uid)
    conn = connect()
    conn.execute("UPDATE users SET ai_blocked_until=datetime('now','+1 day') WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    calls = []
    _generate_returns(monkeypatch, "не должно вызваться", calls)
    text, source = await adam_proactive.compose_message(_lab("late_visit"), "late_visit", uid, "2026-10-14")
    assert calls == [] and source == "fallback" and text


# ---------------------------------------------------------------------------------------------------------------- чат целиком

async def test_the_chat_greeting_notices_a_late_arrival_for_a_regular_user(client, uid, monkeypatch):
    from tests.test_ai_nudge import _user as nudge_user
    nudge_user(uid, chatted_before=True)
    _habits(uid, ["Зарядка", "Чтение"], done=0)
    rhythm = dict(_lab("late_visit")["rhythm"])
    monkeypatch.setattr(ac, "visit_rhythm", lambda user_id, now_local: dict(rhythm))
    real_pick = ac.pick_scenario
    monkeypatch.setattr(ac, "pick_scenario", lambda state, slot, chat_day=False: real_pick({**state, "hour": 19, "minute": 40}, slot, chat_day))
    prompts = []
    _generate_returns(monkeypatch, "Странно видеть тебя только сейчас. День пошёл иначе?", prompts)
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    greeting = (await reply.json())["greeting"]
    assert greeting["message"].startswith("Странно видеть тебя только сейчас")
    assert "обычно заходит в приложение утром" in prompts[0] and "Сценарий: " + ac.SCENARIO_GUIDE["late_visit"] in prompts[0]
    row = _log(uid)[0]
    assert (row["slot"], row["status"], row["scenario"], row["source"]) == ("app", "sent", "late_visit", "llm")
    assert not ac.is_enabled_for(uid), "и всё это без флага: пуши остаются выключенными"


def test_the_admin_lab_lists_the_new_scenarios_and_shows_the_rhythm_of_the_admin():
    labs = ac.lab_scenarios()
    for key in ("late_visit", "evening_regular", "back_after_pause"):
        assert labs[key][1] == "app" and ac.pick_scenario(labs[key][2], "app")[0] == key
    source = open("webapp/routes_admin.py", encoding="utf-8").read()
    assert '"rhythm": state.get("rhythm")' in source
