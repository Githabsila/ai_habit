"""
Подписки (db/follows.py), профиль игрока (db/profiles.py) и их маршруты.
Дружба = взаимная подписка; что видно в чужом профиле, зависит от того,
подписан ли зритель, и от настройки владельца.
"""
import asyncio
import itertools
import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import webapp.webapp_server as ws
from db import (
    add_user, add_habit, complete_habit, follow, unfollow, make_friends, get_relation, get_counts,
    list_follows, find_user_by_handle, block_user, unblock_user, report_user, get_player_profile,
    get_friend_sources, set_stats_visibility, toggle_reminders, mark_bot_blocked,
    MAX_FOLLOWING,
)
from db.core import connect, create_tables
from db.statistics import add_statistics
from tests.conftest import sign_init_data

_uid_counter = itertools.count(700_000_000, 20)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _user(uid, name="Игрок"):
    add_user(uid, f"u{uid}", name)
    return uid


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def _flush_background():
    tasks = list(ws._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# ПОДПИСКИ
# ---------------------------------------------------------------------------

def test_one_way_follow_is_not_friendship(uid):
    a, b = _user(uid), _user(uid + 1)
    result = follow(a, b)
    assert result == {"ok": True, "newly_followed": True, "friends": False, "notify": "followed"}
    assert get_relation(a, b)["following"] and not get_relation(a, b)["friends"]
    assert get_relation(b, a)["followed_by"]
    assert get_friend_sources(a) == {} and get_friend_sources(b) == {}


def test_follow_back_makes_friends(uid):
    a, b = _user(uid), _user(uid + 1)
    follow(a, b)
    result = follow(b, a)
    assert result["friends"] is True and result["notify"] == "friends"
    assert get_friend_sources(a) == {b: "friend"} and get_friend_sources(b) == {a: "friend"}


def test_repeated_follow_is_idempotent_and_silent(uid):
    a, b = _user(uid), _user(uid + 1)
    follow(a, b)
    again = follow(a, b)
    assert again["newly_followed"] is False and again["notify"] is None
    assert get_counts(b) == {"followers": 1, "following": 0}


def test_unfollow_and_refollow_does_not_notify_twice(uid):
    a, b = _user(uid), _user(uid + 1)
    assert follow(a, b)["notify"] == "followed"
    assert unfollow(a, b) is True
    assert unfollow(a, b) is False
    assert follow(a, b)["notify"] is None  # подписка/отписка по кругу — не спам


def test_unfollow_breaks_friendship_one_way(uid):
    a, b = _user(uid), _user(uid + 1)
    make_friends(a, b)
    unfollow(a, b)
    assert not get_relation(a, b)["friends"]
    relation_from_b = get_relation(b, a)
    assert relation_from_b["following"] and not relation_from_b["followed_by"]
    assert get_friend_sources(b) == {}


def test_cannot_follow_self_missing_or_banned(uid):
    a, b = _user(uid), _user(uid + 1)
    assert follow(a, a) == {"error": "self"}
    assert follow(a, uid + 999) == {"error": "not_found"}
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (b,))
    assert follow(a, b) == {"error": "not_found"}


def test_following_limit(uid, monkeypatch):
    import db.follows as follows_module
    monkeypatch.setattr(follows_module, "MAX_FOLLOWING", 1)
    a, b, c = _user(uid), _user(uid + 1), _user(uid + 2)
    assert follow(a, b)["ok"]
    assert follow(a, c) == {"error": "limit"}
    assert MAX_FOLLOWING >= 100  # реальный лимит не крошечный


def test_make_friends_is_mutual_and_needs_no_extra_notice(uid):
    a, b = _user(uid), _user(uid + 1)
    assert make_friends(a, b) is True
    assert make_friends(b, a) is False
    assert get_relation(a, b)["friends"]
    # Ссылка уже сообщила обоим — «подписался на тебя» отдельным пушем не нужен.
    assert unfollow(a, b) and follow(a, b)["notify"] is None


