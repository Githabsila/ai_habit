"""
Телеметрия долгих кадров/задач Mini App (app.js::reportPerfEvent ->
POST /api/perf/report -> db.perf_events -> GET /api/admin/perf-events).

Тот же принцип, что и у client_errors (Улучшение #70): баг "лагает/
чернеет при прокрутке" в Telegram WebView на Android невозможно
воспроизвести или отладить с десктопа — точные цифры с реального
устройства заменяют разбор видео покадрово.
"""
from db import add_user, add_perf_event, get_recent_perf_events, get_perf_summary

from tests.conftest import sign_init_data


async def _headers(uid_):
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


async def _admin_headers(client, uid_, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid_])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [uid_])
    init_data = sign_init_data(uid_)
    return {"Authorization": f"tma {init_data}", "Content-Type": "application/json"}


# =====================================
# db.perf
# =====================================

def test_add_perf_event_persists_and_truncates(uid):
    add_perf_event(uid, "long_frame", 350, tab="x" * 100, path="y" * 500, is_scrolling=True, device_info="z" * 1000)
    row = next(r for r in get_recent_perf_events(limit=10) if r["user_id"] == uid)
    assert row["event_type"] == "long_frame"
    assert row["duration_ms"] == 350
    assert row["is_scrolling"] == 1
    assert len(row["tab"]) == 50
    assert len(row["path"]) == 300
    assert len(row["device_info"]) == 500


def test_get_recent_perf_events_orders_newest_first(uid):
    add_perf_event(uid, "long_frame", 210)
    add_perf_event(uid, "long_task", 80)
    rows = get_recent_perf_events(limit=2)
    assert rows[0]["event_type"] == "long_task"
    assert rows[1]["event_type"] == "long_frame"


def test_get_perf_summary_aggregates_by_event_type(uid):
    add_perf_event(uid, "long_frame", 200)
    add_perf_event(uid, "long_frame", 400)
    add_perf_event(uid, "long_task", 60)
    summary = {row["event_type"]: row for row in get_perf_summary()}
    assert summary["long_frame"]["count"] >= 2
    assert summary["long_frame"]["max_ms"] >= 400
    assert summary["long_task"]["count"] >= 1


# =====================================
# POST /api/perf/report
# =====================================

async def test_perf_report_route_accepts_and_stores(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/perf/report", headers=headers,
        json={"event_type": "long_frame", "duration_ms": 320, "tab": "home", "path": "/", "is_scrolling": True},
    )
    assert r.status == 204

    row = next(r for r in get_recent_perf_events(limit=5) if r["user_id"] == uid)
    assert row["event_type"] == "long_frame"
    assert row["duration_ms"] == 320
    assert row["tab"] == "home"
    assert row["is_scrolling"] == 1


async def test_perf_report_route_rejects_unknown_event_type(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/perf/report", headers=headers,
        json={"event_type": "totally_made_up", "duration_ms": 320},
    )
    assert r.status == 204
    assert not any(row["user_id"] == uid for row in get_recent_perf_events(limit=20))


async def test_perf_report_route_rejects_zero_duration(client, uid):
    add_user(uid, "u", "Test")
    headers = await _headers(uid)
    r = await client.post(
        "/api/perf/report", headers=headers,
        json={"event_type": "long_frame", "duration_ms": 0},
    )
    assert r.status == 204
    assert not any(row["user_id"] == uid for row in get_recent_perf_events(limit=20))


async def test_perf_report_route_never_errors_on_bad_auth(client):
    # Как и /api/client-error — репортер телеметрии не должен уметь сам
    # уронить что-то ещё из-за невалидного initData.
    r = await client.post(
        "/api/perf/report",
        headers={"Authorization": "tma garbage", "Content-Type": "application/json"},
        json={"event_type": "long_frame", "duration_ms": 320},
    )
    assert r.status == 204


# =====================================
# GET /api/admin/perf-events
# =====================================

async def test_regular_user_gets_403_on_perf_events(client, uid):
    add_user(uid, "u", "Test")
    init_data = sign_init_data(uid)
    r = await client.get("/api/admin/perf-events", headers={"Authorization": f"tma {init_data}"})
    assert r.status == 403


async def test_admin_sees_perf_events(client, uid, monkeypatch):
    headers = await _admin_headers(client, uid, monkeypatch)
    add_user(uid, "admin", "Admin")
    victim = uid + 50_000_000
    add_perf_event(victim, "long_frame", 450, tab="home", is_scrolling=True)

    r = await client.get("/api/admin/perf-events", headers=headers)
    assert r.status == 200
    body = await r.json()
    assert any(e["user_id"] == victim and e["duration_ms"] == 450 for e in body["events"])
    assert any(s["event_type"] == "long_frame" for s in body["summary"])
