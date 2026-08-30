import re

from db import (
    add_habit,
    get_habits,
    edit_habit,
    delete_habit,
    complete_habit,
    get_daily_plan,
    set_daily_main_goal,
    delete_daily_main_goal,
    toggle_daily_main_goal,
    toggle_daily_task,
    consume_completion_event,
)

# =====================================
# УПРАВЛЕНИЕ ПРИВЫЧКАМИ ЧЕРЕЗ AI-ЧАТ
# =====================================
#
# Если пользователь пишет AI-наставнику что-то вроде «добавь привычку
# читать книгу», это распознаётся здесь по паттернам ДО того, как
# сообщение уйдёт в тяжёлый мультиагентный пайплайн — команда выполняется
# напрямую (быстро, бесплатно, без риска, что модель что-то не так поймёт
# и придумает не то). Если сообщение не подходит ни под один паттерн,
# try_handle_habit_intent() возвращает None, и обработка идёт как раньше —
# через chat() / мультиагентную систему.

_ADD_RE = re.compile(
    r"^(?:пожалуйста[,]?\s*)?"
    r"(?:добавь|добавить|создай|создать|заведи|завести|поставь|поставить)\s+"
    r"(?:мне\s+)?(?:новую\s+)?привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_RENAME_RE = re.compile(
    r"^(?:переименуй|переименовать|измени|поменяй)\s+привычку\s+(.+?)\s+(?:на|в)\s+(.+)$",
    re.IGNORECASE,
)

_SET_TIME_RE = re.compile(
    r"^(?:поставь|назначь|перенеси|измени)\s+(?:время\s+)?(?:для\s+)?привычки\s+(.+?)\s+(?:на|в)\s+(\d{1,2}:\d{2})(?:\s*[—-]\s*(\d{1,2}:\d{2}))?[.!?]*$",
    re.IGNORECASE,
)

_CLEAR_TIME_RE = re.compile(
    r"^(?:убери|сними)\s+(?:время\s+с\s+)?(?:у\s+)?привычки\s+(.+?)[.!?]*$",
    re.IGNORECASE,
)

_DELETE_RE = re.compile(
    r"^(?:удали|удалить|убери|убрать|снеси|сноси)\s+привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_COMPLETE_RE = re.compile(
    r"^(?:отметь|выполни|выполнил[аи]?|сделай|сделал[аи]?)\s+привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_LIST_RE = re.compile(
    r"^(?:покажи|выведи|список)\s+(?:мои\s+)?привычки[.!?]*$"
    r"|^какие\s+у\s+меня\s+привычки[.!?]*$",
    re.IGNORECASE,
)

# Местоименные команды без явного названия привычки — «выполни её»,
# «удали это», «отметь то» и т.п. Обычно так пишут сразу после того, как
# привычка была упомянута/добавлена в этом же диалоге. Если у пользователя
# ровно ОДНА привычка — действие однозначно, выполняем сразу. Если их
# несколько — просим уточнить название явно (гадать по местоимению между
# несколькими привычками рискованно).
_COMPLETE_PRONOUN_RE = re.compile(
    r"^(?:отметь|выполни|сделай)\s+(?:её|ее|это|то)(?:\s+выполненн\w*)?[.!?]*$",
    re.IGNORECASE,
)

_DELETE_PRONOUN_RE = re.compile(
    r"^(?:удали|убери|сноси)\s+(?:её|ее|это|то)[.!?]*$",
    re.IGNORECASE,
)

# ===== ПЛАН ДНЯ / ЦЕЛИ (Главная задача + до 5 задач в Mini App) =====

_MAIN_GOAL_SET_RE = re.compile(
    r"^(?:поставь|поставить|добавь|добавить)\s+главн(?:ую|ая)\s+(?:задачу|цель|привычку)\s+дня\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_MAIN_GOAL_COMPLETE_RE = re.compile(
    r"^(?:отметь|выполни|сделай)\s+главн(?:ую|ая)\s+(?:задачу|цель)\s+дня[.!?]*$",
    re.IGNORECASE,
)

_MAIN_GOAL_DELETE_RE = re.compile(
    r"^(?:удали|удалить|убери|убрать)\s+главн(?:ую|ая)\s+(?:задачу|цель)\s+дня[.!?]*$",
    re.IGNORECASE,
)

_MAIN_GOAL_EDIT_RE = re.compile(
    r"^(?:измени|поменяй|переименуй)\s+главн(?:ую|ая)\s+(?:задачу|цель)\s+дня\s+(?:на|в)\s+(.+)$",
    re.IGNORECASE,
)


