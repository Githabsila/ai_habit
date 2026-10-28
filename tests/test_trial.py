"""
Триал ADAM (ТЗ владельца 28.10.2026): состояние человека (db/trial.py), каталог сообщений (trial_messages.py) и их доставка (subscription_scheduler.py).
День триала — по местному календарю; вводная цена для первой покупки в любой день; сообщения по дню, часу, заходам и уже показанным предложениям (день 1 — без продажи,
день 2 — одно приглашение, день 3 — два контакта, окно — одно сообщение, затем редкая серия); тумблеры, тихие часы; тестировщики вне сценария ничего не получают.
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
from db import trial_flow
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


def _open_app(uid_):
    """Человек сегодня открыл приложение (событие «Открыл приложение»)."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO user_events(user_id, at, name, surface) VALUES (?, ?, 'session_start', 'app')", (uid_, now))


def _chat(uid_, count):
    for _ in range(count):
        _sql("INSERT INTO ai_messages(user_id, role, message) VALUES (?, 'user', 'привет')", (uid_,))


def test_day_one_has_no_selling_messages(uid):
    _person(uid, day=1)
    assert all(_due(uid, hour) == set() for hour in range(24)), "в первый день продающих сообщений нет: знакомство, полезные действия и кнопка подписки в приложении"


def test_day_two_invites_once_and_only_when_the_person_did_not_come_in(uid):
    _person(uid, day=2)
    assert _due(uid, 14) == {"d2_invite"} and _due(uid, 11) == set() and _due(uid, 17) == set() and _due(uid, 22) == set()
    _open_app(uid)
    assert _due(uid, 14) == set(), "заходил в приложение — внешнее приглашение не нужно"


def test_the_day_two_invite_does_not_repeat_an_offer_that_was_already_shown(uid):
    _person(uid, day=2)
    trial_flow.record(uid, "app", "d2_app_habit", "shown")
    assert _due(uid, 14) == set(), "предложение в приложении уже было — отдельным сообщением ту же продажу не повторяем"
    other = uid + 1
    _person(other, day=2)
    trial_flow.record(other, "app", "d2_app_habit", "dismissed")
    assert _due(other, 14) == set(), "«Не сейчас» гасит автоматические предложения до конца дня"


def test_the_day_two_invite_has_no_money_hours_left_or_private_details(uid):
    _person(uid, day=2)
    plain = tm.render("d2_invite", _at(uid, 14))
    assert "первый день позади" in plain
    assert not re.search(r"⭐|\$|оплат|цен|скидк|Pro|осталось|часов|«", plain)
    _mark(uid, 1)
    marked = tm.render("d2_invite", _at(uid, 14))
    assert "вчера у тебя была отметка привычки" in marked and plain != marked


def test_day_three_has_exactly_two_contacts_one_to_talk_one_direct(uid):
    _person(uid, day=3)
    assert _due(uid, 12) == {"d3_talk"} and _due(uid, 19) == {"d3_end"}
    assert _due(uid, 9) == set() and _due(uid, 16) == set() and _due(uid, 23) == set() and _due(uid, 2) == set(), "ночью не шлём"
    assert "условия" not in tm.render("d3_talk", _at(uid, 12)).lower() and not re.search(r"⭐|\$|оплат|цен", tm.render("d3_talk", _at(uid, 12))), "приглашение к разговору — не продажа"
    end = tm.render("d3_end", _at(uid, 19))
    assert "сегодня пробный доступ заканчивается" in end and "Твоя история сохранится" in end and "привычками, планом и разговором со мной" in end


def test_the_talk_invitation_is_dropped_if_the_person_already_talks_to_adam(uid):
    _person(uid, day=3)
    _chat(uid, 2)
    assert _due(uid, 12) == set() and _due(uid, 19) == {"d3_end"}


