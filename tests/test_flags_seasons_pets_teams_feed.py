"""
Roadmap #41 (feature flags), #9 (сезонные ивенты), #11 (виртуальный
питомец), #16 (групповые челленджи), #18 (лента активности друзей).
"""
from db import (
    add_user, add_habit, get_habits, complete_habit, add_xp,
    is_feature_enabled, set_feature_flag, get_all_flags, delete_feature_flag,
    get_season_leaderboard, get_season_rank, current_season_key, clear_season_leaderboard_cache,
    get_pet, feed_pet,
    create_team, join_team, leave_team, get_my_team,
    log_activity_event, get_friend_activity_feed,
    send_reaction,
)
from db.core import connect


# =====================================
# FEATURE FLAGS (roadmap #41)
# =====================================

def test_unknown_flag_disabled_by_default():
    assert is_feature_enabled("no_such_flag") is False


def test_set_and_check_flag_enabled():
    set_feature_flag("test_flag_a", True, rollout_pct=100)
    assert is_feature_enabled("test_flag_a") is True
    delete_feature_flag("test_flag_a")


def test_flag_disabled_returns_false_even_with_rollout(uid):
    set_feature_flag("test_flag_b", False, rollout_pct=100)
    assert is_feature_enabled("test_flag_b", uid) is False
    delete_feature_flag("test_flag_b")


def test_flag_rollout_zero_always_false(uid):
    set_feature_flag("test_flag_c", True, rollout_pct=0)
    assert is_feature_enabled("test_flag_c", uid) is False
    delete_feature_flag("test_flag_c")


def test_flag_rollout_deterministic_per_user(uid):
    set_feature_flag("test_flag_d", True, rollout_pct=50)
    first = is_feature_enabled("test_flag_d", uid)
    second = is_feature_enabled("test_flag_d", uid)
    assert first == second  # тот же пользователь — тот же результат
    delete_feature_flag("test_flag_d")


def test_get_all_flags_lists_created():
    set_feature_flag("test_flag_e", True, rollout_pct=100, description="desc")
    flags = get_all_flags()
    assert any(f["key"] == "test_flag_e" for f in flags)
    delete_feature_flag("test_flag_e")


# =====================================
# СЕЗОННЫЙ РЕЙТИНГ (roadmap #9)
# =====================================

def test_season_leaderboard_reflects_this_month_xp(uid):
    add_user(uid, "u", "Test")
    add_xp(uid, 50)
    conn = connect()
    conn.execute(
        "INSERT INTO statistics(user_id, completed, gained_xp, stat_date) VALUES (?,1,50,date('now'))",
        (uid,),
    )
    conn.commit()
    conn.close()
    # Лидерборд кэшируется на 30с на весь процесс (см. db/seasons.py) —
    # без сброса кэша этот тест мог бы увидеть устаревшие данные,
    # закэшированные КАКИМ-ТО другим тестом ранее в этом же прогоне.
    clear_season_leaderboard_cache()
    leaderboard = get_season_leaderboard(limit=100)
    entry = next((r for r in leaderboard if r["telegram_id"] == uid), None)
    assert entry is not None
    assert entry["season_xp"] >= 50


def test_season_rank_none_for_user_without_stats(uid):
    add_user(uid, "u", "Test")
    assert get_season_rank(uid) is None


def test_current_season_key_format():
    key = current_season_key()
    assert len(key) == 7 and key[4] == "-"


# =====================================
# ВИРТУАЛЬНЫЙ ПИТОМЕЦ (roadmap #11)
# =====================================

def test_get_pet_creates_egg_by_default(uid):
    add_user(uid, "u", "Test")
    pet = get_pet(uid)
    assert pet["care_points"] == 0
    assert pet["emoji"] == "🥚"


def test_feed_pet_increments_care_points(uid):
    add_user(uid, "u", "Test")
    result = feed_pet(uid, "2026-01-01")
    assert result["care_points"] == 1


