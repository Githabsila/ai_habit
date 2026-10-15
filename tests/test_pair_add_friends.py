"""
Парное задание → «Выбери союзника» → «Добавить друзей» (15.10): когда все друзья заняты, окно предлагает два способа — ссылку в Telegram
и подписку на активных игроков ADAM (серия от 4 дней). Подписались в ответ — друзья, и их уже можно позвать в задание.
"""
import asyncio
import itertools
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import webapp.webapp_server as ws
from db import ACTIVE_PEOPLE_MIN_STREAK, add_user, block_user, follow, list_active_people, make_friends
from db.core import connect
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")

_uid_counter = itertools.count(840_000_000, 100)
BASE = 4000          # серии тестовых людей — далеко от серий остальных тестов в общей БД, чтобы они не мешали порядку


@pytest.fixture
def uid():
    return next(_uid_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def _person(uid_, streak=BASE, name="Игрок", handle=None, active=True):
    add_user(uid_, f"u{uid_}", name)
    _sql("UPDATE users SET streak=?, best_streak=?, access_status='approved', handle=? WHERE telegram_id=?",
         (streak, streak, handle or f"h{uid_}", uid_))
    if active:
        _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status) VALUES (?, date('now'), 'completed')", (uid_,))
    return uid_


def _ids(viewer, **kwargs):
    return [p["telegram_id"] for p in list_active_people(viewer, limit=500, **kwargs)]


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


