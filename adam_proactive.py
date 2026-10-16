"""
Адам пишет первым — планировщик и сборка текста.

Данные, расписание, выбор сценария и запасные тексты — в db/adam_checkin.py; здесь — всё, что зовёт модель и Telegram:
  • run_adam_checkins(bot) — тик планировщика (раз в несколько минут): у кого сейчас наступил запланированный момент в окне
    12:30–16:30 или 18:30–21:30 (рядом с привычным временем самого человека), тому один раз за окно решаем «писать или промолчать»
    (ТЗ 222.md: допуск → повод → приоритет → cooldown → текст) и, если нужно, отправляем сообщение;
  • compose_message — текст от модели; не прошёл проверку или модель недоступна — запасной текст (тоже без «Как дела?»);
  • compose_app_greeting — первое сообщение Адама, когда человек сам заходит в чат (db/ai_nudge.py → /api/ai/greet).

Сообщение пишется в переписку (ai_messages) как реплика Адама: ответ человека в чате продолжит именно этот разговор.
Раскатка — флаг adam_checkin (админы — всегда). Уведомление учитывает общий тумблер «Напоминания», тихие часы, блокировку бота.
Эксперимент «Адам пишет сам после действий» (process_followup): повтор через 35–60 минут после отметки — флаг adam_followups (админы — пока записи нет).
Под каждым сообщением Адама — кнопка от первого лица «Написать мне 📩» (keyboards.adam_chat_markup).
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramForbiddenError
from db import (
    add_ai_message, claim_notification, get_all_users, get_settings, get_timezone, in_quiet_hours, log_error,
    mark_bot_blocked, notification_scope, reminder_category_enabled, release_notification,
)
from db import adam_checkin as ac
from db import ai_usage

logger = logging.getLogger("adam_proactive")

PUSH_LLM_TIMEOUT = 14.0       # пуш — человек не ждёт у экрана
APP_LLM_TIMEOUT = 7.0         # чат уже открыт: дольше ждать нельзя, берём запасной текст
OTHER_PUSH_QUIET_MINUTES = 60  # недавно приходил другой пуш (сверка привычек, прогресс дня) — Адам подождёт до следующего окна


async def _generate(prompt, style):
    from multi_agent import generate_adam_checkin
    return await generate_adam_checkin(prompt, style)


RETRY_MIN_SECONDS = 3.0        # на вторую попытку модели должно остаться хотя бы столько


async def compose_message(state, scenario, user_id, day_iso, timeout=PUSH_LLM_TIMEOUT):
    """(текст, источник): 'llm' | 'fallback' | 'none' (английский интерфейс без модели — русский запасной текст не шлём).

    Адам не повторяется: текст сравнивается с его последними сообщениями (ac.too_similar); слишком похожий или не прошедший проверку
    переписывается один раз (если хватает времени), потом — запасной вариант, непохожий на недавние. Подход к тексту (ac.ANGLES) у
    каждого сообщения свой."""
    opener = ac.opener_style(user_id, day_iso, scenario)
    recent = ac.recent_adam_messages(user_id, ac.SIMILARITY_LOOKBACK)
    angle = ac.pick_angle(user_id, day_iso, scenario, recent[-1] if recent else "")
    prompt = ac.build_checkin_prompt(state, scenario, opener, recent[-3:], state.get("recent_scenarios") or (), angle=angle)
    started = time.monotonic()
    extra = ""
    if ai_usage.check_allowed(user_id)["ok"]:
        for attempt in range(2):
            left = timeout - (time.monotonic() - started)
            if attempt and left < RETRY_MIN_SECONDS:
                break
            try:
                with ai_usage.scope(user_id, "checkin"):
                    text = ac.clean_text(await asyncio.wait_for(_generate(prompt + extra, state.get("style") or "neutral"), max(left, 1.0)))
            except Exception as exc:
                logger.warning("Адам пишет первым: модель недоступна (%s)", exc)
                break
            if not text:
                break
            if ac.is_acceptable(text, scenario):
                if not ac.too_similar(text, recent):
                    return text, "llm"
                extra = ("\n\nВНИМАНИЕ: прошлый вариант слишком похож на твои недавние сообщения. Перепиши иначе: другая структура, "
                         "другие слова, другой первый оборот.")
            else:
                extra = ("\n\nВНИМАНИЕ: прошлый вариант не прошёл проверку (шаблонная фраза вроде «как дела», слишком длинно или больше одного вопроса). "
                         "Напиши заново: коротко, один вопрос, без шаблонов.")
    if state.get("language") == "en":
        return "", "none"
    return ac.fallback_text(state, scenario, user_id, day_iso, avoid=recent), "fallback"


async def compose_app_greeting(user_id, timeout=APP_LLM_TIMEOUT):
    """Первое сообщение Адама при заходе в чат: (текст, сценарий, источник) или None."""
    state = ac.build_checkin_state(user_id)
    scenario, _reason = ac.pick_scenario(state, "app")
    day_iso = datetime.now(ZoneInfo(get_timezone(user_id))).date().isoformat()
    text, source = await compose_message(state, scenario, user_id, day_iso, timeout=timeout)
    return (text, scenario, source) if text else None


def _keyboard():
    """«Написать мне 📩» — от первого лица: Адам пишет сам, и кнопка под его сообщением говорит его голосом (keyboards.adam_chat_markup)."""
    from keyboards import adam_chat_markup
    return adam_chat_markup()


def other_push_recently(user_id, minutes=OTHER_PUSH_QUIET_MINUTES):
    from db.core import connect
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE user_id=? AND sent_at>=? AND category NOT LIKE 'adam_checkin%' LIMIT 1", (user_id, since)
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
    if not ai_usage.check_allowed(telegram_id)["ok"]:
        return "ИИ для человека ограничен админом"
    if reminder_category_enabled(settings, "streak"):
        from db.streak import get_streak_reengagement_state, has_completed_today
        back = get_streak_reengagement_state(telegram_id)
        if back.get("has_history") and int(back.get("inactive_days") or 0) > 0 and not has_completed_today(telegram_id):
            return "человека возвращают в серию отдельные напоминания (пропущенные дни)"
    if ac.adam_wrote_today(telegram_id, day_iso):
        return "Адам уже писал сегодня"
    wrote = ac.adam_wrote_minutes_ago(telegram_id, day_iso)
    if wrote is not None and wrote < ac.ADAM_GAP_MIN:
        return "Адам писал совсем недавно (повтор после действия или первое сообщение в чате)"
    if ac.chatted_today(telegram_id, now_local):
        return "человек сам уже общался с Адамом сегодня"
    seen = ac.seen_minutes_ago(telegram_id)
    if seen is not None and seen <= ac.ACTIVE_NOW_MINUTES:
        return "человек прямо сейчас в приложении"
    if ac.pushes_since(telegram_id, (now_local.date() - timedelta(days=6)).isoformat()) >= ac.MAX_PUSH_PER_WEEK:
        return "лимит пушей за неделю"
    return None


async def process_user(bot, telegram_id, scope, now_local=None):
    """Один человек за один тик. Возвращает строку-итог (для логов и тестов)."""
    now_local = now_local or datetime.now(ZoneInfo(get_timezone(telegram_id)))
    if ac.window_slot(now_local) is None:
        return "not_due"
    if not ac.is_enabled_for(telegram_id):
        return "disabled"
    # Момент внутри окна — рядом с привычным временем самого человека (профиль считается раз в день), иначе случайный.
    slot = ac.slot_due(telegram_id, now_local, ac.preferred_minutes(telegram_id, now_local))
    if slot is None:
        return "not_due"
    # Только что отметил привычку (идёт окно x2, человек в движении) — подгонять нельзя. Окно не занимаем: пока оно открыто, следующий тик
    # проверит снова; а если человека ждать больше нечего, он и без того в процессе.
    since_mark = ac.last_mark_minutes(telegram_id)
    if since_mark is not None and since_mark < ac.JUDGE_AFTER_MARK_MIN:
        return "wait: недавно отметил привычку"
    day_iso = now_local.date().isoformat()
    if not ac.claim_slot(telegram_id, day_iso, slot):
        return "already_handled"
    try:
        reason = skip_reason(telegram_id, now_local, get_settings(telegram_id))
        if reason is None and other_push_recently(telegram_id):
            reason = "недавно уже приходил другой пуш"
        scenario = None
        if reason is None:
            state = ac.build_checkin_state(telegram_id, now_local)
            scenario, reason, _candidates = ac.choose_scenario(
                state, slot, ac.is_chat_day(telegram_id, now_local.date()), state["recent_scenarios"]
            )
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


def followup_skip_reason(telegram_id, now_local, settings):
    """Почему Адам сейчас не будет возвращаться к человеку после его действия (None — можно). Это временные причины: окно не занимаем,
    следующий тик проверит снова, пока повод не устарел."""
    if not reminder_category_enabled(settings, "habits"):
        return "напоминания выключены"
    if in_quiet_hours(settings, now_local):
        return "тихие часы"
    if not ac.account_old_enough(telegram_id):
        return "аккаунт слишком новый"
    if ac.is_dormant(telegram_id):
        return "давно не заходил"
    if not ai_usage.check_allowed(telegram_id)["ok"]:
        return "ИИ для человека ограничен админом"
    seen = ac.seen_minutes_ago(telegram_id)
    if seen is not None and seen <= ac.ACTIVE_NOW_MINUTES:
        return "человек прямо сейчас в приложении"
    chat = ac.chat_minutes_ago(telegram_id)
    if chat is not None and chat < ac.FOLLOWUP_CHAT_QUIET_MIN:
        return "человек только что говорил с Адамом"
    wrote = ac.adam_wrote_minutes_ago(telegram_id, now_local.date().isoformat())
    if wrote is not None and wrote < ac.ADAM_GAP_MIN:
        return "Адам писал совсем недавно"
    if other_push_recently(telegram_id):
        return "недавно приходил другой пуш"
    return None


async def process_followup(bot, telegram_id, scope, now_local=None):
    """Эксперимент «Адам пишет сам после действий»: через 35–60 минут после отметки (или 60–90 после разговора в чате) Адам возвращается с
    одной репликой по смыслу — осталось дело / всё закрыто / вернуться к разговору. Не чаще FOLLOWUPS_PER_DAY раз в день и не чаще раза в
    ADAM_GAP_MIN минут, только с 10:30 до 21:30. Включено админам; остальным — флаг adam_followups. Возвращает строку-итог (для тестов и логов)."""
    now_local = now_local or datetime.now(ZoneInfo(get_timezone(telegram_id)))
    minute = now_local.hour * 60 + now_local.minute
    if not (ac.FOLLOWUP_FROM_MIN <= minute < ac.FOLLOWUP_TO_MIN):
        return "not_due"
    if not ac.followups_enabled_for(telegram_id):
        return "disabled"
    if not ac.followup_possible(telegram_id):
        return "not_due"
    day_iso = now_local.date().isoformat()
    if ac.followups_sent_today(telegram_id, day_iso) >= ac.FOLLOWUPS_PER_DAY:
        return "cap"
    reason = followup_skip_reason(telegram_id, now_local, get_settings(telegram_id))
    if reason:
        return f"wait: {reason}"
    state = ac.build_checkin_state(telegram_id, now_local)
    for candidate in ac.followup_candidates(state, day_iso):
        scenario, slot = candidate["scenario"], candidate["slot"]
        if not ac.claim_slot(telegram_id, day_iso, slot):
            continue                                                  # про это сегодня уже писали — берём следующий повод
        try:
            text, source = await compose_message(state, scenario, telegram_id, day_iso)
            if not text:
                ac.finish_slot(telegram_id, day_iso, slot, "skipped", scenario=scenario, note="нет текста")
                return "skipped: нет текста"
            kind = f"adam_checkin_{slot}"
            if not claim_notification(telegram_id, day_iso, kind, scope):
                ac.finish_slot(telegram_id, day_iso, slot, "skipped", scenario=scenario, note="уже было уведомление Адама")
                return "skipped: duplicate"
            try:
                await bot.send_message(telegram_id, text, reply_markup=_keyboard())
            except Exception:
                release_notification(telegram_id, day_iso, kind, scope)
                raise
            message_id = add_ai_message(telegram_id, "assistant", text)
            ac.finish_slot(telegram_id, day_iso, slot, "sent", scenario=scenario, source=source, message_id=message_id, note=candidate["reason"])
            return f"sent: {scenario}/{source}"
        except TelegramForbiddenError as exc:
            mark_bot_blocked(telegram_id)
            ac.finish_slot(telegram_id, day_iso, slot, "skipped", note="бот заблокирован")
            log_error("adam_followup", exc, telegram_id)
            return "skipped: blocked"
        except Exception as exc:
            ac.release_slot(telegram_id, day_iso, slot)               # временный сбой: следующий тик повторит, пока повод не устарел
            log_error("adam_followup", exc, telegram_id)
            return "error"
    return "not_due"


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
        try:
            if (await process_followup(bot, user["telegram_id"], scope)).startswith("sent"):
                sent += 1
        except Exception as exc:
            log_error("adam_followup", exc, user["telegram_id"])
    if sent:
        logger.info("Адам пишет первым: отправлено %s сообщений", sent)
