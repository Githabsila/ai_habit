"""
Уникальный игровой @ник (в духе Duolingo/Instagram) — отдельная от
telegram-username сущность: username из Telegram необязателен (часть
пользователей его не ставит) и не гарантированно уникален во времени
(можно сменить/убрать), а @ник есть у КАЖДОГО пользователя с момента
регистрации (см. db.users.add_user) и виден везде, где раньше показывался
только first_name — рейтинг, публичный профиль, лента друзей, команда.
Пользователь может сменить его сам в настройках (см. update_handle).

Уникальность гарантирует UNIQUE INDEX idx_users_handle (db/core.py::
create_tables) — проверка здесь (SELECT перед INSERT/UPDATE) даёт понятную
ошибку вместо сырого IntegrityError, а не заменяет собой констрейнт в БД.
"""
import random
import re
import string

from .core import connect

HANDLE_RE = re.compile(r"^[a-z0-9_]{3,20}$")

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(text):
    out = []
    for ch in (text or "").lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
    return "".join(out)


def _handle_base(first_name):
    base = _translit(first_name)[:12]
    return base if len(base) >= 3 else "user"


def generate_unique_handle(cursor, first_name):
    """Транслитерация имени + случайный цифровой суффикс, с проверкой
    уникальности через переданный курсор (та же транзакция, что и
    последующий INSERT/UPDATE — вызывающий сам сохраняет результат).
    Наращивает длину суффикса при повторных коллизиях."""
    base = _handle_base(first_name)
    for suffix_len in (3, 4, 4, 5, 6, 8):
        for _ in range(4):
            candidate = f"{base}{''.join(random.choices(string.digits, k=suffix_len))}"[:20]
            cursor.execute("SELECT 1 FROM users WHERE handle=?", (candidate,))
            if cursor.fetchone() is None:
                return candidate
    # Практически недостижимо (десятки коллизий подряд), но не должно
    # приводить к бесконечному циклу.
    return "user" + "".join(random.choices(string.digits, k=12))


def get_handle(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT handle FROM users WHERE telegram_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row["handle"] if row else None


def is_handle_taken(handle, exclude_user_id=None):
    conn = connect()
    cursor = conn.cursor()
    if exclude_user_id is None:
        cursor.execute("SELECT 1 FROM users WHERE handle=?", (handle,))
    else:
        cursor.execute("SELECT 1 FROM users WHERE handle=? AND telegram_id != ?", (handle, exclude_user_id))
    taken = cursor.fetchone() is not None
    conn.close()
    return taken


def normalize_handle(raw):
    return (raw or "").strip().lstrip("@").lower()


def update_handle(user_id, new_handle):
    """Меняет @ник пользователя (roadmap — "как в Duolingo", можно менять
    самому в настройках). Возвращает None при успехе, иначе код ошибки:
    'invalid_format' (не 3-20 символов a-z0-9_) или 'taken' (уже занят)."""
    normalized = normalize_handle(new_handle)
    if not HANDLE_RE.match(normalized):
        return "invalid_format"
    if is_handle_taken(normalized, exclude_user_id=user_id):
        return "taken"
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET handle=? WHERE telegram_id=?", (normalized, user_id))
    conn.commit()
    conn.close()
    return None
