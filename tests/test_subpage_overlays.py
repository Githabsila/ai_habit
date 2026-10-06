"""
Подстраницы «Магазин», «Прогресс», «Настройки» (.subpage-overlay).

Скриншот с телефона: магазин открывался не на весь экран — начинался под карточкой игрока,
а нижняя часть уезжала за край окна, и последние строки нельзя было промотать из-под
нижней панели. Причина: в HTML оверлеи лежат внутри <section class="tab-panel">
(contain:layout) и <main id="content"> (transform) — оба становятся предком для
position:fixed. Поведение без браузера не проверить — здесь страж от возврата ошибки.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def test_overlays_are_moved_out_of_the_tab_panel_into_body():
    body = APP_JS[APP_JS.index("function initSubpageOverlays()"):][:1400]
    assert '["shopOverlay", "statsOverlay", "settingsOverlay"]' in body
    assert "document.body.appendChild(overlay)" in body
    # перенос раньше, чем обработчики открытия/закрытия
    assert body.index("document.body.appendChild(overlay)") < body.index('initSubpageOverlay("shopOverlay"')


def test_overlays_still_live_inside_the_profile_section_in_the_html():
    """Если их когда-нибудь вынесут в HTML — перенос в JS станет не нужен, но id обязаны остаться."""
    for overlay_id in ("shopOverlay", "statsOverlay", "settingsOverlay"):
        assert f'id="{overlay_id}"' in INDEX
        assert f'id="{overlay_id}Close"' in INDEX and f'id="{overlay_id}Backdrop"' in INDEX


def test_section_headings_inside_overlays_keep_their_profile_styling():
    """Оформление заголовков держалось на предке section[data-tab=profile] — в body его нет,
    поэтому каждое правило дублируется для .subpage-overlay."""
    assert CSS.count("section[data-tab=\"profile\"] .section-heading") >= 4
    for selector in (".subpage-overlay .section-heading h2{", ".subpage-overlay .section-heading{", ".subpage-overlay .section-heading h2::first-letter{"):
        assert selector in CSS, selector
    # шрифт заголовка (первое правило из группы с .profile-panel-title)
    assert re.search(r"\.subpage-overlay \.section-heading h2\{\s*font-family", CSS)


def test_overlay_body_does_not_chain_the_scroll_to_the_page_behind():
    body_rule = CSS[CSS.index(".subpage-overlay__body{"):][:250]
    assert "overscroll-behavior:contain" in body_rule


def test_transient_ui_stays_above_the_subpages():
    """Подстраницы теперь в body на z-index 1850–1860. Тост («Покупка совершена!»), окно
    нового уровня, итог дня и экран загрузки живут в body со значениями 100–1000 и иначе
    оказались бы под ними."""
    tail = CSS[CSS.rindex("Подстраницы (магазин, настройки, прогресс) вынесены в body"):]
    assert re.search(r"\.toast\{z-index:1880 !important\}", tail)
    assert re.search(r"\.loading-overlay,\.boot-retry-banner\{z-index:1890 !important\}", tail)
    assert re.search(r"\.levelup-overlay,\.impactday-overlay\{z-index:9000 !important\}", tail)
    assert 1880 > 1860 and 1890 > 1860 and 9000 > 1870   # .subpage-overlay--nested / .up-overlay


# ---------------------------------------------------------------------------
# Стрелка «назад» в шапке Telegram
# ---------------------------------------------------------------------------

def test_telegram_back_button_is_hidden_on_load_and_closes_the_top_subpage():
    """Чат с ADAM (ai_coach.js) показывает стрелку и возвращается на «/» обычным переходом —
    Telegram её не сбрасывал, и на главной вместо «✕» оставалась «←» без обработчика."""
    init = APP_JS[APP_JS.index("function initTelegram()"):][:2600]
    assert "tg.BackButton?.hide()" in init
    assert 'tg.onEvent?.("backButtonClicked", closeTopSubpage)' in init
    top = APP_JS[APP_JS.index("function closeTopSubpage()"):][:400]
    # вложенные (магазин, прогресс) лежат поверх настроек — закрываются первыми
    assert "subpage-overlay--nested" in top and "subpage-overlay__back" in top


def test_back_button_follows_the_open_subpages():
    sync = APP_JS[APP_JS.index("function syncBackButton()"):][:400]
    assert '.subpage-overlay.is-open' in sync and "back.show()" in sync and "back.hide()" in sync
    overlay = APP_JS[APP_JS.index("function initSubpageOverlay("):][:2600]
    assert 'overlay.classList.add("is-open"); syncBackButton();' in overlay
    assert overlay.count("syncBackButton()") >= 2          # при открытии и при закрытии
    assert "syncBackButton();\n}" in APP_JS[APP_JS.index("function closeAllSubpageOverlays()"):][:700]
