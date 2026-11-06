"""
Эксперимент «до 5 или до 7 напоминаний в день» (решение 05.11: «можно больше 5 — проверить по статистике»).

Группы — по флагу reminders_7 (админка → флаги, процент раскатки = доля группы «7»; детерминированно по человеку):
  «5» (все, пока флаг выключен): 06 · 12 · 17 · 22 (если что-то отмечено) · 23 · 23:30; «вернись в серию» — 10 и 16.
  «7»: плюс точка дня в 10:00, точка в 22:00 и в пустой день, «вернись в серию» и в 21:00.

Telegram не сообщает боту, прочитано ли сообщение. Поэтому меряем то, что видно:
  • сколько напоминаний по привычкам в среднем уходит человеку за день;
  • доля напоминаний, после которых в течение часа была отметка привычки («сработало»);
  • доля людей группы, у которых сейчас выключены напоминания (все или категории «привычки»/«ударный режим»);
  • доля заблокировавших бота.
"""
from datetime import datetime, timedelta, timezone

from .core import connect

FLAG = "reminders_7"
GROUP_5, GROUP_7 = "5", "7"
# Категории notification_log, которые считаем «напоминаниями по привычкам» (то, что ограничиваем).
HABIT_KINDS_SQL = ("category LIKE 'habit_checkpoint_%' OR category IN ('morning_6', 'risk23', 'risk2330') "
                   "OR category LIKE 'streak_reengage_%'")
FOLLOW_UP_MINUTES = 60


def reminder_group(user_id):
    try:
        from .feature_flags import is_feature_enabled
        return GROUP_7 if is_feature_enabled(FLAG, user_id) else GROUP_5
    except Exception:
        return GROUP_5


def in_group_7(user_id):
    return reminder_group(user_id) == GROUP_7


def stats(days=14, now=None):
    """Сводка по группам за последние `days` дней (для админки)."""
    from .feature_flags import get_all_flags
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    since = (now - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        users = [r["telegram_id"] for r in conn.execute(
            "SELECT DISTINCT u.telegram_id FROM users u WHERE EXISTS(SELECT 1 FROM habits h WHERE h.user_id=u.telegram_id)")]
        sent = conn.execute(f"SELECT user_id, sent_at FROM notification_log WHERE sent_at >= ? AND ({HABIT_KINDS_SQL})", (since,)).fetchall()
        marks = conn.execute("SELECT user_id, at FROM user_events WHERE name='habit_completed' AND at >= ?", (since,)).fetchall()
        settings = {r["user_id"]: r for r in conn.execute("SELECT * FROM settings")}
        blocked = {r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM users WHERE bot_blocked_at IS NOT NULL")}
    finally:
        conn.close()

    def _ts(value):
        try:
            return datetime.fromisoformat(str(value).replace("T", " ")[:19])
        except ValueError:
            return None

    marks_by_user = {}
    for row in marks:
        moment = _ts(row["at"])
        if moment:
            marks_by_user.setdefault(row["user_id"], []).append(moment)

    def _reminders_off(user_id):
        from .settings import reminder_category_enabled
        row = settings.get(user_id)
        if row is None:
            return False
        return not (reminder_category_enabled(row, "habits") and reminder_category_enabled(row, "streak"))

    groups = {GROUP_5: {"users": [], "sent": 0, "followed": 0, "user_days": set()}, GROUP_7: {"users": [], "sent": 0, "followed": 0, "user_days": set()}}
    group_of = {u: reminder_group(u) for u in users}
    for u in users:
        groups[group_of[u]]["users"].append(u)
    for row in sent:
        group = groups.get(group_of.get(row["user_id"]))
        moment = _ts(row["sent_at"])
        if group is None or moment is None:
            continue
        group["sent"] += 1
        group["user_days"].add((row["user_id"], moment.date()))
        window_end = moment + timedelta(minutes=FOLLOW_UP_MINUTES)
        if any(moment <= m <= window_end for m in marks_by_user.get(row["user_id"], ())):
            group["followed"] += 1

    def _pct(part, whole):
        return round(100 * part / whole, 1) if whole else None

    out = {}
    for key, g in groups.items():
        people = g["users"]
        out[key] = {
            "users": len(people),
            "reminders": g["sent"],
            "per_day": round(g["sent"] / len(g["user_days"]), 2) if g["user_days"] else None,
            "followed_pct": _pct(g["followed"], g["sent"]),
            "reminders_off_pct": _pct(sum(1 for u in people if _reminders_off(u)), len(people)),
            "blocked_pct": _pct(sum(1 for u in people if u in blocked), len(people)),
        }
    flag = next((f for f in get_all_flags() if f["key"] == FLAG), None)
    return {
        "days": int(days), "groups": out,
        "flag": {"enabled": bool(flag and flag["enabled"]), "rollout_pct": (flag["rollout_pct"] if flag else 0)},
    }
