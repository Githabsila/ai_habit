"""
Короткая личная сводка человеку (ТЗ «Триал, подписка и приглашения», 28.10.2026, §11): что он отметил, какие задачи добавлял и выполнял, где возникали трудности.

Только реальные данные приложения, без выдуманных фактов: если данных мало, сводка так и говорит и предлагает посильный первый шаг. Это не недельный и не месячный разбор —
за первые дни так не называем; сводка объясняет, что после семи и больше дней использования появится больше материала для подробного недельного разбора, а со временем и для месячного.
Если человеку обещана сводка, ему сначала показывают её, а предложение продолжить по подписке идёт отдельно после (экран подписки, приглашение Адама).
Текст собирается кодом из чисел (без модели): так он точный, мгновенный и не тратит ответы Адама. Без рода у человека: «отмечено», «выполнено».
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .core import connect
from .users import get_user

MIN_DAYS_FOR_WEEKLY = 7
TOP_HABITS = 3
UNTOUCHED_SHOWN = 2


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _parse(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def _window_start(user):
    """С какого момента считаем: со старта триала (первого открытия приложения), а если его нет — с регистрации."""
    from .access_state import trial_bounds
    bounds = trial_bounds(user)
    if bounds:
        return bounds["started"], bounds["tz"]
    from .access_state import user_tz_name
    return _parse(user["created_at"]) if "created_at" in user.keys() else None, user_tz_name(user["telegram_id"])


def collect(user_id, now_utc=None):
    """Числа для сводки или None, если человека нет."""
    user = get_user(user_id)
    if user is None:
        return None
    now = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    start, tz_name = _window_start(user)
    zone = ZoneInfo(tz_name)
    since = start.isoformat(sep=" ") if start else "1970-01-01 00:00:00"
    first_local = start.replace(tzinfo=timezone.utc).astimezone(zone).date() if start else now.replace(tzinfo=timezone.utc).astimezone(zone).date()
    today_local = now.replace(tzinfo=timezone.utc).astimezone(zone).date()
    days = max(1, (today_local - first_local).days + 1)

    conn = connect()
    try:
        habits = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM habits WHERE user_id=?", (user_id,)).fetchall()}
        events = conn.execute("SELECT habit_id, completed_at FROM habit_completion_events WHERE user_id=? AND completed_at>=?", (user_id, since)).fetchall()
        task_row = conn.execute(
            "SELECT COUNT(*) AS added, COALESCE(SUM(CASE WHEN t.completed=1 THEN 1 ELSE 0 END),0) AS done FROM daily_plan_tasks t "
            "JOIN daily_plans p ON p.id=t.plan_id WHERE p.user_id=? AND t.created_at>=?", (user_id, since)).fetchone()
        goal_row = conn.execute(
            "SELECT COUNT(*) AS set_days, COALESCE(SUM(CASE WHEN main_goal_completed=1 THEN 1 ELSE 0 END),0) AS done_days FROM daily_plans "
            "WHERE user_id=? AND created_at>=? AND COALESCE(main_goal,'')!=''", (user_id, since)).fetchone()
        chats = conn.execute("SELECT COUNT(*) AS n FROM ai_messages WHERE user_id=? AND role='user' AND created_at>=?", (user_id, since)).fetchone()["n"]
    finally:
        conn.close()

    per_habit = {}
    mark_days = set()
    for event in events:
        moment = _parse(event["completed_at"])
        if moment is None:
            continue
        per_habit[event["habit_id"]] = per_habit.get(event["habit_id"], 0) + 1
        mark_days.add(moment.replace(tzinfo=timezone.utc).astimezone(zone).date())
    top = sorted(((habits.get(hid) or "привычка", n) for hid, n in per_habit.items() if hid in habits), key=lambda item: -item[1])[:TOP_HABITS]
    untouched = [title for hid, title in habits.items() if per_habit.get(hid, 0) == 0][:UNTOUCHED_SHOWN]
    return {
        "days": days, "habits_total": len(habits), "marks_total": sum(per_habit.values()), "days_with_marks": len(mark_days),
        "top_habits": [{"title": title, "count": n} for title, n in top], "untouched": untouched,
        "tasks_added": int(task_row["added"] or 0), "tasks_done": int(task_row["done"] or 0),
        "goal_days": int(goal_row["set_days"] or 0), "goal_done_days": int(goal_row["done_days"] or 0),
        "chats": int(chats or 0), "streak": int(user["streak"] or 0), "best_streak": int((user["best_streak"] if "best_streak" in user.keys() else 0) or 0),
    }


def build_summary(user_id, now_utc=None):
    """Сводка для человека: {"title", "lines", "note", "enough", "data": числа}. None — человека нет."""
    data = collect(user_id, now_utc)
    if data is None:
        return None
    days = data["days"]
    title = f"Коротко за {days} {_plural(days, 'день', 'дня', 'дней')} с Адамом"
    activity = data["marks_total"] + data["tasks_added"] + data["goal_days"] + data["chats"]
    lines = []
    enough = activity >= 3
    if not enough:
        have = []
        if data["habits_total"]:
            have.append(f"привычек добавлено — {data['habits_total']}")
        if data["marks_total"]:
            have.append(f"отметок — {data['marks_total']}")
        if data["tasks_added"] or data["goal_days"]:
            have.append(f"задач в плане — {data['tasks_added'] + data['goal_days']}")
        if data["chats"]:
            have.append(f"сообщений мне — {data['chats']}")
        lines.append("Пока данных мало для выводов" + (f": {', '.join(have)}." if have else "."))
        lines.append("Начни с одного посильного действия сегодня — отметь одну привычку или запиши главную задачу, и сводка станет точнее.")
    else:
        if data["marks_total"]:
            lines.append(f"Привычки: отмечено {data['marks_total']} {_plural(data['marks_total'], 'раз', 'раза', 'раз')} "
                         f"в {data['days_with_marks']} из {days} {_plural(days, 'дня', 'дней', 'дней')}.")
        if data["top_habits"]:
            head = data["top_habits"][0]
            lines.append(f"Чаще всего повторялась «{head['title']}» — {head['count']} {_plural(head['count'], 'раз', 'раза', 'раз')}.")
        if data["goal_days"]:
            lines.append(f"Главная задача была записана в {data['goal_days']} из {days} {_plural(days, 'дня', 'дней', 'дней')}, выполнена в {data['goal_done_days']}.")
        if data["tasks_added"]:
            lines.append(f"План дня: добавлено задач — {data['tasks_added']}, выполнено — {data['tasks_done']}.")
        if data["chats"]:
            lines.append(f"Мы поговорили: {data['chats']} {_plural(data['chats'], 'сообщение', 'сообщения', 'сообщений')} от тебя.")
        if data["untouched"]:
            names = ", ".join(f"«{title}»" for title in data["untouched"])
            lines.append(f"Пока без отметок: {names}. Здесь стоит выбрать посильный первый шаг.")
        if data["streak"]:
            lines.append(f"Ударный режим сейчас: {data['streak']} {_plural(data['streak'], 'день', 'дня', 'дней')} подряд.")
    note = None
    if days < MIN_DAYS_FOR_WEEKLY:
        note = ("После семи и больше дней использования появится больше материала для подробного недельного разбора, а по мере накопления истории — и для месячного.")
    return {"title": title, "lines": lines, "note": note, "enough": enough, "data": data}
