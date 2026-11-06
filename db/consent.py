"""
Согласие на обработку персональных данных (юридический минимум, 06.11; старый план, п.2.7).

Первый экран стартовой анкеты: «Продолжая, ты соглашаешься с Условиями и Политикой конфиденциальности» (ссылки). «Поехали» записывает
факт согласия, версию документов и время (users.consent_version, users.consent_at). При заметном изменении условий или политики —
поднять CONSENT_VERSION: в админке будет видно, кто соглашался со старой версией. Формулировку без юриста держим минимальной.
"""
from .core import connect

CONSENT_VERSION = "2026-11-06"


def record_consent(user_id, version=CONSENT_VERSION):
    conn = connect()
    try:
        conn.execute("UPDATE users SET consent_version=?, consent_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (str(version)[:20], user_id))
        conn.commit()
    finally:
        conn.close()


def consent_of(user_id):
    """{"version": ..., "at": ...} или None, если человек ещё не соглашался."""
    conn = connect()
    try:
        row = conn.execute("SELECT consent_version, consent_at FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None or not row["consent_at"]:
        return None
    return {"version": row["consent_version"], "at": row["consent_at"]}
