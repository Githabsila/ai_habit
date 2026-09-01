"""Уведомления "в 100 раз лучше" — история отправленных push-ов, единая
точка через claim_notification/release_notification (db/streak.py)."""
from db import add_user, claim_notification, release_notification, get_notification_history

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}"}


def test_claim_notification_logs_history(uid):
    add_user(uid, "u", "Test")
    claim_notification(uid, "2026-01-01", "weekly_report")
    history = get_notification_history(uid)
    assert len(history) == 1
    assert history[0]["category"] == "weekly_report"
    assert "Итог недели" in history[0]["label"]


def test_release_notification_removes_from_history(uid):
    add_user(uid, "u", "Test")
    claim_notification(uid, "2026-01-01", "morning_6")
    release_notification(uid, "2026-01-01", "morning_6")
    assert get_notification_history(uid) == []


def test_unknown_kind_gets_generic_label(uid):
    add_user(uid, "u", "Test")
    claim_notification(uid, "2026-01-01", "some_new_kind")
    history = get_notification_history(uid)
    assert history[0]["label"] == "🔔 some new kind"


def test_history_ordered_newest_first(uid):
    add_user(uid, "u", "Test")
    claim_notification(uid, "2026-01-01", "morning_6")
    claim_notification(uid, "2026-01-02", "weekly_bonus")
    history = get_notification_history(uid)
    assert history[0]["category"] == "weekly_bonus"
    assert history[1]["category"] == "morning_6"


async def test_notification_history_route(client, uid):
    add_user(uid, "u", "Test")
    claim_notification(uid, "2026-01-01", "weekly_report")
    r = await client.get("/api/notifications/history", headers=await _headers(uid))
    assert r.status == 200
    body = await r.json()
    assert len(body["history"]) == 1
