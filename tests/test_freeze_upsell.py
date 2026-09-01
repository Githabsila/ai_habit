"""
Улучшение #38 ("стрик-страховка"): в момент 23:00-риска (0 привычек за день)
пользователь с длинной серией и без единой заморозки в запасе получает мягкое
предложение купить заморозку — не чаще раза в неделю.
"""
from datetime import datetime

from db import add_user, get_freeze_upsell_eligibility
from db.core import connect

from tests.conftest import sign_init_data  # noqa: F401


def _seed(uid_, streak=0, xp=0, freeze_balance=0):
    add_user(uid_, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=?, xp=? WHERE telegram_id=?", (streak, xp, uid_))
    conn.execute(
        "INSERT INTO streak_meta(user_id, freeze_balance) VALUES(?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET freeze_balance=excluded.freeze_balance",
        (uid_, freeze_balance),
    )
    conn.commit()
    conn.close()


# =====================================
# db.streak.get_freeze_upsell_eligibility
# =====================================

def test_freeze_eligibility_reads_streak_xp_and_balance(uid):
    _seed(uid, streak=12, xp=350, freeze_balance=1)
    info = get_freeze_upsell_eligibility(uid)
    assert info == {"streak": 12, "xp": 350, "freeze_balance": 1}


def test_freeze_eligibility_defaults_for_unknown_user(uid):
    info = get_freeze_upsell_eligibility(uid)
    assert info == {"streak": 0, "xp": 0, "freeze_balance": 0}


# =====================================
# streak_scheduler — интеграция через run_streak_risk_notifications
# =====================================

class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


def _freeze_it_2300(monkeypatch, streak_scheduler, uid_list):
    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: uid_list)
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)


async def test_freeze_upsell_sent_when_eligible(monkeypatch, uid):
    import streak_scheduler

    _seed(uid, streak=10, xp=500, freeze_balance=0)
    _freeze_it_2300(monkeypatch, streak_scheduler, [uid])

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    # risk23 + freeze_upsell — два разных сообщения.
    assert len(bot.sent) == 2
    assert "заморозк" in bot.sent[1][1].lower()


async def test_freeze_upsell_skipped_when_balance_already_positive(monkeypatch, uid):
    import streak_scheduler

    _seed(uid, streak=10, xp=500, freeze_balance=1)
    _freeze_it_2300(monkeypatch, streak_scheduler, [uid])

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    assert len(bot.sent) == 1  # только risk23


async def test_freeze_upsell_skipped_below_streak_threshold(monkeypatch, uid):
    import streak_scheduler

    _seed(uid, streak=3, xp=500, freeze_balance=0)
    _freeze_it_2300(monkeypatch, streak_scheduler, [uid])

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    assert len(bot.sent) == 1


async def test_freeze_upsell_skipped_when_not_enough_coins(monkeypatch, uid):
    import streak_scheduler

    _seed(uid, streak=10, xp=50, freeze_balance=0)
    _freeze_it_2300(monkeypatch, streak_scheduler, [uid])

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    assert len(bot.sent) == 1


async def test_freeze_upsell_not_repeated_same_week(monkeypatch, uid):
    import streak_scheduler

    _seed(uid, streak=10, xp=500, freeze_balance=0)
    _freeze_it_2300(monkeypatch, streak_scheduler, [uid])

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)
    await streak_scheduler.run_streak_risk_notifications(bot)

    freeze_msgs = [t for _, t in bot.sent if "заморозк" in (t or "").lower()]
    assert len(freeze_msgs) == 1
