"""
A/B-тест вступительного текста анкеты (survey_variant) — чистая функция
от чётности telegram_id, без отдельного хранения; get_survey_funnel_by_variant
считает конверсию (survey_completed_at) в разрезе варианта прямо в SQL.
"""
from db import add_user, survey_variant, get_survey_funnel_by_variant, set_access_status


def test_survey_variant_is_deterministic_by_parity():
    assert survey_variant(900000002) == "A"
    assert survey_variant(900000003) == "B"
    # Тот же ID — тот же вариант при повторном вызове.
    assert survey_variant(900000002) == survey_variant(900000002)


def test_funnel_by_variant_counts_completed_only_for_that_variant(uid):
    # ВАЖНО: uid идёт из общего sequential-счётчика (tests/conftest.py),
    # который расшарен между ВСЕМИ тестами сессии. uid+1 мог бы случайно
    # совпасть со следующим выданным uid другого теста и "отравить" его
    # чужим access_status — используем uid*10, заведомо вне диапазона
    # счётчика (900_000_000+, шагает по +1).
    even_id = uid * 10
    assert even_id % 2 == 0
    add_user(even_id, "u_a", "Test")
    set_access_status(even_id, "pending")  # выставляет survey_completed_at

    result = get_survey_funnel_by_variant()

    assert result["A"]["total"] >= 1
    assert result["A"]["completed"] >= 1
    assert set(result.keys()) == {"A", "B"}
