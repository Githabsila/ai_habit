"""
Заказы на оплату не через Telegram Stars — картой и криптовалютой (решение владельца 28.10.2026: на старте нужны три способа — звёзды, карта Visa/Mastercard и крипта —
и за подписку, и за покупки в магазине). Звёзды остаются как были (handlers/payments.py); здесь — всё остальное: заказ создаётся у нас, платёжный сервис отдаёт ссылку на
оплату, потом присылает подтверждение (вебхук), и только подтверждённый заказ начисляет покупку, один раз.

Цены для этих способов — в долларах (центы) в таблице USD_PRICES_CENTS ниже. Это стартовые значения «чтобы запуститься»: подписка $1.99 → $5.99 по ТЗ, магазин — чистый доход
со звёздной цены (звезда ≈ $0,013) с округлением до «красивой» цены. Владелец пересчитает экономику отдельно (комиссии сервисов и звёзд разные) — менять нужно только эту таблицу.

Статусы: created → paid (деньги пришли, подтверждено провайдером) | failed | expired. Начисление (fulfilled_at) ставится атомарно: повторный вебхук ничего не добавляет.
"""
import secrets
from datetime import datetime, timezone

from .core import connect

FMT = "%Y-%m-%d %H:%M:%S"

SUBSCRIPTION_FIRST_CENTS = 199        # первый месяц (вводная цена для любой первой покупки, ТЗ §6)
SUBSCRIPTION_RENEWAL_CENTS = 599      # дальше
# id товара в shop_items → цена в центах USD (звёздная цена × $0,013 и округление): +50 ответов 150⭐, +100 ответов 280⭐, бустер 99⭐, 10 алмазов 150⭐,
# 50 алмазов 600⭐, рамка Double Gold 299⭐.
USD_PRICES_CENTS = {22: 199, 23: 399, 24: 129, 27: 199, 28: 799, 7: 399}

