"""
«Знакомство с Адамом» (20.10): тест после онбординга. Адам узнаёт, что для человека важно, что мешает, когда у него больше сил и сколько он
держит фокус; подбирает метод планирования дня (Помодоро 25 + 5, блоки 45 + 10, глубокая работа 90 + 20, «три главных дела»), предлагает
первые привычки и начинает разговор о целях; «План на день» в чате строится по выбранному методу. Браузера в тестах нет: правила подбора,
хранение и маршруты проверяем по-настоящему, окно — по тексту разметки и скрипта.
"""
import itertools
import re
from pathlib import Path

import pytest

from db import add_user, add_habit, adam_profile as ap
from db.core import connect
from tests.conftest import sign_init_data
from webapp.services import chat_brain as cb

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")

_uid_counter = itertools.count(855_000_000, 100)

ALL = {"focus": "study", "struggle": "start", "energy": "morning", "span": "s25", "hours": "h2", "talk": "month"}


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _answers(**changes):
    return {**ALL, **changes}


def _set_created(uid_, day):
    conn = connect()
    conn.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (f"{day} 10:00:00", uid_))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------------------------------------------- подбор метода

@pytest.mark.parametrize("changes, method", [
    ({"span": "s25"}, "pomodoro"),
    ({"span": "s45", "struggle": "quit", "hours": "h2"}, "focus45"),
    ({"span": "s90", "struggle": "no_goal", "hours": "h4p", "energy": "day"}, "deep90"),
    ({"span": "unknown", "struggle": "chaos", "hours": "h1"}, "priorities"),
    ({"span": "unknown", "struggle": "no_goal", "hours": "h2"}, "priorities"),
    ({"span": "unknown", "struggle": "distract", "hours": "h2"}, "pomodoro"),
])
def test_the_method_follows_the_answers(changes, method):
    assert ap.recommend(_answers(**changes))["method"] == method


def test_deep_work_is_never_recommended_to_someone_with_a_short_focus_or_an_hour_a_day():
    assert ap.recommend(_answers(span="s25", hours="h4p"))["method"] != "deep90"
    assert ap.recommend(_answers(span="s90", hours="h1", struggle="quit"))["method"] != "deep90"


def test_no_signal_gives_the_middle_option_and_every_recommendation_explains_itself():
    assert ap.recommend({})["method"] == ap.DEFAULT_METHOD
    for changes in ({"span": "s25"}, {"span": "s45"}, {"span": "s90", "hours": "h4"}, {"span": "unknown", "struggle": "chaos"}):
        reasons = ap.recommend(_answers(**changes))["reasons"]
        assert 1 <= len(reasons) <= 2 and all(r.strip() for r in reasons)


def test_unknown_questions_and_values_are_dropped():
    cleaned = ap.clean_answers({"focus": "study", "span": "s999", "hack": "1", "talk": None})
    assert cleaned == {"focus": "study"}
    assert ap.clean_answers("nope") == {}


def test_every_option_of_every_question_gives_a_valid_result():
    for question in ap.QUESTIONS:
        for value, _icon, _label in question["options"]:
            result = ap.build_result(_answers(**{question["key"]: value}))
            assert result["method"] in ap.METHODS and 1 <= len(result["habits"]) <= 3


def test_methods_carry_their_block_lengths_and_a_plan_rule():
    assert (ap.METHODS["pomodoro"]["work"], ap.METHODS["pomodoro"]["rest"]) == (25, 5)
    assert (ap.METHODS["focus45"]["work"], ap.METHODS["focus45"]["rest"]) == (45, 10)
    assert (ap.METHODS["deep90"]["work"], ap.METHODS["deep90"]["rest"]) == (90, 20)
    assert ap.METHODS["priorities"]["work"] is None
    for key, method in ap.METHODS.items():
        assert method["rule"] and method["blurb"] and method["title"], key
    assert set(ap.METHOD_ORDER) == set(ap.METHODS)


def test_suggested_habits_fit_the_focus_the_struggle_and_skip_what_is_already_there():
    habits = ap.suggest_habits(_answers(focus="health", struggle="distract"))
    assert habits[0] == "Зарядка 10 минут" and "Фокус-блок 25 минут без телефона" in habits and len(habits) == 3
    again = ap.suggest_habits(_answers(focus="health", struggle="distract"), existing_titles=["зарядка 10 минут"])
    assert "Зарядка 10 минут" not in again and len(again) == 3
    assert all(len(title) <= 50 for ideas in ap.HABIT_IDEAS.values() for title in ideas)
    assert all(len(title) <= 50 for title in ap.STRUGGLE_HABITS.values())


