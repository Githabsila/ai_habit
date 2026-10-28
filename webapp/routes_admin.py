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
    reset_onboarding,
    get_error_stats, get_recent_errors,
    get_pending_users, set_access_status,
    get_users_by_tags, get_all_users,
    get_users_by_segment, SEGMENT_LABELS,
    get_user_support_card,
    get_churn_risk_report,
    get_all_flags, set_feature_flag, delete_feature_flag,
    get_recent_client_errors,
    get_recent_perf_events, get_perf_summary,
    get_access, get_access_log, grant_access, gift_days, set_unlimited, start_trial_mode, access_overview,
)
from db.core import DB_PATH
from db.admin_gifts import grant_admin_badge
from db.admin_support import find_users_for_admin, list_active_users_for_admin
from db.pair_lab import build_scenario, lab_scenarios
from db.sim_lab import sim_scenes
from db import adam_checkin as adam_ck
from db.pair_quests import get_pair_rating
from db import ai_usage as ai_usage_db
from db import tracking as tracking_db
from db import traffic as traffic_db
from db import user_summary as summary_db
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


@routes.get("/api/admin/pair-rating")
async def admin_pair_rating_route(request):
    """Сырой рейтинг пар — пока только для админов (смотрим, как он выглядит, до показа пользователям)."""
    await _authenticate_admin(request)
    return web.json_response({"pairs": get_pair_rating()})


@routes.get("/api/admin/lab/scenarios")
async def admin_lab_scenarios_route(request):
    """«Лаборатория»: список демо-состояний новых экранов (db/pair_lab.py)."""
    await _authenticate_admin(request)
    return web.json_response({"scenarios": lab_scenarios()})


@routes.get("/api/admin/lab/pair/{key}")
async def admin_lab_pair_route(request):
    """Демо-состояние парного задания в формате обычного pair_quest — приложение открывает его с ?lab=<ключ>
    настоящей карточкой, ничего не сохраняя."""
    await _authenticate_admin(request)
    payload = build_scenario(request.match_info["key"])
    if payload is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"pair_quest": payload})


@routes.get("/api/admin/lab/sims")
async def admin_lab_sims_route(request):
    """«Окна и анимации»: список симуляций новых окон (db/sim_lab.py); приложение проигрывает их по ?sim=<ключ>."""
    await _authenticate_admin(request)
    return web.json_response({"scenes": sim_scenes()})


@routes.get("/api/admin/lab/adam-chat")
async def admin_lab_adam_chat_presets_route(request):
    """Типовые сообщения для проверки ответов Адама в чате."""
    await _authenticate_admin(request)
    from webapp.services.chat_brain import LAB_PRESETS
    return web.json_response({"presets": [{"label": label, "message": message} for label, message in LAB_PRESETS]})


@routes.post("/api/admin/lab/adam-chat")
async def admin_lab_adam_chat_route(request):
    """Ответ Адама на сообщение — тем же конвейером, что в чате (снимок ситуации, сценарий, настроение, модель), но ничего не пишется в
    переписку и не списывается из лимита человека. body: {"message": "...", "user_id": необязательно — чьи привычки и день взять (по умолчанию твои)}."""
    admin_id = await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    message = " ".join(str(body.get("message") or "").split())[:400]
    if not message:
        return web.json_response({"error": "empty_message"}, status=400)
    try:
        user_id = int(body.get("user_id") or admin_id)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_user"}, status=400)
    if get_user(user_id) is None:
        return web.json_response({"error": "user_not_found"}, status=404)

    from db import get_ai_style
    from multi_agent import solve_task_multiagent
    from webapp.services import chat_brain
    from webapp.services.ai_utils import build_user_context

    context, brain = chat_brain.enrich(user_id, message, build_user_context(user_id))
    try:
        with ai_usage_db.scope(admin_id, "chat"):
            result = await solve_task_multiagent(
                task=message, history="", user_context=context, style=get_ai_style(user_id), first_message=False, humor_note="",
                scenario_note=brain["scenario_note"], mood=brain["mood"],
            )
    except Exception as exc:
        logger.warning("Лаборатория Адама: ответ не получен: %s", exc)
        return web.json_response({"error": "model_failed", "message": str(exc)[:200]}, status=502)
    return web.json_response({
        "answer": result["answer"], "suggested_habit": result.get("suggested_habit"), "mood": brain["mood"], "scenarios": brain["keys"],
        "scenario_note": brain["scenario_note"], "situation": brain["situation"], "user_id": user_id,
    })


