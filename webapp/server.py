import json
import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
from aiohttp import web

from config import BOT_TOKEN, ADMIN_IDS
from webapp.telegram_auth import validate_init_data

from db import (
    get_user, add_user, is_banned, get_access_status,
    get_habits, get_habit, add_habit, edit_habit, delete_habit,
    complete_habit, get_progress, get_settings,
    update_reminder_time, update_ai_style, get_ai_style,
    # Этап 2 данные
    get_shop_items, buy_shop_item, get_user_items,
    get_rating,
    get_calendar,
    get_achievements
)

logger = logging.getLogger("webapp")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# ====================== АВТОРИЗАЦИЯ (как в Этапе 1) ======================
def _extract_init_data(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("tma "):
        return header[4:]
    return request.headers.get("X-Telegram-Init-Data", "")

async def _authenticate(request):
    init_data = _extract_init_data(request)
    tg_user = validate_init_data(init_data, BOT_TOKEN)

    if tg_user is None:
        raise web.HTTPUnauthorized(reason="invalid_init_data")

    telegram_id = tg_user["id"]
    is_admin = telegram_id in ADMIN_IDS

    if get_user(telegram_id) is None:
        add_user(telegram_id=telegram_id, username=tg_user.get("username"), first_name=tg_user.get("first_name", ""))

    if is_banned(telegram_id):
        raise web.HTTPForbidden(text='{"error": "banned"}')

    if not is_admin:
        status = get_access_status(telegram_id) or "approved"
        if status != "approved":
            raise web.HTTPForbidden(
                text=json.dumps({
                    "error": f"access_{status}",
                    "message": "Сначала пройдите анкету в самом боте"
                })
            )
    return telegram_id, is_admin



# ====================== API (всё + Этап 2) ======================
@web.middleware
async def error_middleware(request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception(f"Необработанная ошибка в {request.path}")
        return web.json_response({"error": "internal_error"}, status=500)

routes = web.RouteTableDef()



@routes.get("/api/bootstrap")
async def bootstrap(request):
    telegram_id, is_admin = await _authenticate(request)
    user = get_user(telegram_id)
    habits = get_habits(telegram_id)
    progress = get_progress(telegram_id)
    settings_row = get_settings(telegram_id)

    shop_items = get_shop_items()
    owned_item_ids = set(get_user_items(telegram_id))
    leaderboard = get_rating()
    calendar_events = get_calendar(telegram_id)
    achievements = get_achievements(telegram_id)

    return web.json_response({
        "user": {
            "telegram_id": telegram_id,
            "first_name": user["first_name"] if user else "",
            "xp": user["xp"] if user else 0,
            "level": user["level"] if user else 1,
            "streak": user["streak"] if user else 0,
            "premium": bool(user["premium"]) if user else False,
            "is_admin": is_admin,
        },
        "habits": [{"id": h["id"], "title": h["title"], "completed": bool(h["completed"])} for h in habits],
        "progress": progress,
        "settings": {
            "reminders": bool(settings_row["reminders"]) if settings_row else True,
            "reminder_hour": settings_row["reminder_hour"] if settings_row else 9,
            "reminder_minute": settings_row["reminder_minute"] if settings_row else 0,
            "ai_style": get_ai_style(telegram_id),
        },
        "shop_items": [
            {
                "id": it["id"], "name": it["name"], "description": it["description"],
                "price": it["price"], "owned": it["id"] in owned_item_ids,
            } for it in shop_items
        ],
        "leaderboard": [
            {
                "telegram_id": row["telegram_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "xp": row["xp"], "level": row["level"], "streak": row["streak"],
            } for row in leaderboard
        ],
        "calendar_events": [
            {"day": row["day"], "completed": row["completed"]} for row in calendar_events
        ],
        "achievements": [
            {
                "id": a["id"], "title": a["title"], "description": a["description"],
                "created_at": a["created_at"],
            } for a in achievements
        ],
    })

# ПРИВЫЧКИ (как в Этапе 1)
@routes.post("/api/habits")
async def create_habit(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    title = body.get("title", "").strip()
    if len(title) < 2:
        return web.json_response({"error": "title_too_short"}, status=400)
    add_habit(telegram_id, title)
    return web.json_response({"ok": True})

def _owned_habit_or_404(habit_id, telegram_id):
    habit = get_habit(habit_id)
    if not habit or habit["user_id"] != telegram_id:
        raise web.HTTPNotFound(text='{"error": "not_found"}')
    return habit

@routes.put("/api/habits/{habit_id}")
async def rename_habit(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    body = await request.json()
    new_title = body.get("title", "").strip()
    if len(new_title) < 2:
        return web.json_response({"error": "title_too_short"}, status=400)
    edit_habit(habit_id, new_title)
    return web.json_response({"ok": True})

@routes.post("/api/habits/{habit_id}/complete")
async def complete_habit_route(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    success = complete_habit(habit_id)
    if not success:
        return web.json_response({"error": "already_completed"}, status=409)
    return web.json_response({"ok": True, "progress": get_progress(telegram_id)})

@routes.delete("/api/habits/{habit_id}")
async def delete_habit_route(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    delete_habit(habit_id)
    return web.json_response({"ok": True})

# НАСТРОЙКИ (как в Этапе 1)
@routes.post("/api/settings/reminder-time")
async def set_reminder_time(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    hour = int(body.get("hour"))
    minute = int(body.get("minute"))
    update_reminder_time(telegram_id, hour, minute)
    return web.json_response({"ok": True})

@routes.post("/api/settings/ai-style")
async def set_ai_style(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    style = body.get("style")
    if style not in ("soft", "neutral", "strict"):
        return web.json_response({"error": "invalid_style"}, status=400)
    update_ai_style(telegram_id, style)
    return web.json_response({"ok": True})

# ====================== Этап 2 API ======================
@routes.get("/api/shop")
async def get_shop(request):
    telegram_id, _ = await _authenticate(request)
    owned_item_ids = set(get_user_items(telegram_id))
    items = [
        {
            "id": it["id"], "name": it["name"], "description": it["description"],
            "price": it["price"], "owned": it["id"] in owned_item_ids,
        } for it in get_shop_items()
    ]
    return web.json_response({"items": items})

@routes.post("/api/buy/{item_id}")
async def buy_route(request):
    telegram_id, _ = await _authenticate(request)
    item_id = int(request.match_info["item_id"])
    success = buy_shop_item(telegram_id, item_id)
    if not success:
        return web.json_response({"error": "not_enough_xp_or_not_found"}, status=400)
    user = get_user(telegram_id)
    return web.json_response({"ok": True, "xp": user["xp"] if user else 0})

@routes.get("/")
async def index(request):
    return web.FileResponse(BASE_DIR / "static" / "index.html")

# ====================== AI МиниПриложение ======================
@routes.get("/ai")
async def ai_miniapp(request):
    """Serve the AI mini app interface"""
    return web.FileResponse(BASE_DIR / "static" / "ai_miniapp.html")

def create_app():
    app = web.Application(middlewares=[error_middleware])
    app.add_routes(routes)

    # Добавляем маршруты для AI мини-приложения
    from webapp.routes_ai_miniapp import routes as ai_routes
    app.add_routes(ai_routes)

    app.router.add_static('/static/', path=BASE_DIR / 'static')
    return app

async def run_webapp(port):
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"🌐 MiniApp сервер запущен на порту {port}")
    return runner

@routes.get("/health")
async def health(request):
    return web.json_response({"status": "ok"})

logger.info("MiniApp server started")

