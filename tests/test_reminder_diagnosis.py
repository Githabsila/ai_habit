"""
«Почему не пришла точка дня» (16.10): у одного аккаунта не было напоминаний 10:00/12:00/17:00, хотя привычки не закрыты, а причину узнать было
неоткуда. Теперь: (1) решение «слать или нет» живёт в одном месте — coach.checkpoint_verdict; (2) каждая причина пропуска записывается;
(3) карточка человека в админке показывает настройки, решение по каждой точке дня, что реально ушло и причины пропусков.
"""
import itertools
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import coach
import reminder_diagnosis as rd
from db import add_habit, add_user, complete_habit, get_user, mark_bot_blocked
from db.core import connect
from db.notification_skips import REASON_LABELS, record_skip, recent_skips
from db.settings import set_quiet_hours, toggle_reminder_category, toggle_reminders
from db.streak import set_timezone
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
ADMIN_HTML = (ROOT / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")

_uid_counter = itertools.count(844_000_000, 100)
FROZEN_DAY = "2026-01-01"


@pytest.fixture
def uid():
    return next(_uid_counter)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _person(user_id, tz="Europe/Moscow"):
    add_user(user_id, f"u{user_id}", "Александр")
    _sql("UPDATE users SET access_status='approved' WHERE telegram_id=?", (user_id,))
    set_timezone(user_id, tz)
    return user_id


def _freeze(monkeypatch, hour, minute=0):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, hour, minute, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)


def _only(monkeypatch, user_id):
    monkeypatch.setattr(coach, "get_all_users", lambda **_kw: [{"telegram_id": user_id}])


def _skips(user_id, kind="habit_checkpoint_10"):
    conn = connect()
    try:
        return [r["reason"] for r in conn.execute("SELECT reason FROM notification_skips WHERE user_id=? AND kind=?", (user_id, kind))]
    finally:
        conn.close()


async def _run10(monkeypatch, user_id, hour=10):
    _only(monkeypatch, user_id)
    _freeze(monkeypatch, hour)
    bot = FakeBot()
    await coach.run_habit_checkpoint_10(bot)
    return bot


# ------------------------------------------------------------------------------------------------ причины пропусков пишутся

async def test_a_normal_person_gets_the_checkpoint_and_no_skip_is_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    bot = await _run10(monkeypatch, user)
    assert len(bot.sent) == 1 and _skips(user) == []


async def test_outside_the_persons_hour_nothing_is_sent_or_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    bot = await _run10(monkeypatch, user, hour=11)
    assert bot.sent == [] and _skips(user) == []


async def test_reminders_switched_off_is_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    toggle_reminders(user)
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["reminders_off"]


async def test_habits_category_off_is_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    toggle_reminder_category(user, "habits")
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["habits_off"]


async def test_quiet_hours_are_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    set_quiet_hours(user, 9, 13)
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["quiet_hours"]


async def test_no_habits_and_all_done_are_told_apart(monkeypatch, uid):
    empty, done = _person(uid), _person(uid + 1)
    assert (await _run10(monkeypatch, empty)).sent == [] and _skips(empty) == ["no_habits"]
    complete_habit(add_habit(done, "Сделано"))
    assert (await _run10(monkeypatch, done)).sent == [] and _skips(done) == ["all_done"]


async def test_only_timed_habits_in_the_future_are_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Вечерний шаг", planned_time="23:00")
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["only_timed"]


async def test_a_fresh_mark_is_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Вторая")
    complete_habit(add_habit(user, "Первая"))
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["fresh_mark"]


