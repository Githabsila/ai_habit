"""
Подписки и подписчики в шапке (16.10, п.12): счётчики всегда на виду, по тапу — списки; метка «новые подписчики» на счётчике; в списках —
подборка активных игроков (знакомиться с теми, кого не знаешь) и приглашение друга, пока никто не подписан.
"""
import itertools
from pathlib import Path

import pytest

from db import add_user, follow, unfollow
from db.core import connect
from db.follows import follow_summary, list_follows, mark_followers_seen, new_followers_count
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(845_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _person(user_id, name="Игрок"):
    add_user(user_id, f"u{user_id}", name)
    return user_id


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}", "Content-Type": "application/json"}


def _follow_at(follower, followee, seconds_after):
    """Подписка с заданным временем: «новый» определяется сравнением со временем последнего открытия списка."""
    follow(follower, followee)
    _sql("UPDATE follows SET created_at=datetime('now', ?) WHERE follower_id=? AND followee_id=?", (f"{seconds_after:+d} seconds", follower, followee))


# ------------------------------------------------------------------------------------------------ сервер

def test_the_first_look_remembers_now_so_old_followers_are_not_new(uid):
    me = _person(uid)
    for i in (1, 2):
        follow(_person(uid + i), me)
    assert new_followers_count(me) == 0
    assert follow_summary(me) == {"followers": 2, "following": 0, "new": 0}


def test_a_follower_who_arrives_later_is_new_until_the_list_is_opened(uid):
    me = _person(uid)
    assert new_followers_count(me) == 0                                      # запомнили «сейчас»
    _sql("UPDATE users SET followers_seen_at=datetime('now','-1 hour') WHERE telegram_id=?", (me,))
    _follow_at(_person(uid + 1), me, -60)
    _follow_at(_person(uid + 2), me, -30)
    assert new_followers_count(me) == 2 and follow_summary(me)["new"] == 2
    flags = {u["telegram_id"]: u["is_new"] for u in list_follows(me, "followers")}
    assert flags == {uid + 1: True, uid + 2: True}
    assert all(u["is_new"] is False for u in list_follows(me, "following"))
    mark_followers_seen(me)
    assert new_followers_count(me) == 0 and not any(u["is_new"] for u in list_follows(me, "followers"))
    _follow_at(_person(uid + 3), me, 5)
    assert new_followers_count(me) == 1


def test_a_banned_or_unfollowed_person_is_not_counted_as_new(uid):
    me, banned, left = _person(uid), _person(uid + 1), _person(uid + 2)
    new_followers_count(me)
    _sql("UPDATE users SET followers_seen_at=datetime('now','-1 hour') WHERE telegram_id=?", (me,))
    _follow_at(banned, me, -10)
    _follow_at(left, me, -10)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (banned,))
    unfollow(left, me)
    assert new_followers_count(me) == 0


async def test_bootstrap_and_friends_carry_the_header_counters(client, uid):
    me = _person(uid)
    other = _person(uid + 1)
    follow(me, other)
    boot = await (await client.get("/api/bootstrap", headers=_headers(me))).json()
    assert boot["user"]["follow_counts"] == {"followers": 0, "following": 1, "new": 0}
    _sql("UPDATE users SET followers_seen_at=datetime('now','-1 hour') WHERE telegram_id=?", (me,))
    _follow_at(other, me, -5)
    friends = await (await client.get("/api/friends", headers=_headers(me))).json()
    assert friends["new_followers"] == 1 and friends["counts"] == {"followers": 1, "following": 1}
    boot = await (await client.get("/api/bootstrap", headers=_headers(me))).json()
    assert boot["user"]["follow_counts"]["new"] == 1


async def test_opening_followers_marks_them_seen_through_the_api(client, uid):
    me, fan = _person(uid), _person(uid + 1)
    new_followers_count(me)
    _sql("UPDATE users SET followers_seen_at=datetime('now','-1 hour') WHERE telegram_id=?", (me,))
    _follow_at(fan, me, -5)
    listing = await (await client.get("/api/follows?kind=followers", headers=_headers(me))).json()
    assert listing["new_followers"] == 1 and listing["users"][0]["is_new"] is True
    seen = await client.post("/api/follows/seen", headers=_headers(me))
    assert seen.status == 200 and (await seen.json())["follow_counts"]["new"] == 0
    again = await (await client.get("/api/follows?kind=followers", headers=_headers(me))).json()
    assert again["new_followers"] == 0 and again["users"][0]["is_new"] is False


async def test_seen_route_needs_a_login(client):
    assert (await client.post("/api/follows/seen")).status == 401


# ------------------------------------------------------------------------------------------------ разметка и приложение

def test_the_header_has_both_counters_inside_the_player_card():
    header = INDEX[INDEX.index('<header class="player-card">'):INDEX.index("</header>")]
    for needle in ('id="playerFollow"', 'data-follow-open="following"', 'data-follow-open="followers"', 'id="playerFollowingCount"',
                   'id="playerFollowersCount"', 'id="playerFollowersNew"'):
        assert needle in header, needle
    assert header.index('id="xpLabel"') < header.index('id="playerFollow"')                 # под полосой опыта, внутри блока с информацией


def test_the_app_draws_syncs_and_opens_the_counters():
    assert "function renderFollowCounters()" in APP and "renderFollowCounters();" in APP.split("function renderPlayerCard()")[1].split("renderAiNudgeBadge();")[0]
    assert "syncFollowCounts(friendsData.counts, friendsData.new_followers)" in APP
    assert "syncFollowCounts(followListData.counts, followListData.new_followers)" in APP
    assert 'document.getElementById("playerFollow")?.addEventListener("click"' in APP and "openFollowList(openBtn.dataset.followOpen)" in APP
    assert '"/api/follows/seen"' in APP and "markFollowersSeen()" in APP
    assert 'document.getElementById("playerFollowersNew")' in APP and "1000" in APP                 # большие числа сокращаются


def test_the_lists_suggest_active_players_and_an_invite_when_empty():
    assert '"/api/people/active"' in APP and "followListRecs" in APP and "Активные игроки ADAM" in APP
    assert "data-follow-invite" in APP and "openFriendInvite()" in APP.split("[data-follow-invite]")[1][:200]
    assert "follow-new-tag" in APP and "u.is_new" in APP


def test_the_new_css_is_light_and_scoped():
    block = CSS[CSS.index("/* Подписки и подписчики в шапке"):]
    assert ".player-follow__btn" in block and ".follow-recs__head" in block
    assert "filter" not in block, "в шапке, которая видна на всех вкладках, нельзя использовать filter и backdrop-filter"
    assert "animation" not in block and "transition" not in block


def test_the_feature_map_knows_the_new_route_and_buttons():
    from webapp import feature_map as fm
    assert fm.feature_for_api("POST /api/follows/seen") == "friends"
    assert fm.feature_for_click("player-follow__btn", "home") == "friends"
