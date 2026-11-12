"""
Журнал отправок бота (просьба владельца 11.11: «видеть все отработанные сообщения в боте — когда, почему и при каких условиях»).

Каждое сообщение, которое бот отправил человеку в личный чат (текст, фото, документ, счёт…), записывается здесь middleware'ом
сессии бота (bot_middlewares.log_outbox): кому, когда, что (начало текста), чем отправлено и КТО отправил — функция в нашем коде
(«streak_scheduler.run_streak_risk_notifications»). Рассылки, у которых одна общая функция на несколько сообщений (точки дня 12/17/22,
сообщения триала, сводки недели и месяца), сами называют своё сообщение через `with outbox.kind("checkpoint_17"):` — это ключ карты
сообщений (bot_messages_map.py), он надёжнее стека вызовов. Ошибки доставки тоже пишутся (ok=0, error=тип ошибки).

Запись идёт в буфер в памяти и сбрасывается в базу пачкой (раз в 30 секунд из планировщика, при чтении журнала и при 50 записях):
отдельное подключение к SQLite на каждое сообщение рассылки на тысячи человек держало бы бота (прогон 11.11).
Храним 30 дней (cleanup раз в сутки). Журнал никогда не роняет отправку: любая ошибка записи только логируется.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from .core import connect

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
TEXT_LIMIT = 600
FLUSH_AT = 50

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bot_outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        method TEXT NOT NULL, source TEXT, chain TEXT, text TEXT, ok INTEGER NOT NULL DEFAULT 1, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_bot_outbox_user ON bot_outbox(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_bot_outbox_source ON bot_outbox(source, id)",
    "CREATE INDEX IF NOT EXISTS idx_bot_outbox_sent ON bot_outbox(sent_at)",
)

_kind = ContextVar("outbox_kind", default=None)
_buffer = []


def ensure_schema(cursor):
    for statement in SCHEMA[:1]:
        cursor.execute(statement)
    if "kind" not in {row[1] for row in cursor.execute("PRAGMA table_info(bot_outbox)").fetchall()}:
        cursor.execute("ALTER TABLE bot_outbox ADD COLUMN kind TEXT")
    for statement in SCHEMA[1:]:
        cursor.execute(statement)


@contextmanager
def kind(key):
    """Сообщения, отправленные внутри блока, помечаются ключом карты сообщений (bot_messages_map.BY_KEY)."""
    token = _kind.set(key)
    try:
        yield
    finally:
        _kind.reset(token)


def current_kind():
    return _kind.get()


def record(user_id, method, source, chain, text, ok=True, error=None, kind_key=None):
    try:
        _buffer.append((int(user_id), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), str(method), source, chain,
                        (text or "")[:TEXT_LIMIT], 1 if ok else 0, error, kind_key if kind_key is not None else _kind.get()))
        if len(_buffer) >= FLUSH_AT:
            flush()
    except Exception:
        logger.warning("Журнал отправок: не удалось записать", exc_info=True)


def flush():
    if not _buffer:
        return
    rows = _buffer[:]
    del _buffer[:len(rows)]
    try:
        conn = connect()
        try:
            conn.executemany("INSERT INTO bot_outbox(user_id, sent_at, method, source, chain, text, ok, error, kind) VALUES (?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Журнал отправок: не удалось сбросить %s записей", len(rows), exc_info=True)


def for_user(user_id, limit=100):
    flush()
    conn = connect()
    try:
        rows = conn.execute("SELECT id, sent_at, method, source, chain, text, ok, error, kind FROM bot_outbox WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (int(user_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def source_stats(days=7):
    """Сколько раз за `days` дней сработала каждая рассылка: {ключ карты или «модуль.функция»: {count, failed, last_at}}."""
    flush()
    conn = connect()
    try:
        rows = conn.execute("SELECT COALESCE(kind, source) AS src, COUNT(*) AS cnt, SUM(1 - ok) AS failed, MAX(sent_at) AS last_at FROM bot_outbox "
                            "WHERE sent_at >= datetime('now', ?) GROUP BY COALESCE(kind, source)", (f"-{int(days)} days",)).fetchall()
        return {r["src"]: {"count": r["cnt"], "failed": r["failed"] or 0, "last_at": r["last_at"]} for r in rows}
    finally:
        conn.close()


def cleanup(days=KEEP_DAYS):
    flush()
    conn = connect()
    try:
        conn.execute("DELETE FROM bot_outbox WHERE sent_at < datetime('now', ?)", (f"-{int(days)} days",))
        conn.commit()
    finally:
        conn.close()
