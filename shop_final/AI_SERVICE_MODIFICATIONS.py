"""
MODIFICATIONS FOR webapp/services/ai_service.py

This file shows the EXACT replacement for the chat() function in ai_service.py.

INSTRUCTIONS:
1. Open webapp/services/ai_service.py
2. Find the async def chat(user_id: int, message: str): function
3. REPLACE the entire function body with the code below
4. The imports at the top of ai_service.py are already correct

Current file should look like this at top:
    from db import (get_ai_style, cache_get, cache_set, ...)
    from webapp.services.ai_utils import (build_history_text, build_user_context, _cache_key, ...)
    from multi_agent import solve_task_multiagent
    
After your changes, it will also have:
    from multi_agent import solve_task_multiagent, solve_task_simple
    from query_router import classify_query
"""

# ============================================================================
# ADD THESE IMPORTS AT TOP OF ai_service.py (with existing imports)
# ============================================================================

from multi_agent import solve_task_multiagent, solve_task_simple
from query_router import classify_query


# ============================================================================
# REPLACEMENT: The chat() function
# ============================================================================
# 
# FIND: async def chat(user_id: int, message: str):
# REPLACE: The entire function below

async def chat(user_id: int, message: str):
    """
    Route user query to appropriate solver based on complexity.
    
    - Simple queries (80%): Fast single-call path (2-4 seconds)
    - Complex queries (20%): Full multi-agent pipeline (15-20 seconds)
    
    Args:
        user_id: Telegram user ID
        message: User's message
    
    Returns:
        {
            "answer": str — the response
            "is_crisis": bool — whether crisis signals detected
            "suggested_habit": str | None — suggested habit if any
            "complexity": "simple" | "complex" | "cached" — routing path used
        }
    """
    
    # Build context once (used for both routing and solving)
    history_text = build_history_text(user_id)
    user_context = build_user_context(user_id)
    style = get_ai_style(user_id)

    # Check if answer is already cached
    # Cache key includes user_id to prevent cross-user collisions
    cache_key = f"{user_id}:{_cache_key(message, style)}"
    cached = cache_get(cache_key)

    if cached is not None:
        # Return cached response without re-solving
        return {
            "answer": cached,
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "cached",
        }

    # ⭐ INTELLIGENT ROUTING: Determine which solver to use
    complexity = classify_query(message)
    
    logger.info(f"Routing query (complexity={complexity}, len={len(message)}): {message[:50]}...")
    
    try:
        if complexity == "simple":
            # ⚡ FAST PATH: Single API call (2-4 seconds)
            logger.info("→ Using FAST simple path")
            result = await solve_task_simple(
                task=message,
                history=history_text,
                user_context=user_context,
                style=style,
            )
        else:
            # 🧠 COMPLEX PATH: Full multi-agent pipeline (15-20 seconds)
            logger.info("→ Using FULL multi-agent pipeline")
            result = await solve_task_multiagent(
                task=message,
                history=history_text,
                user_context=user_context,
                style=style,
            )
    except Exception as e:
        logger.exception(f"Error in chat routing for user {user_id}")
        return {
            "answer": (
                "❌ Ошибка при формировании ответа. "
                "Попробуйте ещё раз через минуту."
            ),
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "error",
        }

    # Cache simple non-crisis queries for future use
    # (This saves money and improves response time on duplicate questions)
    if (
        result.get("complexity") == "simple"
        and not result.get("is_crisis")
    ):
        logger.info("→ Caching simple query result")
        cache_set(cache_key, result["answer"])

    return result


# ============================================================================
# OPTIONAL: Add logging if not already present
# ============================================================================
# If you get errors about 'logger' not being defined, add this at the top:

import logging
logger = logging.getLogger("ai_service")


# ============================================================================
# VERIFICATION: Test your modifications
# ============================================================================
#
# After making changes, verify the file loads:
#
#     python -c "from webapp.services.ai_service import chat; print('✓ Imports OK')"
#
# If you get ImportError or other errors:
# 1. Check that query_router.py is in the project root
# 2. Check that multi_agent.py has solve_task_simple() function
# 3. Run: python -m pytest tests/ to check for syntax errors