@routes.get("/api/admin/lab/adam")
async def admin_lab_adam_route(request):
    """«Адам пишет первым» в Лаборатории: раскатка (флаг), окна, сводка за неделю, демо-сценарии."""
    await _authenticate_admin(request)
    flags = {f["key"]: f for f in get_all_flags()}
    flag = flags.get(adam_ck.FLAG_KEY, {"enabled": False, "rollout_pct": 100})
    fu_flag = flags.get(adam_ck.FOLLOWUP_FLAG)
    followup_status = ("только админы (записи флага нет)" if fu_flag is None
                       else f"включено для {fu_flag['rollout_pct']}% людей и админов" if fu_flag["enabled"] else "выключено всем")
    windows = {slot: f"{lo // 60:02d}:{lo % 60:02d}–{hi // 60:02d}:{hi % 60:02d}" for slot, (lo, hi) in adam_ck.SLOT_WINDOWS.items()}
    return web.json_response({
        "flag": flag, "windows": windows, "stats": adam_ck.checkin_stats(7),
        "limits": {"per_day": 1, "per_week": adam_ck.MAX_PUSH_PER_WEEK, "chat_days": list(adam_ck.CHAT_DAYS_PER_WEEK)},
        # эксперимент «Адам пишет сам после действий»: записи флага нет — включено только админам
        "followups": {
            "flag": adam_ck.FOLLOWUP_FLAG, "delay_min": list(adam_ck.FOLLOWUP_DELAY), "chat_delay_min": list(adam_ck.FOLLOWUP_CHAT_DELAY),
            "per_day": adam_ck.FOLLOWUPS_PER_DAY, "gap_min": adam_ck.ADAM_GAP_MIN, "status": followup_status,
            "window": f"{adam_ck.FOLLOWUP_FROM_MIN // 60:02d}:{adam_ck.FOLLOWUP_FROM_MIN % 60:02d}–{adam_ck.FOLLOWUP_TO_MIN // 60:02d}:{adam_ck.FOLLOWUP_TO_MIN % 60:02d}",
        },
        "scenarios": [{"key": k, "title": title, "slot": slot} for k, (title, slot, _state) in adam_ck.lab_scenarios().items()],
    })


