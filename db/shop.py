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

    # Списываем только тратимую валюту (xp / Adam Coin). total_xp и level
    # НЕ трогаем — покупки в магазине не должны понижать уровень игрока
    # (раньше level считался прямо от xp, и покупка вещи буквально
    # отбрасывала игрока на уровень назад).
    #
    # Проверка баланса встроена прямо в WHERE этого UPDATE вместо
    # отдельного SELECT перед ним — раньше два параллельных запроса на
    # покупку могли оба пройти проверку по одному и тому же балансу до
    # того, как любой из них закоммитится, и оба списать деньги, уводя
    # баланс в минус (TOCTOU). rowcount==0 значит, что денег не хватило
    # (или пользователь не найден).
    cursor.execute(
        "UPDATE users SET xp = xp - ? WHERE telegram_id=? AND xp >= ?",
        (item["price"], user_id, item["price"]),
    )
    if cursor.rowcount == 0:
        conn.close()
        return False

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


def count_purchases_today(user_id, item_id):
    """Сколько раз пользователь уже купил этот товар сегодня — основа
    daily_limit_per_user (пром 9). Считает по user_items.purchased_at,
    поэтому применимо и к покупкам за Adam Coin (buy_shop_item), и к
    покупкам за Stars, если их тоже логировать туда же (см.
    log_stars_purchase)."""
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM user_items WHERE user_id=? AND item_id=? AND purchased_at=?",
        (user_id, item_id, str(date.today())),
    )
    n = int(cur.fetchone()["n"] or 0)
    conn.close()
    return n


def has_reached_daily_limit(user_id, item_id, item=None):
    item = item or get_shop_item(item_id)
    if not item:
        return True
    limit = int(item["daily_limit_per_user"]) if "daily_limit_per_user" in item.keys() else 0
    if limit <= 0:
        return False
    return count_purchases_today(user_id, item_id) >= limit


def log_stars_purchase(user_id, item_id):
    """Покупки за Telegram Stars списываются не через buy_shop_item (та
    функция тратит Adam Coin), поэтому для дневного лимита их нужно
    отдельно занести в тот же user_items — см. answer_pack_stars в
    handlers/payments.py."""
    conn = connect()
    conn.execute(
        "INSERT INTO user_items(user_id, item_id, purchased_at) VALUES (?, ?, ?)",
        (user_id, item_id, str(date.today())),
    )
    conn.commit()
    conn.close()

def set_cosmetic(user_id, item_type, payload):
    conn=connect(); cur=conn.cursor();
    col = "avatar_id" if item_type == "avatar" else "frame_id"
    cur.execute(f"UPDATE users SET {col}=? WHERE telegram_id=?", (payload, user_id)); conn.commit(); conn.close()

def add_ai_bonus_answers(user_id, amount):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    _ensure_ai_quota_day(cur, user_id, day)
    cur.execute("UPDATE ai_quota SET bonus_answers=bonus_answers+? WHERE user_id=?", (amount,user_id))
    conn.commit(); conn.close()

def _ensure_ai_quota_day(cur, user_id, day):
    """Гарантирует, что строка лимита относится к сегодняшнему дню.

    Таблица исторически имеет PRIMARY KEY только по user_id, поэтому
    INSERT OR IGNORE сам по себе НЕ создавал новую строку на следующий день.
    Из-за этого вчерашний used/bonus мог переноситься на сегодня.
    """
    cur.execute(
        "INSERT OR IGNORE INTO ai_quota(user_id, day, used, bonus_answers) VALUES (?, ?, 0, 0)",
        (user_id, day),
    )
    cur.execute("SELECT day FROM ai_quota WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if row and str(row["day"]) != day:
        cur.execute(
            "UPDATE ai_quota SET day=?, used=0, bonus_answers=0 WHERE user_id=?",
            (day, user_id),
        )

def get_ai_quota(user_id, is_pro=False):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    _ensure_ai_quota_day(cur, user_id, day)
    cur.execute("SELECT used, bonus_answers FROM ai_quota WHERE user_id=?", (user_id,))
    r=cur.fetchone(); conn.commit(); conn.close()
    try:
        from config import AI_DAILY_PRO_COST_UNITS, AI_DAILY_FREE_COST_UNITS
        base = AI_DAILY_PRO_COST_UNITS if is_pro else AI_DAILY_FREE_COST_UNITS
    except Exception:
        base = 50 if is_pro else 15
    used=int(r["used"] or 0) if r else 0
    bonus=int(r["bonus_answers"] or 0) if r else 0
    return {"used":used,"bonus":bonus,"limit":base+bonus,"remaining":max(0,base+bonus-used),"pro":is_pro}

def consume_ai_answer(user_id, is_pro=False, cost=1):
    from datetime import date
    conn=connect(); cur=conn.cursor(); day=str(date.today())
    _ensure_ai_quota_day(cur, user_id, day)
    cur.execute("SELECT used, bonus_answers FROM ai_quota WHERE user_id=?", (user_id,))
    r = cur.fetchone()

    used = int(r["used"] or 0) if r else 0
    bonus = int(r["bonus_answers"] or 0) if r else 0
    try:
        from config import AI_DAILY_PRO_COST_UNITS, AI_DAILY_FREE_COST_UNITS
        base = AI_DAILY_PRO_COST_UNITS if is_pro else AI_DAILY_FREE_COST_UNITS
    except Exception:
        base = 50 if is_pro else 15
    total = base + bonus

    try:
        cost = max(1, int(cost))
    except (TypeError, ValueError):
        cost = 1

    if used + cost > total:
        conn.close()
        return False

    cur.execute("UPDATE ai_quota SET used=used+? WHERE user_id=?", (cost, user_id))
    conn.commit()
    conn.close()
    return True
