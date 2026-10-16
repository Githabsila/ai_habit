"""
Приглашения друзей (16.10): 100 Адам коинов пригласившему и 50 новичку — только за ПЕРВЫХ ДВУХ приглашённых на человека, а дальше только дружба.
Защита от накрутки: награда лишь новому аккаунту, один приглашённый засчитывается один раз навсегда, совсем новый пригласивший ждёт первой
отметки друга, выплаты можно поставить на паузу или перевести на «после первой отметки», админ видит признаки и отзывает награду.
Плюс мини-рассылка «позови друга» на 2-й/4-й/6-й день и уведомление о награде, выплаченной после первой отметки.
"""
import itertools
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from aiogram.exceptions import TelegramForbiddenError

import referral_mailing as rm
from db import add_habit, add_user, complete_habit, delete_feature_flag, get_user, set_feature_flag
from db import referrals
from db.core import connect
from db.friends import are_friends
from db.settings import set_quiet_hours, toggle_reminders
from db.streak import set_timezone
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
ADMIN_HTML = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")

_uid_counter = itertools.count(843_000_000, 100)
FLAGS = (referrals.PAUSE_FLAG, referrals.AFTER_MARK_FLAG, referrals.MAILING_FLAG)
NOW = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)      # середина обеденного окна рассылки (13:00–14:00 по UTC)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def clean_flags():
    for key in FLAGS:
        delete_feature_flag(key)
    yield
    for key in FLAGS:
        delete_feature_flag(key)


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _row(query, params=()):
    conn = connect()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def _ago(**delta):
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def _old(user_id, name="Старожил", days=5):
    """Давний игрок: анкета пройдена, в ADAM не первый день."""
    add_user(user_id, f"u{user_id}", name)
    _sql("UPDATE users SET access_status='approved', created_at=? WHERE telegram_id=?", (_ago(days=days), user_id))
    return user_id


def _newcomer(user_id, name="Новичок", username=True):
    """Только что нажал /start: аккаунт создан сейчас, анкета не пройдена."""
    add_user(user_id, f"n{user_id}" if username else None, name)
    return user_id


def _xp(user_id):
    return _row("SELECT xp FROM users WHERE telegram_id=?", (user_id,))["xp"]


def _invite(inviter, invitee, via="invite"):
    return referrals.register_invite(inviter, invitee, via)


def _mark_first_habit(user_id, title="Зарядка"):
    habit_id = add_habit(user_id, title)
    assert habit_id
    complete_habit(habit_id)


# ------------------------------------------------------------------------------------------------ награда и лимит

def test_first_two_invites_pay_both_sides_and_the_third_is_only_a_friend(uid):
    inviter = _old(uid)
    before = _xp(inviter)
    first, second, third = (_newcomer(uid + i) for i in (1, 2, 3))

    one = _invite(inviter, first)
    assert one["counted"] and one["status"] == "paid" and (one["inviter_coins"], one["invitee_coins"]) == (100, 50)
    assert (one["slot"], one["left"], one["limit"]) == (1, 1, 2)
    assert _xp(inviter) == before + 100 and _xp(first) == 50

    two = _invite(inviter, second)
    assert two["status"] == "paid" and two["slot"] == 2 and two["left"] == 0
    assert _xp(inviter) == before + 200

    three = _invite(inviter, third)
    assert three["counted"] and three["status"] == "no_reward" and three["reason"] == "limit"
    assert (three["inviter_coins"], three["invitee_coins"]) == (0, 0)
    assert _xp(inviter) == before + 200 and _xp(third) == 0
    # человек всё равно записан приглашённым (счётчик и «кто позвал»), просто без награды
    assert get_user(third)["referrer_id"] == inviter and get_user(inviter)["referrals"] == 3


