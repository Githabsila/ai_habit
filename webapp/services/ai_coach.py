from webapp.services.ai_utils import (
    build_history_text,
    build_user_context,
    _cache_key,
)

from multi_agent import solve_task_multiagent

from db import (
    add_ai_message,
    get_ai_style,
)


async def ask_ai(user_id: int, message: str):
    history = build_history_text(user_id)
    user_context = build_user_context(user_id)
    style = get_ai_style(user_id)

    result = await solve_task_multiagent(
        task=message,
        history=history,
        user_context=user_context,
        style=style,
    )

    answer = result["answer"]

    add_ai_message(user_id, "user", message)
    add_ai_message(user_id, "assistant", answer)

    return answer