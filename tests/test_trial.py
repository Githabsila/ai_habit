"""
Триал ADAM (ТЗ владельца 30.08 п.13 / 12.10): состояние человека (db/trial.py), каталог сообщений (trial_messages.py) и их доставка
(subscription_scheduler.py). День триала — по местному календарю; скидка $1.99 до 4-го дня; сообщения по дню, часу и отметкам;
тумблеры, дневной лимит, тихие часы; тестировщики вне сценария ничего не получают.
"""
import itertools
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import subscription_scheduler as sched
import trial_messages as tm
from db import add_user, get_user, set_timezone, start_trial_mode
from db import trial as trial_db
from db.access import shift_trial_day
from db.access_state import set_trial_start
from db.core import connect
from db.subscription import get_subscription_price_stars, record_subscription_payment
from tests.test_adam_texts import FORMAL, GENDERED, _problems

import config

_counter = itertools.count(859_000_000, 100)
ADMIN = 777
UTC = timezone.utc


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _person(uid_, day=1, tz=None, name="Аня"):
    """Аккаунт в режиме триала; сегодня (по его часовому поясу) — день `day`."""
    add_user(uid_, f"u{uid_}", name)
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    if tz:
        set_timezone(uid_, tz)
    start_trial_mode(uid_, ADMIN)
    shift_trial_day(uid_, day, ADMIN)
    return uid_


def _mark(uid_, days_ago, streak_after=1, status="completed"):
    tz = ZoneInfo(trial_db.snapshot(uid_)["timezone"])
    day = (datetime.now(tz).date() - timedelta(days=days_ago)).isoformat()
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, ?, ?)", (uid_, day, status, streak_after))
    if days_ago == 0:
        _sql("UPDATE users SET streak=? WHERE telegram_id=?", (streak_after, uid_))


def _at(uid_, hour, **kw):
    """Снимок состояния на сегодня в `hour` часов по местному времени человека."""
    tz = ZoneInfo(trial_db.snapshot(uid_)["timezone"])
    now = datetime.now(tz).replace(hour=hour, minute=5, second=0, microsecond=0)
    snap = trial_db.snapshot(uid_, now.astimezone(UTC))
    snap.update(kw)
    return snap


# ---------------------------------------------------------------------------------------------------------------- дни и цена

def test_the_trial_day_follows_the_local_calendar_not_utc(uid):
    add_user(uid, "m", "Аня")
    set_timezone(uid, "Europe/Moscow")
    start_trial_mode(uid, ADMIN)
    # зарегистрировалась в 23:30 по Москве (20:30 UTC): по UTC день 1 закончился бы через 3.5 часа, по местному календарю — через 30 минут
    started = datetime(2026, 10, 20, 20, 30)
    set_trial_start(uid, started)
    assert trial_db.trial_day(uid, datetime(2026, 10, 20, 20, 45, tzinfo=UTC)) == 1           # 23:45 по Москве — всё ещё первый день
    assert trial_db.trial_day(uid, datetime(2026, 10, 20, 21, 30, tzinfo=UTC)) == 2           # 00:30 по Москве уже второй
    assert trial_db.trial_day(uid, datetime(2026, 10, 22, 12, 0, tzinfo=UTC)) == 3
    assert trial_db.trial_day(uid, datetime(2026, 10, 23, 12, 0, tzinfo=UTC)) == 4
    end = trial_db.window_end_utc(get_user(uid))
    assert end == datetime(2026, 10, 22, 21, 0), "триал кончается в полночь по Москве после третьего дня (21:00 UTC)"


def test_the_intro_price_belongs_to_the_first_purchase_on_any_day_and_never_returns(uid):
    """ТЗ 28.10 §6: вводная цена — для любой ПЕРВОЙ покупки, хоть на пятый день; «только сегодня» не говорим, потому что это неправда."""
    _person(uid, day=1)
    intro, full = config.SUBSCRIPTION_INTRO_PRICE_STARS, config.SUBSCRIPTION_RENEWAL_PRICE_STARS
    assert intro < full
    for day in (1, 2, 3, 4, 5, 9, 30):
        shift_trial_day(uid, min(day, 10), ADMIN)
        assert get_subscription_price_stars(uid) == intro, day
        assert trial_db.snapshot(uid)["intro_active"] is True
    shift_trial_day(uid, 2, ADMIN)
    record_subscription_payment(uid, months=1)
    assert get_subscription_price_stars(uid) == full, "после первой оплаты — полная цена"


