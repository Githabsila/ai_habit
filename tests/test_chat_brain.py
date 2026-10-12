"""
Умные ответы Адама в чате (webapp/services/chat_brain.py): сценарий сообщения, настроение, снимок ситуации с парным заданием,
шутки только к месту и сквозная проверка, что всё это доходит до модели.
"""
import itertools

import pytest

from db import add_ai_message, add_user
from db import adam_checkin as ac
from db.pair_quests import PAIR_DAY_CAP, PAIR_GOAL, PAIR_WINDOW_DAYS
from tests.conftest import sign_init_data
from tests.test_adam_checkin import _habits, _user
from tests.test_adam_tz_v2 import _tstate
from tests.test_pair_habits import _mark, _quest
from tests.test_pair_quests import _sql
from webapp.services import chat_brain as cb

_uid_counter = itertools.count(860_000_000, 100)


@pytest.fixture
def uid():
    ac._PREFERRED_CACHE.clear()
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def _no_active_quests_left_behind():
    """Идущие задания этого файла не должны попадать в планировщик и settle_pair_quests() чужих тестов (БД общая на весь прогон)."""
    yield
    _sql("UPDATE pair_quests SET status='cancelled' WHERE status IN ('active','pending') AND inviter_id>=? AND inviter_id<?", (860_000_000, 870_000_000))


# ---------------------------------------------------------------------------
# СЦЕНАРИЙ СООБЩЕНИЯ
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("План на день", ["plan"]),
    ("составь мне план на сегодня", ["plan"]),
    ("Итоги дня", ["review"]),
    ("Главный фокус", ["focus"]),
    ("Разбор ситуации", ["situation_review"]),
    ("Помощь с задачей", ["task_help"]),
    ("Новая идея", ["idea"]),
    ("я так устал, нет сил ни на что", ["tired"]),
    ("хочу бросить бегать, слишком тяжело", ["quit_habit"]),
    ("не успеваю, горит дедлайн", ["overload"]),
    ("вчера сорвался и пропустил зарядку", ["slip"]),
    ("не получилось сегодня", ["slip"]),
    ("мне лень", ["lazy"]),
    ("всё сделал, ура!", ["win"]),
    ("сегодня получилось закрыть все привычки", ["win"]),
    ("как у меня дела с привычками", ["progress_q"]),
    ("как мои дела", ["progress_q"]),
    ("что делать сейчас?", ["next_step"]),
    ("с чего начать", ["next_step"]),
    ("что с парным заданием?", ["pair"]),
    ("как работает парное задание", ["pair"]),
    ("как работает заморозка серии", ["app_help"]),
    ("зачем нужны алмазы", ["app_help"]),
    ("поддержи меня пожалуйста", ["motivation"]),
    ("спасибо!", ["thanks"]),
    ("Привет", ["smalltalk"]),
    ("привет, я устал", ["tired", "smalltalk"]),
    ("Напиши функцию сортировки на Python", []),
    ("забыл купить молоко", []),
    ("", []),
])
def test_message_scenarios(text, expected):
    assert cb.detect_scenarios(text) == expected


def test_a_long_message_is_a_real_question_and_gets_no_scenario_by_keywords():
    assert cb.detect_scenarios("я устал " + "и вот что происходит с моим проектом " * 30) == []
    assert cb.detect_mood("я устал " + "и вот что происходит с моим проектом " * 30) == "нейтрально"


def test_a_greeting_inside_a_longer_sentence_is_not_small_talk():
    assert "smalltalk" not in cb.detect_scenarios("привет, расскажи подробно как лучше выстроить утреннюю рутину на неделю вперёд")


def test_every_scenario_has_an_instruction_and_at_most_two_are_returned():
    keys = {key for key, _pattern in cb._RULES} | {"app_help"}
    assert keys == set(cb.GUIDE)
    assert len(cb.detect_scenarios("привет, я устал, не успеваю, сорвался и мне лень")) == cb.MAX_SCENARIOS


@pytest.mark.parametrize("text,mood", [
    ("я так устал", "устал"),
    ("меня всё бесит", "раздражён"),
    ("волнуюсь перед собеседованием", "тревожен"),
    ("грустно и обидно", "расстроен"),
    ("ура, получилось!", "воодушевлён"),
    ("не получилось", "нейтрально"),
    ("привет", "нейтрально"),
])
def test_mood_by_words(text, mood):
    assert cb.detect_mood(text) == mood