def test_nothing_comes_after_payment(uid):
    _person(uid, day=3)
    record_subscription_payment(uid, months=1)
    assert all(_due(uid, hour) == set() for hour in range(24)), "оплатил — запланированные сообщения больше не приходят"


def test_the_window_gets_one_message_and_the_series_after_it_waits_for_its_first_pause(uid):
    _person(uid, day=4)
    _sql("UPDATE users SET streak=3 WHERE telegram_id=?", (uid,))
    assert _due(uid, 12) == {"grace_notice"} and _due(uid, 6) == set() and _due(uid, 22) == set()
    notice = tm.render("grace_notice", _at(uid, 12))
    assert "Пробный доступ завершён" in notice and "Твоя история сохранена" in notice and "сохранится, если продолжить до" in notice
    for day in (5, 6, 7):
        shift_trial_day(uid, day, ADMIN)
        assert _due(uid, 14) == set(), f"день {day}: первая пауза после окна ещё идёт"
    shift_trial_day(uid, 8, ADMIN)
    due = _due(uid, 14)
    assert len(due) == 1 and next(iter(due)).startswith("wb_")


def test_testers_outside_the_scenario_get_nothing(uid):
    add_user(uid, "t", "Аня")
    _sql("UPDATE users SET created_at=datetime('now', '-1 day') WHERE telegram_id=?", (uid,))
    snap = trial_db.snapshot(uid)
    assert snap["scenario"] is False and tm.due_messages(snap) == []


def _log_wb(uid_, days_ago, key="wb_question"):
    when = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?, ?, 'x', ?)", (uid_, f"trial:{key}", when))


def _state(uid_):
    return trial_flow.winback_state(uid_, trial_db.snapshot(uid_))


def test_the_series_after_the_trial_keeps_calm_pauses_slows_down_without_reaction_and_stops(uid):
    _person(uid, day=10)
    assert _state(uid)["allowed"] is True
    _log_wb(uid, 1)
    state = _state(uid)
    assert state["allowed"] is False and state["reason"] == "wait" and state["sent"] == 1
    _sql("DELETE FROM notification_log WHERE user_id=?", (uid,))
    _log_wb(uid, 5)
    assert _state(uid)["allowed"] is True, "через 4 дня после первого можно писать второе"
    _sql("DELETE FROM notification_log WHERE user_id=?", (uid,))
    _log_wb(uid, 40)
    _log_wb(uid, 8)
    state = _state(uid)
    assert state["ignored"] == 2 and state["allowed"] is False, "два сообщения без захода: пауза после второго вдвое длиннее (14 дней), а прошло 8"
    _log_wb(uid, 20)
    assert _state(uid)["reason"] == "ignored", "после трёх сообщений без захода серия останавливается сама"


