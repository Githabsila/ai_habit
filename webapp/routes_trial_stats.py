"""
Раздел «Триал» в аналитике админки (db/trial_stats.py, ТЗ 28.10.2026 §26–§27): путь людей, дни 2/3/4, покупки, возвраты, сообщения «отправлено / показано / нажато»,
источники с переходом к людям. Только для админов; переписки не читаются.
"""
from aiohttp import web

from db import trial_stats

routes = web.RouteTableDef()


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


@routes.get("/api/admin/trial/stats")
async def admin_trial_stats_route(request):
    """Query: days (1–180, по умолчанию 30), source (код источника), scope (real | test | all, по умолчанию real — тестовые и подарочные дни отдельно)."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    query = request.rel_url.query
    scope = query.get("scope", "real")
    return web.json_response(trial_stats.overview(days=_int(query.get("days"), 30, 1, 180), source=(query.get("source") or "").strip() or None,
                                                  scope=scope if scope in trial_stats.SCOPES else "real"))


@routes.get("/api/admin/trial/source/{code}")
async def admin_trial_source_route(request):
    """Люди одного источника: ник/id, полезное действие, оплата и отметка «подозрительная регистрация» для проверки (без ярлыка «бот»)."""
    from webapp.routes_admin import _authenticate_admin
    await _authenticate_admin(request)
    days = _int(request.rel_url.query.get("days"), 30, 1, 180)
    return web.json_response({"people": trial_stats.source_people(request.match_info["code"], days)})
