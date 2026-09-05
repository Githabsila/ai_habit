import gzip
import json
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web
from aiohttp.web import Application
from io import BytesIO
from PIL import Image, UnidentifiedImageError

from config import BOT_TOKEN, ADMIN_IDS
from webapp.telegram_auth import validate_init_data
from webapp.services.ai_coach import ask_ai
from adam_messages import (
    format_all_tasks_done_message, format_main_goal_done_message,
    format_secondary_task_praise, SECONDARY_TASK_PRAISE_STRICT_DAYS, SECONDARY_TASK_PRAISE_STRICT_COUNT,
    format_perfect_habit_streak_message, format_month_end_reward_message,
)

from db.core import DATA_DIR

from db import (
    get_user, add_user, is_banned, get_access_status, set_access_status,
    get_habits, get_habit, add_habit, edit_habit, delete_habit,
    complete_habit, get_progress, get_settings,
    skip_habit, unskip_habit, HABIT_CATEGORIES,
    update_reminder_time, toggle_reminders, update_ai_style, get_ai_style,
    set_quiet_hours, clear_quiet_hours,
    get_shop_items, buy_shop_item, get_user_items, get_shop_item,
    has_item, get_item_owner_ids, update_theme, get_theme, set_cosmetic,
    get_color_mode, update_color_mode,
    get_language, set_language,
    get_gender, set_gender,
    touch_last_seen,
    has_reached_daily_limit, log_stars_purchase,
    get_rating, get_calendar, get_achievements, ACHIEVEMENT_ICONS,
    was_premium_purchased, give_premium,
    get_daily_plan, save_daily_plan, set_daily_main_goal, delete_daily_main_goal, toggle_daily_main_goal, add_daily_task, update_daily_plan_task, delete_daily_task, toggle_daily_task,
    get_streak_status, set_timezone, buy_freeze, claim_weekly_reward, get_weekly_bonus_available, has_streak_frame,
    restore_streak_free,
    should_show_onboarding, onboarding_message, mark_onboarding_seen, consume_completion_event,
    create_daily_tasks, get_daily_tasks, claim_daily_bonus,
    get_weekly_summary, get_statistics,
    get_progress_comparison, get_streak_forecast,
    get_milestones, save_milestones, toggle_milestone,
    reset_progress,
    cache_get, cache_set, log_error,
    get_bonus_window,
    get_secondary_task_praise_state, record_secondary_task_praise,
    get_monthly_progress, consume_month_end_reward_event,
    get_subscription_status, try_grant_channel_access, bot_access_allowed,
    should_show_app_tour, mark_app_tour_seen,
    increment_habit_progress, get_weekly_progress,
    add_habit_note, get_recent_habit_notes,
    MAX_TARGET_COUNT, MAX_FREQUENCY_PER_WEEK,
    get_daily_quests, claim_daily_quest,
    get_league_tier, get_league_progress,
    is_xp_booster_active,
    set_public_profile_enabled, get_public_profile,
    send_reaction, get_recent_reactions_received, has_reacted_today, REACTION_EMOJIS,
    set_long_term_goals, get_long_term_goals,
    get_struggling_habits, suggest_optimal_reminder_time,
    get_habit_correlations,
    set_archetype, ARCHETYPES,
    get_pet,
    get_season_leaderboard, get_season_rank,
    create_team, join_team, leave_team, get_my_team,
    get_friend_activity_feed,
    get_notification_history,
    log_client_error,
    export_full_account_data, request_account_deletion,
    get_unseen_changelog_entries, mark_changelog_seen,
)

from datetime import date, datetime, timezone
from multi_agent import generate_progress_analysis
from webapp.services.ai_utils import build_user_context

logger = logging.getLogger("webapp")
BASE_DIR = Path(__file__).parent
routes = web.RouteTableDef()

# ID товаров магазина, у которых есть реальный эффект в мини-приложении
# (см. посев в db.py: create_tables): 2 — тема оформления, 3 — значок в рейтинге.
THEME_ITEM_ID = 2
BADGE_ITEM_ID = 3

# ====================== АВТОРИЗАЦИЯ ======================

def _extract_init_data(request):
    header = request.headers.get("Authorization", "")
    if header.startswith("tma "):
        return header[4:]
    return request.headers.get("X-Telegram-Init-Data", "")

# ====================== RATE LIMITING ======================
#
# Раньше у API Mini App'а не было вообще никакой защиты от частоты
# запросов: например /api/feedback можно было дёргать без ограничений и
# заспамить админов сообщениями, а /api/progress/ai-analysis (платный
# вызов OpenAI) — хоть и кэшируется на день, но до первого удачного ответа
# каждый повторный запрос в том же дне снова пытался дозвониться до AI.
# In-memory sliding-window на telegram_id — этого достаточно для одного
# Railway-процесса (см. комментарий про единственный инстанс в
# scheduler.py) и не требует Redis ради одной защиты от спама. Данные не
# переживают рестарт процесса — это ок, при рестарте лимит просто обнуляется.
RATE_LIMIT_WINDOW_SECONDS = 10
RATE_LIMIT_MAX_REQUESTS = 40  # с запасом на нормальную работу Mini App (bootstrap + вкладки)
_rate_limit_buckets = defaultdict(deque)


def _check_rate_limit(telegram_id):
    """True, если запрос разрешён; попутно чистит устаревшие метки времени,
    чтобы _rate_limit_buckets не рос бесконечно для активных пользователей."""
    now = time.monotonic()
    bucket = _rate_limit_buckets[telegram_id]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    bucket.append(now)
    return True


async def _authenticate(request):
    init_data = _extract_init_data(request)
    tg_user = validate_init_data(init_data, BOT_TOKEN)
    if tg_user is None:
        # reason= сам по себе не даёт тела ответа — фронт (app.js api())
        # читает err.data.error, и без тела friendlyError() не мог
        # показать "Telegram не передал данные авторизации", падая на
        # обобщённое "Неизвестная ошибка".
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "invalid_init_data"}),
            content_type="application/json",
        )

    telegram_id = tg_user["id"]
    is_admin = telegram_id in ADMIN_IDS

    if not _check_rate_limit(telegram_id):
        raise web.HTTPTooManyRequests(
            text=json.dumps({"error": "rate_limited"}),
            content_type="application/json",
        )

    if get_user(telegram_id) is None:
        add_user(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name", "")
        )

    if is_banned(telegram_id):
        raise web.HTTPForbidden(text='{"error":"banned"}', content_type="application/json")

    if not is_admin:
        status = get_access_status(telegram_id) or "approved"
        # Новый Telegram-пользователь не должен получать 403 access_new
        # из Mini App: это полностью ломало bootstrap и все лениво
        # загружаемые разделы (магазин, рейтинг, календарь).
        # Анкета в боте сохраняется как отдельный процесс, а Mini App
        # остаётся доступным сразу после корректной Telegram-аутентификации.
        if status == "new":
            set_access_status(telegram_id, "approved")
            status = "approved"
        if status != "approved":
            raise web.HTTPForbidden(
                text=json.dumps({
                    "error": f"access_{status}",
                    "message": "Доступ к приложению пока ожидает подтверждения"
                }),
                content_type="application/json",
            )

    # Пром 13: гейт триал → подписка. Выключен по умолчанию
    # (config.SUBSCRIPTION_GATE_ENABLED) и даже включённый не трогает
    # пользователей, зарегистрированных до SUBSCRIPTION_GATE_CUTOVER —
    # см. db/subscription.py bot_access_allowed/gate_applies_to.
    if not is_admin and not bot_access_allowed(telegram_id):
        status = get_subscription_status(telegram_id)
        raise web.HTTPForbidden(
            text=json.dumps({
                "error": "trial_expired",
                "message": "Пробный период закончился",
                "subscription": status,
            }),
            content_type="application/json",
        )

    # Аналитика (db/analytics.py): каждый успешно авторизованный запрос
    # Mini App = живой пользователь сегодня. _authenticate вызывается из
    # каждого API-роута, поэтому это надёжный источник DAU без отдельного
    # мидлвара на каждый маршрут.
    touch_last_seen(telegram_id)

    return telegram_id, is_admin

# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================

def _owned_habit_or_404(habit_id, telegram_id):
    habit = get_habit(habit_id)
    if not habit or habit["user_id"] != telegram_id:
        raise web.HTTPNotFound()


async def _push(app, telegram_id, text):
    """Проактивное сообщение из Mini App в чат с ботом (поздравления —
    промт п.3 и п.7). Молча пропускаем, если бот недоступен или пользователь
    закрыл чат с ботом — это не должно ронять сам API-запрос."""
    bot = app.get("bot")
    if not bot:
        return
    try:
        await bot.send_message(telegram_id, text, parse_mode="HTML")
    except Exception:
        logger.warning(f"Не удалось отправить push-уведомление {telegram_id}", exc_info=True)

# ====================== MIDDLEWARE ======================

