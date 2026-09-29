"""
Онбординг v3 (по детальному плану пользователя, в духе Habitica): 7
пронумерованных шагов — app.js::PRODUCT_ONBOARDING_STEPS (1-4 —
стартовые: кнопка "Добавить привычку", список привычек, главное дело дня,
второстепенные задачи; 5-7 — контекстные: календарь/рейтинг/профиль, по
факту первого захода на вкладку, независимо от того, завершён ли
стартовый сценарий).
"""
from db import add_user, get_user
from db.product_experience import advance_onboarding, get_onboarding_state, restart_onboarding

from tests.conftest import sign_init_data


def test_advance_onboarding_accepts_max_stage(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 7)
    assert get_onboarding_state(uid)["onboarding_stage"] == 7


def test_advance_onboarding_clamps_above_max(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 99)
    assert get_onboarding_state(uid)["onboarding_stage"] == 7


def test_advance_onboarding_never_decreases(uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 4)
    advance_onboarding(uid, 2)
    assert get_onboarding_state(uid)["onboarding_stage"] == 4


# ------------------- "Показать подсказки заново" (Настройки) -------------------

def test_restart_onboarding_resets_stage_and_tour(uid):
    from db.users import mark_app_tour_seen

    add_user(uid, "u", "Test")
    advance_onboarding(uid, 5)
    mark_app_tour_seen(uid)

    restart_onboarding(uid)

    state = get_onboarding_state(uid)
    assert state["onboarding_stage"] == 0
    assert state["onboarding_started_at"] is None
    user = get_user(uid)
    assert not user["app_tour_seen"]


def test_restart_onboarding_keeps_handle_intro_seen(uid):
    add_user(uid, "u", "Test")
    from db.users import mark_handle_intro_seen, should_show_handle_intro
    mark_handle_intro_seen(uid)

    restart_onboarding(uid)

    assert should_show_handle_intro(uid) is False


async def test_onboarding_restart_route_requires_auth(client):
    r = await client.post("/api/onboarding/restart")
    assert r.status == 401


async def test_onboarding_restart_route_resets_state(client, uid):
    add_user(uid, "u", "Test")
    advance_onboarding(uid, 3)
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}

    r = await client.post("/api/onboarding/restart", headers=headers)

    assert r.status == 200
    assert get_onboarding_state(uid)["onboarding_stage"] == 0
