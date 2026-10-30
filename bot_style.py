"""
bot_style.py

Оформление «Royal Gold» для чата с ботом (решение владельца 28.10.2026; режимы classic / preview / premium — db/style_mode.py, переключатель в админке).

Две вещи, обе работают только для тех, у кого режим включён (db/style_mode.py::premium_active), поэтому «Классика» и чужие чаты остаются как были:
  • royal_dress — request-мидлварь Bot API (подключается в main.py рядом с retry_flood_control): длинным сообщениям добавляет золотую шапку ADAM и превращает
    дефисы списков в ромбики. Короткие ответы, сообщения с entities и с Markdown не трогает; повторно не оформляет.
  • send_banner — на важных сообщениях (приветствие, пробный период, оплата) вместо простого текста уходит золотой баннер с тем же текстом в подписи.
    Баннеры нарисованы tools/make_royal_banners.py и лежат в webapp/static/assets/bot/.
Любой сбой оформления возвращает исходное сообщение: человек никогда не остаётся без текста из-за украшений.
"""
import logging
import os
import re

from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import FSInputFile

from db import style_mode

logger = logging.getLogger("bot_style")

BANNER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp", "static", "assets", "bot")
BANNER_KINDS = ("welcome", "trial", "paid")

HEADER = "⚜️ ━━━ ✦ <b>ADAM</b> ✦ ━━━ ⚜️"
PLAIN_HEADER = "⚜️ ━━━ ✦ ADAM ✦ ━━━ ⚜️"     # так шапка выглядит в message.text: хендлеры, правящие старое сообщение, возвращают её без тегов
MIN_LEN = 70                       # короче — это «Готово ✓» и подобное, шапка там лишняя
MAX_LEN = 3900                     # лимит Telegram 4096: шапка и перевод строки не должны его пробить
CAPTION_LIMIT = 1024

_BULLET_WORD = re.compile(r"(?m)^([ \t]*)[-•] (?=[^\W\d_])")     # «- Привычки» → «◆ Привычки»
_BULLET_ICON = re.compile(r"(?m)^([ \t]*)[-•] (?=[^\w\s])")      # «- 🎯 Привычки» → «🎯 Привычки» (значок уже есть)


def active(user_id):
    try:
        return bool(user_id) and style_mode.bot_active(user_id)
    except Exception:
        logger.warning("Не удалось определить режим оформления для %s", user_id, exc_info=True)
        return False


def _bullets(text):
    return _BULLET_ICON.sub(r"\1", _BULLET_WORD.sub(r"\1◆ ", text))


def dress(text):
    """Текст сообщения → оформленный (HTML). Уже оформленный, короткий или слишком длинный возвращается как есть."""
    if not isinstance(text, str) or text.startswith((HEADER, PLAIN_HEADER)):
        return text
    body = text.strip()
    if len(body) < MIN_LEN or len(body) + len(HEADER) + 1 > MAX_LEN:
        return text
    return HEADER + "\n" + _bullets(body)


def _parse_mode_ok(method):
    """Шапка содержит HTML-теги: безопасна, если сообщение разбирается как HTML (по умолчанию бот именно такой) и нет ручных entities (у них сдвинулись бы смещения)."""
    if getattr(method, "entities", None):
        return False
    mode = getattr(method, "parse_mode", None)
    return mode is None or not isinstance(mode, str) or mode == "HTML"


async def royal_dress(make_request, bot, method):
    """Request-мидлварь: оформляет исходящие SendMessage/EditMessageText людям, у которых включено оформление, остальные вызовы пропускает без изменений."""
    try:
        chat_id = getattr(method, "chat_id", None)
        # Только личные чаты (id людей положительные): группы и каналы получают сообщения как раньше.
        if isinstance(method, (SendMessage, EditMessageText)) and isinstance(chat_id, int) and chat_id > 0 and _parse_mode_ok(method):
            new_text = dress(method.text)
            if new_text != method.text and active(method.chat_id):
                method = method.model_copy(update={"text": new_text})
    except Exception:
        logger.warning("Оформление сообщения не удалось — отправляю как есть", exc_info=True)
    return await make_request(bot, method)


def banner_path(kind):
    path = os.path.join(BANNER_DIR, f"{kind}.png")
    return path if kind in BANNER_KINDS and os.path.exists(path) else None


async def send_banner(bot, chat_id, kind, text, reply_markup=None):
    """Золотой баннер с текстом в подписи. True — сообщение отправлено; False — оформление выключено для человека, нет картинки или Telegram отказал: тогда вызывающий
    отправляет обычное сообщение, как раньше."""
    if bot is None or not active(chat_id):
        return False
    path = banner_path(kind)
    if not path:
        return False
    caption = _bullets(text.strip())
    try:
        if len(caption) <= CAPTION_LIMIT:
            await bot.send_photo(chat_id, FSInputFile(path), caption=caption, reply_markup=reply_markup)
        else:
            await bot.send_photo(chat_id, FSInputFile(path))
            await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except Exception:
        logger.warning("Баннер %s не отправился человеку %s — отправлю обычным сообщением", kind, chat_id, exc_info=True)
        return False


async def send_banner_reply(message, kind, text, reply_markup=None):
    """То же для ответа на входящее сообщение (в тестах и особых случаях у объекта может не быть bot/from_user — тогда просто False)."""
    bot = getattr(message, "bot", None)
    user = getattr(message, "from_user", None)
    return await send_banner(bot, getattr(user, "id", None), kind, text, reply_markup)
