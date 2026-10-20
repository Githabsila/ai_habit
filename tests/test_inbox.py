"""
Центр уведомлений (20.10): колокольчик в шапке → экран «Уведомления» с вкладками Все / Системные / Адам / Друзья / Подписчики / Подарки.
Лента собирается из того, что уже хранится в базе (db/inbox.py), «новое» — позже метки users.inbox_seen_at. Браузера в тестах нет: правила
ленты и маршруты проверяем по-настоящему, экран — по тексту разметки и скрипта.
"""
import itertools
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db import add_user, inbox
from db.core import connect
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(857_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


@pytest.fixture(autouse=True)
def empty_changelog():
    """Записи «Что нового» общие для всех и публикуются при старте — в ленте любого человека они «свежие» и мешают считать. На время теста
    таблица пустая, потом всё возвращается как было (база общая на весь прогон)."""
    conn = connect()
    try:
        saved = [tuple(r) for r in conn.execute("SELECT id, title, body, created_at FROM changelog_entries").fetchall()]
        conn.execute("DELETE FROM changelog_entries")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = connect()
    try:
        conn.execute("DELETE FROM changelog_entries")
        conn.executemany("INSERT INTO changelog_entries(id, title, body, created_at) VALUES (?, ?, ?, ?)", saved)
        conn.commit()
    finally:
        conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def _run(sql, args=()):
    conn = connect()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _person(uid_, name="Мария"):
    add_user(uid_, f"u{uid_}", name)
    return uid_


def _me(uid_):
    """Человек, который «давно» не открывал центр: всё свежее для него новое."""
    add_user(uid_, f"me{uid_}", "Я")
    _run("UPDATE users SET inbox_seen_at='2000-01-01 00:00:00' WHERE telegram_id=?", (uid_,))
    return uid_


def _titles(feed):
    return [it["title"] for it in feed["items"]]


# ---------------------------------------------------------------------------------------------------------------- разделы

def test_the_tabs_are_the_ones_the_owner_asked_for_in_that_order():
    assert [label for _, label in inbox.CATEGORIES][:5] == ["Все", "Системные", "Адам", "Друзья", "Подписчики"]
    assert inbox.CATEGORY_KEYS[0] == "all" and len(set(inbox.CATEGORY_KEYS)) == len(inbox.CATEGORY_KEYS)
    assert "gifts" in inbox.CATEGORY_KEYS, "все подарки — в отдельной вкладке"


def test_first_open_treats_the_past_as_read(uid):
    add_user(uid, "old", "Давний")
    _person(uid + 1)
    _run("INSERT INTO gifts(from_user_id, to_user_id, kind, amount, price, day, created_at) VALUES (?, ?, 'diamonds', 3, 0, '2026-10-01', ?)",
         (uid + 1, uid, _ago(60 * 24 * 3)))
    assert inbox.get_summary(uid) == {"unread": 0, "categories": {key: 0 for key in inbox.CATEGORY_KEYS}}
    assert _titles(inbox.get_feed(uid))[0].endswith("3 💎"), "в ленте старое есть, но не новое"
    assert inbox.get_feed(uid)["items"][0]["unread"] is False


# ---------------------------------------------------------------------------------------------------------------- источники по разделам

def test_gifts_name_the_giver_and_the_gift(uid):
    me, giver = _me(uid), _person(uid + 1, "Мария")
    for kind, amount, expected in (("diamonds", 3, "Мария подарил(а) тебе 3 💎"), ("freeze", 1, "Мария подарил(а) тебе заморозку серии")):
        _run("INSERT INTO gifts(from_user_id, to_user_id, kind, amount, price, day, created_at) VALUES (?, ?, ?, ?, 0, '2026-10-20', ?)",
             (giver, me, kind, amount, _ago(5)))
        assert expected in _titles(inbox.get_feed(me))
    first = inbox.get_feed(me)["items"][0]
    assert first["cat"] == "gifts" and first["actor"]["first_name"] == "Мария" and first["actor"]["telegram_id"] == giver
    assert first["action"] == {"type": "gifts"} and first["icon"] in ("💎", "❄️")


def test_the_admin_medal_is_a_gift_without_an_actor(uid):
    me = _me(uid)
    _run("INSERT INTO admin_gifts(user_id, kind, note, created_at) VALUES (?, 'badge', 'За вклад в развитие ADAM', ?)", (me, _ago(3)))
    item = inbox.get_feed(me)["items"][0]
    assert item["cat"] == "gifts" and item["actor"] is None and item["title"] == "Администратор подарил(а) тебе медаль"
    assert item["text"] == "За вклад в развитие ADAM"


def test_support_and_reminders_from_friends(uid):
    me, friend = _me(uid), _person(uid + 1, "Анна")
    _run("INSERT INTO friend_reactions(from_user_id, to_user_id, emoji, day, created_at) VALUES (?, ?, '👍', '2026-10-20', ?)", (friend, me, _ago(10)))
    _run("INSERT INTO friend_nudges(from_user_id, to_user_id, day, created_at) VALUES (?, ?, '2026-10-20', ?)", (friend, me, _ago(20)))
    feed = inbox.get_feed(me)
    assert _titles(feed) == ["Анна поддержал(а) тебя 👍", "Анна напомнил(а) тебе отметить привычки"]
    assert {it["cat"] for it in feed["items"]} == {"friends"}
    assert feed["items"][0]["action"] == {"type": "profile", "id": friend}


def test_a_follower_is_a_follower_until_you_follow_back_then_a_friend(uid):
    me, fan, mutual = _me(uid), _person(uid + 1, "Игорь"), _person(uid + 2, "Олег")
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (fan, me, _ago(30)))
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (mutual, me, _ago(50)))
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (me, mutual, _ago(40)))
    by_kind = {it["kind"]: it for it in inbox.get_feed(me)["items"]}
    assert by_kind["follower_new"]["cat"] == "followers" and by_kind["follower_new"]["title"] == "Игорь подписался(ась) на тебя"
    assert by_kind["friend_added"]["cat"] == "friends" and by_kind["friend_added"]["title"] == "Олег теперь в друзьях"
    assert by_kind["friend_added"]["at"] == _ago(40), "друзьями стали, когда пара замкнулась (позднейшая подписка)"


