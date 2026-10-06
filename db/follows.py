"""
Подписки — как в Duolingo.

  • follows(follower_id, followee_id): A подписан на B. Можно подписаться на
    любого игрока (например, на топ рейтинга) — он не обязан отвечать.
  • Друзья = ВЗАИМНАЯ подписка. Если B подпишется на A в ответ, они друзья
    и видят расширенную статистику друг друга (см. db/profiles.py).
  • Ссылка «Добавить друга» (?start=friend_<id>) создаёт обе подписки сразу.

Блокировка рвёт подписки в обе стороны и закрывает профиль и подписку для
заблокированного — причём тот, кого заблокировали, видит просто «не найден»,
а не «вас заблокировали».
"""
import sqlite3

from .core import connect

MAX_FOLLOWING = 500
REPORT_REASONS = ("spam", "abuse", "fake", "other")
MAX_REPORT_COMMENT = 500


def _user_ok(cursor, user_id):
    row = cursor.execute(
        "SELECT banned FROM users WHERE telegram_id=?", (user_id,)
    ).fetchone()
    return row is not None and not row["banned"]


def _blocked(cursor, blocker_id, blocked_id):
    return cursor.execute(
        "SELECT 1 FROM user_blocks WHERE blocker_id=? AND blocked_id=?",
        (blocker_id, blocked_id),
    ).fetchone() is not None


def _follows(cursor, follower_id, followee_id):
    return cursor.execute(
        "SELECT 1 FROM follows WHERE follower_id=? AND followee_id=?",
        (follower_id, followee_id),
    ).fetchone() is not None


def _following_count(cursor, user_id):
    return int(cursor.execute(
        "SELECT COUNT(*) AS n FROM follows WHERE follower_id=?", (user_id,)
    ).fetchone()["n"] or 0)


# ---------------------------------------------------------------------------
# ПОДПИСКА / ОТПИСКА
# ---------------------------------------------------------------------------

def follow(follower_id, followee_id):
    """Подписка. {"ok": True, "newly_followed", "friends", "notify"} либо
    {"error": self | not_found | blocked | limit}. notify — что отправить
    получателю: "followed" (просто подписались), "friends" (подписались в
    ответ — теперь друзья) или None (уже уведомляли про эту пару)."""
    if not follower_id or not followee_id or follower_id == followee_id:
        return {"error": "self"}
    conn = connect()
    try:
        c = conn.cursor()
        if not _user_ok(c, followee_id) or _blocked(c, followee_id, follower_id):
            # Заблокировавшему нас человеку — «не найден», без подсказки.
            return {"error": "not_found"}
        if _blocked(c, follower_id, followee_id):
            return {"error": "blocked"}
        already = _follows(c, follower_id, followee_id)
        if not already:
            if _following_count(c, follower_id) >= MAX_FOLLOWING:
                return {"error": "limit"}
            c.execute(
                "INSERT INTO follows(follower_id, followee_id) VALUES (?, ?)",
                (follower_id, followee_id),
            )
        friends = _follows(c, followee_id, follower_id)
        notify = None
        if not already:
            c.execute(
                "INSERT OR IGNORE INTO follow_notices(follower_id, followee_id) VALUES (?, ?)",
                (follower_id, followee_id),
            )
            if c.rowcount > 0:
                notify = "friends" if friends else "followed"
        conn.commit()
        return {"ok": True, "newly_followed": not already, "friends": friends, "notify": notify}
    finally:
        conn.close()


def unfollow(follower_id, followee_id):
    """Отписка в одну сторону: подписчик на тебя остаётся подписчиком, но
    вы больше не друзья. True, если подписка была."""
    conn = connect()
    try:
        cursor = conn.execute(
            "DELETE FROM follows WHERE follower_id=? AND followee_id=?",
            (follower_id, followee_id),
        )
        removed = cursor.rowcount > 0
        conn.commit()
        return removed
    finally:
        conn.close()


