import os
import sqlite3
import json
import logging
from datetime import datetime, date
from typing import Optional, List, Dict, Any

# Каталог для данных, которые должны переживать рестарт/редеплой.
# RAILWAY_VOLUME_MOUNT_PATH — переменная, которую Railway сам прописывает
# в контейнер, когда к сервису подключён persistent Volume (см. README:
# как создать и подключить Volume). Если её нет (например, запуск локально
# для разработки), используем папку рядом с кодом — как было раньше.
DATA_DIR = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "users.db")

logger = logging.getLogger("db")

# ---------- Вспомогательные функции ----------
def get_db_connection():
    """Возвращает соединение с БД и включает поддержку внешних ключей."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def dict_from_row(row):
    """Преобразует sqlite3.Row в dict."""
    return dict(row) if row else None

# ---------- УРОВЕНЬ / ОПЫТ ----------
def _level_for_xp(total_xp: int) -> int:
    """Уровень растёт бесконечно: каждые 100 total_xp — новый уровень.
    Никакого потолка нет (100 -> 2, 1000 -> 11, 100000 -> 1001, ...)."""
    if total_xp is None or total_xp < 0:
        total_xp = 0
    return total_xp // 100 + 1

def add_xp(telegram_id: int, amount: int):
    """Единая точка начисления опыта. Обновляет и тратимый xp (Adam Coin),
    и total_xp (от которого считается уровень), и сам level — атомарно,
    чтобы они никогда не расходились между собой."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT total_xp FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return
    new_total_xp = (row["total_xp"] or 0) + amount
    new_level = _level_for_xp(new_total_xp)
    cur.execute("""
        UPDATE users SET xp = xp + ?, total_xp = ?, level = ?
        WHERE telegram_id = ?
    """, (amount, new_total_xp, new_level, telegram_id))
    conn.commit()
    conn.close()

def give_xp_admin(telegram_id: int, amount: int):
    """Начисление опыта администратором (handlers/admin.py)."""
    add_xp(telegram_id, amount)

# ---------- Создание таблиц ----------
def create_tables():
    """Создаёт все таблицы при первом запуске."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Пользователи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            access_status TEXT DEFAULT 'approved',
            profile_counter INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Привычки
    cur.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Прогресс (дневной)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            telegram_id INTEGER PRIMARY KEY,
            date TEXT DEFAULT (date('now')),
            completed INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0,
            xp_earned INTEGER DEFAULT 0,
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Настройки
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            telegram_id INTEGER PRIMARY KEY,
            reminders INTEGER DEFAULT 1,
            reminder_hour INTEGER DEFAULT 9,
            reminder_minute INTEGER DEFAULT 0,
            ai_style TEXT DEFAULT 'neutral',
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # История AI-чата
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Обратная связь на ответы AI
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ai_feedback (
            message_id INTEGER PRIMARY KEY,
            telegram_id INTEGER NOT NULL,
            rating TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Причины дизлайков (для обучения)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback_reasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            telegram_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Долгосрочный профиль пользователя (память)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            telegram_id INTEGER PRIMARY KEY,
            summary TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Кэш (для простых ответов)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Магазин (предметы)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            price INTEGER NOT NULL
        )
    """)

    # Предметы пользователя
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            acquired_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
            FOREIGN KEY(item_id) REFERENCES shop_items(id) ON DELETE CASCADE,
            UNIQUE(telegram_id, item_id)
        )
    """)

    # Рейтинг (еженедельный)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rating (
            telegram_id INTEGER PRIMARY KEY,
            week_start TEXT DEFAULT (date('now', 'weekday 1', '-7 days')),
            xp INTEGER DEFAULT 0,
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Календарь активности
    cur.execute("""
        CREATE TABLE IF NOT EXISTS calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
            UNIQUE(telegram_id, day)
        )
    """)

    # Достижения
    cur.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Время последнего сообщения AI (троттлинг)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS last_ai_message (
            telegram_id INTEGER PRIMARY KEY,
            last_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # Недельный сброс (для отчётов)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS weekly_summary (
            telegram_id INTEGER PRIMARY KEY,
            week_start TEXT DEFAULT (date('now', 'weekday 1', '-7 days')),
            completed INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            active_days INTEGER DEFAULT 0,
            FOREIGN KEY(telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
    """)

    # План дня
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            plan_date TEXT NOT NULL,
            main_goal TEXT DEFAULT '',
            UNIQUE(telegram_id, plan_date)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            completed INTEGER DEFAULT 0
        )
    """)

    # Миграция: колонка темы оформления могла отсутствовать в старой БД.
    cur.execute("PRAGMA table_info(users)")
    user_columns = {row["name"] for row in cur.fetchall()}
    if "theme" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'violet'")

    # Миграция: total_xp — весь опыт, заработанный за всё время (от него
    # считается уровень). В отличие от xp (это тратимая валюта "Adam Coin",
    # которая уменьшается при покупках в магазине), total_xp никогда не
    # уменьшается, поэтому уровень не может "упасть" из-за покупки.
    if "total_xp" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN total_xp INTEGER DEFAULT 0")
        # Бэкфилл для существующих пользователей: считаем, что весь текущий
        # xp был когда-то заработан, и сразу пересчитываем уровень от него.
        cur.execute("UPDATE users SET total_xp = xp")
        cur.execute("SELECT telegram_id, total_xp FROM users")
        for row in cur.fetchall():
            cur.execute(
                "UPDATE users SET level = ? WHERE telegram_id = ?",
                (_level_for_xp(row["total_xp"]), row["telegram_id"]),
            )

    # Заполняем магазин один раз, если пусто (на новой БД).
    cur.execute("SELECT COUNT(*) AS c FROM shop_items")
    if cur.fetchone()["c"] == 0:
        cur.executemany("""
            INSERT INTO shop_items(id, name, description, price)
            VALUES (?, ?, ?, ?)
        """, [
            (1, "👑 Premium", "Премиум-доступ на 7 дней", 1000),
            (2, "🎨 Тема оформления", "Кастомная тема профиля", 100),
            (3, "🏅 Особый значок", "Значок в профиле", 150),
        ])

    conn.commit()
    conn.close()
    logger.info("✅ Все таблицы созданы (или уже существовали)")

# ---------- ПОЛЬЗОВАТЕЛИ ----------
def get_user(telegram_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict_from_row(row)

def add_user(telegram_id: int, username: str = None, first_name: str = ""):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO users (telegram_id, username, first_name)
        VALUES (?, ?, ?)
    """, (telegram_id, username or "", first_name or ""))
    conn.commit()
    conn.close()
    # Создаём записи в сопутствующих таблицах
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO settings (telegram_id) VALUES (?)", (telegram_id,))
    cur.execute("INSERT OR IGNORE INTO progress (telegram_id) VALUES (?)", (telegram_id,))
    cur.execute("INSERT OR IGNORE INTO user_profile (telegram_id) VALUES (?)", (telegram_id,))
    conn.commit()
    conn.close()

