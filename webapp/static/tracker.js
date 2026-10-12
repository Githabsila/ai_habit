/* Путь человека в приложении (сервер — db/tracking.py, приём — POST /api/track).

   Что собирает, без единой лишней нагрузки на отрисовку (только слушатели без перерисовок и один таймер раз в 5 с):
     • сессию: сколько человек реально был в приложении (экран открыт и были касания), сколько действий сделал;
     • последние 10 действий (подпись нажатой кнопки + экран) — по ним в админке видно, ПОСЛЕ ЧЕГО человек вышел;
     • события из кода приложения: AdamTrack.track("quiz_step", "age_range"), «пропустить» и т.д.;
     • первое открытие каждого раздела за сессию (событие «screen»).
   Текст, который человек вводит в поля, не собирается. В демо-режиме админки (?lab=) трекер выключен. */
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

  var tg = null;
  try { tg = window.Telegram && window.Telegram.WebApp; } catch (e) { /* вне Telegram */ }

  var session = null;
  var queue = [];
  var lastInput = Date.now();
  var hiddenAt = 0;
  var flushTimer = null;
  var lastScreen = "";
  var seenScreens = {};

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

  function startSession() {
    session = { sid: rid(), started: Date.now(), active: 0, actions: 0, last: [], screen: "", dirty: true };
    seenScreens = {};
    session.screen = currentScreen();
    lastScreen = session.screen;
    if (session.screen) seenScreens[session.screen] = 1;
    scheduleFlush(1500);
  }

  function scheduleFlush(delay) {
    if (flushTimer) return;
    flushTimer = setTimeout(function () { flushTimer = null; flush(false); }, delay || FLUSH_MS);
  }

  function flush(final) {
    if (!session) return;
    var init = initData();
    if (!init) return;
    if (!queue.length && !session.dirty && !final) return;
    var body = JSON.stringify({
      init_data: init,
      now: Date.now(),
      session: {
        sid: session.sid, started: session.started, active: session.active, actions: session.actions,
        last: session.last, screen: session.screen, hidden: !!final, platform: platform(), tz: tzOffset()
      },
      events: queue.splice(0, queue.length)
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

  // Подпись нажатого: data-track → aria-label → id → текст кнопки (до 28 знаков).
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

  function afterClick() {
    if (!session) return;
    var screen = currentScreen();
    if (!screen) return;
    session.screen = screen;
    if (screen !== lastScreen) {
      lastScreen = screen;
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
    setTimeout(afterClick, 80);
  }, true);

  ["pointerdown", "keydown", "touchstart", "scroll"].forEach(function (name) {
    document.addEventListener(name, onInput, { capture: true, passive: true });
  });

  setInterval(function () {
    if (!session || document.hidden) return;
    if (Date.now() - lastInput < ACTIVE_WINDOW_MS) {
      session.active += TICK_MS / 1000;
      session.dirty = true;
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
