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

# Roadmap #1 — привычки-счётчики ("выпить 4 стакана воды"): предел, чтобы
# не заводили счётчик на тысячи повторений.
MAX_TARGET_COUNT = 20

# Roadmap #2 — гибкая периодичность: сколько раз в неделю максимум можно
# требовать (7 = по сути "каждый день", тогда проще оставить NULL).
MAX_FREQUENCY_PER_WEEK = 6

# Roadmap #3 — заметка/фото к выполненной привычке.
MAX_NOTE_LENGTH = 300
# ~180 КБ на строку data:URL с запасом хватает под мелкое превью (клиент
# сжимает фото в canvas перед отправкой, см. app.js::compressImageToDataUrl).
MAX_PHOTO_DATA_URL_LENGTH = 180_000


# Сентинел "аргумент не передан" — отличаем от валидного None у
# frequency_per_week (см. edit_habit).
_UNSET = object()


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


def _clamp_target_count(target_count):
    if target_count is None:
        return 1
    try:
        target_count = int(target_count)
    except (TypeError, ValueError):
        return 1
    return max(1, min(target_count, MAX_TARGET_COUNT))


def _clamp_frequency_per_week(frequency_per_week):
    if frequency_per_week is None:
        return None
    try:
        frequency_per_week = int(frequency_per_week)
    except (TypeError, ValueError):
        return None
    if frequency_per_week <= 0:
        return None
    return min(frequency_per_week, MAX_FREQUENCY_PER_WEEK)


def add_habit(user_id, title, planned_time=None, time_window_minutes=60, category=None,
               priority=1, target_count=1, frequency_per_week=None, chain_trigger_habit_id=None):
    ok, reason = can_add_habit(user_id)
    if not ok:
        raise ValueError(reason)
    if category is not None and category not in HABIT_CATEGORIES:
        category = None
    priority = 2 if int(priority or 1) >= 2 else 1
    target_count = _clamp_target_count(target_count)
    frequency_per_week = _clamp_frequency_per_week(frequency_per_week)
    # Привычка-триггер должна принадлежать тому же пользователю — иначе
    # игнорируем (защита от чужого id, которого фронт вообще не должен
    # присылать, но лучше перестраховаться на уровне БД).
    if chain_trigger_habit_id is not None:
        trigger = get_habit(chain_trigger_habit_id)
        if trigger is None or trigger["user_id"] != user_id:
            chain_trigger_habit_id = None
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO habits(user_id, title, assigned_at, reminder_sent, planned_time, time_window_minutes,
                            category, priority, target_count, frequency_per_week, chain_trigger_habit_id)
        VALUES (?, ?, CURRENT_TIMESTAMP, 0, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, title, planned_time, int(time_window_minutes or 60), category, priority,
          target_count, frequency_per_week, chain_trigger_habit_id))
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