def is_banned(telegram_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT banned FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row["banned"])

def get_access_status(telegram_id: int) -> Optional[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT access_status FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row["access_status"] if row else None

def get_all_users() -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE banned = 0")
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

# ---------- ПРИВЫЧКИ ----------
def get_habits(telegram_id: int) -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, completed FROM habits WHERE telegram_id = ?", (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

def get_habit(habit_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM habits WHERE id = ?", (habit_id,))
    row = cur.fetchone()
    conn.close()
    return dict_from_row(row)

def add_habit(telegram_id: int, title: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO habits (telegram_id, title) VALUES (?, ?)", (telegram_id, title))
    conn.commit()
    # Обновляем total в progress
    cur.execute("UPDATE progress SET total = total + 1 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

def edit_habit(habit_id: int, new_title: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE habits SET title = ? WHERE id = ?", (new_title, habit_id))
    conn.commit()
    conn.close()

def delete_habit(habit_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    # Получаем telegram_id для обновления progress
    cur.execute("SELECT telegram_id FROM habits WHERE id = ?", (habit_id,))
    row = cur.fetchone()
    if row:
        tg_id = row["telegram_id"]
        cur.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        cur.execute("UPDATE progress SET total = total - 1 WHERE telegram_id = ? AND total > 0", (tg_id,))
        conn.commit()
    conn.close()

def complete_habit(habit_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT completed, telegram_id FROM habits WHERE id = ?", (habit_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    if row["completed"]:
        conn.close()
        return False
    tg_id = row["telegram_id"]
    # Отмечаем выполненной
    cur.execute("UPDATE habits SET completed = 1 WHERE id = ?", (habit_id,))
    # Обновляем progress
    cur.execute("UPDATE progress SET completed = completed + 1 WHERE telegram_id = ?", (tg_id,))
    # Обновляем серию (простая логика: увеличиваем на 1)
    cur.execute("UPDATE users SET streak = streak + 1 WHERE telegram_id = ?", (tg_id,))
    conn.commit()
    conn.close()
    # Начисляем XP (+5 за каждую привычку) через add_xp — это же пересчитает
    # total_xp и level, чтобы уровень никогда не "зависал" на месте.
    add_xp(tg_id, 5)
    return True

# ---------- ПРОГРЕСС ----------
def get_progress(telegram_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM progress WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict_from_row(row)

# ---------- НАСТРОЙКИ ----------
def get_settings(telegram_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM settings WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict_from_row(row)

def update_reminder_time(telegram_id: int, hour: int, minute: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET reminder_hour = ?, reminder_minute = ? WHERE telegram_id = ?",
                (hour, minute, telegram_id))
    conn.commit()
    conn.close()

def update_ai_style(telegram_id: int, style: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE settings SET ai_style = ? WHERE telegram_id = ?", (style, telegram_id))
    conn.commit()
    conn.close()

def get_ai_style(telegram_id: int) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT ai_style FROM settings WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row["ai_style"] if row else "neutral"

# ---------- AI ИСТОРИЯ ----------
def add_ai_message(telegram_id: int, role: str, message: str) -> int:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO ai_history (telegram_id, role, message) VALUES (?, ?, ?)",
                (telegram_id, role, message))
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id

def get_ai_history(telegram_id: int, limit: int = 20) -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT role, message, created_at FROM ai_history
        WHERE telegram_id = ?
        ORDER BY created_at ASC
        LIMIT ?
    """, (telegram_id, limit))
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

# ---------- AI ФИДБЕК ----------
def save_ai_feedback(message_id: int, telegram_id: int, rating: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO ai_feedback (message_id, telegram_id, rating, created_at)
        VALUES (?, ?, ?, datetime('now'))
    """, (message_id, telegram_id, rating))
    conn.commit()
    conn.close()

def save_feedback_reason(message_id: int, telegram_id: int, reason: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO feedback_reasons (message_id, telegram_id, reason) VALUES (?, ?, ?)",
                (message_id, telegram_id, reason))
    conn.commit()
    conn.close()

def get_recent_negative_reasons(telegram_id: int, limit: int = 3) -> List[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT reason FROM feedback_reasons
        WHERE telegram_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (telegram_id, limit))
    rows = cur.fetchall()
    conn.close()
    return [r["reason"] for r in rows]

# ---------- ПРОФИЛЬ (память) ----------
def get_user_profile(telegram_id: int) -> Optional[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM user_profile WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return dict_from_row(row)

def update_user_profile(telegram_id: int, summary: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO user_profile (telegram_id, summary, updated_at)
        VALUES (?, ?, datetime('now'))
    """, (telegram_id, summary))
    conn.commit()
    conn.close()

def bump_profile_counter(telegram_id: int) -> int:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET profile_counter = profile_counter + 1 WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    cur.execute("SELECT profile_counter FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row["profile_counter"] if row else 0

# ---------- КЭШ ----------
def cache_set(key: str, value: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def cache_get(key: str) -> Optional[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM cache WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else None

# ---------- ТРОТТЛИНГ AI ----------
def get_last_ai_message_at(telegram_id: int) -> Optional[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT last_at FROM last_ai_message WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return row["last_at"] if row else None

def touch_last_ai_message(telegram_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO last_ai_message (telegram_id, last_at)
        VALUES (?, datetime('now'))
    """, (telegram_id,))
    conn.commit()
    conn.close()

# ---------- МАГАЗИН ----------
def get_shop_items() -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shop_items ORDER BY price")
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

def get_user_items(telegram_id: int) -> List[int]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT item_id FROM user_items WHERE telegram_id = ?", (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["item_id"] for r in rows]

def has_item(telegram_id: int, item_id: int) -> bool:
    """Владеет ли пользователь конкретным товаром магазина (куплен ли он)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM user_items WHERE telegram_id = ? AND item_id = ? LIMIT 1",
        (telegram_id, item_id)
    )
    row = cur.fetchone()
    conn.close()
    return row is not None

def get_item_owner_ids(item_id: int) -> set:
    """Множество telegram_id всех пользователей, купивших данный товар —
    используется, например, чтобы показать значок 🏅 в рейтинге у всех
    владельцев товара «Особый значок», без запроса на каждую строку."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM user_items WHERE item_id = ?", (item_id,))
    ids = {row["telegram_id"] for row in cur.fetchall()}
    conn.close()
    return ids

def get_theme(telegram_id: int) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT theme FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return (row["theme"] if row and row["theme"] else "violet")

def update_theme(telegram_id: int, theme: str) -> bool:
    valid_themes = {"violet", "blue", "green", "pink"}
    if theme not in valid_themes:
        return False
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET theme = ? WHERE telegram_id = ?", (theme, telegram_id))
    conn.commit()
    conn.close()
    return True

def buy_shop_item(telegram_id: int, item_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    # Проверяем наличие предмета и цену
    cur.execute("SELECT price FROM shop_items WHERE id = ?", (item_id,))
    item = cur.fetchone()
    if not item:
        conn.close()
        return False
    price = item["price"]
    cur.execute("SELECT xp FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cur.fetchone()
    if not user or user["xp"] < price:
        conn.close()
        return False
    # Проверяем, не куплен ли уже
    cur.execute("SELECT 1 FROM user_items WHERE telegram_id = ? AND item_id = ?", (telegram_id, item_id))
    if cur.fetchone():
        conn.close()
        return False
    # Списываем только тратимую валюту (xp / Adam Coin). total_xp и level
    # НЕ трогаем — покупки в магазине не должны понижать уровень игрока.
    cur.execute("UPDATE users SET xp = xp - ? WHERE telegram_id = ?", (price, telegram_id))
    cur.execute("INSERT INTO user_items (telegram_id, item_id) VALUES (?, ?)", (telegram_id, item_id))
    conn.commit()
    conn.close()
    return True

# ---------- РЕЙТИНГ ----------
def get_rating(limit: int = 50) -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.telegram_id, u.username, u.first_name, u.xp, u.level, u.streak
        FROM users u
        WHERE u.banned = 0
        ORDER BY u.xp DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

# ---------- КАЛЕНДАРЬ ----------
def get_calendar(telegram_id: int) -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    # Возвращаем записи за последние 30 дней
    cur.execute("""
        SELECT day, completed FROM calendar
        WHERE telegram_id = ? AND day >= date('now', '-30 days')
        ORDER BY day
    """, (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

# ---------- ДОСТИЖЕНИЯ ----------
def get_achievements(telegram_id: int) -> List[Dict]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM achievements WHERE telegram_id = ? ORDER BY created_at DESC", (telegram_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

# ---------- НЕДЕЛЬНЫЙ ОТЧЁТ ----------
def get_weekly_summary(telegram_id: int) -> Dict:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT completed, xp, active_days FROM weekly_summary
        WHERE telegram_id = ?
    """, (telegram_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"completed": 0, "xp": 0, "active_days": 0}

# ---------- ЛОГИРОВАНИЕ ОШИБОК ----------
def log_error(context: str, error: Exception, telegram_id: int = None):
    """Сохраняет ошибку в таблицу логов (если нужна). Можно реализовать отдельную таблицу."""
    # Просто логируем в стандартный логгер, но можно и в БД сохранять
    logger.error(f"Error in {context} for user {telegram_id}: {error}")
    # Можно добавить таблицу error_logs и записывать туда


# ---------- ПЛАН ДНЯ ----------
def get_daily_plan(telegram_id: int, plan_date: str | None = None):
    plan_date = plan_date or date.today().isoformat()

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM daily_plans WHERE telegram_id=? AND plan_date=?",
        (telegram_id, plan_date)
    )
    plan = cur.fetchone()

    if not plan:
        cur.execute(
            "INSERT INTO daily_plans (telegram_id, plan_date) VALUES (?, ?)",
            (telegram_id, plan_date)
        )
        conn.commit()

        cur.execute(
            "SELECT * FROM daily_plans WHERE telegram_id=? AND plan_date=?",
            (telegram_id, plan_date)
        )
        plan = cur.fetchone()

    cur.execute(
        "SELECT * FROM daily_tasks WHERE plan_id=? ORDER BY id",
        (plan["id"],)
    )
    tasks = cur.fetchall()

    conn.close()

    return {
        "id": plan["id"],
        "main_goal": plan["main_goal"] or "",
        "tasks": [dict_from_row(t) for t in tasks]
    }


def save_daily_plan(telegram_id: int, main_goal: str, tasks: list[str]):
    plan = get_daily_plan(telegram_id)
    plan_id = plan["id"]

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE daily_plans SET main_goal=? WHERE id=?",
        (main_goal.strip(), plan_id)
    )

    cur.execute("DELETE FROM daily_tasks WHERE plan_id=?", (plan_id,))

    for task in tasks[:5]:
        task = task.strip()
        if task:
            cur.execute(
                "INSERT INTO daily_tasks (plan_id, text) VALUES (?, ?)",
                (plan_id, task)
            )

    conn.commit()
    conn.close()


def toggle_daily_task(task_id: int):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE daily_tasks SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END WHERE id=?",
        (task_id,)
    )

    conn.commit()
    conn.close()