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


# =====================================
# ГРАНУЛЯРНЫЕ НАПОМИНАНИЯ
# =====================================
# Раньше был только один общий тумблер `reminders` — "всё или ничего".
# Эти три категории позволяют, например, отключить именно пуши про
# ударный режим (23:00/23:30), оставив утренние и вечерние напоминания
# по привычкам. Общий `reminders=0` по-прежнему выключает всё разом —
# см. reminder_category_enabled() ниже, она проверяет оба уровня сразу.

REMINDER_CATEGORIES = ("habits", "streak", "digests")

REMINDER_CATEGORY_LABELS = {
    "habits": "Привычки и план дня",
    "streak": "Ударный режим",
    "digests": "Сводки и отчёты",
}


def toggle_reminder_category(user_id, category):
    """Переключает один из гранулярных тумблеров (reminders_habits /
    reminders_streak / reminders_digests) и возвращает новое значение
    (bool). category — строго одно из REMINDER_CATEGORIES: имя колонки
    собирается из уже провалидированного значения, а не из произвольного
    пользовательского ввода, поэтому f-string в SQL здесь безопасен."""
    if category not in REMINDER_CATEGORIES:
        raise ValueError(f"unknown reminder category: {category}")
    column = f"reminders_{category}"

    conn = connect()
    cursor = conn.cursor()

    current = get_settings(user_id)
    current_value = current[column] if current is not None else 1
    new_value = 0 if current_value else 1

    cursor.execute(
        f"UPDATE settings SET {column}=? WHERE user_id=?",
        (new_value, user_id)
    )
    conn.commit()
    conn.close()
    return bool(new_value)


def reminder_category_enabled(settings_row, category):
    """True, только если И общий тумблер reminders, И конкретная категория
    включены. Единая точка проверки для всех job'ов-напоминаний
    (coach.py/streak_scheduler.py/morning_ping.py) — вместо того чтобы в
    каждом job'е руками дублировать проверку обоих уровней."""
    if not settings_row or not settings_row["reminders"]:
        return False
    column = f"reminders_{category}"
    try:
        value = settings_row[column]
    except (IndexError, KeyError):
        # БД ещё не мигрирована (старая строка без новых колонок) —
        # по умолчанию категория включена, как и было до этой фичи.
        return True
    return bool(value) if value is not None else True


# =====================================
# ТИХИЕ ЧАСЫ (roadmap #35)
# =====================================
# Окно локальных часов, в которое не приходят повседневные напоминания
# (привычки, ударный режим) — недельные/месячные сводки и так приходят
# раз в неделю/месяц в один и тот же час, "тихие часы" для них не так
# осмысленны, поэтому проверяются только в job'ах категорий habits/streak.

def set_quiet_hours(user_id, start_hour, end_hour):
    """start_hour/end_hour — 0..23, окно [start, end) по локальному часу
    пользователя; поддерживает ночное окно через полночь (start > end,
    например 23 -> 7). Возвращает False, если часы вне диапазона или равны
    (пустое окно — бессмысленно и, скорее всего, ошибка ввода)."""
    try:
        start_hour = int(start_hour)
        end_hour = int(end_hour)
    except (TypeError, ValueError):
        return False
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23) or start_hour == end_hour:
        return False
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE settings SET quiet_hours_start=?, quiet_hours_end=? WHERE user_id=?",
        (start_hour, end_hour, user_id),
    )
    conn.commit()
    conn.close()
    return True


def clear_quiet_hours(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE settings SET quiet_hours_start=NULL, quiet_hours_end=NULL WHERE user_id=?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def in_quiet_hours(settings_row, now_local):
    """True, если now_local (datetime в локальном времени пользователя)
    попадает в настроенное окно тихих часов. Оба поля NULL (не
    настроено — значение по умолчанию) — всегда False."""
    if not settings_row:
        return False
    try:
        start = settings_row["quiet_hours_start"]
        end = settings_row["quiet_hours_end"]
    except (IndexError, KeyError):
        return False
    if start is None or end is None:
        return False
    hour = now_local.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


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
