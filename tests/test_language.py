"""Roadmap #46 — язык интерфейса (ru/en)."""
from db import add_user, get_language, set_language

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


def test_language_defaults_to_ru(uid):
    add_user(uid, "u", "Test")
    assert get_language(uid) == "ru"


def test_set_language_to_en(uid):
    add_user(uid, "u", "Test")
    assert set_language(uid, "en") is True
    assert get_language(uid) == "en"


def test_set_language_rejects_invalid(uid):
    add_user(uid, "u", "Test")
    assert set_language(uid, "fr") is False
    assert get_language(uid) == "ru"


async def test_language_route_persists(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/language", headers=headers, json={"language": "en"})
    assert r.status == 200
    assert get_language(uid) == "en"


async def test_language_route_rejects_invalid(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post("/api/settings/language", headers=headers, json={"language": "de"})
    assert r.status == 400


async def test_bootstrap_reflects_language(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    await client.post("/api/settings/language", headers=headers, json={"language": "en"})
    r = await client.get("/api/bootstrap", headers=headers)
    body = await r.json()
    assert body["settings"]["language"] == "en"


async def test_ai_context_includes_english_instruction_when_set(uid):
    add_user(uid, "u", "Test")
    set_language(uid, "en")
    from webapp.services.ai_utils import build_user_context
    context = build_user_context(uid)
    assert "respond in English" in context


async def test_ai_context_no_english_instruction_by_default(uid):
    add_user(uid, "u", "Test")
    from webapp.services.ai_utils import build_user_context
    context = build_user_context(uid)
    assert "respond in English" not in context
