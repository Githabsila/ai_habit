"""
onboarding_auto.py
Автоодобрение анкет ("Project ADAM" — эффект закрытого сообщества):
после AUTO_APPROVE_HOURS часов в статусе 'pending' пользователь получает
доступ автоматически, даже если админ не одобрил вручную. Реализовано как
периодический (не одноразовый) job в scheduler — переживает рестарт бота,
в отличие от разовых date-джобов apscheduler в памяти процесса.
"""

import logging

from config import AUTO_APPROVE_HOURS
from db import get_users_pending_since, set_access_status, log_error
from handlers.onboarding import notify_approved

logger = logging.getLogger("onboarding_auto")


async def run_auto_approve(bot):
    pending_ids = get_users_pending_since(AUTO_APPROVE_HOURS)
    approved = 0

    for user_id in pending_ids:
        try:
            set_access_status(user_id, "approved")
            await notify_approved(bot, user_id)
            approved += 1
        except Exception as e:
            log_error("auto_approve", e, user_id)

    if approved:
        logger.info(f"Автоодобрение: открыт доступ {approved} пользователям")