def test_reward_summary_counts_slots(uid):
    inviter = _old(uid)
    assert referrals.reward_summary(inviter)["left"] == 2
    _invite(inviter, _newcomer(uid + 1))
    summary = referrals.reward_summary(inviter)
    assert (summary["used"], summary["left"], summary["earned"], summary["invited"], summary["mode"]) == (1, 1, 100, 1, "now")
    _invite(inviter, _newcomer(uid + 2))
    _invite(inviter, _newcomer(uid + 3))
    summary = referrals.reward_summary(inviter)
    assert (summary["used"], summary["left"], summary["earned"], summary["invited"]) == (2, 0, 200, 3)


def test_five_simultaneous_joins_cannot_get_around_the_limit(uid):
    inviter = _old(uid)
    people = [_newcomer(uid + i) for i in range(1, 6)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda person: _invite(inviter, person), people))
    assert sorted(r["status"] for r in results) == ["no_reward"] * 3 + ["paid"] * 2
    assert _xp(inviter) == 200


# ------------------------------------------------------------------------------------------------ что не считается приглашением

def test_self_repeat_and_second_inviter_do_not_count(uid):
    inviter, other = _old(uid), _old(uid + 1)
    person = _newcomer(uid + 2)
    assert _invite(inviter, inviter) == {"counted": False, "reason": "self", "inviter_coins": 0, "invitee_coins": 0}
    assert _invite(inviter, person)["status"] == "paid"
    repeat = _invite(inviter, person)
    assert repeat["counted"] is False and repeat["reason"] == "already_invited"
    stolen = _invite(other, person)
    assert stolen["counted"] is False and get_user(person)["referrer_id"] == inviter
    assert _xp(inviter) == 100 and _xp(other) == 0 and _xp(person) == 50


def test_someone_who_already_uses_adam_is_not_a_new_player(uid):
    inviter = _old(uid)
    veteran = _old(uid + 1, "Опытный")
    result = _invite(inviter, veteran)
    assert result["counted"] is False and result["reason"] == "not_new"
    slow = _newcomer(uid + 2)
    _sql("UPDATE users SET created_at=? WHERE telegram_id=?", (_ago(minutes=referrals.FRESH_ACCOUNT_MIN + 5), slow))
    assert _invite(inviter, slow)["reason"] == "not_fresh"
    assert _xp(inviter) == 0 and _xp(veteran) == 0 and get_user(veteran)["referrer_id"] is None
    assert referrals.reward_summary(inviter)["left"] == 2


def test_rejections_are_recorded_for_analytics(uid):
    inviter = _old(uid)
    _invite(inviter, _old(uid + 1))
    row = _row("SELECT step, meta FROM user_events WHERE user_id=? AND name='referral_rejected'", (uid + 1,))
    assert row["step"] == "not_new" and str(inviter) in row["meta"]


def test_banned_or_unknown_inviter_gets_nothing(uid):
    banned = _old(uid)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (banned,))
    assert _invite(banned, _newcomer(uid + 1))["reason"] == "inviter_banned"
    assert _invite(uid + 99, _newcomer(uid + 2))["reason"] == "inviter_unknown"
    assert _xp(uid + 1) == 0 and _xp(uid + 2) == 0


def test_deleting_the_account_and_coming_back_does_not_repeat_the_reward(uid):
    inviter = _old(uid)
    person = _newcomer(uid + 1)
    assert _invite(inviter, person)["status"] == "paid"
    _sql("DELETE FROM users WHERE telegram_id=?", (person,))                 # «удалил аккаунт»
    _newcomer(person)                                                        # …и снова /start
    again = _invite(inviter, person)
    assert again["counted"] is False and again["reason"] == "already_invited"
    assert _xp(inviter) == 100 and _xp(person) == 0


# ------------------------------------------------------------------------------------------------ когда платим

