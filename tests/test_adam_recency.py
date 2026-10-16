"""
Адам и «только что сделанное» (15.10). Жалоба владельца: человек отметил первую привычку и хотел отметить вторую, а Адам сразу написал
«Сделана только одна привычка — темп ниже твоего обычного». После отметки 30 минут идёт окно x2, вторую привычку могли закрыть через 20 минут.

Правило одно для ВСЕХ сообщений Адама: пока человек в движении (меньше 30 минут после отметки), о низком темпе речи нет, торопить нельзя,
а окно x2 упоминается как возможность. Оценка темпа — только через 45 минут и только когда есть на что опереться. Здесь же — эксперимент
«Адам пишет сам после действий» (через 35–60 минут после отметки), кнопка «Написать мне 📩» и отложенная подсказка в приложении.
"""
import itertools
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import adam_proactive
from db import adam_checkin as ac
from db import add_habit, complete_habit, get_habits
from db.ai import add_ai_message, get_ai_history, update_user_profile
from db.ai_nudge import NUDGE_AFTER_MARK_MIN, begin_ai_greeting, get_ai_nudge_state
from db.core import connect
from db.feature_flags import delete_feature_flag, set_feature_flag
from tests.test_adam_checkin import KNOWN_PACE, FakeBot, _events, _generate_returns, _habits, _local, _log, _state, _user
from tests.test_adam_texts import FORMAL, GENDERED, _problems

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
TZ = ZoneInfo("Europe/Moscow")

_uid_counter = itertools.count(842_000_000, 100)
PRESSURE = ("темп", "медлен", "отста", "обычно", "ниже")           # слова оценки темпа: в сообщениях «в движении» их нет


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture
def no_followup_flag():
    delete_feature_flag(ac.FOLLOWUP_FLAG)
    yield
    delete_feature_flag(ac.FOLLOWUP_FLAG)


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _mark_ago(uid_, minutes):
    """Сегодняшние отметки человека (за последние 3 часа; история прошлых дней не трогается) считаются сделанными `minutes` минут назад."""
    _sql("UPDATE habit_completion_events SET completed_at=datetime('now', ?) WHERE user_id=? AND completed_at>=datetime('now','-3 hours')",
         (f"-{minutes} minutes", uid_))


def _person(uid_, done=1, ago=70, titles=("Зарядка", "Чтение", "Английский"), history=True):
    """Человек, которому Адам уместен: не новый, активный, привычки, история отметок и `done` привычек, отмеченных `ago` минут назад."""
    _user(uid_)
    rows = _habits(uid_, list(titles), done=0)
    if history:
        _events(uid_, [r["id"] for r in rows], hours=(9, 10, 11))
    for row in rows[:done]:
        complete_habit(row["id"])
    _mark_ago(uid_, ago)
    return rows


def _with(state, **fields):
    return dict(state, **fields)


# ---------------------------------------------------------------------------------------------------------------- первое сообщение в чате

def test_the_owners_case_right_after_the_first_mark_adam_supports_and_never_judges_the_pace():
    state = ac.lab_scenarios()["just_done"][2]                      # 14:29, 1 из 4 привычек, отметка минуту назад, окно x2 открыто
    assert ac.pick_scenario(state, "app") == ("just_done", "отметил привычку 1 мин назад: человек в движении")
    assert ac.pick_scenario(_with(state, pace=KNOWN_PACE), "app")[0] == "just_done", "даже если по обычному темпу человек «позади»"
    prompt = ac.build_checkin_prompt(state, "just_done", "no_greeting")
    assert "Последнюю привычку человек отметил 1 мин назад («Зарядка»)" in prompt
    assert "НЕ говори о низком темпе" in prompt and "Окно x2 Adam Coin открыто ещё около 29 мин" in prompt
    assert "Сценарий: " + ac.SCENARIO_GUIDE["just_done"] in prompt
    for day in range(1, 25):
        text = ac.fallback_text(state, "just_done", 7, f"2026-10-{day:02d}")
        assert ac.is_acceptable(text, "just_done") and "x2" in text and "{" not in text, text
        assert not any(word in text.lower() for word in PRESSURE), text
        assert not _problems(text) and not GENDERED.search(f"Анна, {text}") and not FORMAL.search(text), text


def test_without_an_open_x2_window_the_text_does_not_promise_double_coins():
    state = _with(ac.lab_scenarios()["just_done"][2], last_mark_min=3, bonus_min_left=None)
    prompt = ac.build_checkin_prompt(state, "just_done", "no_greeting")
    assert "Окно x2 Adam Coin открыто" not in prompt
    for day in range(1, 25):
        assert "x2" not in ac.fallback_text(state, "just_done", 7, f"2026-10-{day:02d}")


