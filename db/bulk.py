"""
Кэш на один проход рассылки (прогон 11.11, скорость): ежеминутные рассылки перебирают всех людей, и на каждого делали 2–4 отдельных
подключения к SQLite (часовой пояс, настройки) ещё до проверки «наступил ли его час» — на 2000 человек 4–10 секунд на одну рассылку,
а бот и Mini App работают в том же процессе и подвисали. Внутри `with preload():` часовые пояса и настройки всех людей читаются
двумя запросами, а get_timezone / get_settings берут их из памяти. Вне блока всё как раньше (каждый вызов — свежий запрос).
Кэш живёт только на время одного прохода, поэтому изменение настроек человеком видно уже на следующем тике.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .core import connect

_TZ = ContextVar("bulk_timezones", default=None)
_SETTINGS = ContextVar("bulk_settings", default=None)


@contextmanager
def preload():
    conn = connect()
    try:
        tz = {r["user_id"]: r["timezone"] for r in conn.execute("SELECT user_id, timezone FROM streak_meta").fetchall()}
        settings = {r["user_id"]: r for r in conn.execute("SELECT * FROM settings").fetchall()}
    finally:
        conn.close()
    t1, t2 = _TZ.set(tz), _SETTINGS.set(settings)
    try:
        yield
    finally:
        _TZ.reset(t1)
        _SETTINGS.reset(t2)


def cached_timezone(user_id):
    """(найдено, пояс) из кэша прохода; (False, None) — кэша нет или человека в нём нет (тогда читаем из базы как раньше)."""
    cache = _TZ.get()
    if cache is None or user_id not in cache:
        return False, None
    return True, cache[user_id]


def cached_settings(user_id):
    cache = _SETTINGS.get()
    if cache is None or user_id not in cache:
        return False, None
    return True, cache[user_id]


def preloaded(job):
    """Обёртка задания планировщика: весь проход — внутри preload()."""
    import functools

    @functools.wraps(job)
    async def run(*args, **kwargs):
        with preload():
            return await job(*args, **kwargs)
    return run
