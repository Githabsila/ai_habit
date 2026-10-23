"""
Кнопки раскрытой привычки (23.10.2026, п.4 владельца): ✏️ раскрывала «Изменить / Время / Заметка / ✕», а спрятать их было нечем — только
перезапуск приложения, и торопливый тап «закрыть» мог попасть по ✕. Теперь на месте ✏️ стоит стрелка «Скрыть», кнопки прячет и двойной тап по
привычке, и тап мимо; открытой остаётся одна строка; ✕ удаляет только вторым тапом (красное «Удалить?»), после удаления есть «Отменить».
Браузера в тестах нет — разметку и подключение событий проверяем по тексту скрипта, а логику — в Node с поддельным DOM.
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


def _function(header, indent="  "):
    """Текст функции из IIFE app.js (отступ в два пробела): от заголовка до закрывающей скобки на том же отступе."""
    start = APP_JS.index(header)
    return APP_JS[start:APP_JS.index(f"\n{indent}}}\n", start) + len(indent) + 2]


def _line(pattern):
    return re.search(pattern, APP_JS, flags=re.M).group(0)


# ---------------------------------------------------------------------------------------------------------------- разметка

def test_the_expanded_row_has_an_arrow_where_the_pencil_was_and_a_bar_with_the_actions():
    html = _function("function habitItemHtml(h) {")
    arrow = html.index('class="habit-item__more is-open" data-action="toggle-actions" aria-label="Скрыть кнопки" aria-expanded="true"')
    bar = html.index('<div class="habit-item__bar">')
    assert arrow < bar, "стрелка — в первой строке, на месте ✏️; действия — отдельной строкой ниже"
    actions = [html.index(f'data-action="{name}"', bar) for name in ("edit", "edit-time", "delete")]
    assert actions == sorted(actions) and html.index("${noteBtn}", bar) < html.index('data-action="delete"', bar), "✕ — последняя, в дальнем углу"
    assert "ICON_CHEVRON_UP" in html and 'aria-expanded="false">✏️</button>' in html, "свёрнутая строка — прежняя ✏️"
    assert "habitActionsHintId === h.id" in html and "Дважды нажми на привычку — я спрячу кнопки" in html, "подсказка от первого лица Адама"
    assert html.index("</div>", bar) < html.index("habit-item__bar-hint"), "подсказка под кнопками"


def test_the_arrow_icon_is_drawn_and_the_pencil_button_still_toggles():
    assert 'const ICON_CHEVRON_UP = uiIcon("M6.5 14.5l5.5-5.5 5.5 5.5", "2.6");' in APP_JS
    click = APP_JS[APP_JS.index('if (action === "toggle-actions") {'):][:330]
    assert 'collapseHabitActions([habitId], "arrow")' in click and "openHabitActions(habitId)" in click


def test_delete_asks_first_and_deletes_only_on_the_second_tap():
    branch = APP_JS[APP_JS.index('} else if (action === "delete") {'):][:520]
    assert "habitDeleteStep(habitId)" in branch and 'step === "arm"' in branch and 'step === "confirm"' in branch
    assert branch.index("armHabitDelete(habitId, btn)") < branch.index("deleteHabitWithUndo(habitId)"), "удаление — только в ветке подтверждения"
    assert branch.count("deleteHabitWithUndo(") == 1
    assert _line(r"^  const HABIT_DELETE_CONFIRM_GAP_MS = 400;") and _line(r"^  const HABIT_DELETE_ARM_MS = 3500;")
    arm = _function("function armHabitDelete(habitId, btn) {")
    assert 'btn.textContent = "Удалить?"' in arm and 'haptic("warning")' in arm and 'trk("habit_delete_armed"' in arm
    assert "innerHTML" not in arm, "текст кнопки ставим как текст"


def test_a_redraw_never_leaves_an_invisible_armed_state():
    """Список перерисовали — ✕ снова обычная; значит и «ждёт подтверждения» нужно сбросить, иначе следующий тап удалил бы без вопроса."""
    for header in ("function renderHabits() {", "function renderSingleHabit(habitId) {"):
        assert "if (armedHabitDelete) disarmHabitDelete();" in _function(header)[:260], header


# ---------------------------------------------------------------------------------------------------------------- события

def test_double_tap_hides_the_buttons_and_never_fires_on_the_buttons_themselves():
    block = APP_JS[APP_JS.index("const isHabitControl = (el) =>"):][:3200]
    assert 'closest("button, input, select, textarea, a, label")' in block, "двойной тап по ✓, ✕, ✎ ничего не делает"
    dbl = block[block.index('habitList.addEventListener("dblclick"'):block.index('habitList.addEventListener("touchend"')]
    assert 'collapseHabitActions([Number(li.dataset.id)], "double")' in dbl and "habitCollapsedAt < 600" in dbl
    assert dbl.index("collapseHabitActions") < dbl.index("toggleHabitFullText(li)"), "у раскрытой строки двойной тап прячет, у свёрнутой — текст"
    touch = block[block.index('habitList.addEventListener("touchend"'):block.index("// Тап мимо раскрытой привычки")]
    assert "now - lastHabitTextTap.time < 420" in touch and 'collapseHabitActions([habitId], "double")' in touch
    assert "expandedHabitActionIds.has(habitId) || !!target.closest(\".habit-item__title\")" in touch, "свёрнутая строка реагирует только на название"
    assert "{ passive: false }" in touch


def test_a_tap_elsewhere_hides_the_buttons_after_the_tap_itself_is_handled():
    assert 'document.addEventListener("click", (e) => { collapseOnOutsideTap(e.target instanceof Element ? e.target : null); });' in APP_JS
    outside = _function("function collapseOnOutsideTap(target) {")
    assert "!target.isConnected" in outside, "тап уже перестроил список (✏️ у соседней привычки) — ничего не трогаем"
    assert "target.closest(HABIT_TAP_IGNORE)" in outside
    ignore = _line(r"^  const HABIT_TAP_IGNORE = .*$")
    for needle in ('[role="dialog"]', ".subpage-overlay", ".center-modal", '[class*="sheet"]', '[class*="modal"]', ".toast"):
        assert needle in ignore, needle
    assert "click" in APP_JS[APP_JS.index("const isHabitControl"):][:2600] and "pointerdown" not in outside, "клик, а не pointerdown: нажатие на соседнюю кнопку не съедается"


def test_only_one_row_stays_open_and_only_that_row_is_redrawn_on_hide():
    opened = _function("function openHabitActions(habitId) {")
    assert opened.index("expandedHabitActionIds.clear()") < opened.index("expandedHabitActionIds.add(habitId)")
    assert 'trk("habit_actions", "open", null)' in opened and "takeHabitActionsHint()" in opened
    closed = _function("function collapseHabitActions(ids, how) {")
    assert "renderSingleHabit(id)" in closed and 'trk("habit_actions", how, null)' in closed
    assert 'if (how !== "outside") haptic("light")' in closed, "тап мимо — без вибрации: человек трогал другое"


# ---------------------------------------------------------------------------------------------------------------- стили

def test_the_styles_sit_before_the_chest_block_and_stay_flat():
    start = CSS.index("Раскрытая привычка (app.js::habitItemHtml, 23.10")
    end = CSS.index("Окно «Сундук» (открытие сундука месяца")
    assert start < end, "срезы стилей в других тестах кончаются на окне «Сундук»: блок лежит до него"
    css = CSS[start:end]
    for forbidden in ("filter", "infinite", "animation", "will-change", "backface"):
        assert forbidden not in css, forbidden
    assert ".habit-item__bar{flex:0 0 100%;display:flex" in css, "действия — отдельной строкой под названием"
    assert ".habit-item__bar .habit-item__del{margin-left:auto}" in css, "✕ в дальнем углу"
    armed = re.search(r"\.habit-item__del\.is-armed\{([^}]*)\}", css).group(1)
    assert "background:#EF4444" in armed and "opacity:1 !important" in armed, "красная и видимая (обычная ✕ приглушена !important)"
    assert ".habit-item__more.is-open{" in css and ".habit-item__bar-hint{" in css


# ---------------------------------------------------------------------------------------------------------------- учёт

def test_the_new_events_are_in_the_analytics():
    from db.tracking import EVENT_LABELS
    from webapp.feature_map import FEATURES
    for name in ("habit_actions", "habit_delete_armed"):
        assert name in EVENT_LABELS, name
        owners = [f.id for f in FEATURES if name in f.events]
        assert owners == ["habits"], (name, owners)
    steps = set(re.findall(r'trk\("habit_actions", "?(\w+)"?', APP_JS)) | set(re.findall(r'collapseHabitActions\(\[[^\]]*\], "(\w+)"\)', APP_JS))
    assert {"open", "arrow", "double"} <= steps, steps
    assert 'collapseHabitActions([...expandedHabitActionIds].filter((id) => id !== keep), "outside")' in APP_JS


# ---------------------------------------------------------------------------------------------------------------- логика в Node

@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_hide_and_delete_logic_in_node():
    names = ("function takeHabitActionsHint() {", "function disarmHabitDelete() {", "function armHabitDelete(habitId, btn) {",
             "function habitDeleteStep(habitId, now = Date.now()) {", "function openHabitActions(habitId) {",
             "function collapseHabitActions(ids, how) {", "function collapseOnOutsideTap(target) {")
    functions = "\n".join(_function(h) for h in names)
    consts = "\n".join(_line(rf"^  const {name} = .*$") for name in
                       ("HABIT_DELETE_ARM_MS", "HABIT_DELETE_CONFIRM_GAP_MS", "HABIT_ACTIONS_HINT_KEY", "HABIT_ACTIONS_HINT_TIMES", "HABIT_TAP_IGNORE"))
    script = r"""
