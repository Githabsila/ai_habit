"""
Отмена отметки привычки, сторона приложения (24.10.2026): двойной тап по зелёной галочке открывает окно «Убрать отметку о выполнении?»,
«Да, снять» шлёт POST /api/habits/{id}/uncomplete и обновляет экран тем же патчем, что и обычная отметка. Одиночный тап подсказывает,
как это делается. Браузера в тестах нет — разметку проверяем по тексту, логику — в Node с поддельными таймерами и страницей.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import db.habit_undo as undo
from db.sim_lab import SCENES
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(header, indent="  "):
    start = APP_JS.index(header)
    return APP_JS[start:APP_JS.index(f"\n{indent}}}\n", start) + len(indent) + 2]


def _line(pattern):
    return re.search(pattern, APP_JS, flags=re.M).group(0)


# ---------------------------------------------------------------------------------------------------------------- разметка

def test_the_done_check_can_be_tapped_and_the_open_check_still_marks():
    html = _function("function habitItemHtml(h) {")
    check = html[html.index('<button class="${checkClass}"'):][:360]
    assert 'data-action="${h.completed ? "undo-hint" : (isCounter ? "progress" : "complete")}"' in check
    assert "disabled" not in check, "у отключённой кнопки не бывает двойного тапа"
    assert "Дважды нажми, чтобы убрать отметку" in check


def test_the_tap_on_the_done_check_goes_to_the_double_tap_handler_before_any_request():
    branch = APP_JS[APP_JS.index('if (action === "undo-hint") {'):][:120]
    assert "habitDoneCheckTap(habitId)" in branch and "return;" in branch
    assert APP_JS.index('if (action === "undo-hint") {') < APP_JS.index('if (action === "complete") {'), "до запросов на отметку"


def test_the_question_window_has_the_owners_wording_and_two_answers():
    block = INDEX[INDEX.index('id="habitUndoSheet"'):][:1100]
    assert "<h3 id=\"habitUndoHeading\">Убрать отметку о выполнении?</h3>" in block
    assert re.search(r'id="habitUndoKeep">Оставить</button>', block) and re.search(r'id="habitUndoConfirm">Да, снять</button>', block)
    assert 'role="dialog"' in block and 'aria-modal="true"' in block and 'id="habitUndoName"' in block


def test_the_question_window_says_things_once():
    block = INDEX[INDEX.index('id="habitUndoSheet"'):][:1100]
    title = re.search(r'id="habitUndoHeading">([^<]+)<', block).group(1)
    note = re.search(r'class="habit-undo-note">([^<]+)<', block).group(1)
    keep = re.search(r'id="habitUndoKeep">([^<]+)<', block).group(1)
    confirm = re.search(r'id="habitUndoConfirm">([^<]+)<', block).group(1)
    assert not shared(title, note, keep, confirm), (title, note, keep, confirm)


def test_the_window_is_wired_styled_and_has_a_simulation():
    assert "initHabitUndo();" in APP_JS and "function initHabitUndo() {" in APP_JS
    assert "habit_undo: () =>" in APP_JS and any(key == "habit_undo" for key, *_ in SCENES)
    for selector in (".habit-undo-card", ".habit-undo-name", ".habit-undo-note"):
        assert selector in CSS, selector
    assert 'trk("habit_undo_open"' in APP_JS and 'trk("habit_undo_cancel"' in APP_JS and 'trk("habit_undo_denied"' in APP_JS


def test_every_server_refusal_has_a_friendly_text_in_both_languages():
    for code in (undo.ERROR_NOT_COMPLETED, undo.ERROR_TOO_LATE, undo.ERROR_LIMIT, undo.ERROR_COINS_SPENT):
        assert len(re.findall(rf"^\s+{code}: \"[^\"]+\",", APP_JS, flags=re.M)) == 2, code


def test_the_texts_speak_in_the_first_person_of_adam():
    assert "Нажал случайно? Тапни по галочке дважды — я уберу отметку" in APP_JS
    assert "Убрал отметку и забрал ${result.coins_back} Adam Coin обратно" in APP_JS


# ---------------------------------------------------------------------------------------------------------------- логика

@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_double_tap_and_the_answers_in_node():
    names = ("function habitDoneCheckTap(habitId, now = Date.now()) {", "function openHabitUndo(habitId, { habit = null, sim = false } = {}) {",
             "function closeHabitUndo() {", "async function confirmHabitUndo() {")
    functions = "\n".join(_function(h) for h in names)
    consts = "\n".join(_line(rf"^  const {name} = .*$") for name in ("HABIT_UNDO_DOUBLE_TAP_MS", "HABIT_UNDO_HINT_TEXT")) + "\n" + \
        "\n".join(_line(rf"^  let {name} = .*$") for name in ("lastDoneCheckTap", "doneCheckHintTimer", "habitUndo"))
    script = r"""
