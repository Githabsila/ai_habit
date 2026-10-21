"""
Стрелка «к последнему сообщению» в чате с Адамом (п.10 владельца, 21.10): если человек начинал писать и не спускался вниз, стрелка висела
на одном месте и перекрывала растущее поле ввода. Теперь: пока поле в фокусе (человек пишет) стрелки нет, а когда она видна — стоит
ровно над полем ввода при любой его высоте. Чистые функции проверяем в Node, подключение — по исходникам.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")
COACH_HTML = (STATIC / "ai_miniapp_styled.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _helpers():
    start = COACH_JS.index("const SCROLL_DOWN_MIN_BOTTOM")
    return COACH_JS[start:COACH_JS.index("const SPARK_SVG")]


def _eval(expression):
    script = _helpers() + f"\nprocess.stdout.write(JSON.stringify({expression}));"
    return json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, encoding="utf-8").stdout)


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_arrow_sits_above_the_composer_however_tall_it_grows():
    # экран 800 px; поле ввода растёт вверх: верх поля 740 → 700 → 620 (три строки и больше)
    assert _eval("[scrollDownBottom(800, 740), scrollDownBottom(800, 700), scrollDownBottom(800, 620), scrollDownBottom(800, 560)]") == [72, 112, 192, 252]
    # пока верх поля неизвестен или поле «внизу» — прежняя высота, но не ниже
    assert _eval("[scrollDownBottom(800, 0), scrollDownBottom(0, 700), scrollDownBottom(800, 790), scrollDownBottom(800, 800), scrollDownBottom(undefined, undefined)]") == [62, 62, 62, 62, 62]
    assert _eval("scrollDownBottom(812.4, 700.2)") == 124, "округляется до целого пикселя"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_arrow_is_hidden_while_the_person_is_typing():
    assert _eval("[scrollDownVisible(true, false), scrollDownVisible(true, true), scrollDownVisible(false, false), scrollDownVisible(false, true), scrollDownVisible(undefined, false)]") == [True, False, False, False, False]


def test_the_chat_component_tracks_focus_and_measures_the_composer():
    assert "const [composerFocused, setComposerFocused] = useState(false);" in COACH_JS and "const inputAreaRef = useRef(null);" in COACH_JS
    textarea = COACH_JS[COACH_JS.index('React.createElement("textarea", {'):][:330]
    assert "onFocus: () => setComposerFocused(true)" in textarea and "onBlur: () => setComposerFocused(false)" in textarea
    assert 'React.createElement("div", { className: "input-area", ref: inputAreaRef },' in COACH_JS
    assert "scrollDownVisible(showScrollDown, composerFocused) && React.createElement(\"button\", {" in COACH_JS
    assert 'showScrollDown && React.createElement("button"' not in COACH_JS, "кнопка больше не зависит только от прокрутки"
    effect = COACH_JS[COACH_JS.index("const place = () => {"):][:1100]
    assert "area.getBoundingClientRect().top" in effect and "scrollDownBottom(window.innerHeight, top)" in effect
    assert "'--scroll-down-bottom'" in effect and "setTimeout(place, 260)" in effect, "клавиатура закрывается не мгновенно"
    assert "[input, composerFocused, showScrollDown, showTools]" in effect and "removeEventListener('resize', place)" in effect


def test_the_last_style_rule_takes_the_height_from_the_variable():
    tail = COACH_HTML[COACH_HTML.rindex("<style>"):]
    assert ".scroll-down-btn{bottom:var(--scroll-down-bottom,62px)!important}" in tail
    assert "@media(max-width:560px){.scroll-down-btn{bottom:var(--scroll-down-bottom,60px)!important}}" in tail
    assert COACH_HTML.rindex("--scroll-down-bottom") > COACH_HTML.rindex("bottom:60px!important"), "переопределение идёт после старых правил с !important"
    for old in re.findall(r"\.scroll-down-btn\{[^}]*bottom:\s*(\d+)px\s*!important", COACH_HTML):
        assert old.isdigit()                                     # старые значения остаются запасными, новая переменная идёт после них
    assert "ai_coach.js?v=20261021_COMPOSE_V56" in COACH_HTML
