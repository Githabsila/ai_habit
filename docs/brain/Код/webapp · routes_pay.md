# webapp/routes_pay.py

Маршруты оплаты картой и криптой (payments_ext.py, pay_providers.py, db/pay_orders.py). Звёзды идут прежними маршрутами (/api/subscription/invoice, /api/shop/stars/{id}).

  GET  /api/pay/methods?kind=subscription|item&item_id=  — какие способы показать и по какой цене
  POST /api/pay/order {method, kind, item_id}            — создаёт заказ, возвращает pay_url (куда открыть оплату)
  GET  /api/pay/order/{order_id}                         — состояние заказа (приложение ждёт подтверждения после возврата с оплаты)
  POST /api/pay/webhook/{provider}                       — подтверждение от платёжного сервиса; без входа в приложение, но с проверкой подписи
  GET  /api/admin/pay/orders, POST /api/admin/pay/orders/{order_id}/fulfil — админка: список заказов и повтор начисления, если деньги пришли, а покупка не выдалась

Функции: [[Оплата]]

#код