def test_lists_carry_relation_flags(uid):
    me, fan, idol, friend = _user(uid), _user(uid + 1, "Фанат"), _user(uid + 2, "Кумир"), _user(uid + 3, "Друг")
    follow(fan, me)
    follow(me, idol)
    make_friends(me, friend)
    followers = {u["telegram_id"]: u for u in list_follows(me, "followers")}
    following = {u["telegram_id"]: u for u in list_follows(me, "following")}
    assert set(followers) == {fan, friend} and set(following) == {idol, friend}
    assert followers[fan]["following"] is False and followers[fan]["friends"] is False  # можно подписаться в ответ
    assert following[idol]["followed_by"] is False
    assert followers[friend]["friends"] is True and following[friend]["friends"] is True
    with pytest.raises(ValueError):
        list_follows(me, "enemies")


def test_lists_skip_banned_users(uid):
    me, fan = _user(uid), _user(uid + 1)
    follow(fan, me)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (fan,))
    assert list_follows(me, "followers") == []


def test_find_user_by_handle_is_exact_only(uid):
    a = _user(uid, "Аня")
    _sql("UPDATE users SET handle='anya_find' WHERE telegram_id=?", (a,))
    assert find_user_by_handle("anya_find")["telegram_id"] == a
    assert find_user_by_handle("@Anya_Find")["telegram_id"] == a
    assert find_user_by_handle("anya") is None  # префикс — не поиск
    assert find_user_by_handle("") is None


def test_old_friendships_are_migrated_once_and_not_resurrected(uid):
    a, b = _user(uid), _user(uid + 1)
    _sql("INSERT INTO friendships(user_id, friend_id) VALUES (?, ?)", (a, b))
    _sql("INSERT INTO friendships(user_id, friend_id) VALUES (?, ?)", (b, a))
    create_tables()
    assert get_relation(a, b)["friends"]
    unfollow(a, b)
    create_tables()  # рестарт сервера
    assert not get_relation(a, b)["following"], "отписка не должна откатываться при старте"


# ---------------------------------------------------------------------------
# БЛОКИРОВКА И ЖАЛОБЫ
# ---------------------------------------------------------------------------

def test_block_breaks_follows_and_closes_the_door(uid):
    a, b = _user(uid), _user(uid + 1)
    make_friends(a, b)
    assert block_user(a, b) is True
    assert not get_relation(a, b)["following"] and not get_relation(b, a)["following"]
    assert follow(b, a) == {"error": "not_found"}      # заблокированному — «не найден»
    assert follow(a, b) == {"error": "blocked"}        # заблокировавшему — подсказка
    assert make_friends(a, b) is False
    assert get_player_profile(b, a) is None              # профиль закрыт
    assert get_player_profile(a, b)["relation"]["blocked_by_me"] is True


def test_blocked_teammate_is_not_a_friend_for_nudges(uid):
    from db import create_team, join_team
    a, b = _user(uid), _user(uid + 1)
    team = create_team(a, "Команда")
    join_team(b, team["invite_code"])
    assert get_friend_sources(a) == {b: "team"}
    block_user(a, b)
    assert get_friend_sources(a) == {} and get_friend_sources(b) == {}


def test_unblock_reopens_following(uid):
    a, b = _user(uid), _user(uid + 1)
    block_user(a, b)
    assert unblock_user(a, b) is True and unblock_user(a, b) is False
    assert follow(b, a)["ok"]


def test_cannot_block_self_or_missing(uid):
    a = _user(uid)
    assert block_user(a, a) is False
    assert block_user(a, uid + 999) is False


def test_report_validation_and_daily_limit(uid):
    a, b = _user(uid), _user(uid + 1)
    assert report_user(a, b, "nonsense") == {"error": "invalid_reason"}
    assert report_user(a, a, "spam") == {"error": "self"}
    assert report_user(a, uid + 999, "spam") == {"error": "not_found"}
    first = report_user(a, b, "abuse", "  " + "я" * 900 + "  ")
    assert first["ok"]
    conn = connect()
    row = conn.execute("SELECT comment FROM user_reports WHERE id=?", (first["report_id"],)).fetchone()
    conn.close()
    assert len(row["comment"]) == 500
    assert report_user(a, b, "spam") == {"error": "already_reported"}


