"""
Мелочи оформления (05.11, просьба владельца: «чтобы и на ПК, и на телефоне всё было красиво»).

- Пакеты ответов в магазине шли в порядке с сервера: +10, +50, +100, +20. Теперь сортируются по количеству.
- На ПК окна-подразделы и чат с Адамом растягивались на всю ширину; теперь — колонка по центру, как главное приложение.
- Пока окно открыто на ПК, страница под ним не прокручивается (не было второй полосы прокрутки).
Проверено глазами на 360/390/430/480/1100 px; здесь — страховка, что правила не потеряются при следующих правках.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
COACH = (STATIC / "ai_miniapp_styled.html").read_text(encoding="utf-8")


def test_answer_packs_are_sorted_by_amount():
    assert ".sort((a, b) => packAmount(a) - packAmount(b))" in APP


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")
def test_the_sort_puts_packs_in_ascending_order():
    match = re.search(r"const packAmount = \(it\) => [^\n]+;", APP)
    assert match
    script = match.group(0) + """
const items = [{name: "+10 ответов", payload: "10"}, {name: "+50 ответов", payload: "50"}, {name: "+100 ответов", payload: "100"}, {name: "+20 ответов", payload: "20"}];
console.log(items.sort((a, b) => packAmount(a) - packAmount(b)).map(packAmount).join(","));"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "10,20,50,100"


def test_subpages_and_chat_are_a_centered_column_on_wide_screens():
    wide = CSS[CSS.rindex("@media (min-width:640px)"):]
    assert ".subpage-overlay__panel{" in wide and "max-width:600px" in wide and "margin-inline:auto" in wide
    assert "transform" not in wide.split("}")[0], "без transform: он ломает position:fixed внутри окон"
    assert "html:has(.subpage-overlay:not([hidden])){overflow:hidden}" in CSS
    block = COACH[COACH.index('<style id="adam-wide-screen">'):]
    assert ".app-container{max-width:640px !important;margin-inline:auto !important;" in block
