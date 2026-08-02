"""
Пакет db — модульная замена монолитного database.py.

Разбит по доменам:
    core          — подключение к БД и создание таблиц (DB_NAME здесь и только здесь)
    users         — пользователи, premium, Adam Coin, бан, рефералы, рейтинг, бонус
    settings      — настройки напоминаний
    habits        — привычки, серия (streak), выполнение, прогресс
    statistics    — ежедневная статистика
    daily_tasks   — ежедневные задания/квесты
    achievements  — достижения
    calendar      — внутренний календарь активности (не Google)
    ai            — история диалогов с ИИ-наставником и обратная связь
    shop          — магазин в боте
    google_tokens — хранилище токенов Google OAuth

Все имена, которые раньше импортировались как `from database import ...`,
доступны отсюда же: `from db import ...` — без изменения сигнатур.
"""

from .core import DB_NAME, connect, create_tables

from .users import (
    add_user,
    get_user,
    get_users_count,
    get_all_users,
    get_all_users_info,
    has_premium,
    give_premium,
    give_premium_admin,
    add_xp,
    give_xp_admin,
    ban_user,
    unban_user,
    is_banned,
    reset_progress,
    set_referrer,
    add_referral,
    get_referrals,
    get_rating,
    get_user_rank,
    claim_daily_bonus,
)

from .settings import (
    get_settings,
    update_reminder_time,
)

from .habits import (
    add_habit,
    get_habits,
    get_habit,
    edit_habit,
    delete_habit,
    reset_habits,
    update_streak,
    complete_habit,
    get_progress,
)

from .statistics import (
    add_statistics,
    get_statistics,
)

from .daily_tasks import (
    create_daily_tasks,
    get_daily_tasks,
    update_daily_task,
)

from .achievements import (
    check_achievements,
    get_achievements,
)

from .calendar import (
    update_calendar,
    get_calendar,
)

from .ai import (
    add_ai_message,
    get_ai_history,
    clear_ai_history,
    save_ai_feedback,
)

from .shop import (
    get_shop_items,
    buy_shop_item,
)

from .google_tokens import (
    save_google_tokens,
    get_google_tokens,
    update_google_access_token,
    save_google_event_id,
    delete_google_tokens,
    has_google_calendar,
)

__all__ = [
    "DB_NAME", "connect", "create_tables",
    "add_user", "get_user", "get_users_count", "get_all_users", "get_all_users_info",
    "has_premium", "give_premium", "give_premium_admin",
    "add_xp", "give_xp_admin",
    "ban_user", "unban_user", "is_banned", "reset_progress",
    "set_referrer", "add_referral", "get_referrals",
    "get_rating", "get_user_rank", "claim_daily_bonus",
    "get_settings", "update_reminder_time",
    "add_habit", "get_habits", "get_habit", "edit_habit", "delete_habit",
    "reset_habits", "update_streak", "complete_habit", "get_progress",
    "add_statistics", "get_statistics",
    "create_daily_tasks", "get_daily_tasks", "update_daily_task",
    "check_achievements", "get_achievements",
    "update_calendar", "get_calendar",
    "add_ai_message", "get_ai_history", "clear_ai_history", "save_ai_feedback",
    "get_shop_items", "buy_shop_item",
    "save_google_tokens", "get_google_tokens", "update_google_access_token",
    "save_google_event_id", "delete_google_tokens", "has_google_calendar",
]