def test_humor_only_when_it_is_not_hard_for_the_person():
    assert cb.humor_allowed("нейтрально", []) and cb.humor_allowed("воодушевлён", ["win"])
    assert not cb.humor_allowed("устал", []) and not cb.humor_allowed("расстроен", ["win"])
    assert not cb.humor_allowed("нейтрально", ["slip"]) and not cb.humor_allowed("нейтрально", ["quit_habit"])


# ---------------------------------------------------------------------------
# СНИМОК СИТУАЦИИ (чистая функция по состоянию)
# ---------------------------------------------------------------------------

def _text(lines):
    return "\n".join(lines)


def test_situation_names_time_what_is_left_and_when_it_is_due():
    state = _tstate([("Зарядка", True), ("Чтение", False), ("Английский", False)], [("Отчёт", False, True)], hour=16, minute=30)
    state["habits"][1]["planned"] = "10:00"
    state["habits"][2]["planned"] = "20:00"
    text = _text(cb.situation_lines(state))
    assert "Сейчас 16:30 (день), среда" in text
    assert "привычки 1/3" in text and "задачи 0/1" in text
    assert "Осталось из привычек: «Чтение», «Английский»" in text and "Осталось из задач: «Отчёт»" in text
    assert "Время уже прошло, а привычка не выполнена: «Чтение» (10:00)" in text
    assert "Ближайшая по времени привычка: «Английский» в 20:00" in text
    assert "Главная задача дня «Отчёт» ещё не выполнена" in text


def test_situation_has_history_streak_record_and_struggling_habit():
    state = _tstate([("Зарядка", False)], best_streak=6, struggling={"title": "Чтение", "missed": 4})
    text = _text(cb.situation_lines(state, back_days=0))
    assert "Вчера закрыто привычек: 3; дней с отметками за последнюю неделю: 6 из 7" in text
    assert "Серия 4 дн.; до личного рекорда (6 дн.) осталось 2" in text
    assert "«Чтение» не получается: пропущена 4 из последних 5 дней" in text
    young = _text(cb.situation_lines(_tstate([("Зарядка", False)], pace={"days": 1, "avg_by_now": 1.0, "avg_total": 1.0, "yesterday": 1, "active_7": 1})))
    assert "Вчера закрыто" not in young, "мало истории — цифр не называем"


def test_returning_after_a_pause_is_named_without_blame():
    text = _text(cb.situation_lines(_tstate([("Зарядка", False)]), back_days=4))
    assert "вернулся после 4 дн. без отметок" in text and "без упрёков" in text
    assert "вернулся" not in _text(cb.situation_lines(_tstate([("Зарядка", False)]), back_days=0))


def test_night_and_gender_hints():
    night = _text(cb.situation_lines(_tstate([("Зарядка", False)], hour=2, minute=10)))
    assert "Глубокая ночь" in night
    assert "Глубокая ночь" not in _text(cb.situation_lines(_tstate([("Зарядка", False)], hour=15)))
    assert "Пол: женский" in _text(cb.situation_lines(_tstate([("Зарядка", False)])))
    assert "Пол неизвестен" in _text(cb.situation_lines(_tstate([("Зарядка", False)], gender=None)))


def test_everything_done_and_nothing_planned():
    done = _text(cb.situation_lines(_tstate([("Зарядка", True), ("Чтение", True)])))
    assert "На сегодня всё выполнено" in done and "Осталось из привычек" not in done
    empty = _text(cb.situation_lines(_tstate([])))
    assert "Привычек и задач на сегодня нет" in empty and "всё выполнено" not in empty


def test_pair_facts_are_appended_as_is():
    lines = cb.situation_lines(_tstate([("Зарядка", False)]), ["Парное задание с напарником «Борис»: общий счёт 9 из 20"])
    assert lines[-1].startswith("Парное задание с напарником «Борис»")


# ---------------------------------------------------------------------------
# ИНСТРУКЦИИ МОДЕЛИ
# ---------------------------------------------------------------------------

def test_pair_question_brings_the_rules_with_numbers_from_the_constants():
    note = cb.build_note(["pair"])
    assert f"{PAIR_GOAL} привычек за {PAIR_WINDOW_DAYS} дней" in note and f"до {PAIR_DAY_CAP} разных привычек в день" in note
    assert "только в день, когда отметились оба" in note and "Сценарий сообщения:" in note