def make_friends(user_id, other_id):
    """Взаимная подписка разом — ссылка «Добавить друга». True, если пара
    только что стала друзьями; False — нельзя (сам с собой, нет такого
    игрока, бан, блокировка, лимит) или уже были друзьями."""
    if not user_id or not other_id or user_id == other_id:
        return False
    conn = connect()
    try:
        c = conn.cursor()
        if not _user_ok(c, user_id) or not _user_ok(c, other_id):
            return False
        if _blocked(c, user_id, other_id) or _blocked(c, other_id, user_id):
            return False
        had_forward = _follows(c, user_id, other_id)
        had_back = _follows(c, other_id, user_id)
        if had_forward and had_back:
            return False
        for a, b, had in ((user_id, other_id, had_forward), (other_id, user_id, had_back)):
            if not had and _following_count(c, a) >= MAX_FOLLOWING:
                return False
        c.execute("INSERT OR IGNORE INTO follows(follower_id, followee_id) VALUES (?, ?)", (user_id, other_id))
        c.execute("INSERT OR IGNORE INTO follows(follower_id, followee_id) VALUES (?, ?)", (other_id, user_id))
        # Об этой паре люди узнали из сообщений про ссылку — «подписался на
        # тебя» отдельным пушем не нужен.
        c.execute("INSERT OR IGNORE INTO follow_notices(follower_id, followee_id) VALUES (?, ?)", (user_id, other_id))
        c.execute("INSERT OR IGNORE INTO follow_notices(follower_id, followee_id) VALUES (?, ?)", (other_id, user_id))
        conn.commit()
        return True
    finally:
        conn.close()


def remove_mutual(user_id, other_id):
    """Убирает подписки в обе стороны. True, если что-то было удалено."""
    conn = connect()
    try:
        cursor = conn.execute(
            "DELETE FROM follows WHERE (follower_id=? AND followee_id=?) OR (follower_id=? AND followee_id=?)",
            (user_id, other_id, other_id, user_id),
        )
        removed = cursor.rowcount > 0
        conn.commit()
        return removed
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ОТНОШЕНИЯ, СЧЁТЧИКИ, СПИСКИ
# ---------------------------------------------------------------------------

def get_relation(viewer_id, target_id):
    conn = connect()
    try:
        c = conn.cursor()
        following = _follows(c, viewer_id, target_id)
        followed_by = _follows(c, target_id, viewer_id)
        return {
            "following": following,
            "followed_by": followed_by,
            "friends": following and followed_by,
            "blocked_by_me": _blocked(c, viewer_id, target_id),
            "blocked_me": _blocked(c, target_id, viewer_id),
        }
    finally:
        conn.close()


def get_counts(user_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM follows WHERE followee_id=?) AS followers, "
            "(SELECT COUNT(*) FROM follows WHERE follower_id=?) AS following",
            (user_id, user_id),
        ).fetchone()
        return {"followers": int(row["followers"] or 0), "following": int(row["following"] or 0)}
    finally:
        conn.close()


