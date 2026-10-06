"""
Подарки друзьям: алмазы и косметика из магазина (рамки, аватар, тема, значок).

Правила — те, о которых договорились:
  • дарить можно только ДРУЗЬЯМ (взаимная подписка, db/follows.py);
  • Adam Coin / очки уровня дарить НЕЛЬЗЯ: они идут в рейтинг, и подарок очков
    был бы лазейкой накрутки через второй аккаунт. Предмет оплачивается
    монетами ОТПРАВИТЕЛЯ (тратится только xp-«кошелёк», total_xp и уровень
    не меняются), получатель получает сам предмет — это просто покупка «на
    чужое имя», ничего не создаётся из воздуха;
  • алмазы списываются у отправителя и зачисляются получателю;
  • не больше MAX_GIFTS_PER_DAY подарков в сутки на отправителя;
  • пакеты ответов ИИ, бустеры, Premium и товары за Telegram Stars не
    дарятся: у них свои дневные лимиты/оплата, подарок стал бы обходом.

Списание, проверка суточного лимита и выдача идут одной транзакцией:
первый UPDATE берёт блокировку записи SQLite, поэтому параллельные подарки
выстраиваются в очередь и лимит нельзя пробить гонкой.
"""
from datetime import date

from .core import connect

GIFT_DIAMOND_AMOUNTS = (1, 3, 5)
MAX_GIFTS_PER_DAY = 3
# Рамки и аватар — «frame»/«avatar» (на очень старых базах id 4–6 ещё «cosmetic»,
# см. миграцию в db/core.py): дарим любые.
GIFTABLE_ITEM_TYPES = ("cosmetic", "frame", "avatar", "theme", "badge")


def _local_day(user_id):
    from .streak import local_today
    return str(local_today(user_id))


def _are_mutual_friends(user_id, other_id):
    from .follows import get_relation
    return user_id != other_id and get_relation(user_id, other_id)["friends"]


def _giftable_items(cursor):
    rows = cursor.execute(
        "SELECT id, name, description, price, item_type FROM shop_items "
        "WHERE item_type IN ({}) AND price > 0 ORDER BY price ASC".format(",".join("?" * len(GIFTABLE_ITEM_TYPES))),
        GIFTABLE_ITEM_TYPES,
    ).fetchall()
    return rows


def gifts_left_today(sender_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM gifts WHERE from_user_id=? AND day=?",
            (sender_id, _local_day(sender_id)),
        ).fetchone()
    finally:
        conn.close()
    return max(0, MAX_GIFTS_PER_DAY - int(row["n"] or 0))


# ---------------------------------------------------------------------------
# ВХОДЯЩИЕ: экран «Мне подарили»
# ---------------------------------------------------------------------------

RECEIVED_LIMIT = 50


def _received_label(row):
    if row["kind"] == "diamonds":
        return f"{int(row['amount'] or 1)} 💎"
    return row["item_name"] or "Подарок"