let timers = []; let now = 1000; let nextId = 1;
const realNow = Date.now; Date.now = () => now;
function setTimeout(fn, ms) { const id = nextId++; timers.push({ id, at: now + ms, fn }); return id; }
function clearTimeout(id) { timers = timers.filter((t) => t.id !== id); }
function advance(ms) {
  const target = now + ms;
  for (;;) {
    const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    timers = timers.filter((t) => t !== due); now = due.at; due.fn();
  }
  now = target;
}
const store = {};
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
const ICON_X = "<svg/>";
const expandedHabitActionIds = new Set();
let habitActionsHintId = null, habitCollapsedAt = 0, armedHabitDelete = null;
%s
const events = [], rendered = [], buzz = [];
const trk = (name, step) => events.push(name + ":" + (step ?? ""));
const haptic = (kind) => buzz.push(kind);
const renderHabits = () => rendered.push("all");
const renderSingleHabit = (id) => { rendered.push(id); return true; };
const buttons = {};
const delButton = (id) => (buttons[id] = buttons[id] || { cls: new Set(), html: "<svg/>", label: "Удалить",
  classList: { add(c) { delButton(id).cls.add(c); }, remove(c) { delButton(id).cls.delete(c); } },
  set textContent(v) { this.html = "T:" + v; }, set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; },
  setAttribute(k, v) { if (k === "aria-label") this.label = v; } });