def test_pair_quest_invitations_and_results_land_in_friends(uid):
    me, partner = _me(uid), _person(uid + 1, "Олег")
    _run("INSERT INTO pair_quests(inviter_id, invitee_id, status, goal, created_at) VALUES (?, ?, 'pending', 20, ?)", (partner, me, _ago(15)))
    _run("INSERT INTO pair_quests(inviter_id, invitee_id, status, goal, created_at, accepted_at) VALUES (?, ?, 'active', 20, ?, ?)",
         (me, partner, _ago(200), _ago(100)))
    _run("INSERT INTO pair_quests(inviter_id, invitee_id, status, goal, created_at, accepted_at, finished_at) VALUES (?, ?, 'completed', 20, ?, ?, ?)",
         (me, partner, _ago(5000), _ago(4900), _ago(60)))
    feed = inbox.get_feed(me)
    kinds = {it["kind"] for it in feed["items"]}
    assert {"pair_invite", "pair_accepted", "pair_done"} <= kinds
    assert all(it["cat"] == "friends" and it["action"]["type"] == "pair" for it in feed["items"] if it["kind"].startswith("pair_"))
    assert "Олег зовёт тебя в парное задание" in _titles(feed)


def test_adam_messages_are_shown_with_a_short_preview(uid):
    me = _me(uid)
    conn = connect()
    try:
        cursor = conn.execute("INSERT INTO ai_messages(user_id, role, message) VALUES (?, 'assistant', ?)", (me, "Заметил, что вторая привычка ещё ждёт. " * 12))
        message_id = cursor.lastrowid
        conn.execute("INSERT INTO adam_checkins(user_id, day, slot, status, scenario, source, message_id, created_at) VALUES (?, '2026-10-20', 'day', 'sent', 'x', 'app', ?, ?)",
                     (me, message_id, _ago(8)))
        conn.execute("INSERT INTO adam_checkins(user_id, day, slot, status, created_at) VALUES (?, '2026-10-20', 'evening', 'skipped', ?)", (me, _ago(2)))
        conn.commit()
    finally:
        conn.close()
    items = inbox.get_feed(me)["items"]
    assert len(items) == 1, "пропущенные окна в ленту не попадают"
    assert items[0]["cat"] == "adam" and items[0]["title"] == "Адам написал тебе" and items[0]["action"] == {"type": "chat"}
    assert items[0]["text"].startswith("Заметил, что вторая привычка") and len(items[0]["text"]) <= inbox.PREVIEW_CHARS and items[0]["text"].endswith("…")


