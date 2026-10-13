"""
Автоподхват использования: клики по подписям, экраны со временем, действия в боте и вызовы API — без отдельного кода на каждую
новую кнопку или маршрут. Появилась кнопка, экран или эндпоинт — и первый же человек, который ими воспользовался, добавил их в каталог
(ui_catalog) и в дневные счётчики; аналитика (db/stats.py) сопоставляет их с картой функций (webapp/feature_map.py) и показывает всё
неразмеченное отдельным списком «добавь в карту».

Все записи мелкие, дневные и идемпотентные по смыслу (приращения), а любая ошибка записи молча глотается: учёт не должен мешать продукту.
"""
import contextvars
import threading
import time
from datetime import datetime, timedelta, timezone

from .core import connect

# Кто сейчас обслуживается в этом запросе: ставят _authenticate (Mini App) и authenticate (чат Адама), читает мидлвара API.
request_user = contextvars.ContextVar("request_user", default=0)

MAX_LABEL = 40
MAX_CLICK_ROWS = 80
MAX_CATALOG = 1500      # записей одного вида (кнопки/экраны/маршруты) в каталоге
API_FLUSH_SECONDS = 30
MAX_BUFFER_KEYS = 20000   # сколько разных счётчиков держим в памяти, если база временно недоступна


def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _label(value):
    return " ".join(str(value or "").split())[:MAX_LABEL]


# ---------------------------------------------------------------------------------------------------------------- клики и экраны