def test_coming_back_resets_the_ignored_count_and_the_opt_out_or_payment_stops_the_series(uid):
    _person(uid, day=10)
    _log_wb(uid, 30)
    _log_wb(uid, 12)
    _sql("INSERT INTO user_events(user_id, at, name, surface) VALUES (?, ?, 'session_start', 'app')", (uid, (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")))
    assert _state(uid)["ignored"] == 0, "заходил после последнего сообщения — реакция была"
    trial_flow.set_winback_optout(uid, True)
    assert _state(uid)["reason"] == "optout" and _due(uid, 14) == set()
    trial_flow.set_winback_optout(uid, False)
    record_subscription_payment(uid, months=1)
    assert _state(uid)["reason"] == "not_expired" and _due(uid, 14) == set(), "после оплаты серия прекращается"


def test_the_series_rotates_kinds_and_never_invents_facts(uid):
    facts = {"marks_total": 0, "habits_total": 0, "tasks_added": 0}
    assert trial_flow.winback_kind(uid, facts, 0) == "question" and trial_flow.winback_kind(uid, facts, 1) == "question" and trial_flow.winback_kind(uid, facts, 2) == "question"
    rich = {"marks_total": 6, "habits_total": 2, "tasks_added": 1, "days_with_marks": 3}
    assert [trial_flow.winback_kind(uid, rich, n) for n in range(3)] == ["question", "insight", "continue"]
    texts = {trial_flow.winback_text(uid, rich, n) for n in range(6)}
    assert len(texts) >= 3, "формулировки разные, дословно не повторяются"
    assert not any(re.search(r"⭐|\$|оплат|цен|скидк|шанс", text) for text in texts), "в редких сообщениях нет текста про оплату и «последних шансов»"


# ---------------------------------------------------------------------------------------------------------------- тексты

def _all_texts(uid_):
    texts = []
    base = _at(uid_, 12)
    for key, message in tm.BY_KEY.items():
        for marked_yesterday in (False, True):
            for streak in (0, 1, 2, 5):
                for name in ("Аня", ""):
                    for phase in ("day2", "day3", "grace", "expired"):
                        for intro in (True, False):
                            snap = dict(base, marked_yesterday=marked_yesterday, streak=streak, name=name, phase=phase, intro_active=intro,
                                        price_stars=153 if intro else 461, wb={"sent": streak})
                            texts.append((key, tm.render(key, snap)))
    rich = {"marks_total": 6, "habits_total": 2, "tasks_added": 1, "goal_days": 1, "days_with_marks": 3, "top_habits": [{"title": "Зарядка", "count": 3}], "untouched": ["Вода"]}
    for facts in ({}, {"marks_total": 4, "top_habits": [{"title": "Зарядка", "count": 3}]}, {"tasks_added": 2}, rich):
        for name in ("Аня", ""):
            for day in ("2026-10-28", "2026-10-29", "2026-10-30"):
                texts.append(("invite", trial_flow.invite_question(uid_, dict(base, name=name, local_date=day), facts)))
    for sent in range(6):
        for kind in ("question", "insight", "continue"):
            texts.append((f"wb_{kind}", trial_flow.winback_text(uid_, rich, sent, kind)))
    texts.append(("offer", "Показать короткую сводку?"))
    return texts


def test_no_trial_text_looks_raw_gendered_or_formal(uid):
    _person(uid, day=2)
    texts = _all_texts(uid)
    assert len(texts) > 400
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
    text = tm.render("d3_end", _at(uid, 19))
    assert "<b>" not in text and "&lt;b&gt;Аня&lt;/b&gt;" in text


def test_the_text_after_payment_names_the_term_the_free_days_and_the_renewal():
    """ТЗ 28.10 §19: после оплаты — точный срок, что стало с бесплатными днями и как продлевается подписка; про закрытый канал не говорим."""
    from datetime import datetime as dt
    snap = {"renewal_price_stars": 461, "renewal_usd": "$5.99"}
    plain = tm.paid_welcome_text(snap, dt(2026, 11, 21))
    assert plain.startswith("✅ Подписка активна до 21.11.2026") and "Продление вручную" in plain and "само ничего не спишется" in plain
    assert "канал" not in plain.lower() and "бесплатные дни" not in plain
    early = tm.paid_welcome_text({**snap, "early": True}, dt(2026, 11, 21))
    assert "Оставшиеся бесплатные дни пробного периода добавлены к сроку" in early
    kept = tm.paid_welcome_text({**snap, "protection": {"applied": True, "restored": 5}}, dt(2026, 11, 21))
    assert "Серия сохранена: 5 дней подряд снова с тобой" in kept
    auto = tm.paid_welcome_text(snap, dt(2026, 11, 21), auto_renew=True)
    assert "Продление автоматическое: каждые 30 дней спишется 461 ⭐ (≈$5.99)" in auto and "настройках Telegram" in auto and "вручную" not in auto


def test_the_text_after_payment_carries_the_link_only_when_there_is_one():
    snap = {"renewal_price_stars": 461, "renewal_usd": "$5.99", "streak": 2}
    assert "https://t.me/+abc" in tm.paid_welcome_text(snap, "21.11.2026", link="https://t.me/+abc")
    assert "https://" not in tm.paid_welcome_text(snap, "21.11.2026")


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


def _buttons(call):
    return [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]


async def test_the_scheduler_sends_the_day_two_invite_once_to_the_trial_account_only(uid, monkeypatch):
    trial = _person(uid, day=2)
    tester = uid + 1
    add_user(tester, "t", "Аня")
    _sql("UPDATE users SET created_at=datetime('now', '-1 day'), access_status='approved' WHERE telegram_id=?", (tester,))
    bot = await _tick(monkeypatch, [trial, tester], 14)
    assert [c.args[0] for c in bot.send_message.call_args_list] == [trial]
    call = bot.send_message.call_args_list[0]
    assert call.args[1].startswith("Аня, первый день позади") and "⭐" not in call.args[1]
    assert [b.text for b in _buttons(call)] == ["Открыть ADAM", "Условия подписки"]
    assert all(b.web_app is not None for b in _buttons(call)), "кнопки открывают приложение, а не просят платить в чате"
    again = await _tick(monkeypatch, [trial, tester], 14)
    assert again.send_message.call_count == 0, "второй тик в то же окно ничего не шлёт"
    assert [x["key"] for x in trial_db.sent_log(trial)] == ["d2_invite"]
    assert trial_flow.offer_state(trial)["sale_shown"] is True, "отправка записана в журнал предложений: «отправлено» — отдельное событие"
    assert _sql_one("SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND name='trial_msg_sent' AND step='d2_invite'", (trial,))["n"] == 1


def _sql_one(query, params=()):
    conn = connect()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


async def test_the_day_two_invite_is_not_sent_to_someone_who_came_in(uid, monkeypatch):
    trial = _person(uid, day=2)
    _open_app(trial)
    assert (await _tick(monkeypatch, [trial], 14)).send_message.call_count == 0


async def test_day_three_sends_the_talk_invitation_and_later_the_direct_notice_each_once(uid, monkeypatch):
    trial = _person(uid, day=3)
    first = await _tick(monkeypatch, [trial], 12)
    assert first.send_message.call_count == 1
    call = first.send_message.call_args_list[0]
    assert "у меня есть вопрос по твоим дням" in call.args[1]
    talk = _buttons(call)[0]
    assert talk.text == "Поговорить с Адамом" and talk.web_app.url.endswith("/coach?intro=trial&m=d3_talk")
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 0
    last = await _tick(monkeypatch, [trial], 19)
    assert last.send_message.call_count == 1 and "сегодня пробный доступ заканчивается" in last.send_message.call_args_list[0].args[1]
    sub = _buttons(last.send_message.call_args_list[0])[0]
    assert sub.text == "Условия подписки" and sub.web_app.url.endswith("/?sub=1&m=d3_end")
    assert (await _tick(monkeypatch, [trial], 19)).send_message.call_count == 0
    assert [x["key"] for x in trial_db.sent_log(trial)] == ["d3_end", "d3_talk"]


async def test_switches_quiet_hours_and_reminders_hold_the_messages_back(uid, monkeypatch):
    trial = _person(uid, day=3)
    trial_db.set_message_enabled("d3_talk", False)
    bot = await _tick(monkeypatch, [trial], 12)
    assert bot.send_message.call_count == 0, "выключенное сообщение не уходит"
    trial_db.set_message_enabled("d3_talk", True)
    trial_db.set_message_enabled(trial_db.ALL, False)
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 0, "общий тумблер выключает весь триал"
    trial_db.set_message_enabled(trial_db.ALL, True)
    _sql("UPDATE settings SET reminders=0 WHERE user_id=?", (trial,))
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 0, "напоминания выключены — приглашения не шлём"
    assert (await _tick(monkeypatch, [trial], 19)).send_message.call_count == 1, "а сообщение о настоящем конце доступа приходит и без напоминаний"
    _sql("UPDATE settings SET reminders=1, quiet_hours_start=0, quiet_hours_end=23 WHERE user_id=?", (trial,))
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 0, "тихие часы"
    _sql("UPDATE settings SET quiet_hours_start=NULL, quiet_hours_end=NULL WHERE user_id=?", (trial,))
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 1


async def test_there_is_no_common_daily_cap_only_a_limit_for_each_kind(uid, monkeypatch):
    trial = _person(uid, day=3)
    for index in range(5):                                                        # много чужих сообщений триала за день — общего предела нет
        _sql("INSERT INTO streak_notifications(user_id, day, kind) VALUES (?, ?, ?)", (trial, trial_db.snapshot(trial)["local_date"], f"trial:x{index}:default"))
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 1


async def test_a_failed_send_is_retried_by_the_next_tick(uid, monkeypatch):
    trial = _person(uid, day=3)
    broken = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("telegram down")), token="")
    await _tick(monkeypatch, [trial], 12, bot=broken)
    assert trial_db.sent_log(trial) == [], "неудачная отправка не считается доставленной"
    assert trial_flow.offer_state(trial)["keys"] == [], "и в журнал предложений не попадает"
    bot = await _tick(monkeypatch, [trial], 12)
    assert bot.send_message.call_count == 1


async def test_a_person_who_blocked_the_bot_is_marked_and_not_retried(uid, monkeypatch):
    from db import is_bot_blocked

    class TelegramForbiddenError(Exception):
        pass

    trial = _person(uid, day=3)
    blocked = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramForbiddenError("bot was blocked by the user")), token="")
    await _tick(monkeypatch, [trial], 12, bot=blocked)
    assert is_bot_blocked(trial) is True


