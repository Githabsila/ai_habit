"""
Улучшение #40: "тебя обогнали в рейтинге" — сравнение текущего сезонного
рейтинга со вчерашним снимком (db/seasons.py::get_rank_overtakes_and_update_snapshot),
плюс раз-в-сутки пуш через streak_scheduler.run_rank_overtaken_notifications.

Сезонный лидерборд — ГЛОБАЛЬНЫЙ запрос по всем пользователям в общей
тестовой БД, поэтому здесь намеренно используются очень большие season_xp
(миллионы), чтобы тестовые пользователи гарантированно доминировали над
любым мусором, оставленным другими тестами (обычно там десятки/сотни XP).
"""
from datetime import datetime

from db import (
    add_user, add_xp, clear_season_leaderboard_cache,
    get_rank_overtakes_and_update_snapshot,
)
from db.core import connect

from tests.conftest import sign_init_data  # noqa: F401


def _give_season_xp(uid_, first_name, xp):
    add_user(uid_, "u", first_name)
    add_xp(uid_, xp)
    conn = connect()
    conn.execute(
        "INSERT INTO statistics(user_id, completed, gained_xp, stat_date) VALUES (?,1,?,date('now'))",
        (uid_, xp),
    )
    conn.commit()
    conn.close()
    clear_season_leaderboard_cache()


# =====================================
# db.seasons.get_rank_overtakes_and_update_snapshot
# =====================================

def test_first_check_has_nothing_to_compare_against(uid):
    _give_season_xp(uid, "Первый", 1_000_000)
    overtaken = get_rank_overtakes_and_update_snapshot()
    # Первый вызов для этого пользователя — снимка ещё не было, сравнивать
    # не с чем, поэтому в списке "обогнали" его быть не может.
    assert not any(o["user_id"] == uid for o in overtaken)


def test_detects_overtake_after_someone_gains_more_xp(uid):
    other_uid = uid + 50_000_000
    _give_season_xp(uid, "Игорь", 1_000_000)
    get_rank_overtakes_and_update_snapshot()  # первый снимок для uid

    _give_season_xp(other_uid, "Соперник", 2_000_000)
    overtaken = get_rank_overtakes_and_update_snapshot()

    mine = next((o for o in overtaken if o["user_id"] == uid), None)
    assert mine is not None
    assert mine["new_rank"] > mine["old_rank"]
    assert mine["overtaker_name"] == "Соперник"


def test_no_overtake_when_rank_unchanged(uid):
    _give_season_xp(uid, "Стабильный", 1_000_000)
    get_rank_overtakes_and_update_snapshot()

    # Второй вызов без изменений XP — рейтинг тот же.
    overtaken = get_rank_overtakes_and_update_snapshot()
    assert not any(o["user_id"] == uid for o in overtaken)


def test_no_overtake_when_rank_improves(uid):
    other_uid = uid + 50_000_000
    _give_season_xp(uid, "Растущий", 1_000_000)
    _give_season_xp(other_uid, "ПокаВыше", 2_000_000)
    get_rank_overtakes_and_update_snapshot()

    # uid обгоняет other_uid, а не наоборот -> для uid уведомления быть не должно.
    _give_season_xp(uid, "Растущий", 5_000_000)
    overtaken = get_rank_overtakes_and_update_snapshot()
    assert not any(o["user_id"] == uid for o in overtaken)


# =====================================
# streak_scheduler.run_rank_overtaken_notifications
# =====================================

class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


async def test_scheduler_sends_push_at_8am_utc(monkeypatch, uid):
    import streak_scheduler

    other_uid = uid + 50_000_000
    _give_season_xp(uid, "Пуш", 1_000_000)
    get_rank_overtakes_and_update_snapshot()
    _give_season_xp(other_uid, "Обгоняющий", 2_000_000)

    # Сбрасываем внутригей-процессный дневной гейт и настройки/часовой пояс.
    monkeypatch.setattr(streak_scheduler, "_last_rank_check_day", None)
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 8, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_rank_overtaken_notifications(bot)

    mine = [t for u, t in bot.sent if u == uid]
    assert len(mine) == 1
    assert "обогнал" in mine[0].lower()


async def test_scheduler_runs_only_once_per_day(monkeypatch, uid):
    import streak_scheduler

    other_uid = uid + 50_000_000
    _give_season_xp(uid, "Разово", 1_000_000)
    get_rank_overtakes_and_update_snapshot()
    _give_season_xp(other_uid, "Обгонятель", 2_000_000)

    monkeypatch.setattr(streak_scheduler, "_last_rank_check_day", None)
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 8, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_rank_overtaken_notifications(bot)
    await streak_scheduler.run_rank_overtaken_notifications(bot)

    mine = [t for u, t in bot.sent if u == uid]
    assert len(mine) == 1  # второй прогон в тот же день — no-op по глобальному гейту
