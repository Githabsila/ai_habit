"""
Прогон 07.11 (зона bot-schedulers, B2): обработчик магазина ловил F.data.startswith("buy_") — и «Оплатить подписку» (buy_subscription),
и «Купить Premium» (buy_premium). Роутер магазина подключён раньше оплаты, падал на int("subscription"), и до оплаты апдейт не доходил:
человек с закрытым доступом жал «Оплатить» в боте — и ничего.
"""
from types import SimpleNamespace

import handlers.shop as shop


def _handler(router, name):
    return next(h for h in router.callback_query.handlers if h.callback.__name__ == name)


async def test_review_the_shop_does_not_take_the_subscription_and_premium_buttons():
    buy_item = _handler(shop.router, "buy_item")
    for data in ("buy_subscription", "buy_premium"):
        matched, _ = await buy_item.check(SimpleNamespace(data=data))
        assert not matched, data


async def test_review_the_shop_still_takes_its_own_items():
    buy_item = _handler(shop.router, "buy_item")
    matched, _ = await buy_item.check(SimpleNamespace(data="buy_7"))
    assert matched
