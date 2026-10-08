"""
Разметка и скрипт «Друзья / Напомнить друзьям» в Mini App: ничего из этого
не проверить без браузера, но потерю блока или переименованный оверлей
можно поймать заранее.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_markup_has_the_friends_card_the_sheet_and_the_setting():
    for needle in (
        'id="friendsCard"',
        'id="remindFriendsSheet"',
        'id="remindFriendsList"',
        'id="remindFriendsClose"',
        'id="remindFriendsBackdrop"',
        'id="friendNudgesToggle"',
    ):
        assert needle in INDEX, needle


def test_friends_card_sits_in_the_rating_tab_before_the_team_card():
    """По просьбе пользователя «Друзья» и «Групповой челлендж» поменяны местами."""
    rating = INDEX.index('data-tab="rating"')
    friends = INDEX.index('id="friendsCard"', rating)
    team = INDEX.index('id="teamCard"', rating)
    profile = INDEX.index('data-tab="profile"', rating)
    assert friends < team < profile


def test_completion_hook_schedules_the_remind_window():
    celebrate = APP_JS.index("async function celebrateHabitCompletion")
    body = APP_JS[celebrate:APP_JS.index("\n}\n", celebrate)]
    assert "scheduleRemindFriends(result.remind_friends)" in body


def test_blocking_overlay_ids_exist_in_the_page():
    """Окно друзей ждёт, пока уйдут перечисленные оверлеи — если один из
    id переименуют, окно начнёт наезжать на праздничный экран."""
    block = re.search(r"const REMIND_BLOCKING_OVERLAYS = \[(.*?)\];", APP_JS, re.S)
    assert block, "нет списка REMIND_BLOCKING_OVERLAYS"
    ids = re.findall(r'"(\w+)"', block.group(1))
    assert {"streakCelebrationOverlay", "doubleBonusOverlay", "levelupOverlay"} <= set(ids)
    for overlay_id in ids:
        assert f'id="{overlay_id}"' in INDEX, overlay_id


def test_error_codes_have_russian_and_english_text():
    for code in ("sender_not_done", "already_reminded", "already_done", "unavailable",
                 "not_friends", "delivery_failed"):
        assert len(re.findall(rf"\b{code}:", APP_JS)) >= 2, code


def test_invite_link_is_the_friend_link_and_shared_through_telegram():
    assert "start=friend_${state.user.telegram_id}" in APP_JS
    assert "telegramShareUrl(text, link)" in APP_JS[APP_JS.index("async function openFriendInvite"):]


def test_styles_for_every_new_block_exist():
    for selector in (".friends-card", ".friend-row", ".friend-row__btn", ".friend-row__tag",
                     ".friend-row__remove", ".remind-friends-card", ".feedback-actions--single"):
        assert selector in CSS, selector


def test_asset_versions_were_bumped_together_with_the_change():
    assert "style.css?v=20261009_PAINT_V52" in INDEX
    assert "app.js?v=20261009_PAINT_V59" in INDEX


# ---------------------------------------------------------------------------
# Подписки и профиль игрока
# ---------------------------------------------------------------------------

def test_profile_overlay_and_follow_list_live_outside_the_tab_panels():
    """Профиль открывается и из Рейтинга, и из карточки «Друзья», и из списков —
    поэтому он не может лежать внутри вкладки (её [hidden] спрятал бы его)."""
    for needle in ('id="userProfileOverlay"', 'id="userProfileBody"', 'id="userProfileClose"',
                   'id="followListSheet"', 'id="followList"', 'id="followTabs"'):
        assert needle in INDEX, needle
    overlay = INDEX.index('id="userProfileOverlay"')
    last_panel = INDEX.rindex('<section class="tab-panel"')
    assert overlay > last_panel, "оверлей профиля должен быть после всех вкладок"


def test_stats_visibility_setting_is_in_settings():
    assert 'id="statsVisibilityToggle"' in INDEX


def test_sharing_habits_with_friends_is_an_explicit_opt_in():
    assert 'id="shareHabitsToggle"' in INDEX
    assert "По умолчанию выключено" in INDEX and "Подписчикам они недоступны никогда" in INDEX
    assert "initShareHabitsToggle();" in APP_JS
    # Блок привычек в профиле рисуется только если сервер сказал «показываем».
    body = APP_JS[APP_JS.index("function renderUserProfile"):APP_JS.index("async function openUserProfile")]
    assert "x.habits_shared && Array.isArray(x.habits)" in body
    assert ".up-habit{" in CSS and ".up-goals{" in CSS


def test_season_leaderboard_rows_open_the_profile_too():
    season = APP_JS[APP_JS.index("function renderSeasonList"):APP_JS.index("function initRatingScopeSwitch")]
    assert 'data-profile-id="${Number(r.telegram_id)}"' in season
    handler = APP_JS[APP_JS.index("function initRatingActions"):]
    assert 'getElementById("seasonRatingList")?.addEventListener("click"' in handler


def test_rating_rows_open_the_profile():
    assert APP_JS.count('data-profile-id="${Number(r.telegram_id)}"') == 3  # подиум + список + сезон
    handler = APP_JS[APP_JS.index("function initRatingActions"):]
    assert "openUserProfile(Number(row.dataset.profileId))" in handler
    assert "openUserProfile(Number(card.dataset.profileId))" in handler


def test_profile_actions_cover_follow_block_and_report():
    body = APP_JS[APP_JS.index("function renderUserProfile"):APP_JS.index("async function openUserProfile")]
    for action in ('data-up="${a.act}"', 'data-up="report-toggle"', 'data-up="report-send"'):
        assert action in body, action
    assert 'rel.blocked_by_me ? "unblock" : "block"' in body
    relation = APP_JS[APP_JS.index("function relationAction"):APP_JS.index("function niceChartStep")]
    for label in ("Подписаться в ответ", "✓ Вы подписаны", "🤝 Друзья", "Разблокировать"):
        assert label in relation, label


def test_profile_sections_are_driven_by_the_server_not_guessed():
    body = APP_JS[APP_JS.index("function renderUserProfile"):APP_JS.index("async function openUserProfile")]
    for section in ('sections.has("chart")', 'sections.has("overview")',
                    'sections.has("achievements")', 'sections.has("extended")'):
        assert section in body, section


def test_new_error_codes_have_both_languages():
    for code in ("self", "blocked", "limit", "invalid_reason", "already_reported",
                 "invalid_kind", "invalid_visibility"):
        assert len(re.findall(rf"\b{code}:", APP_JS)) >= 2, code


def test_styles_for_profile_blocks_exist():
    for selector in (".up-overlay", ".up-hero", ".up-avatar", ".up-btn--primary", ".up-btn--following",
                     ".up-btn--friends", ".up-chart__line", ".up-legend", ".up-grid", ".up-lock",
                     ".friends-card__search", ".follow-tab", ".friend-row__btn--following"):
        assert selector in CSS, selector
