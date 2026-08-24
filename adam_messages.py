"""
Единый источник текстов проактивного AI-наставника ADAM.

Правила:
- обращение только на «ты»;
- никаких «AI ADAM следит за тобой» — наставник говорит от первого лица;
- один осмысленный эмодзи в конце вместо случайных медалек;
- формулировки меняются, но остаются короткими и естественными.
"""
import random

# Эмодзи разделены по смыслу: медали/награды больше не попадают в обычные
# напоминания случайно.
MOTIVATION_EMOJIS = ["💪", "🚀", "⚡️", "🔥", "⏰️", "✊️"]
TASK_EMOJIS = ["❗️", "⚡️", "🚀", "⏰️"]
SOFT_EMOJIS = ["👌", "⏱️", "💪", "🚀", "⚡️", "⏳️"]


def random_emoji(pool=MOTIVATION_EMOJIS) -> str:
    return random.choice(pool)


def pick(templates: list[str], pool=MOTIVATION_EMOJIS, **kwargs) -> str:
    template = random.choice(templates)
    return template.format(emoji=random_emoji(pool), **kwargs)


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение после числительных: 1 задача, 2 задачи, 5 задач."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def task_word(n: int) -> str:
    return plural_ru(n, "задача", "задачи", "задач")


def day_word(n: int) -> str:
    return plural_ru(n, "день", "дня", "дней")


def streak_phrase(n: int) -> str:
    return f"{int(n)} {day_word(n)}"


# =====================================
# 12:00 — ЕДИНАЯ ТОЧКА ПО ПРИВЫЧКАМ
# =====================================

HABIT_NOON_TEMPLATES = [
    "На контрольной точке вижу: {verb} {habit_word}: {left}. {habits}. {action} {emoji}",
    "Проверка курса: {verb} {habit_word}: {left}. {habits}. {action} {emoji}",
    "Контрольная точка: {verb} {habit_word}: {left}. {habits}. {action} {emoji}",
]


def format_habit_noon_message(incomplete_habits) -> str:
    titles = [str(h["title"]) for h in incomplete_habits]
    left = len(titles)
    habits = ", ".join(f"«{t}»" for t in titles)
    habit_word = plural_ru(left, "привычка", "привычки", "привычек")
    verb = "Неотмечена" if left == 1 else "Неотмечены"
    action = "Выполни её, когда будет удобно" if left == 1 else "Выполни их, когда будет удобно"

    return pick(
        HABIT_NOON_TEMPLATES,
        pool=MOTIVATION_EMOJIS,
        left=left,
        habit_word=habit_word,
        habits=habits,
        verb=verb,
        action=action,
    )


# Совместимость со старыми вызовами. Теперь это не «утренний» текст и не
# утверждает, что привычку нужно сделать с утра.
HABIT_REMINDER_TEMPLATES = [
    "Привычка «{title}» ещё не отмечена. Вернись к ней, когда наступит подходящее время {emoji}",
    "«{title}» пока остаётся открытой. Если её время ещё не пришло — просто держи её в уме {emoji}",
    "Я вижу, что «{title}» ещё не закрыта. Выбери удобный момент и доведи её до конца {emoji}",
]


def format_habit_reminder_message(title: str) -> str:
    return pick(HABIT_REMINDER_TEMPLATES, pool=SOFT_EMOJIS, title=title)


HABIT_FINAL_MOTIVATION_TEMPLATES = [
    "Одна следующая привычка — и ты уже двигаешь день вперёд {emoji}",
    "Не пытайся закрыть всё одним рывком. Просто выбери следующий шаг {emoji}",
    "Держи курс: маленькие закрытые действия сегодня складываются в большую серию {emoji}",
]


def format_habit_final_motivation_message() -> str:
    return pick(HABIT_FINAL_MOTIVATION_TEMPLATES)


PLAN_TASK_REMINDER_TEMPLATES = [
    "Задача «{title}» ещё открыта. Если она важна сегодня — самое время вернуться к ней {emoji}",
    "«{title}» пока не закрыта. Выдели ей немного времени и доведи до конца {emoji}",
    "Я бы сейчас вернулся к задаче «{title}» — она всё ещё ждёт тебя {emoji}",
]


def format_plan_task_reminder_message(title: str) -> str:
    return pick(PLAN_TASK_REMINDER_TEMPLATES, pool=TASK_EMOJIS, title=title)


# =====================================
# ПЛАН ДНЯ — 15:00
# =====================================

DAY_PROGRESS_TEMPLATES = [
    "Сейчас {done} из {total} {task_word} уже закрыто. Осталось {left} {left_word} — ещё есть время спокойно добить главное {emoji}",
    "Половина дня позади. {left} {left_word} из плана ещё ждут тебя. Выбери одну и вернись в ритм {emoji}",
    "Я бы сейчас не распылялся: {left} {left_word} ещё открыты. Закрой следующую — остальное станет проще {emoji}",
    "Проверил твой план: готово {done} из {total} {task_word}. Осталось {left} {left_word} — время ещё на твоей стороне {emoji}",
]


