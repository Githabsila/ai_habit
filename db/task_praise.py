from datetime import date, datetime

from .core import connect

# =====================================
# ПОХВАЛА ЗА ВТОРОСТЕПЕННЫЕ ЗАДАЧИ ПЛАНА ДНЯ (пром 7.1)
# =====================================
# Короткое поощрение после каждой отдельно закрытой второстепенной задачи
# (не главной, и не в момент, когда закрыт весь план — там уже есть
# отдельное сообщение format_all_tasks_done_message). Не повторяется в
# течение дня; в первые 3 дня использования и до 15 отметок — не
# повторяется вовсе (см. adam_messages.format_secondary_task_praise).


def get_secondary_task_praise_state(user_id):
    """total — сколько похвал уже показано этому пользователю за всё время;
    used_today/used_ever — ключи уже показанных сообщений (за сегодня и
    за всё время соответственно), чтобы не повторяться."""
    today = date.today().isoformat()
    conn = connect()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) AS n FROM secondary_task_praise_log WHERE user_id=?",
        (user_id,),
    )
    total = int(c.fetchone()["n"] or 0)
    c.execute(
        "SELECT DISTINCT message_key FROM secondary_task_praise_log WHERE user_id=?",
        (user_id,),
    )
    used_ever = {r["message_key"] for r in c.fetchall()}
    c.execute(
        "SELECT message_key FROM secondary_task_praise_log WHERE user_id=? AND day=?",
        (user_id, today),
    )
    used_today = {r["message_key"] for r in c.fetchall()}
    conn.close()
    return {"total": total, "used_ever": used_ever, "used_today": used_today}


def record_secondary_task_praise(user_id, message_key):
    today = date.today().isoformat()
    conn = connect()
    conn.execute(
        "INSERT INTO secondary_task_praise_log(user_id, message_key, day) VALUES(?,?,?)",
        (user_id, message_key, today),
    )
    conn.commit()
    conn.close()
