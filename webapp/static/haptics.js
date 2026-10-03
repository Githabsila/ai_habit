/* Вибро-отклик Mini App: один модуль на все страницы (Главная и чат ADAM).
 *
 * Принципы — мягко и приятно, без «дрели»:
 *   • обычное нажатие — самый мягкий удар (impact "soft");
 *   • переключатели, вкладки, выбор — «щелчок выбора» (selectionChanged);
 *   • подтверждение действия — лёгкий удар и тихое эхо через 70 мс;
 *   • праздник (уровень, серия, сундук) и предупреждение — системные
 *     notification success/warning, а не самые сильные «heavy»/«error»;
 *   • два вызова подряд сливаются: чаще чем раз в 30 мс вибрация не идёт.
 *
 * Глобальный обработчик клика сам даёт «тап» любой кнопке и кликабельной
 * карточке, поэтому для обычной кнопки отдельный вызов не нужен. Если же
 * обработчик кнопки уже вызвал свою вибрацию (подтверждение, успех), второй
 * «тап» поверх не добавляется.
 *
 * Пользователь может выключить вибрацию в «Настройки → Вибрация»: флажок
 * хранится только на устройстве (localStorage), на сервер не уходит.
 *
 * Силу и рисунок всех эффектов можно поменять в одном месте — таблица
 * FALLBACK ниже и функции семейства AdamHaptics.*.
 */
(function () {
  "use strict";
  if (window.AdamHaptics) return;

  var STORAGE_KEY = "adam_haptics";
  var MIN_GAP_MS = 30;
  var ECHO_DELAY_MS = 70;
  var GHOST_CLICK_MS = 500;
  var LOADED_AT = Date.now();

  // Вне Telegram (обычный браузер Android) — самые короткие импульсы;
  // в iOS Safari navigator.vibrate нет вовсе, и это нормально.
  var FALLBACK = {
    soft: 6,
    light: 9,
    select: 5,
    success: [10, 50, 10, 50, 14],
    warning: [12, 60, 12],
  };

  var lastAt = 0;
  var seq = 0;
  var echoTimer = null;
  var enabled = readEnabled();

  function readEnabled() {
    try { return window.localStorage.getItem(STORAGE_KEY) !== "0"; } catch (_) { return true; }
  }

  function nativeFeedback() {
    var app = window.Telegram && window.Telegram.WebApp;
    return app && app.HapticFeedback ? app.HapticFeedback : null;
  }

  function pulse(kind) {
    var native = nativeFeedback();
    if (native) {
      try {
        if (kind === "select") native.selectionChanged();
        else if (kind === "success" || kind === "warning") native.notificationOccurred(kind);
        else native.impactOccurred(kind);
      } catch (_) {}
      return;
    }
    try { if (navigator.vibrate) navigator.vibrate(FALLBACK[kind] || 6); } catch (_) {}
  }

  // success/warning — редкие и важные события: их не глушит защита от
  // частых вызовов, иначе праздник можно потерять из-за соседнего тапа.
  function fire(kind, coalesceMs) {
    if (!enabled) return false;
    var t = Date.now();
    var important = kind === "success" || kind === "warning";
    if (!important && t - lastAt < MIN_GAP_MS) return false;
    if (coalesceMs && t - lastAt < coalesceMs) return false;
    lastAt = t;
    seq += 1;
    // Новый отклик важнее тихого эха прошлого подтверждения: иначе эхо
    // «смазывает» его (например, ошибка сразу после тапа по привычке).
    if (echoTimer) { clearTimeout(echoTimer); echoTimer = null; }
    pulse(kind);
    return true;
  }

  var AdamHaptics = {
    // Обычное нажатие.
    tap: function () { return fire("soft"); },
    // Переключатель, вкладка, выбор значения.
    select: function () { return fire("select"); },
    // Действие выполнено: лёгкий удар + тихое эхо.
    confirm: function () {
      if (!fire("light")) return false;
      echoTimer = setTimeout(function () {
        echoTimer = null;
        if (!enabled) return;
        lastAt = Date.now();
        pulse("soft");
      }, ECHO_DELAY_MS);
      return true;
    },
    // Маленькая награда (монеты, похвала): одиночный мягкий тик, и только
    // если за последние ~0,45 с ничего не вибрировало — чтобы тап по
    // привычке и пришедшая следом награда не сливались в «трель».
    reward: function () { return fire("soft", 450); },
    // Праздник: уровень, серия, сундук.
    success: function () { return fire("success"); },
    // Мягкое предупреждение: ошибка, необратимое действие.
    warning: function () { return fire("warning"); },
    // Совместимость со старыми вызовами haptic("light"|"medium"|"heavy"):
    // ничего «тяжёлого» — heavy/error превращаются в мягкое предупреждение.
    play: function (style) {
      switch (style) {
        case "select": return AdamHaptics.select();
        case "medium":
        case "confirm": return AdamHaptics.confirm();
        case "heavy":
        case "error":
        case "warning": return AdamHaptics.warning();
        case "success": return AdamHaptics.success();
        case "reward": return AdamHaptics.reward();
        default: return AdamHaptics.tap(); // light, soft, rigid, tap и всё прочее
      }
    },
    isEnabled: function () { return enabled; },
    setEnabled: function (value) {
      enabled = !!value;
      try { window.localStorage.setItem(STORAGE_KEY, enabled ? "1" : "0"); } catch (_) {}
    },
  };
  window.AdamHaptics = AdamHaptics;

  // ---------- Глобальный отклик на нажатия ----------

  var INTERACTIVE = 'button, a[href], summary, select, [role="button"], [role="tab"], [role="switch"], [role="menuitem"], [data-haptic]';
  var SKIPPED = '[disabled], [aria-disabled="true"], [data-haptic="off"], textarea, [contenteditable="true"],'
    + ' input:not([type="button"]):not([type="submit"]):not([type="reset"])';

  function interactiveTarget(node) {
    var el = node && node.nodeType === 1 ? node : (node && node.parentElement);
    if (!el || !el.closest) return null;
    var hit = el.closest(INTERACTIVE);
    // Кликабельные карточки и строки без <button>: их выдаёт курсор-«рука»
    // (он наследуется, так что хватает одной проверки на самом элементе).
    if (!hit) {
      try { if (window.getComputedStyle(el).cursor === "pointer") hit = el; } catch (_) {}
    }
    if (!hit || hit.closest(SKIPPED)) return null;
    return hit;
  }

  document.addEventListener("click", function (e) {
    if (!enabled || !e.isTrusted) return;
    // Первые полсекунды app.js глушит «призрачные» клики с прошлой страницы.
    if (Date.now() - LOADED_AT < GHOST_CLICK_MS) return;
    if (!interactiveTarget(e.target)) return;
    var before = seq;
    // Ждём конца обработчиков: если кнопка уже дала свой, более точный
    // отклик (подтверждение, выбор), отдельный «тап» не нужен.
    setTimeout(function () { if (seq === before) AdamHaptics.tap(); }, 0);
  }, true);

  // Переключатели, выпадающие списки, время/дата — «щелчок выбора».
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!enabled || !e.isTrusted || !t || !t.matches) return;
    if (t.matches('input[type="checkbox"], input[type="radio"], input[type="time"], input[type="date"], select')) {
      AdamHaptics.select();
    }
  }, true);

  // Ползунок: тик на каждом шаге, но не чаще, чем позволяет защита от трели.
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (!enabled || !e.isTrusted || !t || !t.matches) return;
    if (t.matches('input[type="range"]')) AdamHaptics.select();
  }, true);
})();