def test_in_motion_means_no_pace_verdict_at_any_hour_for_any_history():
    """Матрица: меньше 30 минут после отметки × сколько сделано × время суток × известен ли обычный темп."""
    for since in (0, 1, 10, 29):
        for done, total in ((1, 3), (1, 4), (2, 5), (1, 6), (3, 8)):
            for hour in (12, 13, 14, 15, 16, 17, 18, 19, 21):
                for pace in (None, KNOWN_PACE):
                    state = _with(_state(done, total, hour=hour, pace=pace), last_mark_min=since)
                    scenario = ac.pick_scenario(state, "app")[0]
                    assert scenario == "just_done", (since, done, total, hour, pace, scenario)
    for since in (0, 29):                                       # один оставшийся пункт и «всё сделано» — как раньше: это поддержка
        assert ac.pick_scenario(_with(_state(3, 4, hour=14), last_mark_min=since), "app")[0] == "one_left"
        assert ac.pick_scenario(_with(_state(4, 4, hour=14), last_mark_min=since), "app")[0] == "all_done"
    assert ac.pick_scenario(_with(_state(0, 4, hour=14), last_mark_min=0), "app")[0] == "zero_done", "отметок нет — значит нечего «только что»"


def test_between_30_and_45_minutes_it_is_still_a_calm_summary_without_judging():
    for since in (30, 38, 44):
        for hour in (14, 16, 19, 21):
            state = _with(_state(1, 4, hour=hour, pace=KNOWN_PACE), last_mark_min=since)
            assert ac.pick_scenario(state, "app")[0] == "steady", (since, hour)
    steady = _with(_state(2, 5, hour=15), last_mark_min=35)
    for day in range(1, 25):
        text = ac.fallback_text(steady, "steady", 4, f"2026-10-{day:02d}")
        assert ac.is_acceptable(text, "steady") and not any(word in text.lower() for word in PRESSURE), text


def test_the_pace_is_judged_only_after_a_while_and_only_with_something_to_judge_by():
    old = lambda **kw: _with(_state(1, 4, **kw), last_mark_min=60)
    assert ac.pick_scenario(old(hour=15, pace=KNOWN_PACE), "app")[0] == "behind", "свой обычный темп известен и человек позади"
    assert ac.pick_scenario(old(hour=14, pace=KNOWN_PACE), "app")[0] == "behind"
    assert ac.pick_scenario(old(hour=14), "app")[0] == "steady", "до 15:00 без истории оценивать темп нечем"
    assert ac.pick_scenario(old(hour=15), "app")[0] == "behind", "без истории — только вечереющий день и меньше трети"
    assert ac.pick_scenario(old(hour=20), "app")[0] == "evening_left"
    fast = {"days": 8, "avg_by_now": 0.8, "avg_total": 3.0}
    assert ac.pick_scenario(_with(_state(1, 4, hour=15, pace=fast), last_mark_min=60), "app")[0] == "steady", "человек идёт как обычно"
    assert ac.pick_scenario(_with(_state(1, 4, hour=15, pace=KNOWN_PACE), last_mark_min=None), "app")[0] == "behind"


def test_a_pace_claim_in_the_fallback_needs_a_known_pace():
    unknown = _with(_state(1, 6, hour=16), last_mark_min=None)
    for day in range(1, 25):
        assert "обычно" not in ac.fallback_text(unknown, "behind", 5, f"2026-10-{day:02d}").lower()
    known = _with(_state(1, 6, hour=16, pace=KNOWN_PACE), last_mark_min=None)
    assert any("обычно" in ac.fallback_text(known, "behind", 5, f"2026-10-{day:02d}").lower() for day in range(1, 25))


# ---------------------------------------------------------------------------------------------------------------- данные о времени отметки

def test_the_state_knows_when_the_last_habit_was_marked_and_whether_the_x2_window_is_open(uid):
    _user(uid)
    rows = _habits(uid, ["Зарядка", "Чтение", "Английский"], done=0)
    assert ac.build_checkin_state(uid, _local(14, 29))["last_mark_min"] is None
    complete_habit(rows[0]["id"])
    state = ac.build_checkin_state(uid, _local(14, 29))
    assert state["last_mark_min"] == 0 and state["last_mark_title"] == "Зарядка" and 28 <= state["bonus_min_left"] <= 30
    assert ac.pick_scenario(state, "app")[0] == "just_done", "первая отметка у человека без истории: поддержка, а не «темп ниже обычного»"
    _mark_ago(uid, 50)
    state = ac.build_checkin_state(uid, _local(14, 29))
    assert state["last_mark_min"] in (50, 51)
    assert ac.recency_lines(state)[0].startswith("Последнюю привычку человек отметил 5")
    assert not any("НЕ говори" in line for line in ac.recency_lines(state)), "через 50 минут запрета на оценку темпа уже нет"


