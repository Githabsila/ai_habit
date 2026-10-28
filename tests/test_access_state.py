"""
Состояния доступа и защита серии (ТЗ «Адам. Триал, подписка и приглашения друзей», 28.10.2026, часть 1 и §4–§8).

Один аккаунт — одно понятное состояние: not_started → trial (три календарных дня, считая день первого открытия) → grace (24 часа: только чтение, можно оплатить и сохранить серию)
→ expired; paid — по оплате. Триал стартует при первом успешном открытии приложения, срок записывается один раз.
"""
import itertools
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import config
from db import add_habit, add_user, get_user, set_timezone, start_trial_mode
from db import access_state as acs
from db import trial_protection as protection
from db.access import reset_trial_start
from db.core import connect
from db.streak import rollover_user
from db.subscription import record_subscription_payment
from tests.conftest import sign_init_data

UTC = timezone.utc
ADMIN = 777
_counter = itertools.count(861_900_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        row = conn.execute(query, params).fetchone() if query.lstrip().upper().startswith("SELECT") else conn.execute(query, params)
        conn.commit()
        return row
    finally:
        conn.close()


def _person(uid_, tz="Europe/Moscow", started=None):
    """Аккаунт в сценарии триала; started (UTC, без tzinfo) — когда человек впервые открыл приложение; None — ещё не открывал."""
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, tz)
    start_trial_mode(uid_, ADMIN)
    if started is None:
        reset_trial_start(uid_, ADMIN)
    else:
        acs.set_trial_start(uid_, started)
    return uid_


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _utc(*args):
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------------- старт и срок триала

def test_the_trial_does_not_start_until_the_app_is_opened(uid):
    _person(uid)
    info = acs.access_state(uid)
    assert info["state"] == "not_started" and info["allowed"] is True and info["started_at"] is None and info["trial_ends_at"] is None
    assert "начнётся при первом открытии" in info["reason_text"]
    from db.subscription import bot_access_allowed
    assert bot_access_allowed(uid) is True, "одно «Старт» в боте триал не расходует"


def test_the_first_open_starts_three_calendar_days_and_the_end_is_local_midnight(uid):
    """Пример из ТЗ: впервые открыл в понедельник — понедельник первый день, вторник второй, среда третий; в ночь со среды на четверг в 00:00 основные действия закрываются."""
    _person(uid, tz="Europe/Moscow")
    monday_evening = _utc(2026, 10, 26, 19, 40)                     # 22:40 по Москве: этот вечер тоже считается первым днём
    assert acs.ensure_trial_started(uid, monday_evening) is True
    info = acs.access_state(uid, monday_evening)
    assert info["state"] == "trial" and info["day"] == 1 and info["phase"] == "day1"
    assert info["trial_ends_at"] == "2026-10-28T21:00:00", "четверг 00:00 по Москве = среда 21:00 UTC"
    assert acs.access_state(uid, _utc(2026, 10, 27, 12, 0))["day"] == 2
    assert acs.access_state(uid, _utc(2026, 10, 28, 20, 59))["state"] == "trial"
    assert acs.access_state(uid, _utc(2026, 10, 28, 20, 59))["day"] == 3
    assert acs.access_state(uid, _utc(2026, 10, 28, 21, 0))["state"] == "grace"
    assert "29 октября, 00:00" in acs.access_state(uid, monday_evening)["reason_text"], "срок показан конкретной датой и временем"


def test_reopening_or_changing_the_time_zone_never_gives_a_new_trial(uid):
    _person(uid, tz="Europe/Moscow")
    start = _utc(2026, 10, 26, 8, 0)
    acs.ensure_trial_started(uid, start)
    before = acs.access_state(uid, start)
    assert acs.ensure_trial_started(uid, _utc(2026, 10, 27, 9, 0)) is False, "повторное открытие триал не перезапускает"
    set_timezone(uid, "Asia/Vladivostok")                           # человек поменял пояс: срок уже назначен и не двигается
    after = acs.access_state(uid, start)
    assert after["trial_ends_at"] == before["trial_ends_at"] and after["started_at"] == before["started_at"]
    assert acs.start_trial(uid, _utc(2026, 11, 20, 8, 0)) is not None and acs.access_state(uid, start)["started_at"] == before["started_at"], "start_trial без force ничего не меняет"


