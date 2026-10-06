"""
Эволюции ADAM и ранги — постоянный «облик» игрока, который видят друзья.

Состояние героя (db/hero.py) показывает, КАК СЕЙЧАС ДЕЛА У СЕРИИ: старт,
рост, пик, под угрозой, срыв. Эволюция — другое: это то, чего человек
достиг и что у него уже не отнять. Она считается по ЛУЧШЕЙ серии
(users.best_streak), поэтому срыв серии эволюции не отбирает — меняется
только сиюминутное состояние героя, а ранг остаётся и виден в профиле,
в рейтинге и в списке друзей.

    SPARK I–II      3 · 7 дней
    FLOW I–III      14 · 21 · 31 день
    CONTROL I–III   50 · 75 · 100 дней
    APEX I–III      180 · 270 · 365 дней
    LEGEND          500 дней

Всего 12 эволюций. Пороги подобраны так, чтобы первые приходили часто (3, 7,
14 дней — самый опасный отрезок для привычки), дальше — реже, и совпадали с
тем, что уже есть в приложении: 14 дней — первая рамка и полоса «Стабильный
рост» героя, 31 — «Пик формы» и лига «Мастера», 100 и 365 — рамки серии.

Праздничный экран «EVOLUTION 01 UNLOCKED» показывается один раз на каждую
новую эволюцию: seen_level хранит, какой уровень игрок уже видел. Для тех,
у кого эволюции уже были к моменту выхода функции, строка создаётся сразу
с текущим уровнем — без «догоняющих» праздников задним числом.
"""
from .core import connect

# (уровень, дней серии, ключ семейства, римская цифра внутри семейства)
_LADDER = (
    (1, 3, "spark", "I"),
    (2, 7, "spark", "II"),
    (3, 14, "flow", "I"),
    (4, 21, "flow", "II"),
    (5, 31, "flow", "III"),
    (6, 50, "control", "I"),
    (7, 75, "control", "II"),
    (8, 100, "control", "III"),
    (9, 180, "apex", "I"),
    (10, 270, "apex", "II"),
    (11, 365, "apex", "III"),
    (12, 500, "legend", ""),
)

# Название семейства (латиница — как в оформлении), русское пояснение, цвет
# (фронт красит значок и свечение героя по data-family / --evo-accent).
FAMILIES = {
    "spark": {"title": "SPARK", "ru": "Искра", "accent": "#5ad1ff"},
    "flow": {"title": "FLOW", "ru": "Поток", "accent": "#4da3ff"},
    "control": {"title": "CONTROL", "ru": "Контроль", "accent": "#8b8cff"},
    "apex": {"title": "APEX", "ru": "Вершина", "accent": "#ffc83d"},
    "legend": {"title": "LEGEND", "ru": "Легенда", "accent": "#ffe9a8"},
}

EVOLUTION_COUNT = len(_LADDER)


def _entry(level, days, family, numeral):
    meta = FAMILIES[family]
    name = f"{meta['title']} {numeral}".strip()
    return {
        "level": level,
        "days": days,
        "family": family,
        "family_ru": meta["ru"],
        "numeral": numeral,
        "name": name,
        "accent": meta["accent"],
    }


LADDER = tuple(_entry(*row) for row in _LADDER)
EVOLUTION_LADDER = LADDER


def level_for_days(days):
    """Сколько эволюций открыто при такой (лучшей) серии: 0…EVOLUTION_COUNT."""
    days = int(days or 0)
    level = 0
    for entry in LADDER:
        if days >= entry["days"]:
            level = entry["level"]
    return level


def rank_for_level(level):
    """Описание ранга по уровню эволюции; None для уровня 0 (ранга ещё нет)."""
    level = int(level or 0)
    if level <= 0:
        return None
    return LADDER[min(level, EVOLUTION_COUNT) - 1]


def evolution_view(best_streak, streak=None):
    """Состояние эволюции по цифрам. Ничего не читает из БД — этим пользуются
    и профиль другого игрока, и списки (друзья, рейтинг)."""
    best = max(int(best_streak or 0), int(streak or 0))
    current = int(streak if streak is not None else best)
    level = level_for_days(best)
    rank = rank_for_level(level)
    nxt = LADDER[level] if level < EVOLUTION_COUNT else None

    # Прогресс к следующей эволюции — по ТЕКУЩЕЙ серии: чтобы получить новую,
    # серию нужно набрать (после срыва — заново), старые эволюции при этом
    # остаются.
    percent = 100
    days_left = 0
    if nxt is not None:
        base = rank["days"] if rank else 0
        span = max(1, nxt["days"] - base)
        percent = max(0, min(100, round(100 * (current - base) / span)))
        days_left = max(0, nxt["days"] - current)

    return {
        "level": level,
        "total": EVOLUTION_COUNT,
        "name": rank["name"] if rank else None,
        "family": rank["family"] if rank else None,
        "family_ru": rank["family_ru"] if rank else None,
        "numeral": rank["numeral"] if rank else None,
        "accent": rank["accent"] if rank else None,
        "best_streak": best,
        "streak": current,
        "next": (
            {
                "level": nxt["level"],
                "name": nxt["name"],
                "family": nxt["family"],
                "days": nxt["days"],
                "days_left": days_left,
            }
            if nxt
            else None
        ),
        "percent": percent,
    }


def _load(conn, user_id):
    row = conn.execute(
        """SELECT u.streak, u.best_streak, e.seen_level
           FROM users u LEFT JOIN hero_evolution e ON e.user_id = u.telegram_id
           WHERE u.telegram_id=?""",
        (user_id,),
    ).fetchone()
    return row


def get_evolution(user_id):
    """Эволюция игрока для его собственного экрана: ранг, прогресс, лестница и
    pending — уровень, праздник которого ещё не показывали (None, если всё
    уже видел)."""
    conn = connect()
    try:
        row = _load(conn, user_id)
        if row is None:
            view = evolution_view(0, 0)
            seen = 0
        else:
            view = evolution_view(row["best_streak"], row["streak"])
            seen = row["seen_level"]
            if seen is None:
                # Первое обращение: всё, что уже есть, считается увиденным —
                # праздновать задним числом нечего.
                seen = view["level"]
                conn.execute(
                    "INSERT OR IGNORE INTO hero_evolution(user_id, seen_level) VALUES (?, ?)",
                    (user_id, seen),
                )
                conn.commit()
    finally:
        conn.close()
    view["seen_level"] = int(seen)
    view["pending"] = view["level"] if view["level"] > int(seen) else None
    view["ladder"] = [
        {k: e[k] for k in ("level", "days", "family", "numeral", "name", "accent")} for e in LADDER
    ]
    return view


def acknowledge_evolution(user_id, level):
    """Игрок увидел праздник эволюции `level`. Больше текущего уровня отметить
    нельзя — выдуманный уровень просто обрежется до реального."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 0
    conn = connect()
    try:
        row = _load(conn, user_id)
        if row is None:
            return None
        real = level_for_days(max(int(row["best_streak"] or 0), int(row["streak"] or 0)))
        target = max(0, min(level, real))
        conn.execute(
            "INSERT OR IGNORE INTO hero_evolution(user_id, seen_level) VALUES (?, 0)", (user_id,)
        )
        conn.execute(
            "UPDATE hero_evolution SET seen_level = MAX(seen_level, ?) WHERE user_id=?",
            (target, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_evolution(user_id)


def public_level(best_streak, streak=None):
    """Только уровень (0…12) — для строк списков."""
    return level_for_days(max(int(best_streak or 0), int(streak or 0)))