def test_the_last_mark_does_not_count_when_nothing_is_marked_now(uid):
    """День сменился (все привычки снова открыты) — вчерашняя отметка не делает человека «только что отметившим»."""
    _user(uid)
    rows = _habits(uid, ["Зарядка", "Чтение"], done=0)
    complete_habit(rows[0]["id"])
    _sql("UPDATE habits SET completed=0 WHERE user_id=?", (uid,))
    state = ac.build_checkin_state(uid, _local(14, 29))
    assert state["habits_done"] == 0 and state["last_mark_min"] is None and state["bonus_min_left"] is None


def test_the_recency_rules_reach_the_prompt_the_chat_snapshot_and_both_system_prompts():
    from multi_agent import ADAM_CHECKIN_SYSTEM, BASE_PERSONA
    from webapp.services.chat_brain import situation_lines
    state = ac.lab_scenarios()["just_done"][2]
    lines = "\n".join(situation_lines(state))
    assert "Последнюю привычку человек отметил 1 мин назад" in lines and "НЕ говори о низком темпе" in lines and "Окно x2 Adam Coin" in lines
    stale = ac.lab_scenarios()["steady"][2]
    assert "НЕ говори о низком темпе" not in "\n".join(situation_lines(stale))
    assert "ТОЛЬКО ЧТО отметил привычку" in BASE_PERSONA and "окно x2" in BASE_PERSONA
    assert "только что отметил привычку" in ADAM_CHECKIN_SYSTEM and "НЕ говори о низком темпе" in ADAM_CHECKIN_SYSTEM


async def test_the_owners_flow_end_to_end_mark_a_habit_open_the_chat_and_get_a_supporting_first_message(client, uid, monkeypatch):
    """Ровно то, что случилось у владельца: отметил первую привычку, сразу открыл чат — Адам здоровается. Раньше: «темп ниже твоего обычного»."""
    from tests.conftest import sign_init_data
    _user(uid)
    _sql("UPDATE users SET ai_intro_shown=1 WHERE telegram_id=?", (uid,))
    add_ai_message(uid, "user", "привет")
    _sql("UPDATE ai_messages SET created_at=datetime('now','-1 day') WHERE user_id=?", (uid,))
    rows = _habits(uid, ["Зарядка", "Чтение", "Английский", "Холодный душ"], done=0)
    _events(uid, [r["id"] for r in rows], hours=(9, 10, 11, 12))                     # у человека есть свой обычный темп
    complete_habit(rows[0]["id"])                                                    # первая привычка — только что
    prompts = []
    _generate_returns(monkeypatch, "Первая привычка уже есть — хороший старт. Окно x2 ещё открыто: следующая принесёт вдвое больше монет. Что берёшь дальше?", prompts)
    real_build = ac.build_checkin_state                                              # время у человека — 14:29, как на скриншоте владельца
    monkeypatch.setattr(ac, "build_checkin_state", lambda user_id, now_local=None, with_pace=True: real_build(user_id, _local(14, 29), with_pace))
    response = await client.post("/api/ai/greet", json={"init_data": sign_init_data(uid)})
    data = await response.json()
    assert data["greeting"] and data["greeting"]["message"].startswith("Первая привычка уже есть")
    prompt = prompts[0]
    assert "Сценарий: " + ac.SCENARIO_GUIDE["just_done"] in prompt, "выбран сценарий поддержки, а не «behind»"
    assert ac.SCENARIO_GUIDE["behind"] not in prompt and "Последнюю привычку человек отметил 0 мин назад" in prompt
    assert "НЕ говори о низком темпе" in prompt and "Окно x2 Adam Coin открыто ещё около" in prompt
    row = [r for r in _log(uid) if r["slot"] == "app"][0]
    assert (row["status"], row["scenario"]) == ("sent", "just_done")


# ---------------------------------------------------------------------------------------------------------------- плановые пуши и сверка

def test_scheduled_push_candidates_hold_back_pressure_while_the_person_is_in_motion():
    state = _with(_state(1, 4, hour=15, pace=KNOWN_PACE), last_mark_min=10)
    assert [c["scenario"] for c in ac.list_candidates(state, "day")] == []
    later = _with(state, last_mark_min=60)
    assert "behind" in [c["scenario"] for c in ac.list_candidates(later, "day")]
    done = _with(_state(4, 4, hour=20), last_mark_min=5)
    assert [c["scenario"] for c in ac.list_candidates(done, "evening", chat_day=True)] == ["all_done"], "радоваться можно сразу"


