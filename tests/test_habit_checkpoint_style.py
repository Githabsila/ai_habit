"""
Стиль контрольной точки по привычкам (10/12/17/22:00): 'full' (по
умолчанию) — как раньше, со сверкой прогресса и доп. строкой про
привычки со своим ещё не наступившим временем; 'simple' — для тех, у
кого почти все привычки уже на таймере и своя система в голове/жизни
выстроена, без сверки и без упоминания таймерных привычек вообще.
"""
from db import add_user, get_habit_checkpoint_style, update_habit_checkpoint_style

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def test_habit_checkpoint_style_defaults_to_full(uid):
    add_user(uid, "u", "Test")
    assert get_habit_checkpoint_style(uid) == "full"


def test_update_habit_checkpoint_style_to_simple(uid):
    add_user(uid, "u", "Test")
    assert update_habit_checkpoint_style(uid, "simple") is True
    assert get_habit_checkpoint_style(uid) == "simple"


def test_update_habit_checkpoint_style_rejects_invalid(uid):
    add_user(uid, "u", "Test")
    assert update_habit_checkpoint_style(uid, "extreme") is False
    assert get_habit_checkpoint_style(uid) == "full"


async def test_habit_checkpoint_style_route_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/habit-checkpoint-style", headers=headers, json={"style": "simple"})
    assert r.status == 200
    assert get_habit_checkpoint_style(uid) == "simple"


async def test_habit_checkpoint_style_route_rejects_invalid(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/habit-checkpoint-style", headers=headers, json={"style": "extreme"})
    assert r.status == 400


async def test_bootstrap_reflects_habit_checkpoint_style(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/habit-checkpoint-style", headers=headers, json={"style": "simple"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["habit_checkpoint_style"] == "simple"
