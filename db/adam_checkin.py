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
import re
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

# Ритм заходов САМОГО человека (visit_rhythm): когда он обычно открывает приложение и когда пришёл сегодня.
RHYTHM_DAYS = 21                     # окно наблюдения
RHYTHM_MIN_DAYS = 3                  # меньше дней истории — «обычно» говорить рано
ARRIVED_NOW_MIN = 45                 # «пришёл только что»: первая активность сегодня не раньше, чем столько минут назад
LATE_VISIT_MIN = 240                 # пришёл позже своего обычного времени на столько минут (и не раньше 14:00)
LATE_VISIT_FROM_HOUR = 14
EVENING_REGULAR_FROM_HOUR = 18
EVENING_REGULAR_WINDOW_MIN = 150     # вечерний человек пришёл не дальше этого от своего обычного времени
PAUSE_DAYS = 3                       # столько дней без заходов — «давно не виделись»
SIMILARITY_LOOKBACK = 8              # с какими прежними сообщениями Адама сравниваем новое, чтобы не повторяться
SIMILARITY_LIMIT = 0.5               # доля общих слов, начиная с которой тексты считаем «слишком похожими»

# Реакция на ТОЛЬКО ЧТО сделанное (15.10, жалоба владельца: Адам написал «темп ниже обычного» через минуту после первой отметки, хотя после
# отметки 30 минут идёт окно x2, и вторую привычку человек мог закрыть через 20 минут). Правило одно для ВСЕХ сообщений Адама.
FRESH_MARK_MIN = 30                  # столько минут после отметки человек «в движении» (= окно x2 Adam Coin, db/habits.py::BONUS_WINDOW_MINUTES)
JUDGE_AFTER_MARK_MIN = 45            # о темпе («ниже обычного», «отстаёшь»), невыполненном и новых делах — только когда после отметки прошло столько
RECENT_MARK_ALLOWED = ("positive", "all_done", "free_chat", "check_in")      # что можно писать, пока человек «в движении»

# Эксперимент «Адам пишет сам после действий человека»: через 35–60 минут после отметки (или 60–90 после разговора в чате) Адам может
# вернуться с одной репликой по смыслу — осталось дело / всё закрыто / вернуться к разговору. Раскатка — флаг adam_followups (записи нет —
# только админы; запись «выключено» — никому). Тот же режим тишины и тумблеры, что у остальных сообщений Адама.
FOLLOWUP_FLAG = "adam_followups"
FOLLOWUP_DELAY = (35, 60)            # минут после отметки; своё число на каждое событие, чтобы не «ровно через час»
FOLLOWUP_CHAT_DELAY = (60, 90)       # после разговора с Адамом в чате — дольше
FOLLOWUP_MAX_AGE_MIN = 150           # позже этого после действия реплика уже не по теме — молчим
FOLLOWUP_FROM_MIN, FOLLOWUP_TO_MIN = 10 * 60 + 30, 21 * 60 + 30
FOLLOWUPS_PER_DAY = 2
FOLLOWUP_CHAT_QUIET_MIN = 45         # человек сам писал Адаму только что — он в разговоре, не вмешиваемся
ADAM_GAP_MIN = 150                   # между двумя сообщениями Адама (пуш, повтор после действия, первое в чате) — не меньше этого
FOLLOWUP_SLOTS = ("fu_mark", "fu_done", "fu_chat", "fu_visit")

SCENARIOS = (
    "zero_done", "zero_done_evening", "behind", "one_left", "evening_left", "all_done", "free_chat", "morning", "struggling",
    "missed_habit", "task_progress", "task_remaining", "positive", "check_in", "late_visit", "evening_regular", "back_after_pause",
    "just_done", "steady", "after_mark", "after_done", "chat_followup", "after_visit",
)

