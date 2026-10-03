"""Вибро-отклик Mini App (webapp/static/haptics.js).

Просьба пользователя: вибрации при нажатиях кнопок и в уместных местах, но
не сильные, а приятные. Поведение модуля проверяем в Node с поддельными
window/document (счётчик вызовов Telegram.WebApp.HapticFeedback), подключение
и настройку — по исходникам страниц.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
HAPTICS = STATIC / "haptics.js"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
COACH_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
COACH_HTML = (STATIC / "ai_miniapp_styled.html").read_text(encoding="utf-8")

NODE = shutil.which("node")

# Скрипт-«стенд»: грузит haptics.js в подделанное окружение и прогоняет
# сценарии. Время и таймеры управляются вручную — результат детерминирован.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.env.HAPTICS_PATH, "utf8");

function makeEnv({ stored = null, telegram = true, loadedAt = 0 } = {}) {
  let now = loadedAt;
  const timers = [];
  const calls = [];
  const handlers = {};
  const store = {};
  if (stored !== null) store.adam_haptics = stored;
  const native = {
    impactOccurred: (s) => calls.push("impact:" + s),
    notificationOccurred: (s) => calls.push("notify:" + s),
    selectionChanged: () => calls.push("selection"),
  };
  const win = {
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    Telegram: telegram ? { WebApp: { HapticFeedback: native } } : undefined,
    getComputedStyle: (el) => ({ cursor: el.cursor || "auto" }),
  };
  const sandbox = {
    window: win,
    document: { addEventListener: (type, fn) => { handlers[type] = fn; } },
    navigator: { vibrate: (p) => calls.push("vibrate:" + JSON.stringify(p)) },
    setTimeout: (fn, ms) => { timers.push({ fn, ms, at: now + ms }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].dead = true; },
    Date: { now: () => now },
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return {
    api: win.AdamHaptics,
    calls,
    handlers,
    advance(ms) {
      now += ms;
      timers.sort((a, b) => a.at - b.at);
      while (timers.length && timers[0].at <= now) {
        const timer = timers.shift();
        if (!timer.dead) timer.fn();
      }
    },
    store,
  };
}

function el(opts = {}) {
  const self = {
    nodeType: 1,
    cursor: opts.cursor,
    closest(sel) {
      if (sel.indexOf("[disabled]") !== -1) return opts.skipped ? self : null;
      return opts.interactive === false ? null : self;
    },
    matches(sel) { return sel.indexOf(opts.type || "__none__") !== -1; },
  };
  return self;
}

const out = {};

// 1. Сопоставление старых стилей: ничего «тяжёлого».
{
  const e = makeEnv();
  const seen = [];
  for (const style of ["light", "soft", "rigid", "medium", "heavy", "error", "warning", "success", "select", "reward", "confirm", "tap"]) {
    e.advance(500);
    e.calls.length = 0;
    e.api.play(style);
    e.advance(200); // дать отработать эху confirm
    seen.push([style, e.calls.slice()]);
  }
  out.mapping = seen;
}

// 2. Защита от «трели», важные события не глушатся, награда сливается.
{
  const e = makeEnv();
  e.advance(1000);
  e.api.tap(); e.api.tap();
  out.throttled = e.calls.slice();
  e.calls.length = 0;
  e.advance(100);
  e.api.tap(); e.api.success();
  out.successNotThrottled = e.calls.slice();
  e.calls.length = 0;
  e.advance(100);
  e.api.tap(); e.advance(100); e.api.reward();
  out.rewardCoalesced = e.calls.slice();
  e.calls.length = 0;
  e.advance(600);
  e.api.reward();
  out.rewardAlone = e.calls.slice();
}

// 2b. Эхо подтверждения не смазывает следующий, более важный отклик.
{
  const e = makeEnv();
  e.advance(1000);
  e.api.confirm();
  e.advance(10);
  e.api.warning(); // ошибка сразу после тапа
  e.advance(300);
  out.echoCancelled = e.calls.slice();
  e.calls.length = 0;
  e.advance(1000);
  e.api.confirm();
  e.advance(300);
  out.echoPlays = e.calls.slice();
}

// 3. Выключатель и память между запусками.
{
  const e = makeEnv();
  e.advance(1000);
  e.api.setEnabled(false);
  e.api.tap(); e.api.success(); e.api.confirm();
  out.disabledCalls = e.calls.slice();
  out.disabledStored = e.store.adam_haptics;
  out.disabledFlag = e.api.isEnabled();
  const again = makeEnv({ stored: "0" });
  again.advance(1000);
  again.api.tap();
  out.restartedDisabled = [again.api.isEnabled(), again.calls.slice()];
  e.api.setEnabled(true);
  out.reenabled = [e.api.isEnabled(), e.store.adam_haptics];
}

// 4. Без Telegram — короткие импульсы navigator.vibrate.
{
  const e = makeEnv({ telegram: false });
  e.advance(1000);
  e.api.tap();
  out.fallback = e.calls.slice();
}

// 5. Глобальный клик.
{
  const e = makeEnv({ loadedAt: 0 });
  const click = (target, extra = {}) => e.handlers.click(Object.assign({ isTrusted: true, target }, extra));

  e.advance(1000);
  click(el());
  e.advance(1);
  out.clickTap = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  click(el());
  e.api.confirm(); // обработчик кнопки сам дал отклик
  e.advance(300);
  out.clickWithOwnHaptic = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  click(el(), { isTrusted: false }); e.advance(10);
  out.clickUntrusted = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  click(el({ skipped: true })); e.advance(10);
  out.clickDisabled = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  click(el({ interactive: false })); e.advance(10);
  out.clickPlainText = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  click(el({ interactive: false, cursor: "pointer" })); e.advance(10);
  out.clickPointerCursor = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  e.api.setEnabled(false);
  click(el()); e.advance(10);
  out.clickWhenDisabled = e.calls.slice();
  e.api.setEnabled(true);

  e.calls.length = 0; e.advance(500);
  e.handlers.change({ isTrusted: true, target: el({ type: "checkbox" }) });
  out.changeCheckbox = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  e.handlers.change({ isTrusted: true, target: el({ type: "text-not-listed" }) });
  out.changeOther = e.calls.slice();

  e.calls.length = 0; e.advance(500);
  e.handlers.input({ isTrusted: true, target: el({ type: "range" }) });
  out.inputRange = e.calls.slice();
}

// 6. «Призрачный» клик сразу после загрузки страницы.
{
  const e = makeEnv({ loadedAt: 0 });
  e.advance(100);
  e.handlers.click({ isTrusted: true, target: el() });
  e.advance(10);
  out.ghostClick = e.calls.slice();
}

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run():
    if not NODE:
        pytest.skip("Node.js не установлен — поведение haptics.js не проверить")
    done = subprocess.run(
        [NODE, "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={**os.environ, "HAPTICS_PATH": str(HAPTICS)},
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ---------- Поведение модуля ----------

def test_legacy_styles_map_to_gentle_effects(run):
    mapping = dict((style, calls) for style, calls in run["mapping"])
    # Обычное нажатие — самый мягкий удар, даже если зовут «light»/«rigid».
    for style in ("light", "soft", "rigid", "tap"):
        assert mapping[style] == ["impact:soft"], style
    # «medium» — подтверждение: лёгкий удар и тихое эхо.
    assert mapping["medium"] == ["impact:light", "impact:soft"]
    assert mapping["confirm"] == ["impact:light", "impact:soft"]
    # «heavy»/«error» превращаются в мягкое предупреждение, не в сильный удар.
    for style in ("heavy", "error", "warning"):
        assert mapping[style] == ["notify:warning"], style
    assert mapping["success"] == ["notify:success"]
    assert mapping["select"] == ["selection"]
    assert mapping["reward"] == ["impact:soft"]


def test_nothing_strong_is_ever_fired(run):
    fired = [c for _, calls in run["mapping"] for c in calls]
    for strong in ("impact:heavy", "impact:medium", "impact:rigid", "notify:error"):
        assert strong not in fired


def test_rapid_calls_do_not_stutter(run):
    assert run["throttled"] == ["impact:soft"]
    # Праздник не теряется из-за соседнего тапа.
    assert run["successNotThrottled"] == ["impact:soft", "notify:success"]


def test_confirm_echo_does_not_smear_a_following_warning(run):
    assert run["echoCancelled"] == ["impact:light", "notify:warning"]
    assert run["echoPlays"] == ["impact:light", "impact:soft"]


def test_reward_merges_with_a_fresh_tap(run):
    assert run["rewardCoalesced"] == ["impact:soft"]
    assert run["rewardAlone"] == ["impact:soft"]


def test_switch_off_is_silent_and_remembered(run):
    assert run["disabledCalls"] == []
    assert run["disabledStored"] == "0"
    assert run["disabledFlag"] is False
    assert run["restartedDisabled"] == [False, []]
    assert run["reenabled"] == [True, "1"]


def test_outside_telegram_uses_tiny_pulse(run):
    assert run["fallback"] == ["vibrate:6"]


def test_global_click_gives_a_tap_to_buttons(run):
    assert run["clickTap"] == ["impact:soft"]
    assert run["clickPointerCursor"] == ["impact:soft"]  # кликабельная карточка


def test_global_click_does_not_double_up_with_own_haptic(run):
    assert run["clickWithOwnHaptic"] == ["impact:light", "impact:soft"]


def test_global_click_ignores_noise(run):
    assert run["clickUntrusted"] == []   # программный .click()
    assert run["clickDisabled"] == []    # неактивная кнопка
    assert run["clickPlainText"] == []   # обычный текст
    assert run["clickWhenDisabled"] == []
    assert run["ghostClick"] == []       # «призрачный» клик после загрузки


def test_toggles_and_sliders_get_selection_tick(run):
    assert run["changeCheckbox"] == ["selection"]
    assert run["changeOther"] == []
    assert run["inputRange"] == ["selection"]


# ---------- Подключение и места применения ----------

def test_pages_load_the_module_before_their_scripts():
    assert re.search(r'haptics\.js\?v=\w+"></script>\s*<script type="module" src="/static/app\.js', INDEX)
    assert re.search(r'haptics\.js\?v=\w+"></script>\s*<script src="/static/ai_coach\.js', COACH_HTML)


def test_main_app_delegates_to_the_module_and_stays_gentle():
    assert "window.AdamHaptics.play(style)" in APP_JS
    assert "navigator.vibrate" not in APP_JS, "длинные паттерны vibrate убраны — отклик только через haptics.js"
    assert "notificationOccurred" not in APP_JS
    assert not re.search(r'impactOccurred\(\s*["\'](?:heavy|medium|rigid)', APP_JS)
    assert not re.search(r'haptic\(\s*["\']heavy', APP_JS), "heavy больше не используется"


def test_meaningful_moments_use_dedicated_effects():
    assert 'haptic("select")' in APP_JS            # вкладки
    assert 'haptic("success")' in APP_JS           # уровень, серия, сундук
    assert 'haptic("reward")' in APP_JS            # похвала за награду
    assert 'haptic("warning")' in APP_JS           # ошибки
    assert re.search(r'api\("/api/month-quests/claim".{0,120}\n\s*haptic\("success"\)', APP_JS, re.S)


def test_coach_chat_uses_the_same_module():
    assert "window.AdamHaptics.play(type)" in COACH_JS
    assert "window.AdamHaptics.tap()" in COACH_JS
    assert not re.search(r"impactOccurred\('(?:heavy|medium|rigid)", COACH_JS)


def test_settings_have_a_vibration_switch():
    assert 'id="hapticsToggle"' in INDEX
    assert "Вибрация при нажатиях" in INDEX
    assert "function initHapticsToggle" in APP_JS
    assert "initHapticsToggle();" in APP_JS
    assert "window.AdamHaptics" in APP_JS and "engine.setEnabled(" in APP_JS


def test_module_has_no_strong_effects_or_stray_globals():
    code = HAPTICS.read_text(encoding="utf-8")
    assert "impactOccurred(\"heavy\")" not in code
    assert not re.search(r'pulse\(\s*"(?:heavy|medium|rigid|error)"', code)
    assert code.count("window.AdamHaptics =") == 1
