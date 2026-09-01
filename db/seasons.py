"""
Roadmap #9 — сезонные ивенты. Осознанный масштаб: сезон = календарный
месяц — тот же ритм, что уже использует награда за идеальный месяц (см.
db/monthly_streak.py) — не отдельный, рассинхронизирующийся календарь.
Рейтинг сезона считается "на лету" суммой gained_xp из statistics за
текущий месяц (та же таблица, что уже питает /api/progress/stats), без
отдельного счётчика, который пришлось бы аккуратно инкрементировать в
каждом месте начисления XP. В конце месяца (day=1 следующего) топ-3
получают разовую награду — см. season_scheduler.py.
"""
import time
from datetime import date

from .core import connect

SEASON_TOP_REWARDS = {1: (300, 3), 2: (200, 2), 3: (100, 1)}  # rank: (coins, diamonds)

# Производительность: сезонный лидерборд — это агрегатный запрос по ВСЕМ
# пользователям (JOIN + GROUP BY), пересчитывать его на каждый запрос
# вкладки "Рейтинг" от каждого пользователя расточительно, а секундная
# точность тут никому не нужна — 30 секунд кэша сглаживают пики нагрузки,
# не делая данные заметно "устаревшими".
_LEADERBOARD_CACHE = {"at": 0, "data": None}
_LEADERBOARD_TTL_SECONDS = 30


def current_season_key():
    return date.today().strftime("%Y-%m")


def _fetch_full_season_leaderboard():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, u.first_name, u.username, u.avatar_id, u.frame_id,
               SUM(s.gained_xp) as season_xp
        FROM statistics s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE s.stat_date >= date('now', 'start of month') AND u.banned=0
        GROUP BY s.user_id
        ORDER BY season_xp DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "telegram_id": r["user_id"],
            "first_name": r["first_name"],
            "username": r["username"],
            "avatar_id": r["avatar_id"],
            "frame_id": r["frame_id"],
            "season_xp": r["season_xp"] or 0,
        }
        for r in rows
    ]


def clear_season_leaderboard_cache():
    """Сбрасывает кэш вручную — нужно только тестам (иначе кэш одного
    теста мог бы отдать устаревшие данные следующему в том же процессе,
    т.к. кэш модульный/на весь процесс, а не per-request)."""
    _LEADERBOARD_CACHE["data"] = None
    _LEADERBOARD_CACHE["at"] = 0


def _get_cached_full_leaderboard():
    now = time.monotonic()
    if _LEADERBOARD_CACHE["data"] is None or now - _LEADERBOARD_CACHE["at"] > _LEADERBOARD_TTL_SECONDS:
        _LEADERBOARD_CACHE["data"] = _fetch_full_season_leaderboard()
        _LEADERBOARD_CACHE["at"] = now
    return _LEADERBOARD_CACHE["data"]


def get_season_leaderboard(limit=10):
    """Топ пользователей по Adam Coin, заработанным ЗА ТЕКУЩИЙ сезон
    (месяц) — отдельно от общего рейтинга (db/users.py::get_rating,
    который считает по streak/общему xp за всё время). Кэшируется на
    _LEADERBOARD_TTL_SECONDS — это агрегат по всем пользователям, не
    имеет смысла пересчитывать при каждом открытии вкладки."""
    return _get_cached_full_leaderboard()[:limit]


def get_season_rank(user_id):
    """Место конкретного пользователя в сезонном рейтинге (для профиля —
    "ты #4 в этом сезоне"), даже если он не входит в топ-10."""
    leaderboard = get_season_leaderboard(limit=100000)
    for i, row in enumerate(leaderboard):
        if row["telegram_id"] == user_id:
            return {"rank": i + 1, "season_xp": row["season_xp"], "total": len(leaderboard)}
    return None


