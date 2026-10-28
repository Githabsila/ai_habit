"""
Маршруты денежной партнёрской программы (db/partners.py, ТЗ 28.10.2026 §20–§24).

Участник (Mini App): свои цифры и история, подписанные ссылки; новичок — карточка пригласившего.
Админ: список «кому, сколько и за что», добавление в пилот, индивидуальные условия, настройки, выплаты и исправления спорных начислений — всё с журналом.
"""
import json
import logging

from aiohttp import web

from db import partners
from db.tracking import track_server

logger = logging.getLogger(__name__)
routes = web.RouteTableDef()

ERROR_STATUS = {
    "not_partner": 404, "user_not_found": 404, "not_found": 404, "already_partner": 409, "below_minimum": 409, "already_paid": 409,
}


async def _json(request):
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _answer(result):
    if isinstance(result, dict) and "error" in result:
        return web.json_response(result, status=ERROR_STATUS.get(result["error"], 400))
    return web.json_response(result)


def _opt_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1                                          # заведомо неверное: модуль вернёт invalid_*


# ---------------------------------------------------------------------------------------------------------------- участник

@routes.get("/api/partner/stats")
async def partner_stats_route(request):
    """Что видит сам участник: приглашения, покупки, суммы, история без имён приглашённых и его ссылки. Не участнику — 404."""
    from webapp.webapp_server import _authenticate
    telegram_id, _ = await _authenticate(request, allow_locked=True)
    stats = partners.participant_stats(telegram_id)
    if stats is None:
        return web.json_response({"error": "not_partner"}, status=404)
    track_server(telegram_id, "partner_stats_open")
    return web.json_response(stats)


@routes.post("/api/partner/link")
async def partner_link_route(request):
    """Новая подписанная ссылка участника: body {"label": "Канал про привычки"} — чтобы сравнивать площадки."""
    from webapp.webapp_server import _authenticate
    telegram_id, _ = await _authenticate(request)
    result = partners.create_link(telegram_id, (await _json(request)).get("label"))
    if "error" not in result:
        track_server(telegram_id, "partner_link_create", result["label"][:40])
        result["links"] = partners.list_links(telegram_id)
    return _answer(result)


@routes.get("/api/partner/inviter")
async def partner_inviter_route(request):
    """Карточка пригласившего для новичка (имя и @ник): подписка и дружба — только по его решению, ничего не делается за него. Нет привязки — {"inviter": null}."""
    from webapp.webapp_server import _authenticate
    telegram_id, _ = await _authenticate(request, allow_locked=True)
    return web.json_response({"inviter": partners.inviter_card(telegram_id)})


@routes.post("/api/partner/inviter/friend")
async def partner_inviter_friend_route(request):
    """«Добавить в друзья» на карточке пригласившего — только по нажатию самого новичка и только пригласившего именно по этой привязке."""
    from webapp.webapp_server import _authenticate
    telegram_id, _ = await _authenticate(request)
    card = partners.inviter_card(telegram_id)
    if card is None:
        return web.json_response({"error": "no_inviter"}, status=404)
    from db.friends import add_friendship
    add_friendship(telegram_id, card["user_id"])
    track_server(telegram_id, "inviter_card_action", "friend")
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------------------------------------------- админка

@routes.get("/api/admin/partners")
async def admin_partners_route(request):
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    data = partners.admin_overview()
    data["audit"] = partners.audit_log(limit=20)
    return web.json_response(data)


@routes.get("/api/admin/partners/{user_id}")
async def admin_partner_detail_route(request):
    """Карточка участника: условия, суммы, что именно к выплате («за что»), ссылки и журнал."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    user_id = int(request.match_info["user_id"])
    stats = partners.participant_stats(user_id)
    if stats is None:
        return web.json_response({"error": "not_partner"}, status=404)
    stats["payable_items"] = [{"id": r["id"], "charge_id": r["charge_id"], "amount": r["amount"], "base": r["base_amount"], "share_percent": r["share_percent"], "purchased_at": r["purchased_at"]}
                              for r in partners.payable_accruals(user_id)]
    stats["audit"] = partners.audit_log(user_id, limit=30)
    return web.json_response(stats)


@routes.post("/api/admin/partners")
async def admin_partner_add_route(request):
    """Включить человека в пилот. body: {"user_id", "share_percent"?, "window_days"?, "valid_until"?, "note"?}."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    body = await _json(request)
    user_id = _opt_int(body.get("user_id"))
    if not user_id or user_id < 0:
        return web.json_response({"error": "invalid_user"}, status=400)
    result = partners.add_partner(user_id, admin_id, share_percent=_opt_int(body.get("share_percent")), window_days=_opt_int(body.get("window_days")),
                                  valid_until=body.get("valid_until") or None, note=body.get("note") or "")
    return _answer(result)


@routes.post("/api/admin/partners/{user_id}/conditions")
async def admin_partner_conditions_route(request):
    """Индивидуальные условия: {"share_percent"?, "window_days"?, "valid_until"?, "status"?: "active" | "paused"}. Меняют только будущие начисления."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    body = await _json(request)
    return _answer(partners.set_conditions(int(request.match_info["user_id"]), admin_id, share_percent=_opt_int(body.get("share_percent")), window_days=_opt_int(body.get("window_days")),
                                           valid_until=body.get("valid_until") or None, status=body.get("status") or None))


@routes.post("/api/admin/partners/{user_id}/payout")
async def admin_partner_payout_route(request):
    """Отметить ручную выплату всего, что сейчас «к выплате». body: {"note"?, "confirmation"?} — подтверждение (номер перевода) пишется в журнал."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    body = await _json(request)
    result = partners.create_payout(int(request.match_info["user_id"]), admin_id, note=body.get("note") or "", confirmation=body.get("confirmation") or "")
    if "error" not in result:
        track_server(int(request.match_info["user_id"]), "partner_payout", meta={"amount": result["amount"]})
    return _answer(result)


@routes.post("/api/admin/partners/accrual/{accrual_id}")
async def admin_partner_accrual_route(request):
    """Исправление спорного начисления: {"action": "hold" | "release" | "reverse", "reason": "..."} — причина обязательна."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    body = await _json(request)
    return _answer(partners.correct_accrual(int(request.match_info["accrual_id"]), admin_id, str(body.get("action") or ""), body.get("reason") or ""))


@routes.post("/api/admin/partners/settings")
async def admin_partner_settings_route(request):
    """Параметры программы (правило привязки, доля, срок, ожидание выплаты, минимум, округление). body: {"key", "value"}."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    body = await _json(request)
    result = partners.set_setting(str(body.get("key") or ""), body.get("value", ""), admin_id)
    if "error" not in result:
        result["settings"] = partners.all_settings()
    return _answer(result)
