"""
Общая аутентификация Mini App API — вынесена сюда, чтобы webapp_server.py
и routes_ai_miniapp.py проверяли РОВНО одно и то же (бан / статус анкеты /
гейт подписки), а не расходились в проверках.

История бага: у routes_ai_miniapp.py (все /api/ai/*) была СВОЯ, более
слабая проверка — только подпись initData, без is_banned/access_status/
bot_access_allowed. Это значило, что забаненный пользователь или тот, у
кого истёк пробный период (после включения SUBSCRIPTION_GATE_ENABLED),
мог продолжать бесплатно тратить платную AI-квоту через чат ADAM, при
том что остальной Mini App для него уже был закрыт.
"""
import json

from aiohttp import web

from config import BOT_TOKEN, ADMIN_IDS
from webapp.telegram_auth import validate_init_data
from db import (
    get_user, add_user, is_banned, get_access_status, set_access_status,
    bot_access_allowed,
)


def _error(exc_cls, error, message=None, extra=None):
    body = {"error": error}
    if message:
        body["message"] = message
    if extra:
        body.update(extra)
    return exc_cls(text=json.dumps(body), content_type="application/json")


async def authenticate(init_data):
    """Проверяет подпись Telegram initData И статус доступа (бан / анкета /
    подписка) — то же самое, что webapp_server._authenticate. Создаёт
    пользователя при первом обращении. Возвращает (telegram_id, is_admin)
    либо бросает aiohttp.web.HTTPException с уже готовым JSON-телом."""
    tg_user = validate_init_data(init_data, BOT_TOKEN)
    if tg_user is None:
        raise _error(
            web.HTTPUnauthorized, "unauthorized",
            "Не получилось подтвердить, что это ты. Закрой и открой Mini App заново.",
        )

    telegram_id = tg_user["id"]
    is_admin = telegram_id in ADMIN_IDS

    if get_user(telegram_id) is None:
        add_user(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name", ""),
        )

    if is_banned(telegram_id):
        raise _error(web.HTTPForbidden, "banned")

    if not is_admin:
        status = get_access_status(telegram_id) or "approved"
        if status == "new":
            set_access_status(telegram_id, "approved")
            status = "approved"
        if status != "approved":
            raise _error(
                web.HTTPForbidden, f"access_{status}",
                "Доступ к приложению пока ожидает подтверждения",
            )
        if not bot_access_allowed(telegram_id):
            raise _error(web.HTTPForbidden, "trial_expired", "Пробный период закончился")

    return telegram_id, is_admin