@web.middleware
async def error_middleware(request, handler):
    try:
        response = await handler(request)
        # Статические файлы Mini App тоже не кэшируем: иначе Telegram/WebView
        # может оставить старый app.js/style.css после редеплоя.
        if request.path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif request.path.startswith("/static/"):
            # JS/CSS URLs are versioned in index.html; long caching avoids
            # re-downloading ~100KB+ of assets on every Mini App open.
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response
    except web.HTTPException:
        raise
    except Exception:
        logger.exception(f"Необработанная ошибка в {request.path}")
        return web.json_response({"error": "internal_error"}, status=500)

# ====================== МАРШРУТЫ ======================

# Юзернейм бота нужен фронту, чтобы собрать реферальную ссылку и текст
# для кнопки "Поделиться" (achievement-share-overlay) — раньше карточка
# только красиво показывалась и закрывалась, реального шаринга не было.
# Кэшируем на весь процесс: один вызов Telegram API вместо одного на
# каждое открытие Mini App.
_BOT_USERNAME_CACHE = {"value": None}


async def _get_bot_username(bot):
    if _BOT_USERNAME_CACHE["value"]:
        return _BOT_USERNAME_CACHE["value"]
    if not bot:
        return None
    try:
        me = await bot.get_me()
        _BOT_USERNAME_CACHE["value"] = me.username
        return me.username
    except Exception:
        return None


@routes.get("/api/bootstrap")
async def bootstrap(request):
    """Критический снимок для первого экрана.
    Здесь только данные, без которых Главная не может стать интерактивной.
    Рейтинг/календарь/архив достижений/магазин загружаются после первого кадра.
    """
    telegram_id, is_admin = await _authenticate(request)
    user = get_user(telegram_id)
    habits = get_habits(telegram_id)
    progress = get_progress(telegram_id)
    settings_row = get_settings(telegram_id)
    daily_plan = get_daily_plan(telegram_id)
    streak = get_streak_status(telegram_id)

    # Пром 8: если мини-апп перезагрузили посреди активного окна удвоения
    # Adam Coin, бейдж с обратным отсчётом должен восстановиться, а не
    # пропасть до следующей отметки привычки.
    bonus_until_dt = get_bonus_window(telegram_id)
    has_incomplete_habits = any(not h["completed"] for h in habits)
    bonus_active = bool(bonus_until_dt and bonus_until_dt > datetime.now(timezone.utc).replace(tzinfo=None) and has_incomplete_habits)
    bot_username = await _get_bot_username(request.app.get("bot"))

    return web.json_response({
        "bot_username": bot_username,
        "user": {
            "telegram_id": telegram_id,
            "first_name": user["first_name"] if user else "",
            "xp": user["xp"] if user else 0,
            "total_xp": user["total_xp"] if user else 0,
            "level": user["level"] if user else 1,
            "streak": user["streak"] if user else 0,
            "diamonds": user["diamonds"] if user and "diamonds" in user.keys() else 0,
            "premium": bool(user["premium"]) if user else False,
            "badge": has_item(telegram_id, BADGE_ITEM_ID),
            "avatar_id": user["avatar_id"] if user else "default",
            "frame_id": user["frame_id"] if user else "default",
            "is_admin": is_admin,
            "league_tier": get_league_tier(user["total_xp"] if user else 0),
            "league_progress": get_league_progress(user["total_xp"] if user else 0),
            "archetype": (ARCHETYPES.get(user["archetype"]) if user and "archetype" in user.keys() and user["archetype"] else None),
            "xp_boosted": is_xp_booster_active(telegram_id),
            "xp_boost_until": user["bonus_2x_xp_until"] if user and "bonus_2x_xp_until" in user.keys() else None,
        },
        "daily_quests": get_daily_quests(telegram_id),
        "pet": get_pet(telegram_id),
        "monthly_progress": get_monthly_progress(telegram_id),
        "habits": [
            {
                "id": h["id"],
                "title": h["title"],
                "completed": bool(h["completed"]),
                "planned_time": h["planned_time"] if "planned_time" in h.keys() else None,
                "time_window_minutes": h["time_window_minutes"] if "time_window_minutes" in h.keys() else 60,
                "category": h["category"] if "category" in h.keys() else None,
                "priority": h["priority"] if "priority" in h.keys() and h["priority"] else 1,
                "skip_reason": h["skip_reason"] if "skip_reason" in h.keys() else None,
                "target_count": h["target_count"] if "target_count" in h.keys() and h["target_count"] else 1,
                "progress_count": h["progress_count"] if "progress_count" in h.keys() and h["progress_count"] else 0,
                "frequency_per_week": h["frequency_per_week"] if "frequency_per_week" in h.keys() else None,
                "weekly_progress": (
                    get_weekly_progress(h["id"], telegram_id)
                    if "frequency_per_week" in h.keys() and h["frequency_per_week"] else None
                ),
                "chain_trigger_habit_id": h["chain_trigger_habit_id"] if "chain_trigger_habit_id" in h.keys() else None,
                # Roadmap #23/#36 — только когда у привычки ЕЩЁ нет своего
                # времени: если планово время уже стоит, подсказывать нечего.
                "suggested_time": (
                    suggest_optimal_reminder_time(h["id"], telegram_id)
                    if not (h["planned_time"] if "planned_time" in h.keys() else None) else None
                ),
            }
            for h in habits
        ],
        # Roadmap #22 — привычки, проваленные несколько дней подряд, для
        # мягкой подсказки "может, снизить планку?".
        "struggling_habits": get_struggling_habits(telegram_id),
        "habit_categories": HABIT_CATEGORIES,
        "progress": progress,
        "streak": streak,
        "streak_onboarding": {
            "show": bool(habits) and should_show_onboarding(telegram_id),
            "message": onboarding_message(telegram_id) if habits and should_show_onboarding(telegram_id) else None,
        },
        "show_app_tour": should_show_app_tour(telegram_id),
        "settings": {
            "reminders": bool(settings_row["reminders"]) if settings_row else True,
            "reminder_hour": settings_row["reminder_hour"] if settings_row else 9,
            "reminder_minute": settings_row["reminder_minute"] if settings_row else 0,
            "ai_style": get_ai_style(telegram_id),
            "theme_owned": has_item(telegram_id, THEME_ITEM_ID),
            "theme": get_theme(telegram_id),
            "color_mode": get_color_mode(telegram_id),
            "language": get_language(telegram_id),
            "gender": get_gender(telegram_id),
            "quiet_hours": (
                {"start": settings_row["quiet_hours_start"], "end": settings_row["quiet_hours_end"]}
                if settings_row and "quiet_hours_start" in settings_row.keys()
                and settings_row["quiet_hours_start"] is not None and settings_row["quiet_hours_end"] is not None
                else None
            ),
            "public_profile_enabled": bool(user["public_profile_enabled"]) if user and "public_profile_enabled" in user.keys() else False,
            "long_term_goals": get_long_term_goals(telegram_id),
        },
        "daily_plan": {
            "main_goal": daily_plan["main_goal"],
            "main_goal_completed": bool(daily_plan["main_goal_completed"]),
            "tasks": [
                {"id": t["id"], "text": t["text"], "completed": bool(t["completed"])}
                for t in daily_plan["tasks"]
            ],
        },
        "bonus_window": {
            "active": bonus_active,
            "until": bonus_until_dt.isoformat() if bonus_active else None,
        },
    })


@routes.get("/api/quests")
async def daily_quests_route(request):
    """Roadmap #12 — ежедневные микро-квесты на сегодня."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({"quests": get_daily_quests(telegram_id)})


@routes.post("/api/quests/{quest_key}/claim")
async def claim_quest_route(request):
    telegram_id, _ = await _authenticate(request)
    quest_key = request.match_info["quest_key"]
    reward = claim_daily_quest(telegram_id, quest_key)
    if reward is None:
        return web.json_response({"error": "quest_not_claimable"}, status=400)
    return web.json_response({"ok": True, "reward": reward, "progress": get_progress(telegram_id)})


@routes.get("/api/pet")
async def pet_route(request):
    """Roadmap #11 — виртуальный питомец."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response(get_pet(telegram_id))


@routes.get("/api/season")
async def season_route(request):
    """Roadmap #9 — сезонный (месячный) рейтинг."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({
        "leaderboard": get_season_leaderboard(limit=10),
        "my_rank": get_season_rank(telegram_id),
    })


@routes.get("/api/team")
async def team_route(request):
    """Roadmap #16 — моя команда (или null, если ни в одной не состою)."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({"team": get_my_team(telegram_id)})


@routes.post("/api/team/create")
async def team_create_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    team = create_team(telegram_id, body.get("name"))
    if team is None:
        return web.json_response({"error": "invalid_name"}, status=400)
    return web.json_response({"ok": True, "team": team})


@routes.post("/api/team/join")
async def team_join_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    result = join_team(telegram_id, body.get("invite_code"))
    if result is None:
        return web.json_response({"error": "invalid_code"}, status=404)
    if isinstance(result, dict) and result.get("error"):
        return web.json_response(result, status=400)
    return web.json_response({"ok": True, "team": result})


