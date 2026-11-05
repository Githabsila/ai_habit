"""
Возраст 16+ (решение владельца 05.11).

В стартовой анкете приложения вариант «Мне нет 16» (age_range=under16) ставит access_status=age_restricted. Дальше работает обычная проверка
доступа: приложение отвечает 403 access_age_restricted (экран с этим текстом), рассылки не идут (db/push_access.py пропускает только approved),
бот отвечает этим текстом на любое сообщение (middlewares/access_control.py). Ошибся — админ возвращает доступ в карточке человека.
"""
AGE_UNDER_16 = "under16"
AGE_RESTRICTED = "age_restricted"
AGE_RESTRICTED_TEXT = "ADAM — для тех, кому уже исполнилось 16. Возвращайся, когда будет 16: мы тебя дождёмся 🙌"


def age_restricted_users(limit=30):
    """Кто закрыт по возрасту — для веб-админки (список «ждут одобрения» с пометкой 16+): если человек ошибся, админ откроет доступ."""
    from .core import connect
    conn = connect()
    try:
        return conn.execute("SELECT telegram_id, username, first_name FROM users WHERE access_status=? ORDER BY telegram_id DESC LIMIT ?",
                            (AGE_RESTRICTED, int(limit))).fetchall()
    finally:
        conn.close()
