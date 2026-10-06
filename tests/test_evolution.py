"""
Эволюции ADAM и ранги (db/evolution.py): 12 постоянных эволюций по лучшей
серии, праздник «EVOLUTION N UNLOCKED» один раз на эволюцию, ранг виден в
профиле, у друзей, в подписках и в рейтинге.
"""
from datetime import timedelta
from pathlib import Path

import pytest

from db import (
    add_user, get_evolution, acknowledge_evolution, evolution_view, evolution_level_for_days,
    EVOLUTION_COUNT, EVOLUTION_LADDER, get_hero_state, get_player_profile, get_friends_overview,
    list_follows, make_friends, get_rating, clear_rating_cache,
)
from db.core import connect
from db.streak import local_today
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"


def _seed(uid_, streak=0, best=None):
    add_user(uid_, f"u{uid_}", "Игрок")
    conn = connect()
    conn.execute(
        "UPDATE users SET streak=?, best_streak=? WHERE telegram_id=?",
        (streak, streak if best is None else best, uid_),
    )
    conn.commit()
    conn.close()


def _set_streak(uid_, streak, best=None):
    conn = connect()
    conn.execute(
        "UPDATE users SET streak=?, best_streak=MAX(COALESCE(best_streak,0), ?) WHERE telegram_id=?",
        (streak, streak if best is None else best, uid_),
    )
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# =====================================
# Лестница
# =====================================

def test_ladder_is_consistent():
    assert EVOLUTION_COUNT == 12 == len(EVOLUTION_LADDER)
    assert [e["level"] for e in EVOLUTION_LADDER] == list(range(1, 13))
    days = [e["days"] for e in EVOLUTION_LADDER]
    assert days == sorted(set(days)), "пороги должны строго расти"
    assert len({e["name"] for e in EVOLUTION_LADDER}) == 12, "названия рангов не должны повторяться"
    assert EVOLUTION_LADDER[0]["name"] == "SPARK I" and EVOLUTION_LADDER[0]["days"] == 3
    assert EVOLUTION_LADDER[-1]["name"] == "LEGEND"


@pytest.mark.parametrize("days,level", [
    (0, 0), (2, 0), (3, 1), (6, 1), (7, 2), (13, 2), (14, 3), (30, 4), (31, 5),
    (99, 7), (100, 8), (127, 8), (179, 8), (180, 9), (364, 10), (365, 11), (499, 11), (500, 12), (9999, 12),
])
def test_level_for_days(days, level):
    assert evolution_level_for_days(days) == level


def test_view_example_from_the_design():
    """127 дней → 8 эволюций, ранг CONTROL III, следующая — на 180-й день."""
    view = evolution_view(127, 127)
    assert view["level"] == 8 and view["total"] == 12
    assert view["name"] == "CONTROL III" and view["family"] == "control"
    assert view["next"]["name"] == "APEX I" and view["next"]["days"] == 180
    assert view["next"]["days_left"] == 53
    assert 0 < view["percent"] < 100


def test_view_without_rank_and_at_the_top():
    zero = evolution_view(0, 0)
    assert zero["level"] == 0 and zero["name"] is None
    assert zero["next"]["name"] == "SPARK I" and zero["next"]["days_left"] == 3
    top = evolution_view(800, 800)
    assert top["level"] == 12 and top["name"] == "LEGEND"
    assert top["next"] is None and top["percent"] == 100


def test_evolutions_survive_a_broken_streak():
    """Лучшая серия 127, текущая сброшена: эволюции на месте, а до следующей
    считаем от текущей серии — её придётся набирать заново."""
    view = evolution_view(127, 0)
    assert view["level"] == 8 and view["name"] == "CONTROL III"
    assert view["streak"] == 0 and view["best_streak"] == 127
    assert view["next"]["days_left"] == 180 and view["percent"] == 0


# =====================================
# Праздник: один раз на эволюцию
# =====================================

def test_existing_progress_is_not_celebrated_retroactively(uid):
    _seed(uid, streak=127)
    evo = get_evolution(uid)
    assert evo["level"] == 8 and evo["pending"] is None and evo["seen_level"] == 8
    again = get_evolution(uid)
    assert again["pending"] is None


