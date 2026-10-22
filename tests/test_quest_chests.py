"""
Сундуки у квестов дня (по скринам Duolingo): в конце полоски каждого квеста свой сундук (дерево / синий / золото); выполнил квест — сундук «готов»,
тап по нему открывает окно «Сундук» с тремя тапами и вибрацией. Награду (+Adam Coin и очко месяца) сервер выдаёт сам при выполнении, как и раньше,
поэтому сундук её только показывает и запоминает «открыт» (db/quests.py::open_quest_chest). Здесь: правила и маршрут, события, разметка, скрипт и стили.
"""
import itertools
import json
import re
from pathlib import Path

import pytest

from db import add_habit, add_user, complete_habit, get_habits, get_user
from db import quests as quests_db
from db.core import connect
from db.month_quests import get_month_quests
from tests.conftest import sign_init_data
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
_counter = itertools.count(860_500_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _person_with_done_habits(uid_):
    add_user(uid_, f"c{uid_}", "Аня")
    for i in range(2):
        add_habit(uid_, f"Привычка {i + 1}")
    for habit in get_habits(uid_):
        complete_habit(habit["id"])


def _quest(uid_, key):
    return next(q for q in quests_db.get_daily_quests(uid_) if q["key"] == key)


def _chest_events(uid_):
    conn = connect()
    try:
        return [r["step"] for r in conn.execute("SELECT step FROM user_events WHERE user_id=? AND name='chest_opened'", (uid_,)).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- правила

def test_every_quest_has_its_own_chest_colour_and_starts_closed(uid):
    add_user(uid, f"c{uid}", "Аня")
    quests = quests_db.get_daily_quests(uid)
    assert [q["chest"] for q in quests] == ["wood", "blue", "gold"]
    assert all(q["chest_opened"] is False for q in quests)


def test_an_unfinished_or_unknown_quest_has_no_chest_to_open(uid):
    add_user(uid, f"c{uid}", "Аня")
    assert quests_db.open_quest_chest(uid, "two_habits") is None, "квест не выполнен — открывать нечего"
    assert quests_db.open_quest_chest(uid, "no_such_quest") is None
    assert quests_db.open_quest_chest(uid, "before_noon") is None, "прежние квесты сундуков не имеют"
    assert _chest_events(uid) == []


def test_opening_shows_the_reward_but_pays_nothing_new(uid):
    _person_with_done_habits(uid)
    quests_db.get_daily_quests(uid)                       # награда выдана при выполнении
    xp, points = get_user(uid)["xp"], get_month_quests(uid)["points"]
    result = quests_db.open_quest_chest(uid, "two_habits")
    assert result == {"reward": {"coins": 5, "month_points": 1}, "first": True}
    assert get_user(uid)["xp"] == xp and get_month_quests(uid)["points"] == points, "сундук — показ награды, начислений нет"
    assert _quest(uid, "two_habits")["chest_opened"] is True
    assert _quest(uid, "plan_day")["chest_opened"] is False, "другие сундуки не затронуты"


def test_a_second_opening_is_harmless_and_counted_once(uid):
    _person_with_done_habits(uid)
    quests_db.open_quest_chest(uid, "two_habits")
    again = quests_db.open_quest_chest(uid, "two_habits")
    assert again["first"] is False and again["reward"]["coins"] == 5
    assert _chest_events(uid) == ["quest_two_habits"], "в учёте сундук — одно событие, сколько бы раз ни тапали"


def test_a_quest_finished_while_the_app_was_closed_can_still_be_opened(uid):
    """Открыли сундук раньше, чем приложение успело «догнать» выполненный квест: награда выдаётся здесь же, а не теряется."""
    _person_with_done_habits(uid)
    result = quests_db.open_quest_chest(uid, "two_habits")           # первое же обращение после выполнения
    assert result["first"] is True
    conn = connect()
    paid = conn.execute("SELECT COUNT(*) AS n FROM daily_quests WHERE user_id=? AND quest_key='two_habits' AND claimed=1", (uid,)).fetchone()["n"]
    conn.close()
    assert paid == 1


# ---------------------------------------------------------------------------------------------------------------- маршрут

async def test_the_open_route_answers_with_the_reward_and_the_fresh_quests(client, uid):
    _person_with_done_habits(uid)
    early = await client.post("/api/quests/plan_day/open", headers=_headers(uid))
    assert early.status == 400 and (await early.json())["error"] == "quest_not_done"
    assert (await client.post("/api/quests/nope/open", headers=_headers(uid))).status == 400
    ok = await client.post("/api/quests/two_habits/open", headers=_headers(uid))
    body = await ok.json()
    assert ok.status == 200 and body["ok"] and body["first"] and body["reward"] == {"coins": 5, "month_points": 1}
    assert next(q for q in body["daily_quests"] if q["key"] == "two_habits")["chest_opened"] is True
    assert body["month_quests"]["goal"] == 60
    repeat = await (await client.post("/api/quests/two_habits/open", headers=_headers(uid))).json()
    assert repeat["first"] is False


async def test_the_route_needs_a_logged_in_person(client):
    assert (await client.post("/api/quests/two_habits/open")).status in (401, 403)


async def test_bootstrap_carries_the_chest_state(client, uid):
    _person_with_done_habits(uid)
    quests_db.open_quest_chest(uid, "two_habits")
    body = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    by_key = {q["key"]: q for q in body["daily_quests"]}
    assert by_key["two_habits"]["chest_opened"] is True and by_key["two_habits"]["chest"] == "wood"
    assert by_key["talk_adam"]["chest_opened"] is False


# ---------------------------------------------------------------------------------------------------------------- приложение

def test_each_row_shows_a_locked_ready_or_opened_chest():
    side = APP_JS[APP_JS.index("function questSideHtml("):][:1500]
    assert "is-locked" in side and "is-ready" in side and "is-opened" in side
    assert 'data-chest="${q.key}"' in side and "<button" in side, "готовый сундук — настоящая кнопка"
    assert "aria-label" in side, "сундуки подписаны для скринридера"
    assert "daily-quest__progress" in side, "у невыполненного квеста по-прежнему видно «1/2»"
    svg = APP_JS[APP_JS.index("function questChestSvg("):][:1400]
    assert "qc-lock" in svg and "qc-glow" in svg, "закрытый — с замком, открытый — со свечением внутри"


def test_finishing_a_quest_wakes_its_chest_and_invites_a_tap():
    done = APP_JS[APP_JS.index("function completeQuestRow("):][:1800]
    assert '"is-ready is-pop"' in done and "inviteQuestChests()" in done and "updateQuestChestHint()" in done
    assert "flyQuestPoint(check || li" in done, "балл вылетает из сундука"
    invite = APP_JS[APP_JS.index("function inviteQuestChests("):][:700]
    assert "iterations: 3" in invite and "infinite" not in invite.lower() and "Infinity" not in invite, "приглашение конечное"
    assert 'el.animate' in invite and "transform" in invite


def test_tapping_a_ready_chest_runs_the_three_taps_and_asks_the_server_after_them():
    assert "if (chest) { openQuestChest(chest.dataset.chest, chest); return; }" in APP_JS
    opener = APP_JS[APP_JS.index("async function openQuestChest("):][:2300]
    assert "openChestReveal({" in opener and 'tone: kind' in opener and 'eyebrow: "ТВОЙ СУНДУК"' in opener
    assert "/api/quests/${encodeURIComponent(key)}/open" in opener
    assert "applyActionPatch(res)" in opener, "состояние квестов обновляется после закрытия окна"
    assert "questChestBusy" in opener, "второй тап по сундуку, пока окно открыто, ничего не запускает"
    assert "if (result === null) btn.disabled = false;" in opener, "«Не сейчас» оставляет сундук ждать"
    assert "const CHEST_TAPS = 3;" in APP_JS
    reveal = APP_JS[APP_JS.index("async function openChestReveal("):][:5200]
    assert 'tone = ""' in reveal and "card.dataset.tone" in reveal
    assert reveal.index("await new Promise((resolve) => {") < reveal.rindex("result = await request();"), "запрос уходит только после тапов"


def test_the_demo_never_talks_to_the_server_from_the_chest():
    opener = APP_JS[APP_JS.index("async function openQuestChest("):][:2300]
    assert "const sim = !!simMode;" in opener and "simReply({ reward: { coins: 5, month_points: 1 } })" in opener
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):]
    block = block[:block.index("\n};\n")]
    for key in ("chest_quest:", "quests_chests:"):
        assert key in block, key
    for forbidden in ("api(", "fetch(", "applyActionPatch", "bootstrap", "claim"):
        assert forbidden not in block, forbidden
    from db.sim_lab import SCENES
    assert {"chest_quest", "quests_chests"} <= {row[0] for row in SCENES}


