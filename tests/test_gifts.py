"""
Подарки друзьям (db/gifts.py): алмазы и косметика, только друзьям, без
Adam Coin/очков, платит отправитель, не больше 3 в день.
"""
import asyncio
import itertools
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import db.gifts as gifts
import webapp.webapp_server as ws
from db import (
    add_user, get_user, has_item, make_friends, follow, block_user, get_gift_options, send_gift,
    toggle_reminders, MAX_GIFTS_PER_DAY, GIFT_DIAMOND_AMOUNTS,
)
from db.core import connect
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
_uid_counter = itertools.count(500_000_000, 10)

# Товары магазина (см. db/core.py): 5 — рамка Neon за 200, 4 — аватар за 250.
NEON, AVATAR = 5, 4


@pytest.fixture
def uid():
    return next(_uid_counter)


def _pair(uid, coins=1000, diamonds=10):
    """Два друга; у отправителя заданные монеты и алмазы."""
    sender, receiver = uid, uid + 1
    add_user(sender, f"u{sender}", "Дарящий")
    add_user(receiver, f"u{receiver}", "Получатель")
    assert make_friends(sender, receiver)
    _sql("UPDATE users SET xp=?, diamonds=? WHERE telegram_id=?", (coins, diamonds, sender))
    _sql("UPDATE users SET xp=0, diamonds=0 WHERE telegram_id=?", (receiver,))
    return sender, receiver


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# ЧТО МОЖНО ДАРИТЬ
# ---------------------------------------------------------------------------

def test_options_list_only_giftable_things(uid):
    sender, receiver = _pair(uid, coins=300, diamonds=2)
    options = get_gift_options(sender, receiver)
    ids = {it["id"] for it in options["items"]}
    assert ids == {2, 3, AVATAR, NEON, 6, 25, 26}            # косметика за монеты — и ничего больше
    assert not ids & {1, 7, 20, 21, 22, 23, 24, 27, 28}      # Premium, Stars, пакеты ИИ, бустеры, алмазные пакеты — нельзя
    assert options["balances"] == {"coins": 300, "diamonds": 2}
    assert [(d["amount"], d["affordable"]) for d in options["diamonds"]] == [(1, True), (3, False), (5, False)]
    neon = next(it for it in options["items"] if it["id"] == NEON)
    assert neon["price"] == 200 and neon["affordable"] is True and neon["owned_by_receiver"] is False
    assert options["left_today"] == options["max_per_day"] == MAX_GIFTS_PER_DAY == 3


def test_only_mutual_friends_can_gift(uid):
    a, b, c = uid, uid + 1, uid + 2
    for u in (a, b, c):
        add_user(u, f"u{u}", "Игрок")
    follow(a, b)                                              # односторонняя подписка — не друг
    assert send_gift(a, b, "diamonds", amount=1) == {"error": "not_friends"}
    assert get_gift_options(a, b) == {"error": "not_friends"}
    assert send_gift(a, a, "diamonds", amount=1) == {"error": "not_friends"}
    assert send_gift(a, c, "diamonds", amount=1) == {"error": "not_friends"}   # вообще не знакомы


def test_blocked_or_banned_friend_cannot_receive(uid):
    sender, receiver = _pair(uid)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (receiver,))
    assert send_gift(sender, receiver, "diamonds", amount=1) == {"error": "not_friends"}
    _sql("UPDATE users SET banned=0 WHERE telegram_id=?", (receiver,))
    block_user(receiver, sender)
    assert send_gift(sender, receiver, "diamonds", amount=1) == {"error": "not_friends"}


# ---------------------------------------------------------------------------
# АЛМАЗЫ
# ---------------------------------------------------------------------------

