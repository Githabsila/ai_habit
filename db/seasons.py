"""
Roadmap #9 — сезонные ивенты. Осознанный масштаб: сезон = календарный
месяц — тот же ритм, что уже использует награда за идеальный месяц (см.
db/monthly_streak.py) — не отдельный, рассинхронизирующийся календарь.
Рейтинг сезона считается "на лету" суммой gained_xp из statistics за
текущий месяц (та же таблица, что уже питает /api/progress/stats), без
отдельного счётчика, который пришлось бы аккуратно инкрементировать в
каждом месте начисления XP. В конце месяца (day=1 следующего) топ-3
получают разовую награду — см. season_scheduler.py.
"""
from datetime import date

from .core import connect

SEASON_TOP_REWARDS = {1: (300, 3), 2: (200, 2), 3: (100, 1)}  # rank: (coins, diamonds)


def current_season_key():
    return date.today().strftime("%Y-%m")


def get_season_leaderboard(limit=10):
    """Топ пользователей по Adam Coin, заработанным ЗА ТЕКУЩИЙ сезон
    (месяц) — отдельно от общего рейтинга (db/users.py::get_rating,
    который считает по streak/общему xp за всё время)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, u.first_name, u.username, u.avatar_id, u.frame_id,
               SUM(s.gained_xp) as season_xp
        FROM statistics s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE s.stat_date >= date('now', 'start of month') AND u.banned=0
        GROUP BY s.user_id
        ORDER BY season_xp DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "telegram_id": r["user_id"],
            "first_name": r["first_name"],
            "username": r["username"],
            "avatar_id": r["avatar_id"],
            "frame_id": r["frame_id"],
            "season_xp": r["season_xp"] or 0,
        }
        for r in rows
    ]


def get_season_rank(user_id):
    """Место конкретного пользователя в сезонном рейтинге (для профиля —
    "ты #4 в этом сезоне"), даже если он не входит в топ-10."""
    leaderboard = get_season_leaderboard(limit=100000)
    for i, row in enumerate(leaderboard):
        if row["telegram_id"] == user_id:
            return {"rank": i + 1, "season_xp": row["season_xp"], "total": len(leaderboard)}
    return None


def award_season_rewards():
    """Вызывается раз в месяц (1-го числа, до сброса) — раздаёт монеты/
    алмазы топ-3 ПРЕДЫДУЩЕГО сезона. Идемпотентно (UNIQUE(user_id,
    season_key)) — повторный запуск в тот же день ничего не сломает."""
    from datetime import timedelta
    from .users import add_xp, add_diamonds

    last_day_prev_month = date.today().replace(day=1) - timedelta(days=1)
    season_key = last_day_prev_month.strftime("%Y-%m")

    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, SUM(s.gained_xp) as season_xp
        FROM statistics s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE s.stat_date >= ? AND s.stat_date <= ? AND u.banned=0
        GROUP BY s.user_id
        ORDER BY season_xp DESC
        LIMIT 3
    """, (season_key + "-01", str(last_day_prev_month)))
    top3 = cursor.fetchall()
    conn.close()

    awarded = []
    for i, row in enumerate(top3):
        rank = i + 1
        coins, diamonds = SEASON_TOP_REWARDS[rank]
        conn = connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO season_rewards(user_id, season_key, rank, coins, diamonds) VALUES (?,?,?,?,?)",
                (row["user_id"], season_key, rank, coins, diamonds),
            )
            conn.commit()
            already = False
        except Exception:
            already = True
        conn.close()
        if not already:
            add_xp(row["user_id"], coins)
            add_diamonds(row["user_id"], diamonds)
            awarded.append({"user_id": row["user_id"], "rank": rank, "coins": coins, "diamonds": diamonds})
    return awarded
