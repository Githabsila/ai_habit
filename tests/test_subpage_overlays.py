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


def test_overlay_body_has_no_rubber_band_and_does_not_chain_the_scroll():
    """Видео с телефона: в конце списка окно «прокручивалось ниже ещё» и после возврата вверх
    оставалось смещённым. overscroll-behavior:none убирает и растяжение (Android 12+), и передачу
    прокрутки странице под окном."""
    body_rule = CSS[CSS.index(".subpage-overlay__body{"):][:400]
    assert "overscroll-behavior:none" in body_rule and "min-height:0" in body_rule


def test_subpage_header_with_the_back_arrow_is_fixed_outside_the_scroller():
    """Шапка со стрелкой «назад» — вне прокручиваемого тела: не двигается ни при прокрутке, ни в конце списка."""
    header = CSS[CSS.index(".subpage-overlay__header{"):][:500]
    assert "flex:0 0 auto" in header and "touch-action:none" in header
    # отступы сверху и снизу одинаковые — стрелка по центру шапки по вертикали
    assert re.search(r"padding:calc\(env\(safe-area-inset-top,0px\) \+ 10px\) 16px 10px", header)
    back = CSS[CSS.index(".subpage-overlay__back{"):][:400]
    assert "display:grid;place-items:center" in back
    panel = CSS[CSS.index(".subpage-overlay__panel{"):][:400]
    assert "transform" not in panel, "окно открывается статично, без заезда"


def test_back_arrow_is_an_svg_centered_in_its_button():
    """Текстовая «←» сидела в кнопке чуть ниже центра — теперь SVG с симметричным viewBox."""
    for overlay_id in ("shopOverlayClose", "statsOverlayClose", "settingsOverlayClose", "userProfileClose"):
        button = INDEX[INDEX.index(f'id="{overlay_id}"'):][:520]
        assert "<svg" in button and "M19 12H5M11 6l-6 6 6 6" in button and 'aria-label="Назад"' in button, overlay_id
        assert "←" not in button[:button.index("</button>")], overlay_id


def test_every_open_starts_from_the_top():
    opening = APP_JS[APP_JS.index("function initSubpageOverlay("):][:900]
    assert "scroller.scrollTop = 0" in opening


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
    top = APP_JS[APP_JS.index("function closeTopSubpage()"):][:600]
    # закрывается верхнее окно по z-index: магазин/прогресс (1860) поверх настроек (1850), профиль игрока (1870) выше всех
    assert "getComputedStyle(overlay).zIndex" in top and "subpage-overlay__back" in top


def test_back_button_follows_the_open_subpages():
    sync = APP_JS[APP_JS.index("function syncBackButton()"):][:400]
    assert '.subpage-overlay.is-open' in sync and "back.show()" in sync and "back.hide()" in sync
    overlay = APP_JS[APP_JS.index("function initSubpageOverlay("):][:2600]
    assert 'overlay.classList.add("is-open"); syncBackButton();' in overlay
    assert overlay.count("syncBackButton()") >= 2          # при открытии и при закрытии
    assert "syncBackButton();\n}" in APP_JS[APP_JS.index("function closeAllSubpageOverlays()"):][:700]


# ---------------------------------------------------------------------------
# Линии в Настройках и пустая полоса внизу
# ---------------------------------------------------------------------------

def _tail_after(marker):
    return CSS[CSS.rindex(marker):]


def test_settings_hints_have_their_line_under_the_text_not_over_it():
    """Скрин: тонкая линия шла по ВЕРХНЕМУ краю подсказки и перечёркивала первую строку
    (общее правило «UNIVERSAL CARDS» рисует inset-подсветку у .theme-picker__hint).
    Подсветку у подсказок гасим, линия — border-bottom под текстом."""
    tail = _tail_after("Линии в Настройках.")
    rule = re.search(r"\.settings-card \.theme-picker__hint\{([^}]*)\}", tail).group(1)
    assert "box-shadow:none !important" in rule
    assert "border-bottom:1px solid var(--adam-line)" in rule
    assert re.search(r"padding:0 \d+px \d+px", rule)            # воздух между текстом и линией


def test_gender_block_has_a_single_line_under_its_text():
    """«Как к тебе обращаться»: раньше две линии подряд — под заголовком и сверху подсказки.
    Линию под заголовком убираем, остаётся одна — под текстом."""
    assert 'class="section-heading section-heading--text-first"' in INDEX
    block = INDEX[INDEX.index("section-heading--text-first"):][:400]
    assert "Как к тебе обращаться" in block and 'id="genderHint"' in block
    rule = re.search(r"\.settings-card \.section-heading--text-first\{([^}]*)\}", _tail_after("Линии в Настройках.")).group(1)
    assert "border-bottom:0 !important" in rule


def test_every_settings_hint_uses_the_fixed_class():
    hints = re.findall(r'<p class="theme-picker__hint"', INDEX)
    assert len(hints) == 5       # все подсказки Настроек лежат в .settings-card и получают правило выше
    card = INDEX[INDEX.index('<div class="settings-card">'):]
    assert card.count('class="theme-picker__hint"') == 5


def test_tab_bottom_clearance_is_one_value_for_all_tabs():
    """Под последним блоком вкладки был запас 126 (#app) + 20 (#content) + отступ секции —
    до ~180px пустой полосы над плавающей панелью (80px). Теперь панель + ~20px."""
    tail = _tail_after("Запас снизу у вкладок.")
    assert re.search(r"#app\{padding-bottom:calc\(env\(safe-area-inset-bottom,0px\) \+ 104px\) !important\}", tail)
    assert "#content{padding-bottom:0 !important}" in tail


def test_last_block_of_a_subpage_has_no_extra_margin():
    tail = _tail_after("Окна «Магазин»/«Настройки»/«Прогресс»: у последнего блока")
    assert ".subpage-overlay__body > :last-child{margin-bottom:0 !important}" in tail
