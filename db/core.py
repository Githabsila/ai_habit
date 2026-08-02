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
        reminder_minute INTEGER DEFAULT 0
    )
    """)

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

    # ---------------- GOOGLE CALENDAR (OAuth-токены) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS google_tokens(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        access_token TEXT,
        refresh_token TEXT,
        token_expiry TEXT,
        calendar_event_id TEXT,
        connected_at TEXT
    )
    """)

    conn.commit()
    conn.close()
