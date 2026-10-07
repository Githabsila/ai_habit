"""
Подаренная вещь должна работать у получателя так же, как купленная: отражаться
в магазине («Надеть», а не «Купить») и реально надеваться.

Жалоба с телефона: подарили «Рамка: Пульс» — в «Мне подарили» запись есть, а в
магазине у получателя рамка по-прежнему «Купить»; надеть её тоже нельзя.
"""
import asyncio
import itertools
from pathlib import Path

import pytest

import db.core as core
from db import add_user, get_user, make_friends, send_gift
from db.core import connect
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
_uid_counter = itertools.count(520_000_000, 10)

# id товара → (frame_id/avatar_id, который он даёт)
FRAMES = {5: "neon", 6: "gold", 25: "rainbow", 26: "pulse_violet"}


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _pair(uid):
    sender, receiver = uid, uid + 1
    add_user(sender, f"u{sender}", "Дарящий")
    add_user(receiver, f"u{receiver}", "Получатель")
    assert make_friends(sender, receiver)
    conn = connect()
    conn.execute("UPDATE users SET xp=5000 WHERE telegram_id=?", (sender,))
    conn.execute("UPDATE users SET xp=0 WHERE telegram_id=?", (receiver,))
    conn.commit()
    conn.close()
    return sender, receiver


@pytest.mark.parametrize("item_id,frame_id", sorted(FRAMES.items()))
async def test_gifted_frame_shows_as_owned_and_can_be_equipped(client, uid, item_id, frame_id):
    sender, receiver = _pair(uid)
    assert send_gift(sender, receiver, "item", item_id=item_id)["ok"]

    shop = await (await client.get("/api/shop", headers=_headers(receiver))).json()
    item = next(it for it in shop["items"] if it["id"] == item_id)
    assert item["owned"] is True and item["item_type"] == "frame" and item["payload"] == frame_id

    secondary = await (await client.get("/api/bootstrap-secondary?section=profile", headers=_headers(receiver))).json()
    assert next(it for it in secondary["shop_items"] if it["id"] == item_id)["owned"] is True

    r = await client.post("/api/cosmetics/equip", json={"frame_id": frame_id}, headers=_headers(receiver))
    assert r.status == 200, await r.text()
    assert (await r.json())["frame_id"] == frame_id
    assert get_user(receiver)["frame_id"] == frame_id


@pytest.mark.parametrize("frame_id", ["rainbow", "pulse_violet", "neon", "gold"])
async def test_frame_that_was_not_bought_or_gifted_cannot_be_equipped(client, uid, frame_id):
    _, stranger = _pair(uid)
    r = await client.post("/api/cosmetics/equip", json={"frame_id": frame_id}, headers=_headers(stranger))
    assert r.status == 403 and (await r.json())["error"] == "frame_not_owned"
    assert get_user(stranger)["frame_id"] in (None, "", "default")


async def test_gifted_avatar_can_be_equipped(client, uid):
    sender, receiver = _pair(uid)
    assert send_gift(sender, receiver, "item", item_id=4)["ok"]
    r = await client.post("/api/cosmetics/equip", json={"avatar_id": "adam"}, headers=_headers(receiver))
    assert r.status == 200 and (await r.json())["avatar_id"] == "adam"


async def test_frame_bought_in_the_shop_can_be_switched_away_and_back(client, uid):
    """Покупка сразу надевает рамку; вернуть её после другой рамки тоже надо мочь
    (у «Пульса» и «Радуги» «Надеть» раньше отвечало frame_not_owned)."""
    sender, buyer = _pair(uid)
    conn = connect()
    conn.execute("UPDATE users SET xp=1000 WHERE telegram_id=?", (buyer,))
    conn.commit()
    conn.close()
    assert (await client.post("/api/buy/26", headers=_headers(buyer))).status == 200
    assert get_user(buyer)["frame_id"] == "pulse_violet"
    assert send_gift(sender, buyer, "item", item_id=25)["ok"]
    for frame_id in ("rainbow", "pulse_violet", "rainbow"):
        r = await client.post("/api/cosmetics/equip", json={"frame_id": frame_id}, headers=_headers(buyer))
        assert r.status == 200 and get_user(buyer)["frame_id"] == frame_id


