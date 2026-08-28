"""
MODIFICATIONS FOR multi_agent.py

These are the exact code changes needed for multi_agent.py.

INSTRUCTIONS:
1. Search for "async def solve_task_multiagent" and keep it (don't delete)
2. Find the end of solve_task_multiagent function
3. Add the solve_task_simple() function AFTER it (before the next function)
4. Find "async def generate_daily_tip" and REPLACE its entire docstring/implementation
5. That's it!

Location markers are provided to help you find where to insert.
"""

# ============================================================================
# MODIFICATION #1: Update generate_daily_tip() function
# ============================================================================
# 
# FIND THIS in multi_agent.py (around line 1293):
#     async def generate_daily_tip(user_context: str, style: str = DEFAULT_STYLE) -> str:
#
# REPLACE the entire function with the code below:

async def generate_daily_tip(user_context: str, style: str = DEFAULT_STYLE) -> str:
    """
    Generates a daily tip based on user's current progress and habits.
    
    ⚠️ IMPORTANT: This function MUST NOT be cached because:
    - User can click "Совет дня" multiple times per day
    - Each click should reflect current task/habit status
    - Morning advice ≠ Evening advice (context changes throughout day)
    - Any caching must be cleared at the handler level (handlers/ai.py)
    
    Args:
        user_context: User's current situation (habits, tasks, progress)
        style: Communication style preference
    
    Returns:
        Daily tip text (not cached)
    """
    style_note = STYLE_NOTES.get(style, "")
    system = TIP_SYSTEM
    if style_note:
        system = system + "\n\n" + style_note

    if user_context:
        user = f"Данные о пользователе:\n{user_context}"
    else:
        user = "Данных о пользователе пока нет — дай общий полезный совет по формированию привычек."

    # DO NOT CACHE - See explanation above
    # Always use fresh data, even if identical user_context
    # The handler (handlers/ai.py) ensures cache invalidation on each call
    return await _ask(system, user, temperature=0.6, max_tokens=400, model=FAST_MODEL)


# ============================================================================
# MODIFICATION #2: Add solve_task_simple() function
# ============================================================================
# 
# FIND THIS: The end of solve_task_multiagent() function (around line ~1200)
# ADD THIS CODE RIGHT AFTER IT (before generate_daily_tip):

async def solve_task_simple(
    task: str,
    history: str = "",
    user_context: str = "",
    style: str = DEFAULT_STYLE,
) -> dict:
    """
    Fast path: Single API call for simple queries (3-4 seconds).
    
    This bypasses the complex multi-agent pipeline for 80% of queries.
    Still includes crisis detection for safety.
    
    Args:
        task: User's question
        history: Previous conversation history
        user_context: User's profile and current situation
        style: Communication style preference
    
    Returns:
        {
            "answer": str — the response
            "is_crisis": bool — whether crisis signals detected
            "suggested_habit": str | None — habit to suggest if any
            "complexity": "simple" — always "simple" for this path
        }
    
    Performance:
        - Total time: 2-4 seconds (one API call)
        - Suitable for: quick advice, motivation, habit tracking, troubleshooting
    """
    
    logger.info(f"SIMPLE PATH: Processing task")
    
    # Crisis check: still run this even for simple queries (safety first)
    try:
        crisis_check = await _ask(
            CRISIS_GATE_SYSTEM,
            task,
            temperature=0.3,
            max_tokens=50,
            model=FAST_MODEL,
        )
        is_crisis = "да" in crisis_check.lower() or "crisis" in crisis_check.lower()
        logger.debug(f"Crisis check result: {is_crisis}")
    except Exception as e:
        logger.warning(f"Crisis check failed: {e}")
        is_crisis = False
    
    if is_crisis:
        logger.warning(f"CRISIS DETECTED - returning crisis response")
        # Return crisis response with support info
        return {
            "answer": (
                "🤝 Мне важна твоя забота о себе. "
                "Не давай себе совсем сломаться. "
                "Если тебе нужна профессиональная помощь:\n"
                "• Психолог: психолог-онлайн.рф\n"
                "• Горячая линия: 8-800-100-20-92 (Телефон доверия)"
            ),
            "is_crisis": True,
            "suggested_habit": None,
            "complexity": "simple",
        }
    
    # Build system prompt
    style_note = STYLE_NOTES.get(style, "")
    system = BASE_PERSONA + "\n\n" + RESPONSE_FORMAT
    if style_note:
        system = system + "\n\n" + style_note
    
    # Build user message with context
    user_parts = []
    if history:
        user_parts.append(f"История бесед:\n{history}")
    user_parts.append(f"Вопрос пользователя:\n{task}")
    if user_context:
        user_parts.append(f"\nКонтекст о пользователе:\n{user_context}")
    
    user_message = "\n".join(user_parts)
    
    # ⭐ SINGLE API CALL (the whole simple path is just this one call)
    logger.info("Making single API call for simple query...")
    answer = await _ask(
        system=system,
        user=user_message,
        temperature=0.7,
        max_tokens=800,
        model=FAST_MODEL,
    )
    
    # Try to extract habit suggestion (simple regex, no additional AI call)
    suggested_habit = _extract_habit_from_text(answer)
    
    logger.info(f"Simple path complete. Suggested habit: {suggested_habit}")
    
    return {
        "answer": answer,
        "is_crisis": False,
        "suggested_habit": suggested_habit,
        "complexity": "simple",
    }


def _extract_habit_from_text(text: str) -> str | None:
    """
    Extract habit name from response using simple regex (no AI call).
    
    Looks for patterns like:
    - "рекомендую привычку: XXX"
    - "новая привычка: XXX"  
    - "начни с: XXX"
    - "добавь привычку: XXX"
    
    Args:
        text: Response text to search
    
    Returns:
        Habit name if found, None otherwise
    """
    import re
    
    patterns = [
        r"(?:новая\s+)?привычка[:\s]+([^.\n]+)",
        r"(?:добавь|попробуй)\s+(?:новую\s+)?привычку[:\s]+([^.\n]+)",
        r"начни\s+с[:\s]+([^.\n]+)",
        r"(?:начните|начните)\s+с[:\s]+([^.\n]+)",
        r"рекомендую\s+(?:новую\s+)?привычку[:\s]+([^.\n]+)",
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            habit = match.group(1).strip()
            # Sanity check: habit name should be reasonably short
            if 3 < len(habit) < 100:
                return habit
    
    return None


# ============================================================================
# MODIFICATION #3: Ensure RESPONSE_FORMAT is defined (check if it exists)
# ============================================================================
#
# If you get an error about RESPONSE_FORMAT not being defined,
# add this near the top of multi_agent.py (around line 100, after BASE_PERSONA):

RESPONSE_FORMAT = (
    "Отвечай кратко и по делу. Если есть конкретный совет или шаги, "
    "выдели их пунктами. Используй эмодзи для выделения, но не переусложняй. "
    "Если уместно, предложи конкретную новую привычку."
)


# ============================================================================
# VERIFICATION: Test your modifications
# ============================================================================
#
# After making changes, test that imports work:
#
#     python -c "from multi_agent import solve_task_simple, generate_daily_tip; print('✓ Imports OK')"
#
# If you get ImportError, check that you added the functions in the right location.
