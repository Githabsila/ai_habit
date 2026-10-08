"""
Адам пишет первым: проактивные сообщения наставника (состояние человека → сценарий → текст).

Что тут есть (всё без вызова модели — её зовёт adam_checkin.py в корне):
  • build_checkin_state — «User_State»: имя, время, ПРИВЫЧКИ И ЗАДАЧИ на сегодня (вместе: «выполнено X из Y»),
    названия оставшихся, серия, пометка о настроении, обычный темп человека;
  • расписание: окна «день» 12:30–16:30 и «вечер» 18:30–21:30 (утром и после 22:00 Адам сам не пишет), момент внутри окна —
    рядом с привычным временем САМОГО человека ±20–40 минут (activity_profile: заходы, отметки, чат), иначе случайный;
    «чатовые» дни — 2–3 в неделю не подряд, лимиты 1 сообщение в день и 5 в неделю;
  • решение «писать или промолчать» (ТЗ 222.md): EVENT → состояние → допуск → повод (intent) → приоритет → cooldown → текст.
    list_candidates собирает ВСЕ уместные поводы с весом, choose_scenario берёт самый важный, которого не было в последних двух
    сообщениях; если уместного нет — тишина (это нормально и записывается в журнал с причиной);
  • привычки — ядро (вес выше), задачи — слой дня: главная задача становится поводом, только если с привычками всё в порядке;
    «отдых/выходной» в главной задаче — полноценный план, его не оценивают; задач нет — про них вообще не говорим;
  • история дней в состоянии: вчера, серия и рекорд, привычка, которая «не получается» (get_struggling_habits);
  • build_checkin_prompt — блок данных для модели + эталонный пример тона (REFERENCE_EXAMPLES); fallback_text — запасные
    тексты без «Как дела?»;
  • clean_text / is_acceptable — защита от шаблонных и слишком длинных ответов модели;
  • журнал adam_checkins — «окно уже обработано», чтобы пуш не дублировался и не пересчитывался каждый тик.

Утром (до 12:00) Адам вопросов не задаёт: только реагирует на действия человека и спокойно называет, что дальше.
Раскатка — флаг adam_checkin в «Feature flags» админки; у админов включено всегда.
"""
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect

FLAG_KEY = "adam_checkin"

# Окна пушей в локальном времени человека, минуты от полуночи. Утром (08:00–11:00) человек планирует день — Адам сам не пишет
# (только отвечает в чате при заходе); после 22:00 не пишет тоже. Момент внутри окна — у привычного времени самого человека.
SLOT_WINDOWS = {"day": (12 * 60 + 30, 16 * 60 + 30), "evening": (18 * 60 + 30, 21 * 60 + 30)}
DELIVERY_MARGIN_MIN = 10             # момент отправки не ближе 10 минут к концу окна
JITTER_MINUTES = (20, 40)            # разброс вокруг привычного времени: не «ровно в 14:00 каждый день»
MAX_PUSH_PER_WEEK = 5                # пушей Адама за скользящие 7 дней (в день — максимум один)
CHAT_DAYS_PER_WEEK = (2, 3)          # «просто поболтать» (рефлексия, разговор) — 2–3 дня в неделю
MIN_ACCOUNT_AGE_HOURS = 20           # в день регистрации работают тур и «Привет! Я Adam…» — Адам молчит
ACTIVE_NOW_MINUTES = 10              # человек прямо сейчас в приложении — пуш не нужен (там сработает подсказка)
INTENT_COOLDOWN = 2                  # повод из двух последних сообщений не повторяем — лучше промолчать
CHECK_IN_EVERY_DAYS = 14             # «просто контакт» без повода — редко
DORMANT_DAYS = 7                     # неделю без отметок и без чата — этим занимается возвращение серии, а не Адам
PACE_HISTORY_DAYS = 14
PACE_MIN_DAYS = 3                    # меньше дней истории — темп не оцениваем
PROFILE_MIN_DAYS = 3                 # сколько дней истории нужно, чтобы говорить «обычно»
HABIT_TYPICAL_MIN_DAYS = 4           # привычка выполнялась ≥4 дней из 14 — по ней можно сказать «обычно к этому времени»
MISSED_LATE_MIN = 90                 # привычка «опаздывает» от своего обычного времени (или времени напоминания) на столько минут
TASK_MIN_AGE_MIN = 120               # главная задача «стоит», если план лежит без отметки хотя бы столько
DAY_END_HOUR = 22
TEXT_MAX_CHARS = 320

SCENARIOS = (
    "zero_done", "zero_done_evening", "behind", "one_left", "evening_left", "all_done", "free_chat", "morning", "struggling",
    "missed_habit", "task_progress", "task_remaining", "positive", "check_in",
)

# Повод (intent) из ТЗ для каждого сценария — так он называется в журнале, админке и в контексте разговора.
INTENT_LABELS = {
    "zero_done": "ANOMALY", "behind": "ANOMALY", "zero_done_evening": "STREAK_AT_RISK", "one_left": "LAST_HABIT",
    "evening_left": "PROGRESS", "all_done": "REFLECTION", "free_chat": "CONVERSATION", "morning": "PLAN",
    "struggling": "MISSED_HABIT", "missed_habit": "MISSED_HABIT", "task_progress": "TASK_PROGRESS",
    "task_remaining": "TASK_REMAINING", "positive": "POSITIVE", "check_in": "CHECK_IN",
}

# Шаблонные фразы, которых Адам писать не должен (проверка по тексту без ё и в нижнем регистре).
BANNED_PHRASES = (
    "как дела", "как твои дела", "как ваши дела", "как ты?", "как настроение", "как прошел день",
    "как прошел твой день", "как проходит день", "как жизнь", "как поживаешь",
)
# ТЗ §18: так не говорим никогда — обвинение вместо наблюдения («Ты опять ничего не сделал», «Ты потерял полдня», «Почему ты ленишься?»).
TOXIC_PHRASES = ("опять ничего не сделал", "потерял полдня", "потеряла полдня", "почему ты лениш", "ленишься", "лентяй", "бездельнич", "ты ничего не делаешь")
# Редкий «просто контакт» (CHECK_IN) — единственное место, где допустимо «как проходит день».
CHECK_IN_ALLOWED = ("как проходит день",)

# Главная задача про отдых/личное — полноценный план: Адам не воспринимает её как «низкую продуктивность» и не торопит.
REST_WORDS = (
    "отдых", "отдохн", "выходн", "погуля", "прогул", "расслаб", "восстанов", "выспа", "поспа", "релакс", "отпуск",
    "семьей", "семьёй", "с семь", "к друг", "с друг", "кино",
)


def rest_like(title):
    low = str(title or "").lower().replace("ё", "е")
    return any(word.replace("ё", "е") in low for word in REST_WORDS)


# ------------------------------------------------------------------ время и расписание

def part_of_day(hour):
    if hour < 5:
        return "ночь"
    if hour < 12:
        return "утро"
    if hour < 18:
        return "день"
    return "вечер"


def _parse_utc(value):
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def planned_minute(user_id, day_iso, slot, preferred=None):
    """Во сколько (минут от полуночи) Адам напишет в этом окне. Для (человек, день, окно) всегда одно и то же — тик планировщика
    не перебрасывает монетку заново. preferred — привычное время самого человека в этом окне (activity_profile): момент рядом с ним
    ±20–40 минут, в пределах окна; нет данных — случайный внутри окна («не ровно в 14:00 для всех»)."""
    start, end = SLOT_WINDOWS[slot]
    last = end - DELIVERY_MARGIN_MIN
    rng = random.Random(f"adam-slot:{user_id}:{day_iso}:{slot}")
    if preferred is None:
        return rng.randrange(start, last)
    shift = rng.randint(*JITTER_MINUTES) * rng.choice((-1, 1))
    return max(start, min(last - 1, int(preferred) + shift))


def window_slot(now_local):
    """В каком окне сейчас местное время (без учёта запланированного момента). Дёшево: тик планировщика сначала спрашивает это."""
    minute = now_local.hour * 60 + now_local.minute
    for slot, (start, end) in SLOT_WINDOWS.items():
        if start <= minute < end:
            return slot
    return None


def slot_due(user_id, now_local, preferred=None):
    """Окно, в котором сейчас пора писать: уже наступил запланированный момент, а окно ещё не закончилось. Иначе None.
    preferred — {"day": минута|None, "evening": минута|None} из activity_profile."""
    slot = window_slot(now_local)
    if slot is None:
        return None
    minute = now_local.hour * 60 + now_local.minute
    wanted = (preferred or {}).get(slot)
    return slot if minute >= planned_minute(user_id, now_local.date().isoformat(), slot, wanted) else None


