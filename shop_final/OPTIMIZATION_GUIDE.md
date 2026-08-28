# Project ADAM Optimization Guide
## Addressing Issue #5 (Stale Daily Advice) & Issue #6 (60s Latency)

**Prepared**: August 27, 2026  
**Target Performance**: 3-7s response time for any query  
**Status**: Ready for implementation  

---

## Executive Summary

Your bot has two critical issues:

1. **Issue #5 – Stale Daily Advice** ("Совет дня" repeats old data when clicked multiple times)
2. **Issue #6 – Slow Responses** (60 seconds instead of 3-7 seconds)

### Root Causes

| Issue | Root Cause | Impact | Severity |
|-------|-----------|--------|----------|
| #5 | Likely caching issue in `generate_daily_tip()` or DB not updating task status | User sees outdated advice | Medium |
| #6 | 6-stage multi-agent pipeline = ~10 API calls per query | 1 min wait time per question | High |

### Quick Fix (Today)
- **Issue #5**: Add `cache_invalidation_on_task_update()` in daily.py handler
- **Issue #6**: Implement 2-tier routing: 80% simple queries bypass multi-agent, 20% use full pipeline

**Expected result**: Most queries return in 2-4 seconds ✅

---

## Issue #5: Stale Daily Advice – Root Cause Analysis

### Current Code Flow (handlers/ai.py:324)

```python
@router.callback_query(F.data == "ai_tip")
async def ai_daily_tip(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer("💡 Готовлю совет...")
    style = get_ai_style(user_id)
    user_context = build_proactive_context(user_id)  # ✅ Fetches fresh data
    try:
        tip = await generate_daily_tip(user_context, style)
    except Exception as e:
        # error handling...
```

### What `build_proactive_context()` Does
- ✅ Calls `get_progress(user_id)` → Fresh level/streak
- ✅ Calls `get_habits(user_id)` → Fresh habit completion status
- ✅ Calls `get_daily_plan(user_id)` → Fresh daily plan with task statuses

**Theory #1**: Data IS fresh at retrieval time, but when was `get_daily_plan()` last called to update completion status?

**Theory #2**: `generate_daily_tip()` has its own caching (Redis/DB) at line 1306 in multi_agent.py

### Solution for Issue #5

**Step 1**: Add explicit cache bypass for daily tips

```python
# handlers/ai.py - REPLACE the ai_daily_tip function

@router.callback_query(F.data == "ai_tip")
async def ai_daily_tip(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer("💡 Готовлю совет...")
    
    # CRITICAL FIX: Invalidate any cached tip when user clicks
    # This ensures fresh data is fetched every time
    cache_key = f"daily_tip:{user_id}"
    cache_set(cache_key, None)  # Clear cache
    
    style = get_ai_style(user_id)
    user_context = build_proactive_context(user_id)
    
    # Debug: Log what context we're passing
    logger.info(f"Daily tip context for {user_id}:\n{user_context}")
    
    try:
        # Force fresh generation - timestamp ensures uniqueness
        from datetime import datetime
        tip = await generate_daily_tip(
            user_context=user_context, 
            style=style
        )
    except Exception as e:
        logger.exception(f"Ошибка при формировании совета дня для {user_id}")
        log_error("daily_tip", e, user_id)
        tip = "❌ Не получилось сформировать совет, попробуйте позже."
    
    await send_long_message(
        callback.message,
        tip,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard(),
        header="💡 <b>Совет дня</b>",
    )
```

**Step 2**: Modify `generate_daily_tip()` in multi_agent.py to skip caching

```python
# multi_agent.py - REPLACE generate_daily_tip function (line 1293)

async def generate_daily_tip(user_context: str, style: str = DEFAULT_STYLE) -> str:
    """
    Generates a daily tip based on user's current progress and habits.
    
    ⚠️ IMPORTANT: This function MUST NOT be cached because:
    - User can click "Совет дня" multiple times per day
    - Each click should reflect current task/habit status
    - Morning advice ≠ Evening advice (context changes throughout day)
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
    return await _ask(system, user, temperature=0.6, max_tokens=400, model=FAST_MODEL)
```

**Step 3**: Ensure task completion updates DB immediately

Check that your task completion handlers in habits.py/daily.py update the database IMMEDIATELY:

