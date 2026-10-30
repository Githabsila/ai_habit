"""
Единый источник текстов проактивного AI-наставника ADAM.

Правила:
- обращение только на «ты»;
- никаких «AI ADAM следит за тобой» — наставник говорит от первого лица;
- один осмысленный эмодзи в конце вместо случайных медалек;
- формулировки меняются, но остаются короткими и естественными.
"""
import html
import random
import re

# Эмодзи разделены по смыслу: медали/награды больше не попадают в обычные
# напоминания случайно.
MOTIVATION_EMOJIS = ["💪", "🚀", "⚡️", "🔥", "⏰️", "✊️"]
TASK_EMOJIS = ["❗️", "⚡️", "🚀", "⏰️"]
SOFT_EMOJIS = ["👌", "⏱️", "💪", "🚀", "⚡️", "⏳️"]


def random_emoji(pool=MOTIVATION_EMOJIS) -> str:
    return random.choice(pool)


def pick(templates: list[str], pool=MOTIVATION_EMOJIS, **kwargs) -> str:
    template = random.choice(templates)
    # пустая вставка (например, нет списка оставшихся задач) не должна оставлять двойной пробел
    return re.sub(r"[ 	]{2,}", " ", template.format(emoji=random_emoji(pool), **kwargs)).strip()


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


def habit_word(n: int) -> str:
    return plural_ru(n, "привычка", "привычки", "привычек")


def diamond_word(n: int) -> str:
    return plural_ru(n, "алмаз", "алмаза", "алмазов")


def _genitive_after_iz(n: int, one: str, many: str) -> str:
    """Форма после «из»: из 1 задачи, из 2 задач, из 21 задачи, из 12 задач."""
    n = abs(int(n))
    return one if n % 10 == 1 and n % 100 != 11 else many


def habit_genitive(n: int) -> str:
    return _genitive_after_iz(n, "привычки", "привычек")


def left_phrase_tasks(left: int) -> str:
    """«Осталась одна задача» / «Осталось 2 задачи» / «Осталось 5 задач»."""
    return "Осталась одна задача" if int(left) == 1 else f"Осталось {int(left)} {task_word(left)}"


def day_word(n: int) -> str:
    return plural_ru(n, "день", "дня", "дней")


def streak_phrase(n: int) -> str:
    return f"{int(n)} {day_word(n)}"


# =====================================
# 12:00 — ЕДИНАЯ ТОЧКА ПО ПРИВЫЧКАМ
# =====================================

HABIT_CHECKPOINT_TEMPLATES = [
    "⏱️ Контрольная точка дня: уже закрыто {completed} из {total}. Сейчас остаётся {left_phrase}: {habits}.{timed_note} Начни с ближайшей — и продолжай двигаться по плану {emoji}",
    "🎯 {time}:00 — сверка курса. Уже выполнено {completed} из {total}. В фокусе сейчас {left_phrase}: {habits}.{timed_note} Закрой следующий пункт и продолжай день {emoji}",
    "☀️ На {time}:00 вижу реальный прогресс: {completed} из {total} уже выполнено. Остаётся {left_phrase}: {habits}.{timed_note} Следующий шаг — закрыть один из этих пунктов {emoji}",
    "📍 Точка дня: {completed} из {total} уже закрыто. Сейчас в плане {left_phrase}: {habits}.{timed_note} Двигайся дальше с ближайшего пункта {emoji}",
    "⚡️ Уже есть результат: {completed} из {total} {total_gen} выполнено. Остаётся {left_phrase}: {habits}.{timed_note} Закрой следующую и сохрани темп {emoji}",
]

# Когда completed == 0, формулировки выше звучат как противоречие ("уже
# закрыто 0 из 6", "уже есть результат: 0 из 6") — отдельный пул без
# рамки "уже что-то сделано" для случая, когда за день пока ничего не
# отмечено.
HABIT_CHECKPOINT_ZERO_TEMPLATES = [
    "⏱️ Контрольная точка дня: из {total} пока не закрыто ни одной. В плане {left_phrase}: {habits}.{timed_note} Начни с ближайшей — и сдвинь день с места {emoji}",
    "🎯 {time}:00 — сверка курса. Пока 0 из {total}. В фокусе {left_phrase}: {habits}.{timed_note} Закрой первый пункт — и наберёшь темп {emoji}",
    "📍 Точка дня: из {total} пока ничего не отмечено. В плане {left_phrase}: {habits}.{timed_note} Начни с ближайшего пункта {emoji}",
]


