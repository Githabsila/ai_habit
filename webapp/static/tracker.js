/* Путь человека в приложении (сервер — db/tracking.py и db/usage.py, приём — POST /api/track).

   Что собирает, без единой лишней нагрузки на отрисовку (только слушатели без перерисовок и один таймер раз в 5 с):
     • сессию: сколько человек реально был в приложении (экран открыт и были касания), сколько действий сделал;
     • последние 10 действий (подпись нажатой кнопки + экран) — по ним в админке видно, ПОСЛЕ ЧЕГО человек вышел;
     • события из кода приложения: AdamTrack.track("quiz_step", "age_range"), «пропустить» и т.д.;
     • первое открытие каждого раздела за сессию (событие «screen»);
     • v2 — счётчики для аналитики, которые подхватывают НОВЫЕ кнопки и экраны сами, без правок трекера:
         – клики по стабильной подписи кнопки (data-track → id → класс[=значение data-action/…]) вместе с экраном;
         – показы и секунды на каждом экране (окно «…Overlay» или вкладка);
         – версия приложения (?v= у app.js) — чтобы сравнивать поведение до и после выпуска.
   Текст, который человек вводит в поля, и тексты кнопок в счётчики не попадают. В демо-режиме админки (?lab=) трекер выключен. */
(function () {
  "use strict";
  if (window.AdamTrack) return;

  var noop = { track: function () {}, screen: function () { return ""; }, enabled: false };
  if (/[?&]lab=/.test(location.search)) { window.AdamTrack = noop; return; }

  var ENDPOINT = "/api/track";
  var GAP_MS = 25 * 60 * 1000;     // пауза, после которой возвращение — новая сессия (как на сервере: SESSION_GAP_MINUTES)
  var TICK_MS = 5000;              // шаг подсчёта активного времени
  var ACTIVE_WINDOW_MS = 60000;    // «был в приложении», пока последнее касание не старше минуты
  var HEARTBEAT_MS = 60000;
  var FLUSH_MS = 4000;
  var MAX_QUEUE = 60;
  var MAX_LAST = 10;
  var MAX_USAGE_KEYS = 60;         // за один пакет: разных кнопок / экранов (остальное уйдёт со следующим)

  // Атрибуты, значения которых — перечисления (а не id и не текст людей): ими различаем кнопки с одним классом.
  var ENUM_ATTRS = ["data-action", "data-pair-act", "data-up", "data-main-action", "data-follow-kind", "data-gift-kind", "data-tab", "data-lab", "data-up-list"];

  var tg = null;
  try { tg = window.Telegram && window.Telegram.WebApp; } catch (e) { /* вне Telegram */ }

  var session = null;
  var queue = [];
  var lastInput = Date.now();
  var hiddenAt = 0;
  var flushTimer = null;
  var lastScreen = "";
  var seenScreens = {};
  var uClicks = {};                // "подпись\u0001экран" -> сколько раз
  var uScreens = {};               // экран -> { v: показов, s: секунд }
  var uDirty = false;
  var cachedVersion = null;

  function initData() {
    try { return (tg && tg.initData) || ""; } catch (e) { return ""; }
  }

  function rid() {
    return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  }

  function platform() {
    try { return String((tg && tg.platform) || "web").slice(0, 20); } catch (e) { return "web"; }
  }

  function tzOffset() {
    try { return -new Date().getTimezoneOffset(); } catch (e) { return 0; }
  }

  // Версия приложения берётся из ?v= у app.js (он меняется с каждым выпуском), на странице чата — у самого трекера.
  function version() {
    if (cachedVersion !== null) return cachedVersion;
    try {
      var el = document.querySelector('script[src*="/static/app.js"]') || document.querySelector('script[src*="/static/tracker.js"]');
      var m = el && /[?&]v=([\w.-]{1,24})/.exec(el.getAttribute("src") || "");
      cachedVersion = m ? m[1] : "";
    } catch (e) { cachedVersion = ""; }
    return cachedVersion;
  }

  // Где человек сейчас: верхнее открытое окно (id без «Overlay») или вкладка внизу, на странице чата — «coach».
  function currentScreen() {
    try {
      var overlays = document.querySelectorAll('[id$="Overlay"]');
      for (var i = overlays.length - 1; i >= 0; i--) {
        var o = overlays[i];
        if (!o.hidden && o.classList.contains("show")) return o.id.replace(/Overlay$/, "").slice(0, 40);
      }
      var tab = document.querySelector(".tab-bar__item.is-active");
      if (tab && tab.dataset && tab.dataset.tab) return tab.dataset.tab;
      if (/\/coach/.test(location.pathname)) return "coach";
    } catch (e) { /* не мешаем приложению */ }
    return session ? session.screen : "";
  }

  function addView(screen) {
    if (!screen) return;
    var row = uScreens[screen] || (uScreens[screen] = { v: 0, s: 0 });
    row.v += 1;
    uDirty = true;
  }

  function startSession() {
    session = { sid: rid(), started: Date.now(), active: 0, actions: 0, last: [], screen: "", dirty: true };
    seenScreens = {};
    session.screen = currentScreen();
    lastScreen = session.screen;
    if (session.screen) { seenScreens[session.screen] = 1; addView(session.screen); }
    scheduleFlush(1500);
  }

  function scheduleFlush(delay) {
    if (flushTimer) return;
    flushTimer = setTimeout(function () { flushTimer = null; flush(false); }, delay || FLUSH_MS);
  }

  // Забирает накопленные счётчики пакетом и обнуляет их (потеря пакета — это лишь несколько кликов, не страшно).
  function takeUsage() {
    var clicks = [];
    var keys = Object.keys(uClicks);
    for (var i = 0; i < keys.length && clicks.length < MAX_USAGE_KEYS; i++) {
      var parts = keys[i].split("\u0001");
      clicks.push([parts[0], parts[1] || "", uClicks[keys[i]]]);
      delete uClicks[keys[i]];
    }
    var screens = {};
    var names = Object.keys(uScreens);
    for (var j = 0; j < names.length && j < MAX_USAGE_KEYS; j++) {
      screens[names[j]] = uScreens[names[j]];
      delete uScreens[names[j]];
    }
    uDirty = Object.keys(uClicks).length > 0 || Object.keys(uScreens).length > 0;
    return { clicks: clicks, screens: screens };
  }

  function flush(final) {
    if (!session) return;
    var init = initData();
    if (!init) return;
    if (!queue.length && !session.dirty && !uDirty && !final) return;
    var body = JSON.stringify({
      init_data: init,
      now: Date.now(),
      v: version(),
      session: {
        sid: session.sid, started: session.started, active: session.active, actions: session.actions,
        last: session.last, screen: session.screen, hidden: !!final, platform: platform(), tz: tzOffset()
      },
      events: queue.splice(0, queue.length),
      usage: takeUsage()
    });
    session.dirty = false;
    try {
      if (final && navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
        return;
      }
    } catch (e) { /* ниже обычный запрос */ }
    try {
      fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true })
        .catch(function () { /* потеря пакета — не беда */ });
    } catch (e) { /* то же */ }
  }

  function track(name, step, meta) {
    if (!session || !name) return;
    queue.push({
      n: String(name).slice(0, 41),
      s: step == null ? null : String(step).slice(0, 40),
      m: meta || null,
      sc: currentScreen() || session.screen,
      t: Date.now()
    });
    if (queue.length > MAX_QUEUE) queue.shift();
    scheduleFlush();
  }

  // Подпись нажатого для «последних действий» (читается человеком): data-track → aria-label → id → текст кнопки (до 28 знаков).
  function labelOf(target) {
    var el = target && target.closest ? target.closest("[data-track],button,a,[role='button'],.tab-bar__item,summary,label,select") : null;
    if (!el) return null;
    var label = el.getAttribute("data-track") || el.getAttribute("aria-label") || el.id ||
      (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 28);
    if (!label) {
      var cls = typeof el.className === "string" ? el.className.split(" ")[0] : "";
      label = el.tagName.toLowerCase() + (cls ? "." + cls : "");
    }
    return label.slice(0, 40);
  }

  // Стабильная подпись для счётчиков: без текста кнопки и без чужих id, чтобы разных подписей было конечное число.
  // data-track (если разработчик задал имя) → id → первый «не состояние» класс + значение data-action/… → тег.
  function stableLabel(target) {
    var el = target && target.closest
      ? target.closest("[data-track],button,a,[role='button'],.tab-bar__item,summary,label,select,[data-action],[data-pair-act],[data-up]")
      : null;
    if (!el) return null;
    var named = el.getAttribute("data-track");
    if (named) return named.slice(0, 40);
    if (el.id) return el.id.slice(0, 40);
    var cls = "";
    var classes = typeof el.className === "string" ? el.className.split(/\s+/) : [];
    for (var i = 0; i < classes.length; i++) {
      if (classes[i] && !/^is-|^has-|--/.test(classes[i])) { cls = classes[i]; break; }
    }
    var kind = "";
    for (var j = 0; j < ENUM_ATTRS.length; j++) {
      var v = el.getAttribute(ENUM_ATTRS[j]);
      if (v && /^[a-z0-9_-]{1,24}$/i.test(v)) { kind = v; break; }
    }
    var base = cls || el.tagName.toLowerCase();
    return (kind ? base + "=" + kind : base).slice(0, 40);
  }

  function countClick(label, screen) {
    var key = label + "\u0001" + (screen || "");
    if (!(key in uClicks) && Object.keys(uClicks).length >= 400) return;   // защита от неограниченного роста в одной сессии
    uClicks[key] = (uClicks[key] || 0) + 1;
    uDirty = true;
  }

  // Экран мог смениться кодом приложения без клика (открылось окно, закрылось) — сверяем и по кликам, и по таймеру.
  function syncScreen() {
    if (!session) return;
    var screen = currentScreen();
    if (!screen) return;
    session.screen = screen;
    if (screen !== lastScreen) {
      lastScreen = screen;
      addView(screen);
      if (!seenScreens[screen]) { seenScreens[screen] = 1; track("screen", screen); }
    }
  }

  function onInput() { lastInput = Date.now(); }

  document.addEventListener("click", function (e) {
    lastInput = Date.now();
    if (!session) return;
    var label = labelOf(e.target);
    if (!label) return;
    session.actions += 1;
    session.last.push({ l: label, s: currentScreen(), t: Date.now() - session.started });
    if (session.last.length > MAX_LAST) session.last.shift();
    session.dirty = true;
    var stable = stableLabel(e.target);
    if (stable) countClick(stable, currentScreen());
    setTimeout(syncScreen, 80);
  }, true);

  ["pointerdown", "keydown", "touchstart", "scroll"].forEach(function (name) {
    document.addEventListener(name, onInput, { capture: true, passive: true });
  });

  setInterval(function () {
    if (!session || document.hidden) return;
    if (Date.now() - lastInput < ACTIVE_WINDOW_MS) {
      session.active += TICK_MS / 1000;
      session.dirty = true;
      syncScreen();
      if (lastScreen) {
        var row = uScreens[lastScreen] || (uScreens[lastScreen] = { v: 0, s: 0 });
        row.s += TICK_MS / 1000;
        uDirty = true;
      }
    }
  }, TICK_MS);

  setInterval(function () {
    if (!document.hidden) flush(false);
  }, HEARTBEAT_MS);

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      hiddenAt = Date.now();
      flush(true);
    } else {
      if (hiddenAt && Date.now() - hiddenAt > GAP_MS) startSession();
      lastInput = Date.now();
      hiddenAt = 0;
      if (session) session.dirty = true;
    }
  });
  window.addEventListener("pagehide", function () { flush(true); });

  startSession();
  window.AdamTrack = { track: track, screen: currentScreen, enabled: true };
})();