# ---------------------------------------------------------------------------
# ПРОФИЛЬ: КТО ЧТО ВИДИТ
# ---------------------------------------------------------------------------

def test_profile_tiers_follow_the_relation(uid):
    owner, stranger, subscriber, friend = _user(uid, "Топ"), _user(uid + 1), _user(uid + 2), _user(uid + 3)
    follow(subscriber, owner)
    make_friends(friend, owner)

    public = get_player_profile(stranger, owner)
    assert public["tier"] == "public" and public["sections"] == ["basic"]
    assert "chart" not in public and "overview" not in public and "extended" not in public

    sub = get_player_profile(subscriber, owner)
    assert sub["tier"] == "subscriber"
    assert {"chart", "overview", "achievements"} <= set(sub) and "extended" not in sub

    fr = get_player_profile(friend, owner)
    assert fr["tier"] == "friend" and "extended" in fr

    me = get_player_profile(owner, owner)
    assert me["tier"] == "self" and me["chart"]["viewer"] is None


def test_owner_can_hide_stats_from_plain_subscribers(uid):
    owner, subscriber, friend = _user(uid), _user(uid + 1), _user(uid + 2)
    follow(subscriber, owner)
    make_friends(friend, owner)
    assert set_stats_visibility(owner, "friends") is True
    assert get_player_profile(subscriber, owner)["tier"] == "public"
    assert get_player_profile(friend, owner)["tier"] == "friend"  # друзьям — по-прежнему всё
    assert set_stats_visibility(owner, "everyone") is False


def test_profile_basics_and_counts(uid):
    owner, fan, idol = _user(uid, "Аня"), _user(uid + 1), _user(uid + 2)
    follow(fan, owner)
    follow(owner, idol)
    profile = get_player_profile(fan, owner)
    assert profile["first_name"] == "Аня"
    assert profile["followers"] == 1 and profile["following"] == 1
    assert profile["relation"]["following"] is True and profile["relation"]["self"] is False


def test_profile_missing_or_banned(uid):
    a, b = _user(uid), _user(uid + 1)
    assert get_player_profile(a, uid + 999) is None
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (b,))
    assert get_player_profile(a, b) is None


def test_week_chart_compares_viewer_with_target(uid):
    owner, viewer = _user(uid), _user(uid + 1)
    follow(viewer, owner)
    add_statistics(owner, 1, 40)
    add_statistics(owner, 1, 25)       # две записи за день складываются
    add_statistics(viewer, 1, 10)
    chart = get_player_profile(viewer, owner)["chart"]
    assert len(chart["labels"]) == len(chart["target"]) == len(chart["viewer"]) == 7
    assert chart["target"][-1] == 65 and chart["target_total"] == 65
    assert chart["viewer"][-1] == 10 and chart["viewer_total"] == 10
    assert chart["target"][:-1] == [0] * 6  # дни без активности — нули
    assert chart["labels"][-1] == ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[date.today().weekday()]


def test_overview_and_achievements(uid):
    owner, viewer = _user(uid), _user(uid + 1)
    follow(viewer, owner)
    _sql("UPDATE users SET streak=17, best_streak=21, total_xp=1485, total_completed=60 WHERE telegram_id=?", (owner,))
    for i in range(15):
        _sql("INSERT INTO achievements(user_id, title, description) VALUES (?, ?, ?)", (owner, f"Ачивка {i}", "d"))
    profile = get_player_profile(viewer, owner)
    assert profile["overview"]["streak"] == 17 and profile["overview"]["best_streak"] == 21
    assert profile["overview"]["total_xp"] == 1485 and profile["overview"]["total_completed"] == 60
    assert profile["overview"]["league"] == "🥈 Серебро"
    assert profile["achievements_count"] == 15 and len(profile["achievements"]) == 12


