"""
Денежная партнёрская программа (ТЗ «Триал, подписка и приглашения» 28.10.2026, §20–§24) — отдельно от игровых приглашений (db/referrals.py: коины за первых двух друзей).
Коины и алмазы с деньгами не смешиваются: здесь только начисления в Stars за покупки НЕПОСРЕДСТВЕННО приглашённых, без «друзей друзей».

Кто участвует: человек из списка пилота (запись в `partners`, её заводит админ) И с действующей платной подпиской. Сама покупка допуск не даёт. Если в момент покупки
приглашённого у участника нет подписки или срок его условий вышел, начисление не пропадает, а встаёт «на проверку» с причиной.

Что начисляется: доля (по умолчанию 40 %) от ПОЛНОЙ суммы подтверждённой покупки приглашённого в первые 30 дней — подписка и мелкие покупки; комиссия платёжной системы базу не
уменьшает. Тестовые оплаты (is_test) не считаются. Возврат или отмена сторнирует начисление. Условия участника (доля, период, срок действия) хранятся в начислении на
момент покупки: изменение условий не трогает прошлое и других участников.

Откуда берётся приглашённый: личная ссылка участника и его подписанные ссылки (для сравнения площадок) → `register_touch`. История всех переходов хранится отдельно
(`partner_touches`), а какая ссылка засчитывается — настройка `attribution_rule` (first | last, по умолчанию first). Самоприглашение и повтор не засчитываются; после первой
покупки привязку менять нельзя; всплеск регистраций по одной ссылке помечается для проверки (начисления уходят «на проверку»). Все значения-параметры — в `partner_settings`
с безопасными значениями по умолчанию (SETTINGS_DEFAULTS); выплаты ручные: список «кому, сколько, за что» в админке, отметка выплаты и журнал исправлений (`partner_audit`).
"""
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from .core import connect

logger = logging.getLogger(__name__)

FMT = "%Y-%m-%d %H:%M:%S"
CODE_PREFIX = "p"                       # стартовая ссылка: /start p_<код>
MAX_LINKS = 10
BURST_WINDOW_MIN = 60
BURST_COUNT = 5                         # столько приходов по одной ссылке за час — признак для проверки

