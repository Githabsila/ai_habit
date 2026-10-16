"""
Уведомления админам о подозрении на накрутку наград (db/habit_guard.py): раз в несколько минут каждое новое подозрение уходит админам одним сообщением.
Сообщение одно на подозрение (человек + день + вид), повторов нет; сбой доставки одному админу не мешает остальным и не повторяется бесконечно.
"""
import html
import logging

from aiogram.exceptions import TelegramForbiddenError

from config import ADMIN_IDS
from db import habit_guard, log_error

logger = logging.getLogger(__name__)


def alert_text(item):
    name = html.escape(item.get("first_name") or item.get("username") or "Игрок")
    handle = item.get("handle") or item.get("username")
    who = f"<b>{name}</b>" + (f" (@{html.escape(str(handle))})" if handle else "") + f" · ID {item['user_id']}"
    label = habit_guard.KIND_LABELS.get(item["kind"], item["kind"])
    return (
        f"🛑 <b>Подозрение на накрутку наград</b>\n{who}\n{html.escape(label)}: {html.escape(item.get('detail') or '')}.\n\n"
        "Монеты за привычки у этого человека на сегодня отключены. Посмотреть и при необходимости заморозить награды дольше — "
        "админка → «Накрутка наград»."
    )


async def run_abuse_alerts(bot):
    if not bot:
        return 0
    sent = 0
    for item in habit_guard.unnotified_flags():
        text = alert_text(item)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
                sent += 1
            except TelegramForbiddenError:
                logger.info("Админ %s не принимает сообщения от бота", admin_id)
            except Exception as exc:
                log_error("abuse_alerts", exc, admin_id)
        habit_guard.mark_notified(item["id"])          # одна попытка на подозрение: сбой доставки не должен повторяться каждые три минуты
    if sent:
        logger.info("Накрутка наград: админам отправлено %s сообщений", sent)
    return sent