def test_open_accounts_have_no_trial_and_are_never_started(uid):
    add_user(uid, f"u{uid}", "Аня")
    assert acs.ensure_trial_started(uid) is False
    info = acs.access_state(uid)
    assert info["state"] == "open" and info["trial_ends_at"] is None and get_user(uid)["trial_started_at"] is None


# ---------------------------------------------------------------------------------------------------------------- дополнительное окно

def test_the_extra_window_lasts_a_real_24_hours_even_when_the_clocks_change(uid):
    _person(uid, tz="America/New_York")
    acs.set_trial_start(uid, _utc(2026, 10, 29, 14, 0))             # четверг 10:00 по Нью-Йорку
    info = acs.access_state(uid, _utc(2026, 11, 1, 6, 0))
    assert info["state"] == "trial" or info["state"] == "grace"
    ends = datetime.fromisoformat(info["trial_ends_at"])
    grace_end = datetime.fromisoformat(info["grace_ends_at"])
    assert grace_end - ends == timedelta(hours=24), "ориентируемся на фактический срок, а не на следующую полночь (перевод часов в ночь на 1 ноября)"
    assert acs.access_state(uid, grace_end.replace(tzinfo=UTC) - timedelta(minutes=1))["state"] == "grace"
    assert acs.access_state(uid, grace_end.replace(tzinfo=UTC))["state"] == "expired"
    assert info["ends_at"] == info["grace_ends_at"] if info["state"] == "grace" else True


def test_the_window_is_read_only_and_payment_opens_everything(uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3, hours=1))
    info = acs.access_state(uid)
    assert info["state"] == "grace" and info["read_only"] is True and info["allowed"] is False
    from db.subscription import bot_access_allowed
    assert bot_access_allowed(uid) is False
    record_subscription_payment(uid)
    paid = acs.access_state(uid)
    assert paid["state"] == "paid" and paid["allowed"] is True and paid["read_only"] is False and "Подписка до" in paid["reason_text"]


async def test_the_app_shows_history_in_the_window_but_refuses_new_actions(client, uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3, hours=1))
    add_habit(uid, "Зарядка")
    read = await client.get("/api/bootstrap", headers=_headers(uid))
    assert read.status == 200 and (await read.json())["access"]["state"] == "grace"
    write = await client.post("/api/habits", headers=_headers(uid), json={"title": "Ещё одна"})
    assert write.status == 403 and (await write.json())["error"] == "trial_expired"
    status = await client.get("/api/trial", headers=_headers(uid))
    assert status.status == 200 and (await status.json())["trial"]["state"] == "grace", "экран продолжения доступен всегда"
    acs_row = _sql("SELECT trial_started_at FROM users WHERE telegram_id=?", (uid,))
    assert acs_row["trial_started_at"], "чтение в окне триал не перезапускает"


async def test_after_the_window_the_app_is_closed_and_the_first_open_starts_the_trial(client, uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=5))
    closed = await client.get("/api/bootstrap", headers=_headers(uid))
    assert closed.status == 403 and (await closed.json())["error"] == "trial_expired"
    fresh = _person(uid + 1, tz="UTC")
    assert get_user(fresh)["trial_started_at"] is None
    opened = await client.get("/api/bootstrap", headers=_headers(fresh))
    body = await opened.json()
    assert opened.status == 200 and body["access"]["state"] == "trial" and body["access"]["ends_at_text"]
    started = get_user(fresh)["trial_started_at"]
    await client.get("/api/bootstrap", headers=_headers(fresh))
    assert get_user(fresh)["trial_started_at"] == started, "второй вход ничего не перезапускает"


# ---------------------------------------------------------------------------------------------------------------- цена и срок при ранней покупке