def test_diamond_gift_moves_diamonds_and_nothing_else(uid):
    sender, receiver = _pair(uid, coins=500, diamonds=5)
    before_sender, before_receiver = get_user(sender), get_user(receiver)
    result = send_gift(sender, receiver, "diamonds", amount=3)
    assert result["ok"] and result["gift"]["amount"] == 3 and result["left_today"] == 2
    after_sender, after_receiver = get_user(sender), get_user(receiver)
    assert after_sender["diamonds"] == 2 and after_receiver["diamonds"] == 3
    for field in ("xp", "total_xp", "level"):
        assert after_sender[field] == before_sender[field] and after_receiver[field] == before_receiver[field]


@pytest.mark.parametrize("amount", [0, 2, 4, 100, -1, "много", None])
def test_invalid_diamond_amounts_are_rejected(uid, amount):
    sender, receiver = _pair(uid)
    assert send_gift(sender, receiver, "diamonds", amount=amount) == {"error": "invalid_amount"}
    assert get_user(sender)["diamonds"] == 10


def test_not_enough_diamonds_changes_nothing(uid):
    sender, receiver = _pair(uid, diamonds=2)
    assert send_gift(sender, receiver, "diamonds", amount=3) == {"error": "not_enough_diamonds"}
    assert get_user(sender)["diamonds"] == 2 and get_user(receiver)["diamonds"] == 0
    assert gifts.gifts_left_today(sender) == 3


def test_unknown_kind_is_rejected(uid):
    sender, receiver = _pair(uid)
    assert send_gift(sender, receiver, "coins", amount=100) == {"error": "invalid_kind"}
    assert send_gift(sender, receiver, "xp", amount=100) == {"error": "invalid_kind"}
    assert send_gift(sender, receiver, None) == {"error": "invalid_kind"}


# ---------------------------------------------------------------------------
# ПРЕДМЕТЫ
# ---------------------------------------------------------------------------

def test_item_gift_costs_the_sender_coins_and_gives_the_item(uid):
    sender, receiver = _pair(uid, coins=500)
    before_sender, before_receiver = get_user(sender), get_user(receiver)
    result = send_gift(sender, receiver, "item", item_id=NEON)
    assert result["ok"] and result["gift"]["price"] == 200
    assert has_item(receiver, NEON) and not has_item(sender, NEON)
    after_sender, after_receiver = get_user(sender), get_user(receiver)
    assert after_sender["xp"] == 300                          # заплатил сам
    # Рейтинг и уровень не пострадали, получатель очков не получил.
    assert after_sender["total_xp"] == before_sender["total_xp"] and after_sender["level"] == before_sender["level"]
    assert after_receiver["xp"] == before_receiver["xp"] == 0
    assert after_receiver["total_xp"] == before_receiver["total_xp"]
    # Подарок не надевается сам: внешний вид получателя не меняется.
    assert after_receiver["frame_id"] == before_receiver["frame_id"]


def test_already_owned_item_is_not_charged(uid):
    sender, receiver = _pair(uid, coins=500)
    assert send_gift(sender, receiver, "item", item_id=NEON)["ok"]
    assert send_gift(sender, receiver, "item", item_id=NEON) == {"error": "already_owned"}
    assert get_user(sender)["xp"] == 300 and gifts.gifts_left_today(sender) == 2
    options = get_gift_options(sender, receiver)
    assert next(it for it in options["items"] if it["id"] == NEON)["owned_by_receiver"] is True


def test_not_enough_coins_changes_nothing(uid):
    sender, receiver = _pair(uid, coins=199)
    assert send_gift(sender, receiver, "item", item_id=NEON) == {"error": "not_enough_coins"}
    assert get_user(sender)["xp"] == 199 and not has_item(receiver, NEON)
    assert gifts.gifts_left_today(sender) == 3


@pytest.mark.parametrize("item_id", [1, 7, 20, 21, 22, 24, 27, 9999, "abc", None])
def test_only_cosmetics_can_be_gifted(uid, item_id):
    sender, receiver = _pair(uid, coins=5000)
    assert send_gift(sender, receiver, "item", item_id=item_id) == {"error": "item_not_giftable"}
    assert get_user(sender)["xp"] == 5000


