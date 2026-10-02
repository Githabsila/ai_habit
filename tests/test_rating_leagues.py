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
from db.leagues import MIN_RATING_LEAGUE_SIZE
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


@pytest.fixture(autouse=True)
def _isolated_streaks():
    # БД общая на весь прогон, а размер лиги теперь влияет на результат
    # (см. MIN_RATING_LEAGUE_SIZE) — пользователи с серией из чужих тестов
    # исказили бы подсчёт, поэтому обнуляем серии перед каждым тестом.
    conn = connect()
    conn.execute("UPDATE users SET streak=0")
    conn.commit()
    conn.close()
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


def test_rating_only_includes_same_league_when_it_is_big_enough(uid):
    # Большие несовпадающие смещения — не uid+1/uid+2: счётчик fixture'ы
    # `uid` общий на весь тестовый прогон, и маленькое смещение в одном
    # тесте может случайно совпасть со значением uid, выданным СЛЕДУЮЩЕМУ
    # тесту тем же счётчиком.
    add_user(uid, "u", "Test")
    _set_streak(uid, 20)               # Продвинутые (14-30)
    other_league_uid = _make_peers(uid, 1, [5])[0]   # Ученики (4-7) — должен быть исключён
    same_league = _make_peers(uid, 2, [25] * (MIN_RATING_LEAGUE_SIZE - 1))  # Продвинутые — должны попасть

    league, rows = get_rating(uid, limit=1000)
    ids = {r["telegram_id"] for r in rows}
    assert uid in ids
    assert set(same_league) <= ids
    assert other_league_uid not in ids
    assert "merged_from" not in league


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
# Мало игроков в лиге -> подмешиваем лиги ниже
# (жалоба: серия стала 31, лига "Мастера" 31-60 опустела, "все пропали")
# =====================================

def _make_peers(uid_, group, streaks):
    """Игроки для теста с заданными сериями. id уникальны для КАЖДОГО теста
    (uid * 1000 + группа * 100 + i), а не uid + смещение: счётчик `uid`
    общий на прогон, и пересекающиеся диапазоны пиров соседних тестов
    переносили бы забаненных/чужие данные из предыдущего теста в следующий."""
    ids = []
    for i, st in enumerate(streaks):
        peer = uid_ * 1000 + group * 100 + i
        add_user(peer, f"p{i}", f"Peer{i}")
        _set_streak(peer, st)
        ids.append(peer)
    return ids


def test_small_league_is_widened_with_lower_leagues(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)  # Мастера (31-60) — в одиночку
    # 14-30: двое, 8-13: двое -> суммарно 5 (я + 4) — на этом расширение
    # останавливается, лига 4-7 уже не нужна.
    close = _make_peers(uid, 1, [20, 18, 10, 9])
    too_low = _make_peers(uid, 2, [5])[0]

    league, rows = get_rating(uid, limit=1000)

    ids = [r["telegram_id"] for r in rows]
    assert league["name"] == "🥇 Мастера"          # лига зрителя не меняется
    assert league["merged_from"] == "⚡ В темпе"
    assert league["merged_min_streak"] == 8
    assert ids[0] == uid                            # сам он по серии первый
    assert set(close) <= set(ids)
    assert too_low not in ids


def test_widening_stops_at_first_lower_league_that_is_enough(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)
    _make_peers(uid, 1, [20, 19, 18, 17])  # одна лига ниже даёт нужные 5
    deeper = _make_peers(uid, 2, [9])[0]    # на лигу глубже — не нужен

    league, rows = get_rating(uid, limit=1000)

    assert league["merged_from"] == "🥈 Продвинутые"
    assert deeper not in {r["telegram_id"] for r in rows}


def test_league_big_enough_is_not_widened(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)
    _make_peers(uid, 1, [40] * (MIN_RATING_LEAGUE_SIZE - 1))
    lower = _make_peers(uid, 2, [20])[0]

    league, rows = get_rating(uid, limit=1000)

    assert "merged_from" not in league
    assert lower not in {r["telegram_id"] for r in rows}


def test_widening_never_pulls_in_higher_leagues(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 5)  # Ученики (4-7)
    higher = _make_peers(uid, 1, [40])[0]

    league, rows = get_rating(uid, limit=1000)

    assert higher not in {r["telegram_id"] for r in rows}
    assert league["merged_from"] == "🌱 Новички"


def test_lowest_league_is_never_widened(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 2)

    league, rows = get_rating(uid, limit=1000)

    assert league["name"] == "🌱 Новички"
    assert "merged_from" not in league
    assert [r["telegram_id"] for r in rows] == [uid]


def test_widened_league_info_survives_the_cache(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)
    _make_peers(uid, 1, [20, 19, 18, 17])

    first, _ = get_rating(uid, limit=1000)
    second, _ = get_rating(uid, limit=1000)  # из кэша

    assert first["merged_from"] == second["merged_from"] == "🥈 Продвинутые"


def test_widening_ignores_banned_users_when_counting(uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)
    banned = _make_peers(uid, 1, [40] * MIN_RATING_LEAGUE_SIZE)
    conn = connect()
    conn.executemany("UPDATE users SET banned=1 WHERE telegram_id=?", [(b,) for b in banned])
    conn.commit()
    conn.close()

    league, rows = get_rating(uid, limit=1000)

    # Забаненные не считаются: в лиге по факту один зритель — расширяемся
    # вниз, а самих забаненных в рейтинге нет.
    assert league.get("merged_from")
    assert not ({r["telegram_id"] for r in rows} & set(banned))


# =====================================
# РОУТ /api/bootstrap-secondary?section=rating
# =====================================

async def test_rating_route_includes_league_and_scoped_leaderboard(client, uid):
    add_user(uid, "u", "Test")
    _set_streak(uid, 20)
    # В лиге зрителя должно быть достаточно игроков, иначе к ней подмешаются
    # лиги ниже (см. MIN_RATING_LEAGUE_SIZE) — а здесь проверяем именно
    # разделение по лигам.
    _make_peers(uid, 1, [25] * (MIN_RATING_LEAGUE_SIZE - 1))
    other_uid = _make_peers(uid, 2, [5])[0]  # другая лига — не должен попасть в ответ

    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=rating", headers=headers)
    assert r.status == 200
    body = await r.json()

    assert body["rating_league"]["name"] == "🥈 Продвинутые"
    assert "merged_from" not in body["rating_league"]
    ids = {row["telegram_id"] for row in body["leaderboard"]}
    assert uid in ids
    assert other_uid not in ids


async def test_rating_route_new_user_previews_newcomers_league(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=rating", headers=headers)
    body = await r.json()
    assert body["rating_league"]["name"] == "🌱 Новички"


async def test_rating_route_reports_widened_league(client, uid):
    add_user(uid, "u", "Me")
    _set_streak(uid, 35)
    _make_peers(uid, 1, [20, 19, 18, 17])

    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=rating", headers=headers)
    body = await r.json()

    assert body["rating_league"]["name"] == "🥇 Мастера"
    assert body["rating_league"]["merged_from"] == "🥈 Продвинутые"
    assert len(body["leaderboard"]) == 5
