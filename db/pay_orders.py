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
)


def ensure_schema(cursor):
    for statement in SCHEMA:
        cursor.execute(statement)
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


def new_order_id(user_id):
    return f"a{int(user_id)}x{secrets.token_hex(6)}"


def create_order(user_id, provider, method, kind, item_id, amount_cents, currency="USD"):
    order_id = new_order_id(user_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO pay_orders(order_id, provider, method, user_id, kind, item_id, amount_cents, currency, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?)",
            (order_id, provider, method, int(user_id), kind, item_id, int(amount_cents), currency, _now()))
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
