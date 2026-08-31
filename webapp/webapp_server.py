import json
import logging
import os
from pathlib import Path

from aiohttp import web
from aiohttp.web import Application

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
    update_reminder_time, toggle_reminders, update_ai_style, get_ai_style,
    get_shop_items, buy_shop_item, get_user_items, get_shop_item,
    has_item, get_item_owner_ids, update_theme, get_theme, set_cosmetic,
    has_reached_daily_limit, log_stars_purchase,
    get_rating, get_calendar, get_achievements,
    was_premium_purchased, give_premium,
    get_daily_plan, save_daily_plan, set_daily_main_goal, delete_daily_main_goal, toggle_daily_main_goal, add_daily_task, update_daily_plan_task, delete_daily_task, toggle_daily_task,
    get_streak_status, set_timezone, buy_freeze, claim_weekly_reward, get_weekly_bonus_available, has_streak_frame,
    should_show_onboarding, onboarding_message, mark_onboarding_seen, consume_completion_event,
    create_daily_tasks, get_daily_tasks, claim_daily_bonus,
    get_weekly_summary, get_statistics,
    get_milestones, save_milestones, toggle_milestone,
    reset_progress,
    cache_get, cache_set, log_error,
    get_bonus_window,
    get_secondary_task_praise_state, record_secondary_task_praise,
    get_monthly_progress, consume_month_end_reward_event,
    get_subscription_status, try_grant_channel_access, bot_access_allowed,
    should_show_app_tour, mark_app_tour_seen,
)

from datetime import date, datetime
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

async def _authenticate(request):
    init_data = _extract_init_data(request)
    tg_user = validate_init_data(init_data, BOT_TOKEN)
    if tg_user is None:
        raise web.HTTPUnauthorized(reason="invalid_init_data")

    telegram_id = tg_user["id"]
    is_admin = telegram_id in ADMIN_IDS

    if get_user(telegram_id) is None:
        add_user(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name", "")
        )

    if is_banned(telegram_id):
        raise web.HTTPForbidden(text='{"error":"banned"}')

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
                })
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
            })
        )

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
    bonus_active = bool(bonus_until_dt and bonus_until_dt > datetime.utcnow() and has_incomplete_habits)

    return web.json_response({
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
        },
        "monthly_progress": get_monthly_progress(telegram_id),
        "habits": [
            {
                "id": h["id"],
                "title": h["title"],
                "completed": bool(h["completed"]),
                "planned_time": h["planned_time"] if "planned_time" in h.keys() else None,
                "time_window_minutes": h["time_window_minutes"] if "time_window_minutes" in h.keys() else 60,
            }
            for h in habits
        ],
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


@routes.post("/api/habits")
async def create_habit(request):
    telegram_id, _ = await _authenticate(request)
    body = await request.json()
    title = body.get("title", "").strip()
    if len(title) < 2:
        return web.json_response({"error": "title_too_short"}, status=400)
    had_habits = bool(get_habits(telegram_id))
    try:
        add_habit(telegram_id, title)
    except ValueError as exc:
        # habit_limit — уже максимум 7 привычек; habit_add_locked — сегодня
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

@routes.post("/api/settings/reminders/toggle")
async def toggle_reminders_route(request):
    telegram_id, _ = await _authenticate(request)
    enabled = toggle_reminders(telegram_id)
    return web.json_response({"ok": True, "reminders": enabled})

@routes.post("/api/settings/reset-progress")
async def reset_progress_route(request):
    telegram_id, _ = await _authenticate(request)
    reset_progress(telegram_id)
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
    })

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
                account_age_days = (datetime.utcnow() - datetime.fromisoformat(str(created_at))).days
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
    response = web.FileResponse(BASE_DIR / "static" / "index.html")
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

    avatars_dir = Path(DATA_DIR) / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    target = avatars_dir / f"{telegram_id}.jpg"
    tmp = avatars_dir / f".{telegram_id}.upload"
    total = 0
    with tmp.open("wb") as f:
        while True:
            chunk = await field.read_chunk(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 5 * 1024 * 1024:
                tmp.unlink(missing_ok=True)
                return web.json_response({"error": "avatar_too_large"}, status=413)
            f.write(chunk)
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
    if not item or item["item_type"] not in ("frame_stars", "answer_pack_stars"):
        return web.json_response({"error": "stars_item_not_found"}, status=404)

    if item["item_type"] == "frame_stars":
        user = get_user(telegram_id)
        if user and user["frame_id"] == "paid_double_gold":
            return web.json_response({"error": "already_owned"}, status=400)
        title = "ADAM — Double Gold"
        description = "Премиальная рамка с двойной позолотой и подсветкой для аватарки."
        payload = f"avatar_frame:{item_id}:{telegram_id}"
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

@routes.get("/health")
async def health(request):
    return web.json_response({"status": "ok"})

# ====================== СОЗДАНИЕ ПРИЛОЖЕНИЯ ======================

def create_app(bot=None):
    app = web.Application(middlewares=[error_middleware])
    app["bot"] = bot
    app.add_routes(routes)
    
    # Добавляем маршруты для AI мини-приложения
    from webapp.routes_ai_miniapp import routes as ai_routes
    app.add_routes(ai_routes)
    
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
   

@routes.get("/coach")
async def coach(request):
    response = web.FileResponse(BASE_DIR / "static" / "ai_miniapp_styled.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response