def _format_timed_note(timed_habits) -> str:
    """timed_habits — список (hour, minute, title) привычек со своим ещё не
    наступившим временем, отсортированный по времени по возрастанию.
    Возвращает готовую вставку вида
    " Не забудь в 18:00 — «Спорт» и в 23:00 — «Растяжка»." — как
    дополнительную информацию в конце точки дня, перед мотивационной
    концовкой шаблона. Пусто, если таких привычек нет."""
    if not timed_habits:
        return ""
    parts = [f"в {int(h):02d}:{int(m):02d} — «{title}»" for h, m, title in timed_habits]
    return " Не забудь " + " и ".join(parts) + "."


def format_habit_checkpoint_message(incomplete_habits, hour: int, completed: int | None = None, total: int | None = None, timed_habits=None) -> str:
    titles = [str(h["title"]) for h in incomplete_habits]
    left = len(titles)
    if total is None:
        total = left + max(0, int(completed or 0))
    if completed is None:
        completed = max(0, int(total) - left)
    completed = max(0, int(completed))
    total = max(left, int(total))
    habits = ", ".join(f"«{t}»" for t in titles)
    left_word = plural_ru(left, "привычка", "привычки", "привычек")
    left_phrase = f"одна {left_word}" if left == 1 else f"{left} {left_word}"
    templates = HABIT_CHECKPOINT_TEMPLATES if completed > 0 else HABIT_CHECKPOINT_ZERO_TEMPLATES
    timed_note = _format_timed_note(timed_habits)
    return pick(templates, pool=SOFT_EMOJIS, time=hour,
                completed=completed, total=total, total_gen=habit_genitive(total), left=left, habits=habits,
                left_phrase=left_phrase, timed_note=timed_note)


# =====================================
# ТОЧКА ДНЯ (10/12/17/22): сообщение собирается из частей и не повторяет недавние
# =====================================
# Раньше на каждый час выбирался один из 3–5 готовых текстов наугад: в 12:00, 17:00 и 22:00 приходило почти одно и то же
# («сверка курса. Пока 0 из 1 …»), а у человека с ОДНОЙ привычкой — ещё и «0 из 1». Теперь текст = начало (под время дня) +
# счёт (его нет, если привычка одна) + что в фокусе + «не забудь» по таймерным + призыв. Каждая часть берётся из своего набора,
# и та, что была в трёх последних точках этого человека, не повторяется (compose_checkpoint_message(recent_picks=…)).
# Сообщение без родовых форм («закрыл/закрыла»): пол человека неизвестен.

CHECKPOINT_OPENERS = {
    10: (
        "☀️ Утро — удобное время начать.", "⏱️ Контрольная точка утра.", "📍 Точка дня, утро.", "🌤️ Самое начало дня.",
        "🎯 {time}:00 — первая сверка.",
    ),
    12: (
        "🎯 {time}:00 — сверка курса.", "☀️ Полдень — день разворачивается.", "📍 Точка дня.", "⏱️ Середина дня — самое время свериться.",
        "🧭 Короткая сверка в полдень.",
    ),
    17: (
        "🌇 Вечер уже близко.", "🎯 {time}:00 — вечерняя сверка.", "📍 Точка дня: вечер на подходе.", "⏱️ До конца дня ещё есть время.",
        "🧭 Сверка перед вечером.",
    ),
    22: (
        "🌙 День почти закончился.", "🎯 {time}:00 — последняя сверка дня.", "⏳ Остались последние часы дня.", "📍 Точка дня: поздний вечер.",
        "🕙 Вечер на исходе.",
    ),
}
CHECKPOINT_OPENERS_DEFAULT = ("📍 Точка дня.", "🎯 {time}:00 — сверка курса.", "⏱️ Короткая сверка.", "🧭 Свериться самое время.")
# Уже напоминали сегодня, а отметок так и нет: не повторяем то же самое, а говорим, что картина прежняя.
CHECKPOINT_OPENERS_AGAIN = (
    "🔁 С прошлой сверки ничего не изменилось.", "📍 Новая точка дня — картина прежняя.", "⏱️ Напомню ещё раз, коротко.",
    "🎯 {time}:00 — возвращаюсь к плану дня.", "🧭 Всё ещё без отметок.",
)

