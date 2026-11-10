"""
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

Подписка (решение 05.11, вариант А): первый месяц — разовая покупка по вводной цене (sub_first), дальше — оффер с периодичностью MONTHLY
(sub_renewal): Lava сама списывает раз в месяц и присылает subscription.recurring.payment.success с parentContractId — продлеваем на
календарный месяц (как все карты, db/subscription.py::add_calendar_months). Отмена — subscription.cancelled или DELETE /api/v1/subscriptions.
Возврат и чарджбек — сообщение админам, доступ автоматически не снимаем (как у Tribute: решает владелец).

Ключи в код не пишутся: LAVA_API_KEY и LAVA_WEBHOOK_SECRET — в Railway → Variables.
"""
import hmac
import json
import logging
import re

import aiohttp

import config
from db import pay_orders

logger = logging.getLogger(__name__)

API_BASE = "https://gate.lava.top"
PROVIDER_NAMES = ("lava", "lava_rub", "lava_sbp")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")


def valid_email(text):
    text = str(text or "").strip()
    return text if len(text) <= 254 and EMAIL_RE.match(text) else None


class Lava:
    """Один способ оплаты Lava. Вебхук принимает экземпляр с name == "lava" (адрес /api/pay/webhook/lava) — для заказов всех трёх способов."""

    def __init__(self, name, method, label, currency, payment_provider, payment_method):
        self.name, self.method, self.label = name, method, label
        self.currency, self.payment_provider, self.payment_method = currency, payment_provider, payment_method
        self.needs_email = True

    @staticmethod
    def _key():
        return str(getattr(config, "LAVA_API_KEY", "") or "").strip()

    @staticmethod
    def _secret():
        return str(getattr(config, "LAVA_WEBHOOK_SECRET", "") or "").strip()

    def configured(self):
        return bool(self._key() and self._secret())

    def available_for(self, price_key):
        """Способ показываем, если хотя бы одна цена привязана (общий список) или привязан оффер этой цены и у него есть цена в нашей валюте."""
        if price_key is None:
            return any(pay_orders.lava_price(row["key"], self.currency) for row in pay_orders.lava_offers())
        return bool(pay_orders.lava_price(price_key, self.currency))

    def order_amount(self, price_key):
        """Сколько ждём в вебхуке (центы/копейки в валюте способа)."""
        return pay_orders.lava_price(price_key, self.currency)

    # ---------------------------------------------------------------------------------------------------------------- счёт

    async def _request(self, method, path, body=None, params=None):
        headers = {"X-Api-Key": self._key(), "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.request(method, f"{API_BASE}{path}", json=body, params=params, headers=headers) as response:
                    text = await response.text()
                    try:
                        data = json.loads(text) if text else None
                    except ValueError:
                        data = None
                    return response.status, data
        except Exception as exc:
            return 0, {"error": type(exc).__name__}

    async def create_invoice(self, order, amount_cents):
        from pay_providers import ProviderError
        offer = pay_orders.lava_offer(order["price_key"])
        email = pay_orders.get_pay_email(order["user_id"])
        if offer is None or not email:
            raise ProviderError("нет оффера Lava или почты покупателя")
        pay_orders.set_order_email(order["order_id"], email)
        base = str(getattr(config, "WEBAPP_URL", "") or "").rstrip("/")
        body = {
            "email": email, "offerId": offer["offer_id"], "currency": self.currency, "paymentProvider": self.payment_provider,
            "buyerLanguage": "RU", "clientUtm": {"utm_source": "adam", "utm_content": order["order_id"]},
        }
        if self.payment_method:
            body["paymentMethod"] = self.payment_method
        if offer["periodicity"]:
            body["periodicity"] = offer["periodicity"]
        if base:
            body.update({"successful_return_url": f"{base}/?m=pay_ok", "failure_return_url": base, "cancel_return_url": base})
        status, data = await self._request("POST", "/api/v3/invoice", body)
        if status not in (200, 201) or not isinstance(data, dict) or not data.get("paymentUrl") or not data.get("id"):
            message = (data or {}).get("error") if isinstance(data, dict) else None
            raise ProviderError(f"Lava отказала ({status}): {str(message or data)[:120]}")
        return {"uuid": str(data["id"]), "url": str(data["paymentUrl"])}

    def contract_email(self, contract_id, user_id=None):
        """(почта, точно_та_самая): почта из заказа, которым оформлена подписка; для старых заказов без неё — последняя введённая."""
        order = pay_orders.find_by_provider_uuid_any(PROVIDER_NAMES, contract_id)
        email = dict(order).get("email") if order is not None else None
        if email:
            return email, True
        if user_id is None and order is not None:
            user_id = order["user_id"]
        return (pay_orders.get_pay_email(user_id) if user_id is not None else None), False

    async def cancel_subscription(self, contract_id, email, missing_ok=True):
        """Отменить автосписание (DELETE /api/v1/subscriptions). True — отменено (или уже отменено, если почта точно та, с которой
        оформляли: missing_ok). С «запасной» почтой 404 успехом не считаем — подписка могла остаться (прогон 09.11)."""
        status, _data = await self._request("DELETE", "/api/v1/subscriptions", params={"contractId": contract_id, "email": email})
        return status in (200, 204) or (missing_ok and status == 404)

    async def list_products(self):
        status, data = await self._request("GET", "/api/v2/products", params={"showAllSubscriptionPeriods": "true"})
        if status != 200:
            return []
        items = data.get("items") if isinstance(data, dict) else data
        return items if isinstance(items, list) else []

    # ---------------------------------------------------------------------------------------------------------------- вебхук

    def parse_webhook(self, raw_body, headers=None):
        """Lava подтверждает вебхук нашим же секретом в заголовке X-Api-Key (настройка «API-ключ» вебхука в кабинете). Нет секрета или не совпал — None."""
        secret = self._secret()
        if not secret:
            return None
        received = str((headers or {}).get("X-Api-Key") or (headers or {}).get("x-api-key") or "")
        if not received or not hmac.compare_digest(received, secret):
            return None
        try:
            data = json.loads(raw_body)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def outcome(self, payload):                                           # общий интерфейс сервисов; Lava обрабатывается в handle()
        return "pending", str((payload or {}).get("status") or "")

    def paid_amount_matches(self, payload, order):
        return _amount_matches(payload, order)

    async def handle(self, payload, bot):
        event = str(payload.get("eventType") or payload.get("event_type") or "")
        if event == "payment.success":
            return await self._on_paid(payload, bot)
        if event == "subscription.recurring.payment.success":
            return await self._on_renewed(payload, bot)
        if event in ("payment.failed", "subscription.recurring.payment.failed"):
            return self._on_failed(payload)
        if event == "subscription.cancelled":
            return self._on_cancelled(payload)
        if event in ("refund.success", "chargeback.initiated"):
            data = payload.get("data") or {}
            await _alert(bot, f"⚠️ Lava: {'возврат' if event == 'refund.success' else 'чарджбек'} {data.get('amount')} {data.get('currency')} — "
                              f"«{(data.get('product') or {}).get('product_name')}», почта {data.get('customer_email')}. Доступ автоматически не снят — реши в админке.")
            return {"status": "ok"}
        return {"status": "ok", "ignored": True}

    def _order_for(self, payload):
        contract = str(payload.get("contractId") or "")
        order = pay_orders.find_by_provider_uuid_any(PROVIDER_NAMES, contract) if contract else None
        if order is None:                                                 # запасной путь: наш номер заказа в UTM
            utm = (payload.get("clientUtm") or {}).get("utm_content")
            candidate = pay_orders.get_order(utm) if utm else None
            if candidate is not None and candidate["provider"] in PROVIDER_NAMES:
                order = candidate
        return order

    async def _on_paid(self, payload, bot):
        import payments_ext
        order = self._order_for(payload)
        contract = str(payload.get("contractId") or "")
        if order is None:
            await _alert(bot, f"⚠️ Lava: оплата {contract} без нашего заказа (почта {(payload.get('buyer') or {}).get('email')}, "
                              f"{payload.get('amount')} {payload.get('currency')}). Выдай вручную после проверки.")
            return {"status": "ok", "ignored": True}
        if not _amount_matches(payload, order):
            await _alert(bot, f"⚠️ Lava: заказ {order['order_id']} оплачен на другую сумму ({payload.get('amount')} {payload.get('currency')}). Покупка не выдана.")
            pay_orders.mark_failed(order["order_id"], "сумма в подтверждении не совпала", status="failed")
            return {"status": "ok", "ignored": True}
        if not pay_orders.claim_event("lava", f"p{contract}"):
            return {"status": "ok", "duplicate": True}
        try:
            paid = pay_orders.mark_paid(order["order_id"], str(payload.get("status") or "completed"))
            await payments_ext.fulfil(bot, paid)
            if order["kind"] == "subscription" and str(payload.get("status") or "") == "subscription-active":
                await self._cancel_previous_autorenew(bot, order["user_id"], contract)
                _remember_autorenew(order["user_id"], contract)
                await _say(bot, order["user_id"], "🔁 Автопродление картой включено: каждый месяц Lava спишет полную цену, и подписка продлится сама. "
                                                  "Отключить можно в приложении (раздел «Подписка»).")
        except Exception:
            pay_orders.release_event("lava", f"p{contract}")
            raise
        return {"status": "ok"}

    async def _cancel_previous_autorenew(self, bot, user_id, new_contract):
        """Две оплаченные подписки = два автосписания каждый месяц, а в приложении отменить можно только последнюю (прогон 09.11).
        Оставляем новую, старую отменяем у Lava; не вышло — пишем админам."""
        from db.users import get_user
        user = get_user(user_id)
        previous = str(user["subscription_charge_id"] or "") if user is not None else ""
        if not previous.startswith("lava:") or not int(user["subscription_auto_renew"] or 0) or previous[5:] == new_contract:
            return
        old_contract = previous[5:]
        email, exact = self.contract_email(old_contract, user_id)
        if not email or not await self.cancel_subscription(old_contract, email, missing_ok=exact):
            await _alert(bot, f"⚠️ Lava: у {user_id} две подписки с автосписанием ({old_contract} и {new_contract}). "
                              "Старую отменить не удалось — отмени её в кабинете Lava.")

    async def _on_renewed(self, payload, bot):
        """Очередное автосписание: заказ на продление создаём уже оплаченным (родитель — первая оплата подписки) и начисляем календарный месяц."""
        import payments_ext
        contract, parent_id = str(payload.get("contractId") or ""), str(payload.get("parentContractId") or "")
        parent = pay_orders.find_by_provider_uuid_any(PROVIDER_NAMES, parent_id) if parent_id else None
        if parent is None:
            await _alert(bot, f"⚠️ Lava: продление подписки {contract} (родитель {parent_id}) — не нашёл, чьё оно. Продли вручную.")
            return {"status": "ok", "ignored": True}
        if not pay_orders.claim_event("lava", f"p{contract}"):
            return {"status": "ok", "duplicate": True}
        try:
            renewal = pay_orders.find_by_provider_uuid_any(PROVIDER_NAMES, contract)
            if renewal is None:
                cents = int(round(float(payload.get("amount") or 0) * 100))
                renewal = pay_orders.create_order(parent["user_id"], parent["provider"], parent["method"], "subscription", None, cents,
                                                  str(payload.get("currency") or parent["currency"]), parent["price_key"])
                pay_orders.set_provider_uuid(renewal["order_id"], contract)
            paid = pay_orders.mark_paid(renewal["order_id"], "subscription-active")
            await payments_ext.fulfil(bot, paid)
        except Exception:
            pay_orders.release_event("lava", f"p{contract}")
            raise
        return {"status": "ok"}

    def _on_failed(self, payload):
        order = self._order_for(payload)
        if order is not None and order["status"] == "created":
            pay_orders.mark_failed(order["order_id"], str(payload.get("errorMessage") or payload.get("status") or "failed"))
        return {"status": "ok"}                                          # неудачное автосписание: напоминание subscription_renewal скажет «не получилось»

    def _on_cancelled(self, payload):
        contract = str(payload.get("contractId") or "")
        order = pay_orders.find_by_provider_uuid_any(PROVIDER_NAMES, contract)
        if order is not None:
            from db.subscription import set_auto_renew
            from db.users import get_user
            user = get_user(order["user_id"])
            if user is not None and str(user["subscription_charge_id"] or "") == f"lava:{contract}":
                set_auto_renew(order["user_id"], False)
        return {"status": "ok"}

    # ---------------------------------------------------------------------------------------------------------------- привязка офферов

    async def auto_link(self):
        """«Привязать Lava автоматически»: для каждой цены ищем ровно один оффер с той же ценой в USD и подходящим названием продукта (шаблоны —
        pay_tribute.AUTO_LINK_NAMES); для продления (sub_renewal) — оффер с ежемесячной периодичностью. Ноль или два кандидата — не трогаем."""
        from pay_tribute import AUTO_LINK_NAMES
        offers = []
        for product in await self.list_products():
            for offer in (product.get("offers") or []):
                prices = {}
                periodicity = None
                for price in (offer.get("prices") or []):
                    currency = str(price.get("currency") or "").upper()
                    if price.get("amount") is not None and currency in ("USD", "RUB", "EUR"):
                        prices[currency] = int(round(float(price["amount"]) * 100))
                    periodicity = periodicity or price.get("periodicity")
                if str(offer.get("recurrent") or "").lower() == "monthly":
                    periodicity = periodicity or "MONTHLY"
                offers.append({"product_id": product.get("id"), "offer_id": offer.get("id"), "title": f"{product.get('title') or ''} {offer.get('name') or ''}",
                               "prices": prices, "periodicity": periodicity})
        mapped = {row["key"] for row in pay_orders.lava_offers()}
        taken = {row["offer_id"] for row in pay_orders.lava_offers()}
        results = []
        for key, label, cents in pay_orders.all_price_keys():
            if key in mapped:
                results.append({"key": key, "label": label, "status": "already"})
                continue
            monthly = key == "sub_renewal"
            pattern = AUTO_LINK_NAMES.get(key)
            found = [o for o in offers if o["offer_id"] not in taken and o["prices"].get("USD") == cents
                     and ((o["periodicity"] or "ONE_TIME") == "MONTHLY") == monthly
                     and pattern and re.search(pattern, o["title"], re.IGNORECASE)]
            if len(found) != 1:
                results.append({"key": key, "label": label, "status": "not_found" if not found else "ambiguous", "candidates": [o["offer_id"] for o in found]})
                continue
            o = found[0]
            pay_orders.set_lava_offer(key, o["product_id"], o["offer_id"], o["title"].strip(), o["prices"], "MONTHLY" if monthly else None)
            taken.add(o["offer_id"])
            results.append({"key": key, "label": label, "status": "linked", "offer_id": o["offer_id"], "name": o["title"].strip(),
                            "rub": o["prices"].get("RUB")})
        return results


def _amount_matches(payload, order):
    """Сумма и валюта вебхука — как у оффера в валюте заказа (заказ хранит ожидаемую сумму в центах/копейках своей валюты)."""
    try:
        paid = int(round(float(payload.get("amount")) * 100))
    except (TypeError, ValueError):
        return False
    return str(payload.get("currency") or "").upper() == str(order["currency"]).upper() and paid == int(order["amount_cents"])


def _remember_autorenew(user_id, contract_id):
    from db.core import connect
    conn = connect()
    try:
        conn.execute("UPDATE users SET subscription_auto_renew=1, subscription_charge_id=? WHERE telegram_id=?", (f"lava:{contract_id}", user_id))
        conn.commit()
    finally:
        conn.close()


async def _say(bot, user_id, text):
    if bot is None:
        return
    try:
        await bot.send_message(user_id, text, parse_mode=None)
    except Exception:
        logger.info("Не удалось написать %s про автопродление", user_id)


async def _alert(bot, text):
    if bot is None:
        return
    for admin_id in getattr(config, "ADMIN_IDS", []):
        try:
            await bot.send_message(admin_id, text, parse_mode=None)
        except Exception:
            logger.info("Не удалось сообщить админу %s о Lava", admin_id)


def providers():
    return {
        "lava": Lava("lava", "card", "Картой Visa / Mastercard", "USD", "UNLIMINT", "CARD"),
        "lava_rub": Lava("lava_rub", "rub", "Картой «Мир»", "RUB", "SMART_GLOCAL", "CARD"),
        "lava_sbp": Lava("lava_sbp", "sbp", "СБП", "RUB", "PAY2ME", "SBP"),
    }