# Параметры, которые владелец уточнит перед включением (ТЗ §22); безопасные значения по умолчанию.
SETTINGS_DEFAULTS = {
    "attribution_rule": "first",        # first | last — какая из нескольких ссылок засчитывается
    "default_share": "40",               # процент от полной суммы покупки
    "default_window_days": "30",         # сколько дней после старта окна идут начисления
    "window_start": "attribution",       # attribution | registration — от какого события считаем срок
    "hold_days": "14",                   # столько дней после покупки начисление ждёт (возвраты) и только потом «к выплате»
    "min_payout": "100",                 # минимальная сумма выплаты, Stars
    "currency": "XTR",                   # единица начислений (Telegram Stars)
    "rounding": "floor",                 # floor | round — округление доли до целого
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS partners(
        user_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', share_percent INTEGER, window_days INTEGER,
        valid_from TEXT, valid_until TEXT, note TEXT, created_at TEXT, created_by INTEGER)""",
    """CREATE TABLE IF NOT EXISTS partner_links(
        id INTEGER PRIMARY KEY AUTOINCREMENT, partner_id INTEGER NOT NULL, code TEXT UNIQUE NOT NULL, label TEXT, is_personal INTEGER DEFAULT 0, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_partner_links_partner ON partner_links(partner_id)",
    """CREATE TABLE IF NOT EXISTS partner_touches(
        id INTEGER PRIMARY KEY AUTOINCREMENT, invitee_id INTEGER NOT NULL, partner_id INTEGER NOT NULL, link_id INTEGER, at TEXT NOT NULL, counted INTEGER DEFAULT 0, reason TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_partner_touches_invitee ON partner_touches(invitee_id, id)",
    """CREATE TABLE IF NOT EXISTS partner_attributions(
        invitee_id INTEGER PRIMARY KEY, partner_id INTEGER NOT NULL, link_id INTEGER, rule TEXT, attributed_at TEXT NOT NULL, flags TEXT, locked_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_partner_attr_partner ON partner_attributions(partner_id)",
    """CREATE TABLE IF NOT EXISTS partner_accruals(
        id INTEGER PRIMARY KEY AUTOINCREMENT, partner_id INTEGER NOT NULL, invitee_id INTEGER NOT NULL, charge_id TEXT UNIQUE NOT NULL,
        base_amount INTEGER NOT NULL, share_percent INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL, reason TEXT,
        purchased_at TEXT, created_at TEXT NOT NULL, reversed_at TEXT, payout_id INTEGER, notified_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_partner_accruals_partner ON partner_accruals(partner_id, status)",
    """CREATE TABLE IF NOT EXISTS partner_payouts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, partner_id INTEGER NOT NULL, amount INTEGER NOT NULL, note TEXT, confirmation TEXT, admin_id INTEGER, paid_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS partner_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, admin_id INTEGER, action TEXT NOT NULL, partner_id INTEGER, details TEXT)""",
    "CREATE TABLE IF NOT EXISTS partner_settings(key TEXT PRIMARY KEY, value TEXT)",
)

STATUS_LABELS = {"accrued": "начислено", "hold": "на проверке", "reversed": "сторнировано", "paid": "выплачено"}


def ensure_schema(cursor):
    for statement in SCHEMA:
        cursor.execute(statement)


# ---------------------------------------------------------------------------------------------------------------- вспомогательное

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(moment):
    return moment.strftime(FMT) if moment else None


def _parse(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], FMT)
    except ValueError:
        return None


def get_setting(key):
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM partner_settings WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    return row["value"] if row else SETTINGS_DEFAULTS[key]


def set_setting(key, value, admin_id=None):
    if key not in SETTINGS_DEFAULTS:
        return {"error": "unknown_setting"}
    value = str(value).strip()
    checks = {
        "attribution_rule": value in ("first", "last"), "window_start": value in ("attribution", "registration"), "rounding": value in ("floor", "round"),
        "default_share": value.isdigit() and 0 <= int(value) <= 100, "default_window_days": value.isdigit() and 1 <= int(value) <= 365,
        "hold_days": value.isdigit() and 0 <= int(value) <= 180, "min_payout": value.isdigit(), "currency": value == "XTR",
    }
    if not checks.get(key, False):
        return {"error": "invalid_value"}
    conn = connect()
    try:
        conn.execute("INSERT INTO partner_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        _audit(conn, admin_id, "setting", None, f"{key}={value}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def all_settings():
    return {key: get_setting(key) for key in SETTINGS_DEFAULTS}


def _audit(conn, admin_id, action, partner_id, details):
    conn.execute("INSERT INTO partner_audit(at, admin_id, action, partner_id, details) VALUES (?, ?, ?, ?, ?)", (_fmt(_now()), admin_id, action, partner_id, str(details)[:500]))


# ---------------------------------------------------------------------------------------------------------------- участники

def get_partner(user_id):
    conn = connect()
    try:
        return conn.execute("SELECT * FROM partners WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()


def _conditions(row):
    """Действующие условия участника: своя доля и период либо значения по умолчанию."""
    share = row["share_percent"] if row["share_percent"] is not None else int(get_setting("default_share"))
    window = row["window_days"] if row["window_days"] is not None else int(get_setting("default_window_days"))
    return int(share), int(window)


def is_valid_now(row, now=None):
    now = now or _now()
    if not row or row["status"] != "active":
        return False
    start, end = _parse(row["valid_from"]), _parse(row["valid_until"])
    return not ((start and now < start) or (end and now >= end))


def add_partner(user_id, admin_id, share_percent=None, window_days=None, valid_until=None, note=""):
    """Включить человека в пилот. Условия по умолчанию — из настроек; свои (доля, период 2–3 месяца, срок действия) задаются отдельно (§24)."""
    from .users import get_user
    if get_user(user_id) is None:
        return {"error": "user_not_found"}
    if share_percent is not None and not 0 <= int(share_percent) <= 100:
        return {"error": "invalid_share"}
    if window_days is not None and not 1 <= int(window_days) <= 365:
        return {"error": "invalid_window"}
    conn = connect()
    try:
        if conn.execute("SELECT 1 FROM partners WHERE user_id=?", (user_id,)).fetchone():
            return {"error": "already_partner"}
        conn.execute(
            "INSERT INTO partners(user_id, status, share_percent, window_days, valid_from, valid_until, note, created_at, created_by) VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?)",
            (user_id, share_percent, window_days, _fmt(_now()), valid_until, str(note or "")[:200], _fmt(_now()), admin_id))
        _audit(conn, admin_id, "add_partner", user_id, f"share={share_percent} window={window_days} until={valid_until}")
        conn.commit()
    finally:
        conn.close()
    ensure_personal_link(user_id)
    return {"ok": True}


def set_conditions(user_id, admin_id, share_percent=None, window_days=None, valid_until=None, status=None):
    """Индивидуальные условия действующего участника со сроком действия (§24): меняют только его будущие начисления — прошлые хранят свои условия."""
    row = get_partner(user_id)
    if row is None:
        return {"error": "not_partner"}
    if share_percent is not None and not 0 <= int(share_percent) <= 100:
        return {"error": "invalid_share"}
    if window_days is not None and not 1 <= int(window_days) <= 365:
        return {"error": "invalid_window"}
    if status is not None and status not in ("active", "paused"):
        return {"error": "invalid_status"}
    conn = connect()
    try:
        conn.execute(
            "UPDATE partners SET share_percent=COALESCE(?, share_percent), window_days=COALESCE(?, window_days), valid_until=COALESCE(?, valid_until), status=COALESCE(?, status) WHERE user_id=?",
            (share_percent, window_days, valid_until, status, user_id))
        _audit(conn, admin_id, "conditions", user_id, f"share={share_percent} window={window_days} until={valid_until} status={status}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def has_active_subscription(user_id):
    from .subscription import has_active_subscription as active
    return bool(active(user_id))


# ---------------------------------------------------------------------------------------------------------------- ссылки

def _new_code():
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def ensure_personal_link(partner_id):
    """Личная ссылка участника (одна навсегда)."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM partner_links WHERE partner_id=? AND is_personal=1", (partner_id,)).fetchone()
        if row:
            return row
        conn.execute("INSERT INTO partner_links(partner_id, code, label, is_personal, created_at) VALUES (?, ?, 'Личная', 1, ?)", (partner_id, _new_code(), _fmt(_now())))
        conn.commit()
        return conn.execute("SELECT * FROM partner_links WHERE partner_id=? AND is_personal=1", (partner_id,)).fetchone()
    finally:
        conn.close()


def create_link(partner_id, label):
    """Подписанная ссылка того же участника — чтобы сравнивать площадки. Названий не больше 40 символов, ссылок не больше MAX_LINKS."""
    if get_partner(partner_id) is None:
        return {"error": "not_partner"}
    label = " ".join(str(label or "").split())[:40]
    if not label:
        return {"error": "empty_label"}
    conn = connect()
    try:
        if conn.execute("SELECT COUNT(*) AS n FROM partner_links WHERE partner_id=?", (partner_id,)).fetchone()["n"] >= MAX_LINKS:
            return {"error": "too_many_links"}
        code = _new_code()
        conn.execute("INSERT INTO partner_links(partner_id, code, label, is_personal, created_at) VALUES (?, ?, ?, 0, ?)", (partner_id, code, label, _fmt(_now())))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "code": code, "label": label}


