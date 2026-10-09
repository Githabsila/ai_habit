"""
Парное задание, 09.10: (1) незавершённые задания по старым правилам (дни, цель 10) переходят на новые (привычки, цель 20) —
иначе две привычки за день давали одно очко; (2) окно «Как это работает» — по ⓘ и по тапу на любое место карточки в Рейтинге.
"""
import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from db import get_pair_quest, send_pair_invite
from db.core import create_tables
from db.pair_quests import LEGACY_PAIR_GOAL, PAIR_DAY_CAP, PAIR_GOAL, upgrade_legacy_quests
from db.streak import local_today
from tests.test_pair_habits import _event
from tests.test_pair_quests import _clean, _complete, _one, _pair, _sql, _start, _window

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
_uid_counter = itertools.count(820_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _legacy_active(uid_):
    """Идущее старое задание: сегодня — его третий день, у A сегодня отмечен день и закрыты две привычки."""
    a, b = _pair(uid_)
    quest_id = _start(a, b)                                  # rules='days', цель 10
    _window(quest_id)
    _clean(a, b)
    today = str(local_today(a))
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', 1)", (a, today))
    _event(a, today)
    _event(a, today)
    return a, b, quest_id


# ---------------------------------------------------------------------------
# ПЕРЕВОД СТАРЫХ ЗАДАНИЙ НА НОВЫЕ ПРАВИЛА
# ---------------------------------------------------------------------------

def test_started_old_quest_becomes_a_habit_quest_and_counts_every_habit(uid):
    a, b, quest_id = _legacy_active(uid)
    before = get_pair_quest(a)["quest"]
    assert (before["rules"], before["goal"], before["progress"]) == ("days", LEGACY_PAIR_GOAL, 1), "по старым правилам день — одно очко"

    upgraded = {e["id"]: e for e in upgrade_legacy_quests()}
    assert upgraded[quest_id]["status"] == "active" and upgraded[quest_id]["progress"] == 2 and "pace" in upgraded[quest_id]

    after = get_pair_quest(a)["quest"]
    assert (after["rules"], after["goal"], after["cap"], after["progress"]) == ("habits", PAIR_GOAL, PAIR_DAY_CAP, 2)
    assert after["today"]["me"] == 2 and after["need"] == PAIR_GOAL - 2
    assert get_pair_quest(b)["quest"]["goal"] == PAIR_GOAL, "у напарника — тоже"


def test_second_run_changes_nothing(uid):
    a, b, quest_id = _legacy_active(uid)
    assert quest_id in {e["id"] for e in upgrade_legacy_quests()}
    assert quest_id not in {e["id"] for e in upgrade_legacy_quests()}
    assert _one("SELECT rules, goal FROM pair_quests WHERE id=?", (quest_id,))["goal"] == PAIR_GOAL


def test_pending_old_invite_gets_the_new_goal_too(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    _sql("UPDATE pair_quests SET rules='days', goal=? WHERE id=?", (LEGACY_PAIR_GOAL, quest_id))
    upgraded = {e["id"]: e for e in upgrade_legacy_quests()}
    assert upgraded[quest_id]["status"] == "pending"
    row = _one("SELECT rules, goal, status FROM pair_quests WHERE id=?", (quest_id,))
    assert (row["rules"], row["goal"], row["status"]) == ("habits", PAIR_GOAL, "pending")


def test_finished_old_quest_keeps_its_rules_and_the_chest(uid):
    a, b = _pair(uid)
    quest_id = _complete(a, b)
    assert quest_id not in {e["id"] for e in upgrade_legacy_quests()}
    row = _one("SELECT rules, goal, status FROM pair_quests WHERE id=?", (quest_id,))
    assert (row["rules"], row["goal"], row["status"]) == ("days", LEGACY_PAIR_GOAL, "completed")
    assert get_pair_quest(a)["quest"]["claimable"] is True, "награда заработана по старым правилам"


def test_new_quests_are_not_touched(uid):
    a, b = _pair(uid)
    quest_id = send_pair_invite(a, b)["quest_id"]
    assert quest_id not in {e["id"] for e in upgrade_legacy_quests()}
    assert _one("SELECT rules, goal FROM pair_quests WHERE id=?", (quest_id,))["rules"] == "habits"


def test_startup_runs_the_upgrade(uid):
    """create_tables() вызывается при старте бота (main.py) — перевод идёт оттуда, отдельного шага при деплое нет."""
    a, b, quest_id = _legacy_active(uid)
    create_tables()
    assert _one("SELECT rules, goal FROM pair_quests WHERE id=?", (quest_id,))["goal"] == PAIR_GOAL
    core = (ROOT / "db" / "core.py").read_text(encoding="utf-8")
    assert "from .pair_quests import upgrade_legacy_quests" in core[core.index("_ensure_streak_tables()"):]


def test_invite_push_names_habits_not_days():
    src = (ROOT / "webapp" / "webapp_server.py").read_text(encoding="utf-8")
    assert "нужно закрыть" in src and "{PAIR_GOAL} привычек на двоих за неделю" in src
    assert "{PAIR_GOAL} дней на двоих" not in src


# ---------------------------------------------------------------------------
# ОКНО «КАК ЭТО РАБОТАЕТ»
# ---------------------------------------------------------------------------

def _node(js):
    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен")
    done = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _function_source(name):
    start = APP_JS.index(f"function {name}(")
    return APP_JS[start:APP_JS.index("\n}\n", start) + 3]


def test_guide_text_comes_from_the_server_numbers_and_does_not_repeat_the_title():
    helpers = APP_JS[APP_JS.index("function ruPlural("):APP_JS.index("function pairDaysWord(n)")]
    helpers += APP_JS[APP_JS.index("function pairDaysWord(n)"):].split("\n", 1)[0]
    code = helpers + "\n" + _function_source("pairGuideContent")
    js = (
        "const ADAM_COIN_ICON = '[coin]';\n"
        f"eval({json.dumps(code)} + ';globalThis.fn = pairGuideContent;');\n"
        "const real = fn({goal: 20, window_days: 7, day_cap: 3, reward: {coins: 60, diamonds: 1}});\n"
        "const bare = fn(null);\n"
        "const odd = fn({goal: 21, window_days: 1, day_cap: 2, reward: {coins: 5, diamonds: 0}});\n"
        "console.log(JSON.stringify({real, bare, odd}));"
    )
    out = _node(js)
    assert out["real"]["title"] == "20 привычек на двоих за 7 дней"
    assert out["bare"]["title"] == out["real"]["title"], "без данных — те же правила по умолчанию"
    assert out["odd"]["title"] == "21 привычка на двоих за 1 день"
    rows = out["real"]["list"]
    assert rows.count("<li>") == 3
    assert "до <b>3</b> привычек в день" in rows and "<b>оба</b>" in rows and "<b>+60</b> [coin] и 💎1" in rows
    assert "20" not in rows.replace("+60", "").replace("🎁", "") and "7 дн" not in rows, "цель и срок уже в заголовке"


def test_guide_overlay_markup_is_hidden_and_filled_by_script():
    block = INDEX[INDEX.index('id="pairGuideOverlay"'):][:900]
    for needle in ('hidden aria-hidden="true"', 'role="dialog"', 'id="pairGuideTitle"', 'id="pairGuideList"',
                   'id="pairGuideClose"', "ПАРНОЕ ЗАДАНИЕ", "Понятно"):
        assert needle in block, needle
    assert INDEX.index('id="doubleBonusOverlay"') < INDEX.index('id="pairGuideOverlay"') < INDEX.index('id="archetypeQuizOverlay"')


def test_info_button_and_the_whole_rating_card_open_the_guide():
    assert 'if (act === "info") { openPairGuide(); return; }' in APP_JS
    assert "pairInfoOpen" not in APP_JS and "pairInfoHtml" not in APP_JS and "pq-info\"" not in APP_JS
    rating = APP_JS[APP_JS.index('document.getElementById("pairQuestCardRating")?.addEventListener("click"'):][:520]
    assert 'e.target.closest("button, a, [data-pair-act], [data-pair-toggle]")) return onCardClick(e);' in rating
    assert "openPairGuide();" in rating, "тап мимо кнопок открывает окно"
    render = APP_JS[APP_JS.index("function renderPairCardInto("):][:1400]
    assert 'box.classList.toggle("pair-card--tappable", !collapsible)' in render, "нажимается только карточка в Рейтинге"
    assert "pq-info-btn pq-info-btn--head" in APP_JS and "${infoBtn}${toggle}" in APP_JS, "ⓘ — в шапке, одна на карточку"
    actions = APP_JS[APP_JS.index("function pairActionsHtml("):][:700]
    assert "PAIR_INFO_ICON" not in actions


def test_guide_closes_by_button_backdrop_and_escape_but_not_by_a_double_tap():
    wiring = APP_JS[APP_JS.index('const guide = document.getElementById("pairGuideOverlay")'):][:700]
    assert "performance.now() - pairGuideOpenedAt < 300" in wiring
    assert 'e.target === guide || e.target.closest("#pairGuideClose")' in wiring
    assert 'e.key === "Escape"' in wiring and "closePairGuide()" in wiring
    assert '"pairGuideOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:400], "окно напоминаний ждёт, пока оно закрыто"


def test_guide_css_is_premium_but_light_for_the_webview():
    start = CSS.index("/* ===== Парное задание: окно «Как это работает»")
    block = CSS[start:]
    for banned in ("filter:", "backdrop-filter", "infinite", "will-change", "@keyframes"):
        assert banned not in block, banned
    assert ".pq-guide-overlay[hidden]{display:none}" in block, "скрытое окно не должно висеть слоем на весь экран"
    assert "width:min(380px,100%)" in block and ".pair-card--tappable{cursor:pointer" in block
    assert "linear-gradient(135deg,#FFD97A,#F59E1B)" in block
    assert ".pq-info{" not in CSS and ".pq-info-btn.is-open" not in CSS, "старая плашка удалена"
