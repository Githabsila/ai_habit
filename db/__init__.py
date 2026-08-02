

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
    update_ai_style,
    get_ai_style,
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
    get_weekly_summary,
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
    get_ai_feedback_stats,
    save_feedback_reason,
    get_recent_negative_reasons,
    get_user_profile,
    update_user_profile,
    bump_profile_counter,
    cache_get,
    cache_set,
    log_error,
    get_error_stats,
)

from .shop import (
    get_shop_items,
    buy_shop_item,
)



__all__ = [
    "DB_NAME", "connect", "create_tables",
    "add_user", "get_user", "get_users_count", "get_all_users", "get_all_users_info",
    "has_premium", "give_premium", "give_premium_admin",
    "add_xp", "give_xp_admin",
    "ban_user", "unban_user", "is_banned", "reset_progress",
    "set_referrer", "add_referral", "get_referrals",
    "get_rating", "get_user_rank", "claim_daily_bonus",
    "get_settings", "update_reminder_time", "update_ai_style", "get_ai_style",
    "add_habit", "get_habits", "get_habit", "edit_habit", "delete_habit",
    "reset_habits", "update_streak", "complete_habit", "get_progress",
    "add_statistics", "get_statistics", "get_weekly_summary",
    "create_daily_tasks", "get_daily_tasks", "update_daily_task",
    "check_achievements", "get_achievements",
    "update_calendar", "get_calendar",
    "add_ai_message", "get_ai_history", "clear_ai_history", "save_ai_feedback", "get_ai_feedback_stats",
    "save_feedback_reason", "get_recent_negative_reasons",
    "get_user_profile", "update_user_profile", "bump_profile_counter",
    "cache_get", "cache_set", "log_error", "get_error_stats",
    "get_shop_items", "buy_shop_item",
]