def record_ui(conn, user_id, clicks=None, screens=None, version=None, day=None):
    """Приращения за пакет от приложения: clicks — [[подпись, экран, сколько]], screens — {экран: {"v": показов, "s": секунд}}.
    Работает на уже открытом соединении (его коммитит вызывающий)."""
    day = day or today_utc()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if version:
        _catalog(conn, "version", _label(version), "", version, now)
    for row in (clicks or [])[:MAX_CLICK_ROWS]:
        try:
            label, screen, count = _label(row[0]), _label(row[1]), int(row[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not label or count <= 0 or count > 500 or not _may_add(conn, "click", label):
            continue
        conn.execute(
            "INSERT INTO ui_clicks(day, user_id, label, screen, clicks) VALUES (?,?,?,?,?) "
            "ON CONFLICT(day, user_id, label, screen) DO UPDATE SET clicks = clicks + excluded.clicks",
            (day, user_id, label, screen, count),
        )
        _catalog(conn, "click", label, screen, version, now)
    for screen, value in list((screens or {}).items())[:40]:
        try:
            name, views, seconds = _label(screen), int(value.get("v", 0)), int(value.get("s", 0))
        except (TypeError, ValueError, AttributeError):
            continue
        if not name or views < 0 or seconds < 0 or views > 500 or seconds > 6 * 3600 or not _may_add(conn, "screen", name):
            continue
        conn.execute(
            "INSERT INTO ui_screens(day, user_id, screen, views, seconds) VALUES (?,?,?,?,?) "
            "ON CONFLICT(day, user_id, screen) DO UPDATE SET views = views + excluded.views, seconds = seconds + excluded.seconds",
            (day, user_id, name, views, seconds),
        )
        _catalog(conn, "screen", name, name, version, now)


_known = set()


def _may_add(conn, kind, key):
    """Защита от разрастания каталога: подписи приходят с клиента, и кривая верстка (динамический id вместо стабильного) могла бы
    плодить тысячи «кнопок». Известные пропускаем без запроса; новые — пока в каталоге меньше MAX_CATALOG записей этого вида."""
    if (kind, key) in _known:
        return True
    row = conn.execute("SELECT 1 FROM ui_catalog WHERE kind=? AND key=?", (kind, key)).fetchone()
    if row is None:
        total = conn.execute("SELECT COUNT(*) FROM ui_catalog WHERE kind=?", (kind,)).fetchone()[0]
        if total >= MAX_CATALOG:
            return False
    if len(_known) < 20000:
        _known.add((kind, key))
    return True


def _catalog(conn, kind, key, screen, version, now):
    conn.execute(
        "INSERT INTO ui_catalog(kind, key, first_seen, last_seen, first_screen, first_version) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(kind, key) DO UPDATE SET last_seen = excluded.last_seen",
        (kind, key, now, now, screen or None, _label(version) or None),
    )


# ---------------------------------------------------------------------------------------------------------------- буфер: бот и API
# Бот и мидлвара API пишут в память, а в базу всё уходит одной транзакцией раз в API_FLUSH_SECONDS (и раз в минуту из планировщика):
# горячий путь запроса не ждёт записи.

_lock = threading.Lock()
_buffer = {}          # (день, маршрут, человек) -> [вызовов, 5xx, 4xx, сумма мс, максимум мс]
_bot_buffer = {}      # (день, человек, действие) -> сколько раз
_last_flush = [time.monotonic()]


def _due():
    return time.monotonic() - _last_flush[0] >= API_FLUSH_SECONDS


def bot_action(user_id, label):
    """Действие человека в боте (команда, кнопка, тип сообщения): считается как клик на «экране» bot. Текст сообщений не сохраняется."""
    label = _label(label)
    if not user_id or not label:
        return
    with _lock:
        key = (today_utc(), int(user_id), label)
        _bot_buffer[key] = _bot_buffer.get(key, 0) + 1
        due = _due()
    if due:
        flush()


def record_api(route, status, ms, user_id=0, day=None):
    """Один вызов маршрута (шаблон без id): складывается в память и уходит в базу пачкой."""
    key = (day or today_utc(), str(route)[:120], int(user_id or 0))
    with _lock:
        row = _buffer.setdefault(key, [0, 0, 0, 0, 0])
        row[0] += 1
        if status >= 500:
            row[1] += 1
        elif status >= 400:
            row[2] += 1
        row[3] += int(ms)
        row[4] = max(row[4], int(ms))
        due = _due()
    if due:
        flush()


def flush():
    """Сбросить накопленное в базу. Возвращает число записанных строк; при сбое данные этой пачки теряются (это только счётчики)."""
    with _lock:
        api_rows, bot_rows = dict(_buffer), dict(_bot_buffer)
        _buffer.clear()
        _bot_buffer.clear()
        _last_flush[0] = time.monotonic()
    if not api_rows and not bot_rows:
        return 0
    try:
        conn = connect()
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for (day, route, user_id), (calls, errors, client_errors, total_ms, max_ms) in api_rows.items():
                conn.execute(
                    "INSERT INTO api_usage(day, route, user_id, calls, errors, client_errors, total_ms, max_ms) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(day, route, user_id) DO UPDATE SET calls = calls + excluded.calls, errors = errors + excluded.errors, "
                    "client_errors = client_errors + excluded.client_errors, total_ms = total_ms + excluded.total_ms, "
                    "max_ms = MAX(max_ms, excluded.max_ms)",
                    (day, route, user_id, calls, errors, client_errors, total_ms, max_ms),
                )
                if _may_add(conn, "api", route):
                    _catalog(conn, "api", route, "", None, now)
            for (day, user_id, label), count in bot_rows.items():
                record_ui(conn, user_id, clicks=[[label, "bot", count]], day=day)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        _put_back(api_rows, bot_rows)         # база занята — не теряем пачку, попробуем со следующим сбросом
        return 0
    return len(api_rows) + len(bot_rows)


def _put_back(api_rows, bot_rows):
    """Вернуть несохранённые счётчики в буфер (с ограничением размера, чтобы долгий сбой базы не раздул память)."""
    with _lock:
        if len(_buffer) + len(api_rows) <= MAX_BUFFER_KEYS:
            for key, row in api_rows.items():
                mine = _buffer.setdefault(key, [0, 0, 0, 0, 0])
                mine[0] += row[0]; mine[1] += row[1]; mine[2] += row[2]; mine[3] += row[3]; mine[4] = max(mine[4], row[4])
        if len(_bot_buffer) + len(bot_rows) <= MAX_BUFFER_KEYS:
            for key, count in bot_rows.items():
                _bot_buffer[key] = _bot_buffer.get(key, 0) + count


def cleanup(days=180):
    """Раз в сутки убирает дневные счётчики старше `days` дней (каталог и итоги по версиям остаются): база не растёт бесконечно."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        conn = connect()
        try:
            removed = 0
            for table in ("ui_clicks", "ui_screens", "api_usage"):
                removed += conn.execute(f"DELETE FROM {table} WHERE day<?", (cutoff,)).rowcount
            conn.commit()
            return removed
        finally:
            conn.close()
    except Exception:
        return 0