def start_payload(code):
    return f"{CODE_PREFIX}_{code}"


def parse_start_code(payload):
    """'p_ab12cd34' → 'ab12cd34'; всё остальное — None."""
    value = str(payload or "")
    if value.startswith(CODE_PREFIX + "_") and 4 <= len(value) <= 24 and value[2:].isalnum():
        return value[2:].lower()
    return None


def list_links(partner_id):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT l.id, l.code, l.label, l.is_personal, "
            "(SELECT COUNT(*) FROM partner_touches t WHERE t.link_id=l.id) AS touches, "
            "(SELECT COUNT(*) FROM partner_attributions a WHERE a.link_id=l.id) AS invitees, "
            "(SELECT COUNT(DISTINCT c.invitee_id) FROM partner_accruals c JOIN partner_attributions a2 ON a2.invitee_id=c.invitee_id "
            " WHERE a2.link_id=l.id AND c.status!='reversed') AS buyers "
            "FROM partner_links l WHERE l.partner_id=? ORDER BY l.is_personal DESC, l.id", (partner_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- приход по ссылке и привязка

def register_touch(invitee_id, code, now=None):
    """Человек перешёл по ссылке участника. Всегда пишет историю переходов; решает, засчитывать ли привязку, по настройке `attribution_rule`.
    Возвращает {"counted": bool, "partner_id", "reason"}: self | locked | stale | not_found | burst — не засчитано/помечено."""
    from .users import get_user
    now = now or _now()
    code = parse_start_code(code) or str(code or "").lower()
    conn = connect()
    try:
        link = conn.execute("SELECT * FROM partner_links WHERE code=?", (code,)).fetchone()
        if link is None:
            return {"counted": False, "partner_id": None, "reason": "not_found"}
        partner_id = link["partner_id"]
        result = {"counted": False, "partner_id": partner_id, "reason": ""}
        if partner_id == invitee_id:
            result["reason"] = "self"
        elif get_user(invitee_id) is None:
            result["reason"] = "no_user"
        existing = conn.execute("SELECT * FROM partner_attributions WHERE invitee_id=?", (invitee_id,)).fetchone()
        if not result["reason"] and existing and existing["locked_at"]:
            result["reason"] = "locked"                              # уже была покупка — привязку не меняем
        if not result["reason"]:
            paid_before = conn.execute("SELECT 1 FROM stars_payments WHERE user_id=? AND COALESCE(is_test,0)=0 AND refunded_at IS NULL LIMIT 1", (invitee_id,)).fetchone()
            if paid_before:
                result["reason"] = "stale"                           # платил раньше — это не новый приглашённый
        rule = get_setting("attribution_rule")
        counted = False
        if not result["reason"]:
            if existing is None:
                counted = True
            elif rule == "last" and existing["link_id"] != link["id"]:
                counted = True
            else:
                result["reason"] = "kept_first" if rule == "first" else "same_link"
        conn.execute("INSERT INTO partner_touches(invitee_id, partner_id, link_id, at, counted, reason) VALUES (?, ?, ?, ?, ?, ?)",
                     (invitee_id, partner_id, link["id"], _fmt(now), 1 if counted else 0, result["reason"] or None))
        if counted:
            flags = ""
            since = _fmt(now - timedelta(minutes=BURST_WINDOW_MIN))
            recent = conn.execute("SELECT COUNT(DISTINCT invitee_id) AS n FROM partner_touches WHERE link_id=? AND at>=?", (link["id"], since)).fetchone()["n"]
            if recent >= BURST_COUNT:
                flags = "burst"
            conn.execute(
                "INSERT INTO partner_attributions(invitee_id, partner_id, link_id, rule, attributed_at, flags) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(invitee_id) DO UPDATE SET partner_id=excluded.partner_id, link_id=excluded.link_id, rule=excluded.rule, attributed_at=excluded.attributed_at, flags=excluded.flags",
                (invitee_id, partner_id, link["id"], rule, _fmt(now), flags))
            result["counted"] = True
            if flags:
                result["reason"] = "burst"
        conn.commit()
        return result
    finally:
        conn.close()


def inviter_card(invitee_id):
    """Карточка пригласившего для новичка при первом входе (§21): имя и @ник (его можно скопировать). Подписка и дружба — только по решению самого новичка."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT u.telegram_id, u.first_name, u.handle, u.username FROM partner_attributions a JOIN users u ON u.telegram_id=a.partner_id WHERE a.invitee_id=?", (invitee_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    handle = (row["handle"] or row["username"] or "").lstrip("@")
    return {"user_id": row["telegram_id"], "name": (row["first_name"] or "").strip() or "Участник", "handle": f"@{handle}" if handle else None}


# ---------------------------------------------------------------------------------------------------------------- начисления

def _round(value):
    return int(value) if get_setting("rounding") == "floor" else int(round(value))


def accrue_payment(charge_id):
    """Начисление за подтверждённую покупку приглашённого. Идемпотентно по charge_id. Возвращает строку начисления или None, если начислять нечего."""
    conn = connect()
    try:
        payment = conn.execute("SELECT * FROM stars_payments WHERE charge_id=?", (charge_id,)).fetchone()
        if payment is None or int(payment["is_test"] or 0) or payment["refunded_at"] or (payment["status"] or "done") != "done":
            return None
        if conn.execute("SELECT 1 FROM partner_accruals WHERE charge_id=?", (charge_id,)).fetchone():
            return None
        attribution = conn.execute("SELECT * FROM partner_attributions WHERE invitee_id=?", (payment["user_id"],)).fetchone()
        if attribution is None or attribution["partner_id"] == payment["user_id"]:
            return None
        partner = conn.execute("SELECT * FROM partners WHERE user_id=?", (attribution["partner_id"],)).fetchone()
        if partner is None:
            return None                                                   # не из пилота — денег нет
        share, window_days = _conditions(partner)
        purchased = _parse(payment["purchased_at"]) or _parse(payment["created_at"]) or _now()
        if get_setting("window_start") == "registration":
            user = conn.execute("SELECT created_at FROM users WHERE telegram_id=?", (payment["user_id"],)).fetchone()
            start = _parse(user["created_at"]) if user else None
        else:
            start = _parse(attribution["attributed_at"])
        if start is None or purchased < start or purchased > start + timedelta(days=window_days):
            return None                                                   # вне периода начислений
        base = int(payment["amount"] or 0)
        if base <= 0:
            return None
        amount = _round(base * share / 100)
        status, reason = "accrued", None
        if not is_valid_now(partner, purchased):
            status, reason = "hold", "условия участника не действовали на момент покупки"
        elif not has_active_subscription(partner["user_id"]):
            status, reason = "hold", "у участника нет действующей платной подписки"
        elif "burst" in str(attribution["flags"] or ""):
            status, reason = "hold", "подозрительная регистрация: всплеск приходов по одной ссылке"
        conn.execute(
            "INSERT INTO partner_accruals(partner_id, invitee_id, charge_id, base_amount, share_percent, amount, status, reason, purchased_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (partner["user_id"], payment["user_id"], charge_id, base, share, amount, status, reason, _fmt(purchased), _fmt(_now())))
        conn.execute("UPDATE partner_attributions SET locked_at=COALESCE(locked_at, ?) WHERE invitee_id=?", (_fmt(_now()), payment["user_id"]))
        conn.commit()
        return conn.execute("SELECT * FROM partner_accruals WHERE charge_id=?", (charge_id,)).fetchone()
    finally:
        conn.close()


def reverse_payment(charge_id):
    """Возврат или отмена покупки: начисление сторнируется. Если оно уже выплачено — остаётся в списке «к удержанию» (payout_id сохраняется)."""
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM partner_accruals WHERE charge_id=?", (charge_id,)).fetchone()
        if row is None or row["status"] == "reversed":
            return None
        conn.execute("UPDATE partner_accruals SET status='reversed', reason='возврат покупки', reversed_at=? WHERE id=?", (_fmt(_now()), row["id"]))
        conn.commit()
        return conn.execute("SELECT * FROM partner_accruals WHERE id=?", (row["id"],)).fetchone()
    finally:
        conn.close()


def sync_accruals(limit=500):
    """Страховка: подтверждённые покупки приглашённых, по которым ещё нет начисления, и возвраты, о которых начисление не знает. Безопасно запускать сколько угодно раз."""
    conn = connect()
    try:
        fresh = conn.execute(
            "SELECT p.charge_id FROM stars_payments p JOIN partner_attributions a ON a.invitee_id=p.user_id "
            "WHERE COALESCE(p.is_test,0)=0 AND p.refunded_at IS NULL AND COALESCE(p.status,'done')='done' "
            "AND NOT EXISTS (SELECT 1 FROM partner_accruals c WHERE c.charge_id=p.charge_id) LIMIT ?", (int(limit),)).fetchall()
        refunded = conn.execute(
            "SELECT c.charge_id FROM partner_accruals c JOIN stars_payments p ON p.charge_id=c.charge_id WHERE p.refunded_at IS NOT NULL AND c.status!='reversed' LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    made = sum(1 for r in fresh if accrue_payment(r["charge_id"]))
    undone = sum(1 for r in refunded if reverse_payment(r["charge_id"]))
    return {"accrued": made, "reversed": undone}


# ---------------------------------------------------------------------------------------------------------------- статистика участника

def _payable_clause():
    return "status='accrued' AND payout_id IS NULL AND purchased_at <= ?"


def _hold_cutoff():
    return _fmt(_now() - timedelta(days=int(get_setting("hold_days"))))


def balance(partner_id):
    """Суммы по участнику: начислено, на проверке, ждёт срока, к выплате, выплачено, к удержанию (возврат после выплаты)."""
    cutoff = _hold_cutoff()
    conn = connect()
    try:
        def total(where, params=()):
            return int(conn.execute(f"SELECT COALESCE(SUM(amount),0) AS s FROM partner_accruals WHERE partner_id=? AND {where}", (partner_id, *params)).fetchone()["s"])
        return {
            "accrued": total("status IN ('accrued','paid')"), "hold": total("status='hold'"),
            "waiting": total("status='accrued' AND payout_id IS NULL AND purchased_at > ?", (cutoff,)),
            "payable": total(_payable_clause(), (cutoff,)), "paid": total("status='paid'"),
            "clawback": total("status='reversed' AND payout_id IS NOT NULL"),
        }
    finally:
        conn.close()


def participant_stats(user_id):
    """Что видит сам участник: приглашения, покупки, суммы и история без личных данных приглашённых (только «Приглашённый №N»)."""
    partner = get_partner(user_id)
    if partner is None:
        return None
    conn = connect()
    try:
        invited = conn.execute("SELECT COUNT(*) AS n FROM partner_attributions WHERE partner_id=?", (user_id,)).fetchone()["n"]
        buyers = conn.execute("SELECT COUNT(DISTINCT invitee_id) AS n FROM partner_accruals WHERE partner_id=? AND status!='reversed'", (user_id,)).fetchone()["n"]
        purchases = conn.execute("SELECT COUNT(*) AS n FROM partner_accruals WHERE partner_id=? AND status!='reversed'", (user_id,)).fetchone()["n"]
        order = {r["invitee_id"]: index + 1 for index, r in enumerate(conn.execute("SELECT invitee_id FROM partner_attributions WHERE partner_id=? ORDER BY attributed_at, invitee_id", (user_id,)).fetchall())}
        history = conn.execute("SELECT * FROM partner_accruals WHERE partner_id=? ORDER BY id DESC LIMIT 50", (user_id,)).fetchall()
        payouts = conn.execute("SELECT amount, paid_at, note FROM partner_payouts WHERE partner_id=? ORDER BY id DESC LIMIT 20", (user_id,)).fetchall()
    finally:
        conn.close()
    share, window = _conditions(partner)
    return {
        "active": is_valid_now(partner), "subscription_active": has_active_subscription(user_id), "share_percent": share, "window_days": window, "valid_until": partner["valid_until"],
        "invited": invited, "buyers": buyers, "purchases": purchases, "balance": balance(user_id), "min_payout": int(get_setting("min_payout")), "currency": get_setting("currency"),
        "history": [{"invitee": f"Приглашённый №{order.get(r['invitee_id'], '?')}", "amount": r["amount"], "base": r["base_amount"], "status": r["status"], "status_label": STATUS_LABELS.get(r["status"], r["status"]),
                     "reason": r["reason"], "at": r["purchased_at"]} for r in history],
        "payouts": [dict(p) for p in payouts], "links": list_links(user_id),
    }


def unnotified_accruals(limit=50):
    """Новые начисления, о которых участнику ещё не сообщали (один сигнал на начисление — без дублей)."""
    conn = connect()
    try:
        return conn.execute("SELECT * FROM partner_accruals WHERE notified_at IS NULL AND status='accrued' ORDER BY id LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()


def mark_accrual_notified(accrual_id):
    conn = connect()
    try:
        cur = conn.execute("UPDATE partner_accruals SET notified_at=? WHERE id=? AND notified_at IS NULL", (_fmt(_now()), accrual_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- админка: выплаты и исправления

def admin_overview():
    """Список участников: кому сколько к выплате и за что (§23)."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT p.*, u.first_name, u.username, u.handle FROM partners p LEFT JOIN users u ON u.telegram_id=p.user_id ORDER BY p.created_at").fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        stats = balance(row["user_id"])
        share, window = _conditions(row)
        result.append({
            "user_id": row["user_id"], "name": row["first_name"], "username": row["username"], "status": row["status"], "valid_now": is_valid_now(row), "share_percent": share,
            "window_days": window, "valid_until": row["valid_until"], "note": row["note"], "balance": stats, "subscription_active": has_active_subscription(row["user_id"]),
        })
    return {"partners": result, "settings": all_settings()}


