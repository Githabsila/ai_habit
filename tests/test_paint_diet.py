"""
Диета для отрисовки (скрины 09.10: пустые квадраты на месте ✏️ и целых строк привычек; фризы при прокрутке вверх после
повышения уровня). Браузера здесь нет — тесты держат от возврата найденные причины: каждая проверка соответствует
пункту в блоке «Диета для отрисовки» в конце style.css и правкам app.js.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
SERVER = (STATIC.parent / "webapp_server.py").read_text(encoding="utf-8")

_START = CSS.rindex("/* ===== Диета для отрисовки")
DIET = CSS[_START:CSS.index("/* Звезда в «Главная задача дня»", _START)]


def _rule(selector_start):
    block = DIET[DIET.index(selector_start):]
    return block[:block.index("}") + 1]


def test_levelup_overlay_is_really_hidden_when_hidden():
    """display:flex перебивал атрибут hidden — оверлей висел на весь экран невидимым, с вечной анимацией внутри."""
    assert 'id="levelupOverlay" hidden' in INDEX
    assert ".levelup-overlay[hidden]{display:none !important}" in DIET
    show = APP_JS[APP_JS.index("function showLevelUp("):][:2600]
    assert show.index("overlay.hidden = false;") < show.index("void overlay.offsetWidth;") < show.index('overlay.classList.add("show");'), \
        "без пересчёта между display:none → flex и классом show плавное появление не сыграет"


def test_buttons_and_cards_do_not_get_their_own_layers_any_more():
    """backface-visibility:hidden на каждой кнопке/карточке делал из них отдельные слои; при нехватке видеопамяти часть слоёв
    оставалась пустой (плоские квадраты на месте ✏️, строки привычек без текста)."""
    original = CSS.index("backface-visibility:hidden;\n  -webkit-backface-visibility:hidden;\n}") if "backface-visibility:hidden;\n  -webkit-backface-visibility:hidden;\n}" in CSS else CSS.index("backface-visibility:hidden;\r\n  -webkit-backface-visibility:hidden;\r\n}")
    block = _rule("button:not(:disabled),")
    for selector in (".habit-item", ".plan-card", ".streak-widget", ".shop-item", ".achievement-item", ".rating-item", ".cal-cell", ".btn"):
        assert selector in block, selector
    assert "backface-visibility:visible" in block and "-webkit-backface-visibility:visible" in block
    assert CSS.rindex("button:not(:disabled),") > original, "переопределение стоит ПОСЛЕ исходного правила"
    assert ".plan-item--main{transform:none}" in DIET, "главная задача дня больше не отдельный слой с маской"


def test_invisible_decor_is_removed_from_painting_not_just_made_transparent():
    assert ".decor-settled .player-card::before{animation:none !important;display:none !important}" in DIET
    group = _rule(".decor-settled .bg-particles,")
    assert ".decor-settled .streak-widget__spark" in group and "display:none !important" in group


def test_spotlight_dimming_has_no_blur_and_rings_have_no_permanent_will_change():
    panel = _rule(".onboarding-spotlight__panel{")
    assert "backdrop-filter:none !important" in panel and "-webkit-backdrop-filter:none !important" in panel
    assert re.search(r"background:rgba\(8,6,16,\.6\d?\)", panel)
    hint = _rule(".xp-bar__fill,")
    assert ".player-card__ring-wrap .level-ring__fill" in hint and "will-change:auto !important" in hint


def test_decor_pauses_while_scrolling():
    group = _rule(".adam-scrolling .player-card__aurora i,")
    for selector in (".streak-orbit", ".streak-widget__flame-halo", ".streak-widget__mini-flame", ".pair-chip--glow::after"):
        assert selector in group, selector
    assert "animation-play-state:paused !important" in group
    assert 'document.documentElement.classList.add("adam-scrolling")' in APP_JS, "класс ставит обработчик прокрутки"


def test_new_css_follows_the_projects_paint_rules():
    """В новом хвосте нет filter/backdrop-filter с значением и бесконечных анимаций (старые тесты держат то же правило)."""
    assert "infinite" not in DIET
    assert not re.search(r"(?<![-\w])filter\s*:", DIET)
    assert not re.search(r"backdrop-filter\s*:\s*blur", DIET)


def test_level_change_makes_ring_and_xp_bar_jump_instead_of_sweeping():
    fn = APP_JS[APP_JS.index("function renderPlayerCard()"):][:6200]
    assert "const levelChanged = previousLevel !== undefined && previousLevel !== String(levelValue);" in fn
    xp = fn[fn.index("if (xpBarFill) {"):][:700]
    assert "if (levelChanged) {" in xp and 'xpBarFill.style.transition = "none";' in xp and 'xpBarFill.style.transition = "";' in xp
    assert "if (!initialized || levelChanged) {" in fn, "кольцо при повышении уровня перескакивает, как при первом показе"


def test_repaint_nudge_after_an_action_touches_only_what_changed():
    """Раньше каждый тап на 2 кадра выносил в отдельный буфер весь список привычек (до ~20 МБ видеопамяти)."""
    fn = APP_JS[APP_JS.index("function stabilizeFirstPaint("):][:4400]
    assert "function stabilizeFirstPaint(extraTargets, { lists = true } = {}) {" in APP_JS
    assert "const dynamic = lists ? [" in fn and "const targets = dynamic.concat(extra);" in fn
    action = APP_JS[APP_JS.index("function applyActionPatch("):][:3800]
    assert 'stabilizeFirstPaint([changedRow, "streakDays"].filter(Boolean), { lists: false });' in action
    assert "habitPatched && result.habit ? document.querySelector(`.habit-item[data-id=\"${result.habit.id}\"]`)" in action
    plan = APP_JS[APP_JS.index("function applyPlanPatch("):][:400]
    assert 'stabilizeFirstPaint(["planList"], { lists: false });' in plan
    assert "stabilizeFirstPaint();" in APP_JS, "полная перерисовка (renderAll) по-прежнему обновляет все списки"


def test_stale_scroll_lock_is_released():
    """Подсказка закрыта, а body остался position:fixed — страница «залипает», каждое переключение — полный пересчёт."""
    assert "function releaseStaleScrollLock()" in APP_JS
    hide = APP_JS[APP_JS.index("function hideOnboardingSpotlight()"):][:520]
    assert "releaseStaleScrollLock();" in hide
    stale = APP_JS[APP_JS.index("function releaseStaleScrollLock()"):][:520]
    assert "(hint && !hint.hidden) || (spot && !spot.hidden)" in stale and "unlockBackgroundScroll();" in stale
    assert "document.addEventListener('visibilitychange', () => { if (!document.hidden) releaseStaleScrollLock(); });" in APP_JS


def test_frames_after_a_level_up_are_reported_separately_without_feeding_lite_mode():
    assert '"lvl_long_frame"' in SERVER.split("_VALID_PERF_EVENT_TYPES")[1][:80]
    report = APP_JS[APP_JS.index("function reportLevelUpFrame("):][:900]
    assert 'event_type: "lvl_long_frame"' in report and "_bumpPerfHitCounter" not in report, "не превращает телефон в «слабое устройство»"
    watcher = APP_JS[APP_JS.index("function frameWatcher("):][:700]
    assert "window.__adamLevelUpAt" in watcher and "< 30000" in watcher and "LVL_FRAME_THRESHOLD_MS" in watcher
    assert "window.__adamLevelUpAt = performance.now();" in APP_JS[APP_JS.index("function showLevelUp("):][:2600]


def test_level_up_celebration_is_lighter_and_assets_are_versioned():
    assert "for (let i = 0; i < 6; i++) {" in APP_JS[APP_JS.index("function burstCoins()"):][:120]
    assert "style.css?v=20261009_PAIRGUIDE_V53" in INDEX and "app.js?v=20261009_PAIRGUIDE_V60" in INDEX
