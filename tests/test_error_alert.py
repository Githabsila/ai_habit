"""
Реалтайм-алерт админам при всплеске ошибок (error_alert_scheduler.py) —
раньше про сбой узнавали только из ежедневной сводки в 8 утра.
"""
import error_alert_scheduler as mod
from db import log_error


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


async def test_no_alert_below_threshold(monkeypatch):
    monkeypatch.setattr(mod, "_last_alert_at", None)
    monkeypatch.setattr(mod, "get_error_stats", lambda hours=1: {"total": 0, "by_scope": [], "hours": 1})
    bot = FakeBot()

    await mod.run_error_spike_check(bot)

    assert bot.sent == []


async def test_alert_fires_once_above_threshold(monkeypatch):
    monkeypatch.setattr(mod, "_last_alert_at", None)
    monkeypatch.setattr(
        mod, "get_error_stats",
        lambda hours=1: {"total": 999, "by_scope": [{"scope": "test", "cnt": 999}], "hours": 1},
    )
    monkeypatch.setattr("config.ADMIN_IDS", [777])
    monkeypatch.setattr("alerts.ADMIN_IDS", [777])
    bot = FakeBot()

    await mod.run_error_spike_check(bot)

    assert len(bot.sent) == 1
    assert "999" in bot.sent[0][1]


async def test_cooldown_prevents_repeat_alert_right_after(monkeypatch):
    import datetime
    monkeypatch.setattr(mod, "_last_alert_at", datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
    monkeypatch.setattr(
        mod, "get_error_stats",
        lambda hours=1: {"total": 999, "by_scope": [], "hours": 1},
    )
    monkeypatch.setattr("config.ADMIN_IDS", [777])
    monkeypatch.setattr("alerts.ADMIN_IDS", [777])
    bot = FakeBot()

    await mod.run_error_spike_check(bot)

    assert bot.sent == []


def test_real_error_logging_is_visible_to_error_stats():
    from db import get_error_stats
    before = get_error_stats(hours=1)["total"]
    log_error("test_scope", "boom", user_id=1)
    after = get_error_stats(hours=1)["total"]
    assert after == before + 1
