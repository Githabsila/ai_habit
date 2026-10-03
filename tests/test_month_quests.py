"""
Задания месяца (db/month_quests.py): прогресс = ежедневные задания, забранные
за текущий месяц; 4 сундука на 15/30/45/60. Заменили «идеальный месяц».
"""
import itertools
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

import db.month_quests as month_quests
from db import add_user, get_user, get_achievements, get_month_quests, claim_month_chest, MONTH_GOAL
from db.core import connect
from db.quests import QUEST_KEYS
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"

_uid_counter = itertools.count(600_000_000, 10)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid):
    add_user(uid, f"u{uid}", "Игрок")
    return uid


def _give_points(uid, n, month="current"):
    """n забранных ежедневных заданий в месяце (по умолчанию — текущем)."""
    key = month_quests.month_key(month_quests._today(uid)) if month == "current" else month
    conn = connect()
    for i in range(n):
        conn.execute(
            "INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) "
            "VALUES (?, ?, ?, 't', 1, 1, 5, 1)",
            (uid, f"{key}-{(i % 28) + 1:02d}", QUEST_KEYS[i // 28]),
        )
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# ПРОГРЕСС
# ---------------------------------------------------------------------------

def test_points_count_only_claimed_quests_of_this_month(uid):
    me = _user(uid)
    _give_points(me, 7)
    _give_points(me, 5, month="2020-01")               # другой месяц — не считается
    conn = connect()                                    # не забранный квест — тоже
    key = month_quests.month_key(month_quests._today(me))
    conn.execute(
        "INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) "
        "VALUES (?, ?, 'no_skip', 't', 1, 1, 5, 0)", (me, f"{key}-29"))
    conn.commit()
    conn.close()
    mq = get_month_quests(me)
    assert mq["points"] == 7 and mq["goal"] == MONTH_GOAL == 60


def test_chests_open_at_every_15_quests(uid):
    me = _user(uid)
    expected = {0: [], 14: [], 15: [15], 29: [15], 30: [15, 30], 59: [15, 30, 45], 60: [15, 30, 45, 60]}
    given = 0
    for points, reached in expected.items():
        _give_points_range(me, given, points)
        given = points
        mq = get_month_quests(me)
        assert [c["at"] for c in mq["chests"] if c["reached"]] == reached, points
        assert mq["claimable"] == len(reached)


def _give_points_range(uid, start, end):
    """Добавить задания с номерами [start, end) — чтобы наращивать прогресс по шагам."""
    key = month_quests.month_key(month_quests._today(uid))
    conn = connect()
    for i in range(start, end):
        conn.execute(
            "INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) "
            "VALUES (?, ?, ?, 't', 1, 1, 5, 1)",
            (uid, f"{key}-{(i % 28) + 1:02d}", QUEST_KEYS[i // 28]),
        )
    conn.commit()
    conn.close()


def test_card_shape(uid):
    me = _user(uid)
    mq = get_month_quests(me)
    assert mq["title"].startswith("Задания ") and re.match(r"\d{4}-\d{2}$", mq["month_key"])
    assert [(c["at"], c["final"]) for c in mq["chests"]] == [(15, False), (30, False), (45, False), (60, True)]
    # Сумма наград — те же 300 Adam Coin и 3 алмаза, что давал «идеальный месяц».
    assert sum(c["coins"] for c in mq["chests"]) == 300 and sum(c["diamonds"] for c in mq["chests"]) == 3
    assert mq["days_left"] >= 0


def test_days_left_and_title_follow_the_local_date(uid, monkeypatch):
    me = _user(uid)
    monkeypatch.setattr(month_quests, "_today", lambda user_id: date(2026, 10, 3))
    mq = get_month_quests(me)
    assert mq["title"] == "Задания октября" and mq["days_left"] == 28 and mq["month_key"] == "2026-10"


# ---------------------------------------------------------------------------
# СУНДУКИ
# ---------------------------------------------------------------------------

def test_cannot_open_a_closed_or_unknown_chest(uid):
    me = _user(uid)
    _give_points(me, 14)
    assert claim_month_chest(me, 15) == {"error": "not_reached"}
    assert claim_month_chest(me, 20) == {"error": "invalid_chest"}
    assert claim_month_chest(me, "abc") == {"error": "invalid_chest"}
    assert claim_month_chest(me, None) == {"error": "invalid_chest"}


def test_opening_a_chest_pays_coins_and_diamonds_once(uid):
    me = _user(uid)
    _give_points(me, 30)
    before = get_user(me)
    first = claim_month_chest(me, 15)
    assert first == {"ok": True, "at": 15, "coins": 25, "diamonds": 0, "badge": None}
    second = claim_month_chest(me, 30)
    assert second["coins"] == 50 and second["diamonds"] == 1
    after = get_user(me)
    assert after["xp"] - before["xp"] == 75 and after["diamonds"] - before["diamonds"] == 1
    assert claim_month_chest(me, 15) == {"error": "already_claimed"}
    assert get_user(me)["xp"] == after["xp"]
    mq = get_month_quests(me)
    assert [c["claimed"] for c in mq["chests"]] == [True, True, False, False]
    assert mq["claimable"] == 0


def test_chests_can_be_opened_in_any_order(uid):
    me = _user(uid)
    _give_points(me, 45)
    assert claim_month_chest(me, 45)["ok"]
    assert claim_month_chest(me, 15)["ok"]


def test_final_chest_gives_the_month_badge_once(uid):
    me = _user(uid)
    _give_points(me, 60)
    result = claim_month_chest(me, 60)
    assert result["coins"] == 150 and result["diamonds"] == 1
    title = result["badge"]
    assert re.match(r"Задания \w+ \d{4}$", title)
    assert [a["title"] for a in get_achievements(me)].count(title) == 1
    assert claim_month_chest(me, 60) == {"error": "already_claimed"}
    assert [a["title"] for a in get_achievements(me)].count(title) == 1


def test_a_new_month_starts_from_scratch(uid, monkeypatch):
    me = _user(uid)
    monkeypatch.setattr(month_quests, "_today", lambda user_id: date(2026, 10, 20))
    _give_points(me, 15, month="2026-10")
    assert claim_month_chest(me, 15)["ok"]
    monkeypatch.setattr(month_quests, "_today", lambda user_id: date(2026, 11, 2))
    mq = get_month_quests(me)
    assert mq["points"] == 0 and mq["claimable"] == 0
    assert claim_month_chest(me, 15) == {"error": "not_reached"}   # октябрьские задания не переходят
    _give_points(me, 15, month="2026-11")
    assert claim_month_chest(me, 15)["ok"]                           # а сундук ноября — свой


def test_unclaimed_chests_of_a_finished_month_expire(uid, monkeypatch):
    me = _user(uid)
    monkeypatch.setattr(month_quests, "_today", lambda user_id: date(2026, 10, 31))
    _give_points(me, 30, month="2026-10")
    assert get_month_quests(me)["claimable"] == 2
    monkeypatch.setattr(month_quests, "_today", lambda user_id: date(2026, 11, 1))
    assert get_month_quests(me)["claimable"] == 0
    assert claim_month_chest(me, 30) == {"error": "not_reached"}


def test_simultaneous_taps_pay_the_chest_once(uid):
    me = _user(uid)
    _give_points(me, 15)
    before = get_user(me)["xp"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: claim_month_chest(me, 15), range(6)))
    assert sum(1 for r in results if r.get("ok")) == 1
    assert get_user(me)["xp"] - before == 25


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def test_quests_route_includes_month_card(client, uid):
    me = _user(uid)
    data = await (await client.get("/api/quests", headers=_headers(me))).json()
    assert data["month_quests"]["goal"] == 60 and len(data["month_quests"]["chests"]) == 4


async def test_bootstrap_has_month_card_and_no_old_perfect_month(client, uid):
    me = _user(uid)
    data = await (await client.get("/api/bootstrap", headers=_headers(me))).json()
    assert data["month_quests"]["points"] == 0
    assert "monthly_progress" not in data


async def test_completing_a_habit_no_longer_reports_perfect_month_fields(client, uid):
    from db import add_habit
    me = _user(uid)
    habit = add_habit(me, "Зарядка")
    data = await (await client.post(f"/api/habits/{habit}/complete", headers=_headers(me))).json()
    assert "monthly_progress" not in data and "month_end_reward" not in data


async def test_claim_chest_route(client, uid):
    me = _user(uid)
    _give_points(me, 15)
    r = await client.post("/api/month-quests/claim", json={"at": 15}, headers=_headers(me))
    assert r.status == 200
    data = await r.json()
    assert data["reward"] == {"at": 15, "coins": 25, "diamonds": 0, "badge": None}
    assert data["month_quests"]["chests"][0]["claimed"] is True
    assert data["user"]["xp"] >= 25
    r = await client.post("/api/month-quests/claim", json={"at": 15}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "already_claimed"
    r = await client.post("/api/month-quests/claim", json={"at": 30}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "not_reached"
    r = await client.post("/api/month-quests/claim", json={"at": 7}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "invalid_chest"


async def test_claiming_a_daily_quest_returns_the_month_card(client, uid):
    from db import add_habit, complete_habit, get_daily_quests
    me = _user(uid)
    for title in ("Одна", "Две"):
        complete_habit(add_habit(me, title))
    quest = next(q for q in get_daily_quests(me) if q["completed"] and not q["claimed"])
    r = await client.post(f"/api/quests/{quest['key']}/claim", headers=_headers(me))
    assert r.status == 200
    assert (await r.json())["month_quests"]["points"] == 1


# ---------------------------------------------------------------------------
# РАЗМЕТКА И СКРИПТ
# ---------------------------------------------------------------------------

INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_month_card_lives_in_the_quests_modal_and_old_row_is_gone():
    modal = INDEX[INDEX.index('id="dailyQuestsOverlay"'):]
    for needle in ('id="monthCard"', 'id="monthCardTrack"', 'id="monthCardOpen"', 'id="monthCardHint"'):
        assert needle in modal, needle
    assert "monthlyProgressRow" not in INDEX and "Идеальных дней месяца" not in INDEX
    assert "monthlyProgress" not in APP_JS and "monthly_progress" not in APP_JS
    assert "month_end_reward" not in APP_JS


def test_script_renders_the_card_and_opens_chests():
    assert "function renderMonthCard" in APP_JS and "function applyMonthQuests" in APP_JS
    assert "renderMonthCard();" in APP_JS[APP_JS.index("function renderDailyQuests"):]
    assert '"/api/month-quests/claim"' in APP_JS
    # Ответы действий (квест забран, сундук открыт) обновляют карточку.
    patch = APP_JS[APP_JS.index("function applyActionPatch"):]
    assert "if (result.month_quests) applyMonthQuests(result.month_quests);" in patch


def test_month_card_styles_avoid_css_filter():
    block = CSS[CSS.index("Задания месяца (db/month_quests.py)"):]
    assert ".month-card{" in block and ".month-chest.is-ready" in block and ".month-card__open{" in block
    # filter на Android WebView даёт «квадраты» (см. кольцо уровня) — само
    # свойство не должно появляться (в комментарии слово можно упоминать).
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", block)
    assert ".streak-widget__monthly" not in CSS