const document = { querySelector: (sel) => { const m = sel.match(/data-id="(\d+)"/); return m ? delButton(Number(m[1])) : null; } };
%s
const out = {};

// 1. первый тап по ✕ только спрашивает; торопливый второй не подтверждает; через 0,5 с — подтверждение
out.step1 = habitDeleteStep(7);
armHabitDelete(7, delButton(7));
out.armed = { html: delButton(7).html, armedClass: delButton(7).cls.has("is-armed"), label: delButton(7).label, buzz: buzz.slice(), events: events.slice() };
advance(100); out.hurried = habitDeleteStep(7);
advance(300); out.confirm = habitDeleteStep(7);
out.otherHabit = habitDeleteStep(8);
// 2. сам собой сбрасывается через 3,5 с и возвращает ✕
advance(3200); out.afterTimeout = { step: habitDeleteStep(7), html: delButton(7).html, armedClass: delButton(7).cls.has("is-armed"), label: delButton(7).label, armed: armedHabitDelete };

// 3. подсказка про двойной тап — только первые три раза
const hints = [];
for (let i = 0; i < 5; i++) { openHabitActions(1); hints.push(habitActionsHintId); }
out.hints = hints; out.counter = store.adam_habit_actions_hint;

// 4. открытой остаётся одна: вторая привычка прячет первую
openHabitActions(2); out.accordion = [...expandedHabitActionIds];