async def test_the_scheduled_push_waits_after_a_mark_and_does_not_burn_the_window(uid, monkeypatch):
    set_feature_flag(ac.FLAG_KEY, True, 100)
    try:
        _generate_returns(monkeypatch, "Один маленький шаг — «Чтение».")
        _person(uid, done=1, ago=3)
        bot = FakeBot()
        from tests.test_adam_checkin import _due
        now = _due(uid, "day")
        outcome = await adam_proactive.process_user(bot, uid, "s", now)
        assert outcome == "wait: недавно отметил привычку" and not bot.sent and _log(uid) == [], "окно не потрачено"
        _mark_ago(uid, 5)
        assert (await adam_proactive.process_user(bot, uid, "s", now)).startswith("wait")
        _mark_ago(uid, 60)
        assert (await adam_proactive.process_user(bot, uid, "s", now)).startswith("sent"), "через час тот же день и то же окно — пишет"
    finally:
        set_feature_flag(ac.FLAG_KEY, False, 100)


async def test_the_day_checkpoint_skips_someone_who_has_just_marked_a_habit(monkeypatch, uid):
    import coach
    from tests.test_habit_checkpoints import FakeBot as PlainBot, _freeze, _patch_user
    _user(uid)
    add_habit(uid, "Пить воду")
    add_habit(uid, "Спорт")
    complete_habit(get_habits(uid)[0]["id"])
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 12, 0)
    bot = PlainBot()
    await coach.run_habit_checkpoint_12(bot)
    assert bot.sent == [], "отметил минуту назад — сверка курса не нужна"
    _mark_ago(uid, 40)
    await coach.run_habit_checkpoint_12(bot)
    assert len(bot.sent) == 1, "прошло 40 минут — обычная точка дня"


# ---------------------------------------------------------------------------------------------------------------- подсказка в приложении и первое сообщение

def test_the_app_hint_waits_after_a_mark_and_the_server_says_how_long(uid):
    _person(uid, done=1, ago=0)
    state = get_ai_nudge_state(uid)
    assert NUDGE_AFTER_MARK_MIN == 35 and state["wait_min"] in (34, 35)
    _mark_ago(uid, 20)
    assert get_ai_nudge_state(uid)["wait_min"] == 15
    _mark_ago(uid, 40)
    assert get_ai_nudge_state(uid)["wait_min"] == 0
    other = uid + 1
    _user(other)
    assert get_ai_nudge_state(other)["wait_min"] == 0, "отметок нет — ждать нечего"


def test_the_first_chat_greeting_is_not_a_second_message_right_after_adams_own_push(uid):
    _user(uid)
    _sql("UPDATE users SET ai_intro_shown=1 WHERE telegram_id=?", (uid,))
    add_ai_message(uid, "user", "привет")
    _sql("UPDATE ai_messages SET created_at=datetime('now','-1 day') WHERE user_id=?", (uid,))
    today = datetime.now(ZoneInfo(_tz(uid))).date().isoformat()
    _sql("INSERT INTO adam_checkins(user_id, day, slot, status, scenario) VALUES (?,?, 'fu_mark', 'sent', 'after_mark')", (uid, today))
    assert begin_ai_greeting(uid) is None, "Адам только что написал сам — это сообщение уже в чате"
    _sql("UPDATE adam_checkins SET created_at=datetime('now','-200 minutes') WHERE user_id=?", (uid,))
    assert begin_ai_greeting(uid)["today"] == today