CHECKPOINT_STATUS_ZERO = (
    "Пока 0 из {total}.", "Из {total} пока ни одной отметки.", "Отметок за сегодня пока нет.", "Ни одна из {total} пока не закрыта.", "",
)
CHECKPOINT_STATUS_PROGRESS = (
    "Уже закрыто {completed} из {total}.", "Выполнено {completed} из {total}.", "Есть прогресс: {completed} из {total}.",
    "{completed} из {total} уже позади.", "",
)

CHECKPOINT_FOCUS = (
    "В плане {left_phrase}: {habits}.", "В фокусе {left_phrase}: {habits}.", "На очереди {left_phrase}: {habits}.", "Осталось: {habits}.",
    "Список на сейчас: {habits}.",
)
# Одна привычка на весь день: без «из 1», просто что в плане.
CHECKPOINT_FOCUS_SINGLE = (
    "В плане одна привычка: {habits}.", "Сегодня в плане одна привычка — {habits}.", "Одна привычка на сегодня: {habits}.",
    "На очереди одна привычка: {habits}.", "Всё внимание — на одной привычке: {habits}.",
)

CHECKPOINT_CTA_ZERO = (
    "Начни с ближайшего пункта", "Закрой первый пункт — и наберёшь темп", "Один небольшой шаг — и день сдвинется с места",
    "Самое простое — начать прямо сейчас", "Пара минут — и счёт будет открыт",
)
CHECKPOINT_CTA_SINGLE = (
    "Начать можно с пары минут", "Одного шага достаточно, чтобы сдвинуть день", "Сделай её прямо сейчас — и день пойдёт",
    "Это займёт всего несколько минут", "Займись ею — и первая отметка за сегодня готова",
)
CHECKPOINT_CTA_PROGRESS = (
    "Закрой следующий пункт и держи темп", "Продолжай с ближайшего пункта", "Один шаг — и ближе к полному дню",
    "Не сбавляй темп: следующий пункт рядом", "Ещё чуть-чуть — и день в копилке",
)
CHECKPOINT_CTA_BY_HOUR = {
    10: ("Утро — лучшее время, чтобы начать",),
    12: ("Впереди ещё весь день",),
    17: ("До вечера хватит времени",),
    22: ("Успеешь закрыть до сна", "Пока день не закончился, ещё можно успеть"),
}
CHECKPOINT_RECENT = 3                # сколько последних точек человека учитываем, чтобы не повторять части


def _fresh_part(pool, used, rng):
    """Элемент набора, которого не было в последних сообщениях (если свежих нет — любой)."""
    fresh = [item for item in pool if item not in used]
    return rng.choice(fresh or list(pool))


def compose_checkpoint_message(incomplete_habits, hour: int, completed: int | None = None, total: int | None = None,
                               timed_habits=None, recent_picks=(), earlier_today: int = 0, rng=None):
    """(текст, выбранные части). recent_picks — выбранные части трёх последних точек этого человека (новые первыми),
    earlier_today — сколько точек дня человеку уже приходило сегодня. Выбранные части сохраняет вызывающий (после успешной отправки)."""
    rng = rng or random
    titles = [html.escape(str(h["title"])) for h in incomplete_habits]       # сообщение уходит с parse_mode=HTML
    timed_habits = [(h, m, html.escape(str(title))) for h, m, title in (timed_habits or [])]
    left = len(titles)
    if total is None:
        total = left + max(0, int(completed or 0))
    if completed is None:
        completed = max(0, int(total) - left)
    completed = max(0, int(completed))
    total = max(left, int(total))
    habits = ", ".join(f"«{t}»" for t in titles)
    left_word = plural_ru(left, "привычка", "привычки", "привычек")
    left_phrase = f"одна {left_word}" if left == 1 else f"{left} {left_word}"

    single = total <= 1
    case = "single" if single else ("zero" if completed == 0 else "progress")
    openers = list(CHECKPOINT_OPENERS.get(hour, CHECKPOINT_OPENERS_DEFAULT))
    if earlier_today and completed == 0:
        openers += list(CHECKPOINT_OPENERS_AGAIN)
    statuses = ("",) if single else (CHECKPOINT_STATUS_ZERO if completed == 0 else CHECKPOINT_STATUS_PROGRESS)
    focus = CHECKPOINT_FOCUS_SINGLE if single else CHECKPOINT_FOCUS
    ctas = {"single": CHECKPOINT_CTA_SINGLE, "zero": CHECKPOINT_CTA_ZERO, "progress": CHECKPOINT_CTA_PROGRESS}[case] + CHECKPOINT_CTA_BY_HOUR.get(hour, ())

    used = {part: {p.get(part) for p in list(recent_picks)[:CHECKPOINT_RECENT]} for part in ("o", "s", "f", "c")}
    picks = {
        "o": _fresh_part(openers, used["o"], rng), "s": _fresh_part(statuses, used["s"], rng),
        "f": _fresh_part(focus, used["f"], rng), "c": _fresh_part(ctas, used["c"], rng),
    }
    kwargs = dict(time=hour, completed=completed, total=total, habits=habits, left_phrase=left_phrase)
    text = " ".join(part for part in (
        picks["o"].format(**kwargs), picks["s"].format(**kwargs), picks["f"].format(**kwargs) + _format_timed_note(timed_habits),
        picks["c"].format(**kwargs) + " " + random_emoji(SOFT_EMOJIS),
    ) if part)
    return re.sub(r"[ \t]{2,}", " ", text).strip(), picks


