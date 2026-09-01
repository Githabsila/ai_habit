"""
Roadmap #22 (AI предлагает снизить планку у постоянно проваливаемой
привычки) и #23/#36 (AI сам подбирает оптимальное время напоминания по
истории выполнения) — оба читают уже существующие журналы (habit_logs,
habit_completion_events), новых таблиц не требуют.
"""
from collections import Counter
from datetime import datetime, timezone

from .core import connect

# Roadmap #22 — если привычка провалена (не выполнена и НЕ осознанно
# пропущена) хотя бы STRUGGLE_THRESHOLD раз из последних STRUGGLE_WINDOW_DAYS
# дней, считаем её "постоянно проваливаемой" и стоит предложить снизить
# планку (например, target_count поменьше или добавить frequency_per_week).
STRUGGLE_WINDOW_DAYS = 5
STRUGGLE_THRESHOLD = 4

# Roadmap #23/#36 — минимум зафиксированных выполнений, чтобы вообще
# предлагать "обычно ты делаешь это в районе X" — на 1-2 точках предлагать
# рано, слишком похоже на случайность.
MIN_COMPLETIONS_FOR_SUGGESTION = 3


def get_struggling_habits(user_id, threshold=STRUGGLE_THRESHOLD, window_days=STRUGGLE_WINDOW_DAYS):
    """Привычки (id, title, missed) пользователя, проваленные >= threshold
    раз за последние window_days дней (по habit_logs, без учёта осознанных
    пропусков) — источник для мягкой AI-подсказки "может, снизить планку?"."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT habit_id, habit_title,
               SUM(CASE WHEN completed=0 AND skipped=0 THEN 1 ELSE 0 END) as missed
        FROM habit_logs
        WHERE user_id=? AND day >= date('now', ?)
        GROUP BY habit_id, habit_title
        HAVING missed >= ?
        ORDER BY missed DESC
    """, (user_id, f"-{window_days} days", threshold))
    rows = cursor.fetchall()
    conn.close()
    return [{"habit_id": r["habit_id"], "title": r["habit_title"], "missed": r["missed"]} for r in rows]


def suggest_optimal_reminder_time(habit_id, user_id):
    """Самый частый ЛОКАЛЬНЫЙ час выполнения этой привычки за всё время
    (habit_completion_events хранит момент КАЖДОГО complete_habit()) —
    None, если данных ещё недостаточно (см. MIN_COMPLETIONS_FOR_SUGGESTION)."""
    from .streak import get_timezone
    from zoneinfo import ZoneInfo

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT completed_at FROM habit_completion_events WHERE habit_id=? ORDER BY id DESC LIMIT 30",
        (habit_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < MIN_COMPLETIONS_FOR_SUGGESTION:
        return None

    try:
        tz = ZoneInfo(get_timezone(user_id))
    except Exception:
        tz = timezone.utc

    hours = []
    for row in rows:
        try:
            dt = datetime.fromisoformat(str(row["completed_at"])).replace(tzinfo=timezone.utc).astimezone(tz)
        except ValueError:
            continue
        hours.append(dt.hour)

    if len(hours) < MIN_COMPLETIONS_FOR_SUGGESTION:
        return None

    most_common_hour, count = Counter(hours).most_common(1)[0]
    # Если самый частый час набрал меньше трети всех точек — сигнал слишком
    # шумный, нет устойчивой закономерности, лучше промолчать.
    if count < len(hours) / 3:
        return None
    return f"{most_common_hour:02d}:00"


# Roadmap #27 — статистические корреляции между привычками.
CORRELATION_WINDOW_DAYS = 30
CORRELATION_MIN_SAMPLES = 5  # минимум дней, когда A была выполнена, чтобы вообще судить
CORRELATION_MIN_RATE = 60  # "когда A сделана, B тоже сделана" минимум в % случаев
CORRELATION_MIN_LIFT = 20  # и это должно быть заметно выше базовой частоты B


def get_habit_correlations(user_id, limit=3):
    """Топ пар привычек вида «когда выполняешь A, обычно выполняешь и B» —
    считается по co-occurrence за последние CORRELATION_WINDOW_DAYS дней
    из habit_logs (группировка по habit_title, а не habit_id — так пары
    остаются осмысленными даже если привычку удалили и создали заново)."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT day, habit_title, completed FROM habit_logs
        WHERE user_id=? AND day >= date('now', ?)
    """, (user_id, f"-{CORRELATION_WINDOW_DAYS} days"))
    rows = cursor.fetchall()
    conn.close()

    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["habit_title"]] = bool(r["completed"])

    titles = sorted({t for day in by_day.values() for t in day})
    days = list(by_day.values())

    results = []
    for i, a in enumerate(titles):
        a_done_days = [d for d in days if d.get(a)]
        if len(a_done_days) < CORRELATION_MIN_SAMPLES:
            continue
        for b in titles:
            if b == a:
                continue
            b_done_days = [d for d in days if b in d and d[b]]
            b_total_days = [d for d in days if b in d]
            if not b_total_days:
                continue
            baseline = 100 * len(b_done_days) / len(b_total_days)
            b_when_a = sum(1 for d in a_done_days if d.get(b))
            rate = round(100 * b_when_a / len(a_done_days))
            lift = rate - baseline
            if rate >= CORRELATION_MIN_RATE and lift >= CORRELATION_MIN_LIFT:
                results.append({
                    "a": a, "b": b, "rate": rate,
                    "baseline": round(baseline), "samples": len(a_done_days),
                })

    results.sort(key=lambda r: (r["rate"] - r["baseline"]), reverse=True)
    return results[:limit]
