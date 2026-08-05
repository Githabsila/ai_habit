from datetime import date, timedelta

from .core import connect
from .users import get_user, add_xp
from .statistics import add_statistics
from .calendar import update_calendar
from .daily_tasks import update_daily_task
from .achievements import check_achievements


# =====================================
# ПРИВЫЧКИ
# =====================================

def add_habit(user_id, title):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO habits(user_id, title) VALUES (?, ?)", (user_id, title))
    conn.commit()
    conn.close()


def get_habits(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM habits WHERE user_id=? ORDER BY id DESC", (user_id,))
    habits = cursor.fetchall()
    conn.close()
    return habits


def get_habit(habit_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM habits WHERE id=?", (habit_id,))
    habit = cursor.fetchone()
    conn.close()
    return habit


def edit_habit(habit_id, new_title):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE habits SET title=? WHERE id=?", (new_title, habit_id))
    conn.commit()
    conn.close()


def delete_habit(habit_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM habits WHERE id=?", (habit_id,))
    conn.commit()
    conn.close()


def reset_habits():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE habits SET completed=0")
    conn.commit()
    conn.close()


def get_incomplete_habits(user_id):
    """Привычки пользователя, ещё не отмеченные выполненными сегодня —
    используется для жёсткого дедлайна в 21:00 (coach.run_hard_deadline_check)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM habits WHERE user_id=? AND completed=0",
        (user_id,)
    )
    habits = cursor.fetchall()
    conn.close()
    return habits


# =====================================
# ПОСУТОЧНЫЙ ЖУРНАЛ ПО ПРИВЫЧКАМ (для AI-анализа недели)
# =====================================

def log_daily_habits():
    """Снимок состояния КАЖДОЙ привычки за уходящий день — вызывается в
    scheduler.new_day() ДО reset_habits(), пока completed ещё не сброшен.
    В отличие от calendar (только агрегат по дню), тут видно конкретно
    какая привычка была выполнена/пропущена — на этом строится еженедельный
    персонализированный AI-разбор по привычкам."""
    conn = connect()
    cursor = conn.cursor()

    yesterday = str(date.today() - timedelta(days=1))

    cursor.execute("SELECT id, user_id, title, completed FROM habits")
    habits = cursor.fetchall()

    for h in habits:
        cursor.execute("""
            INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed)
            VALUES (?, ?, ?, ?, ?)
        """, (h["user_id"], h["id"], h["title"], yesterday, h["completed"]))

    conn.commit()
    conn.close()


def get_weekly_habit_breakdown(user_id):
    """За последние 7 дней — по каждой привычке (по названию), сколько раз
    выполнена и сколько пропущена. Основа для еженедельного AI-анализа
    (coach.run_weekly_habit_analysis): «Ты пропустил 3 дня медитации...»."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            habit_title,
            SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN completed=0 THEN 1 ELSE 0 END) as missed,
            COUNT(*) as total
        FROM habit_logs
        WHERE user_id=? AND day >= date('now', '-7 days')
        GROUP BY habit_title
        ORDER BY missed DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


# =====================================
# СЕРИЯ (STREAK)
# =====================================

def update_streak(user_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT streak, last_completed FROM users WHERE telegram_id=?",
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    today = str(date.today())
    yesterday = str(date.today() - timedelta(days=1))
    last = row["last_completed"]
    streak = row["streak"]

    if last == today:
        # уже засчитано сегодня — ничего не делаем
        conn.close()
        return
    elif last == yesterday:
        streak += 1
    else:
        streak = 1

    cursor.execute("""
        UPDATE users SET streak=?, last_completed=? WHERE telegram_id=?
    """, (streak, today, user_id))

    conn.commit()
    conn.close()


# =====================================
# ВЫПОЛНЕНИЕ ПРИВЫЧКИ
# =====================================

def complete_habit(habit_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM habits WHERE id=?", (habit_id,))
    habit = cursor.fetchone()

    if habit is None:
        conn.close()
        return False

    if habit["completed"] == 1:
        conn.close()
        return False

    cursor.execute("UPDATE habits SET completed=1 WHERE id=?", (habit_id,))

    cursor.execute("""
        UPDATE users SET total_completed = total_completed + 1
        WHERE telegram_id=?
    """, (habit["user_id"],))

    conn.commit()
    conn.close()

    update_streak(habit["user_id"])
    add_xp(habit["user_id"], 10)
    add_statistics(habit["user_id"], completed=1, xp=10)
    update_calendar(habit["user_id"])
    update_daily_task(habit["user_id"], "Выполнить привычку")
    update_daily_task(habit["user_id"], "Получить 20 Adam Coin", 10)
    check_achievements(habit["user_id"])

    return True


# =====================================
# ПРОГРЕСС (shop.py / progress.py)
# =====================================

def get_progress(user_id):
    user = get_user(user_id)

    if not user:
        return None

    habits = get_habits(user_id)

    completed = len([
        h for h in habits
        if h["completed"]
    ])

    return {
        "xp": user["xp"],
        "level": user["level"],
        "streak": user["streak"],
        "completed": completed,
        "total": len(habits)
    }
