"""
Память точек дня (10/12/17/22): какие части сообщений человеку уже приходили — чтобы следующая точка писалась иначе
(adam_messages.compose_checkpoint_message). Любой сбой здесь не должен ломать рассылку: без памяти сообщение просто выбирается заново.
"""
import json
import logging

from .core import connect

logger = logging.getLogger(__name__)

KEEP = 8                                   # сколько последних точек помним на человека


def recent_checkpoint_picks(user_id, limit=3):
    """Выбранные части последних точек человека, новые первыми: [{"o": начало, "s": счёт, "f": фокус, "c": призыв}, …]."""
    try:
        conn = connect()
        try:
            rows = conn.execute("SELECT picks FROM checkpoint_picks WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), int(limit))).fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            try:
                picks = json.loads(row["picks"])
            except (TypeError, ValueError):
                continue
            if isinstance(picks, dict):
                out.append(picks)
        return out
    except Exception:
        logger.warning("Не удалось прочитать историю точек дня (%s)", user_id, exc_info=True)
        return []


def save_checkpoint_picks(user_id, picks):
    """Запоминает, из каких частей собрана отправленная точка дня (вызывать после успешной отправки)."""
    try:
        conn = connect()
        try:
            conn.execute("INSERT INTO checkpoint_picks(user_id, picks) VALUES (?,?)", (int(user_id), json.dumps(picks, ensure_ascii=False)))
            conn.execute(
                "DELETE FROM checkpoint_picks WHERE user_id=? AND id NOT IN (SELECT id FROM checkpoint_picks WHERE user_id=? ORDER BY id DESC LIMIT ?)",
                (int(user_id), int(user_id), KEEP),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Не удалось запомнить точку дня (%s)", user_id, exc_info=True)


def checkpoints_sent_today(user_id, day_iso):
    """Сколько точек дня человеку уже отправлено за этот день (по журналу одноразовых уведомлений)."""
    try:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM streak_notifications WHERE user_id=? AND day=? AND kind LIKE 'habit_checkpoint_%'",
                (int(user_id), str(day_iso)),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"] or 0)
    except Exception:
        logger.warning("Не удалось посчитать точки дня за сегодня (%s)", user_id, exc_info=True)
        return 0
