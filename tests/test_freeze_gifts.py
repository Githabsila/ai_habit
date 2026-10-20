"""
Подарок-заморозка другу (20.10, как взаимопомощь в Duolingo): когда у друга кончились заморозки, в карточке «Друзья» справа от его имени
появляется серая снежинка; тап открывает окно «Надо помочь другу!» — отправить одну за 200 монет или «Нет, спасибо». У кого сколько заморозок —
видно в списке (синяя снежинка с цифрой). Правила подарка — db/gifts.py, «кончились» — db/streak.py (freeze_empty_since). Браузера в тестах
нет: правила, маршруты и учёт проверяем по-настоящему, окно — по тексту разметки и скрипта.
"""
import itertools
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from db import add_user
from db import gifts, streak
from db.core import connect
from db.follows import make_friends
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(856_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _pair(uid_, coins=1000, friend_streak=10, balance=0, empty="2026-10-19", mutual=True):
    """Отправитель с монетами и друг с серией и запасом заморозок; возвращает (отправитель, друг)."""
    sender, friend = uid_, uid_ + 1
    add_user(sender, f"s{sender}", "Отправитель")
    add_user(friend, f"f{friend}", "Мария")
    conn = connect()
    conn.execute("UPDATE users SET xp=? WHERE telegram_id=?", (coins, sender))
    conn.execute("UPDATE users SET streak=?, best_streak=? WHERE telegram_id=?", (friend_streak, friend_streak, friend))
    conn.execute("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (friend,))
    conn.execute("UPDATE streak_meta SET freeze_balance=?, freeze_empty_since=? WHERE user_id=?", (balance, empty, friend))
    conn.commit()
    conn.close()
    if mutual:
        make_friends(sender, friend)
    return sender, friend


def _row(sql, args):
    conn = connect()
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- правила подарка

def test_the_gift_price_and_the_cap_match_the_shop():
    assert gifts.FREEZE_GIFT_PRICE == streak.FREEZE_PRICE == 200
    assert gifts.MAX_FREEZE_BALANCE == streak.MAX_FREEZE_BALANCE == 2


def test_a_freeze_gift_costs_the_sender_coins_and_gives_the_friend_one_freeze(uid):
    sender, friend = _pair(uid)
    result = gifts.send_gift(sender, friend, "freeze")
    assert result["ok"] and result["gift"] == {"kind": "freeze", "label": "Заморозка серии", "price": 200, "amount": 1, "item_id": None}
    assert _row("SELECT xp, total_xp FROM users WHERE telegram_id=?", (sender,))["xp"] == 800
    meta = _row("SELECT freeze_balance, freeze_empty_since FROM streak_meta WHERE user_id=?", (friend,))
    assert meta["freeze_balance"] == 1 and meta["freeze_empty_since"] is None, "заморозка появилась — «кончились» сбрасывается"
    saved = _row("SELECT kind, price, amount FROM gifts WHERE from_user_id=? AND to_user_id=?", (sender, friend))
    assert (saved["kind"], saved["price"], saved["amount"]) == ("freeze", 200, 1)


def test_the_gift_does_not_touch_levels_or_the_leaderboard_points(uid):
    sender, friend = _pair(uid)
    before = _row("SELECT total_xp, level FROM users WHERE telegram_id=?", (sender,))
    gifts.send_gift(sender, friend, "freeze")
    after = _row("SELECT total_xp, level FROM users WHERE telegram_id=?", (sender,))
    assert (before["total_xp"], before["level"]) == (after["total_xp"], after["level"])
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (friend,))["xp"] == 0, "другу монеты не идут — только заморозка"


def test_only_friends_can_get_one_and_only_with_enough_coins(uid):
    sender, stranger = _pair(uid, mutual=False)
    assert gifts.send_gift(sender, stranger, "freeze") == {"error": "not_friends"}
    poor, friend = _pair(uid + 10, coins=199)
    assert gifts.send_gift(poor, friend, "freeze") == {"error": "not_enough_coins"}
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (poor,))["xp"] == 199, "ничего не списано"
    assert _row("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (friend,))["freeze_balance"] == 0


def test_the_friends_stock_never_goes_above_two(uid):
    sender, friend = _pair(uid, balance=2, empty=None)
    assert gifts.send_gift(sender, friend, "freeze") == {"error": "receiver_full"}
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (sender,))["xp"] == 1000