KINDS = ("subscription", "item")
METHODS = ("crypto", "card")

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pay_orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL, provider TEXT NOT NULL, method TEXT NOT NULL, user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, item_id INTEGER, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', status TEXT NOT NULL,
        provider_uuid TEXT, pay_url TEXT, raw_status TEXT, error TEXT, created_at TEXT NOT NULL, paid_at TEXT, fulfilled_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_pay_orders_user ON pay_orders(user_id, id)",
    # Tribute: ссылка на оплату у каждого товара своя и не знает, кто платит, — человека называет вебхук (telegram_user_id). Поэтому у нас на каждую цену (ключ) заведён свой
    # товар в Tribute; привязка «ключ → товар» хранится здесь и проверяется по цене при заведении.
    """CREATE TABLE IF NOT EXISTS pay_tribute_products(
        key TEXT PRIMARY KEY, product_id INTEGER NOT NULL, name TEXT, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, link TEXT, web_link TEXT, synced_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_pay_tribute_product ON pay_tribute_products(product_id)",
    # акции в окне «Покупки» (webapp «магазин»): заголовок, текст, до какого числа показывать
    "CREATE TABLE IF NOT EXISTS pay_promos(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, text TEXT NOT NULL, until TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)",
    # события платёжных сервисов, которые уже обработаны (идемпотентность: повторный вебхук с тем же номером покупки ничего не добавляет)
    "CREATE TABLE IF NOT EXISTS pay_events(provider TEXT NOT NULL, event_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(provider, event_id))",
)


def ensure_schema(cursor):
    for statement in SCHEMA:
        cursor.execute(statement)
    if "price_key" not in {row[1] for row in cursor.execute("PRAGMA table_info(pay_orders)").fetchall()}:
        cursor.execute("ALTER TABLE pay_orders ADD COLUMN price_key TEXT")
    # в общем журнале платежей видно, чем заплатили (звёзды — по умолчанию)
    if "provider" not in {row[1] for row in cursor.execute("PRAGMA table_info(stars_payments)").fetchall()}:
        cursor.execute("ALTER TABLE stars_payments ADD COLUMN provider TEXT DEFAULT 'stars'")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(FMT)


def format_usd(cents):
    return f"${int(cents) // 100}.{int(cents) % 100:02d}"


def price_cents(kind, item_id=None, user_id=None):
    """Цена заказа в центах USD или None, если такого товара нет. Подписка: первая покупка — вводная цена, дальше полная."""
    if kind == "subscription":
        from . import trial as trial_db
        return SUBSCRIPTION_FIRST_CENTS if trial_db.intro_price_open(user_id) else SUBSCRIPTION_RENEWAL_CENTS
    if kind == "item":
        return USD_PRICES_CENTS.get(int(item_id)) if item_id is not None and str(item_id).lstrip("-").isdigit() else None
    return None


def price_key(kind, item_id=None, user_id=None):
    """Ключ цены: sub_first | sub_renewal | item_<id>. По нему находим товар у платёжного сервиса, у которого цена задана заранее (Tribute)."""
    if kind == "subscription":
        return "sub_first" if price_cents("subscription", user_id=user_id) == SUBSCRIPTION_FIRST_CENTS else "sub_renewal"
    return f"item_{int(item_id)}" if kind == "item" and item_id is not None else None


def all_price_keys():
    """Все цены, под которые в Tribute нужны товары: [(ключ, подпись, центы)]."""
    keys = [("sub_first", "Подписка, первый месяц", SUBSCRIPTION_FIRST_CENTS), ("sub_renewal", "Подписка, дальше", SUBSCRIPTION_RENEWAL_CENTS)]
    names = {22: "+50 ответов ADAM", 23: "+100 ответов ADAM", 24: "Бустер x2 на 24 часа", 27: "10 алмазов", 28: "50 алмазов", 7: "Рамка Double Gold"}
    keys += [(f"item_{item_id}", names[item_id], cents) for item_id, cents in USD_PRICES_CENTS.items()]
    return keys


def key_to_purchase(key):
    """'sub_first' → ('subscription', None, 199); 'item_27' → ('item', 27, 199); неизвестный ключ → None."""
    if key in ("sub_first", "sub_renewal"):
        return "subscription", None, SUBSCRIPTION_FIRST_CENTS if key == "sub_first" else SUBSCRIPTION_RENEWAL_CENTS
    text = str(key)
    if text.startswith("item_") and text[5:].isdigit() and int(text[5:]) in USD_PRICES_CENTS:
        return "item", int(text[5:]), USD_PRICES_CENTS[int(text[5:])]
    return None


def new_order_id(user_id):
    return f"a{int(user_id)}x{secrets.token_hex(6)}"


def create_order(user_id, provider, method, kind, item_id, amount_cents, currency="USD", price_key=None):
    order_id = new_order_id(user_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO pay_orders(order_id, provider, method, user_id, kind, item_id, amount_cents, currency, status, created_at, price_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
            (order_id, provider, method, int(user_id), kind, item_id, int(amount_cents), currency, _now(), price_key))
        conn.commit()
    finally:
        conn.close()
    return get_order(order_id)


def get_order(order_id):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_orders WHERE order_id=?", (str(order_id),)).fetchone()
    finally:
        conn.close()


def set_provider_info(order_id, provider_uuid, pay_url):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET provider_uuid=?, pay_url=? WHERE order_id=?", (provider_uuid, pay_url, order_id))
        conn.commit()
    finally:
        conn.close()


def mark_failed(order_id, error=None, status="failed"):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET status=?, error=? WHERE order_id=? AND status NOT IN ('paid')", (status, str(error or "")[:300], order_id))
        conn.commit()
    finally:
        conn.close()


def set_raw_status(order_id, raw_status):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET raw_status=? WHERE order_id=?", (str(raw_status or "")[:40], order_id))
        conn.commit()
    finally:
        conn.close()


def mark_paid(order_id, raw_status=None):
    """Провайдер подтвердил оплату. Возвращает строку заказа; повторное подтверждение безопасно (статус уже paid)."""
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET status='paid', raw_status=?, paid_at=COALESCE(paid_at, ?), error=NULL WHERE order_id=?", (str(raw_status or "")[:40], _now(), order_id))
        conn.commit()
    finally:
        conn.close()
    return get_order(order_id)


def claim_fulfilment(order_id):
    """Атомарно «занимает» начисление: True только в первый раз. Если начисление упадёт, release_fulfilment вернёт заказ в очередь (следующий вебхук попробует снова)."""
    conn = connect()
    try:
        cursor = conn.execute("UPDATE pay_orders SET fulfilled_at=? WHERE order_id=? AND fulfilled_at IS NULL AND status='paid'", (_now(), order_id))
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def release_fulfilment(order_id, error=None):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET fulfilled_at=NULL, error=? WHERE order_id=?", (str(error or "")[:300], order_id))
        conn.commit()
    finally:
        conn.close()


def find_open(user_id, provider, kind, item_id, within_hours=6):
    """Самый свежий неоплаченный заказ человека у этого сервиса на эту покупку (Tribute не знает наших номеров заказов — сопоставляем по человеку и товару)."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=int(within_hours))).strftime(FMT)
    conn = connect()
    try:
        return conn.execute(
            "SELECT * FROM pay_orders WHERE user_id=? AND provider=? AND kind=? AND COALESCE(item_id,-1)=COALESCE(?, -1) AND status='created' AND created_at>=? ORDER BY id DESC LIMIT 1",
            (int(user_id), provider, kind, item_id, since)).fetchone()
    finally:
        conn.close()