def edit_habit(habit_id, new_title, planned_time=None, time_window_minutes=None, category=None,
                priority=None, target_count=None, frequency_per_week=_UNSET):
    if category is not None and category not in HABIT_CATEGORIES:
        category = None
    if priority is not None:
        priority = 2 if int(priority) >= 2 else 1
    if target_count is not None:
        target_count = _clamp_target_count(target_count)
    # frequency_per_week допускает явный сброс на "каждый день" (None),
    # поэтому отличаем "не передали" (_UNSET, оставить как было) от
    # "передали None" (снять периодичность) через отдельный сентинел.
    if frequency_per_week is _UNSET:
        freq_clause, freq_val = "frequency_per_week", None
        use_coalesce = True
    else:
        freq_clause, freq_val = "?", _clamp_frequency_per_week(frequency_per_week)
        use_coalesce = False
    conn = connect()
    cursor = conn.cursor()
    if use_coalesce:
        cursor.execute(
            "UPDATE habits SET title=?, planned_time=COALESCE(?, planned_time), "
            "time_window_minutes=COALESCE(?, time_window_minutes), "
            "category=COALESCE(?, category), priority=COALESCE(?, priority), "
            "target_count=COALESCE(?, target_count), "
            "reminder_sent=0 WHERE id=?",
            (new_title, planned_time, time_window_minutes, category, priority, target_count, habit_id),
        )
    else:
        cursor.execute(
            "UPDATE habits SET title=?, planned_time=COALESCE(?, planned_time), "
            "time_window_minutes=COALESCE(?, time_window_minutes), "
            "category=COALESCE(?, category), priority=COALESCE(?, priority), "
            "target_count=COALESCE(?, target_count), frequency_per_week=?, "
            "reminder_sent=0 WHERE id=?",
            (new_title, planned_time, time_window_minutes, category, priority, target_count, freq_val, habit_id),
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
        UPDATE habits SET completed=0, assigned_at=CURRENT_TIMESTAMP, reminder_sent=0,
                           skip_reason=NULL, progress_count=0
    """)
    conn.commit()
    conn.close()


# Условие "неделя уже закрыта" для гибкой периодичности (roadmap #2) — общее
# для get_incomplete_habits и get_habits_needing_reminder: если у привычки
# задан frequency_per_week, а выполнений с начала недели (пн) уже хватает,
# она не считается "невыполненной" сегодня, даже если completed=0.
_WEEKLY_QUOTA_MET_SQL = """
    (h.frequency_per_week IS NULL OR (
        COALESCE((
            SELECT COUNT(*) FROM habit_logs hl
            WHERE hl.habit_id = h.id AND hl.completed = 1 AND hl.day >= ?
        ), 0) < h.frequency_per_week
    ))
"""


def _monday_of(local_today):
    return str(local_today - timedelta(days=local_today.weekday()))


def get_incomplete_habits(user_id):
    """Привычки пользователя, ещё не отмеченные выполненными сегодня — не
    считая осознанно пропущенных (skip_reason) и привычек с гибкой
    периодичностью, недельная норма которых уже выполнена — используется
    для контрольной точки в 22:00 (coach.run_hard_deadline_check). Важные
    привычки (priority=2) идут первыми — на них и стоит обращать внимание
    в первую очередь в напоминании."""
    from .streak import local_today
    monday = _monday_of(local_today(user_id))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT h.* FROM habits h WHERE h.user_id=? AND h.completed=0 AND h.skip_reason IS NULL "
        f"AND {_WEEKLY_QUOTA_MET_SQL} "
        f"ORDER BY h.priority DESC, h.id DESC",
        (user_id, monday)
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
    отдельно, в отличие от общей рассылки reminders.py. Привычки с гибкой
    периодичностью (roadmap #2), недельная норма которых уже выполнена, не
    напоминаются."""
    from .streak import local_today
    monday = _monday_of(local_today(user_id))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT h.* FROM habits h
        WHERE h.user_id=?
          AND h.completed=0
          AND h.reminder_sent=0
          AND h.skip_reason IS NULL
          AND h.assigned_at <= datetime('now', ?)
          AND {_WEEKLY_QUOTA_MET_SQL}
    """, (user_id, f"-{hours} hours", monday))
    habits = cursor.fetchall()
    conn.close()
    return habits


def get_weekly_progress(habit_id, user_id):
    """Сколько раз привычка с гибкой периодичностью (roadmap #2) уже
    выполнена с начала текущей недели (пн, по локальному времени
    пользователя), включая сегодня — используется и для отображения
    "2/3 на этой неделе" на фронте, и внутри _WEEKLY_QUOTA_MET_SQL."""
    from .streak import local_today
    today = local_today(user_id)
    monday = _monday_of(today)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM habit_logs WHERE habit_id=? AND completed=1 AND day>=? AND day<?",
        (habit_id, monday, str(today)),
    )
    done_before_today = cursor.fetchone()["cnt"]
    cursor.execute("SELECT completed FROM habits WHERE id=?", (habit_id,))
    row = cursor.fetchone()
    conn.close()
    today_done = 1 if (row and row["completed"]) else 0
    return done_before_today + today_done


def increment_habit_progress(habit_id, amount=1):
    """Roadmap #1 — прибавляет прогресс к привычке-счётчику ("выпить 4
    стакана воды"). Как только progress_count достигает target_count,
    сама вызывает complete_habit() (со всеми монетами/streak/ачивками) —
    для обычных привычек (target_count=1) поведение не меняется, просто
    сразу достигают цели с первого нажатия."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM habits WHERE id=?", (habit_id,))
    habit = cursor.fetchone()
    if habit is None or habit["completed"] == 1:
        conn.close()
        return None
    target = habit["target_count"] if ("target_count" in habit.keys() and habit["target_count"]) else 1
    current = habit["progress_count"] if ("progress_count" in habit.keys() and habit["progress_count"]) else 0
    new_progress = min(current + max(1, int(amount or 1)), target)
    cursor.execute("UPDATE habits SET progress_count=? WHERE id=?", (new_progress, habit_id))
    conn.commit()
    conn.close()
    if new_progress >= target:
        result = complete_habit(habit_id)
        if result:
            result["progress_count"] = new_progress
            result["target_count"] = target
            result["just_completed"] = True
        return result
    return {"just_completed": False, "progress_count": new_progress, "target_count": target}


# =====================================
# ЗАМЕТКА/ФОТО К ВЫПОЛНЕННОЙ ПРИВЫЧКЕ (roadmap #3)
# =====================================

def add_habit_note(user_id, habit_id, note=None, photo_data_url=None):
    """Заметка и/или мини-фото к сегодняшней отметке привычки — 'дневник
    прогресса'. Одна запись на привычку в день (UNIQUE(habit_id, day)),
    повторное сохранение в тот же день просто перезаписывает. Фото —
    сжатая на клиенте data:image/... строка (без внешнего файлового
    хранилища), с потолком по размеру, чтобы не раздувать БД."""
    note = (note or "").strip()[:MAX_NOTE_LENGTH] or None
    photo_data_url = (photo_data_url or "").strip() or None
    if photo_data_url and (
        not photo_data_url.startswith("data:image/") or len(photo_data_url) > MAX_PHOTO_DATA_URL_LENGTH
    ):
        photo_data_url = None
    if not note and not photo_data_url:
        return False
    day = _local_day(user_id)
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO habit_notes(user_id, habit_id, day, note, photo_data_url) VALUES (?,?,?,?,?) "
        "ON CONFLICT(habit_id, day) DO UPDATE SET note=excluded.note, photo_data_url=excluded.photo_data_url",
        (user_id, habit_id, day, note, photo_data_url),
    )
    conn.commit()
    conn.close()
    return True


