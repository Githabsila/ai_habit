from datetime import date

from .core import connect


# =====================================
# ПОЛЬЗОВАТЕЛИ
# =====================================

def add_user(telegram_id, username, first_name):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR IGNORE INTO users(telegram_id, username, first_name)
        VALUES (?, ?, ?)
    """, (telegram_id, username, first_name))

    cursor.execute("""
        INSERT OR IGNORE INTO settings(user_id)
        VALUES(?)
    """, (telegram_id,))

    conn.commit()
    conn.close()

    # Сразу создаём задания на сегодня, чтобы новый пользователь
    # не видел "задания ещё не созданы" до полуночи.
    # Импорт внутри функции — чтобы избежать циклического импорта
    # с db.daily_tasks (которому, в свою очередь, нужен add_xp отсюда).
    from .daily_tasks import create_daily_tasks
    create_daily_tasks(telegram_id)


def get_user(telegram_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
    user = cursor.fetchone()

    conn.close()
    return user


def get_users_count():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]

    conn.close()
    return count


def get_all_users():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users ORDER BY id")
    users = cursor.fetchall()

    conn.close()
    return users


def get_all_users_info():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            telegram_id, username, first_name, xp, level,
            streak, referrals, premium, banned,
            total_completed, created_at
        FROM users
        ORDER BY id
    """)

    users = cursor.fetchall()

    conn.close()
    return users


# =====================================
# ТРОТТЛИНГ AI-ЧАТА (БД вместо in-memory — переживает рестарт)
# =====================================

def get_last_ai_message_at(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT last_ai_message_at FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["last_ai_message_at"] if row else None


def touch_last_ai_message(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_ai_message_at=CURRENT_TIMESTAMP WHERE telegram_id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()


# =====================================
# PREMIUM
# =====================================

def has_premium(user_id):
    """Активен ли premium прямо сейчас (с учётом истечения срока)."""
    _expire_premium_if_needed(user_id)
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["premium"])


def was_premium_purchased(user_id):
    """Покупал ли пользователь premium когда-либо — не сбрасывается по
    истечении срока, используется чтобы не дать купить второй раз."""
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["premium_purchased"])


def _expire_premium_if_needed(user_id):
    """Если у пользователя стоит premium=1, но срок (premium_until) уже
    прошёл — гасим флаг. Ленивая проверка при каждом обращении вместо
    фоновой задачи, чтобы не понадобился отдельный планировщик."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT premium, premium_until FROM users WHERE telegram_id=?",
        (user_id,)
    )
    row = cursor.fetchone()

    if row and row["premium"] and row["premium_until"]:
        cursor.execute(
            "UPDATE users SET premium=0 "
            "WHERE telegram_id=? AND premium=1 AND premium_until IS NOT NULL "
            "AND premium_until <= CURRENT_TIMESTAMP",
            (user_id,)
        )
        conn.commit()

    conn.close()


def give_premium(user_id, days=7):
    """Выдаёт premium на `days` дней (по умолчанию неделя) и навсегда
    отмечает, что этот пользователь его покупал."""
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET premium=1,
            premium_purchased=1,
            premium_until=datetime(CURRENT_TIMESTAMP, ? || ' days')
        WHERE telegram_id=?
    """, (f"+{days}", user_id))

    conn.commit()
    conn.close()


def give_premium_admin(user_id):
    """Ручная выдача premium админом и активация через оплату Telegram
    Stars (handlers/payments.py) — навсегда, без срока действия и без
    ограничения на повторную выдачу. Это отдельная от магазина ветка:
    неделя/1000 Adam Coin/один раз — только для покупки за Adam Coin
    в магазине (см. give_premium)."""
    conn = connect()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE users SET premium=1, premium_until=NULL WHERE telegram_id=?",
        (user_id,)
    )

    conn.commit()
    conn.close()


# =====================================
# Adam Coin
# =====================================

def add_xp(user_id, amount):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT xp FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return

    xp = user["xp"] + amount
    level = xp // 100 + 1

    cursor.execute("""
        UPDATE users SET xp=?, level=? WHERE telegram_id=?
    """, (xp, level, user_id))

    conn.commit()
    conn.close()


def give_xp_admin(user_id, xp):
    add_xp(user_id, xp)


# =====================================
# БАН
# =====================================

def ban_user(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned=1 WHERE telegram_id=?", (user_id,))
    conn.commit()
    conn.close()


def unban_user(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET banned=0 WHERE telegram_id=?", (user_id,))
    conn.commit()
    conn.close()


def is_banned(user_id):
    user = get_user(user_id)
    if not user:
        return False
    return bool(user["banned"])


# =====================================
# СБРОС ПРОГРЕССА (settings.py)
# =====================================

def reset_progress(user_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users
        SET xp=0, level=1, streak=0, total_completed=0, last_completed=NULL
        WHERE telegram_id=?
    """, (user_id,))

    cursor.execute("""
        UPDATE habits
        SET completed=0
        WHERE user_id=?
    """, (user_id,))

    conn.commit()
    conn.close()


# =====================================
# РЕФЕРАЛЫ (start.py / community.py)
# =====================================

def set_referrer(user_id, referrer_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users SET referrer_id=? WHERE telegram_id=?
    """, (referrer_id, user_id))

    conn.commit()
    conn.close()


def add_referral(referrer_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE users SET referrals = referrals + 1 WHERE telegram_id=?
    """, (referrer_id,))

    conn.commit()
    conn.close()


def get_referrals(user_id):
    user = get_user(user_id)
    if not user:
        return 0
    return user["referrals"]


# =====================================
# РЕЙТИНГ (menu.py / rating.py)
# =====================================

def get_rating(limit=10):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT telegram_id, username, first_name, xp, level, streak
        FROM users
        WHERE banned=0
        ORDER BY xp DESC
        LIMIT ?
    """, (limit,))

    data = cursor.fetchall()
    conn.close()
    return data


def get_user_rank(user_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT telegram_id
        FROM users
        WHERE banned=0
        ORDER BY xp DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    for index, row in enumerate(rows, start=1):
        if row["telegram_id"] == user_id:
            return index

    return None


# =====================================
# ЕЖЕДНЕВНЫЙ БОНУС (bonus.py)
# =====================================

def claim_daily_bonus(user_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT bonus_date FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return False

    today = str(date.today())

    if row["bonus_date"] == today:
        conn.close()
        return False

    cursor.execute("""
        UPDATE users SET bonus_date=? WHERE telegram_id=?
    """, (today, user_id))

    conn.commit()
    conn.close()

    add_xp(user_id, 20)
    return True