def list_follows(user_id, kind, limit=200):
    """kind: "followers" (кто подписан на меня) или "following" (на кого
    подписан я). Каждая запись несёт отношения относительно user_id — по
    ним фронт рисует «Подписаться в ответ» / «Вы подписаны»."""
    if kind == "followers":
        my_col, other_col = "followee_id", "follower_id"
    elif kind == "following":
        my_col, other_col = "follower_id", "followee_id"
    else:
        raise ValueError(kind)
    conn = connect()
    try:
        rows = conn.execute(
            f"""
            SELECT u.telegram_id, u.first_name, u.username, u.handle, u.avatar_id, u.frame_id, u.streak, u.best_streak,
                   EXISTS(SELECT 1 FROM follows a WHERE a.follower_id=:me AND a.followee_id=u.telegram_id) AS following,
                   EXISTS(SELECT 1 FROM follows b WHERE b.follower_id=u.telegram_id AND b.followee_id=:me) AS followed_by
            FROM follows f
            JOIN users u ON u.telegram_id = f.{other_col}
            WHERE f.{my_col} = :me AND u.banned = 0
            ORDER BY f.created_at DESC, f.rowid DESC
            LIMIT :limit
            """,
            {"me": user_id, "limit": int(limit)},
        ).fetchall()
    finally:
        conn.close()
    from .evolution import public_level

    result = []
    for r in rows:
        following, followed_by = bool(r["following"]), bool(r["followed_by"])
        result.append({
            "telegram_id": r["telegram_id"],
            "first_name": r["first_name"] or r["username"] or "Игрок",
            "handle": r["handle"],
            "avatar_id": r["avatar_id"] or "default",
            "frame_id": r["frame_id"] or "default",
            "streak": int(r["streak"] or 0),
            "evo": public_level(r["best_streak"], r["streak"]),
            "following": following,
            "followed_by": followed_by,
            "friends": following and followed_by,
        })
    return result


def find_user_by_handle(raw_handle):
    """Точное совпадение @ника (без регистра) — не поиск по подстроке:
    перебором префиксов список игроков не вытащить."""
    from .handles import normalize_handle
    handle = normalize_handle(raw_handle)
    if not handle:
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT telegram_id, first_name, username, handle FROM users WHERE lower(handle)=? AND banned=0",
            (handle,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "telegram_id": row["telegram_id"],
        "first_name": row["first_name"] or row["username"] or "Игрок",
        "handle": row["handle"],
    }


# ---------------------------------------------------------------------------
# БЛОКИРОВКА И ЖАЛОБЫ
# ---------------------------------------------------------------------------

def block_user(blocker_id, target_id):
    """True — заблокирован (повторная блокировка тоже True); False — нельзя."""
    if not blocker_id or not target_id or blocker_id == target_id:
        return False
    conn = connect()
    try:
        c = conn.cursor()
        if c.execute("SELECT 1 FROM users WHERE telegram_id=?", (target_id,)).fetchone() is None:
            return False
        c.execute("INSERT OR IGNORE INTO user_blocks(blocker_id, blocked_id) VALUES (?, ?)", (blocker_id, target_id))
        c.execute(
            "DELETE FROM follows WHERE (follower_id=? AND followee_id=?) OR (follower_id=? AND followee_id=?)",
            (blocker_id, target_id, target_id, blocker_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def unblock_user(blocker_id, target_id):
    conn = connect()
    try:
        cursor = conn.execute(
            "DELETE FROM user_blocks WHERE blocker_id=? AND blocked_id=?", (blocker_id, target_id)
        )
        removed = cursor.rowcount > 0
        conn.commit()
        return removed
    finally:
        conn.close()


def is_blocked_between(user_id, other_id):
    conn = connect()
    try:
        c = conn.cursor()
        return _blocked(c, user_id, other_id) or _blocked(c, other_id, user_id)
    finally:
        conn.close()


def report_user(reporter_id, target_id, reason, comment=None):
    """{"ok": True, "report_id"} либо {"error": invalid_reason | self |
    not_found | already_reported}. Одна жалоба в день на пару — защита от
    заваливания админов одним и тем же."""
    from .streak import local_today

    if reason not in REPORT_REASONS:
        return {"error": "invalid_reason"}
    if not reporter_id or reporter_id == target_id:
        return {"error": "self"}
    comment = (comment or "").strip()[:MAX_REPORT_COMMENT] or None
    day = str(local_today(reporter_id))
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (target_id,)).fetchone() is None:
            return {"error": "not_found"}
        try:
            cursor = conn.execute(
                "INSERT INTO user_reports(reporter_id, target_id, reason, comment, day) VALUES (?, ?, ?, ?, ?)",
                (reporter_id, target_id, reason, comment, day),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return {"error": "already_reported"}
        return {"ok": True, "report_id": cursor.lastrowid}
    finally:
        conn.close()