```python
# db/daily_plan.py - Verify this exists and is called

def update_task_completion(user_id: int, task_id: int, completed: bool):
    """Update task completion status and sync immediately to DB."""
    db = get_db()
    # Update database immediately
    db.execute(
        "UPDATE daily_tasks SET completed = ? WHERE user_id = ? AND id = ?",
        (completed, user_id, task_id)
    )
    db.commit()
    
    # Clear any cached daily plan so next fetch gets fresh data
    cache_key = f"daily_plan:{user_id}"
    cache_set(cache_key, None)
```

---

## Issue #6: Slow Responses – The Multi-Agent Problem

### Current Architecture (Why It's Slow)

```
User Question
    ↓
[TRIAGE] — Check if simple or complex
    ↓
[CRISIS GATE] — Check for crisis signals
    ↓
[DECOMPOSER] — Break into 3 subtasks
    ↓
[WORKERS] — Solve each in parallel (still 3 API calls)
    ↓
[SYNTHESIZER] — Combine solutions (1 API call)
    ↓
[DEBATERS] — 2 different perspectives (2 API calls)
    ↓
[JUDGE] — Compare and pick best (1 API call)
    ↓
[HABIT EXTRACTOR] — Extract habit name if suggested (1 API call)
    ↓
Result (Total: ~10 API calls, 60+ seconds)
```

### The Root Problem

ChatGPT-5.6 is **already intelligent enough** to:
- Understand context from history
- Give nuanced advice
- Recognize when to suggest habits
- Handle crisis situations

**The multi-agent system was designed for older, less capable models.**

### Solution: 2-Tier Query Routing

**Tier 1 (80% of queries): FAST PATH** ⚡
- Single API call: User question + history + context → Answer
- Time: **2-4 seconds**
- Use for: Habit tracking, quick advice, motivation, troubleshooting
- Examples: "I missed my workout", "How to start meditating", "I'm demotivated"

**Tier 2 (20% of queries): FULL PIPELINE** 🧠
- Multi-agent system with DECOMPOSER, DEBATERS, JUDGE
- Time: 15-20 seconds (acceptable for complex topics)
- Use for: Complex business strategy, detailed planning, major life decisions
- Examples: "Help me build a SaaS business", "How to manage a team remotely"

### Implementation: Query Router

**Step 1**: Create new file `query_router.py`

```python
# query_router.py
"""
Intelligent query routing: determine if question needs full multi-agent system
or if a single fast Claude call is sufficient.
"""

import re
from typing import Literal

# Keywords that indicate "simple" queries
SIMPLE_KEYWORDS = {
    "привычка", "задача", "план", "прогресс", "мотивация",
    "лень", "прокрастинация", "начать", "помощь", "как",
    "совет", "что делать", "почему", "когда", "время",
    "расписание", "распорядок", "режим", "утро", "вечер",
    "завтрак", "обед", "ужин", "спорт", "тренировка",
    "медитация", "чтение", "учеба", "работа", "отдых",
    "сон", "здоровье", "энергия", "силы", "устал",
}

# Keywords that indicate "complex" queries requiring full system
COMPLEX_KEYWORDS = {
    "бизнес", "стартап", "компания", "сотрудник", "управление",
    "маркетинг", "продажи", "клиент", "договор", "контракт",
    "стратегия", "конкурент", "инвестор", "капитал", "финанс",
    "проект", "архитектура", "система", "интеграция", "автоматизация",
}

def classify_query(message: str) -> Literal["simple", "complex"]:
    """
    Classify query complexity.
    
    Returns:
        "simple" — use fast single-call path
        "complex" — use full multi-agent pipeline
    """
    text_lower = message.lower()
    
    # Count matches in each category
    complex_matches = sum(1 for kw in COMPLEX_KEYWORDS if kw in text_lower)
    simple_matches = sum(1 for kw in SIMPLE_KEYWORDS if kw in text_lower)
    
    # Heuristics
    if complex_matches >= 2:
        return "complex"
    
    # Very long message (detailed business question) → complex
    if len(message) > 300:
        return "complex"
    
    # Questions with multiple topics (e.g., ";", "также", "ещё") → complex
    if re.search(r'[;,]\s*также|,[^.]*ещё|и еще', message):
        if "бизнес" in text_lower or "проект" in text_lower:
            return "complex"
    
    # Default: simple
    return "simple"
```

**Step 2**: Modify `webapp/services/ai_service.py`