def test_new_evolution_is_pending_until_acknowledged(uid):
    _seed(uid, streak=2)
    assert get_evolution(uid)["pending"] is None  # строка создана с уровнем 0

    _set_streak(uid, 3)
    evo = get_evolution(uid)
    assert evo["level"] == 1 and evo["pending"] == 1 and evo["name"] == "SPARK I"
    assert get_evolution(uid)["pending"] == 1, "пока не подтвердили — праздник не пропадает"

    assert acknowledge_evolution(uid, 1)["pending"] is None
    assert get_evolution(uid)["pending"] is None

    _set_streak(uid, 7)
    assert get_evolution(uid)["pending"] == 2


def test_acknowledge_cannot_skip_ahead(uid):
    _seed(uid, streak=2)
    get_evolution(uid)
    _set_streak(uid, 3)
    result = acknowledge_evolution(uid, 12)  # выдуманный уровень
    assert result["seen_level"] == 1 and result["pending"] is None
    _set_streak(uid, 14)
    assert get_evolution(uid)["pending"] == 3, "будущие эволюции всё равно покажутся"
    assert acknowledge_evolution(uid, "мусор")["seen_level"] == 1
    assert acknowledge_evolution(uid, -5)["seen_level"] == 1


def test_jump_over_several_levels_celebrates_the_highest(uid):
    _seed(uid, streak=2)
    get_evolution(uid)
    _set_streak(uid, 31)
    evo = get_evolution(uid)
    assert evo["level"] == 5 and evo["pending"] == 5
    assert acknowledge_evolution(uid, 5)["pending"] is None


def test_unknown_user_is_harmless():
    assert get_evolution(1)["level"] == 0
    assert acknowledge_evolution(1, 3) is None


def test_hero_state_carries_the_evolution(uid):
    _seed(uid, streak=14)
    hero = get_hero_state(uid)
    evo = hero["evolution"]
    assert evo["level"] == 3 and evo["name"] == "FLOW I"
    assert len(evo["ladder"]) == 12 and evo["ladder"][0]["name"] == "SPARK I"


# =====================================
# Что видят другие
# =====================================

def test_profile_shows_rank_and_a_neutral_showcase(uid):
    viewer, target = uid, uid + 1
    _seed(viewer, streak=1)
    _seed(target, streak=0, best=127)  # серия сорвана, ранг остался
    profile = get_player_profile(viewer, target)
    assert profile["evolution"]["name"] == "CONTROL III" and profile["evolution"]["level"] == 8
    # Облик — по достигнутому: «долгий перерыв/срыв» чужим не показываем.
    assert profile["showcase"]["key"] == "peak"
    assert profile["showcase"]["image"].startswith("/static/assets/hero/peak.webp")


def test_rank_in_friends_follows_and_rating(uid):
    me, friend = uid, uid + 1
    _seed(me, streak=5)
    _seed(friend, streak=60, best=75)
    assert make_friends(me, friend)
    friends = get_friends_overview(me)["friends"]
    assert [f["evo"] for f in friends] == [7]  # CONTROL II
    rows = list_follows(me, "following")
    assert [r["evo"] for r in rows] == [7]
    clear_rating_cache()
    _league, board = get_rating(friend, limit=50)
    mine = [r for r in board if r["telegram_id"] == friend]
    assert mine and mine[0]["best_streak"] == 75


# =====================================
# HTTP
# =====================================

async def test_bootstrap_and_seen_route(client, uid):
    _seed(uid, streak=2)
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["hero"]["evolution"]["level"] == 0 and state["hero"]["evolution"]["pending"] is None

    _set_streak(uid, 3)
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["hero"]["evolution"]["pending"] == 1

    r = await client.post("/api/evolution/seen", json={"level": 1}, headers=_headers(uid))
    assert r.status == 200
    assert (await r.json())["evolution"]["pending"] is None
    state = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert state["hero"]["evolution"]["pending"] is None


async def test_seen_route_guards(client, uid):
    assert (await client.post("/api/evolution/seen", json={"level": 1})).status == 401
    _seed(uid, streak=1)
    bad = await client.post("/api/evolution/seen", data="не json", headers=_headers(uid))
    assert bad.status == 400
    weird = await client.post("/api/evolution/seen", json=[1, 2], headers=_headers(uid))
    assert weird.status == 400
    ok = await client.post("/api/evolution/seen", json={"level": "x"}, headers=_headers(uid))
    assert ok.status == 200 and (await ok.json())["evolution"]["seen_level"] == 0