def test_a_friend_gets_at_most_one_freeze_a_day_from_anyone(uid):
    first, friend = _pair(uid)
    second = uid + 20
    add_user(second, "second", "Второй")
    conn = connect()
    conn.execute("UPDATE users SET xp=1000 WHERE telegram_id=?", (second,))
    conn.commit()
    conn.close()
    make_friends(second, friend)
    assert gifts.send_gift(first, friend, "freeze")["ok"]
    assert gifts.send_gift(second, friend, "freeze") == {"error": "receiver_limit"}
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (second,))["xp"] == 1000, "второй отправитель ничего не заплатил"


def test_the_sender_s_daily_gift_limit_is_shared_with_other_gifts(uid):
    sender, friend = _pair(uid, coins=5000)
    others = []
    for i in range(1, gifts.MAX_GIFTS_PER_DAY + 1):
        other = uid + 30 + i
        add_user(other, f"o{other}", "Друг")
        make_friends(sender, other)
        others.append(other)
    conn = connect()
    for other in others:
        conn.execute("UPDATE users SET diamonds=5 WHERE telegram_id=?", (sender,))
    conn.commit()
    conn.close()
    for other in others:
        assert gifts.send_gift(sender, other, "diamonds", amount=1)["ok"]
    assert gifts.send_gift(sender, friend, "freeze") == {"error": "daily_limit"}
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (sender,))["xp"] == 5000, "при отказе по лимиту монеты возвращаются"
    assert _row("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (friend,))["freeze_balance"] == 0


def test_a_friend_without_a_streak_row_still_gets_a_freeze(uid):
    sender, friend = uid, uid + 1
    add_user(sender, "s", "Отправитель")
    add_user(friend, "f", "Друг")
    conn = connect()
    conn.execute("UPDATE users SET xp=1000 WHERE telegram_id=?", (sender,))
    conn.execute("DELETE FROM streak_meta WHERE user_id=?", (friend,))
    conn.commit()
    conn.close()
    make_friends(sender, friend)
    assert gifts.send_gift(sender, friend, "freeze")["ok"]
    assert _row("SELECT freeze_balance FROM streak_meta WHERE user_id=?", (friend,))["freeze_balance"] == 1


def test_options_tell_the_sender_whether_and_why_a_freeze_can_be_sent(uid):
    sender, friend = _pair(uid)
    freeze = gifts.get_gift_options(sender, friend)["freeze"]
    assert freeze == {"price": 200, "affordable": True, "receiver_balance": 0, "max_balance": 2, "can_send": True, "reason": None}
    gifts.send_gift(sender, friend, "freeze")
    again = gifts.get_gift_options(sender, friend)["freeze"]
    assert again["can_send"] is False and again["reason"] == "today" and again["receiver_balance"] == 1
    full_sender, full_friend = _pair(uid + 40, balance=2, empty=None)
    assert gifts.get_gift_options(full_sender, full_friend)["freeze"]["reason"] == "full"


def test_the_received_gifts_screen_names_the_freeze(uid):
    sender, friend = _pair(uid)
    gifts.send_gift(sender, friend, "freeze")
    received = gifts.get_received_gifts(friend)
    assert received[0]["kind"] == "freeze" and received[0]["label"] == "Заморозка серии ❄️" and received[0]["from"]["telegram_id"] == sender


# ---------------------------------------------------------------------------------------------------------------- «заморозки кончились»

def _missed_a_day(uid_, balance):
    """Серия идёт, вчера отметок не было, ночной переход ещё не выполнялся — он спишет заморозку."""
    add_user(uid_, f"u{uid_}", "Аня")
    conn = connect()
    conn.execute("UPDATE users SET streak=5, best_streak=5, xp=500 WHERE telegram_id=?", (uid_,))
    conn.execute("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (uid_,))
    conn.execute("UPDATE streak_meta SET freeze_balance=?, freeze_empty_since=NULL, rollover_day='2000-01-01' WHERE user_id=?", (balance, uid_))
    conn.commit()
    conn.close()


def test_the_last_freeze_running_out_is_remembered_and_buying_one_clears_it(uid):
    streak.ensure_tables()
    _missed_a_day(uid, 1)
    streak.rollover_user(uid)
    meta = _row("SELECT freeze_balance, freeze_empty_since FROM streak_meta WHERE user_id=?", (uid,))
    assert meta["freeze_balance"] == 0 and meta["freeze_empty_since"] == str(streak.local_today(uid)), "запас кончился — запомнили день"
    assert streak.buy_freeze(uid)["ok"]
    assert _row("SELECT freeze_empty_since FROM streak_meta WHERE user_id=?", (uid,))["freeze_empty_since"] is None


def test_spending_one_of_two_freezes_is_not_running_out(uid):
    streak.ensure_tables()
    _missed_a_day(uid, 2)
    streak.rollover_user(uid)
    meta = _row("SELECT freeze_balance, freeze_empty_since FROM streak_meta WHERE user_id=?", (uid,))
    assert meta["freeze_balance"] == 1 and meta["freeze_empty_since"] is None


# ---------------------------------------------------------------------------------------------------------------- кому «надо помочь»

def test_only_a_friend_whose_freezes_ran_out_while_the_streak_goes_needs_help(uid):
    sender, ran_out = _pair(uid, friend_streak=14)
    never, have, short = uid + 50, uid + 51, uid + 52
    for person, streak_value, balance, empty in ((never, 9, 0, None), (have, 6, 1, None), (short, 1, 0, "2026-10-19")):
        add_user(person, f"p{person}", "Друг")
        conn = connect()
        conn.execute("UPDATE users SET streak=? WHERE telegram_id=?", (streak_value, person))
        conn.execute("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (person,))
        conn.execute("UPDATE streak_meta SET freeze_balance=?, freeze_empty_since=? WHERE user_id=?", (balance, empty, person))
        conn.commit()
        conn.close()
        make_friends(sender, person)
    states = gifts.freeze_states([ran_out, never, have, short])
    assert states[ran_out] == {"balance": 0, "needs_help": True}
    assert states[never] == {"balance": 0, "needs_help": False}, "заморозок не было — серая иконка не нужна (дарят через обычное окно)"
    assert states[have] == {"balance": 1, "needs_help": False}
    assert states[short]["needs_help"] is False, "серия короче порога — терять нечего"
    assert gifts.freeze_states([]) == {}
    candidates = gifts.freeze_help_candidates(sender)
    assert [c["telegram_id"] for c in candidates] == [ran_out]
    assert candidates[0]["first_name"] == "Мария" and candidates[0]["streak"] == 14


def test_the_longest_streak_comes_first_and_a_gifted_friend_drops_out(uid):
    sender, first = _pair(uid, friend_streak=5)
    second = uid + 60
    add_user(second, "second", "Долгая серия")
    conn = connect()
    conn.execute("UPDATE users SET streak=30 WHERE telegram_id=?", (second,))
    conn.execute("INSERT OR IGNORE INTO streak_meta(user_id, onboarding_seen) VALUES (?, 1)", (second,))
    conn.execute("UPDATE streak_meta SET freeze_balance=0, freeze_empty_since='2026-10-18' WHERE user_id=?", (second,))
    conn.commit()
    conn.close()
    make_friends(sender, second)
    assert [c["telegram_id"] for c in gifts.freeze_help_candidates(sender)] == [second, first]
    assert gifts.send_gift(sender, second, "freeze")["ok"]
    assert [c["telegram_id"] for c in gifts.freeze_help_candidates(sender)] == [first]


def test_team_mates_who_are_not_friends_are_not_offered(uid):
    sender, stranger = _pair(uid, mutual=False)
    assert gifts.freeze_help_candidates(sender) == []


def test_the_friends_overview_shows_the_stock_only_for_real_friends(uid):
    from db.friends import get_friends_overview
    sender, friend = _pair(uid, friend_streak=14)
    overview = get_friends_overview(sender)
    assert overview["freeze_price"] == 200
    entry = next(f for f in overview["friends"] if f["telegram_id"] == friend)
    assert entry["freeze"] == {"balance": 0, "needs_help": True}


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_routes_need_a_signed_in_person(client):
    assert (await client.get("/api/freeze-help")).status == 401


async def test_freeze_help_route_lists_the_friends_in_need_with_the_price(client, uid):
    sender, friend = _pair(uid)
    body = await (await client.get("/api/freeze-help", headers=_headers(sender))).json()
    assert body["price"] == 200 and [f["telegram_id"] for f in body["friends"]] == [friend]
    assert {"first_name", "avatar_id", "frame_id", "streak"} <= set(body["friends"][0])


async def test_friends_route_carries_the_icons_and_the_price(client, uid):
    sender, friend = _pair(uid)
    body = await (await client.get("/api/friends", headers=_headers(sender))).json()
    assert body["freeze_price"] == 200
    assert next(f for f in body["friends"] if f["telegram_id"] == friend)["freeze"]["needs_help"] is True


async def test_gift_route_sends_a_freeze_writes_the_event_and_returns_the_new_balance(client, uid):
    sender, friend = _pair(uid)
    r = await client.post(f"/api/users/{friend}/gift", json={"kind": "freeze"}, headers=_headers(sender))
    body = await r.json()
    assert r.status == 200 and body["gift"]["kind"] == "freeze" and body["gift"]["price"] == 200
    assert _row("SELECT xp FROM users WHERE telegram_id=?", (sender,))["xp"] == 800
    again = await client.post(f"/api/users/{friend}/gift", json={"kind": "freeze"}, headers=_headers(sender))
    assert again.status == 400 and (await again.json())["error"] in ("receiver_full", "receiver_limit")
    conn = connect()
    try:
        rows = conn.execute("SELECT meta FROM user_events WHERE user_id=? AND name='freeze_gift_sent'", (sender,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and str(friend) in rows[0]["meta"]


async def test_gift_route_refuses_strangers_and_poor_senders(client, uid):
    sender, stranger = _pair(uid, mutual=False)
    r = await client.post(f"/api/users/{stranger}/gift", json={"kind": "freeze"}, headers=_headers(sender))
    assert r.status == 400 and (await r.json())["error"] == "not_friends"
    poor, friend = _pair(uid + 10, coins=5)
    r = await client.post(f"/api/users/{friend}/gift", json={"kind": "freeze"}, headers=_headers(poor))
    assert r.status == 400 and (await r.json())["error"] == "not_enough_coins"


# ---------------------------------------------------------------------------------------------------------------- окно в приложении

def test_window_markup_is_a_hidden_dialog_with_the_duolingo_parts():
    block = INDEX[INDEX.index('id="freezeHelpOverlay"'):]
    block = block[:block.index('id="appTourOverlay"')]
    assert re.search(r'<div class="freeze-help-overlay" id="freezeHelpOverlay" hidden aria-hidden="true">', INDEX)
    for needle in ('role="dialog"', 'aria-modal="true"', 'id="freezeHelpBalance"', 'id="freezeHelpAvatar"', 'class="freeze-help-badge"',
                   'id="freezeHelpTitle"', 'id="freezeHelpSub"', 'id="freezeHelpSend"', 'id="freezeHelpSkip"', "Нет, спасибо"):
        assert needle in block, needle
    assert block.count('class="freeze-spark') == 8, "искры при отправке"


def test_the_gray_icon_sits_right_of_the_name_and_a_tap_opens_the_window():
    row = APP_JS[APP_JS.index("function friendRowHtml("):][:1400]
    assert 'friendNameHtml(f, "Друг", friendFreezeHtml(f))' in row
    name = APP_JS[APP_JS.index("function friendNameHtml("):][:520]
    assert "${chip}${extra}" in name, "значок — после имени и ранга"
    icon = APP_JS[APP_JS.index("function friendFreezeHtml("):][:1100]
    assert "friend-freeze--help" in icon and "data-freeze-help" in icon and "friend-freeze--have" in icon
    assert "needs_help" in icon and "<b>${have}</b>" in icon
    tap = APP_JS[APP_JS.index("function initFriendsActions()"):][:1500]
    assert tap.index("[data-freeze-help]") < tap.index("[data-nudge]"), "серая снежинка важнее тапа по строке"
    assert "openFreezeHelp({ friend, price: Number(friendsData.freeze_price) || 200 })" in tap and 'trk("freeze_help_open")' in tap


def test_sending_pays_updates_the_friend_and_celebrates_and_errors_are_handled():
    send = APP_JS[APP_JS.index("async function sendFreezeHelp()"):][:1800]
    assert 'body: JSON.stringify({ kind: "freeze" })' in send and "applyActionPatch(result)" in send
    assert send.index("if (s.sim)") < send.index("/gift"), "в симуляции ничего не отправляется"
    assert "friend.freeze = { balance: 1, needs_help: false };" in send and "renderFriendsCard();" in send
    assert 'haptic("success");' in send and "playChime();" in send
    assert '["receiver_full", "receiver_limit", "not_friends"]' in send
    texts = APP_JS[APP_JS.index("function renderFreezeHelp()"):][:2200]
    assert "теперь без заморозок! Надо помочь другу!" in texts and "Заморозка отправлена!" in texts
    assert "Отправить одну за ${ADAM_COIN_ICON}${s.price}" in texts and "Не хватает монет" in texts


def test_the_popup_on_entry_is_rare_polite_and_needs_enough_coins():
    popup = APP_JS[APP_JS.index("async function maybeShowFreezeHelp("):][:1900]
    assert "freezeHelpPrompted" in popup and "introChainPending()" in popup and "celebrationOverlayOpen()" in popup
    assert "state?.adam_intro?.show" in popup, "не поверх знакомства с Адамом"
    assert "freezeHelpCoins() < price" in popup, "нет монет — окно не открываем"
    assert "adam_freeze_help_" in popup and 'toLocaleDateString("sv")' in popup, "раз в день на друга"
    assert 'trk("freeze_help_shown")' in popup
    assert "setTimeout(() => maybeShowFreezeHelp(), 5000);" in APP_JS[APP_JS.index("postBootstrapInitDone = true;"):][:420]
    assert "initFreezeHelp();" in APP_JS
    assert '"freezeHelpOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:700]


def test_the_gift_sheet_offers_the_freeze_too():
    sheet = APP_JS[APP_JS.index("function renderGiftSheet()"):][:3600]
    assert 'data-gift-kind="freeze"' in sheet and "o.freeze.can_send && o.freeze.affordable && !noLeft" in sheet
    assert "у друга уже полный запас" in sheet and "сегодня другу уже дарили" in sheet
    send = APP_JS[APP_JS.index("async function sendGiftFromSheet("):][:900]
    assert 'payload = { kind: "freeze" };' in send and "заморозку серии за" in send


def test_new_error_codes_are_translated_both_ways():
    assert "receiver_full: \"У друга уже максимум заморозок\"" in APP_JS and "receiver_limit: \"Сегодня другу уже подарили заморозку\"" in APP_JS
    assert "receiver_full: \"Your friend already has the maximum number of freezes\"" in APP_JS


def test_simulations_show_the_offer_and_the_result():
    from db.sim_lab import sim_scenes
    keys = [s["key"] for s in sim_scenes()]
    assert "freeze_help" in keys and "freeze_help_sent" in keys
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):APP_JS.index("async function initSimMode()")]
    assert "freeze_help: () => openFreezeHelp({ sim: true, friend: SIM_FRIEND, price: 200, coins: 640 })," in block
    assert "freeze_help_sent: () => openFreezeHelp({ sim: true, friend: SIM_FRIEND, price: 200, coins: 440, sent: true })," in block


def test_styles_are_light_and_the_overlay_sits_with_the_other_full_screen_windows():
    css = CSS[CSS.index("Заморозка другу (app.js::openFreezeHelp)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", css) and "backdrop-filter" not in css
    assert "infinite" not in css
    assert ".freeze-help-overlay[hidden]{display:none !important}" in css
    assert int(re.search(r"\.freeze-help-overlay\{[^}]*z-index:(\d+)", css).group(1)) >= 10150
    for name in ("freezePop", "freezeSpark"):
        assert f"@keyframes {name}" in css
    assert ".friend-freeze--help{" in css and ".friend-freeze--have{" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


# ---------------------------------------------------------------------------------------------------------------- учёт

def test_events_are_labelled_and_belong_to_the_gifts_feature():
    from db.tracking import EVENT_LABELS
    from webapp import feature_map as fm
    names = ("freeze_gift_sent", "freeze_help_shown", "freeze_help_open")
    assert all(name in EVENT_LABELS for name in names)
    assert {f.id for f in fm.FEATURES for name in names if name in f.events} == {"gifts"}
    gifts_feature = next(f for f in fm.FEATURES if f.id == "gifts")
    assert "freezeHelp" in gifts_feature.screens
    for name in re.findall(r'trk\("(freeze_help_\w+)"', APP_JS):
        assert name in names, name