def test_extended_stats_for_friends_and_no_habit_titles_anywhere(uid):
    owner, friend, subscriber = _user(uid), _user(uid + 1), _user(uid + 2)
    make_friends(friend, owner)
    follow(subscriber, owner)
    secret = "Бросить курить к декабрю"
    complete_habit(add_habit(owner, secret))
    add_habit(owner, "Вторая")
    yesterday = str(date.today() - timedelta(days=1))
    _sql("INSERT INTO calendar(user_id, day, completed, total) VALUES (?, ?, ?, ?)", (owner, yesterday, 3, 4))

    extended = get_player_profile(friend, owner)["extended"]
    assert extended["today"] == {"completed": 1, "total": 2, "done": True}
    # Вчера 3 из 4 + сегодняшняя строка календаря, которую сама пишет
    # complete_habit (1 из 1): 4 из 5 за два активных дня.
    assert extended["completion_rate_30d"] == 80 and extended["active_days_30"] == 2
    assert len(extended["week_completed"]) == 7
    # Названия привычек не отдаются ни одному уровню.
    for viewer in (friend, subscriber, owner):
        assert secret not in json.dumps(get_player_profile(viewer, owner), ensure_ascii=False)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _bot():
    return SimpleNamespace(send_message=AsyncMock())


async def test_profile_route(client, uid):
    me, other = _user(uid), _user(uid + 1, "Боря")
    r = await client.get(f"/api/users/{other}/profile", headers=_headers(me))
    assert r.status == 200
    data = await r.json()
    assert data["first_name"] == "Боря" and data["tier"] == "public"
    assert (await client.get(f"/api/users/{uid + 999}/profile", headers=_headers(me))).status == 404
    assert (await client.get("/api/users/abc/profile", headers=_headers(me))).status == 400


async def test_follow_route_and_push(client, uid):
    me, other = _user(uid, "Аня"), _user(uid + 1)
    client.app["bot"] = bot = _bot()
    r = await client.post(f"/api/users/{other}/follow", headers=_headers(me))
    assert r.status == 200
    data = await r.json()
    assert data["relation"]["following"] and data["counts"] == {"followers": 1, "following": 0}
    await _flush_background()
    (chat_id, text), _ = bot.send_message.call_args
    assert chat_id == other and "Аня" in text and "подписался(ась) на тебя" in text and "друзья" not in text

    # Повторная подписка — без нового пуша.
    bot.send_message.reset_mock()
    await client.post(f"/api/users/{other}/follow", headers=_headers(me))
    await _flush_background()
    bot.send_message.assert_not_called()


async def test_follow_back_pushes_friendship(client, uid):
    a, b = _user(uid, "Аня"), _user(uid + 1, "Боря")
    follow(a, b)
    client.app["bot"] = bot = _bot()
    r = await client.post(f"/api/users/{a}/follow", headers=_headers(b))
    assert (await r.json())["relation"]["friends"] is True
    await _flush_background()
    (chat_id, text), _ = bot.send_message.call_args
    assert chat_id == a and "в ответ" in text and "друзья" in text


async def test_follow_push_escapes_name_and_respects_opt_outs(client, uid):
    evil, quiet, blocked_bot = _user(uid, "<b>X</b>"), _user(uid + 1), _user(uid + 2)
    client.app["bot"] = bot = _bot()
    await client.post(f"/api/users/{quiet}/follow", headers=_headers(evil))
    await _flush_background()
    assert "&lt;b&gt;X&lt;/b&gt;" in bot.send_message.call_args.args[1]

    bot.send_message.reset_mock()
    toggle_reminders(quiet)  # выключил напоминания → не пишем
    other = _user(uid + 3)
    await client.post(f"/api/users/{quiet}/follow", headers=_headers(other))
    await _flush_background()
    bot.send_message.assert_not_called()

    mark_bot_blocked(blocked_bot)
    await client.post(f"/api/users/{blocked_bot}/follow", headers=_headers(evil))
    await _flush_background()
    bot.send_message.assert_not_called()


