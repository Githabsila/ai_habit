"""
Админка: «Лаборатория» (демо-состояния парного задания без записи в базу) и сырой рейтинг пар.
Только для админов; приложение открывает демо по ?lab=<ключ> и блокирует все действия на карточке.
"""
import itertools
from pathlib import Path

import pytest

from db import add_user, get_pair_quest
from db.pair_lab import SCENARIOS, build_scenario, lab_scenarios
from db.pair_quests import PAIR_GOAL, get_pair_rating
from tests.conftest import sign_init_data
from tests.test_pair_habits import _mark, _quest, _window
from tests.test_pair_quests import _clean, _pair, _sql, _start

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
ADMIN_HTML = (STATIC / "admin_panel.html").read_text(encoding="utf-8")

_uid_counter = itertools.count(760_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


# --- сценарии лаборатории -----------------------------------------------------------------------------

def test_every_scenario_builds_and_unknown_ones_do_not():
    keys = [s["key"] for s in lab_scenarios()]
    assert keys == [k for k, _ in SCENARIOS] and len(set(keys)) == len(keys)
    for key in keys:
        payload = build_scenario(key)
        assert payload["goal"] == PAIR_GOAL and "quest" in payload and "incoming" in payload, key
    assert build_scenario("nope") is None


def test_scenarios_show_the_states_they_are_named_after():
    quest = lambda key: build_scenario(key)["quest"]
    assert build_scenario("none")["quest"] is None and build_scenario("none")["can_choose"] is True
    assert build_scenario("invite_out")["outgoing"]["to"]["first_name"] == "Александр"
    assert build_scenario("invite_in")["incoming"][0]["from"]["first_name"] == "Александр"
    assert quest("scheduled")["phase"] == "scheduled" and quest("scheduled")["starts_in"] == 1
    day1 = quest("day1")
    assert day1["phase"] == "active" and day1["progress"] == 3 and day1["today"]["me"] == 2 and day1["today"]["partner"] == 1
    assert quest("mid")["progress"] == 15 and quest("mid")["pace"] == "ok"
    assert quest("tight")["pace"] == "tight" and quest("lost")["pace"] == "lost"
    hold = quest("hold_partner")
    assert (hold["progress"], hold["waiting_for"], hold["today"]["held"]) == (19, "partner", 1)
    assert quest("hold_me")["waiting_for"] == "me" and quest("hold_me")["progress"] == 19
    done = quest("done")
    assert done["phase"] == "completed" and done["claimable"] is True and done["progress"] == PAIR_GOAL
    claimed = build_scenario("claimed")
    assert claimed["quest"]["claimed"] is True and claimed["choose_from_day"]


def test_scenarios_write_nothing_to_the_database(uid):
    add_user(uid, "u", "Аня")
    before = get_pair_quest(uid)
    for key, _ in SCENARIOS:
        build_scenario(key)
    assert get_pair_quest(uid) == before


# --- рейтинг пар ----------------------------------------------------------------------------------------

def test_pair_rating_orders_by_completed_then_speed_and_shows_the_current_quest(uid):
    a, b, done_quest = _quest(uid)
    _window(done_quest, start_offset=-3)
    _mark(a, done_quest, [3, 3, 3, 1]); _mark(b, done_quest, [3, 3, 3, 1])
    assert get_pair_quest(a)["quest"]["phase"] == "completed"
    c, d, live_quest = _quest(uid + 10)
    _mark(c, live_quest, [2, 1, 1]); _mark(d, live_quest, [1, 1, 0])
    rating = get_pair_rating(limit=500)
    ids = lambda entry: {u["telegram_id"] for u in entry["users"]}
    first = next(e for e in rating if ids(e) == {a, b})
    second = next(e for e in rating if ids(e) == {c, d})
    assert first["rank"] < second["rank"]
    assert first["completed"] == 1 and first["fastest_days"] == 4 and first["habits_total"] == PAIR_GOAL and first["current"] is None
    assert second["completed"] == 0 and second["current"]["progress"] == 6 and second["current"]["goal"] == PAIR_GOAL
    assert {u["first_name"] for u in first["users"]} == {"Анна", "Борис"}


def test_pair_rating_counts_each_pair_once_across_several_quests(uid):
    a, b, quest_id = _quest(uid)
    _window(quest_id, start_offset=-3)
    _mark(a, quest_id, [3, 3, 3, 1]); _mark(b, quest_id, [3, 3, 3, 1])
    get_pair_quest(a)
    _sql("UPDATE pair_quests SET status='expired' WHERE id=?", (quest_id,))                # условно «неудачное»
    entries = [e for e in get_pair_rating(limit=500) if {u["telegram_id"] for u in e["users"]} == {a, b}]
    assert len(entries) == 1 and entries[0]["quests"] == 1 and entries[0]["completed"] == 0


# --- маршруты -------------------------------------------------------------------------------------------

async def test_admin_lab_and_rating_routes_are_for_admins_only(client, uid):
    add_user(uid, "u", "Аня")
    for path in ("/api/admin/lab/scenarios", "/api/admin/lab/pair/mid", "/api/admin/pair-rating"):
        assert (await client.get(path)).status == 401
        assert (await client.get(path, headers=_headers(uid))).status == 403


async def test_admin_gets_scenarios_demo_payload_and_rating(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin_id = uid + 50
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin_id})
    add_user(admin_id, "adm", "Админ")
    listing = await (await client.get("/api/admin/lab/scenarios", headers=_headers(admin_id))).json()
    assert [s["key"] for s in listing["scenarios"]][:2] == ["none", "invite_out"] and len(listing["scenarios"]) == len(SCENARIOS)
    demo = await client.get("/api/admin/lab/pair/hold_partner", headers=_headers(admin_id))
    body = await demo.json()
    assert demo.status == 200 and body["pair_quest"]["quest"]["waiting_for"] == "partner"
    missing = await client.get("/api/admin/lab/pair/nope", headers=_headers(admin_id))
    assert missing.status == 404
    rating = await client.get("/api/admin/pair-rating", headers=_headers(admin_id))
    assert rating.status == 200 and isinstance((await rating.json())["pairs"], list)


