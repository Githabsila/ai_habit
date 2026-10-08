"""
Заблокировавшие бота пользователи не получают повторных попыток отправки ни
в одной рассылке.

Раньше (см. tests/test_habit_checkpoints.py) пропуск был только у morning_ping
и контрольных точек привычек. Логи прода показали, что остальные job'ы
(streak-risk в 23:00/23:30, возвращение, недельный бонус, напоминания,
вечерняя сверка...) продолжали стучаться к тем же пользователям каждую минуту
своего окна и писать ошибку с трейсбеком.
"""
import importlib
import logging
from datetime import datetime

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage

from db import (
    add_user as _add_user, set_access_status, add_habit, get_all_users, is_bot_blocked, log_error, mark_bot_blocked,
)
from db.streak import get_streak_users


class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


class ForbiddenBot:
    token = "test"

    def __init__(self):
        self.calls = 0

    async def send_message(self, chat_id, text=None, **kwargs):
        self.calls += 1
        raise TelegramForbiddenError(
            method=SendMessage(chat_id=chat_id, text=text or ""),
            message="Forbidden: bot was blocked by the user",
        )



def add_user(uid, username, first_name):
    """Рассылки получают только прошедшие анкету (db/push_access.py) — в этих тестах пользователь «свой»."""
    _add_user(uid, username, first_name)
    set_access_status(uid, "approved")

def _forbidden():
    return TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"),
        message="Forbidden: bot was blocked by the user",
    )


def _freeze(monkeypatch, module, hour, minute=0):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, hour, minute, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)


# =====================================
# Списки пользователей
# =====================================

def test_get_all_users_can_skip_blocked(uid):
    other = uid + 400_000
    add_user(uid, "a", "A")
    add_user(other, "b", "B")
    mark_bot_blocked(uid)

    all_ids = {u["telegram_id"] for u in get_all_users()}
    assert {uid, other} <= all_ids  # по умолчанию — все (статистика, рассылки админа)

    notifiable = {u["telegram_id"] for u in get_all_users(include_blocked=False)}
    assert uid not in notifiable
    assert other in notifiable


def test_get_streak_users_can_skip_blocked(uid):
    other = uid + 400_000
    for user_id in (uid, other):
        add_user(user_id, "u", "U")
        add_habit(user_id, "Пить воду")
    mark_bot_blocked(uid)

    assert {uid, other} <= set(get_streak_users())
    notifiable = set(get_streak_users(include_blocked=False))
    assert uid not in notifiable
    assert other in notifiable


# =====================================
# log_error запоминает постоянный отказ Telegram
# =====================================

def test_log_error_marks_bot_blocked_on_forbidden(uid):
    add_user(uid, "u", "U")
    assert not is_bot_blocked(uid)
    log_error("any_job", _forbidden(), uid)
    assert is_bot_blocked(uid)


def test_log_error_does_not_mark_on_ordinary_errors(uid):
    add_user(uid, "u", "U")
    log_error("any_job", RuntimeError("telegram недоступен"), uid)
    assert not is_bot_blocked(uid)


def test_log_error_without_user_does_not_crash():
    log_error("any_job", _forbidden(), None)


# =====================================
# streak_scheduler
# =====================================

def _patch_streak(monkeypatch, uid, hour, minute=0):
    import streak_scheduler

    real = get_streak_users
    # Реальный фильтр заблокированных, но только по нашему пользователю: общая
    # БД тестового прогона содержит чужих пользователей с привычками.
    monkeypatch.setattr(
        streak_scheduler, "get_streak_users",
        lambda include_blocked=True: [u for u in real(include_blocked=include_blocked) if u == uid],
    )
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)
    _freeze(monkeypatch, streak_scheduler, hour, minute)
    return streak_scheduler


