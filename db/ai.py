from .core import connect

CACHE_TTL_HOURS = 12   # сколько хранится закэшированный ответ
CACHE_MAX_AGE_HOURS = 48  # старше этого — чистим из таблицы при записи


def add_ai_message(user_id, role, message):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ai_messages(user_id, role, message) VALUES (?, ?, ?)
    """, (user_id, role, message))
    message_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return message_id


def get_ai_history(user_id, limit=20):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM ai_messages WHERE user_id=? ORDER BY id DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return list(reversed(rows))


def clear_ai_history(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ai_messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def save_ai_feedback(message_id, user_id, rating):
    """rating: 'up' или 'down'. Повторное нажатие меняет оценку, а не дублирует её."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ai_feedback(message_id, user_id, rating)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id, user_id) DO UPDATE SET
            rating = excluded.rating,
            created_at = CURRENT_TIMESTAMP
    """, (message_id, user_id, rating))
    conn.commit()
    conn.close()


def get_ai_feedback_stats():
    """Сводка по оценкам ответов AI — для админ-панели: сколько 👍/👎 и доля
    положительных, чтобы было видно, влияет ли фидбек хоть на что-то."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT rating, COUNT(*) as cnt FROM ai_feedback GROUP BY rating
    """)
    rows = cursor.fetchall()
    conn.close()

    counts = {"up": 0, "down": 0}
    for row in rows:
        if row["rating"] in counts:
            counts[row["rating"]] = row["cnt"]

    total = counts["up"] + counts["down"]
    positive_share = round(counts["up"] / total * 100) if total else None

    return {
        "up": counts["up"],
        "down": counts["down"],
        "total": total,
        "positive_share": positive_share,
    }


def save_feedback_reason(message_id, user_id, reason):
    """Причина дизлайка (выбрана кнопкой) — записывается в уже существующую
    строку ai_feedback, созданную save_ai_feedback()."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE ai_feedback SET reason=? WHERE message_id=? AND user_id=?
    """, (reason, message_id, user_id))
    conn.commit()
    conn.close()


def get_recent_negative_reasons(user_id, limit=3):
    """Последние причины дизлайков пользователя — используется, чтобы AI
    не повторял одни и те же ошибки (этап 2: 'обучение по 👍👎')."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT reason FROM ai_feedback
        WHERE user_id=? AND rating='down' AND reason IS NOT NULL
        ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [row["reason"] for row in rows]


# =====================================
# ДОЛГОСРОЧНАЯ ПАМЯТЬ О ПОЛЬЗОВАТЕЛЕ
# =====================================

def get_user_profile(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_ai_profile WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def update_user_profile(user_id, summary):
    """Перезаписывает краткий профиль и сбрасывает счётчик сообщений."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_ai_profile(user_id, summary, message_count, updated_at)
        VALUES (?, ?, 0, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            summary = excluded.summary,
            message_count = 0,
            updated_at = CURRENT_TIMESTAMP
    """, (user_id, summary))
    conn.commit()
    conn.close()


def bump_profile_counter(user_id):
    """Увеличивает счётчик сообщений с последнего обновления профиля и
    возвращает новое значение — хендлер решает, пора ли пересобрать профиль."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_ai_profile(user_id, message_count)
        VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET message_count = message_count + 1
    """, (user_id,))
    conn.commit()
    cursor.execute("SELECT message_count FROM user_ai_profile WHERE user_id=?", (user_id,))
    count = cursor.fetchone()["message_count"]
    conn.close()
    return count


# =====================================
# КЭШ ОТВЕТОВ (для простых/повторяющихся сообщений)
# =====================================

def cache_get(key):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT answer FROM ai_response_cache
        WHERE cache_key=? AND created_at >= datetime('now', ?)
    """, (key, f"-{CACHE_TTL_HOURS} hours"))
    row = cursor.fetchone()
    conn.close()
    return row["answer"] if row else None


def cache_set(key, answer):
    conn = connect()
    cursor = conn.cursor()
    # Заодно подчищаем совсем старые записи, чтобы таблица не росла бесконечно.
    cursor.execute(
        "DELETE FROM ai_response_cache WHERE created_at < datetime('now', ?)",
        (f"-{CACHE_MAX_AGE_HOURS} hours",)
    )
    cursor.execute("""
        INSERT INTO ai_response_cache(cache_key, answer, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cache_key) DO UPDATE SET
            answer = excluded.answer,
            created_at = CURRENT_TIMESTAMP
    """, (key, answer))
    conn.commit()
    conn.close()


# =====================================
# МОНИТОРИНГ ОШИБОК
# =====================================

def log_error(scope, error, user_id=None):
    """scope — короткий тег места ошибки ('ai_pipeline', 'daily_tip', ...)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO error_log(scope, error, user_id) VALUES (?, ?, ?)
    """, (scope, str(error)[:500], user_id))
    conn.commit()
    conn.close()


def get_error_stats(hours=24, limit=5):
    """Сводка ошибок за последние N часов — для админ-панели."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT scope, COUNT(*) as cnt FROM error_log
        WHERE created_at >= datetime('now', ?)
        GROUP BY scope ORDER BY cnt DESC
    """, (f"-{hours} hours",))
    by_scope = [dict(row) for row in cursor.fetchall()][:limit]

    cursor.execute("""
        SELECT COUNT(*) as cnt FROM error_log WHERE created_at >= datetime('now', ?)
    """, (f"-{hours} hours",))
    total = cursor.fetchone()["cnt"]

    conn.close()
    return {"total": total, "by_scope": by_scope, "hours": hours}
