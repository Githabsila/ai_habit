"""
Журнал «почему напоминание НЕ пришло». Точка дня (coach._run_habit_checkpoint) проверяет много условий — выключенные напоминания, тихие часы,
пропущенный день, нечего напоминать, — и раньше при любом «нет» молчала: на вопрос «почему не пришло» ответить было нечем. Теперь причина
пишется (раз в сутки на человека и точку дня) и показывается в админке (reminder_diagnosis.py). Сбой журнала рассылку не ломает.
"""
import logging

from .core import connect

logger = logging.getLogger(__name__)

KEEP_DAYS = 30

REASON_LABELS = {
    "bot_blocked": "бот помечен как заблокированный — рассылки не идут, пока человек сам что-нибудь не напишет боту",
    "reminders_off": "в настройках выключены все напоминания",
    "habits_off": "выключена категория напоминаний «Привычки и план дня»",
    "quiet_hours": "тихие часы — в это время бот молчит",
    "reengage": "человек пропустил день — вместо точки дня должно идти сообщение «вернись в серию»",
    "no_habits": "у человека нет привычек",
    "all_done": "привычки на сегодня закрыты (или осознанно пропущены) — напоминать нечего",
    "fresh_mark": "человек только что отметил привычку (меньше 30 минут назад) — «сверка курса» ему сейчас не нужна",
    "only_timed": "остались только привычки со своим временем, которое ещё не наступило — по ним придёт отдельное напоминание",
}


def record_skip(user_id, day, kind, reason):
    """Запоминает причину, по которой точка дня `kind` (например habit_checkpoint_10) не ушла человеку в день `day` (местный, YYYY-MM-DD)."""
    try:
        conn = connect()
        try:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO notification_skips(user_id, day, kind, reason) VALUES (?,?,?,?)",
                (int(user_id), str(day), str(kind), str(reason)),
            )
            if cursor.rowcount == 1:
                conn.execute("DELETE FROM notification_skips WHERE day < date(?, ?)", (str(day), f"-{KEEP_DAYS} days"))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Не удалось записать причину пропуска напоминания (%s)", user_id, exc_info=True)


def recent_skips(user_id, days=7):
    """Причины пропусков за последние дни, новые первыми: [{"day", "kind", "reason", "label"}]."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT day, kind, reason FROM notification_skips WHERE user_id=? AND day >= date('now', ?) ORDER BY day DESC, kind",
            (int(user_id), f"-{int(days)} days"),
        ).fetchall()
    finally:
        conn.close()
    return [{"day": r["day"], "kind": r["kind"], "reason": r["reason"], "label": REASON_LABELS.get(r["reason"], r["reason"])} for r in rows]