def test_the_best_time_is_named_only_when_the_person_has_one():
    assert ap.build_result(_answers(energy="morning"))["peak"] == {"label": "утро", "window": "до 12:00"}
    assert ap.build_result(_answers(energy="varies"))["peak"] is None


# ---------------------------------------------------------------------------------------------------------------- хранение

def test_saving_needs_every_answer_and_keeps_the_chosen_method(uid):
    add_user(uid, "u", "Аня")
    assert ap.save_profile(uid, {"focus": "study"}) == {"error": "incomplete"}
    assert ap.get_profile(uid) is None
    saved = ap.save_profile(uid, _answers(), "deep90")
    profile = ap.get_profile(uid)
    assert saved["ok"] and saved["method"] == "deep90" == profile["method"]
    assert profile["answers"] == _answers() and profile["completed_at"] and not profile["skipped_at"]
    # неизвестный метод заменяется рекомендованным, повторное сохранение перезаписывает
    again = ap.save_profile(uid, _answers(span="s45", struggle="quit"), "nope")
    assert again["method"] == "focus45" == ap.get_profile(uid)["method"]


def test_skipping_never_overwrites_a_finished_profile(uid):
    add_user(uid, "u", "Аня")
    ap.skip_profile(uid)
    assert ap.get_profile(uid)["skipped_at"] and not ap.get_profile(uid)["completed_at"]
    ap.save_profile(uid, _answers())
    ap.skip_profile(uid)
    profile = ap.get_profile(uid)
    assert profile["completed_at"] and profile["skipped_at"] is None


def test_the_window_opens_by_itself_only_for_new_people_without_an_answer(uid, monkeypatch):
    add_user(uid, "u", "Аня")
    assert ap.should_show(uid) is True, "аккаунт создан сегодня — это «новый» человек"
    ap.skip_profile(uid)
    assert ap.should_show(uid) is False, "«Позже» — больше само не открывается"
    old = uid + 1
    add_user(old, "o", "Олег")
    _set_created(old, "2026-10-01")
    assert ap.should_show(old) is False, "кто пришёл раньше релиза, видит окно только по флагу"
    from db.feature_flags import set_feature_flag
    set_feature_flag(ap.FLAG_KEY, True, 100)
    try:
        assert ap.should_show(old) is True
    finally:
        set_feature_flag(ap.FLAG_KEY, False, 0)
    ap.save_profile(old, _answers())
    assert ap.should_show(old) is False and ap.should_show(uid + 99) is False, "нет профиля у несуществующего человека"


def test_public_state_names_the_chosen_method(uid):
    add_user(uid, "u", "Аня")
    assert ap.public_state(uid) == {"show": True, "done": False, "method": None, "method_title": None}
    ap.save_profile(uid, _answers(), "pomodoro")
    assert ap.public_state(uid) == {"show": False, "done": True, "method": "pomodoro", "method_title": "Помодоро 25 + 5"}


def test_admin_onboarding_reset_also_resets_the_introduction(uid):
    from db.users import reset_onboarding
    add_user(uid, "u", "Аня")
    ap.save_profile(uid, _answers())
    reset_onboarding(uid)
    assert ap.get_profile(uid) is None and ap.should_show(uid) is True


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_routes_need_a_signed_in_person(client):
    assert (await client.get("/api/adam-profile")).status == 401
    for path in ("/api/adam-profile", "/api/adam-profile/recommend", "/api/adam-profile/skip"):
        assert (await client.post(path, json={})).status == 401


async def test_quiz_payload_carries_questions_methods_and_previous_answers(client, uid):
    add_user(uid, "u", "Аня")
    body = await (await client.get("/api/adam-profile", headers=_headers(uid))).json()
    assert [q["key"] for q in body["questions"]] == list(ap.QUESTION_KEYS)
    assert all(q["options"] and all({"value", "icon", "label"} <= set(o) for o in q["options"]) for q in body["questions"])
    assert [m["key"] for m in body["methods"]] == list(ap.METHOD_ORDER)
    assert body["profile"] == {"done": False, "answers": {}, "method": None}
    ap.save_profile(uid, _answers(), "focus45")
    again = await (await client.get("/api/adam-profile", headers=_headers(uid))).json()
    assert again["profile"] == {"done": True, "answers": _answers(), "method": "focus45"}