def test_paying_during_the_trial_adds_the_remaining_free_time(uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=5))
    ends = datetime.fromisoformat(acs.access_state(uid)["trial_ends_at"])
    until = record_subscription_payment(uid)
    assert until == ends + timedelta(days=30), "оплатил рано — платный доступ включён сразу, а оставшиеся бесплатные часы добавлены к нему"
    assert acs.access_state(uid)["state"] == "paid"


def test_paying_in_the_window_counts_from_the_purchase_not_from_the_free_time(uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3, hours=2))
    now = datetime.now(UTC).replace(tzinfo=None)
    until = record_subscription_payment(uid, paid_at=now - timedelta(minutes=10))
    assert until == now - timedelta(minutes=10) + timedelta(days=30), "окно — не бесплатное время: месяц идёт от покупки (по часам Telegram, а не от обработки)"


def test_gift_days_extend_the_free_period_and_the_window_follows_them(uid):
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2))
    from db.access import gift_days
    gift_days(uid, 7, ADMIN)
    info = acs.access_state(uid)
    assert info["state"] == "trial", "подарочные дни добавляются после триала"
    far = datetime.fromisoformat(info["free_end"]).replace(tzinfo=UTC) + timedelta(hours=1)
    assert acs.access_state(uid, far)["state"] == "grace" and acs.access_state(uid, far + timedelta(hours=25))["state"] == "expired"


# ---------------------------------------------------------------------------------------------------------------- защита серии

def _streak_setup(uid_, paid_in_window, streak=5, freezes=0):
    """Триал закончился позавчера вечером (местное «сегодня» — день 5), днём окна был вчерашний день; серия — streak дней до окна."""
    tz = ZoneInfo("UTC")
    today = datetime.now(tz).date()
    started = datetime.combine(today - timedelta(days=4), datetime.min.time()) + timedelta(hours=8)
    _person(uid_, tz="UTC", started=started)
    window_day = today - timedelta(days=1)                                    # «четвёртый день»: начало окна — его полночь
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?, ?, 'completed', ?)", (uid_, (today - timedelta(days=2)).isoformat(), streak))
    _sql("UPDATE users SET streak=?, last_completed=? WHERE telegram_id=?", (streak, (today - timedelta(days=2)).isoformat(), uid_))
    _sql("INSERT OR REPLACE INTO streak_meta(user_id, rollover_day, freeze_balance) VALUES (?, ?, ?)", (uid_, window_day.isoformat(), freezes))
    if paid_in_window:
        moment = datetime.combine(window_day, datetime.min.time()) + timedelta(hours=10)
        _sql("INSERT INTO stars_payments(charge_id, user_id, payload, amount, purchased_at) VALUES (?, ?, ?, 153, ?)", (f"c{uid_}", uid_, f"subscription:{uid_}", moment.isoformat()))
    return window_day


def test_a_day_of_the_window_does_not_break_the_streak_when_the_person_paid_in_time(uid):
    window_day = _streak_setup(uid, paid_in_window=True, freezes=1)
    assert protection.protected_days(get_user(uid)) == [window_day.isoformat()]
    assert rollover_user(uid) is True
    row = _sql("SELECT status, streak_after, ai_message FROM streak_days WHERE user_id=? AND day=?", (uid, window_day.isoformat()))
    assert row["status"] == "freeze" and row["streak_after"] == 5 and row["ai_message"] == protection.PROTECTED_MESSAGE
    assert get_user(uid)["streak"] == 5
    meta = _sql("SELECT freeze_balance, last_broken_date FROM streak_meta WHERE user_id=?", (uid,))
    assert meta["freeze_balance"] == 1 and meta["last_broken_date"] is None, "заморозка не потрачена, срыва нет"
    done = _sql("SELECT COUNT(*) AS n FROM streak_days WHERE user_id=? AND status='completed' AND day>=?", (uid, window_day.isoformat()))
    assert done["n"] == 0, "платёж привычку не засчитывает"


