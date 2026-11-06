"""
Автоудаление неактивных аккаунтов (юридический минимум, 06.11; старый план, п.2.5): данные не храним дольше, чем нужно.

Включается переменной INACTIVE_PURGE_MONTHS (по умолчанию 0 — выключено; предложение — 12, решает владелец). Раз в сутки:
  • кто не заходил INACTIVE_PURGE_MONTHS месяцев, у кого нет действующей подписки и кто не админ — обезличивается тем же способом,
    что и ручное «Удалить аккаунт» (db/account.py::request_account_deletion: платежи и журналы остаются, как требует учёт);
  • за WARN_DAYS дней до этого бот один раз предупреждает: «зайди — и всё сохранится». Зашёл — предупреждение сбрасывается.
"""
from datetime import datetime, timedelta, timezone

from .core import connect

WARN_DAYS = 30
FMT = "%Y-%m-%d %H:%M:%S"


def _ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS inactive_purge_notices(user_id INTEGER PRIMARY KEY, warned_at TEXT NOT NULL)")


def purge_months():
    import os
    try:
        return max(0, int(os.getenv("INACTIVE_PURGE_MONTHS", "0")))
    except ValueError:
        return 0


def candidates(now=None, months=None):
    """{"warn": [...], "purge": [...]} — кого предупредить и кого обезличить сейчас."""
    from config import ADMIN_IDS
    from .subscription import has_active_subscription
    months = purge_months() if months is None else months
    if months <= 0:
        return {"warn": [], "purge": []}
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    purge_before = now - timedelta(days=30 * months)
    warn_before = purge_before + timedelta(days=WARN_DAYS)
    conn = connect()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT u.telegram_id, u.last_seen, n.warned_at FROM users u LEFT JOIN inactive_purge_notices n ON n.user_id=u.telegram_id "
            "WHERE COALESCE(u.banned, 0)=0 AND u.last_seen IS NOT NULL AND u.last_seen < ?", (warn_before.strftime(FMT),)).fetchall()
    finally:
        conn.close()
    admins = {int(a) for a in ADMIN_IDS}
    warn, purge = [], []
    for row in rows:
        uid = row["telegram_id"]
        if uid in admins or has_active_subscription(uid):
            continue
        last_seen = datetime.fromisoformat(str(row["last_seen"]).replace("T", " ")[:19])
        warned = row["warned_at"] and row["warned_at"] > str(row["last_seen"])     # предупреждение после последнего захода
        if last_seen < purge_before and warned:
            purge.append(uid)
        elif not warned:
            warn.append(uid)
    return {"warn": warn, "purge": purge}


def mark_warned(user_id, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    conn = connect()
    try:
        _ensure(conn)
        conn.execute("INSERT INTO inactive_purge_notices(user_id, warned_at) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET warned_at=excluded.warned_at",
                     (user_id, now.strftime(FMT)))
        conn.commit()
    finally:
        conn.close()


WARN_TEXT = ("Тебя давно не было в ADAM. Через {days} дней я удалю твои данные — так мы бережём приватность. "
             "Хочешь сохранить привычки, серию и историю? Просто открой приложение — этого достаточно.")


async def run_inactive_purge(bot, now=None):
    """Ежедневная задача. Возвращает (предупреждено, обезличено)."""
    from .account import request_account_deletion
    from keyboards import welcome_menu
    plan = candidates(now)
    warned = purged = 0
    for uid in plan["warn"]:
        try:
            await bot.send_message(uid, WARN_TEXT.format(days=WARN_DAYS), reply_markup=welcome_menu())
        except Exception:
            pass                                          # бот заблокирован — предупредить нельзя, но срок всё равно отсчитываем
        mark_warned(uid, now)
        warned += 1
    for uid in plan["purge"]:
        request_account_deletion(uid)
        purged += 1
    return warned, purged