def _tz(uid_):
    from db.streak import get_timezone
    return get_timezone(uid_)


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_app_hint_does_not_pop_up_while_the_person_keeps_marking(tmp_path):
    start = APP.index("let adamNudgeActive = false;")
    end = APP.index("function goToAdamChat() {")

    def run(state, body):
        script = tmp_path / "nudge.js"
        script.write_text(r"""
const src = %s;
let now = 0, nextId = 1; const timers = new Map(); const shown = [];
function advance(ms) {
  const target = now + ms;
  for (;;) {
    let best = null, bestId = null;
    for (const [id, t] of timers) if (t.at <= target && (!best || t.at < best.at)) { best = t; bestId = id; }
    if (!best) break;
    now = best.at;
    if (best.every) best.at += best.every; else timers.delete(bestId);
    best.f();
  }
  now = target;
}
const holder = {
  state: JSON.parse(%s), hour: 20, keyboardOpen: false, isTextField: () => false, celebrationOverlayOpen: () => false,
  document: { getElementById: () => null, querySelector: () => null, hidden: false },
  showAdamNudge: () => { shown.push(Math.round(now / 1000)); },
  setTimeout: (f, ms) => { const id = nextId++; timers.set(id, { f, at: now + ms, every: 0 }); return id; },
  clearTimeout: (id) => timers.delete(id),
  setInterval: (f, ms) => { const id = nextId++; timers.set(id, { f, at: now + ms, every: ms }); return id; },
  clearInterval: (id) => timers.delete(id),
  Date: Object.assign(function () { return { getHours: () => holder.hour }; }, { now: () => now }),
};
const api = new Function('holder', 'with (holder) {' + src + ' return { scheduleAdamNudge, maybeScheduleEveningAdamNudge, AFTER: ADAM_NUDGE_AFTER_ACTION_MS }; }')(holder);
const MIN = 60 * 1000, out = {};
%s
console.log(JSON.stringify(out));
""" % (json.dumps(APP[start:end]), json.dumps(json.dumps(state)), body), encoding="utf-8")
        result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    eligible = {"ai_nudge": {"eligible": True, "hint_shown_today": False, "wait_min": 0}}
    out = run(eligible, """
api.scheduleAdamNudge({ delayMs: api.AFTER }); advance(34 * MIN); out.before = shown.length;
advance(2 * MIN); out.after = shown.length;""")
    assert out == {"before": 0, "after": 1}, "через 35 минут после отметки — и только тогда"

    out = run(eligible, """
api.scheduleAdamNudge({ delayMs: api.AFTER }); advance(20 * MIN);
api.scheduleAdamNudge({ delayMs: api.AFTER }); advance(20 * MIN); out.second_mark_resets = shown.length;
advance(16 * MIN); out.then = shown.length;""")
    assert out == {"second_mark_resets": 0, "then": 1}, "новая отметка сбрасывает отсчёт: подсказка не лезет посреди отметок"

    out = run(eligible, "api.scheduleAdamNudge(); advance(5000); out.now = shown.length;")
    assert out == {"now": 1}, "без действия (обычный вход) подсказка, как раньше, ждёт только тишины на экране"

    for state in ({"ai_nudge": {"eligible": False, "hint_shown_today": False}}, {"ai_nudge": {"eligible": True, "hint_shown_today": True}}):
        assert run(state, "api.scheduleAdamNudge({ delayMs: api.AFTER }); advance(60 * MIN); out.n = shown.length;") == {"n": 0}

    evening = {"ai_nudge": {"eligible": True, "hint_shown_today": False, "wait_min": 12}}
    out = run(evening, "api.maybeScheduleEveningAdamNudge(); advance(11 * MIN); out.early = shown.length; advance(2 * MIN); out.late = shown.length;")
    assert out == {"early": 0, "late": 1}, "зашёл вечером через 23 минуты после отметки — подсказка ждёт оставшиеся 12"
    out = run(evening, "holder.hour = 10; api.maybeScheduleEveningAdamNudge(); advance(60 * MIN); out.n = shown.length;")
    assert out == {"n": 0}, "утром при входе подсказки нет"


# ---------------------------------------------------------------------------------------------------------------- повтор после действий (эксперимент)

def test_followup_delay_is_stable_per_event_and_inside_the_range():
    for user in range(200):
        value = ac.followup_delay(user, "2026-10-15", "mark1")
        assert ac.FOLLOWUP_DELAY[0] <= value <= ac.FOLLOWUP_DELAY[1] and value == ac.followup_delay(user, "2026-10-15", "mark1")
        assert ac.FOLLOWUP_CHAT_DELAY[0] <= ac.followup_delay(user, "2026-10-15", "chat", chat=True) <= ac.FOLLOWUP_CHAT_DELAY[1]
    assert len({ac.followup_delay(user, "2026-10-15", "mark1") for user in range(200)}) > 15, "у людей разное число минут, а не «ровно через час»"


def test_followup_candidates_follow_the_action_and_its_age():
    base = _state(1, 4, hour=15)
    assert ac.followup_candidates(_with(base, user_id=1, last_mark_min=10), "2026-10-15") == [], "рано"
    assert ac.followup_candidates(_with(base, user_id=1, last_mark_min=None), "2026-10-15") == [], "действия не было"
    assert ac.followup_candidates(_with(base, user_id=1, last_mark_min=ac.FOLLOWUP_MAX_AGE_MIN + 1), "2026-10-15") == [], "слишком поздно, не по теме"
    found = ac.followup_candidates(_with(base, user_id=1, last_mark_min=70), "2026-10-15")
    assert [c["scenario"] for c in found] == ["after_mark"] and found[0]["slot"] == "fu_mark"
    done = ac.followup_candidates(_with(_state(4, 4, hour=19), user_id=1, last_mark_min=70), "2026-10-15")
    assert [c["scenario"] for c in done] == ["after_done"] and done[0]["slot"] == "fu_done"
    assert ac.followup_candidates(_with(_state(0, 0, hour=19), user_id=1, last_mark_min=70), "2026-10-15") == []


