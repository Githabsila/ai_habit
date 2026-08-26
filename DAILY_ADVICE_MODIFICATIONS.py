"""
MODIFICATIONS FOR handlers/ai.py

Fix Issue #5: Stale daily advice when clicked multiple times

INSTRUCTIONS:
1. Open handlers/ai.py
2. Find the function: @router.callback_query(F.data == "ai_tip")
3. Find the async def ai_daily_tip(callback: CallbackQuery): function
4. REPLACE the entire function (starting from @router line) with the code below
5. The required imports are already in the file

Current location: around line 324-346 in handlers/ai.py
"""

# ============================================================================
# REPLACEMENT: The ai_daily_tip handler
# ============================================================================
#
# FIND (around line 324):
#     @router.callback_query(F.data == "ai_tip")
#     async def ai_daily_tip(callback: CallbackQuery):
#
# REPLACE: The entire function below

@router.callback_query(F.data == "ai_tip")
async def ai_daily_tip(callback: CallbackQuery):
    """
    Generate and send daily advice tip.
    
    FIX FOR ISSUE #5:
    - Explicitly clear cache before generating
    - Force fresh data fetch from DB
    - Log what data we're using (for debugging)
    - Add timestamp to verify freshness
    
    This ensures that when user clicks "Совет дня" multiple times per day,
    they get advice based on their CURRENT task/habit status, not old data.
    """
    user_id = callback.from_user.id
    
    logger.info(f"User {user_id} clicked 'Совет дня' button")
    await callback.answer("💡 Готовлю совет...")
    
    # ⭐ CRITICAL FIX #1: Invalidate any cached daily tip
    # This ensures fresh generation every time
    from datetime import datetime
    cache_key = f"daily_tip:{user_id}"
    cache_set(cache_key, None)  # Clear cache
    logger.info(f"Cleared daily tip cache for user {user_id}")
    
    # Get user's communication style
    style = get_ai_style(user_id)
    
    # ⭐ CRITICAL FIX #2: Always build fresh context
    # This fetches current progress, habits, and tasks from DB
    user_context = build_proactive_context(user_id)
    
    # Log the context being used (helps debug if advice is stale)
    logger.info(f"Daily tip context for user {user_id}:")
    for line in user_context.split("\n"):
        logger.info(f"  {line}")
    
    try:
        # ⭐ CRITICAL FIX #3: Add timestamp to response
        # This helps verify the advice is fresh
        current_time = datetime.now()
        time_str = current_time.strftime("%H:%M")
        
        # Generate the tip (single API call, no multi-agent)
        tip = await generate_daily_tip(user_context, style)
        
        # Add timestamp to show freshness
        tip_with_timestamp = (
            f"{tip}\n\n"
            f"<i>Совет сгенерирован в {time_str}</i>"
        )
        
        logger.info(f"Daily tip generated successfully for user {user_id}")
        
    except Exception as e:
        logger.exception(f"Ошибка при формировании совета дня для {user_id}")
        log_error("daily_tip", e, user_id)
        tip_with_timestamp = "❌ Не получилось сформировать совет, попробуйте позже."

    # Send the tip to user
    await send_long_message(
        callback.message,
        tip_with_timestamp,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard(),
        header="💡 <b>Совет дня</b>",
    )
    
    logger.info(f"Daily tip sent to user {user_id}")


# ============================================================================
# OPTIONAL: Add a debug endpoint to check what context is being used
# ============================================================================
#
# If you want to verify that fresh data is being fetched, add this function
# to handlers/ai.py. It will help you debug Issue #5.

@router.callback_query(F.data == "ai_tip_debug")
async def ai_daily_tip_debug(callback: CallbackQuery):
    """
    DEBUG ONLY: Show what context data is being used for daily tips.
    
    Add a button in the main menu pointing to this: 
        InlineKeyboardButton("🐛 Debug Tip Data", callback_data="ai_tip_debug")
    
    Then remove this button before production!
    """
    user_id = callback.from_user.id
    
    # Fetch all the data that would be used for daily tips
    context = build_proactive_context(user_id)
    
    if not context:
        debug_text = "❌ No context data available (new user?)"
    else:
        # Parse context to show clearly
        debug_text = f"<b>📊 Debug: Daily Tip Context Data</b>\n\n{context}"
    
    await callback.message.edit_text(
        debug_text,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard(),
    )
    
    await callback.answer("Debug info shown 🔍")


# ============================================================================
# ADDITIONAL FIX: Ensure task completion updates cache immediately
# ============================================================================
#
# If users are completing tasks but the daily tip still shows them as incomplete,
# check that task completion handlers clear the cache.
#
# In handlers/habits.py or handlers/daily.py, when task is marked complete,
# add this before returning:

def _clear_daily_tip_cache_on_task_update(user_id: int):
    """Call this whenever user updates a habit or task."""
    # Clear daily tip cache so next "Совет дня" gets fresh context
    cache_set(f"daily_tip:{user_id}", None)
    
    # Also clear any daily plan cache
    cache_set(f"daily_plan:{user_id}", None)
    
    logger.info(f"Cleared daily caches for user {user_id} (task update)")


# ============================================================================
# IMPORTS: Make sure these are present at top of handlers/ai.py
# ============================================================================
#
# The code above uses these, they should already be imported:
#
#     from datetime import datetime
#     from db import (..., cache_set, ...)
#     from webapp.services.ai_utils import build_proactive_context
#     from multi_agent import generate_daily_tip
#     from handlers.helpers import send_long_message
#     from keyboards import back_menu_keyboard


# ============================================================================
# TESTING: How to verify the fix works
# ============================================================================
#
# 1. Add a test task with completion deadline in next 30 minutes
#
# 2. Click "Совет дня" → note the timestamp and task shown
#
# 3. Complete that task in the bot
#
# 4. Click "Совет дня" again immediately
#
# 5. VERIFY:
#    ✓ Timestamp changed (shows fresh generation)
#    ✓ Task is no longer shown as "не выполнена" (shows as "выполнена")
#    ✓ Advice mentions the completed task differently
#
# If the task still shows as incomplete:
#   - Check that task completion is saved to DB immediately
#   - Check `get_daily_plan(user_id)` returns updated status
#   - Add logger.info() in the task completion handler to verify it runs


# ============================================================================
# ROLLBACK: If the fix doesn't work
# ============================================================================
#
# 1. Comment out the timestamp line (line with time_str)
# 2. Remove the cache invalidation (the cache_set line)
# 3. Keep everything else the same
# 4. Check logs to see what context is being used:
#    tail -f logs/*.log | grep "Daily tip context"
#
# If context shows the OLD data, the problem is in build_proactive_context()
# or in the database not updating task status immediately.