async def test_recommend_is_stateless_and_leaves_out_habits_the_person_already_has(client, uid):
    add_user(uid, "u", "Аня")
    add_habit(uid, "Учёба 25 минут")
    incomplete = await client.post("/api/adam-profile/recommend", json={"answers": {"focus": "study"}}, headers=_headers(uid))
    assert incomplete.status == 400
    r = await client.post("/api/adam-profile/recommend", json={"answers": _answers()}, headers=_headers(uid))
    result = (await r.json())["result"]
    assert r.status == 200 and result["method"] == "pomodoro" and "Учёба 25 минут" not in result["habits"]
    assert ap.get_profile(uid) is None, "рекомендация ничего не сохраняет"


async def test_saving_writes_the_profile_the_event_and_updates_the_state(client, uid):
    add_user(uid, "u", "Аня")
    bad = await client.post("/api/adam-profile", json={"answers": {"focus": "study"}}, headers=_headers(uid))
    assert bad.status == 400
    r = await client.post("/api/adam-profile", json={"answers": _answers(), "method": "priorities"}, headers=_headers(uid))
    body = await r.json()
    assert r.status == 200 and body["method"] == "priorities" and body["adam_intro"]["done"] is True
    assert body["result"]["method"] == "pomodoro", "итог — рекомендация; выбранный метод человек видит в adam_intro"
    conn = connect()
    try:
        rows = conn.execute("SELECT step FROM user_events WHERE user_id=? AND name='adam_intro_done'", (uid,)).fetchall()
    finally:
        conn.close()
    assert [row["step"] for row in rows] == ["priorities"]
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["adam_intro"] == {"show": False, "done": True, "method": "priorities", "method_title": "Три главных дела"}


async def test_skip_is_recorded_and_stops_the_window(client, uid):
    add_user(uid, "u", "Аня")
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["adam_intro"]["show"] is True
    assert (await client.post("/api/adam-profile/skip", headers=_headers(uid))).status == 200
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["adam_intro"] == {"show": False, "done": False, "method": None, "method_title": None}
    conn = connect()
    try:
        names = [r["name"] for r in conn.execute("SELECT name FROM user_events WHERE user_id=?", (uid,)).fetchall()]
    finally:
        conn.close()
    assert "adam_intro_skip" in names


# ---------------------------------------------------------------------------------------------------------------- чат Адама

def test_the_chat_knows_the_profile_and_builds_the_plan_by_the_chosen_method(uid):
    add_user(uid, "u", "Аня")
    assert ap.profile_lines(ap.get_profile(uid)) == [] and ap.plan_rule(None) == ""
    ap.save_profile(uid, _answers(), "deep90")
    profile = ap.get_profile(uid)
    lines = ap.profile_lines(profile)
    assert "учёба" in lines[0] and "утро" in lines[0] and "трудно начать" in lines[0].lower() or "откладываю" in lines[0].lower()
    assert "Глубокая работа 90 + 20" in lines[1]
    note = cb.build_note(["plan"], profile=profile)
    assert "90 минут" in note and "максимум 4 шага" in note and "заменяет" in note and "утро (до 12:00)" in note
    assert "90 минут" not in cb.build_note(["review"], profile=profile), "метод влияет только на план дня"
    assert "90 минут" not in cb.build_note(["plan"], profile=None)


def test_the_situation_snapshot_contains_the_profile_only_after_the_introduction(uid):
    add_user(uid, "u", "Аня")
    assert "Из знакомства с Адамом" not in cb.prepare(uid, "привет")["situation"]
    ap.save_profile(uid, _answers(), "pomodoro")
    brain = cb.prepare(uid, "составь мне план на сегодня")
    assert "Из знакомства с Адамом" in brain["situation"] and "Помодоро 25 + 5" in brain["situation"]
    assert "25 минут" in brain["scenario_note"] and brain["keys"] == ["plan"]


def test_goal_conversations_get_their_own_scenario():
    assert "goals" in cb.GUIDE and "goals" in cb.SCENARIO_LABELS
    for text in ("Давай обсудим мою цель на ближайший месяц: помоги сформулировать её и разбить на шаги.",
                 "Давай обсудим мою большую цель на год и дальше. С чего мне начать?",
                 "хочу поставить цель на год", "помоги с долгосрочными планами", "мои цели"):
        assert "goals" in cb.detect_scenarios(text), text
    assert cb.detect_scenarios("Составь мне план на сегодня.") == ["plan"]
    assert "plan" not in cb.detect_scenarios("Давай обсудим мою цель на ближайший месяц")


def test_every_conversation_starter_in_the_chat_page_lands_in_a_sensible_scenario():
    block = COACH_JS[COACH_JS.index("params.get('intro') === 'plans'"):][:900]
    starters = dict(re.findall(r"(\w+): '([^']+)'", block))
    assert set(starters) == {"month", "year", "today", "look"}
    expected = {"month": "goals", "year": "goals", "today": "plan", "look": "next_step"}
    for key, text in starters.items():
        assert expected[key] in cb.detect_scenarios(text), (key, text)
    assert "sendText(" in block and "history.replaceState" in block and "sentOnboardingIntroRef.current = true;" in block


