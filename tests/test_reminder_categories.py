"""
Гранулярные напоминания (db/settings.py::reminder_category_enabled /
toggle_reminder_category). Раньше был только один общий тумблер
settings.reminders — "всё или ничего". Теперь можно отключить, например,
только пуши про ударный режим, оставив напоминания по привычкам.
"""
from datetime import datetime

from db import (
    add_user, get_settings, toggle_reminder_category, reminder_category_enabled,
    toggle_reminders, set_daily_main_goal,
)


def test_new_user_has_all_categories_enabled_by_default(uid):
    add_user(uid, "u", "Test")
    settings = get_settings(uid)

    assert reminder_category_enabled(settings, "habits") is True
    assert reminder_category_enabled(settings, "streak") is True
    assert reminder_category_enabled(settings, "digests") is True


def test_toggle_reminder_category_flips_only_that_category(uid):
    add_user(uid, "u", "Test")

    new_value = toggle_reminder_category(uid, "streak")
    assert new_value is False

    settings = get_settings(uid)
    assert reminder_category_enabled(settings, "streak") is False
    # Остальные категории не затронуты.
    assert reminder_category_enabled(settings, "habits") is True
    assert reminder_category_enabled(settings, "digests") is True

    # Переключение обратно возвращает True.
    assert toggle_reminder_category(uid, "streak") is True


def test_toggle_reminder_category_rejects_unknown_category(uid):
    add_user(uid, "u", "Test")
    try:
        toggle_reminder_category(uid, "not_a_real_category")
        assert False, "должно было поднять ValueError"
    except ValueError:
        pass


def test_master_toggle_off_disables_every_category_regardless(uid):
    """reminders=0 должен выключать всё разом, даже если конкретная
    категория сама по себе включена."""
    add_user(uid, "u", "Test")
    toggle_reminders(uid)  # выключает общий тумблер

    settings = get_settings(uid)
    assert reminder_category_enabled(settings, "habits") is False
    assert reminder_category_enabled(settings, "streak") is False
    assert reminder_category_enabled(settings, "digests") is False


class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


async def test_scheduler_job_skips_when_its_category_disabled(monkeypatch, uid):
    """Сквозная проверка: streak-категория выключена — run_streak_risk_notifications
    молчит, но run_day_progress_check (категория habits) всё равно шлёт."""
    import coach
    import streak_scheduler

    add_user(uid, "u", "Test")
    toggle_reminder_category(uid, "streak")  # выключаем только streak
    set_daily_main_goal(uid, "Сделать важное дело")

    # -- streak (должен молчать) --
    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)

    class FrozenDatetimeStreak(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetimeStreak)

    streak_bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(streak_bot)
    assert streak_bot.sent == []

    # -- habits (должен сработать как обычно) --
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetimeHabits(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 19, 0, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetimeHabits)

    habits_bot = FakeBot()
    await coach.run_day_progress_check(habits_bot)
    assert len(habits_bot.sent) == 1