async def _adam_preview(admin_id, body):
    """Текст сообщения Адама для демо-состояния (или для настоящего состояния админа, key='mine'). Ничего не отправляет и не сохраняет."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from adam_proactive import compose_message
    from db.streak import get_timezone

    key = str(body.get("scenario") or "")
    now_local = datetime.now(ZoneInfo(get_timezone(admin_id)))
    day_iso = now_local.date().isoformat()
    decisions = None
    if key == "mine":
        state = adam_ck.build_checkin_state(admin_id)
        slot = "app"
        title = "Твой день прямо сейчас"
        chat_day = adam_ck.is_chat_day(admin_id, now_local.date())
        decisions = {
            s: dict(zip(("scenario", "reason"), adam_ck.pick_scenario(state, s, chat_day))) for s in ("day", "evening")
        }
        for s in ("day", "evening"):
            picked, why, _all = adam_ck.choose_scenario(state, s, chat_day, state.get("recent_scenarios") or ())
            decisions[s] = {"scenario": picked, "reason": why, "intent": adam_ck.INTENT_LABELS.get(picked)}
        decisions["chat_day"] = chat_day
    else:
        item = adam_ck.lab_scenarios().get(key)
        if item is None:
            return None
        title, slot, state = item
        chat_day = True
    state["user_id"] = admin_id
    scenario, reason = adam_ck.pick_scenario(state, slot, chat_day)
    # Все уместные поводы с весом — видно, ПОЧЕМУ выбран именно этот (и почему при тишине выбирать нечего).
    candidates = (adam_ck.list_candidates(state, slot, chat_day) if slot in ("day", "evening")
                  else adam_ck.followup_candidates(state) if slot == "followup" else [])
    baseline = {
        "usual_day": state.get("usual", {}).get("day"), "usual_evening": state.get("usual", {}).get("evening"),
        "first_habit_minute": state.get("first_habit_minute"), "task_hist": state.get("task_hist"),
        "seen_minutes_ago": state.get("seen_minutes_ago"), "recent_scenarios": state.get("recent_scenarios"), "rhythm": state.get("rhythm"),
    } if key == "mine" else None
    if scenario is None:
        return {"title": title, "scenario": None, "intent": None, "reason": reason, "decisions": decisions, "candidates": candidates,
                "baseline": baseline, "state": adam_ck.lab_state_summary(state), "text": "", "source": "none"}
    opener = adam_ck.opener_style(admin_id, day_iso, scenario)
    prompt = adam_ck.build_checkin_prompt(state, scenario, opener, adam_ck.recent_adam_messages(admin_id), state.get("recent_scenarios") or ())
    if body.get("source") == "fallback":
        text, source = adam_ck.fallback_text(state, scenario, admin_id, f"{day_iso}:{datetime.now().microsecond}"), "fallback"
    else:
        text, source = await compose_message(state, scenario, admin_id, day_iso)
    reference = adam_ck.REFERENCE_EXAMPLES.get(scenario, "").format(name=state.get("first_name") or "друг")
    return {
        "title": title, "scenario": scenario, "intent": adam_ck.INTENT_LABELS.get(scenario), "reason": reason, "decisions": decisions,
        "candidates": candidates, "baseline": baseline, "state": adam_ck.lab_state_summary(state),
        "text": text, "source": source, "prompt": prompt, "reference": reference,
    }


@routes.post("/api/admin/lab/adam/preview")
async def admin_lab_adam_preview_route(request):
    admin_id = await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    result = await _adam_preview(admin_id, body)
    if result is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(result)


@routes.post("/api/admin/lab/adam/send")
async def admin_lab_adam_send_route(request):
    """Отправить показанное сообщение В TELEGRAM САМОМУ АДМИНУ (получатель — только тот, кто нажал; в историю чата не пишется)."""
    admin_id = await _authenticate_admin(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    text = (body.get("text") or "").strip()
    if not text or len(text) > adam_ck.TEXT_MAX_CHARS * 2:
        return web.json_response({"error": "invalid_text"}, status=400)
    from adam_proactive import _keyboard
    await bot.send_message(admin_id, "🧪 Тест · " + text, reply_markup=_keyboard())
    return web.json_response({"ok": True})


@routes.get("/api/admin/users/search")
async def admin_users_search_route(request):
    """Поиск для админ-панели: ID, @ник (Telegram или в приложении) или часть имени."""
    await _authenticate_admin(request)
    return web.json_response({"users": find_users_for_admin(request.query.get("q", ""))})


@routes.get("/api/admin/users/active")
async def admin_users_active_route(request):
    """Активные за неделю (по серии) — список «кому выдать медаль»."""
    await _authenticate_admin(request)
    return web.json_response({"users": list_active_users_for_admin()})


@routes.get("/api/admin/user/{telegram_id}/reminders")
async def admin_user_reminders_route(request):
    """«Почему не приходят напоминания»: настройки человека, решение по каждой точке дня тем же кодом, что у рассылки, что реально ушло
    и причины пропусков (reminder_diagnosis.py)."""
    await _authenticate_admin(request)
    try:
        telegram_id = int(request.match_info["telegram_id"])
    except ValueError:
        return web.json_response({"error": "invalid_user"}, status=400)
    import reminder_diagnosis
    result = reminder_diagnosis.diagnose_user(telegram_id)
    if result is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(result)


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
    card["access"] = get_access(telegram_id)
    card["access_log"] = get_access_log(telegram_id, limit=8)
    card["trial"] = _trial_state(telegram_id)
    card["ai_usage"] = ai_usage_db.user_usage(telegram_id)
    return web.json_response(card)


# ---------------------------------------------------------------------------------------------------------------- путь человека

@routes.get("/api/admin/user/{telegram_id}/journey")
async def admin_user_journey_route(request):
    """Путь человека: когда что сделал (минуты от регистрации), онбординг и пропуски, время в приложении, после чего выходит,
    лента действий (с текстом сообщений Адаму) и последние сессии."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    journey = tracking_db.journey(telegram_id)
    if journey is None:
        return web.json_response({"error": "not_found"}, status=404)
    try:
        limit = min(int(request.query.get("limit", 60)), 300)
    except (TypeError, ValueError):
        limit = 60
    return web.json_response({
        "journey": journey, "timeline": tracking_db.timeline(telegram_id, limit=limit), "sessions": tracking_db.sessions(telegram_id, limit=8),
    })


@routes.get("/api/admin/analytics/funnel")
async def admin_funnel_route(request):
    """Воронка когорты за N дней (без админов): шаги, медианы минут, где застревают, пропуски, после чего выходят. ?days=14&source=<код|none>"""
    await _authenticate_admin(request)
    try:
        days = int(request.query.get("days", 14))
    except (TypeError, ValueError):
        days = 14
    return web.json_response(tracking_db.funnel(days=days, source=(request.query.get("source") or None)))


# ---------------------------------------------------------------------------------------------------------------- ИИ-сводки

SUMMARY_ERRORS = {"user_not_found": 404, "invalid_kind": 400, "too_soon": 429, "model_failed": 502}


