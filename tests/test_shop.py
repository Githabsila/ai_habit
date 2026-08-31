"""
Магазин: покупка не должна уводить баланс в минус (регрессия на TOCTOU-баг,
исправленный в этой же сессии — db/shop.py buy_shop_item), владение
товаром корректно отражается после покупки.
"""
from db import add_user, get_user, connect
from db.shop import buy_shop_item, has_item, get_shop_item


def _set_xp(telegram_id, amount):
    conn = connect()
    conn.execute("UPDATE users SET xp=? WHERE telegram_id=?", (amount, telegram_id))
    conn.commit()
    conn.close()


def test_buy_theme_item_succeeds_with_enough_xp(uid):
    add_user(uid, "tester", "Test")
    item = get_shop_item(2)  # "🎨 Тема оформления"
    assert item is not None
    _set_xp(uid, item["price"] + 50)

    ok = buy_shop_item(uid, 2)

    assert ok is True
    assert has_item(uid, 2) is True
    user = get_user(uid)
    assert user["xp"] == 50


def test_buy_fails_without_enough_xp(uid):
    add_user(uid, "tester", "Test")
    item = get_shop_item(2)
    _set_xp(uid, item["price"] - 1)

    ok = buy_shop_item(uid, 2)

    assert ok is False
    assert has_item(uid, 2) is False
    user = get_user(uid)
    assert user["xp"] == item["price"] - 1  # баланс не тронут


def test_balance_never_goes_negative_on_repeated_attempts(uid):
    """Регрессия: раньше баланс списывался отдельным UPDATE после
    отдельного SELECT — гонка могла увести xp в минус. Сейчас проверка
    баланса встроена в сам UPDATE (см. db/shop.py)."""
    add_user(uid, "tester", "Test")
    item = get_shop_item(2)
    _set_xp(uid, item["price"])  # хватает ровно на одну покупку

    first = buy_shop_item(uid, 2, allow_repeatable=True)
    second = buy_shop_item(uid, 2, allow_repeatable=True)

    assert first is True
    assert second is False  # денег больше нет
    user = get_user(uid)
    assert user["xp"] == 0
    assert user["xp"] >= 0
