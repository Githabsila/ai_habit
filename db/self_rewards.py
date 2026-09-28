"""«Вознаградите себя» — простая отметка, что пользователь сделал себе
что-то приятное, за фиксированную цену в Adam Coin (xp). Не привязана к
shop_items: это не покупка вещи, а лог с необязательной заметкой (чем себя
вознаградил), который пользователь открывает через неделю-другую, чтобы
увидеть, когда, чем и сколько раз себя порадовал.
"""
from .core import connect

DEFAULT_COST = 20
MAX_NOTE_LEN = 200


def log_self_reward(user_id, note=None, cost=DEFAULT_COST):
    """Атомарно списывает `cost` Adam Coin и логирует вознаграждение.

    Проверка баланса встроена в сам UPDATE (тот же приём, что и
    db/shop.py::buy_shop_item) — исключает гонку, при которой баланс мог бы
    уйти в минус при двух параллельных запросах. Возвращает новый баланс xp
    при успехе, иначе None (не хватило Adam Coin).
    """
    cost = max(1, int(cost))
    note = (str(note).strip()[:MAX_NOTE_LEN] or None) if note else None

    conn = connect()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET xp = xp - ? WHERE telegram_id=? AND xp >= ?",
            (cost, user_id, cost),
        )
        if cursor.rowcount == 0:
            return None

        cursor.execute(
            "INSERT INTO self_rewards(user_id, note, cost) VALUES (?, ?, ?)",
            (user_id, note, cost),
        )
        conn.commit()

        cursor.execute("SELECT xp FROM users WHERE telegram_id=?", (user_id,))
        row = cursor.fetchone()
        return int(row["xp"]) if row else 0
    finally:
        conn.close()


def get_self_reward_history(user_id, limit=50):
    limit = max(1, min(int(limit or 50), 200))
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, note, cost, created_at FROM self_rewards "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_self_reward_stats(user_id, days=7):
    """Сводка для истории: сколько раз и на сколько Adam Coin пользователь
    вознаградил себя за последние `days` дней."""
    days = max(1, min(int(days or 7), 90))
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS total "
            "FROM self_rewards WHERE user_id=? AND created_at >= datetime('now', ?)",
            (user_id, f"-{days} days"),
        ).fetchone()
        return {
            "days": days,
            "count": int(row["n"] or 0),
            "total_cost": int(row["total"] or 0),
        }
    finally:
        conn.close()