def test_a_brand_new_inviter_is_paid_after_the_friends_first_mark(uid):
    inviter = _newcomer(uid, "Аня")                                           # аккаунту меньше суток — типичная схема накрутки
    person = _newcomer(uid + 1)
    result = _invite(inviter, person)
    assert result["status"] == "pending" and result["reason"] == "new_inviter" and result["inviter_coins"] == 0
    assert _xp(inviter) == 0 and _xp(person) == 0
    assert referrals.reward_summary(inviter)["used"] == 1                     # слот уже занят: два «ожидающих» не обойдут лимит
    assert "new_inviter" in _row("SELECT flags FROM referral_invites WHERE invitee_id=?", (person,))["flags"]

    xp_before = _xp(person)
    _mark_first_habit(person)
    row = _row("SELECT status, activated_at, paid_at FROM referral_invites WHERE invitee_id=?", (person,))
    assert row["status"] == "paid" and row["activated_at"] and row["paid_at"]
    assert _xp(inviter) == 100 and _xp(person) >= xp_before + 50

    _mark_first_habit(person, "Чтение")                                        # вторая отметка награду не повторяет
    assert _xp(inviter) == 100


def test_after_mark_mode_pays_only_when_the_friend_really_marks(uid):
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1)
    result = _invite(inviter, person)
    assert result["status"] == "pending" and result["reason"] == "after_mark" and _xp(inviter) == 0
    assert referrals.reward_summary(inviter)["mode"] == "after_mark"
    _mark_first_habit(person)
    assert _xp(inviter) == 100
    set_feature_flag(referrals.AFTER_MARK_FLAG, False)
    assert referrals.reward_summary(inviter)["mode"] == "now"


def test_pause_stops_payouts_and_does_not_burn_slots(uid):
    set_feature_flag(referrals.PAUSE_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1)
    result = _invite(inviter, person)
    assert result["counted"] and result["status"] == "no_reward" and result["reason"] == "paused"
    summary = referrals.reward_summary(inviter)
    assert summary["paused"] and summary["left"] == 0 and summary["used"] == 0 and _xp(inviter) == 0
    set_feature_flag(referrals.PAUSE_FLAG, False)
    assert referrals.reward_summary(inviter)["left"] == 2


def test_a_waiting_reward_survives_a_pause_and_is_paid_after_it(uid):
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1)
    _invite(inviter, person)
    set_feature_flag(referrals.PAUSE_FLAG, True)
    _mark_first_habit(person)
    assert _row("SELECT status FROM referral_invites WHERE invitee_id=?", (person,))["status"] == "pending" and _xp(inviter) == 0
    set_feature_flag(referrals.PAUSE_FLAG, False)
    _mark_first_habit(person, "Чтение")
    assert _row("SELECT status FROM referral_invites WHERE invitee_id=?", (person,))["status"] == "paid" and _xp(inviter) == 100


def test_a_friend_who_never_marks_frees_the_slot_after_two_weeks(uid):
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1)
    _invite(inviter, person)
    assert referrals.reward_summary(inviter)["used"] == 1
    _sql("UPDATE referral_invites SET created_at=? WHERE invitee_id=?", (_ago(days=referrals.PENDING_TTL_DAYS + 1), person))
    assert referrals.reward_summary(inviter)["used"] == 0
    _mark_first_habit(person)                                                  # поздно: награда сгорела
    row = _row("SELECT status, reason FROM referral_invites WHERE invitee_id=?", (person,))
    assert (row["status"], row["reason"]) == ("expired", "ttl") and _xp(inviter) == 0


def test_inviter_banned_while_waiting_gets_nothing(uid):
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1)
    _invite(inviter, person)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (inviter,))
    _mark_first_habit(person)
    row = _row("SELECT status, reason FROM referral_invites WHERE invitee_id=?", (person,))
    assert (row["status"], row["reason"]) == ("no_reward", "inviter_banned") and _xp(inviter) == 0


# ------------------------------------------------------------------------------------------------ признаки и отзыв награды