# Улучшение #40: "обогнали в рейтинге" имеет смысл только в реально
# соревновательной зоне — за пределами топ-100 обычные шумовые перестановки
# каждый день превратили бы пуш в спам без всякой мотивационной ценности.
RANK_OVERTAKE_NOTIFY_LIMIT = 100


def get_rank_overtakes_and_update_snapshot():
    """Сравнивает текущий сезонный рейтинг с последним сохранённым снимком
    на пользователя. Возвращает список тех, чьё место ухудшилось с прошлой
    проверки (в пределах RANK_OVERTAKE_NOTIFY_LIMIT), с именем того, кто
    теперь стоит на их прежнем месте. Одним проходом же обновляет снимок
    ВСЕХ участников сезона до актуального состояния — вызывать раз в день,
    не чаще (иначе "обогнал" будет фиксироваться на каждое мелкое колебание)."""
    leaderboard = get_season_leaderboard(limit=100000)
    season_key = current_season_key()

    conn = connect()
    c = conn.cursor()
    c.execute("SELECT user_id, season_key, rank FROM season_rank_snapshot")
    previous = {r["user_id"]: {"season_key": r["season_key"], "rank": r["rank"]} for r in c.fetchall()}

    overtaken = []
    for i, row in enumerate(leaderboard):
        rank = i + 1
        uid = row["telegram_id"]
        prev = previous.get(uid)
        if (
            prev
            and prev["season_key"] == season_key
            and rank > prev["rank"]
            and rank <= RANK_OVERTAKE_NOTIFY_LIMIT
        ):
            # Кто теперь стоит на прежнем месте пользователя (индекс rank-1
            # 0-based == позиция prev["rank"] в 1-based нумерации).
            overtaker_idx = prev["rank"] - 1
            overtaker_name = leaderboard[overtaker_idx]["first_name"] if 0 <= overtaker_idx < len(leaderboard) else None
            overtaken.append({
                "user_id": uid,
                "old_rank": prev["rank"],
                "new_rank": rank,
                "overtaker_name": overtaker_name,
            })
        c.execute(
            "INSERT INTO season_rank_snapshot(user_id, season_key, rank, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET season_key=excluded.season_key, rank=excluded.rank, updated_at=excluded.updated_at",
            (uid, season_key, rank),
        )
    conn.commit()
    conn.close()
    return overtaken


def award_season_rewards():
    """Вызывается раз в месяц (1-го числа, до сброса) — раздаёт монеты/
    алмазы топ-3 ПРЕДЫДУЩЕГО сезона. Идемпотентно (UNIQUE(user_id,
    season_key)) — повторный запуск в тот же день ничего не сломает."""
    from datetime import timedelta
    from .users import add_xp, add_diamonds

    last_day_prev_month = date.today().replace(day=1) - timedelta(days=1)
    season_key = last_day_prev_month.strftime("%Y-%m")

    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.user_id, SUM(s.gained_xp) as season_xp
        FROM statistics s
        JOIN users u ON u.telegram_id = s.user_id
        WHERE s.stat_date >= ? AND s.stat_date <= ? AND u.banned=0
        GROUP BY s.user_id
        ORDER BY season_xp DESC
        LIMIT 3
    """, (season_key + "-01", str(last_day_prev_month)))
    top3 = cursor.fetchall()
    conn.close()

    awarded = []
    for i, row in enumerate(top3):
        rank = i + 1
        coins, diamonds = SEASON_TOP_REWARDS[rank]
        conn = connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO season_rewards(user_id, season_key, rank, coins, diamonds) VALUES (?,?,?,?,?)",
                (row["user_id"], season_key, rank, coins, diamonds),
            )
            conn.commit()
            already = False
        except Exception:
            already = True
        conn.close()
        if not already:
            add_xp(row["user_id"], coins)
            add_diamonds(row["user_id"], diamonds)
            awarded.append({"user_id": row["user_id"], "rank": rank, "coins": coins, "diamonds": diamonds})
    return awarded
