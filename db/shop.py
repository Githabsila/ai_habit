from datetime import date

from .core import connect


# Товары, которые нельзя купить: медаль 🏅 выдаёт только администратор (db/admin_gifts.py).
NOT_FOR_SALE_ITEM_TYPES = ("badge",)


def get_shop_items():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM shop_items ORDER BY price ASC")
    items = cursor.fetchall()

    conn.close()
    # Единая версия Pro (24.10): отдельный Premium не продаётся, пока не запущен более дорогой тариф (config.PREMIUM_TIER_ENABLED).
    import config
    if not config.PREMIUM_TIER_ENABLED:
        items = [it for it in items if it["item_type"] != "premium"]
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

    if "item_type" in item.keys() and item["item_type"] in NOT_FOR_SALE_ITEM_TYPES:
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


# =====================================
# ИДЕМПОТЕНТНОСТЬ ПЛАТЕЖЕЙ STARS
# =====================================
# См. db/core.py::stars_payments — защита от двойного начисления награды,
# если Telegram повторно доставит один и тот же successful_payment.

def is_payment_processed(charge_id):
    """True, если этот telegram_payment_charge_id уже был обработан раньше —
    handlers/payments.py должен молча подтвердить оплату (она реально
    прошла), но НЕ начислять награду второй раз."""
    if not charge_id:
        return False
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM stars_payments WHERE charge_id=?", (charge_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def mark_payment_processed(charge_id, user_id, payload, amount, purchased_at=None, is_test=False):
    """Записывает charge_id ДО начисления награды в handlers/payments.py.
    INSERT OR IGNORE — если между is_payment_processed() и этим вызовом
    случилась гонка, UNIQUE PRIMARY KEY на charge_id просто тихо не даст
    вставить дубликат, повторное начисление всё равно не произойдёт
    благодаря проверке в вызывающем коде. purchased_at — когда человек реально заплатил (по часам Telegram, UTC), is_test — тестовая оплата из админки."""
    if not charge_id:
        return
    moment = None
    if purchased_at is not None:
        if getattr(purchased_at, "tzinfo", None) is not None:
            from datetime import timezone as _tz
            purchased_at = purchased_at.astimezone(_tz.utc).replace(tzinfo=None)
        moment = purchased_at.isoformat(sep=" ", timespec="seconds")
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO stars_payments(charge_id, user_id, payload, amount, purchased_at, is_test) VALUES (?, ?, ?, ?, ?, ?)",
        (charge_id, user_id, payload, amount, moment, 1 if is_test else 0),
    )
    conn.commit()
    conn.close()


# ---- состояние обработки платежа (ТЗ 28.10 §19): received → done / failed
# Раньше charge_id записывался ДО начисления и больше нигде не отмечался: если начисление падало, платёж считался «обработанным», доступ не открывался,
# а повторное сообщение Telegram молча игнорировалось. Теперь у платежа есть состояние, и сбой можно повторить без двойного начисления.

def begin_payment(charge_id, user_id, payload, amount, purchased_at=None, is_test=False, is_recurring=False, expires_at=None):
    """Начинает обработку платежа. 'new' — первый раз (строка записана, состояние received), 'duplicate' — платёж уже обработан (ничего не начисляем),
    'retry' — раньше обработка не завершилась (failed/received): начисляем заново, один раз."""
    if not charge_id:
        return "new"
    conn = connect()
    try:
        row = conn.execute("SELECT status FROM stars_payments WHERE charge_id=?", (charge_id,)).fetchone()
        if row is not None:
            if (row["status"] or "done") == "done":
                return "duplicate"
            conn.execute("UPDATE stars_payments SET status='received', updated_at=CURRENT_TIMESTAMP WHERE charge_id=?", (charge_id,))
            conn.commit()
            return "retry"
    finally:
        conn.close()
    mark_payment_processed(charge_id, user_id, payload, amount, purchased_at=purchased_at, is_test=is_test)
    conn = connect()
    try:
        conn.execute(
            "UPDATE stars_payments SET status='received', is_recurring=?, expires_at=?, updated_at=CURRENT_TIMESTAMP WHERE charge_id=?",
            (1 if is_recurring else 0, _iso_utc(expires_at), charge_id),
        )
        conn.commit()
    finally:
        conn.close()
    return "new"


