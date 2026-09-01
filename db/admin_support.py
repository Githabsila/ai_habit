"""
Roadmap #43 (сегментированная рассылка), #44 (карточка пользователя для
поддержки), #45 (авто-детект риска оттока) — все три для админ-панели
(webapp/routes_admin.py), сгруппированы в один модуль, потому что все
опираются на одну и ту же idea: "риск оттока" уже считается для
персональных уведомлений (db/streak.py::get_streak_reengagement_state),
здесь та же логика просто агрегируется по всем пользователям для админа
вместо одного конкретного.
"""
from .core import connect

# =====================================
# Roadmap #45 — риск оттока (агрегат по всем пользователям)
# =====================================
CHURN_RISK_TIERS = [
    ("healthy", 0, 0),
    ("watch", 1, 3),
    ("at_risk", 4, 7),
    ("churned", 8, 30),
    ("lost", 31, None),
]
CHURN_RISK_LABELS = {
    "healthy": "🟢 В порядке",
    "watch": "🟡 Присмотреться (1-3 дня)",
    "at_risk": "🟠 Риск (4-7 дней)",
    "churned": "🔴 Отток (8-30 дней)",
    "lost": "⚫ Потерян (31+ дней)",
}


def _churn_tier(inactive_days):
    for name, lo, hi in CHURN_RISK_TIERS:
        if hi is None:
            if inactive_days >= lo:
                return name
        elif lo <= inactive_days <= hi:
            return name
    return "healthy"


def get_churn_risk_report(limit_at_risk=20):
    """Сводка по риску оттока: сколько пользователей в каждом тире +
    список самых 'горящих' (дольше всех не заходят), для админ-панели.
    Использует ту же get_streak_reengagement_state(), что и персональные
    push-уведомления (db/streak_scheduler.py) — не отдельная, рассинхронизирующаяся
    логика "что считать оттоком"."""
    from .streak import get_streak_users, get_streak_reengagement_state
    from .users import get_user

    tiers = {name: 0 for name, _, _ in CHURN_RISK_TIERS}
    at_risk_list = []
    for uid in get_streak_users():
        state = get_streak_reengagement_state(uid)
        if not state["has_history"]:
            continue
        inactive = int(state["inactive_days"] or 0)
        tier = _churn_tier(inactive)
        tiers[tier] += 1
        if tier != "healthy":
            user = get_user(uid)
            if user is None or user["banned"]:
                continue
            at_risk_list.append({
                "telegram_id": uid,
                "first_name": user["first_name"],
                "inactive_days": inactive,
                "tier": tier,
                "tier_label": CHURN_RISK_LABELS[tier],
                "streak_before": state["streak"],
            })

    at_risk_list.sort(key=lambda r: r["inactive_days"], reverse=True)
    return {
        "tiers": {name: {"count": count, "label": CHURN_RISK_LABELS[name]} for name, count in tiers.items()},
        "at_risk": at_risk_list[:limit_at_risk],
    }


# =====================================
# Roadmap #43 — сегментированная рассылка
# =====================================
SEGMENT_LABELS = {
    "all": "Все пользователи",
    "premium": "С Premium",
    "no_premium": "Без Premium",
    "new_7d": "Новые за 7 дней",
    "inactive_7d": "Неактивны 7+ дней",
    "at_risk": "В зоне риска оттока (4+ дней)",
}


def get_users_by_segment(segment):
    """None — сегмент не распознан (вызывающий код сам решает, что делать,
    см. admin_broadcast_route: пустой/неизвестный segment откатывается на
    старое поведение с tag/all)."""
    if segment in (None, "", "all"):
        from .users import get_all_users
        return [u["telegram_id"] for u in get_all_users()]

    if segment in ("inactive_7d", "at_risk"):
        from .streak import get_streak_users, get_streak_reengagement_state
        threshold = 7 if segment == "inactive_7d" else 4
        ids = []
        for uid in get_streak_users():
            state = get_streak_reengagement_state(uid)
            if state["has_history"] and int(state["inactive_days"] or 0) >= threshold:
                ids.append(uid)
        return ids

    if segment not in ("premium", "no_premium", "new_7d"):
        return None

    conn = connect()
    cursor = conn.cursor()
    if segment == "premium":
        cursor.execute("SELECT telegram_id FROM users WHERE premium=1 AND banned=0")
    elif segment == "no_premium":
        cursor.execute("SELECT telegram_id FROM users WHERE premium=0 AND banned=0")
    else:  # new_7d
        cursor.execute("SELECT telegram_id FROM users WHERE created_at >= datetime('now','-7 days') AND banned=0")
    ids = [row["telegram_id"] for row in cursor.fetchall()]
    conn.close()
    return ids


# =====================================
# Roadmap #44 — карточка пользователя для поддержки
# =====================================
def get_user_support_card(user_id):
    """Консолидированная сводка по одному пользователю для поддержки —
    вместо того чтобы вручную смотреть в 4 разные таблицы, всё нужное
    для ответа человеку на одном экране."""
    from .users import get_user
    from .habits import get_habits
    from .subscription import get_subscription_status
    from .streak import get_streak_reengagement_state

    user = get_user(user_id)
    if user is None:
        return None

    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT day, habit_title, completed FROM habit_logs WHERE user_id=? ORDER BY day DESC LIMIT 10",
        (user_id,),
    )
    recent_logs = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as cnt FROM user_items WHERE user_id=?", (user_id,))
    purchases_row = cursor.fetchone()
    conn.close()

    habits = get_habits(user_id)
    reengagement = get_streak_reengagement_state(user_id)
    keys = user.keys()

    return {
        "telegram_id": user_id,
        "username": user["username"],
        "first_name": user["first_name"],
        "banned": bool(user["banned"]),
        "premium": bool(user["premium"]),
        "xp": user["xp"],
        "level": user["level"],
        "streak": user["streak"],
        "last_seen": str(user["last_seen"]) if "last_seen" in keys and user["last_seen"] else None,
        "created_at": str(user["created_at"]) if "created_at" in keys and user["created_at"] else None,
        "inactive_days": reengagement.get("inactive_days", 0),
        "habits": [
            {"title": h["title"], "completed": bool(h["completed"])}
            for h in habits
        ],
        "recent_logs": [
            {"day": r["day"], "title": r["habit_title"], "completed": bool(r["completed"])}
            for r in recent_logs
        ],
        "purchases_count": purchases_row["cnt"] if purchases_row else 0,
        "subscription": get_subscription_status(user_id),
    }
