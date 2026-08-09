from datetime import date

from .core import connect


def get_shop_items():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shop_items ORDER BY price ASC")
    items = cursor.fetchall()

    conn.close()
    return items


def get_user_items(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT item_id FROM user_items WHERE user_id=?", (user_id,))
    ids = [row["item_id"] for row in cursor.fetchall()]
    conn.close()
    return ids


def has_item(user_id, item_id):
    """Владеет ли пользователь конкретным товаром магазина (куплен ли он)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM user_items WHERE user_id=? AND item_id=? LIMIT 1",
        (user_id, item_id)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_item_owner_ids(item_id):
    """Множество telegram_id всех пользователей, купивших данный товар —
    используется, например, чтобы показать значок 🏅 в рейтинге у всех
    владельцев товара «Особый значок», без запроса на каждую строку."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM user_items WHERE item_id=?", (item_id,))
    ids = {row["user_id"] for row in cursor.fetchall()}
    conn.close()
    return ids


def buy_shop_item(user_id, item_id):
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shop_items WHERE id=?", (item_id,))
    item = cursor.fetchone()

    if not item:
        conn.close()
        return False

    cursor.execute("SELECT xp FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()

    if not user or user["xp"] < item["price"]:
        conn.close()
        return False

    cursor.execute("""
        UPDATE users SET xp = xp - ? WHERE telegram_id=?
    """, (item["price"], user_id))

    cursor.execute("""
        INSERT INTO user_items(user_id, item_id, purchased_at)
        VALUES (?, ?, ?)
    """, (user_id, item_id, str(date.today())))

    conn.commit()
    conn.close()

    return True
