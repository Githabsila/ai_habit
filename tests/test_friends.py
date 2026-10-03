"""
Друзья и «Напомнить друзьям» (db/friends.py, маршруты /api/friends*,
handlers/start.py). Часы получателя подменяем на полдень: правило «не ночью»
иначе делало бы тесты зависимыми от времени суток.
"""
import itertools
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import db.friends as friends
from db import (
    add_user, add_habit, complete_habit, get_habits,
    add_friendship, remove_friendship, are_friends, get_friend_sources,
    get_friends_overview, send_nudge, cancel_nudge, claim_remind_prompt,
    set_friend_nudges, toggle_reminders, set_quiet_hours, mark_bot_blocked,
    create_team, join_team, MAX_NUDGES_PER_DAY,
)
from db.core import connect
from db.friends import get_friend_state
from tests.conftest import sign_init_data


_uid_counter = itertools.count(800_000_000, 20)


@pytest.fixture
def uid():
    """Тесты берут несколько соседних id (uid, uid+1, …), а базовая фикстура
    шагает на 1 — id разных тестов пересекались бы. Здесь шаг 20."""
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def midday(monkeypatch):
    monkeypatch.setattr(
        friends, "_local_now",
        lambda user_id: datetime(2026, 10, 3, 12, 0, tzinfo=ZoneInfo("UTC")),
    )


def _user(uid, name="Игрок"):
    add_user(uid, f"u{uid}", name)
    return uid


def _with_habit(uid, name="Игрок"):
    """Пользователь с одной невыполненной привычкой — тому, кому можно напомнить."""
    _user(uid, name)
    habit_id = add_habit(uid, "Зарядка")
    assert habit_id
    return habit_id


def _befriend(a, b):
    assert add_friendship(a, b)


def _done(uid):
    """Пользователь отметился сегодня (через настоящий complete_habit)."""
    habit_id = add_habit(uid, "Вода")
    assert complete_habit(habit_id)


# ---------------------------------------------------------------------------
# ДРУЖБА
# ---------------------------------------------------------------------------

def test_friendship_is_mutual_and_idempotent(uid):
    a, b = _user(uid), _user(uid + 1)
    assert add_friendship(a, b) is True
    assert are_friends(a, b) and are_friends(b, a)
    assert add_friendship(a, b) is False
    assert add_friendship(b, a) is False  # та же пара с другой стороны


def test_cannot_befriend_self_or_missing_or_banned(uid):
    a, b = _user(uid), _user(uid + 1)
    assert add_friendship(a, a) is False
    assert add_friendship(a, uid + 999) is False
    conn = connect()
    conn.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (b,))
    conn.commit()
    conn.close()
    assert add_friendship(a, b) is False


def test_friend_limit(uid, monkeypatch):
    import db.follows as follows_module
    monkeypatch.setattr(follows_module, "MAX_FOLLOWING", 1)
    a, b, c = _user(uid), _user(uid + 1), _user(uid + 2)
    assert add_friendship(a, b) is True
    assert add_friendship(a, c) is False


def test_remove_friendship_both_sides(uid):
    a, b = _user(uid), _user(uid + 1)
    _befriend(a, b)
    assert remove_friendship(a, b) is True
    assert not are_friends(a, b) and not are_friends(b, a)
    assert remove_friendship(a, b) is False


def test_teammates_count_as_friends(uid):
    a, b = _user(uid), _user(uid + 1)
    team = create_team(a, "Команда")
    assert join_team(b, team["invite_code"])
    assert get_friend_sources(a) == {b: "team"}
    # Явная дружба приоритетнее «просто в одной команде».
    assert add_friendship(a, b)
    assert get_friend_sources(a) == {b: "friend"}


def test_reaction_partners_are_not_friends(uid):
    from db import send_reaction
    a, b = _user(uid), _user(uid + 1)
    assert send_reaction(a, b, "🔥")
    assert get_friend_sources(a) == {}


# ---------------------------------------------------------------------------
# СОСТОЯНИЕ «СЕГОДНЯ»
# ---------------------------------------------------------------------------

def test_friend_with_pending_habit_can_be_reminded(uid):
    viewer, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(viewer, friend)
    assert get_friend_state(viewer, friend) == "can_remind"


