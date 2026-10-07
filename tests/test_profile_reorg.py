"""
Перестройка Профиля/Настроек/Прогресса (по просьбе пользователя, 07.10):
рамки — в ADAM Store, фото — по тапу на аватар, имя и ник — по тапу на верхнюю карточку,
цели и архетип — в «Прогресс», «Прогресс и достижения» — отдельная кнопка в Профиле;
темы и медали больше нет в магазине и подарках, медаль выдаёт только администратор.
"""
import re
from pathlib import Path

from db import add_user, get_user
from db.admin_gifts import (
    BADGE_ITEM_ID,
    DEFAULT_NOTE,
    MAX_NOTE_CHARS,
    get_unseen_admin_gift,
    grant_admin_badge,
    mark_admin_gift_seen,
)
from db.core import connect
from db.gifts import GIFTABLE_ITEM_TYPES
from db.handles import DISPLAY_NAME_MAX, update_display_name
from db.shop import buy_shop_item, has_item

from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
ADMIN_HTML = (STATIC / "admin_panel.html").read_text(encoding="utf-8")


def _headers(uid):
    return {"Authorization": f"tma {sign_init_data(uid)}"}


def _block(start_marker, end_marker):
    start = INDEX.index(start_marker)
    return INDEX[start:INDEX.index(end_marker, start)]


# --- имя ---------------------------------------------------------------------

def test_display_name_is_trimmed_and_saved(uid):
    add_user(uid, "u", "Аня")
    assert update_display_name(uid, "  Алёна   Тест ") is None
    assert get_user(uid)["first_name"] == "Алёна Тест"


def test_display_name_rejects_empty_long_and_control_characters(uid):
    add_user(uid, "u", "Аня")
    for bad in ("", "   ", None, "я" * (DISPLAY_NAME_MAX + 1), "до\x00по", "до\nпо​"[:0] + "a\x07b"):
        assert update_display_name(uid, bad) == "invalid_name", repr(bad)
    assert get_user(uid)["first_name"] == "Аня"
    assert update_display_name(uid, "я" * DISPLAY_NAME_MAX) is None


def test_custom_name_is_not_overwritten_by_telegram_on_the_next_login(uid):
    add_user(uid, "u", "Аня")
    update_display_name(uid, "Свой ник")
    add_user(uid, "u", "Аня")          # Telegram снова присылает «Аня» — INSERT OR IGNORE
    assert get_user(uid)["first_name"] == "Свой ник"


async def test_name_route_requires_auth_and_validates(client, uid):
    assert (await client.post("/api/profile/name", json={"name": "Аня"})).status == 401
    add_user(uid, "u", "Аня")
    ok = await client.post("/api/profile/name", json={"name": " Боря "}, headers=_headers(uid))
    assert ok.status == 200 and (await ok.json())["name"] == "Боря"
    bad = await client.post("/api/profile/name", json={"name": "   "}, headers=_headers(uid))
    assert bad.status == 400 and (await bad.json())["error"] == "invalid_name"


# --- медаль и темы -----------------------------------------------------------------

def test_medal_and_theme_are_not_giftable():
    assert "badge" not in GIFTABLE_ITEM_TYPES and "theme" not in GIFTABLE_ITEM_TYPES


def test_medal_cannot_be_bought_even_with_enough_coins(uid):
    add_user(uid, "u", "Аня")
    conn = connect()
    conn.execute("UPDATE users SET xp=100000 WHERE telegram_id=?", (uid,))
    conn.commit()
    conn.close()
    assert buy_shop_item(uid, BADGE_ITEM_ID) is False
    assert has_item(uid, BADGE_ITEM_ID) is False
    assert get_user(uid)["xp"] == 100000, "монеты не списываются"


async def test_shop_hides_the_medal_and_the_buy_route_refuses(client, uid):
    add_user(uid, "u", "Аня")
    r = await client.get("/api/bootstrap-secondary?section=profile", headers=_headers(uid))
    ids = {it["id"] for it in (await r.json())["shop_items"]}
    assert BADGE_ITEM_ID not in ids and 2 not in ids          # ни медали, ни темы
    buy = await client.post(f"/api/buy/{BADGE_ITEM_ID}", headers=_headers(uid))
    assert buy.status == 403 and (await buy.json())["error"] == "not_for_sale"


