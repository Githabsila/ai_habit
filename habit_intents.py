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
    add_daily_task,
    update_daily_plan_task,
    delete_daily_task,
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

# ===== ЗАДАЧИ ПЛАНА ДНЯ =====
# AI может управлять отдельными задачами плана так же, как привычками:
# добавить, изменить, удалить и отметить выполненной. Номер задачи тоже
# поддерживается существующим _COMPLETE_TASK_RE.

_ADD_PLAN_TASK_RE = re.compile(
    r"^(?:пожалуйста[,]?\s*)?"
    r"(?:добавь|добавить|создай|создать|поставь|поставить)\s+"
    r"(?:мне\s+)?(?:новую\s+)?задачу(?!\s+дня|\s+главную)\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_EDIT_PLAN_TASK_RE = re.compile(
    r"^(?:измени|изменить|переименуй|переименовать|замени|заменить)\s+"
    r"задачу(?!\s+дня|\s+главную)\s+(?:(\d+)\s+|«?(.+?)»?\s+)(?:на|в)\s+(.+)$",
    re.IGNORECASE,
)

_DELETE_PLAN_TASK_RE = re.compile(
    r"^(?:удали|удалить|убери|убрать|снеси|снести)\s+"
    r"задачу(?!\s+дня|\s+главную)\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_COMPLETE_PLAN_TASK_TEXT_RE = re.compile(
    r"^(?:отметь|выполни|сделай|заверши|закрой|сделал(?:а|и)?|выполнил(?:а|и)?)\s+"
    r"задачу(?!\s+дня|\s+главную)\s+(.+)$",
    re.IGNORECASE,
)

# ===== ГЛАВНАЯ ЗАДАЧА ДНЯ / ЦЕЛЬ ДНЯ =====
# Главная задача управляется AI-наставником так же, как обычная привычка:
# создать/поставить, изменить, удалить и отметить выполненной. "Главная
# привычка дня" тоже считается синонимом, чтобы AI не путал терминологию.

_MAIN_GOAL_SET_RE = re.compile(
    r"^(?:пожалуйста[,]?\s*)?"
    r"(?:добавь|добавить|создай|создать|поставь|поставить|задай|задать|"
    r"установи|установить)\s+"
    r"(?:мне\s+)?(?:новую\s+)?"
    r"(?:главную\s+(?:задачу|цель|привычку)(?:\s+(?:на|дня))?|"
    r"(?:задачу|цель)\s+дня)\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_MAIN_GOAL_EDIT_RE = re.compile(
    r"^(?:измени|изменить|переименуй|переименовать|замени|заменить)\s+"
    r"(?:главную\s+(?:задачу|цель|привычку)(?:\s+(?:на|дня))?|"
    r"(?:задачу|цель)\s+дня)\s+(?:на|в)\s+(.+)$",
    re.IGNORECASE,
)

_MAIN_GOAL_DELETE_RE = re.compile(
    r"^(?:удали|удалить|убери|убрать|снеси|снести)\s+"
    r"(?:главную\s+(?:задачу|цель|привычку)(?:\s+(?:на|дня))?|"
    r"(?:задачу|цель)\s+дня)[.!?]*$",
    re.IGNORECASE,
)

_MAIN_GOAL_COMPLETE_RE = re.compile(
    r"^(?:отметь|выполни|сделай|заверши|закрой|сделал(?:а|и)?|выполнил(?:а|и)?)\s+"
    r"(?:главную\s+(?:задачу|цель|привычку)(?:\s+(?:на|дня))?|"
    r"(?:задачу|цель)\s+дня)\s*(?:выполненн\w*)?[.!?]*$",
    re.IGNORECASE,
)

_MAIN_GOAL_LIST_RE = re.compile(
    r"^(?:какая|что)\s+(?:у\s+меня\s+)?(?:главная\s+(?:задача|цель|привычка)(?:\s+(?:на|дня))?|"
    r"(?:задача|цель)\s+дня)\??$",
    re.IGNORECASE,
)


def _find_plan_task(user_id: int, query: str):
    query_norm = _clean(query).lower()
    plan = get_daily_plan(user_id)
    tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]

    exact = [t for t in tasks if t["text"].strip().lower() == query_norm]
    if len(exact) == 1:
        return exact[0], None

    partial = [
        t for t in tasks
        if query_norm in t["text"].strip().lower()
        or t["text"].strip().lower() in query_norm
    ]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, partial
    return None, None


