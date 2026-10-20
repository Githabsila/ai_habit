"""
Подарки друзьям: алмазы и косметика из магазина (рамки аватарки и аватар).

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
    дарятся: у них свои дневные лимиты/оплата, подарок стал бы обходом;
  • ЗАМОРОЗКА серии (kind="freeze", 20.10): как в Duolingo — когда у друга кончились заморозки, ему можно подарить одну. Цена та же, что
    в магазине (FREEZE_GIFT_PRICE, монеты отправителя; очки и уровень не трогаются), у друга запас не больше MAX_FREEZE_BALANCE, и ему
    дарят не больше одной заморозки в день (иначе можно «набить» запас всем миром); общий дневной лимит подарков отправителя — тот же.

Списание, проверка суточного лимита и выдача идут одной транзакцией:
первый UPDATE берёт блокировку записи SQLite, поэтому параллельные подарки
выстраиваются в очередь и лимит нельзя пробить гонкой.
"""
from datetime import date

from .core import connect

GIFT_DIAMOND_AMOUNTS = (1, 3, 5)
MAX_GIFTS_PER_DAY = 3
FREEZE_GIFT_PRICE = 200                 # = db/streak.py::FREEZE_PRICE (тест следит, чтобы совпадало)
MAX_FREEZE_BALANCE = 2                  # = db/streak.py::MAX_FREEZE_BALANCE
FREEZE_GIFTS_PER_RECEIVER_PER_DAY = 1
FREEZE_HELP_MIN_STREAK = 2              # «надо помочь» — только тем, у кого есть что терять: серия не короче
FREEZE_HELP_LIMIT = 3                   # сколько друзей без заморозок предлагает окно при входе
# Рамки и аватар — «frame»/«avatar» (на очень старых базах id 4–6 ещё «cosmetic»,
# см. миграцию в db/core.py): дарим любые.
# Тема оформления убрана из интерфейса, а медаль 🏅 выдаёт только администратор
# (db/admin_gifts.py) — ни то, ни другое друзьям не дарится.
GIFTABLE_ITEM_TYPES = ("cosmetic", "frame", "avatar")


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
    if row["kind"] == "freeze":
        return "Заморозка серии ❄️"
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


def _freeze_gift_today(cursor, receiver_id, day):
    """Дарили ли этому человеку заморозку в этот (отправителя) день."""
    return cursor.execute(
        "SELECT COUNT(*) AS n FROM gifts WHERE to_user_id=? AND kind='freeze' AND day=?", (receiver_id, day)
    ).fetchone()["n"] >= FREEZE_GIFTS_PER_RECEIVER_PER_DAY


def get_gift_options(sender_id, receiver_id):
    """Что можно подарить этому другу прямо сейчас — для окна подарка.
    {"error": "not_friends"} — если он не друг."""
    if not _are_mutual_friends(sender_id, receiver_id):
        return {"error": "not_friends"}
    day = _local_day(sender_id)
    conn = connect()
    try:
        c = conn.cursor()
        sender = c.execute("SELECT xp, diamonds, first_name FROM users WHERE telegram_id=?", (sender_id,)).fetchone()
        receiver = c.execute("SELECT first_name, username FROM users WHERE telegram_id=?", (receiver_id,)).fetchone()
        if sender is None or receiver is None:
            return {"error": "not_found"}
        owned = {r["item_id"] for r in c.execute("SELECT item_id FROM user_items WHERE user_id=?", (receiver_id,)).fetchall()}
        coins, diamonds = int(sender["xp"] or 0), int(sender["diamonds"] or 0)
        meta = c.execute("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (receiver_id,)).fetchone()
        receiver_freezes = int(meta["freeze_balance"] or 0) if meta else 0
        freeze_today = _freeze_gift_today(c, receiver_id, day)
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
        "freeze": {
            "price": FREEZE_GIFT_PRICE,
            "affordable": coins >= FREEZE_GIFT_PRICE,
            "receiver_balance": receiver_freezes,
            "max_balance": MAX_FREEZE_BALANCE,
            "can_send": receiver_freezes < MAX_FREEZE_BALANCE and not freeze_today,
            "reason": "full" if receiver_freezes >= MAX_FREEZE_BALANCE else ("today" if freeze_today else None),
        },
        "left_today": gifts_left_today(sender_id),
        "max_per_day": MAX_GIFTS_PER_DAY,
    }


