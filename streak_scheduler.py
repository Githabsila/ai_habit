
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from db import (
    rollover_all_users, get_streak_users, get_timezone, create_daily_tasks,
    has_completed_today, claim_notification, RISK_15, RISK_23,
    get_weekly_bonus_available,
)

logger = logging.getLogger("streak_scheduler")

async def run_streak_rollover(bot=None):
    try:
        changed = rollover_all_users()
        for uid in changed:
            try:
                create_daily_tasks(uid)
            except Exception:
                logger.exception("Не удалось создать ежедневные задания для %s", uid)
        if changed:
            logger.info("Streak rollover: %s users", len(changed))
    except Exception:
        logger.exception("Ошибка rollover ударного режима")

async def run_streak_risk_notifications(bot):
    if not bot:
        return
    for uid in get_streak_users():
        try:
            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if now.minute != 0 or now.hour not in (15, 23):
                continue
            if has_completed_today(uid):
                continue
            kind = "risk15" if now.hour == 15 else "risk23"
            day = now.date().isoformat()
            if not claim_notification(uid, day, kind):
                continue
            text = RISK_15 if kind == "risk15" else __import__("random").choice(RISK_23)
            await bot.send_message(uid, text)
        except Exception:
            logger.exception("Ошибка streak-risk для %s", uid)

async def run_weekly_streak_bonus(bot):
    if not bot:
        return
    for uid in get_streak_users():
        try:
            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if now.weekday() != 6 or now.hour != 10 or now.minute != 0:
                continue
            day = now.date().isoformat()
            if not get_weekly_bonus_available(uid):
                continue
            if not claim_notification(uid, day, "weekly_bonus"):
                continue
            await bot.send_message(
                uid,
                "🎁 Неделя в огне!\n\nТы прошёл предыдущие 7 дней без пропусков. "
                "Открой ADAM и выбери награду: 200 Adam Coin или временную рамку на 7 дней."
            )
        except Exception:
            logger.exception("Ошибка weekly streak bonus для %s", uid)
