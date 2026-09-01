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