def payable_accruals(partner_id):
    cutoff = _hold_cutoff()
    conn = connect()
    try:
        return conn.execute(f"SELECT * FROM partner_accruals WHERE partner_id=? AND {_payable_clause()} ORDER BY id", (partner_id, cutoff)).fetchall()
    finally:
        conn.close()


def create_payout(partner_id, admin_id, note="", confirmation=""):
    """Ручная выплата всего, что сейчас «к выплате»: помечает начисления выплаченными, пишет выплату и журнал. Минимальная сумма — настройка min_payout."""
    if get_partner(partner_id) is None:
        return {"error": "not_partner"}
    rows = payable_accruals(partner_id)
    total = sum(int(r["amount"]) for r in rows)
    if not rows or total < int(get_setting("min_payout")):
        return {"error": "below_minimum", "payable": total, "min_payout": int(get_setting("min_payout"))}
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO partner_payouts(partner_id, amount, note, confirmation, admin_id, paid_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (partner_id, total, str(note or "")[:200], str(confirmation or "")[:200], admin_id, _fmt(_now())))
        payout_id = cur.lastrowid
        marks = ",".join("?" for _ in rows)
        conn.execute(f"UPDATE partner_accruals SET status='paid', payout_id=? WHERE id IN ({marks}) AND status='accrued' AND payout_id IS NULL", (payout_id, *[r["id"] for r in rows]))
        _audit(conn, admin_id, "payout", partner_id, f"payout={payout_id} amount={total} accruals={len(rows)} confirmation={confirmation}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "payout_id": payout_id, "amount": total, "accruals": len(rows)}


def correct_accrual(accrual_id, admin_id, action, reason=""):
    """Исправление спорного начисления: hold (на проверку), release (вернуть в «начислено»), reverse (сторнировать). Причина обязательна; всё пишется в журнал."""
    reason = " ".join(str(reason or "").split())[:200]
    if action not in ("hold", "release", "reverse") or not reason:
        return {"error": "invalid_request"}
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM partner_accruals WHERE id=?", (accrual_id,)).fetchone()
        if row is None:
            return {"error": "not_found"}
        if row["status"] == "paid" and action != "reverse":
            return {"error": "already_paid"}
        if action == "hold":
            conn.execute("UPDATE partner_accruals SET status='hold', reason=? WHERE id=? AND status IN ('accrued','hold')", (reason, accrual_id))
        elif action == "release":
            conn.execute("UPDATE partner_accruals SET status='accrued', reason=NULL WHERE id=? AND status='hold'", (accrual_id,))
        else:
            conn.execute("UPDATE partner_accruals SET status='reversed', reason=?, reversed_at=? WHERE id=? AND status!='reversed'", (reason, _fmt(_now()), accrual_id))
        _audit(conn, admin_id, f"accrual_{action}", row["partner_id"], f"accrual={accrual_id} {reason}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def audit_log(partner_id=None, limit=50):
    conn = connect()
    try:
        if partner_id is None:
            rows = conn.execute("SELECT * FROM partner_audit ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM partner_audit WHERE partner_id=? ORDER BY id DESC LIMIT ?", (partner_id, int(limit))).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
