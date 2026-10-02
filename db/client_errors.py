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

# "Script error." без стека — это не ошибка нашего кода, а заглушка, которую
# браузер подставляет вместо настоящего сообщения, когда исключение вылетело
# в скрипте с ДРУГОГО домена (у нас единственный такой — telegram.org/js/
# telegram-web-app.js) и тег <script> не помечен crossorigin. В записи нет ни
# строки, ни стека, ни реального текста — диагностировать по ней нечего, а
# сворачивание/возврат в Mini App порождает их пачками. Такие записи не
# сохраняем и не показываем (см. is_opaque_client_error).
OPAQUE_MESSAGE = "Script error."

_NOT_OPAQUE_SQL = "NOT (message = 'Script error.' AND (stack IS NULL OR stack = ''))"


def is_opaque_client_error(message, stack=None):
    return (message or "").strip() == OPAQUE_MESSAGE and not (stack or "").strip()


def _strip_url_fragment(url):
    """Mini App открывается как https://host/#tgWebAppData=<подписанный
    initData пользователя>... — это не должно оседать в БД и (тем более)
    уезжать дальше в сообщения админам: для диагностики хватает пути."""
    return str(url).split("#", 1)[0]


def log_client_error(user_id, message, stack=None, url=None, user_agent=None):
    conn = connect()
    c = conn.cursor()
    c.execute(
        "INSERT INTO client_errors(user_id, message, stack, url, user_agent) VALUES (?,?,?,?,?)",
        (
            user_id,
            (str(message) if message else "")[:MAX_MESSAGE_LEN],
            (str(stack) if stack else None) and str(stack)[:MAX_STACK_LEN],
            (_strip_url_fragment(url) if url else None) and _strip_url_fragment(url)[:MAX_URL_LEN],
            (str(user_agent) if user_agent else None) and str(user_agent)[:MAX_UA_LEN],
        ),
    )
    conn.commit()
    conn.close()


def get_recent_client_errors(limit=100, include_opaque=False):
    conn = connect()
    c = conn.cursor()
    where = "" if include_opaque else f"WHERE {_NOT_OPAQUE_SQL} "
    c.execute(
        "SELECT id, user_id, message, stack, url, user_agent, created_at "
        f"FROM client_errors {where}ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_client_error_stats(hours=24, limit=5):
    """Сводка клиентских JS-ошибок за период для ежедневного отчёта админам:
    сколько всего, у скольких разных пользователей и топ сообщений (по числу
    повторов) — чтобы массовый краш после деплоя отличался от разовой ошибки
    одного устройства."""
    conn = connect()
    c = conn.cursor()
    window = f"-{hours} hours"
    c.execute(
        f"SELECT COUNT(*) AS total, COUNT(DISTINCT user_id) AS users FROM client_errors "
        f"WHERE created_at >= datetime('now', ?) AND {_NOT_OPAQUE_SQL}",
        (window,),
    )
    totals = c.fetchone()
    c.execute(
        f"SELECT message, COUNT(*) AS cnt, COUNT(DISTINCT user_id) AS users FROM client_errors "
        f"WHERE created_at >= datetime('now', ?) AND {_NOT_OPAQUE_SQL} "
        f"GROUP BY message ORDER BY cnt DESC, MAX(id) DESC LIMIT ?",
        (window, limit),
    )
    top = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"total": totals["total"], "users": totals["users"], "top": top}
