"""
Приглашения друзей: награда, лимит и защита от накрутки.

Правила (решение владельца, 16.10):
  • Пригласивший получает 100 Адам коинов только за ПЕРВЫХ ДВУХ приглашённых за всё время (REWARD_LIMIT). Дальше ссылка по-прежнему
    сводит людей в друзья, но коины не платятся ни одной из сторон.
  • Приглашённый новичок получает +50, пока у пригласившего есть «слоты» награды.
  • Раньше награда шла без лимита, а обычная ссылка ?start=<id> платила даже тем, кто уже давно пользуется ADAM.

Что считается приглашением (register_invite): человек впервые нажал /start по ссылке, его аккаунт создан только что (анкета ещё не пройдена,
не старше FRESH_ACCOUNT_MIN минут), он не пригласил сам себя и у него ещё нет пригласившего. Один человек = один приглашённый навсегда:
запись в referral_invites уникальна по invitee_id и не удаляется вместе с аккаунтом, поэтому «удалить аккаунт и зайти заново» награду не повторит.

Когда платим (три случая):
  • сразу — по умолчанию, как обещано в тексте приглашения;
  • после первой отметки приглашённого — если пригласивший сам совсем новичок (аккаунту меньше NEW_INVITER_HOURS часов: типичная схема
    накрутки «создал аккаунт и тут же позвал второй») или если админ включил флаг referral_pay_after_mark для всех;
  • никогда — слоты кончились (limit) или админ поставил выплаты на паузу флагом referral_rewards_pause (paused).

Контроль: у каждого приглашения есть статус и причина, подозрительные признаки пишутся в flags, админ видит список и может отозвать выплату
(слот при этом остаётся занятым — повторить не выйдет). События referral_* попадают в аналитику.
"""
import logging
from datetime import datetime, timedelta, timezone

from . import tracking
from .core import connect
from .feature_flags import is_feature_enabled
from .users import add_xp, get_user

logger = logging.getLogger(__name__)

REWARD_COINS = 100                 # пригласившему
INVITEE_COINS = 50                 # приглашённому
REWARD_LIMIT = 2                   # сколько приглашений за жизнь оплачивается
FRESH_ACCOUNT_MIN = 30             # приглашённый — только что созданный аккаунт (как окно привязки канала в db/traffic.py)
NEW_INVITER_HOURS = 24             # пригласивший младше суток — награда ждёт первой отметки приглашённого
PENDING_TTL_DAYS = 14              # не отметился за две недели — слот освобождается, награда сгорает
BURST_WINDOW_MIN = 60              # три и больше приглашённых у одного человека за час — признак для проверки
BURST_COUNT = 3
INACTIVE_AFTER_DAYS = 3            # за столько дней без единой отметки приглашённый попадает в «молчунов» в админке

PAUSE_FLAG = "referral_rewards_pause"
AFTER_MARK_FLAG = "referral_pay_after_mark"
MAILING_FLAG = "referral_mailing"

