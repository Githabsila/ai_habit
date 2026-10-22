"""
П.14 владельца (21.10): «Квесты дня» связывают три возможности приложения — привычки, план дня и Адама. Каждый день три постоянных квеста:
«Выполни 2 привычки» (0/2 → 2/2), «Заполни план дня» (главная + второстепенная задача), «Поговори сегодня с Адамом» (хотя бы одно сообщение).
Награда выдаётся сама; окно показывает заполнение полосок и перелёт балла в месяц. Здесь: правила (db/quests.py), маршруты и чат, разметка окна,
скрипт и стили. Браузера в тестах нет — анимацию проверяем по тексту скрипта и в Node.
"""
import itertools
import json
import re
import shutil
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db import add_habit, add_user, complete_habit, delete_habit, get_habits, get_user
from db import quests as quests_db
from db.core import connect
from db.daily_plan import add_daily_task, get_daily_plan, set_daily_main_goal
from db.month_quests import get_month_quests
from db.streak import local_today
from tests.conftest import sign_init_data
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
_counter = itertools.count(860_300_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _quest(uid_, key):
    return next(q for q in quests_db.get_daily_quests(uid_) if q["key"] == key)


def _person(uid_, habits=0):
    add_user(uid_, f"p{uid_}", "Аня")
    for i in range(habits):
        add_habit(uid_, f"Привычка {i + 1}")
    return [h["id"] for h in get_habits(uid_)]


def _say(uid_, role="user", when=None):
    """Сообщение в переписке с Адамом; when — время (UTC) вместо «сейчас»."""
    conn = connect()
    if when is None:
        conn.execute("INSERT INTO ai_messages(user_id, role, message) VALUES (?, ?, 'привет')", (uid_, role))
    else:
        conn.execute("INSERT INTO ai_messages(user_id, role, message, created_at) VALUES (?, ?, 'привет', ?)", (uid_, role, when.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


def _xp(uid_):
    return get_user(uid_)["xp"]


# ---------------------------------------------------------------------------------------------------------------- набор

def test_the_day_always_has_the_same_three_quests_in_the_same_order(uid):
    _person(uid)
    for _ in range(3):
        quests = quests_db.get_daily_quests(uid)
        assert [q["key"] for q in quests] == ["two_habits", "plan_day", "talk_adam"]
    assert quests_db.QUEST_KEYS == ["two_habits", "plan_day", "talk_adam"] and quests_db.QUESTS_PER_DAY == 3
    titles = [q["title"] for q in quests]
    assert titles == ["Выполни 2 привычки", "Заполни план дня", "Поговори сегодня с Адамом"]
    assert "сообщение" not in titles[2].lower(), "в названии нет «отправь минимум одно сообщение»"
    assert [q["target"] for q in quests] == [2, 2, 1]


def test_old_random_quests_are_gone_from_the_list_but_their_points_stay(uid):
    ids = _person(uid, habits=2)
    today = str(local_today(uid))
    conn = connect()
    conn.execute("INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) VALUES (?, ?, 'all_done', 't', 1, 1, 8, 1)", (uid, today))
    conn.commit()
    conn.close()
    assert "all_done" not in [q["key"] for q in quests_db.get_daily_quests(uid)]
    assert get_month_quests(uid)["points"] >= 1, "выполненный прежний квест по-прежнему идёт в очки месяца"
    assert set(quests_db.LEGACY_QUEST_KEYS).isdisjoint(quests_db.QUEST_KEYS)


# ---------------------------------------------------------------------------------------------------------------- привычки

def test_two_habits_quest_fills_zero_one_two_and_creating_a_habit_does_not_count(uid):
    ids = _person(uid, habits=2)
    q = _quest(uid, "two_habits")
    assert (q["progress"], q["completed"], q["just_completed"]) == (0, False, False), "создание привычки выполнением не считается"
    complete_habit(ids[0])
    q = _quest(uid, "two_habits")
    assert (q["progress"], q["completed"], q["claimed"]) == (1, False, False)
    complete_habit(ids[1])
    q = _quest(uid, "two_habits")
    assert (q["progress"], q["completed"], q["claimed"], q["just_completed"]) == (2, True, True, True)
    assert _quest(uid, "two_habits")["just_completed"] is False


def test_with_a_single_habit_the_quest_still_asks_for_two(uid):
    ids = _person(uid, habits=1)
    complete_habit(ids[0])
    q = _quest(uid, "two_habits")
    assert (q["progress"], q["target"], q["completed"]) == (1, 2, False)


def test_the_reward_and_the_month_point_come_by_themselves_exactly_once(uid):
    ids = _person(uid, habits=2)
    before_points = get_month_quests(uid)["points"]
    for habit_id in ids:
        complete_habit(habit_id)
    xp = _xp(uid)
    q = _quest(uid, "two_habits")
    assert q["just_completed"] and _xp(uid) == xp + q["reward"]
    after = _xp(uid)
    for _ in range(3):
        quests_db.get_daily_quests(uid)
    assert _xp(uid) == after, "повторные чтения награду не дублируют"
    assert get_month_quests(uid)["points"] == before_points + 1


def test_a_finished_quest_stays_finished_when_the_habit_is_deleted(uid):
    ids = _person(uid, habits=2)
    for habit_id in ids:
        complete_habit(habit_id)
    quests_db.get_daily_quests(uid)
    delete_habit(ids[0])
    q = _quest(uid, "two_habits")
    assert q["completed"] and q["claimed"] and q["progress"] == 2


def test_a_quest_paid_by_the_old_scheme_today_is_not_paid_twice(uid):
    ids = _person(uid, habits=2)
    for habit_id in ids:
        complete_habit(habit_id)
    conn = connect()
    conn.execute("INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) VALUES (?, ?, 'two_habits', 't', 2, 2, 5, 1)", (uid, str(local_today(uid))))
    conn.commit()
    conn.close()
    xp = _xp(uid)
    q = _quest(uid, "two_habits")
    assert q["completed"] and not q["just_completed"] and _xp(uid) == xp


def test_two_simultaneous_payouts_pay_once(uid):
    _person(uid, habits=0)
    definition = quests_db.QUEST_DEFINITIONS["talk_adam"]
    day = str(local_today(uid))
    xp = _xp(uid)
    assert quests_db._award(uid, day, "talk_adam", definition, 1) is True
    assert quests_db._award(uid, day, "talk_adam", definition, 1) is False
    assert _xp(uid) == xp + definition["reward"]


# ---------------------------------------------------------------------------------------------------------------- план дня

def test_the_plan_quest_needs_a_main_and_a_secondary_task(uid):
    _person(uid)
    q = _quest(uid, "plan_day")
    assert (q["progress"], q["completed"]) == (0, False) and [p["done"] for p in q["parts"]] == [False, False]
    assert [p["label"] for p in q["parts"]] == ["Главная задача", "Второстепенная"]
    set_daily_main_goal(uid, "Закончить отчёт")
    q = _quest(uid, "plan_day")
    assert (q["progress"], q["completed"]) == (1, False) and [p["done"] for p in q["parts"]] == [True, False]
    add_daily_task(uid, "Позвонить маме")
    q = _quest(uid, "plan_day")
    assert (q["progress"], q["completed"], q["claimed"], q["just_completed"]) == (2, True, True, True)


def test_only_a_secondary_task_gives_half_and_more_tasks_give_no_extra(uid):
    _person(uid)
    add_daily_task(uid, "Позвонить маме")
    q = _quest(uid, "plan_day")
    assert [p["done"] for p in q["parts"]] == [False, True] and q["progress"] == 1
    set_daily_main_goal(uid, "Главное")
    for text in ("Вторая", "Третья", "Четвёртая"):
        add_daily_task(uid, text)
    q = _quest(uid, "plan_day")
    assert q["progress"] == 2 and q["reward"] == 5, "три задачи — тот же квест и те же баллы"


def test_the_tasks_do_not_have_to_be_done_and_deleting_them_later_changes_nothing(uid):
    _person(uid)
    set_daily_main_goal(uid, "Главное")
    add_daily_task(uid, "Позвонить маме")
    assert _quest(uid, "plan_day")["completed"]
    plan = get_daily_plan(uid)
    conn = connect()
    conn.execute("DELETE FROM daily_plan_tasks WHERE plan_id=?", (plan["id"],))
    conn.commit()
    conn.close()
    q = _quest(uid, "plan_day")
    assert q["completed"] and q["claimed"] and [p["done"] for p in q["parts"]] == [True, True]


def test_yesterdays_plan_that_is_still_on_screen_does_not_count_again(uid):
    _person(uid)
    set_daily_main_goal(uid, "Главное")
    add_daily_task(uid, "Вторая")
    plan_id = str(get_daily_plan(uid)["id"])
    yesterday = str(local_today(uid) - timedelta(days=1))
    conn = connect()
    conn.execute("INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed, ref) VALUES (?, ?, 'plan_day', 't', 2, 2, 5, 1, ?)", (uid, yesterday, plan_id))
    conn.commit()
    conn.close()
    q = _quest(uid, "plan_day")
    assert (q["progress"], q["completed"], q["just_completed"]) == (0, False, False), "на Главной ещё вчерашний план — за сегодня он не засчитывается"


# ---------------------------------------------------------------------------------------------------------------- Адам

def test_a_message_to_adam_completes_the_quest_and_adams_own_message_does_not(uid):
    _person(uid)
    assert _quest(uid, "talk_adam")["completed"] is False
    _say(uid, role="assistant")
    assert _quest(uid, "talk_adam")["completed"] is False, "Адам написал первым — это не разговор"
    _say(uid, role="user")
    q = _quest(uid, "talk_adam")
    assert (q["progress"], q["completed"], q["just_completed"], q["target"]) == (1, True, True, 1)


def test_talking_in_the_morning_already_counts_when_the_window_is_opened_later(uid):
    _person(uid)
    _say(uid, role="user")
    assert _quest(uid, "talk_adam")["completed"], "порядок шагов не важен"
    habits = [_quest(uid, key)["completed"] for key in ("two_habits", "plan_day")]
    assert habits == [False, False]


def test_yesterdays_message_does_not_count_today(uid):
    _person(uid)
    _say(uid, role="user", when=datetime.now(timezone.utc) - timedelta(days=2))
    assert _quest(uid, "talk_adam")["completed"] is False


def test_the_day_is_the_persons_local_day(uid):
    _person(uid)
    from db.streak import set_timezone
    set_timezone(uid, "Asia/Vladivostok")
    tz = ZoneInfo("Asia/Vladivostok")
    today = local_today(uid)
    just_before_midnight = (datetime.combine(today, time.min, tzinfo=tz) - timedelta(minutes=30)).astimezone(timezone.utc)
    _say(uid, role="user", when=just_before_midnight)
    assert _quest(uid, "talk_adam")["completed"] is False, "вчера по его времени"
    just_after_midnight = (datetime.combine(today, time.min, tzinfo=tz) + timedelta(minutes=30)).astimezone(timezone.utc)
    _say(uid, role="user", when=just_after_midnight)
    assert _quest(uid, "talk_adam")["completed"] is True


def test_all_three_together_give_three_month_points(uid):
    ids = _person(uid, habits=2)
    before = get_month_quests(uid)["points"]
    for habit_id in ids:
        complete_habit(habit_id)
    set_daily_main_goal(uid, "Главное")
    add_daily_task(uid, "Вторая")
    _say(uid, role="user")
    assert all(q["completed"] for q in quests_db.get_daily_quests(uid))
    assert get_month_quests(uid)["points"] == before + 3


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_the_habit_route_answers_with_the_quests_and_the_month_points(client, uid):
    ids = _person(uid, habits=2)
    first = await (await client.post(f"/api/habits/{ids[0]}/complete", headers=_headers(uid))).json()
    habits_quest = next(q for q in first["daily_quests"] if q["key"] == "two_habits")
    assert (habits_quest["progress"], habits_quest["completed"], habits_quest["just_completed"]) == (1, False, False)
    assert "month_quests" in first and first["month_quests"]["goal"] == 60
    second = await (await client.post(f"/api/habits/{ids[1]}/complete", headers=_headers(uid))).json()
    done = next(q for q in second["daily_quests"] if q["key"] == "two_habits")
    assert done["completed"] and done["just_completed"], "именно этот ответ несёт момент выполнения — по нему окно покажет перелёт балла"
    assert second["month_quests"]["points"] == first["month_quests"]["points"] + 1


async def test_bootstrap_catches_up_a_quest_done_without_the_app(client, uid):
    _person(uid, habits=1)
    _say(uid, role="user")                                  # сообщение Адаму через бота
    body = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    adam = next(q for q in body["daily_quests"] if q["key"] == "talk_adam")
    assert adam["completed"] and adam["just_completed"]
    again = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert next(q for q in again["daily_quests"] if q["key"] == "talk_adam")["just_completed"] is False


async def test_saving_the_plan_through_the_routes_completes_the_plan_quest(client, uid):
    _person(uid)
    assert (await client.post("/api/plan/main/save", headers=_headers(uid), data=json.dumps({"text": "Главное"}))).status == 200
    assert (await client.post("/api/plan/task", headers=_headers(uid), data=json.dumps({"text": "Вторая"}))).status == 200
    body = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    plan = next(q for q in body["daily_quests"] if q["key"] == "plan_day")
    assert plan["completed"] and plan["just_completed"] and [p["done"] for p in plan["parts"]] == [True, True]


async def test_a_message_in_the_chat_tells_the_coach_about_the_finished_quest(client, uid, monkeypatch):
    import webapp.routes_ai_miniapp as route_mod
    from tests.test_ai_quota import _mock_ai_chat_deps

    _person(uid)
    _mock_ai_chat_deps(monkeypatch)

    async def fake_solve(**kwargs):
        return {"answer": "Давай разберём день.", "is_crisis": False, "suggested_habit": None, "complexity": "просто"}

    monkeypatch.setattr(route_mod, "solve_task_multiagent", fake_solve)
    response = await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "помоги разобрать день", "request_id": "r-quest"})
    body = await response.json()
    assert response.status == 200
    assert body["quest_events"] == [{"key": "talk_adam", "title": "Поговори сегодня с Адамом", "reward": 5}]
    second = await (await client.post("/api/ai/chat", json={"init_data": sign_init_data(uid), "message": "и ещё вопрос", "request_id": "r-quest-2"})).json()
    assert second["quest_events"] == [], "второе сообщение квест заново не выдаёт"


async def test_the_old_claim_route_has_nothing_to_claim_any_more(client, uid):
    _person(uid)
    response = await client.post("/api/quests/two_habits/claim", headers=_headers(uid))
    assert response.status == 400 and (await response.json())["error"] == "quest_not_claimable"
    unknown = await client.post("/api/quests/not_a_quest/claim", headers=_headers(uid))
    assert unknown.status == 400


# ---------------------------------------------------------------------------------------------------------------- окно и скрипт

def test_the_window_has_a_list_and_the_month_card_and_no_old_hint():
    block = INDEX[INDEX.index('id="dailyQuestsOverlay"'):][:3600]
    for needle in ('id="dailyQuestsList"', 'id="monthCard"', 'id="monthCardTrack"', 'id="monthCardCount"', 'id="dailyQuestsClose"'):
        assert needle in block, needle
    assert "dailyQuestsNoHabitsHint" not in INDEX and "dailyQuestsNoHabitsHint" not in APP_JS


def test_the_script_compares_before_and_after_and_has_no_claim_button_any_more():
    assert "daily-quest__claim" not in APP_JS
    patch = APP_JS[APP_JS.index("function applyActionPatch(result) {"):][:5200]
    assert "const questsBefore = state.daily_quests;" in patch and "announceQuestChange(questsBefore, monthBefore);" in patch
    assert patch.index("const questsBefore") < patch.index("state.daily_quests = result.daily_quests")
    boot = APP_JS[APP_JS.index("async function loadBootstrap()"):][:2600]
    assert "announceQuestChange(previousState.daily_quests, previousState.month_quests)" in boot and "announceCaughtUpQuests(state.daily_quests)" in boot
    render = APP_JS[APP_JS.index("function renderDailyQuests() {"):][:1100]
    assert "if (questsAnim) return;" in render and "has-claimable\", (state.month_quests?.claimable || 0) > 0" in render


def test_the_window_opens_after_the_first_habit_and_waits_for_the_other_windows():
    announce = APP_JS[APP_JS.index("function announceQuestChange("):][:900]
    assert "if (!diff.habitsMoved && !diff.done.length) return;" in announce, "частичный прогресс плана окно не открывает"
    assert "questsPending?.before || waiting?.before || before" in announce, "быстрые отметки подряд показываются одним окном от самого первого состояния"
    schedule = APP_JS[APP_JS.index("function scheduleQuestsWindow("):][:1500]
    assert "celebrationOverlayOpen() || document.hidden" in schedule and "quietTicks < 2" in schedule and 'trk("quests_window_auto")' in schedule


def test_the_fill_the_check_and_the_flying_point_are_all_there_and_light():
    play = APP_JS[APP_JS.index("function playQuestsChange("):][:2200]
    assert "fill.style.width" in play and "QUEST_FILL_MS" in play and 'haptic("light")' in play
    done = APP_JS[APP_JS.index("function completeQuestRow("):][:2200]
    assert 'haptic("success")' in done and "flyQuestPoint(" in done and '"is-ready is-pop"' in done and "inviteQuestChests()" in done and "count.classList.add(\"is-bump\")" in done
    fly = APP_JS[APP_JS.index("function flyQuestPoint("):][:1400]
    assert "fromEl.animate" in fly and "fill: \"forwards\"" in fly and "quest-point" in fly
    assert "infinite" not in fly.lower() and "Infinity" not in fly


def test_tapping_an_unfinished_quest_leads_to_its_next_step():
    row = APP_JS[APP_JS.index("function questRowHtml("):][:1500]
    assert 'data-go="${q.key}" role="button" tabindex="0"' in row, "у выполненного квеста перехода нет"
    go = APP_JS[APP_JS.index("function goToQuestStep("):][:1300]
    assert '"aiCoachBtn"' in go and '"mainGoalInput"' in go and 'addHabitCollapse' in go and 'trk("quests_go", key)' in go


def test_the_simulation_scenes_use_the_same_titles_as_the_server_and_are_registered():
    from db.sim_lab import SCENES
    keys = [row[0] for row in SCENES]
    for key in ("quests_first", "quests_done", "quests_all", "quests_plan", "quests_chests"):
        assert key in keys, key
    for key, definition in quests_db.QUEST_DEFINITIONS.items():
        assert f'title: "{definition["title"]}"' in APP_JS, key
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):]
    block = block[:block.index("\n};\n")]
    assert block.count("openQuestsWindow({") == 5
    for forbidden in ("api(", "fetch(", "applyActionPatch", "bootstrap", "claim"):
        assert forbidden not in block, forbidden
    assert "closeQuestsOverlay();" in APP_JS[APP_JS.index("function playSimScene()"):][:700]


