import hashlib

from db import (
    get_ai_history,
    get_progress,
    get_habits,
    get_user_profile,
    get_recent_negative_reasons,
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

    lines = [
        f"Уровень: {progress['level']}",
        f"Adam Coin: {progress['xp']}",
        f"Серия дней подряд: {progress['streak']}",
    ]

    if habits:
        lines.append("")
        lines.append("Привычки пользователя:")

        for habit in habits:
            status = "да" if habit["completed"] else "нет"
            lines.append(f"• {habit['title']} — {status}")
    else:
        lines.append("")
        lines.append("Привычек пока не добавлено.")

    profile = get_user_profile(user_id)

    if profile and profile.get("summary"):
        lines.append("")
        lines.append("Что известно о пользователе:")
        lines.append(profile["summary"])

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