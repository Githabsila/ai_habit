"""
API и страница раздела «Аналитика» (webapp/static/analytics.html) — отдельно от админ-панели, чтобы её не перегружать.

Доступ — только админам: каждый маршрут проверяет подпись Telegram и ADMIN_IDS на сервере (как в webapp/routes_admin.py).
Тяжёлые расчёты идут в потоке, чтобы не блокировать приложение, и на 20 секунд кэшируются: страница дёргает несколько разрезов сразу.
Сами расчёты — db/stats.py (цифры), db/analyst.py (ИИ-аналитик), webapp/feature_map.py (карта функций).
"""
import asyncio
import json
import logging
import time

from aiohttp import web

from db import analyst, stats, tracking, traffic
from db import ai_usage as ai_usage_db
from db.admin_support import find_users_for_admin
from webapp.routes_admin import _authenticate_admin

logger = logging.getLogger("webapp.routes_analytics")
routes = web.RouteTableDef()

CACHE_TTL = 20
_cache = {}


def invalidate_cache():
    _cache.clear()


async def _compute(name, args, fn):
    """Расчёт в потоке с коротким кэшем: key = (разрез, параметры)."""
    key = (name, args)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    value = await asyncio.get_running_loop().run_in_executor(None, fn)
    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = (now, value)
    return value


def _int(request, name, default):
    try:
        return int(request.query.get(name, default))
    except (TypeError, ValueError):
        return default


def _source(request):
    value = (request.query.get("source") or "").strip()[:40]
    return value or None


async def _body(request):
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text=json.dumps({"error": "bad_json"}), content_type="application/json")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "bad_json"}), content_type="application/json")
    return data


@routes.get("/analytics")
async def analytics_page(request):
    """Страница отдаётся всем, как и /admin: контент она показывает только после проверки админа через API."""
    from webapp.webapp_server import BASE_DIR
    response = web.FileResponse(BASE_DIR / "static" / "analytics.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _simple(name, fn, **params):
    """Фабрика однотипных GET-маршрутов: параметры из строки запроса, расчёт в потоке."""
    async def handler(request):
        await _authenticate_admin(request)
        values = {key: _int(request, key, default) for key, default in params.items()}
        if name in ("overview", "retention", "funnel"):
            values["source"] = _source(request)
        args = tuple(sorted(values.items()))
        return web.json_response(await _compute(name, args, lambda: fn(**values)))
    return handler


routes.get("/api/analytics/overview")(_simple("overview", stats.overview, days=30))
routes.get("/api/analytics/retention")(_simple("retention", stats.retention, days=60))
routes.get("/api/analytics/funnel")(_simple("funnel", tracking.funnel, days=14))
routes.get("/api/analytics/features")(_simple("features", stats.features, days=14))
routes.get("/api/analytics/screens")(_simple("screens", stats.screens, days=14))
routes.get("/api/analytics/versions")(_simple("versions", stats.versions, limit=10))
routes.get("/api/analytics/health")(_simple("health", stats.health, days=7))
routes.get("/api/analytics/rhythm")(_simple("rhythm", stats.rhythm, days=30))
routes.get("/api/analytics/engagement")(_simple("engagement", stats.engagement, days=14))
routes.get("/api/analytics/ai")(_simple("ai", ai_usage_db.overview, days=14))
routes.get("/api/analytics/quests")(_simple("quests", stats.quests, days=14))


@routes.get("/api/analytics/buttons")
async def buttons_route(request):
    await _authenticate_admin(request)
    days, limit = _int(request, "days", 14), _int(request, "limit", 80)
    screen = (request.query.get("screen") or "").strip()[:40] or None
    return web.json_response(await _compute("buttons", (days, limit, screen), lambda: stats.buttons(days, screen, limit)))


@routes.get("/api/analytics/quality")
async def quality_route(request):
    await _authenticate_admin(request)
    return web.json_response(await _compute("quality", (), stats.quality))


@routes.get("/api/analytics/signals")
async def signals_route(request):
    await _authenticate_admin(request)
    return web.json_response(await _compute("signals", (), stats.signals))


@routes.get("/api/analytics/sources")
async def sources_route(request):
    """Каналы для фильтра: созданные ссылки + «по приглашению друга» + «без канала»."""
    await _authenticate_admin(request)
    items = [{"code": s["code"], "title": s["title"]} for s in traffic.list_sources(include_archived=True)]
    items += [{"code": "ref", "title": traffic.LABELS.get("ref", "По приглашению друга")}, {"code": "none", "title": traffic.LABELS.get("none", "Без канала")}]
    return web.json_response({"sources": items})


# ---------------------------------------------------------------------------------------------------------------- исключённые аккаунты

@routes.post("/api/analytics/exclude")
async def exclude_route(request):
    """Пометить человека «не учитывать в статистике» (тестовый аккаунт). Найти можно по @нику или id."""
    await _authenticate_admin(request)
    body = await _body(request)
    query = str(body.get("query") or "").strip()[:60]
    excluded = bool(body.get("excluded", True))
    if not query:
        return web.json_response({"error": "empty_query"}, status=400)
    found = find_users_for_admin(query, limit=3)
    if not found:
        return web.json_response({"error": "not_found"}, status=404)
    exact = str(found[0].get("username") or "").lower() == query.lstrip("@").lower()
    if len(found) > 1 and not query.lstrip("@").isdigit() and not exact:
        return web.json_response({"error": "ambiguous", "candidates": [{"telegram_id": u["telegram_id"], "username": u.get("username"), "first_name": u.get("first_name")} for u in found]}, status=409)
    person = found[0]
    stats.set_excluded(person["telegram_id"], excluded)
    invalidate_cache()
    return web.json_response({"ok": True, "telegram_id": person["telegram_id"], "username": person.get("username"), "excluded": excluded})


# ---------------------------------------------------------------------------------------------------------------- ИИ-аналитик

@routes.get("/api/analytics/analyst")
async def analyst_state_route(request):
    await _authenticate_admin(request)
    return web.json_response({"latest": analyst.latest(), "history": [{"id": h["id"], "created_at": h["created_at"], "days": h["days"], "trigger": h["trigger"],
                                                                      "headline": (h["report"] or {}).get("headline")} for h in analyst.history(8)],
                              "notes": analyst.list_notes("open")})


@routes.post("/api/analytics/analyst/run")
async def analyst_run_route(request):
    await _authenticate_admin(request)
    body = await _body(request)
    days = max(3, min(int(body.get("days") or 14), 60))
    result = await analyst.run(days, trigger="manual")
    status = 200 if result.get("ok") else (429 if result.get("error") == "too_soon" else 502)
    return web.json_response(result, status=status)


@routes.post("/api/analytics/analyst/notes")
async def analyst_note_add_route(request):
    await _authenticate_admin(request)
    body = await _body(request)
    note_id = analyst.add_note(body.get("text"), "owner")
    if note_id is None:
        return web.json_response({"error": "empty_text"}, status=400)
    return web.json_response({"ok": True, "id": note_id, "notes": analyst.list_notes("open")})


@routes.post("/api/analytics/analyst/notes/{note_id}")
async def analyst_note_status_route(request):
    await _authenticate_admin(request)
    body = await _body(request)
    try:
        note_id = int(request.match_info["note_id"])
    except ValueError:
        return web.json_response({"error": "bad_id"}, status=400)
    if not analyst.set_note_status(note_id, str(body.get("status") or "")):
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True, "notes": analyst.list_notes("open")})
