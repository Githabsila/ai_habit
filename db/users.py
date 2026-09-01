from datetime import date

from .core import connect


# =====================================
# ПОЛЬЗОВАТЕЛИ
# =====================================

def survey_variant(telegram_id):
    """A/B-вариант вступительного текста анкеты — чисто детерминированная
    функция от telegram_id (чётность), без отдельного хранения в БД: тот
    же пользователь всегда получает тот же вариант, а долю по варианту
    можно посчитать прямо в SQL через `telegram_id % 2` (см. db/analytics.py
    get_survey_funnel_by_variant) — не нужна ни миграция, ни бэкфилл для
    уже существующих пользователей."""
    return "B" if telegram_id % 2 else "A"

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

    _ensure_admin_premium(telegram_id)


def should_show_app_tour(user_id):
    user = get_user(user_id)
    if not user or "app_tour_seen" not in user.keys():
        return False
    return not bool(user["app_tour_seen"])


def mark_app_tour_seen(user_id):
    conn = connect()
    conn.execute("UPDATE users SET app_tour_seen=1 WHERE telegram_id=?", (user_id,))
    conn.commit()
    conn.close()


def _ensure_admin_premium(telegram_id):
    """Администраторы бота (config.ADMIN_IDS) всегда получают Premium
    навсегда, без покупки — вызывается при каждом add_user (idempotent),
    так что действует и для уже существующих админов, и для тех, кого
    добавят в ADMIN_IDS позже."""
    try:
        from config import ADMIN_IDS
    except Exception:
        return
    if telegram_id in ADMIN_IDS:
        give_premium_admin(telegram_id)


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
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET last_ai_message_at=CURRENT_TIMESTAMP WHERE telegram_id=?",
            (user_id,)
        )
        conn.commit()
    finally:
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
# Roadmap #39 — архетип личности (короткий тест, 1 раз или пересдать)
# =====================================
ARCHETYPES = {
    "strategist": "🎯 Стратег",
    "marathoner": "🧗 Марафонец",
    "sprinter": "🏃 Спринтер",
    "explorer": "🔭 Исследователь",
}


def set_archetype(user_id, archetype_key):
    if archetype_key not in ARCHETYPES:
        return False
    conn = connect()
    conn.execute("UPDATE users SET archetype=? WHERE telegram_id=?", (archetype_key, user_id))
    conn.commit()
    conn.close()
    return True


# =====================================
# Roadmap #25 — долгосрочные жизненные цели для AI-наставника
# =====================================
# Отдельно от разовой анкеты онбординга (user_survey.life_goal, см.
# db/onboarding.py) — тот текст задаётся один раз при первом входе и не
# у всех пользователей вообще есть (для тех, кто был в БД до анкеты).
# long_term_goals пользователь может завести/переписать в любой момент из
# настроек, и он всегда есть на колонке users (без зависимости от
# отдельной строки user_survey) — см. build_user_context/
# build_proactive_context в webapp/services/ai_utils.py, куда это
# подмешивается в контекст AI-наставника.
MAX_LONG_TERM_GOALS_LENGTH = 500


def set_long_term_goals(user_id, text):
    text = (text or "").strip()[:MAX_LONG_TERM_GOALS_LENGTH]
    conn = connect()
    conn.execute(
        "UPDATE users SET long_term_goals=? WHERE telegram_id=?",
        (text or None, user_id),
    )
    conn.commit()
    conn.close()
    return text


def get_long_term_goals(user_id):
    user = get_user(user_id)
    if not user or "long_term_goals" not in user.keys():
        return None
    return user["long_term_goals"]


# =====================================
# Roadmap #32 — разовый бустер x2 Adam Coin за Telegram Stars
# =====================================

def activate_xp_booster(user_id, hours):
    """Продлевает окно x2 Adam Coin — повторная покупка ДОБАВЛЯЕТ время
    поверх уже активного окна (а не просто переустанавливает его), а не
    начинает 24ч заново с текущего момента, если предыдущий бустер ещё не
    истёк — иначе покупка про запас была бы невыгодной."""
    from datetime import datetime, timezone, timedelta
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT bonus_2x_xp_until FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_until = None
    if row and row["bonus_2x_xp_until"]:
        try:
            current_until = datetime.fromisoformat(str(row["bonus_2x_xp_until"]))
        except ValueError:
            current_until = None
    base = current_until if (current_until and current_until > now) else now
    new_until = base + timedelta(hours=hours)
    cursor.execute(
        "UPDATE users SET bonus_2x_xp_until=? WHERE telegram_id=?",
        (new_until.isoformat(), user_id),
    )
    conn.commit()
    conn.close()
    return new_until


def is_xp_booster_active(user_id):
    from datetime import datetime, timezone
    user = get_user(user_id)
    if not user or "bonus_2x_xp_until" not in user.keys() or not user["bonus_2x_xp_until"]:
        return False
    try:
        until = datetime.fromisoformat(str(user["bonus_2x_xp_until"]))
    except ValueError:
        return False
    return until > datetime.now(timezone.utc).replace(tzinfo=None)


# =====================================
# Adam Coin
# =====================================

def add_xp(user_id, amount):
    """Единая точка начисления опыта. total_xp — весь опыт, заработанный
    за всё время, от него считается level (растёт бесконечно, потолка
    нет). xp — тратимая валюта ("Adam Coin"), уменьшается в магазине
    (db/shop.py), но на level больше не влияет — иначе покупка предмета
    отбрасывала бы игрока на уровень назад."""
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT total_xp FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return

    total_xp = (user["total_xp"] or 0) + amount
    level = total_xp // 100 + 1

    cursor.execute("""
        UPDATE users SET xp = xp + ?, total_xp=?, level=? WHERE telegram_id=?
    """, (amount, total_xp, level, user_id))

    conn.commit()
    conn.close()


def give_xp_admin(user_id, xp):
    add_xp(user_id, xp)


# =====================================
# АЛМАЗЫ (пром 8, доп.) — премиальная валюта: только за деньги/Stars или
# небольшая награда за идеальный месяц серии 2+ привычек.
# =====================================

def add_diamonds(user_id, amount):
    conn = connect()
    conn.execute("UPDATE users SET diamonds = diamonds + ? WHERE telegram_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def get_diamonds(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT diamonds FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return int(row["diamonds"] or 0) if row else 0


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
        SET xp=0, total_xp=0, level=1, streak=0, total_completed=0, last_completed=NULL
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


def get_referred_users(user_id):
    """Список тех, кого пригласил user_id — в отличие от get_referrals()
    (просто счётчик), нужен, чтобы предложить кого-то из уже приглашённых
    друзей для недельного челленджа (db/challenges.py)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT telegram_id, username, first_name FROM users WHERE referrer_id=? ORDER BY id DESC",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


# =====================================
# РЕЙТИНГ (menu.py / rating.py)
# =====================================

def get_rating(limit=10):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT telegram_id, username, first_name, xp, level, streak, avatar_id, frame_id, total_xp
        FROM users
        WHERE banned=0
        ORDER BY streak DESC, xp DESC
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
