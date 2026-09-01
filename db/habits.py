from datetime import date, timedelta, datetime, timezone

from .core import connect
from .users import get_user, add_xp
from .statistics import add_statistics
from .calendar import update_calendar
from .daily_tasks import update_daily_task
from .achievements import check_achievements

# Пром 8: базовая награда за привычку и длительность окна удвоения.
BASE_HABIT_COINS = 10
BONUS_WINDOW_MINUTES = 30

# Пром 10.2: максимум привычек в обычной версии + анти-абузная защита.
MAX_HABITS = 7

# Категории привычек — просто список известных ключей для фронтенда
# (badge/фильтр), сама колонка habits.category — свободный TEXT, так что
# невалидный/пустой ключ просто не попадёт ни в один известный фильтр.
HABIT_CATEGORIES = {
    "health": "🩺 Здоровье",
    "work": "💼 Работа",
    "study": "📚 Учёба",
    "mind": "🧘 Разум",
    "other": "✨ Другое",
}

# За долгую активную серию каждая отметка привычки приносит чуть больше
# монет — "проценты за верность" (см. roadmap). +1 монета за каждые 10 дней
# текущей серии, потолок +5, чтобы не разгонять экономику бесконтрольно.
LOYALTY_BONUS_PER_STREAK_DAYS = 10
LOYALTY_BONUS_CAP = 5


# =====================================
# ПРИВЫЧКИ
# =====================================

def _local_day(user_id):
    from .streak import local_today, day_key
    return day_key(local_today(user_id))


def log_habit_deletion(user_id):
    conn = connect()
    conn.execute(
        "INSERT INTO habit_deletions(user_id, day) VALUES(?,?)",
        (user_id, _local_day(user_id)),
    )
    conn.commit()
    conn.close()


def has_deleted_habit_today(user_id):
    conn = connect()
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM habit_deletions WHERE user_id=? AND day=? LIMIT 1",
        (user_id, _local_day(user_id)),
    )
    row = c.fetchone()
    conn.close()
    return row is not None


def can_add_habit(user_id):
    """Пром 10.2: (ok, reason). reason — 'habit_limit' (уже максимум 7
    привычек) либо 'habit_add_locked' (сегодня уже была отметка + удаление —
    похоже на попытку накрутить Adam Coin, блокируем добавление до 00:00)."""
    from .streak import has_completed_today

    if len(get_habits(user_id)) >= MAX_HABITS:
        return False, "habit_limit"
    if has_completed_today(user_id) and has_deleted_habit_today(user_id):
        return False, "habit_add_locked"
    return True, None


def add_habit(user_id, title, planned_time=None, time_window_minutes=60, category=None, priority=1):
    ok, reason = can_add_habit(user_id)
    if not ok:
        raise ValueError(reason)
    if category is not None and category not in HABIT_CATEGORIES:
        category = None
    priority = 2 if int(priority or 1) >= 2 else 1
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO habits(user_id, title, assigned_at, reminder_sent, planned_time, time_window_minutes, category, priority)
        VALUES (?, ?, CURRENT_TIMESTAMP, 0, ?, ?, ?, ?)
    """, (user_id, title, planned_time, int(time_window_minutes or 60), category, priority))
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


def edit_habit(habit_id, new_title, planned_time=None, time_window_minutes=None, category=None, priority=None):
    if category is not None and category not in HABIT_CATEGORIES:
        category = None
    if priority is not None:
        priority = 2 if int(priority) >= 2 else 1
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE habits SET title=?, planned_time=COALESCE(?, planned_time), "
        "time_window_minutes=COALESCE(?, time_window_minutes), "
        "category=COALESCE(?, category), priority=COALESCE(?, priority), "
        "reminder_sent=0 WHERE id=?",
        (new_title, planned_time, time_window_minutes, category, priority, habit_id),
    )
    conn.commit()
    conn.close()


def skip_habit(habit_id, reason):
    """Отмечает привычку как осознанно пропущенную сегодня (не "выполнено",
    но и не забытая) — не даёт XP/монет, но перестаёт напоминать и не
    учитывается как "пропуск" в еженедельном AI-разборе (см.
    get_weekly_habit_breakdown/log_daily_habits — там skip_reason тоже
    проверяется). reason — короткий текст причины ("Болею" и т.п.),
    выбирается пользователем из готовых вариантов на фронтенде."""
    reason = (reason or "").strip()[:60]
    if not reason:
        return False
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE habits SET skip_reason=? WHERE id=? AND completed=0",
        (reason, habit_id),
    )
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def unskip_habit(habit_id):
    """Отменяет пропуск (например, пользователь передумал и решил всё же
    выполнить привычку сегодня — интерфейс скипа обратимый)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE habits SET skip_reason=NULL WHERE id=?", (habit_id,))
    conn.commit()
    conn.close()


