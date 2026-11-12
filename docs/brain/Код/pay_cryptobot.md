# pay_cryptobot.py

Оплата криптовалютой через Crypto Bot (@CryptoBot, Crypto Pay API — https://help.crypt.bot/crypto-pay-api), 11.11: запасной способ рядом со звёздами и Tribute.

Как идёт оплата: на каждый наш заказ создаём счёт в долларах (currency_type=fiat, fiat=USD) — человек платит любой поддерживаемой
криптовалютой (USDT, TON, BTC…) прямо в Telegram, ссылка вида t.me/CryptoBot?start=… открывается без браузера и без входа.
В счёте лежит наш номер заказа (payload). Crypto Bot присылает вебхук invoice_paid, подписанный HMAC-SHA256 тела с ключом SHA256(токен)
в заголовке crypto-pay-api-signature. Дальше — общий путь webapp/routes_pay.py: сверка суммы, отметка оплаты, выдача (payments_ext.fulfil).

Токен (CRYPTOBOT_API_TOKEN) задаёт владелец в Railway; в код и в чат он не попадает.

Функции: [[Crypto Bot]]

#код