def test_suspicious_signs_are_recorded_but_do_not_block_the_first_two(uid):
    inviter = _old(uid)
    anon = _newcomer(uid + 1, username=False)
    one = _invite(inviter, anon)
    assert one["status"] == "paid" and "no_username" in one["flags"]
    _invite(inviter, _newcomer(uid + 2))
    third = _invite(inviter, _newcomer(uid + 3))
    assert "burst" in third["flags"]                                           # три приглашённых за час


def test_admin_overview_lists_invites_with_signs_and_inactive_friends(uid):
    inviter, person = _old(uid, "Аня"), _newcomer(uid + 1, "Боря")
    _invite(inviter, person)
    _sql("UPDATE referral_invites SET created_at=? WHERE invitee_id=?", (_ago(days=referrals.INACTIVE_AFTER_DAYS + 1), person))
    data = referrals.admin_overview(limit=500)
    item = next(i for i in data["items"] if i["invitee"]["id"] == person)
    assert item["inviter"]["name"] == "Аня" and item["invitee"]["name"] == "Боря" and item["status"] == "paid" and item["coins"] == 150
    assert item["activated"] is False and any(f["key"] == "inactive" for f in item["flags"]) and item["can_revoke"]
    assert data["limit"] == 2 and data["reward"] == 100 and data["invitee_reward"] == 50 and data["flags"]["pause"] == referrals.PAUSE_FLAG
    assert data["counts"]["paid"] >= 1 and data["coins_paid"] >= 150


def test_revoke_takes_the_coins_back_but_the_slot_stays_used(uid):
    inviter, person = _old(uid), _newcomer(uid + 1)
    _invite(inviter, person)
    _sql("UPDATE users SET xp=10 WHERE telegram_id=?", (person,))             # новичок уже потратил почти всё
    result = referrals.revoke_invite(person, uid + 50)
    assert result["ok"] and result["taken"][inviter] == 100 and result["taken"][person] == 10
    assert _xp(inviter) == 0 and _xp(person) == 0
    assert _row("SELECT status FROM referral_invites WHERE invitee_id=?", (person,))["status"] == "revoked"
    assert referrals.reward_summary(inviter)["used"] == 1                     # слот сгорел: «отозвали — позвали ещё раз» не работает
    assert referrals.revoke_invite(person, uid + 50) == {"error": "not_revocable"}
    assert referrals.revoke_invite(uid + 98, uid + 50) == {"error": "not_found"}


# ------------------------------------------------------------------------------------------------ /start