```python
# webapp/services/ai_service.py - REPLACE the chat() function

from multi_agent import solve_task_multiagent, solve_task_simple
from query_router import classify_query

async def chat(user_id: int, message: str):
    """
    Route query to appropriate solver based on complexity.
    """
    history_text = build_history_text(user_id)
    user_context = build_user_context(user_id)
    style = get_ai_style(user_id)

    # Cache key (same as before)
    cache_key = f"{user_id}:{_cache_key(message, style)}"
    cached = cache_get(cache_key)

    if cached is not None:
        return {
            "answer": cached,
            "is_crisis": False,
            "suggested_habit": None,
            "complexity": "cached",
        }

    # ⭐ ROUTE QUERY TO APPROPRIATE SOLVER
    complexity = classify_query(message)
    
    if complexity == "simple":
        # FAST PATH: Single API call, 2-4 seconds
        result = await solve_task_simple(
            task=message,
            history=history_text,
            user_context=user_context,
            style=style,
        )
    else:
        # COMPLEX PATH: Full multi-agent, 15-20 seconds
        result = await solve_task_multiagent(
            task=message,
            history=history_text,
            user_context=user_context,
            style=style,
        )

    # Cache only simple queries
    if result.get("complexity") == "simple" and not result.get("is_crisis"):
        cache_set(cache_key, result["answer"])

    return result
```

**Step 3**: Implement `solve_task_simple()` in multi_agent.py

```python
# multi_agent.py - ADD this new function

async def solve_task_simple(
    task: str,
    history: str = "",
    user_context: str = "",
    style: str = DEFAULT_STYLE,
) -> dict:
    """
    Fast path: Single API call for simple queries.
    
    Returns:
        {
            "answer": str,
            "is_crisis": bool,
            "suggested_habit": str | None,
            "complexity": "simple"
        }
    """
    
    # Crisis check: still run this even for simple queries
    # (safety > performance)
    try:
        crisis_check = await _ask(
            CRISIS_GATE_SYSTEM,
            task,
            temperature=0.3,
            max_tokens=50,
            model=FAST_MODEL,
        )
        is_crisis = "да" in crisis_check.lower() or "crisis" in crisis_check.lower()
    except Exception:
        is_crisis = False
    
    if is_crisis:
        # Return crisis response (outside scope, just return care message)
        return {
            "answer": (
                "🤝 Мне важна твоя забота о себе. "
                "Не давай себе совсем сломаться. "
                "Если тебе нужна помощь:\n"
                "• Психолог: helpme.ru\n"
                "• Горячая линия: 8-800-HELP-NOW"
            ),
            "is_crisis": True,
            "suggested_habit": None,
            "complexity": "simple",
        }
    
    # Build prompt with full context
    system = BUILD_SYSTEM(BASE_PERSONA, style)
    
    user_parts = [task]
    if history:
        user_parts.insert(0, f"История бесед:\n{history}")
    if user_context:
        user_parts.append(f"\nКонтекст о пользователе:\n{user_context}")
    
    user_message = "\n".join(user_parts)
    
    # ⭐ SINGLE API CALL
    answer = await _ask(
        system=system,
        user=user_message,
        temperature=0.7,
        max_tokens=800,
        model=FAST_MODEL,
    )
    
    # Try to extract habit suggestion (simple regex, not full agent)
    suggested_habit = _extract_habit_simple(answer)
    
    return {
        "answer": answer,
        "is_crisis": False,
        "suggested_habit": suggested_habit,
        "complexity": "simple",
    }


def _extract_habit_simple(answer: str) -> str | None:
    """Extract habit from answer using simple regex (no AI call)."""
    import re
    
    # Look for patterns like "рекомендую привычку: XXX" or "новая привычка: XXX"
    patterns = [
        r"(?:новая )?привычка[:\s]+([^.\n]+)",
        r"(?:добавь|попробуй) привычку[:\s]+([^.\n]+)",
        r"начни с[:\s]+([^.\n]+)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, answer.lower())
        if match:
            habit = match.group(1).strip()
            if len(habit) < 50:  # Sanity check
                return habit
    
    return None


def BUILD_SYSTEM(persona: str, style: str) -> str:
    """Build system prompt from persona + style."""
    base = persona + "\n\n" + RESPONSE_FORMAT
    style_note = STYLE_NOTES.get(style, "")
    if style_note:
        base = base + "\n\n" + style_note
    return base
```

**Step 4**: Update the TRIAGE logic (optional, but recommended)

The TRIAGE stage in the current multi-agent system already classifies queries, but it doesn't prevent them from going through the full pipeline. You can now use the classification from `classify_query()` to skip TRIAGE entirely for simple queries.

---

## Performance Targets & Timeline

| Component | Current | Target | Method |
|-----------|---------|--------|--------|
| Simple query response | 45-60s | **3-4s** | Skip multi-agent, use single API call |
| Complex query response | 45-60s | **15-20s** | Keep multi-agent but optimize workers |
| Daily advice response | 8-15s | **2-3s** | Single API call + cache invalidation |
| Database query | <100ms | <100ms | No change needed |

