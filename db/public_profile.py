"""
Roadmap #17 — публичный шаринг-профиль: карточка прогресса, которую можно
показать вне Telegram (обычная HTTPS-ссылка, без авторизации). Выключен по
умолчанию — включает только сам пользователь в настройках. Отдаём
намеренно узкий набор данных (никаких telegram username-only полей вроде
списка личных привычек/заметок) — это витрина достижений, не дамп профиля.
"""
from .core import connect


def set_public_profile_enabled(user_id, enabled):
    conn = connect()
    conn.execute(
        "UPDATE users SET public_profile_enabled=? WHERE telegram_id=?",
        (1 if enabled else 0, user_id),
    )
    conn.commit()
    conn.close()


def get_public_profile(user_id):
    """None если пользователь не найден или не включил публичный профиль."""
    from .achievements import get_achievements
    from .leagues import get_league_tier
    from .shop import has_item

    # id=3 в shop_items — фирменный "🏅" бейдж (см. db/core.py, отмечен
    # item_type='badge' при сидировании магазина). webapp_server.py уже
    # держит этот же id как локальную константу BADGE_ITEM_ID.
    BADGE_ITEM_ID = 3

    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user is None or not user["public_profile_enabled"]:
        return None

    achievements = get_achievements(user_id)
    return {
        "telegram_id": user_id,
        "first_name": user["first_name"] or "Игрок",
        "level": user["level"],
        "streak": user["streak"],
        "total_completed": user["total_completed"] if "total_completed" in user.keys() else 0,
        "league_tier": get_league_tier(user["total_xp"] if "total_xp" in user.keys() else user["xp"]),
        "avatar_id": user["avatar_id"] if "avatar_id" in user.keys() else "default",
        "frame_id": user["frame_id"] if "frame_id" in user.keys() else "default",
        "badge": has_item(user_id, BADGE_ITEM_ID),
        "achievements_count": len(achievements),
        "member_since": str(user["created_at"]) if "created_at" in user.keys() else None,
    }
