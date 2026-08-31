"""
Своё время напоминания у привычки (habits.planned_time):
- API /api/habits и /api/habits/{id} принимают/валидируют/очищают его;
- coach.run_planned_time_reminders шлёт сообщение только тем, у кого
  время совпало, привычка не выполнена и сегодня ещё не напоминали.
"""
from db import add_user, get_habits, add_habit, mark_habit_reminder_sent, complete_habit

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_create_habit_with_valid_planned_time(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post("/api/habits", headers=headers, data='{"title": "Пить воду", "planned_time": "09:30"}')

    assert r.status == 200
    data = await r.json()
    assert data["habit"]["planned_time"] == "09:30"


async def test_create_habit_rejects_malformed_time(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)

    r = await client.post("/api/habits", headers=headers, data='{"title": "Бегать", "planned_time": "25:99"}')

    assert r.status == 400
    body = await r.json()
    assert body["error"] == "invalid_time"


async def test_update_habit_can_clear_planned_time(client, uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Читать", planned_time="20:00")
    habit_id = get_habits(uid)[0]["id"]
    headers = await _headers(uid)

    r = await client.put(f"/api/habits/{habit_id}", headers=headers, data='{"title": "Читать", "planned_time": ""}')

    assert r.status == 200
    updated = next(h for h in get_habits(uid) if h["id"] == habit_id)
    assert updated["planned_time"] is None


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


async def test_planned_time_reminder_fires_only_at_matching_minute(monkeypatch, uid):
    import coach
    from datetime import datetime

    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка", planned_time="07:00")

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 8, 15, tzinfo=tz)  # не совпадает с 07:00

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)
    bot = FakeBot()

    await coach.run_planned_time_reminders(bot)
    assert bot.sent == []


async def test_planned_time_reminder_fires_at_matching_minute_once(monkeypatch, uid):
    import coach
    from datetime import datetime

    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка", planned_time="07:00")

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 7, 0, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)
    bot = FakeBot()

    await coach.run_planned_time_reminders(bot)
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == uid

    # Второй прогон в ту же минуту — уже не должен слать повторно
    # (reminder_sent выставлен после первой отправки).
    bot2 = FakeBot()
    await coach.run_planned_time_reminders(bot2)
    assert bot2.sent == []


async def test_planned_time_reminder_skips_completed_habit(monkeypatch, uid):
    import coach
    from datetime import datetime

    add_user(uid, "u", "Test")
    add_habit(uid, "Зарядка", planned_time="07:00")
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 7, 0, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)
    bot = FakeBot()

    await coach.run_planned_time_reminders(bot)
    assert bot.sent == []