@pytest.mark.parametrize("day,phase", [(1, "day1"), (2, "day2"), (3, "day3"), (4, "grace"), (5, "expired"), (8, "expired")])
def test_the_phase_follows_the_day_and_payment_wins(uid, day, phase):
    _person(uid, day=day)
    snap = trial_db.snapshot(uid)
    assert snap["day"] == day and snap["phase"] == phase and snap["scenario"] is True
    record_subscription_payment(uid, months=1)
    assert trial_db.snapshot(uid)["phase"] == "paid"


def test_the_snapshot_reads_marks_streak_and_channel_state(uid):
    _person(uid, day=3)
    _mark(uid, 1, streak_after=1)
    snap = trial_db.snapshot(uid)
    assert snap["marked_yesterday"] and not snap["marked_today"] and snap["streak"] == 0 and not snap["channel_eligible"]
    _mark(uid, 0, streak_after=2)
    snap = trial_db.snapshot(uid)
    assert snap["marked_today"] and snap["streak"] == 2 and snap["channel_eligible"] and snap["needed"] == 2
    assert snap["channel_configured"] is bool(config.CLOSED_CHANNEL_ID) and snap["channel_granted"] is False
    _sql("UPDATE users SET channel_access_granted_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (uid,))
    assert trial_db.snapshot(uid)["channel_granted"] is True


def test_the_shift_works_only_for_trial_accounts_and_is_logged(uid):
    add_user(uid, "o", "Аня")
    assert shift_trial_day(uid, 2, ADMIN) == {"error": "not_in_trial"}
    start_trial_mode(uid, ADMIN)
    assert shift_trial_day(uid, 0, ADMIN) == {"error": "invalid_day"} and shift_trial_day(uid, "x", ADMIN) == {"error": "invalid_day"}
    info = shift_trial_day(uid, 3, ADMIN)
    assert info["trial_day"] == 3
    from db import get_access_log
    assert get_access_log(uid)[0]["action"] == "trial_shift" and get_access_log(uid)[0]["days"] == 3


# ---------------------------------------------------------------------------------------------------------------- что и когда подходит

def _due(uid_, hour, **kw):
    return {m.key for m in tm.due_messages(_at(uid_, hour, **kw))}


def test_day_one_has_one_evening_goal_message(uid):
    _person(uid, day=1)
    assert _due(uid, 19) == {"d1_goal"} and _due(uid, 12) == set() and _due(uid, 22) == set()


def test_day_two_talks_goal_without_money_then_offers_after_the_first_mark(uid):
    _person(uid, day=2)
    assert _due(uid, 12) == {"d2_goal"}
    goal = tm.render("d2_goal", _at(uid, 12))
    assert not re.search(r"⭐|\$|оплат|цен|скидк|Pro", goal), "про деньги — ни слова, пока человек не отметился"
    _mark(uid, 0, streak_after=1)
    assert _due(uid, 12) == {"d2_offer"} and _due(uid, 9) == {"d2_offer"} and _due(uid, 8) == set() and _due(uid, 23) == set()
    offer = tm.render("d2_offer", _at(uid, 15))
    assert "153 ⭐" in offer and "$1.99" in offer and "$5.99" in offer and "завтра достаточно отметить привычку" in offer


def test_day_two_goal_changes_with_yesterdays_mark(uid):
    _person(uid, day=2)
    without = tm.render("d2_goal", _at(uid, 12))
    _mark(uid, 1)
    with_mark = tm.render("d2_goal", _at(uid, 12))
    assert without != with_mark and "два дня подряд будут у тебя в кармане" in with_mark and "Твоя цель сейчас" in without


def test_the_last_trial_day_writes_at_noon_and_in_the_evening(uid):
    _person(uid, day=3)
    assert _due(uid, 13) == {"d3_midday"} and _due(uid, 20) == {"d3_evening"} and _due(uid, 16) == set()
    assert "завтра доступ ко мне закроется" in tm.render("d3_midday", _at(uid, 13))
    evening = tm.render("d3_evening", _at(uid, 20))
    assert "Или все твои мысли и первые действия о дисциплине и привычках были лишь слова?" in evening and "153 ⭐" in evening


def test_the_day_three_message_tells_what_the_channel_needs(uid):
    _person(uid, day=3)
    nothing = tm.render("d3_midday", _at(uid, 13))
    assert "отметь привычки сегодня и завтра" in nothing
    _mark(uid, 1)
    assert "Отметь привычку сегодня — и после оплаты закрытый канал откроется сразу" in tm.render("d3_midday", _at(uid, 13))
    _mark(uid, 0, streak_after=2)
    assert "после оплаты сразу пришлю ссылку в закрытый канал" in tm.render("d3_midday", _at(uid, 13))


def test_after_the_trial_the_last_discount_day_then_the_full_price_once(uid):
    _person(uid, day=4)
    assert _due(uid, 13) == {"d4_midday"} and _due(uid, 20) == {"d4_evening"}
    midday = tm.render("d4_midday", _at(uid, 13))
    assert "Пробный период закончился" in midday and "Сегодня последний день" in midday and "461 ⭐" in midday and "153 ⭐" in midday
    assert "Ударный режим пока не набран" in midday
    _mark(uid, 0, streak_after=2)
    assert "2 дня подряд — сохранился" in tm.render("d4_midday", _at(uid, 13))
    shift_trial_day(uid, 5, ADMIN)
    assert _due(uid, 13) == {"d5_full"} and _due(uid, 20) == set()
    full = tm.render("d5_full", _at(uid, 13))
    assert "461 ⭐" in full and "153" not in full
    shift_trial_day(uid, 6, ADMIN)
    assert _due(uid, 13) == set(), "после пятого дня триал-сообщений больше нет, повторного триала тоже"


def test_a_paying_person_gets_only_the_channel_messages(uid):
    _person(uid, day=2)
    record_subscription_payment(uid, months=1)
    config_channel = {"channel_configured": True}
    assert _due(uid, 12, **config_channel) == {"paid_nudge"} and _due(uid, 20, **config_channel) == set()
    assert _due(uid, 12) == set() or not config.CLOSED_CHANNEL_ID, "без CLOSED_CHANNEL_ID про канал не пишем"
    eligible = {"channel_configured": True, "channel_eligible": True, "streak": 2}
    assert _due(uid, 9, **eligible) == {"channel_link"}
    assert _due(uid, 9, **eligible, channel_granted=True) == set()
    nudge = tm.render("paid_nudge", _at(uid, 12, streak=1, marked_today=False))
    assert "осталось 1 день" in nudge and "отметь привычку сегодня" in nudge
    today = tm.render("paid_nudge", _at(uid, 12, streak=1, marked_today=True))
    assert "Сегодня уже отмечено" in today


def test_testers_outside_the_scenario_get_nothing(uid):
    add_user(uid, "t", "Аня")
    _sql("UPDATE users SET created_at=datetime('now', '-1 day') WHERE telegram_id=?", (uid,))
    snap = trial_db.snapshot(uid)
    assert snap["scenario"] is False and tm.due_messages(snap) == []


# ---------------------------------------------------------------------------------------------------------------- тексты

def _all_texts(uid_):
    texts = []
    base = _at(uid_, 12)
    for key, message in tm.BY_KEY.items():
        if message.action:
            continue
        for marked_today in (False, True):
            for marked_yesterday in (False, True):
                for streak in (0, 1, 2, 5):
                    for name in ("Аня", ""):
                        for day in (1, 2, 3, 4, 5):
                            for intro in (True, False):
                                snap = dict(base, marked_today=marked_today, marked_yesterday=marked_yesterday, streak=streak,
                                            channel_eligible=streak >= 2, name=name, day=day, intro_active=intro,
                                            price_stars=153 if intro else 461)
                                texts.append((key, tm.render(key, snap)))
    for day in (1, 2, 3, 4):
        for streak in (0, 1, 2, 4):
            for marked_today in (False, True):
                for marked_yesterday in (False, True):
                    for link, configured in ((None, False), (None, True), ("https://t.me/+abc", True)):
                        snap = dict(base, day=day, streak=streak, marked_today=marked_today, marked_yesterday=marked_yesterday,
                                    channel_eligible=streak >= 2, channel_configured=configured)
                        texts.append(("paid_welcome", tm.paid_welcome_text(snap, "21.11.2026", link=link)))
    texts.append(("channel_link", tm.channel_link_text(dict(base, streak=2), "https://t.me/+abc")))
    return texts


def test_no_trial_text_looks_raw_gendered_or_formal(uid):
    _person(uid, day=2)
    texts = _all_texts(uid)
    assert len(texts) > 500
    bad = {}
    for key, text in texts:
        problems = _problems(text)
        if GENDERED.search(text) or FORMAL.search(text):
            problems.append("род или «вы»")
        if re.search(r"\bбот\w*|\bсистем\w*|\bAI\b|Адам написал", text):
            problems.append("третье лицо / бот / система")
        if problems:
            bad[(key, text[:60])] = problems
    assert not bad, "\n".join(f"{k}: {v}" for k, v in list(bad.items())[:10])


def test_a_name_with_html_is_escaped(uid):
    _person(uid, day=3, name="<b>Аня</b>")
    text = tm.render("d3_midday", _at(uid, 13))
    assert "<b>" not in text and "&lt;b&gt;Аня&lt;/b&gt;" in text


@pytest.mark.parametrize("day,streak,mt,my,needle", [
    (1, 0, False, False, "Закрытый канал открывается за 2 дня ударного режима подряд: отметь привычку сегодня и завтра"),
    (2, 0, False, False, "Осталось два дня: отметь хотя бы одну привычку сегодня и завтра"),
    (2, 1, False, True, "Сначала отметь привычку сегодня"),
    (2, 1, True, False, "Сегодня уже отмечено — завтра отметь привычку ещё раз"),
    (3, 0, False, False, "нужно 2 дня ударного режима подряд: отметь привычки сегодня и завтра"),
    (2, 2, True, True, "условие для закрытого канала выполнено"),
])
def test_the_text_after_payment_follows_the_day_and_the_marks(uid, day, streak, mt, my, needle):
    _person(uid, day=day)
    snap = _at(uid, 12, streak=streak, marked_today=mt, marked_yesterday=my, channel_eligible=streak >= 2, channel_configured=False)
    text = tm.paid_welcome_text(snap, "21.11.2026")
    assert text.startswith("✅ Доступ продлён до 21.11.2026") and needle in text


def test_the_text_after_payment_carries_the_link_when_the_streak_is_there(uid):
    _person(uid, day=2)
    snap = _at(uid, 12, streak=2, marked_today=True, marked_yesterday=True, channel_eligible=True, channel_configured=True)
    assert "https://t.me/+abc" in tm.paid_welcome_text(snap, "21.11.2026", link="https://t.me/+abc")
    assert "Ссылку пришлю чуть позже" in tm.paid_welcome_text(snap, "21.11.2026")


# ---------------------------------------------------------------------------------------------------------------- доставка

def _fixed(local_hour, tz="UTC"):
    class _Fixed(datetime):
        @classmethod
        def now(cls, zone=None):
            return datetime.now(zone or ZoneInfo(tz)).replace(hour=local_hour, minute=2, second=0, microsecond=0)
    return _Fixed


async def _tick(monkeypatch, users, hour, bot=None, tz="UTC"):
    bot = bot or SimpleNamespace(send_message=AsyncMock(), token="")
    rows = [get_user(u) for u in users]
    monkeypatch.setattr(sched, "get_all_users", lambda **kw: rows)
    monkeypatch.setattr(sched, "datetime", _fixed(hour, tz))
    await sched.run_trial_reminders(bot)
    return bot


async def test_the_scheduler_sends_each_message_once_to_the_trial_account_only(uid, monkeypatch):
    trial = _person(uid, day=2)
    tester = uid + 1
    add_user(tester, "t", "Аня")
    _sql("UPDATE users SET created_at=datetime('now', '-1 day'), access_status='approved' WHERE telegram_id=?", (tester,))
    bot = await _tick(monkeypatch, [trial, tester], 12)
    assert [c.args[0] for c in bot.send_message.call_args_list] == [trial]
    text = bot.send_message.call_args_list[0].args[1]
    assert text.startswith("Аня, сегодня второй день") and "⭐" not in text
    again = await _tick(monkeypatch, [trial, tester], 12)
    assert again.send_message.call_count == 0, "второй тик в то же окно ничего не шлёт"
    assert [x["key"] for x in trial_db.sent_log(trial)] == ["d2_goal"]


async def test_after_the_first_mark_the_offer_comes_with_a_buy_button(uid, monkeypatch):
    trial = _person(uid, day=2)
    await _tick(monkeypatch, [trial], 12)
    _mark(trial, 0, streak_after=1)
    bot = await _tick(monkeypatch, [trial], 12)
    assert bot.send_message.call_count == 1
    call = bot.send_message.call_args_list[0]
    assert "особое предложение" in call.args[1] and "153 ⭐" in call.args[1]
    buttons = [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["buy_subscription"] and "153" in buttons[0].text
    assert {x["key"] for x in trial_db.sent_log(trial)} == {"d2_goal", "d2_offer"}


async def test_switches_quiet_hours_reminders_and_the_daily_cap_hold_the_messages_back(uid, monkeypatch):
    trial = _person(uid, day=3)
    trial_db.set_message_enabled("d3_midday", False)
    bot = await _tick(monkeypatch, [trial], 13)
    assert bot.send_message.call_count == 0, "выключенное сообщение не уходит"
    trial_db.set_message_enabled("d3_midday", True)
    trial_db.set_message_enabled(trial_db.ALL, False)
    assert (await _tick(monkeypatch, [trial], 13)).send_message.call_count == 0, "общий тумблер выключает весь триал"
    trial_db.set_message_enabled(trial_db.ALL, True)
    _sql("UPDATE settings SET reminders=0 WHERE user_id=?", (trial,))
    assert (await _tick(monkeypatch, [trial], 13)).send_message.call_count == 0, "напоминания выключены — пуши не шлём"
    _sql("UPDATE settings SET reminders=1, quiet_hours_start=0, quiet_hours_end=23 WHERE user_id=?", (trial,))
    assert (await _tick(monkeypatch, [trial], 13)).send_message.call_count == 0, "тихие часы"
    _sql("UPDATE settings SET quiet_hours_start=NULL, quiet_hours_end=NULL WHERE user_id=?", (trial,))
    for index in range(3):                                         # дневной лимит: три сообщения триала в день
        _sql("INSERT INTO streak_notifications(user_id, day, kind) VALUES (?, ?, ?)", (trial, trial_db.snapshot(trial)["local_date"], f"trial:x{index}:default"))
    assert (await _tick(monkeypatch, [trial], 13)).send_message.call_count == 0
    _sql("DELETE FROM streak_notifications WHERE user_id=? AND kind LIKE 'trial:x%'", (trial,))
    assert (await _tick(monkeypatch, [trial], 13)).send_message.call_count == 1


async def test_a_failed_send_is_retried_by_the_next_tick(uid, monkeypatch):
    trial = _person(uid, day=3)
    broken = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("telegram down")), token="")
    await _tick(monkeypatch, [trial], 13, bot=broken)
    assert trial_db.sent_log(trial) == [], "неудачная отправка не считается доставленной"
    bot = await _tick(monkeypatch, [trial], 13)
    assert bot.send_message.call_count == 1