// 5. спрятать: только нужные строки перерисовываются, учёт и вибро по способу
rendered.length = 0; events.length = 0; buzz.length = 0;
out.hideArrow = { result: collapseHabitActions([2], "arrow"), open: [...expandedHabitActionIds], rendered: rendered.slice(), events: events.slice(), buzz: buzz.slice() };
out.hideAgain = collapseHabitActions([2], "arrow");
openHabitActions(3); rendered.length = 0; events.length = 0; buzz.length = 0;
const row = (id) => ({ dataset: { id: String(id) } });
const target = (opts) => ({ isConnected: opts.connected !== false, closest: (sel) => sel.includes(".habit-item") ? (opts.row ? row(opts.row) : null) : (opts.ignored ? {} : null) });
// 6. тап мимо: в окне/шторке — нет; по тому же ряду — нет; по сорванному из DOM элементу — нет; по другому месту — да, без вибрации
out.outsideInSheet = collapseOnOutsideTap(target({ ignored: true }));
out.outsideSameRow = collapseOnOutsideTap(target({ row: 3 }));
out.outsideDetached = collapseOnOutsideTap(target({ connected: false }));
out.stillOpen = [...expandedHabitActionIds];
out.outsideOtherRow = collapseOnOutsideTap(target({ row: 9 }));
out.afterOutside = { open: [...expandedHabitActionIds], rendered: rendered.slice(), events: events.slice(), buzz: buzz.slice(), collapsedAtSet: habitCollapsedAt === now };
out.nothingOpen = collapseOnOutsideTap(target({ row: 9 }));
process.stdout.write(JSON.stringify(out));
""" % (consts, functions)
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["step1"] == "arm"
    assert out["armed"] == {"html": "T:Удалить?", "armedClass": True, "label": "Подтвердить удаление", "buzz": ["warning"], "events": ["habit_delete_armed:"]}
    assert out["hurried"] == "wait", "через 100 мс — это дрожащий двойной тап"
    assert out["confirm"] == "confirm", "через 0,4 с и позже — подтверждение"
    assert out["otherHabit"] == "arm", "подтверждение относится только к той привычке, у которой нажали ✕"
    assert out["afterTimeout"] == {"step": "arm", "html": "<svg/>", "armedClass": False, "label": "Удалить", "armed": None}
    assert out["hints"] == [1, 1, 1, None, None] and out["counter"] == "3", "подсказка — первые три раза"
    assert out["accordion"] == [2]
    assert out["hideArrow"] == {"result": True, "open": [], "rendered": [2], "events": ["habit_actions:arrow"], "buzz": ["light"]}
    assert out["hideAgain"] is False
    assert out["outsideInSheet"] is False and out["outsideSameRow"] is False and out["outsideDetached"] is False
    assert out["stillOpen"] == [3]
    assert out["outsideOtherRow"] is True
    assert out["afterOutside"] == {"open": [], "rendered": [3], "events": ["habit_actions:outside"], "buzz": [], "collapsedAtSet": True}
    assert out["nothingOpen"] is False