def test_the_window_calls_the_person_to_a_ready_chest_without_repeating_words():
    assert 'id="dailyQuestsChestHint" hidden' in INDEX
    title = re.search(r'id="dailyQuestsModalTitle">([^<]+)<', INDEX).group(1)
    hint = re.search(r'id="dailyQuestsChestHint"[^>]*>([^<]+)<', INDEX).group(1)
    assert shared(title, hint) == set(), shared(title, hint)
    # окно открытия: заголовок, подпись-название квеста и кнопки не повторяют слов друг друга
    for caption in ("Выполни 2 привычки", "Заполни план дня", "Поговори сегодня с Адамом"):
        assert shared("ТВОЙ СУНДУК", "Тебя ждёт награда", caption, "Не сейчас", "Забрать", "Всё твоё!") == set(), caption
    hint_fn = APP_JS[APP_JS.index("function updateQuestChestHint("):][:360]
    assert "hint.hidden = !document.querySelector" in hint_fn


def test_the_demo_data_carries_the_chest_fields():
    for helper in ("simQuestHabits", "simQuestPlan", "simQuestAdam"):
        line = APP_JS[APP_JS.index(f"const {helper} = "):][:900]
        assert "chest_opened" in line and "chest:" in line, helper
    assert '"wood"' in APP_JS[APP_JS.index("const simQuestHabits"):][:500]
    assert '"blue"' in APP_JS[APP_JS.index("const simQuestPlan"):][:700]
    assert '"gold"' in APP_JS[APP_JS.index("const simQuestAdam"):][:500]


def test_chest_styles_are_light_and_sit_before_the_chest_window_block():
    start = CSS.index("/* Сундук в конце полоски квеста")
    chest_window = CSS.index("Окно «Сундук» (открытие сундука месяца")
    assert start < chest_window, "иначе страж хвоста style.css (без filter/infinite/animation) их не охватит"
    block = CSS[start:chest_window]
    assert "filter" not in block and "infinite" not in block and "animation" not in block and "will-change" not in block and "backface" not in block
    for kind in ("wood", "blue", "gold"):
        assert f".chest-card[data-tone=\"{kind}\"]" in CSS or kind == "wood"
    assert ".quest-chest--blue{" in block and ".quest-chest--gold{" in block
    assert ".quest-chest.is-pop{transform:scale(.2);opacity:0}" in block


def test_chest_events_are_registered_for_the_analytics():
    from db.tracking import EVENT_LABELS
    from webapp.feature_map import FEATURES
    assert "квеста дня" in EVENT_LABELS["chest_opened"][1]
    quests = next(f for f in FEATURES if f.id == "quests")
    assert "POST /api/quests/{quest_key}/open" in quests.api and "chest_opened" in quests.events
