import hashlib
import re

from db import (
    get_ai_history,
    get_progress,
    get_habits,
    get_user_profile,
    get_recent_negative_reasons,
    get_daily_plan,
    get_timezone,
    get_proactive_topic,
    get_long_term_goals,
    get_language,
)


def build_history_text(
    user_id: int,
    limit: int = 4,
    max_chars_per_msg: int = 200,
    last_assistant_chars: int | None = None,
) -> str:
    """
    Берёт последние сообщения переписки с AI-наставником из БД и
    превращает их в текстовый блок-контекст для мультиагентного пайплайна.

    last_assistant_chars — сколько символов оставить от ПОСЛЕДНЕГО ответа наставника (остальные режутся по
    max_chars_per_msg): без этого просьба «а второй пункт подробнее?» приходила к модели, когда сам план уже обрезан.
    """
    history = get_ai_history(user_id, limit=limit)
    if not history:
        return ""

    lines = []
    window = history[-limit:]
    last_assistant = max((i for i, row in enumerate(window) if row["role"] != "user"), default=None)

    for index, row in enumerate(window):
        role = "Пользователь" if row["role"] == "user" else "Наставник"

        text = row["message"]
        cap = last_assistant_chars if (index == last_assistant and last_assistant_chars) else max_chars_per_msg

        if len(text) > cap:
            text = text[:cap] + "…"

        lines.append(f"{role}: {text}")

    return "\n".join(lines)


def build_user_context(user_id: int, max_chars: int = 2800) -> str:
    """
    Собирает данные пользователя для AI.
    """
    progress = get_progress(user_id)

    if not progress:
        return ""

    habits = get_habits(user_id)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        local_now = datetime.now(ZoneInfo(get_timezone(user_id)))
        now_text = local_now.strftime('%H:%M')
    except Exception:
        now_text = datetime.now().strftime('%H:%M')
    lines = []
    # Roadmap #46 — язык интерфейса пользователя (units.language, по
    # умолчанию 'ru'). Инструкция первой строкой — самый заметный для
    # модели способ переключить язык ВСЕГО пайплайна одним местом, не
    # трогая промпты каждой отдельной стадии в multi_agent.py.
    if get_language(user_id) == "en":
        lines.append("IMPORTANT: The user's interface language is English — respond in English, not Russian.")
    lines += [
        f"Текущее локальное время пользователя: {now_text}",
        f"Уровень: {progress['level']}",
        f"Adam Coin: {progress['xp']}",
        f"Серия дней подряд: {progress['streak']}",
    ]

    if habits:
        lines.append("")
        lines.append("Привычки пользователя:")

        for habit in habits:
            status = "да" if habit["completed"] else "нет"
            planned = habit["planned_time"] if "planned_time" in habit.keys() else None
            timing = f" — время: {planned}" if planned else ""
            lines.append(f"• {habit['title']} — {status}{timing}")
        # Явный счёт: модель не должна принять «привычки со временем» за все привычки (так ответ «у тебя одна привычка» получался,
        # когда вторая была без времени).
        done_count = sum(1 for habit in habits if habit["completed"])
        lines.append(f"Всего привычек: {len(habits)} (выполнено {done_count}, не выполнено {len(habits) - done_count}). "
                     "Называя привычки или их число, учитывай ВСЕ из этого списка, в том числе без времени")
    else:
        lines.append("")
        lines.append("Привычек пока не добавлено.")

    # ✅ План на сегодня (главная задача + до 5 задач) из Mini App —
    # чтобы AI-наставник видел не только привычки, но и текущие цели
    # пользователя на день и мог давать советы с их учётом.
    plan = get_daily_plan(user_id)

    if plan and (plan["main_goal"] or plan["tasks"]):
        lines.append("")
        lines.append("План пользователя на сегодня:")

        if plan["main_goal"]:
            status = "выполнена" if plan.get("main_goal_completed") else "НЕ выполнена"
            lines.append(f"• Главная: {plan['main_goal']} — {status}")

        secondary_lines = []
        for task in plan["tasks"]:
            if not task["text"]:
                continue
            status = "выполнено" if task["completed"] else "не выполнено"
            secondary_lines.append(f"{task['text']} — {status}")
        if secondary_lines:
            lines.append("• Второстепенные:")
            lines.extend(f"  - {text}" for text in secondary_lines)
    else:
        lines.append("")
        lines.append("План на сегодня пока не составлен.")

    # Roadmap #25 — долгосрочные цели, которые пользователь сам задал в
    # настройках (в отличие от profile_summary ниже — это не
    # авто-суммаризация переписки, а явный текст от самого пользователя,
    # который он мог редактировать когда угодно).
    long_term_goals = get_long_term_goals(user_id)
    if long_term_goals:
        lines.append("")
        lines.append(f"Долгосрочная цель пользователя (важно держать в уме, упоминать когда уместно): {long_term_goals}")

    profile = get_user_profile(user_id)
    profile_summary = profile["summary"] if profile else None

    if profile_summary:
        lines.append("")
        lines.append("Что известно о пользователе:")
        lines.append(profile_summary)

    reasons = get_recent_negative_reasons(user_id, limit=3)

    if reasons:
        unique = list(dict.fromkeys(reasons))
        lines.append("")
        lines.append(
            "Недавние замечания пользователя: "
            + "; ".join(unique)
            + ". Не повторяй эти ошибки."
        )

    # Адам сам начал разговор (пуш / первое сообщение в чате) — при ответе человека он помнит, почему написал и что сказал.
    try:
        from db import adam_checkin as _adam_checkin
        proactive = _adam_checkin.last_proactive_context(user_id)
    except Exception:
        proactive = ""
    if proactive:
        lines.append("")
        lines.append(proactive)

    return "\n".join(lines)