# Повод (intent) из ТЗ для каждого сценария — так он называется в журнале, админке и в контексте разговора.
INTENT_LABELS = {
    "zero_done": "ANOMALY", "behind": "ANOMALY", "zero_done_evening": "STREAK_AT_RISK", "one_left": "LAST_HABIT",
    "evening_left": "PROGRESS", "all_done": "REFLECTION", "free_chat": "CONVERSATION", "morning": "PLAN",
    "struggling": "MISSED_HABIT", "missed_habit": "MISSED_HABIT", "task_progress": "TASK_PROGRESS",
    "task_remaining": "TASK_REMAINING", "positive": "POSITIVE", "check_in": "CHECK_IN",
    "late_visit": "LATE_VISIT", "evening_regular": "USUAL_TIME", "back_after_pause": "WELCOME_BACK",
    "just_done": "MOMENTUM", "steady": "STEADY", "after_mark": "FOLLOW_UP", "after_done": "AFTER_DONE", "chat_followup": "CHAT_FOLLOW_UP",
    "after_visit": "VISIT_FOLLOW_UP",
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
# Вопрос про настроение допустим, только когда он про дело («Как настроение — возьмёмся за наши планы?») у человека, который пришёл
# в своё привычное вечернее время: это пример владельца проекта, а не голое «Как настроение?».
ALLOWED_BY_SCENARIO = {"check_in": CHECK_IN_ALLOWED, "evening_regular": ("как настроение",)}

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


EMPTY_RHYTHM = {"days": 0, "usual_first": None, "usual_part": None, "first_mark": None, "first_today": None, "arrived_now": False, "gap_days": None}


def _rhythm_part(minute):
    if minute is None:
        return None
    return "утро" if minute < 12 * 60 else "день" if minute < 18 * 60 else "вечер"


def visit_rhythm(user_id, now_local):
    """Ритм заходов САМОГО человека за последние 3 недели (только его собственные данные: заходы, отметки, сообщения Адаму).
    days — дней с активностью (без сегодняшнего); usual_first — минута, когда он обычно впервые за день появляется (медиана; ночные
    часы считаются хвостом вечера); usual_part — утро/день/вечер; first_mark — когда обычно отмечает первую привычку; first_today —
    когда была первая активность сегодня (None — ни заходов, ни отметок); arrived_now — пришёл только что (до этого сегодня не было);
    gap_days — сколько дней назад был до сегодня. «Обычно» — только от RHYTHM_MIN_DAYS дней."""
    tz = now_local.tzinfo
    today = now_local.date()
    now_minute = now_local.hour * 60 + now_local.minute
    since = (datetime.now(timezone.utc) - timedelta(days=RHYTHM_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        opens = [r["at"] for r in conn.execute("SELECT at FROM app_opens WHERE user_id=? AND at>=?", (user_id, since)).fetchall()]
        chats = [r["created_at"] for r in conn.execute(
            "SELECT created_at FROM ai_messages WHERE user_id=? AND role='user' AND created_at>=?", (user_id, since)).fetchall()]
        marks = [r["completed_at"] for r in conn.execute(
            "SELECT completed_at FROM habit_completion_events WHERE user_id=? AND completed_at>=?", (user_id, since)).fetchall()]
    finally:
        conn.close()

    first_by_day, mark_by_day, today_first = {}, {}, None
    for kind, values in (("open", opens), ("chat", chats), ("mark", marks)):
        for value in values:
            moment = _parse_utc(value)
            if moment is None:
                continue
            local = moment.astimezone(tz)
            if local.date() > today:
                continue
            minute = local.hour * 60 + local.minute
            if local.date() == today:
                today_first = minute if today_first is None else min(today_first, minute)
                continue
            shifted = minute + 24 * 60 if minute < 4 * 60 else minute
            first_by_day[local.date()] = min(shifted, first_by_day.get(local.date(), 10 ** 6))
            if kind == "mark":
                mark_by_day[local.date()] = min(shifted, mark_by_day.get(local.date(), 10 ** 6))
    days = len(first_by_day)
    usual = int(_median(list(first_by_day.values()))) if days >= RHYTHM_MIN_DAYS else None
    return {
        "days": days, "usual_first": usual, "usual_part": _rhythm_part(usual),
        "first_mark": int(_median(list(mark_by_day.values()))) if len(mark_by_day) >= RHYTHM_MIN_DAYS else None,
        "first_today": today_first, "arrived_now": today_first is None or now_minute - today_first <= ARRIVED_NOW_MIN,
        "gap_days": (today - max(first_by_day)).days if first_by_day else None,
    }


def rhythm_notable(state):
    """Есть ли в ритме что-то, о чём стоит сказать в разговоре: пауза в заходах или человек, который обычно приходит рано, а сегодня — поздно."""
    rhythm = state.get("rhythm") or {}
    if rhythm.get("days", 0) < RHYTHM_MIN_DAYS:
        return False
    if (rhythm.get("gap_days") or 0) >= 2:
        return True
    usual = rhythm.get("usual_first")
    now_minute = state["hour"] * 60 + state.get("minute", 0)
    return bool(usual is not None and rhythm.get("arrived_now") and rhythm.get("usual_part") in ("утро", "день")
                and state["hour"] >= LATE_VISIT_FROM_HOUR and now_minute - usual >= LATE_VISIT_MIN)


def rhythm_lines(state):
    """Строки для модели про ритм человека: когда он обычно приходит, когда пришёл сегодня, сколько его не было. [] — рассказать нечего."""
    rhythm = state.get("rhythm") or {}
    out = []
    usual = rhythm.get("usual_first")
    if usual is not None and rhythm.get("usual_part"):
        when = {"утро": "утром", "день": "днём", "вечер": "вечером"}[rhythm["usual_part"]]
        out.append(f"Ритм человека: обычно заходит в приложение {when} (около {_hhmm(usual)}; по {rhythm['days']} дням за последние 3 недели)")
    if rhythm.get("first_mark") is not None:
        out.append(f"Первую привычку за день обычно отмечает около {_hhmm(rhythm['first_mark'])}")
    if rhythm.get("arrived_now") and rhythm.get("days", 0) >= RHYTHM_MIN_DAYS:
        out.append("Сегодня человек пришёл только сейчас: до этого сегодня не было ни заходов, ни отметок")
    elif rhythm.get("first_today") is not None and rhythm.get("days", 0) >= RHYTHM_MIN_DAYS:
        out.append(f"Сегодня человек впервые был в приложении около {_hhmm(rhythm['first_today'])}")
    gap = rhythm.get("gap_days")
    if gap is not None and gap >= 2 and rhythm.get("days", 0) >= RHYTHM_MIN_DAYS:
        out.append(f"Перед этим человека не было в приложении {gap} дн.")
    if out:
        out.append("Покажи, что замечаешь привычное время захода, — живыми словами («обычно ты здесь с утра», «сегодня тебя долго не было»), "
                   "без точной минуты, без слов «слежу», «по моим данным», «статистика показывает», и не больше одного такого наблюдения")
    return out


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


def _pair_lines(user_id):
    """Факты об ИДУЩЕМ парном задании для сообщения Адама (счёт, срок, кто кого ждёт). Ни приглашений, ни старта завтра: о них говорят
    отдельные пуши задания. Сбой — просто без фактов."""
    try:
        from .pair_quests import context_lines
        return context_lines(user_id, include_idle=False)
    except Exception:
        return []


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
    last_mark_min, last_mark_title = last_mark_info(user_id)
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
        "pair_lines": _pair_lines(user_id),
        "minutes_since_chat": minutes_since_chat,
        "last_mark_min": last_mark_min,
        "last_open_min": last_open_minutes(user_id),
        "last_mark_title": _short(last_mark_title or "", 60),
        "bonus_min_left": bonus_minutes_left(user_id),
        "seen_minutes_ago": seen_minutes_ago(user_id),
        "recent_scenarios": recent_scenarios(user_id, 6),
        "last_check_in_days": days_since_scenario(user_id, "check_in", now_local.date()),
        "language": get_language(user_id),
        "style": get_ai_style(user_id),
        "rhythm": visit_rhythm(user_id, now_local),
        "pace": pace_history(user_id, now_local) if with_pace else {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0, "yesterday": 0, "active_7": 0},
    }
    state = fill_counts(state)
    if not state["habits_done"]:
        state["last_mark_min"] = state["bonus_min_left"] = None        # отметки сняты или день сменился: «только что отметил» — не про этого человека
    return state


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
    if not judge_ready(state):
        out = [c for c in out if c["scenario"] in RECENT_MARK_ALLOWED]
    out.sort(key=lambda c: -c["priority"])
    return out


def recently_marked(state, minutes=FRESH_MARK_MIN):
    """Человек отметил привычку меньше `minutes` минут назад (и она сейчас отмечена): он «в движении», идёт окно x2."""
    since = state.get("last_mark_min")
    return bool(since is not None and since < minutes and state.get("habits_done", 0) > 0)


def judge_ready(state):
    """После последней отметки прошло достаточно времени, чтобы говорить о темпе, невыполненном и новых делах."""
    return not recently_marked(state, JUDGE_AFTER_MARK_MIN)


def behind_evidence(state):
    """Есть ли основание сказать «темп ниже обычного». Свой обычный темп известен — человек заметно позади него (по привычкам). Не известен —
    только когда сделано меньше трети и день уже на исходе (с 15:00): раньше оценивать нечем."""
    pace = state.get("pace") or {}
    if pace.get("days", 0) >= PACE_MIN_DAYS:
        return bool(state["habits_total"] and pace.get("avg_by_now", 0) >= 1 and state["habits_done"] < 0.6 * pace["avg_by_now"])
    return bool(state["total"] and state["done"] / state["total"] < 0.34 and state["hour"] >= 15)


def actionable_left(state):
    """Что осталось и не ждёт своего времени: у привычки со временем, которое ещё впереди, своё напоминание — её не торопим."""
    now_minute = state["hour"] * 60 + state.get("minute", 0)
    items = []
    for habit in state["habits"]:
        planned = _parse_hhmm(habit.get("planned"))
        if habit["done"] or (planned is not None and planned > now_minute):
            continue
        items.append(habit["title"])
    return items + list(state.get("pending_tasks") or [])


def followup_delay(user_id, day_iso, key, chat=False):
    """Через сколько минут после события Адам может вернуться: у (человека, дня, события) всегда одно число из диапазона."""
    lo, hi = FOLLOWUP_CHAT_DELAY if chat else FOLLOWUP_DELAY
    return random.Random(f"adam-followup:{user_id}:{day_iso}:{key}").randint(lo, hi)


def followup_candidates(state, day_iso=None):
    """Поводы вернуться к человеку ПОСЛЕ его действия (эксперимент «Адам пишет сам»): [{"scenario", "slot", "priority", "reason"}], важные
    первыми. Пора — когда с момента действия прошло задержку этого события (35–60 мин после отметки, 60–90 после разговора в чате), но не
    дольше FOLLOWUP_MAX_AGE_MIN: позже реплика уже не по теме. Лимиты дня и допуск (тишина, тумблеры) проверяет вызывающий код."""
    user_id = state.get("user_id", 0)
    day_iso = day_iso or datetime.now().date().isoformat()
    since = state.get("last_mark_min")
    marked = since is not None and state.get("habits_done", 0) > 0
    left_items = actionable_left(state)
    out = []
    if marked and since <= FOLLOWUP_MAX_AGE_MIN:
        if state["left"] == 0 and state["total"]:
            if since >= followup_delay(user_id, day_iso, "done"):
                out.append({"scenario": "after_done", "slot": "fu_done", "priority": 80, "reason": f"всё закрыто {since} мин назад"})
        elif left_items and since >= followup_delay(user_id, day_iso, f"mark{state['habits_done']}"):
            out.append({"scenario": "after_mark", "slot": "fu_mark", "priority": 70,
                        "reason": f"после отметки прошло {since} мин, осталось дел: {len(left_items)}"})
    chat = state.get("minutes_since_chat")
    if (state.get("mood_hint") and chat is not None and left_items and chat <= FOLLOWUP_MAX_AGE_MIN
            and chat >= followup_delay(user_id, day_iso, "chat", chat=True) and (not marked or since > chat)):
        out.append({"scenario": "chat_followup", "slot": "fu_chat", "priority": 60, "reason": f"разговор в чате был {chat} мин назад"})
    opened = state.get("last_open_min")
    if (opened is not None and not marked and state.get("habits_total") and state["left"] and left_items
            and state["hour"] >= 11 and opened <= FOLLOWUP_MAX_AGE_MIN and opened >= followup_delay(user_id, day_iso, "visit")
            and (chat is None or chat > opened)):
        out.append({"scenario": "after_visit", "slot": "fu_visit", "priority": 55, "reason": f"заходил {opened} мин назад, отметок сегодня нет"})
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
    if slot == "followup":
        candidates = followup_candidates(state)
        return (candidates[0]["scenario"], candidates[0]["reason"]) if candidates else (None, "после действия ещё рано или не о чем")
    # 'app': человек сам зашёл в чат — отвечаем на то, что происходит сейчас и на его привычный ритм
    total, done, left = state["total"], state["done"], state["left"]
    if state["hour"] < 12:
        return "morning", "утро: без вопросов"
    rhythm = state.get("rhythm") or {}
    known = rhythm.get("days", 0) >= RHYTHM_MIN_DAYS
    now_minute = state["hour"] * 60 + state.get("minute", 0)
    if known and (rhythm.get("gap_days") or 0) >= PAUSE_DAYS:
        return "back_after_pause", f"не было в приложении {rhythm['gap_days']} дн."
    if total == 0:
        return "free_chat", "нет дел"
    if left == 0:
        return "all_done", "всё выполнено"
    if done == 0:
        usual = rhythm.get("usual_first")
        if known and usual is not None and rhythm.get("arrived_now"):
            if rhythm.get("usual_part") in ("утро", "день") and state["hour"] >= LATE_VISIT_FROM_HOUR and now_minute - usual >= LATE_VISIT_MIN:
                return "late_visit", "обычно приходит раньше, сегодня — только сейчас, ничего не отмечено"
            if rhythm.get("usual_part") == "вечер" and state["hour"] >= EVENING_REGULAR_FROM_HOUR and abs(now_minute - usual) <= EVENING_REGULAR_WINDOW_MIN:
                return "evening_regular", "пришёл в своё привычное вечернее время, ничего не отмечено"
        return ("zero_done_evening" if state["hour"] >= 18 else "zero_done"), "ничего не выполнено"
    if left == 1:
        return "one_left", "остался один пункт"
    # Осталось несколько пунктов. Только что отметил — человек в движении (идёт окно x2): ни слова о темпе. Темп оцениваем, лишь когда
    # после отметки прошло время и есть на что опереться (свой обычный темп); иначе — спокойный итог без оценок.
    if recently_marked(state):
        return "just_done", f"отметил привычку {state['last_mark_min']} мин назад: человек в движении"
    if not judge_ready(state):
        return "steady", "отметка была недавно: темп не оцениваем"
    if state["hour"] >= 18:
        return "evening_left", "осталось несколько пунктов"
    if behind_evidence(state):
        return "behind", "заметно ниже своего обычного темпа"
    return "steady", "идёт ровно: оценивать темп не на чем"


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
    "late_visit": "Человек обычно заходит раньше (его привычное время — в контексте), а сегодня появился только сейчас, поздно, и ничего не отмечено. Дай понять, что ты заметил, что сегодня его долго не было, и спроси ОДИН вопрос без упрёка: что случилось, день пошёл не по плану? Не стыди, не требуй и не перечисляй дела; в конце можно предложить одно самое маленькое дело на остаток дня. Приветствие необязательно.",
    "evening_regular": "Вечер — привычное время этого человека (оно в контексте), он пришёл как обычно, но пока ничего не отмечено. Скажи тепло, что ждал его, и спроси ОДИН вопрос про готовность или настроение взяться за планы на сегодня (вопрос про настроение здесь допустим, если он про дело, например «как настроение — возьмёмся за наши планы?»). Без упрёка, без перечисления дел; можно назвать одно самое простое.",
    "back_after_pause": "Человека не было в приложении несколько дней (сколько — в контексте). Скажи тепло и без упрёка, что заметил паузу и рад его видеть, и задай ОДИН вопрос: что изменилось или с чего удобнее вернуться. Не упоминай серию и потерянные очки, не перечисляй дела.",
    "just_done": "Человек только что отметил привычку (сколько минут назад — в контексте) и идёт дальше. Поддержи по факту (сколько уже сделано, что именно) и назови ОДНО следующее дело из оставшегося как возможность, не как задание. Если окно x2 открыто (минуты в контексте), скажи, что следующая привычка принесёт вдвое больше монет. НИ СЛОВА о низком темпе, отставании, «медленно» и сравнении с обычным днём: человек в движении, торопить нельзя. Вопрос необязателен, максимум один и лёгкий.",
    "steady": "Сегодня уже есть отметки, осталось несколько дел, повода для тревоги нет. Коротко отметь сделанное по факту и предложи на выбор одно следующее дело. НЕ говори о низком темпе и отставании и не сравнивай с обычным днём: оценивать темп не на чем. Один лёгкий вопрос или без вопроса.",
    "after_mark": "Человек недавно отметил привычку (что и сколько минут назад — в контексте) и после этого уже около часа тихо; сегодня осталось ещё несколько дел. Напиши коротко, как наставник, который замечает паузу после хорошего шага: отметь сделанное, назови ОДНО следующее дело и предложи маленький шаг в ближайший час. Без упрёка, без «ты остановился/пропал» и без оценки темпа. Один вопрос максимум.",
    "after_done": "Человек недавно закрыл ВСЁ на сегодня (сколько минут назад — в контексте). Не хвали общими словами: одной фразой отметь результат конкретно (счёт, а серию — только если это помогает) и задай ОДИН вопрос для рефлексии: что получилось лучше всего или какой момент дня запомнился. Новых дел не придумывай.",
    "after_visit": "Человек недавно заходил в приложение (сколько минут назад — в контексте), но сегодня ничего не отметил и ушёл. Дай понять, что ты рядом и ничего страшного: самое сложное — начать. Назови ОДНО самое лёгкое из оставшегося и предложи сделать его за пару минут. Без упрёка и без «зашёл и ничего не сделал»; один лёгкий вопрос.",
    "chat_followup": "Недавно человек писал тебе в чате (тема — в контексте: «Недавняя тема из разговора»; сколько минут назад — тоже). Вернись к этой теме одной фразой, будто помнишь разговор: спроси, как сейчас, и предложи ОДИН самый маленький шаг из оставшегося. Не цитируй дословно, без упрёка и давления.",
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
    "late_visit": "Что случилось — сегодня тебя не было весь день, а обычно ты здесь с утра. День пошёл не по плану?",
    "evening_regular": "Привет, я ждал тебя. Как настроение — возьмёмся сегодня за наши планы?",
    "back_after_pause": "Тебя не было несколько дней — я заметил. Что изменилось и с чего удобнее вернуться?",
    "just_done": "Первая привычка уже есть — хороший старт. Следом хорошо зайдёт «Чтение»: что скажешь?",
    "steady": "Уже 3 из 6 — идёшь ровно. Дальше хорошо зайдёт «Чтение»: получится сегодня?",
    "after_mark": "«Зарядка» уже в копилке — хороший шаг. Следом по списку «Чтение»: возьмёшь его в ближайший час?",
    "after_done": "День закрыт полностью — 4 из 4. Что из сегодняшнего далось легче всего?",
    "chat_followup": "Про усталость мы говорили недавно — как сейчас? Если силы вернулись, самое лёгкое — «Зарядка».",
    "after_visit": "Самое сложное — начать, дальше легче. «Зарядка» займёт пару минут: возьмёшь её сейчас?",
}


EMPHASIS_SCENARIOS = ("zero_done", "zero_done_evening", "positive", "struggling", "missed_habit", "all_done")


def opener_style(user_id, day_iso, scenario):
    """Как начать: у каждого человека и дня своё; стабильно в течение дня. ТЗ §23–24: чаще без приветствия, имя — акцент (только в
    сообщениях, где оно уместно: заметное отклонение, успех), «Привет, Имя» каждый день — нет."""
    seed = random.Random(f"adam-opener:{user_id}:{day_iso}:{scenario}").random()
    # Первое сообщение про ритм: опоздавшего чаще спрашиваем сразу («Что случилось…»), вечернего человека встречаем «Привет, я ждал тебя».
    if scenario == "late_visit":
        return "no_greeting" if seed < 0.7 else "by_name"
    if scenario == "evening_regular":
        return "short_greeting" if seed < 0.6 else "no_greeting"
    if scenario == "back_after_pause":
        return "short_greeting" if seed < 0.5 else "by_name"
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


# Подход к тексту: у каждого сообщения свой («вопрос о причине», «поддержка + шаг» …), чтобы Адам не писал по одной схеме.
ANGLES = (
    "Начни с наблюдения и закончи коротким вопросом о причине.",
    "Скажи одной фразой, что заметил, и спроси про самочувствие или энергию — без «как дела».",
    "Задай вопрос прямо, без вступления: одна короткая реплика.",
    "Сначала поддержи (день ещё можно спасти), потом предложи один маленький шаг.",
    "Лёгкий тон с едва заметной иронией — без колкостей и упрёков.",
    "Спроси, что помешало, и предложи на выбор самое простое действие.",
)


def pick_angle(user_id, day_iso, scenario, last_message=""):
    """Подход на этот раз: зависит от человека, дня, повода и от последнего сообщения Адама — подряд два сообщения не повторят схему."""
    return random.Random(f"adam-angle:{user_id}:{day_iso}:{scenario}:{last_message}").choice(ANGLES)


def _words(text):
    low = str(text or "").lower().replace("ё", "е")
    return [w for w in re.findall(r"[a-zа-я0-9]+", low) if len(w) > 2]


def similarity(a, b):
    """Доля общих слов двух текстов (0…1): слова короче трёх букв не считаем."""
    sa, sb = set(_words(a)), set(_words(b))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def too_similar(text, recent):
    """Новое сообщение слишком похоже на одно из недавних: много общих слов или то же начало из трёх слов."""
    head = _words(text)[:3]
    for old in recent or ():
        if similarity(text, old) >= SIMILARITY_LIMIT or (len(head) == 3 and _words(old)[:3] == head):
            return True
    return False


def _count_phrase(n):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return f"{n} дел"
    last = n % 10
    return f"{n} дело" if last == 1 else f"{n} дела" if 2 <= last <= 4 else f"{n} дел"


def _hhmm(minute):
    minute = int(minute) % (24 * 60)            # ночные минуты в ритме хранятся со сдвигом +24 ч
    return f"{minute // 60:02d}:{minute % 60:02d}"


def recency_lines(state):
    """Факты о ТОЛЬКО ЧТО сделанном: когда была последняя отметка и открыто ли окно x2 Adam Coin. Правило одно для всех сообщений Адама (и в чате,
    и пушей): пока человек в движении, о низком темпе, отставании и новых делах речи нет."""
    out = []
    since = state.get("last_mark_min")
    if since is not None and since < 180 and state.get("habits_done", 0) > 0:
        title = state.get("last_mark_title")
        out.append(f"Последнюю привычку человек отметил {int(since)} мин назад" + (f" («{title}»)" if title else ""))
        if since < JUDGE_AFTER_MARK_MIN:
            out.append("После последней отметки прошло мало времени: человек в движении. НЕ говори о низком темпе, отставании, «медленно» и "
                       "«ниже обычного», не сравнивай с обычным днём и не торопи; поддержи и подскажи следующий шаг")
    bonus = state.get("bonus_min_left")
    if bonus:
        out.append(f"Окно x2 Adam Coin открыто ещё около {int(bonus)} мин: следующая отмеченная привычка принесёт вдвое больше монет — "
                   "упомяни это как возможность одной фразой, не как давление")
    return out


def build_checkin_prompt(state, scenario, opener, recent_messages=(), recent_scenarios=(), angle=None):
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
    lines.extend(rhythm_lines(state))
    lines.extend(recency_lines(state))
    if state.get("mood_hint"):
        lines.append(f"Недавняя тема из разговора (мягко учти, не цитируй): {state['mood_hint']}")
        if scenario == "chat_followup" and state.get("minutes_since_chat") is not None:
            lines.append(f"Человек писал тебе в чате {state['minutes_since_chat']} мин назад")
    pair = state.get("pair_lines") or []
    if pair:
        lines.extend(pair)
        lines.append(
            "Парное задание упомяни, только когда это естественно усиливает сообщение, одной короткой фразой и не главной темой; "
            "имя напарника — как написано, без склонения и без догадок о поле; напарником не упрекай и не пугай"
        )
    if recent_scenarios:
        lines.append("Поводы последних сообщений (не повторяй тот же): " + ", ".join(INTENT_LABELS.get(x, x) for x in recent_scenarios))
    lines.append("")
    lines.append("Сценарий: " + SCENARIO_GUIDE[scenario])
    lines.append("Подача: " + OPENER_GUIDE[opener])
    if angle:
        lines.append("Подход на этот раз: " + angle)
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
    # Без «зашёл/пришёл»: пол неизвестен — только настоящее время и безличные обороты.
    "late_visit": (
        "что случилось — сегодня тебя не было весь день, а обычно ты здесь раньше. День пошёл не по плану?",
        "тебя сегодня не было до самого вечера, хотя обычно ты заглядываешь раньше. Что-то пошло иначе?",
        "день почти закончился, а ты появляешься только сейчас. Что-то сбило планы? Хватит одного маленького шага — «{p1}».",
        "странно видеть тебя так поздно — обычно ты приходишь раньше. День выдался непростым?",
        "вот и ты — обычно раньше. Что-то поменяло планы на сегодня?",
    ),
    "evening_regular": (
        "я ждал тебя. Как настроение — возьмёмся за наши планы?",
        "вот и ты — самое время для наших дел. С чего начнём: «{p1}»?",
        "вечер — наше время. Для начала — «{p1}»: берём?",
        "ты вовремя: до конца дня ещё есть запас. С чего начнём — «{p1}»?",
    ),
    "back_after_pause": (
        "тебя не было {gap_phrase} — я заметил. С чего удобнее вернуться?",
        "давно не виделись: {gap_phrase}. Что изменилось за это время?",
        "рад видеть тебя снова — прошло {gap_phrase}. Что помогло бы вернуться в ритм?",
    ),
    # Только что отметил: без слов о темпе. {x2_line} — про окно x2, пока оно открыто (иначе пусто).
    "just_done": (
        "уже {done} из {total} — хороший ход. {x2_line}Следом можно «{p1}», когда будет момент.",
        "{done} из {total} есть. {x2_line}Что возьмёшь следом — «{p1}»?",
        "ритм пошёл: {done} из {total}. {x2_line}«{p1}» подойдёт следующей, без спешки.",
        "хорошее начало — {done} из {total}. {x2_line}Дальше хорошо зайдёт «{p1}».",
    ),
    "steady": (
        "уже {done} из {total} — идём ровно. Дальше хорошо зайдёт «{p1}».",
        "{done} из {total} в копилке. Что возьмёшь следом — «{p1}»?",
        "есть {done} из {total}, день идёт. Когда будет окно, «{p1}» подойдёт.",
        "{done} из {total} уже позади. «{p1}» — хороший кандидат на следующий шаг.",
    ),
    # {last} — название последней отмеченной привычки; шаблоны с ним берутся, только если оно известно.
    "after_mark": (
        "«{last}» уже в копилке — хороший шаг. Следом по списку «{p1}»: возьмёшь в ближайший час?",
        "{done} из {total} уже есть. Пока день идёт — самое время для «{p1}».",
        "после «{last}» наступила пауза — это нормально. «{p1}» займёт пару минут: сделаем?",
        "{done} из {total} — хороший задел. «{p1}» ждёт: возьмёшь в ближайший час?",
    ),
    "after_done": (
        "день закрыт полностью — {total} из {total}. Что из сегодняшнего далось легче всего?",
        "все дела на сегодня выполнены — сильный результат. Что помогло так собраться?",
        "{total} из {total}: ничего не осталось. Что сегодня удалось лучше всего?",
        "полностью закрытый день — {total} из {total}. Какой момент запомнился больше всего?",
    ),
    "after_visit": (
        "самое сложное — начать, дальше легче. «{p1}» займёт пару минут: возьмёшь сейчас?",
        "пока в списке тихо. Хватит одного маленького шага — «{p1}».",
        "день идёт, а список ждёт. Начни с «{p1}»: пара минут — и ритм пошёл.",
        "один маленький шаг сейчас — «{p1}» — и день сдвинется с места.",
    ),
    "chat_followup": (
        "недавно в чате был разговор — как сейчас? Если силы есть, самое лёгкое — «{p1}».",
        "после нашего разговора прошло немного времени. Получится что-то из списка — например, «{p1}»?",
        "что с настроением после разговора? Один маленький шаг — «{p1}» — и достаточно.",
        "вернусь к нашему разговору: стало легче? Если да, «{p1}» займёт пару минут.",
    ),
}