@routes.get("/api/admin/user/{telegram_id}/summary")
async def admin_user_summary_route(request):
    """Последняя короткая и расширенная ИИ-сводки по человеку; stale — данные изменились после сводки."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    if get_user(telegram_id) is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({**summary_db.get_summaries(telegram_id), "history": summary_db.history(telegram_id)})


@routes.post("/api/admin/user/{telegram_id}/summary")
async def admin_user_summary_make_route(request):
    """Заново составить ИИ-сводку по свежим данным. body: {"kind": "short"|"extended"}."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    result = await summary_db.generate(telegram_id, (body.get("kind") or "short"))
    if "error" in result:
        return web.json_response(result, status=SUMMARY_ERRORS.get(result["error"], 400))
    return web.json_response(result)


@routes.get("/api/admin/analytics/insights")
async def admin_insights_get_route(request):
    await _authenticate_admin(request)
    return web.json_response({"insights": summary_db.latest(0, "insights")})


@routes.post("/api/admin/analytics/insights")
async def admin_insights_make_route(request):
    """Общий ИИ-разбор за период: что работает, что мешает, повторяющиеся ошибки, каналы, что сделать. body: {"days": 14}."""
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    try:
        days = max(1, min(int(body.get("days", 14)), 90))
    except (TypeError, ValueError):
        days = 14
    result = await summary_db.generate_insights(days)
    if "error" in result:
        return web.json_response(result, status=SUMMARY_ERRORS.get(result["error"], 502))
    return web.json_response(result)


# ---------------------------------------------------------------------------------------------------------------- каналы трафика

_ADMIN_BOT_USERNAME = {"value": None}


async def _bot_username(request):
    """Имя бота для ссылок: из webapp_server (оно уже кэшируется на процесс) либо одним запросом get_me."""
    if _ADMIN_BOT_USERNAME["value"]:
        return _ADMIN_BOT_USERNAME["value"]
    bot = request.app.get("bot")
    if bot is None:
        return None
    try:
        _ADMIN_BOT_USERNAME["value"] = (await bot.get_me()).username
    except Exception:
        return None
    return _ADMIN_BOT_USERNAME["value"]