let timers = []; let now = 1000; let nextId = 1;
Date.now = () => now;
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
const requestAnimationFrame = (fn) => fn();
const clearInterval = (id) => { log.cleared.push(id); };
let questsWindowTimer = 11, questsPending = { before: 1 }, remindSheetTimer = 22, friendsData = { viewer_done: true };
const renderFriendsCard = () => { log.friendsRendered++; };
const log = { toasts: [], buzz: [], events: [], patches: [], api: [], bootstraps: 0, cleared: [], friendsRendered: 0 };
const haptic = (kind) => log.buzz.push(kind);
const showToast = (text, kind) => log.toasts.push(kind + ":" + text);
const trk = (name, step) => log.events.push(name + ":" + (step ?? ""));
const applyActionPatch = (result) => { log.patches.push(result.coins_back); state.habits[0].completed = false; };
const friendlyError = (err) => "ERR " + err.data.error;
const loadBootstrap = async () => { log.bootstraps++; };
let bonusHintTimer = 77;
let apiPlan = { ok: { coins_back: 30 } };
const api = async (path, options) => { log.api.push(options.method + " " + path); if (apiPlan.fail) { const e = new Error(apiPlan.fail); e.data = { error: apiPlan.fail }; throw e; } return apiPlan.ok; };
const state = { habits: [{ id: 5, title: "Зарядка", completed: true }, { id: 6, title: "Чтение", completed: false }] };
const els = {};
const el = (id) => (els[id] = els[id] || { id, hidden: true, disabled: false, textContent: "", attrs: {}, cls: new Set(),
  classList: { add(c) { el(id).cls.add(c); }, remove(c) { el(id).cls.delete(c); }, toggle(c, on) { on ? el(id).cls.add(c) : el(id).cls.delete(c); } },
  setAttribute(k, v) { el(id).attrs[k] = v; } });
const document = { getElementById: (id) => ["habitUndoSheet", "habitUndoName", "habitUndoConfirm"].includes(id) ? el(id) : null };
%s
%s
const out = {};
const opened = [];
const realOpen = openHabitUndo;