def test_without_payment_the_special_protection_ends_and_the_usual_rules_apply(uid):
    window_day = _streak_setup(uid, paid_in_window=False, freezes=1)
    rollover_user(uid)
    row = _sql("SELECT status, ai_message FROM streak_days WHERE user_id=? AND day=?", (uid, window_day.isoformat()))
    assert row["status"] == "freeze" and "заморозкой" in row["ai_message"] and "🛡" not in row["ai_message"], "обычная заморозка, а не защита"
    assert _sql("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (uid,))["freeze_balance"] == 0
    other = next(_counter)
    _streak_setup(other, paid_in_window=False, freezes=0)
    rollover_user(other)
    assert get_user(other)["streak"] == 0 and _sql("SELECT status FROM streak_days WHERE user_id=? AND day=?", (other, window_day.isoformat()))["status"] == "missed"


def test_a_late_confirmation_restores_the_streak_once_and_cannot_be_restored_again(uid):
    window_day = _streak_setup(uid, paid_in_window=False, freezes=0)
    rollover_user(uid)                                                         # день окна закрылся как пропущенный, подтверждение оплаты ещё не пришло
    assert get_user(uid)["streak"] == 0
    purchase = datetime.combine(window_day, datetime.min.time()) + timedelta(hours=20)       # заплатил в окне, а подтверждение — позже
    result = protection.apply_on_payment(uid, purchase)
    assert result["applied"] is True and result["restored"] == 5
    assert get_user(uid)["streak"] == 5
    assert _sql("SELECT status, ai_message FROM streak_days WHERE user_id=? AND day=?", (uid, window_day.isoformat()))["ai_message"] == protection.PROTECTED_MESSAGE
    assert _sql("SELECT last_broken_date FROM streak_meta WHERE user_id=?", (uid,))["last_broken_date"] is None
    from db.streak import get_free_restore_status
    assert get_free_restore_status(uid)["available"] is False, "тот же пропуск нельзя восстановить повторно"
    assert protection.apply_on_payment(uid, purchase)["applied"] is False


def test_a_payment_outside_the_window_changes_nothing_for_the_streak(uid):
    window_day = _streak_setup(uid, paid_in_window=False, freezes=0)
    rollover_user(uid)
    late = datetime.combine(window_day + timedelta(days=2), datetime.min.time())
    assert protection.apply_on_payment(uid, late) == {"applied": False, "reason": "outside_window"}
    assert get_user(uid)["streak"] == 0


def test_a_new_streak_is_not_glued_to_the_old_one(uid):
    window_day = _streak_setup(uid, paid_in_window=False, freezes=0)
    rollover_user(uid)
    _sql("UPDATE users SET streak=1 WHERE telegram_id=?", (uid,))
    purchase = datetime.combine(window_day, datetime.min.time()) + timedelta(hours=20)
    assert protection.apply_on_payment(uid, purchase) == {"applied": False, "reason": "streak_restarted"}


# ---------------------------------------------------------------------------------------------------------------- тестовые деньги не в показателях

async def test_a_test_payment_from_the_admin_never_reaches_the_real_numbers(uid):
    from types import SimpleNamespace
    import subscription_flow as flow
    from db import analytics
    _person(uid, tz="UTC", started=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3))
    before = analytics.get_subscription_conversion()
    text, snap, link = await flow.complete_subscription_payment(SimpleNamespace(), uid, test=True)
    assert acs.access_state(uid)["state"] == "paid", "доступ человеку тестовая оплата открывает, как настоящая"
    row = _sql("SELECT is_test, purchased_at FROM stars_payments WHERE user_id=?", (uid,))
    assert row["is_test"] == 1 and row["purchased_at"]
    assert analytics.get_subscription_conversion() == before, "число платящих не выросло"
    from db import trial as trial_db
    trial_db.clear_test_payment(uid)
    assert _sql("SELECT COUNT(*) AS n FROM stars_payments WHERE user_id=?", (uid,))["n"] == 0
