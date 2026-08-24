from .core import connect
from .users import get_user


ACHIEVEMENT_COPY = {
    "Первый шаг": ("Первый шаг", "Первая привычка выполнена. Начало положено."),
    "Целеустремлённый": ("Держишь курс", "10 привычек выполнены. Ты уже создаёшь систему."),
    "Мастер привычек": ("Мастер привычек", "50 привычек выполнены. Дисциплина становится твоей силой."),
    "Серия 3 дня": ("Три дня в ударе", "3 дня подряд без сбоя. Ритм набран."),
    "Серия 7 дней": ("Неделя в ударе", "7 дней подряд. Ты закрепил сильный ритм."),
    "Опытный": ("Первые 100", "100 Adam Coin заработаны. Твой прогресс уже заметен."),
}


def _normalize_achievement(row):
    item = dict(row)
    title, description = ACHIEVEMENT_COPY.get(item["title"], (item["title"], item["description"]))
    item["title"] = title
    item["description"] = description
    return item


def _has_achievement(cursor, user_id, title):
    aliases = [title]
    for legacy, (new_title, _description) in ACHIEVEMENT_COPY.items():
        if new_title == title:
            aliases.append(legacy)
    placeholders = ",".join("?" for _ in aliases)
    cursor.execute(
        f"SELECT id FROM achievements WHERE user_id=? AND title IN ({placeholders})",
        (user_id, *aliases)
    )
    return cursor.fetchone() is not None


def check_achievements(user_id):
    user = get_user(user_id)
    if not user:
        return

    conn = connect()
    cursor = conn.cursor()

    checks = [
        ("Первый шаг", "Первая привычка выполнена. Начало положено.", user["total_completed"] >= 1),
        ("Держишь курс", "10 привычек выполнены. Ты уже создаёшь систему.", user["total_completed"] >= 10),
        ("Мастер привычек", "50 привычек выполнены. Дисциплина становится твоей силой.", user["total_completed"] >= 50),
        ("Три дня в ударе", "3 дня подряд без сбоя. Ритм набран.", user["streak"] >= 3),
        ("Неделя в ударе", "7 дней подряд. Ты закрепил сильный ритм.", user["streak"] >= 7),
        ("Первые 100", "100 Adam Coin заработаны. Твой прогресс уже заметен.", user["xp"] >= 100),
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
    return [_normalize_achievement(row) for row in data]
