import sqlite3

DB_NAME = "users.db"


# =====================================
# ПОДКЛЮЧЕНИЕ
# =====================================

def connect():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


# =====================================
# СОЗДАНИЕ ТАБЛИЦ
# =====================================

def create_tables():

    conn = connect()
    cursor = conn.cursor()

    # ---------------- USERS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        premium INTEGER DEFAULT 0,
        banned INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        streak INTEGER DEFAULT 0,
        referrals INTEGER DEFAULT 0,
        referrer_id INTEGER,
        total_completed INTEGER DEFAULT 0,
        last_completed TEXT,
        bonus_date TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- SETTINGS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        reminders INTEGER DEFAULT 1,
        reminder_hour INTEGER DEFAULT 9,
        reminder_minute INTEGER DEFAULT 0,
        ai_style TEXT DEFAULT 'neutral'
    )
    """)

    # Миграция для БД, созданных до появления ai_style (у существующих
    # пользователей колонки ещё нет — ALTER TABLE один раз безопасно
    # добавляет её со значением по умолчанию 'neutral').
    cursor.execute("PRAGMA table_info(settings)")
    settings_columns = {row[1] for row in cursor.fetchall()}
    if "ai_style" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN ai_style TEXT DEFAULT 'neutral'")

    # ---------------- HABITS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- SHOP ----------------

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS shop_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        price INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_id INTEGER,
        purchased_at TEXT
    )
    """)

    # Заполняем магазин один раз (если пусто)
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO shop_items(id, name, description, price)
            VALUES (?, ?, ?, ?)
        """, [
            (1, "👑 Premium", "Премиум-доступ навсегда", 500),
            (2, "🎨 Тема оформления", "Кастомная тема профиля", 100),
            (3, "🏅 Особый значок", "Значок в профиле", 150),
        ])

    # ---------------- DAILY TASKS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT,
        progress INTEGER DEFAULT 0,
        goal INTEGER DEFAULT 1,
        reward INTEGER DEFAULT 20,
        completed INTEGER DEFAULT 0,
        task_date TEXT
    )
    """)

    # ---------------- STATISTICS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS statistics(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        completed INTEGER DEFAULT 0,
        gained_xp INTEGER DEFAULT 0,
        stat_date TEXT
    )
    """)

    # ---------------- AI ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        role TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- AI FEEDBACK ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_feedback(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER,
        user_id INTEGER,
        rating TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(message_id, user_id)
    )
    """)

    # Миграция: причина дизлайка (для "обучения" на 👎 — этап 2 AI Core).
    cursor.execute("PRAGMA table_info(ai_feedback)")
    ai_feedback_columns = {row[1] for row in cursor.fetchall()}
    if "reason" not in ai_feedback_columns:
        cursor.execute("ALTER TABLE ai_feedback ADD COLUMN reason TEXT")

    # ---------------- AI ДОЛГОСРОЧНАЯ ПАМЯТЬ ----------------
    # Короткий профиль пользователя (3-5 фактов), который переживает
    # рестарты и обновляется раз в несколько сообщений — этап 2 AI Core.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_ai_profile(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        summary TEXT DEFAULT '',
        message_count INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- AI КЭШ ОТВЕТОВ ----------------
    # Кэш финальных ответов на простые/повторяющиеся сообщения ("привет",
    # "спасибо" и т.п.) — этап 4 "Оптимизация": меньше запросов к Groq,
    # быстрее ответ. Хранится в БД (не в памяти процесса), так что переживает
    # рестарты и работает одинаково для всех воркеров.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_response_cache(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cache_key TEXT UNIQUE,
        answer TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- ЛОГ ОШИБОК (мониторинг) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS error_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT,
        error TEXT,
        user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- ACHIEVEMENTS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS achievements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- CALENDAR ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS calendar(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        day TEXT,
        completed INTEGER DEFAULT 0
    )
    """)

    

    conn.commit()
    conn.close()
