"""
API для админ-панели внутри Mini App (webapp/static/admin_panel.html).

Дублирует по возможностям то, что уже есть в handlers/admin.py (бот),
но как отдельный HTTP API для отдельной страницы Mini App — потому что
у бота это просто текстовые кнопки без какой-либо визуальной темы, а
здесь та же "премиальная стеклянная" тема, что и во всём остальном
приложении.

КАЖДЫЙ роут независимо проверяет is_admin на сервере (см.
_authenticate_admin) — скрытая на фронте кнопка это только UX, не
защита.
"""
import json
import logging

from aiohttp import web

from config import ADMIN_IDS, BOT_TOKEN
from webapp.telegram_auth import validate_init_data
from db import (
    get_user, get_users_count,
    ban_user, unban_user,
    give_premium_admin, give_xp_admin,
    get_pending_users, set_access_status,
    get_users_by_tags, get_all_users,
)
from admin_digest_scheduler import build_stats_report

logger = logging.getLogger("webapp.routes_admin")

routes = web.RouteTableDef()


def _extract_init_data(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("tma "):
        return header[4:]
    return request.headers.get("X-Telegram-Init-Data", "")


async def _authenticate_admin(request):
    """Как обычная Mini App авторизация, но дополнительно требует
    is_admin — иначе 403. Никогда не доверяем тому, что фронт скрыл
    кнопку админки для не-админов."""
    init_data = _extract_init_data(request)
    tg_user = validate_init_data(init_data, BOT_TOKEN)
    if tg_user is None:
        raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized"}), content_type="application/json")
    telegram_id = tg_user["id"]
    if telegram_id not in ADMIN_IDS:
        raise web.HTTPForbidden(text=json.dumps({"error": "not_admin"}), content_type="application/json")
    return telegram_id


def _user_card(row):
    if row is None:
        return None
    keys = row.keys()
    return {
        "telegram_id": row["telegram_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "premium": bool(row["premium"]),
        "banned": bool(row["banned"]),
        "xp": row["xp"],
        "level": row["level"],
        "streak": row["streak"],
        "access_status": row["access_status"] if "access_status" in keys else None,
        "created_at": str(row["created_at"]) if "created_at" in keys else None,
    }


@routes.get("/api/admin/stats")
async def admin_stats_route(request):
    await _authenticate_admin(request)
    return web.json_response({
        "total_users": get_users_count(),
        "report_html": build_stats_report(),
    })


@routes.get("/api/admin/pending")
async def admin_pending_route(request):
    await _authenticate_admin(request)
    users = get_pending_users(limit=30)
    return web.json_response({
        "users": [
            {"telegram_id": u["telegram_id"], "username": u["username"], "first_name": u["first_name"]}
            for u in users
        ]
    })


@routes.post("/api/admin/approve/{telegram_id}")
async def admin_approve_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    set_access_status(telegram_id, "approved")
    return web.json_response({"ok": True})


@routes.get("/api/admin/user/{telegram_id}")
async def admin_user_card_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    user = _user_card(get_user(telegram_id))
    if user is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(user)


@routes.post("/api/admin/user/{telegram_id}/ban")
async def admin_ban_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    ban_user(telegram_id)
    return web.json_response({"ok": True, "banned": True})


@routes.post("/api/admin/user/{telegram_id}/unban")
async def admin_unban_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    unban_user(telegram_id)
    return web.json_response({"ok": True, "banned": False})


@routes.post("/api/admin/user/{telegram_id}/premium")
async def admin_premium_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    give_premium_admin(telegram_id)
    return web.json_response({"ok": True})


@routes.post("/api/admin/user/{telegram_id}/xp")
async def admin_xp_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_amount"}, status=400)
    if amount == 0 or abs(amount) > 100000:
        return web.json_response({"error": "invalid_amount"}, status=400)
    give_xp_admin(telegram_id, amount)
    return web.json_response({"ok": True})


@routes.post("/api/admin/broadcast")
async def admin_broadcast_route(request):
    """Рассылка всем или по тегу — то же самое, что делает бот в
    handlers/admin.py::send_broadcast, только вызывается из Mini App."""
    await _authenticate_admin(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    text = (body.get("text") or "").strip()
    tag = (body.get("tag") or "").strip() or None
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    if len(text) > 4000:
        return web.json_response({"error": "text_too_long"}, status=400)

    telegram_ids = get_users_by_tags([tag]) if tag else [u["telegram_id"] for u in get_all_users()]

    success = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
            success += 1
        except Exception:
            failed += 1

    return web.json_response({"ok": True, "success": success, "failed": failed, "total": len(telegram_ids)})