def _iso_utc(moment):
    if moment is None:
        return None
    if getattr(moment, "tzinfo", None) is not None:
        from datetime import timezone as _tz
        moment = moment.astimezone(_tz.utc).replace(tzinfo=None)
    return moment.isoformat(sep=" ", timespec="seconds")


def finish_payment(charge_id, ok, error=None):
    """Платёж обработан (ok) или обработка упала (error сохраняется, попытка засчитывается)."""
    if not charge_id:
        return
    conn = connect()
    try:
        if ok:
            conn.execute("UPDATE stars_payments SET status='done', error=NULL, updated_at=CURRENT_TIMESTAMP WHERE charge_id=?", (charge_id,))
        else:
            conn.execute(
                "UPDATE stars_payments SET status='failed', error=?, attempts=COALESCE(attempts,0)+1, updated_at=CURRENT_TIMESTAMP WHERE charge_id=?",
                (str(error or "")[:300], charge_id),
            )
        conn.commit()
    finally:
        conn.close()
    if ok:
        try:                                                # подтверждённая покупка приглашённого — начисление участнику (db/partners.py); сбой платёж не ломает
            from . import partners
            partners.accrue_payment(charge_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Не удалось начислить участнику программы за платёж %s", charge_id)


def is_credited(charge_id):
    """Начислен ли уже срок подписки за этот платёж (чтобы повтор после сбоя не добавил месяц второй раз)."""
    if not charge_id:
        return False
    conn = connect()
    try:
        row = conn.execute("SELECT credited_at FROM stars_payments WHERE charge_id=?", (charge_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["credited_at"])


def mark_credited(charge_id):
    if not charge_id:
        return
    conn = connect()
    try:
        conn.execute("UPDATE stars_payments SET credited_at=CURRENT_TIMESTAMP WHERE charge_id=? AND credited_at IS NULL", (charge_id,))
        conn.commit()
    finally:
        conn.close()


def failed_payments(limit=20, max_attempts=5):
    """Платежи, по которым деньги получены, а начисление не прошло (для повтора и для админки): самые старые первыми."""
    conn = connect()
    try:
        return conn.execute(
            "SELECT * FROM stars_payments WHERE status='failed' AND COALESCE(attempts,0) < ? AND COALESCE(is_test,0)=0 "
            "AND refunded_at IS NULL ORDER BY COALESCE(updated_at, created_at) LIMIT ?", (int(max_attempts), int(limit))).fetchall()
    finally:
        conn.close()


def get_payment(charge_id):
    """Платёж Stars по номеру операции Telegram (telegram_payment_charge_id) или None."""
    conn = connect()
    try:
        return conn.execute("SELECT * FROM stars_payments WHERE charge_id=?", (charge_id,)).fetchone()
    finally:
        conn.close()


def list_user_payments(user_id, limit=10):
    """Последние платежи человека (новые первыми) — для возврата и карточки в админке."""
    conn = connect()
    try:
        return conn.execute("SELECT * FROM stars_payments WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?", (user_id, int(limit))).fetchall()
    finally:
        conn.close()


def mark_payment_refunded(charge_id):
    conn = connect()
    try:
        conn.execute("UPDATE stars_payments SET refunded_at=CURRENT_TIMESTAMP WHERE charge_id=?", (charge_id,))
        conn.commit()
    finally:
        conn.close()
    try:                                                    # возврат сторнирует начисление участнику партнёрской программы
        from . import partners
        partners.reverse_payment(charge_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Не удалось сторнировать начисление по возврату %s", charge_id)


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
