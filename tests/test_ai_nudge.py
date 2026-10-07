"""
Напоминание «Адам хочет спросить, как дела» (db/ai_nudge.py, app.js::showAdamNudge,
ai_coach.js — первый вопрос Адама при заходе в чат).
"""
from pathlib import Path

from db import add_user
from db.ai import add_ai_message, get_ai_history
from db.ai_nudge import (
    FRESH_ACCOUNT_MINUTES,
    GREETINGS_DAY,
    GREETINGS_MORNING,
    claim_ai_greeting,
    get_ai_nudge_state,
    mark_ai_nudge_hint_shown,
    pick_greeting,
)
from db.core import connect

from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")


def _user(uid, *, old_account=True, chatted_before=False, name="Аня"):
    """Пользователь «с историей»: аккаунт старше 30 минут, писал Адаму вчера."""
    add_user(uid, "u", name)
    conn = connect()
    if old_account:
        conn.execute("UPDATE users SET created_at=datetime('now','-3 days') WHERE telegram_id=?", (uid,))
    if chatted_before:
        conn.execute("UPDATE users SET ai_intro_shown=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    if chatted_before:
        add_ai_message(uid, "user", "привет")
        add_ai_message(uid, "assistant", "Привет! Чем помочь?")
        conn = connect()
        conn.execute("UPDATE ai_messages SET created_at=datetime('now','-1 day') WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()


# --- состояние ---------------------------------------------------------------

def test_new_account_is_pending_but_not_eligible_for_the_first_half_hour(uid):
    add_user(uid, "u", "Аня")
    state = get_ai_nudge_state(uid)
    assert state["pending"] is True
    assert state["eligible"] is False, f"первые {FRESH_ACCOUNT_MINUTES} минут — стартовый сценарий"
    assert state["hint_shown_today"] is False and state["has_chatted"] is False


def test_old_account_that_did_not_chat_today_is_eligible(uid):
    _user(uid, chatted_before=True)
    assert get_ai_nudge_state(uid) == {
        "pending": True, "eligible": True, "hint_shown_today": False, "has_chatted": True,
    }


def test_message_to_adam_today_switches_the_reminder_off(uid):
    _user(uid, chatted_before=True)
    add_ai_message(uid, "user", "как дела?")
    state = get_ai_nudge_state(uid)
    assert state["pending"] is False and state["eligible"] is False


def test_answer_of_adam_alone_does_not_count_as_chatting(uid):
    """Считается только сообщение самого человека (role='user')."""
    _user(uid, chatted_before=True)
    add_ai_message(uid, "assistant", "Привет!")
    assert get_ai_nudge_state(uid)["pending"] is True


def test_hint_is_marked_once_per_day(uid):
    _user(uid, chatted_before=True)
    mark_ai_nudge_hint_shown(uid)
    state = get_ai_nudge_state(uid)
    assert state["hint_shown_today"] is True
    assert state["pending"] is True, "метка на «ИИ» остаётся, пока человек не зашёл в чат"


def test_unknown_user_has_nothing_pending():
    assert get_ai_nudge_state(987_654_321)["eligible"] is False


# --- приветствие в чате --------------------------------------------------------

def test_greeting_comes_once_a_day_and_lands_in_the_history(uid):
    _user(uid, chatted_before=True, name="Аня")
    greeting = claim_ai_greeting(uid)
    assert greeting and "Аня" in greeting["message"] and greeting["id"]
    assert claim_ai_greeting(uid) is None, "второй заход в тот же день — без повторного вопроса"

    last = get_ai_history(uid)[-1]
    assert last["role"] == "assistant" and last["message"] == greeting["message"]
    state = get_ai_nudge_state(uid)
    assert state["pending"] is False, "после захода в чат метка гаснет"


def test_no_greeting_for_a_newcomer_with_an_empty_chat(uid):
    """У тех, кто ещё ни разу не писал, в пустом чате и так приветственный экран с быстрыми действиями."""
    _user(uid, chatted_before=False)
    assert claim_ai_greeting(uid) is None


def test_no_greeting_if_the_user_already_wrote_today(uid):
    _user(uid, chatted_before=True)
    add_ai_message(uid, "user", "я тут")
    assert claim_ai_greeting(uid) is None


def test_demo_greeting_always_comes_and_does_not_burn_the_real_one(uid):
    _user(uid, chatted_before=True)
    assert claim_ai_greeting(uid, demo=True)
    assert claim_ai_greeting(uid, demo=True)
    assert get_ai_nudge_state(uid)["pending"] is True
    assert claim_ai_greeting(uid) is not None


def test_greeting_does_not_need_a_name_and_ignores_braces_in_it(uid):
    _user(uid, chatted_before=True, name="{name}")
    assert "{name}" in claim_ai_greeting(uid, demo=True)["message"]
    assert "друг" in pick_greeting(1, "друг", 15, "2026-10-07")


def test_greeting_is_stable_within_a_day_and_depends_on_the_time_of_day():
    morning = pick_greeting(5, "Аня", 8, "2026-10-07")
    assert morning == pick_greeting(5, "Аня", 8, "2026-10-07")
    assert morning in [t.format(name="Аня") for t in GREETINGS_MORNING]
    assert pick_greeting(5, "Аня", 20, "2026-10-07") in [t.format(name="Аня") for t in GREETINGS_DAY]
    # «как прошёл день» утром не звучит — такие фразы только в дневном наборе
    assert not any("прошёл день" in t for t in GREETINGS_MORNING)


def test_greetings_contain_the_wording_from_the_request():
    assert "Привет, {name}! Как у тебя дела? Как прошёл день?" in GREETINGS_DAY
    assert "Привет, {name}! Что нового было сегодня?" in GREETINGS_DAY
    assert not any(w in t for t in GREETINGS_DAY + GREETINGS_MORNING for w in ("успел", "сделал "))


# --- маршруты ----------------------------------------------------------------

async def test_bootstrap_carries_the_nudge_state(client, uid):
    _user(uid, chatted_before=True)
    r = await client.get("/api/bootstrap", headers={"Authorization": f"tma {sign_init_data(uid)}"})
    assert r.status == 200
    assert (await r.json())["ai_nudge"]["eligible"] is True


async def test_shown_route_marks_the_hint_and_requires_auth(client, uid):
    assert (await client.post("/api/ai/nudge/shown")).status == 401
    _user(uid, chatted_before=True)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    assert (await client.post("/api/ai/nudge/shown", headers=headers)).status == 200
    assert get_ai_nudge_state(uid)["hint_shown_today"] is True


async def test_greet_route_returns_the_greeting_once(client, uid):
    _user(uid, chatted_before=True)
    body = {"init_data": sign_init_data(uid)}
    first = await (await client.post("/api/ai/greet", json=body)).json()
    assert first["greeting"]["message"].startswith("Привет, ")
    second = await (await client.post("/api/ai/greet", json=body)).json()
    assert second["greeting"] is None
    demo = await (await client.post("/api/ai/greet", json={**body, "demo": True})).json()
    assert demo["greeting"] is not None


async def test_greet_route_rejects_a_forged_init_data(client, uid):
    r = await client.post("/api/ai/greet", json={"init_data": "hash=bad"})
    assert r.status == 401


# --- Mini App и чат ------------------------------------------------------------

def test_mini_app_wires_the_reminder_to_actions_and_to_the_evening():
    for needle in ("function showAdamNudge(", "function scheduleAdamNudge(", "function renderAiNudgeBadge(",
                   "function maybeScheduleEveningAdamNudge("):
        assert needle in APP_JS, needle
    patch = APP_JS[APP_JS.index("function applyActionPatch("):][:6500]
    assert "scheduleAdamNudge();" in patch
    plan = APP_JS[APP_JS.index("function applyPlanPatch("):][:600]
    assert "scheduleAdamNudge();" in plan
    assert "maybeScheduleEveningAdamNudge();" in APP_JS[APP_JS.index("function renderAll()"):][:1500]


def test_reminder_waits_for_quiet_and_is_shown_at_most_once_a_day():
    can = APP_JS[APP_JS.index("function adamNudgeCanShow()"):][:900]
    for needle in ("nudge?.eligible", "hint_shown_today", "celebrationOverlayOpen()", "keyboardOpen", "subpage-overlay.is-open"):
        assert needle in can, needle
    show = APP_JS[APP_JS.index("function showAdamNudge("):][:1800]
    assert "/api/ai/nudge/shown" in show and "state.ai_nudge.hint_shown_today = true" in show


def test_demo_button_in_settings_does_not_touch_the_server_state():
    assert 'id="adamNudgeDemoBtn"' in INDEX
    demo = APP_JS[APP_JS.index('getElementById("adamNudgeDemoBtn")'):][:420]
    assert "showAdamNudge({ demo: true })" in demo
    show = APP_JS[APP_JS.index("function showAdamNudge("):][:1200]
    assert show.index("if (demo)") < show.index("/api/ai/nudge/shown"), "в демо метка «показано» на сервер не уходит"


def test_skip_chip_of_the_reminder_only_closes_it_and_never_ends_the_tour():
    skip = APP_JS[APP_JS.index("getElementById('productOnboardingHintSkip')"):][:420]
    assert skip.index("adamNudgeActive") < skip.index("skipProductOnboarding()")


def test_ai_tab_glows_and_shows_the_dot_only_when_lit():
    badge = APP_JS[APP_JS.index("function renderAiNudgeBadge("):][:900]
    assert 'classList.toggle("is-adam-nudge", lit)' in badge and "hint_shown_today" in badge
    assert "#aiCoachBtn.is-adam-nudge::after" in CSS and "@keyframes adamNudgePulse" in CSS
    pulse = CSS[CSS.index("@keyframes adamNudgePulse"):][:160]
    assert "transform" in pulse and "filter" not in pulse     # только transform/opacity — дёшево для WebView


def test_chat_asks_the_server_for_adams_first_question_unless_an_intro_flow_is_running():
    assert "/api/ai/greet" in COACH_JS
    greet = COACH_JS[COACH_JS.index("const greetFromAdam"):][:700]
    assert "startParams.get('intro')" in greet and "nudge') === 'demo'" in greet
    # параметры читаются ДО maybeSendOnboardingIntro, который чистит URL
    assert COACH_JS.index("const startParams") < COACH_JS.index("const maybeSendOnboardingIntro")
    assert "ADAM печатает" in COACH_JS and "canRate: false" in COACH_JS
