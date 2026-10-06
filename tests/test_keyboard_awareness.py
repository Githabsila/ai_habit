"""
Клавиатура и размер окна на телефоне (Telegram WebView).

Скриншот с телефона: на шаге 3 подсказок при наборе «Главной задачи дня» нижняя
панель (.tab-bar) всплывала над клавиатурой поверх самого поля, набранного не
видно. Поведение без браузера не проверить — здесь страж от возврата ошибок.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _function_body(name):
    start = APP_JS.index(f"function {name}(")
    end = APP_JS.index("\n  }\n", start)
    return APP_JS[start:end]


def test_bottom_bar_and_hint_card_hide_while_the_keyboard_is_open():
    assert re.search(r"html\.kb-open \.tab-bar\s*\{\s*display:\s*none\s*!important", CSS)
    assert re.search(r"html\.kb-open \.product-onboarding-hint\s*\{[^}]*opacity:\s*0\s*!important", CSS)
    # правило в самом конце файла — его не перебьют более поздние .tab-bar{...}
    tail = CSS[CSS.rindex("html.kb-open .tab-bar") + len("html.kb-open .tab-bar"):]
    assert not re.search(r"\.tab-bar\s*\{", tail)


def test_keyboard_state_follows_focus_in_text_fields():
    assert "const TEXT_FIELD_SELECTOR" in APP_JS
    for excluded in ("checkbox", "radio", "range", "file", "button", "submit", "hidden"):
        assert f":not([type={excluded}])" in APP_JS[APP_JS.index("const TEXT_FIELD_SELECTOR"):][:400], excluded
    assert "document.addEventListener('focusin'" in APP_JS and "document.addEventListener('focusout'" in APP_JS
    toggle = _function_body("setKeyboardOpen")
    assert "classList.toggle('kb-open', open)" in toggle
    # фокус может сразу перейти в другое поле — клавиатура при этом не закрывается
    focusout = APP_JS[APP_JS.index("document.addEventListener('focusout'"):][:500]
    assert "setTimeout" in focusout and "isTextField(document.activeElement)" in focusout


def test_keyboard_hidden_with_the_back_button_brings_the_bar_back():
    body = _function_body("onKeyboardViewportChange")
    assert "keyboardSawShrink" in body and "setKeyboardOpen(false)" in body
    assert "window.addEventListener('resize', onKeyboardViewportChange)" in APP_JS
    assert "window.visualViewport?.addEventListener('resize', onKeyboardViewportChange)" in APP_JS


def test_focused_field_is_scrolled_above_the_keyboard_even_when_the_page_is_locked():
    """При открытой подсказке страница заблокирована (position:fixed на body) — scrollIntoView
    молчит, поэтому сдвигаем top у body, не снимая блокировку."""
    body = _function_body("revealFocusedField")
    assert "if (scrollLockActive)" in body and "scrollLockY = " in body and "document.body.style.top" in body
    assert "scrollIntoView({ block: 'center'" in body


def test_hint_goes_back_next_to_its_target_after_keyboard_or_window_resize():
    assert "function refreshHintLayout" in APP_JS
    refresh = _function_body("refreshHintLayout")
    assert "positionHintCardNear(productHintTarget, el)" in refresh and "positionOnboardingSpotlight(productHintTarget)" in refresh
    assert "if (!open) refreshHintLayout();" in APP_JS
    # смена размера окна (Telegram раскрыл Mini App, поворот): карточка считалась под прежний размер
    assert "hintResizeTimer" in APP_JS and "if (!keyboardOpen) refreshHintLayout();" in APP_JS


def test_hint_pointing_at_the_bottom_bar_drops_the_text_cursor_first():
    """Иначе (на iOS кнопка не забирает фокус) панель останется спрятанной."""
    show = _function_body("showProductHint")
    assert "if (onTabBar) blurActiveTextField();" in show


def test_hint_card_leaves_room_for_the_bottom_bar():
    place = _function_body("positionHintCardNear")
    assert "(target.closest('.tab-bar') ? vh : vh - navClearance) - r.bottom" in place