def test_chat_page_script_version_was_bumped_for_the_new_intro():
    html = (STATIC / "ai_miniapp_styled.html").read_text(encoding="utf-8")
    assert "ai_coach.js?v=20261020_PLANS_V54" in html


# ---------------------------------------------------------------------------------------------------------------- окно в приложении

def test_window_markup_has_every_part_and_sits_in_the_settings_too():
    block = INDEX[INDEX.index('id="adamIntroOverlay"'):]
    block = block[:block.index('id="appTourOverlay"')]
    assert re.search(r'<div class="adam-intro-overlay" id="adamIntroOverlay" hidden aria-hidden="true">', INDEX)
    for needle in ('role="dialog"', 'aria-modal="true"', 'id="adamIntroBack"', 'id="adamIntroProgress"', 'id="adamIntroLater"',
                   'id="adamIntroBody"', 'id="adamIntroTitle"', 'id="adamIntroContent"', 'id="adamIntroNext"', 'id="adamIntroClose"'):
        assert needle in block, needle
    assert INDEX.index('id="startQuizOverlay"') < INDEX.index('id="adamIntroOverlay"') < INDEX.index('id="appTourOverlay"')
    assert 'id="adamIntroBtn"' in INDEX and "Знакомство с Адамом" in INDEX


def test_window_is_part_of_the_onboarding_chain_after_the_streak_window_and_before_the_hints():
    chain = APP_JS[APP_JS.index("const afterStreak = () =>"):][:520]
    assert "maybeShowAdamIntro({ chain: true, then: startHints })" in chain
    assert "maybeShowStreakOnboarding({ chain: true, then: afterStreak })" in chain
    assert "afterStreak();" in chain, "без окна про серию цепочка идёт дальше сама"
    close = APP_JS[APP_JS.index("function closeStreakOnboarding()"):][:900]
    assert "else setTimeout(() => maybeShowAdamIntro(), 450);" in close
    assert "maybeShowAdamIntro();" in APP_JS[APP_JS.index("maybeShowStartQuiz();\r\n" if "\r\n" in APP_JS else "maybeShowStartQuiz();\n"):][:200]
    gate = APP_JS[APP_JS.index("function maybeShowAdamIntro("):][:520]
    assert "introChainPending() || streakOnboardingDue() || celebrationOverlayOpen()" in gate, "само окно не лезет поверх других"
    assert "if (!state?.adam_intro?.show || adamIntro || adamIntroHandled) return false;" in gate, "после показа окно в этой сессии не открывается снова"
    assert "adamIntroHandled = true;" in APP_JS[APP_JS.index("function closeAdamIntro("):][:900]
    assert "initAdamIntro();" in APP_JS and "renderAdamIntroSettings();" in APP_JS
    assert '"adamIntroOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:700]


def test_the_flow_saves_on_the_result_changes_the_method_adds_habits_and_opens_the_chat():
    flow = APP_JS[APP_JS.index("async function showAdamIntroResult()"):][:1500]
    assert 's.sim ? "/api/adam-profile/recommend" : "/api/adam-profile"' in flow
    assert "ADAM_INTRO_THINK_MS" in flow and "reduceMotion" in flow
    method = APP_JS[APP_JS.index("async function onAdamIntroMethod("):][:700]
    assert "if (s.sim) return;" in method and 'api("/api/adam-profile"' in method
    habit = APP_JS[APP_JS.index("async function onAdamIntroHabit("):][:1300]
    assert habit.index("if (s.sim)") < habit.index('api("/api/habits"'), "в симуляции привычки не добавляются"
    assert "state.habits = [result.habit, ...(state.habits || [])];" in habit
    close = APP_JS[APP_JS.index("function closeAdamIntro("):][:2200]
    assert '"/api/adam-profile/skip"' in close and "!s.retake" in close, "«Отмена» в Настройках профиль не трогает"
    assert "/coach?intro=plans&t=${encodeURIComponent(talk)}" in close
    assert close.index("if (s.sim)") < close.index("/coach?intro=plans"), "в симуляции чат не открывается"


def test_simulations_cover_the_whole_test_and_the_result():
    from db.sim_lab import sim_scenes
    keys = [s["key"] for s in sim_scenes()]
    assert "adam_intro" in keys and "adam_intro_result" in keys
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):APP_JS.index("async function initSimMode()")]
    assert "adam_intro: () => openAdamIntro({ sim: true })," in block
    assert "adam_intro_result: () => openAdamIntro({ sim: true, jump: true, demo:" in block