async def test_streak_risk_marks_blocked_once_without_traceback_then_skips(monkeypatch, uid, caplog):
    add_user(uid, "u", "U")
    add_habit(uid, "Пить воду")
    streak_scheduler = _patch_streak(monkeypatch, uid, 23, 0)

    bot = ForbiddenBot()
    with caplog.at_level(logging.WARNING, logger="streak_scheduler"):
        await streak_scheduler.run_streak_risk_notifications(bot)

    assert bot.calls == 1
    assert is_bot_blocked(uid)
    # Одна короткая строка-предупреждение, без ERROR/трейсбека.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("заблокирован" in r.getMessage() for r in caplog.records)

    # Следующий тик окна: больше ни одной попытки.
    await streak_scheduler.run_streak_risk_notifications(bot)
    assert bot.calls == 1


async def test_streak_risk_still_reaches_users_who_did_not_block(monkeypatch, uid):
    add_user(uid, "u", "U")
    add_habit(uid, "Пить воду")
    streak_scheduler = _patch_streak(monkeypatch, uid, 23, 0)

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)
    assert [chat for chat, _ in bot.sent] == [uid]


async def test_reengagement_and_weekly_bonus_mark_blocked_on_forbidden(monkeypatch, uid):
    """Остальные streak-job'ы тоже запоминают отказ, а не падают с трейсбеком."""
    import streak_scheduler

    add_user(uid, "u", "U")
    add_habit(uid, "Пить воду")
    _patch_streak(monkeypatch, uid, 10, 0)
    monkeypatch.setattr(
        streak_scheduler, "get_streak_reengagement_state",
        lambda _uid: {"inactive_days": 2, "has_history": True},
    )

    bot = ForbiddenBot()
    await streak_scheduler.run_streak_reengagement_notifications(bot)
    assert bot.calls == 1
    assert is_bot_blocked(uid)


async def test_personal_record_skips_blocked_user(monkeypatch, uid):
    import streak_scheduler

    add_user(uid, "u", "U")
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(
        streak_scheduler, "get_users_near_personal_record",
        lambda: [{"user_id": uid, "streak": 5, "best_streak": 6}],
    )
    _freeze(monkeypatch, streak_scheduler, 9, 0)

    ok_bot = FakeBot()
    await streak_scheduler.run_personal_record_notifications(ok_bot)
    assert [chat for chat, _ in ok_bot.sent] == [uid]  # контроль: без блокировки уходит

    other = uid + 400_000
    add_user(other, "u2", "U2")
    mark_bot_blocked(other)
    monkeypatch.setattr(
        streak_scheduler, "get_users_near_personal_record",
        lambda: [{"user_id": other, "streak": 5, "best_streak": 6}],
    )
    blocked_bot = FakeBot()
    await streak_scheduler.run_personal_record_notifications(blocked_bot)
    assert blocked_bot.sent == []


# =====================================
# Рассылочные job'ы просят список без заблокированных
# =====================================

BROADCAST_JOBS = [
    ("coach", "run_weekly_report"),
    ("coach", "run_task_reminder_check"),
    ("coach", "run_planned_time_reminders"),
    ("coach", "run_habit_checkpoint_10"),
    ("coach", "run_day_progress_check"),
    ("coach", "run_week_start_ping"),
    ("coach", "run_weekly_habit_analysis"),
    ("coach", "run_monthly_habit_analysis"),
    ("morning_ping", "run_morning_ping"),
    ("subscription_scheduler", "run_trial_reminders"),
]


@pytest.mark.parametrize("module_name,job_name", BROADCAST_JOBS)
async def test_broadcast_job_excludes_blocked_users(monkeypatch, module_name, job_name):
    module = importlib.import_module(module_name)
    calls = []
    monkeypatch.setattr(module, "get_all_users", lambda **kw: calls.append(kw) or [])

    await getattr(module, job_name)(FakeBot())

    assert calls, f"{module_name}.{job_name} не запрашивал список пользователей"
    assert all(kw == {"include_blocked": False} for kw in calls)


@pytest.mark.parametrize("job_name", [
    "run_streak_risk_notifications",
    "run_streak_reengagement_notifications",
    "run_weekly_streak_bonus",
])
async def test_streak_jobs_exclude_blocked_users(monkeypatch, job_name):
    import streak_scheduler

    calls = []
    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda **kw: calls.append(kw) or [])

    await getattr(streak_scheduler, job_name)(FakeBot())

    assert calls
    assert all(kw == {"include_blocked": False} for kw in calls)
