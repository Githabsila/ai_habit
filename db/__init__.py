

from .core import DB_NAME, DATA_DIR, DB_PATH, connect, create_tables

from .users import (
    add_user,
    get_user,
    get_users_count,
    get_all_users,
    get_all_users_info,
    has_premium,
    was_premium_purchased,
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
    get_last_ai_message_at,
    touch_last_ai_message,
)

from .settings import (
    get_settings,
    update_reminder_time,
    toggle_reminders,
    update_ai_style,
    get_ai_style,
    update_theme,
    get_theme,
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
    get_incomplete_habits,
    log_daily_habits,
    get_weekly_habit_breakdown,
    get_habits_needing_reminder,
    mark_habit_reminder_sent,
)

from .daily_plan import (
    get_daily_plan,
    save_daily_plan,
    set_daily_main_goal,
    delete_daily_main_goal,
    toggle_daily_main_goal,
    add_daily_task,
    update_daily_plan_task,
    delete_daily_task,
    toggle_daily_task,
    get_plan_tasks_needing_reminder,
    mark_plan_task_reminder_sent,
    get_plans_needing_goal_reminder,
    mark_goal_reminder_sent,
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
    get_proactive_topic,
    bump_profile_counter,
    claim_ai_first_message,
    cache_get,
    cache_set,
    log_error,
    get_error_stats,
)

from .shop import (
    get_shop_items,
    buy_shop_item,
    get_user_items,
    has_item,
    get_item_owner_ids,
    get_shop_item, set_cosmetic, add_ai_bonus_answers, get_ai_quota, consume_ai_answer,
)

from .onboarding import (
    get_access_status,
    set_access_status,
    get_pending_users,
    get_users_pending_since,
    get_access_status_counts,
    save_survey_answers,
    save_survey_analysis,
    get_survey,
    get_survey_tags,
    search_users_by_tag,
    get_users_by_tags,
    get_surveys_due_for_feedback,
    mark_feedback_sent,
    find_match_by_tags,
    save_milestones,
    get_milestones,
    toggle_milestone,
)



from .streak import (
    ensure_tables as ensure_streak_tables,
    get_streak_status, set_timezone, get_timezone,
    register_completion, rollover_user, rollover_all_users,
    buy_freeze, claim_weekly_reward, get_weekly_bonus_available,
    should_show_onboarding, onboarding_message, mark_onboarding_seen,
    RISK_15, RISK_23, claim_notification, release_notification, notification_scope, has_completed_today, get_streak_users, consume_completion_event,
    get_streak_reengagement_state, get_recent_streak_message_keys, record_streak_message_key, has_streak_frame,
    get_bonus_window, set_bonus_window,
)

from .task_praise import (
    get_secondary_task_praise_state,
    record_secondary_task_praise,
)


__all__ = [
    "DB_NAME", "DATA_DIR", "DB_PATH", "connect", "create_tables",
    "add_user", "get_user", "get_users_count", "get_all_users", "get_all_users_info",
    "has_premium", "was_premium_purchased", "give_premium", "give_premium_admin",
    "add_xp", "give_xp_admin",
    "ban_user", "unban_user", "is_banned", "reset_progress",
    "set_referrer", "add_referral", "get_referrals",
    "get_rating", "get_user_rank", "claim_daily_bonus",
    "get_last_ai_message_at", "touch_last_ai_message", "claim_ai_first_message",
    "get_settings", "update_reminder_time", "toggle_reminders", "update_ai_style", "get_ai_style",
    "add_habit", "get_habits", "get_habit", "edit_habit", "delete_habit",
    "reset_habits", "update_streak", "complete_habit", "get_progress",
    "get_incomplete_habits", "log_daily_habits", "get_weekly_habit_breakdown",
    "get_habits_needing_reminder", "mark_habit_reminder_sent",
    "get_daily_plan", "save_daily_plan", "set_daily_main_goal", "delete_daily_main_goal",
    "toggle_daily_main_goal", "add_daily_task", "update_daily_plan_task", "delete_daily_task",
    "toggle_daily_task",
    "get_plan_tasks_needing_reminder", "mark_plan_task_reminder_sent",
    "get_plans_needing_goal_reminder", "mark_goal_reminder_sent",
    "add_statistics", "get_statistics", "get_weekly_summary",
    "create_daily_tasks", "get_daily_tasks", "update_daily_task",
    "check_achievements", "get_achievements",
    "update_calendar", "get_calendar",
    "add_ai_message", "get_ai_history", "clear_ai_history", "save_ai_feedback", "get_ai_feedback_stats",
    "save_feedback_reason", "get_recent_negative_reasons",
    "get_user_profile", "update_user_profile", "get_proactive_topic", "bump_profile_counter",
    "cache_get", "cache_set", "log_error", "get_error_stats",
    "get_shop_items", "buy_shop_item", "get_user_items",
    "has_item", "get_item_owner_ids", "update_theme", "get_theme",
    "get_access_status", "set_access_status", "reject_user", "get_pending_users",
    "get_users_pending_since", "get_access_status_counts",
    "save_survey_answers", "save_survey_analysis", "get_survey", "get_survey_tags",
    "search_users_by_tag", "get_users_by_tags",
    "get_surveys_due_for_feedback", "mark_feedback_sent",
    "find_match_by_tags", "save_milestones", "get_milestones", "toggle_milestone",
    "ensure_streak_tables", "get_streak_status", "set_timezone", "get_timezone",
    "register_completion", "rollover_user", "rollover_all_users", "buy_freeze",
    "claim_weekly_reward", "get_weekly_bonus_available", "should_show_onboarding",
    "onboarding_message", "mark_onboarding_seen", "RISK_15", "RISK_23", "claim_notification", "release_notification", "notification_scope", "has_completed_today", "get_streak_users", "consume_completion_event",
    "get_streak_reengagement_state", "get_recent_streak_message_keys", "record_streak_message_key", "has_streak_frame",
    "get_bonus_window", "set_bonus_window",
    "get_secondary_task_praise_state", "record_secondary_task_praise",
]
