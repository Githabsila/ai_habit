"""
Квесты дня (п.14 владельца, 21.10): три постоянных квеста, которые связывают три возможности приложения — привычки, план дня и Адама.
Человек пришёл отметить привычку, видит следующий понятный шаг и постепенно осваивает остальное:

  • two_habits — «Выполни 2 привычки»: две разные привычки, выполненные сегодня (создание привычки выполнением не считается);
    прогресс 0/2 → 1/2 → 2/2. Если привычка у человека одна, квест всё равно остаётся на две — он видит, что можно добавить вторую.
  • plan_day — «Заполни план дня»: на сегодня сохранены главная задача и хотя бы одна второстепенная (две части, 0/2 → 2/2). Выполнять сами
    задачи не нужно — поощряем именно планирование; больше двух задач баллов не добавляют.
  • talk_adam — «Поговори сегодня с Адамом»: человек отправил Адаму хотя бы одно сообщение за сегодняшний (местный) день — в приложении или в
    боте. Просто открыть чат мало. Сообщения, которые Адам написал первым, не считаются.

Прогресс считается «на лету» по реальному состоянию дня, поэтому, если человек уже поговорил с Адамом утром, квест отмечен сразу — порядок
шагов не важен. Награда выдаётся САМА в момент выполнения (раньше была кнопка «Забрать»): +reward Adam Coin и +1 очко в «Задания месяца»
(db/month_quests.py считает забранные записи daily_quests). В базе лежит только факт «этот квест на этот день выполнен» (claimed), и он
«липкий»: удалили привычку или задачу после выполнения — квест остаётся выполненным. Прежние квесты (before_noon, all_done, priority_habit,
two_categories, no_skip) из выдачи убраны; их выполненные записи по-прежнему идут в очки месяца.

Каждый вызов get_daily_quests сам выдаёт награды за только что выполненные квесты и помечает их just_completed — ответ, в котором это
пришло, и есть момент для окна «Квесты дня» (приложение сравнивает с прошлым состоянием).
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect

QUEST_DEFINITIONS = {
    "two_habits": {"emoji": "✌️", "title": "Выполни 2 привычки", "target": 2, "reward": 5, "chest": "wood"},
    "plan_day": {"emoji": "📝", "title": "Заполни план дня", "target": 2, "reward": 5, "chest": "blue"},
    "talk_adam": {"emoji": "💬", "title": "Поговори сегодня с Адамом", "target": 1, "reward": 5, "chest": "gold"},
}
QUEST_KEYS = list(QUEST_DEFINITIONS.keys())
QUESTS_PER_DAY = len(QUEST_KEYS)

# Квесты прошлой схемы (по три случайных из шести): в выдаче их больше нет, а забранные записи продолжают считаться в очках месяца.
LEGACY_QUEST_KEYS = ("before_noon", "all_done", "priority_habit", "two_categories", "no_skip")

PLAN_PARTS = ("Главная задача", "Второстепенная")


def _habits_progress(habits):
    return min(sum(1 for h in habits if h["completed"]), QUEST_DEFINITIONS["two_habits"]["target"])


def _plan_state(user_id):
    """(id плана, [есть главная, есть второстепенная]) — тот же «план дня», что человек видит на Главной."""
    from .daily_plan import get_daily_plan

    plan = get_daily_plan(user_id)
    return plan["id"], [bool((plan["main_goal"] or "").strip()), bool(plan["tasks"])]


def _wrote_to_adam_today(user_id, today):
    """Было ли у человека сегодня (по его местному дню) хотя бы одно сообщение Адаму: ai_messages, роль user."""
    from .streak import get_timezone

    try:
        tz = ZoneInfo(get_timezone(user_id))
    except Exception:
        tz = timezone.utc
    start = datetime.combine(today, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(today, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(timezone.utc)
    fmt = "%Y-%m-%d %H:%M:%S"
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM ai_messages WHERE user_id=? AND role='user' AND created_at >= ? AND created_at < ? LIMIT 1",
            (user_id, start.strftime(fmt), end.strftime(fmt)),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def _award(user_id, day, key, definition, progress, ref=None):
    """Записать выполненный квест и выдать награду. True — выдано именно сейчас (запись создана этим вызовом)."""
    from .users import add_xp

    conn = connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed, ref) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (user_id, day, key, definition["title"], definition["target"], progress, definition["reward"], ref),
        )
        created = cursor.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    if created:
        add_xp(user_id, definition["reward"])
        try:
            from .tracking import track_server
            track_server(user_id, "quest_done", step=key, meta={"coins": definition["reward"]})
        except Exception:
            pass
    return created


def get_daily_quests(user_id):
    """Три квеста на сегодня с прогрессом. Выполненные, но ещё не записанные квесты записываются здесь же (награда выдаётся сама), и у
    них стоит just_completed. completed — «липкий»: однажды выполненный квест остаётся выполненным до конца дня."""
    from .streak import local_today

    today = local_today(user_id)
    day = str(today)

    conn = connect()
    try:
        habits = conn.execute("SELECT completed FROM habits WHERE user_id=?", (user_id,)).fetchall()
        rows = conn.execute(
            f"SELECT quest_key, day, ref, chest_opened FROM daily_quests WHERE user_id=? AND claimed=1 AND quest_key IN ({','.join('?' for _ in QUEST_KEYS)})",
            (user_id, *QUEST_KEYS),
        ).fetchall()
    finally:
        conn.close()
    claimed_today = {r["quest_key"] for r in rows if r["day"] == day}
    chest_opened_today = {r["quest_key"] for r in rows if r["day"] == day and r["chest_opened"]}
    # План, на который квест уже засчитан в другой день, повторно не считается: между местной полуночью и сменой дня плана (db/daily_plan.py)
    # на Главной ещё вчерашний план — «заполнен» он не за сегодня.
    used_plan_refs = {r["ref"] for r in rows if r["quest_key"] == "plan_day" and r["day"] != day and r["ref"]}

    result = []
    for key in QUEST_KEYS:
        definition = QUEST_DEFINITIONS[key]
        target = definition["target"]
        parts = None
        ref = None
        if key == "two_habits":
            progress = _habits_progress(habits)
        elif key == "plan_day":
            plan_id, done = _plan_state(user_id)
            ref = str(plan_id)
            parts = [{"label": label, "done": bool(flag and ref not in used_plan_refs)} for label, flag in zip(PLAN_PARTS, done)]
            progress = sum(1 for part in parts if part["done"])
        else:
            progress = 1 if _wrote_to_adam_today(user_id, today) else 0
        claimed = key in claimed_today
        just_completed = False
        if not claimed and progress >= target:
            just_completed = _award(user_id, day, key, definition, progress, ref)
            claimed = True
        if claimed:
            progress = target
            if parts is not None:
                parts = [{"label": part["label"], "done": True} for part in parts]
        item = {
            "key": key,
            "emoji": definition["emoji"],
            "title": definition["title"],
            "target": target,
            "progress": progress,
            "reward": definition["reward"],
            "completed": claimed or progress >= target,
            "claimed": claimed,
            "just_completed": just_completed,
            "chest": definition["chest"],
            "chest_opened": claimed and key in chest_opened_today,
        }
        if parts is not None:
            item["parts"] = parts
        result.append(item)
    return result


def open_quest_chest(user_id, quest_key):
    """Сундук квеста: человек трижды тапнул по нему в окне. Награда (+reward Adam Coin и +1 очко месяца) выдана уже в момент выполнения — так
    выполненный квест ничего не теряет, даже если приложение закрыли, — поэтому сундук её только показывает и запоминает, что он открыт.
    Возвращает {"reward": {...}, "first": bool} или None, если квест сегодня не выполнен. Повторное открытие безвредно: ничего не начисляется."""
    from .streak import local_today

    definition = QUEST_DEFINITIONS.get(quest_key)
    if definition is None:
        return None
    quest = next((q for q in get_daily_quests(user_id) if q["key"] == quest_key), None)
    if not quest or not quest["completed"]:
        return None
    conn = connect()
    try:
        cursor = conn.execute(
            "UPDATE daily_quests SET chest_opened=1 WHERE user_id=? AND day=? AND quest_key=? AND claimed=1 AND COALESCE(chest_opened,0)=0",
            (user_id, str(local_today(user_id)), quest_key),
        )
        first = cursor.rowcount == 1
        conn.commit()
    finally:
        conn.close()
    if first:
        try:
            from .tracking import track_server
            track_server(user_id, "chest_opened", step=f"quest_{quest_key}", meta={"coins": definition["reward"]})
        except Exception:
            pass
    return {"reward": {"coins": definition["reward"], "month_points": 1}, "first": first}


def claim_daily_quest(user_id, quest_key):
    """Совместимость со старым приложением (кнопка «Забрать»): награды теперь выдаются сами, поэтому забирать нечего — всегда None."""
    if quest_key not in QUEST_DEFINITIONS:
        return None
    get_daily_quests(user_id)
    return None