def test_system_events_cover_the_streak_news_and_invites(uid):
    me, friend = _me(uid), _person(uid + 1, "Мария")
    _run("INSERT INTO streak_days(user_id, day, status, streak_after, ai_message, created_at) VALUES (?, '2026-10-19', 'freeze', 14, '❄️ День сохранён заморозкой.', ?)", (me, _ago(30)))
    _run("INSERT INTO streak_days(user_id, day, status, streak_after, ai_message, created_at) VALUES (?, '2026-10-18', 'missed', 0, 'x', ?)", (me, _ago(60 * 24 + 5)))
    _run("INSERT INTO streak_days(user_id, day, status, streak_after, created_at) VALUES (?, '2026-10-17', 'completed', 3, ?)", (me, _ago(60 * 48)))
    _run("INSERT INTO streak_rewards(user_id, milestone, status, frame, created_at) VALUES (?, 7, 'Старт', 'Первая искра', ?)", (me, _ago(90)))
    _run("INSERT INTO changelog_entries(title, body, created_at) VALUES ('Знакомство с Адамом', 'Теперь Адам узнаёт тебя и подбирает метод планирования дня.', ?)", (_ago(120),))
    _run("INSERT INTO referral_invites(invitee_id, inviter_id, status, inviter_coins, invitee_coins, created_at, paid_at) VALUES (?, ?, 'paid', 100, 50, ?, ?)",
         (friend, me, _ago(200), _ago(150)))
    feed = inbox.get_feed(me)
    kinds = {it["kind"] for it in feed["items"]}
    assert {"streak_freeze", "streak_lost", "streak_status", "whats_new", "referral_paid"} <= kinds
    assert "completed" not in kinds and all(it["cat"] == "system" for it in feed["items"])
    titles = _titles(feed)
    assert "День сохранён заморозкой" in titles and "Серия прервалась" in titles and "Новый статус «Старт»" in titles
    assert next(it for it in feed["items"] if it["kind"] == "streak_freeze")["text"] == "Серия 14 дн. осталась с тобой"
    assert "Что нового: Знакомство с Адамом" in titles and "Мария пришёл(ла) по твоему приглашению: +100 Adam Coin" in titles


def test_every_tab_has_something_to_show_when_every_source_has_news(uid):
    me, friend = _me(uid), _person(uid + 1)
    _run("INSERT INTO gifts(from_user_id, to_user_id, kind, amount, price, day, created_at) VALUES (?, ?, 'diamonds', 1, 0, 'd', ?)", (friend, me, _ago(1)))
    _run("INSERT INTO friend_nudges(from_user_id, to_user_id, day, created_at) VALUES (?, ?, 'd', ?)", (friend, me, _ago(2)))
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (friend, me, _ago(3)))
    _run("INSERT INTO streak_days(user_id, day, status, streak_after, created_at) VALUES (?, '2026-10-19', 'missed', 0, ?)", (me, _ago(4)))
    conn = connect()
    try:
        message_id = conn.execute("INSERT INTO ai_messages(user_id, role, message) VALUES (?, 'assistant', 'Привет')", (me,)).lastrowid
        conn.execute("INSERT INTO adam_checkins(user_id, day, slot, status, message_id, created_at) VALUES (?, 'd', 'day', 'sent', ?, ?)", (me, message_id, _ago(5)))
        conn.commit()
    finally:
        conn.close()
    feed = inbox.get_feed(me)
    assert {it["cat"] for it in feed["items"]} == {"gifts", "friends", "followers", "system", "adam"} == set(inbox.CATEGORY_KEYS) - {"all"}
    counts = {c["key"]: c["unread"] for c in feed["categories"]}
    assert counts["all"] == 5 and all(counts[key] == 1 for key in counts if key != "all")
    assert [c["label"] for c in feed["categories"]] == [label for _, label in inbox.CATEGORIES]


# ---------------------------------------------------------------------------------------------------------------- порядок, «новое», блокировки

