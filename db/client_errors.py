"""
Улучшение #70: логирование клиентских JS-ошибок.

Раньше единственный способ узнать про JS-краш в Mini App у реального
пользователя — попросить прислать видео/скриншот открытой консоли вручную
(именно так был найден и починен баг с backdrop-filter в .tab-bar в этой же
сессии). window.onerror/unhandledrejection на фронте (app.js) шлют сюда
best-effort, без ретраев и без блокировки UI — если сама отправка ошибки
упала, это тихо игнорируется.
"""
from .core import connect

MAX_MESSAGE_LEN = 500
MAX_STACK_LEN = 4000
MAX_URL_LEN = 300
MAX_UA_LEN = 300


def log_client_error(user_id, message, stack=None, url=None, user_agent=None):
    conn = connect()
    c = conn.cursor()
    c.execute(
        "INSERT INTO client_errors(user_id, message, stack, url, user_agent) VALUES (?,?,?,?,?)",
        (
            user_id,
            (str(message) if message else "")[:MAX_MESSAGE_LEN],
            (str(stack) if stack else None) and str(stack)[:MAX_STACK_LEN],
            (str(url) if url else None) and str(url)[:MAX_URL_LEN],
            (str(user_agent) if user_agent else None) and str(user_agent)[:MAX_UA_LEN],
        ),
    )
    conn.commit()
    conn.close()


def get_recent_client_errors(limit=100):
    conn = connect()
    c = conn.cursor()
    c.execute(
        "SELECT id, user_id, message, stack, url, user_agent, created_at "
        "FROM client_errors ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
