"""
Эксперимент «до 5 или до 7 напоминаний в день» (db/reminder_experiment.py): группы по флагу reminders_7, цифры по группам —
напоминаний в день, «сработало» (отметка в течение часа), выключили напоминания, заблокировали бота.
"""
from datetime import datetime, timedelta

import config
import db.reminder_experiment as rx
from db import add_habit, add_user
from db.core import connect
from db.feature_flags import set_feature_flag
from tests.conftest import sign_init_data

NOW = datetime(2026, 11, 6, 20, 0)


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def test_groups_follow_the_flag(uid):
    set_feature_flag(rx.FLAG, False, 0)
    assert rx.reminder_group(uid) == rx.GROUP_5
    set_feature_flag(rx.FLAG, True, 100)
    try:
        assert rx.reminder_group(uid) == rx.GROUP_7
    finally:
        set_feature_flag(rx.FLAG, False, 0)


def test_stats_count_reminders_follow_ups_and_blocks(uid, monkeypatch):
    five, seven = uid, uid + 1
    for person in (five, seven):
        add_user(person, "p", "Аня")
        add_habit(person, "Вода")
    monkeypatch.setattr(rx, "reminder_group", lambda u: rx.GROUP_7 if u == seven else rx.GROUP_5)
    t = NOW - timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    for person, kinds in ((five, ["habit_checkpoint_12", "risk23"]), (seven, ["habit_checkpoint_10", "habit_checkpoint_12", "weekly_report"])):
        for i, kind in enumerate(kinds):
            _sql("INSERT INTO notification_log(user_id, category, title, sent_at) VALUES (?,?,?,?)",
                 (person, kind, kind, (t + timedelta(hours=i)).strftime(fmt)))
    _sql("INSERT INTO user_events(user_id, at, name) VALUES (?,?,?)", (five, (t + timedelta(minutes=20)).strftime(fmt), "habit_completed"))
    _sql("UPDATE users SET bot_blocked_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (seven,))

    data = rx.stats(days=7, now=NOW)
    g5, g7 = data["groups"]["5"], data["groups"]["7"]
    assert g5["reminders"] >= 2 and g7["reminders"] >= 2
    assert g5["followed_pct"] is not None and g5["followed_pct"] > 0, "отметка через 20 минут после напоминания — «сработало»"
    assert g7["blocked_pct"] and g7["blocked_pct"] > 0


async def test_the_admin_route(client, uid, monkeypatch):
    admin = uid + 900
    add_user(admin, "adm", "Админ")
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    monkeypatch.setattr("webapp.routes_admin.ADMIN_IDS", [admin])
    response = await client.get("/api/admin/reminder-experiment?days=7", headers={"Authorization": f"tma {sign_init_data(admin)}"})
    data = await response.json()
    assert response.status == 200 and set(data["groups"]) == {"5", "7"} and data["days"] == 7 and "enabled" in data["flag"]
    assert (await client.get("/api/admin/reminder-experiment", headers={"Authorization": f"tma {sign_init_data(uid)}"})).status == 403
