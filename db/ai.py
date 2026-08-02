from .core import connect


def add_ai_message(user_id, role, message):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ai_messages(user_id, role, message) VALUES (?, ?, ?)
    """, (user_id, role, message))
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return message_id


def get_ai_history(user_id, limit=20):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM ai_messages WHERE user_id=? ORDER BY id DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


def clear_ai_history(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ai_messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def save_ai_feedback(message_id, user_id, rating):
    """rating: 'up' или 'down'. Повторное нажатие меняет оценку, а не дублирует её."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ai_feedback(message_id, user_id, rating)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id, user_id) DO UPDATE SET
            rating = excluded.rating,
            created_at = CURRENT_TIMESTAMP
    """, (message_id, user_id, rating))
    conn.commit()
    conn.close()