def _days_phrase(n):
    n = abs(int(n or 0))
    if n <= 0:
        return "несколько дней"
    unit = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
    return f"уже {n} {unit}"


def fallback_text(state, scenario, user_id=None, day_iso=None, avoid=()):
    """Текст без модели. Вариант и подача (имя / без имени) — стабильно на (человек, день), на следующий день другие.
    avoid — недавние сообщения Адама: вариант, слишком похожий на них, заменяется другим из набора."""
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
    last_title = state.get("last_mark_title") or ""
    if not last_title:
        pool = tuple(t for t in pool if "{last}" not in t) or pool
    if scenario == "behind" and (state.get("pace") or {}).get("days", 0) < PACE_MIN_DAYS:
        pool = tuple(t for t in pool if "обычн" not in t) or pool           # про «обычно у тебя быстрее» — только когда свой темп известен
    rng = random.Random(f"adam-fallback:{user_id}:{day_iso}:{key}")
    streak = int(state.get("streak") or 0)
    streak_unit = "день" if streak % 10 == 1 and streak % 100 != 11 else "дня" if 2 <= streak % 10 <= 4 and not 12 <= streak % 100 <= 14 else "дней"
    pending = state.get("pending") or [""]
    # «Начни с самого лёгкого» — это привычка (задача, особенно главная, обычно тяжелее); для остальных сценариев — первое по списку.
    easy = (state.get("pending_habits") or state.get("pending_tasks") or [""])[0]
    p1 = easy if scenario in ("zero_done", "zero_done_evening", "behind", "evening_left", "late_visit", "evening_regular") else pending[0]
    if scenario == "struggling" and state.get("struggling"):
        p1 = state["struggling"]["title"]
    elif scenario in ("task_progress", "task_remaining"):
        p1 = state.get("main_title") or pending[0]
    elif scenario == "missed_habit" and state.get("missed"):
        p1 = state["missed"]["title"]
    elif key == "positive_habit":
        p1 = state["struggling_done"]
    elif scenario in ("just_done", "steady", "after_mark", "chat_followup", "after_visit"):
        p1 = (actionable_left(state) or pending)[0]
    x2_line = "Окно x2 ещё открыто — следующая привычка принесёт вдвое больше монет. " if state.get("bonus_min_left") else ""
    gap_phrase = _days_phrase((state.get("rhythm") or {}).get("gap_days"))

    def render(template):
        return template.format(
            p1=p1, done=state["done"], total=state["total"], left=state["left"],
            missed=(state.get("struggling") or {}).get("missed", 4),
            count_phrase=_count_phrase(state["total"]), left_phrase=_count_phrase(state["left"]),
            streak_line=f"Серия {streak} {streak_unit} держится, и её сохранит даже одна отметка. " if streak > 0 else "",
            gap_phrase=gap_phrase, last=last_title, x2_line=x2_line,
        ).replace("  ", " ").strip()

    chosen = rng.choice(pool)
    body = render(chosen)
    if avoid and too_similar(body, avoid):
        for variant in pool:
            candidate = render(variant)
            if not too_similar(candidate, avoid):
                body = candidate
                break
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
    banned = [p for p in BANNED_PHRASES if p not in ALLOWED_BY_SCENARIO.get(scenario, ())]
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


