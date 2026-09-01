from datetime import datetime
from zoneinfo import ZoneInfo

from .core import connect
from .users import get_user

# Эмодзи-иконка коллекционного бейджа по названию — отдельной колонки в
# БД под это нет (см. схему в db/core.py), проще держать одним словарём
# здесь и на фронтенде (webapp_server.py прокидывает его в bootstrap),
# чем гонять миграцию ради одной картинки.
ACHIEVEMENT_ICONS = {
    "Первый шаг": "🥾",
    "Целеустремлённый": "🎯",
    "Мастер привычек": "🏆",
    "Серия 3 дня": "🔥",
    "Серия 7 дней": "🔥",
    "Железная воля": "💪",
    "Легенда": "👑",
    "Марафонец": "🏅",
    "Опытный": "💰",
    "Полуночник": "🌙",
    "Жаворонок": "🌅",
}


def _has_achievement(cursor, user_id, title):
    cursor.execute(
        "SELECT id FROM achievements WHERE user_id=? AND title=?",
        (user_id, title)
    )
    return cursor.fetchone() is not None


def check_achievements(user_id):
    """Раздел "Коллекционные бейджи" (roadmap #10): помимо очевидных
    рубежей по количеству/серии, есть несколько "необычных" — за отметку
    привычки в конкретное время суток. check_achievements() и раньше
    вызывалась сразу после complete_habit(), так что "сейчас" — это и
    есть момент отметки."""
    user = get_user(user_id)
    if not user:
        return

    try:
        from .streak import get_timezone
        local_now = datetime.now(ZoneInfo(get_timezone(user_id)))
    except Exception:
        local_now = None

    midnight_habit = local_now is not None and local_now.hour == 0 and local_now.minute < 5
    early_bird = local_now is not None and 5 <= local_now.hour < 6

    conn = connect()
    cursor = conn.cursor()

    checks = [
        ("Первый шаг", "Выполните первую привычку", user["total_completed"] >= 1),
        ("Целеустремлённый", "Выполните 10 привычек", user["total_completed"] >= 10),
        ("Мастер привычек", "Выполните 50 привычек", user["total_completed"] >= 50),
        ("Марафонец", "Выполните 200 привычек", user["total_completed"] >= 200),
        ("Серия 3 дня", "Серия выполнения 3 дня подряд", user["streak"] >= 3),
        ("Серия 7 дней", "Серия выполнения 7 дней подряд", user["streak"] >= 7),
        ("Железная воля", "Серия выполнения 30 дней подряд", user["streak"] >= 30),
        ("Легенда", "Серия выполнения 100 дней подряд", user["streak"] >= 100),
        ("Опытный", "Наберите 100 Adam Coin", user["xp"] >= 100),
        ("Полуночник", "Отметьте привычку ровно в полночь", midnight_habit),
        ("Жаворонок", "Отметьте привычку рано утром (5:00-6:00)", early_bird),
    ]

    newly_unlocked = []
    for title, description, condition in checks:
        if condition and not _has_achievement(cursor, user_id, title):
            cursor.execute("""
                INSERT INTO achievements(user_id, title, description)
                VALUES (?, ?, ?)
            """, (user_id, title, description))
            newly_unlocked.append(title)

    conn.commit()
    conn.close()

    # Roadmap #18 — новое достижение попадает в ленту активности друзей.
    if newly_unlocked:
        from .activity_feed import log_activity_event
        for title in newly_unlocked:
            log_activity_event(user_id, "achievement", {"detail": title})


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