async def test_habit_complete_response_has_pending_evolution(client, uid):
    """Третий день подряд отметил привычку — ответ на отметку сразу несёт
    эволюцию (pending), фронт покажет праздник без перезагрузки."""
    _seed(uid, streak=2)
    yesterday = str(local_today(uid) - timedelta(days=1))
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', 2)",
        (uid, yesterday),
    )
    conn.commit()
    conn.close()
    assert get_evolution(uid)["pending"] is None  # строка создана с уровнем 0

    r = await client.post("/api/habits", json={"title": "Бег"}, headers=_headers(uid))
    habit_id = (await r.json())["habit"]["id"]
    done = await client.post(f"/api/habits/{habit_id}/complete", headers=_headers(uid))
    assert done.status == 200
    evo = (await done.json())["hero"]["evolution"]
    assert evo["streak"] == 3 and evo["level"] == 1 and evo["pending"] == 1


# =====================================
# Разметка и подключение фронтенда
# =====================================

def _static(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_markup_has_the_celebration_the_sheet_and_the_hero_row():
    index = _static("index.html")
    for needle in (
        'id="evolutionOverlay"', 'id="evolutionImg"', 'id="evolutionSparks"', 'id="evolutionKicker"',
        'id="evolutionRank"', 'id="evolutionContinue"', 'id="evolutionToProfile"',
        'id="evolutionSheet"', 'id="evolutionSheetBody"', 'id="evolutionSheetBackdrop"',
        'id="heroEvoRow"',
    ):
        assert needle in index, needle
    assert index.count('class="evo-stream evo-stream--') == 2, "два голубых потока"
    # праздник — внутри карточки героя нет, он поверх всего (после лайтбокса героя)
    assert index.index('id="heroLightbox"') < index.index('id="evolutionOverlay"')


def test_script_is_wired_up():
    js = _static("app.js")
    assert "initEvolution();" in js
    assert "renderHeroEvoRow(hero.evolution);" in js and "queueEvolutionCelebration();" in js
    assert '"/api/evolution/seen"' in js
    # Праздник встаёт в очередь за остальными окнами и сам их задерживает.
    assert '"heroLightbox", "evolutionOverlay"' in js
    assert "celebrationOverlayOpen({ skipEvolution: true })" in js
    # Звуки интерфейса глохнут на время праздника.
    assert "if (evolutionSilence) return;" in js
    # Ранг виден в списках и в чужом профиле.
    assert js.count("evoChipHtml(") >= 4 and "userEvolutionCardHtml(p)" in js
    # Вспышка и потоки — только в полном варианте; для слабых устройств есть короткий.
    assert 'is-lite' in js and "heroMotionAllowed()" in js


def test_every_family_has_an_icon_and_a_colour():
    from db.evolution import FAMILIES

    js = _static("app.js")
    css = _static("style.css")
    for family in FAMILIES:
        assert f"{family}: '<" in js, f"нет значка семейства {family}"
        assert f'[data-evo-family="{family}"]{{--evo-accent' in css, f"нет цвета семейства {family}"


def test_celebration_css_only_uses_cheap_animations():
    """Без blur/filter на новом экране и без бесконечных анимаций: на слабых
    Android WebView они уже ломали отрисовку."""
    css = _static("style.css")
    start = css.index("/* Праздник «EVOLUTION N UNLOCKED» */")
    block = css[start:]
    assert "backdrop-filter" not in block and "filter:" not in block
    assert "infinite" not in block
    for name in ("evoRise", "evoRing", "evoGather", "evoFlash"):
        assert f"@keyframes {name}" in block
    assert ".evo-overlay.is-lite .evo-flash" in block, "короткий вариант без вспышки"


# =====================================
# Приватность и маршрут профиля
# =====================================

def test_exact_best_streak_stays_in_the_overview_tier(uid):
    from db import follow

    viewer, target = uid, uid + 1
    _seed(viewer, streak=1)
    _seed(target, streak=0, best=127)
    stranger = get_player_profile(viewer, target)
    assert stranger["evolution"]["level"] == 8 and stranger["evolution"]["best_streak"] is None
    assert "overview" not in stranger
    follow(viewer, target)
    subscriber = get_player_profile(viewer, target)
    assert subscriber["evolution"]["best_streak"] == 127 and subscriber["overview"]["best_streak"] == 127


async def test_profile_route_returns_rank_and_showcase(client, uid):
    viewer, target = uid, uid + 1
    _seed(viewer, streak=1)
    _seed(target, streak=40, best=40)
    r = await client.get(f"/api/users/{target}/profile", headers=_headers(viewer))
    assert r.status == 200
    data = await r.json()
    assert data["evolution"]["name"] == "FLOW III"
    assert data["showcase"]["key"] == "peak" and data["showcase"]["image"]
