"""
Согласие на обработку данных (юридический минимум, 06.11; db/consent.py): первый экран анкеты со ссылками на Условия и Политику,
«Поехали» записывает версию документов и время.
"""
from pathlib import Path

from db import add_user
from db.consent import CONSENT_VERSION, consent_of
from tests.conftest import sign_init_data

APP = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


async def test_consent_is_recorded_with_the_version(client, uid):
    add_user(uid, "a", "Аня")
    assert consent_of(uid) is None
    response = await client.post("/api/consent", json={}, headers={"Authorization": f"tma {sign_init_data(uid)}"})
    assert (await response.json()) == {"ok": True, "version": CONSENT_VERSION}
    consent = consent_of(uid)
    assert consent["version"] == CONSENT_VERSION and consent["at"]


def test_the_welcome_screen_links_the_documents_and_sends_consent():
    assert 'class="start-quiz-consent"' in APP and 'href="/terms"' in APP and 'href="/privacy"' in APP
    assert 'api("/api/consent"' in APP and 'step.type === "welcome"' in APP
