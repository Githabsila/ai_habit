
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .core import connect
from .users import get_user, add_xp

MILESTONES = {
    30: ("Первый огонь", "Бронзовое пламя", "bronze_flame"),
    60: ("Стальной стержень", "Серебряный уголь", "silver_coal"),
    100: ("Сотня в огне", "Золотая искра", "gold_spark"),
    200: ("Несгораемый", "Платиновый факел", "platinum_torch"),
    300: ("Легенда", "Алмазный феникс", "diamond_phoenix"),
    365: ("Год в огне", "Секретная рамка", "secret_phoenix"),
}

PRAISE_A = [
    "Так держать, {name}. Я думал, ты сдашься ⚡️",
    "Ты меня удивляешь, {name}. Продолжай в том же духе 🔥",
    "Ого, {name}, а ты серьёзно настроен. Это круто 🚀",
    "Ещё один день закрыт. Не расслабляйся, {name} — серия растёт 🔥",
    "Вот это характер. Сегодня ты снова выбрал себя, {name} ⚡️",
    "Я вижу прогресс. {name}, продолжай давить вперёд 🔥",
    "Хорошо. День твой. Теперь не дай завтрашнему дню всё испортить 😈",
    "Серия не держится сама. Ты только что удержал её ещё на один день 🔥",
]

RISK_15 = "Внимание! До конца дня ещё есть время, но если не отметишь хотя бы одну привычку — потеряешь ударный режим. Ты же не хочешь начинать с нуля?"
RISK_23 = [
    "Я не могу поверить, ты хочешь потерять свой ударный режим? 😠",
    "Самое время доказать, что ты не сдался, или я ошибаюсь? 😡",
]

def ensure_tables():
    conn = connect()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS streak_meta(
        user_id INTEGER PRIMARY KEY,
        timezone TEXT DEFAULT 'UTC',
        rollover_day TEXT,
        onboarding_seen INTEGER DEFAULT 0,
        freeze_balance INTEGER DEFAULT 0,
        freeze_purchased_week TEXT,
        freeze_purchased_count INTEGER DEFAULT 0,
        temp_frame TEXT,
        temp_status TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS streak_days(
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        status TEXT NOT NULL,
        streak_after INTEGER DEFAULT 0,
        ai_message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        event_delivered INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, day)
    )""")
    c.execute("PRAGMA table_info(streak_days)")
    streak_day_cols = {r[1] for r in c.fetchall()}
    if "event_delivered" not in streak_day_cols:
        c.execute("ALTER TABLE streak_days ADD COLUMN event_delivered INTEGER DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS streak_rewards(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        milestone INTEGER NOT NULL,
        status TEXT NOT NULL,
        frame TEXT NOT NULL,
        permanent INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, milestone)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS streak_weekly_choices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        week_key TEXT NOT NULL,
        reward_type TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, week_key)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS streak_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, day, kind)
    )""")
    conn.commit()
    conn.close()

def _meta(user_id):
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT * FROM streak_meta WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO streak_meta(user_id) VALUES(?)", (user_id,))
        conn.commit()
        c.execute("SELECT * FROM streak_meta WHERE user_id=?", (user_id,))
        row = c.fetchone()
    conn.close()
    return row

def get_timezone(user_id):
    row = _meta(user_id)
    tz = row["timezone"] if row and row["timezone"] else "UTC"
    try:
        ZoneInfo(tz)
        return tz
    except Exception:
        return "UTC"

def set_timezone(user_id, timezone):
    try:
        ZoneInfo(timezone)
    except Exception:
        timezone = "UTC"
    conn = connect()
    conn.execute("""INSERT INTO streak_meta(user_id, timezone) VALUES(?,?)
                    ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone""",
                 (user_id, timezone))
    conn.commit()
    conn.close()

def local_today(user_id):
    return datetime.now(ZoneInfo(get_timezone(user_id))).date()

def day_key(d):
    return d.isoformat()

def week_key(d):
    return f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"

def _ensure_week(c, user_id, wk):
    c.execute("SELECT freeze_purchased_week, freeze_purchased_count FROM streak_meta WHERE user_id=?", (user_id,))
    r = c.fetchone()
    if not r or r["freeze_purchased_week"] != wk:
        c.execute("""UPDATE streak_meta SET freeze_purchased_week=?, freeze_purchased_count=0
                     WHERE user_id=?""", (wk, user_id))
        return 0
    return int(r["freeze_purchased_count"] or 0)

def generate_praise(user_id):
    user = get_user(user_id)
    name = (user["first_name"] if user else "Игрок").strip() or "Игрок"
    return random.choice(PRAISE_A).format(name=name)

