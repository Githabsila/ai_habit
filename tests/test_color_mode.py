"""Roadmap #48 — светлая/тёмная тема."""
from db import add_user, get_color_mode, update_color_mode

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def test_color_mode_defaults_to_dark(uid):
    add_user(uid, "u", "Test")
    assert get_color_mode(uid) == "dark"


def test_update_color_mode_to_light(uid):
    add_user(uid, "u", "Test")
    assert update_color_mode(uid, "light") is True
    assert get_color_mode(uid) == "light"


def test_update_color_mode_rejects_invalid(uid):
    add_user(uid, "u", "Test")
    assert update_color_mode(uid, "purple") is False
    assert get_color_mode(uid) == "dark"


async def test_color_mode_route_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/color-mode", headers=headers, json={"mode": "light"})
    assert r.status == 200
    assert get_color_mode(uid) == "light"


async def test_color_mode_route_rejects_invalid(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/color-mode", headers=headers, json={"mode": "neon"})
    assert r.status == 400


async def test_bootstrap_reflects_color_mode(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/color-mode", headers=headers, json={"mode": "light"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["color_mode"] == "light"
