"""
Раздел «Триал» в аналитике (ТЗ 28.10.2026 §26–§27): путь людей, дни 2/3/4, ранние и поздние покупки, возвраты, «отправлено / показано / нажато» отдельно, нагрузка уведомлениями,
реальные и тестовые люди порознь, источники с переходом к людям и пометкой «подозрительная регистрация».
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

import config
from db import add_user, set_timezone, start_trial_mode, trial_flow, trial_stats
from db.access_state import set_trial_start
from db.core import connect
from tests.conftest import sign_init_data

UTC = timezone.utc
ADMIN = 777
_counter = itertools.count(866_500_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _fmt(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _person(uid_, started_days_ago=4, source=None):
    add_user(uid_, f"s{uid_}", "Аня")
    set_timezone(uid_, "UTC")
    start_trial_mode(uid_, ADMIN)
    started = (datetime.now(UTC) - timedelta(days=started_days_ago)).replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=None)
    set_trial_start(uid_, started)
    if source:
        _sql("UPDATE users SET source_code=? WHERE telegram_id=?", (source, uid_))
    return uid_, started


def _open(uid_, at):
    _sql("INSERT INTO user_events(user_id, at, name, surface) VALUES (?, ?, 'session_start', 'app')", (uid_, _fmt(at)))


def _pay(uid_, charge, at, test=False):
    _sql("INSERT INTO stars_payments(charge_id, user_id, payload, amount, created_at, purchased_at, is_test, status) VALUES (?, ?, 'x', 153, ?, ?, ?, 'done')",
         (charge, uid_, _fmt(at), _fmt(at), 1 if test else 0))


def _mark_real(monkeypatch, *people):
    """Для проверки «реальных»: гейт включён, аккаунт зарегистрирован после даты включения (20 дней назад); остальные — раньше (40 дней назад)."""
    cutover = (datetime.now(UTC) - timedelta(days=20)).date().isoformat()
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", cutover)
    _sql("UPDATE users SET created_at=datetime('now', '-40 days') WHERE source_code='chan_a'")
    for person in people:
        _sql("UPDATE users SET created_at=datetime('now', '-10 days') WHERE telegram_id=?", (person,))


def test_real_and_test_people_are_counted_apart(uid, monkeypatch):
    real, _ = _person(uid, source="chan_a")
    checker, _ = _person(uid + 1, source="chan_a")
    _mark_real(monkeypatch, real)
    assert trial_stats.overview(days=30, source="chan_a", scope="real")["started"] == 1
    assert trial_stats.overview(days=30, source="chan_a", scope="test")["started"] == 1
    assert trial_stats.overview(days=30, source="chan_a", scope="all")["started"] == 2
    assert trial_stats.overview(days=30, source="chan_a", scope="boom")["scope"] == "real"
    assert trial_stats.overview(days=30, source="nothing_here", scope="all")["started"] == 0


def test_days_purchases_and_returns_follow_each_persons_own_trial(uid):
    early_pay, started = _person(uid, source="chan_b")
    day = timedelta(days=1)
    for person, offsets in ((early_pay, (0, 1, 2)),):
        for offset in offsets:
            _open(person, started + offset * day + timedelta(hours=2))
    _pay(early_pay, f"e{early_pay}", started + day + timedelta(hours=3))
    waiter, started2 = _person(uid + 1, source="chan_b")
    for offset in (0, 1, 3, 9):
        _open(waiter, started2 + offset * day + timedelta(hours=2))
    _pay(waiter, f"w{waiter}", started2 + 3 * day + timedelta(hours=6))
    late, started3 = _person(uid + 2, source="chan_b")
    _pay(late, f"l{late}", started3 + 6 * day)
    skipped, started4 = _person(uid + 3, source="chan_b")
    _pay(skipped, f"t{skipped}", started4 + day, test=True)
    data = trial_stats.overview(days=30, source="chan_b", scope="all")
    assert data["started"] == 4
    assert (data["day2"], data["day3"], data["day4"]) == (2, 1, 1)
    assert (data["early_purchases"], data["window_purchases"], data["late_purchases"], data["paid"]) == (1, 1, 1, 3), "тестовая оплата не в счёт"
    assert data["returned_after_window"] == 1
    assert data["by_source"] == [{"source": "chan_b", "users": 4, "paid": 3}]
    assert sum(data["hours"]) == 7 and data["hours"][10] == 7, "семь заходов, все в 10 утра по местному времени (старт в 8:00 + 2 часа)"


def test_sent_shown_and_clicked_are_different_events_and_the_load_is_visible(uid):
    person, started = _person(uid, source="chan_c")
    _sql("INSERT INTO user_events(user_id, at, name, step, surface) VALUES (?, ?, 'trial_msg_sent', 'd3_end', 'server')", (person, _fmt(datetime.now(UTC))))
    _sql("INSERT INTO user_events(user_id, at, name, step, surface) VALUES (?, ?, 'trial_msg_open', 'd3_end', 'app')", (person, _fmt(datetime.now(UTC))))
    trial_flow.record(person, "push", "d3_end", "sent")
    trial_flow.record(person, "app", "d2_app_habit", "shown")
    trial_flow.record(person, "app", "d2_app_habit", "clicked")
    trial_flow.record(person, "chat", "summary_ask", "dismissed")
    for category in ("habit_checkpoint_10", "habit_checkpoint_12", "trial:d3_end"):
        _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?, ?, 'x', ?)", (person, category, _fmt(datetime.now(UTC))))
    data = trial_stats.overview(days=30, source="chan_c", scope="all")
    assert {"key": "d3_end", "sent": 1, "opened": 1} in data["messages"]
    by = {(o["place"], o["key"]): o for o in data["offers"]}
    assert by[("app", "d2_app_habit")]["shown"] == 1 and by[("app", "d2_app_habit")]["clicked"] == 1 and by[("push", "d3_end")]["sent"] == 1
    assert by[("chat", "summary_ask")]["dismissed"] == 1
    assert data["notifications"]["total"] == 3 and data["notifications"]["max_per_user"] == 3 and data["notifications"]["per_user"] == 3.0
    assert "прочитано" not in str(data).lower()


def test_a_burst_of_registrations_from_one_source_is_flagged_for_review(uid):
    people = []
    for index in range(trial_stats.SUSPICIOUS_COUNT):
        person, _ = _person(uid + index, started_days_ago=1, source="burst_src")
        _sql("UPDATE users SET created_at=? WHERE telegram_id=?", (_fmt(datetime.now(UTC) - timedelta(minutes=index)), person))
        people.append(person)
    calm, _ = _person(uid + 50, started_days_ago=1, source="calm_src")
    flagged = trial_stats.suspicious_registrations()
    assert set(people) <= flagged and calm not in flagged
    listing = trial_stats.source_people("burst_src")
    assert len(listing) == trial_stats.SUSPICIOUS_COUNT and all(item["suspicious"] for item in listing)
    assert trial_stats.overview(days=30, source="burst_src", scope="all")["suspicious"] == trial_stats.SUSPICIOUS_COUNT
    assert "бот" not in str(listing).lower()


async def test_the_admin_routes_serve_the_stats_only_to_admins(client, uid, monkeypatch):
    import webapp.routes_admin as routes_admin
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [uid])
    add_user(uid, "adm", "Админ")
    person, _ = _person(uid + 1, source="route_src")
    headers = {"Authorization": f"tma {sign_init_data(uid)}"}
    body = await (await client.get("/api/admin/trial/stats?days=30&source=route_src&scope=all", headers=headers)).json()
    assert body["started"] == 1 and body["source"] == "route_src"
    people = await (await client.get("/api/admin/trial/source/route_src", headers=headers)).json()
    assert [p["user_id"] for p in people["people"]] == [person]
    stranger = {"Authorization": f"tma {sign_init_data(person)}"}
    assert (await client.get("/api/admin/trial/stats", headers=stranger)).status == 403
    assert (await client.get("/api/admin/trial/source/route_src", headers=stranger)).status == 403
