# pay_lava.py

Lava.top — оплата картами и СБП (решение владельца 07.11: Tribute и для Армении показывал только СБП и звёзды).
Документация: https://gate.lava.top/docs (OpenAPI https://gate.lava.top/docs/documentation.yaml, версия 1.22.0).

Три способа оплаты у человека, один сервис и один вебхук:
  • lava      — «Картой Visa / Mastercard», доллары (провайдер UNLIMINT): СНГ и остальной мир;
  • lava_rub  — «Картой «Мир»», рубли (SMART_GLOCAL);
  • lava_sbp  — «СБП», рубли (PAY2ME).

Как идёт оплата: на каждую нашу цену (db/pay_orders.py::all_price_keys) у владельца в Lava заведён продукт с оффером (offerId) — цены в USD
и в RUB; привязка «ключ → оффер» — в админке (auto_link по цене в USD и названию, как у Tribute). Заказ создаёт счёт POST /api/v3/invoice
(почта покупателя обязательна — для чека; номер нашего заказа уходит в clientUtm.utm_content и возвращается в вебхуке), человек платит
на странице Lava, Lava присылает вебхук с заголовком X-Api-Key = LAVA_WEBHOOK_SECRET (его владелец задаёт в кабинете Lava сам).

Функции: [[Lava]]

#код
