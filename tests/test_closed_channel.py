"""
Закрытый канал (решение владельца 05.11): вход — оплата + серия 3 дня; серия сгорела или подписка кончилась — бот убирает из канала;
вернуться — с серией 5 дней. Без CLOSED_CHANNEL_ID ничего не происходит. Тексты не рассказывают, что в канале (решение 05.11).
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import channel_sync
import config
from db import add_user, get_user
from db.core import connect
from db.subscription import get_subscription_status, record_subscription_payment, try_grant_channel_access

CHANNEL = "-1001234567890"


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _bot():
    return SimpleNamespace(
        create_chat_invite_link=AsyncMock(return_value=SimpleNamespace(invite_link="https://t.me/+abc")),
        ban_chat_member=AsyncMock(), unban_chat_member=AsyncMock(), send_message=AsyncMock(), revoke_chat_invite_link=AsyncMock(),
    )


@pytest.fixture
def channel(monkeypatch):
    monkeypatch.setattr(config, "CLOSED_CHANNEL_ID", CHANNEL)
    monkeypatch.setattr(config, "SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL", 3)
    monkeypatch.setattr(config, "CHANNEL_REENTRY_STREAK_DAYS", 5)
    monkeypatch.setattr(channel_sync, "_members", lambda: [r for r in _all_members()])
    return CHANNEL


def _all_members():
    conn = connect()
    try:
        return [r["telegram_id"] for r in conn.execute("SELECT telegram_id FROM users WHERE channel_access_granted_at IS NOT NULL")]
    finally:
        conn.close()


def _paid(user_id, streak):
    add_user(user_id, "u", "Аня")
    record_subscription_payment(user_id)
    _sql("UPDATE users SET streak=? WHERE telegram_id=?", (streak, user_id))


async def test_paid_with_a_3_day_streak_gets_the_link_and_2_days_is_not_enough(uid, channel):
    _paid(uid, 2)
    bot = _bot()
    assert await try_grant_channel_access(bot, uid) is None
    _sql("UPDATE users SET streak=3 WHERE telegram_id=?", (uid,))
    assert await try_grant_channel_access(bot, uid) == "https://t.me/+abc"
    assert bot.create_chat_invite_link.await_args.kwargs["creates_join_request"] is True, "личная ссылка-заявка (прогон 07.11)"
    assert await try_grant_channel_access(bot, uid) is None, "ссылка выдаётся один раз"


async def test_a_burnt_streak_removes_from_the_channel_and_return_needs_5_days(uid, channel):
    _paid(uid, 3)
    bot = _bot()
    await try_grant_channel_access(bot, uid)
    _sql("UPDATE users SET streak=0 WHERE telegram_id=?", (uid,))
    assert await channel_sync.run_channel_sync(bot) >= 1
    assert bot.ban_chat_member.await_args.kwargs == {"chat_id": CHANNEL, "user_id": uid}
    assert bot.unban_chat_member.await_args.kwargs["only_if_banned"] is True, "не вечный бан — вернуться можно"
    text = bot.send_message.await_args.args[1]
    assert "Серия погасла" in text and "5 дней" in text
    assert get_user(uid)["channel_access_granted_at"] is None and get_user(uid)["channel_removed_at"]

    assert get_subscription_status(uid)["streak_needed_for_channel"] == 5
    _sql("UPDATE users SET streak=4 WHERE telegram_id=?", (uid,))
    assert await try_grant_channel_access(bot, uid) is None
    _sql("UPDATE users SET streak=5 WHERE telegram_id=?", (uid,))
    assert await try_grant_channel_access(bot, uid) == "https://t.me/+abc"


async def test_an_ended_subscription_removes_from_the_channel(uid, channel):
    _paid(uid, 4)
    bot = _bot()
    await try_grant_channel_access(bot, uid)
    _sql("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ((datetime.utcnow() - timedelta(hours=1)).isoformat(), uid))
    await channel_sync.run_channel_sync(bot)
    assert "Подписка закончилась" in bot.send_message.await_args.args[1]
    assert get_user(uid)["channel_access_granted_at"] is None


async def test_members_in_good_standing_stay(uid, channel):
    _paid(uid, 6)
    bot = _bot()
    await try_grant_channel_access(bot, uid)
    bot.ban_chat_member.reset_mock()
    await channel_sync.run_channel_sync(bot)
    assert all(c.kwargs.get("user_id") != uid for c in bot.ban_chat_member.await_args_list)


async def test_without_a_channel_nothing_happens(uid, monkeypatch):
    monkeypatch.setattr(config, "CLOSED_CHANNEL_ID", "")
    _paid(uid, 5)
    bot = _bot()
    assert await try_grant_channel_access(bot, uid) is None
    assert await channel_sync.run_channel_sync(bot) == 0
    assert bot.create_chat_invite_link.await_count == 0 and bot.ban_chat_member.await_count == 0


def test_removal_texts_do_not_describe_the_channel_contents():
    for text in channel_sync.REMOVED_TEXT.values():
        assert "мышлен" not in text and "мировоззр" not in text and "контент" not in text


# ---------------------------------------------------------------------------------------------------------------- прогон 07.11

def _bot_join():
    bot = _bot()
    bot.approve_chat_join_request = AsyncMock()
    bot.decline_chat_join_request = AsyncMock()
    bot.revoke_chat_invite_link = AsyncMock()
    return bot


def _request(user_id, link_name):
    return SimpleNamespace(chat=SimpleNamespace(id=int(CHANNEL)), from_user=SimpleNamespace(id=user_id),
                           invite_link=SimpleNamespace(name=link_name, invite_link="https://t.me/+abc"))


async def test_review_the_link_lets_in_only_its_owner_while_access_holds(uid, channel):
    """Прогон 07.11, находка A1: ссылка в канал была ничьей — её можно было отдать другу (друг оставался навсегда) или потратить уже после
    конца подписки. Теперь ссылка — заявка, бот одобряет её только получателю и только пока доступ действует."""
    from handlers.channel_join import on_join_request
    _paid(uid, 3)
    bot = _bot_join()
    assert await try_grant_channel_access(bot, uid) == "https://t.me/+abc"
    kwargs = bot.create_chat_invite_link.await_args.kwargs
    assert kwargs["creates_join_request"] is True and kwargs["name"] == f"user_{uid}" and "member_limit" not in kwargs
    await on_join_request(_request(uid + 1, f"user_{uid}"), bot)              # друг с чужой ссылкой
    bot.decline_chat_join_request.assert_awaited_once()
    await on_join_request(_request(uid, f"user_{uid}"), bot)                  # сам получатель
    bot.approve_chat_join_request.assert_awaited_once()


async def test_review_after_removal_the_old_link_is_revoked_and_refused(uid, channel):
    from handlers.channel_join import on_join_request
    _paid(uid, 3)
    bot = _bot_join()
    await try_grant_channel_access(bot, uid)
    _sql("UPDATE users SET subscription_paid_until=? WHERE telegram_id=?", ((datetime.utcnow() - timedelta(days=3)).isoformat(), uid))
    await channel_sync.run_channel_sync(bot)
    bot.revoke_chat_invite_link.assert_awaited_once()
    await on_join_request(_request(uid, f"user_{uid}"), bot)
    assert bot.approve_chat_join_request.await_count == 0 and bot.decline_chat_join_request.await_count == 1


async def test_review_parallel_grants_make_one_link(uid, channel):
    """Прогон 07.11, находка A3: проверка «уже выдано» и отметка разделены await — параллельные отметки давали несколько ссылок."""
    import asyncio
    _paid(uid, 3)
    bot = _bot_join()

    async def slow_link(**kwargs):
        await asyncio.sleep(0.01)                                            # как настоящий запрос к Telegram: уступает очередь
        return SimpleNamespace(invite_link="https://t.me/+abc")

    bot.create_chat_invite_link = AsyncMock(side_effect=slow_link)
    results = await asyncio.gather(*[try_grant_channel_access(bot, uid) for _ in range(5)])
    assert bot.create_chat_invite_link.await_count == 1 and sum(1 for r in results if r) == 1
