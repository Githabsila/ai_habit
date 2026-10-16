"""
Сверка Telegram-ников (пункт 14 владельца): человек поменял @ник в Telegram — в нашей статистике и админке должен быть новый.

Раньше ник записывался один раз при регистрации (add_user — INSERT OR IGNORE) и больше не обновлялся, поэтому в списках «кто больше тратит»,
«кому выдать медаль», поиске и карточке человека висел старый. Теперь три слоя:
  1. «живое» обновление — при каждом сообщении боту и каждом запросе из приложения (db/users.py::sync_telegram_username; не чаще раза в 10 минут
     на человека, лишних записей в базу нет);
  2. ночная сверка этого модуля — раз в сутки бот спрашивает у Telegram (getChat) ник тех, кого не проверяли 7 дней: так обновляются и те, кто
     давно не заходит. За прогон не больше LIMIT человек, остальных заберёт следующая ночь;
  3. кнопка «Сверить ники сейчас» в админке — проверяет всех сразу (до MANUAL_LIMIT) и показывает, сколько изменилось.

Имя в приложении (users.first_name) не трогаем: человек выбирает его сам, «из Telegram имя берётся только при регистрации». Свой внутренний @ник
в приложении (users.handle) тоже не трогаем — он наш, а не телеграмный.
"""
import asyncio
import logging

from aiogram.exceptions import TelegramRetryAfter

from db.users import mark_username_checked, stale_username_users, sync_telegram_username

logger = logging.getLogger(__name__)

STALE_DAYS = 7
LIMIT = 400
MANUAL_LIMIT = 500
PAUSE = 0.06                 # ~16 запросов в секунду — с запасом ниже лимита Telegram (30/с)


async def run_username_sync(bot, limit=LIMIT, stale_days=STALE_DAYS, pause=PAUSE):
    """Сверяет ники по данным Telegram. Возвращает {'checked', 'changed', 'failed', 'left'} — left: сколько ещё осталось непроверенных."""
    stats = {"checked": 0, "changed": 0, "failed": 0, "left": 0}
    if not bot:
        return stats
    ids = stale_username_users(limit + 1, stale_days)
    stats["left"] = max(0, len(ids) - limit)
    for user_id in ids[:limit]:
        try:
            chat = await bot.get_chat(user_id)
            if sync_telegram_username(user_id, getattr(chat, "username", None), force=True):
                stats["changed"] += 1
            stats["checked"] += 1
        except TelegramRetryAfter as exc:
            # Telegram просит притормозить — прекращаем прогон, остальных сверит следующая ночь (или кнопка в админке).
            logger.warning("Сверка ников: Telegram просит подождать %s с, прогон остановлен", exc.retry_after)
            stats["left"] += len(ids[:limit]) - stats["checked"] - stats["failed"]
            break
        except Exception:
            # Не нашли чат (человек удалил аккаунт / бот с ним не общался): отмечаем проверенным, повтор — через неделю, а не каждую ночь.
            stats["failed"] += 1
            mark_username_checked(user_id)
        if pause:
            await asyncio.sleep(pause)
    if stats["checked"] or stats["failed"]:
        logger.info("Сверка ников: проверено %(checked)s, изменилось %(changed)s, не удалось %(failed)s, осталось %(left)s", stats)
    return stats
