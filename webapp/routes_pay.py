"""
Маршруты оплаты картой и криптой (payments_ext.py, pay_providers.py, db/pay_orders.py). Звёзды идут прежними маршрутами (/api/subscription/invoice, /api/shop/stars/{id}).

  GET  /api/pay/methods?kind=subscription|item&item_id=  — какие способы показать и по какой цене
  POST /api/pay/order {method, kind, item_id}            — создаёт заказ, возвращает pay_url (куда открыть оплату)
  GET  /api/pay/order/{order_id}                         — состояние заказа (приложение ждёт подтверждения после возврата с оплаты)
  POST /api/pay/webhook/{provider}                       — подтверждение от платёжного сервиса; без входа в приложение, но с проверкой подписи
  GET  /api/admin/pay/orders, POST /api/admin/pay/orders/{order_id}/fulfil — админка: список заказов и повтор начисления, если деньги пришли, а покупка не выдалась
"""
import json
import logging

from aiohttp import web

import pay_providers
import payments_ext
from config import ADMIN_IDS
from db import pay_orders
from db.tracking import track_server

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

ERROR_STATUS = {"method_unavailable": 503, "provider_error": 502, "already_paid": 400, "already_owned": 400, "daily_limit_reached": 400, "item_not_found": 404, "invalid_request": 400}


def _error(code):
    return web.json_response({"error": code}, status=ERROR_STATUS.get(code, 400))


async def _auth(request):
    from webapp.webapp_server import _authenticate
    telegram_id, _ = await _authenticate(request, allow_locked=True)         # закрытому аккаунту платить можно и нужно
    return telegram_id


@routes.get("/api/pay/catalog")
async def pay_catalog_route(request):
    """Каталог для окна «Покупки»: подписка, товары с ценами, способы оплаты, поддержка, акции."""
    telegram_id = await _auth(request)
    return web.json_response(payments_ext.catalog(telegram_id))


@routes.get("/api/pay/methods")
async def pay_methods_route(request):
    telegram_id = await _auth(request)
    query = request.rel_url.query
    try:
        methods = payments_ext.methods_for(telegram_id, query.get("kind", ""), query.get("item_id"))
    except payments_ext.PayError as exc:
        return _error(exc.code)
    return web.json_response({"methods": methods})


@routes.post("/api/pay/order")
async def pay_order_route(request):
    telegram_id = await _auth(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    method, kind, item_id = str(body.get("method") or ""), str(body.get("kind") or ""), body.get("item_id")
    try:
        created = await payments_ext.start_order(telegram_id, method, kind, item_id)
    except payments_ext.PayError as exc:
        return _error(exc.code)
    return web.json_response({"ok": True, **created})


@routes.get("/api/pay/order/{order_id}")
async def pay_order_status_route(request):
    telegram_id = await _auth(request)
    order = pay_orders.get_order(request.match_info["order_id"])
    if order is None or order["user_id"] != telegram_id:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"status": order["status"], "fulfilled": bool(order["fulfilled_at"]), "kind": order["kind"]})


@routes.post("/api/pay/webhook/{provider}")
async def pay_webhook_route(request):
    """Подтверждение от платёжного сервиса. Подпись проверяем по телу; чужое или поддельное не принимаем. Ошибку начисления возвращаем как 500 — сервис повторит позже."""
    provider = pay_providers.PROVIDERS.get(request.match_info["provider"])
    if provider is None:
        return web.json_response({"error": "unknown_provider"}, status=404)
    raw = await request.read()
    payload = provider.parse_webhook(raw, request.headers)
    if payload is None:
        logger.warning("Вебхук оплаты %s отклонён: подпись не сошлась", provider.name)
        return web.json_response({"error": "bad_signature"}, status=400)
    handler = getattr(provider, "handle", None)
    if handler is not None:                                                   # Tribute: сопоставление с заказом и выдача внутри сервиса (нет наших номеров заказов)
        try:
            return web.json_response(await handler(payload, request.app.get("bot")))
        except Exception:
            logger.exception("Не удалось обработать вебхук %s", provider.name)
            return web.json_response({"error": "handler_failed"}, status=500)     # сервис повторит позже
    order = pay_orders.get_order(payload.get("order_id") or "")
    if order is None or order["provider"] != provider.name:
        return web.json_response({"ok": True, "ignored": True})
    outcome, raw_status = provider.outcome(payload)
    pay_orders.set_raw_status(order["order_id"], raw_status)
    bot = request.app.get("bot")
    if outcome == "paid":
        if not provider.paid_amount_matches(payload, order):
            await _alert_admins(bot, f"⚠️ Оплата {order['order_id']} подтверждена на другую сумму ({payload.get('amount')}), покупка не выдана. Проверь в админке.")
            pay_orders.mark_failed(order["order_id"], "сумма в подтверждении не совпала", status="failed")
            return web.json_response({"ok": True, "ignored": True})
        paid = pay_orders.mark_paid(order["order_id"], raw_status)
        await payments_ext.fulfil(bot, paid)
    elif outcome == "failed":
        pay_orders.mark_failed(order["order_id"], raw_status)
    return web.json_response({"ok": True})


