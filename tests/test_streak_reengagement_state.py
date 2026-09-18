"""
Жалоба пользователя: в 10 утра пришло реэнгейджмент-сообщение "Один пропуск
не решает, кем ты будешь дальше" при полностью активной серии — пропуска не
было, вчера всё было закрыто как обычно, просто СЕГОДНЯШНИЙ день ещё не
закончился. Та же причина параллельно ГЛУШИЛА обычные напоминания о
незакрытых привычках (coach.py читает inactive_days>0 как "человек выпал из
режима" и уступает слот реэнгейджменту).

Корень: get_streak_reengagement_state() считала inactive_days как
(today - last_completed_day).days БЕЗ поправки — это даёт 1 уже в первую же
минуту нового дня, хотя её собственный docstring прямо обещает "текущий день
не считается новым пропуском, пока он не закончился". Поправка -1 возвращает
смысл к обещанному: inactive_days = число ПОЛНОСТЬЮ прошедших дней без
отметки, не считая ещё идущий сегодняшний день.
"""
from datetime import datetime, timedelta

from db import add_user
from db.core import connect
from db.streak import local_today, day_key, get_streak_reengagement_state


def _seed_completed_day(uid, day_str, streak_after=1):
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?,?,?)",
        (uid, day_str, "completed", streak_after),
    )
    conn.commit()
    conn.close()


def test_not_inactive_when_yesterday_completed_and_today_still_pending(uid):
    add_user(uid, "u", "Test")
    today = local_today(uid)
    _seed_completed_day(uid, day_key(today - timedelta(days=1)), streak_after=5)

    state = get_streak_reengagement_state(uid)

    assert state["has_history"] is True
    assert state["inactive_days"] == 0


def test_detects_genuine_one_day_gap(uid):
    """Вчера ничего не сделано, позавчера — да: это уже настоящий пропуск,
    реэнгейджмент должен продолжать срабатывать (фикс не должен выключить
    фичу целиком, только убрать ложное срабатывание в день 0)."""
    add_user(uid, "u", "Test")
    today = local_today(uid)
    _seed_completed_day(uid, day_key(today - timedelta(days=2)), streak_after=3)

    state = get_streak_reengagement_state(uid)

    assert state["inactive_days"] == 1


def test_no_history_reports_zero_inactive_days(uid):
    add_user(uid, "u", "Test")
    state = get_streak_reengagement_state(uid)
    assert state["has_history"] is False
    assert state["inactive_days"] == 0


# =====================================
# streak_scheduler.run_streak_reengagement_notifications — не должен слать
# "ты выпал из режима" тому, кто просто ещё не отметился сегодня.
# =====================================

class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


async def test_reengagement_scheduler_silent_when_streak_is_healthy(monkeypatch, uid):
    import streak_scheduler

    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)
    monkeypatch.setattr(
        streak_scheduler, "get_streak_reengagement_state",
        lambda _uid: {"has_history": True, "inactive_days": 0, "last_completed": "2026-01-01", "streak": 5},
    )

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 10, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_streak_reengagement_notifications(bot)

    assert bot.sent == []


async def test_reengagement_scheduler_still_fires_for_a_real_gap(monkeypatch, uid):
    import streak_scheduler

    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)
    monkeypatch.setattr(
        streak_scheduler, "get_streak_reengagement_state",
        lambda _uid: {"has_history": True, "inactive_days": 1, "last_completed": "2025-12-31", "streak": 0},
    )

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 10, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_streak_reengagement_notifications(bot)

    assert len(bot.sent) == 1
