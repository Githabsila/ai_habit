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


def buy_shop_item(user_id, item_id, allow_repeatable=False):
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

    # Списываем только тратимую валюту (xp / Adam Coin). total_xp и level
    # НЕ трогаем — покупки в магазине не должны понижать уровень игрока
    # (раньше level считался прямо от xp, и покупка вещи буквально
    # отбрасывала игрока на уровень назад).
    cursor.execute("""
        UPDATE users SET xp = xp - ? WHERE telegram_id=?
    """, (item["price"], user_id))

    if allow_repeatable or ("repeatable" in item.keys() and bool(item["repeatable"])):
        # Повторяемый товар не должен упираться в уникальность user_items.
        # Сохраняем каждую покупку отдельной записью, если схема это позволяет.
        cursor.execute("""
            INSERT INTO user_items(user_id, item_id, purchased_at)
            VALUES (?, ?, ?)
        """, (user_id, item_id, str(date.today())))
    else:
        cursor.execute("""
            INSERT OR IGNORE INTO user_items(user_id, item_id, purchased_at)
            VALUES (?, ?, ?)
        """, (user_id, item_id, str(date.today())))

    conn.commit()
    conn.close()

    return True


def get_shop_item(item_id):
    conn=connect(); cur=conn.cursor(); cur.execute("SELECT * FROM shop_items WHERE id=?", (item_id,)); row=cur.fetchone(); conn.close(); return row

def set_cosmetic(user_id, item_type, payload):
    conn=connect(); cur=conn.cursor();
    col = "avatar_id" if item_type == "avatar" else "frame_id"
    cur.execute(f"UPDATE users SET {col}=? WHERE telegram_id=?", (payload, user_id)); conn.commit(); conn.close()

def add_ai_bonus_answers(user_id, amount):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    cur.execute("INSERT OR IGNORE INTO ai_quota(user_id, day, used, bonus_answers) VALUES (?, ?, 0, 0)", (user_id, day))
    cur.execute("UPDATE ai_quota SET bonus_answers=bonus_answers+? WHERE user_id=? AND day=?", (amount,user_id,day)); conn.commit(); conn.close()

def get_ai_quota(user_id, is_pro=False):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    cur.execute("INSERT OR IGNORE INTO ai_quota(user_id, day, used, bonus_answers) VALUES (?, ?, 0, 0)", (user_id, day))
    cur.execute("SELECT used, bonus_answers FROM ai_quota WHERE user_id=? AND day=?", (user_id,day)); r=cur.fetchone(); conn.commit(); conn.close()
    base=50 if is_pro else 10
    used=int(r["used"] if r else 0); bonus=int(r["bonus_answers"] if r else 0)
    return {"used":used,"bonus":bonus,"limit":base+bonus,"remaining":max(0,base+bonus-used),"pro":is_pro}

def consume_ai_answer(user_id, is_pro=False):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    cur.execute("INSERT OR IGNORE INTO ai_quota(user_id, day, used, bonus_answers) VALUES (?, ?, 0, 0)", (user_id,day))
    cur.execute("SELECT used, bonus_answers FROM ai_quota WHERE user_id=? AND day=?", (user_id,day))
    r = cur.fetchone()

    # Defensive fallback: INSERT OR IGNORE above should normally guarantee a row,
    # but never let a missing row crash /api/ai/chat with a NoneType error.
    used = int(r["used"] or 0) if r else 0
    bonus = int(r["bonus_answers"] or 0) if r else 0
    base = 50 if is_pro else 10
    total = base + bonus

    if used >= total:
        conn.close()
        return False

    cur.execute(
        "UPDATE ai_quota SET used=used+1 WHERE user_id=? AND day=?",
        (user_id, day),
    )
    conn.commit()
    conn.close()
    return True
