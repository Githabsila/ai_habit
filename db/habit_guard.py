"""
Защита от накрутки Adam Coin через привычки (п.1 владельца, 16.10): «добавил → отметил → удалил → добавил ещё → отметил…».

Раньше потолок был «10 оплачиваемых отметок в день» независимо от того, сколько привычек у человека: с одной-двумя привычками можно было получить
10 выплат, гоняя «добавить / отметить / удалить» по кругу. Теперь четыре слоя; обычный человек (до 5–6 привычек, пара удалений в день) их не замечает:

  1. Монеты за день — не больше, чем привычек в списке ПРЯМО СЕЙЧАС (db/habits.py::complete_habit). Круг «удалил и создал новую» не даёт новых выплат:
     удалённая отметка потраченный «слот» не возвращает. Честные случаи — добавил привычку днём, исправил опечатку, убрал лишнее — укладываются в тот же потолок.
  2. Монеты за привычки в этот день не платятся вовсе, если удалено DELETE_SOFT_LIMIT привычек или создано ADD_SOFT_LIMIT за день — так делают не люди,
     а те, кто ищет лазейку. Отметка и серия при этом засчитываются как обычно.
  3. Создавать привычки пачкой (ADD_BURST за BURST_WINDOW_SECONDS секунд) нельзя — это защита и от скриптов, и от нагрузки на базу.
  4. Каждый такой случай — подозрение (abuse_flags): админам уходит уведомление, список виден в админке, а там можно заморозить человеку награды за
     привычки на сутки или неделю (users.rewards_frozen_until) и снять заморозку.
"""
import logging
from datetime import datetime, timedelta, timezone

from .core import connect

logger = logging.getLogger(__name__)

DELETE_SOFT_LIMIT = 8            # удалений привычек за (местный) день → монеты за привычки до конца дня не платятся
ADD_SOFT_LIMIT = 20              # созданий привычек за день → то же
ADD_BURST = 10                   # создано привычек за BURST_WINDOW_SECONDS → следующую пока нельзя
BURST_WINDOW_SECONDS = 60
FREEZE_CHOICES_HOURS = (24, 168)
KEEP_DAYS = 30

