"""
Аватар-наставник ADAM («герой») вместо виртуального питомца-птенца.

Питомец (db/pets.py) рос от «очков заботы» — каждой отмеченной привычки — и
показывался эмодзи. Герой — это картинка наставника, и она зависит не от
накопленных очков, а от того, КАК СЕЙЧАС ДЕЛА У СЕРИИ пользователя:

    start       серия 0–1 (в том числе у нового пользователя)
    early       серия 2–13: первые дни прогресса
    growth      серия 14–30: стабильный рост
    peak        серия 31+: пик формы, золото
    at_risk     серия есть, но сегодня ещё не засчитана, и либо уже
                15:00+ (в это время уходит пуш RISK_15), либо вчерашний
                день спасла заморозка — «пропущен день / серия под угрозой»
    ended       серия только что оборвалась (последние FREE_RESTORE_GRACE_DAYS
                дней после срыва)
    long_break  серия оборвалась давно, а пользователь так и не вернулся
    return      пользователь снова закрыл привычку после срыва — серия = 1
                (на следующий день, пока привычка не закрыта, картинка уже
                «start», а при серии 2 — «early»)

Пороги серий намеренно совпадают с тем, что уже есть в приложении: 14 дней —
первая рамка-награда (db/streak.py::MILESTONES), 31+ — лига «Мастера»
(db/leagues.py::RATING_LEAGUES).

К каждому состоянию можно добавить необязательную видео-петлю
webapp/static/assets/hero/{ключ}.mp4 (готовить: tools/convert_hero_videos.py).
Если файл есть, в ответе появляется hero["video"] и фронт играет его поверх
картинки; если нет — просто картинка.
"""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import connect
from .streak import FREE_RESTORE_GRACE_DAYS, day_key, get_timezone

# Файлы героя лежат в webapp/static/assets/hero/: {ключ}.webp — картинка
# (обязательна), {ключ}.mp4 — необязательная видео-петля того же состояния.
# Если mp4 нет, hero["video"] = None и фронт показывает просто картинку.
HERO_ASSETS_DIR = Path(__file__).resolve().parent.parent / "webapp" / "static" / "assets" / "hero"

# Ручная «соль» версии — на случай, если нужно принудительно сбросить кэш
# всех файлов героя. Обычно трогать не нужно: в ?v=… уже входит размер файла,
# так что замена картинки/видео сбрасывает кэш Telegram WebView сама.
HERO_ASSET_VERSION = 1

# (минимальная серия, ключ) — «обычные» состояния по возрастанию серии.
HERO_BANDS = (
    (0, "start"),
    (2, "early"),
    (14, "growth"),
    (31, "peak"),
)

# Час локального времени, с которого незасчитанный сегодня день считается
# «серия под угрозой» — как RISK_15 в db/streak.py.
AT_RISK_HOUR = 15

# tone — подсказка фронту для цвета рамки/полоски (см. .hero-widget--* в
# style.css): calm — фирменный синий, gold — золото, warn — тревожный
# янтарь, dim — потухший серо-синий, hope — тёплое «рассветное» возвращение.
HERO_STATES = {
    "start": {
        "title": "Старт",
        "tone": "calm",
        "caption": "Всё только начинается. Закрой привычку — и огонь разгорится.",
    },
    "early": {
        "title": "Первые дни",
        "tone": "calm",
        "caption": "Хороший ход. Не сбавляй — огонь только разгорается.",
    },
    "growth": {
        "title": "Стабильный рост",
        "tone": "calm",
        "caption": "Ты в ритме. Серия держится, и я это вижу.",
    },
    "peak": {
        "title": "Пик формы",
        "tone": "gold",
        "caption": "Легендарная серия. Ты — пример для остальных.",
    },
    "at_risk": {
        "title": "Серия под угрозой",
        "tone": "warn",
        "caption": "Сегодня привычка ещё не отмечена — серия может погаснуть. Время есть.",
    },
    "ended": {
        "title": "Серия оборвалась",
        "tone": "dim",
        "caption": "Серия погасла. Ничего — начни заново сегодня.",
    },
    "long_break": {
        "title": "Долгий перерыв",
        "tone": "dim",
        "caption": "Давно тебя не было. Одна привычка — и мы снова в игре.",
    },
    "return": {
        "title": "С возвращением",
        "tone": "hope",
        "caption": "Ты снова в деле. Вернуться — уже половина дела.",
    },
}

HERO_KEYS = tuple(HERO_STATES)

_COUNTED = ("completed", "freeze")


def _asset_url(key, ext):
    """URL файла героя с версией (соль + размер файла) или None, если файла
    нет. Раздаётся с долгим кэшем (см. error_middleware), поэтому версия в
    ссылке обязательна — иначе замена файла не дошла бы до пользователей."""
    try:
        size = (HERO_ASSETS_DIR / f"{key}.{ext}").stat().st_size
    except OSError:
        return None
    return f"/static/assets/hero/{key}.{ext}?v={HERO_ASSET_VERSION}-{size}"


