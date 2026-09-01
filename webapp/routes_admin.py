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
import os
import sqlite3
import tempfile
from datetime import datetime

from aiohttp import web

from config import ADMIN_IDS, BOT_TOKEN
from webapp.telegram_auth import validate_init_data
from db import (
    get_user, get_users_count,
    ban_user, unban_user,
    give_premium_admin, give_xp_admin,
    get_pending_users, set_access_status,
    get_users_by_tags, get_all_users,
    get_users_by_segment, SEGMENT_LABELS,
    get_user_support_card,
    get_churn_risk_report,
    get_all_flags, set_feature_flag, delete_feature_flag,
    get_recent_client_errors,
)
from db.core import DB_PATH
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


@routes.get("/api/admin/stats")
async def admin_stats_route(request):
    await _authenticate_admin(request)
    return web.json_response({
        "total_users": get_users_count(),
        "report_html": build_stats_report(),
    })


@routes.get("/api/admin/export-db")
async def admin_export_db_route(request):
    """Roadmap #42 — полный дамп БД одной кнопкой из админ-панели.
    Снимок берём тем же безопасным способом, что и в backups/backup.py
    (sqlite3 Backup API), а не сырым чтением файла — на WAL-режиме
    (см. db/core.py) сырая копия рискует оказаться неполной/нецелостной,
    если снимать её ровно в момент записи."""
    await _authenticate_admin(request)

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src_conn = sqlite3.connect(DB_PATH)
        dst_conn = sqlite3.connect(tmp_path)
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()

        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    filename = f"adam_db_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
    return web.Response(
        body=data,
        content_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    """Roadmap #44 — консолидированная карточка пользователя для
    поддержки: привычки, последние логи, подписка, дни без захода —
    вместо того чтобы вручную смотреть в 4 разные таблицы."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    card = get_user_support_card(telegram_id)
    if card is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(card)


@routes.get("/api/admin/flags")
async def admin_flags_list_route(request):
    """Roadmap #41 — feature flags."""
    await _authenticate_admin(request)
    return web.json_response({"flags": get_all_flags()})


@routes.post("/api/admin/flags")
async def admin_flags_set_route(request):
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    key = (body.get("key") or "").strip()
    if not set_feature_flag(key, bool(body.get("enabled")), body.get("rollout_pct", 100), body.get("description")):
        return web.json_response({"error": "invalid_flag"}, status=400)
    return web.json_response({"ok": True})


@routes.delete("/api/admin/flags/{key}")
async def admin_flags_delete_route(request):
    await _authenticate_admin(request)
    delete_feature_flag(request.match_info["key"])
    return web.json_response({"ok": True})


@routes.get("/api/admin/churn-risk")
async def admin_churn_risk_route(request):
    """Roadmap #45 — сводка риска оттока: сколько пользователей в каждом
    тире + список самых 'горящих'."""
    await _authenticate_admin(request)
    return web.json_response(get_churn_risk_report())


@routes.get("/api/admin/client-errors")
async def admin_client_errors_route(request):
    """Улучшение #70 — лента последних JS-ошибок с реальных устройств
    пользователей, вместо ручных отчётов "странички лагают" со скриншотами."""
    await _authenticate_admin(request)
    return web.json_response({"errors": get_recent_client_errors(limit=100)})


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


@routes.get("/api/admin/broadcast/segments")
async def admin_broadcast_segments_route(request):
    """Roadmap #43 — список сегментов для селектора в UI."""
    await _authenticate_admin(request)
    return web.json_response({"segments": [{"key": k, "label": v} for k, v in SEGMENT_LABELS.items()]})


@routes.post("/api/admin/broadcast")
async def admin_broadcast_route(request):
    """Рассылка всем / по тегу / по сегменту (roadmap #43 — например,
    "только неактивным 7+ дней") — то же самое, что делает бот в
    handlers/admin.py::send_broadcast, только вызывается из Mini App.
    segment имеет приоритет над tag, если оба почему-то присланы."""
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
    segment = (body.get("segment") or "").strip() or None
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    if len(text) > 4000:
        return web.json_response({"error": "text_too_long"}, status=400)

    if segment:
        telegram_ids = get_users_by_segment(segment)
        if telegram_ids is None:
            return web.json_response({"error": "unknown_segment"}, status=400)
    elif tag:
        telegram_ids = get_users_by_tags([tag])
    else:
        telegram_ids = [u["telegram_id"] for u in get_all_users()]

    success = 0
    failed = 0
    for telegram_id in telegram_ids:
        try:
            await bot.send_message(chat_id=telegram_id, text=text, parse_mode="HTML")
            success += 1
        except Exception:
            failed += 1

    return web.json_response({"ok": True, "success": success, "failed": failed, "total": len(telegram_ids)})