def test_friend_who_already_checked_in_is_done(uid):
    viewer, friend = _user(uid), uid + 1
    _with_habit(friend)
    _done(friend)
    _befriend(viewer, friend)
    assert get_friend_state(viewer, friend) == "done"


def test_friend_without_habits_cannot_be_reminded(uid):
    viewer, friend = _user(uid), _user(uid + 1)
    _befriend(viewer, friend)
    assert get_friend_state(viewer, friend) == "unavailable"


@pytest.mark.parametrize("opt_out", ["nudges_off", "reminders_off", "blocked_bot", "quiet_hours"])
def test_recipient_opt_outs_make_friend_unavailable(uid, opt_out):
    viewer, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(viewer, friend)
    if opt_out == "nudges_off":
        set_friend_nudges(friend, False)
    elif opt_out == "reminders_off":
        toggle_reminders(friend)  # по умолчанию включены → выключаем
    elif opt_out == "blocked_bot":
        mark_bot_blocked(friend)
    else:
        assert set_quiet_hours(friend, 11, 13)  # полдень попадает в окно
    assert get_friend_state(viewer, friend) == "unavailable"


@pytest.mark.parametrize("hour", [3, 7, 22, 23])
def test_no_reminders_at_night_local_time(uid, monkeypatch, hour):
    viewer, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(viewer, friend)
    monkeypatch.setattr(
        friends, "_local_now",
        lambda user_id: datetime(2026, 10, 3, hour, 30, tzinfo=ZoneInfo("UTC")),
    )
    assert get_friend_state(viewer, friend) == "unavailable"


def test_unavailable_reason_is_not_leaked_in_overview(uid):
    viewer, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(viewer, friend)
    set_friend_nudges(friend, False)
    entry = get_friends_overview(viewer)["friends"][0]
    assert entry["state"] == "unavailable"
    assert set(entry) == {"telegram_id", "first_name", "handle", "avatar_id", "frame_id", "streak", "state", "can_remove"}


# ---------------------------------------------------------------------------
# send_nudge
# ---------------------------------------------------------------------------

def test_nudge_requires_friendship(uid):
    sender, stranger = _user(uid), uid + 1
    _with_habit(stranger)
    _done(sender)
    assert send_nudge(sender, stranger) == {"error": "not_friends"}
    assert send_nudge(sender, sender) == {"error": "not_friends"}


def test_nudge_requires_sender_to_have_checked_in(uid):
    sender, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(sender, friend)
    assert send_nudge(sender, friend) == {"error": "sender_not_done"}


def test_nudge_once_per_day_per_pair(uid):
    sender, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(sender, friend)
    _done(sender)
    assert send_nudge(sender, friend) == {"ok": True}
    assert get_friend_state(sender, friend) == "reminded"
    assert send_nudge(sender, friend) == {"error": "already_reminded"}


def test_cancel_nudge_gives_the_chance_back(uid):
    sender, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(sender, friend)
    _done(sender)
    assert send_nudge(sender, friend) == {"ok": True}
    cancel_nudge(sender, friend)
    assert get_friend_state(sender, friend) == "can_remind"
    assert send_nudge(sender, friend) == {"ok": True}


def test_nudge_to_someone_who_checked_in_is_rejected(uid):
    sender, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(sender, friend)
    _done(sender)
    _done(friend)
    assert send_nudge(sender, friend) == {"error": "already_done"}


def test_recipient_gets_at_most_n_nudges_a_day(uid):
    friend = uid
    _with_habit(friend)
    senders = [_user(uid + 1 + i) for i in range(MAX_NUDGES_PER_DAY + 1)]
    for s in senders:
        _befriend(s, friend)
        _done(s)
    results = [send_nudge(s, friend) for s in senders]
    assert results[:MAX_NUDGES_PER_DAY] == [{"ok": True}] * MAX_NUDGES_PER_DAY
    assert results[MAX_NUDGES_PER_DAY] == {"error": "unavailable"}


# ---------------------------------------------------------------------------
# ОКНО ПОСЛЕ ОТМЕТКИ
# ---------------------------------------------------------------------------

