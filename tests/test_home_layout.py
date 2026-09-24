"""
Главный экран: порядок разделов "План дня"/"Привычки" и видимость
"Плана дня". Просьба пользователя: многие пользуются сторонними
планировщиками задач, а у нас главное — привычки. Два независимых
тумблера: поменять секции местами, и скрыть "План дня" с возможностью
вернуть обратно (сами данные плана при этом не трогаются).
"""
from db import add_user, get_home_layout, update_home_habits_first, update_home_plan_hidden

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def test_home_layout_defaults_to_plan_first_and_visible(uid):
    add_user(uid, "u", "Test")
    assert get_home_layout(uid) == {"habits_first": False, "plan_hidden": False}


def test_update_home_habits_first(uid):
    add_user(uid, "u", "Test")
    update_home_habits_first(uid, True)
    assert get_home_layout(uid)["habits_first"] is True
    update_home_habits_first(uid, False)
    assert get_home_layout(uid)["habits_first"] is False


def test_update_home_plan_hidden(uid):
    add_user(uid, "u", "Test")
    update_home_plan_hidden(uid, True)
    assert get_home_layout(uid)["plan_hidden"] is True
    update_home_plan_hidden(uid, False)
    assert get_home_layout(uid)["plan_hidden"] is False


def test_home_layout_settings_are_independent(uid):
    add_user(uid, "u", "Test")
    update_home_habits_first(uid, True)
    update_home_plan_hidden(uid, True)
    assert get_home_layout(uid) == {"habits_first": True, "plan_hidden": True}


async def test_home_layout_route_updates_habits_first_only(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/home-layout", headers=headers, json={"habits_first": True})
    assert r.status == 200
    body = await r.json()
    assert body["home_layout"] == {"habits_first": True, "plan_hidden": False}


async def test_home_layout_route_updates_plan_hidden_only(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/home-layout", headers=headers, json={"plan_hidden": True})
    assert r.status == 200
    body = await r.json()
    assert body["home_layout"] == {"habits_first": False, "plan_hidden": True}


async def test_home_layout_route_updates_both_at_once(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/settings/home-layout", headers=headers,
        json={"habits_first": True, "plan_hidden": True},
    )
    body = await r.json()
    assert body["home_layout"] == {"habits_first": True, "plan_hidden": True}


async def test_home_layout_route_with_empty_body_is_a_noop(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/home-layout", headers=headers, json={"habits_first": True})
    r = await client.post("/api/settings/home-layout", headers=headers, json={})
    body = await r.json()
    assert body["home_layout"] == {"habits_first": True, "plan_hidden": False}


async def test_bootstrap_reflects_home_layout(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/home-layout", headers=headers, json={"plan_hidden": True})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["home_layout"] == {"habits_first": False, "plan_hidden": True}
