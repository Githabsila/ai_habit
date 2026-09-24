"""
Roadmap #13 — лиги (тиры) в рейтинге. Осознанно ограниченный масштаб: это
косметическая надстройка над уже существующим рейтингом (get_rating в
db/users.py) — тир считается на лету из total_xp, без отдельного сезонного
цикла/сброса (полноценные сезонные события — roadmap #9, отдельная крупная
фича, см. ответ пользователю про то, что вынесено за рамки этого пакета).
"""

LEAGUE_TIERS = [
    (0, "🥉 Бронза"),
    (500, "🥈 Серебро"),
    (2000, "🥇 Золото"),
    (5000, "💎 Платина"),
    (15000, "👑 Легенда"),
]


def get_league_tier(total_xp):
    total_xp = int(total_xp or 0)
    tier_name = LEAGUE_TIERS[0][1]
    for threshold, name in LEAGUE_TIERS:
        if total_xp >= threshold:
            tier_name = name
        else:
            break
    return tier_name


def get_league_progress(total_xp):
    """Для профиля — "до следующей лиги осталось N XP", либо None, если
    уже максимальный тир."""
    total_xp = int(total_xp or 0)
    for i, (threshold, name) in enumerate(LEAGUE_TIERS):
        if total_xp < threshold:
            prev_threshold = LEAGUE_TIERS[i - 1][0] if i > 0 else 0
            span = threshold - prev_threshold
            into = total_xp - prev_threshold
            return {
                "next_tier": name,
                "xp_needed": threshold - total_xp,
                "progress_pct": round(100 * into / span) if span else 0,
            }
    return None


# =====================================
# ЛИГИ РЕЙТИНГА (сегментированный лидерборд)
# =====================================
# В отличие от LEAGUE_TIERS выше (косметический бейдж прогресса по total_xp,
# один и тот же набор для всех, не группирует людей) — эти лиги определяют,
# КАКОЙ круг игроков конкретный пользователь видит в рейтинге ("рейтинг у
# каждого свой", как в Duolingo). Признак — streak, а не XP: тот же, что
# уже используется для сортировки рейтинга (streak DESC, xp DESC), и даёт
# нужный побочный эффект бесплатно, без отдельной системы "обнаружения
# ушедших": кто попробовал один раз и не вернулся, streak падает до 0 при
# ближайшем rollover (db/streak.py::rollover_user) и человек сам выпадает
# из всех лиг рейтинга, ничего специально удалять не нужно.
#
# Порог входа — 2 дня подряд (streak>=2), а не 1: одна отметка в первый
# день ещё не отличает того, кто останется, от того, кто в тот же день
# передумал. Ровно тот же порог работает и для вернувшегося после паузы —
# streak сбрасывается до 0 при пропуске, и человек снова появляется в
# рейтинге (лига "Новички") только после 2 дней новой серии.
RATING_LEAGUE_MIN_STREAK = 2

RATING_LEAGUES = [
    (2, 3, "🌱 Новички"),
    (4, 7, "🔥 Ученики"),
    (8, 13, "⚡ В темпе"),
    (14, 30, "🥈 Продвинутые"),
    (31, 60, "🥇 Мастера"),
    (61, None, "👑 Легенды"),
]


def get_rating_league(streak):
    """None, если streak < RATING_LEAGUE_MIN_STREAK — пользователь пока не
    попадает ни в одну лигу рейтинга."""
    streak = int(streak or 0)
    if streak < RATING_LEAGUE_MIN_STREAK:
        return None
    for index, (lo, hi, name) in enumerate(RATING_LEAGUES):
        if streak >= lo and (hi is None or streak <= hi):
            return {"index": index, "name": name, "min_streak": lo, "max_streak": hi}
    return None


def get_rating_league_for_viewer(streak):
    """Как get_rating_league, но для того, КАКУЮ лигу показать зрителю в
    интерфейсе рейтинга: тем, кто ещё не набрал streak>=2, показываем
    нижнюю лигу ("Новички") как предпросмотр цели — так же, как в
    Duolingo можно увидеть бронзовую лигу до первого урока — но зрителя
    самого в списке участников при этом не будет (см. db.users.get_rating)."""
    return get_rating_league(streak) or {
        "index": 0,
        "name": RATING_LEAGUES[0][2],
        "min_streak": RATING_LEAGUES[0][0],
        "max_streak": RATING_LEAGUES[0][1],
    }