def test_a_habit_with_its_own_time_still_ahead_is_not_nagged_about():
    state = _with(_state(1, 2, hour=15), user_id=1, last_mark_min=70)
    state["habits"][1]["planned"] = "23:00"
    assert ac.actionable_left(state) == []
    assert ac.followup_candidates(state, "2026-10-15") == []
    state["habits"][1]["planned"] = "12:00"
    assert ac.actionable_left(state) == ["Привычка 1"] and ac.followup_candidates(state, "2026-10-15")


def test_chat_followup_needs_a_recent_conversation_with_a_topic_and_no_marks_after_it():
    state = _with(_state(0, 3, hour=16), user_id=1, mood_hint="устал, мало сил", minutes_since_chat=95, last_mark_min=None)
    assert [c["scenario"] for c in ac.followup_candidates(state, "2026-10-15")] == ["chat_followup"]
    assert ac.followup_candidates(_with(state, minutes_since_chat=20), "2026-10-15") == [], "только что говорили"
    assert ac.followup_candidates(_with(state, mood_hint=""), "2026-10-15") == [], "темы нет — вернуться не к чему"
    assert ac.followup_candidates(_with(state, minutes_since_chat=ac.FOLLOWUP_MAX_AGE_MIN + 1), "2026-10-15") == []
    marked = _with(state, habits_done=1, last_mark_min=30)
    assert "chat_followup" not in [c["scenario"] for c in ac.followup_candidates(marked, "2026-10-15")], "после разговора уже сделал шаг"


def test_after_visit_needs_an_open_without_marks_and_nothing_after_it():
    base = _with(_state(0, 3, hour=15), user_id=1, last_open_min=70, last_mark_min=None)
    found = ac.followup_candidates(base, "2026-10-15")
    assert [c["scenario"] for c in found] == ["after_visit"] and found[0]["slot"] == "fu_visit"
    assert ac.followup_candidates(_with(base, last_open_min=10), "2026-10-15") == [], "рано"
    assert ac.followup_candidates(_with(base, last_open_min=ac.FOLLOWUP_MAX_AGE_MIN + 1), "2026-10-15") == [], "не по теме"
    assert ac.followup_candidates(_with(base, last_open_min=None), "2026-10-15") == []
    assert ac.followup_candidates(_with(base, hour=10), "2026-10-15") == [], "до 11:00 человек планирует день"
    assert ac.followup_candidates(_with(base, minutes_since_chat=30), "2026-10-15") == [], "после захода они уже говорили"
    marked = _with(_state(1, 3, hour=15), user_id=1, last_open_min=70, last_mark_min=65)
    assert [c["scenario"] for c in ac.followup_candidates(marked, "2026-10-15")] == ["after_mark"], "отметил — это уже другой повод"
    for day in range(1, 25):
        text = ac.fallback_text(_with(base, first_name="Анна"), "after_visit", 3, f"2026-10-{day:02d}")
        assert ac.is_acceptable(text, "after_visit") and not _problems(text) and "Привычка 0" in text, text
        assert not any(word in text.lower() for word in ("зашёл", "заходил", "ничего не сделал")), text


