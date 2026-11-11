"""
Жалоба владельца 11.11: после записи в «План дня» блок сам переезжал (порядок «План / Привычки» сбрасывался) и возвращался только
после перезапуска. Причина — две функции двигали блок привычек: applyHomeLayout (настройка «Привычки сверху») и applyNewcomerHomeLayout,
которая при каждой перерисовке возвращала блок к «родному месту», запомненному при первой отрисовке. Проверено в браузере (Playwright):
включил «Привычки сверху» → сохранил главную задачу → порядок держится.
"""
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


def _body(name):
    start = APP.index(f"function {name}(")
    return APP[start:APP.index("\n  }\n", start) if name == "applyNewcomerHomeLayout" else APP.index("\n}\n", start)]


def test_newcomer_layout_no_longer_remembers_a_stale_home_spot():
    assert "habitsHomeAnchor" not in APP
    body = _body("applyNewcomerHomeLayout")
    assert "applyHomeLayout()" in body                 # вернувшийся из «новичка» — по настройке, а не к старому месту


def test_home_layout_leaves_the_newcomer_order_alone():
    assert 'classList?.contains("is-newcomer")' in _body("applyHomeLayout")
