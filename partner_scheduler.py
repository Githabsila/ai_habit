"""
Фоновая работа партнёрской программы (db/partners.py): страховка начислений (покупки приглашённых, которые не успели записаться сразу, и возвраты) и один сигнал участнику
о новом начислении. Сигнал уходит один раз на начисление: сначала помечаем «сообщили», потом шлём — повтор не нужен, дубля не будет.
Сообщение короткое и без имён приглашённых: подробности — в разделе партнёрской программы в приложении.
"""
import logging

from db import partners
from db.tracking import track_server
from db.users import is_bot_forbidden_error, mark_bot_blocked

logger = logging.getLogger("partner_scheduler")


async def run_partner_jobs(bot):
    try:
        partners.sync_accruals()
    except Exception:
        logger.exception("Не удалось сверить начисления партнёрской программы")
    sent = 0
    for row in partners.unnotified_accruals():
        if not partners.mark_accrual_notified(row["id"]):
            continue
        text = (f"🤝 Новое начисление: {row['amount']} ⭐. Кто-то из приглашённых тобой оформил покупку. "
                "Все цифры и история — в разделе «Партнёрская программа» в приложении.")
        try:
            await bot.send_message(row["partner_id"], text)
            track_server(row["partner_id"], "partner_accrual_signal", meta={"amount": row["amount"]})
            sent += 1
        except Exception as exc:
            if is_bot_forbidden_error(exc):
                mark_bot_blocked(row["partner_id"])
            logger.warning("Не удалось сообщить участнику %s о начислении", row["partner_id"])
    if sent:
        logger.info("Партнёрская программа: сообщений о начислениях — %s", sent)