STATUS_LABELS = {
    "paid": "выплачено", "pending": "ждёт первой отметки", "no_reward": "без награды", "revoked": "отозвано", "expired": "истекло",
}
REASON_LABELS = {
    "limit": "награда за первых двух уже выдана", "paused": "выплаты были на паузе", "after_mark": "режим «после первой отметки»",
    "new_inviter": "пригласивший — новичок (меньше суток)", "ttl": "не отметился за две недели", "inviter_banned": "пригласивший заблокирован",
}
FLAG_LABELS = {
    "burst": "3+ приглашённых за час", "new_inviter": "пригласивший младше суток", "no_username": "у приглашённого нет @ника",
    "inactive": f"{INACTIVE_AFTER_DAYS}+ дня без отметок",
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(moment):
    return tracking._fmt(moment)


def rewards_paused():
    return is_feature_enabled(PAUSE_FLAG)


def pay_after_mark():
    return is_feature_enabled(AFTER_MARK_FLAG)


def pay_mode():
    """'now' — награда сразу; 'after_mark' — после первой отметки приглашённого (глобальный режим по флагу)."""
    return "after_mark" if pay_after_mark() else "now"


def mailing_enabled_for(user_id):
    """Рассылка «позови друга» в первую неделю включена всем: записи флага нет — значит можно. Выключить или раскатать на процент — запись флага
    (выключено = никому, включено + процент = по хэшу, как в других флагах)."""
    conn = connect()
    try:
        row = conn.execute("SELECT enabled, rollout_pct FROM feature_flags WHERE key=?", (MAILING_FLAG,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return True
    return is_feature_enabled(MAILING_FLAG, user_id)


# ---------------------------------------------------------------------------------------------------------------- слоты и сводка

def _slots_used(conn, inviter_id):
    """Сколько «слотов» награды занято: выплаченные, отозванные (повторить нельзя) и ожидающие первой отметки (пока не истёк срок)."""
    cutoff = _fmt(_now() - timedelta(days=PENDING_TTL_DAYS))
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM referral_invites WHERE inviter_id=? AND (status IN ('paid','revoked') OR (status='pending' AND created_at>=?))",
        (inviter_id, cutoff),
    ).fetchone()
    return int(row["n"])


def reward_summary(user_id):
    """Для Mini App, рассылки и сообщений бота: сколько слотов занято/осталось, режим выплаты, пауза."""
    conn = connect()
    try:
        used = _slots_used(conn, user_id)
        earned = conn.execute("SELECT COALESCE(SUM(inviter_coins),0) AS n FROM referral_invites WHERE inviter_id=? AND status='paid'", (user_id,)).fetchone()["n"]
        invited = conn.execute("SELECT COUNT(*) AS n FROM referral_invites WHERE inviter_id=?", (user_id,)).fetchone()["n"]
    finally:
        conn.close()
    paused = rewards_paused()
    return {
        "coins": REWARD_COINS, "invitee_coins": INVITEE_COINS, "limit": REWARD_LIMIT, "used": min(used, REWARD_LIMIT),
        "left": 0 if paused else max(0, REWARD_LIMIT - used), "earned": int(earned), "invited": int(invited),
        "mode": pay_mode(), "paused": paused,
    }


# ---------------------------------------------------------------------------------------------------------------- регистрация приглашения

def _reject_reason(inviter_id, invitee_id):
    """Почему переход по ссылке НЕ считается приглашением (дружба при этом всё равно создаётся — это решает handlers/start.py)."""
    if inviter_id == invitee_id:
        return "self"
    inviter, invitee = get_user(inviter_id), get_user(invitee_id)
    if inviter is None:
        return "inviter_unknown"
    if inviter["banned"]:
        return "inviter_banned"
    if invitee is None:
        return "invitee_unknown"
    if invitee["referrer_id"] is not None:
        return "already_invited"
    if (invitee["access_status"] or "approved") != "new":
        return "not_new"
    created = tracking._parse(invitee["created_at"])
    if created is None or _now() - created > timedelta(minutes=FRESH_ACCOUNT_MIN):
        return "not_fresh"
    return None


def _suspicion_flags(conn, inviter_id, inviter_young, invitee_username):
    flags = []
    since = _fmt(_now() - timedelta(minutes=BURST_WINDOW_MIN))
    recent = conn.execute("SELECT COUNT(*) AS n FROM referral_invites WHERE inviter_id=? AND created_at>=?", (inviter_id, since)).fetchone()["n"]
    if recent + 1 >= BURST_COUNT:
        flags.append("burst")
    if inviter_young:
        flags.append("new_inviter")
    if not invitee_username:
        flags.append("no_username")
    return ",".join(flags)


def register_invite(inviter_id, invitee_id, via="invite"):
    """Засчитывает переход по ссылке-приглашению. Возвращает словарь:
      counted — приглашение записано (иначе reason говорит почему нет);
      status — paid / pending / no_reward, reason — limit / paused / after_mark / new_inviter / None;
      inviter_coins, invitee_coins — сколько выплачено прямо сейчас; slot — номер оплачиваемого приглашения; left — сколько ещё осталось.
    Проверка слотов и запись — в одной транзакции, поэтому два одновременных перехода не обойдут лимит."""
    reason = _reject_reason(inviter_id, invitee_id)
    if reason:
        if reason in ("not_new", "not_fresh", "inviter_banned", "inviter_unknown"):
            tracking.track_server(invitee_id, "referral_rejected", step=reason, meta={"inviter": inviter_id, "via": via})
        return {"counted": False, "reason": reason, "inviter_coins": 0, "invitee_coins": 0}

    paused, after_mark = rewards_paused(), pay_after_mark()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        invitee = conn.execute("SELECT referrer_id, username FROM users WHERE telegram_id=?", (invitee_id,)).fetchone()
        if invitee is None or invitee["referrer_id"] is not None or conn.execute("SELECT 1 FROM referral_invites WHERE invitee_id=?", (invitee_id,)).fetchone():
            conn.rollback()
            return {"counted": False, "reason": "already_invited", "inviter_coins": 0, "invitee_coins": 0}
        inviter = conn.execute("SELECT created_at FROM users WHERE telegram_id=?", (inviter_id,)).fetchone()
        inviter_created = tracking._parse(inviter["created_at"]) if inviter else None
        inviter_young = inviter_created is not None and _now() - inviter_created < timedelta(hours=NEW_INVITER_HOURS)
        used = _slots_used(conn, inviter_id)

        if used >= REWARD_LIMIT:
            status, why = "no_reward", "limit"
        elif paused:
            status, why = "no_reward", "paused"
        elif after_mark:
            status, why = "pending", "after_mark"
        elif inviter_young:
            status, why = "pending", "new_inviter"
        else:
            status, why = "pending", None          # платим сразу, ниже; «pending» только на миг записи

        flags = _suspicion_flags(conn, inviter_id, inviter_young, invitee["username"])
        cursor = conn.execute(
            "INSERT INTO referral_invites(invitee_id, inviter_id, via, status, reason, flags, created_at) VALUES (?,?,?,?,?,?,?)",
            (invitee_id, inviter_id, via, status, why, flags, _fmt(_now())),
        )
        invite_id = cursor.lastrowid
        conn.execute("UPDATE users SET referrer_id=? WHERE telegram_id=? AND referrer_id IS NULL", (inviter_id, invitee_id))
        conn.execute("UPDATE users SET referrals = referrals + 1 WHERE telegram_id=?", (inviter_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    paid = _pay(invite_id) if status == "pending" and why is None else None
    final = "paid" if paid else status
    taken = status == "pending"
    result = {
        "counted": True, "invite_id": invite_id, "status": final, "reason": why, "flags": flags, "via": via,
        "inviter_coins": paid["inviter_coins"] if paid else 0, "invitee_coins": paid["invitee_coins"] if paid else 0,
        "slot": used + 1 if taken else None, "limit": REWARD_LIMIT,
        "left": max(0, REWARD_LIMIT - used - (1 if taken else 0)),
    }
    tracking.track_server(invitee_id, "referral_joined", step=final, meta={"inviter": inviter_id, "reason": why, "flags": flags, "via": via})
    return result


def _pay(invite_id):
    """Выплачивает награду по приглашению ровно один раз: статус pending → paid меняется одним UPDATE, и только победитель начисляет коины."""
    conn = connect()
    try:
        row = conn.execute("SELECT inviter_id, invitee_id, status FROM referral_invites WHERE id=?", (invite_id,)).fetchone()
        if row is None or row["status"] != "pending":
            return None
        inviter = conn.execute("SELECT banned FROM users WHERE telegram_id=?", (row["inviter_id"],)).fetchone()
        if inviter is None or inviter["banned"]:
            conn.execute("UPDATE referral_invites SET status='no_reward', reason='inviter_banned' WHERE id=? AND status='pending'", (invite_id,))
            conn.commit()
            return None
        cursor = conn.execute(
            "UPDATE referral_invites SET status='paid', paid_at=?, inviter_coins=?, invitee_coins=? WHERE id=? AND status='pending'",
            (_fmt(_now()), REWARD_COINS, INVITEE_COINS, invite_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
    finally:
        conn.close()
    add_xp(row["inviter_id"], REWARD_COINS)
    add_xp(row["invitee_id"], INVITEE_COINS)
    tracking.track_server(row["inviter_id"], "referral_paid", meta={"invitee": row["invitee_id"], "coins": REWARD_COINS})
    return {"invite_id": invite_id, "inviter_id": row["inviter_id"], "invitee_id": row["invitee_id"],
            "inviter_coins": REWARD_COINS, "invitee_coins": INVITEE_COINS}


def on_first_mark(user_id):
    """Приглашённый отметил привычку: фиксируем «активацию», а ожидающую награду выплачиваем. Вызывается из db/habits.py::complete_habit
    после каждой отметки, поэтому дёшево (один поиск по уникальному индексу) и никогда не бросает исключение."""
    try:
        conn = connect()
        try:
            row = conn.execute("SELECT id, status, created_at FROM referral_invites WHERE invitee_id=? AND activated_at IS NULL", (user_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        pending = row["status"] == "pending"
        if pending and rewards_paused():
            return None                                      # пауза: оставляем ожидать, следующая отметка проверит снова
        created = tracking._parse(row["created_at"])
        stale = pending and created is not None and _now() - created > timedelta(days=PENDING_TTL_DAYS)
        conn = connect()
        try:
            conn.execute("UPDATE referral_invites SET activated_at=? WHERE id=? AND activated_at IS NULL", (_fmt(_now()), row["id"]))
            if stale:
                conn.execute("UPDATE referral_invites SET status='expired', reason='ttl' WHERE id=? AND status='pending'", (row["id"],))
            conn.commit()
        finally:
            conn.close()
        tracking.track_server(user_id, "referral_activated", step="expired" if stale else row["status"])
        return _pay(row["id"]) if pending and not stale else None
    except Exception:
        logger.exception("Не удалось обработать первую отметку приглашённого %s", user_id)
        return None


# ---------------------------------------------------------------------------------------------------------------- уведомления о выплате

def mark_notified(invite_id):
    conn = connect()
    try:
        conn.execute("UPDATE referral_invites SET notified_at=? WHERE id=? AND notified_at IS NULL", (_fmt(_now()), invite_id))
        conn.commit()
    finally:
        conn.close()


def unnotified_payments(limit=50):
    """Выплаты после первой отметки приглашённого, о которых пригласивший ещё не узнал (выплаты «сразу» сообщает обработчик /start)."""
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT r.id, r.inviter_id, r.invitee_id, r.inviter_coins, u.first_name AS invitee_name
            FROM referral_invites r LEFT JOIN users u ON u.telegram_id = r.invitee_id
            WHERE r.status='paid' AND r.notified_at IS NULL AND r.activated_at IS NOT NULL AND r.paid_at >= r.activated_at
            ORDER BY r.id LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- рассылка «позови друга»

def mailing_candidates(now_utc=None):
    """Люди первой недели, которым можно писать в боте: (telegram_id, created_at). Точный день считает рассылка по часовому поясу человека."""
    from .push_access import push_allowed_sql
    now = (now_utc or datetime.now(timezone.utc)).replace(tzinfo=None)
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT u.telegram_id, u.created_at FROM users u WHERE u.bot_blocked_at IS NULL AND {push_allowed_sql('u')} "
            "AND u.created_at >= ? AND u.created_at <= ?",
            (_fmt(now - timedelta(days=9)), _fmt(now - timedelta(hours=30))),
        ).fetchall()
    finally:
        conn.close()
    return [(r["telegram_id"], r["created_at"]) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- админка

def _person(row, prefix):
    return {
        "id": row[f"{prefix}_id"], "name": row[f"{prefix}_name"], "handle": row[f"{prefix}_handle"] or row[f"{prefix}_username"],
    }


def admin_overview(limit=30):
    """Сводка и последние приглашения для админ-панели: кто кого позвал, статус, признаки для проверки."""
    conn = connect()
    try:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM referral_invites GROUP BY status")}
        totals = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='paid' THEN inviter_coins + invitee_coins END),0) AS coins, "
            "COUNT(activated_at) AS activated, COUNT(*) AS total FROM referral_invites"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT r.*, a.first_name AS inviter_name, a.handle AS inviter_handle, a.username AS inviter_username,
                   b.first_name AS invitee_name, b.handle AS invitee_handle, b.username AS invitee_username, b.total_completed AS invitee_marks
            FROM referral_invites r
            LEFT JOIN users a ON a.telegram_id = r.inviter_id
            LEFT JOIN users b ON b.telegram_id = r.invitee_id
            ORDER BY r.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    items = []
    quiet_since = _now() - timedelta(days=INACTIVE_AFTER_DAYS)
    for row in rows:
        flags = [f for f in (row["flags"] or "").split(",") if f]
        created = tracking._parse(row["created_at"])
        if row["activated_at"] is None and row["status"] in ("paid", "pending") and created is not None and created <= quiet_since:
            flags.append("inactive")
        items.append({
            "invite_id": row["id"], "inviter": _person(row, "inviter"), "invitee": _person(row, "invitee"), "via": row["via"],
            "status": row["status"], "status_label": STATUS_LABELS.get(row["status"], row["status"]),
            "reason": REASON_LABELS.get(row["reason"], row["reason"]),
            "coins": (row["inviter_coins"] or 0) + (row["invitee_coins"] or 0), "created_at": row["created_at"],
            "activated": row["activated_at"] is not None, "marks": row["invitee_marks"] or 0,
            "flags": [{"key": f, "label": FLAG_LABELS.get(f, f)} for f in flags], "can_revoke": row["status"] in ("paid", "pending"),
        })
    flag_state = {key: bool(is_feature_enabled(key)) for key in (PAUSE_FLAG, AFTER_MARK_FLAG)}
    conn = connect()
    try:
        mailing_row = conn.execute("SELECT enabled, rollout_pct FROM feature_flags WHERE key=?", (MAILING_FLAG,)).fetchone()
    finally:
        conn.close()
    return {
        "counts": counts, "total": int(totals["total"]), "activated": int(totals["activated"]), "coins_paid": int(totals["coins"]),
        "limit": REWARD_LIMIT, "reward": REWARD_COINS, "invitee_reward": INVITEE_COINS,
        "paused": flag_state[PAUSE_FLAG], "after_mark": flag_state[AFTER_MARK_FLAG],
        "mailing": {"on": mailing_row is None or bool(mailing_row["enabled"]), "pct": 100 if mailing_row is None else mailing_row["rollout_pct"]},
        "flags": {"pause": PAUSE_FLAG, "after_mark": AFTER_MARK_FLAG, "mailing": MAILING_FLAG},
        "items": items,
    }


def revoke_invite(invitee_id, admin_id):
    """Отзывает выплату: списывает коины (не глубже нуля) и оставляет слот занятым. Возвращает {'ok': True, ...} или {'error': код}."""
    conn = connect()
    try:
        row = conn.execute("SELECT id, inviter_id, status, inviter_coins, invitee_coins FROM referral_invites WHERE invitee_id=?", (invitee_id,)).fetchone()
        if row is None:
            return {"error": "not_found"}
        if row["status"] not in ("paid", "pending"):
            return {"error": "not_revocable"}
        conn.execute(
            "UPDATE referral_invites SET status='revoked', revoked_at=?, revoked_by=? WHERE id=? AND status IN ('paid','pending')",
            (_fmt(_now()), admin_id, row["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    taken = {}
    if row["status"] == "paid":
        taken[row["inviter_id"]] = _take_coins(row["inviter_id"], row["inviter_coins"] or 0)
        taken[invitee_id] = _take_coins(invitee_id, row["invitee_coins"] or 0)
    tracking.track_server(admin_id, "referral_revoked", meta={"invitee": invitee_id, "inviter": row["inviter_id"]})
    return {"ok": True, "taken": taken}


def _take_coins(user_id, amount):
    """Списывает коины с баланса и из общего опыта, не уходя ниже нуля. Возвращает, сколько списано на самом деле."""
    if amount <= 0:
        return 0
    conn = connect()
    try:
        row = conn.execute("SELECT xp, total_xp FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if row is None:
            return 0
        xp, total = int(row["xp"] or 0), int(row["total_xp"] or 0)
        take_xp, take_total = min(amount, xp), min(amount, total)
        new_total = total - take_total
        conn.execute("UPDATE users SET xp=?, total_xp=?, level=? WHERE telegram_id=?", (xp - take_xp, new_total, new_total // 100 + 1, user_id))
        conn.commit()
        return take_xp
    finally:
        conn.close()