def get_habit_note(habit_id, day):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM habit_notes WHERE habit_id=? AND day=?", (habit_id, day))
    row = cursor.fetchone()
    conn.close()
    return row


def get_recent_habit_notes(user_id, limit=30):
    """'Дневник' — последние заметки/фото по всем привычкам пользователя,
    новые сверху."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM habit_notes WHERE user_id=? AND (note IS NOT NULL OR photo_data_url IS NOT NULL) "
        "ORDER BY day DESC, id DESC LIMIT ?",
        (user_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


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

    # Если привычку отметили напрямую (не через increment_habit_progress),
    # а у неё был счётчик (target_count>1) — дотягиваем progress_count до
    # цели, чтобы на фронте не осталась "2/4" у уже выполненной привычки.
    cursor.execute(
        "UPDATE habits SET completed=1, "
        "progress_count=CASE WHEN target_count>1 THEN target_count ELSE progress_count END "
        "WHERE id=?",
        (habit_id,),
    )

    # Roadmap #23/#36 — журнал МОМЕНТОВ выполнения (час/минута), на котором
    # позже считается "обычно ты выполняешь это в районе 8 утра" (см.
    # db/insights.py::suggest_optimal_reminder_time).
    cursor.execute(
        "INSERT INTO habit_completion_events(user_id, habit_id) VALUES (?, ?)",
        (user_id, habit_id),
    )

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

    # Roadmap #32 — разовый бустер x2 Adam Coin (куплен за Telegram Stars,
    # см. db/users.py::activate_xp_booster) — умножает ИТОГОВУЮ сумму
    # (после всех остальных бонусов), в отличие от doubled (окно
    # удвоения за подряд идущие привычки), которое множит только базу.
    from .users import is_xp_booster_active
    xp_boosted = is_xp_booster_active(user_id)
    if xp_boosted:
        coins *= 2

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

    # Roadmap #7 — цепочки привычек: "сделал А → предложи Б". Если на эту
    # привычку как на триггер настроена другая (chain_trigger_habit_id) и
    # она сегодня ещё не выполнена/не пропущена — подсказываем её фронту
    # мягким тостом-предложением (не автовыполнение, выбор остаётся за
    # пользователем).
    chain_suggestion = None
    conn2 = connect()
    cursor2 = conn2.cursor()
    cursor2.execute(
        "SELECT id, title FROM habits WHERE chain_trigger_habit_id=? AND user_id=? "
        "AND completed=0 AND skip_reason IS NULL LIMIT 1",
        (habit_id, user_id),
    )
    chained = cursor2.fetchone()
    conn2.close()
    if chained:
        chain_suggestion = {"habit_id": chained["id"], "title": chained["title"]}

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
        "chain_suggestion": chain_suggestion,
        "xp_boosted": xp_boosted,
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