def _band_index(streak):
    index = 0
    for i, (minimum, _key) in enumerate(HERO_BANDS):
        if streak >= minimum:
            index = i
    return index


def _band_key(streak):
    return HERO_BANDS[_band_index(streak)][1]


def _band_progress(streak):
    """Прогресс до следующего «обычного» состояния — для полоски в карточке.
    На пике (последняя полоса) следующего нет: to=None, percent=100."""
    index = _band_index(streak)
    current_min = HERO_BANDS[index][0]
    if index + 1 >= len(HERO_BANDS):
        return {"from": current_min, "to": None, "days_left": 0, "next_title": None, "percent": 100}
    next_min, next_key = HERO_BANDS[index + 1]
    span = next_min - current_min
    return {
        "from": current_min,
        "to": next_min,
        "days_left": next_min - streak,
        "next_title": HERO_STATES[next_key]["title"],
        "percent": max(0, min(100, round(100 * (streak - current_min) / span))),
    }


def _load_history(user_id, today, yesterday):
    conn = connect()
    try:
        c = conn.cursor()
        c.execute("SELECT streak FROM users WHERE telegram_id=?", (user_id,))
        row = c.fetchone()
        streak = int(row["streak"] or 0) if row else 0

        c.execute(
            "SELECT day, status FROM streak_days WHERE user_id=? AND day IN (?, ?)",
            (user_id, day_key(today), day_key(yesterday)),
        )
        statuses = {r["day"]: r["status"] for r in c.fetchall()}

        c.execute(
            "SELECT MAX(day) AS last_day FROM streak_days WHERE user_id=? AND status='missed'",
            (user_id,),
        )
        last_missed = c.fetchone()["last_day"]

        c.execute(
            "SELECT 1 FROM streak_days WHERE user_id=? AND status IN ('completed','freeze','missed') LIMIT 1",
            (user_id,),
        )
        has_history = c.fetchone() is not None

        c.execute("SELECT last_broken_streak FROM streak_meta WHERE user_id=?", (user_id,))
        meta = c.fetchone()
        last_broken_streak = int(meta["last_broken_streak"] or 0) if meta else 0
    finally:
        conn.close()
    return {
        "streak": streak,
        "today_status": statuses.get(day_key(today)),
        "yesterday_status": statuses.get(day_key(yesterday)),
        "last_missed": last_missed,
        "has_history": has_history,
        "last_broken_streak": last_broken_streak,
    }


def get_hero_state(user_id, now=None):
    """Состояние героя для карточки в профиле. `now` — локальное время
    пользователя (в тестах подставляется явно, чтобы не зависеть от часов)."""
    now = now or datetime.now(ZoneInfo(get_timezone(user_id)))
    today = now.date()
    yesterday = date.fromordinal(today.toordinal() - 1)
    h = _load_history(user_id, today, yesterday)

    streak = h["streak"]
    counted_today = h["today_status"] in _COUNTED
    had_break = bool(h["last_missed"]) or h["last_broken_streak"] > 0
    caption = None

    if streak > 0:
        if counted_today:
            key = "return" if (streak == 1 and had_break) else _band_key(streak)
        else:
            saved_by_freeze = h["yesterday_status"] == "freeze"
            if saved_by_freeze or now.hour >= AT_RISK_HOUR:
                key = "at_risk"
                if saved_by_freeze:
                    caption = "Вчера день спасла заморозка. Закрой привычку сегодня, чтобы серия продолжилась."
            else:
                key = _band_key(streak)
    else:
        days_since_break = None
        if h["last_missed"]:
            try:
                days_since_break = (today - date.fromisoformat(h["last_missed"])).days
            except ValueError:
                days_since_break = None
        if days_since_break is not None and days_since_break <= FREE_RESTORE_GRACE_DAYS:
            key = "ended"
            if h["last_broken_streak"] > 0:
                caption = f"Серия в {h['last_broken_streak']} дн. погасла. Ничего — начни заново сегодня."
        elif h["has_history"]:
            key = "long_break"
        else:
            key = "start"

    meta = HERO_STATES[key]
    return {
        "key": key,
        "title": meta["title"],
        "caption": caption or meta["caption"],
        "tone": meta["tone"],
        "streak": streak,
        # Номер «обычной» полосы по серии (0 start … 3 peak) — фронт по росту
        # этого числа показывает тост «ADAM стал сильнее»; тревожные состояния
        # (at_risk/ended/…) на него не влияют.
        "band": _band_index(streak),
        "counted_today": counted_today,
        "image": _asset_url(key, "webp") or f"/static/assets/hero/{key}.webp?v={HERO_ASSET_VERSION}",
        "video": _asset_url(key, "mp4"),
        "progress": _band_progress(streak),
    }
