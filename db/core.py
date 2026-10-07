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
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Railway/aiohttp can have several concurrent requests touching SQLite.
    # WAL allows readers during writes and busy_timeout prevents immediate
    # "database is locked" failures during short concurrent transactions.
    # WAL mode is persistent; avoid reconfiguring it on every connection.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# =====================================
# СОЗДАНИЕ ТАБЛИЦ
# =====================================

def create_tables():

    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
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

    # Одноразовое приветственное замечание AI: после первого обработанного
    # сообщения автоматически больше не показываем его.
    if "ai_intro_shown" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN ai_intro_shown INTEGER DEFAULT 0")

    # Напоминание «Адам хочет спросить, как дела» (db/ai_nudge.py): день (локальная
    # дата пользователя, ISO), когда подсказка уже показывалась / Адам уже
    # поздоровался в чате — чтобы не повторять чаще раза в день.
    if "ai_nudge_hint_day" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN ai_nudge_hint_day TEXT")
    if "ai_greet_day" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN ai_greet_day TEXT")

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

    # Пром 8 (доп.): «алмазы» — премиальная валюта, которую нельзя заработать
    # обычными действиями, только купить за деньги/Stars, либо получить
    # небольшое количество в награду за сундуки «Заданий месяца»
    # (см. db/month_quests.py).
    if "diamonds" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN diamonds INTEGER DEFAULT 0")

    # Обучение по основному функционалу при первом входе (не путать с
    # streak_meta.onboarding_seen — то показывается только после первой
    # добавленной привычки и только про ударный режим). Это — один раз за
    # всё время использования аккаунта, показывает весь Mini App целиком.
    if "app_tour_seen" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN app_tour_seen INTEGER DEFAULT 0")

    # Разовый экран "вот твой @ник" сразу после app-tour (Слой A онбординга):
    # ник уже назначен автоматически при регистрации (см. db/handles.py),
    # этот экран просто показывает его и даёт сразу сменить, не уходя в
    # Настройки. Отдельный флаг от app_tour_seen — экран логически другой
    # шаг и должен показываться независимо, если когда-нибудь app-tour
    # начнёт пропускаться раньше.
    if "handle_intro_seen" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN handle_intro_seen INTEGER DEFAULT 0")

    # Стартовый квиз "сколько лет / зачем пришёл" — 2-шаговый экран ПЕРЕД
    # app-tour (просьба пользователя, по образцу конкурентов): возраст и
    # цель дальше используются, чтобы ADAM обращался уместнее в первом
    # приветствии AI-чата. Один раз за всё время аккаунта, как и
    # app_tour_seen/handle_intro_seen выше.
    if "start_quiz_seen" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN start_quiz_seen INTEGER DEFAULT 0")
    if "onboarding_age_range" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN onboarding_age_range TEXT")
    if "onboarding_goal" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN onboarding_goal TEXT")

    # ---------------- ПОДПИСКА: триал → оплата → закрытый канал (пром 13) ----------------
    # Отдельно от "Premium" (косметический тариф выше) — это доступ к
    # самому боту после 3-дневного триала. subscription_paid_until=NULL
    # значит "ещё ни разу не платил"; subscription_first_payment_at нужен,
    # чтобы отличить первый платёж (по скидке) от продления (полная цена).
    if "subscription_paid_until" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_paid_until TIMESTAMP")
    if "subscription_first_payment_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_first_payment_at TIMESTAMP")
    if "channel_access_granted_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN channel_access_granted_at TIMESTAMP")

    # last_seen — для DAU/WAU в аналитике (db/analytics.py). Обновляется на
    # каждый заход в Mini App (webapp/auth_helpers.py) и на каждое сообщение
    # боту (middlewares/access_control.py), поэтому отражает обе поверхности.
    if "last_seen" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_seen TIMESTAMP")

    # Найдено при разборе ежедневного мониторинга ошибок: morning_ping и
    # habit_checkpoint_* каждый тик заново пытались слать уже заблокировавшим
    # бота пользователям (Telegram отвечает Forbidden на КАЖДУЮ попытку
    # навсегда, не разово) — та же пара пользователей давала одинаковую
    # ошибку по 4 раза в каждое окно каждого из 5 job'ов. NULL — не
    # заблокирован, timestamp — когда это обнаружили (см. db/users.py::
    # mark_bot_blocked, используется в coach.py/morning_ping.py).
    if "bot_blocked_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bot_blocked_at TIMESTAMP")

    # Roadmap #39 — архетип личности, определяется один раз коротким тестом
    # при онбординге/из профиля (см. db/personality.py).
    if "archetype" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN archetype TEXT")
    # Roadmap #17 — публичный шаринг-профиль: по умолчанию выключен, только
    # сам пользователь может включить в настройках (см. db/public_profile.py).
    if "public_profile_enabled" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN public_profile_enabled INTEGER DEFAULT 0")
    # Roadmap #32 — разовый бустер x2 XP за Stars: до какого момента активен.
    if "bonus_2x_xp_until" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN bonus_2x_xp_until TIMESTAMP")
    # Roadmap #25 — долгосрочные жизненные цели, которые AI-наставник должен
    # держать в контексте (в отличие от auto-summary памяти — этот текст
    # редактирует сам пользователь явно, см. db/goals.py).
    if "long_term_goals" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN long_term_goals TEXT")

    # Product onboarding: фиксируем прогресс первых 15 минут серверно, чтобы
    # подсказки не сбрасывались при перезагрузке Mini App и были доступны
    # для продуктовой аналитики ранних тестеров.
    if "onboarding_started_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN onboarding_started_at TIMESTAMP")
    if "onboarding_stage" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN onboarding_stage INTEGER DEFAULT 0")
    if "first_habit_completed_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_habit_completed_at TIMESTAMP")
    if "first_task_completed_at" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_task_completed_at TIMESTAMP")
    if "first_win_push_sent" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN first_win_push_sent INTEGER DEFAULT 0")

    # Уникальный игровой @ник (в духе Duolingo) — отдельная от
    # telegram-username сущность (см. db/handles.py), есть у КАЖДОГО
    # пользователя, в отличие от username, который Telegram не гарантирует.
    # UNIQUE — не inline в ALTER TABLE (SQLite запрещает добавлять колонку
    # с UNIQUE на непустую таблицу через ALTER), а отдельным
    # CREATE UNIQUE INDEX сразу после добавления колонки. Бэкфилл сразу для
    # уже существующих пользователей — ник появляется у всех сразу в
    # момент этого деплоя, а не откладывается до следующего входа каждого.
    if "handle" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN handle TEXT")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_handle ON users(handle)")
        from .handles import generate_unique_handle
        cursor.execute("SELECT telegram_id, first_name FROM users WHERE handle IS NULL")
        for row in cursor.fetchall():
            cursor.execute(
                "UPDATE users SET handle=? WHERE telegram_id=?",
                (generate_unique_handle(cursor, row["first_name"]), row["telegram_id"]),
            )

    # ---------------- SETTINGS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        reminders INTEGER DEFAULT 1,
        reminder_hour INTEGER DEFAULT 9,
        reminder_minute INTEGER DEFAULT 0,
        ai_style TEXT DEFAULT 'neutral',
        theme TEXT DEFAULT 'violet',
        reminders_habits INTEGER DEFAULT 1,
        reminders_streak INTEGER DEFAULT 1,
        reminders_digests INTEGER DEFAULT 1,
        quiet_hours_start INTEGER,
        quiet_hours_end INTEGER,
        habit_checkpoint_style TEXT DEFAULT 'full',
        home_habits_first INTEGER DEFAULT 0,
        home_plan_hidden INTEGER DEFAULT 0
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

    # Roadmap #48 — светлая тема: 'dark'/'light', бесплатно (в отличие от
    # акцентного theme выше, который требует покупки в магазине).
    if "color_mode" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN color_mode TEXT DEFAULT 'dark'")

    # Гранулярные напоминания: раньше был только один общий тумблер
    # `reminders` — "всё или ничего". Эти три колонки позволяют отключить
    # ТОЛЬКО, например, пуши про ударный режим, оставив утренние и вечерние
    # напоминания по привычкам. `reminders=0` по-прежнему выключает всё
    # разом (проверяется первым во всех job'ах-напоминаниях) — новые флаги
    # сужают именно ВКЛЮЧЁННОЕ подмножество, ничего не ломая для тех, кто
    # их ещё не трогал (DEFAULT 1 — как было).
    if "reminders_habits" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN reminders_habits INTEGER DEFAULT 1")
    if "reminders_streak" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN reminders_streak INTEGER DEFAULT 1")
    if "reminders_digests" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN reminders_digests INTEGER DEFAULT 1")

    # "Тихие часы" (roadmap #35) — окно локальных часов, в которое не
    # приходят повседневные напоминания (привычки/ударный режим). Оба
    # NULL по умолчанию — функция выключена, ничего не меняется для тех,
    # кто её не настраивал.
    if "quiet_hours_start" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN quiet_hours_start INTEGER")
    if "quiet_hours_end" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN quiet_hours_end INTEGER")

    # Стиль контрольной точки по привычкам (10/12/17/22:00): 'full' — как
    # раньше, со сверкой прогресса и доп. строкой про привычки со своим
    # (ещё не наступившим) временем; 'simple' — для тех, у кого почти все
    # привычки уже на таймере и своя система в голове/жизни — без рамки
    # "сверки точки дня" и без упоминания таймерных привычек вообще (по
    # ним и так придёт отдельное напоминание в своё время), только прямое
    # упоминание того, что осталось без таймера. DEFAULT 'full' — ничего
    # не меняется для тех, кто его не настраивал.
    if "habit_checkpoint_style" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN habit_checkpoint_style TEXT DEFAULT 'full'")

    # Главный экран: порядок разделов "План дня"/"Привычки" и видимость
    # "Плана дня" — многие пользуются сторонними планировщиками задач, а
    # здесь главное привычки. DEFAULT 0/0 — ничего не меняется для тех,
    # кто их не настраивал (план сверху и виден, как раньше).
    if "home_habits_first" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN home_habits_first INTEGER DEFAULT 0")
    if "home_plan_hidden" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN home_plan_hidden INTEGER DEFAULT 0")

    # «Напомнить друзьям» (db/friends.py): тумблер «разрешить друзьям
    # подталкивать меня». DEFAULT 1 — включено, как у остальных напоминаний.
    if "friend_nudges" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN friend_nudges INTEGER DEFAULT 1")

    # Кому видна моя статистика в профиле (db/profiles.py): 'subscribers' —
    # всем, кто подписан на меня (частичная), друзьям (взаимная подписка)
    # — расширенная; 'friends' — только друзьям, подписчики видят лишь
    # публичный минимум.
    if "stats_visibility" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN stats_visibility TEXT DEFAULT 'subscribers'")

    # Показывать ли ДРУЗЬЯМ (взаимная подписка) названия моих привычек и
    # текст долгосрочных целей. По умолчанию выключено: названия бывают
    # личными («бросить курить»), а числа — нет. Подписчикам не отдаётся
    # никогда, даже при включённом показе.
    if "share_habits" not in settings_columns:
        cursor.execute("ALTER TABLE settings ADD COLUMN share_habits INTEGER DEFAULT 0")

    # ---------------- HABITS ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        category TEXT,
        priority INTEGER DEFAULT 1,
        skip_reason TEXT
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

    # Категория (health/work/study/other — см. db/habits.py HABIT_CATEGORIES),
    # приоритет (1 обычная, 2 важная — влияет на награду и на то, что
    # подсвечивается в напоминаниях) и причина пропуска на сегодня
    # (skip_reason — заполняется через skip_habit(), сбрасывается каждую
    # ночь вместе с completed в reset_habits(), см. ниже).
    if "category" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN category TEXT")
    if "priority" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN priority INTEGER DEFAULT 1")
    if "skip_reason" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN skip_reason TEXT")

    # Roadmap #1 (счётчик, «выпить 4 стакана») — target_count/progress_count.
    # Roadmap #2 (гибкая периодичность, «3 раза в неделю») — frequency_per_week
    # (NULL = как раньше, каждый день). Roadmap #7 (цепочки привычек) —
    # chain_trigger_habit_id: если задано, эта привычка "предлагается"
    # сразу после выполнения привычки-триггера (см. db/habits.py).
    if "target_count" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN target_count INTEGER DEFAULT 1")
    if "progress_count" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN progress_count INTEGER DEFAULT 0")
    if "frequency_per_week" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN frequency_per_week INTEGER")
    if "chain_trigger_habit_id" not in habits_columns:
        cursor.execute("ALTER TABLE habits ADD COLUMN chain_trigger_habit_id INTEGER")

    # ---------------- (УСТАРЕЛО) МЕСЯЧНАЯ СЕРИЯ 2+ ПРИВЫЧЕК ----------------
    # Заменена «Заданиями месяца» (db/month_quests.py, таблица month_chests
    # ниже): «идеальный месяц» обнулялся от одного пропуска. Эти две таблицы
    # остались только как архив старых данных — код в них больше не пишет.
    # multi_habit_days — локальный день, в который пользователь закрыл 2+
    # привычки (см. db/habits.py complete_habit) — это и есть "1 балл" к
    # месячному счётчику. monthly_streak_rewards — выданные награды за
    # идеальный месяц (все дни месяца с 2+ привычками), одна запись на
    # месяц на пользователя, чтобы не выдать повторно.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS multi_habit_days(
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, day)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS monthly_streak_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        month_key TEXT NOT NULL,
        coins INTEGER DEFAULT 0,
        diamonds INTEGER DEFAULT 0,
        event_delivered INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, month_key)
    )
    """)

    # ---------------- НЕДЕЛЬНЫЕ ЧЕЛЛЕНДЖИ С ДРУГОМ (пром: соц. механика
    # поверх уже существующей рефералки) ----------------
    # Один ряд = один челлендж между двумя людьми на start_day..end_day
    # (обычно 7 дней). Прогресс каждого считается на лету из calendar
    # (день "активен", если completed > 0), а не хранится отдельно —
    # меньше состояния для рассинхрона.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS challenges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        partner_id INTEGER NOT NULL,
        start_day TEXT NOT NULL,
        end_day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Журнал удалений привычек (пром 10.2) — нужен для анти-абузной блокировки
    # добавления новых привычек: если сегодня уже была отметка выполнения и
    # сегодня же что-то удалили, это похоже на попытку накрутить Adam Coin
    # (закрыть → удалить → добавить новую → закрыть...), поэтому добавление
    # новых привычек блокируется до сброса в 00:00. См. db/habits.py.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_deletions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habit_deletions_user ON habit_deletions(user_id, day)"
    )

    # Сколько отметок привычек за локальный день уже «засчитано» пользователю
    # (n растёт на каждую отметку, удаление привычки его не уменьшает). Монеты
    # платят только за первые MAX_REWARDED_COMPLETIONS_PER_DAY — так накрутку
    # «отметить → удалить → добавить → отметить...» гасит потолок наград, а не
    # запрет добавлять привычки. См. db/habits.py::_claim_reward_slot.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_reward_days(
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        n INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )
    """)

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

    # Журнал похвал за второстепенные задачи плана дня (пром 7.1) — нужен,
    # чтобы короткие поощрения не повторялись в течение дня, а в первые
    # 3 дня/15 отметок не повторялись вовсе. См. db/task_praise.py.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS secondary_task_praise_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message_key TEXT NOT NULL,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_secondary_task_praise_user ON secondary_task_praise_log(user_id, day)"
    )

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
    if "daily_limit_per_user" not in shop_columns:
        # Пром 9: пакеты доп. ответов ADAM можно купить не больше N раз в
        # день (0 = без ограничения) — иначе можно было бы бесконечно
        # докупать лимит запросов за Adam Coin.
        cursor.execute("ALTER TABLE shop_items ADD COLUMN daily_limit_per_user INTEGER DEFAULT 0")

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
            (20, "💬 +5 ответов ADAM", "5 дополнительных ответов ADAM к вашему текущему лимиту", 100),
            (21, "💬 +20 ответов ADAM", "20 дополнительных ответов ADAM к вашему текущему лимиту", 300),
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

    # ---------------- РЕАЛЬНЫЙ РАСХОД ТОКЕНОВ LLM ----------------
    # В отличие от ai_quota (которая считает только ручной чат ADAM и
    # используется для лимита конкретного пользователя), эта таблица
    # пишется из ЕДИНОЙ точки всех вызовов LLM — multi_agent.py::_ask —
    # и поэтому покрывает вообще всё: чат, "Совет дня", утренние
    # сообщения, еженедельный разбор, анализ анкеты онбординга и т.д.
    # Именно эти автоматические напоминания раньше нигде не считались.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_token_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        tokens INTEGER DEFAULT 0,
        provider TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_token_log_day ON ai_token_log(day)")

    # Косметика профиля: рамки за Adam Coin. Повторный INSERT безопасен для существующей БД.
    cursor.execute("UPDATE shop_items SET item_type='premium' WHERE id=1")
    cursor.execute("UPDATE shop_items SET item_type='theme' WHERE id=2")
    cursor.execute("UPDATE shop_items SET item_type='badge' WHERE id=3")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (4,'🧑‍🚀 Аватар: ADAM','Аватар профиля',250,'avatar','adam',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (5,'🪐 Рамка: Neon','Рамка аватара',200,'frame','neon',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (6,'✨ Рамка: Gold','Рамка аватара',350,'frame','gold',0)")
    # Свежая БД: первый сид выше заводит товары 4–6 без типа и payload, а
    # INSERT OR IGNORE их уже не обновляет — рамки Neon/Gold и аватар не
    # опознавались как рамка/аватар (нет «Надеть», рамка не попадала в выбор).
    # На рабочей базе, где всё уже верно, эти UPDATE ничего не меняют.
    cursor.execute("UPDATE shop_items SET item_type='avatar', payload='adam' WHERE id=4 AND item_type='cosmetic' AND COALESCE(payload,'')=''")
    cursor.execute("UPDATE shop_items SET item_type='frame', payload='neon' WHERE id=5 AND item_type='cosmetic' AND COALESCE(payload,'')=''")
    cursor.execute("UPDATE shop_items SET item_type='frame', payload='gold' WHERE id=6 AND item_type='cosmetic' AND COALESCE(payload,'')=''")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (7,'👑 Рамка: Double Gold','Платная премиальная рамка с двойной позолотой и подсветкой',299,'frame_stars','paid_double_gold',0)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (20,'💬 +5 ответов ADAM','5 дополнительных ответов ADAM к вашему текущему лимиту',100,'answer_pack','5',1)")
    cursor.execute("INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) VALUES (21,'💬 +20 ответов ADAM','20 дополнительных ответов ADAM к вашему текущему лимиту',300,'answer_pack','20',1)")

    # Пром 9: пересборка экономики AI-запросов — обычный пакет за Adam Coin
    # даёт +10 (было +5), и оба пакета за монеты теперь ограничены одной
    # покупкой в день каждый. Плюс два новых пакета покрупнее — уже только
    # за Telegram Stars (реальные деньги), тоже по одному разу в день.
    # ВАЖНО: цены в Stars ниже — плейсхолдер, требуют финального ревью
    # (курс Stars→USD задаёт Telegram и меняется).
    cursor.execute("""
        UPDATE shop_items
        SET name='💬 +10 ответов ADAM',
            description='10 дополнительных ответов ADAM к вашему текущему лимиту',
            payload='10', item_type='answer_pack', repeatable=1
        WHERE id=20
    """)
    cursor.execute("UPDATE shop_items SET item_type='answer_pack', repeatable=1 WHERE id=21")
    cursor.execute("UPDATE shop_items SET daily_limit_per_user=1 WHERE id IN (20,21)")
    cursor.execute("""
        INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable,daily_limit_per_user)
        VALUES (22,'💬 +50 ответов ADAM','50 дополнительных ответов ADAM — оплата Telegram Stars',150,'answer_pack_stars','50',1,1)
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable,daily_limit_per_user)
        VALUES (23,'💬 +100 ответов ADAM','100 дополнительных ответов ADAM — оплата Telegram Stars',280,'answer_pack_stars','100',1,1)
    """)
    # Roadmap #32 — разовый бустер x2 Adam Coin на N часов, за Telegram
    # Stars. payload — длительность в часах. Повторная покупка ПРОДЛЕВАЕТ
    # окно (см. handlers/payments.py), поэтому лимит в день не нужен.
    cursor.execute("""
        INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable,daily_limit_per_user)
        VALUES (24,'⚡ Бустер x2 Adam Coin — 24ч','Все привычки следующие 24 часа приносят вдвое больше Adam Coin',99,'booster_stars','24',1,0)
    """)
    # Roadmap #15 — анимированные рамки за Adam Coin (в отличие от
    # frame_stars id=7 — та единственная существующая анимированная рамка
    # раньше была только премиальной, за Stars).
    cursor.execute(
        "INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) "
        "VALUES (25,'🌈 Рамка: Радуга','Анимированная переливающаяся рамка аватара',450,'frame','rainbow',0)"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable) "
        "VALUES (26,'💜 Рамка: Пульс','Анимированная пульсирующая рамка аватара',280,'frame','pulse_violet',0)"
    )

    # Алмазы — премиальная валюта, которую нельзя заработать обычными
    # действиями (см. диздок-комментарий у колонки users.diamonds выше),
    # только купить за реальные деньги (Telegram Stars) или получить в
    # награду за идеальный месяц серии. Это сама покупка — правила, на что
    # алмазы тратятся, продумаем позже, сейчас важно включить сам вход
    # денег. Без дневного лимита (daily_limit_per_user=0) — в отличие от
    # пакетов ответов ADAM, здесь ограничивает только кошелёк покупателя,
    # а не риск абуза лимита AI-запросов.
    # ВАЖНО: цены в Stars ниже — плейсхолдер, требуют финального ревью
    # (курс Stars→USD задаёт Telegram и меняется), как и остальные
    # Stars-цены выше.
    cursor.execute(
        "INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable,daily_limit_per_user) "
        "VALUES (27,'💎 10 алмазов','Премиальная валюта — оплата Telegram Stars',150,'diamond_pack_stars','10',1,0)"
    )
    cursor.execute(
        "INSERT OR IGNORE INTO shop_items(id,name,description,price,item_type,payload,repeatable,daily_limit_per_user) "
        "VALUES (28,'💎 50 алмазов','Премиальная валюта — оплата Telegram Stars',600,'diamond_pack_stars','50',1,0)"
    )

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

    # ---------------- ПОДАРКИ ОТ АДМИНИСТРАТОРА ----------------
    # Медаль выдаёт только админ (db/admin_gifts.py); запись нужна, чтобы человек увидел
    # окно «Эксклюзивный подарок» при следующем входе, а не только пуш.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_gifts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL DEFAULT 'badge',
        item_id INTEGER,
        note TEXT,
        seen INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        seen_at TIMESTAMP
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_gifts_user ON admin_gifts(user_id, seen)")

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
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        proactive_topic TEXT DEFAULT '',
        proactive_until TIMESTAMP
    )
    """)
    profile_columns = {row[1] for row in cursor.execute("PRAGMA table_info(user_ai_profile)").fetchall()}
    if "proactive_topic" not in profile_columns:
        cursor.execute("ALTER TABLE user_ai_profile ADD COLUMN proactive_topic TEXT DEFAULT ''")
    if "proactive_until" not in profile_columns:
        cursor.execute("ALTER TABLE user_ai_profile ADD COLUMN proactive_until TIMESTAMP")

    # ---------------- AI КЭШ ОТВЕТОВ ----------------
    # Кэш финальных ответов на простые/повторяющиеся сообщения ("привет",
    # "спасибо" и т.п.) — этап 4 "Оптимизация": меньше запросов к OpenAI,
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
        completed INTEGER DEFAULT 0,
        skipped INTEGER DEFAULT 0
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habit_logs_user_day "
        "ON habit_logs(user_id, day)"
    )
    cursor.execute("PRAGMA table_info(habit_logs)")
    habit_logs_columns = {row[1] for row in cursor.fetchall()}
    if "skipped" not in habit_logs_columns:
        # Осознанный пропуск (skip_habit, см. db/habits.py) не должен
        # засчитываться как "провал" в еженедельном AI-разборе — без этой
        # колонки не отличить "забыл" от "пропустил по уважительной причине".
        cursor.execute("ALTER TABLE habit_logs ADD COLUMN skipped INTEGER DEFAULT 0")

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

    # ---------------- Roadmap #3: заметка/фото к выполненной привычке ----------------
    # Одна запись на привычку в день (UNIQUE) — повторное сохранение в тот
    # же день просто перезаписывает (INSERT ... ON CONFLICT DO UPDATE, см.
    # db/habits.py::add_habit_note). photo_data_url — сжатая на клиенте
    # картинка как data: URL прямо в БД (без внешнего файлового хранилища).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_notes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        habit_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        note TEXT,
        photo_data_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(habit_id, day)
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habit_notes_user_day ON habit_notes(user_id, day)"
    )

    # ---------------- Roadmap #23/#36: AI сам подбирает время напоминания ----------------
    # Отдельный журнал МОМЕНТОВ выполнения (в отличие от habit_logs — там
    # только "выполнено да/нет за день", без времени суток) — на нём считаем
    # типичный час выполнения конкретной привычки, см. db/insights.py.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS habit_completion_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        habit_id INTEGER NOT NULL,
        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habit_completion_events_habit ON habit_completion_events(habit_id)"
    )

    # ---------------- Roadmap #12: ежедневные микро-квесты ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_quests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        quest_key TEXT NOT NULL,
        title TEXT NOT NULL,
        target INTEGER DEFAULT 1,
        progress INTEGER DEFAULT 0,
        reward_coins INTEGER DEFAULT 5,
        claimed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, day, quest_key)
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_quests_user_day ON daily_quests(user_id, day)"
    )

    # ---------------- Roadmap #19: реакции/стикеры поддержки другу ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS friend_reactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        emoji TEXT NOT NULL,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(from_user_id, to_user_id, day)
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_friend_reactions_to ON friend_reactions(to_user_id)"
    )

    # ---------------- Друзья и «Напомнить друзьям» (db/friends.py) ----------------
    # friendships — симметричная: на пару (A, B) две строки (A→B и B→A),
    # поэтому «друзья пользователя» — один простой SELECT по user_id.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS friendships(
        user_id INTEGER NOT NULL,
        friend_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, friend_id)
    )
    """)
    # day — локальный день ПОЛУЧАТЕЛЯ: «раз в день на пару» и «не больше N в
    # день одному человеку» считаются по его календарю, а не отправителя.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS friend_nudges(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(from_user_id, to_user_id, day)
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_friend_nudges_to_day ON friend_nudges(to_user_id, day)"
    )
    # Окно «Напомнить друзьям» после отметки привычки — раз в день.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS friend_remind_prompts(
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        PRIMARY KEY(user_id, day)
    )
    """)

    # ---------------- Задания месяца (db/month_quests.py) ----------------
    # Открытые сундуки месяца: одна запись на (пользователь, месяц, сундук) —
    # PRIMARY KEY не даёт открыть один и тот же дважды.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS month_chests(
        user_id INTEGER NOT NULL,
        month_key TEXT NOT NULL,
        milestone INTEGER NOT NULL,
        coins INTEGER DEFAULT 0,
        diamonds INTEGER DEFAULT 0,
        claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(user_id, month_key, milestone)
    )
    """)

    # ---------------- Парное задание (db/pair_quests.py) ----------------
    # status: pending (приглашение) | active (принято: start_day..end_day) |
    # completed (цель набрана) | expired | declined | cancelled. *_claimed_day —
    # когда участник открыл сундук (NULL — ещё нет).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pair_quests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER NOT NULL,
        invitee_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        goal INTEGER NOT NULL,
        start_day TEXT,
        end_day TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        accepted_at TIMESTAMP,
        finished_at TIMESTAMP,
        inviter_claimed_day TEXT,
        invitee_claimed_day TEXT
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pair_quests_inviter ON pair_quests(inviter_id, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pair_quests_invitee ON pair_quests(invitee_id, status)"
    )
    # Одно ожидающее приглашение на человека — гарантия на уровне БД, чтобы два
    # одновременных нажатия не создали два приглашения.
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pair_quests_one_invite ON pair_quests(inviter_id) WHERE status='pending'"
    )
    # Пуш «напарник отметился» — не чаще раза в день на получателя и задание.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pair_quest_pings(
        quest_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        PRIMARY KEY(quest_id, to_user_id, day)
    )
    """)

    # ---------------- Эволюции ADAM (db/evolution.py) ----------------
    # seen_level — до какой эволюции игрок уже видел праздник «EVOLUTION N
    # UNLOCKED». Сами эволюции нигде не хранятся: они считаются по лучшей серии.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hero_evolution(
        user_id INTEGER PRIMARY KEY,
        seen_level INTEGER NOT NULL DEFAULT 0
    )
    """)

    # ---------------- Подарки друзьям (db/gifts.py) ----------------
    # Журнал подарков: он же основа суточного лимита отправителя. price —
    # сколько Adam Coin отправитель заплатил (0 для алмазов).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS gifts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user_id INTEGER NOT NULL,
        to_user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        item_id INTEGER,
        amount INTEGER DEFAULT 1,
        price INTEGER DEFAULT 0,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gifts_from_day ON gifts(from_user_id, day)"
    )
    # seen — видел ли получатель подарок внутри приложения (экран «Мне
    # подарили»); 0 по умолчанию, поэтому подарки, отправленные до появления
    # экрана, тоже один раз покажутся как новые.
    cursor.execute("PRAGMA table_info(gifts)")
    if "seen" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE gifts ADD COLUMN seen INTEGER DEFAULT 0")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_gifts_to_seen ON gifts(to_user_id, seen)"
    )

    # ---------------- Подписки (db/follows.py) — как в Duolingo ----------------
    # follower_id подписан на followee_id. Друзья = ВЗАИМНАЯ подписка (обе
    # строки). Ссылка «Добавить друга» создаёт обе строки сразу.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS follows(
        follower_id INTEGER NOT NULL,
        followee_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(follower_id, followee_id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_follows_followee ON follows(followee_id)")
    # Первая версия дружбы (friendships — две строки на пару) — это уже две
    # взаимные подписки: переносим один раз и очищаем старую таблицу, иначе
    # при следующем старте отписавшийся «воскресал» бы из неё.
    cursor.execute("""
        INSERT OR IGNORE INTO follows(follower_id, followee_id, created_at)
        SELECT user_id, friend_id, created_at FROM friendships
    """)
    cursor.execute("DELETE FROM friendships")
    # Пуш «подписался на тебя» уходит один раз на пару — иначе подписка/
    # отписка по кругу превращалась бы в спам получателю.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS follow_notices(
        follower_id INTEGER NOT NULL,
        followee_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(follower_id, followee_id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_blocks(
        blocker_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(blocker_id, blocked_id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        comment TEXT,
        day TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(reporter_id, target_id, day)
    )
    """)

    # ---------------- Roadmap #41: feature flags ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feature_flags(
        key TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        rollout_pct INTEGER DEFAULT 100,
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- Roadmap #9: сезонные награды лиги ----------------
    # Сезон = календарный месяц (тот же ритм, что и у «Заданий месяца»,
    # см. db/month_quests.py) — рейтинг сезона считается
    # "на лету" суммой gained_xp из statistics за текущий месяц, отдельного
    # счётчика заводить не нужно; здесь только факт "награда за сезон уже
    # выдана" — чтобы не выдать повторно.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS season_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        season_key TEXT NOT NULL,
        rank INTEGER NOT NULL,
        coins INTEGER DEFAULT 0,
        diamonds INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, season_key)
    )
    """)

    # ---------------- Roadmap #11: виртуальный питомец ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS virtual_pets(
        user_id INTEGER PRIMARY KEY,
        care_points INTEGER DEFAULT 0,
        last_fed_day TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- Roadmap #16: групповые челленджи (команды) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teams(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        invite_code TEXT UNIQUE NOT NULL,
        created_by INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS team_members(
        team_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(team_id, user_id)
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id)"
    )

    # ---------------- Roadmap #18: лента активности друзей ----------------
    # "Друзья" здесь — участники твоей команды (#16) + все, с кем ты хоть
    # раз обменялся реакцией (#19) — без отдельной системы заявок в друзья,
    # которая была бы совсем новой, никем не просимой веткой поверх и так
    # большого пакета.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_activity_events_user_created ON activity_events(user_id, created_at)"
    )

    # ---------------- Журнал отправленных уведомлений (для истории у пользователя) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notification_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT,
        title TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_notification_log_user_sent ON notification_log(user_id, sent_at)"
    )

    # ---------------- Roadmap #46: язык интерфейса ----------------
    if "language" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'ru'")

    # ---------------- Улучшение #49: личный рекорд серии ----------------
    # Бэкфилл best_streak = текущий streak на существующих пользователях —
    # безопасное начальное значение: если бы колонка существовала с самого
    # начала, best_streak всегда был бы >= текущего streak (обновляется через
    # MAX() в db/streak.py::register_completion). Без бэкфилла первое
    # завершение привычки после деплоя всё равно сразу выставило бы то же
    # самое через MAX(0, streak) — бэкфилл просто убирает один "пустой" шаг.
    if "best_streak" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN best_streak INTEGER DEFAULT 0")
        cursor.execute("UPDATE users SET best_streak = streak")

    # ---------------- Улучшение #40: "тебя обогнали в рейтинге" ----------------
    # Снимок последнего известного места в сезонном рейтинге на пользователя —
    # без него не с чем сравнивать текущее место, чтобы понять, ухудшилось ли
    # оно с прошлой проверки (см. streak_scheduler.run_rank_overtaken_notifications).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS season_rank_snapshot(
        user_id INTEGER PRIMARY KEY,
        season_key TEXT NOT NULL,
        rank INTEGER NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- Улучшение #70: логирование клиентских JS-ошибок ----------------
    # Раньше единственный способ узнать про JS-краш у реального пользователя —
    # попросить прислать видео/скриншот консоли вручную. Теперь window.onerror
    # / unhandledrejection на фронте шлют сюда, админ видит в /api/admin/client-errors.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS client_errors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        stack TEXT,
        url TEXT,
        user_agent TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_client_errors_created ON client_errors(created_at)"
    )
    # Раньше в url писался полный location.href — вместе с #tgWebAppData=
    # (подписанные initData пользователя). Новые записи чистятся при вставке
    # (db/client_errors.py), здесь — разовая зачистка уже сохранённых.
    cursor.execute(
        "UPDATE client_errors SET url = substr(url, 1, instr(url, '#') - 1) "
        "WHERE url LIKE '%#%'"
    )

    # ---------------- Улучшение #4 (фидбек): пол для согласования обращения ----------------
    # 'm'/'f'/NULL (не определён/не задан явно). NULL — не то же самое, что
    # "неизвестно навсегда": db.users.get_gender() при NULL пробует угадать
    # по имени (guess_gender_from_name), но это именно ДОГАДКА для текста
    # уведомлений — explicit_gender ниже отличает "пользователь сам выбрал"
    # от "мы угадали", чтобы явный выбор в настройках никогда не перезаписался.
    if "gender" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN gender TEXT")
    if "gender_explicit" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN gender_explicit INTEGER DEFAULT 0")

    # ---------------- Идемпотентность платежей Telegram Stars ----------------
    # Telegram может редоставить update с successful_payment повторно (сбой
    # сети, рестарт бота между получением апдейта и обработкой) — без этой
    # таблицы handlers/payments.py начислил бы награду дважды за одну и ту
    # же оплату. telegram_payment_charge_id уникален для каждой реальной
    # транзакции Stars — UNIQUE ловит повтор на уровне БД, даже если сама
    # проверка в коде почему-то будет пропущена.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stars_payments(
        charge_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        payload TEXT,
        amount INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ---------------- «Что нового» (changelog внутри Mini App) ----------------
    # Раньше об обновлениях узнавали только по факту (или никак) — теперь
    # каждая запись здесь показывается пользователю один раз модальным окном
    # при следующем открытии Mini App (см. db/changelog.py,
    # webapp_server.py /api/changelog/*). last_seen_changelog_id — ID
    # последней увиденной записи, а не TIMESTAMP: секундная точность
    # CURRENT_TIMESTAMP в SQLite создавала реальный race condition (запись,
    # добавленная в ту же секунду, что и отметка "просмотрено", терялась бы
    # или наоборот показывалась повторно). Целочисленный ID монотонен и
    # такой гонки не имеет. DEFAULT 0 (а не NULL) — у всех существующих
    # пользователей на момент этой миграции корректно означает "не видел
    # ни одной из уже существующих записей" (id начинаются с 1).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS changelog_entries(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    if "last_seen_changelog_id" not in users_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_seen_changelog_id INTEGER DEFAULT 0")

    # ---------------- Телеметрия долгих кадров (Mini App) ----------------
    # Пользователи жалуются на "лаги/чернеет при прокрутке" в Telegram
    # WebView на Android — этот класс багов физически невозможно
    # воспроизвести или отладить с обычного десктопа: движок другой,
    # железо другое, и по видео можно только гадать. app.js меряет
    # реальные разрывы между кадрами (через requestAnimationFrame) и
    # длинные JS-таски (PerformanceObserver longtask) на устройстве
    # пользователя и шлёт сюда — так у разработчика появляются точные
    # цифры без переписки "у меня лагает" / "а на каком кадре".
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS perf_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        tab TEXT,
        path TEXT,
        is_scrolling INTEGER DEFAULT 0,
        device_info TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_perf_events_created ON perf_events(created_at)"
    )

    # habits.user_id — фильтр почти в каждой per-user джобе напоминаний
    # (get_habits/get_incomplete_habits/get_progress), индекса не было.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_habits_user ON habits(user_id)"
    )

    # ---------------- «Вознаградите себя» (db/self_rewards.py) ----------------
    # Простая отметка "я себя порадовал(а)" за фиксированную цену в Adam
    # Coin (xp) — отдельная таблица, а не shop_items, потому что это не
    # покупка вещи, а история с необязательной заметкой (чем себя
    # вознаградил), которую пользователь смотрит через неделю-другую.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS self_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        note TEXT,
        cost INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_self_rewards_user ON self_rewards(user_id, created_at)"
    )

    conn.commit()
    conn.close()

    # db/streak.py::ensure_tables() создаёт свои таблицы отдельным
    # подключением и раньше нигде централизованно не вызывалась — вместо
    # этого 16 разных функций в streak.py вызывали её сами на каждый вызов
    # "на всякий случай". Из-за этого каждый per-user проход в джобах
    # напоминаний (контрольные точки привычек, реэнгеймент и т.д.), которые
    # тикают каждую минуту, делал лишний CREATE TABLE/PRAGMA/CREATE INDEX
    # проход на КАЖДОГО пользователя — то есть бот синхронно "подвисал" на
    # это в общем event loop (тот же луп, что обрабатывает сообщения
    # пользователей). Вызываем один раз при старте, как остальные таблицы;
    # локальный импорт — чтобы не завести цикл core->streak->core, streak.py
    # сам делает "from .core import connect" на уровне модуля.
    from .streak import ensure_tables as _ensure_streak_tables
    _ensure_streak_tables()
