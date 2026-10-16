"""
Первое сообщение Адама в чате (16.10, п.15): человек получал изо дня в день «Привет, Имя! Как у тебя дела? Как прошёл день?» / «Что нового было
сегодня?» — одинаковые и не про его день. Общих фраз больше нет: сообщение строится по дню человека; нет текста (английский интерфейс и модель
не ответила, сбой) — Адам молчит, а метка «поздоровался сегодня» снимается, чтобы следующий заход в чат попробовал ещё раз.
"""
import itertools

import pytest

import adam_proactive
from db import add_habit, get_user, set_feature_flag
from db.ai_nudge import GREETINGS_DAY, GREETINGS_MORNING, get_ai_nudge_state
from db.core import connect
from db.users import set_language
from tests.conftest import sign_init_data
from tests.test_ai_nudge import _user as nudge_user

_uid_counter = itertools.count(849_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture
def every_hour(monkeypatch):
    """Часовой пояс по умолчанию UTC: до 12:00 UTC это «утро» (без вопросов) — час фиксируем, чтобы тест не зависел от времени запуска."""
    from db import adam_checkin as ac
    real = ac.pick_scenario
    monkeypatch.setattr(ac, "pick_scenario", lambda state, slot, chat_day=False: real({**state, "hour": 15}, slot, chat_day))


async def _greet(client, uid, **extra):
    reply = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid), **extra})
    assert reply.status == 200
    return (await reply.json())["greeting"]


def _model_down(monkeypatch):
    async def broken(prompt, style):
        raise RuntimeError("модель недоступна")
    monkeypatch.setattr(adam_proactive, "_generate", broken)


async def test_english_interface_without_a_model_gets_silence_not_a_russian_template(client, uid, every_hour, monkeypatch):
    nudge_user(uid, chatted_before=True)
    add_habit(uid, "Reading")
    set_language(uid, "en")
    _model_down(monkeypatch)
    assert await _greet(client, uid) is None
    assert get_ai_nudge_state(uid)["pending"] is True                         # метка «поздоровался» снята — следующий заход попробует снова
    assert get_user(uid)["ai_greet_day"] is None


async def test_the_next_visit_gets_a_contextual_greeting_once_the_model_is_back(client, uid, every_hour, monkeypatch):
    nudge_user(uid, chatted_before=True)
    add_habit(uid, "Чтение")
    set_language(uid, "en")
    _model_down(monkeypatch)
    assert await _greet(client, uid) is None
    set_language(uid, "ru")
    greeting = await _greet(client, uid)
    assert greeting and greeting["message"]
    assert not any(greeting["message"] == t.format(name="Аня") for t in GREETINGS_DAY + GREETINGS_MORNING)
    assert "дела" not in greeting["message"].lower() and "как прошёл день" not in greeting["message"].lower()
    assert await _greet(client, uid) is None                                  # в день по-прежнему одно первое сообщение


async def test_a_crash_while_composing_also_means_silence_and_a_retry_later(client, uid, monkeypatch):
    nudge_user(uid, chatted_before=True)

    async def boom(user_id, timeout=7.0):
        raise RuntimeError("что-то сломалось")
    monkeypatch.setattr(adam_proactive, "compose_app_greeting", boom)
    assert await _greet(client, uid) is None
    assert get_ai_nudge_state(uid)["pending"] is True
    conn = connect()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM ai_messages WHERE user_id=? AND role='assistant' AND message LIKE 'Привет, Аня!%'", (uid,)).fetchone()["n"] == 0
    finally:
        conn.close()


async def test_the_settings_demo_still_always_shows_something(client, uid, monkeypatch):
    nudge_user(uid, chatted_before=True)
    set_language(uid, "en")
    _model_down(monkeypatch)
    greeting = await _greet(client, uid, demo=True)
    assert greeting and greeting["message"]                                    # «Показать напоминание» в Настройках: приветствие приходит всегда


async def test_the_support_card_shows_the_interface_language(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    from db import add_user
    admin, person = uid + 50, uid + 1
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    add_user(person, "x", "Игрок")
    headers = {"Authorization": f"tma {sign_init_data(admin)}"}
    assert (await (await client.get(f"/api/admin/user/{person}", headers=headers)).json())["language"] == "ru"
    set_language(person, "en")
    assert (await (await client.get(f"/api/admin/user/{person}", headers=headers)).json())["language"] == "en"


# ------------------------------------------------------------------------------------------------ запасные тексты: достаточно разнообразия

SAMPLE = dict(p1="Чтение", done=1, total=3, left=2, missed=4, count_phrase="3 пункта", left_phrase="2 пункта",
              streak_line="Серия 5 дней держится, и её сохранит даже одна отметка. ", gap_phrase="уже 5 дней", last="Зарядка", x2_line="Окно x2 ещё открыто. ")
GREETING_SCENARIOS = ("morning", "morning_progress", "morning_done", "morning_empty", "free_chat", "back_after_pause", "all_done", "zero_done",
                      "zero_done_evening", "one_left", "evening_left", "behind", "steady", "just_done")


def _scenario_of(key):
    return "morning" if key.startswith("morning") else "positive" if key.startswith("positive") else key


def test_the_fallback_pools_of_the_first_message_are_big_enough_and_clean():
    from db import adam_checkin as ac
    for key in GREETING_SCENARIOS:
        pool = ac.FALLBACKS[key]
        assert len(set(pool)) == len(pool), f"{key}: есть повторяющиеся фразы"
        if key not in ("morning_done", "morning_empty", "morning_progress", "just_done", "behind", "evening_left", "back_after_pause", "morning"):
            assert len(pool) >= 7, f"{key}: фраз мало — они повторятся через несколько дней"
        for template in pool:
            body = template.format(**SAMPLE).replace("  ", " ").strip()
            assert ac.is_acceptable(body[0].upper() + body[1:], _scenario_of(key)), (key, body)
            assert "  " not in body and "{" not in body and "}" not in body, (key, body)
            low = body.lower()
            assert "как дела" not in low and "как прошёл день" not in low and "как прошел день" not in low, (key, body)


def test_a_week_of_evenings_without_a_model_does_not_repeat_the_same_text():
    from db import adam_checkin as ac
    state = {"user_id": 1, "first_name": "", "done": 0, "total": 2, "left": 2, "streak": 0, "pending": ["Чтение", "Спорт"],
             "pending_habits": ["Чтение", "Спорт"], "pending_tasks": [], "hour": 20, "minute": 0}
    texts = []
    for day in range(1, 8):
        texts.append(ac.fallback_text(state, "zero_done_evening", 4242, f"2026-10-{day:02d}", avoid=tuple(texts[-3:])))
    assert len(set(texts)) >= 6, texts