def test_newest_first_and_unread_follow_the_seen_mark(uid):
    me, friend = _me(uid), _person(uid + 1)
    _run("INSERT INTO friend_nudges(from_user_id, to_user_id, day, created_at) VALUES (?, ?, 'a', ?)", (friend, me, _ago(300)))
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (friend, me, _ago(10)))
    feed = inbox.get_feed(me)
    assert [it["kind"] for it in feed["items"]] == ["follower_new", "friend_nudge"] and all(it["unread"] for it in feed["items"])
    _run("UPDATE users SET inbox_seen_at=? WHERE telegram_id=?", (_ago(100), me))
    feed = inbox.get_feed(me)
    assert [it["unread"] for it in feed["items"]] == [True, False], "позже метки — новое, раньше — уже читано"
    assert inbox.get_summary(me) == {"unread": 1, "categories": {"all": 1, "system": 0, "adam": 0, "friends": 0, "followers": 1, "gifts": 0}}
    inbox.mark_seen(me)
    assert inbox.get_summary(me)["unread"] == 0
    assert all(not it["unread"] for it in inbox.get_feed(me)["items"])


def test_blocked_people_never_show_up_in_either_direction(uid):
    me, a, b = _me(uid), _person(uid + 1, "Блок1"), _person(uid + 2, "Блок2")
    for person in (a, b):
        _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (person, me, _ago(5)))
    _run("INSERT INTO user_blocks(blocker_id, blocked_id) VALUES (?, ?)", (me, a))
    _run("INSERT INTO user_blocks(blocker_id, blocked_id) VALUES (?, ?)", (b, me))
    assert inbox.get_feed(me)["items"] == []


def test_old_news_beyond_the_recent_window_is_dropped(uid):
    me, friend = _me(uid), _person(uid + 1)
    _run("INSERT INTO friend_nudges(from_user_id, to_user_id, day, created_at) VALUES (?, ?, 'a', ?)", (friend, me, _ago(60 * 24 * (inbox.RECENT_DAYS + 3))))
    assert inbox.get_feed(me)["items"] == []


def test_a_broken_source_does_not_take_the_whole_feed_down(uid, monkeypatch):
    me, friend = _me(uid), _person(uid + 1)
    _run("INSERT INTO follows(follower_id, followee_id, created_at) VALUES (?, ?, ?)", (friend, me, _ago(5)))

    def broken(conn, user_id, since):
        raise RuntimeError("boom")

    monkeypatch.setattr(inbox, "SOURCES", (broken,) + inbox.SOURCES)
    assert [it["kind"] for it in inbox.get_feed(me)["items"]] == ["follower_new"]


# ---------------------------------------------------------------------------------------------------------------- маршруты

async def test_routes_need_a_signed_in_person(client):
    assert (await client.get("/api/inbox")).status == 401
    assert (await client.get("/api/inbox/summary")).status == 401
    assert (await client.post("/api/inbox/seen")).status == 401


async def test_feed_summary_and_seen_work_together(client, uid):
    me, friend = _me(uid), _person(uid + 1, "Мария")
    _run("INSERT INTO gifts(from_user_id, to_user_id, kind, amount, price, day, created_at) VALUES (?, ?, 'diamonds', 5, 0, 'd', ?)", (friend, me, _ago(3)))
    summary = await (await client.get("/api/inbox/summary", headers=_headers(me))).json()
    assert summary["unread"] == 1 and summary["categories"]["gifts"] == 1
    feed = await (await client.get("/api/inbox", headers=_headers(me))).json()
    assert [c["key"] for c in feed["categories"]] == list(inbox.CATEGORY_KEYS)
    assert feed["items"][0]["title"] == "Мария подарил(а) тебе 5 💎" and feed["items"][0]["unread"] is True
    assert (await client.post("/api/inbox/seen", headers=_headers(me))).status == 200
    again = await (await client.get("/api/inbox/summary", headers=_headers(me))).json()
    assert again["unread"] == 0


# ---------------------------------------------------------------------------------------------------------------- экран в приложении

def test_the_bell_sits_in_the_header_and_the_screen_has_tabs_and_a_list():
    header = INDEX[INDEX.index('<header class="player-card">'):INDEX.index("</header>")]
    assert 'id="inboxBtn"' in header and 'class="player-card__bell"' in header and 'aria-label="Уведомления"' in header
    assert 'id="inboxBadge"' in header and 'class="player-card__bell-badge" id="inboxBadge" hidden' in header
    block = INDEX[INDEX.index('id="inboxOverlay"'):]
    block = block[:block.index('id="freezeHelpOverlay"')]
    assert re.search(r'<div class="subpage-overlay inbox-overlay" id="inboxOverlay" hidden aria-hidden="true">', INDEX)
    for needle in ('id="inboxOverlayBackdrop"', 'id="inboxOverlayClose"', ">Уведомления</h2>", 'id="inboxTabs"', 'role="tablist"', 'id="inboxBody"'):
        assert needle in block, needle
    assert INDEX.index('id="inboxOverlay"') > INDEX.index("</header>"), "экран лежит в документе вне шапки"


