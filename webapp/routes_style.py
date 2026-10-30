"""
Переключатель оформления «Royal Gold» для админки (db/style_mode.py): GET /api/admin/style — текущий режим, POST /api/admin/style {"mode": "classic" | "preview" | "premium"}.
POST /api/admin/style/preview присылает админу в бот примеры: приветствие, пробный период и оплату с баннерами и длинное сообщение с золотой шапкой.
Только для админов; переключение действует сразу, а «classic» возвращает прежний вид целиком.
"""
from aiohttp import web

import bot_style
from db import style_mode
from db.tracking import track_server

routes = web.RouteTableDef()

LABELS = {"classic": "Классика (как было)", "preview": "Предпросмотр (только админы)", "bot": "Бот у всех, приложение админам", "premium": "Премиум (бот и приложение у всех)"}

# Примеры для проверки глазами: тексты в духе настоящих сообщений, но с пометкой, что это образец.
SAMPLES = (
    ("welcome", "👋 Образец приветствия. Я — ADAM.\n\nВместе мы будем:\n\n- 🎯 Развивать привычки\n- 💬 Говорить о твоих планах и целях\n- 📈 Следить за прогрессом\n\nВсё это — в приложении. Открой его кнопкой ниже 👇"),
    ("trial", "⏳ Образец напоминания про пробный период. Пока открыты все мои функции: разбор дня, подсказки по привычкам и рейтинг. Загляни сегодня — я подскажу, с чего начать."),
    ("paid", "✅ Образец подтверждения оплаты. Pro открыт на 30 дней. Спасибо, что остаёшься со мной: все функции теперь твои."),
)
SAMPLE_LONG = ("Образец обычного сообщения. Так выглядят мои длинные сообщения с золотой шапкой: я напомню про привычки, подведу итог дня и подскажу, что сделать завтра.\n\n"
               "- Привычки на сегодня — две из трёх\n- Серия держится уже пять дней\n- До нового ранга осталось совсем немного")


@routes.get("/api/admin/style")
async def admin_style_route(request):
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    return web.json_response({"mode": style_mode.get_mode(), "modes": [{"mode": m, "label": LABELS[m]} for m in style_mode.MODES]})


@routes.post("/api/admin/style")
async def admin_style_set_route(request):
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    mode = str(body.get("mode") or "")
    if not style_mode.set_mode(mode, admin_id):
        return web.json_response({"error": "invalid_mode"}, status=400)
    track_server(admin_id, "style_mode_set", mode)
    return web.json_response({"ok": True, "mode": mode})


@routes.post("/api/admin/style/preview")
async def admin_style_preview_route(request):
    """Образцы оформления в чат админа. Работает, пока оформление включено именно для админа (предпросмотр или премиум)."""
    from webapp.routes_admin import _authenticate_admin
    admin_id = await _authenticate_admin(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)
    if not bot_style.active(admin_id):
        return web.json_response({"error": "style_off"}, status=409)
    sent = 0
    for kind, text in SAMPLES:
        if await bot_style.send_banner(bot, admin_id, kind, text):
            sent += 1
    await bot.send_message(admin_id, SAMPLE_LONG)                  # шапку и ромбики добавит мидлварь bot_style.royal_dress
    track_server(admin_id, "style_preview_sent", meta={"banners": sent})
    return web.json_response({"ok": True, "banners": sent})
