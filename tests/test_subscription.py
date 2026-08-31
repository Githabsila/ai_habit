"""
Подписка: первый платёж выставляет subscription_first_payment_at, цена
после первого платежа переключается со скидки на полную (has_ever_paid).
"""
from datetime import datetime, timedelta

from db import add_user
from db.subscription import (
    has_ever_paid,
    get_subscription_price_stars,
    record_subscription_payment,
)
import config


def test_first_payment_is_discounted_then_renewal_is_full_price(uid):
    add_user(uid, "tester", "Test")

    assert has_ever_paid(uid) is False
    assert get_subscription_price_stars(uid) == config.SUBSCRIPTION_INTRO_PRICE_STARS

    record_subscription_payment(uid, months=1)

    assert has_ever_paid(uid) is True
    assert get_subscription_price_stars(uid) == config.SUBSCRIPTION_RENEWAL_PRICE_STARS


def test_renewal_extends_from_existing_paid_until_not_from_today(uid):
    """Если подписка ещё активна, продление должно добавлять срок к уже
    оплаченной дате, а не начинать отсчёт заново от сегодня (иначе
    повторная оплата ДО истечения текущего периода "сжигала" бы
    оставшиеся дни)."""
    add_user(uid, "tester", "Test")

    first_until = record_subscription_payment(uid, months=1)
    second_until = record_subscription_payment(uid, months=1)

    assert second_until > first_until
    # ~30 дней разницы между продлениями, не "с нуля от сегодня"
    assert (second_until - first_until) >= timedelta(days=29)