# =====================================
# УПРОЩЁННАЯ ТОЧКА ДНЯ (habit_checkpoint_style == "simple")
# =====================================
# Для тех, у кого почти все привычки уже на таймере (planned_time) и своя
# система в голове/жизни выстроена — без рамки "X из Y выполнено, сверка
# курса" и без доп. строки про таймерные привычки (это и есть "лишняя
# доп. инфа", от которой этот стиль отказывается): просто короткое прямое
# упоминание того, что осталось без таймера.

SIMPLE_HABIT_CHECKPOINT_TEMPLATES = [
    "Ещё не отмечено: {habits} {emoji}",
    "Пока открыто: {habits} {emoji}",
    "На сегодня осталось: {habits} {emoji}",
    "Без отметки пока: {habits} {emoji}",
]


def format_simple_habit_checkpoint_message(free_habits) -> str:
    titles = [str(h["title"]) for h in free_habits]
    habits = ", ".join(f"«{t}»" for t in titles)
    return pick(SIMPLE_HABIT_CHECKPOINT_TEMPLATES, pool=SOFT_EMOJIS, habits=habits)


# =====================================
# СВОЁ ВРЕМЯ НАПОМИНАНИЯ У ПРИВЫЧКИ
# =====================================

PLANNED_TIME_REMINDER_TEMPLATES = [
    "⏰ Как раз время для «{title}» {emoji}",
    "🔔 Пора: «{title}». Ты сам выбрал это время — доверься себе {emoji}",
    "✨ Момент для «{title}» настал. Пара минут — и готово {emoji}",
    "📌 «{title}» ждёт своего времени — а оно как раз сейчас {emoji}",
]

def format_planned_time_reminder_message(title: str) -> str:
    # Название привычки пишет человек, а сообщение уходит в HTML-режиме: «<» или «&» в нём без экранирования роняют отправку («can't parse entities»), и напоминания нет.
    return pick(PLANNED_TIME_REMINDER_TEMPLATES, pool=SOFT_EMOJIS, title=html.escape(str(title), quote=False))


def format_habit_checkpoint_10_message(incomplete_habits) -> str:
    """Совместимость со старыми вызовами: контрольная точка в 10:00."""
    return format_habit_checkpoint_message(incomplete_habits, 10)


# Совместимость со старыми вызовами. Теперь это не «утренний» текст и не
# утверждает, что привычку нужно сделать с утра.
HABIT_REMINDER_TEMPLATES = [
    "Привычка «{title}» ещё не отмечена. Её время пришло — закрой её и освободи место в плане {emoji}",
    "«{title}» пока открыта. Найди для неё несколько минут и доведи до конца {emoji}",
    "Я вижу, что «{title}» ещё ждёт отметки. Сделай её, когда будет удобный момент {emoji}",
    "Проверка по привычке «{title}»: пока без отметки. Один небольшой шаг — и она закрыта {emoji}",
    "«{title}» ещё в списке на сегодня. Не обязательно спешить — просто не потеряй её из фокуса {emoji}",
    "На сегодня осталась привычка «{title}». Закрой её сейчас и освободи голову от ещё одного пункта {emoji}",
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
    "Задача «{title}» ещё открыта. Самое время вернуться к ней {emoji}",
    "«{title}» пока не закрыта. Выдели ей немного времени и доведи до конца {emoji}",
    "Я бы сейчас вернулся к задаче «{title}» — она всё ещё ждёт тебя {emoji}",
]


