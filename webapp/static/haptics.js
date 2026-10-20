/* Вибро-отклик Mini App: один модуль на все страницы (Главная и чат ADAM).
 *
 * Принципы — мягко и приятно, без «дрели»:
 *   • обычное нажатие — самый мягкий удар (impact "soft");
 *   • переключатели, вкладки, выбор — «щелчок выбора» (selectionChanged);
 *   • подтверждение действия — лёгкий удар и тихое эхо через 70 мс;
 *   • праздник (уровень, серия, сундук) и предупреждение — системные
 *     notification success/warning, а не самые сильные «heavy»/«error»;
 *   • за что-то по-настоящему сложное (открытый сундук, рубеж уровня, эволюция,
 *     выполненное парное задание, новый статус серии) — тройная долгая
 *     вибрация triple(), как за целый раздел в Duolingo; удары по сундуку
 *     knock(1..3) растут от тапа к тапу;
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
    triple: [180, 110, 180, 110, 260],
  };

  // Тройная долгая вибрация: Telegram умеет только короткие удары, поэтому «долгий» всплеск — это системный success и следом серия
  // лёгких тиков каждые 45 мс; три всплеска с паузами (всё вместе около секунды). Остаётся в той же мягкой палитре: success + light.
  var TRIPLE_BURST_MS = 330;
  var TRIPLE_TICK_MS = 45;
  var TRIPLE_TICKS = 4;
  var tripleTimers = [];

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

  function cancelTriple() {
    while (tripleTimers.length) clearTimeout(tripleTimers.pop());
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
    // Тап по сундуку: первый — самый тихий, второй чуть заметнее, третий — подтверждение с эхом (растёт к открытию).
    knock: function (n) { return n >= 3 ? AdamHaptics.confirm() : fire(n === 2 ? "light" : "soft"); },
    // Тройная долгая вибрация — за что-то сложное (см. шапку файла). Новый вызов обрывает прошлый, выключатель её глушит.
    triple: function () {
      if (!enabled) return false;
      cancelTriple();
      lastAt = Date.now();
      seq += 1;
      if (echoTimer) { clearTimeout(echoTimer); echoTimer = null; }
      if (!nativeFeedback()) {
        try { if (navigator.vibrate) navigator.vibrate(FALLBACK.triple); } catch (_) {}
        return true;
      }
      for (var burst = 0; burst < 3; burst++) {
        (function (offset) {
          var start = function () { pulse("success"); };
          if (offset === 0) start(); else tripleTimers.push(setTimeout(start, offset));
          for (var tick = 1; tick <= TRIPLE_TICKS; tick++) {
            tripleTimers.push(setTimeout(function () { if (enabled) pulse("light"); }, offset + tick * TRIPLE_TICK_MS));
          }
        })(burst * TRIPLE_BURST_MS);
      }
      return true;
    },
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
        case "triple": return AdamHaptics.triple();
        case "reward": return AdamHaptics.reward();
        default: return AdamHaptics.tap(); // light, soft, rigid, tap и всё прочее
      }
    },
    isEnabled: function () { return enabled; },
    setEnabled: function (value) {
      enabled = !!value;
      if (!enabled) cancelTriple();
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
