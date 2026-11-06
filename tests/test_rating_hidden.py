"""
«Скрыть меня из рейтинга» (юридический минимум, 06.11): человека не видно в рейтинге лиги и сезона у других, сам рейтинг он видит;
переключатель в настройках, значение приходит в bootstrap.
"""
from db import add_user
from db.core import connect
from db.seasons import clear_season_leaderboard_cache, get_season_leaderboard
from db.users import _RATING_CACHE, get_rating
from tests.conftest import sign_init_data


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _player(user_id, streak):
    add_user(user_id, f"p{user_id}", "Игрок")
    _sql("UPDATE users SET streak=?, xp=100, access_status='approved' WHERE telegram_id=?", (streak, user_id))


async def test_hidden_player_disappears_from_others_rating_and_comes_back(client, uid):
    hidden, viewer = uid, uid + 1
    _player(hidden, 40)
    _player(viewer, 40)
    _RATING_CACHE.clear()
    assert hidden in [r["telegram_id"] for r in get_rating(viewer, limit=1000)[1]]

    headers = {"Authorization": f"tma {sign_init_data(hidden)}"}
    response = await client.post("/api/settings/rating-hidden", json={"hidden": True}, headers=headers)
    assert (await response.json()) == {"ok": True, "hidden": True}
    assert hidden not in [r["telegram_id"] for r in get_rating(viewer, limit=1000)[1]]
    boot = await (await client.get("/api/bootstrap", headers=headers)).json()
    assert boot["settings"]["rating_hidden"] is True

    await client.post("/api/settings/rating-hidden", json={"hidden": False}, headers=headers)
    assert hidden in [r["telegram_id"] for r in get_rating(viewer, limit=1000)[1]]


def test_hidden_player_is_not_in_the_season_list(uid):
    from db.users import set_rating_hidden
    _player(uid, 3)
    _sql("INSERT INTO statistics(user_id, stat_date, gained_xp) VALUES (?, date('now'), 500)", (uid,))
    clear_season_leaderboard_cache()
    assert uid in [r.get("telegram_id", r.get("user_id")) for r in get_season_leaderboard(limit=100000)]
    set_rating_hidden(uid, True)
    assert uid not in [r.get("telegram_id", r.get("user_id")) for r in get_season_leaderboard(limit=100000)]
