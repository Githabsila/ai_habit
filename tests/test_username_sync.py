"""
Сверка Telegram-ников (16.10, п.14): человек сменил @ник — в статистике и админке новый. Живое обновление при каждом заходе (бот и приложение),
ночная сверка через Telegram (getChat) для тех, кто давно не заходит, и кнопка «Сверить ники сейчас» в админке.
"""
import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

import username_sync
from db import add_user, get_user
from db import users as users_module
from db.core import connect
from db.users import mark_username_checked, normalize_telegram_username, stale_username_users, sync_telegram_username
from middlewares.access_control import AccessControlMiddleware
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
ADMIN_HTML = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")

_uid_counter = itertools.count(847_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def fresh_cache():
    users_module._USERNAME_SEEN.clear()
    yield
    users_module._USERNAME_SEEN.clear()


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _nick(user_id):
    return get_user(user_id)["username"]


def _events(user_id):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT step, meta FROM user_events WHERE user_id=? AND name='username_changed'", (user_id,))]
    finally:
        conn.close()


# ------------------------------------------------------------------------------------------------ запись ника

def test_the_nick_is_normalised_like_telegram_gives_it():
    assert normalize_telegram_username("@Sater999") == "Sater999" and normalize_telegram_username("  nick ") == "nick"
    assert normalize_telegram_username("") is None and normalize_telegram_username(None) is None and normalize_telegram_username("@") is None
    assert len(normalize_telegram_username("a" * 100)) == 64


def test_a_changed_nick_replaces_the_old_one_and_is_recorded(uid):
    add_user(uid, "old_nick", "Александр")
    assert sync_telegram_username(uid, "new_nick") is True
    assert _nick(uid) == "new_nick" and get_user(uid)["username_checked_at"]
    assert get_user(uid)["first_name"] == "Александр"                       # имя в приложении не трогаем
    events = _events(uid)
    assert len(events) == 1 and "old_nick" in events[0]["meta"] and "new_nick" in events[0]["meta"]


def test_the_same_nick_changes_nothing_and_writes_no_event(uid):
    add_user(uid, "same", "Игрок")
    assert sync_telegram_username(uid, "same", force=True) is False
    assert _nick(uid) == "same" and _events(uid) == []


def test_a_removed_nick_is_cleared_so_the_app_handle_shows_instead(uid):
    add_user(uid, "gone_soon", "Игрок")
    assert sync_telegram_username(uid, None) is True
    assert _nick(uid) is None and get_user(uid)["handle"]


def test_a_nick_that_changes_only_in_case_is_updated(uid):
    add_user(uid, "sater999", "Игрок")
    assert sync_telegram_username(uid, "Sater999") is True and _nick(uid) == "Sater999"


def test_an_unknown_person_is_ignored(uid):
    assert sync_telegram_username(uid + 55, "ghost") is False


def test_the_live_update_does_not_hit_the_database_every_time(uid, monkeypatch):
    add_user(uid, "first", "Игрок")
    assert sync_telegram_username(uid, "second") is True
    calls = []
    real = users_module.connect
    monkeypatch.setattr(users_module, "connect", lambda: calls.append(1) or real())
    assert sync_telegram_username(uid, "second") is False and calls == []   # тот же ник сверяли только что — запроса к базе нет
    assert sync_telegram_username(uid, "third") is True and calls             # а новый ник проходит сразу
    assert _nick(uid) == "third"


# ------------------------------------------------------------------------------------------------ живое обновление: бот и приложение

async def test_any_bot_update_refreshes_the_nick(uid):
    add_user(uid, "before", "Игрок")
    middleware = AccessControlMiddleware()
    handler = AsyncMock(return_value="ok")
    event = SimpleNamespace(from_user=SimpleNamespace(id=uid, username="after_bot"))
    from config import ADMIN_IDS
    assert uid not in ADMIN_IDS
    _sql("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid,))
    await middleware(handler, event, {})
    assert _nick(uid) == "after_bot"


async def test_any_app_request_refreshes_the_nick(client, uid):
    add_user(uid, "before_app", "Игрок")
    _sql("UPDATE users SET access_status='approved' WHERE telegram_id=?", (uid,))
    headers = {"Authorization": f"tma {sign_init_data(uid, 'Игрок', 'fresh_in_app')}"}
    reply = await client.get("/api/friends", headers=headers)
    assert reply.status == 200 and _nick(uid) == "fresh_in_app"


# ------------------------------------------------------------------------------------------------ ночная сверка

def _bot(chats):
    """chats: {id: ник | исключение} — что ответит Telegram на getChat. Остальные люди общей тестовой базы отвечают тем же ником, что у них записан
    (для них сверка ничего не меняет)."""
    async def get_chat(user_id):
        value = chats[user_id] if user_id in chats else (get_user(user_id)["username"] if get_user(user_id) else None)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(id=user_id, username=value)
    return SimpleNamespace(get_chat=AsyncMock(side_effect=get_chat))


async def test_the_nightly_check_fixes_nicks_of_people_who_stopped_visiting(uid):
    quiet, stable, removed = uid, uid + 1, uid + 2
    for user_id, nick in ((quiet, "old_quiet"), (stable, "stable"), (removed, "will_remove")):
        add_user(user_id, nick, "Игрок")
        _sql("UPDATE users SET username_checked_at=datetime('now','-9 days') WHERE telegram_id=?", (user_id,))
    bot = _bot({quiet: "new_quiet", stable: "stable", removed: None})
    stats = await username_sync.run_username_sync(bot, limit=100000, pause=0)
    assert stats["changed"] >= 2 and stats["checked"] >= 3
    assert (_nick(quiet), _nick(stable), _nick(removed)) == ("new_quiet", "stable", None)


async def test_recently_checked_people_are_skipped_and_stamped_ones_are_not_asked_again(uid):
    fresh, old = uid, uid + 1
    add_user(fresh, "fresh", "Игрок")
    add_user(old, "old", "Игрок")
    mark_username_checked(fresh)
    _sql("UPDATE users SET username_checked_at=datetime('now','-30 days') WHERE telegram_id=?", (old,))
    due = stale_username_users(100000, 7)
    assert old in due and fresh not in due
    assert fresh in stale_username_users(100000, 0)                        # «всех» — для кнопки в админке


async def test_an_unreachable_person_is_stamped_and_does_not_break_the_run(uid):
    gone, fine = uid, uid + 1
    add_user(gone, "gone", "Игрок")
    add_user(fine, "fine_old", "Игрок")
    for user_id in (gone, fine):
        _sql("UPDATE users SET username_checked_at=NULL WHERE telegram_id=?", (user_id,))
    error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: chat not found")
    bot = _bot({gone: error, fine: "fine_new"})
    stats = await username_sync.run_username_sync(bot, limit=100000, pause=0)
    assert stats["failed"] >= 1 and _nick(fine) == "fine_new" and _nick(gone) == "gone"
    assert get_user(gone)["username_checked_at"]                           # отмечен — завтра его не дёргаем снова


async def test_telegram_asking_to_slow_down_stops_the_run_politely(uid):
    first = uid
    add_user(first, "slow", "Игрок")
    _sql("UPDATE users SET username_checked_at=NULL WHERE telegram_id=?", (first,))
    retry = TelegramRetryAfter(method=SimpleNamespace(), message="Too Many Requests", retry_after=1)
    sleeps = []
    import asyncio as aio
    real_sleep = aio.sleep
    aio.sleep = AsyncMock(side_effect=lambda s: sleeps.append(s))
    try:
        stats = await username_sync.run_username_sync(_bot({first: retry}), limit=100000, pause=0)
    finally:
        aio.sleep = real_sleep
    assert stats["checked"] == 0 and stats["left"] >= 1 and _nick(first) == "slow"


async def test_without_a_bot_nothing_happens():
    assert await username_sync.run_username_sync(None) == {"checked": 0, "changed": 0, "failed": 0, "left": 0}


# ------------------------------------------------------------------------------------------------ админка

async def test_admin_button_checks_everyone(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin = uid + 50
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    person = uid + 1
    add_user(person, "stale_nick", "Игрок")
    mark_username_checked(person)                                           # только что проверяли — кнопка всё равно проверит
    headers = lambda user_id: {"Authorization": f"tma {sign_init_data(user_id)}"}
    assert (await client.post("/api/admin/usernames/sync", headers=headers(person))).status == 403

    monkeypatch.setattr(username_sync, "MANUAL_LIMIT", 100000)           # в общей тестовой базе тысячи людей — берём всех
    client.app["bot"] = _bot({person: "actual_nick"})
    reply = await client.post("/api/admin/usernames/sync", headers=headers(admin))
    stats = await reply.json()
    assert reply.status == 200 and stats["changed"] >= 1 and stats["checked"] >= 1
    assert _nick(person) == "actual_nick"


async def test_the_support_card_shows_when_the_nick_was_checked(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin, person = uid + 50, uid + 1
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    add_user(person, "x", "Игрок")
    sync_telegram_username(person, "y")
    card = await (await client.get(f"/api/admin/user/{person}", headers={"Authorization": f"tma {sign_init_data(admin)}"})).json()
    assert card["username"] == "y" and card["username_checked_at"]


def test_admin_panel_has_the_button_and_the_tile():
    for needle in ("syncUsernamesBtn", "/api/admin/usernames/sync", "Ник сверен", "Сверить ники сейчас"):
        assert needle in ADMIN_HTML, needle


def test_the_job_is_scheduled_nightly():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "run_username_sync" in main and 'hour=4, minute=20' in main.split("run_username_sync, \"cron\"")[1][:80]
