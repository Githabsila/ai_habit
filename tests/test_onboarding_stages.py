"""
Онбординг v3 (по детальному плану пользователя, в духе Habitica): 5
пронумерованных шагов — app.js::PRODUCT_ONBOARDING_STEPS (1-2 —
стартовые: кнопка "Добавить привычку" и главное дело дня; 3-5 —
контекстные: календарь/рейтинг/профиль, по факту первого захода на
вкладку, независимо от того, завершён ли стартовый сценарий).
"""
from db import add_user
from db.product_experience import advance_onboarding, get_onboarding_state


def test_advance_onboarding_accepts_max_stage(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 5)
    assert get_onboarding_state(uid)["onboarding_stage"] == 5


def test_advance_onboarding_clamps_above_max(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 99)
    assert get_onboarding_state(uid)["onboarding_stage"] == 5


def test_advance_onboarding_never_decreases(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 4)
    advance_onboarding(uid, 2)
    assert get_onboarding_state(uid)["onboarding_stage"] == 4