def last_mark_info(user_id, now_utc=None):
    """(минут назад, название) последней отметки привычки за последние сутки; (None, None) — отметок нет."""
    now_utc = now_utc or datetime.now(timezone.utc)
    since = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        row = conn.execute(
            """SELECT e.completed_at AS at, h.title AS title FROM habit_completion_events e LEFT JOIN habits h ON h.id = e.habit_id
               WHERE e.user_id=? AND e.completed_at>=? ORDER BY e.id DESC LIMIT 1""",
            (user_id, since),
        ).fetchone()
    finally:
        conn.close()
    moment = _parse_utc(row["at"]) if row else None
    if moment is None:
        return None, None
    return max(0, int((now_utc - moment).total_seconds() // 60)), row["title"]


def last_open_minutes(user_id, now_utc=None):
    """Сколько минут назад человек последний раз открывал приложение (по журналу заходов app_opens); None — заходов нет."""
    now_utc = now_utc or datetime.now(timezone.utc)
    conn = connect()
    try:
        row = conn.execute("SELECT MAX(at) AS at FROM app_opens WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    moment = _parse_utc(row["at"]) if row and row["at"] else None
    return max(0, int((now_utc - moment).total_seconds() // 60)) if moment else None


def last_mark_minutes(user_id, now_utc=None):
    """Сколько минут назад человек последний раз отметил привычку (None — за сутки ни одной)."""
    return last_mark_info(user_id, now_utc)[0]


def bonus_minutes_left(user_id, now_utc=None):
    """Сколько минут ещё открыто окно x2 Adam Coin (None — не открыто). Окно открывает каждая отметка, пока есть что отмечать дальше."""
    from .streak import get_bonus_window
    until = get_bonus_window(user_id)
    if until is None:
        return None
    if until.tzinfo is not None:
        until = until.astimezone(timezone.utc).replace(tzinfo=None)
    seconds = (until - (now_utc or datetime.now(timezone.utc)).replace(tzinfo=None)).total_seconds()
    return int(-(-seconds // 60)) if seconds > 0 else None


def adam_wrote_minutes_ago(user_id, day_iso=None, now_utc=None):
    """Сколько минут назад Адам последний раз сам написал человеку (пуш, повтор после действия, первое сообщение в чате); None — не писал.
    day_iso — только сообщения этого дня (местного): интервал между сообщениями нужен внутри дня, а не через полночь.
    now_utc — «сейчас» (для проверок со своим временем; по умолчанию настоящее)."""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT created_at FROM adam_checkins WHERE user_id=? AND status='sent'" + (" AND day=?" if day_iso else "") + " ORDER BY id DESC LIMIT 1",
            (user_id, day_iso) if day_iso else (user_id,),
        ).fetchone()
    finally:
        conn.close()
    last = _parse_utc(row["created_at"]) if row else None
    return max(0, int(((now_utc or datetime.now(timezone.utc)) - last).total_seconds() // 60)) if last else None


def followups_sent_today(user_id, day_iso):
    """Сколько повторов после действий Адам уже отправил (или отправляет прямо сейчас) человеку за этот день."""
    marks = ",".join("?" * len(FOLLOWUP_SLOTS))
    conn = connect()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM adam_checkins WHERE user_id=? AND day=? AND slot IN ({marks}) AND status IN ('sent','pending')",
            (user_id, day_iso, *FOLLOWUP_SLOTS),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])


def followups_enabled_for(user_id):
    """Эксперимент «Адам пишет сам после действий»: записи флага нет — только админам; запись есть — по ней (включено и процент раскатки),
    «выключено» отключает эксперимент всем, в том числе админам."""
    from config import ADMIN_IDS
    conn = connect()
    try:
        row = conn.execute("SELECT enabled, rollout_pct FROM feature_flags WHERE key=?", (FOLLOWUP_FLAG,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return user_id in ADMIN_IDS
    if not row["enabled"]:
        return False
    if user_id in ADMIN_IDS:
        return True
    from .feature_flags import is_feature_enabled
    return is_feature_enabled(FOLLOWUP_FLAG, user_id)


def followup_possible(user_id):
    """Дешёвая проверка перед тяжёлой сборкой состояния: есть ли вообще действие (отметка или разговор), от которого сейчас могла пройти задержка."""
    since = last_mark_minutes(user_id)
    if since is not None and FOLLOWUP_DELAY[0] <= since <= FOLLOWUP_MAX_AGE_MIN:
        return True
    chat = chat_minutes_ago(user_id)
    if chat is not None and FOLLOWUP_CHAT_DELAY[0] <= chat <= FOLLOWUP_MAX_AGE_MIN:
        return True
    opened = last_open_minutes(user_id)
    return opened is not None and FOLLOWUP_DELAY[0] <= opened <= FOLLOWUP_MAX_AGE_MIN


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
               struggling=None, missed=None, struggling_done=None, main_age_min=None, task_hist=None, last_check_in_days=None, rhythm=None,
               last_mark_min=None, last_mark_title="", bonus_min_left=None, minutes_since_chat=None, last_open_min=None):
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
        "minutes_since_chat": minutes_since_chat, "seen_minutes_ago": None, "recent_scenarios": [], "last_check_in_days": last_check_in_days,
        "last_mark_min": last_mark_min, "last_mark_title": last_mark_title, "bonus_min_left": bonus_min_left, "last_open_min": last_open_min,
        "language": "ru", "style": style, "rhythm": dict(rhythm or EMPTY_RHYTHM),
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
        # первое сообщение в чате с учётом ритма заходов человека
        "late_visit": ("Обычно заходит утром, а пришёл только вечером, ничего не отмечено (19:40)", "app",
                       _lab_state("Александр", 19, 40, _LAB_HABITS, _LAB_TASKS, streak=9, rhythm={
                           "days": 12, "usual_first": 8 * 60 + 40, "usual_part": "утро", "first_mark": 9 * 60,
                           "first_today": 19 * 60 + 36, "arrived_now": True, "gap_days": 1})),
        "evening_regular": ("Вечерний человек пришёл в своё время, ничего не отмечено (20:10)", "app",
                            _lab_state("Мария", 20, 10, _LAB_HABITS, _LAB_TASKS, streak=6, gender="f", rhythm={
                                "days": 10, "usual_first": 20 * 60 + 5, "usual_part": "вечер", "first_mark": 20 * 60 + 30,
                                "first_today": 20 * 60 + 8, "arrived_now": True, "gap_days": 1})),
        "back_after_pause": ("Не заходил 4 дня, вернулся днём (13:20)", "app",
                             _lab_state("Дмитрий", 13, 20, _LAB_HABITS, _LAB_TASKS, streak=0, rhythm={
                                 "days": 9, "usual_first": 9 * 60 + 10, "usual_part": "утро", "first_mark": None,
                                 "first_today": 13 * 60 + 15, "arrived_now": True, "gap_days": 4})),
        # реакция на только что сделанное (15.10) и «Адам пишет сам после действий» (эксперимент)
        "just_done": ("Минуту назад отметил первую привычку, окно x2 открыто (14:29)", "app",
                      _lab_state("Александр", 14, 29, done(_LAB_HABITS, 1), _LAB_TASKS, last_mark_min=1, last_mark_title="Зарядка", bonus_min_left=29)),
        "steady": ("Половина сделана давно, оценивать темп не на чем (15:10)", "app",
                   _lab_state("Мария", 15, 10, done(_LAB_HABITS, 3), _LAB_TASKS, gender="f", last_mark_min=75, last_mark_title="Холодный душ",
                              pace={"days": 0, "avg_by_now": 0.0, "avg_total": 0.0, "yesterday": 0, "active_7": 0})),
        "after_mark": ("Час назад отметил привычку, дальше тишина (15:35)", "followup",
                       _lab_state("Дмитрий", 15, 35, done(_LAB_HABITS, 1), _LAB_TASKS, last_mark_min=62, last_mark_title="Зарядка")),
        "after_done": ("Всё закрыто больше часа назад (19:10)", "followup",
                       _lab_state("Анна", 19, 10, done(_LAB_HABITS, 4), [("Отправить предложение клиенту", True, True), ("Созвон с командой", True, False)],
                                  gender="f", streak=14, last_mark_min=70, last_mark_title="Английский")),
        "after_visit": ("Час назад заходил в приложение, ничего не отметил (15:20)", "followup",
                        _lab_state("Мария", 15, 20, _LAB_HABITS, _LAB_TASKS, gender="f", last_open_min=65)),
        "chat_followup": ("Полтора часа назад писал Адаму про усталость (16:00)", "followup",
                          _lab_state("Алексей", 16, 0, _LAB_HABITS, [], mood_hint="устал, мало сил", minutes_since_chat=95)),
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
        "last_mark_min": state.get("last_mark_min"), "bonus_min_left": state.get("bonus_min_left"),
    }
