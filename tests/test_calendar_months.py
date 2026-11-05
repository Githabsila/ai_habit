"""
Оплата картой продлевает подписку календарными месяцами с «якорным» числом (решение владельца 05.11: «если в следующем месяце нет 31-го —
доступ клиенту не обрезать»). У звёзд Telegram — по-прежнему 30 дней: подписка Stars другой не бывает.
"""
from datetime import datetime

from db import add_user, get_user
from db.core import connect
from db.subscription import add_calendar_months, record_subscription_payment


def _until(user_id):
    return datetime.fromisoformat(get_user(user_id)["subscription_paid_until"])


def _set_until(user_id, value):
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", (value.isoformat() if value else None, user_id))
        conn.commit()
    finally:
        conn.close()


def test_add_calendar_months_keeps_the_anchor_through_short_months():
    jan31 = datetime(2027, 1, 31, 14, 5)
    assert add_calendar_months(jan31, 1, 31) == datetime(2027, 2, 28, 14, 5)          # февраля 31-го нет — последний день, доступ не обрезан
    assert add_calendar_months(datetime(2027, 2, 28, 14, 5), 1, 31) == datetime(2027, 3, 31, 14, 5)   # и снова 31-е, а не 28-е
    assert add_calendar_months(datetime(2028, 1, 30), 1, 30) == datetime(2028, 2, 29)  # високосный
    assert add_calendar_months(datetime(2027, 11, 15), 2, 15) == datetime(2028, 1, 15)  # через границу года
    assert add_calendar_months(datetime(2027, 4, 30), 1, 31) == datetime(2027, 5, 31)


def test_card_renewals_follow_the_anchor_day(uid):
    add_user(uid, "c", "Аня")
    first = record_subscription_payment(uid, paid_at=datetime(2027, 1, 31, 10, 0), calendar_months=True)
    assert first == datetime(2027, 2, 28, 10, 0) and get_user(uid)["subscription_anchor_day"] == 31
    # продление пришло заранее (подписка ещё идёт) — от конца срока, с прежним якорем
    second = record_subscription_payment(uid, paid_at=datetime(2027, 2, 27, 9, 0), calendar_months=True)
    assert second == datetime(2027, 3, 31, 10, 0)
    third = record_subscription_payment(uid, paid_at=datetime(2027, 3, 30, 9, 0), calendar_months=True)
    assert third == datetime(2027, 4, 30, 10, 0)


def test_after_a_break_the_anchor_restarts_from_the_new_payment(uid):
    add_user(uid, "c", "Аня")
    record_subscription_payment(uid, paid_at=datetime(2027, 1, 31, 10, 0), calendar_months=True)
    _set_until(uid, datetime(2027, 2, 28, 10, 0))
    restarted = record_subscription_payment(uid, paid_at=datetime(2027, 6, 12, 18, 0), calendar_months=True)   # срок давно истёк
    assert restarted == datetime(2027, 7, 12, 18, 0) and get_user(uid)["subscription_anchor_day"] == 12


def test_stars_payments_still_add_30_days(uid):
    add_user(uid, "s", "Аня")
    until = record_subscription_payment(uid, paid_at=datetime(2027, 1, 31, 10, 0))
    assert until == datetime(2027, 3, 2, 10, 0) and get_user(uid)["subscription_anchor_day"] is None
