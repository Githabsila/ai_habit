import json

from .core import connect


# =====================================
# СТАТУС ДОСТУПА ("закрытое сообщество")
# =====================================
# Возможные значения access_status: 'new' -> 'pending' -> 'approved'
# ('new' — зарегистрировался, анкету ещё не заполнил;
#  'pending' — анкету заполнил, ждёт одобрения/автоапрува;
#  'approved' — полный доступ к боту)

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
