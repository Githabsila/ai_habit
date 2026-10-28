"""
Tribute (tribute.tg) — оплата картой Visa/Mastercard и не только, внутри Telegram. Решение владельца 28.10.2026: карту подключаем через Tribute (подходит физлицам, нужен KYC).
Документация: https://wiki.tribute.tg/ru/for-content-creators/digital-product/api-integration, спецификация https://tribute.tg/api/v1/openapi/ru.

Как это устроено (отличается от Heleket): у Tribute нет «создать счёт на конкретного человека». Каждый цифровой товар имеет свою ссылку на оплату (`link`), по ней платит кто угодно, а кто именно —
сообщает вебхук после оплаты (`telegram_user_id`, уникальный `purchase_id`). Поэтому:
  • для каждой нашей цены (db/pay_orders.py::all_price_keys: подписка, пакеты ответов, алмазы, бустер, рамка) владелец заводит в Tribute цифровой товар с этой ценой в долларах и привязывает его в админке
    («Tribute: товары»); привязка проверяется по цене и статусу товара;
  • человек выбирает «Картой» → создаётся наш заказ и открывается ссылка этого товара; после оплаты приходит вебхук, мы находим его открытый заказ (по человеку и покупке) и выдаём
    покупку один раз; если оплату сделали по ссылке не из приложения — заказ создаётся сразу оплаченным, деньги не теряются;
  • идемпотентность — по `purchase_id` (таблица pay_events): повтор вебхука ничего не добавляет;
  • подпись — заголовок `trbt-signature` = HMAC-SHA256 тела запроса ключом API (hex);
  • возврат (`digital_product_refund`) заказ помечает возвращённым и сообщает админам: доступ автоматически не отзываем, решает владелец.
Ключ API (`TRIBUTE_API_KEY`) владелец задаёт в Railway сам — в код и в чат он не попадает.
"""
import base64
import hashlib
import hmac
import json
import logging

import aiohttp

import config
from db import pay_orders

logger = logging.getLogger(__name__)
API_BASE = "https://tribute.tg/api/v1"


