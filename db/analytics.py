"""
Базовая аналитика и мониторинг бота — DAU, воронки, расход ИИ-лимита,
завершаемость привычек. Все функции только читают/пишут через .core.connect(),
без побочных эффектов на бизнес-логику — безопасно вызывать откуда угодно
(админ-команда, ежедневный дайджест, ручной запрос).
"""
from datetime import date, datetime, timedelta

from .core import connect


def touch_last_seen(user_id):
    """Отмечает активность пользователя — источник для DAU/WAU. Вызывается
    и из Mini App (webapp/auth_helpers.authenticate), и из бота
    (middlewares/access_control.py), чтобы отражать обе поверхности."""
    conn = connect()
    conn.execute(
        "UPDATE users SET last_seen=? WHERE telegram_id=?",
        (datetime.utcnow().isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def get_dau(days=1):
    """Уникальных пользователей с активностью за последние `days` дней."""
    conn = connect()
    cursor = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) AS n FROM users WHERE last_seen IS NOT NULL AND last_seen >= ?",
        (since,),
    )
    n = int(cursor.fetchone()["n"] or 0)
    conn.close()
    return n


def get_subscription_conversion():
    """Сколько пользователей когда-либо платили за подписку из тех, кто
    вообще начал триал (has subscription state at all)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS n FROM users WHERE subscription_first_payment_at IS NOT NULL "
        "OR subscription_paid_until IS NOT NULL"
    )
    started_trial_or_paid = int(cursor.fetchone()["n"] or 0)
    cursor.execute(
        "SELECT COUNT(*) AS n FROM users WHERE subscription_first_payment_at IS NOT NULL"
    )
    paid = int(cursor.fetchone()["n"] or 0)
    conn.close()
    rate = round(100 * paid / started_trial_or_paid, 1) if started_trial_or_paid else 0.0
    return {"paid": paid, "eligible": started_trial_or_paid, "rate_percent": rate}


def get_ai_usage_today():
    """Суммарно потраченных единиц ответа ИИ за сегодня по всем
    пользователям — это лимит РУЧНОГО ЧАТА конкретного пользователя
    (см. handlers/ai.py consume_ai_answer), НЕ реальный расход API.
    Для реального расхода токенов (включая автоматические напоминания,
    которые сюда не попадают) — см. get_ai_tokens_today() ниже."""
    conn = connect()
    cursor = conn.cursor()
    today = str(date.today())
    cursor.execute("SELECT COALESCE(SUM(used), 0) AS n FROM ai_quota WHERE day=?", (today,))
    n = int(cursor.fetchone()["n"] or 0)
    conn.close()
    return n


def log_ai_tokens(tokens, provider=""):
    """Пишет ОДИН реальный вызов LLM — вызывается из multi_agent.py::_ask,
    единственной точки, через которую идут вообще все обращения к
    Groq/OpenAI (ручной чат, совет дня, утренние сообщения, еженедельный
    разбор, анализ анкеты и т.д.) — поэтому покрывает всё, а не только чат.
    Не должно ронять сам AI-ответ, если запись не удалась."""
    if not tokens:
        return
    try:
        conn = connect()
        conn.execute(
            "INSERT INTO ai_token_log(day, tokens, provider) VALUES (?, ?, ?)",
            (str(date.today()), int(tokens), provider or ""),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_ai_tokens_today():
    """Реальный суммарный расход токенов LLM за сегодня по ВСЕМ фичам
    (не только чату) — см. log_ai_tokens. Это то, что реально стоит денег
    у провайдера, в отличие от get_ai_usage_today()."""
    conn = connect()
    cursor = conn.cursor()
    today = str(date.today())
    cursor.execute("SELECT COALESCE(SUM(tokens), 0) AS n FROM ai_token_log WHERE day=?", (today,))
    n = int(cursor.fetchone()["n"] or 0)
    conn.close()
    return n


def get_ai_tokens_by_provider_today():
    """То же самое, но с разбивкой по провайдеру (groq/openai) — полезно
    понять, кто из двух реально тратит бюджет прямо сейчас."""
    conn = connect()
    cursor = conn.cursor()
    today = str(date.today())
    cursor.execute(
        "SELECT COALESCE(provider,'?') AS provider, COALESCE(SUM(tokens),0) AS n "
        "FROM ai_token_log WHERE day=? GROUP BY provider",
        (today,),
    )
    rows = {row["provider"]: int(row["n"] or 0) for row in cursor.fetchall()}
    conn.close()
    return rows


def get_habit_completion_rate():
    """Доля привычек, отмеченных выполненными сегодня, от всех активных
    привычек (habits.completed сбрасывается в полночь — см. db/habits.py)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total, COALESCE(SUM(completed), 0) AS done FROM habits")
    row = cursor.fetchone()
    conn.close()
    total = int(row["total"] or 0)
    done = int(row["done"] or 0)
    rate = round(100 * done / total, 1) if total else 0.0
    return {"done": done, "total": total, "rate_percent": rate}


def get_survey_funnel():
    """Воронка входа: анкета начата (есть запись в users) → анкета
    завершена (survey_completed_at) → доступ одобрен (access_status)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM users")
    total = int(cursor.fetchone()["n"] or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE survey_completed_at IS NOT NULL")
    completed_survey = int(cursor.fetchone()["n"] or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE access_status='approved'")
    approved = int(cursor.fetchone()["n"] or 0)
    conn.close()
    return {"total": total, "completed_survey": completed_survey, "approved": approved}


def get_first_ai_message_funnel():
    """Сколько одобренных пользователей вообще написали ADAM хотя бы раз
    (ai_intro_shown=1 ставится в claim_ai_first_message — db/ai.py)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE access_status='approved'")
    approved = int(cursor.fetchone()["n"] or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM users WHERE ai_intro_shown=1")
    sent_first_message = int(cursor.fetchone()["n"] or 0)
    conn.close()
    rate = round(100 * sent_first_message / approved, 1) if approved else 0.0
    return {"sent_first_message": sent_first_message, "approved": approved, "rate_percent": rate}