def test_prompt_lists_only_friends_who_can_be_reminded(uid):
    me, waiting, finished, silent = _user(uid), uid + 1, uid + 2, uid + 3
    _with_habit(waiting, "Ждущий")
    _with_habit(finished)
    _done(finished)
    _user(silent)  # без привычек — напоминать нечего
    for f in (waiting, finished, silent):
        _befriend(me, f)
    _done(me)
    prompt = claim_remind_prompt(me)
    assert [f["telegram_id"] for f in prompt["friends"]] == [waiting]


def test_prompt_is_shown_once_a_day(uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(me, friend)
    _done(me)
    assert claim_remind_prompt(me) is not None
    assert claim_remind_prompt(me) is None


def test_prompt_needs_the_user_to_have_checked_in(uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(me, friend)
    assert claim_remind_prompt(me) is None


def test_prompt_not_consumed_when_nobody_to_remind(uid):
    """Нет кому напомнить → окно не тратится: позже в тот же день друг
    может появиться, и тогда оно ещё покажется."""
    me, friend = _user(uid), _user(uid + 1)
    _befriend(me, friend)
    _done(me)
    assert claim_remind_prompt(me) is None
    add_habit(friend, "Зарядка")
    assert claim_remind_prompt(me) is not None


def test_prompt_without_friends(uid):
    me = _user(uid)
    _done(me)
    assert claim_remind_prompt(me) is None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def test_friends_route_lists_friends(client, uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend, "Аня")
    _befriend(me, friend)
    r = await client.get("/api/friends", headers=_headers(me))
    assert r.status == 200
    data = await r.json()
    assert [(f["telegram_id"], f["first_name"], f["state"], f["can_remove"]) for f in data["friends"]] == [
        (friend, "Аня", "can_remind", True)
    ]
    assert data["viewer_done"] is False
    assert data["nudges_enabled"] is True


async def test_nudge_route_happy_path_and_errors(client, uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(me, friend)

    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "sender_not_done"

    _done(me)
    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 200 and (await r.json()) == {"ok": True}

    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "already_reminded"

    r = await client.post("/api/friends/abc/nudge", headers=_headers(me))
    assert r.status == 400 and (await r.json())["error"] == "invalid_target"


async def test_nudge_route_sends_telegram_message_with_escaped_name(client, uid):
    me, friend = _user(uid, "<b>Хакер</b>"), uid + 1
    _with_habit(friend)
    _befriend(me, friend)
    _done(me)
    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 200
    (chat_id, text), kwargs = bot.send_message.call_args
    assert chat_id == friend
    assert "&lt;b&gt;Хакер&lt;/b&gt;" in text and "<b>Хакер</b>" not in text
    assert kwargs["parse_mode"] == "HTML"


async def test_nudge_route_rolls_back_when_delivery_fails(client, uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(me, friend)
    _done(me)
    client.app["bot"] = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 502 and (await r.json())["error"] == "delivery_failed"
    assert get_friend_state(me, friend) == "can_remind"


async def test_nudge_route_marks_blocked_bot(client, uid):
    from aiogram.exceptions import TelegramForbiddenError
    from db import is_bot_blocked
    me, friend = _user(uid), uid + 1
    _with_habit(friend)
    _befriend(me, friend)
    _done(me)
    forbidden = TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked by the user")
    client.app["bot"] = SimpleNamespace(send_message=AsyncMock(side_effect=forbidden))
    r = await client.post(f"/api/friends/{friend}/nudge", headers=_headers(me))
    assert r.status == 502
    assert is_bot_blocked(friend)


async def test_remove_route_unfollows_one_way(client, uid):
    """Как в Duolingo: отписался ты — друг остаётся твоим подписчиком, но
    вы больше не друзья."""
    from db import get_relation
    me, friend = _user(uid), _user(uid + 1)
    _befriend(me, friend)
    r = await client.post(f"/api/friends/{friend}/remove", headers=_headers(me))
    assert r.status == 200
    relation = get_relation(me, friend)
    assert relation == {"following": False, "followed_by": True, "friends": False,
                        "blocked_by_me": False, "blocked_me": False}
    assert not are_friends(me, friend) and not are_friends(friend, me)
    r = await client.post(f"/api/friends/{friend}/remove", headers=_headers(me))
    assert r.status == 404


async def test_friend_nudges_setting_route(client, uid):
    me = _user(uid)
    r = await client.post("/api/settings/friend-nudges", json={"enabled": False}, headers=_headers(me))
    assert (await r.json()) == {"ok": True, "enabled": False}
    r = await client.get("/api/bootstrap", headers=_headers(me))
    assert (await r.json())["settings"]["friend_nudges"] is False
    r = await client.post("/api/settings/friend-nudges", json={"enabled": True}, headers=_headers(me))
    assert (await r.json())["enabled"] is True


async def test_completing_a_habit_returns_the_remind_window_once(client, uid):
    me, friend = _user(uid), uid + 1
    _with_habit(friend, "Аня")
    _befriend(me, friend)
    first = add_habit(me, "Первая")
    second = add_habit(me, "Вторая")

    r = await client.post(f"/api/habits/{first}/complete", headers=_headers(me))
    assert r.status == 200
    prompt = (await r.json())["remind_friends"]
    assert [f["first_name"] for f in prompt["friends"]] == ["Аня"]

    r = await client.post(f"/api/habits/{second}/complete", headers=_headers(me))
    assert (await r.json())["remind_friends"] is None


async def test_completing_a_habit_without_friends_has_no_window(client, uid):
    me = _user(uid)
    habit = add_habit(me, "Одна")
    r = await client.post(f"/api/habits/{habit}/complete", headers=_headers(me))
    assert (await r.json())["remind_friends"] is None


# ---------------------------------------------------------------------------
# /start по ссылке
# ---------------------------------------------------------------------------

def _start_message(user_id, payload):
    return SimpleNamespace(
        text=f"/start {payload}" if payload else "/start",
        from_user=SimpleNamespace(id=user_id, username=f"u{user_id}", first_name="Новичок"),
        answer=AsyncMock(),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.fixture
def start_handler(monkeypatch):
    import handlers.start as start_module
    monkeypatch.setattr(start_module, "begin_survey", AsyncMock())
    return start_module.start


def _xp(user_id):
    conn = connect()
    row = conn.execute("SELECT xp FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    conn.close()
    return row["xp"]


async def test_friend_link_befriends_new_user_and_pays_referral_bonus(start_handler, uid):
    inviter, newcomer = _user(uid, "Аня"), uid + 1
    before = _xp(inviter)
    message = _start_message(newcomer, f"friend_{inviter}")
    await start_handler(message, None)
    assert are_friends(inviter, newcomer)
    assert _xp(inviter) == before + 100
    # Пригласивший получил push, пришедший — подтверждение.
    assert message.bot.send_message.await_args.args[0] == inviter
    assert any("друзья" in c.args[0] for c in message.answer.await_args_list)


async def test_friend_link_between_existing_users_gives_no_xp(start_handler, uid):
    from db import set_access_status
    inviter, existing = _user(uid, "Аня"), _user(uid + 1, "Боря")
    set_access_status(existing, "approved")
    before_inviter, before_existing = _xp(inviter), _xp(existing)
    await start_handler(_start_message(existing, f"friend_{inviter}"), None)
    assert are_friends(inviter, existing)
    assert _xp(inviter) == before_inviter and _xp(existing) == before_existing


async def test_plain_referral_link_also_befriends(start_handler, uid):
    inviter, newcomer = _user(uid), uid + 1
    await start_handler(_start_message(newcomer, str(inviter)), None)
    assert are_friends(inviter, newcomer)


async def test_own_friend_link_does_nothing(start_handler, uid):
    me = _user(uid)
    await start_handler(_start_message(me, f"friend_{me}"), None)
    assert get_friend_sources(me) == {}


async def test_garbage_start_payload_is_ignored(start_handler, uid):
    me = _user(uid)
    await start_handler(_start_message(me, "friend_abc"), None)
    await start_handler(_start_message(me, "hello"), None)
    assert get_friend_sources(me) == {}


async def test_inviter_blocking_bot_does_not_break_start(start_handler, uid):
    inviter, newcomer = _user(uid), uid + 1
    message = _start_message(newcomer, f"friend_{inviter}")
    message.bot.send_message = AsyncMock(side_effect=RuntimeError("blocked"))
    await start_handler(message, None)
    assert are_friends(inviter, newcomer)
