"""
goal_feedback.py
Еженедельно (Premium-фича) сравнивает цель из анкеты с реальным прогрессом
пользователя и присылает честную AI-обратную связь: где человек торопится,
а где стоит поднажать.
"""

import html
import logging

from aiogram.exceptions import TelegramForbiddenError

from db import get_surveys_due_for_feedback, mark_feedback_sent, get_progress, get_user, log_error, mark_bot_blocked
from db.settings import get_settings, in_quiet_hours
from multi_agent import analyze_goal_progress
from db import ai_usage
from alerts import notify_admins

logger = logging.getLogger("goal_feedback")


def _due_local(user_id, now_utc=None):
    """Понедельник 10:00–11:00 по местному времени человека и не в его тихие часы, иначе None. Раньше — один cron в 10:00 по
    серверу: у UTC−8 разбор приходил в 2 ночи (прогон 09.11). Проверяем ДО вызова ИИ, чтобы не платить за неотправляемое."""
    from coach import _local_due
    local = _local_due(user_id, now_utc, weekday=0, hour=10)
    if local is None or in_quiet_hours(get_settings(user_id), local):
        return None
    return local


async def run_goal_feedback(bot, now_utc=None):
    # 6, а не 7 дней: отметка «отправлено» ставится на секунды позже слота, и через неделю тот же слот был «ещё рано» — отправка
    # сползала на 15 минут каждую неделю и раз в 5 недель выпадала из окна 10:00–11:00 (прогон 11.11). Окно (пн 10:00) держит _due_local.
    surveys = get_surveys_due_for_feedback(days=6)
    sent = 0
    failed = 0

    for survey in surveys:
        user_id = survey["user_id"]
        try:
            if _due_local(user_id, now_utc) is None:
                continue
            user = get_user(user_id)
            progress = get_progress(user_id) or {}
            completed_ratio = (
                progress.get("completed", 0) / progress["total"]
                if progress.get("total") else 0
            )

            with ai_usage.scope(user_id, "goal"):
                feedback = await analyze_goal_progress(
                    life_goal=survey["life_goal"] or "",
                    bot_goal=survey["bot_goal"] or "",
                    streak=user["streak"] if user else 0,
                    completed_ratio=completed_ratio,
                )

            if feedback:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"🧠 <b>Мой разбор недели по твоей цели</b>\n\n{html.escape(feedback, quote=False)}",   # прогон 09.11
                    parse_mode="HTML"
                )
                sent += 1
                # Помечаем "разобрано" только когда реально что-то отправили.
                # Раньше вызывалось безусловно — если AI вернул пустой ответ
                # (сбой/лимит), пользователь молча пропускал разбор и следующая
                # попытка была только через 7 дней, хотя фактически ничего не
                # ушло.
                mark_feedback_sent(user_id)

        except TelegramForbiddenError:
            mark_bot_blocked(user_id)          # заблокировал бота — больше не тратим на него вызов ИИ каждую неделю
        except Exception as e:
            failed += 1
            logger.warning(f"Не удалось отправить разбор цели {user_id}: {e}")
            log_error("goal_feedback", e, user_id)

    if failed:
        await notify_admins(
            bot,
            f"goal_feedback: {sent} отправлено, {failed} ошибок за прогон — см. статистику ошибок в админке."
        )
