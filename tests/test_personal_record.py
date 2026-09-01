"""
Улучшение #49: пуш "ещё один день до личного рекорда" — best_streak растёт
вместе со streak через MAX() в register_completion и "отстаёт" ровно на то
количество дней, что пользователь потерял после срыва серии.
"""
from datetime import datetime, timedelta

from db import add_user, get_users_near_personal_record
from db.core import connect
from db.streak import register_completion, day_key, local_today

from tests.conftest import sign_init_data  # noqa: F401


def _seed_users_row(uid_, streak=0, best_streak=0):
    add_user(uid_, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=?, best_streak=? WHERE telegram_id=?", (streak, best_streak, uid_))
    conn.commit()
    conn.close()


def _mark_yesterday_completed(uid_):
    yesterday = day_key(local_today(uid_) - timedelta(days=1))
    conn = connect()
    conn.execute(
        "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after) VALUES (?,?,?,?)",
        (uid_, yesterday, "completed", 1),
    )
    conn.commit()
    conn.close()


# =====================================
# register_completion — best_streak через MAX()
# =====================================

def test_best_streak_untouched_when_streak_resets_below_it(uid):
    # Рекорд 10, но серия сорвалась (вчера ничего не было выполнено) —
    # register_completion() начинает новый подъём с 1.
    _seed_users_row(uid, streak=0, best_streak=10)
    register_completion(uid)

    conn = connect()
    row = conn.execute("SELECT streak, best_streak FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()
    assert row["streak"] == 1
    assert row["best_streak"] == 10  # рекорд не тронут


def test_best_streak_updates_when_streak_surpasses_it(uid):
    # Рекорд 9, серия продолжается (вчера был completed) -> сегодня 10-й день,
    # новый рекорд.
    _seed_users_row(uid, streak=9, best_streak=9)
    _mark_yesterday_completed(uid)
    register_completion(uid)

    conn = connect()
    row = conn.execute("SELECT streak, best_streak FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()
    assert row["streak"] == 10
    assert row["best_streak"] == 10


# =====================================
# get_users_near_personal_record
# =====================================

def test_near_personal_record_includes_user_one_day_short(uid):
    _seed_users_row(uid, streak=14, best_streak=15)
    rows = get_users_near_personal_record()
    match = next((r for r in rows if r["user_id"] == uid), None)
    assert match == {"user_id": uid, "streak": 14, "best_streak": 15}


def test_near_personal_record_excludes_user_already_at_record(uid):
    # На активном подъёме best_streak == streak каждый день — это НЕ "на 1
    # день короче", а "уже на пике" — уведомление тут не нужно.
    _seed_users_row(uid, streak=15, best_streak=15)
    rows = get_users_near_personal_record()
    assert not any(r["user_id"] == uid for r in rows)


def test_near_personal_record_excludes_user_far_from_record(uid):
    _seed_users_row(uid, streak=3, best_streak=15)
    rows = get_users_near_personal_record()
    assert not any(r["user_id"] == uid for r in rows)


def test_near_personal_record_excludes_user_with_no_history(uid):
    _seed_users_row(uid, streak=0, best_streak=0)
    rows = get_users_near_personal_record()
    assert not any(r["user_id"] == uid for r in rows)


# =====================================
# streak_scheduler.run_personal_record_notifications
# =====================================

class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


def _at_0900(monkeypatch, streak_scheduler):
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 9, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)


async def test_personal_record_push_sent_at_9am_when_eligible(monkeypatch, uid):
    # get_users_near_personal_record() — глобальный запрос по всей таблице
    # users в общей тестовой БД, поэтому фильтруем отправленные сообщения по
    # своему uid, а не по общей длине bot.sent (другие тесты этого файла
    # тоже оставляют в БД пользователей с тем же streak/best_streak).
    import streak_scheduler

    _seed_users_row(uid, streak=14, best_streak=15)
    _at_0900(monkeypatch, streak_scheduler)

    bot = FakeBot()
    await streak_scheduler.run_personal_record_notifications(bot)

    mine = [t for u, t in bot.sent if u == uid]
    assert len(mine) == 1
    assert "рекорд" in mine[0].lower()


async def test_personal_record_push_not_repeated_same_day(monkeypatch, uid):
    import streak_scheduler

    _seed_users_row(uid, streak=14, best_streak=15)
    _at_0900(monkeypatch, streak_scheduler)

    bot = FakeBot()
    await streak_scheduler.run_personal_record_notifications(bot)
    await streak_scheduler.run_personal_record_notifications(bot)

    mine = [t for u, t in bot.sent if u == uid]
    assert len(mine) == 1


async def test_personal_record_push_skipped_when_reminders_disabled(monkeypatch, uid):
    import streak_scheduler

    _seed_users_row(uid, streak=14, best_streak=15)
    _at_0900(monkeypatch, streak_scheduler)
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 0})

    bot = FakeBot()
    await streak_scheduler.run_personal_record_notifications(bot)

    assert not any(u == uid for u, _t in bot.sent)