def delete_habit(habit_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM habits WHERE id=?", (habit_id,))
    row = cursor.fetchone()
    cursor.execute("DELETE FROM habits WHERE id=?", (habit_id,))
    conn.commit()
    conn.close()
    if row:
        log_habit_deletion(row["user_id"])


def reset_habits():
    conn = connect()
    cursor = conn.cursor()
    # assigned_at и reminder_sent сбрасываются вместе с completed — новый
    # день значит задача "выдана" заново, и 2-часовой отсчёт для
    # напоминаний (см. get_habits_needing_reminder) начинается с нуля.
    cursor.execute("""
        UPDATE habits SET completed=0, assigned_at=CURRENT_TIMESTAMP, reminder_sent=0, skip_reason=NULL
    """)
    conn.commit()
    conn.close()


def get_incomplete_habits(user_id):
    """Привычки пользователя, ещё не отмеченные выполненными сегодня — не
    считая осознанно пропущенных (skip_reason) — используется для
    контрольной точки в 22:00 (coach.run_hard_deadline_check). Важные
    привычки (priority=2) идут первыми — на них и стоит обращать внимание
    в первую очередь в напоминании."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM habits WHERE user_id=? AND completed=0 AND skip_reason IS NULL "
        "ORDER BY priority DESC, id DESC",
        (user_id,)
    )
    habits = cursor.fetchall()
    conn.close()
    return habits


def get_habits_needing_reminder(user_id, hours=2):
    """Привычки пользователя, которые не выполнены и 'висят' без действия
    уже >= hours часов с момента, когда были выданы (assigned_at — задаётся
    при создании и обновляется каждый день в reset_habits()), и по которым
    сегодня ещё не отправляли индивидуальное напоминание — используется
    coach.run_task_reminder_check для точечных пингов по каждой задаче
    отдельно, в отличие от общей рассылки reminders.py."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM habits
        WHERE user_id=?
          AND completed=0
          AND reminder_sent=0
          AND skip_reason IS NULL
          AND assigned_at <= datetime('now', ?)
    """, (user_id, f"-{hours} hours"))
    habits = cursor.fetchall()
    conn.close()
    return habits


def mark_habit_reminder_sent(habit_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE habits SET reminder_sent=1 WHERE id=?", (habit_id,))
    conn.commit()
    conn.close()


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

    cursor.execute("SELECT id, user_id, title, completed, skip_reason FROM habits")
    habits = cursor.fetchall()

    for h in habits:
        cursor.execute("""
            INSERT INTO habit_logs(user_id, habit_id, habit_title, day, completed, skipped)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (h["user_id"], h["id"], h["title"], yesterday, h["completed"], 1 if h["skip_reason"] else 0))

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
            SUM(CASE WHEN completed=0 AND skipped=0 THEN 1 ELSE 0 END) as missed,
            SUM(CASE WHEN skipped=1 THEN 1 ELSE 0 END) as skipped,
            COUNT(*) as total
        FROM habit_logs
        WHERE user_id=? AND day >= date('now', '-7 days')
        GROUP BY habit_title
        ORDER BY missed DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_monthly_habit_breakdown(user_id):
    """То же самое, что get_weekly_habit_breakdown(), но за последние
    30 дней — источник для ежемесячного AI-разбора (roadmap #24,
    coach.run_monthly_habit_analysis)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            habit_title,
            SUM(CASE WHEN completed=1 THEN 1 ELSE 0 END) as done,
            SUM(CASE WHEN completed=0 AND skipped=0 THEN 1 ELSE 0 END) as missed,
            SUM(CASE WHEN skipped=1 THEN 1 ELSE 0 END) as skipped,
            COUNT(*) as total
        FROM habit_logs
        WHERE user_id=? AND day >= date('now', '-30 days')
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
    # Новая реализация хранит отдельную запись за каждый локальный день.
    # Старое имя функции оставлено для совместимости с остальным проектом.
    from .streak import register_completion
    return register_completion(user_id)


# =====================================
# ВЫПОЛНЕНИЕ ПРИВЫЧКИ
# =====================================

def complete_habit(habit_id):
    from .streak import get_bonus_window, set_bonus_window, local_today, day_key
    from .monthly_streak import record_multi_habit_day, MULTI_HABIT_THRESHOLD

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

    user_id = habit["user_id"]

    cursor.execute("UPDATE habits SET completed=1 WHERE id=?", (habit_id,))

    cursor.execute("""
        UPDATE users SET total_completed = total_completed + 1
        WHERE telegram_id=?
    """, (user_id,))

    cursor.execute("SELECT COUNT(*) AS cnt FROM habits WHERE user_id=?", (user_id,))
    total_habits = cursor.fetchone()["cnt"]

    cursor.execute("SELECT COUNT(*) AS cnt FROM habits WHERE user_id=? AND completed=0", (user_id,))
    remaining_incomplete = cursor.fetchone()["cnt"]

    conn.commit()
    conn.close()

    # Пром 8: удвоение Adam Coin за поочерёдное выполнение привычек.
    # Если на момент этой отметки было открыто окно (открытое предыдущей
    # отметкой) — эта привычка приносит удвоенные монеты. Затем окно
    # заново открывается на 30 минут вперёд, но только если у пользователя
    # больше одной привычки и после этой отметки ещё остались незакрытые —
    # иначе продлевать нечего, и функцию не показываем вовсе.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window_until = get_bonus_window(user_id)
    doubled = bool(window_until and now < window_until)
    coins = BASE_HABIT_COINS * (2 if doubled else 1)

    # Важная привычка (priority=2, звёздочка в интерфейсе) — плоская
    # надбавка сверху, не умножается вместе с x2-окном (иначе экономика
    # разгонялась бы слишком быстро при удачном стечении обоих бонусов).
    priority = habit["priority"] if "priority" in habit.keys() and habit["priority"] else 1
    priority_bonus = 5 if priority == 2 else 0

    # "Проценты за верность" — чем длиннее УЖЕ идущая серия на момент этой
    # отметки, тем чуть весомее каждая привычка. +1 монета за каждые
    # LOYALTY_BONUS_PER_STREAK_DAYS дней серии, потолок LOYALTY_BONUS_CAP.
    user_row = get_user(user_id)
    current_streak = int(user_row["streak"]) if user_row and "streak" in user_row.keys() and user_row["streak"] else 0
    loyalty_bonus = min(current_streak // LOYALTY_BONUS_PER_STREAK_DAYS, LOYALTY_BONUS_CAP)

    coins += priority_bonus + loyalty_bonus

    if total_habits > 1 and remaining_incomplete > 0:
        new_window_until = now + timedelta(minutes=BONUS_WINDOW_MINUTES)
        set_bonus_window(user_id, new_window_until)
    else:
        new_window_until = None
        set_bonus_window(user_id, None)

    # Перед первым действием нового локального дня проверяем вчерашний день,
    # применяем freeze или сбрасываем серию, затем считаем сегодняшний день.
    from .streak import rollover_user
    rollover_user(user_id)
    update_streak(user_id)
    add_xp(user_id, coins)
    add_statistics(user_id, completed=1, xp=coins)
    update_calendar(user_id, total_habits)
    update_daily_task(user_id, "Выполнить привычку")
    update_daily_task(user_id, "Получить 20 Adam Coin", coins)
    check_achievements(user_id)

    # Пром 8 (доп.): 1 балл к месячному счётчику за каждый локальный день,
    # в который закрыто 2+ привычки (см. db/monthly_streak.py). А если
    # сегодня закрыты ВСЕ привычки и их было 2+ — короткое поздравление
    # "идеальный страйк дня" (реализовано отдельно от месячных баллов).
    completed_today = total_habits - remaining_incomplete
    if completed_today >= MULTI_HABIT_THRESHOLD:
        record_multi_habit_day(user_id, day_key(local_today(user_id)))
    # Тост "+1 балл к месяцу" показываем только в момент пересечения порога
    # (иначе он повторялся бы на каждой следующей привычке того же дня —
    # балл-то всё равно только один в день).
    monthly_point_awarded = completed_today == MULTI_HABIT_THRESHOLD
    perfect_day = remaining_incomplete == 0 and completed_today >= MULTI_HABIT_THRESHOLD

    return {
        "coins": coins,
        "doubled": doubled,
        "priority_bonus": priority_bonus,
        "loyalty_bonus": loyalty_bonus,
        "total_habits": total_habits,
        "remaining_incomplete": remaining_incomplete,
        "completed_today": completed_today,
        "bonus_active": bool(new_window_until),
        "bonus_until": new_window_until.isoformat() if new_window_until else None,
        "monthly_point_awarded": monthly_point_awarded,
        "perfect_day": perfect_day,
    }


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