async def test_streak_and_paid_frames_keep_their_own_rules(client, uid):
    _, user = _pair(uid)
    r = await client.post("/api/cosmetics/equip", json={"frame_id": "streak_14"}, headers=_headers(user))
    assert r.status == 403
    r = await client.post("/api/cosmetics/equip", json={"frame_id": "paid_double_gold"}, headers=_headers(user))
    assert r.status == 403
    conn = connect()
    conn.execute("UPDATE users SET frame_id='paid_double_gold' WHERE telegram_id=?", (user,))
    conn.commit()
    conn.close()
    r = await client.post("/api/cosmetics/equip", json={"frame_id": "paid_double_gold"}, headers=_headers(user))
    assert r.status == 200


async def test_gift_push_tells_where_to_wear_the_frame(client, uid):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import webapp.webapp_server as ws
    sender, receiver = _pair(uid)
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    r = await client.post(f"/api/users/{receiver}/gift", json={"kind": "item", "item_id": 26}, headers=_headers(sender))
    assert r.status == 200
    await asyncio.gather(*list(ws._background_tasks))
    text = bot.send_message.call_args.args[1]
    assert "Пульс" in text and "ADAM Store" in text and "Настройки" not in text


def test_fresh_database_knows_which_shop_items_are_frames():
    """Свежая БД раньше заводила Neon/Gold/аватар с типом «cosmetic» и пустым payload."""
    conn = connect()
    rows = {r["id"]: (r["item_type"], r["payload"]) for r in conn.execute(
        "SELECT id, item_type, payload FROM shop_items WHERE id IN (4, 5, 6, 25, 26)")}
    conn.close()
    assert rows == {4: ("avatar", "adam"), 5: ("frame", "neon"), 6: ("frame", "gold"),
                    25: ("frame", "rainbow"), 26: ("frame", "pulse_violet")}


# ---------------------------------------------------------------------------
# КЛИЕНТ: после подарка и после возврата в приложение магазин должен быть свежим
# ---------------------------------------------------------------------------

def _body(name):
    start = APP_JS.index(name)
    return APP_JS[start:start + 1800]


def test_bootstrap_refresh_keeps_the_shop_data():
    """state заменяется целиком при каждом /api/bootstrap, а магазин приходит отдельно:
    без переноса купленные/подаренные рамки в выборе превращались в «Не куплена»."""
    load = _body("async function loadBootstrap()")
    assert "carrySecondaryData(previousState, state)" in load
    carry = _body("function carrySecondaryData")
    for key in ("shop_items", "achievements", "leaderboard", "rating_league", "calendar_events"):
        assert key in APP_JS[APP_JS.index("const SECONDARY_STATE_KEYS"):][:200], key
    assert "next[key] === undefined" in carry


def test_returning_to_the_app_and_opening_new_gifts_reload_the_profile_data():
    invalidate = _body("function invalidateProfileData")
    assert 'secondaryLoaded.delete("profile")' in invalidate
    assert 'loadBootstrapSecondary("profile")' in invalidate
    resume = APP_JS[APP_JS.index("wasHiddenMs < 15000"):][:300]
    assert "loadBootstrap().then(invalidateProfileData)" in resume
    opened = _body("async function openReceivedGifts")
    seen = opened[opened.index('"/api/gifts/seen"'):]
    assert ".then(() => loadBootstrap())" in seen and ".then(invalidateProfileData)" in seen
    assert seen.index("loadBootstrap()") > seen.index('"/api/gifts/seen"'), "сначала отметка просмотра, потом свежий bootstrap"