def onboarding_message(user_id):
    user = get_user(user_id)
    name = (user["first_name"] if user else "Игрок").strip() or "Игрок"
    variants = [
        f"{name}, теперь всё просто: каждый день закрывай хотя бы одну привычку и не отдавай свою серию. Я буду следить за огнём — твоя задача не дать ему погаснуть. 🔥",
        f"{name}, с этого момента начинается ударный режим. Один день — маленькая победа, десятки дней — уже характер. Я рядом, но работу за тебя не сделаю. ⚡️",
        f"{name}, у тебя появился шанс построить серию, которой захочется хвастаться. Выполняй хотя бы одну привычку в день, собирай коины и открывай уникальные рамки. Не подведи меня. 🔥",
    ]
    return random.choice(variants)

def mark_onboarding_seen(user_id):
    conn = connect()
    conn.execute("UPDATE streak_meta SET onboarding_seen=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def should_show_onboarding(user_id):
    row = _meta(user_id)
    return bool(row and not row["onboarding_seen"])

def register_completion(user_id):
    """Засчитать первый completion за локальный день. Возвращает событие."""
    ensure_tables()
    today = local_today(user_id)
    today_s = day_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT status FROM streak_days WHERE user_id=? AND day=?", (user_id, today_s))
    existing = c.fetchone()
    if existing and existing["status"] in ("completed", "freeze"):
        c.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,))
        streak = int(c.fetchone()["streak"] or 0)
        conn.close()
        return {"counted": False, "streak": streak, "message": None, "milestone": None}
    c.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,))
    row = c.fetchone()
    streak = int(row["streak"] or 0)
    yesterday = day_key(today - timedelta(days=1))
    c.execute("SELECT status FROM streak_days WHERE user_id=? AND day=?", (user_id, yesterday))
    prev = c.fetchone()
    if prev and prev["status"] in ("completed", "freeze"):
        streak += 1
    else:
        streak = 1
    msg = generate_praise(user_id)
    c.execute("""INSERT OR REPLACE INTO streak_days(user_id,day,status,streak_after,ai_message,event_delivered)
                 VALUES(?,?,?,?,?,0)""", (user_id, today_s, "completed", streak, msg))
    c.execute("""UPDATE users SET streak=?, last_completed=? WHERE telegram_id=?""",
              (streak, today_s, user_id))
    conn.commit()
    conn.close()

    milestone = None
    if streak in MILESTONES:
        title, frame, code = MILESTONES[streak]
        conn = connect()
        c = conn.cursor()
        c.execute("""INSERT OR IGNORE INTO streak_rewards(user_id,milestone,status,frame,permanent)
                     VALUES(?,?,?,?,1)""", (user_id, streak, title, frame))
        c.execute("""UPDATE streak_meta SET temp_frame=?, temp_status=? WHERE user_id=?""",
                  (code, title, user_id))
        conn.commit()
        conn.close()
        milestone = {"days": streak, "status": title, "frame": frame, "code": code}
    else:
        # Временный статус/рамка отражают текущую серию, но исторические
        # milestone-награды хранятся отдельно и не исчезают.
        conn = connect()
        conn.execute("""UPDATE streak_meta SET temp_frame=?, temp_status=? WHERE user_id=?""",
                     ("streak_flame", f"В ударе {streak} дн.", user_id))
        conn.commit()
        conn.close()

    return {"counted": True, "streak": streak, "message": msg, "milestone": milestone}