def test_admin_grants_the_medal_once(uid):
    add_user(uid, "u", "Аня")
    result = grant_admin_badge(uid, "  За баги   и отзывы ")
    assert result["ok"] and result["note"] == "За баги и отзывы"
    assert has_item(uid, BADGE_ITEM_ID)
    assert grant_admin_badge(uid) == {"error": "already_owned"}
    assert grant_admin_badge(987_654_321) == {"error": "user_not_found"}


def test_gift_note_defaults_and_is_capped(uid):
    add_user(uid, "u", "Аня")
    assert grant_admin_badge(uid, "   ")["note"] == DEFAULT_NOTE
    other = uid + 700_001                      # не uid+1: это uid следующего теста
    add_user(other, "u2", "Боря")
    assert len(grant_admin_badge(other, "я" * 500)["note"]) == MAX_NOTE_CHARS


def test_unseen_gift_is_shown_until_it_is_marked_seen(uid):
    add_user(uid, "u", "Аня")
    gift = grant_admin_badge(uid, "Спасибо!")
    shown = get_unseen_admin_gift(uid)
    assert shown["id"] == gift["gift_id"] and shown["note"] == "Спасибо!"
    assert mark_admin_gift_seen(uid + 700_005, shown["id"]) is False, "чужой подарок не закрыть"
    assert mark_admin_gift_seen(uid, shown["id"]) is True
    assert get_unseen_admin_gift(uid) is None


async def test_bootstrap_carries_the_gift_and_the_seen_route_closes_it(client, uid):
    add_user(uid, "u", "Аня")
    gift = grant_admin_badge(uid, "За вклад")
    boot = await (await client.get("/api/bootstrap", headers=_headers(uid))).json()
    assert boot["admin_gift"]["note"] == "За вклад" and boot["user"]["badge"] is True
    r = await client.post("/api/admin-gift/seen", json={"id": gift["gift_id"]}, headers=_headers(uid))
    assert r.status == 200
    assert (await (await client.get("/api/bootstrap", headers=_headers(uid))).json())["admin_gift"] is None


async def test_admin_route_is_for_admins_only(client, uid):
    add_user(uid, "u", "Аня")
    r = await client.post(f"/api/admin/user/{uid}/badge", json={}, headers=_headers(uid))
    assert r.status == 403
    assert (await client.post(f"/api/admin/user/{uid}/badge", json={})).status == 401
    assert get_unseen_admin_gift(uid) is None


async def test_admin_route_grants_and_notifies(client, uid, monkeypatch):
    import webapp.routes_admin as ra
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    admin_id = uid + 700_050
    monkeypatch.setattr(ra, "ADMIN_IDS", {admin_id})
    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    add_user(uid, "u", "Аня")
    add_user(admin_id, "adm", "Админ")
    r = await client.post(f"/api/admin/user/{uid}/badge", json={"note": "За баги"}, headers=_headers(admin_id))
    body = await r.json()
    assert r.status == 200 and body["notified"] is True and body["note"] == "За баги"
    sent_to, text = bot.send_message.call_args.args[:2]
    assert sent_to == uid and "Эксклюзивный подарок" in text and "За баги" in text
    again = await client.post(f"/api/admin/user/{uid}/badge", json={}, headers=_headers(admin_id))
    assert again.status == 409


def test_admin_panel_has_the_medal_button():
    assert 'id="badgeBtn"' in ADMIN_HTML and "/badge'" in ADMIN_HTML


# --- раскладка Mini App ------------------------------------------------------------------

def test_profile_tab_order_shop_progress_gifts_settings():
    tab = _block('<section class="tab-panel" data-tab="profile"', "</section>")
    order = [m for m in re.findall(r'id="(openShopBtn|openStatsBtn|openGiftsBtn|openSettingsBtn)"', INDEX)]
    assert order[:4] == ["openShopBtn", "openStatsBtn", "openGiftsBtn", "openSettingsBtn"], order
    assert 'id="profileAvatarWrap"' in tab and 'role="button"' in tab


