"""
Roadmap #17 (публичный шаринг-профиль) и #19 (реакции/стикеры поддержки
другу).
"""
from db import (
    add_user, set_public_profile_enabled, get_public_profile,
    send_reaction, get_recent_reactions_received, has_reacted_today, REACTION_EMOJIS,
)


# =====================================
# ПУБЛИЧНЫЙ ПРОФИЛЬ (roadmap #17)
# =====================================

def test_public_profile_none_by_default(uid):
    add_user(uid, "u", "Test")
    assert get_public_profile(uid) is None


def test_public_profile_none_for_unknown_user():
    assert get_public_profile(999999999999) is None


def test_enable_public_profile_exposes_safe_data(uid):
    add_user(uid, "u", "TestName")
    set_public_profile_enabled(uid, True)
    profile = get_public_profile(uid)
    assert profile is not None
    assert profile["first_name"] == "TestName"
    assert profile["telegram_id"] == uid
    assert "level" in profile and "streak" in profile and "league_tier" in profile
    # Приватные поля НЕ должны попадать в публичную витрину.
    assert "username" not in profile
    assert "xp" not in profile  # только через league_tier, не сырое число


def test_disable_public_profile_hides_it_again(uid):
    add_user(uid, "u", "Test")
    set_public_profile_enabled(uid, True)
    assert get_public_profile(uid) is not None
    set_public_profile_enabled(uid, False)
    assert get_public_profile(uid) is None


# =====================================
# РЕАКЦИИ ДРУЗЬЯМ (roadmap #19)
# =====================================

def test_send_reaction_succeeds(uid):
    add_user(uid, "u", "Test")
    target = uid + 1
    add_user(target, "u2", "Test2")
    assert send_reaction(uid, target, "🔥") is True
    reactions = get_recent_reactions_received(target)
    assert len(reactions) == 1
    assert reactions[0]["emoji"] == "🔥"


def test_send_reaction_rejects_unknown_emoji(uid):
    add_user(uid, "u", "Test")
    target = uid + 1
    add_user(target, "u2", "Test2")
    assert send_reaction(uid, target, "💩") is False


def test_send_reaction_rejects_self(uid):
    add_user(uid, "u", "Test")
    assert send_reaction(uid, uid, "🔥") is False


def test_send_reaction_once_per_day_per_pair(uid):
    add_user(uid, "u", "Test")
    target = uid + 1
    add_user(target, "u2", "Test2")
    assert send_reaction(uid, target, "🔥") is True
    assert send_reaction(uid, target, "💪") is False  # уже отправляли сегодня
    assert has_reacted_today(uid, target) is True


def test_different_senders_can_both_react_same_day(uid):
    # Большой сдвиг у sender1/sender2 — чтобы гарантированно не столкнуться
    # с telegram_id, который фикстура `uid` выдаст какому-то ДРУГОМУ тесту
    # в этом же прогоне (общая БД на весь тестовый файл, см. conftest.py).
    target = uid
    add_user(target, "target", "Target")
    sender1 = uid + 10_000_000
    sender2 = uid + 20_000_000
    add_user(sender1, "s1", "S1")
    add_user(sender2, "s2", "S2")
    before = len(get_recent_reactions_received(target))
    assert send_reaction(sender1, target, "🔥") is True
    assert send_reaction(sender2, target, "👏") is True
    assert len(get_recent_reactions_received(target)) == before + 2


def test_reaction_emojis_list_not_empty():
    assert len(REACTION_EMOJIS) >= 3
