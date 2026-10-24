"""
24.10.2026, жалоба владельца: открыл «Квесты» сам, сразу после двух отметок, взял первый сундук — окно квестов само закрылось, на секунду
показалась Главная и окно открылось снова, с анимацией «балл летит в месячную полосу». Два изъяна:
  • лишний тап по «Забрать» (кнопка оживает не сразу, по ней стучат с запасом) попадал в строку квеста под сундуком и уводил к её шагу;
    теперь полсекунды после закрытия сундука тапы по окну квестов не считаются;
  • отметки привычек оставляли «ждущее» окно квестов, а открытое вручную окно его не забирало — оно всплывало второй раз позже;
    теперь вручную открытое окно показывает изменение прямо в себе.
Браузера в тестах нет — разметку и подключение проверяем по тексту скрипта, логику — в Node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
NODE = shutil.which("node")


def _function(header):
    """Функция верхнего уровня app.js: от заголовка до закрывающей скобки в начале строки."""
    start = APP_JS.index(header)
    return APP_JS[start:APP_JS.index("\n}\n", start) + 3]


def _line(pattern):
    return re.search(pattern, APP_JS, flags=re.M).group(0)


def test_every_way_to_close_a_chest_stamps_the_time_and_the_quest_list_checks_it():
    assert "chestClosedAt = Date.now();" in _function("function hideChestOverlay(overlay) {")
    handlers = APP_JS[APP_JS.index("function initDailyQuestActions() {"):][:3200]
    click = handlers[handlers.index('list.addEventListener("click"'):][:200]
    assert "if (questTapIsStray()) return;" in click, "строка квеста под сундуком не реагирует на лишний тап"
    assert 'dailyQuestsBackdrop")?.addEventListener("click", () => { if (!questTapIsStray()) closeQuestsOverlay(); })' in handlers
    assert _line(r"^const QUEST_STRAY_TAP_MS = \d+;") and _line(r"^const questTapIsStray = .*$")


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_stray_taps_and_the_manual_open_in_node():
    consts = "\n".join(_line(rf"^(?:const|let) {name} = .*$") for name in ("QUEST_STRAY_TAP_MS", "chestClosedAt", "questTapIsStray"))
    functions = "\n".join(_function(h) for h in ("function hideChestOverlay(overlay) {", "function openQuestsFromButton() {"))
    script = r"""
let now = 10000; Date.now = () => now;
const setTimeout = () => 0;
const log = [];
const trk = (name) => log.push("trk:" + name);
const state = { daily_quests: ["fresh-quests"], month_quests: { fresh: true } };
let questsPlanHold = null, questsPending = null, questsWindowTimer = 5;
const clearInterval = (id) => log.push("clearInterval:" + id);
const clearPlanHold = () => { questsPlanHold = null; log.push("clearPlanHold"); };
const openQuestsOverlay = () => log.push("overlay");
const openQuestsWindow = (payload) => log.push("window:" + JSON.stringify(payload));
const overlayEl = { classList: { remove() {} }, setAttribute() {} };
%s
%s
const out = {};

// 1. сразу после закрытия сундука тап считается лишним, через 0,7 с — уже нет
out.beforeAnyChest = questTapIsStray();
hideChestOverlay(overlayEl);
out.right = questTapIsStray();
now += 300; out.after300 = questTapIsStray();
now += 450; out.after750 = questTapIsStray();

// 2. «Квесты» открыты вручную, а окно после отметок ещё ждёт: показываем изменение прямо в нём и ничего не оставляем на потом
questsPending = { before: ["b"], monthBefore: { m: 1 }, after: ["stale"], monthAfter: { stale: true } };
openQuestsFromButton();
out.pending = { log: log.slice(), pendingLeft: questsPending, timer: questsWindowTimer };

// 3. ждущего окна нет — просто открывается как раньше
log.length = 0; questsWindowTimer = null;
openQuestsFromButton();
out.plain = log.slice();

// 4. окно про план ждало своих двух минут — по-прежнему показывается сразу
log.length = 0; questsPlanHold = { before: ["p"], monthBefore: null, after: ["old"], monthAfter: null };
openQuestsFromButton();
out.held = log.slice();
process.stdout.write(JSON.stringify(out));
""" % (consts, functions)
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["beforeAnyChest"] is False
    assert out["right"] is True and out["after300"] is True and out["after750"] is False
    window = out["pending"]["log"]
    assert window[0] == "trk:quests_window_open" and window[1] == "clearInterval:5"
    shown = json.loads(window[2].removeprefix("window:"))
    assert shown == {"before": ["b"], "monthBefore": {"m": 1}, "after": ["fresh-quests"], "monthAfter": {"fresh": True}}, "показывается свежее состояние, а не снятое при отметке"
    assert out["pending"]["pendingLeft"] is None and out["pending"]["timer"] is None
    assert out["plain"] == ["trk:quests_window_open", "overlay"]
    assert out["held"][0] == "trk:quests_window_open" and out["held"][1] == "clearPlanHold" and out["held"][2].startswith("window:")
