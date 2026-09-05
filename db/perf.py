"""
Телеметрия долгих кадров/задач Mini App — см. комментарий у таблицы
perf_events в db/core.py::create_tables(). Единственная цель — дать
разработчику точные цифры по багу "лагает/чернеет при прокрутке" в
Telegram WebView на Android, который невозможно воспроизвести локально.
"""
from .core import connect

# Событий может быть очень много (частый лаг у многих пользователей) —
# ограничиваем историю, чтобы таблица не росла бесконечно на проде.
MAX_STORED_EVENTS = 5000

# Та же дисциплина, что и в db/client_errors.py::log_client_error —
# поля со стороны клиента обрезаются на сервере, а не только в JS.
MAX_TAB_LEN = 50
MAX_PATH_LEN = 300
MAX_DEVICE_INFO_LEN = 500


def add_perf_event(user_id, event_type, duration_ms, tab=None, path=None, is_scrolling=False, device_info=None):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO perf_events(user_id, event_type, duration_ms, tab, path, is_scrolling, device_info)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            str(event_type)[:20],
            int(duration_ms),
            (str(tab) if tab else None) and str(tab)[:MAX_TAB_LEN],
            (str(path) if path else None) and str(path)[:MAX_PATH_LEN],
            1 if is_scrolling else 0,
            (str(device_info) if device_info else None) and str(device_info)[:MAX_DEVICE_INFO_LEN],
        ),
    )
    # Дешёвая ротация без отдельного cron: раз в ~50 вставок подчищаем
    # самые старые строки сверх лимита — событий может быть много, а
    # смотрят их редко и вручную.
    if cursor.lastrowid % 50 == 0:
        cursor.execute(
            "DELETE FROM perf_events WHERE id NOT IN (SELECT id FROM perf_events ORDER BY id DESC LIMIT ?)",
            (MAX_STORED_EVENTS,),
        )
    conn.commit()
    conn.close()


def get_recent_perf_events(limit=200):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM perf_events ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_perf_summary():
    """Агрегат по типу события — сколько раз, средняя/максимальная
    длительность, за последние сутки и за всё время сразу, чтобы одним
    запросом понять масштаб проблемы, не листая сотни отдельных строк."""
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            event_type,
            COUNT(*) as count,
            AVG(duration_ms) as avg_ms,
            MAX(duration_ms) as max_ms,
            SUM(CASE WHEN created_at >= datetime('now', '-1 day') THEN 1 ELSE 0 END) as count_24h
        FROM perf_events
        GROUP BY event_type
        ORDER BY count DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]
