"""
Roadmap #12 — ежедневные микро-квесты, отдельно от обычных привычек:
короткие, разные каждый день, с небольшой наградой в Adam Coin. В отличие
от daily_tasks.py (та система — фиксированные 2 задачи "выполнить
привычку"/"получить 20 монет", показывается всегда одинаково), квесты
каждый день другие и опираются на РЕАЛЬНОЕ состояние дня (привычки,
категории, время выполнения), а не на отдельный прогресс-счётчик,
который нужно было бы вручную двигать при каждом действии.

Прогресс считается "на лету" при каждом чтении (get_daily_quests) — в
БД (таблица daily_quests, см. db/core.py) хранится только факт "этот
квест на этот день уже забран" (claimed), сами квесты не хранятся как
состояние, только как история наград.
"""
import hashlib
import random

from .core import connect

QUEST_DEFINITIONS = {
    "two_habits": {"emoji": "✌️", "title": "Выполни 2 привычки сегодня", "target": 2, "reward": 5},
    "before_noon": {"emoji": "🌅", "title": "Отметь привычку до полудня", "target": 1, "reward": 5},
    "all_done": {"emoji": "🎯", "title": "Закрой все привычки на сегодня", "target": 1, "reward": 8},
    "priority_habit": {"emoji": "⭐", "title": "Выполни важную привычку", "target": 1, "reward": 5},
    "two_categories": {"emoji": "🌈", "title": "Привычки из 2 разных категорий", "target": 1, "reward": 6},
    "no_skip": {"emoji": "💪", "title": "Ни одного пропуска сегодня", "target": 1, "reward": 6},
}
QUEST_KEYS = list(QUEST_DEFINITIONS.keys())
QUESTS_PER_DAY = 3


def _pick_quests_for_day(user_id, day):
    """Детерминированный выбор 3 из 6 квестов на конкретный день — тот же
    пользователь в тот же день всегда видит один и тот же набор при
    повторных запросах, без необходимости хранить выбор в БД."""
    seed = int(hashlib.md5(f"{user_id}:{day}".encode("utf-8")).hexdigest(), 16)
    keys = QUEST_KEYS[:]
    random.Random(seed).shuffle(keys)
    return keys[:QUESTS_PER_DAY]


def _quest_progress(user_id, day, habits, key):
    completed_habits = [h for h in habits if h["completed"]]
    if key == "two_habits":
        return min(len(completed_habits), 2)
    if key == "all_done":
        return 1 if (habits and len(completed_habits) == len(habits)) else 0
    if key == "priority_habit":
        has = any(h["completed"] and ("priority" in h.keys() and h["priority"] == 2) for h in habits)
        return 1 if has else 0
    if key == "two_categories":
        cats = {h["category"] for h in completed_habits if "category" in h.keys() and h["category"]}
        return 1 if len(cats) >= 2 else 0
    if key == "no_skip":
        has_skipped = any("skip_reason" in h.keys() and h["skip_reason"] for h in habits)
        return 1 if (habits and not has_skipped and len(completed_habits) == len(habits)) else 0
    if key == "before_noon":
        conn = connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT completed_at FROM habit_completion_events WHERE user_id=? AND date(completed_at)=?",
            (user_id, day),
        )
        rows = cursor.fetchall()
        conn.close()
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from .streak import get_timezone
        try:
            tz = ZoneInfo(get_timezone(user_id))
        except Exception:
            tz = timezone.utc
        for row in rows:
            try:
                dt = datetime.fromisoformat(str(row["completed_at"])).replace(tzinfo=timezone.utc).astimezone(tz)
            except ValueError:
                continue
            if dt.hour < 12:
                return 1
        return 0
    return 0


def get_daily_quests(user_id):
    """Список из QUESTS_PER_DAY квестов на сегодня с прогрессом/статусом —
    используется и для отображения, и для проверки claim_daily_quest."""
    from .streak import local_today
    day = str(local_today(user_id))
    keys = _pick_quests_for_day(user_id, day)

    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT completed, priority, category, skip_reason FROM habits WHERE user_id=?", (user_id,))
    habits = cursor.fetchall()
    cursor.execute(
        "SELECT quest_key FROM daily_quests WHERE user_id=? AND day=? AND claimed=1", (user_id, day)
    )
    claimed_keys = {row["quest_key"] for row in cursor.fetchall()}
    conn.close()

    result = []
    for key in keys:
        definition = QUEST_DEFINITIONS[key]
        progress = _quest_progress(user_id, day, habits, key)
        result.append({
            "key": key,
            "emoji": definition["emoji"],
            "title": definition["title"],
            "target": definition["target"],
            "progress": progress,
            "reward": definition["reward"],
            "completed": progress >= definition["target"],
            "claimed": key in claimed_keys,
        })
    return result


def claim_daily_quest(user_id, quest_key):
    """Забирает награду за выполненный квест. Возвращает количество монет
    или None, если квест не найден/не выполнен/уже забран."""
    from .streak import local_today
    from .users import add_xp

    if quest_key not in QUEST_DEFINITIONS:
        return None
    day = str(local_today(user_id))
    quests = get_daily_quests(user_id)
    quest = next((q for q in quests if q["key"] == quest_key), None)
    if not quest or not quest["completed"] or quest["claimed"]:
        return None

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM daily_quests WHERE user_id=? AND day=? AND quest_key=?",
        (user_id, day, quest_key),
    )
    if cursor.fetchone():
        conn.close()
        return None
    reward = quest["reward"]
    cursor.execute(
        "INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) "
        "VALUES (?,?,?,?,?,?,?,1)",
        (user_id, day, quest_key, quest["title"], quest["target"], quest["progress"], reward),
    )
    conn.commit()
    conn.close()
    add_xp(user_id, reward)
    return reward
