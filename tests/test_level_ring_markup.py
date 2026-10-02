"""
Кольцо уровня в шапке: вокруг него не должно проступать светлого квадрата.

Причина (воспроизведена в браузере): filter:drop-shadow на самой дуге
(.level-ring__fill) WebView рисует отдельным прямоугольным слоем 104x104
поверх анимированного фона шапки. Поэтому на кольце не должно быть
CSS-filter, а свечение делают дуги .level-ring__glow без фильтра.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
MARKER = "Кольцо уровня: свечение без CSS-filter"


def _css():
    return (STATIC / "style.css").read_text(encoding="utf-8")


def test_glow_arcs_are_in_markup_before_the_main_arc():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    glow_positions = [m.start() for m in re.finditer(r'class="level-ring__fill level-ring__glow', html)]
    main_position = html.index('id="levelRingFill"')
    assert len(glow_positions) == 2
    assert all(p < main_position for p in glow_positions)  # свечение под основной дугой


def test_js_moves_glow_arcs_together_with_the_main_arc():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '.level-ring__glow' in js
    assert "ringParts" in js


def test_ring_override_comes_after_every_legacy_filter_rule():
    """Каскад: финальный блок с filter:none должен идти ПОСЛЕ всех старых правил
    с drop-shadow на дуге/бейдже — иначе новый drop-shadow ниже по файлу
    снова вернёт квадрат."""
    css = _css()
    marker = css.rfind(MARKER)
    assert marker > 0, "нет финального блока без фильтров для кольца уровня"

    legacy = [
        m.start()
        for m in re.finditer(r"level-ring__(?:fill|badge)[^{}]*\{[^{}]*drop-shadow", css)
    ]
    assert legacy, "тест устарел: старых правил с drop-shadow уже нет"
    assert all(position < marker for position in legacy)

    block = css[marker:]
    assert re.search(r"circle\.level-ring__fill[^{}]*\{[^{}]*filter:\s*none\s*!important", block)
    assert re.search(r"\.level-ring__badge\s*\{[^{}]*filter:\s*none\s*!important", block)
    assert "drop-shadow" not in block.split("*/", 1)[1]  # в самом блоке (после комментария) фильтров нет
