"""
Спасение серии друга (21.10, п.12 владельца: в Duolingo «друг обрывает ваш дружеский забег, но его ещё можно спасти» — «Восстановить
бесплатно» / «Напомнить позже»). У нас: серия друга погасла → ему можно её вернуть бесплатно (db/rescue.py), окно «Серию друга ещё можно
спасти!», значок погасшей серии у друга в рейтинге, пуш друзьям (streak_scheduler.run_rescue_notifications). Браузера в тестах нет: правила,
маршруты, пуши и учёт проверяем по-настоящему, окно — по разметке и скрипту.
"""
import asyncio
import itertools
import re
import subprocess
import shutil
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db import add_user
from db import rescue, streak
from db.core import connect
from db.follows import make_friends
from tests.conftest import sign_init_data
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")

_uid_counter = itertools.count(860_100_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _today():
    return datetime.now(ZoneInfo("UTC")).date()           # у новых людей пояс UTC (db/streak.py::get_timezone)


def _row(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


def _row_all(sql, args=()):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _run(sql, args=()):
    conn = connect()
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def _person(uid_, name="Мария"):
    add_user(uid_, f"p{uid_}", name)
    _run("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (uid_,))
    return uid_


def _broken(owner, lost=12, days_ago=1, now_streak=0, done_between=False):
    """Серия человека погасла days_ago дней назад (lost дней потеряно). done_between — день после срыва он закрыл (так бывает на 2-й день)."""
    broken_day = (_today() - timedelta(days=days_ago)).isoformat()
    _run("UPDATE users SET streak=? WHERE telegram_id=?", (now_streak, owner))
    _run("UPDATE streak_meta SET last_broken_streak=?, last_broken_date=?, free_restore_month=NULL WHERE user_id=?", (lost, broken_day, owner))
    _run("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after, ai_message) VALUES (?, ?, 'missed', 0, 'x')", (owner, broken_day))
    if done_between:
        mid = (_today() - timedelta(days=days_ago - 1)).isoformat()
        _run("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after, ai_message) VALUES (?, ?, 'completed', ?, 'x')",
             (owner, mid, now_streak))
    return broken_day


def _friends(uid_, lost=12, **broken):
    """(спасатель, друг с погасшей серией) — взаимные друзья."""
    helper, owner = _person(uid_, "Спасатель"), _person(uid_ + 1)
    make_friends(helper, owner)
    day = _broken(owner, lost, **broken)
    return helper, owner, day


# ---------------------------------------------------------------------------------------------------------------- когда можно спасти

def test_a_fresh_break_of_a_real_streak_can_be_saved(uid):
    helper, owner, day = _friends(uid)
    state = rescue.rescue_state(owner)
    assert state["available"] and state["lost_streak"] == 12 and state["broken_date"] == day and state["current_streak"] == 0


@pytest.mark.parametrize("lost", [0, 1, 2])
def test_a_short_streak_is_not_worth_saving(uid, lost):
    helper, owner, _ = _friends(uid, lost=lost)
    assert rescue.rescue_state(owner)["available"] is False
    assert rescue.rescue_states([owner]) == {}


def test_an_old_break_is_too_late(uid):
    helper, owner, _ = _friends(uid, days_ago=streak.FREE_RESTORE_GRACE_DAYS + 1)
    assert rescue.rescue_state(owner)["available"] is False


def test_a_second_missed_day_means_the_streak_is_really_gone(uid):
    """Срыв был позавчера и вчера человек тоже не отмечался — серия пережила бы два пустых дня, возвращать нечего."""
    helper, owner, _ = _friends(uid, days_ago=2, done_between=False)
    assert rescue.rescue_state(owner)["available"] is False


def test_the_day_after_the_break_may_already_be_closed(uid):
    helper, owner, _ = _friends(uid, days_ago=2, now_streak=1, done_between=True)
    assert rescue.rescue_state(owner)["available"] is True


def test_nothing_to_save_for_someone_who_never_lost_a_streak(uid):
    person = _person(uid)
    assert rescue.rescue_state(person) == {"available": False, "lost_streak": 0, "reason": "not_available"}
    assert rescue.rescue_state(uid + 999) == {"available": False, "lost_streak": 0, "reason": "not_available"}


# ---------------------------------------------------------------------------------------------------------------- спасение

def test_the_streak_comes_back_and_the_break_is_forgotten(uid):
    helper, owner, day = _friends(uid)
    result = rescue.rescue_streak(helper, owner)
    assert result == {"ok": True, "lost_streak": 12, "streak": 12, "broken_date": day}
    assert _row("SELECT streak, best_streak FROM users WHERE telegram_id=?", (owner,))["streak"] == 12
    meta = _row("SELECT last_broken_streak, last_broken_date, free_restore_month FROM streak_meta WHERE user_id=?", (owner,))
    assert meta["last_broken_streak"] == 0 and meta["last_broken_date"] is None
    assert meta["free_restore_month"] is None, "свой бесплатный возврат раз в месяц спасение не тратит"
    saved = _row("SELECT status, streak_after, ai_message FROM streak_days WHERE user_id=? AND day=?", (owner, day))
    assert saved["status"] == "freeze" and saved["streak_after"] == 12 and saved["ai_message"].startswith("💛")
    assert streak.get_free_restore_status(owner)["available"] is False, "вернуть уже нечего"
    assert rescue.rescue_state(owner)["available"] is False


def test_the_next_midnight_does_not_take_the_restored_streak_away(uid):
    helper, owner, day = _friends(uid)
    rescue.rescue_streak(helper, owner)
    _run("UPDATE streak_meta SET rollover_day='2000-01-01' WHERE user_id=?", (owner,))
    streak.rollover_user(owner)
    assert _row("SELECT streak FROM users WHERE telegram_id=?", (owner,))["streak"] == 12, "вчера в журнале «freeze» — полночь серию не гасит"
    assert _row("SELECT status FROM streak_days WHERE user_id=? AND day=?", (owner, day))["status"] == "freeze"


def test_what_the_person_earned_after_the_break_is_added_to_the_lost_days(uid):
    helper, owner, day = _friends(uid, now_streak=1)
    today = _today().isoformat()
    _run("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after, ai_message) VALUES (?, ?, 'completed', 1, 'x')", (owner, today))
    assert rescue.rescue_streak(helper, owner)["streak"] == 13
    assert _row("SELECT streak, best_streak FROM users WHERE telegram_id=?", (owner,)) ["streak"] == 13
    assert _row("SELECT streak_after FROM streak_days WHERE user_id=? AND day=?", (owner, today))["streak_after"] == 13


def test_a_two_day_old_break_with_a_closed_day_in_between_is_restored_too(uid):
    helper, owner, day = _friends(uid, days_ago=2, now_streak=1, done_between=True)
    assert rescue.rescue_streak(helper, owner)["streak"] == 13
    mid = (_today() - timedelta(days=1)).isoformat()
    assert _row("SELECT streak_after FROM streak_days WHERE user_id=? AND day=?", (owner, mid))["streak_after"] == 13


def test_the_best_streak_grows_if_the_restored_one_is_a_record(uid):
    helper, owner, _ = _friends(uid)
    _run("UPDATE users SET best_streak=5 WHERE telegram_id=?", (owner,))
    rescue.rescue_streak(helper, owner)
    assert _row("SELECT best_streak FROM users WHERE telegram_id=?", (owner,))["best_streak"] == 12


# ---------------------------------------------------------------------------------------------------------------- правила

def test_only_friends_can_save_a_streak(uid):
    stranger, owner = _person(uid, "Чужой"), _person(uid + 1)
    _broken(owner)
    assert rescue.rescue_streak(stranger, owner) == {"error": "not_friends"}
    assert rescue.rescue_streak(owner, owner) == {"error": "not_friends"}, "себе нельзя"
    assert _row("SELECT streak FROM users WHERE telegram_id=?", (owner,))["streak"] == 0
    assert rescue.rescue_candidates(stranger) == []


def test_one_friend_helps_the_same_person_only_once_a_month_but_may_help_different_people(uid):
    helper, first, _ = _friends(uid)
    second = _person(uid + 2, "Второй")
    make_friends(helper, second)
    _broken(second)
    assert rescue.rescue_streak(helper, first)["ok"]
    assert rescue.rescue_state(second)["available"] is True
    assert rescue.rescue_streak(helper, second)["ok"], "решение владельца 28.10: ограничение на пару «друг → человек», а не на спасателя вообще"
    assert rescue.helped_this_month(helper, first) is True and rescue.helped_this_month(helper, second) is True
    _break_again(first)                                 # серия первого погасла снова
    assert rescue.rescue_state(first)["available"] is True, "второй шанс у него есть"
    assert rescue.rescue_streak(helper, first) == {"error": "helper_limit"}, "но от того же друга в этом месяце — уже нет"


def test_one_break_is_saved_once_even_with_two_helpers(uid):
    helper, owner, _ = _friends(uid)
    other = _person(uid + 2, "Другой")
    make_friends(other, owner)
    assert rescue.rescue_streak(helper, owner)["ok"]
    assert rescue.rescue_streak(other, owner) == {"error": "not_available"}
    assert _row("SELECT COUNT(*) AS n FROM streak_rescues WHERE owner_id=?", (owner,))["n"] == 1


def _break_again(owner, lost=9):
    """Серия человека погасла снова: прошлые записи о спасении уходят в «давнюю» дату, чтобы у нового срыва была своя."""
    _broken(owner, lost=lost)
    _run("UPDATE streak_rescues SET broken_date='old-' || id WHERE owner_id=? AND broken_date NOT LIKE 'old-%'", (owner,))


def test_a_person_is_saved_at_most_twice_a_month_each_time_by_a_different_friend(uid):
    helper, owner, _ = _friends(uid)
    other, third = _person(uid + 2, "Другой"), _person(uid + 3, "Третий")
    make_friends(other, owner)
    make_friends(third, owner)
    assert rescue.rescue_streak(helper, owner)["ok"]
    _break_again(owner)
    assert rescue.rescue_state(owner)["available"] is True, "второй шанс"
    assert rescue.rescue_streak(other, owner)["ok"], "помогает другой друг"
    _break_again(owner, lost=7)
    assert rescue.rescue_state(owner)["reason"] == "friend_limit", "в третий раз за месяц — уже нет"
    assert rescue.rescue_streak(third, owner) == {"error": "friend_limit"}
    _run("UPDATE streak_rescues SET month='2000-01', owner_month='2000-01' WHERE owner_id=?", (owner,))
    assert rescue.rescue_streak(third, owner)["ok"], "в новом месяце счётчик свой"


def test_a_failed_attempt_costs_the_helper_nothing(uid):
    helper, owner, _ = _friends(uid)
    _run("UPDATE streak_meta SET last_broken_streak=0 WHERE user_id=?", (owner,))
    assert rescue.rescue_streak(helper, owner) == {"error": "not_available"}
    assert rescue.helped_this_month(helper, owner) is False, "неудача помощь не тратит"


def test_a_banned_person_is_not_offered(uid):
    helper, owner, _ = _friends(uid)
    _run("UPDATE users SET banned=1 WHERE telegram_id=?", (owner,))
    assert rescue.rescue_state(owner)["available"] is False


# ---------------------------------------------------------------------------------------------------------------- кого предлагает окно

def test_candidates_are_friends_with_a_savable_streak_longest_loss_first(uid):
    helper, small, _ = _friends(uid, lost=5)
    big = _person(uid + 2, "Большая")
    make_friends(helper, big)
    _broken(big, lost=40)
    healthy = _person(uid + 3, "Здоровая")
    make_friends(helper, healthy)
    people = rescue.rescue_candidates(helper)
    assert [p["telegram_id"] for p in people] == [big, small]
    assert people[0]["lost_streak"] == 40 and {"first_name", "avatar_id", "frame_id", "handle"} <= set(people[0])


def test_the_window_does_not_offer_a_friend_this_person_already_saved_this_month(uid):
    helper, first, _ = _friends(uid)
    second = _person(uid + 2, "Второй")
    make_friends(helper, second)
    _broken(second)
    rescue.rescue_streak(helper, first)
    _break_again(first)
    offered = [f["telegram_id"] for f in rescue.rescue_candidates(helper)]
    assert offered == [second], "первого этот друг в этом месяце уже спасал — ему предлагается только второй"
    other = _person(uid + 4, "Другой друг")
    make_friends(other, first)
    assert first in [f["telegram_id"] for f in rescue.rescue_candidates(other)], "а другому другу второй шанс предлагается"


def test_the_friends_overview_marks_only_savable_friends(uid):
    from db.friends import get_friends_overview
    helper, owner, _ = _friends(uid, lost=21)
    calm = _person(uid + 2, "Спокойный")
    make_friends(helper, calm)
    overview = {f["telegram_id"]: f for f in get_friends_overview(helper)["friends"]}
    assert overview[owner]["rescue"] == {"lost_streak": 21, "broken_date": _broken_date(owner)}
    assert "rescue" not in overview[calm]
    rescue.rescue_streak(helper, owner)
    assert "rescue" not in {f["telegram_id"]: f for f in get_friends_overview(helper)["friends"]}[owner]


def _broken_date(owner):
    row = _row("SELECT last_broken_date FROM streak_meta WHERE user_id=?", (owner,))
    return row["last_broken_date"]


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_routes_need_a_signed_in_person(client, uid):
    assert (await client.get("/api/rescue-help")).status == 401
    assert (await client.post(f"/api/users/{uid}/rescue")).status == 401


async def test_the_help_route_lists_the_friends_in_trouble(client, uid):
    helper, owner, _ = _friends(uid)
    body = await (await client.get("/api/rescue-help", headers=_headers(helper))).json()
    assert [f["telegram_id"] for f in body["friends"]] == [owner] and body["friends"][0]["lost_streak"] == 12


async def test_the_save_route_returns_the_streak_writes_the_event_and_pushes_the_friend(client, uid, monkeypatch):
    import webapp.webapp_server as server
    helper, owner, _ = _friends(uid)
    pushes = []

    async def fake_push(app, target_id, text):
        pushes.append((target_id, text))

    monkeypatch.setattr(server, "_send_social_push", fake_push)
    resp = await client.post(f"/api/users/{owner}/rescue", headers=_headers(helper))
    body = await resp.json()
    assert resp.status == 200 and body == {"ok": True, "streak": 12, "lost_streak": 12}
    await asyncio.sleep(0.05)
    assert len(pushes) == 1 and pushes[0][0] == owner
    assert "спас(ла) твою серию" in pushes[0][1] and "12" in pushes[0][1] and "Спасатель" in pushes[0][1]
    events = _row("SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND name='rescue_sent'", (helper,))
    assert events["n"] == 1
    again = await client.post(f"/api/users/{owner}/rescue", headers=_headers(helper))
    assert again.status == 400 and (await again.json())["error"] == "helper_limit"
    assert len(pushes) == 1, "отказ другу ничего не отправляет"


async def test_the_save_route_refuses_strangers_and_bad_targets(client, uid):
    stranger, owner = _person(uid, "Чужой"), _person(uid + 1)
    _broken(owner)
    resp = await client.post(f"/api/users/{owner}/rescue", headers=_headers(stranger))
    assert resp.status == 400 and (await resp.json())["error"] == "not_friends"
    bad = await client.post("/api/users/abc/rescue", headers=_headers(stranger))
    assert bad.status == 400 and (await bad.json())["error"] == "invalid_target"


async def test_the_friends_route_carries_the_marker(client, uid):
    helper, owner, _ = _friends(uid, lost=8)
    body = await (await client.get("/api/friends", headers=_headers(helper))).json()
    assert next(f for f in body["friends"] if f["telegram_id"] == owner)["rescue"]["lost_streak"] == 8


# ---------------------------------------------------------------------------------------------------------------- пуш друзьям

class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


def _to(bot, user):
    """Что бот отправил именно этому человеку (база общая на весь прогон: пары из соседних тестов тоже попадают в рассылку)."""
    return [message for message in bot.sent if message[0] == user]


def _daytime(monkeypatch, hour=14):
    import streak_scheduler as sched

    class _Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 21, hour, 0, tzinfo=tz)

    monkeypatch.setattr(sched, "datetime", _Fake)
    return sched


async def test_friends_get_one_push_per_break_in_the_daytime(uid, monkeypatch):
    sched = _daytime(monkeypatch)
    helper, owner, day = _friends(uid, lost=12)
    bot = _Bot()
    await sched.run_rescue_notifications(bot)
    mine = _to(bot, helper)
    assert len(mine) == 1
    text = mine[0][1]
    assert "<b>Мария</b>" in text and "12 дней" in text and "бесплатно" in text
    assert mine[0][2]["parse_mode"] == "HTML"
    await sched.run_rescue_notifications(bot)
    assert len(_to(bot, helper)) == 1, "про один срыв — один пуш"
    assert _row("SELECT 1 FROM rescue_pushes WHERE owner_id=? AND helper_id=? AND broken_date=?", (owner, helper, day)) is not None


async def test_no_push_at_night_or_with_reminders_off_or_after_the_save(uid, monkeypatch):
    sched = _daytime(monkeypatch, hour=3)
    helper, owner, _ = _friends(uid)
    bot = _Bot()
    await sched.run_rescue_notifications(bot)
    assert _to(bot, helper) == [], "ночью друзей не будим"
    sched = _daytime(monkeypatch, hour=14)
    _run("UPDATE settings SET reminders=0 WHERE user_id=?", (helper,))
    await sched.run_rescue_notifications(bot)
    assert _to(bot, helper) == [], "тумблер напоминаний выключен"
    _run("UPDATE settings SET reminders=1 WHERE user_id=?", (helper,))
    rescue.rescue_streak(helper, owner)
    await sched.run_rescue_notifications(bot)
    assert _to(bot, helper) == [], "серия уже возвращена — писать не о чем"


async def test_a_friend_who_already_saved_this_person_this_month_is_not_asked_again_about_him(uid, monkeypatch):
    sched = _daytime(monkeypatch)
    helper, first, _ = _friends(uid)
    second = _person(uid + 2, "Второй")
    make_friends(helper, second)
    _broken(second)
    rescue.rescue_streak(helper, first)
    _break_again(first)
    bot = _Bot()
    await sched.run_rescue_notifications(bot)
    mine = _to(bot, helper)
    assert len(mine) == 1 and "<b>Второй</b>" in mine[0][1] and "<b>Мария</b>" not in mine[0][1], "про первого — нет (тот уже помог), про второго — есть"


async def test_when_the_first_friend_saves_the_streak_the_push_vanishes_for_the_others(client, uid, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import webapp.webapp_server as server
    sched = _daytime(monkeypatch)
    helper, owner, day = _friends(uid)
    other = _person(uid + 2, "Другой")
    make_friends(other, owner)

    class _NumberedBot(_Bot):
        delete_message = AsyncMock()

        async def send_message(self, chat_id, text, **kwargs):
            await super().send_message(chat_id, text, **kwargs)
            return SimpleNamespace(message_id=5000 + len(self.sent))

    bot = _NumberedBot()
    bot.delete_message = AsyncMock()
    await sched.run_rescue_notifications(bot)
    assert len(_to(bot, helper)) == 1 and len(_to(bot, other)) == 1
    stored = {r["helper_id"]: r["message_id"] for r in [dict(x) for x in (_row_all("SELECT helper_id, message_id FROM rescue_pushes WHERE owner_id=? AND broken_date=?", (owner, day)))]}
    assert set(stored) == {helper, other} and all(isinstance(v, int) for v in stored.values())

    async def quiet_push(app, target_id, text):
        return None

    monkeypatch.setattr(server, "_send_social_push", quiet_push)
    client.app["bot"] = bot
    assert (await client.post(f"/api/users/{owner}/rescue", headers=_headers(helper))).status == 200
    await asyncio.sleep(0.1)
    deleted = [(c.args[0], c.args[1]) for c in bot.delete_message.call_args_list]
    assert deleted == [(other, stored[other])], "тому, кто спас, его собственное сообщение остаётся, у остальных — удалено"
    assert _row("SELECT withdrawn_at FROM rescue_pushes WHERE owner_id=? AND broken_date=? AND helper_id=?", (owner, day, other))["withdrawn_at"] is not None
    assert (await client.get("/api/rescue-help", headers=_headers(other))).status == 200
    assert (await (await client.get("/api/rescue-help", headers=_headers(other))).json())["friends"] == [], "и в приложении окно у других пропало"


def test_the_push_text_declines_the_days_and_escapes_nothing_twice():
    import streak_scheduler as sched
    assert "1 день" in sched.rescue_push_text("Аня", 1)
    assert "3 дня" in sched.rescue_push_text("Аня", 3)
    assert "21 день" in sched.rescue_push_text("Аня", 21)
    assert "<b>Аня</b>" in sched.rescue_push_text("Аня", 5)


def test_the_job_is_scheduled():
    main = Path(__file__).resolve().parent.parent / "main.py"
    text = main.read_text(encoding="utf-8")
    assert "run_rescue_notifications" in text and 'scheduler.add_job(run_rescue_notifications, "interval", minutes=5' in text


# ---------------------------------------------------------------------------------------------------------------- центр уведомлений

def test_the_saved_person_finds_it_in_the_inbox_and_not_as_a_freeze(uid):
    from db import inbox
    helper, owner, _ = _friends(uid)
    rescue.rescue_streak(helper, owner)
    items = inbox.get_feed(owner)["items"]
    rescued = [i for i in items if i["kind"] == "friend_rescue"]
    assert len(rescued) == 1 and rescued[0]["cat"] == "friends" and "спас(ла) твою серию" in rescued[0]["title"]
    assert "12" in rescued[0]["text"]
    assert not [i for i in items if i["kind"] == "streak_freeze"], "день вернул друг — это не заморозка"


# ---------------------------------------------------------------------------------------------------------------- окно в приложении

def test_window_markup_is_a_hidden_dialog_with_both_buttons():
    start = INDEX.index('id="rescueHelpOverlay"')
    block = INDEX[start:INDEX.index('id="appTourOverlay"')]
    assert re.search(r'<div class="freeze-help-overlay rescue-help-overlay" id="rescueHelpOverlay" hidden aria-hidden="true">', INDEX)
    for needle in ('role="dialog"', 'aria-modal="true"', 'aria-labelledby="rescueHelpTitle"', 'id="rescueHelpAvatar"', 'class="freeze-help-badge rescue-help-badge"',
                   'class="rescue-help-crack"', 'id="rescueHelpTitle"', 'id="rescueHelpSub"', 'id="rescueHelpSend"', 'id="rescueHelpSkip"', "Напомнить позже"):
        assert needle in block, needle
    assert block.count('class="freeze-spark') == 8, "искры при спасении"


def test_the_window_words_do_not_repeat_each_other():
    """Заголовок, подпись и кнопки не повторяют слов (правило владельца, tests/test_window_copy.py)."""
    start = APP_JS.index("function renderRescueHelp()")
    body = APP_JS[start:APP_JS.index("function openRescueHelp(", start)]
    offer_title = "Мария потерял(а) серию в 12 дней. Но её ещё можно спасти!"
    offer_sub = re.search(r'"(Вернуть её ты успеешь[^"]+)"', body).group(1)
    send = re.search(r'send\.textContent = "(Восстановить бесплатно)"', body).group(1)
    skip = re.search(r'id="rescueHelpSkip" type="button">([^<]+)<', INDEX).group(1)
    assert not shared(offer_title, offer_sub, send, skip)
    sent_title = re.search(r'textContent = "(Серия возвращена!)"', body).group(1)
    sent_sub = "🔥 13 снова горит у друга. Это сделал ты."
    sent_send = re.search(r'send\.textContent = "(Отлично)"', body).group(1)
    assert not shared(sent_title, sent_sub, sent_send)


def test_the_cracked_flame_marker_sits_right_of_the_name_and_a_tap_opens_the_window():
    row = APP_JS[APP_JS.index("function friendRowHtml("):][:1400]
    assert 'friendNameHtml(f, "Друг", friendRescueHtml(f) + friendFreezeHtml(f))' in row
    icon = APP_JS[APP_JS.index("function friendRescueHtml("):][:520]
    assert "if (!f.rescue) return" in icon and "data-rescue-help" in icon and "friend-rescue" in icon
    tap = APP_JS[APP_JS.index("function initFriendsActions()"):][:2200]
    assert tap.index("[data-rescue-help]") < tap.index("[data-freeze-help]") < tap.index("[data-nudge]")
    assert 'trk("rescue_help_open")' in tap and "lost_streak: Number(friend.rescue.lost_streak) || 0" in tap


def test_the_save_button_calls_the_server_and_never_in_the_simulation():
    body = APP_JS[APP_JS.index("async function doRescueHelp()"):][:1700]
    assert 'api(`/api/users/${Number(s.friend.telegram_id)}/rescue`, { method: "POST", body: JSON.stringify({}) })' in body
    assert body.index("if (s.sim)") < body.index("api(`/api/users/"), "в симуляции сервер не трогаем"
    assert '"helper_limit", "friend_limit", "not_available", "not_friends"' in body and "loadFriendsCard()" in body
    assert 'step = "sent"' in body and 'haptic("success")' in body and "playChime()" in body


def test_the_window_opens_at_launch_before_the_freeze_one_and_remembers_snoozing():
    popup = APP_JS[APP_JS.index("async function maybeShowRescueHelp("):][:1900]
    assert "rescueHelpPrompted" in popup and "introChainPending()" in popup and "celebrationOverlayOpen()" in popup
    assert "RESCUE_REMIND_MS" in popup and "adam_rescue_help_" in popup and 'trk("rescue_help_shown")' in popup
    assert "5 * 3600 * 1000" in APP_JS
    launch = APP_JS[APP_JS.index("postBootstrapInitDone = true;"):][:700]
    assert launch.index("rescueHelpReady = maybeShowRescueHelp()") < launch.index("maybeShowFreezeHelp()")
    freeze = APP_JS[APP_JS.index("async function maybeShowFreezeHelp("):][:900]
    assert "await rescueHelpReady;" in freeze and "if (rescueHelp) return;" in freeze
    assert '"rescueHelpOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:800]
    assert "initRescueHelp();" in APP_JS


def test_both_scenes_are_in_the_simulation_lab():
    from db.sim_lab import SCENES
    keys = [row[0] for row in SCENES]
    assert "rescue_help" in keys and "rescue_help_sent" in keys
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):]
    block = block[:block.index("\n};\n")]
    assert "rescue_help: () => openRescueHelp({ sim: true, friend: SIM_RESCUE_FRIEND })," in block
    assert "rescue_help_sent: () => openRescueHelp({ sim: true, friend: SIM_RESCUE_FRIEND, sent: true, restored: 13 })," in block
    for forbidden in ("api(", "fetch(", "applyActionPatch", "bootstrap", "claim"):
        assert forbidden not in block, forbidden
    assert "closeRescueHelp(true);" in APP_JS[APP_JS.index("function playSimScene()"):][:400]


def test_styles_are_light_for_weak_webviews_and_sit_before_the_chest_block():
    css = CSS[CSS.index("Спасение серии друга (app.js::openRescueHelp)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    for needle in (".friend-rescue{", ".rescue-help-overlay{", ".rescue-help-send{", '.rescue-help-card[data-state="sent"] .rescue-help-crack{display:none}'):
        assert needle in css, needle
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in css, forbidden
    assert ".freeze-help-overlay[hidden]{display:none !important}" in CSS


def test_events_and_the_feature_are_registered():
    from db.tracking import EVENT_LABELS
    from webapp.feature_map import FEATURES
    for name in ("rescue_help_shown", "rescue_help_open", "rescue_sent"):
        assert name in EVENT_LABELS
    feature = next(f for f in FEATURES if f.id == "rescue")
    assert "rescueHelp" in feature.screens and "POST /api/users/{telegram_id}/rescue" in feature.api and "GET /api/rescue-help" in feature.api
    for name in re.findall(r'trk\("(rescue_\w+)"', APP_JS):
        assert name in feature.events, name


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_window_texts_are_built_from_the_friend_and_the_step():
    text = APP_JS.replace("\r\n", "\n")

    def _function(header):
        start = text.index(header)
        return text[start:text.index("\n}\n", start) + 3]

    script = r"""
const els = {};
const el = (id) => (els[id] = els[id] || { textContent: "", dataset: {}, className: "", innerHTML: "", disabled: false });
const document = { getElementById: el };
const avatarFrameClass = () => "frame-default";
const avatarInner = () => "";
const pairDaysWord = (n) => `${n} дней`;
let rescueHelp = null;
%s
const out = {};
rescueHelp = { friend: { first_name: "Мария", lost_streak: 12 }, sim: true, step: "offer", busy: false, restored: 0 };
renderRescueHelp();
out.offer = { state: els.rescueHelpCard.dataset.state, title: els.rescueHelpTitle.textContent, button: els.rescueHelpSend.textContent, disabled: els.rescueHelpSend.disabled };
rescueHelp.busy = true; renderRescueHelp(); out.busy = els.rescueHelpSend.disabled;
rescueHelp = { friend: { first_name: "Мария", lost_streak: 12 }, sim: true, step: "sent", busy: false, restored: 13 };
renderRescueHelp();
out.sent = { state: els.rescueHelpCard.dataset.state, title: els.rescueHelpTitle.textContent, sub: els.rescueHelpSub.textContent, button: els.rescueHelpSend.textContent };
process.stdout.write(JSON.stringify(out));
""" % _function("function renderRescueHelp() {")
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert "в 12 дней" in out["offer"]["title"], "«в 12 дней» не рвётся на две строки"
    out["offer"]["title"] = out["offer"]["title"].replace(" ", " ")
    assert out["offer"] == {"state": "offer", "title": "Мария потерял(а) серию в 12 дней. Но её ещё можно спасти!", "button": "Восстановить бесплатно", "disabled": False}
    assert out["busy"] is True
    assert out["sent"]["state"] == "sent" and out["sent"]["title"] == "Серия возвращена!"
    assert out["sent"]["sub"] == "🔥 13 снова горит у друга. Это сделал ты." and out["sent"]["button"] == "Отлично"