# ---------------------------------------------------------------------------
# ЛИМИТ В ДЕНЬ
# ---------------------------------------------------------------------------

def test_daily_limit_blocks_the_fourth_gift_without_charging(uid):
    sender, receiver = _pair(uid, diamonds=10)
    for _ in range(MAX_GIFTS_PER_DAY):
        assert send_gift(sender, receiver, "diamonds", amount=1)["ok"]
    assert send_gift(sender, receiver, "diamonds", amount=1) == {"error": "daily_limit"}
    assert get_user(sender)["diamonds"] == 7 and get_user(receiver)["diamonds"] == 3
    assert send_gift(sender, receiver, "item", item_id=NEON) == {"error": "daily_limit"}
    assert get_user(sender)["xp"] == 1000 and not has_item(receiver, NEON)


def test_limit_resets_the_next_day(uid, monkeypatch):
    sender, receiver = _pair(uid, diamonds=10)
    for _ in range(MAX_GIFTS_PER_DAY):
        send_gift(sender, receiver, "diamonds", amount=1)
    monkeypatch.setattr(gifts, "_local_day", lambda user_id: "2099-01-01")
    assert gifts.gifts_left_today(sender) == 3
    assert send_gift(sender, receiver, "diamonds", amount=1)["ok"]


def test_parallel_gifts_cannot_beat_the_limit(uid):
    sender, receiver = _pair(uid, diamonds=20)
    # Строка часового пояса создаётся лениво при первом обращении; в живом
    # сервере запросы идут в одном потоке событий, а тут — 8 потоков, поэтому
    # заводим её заранее (иначе гонка за INSERT в streak_meta, не за подарок).
    gifts._local_day(sender)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: send_gift(sender, receiver, "diamonds", amount=1), range(8)))
    assert sum(1 for r in results if r.get("ok")) == MAX_GIFTS_PER_DAY
    assert get_user(sender)["diamonds"] == 17 and get_user(receiver)["diamonds"] == 3