_LIST_PLAN_RE = re.compile(
    r"^(?:покажи|выведи)\s+(?:мой\s+|мои\s+)?(?:план(?:\s+на\s+(?:сегодня|день))?|цели(?:\s+на\s+(?:сегодня|день))?)[.!?]*$"
    r"|^какой\s+у\s+меня\s+план(?:\s+на\s+(?:сегодня|день))?[.!?]*$"
    r"|^какая\s+у\s+меня\s+главная\s+задача[.!?]*$",
    re.IGNORECASE,
)

_COMPLETE_TASK_RE = re.compile(
    r"^(?:отметь|выполни|сделай)\s+задачу\s*[:\-—]?\s*(\d+)\s*(?:в\s+плане)?[.!?]*$",
    re.IGNORECASE,
)


def _task_word(n: int) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "задач"
    last = n % 10
    if last == 1:
        return "задача"
    if 2 <= last <= 4:
        return "задачи"
    return "задач"


def _clean(text: str) -> str:
    return text.strip().strip(".,!?\"'«»").strip()


def _find_habit(user_id: int, query: str):
    """Ищет привычку пользователя по названию.

    Возвращает (habit, None) при однозначном совпадении,
    (None, [варианты]) если совпадений несколько,
    (None, None) если не нашлось ни одной.
    """
    query_norm = query.strip().lower()
    habits = get_habits(user_id)

    exact = [h for h in habits if h["title"].strip().lower() == query_norm]
    if len(exact) == 1:
        return exact[0], None

    partial = [
        h for h in habits
        if query_norm in h["title"].strip().lower()
        or h["title"].strip().lower() in query_norm
    ]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, partial

    return None, None


def _ambiguous_reply(habits) -> str:
    names = ", ".join(f"«{h['title']}»" for h in habits)
    return f"🤔 Нашлось несколько похожих привычек: {names}. Уточни название точнее."


def _not_found_reply(query: str) -> str:
    return f"⚠️ Не нашёл привычку «{query}». Проверь название в разделе «Привычки»."