KIND_LABELS = {
    "churn_delete": "Много удалений привычек за день",
    "churn_add": "Много новых привычек за день",
    "burst": "Привычки создаются пачкой",
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value):
    try:
        return datetime.strptime(str(value)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------------- журнал

def counts_today(user_id, day):
    conn = connect()
    try:
        rows = conn.execute("SELECT kind, COUNT(*) AS n FROM habit_churn WHERE user_id=? AND day=? GROUP BY kind", (user_id, day)).fetchall()
    finally:
        conn.close()
    result = {"adds": 0, "deletes": 0}
    for row in rows:
        result["adds" if row["kind"] == "add" else "deletes"] = int(row["n"])
    return result


def record(user_id, day, kind):
    """Записывает создание ('add') или удаление ('delete') привычки. Если за день пройден порог — ставит подозрение. Сбой журнала не мешает
    человеку работать с привычками. Возвращает счётчики дня."""
    try:
        conn = connect()
        try:
            conn.execute("INSERT INTO habit_churn(user_id, day, kind) VALUES (?,?,?)", (user_id, day, kind))
            conn.execute("DELETE FROM habit_churn WHERE day < date(?, ?)", (day, f"-{KEEP_DAYS} days"))
            conn.commit()
        finally:
            conn.close()
        counts = counts_today(user_id, day)
        if kind == "delete" and counts["deletes"] >= DELETE_SOFT_LIMIT:
            flag(user_id, day, "churn_delete", f"удалено привычек за день: {counts['deletes']}, создано: {counts['adds']}")
        elif kind == "add" and counts["adds"] >= ADD_SOFT_LIMIT:
            flag(user_id, day, "churn_add", f"создано привычек за день: {counts['adds']}, удалено: {counts['deletes']}")
        return counts
    except Exception:
        logger.warning("Не удалось записать создание/удаление привычки (%s)", user_id, exc_info=True)
        return {"adds": 0, "deletes": 0}


def adds_recently(user_id, seconds=BURST_WINDOW_SECONDS):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM habit_churn WHERE user_id=? AND kind='add' AND at >= ?",
            (user_id, _fmt(_now() - timedelta(seconds=seconds))),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def burst_blocked(user_id, day):
    """True — человек создаёт привычки слишком быстро (первое срабатывание за день ставит подозрение)."""
    try:
        if adds_recently(user_id) < ADD_BURST:
            return False
    except Exception:
        logger.warning("Проверка «привычки пачкой» не сработала (%s) — пропускаем", user_id, exc_info=True)
        return False                                  # сбой защиты не должен мешать человеку добавлять привычки
    flag(user_id, day, "burst", f"создано {ADD_BURST}+ привычек меньше чем за минуту")
    return True


# ---------------------------------------------------------------------------------------------------------------- блокировка монет

def frozen_until(user_id):
    """До какого момента (UTC, naive) админ заморозил награды за привычки; None — не заморожены."""
    conn = connect()
    try:
        row = conn.execute("SELECT rewards_frozen_until FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    until = _parse(row["rewards_frozen_until"]) if row else None
    return until if until and until > _now() else None


def reward_lock(user_id, day):
    """Почему монеты за привычки этому человеку сейчас не платятся: 'frozen' (заморозил админ) | 'churn' (слишком много
    удалений/созданий за день) | None (платятся). Дневной потолок по числу привычек считает complete_habit."""
    try:
        if frozen_until(user_id):
            return "frozen"
        counts = counts_today(user_id, day)
        if counts["deletes"] >= DELETE_SOFT_LIMIT or counts["adds"] >= ADD_SOFT_LIMIT:
            return "churn"
    except Exception:
        logger.warning("Проверка блокировки монет не сработала (%s) — платим как обычно", user_id, exc_info=True)
    return None


def freeze_rewards(user_id, hours):
    """Заморозить награды за привычки на hours часов (0 — снять). Возвращает момент окончания (iso) или None."""
    hours = int(hours or 0)
    until = _fmt(_now() + timedelta(hours=hours)) if hours > 0 else None
    conn = connect()
    try:
        cursor = conn.execute("UPDATE users SET rewards_frozen_until=? WHERE telegram_id=?", (until, user_id))
        conn.commit()
        found = cursor.rowcount > 0
    finally:
        conn.close()
    return {"found": found, "until": until}


# ---------------------------------------------------------------------------------------------------------------- подозрения

def flag(user_id, day, kind, detail=""):
    """Подозрение на накрутку: одно на человека, день и вид. True — поставлено сейчас (раньше не было)."""
    try:
        conn = connect()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO abuse_flags(user_id, day, kind, detail, created_at) VALUES (?,?,?,?,?)",
                (user_id, day, kind, str(detail)[:200], _fmt(_now())),
            )
            conn.commit()
            fresh = cursor.rowcount == 1
        finally:
            conn.close()
        if fresh:
            from . import tracking
            tracking.track_server(user_id, "abuse_flagged", step=kind, meta={"detail": str(detail)[:120]})
        return fresh
    except Exception:
        logger.warning("Не удалось записать подозрение на накрутку (%s)", user_id, exc_info=True)
        return False


def unnotified_flags(limit=20):
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.user_id, f.day, f.kind, f.detail, u.first_name, u.username, u.handle
            FROM abuse_flags f LEFT JOIN users u ON u.telegram_id = f.user_id
            WHERE f.notified_at IS NULL ORDER BY f.id LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def mark_notified(flag_id):
    conn = connect()
    try:
        conn.execute("UPDATE abuse_flags SET notified_at=? WHERE id=? AND notified_at IS NULL", (_fmt(_now()), flag_id))
        conn.commit()
    finally:
        conn.close()


def overview(days=7, limit=40):
    """Для админки: последние подозрения с именами, сегодняшние счётчики и состояние заморозки, плюс пороги."""
    since = (_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.user_id, f.day, f.kind, f.detail, f.created_at, u.first_name, u.username, u.handle, u.rewards_frozen_until
            FROM abuse_flags f LEFT JOIN users u ON u.telegram_id = f.user_id
            WHERE f.day >= ? ORDER BY f.id DESC LIMIT ?
            """,
            (since, limit),
        ).fetchall()
    finally:
        conn.close()
    now = _now()
    items = []
    for row in rows:
        until = _parse(row["rewards_frozen_until"])
        items.append({
            "id": row["id"], "user_id": row["user_id"], "name": row["first_name"] or row["username"] or "—",
            "handle": row["handle"] or row["username"], "day": row["day"], "kind": row["kind"],
            "label": KIND_LABELS.get(row["kind"], row["kind"]), "detail": row["detail"], "created_at": row["created_at"],
            "frozen_until": until.isoformat() if until and until > now else None,
        })
    return {
        "items": items,
        "limits": {"delete_per_day": DELETE_SOFT_LIMIT, "add_per_day": ADD_SOFT_LIMIT, "add_burst": ADD_BURST, "burst_seconds": BURST_WINDOW_SECONDS,
                   "freeze_hours": list(FREEZE_CHOICES_HOURS)},
    }
