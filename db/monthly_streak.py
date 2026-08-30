import calendar
from datetime import date

from .core import connect
from .users import add_xp, add_diamonds

# =====================================
# МЕСЯЧНАЯ СЕРИЯ 2+ ПРИВЫЧЕК (пром 8, дополнение)
# =====================================
# Идея: удвоение Adam Coin (см. db/habits.py) уже мотивирует закрывать
# привычки подряд в течение дня. Это добавляет ВТОРОЙ, более долгий цикл
# мотивации — 1 балл за каждый день, в который закрыто 2+ привычки, к
# счётчику месяца (например "18/30"). Если ВСЕ дни месяца набрали
# балл — в последний день месяца выдаётся награда: Adam Coin + немного
# алмазов (премиальная валюта, которую иначе можно только купить).

MULTI_HABIT_THRESHOLD = 2
MONTH_END_COIN_REWARD = 300
MONTH_END_DIAMOND_REWARD = 3


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def record_multi_habit_day(user_id, day_iso):
    """Отмечает, что в этот локальный день у пользователя было закрыто
    >= MULTI_HABIT_THRESHOLD привычек — вызывается из db/habits.py
    complete_habit. Идемпотентно (один балл в день максимум)."""
    conn = connect()
    conn.execute(
        "INSERT OR IGNORE INTO multi_habit_days(user_id, day) VALUES (?, ?)",
        (user_id, day_iso),
    )
    conn.commit()
    conn.close()


def get_monthly_progress(user_id, today=None):
    today = today or date.today()
    mk = month_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) AS n FROM multi_habit_days WHERE user_id=? AND day LIKE ?",
        (user_id, f"{mk}-%"),
    )
    points = int(c.fetchone()["n"] or 0)
    c.execute(
        "SELECT coins, diamonds FROM monthly_streak_rewards WHERE user_id=? AND month_key=?",
        (user_id, mk),
    )
    reward_row = c.fetchone()
    conn.close()
    total = days_in_month(today)
    return {
        "points": points,
        "total": total,
        "month_key": mk,
        "claimed": reward_row is not None,
        "perfect": points >= total,
    }


def claim_month_end_reward(user_id, for_day):
    """Идемпотентно выдаёт награду за идеальный месяц, если for_day —
    последний день своего месяца и все его дни набрали балл. for_day —
    date локального "вчера" на момент rollover (см. db/streak.py
    rollover_user) — то есть месяц, который только что завершился.
    Возвращает награду (dict) либо None, если сегодня не конец месяца,
    месяц не идеальный, или награда уже была выдана."""
    if for_day.day != days_in_month(for_day):
        return None

    progress = get_monthly_progress(user_id, for_day)
    if progress["claimed"] or not progress["perfect"]:
        return None

    conn = connect()
    try:
        conn.execute(
            "INSERT INTO monthly_streak_rewards(user_id, month_key, coins, diamonds) VALUES (?,?,?,?)",
            (user_id, progress["month_key"], MONTH_END_COIN_REWARD, MONTH_END_DIAMOND_REWARD),
        )
        conn.commit()
    except Exception:
        # UNIQUE(user_id, month_key) — уже выдано параллельно/ранее.
        conn.close()
        return None
    conn.close()

    add_xp(user_id, MONTH_END_COIN_REWARD)
    add_diamonds(user_id, MONTH_END_DIAMOND_REWARD)
    return {
        "coins": MONTH_END_COIN_REWARD,
        "diamonds": MONTH_END_DIAMOND_REWARD,
        "month_key": progress["month_key"],
        "days": progress["total"],
    }


def consume_month_end_reward_event(user_id):
    """Аналог consume_completion_event для наградного окна месяца —
    отдаёт недоставленную награду один раз и помечает её доставленной,
    чтобы повторная загрузка Mini App не показывала окно снова."""
    conn = connect()
    c = conn.cursor()
    c.execute(
        """SELECT id, month_key, coins, diamonds FROM monthly_streak_rewards
           WHERE user_id=? AND event_delivered=0 ORDER BY id DESC LIMIT 1""",
        (user_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    c.execute(
        "UPDATE monthly_streak_rewards SET event_delivered=1 WHERE id=?",
        (row["id"],),
    )
    conn.commit()
    conn.close()
    return {
        "month_key": row["month_key"],
        "coins": int(row["coins"] or 0),
        "diamonds": int(row["diamonds"] or 0),
    }
