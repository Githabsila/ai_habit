"""
Журнал отправок бота (просьба владельца 11.11: «видеть все отработанные сообщения в боте — когда, почему и при каких условиях»).

Каждое сообщение, которое бот отправил человеку в личный чат (текст, фото, документ, счёт…), записывается здесь middleware'ом
сессии бота (bot_middlewares.log_outbox): кому, когда, что (начало текста), чем отправлено и КТО отправил — функция в нашем коде
(«streak_scheduler.run_streak_risk_notifications»). По имени функции админка находит рассылку в карте сообщений (bot_messages_map.py)
и показывает её название, время и условия. Ошибки доставки тоже пишутся (ok=0, error=тип ошибки).

Храним 30 дней (cleanup раз в сутки). Журнал никогда не роняет отправку: любая ошибка записи только логируется.
"""
import logging

from .core import connect

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
TEXT_LIMIT = 600

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bot_outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        method TEXT NOT NULL, source TEXT, chain TEXT, text TEXT, ok INTEGER NOT NULL DEFAULT 1, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_bot_outbox_user ON bot_outbox(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_bot_outbox_source ON bot_outbox(source, id)",
)


def ensure_schema(cursor):
    for statement in SCHEMA:
        cursor.execute(statement)


def record(user_id, method, source, chain, text, ok=True, error=None):
    try:
        conn = connect()
        try:
            conn.execute("INSERT INTO bot_outbox(user_id, method, source, chain, text, ok, error) VALUES (?,?,?,?,?,?,?)",
                         (int(user_id), str(method), source, chain, (text or "")[:TEXT_LIMIT], 1 if ok else 0, error))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Журнал отправок: не удалось записать", exc_info=True)


def for_user(user_id, limit=100):
    conn = connect()
    try:
        rows = conn.execute("SELECT id, sent_at, method, source, chain, text, ok, error FROM bot_outbox WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (int(user_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def source_stats(days=7):
    """Сколько раз за `days` дней сработала каждая рассылка (для карты сообщений: «за неделю ушло N»)."""
    conn = connect()
    try:
        rows = conn.execute("SELECT source, COUNT(*) AS cnt, SUM(1 - ok) AS failed, MAX(sent_at) AS last_at FROM bot_outbox "
                            "WHERE sent_at >= datetime('now', ?) GROUP BY source", (f"-{int(days)} days",)).fetchall()
        return {r["source"]: {"count": r["cnt"], "failed": r["failed"] or 0, "last_at": r["last_at"]} for r in rows}
    finally:
        conn.close()


def cleanup(days=KEEP_DAYS):
    conn = connect()
    try:
        conn.execute("DELETE FROM bot_outbox WHERE sent_at < datetime('now', ?)", (f"-{int(days)} days",))
        conn.commit()
    finally:
        conn.close()
