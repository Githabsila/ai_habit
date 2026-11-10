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
    previous = cursor.execute("SELECT access_status FROM users WHERE telegram_id=?", (user_id,)).fetchone()
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
    if status == "approved" and previous is not None and previous["access_status"] != "approved":
        from .tracking import track_server
        track_server(user_id, "access_approved", meta={"from": previous["access_status"]})


def claim_auto_approve(user_id):
    """Атомарно pending → approved для автоодобрения. False — заявку уже одобрили/отклонили/забанили, пока шёл прогон
    (иначе второе «Доступ открыт» и вторая привычка, прогон 09.11)."""
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE users SET access_status='approved' WHERE telegram_id=? AND access_status='pending' AND COALESCE(banned, 0)=0",
            (user_id,))
        conn.commit()
        claimed = cur.rowcount == 1
    finally:
        conn.close()
    if claimed:
        from .tracking import track_server
        track_server(user_id, "access_approved", meta={"from": "pending", "auto": True})
    return claimed


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
          AND COALESCE(banned, 0)=0
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
# AI-ОБРАТНАЯ СВЯЗЬ ПО ЦЕЛЯМ (еженедельно, входит в Pro — для всех, у кого есть доступ)
# =====================================

def get_surveys_due_for_feedback(days=7):
    """Пользователи с заполненной анкетой и approved-статусом, у которых сейчас есть доступ к приложению (триал, подписка Pro или доступ
    от админа), которым либо ещё не отправляли разбор цели, либо отправляли более `days` дней назад. Единая версия Pro (24.10): раньше
    разбор получали только владельцы Premium, теперь он входит в Pro для всех."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, s.life_goal, s.bot_goal
        FROM user_survey s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE u.access_status='approved'
          AND u.banned=0
          AND u.bot_blocked_at IS NULL
          AND s.bot_goal IS NOT NULL AND s.bot_goal != ''
          AND (s.last_feedback_at IS NULL OR s.last_feedback_at <= datetime('now', ?))
    """, (f"-{days} days",))
    rows = cursor.fetchall()
    conn.close()
    from .subscription import bot_access_allowed
    return [row for row in rows if bot_access_allowed(row["user_id"])]   # закрытому гейтом разбор не шлём


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
    """Approved-пользователи (кроме себя), у которых есть общие теги интересов. Решение владельца 09.11: незнакомцу показываем только то,
    что объединяет (общие теги, поле common_tags), — без ИИ-резюме чужой анкеты и без самих ответов анкеты."""
    my_tags = get_survey_tags(user_id)
    if not my_tags:
        return []
    mine = {str(tag).strip().lower(): str(tag).strip() for tag in my_tags if str(tag).strip()}

    conn = connect()
    cursor = conn.cursor()
    # Сравниваем теги в Python: LIKE в SQLite не различает регистр только у латиницы («Бег» ≠ «бег»).
    cursor.execute("""
        SELECT u.telegram_id, u.username, u.first_name, s.ai_tags
        FROM user_survey s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE u.access_status='approved' AND u.banned=0 AND u.telegram_id != ?
          AND s.ai_tags IS NOT NULL AND s.ai_tags NOT IN ('', '[]')
        ORDER BY RANDOM()
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    found = []
    for row in rows:
        try:
            theirs = {str(tag).strip().lower() for tag in json.loads(row["ai_tags"] or "[]")}
        except (TypeError, ValueError):
            continue
        common = [mine[key] for key in mine if key in theirs]       # точное совпадение тега, а не «подстрока» из LIKE
        if common:
            found.append({"telegram_id": row["telegram_id"], "username": row["username"], "first_name": row["first_name"], "common_tags": common})
        if len(found) >= limit:
            break
    return found


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