def chat_days(user_id, day):
    """Дни недели (Пн=0 … Вс=6), когда Адам может написать «просто поболтать»: 2–3 в неделю и не два дня подряд.
    Набор свой у каждого человека и каждой недели, внутри недели стабилен."""
    iso = day.isocalendar()
    rng = random.Random(f"adam-chat-days:{user_id}:{iso[0]}-{iso[1]}")
    count = rng.choice(CHAT_DAYS_PER_WEEK)
    for _ in range(60):
        picked = sorted(rng.sample(range(7), count))
        if all(b - a >= 2 for a, b in zip(picked, picked[1:])):
            return set(picked)
    return {1, 4}


def is_chat_day(user_id, day):
    return day.weekday() in chat_days(user_id, day)


# ------------------------------------------------------------------ состояние человека

def _short(text, limit=60):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def pace_history(user_id, now_local):
    """Обычный темп человека по журналу отметок за последние 14 дней: сколько разных привычек он закрывает ДО этого времени суток
    и за весь день. {"days": сколько дней с отметками, "avg_by_now": …, "avg_total": …}."""
    tz = now_local.tzinfo
    since = (datetime.now(timezone.utc) - timedelta(days=PACE_HISTORY_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT habit_id, completed_at FROM habit_completion_events WHERE user_id=? AND completed_at>=?",
            (user_id, since),
        ).fetchall()
    finally:
        conn.close()
    today = now_local.date()
    now_minute = now_local.hour * 60 + now_local.minute
    per_day = {}
    for row in rows:
        moment = _parse_utc(row["completed_at"])
        if moment is None:
            continue
        local = moment.astimezone(tz)
        if local.date() >= today:
            continue
        minute = local.hour * 60 + local.minute
        habits = per_day.setdefault(local.date(), {})
        habits[row["habit_id"]] = min(minute, habits.get(row["habit_id"], 24 * 60))
    if not per_day:
        return {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0, "yesterday": 0, "active_7": 0}
    by_now = [sum(1 for minute in habits.values() if minute <= now_minute) for habits in per_day.values()]
    totals = [len(habits) for habits in per_day.values()]
    return {
        "days": len(per_day), "avg_by_now": sum(by_now) / len(by_now), "avg_total": sum(totals) / len(totals),
        # из истории дней: сколько привычек закрыто вчера и в скольких из последних 7 дней были отметки
        "yesterday": len(per_day.get(today - timedelta(days=1), {})),
        "active_7": sum(1 for day in per_day if (today - day).days <= 7),
    }


def fill_counts(state):
    """Выводит счётчики и флаги из списков привычек и задач (так состояние собирают и боевой код, и демо-сценарии админки)."""
    habits, tasks = state["habits"], state["tasks"]
    state["habits_total"], state["habits_done"] = len(habits), sum(1 for h in habits if h["done"])
    state["tasks_total"], state["tasks_done"] = len(tasks), sum(1 for t in tasks if t["done"])
    # Открытая задача про отдых/личное («Отдохнуть», «Съездить к другу») — не «работа, которая осталась»: в «осталось» и в
    # приоритеты она не входит (ТЗ §3, §14: это полноценный план, а не повод торопить).
    counted = [t for t in tasks if t["done"] or not rest_like(t["title"])]
    state["total"] = state["habits_total"] + len(counted)
    state["done"] = state["habits_done"] + state["tasks_done"]
    state["left"] = state["total"] - state["done"]
    state["pending_habits"] = [h["title"] for h in habits if not h["done"]]
    state["pending_tasks"] = [t["title"] for t in counted if not t["done"]]
    # Что назвать первым: главная задача дня, затем остальные задачи, затем привычки.
    main_first = sorted((t for t in counted if not t["done"]), key=lambda t: not t.get("main"))
    state["pending"] = [t["title"] for t in main_first] + state["pending_habits"]
    main = next((t for t in tasks if t.get("main")), None)
    state["main_title"] = main["title"] if main else ""
    state["main_open"] = bool(main and not main["done"])
    state["main_is_rest"] = bool(main and rest_like(main["title"]))
    pace = state.get("pace") or {}
    known = pace.get("days", 0) >= PACE_MIN_DAYS
    # Отклонения от СВОЕГО обычного: впереди темпа / день лучше обычного (положительные повод так же важны, как отстающие).
    state["ahead"] = bool(known and state["habits_done"] >= 2 and state["habits_done"] >= pace.get("avg_by_now", 0) + 1 and state["left"] > 0)
    state["above_usual"] = bool(known and state["habits_total"] and state["habits_done"] > pace.get("avg_total", 0) + 0.5)
    return state


def _best_streak(user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT best_streak FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    return int(row["best_streak"] or 0) if row else 0


def _struggling_pending(user_id, habits):
    """Привычка, которая «не получается» (≥4 пропусков из последних 5 дней) и ещё не выполнена сегодня: {"title", "missed"} или None.
    Тот же источник, что у подсказки «привычка не получается» в приложении (db/coaching_insights.py)."""
    from .coaching_insights import get_struggling_habits
    pending_titles = {h["title"] for h in habits if not h["done"]}
    for item in get_struggling_habits(user_id):
        title = _short(item["title"])
        if title in pending_titles:
            return {"title": title, "missed": int(item["missed"])}
    return None


def _struggling_done(user_id, habits):
    """Привычка, которая обычно ускользает, но СЕГОДНЯ выполнена — повод отметить (ТЗ: «сегодня ты сделал ту, что чаще пропускаешь»)."""
    from .coaching_insights import get_struggling_habits
    done_titles = {h["title"] for h in habits if h["done"]}
    for item in get_struggling_habits(user_id):
        title = _short(item["title"])
        if title in done_titles:
            return title
    return None


def _parse_hhmm(value):
    try:
        hour, minute = (int(x) for x in str(value).split(":")[:2])
        return hour * 60 + minute if 0 <= hour < 24 and 0 <= minute < 60 else None
    except (TypeError, ValueError):
        return None


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


EMPTY_PROFILE = {"usual": {"day": None, "evening": None}, "first_habit": None, "habit_times": {}}


def activity_profile(user_id, now_local):
    """Привычный ритм САМОГО человека за 14 дней (ТЗ §8, §11): когда он обычно заходит/отмечает/пишет в окнах дня и вечера, во
    сколько делает первую привычку и во сколько обычно закрывает каждую. Не «среднее по людям» — только он сам.
    {"usual": {"day": минута|None, "evening": …}, "first_habit": минута|None, "habit_times": {habit_id: {"minute", "days"}}}.
    Нужно ≥3 дней истории на окно (и ≥4 для привычки) — иначе None: «обычно» говорить рано."""
    tz = now_local.tzinfo
    today = now_local.date()
    since = (datetime.now(timezone.utc) - timedelta(days=PACE_HISTORY_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        opens = conn.execute("SELECT at FROM app_opens WHERE user_id=? AND at>=?", (user_id, since)).fetchall()
        chats = conn.execute(
            "SELECT created_at FROM ai_messages WHERE user_id=? AND role='user' AND created_at>=?", (user_id, since)
        ).fetchall()
        events = conn.execute(
            "SELECT habit_id, completed_at FROM habit_completion_events WHERE user_id=? AND completed_at>=?", (user_id, since)
        ).fetchall()
    finally:
        conn.close()

    window_days = {slot: {} for slot in SLOT_WINDOWS}      # окно → {день: самая ранняя минута активности в окне}
    first_habit, per_habit = {}, {}

    def local_past(value):
        moment = _parse_utc(value)
        if moment is None:
            return None
        local = moment.astimezone(tz)
        return None if local.date() >= today else local

    def see(local):
        minute = local.hour * 60 + local.minute
        for slot, (lo, hi) in SLOT_WINDOWS.items():
            if lo <= minute < hi:
                window_days[slot][local.date()] = min(minute, window_days[slot].get(local.date(), 24 * 60))
        return minute

    for row in opens:
        local = local_past(row["at"])
        if local:
            see(local)
    for row in chats:
        local = local_past(row["created_at"])
        if local:
            see(local)
    for row in events:
        local = local_past(row["completed_at"])
        if not local:
            continue
        minute = see(local)
        first_habit[local.date()] = min(minute, first_habit.get(local.date(), 24 * 60))
        days = per_habit.setdefault(row["habit_id"], {})
        days[local.date()] = min(minute, days.get(local.date(), 24 * 60))

    return {
        "usual": {slot: (int(_median(list(days.values()))) if len(days) >= PROFILE_MIN_DAYS else None) for slot, days in window_days.items()},
        "first_habit": int(_median(list(first_habit.values()))) if len(first_habit) >= PROFILE_MIN_DAYS else None,
        "habit_times": {
            hid: {"minute": int(_median(list(days.values()))), "days": len(days)}
            for hid, days in per_habit.items() if len(days) >= HABIT_TYPICAL_MIN_DAYS
        },
    }


_PREFERRED_CACHE = {}


def preferred_minutes(user_id, now_local):
    """Привычное время человека по окнам на сегодня ({"day": минута|None, "evening": …}); считается раз в день, не на каждый тик."""
    key = (user_id, now_local.date().isoformat())
    if key not in _PREFERRED_CACHE:
        if len(_PREFERRED_CACHE) > 20000:
            _PREFERRED_CACHE.clear()
        _PREFERRED_CACHE[key] = activity_profile(user_id, now_local)["usual"]
    return _PREFERRED_CACHE[key]


def _missed_habit(habits, profile, now_minute):
    """Привычка, которую человек ОБЫЧНО уже закрывает к этому времени (по своим последним дням) или чьё время напоминания прошло,
    а сегодня её нет: {"title", "usual_minute", "late_min", "kind": typical|planned, "days"} или None."""
    if now_minute >= DAY_END_HOUR * 60:
        return None
    best = None
    for habit in habits:
        if habit["done"]:
            continue
        options = []
        typical = (profile.get("habit_times") or {}).get(habit.get("id"))
        if typical:
            options.append(("typical", typical["minute"], typical["days"]))
        planned = _parse_hhmm(habit.get("planned"))
        if planned is not None:
            options.append(("planned", planned, 99))
        for kind, minute, days in options:
            late = now_minute - minute
            if late >= MISSED_LATE_MIN:
                item = {"title": habit["title"], "usual_minute": int(minute), "late_min": int(late), "kind": kind, "days": days}
                if best is None or (item["days"], item["late_min"]) > (best["days"], best["late_min"]):
                    best = item
    return best


def _task_history(user_id, today_iso):
    """Как человек обращается с задачами за последние 7 дней: сколько дней была главная и сколько раз закрыта, сколько мелких задач и сколько закрыто."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT p.main_goal AS main_goal, p.main_goal_completed AS main_done,
                      (SELECT COUNT(*) FROM daily_plan_tasks t WHERE t.plan_id=p.id AND TRIM(t.text)!='') AS n,
                      (SELECT COALESCE(SUM(t.completed),0) FROM daily_plan_tasks t WHERE t.plan_id=p.id) AS d
               FROM daily_plans p WHERE p.user_id=? AND p.plan_date<? AND p.plan_date>=date(?, '-7 days')""",
            (user_id, today_iso, today_iso),
        ).fetchall()
    finally:
        conn.close()
    with_main = [r for r in rows if (r["main_goal"] or "").strip()]
    return {
        "days_with_main": len(with_main), "main_done_days": sum(1 for r in with_main if r["main_done"]),
        "tasks_set": sum(int(r["n"] or 0) for r in rows), "tasks_done": sum(int(r["d"] or 0) for r in rows),
    }


def _main_age_minutes(user_id, today_iso, has_main):
    """Сколько минут план дня лежит без отметки главной задачи (отметка времени главной задачи отдельно не хранится — берём
    время создания плана на сегодня). None — главной задачи нет или время неизвестно."""
    if not has_main:
        return None
    conn = connect()
    try:
        row = conn.execute("SELECT created_at FROM daily_plans WHERE user_id=? AND plan_date=?", (user_id, today_iso)).fetchone()
    finally:
        conn.close()
    created = _parse_utc(row["created_at"]) if row else None
    return int((datetime.now(timezone.utc) - created).total_seconds() // 60) if created else None


def build_checkin_state(user_id, now_local=None, with_pace=True):
    """User_State для выбора повода и для модели. Привычки и задачи на сегодня считаются ВМЕСТЕ; плюс история человека (темп,
    привычное время привычек, как он обращается с задачами), сколько назад заходил и какие поводы уже были."""
    from db import get_ai_style, get_daily_plan, get_gender, get_habits, get_incomplete_habits, get_language, get_progress
    from db import get_proactive_topic, get_user
    from .daily_plan import _effective_plan_date
    from .streak import get_timezone

    now_local = now_local or datetime.now(ZoneInfo(get_timezone(user_id)))
    now_minute = now_local.hour * 60 + now_local.minute
    user = get_user(user_id)
    pending_ids = {h["id"] for h in get_incomplete_habits(user_id)}
    habits = []
    for habit in get_habits(user_id):
        planned = habit["planned_time"] if "planned_time" in habit.keys() else None
        if habit["completed"]:
            habits.append({"id": habit["id"], "title": _short(habit["title"]), "done": True, "planned": planned})
        elif habit["id"] in pending_ids:      # сознательно пропущенные и «недельная норма уже выполнена» не считаем
            habits.append({"id": habit["id"], "title": _short(habit["title"]), "done": False, "planned": planned})
    tasks = []
    plan = get_daily_plan(user_id)
    main_goal = (plan.get("main_goal") or "").strip() if plan else ""
    if main_goal:
        tasks.append({"title": _short(main_goal), "done": bool(plan.get("main_goal_completed")), "main": True})
    for task in (plan["tasks"] if plan else []):
        if (task["text"] or "").strip():
            tasks.append({"title": _short(task["text"]), "done": bool(task["completed"]), "main": False})

    minutes_since_chat = chat_minutes_ago(user_id)
    progress = get_progress(user_id) or {}
    profile = activity_profile(user_id, now_local) if with_pace else EMPTY_PROFILE
    plan_date = _effective_plan_date()
    state = {
        "user_id": user_id,
        "first_name": _short((user["first_name"] if user else "") or "", 40),
        "gender": get_gender(user_id),
        "hour": now_local.hour,
        "minute": now_local.minute,
        "time": now_local.strftime("%H:%M"),
        "part": part_of_day(now_local.hour),
        "weekday": now_local.weekday(),
        "hours_left": round(max(0, DAY_END_HOUR * 60 - now_minute) / 60, 1),
        "habits": habits,
        "tasks": tasks,
        "main_goal": main_goal,
        "streak": int(progress.get("streak") or 0),
        "best_streak": _best_streak(user_id),
        "struggling": _struggling_pending(user_id, habits),
        "struggling_done": _struggling_done(user_id, habits),
        "missed": _missed_habit(habits, profile, now_minute) if with_pace else None,
        "usual": profile["usual"],
        "first_habit_minute": profile["first_habit"],
        "task_hist": _task_history(user_id, plan_date) if with_pace else {"days_with_main": 0, "main_done_days": 0, "tasks_set": 0, "tasks_done": 0},
        "main_age_min": _main_age_minutes(user_id, plan_date, bool(main_goal)),
        "mood_hint": _short(get_proactive_topic(user_id), 160),
        "minutes_since_chat": minutes_since_chat,
        "seen_minutes_ago": seen_minutes_ago(user_id),
        "recent_scenarios": recent_scenarios(user_id, 6),
        "last_check_in_days": days_since_scenario(user_id, "check_in", now_local.date()),
        "language": get_language(user_id),
        "style": get_ai_style(user_id),
        "pace": pace_history(user_id, now_local) if with_pace else {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0, "yesterday": 0, "active_7": 0},
    }
    return fill_counts(state)


# ------------------------------------------------------------------ какой сценарий нужен

def _silence_reason(state, slot, chat_day):
    """Почему сейчас писать не о чем — одна строка в журнале окон и в админке («тишина — тоже часть поведения наставника»)."""
    total, left = state["total"], state["left"]
    pace = state.get("pace") or {}
    known = pace.get("days", 0) >= PACE_MIN_DAYS
    if slot == "day":
        if total == 0 and state.get("main_is_rest") and state.get("main_open"):
            return "главная задача дня — отдых: это нормальный план, торопить незачем"
        if total == 0:
            return "дел на сегодня нет"
        if left == 0:
            return "всё уже выполнено"
        if state["habits_done"] == 0 and state["habits_total"] and known and pace.get("avg_by_now", 0) < 0.5:
            return "обычно человек делает привычки позже"
        if state.get("main_is_rest") and state.get("main_open"):
            return "главная задача дня — отдых: это нормальный план, торопить незачем"
        return "темп в норме"
    if total == 0:
        return "нет дел, чатовый день" if chat_day else "нет дел, не чатовый день"
    if left == 0:
        return "всё выполнено, чатовый день" if chat_day else "всё выполнено, не чатовый день"
    if state.get("main_is_rest") and state.get("main_open"):
        return "главная задача дня — отдых: это нормальный план, торопить незачем"
    return "повода нет"


def list_candidates(state, slot, chat_day=False):
    """Всё, о чём сейчас МОЖНО написать, по убыванию важности (ТЗ §16: приоритет → повод): [{"scenario", "intent", "priority", "reason"}].

    Привычки — ядро (вес выше), задачи — контекст дня: главная задача становится поводом, только если с привычками всё в порядке
    (отстающие привычки перекрывают её); «отдых» в главной задаче поводом не бывает. Положительные отклонения — такие же поводы,
    как отстающие. «Просто поболтать» (рефлексия, разговор, редкий check-in) — только в чатовые дни."""
    total, done, left = state["total"], state["done"], state["left"]
    habits_total, habits_done = state["habits_total"], state["habits_done"]
    pace = state.get("pace") or {}
    known = pace.get("days", 0) >= PACE_MIN_DAYS
    main_open = bool(state.get("main_open")) and not state.get("main_is_rest")
    missed = state.get("missed")
    out = []

    def add(scenario, priority, reason):
        out.append({"scenario": scenario, "intent": INTENT_LABELS[scenario], "priority": priority, "reason": reason})

    only_main_left = left == 1 and main_open
    if slot == "day":
        if total == 0 or left == 0:
            return out
        if habits_done == 0 and habits_total:
            if not (known and pace.get("avg_by_now", 0) < 0.5):      # кто обычно делает привычки вечером — днём не дёргаем
                add("zero_done", 90, "до сих пор ничего не выполнено")
        elif left == 1 and habits_total and not only_main_left:
            add("one_left", 75, "остался один пункт")
        elif habits_total:
            behind = habits_done < 0.6 * pace["avg_by_now"] if known else done / total < 0.34
            if behind:
                add("behind", 60, "темп ниже обычного")
        if missed:
            add("missed_habit", 70, f"«{missed['title']}» обычно уже выполнена к этому времени")
        habit_lag = any(c["scenario"] in ("zero_done", "behind", "missed_habit") for c in out)
        age = state.get("main_age_min")
        if main_open and not habit_lag and (age is None or age >= TASK_MIN_AGE_MIN):
            add("task_progress", 78 if only_main_left else 65, "привычки в порядке, главная задача не выполнена")
        if state.get("struggling_done"):
            add("positive", 62, f"выполнена «{state['struggling_done']}» — привычка, которая обычно ускользает")
        elif state.get("ahead"):
            add("positive", 50, "впереди своего обычного темпа")
        last = state.get("last_check_in_days")
        if not out and chat_day and known and (last is None or last >= CHECK_IN_EVERY_DAYS):
            add("check_in", 20, "повода нет, чатовый день: редкий обычный контакт")
    elif slot == "evening":
        if total == 0:
            if chat_day:
                add("free_chat", 40, "нет дел, чатовый день")
        elif left == 0:
            if chat_day:
                add("all_done", 80, "всё выполнено, чатовый день")
        else:
            habits_left = len(state.get("pending_habits") or [])
            if habits_done == 0 and habits_total:
                add("zero_done_evening", 90, "вечер, ни одной привычки")
            else:
                if chat_day and state.get("struggling"):
                    add("struggling", 80, "привычка не получается, чатовый день")
                if main_open and habits_left == 0:
                    add("task_remaining", 77, "привычки закрыты, главная задача открыта")
                if left == 1:
                    add("one_left", 75, "остался один пункт")
                else:
                    add("evening_left", 60, "вечер, осталось несколько пунктов")
            if missed and not (habits_done == 0 and habits_total):
                sole = left == 1 and (state.get("pending") or [None])[0] == missed["title"]
                add("missed_habit", 82 if sole else 70, f"«{missed['title']}» обычно уже выполнена к этому времени")
            if state.get("struggling_done"):
                add("positive", 55, f"выполнена «{state['struggling_done']}» — привычка, которая обычно ускользает")
    out.sort(key=lambda c: -c["priority"])
    return out


def pick_scenario(state, slot, chat_day=False):
    """(сценарий | None, причина). slot: 'day' | 'evening' — плановые пуши; 'app' — первое сообщение при заходе в чат.

    День (12:30–16:30): пишем, только если есть повод относительно СВОЕГО обычного (ничего не выполнено, отстаёт, привычка
    опаздывает, главная задача стоит, остался один пункт, день лучше обычного). Вечер (18:30–21:30): итог дня. «Просто поболтать»
    — только в чатовые дни. Утром вопросов нет вообще. Нет повода — (None, причина): тишина."""
    if slot in ("day", "evening"):
        candidates = list_candidates(state, slot, chat_day)
        if candidates:
            return candidates[0]["scenario"], candidates[0]["reason"]
        return None, _silence_reason(state, slot, chat_day)
    # 'app': человек сам зашёл в чат — отвечаем на то, что происходит сейчас
    total, done, left = state["total"], state["done"], state["left"]
    if state["hour"] < 12:
        return "morning", "утро: без вопросов"
    if total == 0:
        return "free_chat", "нет дел"
    if left == 0:
        return "all_done", "всё выполнено"
    if done == 0:
        return ("zero_done_evening" if state["hour"] >= 18 else "zero_done"), "ничего не выполнено"
    if left == 1:
        return "one_left", "остался один пункт"
    return ("evening_left" if state["hour"] >= 18 else "behind"), "осталось несколько пунктов"


def choose_scenario(state, slot, chat_day=False, recent=()):
    """Решение для планового пуша: (сценарий | None, причина, кандидаты). recent — сценарии последних сообщений, новые первыми.
    Повод из двух последних сообщений не повторяем (ТЗ §23: не крутить одно и то же) — лучше промолчать; исключение — защита
    серии вечером при серии от 3 дней."""
    candidates = list_candidates(state, slot, chat_day)
    blocked = set(list(recent)[:INTENT_COOLDOWN])
    for item in candidates:
        urgent = item["scenario"] == "zero_done_evening" and int(state.get("streak") or 0) >= 3
        if item["scenario"] in blocked and not urgent:
            continue
        return item["scenario"], item["reason"], candidates
    if candidates:
        return None, "тот же повод уже был в последних сообщениях", candidates
    return None, _silence_reason(state, slot, chat_day), candidates


SCENARIO_GUIDE = {
    "zero_done": "Сегодня пока не выполнено НИ ОДНОЙ привычки. Удивись (без упрёка), мягко спроси, всё ли в порядке, и предложи начать с самого простого из оставшегося.",
    "zero_done_evening": "День подходит к концу, а ни одной привычки не выполнено. Без упрёков и давления: скажи, что день ещё не потерян. Если серия больше нуля — скажи, что её сохранит даже одна выполненная привычка. Предложи самое простое дело.",
    "behind": "Человек идёт ниже своего обычного темпа. Отметь, что уже сделано, назови одно-два оставшихся дела и подтолкни к одному маленькому шагу прямо сейчас.",
    "one_left": "Остался ровно ОДИН пункт — назови его по имени. Подбодри: это последний шаг на сегодня.",
    "evening_left": "Вечер, осталось несколько дел. Коротко подведи итог (сделано X из Y), предложи закрыть одно — самое лёгкое — и можешь спросить, что мешает.",
    "all_done": "Всё на сегодня выполнено. Похвали конкретно и задай ОДИН открытый вопрос для рефлексии: что было самым интересным сегодня, что получилось лучше всего или чем планирует заняться вечером.",
    "free_chat": "Дел на сегодня нет. Задай ОДИН нешаблонный открытый вопрос про цели, энергию или планы (не «как дела»).",
    "morning": "Сейчас утро: вопросов НЕ задавай. Коротко отреагируй на уже сделанное (если есть) и спокойно назови, что стоит в плане дальше.",
    "struggling": "Одна привычка не получается несколько дней подряд (название и число дней — в контексте). Без упрёка: скажи, что чаще дело в размере задачи, а не в лени, и задай ОДИН конкретный вопрос — сколько минут для человека точно реально или что мешает. Можно предложить уменьшить планку.",
    "missed_habit": "Привычка, которую человек обычно уже закрывает к этому времени (или чьё время напоминания прошло), сегодня не выполнена — название и обычное время в контексте. Без упрёка, спокойно и наблюдательно: заметь это и спроси, день просто пошёл иначе или что-то мешает.",
    "task_progress": "С привычками всё нормально, а главная задача дня пока не выполнена. Говори про ЗАДАЧУ (одну, по названию), привычки не перечисляй. Спроси, как идёт работа над ней, или что мешает начать; если она долго стоит — мягко предложи маленький первый шаг.",
    "task_remaining": "Вечер: привычки закрыты, а главная задача ещё открыта. Назови её и спроси, получится ли вернуться к ней сегодня или сколько времени на неё реально. Ничего не требуй, это не экзамен.",
    "positive": "Сегодня лучше обычного: человек впереди своего темпа или сделал привычку, которая чаще всего ускользает (это в контексте). Отметь это конкретно и спроси, что сработало, — чтобы он сам это заметил. Не хвали общими словами.",
    "check_in": "Обычный человеческий контакт без анализа и без повода. Коротко и тепло, один вопрос про сегодняшний день («что сегодня уже получилось?», «как сегодня идёт день?»); без цифр, списков и без «как дела».",
}

# Эталонные сообщения. Первые три — от владельца проекта (как должно звучать); остальные в том же тоне. В промпт идут как
# ориентир стиля («не копируй слова»), в лаборатории админки показываются рядом с тем, что написал Адам. {name} — имя из состояния.
REFERENCE_EXAMPLES = {
    "zero_done": "{name}, смотрю на твой дашборд — уже середина дня, а галочек пока нет. День выдался сумасшедшим или просто нет сил? Давай попробуем закрыть хотя бы самую лёгкую задачу на сегодня.",
    "one_left": "Вижу отличный прогресс! Две привычки уже в копилке. Осталось только «Чтение 15 минут». Найдёшь на это время до сна, или сегодня ставим на паузу?",
    "all_done": "Идеальный день, все привычки закрыты! Закинул тебе в статистику отличный результат. Раз уж мы всё успели, расскажи, что сегодня вообще было классного помимо рутины?",
    "behind": "Две из шести — для тебя это медленнее обычного. Что сегодня отъедает время? Возьми «Зарядку»: пять минут, и ритм вернётся.",
    "zero_done_evening": "Вечер, а список пока пустой — но день ещё твой. Серия в 12 дней держится даже от одной отметки: давай «Чтение», хотя бы на десять минут?",
    "evening_left": "Сегодня закрыто 2 из 5, остался вечер. Выбери одно дело — хоть «Английский» — и закрой его до сна. Что сейчас проще всего?",
    "free_chat": "На сегодня ничего не запланировано — редкая свобода. Над чем хочется поработать на этой неделе, без оглядки на список дел?",
    "morning": "Утро, а «Зарядка» уже отмечена — отличный старт. Дальше по плану «Чтение», остальное сложится по ходу.",
    "struggling": "«Чтение» не получается уже четвёртый день из пяти — это чаще про размер задачи, чем про лень. Сколько минут для тебя точно реально?",
    "missed_habit": "Сегодня не увидел твоей обычной вечерней тренировки. День просто пошёл иначе?",
    "task_progress": "С привычками сегодня уже хорошо, а главная задача пока стоит на месте. Как идёт работа над ней?",
    "task_remaining": "Привычки закрыты, а «Закончить отчёт» ещё ждёт. Получится вернуться к ней до вечера?",
    "positive": "Сегодня ты уже впереди своего обычного темпа. Что сработало?",
    "check_in": "Как сегодня идёт день?",
}


EMPHASIS_SCENARIOS = ("zero_done", "zero_done_evening", "positive", "struggling", "missed_habit", "all_done")


def opener_style(user_id, day_iso, scenario):
    """Как начать: у каждого человека и дня своё; стабильно в течение дня. ТЗ §23–24: чаще без приветствия, имя — акцент (только в
    сообщениях, где оно уместно: заметное отклонение, успех), «Привет, Имя» каждый день — нет."""
    seed = random.Random(f"adam-opener:{user_id}:{day_iso}:{scenario}").random()
    if seed < 0.5:
        return "no_greeting"
    if scenario in EMPHASIS_SCENARIOS and seed < 0.85:
        return "by_name"
    return "short_greeting" if seed >= 0.92 else "no_greeting"


OPENER_GUIDE = {
    "no_greeting": "Без приветствия и без имени — сразу к делу.",
    "by_name": "Начни с обращения по имени (оно здесь — акцент, а не приветствие).",
    "short_greeting": "Можно очень короткое приветствие, но без имени.",
}


def _count_phrase(n):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return f"{n} дел"
    last = n % 10
    return f"{n} дело" if last == 1 else f"{n} дела" if 2 <= last <= 4 else f"{n} дел"


def _hhmm(minute):
    return f"{int(minute) // 60:02d}:{int(minute) % 60:02d}"


def build_checkin_prompt(state, scenario, opener, recent_messages=(), recent_scenarios=()):
    """Данные для модели. Привычки и задачи — вместе, но с разбивкой, чтобы текст мог назвать нужное по имени. Задач нет — об этом
    сказано прямо (ТЗ §4: не придумывать и не спрашивать про задачи)."""
    lines = ["Контекст:"]
    if state.get("language") == "en":
        lines.insert(0, "IMPORTANT: The user's interface language is English — write the message in English.")
    lines.append(f"Имя: {state['first_name'] or 'неизвестно'}")
    gender = {"m": "мужской", "f": "женский"}.get(state.get("gender"))
    lines.append(f"Пол: {gender}" if gender else "Пол: неизвестен — не используй формы прошедшего времени с родом (сделал/сделала), пиши нейтрально")
    time_line = f"Время у человека: {state['time']} ({state['part']})"
    if state.get("hours_left") is not None and 0 < state["hours_left"] <= 9:
        time_line += f"; до конца дня около {state['hours_left']:g} ч"
    lines.append(time_line)
    if state["habits_total"] and state["tasks_total"]:
        lines.append(f"Дел на сегодня выполнено: {state['done']} из {state['total']} (привычки {state['habits_done']}/{state['habits_total']}, задачи {state['tasks_done']}/{state['tasks_total']})")
    elif state["habits_total"]:
        lines.append(f"Привычек на сегодня выполнено: {state['habits_done']} из {state['habits_total']}")
    elif state["tasks_total"]:
        lines.append(f"Задач на сегодня выполнено: {state['tasks_done']} из {state['tasks_total']}")
    else:
        lines.append("Привычек и задач на сегодня нет")
    if state["habits_total"] and not state["tasks_total"]:
        lines.append("Задач на сегодня у человека нет — НЕ упоминай задачи, планирование и «план дня», ничего не предлагай записать")
    if state["pending_habits"]:
        lines.append("Осталось из привычек: " + ", ".join(f"«{t}»" for t in state["pending_habits"][:5]))
    if state["pending_tasks"]:
        lines.append("Осталось из задач: " + ", ".join(f"«{t}»" for t in state["pending_tasks"][:5]))
    if state.get("main_goal"):
        lines.append(f"Главная задача дня: «{state['main_goal']}» — " + ("выполнена" if any(t["done"] and t.get("main") for t in state["tasks"]) else "не выполнена"))
        if state.get("main_is_rest"):
            lines.append("Главная задача — отдых/личное дело: это полноценный план. Не оценивай его как низкую продуктивность, не торопи и не требуй работы")
        elif state.get("main_open") and (state.get("main_age_min") or 0) >= 60:
            lines.append(f"План дня составлен около {int(state['main_age_min'] // 60)} ч назад, главная задача всё это время без отметки")
        hist = state.get("task_hist") or {}
        if hist.get("days_with_main", 0) >= 3 and not state.get("main_is_rest"):
            lines.append(f"Главную задачу человек обычно закрывает: {hist['main_done_days']} из {hist['days_with_main']} последних дней")
    lines.append(f"Серия: {state['streak']} дн. подряд" + (" (под угрозой: сегодня ни одной привычки)" if state["streak"] and state["habits_done"] == 0 and state["habits_total"] else ""))
    pace = state.get("pace") or {}
    if pace.get("days", 0) >= PACE_MIN_DAYS:
        lines.append(f"Обычно к этому времени человек закрывает около {pace['avg_by_now']:.1f} привычки из {pace['avg_total']:.1f} за день")
    # История дней — по факту, без оценок; в сообщение идёт не больше ОДНОГО такого факта и только если он помогает сценарию.
    if pace.get("days", 0) >= PACE_MIN_DAYS:
        lines.append(f"Вчера закрыто привычек: {pace.get('yesterday', 0)}; дней с отметками за последнюю неделю: {pace.get('active_7', 0)} из 7")
    best = int(state.get("best_streak") or 0)
    if 0 < best - state["streak"] <= 3 and state["streak"] > 0:
        lines.append(f"До личного рекорда серии ({best} дн.) осталось {best - state['streak']} дн.")
    if state.get("struggling"):
        lines.append(f"Привычка «{state['struggling']['title']}» не выполнена {state['struggling']['missed']} из последних 5 дней")
    if state.get("struggling_done"):
        lines.append(f"Сегодня выполнена «{state['struggling_done']}» — привычка, которую человек чаще всего пропускает")
    missed = state.get("missed")
    if missed:
        if missed.get("kind") == "planned":
            lines.append(f"У привычки «{missed['title']}» время напоминания {_hhmm(missed['usual_minute'])} уже прошло, а она не выполнена")
        else:
            lines.append(f"Привычку «{missed['title']}» человек обычно выполняет около {_hhmm(missed['usual_minute'])} (по последним дням), сегодня её пока нет")
    if state.get("ahead"):
        lines.append("Сегодня человек впереди своего обычного темпа")
    if state.get("above_usual") and state["left"] == 0:
        lines.append("Результат сегодня выше обычного для этого человека")
    if state.get("mood_hint"):
        lines.append(f"Недавняя тема из разговора (мягко учти, не цитируй): {state['mood_hint']}")
    if recent_scenarios:
        lines.append("Поводы последних сообщений (не повторяй тот же): " + ", ".join(INTENT_LABELS.get(x, x) for x in recent_scenarios))
    lines.append("")
    lines.append("Сценарий: " + SCENARIO_GUIDE[scenario])
    lines.append("Подача: " + OPENER_GUIDE[opener])
    example = REFERENCE_EXAMPLES.get(scenario)
    if example:
        lines.append("Ориентир по тону, длине и структуре (это ПРИМЕР: не копируй из него слова, имя и названия дел): «" + example.format(name="<имя>") + "»")
    if recent_messages:
        lines.append("")
        lines.append("Недавно ты уже писал (не повторяй формулировки):")
        lines.extend(f"— {_short(m, 140)}" for m in recent_messages)
    return "\n".join(lines)


# ------------------------------------------------------------------ тексты без модели и защита от шаблонов

# Запасные тексты на случай, когда модель недоступна. Начинаются со строчной буквы: перед ними может встать имя и запятая.
# Без «успел/сделал»: пол неизвестен. Без «Как дела?».
FALLBACKS = {
    "zero_done": (
        "у тебя пока ничего не отмечено — всё в порядке? Начни с самого лёгкого: «{p1}».",
        "день уже в разгаре, а список ещё нетронут. Возьми самое простое — «{p1}» — и считай, что разогнался.",
        "тихо сегодня 🙂 Может, начнём с малого? «{p1}» займёт пару минут.",
        "пока пусто, и это нормально. Один маленький шаг — «{p1}» — и день пойдёт.",
        "смотрю на список — уже середина дня, а галочек пока нет. День сумасшедший или нет сил? Закроем хотя бы «{p1}».",
    ),
    "zero_done_evening": (
        "вечер, а отметок пока нет — день не потерян. {streak_line}Хватит одного маленького шага: «{p1}».",
        "ещё есть время закрыть хоть что-то. {streak_line}Начни с «{p1}» — это быстро.",
        "день почти закончился, но не всё потеряно. «{p1}» — самый простой вход обратно в ритм. {streak_line}",
    ),
    "behind": (
        "уже {done} из {total} — но обычно у тебя идёт быстрее. Осталось, например, «{p1}». Закроешь его сейчас?",
        "{done} из {total} на сегодня. Самый короткий путь вперёд — «{p1}».",
        "темп сегодня ниже обычного: {done} из {total}. Один небольшой шаг, «{p1}», вернёт ритм.",
    ),
    "one_left": (
        "остался последний шаг — «{p1}». Сделай его, и день закрыт 🙌",
        "{done} из {total} уже позади, остался один рывок: «{p1}».",
        "почти финиш: всего «{p1}» отделяет от полностью закрытого дня.",
        "{done} из {total} уже в копилке, осталось только «{p1}». Найдёшь на это время до сна?",
    ),
    "evening_left": (
        "вечер, закрыто {done} из {total}. Начать проще всего с «{p1}» — что мешает сделать это сейчас?",
        "день подходит к концу: {done} из {total}. Выбери одно из оставшегося — например «{p1}» — и закрой до сна.",
        "уже {done} из {total}. Остаётся {left_phrase}; «{p1}» — самое подходящее для финиша.",
    ),
    "all_done": (
        "все дела на сегодня закрыты — {total} из {total}! Что из сегодняшнего получилось лучше всего?",
        "полный комплект: {total} из {total}. Чем займёшься вечером, когда всё сделано?",
        "сегодня всё выполнено, красиво! Какой момент дня запомнился больше всего?",
        "идеальный день — {total} из {total}! Расскажи, что сегодня было классного помимо рутины?",
    ),
    "struggling": (
        "«{p1}» не получается уже несколько дней подряд — чаще дело в размере задачи, а не в лени. Сколько минут для тебя точно реально?",
        "заметил, что «{p1}» который день не складывается. Давай уменьшим планку — на сколько минут согласен?",
        "«{p1}» ускользает {missed} дня из 5 — это сигнал, что задача великовата. Какой кусочек точно по силам сегодня?",
    ),
    "free_chat": (
        "сегодня в списке пусто, и это хороший повод выбрать главное. Над чем хочется поработать на этой неделе?",
        "какая цель для тебя сейчас важнее всего? Помогу разложить её на первый шаг.",
        "есть минутка? Назови одно дело, которое давно откладываешь, — разберём его вместе.",
    ),
    "morning": (
        "утро — хорошее время начать с «{p1}». Остальное сложится по ходу.",
        "на сегодня в плане {count_phrase}: начни с «{p1}».",
    ),
    "morning_progress": (
        "{done} уже отмечено — хороший старт. Дальше по плану «{p1}».",
        "утро идёт по плану: {done} из {total}. Следом — «{p1}».",
    ),
    "morning_done": (
        "все дела на сегодня уже закрыты — сильное утро.",
    ),
    "morning_empty": (
        "на сегодня пока пусто — добавь одну привычку или задачу, и я помогу удержать ритм.",
    ),
    "missed_habit": (
        "обычно к этому времени «{p1}» уже закрыта, а сегодня пока нет. День пошёл иначе?",
        "не вижу сегодня «{p1}» — по твоему ритму она уже должна была быть. Всё в порядке?",
        "«{p1}» сегодня задержалась. Получится вернуться к ней до вечера?",
    ),
    "task_progress": (
        "с привычками сегодня хорошо, а главная задача — «{p1}» — пока стоит. Как с ней?",
        "главная задача дня — «{p1}». Как идёт работа над ней?",
        "«{p1}» пока не тронута. Что мешает начать?",
    ),
    "task_remaining": (
        "привычки закрыты, а «{p1}» ещё открыта. Получится вернуться к ней сегодня?",
        "осталась главная задача — «{p1}». Успеваешь закрыть до сна?",
        "день почти закончился, «{p1}» ждёт. Сколько времени на неё сегодня реально?",
    ),
    "positive": (
        "сегодня ты уже впереди своего обычного темпа. Что сработало?",
        "заметно лучше обычного: {done} из {total}. Что помогло так разогнаться?",
        "день идёт лучше, чем обычно. Что ты сегодня сделал иначе?",
    ),
    "positive_habit": (
        "«{p1}» сегодня получилось — а это та привычка, которая обычно ускользает. Что помогло?",
        "сегодня ты закрыл «{p1}», хотя чаще её пропускаешь. Что сработало?",
    ),
    "check_in": (
        "что сегодня уже получилось?",
        "как сегодня идёт день?",
        "есть минутка? Расскажи, что у тебя сегодня главное.",
    ),
}


def fallback_text(state, scenario, user_id=None, day_iso=None):
    """Текст без модели. Вариант и подача (имя / без имени) — стабильно на (человек, день), на следующий день другие."""
    user_id = user_id if user_id is not None else state.get("user_id", 0)
    day_iso = day_iso or datetime.now().date().isoformat()
    key = scenario
    if scenario == "positive" and state.get("struggling_done") and not state.get("ahead"):
        key = "positive_habit"
    if scenario == "morning":
        if not state["total"]:
            key = "morning_empty"
        elif state["left"] == 0:
            key = "morning_done"
        elif state["done"]:
            key = "morning_progress"
    pool = FALLBACKS[key]
    rng = random.Random(f"adam-fallback:{user_id}:{day_iso}:{key}")
    streak = int(state.get("streak") or 0)
    streak_unit = "день" if streak % 10 == 1 and streak % 100 != 11 else "дня" if 2 <= streak % 10 <= 4 and not 12 <= streak % 100 <= 14 else "дней"
    pending = state.get("pending") or [""]
    # «Начни с самого лёгкого» — это привычка (задача, особенно главная, обычно тяжелее); для остальных сценариев — первое по списку.
    easy = (state.get("pending_habits") or state.get("pending_tasks") or [""])[0]
    p1 = easy if scenario in ("zero_done", "zero_done_evening", "behind", "evening_left") else pending[0]
    if scenario == "struggling" and state.get("struggling"):
        p1 = state["struggling"]["title"]
    elif scenario in ("task_progress", "task_remaining"):
        p1 = state.get("main_title") or pending[0]
    elif scenario == "missed_habit" and state.get("missed"):
        p1 = state["missed"]["title"]
    elif key == "positive_habit":
        p1 = state["struggling_done"]
    body = rng.choice(pool).format(
        p1=p1, done=state["done"], total=state["total"], left=state["left"],
        missed=(state.get("struggling") or {}).get("missed", 4),
        count_phrase=_count_phrase(state["total"]), left_phrase=_count_phrase(state["left"]),
        streak_line=f"Серия {streak} {streak_unit} держится, и её сохранит даже одна отметка. " if streak > 0 else "",
    ).replace("  ", " ").strip()
    name = state.get("first_name") or ""
    if name and opener_style(user_id, day_iso, scenario) == "by_name":
        return f"{name}, {body}"
    return body[0].upper() + body[1:]


def clean_text(text):
    """Выкидывает markdown, кавычки вокруг всего сообщения, подпись «ADAM:» и лишние переносы."""
    text = str(text or "").strip()
    for mark in ("**", "__", "`", "```"):
        text = text.replace(mark, "")
    text = " ".join(text.split())
    for prefix in ("ADAM:", "Adam:", "ADAM —", "Адам:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    if len(text) >= 2 and text[0] in "\"'«“" and text[-1] in "\"'»”":
        text = text[1:-1].strip()
    return text


def is_acceptable(text, scenario):
    """Короткое, без шаблонных «Как дела?», не больше одного вопроса; утром вопросов нет вовсе. «Как проходит день» допустимо
    только в редком обычном контакте (check_in)."""
    if not text or len(text) > TEXT_MAX_CHARS:
        return False
    low = text.lower().replace("ё", "е")
    banned = [p for p in BANNED_PHRASES if not (scenario == "check_in" and p in CHECK_IN_ALLOWED)]
    if any(phrase in low for phrase in banned) or any(phrase in low for phrase in TOXIC_PHRASES):
        return False
    if text.count("?") > 1 or (scenario == "morning" and "?" in text):
        return False
    sentences = sum(1 for i, ch in enumerate(text) if ch in ".!?…" and (i + 1 == len(text) or text[i + 1] == " "))
    return sentences <= 4


# ------------------------------------------------------------------ допуск и журнал

def is_enabled_for(user_id):
    """Включено ли «Адам пишет первым» этому человеку: админам всегда, остальным — по флагу adam_checkin (с процентом раскатки)."""
    from config import ADMIN_IDS
    from .feature_flags import is_feature_enabled
    return user_id in ADMIN_IDS or is_feature_enabled(FLAG_KEY, user_id)


def is_dormant(user_id, now_utc=None):
    """Давно нет ни отметок, ни разговора с Адамом — такому человеку пишет сценарий возврата серии, а не Адам."""
    now_utc = now_utc or datetime.now(timezone.utc)
    since = (now_utc - timedelta(days=DORMANT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        user = conn.execute("SELECT created_at FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        created = _parse_utc(user["created_at"]) if user else None
        if created and now_utc - created < timedelta(days=DORMANT_DAYS):
            return False
        events = conn.execute(
            "SELECT 1 FROM habit_completion_events WHERE user_id=? AND completed_at>=? LIMIT 1", (user_id, since)
        ).fetchone()
        chat = conn.execute(
            "SELECT 1 FROM ai_messages WHERE user_id=? AND role='user' AND created_at>=? LIMIT 1", (user_id, since)
        ).fetchone()
    finally:
        conn.close()
    return not (events or chat)


def account_old_enough(user_id, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    conn = connect()
    try:
        row = conn.execute("SELECT created_at FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    created = _parse_utc(row["created_at"]) if row else None
    return bool(created) and now_utc - created >= timedelta(hours=MIN_ACCOUNT_AGE_HOURS)


def adam_wrote_today(user_id, day_iso):
    """Адам уже писал сегодня (первое сообщение в чате или пуш) — второе за день не нужно."""
    conn = connect()
    try:
        row = conn.execute("SELECT ai_greet_day FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    return bool(row) and row["ai_greet_day"] == day_iso


def mark_adam_wrote_today(user_id, day_iso):
    conn = connect()
    try:
        conn.execute("UPDATE users SET ai_greet_day=? WHERE telegram_id=?", (day_iso, user_id))
        conn.commit()
    finally:
        conn.close()


def claim_slot(user_id, day_iso, slot):
    """Окно (человек, день, слот) обрабатывается один раз: True — мы первые, False — уже обработано/обрабатывается."""
    conn = connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO adam_checkins(user_id, day, slot, status) VALUES (?,?,?, 'pending')",
            (user_id, day_iso, slot),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def finish_slot(user_id, day_iso, slot, status, scenario=None, source=None, message_id=None, note=None):
    conn = connect()
    try:
        conn.execute(
            "UPDATE adam_checkins SET status=?, scenario=?, source=?, message_id=?, note=? WHERE user_id=? AND day=? AND slot=?",
            (status, scenario, source, message_id, note, user_id, day_iso, slot),
        )
        conn.commit()
    finally:
        conn.close()


def release_slot(user_id, day_iso, slot):
    """Отправка не удалась — отпускаем окно, следующий тик повторит."""
    conn = connect()
    try:
        conn.execute("DELETE FROM adam_checkins WHERE user_id=? AND day=? AND slot=? AND status='pending'", (user_id, day_iso, slot))
        conn.commit()
    finally:
        conn.close()


def pushes_since(user_id, day_from_iso):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM adam_checkins WHERE user_id=? AND day>=? AND status='sent' AND slot IN ('day','evening')",
            (user_id, day_from_iso),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def chat_minutes_ago(user_id):
    """Сколько минут назад человек последний раз писал Адаму (None — никогда)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT created_at FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    last = _parse_utc(row["created_at"]) if row else None
    return int((datetime.now(timezone.utc) - last).total_seconds() // 60) if last else None


def chatted_today(user_id, now_local):
    """Человек сам писал Адаму сегодня — второй разговор «по инициативе Адама» не нужен (ТЗ §15)."""
    minutes = chat_minutes_ago(user_id)
    if minutes is None:
        return False
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return moment.astimezone(now_local.tzinfo).date() == now_local.date()


def seen_minutes_ago(user_id):
    """Сколько минут назад человек был в приложении/боте (users.last_seen, обновляется на каждый запрос); None — неизвестно."""
    conn = connect()
    try:
        row = conn.execute("SELECT last_seen FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["last_seen"]:
        return None
    try:
        seen = datetime.fromisoformat(str(row["last_seen"])).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - seen).total_seconds() // 60))


def recent_scenarios(user_id, limit=INTENT_COOLDOWN):
    """Сценарии последних отправленных сообщений Адама (в чате и пушей), новые первыми — для cooldown повода."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT scenario FROM adam_checkins WHERE user_id=? AND status='sent' AND scenario IS NOT NULL ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [r["scenario"] for r in rows]


def days_since_scenario(user_id, scenario, today):
    """Сколько дней назад Адам последний раз писал по этому поводу (None — ещё не писал)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT MAX(day) AS day FROM adam_checkins WHERE user_id=? AND status='sent' AND scenario=?", (user_id, scenario)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["day"]:
        return None
    try:
        return (today - datetime.strptime(row["day"], "%Y-%m-%d").date()).days
    except ValueError:
        return None


CONVERSATION_CONTEXT_HOURS = 10


def last_proactive_context(user_id, hours=CONVERSATION_CONTEXT_HOURS):
    """Блок для разговора в чате: Адам сам начал разговор (пуш или первое сообщение) — при ответе человека он должен знать, ПОЧЕМУ
    написал и что именно сказал, и вести ту же нить, а не отвечать «с чистого листа». "" — недавнего сообщения по инициативе Адама нет."""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT c.scenario AS scenario, c.note AS note, c.slot AS slot, c.created_at AS created_at, m.message AS message
               FROM adam_checkins c LEFT JOIN ai_messages m ON m.id = c.message_id
               WHERE c.user_id=? AND c.status='sent' ORDER BY c.id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["message"]:
        return ""
    sent = _parse_utc(row["created_at"])
    if sent is None or datetime.now(timezone.utc) - sent > timedelta(hours=hours):
        return ""
    where = "в чате при заходе" if row["slot"] == "app" else "сообщением в Telegram"
    intent = INTENT_LABELS.get(row["scenario"], row["scenario"] or "")
    reason = f"; причина: {row['note']}" if row["note"] else ""
    return (
        f"Ты сам начал этот разговор: {where} написал человеку «{_short(row['message'], 220)}» (повод {intent}{reason}). "
        "Если он отвечает на это — веди ту же нить: реагируй на его слова, не повторяй своё сообщение и не задавай тот же вопрос заново. "
        "Отвечай коротко (1–3 предложения), по-человечески. Если человек говорит, что устал или день пошёл иначе, — не дави: "
        "признай это, предложи одно самое простое действие на сегодня и скажи, что этого достаточно."
    )


def recent_adam_messages(user_id, limit=3):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT message FROM ai_messages WHERE user_id=? AND role='assistant' ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    finally:
        conn.close()
    return [r["message"] for r in reversed(rows)]


def checkin_stats(days=7):
    """Сводка для админки: сколько сообщений Адама отправлено/пропущено за N дней, откуда текст (модель/запасной), по сценариям."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT status, source, scenario, slot, COUNT(*) AS n FROM adam_checkins WHERE day>=? GROUP BY status, source, scenario, slot",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    out = {"sent": 0, "skipped": 0, "llm": 0, "fallback": 0, "by_scenario": {}, "app": 0}
    for row in rows:
        out[row["status"]] = out.get(row["status"], 0) + row["n"]
        if row["status"] == "sent":
            if row["source"] in ("llm", "fallback"):
                out[row["source"]] += row["n"]
            if row["slot"] == "app":
                out["app"] += row["n"]
            out["by_scenario"][row["scenario"] or "?"] = out["by_scenario"].get(row["scenario"] or "?", 0) + row["n"]
    return out


# ------------------------------------------------------------------ демо для «Лаборатории» админки

def _lab_state(first_name, hour, minute, habits, tasks, streak=5, pace=None, gender="m", mood_hint="", style="neutral",
               struggling=None, missed=None, struggling_done=None, main_age_min=None, task_hist=None, last_check_in_days=None):
    state = {
        "user_id": 0, "first_name": first_name, "gender": gender, "hour": hour, "minute": minute,
        "time": f"{hour:02d}:{minute:02d}", "part": part_of_day(hour), "weekday": 2,
        "hours_left": round(max(0, DAY_END_HOUR * 60 - (hour * 60 + minute)) / 60, 1),
        "habits": [{"id": i + 1, "title": t, "done": d, "planned": None} for i, (t, d) in enumerate(habits)],
        "tasks": [{"title": t, "done": d, "main": m} for t, d, m in tasks],
        "main_goal": next((t for t, _d, m in tasks if m), ""), "streak": streak, "best_streak": streak,
        "struggling": struggling, "struggling_done": struggling_done, "missed": missed, "mood_hint": mood_hint,
        "usual": {"day": None, "evening": None}, "first_habit_minute": None,
        "main_age_min": main_age_min, "task_hist": task_hist or {"days_with_main": 0, "main_done_days": 0, "tasks_set": 0, "tasks_done": 0},
        "minutes_since_chat": None, "seen_minutes_ago": None, "recent_scenarios": [], "last_check_in_days": last_check_in_days,
        "language": "ru", "style": style,
        "pace": pace or {"days": 9, "avg_by_now": 2.4, "avg_total": 4.2, "yesterday": 4, "active_7": 6},
    }
    return fill_counts(state)


_LAB_HABITS = [("Зарядка", False), ("Чтение 20 минут", False), ("Холодный душ", False), ("Английский", False)]
_LAB_TASKS = [("Отправить предложение клиенту", False, True), ("Созвон с командой", False, False)]
_OWNER_HABITS = [("Зарядка", False), ("Чтение 15 минут", False), ("Холодный душ", False)]


def lab_scenarios():
    """Демо-сценарии админки: ключ → (название, слот, состояние). Слот — в каком окне такое сообщение уходит."""
    done = lambda items, n: [(t, i < n) for i, (t, _d) in enumerate(items)]
    return {
        "zero_done": ("Днём ничего не выполнено (15:10)", "day",
                      _lab_state("Александр", 15, 10, _LAB_HABITS, _LAB_TASKS)),
        "behind": ("Днём отстаёт от своего темпа (15:40)", "day",
                   _lab_state("Мария", 15, 40, done(_LAB_HABITS, 1), _LAB_TASKS, gender="f")),
        "one_left": ("Остался один пункт — задача (16:05)", "day",
                     _lab_state("Дмитрий", 16, 5, done(_LAB_HABITS, 4), [("Отправить предложение клиенту", True, True), ("Созвон с командой", False, False)])),
        "zero_done_evening": ("Вечером ничего, серия под угрозой (20:20)", "evening",
                              _lab_state("Александр", 20, 20, _LAB_HABITS, _LAB_TASKS, streak=12)),
        "evening_left": ("Вечером осталось несколько дел (20:45)", "evening",
                         _lab_state("Анна", 20, 45, done(_LAB_HABITS, 2), _LAB_TASKS, gender="f")),
        "all_done": ("Всё выполнено — вопрос для рефлексии (21:00)", "evening",
                     _lab_state("Дмитрий", 21, 0, done(_LAB_HABITS, 4), [("Отправить предложение клиенту", True, True), ("Созвон с командой", True, False)], streak=21)),
        "free_chat": ("Дел нет — просто поболтать (20:00)", "evening",
                      _lab_state("Мария", 20, 0, [], [], streak=0, pace={"days": 0, "avg_by_now": 0.0, "avg_total": 0.0}, gender="f")),
        "morning": ("Утро, одна привычка отмечена — без вопросов (09:10)", "app",
                    _lab_state("Александр", 9, 10, done(_LAB_HABITS, 1), _LAB_TASKS)),
        "tired": ("Вчера писал про усталость (15:20)", "day",
                  _lab_state("Алексей", 15, 20, _LAB_HABITS, [], mood_hint="вчера жаловался на усталость и мало сна")),
        "struggling": ("Привычка не получается 4 дня из 5 (20:30)", "evening",
                       _lab_state("Анна", 20, 30, [("Зарядка", True), ("Чтение 20 минут", False), ("Холодный душ", True), ("Английский", False)], [],
                                  gender="f", struggling={"title": "Чтение 20 минут", "missed": 4})),
        # ТЗ 222.md: новые поводы
        "positive": ("День лучше обычного — впереди своего темпа (14:20)", "day",
                     _lab_state("Мария", 14, 20, [("Зарядка", True), ("Чтение 20 минут", True), ("Холодный душ", True), ("Английский", False), ("Вода", False)], [],
                                pace={"days": 9, "avg_by_now": 1.6, "avg_total": 3.4, "yesterday": 3, "active_7": 6}, gender="f")),
        "positive_habit": ("Сделал привычку, которая обычно ускользает (15:10)", "day",
                           _lab_state("Дмитрий", 15, 10, [("Зарядка", True), ("Чтение 20 минут", True), ("Холодный душ", False), ("Английский", False)], [],
                                      struggling_done="Чтение 20 минут", pace={"days": 9, "avg_by_now": 1.2, "avg_total": 3.4, "yesterday": 2, "active_7": 6})),
        "missed_habit": ("Обычная вечерняя тренировка пропущена (20:10)", "evening",
                         _lab_state("Александр", 20, 10, [("Зарядка", True), ("Чтение 20 минут", True), ("Тренировка", False), ("Английский", False), ("Вода", False)], [],
                                    missed={"title": "Тренировка", "usual_minute": 18 * 60 + 20, "late_min": 110, "kind": "typical", "days": 6})),
        "task_progress": ("Привычки в порядке, главная задача стоит (14:40)", "day",
                          _lab_state("Алексей", 14, 40, [("Зарядка", True), ("Чтение 20 минут", True), ("Английский", True)],
                                     [("Закончить презентацию", False, True), ("Ответить клиенту", False, False), ("Купить продукты", True, False)],
                                     main_age_min=300, task_hist={"days_with_main": 5, "main_done_days": 4, "tasks_set": 12, "tasks_done": 9})),
        "task_remaining": ("Привычки закрыты, главная задача открыта (20:20)", "evening",
                           _lab_state("Анна", 20, 20, [("Зарядка", True), ("Чтение 20 минут", True), ("Английский", True)],
                                      [("Закончить презентацию", False, True), ("Ответить клиенту", True, False)], gender="f", main_age_min=600)),
        "check_in": ("Всё идёт по плану — редкий обычный контакт (15:00)", "day",
                     _lab_state("Мария", 15, 0, [("Зарядка", True), ("Чтение 20 минут", True), ("Холодный душ", False), ("Английский", False)], [],
                                pace={"days": 9, "avg_by_now": 2.0, "avg_total": 3.5, "yesterday": 3, "active_7": 7}, gender="f")),
        "rest_day": ("Главная задача — «Отдохнуть»: Адам молчит (14:30)", "day",
                     _lab_state("Дмитрий", 14, 30, [("Зарядка", True), ("Чтение 20 минут", True), ("Английский", False), ("Вода", False)],
                                [("Отдохнуть и восстановиться", False, True)], main_age_min=400, last_check_in_days=3)),
        "on_pace": ("Всё идёт как обычно: повода нет, Адам молчит (15:30)", "day",
                    _lab_state("Александр", 15, 30, [("Зарядка", True), ("Чтение 20 минут", True), ("Английский", False), ("Вода", False)], [],
                               pace={"days": 9, "avg_by_now": 2.0, "avg_total": 3.5, "yesterday": 3, "active_7": 7}, last_check_in_days=3)),
        # три примера владельца проекта: время и счёт — как в его описании
        "owner_a": ("Пример А: дневной провал (14:45, 0/3)", "day",
                    _lab_state("Александр", 14, 45, _OWNER_HABITS, [])),
        "owner_b": ("Пример Б: почти у цели (19:20, 2/3)", "evening",
                    _lab_state("Александр", 19, 20, [("Зарядка", True), ("Холодный душ", True), ("Чтение 15 минут", False)], [])),
        "owner_v": ("Пример В: рефлексия (20:15, 3/3)", "evening",
                    _lab_state("Александр", 20, 15, [("Зарядка", True), ("Холодный душ", True), ("Чтение 15 минут", True)], [])),
    }


def lab_state_summary(state):
    """Короткая сводка состояния для экрана админки."""
    return {
        "time": state["time"], "done": state["done"], "total": state["total"], "streak": state["streak"],
        "pending": state["pending"][:6], "habits": f'{state["habits_done"]}/{state["habits_total"]}',
        "tasks": f'{state["tasks_done"]}/{state["tasks_total"]}', "mood_hint": state.get("mood_hint") or "",
        "pace": state.get("pace"), "struggling": state.get("struggling"), "missed": state.get("missed"),
        "main_is_rest": state.get("main_is_rest"), "main_open": state.get("main_open"), "ahead": state.get("ahead"),
    }
