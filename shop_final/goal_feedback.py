"""
goal_feedback.py
Еженедельно (Premium-фича) сравнивает цель из анкеты с реальным прогрессом
пользователя и присылает честную AI-обратную связь: где человек торопится,
а где стоит поднажать.
"""

import logging

from db import get_surveys_due_for_feedback, mark_feedback_sent, get_progress, get_user, log_error
from multi_agent import analyze_goal_progress
from alerts import notify_admins

logger = logging.getLogger("goal_feedback")


async def run_goal_feedback(bot):
    surveys = get_surveys_due_for_feedback(days=7)
    sent = 0
    failed = 0

    for survey in surveys:
        user_id = survey["user_id"]
        try:
            user = get_user(user_id)
            progress = get_progress(user_id) or {}
            completed_ratio = (
                progress.get("completed", 0) / progress["total"]
                if progress.get("total") else 0
            )

            feedback = await analyze_goal_progress(
                life_goal=survey["life_goal"] or "",
                bot_goal=survey["bot_goal"] or "",
                streak=user["streak"] if user else 0,
                completed_ratio=completed_ratio,
            )

            if feedback:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"🧠 <b>Разбор недели по вашей цели</b>\n\n{feedback}",
                    parse_mode="HTML"
                )
                sent += 1

            mark_feedback_sent(user_id)

        except Exception as e:
            failed += 1
            logger.warning(f"Не удалось отправить разбор цели {user_id}: {e}")
            log_error("goal_feedback", e, user_id)

    if failed:
        await notify_admins(
            bot,
            f"goal_feedback: {sent} отправлено, {failed} ошибок за прогон — см. статистику ошибок в админке."
        )
