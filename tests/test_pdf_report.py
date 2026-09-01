"""Roadmap #28 — PDF-отчёт о прогрессе."""
from db import add_user, add_habit, get_habits, complete_habit

from tests.conftest import sign_init_data
from webapp.services.pdf_report import generate_progress_pdf


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}"}


def test_generate_pdf_returns_none_for_unknown_user():
    assert generate_progress_pdf(999999999999) is None


def test_generate_pdf_returns_valid_pdf_bytes(uid):
    add_user(uid, "u", "Test User")
    add_habit(uid, "Медитация")
    complete_habit(get_habits(uid)[0]["id"])

    pdf_bytes = generate_progress_pdf(uid)
    assert pdf_bytes is not None
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 1000


def test_generate_pdf_works_with_no_habits(uid):
    add_user(uid, "u", "Empty User")
    pdf_bytes = generate_progress_pdf(uid)
    assert pdf_bytes[:5] == b"%PDF-"


async def test_pdf_report_route_returns_pdf(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.get("/api/progress/pdf-report", headers=headers)
    assert r.status == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"]
    body = await r.read()
    assert body[:5] == b"%PDF-"


async def test_pdf_report_route_requires_auth(client):
    r = await client.get("/api/progress/pdf-report")
    assert r.status == 401
