"""
«Сегодняшний» план дня — по часовому поясу человека (найдено 06.11 при работе над планом дня).

Раньше дата плана считалась по часам сервера (UTC) со сдвигом до 03:00: в Казахстане (UTC+5) до 08:00 утра человек видел вчерашний
план, в Новосибирске (UTC+7) — до 10:00. Задумано: план «сегодняшний» до 03:00 ночи по МЕСТНОМУ времени (многие работают ночью).
"""
from datetime import datetime

import db.daily_plan as dp
from db import add_user
from db.streak import set_timezone


def _freeze(monkeypatch, utc_moment):
    from datetime import timezone

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = utc_moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(dp, "datetime", Frozen)


def test_morning_in_almaty_is_already_today(uid, monkeypatch):
    add_user(uid, "a", "Айдар")
    set_timezone(uid, "Asia/Almaty")                         # UTC+5
    _freeze(monkeypatch, datetime(2026, 11, 6, 2, 30))      # 07:30 в Алматы
    assert dp._effective_plan_date(uid) == "2026-11-06"


def test_after_midnight_until_3_it_is_still_yesterday_locally(uid, monkeypatch):
    add_user(uid, "a", "Аня")
    set_timezone(uid, "Europe/Moscow")                       # UTC+3
    _freeze(monkeypatch, datetime(2026, 11, 5, 23, 30))     # 02:30 6 ноября в Москве — ещё «5 ноября»
    assert dp._effective_plan_date(uid) == "2026-11-05"
    _freeze(monkeypatch, datetime(2026, 11, 6, 0, 30))      # 03:30 в Москве — уже 6 ноября
    assert dp._effective_plan_date(uid) == "2026-11-06"


def test_the_plan_itself_uses_the_local_day(uid, monkeypatch):
    add_user(uid, "a", "Айдар")
    set_timezone(uid, "Asia/Novosibirsk")                    # UTC+7
    _freeze(monkeypatch, datetime(2026, 11, 6, 2, 0))       # 09:00 в Новосибирске
    dp.add_daily_task(uid, "Позвонить клиенту")
    conn = dp.connect()
    try:
        row = conn.execute("SELECT plan_date FROM daily_plans WHERE user_id=?", (uid,)).fetchone()
    finally:
        conn.close()
    assert row["plan_date"] == "2026-11-06"
