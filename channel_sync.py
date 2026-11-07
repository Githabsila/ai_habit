"""
Закрытый канал: кто вылетает (решение владельца 05.11).

Вход — оплата + серия 3 дня (db/subscription.py::try_grant_channel_access). Серия сгорела (уже после заморозок, защиты после триала и помощи
друга — streak обнулился) или подписка закончилась → бот убирает человека из канала и пишет ему, как вернуться: серия от
CHANNEL_REENTRY_STREAK_DAYS (5) дней при активной подписке.

Убираем через ban + сразу unban: человек выходит из канала, но не попадает в вечный бан — по новой ссылке он сможет вернуться.
Пока CLOSED_CHANNEL_ID не задан, задача ничего не делает. Тексты нейтральные: что в канале — пока не рассказываем (решение 05.11).
"""
import logging

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from db.core import connect
from db.subscription import channel_removal_reason, mark_channel_removed

logger = logging.getLogger(__name__)

REMOVED_TEXT = {
    "streak": ("🔥 Серия погасла — доступ в закрытый канал закрыт.\n\n"
               "Вернуться можно: набери {days} дней ударного режима подряд — ссылка придёт сама."),
    "subscription": ("⏳ Подписка закончилась — доступ в закрытый канал закрыт.\n\n"
                     "Продли подписку и набери {days} дней ударного режима подряд — ссылка придёт сама."),
}


def _members():
    conn = connect()
    try:
        return [r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM users WHERE channel_access_granted_at IS NOT NULL")]
    finally:
        conn.close()


async def _revoke_link(bot, user_id):
    """Прогон 07.11 (A1): личная ссылка-заявка отзывается при вылете — по старой больше не войти (новая придёт при возврате)."""
    from config import CLOSED_CHANNEL_ID
    conn = connect()
    try:
        row = conn.execute("SELECT channel_invite_link FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        link = row["channel_invite_link"] if row is not None else None
        conn.execute("UPDATE users SET channel_invite_link=NULL WHERE telegram_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    if link:
        try:
            await bot.revoke_chat_invite_link(chat_id=CLOSED_CHANNEL_ID, invite_link=link)
        except Exception:                                      # отзыв — подстраховка: не мешает убрать человека из канала
            logger.warning("Не удалось отозвать ссылку в канал у %s", user_id)


async def remove_from_channel(bot, user_id, reason):
    """Убирает из канала и отмечает это в базе. True — убрали (или его там уже не было)."""
    from config import CHANNEL_REENTRY_STREAK_DAYS, CLOSED_CHANNEL_ID
    try:
        await bot.ban_chat_member(chat_id=CLOSED_CHANNEL_ID, user_id=user_id)
        await bot.unban_chat_member(chat_id=CLOSED_CHANNEL_ID, user_id=user_id, only_if_banned=True)
    except TelegramBadRequest as exc:
        if "not found" not in str(exc).lower() and "participant" not in str(exc).lower():
            raise                                         # что-то другое (нет прав у бота) — пусть видно в логах, человека не трогаем
    await _revoke_link(bot, user_id)
    mark_channel_removed(user_id)
    try:
        await bot.send_message(user_id, REMOVED_TEXT[reason].format(days=CHANNEL_REENTRY_STREAK_DAYS))
    except (TelegramForbiddenError, TelegramBadRequest):
        pass
    return True


async def run_channel_sync(bot):
    from config import CLOSED_CHANNEL_ID
    if not bot or not CLOSED_CHANNEL_ID:
        return 0
    removed = 0
    for user_id in _members():
        try:
            reason = channel_removal_reason(user_id)
            if reason and await remove_from_channel(bot, user_id, reason):
                removed += 1
                logger.info("Убран из закрытого канала %s: %s", user_id, reason)
        except Exception:
            logger.exception("Не удалось проверить участие в канале %s", user_id)
    return removed
