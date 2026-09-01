"""HTTP-роут для roadmap #25 (POST /api/settings/goals)."""
from db import add_user, get_long_term_goals

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def test_set_goals_route_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/goals", headers=headers, json={"text": "Пробежать марафон"})
    assert r.status == 200
    assert get_long_term_goals(uid) == "Пробежать марафон"


async def test_bootstrap_reflects_saved_goals(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/goals", headers=headers, json={"text": "Выучить испанский"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["long_term_goals"] == "Выучить испанский"