def rollover_user(user_id):
    """Перевести привычки и streak на новый локальный день. Идемпотентно."""
    ensure_tables()
    today = local_today(user_id)
    today_s = day_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT * FROM streak_meta WHERE user_id=?", (user_id,))
    meta = c.fetchone()
    if not meta:
        c.execute("INSERT INTO streak_meta(user_id, rollover_day) VALUES(?,?)", (user_id, today_s))
        conn.commit()
        conn.close()
        return False

    if meta["rollover_day"] == today_s:
        conn.close()
        return False

    # Важный момент: если пользователь впервые попал в систему после долгого
    # перерыва, не создаём фиктивные пропуски до его первого дня.
    previous_day = day_key(today - timedelta(days=1))
    c.execute("SELECT status FROM streak_days WHERE user_id=? AND day=?", (user_id, previous_day))
    prev = c.fetchone()
    c.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,))
    u = c.fetchone()
    streak = int(u["streak"] or 0) if u else 0

    if streak > 0 and (not prev or prev["status"] not in ("completed", "freeze")):
        wk = week_key(today)
        purchased_count = _ensure_week(c, user_id, wk)
        c.execute("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (user_id,))
        bal = int(c.fetchone()["freeze_balance"] or 0)
        if bal > 0:
            bal -= 1
            c.execute("""UPDATE streak_meta SET freeze_balance=? WHERE user_id=?""", (bal, user_id))
            c.execute("""INSERT OR REPLACE INTO streak_days(user_id,day,status,streak_after,ai_message)
                         VALUES(?,?,?,?,?)""", (user_id, previous_day, "freeze", streak, "❄️ День сохранён заморозкой."))
        else:
            streak = 0
            c.execute("UPDATE users SET streak=0, last_completed=NULL WHERE telegram_id=?", (user_id,))
            c.execute("""INSERT OR REPLACE INTO streak_days(user_id,day,status,streak_after,ai_message)
                         VALUES(?,?,?,?,?)""", (user_id, previous_day, "missed", 0, "🔥 Серия погасла. Начни снова сегодня."))
            c.execute("UPDATE streak_meta SET temp_frame=NULL, temp_status=NULL WHERE user_id=?", (user_id,))

    c.execute("""UPDATE streak_meta SET rollover_day=? WHERE user_id=?""", (today_s, user_id))
    conn.commit()
    conn.close()
    return True

def reset_habits_for_user(user_id):
    conn = connect()
    conn.execute("""UPDATE habits SET completed=0, assigned_at=CURRENT_TIMESTAMP, reminder_sent=0
                    WHERE user_id=?""", (user_id,))
    conn.commit()
    conn.close()

def rollover_all_users():
    ensure_tables()
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT telegram_id FROM users")
    ids = [r["telegram_id"] for r in c.fetchall()]
    conn.close()
    changed = []
    for uid in ids:
        if rollover_user(uid):
            # Сохраняем снимок привычек до их сброса, чтобы недельный AI-анализ
            # продолжал работать и при индивидуальном локальном полуночи.
            try:
                today = local_today(uid)
                prev = day_key(today - timedelta(days=1))
                conn2 = connect()
                c2 = conn2.cursor()
                c2.execute("SELECT id,title,completed FROM habits WHERE user_id=?", (uid,))
                for h in c2.fetchall():
                    c2.execute("""INSERT INTO habit_logs(user_id,habit_id,habit_title,day,completed)
                                  VALUES(?,?,?,?,?)""",
                               (uid, h["id"], h["title"], prev, h["completed"]))
                conn2.commit()
                conn2.close()
            except Exception:
                try:
                    conn2.close()
                except Exception:
                    pass
            reset_habits_for_user(uid)
            changed.append(uid)
    return changed

def get_last7(user_id):
    ensure_tables()
    today = local_today(user_id)
    start = today - timedelta(days=6)
    conn = connect()
    c = conn.cursor()
    c.execute("""SELECT day,status,streak_after,ai_message FROM streak_days
                 WHERE user_id=? AND day>=? ORDER BY day ASC""", (user_id, day_key(start)))
    rows = {r["day"]: dict(r) for r in c.fetchall()}
    conn.close()
    result = []
    for i in range(7):
        d = start + timedelta(days=i)
        ds = day_key(d)
        item = rows.get(ds)
        result.append({
            "day": ds,
            "label": ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][d.weekday()],
            "status": item["status"] if item else "empty",
            "streak": item["streak_after"] if item else 0,
            "bonus": d.weekday() == 6,
        })
    return result

def get_streak_status(user_id):
    ensure_tables()
    today = local_today(user_id)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT * FROM streak_meta WHERE user_id=?", (user_id,))
    meta = c.fetchone()
    c.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,))
    user = c.fetchone()
    c.execute("SELECT * FROM streak_rewards WHERE user_id=? ORDER BY milestone DESC", (user_id,))
    rewards = [dict(r) for r in c.fetchall()]
    conn.close()
    return {
        "days": int(user["streak"] or 0) if user else 0,
        "today": day_key(today),
        "last7": get_last7(user_id),
        "freeze_balance": int(meta["freeze_balance"] or 0) if meta else 0,
        "freeze_purchased_count": int(meta["freeze_purchased_count"] or 0) if meta else 0,
        "freeze_week": meta["freeze_purchased_week"] if meta else None,
        "onboarding_seen": bool(meta["onboarding_seen"]) if meta else False,
        "temp_frame": meta["temp_frame"] if meta else None,
        "temp_status": meta["temp_status"] if meta else None,
        "rewards": rewards,
    }