async def test_the_scheduler_uses_the_local_clock_of_the_person(uid, monkeypatch):
    trial = _person(uid, day=3, tz="Asia/Novosibirsk")
    assert (await _tick(monkeypatch, [trial], 8, tz="Asia/Novosibirsk")).send_message.call_count == 0
    bot = await _tick(monkeypatch, [trial], 13, tz="Asia/Novosibirsk")
    assert bot.send_message.call_count == 1 and "последний день пробного периода" in bot.send_message.call_args_list[0].args[1]


async def test_the_channel_link_goes_out_once_when_the_streak_is_there(uid, monkeypatch):
    trial = _person(uid, day=2)
    record_subscription_payment(trial, months=1)
    _mark(trial, 1, streak_after=1)
    _mark(trial, 0, streak_after=2)
    monkeypatch.setattr(config, "CLOSED_CHANNEL_ID", "-100123")
    granted = AsyncMock(return_value="https://t.me/+abc")
    monkeypatch.setattr(sched, "try_grant_channel_access", granted)
    bot = await _tick(monkeypatch, [trial], 10)
    assert bot.send_message.call_count == 1 and "https://t.me/+abc" in bot.send_message.call_args_list[0].args[1]
    assert (await _tick(monkeypatch, [trial], 10)).send_message.call_count == 0
    # ссылку создать не удалось — сообщение не уходит, а резерв откатывается: следующий тик попробует снова
    _sql("DELETE FROM streak_notifications WHERE user_id=?", (trial,))
    granted.return_value = None
    assert (await _tick(monkeypatch, [trial], 10)).send_message.call_count == 0
    granted.return_value = "https://t.me/+xyz"
    assert (await _tick(monkeypatch, [trial], 10)).send_message.call_count == 1


def test_clearing_the_start_lets_the_scenario_run_again(uid):
    trial = _person(uid, day=3)
    _sql("INSERT INTO streak_notifications(user_id, day, kind) VALUES (?, 'd', 'trial:d3_midday:default')", (trial,))
    _sql("INSERT INTO notification_log(user_id, category, title) VALUES (?, 'trial:d3_midday', 'x')", (trial,))
    assert [x["key"] for x in trial_db.sent_log(trial)] == ["d3_midday"]
    trial_db.clear_trial_messages(trial)
    assert trial_db.sent_log(trial) == []
    record_subscription_payment(trial, months=1)
    trial_db.clear_test_payment(trial)
    snap = trial_db.snapshot(trial)
    assert snap["paid"] is False and snap["ever_paid"] is False and snap["intro_active"] is True