def test_window_styles_are_light_for_weak_webviews():
    css = CSS[CSS.index("Знакомство с Адамом (тест после онбординга"):CSS.index("Заморозка другу (app.js::openFreezeHelp)")]
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", css) and "backdrop-filter" not in css
    assert "infinite" not in css and "@keyframes" not in css
    assert ".adam-intro-overlay[hidden]{display:none !important}" in css
    z = int(re.search(r"\.adam-intro-overlay\{[^}]*z-index:(\d+)", css).group(1))
    assert z >= 10150, "не ниже стартового квиза"
    assert "overflow-y:auto" in css, "длинный итог прокручивается внутри окна"


# ---------------------------------------------------------------------------------------------------------------- учёт

def test_events_are_labelled_and_belong_to_one_feature():
    from db.tracking import EVENT_LABELS
    from webapp import feature_map as fm
    names = ("adam_intro_shown", "adam_intro_step", "adam_intro_done", "adam_intro_skip", "adam_intro_habit", "adam_intro_chat")
    assert all(name in EVENT_LABELS for name in names)
    owners = {f.id for f in fm.FEATURES for name in names if name in f.events}
    assert owners == {"adam_intro"}
    feature = next(f for f in fm.FEATURES if f.id == "adam_intro")
    assert "adamIntro" in feature.screens and len(feature.api) == 4
    for name in re.findall(r'trk\("(adam_intro_\w+)"', APP_JS):
        assert name in names, name


def test_the_funnel_counts_the_introduction_step_by_step_and_the_chosen_methods():
    from datetime import datetime, timezone
    from db import tracking
    source = "adamintro"
    ids = itertools.count(900_600_000)

    def person():
        person_id = next(ids)
        conn = connect()
        conn.execute("INSERT INTO users(telegram_id, username, first_name, created_at, access_status, source_code) VALUES (?,?,?,?,?,?)",
                     (person_id, f"u{person_id}", "Аня", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "approved", source))
        conn.commit()
        conn.close()
        return person_id

    def send(person_id, steps):
        session = {"sid": f"sid-{person_id}", "started": 1_000_000, "active": 5, "actions": 1, "screen": "home", "hidden": False, "platform": "ios", "tz": 180, "last": []}
        tracking.ingest(person_id, session, [{"n": name, "s": step, "t": 1_000_000} for name, step in steps], 1_000_000)

    a, b, c = person(), person(), person()
    send(a, [("adam_intro_shown", None)] + [("adam_intro_step", key) for key in ap.QUESTION_KEYS] + [("adam_intro_habit", None), ("adam_intro_chat", "month")])
    send(b, [("adam_intro_shown", None), ("adam_intro_step", "focus"), ("adam_intro_step", "struggle")])
    send(c, [("adam_intro_shown", None)])
    tracking.track_server(a, "adam_intro_done", step="pomodoro")
    tracking.track_server(a, "adam_intro_done", step="deep90")        # передумал: считаем последний выбор
    tracking.track_server(b, "adam_intro_skip")
    funnel = tracking.funnel(30, source=source)
    rows = {r["key"]: r for g in funnel["onboarding"] if g["id"] == "adam_intro" for r in g["rows"]}
    assert rows["adam_intro_shown"]["users"] == 3 and rows["adam_intro_shown"]["left"] == 1
    assert rows["adam_q:focus"]["users"] == 2 and rows["adam_q:struggle"]["users"] == 2 and rows["adam_q:talk"]["users"] == 1
    assert rows["adam_q:focus"]["label"] == "Вопрос 1: что сейчас важнее всего" and not rows["adam_q:focus"]["unnamed"]
    assert rows["adam_intro_done"]["users"] == 1, "двое сохранений одного человека — один человек"
    assert rows["adam_intro_chat"]["users"] == 1 and rows["adam_intro_chat"]["left"] is None
    assert rows["adam_intro_habit"]["users"] == 1 and rows["adam_intro_skip"]["users"] == 1
    methods = {r["key"]: r["users"] for g in funnel["onboarding"] if g["id"] == "adam_method" for r in g["rows"]}
    assert methods == {"adam_method:pomodoro": 0, "adam_method:focus45": 0, "adam_method:deep90": 1, "adam_method:priorities": 0}


def test_every_question_has_a_funnel_label():
    from db.tracking import ADAM_INTRO_STEP_LABELS
    assert set(ap.QUESTION_KEYS) <= set(ADAM_INTRO_STEP_LABELS), "новому вопросу нужна подпись в db/tracking.py::ADAM_INTRO_STEP_LABELS"
