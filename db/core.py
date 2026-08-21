import os
import sqlite3

DB_NAME = "users.db"

# =====================================
# ПУТЬ К БАЗЕ (постоянный Volume на Railway)
# =====================================
#
# ВАЖНО: Railway монтирует подключённый Volume в контейнер по пути из
# переменной окружения RAILWAY_VOLUME_MOUNT_PATH (её Railway выставляет
# автоматически, если Volume подключён к сервису). Файловая система
# контейнера ВНЕ этого пути — эфемерная и полностью стирается при каждом
# редеплое/рестарте. Раньше DB_NAME использовался как относительный путь
# ("users.db" рядом с кодом) — то есть база физически лежала на эфемерном
# диске и обнулялась при каждом деплое (см. backups/backup.py — он уже
# был написан в расчёте на DATA_DIR/DB_PATH отсюда, но эти два имени тут
# отсутствовали, из-за чего бэкапы вообще не запускались).
#
# Если Volume не подключён (например, при локальном запуске) —
# используем папку рядом с кодом, как раньше.
DATA_DIR = os.environ.get(
    "RAILWAY_VOLUME_MOUNT_PATH",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(DATA_DIR, DB_NAME)


# =====================================
# ПОДКЛЮЧЕНИЕ
# =====================================

def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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

    # Миграция: доступ ("закрытое сообщество") — анкета при первом входе +
    # модерация. Существующие пользователи (те, что были в базе ДО появления
    # этой колонки) считаются approved автоматически, чтобы не заблокировать
    # уже пользующихся ботом людей анкетой задним числом.
    cursor.execute("PRAGMA table_info(users)")
    users_columns = {row[1] for row in cursor.fetchall()}
    is_first_deploy_of_access_gate = "access_status" not in users_columns

    if is_first_deploy_of_access_gate:
        cursor.execute("ALTER TABLE users ADD COLUMN access_status TEXT DEFAULT 'new'")
    if "survey_completed_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN survey_completed_at TIMESTAMP")

    # DB-бэкенд для троттлинга AI-чата вместо in-memory словаря в процессе —
    # переживает рестарт/редеплой, не требует Redis.
    if "last_ai_message_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_ai_message_at TIMESTAMP")

    # Premium теперь временный (на неделю), а не навсегда — нужна дата
    # окончания. premium_purchased хранится отдельно и НИКОГДА не сбрасывается
    # даже после истечения premium — это флаг "уже покупал когда-либо",
    # чтобы Premium из магазина нельзя было купить повторно.
    if "premium_until" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP")
    if "premium_purchased" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN premium_purchased INTEGER DEFAULT 0")
        # У кого уже стоит premium=1 на момент миграции — считаем, что он
        # уже "куплен", чтобы не выдать его повторно бесплатно.
        cursor.execute("UPDATE users SET premium_purchased=1 WHERE premium=1")

    if is_first_deploy_of_access_gate:
        cursor.execute("UPDATE users SET access_status='approved' WHERE access_status='new'")

    # Миграция: total_xp — весь опыт, заработанный за всё время, от него
    # считается уровень. В отличие от xp (тратимая валюта "Adam Coin",
    # уменьшается при покупках в магазине) total_xp никогда не уменьшается,
    # поэтому уровень больше не может "упасть" из-за покупки в магазине.
    if "avatar_id" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN avatar_id TEXT DEFAULT 'default'")
    if "frame_id" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN frame_id TEXT DEFAULT 'default'")

    if "total_xp" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN total_xp INTEGER DEFAULT 0")
        # Бэкфилл для уже существующих пользователей: считаем весь текущий
        # xp когда-то заработанным и сразу пересчитываем уровень от него.
        cursor.execute("UPDATE users SET total_xp = xp")
        cursor.execute("UPDATE users SET level = total_xp / 100 + 1")

    # ---------------- SETTINGS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        reminders INTEGER DEFAULT 1,
        reminder_hour INTEGER DEFAULT 9,
        reminder_minute INTEGER DEFAULT 0,
        ai_style TEXT DEFAULT 'neutral',
        theme TEXT DEFAULT 'violet'
    )
    """)

    # Миграция для БД, созданных до появления ai_style (у существующих
    # пользователей колонки ещё нет — ALTER TABLE один раз безопасно
    # добавляет её со значением по умолчанию 'neutral').
    cursor.execute("PRAGMA table_info(settings)")
    settings_columns = {row[1] for row in cursor.fetchall()}
    if "ai_style" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN ai_style TEXT DEFAULT 'neutral'")

    # Миграция для БД, созданных до появления темы оформления (товар
    # магазина «🎨 Тема оформления») — добавляем колонку со значением
    # по умолчанию 'violet' (текущий цвет приложения).
    if "theme" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN theme TEXT DEFAULT 'violet'")

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

    # Миграция: индивидуальные напоминания по конкретной задаче (привычке).
    # assigned_at — момент, с которого отсчитываем "N часов без выполнения"
    # (при создании привычки = created_at, а каждый день в 00:00 сбрасывается
    # заново вместе с completed — см. reset_habits()). reminder_sent —
    # флаг "по этой задаче уже напомнили сегодня", чтобы не спамить на
    # каждый тик планировщика, сбрасывается там же, в reset_habits().
    cursor.execute("PRAGMA table_info(habits)")
    habits_columns = {row[1] for row in cursor.fetchall()}
    if "assigned_at" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN assigned_at TIMESTAMP")
        cursor.execute("UPDATE habits SET assigned_at = created_at WHERE assigned_at IS NULL")
    if "reminder_sent" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN reminder_sent INTEGER DEFAULT 0")
    if "planned_time" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN planned_time TEXT")
    if "time_window_minutes" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN time_window_minutes INTEGER DEFAULT 60")

    # ---------------- ПЛАН ДНЯ (Mini App) ----------------
    # main_goal — общая цель дня, tasks — до 5 отдельных задач (например
    # «Прочитать книгу»). Раньше эти данные никуда не сохранялись — см.
    # db/daily_plan.py.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_plans(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        plan_date TEXT NOT NULL,
        main_goal TEXT DEFAULT '',
        goal_reminder_sent INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, plan_date)
    )
    """)

    # Миграция: состояние выполнения главной задачи хранится вместе с планом,
    # чтобы галочка не сбрасывалась после перезагрузки Mini App.
    cursor.execute("PRAGMA table_info(daily_plans)")
    daily_plan_columns = {row[1] for row in cursor.fetchall()}
    if "main_goal_completed" not in daily_plan_columns:
        cursor.execute("ALTER TABLE daily_plans ADD COLUMN main_goal_completed INTEGER DEFAULT 0")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_plan_tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER,
        text TEXT NOT NULL,
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reminder_sent INTEGER DEFAULT 0
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

    cursor.execute("PRAGMA table_info(shop_items)")
    shop_columns = {row[1] for row in cursor.fetchall()}
    if "item_type" not in shop_columns:
        cursor.execute("ALTER TABLE shop_items ADD COLUMN item_type TEXT DEFAULT 'cosmetic'")
    if "payload" not in shop_columns:
        cursor.execute("ALTER TABLE shop_items ADD COLUMN payload TEXT DEFAULT ''")
    if "repeatable" not in shop_columns:
        cursor.execute("ALTER TABLE shop_items ADD COLUMN repeatable INTEGER DEFAULT 0")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        item_id INTEGER,
        purchased_at TEXT
    )
    """)

    # Миграция: Premium раньше стоил 500 и был навсегда, теперь 1000 и на
    # неделю — обновляем уже существующую строку (INSERT ниже сработает
    # только на пустой таблице, т.е. только при самом первом деплое).
    cursor.execute("""
        UPDATE shop_items
        SET price=1000, description='Премиум-доступ на 7 дней'
        WHERE id=1
    """)

    # Заполняем магазин один раз (если пусто)
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO shop_items(id, name, description, price)
            VALUES (?, ?, ?, ?)
        """, [
            (1, "👑 Premium", "Премиум-доступ на 7 дней", 1000),
            (2, "🎨 Тема оформления", "Кастомная тема профиля", 100),
            (3, "🏅 Особый значок", "Значок в профиле", 150),
            (4, "🧑‍🚀 Аватар: ADAM", "Аватар профиля", 250),
            (5, "🪐 Рамка: Neon", "Рамка аватара", 200),
            (6, "✨ Рамка: Gold", "Рамка аватара", 350),
            (20, "💬 +5 ответов ADAM", "Пять дополнительных ответов AI", 100),
            (21, "💬 +20 ответов ADAM", "Двадцать дополнительных ответов AI", 300),
        ])

    # ---------------- AI QUOTA ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_quota(
        user_id INTEGER PRIMARY KEY,
        day TEXT NOT NULL,
        used INTEGER DEFAULT 0,
        bonus_answers INTEGER DEFAULT 0
    )
    """)

    cursor.execute("UPDATE shop_items SET item_type='premium' WHERE id=1")
    cursor.execute("UPDATE shop_items SET item_type='theme' WHERE id=2")
    cursor.execute("UPDATE shop_items SET item_type='badge' WHERE id=3")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (4,'🧑‍🚀 Аватар: ADAM','Аватар профиля',250,'avatar','adam',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (5,'🪐 Рамка: Neon','Рамка аватара',200,'frame','neon',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (6,'✨ Рамка: Gold','Рамка аватара',350,'frame','gold',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (20,'💬 +5 ответов ADAM','Пять дополнительных ответов AI',100,'answer_pack','5',1)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (21,'💬 +20 ответов ADAM','Двадцать дополнительных ответов AI',300,'answer_pack','20',1)")

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

    # ---------------- АНКЕТА ПРИ ВХОДЕ (onboarding) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_survey(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        business TEXT,
        hobbies TEXT,
        life_goal TEXT,
        bot_goal TEXT,
        ai_summary TEXT,
        ai_tags TEXT,
        last_feedback_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("PRAGMA table_info(user_survey)")
    survey_columns = {row[1] for row in cursor.fetchall()}
    if "last_feedback_at" not in survey_columns:
        cursor.execute("ALTER TABLE user_survey ADD COLUMN last_feedback_at TIMESTAMP")

    # ---------------- ВЕХИ ПО ЦЕЛИ (milestones) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_milestones(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        goal_text TEXT,
        milestone_text TEXT,
        done INTEGER DEFAULT 0,
        position INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- CALENDAR ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS calendar(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        day TEXT,
        completed INTEGER DEFAULT 0,
        total INTEGER DEFAULT 0
    )
    """)

    # Миграция: раньше клетка календаря красилась по абсолютному числу
    # выполненных привычек за день (0/1/2-3/4+), из-за чего при малом
    # количестве привычек клетка никогда не становилась полностью золотой,
    # даже если пользователь выполнил ВСЕ привычки за день. Теперь хранится
    # ещё и total (сколько привычек было у пользователя на момент отметки),
    # чтобы красить по проценту completed/total, а не по голому счётчику.
    cursor.execute("PRAGMA table_info(calendar)")
    calendar_columns = {row[1] for row in cursor.fetchall()}
    if "total" not in calendar_columns:
        cursor.execute("ALTER TABLE calendar ADD COLUMN total INTEGER DEFAULT 0")

        # Бэкфилл: у уже накопленных записей (созданных до этого обновления)
        # total не было и не могло быть — колонка появилась только что, и
        # ALTER TABLE проставил всем старым строкам 0. Без бэкфилла такие дни
        # красятся как "нет данных" (серый), и пользователь видит, будто его
        # прогресс за прошлые дни пропал, хотя completed по-прежнему на месте.
        # Точное значение total на тот момент не сохранялось, поэтому берём
        # текущее число привычек пользователя как разумное приближение —
        # лучше, чем ничего, и в большинстве случаев совпадает с реальным.
        cursor.execute("""
            UPDATE calendar
            SET total = (
                SELECT COUNT(*) FROM habits WHERE habits.user_id = calendar.user_id
            )
            WHERE total = 0
        """)

    # ---------------- HABIT LOGS (посуточный журнал по каждой привычке) ----------------
    # Снимок состояния каждой привычки за каждый прошедший день — в отличие
    # от calendar (общий агрегат по дню), тут видно конкретно КАКАЯ привычка
    # была выполнена/пропущена. Нужно для еженедельного AI-анализа по
    # привычкам (см. coach.run_weekly_habit_analysis) — заполняется в
    # scheduler.new_day() перед сбросом habits.completed.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        habit_id INTEGER,
        habit_title TEXT,
        day TEXT,
        completed INTEGER DEFAULT 0
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habit_logs_user_day "
        "ON habit_logs(user_id, day)"
    )

    # ---------------- УДАРНЫЙ РЕЖИМ / STREAK ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streak_meta(
        user_id INTEGER PRIMARY KEY,
        timezone TEXT DEFAULT 'UTC',
        rollover_day TEXT,
        onboarding_seen INTEGER DEFAULT 0,
        freeze_balance INTEGER DEFAULT 0,
        freeze_purchased_week TEXT,
        freeze_purchased_count INTEGER DEFAULT 0,
        temp_frame TEXT,
        temp_status TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streak_days(
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        status TEXT NOT NULL,
        streak_after INTEGER DEFAULT 0,
        ai_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, day)
    )
    """)
    cursor.execute("PRAGMA table_info(streak_days)")
    streak_day_columns = {row[1] for row in cursor.fetchall()}
    if "event_delivered" not in streak_day_columns:
        cursor.execute("ALTER TABLE streak_days ADD COLUMN event_delivered INTEGER DEFAULT 0")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streak_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        milestone INTEGER NOT NULL,
        status TEXT NOT NULL,
        frame TEXT NOT NULL,
        permanent INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, milestone)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streak_weekly_choices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        week_key TEXT NOT NULL,
        reward_type TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, week_key)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS streak_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, day, kind)
    )
    """)
    # Старые пользователи не должны внезапно увидеть onboarding.
    cursor.execute("""
        INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen)
        SELECT telegram_id, 1 FROM users
    """)
    conn.commit()
    conn.close()
