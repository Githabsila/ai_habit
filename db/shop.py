from datetime import date

from .core import connect


def get_shop_items():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shop_items ORDER BY price ASC")
    items = cursor.fetchall()

    conn.close()
    return items


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
