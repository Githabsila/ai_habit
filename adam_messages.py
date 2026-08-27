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

HABIT_CHECKPOINT_10_TEMPLATES = [
    "⏱️ Контрольная точка дня: {status_phrase}: {habits}. Посмотри, что хочешь сделать в первой половине дня {emoji}",
    "🎯 10:00 — сверка курса. {verb_cap} {habit_word}: {habits}. Дальше просто двигайся по одной привычке, без спешки {emoji}",
    "☀️ Проверка на 10:00: осталось {left} {habit_word} — {habits}. Если часть уже сделал, отлично: сосредоточься только на оставшемся {emoji}",
    "📍 Точка дня: {left} {habit_word} пока открыты — {habits}. Выбери следующую и продолжай свой темп {emoji}",
    "⚡️ Сейчас вижу {left} {habit_word}: {habits}. Хороший момент определить ближайший шаг и закрыть его {emoji}",
]

def format_habit_checkpoint_10_message(incomplete_habits) -> str:
    titles = [str(h["title"]) for h in incomplete_habits]
    left = len(titles)
    habits = ", ".join(f"«{t}»" for t in titles)
    habit_word = plural_ru(left, "привычка", "привычки", "привычек")
    status_phrase = (
        f"осталась {left} {habit_word}" if left == 1
        else f"остались {left} {habit_word}"
    )
    return pick(
        HABIT_CHECKPOINT_10_TEMPLATES,
        pool=SOFT_EMOJIS,
        left=left,
        habit_word=habit_word,
        habits=habits,
        status_phrase=status_phrase,
    )


# Совместимость со старыми вызовами. Теперь это не «утренний» текст и не
# утверждает, что привычку нужно сделать с утра.
HABIT_REMINDER_TEMPLATES = [
    "Привычка «{title}» ещё не отмечена. Если её время уже пришло — самое время закрыть её {emoji}",
    "«{title}» пока открыта. Найди для неё несколько минут и доведи до конца {emoji}",
    "Я вижу, что «{title}» ещё ждёт отметки. Сделай её, когда будет удобный момент {emoji}",
    "Проверка по привычке «{title}»: пока без отметки. Один небольшой шаг — и она закрыта {emoji}",
    "«{title}» ещё в списке на сегодня. Не обязательно спешить — просто не потеряй её из фокуса {emoji}",
    "На сегодня осталась привычка «{title}». Если можешь — закрой её сейчас и освободи голову {emoji}",
]


def format_habit_reminder_message(title: str, template_index=None) -> str:
    if template_index is None:
        return pick(HABIT_REMINDER_TEMPLATES, pool=SOFT_EMOJIS, title=title)
    template = HABIT_REMINDER_TEMPLATES[template_index % len(HABIT_REMINDER_TEMPLATES)]
    return template.format(title=title, emoji=random_emoji(SOFT_EMOJIS))


def format_habit_reminder_messages(titles) -> list[str]:
    """Отдельное сообщение на каждую привычку без повторения шаблона
    внутри одной рассылки."""
    titles = [str(t) for t in titles]
    if not titles:
        return []
    order = list(range(len(HABIT_REMINDER_TEMPLATES)))
    random.shuffle(order)
    return [
        format_habit_reminder_message(title, order[i % len(order)])
        for i, title in enumerate(titles)
    ]


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
# ПЛАН ДНЯ — 19:00
# =====================================

DAY_PROGRESS_TEMPLATES = [
    "Вечерняя сверка: готово {done} из {total} {total_gen}. Осталось {left} {left_word}: {open_items}. Ещё можно спокойно закрыть главное {emoji}",
    "Проверил весь план: выполнено {done} из {total} {total_gen}, включая главную задачу. Осталось {left} {left_word}: {open_items} {emoji}",
    "19:00 — время свериться с планом. Закрыто {done} из {total} {total_gen}. {left_phrase}: {open_items}. Выбери следующий пункт {emoji}",
    "До финиша осталось {left} {left_word} из всего плана — {open_items}. Сначала закрой то, что важнее всего {emoji}",
]


def task_genitive(n: int) -> str:
    """Форма после «из»: из 1 задачи, из 2 задач, из 5 задач."""
    return "задачи" if abs(int(n)) == 1 else "задач"


def format_day_progress_message(done: int, total: int, open_items=None) -> str:
    left = max(0, total - done)
    open_items = open_items or []
    listed = ", ".join(open_items[:6])
    if len(open_items) > 6:
        listed += f" и ещё {len(open_items) - 6}"

    left_phrase = (
        f"Осталась {left} {task_word(left)}" if left == 1
        else f"Остались {left} {task_word(left)}"
    )

    return pick(
        DAY_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        done=done,
        total=total,
        left=left,
        total_gen=task_genitive(total),
        left_word=task_word(left),
        left_phrase=left_phrase,
        open_items=listed,
    )


# =====================================
# LEGACY: старый вечерний формат (не используется планировщиком)
# =====================================

EVENING_PROGRESS_TEMPLATES = [
    "Вечерняя сверка: {left_verb} {left} {left_word}, {relative}. {task_list}. Если это важно сегодня — закрой приоритет и спокойно заверши день {emoji}",
    "Финишная проверка: {left} {left_word}, {relative}: {task_list}. Не распыляйся — выбери главное и доведи до конца {emoji}",
    "День подходит к концу. В плане ещё {left} {left_word}, {relative}: {task_list}. Посмотри, что можешь закрыть сегодня {emoji}",
    "Проверил весь план, включая главную задачу: {left} {left_word}, {relative}. {task_list}. Ещё есть время закончить главное {emoji}",
]


def format_evening_progress_message(done: int, total: int, task_list=None, main_goal=None) -> str:
    # done/total относятся к обычным задачам плана. Для вечернего сообщения
    # считаем ВСЕ открытые пункты вместе с главной задачей, чтобы ADAM не
    # терял её и не отправлял два раздельных уведомления.
    names = []
    if main_goal:
        names.append(f"главная: «{main_goal}»")
    names.extend(f"«{x}»" for x in (task_list or []))

    left = len(names)
    if left == 0:
        return format_all_tasks_done_message()

    left_word = task_word(left)
    left_verb = "Осталась" if left == 1 else "Остались"
    relative = "которая ещё открыта" if left == 1 else "которые ещё открыты"
    listed = ", ".join(names[:6])
    if len(names) > 6:
        listed += f" и ещё {len(names) - 6}"

    return pick(
        EVENING_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        left=left,
        left_word=left_word,
        left_verb=left_verb,
        relative=relative,
        task_list=listed,
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