def send_gift(sender_id, receiver_id, kind, amount=None, item_id=None):
    """{"ok": True, "gift": {...}, "left_today"} либо {"error": код}:
    not_friends / invalid_kind / invalid_amount / item_not_giftable /
    already_owned / not_enough_coins / not_enough_diamonds / daily_limit /
    (заморозка) receiver_full / receiver_limit."""
    if not _are_mutual_friends(sender_id, receiver_id):
        return {"error": "not_friends"}
    if kind not in ("diamonds", "item", "freeze"):
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
        elif kind == "freeze":
            meta = c.execute("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (receiver_id,)).fetchone()
            if meta is None:
                c.execute("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (receiver_id,))
            if meta is not None and int(meta["freeze_balance"] or 0) >= MAX_FREEZE_BALANCE:
                return {"error": "receiver_full"}
            if _freeze_gift_today(c, receiver_id, day):
                return {"error": "receiver_limit"}
            price = FREEZE_GIFT_PRICE
            c.execute("UPDATE users SET xp = xp - ? WHERE telegram_id=? AND xp >= ?", (price, sender_id, price))
            if c.rowcount == 0:
                conn.rollback()
                return {"error": "not_enough_coins"}
            label, db_item_id = "Заморозка серии", None
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
        elif kind == "freeze":
            c.execute(
                "UPDATE streak_meta SET freeze_balance = COALESCE(freeze_balance, 0) + 1, freeze_empty_since = NULL "
                "WHERE user_id=? AND COALESCE(freeze_balance, 0) < ?",
                (receiver_id, MAX_FREEZE_BALANCE),
            )
            if c.rowcount == 0:      # параллельно подарили/купили — запас уже полный
                conn.rollback()
                return {"error": "receiver_full"}
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


# ---------------------------------------------------------------------------
# ДРУЗЬЯМ БЕЗ ЗАМОРОЗОК: иконка в рейтинге и окно «Надо помочь другу!»
# ---------------------------------------------------------------------------

def freeze_states(friend_ids):
    """{друг: {"balance": запас заморозок, "needs_help": ...}} — для значков в списке друзей рейтинга. needs_help — у человека идёт серия
    (не короче FREEZE_HELP_MIN_STREAK), а заморозки КОНЧИЛИСЬ: последняя ушла на спасение дня (db/streak.py::freeze_empty_since).
    Кто заморозок никогда не имел, «нуждающимся» не считается — им дарят через обычное окно подарка."""
    ids = [int(i) for i in friend_ids]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT u.telegram_id, u.streak, u.banned, COALESCE(sm.freeze_balance, 0) AS balance, sm.freeze_empty_since AS since "
            f"FROM users u LEFT JOIN streak_meta sm ON sm.user_id = u.telegram_id WHERE u.telegram_id IN ({marks})",
            ids,
        ).fetchall()
    finally:
        conn.close()
    return {
        r["telegram_id"]: {
            "balance": int(r["balance"] or 0),
            "needs_help": bool(
                not r["banned"] and int(r["balance"] or 0) == 0 and r["since"] and int(r["streak"] or 0) >= FREEZE_HELP_MIN_STREAK
            ),
        }
        for r in rows
    }


def freeze_help_candidates(viewer_id, limit=FREEZE_HELP_LIMIT):
    """Друзья (взаимная подписка), которым сейчас стоит помочь, — для окна при входе в приложение. Самая длинная серия — первой; тем,
    кому сегодня уже подарили заморозку, окно не предлагается."""
    from .friends import get_friend_sources

    friend_ids = [fid for fid, source in get_friend_sources(viewer_id).items() if source == "friend"]
    needy = [fid for fid, state in freeze_states(friend_ids).items() if state["needs_help"]]
    if not needy:
        return []
    marks = ",".join("?" for _ in needy)
    day = _local_day(viewer_id)
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT telegram_id, first_name, username, handle, avatar_id, frame_id, streak FROM users "
            f"WHERE telegram_id IN ({marks}) ORDER BY streak DESC, telegram_id",
            needy,
        ).fetchall()
        gifted = {
            r["to_user_id"]
            for r in conn.execute(
                f"SELECT DISTINCT to_user_id FROM gifts WHERE kind='freeze' AND day=? AND to_user_id IN ({marks})", [day, *needy]
            ).fetchall()
        }
    finally:
        conn.close()
    return [
        {
            "telegram_id": r["telegram_id"],
            "first_name": r["first_name"] or r["username"] or "Друг",
            "handle": r["handle"],
            "avatar_id": r["avatar_id"] or "default",
            "frame_id": r["frame_id"] or "default",
            "streak": int(r["streak"] or 0),
        }
        for r in rows
        if r["telegram_id"] not in gifted
    ][: int(limit)]