def _start_message(user_id, payload):
    return SimpleNamespace(
        text=f"/start {payload}" if payload else "/start",
        from_user=SimpleNamespace(id=user_id, username=f"u{user_id}", first_name="Новичок"),
        answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.fixture
def start_handler(monkeypatch):
    import handlers.start as start_module
    monkeypatch.setattr(start_module, "begin_survey", AsyncMock())
    return start_module.start


def _texts(mock):
    return " ".join(str(c.args[-1] if c.args else c.kwargs.get("text", "")) for c in mock.await_args_list)


async def test_start_tells_both_sides_about_the_reward(start_handler, uid):
    inviter, person = _old(uid, "Аня"), uid + 1
    message = _start_message(person, f"friend_{inviter}")
    await start_handler(message, None)
    push = message.bot.send_message.await_args
    assert push.args[0] == inviter and "+100 Адам коинов" in push.args[1] and "1 из 2" in push.args[1]
    assert "+50 Adam Coin" in _texts(message.answer)
    assert are_friends(inviter, person) and _xp(inviter) == 100 and _xp(person) == 50
    assert _row("SELECT notified_at FROM referral_invites WHERE invitee_id=?", (person,))["notified_at"]


async def test_start_for_the_third_friend_befriends_without_a_reward(start_handler, uid):
    inviter = _old(uid, "Аня")
    for i in (1, 2):
        await start_handler(_start_message(uid + i, str(inviter)), None)
    message = _start_message(uid + 3, str(inviter))
    await start_handler(message, None)
    assert are_friends(inviter, uid + 3) and _xp(inviter) == 200 and _xp(uid + 3) == 0
    push = message.bot.send_message.await_args
    assert push.args[0] == inviter and "Адам коинов" not in push.args[1]
    assert "Бонус" not in _texts(message.answer)


async def test_start_of_a_brand_new_inviter_promises_the_reward_for_the_first_mark(start_handler, uid):
    inviter = _newcomer(uid, "Аня")
    message = _start_message(uid + 1, f"friend_{inviter}")
    await start_handler(message, None)
    push = message.bot.send_message.await_args
    assert "придут, когда" in push.args[1] and "отметит первую привычку" in push.args[1]
    assert _xp(inviter) == 0 and _xp(uid + 1) == 0 and "Бонус" not in _texts(message.answer)
    assert are_friends(inviter, uid + 1)


async def test_plain_link_no_longer_pays_people_who_already_use_adam(start_handler, uid):
    inviter, veteran = _old(uid), _old(uid + 1, "Опытный")
    await start_handler(_start_message(veteran, str(inviter)), None)
    assert are_friends(inviter, veteran)                                       # дружба по ссылке остаётся
    assert _xp(inviter) == 0 and _xp(veteran) == 0 and get_user(veteran)["referrer_id"] is None


async def test_joining_is_recorded_for_analytics(start_handler, uid):
    inviter = _old(uid)
    await start_handler(_start_message(uid + 1, str(inviter)), None)
    row = _row("SELECT step, meta FROM user_events WHERE user_id=? AND name='referral_joined'", (uid + 1,))
    assert row["step"] == "paid"
    assert _row("SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND name='referral_paid'", (inviter,))["n"] == 1


# ------------------------------------------------------------------------------------------------ рассылка «позови друга»

def _mail_user(user_id, created="2026-10-18 09:00:00", tz="UTC"):
    add_user(user_id, f"m{user_id}", "Игрок")
    _sql("UPDATE users SET access_status='approved', created_at=? WHERE telegram_id=?", (created, user_id))
    set_timezone(user_id, tz)
    return user_id


def _plan(user_id, now=NOW, created="2026-10-18 09:00:00"):
    return rm.mailing_plan(user_id, created, now)


def test_mailing_steps_are_every_two_days_in_the_first_week():
    assert [rm.step_for_age(age) for age in range(0, 10)] == [None, None, 0, 0, 1, 1, 2, 2, None, None]


def test_the_first_message_follows_the_owners_wording(uid):
    plan = _plan(_mail_user(uid))
    assert plan["send"] and plan["step"] == 0 and plan["used"] == 0
    assert "Наше приложение интереснее использовать вместе с друзьями" in plan["text"]
    assert "100 Адам коинов сразу" in plan["text"] and "первых двух" in plan["text"]


def test_each_step_has_its_own_text_and_the_grace_day_repeats_the_step(uid):
    user = _mail_user(uid)
    created = lambda days: (NOW - timedelta(days=days)).strftime("%Y-%m-%d 09:00:00")
    texts = [_plan(user, created=created(days)) for days in (2, 4, 6)]
    assert [t["step"] for t in texts] == [0, 1, 2] and len({t["text"] for t in texts}) == 3
    assert _plan(user, created=created(3))["step"] == 0
    assert _plan(user, created=created(1))["reason"] == "not_due" and _plan(user, created=created(8))["reason"] == "not_due"


def test_one_taken_slot_changes_the_text_and_no_slots_stop_the_mailing(uid):
    inviter = _mail_user(uid)
    _invite(inviter, _newcomer(uid + 1))
    plan = _plan(inviter)
    assert plan["send"] and plan["used"] == 1 and "Это последняя награда" in rm.compose_text(0, 1) and plan["text"] == rm.compose_text(0, 1)
    _invite(inviter, _newcomer(uid + 2))
    assert _plan(inviter) == {"send": False, "reason": "slots_used"}


def test_mode_changes_the_promise_in_the_text(uid):
    assert "когда друг отметит первую привычку" in rm.compose_text(0, 0, "after_mark")
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    assert "когда друг отметит первую привычку" in _plan(_mail_user(uid))["text"]


def test_only_the_lunch_window_by_the_persons_own_clock(uid):
    user = _mail_user(uid)
    at = lambda hour, minute=0: NOW.replace(hour=hour, minute=minute)
    assert _plan(user, at(12, 59))["reason"] == "outside_window" and _plan(user, at(14))["reason"] == "outside_window"
    assert _plan(user, at(13))["send"] and _plan(user, at(13, 59))["send"]
    tokyo = _mail_user(uid + 1, tz="Asia/Tokyo")                               # 04:00 UTC = 13:00 в Токио
    assert _plan(tokyo, NOW.replace(hour=4))["send"] and _plan(tokyo, NOW)["reason"] == "outside_window"


def test_no_mailing_when_reminders_are_off_or_quiet_or_switched_off(uid):
    off = _mail_user(uid)
    toggle_reminders(off)
    assert _plan(off) == {"send": False, "reason": "reminders_off"}
    quiet = _mail_user(uid + 1)
    set_quiet_hours(quiet, 13, 14)
    assert _plan(quiet)["reason"] == "quiet_hours"
    paused = _mail_user(uid + 2)
    set_feature_flag(referrals.PAUSE_FLAG, True)
    assert _plan(paused)["reason"] == "paused"
    set_feature_flag(referrals.PAUSE_FLAG, False)
    set_feature_flag(referrals.MAILING_FLAG, False)
    assert _plan(paused)["reason"] == "disabled"
    set_feature_flag(referrals.MAILING_FLAG, True, 100)
    assert _plan(paused)["send"]
    set_feature_flag(referrals.MAILING_FLAG, True, 0)
    assert _plan(paused)["reason"] == "disabled"


def test_mailing_waits_after_other_messages(uid):
    busy = _mail_user(uid)
    _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?,?,?,?)",
         (busy, "habit_checkpoint_12", "x", (NOW - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")))
    assert _plan(busy)["reason"] == "bot_wrote_recently"
    _sql("UPDATE notification_log SET sent_at=? WHERE user_id=?", ((NOW - timedelta(minutes=70)).strftime("%Y-%m-%d %H:%M:%S"), busy))
    assert _plan(busy)["send"]

    adam = _mail_user(uid + 1)
    _sql("INSERT INTO adam_checkins(user_id, day, slot, status, scenario, source, created_at) VALUES (?,?,?,?,?,?,?)",
         (adam, "2026-10-20", "day", "sent", "steady", "push", (NOW - timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S")))
    assert _plan(adam)["reason"] == "adam_wrote_recently"

    recent_mail = _mail_user(uid + 2)
    _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?,?,?,?)",
         (recent_mail, rm.KIND, rm.KIND, (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")))
    assert _plan(recent_mail)["reason"] == "too_soon_after_previous"
    _sql("UPDATE notification_log SET sent_at=? WHERE user_id=?", ((NOW - timedelta(hours=45)).strftime("%Y-%m-%d %H:%M:%S"), recent_mail))
    assert _plan(recent_mail)["send"]


def test_only_people_of_the_first_week_who_can_get_messages_are_candidates(uid):
    week = _mail_user(uid, created=_ago(days=3))
    _mail_user(uid + 1, created=_ago(days=12))                                 # давно не новичок
    _mail_user(uid + 2, created=_ago(hours=5))                                 # слишком свежий
    blocked = _mail_user(uid + 3, created=_ago(days=3))
    _sql("UPDATE users SET bot_blocked_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (blocked,))
    unapproved = _mail_user(uid + 4, created=_ago(days=3))
    _sql("UPDATE users SET access_status='new' WHERE telegram_id=?", (unapproved,))
    ids = {user_id for user_id, _ in referrals.mailing_candidates()}
    assert week in ids and not ids & {uid + 1, uid + 2, blocked, unapproved}


def _bot(**kwargs):
    return SimpleNamespace(token="123:rm-test", get_me=AsyncMock(return_value=SimpleNamespace(username="adam_test_bot")),
                           send_message=AsyncMock(**kwargs))


def _sent_to(bot, user_id):
    return [c for c in bot.send_message.await_args_list if c.args[0] == user_id]


async def test_run_sends_each_step_once_with_a_working_share_button(uid, monkeypatch):
    monkeypatch.setattr(rm, "SEND_FROM_MIN", 0)
    monkeypatch.setattr(rm, "SEND_TO_MIN", 24 * 60)
    user = _mail_user(uid, created=_ago(days=2))
    bot = _bot()
    await rm.run_referral_mailing(bot)
    calls = _sent_to(bot, user)
    assert len(calls) == 1 and "Наше приложение интереснее" in calls[0].args[1]
    button = calls[0].kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == rm.BUTTON_TEXT
    query = parse_qs(urlparse(button.url).query)
    assert urlparse(button.url).netloc == "t.me" and query["url"] == [rm.SHARE_TEXT]
    assert query["text"] == [f"https://t.me/adam_test_bot?start=friend_{user}"]           # подпись первой, ссылка под ней
    await rm.run_referral_mailing(bot)
    assert len(_sent_to(bot, user)) == 1                                       # тот же шаг второй раз не уходит
    assert _row("SELECT COUNT(*) AS n FROM notification_log WHERE user_id=? AND category=?", (user, rm.KIND))["n"] == 1
    assert _row("SELECT step FROM user_events WHERE user_id=? AND name='referral_mail'", (user,))["step"] == "step0"


async def test_failed_delivery_is_retried_and_a_blocked_bot_is_remembered(uid, monkeypatch):
    monkeypatch.setattr(rm, "SEND_FROM_MIN", 0)
    monkeypatch.setattr(rm, "SEND_TO_MIN", 24 * 60)
    flaky = _mail_user(uid, created=_ago(days=2))
    bot = _bot(side_effect=RuntimeError("сеть"))
    await rm.run_referral_mailing(bot)
    assert _row("SELECT COUNT(*) AS n FROM notification_log WHERE user_id=? AND category=?", (flaky, rm.KIND))["n"] == 0     # резерв снят
    bot.send_message.side_effect = None
    await rm.run_referral_mailing(bot)
    assert len(_sent_to(bot, flaky)) >= 1

    gone = _mail_user(uid + 1, created=_ago(days=2))
    forbidden = TelegramForbiddenError(method=SimpleNamespace(), message="Forbidden: bot was blocked by the user")
    await rm.run_referral_mailing(_bot(side_effect=forbidden))
    assert _row("SELECT bot_blocked_at FROM users WHERE telegram_id=?", (gone,))["bot_blocked_at"]


async def test_after_mark_payment_is_announced_once(uid):
    set_feature_flag(referrals.AFTER_MARK_FLAG, True)
    inviter, person = _old(uid), _newcomer(uid + 1, "Боря")
    _invite(inviter, person)
    assert not [p for p in referrals.unnotified_payments(500) if p["inviter_id"] == inviter]
    _mark_first_habit(person)
    bot = _bot()
    await rm.run_referral_notices(bot)
    calls = _sent_to(bot, inviter)
    assert len(calls) == 1 and "Боря" in calls[0].args[1] and "+100 Адам коинов" in calls[0].args[1] and "Осталось приглашений с наградой: 1" in calls[0].args[1]
    await rm.run_referral_notices(bot)
    assert len(_sent_to(bot, inviter)) == 1


async def test_payments_announced_by_start_are_not_announced_again(start_handler, uid):
    inviter = _old(uid)
    await start_handler(_start_message(uid + 1, str(inviter)), None)           # награда «сразу» — сообщил /start
    _mark_first_habit(uid + 1)
    bot = _bot()
    await rm.run_referral_notices(bot)
    assert _sent_to(bot, inviter) == []


def test_mailing_texts_are_neutral_and_clean():
    for used in (0, 1):
        for mode in ("now", "after_mark"):
            for step in range(3):
                text = rm.compose_text(step, used, mode)
                assert not re.search(r"[{}]|None|  ", text), text
                assert not re.search(r"\b\w+(ла|лась)\b", text), f"род в тексте: {text}"
                assert "100 Адам коинов" in text and "👇" in text


# ------------------------------------------------------------------------------------------------ админка и Mini App

def _headers(user_id):
    return {"Authorization": f"tma {sign_init_data(user_id)}", "Content-Type": "application/json"}


async def test_admin_routes_are_admin_only_and_work(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin = uid + 50
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    stranger = _old(uid + 60)
    assert (await client.get("/api/admin/referrals", headers=_headers(stranger))).status == 403
    assert (await client.post("/api/admin/referrals/1/revoke", headers=_headers(stranger))).status == 403
    assert (await client.post("/api/admin/referrals/preview", headers=_headers(stranger), json={"step": 0})).status == 403

    inviter, person = _old(uid), _newcomer(uid + 1)
    _invite(inviter, person)
    data = await (await client.get("/api/admin/referrals", headers=_headers(admin))).json()
    assert any(i["invitee"]["id"] == person for i in data["items"]) and data["paused"] is False and data["mailing"]["on"] is True

    assert (await client.post("/api/admin/referrals/oops/revoke", headers=_headers(admin))).status == 400
    assert (await client.post(f"/api/admin/referrals/{uid + 97}/revoke", headers=_headers(admin))).status == 404
    ok = await client.post(f"/api/admin/referrals/{person}/revoke", headers=_headers(admin))
    assert ok.status == 200 and (await ok.json())["ok"]
    assert (await client.post(f"/api/admin/referrals/{person}/revoke", headers=_headers(admin))).status == 409


async def test_admin_can_send_the_mailing_to_themselves(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin = uid + 50
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    bot = _bot()
    client.app["bot"] = bot
    bad = await client.post("/api/admin/referrals/preview", headers=_headers(admin), json={"step": 7})
    assert bad.status == 400
    ok = await client.post("/api/admin/referrals/preview", headers=_headers(admin), json={"step": 1, "second": True})
    assert ok.status == 200
    call = bot.send_message.await_args
    assert call.args[0] == admin and call.args[1].startswith("🧪 Тест · ") and "Это последняя награда" not in call.args[1]
    assert str(admin) in call.kwargs["reply_markup"].inline_keyboard[0][0].url.replace("%3D", "=")


async def test_friends_payload_carries_the_reward_state(client, uid):
    me = _old(uid)
    data = await (await client.get("/api/friends", headers=_headers(me))).json()
    assert data["invite_reward"]["left"] == 2 and data["invite_reward"]["coins"] == 100 and data["invite_reward"]["limit"] == 2
    _invite(me, _newcomer(uid + 1))
    data = await (await client.get("/api/friends", headers=_headers(me))).json()
    assert data["invite_reward"]["left"] == 1 and data["invite_reward"]["used"] == 1


def test_mini_app_mentions_the_reward_only_while_slots_remain():
    assert "invite_reward" in APP and "Number(reward.left) > 0" in APP and "первых двух приглашённых" in APP


def test_admin_panel_has_the_referral_card():
    for needle in ("referralsBox", "/api/admin/referrals", "/revoke", "/api/admin/referrals/preview", "data-ref-switch", "data-ref-revoke"):
        assert needle in ADMIN_HTML, needle
