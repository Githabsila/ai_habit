from db import (
    get_ai_style,
    get_ai_history,
    cache_get,
    cache_set,
)

# ✅ Правильный импорт — только из ai_utils (убираем дублирование)
from webapp.services.ai_utils import (
    build_history_text,
    build_user_context,
    _cache_key,
)

# ❌ УДАЛИТЕ эти строки — они создают циклическую зависимость
# from handlers.ai import (
#     build_history_text,
#     build_user_context,
#     _cache_key,
# )

from multi_agent import solve_task_multiagent


async def chat(user_id: int, message: str):
    history_text = build_history_text(user_id, limit=4, max_chars_per_msg=180)
    user_context = build_user_context(user_id)
    style = get_ai_style(user_id)

    # Никакого дополнительного LLM-вызова: примерно каждый третий ответ
    # получает право на короткую умную шутку/интересный факт, если это уместно.
    previous = get_ai_history(user_id, limit=20)
    assistant_count = sum(1 for row in previous if row.get("role") == "assistant")
    humor_note = ""
    if (assistant_count + 1) % 3 == 0:
        humor_note = (
            "Иногда уместно добавить в конце одну короткую умную шутку, ироничную "
            "реплику или интересный факт, который действительно связан с темой. "
            "Это НЕ обязательно: если шутка будет натянутой или неуместной — не добавляй её. "
            "Никакого кринжа, случайных мемов и шуток в серьёзных темах."
        )

    # ⚠️ Раньше ключ кэша не учитывал user_id — при одинаковом тексте
    # вопроса и стиле разные пользователи могли получить чужой закэшированный
    # ответ, посчитанный по чужим привычкам/плану дня. Теперь ключ
    # индивидуальный для каждого пользователя.
    cache_key = f"{user_id}:{_cache_key(message, style)}"
    cached = cache_get(cache_key)

    if cached is not None:
        return {
            "answer": cached,
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "просто",
        }

    result = await solve_task_multiagent(
        task=message,
        history=history_text,
        user_context=user_context,
        style=style,
        humor_note=humor_note,
    )

    if (
        result.get("complexity") == "просто"
        and not result.get("is_crisis")
    ):
        cache_set(cache_key, result["answer"])

    return result