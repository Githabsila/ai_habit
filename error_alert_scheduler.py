"""
Реалтайм-алерт админам при всплеске ошибок.

Раньше мониторинг ошибок жил только в ежедневной сводке (admin_digest_
scheduler.py, 8:00 UTC) — поломка в середине дня оставалась незамеченной
до следующего утра. Этот job проверяет частоту ошибок каждые несколько
минут (см. main.py) и шлёт алерт сразу, как только всплеск превышает
порог — с cooldown, чтобы не заспамить админов на каждый тик при одной
затянувшейся поломке.
"""
import logging
from datetime import datetime, timedelta, timezone

from config import ERROR_SPIKE_THRESHOLD
from db import get_error_stats
from alerts import notify_admins

logger = logging.getLogger("error_alert_scheduler")

# Не слать алерт повторно чаще этого интервала — поломка может длиться
# часами, но незачем присылать одно и то же сообщение на каждый тик.
ALERT_COOLDOWN = timedelta(hours=1)

# In-memory — переживать рестарт процесса не обязано: после рестарта
# лучше перебдеть и прислать алерт заново, чем промолчать про реальную
# проблему.
_last_alert_at = None


async def run_error_spike_check(bot):
    global _last_alert_at

    stats = get_error_stats(hours=1)
    if stats["total"] < ERROR_SPIKE_THRESHOLD:
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if _last_alert_at and now - _last_alert_at < ALERT_COOLDOWN:
        return

    try:
        top = ", ".join(f"{row['scope']}: {row['cnt']}" for row in stats["by_scope"]) or "без разбивки"
        # notify_admins() шлёт без parse_mode (см. alerts.py) — обычный текст.
        await notify_admins(
            bot,
            f"🚨 Всплеск ошибок: {stats['total']} за последний час ({top}). "
            "Загляни в /admin → Статистика.",
        )
        _last_alert_at = now
    except Exception:
        logger.exception("Не удалось отправить алерт о всплеске ошибок")
