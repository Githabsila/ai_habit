"""
Отмена отметки привычки (правка владельца, 24.10.2026): человек случайно нажал на галочку — двойной тап по зелёной галочке, вопрос
«Убрать отметку о выполнении?», и привычка снова не выполнена.

Откатываем ровно то, что дала отметка, и ничего сверх:
  • сама привычка (счётчик «4/4» возвращается к «3/4»), запись в журнале моментов выполнения, total_completed;
  • монеты и опыт — ровно столько, сколько заплатила именно эта отметка (она записана в habit_marks.coins);
  • место в дневном потолке наград — освобождается, чтобы повторная отметка получила монеты как обычно;
  • статистика и календарь за день, прогресс «Получить 20 Adam Coin», питомец (забота −1);
  • серия: если после отмены в этот день не осталось ни одной выполненной привычки — серия, рекорд и рамка возвращаются к тому,
    что было до первой отметки дня (habit_streak_snapshots), иначе день остаётся засчитанным другой привычкой;
  • окно «×2» — если убрана последняя отметка, окно становится таким, каким было до неё.
Что вернуть нельзя и остаётся как есть: уже выданные достижения и рамки за рубежи серии, выполненные квесты и задания (они «липкие»),
награда пригласившему, отправленные пуши. Повторно они не выдаются.

Защита от накрутки: отменить можно только отметку сегодняшнего (местного) дня, которую записал журнал; не больше
UNDO_DAILY_LIMIT раз в день; и только если монеты за отметку ещё на балансе — иначе «отметил → потратил → убрал → отметил снова»
давало бы монеты даром.
"""
import logging

from .core import connect

logger = logging.getLogger(__name__)

UNDO_DAILY_LIMIT = 5

ERROR_NOT_FOUND = "not_found"
ERROR_NOT_COMPLETED = "habit_not_done"
ERROR_TOO_LATE = "undo_too_late"
ERROR_LIMIT = "undo_limit"
ERROR_COINS_SPENT = "undo_coins_spent"


# ---------------------------------------------------------------------------------------------------------------- запись при отметке

def snapshot_streak(user_id):
    """Серия человека ДО учёта сегодняшнего дня: users.streak / last_completed / best_streak и рамка со статусом серии."""
    try:
        conn = connect()
        try:
            user = conn.execute("SELECT streak, last_completed, best_streak FROM users WHERE telegram_id=?", (user_id,)).fetchone()
            meta = conn.execute("SELECT temp_frame, temp_status FROM streak_meta WHERE user_id=?", (user_id,)).fetchone()
        finally:
            conn.close()
        if user is None:
            return None
        return {
            "streak": int(user["streak"] or 0), "last_completed": user["last_completed"], "best_streak": user["best_streak"],
            "temp_frame": meta["temp_frame"] if meta else None, "temp_status": meta["temp_status"] if meta else None,
        }
    except Exception:
        logger.warning("Не удалось сохранить снимок серии (%s)", user_id, exc_info=True)
        return None


def snapshot_tasks(user_id):
    """Дневные задания сегодняшнего (серверного) дня до отметки: {id: выполнено}. Нужны, чтобы понять, какие из них закрыла отметка."""
    try:
        conn = connect()
        try:
            rows = conn.execute("SELECT id, completed FROM daily_tasks WHERE user_id=? AND task_date=date('now')", (user_id,)).fetchall()
        finally:
            conn.close()
        return {row["id"]: int(row["completed"] or 0) for row in rows}
    except Exception:
        logger.warning("Не удалось прочитать дневные задания (%s)", user_id, exc_info=True)
        return {}


def tasks_completed_since(user_id, before):
    """(ids через запятую, сумма наград) заданий, которые стали выполненными после снимка `before`."""
    try:
        now = snapshot_tasks(user_id)
        ids = [task_id for task_id, done in now.items() if done and not before.get(task_id, 1)]
        if not ids:
            return "", 0
        conn = connect()
        try:
            marks = ",".join("?" for _ in ids)
            bonus = conn.execute(f"SELECT COALESCE(SUM(reward), 0) AS total FROM daily_tasks WHERE id IN ({marks})", ids).fetchone()["total"]
        finally:
            conn.close()
        return ",".join(str(task_id) for task_id in ids), int(bonus)
    except Exception:
        logger.warning("Не удалось определить закрытые дневные задания (%s)", user_id, exc_info=True)
        return "", 0


