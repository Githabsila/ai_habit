"""
Режим оформления «Royal Gold» (решение владельца 28.10.2026: сделать графику бота и приложения премиальнее, но так, чтобы всё можно было вернуть как было).

Три режима, переключаются в админке одним нажатием и действуют сразу (без деплоя):
  classic  — как было: ни чат бота, ни приложение ничего нового не показывают;
  preview  — новое оформление видят только админы (режим по умолчанию: владелец смотрит и решает, обычные люди ничего не замечают);
  premium  — новое оформление у всех.

Само оформление ничего не заменяет: у приложения это отдельный файл стилей royal.css, который подключается только когда режим включён для человека, у бота — обёртка
bot_style.py над сообщениями и баннеры из webapp/static/assets/bot/. Выключили режим — прежний вид возвращается целиком. Кроме того, перед работой поставлена метка git
`before-royal-style` (git checkout before-royal-style возвращает код к состоянию до оформления).
"""
from .core import connect

MODES = ("classic", "preview", "premium")
DEFAULT_MODE = "preview"
KEY = "premium_mode"


def get_mode():
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM style_settings WHERE key=?", (KEY,)).fetchone()
    finally:
        conn.close()
    return row["value"] if row and row["value"] in MODES else DEFAULT_MODE


def set_mode(mode, admin_id=None):
    if mode not in MODES:
        return False
    conn = connect()
    try:
        conn.execute("INSERT INTO style_settings(key, value, updated_by, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP", (KEY, mode, admin_id))
        conn.commit()
    finally:
        conn.close()
    return True


def premium_active(user_id):
    """Показывать ли человеку новое оформление: режим «premium» — всем, «preview» — только админам, «classic» — никому."""
    mode = get_mode()
    if mode == "premium":
        return True
    if mode == "classic" or not user_id:
        return False
    try:
        from config import ADMIN_IDS
        return int(user_id) in {int(i) for i in ADMIN_IDS}
    except Exception:
        return False
