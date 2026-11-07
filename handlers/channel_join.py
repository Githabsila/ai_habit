"""
Заявки на вступление в закрытый канал (прогон 07.11, находка A1).

Бот выдаёт личную ссылку-заявку с именем user_<id> (db/subscription.py::try_grant_channel_access). Заявку одобряем, только если её подал
сам получатель ссылки и доступ у него сейчас действует (подписка + серия, channel_removal_reason). Иначе — отклоняем: ссылку переслали
другу или подписка уже кончилась. Раньше ссылка была ничьей и бессрочной: друг по ней оставался в канале навсегда.
"""
import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest

from db.core import connect
from db.subscription import channel_removal_reason

logger = logging.getLogger(__name__)
router = Router()


def _granted(user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT channel_access_granted_at FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        return bool(row and row["channel_access_granted_at"])
    finally:
        conn.close()


async def on_join_request(request, bot):
    from config import CLOSED_CHANNEL_ID
    if not CLOSED_CHANNEL_ID or str(request.chat.id) != str(CLOSED_CHANNEL_ID):
        return
    user_id = request.from_user.id
    name = getattr(request.invite_link, "name", None) if request.invite_link else None
    allowed = name == f"user_{user_id}" and _granted(user_id) and channel_removal_reason(user_id) is None
    try:
        if allowed:
            await bot.approve_chat_join_request(chat_id=request.chat.id, user_id=user_id)
        else:
            await bot.decline_chat_join_request(chat_id=request.chat.id, user_id=user_id)
            logger.info("Заявка в канал отклонена: %s по ссылке %s", user_id, name)
    except Exception:
        logger.exception("Не удалось обработать заявку в канал от %s", user_id)


@router.chat_join_request()
async def chat_join_request(request: ChatJoinRequest):
    await on_join_request(request, request.bot)