async def _flush_background():
    tasks = list(ws._background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


def test_the_list_holds_active_players_with_a_streak_from_four_days(uid):
    assert ACTIVE_PEOPLE_MIN_STREAK == 4
    me = _person(uid)
    ok, short, edge = _person(uid + 1, BASE), _person(uid + 2, 3), _person(uid + 3, 4)
    ids = _ids(me)
    assert ok in ids and edge in ids and short not in ids and me not in ids
    assert len(list_active_people(me, limit=2)) == 2


def test_nobody_is_shown_who_should_not_be(uid):
    me = _person(uid)
    banned, pending, stale = _person(uid + 1), _person(uid + 2), _person(uid + 3, active=False)
    followed, blocked_by_me, blocked_me, friend = _person(uid + 4), _person(uid + 5), _person(uid + 6), _person(uid + 7)
    visible = _person(uid + 8)
    _sql("UPDATE users SET banned=1 WHERE telegram_id=?", (banned,))
    _sql("UPDATE users SET access_status='new' WHERE telegram_id=?", (pending,))
    _sql("INSERT OR REPLACE INTO streak_days(user_id, day, status) VALUES (?, date('now','-10 days'), 'completed')", (stale,))
    follow(me, followed)
    block_user(me, blocked_by_me)
    block_user(blocked_me, me)
    make_friends(me, friend)
    ids = _ids(me)
    assert visible in ids
    for hidden in (banned, pending, stale, followed, blocked_by_me, blocked_me, friend, me):
        assert hidden not in ids, hidden


def test_an_admin_without_the_survey_still_counts_but_a_new_player_does_not(uid, monkeypatch):
    import config
    me, admin, newcomer = _person(uid), _person(uid + 1), _person(uid + 2)
    _sql("UPDATE users SET access_status='new' WHERE telegram_id IN (?, ?)", (admin, newcomer))
    monkeypatch.setattr(config, "ADMIN_IDS", [admin], raising=False)
    ids = _ids(me)
    assert admin in ids and newcomer not in ids


def test_those_who_follow_me_come_first_then_the_closest_streak(uid):
    me = _person(uid, BASE)
    far, near, nearest, follower = _person(uid + 1, BASE + 100), _person(uid + 2, BASE + 3), _person(uid + 3, BASE + 1), _person(uid + 4, BASE + 50)
    follow(follower, me)
    mine = [pid for pid in _ids(me) if pid in (far, near, nearest, follower)]
    assert mine == [follower, nearest, near, far]
    row = next(p for p in list_active_people(me, limit=500) if p["telegram_id"] == follower)
    assert row["followed_by"] is True and row["following"] is False and row["friends"] is False
    assert {"first_name", "handle", "avatar_id", "frame_id", "streak", "evo"} <= set(row)


async def test_the_route_returns_the_list_and_requires_login(client, uid):
    me, other = _person(uid), _person(uid + 1, BASE + 1, name="Борис", handle=f"boris_{uid}")
    follow(other, me)       # подписанные на меня идут первыми — в общей БД других тестов хватает людей с такой же серией
    data = await (await client.get("/api/people/active", headers=_headers(me))).json()
    assert data["min_streak"] == 4
    assert any(u["telegram_id"] == other and u["handle"] == f"boris_{uid}" and u["first_name"] == "Борис" for u in data["users"])
    assert (await client.get("/api/people/active")).status == 401
    await _flush_background()


async def test_following_a_stranger_then_the_answer_makes_friends_and_the_push_tells_who_it_is(client, uid):
    me = _person(uid, name="Аня", handle=f"anya_{uid}")
    other = _person(uid + 1, BASE + 1, name="Борис", handle=f"boris_{uid}")
    client.app["bot"] = bot = SimpleNamespace(send_message=AsyncMock())
    result = await (await client.post(f"/api/users/{other}/follow", headers=_headers(me))).json()
    assert result["relation"]["following"] is True and result["relation"]["friends"] is False
    await _flush_background()
    (chat_id, text), kwargs = bot.send_message.call_args
    assert chat_id == other and f"Аня (@​anya_{uid})" in text and "подписался(ась) на тебя" in text, text
    assert f"@anya_{uid}" not in text, "ник ADAM не должен превратиться в ссылку на чужой @username"
    assert other not in _ids(me), "на кого подписались, из списка пропадает"

    bot.send_message.reset_mock()
    back = await (await client.post(f"/api/users/{me}/follow", headers=_headers(other))).json()
    assert back["relation"]["friends"] is True
    await _flush_background()
    (chat_id, text), _ = bot.send_message.call_args
    assert chat_id == me and f"Борис (@​boris_{uid})" in text and "теперь вы друзья" in text


async def test_a_friend_made_this_way_shows_up_among_the_allies(client, uid):
    me, other = _person(uid, BASE), _person(uid + 1, BASE + 1)
    client.app["bot"] = SimpleNamespace(send_message=AsyncMock())
    await client.post(f"/api/users/{other}/follow", headers=_headers(me))
    before = await (await client.get("/api/pair-quest/candidates", headers=_headers(me))).json()
    assert other not in [f["telegram_id"] for f in before["friends"]]
    await client.post(f"/api/users/{me}/follow", headers=_headers(other))
    after = await (await client.get("/api/pair-quest/candidates", headers=_headers(me))).json()
    assert other in [f["telegram_id"] for f in after["friends"]]
    await _flush_background()


# ---------------------------------------------------------------------------------------------------------------- окно в приложении

def test_the_button_sits_between_the_list_and_confirm_and_is_hidden_until_needed():
    assert re.search(r'<button[^>]*id="pairSheetAdd"[^>]*data-pair-act="add-friends"[^>]*hidden[^>]*>＋ Добавить друзей</button>', INDEX)
    assert INDEX.index('id="pairSheetAdd"') < INDEX.index('id="pairSheetConfirm"')
    render = APP[APP.index("function renderPairSheet()"):APP.index("async function openPairPeople()")]
    assert "addBtn.hidden = friends.some((f) => f.available)" in render, "кнопка — только когда свободных друзей нет"


def test_two_ways_are_offered_the_link_and_the_active_players():
    html = APP[APP.index("function pairAddViewHtml()"):APP.index("function pairPersonRowHtml(")]
    assert 'data-pair-act="invite-link"' in html and "Отправить ссылку в Telegram" in html
    assert 'data-pair-act="pick-people"' in html and "Выбрать из активных игроков" in html and "Серия от ${minStreak} дней" in html
    action = APP[APP.index("async function pairSheetAction("):APP.index("async function openPairSheet()")]
    assert "openFriendInvite(pairShareText())" in action
    assert '"/api/people/active"' in APP and "/api/users/${id}/follow" in APP
    assert "async function openFriendInvite(shareText)" in APP and "shareText ||" in APP, "подпись можно задать под парное задание"


def test_the_pair_share_text_names_the_quest_and_the_goal():
    text = APP[APP.index("function pairShareText()"):APP.index("// «Добавить друзей»")]
    assert "парное задание" in text and "${pairCandidates?.goal || 20}" in text and text.strip().endswith("}")


def test_back_walks_one_step_and_a_new_opening_starts_from_the_list():
    back = APP[APP.index('document.getElementById("pairSheetLater")?.addEventListener'):]
    back = back[:back.index("});") + 3]
    assert 'pairSheetView === "people"' in back and 'pairSheetView = "add"' in back and 'pairSheetView = "pick"' in back and "closePairSheet()" in back
    opening = APP[APP.index("async function openPairSheet()"):APP.index("function closePairSheet()")]
    assert 'pairSheetView = "pick"' in opening and "pairPeople = null" in opening


def test_a_new_friend_is_pulled_into_the_allies_list_right_away():
    follow_fn = APP[APP.index("async function pairFollowPerson("):APP.index("async function pairSheetAction(")]
    assert "relation?.friends" in follow_fn and '"/api/pair-quest/candidates"' in follow_fn and "loadFriendsCard()" in follow_fn


def test_the_new_styles_exist_and_stay_off_the_paint_diet():
    block = CSS[CSS.index("Парное задание → «Добавить друзей»"):]
    for selector in (".pair-option{", ".pair-option__icon", ".pair-option__text", ".pair-sheet--add .pair-sheet__hero", ".pair-sheet__add"):
        assert selector in block, selector
    assert "filter:" not in block and "backdrop-filter" not in block and "infinite" not in block


def test_the_new_buttons_are_counted_by_the_analytics_map():
    from webapp import feature_map as fm
    for label in ("pairSheetAdd", "pair-option=invite-link", "pair-option=pick-people", "pair-btn=follow-person"):
        assert fm.feature_for_click(label) == "pair_quest", label
        assert fm.click_label(label) != label, label
    assert fm.feature_for_api("GET /api/people/active") == "friends"
