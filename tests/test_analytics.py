"""
Реальный расход токенов LLM (не путать с ai_quota — той внутренней
квотой, что считает только ручной чат). log_ai_tokens вызывается из
multi_agent.py::_ask на каждый вызов Groq/OpenAI — покрывает и чат, и
автоматические AI-напоминания (совет дня, утренние сообщения и т.д.).
"""
from db.analytics import log_ai_tokens, get_ai_tokens_today, get_ai_tokens_by_provider_today
from admin_digest_scheduler import build_stats_report


def test_token_logging_sums_correctly_and_by_provider():
    before = get_ai_tokens_today()

    log_ai_tokens(1000, "groq")
    log_ai_tokens(500, "openai")

    assert get_ai_tokens_today() == before + 1500
    by_provider = get_ai_tokens_by_provider_today()
    assert by_provider.get("groq", 0) >= 1000
    assert by_provider.get("openai", 0) >= 500


def test_log_ai_tokens_ignores_zero_or_missing():
    before = get_ai_tokens_today()
    log_ai_tokens(0, "groq")
    log_ai_tokens(None, "groq")
    assert get_ai_tokens_today() == before


def test_digest_report_includes_real_token_line():
    log_ai_tokens(42, "groq")
    report = build_stats_report()
    assert "Реальный расход токенов" in report