async def test_a_missed_day_hands_over_to_the_comeback_message_and_says_so(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    _sql("INSERT INTO streak_days(user_id, day, status) VALUES (?, ?, 'completed')", (user, (date.today() - timedelta(days=3)).isoformat()))
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["reengage"]


async def test_a_blocked_bot_is_recorded(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    mark_bot_blocked(user)
    bot = await _run10(monkeypatch, user)
    assert bot.sent == [] and _skips(user) == ["bot_blocked"]


async def test_the_reason_is_written_once_per_day_and_point(monkeypatch, uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    toggle_reminders(user)
    await _run10(monkeypatch, user)
    await _run10(monkeypatch, user)
    assert _skips(user) == ["reminders_off"]
    assert _skips(user, "habit_checkpoint_12") == []


def test_every_reason_has_a_label_and_old_records_are_cleaned(uid):
    assert {"bot_blocked", "reminders_off", "habits_off", "quiet_hours", "reengage", "no_habits", "all_done", "fresh_mark", "only_timed"} <= set(REASON_LABELS)
    _sql("INSERT INTO notification_skips(user_id, day, kind, reason) VALUES (?,?,?,?)", (uid, "2020-01-01", "habit_checkpoint_10", "all_done"))
    record_skip(uid, date.today().isoformat(), "habit_checkpoint_12", "quiet_hours")
    conn = connect()
    old = conn.execute("SELECT COUNT(*) AS n FROM notification_skips WHERE user_id=? AND day='2020-01-01'", (uid,)).fetchone()["n"]
    conn.close()
    assert old == 0
    assert [s["reason"] for s in recent_skips(uid, days=2)] == ["quiet_hours"]
    assert recent_skips(uid, days=2)[0]["label"] == REASON_LABELS["quiet_hours"]


def test_a_broken_journal_never_breaks_the_mailing(monkeypatch, uid):
    import db.notification_skips as ns

    def boom():
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(ns, "connect", boom)
    record_skip(uid, "2026-01-01", "habit_checkpoint_10", "all_done")          # не бросает исключение


# ------------------------------------------------------------------------------------------------ разбор в админке

NOW = datetime(2026, 10, 16, 11, 53, tzinfo=timezone.utc)                       # 14:53 в Москве — как на скриншоте владельца


def _by_label(report, label):
    return next(a for a in report["account"] if a["label"] == label)


def test_diagnosis_of_a_healthy_account_says_the_checkpoints_should_come(uid):
    user = _person(uid)
    add_habit(user, "1.1")
    add_habit(user, "Проверка")
    report = rd.diagnose_user(user, NOW)
    assert report["name"] == "Александр" and report["timezone"] == "Europe/Moscow" and report["local_time"] == "14:53"
    assert all(a["ok"] for a in report["account"])
    assert [c["hour"] for c in report["checkpoints"]] == [10, 12, 17, 22] and all(c["will_send"] for c in report["checkpoints"])
    assert report["habits"] == {"total": 2, "open": 2} and "должны приходить" in report["summary"]
    assert report["checkpoints"][0]["actual"].startswith("час прошёл") and report["checkpoints"][2]["actual"] == "ещё впереди"


def test_diagnosis_names_every_switch_that_silences_the_bot(uid):
    off, quiet, utc, blocked, unapproved = (_person(uid + i) for i in range(5))
    for user in (off, quiet, utc, blocked, unapproved):
        add_habit(user, "Проверка")
    toggle_reminders(off)
    report = rd.diagnose_user(off, NOW)
    assert not _by_label(report, "Напоминания включены")["ok"] and "общий выключатель" in _by_label(report, "Напоминания включены")["detail"]
    assert not any(c["will_send"] for c in report["checkpoints"]) and "не придёт" in report["summary"]

    set_quiet_hours(quiet, 9, 13)
    report = rd.diagnose_user(quiet, NOW)
    assert _by_label(report, "Тихие часы")["detail"] == "бот молчит 09:00–13:00"
    assert [c["will_send"] for c in report["checkpoints"]] == [False, False, True, True]       # 10:00 и 12:00 — в тихих часах

    set_timezone(utc, "UTC")
    zone = _by_label(rd.diagnose_user(utc, NOW), "Часовой пояс")
    assert not zone["ok"] and "стоит UTC" in zone["detail"]

    mark_bot_blocked(blocked)
    assert not _by_label(rd.diagnose_user(blocked, NOW), "Бот не заблокирован человеком")["ok"]

    _sql("UPDATE users SET access_status='new' WHERE telegram_id=?", (unapproved,))
    assert not _by_label(rd.diagnose_user(unapproved, NOW), "Рассылки разрешены")["ok"]


def test_diagnosis_shows_a_missed_day_as_the_reason(uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    _sql("INSERT INTO streak_days(user_id, day, status) VALUES (?, ?, 'completed')", (user, (date.today() - timedelta(days=4)).isoformat()))
    report = rd.diagnose_user(user)
    assert not _by_label(report, "Серия")["ok"] and "пропущено полных дней" in _by_label(report, "Серия")["detail"]
    assert not any(c["will_send"] for c in report["checkpoints"]) and "вернись в серию" in report["checkpoints"][0]["rule"]


def test_diagnosis_shows_what_was_actually_sent_and_skipped(uid):
    user = _person(uid)
    add_habit(user, "Проверка")
    _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?,?,?,?)", (user, "habit_checkpoint_10", "x", "2026-10-16 07:01:12"))
    record_skip(user, "2026-10-16", "habit_checkpoint_12", "quiet_hours")
    report = rd.diagnose_user(user, NOW)
    by_hour = {c["hour"]: c for c in report["checkpoints"]}
    assert by_hour[10]["actual"] == "сегодня отправлено в 10:01" and by_hour[10]["sent"]
    assert by_hour[12]["actual"].startswith("сегодня пропущено: тихие часы") and by_hour[12]["skipped"]
    assert any("10:01" in r["when"] for r in report["recent"]) and report["recent"][0]["when"].startswith("16.10")


def test_unknown_person_has_no_diagnosis(uid):
    assert rd.diagnose_user(uid + 90) is None


async def test_admin_route_is_admin_only_and_returns_the_report(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin, person = uid + 50, _person(uid)
    add_habit(person, "Проверка")
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin})
    add_user(admin, "adm", "Админ")
    headers = lambda user_id: {"Authorization": f"tma {sign_init_data(user_id)}"}
    assert (await client.get(f"/api/admin/user/{person}/reminders", headers=headers(person))).status == 403
    assert (await client.get(f"/api/admin/user/{uid + 97}/reminders", headers=headers(admin))).status == 404
    assert (await client.get("/api/admin/user/oops/reminders", headers=headers(admin))).status == 400
    ok = await client.get(f"/api/admin/user/{person}/reminders", headers=headers(admin))
    data = await ok.json()
    assert ok.status == 200 and data["user_id"] == person and len(data["checkpoints"]) == 4 and data["account"]


def test_admin_card_has_the_reminders_section():
    for needle in ("remindersSection", "loadReminders", "/reminders", "Напоминания: почему не приходят"):
        assert needle in ADMIN_HTML, needle


async def test_a_hidden_timezone_does_not_overwrite_a_known_one(client, uid):
    from db.streak import get_timezone
    user = _person(uid, "Europe/Moscow")
    headers = {"Authorization": f"tma {sign_init_data(user)}", "Content-Type": "application/json"}
    for masked in ("UTC", "Etc/UTC", "", "GMT"):
        reply = await client.post("/api/streak/timezone", headers=headers, json={"timezone": masked})
        assert reply.status == 200 and (await reply.json())["timezone"] == "Europe/Moscow", masked
    assert get_timezone(user) == "Europe/Moscow"
    moved = await client.post("/api/streak/timezone", headers=headers, json={"timezone": "Asia/Tokyo"})
    assert (await moved.json())["timezone"] == "Asia/Tokyo" and get_timezone(user) == "Asia/Tokyo"

    fresh = uid + 1
    add_user(fresh, "u", "Новый")
    fresh_headers = {"Authorization": f"tma {sign_init_data(fresh)}", "Content-Type": "application/json"}
    await client.post("/api/streak/timezone", headers=fresh_headers, json={"timezone": "UTC"})
    assert get_timezone(fresh) == "UTC"                                          # неизвестного пояса нет — пишем как есть
