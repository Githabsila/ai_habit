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