def test_pair_mention_rule_is_added_only_when_there_is_a_quest():
    assert cb.PAIR_USE_RULE in cb.build_note(["win"], has_pair=True)
    assert cb.PAIR_USE_RULE not in cb.build_note(["win"], has_pair=False)
    assert cb.PAIR_USE_RULE not in cb.build_note(["pair"], has_pair=True), "про задание спросили прямо — правила уже даны"


def test_app_questions_forbid_inventing_rules():
    assert cb.APP_HONESTY in cb.build_note(["app_help"])
    assert cb.build_note([]) == ""


# ---------------------------------------------------------------------------
# С БАЗОЙ
# ---------------------------------------------------------------------------

def test_situation_from_the_database_includes_the_active_pair_quest(uid):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1]); _mark(b, quest_id, [1, 2, 0])
    block = cb.build_situation(a)
    assert block.startswith("Снимок ситуации")
    assert "• Парное задание с напарником «Борис»: общий счёт 9 из 20" in block
    assert "Сегодня закрыто:" in block and "Сейчас " in block


def test_prepare_collects_everything_for_the_chat(uid):
    _user(uid)
    _habits(uid, ["Зарядка", "Чтение"], done=1)
    brain = cb.prepare(uid, "Я так устал, нет сил")
    assert brain["keys"] == ["tired"] and brain["mood"] == "устал" and brain["humor_ok"] is False
    assert "Сценарий сообщения: Человек устал" in brain["scenario_note"]
    assert "Осталось из привычек: «Чтение»" in brain["situation"] or "Осталось из привычек: «Зарядка»" in brain["situation"]
    plain = cb.prepare(uid, "Напиши функцию сортировки")
    assert plain["keys"] == [] and plain["scenario_note"] == "" and plain["humor_ok"] is True


def test_prepare_survives_a_broken_snapshot(uid, monkeypatch):
    _user(uid)
    monkeypatch.setattr(cb, "build_situation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    brain = cb.prepare(uid, "мне лень")
    assert brain["situation"] == "" and brain["keys"] == ["lazy"]


# ---------------------------------------------------------------------------
# МАРШРУТ ЧАТА: ВСЁ ДОХОДИТ ДО МОДЕЛИ
# ---------------------------------------------------------------------------

def _capture_model(monkeypatch):
    import webapp.routes_ai_miniapp as route_mod
    from tests.test_ai_quota import _mock_ai_chat_deps

    _mock_ai_chat_deps(monkeypatch)
    seen = {}

    async def fake_solve(**kwargs):
        seen.update(kwargs)
        return {"answer": "Хорошо.", "is_crisis": False, "suggested_habit": None, "complexity": "сложно"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", fake_solve)
    return seen


def _two_adam_replies(user_id):
    """Последние шесть реплик переписки с двумя ответами Адама: ровно так обработчик решает, что пора «шутке в конце» (2 + 1 кратно 3)."""
    for role, text in (("user", "привет"), ("user", "ты тут?"), ("assistant", "Да."), ("user", "отлично"), ("user", "ещё вопрос"), ("assistant", "Слушаю.")):
        add_ai_message(user_id, role, text)


async def test_chat_gives_the_model_the_situation_the_scenario_and_the_mood(client, uid, monkeypatch):
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1])
    seen = _capture_model(monkeypatch)
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(a), "message": "Я так устал, нет сил"})
    assert resp.status == 200
    assert "Снимок ситуации" in seen["user_context"] and "Парное задание с напарником «Борис»" in seen["user_context"]
    assert "Сценарий сообщения: Человек устал" in seen["scenario_note"] and cb.PAIR_USE_RULE in seen["scenario_note"]
    assert seen["mood"] == "устал"


async def test_chat_skips_the_joke_when_it_is_hard_for_the_person_but_keeps_it_otherwise(client, uid, monkeypatch):
    a, b, quest_id = _quest(uid)
    _two_adam_replies(a)                                       # третий ответ Адама — тот самый, что «с шуткой»
    seen = _capture_model(monkeypatch)
    await client.post("/api/ai/chat", json={"init_data": sign_init_data(a), "message": "Мне тяжело, я устал"})
    assert seen["humor_note"] == ""
    seen.clear()
    _two_adam_replies(a)                                       # теперь ответов Адама кратно трём
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(a), "message": "Расскажи про сон"})
    assert resp.status == 200 and "шутк" in seen["humor_note"]