def test_tabs_come_from_the_server_and_switch_without_reloading():
    code = APP_JS[APP_JS.index("function renderInbox()"):][:2300]
    assert "s.categories.map((c) =>" in code and 'role="tab"' in code and "data-inbox-tab" in code
    assert 's.tab === "all" ? s.items : s.items.filter((it) => it.cat === s.tab)' in code, "вкладка только фильтрует загруженную ленту"
    assert "inbox-day" in code, "лента сгруппирована по дням"
    tap = APP_JS[APP_JS.index("function onInboxTap("):][:1900]
    assert 'haptic("select")' in tap and 'trk("inbox_tab", inboxState.tab)' in tap
    assert "renderInbox();" in tap and 'api("' not in tap.split("const row")[0], "переключение вкладки не ходит на сервер"


def test_opening_loads_marks_read_and_clears_the_badge_but_a_demo_does_not():
    code = APP_JS[APP_JS.index("async function openInbox("):][:2600]
    assert 'api("/api/inbox", { timeoutMs: 8000 })' in code and "setInboxBadge(0);" in code and 'api("/api/inbox/seen", { method: "POST" })' in code
    assert code.index("if (demo) return;") < code.index('api("/api/inbox"'), "демо ничего не грузит и не отмечает"
    assert 'trk("inbox_open"' in code
    badge = APP_JS[APP_JS.index("async function refreshInboxBadge()"):][:700]
    assert "/api/inbox/summary" in badge and "labMode || simMode" in badge


def test_every_kind_of_tap_has_a_destination():
    tap = APP_JS[APP_JS.index("function onInboxTap("):][:2600]
    for needle in ("openUserProfile(Number(row.dataset.inboxId))", 'document.getElementById("openGiftsBtn")?.click()', 'window.location.href = "/coach"', 'data-tab="home"', 'data-tab="profile"'):
        assert needle in tap, needle
    assert 'trk("inbox_item"' in tap and "inboxState.demo" in tap


def test_the_bell_is_wired_at_start_and_refreshes_on_return():
    init = APP_JS[APP_JS.index("function initInbox()"):][:900]
    assert 'getElementById("inboxBtn")?.addEventListener("click", () => openInbox())' in init
    assert "visibilitychange" in init and "setInterval(refreshInboxBadge, 120000)" in init
    assert "initInbox();" in APP_JS[APP_JS.index("async function boot()"):][:900]


def test_the_simulation_shows_a_full_demo_feed():
    from db.sim_lab import sim_scenes
    assert "inbox" in [s["key"] for s in sim_scenes()]
    block = APP_JS[APP_JS.index("const SIM_SCENES = {"):APP_JS.index("async function initSimMode()")]
    assert "inbox: () => openInbox({ demo: SIM_INBOX })," in block
    demo = APP_JS[APP_JS.index("const SIM_INBOX"):APP_JS.index("const SIM_SCENES = {")]
    assert {"gifts", "adam", "followers", "friends", "system"} <= set(re.findall(r'cat: "(\w+)"', demo))


def test_styles_are_light_for_weak_webviews():
    css = CSS[CSS.index("Центр уведомлений (app.js::openInbox"):CSS.index("Заморозка другу (app.js::openFreezeHelp)")]
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", css) and "backdrop-filter" not in css
    assert "infinite" not in css and "@keyframes" not in css
    assert ".inbox-tabs{" in css and "overflow-x:auto" in css, "вкладки листаются в сторону"
    assert ".player-card__bell{" in css and "position:absolute;top:12px;right:12px" in css
    assert ".player-card__bell-badge[hidden]{display:none}" in css


# ---------------------------------------------------------------------------------------------------------------- учёт

def test_events_are_labelled_and_the_screen_belongs_to_a_feature():
    from db.tracking import EVENT_LABELS
    from webapp import feature_map as fm
    names = ("inbox_open", "inbox_tab", "inbox_item")
    assert all(name in EVENT_LABELS for name in names)
    assert {f.id for f in fm.FEATURES for name in names if name in f.events} == {"inbox"}
    feature = next(f for f in fm.FEATURES if f.id == "inbox")
    assert "inbox" in feature.screens and "inboxBtn" in feature.clicks
    for name in re.findall(r'trk\("(inbox_\w+)"', APP_JS):
        assert name in names, name
