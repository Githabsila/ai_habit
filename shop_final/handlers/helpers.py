# handlers/helpers.py
"""
Утилиты для отправки длинных ответов ИИ-наставника в Telegram.

Проблема №1 (обрыв текста): корень бага был не здесь, а в multi_agent.py —
модель упиралась в max_tokens и генерация обрывалась раньше времени
(см. _ask/_trim_to_last_sentence в multi_agent.py). Но даже с исправленной
генерацией стоит иметь безопасный механизм на случай, если готовый ответ
всё равно окажется длиннее лимита Telegram (4096 символов на сообщение) —
например, для полного мультиагентного пайплайна на сложные вопросы.

Проблема №3 (неудобный скроллинг): длинные ответы режутся на части по
границам абзацев/предложений/слов (никогда — посреди слова), части
отправляются последовательно с пометкой "Часть N/M", когда частей больше
одной. Inline-клавиатура вешается только на последнюю часть, чтобы не
мешать чтению текста выше.
"""

import re

TELEGRAM_MESSAGE_LIMIT = 4096
# Берём с запасом от жёсткого лимита — под HTML-обвязку заголовка и пометку
# "Часть N/M".
DEFAULT_CHUNK_SIZE = 3500

_SENTENCE_END_RE = re.compile(r'[.!?…]["\')\]]?\s')


def split_for_telegram(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """
    Режет текст на части не длиннее chunk_size, по возможности не разрывая
    ни слова, ни предложения.

    Порядок предпочтений для места разреза:
      1. граница абзаца (пустая строка);
      2. граница предложения (после . ! ? …);
      3. ближайший пробел (чтобы не разорвать слово).
    Слово разрывается только в вырожденном случае, когда оно само по себе
    длиннее chunk_size.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    while len(text) > chunk_size:
        window = text[:chunk_size]
        min_cut = int(chunk_size * 0.4)  # не режем слишком рано в начале куска

        cut = window.rfind("\n\n")

        if cut < min_cut:
            sentence_cut = -1
            for m in _SENTENCE_END_RE.finditer(window):
                sentence_cut = m.end()
            if sentence_cut >= min_cut:
                cut = sentence_cut

        if cut < min_cut:
            space_cut = window.rfind(" ")
            if space_cut > 0:
                cut = space_cut

        if cut <= 0:
            cut = chunk_size  # край случай: слово длиннее chunk_size целиком

        chunks.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        chunks.append(text)

    return chunks


def _with_part_label(chunk: str, index: int, total: int) -> str:
    if total <= 1:
        return chunk
    return f"{chunk}\n\n<i>Часть {index}/{total}</i>"


async def send_long_message(
    message,
    text: str,
    parse_mode: str = "HTML",
    reply_markup=None,
    header: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
):
    """
    Отправляет text через message.answer(...), автоматически разбивая на
    несколько сообщений, если он не влезает в лимит Telegram.

    header — необязательный HTML-префикс (например "💡 <b>Совет дня</b>"),
    приклеивается только к первому сообщению.
    reply_markup вешается только на последнее отправленное сообщение.
    Возвращает список отправленных сообщений.
    """
    full = f"{header}\n\n{text}" if header else text

    if len(full) <= TELEGRAM_MESSAGE_LIMIT:
        return [await message.answer(full, parse_mode=parse_mode, reply_markup=reply_markup)]

    header_room = len(header) + 2 if header else 0
    chunks = split_for_telegram(text, max(chunk_size - header_room, 500))
    total = len(chunks)

    sent = []
    for i, chunk in enumerate(chunks, start=1):
        prefix = f"{header}\n\n" if (header and i == 1) else ""
        body = _with_part_label(chunk, i, total)
        markup = reply_markup if i == total else None
        sent.append(await message.answer(f"{prefix}{body}", parse_mode=parse_mode, reply_markup=markup))

    return sent


async def edit_or_split_message(
    editable_message,
    answerable_message,
    text: str,
    parse_mode: str = "HTML",
    reply_markup=None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
):
    """
    Вариант send_long_message для случая, когда первая часть ответа должна
    попасть в УЖЕ существующее сообщение через .edit_text (типичный паттерн
    "🤔 Думаю над ответом..." -> редактируем на готовый ответ), а не через
    .answer. Если текст длиннее одного сообщения, первая часть уходит через
    edit_text, остальные — новыми сообщениями через answerable_message.answer.

    editable_message   — сообщение, которое редактируем (например thinking_msg).
    answerable_message — сообщение, у которого есть .answer(...) для
                          отправки дополнительных частей (обычно исходное
                          message пользователя).
    reply_markup вешается только на последнюю часть.
    """
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return [await editable_message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)]

    chunks = split_for_telegram(text, chunk_size)
    total = len(chunks)

    sent = [
        await editable_message.edit_text(
            _with_part_label(chunks[0], 1, total),
            parse_mode=parse_mode,
            reply_markup=reply_markup if total == 1 else None,
        )
    ]

    for i, chunk in enumerate(chunks[1:], start=2):
        markup = reply_markup if i == total else None
        sent.append(
            await answerable_message.answer(
                _with_part_label(chunk, i, total),
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        )

    return sent


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def day_phrase(n: int) -> str:
    return f"{n} {plural_ru(n, 'день', 'дня', 'дней')}"
