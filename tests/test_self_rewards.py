"""
«Вознаградите себя»: атомарное списание Adam Coin (TOCTOU-safe, тот же
приём, что и db/shop.py::buy_shop_item) + история/статистика (db/self_rewards.py).
"""
from db import add_user, get_user, connect
from db.self_rewards import log_self_reward, get_self_reward_history, get_self_reward_stats, DEFAULT_COST


def _set_xp(telegram_id, amount):
    conn = connect()
    conn.execute("UPDATE users SET xp=? WHERE telegram_id=?", (amount, telegram_id))
    conn.commit()
    conn.close()


def test_log_self_reward_deducts_default_cost(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, 100)

    new_xp = log_self_reward(uid)

    assert new_xp == 100 - DEFAULT_COST
    assert get_user(uid)["xp"] == 100 - DEFAULT_COST


def test_log_self_reward_fails_without_enough_xp(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, DEFAULT_COST - 1)

    result = log_self_reward(uid)

    assert result is None
    assert get_user(uid)["xp"] == DEFAULT_COST - 1  # баланс не тронут


def test_log_self_reward_never_goes_negative_on_repeated_attempts(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, DEFAULT_COST)  # хватает ровно на одно вознаграждение

    first = log_self_reward(uid)
    second = log_self_reward(uid)

    assert first == 0
    assert second is None
    assert get_user(uid)["xp"] == 0


def test_log_self_reward_saves_note_and_history_order(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, 1000)

    log_self_reward(uid, note="Посмотрел сериал")
    log_self_reward(uid, note="  Сладкое  ")

    history = get_self_reward_history(uid)

    assert len(history) == 2
    assert history[0]["note"] == "Сладкое"  # самое новое — первым
    assert history[1]["note"] == "Посмотрел сериал"
    assert all(h["cost"] == DEFAULT_COST for h in history)


def test_log_self_reward_empty_note_stored_as_none(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, 1000)

    log_self_reward(uid, note="   ")

    history = get_self_reward_history(uid)
    assert history[0]["note"] is None


def test_get_self_reward_stats_counts_and_sums(uid):
    add_user(uid, "u", "Test")
    _set_xp(uid, 1000)

    log_self_reward(uid)
    log_self_reward(uid)
    log_self_reward(uid)

    stats = get_self_reward_stats(uid)

    assert stats["count"] == 3
    assert stats["total_cost"] == DEFAULT_COST * 3
    assert stats["days"] == 7


def test_get_self_reward_stats_empty_for_new_user(uid):
    add_user(uid, "u", "Test")

    stats = get_self_reward_stats(uid)

    assert stats["count"] == 0
    assert stats["total_cost"] == 0