def test_hints_and_chips_do_not_repeat_the_words_of_the_window_title():
    title = re.search(r'id="dailyQuestsModalTitle">([^<]+)<', INDEX).group(1)
    body = APP_JS[APP_JS.index("function questHint("):]
    body = body[:body.index("\n}\n")]
    hints = re.findall(r'"([^"]*[А-Яа-я][^"]*)"', body)
    assert len(hints) == 4
    for hint in hints:
        assert not shared(title, hint), hint
    assert not shared(title, "Главная задача", "Второстепенная")
    month = re.sub(r"\$\{[^}]*\}", " ", re.search(r"`До ближайшего сундука[^`]+`", APP_JS).group(0))
    assert not shared(title, month), "подпись месячной карточки не повторяет заголовок окна"
    for q in quests_db.QUEST_DEFINITIONS.values():
        assert not shared(title, q["title"]), q["title"]


def test_the_events_and_the_feature_are_registered():
    from db.tracking import EVENT_LABELS
    from webapp.feature_map import FEATURES
    feature = next(f for f in FEATURES if f.id == "quests")
    for name in ("quest_done", "quests_window_auto", "quests_window_open", "quests_go"):
        assert name in EVENT_LABELS and name in feature.events, name
    for name in re.findall(r'trk\("(quests_\w+)"', APP_JS):
        assert name in feature.events, name


