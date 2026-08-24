from .core import connect
from .users import get_user


def _has_achievement(cursor, user_id, title):
    cursor.execute(
        "SELECT id FROM achievements WHERE user_id=? AND title=?",
        (user_id, title)
    )
    return cursor.fetchone() is not None


def check_achievements(user_id):
    user = get_user(user_id)
    if not user:
        return

    conn = connect()
    cursor = conn.cursor()

    checks = [
        ("Первый шаг", "Выполните первую привычку", user["total_completed"] >= 1),
        ("Целеустремлённый", "Выполните 10 привычек", user["total_completed"] >= 10),
        ("Мастер привычек", "Выполните 50 привычек", user["total_completed"] >= 50),
        ("Серия 3 дня", "Серия выполнения 3 дня подряд", user["streak"] >= 3),
        ("Серия 7 дней", "Серия выполнения 7 дней подряд", user["streak"] >= 7),
        ("Опытный", "Наберите 100 Adam Coin", user["xp"] >= 100),
    ]

    for title, description, condition in checks:
        if condition and not _has_achievement(cursor, user_id, title):
            cursor.execute("""
                INSERT INTO achievements(user_id, title, description)
                VALUES (?, ?, ?)
            """, (user_id, title, description))

    conn.commit()
    conn.close()


def get_achievements(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM achievements WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    )
    data = cursor.fetchall()
    conn.close()
    return data