async def test_a_broken_snapshot_never_breaks_the_answer(client, uid, monkeypatch):
    a, b, quest_id = _quest(uid)
    seen = _capture_model(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cb, "prepare", boom)
    resp = await client.post("/api/ai/chat", json={"init_data": sign_init_data(a), "message": "Как начать бегать?"})
    assert resp.status == 200 and seen["scenario_note"] == "" and seen["mood"] == "нейтрально"


def test_model_layer_uses_the_mood_hint_and_the_scenario_note(monkeypatch):
    import asyncio
    import multi_agent

    captured = {}

    async def fake_ask(system, user, **kwargs):
        captured["system"], captured["user"] = system, user
        return "Ок."

    monkeypatch.setattr(multi_agent, "_ask", fake_ask)
    result = asyncio.run(multi_agent.solve_task_multiagent(
        task="Я устал", history="", user_context="Снимок ситуации", style="neutral",
        scenario_note="Сценарий сообщения: Человек устал.", mood="устал",
    ))
    assert result["mood"] == "устал"
    assert multi_agent.MOOD_GUIDANCE["устал"] in captured["system"] and "Сценарий сообщения: Человек устал." in captured["system"]
    captured.clear()
    strange = asyncio.run(multi_agent.solve_task_multiagent(task="Привет", mood="что-то странное"))
    assert strange["mood"] == "нейтрально", "неизвестное настроение считается нейтральным"


# ---------------------------------------------------------------------------
# ПАМЯТЬ ДИАЛОГА И КНОПКА «ДОБАВИТЬ ПРИВЫЧКУ»
# ---------------------------------------------------------------------------

def test_the_last_answer_of_adam_reaches_the_model_whole(uid):
    from webapp.services.ai_utils import build_history_text
    add_user(uid, "u", "Аня")
    plan = "1. Зарядка 10 минут. " * 30                                   # длинный план, который человек хочет уточнить
    add_ai_message(uid, "user", "составь план")
    add_ai_message(uid, "assistant", plan)
    add_ai_message(uid, "user", "а второй пункт подробнее?")
    short = build_history_text(uid, limit=6, max_chars_per_msg=180)
    assert plan[:300] not in short, "по умолчанию длинные реплики режутся"
    whole = build_history_text(uid, limit=6, max_chars_per_msg=180, last_assistant_chars=1200)
    assert plan[:600] in whole
    assert "Пользователь: составь план" in whole and whole.rstrip().endswith("а второй пункт подробнее?")


def test_only_the_last_answer_is_kept_long(uid):
    from webapp.services.ai_utils import build_history_text
    add_user(uid, "u", "Аня")
    old, new = "СТАРЫЙ " * 100, "НОВЫЙ " * 100
    add_ai_message(uid, "assistant", old)
    add_ai_message(uid, "user", "дальше")
    add_ai_message(uid, "assistant", new)
    text = build_history_text(uid, limit=6, max_chars_per_msg=180, last_assistant_chars=1200)
    assert text.count("НОВЫЙ") == 100 and text.count("СТАРЫЙ") < 40


def test_the_chat_asks_for_a_longer_history_and_the_whole_last_answer():
    import webapp.routes_ai_miniapp as route_mod
    import inspect
    assert "limit=6, max_chars_per_msg=180, last_assistant_chars=1200" in inspect.getsource(route_mod._answer_chat_inner)


@pytest.mark.parametrize("answer,expected", [
    ("Попробуй добавить привычку «Читать 10 минут» вечером.", True),
    ("Рекомендую завести привычку пить воду.", True),
    ("Отличный день: все привычки выполнены!", False),
    ("Попробуй сделать паузу на пять минут.", False),
    ("", False),
])
def test_habit_suggestion_gate(answer, expected):
    import multi_agent
    assert multi_agent._may_suggest_habit(answer) is expected


def _fake_model(monkeypatch, answer, habit_json):
    import multi_agent
    calls = []

    async def fake_ask(system, user, **kwargs):
        calls.append(system)
        return habit_json if system == multi_agent.HABIT_EXTRACT_SYSTEM else answer

    monkeypatch.setattr(multi_agent, "_ask", fake_ask)
    return calls