def _task_ambiguous_reply(tasks) -> str:
    names = ", ".join(f"«{t['text']}»" for t in tasks)
    return f"🤔 Нашлось несколько похожих задач: {names}. Уточни название точнее."


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
        title = _clean(m.group(1))
        if not title:
            return "⚠️ Напиши название привычки, например: «добавь привычку пить воду»."
        add_habit(user_id, title)
        return f"✅ Привычка «{title}» добавлена!"

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
                return f"🔥 Привычка «{habit['title']}» отмечена выполненной!{extra}"
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
                return f"🔥 Привычка «{habit['title']}» отмечена выполненной!{extra}"
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
            lines.append(f"{mark} {h['title']}")
        return "\n".join(lines)

    # ===== Управление задачами плана дня через AI =====
    m = _ADD_PLAN_TASK_RE.match(text)
    if m:
        task_text = _clean(m.group(1))
        if not task_text:
            return "⚠️ Напиши текст задачи, например: «добавь задачу подготовить презентацию»."
        try:
            add_daily_task(user_id, task_text)
        except ValueError as e:
            if str(e) == "task_limit":
                return "⚠️ В плане дня уже 5 задач — сначала удали или измени одну из них."
            raise
        return f"✅ Задача «{task_text}» добавлена в план дня."

    m = _EDIT_PLAN_TASK_RE.match(text)
    if m:
        number, old_query, new_text = m.group(1), m.group(2), _clean(m.group(3))
        plan = get_daily_plan(user_id)
        tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]
        if not tasks:
            return "⚠️ В плане дня пока нет задач."
        if number:
            idx = int(number)
            if idx < 1 or idx > len(tasks):
                return f"⚠️ В плане на сегодня только {len(tasks)} {_task_word(len(tasks))}."
            task = tasks[idx - 1]
            old = task["text"]
        else:
            task, ambiguous = _find_plan_task(user_id, old_query)
            if not task:
                if ambiguous:
                    return _task_ambiguous_reply(ambiguous)
                return f"⚠️ Не нашёл задачу «{old_query}» в плане дня."
            old = task["text"]
        if not new_text:
            return "⚠️ Напиши новое название задачи."
        update_daily_plan_task(user_id, task["id"], new_text)
        return f"✏️ Задача «{old}» изменена на «{new_text}»."

    m = _DELETE_PLAN_TASK_RE.match(text)
    if m:
        query = _clean(m.group(1))
        plan = get_daily_plan(user_id)
        tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]
        if query.isdigit():
            idx = int(query)
            if idx < 1 or idx > len(tasks):
                return f"⚠️ В плане на сегодня только {len(tasks)} {_task_word(len(tasks))}."
            task = tasks[idx - 1]
        else:
            task, ambiguous = _find_plan_task(user_id, query)
            if not task:
                if ambiguous:
                    return _task_ambiguous_reply(ambiguous)
                return f"⚠️ Не нашёл задачу «{query}» в плане дня."
        delete_daily_task(user_id, task["id"])
        return f"🗑 Задача «{task['text']}» удалена из плана дня."

    m = _COMPLETE_PLAN_TASK_TEXT_RE.match(text)
    if m and not m.group(1).strip().isdigit():
        query = _clean(m.group(1))
        task, ambiguous = _find_plan_task(user_id, query)
        if task:
            toggle_daily_task(task["id"])
            if task["completed"]:
                return f"↩️ Выполнение задачи «{task['text']}» отменено."
            return f"🔥 Задача «{task['text']}» отмечена выполненной!"
        if ambiguous:
            return _task_ambiguous_reply(ambiguous)
        return f"⚠️ Не нашёл задачу «{query}» в плане дня."

    # ===== Управление главной задачей дня через AI =====
    m = _MAIN_GOAL_SET_RE.match(text)
    if m:
        title = _clean(m.group(1))
        if not title:
            return "⚠️ Напиши, что должно быть главной задачей дня."
        set_daily_main_goal(user_id, title)
        return f"🎯 Главная задача дня установлена: «{title}»."

    m = _MAIN_GOAL_EDIT_RE.match(text)
    if m:
        title = _clean(m.group(1))
        plan = get_daily_plan(user_id)
        if not plan or not plan["main_goal"]:
            if title:
                set_daily_main_goal(user_id, title)
                return f"🎯 Главная задача дня установлена: «{title}»."
            return "⚠️ Главная задача дня пока не задана."
        if not title:
            return "⚠️ Напиши новое название главной задачи."
        old = plan["main_goal"]
        set_daily_main_goal(user_id, title)
        return f"✏️ Главная задача дня изменена: «{old}» → «{title}»."

    if _MAIN_GOAL_DELETE_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan or not plan["main_goal"]:
            return "ℹ️ Главная задача дня пока не задана."
        old = plan["main_goal"]
        delete_daily_main_goal(user_id)
        return f"🗑 Главная задача дня «{old}» удалена."

    if _MAIN_GOAL_COMPLETE_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan or not plan["main_goal"]:
            return "⚠️ На сегодня главная задача не задана."
        was_completed = bool(plan["main_goal_completed"])
        toggle_daily_main_goal(user_id)
        if was_completed:
            return f"↩️ Выполнение главной задачи «{plan['main_goal']}» отменено."
        return f"🔥 Главная задача «{plan['main_goal']}» отмечена выполненной!"

    if _MAIN_GOAL_LIST_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan or not plan["main_goal"]:
            return "ℹ️ На сегодня главная задача не задана."
        mark = "✅" if plan["main_goal_completed"] else "⬜"
        return f"{mark} Главная задача дня: {plan['main_goal']}"

    if _LIST_PLAN_RE.match(text):
        plan = get_daily_plan(user_id)
        if not plan or not (plan["main_goal"] or plan["tasks"]):
            return "На сегодня план ещё не составлен. Заполни его во вкладке «План дня» в приложении."
        lines = ["🎯 Твой план на сегодня:"]
        if plan["main_goal"]:
            goal_mark = "✅" if plan.get("main_goal_completed") else "⬜"
            lines.append(f"{goal_mark} Главная задача: {plan['main_goal']}")
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
    plan = get_daily_plan(user_id)
    plan_tasks = [t["text"] for t in (plan["tasks"] if plan else []) if t["text"]]
    main_goal = (plan["main_goal"] if plan else "") or ""

    # Даже если у пользователя нет обычных привычек, AI всё равно должен
    # уметь управлять главной задачей дня.
    if not habits and not plan_tasks and not main_goal:
        return None

    try:
        decision = await classify_habit_action(
            text,
            habits=[h["title"] for h in habits],
            plan_tasks=plan_tasks,
            main_goal=main_goal,
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
            return f"🔥 Привычка «{habit['title']}» отмечена выполненной!"
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
        add_habit(user_id, title)
        return f"✅ Привычка «{title}» добавлена!"

    if action == "add_task":
        title = _clean(decision.get("title") or "")
        if not title:
            return None
        try:
            add_daily_task(user_id, title)
        except ValueError as e:
            if str(e) == "task_limit":
                return "⚠️ В плане дня уже 5 задач — сначала удали одну из них."
            return None
        return f"✅ Задача «{title}» добавлена в план дня."

    if action in ("edit_task", "delete_task"):
        try:
            idx = int(decision.get("task_number"))
        except (TypeError, ValueError):
            return None
        tasks = [t for t in (plan["tasks"] if plan else []) if t["text"]]
        if idx < 1 or idx > len(tasks):
            return None
        task = tasks[idx - 1]
        if action == "delete_task":
            delete_daily_task(user_id, task["id"])
            return f"🗑 Задача «{task['text']}» удалена из плана дня."
        title = _clean(decision.get("title") or "")
        if not title:
            return None
        update_daily_plan_task(user_id, task["id"], title)
        return f"✏️ Задача «{task['text']}» изменена на «{title}»."

    if action in ("set_main_goal", "add_main_goal"):
        title = _clean(decision.get("title") or "")
        if not title:
            return None
        set_daily_main_goal(user_id, title)
        return f"🎯 Главная задача дня установлена: «{title}»."

    if action == "delete_main_goal":
        if not main_goal:
            return "ℹ️ Главная задача дня пока не задана."
        delete_daily_main_goal(user_id)
        return f"🗑 Главная задача дня «{main_goal}» удалена."

    if action == "complete_main_goal":
        if not main_goal:
            return "⚠️ На сегодня главная задача не задана."
        was_completed = bool(plan.get("main_goal_completed"))
        toggle_daily_main_goal(user_id)
        if was_completed:
            return f"↩️ Выполнение главной задачи «{main_goal}» отменено."
        return f"🔥 Главная задача «{main_goal}» отмечена выполненной!"

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
