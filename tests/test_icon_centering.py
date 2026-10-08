"""
Иконки в квадратах и кружках стоят ровно (по просьбе: «оцентрируй там, где есть неровности»).
Текстовые символы «✕ ✎ ✓ + ✦» у каждого шрифта имеют свои поля, поэтому они заменены рисованными SVG с симметричным viewBox.
Центровку измеряли в браузере: у всех заменённых иконок смещение от центра кнопки 0.0px.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")
COACH_HTML = (STATIC / "ai_miniapp_styled.html").read_text(encoding="utf-8")

X_PATH = "M6 6l12 12M18 6L6 18"
PLUS_PATH = "M12 5v14M5 12h14"
CHECK_PATH = "M5 12.5l4.5 4.5L19 7"
SPARK_PATH = "M12 2c.7 5.6 4.4 9.3 10 10-5.6.7-9.3 4.4-10 10-.7-5.6-4.4-9.3-10-10 5.6-.7 9.3-4.4 10-10Z"


def _path_box(d):
    """Рамка простого пути из относительных/абсолютных M, L, H, V, l, h, v, c, z — ровно тех, что используются в иконках."""
    x = y = 0.0
    xs, ys = [], []
    for cmd, args in re.findall(r"([MLHVlhvczC])([^MLHVlhvczC]*)", d):
        nums = [float(n) for n in re.findall(r"-?\d*\.?\d+", args)]
        if cmd == "M" or cmd == "L":
            x, y = nums[0], nums[1]
        elif cmd == "H":
            x = nums[0]
        elif cmd == "V":
            y = nums[0]
        elif cmd == "l":
            for i in range(0, len(nums), 2):          # несколько пар подряд = несколько отрезков
                x, y = x + nums[i], y + nums[i + 1]
                xs.append(x)
                ys.append(y)
            continue
        elif cmd == "h":
            x += nums[0]
        elif cmd == "v":
            y += nums[0]
        elif cmd == "c":
            for i in range(0, len(nums), 6):
                for j in (0, 2, 4):
                    xs.append(x + nums[i + j])
                    ys.append(y + nums[i + j + 1])
                x, y = x + nums[i + 4], y + nums[i + 5]
            continue
        xs.append(x)
        ys.append(y)
    return min(xs), max(xs), min(ys), max(ys)


def test_icon_geometry_is_centered_in_the_24_box():
    for name, d, tol in (("x", X_PATH, 0.01), ("plus", PLUS_PATH, 0.01), ("check", CHECK_PATH, 0.01), ("spark", SPARK_PATH, 0.3)):
        x0, x1, y0, y1 = _path_box(d)
        assert abs((x0 + x1) / 2 - 12) <= tol and abs((y0 + y1) / 2 - 12) <= tol, (name, (x0, x1, y0, y1))


def test_pencil_is_shifted_to_the_center_and_lifted_on_the_avatar():
    pencil_old = "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4"
    pencil = "M4.5 19.5h4L19.5 8.5l-4-4L4.5 15.5v4zM14 6l4 4"
    assert pencil_old not in INDEX and pencil_old not in APP_JS
    assert INDEX.count(pencil) == 2 and pencil in APP_JS
    x0, x1, y0, y1 = _path_box("M4.5 19.5h4L19.5 8.5l-4-4L4.5 15.5v4z")
    assert (x0 + x1) / 2 == 12 and (y0 + y1) / 2 == 12
    assert ".streak-profile-avatar__edit svg{transform:translateY(-1px)}" in CSS


def test_no_glyph_icons_left_in_the_buttons_and_circles():
    glyph_buttons = re.findall(
        r'<(?:button|span)\b[^>]*class="[^"]*(?:plan-icon-btn|habit-item__del|struggling-habit-banner__close|habit-item__suggest-dismiss|'
        r'daily-quest__done|add-collapse__icon|habit-add-form__submit-icon|daily-quests-modal__close|center-modal__close|'
        r'archetype-quiz-close|self-reward-modal__close)[^"]*"[^>]*>\s*[✕✎✓+×]\s*</',
        APP_JS + INDEX,
    )
    assert glyph_buttons == []
    for button_id in ("dailyQuestsClose", "avatarEditClose", "identityClose", "archetypeQuizClose",
                      "selfRewardModalClose", "selfRewardHistoryClose", "saveMainGoalBtn"):
        tag = INDEX[INDEX.index(f'id="{button_id}"'):][:900]
        body = tag[tag.index(">") + 1:tag.index("</button>")]
        assert "<svg" in body and not re.search(r"[✕✓]", body), button_id
    assert 'class="rating-item__react-btn rating-item__react-btn--sent" aria-hidden="true" title="Сегодня уже поддержал">✓<' not in APP_JS
    assert "? ICON_CHECK" in APP_JS, "галочка выполненной привычки"


def test_icons_are_defined_once_and_sized_in_css():
    assert APP_JS.count(f'uiIcon("{X_PATH}")') == 1 and f'uiIcon("{CHECK_PATH}"' in APP_JS
    assert INDEX.count(PLUS_PATH) == 3 and INDEX.count(X_PATH) == 6 and INDEX.count(CHECK_PATH) == 1
    assert ".ui-icon{display:block;flex:none;width:16px;height:16px;pointer-events:none}" in CSS
    centered = CSS[CSS.index(".plan-icon-btn,\n.habit-item__del"):][:420]
    assert "display:grid;place-items:center;padding:0" in centered


def test_check_marks_drawn_by_pseudo_elements_are_not_text_either():
    toggle = CSS[CSS.rindex(".plan-toggle:checked::after{"):][:400]
    assert 'content:""' in toggle and "data:image/svg+xml" in toggle and "✓" not in toggle
    dot = CSS[CSS.rindex(".pair-dot.is-done::after{"):][:400]
    assert 'content:""' in dot and "data:image/svg+xml" in dot and "✓" not in dot


def test_sparkle_diamond_is_an_svg_in_the_shop_and_the_chat():
    assert 'UI_SPARK' in APP_JS and '"✦"))}</span>' not in APP_JS
    assert f'd="{SPARK_PATH}"' in APP_JS and f'd="{SPARK_PATH}"' in COACH_JS
    assert 'className: "chat-toolbar-orb", dangerouslySetInnerHTML: { __html: SPARK_SVG }' in COACH_JS
    assert 'dangerouslySetInnerHTML: { __html: showTools ? X_SVG : SPARK_SVG }' in COACH_JS
    assert 'className: "icon-btn__glyph", dangerouslySetInnerHTML: { __html: SPARK_SVG }' in COACH_JS
    assert ".chat-toolbar-orb .ui-icon" in COACH_HTML and ".quick-toggle-icon .ui-icon" in COACH_HTML
    assert "ai_coach.js?v=20261008_ICONS_V52" in COACH_HTML


def test_chat_arrows_and_chevron_are_svg_too():
    assert 'className: "quick-arrow", dangerouslySetInnerHTML: { __html: ARROW_SVG }' in COACH_JS
    assert 'className: "quick-toggle-chevron", dangerouslySetInnerHTML: { __html: CHEVRON_SVG }' in COACH_JS
    backslash = chr(92)                                  # \u0432 \u0438\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u0435 \u0447\u0430\u0442\u0430 \u0441\u0438\u043c\u0432\u043e\u043b\u044b \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u044b escape-\u043f\u043e\u0441\u043b\u0435\u0434\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044f\u043c\u0438 \uXXXX
    assert backslash + "u2192" not in COACH_JS and backslash + "u2304" not in COACH_JS
    for d in ("M5 12h14M13 6l6 6-6 6", "M6 9l6 6 6-6"):
        x0, x1, y0, y1 = _path_box(d)
        assert (x0 + x1) / 2 == 12 and (y0 + y1) / 2 == 12, d


def test_ai_badge_is_pinned_in_the_plate_corner_and_does_not_push_the_star():
    """Правило `.tab-bar__item span:last-child{position:relative}` делало метку «есть сообщение» строчным элементом: она
    вставала рядом со звездой и сдвигала её. Метка закреплена по ID (специфичнее) в углу плашки (в меню 46×39)."""
    assert ".tab-bar__item span:last-child{position:relative" in CSS, "причина: общее правило для подписей вкладок"
    badge = CSS[CSS.rindex("#aiCoachBtn .tab-bar__badge{"):][:260]
    assert "position:absolute" in badge, "ID-селектор перебивает span:last-child"
    top = int(re.search(r"top:(\d+)px", badge).group(1))
    right = int(re.search(r"right:(\d+)px", badge).group(1))
    size = int(re.search(r"width:(\d+)px", badge).group(1))
    assert f"height:{size}px" in badge and size <= 8 and "0 0 0 1.5px" in badge, "точка и обводка меньше прежних 9px/2px"
    # центр точки с обводкой лежит внутри скруглённого угла плашки (радиус 15px) и не задевает концы звезды 22×22 по центру
    # плашки 46×39: верхний конец звезды (23; 8.5), правый (34; 19.5) — размеры сняты в браузере
    cx, cy, ring = 46 - right - size / 2, top + size / 2, size / 2 + 1.5
    assert ((cx - 31) ** 2 + (cy - 15) ** 2) ** 0.5 + ring <= 15, "метка целиком внутри плашки"
    assert ((cx - 23) ** 2 + (cy - 8.5) ** 2) ** 0.5 > ring + 3 and ((cx - 34) ** 2 + (cy - 19.5) ** 2) ** 0.5 > ring + 3