// 1. одиночный тап: вибро и, через паузу, подсказка; окно не открывается
out.single = habitDoneCheckTap(5);
advance(300); out.beforeHint = log.toasts.slice();
advance(300); out.afterHint = log.toasts.slice(); out.windowAfterSingle = habitUndo;
// 2. два тапа подряд: открывается окно, подсказки нет
log.toasts.length = 0;
now += 1000;
habitDoneCheckTap(5); advance(200);
out.double = habitDoneCheckTap(5); advance(1000);
out.doubleToasts = log.toasts.slice(); out.windowAfterDouble = habitUndo && habitUndo.id;
out.name = el("habitUndoName").textContent; out.opened = { hidden: el("habitUndoSheet").hidden, aria: el("habitUndoSheet").attrs["aria-hidden"], cls: [...el("habitUndoSheet").cls] };
out.openEvents = log.events.slice();
// 3. окно уже открыто: второе не открывается
out.reopen = openHabitUndo(5);
closeHabitUndo(); advance(300);
out.closed = { hidden: el("habitUndoSheet").hidden, aria: el("habitUndoSheet").attrs["aria-hidden"], cls: [...el("habitUndoSheet").cls], window: habitUndo };
// 4. два тапа с большой паузой — это два одиночных, окна нет; тапы по разным привычкам — тоже
log.toasts.length = 0; now += 2000;
habitDoneCheckTap(5); advance(800); habitDoneCheckTap(5); advance(800);
out.slowTaps = { toasts: log.toasts.length, window: habitUndo };
habitDoneCheckTap(5); advance(100); habitDoneCheckTap(6); advance(1000);
out.otherHabit = habitUndo;
// 5. невыполненную привычку убирать нельзя
out.notDone = openHabitUndo(6);
out.unknown = openHabitUndo(99);
// 6. «Да, снять»: запрос, патч, тост с монетами, подсказка про ×2 отменена
log.events.length = 0; log.buzz.length = 0; log.toasts.length = 0;
openHabitUndo(5);
async function run() {
  await confirmHabitUndo();
  out.confirm = { api: log.api.slice(), patches: log.patches.slice(), toasts: log.toasts.slice(), bonusHint: bonusHintTimer, window: habitUndo, buzz: log.buzz.slice(), events: log.events.slice(),
    queued: { quests: questsWindowTimer, pending: questsPending, remind: remindSheetTimer, cleared: log.cleared.slice(), viewerDone: friendsData.viewer_done, friendsRendered: log.friendsRendered } };
  // без монет — короткий тост
  apiPlan = { ok: { coins_back: 0 } }; log.toasts.length = 0; state.habits[0].completed = true; openHabitUndo(5); await confirmHabitUndo();
  out.noCoins = log.toasts.slice();
  // сервер отказал: понятный текст, учёт причины, экран перечитан
  apiPlan = { fail: "undo_limit" }; log.toasts.length = 0; log.events.length = 0; state.habits[0].completed = true; openHabitUndo(5); await confirmHabitUndo();
  out.denied = { toasts: log.toasts.slice(), events: log.events.slice(), bootstraps: log.bootstraps, window: habitUndo };
  // двойной тап по «Да, снять» запрос не дублирует
  apiPlan = { ok: { coins_back: 5 } }; log.api.length = 0; state.habits[0].completed = true; openHabitUndo(5);
  await Promise.all([confirmHabitUndo(), confirmHabitUndo()]);
  out.once = log.api.slice();
  // симуляция: ничего не отправляется
  log.api.length = 0; log.toasts.length = 0;
  openHabitUndo(0, { habit: { id: 0, title: "Демо", completed: true }, sim: true }); await confirmHabitUndo();
  out.sim = { api: log.api.slice(), toasts: log.toasts.slice(), window: habitUndo };
  process.stdout.write(JSON.stringify(out));
}
run();
""" % (consts, functions)
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    hint = "success:Нажал случайно? Тапни по галочке дважды — я уберу отметку"
    assert out["single"] == "single" and out["beforeHint"] == [] and out["afterHint"] == [hint] and out["windowAfterSingle"] is None
    assert out["double"] == "double" and out["doubleToasts"] == [] and out["windowAfterDouble"] == 5
    assert out["name"] == "Зарядка"
    assert out["opened"] == {"hidden": False, "aria": "false", "cls": ["is-open"]}
    assert out["openEvents"] == ["habit_undo_open:"]
    assert out["reopen"] is False
    assert out["closed"] == {"hidden": True, "aria": "true", "cls": [], "window": None}
    assert out["slowTaps"] == {"toasts": 2, "window": None}, "пауза длиннее 0,45 с — это два одиночных тапа"
    assert out["otherHabit"] is None, "тапы по разным привычкам двойным не считаются"
    assert out["notDone"] is False and out["unknown"] is False
    assert out["confirm"]["api"] == ["POST /api/habits/5/uncomplete"] and out["confirm"]["patches"] == [30]
    assert out["confirm"]["toasts"] == ["success:Убрал отметку и забрал 30 Adam Coin обратно"]
    assert out["confirm"]["bonusHint"] is None and out["confirm"]["window"] is None
    assert out["confirm"]["buzz"][-1] == "medium"
    assert out["confirm"]["queued"] == {"quests": None, "pending": None, "remind": None, "cleared": [11, 22], "viewerDone": False, "friendsRendered": 1},         "окна «Квесты дня» и «Напомнить друзьям», ждавшие после отметки, сняты; карточка друзей знает, что отметок не осталось"
    assert out["noCoins"] == ["success:Убрал отметку"]
    assert out["denied"] == {"toasts": ["error:ERR undo_limit"], "events": ["habit_undo_open:", "habit_undo_denied:undo_limit"], "bootstraps": 1, "window": None}
    assert out["once"] == ["POST /api/habits/5/uncomplete"], "двойной тап по «Да, снять» — один запрос"
    assert out["sim"] == {"api": [], "toasts": ["success:Так выглядит отмена — в симуляции ничего не меняется"], "window": None}
