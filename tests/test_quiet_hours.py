"""
"Тихие часы" (roadmap #35) — окно локальных часов, в которое не приходят
повседневные напоминания (привычки/ударный режим).
"""
from datetime import datetime

from db import add_user, get_settings, set_quiet_hours, clear_quiet_hours, in_quiet_hours

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def test_quiet_hours_disabled_by_default(uid):
    add_user(uid, "u", "Test")
    settings = get_settings(uid)
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 23, 0)) is False


def test_set_quiet_hours_same_day_window(uid):
    add_user(uid, "u", "Test")
    assert set_quiet_hours(uid, 22, 23) is True
    settings = get_settings(uid)
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 22, 30)) is True
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 21, 30)) is False
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 23, 30)) is False


def test_set_quiet_hours_overnight_window(uid):
    add_user(uid, "u", "Test")
    set_quiet_hours(uid, 23, 7)
    settings = get_settings(uid)
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 23, 30)) is True
    assert in_quiet_hours(settings, datetime(2026, 1, 2, 3, 0)) is True
    assert in_quiet_hours(settings, datetime(2026, 1, 2, 6, 59)) is True
    assert in_quiet_hours(settings, datetime(2026, 1, 2, 7, 0)) is False
    assert in_quiet_hours(settings, datetime(2026, 1, 2, 12, 0)) is False


def test_set_quiet_hours_rejects_equal_start_end(uid):
    add_user(uid, "u", "Test")
    assert set_quiet_hours(uid, 10, 10) is False


def test_set_quiet_hours_rejects_out_of_range(uid):
    add_user(uid, "u", "Test")
    assert set_quiet_hours(uid, 5, 25) is False


def test_clear_quiet_hours(uid):
    add_user(uid, "u", "Test")
    set_quiet_hours(uid, 22, 7)
    clear_quiet_hours(uid)
    settings = get_settings(uid)
    assert in_quiet_hours(settings, datetime(2026, 1, 1, 23, 0)) is False


async def test_api_set_and_clear_quiet_hours(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post("/api/settings/quiet-hours", headers=headers, data='{"start": 22, "end": 7}')
    assert r.status == 200
    settings = get_settings(uid)
    assert settings["quiet_hours_start"] == 22
    assert settings["quiet_hours_end"] == 7

    r = await client.post("/api/settings/quiet-hours", headers=headers, data='{}')
    assert r.status == 200
    settings = get_settings(uid)
    assert settings["quiet_hours_start"] is None


async def test_api_rejects_invalid_quiet_hours(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/quiet-hours", headers=headers, data='{"start": 10, "end": 10}')
    assert r.status == 400


async def test_bootstrap_exposes_quiet_hours(client, uid):
    add_user(uid, "u", "Test")
    set_quiet_hours(uid, 22, 7)
    headers = await _headers(uid)
    r = await client.get("/api/bootstrap", headers=headers)
    assert r.status == 200
    data = await r.json()
    assert data["settings"]["quiet_hours"] == {"start": 22, "end": 7}


async def test_scheduler_job_skips_during_quiet_hours(monkeypatch, uid):
    """Сквозная проверка: тихие часы 22-07, тик планировщика в 23:00
    (обычно попадает в окно risk-уведомления) — сообщение не уходит."""
    import streak_scheduler

    add_user(uid, "u", "Test")
    set_quiet_hours(uid, 22, 7)

    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    class FakeBot:
        token = "test"

        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text=None, **kwargs):
            self.sent.append((chat_id, text))

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)
    assert bot.sent == []