async def test_the_scheduler_uses_the_local_clock_of_the_person(uid, monkeypatch):
    trial = _person(uid, day=3, tz="Asia/Novosibirsk")
    assert (await _tick(monkeypatch, [trial], 8, tz="Asia/Novosibirsk")).send_message.call_count == 0
    bot = await _tick(monkeypatch, [trial], 12, tz="Asia/Novosibirsk")
    assert bot.send_message.call_count == 1 and "у меня есть вопрос" in bot.send_message.call_args_list[0].args[1]


async def test_the_window_notice_goes_once_with_the_protection_deadline(uid, monkeypatch):
    trial = _person(uid, day=4)
    _sql("UPDATE users SET streak=3 WHERE telegram_id=?", (trial,))
    bot = await _tick(monkeypatch, [trial], 12)
    assert bot.send_message.call_count == 1
    text = bot.send_message.call_args_list[0].args[1]
    assert "Пробный доступ завершён" in text and "3 дня подряд" in text and "сохранится, если продолжить до" in text
    assert (await _tick(monkeypatch, [trial], 12)).send_message.call_count == 0


async def test_the_series_after_the_trial_sends_one_message_with_an_opt_out_and_then_waits(uid, monkeypatch):
    trial = _person(uid, day=9)
    bot = await _tick(monkeypatch, [trial], 14)
    assert bot.send_message.call_count == 1
    call = bot.send_message.call_args_list[0]
    assert [b.text for b in _buttons(call)] == ["Условия подписки", "Не присылать такое"]
    assert _buttons(call)[1].callback_data == "winback_off"
    assert not re.search(r"⭐|\$|оплат|цен|скидк|шанс", call.args[1])
    assert (await _tick(monkeypatch, [trial], 14)).send_message.call_count == 0
    assert _state(trial)["reason"] == "wait" and _state(trial)["sent"] == 1


async def test_nothing_from_the_catalog_goes_to_a_paid_person(uid, monkeypatch):
    trial = _person(uid, day=3)
    record_subscription_payment(trial, months=1)
    for hour in (12, 19):
        assert (await _tick(monkeypatch, [trial], hour)).send_message.call_count == 0


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
