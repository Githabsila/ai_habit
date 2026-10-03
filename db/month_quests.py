"""
Задания месяца — как в Duolingo («Задания октября 2/60»).

Прогресс — сколько ежедневных заданий (db/quests.py, 3 в день) пользователь
выполнил и забрал за текущий месяц; отдельного счётчика нет, считаем по
таблице daily_quests (claimed=1). По пути — 4 сундука: за каждые 15 заданий
(15/30/45/60). Сундук открывается кнопкой, награда — Adam Coin и алмазы;
последний, на 60 заданиях, ещё даёт значок месяца (запись в достижениях,
видна в профиле). Сумма наград — те же 300 Adam Coin и 3 алмаза, что раньше
давались за «идеальный месяц», но теперь пропущенный день не обнуляет всё.

Сундуки нужно открыть в течение месяца: у каждого месяца свой набор, и
прошлый месяц закрыт для получения.

Монеты начисляются через add_xp — как и награды за сами задания; значит
они идут и в уровень игрока, но не могут быть «подарены» или переданы.
"""
import calendar

from .core import connect

MONTH_GOAL = 60

# at — сколько заданий нужно; последний сундук помечен final и даёт значок.
CHESTS = (
    {"at": 15, "coins": 25, "diamonds": 0, "final": False},
    {"at": 30, "coins": 50, "diamonds": 1, "final": False},
    {"at": 45, "coins": 75, "diamonds": 1, "final": False},
    {"at": 60, "coins": 150, "diamonds": 1, "final": True},
)
CHEST_BY_AT = {chest["at"]: chest for chest in CHESTS}

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _today(user_id):
    from .streak import local_today
    return local_today(user_id)


def month_key(day):
    return f"{day.year}-{day.month:02d}"


def month_title(day):
    return f"Задания {MONTHS_GENITIVE[day.month - 1]}"


def badge_title(day):
    """Название значка месяца в достижениях — с годом, чтобы «октябрь 2026»
    и «октябрь 2027» не слились в один."""
    return f"{month_title(day)} {day.year}"


def _points(cursor, user_id, key):
    row = cursor.execute(
        "SELECT COUNT(*) AS n FROM daily_quests WHERE user_id=? AND claimed=1 AND day LIKE ?",
        (user_id, f"{key}-%"),
    ).fetchone()
    return int(row["n"] or 0)


def _claimed_chests(cursor, user_id, key):
    rows = cursor.execute(
        "SELECT milestone FROM month_chests WHERE user_id=? AND month_key=?", (user_id, key)
    ).fetchall()
    return {int(r["milestone"]) for r in rows}


def get_month_quests(user_id):
    today = _today(user_id)
    key = month_key(today)
    conn = connect()
    try:
        c = conn.cursor()
        points = _points(c, user_id, key)
        claimed = _claimed_chests(c, user_id, key)
    finally:
        conn.close()
    chests = [
        {
            "at": chest["at"],
            "coins": chest["coins"],
            "diamonds": chest["diamonds"],
            "final": chest["final"],
            "reached": points >= chest["at"],
            "claimed": chest["at"] in claimed,
        }
        for chest in CHESTS
    ]
    return {
        "month_key": key,
        "title": month_title(today),
        "points": points,
        "goal": MONTH_GOAL,
        "chests": chests,
        "claimable": sum(1 for ch in chests if ch["reached"] and not ch["claimed"]),
        "days_left": calendar.monthrange(today.year, today.month)[1] - today.day,
    }


def claim_month_chest(user_id, at):
    """Открывает сундук. {"ok": True, "at", "coins", "diamonds", "badge"} либо
    {"error": invalid_chest | not_reached | already_claimed}. Запись о
    получении вставляется первой и атомарно (PRIMARY KEY), поэтому два
    одновременных нажатия выдадут награду один раз."""
    from .users import add_xp, add_diamonds

    try:
        at = int(at)
    except (TypeError, ValueError):
        return {"error": "invalid_chest"}
    chest = CHEST_BY_AT.get(at)
    if chest is None:
        return {"error": "invalid_chest"}

    today = _today(user_id)
    key = month_key(today)
    conn = connect()
    try:
        c = conn.cursor()
        if _points(c, user_id, key) < at:
            return {"error": "not_reached"}
        try:
            c.execute(
                "INSERT INTO month_chests(user_id, month_key, milestone, coins, diamonds) VALUES (?, ?, ?, ?, ?)",
                (user_id, key, at, chest["coins"], chest["diamonds"]),
            )
            conn.commit()
        except Exception:
            # PRIMARY KEY(user_id, month_key, milestone) — уже открыт.
            return {"error": "already_claimed"}
    finally:
        conn.close()

    add_xp(user_id, chest["coins"])
    if chest["diamonds"]:
        add_diamonds(user_id, chest["diamonds"])
    badge = _grant_month_badge(user_id, today) if chest["final"] else None
    return {"ok": True, "at": at, "coins": chest["coins"], "diamonds": chest["diamonds"], "badge": badge}


def _grant_month_badge(user_id, today):
    title = badge_title(today)
    conn = connect()
    try:
        c = conn.cursor()
        exists = c.execute(
            "SELECT 1 FROM achievements WHERE user_id=? AND title=?", (user_id, title)
        ).fetchone()
        if exists:
            return title
        c.execute(
            "INSERT INTO achievements(user_id, title, description) VALUES (?, ?, ?)",
            (user_id, title, f"Выполнено {MONTH_GOAL} ежедневных заданий за месяц"),
        )
        conn.commit()
    finally:
        conn.close()
    from .activity_feed import log_activity_event
    log_activity_event(user_id, "achievement", {"detail": title})
    return title
