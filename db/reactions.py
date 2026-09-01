"""
Roadmap #19 — реакции/стикеры поддержки другу: лёгкий социальный сигнал
поверх уже существующего рейтинга (db/users.py::get_rating) — не полноценный
чат, просто "🔥 Иван поддержал тебя". Один эмодзи от одного пользователя
другому в день (UNIQUE(from_user_id,to_user_id,day) в friend_reactions,
см. db/core.py) — защита от спама одной и той же реакцией по кругу.
"""
from .core import connect

REACTION_EMOJIS = ["🔥", "💪", "👏", "❤️", "🎉", "⭐"]


def send_reaction(from_user_id, to_user_id, emoji):
    if emoji not in REACTION_EMOJIS:
        return False
    if from_user_id == to_user_id:
        return False
    from .streak import local_today
    day = str(local_today(from_user_id))
    conn = connect()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO friend_reactions(from_user_id, to_user_id, emoji, day) VALUES (?,?,?,?)",
            (from_user_id, to_user_id, emoji, day),
        )
        conn.commit()
        sent = True
    except Exception:
        # UNIQUE(from_user_id,to_user_id,day) — уже отправляли реакцию этому
        # человеку сегодня, не даём заспамить.
        sent = False
    conn.close()
    return sent


def get_recent_reactions_received(user_id, limit=20):
    """Последние реакции, полученные пользователем — с именем отправителя,
    для ленты в профиле."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.emoji, r.day, r.created_at, u.first_name, u.username
        FROM friend_reactions r
        LEFT JOIN users u ON u.telegram_id = r.from_user_id
        WHERE r.to_user_id=?
        ORDER BY r.id DESC
        LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows


def has_reacted_today(from_user_id, to_user_id):
    from .streak import local_today
    day = str(local_today(from_user_id))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM friend_reactions WHERE from_user_id=? AND to_user_id=? AND day=? LIMIT 1",
        (from_user_id, to_user_id, day),
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None