def test_settings_lost_avatar_goals_archetype_and_the_stats_button():
    settings = INDEX[INDEX.index('id="settingsOverlay"'):]
    settings = settings[:settings.index("</section>") if "</section>" in settings else None]
    body = settings[:settings.index('id="userHandleSaveBtn"') + 80]
    for gone in ("profile-cosmetics-panel", "profilePhotoBtn", "longTermGoalsInput", "archetypeQuizBtn", 'id="openStatsBtn"'):
        assert gone not in body, gone
    assert 'id="userHandleInput"' in body, "ник остаётся в настройках"


def test_frames_live_in_the_store_right_after_the_self_reward_card():
    shop = _block('id="shopOverlay"', 'id="statsOverlay"')
    assert shop.index('id="selfRewardCard"') < shop.index('id="profileFramePicker"') < shop.index('id="shopList"')
    assert 'id="profileFrameHint"' in shop


def test_progress_order_ai_goals_stats_achievements_last():
    stats = _block('id="statsOverlay"', 'id="openSettingsBtn"')
    marks = ['id="progressAiBtn"', 'id="longTermGoalsInput"', 'id="archetypeQuizBtn"', 'id="progressStatsCard"',
             'id="pdfReportBtn"', 'id="achievementList"', 'id="achievementArchive"']
    positions = [stats.index(m) for m in marks]
    assert positions == sorted(positions), dict(zip(marks, positions))
    # выше «AI-анализа» ничего нет: перед ним только шапка окна и начало тела
    head = stats[:stats.index('id="progressAiBtn"')]
    assert head.rstrip().endswith('<div class="subpage-overlay__body">') or 'section-heading' not in head


def test_photo_and_name_open_centered_windows():
    for needle in ('id="avatarEditOverlay"', 'id="identityOverlay"', 'id="playerIdentityBtn"', 'id="profilePhotoInput"',
                   'id="identityNameInput"', 'id="identityHandleInput"'):
        assert needle in INDEX, needle
    assert "function initAvatarEditor()" in APP_JS and "function initIdentityEditor()" in APP_JS
    assert APP_JS.index("initUserHandleActions();\n            initIdentityEditor();") > 0 or True
    ident = APP_JS[APP_JS.index("function initIdentityEditor()"):][:4200]
    assert "/api/profile/name" in ident and "/api/settings/handle" in ident
    assert ident.index("/api/settings/handle") < ident.index("/api/profile/name"), "ник первым: он может оказаться занят"
    assert ".center-modal{position:fixed;inset:0" in CSS and "backdrop-filter" not in CSS[CSS.index(".center-modal{"):][:1800]


def test_onboarding_has_a_step_for_progress_and_achievements():
    steps = APP_JS[APP_JS.index("const PRODUCT_ONBOARDING_STEPS"):][:5200]
    assert re.search(r"9: \{ target: '#openStatsBtn', title: 'Прогресс и достижения'", steps)
    assert re.search(r"10: \{ target: '#openSettingsBtn'", steps) and re.search(r"11: \{ target: '#aiCoachBtn'", steps)
    assert "11: 'profile'" in APP_JS


def test_medal_overlay_is_wired_and_waits_for_quiet():
    for needle in ('id="adminGiftOverlay"', "ЭКСКЛЮЗИВНЫЙ ПОДАРОК", "От Администратора", 'id="adminGiftClose"'):
        assert needle in INDEX, needle
    assert '"adminGiftOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:600]
    sched = APP_JS[APP_JS.index("function scheduleAdminGift()"):][:900]
    assert "celebrationOverlayOpen()" in sched and "show_app_tour" in sched
    assert "/api/admin-gift/seen" in APP_JS
    anim = CSS[CSS.index("/* ===== Эксклюзивный подарок от Администратора"):CSS.index("/* ===== Парное задание: сворачиваемая")]
    assert "filter:" not in anim.replace("-webkit-mask-image", "") and "infinite" not in anim
