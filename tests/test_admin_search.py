"""
Админка: поиск по ID / @нику / имени и список «кому выдать медаль 🏅» (по просьбе: людей из инсты админ знает
по @нику, а не по Telegram ID; медаль выдаёт только админ). Рейтинг: вкладка «Сезон» прячет подиум «Всё время».
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import add_user, ban_user, set_access_status
from db.admin_gifts import grant_admin_badge
from db.admin_support import find_users_for_admin, get_user_support_card, list_active_users_for_admin
from db.core import connect

from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
ADMIN_HTML = (STATIC / "admin_panel.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _headers(uid):
    return {"Authorization": f"tma {sign_init_data(uid)}"}


def _user(uid, username, name, streak=0, seen_days_ago=0, status="approved", xp=0):
    add_user(uid, username, name)
    seen = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=seen_days_ago)).isoformat()
    conn = connect()
    conn.execute(
        "UPDATE users SET streak=?, xp=?, last_seen=?, access_status=? WHERE telegram_id=?",
        (streak, xp, seen, status, uid),
    )
    conn.commit()
    conn.close()


def _ids(users):
    return [u["telegram_id"] for u in users]


# --- поиск ------------------------------------------------------------------------------------

def test_search_by_id_by_username_with_or_without_at_and_case(uid):
    _user(uid, "InstaFan_Anna", "Анна")
    assert _ids(find_users_for_admin(str(uid))) == [uid]
    assert uid in _ids(find_users_for_admin("@instafan_anna"))
    assert uid in _ids(find_users_for_admin("instafan_anna"))
    assert uid in _ids(find_users_for_admin("  @INSTAFAN  "))


def test_search_by_name_and_by_in_app_handle(uid):
    _user(uid, "zz_tag_x", "Светлана Уникальная")
    conn = connect()
    conn.execute("UPDATE users SET handle=? WHERE telegram_id=?", ("fox_handle_77", uid))
    conn.commit()
    conn.close()
    assert uid in _ids(find_users_for_admin("светлана уник"))
    assert uid in _ids(find_users_for_admin("@fox_handle_77"))
    assert find_users_for_admin("") == [] and find_users_for_admin("@") == []


def test_exact_nick_comes_before_partial_matches(uid):
    _user(uid, "exact_nick_q1", "Первая", seen_days_ago=5)
    _user(uid + 700_001, "exact_nick_q1_longer", "Вторая", seen_days_ago=0)
    found = _ids(find_users_for_admin("exact_nick_q1"))
    assert found[:2] == [uid, uid + 700_001]


def test_like_wildcards_in_the_query_are_literal(uid):
    _user(uid, "wild_card_a1", "Аня")
    _user(uid + 700_002, "wildXcardXa1", "Боря")
    assert _ids(find_users_for_admin("wild_card")) == [uid]       # «_» — это подчёркивание, а не «любой символ»
    assert find_users_for_admin("100%real") == []


def test_search_result_marks_who_already_has_the_medal(uid):
    _user(uid, "badge_owner_q2", "Владелец")
    assert find_users_for_admin("badge_owner_q2")[0]["badge"] is False
    grant_admin_badge(uid, "За баги")
    assert find_users_for_admin("badge_owner_q2")[0]["badge"] is True
    assert get_user_support_card(uid)["badge"] is True


# --- «кому выдать медаль» ---------------------------------------------------------------------------

def test_active_list_has_recent_approved_users_by_streak(uid, monkeypatch):
    import config
    monkeypatch.setattr(config, "ADMIN_IDS", [uid + 700_009])
    _user(uid, "act_a", "Сильный", streak=30)
    _user(uid + 700_003, "act_b", "Средний", streak=7)
    _user(uid + 700_004, "act_old", "Давно", streak=99, seen_days_ago=20)
    _user(uid + 700_005, "act_new", "Анкета", streak=50, status="pending")
    _user(uid + 700_006, "act_ban", "Бан", streak=60)
    ban_user(uid + 700_006)
    _user(uid + 700_009, "act_adm", "Админ", streak=70)
    ids = _ids(list_active_users_for_admin(limit=500))
    assert uid in ids and uid + 700_003 in ids
    assert ids.index(uid) < ids.index(uid + 700_003), "длиннее серия — выше"
    for gone in (uid + 700_004, uid + 700_005, uid + 700_006, uid + 700_009):
        assert gone not in ids, gone


def test_active_list_shows_the_medal_flag_and_respects_the_limit(uid):
    _user(uid, "lim_a", "Один", streak=400_001)
    _user(uid + 700_007, "lim_b", "Два", streak=400_000)
    grant_admin_badge(uid, "x")
    top = list_active_users_for_admin(limit=2)
    assert _ids(top) == [uid, uid + 700_007]
    assert top[0]["badge"] is True and top[1]["badge"] is False
    assert len(list_active_users_for_admin(limit=1)) == 1


# --- маршруты ------------------------------------------------------------------------------------------

async def test_search_and_active_routes_are_for_admins_only(client, uid):
    _user(uid, "route_user", "Аня")
    for path in ("/api/admin/users/search?q=route_user", "/api/admin/users/active"):
        assert (await client.get(path)).status == 401
        assert (await client.get(path, headers=_headers(uid))).status == 403


async def test_routes_return_matches_for_the_admin(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    admin_id = uid + 700_020
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin_id})
    add_user(admin_id, "adm", "Админ")
    _user(uid, "route_hit_q3", "Аня", streak=3)
    r = await client.get("/api/admin/users/search?q=@route_hit_q3", headers=_headers(admin_id))
    body = await r.json()
    assert r.status == 200 and [u["telegram_id"] for u in body["users"]] == [uid]
    assert body["users"][0]["streak"] == 3 and body["users"][0]["badge"] is False
    empty = await (await client.get("/api/admin/users/search?q=", headers=_headers(admin_id))).json()
    assert empty == {"users": []}
    active = await client.get("/api/admin/users/active", headers=_headers(admin_id))
    assert active.status == 200 and isinstance((await active.json())["users"], list)


# --- панель и рейтинг -----------------------------------------------------------------------------------------

def test_admin_panel_searches_by_nick_and_lists_active_people_with_a_medal_button():
    assert 'placeholder="@ник, имя или Telegram ID"' in ADMIN_HTML and 'type="number"' not in ADMIN_HTML.split("userIdInput")[1][:120]
    assert "/api/admin/users/search?q=" in ADMIN_HTML and "/api/admin/users/active" in ADMIN_HTML
    assert 'id="activeUsersBox"' in ADMIN_HTML and "Кому выдать медаль" in ADMIN_HTML
    assert "data-badge=" in ADMIN_HTML and "giveBadge(parseInt(btn.dataset.badge, 10), loadActiveUsers)" in ADMIN_HTML
    assert "Медаль выдана" in ADMIN_HTML, "у кого медаль уже есть — кнопка не активна"


def test_season_scope_really_hides_the_all_time_podium():
    """display:grid у .rating-podium перебивал атрибут hidden — подиум «Всё время» висел над сезонным списком."""
    assert ".rating-podium[hidden]" in CSS and ".rating-rest-head[hidden]" in CSS
    rule = CSS[CSS.index(".rating-podium[hidden]"):][:160]
    assert "display:none !important" in rule


def test_match_list_never_shows_medal_buttons_by_accident():
    """found.map(userRowHtml) передавал индекс вторым аргументом: у 2-й и 3-й строки вместо «Открыть» вылезала «Выдать»."""
    assert "found.map(userRowHtml)" not in ADMIN_HTML
    assert "return userRowHtml(u, false)" in ADMIN_HTML and "return userRowHtml(u, true)" in ADMIN_HTML
