"""
Telegram изредка может повторно доставить update с successful_payment
(сетевой сбой/рестарт бота между получением апдейта и обработкой) —
db.is_payment_processed/mark_payment_processed (см. db/shop.py,
использование в handlers/payments.py) не дают начислить награду дважды
за одну и ту же оплату Stars.
"""
from db import is_payment_processed, mark_payment_processed


def test_new_charge_id_is_not_processed(uid):
    assert is_payment_processed(f"charge_{uid}") is False


def test_marked_charge_id_is_processed(uid):
    charge_id = f"charge_{uid}"
    mark_payment_processed(charge_id, uid, "subscription:" + str(uid), 153)
    assert is_payment_processed(charge_id) is True


def test_marking_same_charge_id_twice_does_not_raise(uid):
    """INSERT OR IGNORE — повторная доставка того же charge_id не должна
    падать с ошибкой UNIQUE constraint, а просто быть проигнорирована."""
    charge_id = f"charge_{uid}"
    mark_payment_processed(charge_id, uid, "subscription:" + str(uid), 153)
    mark_payment_processed(charge_id, uid, "subscription:" + str(uid), 153)
    assert is_payment_processed(charge_id) is True


def test_empty_charge_id_is_never_processed(uid):
    """Пустой/None charge_id не должен ломать проверку (напр. в тестовом
    окружении без реальных Stars-платежей)."""
    assert is_payment_processed(None) is False
    assert is_payment_processed("") is False
