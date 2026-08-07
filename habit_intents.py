import re

from db import (
    add_habit,
    get_habits,
    edit_habit,
    delete_habit,
    complete_habit,
)

# =====================================
# УПРАВЛЕНИЕ ПРИВЫЧКАМИ ЧЕРЕЗ AI-ЧАТ
# =====================================
#
# Если пользователь пишет AI-наставнику что-то вроде «добавь привычку
# читать книгу», это распознаётся здесь по паттернам ДО того, как
# сообщение уйдёт в тяжёлый мультиагентный пайплайн — команда выполняется
# напрямую (быстро, бесплатно, без риска, что модель что-то не так поймёт
# и придумает не то). Если сообщение не подходит ни под один паттерн,
# try_handle_habit_intent() возвращает None, и обработка идёт как раньше —
# через chat() / мультиагентную систему.

_ADD_RE = re.compile(
    r"^(?:пожалуйста[,]?\s*)?"
    r"(?:добавь|добавить|создай|создать|заведи|завести|поставь|поставить)\s+"
    r"(?:мне\s+)?(?:новую\s+)?привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_RENAME_RE = re.compile(
    r"^(?:переименуй|переименовать|измени|поменяй)\s+привычку\s+(.+?)\s+(?:на|в)\s+(.+)$",
    re.IGNORECASE,
)

_DELETE_RE = re.compile(
    r"^(?:удали|удалить|убери|убрать|снеси|сноси)\s+привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_COMPLETE_RE = re.compile(
    r"^(?:отметь|выполнил[аи]?|сделал[аи]?)\s+привычку\s*[:\-—]?\s*(.+)$",
    re.IGNORECASE,
)

_LIST_RE = re.compile(
    r"^(?:покажи|выведи|список)\s+(?:мои\s+)?привычки[.!?]*$"
    r"|^какие\s+у\s+меня\s+привычки[.!?]*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return text.strip().strip(".,!?\"'«»").strip()


def _find_habit(user_id: int, query: str):
    """Ищет привычку пользователя по названию.

    Возвращает (habit, None) при однозначном совпадении,
    (None, [варианты]) если совпадений несколько,
    (None, None) если не нашлось ни одной.
    """
    query_norm = query.strip().lower()
    habits = get_habits(user_id)

    exact = [h for h in habits if h["title"].strip().lower() == query_norm]
    if len(exact) == 1:
        return exact[0], None

    partial = [
        h for h in habits
        if query_norm in h["title"].strip().lower()
        or h["title"].strip().lower() in query_norm
    ]
    if len(partial) == 1:
        return partial[0], None
    if len(partial) > 1:
        return None, partial

    return None, None


def _ambiguous_reply(habits) -> str:
    names = ", ".join(f"«{h['title']}»" for h in habits)
    return f"🤔 Нашлось несколько похожих привычек: {names}. Уточни название точнее."


def _not_found_reply(query: str) -> str:
    return f"⚠️ Не нашёл привычку «{query}». Проверь название в разделе «Привычки»."


def try_handle_habit_intent(user_id: int, text: str) -> str | None:
    """Пробует распознать команду по управлению привычками в сообщении AI-чата.

    Возвращает готовый текст ответа, если команда распознана и выполнена,
    либо None, если это обычное сообщение — тогда его нужно отправить в
    обычный AI-пайплайн (chat())."""

    text = (text or "").strip()
    if not text:
        return None

    m = _ADD_RE.match(text)
    if m:
        title = _clean(m.group(1))
        if not title:
            return "⚠️ Напиши название привычки, например: «добавь привычку пить воду»."
        add_habit(user_id, title)
        return f"✅ Привычка «{title}» добавлена!"

    m = _RENAME_RE.match(text)
    if m:
        old_query, new_title = _clean(m.group(1)), _clean(m.group(2))
        if not new_title:
            return "⚠️ Не понял новое название. Напиши, например: «переименуй привычку бег в утренняя пробежка»."
        habit, ambiguous = _find_habit(user_id, old_query)
        if habit:
            edit_habit(habit["id"], new_title)
            return f"✅ Привычка «{habit['title']}» переименована в «{new_title}»."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(old_query)

    m = _DELETE_RE.match(text)
    if m:
        query = _clean(m.group(1))
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            delete_habit(habit["id"])
            return f"🗑 Привычка «{habit['title']}» удалена."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    m = _COMPLETE_RE.match(text)
    if m:
        query = _clean(m.group(1))
        habit, ambiguous = _find_habit(user_id, query)
        if habit:
            done = complete_habit(habit["id"])
            if done:
                return f"🔥 Привычка «{habit['title']}» отмечена выполненной!"
            return f"✅ Привычка «{habit['title']}» уже была отмечена выполненной."
        if ambiguous:
            return _ambiguous_reply(ambiguous)
        return _not_found_reply(query)

    if _LIST_RE.match(text):
        habits = get_habits(user_id)
        if not habits:
            return "У тебя пока нет ни одной привычки. Напиши «добавь привычку …», чтобы завести первую."
        lines = ["📋 Твои привычки:"]
        for h in habits:
            mark = "✅" if h["completed"] else "⬜"
            lines.append(f"{mark} {h['title']}")
        return "\n".join(lines)

    return None