def test_gifts_are_logged(uid):
    sender, receiver = _pair(uid)
    send_gift(sender, receiver, "diamonds", amount=5)
    send_gift(sender, receiver, "item", item_id=NEON)
    conn = connect()
    rows = conn.execute("SELECT kind, item_id, amount, price FROM gifts WHERE from_user_id=? ORDER BY id", (sender,)).fetchall()
    conn.close()
    assert [(r["kind"], r["item_id"], r["amount"], r["price"]) for r in rows] == [("diamonds", None, 5, 0), ("item", NEON, 1, 200)]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def _flush_background():
    tasks = list(ws._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def test_options_route(client, uid):
    sender, receiver = _pair(uid)
    data = await (await client.get(f"/api/gifts/options?to={receiver}", headers=_headers(sender))).json()
    assert data["to"]["first_name"] == "Получатель" and len(data["items"]) >= 4
    r = await client.get("/api/gifts/options?to=abc", headers=_headers(sender))
    assert r.status == 400 and (await r.json())["error"] == "invalid_target"
    stranger = uid + 2
    add_user(stranger, "s", "Чужой")
    r = await client.get(f"/api/gifts/options?to={stranger}", headers=_headers(sender))
    assert r.status == 400 and (await r.json())["error"] == "not_friends"


async def test_gift_route_returns_balances_and_pushes_the_receiver(client, uid):
    sender, receiver = _pair(uid, coins=500, diamonds=5)
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    r = await client.post(f"/api/users/{receiver}/gift", json={"kind": "diamonds", "amount": 3}, headers=_headers(sender))
    assert r.status == 200
    data = await r.json()
    assert data["gift"]["amount"] == 3 and data["left_today"] == 2 and data["user"]["diamonds"] == 2
    await _flush_background()
    (chat_id, text), _ = bot.send_message.call_args
    assert chat_id == receiver and "Дарящий" in text and "💎 3" in text

    bot.send_message.reset_mock()
    r = await client.post(f"/api/users/{receiver}/gift", json={"kind": "item", "item_id": NEON}, headers=_headers(sender))
    assert (await r.json())["user"]["xp"] == 300
    await _flush_background()
    text = bot.send_message.call_args.args[1]
    assert "Neon" in text and "Профиль → Настройки" in text


async def test_gift_push_escapes_the_name_and_respects_opt_out(client, uid):
    sender, receiver = _pair(uid, diamonds=5)
    _sql("UPDATE users SET first_name=? WHERE telegram_id=?", ("<b>Хакер</b>", sender))
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    await client.post(f"/api/users/{receiver}/gift", json={"kind": "diamonds", "amount": 1}, headers=_headers(sender))
    await _flush_background()
    assert "&lt;b&gt;Хакер&lt;/b&gt;" in bot.send_message.call_args.args[1]

    bot.send_message.reset_mock()
    toggle_reminders(receiver)                                # выключил напоминания → подарок дойдёт, пуша не будет
    r = await client.post(f"/api/users/{receiver}/gift", json={"kind": "diamonds", "amount": 1}, headers=_headers(sender))
    assert r.status == 200
    await _flush_background()
    bot.send_message.assert_not_called()
    assert get_user(receiver)["diamonds"] == 2


@pytest.mark.parametrize("body,error", [
    ({"kind": "coins", "amount": 100}, "invalid_kind"),
    ({"kind": "diamonds", "amount": 2}, "invalid_amount"),
    ({"kind": "item", "item_id": 20}, "item_not_giftable"),
])
async def test_gift_route_rejects_bad_requests(client, uid, body, error):
    sender, receiver = _pair(uid)
    r = await client.post(f"/api/users/{receiver}/gift", json=body, headers=_headers(sender))
    assert r.status == 400 and (await r.json())["error"] == error


async def test_gift_route_to_non_friend(client, uid):
    sender, stranger = uid, uid + 1
    add_user(sender, "a", "А")
    add_user(stranger, "b", "Б")
    r = await client.post(f"/api/users/{stranger}/gift", json={"kind": "diamonds", "amount": 1}, headers=_headers(sender))
    assert r.status == 400 and (await r.json())["error"] == "not_friends"
    assert (await client.post("/api/users/abc/gift", json={}, headers=_headers(sender))).status == 400


# ---------------------------------------------------------------------------
# РАЗМЕТКА И СКРИПТ
# ---------------------------------------------------------------------------

INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_gift_sheet_markup_lives_outside_the_tab_panels():
    for needle in ('id="giftSheet"', 'id="giftBalances"', 'id="giftList"', 'id="giftClose"', 'id="giftBackdrop"'):
        assert needle in INDEX, needle
    assert INDEX.index('id="giftSheet"') > INDEX.rindex('<section class="tab-panel"')


def test_gift_button_is_only_for_friends():
    body = APP_JS[APP_JS.index("function renderUserProfile"):APP_JS.index("async function openUserProfile")]
    assert 'data-up="gift"' in body
    assert "rel.friends" in body[body.index('data-up="gift"') - 200:body.index('data-up="gift"')]


def test_gift_script_uses_the_server_options_and_confirms_before_paying():
    assert '"/api/gifts/options?to="' in APP_JS or "/api/gifts/options?to=" in APP_JS
    assert "/gift`" in APP_JS or '/gift"' in APP_JS
    flow = APP_JS[APP_JS.index("async function sendGiftFromSheet"):]
    assert "confirm(" in flow[:600], "перед списанием нужно подтверждение"
    for code in ("daily_limit", "not_enough_coins", "not_enough_diamonds", "already_owned", "item_not_giftable"):
        assert len([m for m in APP_JS.split(f"{code}:") ]) - 1 >= 2, code


def test_gift_styles_exist():
    for selector in (".gift-card", ".gift-section", ".gift-row", ".gift-row__btn", ".gift-balances"):
        assert selector in CSS, selector
