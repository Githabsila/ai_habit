"""Эксклюзивные подарки от администратора.

Медаль 🏅 (товар BADGE_ITEM_ID) больше не продаётся в магазине и не дарится друзьями: её выдаёт
только администратор за пользу приложению и вклад в его развитие (первым активным пользователям —
за баги и обратную связь). Выдача пишет товар в user_items (значок 🏅 у имени в рейтинге и
профиле работает как раньше) и заводит запись admin_gifts: при следующем входе в Mini App человек
видит окно «Эксклюзивный подарок от Администратора» (app.js, #adminGiftOverlay), после закрытия
запись помечается просмотренной.
"""
from datetime import date

from .core import connect

BADGE_ITEM_ID = 3
DEFAULT_NOTE = "За пользу и вклад в развитие ADAM"
MAX_NOTE_CHARS = 140


def clean_note(note):
    text = " ".join(str(note or "").split())[:MAX_NOTE_CHARS]
    return text or DEFAULT_NOTE


def grant_admin_badge(user_id, note=None):
    """Выдаёт медаль. Возвращает {"ok": True, "gift_id", "note"} либо {"error": ...}:
    user_not_found — такого пользователя нет; already_owned — медаль у него уже есть."""
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (user_id,)).fetchone() is None:
            return {"error": "user_not_found"}
        # В user_items нет уникального индекса (user_id, item_id): владение проверяем сами.
        if conn.execute(
            "SELECT 1 FROM user_items WHERE user_id=? AND item_id=? LIMIT 1", (user_id, BADGE_ITEM_ID)
        ).fetchone():
            return {"error": "already_owned"}
        conn.execute(
            "INSERT INTO user_items(user_id, item_id, purchased_at) VALUES (?, ?, ?)",
            (user_id, BADGE_ITEM_ID, str(date.today())),
        )
        text = clean_note(note)
        cursor = conn.execute(
            "INSERT INTO admin_gifts(user_id, kind, item_id, note) VALUES (?, 'badge', ?, ?)",
            (user_id, BADGE_ITEM_ID, text),
        )
        conn.commit()
        return {"ok": True, "gift_id": cursor.lastrowid, "note": text}
    finally:
        conn.close()


def get_unseen_admin_gift(user_id):
    """Самый старый ещё не показанный подарок (если их несколько — по одному за вход)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, kind, note, created_at FROM admin_gifts WHERE user_id=? AND seen=0 ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"id": row["id"], "kind": row["kind"], "note": row["note"] or DEFAULT_NOTE, "created_at": str(row["created_at"])}


def mark_admin_gift_seen(user_id, gift_id):
    conn = connect()
    try:
        cursor = conn.execute(
            "UPDATE admin_gifts SET seen=1, seen_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND seen=0",
            (gift_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
