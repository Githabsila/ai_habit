from datetime import date

from .core import connect


def add_statistics(user_id, completed, xp):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO statistics(user_id, completed, gained_xp, stat_date)
        VALUES (?, ?, ?, ?)
    """, (user_id, completed, xp, str(date.today())))

    conn.commit()
    conn.close()


def get_statistics(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM statistics WHERE user_id=? ORDER BY id DESC",
        (user_id,)
    )
    stats = cursor.fetchall()
    conn.close()
    return stats


def get_weekly_summary(user_id):
    """Агрегат за последние 7 дней — для недельных отчётов и AI-анализа
    прогресса (этап 3 AI Coach)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COALESCE(SUM(completed), 0) as completed,
            COALESCE(SUM(gained_xp), 0) as xp,
            COUNT(DISTINCT stat_date) as active_days
        FROM statistics
        WHERE user_id=? AND stat_date >= date('now', '-6 days')
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    return {
        "completed": row["completed"] if row else 0,
        "xp": row["xp"] if row else 0,
        "active_days": row["active_days"] if row else 0,
    }


def get_daily_statistics(user_id, days=30):
    """Дневной ряд (дата → выполнено/XP за день) за последние `days` дней —
    для графика роста в Профиле (просьба пользователя: графики везде, по
    образцу референсов). add_statistics пишет ОТДЕЛЬНУЮ строку на каждое
    событие (см. выше), поэтому группируем по stat_date, а не берём как есть."""
    days = max(1, min(int(days or 30), 90))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT stat_date,
               COALESCE(SUM(completed), 0) as completed,
               COALESCE(SUM(gained_xp), 0) as xp
        FROM statistics
        WHERE user_id=? AND stat_date >= date('now', ?)
        GROUP BY stat_date
        ORDER BY stat_date ASC
    """, (user_id, f"-{days - 1} days"))
    rows = cursor.fetchall()
    conn.close()
    return [{"date": r["stat_date"], "completed": r["completed"], "xp": r["xp"]} for r in rows]