async def test_follow_route_errors(client, uid):
    me, other = _user(uid), _user(uid + 1)
    r = await client.post(f"/api/users/{me}/follow", headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "self"
    r = await client.post(f"/api/users/{uid + 999}/follow", headers=_headers(me))
    assert r.status == 404
    block_user(me, other)
    r = await client.post(f"/api/users/{other}/follow", headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "blocked"


async def test_unfollow_route_is_idempotent(client, uid):
    me, other = _user(uid), _user(uid + 1)
    follow(me, other)
    for _ in range(2):
        r = await client.post(f"/api/users/{other}/unfollow", headers=_headers(me))
        assert r.status == 200 and (await r.json())["relation"]["following"] is False


async def test_follows_list_route(client, uid):
    me, fan = _user(uid), _user(uid + 1, "Фанат")
    follow(fan, me)
    r = await client.get("/api/follows?kind=followers", headers=_headers(me))
    data = await r.json()
    assert [u["first_name"] for u in data["users"]] == ["Фанат"] and data["counts"]["followers"] == 1
    assert (await client.get("/api/follows?kind=following", headers=_headers(me))).status == 200
    assert (await client.get("/api/follows?kind=x", headers=_headers(me))).status == 400


async def test_block_unblock_routes(client, uid):
    me, other = _user(uid), _user(uid + 1)
    follow(other, me)
    r = await client.post(f"/api/users/{other}/block", headers=_headers(me))
    assert r.status == 200 and (await r.json())["relation"]["blocked_by_me"] is True
    r = await client.get(f"/api/users/{me}/profile", headers=_headers(other))
    assert r.status == 404
    r = await client.post(f"/api/users/{other}/unblock", headers=_headers(me))
    assert (await r.json())["relation"]["blocked_by_me"] is False
    assert (await client.post(f"/api/users/{me}/block", headers=_headers(me))).status == 400


async def test_blocked_user_cannot_send_reactions(client, uid):
    me, other = _user(uid), _user(uid + 1)
    block_user(me, other)
    r = await client.post(f"/api/friends/{me}/react", json={"emoji": "🔥"}, headers=_headers(other))
    assert r.status == 404 and (await r.json())["error"] == "not_found"
    r = await client.post(f"/api/friends/{other}/react", json={"emoji": "🔥"}, headers=_headers(me))
    assert r.status == 404


async def test_report_route_notifies_admins(client, uid, monkeypatch):
    me, other = _user(uid, "Аня"), _user(uid + 1, "Нарушитель")
    monkeypatch.setattr(ws, "ADMIN_IDS", [4242])
    client.app["bot"] = bot = _bot()
    r = await client.post(f"/api/users/{other}/report", json={"reason": "spam", "comment": "рекламирует"}, headers=_headers(me))
    assert r.status == 200
    kwargs = bot.send_message.call_args.kwargs
    text = kwargs["text"]
    assert kwargs["chat_id"] == 4242 and "Нарушитель" in text and "спам" in text and "рекламирует" in text

    r = await client.post(f"/api/users/{other}/report", json={"reason": "spam"}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "already_reported"
    r = await client.post(f"/api/users/{other}/report", json={"reason": "???"}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "invalid_reason"


async def test_search_route(client, uid):
    me, other = _user(uid), _user(uid + 1, "Боря")
    _sql("UPDATE users SET handle='borya_search' WHERE telegram_id=?", (other,))
    r = await client.get("/api/users/search?handle=@Borya_Search", headers=_headers(me))
    assert r.status == 200 and (await r.json())["user"]["telegram_id"] == other
    assert (await client.get("/api/users/search?handle=borya", headers=_headers(me))).status == 404
    block_user(other, me)
    assert (await client.get("/api/users/search?handle=borya_search", headers=_headers(me))).status == 404


async def test_stats_visibility_route(client, uid):
    me = _user(uid)
    r = await client.post("/api/settings/stats-visibility", json={"visibility": "friends"}, headers=_headers(me))
    assert (await r.json()) == {"ok": True, "visibility": "friends"}
    boot = await (await client.get("/api/bootstrap", headers=_headers(me))).json()
    assert boot["settings"]["stats_visibility"] == "friends"
    r = await client.post("/api/settings/stats-visibility", json={"visibility": "all"}, headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "invalid_visibility"


async def test_friends_route_has_counts(client, uid):
    me, fan, idol = _user(uid), _user(uid + 1), _user(uid + 2)
    follow(fan, me)
    follow(me, idol)
    data = await (await client.get("/api/friends", headers=_headers(me))).json()
    assert data["counts"] == {"followers": 1, "following": 1}
    assert data["friends"] == []  # подписчик и «кумир» друзьями не считаются
