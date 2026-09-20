"""
Контрольные точки по привычкам в течение дня (coach._run_habit_checkpoint).

До этого теста расписание было 10:00 и 12:00, а следующая проверка —
только в 23:00 risk-уведомление, которое молчит, если за день уже
закрыта хотя бы одна привычка. При частичном выполнении (например, 1 из 4)
пользователь мог не получить ни одного напоминания с полудня до вечера.
17:00 и 22:00 закрывают эту дыру.
"""
from datetime import datetime

from db import add_user, add_habit, get_habits, complete_habit


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _freeze(monkeypatch, hour, minute=0):
    import coach

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, hour, minute, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)


def _patch_user(monkeypatch, uid):
    import coach

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")


async def test_checkpoint_17_fires_when_habit_still_open(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 17, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_17(bot)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == uid


async def test_checkpoint_22_fires_when_habit_still_open(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 22, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_22(bot)

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == uid


async def test_checkpoint_17_still_fires_after_partial_completion(monkeypatch, uid):
    """Ключевой случай из жалобы: 1 из 4 привычек закрыта днём, остальные
    3 — нет. 23:00 risk-уведомление в этом случае молчит (has_completed_today
    уже True), поэтому именно 17:00/22:00 должны напомнить про оставшиеся."""
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    add_habit(uid, "Спорт")
    add_habit(uid, "Чтение")
    add_habit(uid, "Медитация")
    done_id = get_habits(uid)[0]["id"]
    complete_habit(done_id)

    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 17, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_17(bot)

    assert len(bot.sent) == 1


async def test_checkpoint_17_silent_when_all_habits_done(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    complete_habit(get_habits(uid)[0]["id"])

    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 17, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_17(bot)

    assert bot.sent == []


async def test_checkpoint_17_does_not_fire_outside_its_window(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 18, 0)  # мимо окна 17:00

    bot = FakeBot()
    await coach.run_habit_checkpoint_17(bot)

    assert bot.sent == []


async def test_checkpoint_17_and_22_use_independent_dedup_keys(monkeypatch, uid):
    """17:00 и 22:00 не должны блокировать друг друга через claim_notification —
    у каждого свой kind, поэтому оба сообщения в один день должны дойти."""
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    _patch_user(monkeypatch, uid)

    _freeze(monkeypatch, 17, 0)
    bot17 = FakeBot()
    await coach.run_habit_checkpoint_17(bot17)
    assert len(bot17.sent) == 1

    _freeze(monkeypatch, 22, 0)
    bot22 = FakeBot()
    await coach.run_habit_checkpoint_22(bot22)
    assert len(bot22.sent) == 1
