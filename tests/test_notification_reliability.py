"""
Системная надёжность напоминаний/уведомлений.

Раньше почти все job'ы-напоминания сверяли "== ровно эта минута" без
допуска и часть широковещательных job'ов (_broadcast, run_weekly_report,
run_weekly_habit_analysis) вообще не защищались claim_notification —
повторное/наложившееся срабатывание планировщика могло: (а) молча
пропустить уведомление на весь день, если тик задержался ровно в нужную
минуту, или (б) отправить один и тот же текст дважды. Эти тесты закрывают
оба сценария после фикса.
"""
from datetime import datetime

from db import (
    add_user, set_daily_main_goal, claim_notification, get_notification_delivery_stats,
)

from tests.conftest import sign_init_data  # noqa: F401 (импортируется для побочного эффекта настройки sys.path в некоторых средах)


class FakeBot:
    """Достаточно гибкий, чтобы принимать и позиционный, и именованный chat_id —
    разные job'ы вызывают send_message по-разному (coach._broadcast — позиционно,
    goal_feedback — именованно)."""

    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


class FailingBot:
    """Всегда падает при отправке — для проверки release_notification-при-ошибке."""

    token = "test"

    def __init__(self):
        self.calls = 0

    async def send_message(self, *args, **kwargs):
        self.calls += 1
        raise RuntimeError("telegram недоступен")


# =====================================
# db.streak.in_time_window
# =====================================

def test_in_time_window_matches_exact_start():
    from db.streak import in_time_window
    now = datetime(2026, 1, 1, 10, 0)
    assert in_time_window(now, hour=10, minute=0) is True


def test_in_time_window_matches_within_tolerance():
    from db.streak import in_time_window
    now = datetime(2026, 1, 1, 10, 3)
    assert in_time_window(now, hour=10, minute=0) is True


def test_in_time_window_rejects_outside_tolerance():
    from db.streak import in_time_window
    now = datetime(2026, 1, 1, 10, 5)
    assert in_time_window(now, hour=10, minute=0) is False


def test_in_time_window_handles_hour_boundary_crossing():
    """Окно [23:58, 00:02) физически заканчивается уже на следующих сутках —
    датой конца окна datetime-арифметика занимается сама (см. docstring
    in_time_window), поэтому проверка в 23:59 того же дня должна попадать
    в окно, а не ломаться на границе часа/суток."""
    from db.streak import in_time_window
    now = datetime(2026, 1, 1, 23, 59)
    assert in_time_window(now, hour=23, minute=58) is True


# =====================================
# coach._broadcast — раньше без claim_notification вообще
# =====================================

async def test_broadcast_dedup_sends_once_for_same_day_key(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})

    bot = FakeBot()
    await coach._broadcast(bot, lambda: "привет", "test_broadcast_kind", "2026-W01")
    await coach._broadcast(bot, lambda: "привет", "test_broadcast_kind", "2026-W01")

    assert len(bot.sent) == 1


async def test_broadcast_skips_users_with_reminders_disabled(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 0})

    bot = FakeBot()
    await coach._broadcast(bot, lambda: "привет", "test_broadcast_kind_2", "2026-W01")

    assert bot.sent == []


# =====================================
# coach.run_weekly_report — раньше не было ни проверки reminders, ни дедупа
# =====================================

async def test_weekly_report_skips_when_reminders_disabled(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 0})
    monkeypatch.setattr(
        coach, "get_weekly_summary",
        lambda _uid: {"active_days": 5, "completed": 3, "xp": 10},
    )

    bot = FakeBot()
    await coach.run_weekly_report(bot)

    assert bot.sent == []


async def test_weekly_report_dedup_sends_once_per_week(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(
        coach, "get_weekly_summary",
        lambda _uid: {"active_days": 5, "completed": 3, "xp": 10},
    )

    bot = FakeBot()
    await coach.run_weekly_report(bot)
    await coach.run_weekly_report(bot)

    assert len(bot.sent) == 1


# =====================================
# coach.run_weekly_habit_analysis — резерв ДО платного вызова AI
# =====================================

async def test_weekly_habit_analysis_dedup_skips_second_ai_call(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(
        coach, "get_weekly_habit_breakdown",
        lambda _uid: [{"habit_title": "Вода", "done": 3, "total": 7, "missed": 4}],
    )
    monkeypatch.setattr(coach, "get_ai_style", lambda _uid: "neutral")

    calls = {"n": 0}

    async def fake_ai(*args, **kwargs):
        calls["n"] += 1
        return "разбор недели"

    monkeypatch.setattr(coach, "generate_weekly_habit_feedback", fake_ai)

    bot = FakeBot()
    await coach.run_weekly_habit_analysis(bot)
    await coach.run_weekly_habit_analysis(bot)

    # Повторный прогон не должен ни второй раз слать сообщение, ни второй
    # раз платить за вызов AI.
    assert calls["n"] == 1
    assert len(bot.sent) == 1


# =====================================
# coach.run_day_progress_check — release_notification при неудачной отправке
# =====================================

async def test_day_progress_check_retries_after_send_failure(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Сделать важное дело")  # остаётся невыполненной

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 19, 0, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)

    bot1 = FailingBot()
    await coach.run_day_progress_check(bot1)
    assert bot1.calls == 1

    # Второй тик в то же окно (19:00–19:04): раз первая отправка не удалась,
    # резерв claim_notification должен был освободиться — повтор обязан
    # попытаться снова, а не молчать до завтра.
    bot2 = FailingBot()
    await coach.run_day_progress_check(bot2)
    assert bot2.calls == 1


async def test_day_progress_check_dedup_after_successful_send(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    set_daily_main_goal(uid, "Сделать важное дело")

    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(coach, "get_timezone", lambda _uid: "UTC")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 19, 2, tzinfo=tz)

    monkeypatch.setattr(coach, "datetime", FrozenDatetime)

    bot = FakeBot()
    await coach.run_day_progress_check(bot)
    await coach.run_day_progress_check(bot)

    assert len(bot.sent) == 1


# =====================================
# streak_scheduler — окна допуска вместо "== ровно эта минута"
# =====================================

async def test_streak_risk_23_fires_when_scheduler_tick_is_late(monkeypatch, uid):
    import streak_scheduler

    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # Тик планировщика задержался на 2 минуты после 23:00.
            return datetime(2026, 1, 1, 23, 2, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    assert len(bot.sent) == 1


async def test_streak_risk_skips_users_with_reminders_disabled(monkeypatch, uid):
    import streak_scheduler

    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda: [uid])
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _uid: {"reminders": 0})
    monkeypatch.setattr(streak_scheduler, "get_timezone", lambda _uid: "UTC")
    monkeypatch.setattr(streak_scheduler, "has_completed_today", lambda _uid: False)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 23, 0, tzinfo=tz)

    monkeypatch.setattr(streak_scheduler, "datetime", FrozenDatetime)

    bot = FakeBot()
    await streak_scheduler.run_streak_risk_notifications(bot)

    assert bot.sent == []