def find_by_provider_uuid(provider, provider_uuid):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_orders WHERE provider=? AND provider_uuid=? ORDER BY id DESC LIMIT 1", (provider, str(provider_uuid))).fetchone()
    finally:
        conn.close()


def mark_refunded(order_id):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET status='refunded' WHERE order_id=?", (order_id,))
        conn.commit()
    finally:
        conn.close()


def set_provider_uuid(order_id, provider_uuid):
    conn = connect()
    try:
        conn.execute("UPDATE pay_orders SET provider_uuid=? WHERE order_id=?", (str(provider_uuid), order_id))
        conn.commit()
    finally:
        conn.close()


# ---- события и привязка товаров Tribute

def event_seen(provider, event_id):
    conn = connect()
    try:
        return conn.execute("SELECT 1 FROM pay_events WHERE provider=? AND event_id=?", (provider, str(event_id))).fetchone() is not None
    finally:
        conn.close()


def record_event(provider, event_id):
    conn = connect()
    try:
        conn.execute("INSERT OR IGNORE INTO pay_events(provider, event_id, created_at) VALUES (?, ?, ?)", (provider, str(event_id), _now()))
        conn.commit()
    finally:
        conn.close()


def tribute_product(key):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_tribute_products WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()


def tribute_by_product(product_id):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_tribute_products WHERE product_id=?", (int(product_id),)).fetchone()
    finally:
        conn.close()


def set_tribute_product(key, product_id, name, amount_cents, currency, link, web_link):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO pay_tribute_products(key, product_id, name, amount_cents, currency, link, web_link, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET product_id=excluded.product_id, name=excluded.name, amount_cents=excluded.amount_cents, currency=excluded.currency, "
            "link=excluded.link, web_link=excluded.web_link, synced_at=excluded.synced_at",
            (key, int(product_id), name, int(amount_cents), str(currency).upper(), link, web_link, _now()))
        conn.commit()
    finally:
        conn.close()


def remove_tribute_product(key):
    conn = connect()
    try:
        conn.execute("DELETE FROM pay_tribute_products WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()


def tribute_products():
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_tribute_products").fetchall()
    finally:
        conn.close()


# ---- акции

def add_promo(title, text, until=None):
    title, text = " ".join(str(title or "").split())[:80], " ".join(str(text or "").split())[:300]
    if not title or not text:
        return None
    conn = connect()
    try:
        cursor = conn.execute("INSERT INTO pay_promos(title, text, until, active, created_at) VALUES (?, ?, ?, 1, ?)", (title, text, until or None, _now()))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def remove_promo(promo_id):
    conn = connect()
    try:
        conn.execute("UPDATE pay_promos SET active=0 WHERE id=?", (int(promo_id),))
        conn.commit()
    finally:
        conn.close()


def list_promos(only_current=True):
    """Акции: только действующие (включены и срок не вышел) или все, новые первыми."""
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM pay_promos ORDER BY id DESC LIMIT 50").fetchall()
    finally:
        conn.close()
    now = _now()
    return [r for r in rows if not only_current or (r["active"] and (not r["until"] or str(r["until"]) >= now))]


def orders_for_user(user_id, limit=10):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_orders WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), int(limit))).fetchall()
    finally:
        conn.close()


def recent_unfulfilled(limit=50):
    """Оплаченные, но не начисленные заказы (для ручной проверки и повтора)."""
    conn = connect()
    try:
        return conn.execute("SELECT * FROM pay_orders WHERE status='paid' AND fulfilled_at IS NULL ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
