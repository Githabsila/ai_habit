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


def test_friends_card_sits_in_the_rating_tab_after_the_team_card():
    rating = INDEX.index('data-tab="rating"')
    team = INDEX.index('id="teamCard"', rating)
    friends = INDEX.index('id="friendsCard"', rating)
    profile = INDEX.index('data-tab="profile"', rating)
    assert team < friends < profile


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
    assert "https://t.me/share/url?url=" in APP_JS[APP_JS.index("async function openFriendInvite"):]


def test_styles_for_every_new_block_exist():
    for selector in (".friends-card", ".friend-row", ".friend-row__btn", ".friend-row__tag",
                     ".friend-row__remove", ".remind-friends-card", ".feedback-actions--single"):
        assert selector in CSS, selector


def test_asset_versions_were_bumped_together_with_the_change():
    assert "style.css?v=20261003_FRIENDS_V25" in INDEX
    assert "app.js?v=20261003_FRIENDS_V29" in INDEX