def format_plan_task_reminder_message(title: str) -> str:
    return pick(PLAN_TASK_REMINDER_TEMPLATES, pool=TASK_EMOJIS, title=html.escape(str(title), quote=False))   # текст задачи пишет человек, см. выше


# =====================================
# ПЛАН ДНЯ — 19:00
# =====================================

DAY_PROGRESS_TEMPLATES = [
    "Вечерняя сверка: готово {done} из {total} {total_gen}. {remaining_block} Можно спокойно двигаться дальше {emoji}",
    "Проверил весь план: выполнено {done} из {total} {total_gen}. {remaining_block} {emoji}",
    "19:00 — время свериться с планом. Закрыто {done} из {total} {total_gen}. {remaining_block} Выбери следующий пункт {emoji}",
    "До финиша {finish_phrase}. {remaining_block} Двигайся по своему порядку и закрой оставшееся {emoji}",
]


def task_genitive(n: int) -> str:
    """Форма после «из»: из 1 задачи, из 2 задач, из 5 задач, из 21 задачи."""
    return _genitive_after_iz(n, "задачи", "задач")


def _format_named_items(items: list[str]) -> str:
    return ", ".join(f"«{x}»" for x in items[:6]) + (f" и ещё {len(items) - 6}" if len(items) > 6 else "")


def format_remaining_plan_block(open_items=None) -> str:
    """Группирует главную и второстепенные задачи, не повторяя слово
    «второстепенная» перед каждым пунктом."""
    if isinstance(open_items, dict):
        main = [str(x).strip() for x in open_items.get("main", []) if str(x).strip()]
        secondary = [str(x).strip() for x in open_items.get("secondary", []) if str(x).strip()]
    else:
        # Совместимость со старым форматом списка строк.
        main, secondary = [], []
        for item in (open_items or []):
            text = str(item).strip()
            if text.startswith("главная:"):
                main.append(text.split(":", 1)[1].strip().strip("«»"))
            elif text.startswith("второстепенная:"):
                secondary.append(text.split(":", 1)[1].strip().strip("«»"))
            else:
                secondary.append(text.strip("«»"))

    parts = []
    if main:
        parts.append(f"Главная: {_format_named_items(main)}.")
    if secondary:
        parts.append(f"Второстепенные: {_format_named_items(secondary)}.")
    return " ".join(parts)


def format_day_progress_message(done: int, total: int, open_items=None) -> str:
    left = max(0, total - done)
    remaining_block = format_remaining_plan_block(open_items)
    left_phrase = left_phrase_tasks(left)

    return pick(
        DAY_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        done=done,
        total=total,
        left=left,
        total_gen=task_genitive(total),
        left_word=task_word(left),
        left_phrase=left_phrase,
        finish_phrase=left_phrase[0].lower() + left_phrase[1:],
        remaining_block=remaining_block,
    )


# =====================================
# LEGACY: старый вечерний формат (не используется планировщиком)
# =====================================

