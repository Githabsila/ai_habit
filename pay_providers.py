"""
Платёжные сервисы для оплаты не звёздами (db/pay_orders.py, payments_ext.py). Каждый сервис — класс с одинаковыми методами, чтобы подключение нового занимало один файл:

  configured()                           — заданы ли ключи (переменные окружения на Railway); без них способ оплаты не показывается
  create_invoice(order, amount_cents)    — создаёт счёт у сервиса, возвращает {"uuid", "url"} (ссылка, куда отправить человека платить)
  parse_webhook(raw_body, headers)       — проверяет подпись подтверждения и возвращает его содержимое либо None (подделку не принимаем)
  outcome(payload)                       — ("paid" | "failed" | "pending", сырой статус)
  paid_amount_matches(payload, order)    — сумма в подтверждении совпала с суммой заказа

Подключено сейчас: Heleket (криптовалюта) — по его документации https://doc.heleket.com/ (запрос и вебхук подписываются MD5 от base64 тела и платёжного API-ключа).
Карта Visa/Mastercard: Tribute (pay_tribute.py) — решение владельца 28.10; у него нет «счёта на человека», вместо этого ссылки заранее заведённых товаров и вебхук с номером покупки.

Ключи в код не пишутся и мне не присылаются: HELEKET_MERCHANT_ID и HELEKET_API_KEY владелец задаёт в Railway сам.
"""
import base64
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

import aiohttp

import config

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Сервис не создал счёт или ответил ошибкой; текст безопасен для журнала (без ключей)."""


class Heleket:
    name = "heleket"
    method = "crypto"
    label = "Криптой"
    api_url = "https://api.heleket.com/v1/payment"
    PAID = {"paid", "paid_over"}
    FAILED = {"fail", "cancel", "system_fail", "refund_process", "refund_fail", "refund_paid"}

    # ключи читаем при каждом обращении: их можно поменять переменными окружения без правок кода
    @staticmethod
    def _merchant():
        return str(getattr(config, "HELEKET_MERCHANT_ID", "") or "").strip()

    @staticmethod
    def _key():
        return str(getattr(config, "HELEKET_API_KEY", "") or "").strip()

    def configured(self):
        return bool(self._merchant() and self._key())

    def available_for(self, price_key):
        return True                              # счёт на любую сумму создаётся по запросу

    # --- подпись: MD5(base64(тело) + ключ). Слэши в теле экранируются как в PHP (json_encode по умолчанию), чтобы подпись совпала при любом способе проверки.
    @staticmethod
    def dump(data):
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("/", "\\/")

    def sign_text(self, text):
        return hashlib.md5((base64.b64encode(text.encode("utf-8")).decode() + self._key()).encode("utf-8")).hexdigest()

    async def create_invoice(self, order, amount_cents):
        base = str(getattr(config, "WEBAPP_URL", "") or "").rstrip("/")
        body = {
            "amount": f"{int(amount_cents) // 100}.{int(amount_cents) % 100:02d}", "currency": "USD", "order_id": order["order_id"], "lifetime": 3600,
            "is_payment_multiple": False, "additional_data": str(order["user_id"])[:255],
        }
        if base:
            body.update({"url_callback": f"{base}/api/pay/webhook/{self.name}", "url_return": base, "url_success": f"{base}/?m=pay_ok"})
        text = self.dump(body)
        headers = {"merchant": self._merchant(), "sign": self.sign_text(text), "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(self.api_url, data=text.encode("utf-8"), headers=headers) as response:
                    payload = await response.json(content_type=None)
        except Exception as exc:
            raise ProviderError(f"сервис не ответил: {type(exc).__name__}") from exc
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or payload.get("state") != 0 or not result.get("url"):
            message = (payload or {}).get("message") if isinstance(payload, dict) else None
            raise ProviderError(f"сервис отказал: {str(message or payload)[:120]}")
        return {"uuid": str(result.get("uuid") or ""), "url": str(result["url"])}

    def parse_webhook(self, raw_body, headers=None):
        """Подпись лежит в самом теле (поле sign): считаем её по телу без этого поля и сравниваем без утечки по времени. Нет ключа или подпись не совпала — None."""
        if not self._key():
            return None
        try:
            data = json.loads(raw_body)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        received = data.pop("sign", None)
        if not isinstance(received, str) or not received:
            return None
        expected = self.sign_text(self.dump(data))
        if not hmac.compare_digest(expected, received.lower()):
            return None
        return data

    def outcome(self, payload):
        status = str((payload or {}).get("status") or (payload or {}).get("payment_status") or "")
        if status in self.PAID:
            return "paid", status
        if status in self.FAILED:
            return "failed", status
        return "pending", status                  # confirm_check, process, check, wrong_amount, locked… — ждём или смотрит человек

    def paid_amount_matches(self, payload, order):
        try:
            return Decimal(str(payload.get("amount"))) == Decimal(int(order["amount_cents"])) / Decimal(100)
        except (InvalidOperation, TypeError, ValueError):
            return False


PROVIDERS = {"heleket": Heleket()}


def provider_for(method, price_key=None):
    """Настроенный сервис для способа оплаты ('crypto' | 'card'), который умеет принять именно эту покупку (price_key), или None."""
    for provider in PROVIDERS.values():
        if provider.method == method and provider.configured() and provider.available_for(price_key):
            return provider
    return None


def available_methods(price_key=None):
    """Способы оплаты, которые можно показать человеку за эту покупку: [{"method", "label", "provider"}]. Звёзды сюда не входят — они есть всегда."""
    result = []
    for method in ("card", "crypto"):
        provider = provider_for(method, price_key)
        if provider:
            result.append({"method": method, "label": provider.label, "provider": provider.name})
    return result


from pay_tribute import Tribute  # noqa: E402  (регистрируем после определения общих классов: Tribute берёт ProviderError отсюда)

PROVIDERS["tribute"] = Tribute()