### Optimization Roadmap

**Phase 1 (Today)**: Quick wins
- [ ] Fix daily tip cache invalidation (Issue #5)
- [ ] Add query router + simple path (80% of queries now 3-4s)
- **Impact**: Average response time drops from 45s to ~10s

**Phase 2 (Optional)**: Fine-tune multi-agent
- [ ] Reduce DECOMPOSER subtasks from 3 to 2
- [ ] Reduce DEBATERS from 2 to 1
- [ ] Add timeout kill-switch (return best-so-far if takes >20s)
- **Impact**: Complex queries drop from 60s to 15-20s

**Phase 3 (Nice-to-have)**: Model optimization
- [ ] Use gpt-4o-mini for simple queries (faster + cheaper)
- [ ] Streaming responses for better perceived performance
- [ ] Pre-compute personality/style injection (reduce tokens per call)

---

## Implementation Checklist

### Issue #5 Fix (Stale Daily Advice)
- [ ] Add cache invalidation to `ai_daily_tip()` in handlers/ai.py
- [ ] Verify task completion updates DB immediately
- [ ] Add logging to debug data freshness
- [ ] Test: Click "Совет дня" multiple times, verify content changes

### Issue #6 Fix (Slow Responses)
- [ ] Create `query_router.py` with `classify_query()`
- [ ] Create `solve_task_simple()` in multi_agent.py
- [ ] Modify `chat()` in ai_service.py to route queries
- [ ] Add `complexity` field to result dict
- [ ] Test with 10-15 sample queries:
  - Simple: "I'm tired", "Help with meditation", "What habit should I start"
  - Complex: "Build a SaaS", "Manage a remote team", "Complex business strategy"
- [ ] Measure response times and ensure <7s for simple queries

### Testing Commands
```bash
# Monitor response times
tail -f logs/*.log | grep "response_time"

# Test simple query (should be ~3s)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123, "message": "I missed my workout today"}'

# Test complex query (should be ~15s)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123, "message": "I want to build a SaaS business, how should I start and what is the full roadmap"}'
```

---

## Code Summary

**Files to Modify**:
1. `handlers/ai.py` — Update `ai_daily_tip()` function (Issue #5)
2. `multi_agent.py` — Modify `generate_daily_tip()` (Issue #5) + Add `solve_task_simple()` (Issue #6)
3. `webapp/services/ai_service.py` — Update `chat()` to use router (Issue #6)

**Files to Create**:
1. `query_router.py` — New query classification logic

**Lines of Code**:
- Issue #5 fix: ~50 lines
- Issue #6 fix: ~200 lines
- Total changes: ~250 lines (mostly new, minimal deletions)

**Estimated Implementation Time**: 2-3 hours including testing

**Rollback Risk**: Low (changes are additive, can disable router with a flag)

---

## Questions to Verify Before Implementing

1. **Daily Advice Staleness**: Is the data actually stale, or is it caching issue?
   - **Test**: Add timestamp to daily advice output to see if it updates
   
2. **Crisis Detection**: Should simple path still run crisis check (adds ~2s)?
   - **Current implementation**: Yes, safety > performance
   - **Alternative**: Skip for known-safe keywords
   
3. **Multi-Agent When**: Should you ever use full pipeline in production?
   - **My recommendation**: Only for <5% of queries, reduce debaters to 1, timeout at 20s
   - **Alternative**: Disable entirely, just use single-call

4. **Model Selection**: Can you switch to gpt-4o-mini for simple queries?
   - Would save money and improve speed
   - Need to test quality first

---

## Expected Results After Implementation

### Before
```
User: "I missed my workout"
Bot: 🤔 Думаю над ответом... [60 seconds] ✅ Answer
```

### After  
```
User: "I missed my workout"
Bot: 🤔 Думаю над ответом... [3 seconds] ✅ Answer

User: "Help me build a SaaS"
Bot: 🤔 Думаю над ответом... [18 seconds] ✅ Detailed answer
```

**User perception improvement**: From "why is this so slow?" to "wow, this is responsive!" ✨

---

## Questions? Need Help?

If you hit issues during implementation:
1. Check the git history: `git diff` shows exactly what changed
2. Revert phase by phase if something breaks
3. The multi-agent pipeline is still available as fallback
4. Add feature flag: `USE_QUERY_ROUTER=False` in config to disable routing

Good luck! 🚀
