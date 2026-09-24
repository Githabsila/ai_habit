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


async def test_checkpoint_silent_when_only_incomplete_habit_has_future_timer(monkeypatch, uid):
    """Просьба пользователя: если у всех оставшихся привычек ещё не
    наступило собственное время (planned_time), общая точка дня молчит —
    по каждой такой привычке своё отдельное напоминание всё равно придёт
    ровно в её время (run_planned_time_reminders), дублировать это было бы
    спамом."""
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Спорт", planned_time="23:00")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 12, 0)  # день, до 23:00

    bot = FakeBot()
    await coach.run_habit_checkpoint_12(bot)

    assert bot.sent == []


async def test_checkpoint_mentions_free_habit_and_appends_timed_note(monkeypatch, uid):
    """Если помимо привычки со своим (будущим) временем остаётся хотя бы
    одна обычная привычка без времени — точка дня отправляется как раньше,
    а привычка со временем добавляется доп. фразой "Не забудь в HH:MM"."""
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Растяжка")
    add_habit(uid, "Спорт", planned_time="23:00")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 12, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_12(bot)

    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert "Растяжка" in text
    assert "Не забудь в 23:00 — «Спорт»" in text


async def test_checkpoint_timed_note_lists_multiple_habits_sorted_by_time(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Растяжка")
    add_habit(uid, "Растяжка вечером", planned_time="23:00")
    add_habit(uid, "Обед", planned_time="18:00")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 12, 0)

    bot = FakeBot()
    await coach.run_habit_checkpoint_12(bot)

    text = bot.sent[0][1]
    assert "в 18:00 — «Обед»" in text
    assert "в 23:00 — «Растяжка вечером»" in text
    assert text.index("18:00") < text.index("23:00")


async def test_checkpoint_treats_past_due_timed_habit_as_normal(monkeypatch, uid):
    """Если собственное время привычки уже прошло, а она всё ещё не
    выполнена — это обычная просроченная привычка, а не "ещё не
    наступившее время": она должна попасть в основной список, а не в
    доп. фразу "Не забудь"."""
    import coach

    add_user(uid, "u", "Test")
    add_habit(uid, "Спорт", planned_time="10:00")
    _patch_user(monkeypatch, uid)
    _freeze(monkeypatch, 12, 0)  # 10:00 уже прошло

    bot = FakeBot()
    await coach.run_habit_checkpoint_12(bot)

    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert "Спорт" in text
    assert "Не забудь" not in text


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