def get_received_gifts(user_id, limit=RECEIVED_LIMIT):
    """Подарки, которые подарили ЭТОМУ пользователю, новые сверху. Видит их
    только сам получатель. seen=False — ещё не показывали в приложении."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT g.id, g.kind, g.item_id, g.amount, g.created_at, g.seen,
                   u.telegram_id AS from_id, u.first_name, u.username, u.handle, u.avatar_id, u.frame_id,
                   s.name AS item_name
            FROM gifts g
            LEFT JOIN users u ON u.telegram_id = g.from_user_id
            LEFT JOIN shop_items s ON s.id = g.item_id
            WHERE g.to_user_id=?
            ORDER BY g.id DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "label": _received_label(r),
            "amount": int(r["amount"] or 1),
            "item_id": r["item_id"],
            "created_at": str(r["created_at"]),
            "seen": bool(r["seen"]),
            "from": {
                "telegram_id": r["from_id"],
                "first_name": r["first_name"] or r["username"] or "Игрок",
                "handle": r["handle"],
                "avatar_id": r["avatar_id"] or "default",
                "frame_id": r["frame_id"] or "default",
            },
        }
        for r in rows
    ]


def count_unseen_gifts(user_id):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM gifts WHERE to_user_id=? AND seen=0", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0)


def mark_gifts_seen(user_id):
    """Отмечает все подарки пользователя просмотренными; возвращает, сколько
    было новых."""
    conn = connect()
    try:
        cursor = conn.execute("UPDATE gifts SET seen=1 WHERE to_user_id=? AND seen=0", (user_id,))
        changed = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return changed


def get_gift_options(sender_id, receiver_id):
    """Что можно подарить этому другу прямо сейчас — для окна подарка.
    {"error": "not_friends"} — если он не друг."""
    if not _are_mutual_friends(sender_id, receiver_id):
        return {"error": "not_friends"}
    conn = connect()
    try:
        c = conn.cursor()
        sender = c.execute("SELECT xp, diamonds, first_name FROM users WHERE telegram_id=?", (sender_id,)).fetchone()
        receiver = c.execute("SELECT first_name, username FROM users WHERE telegram_id=?", (receiver_id,)).fetchone()
        if sender is None or receiver is None:
            return {"error": "not_found"}
        owned = {r["item_id"] for r in c.execute("SELECT item_id FROM user_items WHERE user_id=?", (receiver_id,)).fetchall()}
        coins, diamonds = int(sender["xp"] or 0), int(sender["diamonds"] or 0)
        items = [
            {
                "id": it["id"],
                "name": it["name"],
                "description": it["description"],
                "price": int(it["price"]),
                "affordable": coins >= int(it["price"]),
                "owned_by_receiver": it["id"] in owned,
            }
            for it in _giftable_items(c)
        ]
    finally:
        conn.close()
    return {
        "to": {"telegram_id": receiver_id, "first_name": receiver["first_name"] or receiver["username"] or "Друг"},
        "balances": {"coins": coins, "diamonds": diamonds},
        "diamonds": [{"amount": n, "affordable": diamonds >= n} for n in GIFT_DIAMOND_AMOUNTS],
        "items": items,
        "left_today": gifts_left_today(sender_id),
        "max_per_day": MAX_GIFTS_PER_DAY,
    }


def send_gift(sender_id, receiver_id, kind, amount=None, item_id=None):
    """{"ok": True, "gift": {...}, "left_today"} либо {"error": код}:
    not_friends / invalid_kind / invalid_amount / item_not_giftable /
    already_owned / not_enough_coins / not_enough_diamonds / daily_limit."""
    if not _are_mutual_friends(sender_id, receiver_id):
        return {"error": "not_friends"}
    if kind not in ("diamonds", "item"):
        return {"error": "invalid_kind"}

    day = _local_day(sender_id)
    conn = connect()
    try:
        c = conn.cursor()
        receiver = c.execute(
            "SELECT telegram_id FROM users WHERE telegram_id=? AND banned=0", (receiver_id,)
        ).fetchone()
        if receiver is None:
            return {"error": "not_friends"}

        if kind == "diamonds":
            try:
                amount = int(amount)
            except (TypeError, ValueError):
                return {"error": "invalid_amount"}
            if amount not in GIFT_DIAMOND_AMOUNTS:
                return {"error": "invalid_amount"}
            # Первый UPDATE берёт блокировку записи — дальше всё сериализовано.
            c.execute(
                "UPDATE users SET diamonds = diamonds - ? WHERE telegram_id=? AND diamonds >= ?",
                (amount, sender_id, amount),
            )
            if c.rowcount == 0:
                conn.rollback()
                return {"error": "not_enough_diamonds"}
            price, label, db_item_id = 0, f"{amount} 💎", None
        else:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                return {"error": "item_not_giftable"}
            item = c.execute(
                "SELECT id, name, price, item_type FROM shop_items WHERE id=?", (item_id,)
            ).fetchone()
            if item is None or item["item_type"] not in GIFTABLE_ITEM_TYPES or int(item["price"] or 0) <= 0:
                return {"error": "item_not_giftable"}
            if c.execute(
                "SELECT 1 FROM user_items WHERE user_id=? AND item_id=? LIMIT 1", (receiver_id, item_id)
            ).fetchone():
                return {"error": "already_owned"}
            price = int(item["price"])
            # Списываем только тратимую валюту (xp); total_xp и уровень не трогаем —
            # как и при обычной покупке (db/shop.py::buy_shop_item).
            c.execute(
                "UPDATE users SET xp = xp - ? WHERE telegram_id=? AND xp >= ?",
                (price, sender_id, price),
            )
            if c.rowcount == 0:
                conn.rollback()
                return {"error": "not_enough_coins"}
            label, db_item_id = item["name"], item_id

        used_today = int(c.execute(
            "SELECT COUNT(*) AS n FROM gifts WHERE from_user_id=? AND day=?", (sender_id, day)
        ).fetchone()["n"] or 0)
        if used_today >= MAX_GIFTS_PER_DAY:
            conn.rollback()
            return {"error": "daily_limit"}

        if kind == "diamonds":
            c.execute("UPDATE users SET diamonds = diamonds + ? WHERE telegram_id=?", (amount, receiver_id))
        else:
            c.execute(
                "INSERT OR IGNORE INTO user_items(user_id, item_id, purchased_at) VALUES (?, ?, ?)",
                (receiver_id, db_item_id, str(date.today())),
            )
            if c.rowcount == 0:  # параллельно подарили/купили тот же предмет
                conn.rollback()
                return {"error": "already_owned"}
        c.execute(
            "INSERT INTO gifts(from_user_id, to_user_id, kind, item_id, amount, price, day) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sender_id, receiver_id, kind, db_item_id, amount if kind == "diamonds" else 1, price, day),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "gift": {"kind": kind, "label": label, "price": price, "amount": amount if kind == "diamonds" else 1,
                 "item_id": db_item_id},
        "left_today": max(0, MAX_GIFTS_PER_DAY - used_today - 1),
    }