def record_mark(*, user_id, habit_id, day, server_day, event_id, coins, window_before, task_ids="", task_bonus=0, snapshot=None, streak_counted=False):
    """Запоминает, что дала отметка. Сбой записи не должен мешать отметке — тогда её просто нельзя будет отменить."""
    try:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO habit_marks(user_id, habit_id, day, server_day, event_id, coins, task_bonus, task_ids, window_before) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, habit_id, day, server_day, event_id, int(coins or 0), int(task_bonus or 0), task_ids or "", window_before),
            )
            if streak_counted and snapshot:
                conn.execute(
                    "INSERT OR REPLACE INTO habit_streak_snapshots(user_id, day, streak, last_completed, best_streak, temp_frame, temp_status) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (user_id, day, snapshot["streak"], snapshot["last_completed"], snapshot["best_streak"], snapshot["temp_frame"], snapshot["temp_status"]),
                )
            conn.execute("DELETE FROM habit_marks WHERE day < date(?, '-8 days')", (day,))
            conn.execute("DELETE FROM habit_streak_snapshots WHERE day < date(?, '-8 days')", (day,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning("Не удалось записать отметку привычки в журнал отмены (%s, привычка %s)", user_id, habit_id, exc_info=True)


# ---------------------------------------------------------------------------------------------------------------- проверка

def _today(user_id):
    from .streak import day_key, local_today
    return day_key(local_today(user_id))


def undo_count_today(user_id, day=None):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM habit_marks WHERE user_id=? AND day=? AND undone_at IS NOT NULL", (user_id, day or _today(user_id)),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def check_undo(habit_id):
    """Можно ли убрать отметку прямо сейчас: {"ok": True, "mark": Row} или {"error": код}. Ничего не меняет."""
    conn = connect()
    try:
        habit = conn.execute("SELECT * FROM habits WHERE id=?", (habit_id,)).fetchone()
        if habit is None:
            return {"error": ERROR_NOT_FOUND}
        if not habit["completed"]:
            return {"error": ERROR_NOT_COMPLETED}
        user_id = habit["user_id"]
        day = _today(user_id)
        mark = conn.execute(
            "SELECT * FROM habit_marks WHERE habit_id=? AND undone_at IS NULL ORDER BY id DESC LIMIT 1", (habit_id,),
        ).fetchone()
        if mark is None or mark["day"] != day:
            return {"error": ERROR_TOO_LATE}
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM habit_marks WHERE user_id=? AND day=? AND undone_at IS NOT NULL", (user_id, day),
        ).fetchone()["n"]
        if used >= UNDO_DAILY_LIMIT:
            return {"error": ERROR_LIMIT}
        owed = int(mark["coins"] or 0) + int(mark["task_bonus"] or 0)
        if owed > 0:
            balance = conn.execute("SELECT xp FROM users WHERE telegram_id=?", (user_id,)).fetchone()
            have = int(balance["xp"] or 0) if balance else 0
            if have < owed:
                return {"error": ERROR_COINS_SPENT}
        return {"ok": True, "habit": habit, "mark": mark, "user_id": user_id, "day": day}
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- откат

def undo_mark(habit_id):
    """Убирает отметку привычки. Возвращает {"ok": True, "user_id", "coins_back", "streak_restored"} или {"error": код}."""
    verdict = check_undo(habit_id)
    if "error" in verdict:
        return verdict
    habit, mark, user_id, day = verdict["habit"], verdict["mark"], verdict["user_id"], verdict["day"]
    coins = int(mark["coins"] or 0)
    owed = coins + int(mark["task_bonus"] or 0)
    flipped = [int(x) for x in (mark["task_ids"] or "").split(",") if x]
    target = int(habit["target_count"] or 1)

    conn = connect()
    try:
        # Занимаем отметку первой: двойная отправка запроса откатит её один раз.
        claimed = conn.execute("UPDATE habit_marks SET undone_at=datetime('now') WHERE id=? AND undone_at IS NULL", (mark["id"],))
        if claimed.rowcount != 1:
            conn.rollback()
            return {"error": ERROR_NOT_COMPLETED}

        conn.execute(
            "UPDATE habits SET completed=0, progress_count=CASE WHEN ?>1 THEN ?-1 ELSE 0 END WHERE id=?", (target, target, habit_id),
        )
        if mark["event_id"]:
            conn.execute("DELETE FROM habit_completion_events WHERE id=?", (mark["event_id"],))
        conn.execute("UPDATE users SET total_completed = MAX(total_completed - 1, 0) WHERE telegram_id=?", (user_id,))

        if owed:
            conn.execute(
                "UPDATE users SET xp = MAX(xp - ?, 0), total_xp = MAX(COALESCE(total_xp,0) - ?, 0), "
                "level = MAX(COALESCE(total_xp,0) - ?, 0) / 100 + 1 WHERE telegram_id=?",
                (owed, owed, owed, user_id),
            )
        conn.execute("UPDATE habit_reward_days SET n = MAX(n - 1, 0) WHERE user_id=? AND day=?", (user_id, day))

        server_day = mark["server_day"]
        conn.execute("UPDATE calendar SET completed = MAX(completed - 1, 0) WHERE user_id=? AND day=?", (user_id, server_day))
        conn.execute(
            "INSERT INTO statistics(user_id, completed, gained_xp, stat_date) VALUES (?, ?, ?, ?)",
            (user_id, -(1 + len(flipped)), -owed, server_day),
        )
        # Дневные задания: прогресс «Получить 20 Adam Coin» уменьшается на заплаченное, а закрытые этой отметкой снова открываются.
        if coins:
            conn.execute(
                "UPDATE daily_tasks SET progress = MAX(progress - ?, 0) WHERE user_id=? AND task=? AND task_date=? AND completed=0",
                (coins, user_id, "Получить 20 Adam Coin", server_day),
            )
        for task_id in flipped:
            conn.execute(
                "UPDATE daily_tasks SET completed=0, progress = MAX(progress - CASE task WHEN ? THEN ? ELSE 1 END, 0) WHERE id=? AND user_id=?",
                ("Получить 20 Adam Coin", coins, task_id, user_id),
            )
        conn.execute("UPDATE virtual_pets SET care_points = MAX(care_points - 1, 0) WHERE user_id=?", (user_id,))

        # Окно «×2»: возвращаем прежнее, только если после этой отметки других не было (иначе окно уже принадлежит им).
        later = conn.execute(
            "SELECT 1 FROM habit_marks WHERE user_id=? AND day=? AND undone_at IS NULL AND id>? LIMIT 1", (user_id, day, mark["id"]),
        ).fetchone()
        if later is None:
            conn.execute("UPDATE streak_meta SET bonus_window_until=? WHERE user_id=?", (mark["window_before"], user_id))

        # Серия: день остаётся засчитанным, пока выполнена хотя бы одна привычка.
        streak_restored = False
        left = conn.execute("SELECT COUNT(*) AS n FROM habits WHERE user_id=? AND completed=1", (user_id,)).fetchone()["n"]
        if left == 0:
            snap = conn.execute("SELECT * FROM habit_streak_snapshots WHERE user_id=? AND day=?", (user_id, day)).fetchone()
            if snap is not None:
                conn.execute(
                    "UPDATE users SET streak=?, last_completed=?, best_streak=? WHERE telegram_id=?",
                    (snap["streak"], snap["last_completed"], snap["best_streak"], user_id),
                )
                conn.execute("UPDATE streak_meta SET temp_frame=?, temp_status=? WHERE user_id=?", (snap["temp_frame"], snap["temp_status"], user_id))
                conn.execute("DELETE FROM streak_days WHERE user_id=? AND day=? AND status='completed'", (user_id, day))
                conn.execute("DELETE FROM habit_streak_snapshots WHERE user_id=? AND day=?", (user_id, day))
                streak_restored = True
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    from .tracking import track_server
    track_server(user_id, "habit_undone", meta={"coins": owed, "streak_restored": streak_restored})
    return {"ok": True, "user_id": user_id, "coins_back": owed, "streak_restored": streak_restored}