def try_handle_habit_intent(user_id: int, text: str) -> str | None:
    """Пробует распознать команду по управлению привычками в сообщении AI-чата.

    Возвращает готовый текст ответа, если команда распознана и выполнена,
    либо None, если это обычное сообщение — тогда его нужно отправить в
    обычный AI-пайплайн (chat())."""

    text = (text or "").strip()
    if not text:
        return None

    m = _ADD_RE.match(text)
    if m:
        raw = _clean(m.group(1))
        if not raw:
            return "⚠️ Напиши название привычки, например: «добавь привычку пить воду в 19:00»."
        tm = re.search(r"(?:в|на)\s+(?:около\s+)?(\d{1,2}):(\d{2})\s*$", raw, re.IGNORECASE)
        planned_time = None
        title = raw
        if tm:
            hh, mm = int(tm.group(1)), int(tm.group(2))
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                planned_time = f"{hh:02d}:{mm:02d}"
                title = _clean(raw[:tm.start()])
        try:
            add_habit(user_id, title, planned_time=planned_time)
        except ValueError as exc:
            if str(exc) == "habit_limit":
                return "⚠️ Можно добавить не больше 7 привычек."
            if str(exc) == "habit_add_locked":
                return "⚠️ Сегодня уже была отметка и удаление привычки — добавление новых открыто с 00:00."
            raise
        return f"✅ Привычка «{title}» добавлена" + (f" на {planned_time}." if planned_time else "!")

    m = _RENAME_RE.match(text)
    if m:
        old_query, new_title = _clean(m.group(1)), _clean(m.group(2))
        if not new_title:
            return "⚠️ Не понял новое название. Напиши, например: «переименуй привычку бег в утренняя пробежка»."
        habit, ambiguous = _find_habit(user_id, old_query)
        if habit:
            edit_habit(habit["id"], new_title)
            return f"✅ Привычка «{habit['title']}» переименована в «{new_title}»."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(old_query)

    m = _SET_TIME_RE.match(text)
    if m:
        query = _clean(m.group(1))
        start = m.group(2)
        end = m.group(3)
        try:
            sh, sm = map(int, start.split(":"))
            if sh > 23 or sm > 59:
                raise ValueError
            planned_time = f"{sh:02d}:{sm:02d}"
            window = 60
            if end:
                eh, em = map(int, end.split(":"))
                if eh > 23 or em > 59:
                    raise ValueError
                window = (eh*60+em) - (sh*60+sm)
                if window <= 0: window += 24*60
        except ValueError:
            return "⚠️ Укажи корректное время, например 19:00 или 19:00–20:00."
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            edit_habit(habit["id"], habit["title"], planned_time=planned_time, time_window_minutes=window)
            return f"⏰ Для привычки «{habit['title']}» установлено время {planned_time}" + (f"–{end}." if end else ".")
        if ambiguous: return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    m = _CLEAR_TIME_RE.match(text)
    if m:
        query = _clean(m.group(1))
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            edit_habit(habit["id"], habit["title"], planned_time="", time_window_minutes=60)
            return f"⏰ Время для привычки «{habit['title']}» убрано."
        if ambiguous: return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    m = _DELETE_RE.match(text)
    if m:
        query = _clean(m.group(1))
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            delete_habit(habit["id"])
            return f"🗑 Привычка «{habit['title']}» удалена."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    m = _COMPLETE_RE.match(text)
    if m:
        query = _clean(m.group(1))
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            done = complete_habit(habit["id"])
            if done:
                event = consume_completion_event(user_id)
                extra = f"\n\n🔥 +1 день ударного режима!\n{event['message']}" if event else ""
                coin_line = f"⭐ +{done['coins']} Adam Coin" + (" (×2 — успей закрыть ещё одну!)" if done["doubled"] else "")
                return f"🔥 Привычка «{habit['title']}» отмечена выполненной!\n{coin_line}{extra}"
            return f"✅ Привычка «{habit['title']}» уже была отмечена выполненной."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    if _COMPLETE_PRONOUN_RE.match(text):
        habits = get_habits(user_id)
        if len(habits) == 1:
            habit = habits[0]
            done = complete_habit(habit["id"])
            if done:
                event = consume_completion_event(user_id)
                extra = f"\n\n🔥 +1 день ударного режима!\n{event['message']}" if event else ""
                coin_line = f"⭐ +{done['coins']} Adam Coin" + (" (×2 — успей закрыть ещё одну!)" if done["doubled"] else "")
                return f"🔥 Привычка «{habit['title']}» отмечена выполненной!\n{coin_line}{extra}"
            return f"✅ Привычка «{habit['title']}» уже была отмечена выполненной."
        if len(habits) > 1:
            return "🤔 У тебя несколько привычек — уточни, какую из них: напиши «выполни привычку <название>»."
        return "У тебя пока нет ни одной привычки."

    if _DELETE_PRONOUN_RE.match(text):
        habits = get_habits(user_id)
        if len(habits) == 1:
            habit = habits[0]
            delete_habit(habit["id"])
            return f"🗑 Привычка «{habit['title']}» удалена."
        if len(habits) > 1:
            return "🤔 У тебя несколько привычек — уточни, какую из них: напиши «удали привычку <название>»."
        return "У тебя пока нет ни одной привычки."

    if _LIST_RE.match(text):
        habits = get_habits(user_id)
        if not habits:
            return "У тебя пока нет ни одной привычки. Напиши «добавь привычку …», чтобы завести первую."
        lines = ["📋 Твои привычки:"]
        for h in habits:
            mark = "✅" if h["completed"] else "⬜"
            lines.append(f"{mark} {h['title']}" + (f" — {h['planned_time']}" if h['planned_time'] else ""))
        return "\n".join(lines)

    m = _MAIN_GOAL_SET_RE.match(text)
    if m:
        goal = _clean(m.group(1))
        if not goal:
            return "⚠️ Напиши текст главной задачи дня."
        set_daily_main_goal(user_id, goal)
        return f"🎯 Главная задача дня: «{goal}»"

    m = _MAIN_GOAL_EDIT_RE.match(text)
    if m:
        goal = _clean(m.group(1))
        if not goal:
            return "⚠️ Напиши новое название главной задачи."
        set_daily_main_goal(user_id, goal)
        return f"✏️ Главная задача дня изменена на «{goal}»."

    if _MAIN_GOAL_COMPLETE_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan["main_goal"]:
            return "⚠️ Главная задача дня пока не задана."
        if plan["main_goal_completed"]:
            return f"✅ Главная задача «{plan['main_goal']}» уже отмечена выполненной."
        toggle_daily_main_goal(user_id)
        return f"🔥 Главная задача «{plan['main_goal']}» отмечена выполненной!"

    if _MAIN_GOAL_DELETE_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan["main_goal"]:
            return "ℹ️ Главная задача дня уже пуста."
        delete_daily_main_goal(user_id)
        return "🗑 Главная задача дня удалена."

    if _LIST_PLAN_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan or not (plan["main_goal"] or plan["tasks"]):
            return "На сегодня план ещё не составлен. Заполни его во вкладке «План дня» в приложении."
        lines = ["🎯 Твой план на сегодня:"]
        if plan["main_goal"]:
            lines.append(f"Главная задача: {plan['main_goal']}")
        if plan["tasks"]:
            lines.append("")
            for i, t in enumerate(plan["tasks"], start=1):
                if not t["text"]:
                    continue
                mark = "✅" if t["completed"] else "⬜"
                lines.append(f"{mark} {i}. {t['text']}")
        return "\n".join(lines)

    m = _COMPLETE_TASK_RE.match(text)
    if m:
        idx = int(m.group(1))
        plan = get_daily_plan(user_id)
        tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]
        if not tasks:
            return "В сегодняшнем плане пока нет задач. Заполни его во вкладке «План дня»."
        if idx < 1 or idx > len(tasks):
            return f"⚠️ В плане на сегодня только {len(tasks)} {_task_word(len(tasks))} — укажи номер от 1 до {len(tasks)}."
        task = tasks[idx - 1]
        toggle_daily_task(task["id"])
        new_state = not task["completed"]
        if new_state:
            return f"🔥 Задача «{task['text']}» отмечена выполненной!"
        return f"↩️ Отметка выполнения снята с задачи «{task['text']}»."

    return None


