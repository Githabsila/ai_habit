"""
Персональный рейтинг ("лиги"): каждый пользователь видит только игроков
из своей лиги (streak-диапазон, db/leagues.py::RATING_LEAGUES). Порог
входа в любую лигу — streak>=2 — это же исключает тех, кто попробовал
один раз и не вернулся (streak падает до 0 при ближайшем rollover,
db/streak.py), и возвращает вернувшихся только после 2 дней новой серии.
"""
import pytest

from db import (
    add_user, get_rating, clear_rating_cache,
    get_rating_league, get_rating_league_for_viewer,
    RATING_LEAGUES, RATING_LEAGUE_MIN_STREAK,
)
from db.core import connect

from tests.conftest import sign_init_data


@pytest.fixture(autouse=True)
def _fresh_rating_cache():
    # Кэш get_rating() — на лигу и модульный (весь процесс), а не per-request
    # (см. db/users.py::_RATING_CACHE) — без сброса тест, запущенный через
    # < 5с после другого теста с тем же диапазоном streak, получил бы его
    # устаревшие данные без только что добавленных пользователей.
    clear_rating_cache()
    yield


def _set_streak(uid_, streak):
    conn = connect()
    conn.execute("UPDATE users SET streak=? WHERE telegram_id=?", (streak, uid_))
    conn.commit()
    conn.close()


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# db.leagues.get_rating_league / get_rating_league_for_viewer
# =====================================

def test_get_rating_league_none_below_threshold():
    assert get_rating_league(0) is None
    assert get_rating_league(1) is None
    assert RATING_LEAGUE_MIN_STREAK == 2


def test_get_rating_league_boundaries():
    assert get_rating_league(2)["name"] == "🌱 Новички"
    assert get_rating_league(3)["name"] == "🌱 Новички"
    assert get_rating_league(4)["name"] == "🔥 Ученики"
    assert get_rating_league(13)["name"] == "⚡ В темпе"
    assert get_rating_league(14)["name"] == "🥈 Продвинутые"
    assert get_rating_league(60)["name"] == "🥇 Мастера"
    assert get_rating_league(61)["name"] == "👑 Легенды"
    assert get_rating_league(500)["name"] == "👑 Легенды"


def test_rating_leagues_cover_every_streak_contiguously():
    """Не должно быть дыр/пересечений между соседними лигами."""
    for (lo, hi, _name), (next_lo, _next_hi, _next_name) in zip(RATING_LEAGUES, RATING_LEAGUES[1:]):
        assert hi is not None
        assert next_lo == hi + 1
    assert RATING_LEAGUES[0][0] == RATING_LEAGUE_MIN_STREAK
    assert RATING_LEAGUES[-1][1] is None


def test_get_rating_league_for_viewer_falls_back_to_lowest():
    league = get_rating_league_for_viewer(0)
    assert league["name"] == "🌱 Новички"
    assert league == get_rating_league_for_viewer(1)


def test_get_rating_league_for_viewer_matches_own_league_once_qualified():
    assert get_rating_league_for_viewer(20)["name"] == "🥈 Продвинутые"


# =====================================
# db.users.get_rating — сегментация
# =====================================

def test_viewer_below_threshold_previews_newcomers_without_own_row(uid):
    add_user(uid, "u", "Test")  # свежий пользователь, streak=0
    league, rows = get_rating(uid, limit=1000)
    assert league["name"] == "🌱 Новички"
    assert all(r["telegram_id"] != uid for r in rows)


def test_viewer_at_threshold_appears_in_newcomers(uid):
    add_user(uid, "u", "Test")
    _set_streak(uid, 2)
    league, rows = get_rating(uid, limit=1000)
    assert league["name"] == "🌱 Новички"
    assert any(r["telegram_id"] == uid for r in rows)


def test_rating_only_includes_same_league(uid):
    # Большие несовпадающие смещения — не uid+1/uid+2: счётчик fixture'ы
    # `uid` общий на весь тестовый прогон, и маленькое смещение в одном
    # тесте может случайно совпасть со значением uid, выданным СЛЕДУЮЩЕМУ
    # тесту тем же счётчиком.
    other_league_uid = uid + 100_000
    same_league_uid = uid + 200_000
    add_user(uid, "u", "Test")
    add_user(other_league_uid, "u2", "Other")
    add_user(same_league_uid, "u3", "Same")
    _set_streak(uid, 20)               # Продвинутые (14-30)
    _set_streak(other_league_uid, 5)   # Ученики (4-7) — должен быть исключён
    _set_streak(same_league_uid, 25)   # Продвинутые — должен попасть

    _league, rows = get_rating(uid, limit=1000)
    ids = {r["telegram_id"] for r in rows}
    assert uid in ids
    assert same_league_uid in ids
    assert other_league_uid not in ids


def test_rating_excludes_banned_users(uid):
    banned_uid = uid + 100_000
    add_user(uid, "u", "Test")
    add_user(banned_uid, "b", "Banned")
    _set_streak(uid, 5)
    _set_streak(banned_uid, 5)
    conn = connect()
    conn.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (banned_uid,))
    conn.commit()
    conn.close()

    _league, rows = get_rating(uid, limit=1000)
    assert all(r["telegram_id"] != banned_uid for r in rows)


def test_rating_ordered_by_streak_then_xp_within_league(uid):
    lower_uid = uid + 100_000
    add_user(uid, "u", "Test")
    add_user(lower_uid, "u2", "Lower")
    _set_streak(uid, 10)
    _set_streak(lower_uid, 9)

    _league, rows = get_rating(uid, limit=1000)
    ranked_ids = [r["telegram_id"] for r in rows if r["telegram_id"] in (uid, lower_uid)]
    assert ranked_ids == [uid, lower_uid]


# =====================================
# РОУТ /api/bootstrap-secondary?section=rating
# =====================================

async def test_rating_route_includes_league_and_scoped_leaderboard(client, uid):
    other_uid = uid + 100_000
    add_user(uid, "u", "Test")
    add_user(other_uid, "u2", "Other")
    _set_streak(uid, 20)
    _set_streak(other_uid, 5)  # другая лига — не должен попасть в ответ

    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=rating", headers=headers)
    assert r.status == 200
    body = await r.json()

    assert body["rating_league"]["name"] == "🥈 Продвинутые"
    ids = {row["telegram_id"] for row in body["leaderboard"]}
    assert uid in ids
    assert other_uid not in ids


async def test_rating_route_new_user_previews_newcomers_league(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=rating", headers=headers)
    body = await r.json()
    assert body["rating_league"]["name"] == "🌱 Новички"