EVENING_PROGRESS_TEMPLATES = [
    "Вечерняя сверка: готово {done} из {total} {total_gen}. {left_phrase}: {task_list}. {action_phrase} {emoji}",
    "Финишная проверка: выполнено {done} из {total} {total_gen}. {left_phrase}: {task_list}. {action_phrase} {emoji}",
    "Проверил весь план. {left_phrase}: {task_list}. {action_phrase} {emoji}",
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

    left_phrase = left_phrase_tasks(left)
    action_phrase = (
        "Закрой её — это последний пункт плана" if left == 1
        else "Начни с одной из них и последовательно закрой оставшееся"
    )
    return pick(
        EVENING_PROGRESS_TEMPLATES,
        pool=TASK_EMOJIS,
        done=done,
        total=total,
        total_gen=task_genitive(total),
        left=left,
        left_word=left_word,
        left_verb=left_verb,
        relative=relative,
        task_list=listed,
        left_phrase=left_phrase,
        action_phrase=action_phrase,
    )


ALL_TASKS_DONE_TEMPLATES = [
    "Все задачи дня закрыты. Чисто. Теперь можно выдохнуть и не тащить незакрытые дела в вечер {emoji}",
    "План дня закрыт полностью. Хорошая работа — сегодня начатое доведено до конца {emoji}",
    "Все задачи готовы. Я бы на твоём месте сейчас просто спокойно отдохнул — день уже сделан {emoji}",
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
    "Цель «{goal}» ещё ждёт первого шага сегодня. Сделай небольшой конкретный шаг и запусти движение {emoji}",
]


def format_goal_reminder_message(goal: str) -> str:
    return pick(GOAL_REMINDER_TEMPLATES, pool=TASK_EMOJIS, goal=goal)


# =====================================
# ИДЕАЛЬНЫЙ ДЕНЬ ПО ПРИВЫЧКАМ (пром 8, доп.)
# =====================================
# Показывается, когда закрыты ВСЕ привычки дня и их было 2 и более —
# т.е. пользователь "прошёл" всю цепочку двойных начислений подряд.

PERFECT_HABIT_STREAK_TEMPLATES = [
    "Идеальный страйк из {n} {gen} подряд закрыт! Мало кто такое может 💪",
    "{n} из {n} — всё закрыто одним махом. Это не каждый день бывает ⚡️",
    "Чисто. Все {n} {habits} дня закрыты подряд — вот это дисциплина 🏆",
    "Идеальный день: {n} {habits}, ни одной пропущенной. Уважение 🫡",
    "Вся цепочка из {n} {gen} закрыта за один день. Это заметно 👊",
]


def format_perfect_habit_streak_message(total_habits: int) -> str:
    return random.choice(PERFECT_HABIT_STREAK_TEMPLATES).format(
        n=total_habits, habits=habit_word(total_habits), gen=habit_genitive(total_habits)
    )


MONTH_END_REWARD_TEMPLATE = (
    "💎 Идеальный месяц! Каждый день — 2+ привычки подряд: "
    "{days}/{days}. Держи награду: +{coins} Adam Coin и {diamonds} {diamond_word} 💎 "
    "— такое удаётся не всем."
)


def format_month_end_reward_message(days: int, coins: int, diamonds: int) -> str:
    return MONTH_END_REWARD_TEMPLATE.format(days=days, coins=coins, diamonds=diamonds, diamond_word=diamond_word(diamonds))


# =====================================
# КОРОТКАЯ ПОХВАЛА ЗА ВТОРОСТЕПЕННУЮ ЗАДАЧУ (пром 7.1)
# =====================================
# Короче, чем поздравление за главную задачу/весь план. Ключ у каждой
# фразы стабильный (используется для истории показов — см.
# db/task_praise.py), поэтому индексы менять нельзя, можно только
# дописывать новые в конец.

SECONDARY_TASK_PRAISE = [
    ("secondary_praise_01", "Так держать!"),
    ("secondary_praise_02", "Классно выполнено!"),
    ("secondary_praise_03", "Отлично сделано!"),
    ("secondary_praise_04", "Не знал, что ты так можешь!"),
    ("secondary_praise_05", "Чётко сделано!"),
    ("secondary_praise_06", "Супер!"),
    ("secondary_praise_07", "Прекрасно выполнено!"),
    ("secondary_praise_08", "Классный результат!"),
    ("secondary_praise_09", "Отлично!"),
    ("secondary_praise_10", "Круто, {name}!"),
    ("secondary_praise_11", "Замечательно!"),
    ("secondary_praise_12", "Ура, {name}!"),
    ("secondary_praise_13", "Браво!"),
    ("secondary_praise_14", "Превосходно!"),
    ("secondary_praise_15", "Вот так-то лучше!"),
]

# После 3 дней использования ИЛИ 15 показанных похвал (что наступит
# раньше) повторы уже разрешены — до этого момента каждая фраза из пула
# показывается не больше одного раза.
SECONDARY_TASK_PRAISE_STRICT_DAYS = 3
SECONDARY_TASK_PRAISE_STRICT_COUNT = 15


def format_secondary_task_praise(name, used_today, used_ever, strict_mode) -> tuple[str, str]:
    """Возвращает (ключ, текст) короткой похвалы.

    used_today/used_ever — множества уже показанных ключей (за сегодня и за
    всё время). strict_mode=True запрещает повтор чего-либо из used_ever,
    не только used_today (первые 3 дня/15 показов — см. константы выше)."""
    name = (name or "").strip() or "Игрок"

    candidates = [item for item in SECONDARY_TASK_PRAISE if item[0] not in used_today]
    if strict_mode:
        never_shown = [item for item in candidates if item[0] not in used_ever]
        if never_shown:
            candidates = never_shown
    if not candidates:
        # Пул исчерпан даже без учёта used_today (маловероятно — в пуле 15
        # фраз) — берём весь пул, чтобы не остаться без ответа.
        candidates = SECONDARY_TASK_PRAISE

    key, template = random.choice(candidates)
    return key, template.format(name=name)


FIRST_PLAN_ACTION_TEMPLATES = {
    # Важно: эти формулировки должны начинаться так же, как MAIN_GOAL_DONE_TEMPLATES
    # ("Главная задача закры...") — сообщение должно распознаваться и как
    # "главная задача закрыта" (см. _is_main_goal_done в тестах), и как
    # сообщение про порядок выполнения (первой/сначала/начал с).
    "главная задача": [
        "Главная задача закрыта первой — день уже сдвинулся с места {emoji}",
        "Главная задача закрыта первой: с неё начался день — это хороший старт {emoji}",
        "Главная задача закрыта — сначала она, а дальше по плану {emoji}",
    ],
    "задача": [
        "Сначала закрыта первая задача — день уже сдвинулся с места {emoji}",
        "Первой закрыта обычная задача — хороший старт дня {emoji}",
        "Первой в этот день закрыта задача — движение пошло {emoji}",
    ],
}


def format_first_plan_action_message(kind: str = "задача") -> str:
    """Сообщение только для РЕАЛЬНО ПЕРВОЙ отметки задачи дня.

    Важно: эта функция вызывается роутами только при done_before == 0.
    Поэтому слово «сначала»/«первой» здесь всегда означает фактический
    порядок выполнения, а не важность задачи. Если главная задача закрыта
    последней, сюда попадание невозможно — сработает сообщение о завершении
    всего плана или о главной задаче с учётом оставшихся пунктов.
    """
    pool = FIRST_PLAN_ACTION_TEMPLATES.get(kind, FIRST_PLAN_ACTION_TEMPLATES["задача"])
    return pick(pool, pool=TASK_EMOJIS)


MAIN_GOAL_DONE_TEMPLATES = [
    "Главная задача закрыта. Отлично — теперь можно переключиться на остальное без этого груза {emoji}",
    "Главная задача дня выполнена. Один важный пункт уже закрыт {emoji}",
    "Главная задача закрыта. Хороший ход — продолжаем по оставшемуся плану {emoji}",
]


def format_main_goal_done_message(remaining_count: int = 0) -> str:
    remaining_count = max(0, int(remaining_count or 0))
    if remaining_count == 1:
        return pick([
            "Главная задача закрыта. Осталась ещё одна задача — спокойно закрой её в своём темпе {emoji}",
            "Главная задача выполнена. В плане осталась одна открытая задача {emoji}",
        ])
    if remaining_count > 1:
        return pick([
            "Главная задача закрыта. В плане {} — двигайся по своему порядку {{emoji}}".format(left_phrase_tasks(remaining_count).lower()),
            "Главная задача выполнена. {} — выбери следующую {{emoji}}".format(left_phrase_tasks(remaining_count)),
        ])
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
# Если сегодняшние привычки уже все закрыты, призыв "сделать ещё шаг"
# звучит так, будто прогресс не заметили — отдельные тексты для этого случая.
WEEK_END_ALL_DONE_TEMPLATES = [
    "Отличный результат к концу недели — сегодня уже всё закрыто. Держи этот темп {emoji}",
    "Неделя подходит к концу, а сегодня у тебя чисто. Сильный финиш {emoji}",
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


def format_week_end_message(all_done_today: bool = False) -> str:
    return pick(WEEK_END_ALL_DONE_TEMPLATES if all_done_today else WEEK_END_TEMPLATES)


def format_month_start_message() -> str:
    return pick(MONTH_START_TEMPLATES)


def format_month_end_message() -> str:
    return pick(MONTH_END_TEMPLATES)


# Сообщения триала (ТЗ владельца) живут в trial_messages.py — каталог, тексты и показ по дню триала.


MORNING_GREETING_TEMPLATES = [
    "Новый день начался. Не спеши хвататься за всё сразу — выбери первый важный шаг {emoji}",
    "Сегодня не нужен идеальный старт. Нужен первый сделанный шаг — дальше будет легче {emoji}",
    "Начинаем новый день. Держи фокус на главном и не распыляйся {emoji}",
]


def format_morning_greeting_message() -> str:
    return pick(MORNING_GREETING_TEMPLATES)