def format_day_progress_message(done: int, total: int) -> str:
    left = total - done
    return pick(
        DAY_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        done=done,
        total=total,
        left=left,
        task_word=task_word(total),
        left_word=task_word(left),
    )


# =====================================
# ПЛАН ДНЯ — 20:00
# =====================================

EVENING_PROGRESS_TEMPLATES = [
    "Вечерняя сверка: осталось {left} {left_word} из плана. Если закроешь хотя бы следующую сейчас — день уже будет ощущаться иначе {emoji}",
    "День подходит к концу. У тебя ещё {left} {left_word}: {task_list}. Реши, что действительно важно закрыть сегодня {emoji}",
    "Я вижу {left} {left_word}, которые ещё открыты. Не гонись за идеальностью — выбери приоритет и закончи его {emoji}",
    "Финишная проверка: {left} {left_word} ещё ждут тебя. Если они важны сегодня — самое время вернуться к ним {emoji}",
]


def format_evening_progress_message(done: int, total: int, task_list=None) -> str:
    left = total - done
    names = task_list or []
    listed = ", ".join(f"«{x}»" for x in names[:3])
    if len(names) > 3:
        listed += f" и ещё {len(names)-3}"
    return pick(
        EVENING_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        left=left,
        left_word=task_word(left),
        task_list=listed or "главное из плана",
    )


ALL_TASKS_DONE_TEMPLATES = [
    "Ты закрыл все задачи дня. Чисто. Теперь можно выдохнуть и не тащить незакрытые дела в вечер {emoji}",
    "План дня закрыт полностью. Хорошая работа — сегодня ты довёл начатое до конца {emoji}",
    "Все задачи готовы. Я бы на твоём месте сейчас просто спокойно отдохнул — день ты уже сделал {emoji}",
    "Финиш есть: все задачи выполнены. Заслуженный отдых без чувства, что что-то висит {emoji}",
]


def format_all_tasks_done_message() -> str:
    return pick(ALL_TASKS_DONE_TEMPLATES)


# =====================================
# ГЛАВНАЯ ЦЕЛЬ
# =====================================

GOAL_REMINDER_TEMPLATES = [
    "По цели «{goal}» пока нет движения. Сделай сегодня хотя бы один небольшой шаг {emoji}",
    "Я бы не откладывал цель «{goal}» до вечера. Даже 10 минут уже считаются движением {emoji}",
    "Цель «{goal}» ещё ждёт первого шага сегодня. Не обязательно делать много — начни с малого {emoji}",
]


def format_goal_reminder_message(goal: str) -> str:
    return pick(GOAL_REMINDER_TEMPLATES, pool=TASK_EMOJIS, goal=goal)


MAIN_GOAL_DONE_TEMPLATES = [
    "Главная задача закрыта. Отлично — теперь можно переключиться на остальное без этого груза {emoji}",
    "Ты выполнил главную задачу дня. Хороший ход — самое важное уже позади {emoji}",
    "Главная цель сегодня закрыта. Так и строится нормальный темп: главное сначала {emoji}",
]


def format_main_goal_done_message() -> str:
    return pick(MAIN_GOAL_DONE_TEMPLATES)


# =====================================
# НЕДЕЛЯ / МЕСЯЦ
# =====================================

WEEK_START_TEMPLATES = [
    "Новая неделя. Не пытайся изменить всё сразу — выбери несколько вещей и держи их стабильно {emoji}",
    "Понедельник — это просто новая точка старта. Давай сегодня заложим хороший темп {emoji}",
]
WEEK_END_TEMPLATES = [
    "Неделя подходит к концу. Оглянись на результат и добей то, что действительно важно {emoji}",
    "Финиш недели близко. Не нужен идеальный рывок — нужен ещё один хороший шаг {emoji}",
]
MONTH_START_TEMPLATES = [
    "Новый месяц — хороший момент выбрать один ритм, который ты реально сможешь удержать {emoji}",
    "Месяц только начался. Не разгоняйся слишком резко — лучше стабильность, чем быстрый срыв {emoji}",
]
MONTH_END_TEMPLATES = [
    "Месяц почти закончился. Посмотри, что получилось, и спокойно закрой последний важный кусок {emoji}",
    "Финиш месяца. Забери из него результат, а не только усталость — доведи главное до конца {emoji}",
]


def format_week_start_message() -> str:
    return pick(WEEK_START_TEMPLATES)


def format_week_end_message() -> str:
    return pick(WEEK_END_TEMPLATES)


def format_month_start_message() -> str:
    return pick(MONTH_START_TEMPLATES)


def format_month_end_message() -> str:
    return pick(MONTH_END_TEMPLATES)


MORNING_GREETING_TEMPLATES = [
    "Новый день начался. Не спеши хвататься за всё сразу — выбери первый важный шаг {emoji}",
    "Сегодня не нужен идеальный старт. Нужен первый сделанный шаг — дальше будет легче {emoji}",
    "Начинаем новый день. Держи фокус на главном и не распыляйся {emoji}",
]


def format_morning_greeting_message() -> str:
    return pick(MORNING_GREETING_TEMPLATES)
