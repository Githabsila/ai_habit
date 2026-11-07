"""
Автоудаление неактивных (юридический минимум, 06.11; db/inactive_purge.py): выключено без INACTIVE_PURGE_MONTHS; за 30 дней —
предупреждение; зашёл — сброс; прошёл срок после предупреждения — обезличивание; подписчиков и админов не трогаем.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import config
import db.inactive_purge as ip
from db import add_user, get_user
from db.core import connect

NOW = datetime(2027, 11, 6, 4, 40)


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _person(uid, last_seen):
    add_user(uid, "p", "Аня")
    _sql("UPDATE users SET last_seen=? WHERE telegram_id=?", (last_seen.strftime(ip.FMT), uid))


def test_off_by_default(uid, monkeypatch):
    monkeypatch.delenv("INACTIVE_PURGE_MONTHS", raising=False)
    _person(uid, NOW - timedelta(days=900))
    assert ip.candidates(NOW) == {"warn": [], "purge": []}


async def test_warn_then_purge_and_a_visit_resets(uid, monkeypatch):
    monkeypatch.setenv("INACTIVE_PURGE_MONTHS", "12")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    _person(uid, NOW - timedelta(days=340))                              # 12 мес. − 20 дней: пора предупредить
    plan = ip.candidates(NOW)
    assert uid in plan["warn"] and uid not in plan["purge"]
    bot = SimpleNamespace(send_message=AsyncMock())
    await ip.run_inactive_purge(bot, NOW)
    assert any(c.args[0] == uid and "удалю твои данные" in c.args[1] for c in bot.send_message.await_args_list)
    assert uid not in ip.candidates(NOW)["warn"], "предупреждаем один раз"

    later = NOW + timedelta(days=31)
    assert uid in ip.candidates(later)["purge"]
    await ip.run_inactive_purge(bot, later)
    user = get_user(uid)
    assert user["banned"] == 1 and user["first_name"] == "Удалённый пользователь"


def test_a_visit_after_the_warning_resets_it(uid, monkeypatch):
    monkeypatch.setenv("INACTIVE_PURGE_MONTHS", "12")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    _person(uid, NOW - timedelta(days=340))
    ip.mark_warned(uid, NOW)
    _sql("UPDATE users SET last_seen=? WHERE telegram_id=?", ((NOW + timedelta(days=1)).strftime(ip.FMT), uid))   # вернулся
    assert uid not in ip.candidates(NOW + timedelta(days=40))["purge"]


def test_subscribers_and_admins_are_never_purged(uid, monkeypatch):
    monkeypatch.setenv("INACTIVE_PURGE_MONTHS", "12")
    admin, payer = uid, uid + 1
    monkeypatch.setattr(config, "ADMIN_IDS", [admin])
    _person(admin, NOW - timedelta(days=900))
    _person(payer, NOW - timedelta(days=900))
    _sql("UPDATE users SET subscription_paid_until=datetime('now', '+5 days') WHERE telegram_id=?", (payer,))
    plan = ip.candidates(NOW)
    assert admin not in plan["warn"] + plan["purge"] and payer not in plan["warn"] + plan["purge"]


async def test_review_the_promised_30_days_pass_before_deletion(uid, monkeypatch):
    """Прогон 07.11, находка A4: давно неактивного (старше срока) предупреждали «через 30 дней удалю», а удаляли на следующий день."""
    monkeypatch.setenv("INACTIVE_PURGE_MONTHS", "12")
    monkeypatch.setattr(config, "ADMIN_IDS", [])
    uid = uid + 77_000                                                   # соседние тесты занимают uid+1 (подписчик)
    _person(uid, NOW - timedelta(days=400))
    bot = SimpleNamespace(send_message=AsyncMock())
    await ip.run_inactive_purge(bot, NOW)
    assert uid not in ip.candidates(NOW + timedelta(days=1))["purge"], "назавтра — ещё рано"
    assert uid not in ip.candidates(NOW + timedelta(days=29))["purge"]
    assert uid in ip.candidates(NOW + timedelta(days=31))["purge"], "через обещанные 30 дней — можно"
