"""
Профиль другого игрока — экран «как в Duolingo»: шапка, подписки/подписчики,
график опыта за неделю в сравнении с тобой, обзор, достижения.

Что видно, определяет отношение зрителя к владельцу профиля:
  public      — не подписан: только то, что и так видно в рейтинге;
  subscriber  — подписан на него: + график недели, обзор, достижения;
  friend      — взаимная подписка: + расширенная статистика (привычки за
                сегодня, процент выполнения за 30 дней, активные дни);
  self        — свой профиль: всё.
Таблица VISIBLE_SECTIONS — единственное место, где это решается, чтобы
«поэкспериментировать, кому что показывать» можно было правкой одной строки.
Сам владелец может сузить доступ: stats_visibility='friends' (Настройки) —
тогда подписчики видят лишь публичный минимум.

Названия привычек и текст целей по умолчанию НЕ отдаются никому — только
числа. Владелец может разрешить их ДРУЗЬЯМ (Настройки → «Показывать друзьям
мои привычки и цели», settings.share_habits): тогда друзья видят список
привычек со статусом на сегодня и цели. Подписчикам они недоступны никогда.
"""
from datetime import date, timedelta

from .core import connect

VISIBLE_SECTIONS = {
    "public": ("basic",),
    "subscriber": ("basic", "chart", "overview", "achievements"),
    "friend": ("basic", "chart", "overview", "achievements", "extended"),
    "self": ("basic", "chart", "overview", "achievements", "extended"),
}

RU_WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
CHART_DAYS = 7
MAX_ACHIEVEMENTS = 12
MAX_SHARED_HABITS = 20
MAX_SHARED_TITLE = 80
MAX_SHARED_GOALS = 500
BADGE_ITEM_ID = 3  # как в webapp_server.py и db/public_profile.py


def tier_for(viewer_id, target_id, relation, target_visibility):
    if viewer_id == target_id:
        return "self"
    if relation["friends"]:
        return "friend"
    if relation["following"]:
        return "public" if target_visibility == "friends" else "subscriber"
    return "public"


def _week_series(user_id, today):
    """XP и число отметок по каждому из последних CHART_DAYS дней (нули для
    дней без активности) — statistics пишет по строке на событие."""
    from .statistics import get_daily_statistics
    by_date = {row["date"]: row for row in get_daily_statistics(user_id, days=CHART_DAYS)}
    days = [today - timedelta(days=offset) for offset in range(CHART_DAYS - 1, -1, -1)]
    xp = [int(by_date.get(str(d), {}).get("xp", 0) or 0) for d in days]
    completed = [int(by_date.get(str(d), {}).get("completed", 0) or 0) for d in days]
    return days, xp, completed


def _shared_habits(user_id):
    """Привычки со статусом на сегодня и текст долгосрочных целей — только
    когда владелец включил показ друзьям (см. _extended)."""
    from .habits import get_habits
    from .users import get_long_term_goals

    habits = [
        {
            "title": str(h["title"] or "")[:MAX_SHARED_TITLE],
            "done": bool(h["completed"]),
            "skipped": bool(h["skip_reason"]) if "skip_reason" in h.keys() else False,
        }
        for h in get_habits(user_id)[:MAX_SHARED_HABITS]
    ]
    goals = (get_long_term_goals(user_id) or "").strip()[:MAX_SHARED_GOALS] or None
    return habits, goals


def _extended(user_id, completed_by_day):
    from .habits import get_progress
    from .seasons import get_season_rank
    from .settings import get_settings, share_habits_enabled
    from .streak import has_completed_today

    progress = get_progress(user_id) or {}
    conn = connect()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(completed), 0) AS done, COALESCE(SUM(total), 0) AS total,
                   COUNT(DISTINCT CASE WHEN completed > 0 THEN day END) AS active_days
            FROM calendar
            WHERE user_id=? AND day >= date('now', '-29 days')
            """,
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    total = int(row["total"] or 0)
    extended = {
        "today": {
            "completed": int(progress.get("completed", 0)),
            "total": int(progress.get("total", 0)),
            "done": has_completed_today(user_id),
        },
        "completion_rate_30d": round(100 * int(row["done"] or 0) / total) if total else None,
        "active_days_30": int(row["active_days"] or 0),
        "week_completed": completed_by_day,
        "season": get_season_rank(user_id),
        "habits_shared": share_habits_enabled(get_settings(user_id)),
        "habits": None,
        "goals": None,
    }
    if extended["habits_shared"]:
        extended["habits"], extended["goals"] = _shared_habits(user_id)
    return extended


def get_player_profile(viewer_id, target_id):
    """None — такого игрока нет, он забанен или заблокировал зрителя."""
    from .achievements import get_achievements, ACHIEVEMENT_ICONS
    from .follows import get_counts, get_relation
    from .leagues import get_league_tier
    from .settings import get_settings, get_stats_visibility
    from .shop import has_item

    conn = connect()
    try:
        user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (target_id,)).fetchone()
    finally:
        conn.close()
    if user is None or user["banned"]:
        return None
    relation = get_relation(viewer_id, target_id)
    if relation["blocked_me"]:
        return None

    tier = tier_for(viewer_id, target_id, relation, get_stats_visibility(get_settings(target_id)))
    sections = VISIBLE_SECTIONS[tier]
    keys = user.keys()
    total_xp = user["total_xp"] if "total_xp" in keys else user["xp"]
    counts = get_counts(target_id)

    profile = {
        "telegram_id": target_id,
        "first_name": user["first_name"] or user["username"] or "Игрок",
        "handle": user["handle"] if "handle" in keys else None,
        "avatar_id": (user["avatar_id"] if "avatar_id" in keys else None) or "default",
        "frame_id": (user["frame_id"] if "frame_id" in keys else None) or "default",
        "level": user["level"],
        "streak": int(user["streak"] or 0),
        "league_tier": get_league_tier(total_xp),
        "badge": has_item(target_id, BADGE_ITEM_ID),
        "member_since": str(user["created_at"]) if "created_at" in keys and user["created_at"] else None,
        "followers": counts["followers"],
        "following": counts["following"],
        "relation": {**relation, "self": tier == "self"},
        "tier": tier,
        "sections": list(sections),
    }

    today = date.today()
    days, target_xp, target_completed = _week_series(target_id, today)
    if "chart" in sections:
        viewer_xp = None
        if tier != "self":
            _, viewer_xp, _ = _week_series(viewer_id, today)
        profile["chart"] = {
            "labels": [RU_WEEKDAYS[d.weekday()] for d in days],
            "target": target_xp,
            "viewer": viewer_xp,
            "target_total": sum(target_xp),
            "viewer_total": sum(viewer_xp) if viewer_xp is not None else None,
        }
    if "overview" in sections:
        profile["overview"] = {
            "streak": int(user["streak"] or 0),
            "best_streak": int((user["best_streak"] if "best_streak" in keys else 0) or 0),
            "league": get_league_tier(total_xp),
            "total_xp": int(total_xp or 0),
            "level": user["level"],
            "total_completed": int((user["total_completed"] if "total_completed" in keys else 0) or 0),
        }
    if "achievements" in sections:
        achievements = get_achievements(target_id)
        profile["achievements"] = [
            {"title": a["title"], "icon": ACHIEVEMENT_ICONS.get(a["title"], "🏅")}
            for a in achievements[:MAX_ACHIEVEMENTS]
        ]
        profile["achievements_count"] = len(achievements)
    if "extended" in sections:
        profile["extended"] = _extended(target_id, target_completed)
    return profile
