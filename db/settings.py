from .core import connect


def get_settings(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM settings WHERE user_id=?", (user_id,))
    settings = cursor.fetchone()
    conn.close()
    return settings


def update_reminder_time(user_id, hour, minute):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE settings SET reminder_hour=?, reminder_minute=?
        WHERE user_id=?
    """, (hour, minute, user_id))
    conn.commit()
    conn.close()


def toggle_reminders(user_id):
    """Переключает напоминания вкл/выкл и возвращает новое значение (bool).
    Логика 1:1 с тем, что раньше делал handlers/settings.py::toggle прямым
    SQL — вынесено сюда, чтобы им могли пользоваться и бот, и Mini App."""
    conn = connect()
    cursor = conn.cursor()

    current = get_settings(user_id)
    new_value = 0 if (current and current["reminders"]) else 1

    cursor.execute(
        "UPDATE settings SET reminders=? WHERE user_id=?",
        (new_value, user_id)
    )
    conn.commit()
    conn.close()
    return bool(new_value)


def update_ai_style(user_id, style):
    """style: 'soft' / 'neutral' / 'strict' — стиль общения AI-наставника."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE settings SET ai_style=?
        WHERE user_id=?
    """, (style, user_id))
    conn.commit()
    conn.close()


def get_ai_style(user_id):
    settings = get_settings(user_id)
    if settings is None:
        return "neutral"
    try:
        style = settings["ai_style"]
    except (IndexError, KeyError):
        return "neutral"
    return style or "neutral"


VALID_THEMES = ("violet", "blue", "green", "pink")


def get_theme(user_id):
    settings = get_settings(user_id)
    if settings is None:
        return "violet"
    try:
        theme = settings["theme"]
    except (IndexError, KeyError):
        return "violet"
    return theme or "violet"


def update_theme(user_id, theme):
    """theme: одно из VALID_THEMES — акцентный цвет мини-приложения.
    Применяется только если товар «🎨 Тема оформления» куплен (проверка
    владения — на стороне вызывающего кода в webapp/server.py)."""
    if theme not in VALID_THEMES:
        return False
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE settings SET theme=?
        WHERE user_id=?
    """, (theme, user_id))
    conn.commit()
    conn.close()
    return True