# =====================================
# goal_feedback — mark_feedback_sent только когда реально отправили
# =====================================

async def test_goal_feedback_does_not_mark_sent_when_ai_returns_nothing(monkeypatch, uid):
    import goal_feedback

    survey = {"user_id": uid, "life_goal": "цель", "bot_goal": "цель бота"}
    monkeypatch.setattr(goal_feedback, "get_surveys_due_for_feedback", lambda days=7: [survey])
    monkeypatch.setattr(goal_feedback, "get_user", lambda _uid: {"streak": 3})
    monkeypatch.setattr(goal_feedback, "get_progress", lambda _uid: {"completed": 2, "total": 4})

    marked = []
    monkeypatch.setattr(goal_feedback, "mark_feedback_sent", lambda _uid: marked.append(_uid))

    async def fake_analyze(**kwargs):
        return ""  # AI недоступен / пустой ответ

    monkeypatch.setattr(goal_feedback, "analyze_goal_progress", fake_analyze)

    bot = FakeBot()
    await goal_feedback.run_goal_feedback(bot)

    # Раньше mark_feedback_sent вызывался безусловно — пользователь молча
    # терял разбор на 7 дней, хотя фактически ничего не отправили.
    assert marked == []
    assert bot.sent == []


# =====================================
# db.streak.get_notification_delivery_stats — админ-видимость по доставке
# =====================================

def test_notification_delivery_stats_groups_by_kind_across_scopes(uid):
    # get_notification_delivery_stats — глобальная сводка по ВСЕЙ таблице
    # (для этого и задумана — общая admin-видимость), поэтому используем
    # уникальные для этого теста имена kind вместо реальных
    # "day_progress_19"/"habit_checkpoint_10" — иначе счётчик ловил бы ещё
    # и claim'ы из других тестов, гоняющих реальные coach.* job'ы на общей
    # тестовой БД сессии, и число "сколько именно" стало бы недетерминированным.
    kind_a = f"test_kind_a_{uid}"
    kind_b = f"test_kind_b_{uid}"
    day = "2026-01-01"

    # Разные scope (несколько ботов на одной БД) для одного и того же kind
    # должны схлопнуться в одну строку сводки.
    claim_notification(uid, day, kind_a, scope="bot_a")
    claim_notification(uid * 10, day, kind_a, scope="bot_b")
    claim_notification(uid, day, kind_b, scope="bot_a")

    stats = get_notification_delivery_stats(hours=24)
    by_kind = {row["kind"]: row["cnt"] for row in stats}

    assert by_kind.get(kind_a) == 2
    assert by_kind.get(kind_b) == 1


async def test_goal_feedback_marks_sent_when_feedback_actually_delivered(monkeypatch, uid):
    import goal_feedback

    survey = {"user_id": uid, "life_goal": "цель", "bot_goal": "цель бота"}
    monkeypatch.setattr(goal_feedback, "get_surveys_due_for_feedback", lambda days=7: [survey])
    monkeypatch.setattr(goal_feedback, "get_user", lambda _uid: {"streak": 3})
    monkeypatch.setattr(goal_feedback, "get_progress", lambda _uid: {"completed": 2, "total": 4})

    marked = []
    monkeypatch.setattr(goal_feedback, "mark_feedback_sent", lambda _uid: marked.append(_uid))

    async def fake_analyze(**kwargs):
        return "хороший разбор недели"

    monkeypatch.setattr(goal_feedback, "analyze_goal_progress", fake_analyze)

    bot = FakeBot()
    await goal_feedback.run_goal_feedback(bot)

    assert marked == [uid]
    assert len(bot.sent) == 1
