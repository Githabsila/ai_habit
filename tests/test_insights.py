"""
Roadmap #10 (коллекционные бейджи), #24 (месячный AI-разбор), #29 ("я
сейчас vs я месяц назад"), #30 (прогноз следующего рубежа серии).
"""
from datetime import date, timedelta

from db import (
    add_user, check_achievements, get_achievements, ACHIEVEMENT_ICONS,
    get_progress_comparison, get_streak_forecast,
    get_monthly_habit_breakdown, add_habit, get_habits, complete_habit, log_daily_habits,
)
from db.core import connect

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# КОЛЛЕКЦИОННЫЕ БЕЙДЖИ
# =====================================

def test_new_streak_milestones_award_badges(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=30 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    check_achievements(uid)
    titles = {a["title"] for a in get_achievements(uid)}
    assert "Железная воля" in titles
    assert "Легенда" not in titles  # порог 100, ещё не достигнут


def test_legend_badge_at_100_streak(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=100 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    check_achievements(uid)
    titles = {a["title"] for a in get_achievements(uid)}
    assert "Легенда" in titles


def test_marathon_badge_at_200_completed(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET total_completed=200 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    check_achievements(uid)
    titles = {a["title"] for a in get_achievements(uid)}
    assert "Марафонец" in titles


def test_achievement_icons_cover_all_titles(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=100, total_completed=200, xp=100 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    check_achievements(uid)
    for a in get_achievements(uid):
        assert a["title"] in ACHIEVEMENT_ICONS


async def test_api_exposes_achievement_icon(client, uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET total_completed=1 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    check_achievements(uid)

    headers = await _headers(uid)
    r = await client.get("/api/bootstrap-secondary?section=profile", headers=headers)
    assert r.status == 200
    data = await r.json()
    first = next(a for a in data["achievements"] if a["title"] == "Первый шаг")
    assert first["icon"] == "🥾"


# =====================================
# СРАВНЕНИЕ С МЕСЯЦЕМ НАЗАД (#29)
# =====================================

def _insert_calendar_day(user_id, days_ago, completed, total):
    conn = connect()
    day = str(date.today() - timedelta(days=days_ago))
    conn.execute(
        "INSERT INTO calendar(user_id, day, completed, total) VALUES (?, ?, ?, ?)",
        (user_id, day, completed, total),
    )
    conn.commit()
    conn.close()


def test_progress_comparison_not_enough_data_when_no_history(uid):
    add_user(uid, "u", "Test")
    result = get_progress_comparison(uid)
    assert result["trend"] == "not_enough_data"


def test_progress_comparison_detects_improvement(uid):
    add_user(uid, "u", "Test")
    # Последние 7 дней: 100% выполнения.
    for d in range(1, 6):
        _insert_calendar_day(uid, d, completed=2, total=2)
    # ~месяц назад: 50% выполнения.
    for d in range(29, 34):
        _insert_calendar_day(uid, d, completed=1, total=2)

    result = get_progress_comparison(uid)
    assert result["current_rate"] == 100
    assert result["previous_rate"] == 50
    assert result["trend"] == "up"
    assert result["delta"] == 50


# =====================================
# ПРОГНОЗ РУБЕЖА СЕРИИ (#30)
# =====================================

def test_streak_forecast_returns_next_milestone(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=25 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    forecast = get_streak_forecast(uid)
    assert forecast["current_streak"] == 25
    assert forecast["next_milestone"] == 30
    assert forecast["days_left"] == 5


def test_streak_forecast_none_when_no_streak(uid):
    add_user(uid, "u", "Test")
    assert get_streak_forecast(uid) is None


def test_streak_forecast_none_past_last_milestone(uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=400 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    assert get_streak_forecast(uid) is None


# =====================================
# МЕСЯЧНЫЙ РАЗБОР ПО ПРИВЫЧКАМ (#24)
# =====================================

def test_monthly_habit_breakdown_aggregates_over_30_days(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Пить воду")
    habit_id = get_habits(uid)[0]["id"]
    complete_habit(habit_id)
    log_daily_habits()

    breakdown = get_monthly_habit_breakdown(uid)
    row = next(r for r in breakdown if r["habit_title"] == "Пить воду")
    assert row["done"] == 1
    assert row["total"] == 1


class FakeBot:
    token = "test"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text=None, **kwargs):
        self.sent.append((chat_id, text))


async def test_monthly_habit_analysis_sends_and_dedups(monkeypatch, uid):
    import coach

    add_user(uid, "u", "Test")
    monkeypatch.setattr(coach, "get_all_users", lambda: [{"telegram_id": uid}])
    monkeypatch.setattr(coach, "get_settings", lambda _uid: {"reminders": 1, "reminders_digests": 1})
    monkeypatch.setattr(
        coach, "get_monthly_habit_breakdown",
        lambda _uid: [{"habit_title": "Вода", "done": 20, "total": 30, "missed": 10}],
    )
    monkeypatch.setattr(coach, "get_ai_style", lambda _uid: "neutral")

    async def fake_ai(*a, **kw):
        return "разбор месяца"
    monkeypatch.setattr(coach, "generate_monthly_habit_feedback", fake_ai)

    bot = FakeBot()
    await coach.run_monthly_habit_analysis(bot)
    await coach.run_monthly_habit_analysis(bot)

    assert len(bot.sent) == 1
    assert "разбор месяца" in bot.sent[0][1]


async def test_api_progress_stats_includes_comparison_and_forecast(client, uid):
    add_user(uid, "u", "Test")
    conn = connect()
    conn.execute("UPDATE users SET streak=10 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()

    headers = await _headers(uid)
    r = await client.get("/api/progress/stats", headers=headers)
    assert r.status == 200
    data = await r.json()
    assert "comparison" in data
    assert data["forecast"]["current_streak"] == 10
