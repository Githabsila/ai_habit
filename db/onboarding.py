import json

from .core import connect


# =====================================
# СТАТУС ДОСТУПА ("закрытое сообщество")
# =====================================
# Возможные значения access_status: 'new' -> 'pending' -> 'approved'
# ('new' — зарегистрировался, анкету ещё не заполнил;
#  'pending' — анкету заполнил, ждёт одобрения/автоапрува;
#  'approved' — полный доступ к боту)

def reject_user(user_id):
    """Отклоняет заявку и блокирует пользователя как спам/нежелательного."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET access_status='rejected', banned=1 WHERE telegram_id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def get_access_status(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT access_status FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row["access_status"]:
        return "approved"  # безопасный дефолт для старых строк без миграции
    return row["access_status"]


def set_access_status(user_id, status):
    conn = connect()
    cursor = conn.cursor()
    if status == "pending":
        cursor.execute("""
            UPDATE users SET access_status=?, survey_completed_at=CURRENT_TIMESTAMP
            WHERE telegram_id=?
        """, (status, user_id))
    else:
        cursor.execute(
            "UPDATE users SET access_status=? WHERE telegram_id=?",
            (status, user_id)
        )
    conn.commit()
    conn.close()


def get_pending_users(limit=15):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT telegram_id, username, first_name, survey_completed_at
        FROM users
        WHERE access_status='pending'
        ORDER BY survey_completed_at ASC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_users_pending_since(hours):
    """Пользователи в статусе pending дольше указанного числа часов —
    используется для автоодобрения по таймеру."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT telegram_id FROM users
        WHERE access_status='pending'
          AND survey_completed_at IS NOT NULL
          AND survey_completed_at <= datetime('now', ?)
    """, (f"-{hours} hours",))
    rows = cursor.fetchall()
    conn.close()
    return [row["telegram_id"] for row in rows]


def get_access_status_counts():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(access_status, 'approved') as status, COUNT(*) as cnt
        FROM users GROUP BY status
    """)
    rows = cursor.fetchall()
    conn.close()
    return {row["status"]: row["cnt"] for row in rows}


# =====================================
# АНКЕТА
# =====================================

def save_survey_answers(user_id, business, hobbies, life_goal, bot_goal):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO user_survey(user_id, business, hobbies, life_goal, bot_goal, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            business = excluded.business,
            hobbies = excluded.hobbies,
            life_goal = excluded.life_goal,
            bot_goal = excluded.bot_goal,
            updated_at = CURRENT_TIMESTAMP
    """, (user_id, business, hobbies, life_goal, bot_goal))
    conn.commit()
    conn.close()


def save_survey_analysis(user_id, summary, tags):
    """tags — список строк, сохраняется как JSON."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_survey SET ai_summary=?, ai_tags=?, updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
    """, (summary, json.dumps(tags, ensure_ascii=False), user_id))
    conn.commit()
    conn.close()


def get_survey(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM user_survey WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def get_survey_tags(user_id):
    row = get_survey(user_id)
    if not row or not row["ai_tags"]:
        return []
    try:
        return json.loads(row["ai_tags"])
    except (TypeError, ValueError):
        return []


# =====================================
# ПОИСК ПО ТЕГАМ (админка)
# =====================================

def search_users_by_tag(tag):
    """Простой поиск подстрокой по JSON-полю ai_tags — тегов немного,
    полнотекстовый индекс не нужен."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.telegram_id, u.username, u.first_name, s.ai_tags, s.ai_summary
        FROM user_survey s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE s.ai_tags LIKE ?
        ORDER BY s.updated_at DESC
        LIMIT 20
    """, (f"%{tag}%",))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_users_by_tags(tags):
    """Для рассылки по тегам — объединяет несколько search_users_by_tag."""
    conn = connect()
    cursor = conn.cursor()
    telegram_ids = set()
    for tag in tags:
        cursor.execute("""
            SELECT u.telegram_id FROM user_survey s
            JOIN users u ON u.telegram_id = s.user_id
            WHERE s.ai_tags LIKE ?
        """, (f"%{tag}%",))
        for row in cursor.fetchall():
            telegram_ids.add(row["telegram_id"])
    conn.close()
    return list(telegram_ids)


# =====================================
# AI-ОБРАТНАЯ СВЯЗЬ ПО ЦЕЛЯМ (еженедельно, Premium)
# =====================================

def get_surveys_due_for_feedback(days=7):
    """Пользователи с заполненной анкетой, approved-статусом и Premium,
    которым либо ещё не отправляли разбор цели, либо отправляли
    более `days` дней назад."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, s.life_goal, s.bot_goal
        FROM user_survey s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE u.access_status='approved'
          AND u.premium=1
          AND u.banned=0
          AND s.bot_goal IS NOT NULL AND s.bot_goal != ''
          AND (s.last_feedback_at IS NULL OR s.last_feedback_at <= datetime('now', ?))
    """, (f"-{days} days",))
    rows = cursor.fetchall()
    conn.close()
    return rows


def mark_feedback_sent(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE user_survey SET last_feedback_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (user_id,)
    )
    conn.commit()
    conn.close()


# =====================================
# МЭТЧИНГ ПО ИНТЕРЕСАМ ("найти единомышленника")
# =====================================

def find_match_by_tags(user_id, limit=1):
    """Возвращает approved-пользователей (кроме себя), у которых есть хотя
    бы один общий тег интересов — простое совпадение, без ранжирования."""
    my_tags = get_survey_tags(user_id)
    if not my_tags:
        return []

    conn = connect()
    cursor = conn.cursor()
    placeholders = " OR ".join(["s.ai_tags LIKE ?"] * len(my_tags))
    params = [f"%{tag}%" for tag in my_tags]
    cursor.execute(f"""
        SELECT u.telegram_id, u.username, u.first_name, s.ai_tags, s.ai_summary
        FROM user_survey s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE u.access_status='approved' AND u.banned=0 AND u.telegram_id != ?
          AND ({placeholders})
        ORDER BY RANDOM()
        LIMIT ?
    """, (user_id, *params, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows


# =====================================
# ВЕХИ ПО ЦЕЛИ (milestones)
# =====================================

def save_milestones(user_id, goal_text, milestones):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_milestones WHERE user_id=?", (user_id,))
    for i, text in enumerate(milestones):
        cursor.execute("""
            INSERT INTO user_milestones(user_id, goal_text, milestone_text, position)
            VALUES (?, ?, ?, ?)
        """, (user_id, goal_text, text, i))
    conn.commit()
    conn.close()


def get_milestones(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM user_milestones WHERE user_id=? ORDER BY position ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def toggle_milestone(milestone_id, user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE user_milestones SET done = 1 - done
        WHERE id=? AND user_id=?
    """, (milestone_id, user_id))
    conn.commit()
    conn.close()