def context_fingerprint(base_context: str) -> str:
    """Отпечаток данных человека (привычки, план, серия, цели…) для ключа кэша ответов Адама.

    Кэш живёт 12 часов: без отпечатка ответ, данный когда привычка была одна, возвращался бы на тот же вопрос и после того, как
    человек добавил вторую («сколько у меня привычек?» → «одна»). Минуты в «Текущее локальное время» не берём — только час: иначе
    отпечаток менялся бы каждую минуту, а ответ про «что делать сейчас» вечером не должен приходить из утреннего кэша."""
    lines = []
    for line in str(base_context or "").splitlines():
        if line.startswith("Текущее локальное время"):
            line = re.sub(r"(\d{1,2}):\d{2}", r"\1", line)
        lines.append(line)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def _cache_key(text: str, style: str) -> str:
    normalized = " ".join(text.lower().strip().split())

    return hashlib.md5(
        f"{normalized}|{style}".encode("utf-8")
    ).hexdigest()

def build_proactive_context(user_id: int) -> str:
    """Контекст для советов/проактивных сообщений.

    Намеренно НЕ включает долгую память и прошлые замечания: проактивные
    сообщения должны опираться на актуальный день. Одноразовая тема из
    недавнего разговора допускается максимум на 24 часа.
    """
    progress = get_progress(user_id)
    if not progress:
        return ""
    lines = []
    if get_language(user_id) == "en":
        lines.append("IMPORTANT: The user's interface language is English — respond in English, not Russian.")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        local_now = datetime.now(ZoneInfo(get_timezone(user_id)))
        lines.append(f"Текущее локальное время пользователя: {local_now.strftime('%H:%M')}")
    except Exception:
        pass
    lines.extend([
        f"Уровень: {progress['level']}",
        f"Серия дней подряд: {progress['streak']}",
    ])
    habits = get_habits(user_id)
    if habits:
        lines.append("Актуальные привычки на сегодня:")
        for habit in habits:
            status = "выполнена" if habit["completed"] else "не выполнена"
            planned = habit["planned_time"] if "planned_time" in habit.keys() else None
            timing = f" — время: {planned}" if planned else ""
            lines.append(f"• {habit['title']} — {status}{timing}")
    plan = get_daily_plan(user_id)
    if plan and (plan["main_goal"] or plan["tasks"]):
        lines.append("План на сегодня:")
        if plan["main_goal"]:
            status = "выполнена" if plan.get("main_goal_completed") else "НЕ выполнена"
            lines.append(f"• Главная задача: {plan['main_goal']} — {status}")
        for task in plan["tasks"]:
            if task["text"]:
                status = "выполнено" if task["completed"] else "не выполнено"
                lines.append(f"• Задача: {task['text']} — {status}")
    long_term_goals = get_long_term_goals(user_id)
    if long_term_goals:
        lines.append(f"Долгосрочная цель пользователя: {long_term_goals}")
    topic = get_proactive_topic(user_id)
    if topic:
        lines.append("Одноразовая тема для мягкого напоминания: " + topic)
    return "\n".join(lines)