async def _alert_admins(bot, text):
    if bot is None:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.info("Не удалось сообщить админу %s о проблеме с оплатой", admin_id)


# ---------------------------------------------------------------------------------------------------------------- админка

@routes.get("/api/admin/pay/tribute")
async def admin_tribute_route(request):
    """Что нужно завести в Tribute и что уже привязано: по строке на каждую нашу цену; плюс список товаров из Tribute (если задан ключ API), чтобы выбрать номер."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    provider = pay_providers.PROVIDERS["tribute"]
    mapped = {row["key"]: row for row in pay_orders.tribute_products()}
    needed = [{"key": key, "label": label, "price": pay_orders.format_usd(cents), "product_id": mapped[key]["product_id"] if key in mapped else None,
               "link": mapped[key]["link"] if key in mapped else None} for key, label, cents in pay_orders.all_price_keys()]
    remote = []
    if provider.configured():
        remote = [{"id": p.get("id"), "name": p.get("name"), "price": f"{int(p.get('amount') or 0) / 100:.2f} {p.get('currency')}", "status": p.get("status")}
                  for p in await provider.list_products()]
    return web.json_response({"configured": provider.configured(), "needed": needed, "tribute_products": remote,
                              "webhook_url": f"{(request.url.scheme + '://' + request.host)}/api/pay/webhook/tribute"})


@routes.post("/api/admin/pay/tribute/products")
async def admin_tribute_register_route(request):
    """Привязать товар Tribute к нашей цене. body: {"key": "sub_first", "product_id": 123} (product_id пустой — убрать привязку). Цена, валюта и статус проверяются."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    key, product_id = str(body.get("key") or ""), body.get("product_id")
    if pay_orders.key_to_purchase(key) is None:
        return web.json_response({"error": "unknown_key"}, status=400)
    if product_id in (None, "", 0):
        pay_orders.remove_tribute_product(key)
        return web.json_response({"ok": True, "removed": True})
    if not str(product_id).isdigit():
        return web.json_response({"error": "invalid_product"}, status=400)
    provider = pay_providers.PROVIDERS["tribute"]
    if not provider.configured():
        return web.json_response({"error": "not_configured"}, status=409)
    result = await provider.register_product(key, int(product_id))
    return web.json_response(result, status=200 if result.get("ok") else 400)


@routes.get("/api/admin/pay/promos")
async def admin_promos_route(request):
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    return web.json_response({"promos": [dict(r) for r in pay_orders.list_promos(only_current=False)]})


@routes.post("/api/admin/pay/promos")
async def admin_promo_add_route(request):
    """Новая акция в окне «Покупки»: {"title", "text", "until"?: "ГГГГ-ММ-ДД ЧЧ:ММ:СС"}. Показывается, пока не вышел срок или её не уберут."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    promo_id = pay_orders.add_promo(body.get("title"), body.get("text"), body.get("until"))
    if promo_id is None:
        return web.json_response({"error": "invalid_promo"}, status=400)
    return web.json_response({"ok": True, "id": promo_id})


@routes.post("/api/admin/pay/promos/{promo_id}/remove")
async def admin_promo_remove_route(request):
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    pay_orders.remove_promo(int(request.match_info["promo_id"]))
    return web.json_response({"ok": True})


@routes.get("/api/admin/pay/orders")
async def admin_pay_orders_route(request):
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    from db.core import connect
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM pay_orders ORDER BY id DESC LIMIT 100").fetchall()
    finally:
        conn.close()
    orders = [{"order_id": r["order_id"], "user_id": r["user_id"], "provider": r["provider"], "method": r["method"], "kind": r["kind"], "item_id": r["item_id"],
               "price": pay_orders.format_usd(r["amount_cents"]), "status": r["status"], "raw_status": r["raw_status"], "fulfilled": bool(r["fulfilled_at"]),
               "error": r["error"], "created_at": r["created_at"], "paid_at": r["paid_at"]} for r in rows]
    return web.json_response({"orders": orders, "methods": pay_providers.available_methods()})


@routes.post("/api/admin/pay/orders/{order_id}/fulfil")
async def admin_pay_fulfil_route(request):
    """Деньги пришли, а покупка не выдалась (сбой начисления): повторить начисление. Работает только для оплаченного и ещё не начисленного заказа."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    order = pay_orders.get_order(request.match_info["order_id"])
    if order is None:
        return web.json_response({"error": "not_found"}, status=404)
    if order["status"] != "paid":
        return web.json_response({"error": "not_paid"}, status=409)
    try:
        done = await payments_ext.fulfil(request.app.get("bot"), order)
    except Exception as exc:
        return web.json_response({"error": "fulfil_failed", "detail": str(exc)[:200]}, status=500)
    return web.json_response({"ok": True, "fulfilled_now": done})
