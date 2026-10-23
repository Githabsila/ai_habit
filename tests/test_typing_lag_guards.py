"""
Набор текста в поле (23.10.2026, п.5 владельца): при изменении названия привычки в поле «прыгали» пробелы и слова. Код приложения текст
не трогает (проверено в браузере: ни одной программной записи в value, ни одной долгой задачи), зато пока открыта клавиатура, за полем
крутились пять бесконечных декоративных анимаций и каждый кадр перерисовывали страницу; плюс карточка шторки лежала в композитном слое
(transform + will-change), а при каждом изменении размера окна вызывался scrollIntoView. Здесь — защиты от этого, и «ответ идёт дольше
обычного» в чате с Адамом (раньше при долгом ответе экран просто молчал).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8").replace("\r\n", "\n")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
PRIVACY = (STATIC / "privacy.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(header, indent="  "):
    start = APP_JS.index(header)
    return APP_JS[start:APP_JS.index(f"\n{indent}}}\n", start) + len(indent) + 2]


def test_decor_animations_pause_while_the_keyboard_is_open():
    keyboard = _function("function setKeyboardOpen(open) {")
    assert "setDecorAnimationsPaused(open)" in keyboard and keyboard.index("classList.toggle('kb-open', open)") < keyboard.index("setDecorAnimationsPaused(open)")
    pause = _function("function setDecorAnimationsPaused(paused) {")
    assert "timing.iterations !== Infinity" in pause and "document.getAnimations()" in pause
    assert "spinner" in pause and "aria-busy" in pause, "крутилки загрузки не замираем: они говорят, что приложение занято"
    assert "anim.pause()" in pause and "anim.play()" in pause and "pausedDecorAnimations.clear()" in pause
    assert "catch (_)" in pause, "нет getAnimations (старый WebView) — просто ничего не паузим"


def test_a_bottom_sheet_is_not_scrolled_into_view_on_every_resize():
    reveal = _function("function revealFocusedField() {")
    assert "field.closest('.feedback-sheet')" in reveal
    assert reveal.index("field.closest('.feedback-sheet')") < reveal.index("scrollIntoView"), "до прокрутки"


def test_title_inputs_do_not_let_the_keyboard_autocorrect_them():
    edit = re.search(r'<input[^>]*id="habitEditTitle"[^>]*>', INDEX).group(0)
    new = re.search(r'<input\s[^>]*id="newHabitInput"[^>]*>', INDEX, flags=re.S).group(0)
    for tag in (edit, new):
        assert 'autocomplete="off"' in tag and 'autocorrect="off"' in tag and 'spellcheck="false"' in tag and 'maxlength="50"' in tag
    assert 'enterkeyhint="done"' in edit


def test_the_edit_card_stands_without_a_composited_layer_once_open():
    start = CSS.index("Изменение привычки (app.js::openHabitEdit)")
    end = CSS.index("Окно «Сундук» (открытие сундука месяца")
    rule = ".habit-edit-sheet.is-open .habit-edit-card{transform:none;will-change:auto}"
    assert start < CSS.index(rule) < end, "блок до «Окна «Сундук»»: так устроены срезы стилей в тестах"
    assert CSS.index(rule) > CSS.index(".feedback-sheet.is-open .feedback-card{transform:translateY(0)"), "правило позже общего — оно и побеждает"
    block = CSS[start:end]
    for forbidden in ("filter", "infinite", "animation"):
        assert forbidden not in block, forbidden


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_only_endless_decorations_are_paused_and_they_resume():
    consts = re.search(r"^  const pausedDecorAnimations = new Set\(\);$", APP_JS, flags=re.M).group(0)
    script = r"""
%s
%s
const log = [];
const anim = (name, iterations, { state = "running", closest = () => null } = {}) => ({
  name, playState: state,
  effect: { getComputedTiming: () => ({ iterations }), target: { closest } },
  pause() { this.playState = "paused"; log.push("pause " + name); },
  play() { this.playState = "running"; log.push("play " + name); },
});
const list = [
  anim("orbit", Infinity), anim("flame", Infinity), anim("chestShake", 3), anim("fadeIn", 1),
  anim("spin", Infinity, { closest: (sel) => (sel.includes(".spinner") ? {} : null) }),
  anim("alreadyPaused", Infinity, { state: "paused" }),
];
let document = { getAnimations: () => list };
%s
setDecorAnimationsPaused(true);
const afterPause = list.map((a) => a.name + ":" + a.playState);
setDecorAnimationsPaused(true);                       // повторный вызов ничего не ломает
setDecorAnimationsPaused(false);
const afterResume = list.map((a) => a.name + ":" + a.playState);
setDecorAnimationsPaused(false);
// getAnimations нет вовсе — не падаем
const logBefore = log.length;
document = { getAnimations: undefined };
let threw = false;
try { setDecorAnimationsPaused(true); } catch (e) { threw = true; }
process.stdout.write(JSON.stringify({ afterPause, afterResume, log, threw }));
""" % (consts, "", _function("function setDecorAnimationsPaused(paused) {"))
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["afterPause"] == ["orbit:paused", "flame:paused", "chestShake:running", "fadeIn:running", "spin:running", "alreadyPaused:paused"]
    assert out["afterResume"] == ["orbit:running", "flame:running", "chestShake:running", "fadeIn:running", "spin:running", "alreadyPaused:paused"], \
        "возобновляем только то, что паузили сами"
    assert out["log"] == ["pause orbit", "pause flame", "play orbit", "play flame"]
    assert out["threw"] is False


# ---------------------------------------------------------------------------------------------------------------- чат с Адамом

def test_a_slow_answer_is_announced_instead_of_silence():
    assert "const SLOW_REPLY_MS = 12000;" in COACH_JS
    hook = COACH_JS[COACH_JS.index("const [slowReply, setSlowReply] = useState(false);"):][:420]
    assert "if (!loading) { setSlowReply(false); return undefined; }" in hook
    assert "setTimeout(() => setSlowReply(true), SLOW_REPLY_MS)" in hook and "clearTimeout(timer)" in hook and "[loading]" in hook
    note = COACH_JS[COACH_JS.index("loading && !greeting && slowReply"):][:420]
    assert "Отвечаю дольше обычного" in note and "я допишу ответ сам" in note, "голос Адама, от первого лица"
    assert note.index("thinking-note") < note.index("Отвечаю дольше обычного")


def test_the_privacy_page_describes_the_real_backups():
    assert "не более 10 последних копий" not in PRIVACY
    assert "по одной за день за последние две недели" in PRIVACY and "в закрытый чат Telegram" in PRIVACY
