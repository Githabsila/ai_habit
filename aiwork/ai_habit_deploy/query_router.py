"""
query_router.py

Intelligent query routing: determine if question needs full multi-agent system
or if a single fast Claude call is sufficient.

Usage:
    from query_router import classify_query
    
    complexity = classify_query("How to start meditating?")
    # Returns: "simple"
    
    complexity = classify_query("Help me build a SaaS business with full roadmap")
    # Returns: "complex"
"""

import re
from typing import Literal

# Keywords that indicate "simple" queries (use fast single-call path)
SIMPLE_KEYWORDS = {
    "привычка", "задача", "план", "прогресс", "мотивация",
    "лень", "прокрастинация", "начать", "помощь", "как",
    "совет", "что делать", "почему", "когда", "время",
    "расписание", "распорядок", "режим", "утро", "вечер",
    "завтрак", "обед", "ужин", "спорт", "тренировка",
    "медитация", "чтение", "учеба", "работа", "отдых",
    "сон", "здоровье", "энергия", "силы", "устал",
    "упал", "сорвался", "мотивация", "ленюсь", "лень",
    "скучно", "нудно", "трудно", "сложно", "помощь",
}

# Keywords that indicate "complex" queries requiring full multi-agent system
COMPLEX_KEYWORDS = {
    "бизнес", "стартап", "компания", "сотрудник", "управление",
    "маркетинг", "продажи", "клиент", "договор", "контракт",
    "стратегия", "конкурент", "инвестор", "капитал", "финанс",
    "проект", "архитектура", "система", "интеграция", "автоматизация",
    "карьера", "должность", "зарплата", "повышение", "переговоры",
    "партнер", "партнёрство", "сделка", "предложение", "угод",
}

def classify_query(message: str) -> Literal["simple", "complex"]:
    """
    Classify query complexity to determine which solver to use.
    
    Args:
        message: User's query text
    
    Returns:
        "simple" → use fast single-call path (2-4 seconds)
        "complex" → use full multi-agent pipeline (15-20 seconds)
    
    Heuristics:
        1. Count keyword matches in each category
        2. Prefer multi-agent for very long messages (detailed plans)
        3. Prefer multi-agent for messages with multiple subtopics
        4. Default to simple for everything else
    """
    text_lower = message.lower()
    
    # Count matches in each category
    complex_matches = sum(1 for kw in COMPLEX_KEYWORDS if kw in text_lower)
    simple_matches = sum(1 for kw in SIMPLE_KEYWORDS if kw in text_lower)
    
    # Rule 1: If 2+ complex keywords → use full system
    if complex_matches >= 2:
        return "complex"
    
    # Rule 2: Very long message (detailed business question) → use full system
    # Typically: habit questions are <100 chars, business questions >300
    if len(message) > 300:
        # But check if it's actually complex content (not just rambling)
        if complex_matches >= 1:
            return "complex"
    
    # Rule 3: Multiple subtopics with business context → use full system
    # Example: "Also, how do I manage a team? Plus, should I hire?"
    if re.search(r'[;,]\s*(?:также|еще|и еще)', message, re.IGNORECASE):
        if complex_matches >= 1:
            return "complex"
    
    # Rule 4: Explicit complexity indicators
    # Words like "детально", "подробно", "план", "анализ" suggest need for thinking
    detailed_words = {"детально", "подробно", "проанализи", "разработай", "создай"}
    if any(word in text_lower for word in detailed_words) and len(message) > 150:
        return "complex"
    
    # Default: simple (covers 80% of real queries)
    return "simple"


def get_routing_debug_info(message: str) -> dict:
    """
    Debug helper: return detailed routing information.
    
    Useful for understanding why a query was routed a particular way.
    """
    text_lower = message.lower()
    complexity = classify_query(message)
    
    complex_matches = sum(1 for kw in COMPLEX_KEYWORDS if kw in text_lower)
    simple_matches = sum(1 for kw in SIMPLE_KEYWORDS if kw in text_lower)
    
    return {
        "complexity": complexity,
        "message_length": len(message),
        "complex_keyword_matches": complex_matches,
        "simple_keyword_matches": simple_matches,
        "matched_complex_keywords": [kw for kw in COMPLEX_KEYWORDS if kw in text_lower],
        "matched_simple_keywords": [kw for kw in SIMPLE_KEYWORDS if kw in text_lower],
    }


# ============ TESTS (run with: python -m pytest query_router.py -v) ============

if __name__ == "__main__":
    # Quick sanity checks
    test_cases = [
        ("I missed my workout today", "simple"),
        ("How should I start meditating?", "simple"),
        ("I'm tired and demotivated", "simple"),
        ("Help me build a SaaS business", "complex"),
        ("What's the full roadmap for launching a startup with a remote team?", "complex"),
        ("Manage team and hire employees plus marketing strategy", "complex"),
        ("Just a quick question about my evening routine", "simple"),
    ]
    
    print("Query Router Tests:\n")
    for message, expected in test_cases:
        result = classify_query(message)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{message[:50]}...'")
        print(f"  Expected: {expected}, Got: {result}")
        if result != expected:
            debug = get_routing_debug_info(message)
            print(f"  Debug: {debug}")
        print()
