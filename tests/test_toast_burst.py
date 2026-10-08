"""
Тост «⚡ Ещё 30 минут x2 Adam Coin» после серии быстрых отметок привычек: один, и в самом конце — после всех «+N Adam Coin».
Раньше он ставился в очередь после каждой отметки: закрыл 4 привычки — жди ещё 4 раза по 3.6 с (всего ~23 с вместо ~6 с).
Логику тостов запускаем в Node с виртуальными таймерами (код берётся прямо из app.js).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

PRELUDE = r"""
let now = 0, seq = 0; const timers = new Map();
const Date = { now: () => now };
function setTimeout(fn, ms) { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; }
function clearTimeout(id) { timers.delete(id); }
function advance(to) {
  for (;;) {
    let nextId = null, nextAt = Infinity;
    for (const [id, t] of timers) if (t.at < nextAt) { nextAt = t.at; nextId = id; }
    if (nextId === null || nextAt > to) break;
    const t = timers.get(nextId); timers.delete(nextId); now = nextAt; t.fn();
  }
  now = to;
}
const log = [];
const el = {
  className: "", style: {}, appendChild() {}, set innerHTML(v) {},
  classList: { add() {}, remove(c) { if (c === "is-visible") log.push({ t: now, hide: true }); } },
  set textContent(v) { log.push({ t: now, text: v }); },
};
const document = { getElementById: () => el, createElement: () => ({ style: {}, addEventListener() {} }), hidden: false };
function haptic() {}
function playChime() {}
let state = __STATE__;
"""

SCENARIO = r"""
const api = (function () {
  %(code)s
  return { showToast, scheduleBonusHint };
})();
const steps = %(steps)s;                       // [момент, "coin" | "action"], мс
for (const [at, what] of steps) {
  advance(at);
  if (what === "action") { api.showToast("Удалено", null, 4000, { label: "Отменить", onClick() {} }); continue; }
  api.showToast("+10 Adam Coin", "praise");      // как celebrateHabitCompletion
  api.scheduleBonusHint();                       // result.bonus_active
}
advance(60000);
console.log(JSON.stringify(log));
"""


def _toast_code():
    start = APP_JS.index("let toastTimer = null;")
    end = APP_JS.index("// ===================== PROFILE AVATAR / FRAMES")
    return APP_JS[start:end]


def _run(steps, state=None):
    steps = [[s, "coin"] if isinstance(s, int) else s for s in steps]
    script = PRELUDE.replace("__STATE__", json.dumps(state)) + SCENARIO % {"code": _toast_code(), "steps": json.dumps(steps)}
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _shown(log):
    return [(e["t"], e["text"]) for e in log if "text" in e]


needs_node = pytest.mark.skipif(NODE is None, reason="нужен node")


@needs_node
def test_four_quick_marks_give_four_coin_toasts_and_one_bonus_hint_at_the_end():
    shown = _shown(_run([0, 150, 300, 450]))
    texts = [t for _, t in shown]
    assert len(texts) == 5 and texts[:4] == ["+10 Adam Coin"] * 4 and "x2 Adam Coin" in texts[-1]
    assert sum("x2 Adam Coin" in t for t in texts) == 1
    coin_starts = [at for at, _ in shown[:4]]
    assert coin_starts == [0, 1200, 2400, 3600], "монеты идут подряд по 1.2 с, последняя — 2.2 с"
    hint_at = shown[-1][0]
    assert 5800 <= hint_at <= 6300, hint_at          # было ~23 с: x2 после каждой отметки по 3.6 с


@needs_node
def test_one_mark_shows_the_coin_then_one_hint():
    shown = _shown(_run([0]))
    assert [t for _, t in shown][0] == "+10 Adam Coin" and "x2 Adam Coin" in shown[1][1] and len(shown) == 2
    assert 2200 <= shown[1][0] <= 2700


@needs_node
def test_separate_bursts_each_get_their_own_hint():
    shown = _shown(_run([0, 200, 20000, 20200]))
    hints = [at for at, text in shown if "x2 Adam Coin" in text]
    assert len(hints) == 2 and hints[0] < 20000 < hints[1]


@needs_node
def test_a_toast_with_an_undo_button_is_never_cut_short_by_coins():
    log = _run([[0, "action"], [100, "coin"]])
    coin_at = next(e["t"] for e in log if e.get("text") == "+10 Adam Coin")
    assert coin_at == 4000, "кнопка «Отменить» живёт свои 4 с, монеты ждут своей очереди"


@needs_node
def test_no_hint_when_every_habit_of_the_day_is_already_done():
    done = {"habits": [{"completed": True}, {"completed": True}]}
    open_ = {"habits": [{"completed": True}, {"completed": False}]}
    assert not [1 for _, text in _shown(_run([0, 200], state=done)) if "x2 Adam Coin" in text]
    assert len([1 for _, text in _shown(_run([0, 200], state=open_)) if "x2 Adam Coin" in text]) == 1


def test_source_uses_the_single_hint_scheduler_and_not_a_timeout_per_mark():
    assert "scheduleBonusHint();" in APP_JS
    assert 'setTimeout(() => showToast("⚡️ Ещё 30 минут x2 Adam Coin' not in APP_JS
    flush = APP_JS[APP_JS.index("function flushBonusHint()"):][:420]
    assert "toastActive || toastQueue.length" in flush, "ждём, пока монеты за отметки показаны"
    assert "TOAST_PRAISE_QUEUED_MS = 1200" in APP_JS