# --- панель и приложение ---------------------------------------------------------------------------------

def test_admin_panel_has_the_lab_and_the_pair_rating_at_the_bottom():
    assert ADMIN_HTML.index("Кому выдать медаль") < ADMIN_HTML.index("Тесты и нововведения") < ADMIN_HTML.index("Рейтинг пар (тест)")
    assert 'href="/?lab=' in ADMIN_HTML and "/api/admin/lab/scenarios" in ADMIN_HTML and "/api/admin/pair-rating" in ADMIN_HTML
    assert 'id="labBox"' in ADMIN_HTML and 'id="pairRatingBox"' in ADMIN_HTML


def test_app_opens_the_lab_only_for_admins_and_blocks_every_action():
    init = APP_JS[APP_JS.index("async function initLabMode()"):][:700]
    assert 'new URLSearchParams(location.search).get("lab")' in init and "!state?.user?.is_admin" in init
    assert "initLabMode();" in APP_JS[APP_JS.index("postBootstrapInitDone = true;"):][:120]
    action = APP_JS[APP_JS.index("async function pairCardAction("):][:700]
    assert action.index('act === "info"') < action.index("if (labMode)") < action.index('act === "choose"')
    assert "Демо: действие не выполняется" in action
    assert "if (!state || labMode) return;" in APP_JS, "реальные ответы сервера не затирают демо"
    assert "if (labMode?.payload) state.pair_quest = labMode.payload;" in APP_JS
    assert ".lab-bar{" in CSS and 'data-lab="prev"' in APP_JS and 'data-lab="next"' in APP_JS
