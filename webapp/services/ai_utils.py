import hashlib

from db import (
    get_ai_history,
    get_progress,
    get_habits,
    get_user_profile,
    get_recent_negative_reasons,
    get_daily_plan,
    get_timezone,
    get_proactive_topic,
)


def build_history_text(
    user_id: int,
    limit: int = 4,
    max_chars_per_msg: int = 200,
) -> str:
    """
    Берёт последние сообщения переписки с AI-наставником из БД и
    превращает их в текстовый блок-контекст для мультиагентного пайплайна.
    """
    history = get_ai_history(user_id)
    if not history:
        return ""

    lines = []

    for row in history[-limit:]:
        role = "Пользователь" if row["role"] == "user" else "Наставник"

        text = row["message"]

        if len(text) > max_chars_per_msg:
            text = text[:max_chars_per_msg] + "…"

        lines.append(f"{role}: {text}")

    return "\n".join(lines)


def build_user_context(user_id: int) -> str:
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
    lines = [
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
            # ✅ Проблема №2: раньше сюда не попадал статус главной задачи
            # (plan["main_goal_completed"] из БД просто игнорировался), из-за
            # чего ИИ не мог узнать, что пользователь её уже отметил
            # выполненной, и советовал сделать то, что уже сделано.
            status = "выполнена" if plan.get("main_goal_completed") else "НЕ выполнена"
            lines.append(f"• Главная задача дня: {plan['main_goal']} — {status}")

        for task in plan["tasks"]:
            if not task["text"]:
                continue
            status = "выполнено" if task["completed"] else "не выполнено"
            lines.append(f"• Задача: {task['text']} — {status}")
    else:
        lines.append("")
        lines.append("План на сегодня пока не составлен.")

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

    return "\n".join(lines)


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
    topic = get_proactive_topic(user_id)
    if topic:
        lines.append("Одноразовая тема для мягкого напоминания: " + topic)
    return "\n".join(lines)
