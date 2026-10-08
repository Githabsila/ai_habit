"""
Адам пишет первым — планировщик и сборка текста.

Данные, расписание, выбор сценария и запасные тексты — в db/adam_checkin.py; здесь — всё, что зовёт модель и Telegram:
  • run_adam_checkins(bot) — тик планировщика (раз в несколько минут): у кого сейчас наступил запланированный момент в окне
    14:00–16:30 или 19:30–21:30, тому один раз за окно решаем «писать или нет» и, если нужно, отправляем сообщение;
  • compose_message — текст от модели; не прошёл проверку или модель недоступна — запасной текст (тоже без «Как дела?»);
  • compose_app_greeting — первое сообщение Адама, когда человек сам заходит в чат (db/ai_nudge.py → /api/ai/greet).

Сообщение пишется в переписку (ai_messages) как реплика Адама: ответ человека в чате продолжит именно этот разговор.
Раскатка — флаг adam_checkin (админы — всегда). Уведомление учитывает общий тумблер «Напоминания», тихие часы, блокировку бота.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import WEBAPP_URL
from db import (
    add_ai_message, claim_notification, get_all_users, get_settings, get_timezone, in_quiet_hours, log_error,
    mark_bot_blocked, notification_scope, reminder_category_enabled, release_notification,
)
from db import adam_checkin as ac

logger = logging.getLogger("adam_proactive")

PUSH_LLM_TIMEOUT = 14.0       # пуш — человек не ждёт у экрана
APP_LLM_TIMEOUT = 7.0         # чат уже открыт: дольше ждать нельзя, берём запасной текст
OTHER_PUSH_QUIET_MINUTES = 60  # недавно приходил другой пуш (сверка привычек, прогресс дня) — Адам подождёт до следующего окна


async def _generate(prompt, style):
    from multi_agent import generate_adam_checkin
    return await generate_adam_checkin(prompt, style)


async def compose_message(state, scenario, user_id, day_iso, timeout=PUSH_LLM_TIMEOUT):
    """(текст, источник): 'llm' | 'fallback' | 'none' (английский интерфейс без модели — русский запасной текст не шлём)."""
    opener = ac.opener_style(user_id, day_iso, scenario)
    prompt = ac.build_checkin_prompt(state, scenario, opener, ac.recent_adam_messages(user_id))
    text = ""
    try:
        text = ac.clean_text(await asyncio.wait_for(_generate(prompt, state.get("style") or "neutral"), timeout))
    except Exception as exc:
        logger.warning("Адам пишет первым: модель недоступна (%s)", exc)
    if text and ac.is_acceptable(text, scenario):
        return text, "llm"
    if state.get("language") == "en":
        return "", "none"
    return ac.fallback_text(state, scenario, user_id, day_iso), "fallback"


async def compose_app_greeting(user_id, timeout=APP_LLM_TIMEOUT):
    """Первое сообщение Адама при заходе в чат: (текст, сценарий, источник) или None."""
    state = ac.build_checkin_state(user_id, with_pace=False)
    scenario, _reason = ac.pick_scenario(state, "app")
    day_iso = datetime.now(ZoneInfo(get_timezone(user_id))).date().isoformat()
    text, source = await compose_message(state, scenario, user_id, day_iso, timeout=timeout)
    return (text, scenario, source) if text else None


def _keyboard():
    if not WEBAPP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Ответить Адаму", web_app=WebAppInfo(url=f"{WEBAPP_URL}/coach"))]
    ])


def other_push_recently(user_id, minutes=OTHER_PUSH_QUIET_MINUTES):
    from db.core import connect
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE user_id=? AND sent_at>=? AND category!='adam_checkin' LIMIT 1", (user_id, since)
        ).fetchone()
    finally:
        conn.close()
    return bool(row)


def skip_reason(telegram_id, now_local, settings):
    """Почему Адаму сейчас писать не стоит (None — можно). Каждая причина — одна строка в журнале окон."""
    day_iso = now_local.date().isoformat()
    if not reminder_category_enabled(settings, "habits"):
        return "напоминания выключены"
    if in_quiet_hours(settings, now_local):
        return "тихие часы"
    if not ac.account_old_enough(telegram_id):
        return "аккаунт слишком новый"
    if ac.is_dormant(telegram_id):
        return "давно не заходил — это работа напоминаний о возврате"
    if reminder_category_enabled(settings, "streak"):
        from db.streak import get_streak_reengagement_state, has_completed_today
        back = get_streak_reengagement_state(telegram_id)
        if back.get("has_history") and int(back.get("inactive_days") or 0) > 0 and not has_completed_today(telegram_id):
            return "человека возвращают в серию отдельные напоминания (пропущенные дни)"
    if ac.adam_wrote_today(telegram_id, day_iso):
        return "Адам уже писал сегодня"
    if ac.pushes_since(telegram_id, (now_local.date() - timedelta(days=6)).isoformat()) >= ac.MAX_PUSH_PER_WEEK:
        return "лимит пушей за неделю"
    return None


async def process_user(bot, telegram_id, scope, now_local=None):
    """Один человек за один тик. Возвращает строку-итог (для логов и тестов)."""
    now_local = now_local or datetime.now(ZoneInfo(get_timezone(telegram_id)))
    slot = ac.slot_due(telegram_id, now_local)
    if slot is None:
        return "not_due"
    if not ac.is_enabled_for(telegram_id):
        return "disabled"
    day_iso = now_local.date().isoformat()
    if not ac.claim_slot(telegram_id, day_iso, slot):
        return "already_handled"
    try:
        reason = skip_reason(telegram_id, now_local, get_settings(telegram_id))
        if reason is None and slot == "evening" and other_push_recently(telegram_id):
            reason = "недавно уже приходил другой пуш"
        scenario = None
        if reason is None:
            state = ac.build_checkin_state(telegram_id, now_local)
            scenario, reason = ac.pick_scenario(state, slot, ac.is_chat_day(telegram_id, now_local.date()))
        if scenario is None:
            ac.finish_slot(telegram_id, day_iso, slot, "skipped", note=reason)
            return f"skipped: {reason}"
        text, source = await compose_message(state, scenario, telegram_id, day_iso)
        if not text:
            ac.finish_slot(telegram_id, day_iso, slot, "skipped", scenario=scenario, note="нет текста")
            return "skipped: нет текста"
        if not claim_notification(telegram_id, day_iso, "adam_checkin", scope):
            ac.finish_slot(telegram_id, day_iso, slot, "skipped", scenario=scenario, note="уже было уведомление Адама")
            return "skipped: duplicate"
        try:
            await bot.send_message(telegram_id, text, reply_markup=_keyboard())
        except Exception:
            release_notification(telegram_id, day_iso, "adam_checkin", scope)
            raise
        message_id = add_ai_message(telegram_id, "assistant", text)
        ac.mark_adam_wrote_today(telegram_id, day_iso)
        ac.finish_slot(telegram_id, day_iso, slot, "sent", scenario=scenario, source=source, message_id=message_id, note=reason)
        return f"sent: {scenario}/{source}"
    except TelegramForbiddenError as exc:
        mark_bot_blocked(telegram_id)
        ac.finish_slot(telegram_id, day_iso, slot, "skipped", note="бот заблокирован")
        log_error("adam_checkin", exc, telegram_id)
        return "skipped: blocked"
    except Exception as exc:
        ac.release_slot(telegram_id, day_iso, slot)          # временный сбой: следующий тик повторит, пока окно не закончилось
        log_error("adam_checkin", exc, telegram_id)
        return "error"


async def run_adam_checkins(bot):
    """Тик планировщика: см. заголовок модуля."""
    if not bot:
        return
    scope = notification_scope(bot)
    sent = 0
    for user in get_all_users(include_blocked=False):
        try:
            outcome = await process_user(bot, user["telegram_id"], scope)
        except Exception as exc:
            log_error("adam_checkin", exc, user["telegram_id"])
            continue
        if outcome.startswith("sent"):
            sent += 1
    if sent:
        logger.info("Адам пишет первым: отправлено %s сообщений", sent)
