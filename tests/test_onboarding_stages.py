"""
Базовая версия онбординга "в духе Habitica" (черновик для предпросмотра,
будет дорабатываться по референсам пользователя): добавлен шаг
приветствия первым в app.js::PRODUCT_ONBOARDING_STEPS — теперь 6 шагов
вместо 5, advance_onboarding должен принимать весь диапазон.
"""
from db import add_user
from db.product_experience import advance_onboarding, get_onboarding_state


def test_advance_onboarding_accepts_new_max_stage(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 6)
    assert get_onboarding_state(uid)["onboarding_stage"] == 6


def test_advance_onboarding_clamps_above_new_max(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 99)
    assert get_onboarding_state(uid)["onboarding_stage"] == 6


def test_advance_onboarding_never_decreases(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 4)
    advance_onboarding(uid, 2)
    assert get_onboarding_state(uid)["onboarding_stage"] == 4