@routes.get("/api/admin/traffic")
async def admin_traffic_route(request):
    """Каналы трафика со ссылками и качеством: пришло, дошли до привычки, D1/D7, «мёртвые», подозрения, друзья, оплаты. ?days=30"""
    await _authenticate_admin(request)
    try:
        days = int(request.query.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    username = await _bot_username(request)
    channels = traffic_db.channel_stats(days)
    for item in channels:
        item["link"] = traffic_db.link(item["code"], username) if item["code"] not in traffic_db.LABELS else None
    return web.json_response({"channels": channels, "bot_username": username, "kinds": traffic_db.KINDS, "days": days})


@routes.post("/api/admin/traffic")
async def admin_traffic_create_route(request):
    """Новая ссылка канала. body: {"title": "Канал про бег", "kind": "channel|blogger|ads|friend|other", "note": "...", "code": "необязательно"}"""
    await _authenticate_admin(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    result = traffic_db.create_source(body.get("title"), body.get("kind"), body.get("note"), (body.get("code") or None))
    if "error" in result:
        return web.json_response(result, status=409 if result["error"] == "code_taken" else 400)
    result["link"] = traffic_db.link(result["code"], await _bot_username(request))
    return web.json_response(result)


@routes.post("/api/admin/traffic/{code}/archive")
async def admin_traffic_archive_route(request):
    await _authenticate_admin(request)
    if not traffic_db.archive_source(request.match_info["code"]):
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------------------------------------------- расход ИИ

@routes.get("/api/admin/ai-usage")
async def admin_ai_usage_route(request):
    """Расход токенов за период: всего, по функциям, по дням, кто больше всех тратит. ?days=7"""
    await _authenticate_admin(request)
    try:
        days = int(request.query.get("days", 7))
    except (TypeError, ValueError):
        days = 7
    return web.json_response(ai_usage_db.overview(days=days))


@routes.get("/api/admin/user/{telegram_id}/ai-usage")
async def admin_user_ai_usage_route(request):
    await _authenticate_admin(request)
    return web.json_response(ai_usage_db.user_usage(int(request.match_info["telegram_id"])))


AI_LIMIT_ERRORS = {"user_not_found": 404, "invalid_hours": 400, "invalid_limit": 400}


@routes.post("/api/admin/user/{telegram_id}/ai-limit")
async def admin_ai_limit_route(request):
    """Временно ограничить ИИ для человека (баг, перерасход). body: {"action": "block", "hours": 1|24|168} — отключить на срок;
    {"action": "unblock"} — вернуть; {"action": "day_limit", "tokens": 20000|50000|100000|200000|null} — личный дневной лимит токенов."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    action = (body.get("action") or "").strip()
    if action == "block":
        result = ai_usage_db.block_ai(telegram_id, body.get("hours"))
    elif action == "unblock":
        result = ai_usage_db.unblock_ai(telegram_id)
    elif action == "day_limit":
        result = ai_usage_db.set_day_limit(telegram_id, body.get("tokens"))
    else:
        return web.json_response({"error": "invalid_action"}, status=400)
    if "error" in result:
        return web.json_response(result, status=AI_LIMIT_ERRORS.get(result["error"], 400))
    return web.json_response({"ok": True, "usage": ai_usage_db.user_usage(telegram_id)})


# ---------------------------------------------------------------------------------------------------------------- доступ

ACCESS_ERRORS = {"user_not_found": 404, "invalid_days": 400, "unlimited": 409}


def _access_push_text(action, days, access):
    """Короткое сообщение человеку о выданном доступе. Без обращения по полу, без слов про оплату."""
    from adam_messages import day_word
    until = (access.get("free_end") or "")[:10]
    try:
        until = datetime.fromisoformat(until).strftime("%d.%m")
    except ValueError:
        until = ""
    tail = f" — до {until}" if until else ""
    if action == "gift":
        return f"🎁 Тебе подарили {days} {day_word(days)} использования ADAM{tail}. Спасибо, что ты с нами!"
    return f"🔑 Тебе открыт тестовый доступ к ADAM на {days} {day_word(days)}{tail}. Спасибо, что помогаешь делать приложение лучше!"


@routes.get("/api/admin/user/{telegram_id}/access")
async def admin_access_get_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    info = get_access(telegram_id)
    if info is None:
        return web.json_response({"error": "user_not_found"}, status=404)
    return web.json_response({"access": info, "log": get_access_log(telegram_id, limit=15)})


@routes.post("/api/admin/user/{telegram_id}/access")
async def admin_access_set_route(request):
    """Управление доступом человека. body: {"action": "grant"|"gift"|"unlimited"|"trial_on"|"trial_restart", "days": 7,
    "notify": true, "reset_onboarding": false}.
      grant  — временный доступ на 3 / 7 / 14 дней (свободному аккаунту включает режим «временный»);
      gift   — подарочные дни 3 / 7 / 14 / 30: платящему сдвигают срок подписки (и срок списания), остальным удлиняют бесплатный период;
      unlimited — снять ограничения (как у первых тестировщиков);
      trial_on / trial_restart — аккаунт идёт по сценарию триала (reset_onboarding — заодно показать весь онбординг заново)."""
    admin_id = await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    action = (body.get("action") or "").strip()
    days = body.get("days")
    if action == "grant":
        result = grant_access(telegram_id, days, admin_id)
    elif action == "gift":
        result = gift_days(telegram_id, days, admin_id)
    elif action == "unlimited":
        result = set_unlimited(telegram_id, admin_id)
    elif action in ("trial_on", "trial_restart"):
        result = start_trial_mode(telegram_id, admin_id, restart=(action == "trial_restart"))
        if "error" not in result and body.get("reset_onboarding"):
            reset_onboarding(telegram_id)
    else:
        return web.json_response({"error": "invalid_action"}, status=400)
    if "error" in result:
        return web.json_response(result, status=ACCESS_ERRORS.get(result["error"], 400))

    notified = False
    bot = request.app.get("bot")
    if bot is not None and action in ("grant", "gift") and body.get("notify", True):
        try:
            await bot.send_message(telegram_id, _access_push_text(action, int(days), result))
            notified = True
        except Exception:
            logger.warning("Не удалось отправить пуш о доступе пользователю %s", telegram_id)
    return web.json_response({"ok": True, "access": result, "notified": notified, "log": get_access_log(telegram_id, limit=8)})


@routes.get("/api/admin/access/overview")
async def admin_access_overview_route(request):
    """Все, у кого не безлимит: временный доступ и сценарий триала, ближайшие к концу — первыми."""
    await _authenticate_admin(request)
    return web.json_response(access_overview())


# ---------------------------------------------------------------------------------------------------------------- триал

TRIAL_ERRORS = {"not_in_trial": 409, "invalid_day": 400}


def _trial_catalog():
    import trial_messages
    from db import trial as trial_db
    states = trial_db.message_states()
    global_on = states.get(trial_db.ALL, True)
    return global_on, [
        {"key": m.key, "label": m.label, "when": m.when, "buttons": list(m.buttons), "phase": m.phase,
         "enabled": states.get(m.key, True)}
        for m in trial_messages.CATALOG
    ]


def _trial_state(telegram_id):
    """Всё про триал человека для карточки в админке: состояние, что подходит сейчас, что уже отправлено, платежи."""
    import trial_messages
    from db import trial as trial_db, list_user_payments
    snap = trial_db.snapshot(telegram_id)
    if snap is None:
        return None
    return {
        "snapshot": snap,
        "due": [m.key for m in trial_messages.due_messages(snap)],
        "sent": trial_db.sent_log(telegram_id, limit=20),
        "payments": [{"charge_id": p["charge_id"], "amount": p["amount"], "created_at": p["created_at"], "refunded_at": p["refunded_at"]}
                     for p in list_user_payments(telegram_id, limit=5)],
    }


@routes.get("/api/admin/trial")
async def admin_trial_overview_route(request):
    """Общие настройки триала: тумблеры сообщений (все сразу и по одному) и параметры сценария."""
    await _authenticate_admin(request)
    from config import (
        CLOSED_CHANNEL_ID, SUBSCRIPTION_GATE_ENABLED, SUBSCRIPTION_INTRO_PRICE_STARS, SUBSCRIPTION_RENEWAL_PRICE_STARS,
        SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL, SUBSCRIPTION_TRIAL_DAYS,
    )
    global_on, messages = _trial_catalog()
    return web.json_response({
        "enabled": global_on, "messages": messages,
        "config": {"trial_days": SUBSCRIPTION_TRIAL_DAYS, "intro_price_stars": SUBSCRIPTION_INTRO_PRICE_STARS,
                   "renewal_price_stars": SUBSCRIPTION_RENEWAL_PRICE_STARS, "streak_needed": SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL,
                   "channel_configured": bool(CLOSED_CHANNEL_ID), "gate_enabled": bool(SUBSCRIPTION_GATE_ENABLED)},
    })


@routes.post("/api/admin/trial/toggle")
async def admin_trial_toggle_route(request):
    """body: {"key": "*" | ключ сообщения, "enabled": true|false} — включить/выключить сообщения триала."""
    await _authenticate_admin(request)
    import trial_messages
    from db import trial as trial_db
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    key = str(body.get("key") or "")
    if key != trial_db.ALL and key not in trial_messages.BY_KEY:
        return web.json_response({"error": "unknown_message"}, status=400)
    trial_db.set_message_enabled(key, bool(body.get("enabled")))
    global_on, messages = _trial_catalog()
    return web.json_response({"ok": True, "enabled": global_on, "messages": messages})


@routes.get("/api/admin/user/{telegram_id}/trial")
async def admin_user_trial_get_route(request):
    await _authenticate_admin(request)
    state = _trial_state(int(request.match_info["telegram_id"]))
    if state is None:
        return web.json_response({"error": "user_not_found"}, status=404)
    return web.json_response(state)


@routes.post("/api/admin/user/{telegram_id}/trial")
async def admin_user_trial_action_route(request):
    """Проверка триала с тестового аккаунта. body: {"action": ...}
      shift {day}      — «машина времени»: сегодня у аккаунта (в режиме «trial») день N;
      reset            — обнулить старт: триал с дня 1, забыть отправленные сообщения и тестовую оплату (reset_onboarding — и онбординг);
      send {key, to}   — отправить сообщение каталога: to «me» (превью админу) или «user» (человеку, как будто пришло по расписанию);
      pay_test         — тестовая оплата: тот же путь, что у настоящего платежа (сообщение после оплаты, ссылка в канал), без Stars;
      unpay            — стереть тестовую оплату и «платил раньше»."""
    admin_id = await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    import trial_messages
    from db import trial as trial_db
    from db.access import shift_trial_day
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    if get_user(telegram_id) is None:
        return web.json_response({"error": "user_not_found"}, status=404)
    action = (body.get("action") or "").strip()
    bot = request.app.get("bot")
    info = trial_db.snapshot(telegram_id)
    in_trial = info is not None and info["mode"] == "trial"
    result = {}

    if action == "shift":
        outcome = shift_trial_day(telegram_id, body.get("day"), admin_id)
        if "error" in outcome:
            return web.json_response(outcome, status=TRIAL_ERRORS.get(outcome["error"], 400))
    elif action in ("reset", "unpay", "pay_test"):
        if not in_trial:
            return web.json_response({"error": "not_in_trial"}, status=409)
        if action == "reset":
            start_trial_mode(telegram_id, admin_id, restart=True)
            trial_db.clear_trial_messages(telegram_id)
            trial_db.clear_test_payment(telegram_id)
            if body.get("reset_onboarding"):
                reset_onboarding(telegram_id)
        elif action == "unpay":
            trial_db.clear_test_payment(telegram_id)
        else:
            if bot is None:
                return web.json_response({"error": "bot_unavailable"}, status=503)
            from subscription_flow import complete_subscription_payment
            from keyboards import trial_keyboard
            text, snap, link = await complete_subscription_payment(bot, telegram_id, test=True)
            try:
                await bot.send_message(telegram_id, text, reply_markup=trial_keyboard(("open",), snap["price_stars"]))
                result["delivered"] = True
            except Exception:
                logger.warning("Не удалось отправить текст после тест-оплаты %s", telegram_id)
                result["delivered"] = False
            result["text"] = text
    elif action == "send":
        key, target = str(body.get("key") or ""), body.get("to") or "me"
        message = trial_messages.BY_KEY.get(key)
        if message is None or target not in ("me", "user"):
            return web.json_response({"error": "invalid_message"}, status=400)
        if target == "user" and message.action:
            return web.json_response({"error": "not_for_user"}, status=400)
        if bot is None:
            return web.json_response({"error": "bot_unavailable"}, status=503)
        from subscription_scheduler import build_message
        text, keyboard = build_message(key, info)
        if target == "me":
            from html import escape as html_escape
            who = html_escape(info["name"] or str(telegram_id))
            text = f"🧪 <b>Превью</b> · {who} · день {info['day']} · {info['phase']}\n\n{text}"
        try:
            await bot.send_message(admin_id if target == "me" else telegram_id, text, reply_markup=keyboard)
        except Exception as exc:
            return web.json_response({"error": "send_failed", "detail": str(exc)[:200]}, status=502)
        result["sent_to"] = target
    else:
        return web.json_response({"error": "invalid_action"}, status=400)

    return web.json_response({"ok": True, **result, **(_trial_state(telegram_id) or {}), "access": get_access(telegram_id)})


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


@routes.get("/api/admin/abuse")
async def admin_abuse_route(request):
    """«Накрутка наград»: последние подозрения (много удалений/созданий привычек, создание пачкой), пороги и кто заморожен."""
    await _authenticate_admin(request)
    from db import habit_guard
    return web.json_response(habit_guard.overview())


@routes.post("/api/admin/abuse/{user_id}/freeze")
async def admin_abuse_freeze_route(request):
    """Заморозить человеку награды за привычки (body: {"hours": 24 | 168}) или снять заморозку ({"hours": 0}). Отметки и серия считаются как обычно."""
    await _authenticate_admin(request)
    try:
        user_id = int(request.match_info["user_id"])
        body = await request.json()
        hours = int(body.get("hours"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return web.json_response({"error": "invalid_request"}, status=400)
    from db import habit_guard
    if hours != 0 and hours not in habit_guard.FREEZE_CHOICES_HOURS:
        return web.json_response({"error": "invalid_hours"}, status=400)
    result = habit_guard.freeze_rewards(user_id, hours)
    if not result["found"]:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True, "frozen_until": result["until"]})


@routes.post("/api/admin/usernames/sync")
async def admin_usernames_sync_route(request):
    """Сверить Telegram-ники ВСЕХ людей прямо сейчас (обычно это делается само: при заходах и раз в ночь, см. username_sync.py)."""
    await _authenticate_admin(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)
    import username_sync
    stats = await username_sync.run_username_sync(bot, limit=username_sync.MANUAL_LIMIT, stale_days=0)
    return web.json_response(stats)


@routes.get("/api/admin/referrals")
async def admin_referrals_route(request):
    """Приглашения друзей: сводка, состояние выключателей и последние приглашения с признаками для проверки (db/referrals.py)."""
    await _authenticate_admin(request)
    from db import referrals
    return web.json_response(referrals.admin_overview())


@routes.post("/api/admin/referrals/{invitee_id}/revoke")
async def admin_referral_revoke_route(request):
    """Отозвать награду за приглашение: коины списываются (не глубже нуля), слот пригласившего остаётся занятым."""
    admin_id = await _authenticate_admin(request)
    try:
        invitee_id = int(request.match_info["invitee_id"])
    except ValueError:
        return web.json_response({"error": "invalid_user"}, status=400)
    from db import referrals
    result = referrals.revoke_invite(invitee_id, admin_id)
    if "error" in result:
        return web.json_response(result, status=404 if result["error"] == "not_found" else 409)
    return web.json_response(result)


@routes.post("/api/admin/referrals/preview")
async def admin_referral_preview_route(request):
    """Прислать САМОМУ АДМИНУ сообщение рассылки «позови друга» с рабочей кнопкой (body: step 0–2, second — вариант для тех, у кого один слот уже занят)."""
    admin_id = await _authenticate_admin(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    import referral_mailing
    from db import referrals
    try:
        step = int(body.get("step") or 0)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_step"}, status=400)
    if not 0 <= step < len(referral_mailing.STEP_DAYS):
        return web.json_response({"error": "invalid_step"}, status=400)
    text = referral_mailing.compose_text(step, 1 if body.get("second") else 0, referrals.pay_mode())
    username = (await bot.get_me()).username
    await bot.send_message(admin_id, "🧪 Тест · " + text, reply_markup=referral_mailing.share_markup(username, admin_id))
    return web.json_response({"ok": True, "text": text})


@routes.get("/api/admin/churn-risk")
async def admin_churn_risk_route(request):
    """Roadmap #45 — сводка риска оттока: сколько пользователей в каждом
    тире + список самых 'горящих'."""
    await _authenticate_admin(request)
    return web.json_response(get_churn_risk_report())


@routes.get("/api/admin/server-errors")
async def admin_server_errors_route(request):
    """Сырые записи error_log (db/ai.py::log_error) — ежедневная сводка/
    алерт (🩺 Мониторинг ошибок) даёт только счётчик по scope, без текста
    самой ошибки. ?scope=habit_checkpoint_10 фильтрует по конкретному
    тегу, без параметра — последние ошибки по всем scope."""
    await _authenticate_admin(request)
    scope = request.query.get("scope") or None
    try:
        hours = min(int(request.query.get("hours", 24)), 24 * 30)
    except (TypeError, ValueError):
        hours = 24
    try:
        limit = min(int(request.query.get("limit", 50)), 500)
    except (TypeError, ValueError):
        limit = 50
    return web.json_response({
        "stats": get_error_stats(hours=hours),
        "errors": get_recent_errors(scope=scope, hours=hours, limit=limit),
    })


@routes.get("/api/admin/client-errors")
async def admin_client_errors_route(request):
    """Улучшение #70 — лента последних JS-ошибок с реальных устройств
    пользователей, вместо ручных отчётов "странички лагают" со скриншотами."""
    await _authenticate_admin(request)
    return web.json_response({"errors": get_recent_client_errors(limit=100)})


@routes.get("/api/admin/perf-events")
async def admin_perf_events_route(request):
    """Разрывы кадров/длинные JS-таски с реальных устройств (app.js::
    reportPerfEvent) — тот же принцип, что и client-errors выше: точные
    цифры с телефона пользователя вместо разбора видео "лагает/чернеет
    при прокрутке" покадрово. summary — агрегат по типу события (сколько
    раз, средняя/максимальная длительность, сколько за последние сутки),
    events — последние сырые записи для разбора конкретных случаев."""
    await _authenticate_admin(request)
    try:
        limit = min(int(request.query.get("limit", 200)), 1000)
    except (TypeError, ValueError):
        limit = 200
    return web.json_response({
        "summary": get_perf_summary(),
        "events": get_recent_perf_events(limit=limit),
    })


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


@routes.post("/api/admin/user/{telegram_id}/reset-onboarding")
async def admin_reset_onboarding_route(request):
    """Для разработчика/тестировщика — повторно увидеть ВЕСЬ онбординг
    (стартовый квиз с тест-архетипом + контекстные подсказки по интерфейсу)
    с шага 1, а не с той стадии, на которой тестировщик остановился в
    прошлый раз."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    reset_onboarding(telegram_id)
    return web.json_response({"ok": True})


@routes.post("/api/admin/user/{telegram_id}/premium")
async def admin_premium_route(request):
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    give_premium_admin(telegram_id)
    return web.json_response({"ok": True})


@routes.post("/api/admin/user/{telegram_id}/badge")
async def admin_badge_route(request):
    """Выдать медаль 🏅 как «Эксклюзивный подарок от Администратора» (за пользу приложению и
    вклад в его развитие). Человек увидит окно с анимацией при следующем входе в Mini App и
    получит сообщение от бота. body: {"note": "необязательный текст"}."""
    await _authenticate_admin(request)
    telegram_id = int(request.match_info["telegram_id"])
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    result = grant_admin_badge(telegram_id, body.get("note"))
    if "error" in result:
        status = 404 if result["error"] == "user_not_found" else 409
        return web.json_response(result, status=status)
    bot = request.app.get("bot")
    notified = False
    if bot is not None:
        try:
            await bot.send_message(
                telegram_id,
                "🎖 <b>Эксклюзивный подарок от Администратора</b>\n\n"
                f"Тебе подарили медаль: {result['note']}.\nОткрой ADAM — она уже у твоего имени.",
                parse_mode="HTML",
            )
            notified = True
        except Exception:
            logger.warning("Не удалось отправить пуш о медали пользователю %s", telegram_id)
    return web.json_response({"ok": True, "note": result["note"], "notified": notified})


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