async def test_adam_comes_back_an_hour_after_a_visit_without_marks(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    _generate_returns(monkeypatch, "Самое сложное — начать. «Зарядка» займёт пару минут: возьмёшь её сейчас?")
    _person(uid, done=0, ago=0)
    _sql("INSERT INTO app_opens(user_id, at) VALUES (?, datetime('now','-70 minutes'))", (uid,))
    bot = FakeBot()
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "sent: after_visit/llm"
    assert _log(uid)[0]["slot"] == "fu_visit" and bot.sent[0][2].inline_keyboard[0][0].text == "Написать мне 📩"


def test_followups_are_on_for_admins_only_until_the_flag_says_otherwise(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    assert ac.followups_enabled_for(uid) is True and ac.followups_enabled_for(uid + 1) is False
    set_feature_flag(ac.FOLLOWUP_FLAG, False, 100)
    assert ac.followups_enabled_for(uid) is False, "явное «выключено» выключает эксперимент и админам"
    set_feature_flag(ac.FOLLOWUP_FLAG, True, 100)
    assert ac.followups_enabled_for(uid) is True and ac.followups_enabled_for(uid + 1) is True
    set_feature_flag(ac.FOLLOWUP_FLAG, True, 0)
    assert ac.followups_enabled_for(uid) is True and ac.followups_enabled_for(uid + 1) is False


async def test_adam_comes_back_after_a_mark_with_one_message_and_the_first_person_button(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    text = "«Зарядка» уже в копилке — хороший шаг. Что берёшь следом — «Чтение»?"
    prompts = []
    _generate_returns(monkeypatch, text, prompts)
    _person(uid, done=1, ago=70)
    bot, now = FakeBot(), _local(15, 30)
    assert (await adam_proactive.process_followup(bot, uid, "s", now)) == "sent: after_mark/llm"
    chat_id, sent_text, markup = bot.sent[0]
    assert chat_id == uid and sent_text == text
    button = markup.inline_keyboard[0][0]
    assert button.text == "Написать мне 📩" and button.web_app.url.endswith("/coach"), "кнопка от первого лица, открывает чат"
    assert "Сценарий: " + ac.SCENARIO_GUIDE["after_mark"] in prompts[0] and "Последнюю привычку человек отметил 70 мин назад" in prompts[0]
    row = _log(uid)[0]
    assert (row["slot"], row["status"], row["scenario"], row["source"]) == ("fu_mark", "sent", "after_mark", "llm")
    assert get_ai_history(uid)[-1]["role"] == "assistant" and get_ai_history(uid)[-1]["message"] == text, "в переписке — ответ человека продолжит разговор"
    conn = connect()
    kinds = [r["category"] for r in conn.execute("SELECT category FROM notification_log WHERE user_id=?", (uid,)).fetchall()]
    conn.close()
    assert kinds == ["adam_checkin_fu_mark"] and not adam_proactive.other_push_recently(uid), "собственные сообщения Адама не считаются «другим пушем»"
    assert (await adam_proactive.process_followup(bot, uid, "s", now)).startswith("wait"), "второе подряд — нет"
    assert len(bot.sent) == 1


async def test_followups_stay_quiet_outside_their_window_for_ordinary_people_and_when_the_person_is_busy(uid, monkeypatch, no_followup_flag):
    import config
    _generate_returns(monkeypatch, "«Зарядка» уже в копилке — хороший шаг. Что берёшь следом — «Чтение»?")
    _person(uid, done=1, ago=70)
    bot = FakeBot()
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "disabled", "обычному человеку без флага — нет"
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    for hour, minute in ((9, 0), (10, 29), (21, 30), (23, 0)):
        assert await adam_proactive.process_followup(bot, uid, "s", _local(hour, minute)) == "not_due", (hour, minute)

    _sql("UPDATE users SET last_seen=? WHERE telegram_id=?", (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), uid))
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "wait: человек прямо сейчас в приложении"
    _sql("UPDATE users SET last_seen=NULL WHERE telegram_id=?", (uid,))
    add_ai_message(uid, "user", "привет")
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "wait: человек только что говорил с Адамом"
    _sql("DELETE FROM ai_messages WHERE user_id=?", (uid,))
    _sql("INSERT INTO notification_log(user_id, category, title) VALUES (?, 'habit_checkpoint_12', 'x')", (uid,))
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "wait: недавно приходил другой пуш"
    assert bot.sent == [] and _log(uid) == [], "ожидание ничего не записывает: повод никуда не уходит"


async def test_at_most_two_followups_a_day_and_each_kind_once(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    _generate_returns(monkeypatch, "«Зарядка» уже в копилке — хороший шаг. Что берёшь следом — «Чтение»?")
    _person(uid, done=1, ago=70)
    day = _local(15, 30).date().isoformat()
    _sql("INSERT INTO adam_checkins(user_id, day, slot, status, scenario, created_at) VALUES (?,?, 'fu_done', 'sent', 'after_done', datetime('now','-5 hours'))", (uid, day))
    _sql("INSERT INTO adam_checkins(user_id, day, slot, status, scenario, created_at) VALUES (?,?, 'fu_chat', 'sent', 'chat_followup', datetime('now','-4 hours'))", (uid, day))
    assert ac.followups_sent_today(uid, day) == 2
    assert await adam_proactive.process_followup(FakeBot(), uid, "s", _local(15, 30)) == "cap"
    _sql("DELETE FROM adam_checkins WHERE user_id=? AND slot='fu_chat'", (uid,))
    bot = FakeBot()
    assert (await adam_proactive.process_followup(bot, uid, "s", _local(15, 30))).startswith("sent")


async def test_everything_closed_an_hour_ago_gets_one_reflection_message(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    _generate_returns(monkeypatch, "День закрыт полностью — три из трёх. Что из сегодняшнего далось легче всего?")
    _person(uid, done=3, ago=75)
    bot = FakeBot()
    assert await adam_proactive.process_followup(bot, uid, "s", _local(19, 10)) == "sent: after_done/llm"
    assert _log(uid)[0]["slot"] == "fu_done"


async def test_followup_without_a_model_uses_a_clean_fallback_text(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})

    async def broken(prompt, style):
        raise RuntimeError("модель недоступна")
    monkeypatch.setattr(adam_proactive, "_generate", broken)
    _person(uid, done=1, ago=70)
    bot = FakeBot()
    assert await adam_proactive.process_followup(bot, uid, "s", _local(15, 30)) == "sent: after_mark/fallback"
    text = bot.sent[0][1]
    assert ac.is_acceptable(text, "after_mark") and not _problems(text) and "{" not in text
    assert "Зарядка" in text or "Чтение" in text, text


async def test_followup_after_a_chat_remembers_the_topic(uid, monkeypatch, no_followup_flag):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", {uid})
    prompts = []
    _generate_returns(monkeypatch, "Про усталость мы говорили недавно — как сейчас? Самое лёгкое — «Зарядка».", prompts)
    _person(uid, done=0, ago=0)
    add_ai_message(uid, "user", "я так устал")
    _sql("UPDATE ai_messages SET created_at=datetime('now','-95 minutes') WHERE user_id=?", (uid,))
    update_user_profile(uid, "", "устал, мало сил", (datetime.now(timezone.utc) + timedelta(hours=20)).replace(tzinfo=None).isoformat())
    bot = FakeBot()
    assert await adam_proactive.process_followup(bot, uid, "s", _local(16, 0)) == "sent: chat_followup/llm"
    assert "Недавняя тема из разговора" in prompts[0] and "устал, мало сил" in prompts[0] and "писал тебе в чате 95 мин назад" in prompts[0]


# ---------------------------------------------------------------------------------------------------------------- кнопка от первого лица и лаборатория

def test_every_message_adam_writes_himself_carries_the_first_person_button():
    from coach import _ai_welcome_keyboard
    from keyboards import ADAM_CHAT_BUTTON, adam_chat_markup
    assert ADAM_CHAT_BUTTON == "Написать мне 📩"
    for markup in (adam_chat_markup(), adam_proactive._keyboard(), _ai_welcome_keyboard()):
        button = markup.inline_keyboard[0][0]
        assert button.text == "Написать мне 📩" and button.web_app.url.endswith("/coach")
    morning = (ROOT / "morning_ping.py").read_text(encoding="utf-8")
    assert "reply_markup=adam_chat_markup()" in morning, "утреннее сообщение Адама — тоже с кнопкой"
    for text in ("Ответить Адаму", "Написать ADAM"):
        assert text not in (ROOT / "adam_proactive.py").read_text(encoding="utf-8") + (ROOT / "coach.py").read_text(encoding="utf-8")


def test_the_lab_has_demos_for_every_new_scenario_and_they_pick_what_they_are_named_after():
    labs = ac.lab_scenarios()
    expected = {"just_done": "just_done", "steady": "steady", "after_mark": "after_mark", "after_done": "after_done", "chat_followup": "chat_followup",
                "after_visit": "after_visit"}
    for key, scenario in expected.items():
        _title, slot, state = labs[key]
        assert ac.pick_scenario(state, slot, chat_day=True)[0] == scenario, key
    assert labs["just_done"][1] == "app" and labs["after_mark"][1] == "followup"
    assert set(ac.INTENT_LABELS) == set(ac.SCENARIOS) >= set(expected)


async def test_the_admin_lab_shows_the_experiment_settings_and_previews_followups(client, uid, monkeypatch, no_followup_flag):
    import webapp.routes_admin as ra
    from tests.conftest import sign_init_data
    _user(uid)
    monkeypatch.setattr(ra, "ADMIN_IDS", {uid})
    _generate_returns(monkeypatch, "«Зарядка» уже в копилке — хороший шаг. Что берёшь следом — «Чтение»?")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    overview = await (await client.get("/api/admin/lab/adam", headers=headers)).json()
    assert overview["followups"]["flag"] == "adam_followups" and overview["followups"]["delay_min"] == [35, 60]
    assert overview["followups"]["per_day"] == 2 and overview["followups"]["window"] == "10:30–21:30"
    assert overview["followups"]["status"] == "только админы (записи флага нет)"
    data = await (await client.post("/api/admin/lab/adam/preview", json={"scenario": "after_mark", "source": "llm"}, headers=headers)).json()
    assert data["scenario"] == "after_mark" and data["candidates"][0]["scenario"] == "after_mark"