def test_a_suggested_habit_comes_back_with_the_answer(monkeypatch):
    import asyncio
    import multi_agent
    calls = _fake_model(monkeypatch, "Попробуй добавить привычку «Читать 10 минут» перед сном.", '{"habit": "Читать 10 минут"}')
    result = asyncio.run(multi_agent.solve_task_multiagent(task="Как лучше засыпать?"))
    assert result["suggested_habit"] == "Читать 10 минут" and len(calls) == 2


def test_no_extra_model_call_when_the_answer_suggests_nothing(monkeypatch):
    import asyncio
    import multi_agent
    calls = _fake_model(monkeypatch, "Сегодня хороший день, продолжай в том же духе.", '{"habit": "Не должно вызываться"}')
    result = asyncio.run(multi_agent.solve_task_multiagent(task="Привет"))
    assert result["suggested_habit"] is None and len(calls) == 1


def test_a_slow_extraction_never_holds_the_answer_back(monkeypatch):
    import asyncio
    import multi_agent

    async def slow_extract(answer):
        await asyncio.sleep(5)
        return "Что-то"

    monkeypatch.setattr(multi_agent, "extract_suggested_habit", slow_extract)
    monkeypatch.setattr(multi_agent, "SUGGEST_HABIT_TIMEOUT", 0.05)
    assert asyncio.run(multi_agent.suggest_habit_from_answer("Попробуй добавить привычку бегать.")) is None


def test_the_button_is_not_offered_for_a_habit_that_already_exists_or_when_the_list_is_full(uid):
    import webapp.routes_ai_miniapp as route_mod
    _user(uid)
    _habits(uid, ["Читать 10 минут", "Зарядка"])
    assert route_mod._fresh_suggestion(uid, "читать 10 минут") is None
    assert route_mod._fresh_suggestion(uid, "Зарядка по утрам") is None, "почти то же самое, что уже есть"
    assert route_mod._fresh_suggestion(uid, "  Пить   воду ") == "Пить воду"
    assert route_mod._fresh_suggestion(uid, None) is None and route_mod._fresh_suggestion(uid, "   ") is None
    _user(uid + 1)
    _habits(uid + 1, [f"Привычка номер {i}" for i in range(10)])
    assert route_mod._fresh_suggestion(uid + 1, "Совсем новая") is None, "в списке уже десять привычек"


# ---------------------------------------------------------------------------
# БОТ В TELEGRAM (handlers/ai.py -> ai_service.chat) ОТВЕЧАЕТ ТАК ЖЕ УМНО, КАК MINI APP
# ---------------------------------------------------------------------------

async def test_the_telegram_bot_chat_gets_the_same_snapshot_scenario_and_mood(uid, monkeypatch):
    from webapp.services import ai_service
    a, b, quest_id = _quest(uid)
    _mark(a, quest_id, [2, 3, 1])
    seen = {}

    async def fake_solve(**kwargs):
        seen.update(kwargs)
        return {"answer": "Ок.", "is_crisis": False, "suggested_habit": None, "complexity": "прямой быстрый ответ"}

    monkeypatch.setattr(ai_service, "solve_task_multiagent", fake_solve)
    result = await ai_service.chat(a, "Я так устал, нет сил")
    assert result["answer"] == "Ок."
    assert "Снимок ситуации" in seen["user_context"] and "Парное задание с напарником «Борис»" in seen["user_context"]
    assert "Человек устал" in seen["scenario_note"] and seen["mood"] == "устал"


async def test_the_telegram_chat_leaves_long_requests_lean(uid, monkeypatch):
    from webapp.services import ai_service
    a, b, quest_id = _quest(uid)
    seen = {}

    async def fake_solve(**kwargs):
        seen.update(kwargs)
        return {"answer": "Ок.", "is_crisis": False, "suggested_habit": None, "complexity": "сложно"}

    monkeypatch.setattr(ai_service, "solve_task_multiagent", fake_solve)
    await ai_service.chat(a, "разбери мой проект " * 200)
    assert "Снимок ситуации" not in seen["user_context"] and seen["scenario_note"] == ""


def test_enrich_keeps_the_old_context_when_everything_fails(uid, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cb, "prepare", boom)
    context, brain = cb.enrich(uid, "мне лень", "Привычки: ...")
    assert context == "Привычки: ..." and brain == cb.empty_brain()