class Tribute:
    name = "tribute"
    method = "card"
    label = "Картой"

    @staticmethod
    def _key():
        return str(getattr(config, "TRIBUTE_API_KEY", "") or "").strip()

    def configured(self):
        return bool(self._key())

    def available_for(self, price_key):
        """Картой можно платить только за то, под что в Tribute заведён и привязан товар."""
        if price_key is None:
            return bool(pay_orders.tribute_products())                 # общий список способов: карта есть, если привязана хотя бы одна цена
        return bool(pay_orders.tribute_product(price_key))

    # ------------------------------------------------------------------------------------------------------------ заказ

    async def create_invoice(self, order, amount_cents):
        from pay_providers import ProviderError
        product = pay_orders.tribute_product(order["price_key"]) if order["price_key"] else None
        if product is None or not product["link"]:
            raise ProviderError("товар Tribute под эту цену не привязан")
        return {"uuid": "", "url": product["link"]}

    # ------------------------------------------------------------------------------------------------------------ вебхук

    def parse_webhook(self, raw_body, headers=None):
        """Подпись — заголовок trbt-signature: HMAC-SHA256 тела ключом API. Принимаем запись в hex (как у большинства сервисов) и base64; неверная подпись или нет ключа — None."""
        key = self._key()
        received = str((headers or {}).get("trbt-signature") or "").strip()
        if not key or not received:
            return None
        digest = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha256).digest()
        allowed = (digest.hex(), base64.b64encode(digest).decode())
        if not any(hmac.compare_digest(received.lower() if len(received) == 64 else received, candidate) for candidate in allowed):
            return None
        try:
            data = json.loads(raw_body)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def handle(self, payload, bot):
        """Обработка подтверждённого вебхука. Возвращает тело ответа для Tribute ({"status": "ok"})."""
        name = str(payload.get("name") or "")
        data = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if name == "new_digital_product":
            return await self._on_purchase(data, bot)
        if name == "digital_product_refund":
            return await self._on_refund(data, bot)
        return {"status": "ok", "ignored": True}                       # подписки, донаты и прочее — это не наши покупки

    async def _on_purchase(self, data, bot):
        import payments_ext
        from db.users import get_user
        purchase_id = str(data.get("purchase_id") or "").strip()
        if not purchase_id:
            await _alert(bot, f"⚠️ Tribute прислал покупку без purchase_id: {str(data)[:200]}. Выдай вручную, если человек заплатил.")
            return {"status": "ok", "ignored": True}
        if pay_orders.event_seen(self.name, f"p{purchase_id}"):
            return {"status": "ok", "duplicate": True}
        product = pay_orders.tribute_by_product(data.get("product_id") or 0)
        user_id = int(data.get("telegram_user_id") or 0)
        if product is None:
            await _alert(bot, f"⚠️ В Tribute куплен товар, которого нет в привязке: product_id={data.get('product_id')} «{data.get('product_name')}», покупатель {user_id}, "
                              f"покупка {purchase_id}. Привяжи товар в админке или выдай покупку вручную.")
            return {"status": "ok", "ignored": True}
        purchase = pay_orders.key_to_purchase(product["key"])
        if purchase is None or not user_id or get_user(user_id) is None:
            await _alert(bot, f"⚠️ Tribute: покупка {purchase_id} ({product['key']}) от неизвестного человека {user_id}. Выдай вручную после проверки.")
            return {"status": "ok", "ignored": True}
        kind, item_id, expected_cents = purchase
        if int(data.get("amount") or 0) != expected_cents or str(data.get("currency") or "").lower() != "usd":
            await _alert(bot, f"⚠️ Tribute: покупка {purchase_id} на другую сумму ({data.get('amount')} {data.get('currency')}, ждали {expected_cents} usd), человек {user_id}. "
                              "Покупка не выдана — проверь цену товара в Tribute.")
            return {"status": "ok", "ignored": True}
        order = pay_orders.find_open(user_id, self.name, kind, item_id)
        if order is None:                                               # оплатили по ссылке не из приложения: заказ создаём уже оплаченным
            order = pay_orders.create_order(user_id, self.name, self.method, kind, item_id, expected_cents, "USD", product["key"])
        pay_orders.set_provider_uuid(order["order_id"], purchase_id)
        paid = pay_orders.mark_paid(order["order_id"], "paid")
        await payments_ext.fulfil(bot, paid)
        pay_orders.record_event(self.name, f"p{purchase_id}")
        return {"status": "ok"}

    async def _on_refund(self, data, bot):
        purchase_id = str(data.get("purchase_id") or "").strip()
        if not purchase_id or pay_orders.event_seen(self.name, f"r{purchase_id}"):
            return {"status": "ok", "ignored": True}
        order = pay_orders.find_by_provider_uuid(self.name, purchase_id)
        if order is not None:
            pay_orders.mark_refunded(order["order_id"])
        who = order["user_id"] if order is not None else data.get("telegram_user_id")
        await _alert(bot, f"↩️ Tribute вернул оплату: покупка {purchase_id}, человек {who}, сумма {data.get('amount')} {data.get('currency')}. "
                          "Доступ автоматически не отзываю — реши, нужно ли его снять.")
        pay_orders.record_event(self.name, f"r{purchase_id}")
        return {"status": "ok"}

    # ------------------------------------------------------------------------------------------------------------ товары

    async def _get(self, path):
        headers = {"Api-Key": self._key(), "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(f"{API_BASE}{path}", headers=headers) as response:
                    body = await response.json(content_type=None)
                    return response.status, body
        except Exception as exc:
            return 0, {"error": type(exc).__name__}

    async def fetch_product(self, product_id):
        """Товар из Tribute по номеру: (товар или None, текст ошибки)."""
        status, body = await self._get(f"/products/{int(product_id)}")
        if status == 200 and isinstance(body, dict) and body.get("id"):
            return body, None
        return None, {401: "Tribute не принял ключ API", 404: "такого товара нет"}.get(status, f"Tribute не ответил ({status})")

    async def list_products(self, size=100):
        status, body = await self._get(f"/products?size={int(size)}&desc=true")
        rows = body.get("rows") if status == 200 and isinstance(body, dict) else None
        return rows if isinstance(rows, list) else []

    async def register_product(self, key, product_id):
        """Привязывает товар Tribute к нашей цене. Проверяем: ключ известен, товар одобрен, валюта USD, цена в центах равна нашей. {"ok"} или {"error", "detail"}."""
        purchase = pay_orders.key_to_purchase(key)
        if purchase is None:
            return {"error": "unknown_key"}
        product, error = await self.fetch_product(product_id)
        if product is None:
            return {"error": "product_not_found", "detail": error}
        expected = purchase[2]
        if str(product.get("currency") or "").upper() != "USD" or int(product.get("amount") or 0) != expected:
            return {"error": "amount_mismatch", "detail": f"в Tribute {product.get('amount')} {product.get('currency')}, нужно {expected} USD (центы)"}
        if str(product.get("status") or "") != "approved":
            return {"error": "not_approved", "detail": f"товар в Tribute пока «{product.get('status')}»"}
        pay_orders.set_tribute_product(key, product["id"], product.get("name") or "", expected, "USD", product.get("link"), product.get("webLink"))
        return {"ok": True}


async def _alert(bot, text):
    logger.warning(text)
    if bot is None:
        return
    for admin_id in getattr(config, "ADMIN_IDS", []):
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.info("Не удалось сообщить админу %s о событии Tribute", admin_id)
