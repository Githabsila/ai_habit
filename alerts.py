import logging

from config import ADMIN_IDS

logger = logging.getLogger("alerts")


async def notify_admins(bot, text: str):
    """Шлёт короткое сообщение всем админам — используется в фоновых задачах
    (scheduler jobs), где иначе ошибку никто не увидит, пока не зайдёт в
    статистику вручную."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=f"🩺 {text}")
        except Exception:
            logger.warning(f"Не удалось отправить алерт админу {admin_id}")
