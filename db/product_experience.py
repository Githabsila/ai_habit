"""Продуктовая телеметрия и поддержка ранних тестеров ADAM.

Храним только короткие события продукта и технический контекст, который
помогает восстановить путь пользователя перед багом. Никаких паролей,
токенов Telegram WebApp или полного содержимого чата здесь нет.
"""
import json
from datetime import datetime, timezone

from .core import connect

MAX_EVENT_TYPE_LEN = 60
MAX_EVENT_PAYLOAD_LEN = 1200
MAX_BUG_TEXT_LEN = 2000
MAX_EXPECTED_LEN = 1000
MAX_SEVERITY_LEN = 20
MAX_TAB_LEN = 50
MAX_PATH_LEN = 300
MAX_SCREENSHOT_LEN = 1_500_000


def log_product_event(user_id, event_type, payload=None):
    event_type = str(event_type or "unknown")[:MAX_EVENT_TYPE_LEN]
    if payload is None:
        payload_text = None
    elif isinstance(payload, str):
        payload_text = payload[:MAX_EVENT_PAYLOAD_LEN]
    else:
        try:
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:MAX_EVENT_PAYLOAD_LEN]
        except (TypeError, ValueError):
            payload_text = str(payload)[:MAX_EVENT_PAYLOAD_LEN]

    conn = connect()
    try:
        conn.execute(
            "INSERT INTO product_events(user_id,event_type,payload) VALUES (?,?,?)",
            (int(user_id), event_type, payload_text),
        )
        # Оставляем компактную историю. Для диагностики нам важнее свежие
        # события, чем бесконечный архив кликов.
        if int(user_id) % 20 == 0:
            conn.execute(
                "DELETE FROM product_events WHERE id NOT IN "
                "(SELECT id FROM product_events ORDER BY id DESC LIMIT 15000)"
            )
        conn.commit()
    finally:
        conn.close()


def get_recent_user_events(user_id, minutes=10, limit=120):
    minutes = max(1, min(int(minutes or 10), 60))
    limit = max(1, min(int(limit or 120), 300))
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id,event_type,payload,created_at FROM product_events "
            "WHERE user_id=? AND created_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT ?",
            (int(user_id), f"-{minutes} minutes", limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def claim_first_win_push(user_id, result_type):
    """Атомарно разрешает ровно одно первое result-сообщение в Telegram.

    result_type: habit или task. Возвращает True только тому запросу, который
    первым забрал право на отправку. Это защищает от двойных сообщений при
    двойном тапе/повторе HTTP-запроса.
    """
    result_type = "habit" if result_type == "habit" else "task"
    column = "first_habit_completed_at" if result_type == "habit" else "first_task_completed_at"
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE users SET {column}=COALESCE({column}, CURRENT_TIMESTAMP) "
            "WHERE telegram_id=? AND " + column + " IS NULL",
            (int(user_id),),
        )
        claimed = cur.rowcount > 0
        conn.commit()
        return claimed
    finally:
        conn.close()


def get_first_win_state(user_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT first_habit_completed_at, first_task_completed_at, first_win_push_sent "
            "FROM users WHERE telegram_id=?",
            (int(user_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def create_bug_report(user_id, description, expected=None, severity="medium", tab=None,
                      path=None, screenshot_data_url=None, context=None):
    description = str(description or "").strip()[:MAX_BUG_TEXT_LEN]
    expected = str(expected or "").strip()[:MAX_EXPECTED_LEN] or None
    severity = str(severity or "medium").strip().lower()[:MAX_SEVERITY_LEN]
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "medium"
    tab = (str(tab)[:MAX_TAB_LEN] if tab else None)
    path = (str(path)[:MAX_PATH_LEN] if path else None)
    screenshot_data_url = (str(screenshot_data_url)[:MAX_SCREENSHOT_LEN] if screenshot_data_url else None)
    context_text = None
    if context is not None:
        try:
            context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))[:6000]
        except (TypeError, ValueError):
            context_text = str(context)[:6000]

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bug_reports(user_id,description,expected,severity,tab,path,screenshot_data_url,context) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (int(user_id), description, expected, severity, tab, path, screenshot_data_url, context_text),
        )
        bug_id = cur.lastrowid
        conn.commit()
        return int(bug_id)
    finally:
        conn.close()


def get_bug_report(bug_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM bug_reports WHERE id=?", (int(bug_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_recent_bug_reports(minutes=None, limit=50, user_id=None):
    limit = max(1, min(int(limit or 50), 200))
    clauses = []
    params = []
    if minutes is not None:
        minutes = max(1, min(int(minutes), 24 * 60))
        clauses.append("created_at >= datetime('now', ?)")
        params.append(f"-{minutes} minutes")
    if user_id is not None:
        clauses.append("user_id=?")
        params.append(int(user_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id,user_id,description,expected,severity,tab,path,status,created_at,updated_at "
            f"FROM bug_reports{where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_bug_status(bug_id, status):
    status = str(status or "new").lower()
    if status not in {"new", "in_progress", "fixed", "wont_fix"}:
        return False
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE bug_reports SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, int(bug_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def start_onboarding(user_id):
    """Начинает 15-минутный onboarding один раз."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET onboarding_started_at=COALESCE(onboarding_started_at,CURRENT_TIMESTAMP), "
            "onboarding_stage=COALESCE(onboarding_stage,0) WHERE telegram_id=?",
            (int(user_id),),
        )
        conn.commit()
    finally:
        conn.close()


def get_onboarding_state(user_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT onboarding_started_at,onboarding_stage,first_habit_completed_at,first_task_completed_at,first_win_push_sent "
            "FROM users WHERE telegram_id=?", (int(user_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def advance_onboarding(user_id, stage):
    # 6 шагов теперь (было 5) — базовая версия онбординга добавила шаг
    # приветствия первым (см. app.js::PRODUCT_ONBOARDING_STEPS).
    stage = max(0, min(int(stage), 6))
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET onboarding_started_at=COALESCE(onboarding_started_at,CURRENT_TIMESTAMP), "
            "onboarding_stage=MAX(COALESCE(onboarding_stage,0),?) WHERE telegram_id=?",
            (stage, int(user_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_first_win_push_sent(user_id):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE users SET first_win_push_sent=1 WHERE telegram_id=? AND first_win_push_sent=0", (int(user_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