@routes.post("/api/team/leave")
async def team_leave_route(request):
    telegram_id, _ = await _authenticate(request)
    leave_team(telegram_id)
    return web.json_response({"ok": True})


@routes.get("/api/activity-feed")
async def activity_feed_route(request):
    """Roadmap #18 — лента активности друзей (команда + кому реагировали)."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({"events": get_friend_activity_feed(telegram_id)})


@routes.get("/api/notifications/history")
async def notification_history_route(request):
    """"Уведомления — прозрачно": история реально отправленных плановых
    push-ов (см. db/streak.py::claim_notification — единая точка почти
    для всех), а не чёрный ящик."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({"history": get_notification_history(telegram_id)})


@routes.get("/api/changelog/unseen")
async def changelog_unseen_route(request):
    """«Что нового» — см. db/changelog.py. Фронт показывает эти записи
    модальным окном один раз при открытии, затем сразу шлёт /seen."""
    telegram_id, _ = await _authenticate(request)
    return web.json_response({"entries": get_unseen_changelog_entries(telegram_id)})


@routes.post("/api/changelog/seen")
async def changelog_seen_route(request):
    telegram_id, _ = await _authenticate(request)
    mark_changelog_seen(telegram_id)
    return web.json_response({"ok": True})


@routes.get("/api/bootstrap-secondary")
async def bootstrap_secondary(request):
    """Лениво отдаёт данные только для открытого раздела Mini App.

    section=profile  -> магазин + достижения + тема
    section=rating   -> рейтинг
    section=calendar -> календарь
    Без section сохраняем старый формат для обратной совместимости.
    """
    telegram_id, _ = await _authenticate(request)
    section = request.rel_url.query.get("section", "").strip().lower()

    payload = {}

    if section in ("", "profile"):
        owned_item_ids = set(get_user_items(telegram_id))
        profile_user = get_user(telegram_id)
        user_frame_id = profile_user["frame_id"] if profile_user else "default"
        shop_items = get_shop_items()
        achievements = get_achievements(telegram_id)
        payload["shop_items"] = [
            {
                "id": it["id"],
                "name": it["name"],
                "description": it["description"],
                "price": it["price"],
                "owned": it["id"] in owned_item_ids or (it["item_type"] == "frame_stars" and user_frame_id == "paid_double_gold"),
                "item_type": it["item_type"] if "item_type" in it.keys() else None,
                "payload": it["payload"] if "payload" in it.keys() else None,
            }
            for it in shop_items
        ]
        payload["achievements"] = [
            {
                "id": a["id"],
                "title": a["title"],
                "description": a["description"],
                "created_at": a["created_at"],
                "icon": ACHIEVEMENT_ICONS.get(a["title"], "🏅"),
            }
            for a in achievements
        ]

    if section in ("", "rating"):
        badge_owner_ids = set(get_item_owner_ids(BADGE_ITEM_ID))
        leaderboard = get_rating()
        payload["leaderboard"] = [
            {
                "telegram_id": row["telegram_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "xp": row["xp"],
                "level": row["level"],
                "streak": row["streak"],
                "badge": row["telegram_id"] in badge_owner_ids,
                "avatar_id": row["avatar_id"] if "avatar_id" in row.keys() else "default",
                "frame_id": row["frame_id"] if "frame_id" in row.keys() else "default",
                "streak_status": get_streak_status(row["telegram_id"]),
                "league_tier": get_league_tier(row["total_xp"] if "total_xp" in row.keys() else row["xp"]),
                "can_react": (
                    row["telegram_id"] != telegram_id
                    and not has_reacted_today(telegram_id, row["telegram_id"])
                ),
            }
            for row in leaderboard
        ]

    if section in ("", "calendar"):
        calendar_events = get_calendar(telegram_id)
        payload["calendar_events"] = [
            {"day": row["day"], "completed": row["completed"], "total": row["total"]}
            for row in calendar_events
        ]

    if section not in ("", "profile", "rating", "calendar"):
        return web.json_response({"error": "unknown_section"}, status=400)

    return web.json_response(payload)


import re as _re
_PLANNED_TIME_RE = _re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$')

def _parse_planned_time(raw):
    """Своё время напоминания у привычки — 'HH:MM' в локальном времени
    пользователя, или None ('без личного времени'). Пустая строка —
    валидное 'не задано', любая непустая нераспознанная строка — ошибка,
    чтобы кривой ввод не тихо терялся."""
    if raw in (None, ""):
        return None
    raw = str(raw).strip()
    if not _PLANNED_TIME_RE.match(raw):
        raise ValueError("invalid_time")
    return raw

@routes.post("/api/habits")
async def create_habit(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    title = body.get("title", "").strip()
    if len(title) < 2:
        return web.json_response({"error": "title_too_short"}, status=400)
    try:
        planned_time = _parse_planned_time(body.get("planned_time"))
    except ValueError:
        return web.json_response({"error": "invalid_time"}, status=400)
    category = body.get("category")
    if category not in HABIT_CATEGORIES:
        category = None
    priority = 2 if body.get("priority") == 2 else 1
    try:
        target_count = int(body.get("target_count") or 1)
    except (TypeError, ValueError):
        target_count = 1
    target_count = max(1, min(target_count, MAX_TARGET_COUNT))
    frequency_per_week = body.get("frequency_per_week")
    try:
        frequency_per_week = int(frequency_per_week) if frequency_per_week else None
    except (TypeError, ValueError):
        frequency_per_week = None
    if frequency_per_week is not None:
        frequency_per_week = max(1, min(frequency_per_week, MAX_FREQUENCY_PER_WEEK))
    chain_trigger_habit_id = body.get("chain_trigger_habit_id")
    try:
        chain_trigger_habit_id = int(chain_trigger_habit_id) if chain_trigger_habit_id else None
    except (TypeError, ValueError):
        chain_trigger_habit_id = None
    had_habits = bool(get_habits(telegram_id))
    try:
        add_habit(
            telegram_id, title, planned_time=planned_time, category=category, priority=priority,
            target_count=target_count, frequency_per_week=frequency_per_week,
            chain_trigger_habit_id=chain_trigger_habit_id,
        )
    except ValueError as exc:
        # habit_limit — уже максимум 10 привычек; habit_add_locked — сегодня
        # уже была отметка + удаление привычки, добавление заблокировано до
        # 00:00 (пром 10.2, защита от накрутки Adam Coin).
        if str(exc) in ("habit_limit", "habit_add_locked"):
            return web.json_response({"error": str(exc)}, status=400)
        raise
    first_habit = not had_habits
    # Возвращаем созданную запись, чтобы Mini App мог показать её сразу,
    # даже если повторная загрузка bootstrap временно задержалась.
    created = next((h for h in get_habits(telegram_id) if h["title"] == title), None)
    return web.json_response({
        "ok": True,
        "first_habit": first_habit,
        "habit": {
            "id": created["id"],
            "title": created["title"],
            "completed": bool(created["completed"]),
            "planned_time": created["planned_time"] if created and "planned_time" in created.keys() else None,
            "category": created["category"] if created and "category" in created.keys() else None,
            "priority": created["priority"] if created and "priority" in created.keys() and created["priority"] else 1,
            "target_count": created["target_count"] if created and "target_count" in created.keys() and created["target_count"] else 1,
            "progress_count": 0,
            "frequency_per_week": created["frequency_per_week"] if created and "frequency_per_week" in created.keys() else None,
            "chain_trigger_habit_id": created["chain_trigger_habit_id"] if created and "chain_trigger_habit_id" in created.keys() else None,
        } if created else None,
        "onboarding_message": onboarding_message(telegram_id) if first_habit else None,
    })

@routes.put("/api/habits/{habit_id}")
async def rename_habit(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    body = await request.json()
    new_title = body.get("title", "").strip()
    if len(new_title) < 2:
        return web.json_response({"error": "title_too_short"}, status=400)
    try:
        planned_time = _parse_planned_time(body.get("planned_time"))
    except ValueError:
        return web.json_response({"error": "invalid_time"}, status=400)
    category = body.get("category") if "category" in body else None
    if category is not None and category not in HABIT_CATEGORIES:
        category = None
    priority = body.get("priority") if "priority" in body else None
    target_count = body.get("target_count") if "target_count" in body else None
    edit_kwargs = {}
    if "frequency_per_week" in body:
        # None явно означает "снять периодичность, вернуть к ежедневной" —
        # отличаем от "поле вообще не прислали" (см. edit_habit's _UNSET).
        edit_kwargs["frequency_per_week"] = body.get("frequency_per_week")
    # edit_habit(planned_time=...) через COALESCE обновил бы NULL как
    # "не менять" — а нам как раз нужно уметь ОЧИЩАТЬ время (пользователь
    # снял галочку "напоминать"), поэтому колонку планового времени
    # обновляем отдельным явным запросом, а не через edit_habit().
    edit_habit(habit_id, new_title, category=category, priority=priority, target_count=target_count, **edit_kwargs)
    if "planned_time" in body:
        from db.core import connect
        conn = connect()
        conn.execute("UPDATE habits SET planned_time=? WHERE id=?", (planned_time, habit_id))
        conn.commit()
        conn.close()
    return web.json_response({"ok": True})


@routes.post("/api/habits/{habit_id}/skip")
async def skip_habit_route(request):
    """Осознанный пропуск привычки на сегодня с причиной (#6 из roadmap) —
    в отличие от простого игнорирования, не считается "провалом" в
    еженедельном AI-разборе и перестаёт слать напоминания на сегодня."""
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    body = await request.json()
    reason = (body.get("reason") or "").strip()
    if not reason:
        return web.json_response({"error": "reason_required"}, status=400)
    if len(reason) > 60:
        return web.json_response({"error": "reason_too_long"}, status=400)
    ok = skip_habit(habit_id, reason)
    if not ok:
        return web.json_response({"error": "already_completed"}, status=409)
    return web.json_response({"ok": True})


@routes.post("/api/habits/{habit_id}/unskip")
async def unskip_habit_route(request):
    """Отменяет пропуск — пользователь передумал и хочет вернуть привычку
    в обычный список на сегодня."""
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    unskip_habit(habit_id)
    return web.json_response({"ok": True})

@routes.post("/api/habits/{habit_id}/complete")
async def complete_habit_route(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    success = complete_habit(habit_id)
    if not success:
        return web.json_response({"error": "already_completed"}, status=409)

    event = consume_completion_event(telegram_id)
    # Если событие уже было доставлено в боте, Mini App всё равно получает
    # состояние streak, но не показывает повторное сообщение.
    streak = get_streak_status(telegram_id)
    if event:
        try:
            phrase = event["message"]
            await _push(request.app, telegram_id, f"🔥 +1 день ударного режима!\n\n{phrase}")
        except Exception:
            logger.exception("Не удалось отправить streak-сообщение")

    # Пром 13: если подписка уже оплачена и серия только что достигла
    # нужного порога — выдаём доступ в закрытый канал автоматически (без
    # этого пользователю пришлось бы возвращаться к сообщению об оплате).
    # try_grant_channel_access сам проверяет has_paid/channel_eligible/
    # channel_granted и ничего не делает, если условия не выполнены —
    # безопасно вызывать при каждой отметке привычки.
    bot = request.app.get("bot")
    if bot is not None:
        try:
            invite = await try_grant_channel_access(bot, telegram_id)
            if invite:
                await _push(
                    request.app, telegram_id,
                    f"🔑 Ты выполнил нужную серию ударного режима подряд — вот ссылка в закрытый канал: {invite}",
                )
        except Exception:
            logger.exception("Не удалось выдать доступ в закрытый канал")

    # Промт п.8: окно удвоения Adam Coin показываем большим окном только
    # ОДИН раз в день — сразу после самой первой привычки (event не пуст
    # только в этот момент), и только если есть чем его продолжать
    # (2+ привычки и ещё остались незакрытые). Дальнейшие повторные
    # открытия окна происходят молча — их отражает bonus_active/coins.
    show_bonus_intro = bool(event) and bool(success["bonus_active"])

    # Пром 8 (доп.): короткая похвала за "идеальный день" (закрыты все
    # привычки, их было 2+), плюс — раз в месяц, только на последний день —
    # награда за идеальный месяц (см. db/monthly_streak.py).
    perfect_day_message = (
        format_perfect_habit_streak_message(success["total_habits"])
        if success.get("perfect_day") else None
    )
    month_reward_event = consume_month_end_reward_event(telegram_id)
    month_reward_message = None
    if month_reward_event:
        month_reward_message = format_month_end_reward_message(
            days=get_monthly_progress(telegram_id)["total"],
            coins=month_reward_event["coins"],
            diamonds=month_reward_event["diamonds"],
        )
        try:
            await _push(request.app, telegram_id, f"💎 {month_reward_message}")
        except Exception:
            logger.exception("Не удалось отправить сообщение о награде месяца")

    return web.json_response({
        "ok": True,
        "progress": get_progress(telegram_id),
        "streak": streak,
        "streak_event": event,
        "coins": success["coins"],
        "doubled": success["doubled"],
        "bonus_active": success["bonus_active"],
        "bonus_until": success["bonus_until"],
        "show_bonus_intro": show_bonus_intro,
        "perfect_day_message": perfect_day_message,
        "monthly_progress": get_monthly_progress(telegram_id),
        "month_end_reward": (
            {"message": month_reward_message, **month_reward_event} if month_reward_event else None
        ),
        "chain_suggestion": success.get("chain_suggestion"),
        "xp_boosted": success.get("xp_boosted", False),
        "pet": success.get("pet"),
    })


@routes.post("/api/habits/{habit_id}/progress")
async def habit_progress_route(request):
    """Roadmap #1 — привычка-счётчик ("выпить 4 стакана"): +1 (или body.amount)
    к прогрессу. Как только прогресс достигает target_count, поведение
    полностью совпадает с complete_habit_route (те же монеты/streak/
    ачивки/цепочки) — просто достигается через несколько нажатий вместо
    одного."""
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    try:
        amount = int(body.get("amount") or 1)
    except (TypeError, ValueError):
        amount = 1
    amount = max(1, min(amount, MAX_TARGET_COUNT))

    result = increment_habit_progress(habit_id, amount=amount)
    if result is None:
        return web.json_response({"error": "already_completed"}, status=409)

    if not result.get("just_completed"):
        return web.json_response({
            "ok": True,
            "just_completed": False,
            "progress_count": result["progress_count"],
            "target_count": result["target_count"],
        })

    # Цель достигнута этим нажатием — привычка только что выполнена целиком,
    # дальше то же самое, что и в complete_habit_route (streak-событие,
    # доступ в канал, окно удвоения, идеальный день).
    event = consume_completion_event(telegram_id)
    streak = get_streak_status(telegram_id)
    if event:
        try:
            await _push(request.app, telegram_id, f"🔥 +1 день ударного режима!\n\n{event['message']}")
        except Exception:
            logger.exception("Не удалось отправить streak-сообщение")

    bot = request.app.get("bot")
    if bot is not None:
        try:
            invite = await try_grant_channel_access(bot, telegram_id)
            if invite:
                await _push(
                    request.app, telegram_id,
                    f"🔑 Ты выполнил нужную серию ударного режима подряд — вот ссылка в закрытый канал: {invite}",
                )
        except Exception:
            logger.exception("Не удалось выдать доступ в закрытый канал")

    show_bonus_intro = bool(event) and bool(result["bonus_active"])
    perfect_day_message = (
        format_perfect_habit_streak_message(result["total_habits"])
        if result.get("perfect_day") else None
    )

    return web.json_response({
        "ok": True,
        "just_completed": True,
        "progress_count": result["progress_count"],
        "target_count": result["target_count"],
        "progress": get_progress(telegram_id),
        "streak": streak,
        "streak_event": event,
        "coins": result["coins"],
        "doubled": result["doubled"],
        "bonus_active": result["bonus_active"],
        "bonus_until": result["bonus_until"],
        "show_bonus_intro": show_bonus_intro,
        "perfect_day_message": perfect_day_message,
        "monthly_progress": get_monthly_progress(telegram_id),
        "chain_suggestion": result.get("chain_suggestion"),
        "xp_boosted": result.get("xp_boosted", False),
        "pet": result.get("pet"),
    })


@routes.post("/api/habits/{habit_id}/note")
async def habit_note_route(request):
    """Roadmap #3 — заметка и/или мини-фото к сегодняшней отметке привычки."""
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    ok = add_habit_note(
        telegram_id, habit_id,
        note=body.get("note"), photo_data_url=body.get("photo_data_url"),
    )
    if not ok:
        return web.json_response({"error": "empty_note"}, status=400)
    return web.json_response({"ok": True})


@routes.get("/api/habits/notes")
async def habit_notes_route(request):
    """Roadmap #3 — 'дневник прогресса': последние заметки/фото по всем
    привычкам, новые сверху."""
    telegram_id, _ = await _authenticate(request)
    notes = get_recent_habit_notes(telegram_id, limit=40)
    return web.json_response({
        "notes": [
            {
                "habit_id": n["habit_id"],
                "day": n["day"],
                "note": n["note"],
                "photo_data_url": n["photo_data_url"],
            }
            for n in notes
        ]
    })


@routes.delete("/api/habits/{habit_id}")
async def delete_habit_route(request):
    telegram_id, _ = await _authenticate(request)
    habit_id = int(request.match_info["habit_id"])
    _owned_habit_or_404(habit_id, telegram_id)
    delete_habit(habit_id)
    return web.json_response({"ok": True})


@routes.get("/api/streak/status")
async def streak_status_route(request):
    telegram_id, _ = await _authenticate(request)
    status = get_streak_status(telegram_id)
    status["weekly_bonus_available"] = get_weekly_bonus_available(telegram_id)
    return web.json_response(status)

@routes.post("/api/streak/timezone")
async def set_streak_timezone(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    timezone = str(body.get("timezone", "UTC"))
    set_timezone(telegram_id, timezone)
    return web.json_response({"ok": True, "timezone": timezone})

@routes.post("/api/streak/onboarding/seen")
async def streak_onboarding_seen(request):
    telegram_id, _ = await _authenticate(request)
    mark_onboarding_seen(telegram_id)
    return web.json_response({"ok": True})

@routes.post("/api/tour/seen")
async def app_tour_seen(request):
    telegram_id, _ = await _authenticate(request)
    mark_app_tour_seen(telegram_id)
    return web.json_response({"ok": True})

@routes.post("/api/streak/freeze/buy")
async def streak_buy_freeze(request):
    telegram_id, _ = await _authenticate(request)
    result = buy_freeze(telegram_id)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)

@routes.post("/api/streak/restore-free")
async def streak_restore_free(request):
    """Улучшение #50 — бесплатное восстановление сорванной серии, не чаще
    раза в календарный месяц (см. db.streak.restore_streak_free)."""
    telegram_id, _ = await _authenticate(request)
    result = restore_streak_free(telegram_id)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)

@routes.post("/api/streak/weekly-reward")
async def streak_weekly_reward(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    result = claim_weekly_reward(telegram_id, body.get("reward"))
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)

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

@routes.post("/api/settings/theme")
async def set_theme(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    theme = body.get("theme")

    if not has_item(telegram_id, THEME_ITEM_ID):
        return web.json_response({"error": "theme_not_owned"}, status=403)

    if not update_theme(telegram_id, theme):
        return web.json_response({"error": "invalid_theme"}, status=400)

    return web.json_response({"ok": True})

@routes.post("/api/settings/color-mode")
async def set_color_mode_route(request):
    """Roadmap #48 — светлая/тёмная тема, бесплатно (в отличие от
    акцентного /api/settings/theme выше, который требует покупки)."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    mode = body.get("mode")
    if not update_color_mode(telegram_id, mode):
        return web.json_response({"error": "invalid_color_mode"}, status=400)
    return web.json_response({"ok": True, "mode": mode})

@routes.post("/api/settings/language")
async def set_language_route(request):
    """Roadmap #46 — язык интерфейса. Переключает и статичный текст Mini
    App (см. app.js::I18N), и язык AI-ответов (см.
    webapp/services/ai_utils.py — инструкция подмешивается в контекст
    каждого запроса к AI)."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    if not set_language(telegram_id, body.get("language")):
        return web.json_response({"error": "invalid_language"}, status=400)
    return web.json_response({"ok": True})

@routes.post("/api/settings/gender")
async def set_gender_route(request):
    """Фидбек: пол — чтобы умные напоминания правильно согласовывали "Ты"
    (сделал/сделала). См. db.users.set_gender/by_gender."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    if not set_gender(telegram_id, body.get("gender")):
        return web.json_response({"error": "invalid_gender"}, status=400)
    return web.json_response({"ok": True})

@routes.post("/api/settings/reminders/toggle")
async def toggle_reminders_route(request):
    telegram_id, _ = await _authenticate(request)
    enabled = toggle_reminders(telegram_id)
    return web.json_response({"ok": True, "reminders": enabled})

@routes.post("/api/settings/quiet-hours")
async def set_quiet_hours_route(request):
    """Roadmap #35 — "тихие часы". body: {"start": 0-23, "end": 0-23} чтобы
    включить/изменить окно, или {} (оба поля отсутствуют/null) чтобы
    выключить. start == end отклоняется — это пустое окно."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    start = body.get("start")
    end = body.get("end")

    if start is None and end is None:
        clear_quiet_hours(telegram_id)
        return web.json_response({"ok": True, "quiet_hours": None})

    if not set_quiet_hours(telegram_id, start, end):
        return web.json_response({"error": "invalid_quiet_hours"}, status=400)
    return web.json_response({"ok": True, "quiet_hours": {"start": int(start), "end": int(end)}})


@routes.post("/api/settings/public-profile")
async def set_public_profile_route(request):
    """Roadmap #17 — включает/выключает публичную витрину-профиль на
    /u/{telegram_id} (без авторизации, обычная HTTPS-ссылка)."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    set_public_profile_enabled(telegram_id, enabled)
    return web.json_response({"ok": True, "enabled": enabled})


@routes.get("/api/public/profile/{telegram_id}")
async def public_profile_route(request):
    """Публичный (без авторизации) JSON для страницы /u/{id} — намеренно
    узкий набор данных, см. db/public_profile.py::get_public_profile."""
    try:
        telegram_id = int(request.match_info["telegram_id"])
    except ValueError:
        return web.json_response({"error": "not_found"}, status=404)
    profile = get_public_profile(telegram_id)
    if profile is None:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response(profile)


@routes.get("/u/{telegram_id}")
async def public_profile_page(request):
    response = web.FileResponse(BASE_DIR / "static" / "public_profile.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@routes.post("/api/friends/{telegram_id}/react")
async def send_reaction_route(request):
    """Roadmap #19 — реакция/стикер поддержки другому пользователю
    (обычно с рейтинга). Раз в день на пару отправитель→получатель."""
    telegram_id, _ = await _authenticate(request)
    try:
        target_id = int(request.match_info["telegram_id"])
    except ValueError:
        return web.json_response({"error": "invalid_target"}, status=400)
    if get_user(target_id) is None:
        return web.json_response({"error": "not_found"}, status=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)
    emoji = body.get("emoji")
    if not send_reaction(telegram_id, target_id, emoji):
        reason = "already_reacted_today" if has_reacted_today(telegram_id, target_id) else "invalid_reaction"
        return web.json_response({"error": reason}, status=400)

    bot = request.app.get("bot")
    if bot is not None:
        sender = get_user(telegram_id)
        sender_name = (sender["first_name"] if sender else None) or "Кто-то"
        try:
            await _push(request.app, target_id, f"{emoji} {sender_name} поддержал(а) тебя!")
        except Exception:
            logger.exception("Не удалось отправить уведомление о реакции")
    return web.json_response({"ok": True})


@routes.get("/api/reactions")
async def reactions_route(request):
    """Roadmap #19 — лента полученных реакций для профиля."""
    telegram_id, _ = await _authenticate(request)
    reactions = get_recent_reactions_received(telegram_id, limit=20)
    return web.json_response({
        "reactions": [
            {
                "emoji": r["emoji"],
                "day": r["day"],
                "from_name": r["first_name"] or r["username"] or "Игрок",
            }
            for r in reactions
        ],
        "available_emojis": REACTION_EMOJIS,
    })


@routes.post("/api/settings/archetype")
async def set_archetype_route(request):
    """Roadmap #39 — результат короткого теста на архетип личности,
    сам подсчёт делает фронт (4 детерминированных вопроса), сюда
    приходит уже готовый ключ."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    key = body.get("archetype")
    if not set_archetype(telegram_id, key):
        return web.json_response({"error": "invalid_archetype"}, status=400)
    return web.json_response({"ok": True, "archetype": ARCHETYPES[key]})


@routes.post("/api/settings/goals")
async def set_goals_route(request):
    """Roadmap #25 — долгосрочные цели пользователя, которые AI-наставник
    держит в контексте (см. webapp/services/ai_utils.py)."""
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    saved = set_long_term_goals(telegram_id, body.get("text"))
    return web.json_response({"ok": True, "text": saved})


@routes.post("/api/settings/reset-progress")
async def reset_progress_route(request):
    telegram_id, _ = await _authenticate(request)
    reset_progress(telegram_id)
    return web.json_response({"ok": True})


# ====================== САМООБСЛУЖИВАНИЕ АККАУНТА ======================
# Раньше единственный способ выгрузить свои данные целиком или удалить
# аккаунт был написать на email из privacy.html и ждать до 30 дней, пока
# это вручную сделает админ (см. db/account.py).

@routes.get("/api/account/export")
async def account_export_route(request):
    telegram_id, _ = await _authenticate(request)
    data = export_full_account_data(telegram_id)
    response = web.json_response(data, dumps=lambda d: json.dumps(d, ensure_ascii=False, indent=2))
    response.headers["Content-Disposition"] = f'attachment; filename="adam_data_{telegram_id}.json"'
    response.headers["Cache-Control"] = "no-store"
    return response


@routes.post("/api/account/delete")
async def account_delete_route(request):
    """Необратимо (см. db/account.py::request_account_deletion) — фронт
    обязан показать явное подтверждение ПЕРЕД этим запросом, здесь его
    больше негде перепроверить."""
    telegram_id, _ = await _authenticate(request)
    request_account_deletion(telegram_id)
    return web.json_response({"ok": True})

# ====================== ЕЖЕДНЕВНЫЕ ЗАДАНИЯ / БОНУС ======================

def _serialize_daily_tasks(tasks):
    return [
        {
            "id": t["id"],
            "task": t["task"],
            "progress": t["progress"],
            "goal": t["goal"],
            "reward": t["reward"],
            "completed": bool(t["completed"]),
        }
        for t in tasks
    ]

@routes.get("/api/daily-tasks")
async def daily_tasks_route(request):
    telegram_id, _ = await _authenticate(request)
    tasks = get_daily_tasks(telegram_id)
    if not tasks:
        create_daily_tasks(telegram_id)
        tasks = get_daily_tasks(telegram_id)
    user = get_user(telegram_id)
    bonus_available = (user["bonus_date"] if user and "bonus_date" in user.keys() else None) != str(date.today())
    return web.json_response({
        "tasks": _serialize_daily_tasks(tasks),
        "bonus_available": bonus_available,
    })

@routes.post("/api/daily-bonus/claim")
async def daily_bonus_claim_route(request):
    telegram_id, _ = await _authenticate(request)
    claimed = claim_daily_bonus(telegram_id)
    user = get_user(telegram_id)
    return web.json_response({
        "ok": claimed,
        "xp": user["xp"] if user else 0,
    })

# ====================== ПРОГРЕСС ======================

@routes.get("/api/progress/stats")
async def progress_stats_route(request):
    telegram_id, _ = await _authenticate(request)
    weekly = get_weekly_summary(telegram_id)
    stats = get_statistics(telegram_id)
    total_completed = sum(row["completed"] for row in stats) if stats else 0
    total_xp = sum(row["gained_xp"] for row in stats) if stats else 0
    return web.json_response({
        "weekly": weekly,
        "last30": {
            "completed": total_completed,
            "xp": total_xp,
            "entries": len(stats) if stats else 0,
        },
        # Roadmap #29/#30: "я сейчас vs я месяц назад" + прогноз следующего
        # рубежа серии по текущему темпу.
        "comparison": get_progress_comparison(telegram_id),
        "forecast": get_streak_forecast(telegram_id),
        # Roadmap #27 — статистические корреляции между привычками.
        "correlations": get_habit_correlations(telegram_id),
    })

@routes.get("/api/export/habits.csv")
async def export_habits_csv_route(request):
    """Экспорт полной истории по привычкам в CSV — платящие пользователи
    видят, что их прогресс не заперт внутри бота, и могут унести его
    с собой (в Excel/Google Sheets)."""
    telegram_id, _ = await _authenticate(request)
    import csv
    import io
    from db.core import connect

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT day, habit_title, completed FROM habit_logs WHERE user_id=? ORDER BY day DESC, habit_title",
        (telegram_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    buf = io.StringIO()
    # ﻿ (BOM) — чтобы Excel сам определил UTF-8 и не превратил
    # кириллицу в кракозябры при открытии двойным кликом.
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(["Дата", "Привычка", "Выполнено"])
    for row in rows:
        writer.writerow([row["day"], row["habit_title"], "Да" if row["completed"] else "Нет"])

    response = web.Response(text=buf.getvalue(), content_type="text/csv", charset="utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="adam_habits_{telegram_id}.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@routes.post("/api/feedback")
async def feedback_route(request):
    """Форма бага/фидбека прямо из Mini App — раньше единственный канал
    был написать админу лично, что резко снижало вероятность честного
    отчёта о проблеме. Уходит всем админам с автоприложенным контекстом
    (кто, откуда, с какой вкладки)."""
    telegram_id, _ = await _authenticate(request)
    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid_json"}, status=400)

    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    if len(text) > 2000:
        return web.json_response({"error": "text_too_long"}, status=400)
    tab = (body.get("tab") or "неизвестно")[:40]

    user_row = get_user(telegram_id)
    username = user_row["username"] if user_row and "username" in user_row.keys() else None
    who = f"@{username}" if username else str(telegram_id)
    message = (
        f"💬 Фидбек от {who} (ID {telegram_id})\n"
        f"Вкладка: {tab}\n\n"
        f"{text}"
    )

    from config import ADMIN_IDS
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
            delivered += 1
        except Exception:
            logger.warning(f"Не удалось доставить фидбек админу {admin_id}")

    if not delivered:
        return web.json_response({"error": "delivery_failed"}, status=502)
    return web.json_response({"ok": True})


@routes.post("/api/client-error")
async def client_error_route(request):
    """Улучшение #70: window.onerror/unhandledrejection на фронте шлют сюда
    best-effort. Отправка ошибки НЕ должна сама уметь ронять что-то ещё —
    поэтому любая проблема здесь тихо превращается в 204, а не 500."""
    try:
        telegram_id, _ = await _authenticate(request)
    except web.HTTPException:
        return web.Response(status=204)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.Response(status=204)

    message = (body.get("message") or "").strip()
    if not message:
        return web.Response(status=204)

    try:
        log_client_error(
            telegram_id,
            message,
            stack=body.get("stack"),
            url=body.get("url"),
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception:
        logger.warning("Не удалось сохранить client_error для %s", telegram_id)
    return web.Response(status=204)


@routes.post("/api/progress/ai-analysis")
async def progress_ai_analysis_route(request):
    telegram_id, _ = await _authenticate(request)

    cache_key = f"panalysis:{telegram_id}:{date.today()}"
    text = cache_get(cache_key)

    if text is None:
        weekly = get_weekly_summary(telegram_id)
        weekly_text = (
            f"Выполнено привычек: {weekly['completed']}, "
            f"активных дней: {weekly['active_days']}/7, "
            f"получено Adam Coin: {weekly['xp']}."
        )
        user_context = build_user_context(telegram_id)
        style = get_ai_style(telegram_id)

        try:
            text = await generate_progress_analysis(user_context, weekly_text, style)
        except Exception as e:
            logger.exception(f"Не удалось сформировать AI-анализ прогресса для {telegram_id}")
            log_error("progress_analysis", e, telegram_id)
            return web.json_response({"error": "analysis_failed"}, status=502)

        if text and "[ошибка агента" not in text:
            cache_set(cache_key, text)

    return web.json_response({"text": text})

# ====================== МОЯ ЦЕЛЬ И ВЕХИ ======================

@routes.get("/api/milestones")
async def milestones_route(request):
    telegram_id, _ = await _authenticate(request)
    rows = get_milestones(telegram_id)
    return web.json_response({
        "goal_text": rows[0]["goal_text"] if rows else "",
        "milestones": [
            {"id": m["id"], "text": m["milestone_text"], "done": bool(m["done"])}
            for m in rows
        ],
    })

@routes.post("/api/milestones")
async def save_milestones_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    goal_text = (body.get("goal_text") or "").strip()
    milestones = body.get("milestones") or []
    if not goal_text:
        return web.json_response({"error": "empty_goal"}, status=400)
    if not isinstance(milestones, list) or not milestones:
        return web.json_response({"error": "empty_milestones"}, status=400)
    save_milestones(telegram_id, goal_text, [str(m).strip() for m in milestones if str(m).strip()])
    return web.json_response({"ok": True})

@routes.post("/api/milestones/{milestone_id}/toggle")
async def toggle_milestone_route(request):
    telegram_id, _ = await _authenticate(request)
    try:
        milestone_id = int(request.match_info["milestone_id"])
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_id"}, status=400)
    toggle_milestone(milestone_id, telegram_id)
    return web.json_response({"ok": True})

@routes.post("/api/plan/save")
async def save_plan_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    main_goal = (body.get("main_goal") or "").strip()
    tasks = body.get("tasks") or []
    if not isinstance(tasks, list):
        return web.json_response({"error": "invalid_tasks"}, status=400)
    save_daily_plan(telegram_id, main_goal, [str(t) for t in tasks])
    return web.json_response({"ok": True})

@routes.post("/api/plan/task/toggle")
async def toggle_plan_task_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    try:
        task_id = int(body.get("task_id"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_task_id"}, status=400)

    plan = get_daily_plan(telegram_id)
    task = next((t for t in plan["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise web.HTTPNotFound()
    was_completed = bool(task["completed"])

    toggle_daily_task(task_id)

    # Промт п.3: поздравление сразу после того, как отмечена ПОСЛЕДНЯЯ
    # незакрытая задача плана дня (а не при каждой отдельной задаче).
    updated_plan = get_daily_plan(telegram_id)
    tasks = updated_plan["tasks"]
    message = None
    if tasks and all(t["completed"] for t in tasks):
        message = format_all_tasks_done_message()
    elif not was_completed:
        # Промт п.7.1: короткая похвала за КАЖДУЮ отдельную второстепенную
        # задачу (кроме случая выше, когда это была последняя — там уже
        # общее поздравление). Не повторяется в течение дня; в первые
        # 3 дня использования/15 показов — не повторяется вовсе.
        user = get_user(telegram_id)
        name = (user["first_name"] if user else "") or ""
        praise_state = get_secondary_task_praise_state(telegram_id)
        account_age_days = 0
        created_at = user["created_at"] if user and "created_at" in user.keys() else None
        if created_at:
            try:
                account_age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(str(created_at))).days
            except ValueError:
                account_age_days = 0
        strict_mode = (
            account_age_days < SECONDARY_TASK_PRAISE_STRICT_DAYS
            and praise_state["total"] < SECONDARY_TASK_PRAISE_STRICT_COUNT
        )
        key, message = format_secondary_task_praise(
            name, praise_state["used_today"], praise_state["used_ever"], strict_mode
        )
        record_secondary_task_praise(telegram_id, key)

    return web.json_response({"ok": True, "message": message})

@routes.post("/api/plan/main/save")
async def save_main_goal_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    set_daily_main_goal(telegram_id, text)
    return web.json_response({"ok": True})

@routes.post("/api/plan/main/toggle")
async def toggle_main_goal_route(request):
    telegram_id, _ = await _authenticate(request)
    plan = get_daily_plan(telegram_id)
    if not plan["main_goal"]:
        raise web.HTTPNotFound()

    was_completed = plan["main_goal_completed"]
    toggle_daily_main_goal(telegram_id)

    # Промт п.7: поощрение показываем только когда цель ПЕРЕХОДИТ в
    # выполненное состояние (не при повторном снятии галочки). Отдаём
    # текст в ответе API — фронт мини-аппа показывает его тостом, в чат
    # с ботом больше не шлём.
    message = format_main_goal_done_message() if not was_completed else None

    return web.json_response({"ok": True, "message": message})

@routes.delete("/api/plan/main")
async def delete_main_goal_route(request):
    telegram_id, _ = await _authenticate(request)
    delete_daily_main_goal(telegram_id)
    return web.json_response({"ok": True})

@routes.post("/api/plan/task")
async def add_plan_task_route(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    try:
        task_id = add_daily_task(telegram_id, text)
    except ValueError as exc:
        if str(exc) == "task_limit":
            return web.json_response({"error": "task_limit"}, status=400)
        raise
    return web.json_response({"ok": True, "task_id": task_id})

@routes.put("/api/plan/task/{task_id}")
async def edit_plan_task_route(request):
    telegram_id, _ = await _authenticate(request)
    try:
        task_id = int(request.match_info["task_id"])
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_task_id"}, status=400)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty_text"}, status=400)
    if not update_daily_plan_task(telegram_id, task_id, text):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})

@routes.delete("/api/plan/task/{task_id}")
async def delete_plan_task_route(request):
    telegram_id, _ = await _authenticate(request)
    try:
        task_id = int(request.match_info["task_id"])
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid_task_id"}, status=400)
    if not delete_daily_task(telegram_id, task_id):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})

@routes.get("/")
async def index(request):
    # index.html (~44 КБ) отдавался как есть, без сжатия — та же логика,
    # что и для style.css/app.js: gzip в памяти, кэш не трогаем.
    response = _serve_gzip_asset(request, BASE_DIR / "static" / "index.html", "text/html")
    # Telegram WebView агрессивно кэширует Mini App. Главная страница должна
    # всегда получать актуальные ссылки на JS/CSS, иначе старый интерфейс
    # возвращается даже после обычного обновления.
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@routes.get("/api/shop")
async def get_shop(request):
    telegram_id, _ = await _authenticate(request)
    owned_item_ids = set(get_user_items(telegram_id))
    profile_user = get_user(telegram_id)
    user_frame_id = profile_user["frame_id"] if profile_user else "default"
    items = [
        {
            "id": it["id"],
            "name": it["name"],
            "description": it["description"],
            "price": it["price"],
            "owned": it["id"] in owned_item_ids or (it["item_type"] == "frame_stars" and user_frame_id == "paid_double_gold"),
            "item_type": it["item_type"] if "item_type" in it.keys() else None,
            "payload": it["payload"] if "payload" in it.keys() else None,
        }
        for it in get_shop_items()
    ]
    return web.json_response({"items": items})

@routes.post("/api/buy/{item_id}")
async def buy_route(request):
    telegram_id, _ = await _authenticate(request)
    item_id = int(request.match_info["item_id"])
    item = get_shop_item(item_id)
    if not item:
        return web.json_response({"error": "shop_item_not_found"}, status=404)

    item_type = item["item_type"] if "item_type" in item.keys() else "cosmetic"
    if item_type in ("frame_stars", "answer_pack_stars"):
        return web.json_response({"error": "use_stars_checkout"}, status=400)

    if item_id == 1 and was_premium_purchased(telegram_id):
        return web.json_response({"error": "premium_already_purchased"}, status=400)

    # Пром 9: пакеты доп. ответов ADAM за Adam Coin — не больше 1 раза в
    # день каждый (daily_limit_per_user), иначе лимит AI-запросов можно
    # было бы докупать бесконечно.
    if has_reached_daily_limit(telegram_id, item_id, item):
        return web.json_response({"error": "daily_limit_reached"}, status=400)

    owned_item_ids = set(get_user_items(telegram_id))
    repeatable = bool(item["repeatable"]) if "repeatable" in item.keys() else False
    if not repeatable and item_id != 1 and item_id in owned_item_ids:
        # Для уже купленной рамки/аватара разрешаем просто надеть её.
        if item_type in ("avatar", "frame"):
            set_cosmetic(telegram_id, item_type, item["payload"] or "default")
            user = get_user(telegram_id)
            return web.json_response({"ok": True, "equipped": True, "xp": user["xp"] if user else 0, "avatar_id": user["avatar_id"] if user else "default", "frame_id": user["frame_id"] if user else "default"})
        return web.json_response({"error": "already_owned"}, status=400)

    success = buy_shop_item(telegram_id, item_id, allow_repeatable=repeatable)
    if not success:
        return web.json_response({"error": "not_enough_xp_or_not_found"}, status=400)

    if item_id == 1:
        give_premium(telegram_id)
    elif item_type in ("avatar", "frame"):
        set_cosmetic(telegram_id, item_type, item["payload"] or "default")
    elif item_type == "answer_pack":
        from db import add_ai_bonus_answers
        try:
            add_ai_bonus_answers(telegram_id, int(item["payload"] or 0))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_answer_pack"}, status=400)

    user = get_user(telegram_id)
    return web.json_response({
        "ok": True,
        "xp": user["xp"] if user else 0,
        "avatar_id": user["avatar_id"] if user else "default",
        "frame_id": user["frame_id"] if user else "default",
    })

@routes.post("/api/cosmetics/equip")
async def equip_cosmetic(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    frame_id = str(body.get("frame_id", "default"))
    avatar_id = body.get("avatar_id")

    if frame_id != "default":
        allowed_shop = {5: "neon", 6: "gold", 7: "paid_double_gold"}
        allowed = frame_id in allowed_shop.values() and (
            has_item(telegram_id, next(k for k,v in allowed_shop.items() if v == frame_id))
            if frame_id in ("neon", "gold") else frame_id == "paid_double_gold" and get_user(telegram_id)["frame_id"] == "paid_double_gold"
        )
        if frame_id in ("streak_14", "streak_30"):
            allowed = has_streak_frame(telegram_id, frame_id)
        if not allowed:
            return web.json_response({"error": "frame_not_owned"}, status=403)
        set_cosmetic(telegram_id, "frame", frame_id)

    if avatar_id is not None:
        if avatar_id not in ("default", "adam"):
            return web.json_response({"error": "invalid_avatar"}, status=400)
        if avatar_id == "adam" and not has_item(telegram_id, 4):
            return web.json_response({"error": "avatar_not_owned"}, status=403)
        set_cosmetic(telegram_id, "avatar", avatar_id)

    user = get_user(telegram_id)
    return web.json_response({"ok": True, "avatar_id": user["avatar_id"], "frame_id": user["frame_id"]})

AVATAR_MAX_DIMENSION = 512  # сторона в пикселях — аватарка в интерфейсе нигде не показывается крупнее


@routes.post("/api/profile/avatar")
async def upload_avatar(request):
    telegram_id, _ = await _authenticate(request)
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "avatar":
        return web.json_response({"error": "avatar_required"}, status=400)
    content_type = (field.headers.get("Content-Type") or "").lower()
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        return web.json_response({"error": "unsupported_image"}, status=400)

    raw = bytearray()
    while True:
        chunk = await field.read_chunk(64 * 1024)
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > 5 * 1024 * 1024:
            return web.json_response({"error": "avatar_too_large"}, status=413)

    # Улучшение #74: раньше сырые байты (какими бы они ни были — PNG, WEBP,
    # да хоть не картинка вовсе, лишь бы Content-Type заголовок совпал)
    # записывались напрямую в файл с расширением .jpg. Из-за этого
    # /media/avatars/*.jpg мог реально содержать PNG/WEBP — большинство
    # клиентов такое прощают через сниффинг байтов, но это и лишний вес
    # (без сжатия), и небезопасно (Content-Type с клиента не проверяет,
    # что внутри действительно валидное изображение). Декодируем через
    # Pillow, ужимаем до разумного размера и всегда сохраняем настоящий JPEG.
    try:
        img = Image.open(BytesIO(bytes(raw)))
        img.verify()
        img = Image.open(BytesIO(bytes(raw)))  # verify() портит объект — открываем заново
        img = img.convert("RGB") if img.mode != "RGB" else img
    except (UnidentifiedImageError, OSError, ValueError):
        return web.json_response({"error": "unsupported_image"}, status=400)

    # Фидбек: не-квадратные фото (вертикальные/горизонтальные) сохранялись
    # с оригинальными пропорциями через thumbnail() — сам файл вписывался
    # в 512×512, но не обрезался, а везде в интерфейсе аватарка показывается
    # в квадратной рамке, поэтому такое фото визуально "растягивалось"/
    # обрезалось браузером криво. Центр-кроп до квадрата ДО ресайза —
    # результат всегда ровно AVATAR_MAX_DIMENSION×AVATAR_MAX_DIMENSION.
    side = min(img.width, img.height)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side > AVATAR_MAX_DIMENSION:
        img = img.resize((AVATAR_MAX_DIMENSION, AVATAR_MAX_DIMENSION), Image.LANCZOS)

    avatars_dir = Path(DATA_DIR) / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    target = avatars_dir / f"{telegram_id}.jpg"
    tmp = avatars_dir / f".{telegram_id}.upload"
    img.save(tmp, format="JPEG", quality=82, optimize=True)
    tmp.replace(target)

    set_cosmetic(telegram_id, "avatar", f"upload:{telegram_id}")
    user = get_user(telegram_id)
    return web.json_response({"ok": True, "avatar_url": f"/media/avatars/{telegram_id}.jpg?v={int(target.stat().st_mtime)}", "avatar_id": user["avatar_id"]})

@routes.get("/media/avatars/{filename}")
async def serve_avatar(request):
    filename = request.match_info["filename"]
    if not filename.endswith(".jpg") or not filename[:-4].isdigit():
        raise web.HTTPNotFound()
    path = Path(DATA_DIR) / "avatars" / filename
    if not path.exists():
        raise web.HTTPNotFound()
    return web.FileResponse(path)

@routes.post("/api/shop/stars/{item_id}")
async def create_stars_invoice(request):
    telegram_id, _ = await _authenticate(request)
    item_id = int(request.match_info["item_id"])
    item = get_shop_item(item_id)
    if not item or item["item_type"] not in ("frame_stars", "answer_pack_stars", "booster_stars"):
        return web.json_response({"error": "stars_item_not_found"}, status=404)

    if item["item_type"] == "frame_stars":
        user = get_user(telegram_id)
        if user and user["frame_id"] == "paid_double_gold":
            return web.json_response({"error": "already_owned"}, status=400)
        title = "ADAM — Double Gold"
        description = "Премиальная рамка с двойной позолотой и подсветкой для аватарки."
        payload = f"avatar_frame:{item_id}:{telegram_id}"
    elif item["item_type"] == "booster_stars":
        # Roadmap #32 — повторная покупка продлевает окно (см.
        # activate_xp_booster), поэтому дневной лимит здесь не нужен.
        title = item["name"]
        description = item["description"]
        payload = f"booster:{item_id}:{telegram_id}"
    else:
        # Пром 9: пакеты +50/+100 ответов ADAM за Stars — не больше 1 раза
        # в день каждый, как и монетные пакеты (см. has_reached_daily_limit).
        if has_reached_daily_limit(telegram_id, item_id, item):
            return web.json_response({"error": "daily_limit_reached"}, status=400)
        title = item["name"]
        description = item["description"]
        payload = f"answer_pack_stars:{item_id}:{telegram_id}"

    bot = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "payment_unavailable"}, status=503)
    from aiogram.types import LabeledPrice
    link = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=int(item["price"]))],
    )
    return web.json_response({"ok": True, "invoice_url": link})

@routes.get("/api/progress/pdf-report")
async def pdf_report_route(request):
    """Roadmap #28 — экспортируемый PDF-отчёт о прогрессе."""
    telegram_id, _ = await _authenticate(request)
    from webapp.services.pdf_report import generate_progress_pdf
    pdf_bytes = generate_progress_pdf(telegram_id)
    if pdf_bytes is None:
        return web.json_response({"error": "not_found"}, status=404)
    filename = f"adam_report_{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return web.Response(
        body=pdf_bytes,
        content_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@routes.get("/health")
async def health(request):
    """Раньше просто подтверждал, что процесс жив — не ловил случай,
    когда сам процесс отвечает, а БД недоступна (диск/volume отвалился
    и т.п.). Теперь реально проверяет соединение с БД и возвращает 503
    (а не 200) при проблеме — важно для внешнего аптайм-монитора: он
    должен видеть именно нездоровый статус-код, не просто текст в теле.
    Ничего не пишет — SELECT 1 никак не трогает данные."""
    from db.core import connect
    try:
        conn = connect()
        conn.execute("SELECT 1")
        conn.close()
        return web.json_response({"status": "ok", "db": "ok"})
    except Exception as e:
        logger.exception("Health check: БД недоступна")
        return web.json_response({"status": "degraded", "db": "error", "detail": str(e)}, status=503)

# ====================== СОЗДАНИЕ ПРИЛОЖЕНИЯ ======================

def create_app(bot=None):
    app = web.Application(middlewares=[error_middleware])
    app["bot"] = bot
    app.add_routes(routes)
    
    # Добавляем маршруты для AI мини-приложения
    from webapp.routes_ai_miniapp import routes as ai_routes
    app.add_routes(ai_routes)

    # Маршруты админ-панели — каждый сам проверяет is_admin (см. файл)
    from webapp.routes_admin import routes as admin_routes
    app.add_routes(admin_routes)

    app.router.add_static("/static", BASE_DIR / "static")
    return app

# ====================== ЗАПУСК СЕРВЕРА ======================

async def run_webapp(port, bot=None):
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"🌐 MiniApp сервер запущен на порту {port}")
    return runner
   

# ====================== ГЛАВНЫЕ CSS/JS СЖИМАЕМ ВРУЧНУЮ ======================
# style.css/app.js — самые тяжёлые файлы (~200 КБ и ~85 КБ), и они
# намеренно отдаются с no-cache (иначе Telegram-вебвью может держать
# старую версию после редеплоя — см. error_middleware выше). Значит эти
# файлы качаются заново при КАЖДОМ открытии Mini App. aiohttp.add_static
# сам их не сжимает (отдаёт через FileResponse/sendfile без gzip), поэтому
# для именно этих двух путей — отдельный маршрут со сжатием в памяти.
# Кэш инвалидируется по mtime файла, так что редеплой подхватывается
# автоматически, без риска "залипшей" версии.
_gzip_asset_cache = {}


def _serve_gzip_asset(request, file_path: Path, content_type: str):
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        raise web.HTTPNotFound()

    cached = _gzip_asset_cache.get(file_path)
    if not cached or cached[0] != mtime:
        plain = file_path.read_bytes()
        compressed = gzip.compress(plain, compresslevel=6)
        cached = (mtime, plain, compressed)
        _gzip_asset_cache[file_path] = cached
    _, plain, compressed = cached

    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    body = compressed if accepts_gzip else plain
    response = web.Response(body=body, content_type=content_type, charset="utf-8")
    if accepts_gzip:
        response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@routes.get("/static/style.css")
async def static_style_css(request):
    return _serve_gzip_asset(request, BASE_DIR / "static" / "style.css", "text/css")


@routes.get("/static/app.js")
async def static_app_js(request):
    return _serve_gzip_asset(request, BASE_DIR / "static" / "app.js", "text/javascript")


# ai_coach.js (~44 КБ) раньше уходил через общий app.router.add_static —
# тот отдаёт файл как есть, без сжатия. Тот же приём, что и выше для
# style.css/app.js: даём gzip, no-cache оставляем — этот путь всё равно
# перезатирается middleware для /static/* (см. error_middleware).
@routes.get("/static/ai_coach.js")
async def static_ai_coach_js(request):
    return _serve_gzip_asset(request, BASE_DIR / "static" / "ai_coach.js", "text/javascript")


@routes.get("/coach")
async def coach(request):
    return _serve_gzip_asset(request, BASE_DIR / "static" / "ai_miniapp_styled.html", "text/html")


@routes.get("/admin")
async def admin_panel_page(request):
    """Страница отдаётся всем — реальная защита на уровне API
    (webapp/routes_admin.py::_authenticate_admin на каждом запросе),
    страница сама скрывает контент, пока не подтвердит is_admin."""
    response = web.FileResponse(BASE_DIR / "static" / "admin_panel.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


# Публичные юридические документы — без авторизации (Telegram требует
# ссылку на privacy policy для ботов с платежами, см. настройки BotFather).
# Обычное кеширование ок — документы меняются редко, каждое обновление
# нужно сопровождать актуальной датой в самом файле.
@routes.get("/privacy")
async def privacy_policy_page(request):
    return web.FileResponse(BASE_DIR / "static" / "privacy.html")


@routes.get("/terms")
async def terms_page(request):
    return web.FileResponse(BASE_DIR / "static" / "terms.html")