from datetime import date

from .core import connect


def update_calendar(user_id, total_habits):
    conn = connect()
    cursor = conn.cursor()

    today = str(date.today())

    cursor.execute(
        "SELECT id FROM calendar WHERE user_id=? AND day=?",
        (user_id, today)
    )
    row = cursor.fetchone()

    if row:
        cursor.execute(
            "UPDATE calendar SET completed = completed + 1, total = ? WHERE id=?",
            (total_habits, row["id"])
        )
    else:
        cursor.execute(
            "INSERT INTO calendar(user_id, day, completed, total) VALUES (?, ?, 1, ?)",
            (user_id, today, total_habits)
        )

    conn.commit()
    conn.close()


def get_progress_comparison(user_id):
    """Roadmap #29 — "я сейчас vs я месяц назад": процент выполнения
    привычек за последние 7 дней против такого же 7-дневного окна
    4-5 недель назад. None вместо процента — недостаточно данных за то
    окно (пользователь тогда ещё не пользовался ботом), а не "0%"."""
    conn = connect()
    cursor = conn.cursor()

    def _rate(days_ago_start, days_ago_end):
        cursor.execute("""
            SELECT COALESCE(SUM(completed),0) AS done, COALESCE(SUM(total),0) AS total
            FROM calendar
            WHERE user_id=? AND day >= date('now', ?) AND day < date('now', ?)
        """, (user_id, f"-{days_ago_start} days", f"-{days_ago_end} days"))
        row = cursor.fetchone()
        total = row["total"] or 0
        return round((row["done"] / total) * 100) if total else None

    current_rate = _rate(7, 0)
    previous_rate = _rate(35, 28)
    conn.close()

    if current_rate is None or previous_rate is None:
        return {"current_rate": current_rate, "previous_rate": previous_rate, "trend": "not_enough_data", "delta": None}

    delta = current_rate - previous_rate
    trend = "up" if delta > 0 else ("down" if delta < 0 else "same")
    return {"current_rate": current_rate, "previous_rate": previous_rate, "trend": trend, "delta": delta}


def get_calendar(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM calendar WHERE user_id=? ORDER BY day DESC",
        (user_id,)
    )
    data = cursor.fetchall()
    conn.close()
    return data
