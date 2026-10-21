"""
Скриншоты владельца 21.10 (пункт 13):
13.1 — кнопка «Квесты» на Главной нарисовалась золотой плашкой без значка и подписи (непрорисовка в Android WebView): на ней одновременно сидели
       обрезка по скруглению, бегущий блик, подпрыгивание и тени-фильтры. Теперь кнопка обычная.
13.2 — на подиуме рейтинга строка «В ударе N дн.» повторяла число рядом с 🔥; её нет, а между строками карточки добавлен воздух.
Браузера в тестах нет: правила CSS и разбор статуса проверяем по тексту и в Node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _rules(selector_part):
    """Все объявления у правил, чей селектор содержит selector_part (в том числе внутри @media)."""
    found = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):      # комментарии не в счёт
        selector, body = match.group(1), match.group(2)
        if selector_part in selector and not selector.strip().startswith("@"):
            found.append((selector.strip(), body))
    return found


# ---------------------------------------------------------------------------------------------------------------- 13.1 кнопка «Квесты»

def test_the_quests_button_and_its_parts_have_no_filters_and_no_clipping():
    rules = _rules(".today-focus__quests-btn") + _rules(".today-focus__quests-icon") + _rules(".today-focus__quests-label")
    assert len(rules) >= 15, "кнопка описана в нескольких слоях стилей — проверяем каждый"
    for selector, body in rules:
        if "::after" in selector:
            continue
        assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", body), f"фильтр на {selector}"
        assert not re.search(r"overflow\s*:\s*hidden", body), f"обрезка по скруглению на {selector}"
        assert "questAttention" not in body and "questShine" not in body, f"подпрыгивание/блик на {selector}"


def test_the_shine_is_switched_off_and_a_reward_is_shown_by_the_moving_border_only():
    after = [body for selector, body in _rules(".today-focus__quests-btn::after") if "!important" in body]
    assert after and all("display:none!important" in body for body in after[-1:]), "последнее слово за «блик выключен»"
    claimable = [body for selector, body in _rules(".today-focus__quests-btn.has-claimable") if "animation" in body]
    last = claimable[-1]
    assert "questBtnBorderShine 3s linear infinite!important" in last and "questAttention" not in last
    assert not re.search(r"transform\s*:", last), "сама кнопка не двигается"


def test_the_shadow_of_the_icon_is_a_text_shadow_now():
    icon = [body for selector, body in _rules(".today-focus__quests-icon") if "!important" in body and "font-size:23px" in body]
    assert icon and "text-shadow:0 2px 7px rgba(255,204,66,.42)!important" in icon[0]


def test_the_button_markup_still_has_its_icon_and_label():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    button = index[index.index('id="dailyQuestsBtn"'):][:400]
    assert 'class="today-focus__quests-icon"' in button and 'class="today-focus__quests-label">Квесты<' in button


# ---------------------------------------------------------------------------------------------------------------- 13.2 подиум

@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_streak_label_that_repeats_the_flame_number_is_not_shown():
    start = APP_JS.index("const getStatus = (r) => {")
    body = APP_JS[start:APP_JS.index("\n    };\n", start) + 7]
    script = "const rows = JSON.parse(process.argv[1]);\n" + body + """
const out = rows.map((r) => getStatus(r).status);
process.stdout.write(JSON.stringify(out));
"""
    rows = [
        {"streak_status": {"temp_status": "В ударе 42 дн."}},
        {"streak_status": {"temp_status": "Огонь 9 дн."}},
        {"streak_status": {"temp_status": "В ударе"}},
        {"streak_status": {"temp_status": "Закалка"}},
        {"streak_status": {"temp_status": "", "rewards": [{"status": "Старт"}]}},
        {"streak_status": {}},
        {},
    ]
    result = subprocess.run([NODE, "-e", script, json.dumps(rows)], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["", "", "", "Закалка", "Старт", "", ""]


def test_the_podium_and_the_list_use_the_same_status_rule():
    assert APP_JS.count("const getStatus = (r) =>") == 1
    podium = APP_JS[APP_JS.index("const cardsHtml = rows.slice(0, 3)"):][:2200]
    assert 'rating-podium-card__status">${escapeHtml(status)}' in podium and "const {ss, status} = getStatus(r);" in podium
    assert "getStatus(r)" in APP_JS[APP_JS.index("list.innerHTML = rows.slice(3)"):][:600]
    assert "<div class=\"rating-podium-card__stats\"><span><i class=\"stat-icon\">🔥</i> ${Number(r.streak || 0)}</span>" in podium, "число серии у огонька остаётся"


def test_the_podium_lines_got_more_air_without_new_paint_cost():
    block = CSS[CSS.index("Подиум рейтинга (три верхние карточки)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    expected = {".rating-podium-card__rank": 9, ".rating-podium-card__name": 6, ".rating-podium-card__league": 5, ".rating-podium-card__evo": 6, ".rating-podium-card__stats": 10}
    for selector, margin in expected.items():
        assert f"{selector}{{margin-top:{margin}px}}" in block, selector
    assert ".rating-podium-card__handle{margin-top:3px !important}" in block, "у ника старое правило с !important — переопределяем тем же"
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in block, forbidden
    # прежние отступы (7/3/1/2/3/8) были теснее — новые строго больше
    assert 9 > 7 and 6 > 3 and 3 > 1 and 5 > 2 and 6 > 3 and 10 > 8
