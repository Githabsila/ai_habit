"""
Разбор «почему этому человеку не приходят точки дня» — для карточки человека в админке (/api/admin/user/{id}/reminders).

Решение «уйдёт или нет» не дублируется: берётся тот же coach.checkpoint_verdict, что и у настоящей рассылки. К нему добавлено то, что видно
только из базы: настройки человека (часовой пояс, тихие часы, выключатели, метка «бот заблокирован»), что бот реально отправил за последние
дни и — с момента появления журнала — по какой причине точка дня пропущена (db/notification_skips.py).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from db import get_settings, get_timezone, get_user
from db.core import connect
from db.notification_skips import REASON_LABELS, recent_skips
from db.push_access import push_allowed_sql
from db.streak import get_notification_history, get_streak_reengagement_state

CHECK_HOURS = (10, 12, 17, 22)
HISTORY_DAYS = 3


def _parse_utc(value):
    try:
        return datetime.strptime(str(value)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _quiet_text(settings):
    try:
        start, end = settings["quiet_hours_start"], settings["quiet_hours_end"]
    except (IndexError, KeyError, TypeError):
        return None
    return f"{int(start):02d}:00–{int(end):02d}:00" if start is not None and end is not None else None


def _allowed_by_access(user_id):
    conn = connect()
    try:
        return conn.execute(f"SELECT 1 FROM users WHERE telegram_id=? AND {push_allowed_sql('users')}", (user_id,)).fetchone() is not None
    finally:
        conn.close()


def diagnose_user(user_id, now_utc=None):
    """Полная картина по напоминаниям человека. None — такого человека нет."""
    import coach

    user = get_user(user_id)
    if user is None:
        return None
    now_utc = now_utc or datetime.now(timezone.utc)
    tz_name = get_timezone(user_id)
    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    today = now_local.date().isoformat()
    settings = get_settings(user_id)

    reminders_on = bool(settings and settings["reminders"])
    habits_on = coach.reminder_category_enabled(settings, "habits")
    quiet = _quiet_text(settings)
    reengage = get_streak_reengagement_state(user_id)
    blocked_at = user["bot_blocked_at"] if "bot_blocked_at" in user.keys() else None
    access = user["access_status"] if "access_status" in user.keys() else None

    account = [
        {"ok": _allowed_by_access(user_id), "label": "Рассылки разрешены",
         "detail": f"доступ: {access or 'approved'}" + (", аккаунт заблокирован" if user["banned"] else "")},
        {"ok": not blocked_at, "label": "Бот не заблокирован человеком",
         "detail": f"метка стоит с {blocked_at} — снимается, когда человек что-нибудь напишет боту" if blocked_at else "метки нет"},
        {"ok": reminders_on and habits_on, "label": "Напоминания включены",
         "detail": "общий выключатель выключен" if not reminders_on else "категория «Привычки и план дня» выключена" if not habits_on else "общий выключатель и «Привычки» включены"},
        {"ok": quiet is None, "label": "Тихие часы", "detail": f"бот молчит {quiet}" if quiet else "не заданы"},
        {"ok": tz_name != "UTC", "label": "Часовой пояс", "detail": f"{tz_name}, у человека сейчас {now_local:%H:%M}" + (
            " — стоит UTC: если человек не живёт по Гринвичу, точки придут в «чужие» часы; пояс записывается при открытии приложения" if tz_name == "UTC" else "")},
        {"ok": not (reengage.get("has_history") and int(reengage.get("inactive_days") or 0) > 0), "label": "Серия",
         "detail": (f"пропущено полных дней: {reengage.get('inactive_days')} — вместо точек дня идёт сообщение возврата в серию"
                    if reengage.get("has_history") and int(reengage.get("inactive_days") or 0) > 0 else "без пропущенных дней")},
    ]

    history = get_notification_history(user_id, limit=60)
    sent_today = {}
    recent = []
    cutoff = now_utc - timedelta(days=HISTORY_DAYS)
    for item in history:
        moment = _parse_utc(item["sent_at"])
        if moment is None or moment < cutoff:
            continue
        local = moment.astimezone(tz)
        recent.append({"when": f"{local:%d.%m %H:%M}", "label": item["label"]})
        if local.date().isoformat() == today:
            sent_today.setdefault(item["category"], f"{local:%H:%M}")
    skips_today = {s["kind"]: s for s in recent_skips(user_id, since_day=today) if s["day"] == today}

    checkpoints = []
    for hour in CHECK_HOURS:
        kind = f"habit_checkpoint_{hour}"
        at = now_local.replace(hour=hour, minute=0, second=0, microsecond=0)
        verdict = coach.checkpoint_verdict(user_id, settings, at, hour, check_window=False, check_fresh_mark=False)
        rule = verdict["reason"]
        if kind in sent_today:
            actual = f"сегодня отправлено в {sent_today[kind]}"
        elif kind in skips_today:
            actual = f"сегодня пропущено: {skips_today[kind]['label']}"
        elif at < now_local:
            actual = "час прошёл, а ни отправки, ни причины пропуска не записано"
        else:
            actual = "ещё впереди"
        checkpoints.append({
            "hour": hour, "will_send": rule is None, "rule": REASON_LABELS.get(rule) if rule else None, "actual": actual,
            "sent": kind in sent_today, "skipped": kind in skips_today,
        })

    habits_total = len(coach.get_habits(user_id))
    first = next((c for c in checkpoints if not c["will_send"]), None)
    return {
        "user_id": user_id, "name": user["first_name"] or user["username"], "timezone": tz_name, "local_time": f"{now_local:%H:%M}",
        "account": account, "checkpoints": checkpoints, "habits": {"total": habits_total, "open": len(coach.get_incomplete_habits(user_id))},
        "recent": recent[:15], "skips": recent_skips(user_id, days=7),
        "summary": ("по текущим настройкам точки дня должны приходить" if first is None
                    else f"сейчас точка дня не придёт: {first['rule']}"),
    }