def test_pet_evolves_at_threshold(uid):
    add_user(uid, "u", "Test")
    result = None
    for _ in range(10):
        result = feed_pet(uid, "2026-01-01")
    assert result["care_points"] == 10
    assert result["evolved"] is True
    assert result["emoji"] == "🐣"


def test_complete_habit_feeds_pet(uid):
    add_user(uid, "u", "Test")
    add_habit(uid, "Медитация")
    habit_id = get_habits(uid)[0]["id"]
    result = complete_habit(habit_id)
    assert result["pet"]["care_points"] == 1


# =====================================
# КОМАНДЫ (roadmap #16)
# =====================================

def test_create_team_generates_invite_code(uid):
    add_user(uid, "u", "Test")
    team = create_team(uid, "Утренние бегуны")
    assert team is not None
    assert len(team["invite_code"]) == 6


def test_creator_is_automatically_a_member(uid):
    add_user(uid, "u", "Test")
    team = create_team(uid, "Команда")
    my_team = get_my_team(uid)
    assert my_team["id"] == team["id"]
    assert len(my_team["members"]) == 1


def test_join_team_by_invite_code(uid):
    add_user(uid, "u", "Test")
    other = uid + 70_000_000
    add_user(other, "u2", "Other")
    team = create_team(uid, "Команда")
    result = join_team(other, team["invite_code"])
    assert result["id"] == team["id"]
    assert len(get_my_team(uid)["members"]) == 2


def test_join_unknown_code_returns_none(uid):
    add_user(uid, "u", "Test")
    assert join_team(uid, "ZZZZZZ") is None


def test_joining_new_team_leaves_old_one(uid):
    add_user(uid, "u", "Test")
    other = uid + 71_000_000
    add_user(other, "u2", "Other")
    team_a = create_team(uid, "A")
    team_b = create_team(other, "B")
    join_team(uid, team_b["invite_code"])
    assert get_my_team(uid)["id"] == team_b["id"]


def test_leave_team(uid):
    add_user(uid, "u", "Test")
    create_team(uid, "Команда")
    assert leave_team(uid) is True
    assert get_my_team(uid) is None


def test_team_week_total_sums_completions(uid):
    add_user(uid, "u", "Test")
    create_team(uid, "Команда")
    add_habit(uid, "Бег")
    complete_habit(get_habits(uid)[0]["id"])
    my_team = get_my_team(uid)
    assert my_team["team_week_total"] >= 1


# =====================================
# ЛЕНТА АКТИВНОСТИ (roadmap #18)
# =====================================

def test_feed_empty_without_friends(uid):
    add_user(uid, "u", "Test")
    assert get_friend_activity_feed(uid) == []


def test_feed_shows_team_member_events(uid):
    add_user(uid, "u", "Test")
    other = uid + 72_000_000
    add_user(other, "u2", "Other")
    team = create_team(uid, "Команда")
    join_team(other, team["invite_code"])

    log_activity_event(other, "achievement", {"detail": "Первый шаг"})

    feed = get_friend_activity_feed(uid)
    # join_team() сам логирует "joined_team" — плюс наше achievement выше.
    assert len(feed) == 2
    assert all(e["telegram_id"] == other for e in feed)
    assert any(e["detail"] == "Первый шаг" for e in feed)


def test_feed_shows_reaction_partner_events(uid):
    add_user(uid, "u", "Test")
    other = uid + 73_000_000
    add_user(other, "u2", "Other")
    send_reaction(uid, other, "🔥")

    log_activity_event(other, "level_up", {"detail": "5"})

    feed = get_friend_activity_feed(uid)
    assert any(e["telegram_id"] == other for e in feed)


def test_log_unknown_event_type_ignored(uid):
    add_user(uid, "u", "Test")
    log_activity_event(uid, "not_a_real_event")
    # Не должно упасть и не должно ничего создать — косвенно проверяем
    # через то, что лента для друга остаётся пустой.
    other = uid + 74_000_000
    add_user(other, "u2", "Other")
    send_reaction(other, uid, "🔥")
    assert get_friend_activity_feed(other) == []