def buy_freeze(user_id):
    ensure_tables()
    today = local_today(user_id)
    wk = week_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT xp, freeze_purchased_week, freeze_purchased_count, freeze_balance FROM users JOIN streak_meta ON users.telegram_id=streak_meta.user_id WHERE telegram_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "user_not_found"}
    count = int(row["freeze_purchased_count"] or 0) if row["freeze_purchased_week"] == wk else 0
    bal = int(row["freeze_balance"] or 0)
    xp = int(row["xp"] or 0)
    if count >= 2:
        conn.close()
        return {"ok": False, "error": "weekly_limit"}
    if bal >= 2:
        conn.close()
        return {"ok": False, "error": "max_balance"}
    if xp < 200:
        conn.close()
        return {"ok": False, "error": "not_enough_coins"}
    c.execute("UPDATE users SET xp=xp-200 WHERE telegram_id=?", (user_id,))
    c.execute("""UPDATE streak_meta SET freeze_balance=?, freeze_purchased_week=?, freeze_purchased_count=?
                 WHERE user_id=?""", (bal + 1, wk, count + 1, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "balance": bal + 1, "cost": 200}

def claim_weekly_reward(user_id, reward_type):
    ensure_tables()
    if reward_type not in ("coins", "frame"):
        return {"ok": False, "error": "invalid_reward"}
    today = local_today(user_id)
    # Бонус выбирается в воскресенье за завершённую предыдущую неделю.
    if today.weekday() != 6:
        return {"ok": False, "error": "not_sunday"}
    wk = week_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT 1 FROM streak_weekly_choices WHERE user_id=? AND week_key=?", (user_id,wk))
    if c.fetchone():
        conn.close()
        return {"ok": False, "error": "already_claimed"}
    prev_start = today - timedelta(days=7)
    prev_end = today - timedelta(days=1)
    c.execute("""SELECT COUNT(*) AS missed FROM streak_days
                 WHERE user_id=? AND day BETWEEN ? AND ? AND status NOT IN ('completed')""",
              (user_id, day_key(prev_start), day_key(prev_end)))
    missed = int(c.fetchone()["missed"] or 0)
    c.execute("""SELECT COUNT(*) AS completed FROM streak_days
                 WHERE user_id=? AND day BETWEEN ? AND ? AND status='completed'""",
              (user_id, day_key(prev_start), day_key(prev_end)))
    completed = int(c.fetchone()["completed"] or 0)
    if completed < 7 or missed > 0:
        conn.close()
        return {"ok": False, "error": "week_not_perfect"}
    if reward_type == "coins":
        add_xp(user_id, 200)
    else:
        c.execute("""UPDATE streak_meta SET temp_frame='weekly_frame', temp_status='Неделя в огне'
                     WHERE user_id=?""", (user_id,))
    c.execute("""INSERT INTO streak_weekly_choices(user_id,week_key,reward_type) VALUES(?,?,?)""",
              (user_id,wk,reward_type))
    conn.commit()
    conn.close()
    return {"ok": True, "reward": reward_type}

def get_weekly_bonus_available(user_id):
    today = local_today(user_id)
    if today.weekday() != 6:
        return False
    wk = week_key(today)
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT 1 FROM streak_weekly_choices WHERE user_id=? AND week_key=?", (user_id,wk))
    claimed = c.fetchone()
    start = today - timedelta(days=7)
    end = today - timedelta(days=1)
    c.execute("""SELECT COUNT(*) AS n FROM streak_days
                 WHERE user_id=? AND day BETWEEN ? AND ? AND status='completed'""",
              (user_id,day_key(start),day_key(end)))
    completed = int(c.fetchone()["n"] or 0)
    conn.close()
    return not claimed and completed == 7


def claim_notification(user_id, day, kind):
    conn = connect()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO streak_notifications(user_id,day,kind) VALUES(?,?,?)",
                  (user_id, day, kind))
        conn.commit()
        ok = True
    except Exception:
        ok = False
        conn.rollback()
    conn.close()
    return ok

def has_completed_today(user_id):
    today_s = day_key(local_today(user_id))
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT status FROM streak_days WHERE user_id=? AND day=?", (user_id, today_s))
    row = c.fetchone()
    conn.close()
    return bool(row and row["status"] == "completed")

def get_streak_users():
    conn = connect()
    c = conn.cursor()
    c.execute("""SELECT u.telegram_id FROM users u
                 WHERE EXISTS(SELECT 1 FROM habits h WHERE h.user_id=u.telegram_id)""")
    ids = [r["telegram_id"] for r in c.fetchall()]
    conn.close()
    return ids


def consume_completion_event(user_id):
    ensure_tables()
    today_s = day_key(local_today(user_id))
    conn = connect()
    c = conn.cursor()
    c.execute("""SELECT day, status, streak_after, ai_message, event_delivered
                 FROM streak_days WHERE user_id=? AND day=?""", (user_id, today_s))
    row = c.fetchone()
    if not row or row["status"] != "completed" or int(row["event_delivered"] or 0):
        conn.close()
        return None
    c.execute("""UPDATE streak_days SET event_delivered=1 WHERE user_id=? AND day=? AND event_delivered=0""",
              (user_id, today_s))
    conn.commit()
    conn.close()
    return {
        "day": row["day"],
        "status": row["status"],
        "streak": int(row["streak_after"] or 0),
        "message": row["ai_message"],
    }
