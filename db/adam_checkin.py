"""
Адам пишет первым: проактивные сообщения наставника (состояние человека → сценарий → текст).

Что тут есть (всё без вызова модели — её зовёт adam_checkin.py в корне):
  • build_checkin_state — «User_State»: имя, время, ПРИВЫЧКИ И ЗАДАЧИ на сегодня (вместе: «выполнено X из Y»),
    названия оставшихся, серия, пометка о настроении, обычный темп человека;
  • расписание: окна «день» 14:00–16:30 и «вечер» 19:30–21:30, случайный (но стабильный на день) момент внутри окна,
    «чатовые» дни — 2–3 в неделю не подряд, лимиты 1 сообщение в день и 5 в неделю;
  • pick_scenario — какое сообщение нужно (ничего не выполнено / отстаёт от темпа / остался один пункт / всё готово / …);
  • build_checkin_prompt — блок данных для модели; fallback_text — запасные тексты без «Как дела?»;
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

# Окна пушей в локальном времени человека, минуты от полуночи.
SLOT_WINDOWS = {"day": (14 * 60, 16 * 60 + 30), "evening": (19 * 60 + 30, 21 * 60 + 30)}
DELIVERY_MARGIN_MIN = 10             # момент отправки не ближе 10 минут к концу окна
MAX_PUSH_PER_WEEK = 5                # пушей Адама за скользящие 7 дней (в день — максимум один)
CHAT_DAYS_PER_WEEK = (2, 3)          # «просто поболтать» (вопрос для рефлексии) — 2–3 дня в неделю
MIN_ACCOUNT_AGE_HOURS = 20           # в день регистрации работают тур и «Привет! Я Adam…» — Адам молчит
RECENT_CHAT_MINUTES = 180            # писал Адаму недавно — пушить незачем
DORMANT_DAYS = 7                     # неделю без отметок и без чата — этим занимается возвращение серии, а не Адам
PACE_HISTORY_DAYS = 14
PACE_MIN_DAYS = 3                    # меньше дней истории — темп не оцениваем
TEXT_MAX_CHARS = 320

SCENARIOS = (
    "zero_done", "zero_done_evening", "behind", "one_left", "evening_left", "all_done", "free_chat", "morning",
)

# Шаблонные фразы, которых Адам писать не должен (проверка по тексту без ё и в нижнем регистре).
BANNED_PHRASES = (
    "как дела", "как твои дела", "как ваши дела", "как ты?", "как настроение", "как прошел день",
    "как прошел твой день", "как проходит день", "как жизнь", "как поживаешь",
)


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


def planned_minute(user_id, day_iso, slot):
    """Во сколько (минут от полуночи) Адам напишет в этом окне. Случайно, но для (человек, день, окно) всегда одно и то же —
    не «ровно в 15:00 для всех», и тик планировщика не перебрасывает монетку заново."""
    start, end = SLOT_WINDOWS[slot]
    return random.Random(f"adam-slot:{user_id}:{day_iso}:{slot}").randrange(start, end - DELIVERY_MARGIN_MIN)


def slot_due(user_id, now_local):
    """Окно, в котором сейчас пора писать: уже наступил запланированный момент, а окно ещё не закончилось. Иначе None."""
    minute = now_local.hour * 60 + now_local.minute
    day_iso = now_local.date().isoformat()
    for slot, (start, end) in SLOT_WINDOWS.items():
        if start <= minute < end and minute >= planned_minute(user_id, day_iso, slot):
            return slot
    return None


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
        return {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0}
    by_now = [sum(1 for minute in habits.values() if minute <= now_minute) for habits in per_day.values()]
    totals = [len(habits) for habits in per_day.values()]
    return {"days": len(per_day), "avg_by_now": sum(by_now) / len(by_now), "avg_total": sum(totals) / len(totals)}


def fill_counts(state):
    """Выводит счётчики из списков привычек и задач (так состояние собирают и боевой код, и демо-сценарии админки)."""
    habits, tasks = state["habits"], state["tasks"]
    state["habits_total"], state["habits_done"] = len(habits), sum(1 for h in habits if h["done"])
    state["tasks_total"], state["tasks_done"] = len(tasks), sum(1 for t in tasks if t["done"])
    state["total"] = state["habits_total"] + state["tasks_total"]
    state["done"] = state["habits_done"] + state["tasks_done"]
    state["left"] = state["total"] - state["done"]
    state["pending_habits"] = [h["title"] for h in habits if not h["done"]]
    state["pending_tasks"] = [t["title"] for t in tasks if not t["done"]]
    # Что назвать первым: главная задача дня, затем остальные задачи, затем привычки.
    main_first = sorted((t for t in tasks if not t["done"]), key=lambda t: not t.get("main"))
    state["pending"] = [t["title"] for t in main_first] + state["pending_habits"]
    return state


def build_checkin_state(user_id, now_local=None, with_pace=True):
    """User_State для выбора сценария и для модели. Привычки и задачи на сегодня считаются ВМЕСТЕ."""
    from db import get_ai_style, get_daily_plan, get_gender, get_habits, get_incomplete_habits, get_language, get_progress
    from db import get_proactive_topic, get_user
    from .streak import get_timezone

    now_local = now_local or datetime.now(ZoneInfo(get_timezone(user_id)))
    user = get_user(user_id)
    pending_ids = {h["id"] for h in get_incomplete_habits(user_id)}
    habits = []
    for habit in get_habits(user_id):
        if habit["completed"]:
            habits.append({"title": _short(habit["title"]), "done": True})
        elif habit["id"] in pending_ids:      # сознательно пропущенные и «недельная норма уже выполнена» не считаем
            habits.append({"title": _short(habit["title"]), "done": False})
    tasks = []
    plan = get_daily_plan(user_id)
    main_goal = (plan.get("main_goal") or "").strip() if plan else ""
    if main_goal:
        tasks.append({"title": _short(main_goal), "done": bool(plan.get("main_goal_completed")), "main": True})
    for task in (plan["tasks"] if plan else []):
        if (task["text"] or "").strip():
            tasks.append({"title": _short(task["text"]), "done": bool(task["completed"]), "main": False})

    last_chat = connect()
    try:
        row = last_chat.execute(
            "SELECT created_at FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1", (user_id,)
        ).fetchone()
    finally:
        last_chat.close()
    last_utc = _parse_utc(row["created_at"]) if row else None
    minutes_since_chat = int((datetime.now(timezone.utc) - last_utc).total_seconds() // 60) if last_utc else None

    progress = get_progress(user_id) or {}
    state = {
        "user_id": user_id,
        "first_name": _short((user["first_name"] if user else "") or "", 40),
        "gender": get_gender(user_id),
        "hour": now_local.hour,
        "minute": now_local.minute,
        "time": now_local.strftime("%H:%M"),
        "part": part_of_day(now_local.hour),
        "weekday": now_local.weekday(),
        "habits": habits,
        "tasks": tasks,
        "main_goal": main_goal,
        "streak": int(progress.get("streak") or 0),
        "mood_hint": _short(get_proactive_topic(user_id), 160),
        "minutes_since_chat": minutes_since_chat,
        "language": get_language(user_id),
        "style": get_ai_style(user_id),
        "pace": pace_history(user_id, now_local) if with_pace else {"days": 0, "avg_by_now": 0.0, "avg_total": 0.0},
    }
    return fill_counts(state)


# ------------------------------------------------------------------ какой сценарий нужен

def pick_scenario(state, slot, chat_day=False):
    """(сценарий | None, причина). slot: 'day' | 'evening' — плановые пуши; 'app' — первое сообщение при заходе в чат.

    День (14:00–16:30): пишем, только если человек отстаёт от СВОЕГО обычного темпа (ничего не выполнено, остался один пункт,
    сильно меньше обычного). Вечер (19:30–21:30): итог дня; «просто поболтать» (всё готово / нет дел) — только в чатовые дни.
    Утром вопросов нет вообще."""
    total, done, left = state["total"], state["done"], state["left"]
    pace = state.get("pace") or {}
    if slot == "day":
        if total == 0:
            return None, "дел на сегодня нет"
        if left == 0:
            return None, "всё уже выполнено"
        known = pace.get("days", 0) >= PACE_MIN_DAYS
        if done == 0:
            if known and pace.get("avg_by_now", 0) < 0.5:
                return None, "обычно человек делает привычки позже"
            return "zero_done", "до сих пор ничего не выполнено"
        if left == 1:
            return "one_left", "остался один пункт"
        behind = state["habits_done"] < 0.6 * pace["avg_by_now"] if known and state["habits_total"] else done / total < 0.34
        return ("behind", "темп ниже обычного") if behind else (None, "темп в норме")
    if slot == "evening":
        if total == 0:
            return ("free_chat", "нет дел, чатовый день") if chat_day else (None, "нет дел, не чатовый день")
        if left == 0:
            return ("all_done", "всё выполнено, чатовый день") if chat_day else (None, "всё выполнено, не чатовый день")
        if done == 0:
            return "zero_done_evening", "вечер, ничего не выполнено"
        return ("one_left", "остался один пункт") if left == 1 else ("evening_left", "вечер, осталось несколько пунктов")
    # 'app': человек сам зашёл в чат — отвечаем на то, что происходит сейчас
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


SCENARIO_GUIDE = {
    "zero_done": "Сегодня пока НИЧЕГО не выполнено. Удивись (без упрёка), мягко спроси, всё ли в порядке, и предложи начать с самого простого из оставшегося.",
    "zero_done_evening": "День подходит к концу, а ничего не выполнено. Без упрёков и давления: скажи, что день ещё не потерян. Если серия больше нуля — скажи, что её сохранит даже одна выполненная привычка. Предложи самое простое дело.",
    "behind": "Человек идёт ниже своего обычного темпа. Отметь, что уже сделано, назови одно-два оставшихся дела и подтолкни к одному маленькому шагу прямо сейчас.",
    "one_left": "Остался ровно ОДИН пункт — назови его по имени. Подбодри: это последний шаг на сегодня.",
    "evening_left": "Вечер, осталось несколько дел. Коротко подведи итог (сделано X из Y), предложи закрыть одно — самое лёгкое — и можешь спросить, что мешает.",
    "all_done": "Всё на сегодня выполнено. Похвали конкретно и задай ОДИН открытый вопрос для рефлексии: что было самым интересным сегодня, что получилось лучше всего или чем планирует заняться вечером.",
    "free_chat": "Дел на сегодня нет. Задай ОДИН нешаблонный открытый вопрос про цели, энергию или планы (не «как дела»).",
    "morning": "Сейчас утро: вопросов НЕ задавай. Коротко отреагируй на уже сделанное (если есть) и спокойно назови, что стоит в плане дальше.",
}


def opener_style(user_id, day_iso, scenario):
    """Как начать: у каждого человека и дня своё, чтобы приветствия не повторялись. Стабильно в течение дня."""
    seed = random.Random(f"adam-opener:{user_id}:{day_iso}:{scenario}").random()
    if seed < 0.4:
        return "no_greeting"
    return "by_name" if seed < 0.75 else "short_greeting"


OPENER_GUIDE = {
    "no_greeting": "Без приветствия и без имени — сразу к делу.",
    "by_name": "Начни с обращения по имени.",
    "short_greeting": "Можно очень короткое приветствие, но без имени.",
}


def _count_phrase(n):
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return f"{n} дел"
    last = n % 10
    return f"{n} дело" if last == 1 else f"{n} дела" if 2 <= last <= 4 else f"{n} дел"


def build_checkin_prompt(state, scenario, opener, recent_messages=()):
    """Данные для модели. Привычки и задачи — вместе, но с разбивкой, чтобы текст мог назвать нужное по имени."""
    lines = ["Контекст:"]
    if state.get("language") == "en":
        lines.insert(0, "IMPORTANT: The user's interface language is English — write the message in English.")
    lines.append(f"Имя: {state['first_name'] or 'неизвестно'}")
    gender = {"m": "мужской", "f": "женский"}.get(state.get("gender"))
    lines.append(f"Пол: {gender}" if gender else "Пол: неизвестен — не используй формы прошедшего времени с родом (сделал/сделала), пиши нейтрально")
    lines.append(f"Время у человека: {state['time']} ({state['part']})")
    if state["total"]:
        lines.append(f"Дел на сегодня выполнено: {state['done']} из {state['total']} (привычки {state['habits_done']}/{state['habits_total']}, задачи {state['tasks_done']}/{state['tasks_total']})")
    else:
        lines.append("Привычек и задач на сегодня нет")
    if state["pending_habits"]:
        lines.append("Осталось из привычек: " + ", ".join(f"«{t}»" for t in state["pending_habits"][:5]))
    if state["pending_tasks"]:
        lines.append("Осталось из задач: " + ", ".join(f"«{t}»" for t in state["pending_tasks"][:5]))
    if state.get("main_goal"):
        lines.append(f"Главная задача дня: «{state['main_goal']}» — " + ("выполнена" if any(t["done"] and t.get("main") for t in state["tasks"]) else "не выполнена"))
    lines.append(f"Серия: {state['streak']} дн. подряд" + (" (под угрозой: сегодня ничего не отмечено)" if state["streak"] and state["done"] == 0 and state["habits_total"] else ""))
    pace = state.get("pace") or {}
    if pace.get("days", 0) >= PACE_MIN_DAYS:
        lines.append(f"Обычно к этому времени человек закрывает около {pace['avg_by_now']:.1f} привычки из {pace['avg_total']:.1f} за день")
    if state.get("mood_hint"):
        lines.append(f"Недавняя тема из разговора (мягко учти, не цитируй): {state['mood_hint']}")
    lines.append("")
    lines.append("Сценарий: " + SCENARIO_GUIDE[scenario])
    lines.append("Подача: " + OPENER_GUIDE[opener])
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
}


def fallback_text(state, scenario, user_id=None, day_iso=None):
    """Текст без модели. Вариант и подача (имя / без имени) — стабильно на (человек, день), на следующий день другие."""
    user_id = user_id if user_id is not None else state.get("user_id", 0)
    day_iso = day_iso or datetime.now().date().isoformat()
    key = scenario
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
    body = rng.choice(pool).format(
        p1=p1, done=state["done"], total=state["total"], left=state["left"],
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
    """Короткое, без шаблонных «Как дела?», не больше одного вопроса; утром вопросов нет вовсе."""
    if not text or len(text) > TEXT_MAX_CHARS:
        return False
    low = text.lower().replace("ё", "е")
    if any(phrase in low for phrase in BANNED_PHRASES):
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

def _lab_state(first_name, hour, minute, habits, tasks, streak=5, pace=None, gender="m", mood_hint="", style="neutral"):
    state = {
        "user_id": 0, "first_name": first_name, "gender": gender, "hour": hour, "minute": minute,
        "time": f"{hour:02d}:{minute:02d}", "part": part_of_day(hour), "weekday": 2,
        "habits": [{"title": t, "done": d} for t, d in habits],
        "tasks": [{"title": t, "done": d, "main": m} for t, d, m in tasks],
        "main_goal": next((t for t, _d, m in tasks if m), ""), "streak": streak, "mood_hint": mood_hint,
        "minutes_since_chat": None, "language": "ru", "style": style,
        "pace": pace or {"days": 9, "avg_by_now": 2.4, "avg_total": 4.2},
    }
    return fill_counts(state)


_LAB_HABITS = [("Зарядка", False), ("Чтение 20 минут", False), ("Холодный душ", False), ("Английский", False)]
_LAB_TASKS = [("Отправить предложение клиенту", False, True), ("Созвон с командой", False, False)]


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
    }


def lab_state_summary(state):
    """Короткая сводка состояния для экрана админки."""
    return {
        "time": state["time"], "done": state["done"], "total": state["total"], "streak": state["streak"],
        "pending": state["pending"][:6], "habits": f'{state["habits_done"]}/{state["habits_total"]}',
        "tasks": f'{state["tasks_done"]}/{state["tasks_total"]}', "mood_hint": state.get("mood_hint") or "",
        "pace": state.get("pace"),
    }
