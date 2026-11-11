"""
Оплата криптовалютой через Crypto Bot (@CryptoBot, Crypto Pay API — https://help.crypt.bot/crypto-pay-api), 11.11: запасной способ рядом со звёздами и Tribute.

Как идёт оплата: на каждый наш заказ создаём счёт в долларах (currency_type=fiat, fiat=USD) — человек платит любой поддерживаемой
криптовалютой (USDT, TON, BTC…) прямо в Telegram, ссылка вида t.me/CryptoBot?start=… открывается без браузера и без входа.
В счёте лежит наш номер заказа (payload). Crypto Bot присылает вебхук invoice_paid, подписанный HMAC-SHA256 тела с ключом SHA256(токен)
в заголовке crypto-pay-api-signature. Дальше — общий путь webapp/routes_pay.py: сверка суммы, отметка оплаты, выдача (payments_ext.fulfil).

Токен (CRYPTOBOT_API_TOKEN) задаёт владелец в Railway; в код и в чат он не попадает.
"""
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation

import aiohttp

import config

logger = logging.getLogger(__name__)

# Счёт живёт час — как у Heleket: не оплатил — создаст новый из приложения.
INVOICE_TTL_SECONDS = 3600


class CryptoBot:
    name = "cryptobot"
    method = "crypto"
    label = "Криптой (Crypto Bot)"

    @staticmethod
    def _token():
        return str(getattr(config, "CRYPTOBOT_API_TOKEN", "") or "").strip()

    @staticmethod
    def _base():
        return "https://testnet-pay.crypt.bot/api" if getattr(config, "CRYPTOBOT_TESTNET", False) else "https://pay.crypt.bot/api"

    def configured(self):
        return bool(self._token())

    def available_for(self, price_key):
        return True                                       # счёт на любую сумму создаётся по запросу

    async def _call(self, method, body):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(f"{self._base()}/{method}", json=body, headers={"Crypto-Pay-API-Token": self._token()}) as response:
                    return await response.json(content_type=None)
        except Exception as exc:
            from pay_providers import ProviderError
            raise ProviderError(f"Crypto Bot не ответил: {type(exc).__name__}") from exc

    async def create_invoice(self, order, amount_cents):
        from pay_providers import ProviderError
        body = {
            "currency_type": "fiat", "fiat": "USD", "amount": f"{int(amount_cents) // 100}.{int(amount_cents) % 100:02d}",
            "description": "ADAM — подписка" if order["kind"] == "subscription" else "ADAM — покупка в магазине",
            "payload": order["order_id"], "expires_in": INVOICE_TTL_SECONDS, "allow_comments": False, "allow_anonymous": True,
        }
        bot_url = str(getattr(config, "WEBAPP_URL", "") or "").rstrip("/")
        if bot_url:
            body.update({"paid_btn_name": "callback", "paid_btn_url": f"{bot_url}/?m=pay_ok"})   # кнопка «Вернуться» после оплаты
        data = await self._call("createInvoice", body)
        result = data.get("result") if isinstance(data, dict) and data.get("ok") else None
        url = (result or {}).get("bot_invoice_url") or (result or {}).get("mini_app_invoice_url")
        if not isinstance(result, dict) or not url:
            error = (data or {}).get("error") if isinstance(data, dict) else None
            raise ProviderError(f"Crypto Bot отказал: {str(error or data)[:120]}")
        return {"uuid": str(result.get("invoice_id") or ""), "url": str(url)}

    def parse_webhook(self, raw_body, headers=None):
        """Подпись — HMAC-SHA256(тело, ключ = SHA256(токен)) в заголовке crypto-pay-api-signature. Не сошлась или нет токена — None.
        Возвращаем счёт в общем виде: order_id (наш номер из payload), status, amount, fiat, update_type."""
        token = self._token()
        if not token:
            return None
        received = str((headers or {}).get("crypto-pay-api-signature") or (headers or {}).get("Crypto-Pay-Api-Signature") or "")
        raw = raw_body if isinstance(raw_body, (bytes, bytearray)) else str(raw_body or "").encode("utf-8")
        expected = hmac.new(hashlib.sha256(token.encode("utf-8")).digest(), raw, hashlib.sha256).hexdigest()
        if not received or not hmac.compare_digest(expected, received.lower()):
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        invoice = data.get("payload") if isinstance(data, dict) else None
        if not isinstance(invoice, dict):
            return None
        return {
            "update_type": str(data.get("update_type") or ""), "order_id": str(invoice.get("payload") or ""),
            "status": str(invoice.get("status") or ""), "amount": invoice.get("amount"), "fiat": invoice.get("fiat"),
            "currency_type": invoice.get("currency_type"), "invoice_id": invoice.get("invoice_id"),
            "paid_asset": invoice.get("paid_asset"), "paid_amount": invoice.get("paid_amount"),
        }

    def outcome(self, payload):
        status = str((payload or {}).get("status") or "")
        if (payload or {}).get("update_type") == "invoice_paid" and status == "paid":
            return "paid", status
        if status == "expired":
            return "failed", status
        return "pending", status

    def paid_amount_matches(self, payload, order):
        if str(payload.get("currency_type") or "fiat") != "fiat" or str(payload.get("fiat") or "").upper() != str(order["currency"] or "USD").upper():
            return False
        try:
            return Decimal(str(payload.get("amount"))) == Decimal(int(order["amount_cents"])) / Decimal(100)
        except (InvalidOperation, TypeError, ValueError):
            return False