def test_the_styles_are_light_for_weak_webviews_and_sit_before_the_chest_block():
    css = CSS[CSS.index("Квесты дня (app.js::openQuestsWindow"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    for needle in (".daily-quest__part.is-on{", ".daily-quest__done.is-pop{", ".quest-point{", ".month-card__bar-fill{transition:width", ".daily-quest[data-go]{"):
        assert needle in css, needle
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in css, forbidden
    assert "Квесты дня (app.js" in CSS and CSS.index("Квесты дня (app.js") < CSS.index("Окно «Сундук» (открытие сундука месяца")


# ---------------------------------------------------------------------------------------------------------------- Node: разбор изменений

@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_diff_hint_and_percent_helpers_behave():
    def _function(header):
        start = APP_JS.index(header)
        return APP_JS[start:APP_JS.index("\n}\n", start) + 3]

    script = r"""
const rows = JSON.parse(process.argv[1]);
%s
%s
%s
const out = {
  moved: diffQuests(rows.before, rows.after),
  none: diffQuests(null, rows.after),
  pct: [questPct({ target: 2 }, { progress: 1, completed: false }), questPct({ target: 2 }, { progress: 2, completed: true }), questPct({ target: 1 }, { progress: 0, completed: false }), questPct({ target: 1 }, { progress: 1, completed: true })],
  hints: [questHint({ key: "two_habits" }, { completed: false }, 0), questHint({ key: "two_habits" }, { completed: false }, 1), questHint({ key: "two_habits" }, { completed: false }, 3), questHint({ key: "two_habits" }, { completed: true }, 1), questHint({ key: "talk_adam" }, { completed: false }, 1, false), questHint({ key: "plan_day" }, { completed: false }, 1), questHint({ key: "talk_adam" }, { completed: false }, 2, true)],
};
process.stdout.write(JSON.stringify(out));
""" % (_function("function questPct("), _function("function questHint("), _function("function diffQuests("))
    q = lambda key, progress, completed: {"key": key, "progress": progress, "completed": completed}
    rows = {
        "before": [q("two_habits", 0, False), q("plan_day", 1, False), q("talk_adam", 0, False)],
        "after": [q("two_habits", 1, False), q("plan_day", 2, True), q("talk_adam", 0, False)],
    }
    result = subprocess.run([NODE, "-e", script, json.dumps(rows)], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["moved"] == {"habitsMoved": True, "done": ["plan_day"]}
    assert out["none"] == {"habitsMoved": False, "done": []}, "без прошлого состояния сравнивать не с чем"
    assert out["pct"] == [50, 100, 0, 100]
    assert out["hints"][0] == "Для начала добавь первую и отметь её" and out["hints"][1] == "Добавь вторую — и полоска дойдёт до конца"
    assert out["hints"][2] == "" and out["hints"][3] == "" and out["hints"][4].startswith("Расскажи, как идёт день") and out["hints"][5] == ""
    assert out["hints"][6] == "Всё записано — попроси разложить дела по времени", "план записан — Адам зовёт раскладывать дела по времени"


# ---------------------------------------------------------------------------------------------------------------- остаток ТЗ п.14

def _node_function(header):
    start = APP_JS.index(header)
    return APP_JS[start:APP_JS.index("\n}\n", start) + 3]


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_plan_quest_window_waits_two_minutes_after_the_last_edit():
    """ТЗ п.5: квест про план выполнен и сохранён сразу, а окно с этим — через две минуты после ПОСЛЕДНЕЙ правки (каждая правка — отсчёт заново)."""
    functions = "\n".join(_node_function(h) for h in (
        "function diffQuests(", "function announceQuestChange(", "function restartPlanHold(", "function clearPlanHold(", "function firePlanHold(",
        "function notePlanEdited(", "function openQuestsFromButton("))
    script = r"""
let timers = []; let now = 0; let nextId = 1;
function setTimeout(fn, ms) { const id = nextId++; timers.push({ id, at: now + ms, fn }); return id; }
function clearTimeout(id) { timers = timers.filter((t) => t.id !== id); }
function advance(ms) {
  const target = now + ms;
  for (;;) {
    const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    timers = timers.filter((t) => t !== due); now = due.at; due.fn();
  }
  now = target;
}
const QUESTS_PLAN_DELAY_MS = 120000;
let labMode = false, simMode = false, state = {}, questsPending = null, questsPlanHold = null, questsPlanTimer = null;
const scheduled = [], opened = [];
function scheduleQuestsWindow(payload) { scheduled.push(payload); }
function openQuestsWindow(payload) { opened.push(payload); }
function openQuestsOverlay() { opened.push("plain"); }
function trk() {}
%s
const q = (key, progress, completed) => ({ key, progress, completed });
const habits1 = q("two_habits", 1, false), habits2 = q("two_habits", 2, true), adam = q("talk_adam", 0, false);
const planHalf = q("plan_day", 1, false), planDone = q("plan_day", 2, true);
const out = {};

// 1. квест про план выполнился один: окно держим
state = { daily_quests: [habits2, planDone, adam], month_quests: { points: 6 } };
announceQuestChange([habits2, planHalf, adam], { points: 5 });
out.held = { scheduled: scheduled.length, hold: !!questsPlanHold };
advance(119000); out.at119 = scheduled.length;
// 2. правка на 119-й секунде — отсчёт заново
notePlanEdited(); advance(119000); out.afterEdit = scheduled.length;
advance(1500); out.afterTwoMinutes = scheduled.length;
out.payload = { beforePlan: scheduled[0].before[1].progress, monthBefore: scheduled[0].monthBefore.points, afterPlan: scheduled[0].after[1].progress, hold: !!questsPlanHold };

// 3. правка без ожидающего окна ничего не заводит
notePlanEdited(); advance(300000); out.idleEdit = scheduled.length;

// 4. окно ждёт, а человек отметил привычку: всё показываем сразу одним окном от самого раннего состояния, таймер снят
scheduled.length = 0;
state = { daily_quests: [habits1, planDone, adam], month_quests: { points: 6 } };
announceQuestChange([habits1, planHalf, adam], { points: 5 });
state = { daily_quests: [habits2, planDone, adam], month_quests: { points: 7 } };
announceQuestChange([habits1, planDone, adam], { points: 6 });
out.merged = { scheduled: scheduled.length, beforePlan: scheduled[0].before[1].progress, monthBefore: scheduled[0].monthBefore.points, hold: !!questsPlanHold };
advance(300000); out.noSecondWindow = scheduled.length;

// 5. человек открыл квесты сам раньше двух минут: результат сразу, второго окна по таймеру нет
scheduled.length = 0;
state = { daily_quests: [habits2, planDone, adam], month_quests: { points: 8 } };
announceQuestChange([habits2, planHalf, adam], { points: 7 });
advance(30000); openQuestsFromButton();
out.manual = { opened: opened.length, before: opened[opened.length - 1].before[1].progress, after: opened[opened.length - 1].after[1].progress, hold: !!questsPlanHold };
advance(300000); out.noTimerWindow = scheduled.length;

// 6. без ожидающего окна кнопка просто открывает окно
opened.length = 0; openQuestsFromButton(); out.plain = opened.slice();
process.stdout.write(JSON.stringify(out));
""" % functions
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["held"] == {"scheduled": 0, "hold": True}, "сразу окно не показываем: человек может ещё записывать задачи"
    assert out["at119"] == 0
    assert out["afterEdit"] == 0, "правка на 119-й секунде отодвинула показ ещё на две минуты"
    assert out["afterTwoMinutes"] == 1
    assert out["payload"] == {"beforePlan": 1, "monthBefore": 5, "afterPlan": 2, "hold": False}, "окно показывает, как квест про план завершается"
    assert out["idleEdit"] == 1
    assert out["merged"] == {"scheduled": 1, "beforePlan": 1, "monthBefore": 5, "hold": False}
    assert out["noSecondWindow"] == 1
    assert out["manual"] == {"opened": 1, "before": 1, "after": 2, "hold": False}
    assert out["noTimerWindow"] == 0
    assert out["plain"] == ["plain"]


def test_the_two_minutes_are_stored_but_the_quest_itself_is_saved_at_once(uid):
    """ТЗ п.5: сама выдача не ждёт таймера — закрыл приложение, и квест с баллом уже у человека."""
    _person(uid)
    before = get_month_quests(uid)["points"]
    set_daily_main_goal(uid, "Главное")
    add_daily_task(uid, "Вторая")
    quest = _quest(uid, "plan_day")
    assert quest["claimed"] and quest["completed"]
    assert get_month_quests(uid)["points"] == before + 1
    assert "const QUESTS_PLAN_DELAY_MS = 120000;" in APP_JS


def test_every_plan_edit_restarts_the_wait():
    start = APP_JS.index("mainConfirm.addEventListener(\"click\"")
    handlers = APP_JS[start:start + 5200]
    assert handlers.count("notePlanEdited();") >= 4, "главная, удаление главной, добавление/правка и удаление задачи"
    assert "async function" not in handlers[:0]
    assert 'document.getElementById("dailyQuestsBtn")?.addEventListener("click", openQuestsFromButton);' in APP_JS


def test_the_month_scale_shows_a_plus_one_next_to_the_counter():
    done = APP_JS[APP_JS.index("function completeQuestRow("):][:2600]
    assert 'plus.className = "month-card__plus"' in done and 'plus.textContent = "+1"' in done and 'plus.classList.add("is-on")' in done
    css = CSS[CSS.index("Квесты дня (app.js::openQuestsWindow"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    assert ".month-card__plus{" in css and ".month-card__plus.is-on{" in css and ".daily-quest[data-go]::after{" in css
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in css, forbidden


def test_with_the_plan_written_the_adam_quest_asks_him_to_lay_the_day_out_by_time():
    go = APP_JS[APP_JS.index("function goToQuestStep("):][:1500]
    assert 'window.location.href = "/coach?intro=plans&t=schedule"' in go and "questsPlanReady()" in go
    ready = APP_JS[APP_JS.index("function questsPlanReady("):][:300]
    assert "main_goal" in ready and "tasks" in ready
    coach = (STATIC / "ai_coach.js").read_text(encoding="utf-8")
    starter = re.search(r"schedule: '([^']+)'", coach).group(1)
    assert "распределить" in starter and "по времени" in starter and "привычки" in starter
    from webapp.services import chat_brain
    assert chat_brain.detect_scenarios(starter) == ["plan"], "стартовая фраза узнаётся как просьба о расписании"


@pytest.mark.parametrize("text", [
    "помоги распределить задачи по времени", "Помоги распределить мои задачи на сегодня по времени: учти привычки, работу и отдых.",
    "что мне сегодня реально успеть", "разложи мои дела на день", "составь расписание на вечер", "успею ли я всё сегодня",
])
def test_adam_recognises_the_requests_to_lay_the_day_out(text):
    from webapp.services import chat_brain
    assert "plan" in chat_brain.detect_scenarios(text), text


def test_the_schedule_scenario_uses_the_data_and_asks_only_for_what_is_missing():
    from webapp.services import chat_brain
    note = chat_brain.GUIDE["plan"]
    for needle in ("НЕ проси переписывать задачи", "временными окнами", "время привычек", "отдых", "ОДИН короткий вопрос", "предварительный вариант дай сразу"):
        assert needle in note, needle