# =====================================
# РЕЗЕРВНОЕ РАСПОЗНАВАНИЕ ЧЕРЕЗ AI-КЛАССИФИКАТОР
# =====================================
#
# Жёсткие regex-шаблоны выше ловят только явные, предсказуемые формулировки
# («выполни привычку X», «удали привычку X»). Если человек пишет иначе —
# «я сделал зарядку», «забей на привычку с чтением», «готово, я
# пробежался» — ни один regex не совпадёт, и БЕЗ этой функции сообщение
# ушло бы в обычный AI-чат, который может РАЗГОВОРНО подтвердить действие
# ("Привычка отмечена выполненной!"), ничего на самом деле не изменив в
# базе — у обычного чата нет доступа к БД. Эта функция вызывается ПОСЛЕ
# try_handle_habit_intent() (только если та вернула None) и использует
# лёгкую модель-классификатор, которая видит реальный список привычек/задач
# пользователя и выбирает цель строго из него — не выдумывая.

async def try_handle_habit_intent_ai(user_id: int, text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None

    from multi_agent import classify_habit_action

    habits = get_habits(user_id)
    if not habits:
        # Без привычек единственное осмысленное действие классификатора —
        # add_habit, но для этого он не нужен: сообщения вида "добавь
        # привычку X" и так ловятся regex-шаблоном выше. Не тратим вызов
        # модели впустую.
        return None

    plan = get_daily_plan(user_id)
    plan_tasks = [t["text"] for t in (plan["tasks"] if plan else []) if t["text"]]

    try:
        decision = await classify_habit_action(
            text,
            habits=[h["title"] for h in habits],
            plan_tasks=plan_tasks,
        )
    except Exception:
        return None

    action = decision.get("action")

    if action == "complete_habit":
        target = (decision.get("habit") or "").strip().lower()
        habit = next((h for h in habits if h["title"].strip().lower() == target), None)
        if not habit:
            return None
        done = complete_habit(habit["id"])
        if done:
            coin_line = f"⭐ +{done['coins']} Adam Coin" + (" (×2 — успей закрыть ещё одну!)" if done["doubled"] else "")
            return f"🔥 Привычка «{habit['title']}» отмечена выполненной!\n{coin_line}"
        return f"✅ Привычка «{habit['title']}» уже была отмечена выполненной."

    if action == "delete_habit":
        target = (decision.get("habit") or "").strip().lower()
        habit = next((h for h in habits if h["title"].strip().lower() == target), None)
        if not habit:
            return None
        delete_habit(habit["id"])
        return f"🗑 Привычка «{habit['title']}» удалена."

    if action == "add_habit":
        title = _clean(decision.get("title") or "")
        if not title:
            return None
        try:
            add_habit(user_id, title)
        except ValueError as exc:
            if str(exc) == "habit_limit":
                return "⚠️ Можно добавить не больше 7 привычек."
            if str(exc) == "habit_add_locked":
                return "⚠️ Сегодня уже была отметка и удаление привычки — добавление новых открыто с 00:00."
            raise
        return f"✅ Привычка «{title}» добавлена!"

    if action == "complete_task":
        try:
            idx = int(decision.get("task_number"))
        except (TypeError, ValueError):
            return None
        tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]
        if idx < 1 or idx > len(tasks):
            return None
        task = tasks[idx - 1]
        toggle_daily_task(task["id"])
        new_state = not task["completed"]
        if new_state:
            return f"🔥 Задача «{task['text']}» отмечена выполненной!"
        return f"↩️ Отметка выполнения снята с задачи «{task['text']}»."

    return None
