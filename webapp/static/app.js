(() => {
  "use strict";

  // Иконки в квадратах и кружках (✕ ✎ ✓ +) раньше были текстовыми символами: у каждого шрифта свои поля вокруг глифа, и
  // на телефоне «✕» уезжал вверх, а «✎» — в сторону. Рисованные SVG с симметричным viewBox стоят ровно на любом устройстве
  // (размер и цвет — из CSS: .ui-icon в style.css, stroke=currentColor).
  const uiIcon = (d, width = "2.4") => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="${d}" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const ICON_X = uiIcon("M6 6l12 12M18 6L6 18");
  // Четырёхконечная звезда «✦» (ромбик) — рисованная: текстовый глиф сидел в квадрате не по центру.
  const UI_SPARK = `<svg class="ui-icon ui-icon--fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2c.7 5.6 4.4 9.3 10 10-5.6.7-9.3 4.4-10 10-.7-5.6-4.4-9.3-10-10 5.6-.7 9.3-4.4 10-10Z" fill="currentColor"/></svg>`;
  const ICON_CHECK = uiIcon("M5 12.5l4.5 4.5L19 7", "2.6");
  const ICON_EDIT = uiIcon("M4.5 19.5h4L19.5 8.5l-4-4L4.5 15.5v4zM14 6l4 4", "2.2");
  const ICON_CHEVRON_UP = uiIcon("M6.5 14.5l5.5-5.5 5.5 5.5", "2.6");   // «Спрятать кнопки» у раскрытой привычки

  // Некоторые WebView (в т.ч. Telegram на Android) успевают "доставить"
  // клик/тач, начатый ещё на предыдущей странице, уже ПОСЛЕ полной
  // навигации на новую — с теми же экранными координатами. Кнопка
  // "✕ Закрыть" в /admin и кнопка админки здесь (#adminPanelBtn) обе
  // сидят в верхнем правом углу — из-за этого призрачный клик, оставшийся
  // от нажатия на крестик, тут же попадал по кнопке админки и уносил
  // обратно в /admin (бесконечный "не могу выйти из админки"). Глушим
  // самый первый клик в первые полсекунды после загрузки страницы.
  const PAGE_LOAD_AT = Date.now();
  document.addEventListener("click", (e) => {
    if (Date.now() - PAGE_LOAD_AT < 500) {
      e.stopPropagation();
      e.preventDefault();
    }
  }, { capture: true });

  // Улучшение #70: раньше единственный способ узнать про JS-краш у реального
  // пользователя — попросить прислать видео/скриншот открытой консоли (именно
  // так был найден и починен баг с backdrop-filter в .tab-bar в этой же
  // сессии). Теперь любая непойманная ошибка/отклонённый Promise тихо летит
  // на сервер. Best-effort: если сама отправка упадёт — просто игнорируем,
  // никогда не бросаем дальше и никогда не мешаем работе приложения. Лимит
  // на сессию — чтобы цикл ошибок (например, в setInterval) не заспамил себя.
  let _clientErrorsSent = 0;
  function reportClientError(message, stack) {
    // "Script error." без стека — заглушка браузера для исключения из
    // скрипта с другого домена (telegram.org/js/telegram-web-app.js): ни
    // строки, ни реального текста, диагностировать нечего, а при
    // сворачивании/возврате в Mini App их приходит пачка. Не тратим на них
    // лимит сессии, который нужен настоящим ошибкам.
    if (String(message || "").trim() === "Script error." && !stack) return;
    if (_clientErrorsSent >= 5) return;
    _clientErrorsSent += 1;
    try {
      const initDataRaw = (tg && tg.initData) || (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || "";
      fetch("/api/client-error", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "tma " + initDataRaw },
        body: JSON.stringify({
          message: String(message || "").slice(0, 500),
          stack: stack ? String(stack).slice(0, 4000) : null,
          // Без location.hash: там #tgWebAppData= с подписанными initData.
          url: location.origin + location.pathname,
        }),
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }
  window.addEventListener("error", (e) => {
    // Когда у ошибки нет объекта Error (а значит и стека) — хотя бы
    // файл:строка:колонка, чтобы запись можно было привязать к месту в коде.
    const where = e.filename ? e.filename + ":" + e.lineno + ":" + e.colno : null;
    reportClientError(e.message, (e.error && e.error.stack) || where);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason;
    reportClientError(
      reason && reason.message ? reason.message : String(reason),
      reason && reason.stack
    );
  });

  // "Лагает/чернеет при прокрутке" в Telegram WebView на Android — баг
  // физически невозможно воспроизвести или отладить с десктопа (другой
  // движок, другое железо). Раньше единственным источником были видео от
  // пользователя, по которым можно было только гадать, что происходит в
  // конкретный момент. Меряем прямо на устройстве пользователя две вещи
  // и шлём на сервер (тот же best-effort/лимит-на-сессию приём, что и у
  // reportClientError выше):
  // 1) реальные разрывы между кадрами (requestAnimationFrame) — именно
  //    это и есть визуальное "подвисание/почернение", независимо от того,
  //    что его вызвало (paint, compositor, GC — неважно, разрыв виден
  //    пользователю в любом случае);
  // 2) длинные JS-таски (PerformanceObserver longtask, 50мс+) — отдельная
  //    причина: конкретно код блокирует поток, а не рендер-движок.
  // Текущий детект "слабого" устройства (ниже) смотрит только на число
  // ядер/RAM — реальная телеметрия с прода показала, что это НЕ ловит
  // проблему: флагман Samsung S25 Ultra (8 ядер, 8ГБ) всё равно даёт
  // долгие кадры — дело не в мощности как таковой, а в конкретном
  // рендер-движке WebView на конкретной прошивке, и это никак не
  // коррелирует с характеристиками из navigator. Поэтому копим факт
  // "у ЭТОГО телефона реально были долгие кадры" в localStorage — и раз
  // порог пройден, лайт-режим включаем на будущих открытиях уже по
  // факту, а не по догадке о железе. Не завязано на eventType — оба типа
  // (long_frame и long_task) одинаково означают "юзер это видит".
  const PERF_HITS_KEY = "adam_perf_hits";
  const PERF_HITS_THRESHOLD = 3;
  function _bumpPerfHitCounter(durationMs) {
    // >5000мс отсекаем — это диапазон, где живут артефакты измерения
    // (например, старый баг с фоновой вкладкой, см. frameWatcher ниже),
    // а не реальные фризы, которые пользователь мог бы увидеть на экране.
    if (durationMs <= 0 || durationMs > 5000) return;
    try {
      const n = (parseInt(localStorage.getItem(PERF_HITS_KEY), 10) || 0) + 1;
      localStorage.setItem(PERF_HITS_KEY, String(n));
    } catch (_) {}
  }

  let _perfEventsSent = 0;
  function reportPerfEvent(eventType, durationMs, isScrolling) {
    _bumpPerfHitCounter(durationMs);
    if (_perfEventsSent >= 20) return;
    _perfEventsSent += 1;
    try {
      const initDataRaw = (tg && tg.initData) || (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || "";
      const activeTab = document.querySelector(".tab-panel:not([hidden])")?.dataset.tab || null;
      fetch("/api/perf/report", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "tma " + initDataRaw },
        body: JSON.stringify({
          event_type: eventType,
          duration_ms: Math.round(durationMs),
          tab: activeTab,
          path: location.pathname,
          is_scrolling: !!isScrolling,
          device_info: JSON.stringify({
            ua: (navigator.userAgent || "").slice(0, 200),
            mem: navigator.deviceMemory || null,
            cores: navigator.hardwareConcurrency || null,
          }),
        }),
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }

  // Обычный кадр — ~16мс при 60Гц, но у многих телефонов экран 90-120Гц.
  // Порог с большим запасом, чтобы ловить именно заметные пользователю
  // подвисания, а не обычный джиттер на 1-2 кадра.
  const LONG_FRAME_THRESHOLD_MS = 200;
  let _lastFrameTime = performance.now();
  // НАЙДЕНО при разборе реальных данных с /api/admin/perf-events: часть
  // long_frame событий имела duration_ms в МИЛЛИОНАХ (часы) — это не
  // реальные фризы, а артефакт измерения. requestAnimationFrame не
  // тикает, пока вкладка/Mini App свёрнута — следующий кадр после
  // возврата считает разрыв как "now - _lastFrameTime за ВЕСЬ фон",
  // раздувая телеметрию мусором и маскируя реальную картину. Сбрасываем
  // точку отсчёта при возврате в приложение, чтобы такой кадр вообще не
  // засчитывался как разрыв.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) _lastFrameTime = performance.now();
  });
  // Жалоба: после повышения уровня прокрутка вверх «фризит». Чтобы увидеть это в цифрах, кадры дольше 48 мс в первые 30 с
  // после праздничного экрана пишем отдельным типом lvl_long_frame (админка → perf-events). В счётчик «слабого устройства»
  // (performance-lite) они НЕ входят и не расходуют лимит обычных событий.
  const LVL_FRAME_THRESHOLD_MS = 48;
  let _lvlFramesSent = 0;
  function reportLevelUpFrame(gap, scrolling) {
    if (_lvlFramesSent >= 10) return;
    _lvlFramesSent += 1;
    try {
      const initDataRaw = (tg && tg.initData) || (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || "";
      fetch("/api/perf/report", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "tma " + initDataRaw },
        body: JSON.stringify({
          event_type: "lvl_long_frame",
          duration_ms: Math.round(gap),
          tab: document.querySelector(".tab-panel:not([hidden])")?.dataset.tab || null,
          path: location.pathname,
          is_scrolling: !!scrolling,
          device_info: JSON.stringify({ ua: (navigator.userAgent || "").slice(0, 200), mem: navigator.deviceMemory || null, cores: navigator.hardwareConcurrency || null }),
        }),
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }
  function frameWatcher(now) {
    const gap = now - _lastFrameTime;
    _lastFrameTime = now;
    if (gap > LVL_FRAME_THRESHOLD_MS && gap <= LONG_FRAME_THRESHOLD_MS && !document.hidden && now - (window.__adamLevelUpAt || -1e9) < 30000) {
      reportLevelUpFrame(gap, document.querySelector("header.player-card")?.classList.contains("is-scrolling") || document.documentElement.classList.contains("adam-scrolling"));
    }
    if (gap > LONG_FRAME_THRESHOLD_MS && !document.hidden) {
      const scrolling =
        document.querySelector(".tab-bar")?.classList.contains("is-scrolling") ||
        document.querySelector("header.player-card")?.classList.contains("is-scrolling");
      reportPerfEvent("long_frame", gap, scrolling);
    }
    requestAnimationFrame(frameWatcher);
  }
  requestAnimationFrame(frameWatcher);

  try {
    if ("PerformanceObserver" in window) {
      const po = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          reportPerfEvent("long_task", entry.duration, false);
        }
      });
      po.observe({ type: "longtask", buffered: true });
    }
  } catch (_) {}

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  try {
    let pastHits = 0;
    try { pastHits = parseInt(localStorage.getItem(PERF_HITS_KEY), 10) || 0; } catch (_) {}
    const lowPower =
      (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) ||
      (navigator.deviceMemory && navigator.deviceMemory <= 4) ||
      (navigator.connection && navigator.connection.saveData) ||
      pastHits >= PERF_HITS_THRESHOLD;
    if (lowPower) document.documentElement.classList.add("performance-lite");
  } catch (_) {}

  // Видео-подтверждённый баг: .tab-bar держит backdrop-filter:blur(28px) —
  // WebView не успевает пересчитывать его каждый кадр поверх активно
  // скроллящегося контента и роняет отрисовку (контент чернеет на 0.3-0.5с,
  // старый кадр проступает призраком у низа экрана). Постоянный вид панели
  // НЕ трогаем (см. комментарий в style.css у .tab-bar.is-scrolling) —
  // предыдущая попытка убрать blur насовсем была откачена пользователем.
  // Вместо этого на время самого скролла (+150мс после остановки) дорогой
  // blur временно выключается классом — в состоянии покоя визуально ничего
  // не меняется.
  // .player-card (верхняя карточка уровня) — тот же паттерн, что и
  // .tab-bar: постоянный backdrop-filter:blur(22px) поверх ПОСТОЯННО
  // анимированного ::before (бегущий блик, 5.8s infinite). Карточка не
  // зафиксирована (не sticky/fixed) — при обычном скролле она уходит и
  // возвращается в вьюпорт, и это ровно тот же класс WebView-бага
  // (см. комментарий у .tab-bar.is-scrolling в style.css). Раньше эта
  // карточка получила только транслейт-промоушен слоя (translateZ(0)),
  // сам blur не трогали. Чиним так же аккуратно, как .tab-bar: blur
  // выключаем ТОЛЬКО на время активного скролла, в покое — как было.
  (function initScrollPerfGuard() {
    const bar = document.querySelector(".tab-bar");
    const card = document.querySelector("header.player-card");
    if (!bar && !card) return;
    let scrollTimer = null;
    window.addEventListener("scroll", () => {
      document.documentElement.classList.add("adam-scrolling");
      // The player card/tab bar are already configured without expensive blur
      // in the premium layer. Avoid toggling visual properties every scroll frame.
      bar?.classList.add("is-scrolling");
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        document.documentElement.classList.remove("adam-scrolling");
        bar?.classList.remove("is-scrolling");
        // ВАЖНО: не вызываем stabilizeFirstPaint() после каждого скролла.
        // Эта функция специально делает две смены opacity через rAF и при
        // частом листании сама могла создавать long_task/long_frame.
        // Она уже вызывается после renderAll(), когда реально меняется DOM.
        // На остановке скролла здесь достаточно снять временный guard.
      }, 150);
    }, { passive: true });
  })();

  const RING_CIRCUMFERENCE = 2 * Math.PI * 49; // exact circumference for the 112px SVG ring (r=49)

  function pluralRu(n, one, few, many) {
    n = Math.abs(Number(n) || 0);
    if (n % 100 >= 11 && n % 100 <= 14) return many;
    const last = n % 10;
    if (last === 1) return one;
    if (last >= 2 && last <= 4) return few;
    return many;
  }

  function formatDays(n) {
    return `${Number(n) || 0} ${pluralRu(n, "день", "дня", "дней")}`;
  }

  // Общая карточка-медаль "Поделиться" — раньше умела показывать только
  // серию дней подряд, теперь принимает произвольный заголовок/большое
  // число/статус, чтобы её же переиспользовать для недельного итога
  // (см. initDataSupportActions -> #shareWeeklyBtn).
  let lastShareCard = null; // запоминаем для кнопки "Поделиться" внутри карточки
  function openAchievementShare({ title, big, status }) {
    lastShareCard = { title, big, status };
    const overlay = document.getElementById("achievementShareOverlay");
    const titleEl = document.getElementById("achievementShareTitle");
    const daysEl = document.getElementById("achievementShareDays");
    const statusEl = document.getElementById("achievementShareStatus");
    const levelEl = document.getElementById("achievementShareLevel");
    const coinsEl = document.getElementById("achievementShareCoins");
    if (titleEl) titleEl.textContent = title;
    if (daysEl) daysEl.textContent = big;
    // status пустой/не передан — например у "Ударного режима" он дублировал
    // бы big ("21 день" сверху и "В ударе 21 дн." строкой ниже); скрываем
    // строку вместо показа дубликата.
    if (statusEl) {
      statusEl.textContent = status || "";
      statusEl.hidden = !status;
    }
    if (levelEl) levelEl.textContent = state?.user?.level || 1;
    if (coinsEl) coinsEl.textContent = state?.user?.xp || 0;
    if (overlay) {
      overlay.hidden = false;
      requestAnimationFrame(() => overlay.classList.add("show"));
      overlay.setAttribute("aria-hidden", "false");
      haptic("light");
    }
  }


  // Иконка Adam Coin — инлайн SVG вместо шрифтовой иконки "diamond",
  // чтобы совпадать с фирменным золотым логотипом монеты.
  const ADAM_COIN_ICON = `<svg class="stat-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="adamCoinGrad" x1="4" y1="3" x2="20" y2="21" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#FFEDA6"/>
        <stop offset="0.5" stop-color="#FFC93C"/>
        <stop offset="1" stop-color="#E08E00"/>
      </linearGradient>
    </defs>
    <circle cx="12" cy="12" r="10.4" fill="#B8720A"/>
    <circle cx="12" cy="12" r="9.3" fill="url(#adamCoinGrad)"/>
    <circle cx="12" cy="12" r="7.1" fill="none" stroke="#FFF3C4" stroke-width="0.9" opacity="0.55"/>
    <text x="12" y="16.2" text-anchor="middle" font-family="'Space Grotesk', Arial, sans-serif" font-weight="800" font-size="11" fill="#9C5F06">A</text>
    <path d="M6.3 6.8c1-1.4 2.5-2.3 3.8-2.6" stroke="#FFF8E4" stroke-width="1.5" stroke-linecap="round" opacity="0.7" fill="none"/>
  </svg>`;

  let state = null; // последний bootstrap-снимок
  let knownLevel = null; // для детекта левел-апа между загрузками
  let activeHabitFilter = ""; // выбранная категория в фильтре привычек ("" — все)
  let currentLanguage = "ru"; // roadmap #46 — язык интерфейса, обновляется из state.settings.language
  let reactPickerForId = null; // roadmap #19 — telegram_id, для которого сейчас открыт выбор эмодзи-реакции в рейтинге

  const HABIT_CATEGORY_META = {
    health: { emoji: "🩺", label: "Здоровье" },
    work: { emoji: "💼", label: "Работа" },
    study: { emoji: "📚", label: "Учёба" },
    mind: { emoji: "🧘", label: "Разум" },
    other: { emoji: "✨", label: "Другое" },
  };


  // Безопасный вывод пользовательского текста в HTML.
  // Эта функция используется рейтингом, достижениями и задачами.
  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }


  
  // ===================== TELEGRAM WEBAPP INIT =====================

  // Mini App не должен масштабироваться как обычный сайт. Viewport meta
  // отключает pinch-zoom в поддерживаемых WebView, а эти обработчики
  // закрывают оставшиеся жесты в iOS/Safari-подобных движках. Один палец
  // продолжает нормально прокручивать страницу.
  document.addEventListener("touchmove", (e) => {
    if (e.touches && e.touches.length > 1) e.preventDefault();
  }, { passive: false });
  document.addEventListener("gesturestart", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("gesturechange", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("gestureend", (e) => e.preventDefault(), { passive: false });
  document.addEventListener("wheel", (e) => {
    if (e.ctrlKey) e.preventDefault();
  }, { passive: false });

  function initTelegram() {
    if (!tg) return;
    tg.ready();
    tg.expand();
    // Telegram's native close/menu chrome floats above the WebApp content.
    // Mark the document so the layout can reserve a real top band for it
    // even on large Android/tablet viewports where max-width media queries
    // do not match.
    try { document.documentElement.classList.add("telegram-webapp"); } catch (_) {}
    try {
      tg.setHeaderColor("#0E0B14");
      tg.setBackgroundColor("#0E0B14");
    } catch (e) { /* старые клиенты могут не поддерживать */ }
    // Жалоба "при пролистывании всё заново прогружается": по умолчанию
    // Telegram трактует вертикальный свайп внутри Mini App (особенно
    // резиновый оттяг вверху экрана) как жест сворачивания/закрытия —
    // а повторное открытие грузит страницу с нуля. disableVerticalSwipes
    // (Bot API 7.7+) отдаёт весь вертикальный скролл самой странице.
    // Метода может не быть у старых клиентов Telegram — просто нет-опа.
    try { tg.disableVerticalSwipes?.(); } catch (e) {}
    // Стрелка «назад» в шапке Telegram. Чат с ADAM (ai_coach.js) показывает её и
    // возвращается сюда обычным переходом на «/» — Telegram её не сбрасывает, и
    // на главной вместо привычного «✕» оставалась «←» без обработчика (на
    // скриншотах она есть на каждом экране после чата). Прячем при загрузке; а
    // показываем только пока открыта подстраница (магазин, прогресс, настройки):
    // тогда она закрывает верхнюю, как и «←» в её шапке.
    try {
      tg.BackButton?.hide();
      tg.onEvent?.("backButtonClicked", closeTopSubpage);
    } catch (e) {}
    // Полноэкранный режим: страница начинается с самого верха экрана, а родная шапка Telegram («Закрыть»,
    // ⌄ ⋮) рисуется поверх неё. Отступы сверху отдаёт сам Telegram (--tg-safe-area-inset-top и
    // --tg-content-safe-area-inset-top, см. --adam-chrome-top в style.css); если клиент в полноэкранном режиме
    // их не сообщил — подстраховка, чтобы шапки окон («←», ник) не оказывались под строкой состояния.
    try {
      const syncChromeInsets = () => {
        const reported = (Number(tg.safeAreaInset?.top) || 0) + (Number(tg.contentSafeAreaInset?.top) || 0);
        const extra = tg.isFullscreen && reported < 40 ? 80 : 0;
        document.documentElement.style.setProperty("--adam-chrome-extra", `${extra}px`);
      };
      syncChromeInsets();
      ["fullscreenChanged", "safeAreaChanged", "contentSafeAreaChanged"].forEach((name) => tg.onEvent?.(name, syncChromeInsets));
    } catch (e) {}
  }

  function initData() {
    // Telegram normally exposes signed initData through WebApp.initData.
    // Some embedded/alternative clients can expose it a moment later, or only
    // leave the raw value in tgWebAppData. Never fall back to initDataUnsafe.
    try {
      const direct = (tg && typeof tg.initData === "string" ? tg.initData : "") ||
        (window.Telegram?.WebApp && typeof window.Telegram.WebApp.initData === "string" ? window.Telegram.WebApp.initData : "");
      if (direct) return direct;
    } catch (_) {}
    try {
      const params = new URLSearchParams(location.hash.startsWith("#") ? location.hash.slice(1) : location.hash);
      const hashData = params.get("tgWebAppData");
      if (hashData) return hashData;
      const queryData = new URLSearchParams(location.search).get("tgWebAppData");
      if (queryData) return queryData;
    } catch (_) {}
    return "";
  }

  async function waitForInitData(timeoutMs = 3000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const value = initData();
      if (value) return value;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    return initData();
  }

  // Вся логика вибро-отклика живёт в haptics.js (мягкие эффекты, защита от
  // «трели», выключатель в настройках). Здесь — тонкая обёртка со старым
  // именем: light/medium/heavy и новые select/success/reward/warning.
  // Учёт пути человека (webapp/static/tracker.js → db/tracking.py): событие онбординга, «пропустить» и т.п. Не должно ничего ломать.
  function trk(name, step, meta) {
    try { window.AdamTrack?.track(name, step, meta); } catch (_) { /* учёт необязателен */ }
  }

  function haptic(style) {
    if (window.AdamHaptics) { window.AdamHaptics.play(style); return; }
    // haptics.js не загрузился — хотя бы мягкий тап, без «тяжёлых» ударов.
    if (tg && tg.HapticFeedback) {
      try { tg.HapticFeedback.impactOccurred("soft"); } catch (e) {}
    }
  }

  // Короткий приятный "дзынь" для микро-побед (похвала за задачу, монеты,
  // бонусное окно) — не громкий системный звук, а мягкий синтезированный
  // тон через WebAudio, чтобы не требовать отдельного аудиофайла.
  // На время праздника новой эволюции интерфейсные звуки глохнут (см.
  // playEvolutionCinematic) — иначе «дзынь» монет перебил бы момент.
  let evolutionSilence = false;
  function playChime(variant) {
    if (evolutionSilence) return;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      const [from, to] = variant === "bonus" ? [620, 980] : [520, 820];
      osc.frequency.setValueAtTime(from, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(to, ctx.currentTime + 0.12);
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(); osc.stop(ctx.currentTime + 0.2);
      osc.onended = () => ctx.close();
    } catch (_) {}
  }

  // ===================== API =====================
  let bootstrapPromise = null;

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeoutMs = Number(options.timeoutMs || (path === "/api/bootstrap" ? 45000 : 20000));
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const fetchOptions = { ...options, signal: controller.signal };
    delete fetchOptions.timeoutMs;

    try {
      const rawInitData = initData();
      const res = await fetch(path, {
        ...fetchOptions,
        headers: {
          "Content-Type": "application/json",
          // Send both headers: Authorization is the normal path; the X- header
          // is a compatibility path for WebViews/proxies that treat Authorization specially.
          "Authorization": "tma " + rawInitData,
          "X-Telegram-Init-Data": rawInitData,
          ...(options.headers || {}),
        },
      });
      let data = null;
      try { data = await res.json(); } catch (e) { /* пусто */ }
      if (!res.ok) {
        // "request_failed" — внутренний технический фолбэк для случая, когда
        // сервер не прислал data.error (сеть оборвалась/сервер отдал не JSON).
        // Раньше это слово всплывало прямо в интерфейсе как есть — кладём
        // его в err.data.error тоже, чтобы friendlyError() всегда находил
        // понятный русский текст через свою карту кодов, а не показывал
        // технический код напрямую.
        const code = (data && data.error) || "request_failed";
        const err = new Error(code);
        err.data = Object.assign({}, data, { error: code });
        err.status = res.status;
        if (res.status === 403 && code === "trial_expired") showTrialPaywall(data?.trial);
        throw err;
      }
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") {
        err.message = "Сервер слишком долго отвечает";
        err.code = "timeout";
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function loadBootstrap() {
    // Не допускаем несколько тяжёлых /api/bootstrap одновременно: это могло
    // происходить при быстрых кликах/обновлениях и давать гонки перерисовки.
    if (bootstrapPromise) return bootstrapPromise;

    bootstrapPromise = (async () => {
      let lastError = null;
      // Give Telegram/embedded clients a short window to populate signed initData
      // before the first API request. This avoids a false 401 on cold launch.
      await waitForInitData(3000);
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const previousState = state;
          state = await api("/api/bootstrap");
          carrySecondaryData(previousState, state);
          if (!state || !state.user) {
            // /api/bootstrap ответил без объекта user (пустой конверт,
            // не залогиненная превью-сессия и т.п.). Раньше следующая же
            // строка (state.user.level) кидала исключение и обрывала
            // renderAll() ещё до renderPlayerCard() — шапка оставалась
            // пустой без имени, без аватара, без прогресса.
            state = state || {};
            state.user = state.user || {};
          }
          const newLevel = state.user.level;
          if (knownLevel !== null && newLevel > knownLevel) {
            showLevelUp(newLevel, knownLevel);
          }
          knownLevel = newLevel;
          renderAll();
          // Квесты дня: завёл привычку, сохранил план — ответ /api/bootstrap уже содержит выполненное (сервер выдаёт награду сам). Есть прошлый
          // снимок — показываем изменение окном; первый заход — только тост про то, что сделано без приложения (сообщение Адаму в боте и т.п.).
          if (previousState?.daily_quests) announceQuestChange(previousState.daily_quests, previousState.month_quests);
          else announceCaughtUpQuests(state.daily_quests);
          return state;
        } catch (err) {
          lastError = err;
          // Один короткий повтор только для временной сетевой/серверной ошибки.
          if (attempt < 2 && (!err.status || err.status >= 500 || err.code === "timeout")) {
            await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
            continue;
          }
          throw err;
        }
      }
      throw lastError || new Error("request_failed");
    })().finally(() => {
      bootstrapPromise = null;
    });

    return bootstrapPromise;
  }

  // ===================== RENDER ALL =====================
  function scheduleIdleWork(fn) {
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(fn, { timeout: 1200 });
    } else {
      window.setTimeout(fn, 80);
    }
  }

  // Единая полоса "Сегодня" сверху — вместо того чтобы самому сводить в
  // уме прогресс по привычкам и по плану дня (две разные карточки, два
  // разных счётчика 0/0), один явный ответ на вопрос "что дальше?" сразу
  // при входе. Пусто (нет вообще ни привычек, ни задач) — полоса скрыта:
  // нечего сводить, а пустая карточка сверху — это и есть тот самый
  // лишний шум, которого просили избегать.
  function renderTodayFocus() {
    const el = document.getElementById("todayFocus");
    if (!el) return;

    const habits = state.habits || [];
    const habitsDone = habits.filter(h => h.completed).length;
    const habitsLeft = habits.length - habitsDone;

    const plan = state.daily_plan || { tasks: [] };
    const tasks = plan.tasks || [];
    const planTotal = tasks.length + (plan.main_goal ? 1 : 0);
    const planDone = tasks.filter(t => t.completed).length + (plan.main_goal && plan.main_goal_completed ? 1 : 0);
    const planLeft = planTotal - planDone;

    const totalItems = habits.length + planTotal;
    const totalLeft = habitsLeft + planLeft;

    if (totalItems === 0) {
      el.hidden = true;
      return;
    }
    el.hidden = false;

    const icon = document.getElementById("todayFocusIcon");
    const title = document.getElementById("todayFocusTitle");
    const sub = document.getElementById("todayFocusSub");

    if (totalLeft === 0) {
      el.classList.add("is-done");
      icon.textContent = "🎉";
      title.textContent = "Всё готово на сегодня!";
      sub.textContent = "Всё из плана закрыто — отличная работа.";
      return;
    }

    el.classList.remove("is-done");
    icon.textContent = "☀️";
    title.textContent = `Сегодня осталось: ${totalLeft}`;
    const parts = [];
    if (habitsLeft > 0) parts.push(`${habitsLeft} ${pluralRu(habitsLeft, "привычка", "привычки", "привычек")}`);
    if (planLeft > 0) parts.push(`${planLeft} ${pluralRu(planLeft, "задача", "задачи", "задач")}`);
    sub.textContent = parts.join(" · ");
  }

  // Roadmap #32 — баннер активного бустера x2 Adam Coin.
  function renderBoosterBanner() {
    const banner = document.getElementById("boosterBanner");
    const untilEl = document.getElementById("boosterBannerUntil");
    if (!banner) return;
    banner.hidden = !state.user?.xp_boosted;
    if (untilEl && state.user?.xp_boost_until) {
      try {
        const until = new Date(state.user.xp_boost_until);
        untilEl.textContent = ` до ${until.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}`;
      } catch (_) { untilEl.textContent = ""; }
    }
  }

  // Roadmap #12 — квесты дня: короткий список с прогресс-баром и кнопкой
  // "Забрать" у выполненных. Список живёт внутри модалки (#dailyQuestsOverlay,
  // открывается по кнопке #dailyQuestsBtn в "Сегодня") — карточка на весь
  // экран убрана с Главной по просьбе пользователя (перегружала экран).
  // Три постоянных квеста (db/quests.py, п.14): «Выполни 2 привычки», «Заполни план дня», «Поговори сегодня с Адамом». Награда выдаётся сама в
  // момент выполнения (кнопки «Забрать» больше нет), поэтому подсветка кнопки «Квесты» — только про сундук месяца. Строки рисует
  // questRowHtml; окно с анимацией заполнения полосок — openQuestsWindow (ниже, рядом с initDailyQuestActions).
  function renderDailyQuests() {
    const list = document.getElementById("dailyQuestsList");
    const btn = document.getElementById("dailyQuestsBtn");
    if (!list) return;
    const quests = Array.isArray(state.daily_quests) ? state.daily_quests : [];
    if (btn) {
      btn.hidden = quests.length === 0;
      btn.classList.toggle("has-claimable", (state.month_quests?.claimable || 0) > 0);
    }
    renderMonthCard();
    // Пока идёт показ изменения (полоски заполняются на глазах), список не пересобираем — он дорисует себя сам.
    if (questsAnim) return;
    list.innerHTML = quests.map((q) => questRowHtml(q, q, (state.habits || []).length, questsPlanReady())).join("");
    updateQuestChestHint();
  }

  // «Задания месяца» (db/month_quests.py): прогресс по уже забранным
  // ежедневным заданиям и 4 сундука по пути. Живёт в модалке квестов дня.
  function monthChestLabel(chest) {
    const parts = [`+${chest.coins}`];
    if (chest.diamonds) parts.push(`💎${chest.diamonds}`);
    return parts.join(" ");
  }

  function renderMonthCard(month) {
    const card = document.getElementById("monthCard");
    const mq = month || state?.month_quests;
    if (!card) return;
    if (!mq) { card.hidden = true; return; }
    card.hidden = false;
    const title = document.getElementById("monthCardTitle");
    const count = document.getElementById("monthCardCount");
    const track = document.getElementById("monthCardTrack");
    const openBtn = document.getElementById("monthCardOpen");
    const hint = document.getElementById("monthCardHint");
    if (title) title.textContent = mq.title || "Задания месяца";
    if (count) count.textContent = `${Math.min(mq.points, mq.goal)} / ${mq.goal}`;
    const pct = Math.min(100, Math.round(100 * mq.points / mq.goal));
    if (track) {
      track.innerHTML = `
        <div class="month-card__bar"><div class="month-card__bar-fill" style="width:${pct}%"></div></div>
        ${mq.chests.map((ch) => {
          const left = Math.round(100 * ch.at / mq.goal);
          const stateClass = ch.claimed ? "is-claimed" : (ch.reached ? "is-ready" : "");
          return `<div class="month-chest ${stateClass}" style="left:${left}%">
            <span class="month-chest__icon">${ch.claimed ? ICON_CHECK : `<span class="month-chest__glyph">${ch.final ? "🏆" : "🎁"}</span>`}</span>
            <span class="month-chest__at">${ch.at}</span>
            <span class="month-chest__reward">${monthChestLabel(ch)}</span>
          </div>`;
        }).join("")}`;
    }
    const nextChest = mq.chests.find((ch) => ch.reached && !ch.claimed);
    if (openBtn) {
      openBtn.hidden = !nextChest;
      openBtn.dataset.chest = nextChest ? String(nextChest.at) : "";
    }
    if (hint) {
      const daysLeft = Number(mq.days_left);
      const upcoming = mq.chests.find((ch) => !ch.reached);
      hint.textContent = nextChest
        ? `Сундук готов! Открой его до конца месяца (осталось ${daysLeft} дн.).`
        : upcoming
          ? `До ближайшего сундука — ${upcoming.at - Math.min(mq.points, upcoming.at)}. Каждое выполненное дело из списка выше — один балл. Сундуки открываются до конца месяца (осталось ${daysLeft} дн.).`
          : `Все сундуки месяца собраны — отличная работа (осталось ${daysLeft} дн.).`;
    }
  }

  // Принять свежие данные месяца; если по дороге открылся новый сундук —
  // сказать об этом.
  function applyMonthQuests(next) {
    const previous = state.month_quests;
    state.month_quests = next;
    if (previous && previous.month_key === next.month_key) {
      const reachedBefore = new Set(previous.chests.filter((c) => c.reached).map((c) => c.at));
      const fresh = next.chests.find((c) => c.reached && !reachedBefore.has(c.at));
      if (fresh) setTimeout(() => showToast("🎁 Сундук месяца готов — открой его в квестах дня", "praise", 4200), 1400);
    }
    renderMonthCard();
    const btn = document.getElementById("dailyQuestsBtn");
    if (btn) btn.classList.toggle("has-claimable", next.claimable > 0);
  }

  // Аватар-наставник ADAM вместо питомца-птенца (эмодзи 🥚→🐣→🐥→🦅). Картинка
  // зависит от состояния серии, а не от очков заботы: старт / первые дни /
  // стабильный рост / пик / серия под угрозой / оборвалась / долгий перерыв /
  // возвращение. Состояние целиком считает сервер (db/hero.py::get_hero_state
  // → state.hero) — здесь только отрисовка, никакой своей логики порогов.
  function renderHeroWidget() {
    const wrap = document.getElementById("heroWidget");
    if (!wrap) return;
    const hero = state?.hero;
    if (!hero) { wrap.hidden = true; return; }
    wrap.hidden = false;
    wrap.dataset.tone = hero.tone || "calm";
    wrap.dataset.state = hero.key || "";

    const img = document.getElementById("heroWidgetImg");
    if (img && img.getAttribute("src") !== hero.image) {
      wrap.classList.remove("is-no-image");
      img.onerror = () => wrap.classList.add("is-no-image");
      img.src = hero.image;
      // Разовое проявление при смене состояния (в лайт-режиме анимации нет).
      img.classList.remove("is-fresh");
      void img.offsetWidth;
      img.classList.add("is-fresh");
    }
    const title = document.getElementById("heroWidgetTitle");
    const caption = document.getElementById("heroWidgetCaption");
    if (title) title.textContent = hero.title || "";
    if (caption) caption.textContent = hero.caption || "";

    const bar = document.getElementById("heroWidgetBarFill");
    const hint = document.getElementById("heroWidgetHint");
    const progress = hero.progress || {};
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(progress.percent) || 0))}%`;
    if (hint) {
      const days = Number(hero.streak) || 0;
      hint.textContent = progress.to == null
        ? `🔥 ${days} · максимальная форма`
        : `🔥 ${days} · до «${progress.next_title}» ещё ${progress.days_left} дн.`;
    }
    syncHeroVideo();
    renderHeroEvoRow(hero.evolution);
    queueEvolutionCelebration();
  }

  // ----- Видео-петля героя (необязательна) -----
  // Если для состояния лежит {ключ}.mp4 (см. db/hero.py), сервер отдаёт
  // hero.video, и поверх картинки играет беззвучная петля. Картинка остаётся
  // подложкой и запасным вариантом: нет файла / ошибка / слабое устройство —
  // просто статика, как раньше. Ограничения из-за нагрева телефона и
  // нестабильной отрисовки Android WebView: видео создаётся только когда
  // карточка реально на экране, играет не дольше HERO_VIDEO_MAX_PLAY_MS за
  // один показ и ставится на паузу, как только карточка ушла с экрана или
  // приложение свернули.
  const HERO_VIDEO_MAX_PLAY_MS = 30000;
  const heroMotion = { video: null, visible: false, capped: false, timer: null };

  function heroMotionAllowed() {
    try {
      if (document.documentElement.classList.contains("performance-lite")) return false;
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
      if (navigator.connection && navigator.connection.saveData) return false;
    } catch (_) {}
    return true;
  }

  function createHeroVideo(hero, className) {
    const v = document.createElement("video");
    v.className = className;
    v.muted = true;
    v.defaultMuted = true;
    v.loop = true;
    v.playsInline = true;
    // Атрибуты (а не только свойства) нужны iOS, чтобы видео играло без
    // жеста пользователя и не уходило в полноэкранный плеер.
    v.setAttribute("muted", "");
    v.setAttribute("playsinline", "");
    v.setAttribute("webkit-playsinline", "");
    v.setAttribute("aria-hidden", "true");
    v.disablePictureInPicture = true;
    v.preload = "auto";
    v.poster = hero.image;
    v.dataset.src = hero.video;
    v.src = hero.video;
    // Показываем видео только когда кадры реально пошли — до этого видна
    // картинка, без чёрной вспышки.
    v.addEventListener("playing", () => v.classList.add("is-playing"));
    v.addEventListener("error", () => v.remove());
    return v;
  }

  function dropHeroVideo() {
    clearTimeout(heroMotion.timer);
    heroMotion.timer = null;
    if (heroMotion.video) {
      heroMotion.video.pause();
      heroMotion.video.remove();
      heroMotion.video = null;
    }
  }

  // Приводит видео в карточке к актуальному состоянию героя: создаёт (когда
  // карточка видна и видео разрешено), заменяет при смене состояния,
  // убирает, если для состояния видео нет.
  function syncHeroVideo() {
    const portrait = document.getElementById("heroWidgetPortrait");
    const hero = state?.hero;
    const wanted = portrait && hero && hero.video && heroMotionAllowed() ? hero.video : null;
    if (!wanted || !heroMotion.visible) {
      if (!wanted || heroMotion.video?.dataset.src !== wanted) dropHeroVideo();
      return;
    }
    if (!heroMotion.video || heroMotion.video.dataset.src !== wanted) {
      dropHeroVideo();
      heroMotion.video = createHeroVideo(hero, "hero-widget__video");
      portrait.appendChild(heroMotion.video);
    }
    updateHeroMotion();
  }

  function updateHeroMotion() {
    const v = heroMotion.video;
    if (!v) return;
    const lightboxOpen = !document.getElementById("heroLightbox")?.hidden || evolutionRun.active;
    const shouldPlay = heroMotion.visible && !document.hidden && !lightboxOpen && !heroMotion.capped;
    if (shouldPlay) {
      const p = v.play();
      if (p && p.catch) p.catch(() => {});
      if (!heroMotion.timer) {
        heroMotion.timer = setTimeout(() => {
          heroMotion.timer = null;
          heroMotion.capped = true;
          updateHeroMotion();
        }, HERO_VIDEO_MAX_PLAY_MS);
      }
    } else {
      v.pause();
      clearTimeout(heroMotion.timer);
      heroMotion.timer = null;
    }
  }

  // Тап по картинке героя — увеличенный вид с подписью; любой тап закрывает.
  function initHeroWidget() {
    const portrait = document.getElementById("heroWidgetPortrait");
    const box = document.getElementById("heroLightbox");
    const wrap = document.getElementById("heroWidget");
    if (!portrait || !box) return;

    // Видео в карточке играет, только пока карточка на экране (вкладка
    // «Профиль» открыта и карточка прокручена в видимую область).
    if (wrap && "IntersectionObserver" in window) {
      new IntersectionObserver((entries) => {
        const entry = entries[entries.length - 1];
        heroMotion.visible = !!entry && entry.isIntersecting;
        if (!heroMotion.visible) heroMotion.capped = false;
        syncHeroVideo();
        updateHeroMotion();
      }, { threshold: 0.5 }).observe(wrap);
    } else {
      heroMotion.visible = true;
    }
    document.addEventListener("visibilitychange", updateHeroMotion);

    const closeLightbox = () => {
      box.hidden = true;
      box.querySelector(".hero-lightbox__video")?.remove();
      updateHeroMotion();
    };
    portrait.addEventListener("click", () => {
      const hero = state?.hero;
      if (!hero) return;
      haptic("light");
      document.getElementById("heroLightboxImg").src = hero.image;
      document.getElementById("heroLightboxTitle").textContent = hero.title || "";
      document.getElementById("heroLightboxText").textContent = hero.caption || "";
      box.dataset.tone = hero.tone || "calm";
      box.querySelector(".hero-lightbox__video")?.remove();
      box.hidden = false;
      updateHeroMotion(); // ставит на паузу видео карточки под оверлеем
      if (hero.video && heroMotionAllowed()) {
        const v = createHeroVideo(hero, "hero-lightbox__video");
        document.getElementById("heroLightboxFrame")?.appendChild(v);
        const p = v.play();
        if (p && p.catch) p.catch(() => {});
      }
    });
    box.addEventListener("click", closeLightbox);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLightbox(); });
  }

  // Герой «вырос» (серия перешла в следующую полосу: старт → первые дни →
  // стабильный рост → пик) — вместо прежнего тоста «Питомец вырос».
  function announceHeroGrowth(previous, current) {
    if (!previous || !current) return;
    if (!(Number(current.band) > Number(previous.band))) return;
    setTimeout(() => {
      showToast(`✨ Я стал сильнее: «${current.title}»`, "praise", 4000);
    }, 1200);
  }

  // Roadmap #13 — тир лиги + прогресс до следующего, в профиле.
  function renderLeagueInfo() {
    const el = document.getElementById("leagueInfo");
    if (!el) return;
    const tier = state.user?.league_tier;
    if (!tier) { el.hidden = true; return; }
    el.hidden = false;
    // Просьба пользователя: графики везде — вместо голой строки текста
    // визуальный прогресс-бар до следующей лиги (progress_pct уже считался
    // на бэкенде, см. db/leagues.py::get_league_progress).
    const progress = state.user?.league_progress;
    if (progress) {
      el.innerHTML = `
        <div class="league-progress__head">
          <span>${escapeHtml(tier)}</span>
          <span>до «${escapeHtml(progress.next_tier)}» — ${progress.xp_needed} XP</span>
        </div>
        <div class="league-progress__track"><div class="league-progress__fill" style="width:${Math.max(3, progress.progress_pct)}%"></div></div>
      `;
    } else {
      el.innerHTML = `
        <div class="league-progress__head">
          <span>${escapeHtml(tier)}</span>
          <span>максимальная лига</span>
        </div>
        <div class="league-progress__track"><div class="league-progress__fill league-progress__fill--max" style="width:100%"></div></div>
      `;
    }
  }

  // Roadmap #48 — светлая/тёмная тема. Ставим на <html> (не <body>,
  // чтобы точно попасть под каждый ":root[data-mode=...]" в style.css),
  // применяем при каждом renderAll() — так же, как акцентная тема
  // (data-theme) применяется в renderThemePicker(), только это app-wide
  // и не требует покупки.
  // Roadmap #46 — словарь для [data-i18n]-элементов. Покрывает вкладки,
  // главные заголовки разделов и переключатель языка — самые заметные,
  // всегда видимые места, а не построчный перевод вообще всего текста
  // приложения (сотни строк — нереалистично за один заход, см. отчёт
  // пользователю). Динамические AI-ответы переводятся отдельно, через
  // инструкцию языка в build_user_context (webapp/services/ai_utils.py).
  const I18N = {
    ru: {
      tab_home: "Главная", tab_calendar: "Календарь", tab_ai: "ИИ", tab_rating: "Рейтинг", tab_profile: "Профиль",
      plan_title: "План дня", calendar_title: "Календарь", rating_title: "Рейтинг",
      shop_title: "Магазин ADAM", theme_title: "Тема оформления", achievements_title: "Достижения",
      progress_title: "📊 Прогресс", settings_title: "⚙️ Настройки", data_support_title: "Данные и поддержка",
      language_title: "Язык",
    },
    en: {
      tab_home: "Home", tab_calendar: "Calendar", tab_ai: "AI", tab_rating: "Rating", tab_profile: "Profile",
      plan_title: "Today's Plan", calendar_title: "Calendar", rating_title: "Rating",
      shop_title: "ADAM Shop", theme_title: "Theme", achievements_title: "Achievements",
      progress_title: "📊 Progress", settings_title: "⚙️ Settings", data_support_title: "Data & Support",
      language_title: "Language",
    },
  };

  function applyLanguage() {
    currentLanguage = state?.settings?.language === "en" ? "en" : "ru";
    const dict = I18N[currentLanguage];
    document.querySelectorAll("[data-i18n]").forEach(el => {
      const key = el.dataset.i18n;
      if (dict[key]) el.textContent = dict[key];
    });
    document.querySelectorAll(".language-picker-btn, #languagePicker .color-mode-btn").forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.lang === currentLanguage);
    });
  }

  // Светлая тема убрана по решению пользователя — приложение развивается
  // только в тёмных тонах, независимо от того, что сохранено на сервере
  // (в т.ч. у тех, кто успел включить светлую тему раньше).
  function applyColorMode() {
    document.documentElement.setAttribute("data-mode", "dark");
  }

  // Фидбек #4: подсвечиваем кнопку пола, только если он реально известен
  // (явно выбран или угадан по имени сервером) — если gender === null,
  // ни одна кнопка не подсвечена, а не "Он" по умолчанию (это выглядело
  // бы как уже принятое за пользователя решение).
  function applyGender() {
    const gender = state?.settings?.gender || null;
    document.querySelectorAll("#genderPicker .color-mode-btn").forEach(btn => {
      btn.classList.toggle("is-active", gender && btn.dataset.gender === gender);
    });
  }

  function renderAll() {
    // Критический путь: сначала только то, что пользователь видит на Главной.
    // Привычки и Ударный режим больше не конкурируют за CPU с магазином,
    // рейтингом и архивом достижений.
    renderPlayerCard();
    renderSelfReward();
    applyHomeLayout();
    renderHabits();
    renderPlan();
    renderTodayFocus();
    renderTrialCard();
    renderStreak();
    renderBoosterBanner();
    renderDailyQuests();
    renderPairCard();
    renderGiftsBadge();
    renderLeagueInfo();
    applyColorMode();
    applyLanguage();
    applyGender();
    renderHeroWidget();
    const bw = state?.bonus_window;
    setBonusWindow(bw && bw.active ? bw.until : null);
    maybeShowStartQuiz();
    maybeShowStreakOnboarding();
    maybeShowAdamIntro();
    stabilizeFirstPaint();
    maybeScheduleEveningAdamNudge();
    scheduleAdminGift();
    // Warm the profile data in the background. This makes a later tap on
    // "Профиль" instant without adding anything to the initial critical path.
    scheduleProfilePrefetch();
    scheduleRatingPrefetch();

    // Второстепенные вкладки дорисовываем после первого кадра, когда браузер
    // освободит основной поток. Качество UI не меняется — меняется только
    // порядок работы.
  }

  // Второстепенные данные (магазин, рейтинг, достижения, календарь) отдаёт
  // отдельный /api/bootstrap-secondary — раньше этот запрос нигде не
  // вызывался, поэтому state.shop_items/leaderboard/achievements/calendar_events
  // всегда оставались undefined и вкладки выглядели постоянно пустыми.
  const secondaryPromises = new Map();
  const secondaryLoaded = new Set();
  // /api/bootstrap этих данных не содержит — их отдельно догружает
  // loadBootstrapSecondary. Раньше state заменялся целиком, и магазин с
  // достижениями и рейтингом пропадали: рамки в «Профиле» из «Доступна»
  // превращались в «Не куплена» после любого обновления (например, возврата в
  // приложение), хотя на сервере они куплены или подарены.
  const SECONDARY_STATE_KEYS = ["shop_items", "achievements", "leaderboard", "rating_league", "calendar_events"];
  function carrySecondaryData(previous, next) {
    if (!previous || !next) return;
    for (const key of SECONDARY_STATE_KEYS) {
      if (next[key] === undefined && previous[key] !== undefined) next[key] = previous[key];
    }
  }
  let profilePrefetchScheduled = false;
  let ratingPrefetchScheduled = false;
  let teamSeasonPromise = null;

  function scheduleRatingPrefetch() {
    if (ratingPrefetchScheduled || secondaryLoaded.has("rating")) return;
    ratingPrefetchScheduled = true;
    // Рейтинг — единственная вторичная вкладка, которую пользователь
    // действительно часто открывает сразу после запуска. Раньше он ждал
    // requestIdleCallback до ~2.2 с, поэтому даже быстрый сервер ощущался
    // медленным. Запускаем после первого кадра и ПАРАЛЛЕЛЬНО прогружаем
    // групповой челлендж + сезонный рейтинг, чтобы команда не появлялась
    // последней.
    const run = () => {
      loadBootstrapSecondary("rating");
      loadTeamAndSeason();
    };
    if ("requestAnimationFrame" in window) {
      requestAnimationFrame(() => setTimeout(run, 80));
    } else {
      setTimeout(run, 120);
    }
  }

  function scheduleProfilePrefetch() {
    if (profilePrefetchScheduled || secondaryLoaded.has("profile")) return;
    profilePrefetchScheduled = true;
    const run = () => loadBootstrapSecondary("profile");
    if ("requestIdleCallback" in window) {
      requestIdleCallback(run, { timeout: 1800 });
    } else {
      setTimeout(run, 1200);
    }
  }

  function getTabPanel(key) {
    return document.querySelector(`.tab-panel[data-tab="${key}"]`);
  }

  function setTabLoading(key, loading, message = "", retryKey = "") {
    const panel = getTabPanel(key);
    if (!panel) return;
    panel.classList.toggle("tab-panel--loading", !!loading);
    panel.setAttribute("aria-busy", loading ? "true" : "false");
    let layer = panel.querySelector(":scope > .tab-loading-state");

    // ВАЖНО: раньше здесь при loading=false сразу удаляли layer.remove(),
    // а затем НИЖЕ (при message) пытались переиспользовать ту же
    // переменную layer — но removе() отсоединяет узел от DOM, а не
    // обнуляет переменную, так что баннер ошибки молча писался в
    // невидимый отсоединённый элемент и никогда не появлялся на экране.
    // Теперь у каждого состояния (загрузка / ошибка / ничего) — свой
    // явный путь без повторного использования удалённого узла.
    if (loading) {
      if (!layer) {
        layer = document.createElement("div");
        layer.className = "tab-loading-state";
        panel.prepend(layer);
      }
      layer.hidden = false;
      layer.innerHTML = '<div class="tab-loading-state__spinner" aria-hidden="true"></div><span>Загрузка…</span>';
      return;
    }

    if (message) {
      if (!layer) {
        layer = document.createElement("div");
        layer.className = "tab-loading-state";
        panel.prepend(layer);
      }
      layer.hidden = false;
      layer.innerHTML =
        `<div class="tab-loading-state__error">${escapeHtml(message)}</div>` +
        (retryKey
          ? `<button type="button" class="tab-loading-state__retry" data-retry-section="${escapeHtml(retryKey)}">↻ Повторить</button>`
          : "");
      return;
    }

    // Ни загрузки, ни ошибки — индикатор целиком не нужен.
    // Полностью удаляем его, а не просто скрываем — так он не сможет
    // остаться поверх нижнего меню из-за CSS/кэша WebView.
    if (layer) layer.remove();
  }

  // Один делегированный обработчик на все кнопки "Повторить" в баннерах
  // ошибок вкладок — сами баннеры создаются/пересоздаются динамически.
  document.addEventListener("click", (e) => {
    const retryBtn = e.target.closest(".tab-loading-state__retry");
    if (!retryBtn) return;
    const key = retryBtn.dataset.retrySection;
    if (!key) return;
    haptic("light");
    loadBootstrapSecondary(key);
  });

  async function loadBootstrapSecondary(section) {
    const key = section || "profile";
    if (secondaryLoaded.has(key)) return state;
    if (secondaryPromises.has(key)) return secondaryPromises.get(key);

    // Profile already has its critical content in the main bootstrap.
    // Never hide the whole profile while shop/achievements are loading:
    // on Telegram WebView the secondary request can take 1–3s and the old
    // full-panel loader looked like a frozen/empty page.
    if (key !== "profile") setTabLoading(key, true);
    const promise = (async () => {
      let lastError = null;
      // Один короткий повтор при временной сетевой заминке — так же, как
      // уже делает loadBootstrap() для главного экрана. Без этого любой
      // единичный сбой сети навсегда оставлял вкладку с "request_failed"
      // до ручной перезагрузки Mini App.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const data = await api(`/api/bootstrap-secondary?section=${encodeURIComponent(key)}`, { timeoutMs: 10000 });
          if (state) {
            if (key === "profile") {
              state.shop_items = data.shop_items || [];
              state.achievements = data.achievements || [];
              renderShop();
              renderSelfReward();
              renderProfileAvatarControls();
              renderThemePicker();
              renderAchievements();
              loadActivityFeed();
              // The profile must become interactive immediately. The year
              // heatmap is secondary data; load it after the first profile
              // paint instead of competing with shop/achievements for the
              // same first WebView frame.
              const loadCalendarLater = () => loadBootstrapSecondary("calendar");
              if ("requestIdleCallback" in window) {
                requestIdleCallback(loadCalendarLater, { timeout: 1200 });
              } else {
                setTimeout(loadCalendarLater, 900);
              }
              stabilizeFirstPaint(["shopList", "achievementList", "achievementArchiveList", "heroWidget"]);
            } else if (key === "rating") {
              state.leaderboard = data.leaderboard || [];
              state.rating_league = data.rating_league || null;
              renderRating();
              // Team + season are already prefetched in parallel from boot.
              // Не запускаем второй комплект запросов при ответе рейтинга.
              if (!teamSeasonPromise) loadTeamAndSeason();
              stabilizeFirstPaint(["ratingList"]);
            } else if (key === "calendar") {
              state.calendar_events = data.calendar_events || [];
              renderCalendar();
              renderYearHeatmap();
              stabilizeFirstPaint(["calendarGrid"]);
            }
            secondaryLoaded.add(key);
            if (key !== "profile") setTabLoading(key, false);
          }
          return state;
        } catch (err) {
          lastError = err;
          if (attempt < 2 && (!err.status || err.status >= 500 || err.code === "timeout")) {
            await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
            continue;
          }
          break;
        }
      }
      console.error(`bootstrap-secondary(${key}) failed:`, lastError);
      if (key !== "profile") {
        setTabLoading(key, false, friendlyError(lastError) || "Не удалось загрузить раздел", key);
      } else {
        // Keep the already-rendered profile usable even if optional data fails.
        const shop = document.getElementById("shopList");
        if (shop && !shop.children.length) {
          shop.innerHTML = '<li class="empty-hint">Магазин временно недоступен</li>';
        }
      }
      showToast(friendlyError(lastError) || "Не удалось загрузить раздел", "error");
      return state;
    })().finally(() => secondaryPromises.delete(key));

    secondaryPromises.set(key, promise);
    return promise;
  }

  // Магазин и рамки «Профиля» устарели — например, пока приложение было
  // свёрнуто, пришёл подарок. Если профиль открыт, перечитываем сразу, иначе при
  // следующем заходе во вкладку (loadBootstrapSecondary вызывается на каждый
  // переход, а загруженным раздел перестаёт считаться именно здесь).
  function invalidateProfileData() {
    if (!secondaryLoaded.has("profile")) return;
    secondaryLoaded.delete("profile");
    if (document.querySelector(".tab-bar__item.is-active")?.dataset.tab === "profile") {
      loadBootstrapSecondary("profile");
    }
  }

  // Пока Mini App не виден, декоративные анимации не должны тратить батарею/CPU.
  // При возврате браузер продолжает их с текущего состояния без резкого скачка.
  document.addEventListener("visibilitychange", () => {
    document.documentElement.classList.toggle(
      "app-performance-paused",
      document.hidden
    );
  });

  // Раньше чисто атмосферные эффекты (искры, блики) играли первые 3.5с
  // после каждого открытия/обновления/смены вкладки, потом сами гасли
  // классом decor-settled. По фидбеку пользователя это ощущалось как лаг
  // в первые секунды при каждом входе — убрали "всплеск" совсем: decor-settled
  // включается сразу и никогда не снимается (все call site'ы ниже дергают
  // эту же функцию, поэтому один общий "always settled" здесь достаточно).
  function scheduleDecorSettle() {
    document.documentElement.classList.add("decor-settled");
  }
  scheduleDecorSettle();

  // ===================== УДАРНЫЙ РЕЖИМ =====================
  let streakCelebrationTimer = null;

  function renderStreak() {
    const streak = state?.streak;
    if (!streak) return;
    const daysEl = document.getElementById("streakWidgetDays");
    if (daysEl) daysEl.textContent = formatDays(streak.days || 0);

    const days = document.getElementById("streakDays");
    if (days) {
      const last7 = Array.isArray(streak.last7) ? streak.last7 : [];
      days.innerHTML = last7.map((d) => {
        const cls = d.status === "completed" ? "is-done" :
          d.status === "freeze" ? "is-freeze" :
          d.status === "missed" ? "is-missed" : "is-empty";
        const icon = d.status === "completed" ? "🔥" :
          d.status === "freeze" ? "❄️" :
          d.status === "missed" ? "·" : "○";
        return `<button class="streak-day ${cls}" type="button" title="${escapeHtml(d.day)}: ${escapeHtml(d.status)}">
          <span>${icon}</span><small>${d.label}</small>${d.bonus ? '<b>🎁</b>' : ''}
        </button>`;
      }).join("");
    }

    const balance = document.getElementById("freezeBalanceLabel");
    if (balance) balance.textContent = `Заморозки: ${streak.freeze_balance || 0}/2`;
    const buy = document.getElementById("freezeBuyBtn");
    if (buy) buy.disabled = (streak.freeze_balance || 0) >= 2 || (streak.freeze_purchased_count || 0) >= 2;

    // Улучшение #50: бесплатное восстановление сорванной серии, раз в месяц.
    const restoreBtn = document.getElementById("freeRestoreBtn");
    if (restoreBtn) {
      const restore = streak.free_restore;
      if (restore && restore.available) {
        restoreBtn.hidden = false;
        const daysEl = document.getElementById("freeRestoreDays");
        if (daysEl) daysEl.textContent = restore.lost_streak;
      } else {
        restoreBtn.hidden = true;
      }
    }

    const status = document.getElementById("streakStatusLabel");
    if (status) {
      status.textContent = streak.days > 0
        ? `Огонь горит. Не дай ему погаснуть.`
        : `Серия сброшена. Можно начать заново.`;
    }
    const weekHint = document.getElementById("streakWeekHint");
    if (weekHint) {
      const last7 = Array.isArray(streak.last7) ? streak.last7 : [];
      const done7 = last7.filter(d => d.status === "completed").length;
      const frozen7 = last7.filter(d => d.status === "freeze").length;
      if (weekHint) {
        if (done7 === 7) {
          weekHint.textContent = "7/7";
          weekHint.dataset.subtext = "БЕЗ ПРОПУСКОВ";
        } else if (done7 > 0) {
          weekHint.textContent = `${done7}/7`;
          weekHint.dataset.subtext = "НАДО ПОДНАЖАТЬ И ПОСТАРАТЬСЯ НА СЛЕДУЮЩЕЙ НЕДЕЛЕ ЛУЧШЕ СПРАВИТЬСЯ";
        } else {
          weekHint.textContent = "0/7";
          weekHint.dataset.subtext = "НАДО ПОДНАЖАТЬ И ПОСТАРАТЬСЯ НА СЛЕДУЮЩЕЙ НЕДЕЛЕ ЛУЧШЕ СПРАВИТЬСЯ";
        }
      }
    }

    const profileStatus = document.getElementById("profileStreakStatus");
    const profileFrame = document.getElementById("profileStreakFrame");
    let streakStatus = streak.temp_status || "";
    if (/^Огонь\s+/i.test(streakStatus)) {
      streakStatus = streakStatus.replace(/^Огонь/i, "В ударе");
    }
    const streakDays = Number(streak.days || 0);
    const reward = (streak.rewards || [])[0];
    // "В ударе N дн." (или голое "В ударе") просто повторяет число, которое
    // и так показано крупно в .streak-profile-days ниже — показываем эту
    // строку, только если там что-то содержательное (например название
    // вехи "Месяц в ударе" при достижении рамки).
    const isRedundantStreakLabel = /^В ударе(\s+\d+\s+дн\.?)?$/i.test(streakStatus.trim());
    const statusText = (streakStatus && !isRedundantStreakLabel)
      ? streakStatus
      : (streakDays ? "" : "Серия не начата");
    if (profileStatus) {
      profileStatus.textContent = statusText;
      profileStatus.hidden = !statusText;
    }
    if (profileFrame) {
      if (reward) {
        profileFrame.textContent = `Рамка «${reward.frame}»`;
        profileFrame.className = "streak-profile-frame frame-" + (streak.temp_frame || "none");
        profileFrame.hidden = false;
      } else {
        // No reward yet — avoid showing a placeholder that duplicates the
        // "ТВОЯ УДАРНАЯ СЕРИЯ" kicker above it.
        profileFrame.textContent = "";
        profileFrame.className = "streak-profile-frame frame-none";
        profileFrame.hidden = true;
      }
    }
    const profileDays = document.getElementById("profileStreakDays");
    const profileDaysLabel = document.getElementById("profileStreakDaysLabel");
    const metricStreak = document.getElementById("profileMetricStreak");
    const metricFreeze = document.getElementById("profileMetricFreeze");
    if (profileDays) profileDays.textContent = streakDays;
    if (profileDaysLabel) profileDaysLabel.textContent = pluralRu(streakDays, "день подряд", "дня подряд", "дней подряд");
    if (metricStreak) metricStreak.textContent = streakDays;
    if (metricFreeze) metricFreeze.textContent = `${streak.freeze_balance || 0}/2`;
    renderStreakStatus(streak.status, streakDays);
  }

  // ---- Статус серии: название ступени вместо дней вехи-награды (db/streak_status.py) ----
  // Один значок на ступень (24×24, заливка цветом ступени). Ключи совпадают с db/streak_status.py::LADDER.
  const STATUS_GLYPHS = {
    start: '<circle cx="12" cy="12" r="6.2" fill="none" stroke="currentColor" stroke-width="2.4"/>',
    tempering: '<path d="M13.2 2.5 5 13.5h5.6l-1.2 8 8.4-11.4h-5.8z" fill="currentColor"/>',
    flint: '<path d="M12 2.5l2.4 6.9 6.9 2.6-6.9 2.6L12 21.5l-2.4-6.9L2.7 12l6.9-2.6z" fill="currentColor"/>',
    damask: '<path d="M12 2.8l7 2.7v5.8c0 4.7-2.9 8-7 9.9-4.1-1.9-7-5.2-7-9.9V5.5z" fill="currentColor"/>',
    monolith: '<path d="M12 2.6l7.8 4.5v9.8L12 21.4l-7.8-4.5V7.1z" fill="currentColor"/><path d="M12 2.6l7.8 4.5L12 11.6 4.2 7.1z" fill="#fff" fill-opacity=".34"/>',
    titan: '<path d="M3.5 18.5 5 7.5l4.6 4.2L12 5l2.4 6.7L19 7.5l1.5 11z" fill="currentColor"/><rect x="4.5" y="19.7" width="15" height="2" rx="1" fill="currentColor"/>',
    zenith: '<circle cx="12" cy="12" r="4.3" fill="currentColor"/><path d="M12 2.6v3M12 18.4v3M2.6 12h3M18.4 12h3M5.4 5.4l2.1 2.1M16.5 16.5l2.1 2.1M18.6 5.4l-2.1 2.1M7.5 16.5l-2.1 2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    absolute: '<path d="M12 2.6l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.2 1.2-6.5L2.5 9.5l6.6-.9z" fill="currentColor"/>',
  };

  function statusGlyph(key) {
    return `<svg viewBox="0 0 24 24" width="100%" height="100%" focusable="false" aria-hidden="true">${STATUS_GLYPHS[key] || STATUS_GLYPHS.start}</svg>`;
  }

  function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    if (!m) return "154,148,173";
    const n = parseInt(m[1], 16);
    return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
  }

  function renderStreakStatus(status) {
    const tile = document.getElementById("profileMetricStatus");
    if (!tile || !status) return;
    tile.dataset.tier = String(status.tier || 0);
    tile.style.setProperty("--st", status.accent);
    tile.style.setProperty("--st-rgb", hexToRgb(status.accent));
    document.getElementById("profileMetricBadge").innerHTML = statusGlyph(status.key);
    document.getElementById("profileMetricReward").textContent = status.name;
    const next = status.next;
    const left = next ? `${next.left} ${pluralRu(next.left, "день", "дня", "дней")}` : "";
    document.getElementById("profileMetricNext").textContent = next ? `→ ${next.name} · ${left}` : "высший статус";
    tile.setAttribute("aria-label", `Статус серии: ${status.name}. ${next ? `До «${next.name}» ещё ${left}.` : "Это высший статус."} Нажми, чтобы увидеть все статусы`);
    streakStatusInfo = status;
  }

  let statusLadderOpenedAt = 0;
  let streakStatusInfo = null;

  function openStatusLadder() {
    const overlay = document.getElementById("statusLadderOverlay");
    const status = streakStatusInfo;
    if (!overlay || !overlay.hidden || !status) return;
    const days = Number(state.streak?.days || 0);
    const daysWord = (n) => `${n} ${pluralRu(n, "день", "дня", "дней")}`;
    document.getElementById("statusLadderLead").innerHTML = status.next
      ? `Сейчас у тебя <b>${daysWord(days)}</b> серии — статус «${escapeHtml(status.name)}». До «${escapeHtml(status.next.name)}» ещё <b>${daysWord(status.next.left)}</b>.`
      : `Серия — <b>${daysWord(days)}</b>. Это высший статус.`;
    document.getElementById("statusLadderList").innerHTML = status.ladder.map((row) => {
      const current = row.tier === status.tier;
      const note = current ? "сейчас" : row.reached ? "получен" : `ещё ${daysWord(row.left)}`;
      const from = row.days ? `от ${row.days} дней подряд` : "начало пути";
      return `<li class="status-ladder__row${current ? " is-current" : ""}${row.reached ? "" : " is-locked"}" style="--st:${row.accent};--st-rgb:${hexToRgb(row.accent)}" data-tier="${row.tier}">` +
        `<span class="status-ladder__badge" aria-hidden="true">${statusGlyph(row.key)}</span>` +
        `<span class="status-ladder__text"><b>${escapeHtml(row.name)}</b><small>${from} · ${escapeHtml(row.blurb)}</small></span>` +
        `<em class="status-ladder__note">${note}</em></li>`;
    }).join("");
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    statusLadderOpenedAt = performance.now();
    requestAnimationFrame(() => {
      overlay.classList.add("show");
      overlay.querySelector(".status-ladder__row.is-current")?.scrollIntoView({ block: "center" });
    });
    haptic("light");
  }

  function closeStatusLadder() {
    const overlay = document.getElementById("statusLadderOverlay");
    if (!overlay || overlay.hidden) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 240);
  }

  function openStreakCelebration(event) {
    const overlay = document.getElementById("streakCelebrationOverlay");
    const message = document.getElementById("streakCelebrationMessage");
    const seven = document.getElementById("celebrationSeven");
    if (!overlay || !event) return;
    // event.reward — только у симуляции (SIM_SCENES.streak_status); настоящий статус берётся из наград серии
    const reward = event.reward || (state.streak?.rewards || []).find(r => Number(r.milestone) === Number(event.streak));
    if (message) {
      // Слова под заголовком — слова Адама: маленькая аватарка слева, а новый статус — от первого лица.
      const words = reward
        ? `🏆 Новый статус «${reward.status}» — открыл для тебя рамку «${reward.frame}». ${event.message || ""}`
        : (event.message || "Отличный ход.");
      message.innerHTML = `${adamVoiceHtml("sm")}${escapeHtml(words)}`;
    }
    if (seven) {
      // event.last7 — только у симуляции (SIM_SCENES.streak): настоящее событие недели с собой не несёт
      seven.innerHTML = (event.last7 || state.streak?.last7 || []).map(d => {
        const cls = d.status === "completed" ? "is-done" : d.status === "freeze" ? "is-freeze" : "is-empty";
        return `<span class="streak-seven__day ${cls}">${d.status === "completed" ? "🔥" : d.status === "freeze" ? "❄️" : "○"}</span>`;
      }).join("");
    }
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => overlay.classList.add("show"));
    haptic(reward ? "triple" : "success");        // новый статус серии — сложное достижение: тройная долгая вибрация
    playChime();
    const fire = document.getElementById("streakCelebrationFire");
    fire?.classList.remove("ignite");
    requestAnimationFrame(() => fire?.classList.add("ignite"));
    clearTimeout(streakCelebrationTimer);
    // Окно не закрывается само: пользователь должен осознанно нажать «Продолжить».
    streakCelebrationTimer = null;
  }

  function closeStreakCelebration() {
    const overlay = document.getElementById("streakCelebrationOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    clearTimeout(streakCelebrationTimer);
    setTimeout(() => { overlay.hidden = true; }, 350);
  }

  // ===================== ДВОЙНЫЕ ADAM COIN (промт 8) =====================
  // После самой первой привычки дня (если у пользователя 2+ привычки)
  // показываем один раз большое окно с объяснением. Дальше механика
  // (удвоение + продление на 30 минут при каждой следующей отметке, пока
  // остаются незакрытые привычки) работает молча — её отражает бейдж с
  // обратным отсчётом и подпись "×2" в тосте с монетами.
  let pendingBonusIntro = false;
  let bonusWindowUntil = null;
  let bonusCountdownTimer = null;

  function updateBonusBadge() {
    const badge = document.getElementById("doubleBonusBadge");
    const timerEl = document.getElementById("doubleBonusTimer");
    if (!badge) return;
    if (!bonusWindowUntil) {
      badge.hidden = true;
      clearInterval(bonusCountdownTimer);
      bonusCountdownTimer = null;
      return;
    }
    const msLeft = bonusWindowUntil.getTime() - Date.now();
    if (msLeft <= 0) {
      bonusWindowUntil = null;
      badge.hidden = true;
      clearInterval(bonusCountdownTimer);
      bonusCountdownTimer = null;
      return;
    }
    badge.hidden = false;
    const totalSec = Math.ceil(msLeft / 1000);
    const mm = String(Math.floor(totalSec / 60)).padStart(2, "0");
    const ss = String(totalSec % 60).padStart(2, "0");
    if (timerEl) timerEl.textContent = `${mm}:${ss}`;
  }

  function setBonusWindow(untilIso) {
    bonusWindowUntil = untilIso ? new Date(untilIso) : null;
    clearInterval(bonusCountdownTimer);
    bonusCountdownTimer = null;
    updateBonusBadge();
    if (bonusWindowUntil) {
      bonusCountdownTimer = setInterval(updateBonusBadge, 1000);
    }
  }

  function openBonusIntro() {
    const overlay = document.getElementById("doubleBonusOverlay");
    if (!overlay) return;
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => overlay.classList.add("show"));
    haptic("confirm");
    playChime("bonus");
  }

  function closeBonusIntro() {
    const overlay = document.getElementById("doubleBonusOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 300);
  }

  // Вопросы теста на архетип (Стратег/Марафонец/Спринтер/Исследователь) —
  // раньше жили только в Настройках (см. initArchetypeQuizActions ниже),
  // теперь это и есть "первый тест" прямо в онбординге (просьба
  // пользователя). Объявлены здесь (а не рядом со старым использованием),
  // потому что START_QUIZ_STEPS ниже строится из этого массива при первом
  // же выполнении файла — объявление должно идти раньше по тексту.
  const ARCHETYPE_QUIZ_QUESTIONS = [
    {
      q: "Как ты предпочитаешь идти к цели?",
      options: [
        ["Продуманный план наперёд", "strategist"],
        ["Ровный темп, день за днём", "marathoner"],
        ["Короткие мощные рывки", "sprinter"],
        ["Пробую разное по ходу", "explorer"],
      ],
    },
    {
      q: "Что мотивирует сильнее всего?",
      options: [
        ["Видеть прогресс к большой цели", "strategist"],
        ["Не прерывать серию ни на день", "marathoner"],
        ["Азарт прямо здесь и сейчас", "sprinter"],
        ["Новизна и разнообразие", "explorer"],
      ],
    },
    {
      q: "Пропустил день — что делаешь?",
      options: [
        ["Разбираю, что пошло не так", "strategist"],
        ["Просто продолжаю с завтра", "marathoner"],
        ["Наверстываю вдвойне", "sprinter"],
        ["Пробую заменить на другое", "explorer"],
      ],
    },
    {
      q: "Идеальная привычка — это та, что...",
      options: [
        ["Ведёт к измеримому результату", "strategist"],
        ["Стала частью рутины, без усилий", "marathoner"],
        ["Даёт быстрый результат", "sprinter"],
        ["Интересно пробовать", "explorer"],
      ],
    },
  ];

  // Резервные подписи на случай, если /api/settings/archetype не ответит
  // (офлайн и т.п.) — должны совпадать с db/users.py::ARCHETYPES.
  const ARCHETYPE_LABELS = {
    strategist: "🎯 Стратег",
    marathoner: "🧗 Марафонец",
    sprinter: "🏃 Спринтер",
    explorer: "🔭 Исследователь",
  };

  // Короткие честные описания результата — выведены из смысла самих
  // вопросов выше, без придуманных внешних характеристик.
  const ARCHETYPE_BLURBS = {
    strategist: "Ты любишь чёткий план — и это сила. Я буду помогать раскладывать большие цели на понятные шаги.",
    marathoner: "Для тебя главное — не прерывать серию. Я буду держать твой ритм и напоминать вовремя.",
    sprinter: "Тебя заряжают короткие мощные рывки. Я буду подкидывать вызовы и быстрые победы.",
    explorer: "Тебе интересно пробовать новое. Я помогу находить свежие привычки и не заскучать.",
  };

  // ===================== СТАРТОВЫЙ КВИЗ + ВОРОНКА (ВОЗРАСТ/ЦЕЛЬ/АРХЕТИП/ОТЗЫВЫ/РЕФЕРАЛ/ПОДПИСКА) =====================
  // Просьба пользователя: короткие шаги с уже готовыми вариантами ответа
  // (без свободного ввода) ПЕРЕД app-tour — по образцу популярных
  // фитнес-приложений. Первые 2 шага (возраст/цель) + 4 вопроса архетипа
  // обязательны и сохраняются на сервере (см. /api/start-quiz/seen,
  // db/users.py::mark_start_quiz_seen, /api/settings/archetype), следующие
  // 3 (отзывы/реферал/подписка) — информационные, "Далее" там всегда
  // активна. Отзывы — БЕЗ выдуманных чужих фото/имён с привязкой к
  // несуществующим внешним рейтингам (это было бы введением в заблуждение) —
  // только иллюстративные примеры. Подписка — пока ТОЛЬКО превью без
  // реального списания Stars (SUBSCRIPTION_GATE_ENABLED осознанно остаётся
  // выключен, просьба пользователя — сначала UI, включать биллинг будем
  // отдельно и осознанно).
  const START_QUIZ_STEPS_ALL = [
    // Первый экран квиза раньше был вопросом «Сколько тебе лет?» — без единого слова о том, что это за приложение и зачем вопросы.
    { type: "welcome", title: "Привет!" },
    {
      type: "choice",
      key: "age_range",
      title: "Сколько тебе лет?",
      options: [
        ["До 18", "under18"],
        ["18–24", "18-24"],
        ["25–34", "25-34"],
        ["35–44", "35-44"],
        ["45 и старше", "45plus"],
      ],
    },
    {
      type: "choice",
      key: "goal",
      title: "Зачем ты пришёл в ADAM?",
      options: [
        ["🎯", "Выработать привычку", "habit"],
        ["💪", "Прокачать дисциплину", "discipline"],
        ["🔥", "Не срывать серию", "streak"],
        [UI_SPARK, "Общаться с ИИ-наставником", "ai"],
        ["🚀", "Просто посмотреть", "explore"],
      ],
    },
    ...ARCHETYPE_QUIZ_QUESTIONS.map((q, i) => ({
      type: "choice",
      key: `archetype_q${i}`,
      title: q.q,
      options: q.options,
    })),
    { type: "archetype_result", title: "Твой архетип" },
    { type: "firststeps", title: "Первые шаги" },
    { type: "referral", title: "Позови друзей — бонус обоим" },
    { type: "paywall", title: "ADAM после пробного периода" },
  ];
  // Окно «ADAM после пробного периода» (цены) — только аккаунтам в сценарии триала (state.access.paywall). Тестировщикам со
  // свободным или временным доступом сообщения об оплате не показываем: список собирается при открытии квиза.
  let START_QUIZ_STEPS = START_QUIZ_STEPS_ALL;
  let startQuizArchetypeKey = null;
  let startQuizArchetypeLabel = null;
  let startQuizArchetypeComputed = false;
  let startQuizStep = 0;
  let startQuizAnswers = {};
  let startQuizShownThisSession = false;

  function renderStartQuizChoiceStep(step, options, continueBtn) {
    const selected = startQuizAnswers[step.key];
    if (options) {
      options.innerHTML = step.options.map((o) => {
        const hasIcon = o.length === 3;
        const icon = hasIcon ? o[0] : "";
        const label = hasIcon ? o[1] : o[0];
        const value = hasIcon ? o[2] : o[1];
        const isSel = selected === value;
        return `<button type="button" class="start-quiz-option ${isSel ? "is-selected" : ""}" data-value="${value}">
          <span class="start-quiz-option__dot"></span>
          <span class="start-quiz-option__label">${escapeHtml(label)}</span>
          ${icon ? `<span class="start-quiz-option__icon">${icon}</span>` : ""}
        </button>`;
      }).join("");
      options.querySelectorAll(".start-quiz-option").forEach((btn) => {
        btn.addEventListener("click", () => {
          startQuizAnswers[step.key] = btn.dataset.value;
          haptic("light");
          renderStartQuizStep();
        });
      });
    }
    if (continueBtn) continueBtn.disabled = !selected;
  }

  // Карточки шагов (аватар-значок + заголовок + текст) — одна разметка на приветствие и «первые шаги».
  function startQuizCardsHtml(items) {
    return `<div class="start-quiz-testimonials">${items.map(([icon, name, body]) => `
      <div class="start-quiz-testimonial">
        <div class="start-quiz-testimonial__avatar">${icon}</div>
        <div class="start-quiz-testimonial__body">
          <div class="start-quiz-testimonial__name">${escapeHtml(name)}</div>
          <div class="start-quiz-testimonial__text">${escapeHtml(body)}</div>
        </div>
      </div>`).join("")}</div>`;
  }

  function startQuizWelcomeTitle() {
    const name = String(state?.user?.first_name || "").trim();
    return name ? `Привет, ${name}!` : "Привет!";
  }

  // Приветствие: что это за приложение и зачем дальше вопросы. Только то, что в ADAM есть на самом деле.
  function renderStartQuizWelcomeStep(options) {
    if (!options) return;
    options.innerHTML = `
      <p class="start-quiz-welcome__lead">Я — Адам. Здесь привычки и задачи на день собраны в одном месте. Несколько коротких вопросов — и я подстрою всё под тебя.</p>
      ${startQuizCardsHtml([
        ["✅", "Привычки и задачи дня", "Отмечаются одним нажатием — всё главное на одном экране."],
        ["🔥", "Серия и статусы", "Чем дольше серия, тем выше статус и ярче рамка."],
        [UI_SPARK, "Я в чате", "Подскажу, как спланировать день и не сорваться."],
      ])}`;
  }

  // Шаг вместо прежних «отзывов» Насти, Игоря и Марины: это были придуманные люди, а продукт ещё никому не показывали.
  // Теперь — что человек сделает дальше: ровно та последовательность, которую потом ведут подсказки в приложении.
  function renderStartQuizFirstStepsStep(options) {
    if (!options) return;
    const hasHabit = (state?.habits || []).length > 0;
    options.innerHTML = startQuizCardsHtml([
      hasHabit
        ? ["🎯", "Первая привычка уже в списке", "Её можно оставить или заменить своей — как удобнее."]
        : ["🎯", "Добавь первую привычку", "Одну, самую простую: выбери готовую или напиши свою."],
      ["✅", "Отметь её сегодня", "Первая отметка запускает серию и приносит первые монеты."],
      [UI_SPARK, "Загляни к Адаму", "Расскажи, чего хочешь добиться, — он подскажет план."],
    ]);
  }

  // Ссылка и механика — те же, что уже реально начисляют XP в
  // handlers/start.py (100 XP пригласившему, 50 приглашённому), просто
  // впервые показаны в самом Mini App. Формат ссылки — как в уже
  // существующем шаринге прогресса (achievementShareSend).
  async function renderStartQuizReferralStep(options) {
    if (!options) return;
    const count = state?.user?.referrals || 0;
    let refLink = state?.bot_username
      ? `https://t.me/${state.bot_username}?start=${state?.user?.telegram_id || ""}`
      : "";
    if (!refLink) {
      try {
        const shareMeta = await api("/api/share-link", { timeoutMs: 10000 });
        if (shareMeta?.url) refLink = shareMeta.url;
      } catch (_) { /* остаёмся без ссылки — ниже есть запасной вариант */ }
    }
    if (!refLink) refLink = window.location.origin || window.location.href;
    // Рендерим, только если пользователь не успел уйти на другой шаг, пока
    // ждали /api/share-link (async).
    if (START_QUIZ_STEPS[startQuizStep]?.type !== "referral") return;
    options.innerHTML = `
      <div class="start-quiz-referral">
        <p class="start-quiz-referral__hint">За каждого друга, который перейдёт по твоей ссылке и начнёт пользоваться ADAM — тебе 100 XP, а ему 50 XP сразу на старте.</p>
        <div class="start-quiz-referral__link-box">
          <span class="start-quiz-referral__link">${escapeHtml(refLink)}</span>
          <button type="button" class="start-quiz-referral__copy" id="startQuizRefCopy">Копировать</button>
        </div>
        <div class="start-quiz-referral__count">Уже приглашено: <b>${count}</b></div>
        <button type="button" class="start-quiz-referral__share" id="startQuizRefShare">↗ Поделиться в Telegram</button>
      </div>`;
    document.getElementById("startQuizRefCopy")?.addEventListener("click", async () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(refLink);
        } else {
          throw new Error("no_clipboard");
        }
        haptic("light");
        showToast("Ссылка скопирована", "success");
      } catch (_) {
        showToast(refLink, "success", 6000);
      }
    });
    document.getElementById("startQuizRefShare")?.addEventListener("click", () => {
      const text = "Строю привычки вместе с ADAM — присоединяйся:";
      const shareUrl = telegramShareUrl(text, refLink);
      if (tg && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(shareUrl);
      } else {
        window.open(shareUrl, "_blank");
      }
      haptic("light");
    });
  }

  // Результат теста архетипа — считаем один раз (не на каждый возврат на
  // этот шаг кнопкой "Назад"/"Далее"), сохраняем на сервере через тот же
  // /api/settings/archetype, что и версия теста в Настройках. Кнопка
  // "Написать Адаму" — просьба пользователя "вовлечение в общение с
  // ИИ-Адамом важно очень": вместо того чтобы просто показать результат,
  // сразу даём один тап до настоящего персонального первого ответа ADAM
  // (не блокируем сам квиз сетевым вызовом — переход на /coach с
  // параметрами, первое сообщение формирует и шлёт уже ai_coach.js).
  async function renderStartQuizArchetypeResultStep(options) {
    if (!options) return;
    if (!startQuizArchetypeComputed) {
      options.innerHTML = `<div class="start-quiz-archetype-loading">Считаю результат…</div>`;
      const tally = {};
      ARCHETYPE_QUIZ_QUESTIONS.forEach((_, i) => {
        const value = startQuizAnswers[`archetype_q${i}`];
        if (value) tally[value] = (tally[value] || 0) + 1;
      });
      const winner = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0] || "explorer";
      let label = ARCHETYPE_LABELS[winner] || winner;
      try {
        const res = await api("/api/settings/archetype", { method: "POST", body: JSON.stringify({ archetype: winner }) });
        if (res?.archetype) label = res.archetype;
        if (state.user) state.user.archetype = label;
        const openBtn = document.getElementById("archetypeQuizBtn");
        if (openBtn) openBtn.textContent = label;
      } catch (_) { /* не критично — локально архетип уже известен, просто не сохранился на сервере */ }
      startQuizArchetypeKey = winner;
      startQuizArchetypeLabel = label;
      startQuizArchetypeComputed = true;
      // Пользователь мог уйти на другой шаг, пока ждали ответ сервера.
      if (START_QUIZ_STEPS[startQuizStep]?.type !== "archetype_result") return;
    }
    const key = startQuizArchetypeKey;
    const label = startQuizArchetypeLabel || ARCHETYPE_LABELS[key] || key;
    const blurb = ARCHETYPE_BLURBS[key] || "";
    options.innerHTML = `
      <div class="start-quiz-archetype-result">
        <div class="start-quiz-archetype-result__label">Твой архетип</div>
        <div class="start-quiz-archetype-result__value">${escapeHtml(label)}</div>
        <p class="start-quiz-archetype-result__blurb">${escapeHtml(blurb)}</p>
        <button type="button" class="start-quiz-archetype-result__chat" id="startQuizArchetypeChat">✦ Написать Адаму</button>
      </div>`;
    document.getElementById("startQuizArchetypeChat")?.addEventListener("click", async (event) => {
      const chatBtn = event.currentTarget;
      if (chatBtn.disabled) return;
      chatBtn.disabled = true;
      haptic("light");
      const overlay = document.getElementById("loadingOverlay");
      if (overlay) overlay.hidden = false;
      const goalKey = startQuizAnswers.goal || "";
      const url = `/coach?intro=archetype&a=${encodeURIComponent(key)}&g=${encodeURIComponent(goalKey)}`;
      // Тест уже пройден (возраст, цель, архетип) — отмечаем это ДО ухода в чат.
      // Раньше «seen» ставился только кнопкой «Начать» на последнем шаге, и после
      // разговора с ADAM приложение открывалось с show_start_quiz=true и гнало
      // человека проходить тест с первого вопроса. Сеть не должна держать
      // пользователя: ждём не дольше пары секунд и уходим в любом случае.
      try {
        await Promise.race([
          api("/api/start-quiz/seen", {
            method: "POST",
            body: JSON.stringify({
              age_range: startQuizAnswers.age_range || null,
              goal: startQuizAnswers.goal || null,
            }),
          }),
          new Promise((resolve) => setTimeout(resolve, 2500)),
        ]);
      } catch (_) { /* уйдём в чат и без этого */ }
      if (state) state.show_start_quiz = false;
      setTimeout(() => { window.location.href = url; }, 60);
    });
  }

  // Честная превью-витрина: перечисляет РЕАЛЬНО существующие механики
  // подписки (db/subscription.py — продолжение доступа после триала +
  // закрытый канал за стрик), без выдуманных фич. Биллинг НЕ подключён —
  // кнопка просто продолжает сценарий (просьба пользователя: сначала UI).
  function renderStartQuizPaywallStep(options) {
    if (!options) return;
    const perks = [
      "Продолжаешь пользоваться ADAM без ограничений после бесплатного 3-дневного пробного периода",
      "Доступ в закрытый канал сообщества ADAM — после 2 дней ударного режима подряд",
    ];
    options.innerHTML = `
      <div class="start-quiz-paywall">
        <div class="start-quiz-paywall__badge">🚀 Скоро в ADAM</div>
        <ul class="start-quiz-paywall__perks">${perks.map(p => `<li>${escapeHtml(p)}</li>`).join("")}</ul>
        <div class="start-quiz-paywall__price">
          <div class="start-quiz-paywall__price-row"><span>Первый месяц</span><b>$1.99</b></div>
          <div class="start-quiz-paywall__price-row"><span>Далее</span><b>$5.99 / мес</b></div>
        </div>
        <p class="start-quiz-paywall__note">Пока ничего не списывается — это просто превью. Оплата появится в приложении позже.</p>
      </div>`;
  }

  function renderStartQuizStep() {
    const step = START_QUIZ_STEPS[startQuizStep];
    if (!step) return;
    const title = document.getElementById("startQuizTitle");
    const options = document.getElementById("startQuizOptions");
    const back = document.getElementById("startQuizBack");
    const fill = document.getElementById("startQuizProgressFill");
    const continueBtn = document.getElementById("startQuizContinue");
    if (title) title.textContent = step.type === "welcome" ? startQuizWelcomeTitle() : step.title;
    trk("quiz_step", step.key || step.type, { i: startQuizStep + 1, of: START_QUIZ_STEPS.length });
    if (back) back.hidden = startQuizStep === 0;
    if (fill) fill.style.width = `${((startQuizStep + 1) / START_QUIZ_STEPS.length) * 100}%`;
    if (continueBtn) {
      continueBtn.textContent = step.type === "welcome" ? "Поехали" : (startQuizStep === START_QUIZ_STEPS.length - 1 ? "Начать" : "Далее");
    }
    if (step.type === "choice") {
      renderStartQuizChoiceStep(step, options, continueBtn);
    } else {
      if (step.type === "archetype_result") renderStartQuizArchetypeResultStep(options);
      else if (step.type === "welcome") renderStartQuizWelcomeStep(options);
      else if (step.type === "firststeps") renderStartQuizFirstStepsStep(options);
      else if (step.type === "referral") renderStartQuizReferralStep(options);
      else if (step.type === "paywall") renderStartQuizPaywallStep(options);
      if (continueBtn) continueBtn.disabled = false;
    }
  }

  function openStartQuiz() {
    START_QUIZ_STEPS = START_QUIZ_STEPS_ALL.filter((step) => step.type !== "paywall" || state?.access?.paywall);
    const overlay = document.getElementById("startQuizOverlay");
    if (!overlay) { maybeShowAppTour(); return; }
    startQuizStep = 0;
    startQuizAnswers = {};
    startQuizArchetypeKey = null;
    startQuizArchetypeLabel = null;
    startQuizArchetypeComputed = false;
    trk("quiz_start");
    renderStartQuizStep();
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("show"));
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
  }

  function closeStartQuiz() {
    const overlay = document.getElementById("startQuizOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 280);
  }

  function maybeShowStartQuiz() {
    if (!state?.show_start_quiz) { maybeShowAppTour(); return; }
    if (startQuizShownThisSession) return;
    startQuizShownThisSession = true;
    openStartQuiz();
  }

  function initStartQuiz() {
    document.getElementById("startQuizBack")?.addEventListener("click", () => {
      if (startQuizStep === 0) return;
      startQuizStep--;
      haptic("light");
      renderStartQuizStep();
    });
    document.getElementById("startQuizContinue")?.addEventListener("click", async () => {
      const step = START_QUIZ_STEPS[startQuizStep];
      if (!step) return;
      if (step.type === "choice" && !startQuizAnswers[step.key]) return;
      haptic("light");
      if (startQuizStep < START_QUIZ_STEPS.length - 1) {
        startQuizStep++;
        renderStartQuizStep();
        return;
      }
      const continueBtn = document.getElementById("startQuizContinue");
      if (continueBtn) continueBtn.disabled = true;
      try {
        await api("/api/start-quiz/seen", {
          method: "POST",
          body: JSON.stringify({
            age_range: startQuizAnswers.age_range || null,
            goal: startQuizAnswers.goal || null,
          }),
        });
      } catch (e) {
        // Не блокируем прохождение онбординга из-за сетевой ошибки —
        // ответы необязательны для остального сценария.
      } finally {
        trk("quiz_done", null, { goal: startQuizAnswers.goal || null });
        if (state) state.show_start_quiz = false;
        closeStartQuiz();
        maybeShowAppTour();
      }
    });
  }

  // ===================== ЗНАКОМСТВО С АДАМОМ =====================
  // Тест после онбординга: Адам узнаёт, что для человека важно, что мешает и когда у него больше сил, и по ответам подбирает метод
  // планирования дня (Помодоро 25 + 5, блоки 45 + 10, глубокая работа 90 + 20, «три главных дела»), предлагает первые привычки и
  // начинает разговор о целях в чате. Вопросы, методы и правила подбора — db/adam_profile.py (приложению их отдаёт /api/adam-profile).
  // Ответы сервер сохраняет, как только показан итог; «Позже» закрывает окно, и само оно больше не откроется (кнопка есть в «Прогрессе»).
  // sim — симуляция из админки (SIM_SCENES): те же экраны, но ничего не сохраняется и привычки не добавляются.
  let adamIntro = null;   // { quiz, answers, index, result, method, sim, retake, then, busy, added }
  let adamIntroHandled = false;   // окно уже показывали в этой сессии: перезагрузка состояния раньше ответа сервера не откроет его снова
  const ADAM_INTRO_THINK_MS = 900;
  const ADAM_INTRO_CTA = {
    month: "Обсудить цель на месяц", year: "Обсудить цель на год", today: "Составить план на сегодня", look: "Написать Адаму",
  };
  const ADAM_INTRO_NEXT_TEXT = {
    month: "Давай обсудим твою цель на ближайший месяц и разобьём её на шаги.",
    year: "Давай поговорим о большой цели на год и о том, с чего её начать.",
    today: "Давай прямо сейчас составим план на сегодня — по твоему методу.",
    look: "Загляни в чат: расскажу, с чего тебе лучше начать.",
  };

  function adamIntroAvatarHtml() {
    const src = state?.hero?.image;
    return src ? `<img src="${escapeHtml(src)}" alt="" decoding="async">` : `<span class="adam-intro-avatar__mark">A</span>`;
  }

  function adamIntroMethodBlock(s) {
    const methods = s.quiz.methods || [];
    const current = methods.find((m) => m.key === s.method) || methods[0];
    if (!current) return "";
    const recommended = s.method === s.result.method;
    const reasons = recommended && (s.result.reasons || []).length
      ? `<ul class="adam-intro-reasons">${s.result.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul>` : "";
    const peak = s.result.peak
      ? `<div class="adam-intro-peak"><span>⚡</span>Главное дело ставлю на ${escapeHtml(s.result.peak.label)} (${escapeHtml(s.result.peak.window)})</div>` : "";
    return `
      <section class="adam-intro-block">
        <div class="adam-intro-label">Метод планирования дня${recommended ? " · подобрал Адам" : ""}</div>
        <div class="adam-intro-method"><span class="adam-intro-method__icon">${current.icon}</span><b>${escapeHtml(current.title)}</b></div>
        <p class="adam-intro-blurb">${escapeHtml(current.blurb)}</p>
        ${reasons}
        <div class="adam-intro-chips" role="radiogroup" aria-label="Метод планирования дня">
          ${methods.map((m) => `<button type="button" class="adam-intro-chip${m.key === s.method ? " is-selected" : ""}" role="radio" aria-checked="${m.key === s.method}" data-ai-method="${escapeHtml(m.key)}"><span>${m.icon}</span>${escapeHtml(m.title)}</button>`).join("")}
        </div>
        ${peak}
      </section>`;
  }

  function adamIntroHabitsBlock(s) {
    const habits = s.result.habits || [];
    if (!habits.length) return "";
    return `
      <section class="adam-intro-block">
        <div class="adam-intro-label">Первые привычки</div>
        <div class="adam-intro-habits">
          ${habits.map((title) => {
            const added = s.added.has(title);
            return `<button type="button" class="adam-intro-habit${added ? " is-added" : ""}" data-ai-habit="${escapeHtml(title)}"${added ? " disabled" : ""}><span class="adam-intro-habit__mark">${added ? "✓" : "＋"}</span>${escapeHtml(title)}</button>`;
          }).join("")}
        </div>
        <p class="adam-intro-note">Нажми, чтобы добавить в список. Позже их можно поменять.</p>
      </section>`;
  }

  function renderAdamIntro() {
    const s = adamIntro;
    if (!s) return;
    const questions = s.quiz.questions;
    const total = questions.length;
    const card = document.getElementById("adamIntroCard");
    const body = document.getElementById("adamIntroBody");
    const title = document.getElementById("adamIntroTitle");
    const content = document.getElementById("adamIntroContent");
    const step = s.index < 0 ? "intro" : s.index < total ? "question" : s.result ? "result" : "think";
    const sameScreen = card.dataset.step === step && card.dataset.index === String(s.index);
    const scrollTop = sameScreen ? body.scrollTop : 0;
    card.dataset.step = step;
    card.dataset.index = String(s.index);

    document.getElementById("adamIntroBack").hidden = step === "intro" || step === "think";
    const later = document.getElementById("adamIntroLater");
    later.hidden = step === "result" || step === "think";
    later.textContent = s.retake ? "Отмена" : "Позже";
    document.getElementById("adamIntroProgress").style.width = `${Math.round(((Math.min(s.index, total) + 1) / (total + 1)) * 100)}%`;
    const next = document.getElementById("adamIntroNext");
    next.hidden = step === "question" || step === "think";
    document.getElementById("adamIntroClose").hidden = step !== "result";

    if (step === "intro") {
      title.textContent = "Давай познакомимся";
      next.textContent = "Начать";
      content.innerHTML = `
        <div class="adam-intro-hero">
          <span class="adam-intro-avatar">${adamIntroAvatarHtml()}</span>
          <div class="adam-intro-bubble">Привет! Я Адам. Несколько коротких вопросов — и я подстрою план дня под тебя. Это займёт минуту.</div>
        </div>
        <ul class="adam-intro-perks">
          <li><span>🗓</span>Метод планирования дня под твой ритм</li>
          <li><span>🌱</span>Первые привычки без долгих раздумий</li>
          <li><span>🎯</span>Разговор о целях: на месяц и на год</li>
        </ul>`;
    } else if (step === "question") {
      const q = questions[s.index];
      title.innerHTML = `<small class="adam-intro-eyebrow">Вопрос ${s.index + 1} из ${total}</small>${escapeHtml(q.title)}`;
      content.innerHTML = `<div class="start-quiz-options">${q.options.map((o) => `
        <button type="button" class="start-quiz-option${s.answers[q.key] === o.value ? " is-selected" : ""}" data-ai-option="${escapeHtml(o.value)}">
          <span class="start-quiz-option__dot"></span>
          <span class="start-quiz-option__label">${escapeHtml(o.label)}</span>
          <span class="start-quiz-option__icon">${o.icon}</span>
        </button>`).join("")}</div>`;
      if (!sameScreen) trk("adam_intro_step", q.key, { i: s.index + 1, of: total });
    } else if (step === "think") {
      title.textContent = "Адам думает…";
      content.innerHTML = `<div class="adam-intro-hero"><span class="adam-intro-avatar">${adamIntroAvatarHtml()}</span><div class="adam-intro-bubble">Подбираю метод и привычки под твои ответы…</div></div>`;
    } else {
      const talk = s.answers.talk || "look";
      title.textContent = "Вот что я для тебя подобрал";
      next.textContent = ADAM_INTRO_CTA[talk] || ADAM_INTRO_CTA.look;
      content.innerHTML = `
        ${adamIntroMethodBlock(s)}
        ${adamIntroHabitsBlock(s)}
        <section class="adam-intro-block">
          <div class="adam-intro-label">Что дальше</div>
          <p class="adam-intro-blurb">Скажи мне «План на день» — составлю его по твоему методу. ${escapeHtml(ADAM_INTRO_NEXT_TEXT[talk] || "")}</p>
        </section>`;
    }
    body.scrollTop = scrollTop;
  }

  async function openAdamIntro({ sim = false, then = null, retake = false, demo = null, jump = false } = {}) {
    const overlay = document.getElementById("adamIntroOverlay");
    if (!overlay || adamIntro) { if (then) then(); return false; }
    let data;
    try {
      data = await api("/api/adam-profile");
    } catch (err) {
      showToast(friendlyError(err), "error");
      if (then) then();
      return false;
    }
    if (adamIntro) return false;   // пока ждали ответ, окно уже открыли
    adamIntro = {
      quiz: data, sim, then, retake, index: -1, result: null, method: null, busy: false, added: new Set(),
      answers: demo ? { ...demo } : (retake ? { ...(data.profile?.answers || {}) } : {}),
    };
    hideProductHint();
    renderAdamIntro();
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    void overlay.offsetWidth;
    overlay.classList.add("show");
    haptic("light");
    if (jump) showAdamIntroResult();
    return true;
  }

  async function showAdamIntroResult() {
    const s = adamIntro;
    if (!s) return;
    s.index = s.quiz.questions.length;
    s.result = null;
    renderAdamIntro();
    const startedAt = performance.now();
    let result;
    try {
      // Настоящее прохождение сохраняет ответы сразу (метод — рекомендованный); симуляция только считает итог.
      const reply = await api(s.sim ? "/api/adam-profile/recommend" : "/api/adam-profile", { method: "POST", body: JSON.stringify({ answers: s.answers }) });
      result = reply.result;
    } catch (err) {
      if (adamIntro !== s) return;
      showToast(friendlyError(err), "error");
      s.index = s.quiz.questions.length - 1;
      renderAdamIntro();
      return;
    }
    const reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const wait = (reduceMotion ? 150 : ADAM_INTRO_THINK_MS) - (performance.now() - startedAt);
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
    if (adamIntro !== s) return;
    s.result = result;
    s.method = result.method;
    haptic("success");
    renderAdamIntro();
  }

  function onAdamIntroAnswer(value) {
    const s = adamIntro;
    const q = s?.quiz.questions[s.index];
    if (!q || s.busy) return;
    s.answers[q.key] = value;
    s.busy = true;
    haptic("light");
    renderAdamIntro();
    setTimeout(() => {
      if (adamIntro !== s) return;
      s.busy = false;
      if (s.index + 1 < s.quiz.questions.length) { s.index += 1; renderAdamIntro(); }
      else showAdamIntroResult();
    }, 220);
  }

  function onAdamIntroBack() {
    const s = adamIntro;
    if (!s || s.busy || s.index < 0) return;
    haptic("light");
    s.result = null;
    s.index = Math.max(-1, s.index - 1);
    renderAdamIntro();
  }

  async function onAdamIntroMethod(key) {
    const s = adamIntro;
    if (!s || !s.result || s.method === key) return;
    s.method = key;
    haptic("light");
    renderAdamIntro();
    if (s.sim) return;
    try {
      await api("/api/adam-profile", { method: "POST", body: JSON.stringify({ answers: s.answers, method: key }) });
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  }

  async function onAdamIntroHabit(btn) {
    const s = adamIntro;
    const title = btn.dataset.aiHabit;
    if (!s || !title || s.added.has(title) || btn.disabled) return;
    btn.disabled = true;
    if (s.sim) {
      s.added.add(title);
      haptic("light");
      showToast("🎬 Демо: привычка не добавляется", "success", 1800);
      renderAdamIntro();
      return;
    }
    try {
      const result = await api("/api/habits", { method: "POST", body: JSON.stringify({ title }) });
      if (result?.habit && state) {
        state.habits = [result.habit, ...(state.habits || [])];
        renderHabits();
      }
      s.added.add(title);
      haptic("light");
      trk("adam_intro_habit");
      if (adamIntro === s) renderAdamIntro();
    } catch (err) {
      btn.disabled = false;
      showToast(friendlyError(err), "error");
    }
  }

  function renderAdamIntroSettings() {
    const btn = document.getElementById("adamIntroBtn");
    if (btn) btn.textContent = state?.adam_intro?.done ? "Пройти заново" : "Пройти";
  }

  // chat — открыть чат с Адамом на выбранную тему; skip — «Позже» (само окно больше не откроется).
  function closeAdamIntro({ chat = false, skip = false } = {}) {
    const s = adamIntro;
    if (!s) return;
    const overlay = document.getElementById("adamIntroOverlay");
    const finished = !!s.result;
    if (!s.sim) {
      adamIntroHandled = true;
      if (skip && !s.retake && !finished) api("/api/adam-profile/skip", { method: "POST" }).catch(() => {});
      if (state) {
        const method = finished ? s.method : (state.adam_intro?.method ?? null);
        const methodTitle = (s.quiz.methods || []).find((m) => m.key === method)?.title ?? null;
        state.adam_intro = { show: false, done: finished || !!state.adam_intro?.done, method, method_title: methodTitle };
      }
      renderAdamIntroSettings();
    }
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { if (!adamIntro) overlay.hidden = true; }, 300);
    adamIntro = null;
    haptic("light");
    const talk = s.answers.talk || "look";
    if (chat) {
      if (s.sim) {
        showToast(`🎬 Демо: откроется чат с Адамом — «${ADAM_INTRO_CTA[talk] || ADAM_INTRO_CTA.look}»`, "success", 2600);
        return;
      }
      trk("adam_intro_chat", talk);
      const loading = document.getElementById("loadingOverlay");
      if (loading) loading.hidden = false;
      setTimeout(() => { window.location.href = `/coach?intro=plans&t=${encodeURIComponent(talk)}`; }, 60);
      return;
    }
    if (s.then) setTimeout(s.then, 380);
  }

  function onAdamIntroNext() {
    const s = adamIntro;
    if (!s) return;
    if (s.index < 0) { haptic("light"); s.index = 0; renderAdamIntro(); return; }
    if (s.result) closeAdamIntro({ chat: true });
  }

  // chain — вызов из цепочки онбординга (она сама решает, когда пора); then — что делать после закрытия окна. true — окно открыто.
  function maybeShowAdamIntro({ chain = false, then = null } = {}) {
    if (!state?.adam_intro?.show || adamIntro || adamIntroHandled) return false;
    if (!chain && (introChainPending() || streakOnboardingDue() || celebrationOverlayOpen())) return false;
    trk("adam_intro_shown");
    openAdamIntro({ then });
    return true;
  }

  function initAdamIntro() {
    document.getElementById("adamIntroOverlay")?.addEventListener("click", (e) => {
      if (!adamIntro) return;
      const option = e.target.closest("[data-ai-option]");
      if (option) { onAdamIntroAnswer(option.dataset.aiOption); return; }
      const method = e.target.closest("[data-ai-method]");
      if (method) { onAdamIntroMethod(method.dataset.aiMethod); return; }
      const habit = e.target.closest("[data-ai-habit]");
      if (habit) { onAdamIntroHabit(habit); return; }
      if (e.target.closest("#adamIntroBack")) { onAdamIntroBack(); return; }
      if (e.target.closest("#adamIntroLater")) { closeAdamIntro({ skip: true }); return; }
      if (e.target.closest("#adamIntroClose")) { closeAdamIntro(); return; }
      if (e.target.closest("#adamIntroNext")) onAdamIntroNext();
    });
    document.getElementById("adamIntroBtn")?.addEventListener("click", () => { haptic("light"); openAdamIntro({ retake: true }); });
  }

  // ===================== ОБУЧЕНИЕ ПРИ ПЕРВОМ ВХОДЕ =====================
  // Короткая вводная модалка — 3 шага вместо прежних 6, текста в разы
  // меньше: одна главная мысль на шаг, без перечисления всех фич разом.
  // Дальше (после закрытия/Skip) человек попадает в само приложение, где
  // его встречает интерактивный онбординг (PRODUCT_ONBOARDING_STEPS ниже) —
  // эта модалка только называет, что такое ADAM, не объясняет интерфейс.
  const APP_TOUR_STEPS = [
    {
      icon: "👋",
      title: "Я — ADAM",
      text: "Помогаю с привычками, задачами и планированием дня.",
    },
    {
      icon: "🎯",
      title: "Формируй привычки",
      text: "Постепенно меняй своё поведение — шаг за шагом.",
    },
    {
      icon: "🚀",
      title: "Погнали",
      text: "Дальше покажу прямо в приложении, с чего начать.",
    },
  ];
  let appTourStep = 0;

  function renderAppTourStep(animate) {
    const step = APP_TOUR_STEPS[appTourStep];
    if (!step) return;
    const icon = document.getElementById("appTourIcon");
    const body = document.getElementById("appTourBody");
    const title = document.getElementById("appTourTitle");
    const text = document.getElementById("appTourText");
    const dots = document.getElementById("appTourDots");
    const back = document.getElementById("appTourBack");
    const next = document.getElementById("appTourNext");
    if (icon) icon.innerHTML = step.icon;
    if (title) title.textContent = step.title;
    if (text) text.textContent = step.text;
    if (dots) {
      dots.innerHTML = APP_TOUR_STEPS.map((_, i) =>
        `<span class="app-tour-dot ${i === appTourStep ? "is-active" : ""}"></span>`
      ).join("");
    }
    if (back) back.hidden = appTourStep === 0;
    if (next) next.textContent = appTourStep === APP_TOUR_STEPS.length - 1 ? "Начать" : "Далее";
    if (animate && icon && body) {
      [icon, body].forEach(el => {
        el.classList.remove("tour-anim");
        void el.offsetWidth;
        el.classList.add("tour-anim");
      });
    }
  }

  function openAppTour() {
    const overlay = document.getElementById("appTourOverlay");
    if (!overlay) return;
    appTourStep = 0;
    trk("tour_start");
    renderAppTourStep(false);
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("show"));
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
  }

  function closeAppTour() {
    const overlay = document.getElementById("appTourOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 280);
    // Закрытие этой модалки (хоть через "Начать", хоть через "Пропустить")
    // НЕ должно само по себе гасить app_tour_seen — иначе интерактивный
    // онбординг (подсветка кнопки "Добавить привычку" и т.д.) не успел бы
    // ни разу показаться. Она только называет, что такое ADAM — сам
    // /api/tour/seen вызывается позже, когда завершится или будет
    // пропущен уже интерактивный сценарий (см. skipProductOnboarding).
    proceedPastWelcome();
  }

  let appTourShownThisSession = false;
  let handleIntroShownThisSession = false;

  // Экран "вот твой @ник" (Слой A онбординга) — показывается один раз,
  // сразу после app-tour, ДО интерактивных шагов. Ник уже назначен
  // автоматически при регистрации (db/handles.py), это просто делает его
  // заметным с первой секунды и даёт сразу поменять, как в Habitica.
  function maybeShowHandleIntro() {
    if (!state?.show_handle_intro || handleIntroShownThisSession) return;
    handleIntroShownThisSession = true;
    openHandleIntro();
  }

  // Общая точка выхода из "вводной" части онбординга (app-tour, если он
  // был) в сторону интерактивных стартовых шагов — по пути показывает
  // экран ника, если он ещё не был показан.
  function proceedPastWelcome() {
    if (state?.show_handle_intro && !handleIntroShownThisSession) {
      handleIntroShownThisSession = true;
      openHandleIntro();
    } else {
      scheduleProductOnboarding();
    }
  }

  function openHandleIntro() {
    const overlay = document.getElementById("handleIntroOverlay");
    if (!overlay) { scheduleProductOnboarding(); return; }
    const input = document.getElementById("handleIntroInput");
    if (input) input.value = (state.user && state.user.handle) || "";
    const err = document.getElementById("handleIntroError");
    if (err) { err.hidden = true; err.textContent = ""; }
    trk("handle_intro_shown");
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("show"));
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
  }

  function closeHandleIntro() {
    const overlay = document.getElementById("handleIntroOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 280);
  }

  function initHandleIntro() {
    const continueBtn = document.getElementById("handleIntroContinue");
    if (!continueBtn) return;
    continueBtn.addEventListener("click", async () => {
      trk("handle_intro_done");
      const input = document.getElementById("handleIntroInput");
      const err = document.getElementById("handleIntroError");
      const value = (input?.value || "").trim().replace(/^@/, "");
      const original = (state.user && state.user.handle) || "";
      continueBtn.disabled = true;
      try {
        if (value && value !== original) {
          const res = await api("/api/settings/handle", {
            method: "POST",
            body: JSON.stringify({ handle: value }),
          });
          if (state.user) state.user.handle = res.handle;
          renderPlayerCard();
        }
        await api("/api/handle-intro/seen", { method: "POST" }).catch(() => {});
        if (state) state.show_handle_intro = false;
        haptic("light");
        closeHandleIntro();
        scheduleProductOnboarding();
      } catch (e) {
        if (err) {
          err.textContent = friendlyError(e);
          err.hidden = false;
        }
      } finally {
        continueBtn.disabled = false;
      }
    });
  }

  // Базовая версия онбординга "в духе Habitica" — два независимых слоя:
  //
  // 1) "Стартовые" шаги (1-4) — подсвечивают КНОПКУ/раздел действия, ведут
  //    человека создать первую привычку (1), коротко показывают список
  //    привычек (2), заполнить главное дело дня (3) и коротко показывают,
  //    что можно добавить второстепенные задачи (4). Дальше сценарий НЕ
  //    превращается в экскурсию по всему приложению — человек сам всё
  //    выполняет и получает обычную награду за это (celebrateHabitCompletion/
  //    тост за главную задачу, см. ниже), и уже в этот момент предлагается
  //    добавить ещё одну привычку или продолжить самому (см. maybeOfferAnotherHabit).
  //    Далее/Назад внутри подсказки (см. showProductHint/goToOnboardingStage)
  //    позволяют пролистать эти шаги и вручную, не только по мере действий.
  // 2) "Контекстные" шаги (5-7) — календарь/рейтинг/профиль объясняются
  //    ТОЛЬКО когда человек сам туда впервые заходит, не раньше. Они
  //    завязаны на persisted onboarding_stage (а не на show_app_tour),
  //    поэтому продолжают работать даже после того, как стартовые шаги
  //    уже закончились/были пропущены — иначе пропуск стартового
  //    сценария заодно навсегда скрыл бы и эти разовые контекстные
  //    подсказки, а они как раз про "объяснять по мере использования".
  let productOnboardingTimers = [];
  let productHintTarget = null;
  let activeHintStage = null;
  let onboardingHabitDoneOnce = false;
  let onboardingMainGoalDoneOnce = false;
  let addAnotherHabitPromptShown = false;
  // Просьба пользователя: "Показать подсказки заново" должно всегда
  // начинать именно с шага 1 — без этого флага scheduleProductOnboarding
  // ниже ориентировался на hasHabits/hasMainGoal и у пользователя, который
  // уже прошёл часть сценария, тур при повторном показе стартовал не с
  // первого шага, а с того, до которого тот уже дошёл по факту.
  let onboardingReplayPending = false;

  // 10 шагов (было 9 — просьба пользователя закончить тур отдельным,
  // более подробным шагом про чат с ADAM: акцент на AI-наставнике и на
  // том, чтобы человек реально начал диалог, а не просто узнал, где
  // находится кнопка).
  const PRODUCT_ONBOARDING_STEPS = {
    1: { target: '#addHabitTrigger', title: 'Начни с одной привычки', text: 'Нажми сюда, чтобы добавить первую.', icon: '🎯' },
    2: { target: '.habits-panel', title: 'Твои привычки', text: 'Нажимай на привычку, чтобы отметить её выполненной сегодня — так запускается серия.', icon: '✅' },
    // target — один селектор или список запасных: берётся первый видимый. Главное
    // дело могло быть уже заполнено (редактор скрыт, виден #mainGoalView), а кнопка
    // «Добавить задачу» — заменена раскрытой формой; без запасных целей шаг молча
    // не открывался, и «Далее»/«Назад» через него не проходили.
    3: { target: ['#mainGoalEditor', '#mainGoalView', '.plan-main'], title: 'Главное дело на сегодня', text: 'Одна задача, которую точно сделаешь.', icon: '✨' },
    4: { target: ['#addPlanTaskTrigger', '#addPlanTaskCollapse'], title: 'Второстепенные задачи', text: 'Кроме главного дела можно добавить ещё несколько небольших задач, чтобы ничего не забыть.', icon: '📝' },
    // Баг, найденный попутно: '[data-tab="calendar"]' сам по себе матчит
    // ПЕРВЫЙ элемент с таким атрибутом в DOM — а это <section class=
    // "tab-panel" data-tab="calendar">, а не кнопка вкладки (та же атрибут-
    // связка есть и у .tab-bar__item ниже по разметке). Из-за этого
    // прожектор шагов 5-7 обводил всю страницу раздела целиком вместо
    // маленькой иконки внизу — .tab-bar__item делает селектор однозначным.
    5: { target: '.tab-bar__item[data-tab="calendar"]', title: 'Календарь', text: 'Здесь виден твой прогресс по дням.', icon: '📅' },
    6: { target: '.tab-bar__item[data-tab="rating"]', title: 'Рейтинг', text: 'Здесь видно твоё место среди других.', icon: '🏆' },
    7: { target: '.tab-bar__item[data-tab="profile"]', title: 'Профиль', text: 'Аватар и серия — здесь.', icon: '👤' },
    8: { target: '#openShopBtn', title: 'ADAM Store', text: 'Алмазы и улучшения для ADAM — здесь же можно вознаградить себя.', icon: '🛍️' },
    9: { target: '#openStatsBtn', title: 'Прогресс и достижения', text: 'Цели, статистика, награды и мой разбор твоего прогресса — всё в одном месте.', icon: '📊' },
    10: { target: '#openSettingsBtn', title: 'Настройки', text: 'Напоминания, приватность, данные и всё остальное — одной кнопкой.', icon: '⚙️' },
    11: { target: '#aiCoachBtn', title: 'Я — твой личный наставник', text: 'Планирую день, разбираю проблемы, слежу за прогрессом и просто поддержу разговор. Напиши хотя бы пару сообщений — и сразу увидишь, чем я полезен.', icon: '✦' },
  };
  // Вкладка, на которой живёт цель каждого шага — нужно, чтобы кнопки
  // "Назад"/"Далее" внутри подсказки (просьба пользователя: "переходить
  // между шагами") сами переключали вкладку, а не просто молча не находили
  // цель на неактивной вкладке. У шага 10 цель (#aiCoachBtn) — часть
  // постоянной нижней навигации, как и у шагов 5-7, поэтому вкладка не
  // важна — оставляем ту же, что и у шага 9, чтобы не дёргать её лишний раз.
  const ONBOARDING_STAGE_TAB = { 1: 'home', 2: 'home', 3: 'home', 4: 'home', 5: 'calendar', 6: 'rating', 7: 'profile', 8: 'profile', 9: 'profile', 10: 'profile', 11: 'profile' };
  const CONTEXT_TAB_STAGE = { calendar: 5, rating: 6, profile: 7 };

  function resolveStepTarget(step) {
    const selectors = Array.isArray(step.target) ? step.target : [step.target];
    for (const selector of selectors) {
      const el = document.querySelector(selector);
      if (el && el.offsetParent !== null) return el;
    }
    return null;
  }

  function clearProductOnboardingTarget() {
    if (productHintTarget) productHintTarget.classList.remove('product-onboarding-target');
    productHintTarget = null;
    adamNudgeActive = false;
  }

  // ---------- Прожектор вокруг цели (#onboardingSpotlight) ----------
  // 4 полосы вокруг getBoundingClientRect() цели + светящаяся рамка —
  // просьба пользователя: акцент именно на объекте, весь остальной экран
  // размыт/затемнён, пока подсказка не закрыта или не выполнена.
  function setSpotlightRect(el, top, left, width, height) {
    if (!el) return;
    el.style.top = `${top}px`;
    el.style.left = `${left}px`;
    el.style.width = `${Math.max(0, width)}px`;
    el.style.height = `${Math.max(0, height)}px`;
  }

  function positionOnboardingSpotlight(target) {
    const spotlight = document.getElementById('onboardingSpotlight');
    if (!spotlight || !target) return;
    const pad = 10;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const r = target.getBoundingClientRect();
    const top = Math.max(0, r.top - pad);
    const left = Math.max(0, r.left - pad);
    const right = Math.min(vw, r.right + pad);
    const bottom = Math.min(vh, r.bottom + pad);

    const panels = spotlight.querySelectorAll('.onboarding-spotlight__panel');
    const bySide = {};
    panels.forEach(p => { bySide[p.dataset.side] = p; });
    setSpotlightRect(bySide.top, 0, 0, vw, top);
    setSpotlightRect(bySide.bottom, bottom, 0, vw, vh - bottom);
    setSpotlightRect(bySide.left, top, 0, left, bottom - top);
    setSpotlightRect(bySide.right, top, right, vw - right, bottom - top);
    // Рамку вокруг цели рисует сам .product-onboarding-target (box-shadow
    // прямо на элементе, см. style.css) — раньше тут ЕЩЁ позиционировалась
    // отдельная #onboardingSpotlightRing поверх, и получалось две рамки
    // сразу, вторая из которых при скролле заметно отставала (лаг JS).
  }

  // Просьба пользователя (видео с реального устройства): пока открыта
  // подсказка онбординга, фон не должен листаться и реагировать на тапы —
  // доступны только кнопки самой подсказки и подсвеченная цель. Блокируем
  // через position:fixed на body (надёжно гасит и touch-скролл, не только
  // колесо/трекпад) — тот же приём, что и у модалок с фиксированным фоном.
  // scrollLockActive защищает от повторного вызова при переключении между
  // шагами подсказки (goToOnboardingStage вызывает showProductHint снова,
  // БЕЗ промежуточного hideProductHint) — иначе второй лок прочитал бы
  // window.scrollY уже как 0 (страница и так заблокирована) и страницу
  // визуально дёрнуло бы в самый верх.
  let scrollLockY = 0;
  let scrollLockActive = false;
  function lockBackgroundScroll() {
    if (scrollLockActive) return;
    scrollLockActive = true;
    scrollLockY = window.scrollY || window.pageYOffset || 0;
    document.body.classList.add('onboarding-scroll-lock');
    document.body.style.top = `-${scrollLockY}px`;
  }
  function unlockBackgroundScroll() {
    if (!scrollLockActive) return;
    scrollLockActive = false;
    document.body.classList.remove('onboarding-scroll-lock');
    document.body.style.top = '';
    // У html стоит scroll-behavior:smooth — обычный scrollTo(0, Y) ЕХАЛ бы на Y
    // плавно, а из-за position:fixed страница на момент снятия блокировки
    // стоит в самом верху. На видео с телефона при каждом «Далее»/«Назад»
    // страница на глазах улетала наверх и ехала обратно к цели. На время
    // возврата отключаем smooth инлайн-стилем (так же сделано в ai_coach.js).
    const root = document.documentElement;
    const prevBehavior = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    window.scrollTo(0, scrollLockY);
    root.style.scrollBehavior = prevBehavior;
  }

  function showOnboardingSpotlight(target) {
    const spotlight = document.getElementById('onboardingSpotlight');
    if (!spotlight || !target) return;
    positionOnboardingSpotlight(target);
    spotlight.hidden = false;
    requestAnimationFrame(() => spotlight.classList.add('show'));
    lockBackgroundScroll();
  }

  function hideOnboardingSpotlight() {
    const spotlight = document.getElementById('onboardingSpotlight');
    unlockBackgroundScroll();
    if (!spotlight) return;
    spotlight.classList.remove('show');
    setTimeout(() => {
      if (!spotlight.classList.contains('show')) spotlight.hidden = true;
      releaseStaleScrollLock();
    }, 340);
  }

  // Подсказка закрыта, а body так и остался position:fixed (отложенный показ прожектора мог отработать после закрытия):
  // страница «залипает», а каждое такое переключение — полный пересчёт раскладки и отрисовки. Снимаем, если ни подсказки,
  // ни прожектора на экране нет.
  function releaseStaleScrollLock() {
    const hint = document.getElementById('productOnboardingHint');
    const spot = document.getElementById('onboardingSpotlight');
    if ((hint && !hint.hidden) || (spot && !spot.hidden)) return;
    unlockBackgroundScroll();
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) releaseStaleScrollLock(); });

  // Пока подсказка открыта, цель может уехать (скролл, поворот экрана) —
  // держим прожектор точно на ней. rAF-throttled, почти бесплатно, когда
  // подсказки нет (ранний return). capture:true на 'scroll' — этот эвент
  // не всплывает сам, но так долетает и со вложенных скролл-контейнеров,
  // не только с window.
  let spotlightReflowPending = false;
  function scheduleSpotlightReflow() {
    if (spotlightReflowPending || !productHintTarget) return;
    const spotlight = document.getElementById('onboardingSpotlight');
    if (!spotlight || spotlight.hidden) return;
    spotlightReflowPending = true;
    requestAnimationFrame(() => {
      spotlightReflowPending = false;
      if (!productHintTarget) return;
      if (productHintTarget.offsetParent === null) {
        // Ушли с вкладки/экрана, где была цель — держать прожектор и
        // подсказку смысла нет, они больше ни к чему не указывают.
        hideProductHint();
        return;
      }
      positionOnboardingSpotlight(productHintTarget);
    });
  }
  window.addEventListener('scroll', scheduleSpotlightReflow, { passive: true, capture: true });
  window.addEventListener('resize', scheduleSpotlightReflow);

  // ===================== КЛАВИАТУРА =====================
  // На телефоне Telegram при открытой клавиатуре уменьшает окно, и фиксированная
  // нижняя панель (.tab-bar) всплывает НАД клавиатурой — прямо поверх поля, в
  // которое человек вводит текст (скриншот с телефона: «Главная задача дня» на
  // шаге 3 подсказок наполовину под панелью, набранного не видно). Пока в
  // текстовом поле стоит курсор, панель прячем (класс html.kb-open), а поле при
  // необходимости подкручиваем в видимую часть окна.
  const TEXT_FIELD_SELECTOR = 'input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]):not([type=button]):not([type=submit]):not([type=hidden]), textarea';
  let keyboardOpen = false;
  let keyboardCloseTimer = null;
  let keyboardSawShrink = false;

  function visibleViewportHeight() {
    return Math.round(window.visualViewport?.height || window.innerHeight);
  }
  let keyboardBaseHeight = visibleViewportHeight();

  function isTextField(el) {
    return !!el && el.nodeType === 1 && typeof el.matches === 'function' && el.matches(TEXT_FIELD_SELECTOR);
  }

  function blurActiveTextField() {
    if (isTextField(document.activeElement)) document.activeElement.blur();
    setKeyboardOpen(false);
  }

  // Поле ввода должно быть видно НАД клавиатурой. В обычном состоянии браузер
  // подкручивает его сам, но при открытой подсказке онбординга страница
  // заблокирована (position:fixed на body) — тогда сдвигаем её вручную, меняя
  // top у body, а не снимаем блокировку (лишний перерасчёт раскладки).
  function revealFocusedField() {
    const field = document.activeElement;
    if (!keyboardOpen || !isTextField(field)) return;
    if (field.closest('.feedback-sheet')) return;   // нижняя шторка и так стоит над клавиатурой: страницу под ней двигать незачем, а повторные прокрутки сбивают набор
    const vh = visibleViewportHeight();
    const r = field.getBoundingClientRect();
    if (r.top >= 12 && r.bottom <= vh - 16) return; // уже на виду
    const delta = r.top + r.height / 2 - vh * 0.35;
    if (scrollLockActive) {
      const maxY = Math.max(0, document.body.scrollHeight - vh);
      scrollLockY = Math.max(0, Math.min(maxY, scrollLockY + delta));
      document.body.style.top = `-${scrollLockY}px`;
      scheduleSpotlightReflow();
    } else {
      field.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  // Клавиатура закрылась: подсказка (если открыта) встаёт рядом с целью заново —
  // пока клавиатура была открыта, страница могла сдвинуться.
  function refreshHintLayout() {
    const el = document.getElementById('productOnboardingHint');
    if (!productHintTarget || !el || el.hidden || productHintTarget.offsetParent === null) return;
    positionOnboardingSpotlight(productHintTarget);
    positionHintCardNear(productHintTarget, el);
    el.hidden = false;
  }

  // Пока в поле стоит курсор, бесконечные декоративные анимации (орбиты серии, пламя, свечение чипа) стоят: они перерисовывают страницу
  // под полем каждый кадр, и на слабых телефонах набор подтормаживает — буквы запаздывают, пробелы и слова «прыгают» (23.10, п.5 владельца).
  // Крутилки загрузки не трогаем: они сообщают, что приложение занято.
  const pausedDecorAnimations = new Set();
  function setDecorAnimationsPaused(paused) {
    try {
      if (paused) {
        for (const anim of document.getAnimations()) {
          const timing = anim.effect && anim.effect.getComputedTiming ? anim.effect.getComputedTiming() : null;
          const target = anim.effect && anim.effect.target;
          if (!timing || timing.iterations !== Infinity || anim.playState !== 'running') continue;
          if (target && target.closest && target.closest('.spinner, [class*="spin"], [class*="loading"], [aria-busy="true"]')) continue;
          anim.pause();
          pausedDecorAnimations.add(anim);
        }
      } else {
        pausedDecorAnimations.forEach((anim) => { try { anim.play(); } catch (_) { /* анимацию уже сняли вместе с элементом */ } });
        pausedDecorAnimations.clear();
      }
    } catch (_) { /* getAnimations нет в старых WebView — ничего не паузим */ }
  }

  function setKeyboardOpen(open) {
    if (open === keyboardOpen) return;
    keyboardOpen = open;
    keyboardSawShrink = false;
    document.documentElement.classList.toggle('kb-open', open);
    setDecorAnimationsPaused(open);
    if (!open) refreshHintLayout();
  }

  document.addEventListener('focusin', (event) => {
    if (!isTextField(event.target)) return;
    clearTimeout(keyboardCloseTimer);
    if (!keyboardOpen) keyboardBaseHeight = visibleViewportHeight();
    setKeyboardOpen(true);
    setTimeout(revealFocusedField, 350); // окно успевает уменьшиться
  });
  document.addEventListener('focusout', (event) => {
    if (!isTextField(event.target)) return;
    clearTimeout(keyboardCloseTimer);
    // Фокус может сразу перейти в другое поле — тогда клавиатура не закрывается.
    keyboardCloseTimer = setTimeout(() => {
      if (!isTextField(document.activeElement)) setKeyboardOpen(false);
    }, 160);
  });
  // Клавиатуру можно скрыть кнопкой «назад», не убирая курсор из поля: окно
  // вернулось к прежней высоте — панель тоже возвращаем. Слушаем и window, и
  // visualViewport: на Android меняется окно, на iOS — только визуальная область.
  function onKeyboardViewportChange() {
    const h = visibleViewportHeight();
    if (!keyboardOpen) { keyboardBaseHeight = h; return; }
    if (h < keyboardBaseHeight - 120) {
      keyboardSawShrink = true;
      revealFocusedField();
    } else if (keyboardSawShrink && h >= keyboardBaseHeight - 60) {
      setKeyboardOpen(false);
    }
    scheduleSpotlightReflow();
  }
  window.addEventListener('resize', onKeyboardViewportChange);
  // Размер окна поменялся (Telegram раскрыл Mini App, поворот экрана): карточка
  // подсказки считалась под прежний размер и оставалась на старых координатах —
  // на низком экране уезжала за край.
  let hintResizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(hintResizeTimer);
    hintResizeTimer = setTimeout(() => { if (!keyboardOpen) refreshHintLayout(); }, 150);
  });
  window.visualViewport?.addEventListener('resize', onKeyboardViewportChange);

  // ---------- Эффект "печатается прямо сейчас" ----------
  let typewriterTimer = null;
  function typewriteHintText(el, text) {
    if (!el) return;
    if (typewriterTimer) { clearInterval(typewriterTimer); typewriterTimer = null; }
    el.textContent = '';
    el.classList.add('is-typing');
    const chars = Array.from(text || '');
    let i = 0;
    typewriterTimer = setInterval(() => {
      if (i >= chars.length) {
        clearInterval(typewriterTimer);
        typewriterTimer = null;
        el.classList.remove('is-typing');
        return;
      }
      el.textContent += chars[i];
      i += 1;
    }, 16);
  }

  // Карточка подсказки встаёт рядом с целью (сверху/снизу — где есть
  // место), а не всегда внизу экрана: иначе непонятно, к чему она
  // относится (жалоба пользователя на подсказку про привычки).
  function positionHintCardNear(target, el) {
    const r = target.getBoundingClientRect();
    const vh = window.innerHeight;
    const margin = 14;
    // Над нижней навигацией (как и в CSS по умолчанию: bottom:88px).
    const navClearance = 88;
    // Замер — при ПОЛНОМ тексте шага (showProductHint кладёт его перед вызовом):
    // раньше текст ещё не был «напечатан», карточка замерялась втрое ниже и
    // вставала слишком низко, а при печати росла вниз за край экрана. Высоту
    // дальше фиксируем, чтобы печать не двигала карточку.
    el.style.minHeight = '';
    el.style.visibility = 'hidden';
    el.hidden = false;
    const cardHeight = el.offsetHeight || 140;
    el.hidden = true;
    el.style.visibility = '';
    el.style.minHeight = `${cardHeight}px`;

    // Для цели вне нижней навигации свободное место снизу кончается у самой
    // навигации, а не у края окна (на низких экранах карточка заезжала на неё).
    const spaceBelow = (target.closest('.tab-bar') ? vh : vh - navClearance) - r.bottom;
    const spaceAbove = r.top;
    let top;
    if (spaceBelow >= cardHeight + margin * 2) {
      top = r.bottom + margin;
    } else if (spaceAbove >= cardHeight + margin * 2) {
      top = r.top - cardHeight - margin;
    } else {
      top = vh - cardHeight - navClearance;
    }
    top = Math.max(margin, Math.min(top, vh - cardHeight - margin));
    el.style.top = `${top}px`;
    el.style.bottom = 'auto';
  }

  function hideProductHint() {
    const el = document.getElementById('productOnboardingHint');
    if (!el) return;
    clearProductOnboardingTarget();
    activeHintStage = null;
    el.classList.remove('show');
    el.style.minHeight = '';
    setTimeout(() => { if (!el.classList.contains('show')) el.hidden = true; }, 220);
    const actions = document.getElementById('productOnboardingHintActions');
    if (actions) { actions.hidden = true; actions.innerHTML = ''; }
    hideOnboardingSpotlight();
    if (typewriterTimer) { clearInterval(typewriterTimer); typewriterTimer = null; }
  }

  // Кнопка "Пропустить" сверху подсказки — для тех, кто не хочет читать:
  // в отличие от ✕ (закрывает только текущую подсказку), останавливает
  // именно СТАРТОВЫЙ сценарий (шаги 1-2 + предложение добавить ещё одну
  // привычку) — контекстные подсказки календаря/рейтинга/профиля не
  // затрагивает, у них своя логика показа (см. CONTEXT_TAB_STAGE выше).
  function skipProductOnboarding() {
    hideProductHint();
    finishStartOnboarding();
  }

  function finishStartOnboarding() {
    productOnboardingTimers.forEach(clearTimeout);
    productOnboardingTimers = [];
    if (state) state.show_app_tour = false;
    api('/api/tour/seen', { method: 'POST' }).catch(() => {});
    // Бейдж на кнопке ИИ условлен и на show_app_tour (см. renderPlayerCard) —
    // без этого вызова он появился бы только после следующей перезагрузки.
    renderPlayerCard();
  }

  // Переход на конкретный шаг онбординга вручную (кнопки "Назад"/"Далее" в
  // самой подсказке — просьба пользователя). В отличие от естественного
  // сценария (шаги 1-2 открываются по завершению предыдущего шага, 3-5 —
  // по факту захода на вкладку), здесь нужно САМИМ переключить вкладку,
  // если цель следующего шага живёт не на текущей — иначе showProductHint
  // тихо ничего не сделает (target.offsetParent === null).
  // dir: +1 («Далее») / -1 («Назад») — если цели шага нет на экране, молча
  // остаться на месте нельзя (кнопки «переставали нажиматься»), поэтому шаг
  // пропускается в ту же сторону.
  function goToOnboardingStage(stage, dir = 0) {
    if (!PRODUCT_ONBOARDING_STEPS[stage]) return;
    const tab = ONBOARDING_STAGE_TAB[stage];
    const activeTab = document.querySelector('.tab-bar__item.is-active')?.dataset.tab;
    const show = () => {
      if (!showProductHint(stage) && dir) goToOnboardingStage(stage + dir, dir);
    };
    if (tab && tab !== activeTab) {
      unlockBackgroundScroll();
      document.querySelector(`.tab-bar__item[data-tab="${tab}"]`)?.click();
      setTimeout(show, 200);
    } else {
      show();
    }
  }

  // Ждём, пока страница доедет до цели (smooth-скролл у html), и только потом
  // ставим прожектор и карточку: по фиксированной паузе длинный скролл не
  // успевал, и подсказка вставала по старым координатам.
  function whenScrollSettled(target, done) {
    const startedAt = Date.now();
    let lastTop = null;
    let stable = 0;
    const tick = () => {
      if (productHintTarget !== target) return; // подсказку уже сменили/закрыли
      const top = target.getBoundingClientRect().top;
      stable = lastTop !== null && Math.abs(top - lastTop) < 1 ? stable + 1 : 0;
      lastTop = top;
      const waited = Date.now() - startedAt;
      if ((waited >= 380 && stable >= 2) || waited >= 1500) { done(); return; }
      setTimeout(tick, 60);
    };
    setTimeout(tick, 60);
  }

  function targetWellPlaced(target) {
    const r = target.getBoundingClientRect();
    return r.top >= 70 && r.bottom <= window.innerHeight * 0.6;
  }

  function showProductHint(stage) {
    const step = PRODUCT_ONBOARDING_STEPS[stage];
    const el = document.getElementById('productOnboardingHint');
    if (!step || !el) return false;
    const target = resolveStepTarget(step);
    if (!target) return false;
    // Жалоба пользователя: переход между шагами подсказки выглядел резко —
    // прожектор и карточка СРАЗУ прыгали на новые координаты, потому что
    // showOnboardingSpotlight() ниже только добавляет .show, а при переходе
    // между шагами (goToOnboardingStage зовёт showProductHint снова, БЕЗ
    // промежуточного hideProductHint) .show уже стоял от предыдущего шага —
    // значит позиция менялась мгновенно (см. комментарий в style.css про
    // transition только на opacity) при полной непрозрачности. Если это
    // смена шага, а не первое открытие — гасим текущий кадр здесь, тогда
    // уже существующая 380ms-пауза ниже (ждёт scrollIntoView) попутно даёт
    // время на fade-out, и новый шаг появляется плавным кроссфейдом, а не
    // скачком. Живой скролл (scheduleSpotlightReflow) сюда не попадает —
    // он вызывает positionOnboardingSpotlight() напрямую, не через эту
    // функцию, так что мгновенное слежение за пальцем не трогаем.
    const spotlightForFade = document.getElementById('onboardingSpotlight');
    if (spotlightForFade && spotlightForFade.classList.contains('show')) {
      spotlightForFade.classList.remove('show');
      el.classList.remove('show');
    }
    clearProductOnboardingTarget();
    productHintTarget = target;
    activeHintStage = stage;
    trk("hint_shown", String(stage));
    target.classList.add('product-onboarding-target');
    document.getElementById('productOnboardingHintTitle').textContent = step.title;
    const iconEl = document.getElementById('productOnboardingHintIcon');
    if (iconEl) { iconEl.innerHTML = adamVoiceHtml("lg", step.icon || "💡"); iconEl.classList.add("is-voice"); }
    // Просьба пользователя: возможность переходить между шагами подсказок
    // самому, а не только вперёд по мере выполнения действий.
    const actions = document.getElementById('productOnboardingHintActions');
    if (actions) {
      actions.innerHTML = '';
      const total = Object.keys(PRODUCT_ONBOARDING_STEPS).length;
      if (stage > 1) {
        const back = document.createElement('button');
        back.type = 'button';
        back.className = 'product-onboarding-hint__action';
        back.textContent = '← Назад';
        back.addEventListener('click', () => goToOnboardingStage(stage - 1, -1));
        actions.appendChild(back);
      }
      if (stage < total) {
        const next = document.createElement('button');
        next.type = 'button';
        next.className = 'product-onboarding-hint__action product-onboarding-hint__action--primary';
        next.textContent = 'Далее →';
        next.addEventListener('click', () => goToOnboardingStage(stage + 1, 1));
        actions.appendChild(next);
      } else {
        // Просьба пользователя: на последнем шаге снизу должна быть кнопка
        // "Вперёд" — завершает тур (а не просто повисает без выхода).
        // Последний шаг теперь про чат с ADAM (просьба: акцент на AI и
        // на том, чтобы человек реально начал диалог) — финиш ведёт прямо
        // в /coach, а не просто закрывает подсказку тостом.
        const finish = document.createElement('button');
        finish.type = 'button';
        finish.className = 'product-onboarding-hint__action product-onboarding-hint__action--primary product-onboarding-hint__action--finish';
        finish.textContent = 'Написать ADAM →';
        finish.addEventListener('click', () => {
          hideProductHint();
          finishStartOnboarding();
          haptic('light');
          const overlay = document.getElementById('loadingOverlay');
          if (overlay) overlay.hidden = false;
          setTimeout(() => { window.location.href = '/coach'; }, 60);
        });
        actions.appendChild(finish);
      }
      actions.hidden = actions.children.length === 0;
    }
    // Просьба пользователя: непонятно, сколько шагов онбординга ещё
    // осталось — считаем прямо по PRODUCT_ONBOARDING_STEPS, чтобы не
    // разъезжалось с реальным числом шагов при будущих правках.
    const stepEl = document.getElementById('productOnboardingHintStep');
    if (stepEl) {
      stepEl.textContent = `Шаг ${stage} из ${Object.keys(PRODUCT_ONBOARDING_STEPS).length}`;
      stepEl.hidden = false;
    }

    // Перелистываем/докручиваем к цели — просьба пользователя: раньше
    // подсказка могла говорить про элемент, которого не видно на экране
    // (например, если пользователь наверху страницы). НО: цель шагов 5-7 —
    // кнопка в нижней навигации (position:fixed), она и так всегда видна
    // независимо от скролла — scrollIntoView на fixed-элементе не нужен
    // семантически и на практике дёргал всю страницу далеко вниз без
    // всякого смысла (жалоба пользователя: "в профиле сильно спускает
    // вниз"). Докручиваем только к целям, которые реально могут быть вне
    // экрана — то есть НЕ внутри .tab-bar.
    // Пока подсказка открыта, фон заблокирован (position:fixed на body), а на
    // заблокированной странице scrollIntoView ничего не делает — при переходе
    // между шагами цель следующего шага оставалась за экраном, и её закрывала
    // сама карточка подсказки. Снимаем блокировку на время докрутки;
    // showOnboardingSpotlight поставит её снова уже по новому положению.
    // Если цель и так стоит удачно (под шапкой, в верхней части экрана, под ней
    // хватает места для карточки) — не трогаем ни скролл, ни блокировку: каждое
    // снятие/установка position:fixed на длинной странице — полный перерасчёт
    // раскладки, на телефоне это заметный рывок. Цель в нижней навигации
    // докручивать не надо, но блокировку снимаем: вкладка могла смениться.
    const onTabBar = !!target.closest('.tab-bar');
    // Нижняя панель прячется, пока в текстовом поле курсор (на iOS кнопка не
    // забирает фокус у поля) — а здесь подсказка указывает именно на неё.
    if (onTabBar) blurActiveTextField();
    const wellPlaced = !onTabBar && targetWellPlaced(target);
    if (!wellPlaced) unlockBackgroundScroll();
    if (!onTabBar && !wellPlaced) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
    }
    whenScrollSettled(target, () => {
      // Полный текст кладём ДО замера высоты карточки — см. positionHintCardNear;
      // «печатается» он уже поверх, в зафиксированной высоте.
      const textEl = document.getElementById('productOnboardingHintText');
      if (textEl) {
        if (typewriterTimer) { clearInterval(typewriterTimer); typewriterTimer = null; }
        textEl.classList.remove('is-typing');
        textEl.textContent = step.text;
      }
      showOnboardingSpotlight(target);
      positionHintCardNear(target, el);
      el.hidden = false;
      requestAnimationFrame(() => el.classList.add('show'));
      typewriteHintText(textEl, step.text);
      // Просьба пользователя: у остальных модалок при появлении уже есть
      // лёгкая вибрация, у контекстных подсказок — нет, хотя момент
      // появления прожектора заметнее всего именно на телефоне.
      haptic('light');
    });

    // Баг: reachedStage в обработчике клика по вкладкам (ниже) читает
    // state.product_onboarding.onboarding_stage — раньше это поле
    // обновлял только следующий bootstrap, а не сам показ подсказки,
    // поэтому оно оставалось устаревшим весь сеанс, и подсказка для
    // рейтинга/профиля показывалась заново при каждом повторном переходе
    // на вкладку, даже после закрытия крестиком.
    if (state) {
      state.product_onboarding = state.product_onboarding || {};
      state.product_onboarding.onboarding_stage = Math.max(state.product_onboarding.onboarding_stage || 0, stage);
    }
    api('/api/onboarding/stage', { method: 'POST', body: JSON.stringify({ stage }) }).catch(() => {});
    return true;
  }

  // Шаг 1 (или сразу шаг 3, если привычка уже есть, а главного дела ещё
  // нет — например, страница была перезагружена посередине сценария).
  // Шаги 2 и 4 ("Твои привычки"/"Второстепенные задачи") на resume
  // намеренно пропускаются — это разовые сноски сразу после действия
  // (см. onOnboardingHabitCreated/onOnboardingMainGoalSaved ниже), не то,
  // ради чего стоит прерывать человека при каждой перезагрузке страницы.
  function scheduleProductOnboarding() {
    if (!state?.show_app_tour) { maybeShowStreakOnboarding(); return; }
    api('/api/onboarding/start', { method: 'POST' }).catch(() => {});
    productOnboardingTimers.forEach(clearTimeout);
    productOnboardingTimers = [];
    const startHints = () => {
      if (!state?.show_app_tour) return;
      if (onboardingReplayPending) {
        onboardingReplayPending = false;
        productOnboardingTimers.push(setTimeout(() => showProductHint(1), 700));
        return;
      }
      const hasHabits = (state?.habits || []).length > 0;
      const hasMainGoal = !!state?.daily_plan?.main_goal;
      const reachedStage = state?.product_onboarding?.onboarding_stage || 0;
      if (!hasHabits) {
        productOnboardingTimers.push(setTimeout(() => showProductHint(1), 700));
      } else if (reachedStage < 2) {
        // Первая привычка уже в списке (её подобрал бот при выдаче доступа): сначала показываем, как её отметить, — это главное
        // действие первого дня. Раньше человек сразу попадал на «Шаг 3 из 11» про главное дело, не увидев шагов 1 и 2.
        productOnboardingTimers.push(setTimeout(() => showProductHint(2), 700));
      } else if (!hasMainGoal) {
        productOnboardingTimers.push(setTimeout(() => showProductHint(3), 700));
      }
    };
    // Вводная часть закончилась — сначала окно про ударный режим (если оно ещё не показано), потом знакомство с Адамом, потом подсказки.
    const afterStreak = () => { if (!maybeShowAdamIntro({ chain: true, then: startHints })) startHints(); };
    if (maybeShowStreakOnboarding({ chain: true, then: afterStreak })) return;
    afterStreak();
  }

  // Вызывается из обработчика создания привычки (app.js::addHabitForm) —
  // только для ПЕРВОЙ когда-либо созданной привычки в рамках сценария.
  // Просьба пользователя: рассказать и про сам список привычек, а не
  // только про кнопку добавления — коротко показываем шаг 2, затем (если
  // главное дело ещё не заполнено) ведём к шагу 3.
  function onOnboardingHabitCreated() {
    if (!state?.show_app_tour) return;
    hideProductHint();
    setTimeout(() => showProductHint(2), 700);
  }

  // Вызывается после сохранения главного дела дня, если оно раньше было
  // пустым. Просьба пользователя: коротко упомянуть и второстепенные
  // задачи (шаг 4) — дальше сценарий по-прежнему НЕ превращается в
  // экскурсию по всему приложению, человек сам отмечает привычку/задачу и
  // получает обычную награду (toast за монеты, тосты плана дня) — именно
  // на этом прожитом опыте и держится петля "делаю → вижу прогресс".
  function onOnboardingMainGoalSaved() {
    hideProductHint();
    setTimeout(() => showProductHint(4), 700);
  }

  // После того как в рамках сценария человек хотя бы раз отметил
  // привычку И хотя бы раз закрыл главное дело дня — предлагаем добавить
  // ещё одну привычку (кнопки "Добавить"/"Потом"), не раньше.
  function maybeOfferAnotherHabit() {
    if (addAnotherHabitPromptShown || !state?.show_app_tour) return;
    if (!onboardingHabitDoneOnce || !onboardingMainGoalDoneOnce) return;
    addAnotherHabitPromptShown = true;
    setTimeout(showAddAnotherHabitPrompt, 1600);
  }

  function showAddAnotherHabitPrompt() {
    const el = document.getElementById('productOnboardingHint');
    const actions = document.getElementById('productOnboardingHintActions');
    // Форма добавления привычки могла остаться открытой после создания
    // первой (не сворачивается автоматически) — тогда #addHabitTrigger
    // скрыт. Подсвечиваем саму секцию "Привычки" (она всегда видна),
    // а не конкретно кнопку-триггер.
    const habitTrigger = document.getElementById('addHabitTrigger');
    const triggerVisible = habitTrigger && habitTrigger.offsetParent !== null;
    const target = triggerVisible ? habitTrigger : document.querySelector('.habits-panel');
    if (!el || !actions || !target || target.offsetParent === null) {
      finishStartOnboarding();
      return;
    }
    clearProductOnboardingTarget();
    productHintTarget = target;
    target.classList.add('product-onboarding-target');
    document.getElementById('productOnboardingHintTitle').textContent = 'Ты справляешься 🔥';
    document.getElementById('productOnboardingHintText').textContent = 'Хочешь добавить ещё одну привычку?';
    // Это бонусное предложение, а не один из 5 пронумерованных шагов —
    // счётчик "Шаг N из 5" тут был бы враньём, скрываем его.
    const stepEl = document.getElementById('productOnboardingHintStep');
    if (stepEl) stepEl.hidden = true;
    actions.innerHTML = `
      <button type="button" class="product-onboarding-hint__action product-onboarding-hint__action--primary" id="onboardingAddAnotherYes">Добавить</button>
      <button type="button" class="product-onboarding-hint__action" id="onboardingAddAnotherLater">Потом</button>
    `;
    actions.hidden = false;
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add('show'));
    document.getElementById('onboardingAddAnotherYes')?.addEventListener('click', () => {
      haptic("light");
      hideProductHint();
      finishStartOnboarding();
      const trigger = document.getElementById('addHabitTrigger');
      if (trigger && trigger.offsetParent !== null) {
        trigger.click();
      } else {
        document.getElementById('newHabitInput')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        document.getElementById('newHabitInput')?.focus();
      }
    });
    document.getElementById('onboardingAddAnotherLater')?.addEventListener('click', () => {
      haptic("light");
      hideProductHint();
      finishStartOnboarding();
    });
  }

  // ===================== НАПОМИНАНИЕ «АДАМ ХОЧЕТ СПРОСИТЬ» =====================
  // Если человек сегодня не писал Адаму (или давно не заходил в чат), после его действия
  // (отметил привычку/задачу) либо вечером при входе приходит подсказка в стиле подсказок
  // онбординга: кнопка «ИИ» подсвечивается, на ней загорается красная метка «новое
  // сообщение». Заходит в чат — Адам сам задаёт первый вопрос (ai_coach.js → /api/ai/greet).
  // Условия и «раз в день» считает сервер (db/ai_nudge.py → state.ai_nudge).
  let adamNudgeActive = false;
  let adamNudgeDemoLit = false;
  let adamNudgeTimer = null;
  let adamNudgeDelayTimer = null;
  const ADAM_NUDGE_EVENING_HOUR = 17;
  // После отметки подсказка «у меня к тебе вопрос» ждёт (db/ai_nudge.py::NUDGE_AFTER_MARK_MIN): человек в движении, 30 минут идёт окно x2,
  // и сразу выскакивающая подсказка сбивала того, кто шёл отмечать вторую привычку.
  const ADAM_NUDGE_AFTER_ACTION_MS = 35 * 60 * 1000;

  function renderAiNudgeBadge() {
    const tour = !!state?.show_app_tour;
    const nudge = state?.ai_nudge || {};
    // Метка и подсветка — когда подсказка уже показана, а в чат так и не зашли.
    const lit = !tour && ((!!nudge.pending && !!nudge.hint_shown_today) || adamNudgeDemoLit);
    // Старое правило: пока человек ни разу не писал Адаму — метка есть всегда.
    const neverWrote = !state?.user?.ai_intro_shown && !tour;
    const badge = document.getElementById("aiCoachBadge");
    if (badge) badge.hidden = !(neverWrote || lit);
    document.getElementById("aiCoachBtn")?.classList.toggle("is-adam-nudge", lit);
  }

  function adamNudgeCanShow() {
    const nudge = state?.ai_nudge;
    if (!nudge?.eligible || nudge.hint_shown_today || state?.show_app_tour) return false;
    if (document.hidden || keyboardOpen || isTextField(document.activeElement)) return false;
    // Подарок от администратора важнее: пока он ждёт своей очереди или открыт, напоминание молчит.
    if (state?.admin_gift || celebrationOverlayOpen()) return false;
    const hint = document.getElementById('productOnboardingHint');
    if (hint && !hint.hidden) return false;
    return !document.querySelector(".subpage-overlay.is-open");
  }

  // Ждём тишины (праздничные экраны, шторки, подсказки тура) и показываем.
  function scheduleAdamNudge({ delayMs = 0 } = {}) {
    const nudge = state?.ai_nudge;
    if (!nudge?.eligible || nudge.hint_shown_today || state?.show_app_tour) return;
    if (delayMs > 0) {
      // Новое действие сбрасывает отсчёт: подсказка придёт, когда человек затихнет, а не посреди отметок.
      clearTimeout(adamNudgeDelayTimer);
      adamNudgeDelayTimer = setTimeout(() => { adamNudgeDelayTimer = null; scheduleAdamNudge(); }, delayMs);
      return;
    }
    if (adamNudgeTimer) return;
    const startedAt = Date.now();
    let quietTicks = 0;
    adamNudgeTimer = setInterval(() => {
      if (Date.now() - startedAt > 2 * 60 * 1000) {
        clearInterval(adamNudgeTimer);
        adamNudgeTimer = null;
        return;
      }
      if (!adamNudgeCanShow()) { quietTicks = 0; return; }
      quietTicks += 1;
      if (quietTicks < 3) return;
      clearInterval(adamNudgeTimer);
      adamNudgeTimer = null;
      showAdamNudge();
    }, 800);
  }

  // Вечером (с 17:00 по времени телефона) напоминание приходит и просто при входе —
  // если человек ещё не писал Адаму сегодня.
  function maybeScheduleEveningAdamNudge() {
    if (new Date().getHours() < ADAM_NUDGE_EVENING_HOUR) return;
    // Зашёл вечером сразу после отметки (например, из бота) — сервер говорит, сколько ещё подождать.
    scheduleAdamNudge({ delayMs: Math.max(0, Number(state?.ai_nudge?.wait_min) || 0) * 60 * 1000 });
  }

  function goToAdamChat() {
    haptic('light');
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.hidden = false;
    setTimeout(() => { window.location.href = adamCoachUrl(true); }, 60);
  }

  // Куда ведёт вход в чат. ?nudge=1 — Адам встретит первым вопросом: чат сразу показывает «ADAM печатает»,
  // не дожидаясь ответа сервера (иначе пару секунд казалось, что ничего не происходит). Сервер решает так же,
  // как здесь: сегодня человек не писал, Адам не здоровался и они уже общались (db/ai_nudge.py).
  function adamCoachUrl(fromNudgeHint = false) {
    if (adamNudgeDemoLit) return '/coach?nudge=demo';
    const nudge = state?.ai_nudge;
    return (fromNudgeHint || (nudge?.pending && nudge?.has_chatted)) ? '/coach?nudge=1' : '/coach';
  }

  // demo — «Показать напоминание» в Настройках: показывается всегда и ничего не отмечает
  // на сервере; в чате Адам при этом тоже здоровается (/coach?nudge=demo).
  function showAdamNudge({ demo = false } = {}) {
    const el = document.getElementById('productOnboardingHint');
    const target = document.getElementById('aiCoachBtn');
    const actions = document.getElementById('productOnboardingHintActions');
    if (!el || !actions || !target || target.offsetParent === null) return false;
    hideProductHint();
    clearProductOnboardingTarget();
    productHintTarget = target;
    adamNudgeActive = true;
    target.classList.add('product-onboarding-target');

    if (demo) {
      adamNudgeDemoLit = true;
    } else if (state?.ai_nudge) {
      state.ai_nudge.hint_shown_today = true;
      api('/api/ai/nudge/shown', { method: 'POST' }).catch(() => {});
    }
    renderAiNudgeBadge();

    const name = (state?.user?.first_name || '').trim();
    const text = name
      ? `${name}, у меня к тебе вопрос — загляни в чат 👋`
      : 'У меня к тебе вопрос — загляни в чат 👋';
    document.getElementById('productOnboardingHintTitle').textContent = 'Я написал тебе в чат';
    const iconEl = document.getElementById('productOnboardingHintIcon');
    if (iconEl) { iconEl.innerHTML = adamVoiceHtml("lg", "💬"); iconEl.classList.add("is-voice"); }
    // Не шаг тура — счётчик «Шаг N из 10» тут был бы враньём.
    const stepEl = document.getElementById('productOnboardingHintStep');
    if (stepEl) stepEl.hidden = true;
    actions.innerHTML = `
      <button type="button" class="product-onboarding-hint__action product-onboarding-hint__action--primary" id="adamNudgeOpen">Ответить Адаму</button>
      <button type="button" class="product-onboarding-hint__action" id="adamNudgeLater">Потом</button>
    `;
    actions.hidden = false;
    document.getElementById('adamNudgeOpen')?.addEventListener('click', () => { hideProductHint(); goToAdamChat(); });
    document.getElementById('adamNudgeLater')?.addEventListener('click', () => { haptic('light'); hideProductHint(); });

    const textEl = document.getElementById('productOnboardingHintText');
    if (typewriterTimer) { clearInterval(typewriterTimer); typewriterTimer = null; }
    if (textEl) { textEl.classList.remove('is-typing'); textEl.textContent = text; }
    showOnboardingSpotlight(target);
    positionHintCardNear(target, el);
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add('show'));
    typewriteHintText(textEl, text);
    haptic('light');
    return true;
  }

  // ===================== ЭКСКЛЮЗИВНЫЙ ПОДАРОК ОТ АДМИНИСТРАТОРА =====================
  // Медаль 🏅 выдаёт только админ (db/admin_gifts.py): при входе — окно с анимацией,
  // после закрытия подарок помечается просмотренным. Ждём тишины, как и остальные окна.
  let adminGiftTimer = null;

  function scheduleAdminGift() {
    const gift = state?.admin_gift;
    if (adminGiftTimer || !gift || state?.show_app_tour) return;
    const startedAt = Date.now();
    let quietTicks = 0;
    adminGiftTimer = setInterval(() => {
      if (Date.now() - startedAt > 3 * 60 * 1000) {
        clearInterval(adminGiftTimer);
        adminGiftTimer = null;
        return;
      }
      const hint = document.getElementById('productOnboardingHint');
      if (celebrationOverlayOpen() || document.hidden || state?.show_app_tour || (hint && !hint.hidden)) { quietTicks = 0; return; }
      quietTicks += 1;
      if (quietTicks < 3) return;
      clearInterval(adminGiftTimer);
      adminGiftTimer = null;
      openAdminGift();
    }, 800);
  }

  function openAdminGift() {
    const gift = state?.admin_gift;
    const overlay = document.getElementById("adminGiftOverlay");
    if (!gift || !overlay) return;
    const note = document.getElementById("adminGiftNote");
    if (note) note.textContent = gift.note || "За пользу и вклад в развитие ADAM";
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => overlay.classList.add("show"));
    haptic("success");
    document.getElementById("adminGiftClose")?.addEventListener("click", closeAdminGift, { once: true });
  }

  function closeAdminGift() {
    const overlay = document.getElementById("adminGiftOverlay");
    const gift = state?.admin_gift;
    if (!overlay) return;
    haptic("light");
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 260);
    if (gift) {
      state.admin_gift = null;
      api("/api/admin-gift/seen", { method: "POST", body: JSON.stringify({ id: gift.id }) }).catch(() => {});
    }
  }

  function maybeShowAppTour() {
    if (!state?.show_app_tour) {
      // Пользователи, увидевшие app-tour ещё до появления экрана ника —
      // показываем им ник отдельно, без самого тура.
      maybeShowHandleIntro();
      return;
    }
    if (appTourShownThisSession) return;
    appTourShownThisSession = true;
    // Если вводная модалка уже была показана в предыдущей загрузке этого
    // же (незавершённого) сценария — например, страницу перезагрузили
    // посередине — не показываем её снова, продолжаем сразу с
    // интерактивных шагов.
    if (state?.product_onboarding?.onboarding_started_at) {
      proceedPastWelcome();
    } else {
      openAppTour();
    }
  }

  function initAppTour() {
    document.getElementById("appTourNext")?.addEventListener("click", () => {
      if (appTourStep >= APP_TOUR_STEPS.length - 1) {
        trk("tour_done");
        closeAppTour();
        return;
      }
      appTourStep += 1;
      trk("tour_step", String(appTourStep + 1));
      renderAppTourStep(true);
      haptic("light");
    });
    document.getElementById("appTourBack")?.addEventListener("click", () => {
      if (appTourStep === 0) return;
      appTourStep -= 1;
      renderAppTourStep(true);
      haptic("light");
    });
    document.getElementById("appTourSkip")?.addEventListener("click", () => { haptic("light"); trk("tour_skip", String(appTourStep + 1)); closeAppTour(); });
    // Просьба пользователя: крестик и "Пропустить" делали одно и то же
    // (закрывали подсказку) — оставляем только "Пропустить".
    document.getElementById('productOnboardingHintSkip')?.addEventListener('click', () => {
      haptic("light");
      // Напоминание от Адама — не часть тура: «Пропустить» просто закрывает его.
      if (adamNudgeActive) { hideProductHint(); return; }
      trk("hint_skip", String(activeHintStage));
      skipProductOnboarding();
    });

    // Контекстные подсказки по разделам — по факту первого перехода на
    // вкладку, независимо от того, идёт ли ещё стартовый сценарий (см.
    // комментарий у CONTEXT_TAB_STAGE выше). onboarding_stage персистится
    // на сервере, поэтому "уже показывали" переживает перезагрузку.
    document.getElementById('tabBar')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.tab-bar__item');
      if (!btn) return;
      const stage = CONTEXT_TAB_STAGE[btn.dataset.tab];
      if (!stage) return;
      const reachedStage = state?.product_onboarding?.onboarding_stage || 0;
      if (reachedStage >= stage) return;
      setTimeout(() => showProductHint(stage), 180);
    });
  }

  // «Не дай серии погаснуть» — часть вводной цепочки: квиз → тур → ник → это окно → подсказки в приложении.
  // Раньше оно открывалось сразу при загрузке и ложилось поверх квиза (человек с первой привычкой от бота видел два окна разом),
  // а после создания первой привычки одновременно с ним открывалась подсказка «Твои привычки».
  let afterStreakOnboarding = null;

  function streakOnboardingDue() {
    const data = state?.streak_onboarding;
    return !!(data?.show && data.message);
  }

  // Квиз, тур и экран ника ещё на экране или впереди — окно встаёт в конце цепочки (scheduleProductOnboarding), а не поверх.
  function introChainPending() {
    return !!(state?.show_start_quiz || state?.show_app_tour || state?.show_handle_intro);
  }

  // chain: вызов из самой цепочки (она сама решает, когда пора). then — что сделать после закрытия окна (например, показать подсказки).
  // Возвращает true, если окно открыто (или уже было открыто): тогда продолжение выполнит его закрытие.
  function maybeShowStreakOnboarding({ chain = false, then = null } = {}) {
    if (!streakOnboardingDue()) return false;
    if (!chain && introChainPending()) return false;
    const overlay = document.getElementById("streakOnboardingOverlay");
    const coach = document.getElementById("streakOnboardingCoach");
    if (!overlay) return false;
    if (then) afterStreakOnboarding = then;
    if (!overlay.hidden && overlay.classList.contains("show")) return true;
    hideProductHint();   // подсказка шага 1 осталась бы под окном, пока человек его читает
    if (coach) coach.textContent = `🤖 Адам: ${state.streak_onboarding.message}`;
    overlay.hidden = false;
    overlay.classList.add("show");
    trk("onb_streak_shown");
    return true;
  }

  function closeStreakOnboarding() {
    const overlay = document.getElementById("streakOnboardingOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    setTimeout(() => { overlay.hidden = true; }, 300);
    // Иначе любая перерисовка до ответа сервера открыла бы окно снова.
    if (state?.streak_onboarding) state.streak_onboarding.show = false;
    trk("onb_streak_ok");
    api("/api/streak/onboarding/seen", {method: "POST"}).catch(() => {});
    const next = afterStreakOnboarding;
    afterStreakOnboarding = null;
    if (next) setTimeout(next, 380);
    else setTimeout(() => maybeShowAdamIntro(), 450);
  }

  async function maybeShowWeeklyBonus() {
    // Воскресный бонус приходит из bootstrap через streak state; если он доступен,
    // открываем выбор только один раз за текущую загрузку.
    if (!state?.streak) return;
    const now = new Date();
    if (now.getDay() !== 0) return;
    const overlay = document.getElementById("streakWeeklyOverlay");
    if (!overlay) return;
    try {
      const available = await api("/api/streak/status");
      if (!available?.weekly_bonus_available) return;
    } catch (_) {
      return;
    }
    overlay.hidden = false;
    overlay.classList.add("show");
  }

  function initStreakUI() {
    document.addEventListener("click", (e) => {
      const day = e.target.closest(".streak-day");
      if (!day) return;
      haptic("light");
      showToast(day.getAttribute("title") || "День серии", "success", 1800);
    });
    document.getElementById("streakCelebrationContinue")?.addEventListener("click", () => {
      haptic("light");
      closeStreakCelebration();
      if (pendingBonusIntro) {
        pendingBonusIntro = false;
        setTimeout(openBonusIntro, 380);
      }
    });
    document.getElementById("doubleBonusContinue")?.addEventListener("click", () => { haptic("light"); closeBonusIntro(); });
    document.getElementById("streakOnboardingContinue")?.addEventListener("click", () => { haptic("light"); closeStreakOnboarding(); });
    document.getElementById("shareAchievementBtn")?.addEventListener("click", () => {
      haptic("light");
      const days = Number(state?.streak?.days || 0);
      openAchievementShare({
        title: "Ударный режим",
        big: formatDays(days),
      });
    });
    document.getElementById("achievementShareClose")?.addEventListener("click", () => {
      haptic("light");
      const overlay = document.getElementById("achievementShareOverlay");
      if (!overlay) return;
      overlay.classList.remove("show");
      overlay.setAttribute("aria-hidden", "true");
      setTimeout(() => { overlay.hidden = true; }, 220);
    });
    // Раньше карточка только красиво показывалась и закрывалась — реального
    // шаринга не было. t.me/share/url — официальный способ Telegram открыть
    // выбор чата для пересылки текста, работает без версионных ограничений
    // Bot API (в отличие от shareToStory) и без генерации картинки на сервере.
    document.getElementById("achievementShareSend")?.addEventListener("click", async () => {
      const card = lastShareCard || {};
      let refLink = "";

      // Обычно username уже есть в bootstrap. Если Mini App открылся сразу
      // после рестарта Railway, он мог ещё не быть заполнен в кэше backend.
      // В таком случае один раз получаем его именно в момент шаринга.
      if (state?.bot_username) {
        refLink = `https://t.me/${state.bot_username}?start=${state?.user?.telegram_id || ""}`;
      } else {
        try {
          const shareMeta = await api("/api/share-link", { timeoutMs: 10000 });
          if (shareMeta?.url) refLink = shareMeta.url;
        } catch (_) {
          // Не показываем голый https://t.me: Telegram превращает его
          // в уродливое превью «https://t.me». Остаёмся на публичном URL ADAM.
        }
      }

      if (!refLink) {
        refLink = window.location.origin || window.location.href;
      }

      const text = `${card.title || "Мой прогресс"} в Project ADAM: ${card.big || ""} 🔥\n\nПрисоединяйся — трекер привычек с AI-коучем:`;
      const shareUrl = telegramShareUrl(text, refLink);
      if (tg && typeof tg.openTelegramLink === "function") {
        tg.openTelegramLink(shareUrl);
      } else {
        window.open(shareUrl, "_blank");
      }
      haptic("light");
    });
    const freezeSheet = document.getElementById("freezePurchaseSheet");
    const closeFreezeSheet = () => {
      if (!freezeSheet) return;
      freezeSheet.classList.remove("is-open");
      freezeSheet.setAttribute("aria-hidden", "true");
      setTimeout(() => { freezeSheet.hidden = true; }, 230);
    };
    const openFreezeSheet = () => {
      if (!freezeSheet) return;
      const streakNow = state?.streak;
      if ((streakNow?.freeze_balance || 0) >= 2 || (streakNow?.freeze_purchased_count || 0) >= 2) {
        showToast("У тебя уже максимум 2 заморозки", "error");
        return;
      }
      freezeSheet.hidden = false;
      requestAnimationFrame(() => freezeSheet.classList.add("is-open"));
      freezeSheet.setAttribute("aria-hidden", "false");
      haptic("light");
    };
    document.getElementById("freezeBuyBtn")?.addEventListener("click", openFreezeSheet);
    // Улучшение #50: бесплатное восстановление сорванной серии.
    document.getElementById("freeRestoreBtn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const result = await api("/api/streak/restore-free", { method: "POST" });
        haptic("medium");
        showToast(`🎁 Серия восстановлена: ${result.streak} дн.`, "success");
        await loadBootstrap();
      } catch (err) {
        showToast(friendlyError(err) || "Не получилось восстановить серию", "error");
        btn.disabled = false;
      }
    });
    document.getElementById("freezePurchaseBack")?.addEventListener("click", closeFreezeSheet);
    document.getElementById("freezePurchaseBackdrop")?.addEventListener("click", closeFreezeSheet);
    document.getElementById("freezePurchaseConfirm")?.addEventListener("click", async () => {
      const confirmBtn = document.getElementById("freezePurchaseConfirm");
      if (confirmBtn) confirmBtn.disabled = true;
      try {
        await api("/api/streak/freeze/buy", {method: "POST"});
        haptic("light");
        closeFreezeSheet();
        showToast("❄️ Заморозка куплена", "success");
        await loadBootstrap();
      } catch (e) {
        const map = {
          weekly_limit: "Лимит 2 заморозки на неделю уже достигнут",
          max_balance: "У тебя уже максимум 2 заморозки",
          not_enough_coins: "Нужно 200 Adam Coin",
        };
        showToast(map[e?.data?.error] || friendlyError(e), "error");
      } finally {
        if (confirmBtn) confirmBtn.disabled = false;
      }
    });
    document.querySelectorAll("[data-weekly-reward]").forEach(btn => {
      btn.addEventListener("click", async () => {
        haptic("light");
        try {
          await api("/api/streak/weekly-reward", {
            method: "POST",
            body: JSON.stringify({reward: btn.dataset.weeklyReward})
          });
          const overlay = document.getElementById("streakWeeklyOverlay");
          overlay.classList.remove("show");
          setTimeout(() => overlay.hidden = true, 300);
          await loadBootstrap();
        } catch (e) {
          showToast("Награда пока недоступна", "error");
        }
      });
    });
  }

  async function syncTimezone() {
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      await api("/api/streak/timezone", {
        method: "POST",
        body: JSON.stringify({timezone: tz})
      });
    } catch (_) {}
  }

  function initStreakPopupClick() {
    // Важное окно первого достижения закрывается только кнопкой «Продолжить».
    // Клик по затемнённому фону ничего не делает — случайное закрытие исключено.
  }

  // ===================== TOAST =====================
  let toastTimer = null;
  // Улучшение #71/#66: если toast с действием ("Отменить") подряд перекрывается
  // следующим тостом до истечения таймера, действие теряется молча — раньше
  // showToast просто затирал предыдущий div без разбора. Держим маленькую
  // очередь: пока активный action-тост не истёк или не нажат, следующие обычные
  // тосты ждут своей очереди вместо того, чтобы обрезать чужую кнопку "Отменить".
  let toastQueue = [];
  let toastActive = false;
  const TOAST_PRAISE_QUEUED_MS = 1200;
  let toastShownAt = 0;
  let toastIsPraise = false;      // показывается обычная похвала (монеты) — её можно сократить, если за ней идёт следующая

  // Монеты и счётчики («+10 Adam Coin», «2/3») — не сообщения от Адама: у них аватарки нет (остальные тосты — с ней, см. «ГОЛОС АДАМА»).
  function toastHasVoice(message) {
    return !/^\s*[+\d]/.test(String(message || ""));
  }

  function showToast(message, kind, duration, action) {
    toastQueue.push({ message, kind, duration, action });
    if (!toastActive) { _drainToastQueue(); return; }
    // Пришла следующая похвала, пока ещё висит предыдущая («+10 Adam Coin» за серию быстрых отметок): прежнюю не держим
    // дольше TOAST_PRAISE_QUEUED_MS от её появления, иначе очередь монет тянется по 2.2 с на каждую привычку.
    if (kind === "praise" && !duration && !action && toastIsPraise) {
      clearTimeout(toastTimer);
      toastTimer = setTimeout(_endActiveToast, Math.max(0, toastShownAt + TOAST_PRAISE_QUEUED_MS - Date.now()));
    }
  }

  function _endActiveToast() {
    document.getElementById("toast").classList.remove("is-visible");
    toastActive = false;
    _drainToastQueue();
  }

  function _drainToastQueue() {
    const next = toastQueue.shift();
    if (!next) { toastActive = false; return; }
    toastActive = true;
    const { message, kind, duration, action } = next;
    const el = document.getElementById("toast");
    el.className = "toast is-visible" + (kind ? " is-" + kind : "") + (toastHasVoice(message) ? " has-voice" : "");
    clearTimeout(toastTimer);
    if (action && action.label) {
      el.innerHTML = "";
      const span = document.createElement("span");
      span.textContent = message;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = action.label;
      // .toast сам по себе pointer-events:none (чтобы обычные тосты не
      // перехватывали тапы по контенту под ними). Для тоста с действием
      // возвращаем клики точечно самой кнопке — иначе "Отменить" не нажимается.
      btn.style.cssText = "margin-left:10px;background:none;border:none;color:inherit;font:inherit;font-weight:700;text-decoration:underline;text-underline-offset:2px;cursor:pointer;padding:6px 4px;pointer-events:auto;touch-action:manipulation;";
      btn.addEventListener("click", () => {
        haptic("light");
        clearTimeout(toastTimer);
        el.classList.remove("is-visible");
        toastActive = false;
        try { action.onClick && action.onClick(); } finally { _drainToastQueue(); }
      });
      el.appendChild(span);
      el.appendChild(btn);
      el.style.pointerEvents = "auto";
    } else {
      el.textContent = message;
      el.style.pointerEvents = "none";
    }
    // Монеты за несколько отметок подряд идут одна за другой: если за ними ещё очередь, каждая показывается 1.2 с, а не 2.2 —
    // иначе после четвёртой привычки ждёшь, пока доиграют все уведомления.
    toastShownAt = Date.now();
    toastIsPraise = kind === "praise" && !duration && !(action && action.label);
    const ms = (toastIsPraise && toastQueue.length) ? TOAST_PRAISE_QUEUED_MS : (duration || 2200);
    toastTimer = setTimeout(_endActiveToast, ms);
    // Промт 7.1: короткие микро-победы (похвала за задачу, монеты за
    // привычку) сопровождаются вибрацией и мягким звуком — обычные тосты
    // (сохранено, ошибка и т.п.) молчат, чтобы не звенеть по любому поводу.
    if (kind === "praise") {
      haptic("reward");
      playChime();
    } else if (kind === "error" && !document.hidden) {
      haptic("warning");
    }
  }

  // «⚡ Ещё 30 минут x2 Adam Coin — успей закрыть следующую привычку». Раньше он ставился в очередь после КАЖДОЙ отметки, и
  // человек, закрывший сразу 3–4 привычки, ждал ещё по 3.6 с на каждый такой тост. Теперь отметки подряд откладывают его, а
  // показывается он один раз — когда новых отметок нет и все «+N Adam Coin» уже показаны.
  const BONUS_HINT_TEXT = "⚡️ Ещё 30 минут x2 Adam Coin — успей закрыть следующую привычку";
  const BONUS_HINT_QUIET_MS = 1200;
  const BONUS_HINT_RETRY_MS = 350;
  let bonusHintTimer = null;

  function scheduleBonusHint() {
    clearTimeout(bonusHintTimer);
    bonusHintTimer = setTimeout(flushBonusHint, BONUS_HINT_QUIET_MS);
  }

  function flushBonusHint() {
    if (toastActive || toastQueue.length) {            // монеты ещё показываются — подсказка идёт после них
      bonusHintTimer = setTimeout(flushBonusHint, BONUS_HINT_RETRY_MS);
      return;
    }
    bonusHintTimer = null;
    // «Успей закрыть следующую привычку» — но если все привычки дня уже закрыты, закрывать нечего (тост был бы про воздух)
    const habits = state?.habits;
    if (Array.isArray(habits) && habits.length && habits.every((h) => h.completed)) return;
    showToast(BONUS_HINT_TEXT, "success", 3600);
  }

  // ===================== PROFILE AVATAR / FRAMES =====================
  function avatarMarkup(user, sizeClass = "") {
    const name = user?.first_name || "Игрок";
    const avatarId = String(user?.avatar_id || "default");
    const frame = String(user?.frame_id || "default");
    if (avatarId.startsWith("upload:")) {
      const id = avatarId.split(":")[1];
      return `<img class="avatar-photo ${sizeClass}" src="/media/avatars/${encodeURIComponent(id)}.jpg" alt="Аватар" loading="eager">`;
    }
    if (avatarId === "adam") return `<span class="avatar-fallback ${sizeClass}">A</span>`;
    return `<span class="avatar-fallback ${sizeClass}">${escapeHtml((name[0] || "A").toUpperCase())}</span>`;
  }

  function renderProfileAvatar() {
    const el = document.getElementById("profileAvatar");
    if (!el || !state?.user) return;
    const u = state.user;
    const frame = String(u.frame_id || "default");
    el.className = `streak-profile-avatar frame-${escapeHtml(frame)}`;
    if (String(u.avatar_id || "default").startsWith("upload:")) {
      const id = String(u.avatar_id).split(":")[1];
      el.innerHTML = `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(id)}.jpg?v=${Date.now()}" alt="Аватар">`;
    } else {
      el.textContent = u.first_name ? (u.first_name[0] || "A").toUpperCase() : "A";
    }
  }

  function getAvailableFrames() {
    const frames = [
      { id: "default", title: "Без рамки", type: "default", available: true },
      { id: "neon", title: "Neon", type: "shop", available: !!state?.shop_items?.some(x => x.payload === "neon" && x.owned) },
      { id: "gold", title: "Gold", type: "shop", available: !!state?.shop_items?.some(x => x.payload === "gold" && x.owned) },
      // Roadmap #15 — анимированные рамки.
      { id: "rainbow", title: "Радуга", type: "shop", available: !!state?.shop_items?.some(x => x.payload === "rainbow" && x.owned) },
      { id: "pulse_violet", title: "Пульс", type: "shop", available: !!state?.shop_items?.some(x => x.payload === "pulse_violet" && x.owned) },
      { id: "streak_14", title: "14 дней", type: "achievement", available: !!state?.streak?.rewards?.some(x => Number(x.milestone) === 14) },
      { id: "streak_30", title: "30 дней", type: "achievement", available: !!state?.streak?.rewards?.some(x => Number(x.milestone) === 30) },
      { id: "paid_double_gold", title: "Double Gold", type: "paid", available: state?.user?.frame_id === "paid_double_gold" || !!state?.user?.paid_frame_owned },
    ];
    return frames;
  }

  function renderFramePicker() {
    const picker = document.getElementById("profileFramePicker");
    const hint = document.getElementById("profileFrameHint");
    if (!picker || !state?.user) return;
    const current = String(state.user.frame_id || "default");
    const frames = getAvailableFrames();
    picker.innerHTML = frames.map(f => `
      <button class="frame-choice frame-choice--${f.id} ${current === f.id ? "is-active" : ""} ${f.available ? "" : "is-locked"}" data-frame-id="${f.id}" type="button" ${f.available ? "" : "disabled"}>
        <span class="frame-choice__preview ${f.id === "default" ? "" : "frame-" + f.id}">${avatarMarkup(state.user)}</span>
        <span class="frame-choice__text"><b>${escapeHtml(f.title)}</b><small>${f.available ? (f.type === "achievement" ? "Награда" : f.type === "paid" ? "Куплена" : "Доступна") : (f.type === "achievement" ? `Нужно ${f.id === "streak_14" ? 14 : 30} дней` : "Не куплена")}</small></span>
      </button>`).join("");
    if (hint) hint.textContent = `Активна: ${frames.find(f => f.id === current)?.title || "Без рамки"}`;
  }

  function renderProfileAvatarControls() {
    renderProfileAvatar();
    renderFramePicker();
  }

  // ===================== RENDER: PLAYER CARD =====================
  function renderPlayerCard() {
    const u = state.user || {};
    const levelEl = document.getElementById("levelNumber");
    const badge = levelEl?.closest(".level-ring__badge");
    const ringFill = document.getElementById("levelRingFill");
    const xpBarFill = document.getElementById("xpBarFill");

    renderProfileAvatarControls();
    document.getElementById("playerName").textContent = u.first_name || "Игрок";
    const handleEl = document.getElementById("playerHandle");
    if (handleEl) handleEl.textContent = u.handle ? `@${u.handle}` : "";
    document.getElementById("streakValue").textContent = u.streak || 0;
    document.getElementById("coinValue").textContent = u.xp || 0;
    renderFollowCounters();

    const badgeEl = document.getElementById("playerBadge");
    if (badgeEl) badgeEl.style.display = u.badge ? "inline" : "none";

    const adminBtn = document.getElementById("adminPanelBtn");
    if (adminBtn) {
        adminBtn.hidden = !u.is_admin;
        // Резервируем место под кнопку в строке с именем, иначе длинное
        // имя/приветствие визуально и по кликам перекрывает кнопку.
        adminBtn.closest(".player-card")?.classList.toggle("has-admin-btn", !!u.is_admin);
    }

    // Бейдж непрочитанного на кнопке ИИ (просьба пользователя): только
    // после того, как стартовый тур закрыт/пройден (иначе дублирует
    // прожектор шага 10, который и так указывает на эту же кнопку) И
    // пользователь ни разу не писал ADAM. renderPlayerCard зовётся и из
    // loadBootstrap, и из applyActionPatch — единая точка, актуальна сразу
    // после любого действия, а не только после полной перезагрузки.
    renderAiNudgeBadge();

    const xpIntoLevel = Math.max(0, Math.min(99.999, (u.total_xp ?? u.xp ?? 0) % 100));
    document.getElementById("xpLabel").textContent = `${Math.floor(xpIntoLevel)} / 100 XP`;

    // Force the browser to animate the level number only when its value changes.
    const levelValue = u.level || 1;
    const previousLevel = levelEl?.dataset.level;
    const levelChanged = previousLevel !== undefined && previousLevel !== String(levelValue);
    if (levelEl) {
      levelEl.dataset.level = String(levelValue);
      levelEl.textContent = levelValue;
      if (levelChanged) {
        badge?.classList.remove("is-changing");
        void badge?.offsetWidth;
        badge?.classList.add("is-changing");
        setTimeout(() => badge?.classList.remove("is-changing"), 560);
      }
    }

    if (xpBarFill) {
      if (levelChanged) {
        // Повышение уровня: полоса перескакивает на новое значение сразу. Сверху идёт праздничный экран, а долгая
        // анимация «сброса» из почти полной шкалы в ноль на слабом телефоне только съедала кадры.
        xpBarFill.style.transition = "none";
        xpBarFill.style.width = xpIntoLevel + "%";
        void xpBarFill.offsetWidth;
        xpBarFill.style.transition = "";
      } else {
        requestAnimationFrame(() => {
          xpBarFill.style.width = xpIntoLevel + "%";
        });
      }
    }

    if (ringFill) {
      // Первый рендер не должен анимировать кольцо из пустого состояния.
      // Иначе Telegram WebView успевает показать дугу в промежуточной
      // позиции, из-за чего кажется, что уровень слева "съезжает".
      // Анимация остаётся только для реального изменения XP.
      const offset = RING_CIRCUMFERENCE * (1 - xpIntoLevel / 100);
      const initialized = ringFill.dataset.ringInitialized === "1";
      // Основная дуга + дуги-свечение (.level-ring__glow, см. index.html) —
      // двигаем все вместе, чтобы свечение не отставало от дуги.
      const ringParts = [ringFill, ...document.querySelectorAll(".level-ring__glow")];

      if (!initialized || levelChanged) {
        ringParts.forEach((part) => {
          part.style.transition = "none";
          part.style.strokeDashoffset = String(offset);
        });
        ringFill.dataset.ringInitialized = "1";
        void ringFill.getBoundingClientRect();
        ringParts.forEach((part) => {
          part.style.transition = "";
          part.classList.remove("is-progressing");
        });
      } else {
        ringParts.forEach((part) => part.classList.add("is-progressing"));
        requestAnimationFrame(() => {
          ringParts.forEach((part) => { part.style.strokeDashoffset = String(offset); });
        });
        clearTimeout(ringFill._progressTimer);
        ringFill._progressTimer = setTimeout(
          () => ringParts.forEach((part) => part.classList.remove("is-progressing")),
          760,
        );
      }
    }
  }

  // ===================== RENDER: HABITS =====================
  // Улучшение #66: удаление привычки — не жёсткий confirm(), а optimistic
  // скрытие + 4с окно на "Отменить" в тосте. pendingDeleteHabitIds — чисто
  // UI-состояние (не persisted), поэтому если пользователь перезагрузит
  // страницу до истечения таймера, ничего не потеряется: DELETE ещё не
  // отправлен на сервер, привычка просто снова появится при следующей загрузке.
  const pendingDeleteHabitIds = new Set();
  const pendingDeleteTimers = new Map();

  // Фидбек: раньше кнопки ⏰/📝-или-⏭/✕ были видны всегда рядом и переносились
  // на вторую строку по-разному в зависимости от длины названия привычки —
  // визуально "то сверху, то снизу" от карточки к карточке. Теперь справа по
  // умолчанию одна кнопка ✏️, остальные появляются только для конкретной
  // привычки после тапа по ней (id хранится тут, сбрасывается перезагрузкой
  // страницы — это чисто состояние отображения, не персистентные данные).
  const expandedHabitActionIds = new Set();
  const expandedHabitTextIds = new Set();
  let habitActionsHintId = null;     // у этой привычки под кнопками показана подсказка «дважды нажми — спрячу» (первые несколько раз)
  let habitCollapsedAt = 0;          // когда кнопки спрятали в последний раз: двойной тап не должен тут же сработать ещё и на название
  let armedHabitDelete = null;       // { id, at, timer } — первый тап по ✕ ждёт подтверждения
  const HABIT_DELETE_ARM_MS = 3500;          // сколько красное «Удалить?» ждёт второго тапа
  const HABIT_DELETE_CONFIRM_GAP_MS = 400;   // торопливый двойной тап по ✕ подтверждением не считается
  const HABIT_ACTIONS_HINT_KEY = "adam_habit_actions_hint";
  const HABIT_ACTIONS_HINT_TIMES = 3;
  const HABIT_TAP_IGNORE = '[role="dialog"], .subpage-overlay, .center-modal, [class*="overlay"], [class*="sheet"], [class*="modal"], .toast';
  const HABIT_TIME_SUGGESTION_DISMISS_PREFIX = "adam_habit_time_suggestion_dismissed_";
  function isHabitTimeSuggestionDismissed(habitId) {
    try { return localStorage.getItem(HABIT_TIME_SUGGESTION_DISMISS_PREFIX + habitId) === "1"; }
    catch (_) { return false; }
  }
  function dismissHabitTimeSuggestion(habitId) {
    try { localStorage.setItem(HABIT_TIME_SUGGESTION_DISMISS_PREFIX + habitId, "1"); } catch (_) {}
    renderHabits();
    haptic("light");
  }

  // ---- Кнопки раскрытой привычки (23.10, п.4 владельца) ---------------------------------------------------------------------
  // Раньше ✏️ раскрывала «Изменить / Время / Заметка / ✕», а спрятать их было нечем (только перезапуск приложения), и торопливый тап
  // «закрыть» попадал по ✕. Теперь: на месте ✏️ стоит стрелка «Скрыть»; прячет и двойной тап по привычке, и тап мимо; открытой остаётся
  // одна строка; ✕ удаляет только вторым тапом (красное «Удалить?»), а после удаления ещё есть «Отменить».
  function takeHabitActionsHint() {
    try {
      const shown = Number(localStorage.getItem(HABIT_ACTIONS_HINT_KEY) || 0);
      if (shown >= HABIT_ACTIONS_HINT_TIMES) return false;
      localStorage.setItem(HABIT_ACTIONS_HINT_KEY, String(shown + 1));
      return true;
    } catch (_) { return false; }
  }

  function disarmHabitDelete() {
    if (!armedHabitDelete) return;
    clearTimeout(armedHabitDelete.timer);
    const { id } = armedHabitDelete;
    armedHabitDelete = null;
    const btn = document.querySelector(`#habitList .habit-item[data-id="${id}"] .habit-item__del`);
    if (!btn) return;
    btn.classList.remove("is-armed");
    btn.innerHTML = ICON_X;
    btn.setAttribute("aria-label", "Удалить");
  }

  function armHabitDelete(habitId, btn) {
    disarmHabitDelete();
    btn.classList.add("is-armed");
    btn.textContent = "Удалить?";
    btn.setAttribute("aria-label", "Подтвердить удаление");
    haptic("warning");
    armedHabitDelete = { id: habitId, at: Date.now(), timer: setTimeout(disarmHabitDelete, HABIT_DELETE_ARM_MS) };
    trk("habit_delete_armed", null, null);
  }

  // Что делает тап по ✕: arm — первый (показать «Удалить?»), wait — слишком скоро после первого (это дрожащий двойной тап), confirm — удалить.
  function habitDeleteStep(habitId, now = Date.now()) {
    if (!armedHabitDelete || armedHabitDelete.id !== habitId) return "arm";
    return now - armedHabitDelete.at >= HABIT_DELETE_CONFIRM_GAP_MS ? "confirm" : "wait";
  }

  function openHabitActions(habitId) {
    expandedHabitActionIds.clear();                      // открытой остаётся одна привычка: меньше кнопок, меньше путаницы
    expandedHabitActionIds.add(habitId);
    habitActionsHintId = takeHabitActionsHint() ? habitId : null;
    disarmHabitDelete();
    trk("habit_actions", "open", null);
    renderHabits();
  }

  // ids — какие строки спрятать (по умолчанию все раскрытые), how — чем спрятали: arrow / double / outside (в учёт).
  function collapseHabitActions(ids, how) {
    const open = (ids || [...expandedHabitActionIds]).filter((id) => expandedHabitActionIds.has(id));
    if (!open.length) return false;
    open.forEach((id) => expandedHabitActionIds.delete(id));
    habitActionsHintId = null;
    habitCollapsedAt = Date.now();
    disarmHabitDelete();
    for (const id of open) {
      if (!renderSingleHabit(id)) { renderHabits(); break; }   // подменяем только эту строку — соседние не трогаем
    }
    if (how !== "outside") haptic("light");
    trk("habit_actions", how, null);
    return true;
  }

  // Тап мимо раскрытой привычки. Вызывается ПОСЛЕ обработки самого тапа: нажатие на соседнюю привычку или кнопку не съедается.
  function collapseOnOutsideTap(target) {
    if (!expandedHabitActionIds.size || !target || !target.isConnected) return false;   // не в DOM — тап уже перестроил список
    if (target.closest(HABIT_TAP_IGNORE)) return false;                                   // окна, шторки и тосты — отдельный слой
    const row = target.closest(".habit-item");
    const keep = row ? Number(row.dataset.id) : 0;
    return collapseHabitActions([...expandedHabitActionIds].filter((id) => id !== keep), "outside");
  }

  function deleteHabitWithUndo(habitId) {
    pendingDeleteHabitIds.add(habitId);
    renderHabits();
    haptic("light");
    const timer = setTimeout(async () => {
      pendingDeleteTimers.delete(habitId);
      if (!pendingDeleteHabitIds.has(habitId)) return; // отменили
      try {
        await api(`/api/habits/${habitId}`, { method: "DELETE" });
      } catch (err) {
        showToast(friendlyError(err), "error");
      } finally {
        pendingDeleteHabitIds.delete(habitId);
        await loadBootstrap();
      }
    }, 4000);
    pendingDeleteTimers.set(habitId, timer);
    showToast("Привычка удалена", null, 4000, {
      label: "Отменить",
      onClick: () => {
        const t = pendingDeleteTimers.get(habitId);
        if (t) { clearTimeout(t); pendingDeleteTimers.delete(habitId); }
        pendingDeleteHabitIds.delete(habitId);
        renderHabits();
        haptic("light");
      },
    });
  }

  // ===================== ОТМЕНА ОТМЕТКИ ПРИВЫЧКИ =====================
  // Правка владельца (24.10): нажал на галочку случайно — тапни по зелёной галочке дважды, спрошу «Убрать отметку о выполнении?».
  // Одиночный тап подсказывает, как это сделать. Можно ли убрать, решает сервер (db/habit_undo.py): отметка сегодняшняя, не больше
  // нескольких отмен в день, монеты за неё ещё на балансе. Симуляция: админка → «Окна и анимации» → habit_undo.
  const HABIT_UNDO_DOUBLE_TAP_MS = 450;
  const HABIT_UNDO_HINT_TEXT = "Нажал случайно? Тапни по галочке дважды — я уберу отметку";
  const HABIT_UNDO_SIM = { id: 0, title: "Зарядка по утрам", completed: true };
  let lastDoneCheckTap = { id: null, time: 0 };
  let doneCheckHintTimer = null;
  let habitUndo = null;

  // Тап по зелёной галочке: второй за HABIT_UNDO_DOUBLE_TAP_MS открывает вопрос, одиночный через паузу подсказывает двойной тап.
  function habitDoneCheckTap(habitId, now = Date.now()) {
    clearTimeout(doneCheckHintTimer);
    if (lastDoneCheckTap.id === habitId && now - lastDoneCheckTap.time <= HABIT_UNDO_DOUBLE_TAP_MS) {
      lastDoneCheckTap = { id: null, time: 0 };
      openHabitUndo(habitId);
      return "double";
    }
    lastDoneCheckTap = { id: habitId, time: now };
    haptic("light");
    doneCheckHintTimer = setTimeout(() => {
      lastDoneCheckTap = { id: null, time: 0 };
      showToast(HABIT_UNDO_HINT_TEXT, "success", 3200);
    }, HABIT_UNDO_DOUBLE_TAP_MS + 60);
    return "single";
  }

  function openHabitUndo(habitId, { habit = null, sim = false } = {}) {
    const sheet = document.getElementById("habitUndoSheet");
    if (!sheet || habitUndo) return false;
    const h = habit || (state.habits || []).find((x) => x.id === habitId);
    if (!h || !h.completed) return false;
    habitUndo = { id: h.id, sim, busy: false };
    document.getElementById("habitUndoName").textContent = h.title || "";
    document.getElementById("habitUndoConfirm").disabled = false;
    sheet.classList.toggle("is-sim", !!sim);       // панель симуляции стоит внизу — окно поднимается над ней
    sheet.hidden = false;
    sheet.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => sheet.classList.add("is-open"));
    haptic("warning");
    if (!sim) trk("habit_undo_open", null, null);
    return true;
  }

  function closeHabitUndo() {
    const sheet = document.getElementById("habitUndoSheet");
    if (!sheet || !habitUndo) return;
    habitUndo = null;
    sheet.classList.remove("is-open");
    sheet.setAttribute("aria-hidden", "true");
    setTimeout(() => { if (!habitUndo) sheet.hidden = true; }, 230);
  }

  async function confirmHabitUndo() {
    const s = habitUndo;
    if (!s || s.busy) return;
    if (s.sim) {
      showToast("Так выглядит отмена — в симуляции ничего не меняется", "success", 3200);
      closeHabitUndo();
      return;
    }
    const confirmBtn = document.getElementById("habitUndoConfirm");
    s.busy = true;
    confirmBtn.disabled = true;
    try {
      const result = await api(`/api/habits/${s.id}/uncomplete`, { method: "POST" });
      closeHabitUndo();
      clearTimeout(bonusHintTimer);                  // подсказка «успей закрыть следующую» относилась к отметке, которой больше нет
      bonusHintTimer = null;
      haptic("medium");
      applyActionPatch(result);
      // Окна, которые ждали своей очереди после отметки, относились к ней — отмена их снимает.
      clearInterval(questsWindowTimer);
      questsWindowTimer = null;
      questsPending = null;
      const stillDone = (state.habits || []).some((h) => h.completed);
      if (!stillDone) {
        clearInterval(remindSheetTimer);             // «Напомнить друзьям» — только пока есть своя отметка
        remindSheetTimer = null;
      }
      if (friendsData) {
        friendsData.viewer_done = stillDone;
        renderFriendsCard();
      }
      showToast(result.coins_back ? `Убрал отметку и забрал ${result.coins_back} Adam Coin обратно` : "Убрал отметку", "success", 3400);
    } catch (err) {
      closeHabitUndo();
      trk("habit_undo_denied", (err && err.data && err.data.error) || "error", null);
      showToast(friendlyError(err), "error", 4200);
      await loadBootstrap();                         // сервер знает лучше: перерисуем привычки так, как они есть на самом деле
    }
  }

  function initHabitUndo() {
    if (!document.getElementById("habitUndoSheet")) return;
    document.getElementById("habitUndoBackdrop")?.addEventListener("click", () => { if (habitUndo && !habitUndo.busy) closeHabitUndo(); });
    document.getElementById("habitUndoKeep")?.addEventListener("click", () => {
      if (!habitUndo || habitUndo.busy) return;
      haptic("light");
      if (!habitUndo.sim) trk("habit_undo_cancel", null, null);
      closeHabitUndo();
    });
    document.getElementById("habitUndoConfirm")?.addEventListener("click", confirmHabitUndo);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && habitUndo && !habitUndo.busy) closeHabitUndo(); });
  }

  function renderHabits() {
    if (armedHabitDelete) disarmHabitDelete();     // см. renderSingleHabit
    const list = document.getElementById("habitList");
    const habits = (state.habits || []).filter(h => !pendingDeleteHabitIds.has(h.id));
    const done = habits.filter(h => h.completed).length;
    const progressLabel = document.getElementById("habitsProgressLabel");
    if (progressLabel) {
      // Прогресс сверху — по ВСЕМ привычкам, независимо от активного
      // фильтра категории: фильтр влияет только на то, что показано
      // в списке ниже, а не на то, что реально сделано за день.
      progressLabel.textContent = `${done}/${habits.length}`;
    }

    // Фильтр по категориям — виден, только если у ХОТЯ БЫ одной привычки
    // есть категория. Пустой ряд чипов, когда фильтровать нечего, — тот
    // самый лишний шум, который эта фаза редизайна убирает.
    const filterRow = document.getElementById("habitFilterRow");
    // Roadmap #22 — мягкая подсказка про постоянно проваливаемую привычку.
    // Не навязчиво: одна карточка (первая по числу провалов), с
    // возможностью закрыть на сегодня (sessionStorage, не БД — если
    // ничего не поменялось, подсказка честно вернётся завтра).
    const strugglingBanner = document.getElementById("strugglingHabitBanner");
    if (strugglingBanner) {
      const struggling = Array.isArray(state.struggling_habits) ? state.struggling_habits : [];
      const top = struggling[0];
      let dismissedKey = null;
      try { dismissedKey = top ? sessionStorage.getItem("dismissedStruggle_" + top.habit_id) : null; } catch (_) {}
      if (!top || dismissedKey) {
        strugglingBanner.hidden = true;
      } else {
        strugglingBanner.hidden = false;
        // Тап по тексту подсказки открывает чат с ADAM, и обсуждение решения
        // начинается сразу: ai_coach.js по ?intro=struggle сам отправляет
        // первое сообщение про эту привычку (см. maybeSendIntro там). Кнопка
        // ✕ — отдельная, закрывает подсказку на сегодня и чат не открывает.
        strugglingBanner.innerHTML = `
          <button type="button" class="struggling-habit-banner__open" aria-label="Обсудить с ADAM">
            <span class="struggling-habit-banner__icon">${adamVoiceHtml("sm")}</span>
            <span class="struggling-habit-banner__text">«${escapeHtml(top.title)}» не получается ${top.missed} из последних дней — Может уменьшим нагрузку или обсудим решение 📩 ?</span>
          </button>
          <button type="button" class="struggling-habit-banner__close" aria-label="Закрыть">${ICON_X}</button>
        `;
        strugglingBanner.querySelector(".struggling-habit-banner__open")?.addEventListener("click", () => {
          haptic("light");
          const overlay = document.getElementById("loadingOverlay");
          if (overlay) overlay.hidden = false;
          const title = String(top.title || "").slice(0, 80);
          const url = `/coach?intro=struggle&t=${encodeURIComponent(title)}&m=${encodeURIComponent(String(top.missed))}`;
          setTimeout(() => { window.location.href = url; }, 60);
        });
        strugglingBanner.querySelector(".struggling-habit-banner__close")?.addEventListener("click", () => {
          haptic("light");
          try { sessionStorage.setItem("dismissedStruggle_" + top.habit_id, "1"); } catch (_) {}
          strugglingBanner.hidden = true;
        });
      }
    }

    // Roadmap #7 — список для "После какой привычки предложить" в форме
    // добавления обновляем при каждом рендере, чтобы новая привычка сразу
    // была доступна как возможный триггер для следующей.
    const chainSelect = document.getElementById("newHabitChainTrigger");
    if (chainSelect) {
      const currentValue = chainSelect.value;
      chainSelect.innerHTML = `<option value="">Не связывать</option>` +
        habits.map(h => `<option value="${h.id}">${escapeHtml(h.title)}</option>`).join("");
      if (habits.some(h => String(h.id) === currentValue)) chainSelect.value = currentValue;
    }

    if (filterRow) {
      const usedCategories = [...new Set(habits.map(h => h.category).filter(Boolean))];
      if (usedCategories.length === 0) {
        filterRow.hidden = true;
        activeHabitFilter = "";
      } else {
        filterRow.hidden = false;
        if (activeHabitFilter && !usedCategories.includes(activeHabitFilter)) activeHabitFilter = "";
        const chip = (value, label) =>
          `<button type="button" class="habit-filter-chip ${activeHabitFilter === value ? "is-active" : ""}" data-category="${value}">${label}</button>`;
        filterRow.innerHTML =
          chip("", "Все") +
          usedCategories.map(cat => {
            const meta = HABIT_CATEGORY_META[cat];
            return meta ? chip(cat, `${meta.emoji} ${meta.label}`) : "";
          }).join("");
      }
    }

    if (habits.length === 0) {
      // Готовые привычки-шаблоны решают проблему "чистого листа" —
      // непонятно, с чего начать, при первом открытии.
      list.innerHTML =
        `<li class="empty-hint">Пока нет привычек — добавь первую ниже 👇</li>` +
        `<li class="habit-templates">` +
        HABIT_TEMPLATES.map(t =>
          `<button type="button" class="habit-template-chip" data-template="${escapeHtml(t.title)}">${t.emoji} ${escapeHtml(t.title)}</button>`
        ).join("") +
        `</li>` +
        `<li class="habit-programs-label">Или начни с готовой программы:</li>` +
        `<li class="habit-programs">` +
        HABIT_PROGRAMS.map((p, i) =>
          `<button type="button" class="habit-program-card" data-program="${i}">` +
          `<span class="habit-program-card__emoji">${p.emoji}</span>` +
          `<span class="habit-program-card__title">${escapeHtml(p.title)}</span>` +
          `<span class="habit-program-card__count">${p.habits.length} привычки</span>` +
          `</button>`
        ).join("") +
        `</li>`;
      return;
    }

    const visibleHabits = activeHabitFilter
      ? habits.filter(h => h.category === activeHabitFilter)
      : habits;

    if (visibleHabits.length === 0) {
      list.innerHTML = `<li class="empty-hint">В этой категории пока пусто</li>`;
      return;
    }

    list.innerHTML = visibleHabits.map(habitItemHtml).join("");
  }

  // Вынесено из renderHabits() (05.09, по телеметрии с реальных устройств —
  // см. коммит про applyActionPatch): даже "точечный" патч состояния всё
  // равно гонял ПОЛНУЮ пересборку innerHTML всего списка на каждый тап по
  // ОДНОЙ привычке — на телефонах пользователей это давало серии long_task/
  // long_frame по 100-700мс КАЖДЫЙ, даже на мощных устройствах (Samsung
  // S938B, 8ГБ/8 ядер) — то есть дело не в железе, а в том, что список
  // пересобирается целиком там, где поменялась одна строка. Теперь шаблон
  // одной привычки — отдельная функция: renderHabits() использует её в
  // цикле как раньше, а renderSingleHabit() (см. ниже) точечно подменяет
  // ТОЛЬКО один <li>, не трогая остальные N-1 строк списка.
  function habitItemHtml(h) {
      const catMeta = h.category ? HABIT_CATEGORY_META[h.category] : null;
      const isCounter = (h.target_count || 1) > 1;
      const badges =
        (h.priority === 2 ? `<span class="habit-item__badge" title="Важная привычка">⭐</span>` : "") +
        (catMeta ? `<span class="habit-item__badge" title="${escapeHtml(catMeta.label)}">${catMeta.emoji}</span>` : "");
      const noteBtn = h.completed
        ? `<button class="habit-item__note" data-action="note" aria-label="Заметка/фото">📝</button>`
        : "";
      // Roadmap #23/#36 — подсказка времени, только пока у привычки ещё
      // нет своего planned_time (иначе она и так уже видна как чип ⏰).
      const suggestBtn = h.suggested_time && !isHabitTimeSuggestionDismissed(h.id)
        ? `<span class="habit-item__suggest-wrap">
             <button class="habit-item__suggest-time" data-action="accept-suggested-time" data-time="${h.suggested_time}" title="Я заметил: обычно ты делаешь это в это время">${adamVoiceHtml("xs")}${h.suggested_time}?</button>
             <button type="button" class="habit-item__suggest-dismiss" data-action="dismiss-suggested-time" aria-label="Не предлагать это время" title="Не предлагать">${ICON_X}</button>
           </span>`
        : "";

      const checkLabel = h.completed
        ? ICON_CHECK
        : (isCounter ? `${h.progress_count || 0}/${h.target_count}` : "");
      const checkClass = isCounter && !h.completed ? "habit-item__check habit-item__check--counter" : "habit-item__check";

      const isExpanded = expandedHabitActionIds.has(h.id);
      // Раскрытая строка: стрелка «Скрыть» на месте ✏️ (закрывается той же кнопкой, что открывала), а действия — отдельной строкой под
      // названием, ✕ в её дальнем углу. Подсказка про двойной тап — первые несколько раз (habitActionsHintId).
      const rowActions = isExpanded
        ? `
        <button class="habit-item__more is-open" data-action="toggle-actions" aria-label="Скрыть кнопки" aria-expanded="true">${ICON_CHEVRON_UP}</button>
        <div class="habit-item__bar">
          <button class="habit-item__edit" data-action="edit" aria-label="Изменить привычку">${ICON_EDIT}</button>
          <button class="habit-item__time ${h.planned_time ? "is-set" : ""}" data-action="edit-time" data-time="${h.planned_time || ""}" aria-label="Своё время напоминания">${h.planned_time ? "⏰ " + h.planned_time : "⏰"}</button>
          ${noteBtn}
          <button class="habit-item__del" data-action="delete" aria-label="Удалить">${ICON_X}</button>
        </div>
        ${habitActionsHintId === h.id ? `<p class="habit-item__bar-hint">Дважды нажми на привычку — я спрячу кнопки</p>` : ""}`
        : `<button class="habit-item__more" data-action="toggle-actions" aria-label="Действия с привычкой" aria-expanded="false">✏️</button>`;

      return `
      <li class="habit-item ${h.completed ? "is-done" : ""}" data-id="${h.id}">
        <button class="${checkClass}" data-action="${h.completed ? "undo-hint" : (isCounter ? "progress" : "complete")}"${h.completed ? ' aria-label="Выполнено. Дважды нажми, чтобы убрать отметку"' : ""}>${checkLabel}</button>
        ${badges}<span class="habit-item__title ${isExpanded ? "is-expanded" : ""}" title="${escapeHtml(h.title)}">${escapeHtml(h.title)}</span>
        ${suggestBtn}
        ${rowActions}
      </li>`;
  }

  // Точечная замена ОДНОЙ строки списка привычек, без пересборки списка
  // целиком. Возвращает true, если реально подменила узел в DOM (список
  // сейчас на экране, привычка в нём есть и видна при текущем фильтре) —
  // если нет (например, привычка отфильтрована категорией, или списка нет
  // на экране), вызывающий код сам решает, нужен ли полный renderHabits().
  function renderSingleHabit(habitId) {
    if (armedHabitDelete) disarmHabitDelete();     // перерисовка вернёт ✕ — а «ждущее подтверждения» состояние не должно остаться невидимым
    const list = document.getElementById("habitList");
    const h = (state.habits || []).find(x => x.id === habitId);
    if (!list || !h) return false;
    if (activeHabitFilter && h.category !== activeHabitFilter) return false;
    const li = list.querySelector(`.habit-item[data-id="${habitId}"]`);
    if (!li) return false;
    li.outerHTML = habitItemHtml(h);
    return true;
  }

  // Готовые привычки для новичков — один тап, без набора текста.
  const HABIT_TEMPLATES = [
    { emoji: "💧", title: "Пить воду" },
    { emoji: "🚶", title: "10 000 шагов" },
    { emoji: "📖", title: "Читать 20 минут" },
    { emoji: "🧘", title: "Медитация" },
    { emoji: "😴", title: "Лечь спать вовремя" },
  ];

  // Roadmap #38 — готовые "программы": набор из нескольких привычек одним
  // тапом, а не по одной. В отличие от HABIT_TEMPLATES (одна привычка за
  // клик) — это целый стартовый набор под конкретную цель.
  const HABIT_PROGRAMS = [
    { emoji: "🌅", title: "Утренняя рутина", habits: ["Выпить стакан воды", "Медитация 5 минут", "Зарядка"] },
    { emoji: "🌙", title: "Вечерний ритуал", habits: ["Отложить телефон за час до сна", "5 минут дневника", "Лечь спать вовремя"] },
    { emoji: "💪", title: "Здоровое тело", habits: ["10 000 шагов", "Пить воду", "Растяжка"] },
  ];

  // ===================== RENDER: SHOP =====================
  function renderShop() {
    const list = document.getElementById("shopList");
    const balance = document.getElementById("shopBalanceValue");
    const diamondBalance = document.getElementById("shopDiamondBalanceValue");
    const items = Array.isArray(state.shop_items) ? state.shop_items : [];

    if (balance) balance.textContent = Number(state.user?.xp || 0).toLocaleString("ru-RU");
    if (diamondBalance) diamondBalance.textContent = Number(state.user?.diamonds || 0).toLocaleString("ru-RU");
    if (!list) return;

    if (items.length === 0) {
      list.innerHTML = `<li class="empty-hint">Магазин пока пуст</li>`;
      return;
    }

    const answerItems = items.filter(it => it.item_type === "answer_pack" || /ответ/i.test(it.name || ""));
    const otherItems = items.filter(it => !answerItems.includes(it));

    const renderItem = (it) => {
      const price = Number(it.price || 0);
      const balanceValue = Number(state.user?.xp || 0);
      const canAfford = balanceValue >= price;
      const isAnswer = it.item_type === "answer_pack" || /ответ/i.test(it.name || "");
      const isDiamondPack = it.item_type === "diamond_pack_stars";
      const amountMatch = String(it.payload || it.name || "").match(/(\d+)/);
      const amount = amountMatch ? amountMatch[1] : "";

      let title = escapeHtml(it.name || "Товар ADAM");
      let desc = escapeHtml(it.description || "");

      if (isAnswer) {
        title = amount ? `+${amount} ответов` : title;
        desc = amount ? `Ещё ${amount} запросов к ADAM` : desc;
      } else if (isDiamondPack) {
        title = amount ? `+${amount} 💎` : title;
        desc = amount ? `${amount} алмазов на баланс` : desc;
      }

      // Цена находится только слева. На кнопке никогда не дублируем
      // стоимость: если хватает коинов — только «Купить».
      let btnLabel = "Купить";
      let btnClass = "buy-btn";
      let disabled = "";
      const isStars = it.item_type === "frame_stars" || it.item_type === "answer_pack_stars" || it.item_type === "booster_stars" || it.item_type === "diamond_pack_stars";
      const isFrame = it.item_type === "frame";

      if (isStars) {
        // answer_pack_stars повторяемый (можно покупать ежедневно), поэтому
        // "owned" на нём не должно навсегда блокировать кнопку — в отличие
        // от Double Gold (frame_stars), купленной один раз навсегда.
        const starsLockedForever = isStars && it.owned && !isAnswer;
        btnLabel = starsLockedForever ? "✓ Куплено" : "⭐ Купить";
        if (starsLockedForever) { btnClass += " is-owned"; disabled = "disabled"; }
      } else if (it.owned && !isAnswer) {
        btnLabel = isFrame ? "Надеть" : "✓ Куплено";
        btnClass += isFrame ? " is-equip" : " is-owned";
      } else if (!canAfford) {
        btnLabel = "Не хватает";
        btnClass += " is-unavailable";
        disabled = "disabled";
      } else {
        btnClass += " is-affordable";
      }

      return `
        <li class="shop-item ${isAnswer ? "shop-item--answers" : ""} ${isDiamondPack ? "shop-item--diamond" : ""}" data-id="${it.id}">
          <div class="shop-item__top">
            <span class="shop-item__icon">${isAnswer ? "💬" : (isDiamondPack ? "💎" : UI_SPARK)}</span>
            ${isAnswer ? `<span class="shop-item__tag">ДОП. ОТВЕТЫ</span>` : (isDiamondPack ? `<span class="shop-item__tag">АЛМАЗЫ</span>` : "")}
          </div>
          <div class="shop-item__name">${title}</div>
          <div class="shop-item__desc">${desc}</div>
          <div class="shop-item__footer">
            <div class="shop-item__price" aria-label="Цена">
              ${isStars ? "⭐" : ADAM_COIN_ICON}
              <span>${price.toLocaleString("ru-RU")}${isStars ? " Stars" : ""}</span>
            </div>
            <button class="${btnClass}" data-action="${isStars ? "stars" : (it.owned && isFrame ? "equip" : "buy")}" ${disabled}>${btnLabel}</button>
          </div>
        </li>
      `;
    };

    list.innerHTML = answerItems.map(renderItem).join("") +
      (otherItems.length ? `
        <li class="shop-divider" aria-hidden="true"></li>
        ${otherItems.map(renderItem).join("")}
      ` : "");
  }

  // ===================== "ВОЗНАГРАДИТЕ СЕБЯ" =====================
  // Простая отметка за фиксированную цену (state.self_reward_cost, из
  // db.self_rewards.DEFAULT_COST) — не покупка вещи, а лог с
  // необязательной заметкой, который открывается через "История →" (см.
  // db/self_rewards.py). Карточка пришпилена над обычным списком магазина
  // в Профиле, в духе рюрика "Вознаградите себя" из Habitica.
  function renderSelfReward() {
    const cost = Number(state.self_reward_cost || 20);
    const costLabel = cost.toLocaleString("ru-RU");
    const valueEl = document.getElementById("selfRewardCostValue");
    if (valueEl) valueEl.textContent = costLabel;
    const modalCost = document.getElementById("selfRewardModalCost");
    if (modalCost) modalCost.textContent = costLabel;
    const actionBtn = document.getElementById("selfRewardActionBtn");
    if (actionBtn) {
      const canAfford = Number(state.user?.xp || 0) >= cost;
      actionBtn.classList.toggle("is-unavailable", !canAfford);
    }
  }

  function openSelfRewardModal() {
    const overlay = document.getElementById("selfRewardModalOverlay");
    if (!overlay) return;
    const noteInput = document.getElementById("selfRewardNoteInput");
    if (noteInput) noteInput.value = "";
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("show"));
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
  }

  function closeSelfRewardModal() {
    const overlay = document.getElementById("selfRewardModalOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 280);
  }

  function openSelfRewardHistory() {
    const overlay = document.getElementById("selfRewardHistoryOverlay");
    if (!overlay) return;
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("show"));
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
    loadSelfRewardHistory();
  }

  function closeSelfRewardHistory() {
    const overlay = document.getElementById("selfRewardHistoryOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 280);
  }

  async function loadSelfRewardHistory() {
    const list = document.getElementById("selfRewardHistoryList");
    const statsEl = document.getElementById("selfRewardHistoryStats");
    if (list) list.innerHTML = `<li class="empty-hint">Загрузка…</li>`;
    try {
      const data = await api("/api/self-reward/history");
      const history = Array.isArray(data.history) ? data.history : [];
      const stats = data.stats || {};
      if (statsEl) {
        statsEl.textContent = stats.count
          ? `За последние ${stats.days} дн.: ${stats.count} раз, потрачено ${stats.total_cost} Adam Coin`
          : `За последние ${stats.days || 7} дн. пока пусто`;
      }
      if (!list) return;
      if (history.length === 0) {
        list.innerHTML = `<li class="empty-hint">Пока нет ни одной записи</li>`;
        return;
      }
      list.innerHTML = history.map(h => {
        const raw = String(h.created_at || "").replace(" ", "T") + "Z";
        const date = new Date(raw);
        const dateLabel = isNaN(date.getTime()) ? "" : date.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
        const note = h.note ? escapeHtml(h.note) : "Себя порадовал(а)";
        return `
          <li class="self-reward-history-item">
            <div class="self-reward-history-item__note">${note}</div>
            <div class="self-reward-history-item__meta">
              <span class="self-reward-history-item__date">${dateLabel}</span>
              <span class="self-reward-history-item__cost">−${Number(h.cost || 0)} A</span>
            </div>
          </li>
        `;
      }).join("");
    } catch (err) {
      if (list) list.innerHTML = `<li class="empty-hint">${escapeHtml(friendlyError(err))}</li>`;
    }
  }

  function initSelfRewardActions() {
    document.getElementById("selfRewardActionBtn")?.addEventListener("click", openSelfRewardModal);
    document.getElementById("selfRewardModalClose")?.addEventListener("click", closeSelfRewardModal);
    document.getElementById("selfRewardHistoryBtn")?.addEventListener("click", openSelfRewardHistory);
    document.getElementById("selfRewardHistoryClose")?.addEventListener("click", closeSelfRewardHistory);

    const submitBtn = document.getElementById("selfRewardModalSubmit");
    submitBtn?.addEventListener("click", async () => {
      const noteInput = document.getElementById("selfRewardNoteInput");
      const note = (noteInput?.value || "").trim();
      submitBtn.disabled = true;
      try {
        const res = await api("/api/self-reward", {
          method: "POST",
          body: JSON.stringify({ note: note || null }),
        });
        if (state.user) state.user.xp = res.xp;
        renderPlayerCard();
        renderShop();
        renderSelfReward();
        haptic("medium");
        showToast("Себя можно похвалить! 🎉", "praise", 3000);
        closeSelfRewardModal();
      } catch (err) {
        showToast(friendlyError(err), "error");
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  // ===================== RENDER: THEME PICKER =====================
  const THEMES = [
    { id: "violet", label: "Фиолетовая" },
    { id: "blue", label: "Синяя" },
    { id: "green", label: "Зелёная" },
    { id: "pink", label: "Розовая" },
  ];

  function renderThemePicker() {
    const picker = document.getElementById("themePicker");
    const hint = document.getElementById("themeHint");
    if (!picker || !hint) return;

    const owned = !!state.settings.theme_owned;
    const current = state.settings.theme || "violet";

    // Тема применяется только тем, кто её купил — иначе все пользователи
    // по умолчанию получили бы новый вид профиля вместо оригинального.
    if (owned) {
      document.body.setAttribute("data-theme", current);
    } else {
      document.body.removeAttribute("data-theme");
    }

    picker.innerHTML = THEMES.map(t => `
      <button
        class="theme-swatch theme-swatch--${t.id} ${t.id === current ? "is-active" : ""}"
        data-theme="${t.id}"
        aria-label="${t.label}"
        ${owned ? "" : "disabled"}
      ></button>
    `).join("");

    hint.textContent = owned
      ? "Выбери акцентный цвет приложения"
      : "Купи «Тема оформления» в магазине, чтобы менять цвет";
  }

  // ===================== RENDER: ACHIEVEMENTS =====================
  function renderAchievementItem(a) {
    return `
      <li class="achievement-item">
        <span class="achievement-item__icon">${escapeHtml(a.icon || "🏆")}</span>
        <div class="achievement-item__content">
          <div class="achievement-item__title">${escapeHtml(a.title)}</div>
          <div class="achievement-item__desc">${escapeHtml(a.description || "")}</div>
        </div>
      </li>
    `;
  }

  function renderAchievements() {
    const latestList = document.getElementById("achievementList");
    const archive = document.getElementById("achievementArchive");
    const archiveList = document.getElementById("achievementArchiveList");
    const countLabel = document.getElementById("achievementsCountLabel");
    const items = Array.isArray(state?.achievements) ? state.achievements : [];

    if (countLabel) countLabel.textContent = `${items.length} наград`;

    if (!latestList) return;

    if (items.length === 0) {
      latestList.innerHTML = `<li class="empty-hint">Пока нет достижений — выполняй привычки, чтобы открыть первое 🏆</li>`;
      if (archive) archive.hidden = true;
      return;
    }

    // Backend отдаёт достижения от новых к старым — сверху показываем только 2 последних.
    const latest = items.slice(0, 2);
    const older = items.slice(2);

    latestList.innerHTML = latest.map(renderAchievementItem).join("");

    if (archive && archiveList) {
      archive.hidden = older.length === 0;
      archiveList.innerHTML = older.map(renderAchievementItem).join("");
    }
  }

  // ===================== RENDER: RATING =====================
  function renderRating() {
    const list = document.getElementById("ratingList");
    const podium = document.getElementById("ratingPodium");
    const count = document.getElementById("ratingPlayerCount");
    const countLabel = document.getElementById("ratingPlayerCountLabel");
    if (!list) return;

    // Рейтинг теперь персональный — каждый видит только свою лигу (см.
    // db/leagues.py::RATING_LEAGUES). Показываем название лиги в шапке,
    // чтобы было понятно, что это не "весь" рейтинг.
    const leagueEl = document.getElementById("ratingHeroLeague");
    if (leagueEl) {
      const league = state.rating_league;
      if (league) {
        const range = league.max_streak ? `${league.min_streak}–${league.max_streak}` : `${league.min_streak}+`;
        leagueEl.textContent = `${league.name} · ${range} 🔥`;
        leagueEl.hidden = false;
      } else {
        leagueEl.hidden = true;
      }
    }
    // Если в лиге зрителя мало игроков, сервер добавил игроков из лиг ниже
    // (db/leagues.py::MIN_RATING_LEAGUE_SIZE) — говорим об этом прямо.
    const leagueNoteEl = document.getElementById("ratingHeroLeagueNote");
    if (leagueNoteEl) {
      const merged = state.rating_league && state.rating_league.merged_from;
      leagueNoteEl.textContent = merged
        ? `В твоей лиге пока мало игроков — добавили игроков из лиги ${merged} (серия от ${state.rating_league.merged_min_streak}).`
        : "";
      leagueNoteEl.hidden = !merged;
    }

    let rows = Array.isArray(state.leaderboard) ? state.leaderboard.slice() : [];
    if (count) count.textContent = rows.length;
    if (countLabel) countLabel.textContent = pluralRu(rows.length, "игрок", "игрока", "игроков");
    if (rows.length === 0) {
      if (podium) podium.innerHTML = "";
      // Раньше тут было просто "Рейтинг пока пуст" — непонятно, баг это
      // или так и должно быть (жалоба пользователя: "перепроверься").
      // Рейтинг персональный (см. комментарий выше про db/leagues.py) —
      // попадают только те, у кого серия >= 2 дней подряд, и только из
      // той же лиги, что у зрителя. Пусто значит буквально "пока никто,
      // включая тебя, не набрал серию 2+ дня" — не баг, а раннее
      // состояние лиги. Объясняем это прямо, а не оставляем догадываться.
      const league = state.rating_league;
      const requirement = league
        ? `Начни серию — 2 дня подряд, и лига «${league.name}» откроется.`
        : "Начни серию — 2 дня подряд, и рейтинг откроется.";
      list.innerHTML = `<li class="empty-hint">Здесь пока никого нет: рейтинг открывается только у тех, чья серия — 2 дня подряд и больше. ${requirement}</li>`;
      return;
    }
    const myId = state.user.telegram_id;
    const getStatus = (r) => {
      const ss = r.streak_status || {};
      const reward = (ss.rewards || [])[0];
      let status = ss.temp_status || (reward ? reward.status : "");
      if (/^Огонь\s+/i.test(status)) status = status.replace(/^Огонь/i, "В ударе");
      // «В ударе N дн.» дублирует число серии, которое и так стоит рядом с 🔥 (на подиуме и в списке) — строку не показываем; название вехи
      // («Закалка» и т.п.) остаётся.
      if (/^В ударе(\s+\d+\s+дн\.?)?$/i.test(status.trim())) status = "";
      return {ss, reward, status};
    };
    const medal = ["🥇", "🥈", "🥉"];
    if (podium) {
      // Колонка каждого места в гриде: 1-е — центр (2), 2-е — слева (1),
      // 3-е — справа (3). Кнопки 💌 вынесены в ОТДЕЛЬНЫЙ ряд-оверлей над
      // подиумом (а не в углы карточек) — карточки разной высоты и
      // смещены "ступеньками", из-за чего конвертики в углах оказывались
      // на разной высоте (фидбек — «неровно»). Оверлей выравнивает их
      // по одной линии независимо от карточек.
      const gridCol = [2, 1, 3];
      const cardsHtml = rows.slice(0, 3).map((r, idx) => {
        const rank = idx + 1, isMe = r.telegram_id === myId, name = r.first_name || r.username || "Игрок";
        const {ss, status} = getStatus(r);
        const frame = ss.temp_frame || "none";
        return `<div class="rating-podium-card rank-${rank} ${isMe ? "is-me" : ""}" data-profile-id="${Number(r.telegram_id)}">
          <div class="rating-podium-card__crown">${medal[idx]}</div>
          <div class="rating-podium-card__avatar frame-${escapeHtml(r.frame_id || frame)}">${String(r.avatar_id || "default").startsWith("upload:") ? `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(String(r.avatar_id).split(":")[1])}.jpg" alt="">` : escapeHtml((name[0] || "A").toUpperCase())}</div>
          <div class="rating-podium-card__rank">#${rank}</div>
          <div class="rating-podium-card__name">${escapeHtml(name)} ${r.badge ? "🏅" : ""}</div>
          ${r.handle ? `<div class="rating-podium-card__handle">@${escapeHtml(r.handle)}</div>` : ""}
          ${r.league_tier ? `<div class="rating-podium-card__league">${escapeHtml(r.league_tier)}</div>` : ""}
          ${Number(r.evo) > 0 ? `<div class="rating-podium-card__evo">${evoChipHtml(r.evo)}</div>` : ""}
          ${status ? `<div class="rating-podium-card__status">${escapeHtml(status)}</div>` : ""}
          <div class="rating-podium-card__stats"><span><i class="stat-icon">🔥</i> ${Number(r.streak || 0)}</span><span>${ADAM_COIN_ICON} ${Number(r.xp || 0)}</span></div>
        </div>`;
      }).join("");
      const reactionsHtml = `<div class="rating-podium-reactions" aria-hidden="false">` + rows.slice(0, 3).map((r, idx) => {
        const isMe = r.telegram_id === myId;
        const cell = `style="grid-column:${gridCol[idx]}"`;
        if (r.can_react) return `<button type="button" class="rating-podium-react-btn" ${cell} data-podium-react-target="${r.telegram_id}" aria-label="Поддержать ${escapeHtml(r.first_name || r.username || "игрока")}">💌</button>`;
        if (isMe) return `<span class="rating-podium-react-btn rating-podium-react-btn--self" ${cell} aria-hidden="true">ты</span>`;
        // не can_react и не я → сегодня уже поддержал этого игрока.
        // Показываем "галочку" вместо пустоты, чтобы ряд был ровным и
        // было понятно почему тут нет активного конвертика.
        return `<span class="rating-podium-react-btn rating-podium-react-btn--sent" ${cell} aria-hidden="true" title="Сегодня уже поддержал">${ICON_CHECK}</span>`;
      }).join("") + `</div>`;
      podium.innerHTML = cardsHtml + reactionsHtml;
    }
    list.innerHTML = rows.slice(3).map((r, i) => {
      const rank = i + 4, isMe = r.telegram_id === myId, name = r.first_name || r.username || "Игрок";
      const {ss, status} = getStatus(r);
      const frame = ss.temp_frame || "none";
      return `<li class="rating-item ${isMe ? "is-me" : ""} rank-${rank}" data-profile-id="${Number(r.telegram_id)}">
        <span class="rating-item__rank">${rank}</span>
        <span class="rating-avatar frame-${escapeHtml(r.frame_id || frame)}">${String(r.avatar_id || "default").startsWith("upload:") ? `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(String(r.avatar_id).split(":")[1])}.jpg" alt="" loading="lazy">` : escapeHtml((name[0] || "A").toUpperCase())}</span>
        <span class="rating-item__name">
          <span class="rating-item__name-line"><span class="rating-item__name-text">${escapeHtml(name)}</span>${r.handle ? `<span class="rating-item__handle">@${escapeHtml(r.handle)}</span>` : ""}${r.badge ? '<span class="rating-item__badge">🏅</span>' : ""}${isMe ? ' <span class="rating-item__me">(ты)</span>' : ""}</span>
          ${r.league_tier ? `<small class="rating-item__league">${escapeHtml(r.league_tier)}</small>` : ""}
          ${Number(r.evo) > 0 ? `<span class="rating-item__evo">${evoChipHtml(r.evo)}</span>` : ""}
          ${status ? `<small class="rating-item__status">${escapeHtml(status)}</small>` : ""}
        </span>
        <span class="rating-item__meta"><span class="rating-stat"><span class="material-symbols-rounded stat-icon">local_fire_department</span>${Number(r.streak || 0)}</span><span class="rating-stat">${ADAM_COIN_ICON}${Number(r.xp || 0)}</span></span>
        ${r.can_react
          ? `<button type="button" class="rating-item__react-btn" data-react-target="${r.telegram_id}" aria-label="Поддержать">💌</button>`
          : (isMe ? "" : `<span class="rating-item__react-btn rating-item__react-btn--sent" aria-hidden="true" title="Сегодня уже поддержал">${ICON_CHECK}</span>`)}
        ${reactPickerForId === r.telegram_id ? `
        <div class="rating-react-picker">
          ${REACTION_EMOJIS.map(e => `<button type="button" class="rating-react-chip" data-emoji="${e}">${e}</button>`).join("")}
        </div>` : ""}
      </li>`;
    }).join("");

    // Жалоба пользователя: "после обновления все пользователи в рейтинге
    // пропали" — при переходе на новый диапазон серии (31 → лига "Мастера")
    // человек оказывался в лиге один. Теперь сервер добавляет игроков из лиг
    // ниже, пока не наберётся минимум (db/leagues.py::MIN_RATING_LEAGUE_SIZE),
    // поэтому один в списке зритель остаётся лишь тогда, когда серия 2+ дня
    // сейчас вообще только у него — это и объясняем.
    if (rows.length === 1 && rows[0].telegram_id === myId) {
      list.innerHTML = `<li class="empty-hint">Пока ты единственный, у кого серия 2 дня подряд и больше. Как только кто-то ещё наберёт серию, он появится здесь.</li>`;
    }
  }

  // Должен совпадать с db/reactions.py::REACTION_EMOJIS.
  const REACTION_EMOJIS = ["🔥", "💪", "👏", "❤️", "🎉", "⭐"];

  // Roadmap #26 — GitHub-style тепловая карта года. Данные — те же
  // calendar_events, что и у вкладки "Календарь" (день → {completed,
  // total}), просто разложенные в сетку 7×N вместо помесячного вида.
  function renderYearHeatmap() {
    const wrap = document.getElementById("yearHeatmap");
    const grid = document.getElementById("yearHeatmapGrid");
    if (!wrap || !grid) return;
    const events = Array.isArray(state.calendar_events) ? state.calendar_events : [];
    if (events.length === 0) { wrap.hidden = true; return; }

    const byDay = new Map(events.map(e => [e.day, e]));
    const today = new Date();
    const days = [];
    for (let i = 364; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({ key, event: byDay.get(key) || null });
    }
    // Досыпаем пустыми ячейками в начало, чтобы первая колонка начиналась
    // с понедельника — иначе сетка 7×N "съезжает" вбок нерегулярно.
    const firstWeekday = (new Date(days[0].key).getDay() + 6) % 7; // 0=Пн
    for (let i = 0; i < firstWeekday; i++) days.unshift({ key: null, event: null });

    wrap.hidden = false;
    grid.innerHTML = days.map(d => {
      if (!d.key) return `<span class="year-heatmap__cell is-empty"></span>`;
      const ev = d.event;
      let level = 0;
      if (ev && ev.total > 0) {
        const rate = ev.completed / ev.total;
        level = rate >= 1 ? 4 : rate >= 0.66 ? 3 : rate >= 0.33 ? 2 : 1;
      }
      const title = ev ? `${d.key}: ${ev.completed}/${ev.total}` : d.key;
      return `<span class="year-heatmap__cell" data-level="${level}" title="${escapeHtml(title)}"></span>`;
    }).join("");
  }

  // ===================== RENDER: CALENDAR =====================
  function renderCalendar() {
    const grid = document.getElementById("calendarGrid");
    if (!grid) return;

    const byDay = {};
    (state.calendar_events || []).forEach(ev => {
      byDay[ev.day] = { completed: Number(ev.completed || 0), total: Number(ev.total || 0) };
    });

    const today = new Date();
    const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
    const monthName = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" })
      .format(monthStart);
    const monthTitle = monthName.charAt(0).toUpperCase() + monthName.slice(1);

    const todayKey = [
      today.getFullYear(),
      String(today.getMonth() + 1).padStart(2, "0"),
      String(today.getDate()).padStart(2, "0")
    ].join("-");

    // Текущий месяц + несколько последних дней предыдущего/следующего,
    // чтобы календарь всегда выглядел как полноценная сетка.
    const firstWeekday = (monthStart.getDay() + 6) % 7; // Пн = 0
    const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    const cellsCount = Math.ceil((firstWeekday + daysInMonth) / 7) * 7;

    const monthEvents = [];
    for (let day = 1; day <= daysInMonth; day++) {
      const d = new Date(today.getFullYear(), today.getMonth(), day);
      const key = [
        d.getFullYear(),
        String(d.getMonth() + 1).padStart(2, "0"),
        String(day).padStart(2, "0")
      ].join("-");
      const info = byDay[key] || { completed: 0, total: 0 };
      monthEvents.push({ key, day, ...info });
    }

    const completedDays = monthEvents.filter(d => d.total > 0 && d.completed >= d.total).length;
    const activeDays = monthEvents.filter(d => d.total > 0).length;
    const completedTasks = monthEvents.reduce((sum, d) => sum + d.completed, 0);
    const totalTasks = monthEvents.reduce((sum, d) => sum + d.total, 0);
    const completion = totalTasks ? Math.round((completedTasks / totalTasks) * 100) : 0;
    const todayInfo = byDay[todayKey] || { completed: 0, total: 0 };
    const todayPercent = todayInfo.total
      ? Math.round((todayInfo.completed / todayInfo.total) * 100)
      : 0;

    const stat = (value, label, extra = "") => `
      <div class="calendar-stat ${extra}">
        <strong>${value}</strong>
        <span>${label}</span>
      </div>`;

    // График "% выполнено" за последние 14 дней (просьба пользователя:
    // графики в календаре). byDay построен из state.calendar_events, а он
    // приходит с бэкенда БЕЗ ограничения по месяцу (db/calendar.py::get_calendar
    // отдаёт всю историю) — поэтому тренд корректен даже в первые дни месяца,
    // когда часть окна приходится на предыдущий. Один ряд, одна цель (доля
    // выполненного) — легенда не нужна (см. dataviz: заголовок сам называет
    // серию), подписи только у крайних и сегодняшнего дня (не на каждом баре).
    const TREND_DAYS = 14;
    const trendDays = [];
    for (let i = TREND_DAYS - 1; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, "0"), String(d.getDate()).padStart(2, "0")].join("-");
      const info = byDay[key] || { completed: 0, total: 0 };
      const percent = info.total ? Math.round((info.completed / info.total) * 100) : 0;
      trendDays.push({ key, date: d, percent, info });
    }
    const trendChart = (() => {
      const W = 350, H = 92, padBottom = 18, padTop = 6;
      const barGap = 4;
      const barW = (W - barGap * (TREND_DAYS - 1)) / TREND_DAYS;
      const plotH = H - padBottom - padTop;
      const bars = trendDays.map((d, i) => {
        const x = i * (barW + barGap);
        const h = Math.max(2, (d.percent / 100) * plotH);
        const y = H - padBottom - h;
        const isToday = d.key === todayKey;
        const fill = d.percent > 0 ? "var(--gold)" : "var(--border)";
        return `<rect class="cal-trend-bar__rect ${isToday ? "is-today" : ""}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="3" fill="${fill}"></rect>`;
      }).join("");
      const buttons = trendDays.map((d, i) => {
        const x = i * (barW + barGap);
        const label = d.info.total ? `${d.info.completed} из ${d.info.total} выполнено` : "Нет отметок";
        return `<button type="button" class="cal-trend-bar" data-cal-day="${d.key}" title="${d.key}: ${label}"
                  style="left:${(x / W * 100).toFixed(2)}%;width:${(barW / W * 100).toFixed(2)}%;"></button>`;
      }).join("");
      const firstLabel = trendDays[0].date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
      return `
        <div class="calendar-trend" role="img" aria-label="Процент выполненных привычек за последние 14 дней">
          <div class="calendar-trend__head">
            <span>Прогресс за 14 дней</span>
          </div>
          <div class="calendar-trend__chart">
            <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="calendar-trend__svg">
              <line x1="0" y1="${(H - padBottom).toFixed(1)}" x2="${W}" y2="${(H - padBottom).toFixed(1)}" class="calendar-trend__baseline"></line>
              ${bars}
            </svg>
            <div class="calendar-trend__hit">${buttons}</div>
          </div>
          <div class="calendar-trend__axis">
            <span>${escapeHtml(firstLabel)}</span>
            <span>Сегодня: ${todayPercent}%</span>
          </div>
        </div>`;
    })();

    const cells = [];
    for (let i = 0; i < cellsCount; i++) {
      const dayNumber = i - firstWeekday + 1;
      const inMonth = dayNumber >= 1 && dayNumber <= daysInMonth;

      if (!inMonth) {
        cells.push(`<div class="cal-cell cal-cell--empty" aria-hidden="true"></div>`);
        continue;
      }

      const d = monthEvents[dayNumber - 1];
      let level = 0;
      if (d.completed > 0 && d.total > 0) {
        const ratio = d.completed / d.total;
        level = ratio >= 1 ? 3 : ratio >= 0.5 ? 2 : 1;
      }

      const isToday = d.key === todayKey;
      const label = d.total > 0
        ? `${d.completed} из ${d.total} выполнено`
        : "Нет отметок";

      cells.push(`
        <button class="cal-cell cal-cell--${level} ${isToday ? "is-today" : ""}"
                type="button"
                data-cal-day="${d.key}"
                title="${d.key}: ${label}">
          <span class="cal-cell__day">${d.day}</span>
          ${d.total > 0 ? `<span class="cal-cell__progress">${d.completed}/${d.total}</span>` : ""}
        </button>
      `);
    }

    grid.innerHTML = `
      <div class="calendar-card">
        <div class="calendar-card__head">
          <div>
            <div class="calendar-eyebrow">ТВОЙ ПРОГРЕСС</div>
            <h2>${monthTitle}</h2>
            <p>Каждый день здесь показывает, насколько ты приблизился к своим целям.</p>
          </div>
          <div class="calendar-today-badge"><b>${today.getDate()}</b></div>
        </div>

        <div class="calendar-stats">
          ${stat(`${todayPercent}%`, "день")}
          ${stat(completedDays, "идеальных дней")}
          ${stat(activeDays, "активных дней")}
          ${stat(`${completion}%`, "за месяц")}
        </div>

        ${trendChart}

        <div class="calendar-weekdays">
          <span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span>
          <span>Пт</span><span>Сб</span><span>Вс</span>
        </div>

        <div class="calendar-month-grid">
          ${cells.join("")}
        </div>

        <div class="calendar-selected" id="calendarSelected">
          <div class="calendar-selected__icon">📅</div>
          <div>
            <strong>${today.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })}</strong>
            <span>${todayInfo.total ? `${todayInfo.completed} из ${todayInfo.total} привычек выполнено` : "Пока нет отмеченных привычек"}</span>
          </div>
        </div>

        <div class="calendar-legend">
          <span>Меньше</span>
          <i class="cal-cell cal-cell--0"></i>
          <i class="cal-cell cal-cell--1"></i>
          <i class="cal-cell cal-cell--2"></i>
          <i class="cal-cell cal-cell--3"></i>
          <span>Больше</span>
        </div>
      </div>
    `;

    // Детальная карточка выбранного дня.
    grid.querySelectorAll("[data-cal-day]").forEach(btn => {
      btn.addEventListener("click", () => {
        haptic("light");
        const key = btn.dataset.calDay;
        const info = byDay[key] || { completed: 0, total: 0 };
        const d = new Date(`${key}T12:00:00`);
        const title = d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
        const selected = document.getElementById("calendarSelected");
        if (selected) {
          selected.innerHTML = `
            <div class="calendar-selected__icon">${info.total && info.completed >= info.total ? "🔥" : "📅"}</div>
            <div>
              <strong>${title}</strong>
              <span>${info.total ? `${info.completed} из ${info.total} привычек выполнено` : "В этот день нет отмеченных привычек"}</span>
            </div>
          `;
        }
        grid.querySelectorAll(".cal-cell.is-selected").forEach(x => x.classList.remove("is-selected"));
        btn.classList.add("is-selected");
      });
    });
  }

  // ===================== TABS =====================
function initTabs() {
  const tabBar = document.getElementById("tabBar");
  if (!tabBar) return;
  tabBar.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab-bar__item");
    if (!btn) return;
    const tab = btn.dataset.tab;
    if (!tab) return;
    // См. closeAllSubpageOverlays() — забытый открытым оверлей иначе
    // молча переживает переключение вкладки и выныривает поверх экрана
    // при возврате.
    closeAllSubpageOverlays();
    // Фидбек: чернение экрана на скролле было частично починено (см.
    // initScrollPerfGuard), но не исчезло — видео и повтор жалобы после
    // фикса показывают, что чёрные кадры ловятся и на ПЕРЕКЛЮЧЕНИИ ВКЛАДОК:
    // скрытие/показ целой .tab-panel + её собственная CSS-анимация
    // (adamPanelIn .38s) — это такой же большой перерасчёт области под
    // размытым .tab-bar, как и скролл, просто триггер другой. Гасим blur
    // на время этого всплеска тем же классом .is-scrolling.
    // .player-card — постоянный header над всеми вкладками, тот же
    // размытый+анимированный элемент, что и .tab-bar — гасим синхронно.
    const playerCard = document.querySelector("header.player-card");
    tabBar.classList.add("is-scrolling");
    playerCard?.classList.add("is-scrolling");
    clearTimeout(tabBar._perfGuardTimer);
    tabBar._perfGuardTimer = setTimeout(() => {
      tabBar.classList.remove("is-scrolling");
      playerCard?.classList.remove("is-scrolling");
    }, 450);
    document.querySelectorAll(".tab-bar__item").forEach(b => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".tab-panel").forEach(panel => {
      const active = panel.dataset.tab === tab;
      panel.hidden = !active;
      // Do not animate the entire tab panel. Profile is a tall DOM tree;
      // transform/opacity on the whole panel makes Telegram WebView
      // recalculate/composite thousands of pixels and causes a visible jerk.
      if (active) panel.classList.remove("tab-enter");
    });
    // Цель открытой подсказки может оказаться на скрытой теперь вкладке
    // (стартовые шаги 1-2 живут на Главной) — кнопка вкладки-цели
    // (шаги 3-5) при этом всегда на экране сама по себе, так что прожектор
    // сам не погас бы при уходе на другую вкладку. Гасим явно, если цель
    // больше не видна (жалоба пользователя: подсказка должна убираться
    // вместе с фокусом, а не висеть на произвольной вкладке).
    if (productHintTarget && productHintTarget.offsetParent === null) {
      hideProductHint();
    } else if (activeHintStage && CONTEXT_TAB_STAGE[tab] !== activeHintStage && productHintTarget?.closest('.tab-bar')) {
      hideProductHint();
    }
    // Загружаем только открытый раздел. Никаких фоновых запросов к
    // календарю/рейтингу/профилю при нахождении на Главной.
    if (tab === "profile" || tab === "rating" || tab === "calendar") {
      loadBootstrapSecondary(tab);
    }
    if (tab === "profile") {
      loadProgressStats();
    }
    haptic("select");
    scheduleDecorSettle();
  });
}

// ===================== ПОДСТРАНИЦЫ ПРОФИЛЯ (Магазин / Настройки) =====================
// Просьба пользователя: Профиль был одной длинной лентой (Магазин +
// Настройки + Данные и поддержка) — "вниз сильно много листать". Обе
// секции теперь открываются отдельным полноэкранным оверлеем поверх
// текущей вкладки (тот же приём, что и у daily-quests-overlay), их
// содержимое и id внутри не менялись — просто скрыты за компактной
// карточкой-переходом в Профиле, пока оверлей не открыт.
// Стрелка «назад» Telegram видна, пока открыта хотя бы одна подстраница.
function syncBackButton() {
  const back = window.Telegram?.WebApp?.BackButton;
  if (!back) return;
  const anyOpen = !!document.querySelector(".subpage-overlay.is-open");
  try { if (anyOpen) back.show(); else back.hide(); } catch (_) {}
}

// Верхняя подстраница: магазин/прогресс лежат поверх настроек (--nested).
function closeTopSubpage() {
  const zIndexOf = (overlay) => Number.parseInt(getComputedStyle(overlay).zIndex, 10) || 0;
  const open = Array.from(document.querySelectorAll(".subpage-overlay.is-open"));
  const top = open.sort((a, b) => zIndexOf(b) - zIndexOf(a))[0];
  top?.querySelector(".subpage-overlay__back")?.click();
}

function initSubpageOverlay(overlayId, triggerId, closeId, backdropId) {
  const overlay = document.getElementById(overlayId);
  const trigger = document.getElementById(triggerId);
  if (!overlay || !trigger) return;
  const open = () => {
    overlay.hidden = false;
    // Каждый раз открывается с самого начала, а не там, где закрыли в прошлый раз.
    const scroller = overlay.querySelector(".subpage-overlay__body");
    if (scroller) scroller.scrollTop = 0;
    requestAnimationFrame(() => { overlay.classList.add("is-open"); syncBackButton(); });
    overlay.setAttribute("aria-hidden", "false");
    haptic("light");
    // Открытая подстраница ложится ПОВЕРХ текущей цели подсказки
    // онбординга (жалоба пользователя: тур выглядит зависшим) — сама
    // подсказка технически ещё "видима" (offsetParent цели не null, её
    // просто закрыл оверлей), поэтому автогашение в initTabs (по
    // offsetParent) тут не срабатывает. Гасим явно при открытии любой
    // подстраницы, а не только при уходе на другую нижнюю вкладку.
    hideProductHint();
  };
  const close = () => {
    haptic("light");
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { if (!overlay.classList.contains("is-open")) overlay.hidden = true; }, 300);
    syncBackButton();
  };
  trigger.addEventListener("click", open);
  document.getElementById(closeId)?.addEventListener("click", close);
  document.getElementById(backdropId)?.addEventListener("click", close);
}

function initSubpageOverlays() {
  // Магазин, «Прогресс» и «Настройки» лежат в HTML внутри <section class="tab-panel">
  // (contain:layout) и внутри <main id="content"> (transform от анимации) — оба
  // делают предком для position:fixed. «Полноэкранный» оверлей получал размер
  // вкладки: начинался под карточкой игрока, а нижняя часть уезжала за край
  // окна, и последние строки магазина нельзя было промотать из-под нижней
  // панели (скриншот с телефона). Переносим их в body — как уже сделано у
  // остальных модалок (квесты дня, подарки). Id и обработчики не меняются.
  ["shopOverlay", "statsOverlay", "settingsOverlay"].forEach((id) => {
    const overlay = document.getElementById(id);
    if (overlay && overlay.parentElement !== document.body) document.body.appendChild(overlay);
  });
  initSubpageOverlay("shopOverlay", "openShopBtn", "shopOverlayClose", "shopOverlayBackdrop");
  initSubpageOverlay("statsOverlay", "openStatsBtn", "statsOverlayClose", "statsOverlayBackdrop");
  initSubpageOverlay("settingsOverlay", "openSettingsBtn", "settingsOverlayClose", "settingsOverlayBackdrop");
}

// Баг (жалоба пользователя: "онбординг дальше не хочет продолжаться" +
// ощущение лага): Магазин/Настройки/Прогресс физически лежат ВНУТРИ
// <section data-tab="profile"> — переключение НИЖНЕЙ вкладки прячет их
// вместе со всей секцией через ancestor [hidden], но не сбрасывает их
// собственное "is-open" состояние (initSubpageOverlay.close() дергается
// только явным крестиком/бэкдропом). Открыл Настройки → ушёл на Календарь →
// вернулся в Профиль — Настройки как ни в чём не бывало выныривают поверх
// экрана, закрывая собой в том числе цели шагов 8-9 онбординга
// (#openShopBtn/#openSettingsBtn), из-за чего "Далее" внутри подсказки
// не находит видимую цель и тур выглядит зависшим.
function closeAllSubpageOverlays() {
  document.querySelectorAll(".subpage-overlay.is-open").forEach((overlay) => {
    overlay.classList.remove("is-open");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { if (!overlay.classList.contains("is-open")) overlay.hidden = true; }, 300);
  });
  syncBackButton();
}

// ===================== СВОРАЧИВАЕМЫЕ ФОРМЫ ДОБАВЛЕНИЯ =====================
// "Новая задача"/"Новая привычка" раньше были видны всегда — по умолчанию
// теперь спрятаны за компактной кнопкой "+" (см. .add-collapse в index.html
// и style.css), сама форма и её id/JS не менялись.
function openAddCollapse(collapseId) {
  const collapse = document.getElementById(collapseId);
  if (!collapse) return;
  haptic("light");
  const trigger = collapse.querySelector(".add-collapse__trigger");
  const form = collapse.querySelector("form");
  if (trigger) trigger.hidden = true;
  if (form) {
    form.hidden = false;
    const focusable = form.querySelector('input[type="text"]');
    if (focusable) focusable.focus();
  }
}

function closeAddCollapse(collapseId) {
  const collapse = document.getElementById(collapseId);
  if (!collapse) return;
  const trigger = collapse.querySelector(".add-collapse__trigger");
  const form = collapse.querySelector("form");
  if (form) form.hidden = true;
  if (trigger) trigger.hidden = false;
}

// ===================== HABIT ACTIONS =====================
// Мгновенный (оптимистичный) вид строки привычки на тап — до ответа
// сервера. Для обычной привычки сразу ставит "выполнено"; для счётчика
// (кнопка вида "2/5") прибавляет единицу и, если цель достигнута, тоже
// отмечает выполненной. Возвращает функцию отката на случай ошибки запроса.
// Настоящее состояние всё равно приходит из ответа сервера и подменяет
// строку целиком (applyActionPatch → renderSingleHabit).
function applyOptimisticHabitTap(li, btn) {
  const prevLabel = btn.textContent;
  const prevWasCounter = btn.classList.contains("habit-item__check--counter");
  const m = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(prevLabel);
  const nextCount = m ? Number(m[1]) + 1 : null;
  const completes = !m || nextCount >= Number(m[2]);
  if (completes) {
    li.classList.add("is-done");
    btn.classList.remove("habit-item__check--counter");
    btn.textContent = "✓";
  } else {
    btn.textContent = `${nextCount}/${m[2]}`;
  }
  return () => {
    li.classList.remove("is-done");
    if (prevWasCounter) btn.classList.add("habit-item__check--counter");
    btn.textContent = prevLabel;
    btn.disabled = false;
  };
}

// Общая "победная" реакция после выполнения привычки — вызывается и из
// /complete, и из /progress (когда счётчик как раз достиг цели), чтобы не
// дублировать монеты/streak/идеальный-день/цепочку в двух местах.
async function celebrateHabitCompletion(result) {
  const boostTag = result.xp_boosted ? " ⚡x2 бустер" : (result.doubled ? " ⚡️×2" : "");
  // Сверх дневного потолка наград (db/habits.py) привычка отмечена, а монет нет:
  // `result.coins || 10` показал бы «+10», поэтому честно говорим про потолок.
  if (result.reward_capped) {
    showToast(`Отмечено ✓ ${result.reward_cap_text || "Монеты за сегодня уже выданы — завтра снова"}`, "success", 4200);
  } else {
    showToast(`+${result.coins || 10} Adam Coin` + boostTag, "praise");
  }
  if (state?.show_app_tour) {
    onboardingHabitDoneOnce = true;
    maybeOfferAnotherHabit();
  }
  if (result.streak_event) {
    pendingBonusIntro = !!result.show_bonus_intro;
    openStreakCelebration(result.streak_event);
  } else if (result.bonus_active) {
    // Фидбек: большое окно x2-бонуса раньше показывалось только на ПЕРВОЙ
    // привычке дня (show_bonus_intro завязан на streak_event, а тот бывает
    // только раз в день). На второй/третьей и т.д. привычке того же дня
    // окно бонуса тоже продлевается на 30 минут (см. db/habits.py::
    // complete_habit), но пользователь никак об этом не узнавал — простой
    // короткий тост вместо полноразмерного окна, как и просили.
    scheduleBonusHint();
  }
  // Пром 8 (доп.): "идеальный день" и, раз в месяц, награда за идеальный
  // месяц — показываем следом за тостом монет, со сдвигом, чтобы не
  // перекрывать друг друга в одном #toast элементе.
  if (result.perfect_day_message) {
    setTimeout(() => showToast(result.perfect_day_message, "praise", 4200), 2400);
  }
  // Roadmap #7 — цепочки привычек: мягкая подсказка "сделал А → предложи Б".
  if (result.chain_suggestion) {
    setTimeout(() => {
      showToast(`👉 Может, теперь «${result.chain_suggestion.title}»?`, "success", 4000);
    }, result.perfect_day_message ? 6800 : 2400);
  }
  // «Напомнить друзьям»: сервер присылает окно один раз в день, и только
  // если есть кому напомнить (db/friends.py::claim_remind_prompt). Заодно
  // карточка «Друзья» узнаёт, что сегодня ты уже отметился — кнопки
  // «Напомнить» в ней становятся активными без перезагрузки.
  if (friendsData) {
    friendsData.viewer_done = true;
    renderFriendsCard();
  }
  if (result.remind_friends) scheduleRemindFriends(result.remind_friends);
}

// Раньше ЛЮБАЯ отметка привычки (в том числе просто +1 к счётчику, ещё
// не закрывающий цель) вызывала await loadBootstrap() — это отдельный
// сетевой запрос ЗА ВСЕМ главным экраном разом, плюс renderAll()
// перерисовывает буквально все секции (план дня, квесты, лигу, героя,
// настройки темы/языка/пола, проверки обучения) — хотя от отметки ОДНОЙ
// привычки могли измениться только: сам пользователь (xp/уровень/монеты),
// сама эта привычка, квесты дня. /api/habits/{id}/complete и /progress
// теперь возвращают ровно эти три вещи готовыми (_shape_user/_shape_habit
// в webapp_server.py) — патчим state точечно и рендерим только то, что
// реально могло поменяться, без единого лишнего запроса или перерисовки.
function applyActionPatch(result) {
  if (!state || !result) return;
  if (result.user) {
    Object.assign(state.user, result.user);
    // Жалоба пользователя: модалка "LEVEL UP" появлялась с задержкой в
    // 15-20 минут после реального повышения уровня — раньше её показывал
    // только loadBootstrap() (полная перезагрузка), а applyActionPatch
    // (лёгкий патч после каждой отметки привычки, см. комментарий ниже)
    // молча обновлял state.user.level без сравнения со старым значением.
    // Дублируем ту же проверку knownLevel здесь, чтобы левел-ап праздновался
    // сразу, а не при следующей случайной полной перезагрузке.
    const newLevel = state.user.level;
    if (knownLevel !== null && newLevel > knownLevel) {
      showLevelUp(newLevel, knownLevel);
    }
    knownLevel = newLevel;
  }
  let habitPatched = false;
  if (result.habit) {
    const idx = (state.habits || []).findIndex(h => h.id === result.habit.id);
    if (idx !== -1) state.habits[idx] = result.habit;
    // По телеметрии с реальных устройств (05.09) — даже "точечный" патч
    // всё равно гонял renderHabits(), а она пересобирает innerHTML ВСЕГО
    // списка на каждый тап по одной привычке. На телефонах пользователей
    // (включая мощные — Samsung S938B, 8ГБ/8 ядер) это давало серии
    // long_task/long_frame по 100-700мс каждый тап. Теперь подменяем
    // только один <li> (renderSingleHabit) и обновляем счётчик "N/M"
    // сверху вручную — полный renderHabits() остаётся страховкой на
    // случай, если точечная замена не смогла найти нужный узел.
    habitPatched = renderSingleHabit(result.habit.id);
    if (habitPatched) {
      const progressLabel = document.getElementById("habitsProgressLabel");
      if (progressLabel) {
        const habits = (state.habits || []).filter(h => !pendingDeleteHabitIds.has(h.id));
        const done = habits.filter(h => h.completed).length;
        progressLabel.textContent = `${done}/${habits.length}`;
      }
    }
  }
  const questsBefore = state.daily_quests;                                    // для окна «Квесты дня»: что было до этого действия
  const monthBefore = state.month_quests ? JSON.parse(JSON.stringify(state.month_quests)) : null;
  if (result.daily_quests) state.daily_quests = result.daily_quests;
  if (result.streak) state.streak = result.streak;
  if (result.month_quests) applyMonthQuests(result.month_quests);
  if (result.pair_quest !== undefined) applyPairQuest(result.pair_quest);
  if (result.pet) state.pet = result.pet;
  const previousHero = state.hero;
  if (result.hero) state.hero = result.hero;

  renderPlayerCard();
  if (result.habit && !habitPatched) renderHabits();
  renderTodayFocus();
  renderStreak();
  renderBoosterBanner();
  if (result.daily_quests) {
    renderDailyQuests();
    announceQuestChange(questsBefore, monthBefore);
  }
  if (result.hero) {
    renderHeroWidget();
    announceHeroGrowth(previousHero, result.hero);
  }
  // «Пинок» перерисовки — только заменённой строке привычки и дням серии (а не всему списку).
  const changedRow = habitPatched && result.habit ? document.querySelector(`.habit-item[data-id="${result.habit.id}"]`) : null;
  stabilizeFirstPaint([changedRow, "streakDays"].filter(Boolean), { lists: false });
  scheduleAdamNudge({ delayMs: ADAM_NUDGE_AFTER_ACTION_MS });
}

// Тот же приём, что и applyActionPatch выше, для тумблеров плана дня
// (/api/plan/task/toggle, /api/plan/main/toggle) — эти действия вообще
// не трогают XP/монеты/streak/квесты, только сам план дня.
function applyPlanPatch(result) {
  if (!state || !result || !result.daily_plan) return;
  state.daily_plan = result.daily_plan;
  renderPlan();
  renderTodayFocus();
  stabilizeFirstPaint(["planList"], { lists: false });
  scheduleAdamNudge({ delayMs: ADAM_NUDGE_AFTER_ACTION_MS });
}

// Roadmap #3 — заметка/фото к выполненной привычке: маленькая встроенная
// форма прямо под карточкой привычки (без модалки), фото сжимается на
// клиенте в canvas перед отправкой, чтобы не раздувать запрос/БД.
function openHabitNotePrompt(habitId) {
  const li = document.querySelector(`.habit-item[data-id="${habitId}"]`);
  if (!li) return;
  if (li.querySelector(".habit-note-form")) return; // уже открыта

  const form = document.createElement("div");
  form.className = "habit-note-form";
  form.innerHTML = `
    <textarea class="habit-note-form__text" maxlength="300" placeholder="Как прошло? (необязательно)"></textarea>
    <div class="habit-note-form__row">
      <label class="habit-note-form__photo-btn">
        📷 Фото
        <input type="file" accept="image/*" class="habit-note-form__file" hidden>
      </label>
      <span class="habit-note-form__filename"></span>
      <button type="button" class="habit-note-form__cancel">Отмена</button>
      <button type="button" class="habit-note-form__save">Сохранить</button>
    </div>
  `;
  li.appendChild(form);
  form.querySelector(".habit-note-form__text").focus();

  let photoDataUrl = null;
  const fileInput = form.querySelector(".habit-note-form__file");
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    try {
      photoDataUrl = await compressImageToDataUrl(file);
      form.querySelector(".habit-note-form__filename").textContent = "✓ " + file.name;
    } catch (err) {
      showToast("Не получилось обработать фото", "error");
    }
  });

  form.querySelector(".habit-note-form__cancel").addEventListener("click", () => form.remove());
  form.querySelector(".habit-note-form__save").addEventListener("click", async () => {
    const note = form.querySelector(".habit-note-form__text").value.trim();
    if (!note && !photoDataUrl) {
      showToast("Добавь текст или фото", "error");
      return;
    }
    try {
      await api(`/api/habits/${habitId}/note`, {
        method: "POST",
        body: JSON.stringify({ note: note || undefined, photo_data_url: photoDataUrl || undefined }),
      });
      haptic("light");
      showToast("Сохранено в дневник", "success");
      form.remove();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// Сжимает фото в браузере до небольшого превью (макс. сторона 640px, JPEG
// качество 0.6) перед тем как превращать в data:URL — без этого исходное
// фото с телефона (несколько МБ) не пролезло бы ни в лимит API, ни в БД.
function compressImageToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("bad_image"));
      img.onload = () => {
        const maxSide = 640;
        let { width, height } = img;
        if (width > height && width > maxSide) { height = Math.round(height * maxSide / width); width = maxSide; }
        else if (height > maxSide) { width = Math.round(width * maxSide / height); height = maxSide; }
        const canvas = document.createElement("canvas");
        canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL("image/jpeg", 0.6));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

// ===================== Окно «Сундук» =====================
// Открытие сундука месяца и парного задания — как ежедневный сундук в Duolingo, только за сложные задания: сундук появляется, его нужно
// тапнуть три раза. Каждый тап — удар по сундуку со своим звуком и лёгкой вибрацией (всё сильнее, шкала из трёх точек и свечение растут);
// после третьего уходит запрос, сундук потряхивает, крышка откидывается, вылетают искры, появляются награды, идёт тройная долгая вибрация.
// «Не сейчас» закрывает окно без запроса: сундук остаётся ждать. Данные (монеты, месяц, уровень) применяем, когда окно закрыли: левел-ап
// не прячется под сундуком, а числа в шапке меняются на глазах. Только transform/opacity и конечные анимации (см. style.css).
let chestRevealBusy = false;
// 24.10: «Забрать» нажимают с запасом (кнопка оживает не сразу) — лишний тап после закрытия сундука попадал в строку квеста под ним и
// уводил из окна «Квесты дня» на Главную. После закрытия сундука тапы по окну квестов полсекунды не считаются.
const QUEST_STRAY_TAP_MS = 700;
let chestClosedAt = 0;
const questTapIsStray = () => Date.now() - chestClosedAt < QUEST_STRAY_TAP_MS;
const CHEST_TAPS = 3;
const CHEST_SHAKE_MS = 700;

function playChestSound(phase, n = 1) {
  if (evolutionSilence) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    // [частота, начало (с), длительность (с), тип, громкость]: глухой удар по дереву (с каждым тапом чуть ниже и плотнее) и восходящий «дзынь» при открытии
    const knock = [
      [[160, 0, .1, "triangle", .22], [330, 0, .03, "square", .04]],
      [[180, 0, .1, "triangle", .25], [110, .05, .09, "triangle", .2], [380, 0, .03, "square", .05]],
      [[210, 0, .11, "triangle", .3], [125, .05, .1, "triangle", .26], [85, .1, .12, "triangle", .22], [440, 0, .03, "square", .06]],
    ];
    const notes = phase === "open"
      ? [[523, 0, .2, "sine", .13], [659, .09, .2, "sine", .13], [784, .18, .22, "sine", .13], [1047, .28, .45, "sine", .14]]
      : knock[Math.min(Math.max(n, 1), CHEST_TAPS) - 1];
    let end = 0;
    notes.forEach(([freq, at, dur, type, vol]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const t = ctx.currentTime + at;
      osc.type = type;
      osc.frequency.setValueAtTime(freq, t);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(vol, t + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(t); osc.stop(t + dur + 0.02);
      end = Math.max(end, at + dur);
    });
    setTimeout(() => { try { ctx.close(); } catch (_) {} }, (end + 0.3) * 1000);
  } catch (_) {}
}

// Тап по сундуку: n = 1..3. Вибро — от тихого к заметному (см. haptics.js::knock).
function hapticKnock(n) {
  if (window.AdamHaptics && window.AdamHaptics.knock) window.AdamHaptics.knock(n);
  else haptic("light");
}

function chestPrizeItems(reward) {
  const items = [];
  const chip = (icon, big, small) => items.push(
    `<li class="chest-prize" style="--i:${items.length}"><span class="chest-prize__icon">${icon}</span><b>${big}</b><small>${small}</small></li>`
  );
  const coins = Number(reward.coins) || 0;
  const diamonds = Number(reward.diamonds) || 0;
  const points = Number(reward.month_points) || 0;
  if (coins) chip(ADAM_COIN_ICON, `+${coins}`, "Adam Coin");
  if (diamonds) chip("💎", `+${diamonds}`, pluralRu(diamonds, "алмаз", "алмаза", "алмазов"));
  if (points) chip("🎯", `+${points}`, "к заданиям месяца");
  if (reward.badge) {
    items.push(
      `<li class="chest-prize chest-prize--wide" style="--i:${items.length}"><span class="chest-prize__icon">🏆</span>` +
      `<span class="chest-prize__text"><b>Значок «${escapeHtml(reward.badge)}»</b><small>в твоих достижениях</small></span></li>`
    );
  }
  return items;
}

function hideChestOverlay(overlay) {
  chestClosedAt = Date.now();
  overlay.classList.remove("show");
  overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!chestRevealBusy) overlay.hidden = true; }, 240);
}

const CHEST_TAP_HINTS = ["Тапни 3 раза", "Ещё 2 раза", "Ещё один!"];

// request — функция, делающая запрос на открытие (вернёт ответ с reward; её зовут только после третьего тапа); onDone(result) — когда окно
// закрыли. Ошибку запроса окно не глотает: закрывается и пробрасывает её вызывающему (там показывают тост). «Не сейчас» → null, запроса не было.
async function openChestReveal({ eyebrow, caption = "", final = false, tone = "", request, onDone }) {
  const overlay = document.getElementById("chestOverlay");
  const card = document.getElementById("chestCard");
  if (!overlay || !card || chestRevealBusy) {
    const result = await request();
    if (onDone) onDone(result);
    return result;
  }
  const stage = document.getElementById("chestStage");
  const title = document.getElementById("chestTitle");
  const sub = document.getElementById("chestSub");
  const hint = document.getElementById("chestHint");
  const prizes = document.getElementById("chestPrizes");
  const closeBtn = document.getElementById("chestClose");
  chestRevealBusy = true;
  card.classList.toggle("is-final", !!final);
  card.dataset.tone = tone || "";          // цвет сундука квеста (wood / blue / gold), см. .chest-card[data-tone]
  card.dataset.phase = "wait";
  card.dataset.taps = "0";
  card.dataset.ready = "0";
  document.getElementById("chestEyebrow").textContent = eyebrow;
  title.textContent = "Тебя ждёт награда";
  sub.textContent = caption;
  hint.textContent = CHEST_TAP_HINTS[0];
  prizes.innerHTML = "";
  closeBtn.textContent = "Не сейчас";
  closeBtn.disabled = false;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  void overlay.offsetWidth;
  overlay.classList.add("show");
  haptic("light");

  // Ждём три тапа по сундуку (или «Не сейчас»).
  const outcome = await new Promise((resolve) => {
    let taps = 0;
    const finish = (value) => {
      stage.removeEventListener("click", onTap);
      overlay.removeEventListener("click", onWaitClick);
      document.removeEventListener("keydown", onWaitKey);
      resolve(value);
    };
    function onTap() {
      if (taps >= CHEST_TAPS) return;
      taps += 1;
      card.dataset.taps = String(taps);
      stage.style.setProperty("--kn", String(taps));
      stage.classList.remove("is-knock");
      void stage.offsetWidth;                 // перезапуск анимации удара
      stage.classList.add("is-knock");
      hapticKnock(taps);
      playChestSound("tap", taps);
      hint.textContent = CHEST_TAP_HINTS[taps] || "";
      if (taps >= CHEST_TAPS) finish("tapped");
    }
    function onWaitClick(e) { if (e.target === overlay || e.target.closest("#chestClose")) finish("later"); }
    function onWaitKey(e) {
      if (e.key === "Escape") finish("later");
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onTap(); }
    }
    stage.addEventListener("click", onTap);
    overlay.addEventListener("click", onWaitClick);
    document.addEventListener("keydown", onWaitKey);
  });
  if (outcome === "later") {
    chestRevealBusy = false;
    hideChestOverlay(overlay);
    return null;
  }

  card.dataset.phase = "shake";
  title.textContent = "Открываем…";
  const startedAt = performance.now();
  let result;
  try {
    result = await request();
  } catch (err) {
    chestRevealBusy = false;
    hideChestOverlay(overlay);
    throw err;
  }
  const reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const wait = (reduceMotion ? 250 : CHEST_SHAKE_MS) - (performance.now() - startedAt);
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));

  const items = chestPrizeItems(result?.reward || {});
  title.textContent = final ? "Весь приз твой!" : "Всё твоё!";
  prizes.innerHTML = items.join("");
  closeBtn.textContent = "Забрать";
  closeBtn.disabled = true;
  card.dataset.phase = "open";
  haptic("triple");
  playChestSound("open");

  let closed = false;
  const close = () => {
    if (closed || card.dataset.ready !== "1") return;
    closed = true;
    overlay.removeEventListener("click", onOverlayClick);
    document.removeEventListener("keydown", onKey);
    chestRevealBusy = false;
    hideChestOverlay(overlay);
    haptic("light");
    if (onDone) {
      try { onDone(result); } catch (err) { console.error("chest onDone", err); }
    }
  };
  function onOverlayClick(e) { if (e.target === overlay || e.target.closest("#chestClose")) close(); }
  function onKey(e) { if (e.key === "Escape" || e.key === "Enter") close(); }
  overlay.addEventListener("click", onOverlayClick);
  document.addEventListener("keydown", onKey);
  setTimeout(() => {
    card.dataset.ready = "1";
    closeBtn.disabled = false;
    try { closeBtn.focus({ preventScroll: true }); } catch (_) {}
  }, reduceMotion ? 300 : 450 + Math.max(items.length, 1) * 180 + 450);
  return result;
}

// Roadmap #12 — клик "Забрать" у выполненного квеста дня + открытие/
// закрытие модалки квестов (кнопка #dailyQuestsBtn в "Сегодня").
function initDailyQuestActions() {
  const list = document.getElementById("dailyQuestsList");
  if (!list) return;

  document.getElementById("monthCardOpen")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const at = Number(btn.dataset.chest);
    if (!at) return;
    const mq = state?.month_quests;
    const chest = (mq?.chests || []).find((c) => Number(c.at) === at);
    btn.disabled = true;
    try {
      await openChestReveal({
        eyebrow: chest?.final ? "ГЛАВНЫЙ СУНДУК МЕСЯЦА" : "СУНДУК МЕСЯЦА",
        caption: `${at} из ${mq?.goal || 60} заданий`,
        final: !!chest?.final,
        request: () => api("/api/month-quests/claim", { method: "POST", body: JSON.stringify({ at }) }),
        onDone: (result) => applyActionPatch(result),
      });
    } catch (err) {
      showToast(friendlyError(err), "error");
    } finally {
      btn.disabled = false;
    }
  });
  // Тап по невыполненному квесту ведёт к его следующему шагу: к привычкам, к плану дня или в чат с Адамом.
  list.addEventListener("click", (e) => {
    if (questTapIsStray()) return;
    const chest = e.target.closest("[data-chest]");
    if (chest) { openQuestChest(chest.dataset.chest, chest); return; }
    const row = e.target.closest("[data-go]");
    if (row) goToQuestStep(row.dataset.go);
  });
  list.addEventListener("keydown", (e) => {
    const row = e.target.closest("[data-go]");
    if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); goToQuestStep(row.dataset.go); }
  });
  document.getElementById("dailyQuestsBtn")?.addEventListener("click", openQuestsFromButton);
  document.getElementById("dailyQuestsClose")?.addEventListener("click", closeQuestsOverlay);
  document.getElementById("dailyQuestsBackdrop")?.addEventListener("click", () => { if (!questTapIsStray()) closeQuestsOverlay(); });
}

// ===================== КВЕСТЫ ДНЯ: три квеста и окно с анимацией (db/quests.py, п.14 владельца) =====================
// Идея — как ежедневные задания в Duolingo: человек пришёл отметить привычку и сразу видит следующий понятный шаг. После первой привычки окно
// само показывает, как полоска «Выполни 2 привычки» заполняется с 0/2 до 1/2; после второй — полоска доходит до конца, мягкая вибрация,
// появляется галочка, и заработанный балл перелетает в месячную полосу («Задания месяца»). Так же окно открывается, когда выполнен квест про
// план дня. Квест про Адама выполняется в чате: окно при следующем входе уже отмечает его, а в чате появляется тост. Награду сервер выдаёт сам
// (just_completed у квеста), окно только показывает изменение: сравнивает состояние до и после. Эффекты — transform/opacity/width и конечные
// анимации, без фильтров и вечных циклов. demo — сцена из админки (SIM_SCENES): ничего не читает из state и не пишет.
const QUEST_FILL_MS = 700;
let questsAnim = null;               // { timers, toMonth, demo } пока идёт показ изменения; renderDailyQuests тогда список не трогает
let questsWindowTimer = null;        // ждём, пока закроются другие окна (праздник серии, уровень и т.п.)
let questsPending = null;            // { before, monthBefore, after, monthAfter } — окно, которое ещё не открылось
let questsOverlayHideTimer = null;
const QUESTS_PLAN_DELAY_MS = 120000;  // окно про план — через две минуты после последней правки плана
let questsPlanHold = null;            // { before, monthBefore, after, monthAfter } — квест про план выполнен, окно ждёт
let questsPlanTimer = null;

function questPct(q, shown) {
  if (q.target <= 1) return shown.completed ? 100 : 0;
  return Math.min(100, Math.round(100 * shown.progress / q.target));
}

function questHint(q, shown, habitsCount, planReady) {
  if (shown.completed) return "";
  if (q.key === "two_habits") {
    if (habitsCount === 0) return "Для начала добавь первую и отметь её";
    if (habitsCount === 1) return "Добавь вторую — и полоска дойдёт до конца";
    return "";
  }
  if (q.key === "talk_adam") return planReady ? "Всё записано — попроси разложить дела по времени" : "Расскажи, как идёт день, — отвечу и помогу разобраться";
  return "";
}

// План дня заполнен (главная + хотя бы одна второстепенная): Адаму есть с чем работать — тап по его квесту сразу просит разложить дела по времени.
function questsPlanReady() {
  const plan = state?.daily_plan;
  return !!(plan && String(plan.main_goal || "").trim() && (plan.tasks || []).length);
}

// Сундук в конце полоски (как в Duolingo, у каждого квеста свой цвет: дерево / синий / золото). Форма — здесь, цвета — в CSS (.quest-chest--*).
// Три состояния: закрыт и тусклый (квест не выполнен) → готов, на него можно тапнуть (выполнен) → открыт, с галочкой.
function questChestSvg(open) {
  const body = '<rect class="qc-body qc-line" x="4" y="17" width="36" height="19" rx="4"/>';
  if (open) {
    return `<svg class="quest-chest__svg" viewBox="0 0 44 38" aria-hidden="true" focusable="false">${body}` +
      '<ellipse class="qc-glow" cx="22" cy="18" rx="15" ry="3"/>' +
      '<path class="qc-body qc-line" d="M7 15L10 3C17 0 27 0 34 3L37 15z"/>' +
      '<rect class="qc-band qc-line" x="12" y="17" width="5" height="19"/><rect class="qc-band qc-line" x="27" y="17" width="5" height="19"/></svg>';
  }
  return `<svg class="quest-chest__svg" viewBox="0 0 44 38" aria-hidden="true" focusable="false">${body}` +
    '<path class="qc-body qc-line" d="M4 19V14C4 7 12 3 22 3s18 4 18 11v5z"/>' +
    '<rect class="qc-band qc-line" x="12" y="4" width="5" height="32"/><rect class="qc-band qc-line" x="27" y="4" width="5" height="32"/>' +
    '<rect class="qc-lock qc-line" x="18" y="15" width="8" height="9" rx="2"/></svg>';
}

function questSideHtml(q, shown) {
  const kind = q.chest || shown.chest || "wood";
  if (!shown.completed) {
    const note = q.key === "talk_adam" ? "" : `<small class="daily-quest__progress">${shown.progress}/${q.target}</small>`;
    return `<span class="quest-chest quest-chest--${kind} is-locked" aria-hidden="true">${questChestSvg(false)}</span>${note}`;
  }
  if (shown.chest_opened) {
    return `<span class="quest-chest quest-chest--${kind} is-opened" role="img" aria-label="Сундук открыт">${questChestSvg(true)}<i class="quest-chest__tick">${ICON_CHECK}</i></span>`;
  }
  return `<button type="button" class="quest-chest quest-chest--${kind} is-ready" data-chest="${q.key}" aria-label="Открыть сундук: ${escapeHtml(q.title)}">${questChestSvg(false)}</button>`;
}

function questPartsHtml(q, shown) {
  if (!Array.isArray(q.parts)) return "";
  return `<span class="daily-quest__parts">${q.parts.map((part, i) => {
    const on = !!(shown.parts?.[i]?.done ?? part.done);
    return `<i class="daily-quest__part${on ? " is-on" : ""}">${escapeHtml(part.label)}</i>`;
  }).join("")}</span>`;
}

// q — квест (как сейчас), shown — то, что показываем в этот момент (при анимации — прошлое состояние).
function questRowHtml(q, shown, habitsCount, planReady) {
  const hint = questHint(q, shown, habitsCount, planReady);
  const bar = q.key === "talk_adam" ? "" : `<span class="daily-quest__bar"><span class="daily-quest__bar-fill" style="width:${questPct(q, shown)}%"></span></span>`;
  return `
    <li class="daily-quest${shown.completed ? " is-claimed" : ""}" data-quest="${q.key}"${shown.completed ? "" : ` data-go="${q.key}" role="button" tabindex="0"`}>
      <span class="daily-quest__emoji">${q.emoji}</span>
      <span class="daily-quest__body">
        <span class="daily-quest__title">${escapeHtml(q.title)}</span>
        ${questPartsHtml(q, shown)}
        ${hint ? `<small class="daily-quest__hint">${escapeHtml(hint)}</small>` : ""}
        ${bar}
      </span>
      <span class="daily-quest__side">${questSideHtml(q, shown)}</span>
    </li>`;
}

// Что изменилось между двумя состояниями квестов: habitsMoved — прибавилось по «2 привычки»; done — ключи квестов, ставших выполненными.
function diffQuests(before, after) {
  const prev = new Map((before || []).map((q) => [q.key, q]));
  let habitsMoved = false;
  const done = [];
  for (const q of after || []) {
    const p = prev.get(q.key);
    if (!p) continue;
    if (q.key === "two_habits" && q.progress > p.progress) habitsMoved = true;
    if (q.completed && !p.completed) done.push(q.key);
  }
  return { habitsMoved, done };
}

// После действия (отметка привычки, сохранение плана): если для квестов что-то сдвинулось — показать окно. Просто частичный прогресс плана
// (сохранена только главная задача) окно не открывает: лишний шум.
function announceQuestChange(before, monthBefore) {
  if (!before || labMode || simMode) return;
  const after = state?.daily_quests;
  const diff = diffQuests(before, after);
  if (!diff.habitsMoved && !diff.done.length) return;
  const waiting = questsPlanHold;
  // Выполнился ТОЛЬКО квест про план: человек ещё может записывать задачи, не перебиваем — окно через две минуты после последней правки.
  if (!diff.habitsMoved && diff.done.length === 1 && diff.done[0] === "plan_day") {
    questsPlanHold = { before: waiting?.before || before, monthBefore: waiting?.monthBefore || monthBefore, after, monthAfter: state?.month_quests || null };
    restartPlanHold();
    return;
  }
  // Всё остальное — сразу; если квест про план ждал своих двух минут, показываем его в этом же окне, а таймер снимаем.
  const payload = {
    before: questsPending?.before || waiting?.before || before,
    monthBefore: questsPending?.monthBefore || waiting?.monthBefore || monthBefore,
    after, monthAfter: state?.month_quests || null,
  };
  clearPlanHold();
  questsPending = payload;
  scheduleQuestsWindow(payload);
}

// Квест «Заполни план дня» уже выполнен и сохранён (награда и балл у человека, даже если он закроет приложение), а окно с этим откладывается:
// две минуты от ПОСЛЕДНЕЙ правки плана (каждая новая правка отсчитывает заново); откроет квесты сам раньше — покажем результат сразу.
function restartPlanHold() {
  clearTimeout(questsPlanTimer);
  questsPlanTimer = setTimeout(firePlanHold, QUESTS_PLAN_DELAY_MS);
}

function clearPlanHold() {
  clearTimeout(questsPlanTimer);
  questsPlanTimer = null;
  questsPlanHold = null;
}

function firePlanHold() {
  const held = questsPlanHold;
  clearPlanHold();
  if (!held) return;
  const payload = { ...held, after: state?.daily_quests || held.after, monthAfter: state?.month_quests || held.monthAfter };
  questsPending = payload;
  scheduleQuestsWindow(payload);
}

// Любая правка плана (главная задача, добавить/изменить/удалить задачу): если окно ждёт, отсчёт идёт заново.
function notePlanEdited() {
  if (questsPlanHold) restartPlanHold();
}

// Кнопка «Квесты» в «Сегодня»: если окно про план ещё ждёт своих двух минут — показываем результат сразу, второго окна по таймеру уже не будет.
function openQuestsFromButton() {
  trk("quests_window_open");
  const held = questsPlanHold;
  if (held) {
    clearPlanHold();
    openQuestsWindow({ ...held, after: state?.daily_quests || held.after, monthAfter: state?.month_quests || held.monthAfter });
    return;
  }
  // Окно после отметки привычек ещё ждёт своей очереди (идёт праздник серии и т.п.), а человек открыл квесты сам: показываем изменение
  // прямо в этом окне. Иначе оно вдруг откроется второй раз позже — с анимацией «балл летит в месяц» поверх уже закрытого экрана.
  if (questsPending) {
    const ready = questsPending;
    questsPending = null;
    clearInterval(questsWindowTimer);
    questsWindowTimer = null;
    openQuestsWindow({ ...ready, after: state?.daily_quests || ready.after, monthAfter: state?.month_quests || ready.monthAfter });
    return;
  }
  openQuestsOverlay();
}

// Первый заход за сессию: что выполнено без приложения (сообщение Адаму в боте и т.п.) — одним тостом, окно не открываем.
function announceCaughtUpQuests(quests) {
  const fresh = (quests || []).filter((q) => q.just_completed);
  if (!fresh.length || labMode || simMode) return;
  const coins = fresh.reduce((sum, q) => sum + (Number(q.reward) || 0), 0);
  const text = fresh.length === 1 ? `Квест «${fresh[0].title}» выполнен` : "Квесты дня выполнены";
  setTimeout(() => showToast(`🗺️ ${text} · +${coins} Adam Coin`, "praise", 3800), 1800);
}

function scheduleQuestsWindow(payload) {
  const overlay = document.getElementById("dailyQuestsOverlay");
  if (overlay && !overlay.hidden && !questsAnim) {
    questsPending = null;
    openQuestsWindow(payload);                      // окно уже открыто — просто показываем изменение прямо в нём
    return;
  }
  clearInterval(questsWindowTimer);
  const startedAt = Date.now();
  let quietTicks = 0;
  questsWindowTimer = setInterval(() => {
    if (Date.now() - startedAt > 60000) {
      clearInterval(questsWindowTimer);
      questsWindowTimer = null;
      questsPending = null;
      return;
    }
    if (celebrationOverlayOpen() || document.hidden) { quietTicks = 0; return; }
    quietTicks += 1;
    if (quietTicks < 2) return;
    clearInterval(questsWindowTimer);
    questsWindowTimer = null;
    const ready = questsPending;
    questsPending = null;
    if (!ready) return;
    trk("quests_window_auto");
    openQuestsWindow(ready);
  }, 400);
}

function openQuestsOverlay() {
  const overlay = document.getElementById("dailyQuestsOverlay");
  if (!overlay) return;
  clearTimeout(questsOverlayHideTimer);
  overlay.hidden = false;
  void overlay.offsetWidth;                  // перерасчёт стилей до класса: переход входа проигрывается и без requestAnimationFrame (в фоне он не идёт)
  overlay.classList.add("is-open");
  overlay.setAttribute("aria-hidden", "false");
  haptic("light");
}

function closeQuestsOverlay() {
  const overlay = document.getElementById("dailyQuestsOverlay");
  if (!overlay) return;
  const demo = !!questsAnim?.demo;
  finishQuestsAnim();
  haptic("light");
  overlay.classList.remove("is-open");
  overlay.setAttribute("aria-hidden", "true");
  // Держим overlay в DOM до конца transition закрытия; новое открытие эту отсрочку отменяет (clearTimeout в openQuestsOverlay).
  clearTimeout(questsOverlayHideTimer);
  questsOverlayHideTimer = setTimeout(() => { overlay.hidden = true; }, 430);
  if (demo) renderDailyQuests();
}

function finishQuestsAnim() {
  if (!questsAnim) return;
  questsAnim.timers.forEach(clearTimeout);
  const { toMonth, demo } = questsAnim;
  questsAnim = null;
  document.querySelectorAll(".quest-point, .month-card__plus").forEach((el) => el.remove());
  if (toMonth) renderMonthCard(toMonth);
  if (!demo) renderDailyQuests();
}

// Показать окно «Квесты дня». before/monthBefore — состояние ДО (с него полоски начинают заполняться), after/monthAfter — ПОСЛЕ (по умолчанию —
// текущее из state). Без before окно просто открывается с текущим состоянием.
function openQuestsWindow({ before = null, monthBefore = null, after = null, monthAfter = null, habitsCount = null, planReady = null, demo = false } = {}) {
  const overlay = document.getElementById("dailyQuestsOverlay");
  const list = document.getElementById("dailyQuestsList");
  if (!overlay || !list) return false;
  const toQuests = after || state?.daily_quests || [];
  const toMonth = monthAfter || state?.month_quests || null;
  const habits = habitsCount === null ? (state?.habits || []).length : habitsCount;
  const ready = planReady === null ? questsPlanReady() : planReady;
  finishQuestsAnim();
  hideProductHint();
  const fromByKey = new Map((before || []).map((q) => [q.key, q]));
  questsAnim = { timers: [], toMonth, demo };
  list.innerHTML = toQuests.map((q) => questRowHtml(q, fromByKey.get(q.key) || q, habits, ready)).join("");
  renderMonthCard(monthBefore || toMonth);
  updateQuestChestHint();
  openQuestsOverlay();
  questsAnim.timers.push(setTimeout(inviteQuestChests, 900));
  questsAnim.timers.push(setTimeout(() => playQuestsChange(toQuests, fromByKey, monthBefore, toMonth, habits), overlay.classList.contains("is-open") ? 250 : 650));
  return true;
}

function playQuestsChange(toQuests, fromByKey, monthBefore, toMonth, habits) {
  const anim = questsAnim;
  const list = document.getElementById("dailyQuestsList");
  if (!anim || !list) return;
  let moved = 0;
  let finishAt = 600;
  toQuests.forEach((q) => {
    const from = fromByKey.get(q.key) || q;
    const li = list.querySelector(`[data-quest="${q.key}"]`);
    const changed = q.progress !== from.progress || q.completed !== from.completed;
    if (!li || !changed) return;
    const fill = li.querySelector(".daily-quest__bar-fill");
    if (fill) fill.style.width = `${questPct(q, q)}%`;
    (q.parts || []).forEach((part, i) => li.querySelectorAll(".daily-quest__part")[i]?.classList.toggle("is-on", !!part.done));
    const progress = li.querySelector(".daily-quest__progress");
    if (progress) progress.textContent = `${q.progress}/${q.target}`;
    if (q.completed && !from.completed) {
      const delay = QUEST_FILL_MS + 120 + moved * 900;
      anim.timers.push(setTimeout(() => completeQuestRow(li, q, monthBefore, toMonth), delay));
      finishAt = Math.max(finishAt, delay + 2600);        // галочка → полёт балла (≈0,8 с) → «+1» у счёта (1,5 с)
    } else {
      finishAt = Math.max(finishAt, QUEST_FILL_MS + 200);
    }
    moved += 1;
  });
  if (moved) haptic("light");
  anim.timers.push(setTimeout(() => { if (questsAnim === anim) finishQuestsAnim(); }, finishAt));
}

// Квест закрылся: галочка выскакивает, вибрация, балл летит в месячную полосу.
function completeQuestRow(li, q, monthBefore, toMonth) {
  if (!questsAnim) return;
  li.classList.add("is-claimed");
  li.removeAttribute("data-go");
  li.removeAttribute("role");
  li.removeAttribute("tabindex");
  li.querySelector(".daily-quest__hint")?.remove();
  const side = li.querySelector(".daily-quest__side");
  if (side) side.innerHTML = questSideHtml(q, { ...q, completed: true, chest_opened: false }).replace("is-ready", "is-ready is-pop");
  const check = side?.querySelector(".quest-chest");
  void check?.offsetWidth;
  check?.classList.remove("is-pop");
  inviteQuestChests();
  updateQuestChestHint();
  haptic("success");
  const bar = document.querySelector("#monthCardTrack .month-card__bar-fill");
  const anim = questsAnim;
  flyQuestPoint(check || li, bar, () => {
    if (questsAnim !== anim) return;
    if (bar && toMonth) {
      bar.style.width = `${Math.min(100, Math.round(100 * toMonth.points / toMonth.goal))}%`;
      const count = document.getElementById("monthCardCount");
      if (count) {
        count.textContent = `${Math.min(toMonth.points, toMonth.goal)} / ${toMonth.goal}`;
        count.classList.add("is-bump");
        anim.timers.push(setTimeout(() => count.classList.remove("is-bump"), 420));
        // «+1» всплывает у счёта: видно, какое именно действие продвинуло шкалу
        const head = count.parentElement;
        const plus = document.createElement("i");
        plus.className = "month-card__plus";
        plus.textContent = "+1";
        head?.appendChild(plus);
        void plus.offsetWidth;
        plus.classList.add("is-on");
        anim.timers.push(setTimeout(() => plus.remove(), 1500));
      }
      haptic("light");
    }
  });
}

// Балл «+1» вылетает из галочки квеста и долетает до конца месячной полосы. Web Animations — конечная, только transform/opacity.
function flyQuestPoint(fromEl, toEl, onArrive) {
  if (!fromEl || !toEl || typeof fromEl.animate !== "function") { onArrive(); return; }
  const a = fromEl.getBoundingClientRect();
  const b = toEl.getBoundingClientRect();
  const dot = document.createElement("i");
  dot.className = "quest-point";
  dot.textContent = "+1";
  dot.style.left = `${a.left + a.width / 2}px`;
  dot.style.top = `${a.top + a.height / 2}px`;
  document.body.appendChild(dot);
  const dx = b.left + b.width - (a.left + a.width / 2);
  const dy = b.top + b.height / 2 - (a.top + a.height / 2);
  const flight = dot.animate(
    [
      { transform: "translate(-50%, -50%) scale(.6)", opacity: 0 },
      { transform: "translate(-50%, -50%) scale(1.15)", opacity: 1, offset: 0.18 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(.7)`, opacity: 0.95 },
    ],
    { duration: 780, easing: "cubic-bezier(.4,0,.2,1)", fill: "forwards" },
  );
  // Страховка: если у WebView анимация не идёт (фон, экономия энергии), балл всё равно «долетает» по таймеру — счёт и «+1» не теряются.
  let landed = false;
  const land = () => {
    if (landed) return;
    landed = true;
    dot.remove();
    onArrive();
  };
  flight.onfinish = land;
  flight.oncancel = () => { landed = true; dot.remove(); };
  setTimeout(land, 780 + 250);
}

// Готовые сундуки трижды подпрыгивают — «тапни меня». Конечная Web Animation (transform), без вечных циклов.
function inviteQuestChests() {
  document.querySelectorAll("#dailyQuestsList .quest-chest.is-ready").forEach((el, i) => {
    if (typeof el.animate !== "function") return;
    el.animate(
      [{ transform: "translateY(0) scale(1)" }, { transform: "translateY(-5px) scale(1.1)" }, { transform: "translateY(0) scale(1)" }],
      { duration: 560, iterations: 3, delay: i * 140, easing: "ease-in-out" },
    );
  });
}

// Строка под заголовком окна: есть хотя бы один готовый сундук — зовём его открыть.
function updateQuestChestHint() {
  const hint = document.getElementById("dailyQuestsChestHint");
  if (hint) hint.hidden = !document.querySelector("#dailyQuestsList .quest-chest.is-ready");
}

// Тап по готовому сундуку квеста: три тапа с вибрацией, потом награда. Награду (+Adam Coin и очко месяца) сервер выдал ещё при выполнении квеста,
// сундук её показывает и запоминает «открыт» (db/quests.py::open_quest_chest). «Не сейчас» оставляет сундук ждать. Сцена из админки ничего не пишет.
let questChestBusy = false;
async function openQuestChest(key, btn) {
  if (questChestBusy || !btn) return;
  const li = btn.closest(".daily-quest");
  const title = li?.querySelector(".daily-quest__title")?.textContent?.trim() || "";
  const kind = ["wood", "blue", "gold"].find((name) => btn.classList.contains(`quest-chest--${name}`)) || "wood";
  const sim = !!simMode;
  questChestBusy = true;
  btn.disabled = true;
  try {
    const result = await openChestReveal({
      eyebrow: "ТВОЙ СУНДУК", caption: title, tone: kind,
      request: sim ? simReply({ reward: { coins: 5, month_points: 1 } }) : () => api(`/api/quests/${encodeURIComponent(key)}/open`, { method: "POST" }),
      onDone: (res) => {
        if (sim) {
          const side = li?.querySelector(".daily-quest__side");
          if (side) side.innerHTML = questSideHtml({ key, title, chest: kind }, { completed: true, chest_opened: true, chest: kind });
          updateQuestChestHint();
        } else {
          applyActionPatch(res);
          updateQuestChestHint();
        }
      },
    });
    if (result === null) btn.disabled = false;
  } catch (err) {
    btn.disabled = false;
    showToast(friendlyError(err), "error");
  } finally {
    questChestBusy = false;
  }
}

// Тап по невыполненному квесту — к его следующему шагу.
function goToQuestStep(key) {
  if (questsAnim?.demo) return;
  trk("quests_go", key);
  closeQuestsOverlay();
  setTimeout(() => {
    if (key === "talk_adam") {
      if (questsPlanReady()) window.location.href = "/coach?intro=plans&t=schedule";   // план записан — Адам сразу раскладывает дела по времени
      else document.getElementById("aiCoachBtn")?.click();
      return;
    }
    if (key === "plan_day") { guideToPlanStep(); return; }
    if ((state?.habits || []).length < 2) openAddCollapse("addHabitCollapse");
    document.getElementById("habitList")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 460);
}

// Квест «Заполни план дня»: куда вести, зависит от того, что уже записано. Нет главной — к её полю (mainGoalInput). Главная есть, а второстепенной
// нет — к кнопке «Добавить задачу», она мягко подсвечивается. Раньше вели всегда к полю главной, но после сохранения оно скрыто: второй тап
// по сундуку квеста просто возвращал на Главную и никуда не прокручивал.
function planQuestStep() {
  const plan = state?.daily_plan;
  if (!String(plan?.main_goal || "").trim()) return "main";
  return (plan?.tasks || []).length ? "list" : "task";
}

let planHintTimer = 0;
function guideToPlanStep({ demo = false } = {}) {
  const step = demo ? "task" : planQuestStep();
  if (step === "task") {
    if (demo) closeAddCollapse("addPlanTaskCollapse");        // в симуляции форма может быть раскрыта с прошлого раза — тогда кнопки не видно
    const form = document.getElementById("addPlanTaskForm");
    if (form && !form.hidden) {                                // форма уже раскрыта — сразу к полю, подсвечивать нечего
      const input = document.getElementById("newPlanTaskInput");
      input?.scrollIntoView({ behavior: "smooth", block: "center" });
      try { input?.focus({ preventScroll: true }); } catch (_) { /* без фокуса достаточно прокрутки */ }
      return;
    }
    const trigger = document.getElementById("addPlanTaskTrigger");
    if (!trigger) return;
    trigger.scrollIntoView({ behavior: "smooth", block: "center" });
    clearTimeout(planHintTimer);
    planHintTimer = setTimeout(() => {                         // кольцо — когда прокрутка уже дошла
      trigger.classList.remove("is-hinted");
      void trigger.offsetWidth;                                // повторный тап запускает подсветку заново
      trigger.classList.add("is-hinted");
      planHintTimer = setTimeout(() => trigger.classList.remove("is-hinted"), PLAN_HINT_MS);
    }, 320);
    return;
  }
  const target = step === "main" ? document.getElementById("mainGoalInput") : document.getElementById("planList");
  target?.scrollIntoView({ behavior: "smooth", block: "center" });
  try { target?.focus({ preventScroll: true }); } catch (_) { /* без фокуса достаточно прокрутки */ }
}
const PLAN_HINT_MS = 2600;                                     // два кольца по 1,1 с (style.css::planTaskHint) и немного тишины после

function initHabitActions() {
  const habitList = document.getElementById("habitList");
  const addHabitForm = document.getElementById("addHabitForm");
  if (!habitList || !addHabitForm) return;

  const addHabitTrigger = document.getElementById("addHabitTrigger");
  if (addHabitTrigger) {
    addHabitTrigger.addEventListener("click", () => openAddCollapse("addHabitCollapse"));
  }

  const priorityBtn = document.getElementById("newHabitPriorityBtn");
  if (priorityBtn) {
    priorityBtn.addEventListener("click", () => {
      haptic("light");
      const pressed = priorityBtn.getAttribute("aria-pressed") === "true";
      priorityBtn.setAttribute("aria-pressed", pressed ? "false" : "true");
      // Фон кнопки принудительно перекрашен сайтовым правилом для <button>
      // внутри .add-form (см. style.css) — единственный надёжный визуальный
      // сигнал состояния независимо от фона — закрашенная/контурная звезда.
      const icon = document.getElementById("newHabitPriorityIcon");
      if (icon) icon.textContent = pressed ? "☆" : "⭐";
    });
  }

  // Roadmap #1/#2/#7 — свёрнутая по умолчанию секция "Ещё настройки" в
  // форме добавления привычки: держим быстрое добавление быстрым, а
  // счётчик/периодичность/цепочку показываем только по явному запросу.
  const advToggle = document.getElementById("newHabitAdvancedToggle");
  const advPanel = document.getElementById("newHabitAdvanced");
  if (advToggle && advPanel) {
    advToggle.addEventListener("click", () => { haptic("light"); advPanel.hidden = !advPanel.hidden; });
  }
  const filterRow = document.getElementById("habitFilterRow");
  if (filterRow) {
    filterRow.addEventListener("click", (e) => {
      const chip = e.target.closest(".habit-filter-chip");
      if (!chip) return;
      haptic("light");
      activeHabitFilter = chip.dataset.category || "";
      renderHabits();
    });
  }

  habitList.addEventListener("click", async (e) => {
    // Чип готового шаблона привычки (только в пустом состоянии) — сразу
    // отправляем как обычное создание, без набора текста руками.
    const templateChip = e.target.closest("[data-template]");
    if (templateChip) {
      const input = document.getElementById("newHabitInput");
      if (input) input.value = templateChip.dataset.template;
      addHabitForm.requestSubmit ? addHabitForm.requestSubmit() : addHabitForm.dispatchEvent(new Event("submit", { cancelable: true }));
      return;
    }

    // Готовая программа (roadmap #38) — несколько привычек одним тапом.
    // Шлём по одной последовательно (тот же /api/habits, что и обычное
    // добавление) — если где-то в процессе упрёмся в лимит 10 привычек,
    // молча останавливаемся на том, что успело добавиться.
    const programCard = e.target.closest("[data-program]");
    if (programCard) {
      const program = HABIT_PROGRAMS[Number(programCard.dataset.program)];
      if (!program) return;
      programCard.disabled = true;
      let added = 0;
      for (const title of program.habits) {
        try {
          await api("/api/habits", { method: "POST", body: JSON.stringify({ title }) });
          added++;
        } catch (err) {
          break;
        }
      }
      haptic("light");
      showToast(added ? `Добавлено привычек: ${added}` : "Не получилось добавить программу", added ? "success" : "error");
      await loadBootstrap();
      return;
    }

    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const li = btn.closest(".habit-item");
    if (!li) return;
    // Числом, а не строкой — h.id из API тоже число, и pendingDeleteHabitIds/
    // expandedHabitActionIds (Set) сравнивают по строгому равенству: строка
    // "5" никогда не совпадёт с числом 5, и .has() молча всегда возвращал бы
    // false. Раньше это тихо ломало мгновенное скрытие карточки при удалении
    // (сама привычка пропадала только после loadBootstrap(), а не сразу).
    const habitId = Number(li.dataset.id);
    const action = btn.dataset.action;

    if (action === "toggle-actions") {
      if (expandedHabitActionIds.has(habitId)) collapseHabitActions([habitId], "arrow");
      else openHabitActions(habitId);
      return;
    }

    if (action === "edit") {
      openHabitEdit(habitId);
      return;
    }

    if (action === "edit-time") {
      // Превращаем чип "⏰ HH:MM" во встроенный нативный time-picker —
      // без модалок, изменение сохраняется сразу по выбору времени.
      const current = btn.dataset.time || "";
      const timeInput = document.createElement("input");
      timeInput.type = "time";
      timeInput.className = "habit-item__time-input";
      timeInput.value = current;
      btn.replaceWith(timeInput);
      timeInput.focus();
      try { timeInput.showPicker && timeInput.showPicker(); } catch (_) {}
      let committed = false;
      const commit = async () => {
        if (committed) return;
        committed = true;
        const newTime = timeInput.value || "";
        const titleEl = li.querySelector(".habit-item__title");
        try {
          await api(`/api/habits/${habitId}`, {
            method: "PUT",
            body: JSON.stringify({ title: titleEl ? titleEl.textContent : "", planned_time: newTime }),
          });
          haptic("light");
          showToast(newTime ? `Напоминание в ${newTime}` : "Напоминание убрано", "success");
        } catch (err) {
          showToast(friendlyError(err), "error");
        }
        await loadBootstrap();
      };
      timeInput.addEventListener("change", commit);
      timeInput.addEventListener("blur", () => {
        // Пикер закрыли без выбора — просто вернуть чип на место.
        setTimeout(() => { if (!committed && document.body.contains(timeInput)) renderHabits(); }, 150);
      });
      return;
    }

    if (action === "dismiss-suggested-time") {
      dismissHabitTimeSuggestion(habitId);
      return;
    }

    if (action === "accept-suggested-time") {
      const titleEl = li.querySelector(".habit-item__title");
      try {
        await api(`/api/habits/${habitId}`, {
          method: "PUT",
          body: JSON.stringify({ title: titleEl ? titleEl.textContent : "", planned_time: btn.dataset.time }),
        });
        haptic("light");
        showToast(`Напоминание в ${btn.dataset.time}`, "success");
        await loadBootstrap();
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
      return;
    }

    if (action === "undo-hint") {
      habitDoneCheckTap(habitId);
      return;
    }

    // Жалоба пользователя: отклик на выполнение привычки приходит с задержкой
    // около секунды. Раньше вибрация и галочка появлялись только ПОСЛЕ ответа
    // сервера (а он в другом полушарии: сеть туда-обратно + обработка) — тап
    // ощущался "мёртвым". Теперь отвечаем на касание сразу, оптимистично, а
    // ответ сервера лишь уточняет итог (монеты, серия, квесты). Если запрос
    // упал — откатываем вид обратно, чтобы не врать пользователю.
    let rollbackOptimistic = null;
    try {
      if (action === "complete") {
        btn.disabled = true;
        haptic("medium");
        rollbackOptimistic = applyOptimisticHabitTap(li, btn);
        const result = await api(`/api/habits/${habitId}/complete`, { method: "POST" });
        applyActionPatch(result);
        await celebrateHabitCompletion(result);
        scheduleTrialOffer("habit");
      } else if (action === "progress") {
        btn.disabled = true;
        haptic("light");
        rollbackOptimistic = applyOptimisticHabitTap(li, btn);
        // Roadmap #1 — счётчик: +1 к прогрессу. Если это нажатие как раз
        // закрыло цель, ответ содержит те же поля, что и /complete (монеты,
        // streak и т.д.) — празднуем точно так же.
        const result = await api(`/api/habits/${habitId}/progress`, { method: "POST" });
        if (result.just_completed) haptic("medium");
        applyActionPatch(result);
        if (result.just_completed) {
          await celebrateHabitCompletion(result);
          scheduleTrialOffer("habit");
        } else {
          showToast(`${result.progress_count}/${result.target_count}`, "success");
        }
      } else if (action === "note") {
        openHabitNotePrompt(habitId);
      } else if (action === "delete") {
        // Первый тап по ✕ только спрашивает («Удалить?»), удаляет второй; «wait» — торопливый двойной тап, его не считаем.
        const step = habitDeleteStep(habitId);
        if (step === "arm") armHabitDelete(habitId, btn);
        else if (step === "confirm") { disarmHabitDelete(); deleteHabitWithUndo(habitId); }
      }
    } catch (err) {
      if (rollbackOptimistic) rollbackOptimistic();
      showToast(friendlyError(err), "error");
      await loadBootstrap();
    }
  });

  // Полный текст привычки: двойное нажатие/касание по самой карточке
  // раскрывает название. Интерактивные кнопки внутри карточки не участвуют,
  // чтобы двойной тап по чекбоксу, удалению и т.п. никогда не выполнял
  // действие повторно.
  let lastHabitTextTap = { id: null, time: 0 };
  const toggleHabitFullText = (li) => {
    const habitId = Number(li?.dataset.id);
    if (!habitId) return;
    if (expandedHabitTextIds.has(habitId)) {
      expandedHabitTextIds.delete(habitId);
    } else {
      expandedHabitTextIds.add(habitId);
    }
    renderHabits();
    haptic("light");
  };

  // Двойной тап по привычке (23.10, п.4): у привычки с раскрытыми кнопками — по любому месту строки, кроме самих кнопок, — прячет их;
  // у свёрнутой — по названию — раскрывает полный текст, как раньше. Двойной тап по кнопкам (✓, ✕, ✎…) ничего не делает.
  const isHabitControl = (el) => !!el.closest("button, input, select, textarea, a, label");
  habitList.addEventListener("dblclick", (e) => {
    if (!(e.target instanceof Element) || isHabitControl(e.target)) return;
    const li = e.target.closest(".habit-item");
    if (!li || !habitList.contains(li)) return;
    if (Date.now() - habitCollapsedAt < 600) { e.preventDefault(); return; }   // кнопки только что спрятал двойной тап, второго действия не нужно
    if (expandedHabitActionIds.has(Number(li.dataset.id))) {
      e.preventDefault();
      collapseHabitActions([Number(li.dataset.id)], "double");
      return;
    }
    if (!e.target.closest(".habit-item__title")) return;
    e.preventDefault();
    toggleHabitFullText(li);
  });

  // На телефонах dblclick может не срабатывать одинаково во всех WebView,
  // поэтому отдельно поддерживаем двойное касание: тот же ряд второй раз за 420 мс.
  habitList.addEventListener("touchend", (e) => {
    const target = e.target instanceof Element ? e.target : null;
    const li = target && !isHabitControl(target) ? target.closest(".habit-item") : null;
    const habitId = li ? Number(li.dataset.id) : 0;
    const reacts = !!habitId && (expandedHabitActionIds.has(habitId) || !!target.closest(".habit-item__title"));
    if (!li || !habitList.contains(li) || !reacts) {
      lastHabitTextTap = { id: null, time: 0 };
      return;
    }
    const now = Date.now();
    if (lastHabitTextTap.id === habitId && now - lastHabitTextTap.time < 420) {
      e.preventDefault();
      lastHabitTextTap = { id: null, time: 0 };
      if (expandedHabitActionIds.has(habitId)) collapseHabitActions([habitId], "double");
      else toggleHabitFullText(li);
    } else {
      lastHabitTextTap = { id: habitId, time: now };
    }
  }, { passive: false });

  // Тап мимо раскрытой привычки прячет её кнопки; нажатие на кнопки в окнах и шторках не в счёт (HABIT_TAP_IGNORE).
  document.addEventListener("click", (e) => { collapseOnOutsideTap(e.target instanceof Element ? e.target : null); });

  addHabitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("newHabitInput");
    const timeInput = document.getElementById("newHabitTime");
    const submitBtn = addHabitForm.querySelector('button[type="submit"]');
    const title = input.value.trim();
    if (title.length < 2) {
      showToast("Название слишком короткое", "error");
      input.focus();
      return;
    }
    const plannedTime = timeInput ? timeInput.value : "";
    const categorySelect = document.getElementById("newHabitCategory");
    const priorityBtn = document.getElementById("newHabitPriorityBtn");
    const category = categorySelect ? categorySelect.value : "";
    const priority = priorityBtn && priorityBtn.getAttribute("aria-pressed") === "true" ? 2 : 1;
    const chainSelect = document.getElementById("newHabitChainTrigger");
    const chainTriggerHabitId = chainSelect && chainSelect.value ? Number(chainSelect.value) : undefined;

    if (submitBtn) submitBtn.disabled = true;
    try {
      const result = await api("/api/habits", {
        method: "POST",
        body: JSON.stringify({
          title,
          planned_time: plannedTime || undefined,
          category: category || undefined,
          priority,
          chain_trigger_habit_id: chainTriggerHabitId,
        })
      });

      // Сразу показываем созданную привычку, не заставляя интерфейс ждать
      // повторной загрузки всего bootstrap-состояния.
      if (result && result.habit && state) {
        state.habits = [result.habit, ...(state.habits || [])];
        renderHabits();
      }

      input.value = "";
      if (timeInput) timeInput.value = "";
      if (categorySelect) categorySelect.value = "";
      if (priorityBtn) {
        priorityBtn.setAttribute("aria-pressed", "false");
        const icon = document.getElementById("newHabitPriorityIcon");
        if (icon) icon.textContent = "☆";
      }
      if (chainSelect) chainSelect.value = "";
      const advPanelAfterSubmit = document.getElementById("newHabitAdvanced");
      if (advPanelAfterSubmit) advPanelAfterSubmit.hidden = true;
      haptic("light");
      showToast("Привычка добавлена", "success");

      // Синхронизируем остальные данные (XP, серию, календарь и т.д.).
      try {
        await loadBootstrap();
      } catch (syncErr) {
        console.warn("Не удалось сразу синхронизировать bootstrap после добавления привычки", syncErr);
      }

      if (result.first_habit) {
        if (!maybeShowStreakOnboarding({ chain: true, then: onOnboardingHabitCreated })) onOnboardingHabitCreated();
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
      input.focus();
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

// ===================== DAILY PLAN =====================
function renderPlan() {
  const plan = state.daily_plan || { main_goal: "", main_goal_completed: false, tasks: [] };
  const list = document.getElementById("planList");
  const mainInput = document.getElementById("mainGoalInput");
  const mainEditor = document.getElementById("mainGoalEditor");
  const mainView = document.getElementById("mainGoalView");
  const mainConfirm = document.getElementById("saveMainGoalBtn");
  const addInput = document.getElementById("newPlanTaskInput");
  const addBtn = document.getElementById("addPlanTaskBtn");

  const done = (plan.tasks || []).filter(t => t.completed).length + (plan.main_goal && plan.main_goal_completed ? 1 : 0);
  const total = (plan.tasks || []).length + (plan.main_goal ? 1 : 0);
  document.getElementById("planProgressLabel").textContent = `${done}/${total}`;

  // Главная задача: режим ввода или режим отображения.
  if (plan.main_goal) {
    mainEditor.hidden = true;
    mainView.hidden = false;
    mainView.innerHTML = `
      <div class="plan-item plan-item--main ${plan.main_goal_completed ? "is-done" : ""}">
        <input type="checkbox" class="plan-toggle plan-toggle--main" data-main-toggle="1" ${plan.main_goal_completed ? "checked" : ""} aria-label="Отметить главную задачу выполненной">
        <span class="plan-item__text">${escapeHtml(plan.main_goal)}</span>
        <div class="plan-item__actions">
          <button type="button" class="plan-icon-btn" data-main-action="edit" aria-label="Редактировать">${ICON_EDIT}</button>
          <button type="button" class="plan-icon-btn plan-icon-btn--delete" data-main-action="delete" aria-label="Удалить">${ICON_X}</button>
        </div>
      </div>`;
    mainInput.value = "";
    mainConfirm.hidden = true;
  } else {
    mainEditor.hidden = false;
    mainView.hidden = true;
    mainInput.value = mainInput.dataset.editingValue || mainInput.value || "";
    mainConfirm.hidden = !mainInput.value.trim();
    delete mainInput.dataset.editingValue;
  }

  list.innerHTML = (plan.tasks || []).map(t => `
    <li class="plan-item ${t.completed ? "is-done" : ""}" data-id="${t.id}">
      <input type="checkbox" class="plan-toggle" data-id="${t.id}" ${t.completed ? "checked" : ""} aria-label="Отметить задачу выполненной">
      <span class="plan-item__text">${escapeHtml(t.text)}</span>
      <div class="plan-item__actions">
        <button type="button" class="plan-icon-btn" data-action="edit" aria-label="Редактировать">${ICON_EDIT}</button>
        <button type="button" class="plan-icon-btn plan-icon-btn--delete" data-action="delete" aria-label="Удалить">${ICON_X}</button>
      </div>
    </li>
  `).join("");

  const editingId = addInput.dataset.editingTaskId;
  if (editingId) {
    const task = plan.tasks.find(t => String(t.id) === String(editingId));
    if (!task) {
      delete addInput.dataset.editingTaskId;
      addInput.value = "";
      addBtn.textContent = "Добавить новую задачу";
    } else {
      addBtn.textContent = "✓ Сохранить изменения";
    }
  } else {
    addBtn.textContent = "Добавить новую задачу";
  }

  const MAX_DAILY_TASKS = 9;
  addBtn.disabled = plan.tasks.length >= MAX_DAILY_TASKS && !editingId;
  if (plan.tasks.length >= MAX_DAILY_TASKS && !editingId) {
    addBtn.textContent = `Максимум ${MAX_DAILY_TASKS} задач`;
  }
}

function startMainGoalEdit() {
  const plan = state.daily_plan || { main_goal: "" };
  const input = document.getElementById("mainGoalInput");
  const editor = document.getElementById("mainGoalEditor");
  const view = document.getElementById("mainGoalView");
  input.value = plan.main_goal || "";
  editor.hidden = false;
  view.hidden = true;
  document.getElementById("saveMainGoalBtn").hidden = !input.value.trim();
  input.focus();
  input.select();
}

function resetPlanTaskEditor() {
  const input = document.getElementById("newPlanTaskInput");
  const btn = document.getElementById("addPlanTaskBtn");
  delete input.dataset.editingTaskId;
  input.value = "";
  btn.textContent = "Добавить новую задачу";
  if (state?.daily_plan?.tasks) btn.disabled = state.daily_plan.tasks.length >= 9;
}

function initPlanActions() {
  const mainInput = document.getElementById("mainGoalInput");
  const mainConfirm = document.getElementById("saveMainGoalBtn");
  const mainView = document.getElementById("mainGoalView");
  const taskForm = document.getElementById("addPlanTaskForm");
  const taskInput = document.getElementById("newPlanTaskInput");

  const addPlanTaskTrigger = document.getElementById("addPlanTaskTrigger");
  if (addPlanTaskTrigger) {
    addPlanTaskTrigger.addEventListener("click", () => openAddCollapse("addPlanTaskCollapse"));
  }

  mainInput.addEventListener("input", () => {
    mainConfirm.hidden = !mainInput.value.trim();
  });

  mainInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && mainInput.value.trim()) {
      e.preventDefault();
      mainConfirm.click();
    }
  });

  mainConfirm.addEventListener("click", async () => {
    const text = mainInput.value.trim();
    if (!text) return;
    const isFirstMainGoal = !!state?.show_app_tour && !state?.daily_plan?.main_goal;
    try {
      await api("/api/plan/main/save", {
        method: "POST",
        body: JSON.stringify({ text })
      });
      delete mainInput.dataset.editingValue;
      haptic("light");
      await loadBootstrap();
      notePlanEdited();
      if (isFirstMainGoal) onOnboardingMainGoalSaved();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  mainView.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-main-action]");
    if (!btn) return;
    const action = btn.dataset.mainAction;
    try {
      if (action === "edit") {
        startMainGoalEdit();
      } else if (action === "delete") {
        await api("/api/plan/main", { method: "DELETE" });
        document.getElementById("mainGoalInput").value = "";
        haptic("light");
        await loadBootstrap();
        notePlanEdited();
        document.getElementById("mainGoalInput").focus();
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  taskForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = taskInput.value.trim();
    if (!text) {
      taskInput.focus();
      return;
    }
    const editingId = taskInput.dataset.editingTaskId;

    try {
      if (editingId) {
        // Редактирование уже существующей задачи не должно зависеть от
        // лимита в 5 второстепенных задач: лимит относится только к ДОБАВЛЕНИЮ.
        // Получаем свежий план прямо из ответа, чтобы при пяти заполненных
        // задачах не было гонки с /api/bootstrap и редактирование точно
        // отображалось после сохранения.
        const res = await api(`/api/plan/task/${editingId}`, {
          method: "PUT",
          body: JSON.stringify({ text })
        });
        showToast("Задача обновлена", "success");
        resetPlanTaskEditor();
        haptic("light");
        if (res && res.daily_plan) {
          applyPlanPatch(res);
        } else {
          await loadBootstrap();
        }
        notePlanEdited();
        return;
      }

      await api("/api/plan/task", {
        method: "POST",
        body: JSON.stringify({ text })
      });
      resetPlanTaskEditor();
      haptic("light");
      await loadBootstrap();
      notePlanEdited();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  document.getElementById("planList").addEventListener("click", async (e) => {
    const li = e.target.closest(".plan-item");
    if (!li) return;

    const actionBtn = e.target.closest("button[data-action]");
    if (actionBtn) {
      haptic("light");
      const taskId = li.dataset.id;
      try {
        if (actionBtn.dataset.action === "edit") {
          const task = (state.daily_plan?.tasks || []).find(t => String(t.id) === String(taskId));
          if (!task) return;
          // Форма добавления по умолчанию свёрнута за "+" — без этого
          // редактирование фокусировало бы скрытое поле и было бы
          // незаметно, что вообще что-то произошло.
          openAddCollapse("addPlanTaskCollapse");
          taskInput.value = task.text;
          taskInput.dataset.editingTaskId = task.id;
          const editBtn = document.getElementById("addPlanTaskBtn");
          editBtn.textContent = "✓ Сохранить изменения";
          editBtn.disabled = false;
          taskInput.focus();
          taskInput.select();
          taskInput.scrollIntoView({ behavior: "smooth", block: "center" });
        } else if (actionBtn.dataset.action === "delete") {
          await api(`/api/plan/task/${taskId}`, { method: "DELETE" });
          if (String(taskInput.dataset.editingTaskId) === String(taskId)) resetPlanTaskEditor();
          await loadBootstrap();
          notePlanEdited();
        }
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
    }
  });

  document.addEventListener("change", async (e) => {
    if (e.target.classList.contains("plan-toggle--main")) {
      const wasCompleting = e.target.checked;
      try {
        const res = await api("/api/plan/main/toggle", { method: "POST" });
        if (res && res.message) showToast(res.message, "praise", 4500);
        applyPlanPatch(res);
        if (wasCompleting && state?.show_app_tour) {
          onboardingMainGoalDoneOnce = true;
          maybeOfferAnotherHabit();
        }
      } catch (err) {
        showToast(friendlyError(err), "error");
        await loadBootstrap();
      }
    } else if (e.target.classList.contains("plan-toggle")) {
      try {
        const res = await api("/api/plan/task/toggle", {
          method: "POST",
          body: JSON.stringify({ task_id: e.target.dataset.id })
        });
        if (res && res.message) showToast(res.message, "praise", 4500);
        applyPlanPatch(res);
        scheduleTrialOffer("plan");
      } catch (err) {
        showToast(friendlyError(err), "error");
        await loadBootstrap();
      }
    }
  });
}

// ===================== SHOP ACTIONS =====================
  function initShopActions() {
    const list = document.getElementById("shopList");
    if (!list) return;
    list.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn || btn.disabled) return;
      const li = btn.closest(".shop-item");
      const itemId = li?.dataset.id;
      const action = btn.dataset.action;
      if (!itemId) return;
      try {
        btn.disabled = true;
        if (action === "stars") {
          const item = (state.shop_items || []).find(x => String(x.id) === String(itemId));
          const invoice = await api(`/api/shop/stars/${itemId}`, { method: "POST" });
          if (!tg?.openInvoice) throw new Error("telegram_payment_unavailable");
          tg.openInvoice(invoice.invoice_url, (status) => {
            if (status === "paid") {
              showToast(`${item?.name || "Покупка"} — оплата прошла!`, "praise", 3500);
              setTimeout(async () => { secondaryLoaded.delete("profile"); await loadBootstrap(); await loadBootstrapSecondary("profile"); }, 500);
            }
          });
          return;
        }
        if (action === "equip") {
          const item = (state.shop_items || []).find(x => String(x.id) === String(itemId));
          await api("/api/cosmetics/equip", { method: "POST", body: JSON.stringify({ frame_id: item?.payload }) });
          showToast("Рамка надета", "success");
        } else {
          await api(`/api/buy/${itemId}`, { method: "POST" });
          showToast("Покупка совершена!", "success");
        }
        haptic("medium");
        secondaryLoaded.delete("profile");
        await loadBootstrap();
        await loadBootstrapSecondary("profile");
      } catch (err) {
        btn.disabled = false;
        showToast(friendlyError(err), "error");
      }
    });
  }

  // ---- окна по центру экрана: общий показ/скрытие ----
  function openCenterModal(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    el.hidden = false;
    el.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => el.classList.add("is-open"));
    return el;
  }

  function closeCenterModal(id) {
    const el = document.getElementById(id);
    if (!el || el.hidden) return;
    el.classList.remove("is-open");
    el.setAttribute("aria-hidden", "true");
    blurActiveTextField();
    setTimeout(() => { if (!el.classList.contains("is-open")) el.hidden = true; }, 200);
  }

  function bindCenterModal(id, closeIds) {
    closeIds.forEach((closeId) => document.getElementById(closeId)?.addEventListener("click", () => closeCenterModal(id)));
  }

  // Тап по аватару в Профиле: окно смены фото (раньше кнопка «Изменить фото» жила в Настройках).
  function renderAvatarEditorPreview() {
    const box = document.getElementById("avatarEditPreview");
    if (!box || !state?.user) return;
    const u = state.user;
    const frame = String(u.frame_id || "default");
    const photo = String(u.avatar_id || "default").startsWith("upload:")
      ? `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(String(u.avatar_id).split(":")[1])}.jpg?v=${Date.now()}" alt="Аватар">`
      : escapeHtml((u.first_name ? u.first_name[0] : "A").toUpperCase());
    box.innerHTML = `<div class="streak-profile-avatar frame-${escapeHtml(frame)}">${photo}</div>`;
  }

  function initAvatarEditor() {
    const wrap = document.getElementById("profileAvatarWrap");
    const input = document.getElementById("profilePhotoInput");
    const pick = document.getElementById("avatarEditPick");
    if (!wrap || !input || !pick) return;
    const open = () => { haptic("light"); renderAvatarEditorPreview(); openCenterModal("avatarEditOverlay"); };
    wrap.addEventListener("click", open);
    wrap.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    bindCenterModal("avatarEditOverlay", ["avatarEditClose", "avatarEditCancel", "avatarEditBackdrop"]);
    pick.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) { showToast("Выбери JPG, PNG или WEBP", "error"); return; }
      if (file.size > 5 * 1024 * 1024) { showToast("Фото должно быть не больше 5 МБ", "error"); return; }
      try {
        pick.disabled = true;
        const form = new FormData();
        form.append("avatar", file, file.name);
        const res = await fetch("/api/profile/avatar", { method: "POST", headers: { "Authorization": "tma " + initData() }, body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.error || "upload_failed");
        state.user.avatar_id = data.avatar_id;
        renderProfileAvatarControls();
        renderAvatarEditorPreview();
        renderRating();
        haptic("medium");
        showToast("Аватар обновлён", "success");
        setTimeout(() => closeCenterModal("avatarEditOverlay"), 500);
      } catch (err) {
        showToast(friendlyError(err), "error");
      } finally { pick.disabled = false; input.value = ""; }
    });
  }

  // Тап по имени в верхней карточке: смена имени и ника в одном окне.
  function initIdentityEditor() {
    const trigger = document.getElementById("playerIdentityBtn");
    const nameInput = document.getElementById("identityNameInput");
    const handleInput = document.getElementById("identityHandleInput");
    const save = document.getElementById("identitySave");
    if (!trigger || !nameInput || !handleInput || !save) return;
    const nameError = document.getElementById("identityNameError");
    const handleError = document.getElementById("identityHandleError");
    const showError = (el, text) => { if (el) { el.textContent = text || ""; el.hidden = !text; } };
    const open = () => {
      haptic("light");
      nameInput.value = state?.user?.first_name || "";
      handleInput.value = state?.user?.handle || "";
      showError(nameError, "");
      showError(handleError, "");
      openCenterModal("identityOverlay");
    };
    trigger.addEventListener("click", open);
    trigger.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    bindCenterModal("identityOverlay", ["identityClose", "identityCancel", "identityBackdrop"]);

    save.addEventListener("click", async () => {
      const u = state?.user;
      if (!u) return;
      const name = nameInput.value.replace(/\s+/g, " ").trim();
      const handle = handleInput.value.trim().replace(/^@/, "");
      showError(nameError, "");
      showError(handleError, "");
      if (!name) { showError(nameError, "Введи имя"); return; }
      if (name.length > 30) { showError(nameError, friendlyError({ data: { error: "invalid_name" } })); return; }
      const nameChanged = name !== (u.first_name || "");
      const handleChanged = handle.toLowerCase() !== String(u.handle || "").toLowerCase();
      if (!nameChanged && !handleChanged) { closeCenterModal("identityOverlay"); return; }
      save.disabled = true;
      try {
        // Ник — первым: он может оказаться занят, и тогда ничего не поменялось.
        if (handleChanged) {
          const res = await api("/api/settings/handle", { method: "POST", body: JSON.stringify({ handle }) });
          u.handle = res.handle;
        }
        if (nameChanged) {
          const res = await api("/api/profile/name", { method: "POST", body: JSON.stringify({ name }) });
          u.first_name = res.name;
        }
        (state.leaderboard || []).forEach((row) => {
          if (Number(row.telegram_id) === Number(u.telegram_id)) { row.first_name = u.first_name; row.handle = u.handle; }
        });
        const settingsHandle = document.getElementById("userHandleInput");
        if (settingsHandle) settingsHandle.value = u.handle || "";
        renderPlayerCard();
        renderRating();
        haptic("medium");
        showToast("Сохранено", "success");
        closeCenterModal("identityOverlay");
      } catch (err) {
        const code = err?.data?.error;
        const text = friendlyError(err);
        if (code === "taken" || code === "invalid_format") showError(handleError, text);
        else if (code === "invalid_name") showError(nameError, text);
        else showToast(text, "error");
        if (handleChanged && code === "invalid_name") renderPlayerCard();   // ник уже сохранился
      } finally {
        save.disabled = false;
      }
    });
  }

  // Рамки аватарки теперь выбираются в ADAM Store (раньше — в Настройках).
  function initProfileAvatarActions() {
    initAvatarEditor();

    const picker = document.getElementById("profileFramePicker");
    picker?.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-frame-id]");
      if (!btn || btn.disabled) return;
      try {
        await api("/api/cosmetics/equip", { method: "POST", body: JSON.stringify({ frame_id: btn.dataset.frameId }) });
        state.user.frame_id = btn.dataset.frameId;
        renderProfileAvatarControls();
        renderRating();
        showToast("Рамка установлена", "success");
        haptic("light");
      } catch (err) { showToast(friendlyError(err), "error"); }
    });
  }

  // ===================== THEME ACTIONS =====================
  function initThemeActions() {
    const picker = document.getElementById("themePicker");
    if (!picker) return;
    picker.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-theme]");
      if (!btn || btn.disabled) return;
      const theme = btn.dataset.theme;
      try {
        await api("/api/settings/theme", {
          method: "POST",
          body: JSON.stringify({ theme }),
        });
        haptic("light");
        await loadBootstrap();
        // ВАЖНО: renderThemePicker() — единственное место, которое реально
        // ставит document.body[data-theme] (от него зависят все цвета темы
        // по всему бота). loadBootstrap() выше обновляет только state —
        // без этого вызова тема молча сохранялась на сервере, но на экране
        // ничего не менялось до следующей полной перезагрузки страницы.
        renderThemePicker();
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
    });
  }

  // Roadmap #46 — англ. локализация статичных ошибок, параллельно RU-карте
  // выше. Не тронуто: сообщения, которые генерирует сам AI (те уже
  // подстраиваются под язык через инструкцию в build_user_context, см.
  // webapp/services/ai_utils.py) и длинный хвост редко видимых строк —
  // честно, это не 100%-ный перевод всего приложения, а покрытие самых
  // частых экранов + системных сообщений об ошибках.
  const ERROR_MAP_EN = {
    title_too_short: "Title is too short",
    already_completed: "Already completed",
    not_enough_xp_or_not_found: "Not enough Adam Coin",
    not_found: "Not found",
    banned: "Access restricted",
    theme_not_owned: "Buy «Theme» in the shop first",
    use_stars_checkout: "This frame can only be bought with Telegram Stars",
    telegram_payment_unavailable: "Open the app inside Telegram to pay with Stars",
    frame_not_owned: "This frame isn't unlocked yet",
    avatar_too_large: "Photo must be under 5 MB",
    unsupported_image: "JPG, PNG and WEBP are supported",
    invalid_theme: "That theme doesn't exist",
    task_limit: "You can add up to 9 tasks",
    habit_limit: "You can add up to 10 habits",
    habit_too_fast: "Too fast — wait a minute",
    daily_limit_reached: "Already bought today — available again tomorrow",
    habit_add_locked: "You already logged and deleted a habit today — adding new ones reopens at midnight",
    invalid_init_data: "Telegram didn't pass auth data. Close the Mini App and reopen it.",
    request_failed: "Couldn't reach the server. Check your connection and try again.",
    not_admin: "Admins only",
    bot_unavailable: "The bot is temporarily unavailable, try again later",
    rate_limited: "Too many requests — wait a couple seconds and try again",
    already_reacted_today: "You already cheered this player today — try again tomorrow",
    invalid_reaction: "Couldn't send support",
    invalid_target: "Player not found",
    sender_not_done: "Log one of your habits first — then you can nudge friends",
    already_reminded: "You already nudged this friend today",
    already_done: "Your friend already checked in today",
    unavailable: "Can't send a reminder right now",
    not_friends: "This person is no longer your friend",
    delivery_failed: "Couldn't deliver the reminder — try again later",
    daily_limit: "No more gifts today — try again tomorrow",
    not_enough_diamonds: "Not enough diamonds",
    already_owned: "Your friend already has this",
    receiver_full: "Your friend already has the maximum number of freezes",
    receiver_limit: "Your friend already got a freeze today",
    helper_limit: "You've already saved someone's streak this month",
    cooldown: "This friend's streak was saved very recently",
    item_not_giftable: "This can't be sent as a gift",
    invalid_amount: "You can't gift that many diamonds",
    not_enough_coins: "Not enough Adam Coin",
    invalid_chest: "No such chest",
    not_reached: "The chest is still locked — complete more quests",
    already_claimed: "This chest is already open",
    self: "That's you 🙂",
    blocked: "You blocked this player — unblock first",
    limit: "You've reached the follow limit",
    invalid_reason: "Pick a reason for the report",
    already_reported: "You already reported this player today",
    invalid_kind: "Couldn't open the list",
    invalid_visibility: "Couldn't save the setting",
    invalid_format: "Only latin letters, digits and \"_\", 3 to 20 characters",
    taken: "This handle is already taken",
    invalid_name: "Name must be 1 to 30 characters",
    not_for_sale: "This reward is given by the administrator",
    not_enough_xp: "Not enough Adam Coin",
    habit_not_done: "This habit is not checked anymore",
    undo_too_late: "I can only undo today's check-ins",
    undo_limit: "I've already undone a few check-ins today — let's keep this one",
    undo_coins_spent: "The coins for this check-in are already spent, so it can't be undone",
  };

  function friendlyError(err) {
    const code = err && err.data && err.data.error;

    const map = {
        title_too_short: "Название слишком короткое",
        already_completed: "Уже выполнено",
        not_enough_xp_or_not_found: "Не хватает Adam Coin",
        not_found: "Не найдено",
        banned: "Доступ ограничен",
        theme_not_owned: "Сначала купи «Тема оформления» в магазине",
        use_stars_checkout: "Эту рамку можно купить только за Telegram Stars",
        telegram_payment_unavailable: "Открой приложение внутри Telegram, чтобы оплатить Stars",
        frame_not_owned: "Эта рамка ещё не открыта",
        avatar_too_large: "Фото должно быть не больше 5 МБ",
        unsupported_image: "Поддерживаются JPG, PNG и WEBP",
        not_available: "Уже недоступно — раз в месяц, и только пока свежо",
        invalid_theme: "Такой темы не существует",
        task_limit: "Можно добавить не больше 9 задач",
        habit_limit: "Можно добавить не больше 10 привычек",
        habit_too_fast: "Слишком быстро — подожди минуту и добавь остальное",
        daily_limit_reached: "Этот пакет уже куплен сегодня — доступен снова завтра",
        habit_add_locked: "Сегодня уже была отметка и удаление привычки — добавление новых открыто с 00:00",
        access_expired: "Тестовый доступ закончился. Напиши администратору — он продлит.",
        trial_expired: "Пробный период закончился",
        invalid_init_data: "Telegram не передал данные авторизации. Закрой Mini App и открой его снова.",
        request_failed: "Не удалось связаться с сервером. Проверь соединение и попробуй ещё раз.",
        not_admin: "Доступно только администраторам",
        bot_unavailable: "Сейчас не могу отправить — попробуй чуть позже",
        rate_limited: "Слишком много запросов подряд — подожди пару секунд и попробуй ещё раз",
        already_reacted_today: "Сегодня ты уже поддержал этого игрока — можно снова завтра",
        invalid_reaction: "Не получилось отправить поддержку",
        invalid_target: "Игрок не найден",
        sender_not_done: "Сначала отметь свою привычку — и сможешь напомнить друзьям",
        already_reminded: "Сегодня ты уже напоминал(а) этому другу",
        already_done: "Друг уже отметился сегодня",
        unavailable: "Сейчас напомнить не получится",
        not_friends: "Этот человек больше не в друзьях",
        delivery_failed: "Не получилось доставить напоминание — попробуй позже",
        daily_limit: "На сегодня подарки закончились — завтра снова можно",
        not_enough_diamonds: "Не хватает алмазов",
        already_owned: "У друга это уже есть",
        receiver_full: "У друга уже максимум заморозок",
        receiver_limit: "Сегодня другу уже подарили заморозку",
        helper_limit: "В этом месяце ты уже спасал(а) чью-то серию",
        cooldown: "Этого друга уже спасали совсем недавно",
        item_not_giftable: "Такой подарок отправить нельзя",
        invalid_amount: "Столько алмазов подарить нельзя",
        not_enough_coins: "Не хватает Adam Coin",
        invalid_chest: "Такого сундука нет",
        needs_habit: "Сначала добавь привычку — без неё вклада в задание не будет",
        busy: "Ты уже в парном задании — новое начнётся после него",
        partner_busy: "Этот друг уже в парном задании",
        partner_inactive: "Друг давно не отмечался — выбери того, кто в ритме",
        already_invited: "Ты уже позвал(а) друга — дождись ответа или отзови приглашение",
        invited_you: "Этот друг уже позвал тебя — прими приглашение на Главной",
        not_pending: "Это приглашение уже не действует",
        cannot_cancel: "Начавшееся задание можно покинуть, только если напарник 2 дня и больше не заходит",
        not_completed: "Задание ещё не выполнено",
        not_reached: "Сундук ещё закрыт — выполни больше заданий",
        already_claimed: "Этот сундук уже открыт",
        self: "Это ты сам(а) 🙂",
        blocked: "Ты заблокировал(а) этого игрока — сначала разблокируй",
        limit: "Достигнут предел подписок",
        invalid_reason: "Выбери, на что жалоба",
        already_reported: "Ты уже жаловался(ась) на этого игрока сегодня",
        invalid_kind: "Не получилось открыть список",
        invalid_visibility: "Не получилось сохранить настройку",
        invalid_format: "Только латиница, цифры и «_», от 3 до 20 символов",
        taken: "Этот ник уже занят",
        invalid_name: "Имя — от 1 до 30 символов",
        not_for_sale: "Эту награду выдаёт администратор",
        not_enough_xp: "Не хватает Adam Coin",
        habit_not_done: "Эта привычка уже не отмечена",
        undo_too_late: "Эту отметку я уже не уберу: убираю только сегодняшние",
        undo_limit: "Сегодня я уже убирал отметки несколько раз — эту оставим",
        undo_coins_spent: "Монеты за эту отметку уже потрачены — убрать её не получится",
    };

    const activeMap = currentLanguage === "en" ? ERROR_MAP_EN : map;
    const fallback = currentLanguage === "en" ? "Unknown error" : "Неизвестная ошибка";
    return activeMap[code] || (err && err.message) || fallback;
}

  // ===================== LEVEL UP =====================
  // Рубеж уровня (10/20/30...) — просьба пользователя: на круглых уровнях
  // более "эпичный" момент вместо обычного тоста, в духе LVL-экранов из
  // референсов. Фон — художественно сгенерированный градиент+свечение по
  // тиру (CSS, не фотография) — готовый набор, переиспользуется на всех
  // будущих рубежах без доп. затрат на генерацию картинок под каждый.
  const LEVEL_MILESTONE_TIERS = [
    { name: "🥉 Бронза", text: "Первый настоящий рубеж позади.", a: "#2a1608", b: "#100a06", glow: "rgba(255,138,61,.42)", glowStrong: "rgba(255,138,61,.65)", accent: "#FF8A3D" },
    { name: "🥈 Серебро", text: "Дисциплина уже не случайность — это ты.", a: "#171b24", b: "#0a0c10", glow: "rgba(180,200,224,.36)", glowStrong: "rgba(180,200,224,.6)", accent: "#C9D6E8" },
    { name: "🥇 Золото", text: "Ты сильнее, чем месяц назад.", a: "#2a2007", b: "#100c04", glow: "rgba(240,180,41,.42)", glowStrong: "rgba(240,180,41,.68)", accent: "#FFD54F" },
    { name: "💎 Платина", text: "Немногие заходят так далеко.", a: "#07242a", b: "#040e10", glow: "rgba(45,212,191,.40)", glowStrong: "rgba(45,212,191,.65)", accent: "#2DD4BF" },
    { name: "👑 Легенда", text: "Выше — только ты сам.", a: "#1c0a2a", b: "#0b0410", glow: "rgba(167,139,250,.44)", glowStrong: "rgba(167,139,250,.7)", accent: "#A78BFA" },
  ];
  let levelUpTimer = null;

  function burstCoins() {
    for (let i = 0; i < 6; i++) {
      const el = document.createElement("div");
      el.className = "coin-burst";
      el.textContent = "🪙";
      el.style.left = (50 + (Math.random() * 20 - 10)) + "vw";
      el.style.top = "36vh";
      el.style.setProperty("--x", (Math.random() * 160 - 80) + "px");
      el.style.setProperty("--y", (-Math.random() * 140 - 60) + "px");
      document.body.appendChild(el);
      setTimeout(() => el.remove(), 1200);
    }
  }

  function showLevelUp(level, previousLevel) {
    const overlay = document.getElementById("levelupOverlay");
    const value = document.getElementById("levelupValue");
    if (!overlay || !value) return;

    value.textContent = level;
    // Рубеж — если пересекли границу десятка (а не просто "уровень кратен
    // 10"): так его не пропустит тот, кто получил сразу несколько уровней
    // за раз (например, с 9 сразу на 12) и всё равно должен увидеть момент.
    const tierIndex = Math.floor(level / 10) - 1;
    const isMilestone = tierIndex >= 0 && Math.floor(level / 10) > Math.floor((previousLevel || 0) / 10);
    overlay.classList.toggle("is-milestone", isMilestone);

    if (isMilestone) {
      const tier = LEVEL_MILESTONE_TIERS[Math.min(tierIndex, LEVEL_MILESTONE_TIERS.length - 1)];
      overlay.style.setProperty("--tier-a", tier.a);
      overlay.style.setProperty("--tier-b", tier.b);
      overlay.style.setProperty("--tier-glow", tier.glow);
      overlay.style.setProperty("--tier-glow-strong", tier.glowStrong);
      overlay.style.setProperty("--tier-accent", tier.accent);
      const num = document.getElementById("levelupMilestoneNum");
      const tierLabel = document.getElementById("levelupMilestoneTier");
      const text = document.getElementById("levelupMilestoneText");
      if (num) num.textContent = level;
      if (tierLabel) tierLabel.textContent = tier.name;
      if (text) text.textContent = tier.text;
    }

    overlay.hidden = false;
    void overlay.offsetWidth;                 // оверлей теперь display:none, пока скрыт: без пересчёта плавное появление не сыграет
    overlay.classList.add("show");
    window.__adamLevelUpAt = performance.now();
    burstCoins();
    haptic(isMilestone ? "triple" : "success");   // рубеж (10/20/30…) — тройная долгая вибрация, обычный уровень — «успех»

    const dismiss = () => {
      overlay.classList.remove("show");
      setTimeout(() => { overlay.hidden = true; overlay.classList.remove("is-milestone"); }, 300);
    };
    clearTimeout(levelUpTimer);
    levelUpTimer = setTimeout(dismiss, isMilestone ? 4500 : 2200);
    const continueBtn = document.getElementById("levelupMilestoneContinue");
    if (continueBtn) {
      continueBtn.onclick = () => { haptic("light"); clearTimeout(levelUpTimer); dismiss(); };
    }
  }

  // ===================== BOOT =====================
// БАГ "непрогрузки": эта функция была написана как фикс для того самого
// класса багов, о котором пишут пользователи (пустые эмодзи-иконки —
// ✏️/🔥/❄️ — то в списке привычек, то в днях ударного режима, до тех
// пор пока не тронешь скролл), но её НИКТО и НИКОГДА не вызывал — она
// была мёртвым кодом. К тому же она чинила только 3 контейнера один раз
// при первой загрузке, а WebView теряет отрисовку конкретных текстовых
// узлов на КАЖДОМ перерисовывании через innerHTML (renderAll() вызывается
// повторно после каждого действия — отметил привычку, добавил задачу
// и т.п.), не только на старте. Поэтому: (1) реально подключена в конце
// renderAll(); (2) список целей расширен на все контейнеры, которые
// renderAll() перерисовывает через innerHTML и где пользователи видели
// пропавший текст/иконки.
// extraTargets — id-шники (или сами элементы) контейнеров, которые ТОЛЬКО
// что перерисовали через innerHTML в этом конкретном вызове (вкладки
// Магазин/Достижения/Рейтинг/Календарь рисуются лениво, при первом
// открытии вкладки — их не было смысла держать в фиксированном списке
// критичных элементов, они просто ещё не существуют до первого рендера).
function stabilizeFirstPaint(extraTargets, { lists = true } = {}) {
    // Раньше бралась только Главная (hardcoded 'section[data-tab="home"]') —
    // но эта функция теперь вызывается и при остановке скролла (см.
    // initScrollPerfGuard), когда открыта может быть ЛЮБАЯ вкладка. Берём
    // реально видимую панель, а не всегда Главную.
    const activePanel = document.querySelector(".tab-panel:not([hidden])")
        || document.querySelector('section[data-tab="home"]');
    const critical = [
        document.querySelector("header.player-card"),
        activePanel,
        document.getElementById("streakWidget")
    ].filter(Boolean);
    // НАЙДЕНО по реальной продакшн-телеметрии (запрос напрямую к живому
    // /api/admin/perf-events): 86% реальных long_frame/long_task событий
    // происходят с is_scrolling=0 — то есть ЧЕРЕЗ 150мс ПОСЛЕ остановки
    // скролла, ровно когда initScrollPerfGuard зовёт именно эту функцию.
    // Причина найдена здесь: header.player-card и streakWidget получали
    // contain:"layout paint" — а contain:paint это ТА САМАЯ причина
    // WebView compositor-layer-loss, которую весь остальной проект
    // намеренно избегает (см. комментарии у .habit-item и др. в style.css:
    // contain:paint уже один раз ловил именно этот баг и был убран
    // отовсюду). Здесь он был пропущен и остался единственным местом в
    // коде, ставящим contain:paint — причём НИКОГДА не откатываемым
    // назад (в отличие от opacity ниже), то есть header и streakWidget
    // накапливали это состояние заново при КАЖДОМ renderAll() и КАЖДОЙ
    // остановке скролла. Только "layout style" (без paint) — как и везде
    // в проекте.
    critical.forEach(el => {
        el.style.visibility = "visible";
        el.style.contain = "layout style";
    });
    // lists:false — точечные обновления (отметка привычки, тумблер плана): «пинок» только тому, что реально перерисовали.
    // Весь список привычек (до ~20 МБ видеопамяти на время двух кадров) на каждый тап — это и есть всплеск памяти, после
    // которого на телефоне пропадают куски экрана.
    const dynamic = lists ? [
        document.getElementById("habitList"),
        document.getElementById("planList"),
        document.getElementById("streakDays"),
    ].filter(Boolean) : [];
    const extra = (Array.isArray(extraTargets) ? extraTargets : [])
        .map(x => (typeof x === "string" ? document.getElementById(x) : x))
        .filter(Boolean);
    // ВАЖНО: opacity-«пинок» (ниже) — только перерисованным контейнерам. Раньше
    // в него входили и вся активная вкладка (1500+px), и шапка, и виджет серии:
    // на каждую отметку привычки телефон на 2 кадра собирал всё это в
    // отдельный offscreen-слой — отсюда пустые прямоугольники на месте
    // привычки/задачи/дня серии сразу после тапа.
    const targets = dynamic.concat(extra);
    if (!targets.length) return;
    // НАЙДЕНО по реальной телеметрии с прода (см. app.js::reportPerfEvent):
    // именно этот вызов (при остановке КАЖДОГО скролла) давал long_task до
    // 690мс и long_frame до 1750мс — то есть САМ ФИКС от лагов вызывал лаги.
    // Причина — classic layout thrashing: цикл ЧЕРЕДОВАЛ чтение
    // (el.offsetHeight) и запись (el.style.opacity) по 6-9 элементам, а
    // каждое чтение после чужой записи форсит ОТДЕЛЬНЫЙ синхронный
    // пересчёт layout. offsetHeight тут и не был нужен — нужен именно
    // paint/recomposite слоя, а opacity его форсит и БЕЗ layout (opacity —
    // чисто композитное свойство, geometry не трогает). Теперь: все записи
    // одним батчем, без единого чтения между ними.
    requestAnimationFrame(() => {
        // Два вызова подряд (тап → patch → renderAll) раньше запоминали «0.999»
        // как исходное значение и оставляли его навсегда; busy-флаг это исключает.
        const batch = targets.filter(el => !el._fpBusy);
        batch.forEach(el => { el._fpBusy = true; el._fpPrev = el.style.opacity; el.style.opacity = "0.999"; });
        requestAnimationFrame(() => {
            batch.forEach(el => { el.style.opacity = el._fpPrev; el._fpBusy = false; });
        });
    });
}

// ===================== ПРОГРЕСС + AI-АНАЛИЗ =====================
// Просьба пользователя: графики везде, по образцу референсов — плавная
// растущая кривая. Копим прирост Adam Coin ДЕНЬ ЗА ДНЁМ за последние 30
// дней (а не абсолютный total_xp за всю историю) — честная и куда более
// наглядная метрика роста, чем один раз выведенное большое число.
function renderProgressGrowthChart(daily) {
  const container = document.getElementById("progressGrowthChart");
  if (!container) return;
  const DAYS = 30;
  const byDate = {};
  (daily || []).forEach(d => { byDate[d.date] = d; });
  const series = [];
  const today = new Date();
  for (let i = DAYS - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, "0"), String(d.getDate()).padStart(2, "0")].join("-");
    series.push({ date: key, xp: Number(byDate[key]?.xp || 0) });
  }
  if (!series.some(s => s.xp > 0)) { container.hidden = true; return; }
  container.hidden = false;

  let running = 0;
  const cumulative = series.map(s => { running += s.xp; return running; });
  const total = running;

  const W = 350, H = 130, padTop = 16, padBottom = 6, padX = 4;
  const maxVal = Math.max(1, ...cumulative);
  const stepX = (W - padX * 2) / (series.length - 1);
  const points = cumulative.map((v, i) => [
    padX + i * stepX,
    padTop + (1 - v / maxVal) * (H - padTop - padBottom),
  ]);

  // Сглаженная кривая через кубические Безье по серединам соседних точек —
  // тот же эффект плавного роста, что и в референсах, а не ломаная линия.
  const linePath = points.reduce((acc, [x, y], i, arr) => {
    if (i === 0) return `M${x.toFixed(1)},${y.toFixed(1)}`;
    const [px, py] = arr[i - 1];
    const mx = ((px + x) / 2).toFixed(1);
    return `${acc} C${mx},${py.toFixed(1)} ${mx},${y.toFixed(1)} ${x.toFixed(1)},${y.toFixed(1)}`;
  }, "");
  const last = points[points.length - 1];
  const first = points[0];
  const areaPath = `${linePath} L${last[0].toFixed(1)},${(H - padBottom).toFixed(1)} L${first[0].toFixed(1)},${(H - padBottom).toFixed(1)} Z`;

  const firstLabel = series[0].date.slice(5).split("-").reverse().join(".");

  container.innerHTML = `
    <div class="progress-growth__head">
      <span>Рост Adam Coin за 30 дней</span>
      <b>+${total}</b>
    </div>
    <div class="progress-growth__chart">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="progress-growth__svg">
        <defs>
          <linearGradient id="progressGrowthFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--primary)" stop-opacity=".38"></stop>
            <stop offset="100%" stop-color="var(--primary)" stop-opacity="0"></stop>
          </linearGradient>
        </defs>
        <path d="${areaPath}" fill="url(#progressGrowthFill)"></path>
        <path d="${linePath}" fill="none" stroke="var(--primary-light)" stroke-width="2.5" stroke-linecap="round"></path>
        <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" fill="var(--primary-light)"></circle>
      </svg>
    </div>
    <div class="progress-growth__axis">
      <span>${escapeHtml(firstLabel)}</span>
      <span>Сегодня</span>
    </div>
  `;
}

async function loadProgressStats() {
  try {
    const data = await api("/api/progress/stats");
    const w = data.weekly || {};
    document.getElementById("progressStatCompleted").textContent = w.completed || 0;
    document.getElementById("progressStatActiveDays").textContent = `${w.active_days || 0}/7`;
    document.getElementById("progressStatXp").textContent = w.xp || 0;
    renderProgressGrowthChart(data.daily);

    // Roadmap #29 — "я сейчас vs я месяц назад".
    const cmp = data.comparison;
    const cmpEl = document.getElementById("progressComparison");
    if (cmpEl) {
      if (cmp && cmp.trend !== "not_enough_data") {
        const arrow = cmp.trend === "up" ? "📈" : cmp.trend === "down" ? "📉" : "➖";
        const sign = cmp.delta > 0 ? "+" : "";
        cmpEl.hidden = false;
        cmpEl.textContent = `${arrow} Сейчас ${cmp.current_rate}% выполнения против ${cmp.previous_rate}% месяц назад (${sign}${cmp.delta}%)`;
      } else {
        cmpEl.hidden = true;
      }
    }

    // Roadmap #30 — прогноз следующего рубежа серии по текущему темпу.
    const forecast = data.forecast;
    const forecastEl = document.getElementById("progressForecast");
    if (forecastEl) {
      if (forecast) {
        forecastEl.hidden = false;
        forecastEl.textContent = `🎯 На этом темпе рубеж «${forecast.next_milestone} дней» будет через ${forecast.days_left} ${pluralRu(forecast.days_left, "день", "дня", "дней")}`;
      } else {
        forecastEl.hidden = true;
      }
    }

    // Roadmap #27 — статистические корреляции между привычками.
    const correlations = data.correlations || [];
    const corrEl = document.getElementById("progressCorrelations");
    if (corrEl) {
      const top = correlations[0];
      if (top) {
        corrEl.hidden = false;
        corrEl.textContent = `🔗 Когда ты делаешь «${top.a}», ты также делаешь «${top.b}» в ${top.rate}% случаев (в среднем — ${top.baseline}%)`;
      } else {
        corrEl.hidden = true;
      }
    }
  } catch (err) {
    console.error("loadProgressStats failed:", err);
  }
}

// Roadmap #16/#9 — команда + сезонный рейтинг, оба живут на вкладке Рейтинг.
async function loadTeamAndSeason() {
  // Один общий promise защищает от дублей: boot-предзагрузка и открытие
  // вкладки рейтинга могут происходить почти одновременно. Каждый блок
  // рисуется сразу после своего ответа — команда больше не ждёт сезонный
  // рейтинг и наоборот.
  if (teamSeasonPromise) return teamSeasonPromise;
  teamSeasonPromise = (async () => {
    const teamPromise = api("/api/team", { timeoutMs: 8000 }).then(data => {
      state.team = data.team;
      renderTeamCard();
    }).catch(err => console.error("loadTeam failed:", err));
    const seasonPromise = api("/api/season", { timeoutMs: 8000 }).then(data => {
      state.season = data;
      renderSeasonList();
    }).catch(err => console.error("loadSeason failed:", err));
    const friendsPromise = loadFriendsCard();
    await Promise.allSettled([teamPromise, seasonPromise, friendsPromise]);
    return state;
  })();
  try {
    return await teamSeasonPromise;
  } finally {
    // Оставляем уже полученные данные на экране; promise нужен только для
    // защиты от параллельных стартов во время текущей загрузки.
    teamSeasonPromise = null;
  }
}

function renderTeamCard() {
  const box = document.getElementById("teamCard");
  if (!box) return;
  box.hidden = false;
  const team = state.team;

  if (!team) {
    // Просьба пользователя: две пустые строки без объяснения непонятны —
    // добавляем короткое описание, что вообще даёт команда, до самой
    // формы создания/входа.
    box.innerHTML = `
      <div class="team-card__title">🤝 Групповой челлендж</div>
      <div class="team-card__desc">Создай команду или войди по коду друга — увидите недельный прогресс друг друга и посоревнуетесь, кто больше отметил привычек.</div>
      <div class="team-card__row">
        <input type="text" id="teamNameInput" class="team-card__input" placeholder="Название команды" maxlength="40">
        <button type="button" class="team-card__btn" id="teamCreateBtn">Создать</button>
      </div>
      <div class="team-card__row">
        <input type="text" id="teamJoinInput" class="team-card__input" placeholder="...или код приглашения" maxlength="6">
        <button type="button" class="team-card__btn" id="teamJoinBtn">Войти</button>
      </div>`;
    document.getElementById("teamCreateBtn").addEventListener("click", async () => {
      const input = document.getElementById("teamNameInput");
      try {
        await api("/api/team/create", { method: "POST", body: JSON.stringify({ name: input.value }) });
        haptic("medium");
        await loadTeamAndSeason();
      } catch (err) { showToast(friendlyError(err), "error"); }
    });
    document.getElementById("teamJoinBtn").addEventListener("click", async () => {
      const input = document.getElementById("teamJoinInput");
      try {
        await api("/api/team/join", { method: "POST", body: JSON.stringify({ invite_code: input.value }) });
        haptic("medium");
        showToast("Добро пожаловать в команду!", "success");
        await loadTeamAndSeason();
      } catch (err) { showToast(friendlyError(err), "error"); }
    });
    return;
  }

  const membersHtml = team.members.map(m => `
    <div class="team-card__member">
      <span class="team-card__member-identity">
        <span class="team-card__member-name">${escapeHtml(m.first_name || "Игрок")}</span>
        ${m.handle ? `<span class="team-card__member-handle">@${escapeHtml(m.handle)}</span>` : ""}
      </span>
      <span class="team-card__member-count">${m.week_completions}</span>
    </div>`).join("");

  box.innerHTML = `
    <div class="team-card__title">🤝 ${escapeHtml(team.name)}</div>
    <div class="team-card__sub">Код приглашения: <b>${escapeHtml(team.invite_code)}</b> · за неделю: ${team.team_week_total}</div>
    <div class="team-card__members">${membersHtml}</div>
    <button type="button" class="team-card__leave" id="teamLeaveBtn">Покинуть команду</button>`;
  document.getElementById("teamLeaveBtn").addEventListener("click", async () => {
    if (!confirm("Покинуть команду?")) return;
    try {
      await api("/api/team/leave", { method: "POST" });
      haptic("light");
      await loadTeamAndSeason();
    } catch (err) { showToast(friendlyError(err), "error"); }
  });
}

// ===================== ДРУЗЬЯ И «НАПОМНИТЬ ДРУЗЬЯМ» =====================
// Друг — тот, кого добавили по личной ссылке, плюс участники команды (см.
// db/friends.py). Карточка живёт на вкладке Рейтинг. После первой отметки
// привычки за день сервер может прислать result.remind_friends — тогда
// снизу выезжает окно «Напомнить друзьям» со списком тех, кто сегодня ещё
// не отмечался (как в Duolingo). Причину «нельзя напомнить» сервер не
// раскрывает, поэтому у таких друзей просто нет кнопки.
let friendsData = null;
let remindSheetFriends = [];
let remindSheetTimer = null;

// Аватар игрока: загруженное фото или первая буква имени (та же логика, что в
// рейтинге). Рамка — класс frame-<id> из магазина.
function avatarInner(user) {
  const id = String(user.avatar_id || "default");
  if (id.startsWith("upload:")) {
    return `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(id.split(":")[1])}.jpg" alt="" loading="lazy">`;
  }
  return escapeHtml(String(user.first_name || "Д").trim().charAt(0).toUpperCase() || "Д");
}

function avatarFrameClass(user) {
  return `frame-${escapeHtml(String(user.frame_id || "default"))}`;
}

// Имя в строке друга/подписки + значок ранга справа: имя при нехватке места
// усекается многоточием, значок остаётся целиком.
function friendNameHtml(u, fallback, extra = "") {
  const name = escapeHtml(u.first_name || fallback);
  const chip = evoChipHtml(u.evo);
  if (!chip && !extra) return `<span class="friend-row__name">${name}</span>`;
  return `<span class="friend-row__name friend-row__name--evo"><span class="friend-row__name-text">${name}</span>${chip}${extra}</span>`;
}

function friendRowHtml(f, { viewerDone = true } = {}) {
  const id = Number(f.telegram_id);
  const meta = [
    f.handle ? `@${escapeHtml(f.handle)}` : "",
    Number(f.streak) > 0 ? `🔥 ${Number(f.streak)}` : "",
  ].filter(Boolean).join(" · ");
  let action = "";
  if (f.state === "can_remind") {
    action = `<button type="button" class="friend-row__btn" data-nudge="${id}"${viewerDone ? "" : " disabled"}>Напомнить</button>`;
  } else if (f.state === "reminded") {
    action = `<span class="friend-row__tag friend-row__tag--sent">Напомнил ✓</span>`;
  } else if (f.state === "done") {
    action = `<span class="friend-row__tag">✓ Отмечено</span>`;
  }
  return `
    <li class="friend-row" data-friend-id="${id}" data-profile-id="${id}">
      <span class="friend-row__avatar ${avatarFrameClass(f)}">${avatarInner(f)}</span>
      <span class="friend-row__info">
        ${friendNameHtml(f, "Друг", friendRescueHtml(f) + friendFreezeHtml(f))}
        ${meta ? `<span class="friend-row__meta">${meta}</span>` : ""}
      </span>
      ${action}
    </li>`;
}

function renderFriendsCard() {
  const box = document.getElementById("friendsCard");
  if (!box) return;
  if (!friendsData) { box.hidden = true; return; }
  box.hidden = false;
  const friends = friendsData.friends || [];
  const counts = friendsData.counts || { followers: 0, following: 0 };
  const waiting = friends.some(f => f.state === "can_remind");
  let hint = "";
  if (!friends.length) {
    hint = "Друзья — это взаимная подписка. Добавь друга по ссылке или найди по @нику и подпишись: если он подпишется в ответ, вы увидите статистику друг друга и сможете подталкивать друг друга.";
  } else if (waiting && !friendsData.viewer_done) {
    hint = "Отметь свою привычку — и сможешь напомнить друзьям, которые ещё не начали день.";
  }
  const reward = friendsData.invite_reward;
  const rewardHint = reward && Number(reward.left) > 0
    ? `🎁 За каждого из первых двух приглашённых — ${Number(reward.coins)} Адам коинов${Number(reward.used) ? ` (осталось ${Number(reward.left)})` : ""}.`
    : "";
  const typedSearch = document.getElementById("friendsSearchInput")?.value || "";
  box.innerHTML = `
    <div class="friends-card__head">
      <div class="friends-card__title">👥 Друзья</div>
      <button type="button" class="friends-card__add" id="friendsAddBtn">＋ Добавить</button>
    </div>
    <div class="friends-card__counts">
      <button type="button" class="friends-card__count" data-follow-kind="following"><b>${Number(counts.following)}</b> Подписки</button>
      <button type="button" class="friends-card__count" data-follow-kind="followers"><b>${Number(counts.followers)}</b> Подписчики</button>
    </div>
    <form class="friends-card__search" id="friendsSearchForm" autocomplete="off">
      <input type="text" id="friendsSearchInput" class="friends-card__input" placeholder="Найти игрока по @нику" maxlength="40" autocapitalize="off" autocorrect="off" spellcheck="false">
      <button type="submit" class="friends-card__find">Найти</button>
    </form>
    ${hint ? `<div class="friends-card__hint">${hint}</div>` : ""}
    ${rewardHint ? `<div class="friends-card__hint">${rewardHint}</div>` : ""}
    ${friends.length ? `<ul class="friends-list">${friends.map(f => friendRowHtml(f, { viewerDone: !!friendsData.viewer_done })).join("")}</ul>` : ""}`;
  // Перерисовка (например, после отметки привычки) не должна стирать то, что
  // человек уже набрал в поиске.
  if (typedSearch) document.getElementById("friendsSearchInput").value = typedSearch;
}

// Счётчики подписок в шапке (index.html #playerFollow): пока не пришли с сервера — прячем 0, чтобы не мигали ложные «0».
function renderFollowCounters() {
  const counts = state?.user?.follow_counts;
  if (!counts) return;
  // Большие числа сокращаем (1.2K), чтобы пилюля не растягивала шапку.
  const short = (value) => {
    const n = Number(value) || 0;
    return n >= 1000 ? `${(Math.floor(n / 100) / 10).toString().replace(/\.0$/, "")}K` : String(n);
  };
  const setText = (id, value) => {
    const el = document.getElementById(id);
    const text = short(value);
    if (el && el.textContent !== text) el.textContent = text;
  };
  setText("playerFollowingCount", counts.following);
  setText("playerFollowersCount", counts.followers);
  const badge = document.getElementById("playerFollowersNew");
  if (badge) {
    const fresh = Number(counts.new) || 0;
    badge.hidden = fresh <= 0;
    if (fresh > 0) badge.textContent = fresh > 99 ? "+99" : `+${fresh}`;
  }
}

// Любой ответ сервера со счётчиками (карточка «Друзья», список подписок) держит шапку в актуальном состоянии.
function syncFollowCounts(counts, newFollowers) {
  if (!state?.user || !counts) return;
  const previous = state.user.follow_counts || {};
  state.user.follow_counts = {
    following: Number(counts.following) || 0,
    followers: Number(counts.followers) || 0,
    new: newFollowers === undefined ? Number(previous.new) || 0 : Number(newFollowers) || 0,
  };
  renderFollowCounters();
}

async function loadFriendsCard() {
  try {
    friendsData = await api("/api/friends", { timeoutMs: 8000 });
    syncFollowCounts(friendsData.counts, friendsData.new_followers);
    renderFriendsCard();
  } catch (err) {
    console.error("loadFriends failed:", err);
  }
}

function setFriendState(friendId, nextState) {
  const apply = (f) => { if (Number(f.telegram_id) === friendId) f.state = nextState; };
  (friendsData?.friends || []).forEach(apply);
  remindSheetFriends.forEach(apply);
  renderFriendsCard();
  renderRemindList();
}

async function nudgeFriend(friendId, button) {
  if (button) button.disabled = true;
  try {
    await api(`/api/friends/${friendId}/nudge`, { method: "POST" });
    haptic("medium");
    showToast("Напомнил другу 👋", "success");
    setFriendState(friendId, "reminded");
  } catch (err) {
    const code = err?.data?.error;
    if (code === "already_reminded") setFriendState(friendId, "reminded");
    else if (code === "already_done") setFriendState(friendId, "done");
    else if (button) button.disabled = false;
    showToast(friendlyError(err), "error");
  }
}

// t.me/share/url склеивает сообщение как «url + перенос + text» — ссылка всегда
// оказывалась СВЕРХУ, а подпись («…добавляйся в друзья:») под ней. Нужен порядок
// «подпись, потом ссылка», поэтому меняем параметры местами: подпись едет в url,
// ссылка — в text. Превью строится по ссылке, где бы она ни стояла.
function telegramShareUrl(text, link) {
  return `https://t.me/share/url?url=${encodeURIComponent(text)}&text=${encodeURIComponent(link)}`;
}

async function openFriendInvite(shareText) {
  haptic("light");
  let link = friendsData?.invite_url || "";
  if (!link && state?.bot_username && state?.user?.telegram_id) {
    link = `https://t.me/${state.bot_username}?start=friend_${state.user.telegram_id}`;
  }
  if (!link) {
    try {
      const data = await api("/api/friends", { timeoutMs: 8000 });
      friendsData = data;
      link = data.invite_url || "";
    } catch (_) { /* ниже — сообщение об ошибке */ }
  }
  if (!link) {
    showToast("Не получилось собрать ссылку — попробуй чуть позже", "error");
    return;
  }
  const text = shareText || "Давай держать привычки вместе в ADAM — добавляйся в друзья:";
  const shareUrl = telegramShareUrl(text, link);
  if (tg && typeof tg.openTelegramLink === "function") {
    tg.openTelegramLink(shareUrl);
  } else {
    window.open(shareUrl, "_blank");
  }
}

// Окно после отметки. Праздничные экраны (серия, новый уровень) важнее и
// закрываются сами или по кнопке — ждём, пока они уйдут, плюс пару секунд
// тишины, чтобы окно не наехало на тосты с монетами.
const REMIND_BLOCKING_OVERLAYS = [
  "streakCelebrationOverlay", "doubleBonusOverlay", "pairGuideOverlay", "levelupOverlay", "streakOnboardingOverlay",
  "achievementShareOverlay", "archetypeQuizOverlay", "startQuizOverlay", "appTourOverlay",
  "handleIntroOverlay", "heroLightbox", "evolutionOverlay", "adminGiftOverlay", "statusLadderOverlay", "chestOverlay", "adamIntroOverlay", "freezeHelpOverlay", "rescueHelpOverlay", "inboxOverlay", "habitEditSheet", "habitUndoSheet", "trialPaywallOverlay", "trialOfferCard", "partnerOverlay",
];

function celebrationOverlayOpen({ skipEvolution = false } = {}) {
  // pendingBonusIntro — окно «Удвоение очков» откроется сразу после того,
  // как закроют праздник серии; в этот зазор оно уже «ждёт своей очереди».
  if (pendingBonusIntro) return true;
  // Новая эволюция уже ждёт своей очереди — остальные окна подождут её.
  if (!skipEvolution && (evolutionRun.watch || evolutionRun.active)) return true;
  if (REMIND_BLOCKING_OVERLAYS.some(id => {
    const el = document.getElementById(id);
    return !!el && !el.hidden;
  })) return true;
  // Шторки — по атрибуту hidden, а не по классу is-open: hidden снимается
  // сразу при открытии, а is-open добавляется только на следующем кадре, и в
  // этот зазор второе окно успело бы открыться поверх первого.
  return !!document.querySelector(
    ".feedback-sheet:not([hidden]), .freeze-purchase-sheet:not([hidden]), .daily-quests-overlay:not([hidden])"
  );
}

function scheduleRemindFriends(payload) {
  const friends = payload?.friends;
  if (!Array.isArray(friends) || !friends.length) return;
  remindSheetFriends = friends.map(f => ({ ...f }));
  clearInterval(remindSheetTimer);
  const startedAt = Date.now();
  let quietTicks = 0;
  remindSheetTimer = setInterval(() => {
    if (Date.now() - startedAt > 5 * 60 * 1000) {
      clearInterval(remindSheetTimer);
      remindSheetTimer = null;
      return;
    }
    if (celebrationOverlayOpen() || document.hidden) { quietTicks = 0; return; }
    quietTicks += 1;
    if (quietTicks < 4) return;
    clearInterval(remindSheetTimer);
    remindSheetTimer = null;
    openRemindSheet();
  }, 800);
}

function renderRemindList() {
  const list = document.getElementById("remindFriendsList");
  if (!list) return;
  list.innerHTML = remindSheetFriends.map(f => friendRowHtml(f)).join("");
  const closeBtn = document.getElementById("remindFriendsClose");
  if (closeBtn) {
    closeBtn.textContent = remindSheetFriends.some(f => f.state === "can_remind") ? "Не сейчас" : "Готово";
  }
}

function openRemindSheet() {
  const sheet = document.getElementById("remindFriendsSheet");
  if (!sheet || !remindSheetFriends.length) return;
  renderRemindList();
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
  haptic("light");
}

function closeRemindSheet() {
  const sheet = document.getElementById("remindFriendsSheet");
  if (!sheet || sheet.hidden) return;
  haptic("light");
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  remindSheetDemo = false;
  setTimeout(() => { sheet.hidden = true; }, 230);
}

function initFriendsActions() {
  const card = document.getElementById("friendsCard");
  card?.addEventListener("click", async (e) => {
    if (e.target.closest("#friendsAddBtn")) {
      await openFriendInvite();
      return;
    }
    const rescueBtn = e.target.closest("[data-rescue-help]");
    if (rescueBtn) {
      const friend = (friendsData?.friends || []).find((f) => Number(f.telegram_id) === Number(rescueBtn.dataset.rescueHelp));
      if (friend?.rescue) {
        trk("rescue_help_open");
        openRescueHelp({ friend: { ...friend, lost_streak: Number(friend.rescue.lost_streak) || 0 } });
      }
      return;
    }
    const freezeBtn = e.target.closest("[data-freeze-help]");
    if (freezeBtn) {
      const friend = (friendsData?.friends || []).find((f) => Number(f.telegram_id) === Number(freezeBtn.dataset.freezeHelp));
      if (friend) {
        trk("freeze_help_open");
        openFreezeHelp({ friend, price: Number(friendsData.freeze_price) || 200 });
      }
      return;
    }
    const nudgeBtn = e.target.closest("[data-nudge]");
    if (nudgeBtn) {
      await nudgeFriend(Number(nudgeBtn.dataset.nudge), nudgeBtn);
      return;
    }
    const countBtn = e.target.closest("[data-follow-kind]");
    if (countBtn) {
      await openFollowList(countBtn.dataset.followKind);
      return;
    }
    // Тап по строке друга (мимо кнопок) — его профиль.
    const row = e.target.closest("[data-profile-id]");
    if (row && !e.target.closest("button, input, form")) {
      await openUserProfile(Number(row.dataset.profileId));
    }
  });
  card?.addEventListener("submit", async (e) => {
    if (!e.target.closest("#friendsSearchForm")) return;
    e.preventDefault();
    await searchPlayerByHandle(document.getElementById("friendsSearchInput")?.value || "");
  });

  document.getElementById("remindFriendsList")?.addEventListener("click", async (e) => {
    const nudgeBtn = e.target.closest("[data-nudge]");
    if (!nudgeBtn) return;
    if (remindSheetDemo) { showToast("🧪 Демо: напоминание не отправляется", "success", 1800); return; }
    await nudgeFriend(Number(nudgeBtn.dataset.nudge), nudgeBtn);
  });
  document.getElementById("remindFriendsClose")?.addEventListener("click", closeRemindSheet);
  document.getElementById("remindFriendsBackdrop")?.addEventListener("click", closeRemindSheet);
}

// ===================== ЦЕНТР УВЕДОМЛЕНИЙ (db/inbox.py) =====================
// Колокольчик в правом верхнем углу шапки → экран «Уведомления»: вся лента (подарки, сообщения Адама, друзья, подписчики, системное)
// и вкладки-разделы сверху (Все · Системные · Адам · Друзья · Подписчики · Подарки): переключаются одним тапом, лента грузится один раз,
// вкладки только фильтруют. Список разделов и записи отдаёт сервер (новый вид уведомления — новый «источник» в db/inbox.py, приложение
// менять не нужно). На колокольчике — число новых (GET /api/inbox/summary: при входе, при возвращении в приложение и раз в две минуты).
// Открыли центр — метка «прочитано» обновляется, а новые записи в этой сессии остаются подсвеченными. demo — симуляция из админки.
let inboxState = null;          // { items, categories, tab, demo }
let inboxPollTimer = null;
const INBOX_EMPTY = {
  all: "Здесь появятся подарки, мои сообщения и новости от друзей и подписчиков.",
  system: "Про серию, заморозки и новости напишу сюда.",
  adam: "Когда я напишу сам, сообщение будет здесь.",
  friends: "Поддержка, напоминания и парные задания с друзьями — здесь.",
  followers: "Новые подписчики появятся здесь.",
  gifts: "Подарки от друзей и администратора будут здесь.",
};

function setInboxBadge(count) {
  const badge = document.getElementById("inboxBadge");
  if (!badge) return;
  const n = Number(count) || 0;
  badge.hidden = n <= 0;
  badge.textContent = n > 99 ? "99+" : String(n);
}

async function refreshInboxBadge() {
  if (labMode || simMode || document.visibilityState === "hidden") return;
  try {
    const data = await api("/api/inbox/summary", { timeoutMs: 8000 });
    setInboxBadge(data?.unread);
  } catch (_) { /* колокольчик просто останется как был */ }
}

function inboxWhen(at) {
  const d = new Date(String(at).replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return { day: "", time: "" };
  const now = new Date();
  const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((dayStart(now) - dayStart(d)) / 86400000);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const day = diff === 0 ? "Сегодня" : diff === 1 ? "Вчера" : d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  return { day, time };
}

function inboxItemHtml(it) {
  const when = inboxWhen(it.at);
  const lead = it.actor
    ? `<span class="friend-row__avatar inbox-item__avatar ${avatarFrameClass(it.actor)}">${avatarInner(it.actor)}<i class="inbox-item__kind">${it.icon}</i></span>`
    : it.voice ? `<span class="inbox-item__voice">${adamVoiceHtml("md", it.icon)}</span>`
    : `<span class="inbox-item__icon">${it.icon}</span>`;
  const action = it.action?.type ? ` data-inbox-action="${escapeHtml(it.action.type)}"${it.action.id ? ` data-inbox-id="${Number(it.action.id)}"` : ""}` : "";
  return `
    <li class="inbox-item${it.unread ? " is-unread" : ""}${it.action?.type ? " is-tappable" : ""}" data-inbox-kind="${escapeHtml(it.kind)}"${action}>
      ${lead}
      <span class="inbox-item__body">
        <b class="inbox-item__title">${escapeHtml(it.title)}</b>
        ${it.text ? `<small class="inbox-item__text">${escapeHtml(it.text)}</small>` : ""}
      </span>
      <time class="inbox-item__time">${escapeHtml(when.time)}</time>
    </li>`;
}

function renderInbox() {
  const s = inboxState;
  const tabs = document.getElementById("inboxTabs");
  const body = document.getElementById("inboxBody");
  if (!s || !tabs || !body) return;
  tabs.innerHTML = s.categories.map((c) => `
    <button type="button" class="inbox-tab${c.key === s.tab ? " is-active" : ""}" role="tab" aria-selected="${c.key === s.tab}" data-inbox-tab="${escapeHtml(c.key)}">
      ${escapeHtml(c.label)}${Number(c.unread) > 0 ? `<i class="inbox-tab__count">${Number(c.unread) > 99 ? "99+" : Number(c.unread)}</i>` : ""}
    </button>`).join("");
  const items = s.tab === "all" ? s.items : s.items.filter((it) => it.cat === s.tab);
  if (!items.length) {
    body.innerHTML = `<div class="inbox-empty"><span class="inbox-empty__icon">🔔</span><b>Здесь пока тихо</b><p>${escapeHtml(INBOX_EMPTY[s.tab] || INBOX_EMPTY.all)}</p></div>`;
    return;
  }
  let lastDay = null;
  const parts = [];
  items.forEach((it) => {
    const day = inboxWhen(it.at).day;
    if (day !== lastDay) {
      if (lastDay !== null) parts.push("</ul>");
      parts.push(`<div class="inbox-day">${escapeHtml(day)}</div><ul class="inbox-list">`);
      lastDay = day;
    }
    parts.push(inboxItemHtml(it));
  });
  parts.push("</ul>");
  body.innerHTML = parts.join("");
}

function showInboxOverlay() {
  const overlay = document.getElementById("inboxOverlay");
  if (!overlay) return;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  const scroller = document.getElementById("inboxBody");
  if (scroller) scroller.scrollTop = 0;
  requestAnimationFrame(() => { overlay.classList.add("is-open"); syncBackButton(); });
  hideProductHint();
}

function closeInbox() {
  const overlay = document.getElementById("inboxOverlay");
  if (!overlay || overlay.hidden) return;
  overlay.classList.remove("is-open");
  overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!overlay.classList.contains("is-open")) overlay.hidden = true; }, 300);
  syncBackButton();
  if (inboxState && !inboxState.demo) refreshInboxBadge();
}

// demo — готовая лента для симуляции (SIM_SCENES): ничего не грузится и не отмечается прочитанным.
async function openInbox({ demo = null } = {}) {
  haptic("light");
  inboxState = { items: [], categories: [], tab: "all", demo: !!demo };
  if (demo) {
    inboxState.items = demo.items;
    inboxState.categories = demo.categories;
  } else {
    inboxState.categories = [{ key: "all", label: "Все", unread: 0 }];
    document.getElementById("inboxBody").innerHTML = `<div class="inbox-empty"><b>Загружаю…</b></div>`;
  }
  renderInbox();
  showInboxOverlay();
  trk("inbox_open", null, { unread: Number(document.getElementById("inboxBadge")?.textContent) || 0 });
  if (demo) return;
  try {
    const data = await api("/api/inbox", { timeoutMs: 8000 });
    if (!inboxState || inboxState.demo) return;
    inboxState.items = data.items || [];
    inboxState.categories = data.categories || [];
    renderInbox();
    // открыли — значит, прочитано: бейдж гаснет сразу, а подсветка новых в этой сессии остаётся
    setInboxBadge(0);
    api("/api/inbox/seen", { method: "POST" }).catch(() => {});
  } catch (err) {
    document.getElementById("inboxBody").innerHTML = `<div class="inbox-empty"><b>Не получилось загрузить</b><p>Проверь соединение и открой снова.</p></div>`;
  }
}

function onInboxTap(e) {
  const tab = e.target.closest("[data-inbox-tab]");
  if (tab && inboxState) {
    inboxState.tab = tab.dataset.inboxTab;
    haptic("select");
    trk("inbox_tab", inboxState.tab);
    renderInbox();
    document.getElementById("inboxBody").scrollTop = 0;
    tab.scrollIntoView?.({ block: "nearest", inline: "center" });
    return;
  }
  const row = e.target.closest("[data-inbox-action]");
  if (!row || !inboxState) return;
  const type = row.dataset.inboxAction;
  trk("inbox_item", row.dataset.inboxKind || null);
  if (inboxState.demo) { haptic("light"); showToast("🎬 Демо: тут откроется " + ({ profile: "профиль", gifts: "«Мне подарили»", chat: "чат с Адамом", pair: "парное задание", home: "Главная" }[type] || "нужный экран"), "success", 1800); return; }
  closeInbox();
  setTimeout(() => {
    if (type === "profile") openUserProfile(Number(row.dataset.inboxId));
    else if (type === "gifts") document.getElementById("openGiftsBtn")?.click();
    else if (type === "chat") { window.location.href = "/coach"; }
    else if (type === "pair" || type === "home") document.querySelector('.tab-bar__item[data-tab="home"]')?.click();
    else if (type === "profile_self") document.querySelector('.tab-bar__item[data-tab="profile"]')?.click();
  }, 260);
}

function initInbox() {
  document.getElementById("inboxBtn")?.addEventListener("click", () => openInbox());
  document.getElementById("inboxOverlayClose")?.addEventListener("click", closeInbox);
  document.getElementById("inboxOverlayBackdrop")?.addEventListener("click", closeInbox);
  document.getElementById("inboxOverlay")?.addEventListener("click", onInboxTap);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshInboxBadge(); });
  clearInterval(inboxPollTimer);
  inboxPollTimer = setInterval(refreshInboxBadge, 120000);
  setTimeout(refreshInboxBadge, 3000);
}

// ===================== ГОЛОС АДАМА =====================
// Все сообщения приложения говорят от первого лица Адама («я напомню», «я написал»), а не от третьего лица или от «бота». Рядом с
// такими сообщениями — маленькая аватарка Адама с тонким кольцом: в тостах (вместо цветной точки), в окнах-сообщениях, подсказках,
// плашках и в «Уведомлениях». adamVoiceHtml(размер, наклейка): xs 16 · sm 24 · md 38 · lg 46 px; наклейка — маленький эмодзи у угла.
const ADAM_VOICE_SRC = "/static/assets/adam-avatar-96.webp";
function adamVoiceHtml(size = "sm", sticker = "") {
  return `<span class="adam-voice adam-voice--${size}" aria-hidden="true"><img src="${ADAM_VOICE_SRC}" alt="" decoding="async">${sticker ? `<i class="adam-voice__sticker">${sticker}</i>` : ""}</span>`;
}
// Демо для симуляции «Напомнить друзьям»: те же строки, но «Напомнить» ничего не отправляет.
let remindSheetDemo = false;
const SIM_REMIND_FRIENDS = [
  { telegram_id: 1, first_name: "Мария", handle: "maria", streak: 42, state: "can_remind", avatar_id: "default", frame_id: "default" },
  { telegram_id: 2, first_name: "Денис", handle: "denis", streak: 9, state: "can_remind", avatar_id: "default", frame_id: "default" },
];
function openRemindSheetDemo(friends) {
  remindSheetDemo = true;
  remindSheetFriends = friends.map((f) => ({ ...f }));
  openRemindSheet();
}
// Три уведомления подряд (успех, ошибка, похвала) — как выглядит аватарка в тостах; монетный тост «+10» — без неё.
function showVoiceToastsDemo() {
  showToast("Привычка обновлена", "success", 2200);
  showToast("Сейчас не могу отправить — попробуй чуть позже", "error", 2400);
  showToast("Себя можно похвалить! 🎉", "praise", 2400);
  showToast("+10 Adam Coin", "praise", 2000);
}

// ===================== ПОДПИСКА НА АДАМА И ТРИАЛ (db/access_state.py, db/trial.py, db/subscription.py) =====================
// ТЗ 28.10: один экран «Подписка на Адама» на все состояния — пробные дни, дополнительное окно после них, закрытый доступ, оплачено. Кнопка подписки доступна с первого дня:
// тап по карточке на Главной, по индикатору времени или строка в «Настройках». Блокирующим экран бывает только после триала (дополнительное окно: можно закрыть и смотреть
// историю, она только для чтения). Данные — публичный снимок db/trial.py::public_trial (state.access.trial или err.data.trial у закрытого аккаунта) и GET /api/subscription
// (срок, продление, ожидание оплаты). Оплата: POST /api/subscription/invoice → Telegram.WebApp.openInvoice; доступ открывает бот (successful_payment), экран ждёт подтверждение.
// Симуляции (админка → «Окна и анимации»): trial_card, trial_sheet, trial_paywall_intro, trial_paywall_full, sub_paid, sub_pending, sub_summary, trial_timer — демо-данные, оплата не открывается.
const SIM_TRIAL = {
  scenario: true, day: 2, days_total: 3, phase: "day2", state: "trial", paid: false, read_only: false, intro_active: true, price_stars: 153, intro_price_stars: 153,
  renewal_price_stars: 461, intro_usd: "$1.99", renewal_usd: "$5.99", streak: 1, needed: 2, marked_today: false, marked_yesterday: true,
  channel_configured: false, channel_granted: false, channel_eligible: false, ends_at_text: "30 октября, 00:00", reason_text: "Пробный доступ до 30 октября, 00:00",
  ends_at: null, payment_pending: false,
};
const SIM_TRIAL_LAST = { ...SIM_TRIAL, day: 3, phase: "day3", ends_at_text: "30 октября, 00:00", timer_demo_ms: 5 * 3600000 + 20 * 60000 };
const SIM_TRIAL_INTRO_EXPIRED = { ...SIM_TRIAL, day: 4, phase: "grace", state: "grace", read_only: true, ends_at_text: "29 октября, 00:00", streak: 2, marked_today: false, marked_yesterday: true, timer_demo_ms: 14 * 3600000 + 5 * 60000 };
const SIM_TRIAL_FULL_EXPIRED = { ...SIM_TRIAL, day: 6, phase: "expired", state: "expired", read_only: false, streak: 0, marked_yesterday: false };
const SIM_TRIAL_PAID = { ...SIM_TRIAL, phase: "paid", state: "paid", paid: true, intro_active: false };
const SIM_SUB_OVERVIEW = { auto_renew: false, can_toggle: false, autorenew_available: false, paid_until_text: "30 ноября, 00:00", payment_pending: false, support: "@egor_support" };
const SIM_SUB_AUTO = { ...SIM_SUB_OVERVIEW, auto_renew: true, can_toggle: true, autorenew_available: true };
const SIM_SUMMARY = {
  title: "Коротко за 3 дня с Адамом", enough: true,
  lines: ["Привычки: отмечено 5 раз в 3 из 3 дней.", "Чаще всего повторялась «Зарядка» — 3 раза.", "Главная задача была записана в 2 из 3 дней, выполнена в 1.",
          "План дня: добавлено задач — 4, выполнено — 2.", "Пока без отметок: «Читать 20 минут». Здесь стоит выбрать посильный первый шаг."],
  note: "После семи и больше дней использования появится больше материала для подробного недельного разбора, а по мере накопления истории — и для месячного.",
};

// Тексты экрана. Короткое предложение и раскрываемый рассказ о проекте — из ТЗ дословно; слова не повторяются между заголовком, подписью, предложением и кнопкой.
const SUB_LEAD = "Собирай день вокруг того, что важно тебе: поддерживай привычки, выбирай главные задачи и обсуждай следующий шаг с Адамом. Твоя история помогает замечать, что получается и где стоит изменить подход.";
const SUB_PERKS = ["Привычки и серия", "План дня: главная и второстепенные задачи", "Адам, который знает контекст твоих действий", "Прогресс и разборы", "Рейтинг и друзья"];
const SUB_MISSION = [
  "Адам создаётся, чтобы помогать превращать намерения в ежедневные действия. Мы часто понимаем, что для нас важно, но теряем это среди срочных дел, отвлечений и новых планов. Здесь можно выбрать привычки, расставить приоритеты и возвращаться к своим решениям вместе с наставником.",
  "Для нас развитие — это возможность самостоятельнее управлять своей жизнью: выбирать цели, замечать последствия действий и менять подход, если он не работает. Это и есть субъектность — постепенно становиться автором собственных решений.",
  "Этот путь необязательно проходить одному. Можно приглашать знакомых, видеть прогресс друг друга, соревноваться и поддерживать. За отметками должны оставаться реальные действия, а за рейтингом — собственный результат.",
  "Ты можешь быть одним из первых участников: попробовать этот подход в своей жизни и рассказать, что помогает, а что мешает. Обратная связь поможет сделать приложение полезнее. Начать можно с одного посильного действия сегодня.",
];
const SUB_PENDING_TEXT = "Ждём подтверждение оплаты: Telegram передаёт её чуть позже. Повторно платить не нужно.";
const SUB_DISMISS_KEY = "adam_sub_dismissed_on";
let trialSheetData = null;        // снимок, который показывает экран
let trialDemo = false;            // симуляция: «Продолжить с Адамом» ничего не открывает
let trialPayBusy = false;
let trialPaywallTracked = false;
let subOverview = null;           // GET /api/subscription (срок, продление, ожидание оплаты)
let subSummaryShown = false;
let trialTimerHandle = null;
let trialTimerTracked = false;

function trialDays(n) {
  return `${n} ${pluralRu(n, "день", "дня", "дней")}`;
}

function trialDayDots(t) {
  let html = "";
  for (let n = 1; n <= t.days_total; n += 1) {
    const cls = t.paid || n < t.day ? "is-done" : (n === t.day ? "is-now" : "");
    html += `<i class="trial-dot ${cls}"></i>`;
  }
  return html;
}

// Сколько осталось до конца пробных дней (в последние сутки) или до конца защиты серии (в дополнительном окне). null — таймер не нужен.
function trialTimerInfo(t, now = Date.now()) {
  if (!t || !t.scenario || t.paid) return null;
  let left = null;
  if (typeof t.timer_demo_ms === "number") left = t.timer_demo_ms;
  else if (t.ends_at) left = new Date(`${t.ends_at}Z`).getTime() - now;
  if (left === null || left <= 0) return null;
  const grace = t.phase === "grace";
  if (!grace && !(t.phase === "day3" || left <= 24 * 3600000)) return null;
  const total = Math.ceil(left / 60000);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  const span = hours ? `${hours} ч ${minutes} мин` : `${minutes} мин`;
  return { grace, text: grace ? `Защита серии: ещё ${span}` : `Осталось ${span}`, caption: grace ? "Столько можно сохранить заработанную серию" : "Столько осталось до конца пробного периода" };
}

// Что написано на карточке Главной: срок пробного периода; в последние сутки — индикатор времени, после триала — срок защиты серии.
function trialCardText(t) {
  if (t.phase === "grace") return { title: "Пробный доступ завершён", sub: `Серию можно сохранить до ${t.ends_at_text}` };
  if (t.phase === "day3" || trialTimerInfo(t)) return { title: "Последние сутки пробного периода", sub: `Доступ открыт до ${t.ends_at_text}` };
  return { title: `Пробный период · день ${t.day}/${t.days_total}`, sub: `Доступ открыт до ${t.ends_at_text}` };
}

function updateTrialTimer() {
  const t = state?.access?.paywall ? state.access.trial : null;
  const el = document.getElementById("trialCardTimer");
  const hint = document.getElementById("trialCardHint");
  if (!el) return;
  const info = t ? trialTimerInfo(t) : null;
  if (!info) { el.hidden = true; if (hint) hint.hidden = true; return; }
  el.hidden = false;
  el.textContent = info.text;
  if (hint) {
    let seen = false;
    try { seen = localStorage.getItem("adam_trial_timer_hint") === "1"; } catch (_) {}
    hint.textContent = info.caption;
    hint.hidden = seen;
  }
  if (!trialTimerTracked) { trialTimerTracked = true; trk("trial_timer_shown", info.grace ? "grace" : "trial", null); }
}

function renderTrialCard() {
  const card = document.getElementById("trialCard");
  if (!card) return;
  const t = state?.access?.paywall ? state.access.trial : null;
  const settingsRow = document.getElementById("subscriptionSettingsRow");     // кнопка подписки в настройках — у всех в сценарии триала, с первого дня
  if (settingsRow) settingsRow.hidden = !(t && t.scenario);
  const hide = !t || !t.scenario || t.phase === "expired" || t.paid;
  if (hide) { card.hidden = true; clearInterval(trialTimerHandle); trialTimerHandle = null; return; }
  const text = trialCardText(t);
  document.getElementById("trialCardVoice").innerHTML = adamVoiceHtml("md");
  document.getElementById("trialCardTitle").textContent = text.title;
  document.getElementById("trialCardSub").textContent = text.sub;
  document.getElementById("trialCardDays").innerHTML = t.phase === "grace" ? "" : trialDayDots(t);
  card.hidden = false;
  updateTrialTimer();
  clearInterval(trialTimerHandle);
  trialTimerHandle = setInterval(updateTrialTimer, 30000);
}

function trialPayLabel() {
  return "Продолжить с Адамом";
}

// Как продлевается подписка — честно, по тому, как она реально оформлена (db/subscription.py::subscription_overview).
function subRenewalText(t, ov) {
  if (t.paid) {
    if (ov?.can_toggle) return ov.auto_renew
      ? "Списание повторяется каждые 30 дней, пока ты не отключишь его: в приложении или в настройках Telegram."
      : "Автоматические списания выключены: когда срок закончится, подписку можно будет оформить снова.";
    return "Само ничего не спишется: срок ты обновляешь вручную, я напомню заранее.";
  }
  return ov?.autorenew_available
    ? "Первая оплата разовая, на 30 дней. Дальше можно оформить подписку, которая списывается сама, — отключить её можно в любой момент."
    : "Само ничего не спишется: срок ты обновляешь вручную, я напомню заранее.";
}

function subCopy(t, ov) {
  if (t.paid) return { title: "Подписка на Адама", status: `Доступ открыт до ${ov?.paid_until_text || ""}`.trim(), lead: "Спасибо, что остаёшься со мной." };
  if (t.phase === "grace") return {
    title: "Пробный доступ завершён",
    status: `Серия сохранится, если оформить подписку до ${t.ends_at_text}`,
    lead: "Твоя история остаётся с тобой: вернутся привычки, планирование и разговор с Адамом.",
  };
  if (t.phase === "expired") return {
    title: "Доступ пока закрыт",
    status: "Данные и серия ждут твоего возвращения",
    lead: "Подписка снова откроет привычки, планирование и разговор с Адамом. История остаётся с тобой.",
  };
  return { title: "Подписка на Адама", status: `Пробный доступ открыт до ${t.ends_at_text}`, lead: SUB_LEAD };
}

function subOfferHtml(t) {
  return t.intro_active
    ? `<b>${t.price_stars} ⭐</b><small>${escapeHtml(`≈ ${t.intro_usd} за первый месяц, дальше ${t.renewal_price_stars} ⭐ (${t.renewal_usd})`)}</small>`
    : `<b>${t.price_stars} ⭐</b><small>${escapeHtml(`≈ ${t.renewal_usd} за месяц`)}</small>`;
}

function renderSubscriptionScreen(t, ov) {
  const copy = subCopy(t, ov);
  document.getElementById("trialPaywallVoice").innerHTML = adamVoiceHtml("lg", t.phase === "grace" || t.phase === "expired" ? "🔒" : "💳");
  document.getElementById("trialPaywallTitle").textContent = copy.title;
  document.getElementById("trialPaywallStatus").textContent = copy.status;
  document.getElementById("trialPaywallLead").textContent = copy.lead;
  document.getElementById("trialPaywallPerks").innerHTML = t.paid ? "" : SUB_PERKS.map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  document.getElementById("trialPaywallOffer").innerHTML = t.paid ? "" : subOfferHtml(t);
  const offer = document.getElementById("trialPaywallOffer");
  offer.hidden = !!t.paid;
  const pay = document.getElementById("trialPaywallPay");
  const pending = !!(t.payment_pending || ov?.payment_pending);
  pay.hidden = !!t.paid;
  pay.textContent = trialPayLabel();
  pay.disabled = pending;
  document.getElementById("trialPaywallRenewal").textContent = subRenewalText(t, ov);
  const pendingEl = document.getElementById("trialPaywallPending");
  pendingEl.hidden = !pending;
  pendingEl.textContent = pending ? `${SUB_PENDING_TEXT}${ov?.support ? ` Если за несколько минут ничего не изменится, напиши: ${ov.support}.` : ""}` : "";
  const toggle = document.getElementById("trialPaywallRenewToggle");
  toggle.hidden = !(t.paid && ov?.can_toggle);
  toggle.textContent = ov?.auto_renew ? "Отключить автоматическое списание" : "Включить автоматическое списание";
  document.getElementById("trialSummary").hidden = true;
  document.getElementById("trialPaywallSummaryBtn").hidden = !!t.paid && !t.scenario;
  subSummaryShown = false;
  document.getElementById("trialMissionText").innerHTML = SUB_MISSION.map((p) => `<p>${escapeHtml(p)}</p>`).join("");
  const note = document.getElementById("trialPaywallNote");
  const support = ov?.support ? ` Вопросы по оплате: ${ov.support}.` : " Вопросы по оплате — команда /paysupport в чате со мной.";
  note.innerHTML = `Оплата идёт через Telegram Stars. <a href="/terms" target="_blank" rel="noopener">Условия</a>.${escapeHtml(support)}`;
}

async function loadSubscriptionOverview() {
  try { return await api("/api/subscription"); } catch (_) { return null; }
}

// Экран «Подписка на Адама». source — откуда открыли (для учёта): card, timer, settings, auto, nudge. blocking — после триала.
async function openSubscriptionScreen({ source = "card", demo = null, overview = null, blocking = false } = {}) {
  const overlay = document.getElementById("trialPaywallOverlay");
  const t = demo || (state?.access?.paywall ? state.access.trial : null);
  if (!overlay || !t) return false;
  trialSheetData = t;
  trialDemo = !!demo;
  hideProductHint();
  const ov = demo ? (overview || SIM_SUB_OVERVIEW) : (overview || await loadSubscriptionOverview());
  subOverview = ov;
  renderSubscriptionScreen(t, ov);
  const closeBtn = document.getElementById("trialPaywallClose");
  closeBtn.hidden = !!blocking && !t.read_only && !demo;                        // закрыть нельзя только на закрытом доступе; в окне можно — история видна
  closeBtn.textContent = blocking && t.read_only ? "Посмотреть историю" : (demo && blocking ? "Закрыть демо" : "Не сейчас");
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  overlay.scrollTop = 0;
  const loading = document.getElementById("loadingOverlay");
  if (loading) loading.hidden = true;
  const banner = document.getElementById("bootRetryBanner");
  if (banner) banner.hidden = true;
  haptic("light");
  if (!demo) {
    trk("sub_screen_open", source, null);
    if (blocking && !trialPaywallTracked) { trialPaywallTracked = true; trk("trial_paywall_shown", t.phase, null); }
  }
  return true;
}

function openTrialSheet({ demo = null } = {}) {
  return openSubscriptionScreen({ source: "card", demo });
}

function closeTrialSheet() { /* прежнее окно заменено экраном подписки (closeTrialPaywall) */ }

function closeTrialPaywall({ dismissed = false } = {}) {
  const overlay = document.getElementById("trialPaywallOverlay");
  if (!overlay) return;
  overlay.hidden = true;
  overlay.setAttribute("aria-hidden", "true");
  if (dismissed && !trialDemo) {
    trk("sub_not_now", null, null);
    try { localStorage.setItem(SUB_DISMISS_KEY, new Date().toDateString()); } catch (_) {}
  }
  trialDemo = false;
}

// Деньги за доступ: счёт Stars → окно Telegram. «paid» — ждём, пока бот запишет платёж (successful_payment приходит отдельным апдейтом): проверяем, что подписка действительно активна.
async function waitForTrialAccess() {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      const res = await api("/api/trial");
      if (res?.trial?.paid) return true;
    } catch (_) { /* повторим */ }
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
  return false;
}

async function payTrial(source) {
  if (trialPayBusy) return;
  if (trialDemo) { showToast("🧪 Демо: оплата не открывается", "success", 1800); return; }
  const button = document.getElementById("trialPaywallPay");
  const setBusy = (busy) => { trialPayBusy = busy; if (button) button.disabled = busy; };
  setBusy(true);
  haptic("medium");
  trk("trial_pay_tap", source, null);
  try {
    const invoice = await api("/api/subscription/invoice", { method: "POST" });
    if (!tg?.openInvoice) throw new Error("telegram_payment_unavailable");
    tg.openInvoice(invoice.invoice_url, async (status) => {
      if (status !== "paid") {
        setBusy(false);
        if (status === "failed") showToast("Оплата не прошла — попробуй ещё раз", "error");
        return;
      }
      trk("trial_paid", source, null);
      haptic("success");
      if (await waitForTrialAccess()) {
        closeTrialPaywall();
        showToast("Подписка активна ✅ Спасибо, что остаёшься со мной", "praise", 4200);
        if (state?.user) { try { await loadBootstrap(); } catch (_) { /* карточка обновится при следующем заходе */ } } else { location.reload(); }
        setBusy(false);
      } else {
        // Деньги списаны, подтверждения ещё нет: показываем ожидание и запасной контакт, повторно платить не просим.
        const ov = { ...(subOverview || {}), payment_pending: true };
        subOverview = ov;
        if (trialSheetData) renderSubscriptionScreen({ ...trialSheetData, payment_pending: true }, ov);
        showToast("Платёж получен — доступ откроется чуть позже", "success", 4200);
      }
    });
  } catch (err) {
    setBusy(false);
    showToast(friendlyError(err), "error");
  }
}

async function toggleAutoRenew() {
  if (trialDemo || !subOverview?.can_toggle) return;
  const enable = !subOverview.auto_renew;
  try {
    const result = await api("/api/subscription/auto-renew", { method: "POST", body: JSON.stringify({ enabled: enable }) });
    subOverview = result;
    renderSubscriptionScreen(trialSheetData, subOverview);
    haptic("light");
    showToast(enable ? "Автоматическое списание включено" : "Автоматическое списание отключено", "success", 2600);
  } catch (err) {
    showToast(friendlyError(err), "error");
  }
}

function renderTrialSummary(summary) {
  const box = document.getElementById("trialSummary");
  box.innerHTML = `<b>${escapeHtml(summary.title)}</b>` + summary.lines.map((line) => `<p>${escapeHtml(line)}</p>`).join("")
    + (summary.note ? `<small>${escapeHtml(summary.note)}</small>` : "");
  box.hidden = false;
  document.getElementById("trialPaywallSummaryBtn").hidden = true;
}

async function showTrialSummary() {
  if (subSummaryShown) return;
  subSummaryShown = true;
  haptic("light");
  if (trialDemo) { renderTrialSummary(SIM_SUMMARY); return; }
  trk("sub_summary_open", null, null);
  try {
    renderTrialSummary(await api("/api/trial/summary"));
  } catch (err) {
    subSummaryShown = false;
    showToast(friendlyError(err), "error");
  }
}

// Блокирующий вход: сервер ответил «доступ закрыт» (403 trial_expired) или человек открыл приложение в дополнительном окне.
function showTrialPaywall(t, { demo = false } = {}) {
  if (!document.getElementById("trialPaywallOverlay")) return;
  if (!t) {                                         // сервер не прислал снимок — спрашиваем отдельным запросом (он доступен и закрытому аккаунту)
    api("/api/trial").then((res) => { if (res?.trial) showTrialPaywall(res.trial, { demo }); }).catch(() => {});
    return;
  }
  if (!demo) { state = state || {}; state.access = { ...(state.access || {}), paywall: true, trial: t }; }
  openSubscriptionScreen({ source: "auto", demo: demo ? t : null, blocking: true });
}

// Дополнительное окно после триала: при открытии приложения человек видит экран продолжения (история под ним только для чтения). Если он уже нажал «Не сейчас» сегодня, второй раз сам экран не открывается.
let graceScreenShown = false;
function maybeShowGraceScreen() {
  handleTrialEntry();                                                        // переход из сообщения триала (?sub=1&m=…) — раньше автоматического экрана окна
  const access = state?.access;
  if (graceScreenShown || !access || access.state !== "grace" || !access.trial) return;
  let dismissed = false;
  try { dismissed = localStorage.getItem(SUB_DISMISS_KEY) === new Date().toDateString(); } catch (_) {}
  if (dismissed) return;
  graceScreenShown = true;
  showTrialPaywall(access.trial);
}

// Симуляция «карточка на Главной»: подставляем демо-снимок в состояние (только в памяти страницы) и рисуем карточку.
function showTrialCardDemo(t) {
  closeTrialPaywall();
  state.access = { ...(state.access || {}), paywall: true, trial: t };
  renderTrialCard();
  document.getElementById("trialCard")?.scrollIntoView({ block: "center" });
}

// ----- Предложение подписки внутри приложения (db/trial_flow.py::app_offer, ТЗ §12) -----
// Во второй день пробного периода, после настоящего действия (отметка привычки, работа с планом), и не больше одного раза в день: спокойная карточка с фактом.
// Не открывается сама по таймеру и не повторяется после «Не сейчас» (то же правило, что у экрана продолжения: SUB_DISMISS_KEY). Сервер записывает показ в журнал.
const SIM_OFFER = { key: "d2_app_habit", text: "Первая отметка «Зарядка» есть — с неё и начинается ритм. Я могу помогать его держать.", terms: "Условия подписки", later: "Не сейчас" };
let trialOfferData = null;
let trialOfferDemo = false;
let trialOfferTimer = null;
let trialOfferHideTimer = null;
const trialOfferAsked = new Set();

function trialDismissedToday() {
  try { return localStorage.getItem(SUB_DISMISS_KEY) === new Date().toDateString(); } catch (_) { return false; }
}

function hideTrialOffer() {
  const card = document.getElementById("trialOfferCard");
  clearTimeout(trialOfferHideTimer);
  if (card) card.hidden = true;
  trialOfferData = null;
}

function showTrialOffer(offer, { demo = false } = {}) {
  const card = document.getElementById("trialOfferCard");
  if (!card || !offer) return;
  trialOfferData = offer;
  trialOfferDemo = !!demo;
  document.getElementById("trialOfferVoice").innerHTML = adamVoiceHtml("sm");
  document.getElementById("trialOfferText").textContent = offer.text;
  document.getElementById("trialOfferGo").textContent = offer.terms || "Условия подписки";
  document.getElementById("trialOfferLater").textContent = offer.later || "Не сейчас";
  card.hidden = false;
  haptic("light");
  clearTimeout(trialOfferHideTimer);
  if (!demo) trialOfferHideTimer = setTimeout(hideTrialOffer, 20000);       // не отреагировал — карточка уходит сама, без отметки «Не сейчас»
}

function sendTrialOfferEvent(event) {
  if (trialOfferDemo || !trialOfferData) return;
  api("/api/trial/offer-event", { method: "POST", body: JSON.stringify({ place: "app", key: trialOfferData.key, event }) }).catch(() => {});
}

function scheduleTrialOffer(trigger) {
  const t = state?.access?.paywall ? state.access.trial : null;
  if (!t || t.phase !== "day2" || t.paid || trialDemo || trialDismissedToday() || trialOfferAsked.has(trigger)) return;
  trialOfferAsked.add(trigger);
  clearTimeout(trialOfferTimer);
  let waited = 0;
  const attempt = async () => {
    if (celebrationOverlayOpen()) {                                          // праздники и окна важнее: ждём, пока уйдут
      waited += 1;
      if (waited <= 12) trialOfferTimer = setTimeout(attempt, 1500);
      return;
    }
    try {
      const res = await api("/api/trial/offer", { method: "POST", body: JSON.stringify({ trigger }) });
      if (res?.offer) showTrialOffer(res.offer);
    } catch (_) { /* предложение не обязательно */ }
  };
  trialOfferTimer = setTimeout(attempt, 2800);
}

// Переход из сообщения триала в Telegram (keyboards.py::trial_keyboard): ?m=<ключ сообщения> — учёт перехода, ?sub=1 — сразу экран подписки.
function handleTrialEntry() {
  let params;
  try { params = new URLSearchParams(location.search); } catch (_) { return; }
  const key = (params.get("m") || "").replace(/[^a-z0-9_]/gi, "").slice(0, 40);
  const wantsSub = params.get("sub") === "1";
  if (!key && !wantsSub) return;
  params.delete("m");
  params.delete("sub");
  try { const rest = params.toString(); history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : "") + location.hash); } catch (_) {}
  if (key) trk("trial_msg_open", key, null);
  if (wantsSub && state?.access?.paywall && state.access.trial) {
    graceScreenShown = true;                                                 // нажали сами — экран открываем даже после сегодняшнего «Не сейчас»
    if (state.access.state === "grace") showTrialPaywall(state.access.trial);
    else openSubscriptionScreen({ source: "push" });
  }
}

function initTrial() {
  document.getElementById("trialCard")?.addEventListener("click", () => {
    haptic("light");
    if (document.getElementById("trialCardTimer") && !document.getElementById("trialCardTimer").hidden) trk("trial_timer_tap", null, null);
    try { localStorage.setItem("adam_trial_timer_hint", "1"); } catch (_) {}
    openSubscriptionScreen({ source: document.getElementById("trialCardTimer")?.hidden === false ? "timer" : "card" });
  });
  document.getElementById("subscriptionSettingsBtn")?.addEventListener("click", () => {
    document.getElementById("settingsOverlayClose")?.click();
    openSubscriptionScreen({ source: "settings" });
  });
  document.getElementById("trialPaywallPay")?.addEventListener("click", () => payTrial("paywall"));
  document.getElementById("trialPaywallClose")?.addEventListener("click", () => { haptic("light"); closeTrialPaywall({ dismissed: true }); });
  document.getElementById("trialPaywallRenewToggle")?.addEventListener("click", toggleAutoRenew);
  document.getElementById("trialPaywallSummaryBtn")?.addEventListener("click", showTrialSummary);
  document.getElementById("trialMission")?.addEventListener("toggle", (e) => { if (e.target.open && !trialDemo) trk("sub_mission_open", null, null); });
  document.getElementById("trialOfferGo")?.addEventListener("click", () => {
    haptic("light");
    const demo = trialOfferDemo;
    sendTrialOfferEvent("clicked");
    hideTrialOffer();
    if (demo) { showToast("🧪 Демо: экран подписки не открывается", "success", 1800); return; }
    openSubscriptionScreen({ source: "offer" });
  });
  document.getElementById("trialOfferLater")?.addEventListener("click", () => {
    haptic("light");
    sendTrialOfferEvent("dismissed");
    if (!trialOfferDemo) { try { localStorage.setItem(SUB_DISMISS_KEY, new Date().toDateString()); } catch (_) {} }
    hideTrialOffer();
  });
}

// ===================== ПАРТНЁРСКАЯ ПРОГРАММА И КАРТОЧКА ПРИГЛАСИВШЕГО (db/partners.py, ТЗ §20–§24) =====================
// Участник пилота (state.is_partner) видит в «Настройках» свой раздел: условия, цифры, ссылки (личная и подписанные — чтобы сравнивать площадки) и историю начислений без имён
// приглашённых. Новичок, пришедший по ссылке участника, один раз видит карточку пригласившего: добавить в друзья и подписаться он решает сам, ник можно скопировать.
// Симуляции (админка → «Окна и анимации»): partner_screen, inviter_card — демо-данные, ничего не отправляется.
const SIM_PARTNER = {
  active: true, subscription_active: true, share_percent: 40, window_days: 30, valid_until: null, invited: 6, buyers: 2, purchases: 3, currency: "XTR", min_payout: 100,
  balance: { accrued: 244, hold: 61, waiting: 61, payable: 122, paid: 61, clawback: 0 },
  history: [
    { invitee: "Приглашённый №4", amount: 61, base: 153, status: "accrued", status_label: "начислено", reason: null, at: "2026-10-27 12:10:00" },
    { invitee: "Приглашённый №2", amount: 61, base: 153, status: "hold", status_label: "на проверке", reason: "подозрительная регистрация: всплеск приходов по одной ссылке", at: "2026-10-26 09:30:00" },
  ],
  links: [
    { id: 1, code: "demo1234", label: "Личная", is_personal: 1, touches: 14, invitees: 6, buyers: 2 },
    { id: 2, code: "demo5678", label: "Канал про привычки", is_personal: 0, touches: 9, invitees: 3, buyers: 1 },
  ],
  payouts: [],
};
const SIM_INVITER = { user_id: 1, name: "Аня", handle: "@anya_pro" };
const INVITER_CLOSED_KEY = "adam_inviter_card_closed";
let partnerData = null;
let partnerDemo = false;
let inviterDemo = false;

function partnerLinkUrl(code) {
  return state?.bot_username ? `https://t.me/${state.bot_username}?start=p_${code}` : "";
}

function partnerLinkText(link) {
  return `${link.label}: ${link.touches} ${pluralRu(link.touches, "переход", "перехода", "переходов")}, приглашённых — ${link.invitees}, купили — ${link.buyers}`;
}

function renderPartnerScreen(data) {
  const b = data.balance || {};
  const until = data.valid_until ? ` Условия действуют до ${escapeHtml(String(data.valid_until).slice(0, 10))}.` : "";
  document.getElementById("partnerVoice").innerHTML = adamVoiceHtml("lg", "🤝");
  document.getElementById("partnerTerms").innerHTML = escapeHtml(
    `Ты получаешь ${data.share_percent}% от полной суммы покупок тех, кто пришёл по твоим ссылкам, в первые ${data.window_days} ${pluralRu(data.window_days, "день", "дня", "дней")} после перехода. `
    + `Выплаты ручные, от ${data.min_payout} ⭐.${until}`) + (data.active && data.subscription_active ? "" : `<br><b>${escapeHtml(data.subscription_active ? "Условия сейчас не действуют." : "Нужна действующая подписка: пока её нет, начисления ждут проверки.")}</b>`);
  document.getElementById("partnerGrid").innerHTML = [
    ["Приглашено", data.invited], ["Купили", data.buyers], ["Начислено", `${b.accrued} ⭐`], ["К выплате", `${b.payable} ⭐`],
    ["Ждёт срока", `${b.waiting} ⭐`], ["На проверке", `${b.hold} ⭐`], ["Выплачено", `${b.paid} ⭐`], ["Покупок", data.purchases],
  ].map(([label, value]) => `<div><b>${escapeHtml(String(value))}</b><small>${escapeHtml(label)}</small></div>`).join("");
  document.getElementById("partnerLinks").innerHTML = (data.links || []).map((link) => `
    <div class="partner-link" data-code="${escapeHtml(link.code)}"><b>${escapeHtml(link.label)}</b>
      <span><button type="button" data-partner-copy="${escapeHtml(link.code)}" data-personal="${link.is_personal}">Копировать</button><button type="button" class="partner-link__share" data-partner-share="${escapeHtml(link.code)}">Отправить</button></span>
      <small>${escapeHtml(partnerLinkText(link))}</small></div>`).join("");
  const history = data.history || [];
  document.getElementById("partnerHistory").innerHTML = history.length
    ? history.map((row) => `<div class="partner-row"><span>${escapeHtml(row.invitee)}<small>${escapeHtml(String(row.at || "").slice(0, 10))}${row.reason ? ` · ${escapeHtml(row.reason)}` : ""}</small></span><span>${row.amount} ⭐<small>${escapeHtml(row.status_label)}</small></span></div>`).join("")
    : `<div class="partner-empty">Пока начислений нет: они появятся после первых покупок приглашённых.</div>`;
}

async function openPartnerScreen({ demo = null } = {}) {
  const overlay = document.getElementById("partnerOverlay");
  if (!overlay) return;
  partnerDemo = !!demo;
  try {
    partnerData = demo || await api("/api/partner/stats");
  } catch (err) {
    showToast(friendlyError(err), "error");
    return;
  }
  renderPartnerScreen(partnerData);
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  overlay.scrollTop = 0;
  haptic("light");
}

function closePartnerScreen() {
  const overlay = document.getElementById("partnerOverlay");
  if (!overlay) return;
  overlay.hidden = true;
  overlay.setAttribute("aria-hidden", "true");
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* запасной путь ниже */ }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch (_) { return false; }
}

async function createPartnerLink() {
  const input = document.getElementById("partnerLinkLabel");
  const label = (input?.value || "").trim();
  if (!label) { showToast("Напиши, где разместишь ссылку", "error"); return; }
  if (partnerDemo) { showToast("🧪 Демо: ссылка не создаётся", "success", 1800); return; }
  try {
    const result = await api("/api/partner/link", { method: "POST", body: JSON.stringify({ label }) });
    partnerData = { ...partnerData, links: result.links };
    input.value = "";
    renderPartnerScreen(partnerData);
    haptic("success");
    showToast("Ссылка создана", "success", 1800);
  } catch (err) {
    const texts = { too_many_links: "Ссылок уже много — хватит десяти", empty_label: "Напиши, где разместишь ссылку" };
    showToast(texts[err?.data?.error] || friendlyError(err), "error");
  }
}

function renderPartnerRow() {
  const row = document.getElementById("partnerSettingsRow");
  if (row) row.hidden = !state?.is_partner;
}

function renderInviterCard(card = state?.inviter_card, { demo = false } = {}) {
  const el = document.getElementById("inviterCard");
  if (!el) return;
  let closed = false;
  try { closed = !demo && localStorage.getItem(INVITER_CLOSED_KEY) === "1"; } catch (_) {}
  if (!card || closed) { el.hidden = true; return; }
  inviterDemo = demo;
  document.getElementById("inviterVoice").innerHTML = adamVoiceHtml("sm");
  document.getElementById("inviterWho").textContent = `${card.name}${card.handle ? ` · ${card.handle}` : ""}`;
  document.getElementById("inviterCopy").hidden = !card.handle;
  el.hidden = false;
  if (!demo && !el.dataset.tracked) { el.dataset.tracked = "1"; trk("inviter_card_shown", null, null); }
}

function initPartner() {
  document.getElementById("partnerSettingsBtn")?.addEventListener("click", () => {
    document.getElementById("settingsOverlayClose")?.click();
    openPartnerScreen();
  });
  document.getElementById("partnerClose")?.addEventListener("click", () => { haptic("light"); closePartnerScreen(); });
  document.getElementById("partnerLinkAdd")?.addEventListener("click", createPartnerLink);
  document.getElementById("partnerOverlay")?.addEventListener("click", async (e) => {
    const copy = e.target.closest("[data-partner-copy]");
    const share = e.target.closest("[data-partner-share]");
    if (!copy && !share) return;
    const code = (copy || share).dataset.partnerCopy || (copy || share).dataset.partnerShare;
    const url = partnerLinkUrl(code);
    if (!url) { showToast("Не получилось собрать ссылку — попробуй чуть позже", "error"); return; }
    haptic("light");
    if (copy) {
      if (!partnerDemo) trk("partner_link_copy", copy.dataset.personal === "1" ? "personal" : "labeled", null);
      showToast((await copyText(url)) ? "Ссылка скопирована" : "Не получилось скопировать", "success", 1800);
    } else {
      if (!partnerDemo) trk("partner_link_copy", "share", null);
      const shareUrl = telegramShareUrl("Присоединяйся к ADAM — привычки, план дня и наставник:", url);
      if (tg && typeof tg.openTelegramLink === "function") tg.openTelegramLink(shareUrl); else window.open(shareUrl, "_blank");
    }
  });
  const closeCard = () => {
    document.getElementById("inviterCard").hidden = true;
    if (!inviterDemo) { try { localStorage.setItem(INVITER_CLOSED_KEY, "1"); } catch (_) {} trk("inviter_card_action", "close", null); }
  };
  document.getElementById("inviterClose")?.addEventListener("click", () => { haptic("light"); closeCard(); });
  document.getElementById("inviterCopy")?.addEventListener("click", async () => {
    haptic("light");
    const card = inviterDemo ? SIM_INVITER : state?.inviter_card;
    if (!card?.handle) return;
    if (!inviterDemo) trk("inviter_card_action", "copy", null);
    showToast((await copyText(card.handle)) ? "Ник скопирован" : "Не получилось скопировать", "success", 1800);
  });
  document.getElementById("inviterFriend")?.addEventListener("click", async () => {
    haptic("light");
    if (inviterDemo) { showToast("🧪 Демо: никто не добавляется", "success", 1800); return; }
    try {
      await api("/api/partner/inviter/friend", { method: "POST" });
      showToast("Вы теперь друзья", "praise", 2400);
      closeCard();
    } catch (err) { showToast(friendlyError(err), "error"); }
  });
  document.getElementById("inviterFollow")?.addEventListener("click", async () => {
    haptic("light");
    const card = inviterDemo ? SIM_INVITER : state?.inviter_card;
    if (inviterDemo || !card) { showToast("🧪 Демо: никто не подписывается", "success", 1800); return; }
    try {
      await api(`/api/users/${card.user_id}/follow`, { method: "POST" });
      trk("inviter_card_action", "follow", null);
      showToast("Подписка оформлена", "praise", 2400);
      closeCard();
    } catch (err) { showToast(friendlyError(err), "error"); }
  });
  renderPartnerRow();
  renderInviterCard();
}

// ===================== ИЗМЕНЕНИЕ ПРИВЫЧКИ =====================
// Привычек может быть много, а поменять нужно одну: в раскрытой строке (✏️) у каждой привычки есть кнопка «Изменить» — окно
// с названием, категорией, важностью (⭐) и временем напоминания. Сохраняет PUT /api/habits/{id} только для этой привычки,
// остальные не трогает. Симуляция habit_edit (админка → «Окна и анимации») открывает то же окно на демо-привычке и ничего не сохраняет.
const HABIT_EDIT_SIM = { id: 0, title: "Читать 20 минут", category: "study", priority: 2, planned_time: "21:30", place: 3, total: 5 };
let habitEdit = null;

function renderHabitEditCategories() {
  const box = document.getElementById("habitEditCats");
  if (!box || !habitEdit) return;
  const chip = (value, label) =>
    `<button type="button" class="habit-edit-chip${habitEdit.category === value ? " is-active" : ""}" role="radio" aria-checked="${habitEdit.category === value}" data-hec="${value}">${label}</button>`;
  box.innerHTML = chip("", "Нет") +
    Object.entries(HABIT_CATEGORY_META).map(([key, meta]) => chip(key, `${meta.emoji} ${meta.label}`)).join("");
}

function renderHabitEditPriority() {
  const btn = document.getElementById("habitEditPriority");
  if (!btn || !habitEdit) return;
  const on = habitEdit.priority === 2;
  btn.classList.toggle("is-on", on);
  btn.setAttribute("aria-pressed", String(on));
  const star = document.getElementById("habitEditStar");
  if (star) star.textContent = on ? "★" : "☆";
}

// Место в списке: «Выше / Ниже / В начало» двигают ТОЛЬКО эту привычку (POST /api/habits/{id}/move), остальные сохраняют порядок. Двигается сразу,
// без «Сохранить»; список на Главной перестраивается по ответу сервера. В симуляции порядок меняется только в окне.
function renderHabitEditOrder() {
  const s = habitEdit;
  if (!s) return;
  const hint = document.getElementById("habitEditOrderHint");
  const alone = s.total < 2;
  if (hint) hint.textContent = alone ? "Привычка одна — двигать нечего" : `Сейчас ${s.place} из ${s.total}`;
  const set = (id, disabled) => { const btn = document.getElementById(id); if (btn) btn.disabled = disabled; };
  set("habitEditUp", alone || s.place <= 1);
  set("habitEditTop", alone || s.place <= 1);
  set("habitEditDown", alone || s.place >= s.total);
}

function applyHabitOrder(order) {
  const rank = new Map(order.map((id, index) => [id, index]));
  state.habits = [...(state.habits || [])].sort((a, b) => (rank.has(a.id) ? rank.get(a.id) : 1e9) - (rank.has(b.id) ? rank.get(b.id) : 1e9));
  renderHabits();
}

async function moveHabitEdit(to) {
  const s = habitEdit;
  if (!s || s.busy) return;
  const target = { up: Math.max(1, s.place - 1), down: Math.min(s.total, s.place + 1), top: 1 }[to];
  if (!target || target === s.place) return;
  if (s.sim) { s.place = target; haptic("light"); renderHabitEditOrder(); return; }
  s.busy = true;
  try {
    const res = await api(`/api/habits/${s.id}/move`, { method: "POST", body: JSON.stringify({ to }) });
    s.place = res.place;
    applyHabitOrder(res.order);
    haptic("light");
    trk("habit_moved", to, null);
  } catch (err) {
    showToast(friendlyError(err), "error");
  } finally {
    s.busy = false;
    renderHabitEditOrder();
  }
}

function openHabitEdit(habitId, { habit = null, sim = false } = {}) {
  const sheet = document.getElementById("habitEditSheet");
  if (!sheet || habitEdit) return false;
  const h = habit || (state.habits || []).find((x) => x.id === habitId);
  if (!h) return false;
  const category = h.category && HABIT_CATEGORY_META[h.category] ? h.category : "";
  const priority = h.priority === 2 ? 2 : 1;
  // Место в списке: у настоящей привычки — по текущему порядку на экране, у демо — из данных сцены.
  const list = (state.habits || []).filter((x) => !pendingDeleteHabitIds.has(x.id));
  const place = sim ? (h.place || 1) : Math.max(1, list.findIndex((x) => x.id === h.id) + 1);
  const total = sim ? (h.total || 1) : Math.max(1, list.length);
  habitEdit = { id: h.id, sim, busy: false, category, priority, place, total, original: { title: h.title, category, priority, time: h.planned_time || "" } };
  hideProductHint();
  renderHabitEditOrder();
  document.getElementById("habitEditTitle").value = h.title || "";
  document.getElementById("habitEditTime").value = h.planned_time || "";
  document.getElementById("habitEditSave").disabled = false;
  renderHabitEditCategories();
  renderHabitEditPriority();
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
  haptic("light");
  if (!sim) trk("habit_edit_open", null, null);
  return true;
}

function closeHabitEdit() {
  const sheet = document.getElementById("habitEditSheet");
  if (!sheet || !habitEdit) return;
  habitEdit = null;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!habitEdit) sheet.hidden = true; }, 230);
}

async function saveHabitEdit() {
  const s = habitEdit;
  if (!s || s.busy) return;
  const titleInput = document.getElementById("habitEditTitle");
  const title = (titleInput.value || "").replace(/\s+/g, " ").trim();
  if (title.length < 2) {
    showToast("Название — минимум 2 символа", "error");
    titleInput.focus();
    return;
  }
  const time = document.getElementById("habitEditTime").value || "";
  const changed = [];
  if (title !== s.original.title) changed.push("title");
  if (s.category !== s.original.category) changed.push("category");
  if (s.priority !== s.original.priority) changed.push("priority");
  if (time !== s.original.time) changed.push("time");
  if (!changed.length) { closeHabitEdit(); return; }
  if (s.sim) {
    showToast("Так выглядит сохранение — в симуляции ничего не меняется", "success", 3200);
    closeHabitEdit();
    return;
  }
  const saveBtn = document.getElementById("habitEditSave");
  s.busy = true;
  saveBtn.disabled = true;
  try {
    await api(`/api/habits/${s.id}`, {
      method: "PUT",
      body: JSON.stringify({ title, category: s.category, priority: s.priority, planned_time: time }),
    });
    trk("habit_edit_save", null, { fields: changed.join(",") });
    haptic("success");
    showToast("Привычка обновлена", "success");
    closeHabitEdit();
    await loadBootstrap();
  } catch (err) {
    showToast(friendlyError(err), "error");
    s.busy = false;
    saveBtn.disabled = false;
  }
}

function initHabitEdit() {
  const sheet = document.getElementById("habitEditSheet");
  if (!sheet) return;
  document.getElementById("habitEditBackdrop")?.addEventListener("click", closeHabitEdit);
  document.getElementById("habitEditCancel")?.addEventListener("click", () => { haptic("light"); closeHabitEdit(); });
  document.getElementById("habitEditSave")?.addEventListener("click", saveHabitEdit);
  document.getElementById("habitEditUp")?.addEventListener("click", () => moveHabitEdit("up"));
  document.getElementById("habitEditDown")?.addEventListener("click", () => moveHabitEdit("down"));
  document.getElementById("habitEditTop")?.addEventListener("click", () => moveHabitEdit("top"));
  document.getElementById("habitEditTitle")?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveHabitEdit(); } });
  document.getElementById("habitEditCats")?.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-hec]");
    if (!chip || !habitEdit) return;
    habitEdit.category = chip.dataset.hec;
    haptic("light");
    renderHabitEditCategories();
  });
  document.getElementById("habitEditPriority")?.addEventListener("click", () => {
    if (!habitEdit) return;
    habitEdit.priority = habitEdit.priority === 2 ? 1 : 2;
    haptic("light");
    renderHabitEditPriority();
  });
  document.getElementById("habitEditTimeClear")?.addEventListener("click", () => {
    document.getElementById("habitEditTime").value = "";
    haptic("light");
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && habitEdit) closeHabitEdit(); });
}

// ===================== ЗАМОРОЗКА ДРУГУ (взаимопомощь, как в Duolingo) =====================
// У друга кончились заморозки (последняя ушла на спасение дня, а серия идёт) — в карточке «Друзья» справа от его имени появляется серая
// снежинка. Тап открывает окно «Надо помочь другу!»: «Отправить одну за 🪙 200» или «Нет, спасибо». То же окно один раз за сессию само
// появляется при входе, если такой друг есть (по одному разу в день на друга, и только если монет хватает). Подарок и правила — db/gifts.py
// (kind "freeze": только друзьям, цену платит отправитель, у друга запас не больше двух, в день ему дарят одну). У кого сколько заморозок —
// видно в списке друзей: синяя снежинка с цифрой. sim — симуляция из админки (SIM_SCENES): ничего не отправляется и не списывается.
const FREEZE_FLAKE = (cls) =>
  `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2.5v19M3.8 7.25l16.4 9.5M3.8 16.75l16.4-9.5M9.6 4.6L12 6.6l2.4-2M9.6 19.4L12 17.4l2.4 2" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

// Значок заморозок у друга: серая кнопка, если они кончились (тап — подарить), синяя снежинка с числом, если запас есть. Нет заморозок и не было — пусто.
function friendFreezeHtml(f) {
  const z = f.freeze;
  if (!z) return "";
  if (z.needs_help) {
    return `<button type="button" class="friend-freeze friend-freeze--help" data-freeze-help="${Number(f.telegram_id)}" aria-label="У друга кончились заморозки — подарить одну">${FREEZE_FLAKE("friend-freeze__icon")}</button>`;
  }
  const have = Number(z.balance) || 0;
  return have > 0
    ? `<span class="friend-freeze friend-freeze--have" title="Заморозок в запасе: ${have}">${FREEZE_FLAKE("friend-freeze__icon")}<b>${have}</b></span>`
    : "";
}

let freezeHelp = null;            // { friend, price, coins, sim, step: "offer" | "sent", busy }
let freezeHelpPrompted = false;   // окно при входе — один раз за сессию

function freezeHelpCoins() {
  return Number(state?.user?.xp) || 0;
}

function renderFreezeHelp() {
  const s = freezeHelp;
  if (!s) return;
  const name = s.friend.first_name || "Друг";
  const card = document.getElementById("freezeHelpCard");
  const send = document.getElementById("freezeHelpSend");
  const affordable = s.coins >= s.price;
  card.dataset.state = s.step;
  document.getElementById("freezeHelpBalance").innerHTML = `${ADAM_COIN_ICON}<b>${s.coins}</b>`;
  const avatar = document.getElementById("freezeHelpAvatar");
  avatar.className = `freeze-help-avatar ${avatarFrameClass(s.friend)}`;
  avatar.innerHTML = avatarInner(s.friend);
  const streak = Number(s.friend.streak) || 0;
  if (s.step === "sent") {
    document.getElementById("freezeHelpTitle").textContent = "Заморозка отправлена!";
    document.getElementById("freezeHelpSub").textContent = streak > 0
      ? `Серия 🔥 ${streak} под защитой: один пропущенный день её не погасит.` : `${name} теперь под защитой от одного пропущенного дня.`;
    send.textContent = "Отлично";
    send.disabled = false;
  } else {
    document.getElementById("freezeHelpTitle").textContent = `${name} теперь без заморозок! Надо помочь другу!`;
    document.getElementById("freezeHelpSub").textContent = streak > 0
      ? `Серия 🔥 ${streak} под угрозой: один пропущенный день — и она погаснет.` : "";
    send.innerHTML = affordable ? `Отправить одну за ${ADAM_COIN_ICON}${s.price}` : `Не хватает монет: нужно ${s.price}`;
    send.disabled = !affordable || s.busy;
  }
}

function openFreezeHelp({ friend, price, coins = null, sim = false, sent = false } = {}) {
  const overlay = document.getElementById("freezeHelpOverlay");
  if (!overlay || freezeHelp || !friend) return false;
  freezeHelp = { friend, price: Number(price) || 200, coins: coins === null ? freezeHelpCoins() : Number(coins), sim, step: sent ? "sent" : "offer", busy: false };
  hideProductHint();
  renderFreezeHelp();
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  void overlay.offsetWidth;
  overlay.classList.add("show");
  haptic("light");
  return true;
}

function closeFreezeHelp() {
  const s = freezeHelp;
  if (!s) return;
  const overlay = document.getElementById("freezeHelpOverlay");
  overlay.classList.remove("show");
  overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!freezeHelp) overlay.hidden = true; }, 300);
  freezeHelp = null;
  haptic("light");
}

async function sendFreezeHelp() {
  const s = freezeHelp;
  if (!s || s.busy || s.step !== "offer" || s.coins < s.price) return;
  s.busy = true;
  renderFreezeHelp();
  try {
    if (s.sim) {
      s.coins -= s.price;
    } else {
      const result = await api(`/api/users/${Number(s.friend.telegram_id)}/gift`, { method: "POST", body: JSON.stringify({ kind: "freeze" }) });
      applyActionPatch(result);
      s.coins = freezeHelpCoins();
      const friend = (friendsData?.friends || []).find((f) => Number(f.telegram_id) === Number(s.friend.telegram_id));
      if (friend) { friend.freeze = { balance: 1, needs_help: false }; renderFriendsCard(); }
    }
  } catch (err) {
    s.busy = false;
    showToast(friendlyError(err), "error");
    if (["receiver_full", "receiver_limit", "not_friends"].includes(err?.data?.error)) { closeFreezeHelp(); loadFriendsCard(); }
    else renderFreezeHelp();
    return;
  }
  s.busy = false;
  s.step = "sent";
  renderFreezeHelp();
  haptic("success");
  playChime();
}

// Окно при входе: один запрос и одно окно за сессию, по одному разу в день на друга, только если монет хватает и ничто другое не открыто.
async function maybeShowFreezeHelp(attempt = 0) {
  if (freezeHelpPrompted || freezeHelp || labMode || simMode) return;
  const busy = !state?.user || introChainPending() || adamIntro || state?.adam_intro?.show || celebrationOverlayOpen();
  if (busy) { if (attempt < 3) setTimeout(() => maybeShowFreezeHelp(attempt + 1), 7000); return; }
  await rescueHelpReady;                    // серию друга спасти важнее заморозки: если окно «спасти» открылось, это не показываем
  if (rescueHelp) return;
  freezeHelpPrompted = true;
  let data;
  try { data = await api("/api/freeze-help", { timeoutMs: 8000 }); } catch (_) { return; }
  const price = Number(data?.price) || 200;
  if (freezeHelpCoins() < price) return;
  const today = new Date().toLocaleDateString("sv");
  const key = (id) => `adam_freeze_help_${id}`;
  const friend = (data.friends || []).find((f) => { try { return localStorage.getItem(key(f.telegram_id)) !== today; } catch (_) { return true; } });
  if (!friend || celebrationOverlayOpen()) return;
  try { localStorage.setItem(key(friend.telegram_id), today); } catch (_) { /* без памяти окно просто покажется снова в следующий раз */ }
  trk("freeze_help_shown");
  openFreezeHelp({ friend, price });
}

function initFreezeHelp() {
  document.getElementById("freezeHelpSend")?.addEventListener("click", () => {
    if (freezeHelp?.step === "sent") closeFreezeHelp();
    else sendFreezeHelp();
  });
  document.getElementById("freezeHelpSkip")?.addEventListener("click", () => closeFreezeHelp());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && freezeHelp) closeFreezeHelp(); });
}

// ===================== СПАСЕНИЕ СЕРИИ ДРУГА (db/rescue.py) =====================
// Серия друга погасла, но её ещё можно вернуть — как «дружеский забег ещё можно спасти» в Duolingo. В карточке «Друзья» рядом с именем
// появляется значок погасшей серии (тап — окно), то же окно один раз за сессию само показывается при входе (на одного друга не чаще раза
// в 5 часов — это и есть «Напомнить позже»). «Восстановить бесплатно» — POST /api/users/{id}/rescue: серия возвращается сразу, другу уходит
// пуш. Правила (только друзьям, свежий срыв, раз в месяц на спасателя, раз в две недели на того, кого спасают) считает сервер.
// sim — симуляция из админки (SIM_SCENES): ничего не отправляется.
const RESCUE_FLAME = (cls) =>
  `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 2.5c.8 3.4-1.5 5.2-3.3 7.4C7.2 11.7 6 13.4 6 15.6a6 6 0 0 0 12 0c0-2.5-1.1-4.2-2.4-5.8-.3 1.5-1 2.4-2 2.9.4-3.6-.4-7.6-1.6-10.2z" fill="currentColor"/><path class="rescue-help-crack" d="M13.4 7.2l-2.3 3.8 2.3 1.7-2.5 4.5" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const RESCUE_REMIND_MS = 5 * 3600 * 1000;
let rescueHelp = null;                       // { friend, sim, step: "offer" | "sent", busy, restored }
let rescueHelpPrompted = false;              // окно при входе — один раз за сессию
let rescueHelpReady = Promise.resolve();     // окно «заморозка другу» ждёт, пока решится, показывать ли это

// Значок рядом с именем друга, чью серию ещё можно спасти (тап — окно).
function friendRescueHtml(f) {
  if (!f.rescue) return "";
  return `<button type="button" class="friend-rescue" data-rescue-help="${Number(f.telegram_id)}" aria-label="Серию друга ещё можно спасти">${RESCUE_FLAME("friend-rescue__icon")}</button>`;
}

function renderRescueHelp() {
  const s = rescueHelp;
  if (!s) return;
  const name = s.friend.first_name || "Друг";
  const lost = Number(s.friend.lost_streak) || 0;
  document.getElementById("rescueHelpCard").dataset.state = s.step;
  const avatar = document.getElementById("rescueHelpAvatar");
  avatar.className = `freeze-help-avatar ${avatarFrameClass(s.friend)}`;
  avatar.innerHTML = avatarInner(s.friend);
  const send = document.getElementById("rescueHelpSend");
  if (s.step === "sent") {
    document.getElementById("rescueHelpTitle").textContent = "Серия возвращена!";
    document.getElementById("rescueHelpSub").textContent = `🔥 ${s.restored || lost} снова горит у друга. Это сделал ты.`;
    send.textContent = "Отлично";
    send.disabled = false;
  } else {
    // неразрывный пробел: «в 12 дней» не рвётся на «в» и «12 дней» в разных строках
    document.getElementById("rescueHelpTitle").textContent = `${name} потерял(а) серию в ${pairDaysWord(lost).replace(" ", " ")}. Но её ещё можно спасти!`;
    document.getElementById("rescueHelpSub").textContent = "Вернуть её ты успеешь, пока не поздно. Такая помощь — раз в месяц и ничего не стоит.";
    send.textContent = "Восстановить бесплатно";
    send.disabled = s.busy;
  }
}

function openRescueHelp({ friend, sim = false, sent = false, restored = 0 } = {}) {
  const overlay = document.getElementById("rescueHelpOverlay");
  if (!overlay || !friend) return false;
  if (rescueHelp) {
    if (!sim) return false;
    rescueHelp = null;
  }
  rescueHelp = { friend, sim, step: sent ? "sent" : "offer", busy: false, restored: Number(restored) || 0 };
  hideProductHint();
  renderRescueHelp();
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  void overlay.offsetWidth;
  overlay.classList.add("show");
  haptic("light");
  return true;
}

function closeRescueHelp(instant = false) {
  if (!rescueHelp) return;
  const overlay = document.getElementById("rescueHelpOverlay");
  overlay.classList.remove("show");
  overlay.setAttribute("aria-hidden", "true");
  if (instant) overlay.hidden = true;
  else setTimeout(() => { if (!rescueHelp) overlay.hidden = true; }, 300);
  rescueHelp = null;
  if (!instant) haptic("light");
}

async function doRescueHelp() {
  const s = rescueHelp;
  if (!s || s.busy || s.step !== "offer") return;
  s.busy = true;
  renderRescueHelp();
  try {
    if (s.sim) {
      s.restored = Number(s.friend.lost_streak) || 0;
    } else {
      const res = await api(`/api/users/${Number(s.friend.telegram_id)}/rescue`, { method: "POST", body: JSON.stringify({}) });
      s.restored = Number(res?.streak) || Number(s.friend.lost_streak) || 0;
      const friend = (friendsData?.friends || []).find((f) => Number(f.telegram_id) === Number(s.friend.telegram_id));
      if (friend) { delete friend.rescue; friend.streak = s.restored; renderFriendsCard(); }
    }
  } catch (err) {
    s.busy = false;
    showToast(friendlyError(err), "error");
    if (["helper_limit", "cooldown", "not_available", "not_friends"].includes(err?.data?.error)) { closeRescueHelp(); loadFriendsCard(); }
    else renderRescueHelp();
    return;
  }
  s.busy = false;
  s.step = "sent";
  renderRescueHelp();
  haptic("success");
  playChime();
}

// Окно при входе: один запрос и одно окно за сессию, на одного друга не чаще раза в RESCUE_REMIND_MS, и только если ничто другое не открыто.
async function maybeShowRescueHelp(attempt = 0) {
  if (rescueHelpPrompted || rescueHelp || labMode || simMode) return;
  const busy = !state?.user || introChainPending() || adamIntro || state?.adam_intro?.show || celebrationOverlayOpen();
  if (busy) { if (attempt < 3) setTimeout(() => maybeShowRescueHelp(attempt + 1), 7000); return; }
  rescueHelpPrompted = true;
  let data;
  try { data = await api("/api/rescue-help", { timeoutMs: 8000 }); } catch (_) { return; }
  const now = Date.now();
  const key = (id) => `adam_rescue_help_${id}`;
  const friend = (data.friends || []).find((f) => { try { return now - Number(localStorage.getItem(key(f.telegram_id)) || 0) > RESCUE_REMIND_MS; } catch (_) { return true; } });
  if (!friend || celebrationOverlayOpen()) return;
  try { localStorage.setItem(key(friend.telegram_id), String(now)); } catch (_) { /* без памяти окно просто покажется снова в следующий раз */ }
  trk("rescue_help_shown");
  openRescueHelp({ friend });
}

function initRescueHelp() {
  document.getElementById("rescueHelpSend")?.addEventListener("click", () => {
    if (rescueHelp?.step === "sent") closeRescueHelp();
    else doRescueHelp();
  });
  document.getElementById("rescueHelpSkip")?.addEventListener("click", () => closeRescueHelp());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && rescueHelp) closeRescueHelp(); });
}

// ===================== ПАРНОЕ ЗАДАНИЕ (db/pair_quests.py) =====================
// Как «Задания с друзьями» в Duolingo: выбираешь союзника из друзей, он
// принимает, и вместе за неделю нужно отметить PAIR_GOAL дней — прогресс
// общий, у каждого вклад до 7, поэтому одному не вытянуть. Карточка живёт на
// Главной (#pairQuestCard), выбор союзника — шторка #pairSheet. Всё состояние
// и все правила считает сервер, здесь только отрисовка и вызовы.
let pairCandidates = null;
let pairSelectedId = null;
let pairBusy = false;
let pairSheetView = "pick";   // pick — выбор союзника; add — «Добавить друзей» (два способа); people — активные игроки ADAM
let pairPeople = null;        // ответ /api/people/active (null — ещё грузится)

function ruPlural(n, forms) {
  const k = Math.abs(Number(n)) % 100;
  const d = k % 10;
  if (k > 10 && k < 20) return forms[2];
  if (d === 1) return forms[0];
  if (d >= 2 && d <= 4) return forms[1];
  return forms[2];
}

function pairDaysWord(n) { return `${n} ${ruPlural(n, ["день", "дня", "дней"])}`; }

// Карточка на Главной по умолчанию свёрнута (узкая); развернул — остаётся развёрнутой, пока открыто приложение.
let pairHomeExpanded = false;

const PAIR_CHEVRON_DOWN = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const PAIR_CHEVRON_UP = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 15l6-6 6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// Короткая сводка для свёрнутой карточки и краткой версии в «Квестах дня»:
// chip — слово/смайл «сделал ли ты сегодня» (tone glow — светится, зовёт отметиться).
function pairSummary(pq) {
  const q = pq.quest;
  if (q) {
    const partner = escapeHtml(q.partner.first_name);
    const pct = Math.max(0, Math.min(100, Math.round(100 * q.progress / q.goal)));
    const base = { pct, score: `${q.progress}/${q.goal}`, partner: q.partner, line: `напарник — ${partner} · ${q.progress}/${q.goal}` };
    if (q.phase === "scheduled") {
      return { ...base, chip: "⏳ Скоро старт", tone: "muted", days: "скоро старт" };
    }
    if (q.phase === "completed") {
      return q.claimable
        ? { ...base, chip: "🎁 Сундук ждёт", tone: "glow", days: "✓ выполнено" }
        : { ...base, chip: "✓ Выполнено", tone: "ok", days: "✓ выполнено" };
    }
    const today = q.today || { me: 0, partner: 0 };
    const days = `осталось ${q.days_left} дн.`;
    if (q.waiting_for === "me") return { ...base, chip: "⚡ Твой ход", tone: "glow", days };
    if (q.waiting_for === "partner") return { ...base, chip: "⏳ Ждём друга", tone: "muted", days };
    if (!today.me) return { ...base, chip: "⚡ Твой ход", tone: "glow", days };
    if (!today.partner) return { ...base, chip: "✓ Ты в деле", tone: "ok", days };
    return { ...base, chip: "🔥 Оба сегодня", tone: "ok", days };
  }
  const reward = `+${pq.reward.coins} ${ADAM_COIN_ICON} · 💎${pq.reward.diamonds}`;
  if (pq.incoming.length) {
    return { chip: "💌 Приглашение", tone: "glow", days: reward, line: `${escapeHtml(pq.incoming[0].from.first_name)} зовёт тебя в парное задание` };
  }
  if (pq.outgoing) {
    return { chip: "⏳ Ждём ответ", tone: "muted", days: reward, line: `Приглашение для ${escapeHtml(pq.outgoing.to.first_name)} отправлено` };
  }
  if (pq.needs_habit) return { chip: "Добавь привычку", tone: "muted", days: reward, line: "Без привычки вклада в задание не будет" };
  if (!pq.can_choose && pq.choose_from_day) {
    return { chip: "⏳ Скоро", tone: "muted", days: reward, line: `Новое задание — с ${escapeHtml(pairDateLabel(pq.choose_from_day))}` };
  }
  return {
    chip: "＋ Союзник", tone: "glow", days: reward, act: "choose",     // сразу открывает выбор союзника
    line: pq.recent_fail ? "В этот раз не вышло — попробуйте снова" : "Позови друга — вместе за неделю",
  };
}

// Свёрнутая карточка (Главная): заголовок с «осталось N дн.» и стрелкой, ниже — прогресс и статус «сегодня».
function pairCollapsedHtml(pq) {
  const s = pairSummary(pq);
  const chip = s.act
    ? `<button type="button" class="pair-chip pair-chip--${s.tone}" data-pair-act="${s.act}">${s.chip}</button>`
    : `<span class="pair-chip pair-chip--${s.tone}">${s.chip}</span>`;
  // Одна узкая строка: «Парное задание / 3/10 · осталось 5 дн.», справа статус «сегодня» и стрелка.
  const sub = pq.quest ? `${s.score} · ${s.days}` : s.line;
  return `
    <div class="pair-card__head pair-card__head--toggle pair-card__head--row" data-pair-toggle role="button" tabindex="0" aria-expanded="false">
      <span class="pair-row__icon" aria-hidden="true">🤝</span>
      <div class="pair-row__text"><b>Парное задание</b><small>${sub}</small></div>
      ${chip}
      <button type="button" class="pair-toggle" data-pair-toggle aria-label="Развернуть парное задание">${PAIR_CHEVRON_DOWN}</button>
    </div>`;
}

function pairDateLabel(day) {
  const d = new Date(`${day}T00:00:00`);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

function pairAvatarHtml(person, extra = "") {
  return `<span class="pair-avatar ${avatarFrameClass(person)} ${extra}">${avatarInner(person)}</span>`;
}

// ---- развёрнутая карточка: минимум текста, максимум картинок ----
// Правила (db/pair_quests.py): цель — 20 закрытых привычек на двоих за 7 дней, с человека в день до 3, закрывается днём,
// когда отметились оба. Экран показывает это без объяснений: сегментный бар с сундуком в конце, три «точки» на человека
// за сегодня, неделя столбцами (золотой столбец — оба были в деле); «как это работает» — отдельное окно (ⓘ в шапке, а в
// Рейтинге ещё и тап по любому месту карточки), а не текст на самой карточке.
const pairShownProgress = {};   // «карточка:задание» → сколько сегментов уже показано (новые вспыхивают)

const PAIR_INFO_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 11v5.5M12 7.6v.4" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>';

function pairSegmentsHtml(q, boxKey) {
  const key = `${boxKey}:${q.id}`;
  const before = pairShownProgress[key] ?? q.progress;
  pairShownProgress[key] = q.progress;
  let html = "";
  for (let i = 0; i < q.goal; i++) {
    const on = i < q.progress;
    html += `<i class="pq-seg${on ? " is-on" : ""}${on && i >= before ? " is-new" : ""}"></i>`;
  }
  return `<div class="pq-bar${q.phase === "completed" ? " is-done" : ""}" style="--pq-goal:${q.goal}">${html}</div>`;
}

function pairPipsHtml(count, cap, held = 0) {
  let html = "";
  for (let i = 0; i < cap; i++) {
    const on = i < count;
    html += `<i class="pq-pip${on ? " is-on" : ""}${on && i >= count - held ? " is-held" : ""}"></i>`;
  }
  return `<span class="pq-pips" aria-hidden="true">${html}</span>`;
}

function pairHeroHtml(q, boxKey) {
  const chips = [];
  if (q.phase === "active" && q.waiting_for) chips.push('<span class="pq-chip pq-chip--lock">🔒 нужны оба</span>');
  if (q.phase === "active" && q.pace === "tight") chips.push('<span class="pq-chip pq-chip--tight">⚠️ быстрее</span>');
  if (q.phase === "active" && q.pace === "lost") chips.push('<span class="pq-chip pq-chip--lost">😬 не успеть</span>');
  const chest = `<span class="pq-chest${q.phase === "completed" ? " is-open" : (q.need > 0 && q.need <= 3 ? " is-near" : "")}" aria-hidden="true">🎁</span>`;
  return `
    <div class="pq-hero">
      <div class="pq-score"><b>${q.progress}</b><span>/${q.goal}</span></div>
      <div class="pq-hero__right">${chips.join("")}${chest}</div>
    </div>
    ${pairSegmentsHtml(q, boxKey)}`;
}

// Вклад человека за всё задание под его именем: «6 привычек». Ноль не показываем — пустая строка честнее, чем «0 привычек».
function pairShareHtml(count) {
  const n = Number(count) || 0;
  return n > 0 ? `<small class="pq-share"><i>${n}</i> ${ruPlural(n, ["привычка", "привычки", "привычек"])}</small>` : "";
}

function pairTodayHtml(q) {
  const me = state?.user || {};
  const t = q.today || { me: 0, partner: 0, held: 0 };
  const holder = q.waiting_for === "partner" ? "me" : (q.waiting_for === "me" ? "partner" : null);
  const myTurn = q.waiting_for === "me" || (q.waiting_for !== "partner" && !t.me && t.partner > 0);
  const theirTurn = q.waiting_for === "partner" || (q.waiting_for !== "me" && !t.partner && t.me > 0);
  return `
    <div class="pq-today">
      <div class="pq-person${myTurn ? " is-turn" : ""}">${pairAvatarHtml(me)}${pairPipsHtml(t.me, q.cap, holder === "me" ? t.held : 0)}<b>Ты</b>${pairShareHtml(q.mine)}</div>
      <span class="pq-vs" aria-hidden="true">🤝</span>
      <div class="pq-person${theirTurn ? " is-wait" : ""}">${pairAvatarHtml(q.partner)}${pairPipsHtml(t.partner, q.cap, holder === "partner" ? t.held : 0)}<b>${escapeHtml(q.partner.first_name)}</b>${pairShareHtml(q.partner_count)}</div>
    </div>`;
}

function pairWeekHtml(q) {
  const me = state?.user || {};
  const cols = q.days.map((d) => `
      <div class="pq-col${d.both ? " is-both" : ""}${d.state === "today" ? " is-today" : ""}${d.state === "future" ? " is-future" : ""}${d.done ? " is-finish" : ""}">
        <b>${escapeHtml(d.weekday)}</b>
        <i class="pq-cell pq-n${d.me}">${d.me || ""}</i>
        <i class="pq-cell pq-n${d.partner}">${d.partner || ""}</i>
      </div>`).join("");
  return `
    <div class="pq-week" style="--pq-days:${q.days.length}">
      <div class="pq-who"><span></span>${pairAvatarHtml(me, "pair-avatar--xs")}${pairAvatarHtml(q.partner, "pair-avatar--xs")}</div>
      ${cols}
    </div>`;
}

// ---- окно «Как это работает» ----
// Числа берутся из состояния сервера, чтобы текст не разошёлся с правилами (db/pair_quests.py). Заголовок уже называет цель
// и срок, поэтому строки ниже их не повторяют.
let pairGuideOpenedAt = 0;

function pairGuideContent(pq) {
  const goal = Number(pq?.goal) || 20;
  const days = Number(pq?.window_days) || 7;
  const cap = Number(pq?.day_cap) || 3;
  const reward = pq?.reward || {};
  const rows = [
    ["☝️", `С каждого засчитывается до <b>${cap}</b> привычек в день`],
    ["🤝", "Закончить можно только вдвоём — в день, когда отметились <b>оба</b>"],
    ["🎁", `Сундук каждому: <b>+${reward.coins ?? 60}</b> ${ADAM_COIN_ICON} и 💎${reward.diamonds ?? 1}, плюс очки «Заданий месяца»`],
  ];
  return {
    title: `${goal} ${ruPlural(goal, ["привычка", "привычки", "привычек"])} на двоих за ${pairDaysWord(days)}`,
    list: rows.map(([icon, text]) => `<li><span class="pq-guide-ico" aria-hidden="true">${icon}</span><span>${text}</span></li>`).join(""),
  };
}

function openPairGuide() {
  const overlay = document.getElementById("pairGuideOverlay");
  if (!overlay || !overlay.hidden) return;
  const { title, list } = pairGuideContent(state?.pair_quest);
  document.getElementById("pairGuideTitle").textContent = title;
  document.getElementById("pairGuideList").innerHTML = list;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  pairGuideOpenedAt = performance.now();
  requestAnimationFrame(() => overlay.classList.add("show"));
  haptic("light");
}

function closePairGuide() {
  const overlay = document.getElementById("pairGuideOverlay");
  if (!overlay || overlay.hidden) return;
  overlay.classList.remove("show");
  overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { overlay.hidden = true; }, 240);
}

function pairActionsHtml(q) {
  const partner = q.partner;
  let nudge = "";
  if (q.partner_state === "can_remind") {
    nudge = `<button type="button" class="pq-nudge" data-pair-act="nudge" data-id="${partner.telegram_id}">👋 Напомнить</button>`;
  } else if (q.partner_state === "reminded") {
    nudge = `<span class="pq-nudge is-sent">✓ Напомнили</span>`;
  }
  // Напарник пропал (2+ полных дня не заходит и не отмечается): можно не ждать конца недели — выйти и позвать другого друга.
  const idle = Number(q.partner_idle_days) || 0;
  const leave = q.can_leave
    ? `<button type="button" class="pair-btn pair-btn--ghost pq-leave" data-pair-act="cancel" data-id="${q.id}" data-leave="1" data-confirm="Выйти из парного задания? Оно закончится без награды, и ты сможешь взять новое с другим другом.">Выйти</button>`
    : "";
  const note = q.can_leave
    ? `<div class="pq-idle-note">${escapeHtml(q.partner?.first_name || "Напарник")} не заходит уже ${pairDaysWord(idle)} — можно не ждать конца недели и взять другого напарника.</div>`
    : "";
  return nudge || leave ? `<div class="pq-actions">${nudge}${leave}</div>${note}` : "";
}

function pairQuestBodyHtml(pq, q, boxKey) {
  if (q.phase === "scheduled") {
    const when = q.starts_in <= 1 ? "завтра" : `через ${pairDaysWord(q.starts_in)}`;
    const me = state?.user || {};
    return `
      <div class="pq-start">
        <div class="pq-start__duo">${pairAvatarHtml(me)}<span aria-hidden="true">🤝</span>${pairAvatarHtml(q.partner)}</div>
        <div class="pq-start__when">🕒 Старт <b>${when}</b></div>
      </div>
      ${pairSegmentsHtml(q, boxKey)}
      <div class="pq-actions"><button type="button" class="pair-btn pair-btn--ghost pq-leave" data-pair-act="cancel" data-id="${q.id}" data-confirm="Выйти из парного задания?">Выйти</button></div>`;
  }

  if (q.phase === "completed") {
    if (q.claimable) {
      return `
        ${pairHeroHtml(q, boxKey)}
        ${pairWeekHtml(q)}
        <button type="button" class="pair-btn pair-btn--primary pq-claim" data-pair-act="claim" data-id="${q.id}">
          🎁 Открыть · +${pq.reward.coins} ${ADAM_COIN_ICON} · 💎${pq.reward.diamonds}
        </button>`;
    }
    const next = pq.choose_from_day ? `<div class="pq-next">🗓 Новое — с ${escapeHtml(pairDateLabel(pq.choose_from_day))}</div>` : "";
    return `${pairHeroHtml(q, boxKey)}${pairWeekHtml(q)}<div class="pq-next">✓ Награда получена</div>${next}`;
  }

  // active
  return `
    ${pairHeroHtml(q, boxKey)}
    ${pairTodayHtml(q)}
    ${pairWeekHtml(q)}
    ${pairActionsHtml(q)}`;
}

function pairInvitesHtml(pq) {
  return pq.incoming.map((inv) => `
    <div class="pair-invite">
      ${pairAvatarHtml(inv.from)}
      <div class="pair-invite__text"><b>${escapeHtml(inv.from.first_name)}</b> зовёт тебя в парное задание</div>
      <div class="pair-invite__btns">
        <button type="button" class="pair-btn pair-btn--small pair-btn--primary" data-pair-act="accept" data-id="${inv.id}">Принять</button>
        <button type="button" class="pair-btn pair-btn--small pair-btn--ghost" data-pair-act="decline" data-id="${inv.id}">Не сейчас</button>
      </div>
    </div>`).join("");
}

function renderPairCard() {
  if (labMode?.payload) state.pair_quest = labMode.payload;      // демо: свежий bootstrap не должен затирать сценарий
  renderPairCardInto(document.getElementById("pairQuestCard"), true);
  renderPairCardInto(document.getElementById("pairQuestCardRating"), false);
  renderPairMini();
}

// collapsible: Главная — карточка сворачивается; Рейтинг — всегда развёрнута.
function renderPairCardInto(box, collapsible) {
  if (!box) return;
  const pq = state?.pair_quest;
  if (!pq) { box.hidden = true; return; }
  box.hidden = false;
  const expanded = !collapsible || pairHomeExpanded;
  box.classList.toggle("pair-card--collapsed", !expanded);
  box.classList.toggle("pair-card--tappable", !collapsible);        // Рейтинг: тап по любому месту карточки — инструкция
  if (!expanded) { box.innerHTML = pairCollapsedHtml(pq); return; }

  const q = pq.quest;
  const reward = `+${pq.reward.coins} ${ADAM_COIN_ICON} · 💎${pq.reward.diamonds}`;

  let badge = reward;
  let body = "";
  if (q) {
    badge = q.phase === "scheduled" ? "🕒 скоро"
      : q.phase === "completed" ? "✓ готово"
      : `⏳ ${q.days_left} дн.`;
    body = pairQuestBodyHtml(pq, q, box.id || "pair");
  } else {
    if (pq.incoming.length) body += pairInvitesHtml(pq);
    if (pq.outgoing) {
      body += `
        <div class="pq-empty">
          <div class="pq-empty__icons">${pairAvatarHtml(pq.outgoing.to)}<span aria-hidden="true">⏳</span></div>
          <div class="pq-empty__line">Ждём <b>${escapeHtml(pq.outgoing.to.first_name)}</b></div>
        </div>
        <button type="button" class="pair-btn pair-btn--ghost" data-pair-act="cancel" data-id="${pq.outgoing.id}">Отозвать</button>`;
    } else if (pq.can_choose) {
      const line = pq.needs_habit
        ? "Сначала добавь привычку"
        : (pq.recent_fail
          ? "В этот раз не вышло — ещё раз?"
          : `<b>${pq.goal}</b> привычек на двоих · <b>${pq.window_days || 7}</b> дней`);
      body += `
        <div class="pq-empty">
          <div class="pq-empty__icons"><span aria-hidden="true">🤝</span><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span aria-hidden="true">🎁</span></div>
          <div class="pq-empty__line">${line}</div>
        </div>
        <button type="button" class="pair-btn pair-btn--primary" data-pair-act="choose"${pq.needs_habit ? " disabled" : ""}>🤝 ${pq.recent_fail ? "Новый союзник" : "Выбрать союзника"}</button>`;
    }
  }
  const total = Number(pq.completed_total || 0);
  const infoBtn = `<button type="button" class="pq-info-btn pq-info-btn--head" data-pair-act="info" aria-label="Как это работает">${PAIR_INFO_ICON}</button>`;
  const toggle = collapsible
    ? `<button type="button" class="pair-toggle" data-pair-toggle aria-label="Свернуть парное задание">${PAIR_CHEVRON_UP}</button>`
    : "";
  box.innerHTML = `
    <div class="pair-card__head${collapsible ? " pair-card__head--toggle" : ""}"${collapsible ? ' data-pair-toggle role="button" tabindex="0" aria-expanded="true"' : ""}>
      <div class="pair-card__title">🤝 Парное задание</div>
      <div class="pair-card__right"><div class="pair-card__badge">${badge}</div>${infoBtn}${toggle}</div>
    </div>
    ${body}
    ${total > 0 ? `<div class="pair-card__foot">🏆 ${total}</div>` : ""}`;
}

// Краткая версия в «Квестах дня» — после заданий месяца (как в Duolingo).
function renderPairMini() {
  const box = document.getElementById("pairQuestMini");
  if (!box) return;
  const pq = state?.pair_quest;
  if (!pq) { box.hidden = true; return; }
  const s = pairSummary(pq);
  box.hidden = false;
  box.innerHTML = `
    <div class="pair-mini-card__row">
      <span class="pair-mini-card__icon" aria-hidden="true">🤝</span>
      <div class="pair-mini-card__body"><b>Парное задание</b><small>${s.line}</small></div>
      <span class="pair-chip pair-chip--${s.tone}">${s.chip}</span>
    </div>
    ${pq.quest ? `<div class="pair-bar${pq.quest.phase === "completed" ? " is-done" : ""}"><i style="width:${s.pct}%"></i></div>` : ""}`;
}

// Принять свежее состояние и отметить сдвиги: прогресс, выполнение.
function applyPairQuest(next) {
  if (!state || labMode) return;
  const prev = state.pair_quest?.quest;
  state.pair_quest = next || null;
  const cur = next?.quest;
  if (prev && cur && prev.id === cur.id) {
    if (cur.phase === "completed" && prev.phase !== "completed") {
      haptic("triple");
      showToast("🏆 Парное задание выполнено — открой сундук на Главной", "praise", 4600);
    } else if (cur.phase === "active" && cur.progress > prev.progress) {
      showToast(`🤝 ${cur.progress}/${cur.goal}`, "success", 2200);
    }
  }
  renderPairCard();
}

async function loadPairQuest() {
  try {
    const data = await api("/api/pair-quest", { timeoutMs: 8000 });
    applyPairQuest(data.pair_quest);
  } catch (err) {
    console.error("loadPairQuest failed:", err);
  }
}

// ---- выбор союзника ----
const PAIR_ICON_NEXT = '<svg class="ui-icon pair-option__chev" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const PAIR_PEOPLE_MIN_STREAK = 4;

function pairShareText() {
  return `Давай возьмём парное задание в ADAM — ${pairCandidates?.goal || 20} привычек на двоих за неделю. Добавляйся в друзья:`;
}

// «Добавить друзей»: ссылка в Telegram (так бота рекомендуют знакомым) или подписка на активных игроков ADAM.
function pairAddViewHtml() {
  const minStreak = pairPeople?.min_streak || PAIR_PEOPLE_MIN_STREAK;
  const reward = friendsData?.invite_reward;
  const rewardNote = reward && Number(reward.left) > 0 ? `. Тебе — ${Number(reward.coins)} Адам коинов за каждого из первых двух` : "";
  return `
    <li class="pair-option" data-pair-act="invite-link" role="button" tabindex="0">
      <span class="pair-option__icon" aria-hidden="true">✈️</span>
      <span class="pair-option__text"><b>Отправить ссылку в Telegram</b><small>Друг откроет меня в Telegram и сразу станет твоим другом${rewardNote}</small></span>
      ${PAIR_ICON_NEXT}
    </li>
    <li class="pair-option" data-pair-act="pick-people" role="button" tabindex="0">
      <span class="pair-option__icon" aria-hidden="true">🔥</span>
      <span class="pair-option__text"><b>Выбрать из активных игроков</b><small>Серия от ${minStreak} дней. Подпишись — если подпишутся в ответ, вы друзья</small></span>
      ${PAIR_ICON_NEXT}
    </li>`;
}

function pairPersonRowHtml(u) {
  const id = Number(u.telegram_id);
  const meta = [u.handle ? `@${escapeHtml(u.handle)}` : "", `🔥 ${Number(u.streak)}`].filter(Boolean).join(" · ");
  let action;
  if (u.friends) action = `<span class="friend-row__tag friend-row__tag--sent">Друзья ✓</span>`;
  else if (u.following) action = `<span class="friend-row__tag">Подписка ✓</span>`;
  else action = `<button type="button" class="pair-btn pair-btn--primary pair-btn--small" data-pair-act="follow-person" data-id="${id}">Подписаться</button>`;
  const note = u.followed_by && !u.following
    ? `<span class="friend-row__meta pair-person__note">Подписан(а) на тебя — сразу друзья</span>` : "";
  return `
    <li class="friend-row" data-pair-person="${id}">
      <span class="friend-row__avatar ${avatarFrameClass(u)}">${avatarInner(u)}</span>
      <span class="friend-row__info">
        ${friendNameHtml(u, "Игрок")}
        <span class="friend-row__meta">${meta}</span>
        ${note}
      </span>
      ${action}
    </li>`;
}

function renderPairSheet() {
  const list = document.getElementById("pairSheetList");
  const confirmBtn = document.getElementById("pairSheetConfirm");
  const ally = document.getElementById("pairSheetAlly");
  const meBox = document.getElementById("pairSheetMe");
  const sub = document.getElementById("pairSheetSub");
  const sheet = document.getElementById("pairSheet");
  const title = document.getElementById("pairSheetTitle");
  const addBtn = document.getElementById("pairSheetAdd");
  const later = document.getElementById("pairSheetLater");
  if (!list || !pairCandidates) return;
  const view = pairSheetView;
  sheet?.classList.toggle("pair-sheet--add", view !== "pick");
  if (title) title.textContent = view === "add" ? "Добавить друзей" : view === "people" ? "Активные игроки" : "Выбери союзника!";
  if (later) later.textContent = view === "pick" ? "Выбрать позже" : "Назад";
  if (confirmBtn) confirmBtn.hidden = view !== "pick";
  list.setAttribute("role", view === "pick" ? "radiogroup" : "list");
  if (view !== "pick") {
    if (addBtn) addBtn.hidden = true;
    const minStreak = pairPeople?.min_streak || PAIR_PEOPLE_MIN_STREAK;
    if (sub) {
      sub.textContent = view === "add"
        ? "Парное задание берут с другом — это взаимная подписка. Выбери, как его найти."
        : `Серия от ${minStreak} дней. Подпишись — если подпишутся в ответ, вы друзья и сможете взять задание вместе.`;
    }
    if (view === "add") list.innerHTML = pairAddViewHtml();
    else if (!pairPeople) list.innerHTML = `<li class="pair-sheet__empty">Ищу игроков…</li>`;
    else if (!pairPeople.users.length) {
      list.innerHTML = `
        <li class="pair-sheet__empty">
          Пока мало активных игроков с такой серией. Отправь ссылку другу — так быстрее.
          <button type="button" class="pair-btn pair-btn--ghost" data-pair-act="invite-link">✈️ Отправить ссылку в Telegram</button>
        </li>`;
    } else list.innerHTML = pairPeople.users.map(pairPersonRowHtml).join("");
    return;
  }
  const me = state?.user || {};
  if (meBox) {
    meBox.className = `pair-avatar ${avatarFrameClass(me)}`;
    meBox.innerHTML = avatarInner(me);
  }
  const friends = pairCandidates.friends || [];
  const picked = friends.find((f) => Number(f.telegram_id) === pairSelectedId);
  if (ally) {
    ally.className = `pair-avatar${picked ? ` ${avatarFrameClass(picked)}` : " pair-avatar--empty"}`;
    ally.innerHTML = picked ? avatarInner(picked) : "👤";
  }
  if (sub) {
    const hasRecommended = friends.some((f) => f.recommended);
    sub.textContent = `🎯 ${pairCandidates.goal} привычек на двоих за ${pairCandidates.window_days} дней · до ${pairCandidates.day_cap || 3} в день с каждого · старт завтра.${hasRecommended ? " ⭐ — самые активные друзья." : ""}`;
  }
  if (confirmBtn) confirmBtn.disabled = pairBusy || !picked;
  // Все друзья заняты (или давно не заходили, или друзей ещё нет) — предлагаем найти новых.
  if (addBtn) addBtn.hidden = friends.some((f) => f.available);
  if (!friends.length) {
    list.innerHTML = `<li class="pair-sheet__empty">Пока нет друзей, с которыми можно взять задание.</li>`;
    return;
  }
  const row = (f) => {
    const id = Number(f.telegram_id);
    const metaParts = [f.handle ? `@${escapeHtml(f.handle)}` : "", Number(f.streak) > 0 ? `🔥 ${Number(f.streak)}` : ""];
    if (f.available && Number(f.active_days) > 0) metaParts.push(`отмечался ${Number(f.active_days)} из ${pairCandidates.active_window || 7} дней`);
    if (f.was_partner) metaParts.push("уже были вместе");
    const meta = metaParts.filter(Boolean).join(" · ");
    const tag = f.reason === "busy" ? "уже в задании" : f.reason === "inactive" ? "давно не заходил(а)" : "";
    const selected = id === pairSelectedId;
    return `
      <li class="friend-row pair-row${selected ? " is-selected" : ""}${f.available ? "" : " is-disabled"}"
          data-pair-friend="${id}" role="radio" aria-checked="${selected}" aria-disabled="${!f.available}">
        <span class="friend-row__avatar ${avatarFrameClass(f)}">${avatarInner(f)}</span>
        <span class="friend-row__info">
          <span class="friend-row__name">${escapeHtml(f.first_name || "Друг")}</span>
          ${meta ? `<span class="friend-row__meta">${meta}</span>` : ""}
        </span>
        ${tag ? `<span class="friend-row__tag">${tag}</span>` : `<span class="pair-radio${selected ? " is-on" : ""}" aria-hidden="true"></span>`}
      </li>`;
  };
  const recommended = friends.filter((f) => f.recommended);
  const rest = friends.filter((f) => !f.recommended);
  list.innerHTML =
    (recommended.length
      ? `<li class="pair-section">⭐ Рекомендуем — самые активные</li>${recommended.map(row).join("")}${rest.length ? '<li class="pair-section">Остальные друзья</li>' : ""}`
      : "") + rest.map(row).join("");
}

// Действия внутри окна выбора союзника: «Добавить друзей» → ссылка в Telegram / активные игроки / подписка на игрока.
async function openPairPeople() {
  pairSheetView = "people";
  pairPeople = null;
  renderPairSheet();
  try {
    pairPeople = await api("/api/people/active", { timeoutMs: 8000 });
  } catch (err) {
    showToast(friendlyError(err), "error");
    pairSheetView = "add";
  }
  renderPairSheet();
}

async function pairFollowPerson(button) {
  const id = Number(button.dataset.id);
  if (!id || button.disabled) return;
  button.disabled = true;
  try {
    const result = await api(`/api/users/${id}/follow`, { method: "POST" });
    const person = (pairPeople?.users || []).find((u) => Number(u.telegram_id) === id);
    const friends = !!result?.relation?.friends;
    if (person) { person.following = true; person.friends = friends; }
    haptic("medium");
    showToast(friends ? "🤝 Теперь вы друзья — можно звать в задание" : "Подписка отправлена — если подпишутся в ответ, вы друзья", "success", 3200);
    loadFriendsCard();      // счётчики подписок в Рейтинге
    if (friends) {
      try { pairCandidates = await api("/api/pair-quest/candidates", { timeoutMs: 8000 }); } catch (_) { /* список обновится при следующем открытии */ }
    }
  } catch (err) {
    showToast(friendlyError(err), "error");
    button.disabled = false;
    return;
  }
  renderPairSheet();
}

async function pairSheetAction(el) {
  const act = el.dataset.pairAct;
  if (act === "add-friends") { haptic("light"); pairSheetView = "add"; renderPairSheet(); return; }
  if (act === "invite-link") { closePairSheet(); await openFriendInvite(pairShareText()); return; }
  if (act === "pick-people") { haptic("light"); await openPairPeople(); return; }
  if (act === "follow-person") await pairFollowPerson(el);
}

async function openPairSheet() {
  const sheet = document.getElementById("pairSheet");
  if (!sheet || pairBusy) return;
  try {
    pairCandidates = await api("/api/pair-quest/candidates", { timeoutMs: 8000 });
  } catch (err) {
    showToast(friendlyError(err), "error");
    return;
  }
  if (pairCandidates.needs_habit) { showToast(friendlyError({ data: { error: "needs_habit" } }), "error"); return; }
  if (!pairCandidates.can_choose) { await loadPairQuest(); return; }
  pairSelectedId = null;
  pairSheetView = "pick";
  pairPeople = null;
  renderPairSheet();
  haptic("light");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closePairSheet() {
  const sheet = document.getElementById("pairSheet");
  if (!sheet || sheet.hidden) return;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { sheet.hidden = true; }, 230);
}

async function confirmPairInvite() {
  if (pairBusy || pairSelectedId === null) return;
  pairBusy = true;
  renderPairSheet();
  try {
    const result = await api("/api/pair-quest/invite", { method: "POST", body: JSON.stringify({ friend_id: pairSelectedId }) });
    haptic("medium");
    closePairSheet();
    showToast("🤝 Приглашение отправлено — ждём ответ", "success", 3200);
    applyPairQuest(result.pair_quest);
  } catch (err) {
    const code = err?.data?.error;
    showToast(friendlyError(err), "error");
    if (code === "invited_you" || code === "busy" || code === "already_invited") {
      closePairSheet();
      await loadPairQuest();
    } else if (code === "partner_busy" || code === "partner_inactive") {
      try { pairCandidates = await api("/api/pair-quest/candidates", { timeoutMs: 8000 }); pairSelectedId = null; } catch (_) {}
    }
  } finally {
    pairBusy = false;
    renderPairSheet();
  }
}

// ---- действия на карточке ----
async function pairCardAction(button) {
  const act = button.dataset.pairAct;
  const id = Number(button.dataset.id);
  if (act === "info") { openPairGuide(); return; }
  if (labMode) { haptic("light"); showToast("🧪 Демо: действие не выполняется", "success", 1800); return; }
  if (act === "choose") { await openPairSheet(); return; }
  if (act === "nudge") { await pairNudge(id, button); return; }
  if (pairBusy) return;
  const endpoint = { accept: "accept", decline: "decline", cancel: "cancel", claim: "claim" }[act];
  if (!endpoint) return;
  if (button.dataset.confirm && !confirm(button.dataset.confirm)) return;
  pairBusy = true;
  button.disabled = true;
  try {
    if (act === "claim") {
      // Окно «Сундук»: три тапа по сундуку, потом запрос; данные применяем, когда окно закрыли.
      const opened = await openChestReveal({
        eyebrow: "СУНДУК НА ДВОИХ",
        caption: "За парное задание",
        request: () => api(`/api/pair-quest/${id}/claim`, { method: "POST" }),
        onDone: (result) => applyActionPatch(result),       // обновит монеты/алмазы, месяц и саму карточку
      });
      if (!opened) button.disabled = false;                 // «Не сейчас»: сундук ждёт, кнопка снова нажимается
    } else {
      const result = await api(`/api/pair-quest/${id}/${endpoint}`, { method: "POST" });
      haptic("medium");
      if (act === "accept") showToast("✅ Приняли! Задание стартует завтра", "success", 3200);
      if (act === "cancel" && result.left_early) showToast("Вышли из задания — теперь можно выбрать нового союзника", "success", 3600);
      applyPairQuest(result.pair_quest);
    }
  } catch (err) {
    showToast(friendlyError(err), "error");
    button.disabled = false;
    if (["not_pending", "not_found", "cannot_cancel", "busy", "already_claimed"].includes(err?.data?.error)) await loadPairQuest();
  } finally {
    pairBusy = false;
  }
}

function setPairPartnerState(nextState) {
  const q = state?.pair_quest?.quest;
  if (!q) return;
  q.partner_state = nextState;
  renderPairCard();
}

async function pairNudge(friendId, button) {
  button.disabled = true;
  try {
    await api(`/api/friends/${friendId}/nudge`, { method: "POST" });
    haptic("medium");
    showToast("Напомнил другу 👋", "success");
    setPairPartnerState("reminded");
    setFriendState(friendId, "reminded");
  } catch (err) {
    const code = err?.data?.error;
    if (code === "already_reminded") setPairPartnerState("reminded");
    else if (code === "already_done") setPairPartnerState("done");
    else button.disabled = false;
    showToast(friendlyError(err), "error");
  }
}

// ===================== ЛАБОРАТОРИЯ (только админ) =====================
// Админка → «Тесты и нововведения» открывает приложение с ?lab=<сценарий>. Сервер (db/pair_lab.py) отдаёт демо-состояние в
// формате обычного pair_quest; рисует его настоящий код карточки. Ничего не сохраняется: действия на карточке заблокированы,
// пока включена лаборатория (labMode). Новые экраны сюда добавляются сценариями — до пользователей они доходят проверенными.
let labMode = null;   // { list, index, payload }

async function initLabMode() {
  const key = new URLSearchParams(location.search).get("lab");
  if (!key || !state?.user?.is_admin) return;
  try {
    const list = (await api("/api/admin/lab/scenarios")).scenarios || [];
    if (!list.length) return;
    labMode = { list, index: Math.max(0, list.findIndex((item) => item.key === key)), payload: null };
    await applyLabScenario();
  } catch (err) {
    console.error("initLabMode failed:", err);
    labMode = null;
  }
}

async function applyLabScenario() {
  const item = labMode.list[labMode.index];
  const data = await api(`/api/admin/lab/pair/${encodeURIComponent(item.key)}`);
  labMode.payload = data.pair_quest;
  pairHomeExpanded = true;
  renderPairCard();
  renderLabBar();
  document.getElementById("pairQuestCard")?.scrollIntoView({ block: "center" });
}

const LAB_ICON_PREV = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M15 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const LAB_ICON_NEXT = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function renderLabBar() {
  let bar = document.getElementById("labBar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "labBar";
    bar.className = "lab-bar";
    document.body.appendChild(bar);
    bar.addEventListener("click", async (e) => {
      const act = e.target.closest("[data-lab]")?.dataset.lab;
      if (!act || !labMode) return;
      haptic("light");
      if (act === "exit") { window.location.href = "/admin"; return; }
      const n = labMode.list.length;
      labMode.index = (labMode.index + (act === "next" ? 1 : n - 1)) % n;
      try { await applyLabScenario(); } catch (err) { showToast(friendlyError(err), "error"); }
    });
  }
  const item = labMode.list[labMode.index];
  bar.innerHTML = `
    <button type="button" class="lab-bar__btn" data-lab="prev" aria-label="Назад">${LAB_ICON_PREV}</button>
    <div class="lab-bar__text"><b>🧪 Демо ${labMode.index + 1}/${labMode.list.length}</b><small>${escapeHtml(item.title)}</small></div>
    <button type="button" class="lab-bar__btn" data-lab="next" aria-label="Дальше">${LAB_ICON_NEXT}</button>
    <button type="button" class="lab-bar__btn lab-bar__btn--exit" data-lab="exit" aria-label="Выйти из лаборатории">${ICON_X}</button>`;
}

// ===================== СИМУЛЯЦИИ ОКОН (только админ) =====================
// Админка → «Окна и анимации» открывает приложение с ?sim=<ключ>: новое окно или праздник проигрывается НАСТОЯЩИМ кодом на
// демо-данных. Ничего не сохраняется, не отправляется и не начисляется: в сценах нет запросов к серверу (тест это проверяет).
// Новое окно с анимацией = новая сцена здесь + строка в db/sim_lab.py (тест следит, чтобы списки совпадали). Внизу панель:
// «‹ предыдущее · ↻ повторить · › следующее · ✕ выйти в админку». Панель лежит под окнами — после закрытия окна она снова видна.
let simMode = null;   // { list, index }

// Ответ «сервера» в демо: через паузу, чтобы успела сыграть тряска закрытого сундука.
const simReply = (result, ms = 700) => () => new Promise((resolve) => setTimeout(() => resolve(result), ms));

// Демо-лента для симуляции «Уведомления»: по записи на каждый раздел и разные виды, часть — «новое».
const SIM_INBOX = (() => {
  const who = (id, name) => ({ telegram_id: id, first_name: name, handle: null, avatar_id: "default", frame_id: "default" });
  const ago = (minutes) => new Date(Date.now() - minutes * 60000).toISOString().slice(0, 19).replace("T", " ");
  const items = [
    { id: "s1", cat: "gifts", kind: "gift_freeze", at: ago(12), icon: "❄️", title: "Мария подарил(а) тебе заморозку серии", text: "", actor: who(1, "Мария"), action: { type: "gifts" }, unread: true },
    { id: "s2", cat: "adam", kind: "adam_checkin", at: ago(55), icon: "💬", title: "Я написал тебе", text: "Заметил, что вторая привычка ещё ждёт. Начнём с пяти минут?", voice: true, actor: null, action: { type: "chat" }, unread: true },
    { id: "s3", cat: "followers", kind: "follower_new", at: ago(95), icon: "➕", title: "Денис подписался(ась) на тебя", text: "", actor: who(2, "Денис"), action: { type: "profile", id: 2 }, unread: true },
    { id: "s4", cat: "friends", kind: "friend_support", at: ago(240), icon: "💌", title: "Анна поддержал(а) тебя 👍", text: "", actor: who(3, "Анна"), action: { type: "profile", id: 3 }, unread: false },
    { id: "s5", cat: "friends", kind: "pair_invite", at: ago(300), icon: "🤝", title: "Олег зовёт тебя в парное задание", text: "Прими приглашение на Главной", actor: who(4, "Олег"), action: { type: "pair" }, unread: false },
    { id: "s6", cat: "system", kind: "streak_freeze", at: ago(1200), icon: "❄️", title: "Я сохранил твой день заморозкой", text: "Серия 14 дн. осталась с тобой", voice: true, actor: null, action: { type: "home" }, unread: false },
    { id: "s7", cat: "gifts", kind: "gift_diamonds", at: ago(1560), icon: "💎", title: "Мария подарил(а) тебе 3 💎", text: "", actor: who(1, "Мария"), action: { type: "gifts" }, unread: false },
    { id: "s8", cat: "system", kind: "whats_new", at: ago(3000), icon: "✨", title: "Новое от меня: Знакомство", text: "Теперь я узнаю тебя и подбираю метод планирования дня.", voice: true, actor: null, action: {}, unread: false },
    { id: "s9", cat: "friends", kind: "friend_added", at: ago(4200), icon: "🤝", title: "Денис теперь в друзьях", text: "Подписаны друг на друга", actor: who(2, "Денис"), action: { type: "profile", id: 2 }, unread: false },
  ];
  const labels = [["all", "Все"], ["system", "Системные"], ["adam", "Адам"], ["friends", "Друзья"], ["followers", "Подписчики"], ["gifts", "Подарки"]];
  const categories = labels.map(([key, label]) => ({ key, label, unread: items.filter((it) => it.unread && (key === "all" || it.cat === key)).length }));
  return { items, categories };
})();

const SIM_FRIEND = { telegram_id: 0, first_name: "Мария", avatar_id: "default", frame_id: "default", streak: 14 };
const SIM_RESCUE_FRIEND = { telegram_id: 0, first_name: "Мария", avatar_id: "default", frame_id: "default", streak: 0, lost_streak: 12 };

// Демо-данные для сцен «Квесты дня» (названия — те же, что в db/quests.py; тест следит).
const simQuestHabits = (progress, opened = false) => ({ key: "two_habits", emoji: "✌️", title: "Выполни 2 привычки", target: 2, progress, reward: 5, completed: progress >= 2, claimed: progress >= 2, just_completed: false, chest: "wood", chest_opened: opened });
const simQuestPlan = (main, secondary, opened = false) => {
  const parts = [{ label: "Главная задача", done: main }, { label: "Второстепенная", done: secondary }];
  const progress = parts.filter((part) => part.done).length;
  return { key: "plan_day", emoji: "📝", title: "Заполни план дня", target: 2, progress, reward: 5, completed: progress >= 2, claimed: progress >= 2, just_completed: false, parts, chest: "blue", chest_opened: opened };
};
const simQuestAdam = (done, opened = false) => ({ key: "talk_adam", emoji: "💬", title: "Поговори сегодня с Адамом", target: 1, progress: done ? 1 : 0, reward: 5, completed: done, claimed: done, just_completed: false, chest: "gold", chest_opened: opened });
const simMonth = (points) => ({
  month_key: "2026-10", title: "Задания октября", points, goal: 60, days_left: 9,
  claimable: [15, 30, 45, 60].filter((at) => points >= at).length,
  chests: [
    { at: 15, coins: 25, diamonds: 0, final: false, reached: points >= 15, claimed: false },
    { at: 30, coins: 50, diamonds: 1, final: false, reached: points >= 30, claimed: false },
    { at: 45, coins: 75, diamonds: 1, final: false, reached: points >= 45, claimed: false },
    { at: 60, coins: 150, diamonds: 1, final: true, reached: points >= 60, claimed: false },
  ],
});

const SIM_SCENES = {
  chest_month: () => openChestReveal({
    eyebrow: "СУНДУК МЕСЯЦА", caption: "30 из 60 заданий",
    request: simReply({ reward: { at: 30, coins: 50, diamonds: 1 } }),
  }),
  chest_final: () => openChestReveal({
    eyebrow: "ГЛАВНЫЙ СУНДУК МЕСЯЦА", caption: "60 из 60 заданий", final: true,
    request: simReply({ reward: { at: 60, coins: 150, diamonds: 1, badge: "Октябрь 2026" } }),
  }),
  chest_pair: () => openChestReveal({
    eyebrow: "СУНДУК НА ДВОИХ", caption: "За парное задание",
    request: simReply({ reward: { coins: 60, diamonds: 1, month_points: 3 } }),
  }),
  chest_quest: () => openChestReveal({
    eyebrow: "ТВОЙ СУНДУК", caption: "Заполни план дня", tone: "blue",
    request: simReply({ reward: { coins: 5, month_points: 1 } }),
  }),
  adam_intro: () => openAdamIntro({ sim: true }),
  adam_intro_result: () => openAdamIntro({ sim: true, jump: true, demo: { focus: "study", struggle: "start", energy: "morning", span: "s25", hours: "h2", talk: "month" } }),
  inbox: () => openInbox({ demo: SIM_INBOX }),
  habit_edit: () => openHabitEdit(0, { habit: HABIT_EDIT_SIM, sim: true }),
  habit_undo: () => { closeHabitUndo(); setTimeout(() => openHabitUndo(0, { habit: HABIT_UNDO_SIM, sim: true }), 260); },
  trial_card: () => showTrialCardDemo(SIM_TRIAL),
  trial_sheet: () => { closeTrialPaywall(); closeTrialSheet(); setTimeout(() => openTrialSheet({ demo: SIM_TRIAL }), 260); },
  trial_paywall_intro: () => showTrialPaywall(SIM_TRIAL_INTRO_EXPIRED, { demo: true }),
  trial_paywall_full: () => showTrialPaywall(SIM_TRIAL_FULL_EXPIRED, { demo: true }),
  trial_timer: () => showTrialCardDemo(SIM_TRIAL_LAST),
  partner_screen: () => openPartnerScreen({ demo: SIM_PARTNER }),
  inviter_card: () => renderInviterCard(SIM_INVITER, { demo: true }),
  trial_offer_app: () => { closeTrialPaywall(); showTrialOffer(SIM_OFFER, { demo: true }); },
  trial_chat_offer: () => { window.location.href = "/coach?sim=trial_offer"; },
  sub_paid: () => { closeTrialPaywall(); openSubscriptionScreen({ source: "sim", demo: SIM_TRIAL_PAID, overview: SIM_SUB_AUTO }); },
  sub_pending: () => { closeTrialPaywall(); openSubscriptionScreen({ source: "sim", demo: { ...SIM_TRIAL, payment_pending: true }, overview: { ...SIM_SUB_OVERVIEW, payment_pending: true } }); },
  sub_summary: () => { closeTrialPaywall(); openSubscriptionScreen({ source: "sim", demo: SIM_TRIAL }).then(() => showTrialSummary()); },
  remind_friends: () => openRemindSheetDemo(SIM_REMIND_FRIENDS),
  adam_voice: () => showVoiceToastsDemo(),
  freeze_help: () => openFreezeHelp({ sim: true, friend: SIM_FRIEND, price: 200, coins: 640 }),
  freeze_help_sent: () => openFreezeHelp({ sim: true, friend: SIM_FRIEND, price: 200, coins: 440, sent: true }),
  rescue_help: () => openRescueHelp({ sim: true, friend: SIM_RESCUE_FRIEND }),
  rescue_help_sent: () => openRescueHelp({ sim: true, friend: SIM_RESCUE_FRIEND, sent: true, restored: 13 }),
  quests_plan: () => openQuestsWindow({
    demo: true, habitsCount: 2, planReady: true, monthBefore: simMonth(21), monthAfter: simMonth(22),
    before: [simQuestHabits(2), simQuestPlan(true, false), simQuestAdam(false)],
    after: [simQuestHabits(2), simQuestPlan(true, true), simQuestAdam(false)],
  }),
  quests_first: () => openQuestsWindow({
    demo: true, habitsCount: 1, planReady: false, monthBefore: simMonth(14), monthAfter: simMonth(14),
    before: [simQuestHabits(0), simQuestPlan(false, false), simQuestAdam(false)],
    after: [simQuestHabits(1), simQuestPlan(true, false), simQuestAdam(false)],
  }),
  quests_done: () => openQuestsWindow({
    demo: true, habitsCount: 2, monthBefore: simMonth(14), monthAfter: simMonth(15),
    before: [simQuestHabits(1), simQuestPlan(true, false), simQuestAdam(true)],
    after: [simQuestHabits(2), simQuestPlan(true, false), simQuestAdam(true)],
  }),
  quests_all: () => openQuestsWindow({
    demo: true, habitsCount: 2, monthBefore: simMonth(29), monthAfter: simMonth(30),
    before: [simQuestHabits(2), simQuestPlan(true, false), simQuestAdam(true)],
    after: [simQuestHabits(2), simQuestPlan(true, true), simQuestAdam(true)],
  }),
  quests_chests: () => openQuestsWindow({
    demo: true, habitsCount: 2, planReady: true, monthBefore: simMonth(29), monthAfter: simMonth(29),
    before: [simQuestHabits(2), simQuestPlan(true, true), simQuestAdam(true, true)],
    after: [simQuestHabits(2), simQuestPlan(true, true), simQuestAdam(true, true)],
  }),
  plan_hint: () => guideToPlanStep({ demo: true }),
  haptic_triple: () => {
    haptic("triple");
    showToast("📳 Тройная долгая вибрация — так ощущается сложное достижение", "success", 3600);
  },
  levelup: () => showLevelUp(12, 11),
  levelup_milestone: () => showLevelUp(30, 29),
  streak_status: () => openStreakCelebration({
    streak: 7, message: "Отличный ход.", reward: { status: "Старт", frame: "Первая искра" },
    last7: Array(7).fill({ status: "completed" }),
  }),
  streak: () => openStreakCelebration({
    streak: 7, message: "Отличный ход.",
    last7: ["completed", "completed", "freeze", "completed", "completed", "completed", "completed"].map((status) => ({ status })),
  }),
};

async function initSimMode() {
  const key = new URLSearchParams(location.search).get("sim");
  if (!key || !state?.user?.is_admin) return;
  try {
    const list = ((await api("/api/admin/lab/sims")).scenes || []).filter((item) => SIM_SCENES[item.key]);
    if (!list.length) return;
    simMode = { list, index: Math.max(0, list.findIndex((item) => item.key === key)) };
    renderSimBar();
    setTimeout(playSimScene, 500);
  } catch (err) {
    console.error("initSimMode failed:", err);
    simMode = null;
  }
}

function playSimScene() {
  closeTrialPaywall();                      // окна триала от прошлой сцены не должны перекрывать следующую
  closeTrialSheet();
  closeRescueHelp(true);                    // и «Серию можно спасти»: повтор той же сцены заново запускает анимацию значка
  if (document.getElementById("dailyQuestsOverlay")?.classList.contains("is-open")) closeQuestsOverlay();   // окно «Квесты дня» от прошлой сцены
  const item = simMode?.list[simMode.index];
  const run = item && SIM_SCENES[item.key];
  if (!run) return;
  Promise.resolve(run()).catch((err) => console.error("sim scene failed:", err));
}

const SIM_ICON_REPLAY = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20 12a8 8 0 1 1-2.6-5.9M20 4v5h-5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function renderSimBar() {
  let bar = document.getElementById("simBar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "simBar";
    bar.className = "lab-bar";
    document.body.appendChild(bar);
    bar.addEventListener("click", (e) => {
      const act = e.target.closest("[data-sim]")?.dataset.sim;
      if (!act || !simMode) return;
      haptic("light");
      if (act === "exit") { window.location.href = "/admin"; return; }
      if (act !== "replay") {
        const n = simMode.list.length;
        simMode.index = (simMode.index + (act === "next" ? 1 : n - 1)) % n;
        renderSimBar();
      }
      playSimScene();
    });
  }
  const item = simMode.list[simMode.index];
  bar.innerHTML = `
    <button type="button" class="lab-bar__btn" data-sim="prev" aria-label="Предыдущее окно">${LAB_ICON_PREV}</button>
    <button type="button" class="lab-bar__btn" data-sim="replay" aria-label="Повторить">${SIM_ICON_REPLAY}</button>
    <div class="lab-bar__text"><b>🎬 Окно ${simMode.index + 1}/${simMode.list.length}</b><small>${escapeHtml(item.title)}</small></div>
    <button type="button" class="lab-bar__btn" data-sim="next" aria-label="Следующее окно">${LAB_ICON_NEXT}</button>
    <button type="button" class="lab-bar__btn lab-bar__btn--exit" data-sim="exit" aria-label="Выйти в админку">${ICON_X}</button>`;
}

function togglePairHome() {
  pairHomeExpanded = !pairHomeExpanded;
  haptic("light");
  renderPairCard();
}

function initPairQuest() {
  const onCardClick = async (e) => {
    // Кнопка-действие (в т.ч. «＋ Союзник» в свёрнутой карточке) важнее сворачивания.
    const btn = e.target.closest("[data-pair-act]");
    if (btn) { if (!btn.disabled) await pairCardAction(btn); return; }
    if (e.target.closest("[data-pair-toggle]")) togglePairHome();
  };
  const home = document.getElementById("pairQuestCard");
  home?.addEventListener("click", onCardClick);
  home?.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-pair-toggle][role=button]")) { e.preventDefault(); togglePairHome(); }
  });
  // Рейтинг: кнопки на карточке работают как обычно, а тап по любому другому месту открывает короткую инструкцию.
  document.getElementById("pairQuestCardRating")?.addEventListener("click", (e) => {
    if (e.target.closest("button, a, [data-pair-act], [data-pair-toggle]")) return onCardClick(e);
    openPairGuide();
  });

  // Краткая версия в «Квестах дня»: есть задание/приглашение — ведёт к полной карточке в Рейтинге, иначе — к выбору союзника.
  const mini = document.getElementById("pairQuestMini");
  const openFromMini = async () => {
    const pq = state?.pair_quest;
    if (!pq || labMode) return;
    document.getElementById("dailyQuestsClose")?.click();
    if (pq.quest || pq.incoming.length || pq.outgoing) {
      document.querySelector('.tab-bar__item[data-tab="rating"]')?.click();
      setTimeout(() => document.getElementById("pairQuestCardRating")?.scrollIntoView({ behavior: "smooth", block: "start" }), 350);
    } else if (pq.needs_habit) {
      showToast(friendlyError({ data: { error: "needs_habit" } }), "error");
    } else if (pq.can_choose) {
      await openPairSheet();
    }
  };
  mini?.addEventListener("click", openFromMini);
  mini?.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openFromMini(); } });
  document.getElementById("pairSheetList")?.addEventListener("click", async (e) => {
    const action = e.target.closest("[data-pair-act]");
    if (action) { await pairSheetAction(action); return; }
    const row = e.target.closest("[data-pair-friend]");
    if (!row || row.classList.contains("is-disabled") || pairBusy) return;
    pairSelectedId = Number(row.dataset.pairFriend);
    haptic("select");
    renderPairSheet();
  });
  document.getElementById("pairSheetConfirm")?.addEventListener("click", confirmPairInvite);
  document.getElementById("pairSheetList")?.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches(".pair-option")) { e.preventDefault(); e.target.click(); }
  });
  document.getElementById("pairSheetAdd")?.addEventListener("click", (e) => pairSheetAction(e.currentTarget));
  document.getElementById("pairSheetLater")?.addEventListener("click", () => {
    haptic("light");
    if (pairSheetView === "people") { pairSheetView = "add"; renderPairSheet(); return; }
    if (pairSheetView === "add") { pairSheetView = "pick"; renderPairSheet(); return; }
    closePairSheet();
  });
  document.getElementById("pairBackdrop")?.addEventListener("click", closePairSheet);

  const guide = document.getElementById("pairGuideOverlay");
  guide?.addEventListener("click", (e) => {
    if (performance.now() - pairGuideOpenedAt < 300) return;        // двойной тап по карточке не должен тут же закрыть окно
    if (e.target === guide || e.target.closest("#pairGuideClose")) { haptic("light"); closePairGuide(); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePairGuide(); });
}

// ===================== ЭВОЛЮЦИИ ADAM И РАНГИ =====================
// Постоянный ранг по лучшей серии (db/evolution.py): SPARK → FLOW → CONTROL →
// APEX → LEGEND, 12 эволюций. Сервер считает всё — уровень, прогресс, лестницу и
// pending (эволюция, праздник которой ещё не показывали); здесь только
// отрисовка: значок ранга, окно «Эволюции», праздник на весь экран и ранг в
// профиле, рейтинге и списках друзей. Эволюции остаются после срыва серии.
const EVO_ICONS = {
  spark: '<path d="M8 .8l1.9 5.3 5.3 1.9-5.3 1.9L8 15.2l-1.9-5.3L.8 8l5.3-1.9z"/>',
  flow: '<path d="M1.5 7.5L8 2l6.5 5.5-1.5 1.7L8 4.9 3 9.2z"/><path d="M1.5 13L8 7.5l6.5 5.5-1.5 1.7L8 10.4l-5 4.3z"/>',
  control: '<path fill-rule="evenodd" d="M8 1l6 3.5v7L8 15l-6-3.5v-7zM8 3.3L4.2 5.5v5L8 12.7l3.8-2.2v-5zM8 6.2a1.8 1.8 0 100 3.6 1.8 1.8 0 000-3.6z"/>',
  apex: '<path d="M.8 14.2L6 4.8l2.4 3.8 1.9-2.6 4.9 8.2z"/>',
  legend: '<path d="M1 12.8L.6 4.2l3.9 3.4L8 2.4l3.5 5.2 3.9-3.4-.4 8.6z"/>',
};
const EVOLUTION_REVEAL_TEXT = "Эта моя форма теперь с тобой навсегда — даже срыв серии её не отнимет. Друзья увидят твой ранг.";

function evoLadder() { return state?.hero?.evolution?.ladder || []; }

function evoRankByLevel(level) {
  const n = Number(level) || 0;
  return n > 0 ? (evoLadder()[n - 1] || null) : null;
}

function evoIconSvg(family) {
  const body = EVO_ICONS[family] || '<circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" stroke-width="1.6"/>';
  return `<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">${body}</svg>`;
}

// rank — запись лестницы или описание эволюции из профиля (нужны family и name).
function evoBadgeHtml(rank, size) {
  if (!rank || !rank.family || !rank.name) return "";
  return `<span class="evo-badge${size ? ` evo-badge--${size}` : ""}" data-evo-family="${escapeHtml(rank.family)}">${evoIconSvg(rank.family)}<span>${escapeHtml(rank.name)}</span></span>`;
}

// Маленький значок в строках списков: в данных строки только уровень (evo).
function evoChipHtml(level) { return evoBadgeHtml(evoRankByLevel(level), "chip"); }

// ----- Карточка героя в профиле -----
function renderHeroEvoRow(evo) {
  const row = document.getElementById("heroEvoRow");
  const wrap = document.getElementById("heroWidget");
  if (!row) return;
  if (!evo || !Array.isArray(evo.ladder)) {
    row.hidden = true;
    if (wrap) delete wrap.dataset.evoFamily;
    return;
  }
  const level = Number(evo.level) || 0;
  const next = evo.next;
  if (wrap) {
    if (evo.family) wrap.dataset.evoFamily = evo.family;
    else delete wrap.dataset.evoFamily;
  }
  row.dataset.evoFamily = evo.family || (next && next.family) || "spark";
  row.classList.toggle("is-locked", level === 0);
  let hint;
  if (!next) hint = "Максимальная эволюция — выше только слава.";
  else if (Number(evo.streak) < Number(evo.best_streak)) hint = `Рекорд ${pairDaysWord(Number(evo.best_streak))} — открытые эволюции остаются навсегда`;
  else hint = `До ${next.name} — ещё ${pairDaysWord(Number(next.days_left))}`;
  row.innerHTML = `
    <span class="hero-widget__evo-top">
      ${level > 0 ? evoBadgeHtml(evo) : `<span class="hero-widget__evo-none">Эволюция не открыта</span>`}
      <span class="hero-widget__evo-count">${level}/${Number(evo.total) || 12} ›</span>
    </span>
    <span class="hero-widget__evo-bar"><i style="width:${Math.max(0, Math.min(100, Number(evo.percent) || 0))}%"></i></span>
    <span class="hero-widget__evo-hint">${escapeHtml(hint)}</span>`;
  row.hidden = false;
}

// ----- Окно «Эволюции» (свой профиль и чужой) -----
let evoSheetCtx = null;

function renderEvolutionSheet() {
  const ctx = evoSheetCtx;
  const body = document.getElementById("evolutionSheetBody");
  if (!ctx || !body) return;
  const evo = ctx.evo;
  const ladder = evoLadder();
  const level = Number(evo.level) || 0;
  const total = Number(evo.total) || ladder.length || 12;
  const next = evo.next;
  const family = evo.family || (next && next.family) || "spark";
  const title = ctx.self ? "Твои эволюции" : `Эволюции — ${ctx.name || "игрок"}`;
  let sub;
  if (ctx.self) {
    sub = level > 0
      ? "ADAM открывает новые формы за серию. Открытые остаются с тобой навсегда."
      : "Серия 3 дня — и ADAM откроет первую эволюцию.";
  } else {
    sub = evo.best_streak != null
      ? `Лучшая серия — ${pairDaysWord(Number(evo.best_streak) || 0)}.`
      : "Ранг считается по лучшей серии и остаётся навсегда.";
  }
  const image = ctx.showcase?.image || "";
  const stats = `
    <div class="evo-sheet-stats">
      <div class="evo-stat"><b>${Number(evo.streak) || 0}</b><small>${ruPlural(Number(evo.streak) || 0, ["день", "дня", "дней"])} подряд</small></div>
      <div class="evo-stat"><b>${level}/${total}</b><small>эволюций открыто</small></div>
      <div class="evo-stat"><b>${next ? Number(next.days) : "MAX"}</b><small>${next ? "дней до следующей" : "выше только слава"}</small></div>
    </div>`;
  const steps = ladder.map((e) => {
    const unlocked = e.level <= level;
    const current = e.level === level;
    return `<li class="evo-step ${unlocked ? "is-unlocked" : "is-locked"}${current ? " is-current" : ""}" data-evo-family="${escapeHtml(e.family)}">
      <span class="evo-step__n">${String(e.level).padStart(2, "0")}</span>
      ${evoBadgeHtml(e)}
      ${current ? `<span class="evo-step__tag">сейчас</span>` : ""}
      <span class="evo-step__days">${unlocked ? "✓ " : ""}${Number(e.days)} дн.</span>
    </li>`;
  }).join("");
  body.dataset.evoFamily = family;
  body.innerHTML = `
    <div class="evo-sheet-head">
      ${image ? `<span class="evo-sheet-portrait"><img src="${escapeHtml(image)}" alt="" decoding="async"></span>` : ""}
      <div class="evo-sheet-head__body">
        ${level > 0 ? evoBadgeHtml(evo, "big") : `<span class="hero-widget__evo-none">Эволюция не открыта</span>`}
        <h3 class="evo-sheet-head__title" id="evolutionSheetTitle">${escapeHtml(title)}</h3>
        <p class="evo-sheet-head__sub">${escapeHtml(sub)}</p>
      </div>
    </div>
    ${stats}
    <ul class="evo-ladder">${steps}</ul>
    ${ctx.self && level > 0 ? `<button type="button" class="pair-btn pair-btn--ghost" data-evo-replay>▶ Показать праздник заново</button>` : ""}`;
}

function openEvolutionSheet(ctx) {
  const sheet = document.getElementById("evolutionSheet");
  if (!sheet || !ctx || !ctx.evo) return;
  evoSheetCtx = ctx;
  renderEvolutionSheet();
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  const card = sheet.querySelector(".evo-sheet__card");
  if (card) card.scrollTop = 0;
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeEvolutionSheet() {
  const sheet = document.getElementById("evolutionSheet");
  if (!sheet || sheet.hidden) return;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!sheet.classList.contains("is-open")) sheet.hidden = true; }, 230);
}

function openOwnEvolutionSheet() {
  const hero = state?.hero;
  if (!hero || !hero.evolution) return;
  openEvolutionSheet({ evo: hero.evolution, showcase: hero, name: "", self: true });
}

// ----- Эволюция в чужом профиле: «живой» аватар и ранг -----
function userEvolutionCardHtml(p) {
  const evo = p.evolution;
  if (!evo) return "";
  const level = Number(evo.level) || 0;
  const next = evo.next;
  const family = evo.family || (next && next.family) || "spark";
  const image = p.showcase?.image || "";
  return `
    <button type="button" class="up-evo" data-up="evo" data-evo-family="${escapeHtml(family)}" aria-label="Эволюции">
      <span class="up-evo__portrait">${image ? `<img src="${escapeHtml(image)}" alt="" decoding="async">` : ""}</span>
      <span class="up-evo__body">
        ${level > 0 ? evoBadgeHtml(evo, "big") : `<span class="hero-widget__evo-none">Эволюция не открыта</span>`}
        <span class="up-evo__line">Эволюций открыто: <b>${level}</b> из ${Number(evo.total) || 12}</span>
        <span class="up-evo__next">${next
          ? `Следующая: ${escapeHtml(next.name)} — ${pairDaysWord(Number(next.days))} серии`
          : "Максимальная эволюция"}</span>
      </span>
      <span class="up-evo__chev" aria-hidden="true">›</span>
    </button>`;
}

let profileHeroVideo = null;
let profileHeroTimer = null;

function dropProfileHeroVideo() {
  clearTimeout(profileHeroTimer);
  profileHeroTimer = null;
  if (profileHeroVideo) {
    profileHeroVideo.pause();
    profileHeroVideo.remove();
    profileHeroVideo = null;
  }
}

// Аватар в чужом профиле живой — та же беззвучная петля, что и в своей
// карточке, с теми же ограничениями (не на слабых устройствах, не дольше 30 с).
function attachProfileHeroVideo() {
  dropProfileHeroVideo();
  const holder = document.querySelector("#userProfileBody .up-evo__portrait");
  const showcase = profileData?.showcase;
  if (!holder || !showcase || !showcase.video || !heroMotionAllowed()) return;
  const v = createHeroVideo(showcase, "up-evo__video");
  holder.appendChild(v);
  profileHeroVideo = v;
  const p = v.play();
  if (p && p.catch) p.catch(() => {});
  profileHeroTimer = setTimeout(() => v.pause(), HERO_VIDEO_MAX_PLAY_MS);
}

// ----- Праздник «EVOLUTION N UNLOCKED» -----
// Экран слегка темнеет → аватар замирает, интерфейсные звуки глохнут → первый
// голубой поток → второй → собирается золото → яркая вспышка → «EVOLUTION 01
// UNLOCKED» → аватар возвращается в обычную петлю. Тап до конца — пропустить.
// Каждая эволюция празднуется один раз (сервер хранит seen_level); на слабых
// устройствах и при reduced-motion — короткий вариант без потоков и вспышки.
const evolutionRun = { active: false, watch: null, timers: [], hideTimer: null, played: 0, level: 0, replay: false, video: null, revealed: false };

function evolutionAt(ms, fn) { evolutionRun.timers.push(setTimeout(fn, ms)); }

function clearEvolutionTimers() {
  evolutionRun.timers.forEach(clearTimeout);
  evolutionRun.timers = [];
}

function dropEvolutionVideo() {
  if (evolutionRun.video) {
    evolutionRun.video.pause();
    evolutionRun.video.remove();
    evolutionRun.video = null;
  }
}

// Мягкий нарастающий аккорд на вспышке — после тишины, которую создаёт
// evolutionSilence (playChime в это время молчит).
function playEvolutionSwell() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const t = ctx.currentTime;
    const master = ctx.createGain();
    master.gain.setValueAtTime(0.0001, t);
    master.gain.exponentialRampToValueAtTime(0.1, t + 0.25);
    master.gain.exponentialRampToValueAtTime(0.0001, t + 1.6);
    master.connect(ctx.destination);
    [220, 330, 440].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, t);
      osc.frequency.exponentialRampToValueAtTime(freq * 1.5, t + 0.7);
      osc.connect(master);
      osc.start(t + i * 0.04);
      osc.stop(t + 1.7);
    });
    setTimeout(() => ctx.close(), 1900);
  } catch (_) {}
}

async function ackEvolution(level) {
  try {
    const data = await api("/api/evolution/seen", { method: "POST", body: JSON.stringify({ level }), timeoutMs: 8000 });
    if (data && data.evolution && state?.hero) {
      state.hero.evolution = data.evolution;
      renderHeroWidget();
    }
  } catch (_) {
    // Не дошло — праздник покажется снова при следующем обновлении, ничего страшного.
  }
}

function revealEvolution() {
  const overlay = document.getElementById("evolutionOverlay");
  if (!overlay || overlay.hidden || evolutionRun.revealed) return;
  evolutionRun.revealed = true;
  overlay.classList.add("is-reveal", "is-revealed");
  evolutionSilence = false;
  const v = evolutionRun.video;
  if (v) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
  if (!evolutionRun.replay) ackEvolution(evolutionRun.level);
  document.getElementById("evolutionContinue")?.focus({ preventScroll: true });
}

function skipEvolutionBuildup() {
  const overlay = document.getElementById("evolutionOverlay");
  if (!overlay || overlay.hidden || !evolutionRun.active || evolutionRun.revealed) return;
  clearEvolutionTimers();
  overlay.classList.add("is-dim", "is-gold");
  revealEvolution();
}

function playEvolutionCinematic(level, { replay = false } = {}) {
  const overlay = document.getElementById("evolutionOverlay");
  const rank = evoRankByLevel(level);
  const hero = state?.hero;
  if (!overlay || !rank || !hero) return false;
  clearEvolutionTimers();
  clearTimeout(evolutionRun.hideTimer);
  dropEvolutionVideo();
  const lite = !heroMotionAllowed();
  evolutionRun.active = true;
  evolutionRun.level = Number(level);
  evolutionRun.replay = replay;
  evolutionRun.revealed = false;
  if (!replay) evolutionRun.played = Math.max(evolutionRun.played, Number(level));
  evolutionSilence = true;
  updateHeroMotion(); // видео карточки героя — на паузу, пока идёт праздник

  document.getElementById("evolutionKicker").textContent = `EVOLUTION ${String(level).padStart(2, "0")}`;
  document.getElementById("evolutionRank").innerHTML = evoBadgeHtml(rank, "big");
  document.getElementById("evolutionText").textContent = EVOLUTION_REVEAL_TEXT;
  const img = document.getElementById("evolutionImg");
  if (img) img.src = hero.image;
  const sparks = document.getElementById("evolutionSparks");
  if (sparks) {
    sparks.innerHTML = "";
    if (!lite) {
      for (let i = 0; i < 18; i++) {
        const angle = (Math.PI * 2 * i) / 18 + Math.random() * 0.3;
        const dist = 120 + Math.random() * 70;
        const dot = document.createElement("i");
        dot.style.setProperty("--x", `${Math.round(Math.cos(angle) * dist)}px`);
        dot.style.setProperty("--y", `${Math.round(Math.sin(angle) * dist * 1.1)}px`);
        dot.style.setProperty("--d", `${Math.round(Math.random() * 450)}ms`);
        sparks.appendChild(dot);
      }
    }
  }
  const avatar = document.getElementById("evolutionAvatar");
  avatar?.querySelector(".evo-avatar__video")?.remove();
  if (avatar && hero.video && !lite) {
    const v = createHeroVideo(hero, "evo-avatar__video");
    avatar.appendChild(v);
    evolutionRun.video = v;
    const p = v.play();
    if (p && p.catch) p.catch(() => {});
  }

  overlay.className = `evo-overlay${lite ? " is-lite" : ""}`;
  overlay.dataset.evoFamily = rank.family;
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  void overlay.offsetWidth; // чтобы затемнение прошло плавно, а не включилось сразу
  const add = (cls) => overlay.classList.add(cls);

  evolutionAt(30, () => add("is-dim"));
  if (lite) {
    evolutionAt(700, () => { haptic("triple"); revealEvolution(); });
  } else {
    evolutionAt(700, () => { evolutionRun.video?.pause(); add("is-s1"); haptic("select"); });
    evolutionAt(1900, () => { add("is-s2"); haptic("light"); });
    evolutionAt(3100, () => { add("is-gold"); haptic("confirm"); });
    evolutionAt(4300, () => { add("is-flash"); haptic("triple"); playEvolutionSwell(); });
    evolutionAt(4800, revealEvolution);
  }
  return true;
}

function closeEvolutionCinematic({ toProfile = false } = {}) {
  const overlay = document.getElementById("evolutionOverlay");
  if (!overlay || overlay.hidden || overlay.classList.contains("is-leaving")) return;
  clearEvolutionTimers();
  dropEvolutionVideo();
  evolutionRun.active = false;
  evolutionSilence = false;
  overlay.classList.add("is-leaving");
  clearTimeout(evolutionRun.hideTimer);
  evolutionRun.hideTimer = setTimeout(() => {
    overlay.hidden = true;
    overlay.className = "evo-overlay";
    overlay.setAttribute("aria-hidden", "true");
    updateHeroMotion(); // аватар в карточке снова играет свою петлю
  }, 350);
  if (toProfile) document.querySelector('.tab-bar__item[data-tab="profile"]')?.click();
}

// Новая эволюция ждёт своей очереди: сначала уходят праздник серии, новый
// уровень, подсказки онбординга — потом полторы секунды тишины и наш экран.
function queueEvolutionCelebration() {
  const wanted = Number(state?.hero?.evolution?.pending) || 0;
  if (!wanted || wanted <= evolutionRun.played || evolutionRun.active || evolutionRun.watch) return;
  const startedAt = Date.now();
  let quiet = 0;
  const stop = () => { clearInterval(evolutionRun.watch); evolutionRun.watch = null; };
  evolutionRun.watch = setInterval(() => {
    if (Date.now() - startedAt > 120000) { stop(); return; }
    if (document.hidden || celebrationOverlayOpen({ skipEvolution: true })) { quiet = 0; return; }
    quiet += 1;
    if (quiet < 2) return;
    stop();
    const level = Number(state?.hero?.evolution?.pending) || 0;
    if (level > evolutionRun.played) playEvolutionCinematic(level);
  }, 700);
}

function initEvolution() {
  document.getElementById("heroEvoRow")?.addEventListener("click", () => { haptic("light"); openOwnEvolutionSheet(); });
  document.getElementById("evolutionSheetBackdrop")?.addEventListener("click", closeEvolutionSheet);
  document.getElementById("evolutionSheetClose")?.addEventListener("click", closeEvolutionSheet);
  document.getElementById("evolutionSheetBody")?.addEventListener("click", (e) => {
    if (!e.target.closest("[data-evo-replay]")) return;
    const level = Number(evoSheetCtx?.evo?.level) || 0;
    closeEvolutionSheet();
    if (level > 0) setTimeout(() => playEvolutionCinematic(level, { replay: true }), 260);
  });
  const overlay = document.getElementById("evolutionOverlay");
  overlay?.addEventListener("click", (e) => {
    if (e.target.closest(".evo-reveal__actions")) return;
    skipEvolutionBuildup();
  });
  document.getElementById("evolutionContinue")?.addEventListener("click", () => { haptic("light"); closeEvolutionCinematic(); });
  document.getElementById("evolutionToProfile")?.addEventListener("click", () => { haptic("light"); closeEvolutionCinematic({ toProfile: true }); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (overlay && !overlay.hidden) { if (evolutionRun.revealed) closeEvolutionCinematic(); else skipEvolutionBuildup(); return; }
    closeEvolutionSheet();
  });
}

// ===================== ПОДПИСКИ И ПРОФИЛИ ИГРОКОВ =====================
// Как в Duolingo: подписаться можно на любого (например, на топ рейтинга),
// взаимная подписка = друзья. В чужом профиле видно ровно то, что разрешает
// сервер (db/profiles.py::VISIBLE_SECTIONS): без подписки — только базовое,
// подписчику — график недели, обзор, достижения, другу — ещё и расширенная
// статистика. Названия привычек сервер не отдаёт никому.
let profileData = null;
let profileReportOpen = false;
let followListKind = "following";
let followListData = null;

const REPORT_REASONS_UI = [
  ["spam", "Спам"],
  ["abuse", "Оскорбления"],
  ["fake", "Фейк / подделка"],
  ["other", "Другое"],
];

// Что делает основная кнопка на профиле/в списке при данном отношении.
function relationAction(rel) {
  if (rel.blocked_by_me) return { act: "unblock", label: "Разблокировать", cls: "ghost" };
  if (rel.friends) return { act: "unfollow", label: "🤝 Друзья", cls: "friends" };
  if (rel.following) return { act: "unfollow", label: "✓ Вы подписаны", cls: "following" };
  if (rel.followed_by) return { act: "follow", label: "Подписаться в ответ", cls: "primary" };
  return { act: "follow", label: "Подписаться", cls: "primary" };
}

function niceChartStep(raw) {
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

// Линейный график опыта за 7 дней: у владельца профиля и (если есть) у тебя.
function weekChartSvg(chart) {
  const W = 340, H = 186, L = 38, R = 14, T = 12, B = 30;
  const target = (chart.target || []).map(Number);
  const viewer = chart.viewer ? chart.viewer.map(Number) : null;
  const labels = chart.labels || [];
  const max = Math.max(1, ...target, ...(viewer || []));
  const step = Math.max(1, niceChartStep(max / 3)); // опыт целый — дробных делений не бывает
  const top = step * 3;
  const x = (i) => L + (W - L - R) * (labels.length > 1 ? i / (labels.length - 1) : 0.5);
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const grid = [0, 1, 2, 3].map((k) => {
    const v = step * k;
    return `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="up-chart__grid"/>` +
      `<text x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" class="up-chart__tick">${v}</text>`;
  }).join("");
  const xLabels = labels.map((l, i) =>
    `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="up-chart__label">${escapeHtml(l)}</text>`).join("");
  const line = (values, cls) =>
    `<polyline points="${values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}" class="up-chart__line ${cls}"/>` +
    values.map((v, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4.5" class="up-chart__dot ${cls}"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="up-chart" role="img" aria-label="Опыт за неделю">` +
    `${grid}${xLabels}${viewer ? line(viewer, "up-chart--viewer") : ""}${line(target, "up-chart--target")}</svg>`;
}

function weekBarsHtml(labels, values) {
  const nums = values.map(Number);
  const max = Math.max(1, ...nums);
  return `<div class="up-bars">${nums.map((v, i) => `
    <div class="up-bars__col"><span class="up-bars__val">${v}</span>
      <span class="up-bars__bar" style="height:${Math.max(4, Math.round(54 * v / max))}px"></span>
      <span class="up-bars__label">${escapeHtml(labels[i] || "")}</span></div>`).join("")}</div>`;
}

function renderUserProfile() {
  const p = profileData;
  const body = document.getElementById("userProfileBody");
  const title = document.getElementById("userProfileTitle");
  if (!p || !body) return;
  const rel = p.relation || {};
  const name = p.first_name || "Игрок";
  if (title) title.textContent = p.handle ? `@${p.handle}` : name;
  const since = /^\d{4}/.test(String(p.member_since || "")) ? `В ADAM с ${String(p.member_since).slice(0, 4)} года` : "";
  const sections = new Set(p.sections || []);

  const countsHtml = rel.self
    ? `<button type="button" class="up-count" data-up-list="following"><b>${Number(p.following)}</b><span>Подписки</span></button>
       <button type="button" class="up-count" data-up-list="followers"><b>${Number(p.followers)}</b><span>Подписчики</span></button>`
    : `<div class="up-count"><b>${Number(p.following)}</b><span>Подписки</span></div>
       <div class="up-count"><b>${Number(p.followers)}</b><span>Подписчики</span></div>`;

  let action = "";
  if (!rel.self) {
    const a = relationAction(rel);
    action = `<button type="button" class="up-btn up-btn--${a.cls}" data-up="${a.act}">${a.label}</button>`;
    // Подарки — только друзьям (взаимная подписка); сервер проверяет это же.
    if (rel.friends) {
      action += `<button type="button" class="up-btn up-btn--gift" data-up="gift">🎁 Подарить</button>`;
    }
  }

  let html = `
    <section class="up-hero">
      <div class="up-avatar ${avatarFrameClass(p)}">${avatarInner(p)}</div>
      <div class="up-name">${escapeHtml(name)}${p.badge ? " 🏅" : ""}</div>
      ${p.handle ? `<div class="up-handle">@${escapeHtml(p.handle)}</div>` : ""}
      <div class="up-meta">${[since, p.league_tier ? escapeHtml(p.league_tier) : "", Number(p.streak) > 0 ? `🔥 ${Number(p.streak)}` : ""].filter(Boolean).join(" · ")}</div>
    </section>
    ${userEvolutionCardHtml(p)}
    <div class="up-counts">${countsHtml}</div>
    ${action}`;

  if (sections.has("chart") && p.chart) {
    const c = p.chart;
    const allZero = [...c.target, ...(c.viewer || [])].every((v) => !Number(v));
    html += `
      <section class="up-card">
        <div class="up-card__title">Прогресс за неделю</div>
        ${weekChartSvg(c)}
        ${allZero ? `<div class="up-note">За эту неделю опыта пока нет.</div>` : ""}
        <div class="up-legend">
          <div class="up-legend__row"><i class="up-legend__dot up-legend__dot--target"></i><span>${escapeHtml(name)}</span><b>Опыт: ${Number(c.target_total)}</b></div>
          ${c.viewer ? `<div class="up-legend__row"><i class="up-legend__dot up-legend__dot--viewer"></i><span>Вы</span><b>Опыт: ${Number(c.viewer_total)}</b></div>` : ""}
        </div>
      </section>`;
  }
  if (sections.has("overview") && p.overview) {
    const o = p.overview;
    html += `
      <section class="up-card">
        <div class="up-card__title">Обзор</div>
        <div class="up-grid">
          <div class="up-stat"><span>🔥</span><b>${Number(o.streak)}</b><small>дней подряд</small></div>
          <div class="up-stat"><span>📈</span><b>${Number(o.best_streak)}</b><small>лучшая серия</small></div>
          <div class="up-stat"><span>🏆</span><b>${escapeHtml(String(o.league).replace(/^\S+\s/, ""))}</b><small>лига</small></div>
          <div class="up-stat"><span>⚡</span><b>${Number(o.total_xp)}</b><small>очков всего</small></div>
          <div class="up-stat"><span>⭐</span><b>${Number(o.level)}</b><small>уровень</small></div>
          <div class="up-stat"><span>✅</span><b>${Number(o.total_completed)}</b><small>отмечено</small></div>
        </div>
      </section>`;
  }
  if (sections.has("achievements") && p.achievements) {
    html += `
      <section class="up-card">
        <div class="up-card__title">Достижения${p.achievements_count ? ` · ${Number(p.achievements_count)}` : ""}</div>
        ${p.achievements.length
          ? `<div class="up-chips">${p.achievements.map((a) => `<span class="up-chip">${escapeHtml(a.icon || "🏅")} ${escapeHtml(a.title)}</span>`).join("")}</div>`
          : `<div class="up-note">Пока нет достижений.</div>`}
      </section>`;
  }
  if (sections.has("extended") && p.extended) {
    const x = p.extended;
    const today = x.today || {};
    html += `
      <section class="up-card up-card--friend">
        <div class="up-card__title">Статистика друга</div>
        <div class="up-grid">
          <div class="up-stat"><span>${today.done ? "✅" : "⏳"}</span><b>${Number(today.completed)}/${Number(today.total)}</b><small>привычек сегодня</small></div>
          <div class="up-stat"><span>🎯</span><b>${x.completion_rate_30d == null ? "—" : `${Number(x.completion_rate_30d)}%`}</b><small>выполнение за 30 дней</small></div>
          <div class="up-stat"><span>📅</span><b>${Number(x.active_days_30)}/30</b><small>активных дней</small></div>
          <div class="up-stat"><span>🏅</span><b>${x.season ? `#${Number(x.season.rank)}` : "—"}</b><small>место в сезоне</small></div>
        </div>
        <div class="up-card__subtitle">Отмечено привычек по дням</div>
        ${weekBarsHtml(p.chart ? p.chart.labels : [], x.week_completed || [])}
      </section>`;
    if (x.habits_shared && Array.isArray(x.habits)) {
      html += `
      <section class="up-card up-card--friend">
        <div class="up-card__title">Привычки${rel.self ? " · так видят друзья" : ""}</div>
        ${x.habits.length
          ? `<ul class="up-habits">${x.habits.map((h) => `
              <li class="up-habit${h.done ? " is-done" : ""}">
                <span class="up-habit__mark">${h.done ? "✓" : (h.skipped ? "⏸" : "○")}</span>
                <span class="up-habit__title">${escapeHtml(h.title)}</span>
              </li>`).join("")}</ul>`
          : `<div class="up-note">Привычек пока нет.</div>`}
        ${x.goals ? `<div class="up-card__subtitle">Цели</div><div class="up-goals">${escapeHtml(x.goals)}</div>` : ""}
      </section>`;
    } else {
      html += `<div class="up-lock">🙈 ${rel.self
        ? "Друзья видят только числа. Названия привычек и цели можно открыть им в Настройках."
        : `${escapeHtml(name)} не показывает друзьям названия привычек и цели.`}</div>`;
    }
  }

  // Подсказки про то, что откроется после подписки / взаимной подписки.
  if (!rel.self && !rel.blocked_by_me) {
    if (!sections.has("chart")) {
      html += `<div class="up-lock">🔒 ${rel.following
        ? `${escapeHtml(name)} открыл(а) статистику только друзьям. Когда подпишется в ответ — станете друзьями, и она откроется.`
        : "Подпишись, чтобы видеть прогресс за неделю, обзор и достижения."}</div>`;
    } else if (!sections.has("extended")) {
      html += `<div class="up-lock">🤝 Когда ${escapeHtml(name)} подпишется в ответ, вы станете друзьями — откроется расширенная статистика, и вы сможете подталкивать друг друга и дарить подарки.</div>`;
    }
  }

  if (!rel.self) {
    html += `
      <div class="up-footer">
        <button type="button" class="up-link" data-up="report-toggle">🚩 Пожаловаться</button>
        <button type="button" class="up-link up-link--danger" data-up="${rel.blocked_by_me ? "unblock" : "block"}">${rel.blocked_by_me ? "Разблокировать" : "⛔ Заблокировать"}</button>
      </div>`;
    if (profileReportOpen) {
      html += `
        <section class="up-card up-report">
          <div class="up-card__title">На что жалоба?</div>
          <div class="up-report__reasons">${REPORT_REASONS_UI.map(([code, label]) =>
            `<label class="up-report__reason"><input type="radio" name="upReportReason" value="${code}"> ${label}</label>`).join("")}</div>
          <textarea id="upReportComment" class="up-report__comment" maxlength="500" placeholder="Комментарий (необязательно)"></textarea>
          <button type="button" class="up-btn up-btn--primary" data-up="report-send">Отправить жалобу</button>
        </section>`;
    }
  }
  dropProfileHeroVideo();
  body.innerHTML = html;
  attachProfileHeroVideo();
}

async function openUserProfile(userId) {
  const overlay = document.getElementById("userProfileOverlay");
  if (!overlay) return;
  haptic("light");
  try {
    profileData = await api(`/api/users/${Number(userId)}/profile`, { timeoutMs: 10000 });
  } catch (err) {
    showToast(err?.data?.error === "not_found" ? "Профиль недоступен" : friendlyError(err), "error");
    return;
  }
  profileReportOpen = false;
  closeFollowListSheet();
  renderUserProfile();
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  const body = document.getElementById("userProfileBody");
  if (body) body.scrollTop = 0;
  requestAnimationFrame(() => overlay.classList.add("is-open"));
}

function closeUserProfile() {
  const overlay = document.getElementById("userProfileOverlay");
  if (!overlay || overlay.hidden) return;
  haptic("light");
  overlay.classList.remove("is-open");
  overlay.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!overlay.classList.contains("is-open")) overlay.hidden = true; }, 300);
  dropProfileHeroVideo();
  profileData = null;
}

// После подписки/отписки/блокировки меняется и то, что видно в профиле
// (уровень доступа), и карточка «Друзья», и открытый список подписок.
async function refreshAfterRelationChange() {
  const tasks = [loadFriendsCard()];
  if (profileData) {
    tasks.push(api(`/api/users/${Number(profileData.telegram_id)}/profile`, { timeoutMs: 10000 })
      .then((data) => { profileData = data; renderUserProfile(); })
      .catch(() => closeUserProfile()));
  }
  const sheet = document.getElementById("followListSheet");
  if (sheet && !sheet.hidden) tasks.push(loadFollowList(followListKind, { quiet: true }));
  await Promise.allSettled(tasks);
}

async function changeRelation(userId, action, displayName) {
  if (action === "unfollow" && !confirm(`Отписаться от ${displayName || "игрока"}?`)) return;
  if (action === "block" && !confirm(`Заблокировать ${displayName || "игрока"}? Вы перестанете видеть профили друг друга, подписки будут сняты.`)) return;
  try {
    await api(`/api/users/${Number(userId)}/${action}`, { method: "POST" });
    haptic(action === "follow" ? "medium" : "light");
    if (action === "follow") showToast("Вы подписались ✓", "success");
    await refreshAfterRelationChange();
  } catch (err) {
    showToast(friendlyError(err), "error");
  }
}

async function sendProfileReport() {
  const p = profileData;
  if (!p) return;
  const reason = document.querySelector('input[name="upReportReason"]:checked')?.value;
  if (!reason) { showToast("Выбери, на что жалоба", "error"); return; }
  const comment = document.getElementById("upReportComment")?.value || "";
  try {
    await api(`/api/users/${Number(p.telegram_id)}/report`, {
      method: "POST",
      body: JSON.stringify({ reason, comment }),
    });
    haptic("medium");
    showToast("Жалоба отправлена — спасибо", "success");
    profileReportOpen = false;
    renderUserProfile();
  } catch (err) {
    showToast(friendlyError(err), "error");
  }
}

async function searchPlayerByHandle(raw) {
  const handle = String(raw || "").trim().replace(/^@/, "");
  if (!handle) return;
  try {
    const data = await api(`/api/users/search?handle=${encodeURIComponent(handle)}`, { timeoutMs: 8000 });
    await openUserProfile(data.user.telegram_id);
  } catch (err) {
    showToast(err?.data?.error === "not_found" ? "Игрок с таким @ником не найден" : friendlyError(err), "error");
  }
}

// ---- «Подписки» / «Подписчики» ----
function followRowHtml(u) {
  const id = Number(u.telegram_id);
  const a = relationAction(u);
  const meta = [u.handle ? `@${escapeHtml(u.handle)}` : "", Number(u.streak) > 0 ? `🔥 ${Number(u.streak)}` : ""].filter(Boolean).join(" · ");
  const fresh = u.is_new ? `<span class="friend-row__meta follow-new-tag">✨ новый</span>` : "";
  return `
    <li class="friend-row" data-profile-id="${id}">
      <span class="friend-row__avatar ${avatarFrameClass(u)}">${avatarInner(u)}</span>
      <span class="friend-row__info">
        ${friendNameHtml(u, "Игрок")}
        ${meta ? `<span class="friend-row__meta">${meta}</span>` : ""}
        ${fresh}
      </span>
      <button type="button" class="friend-row__btn friend-row__btn--${a.cls}" data-follow-act="${a.act}" data-follow-id="${id}" data-follow-name="${escapeHtml(u.first_name || "игрока")}">${a.label}</button>
    </li>`;
}

function renderFollowList() {
  const list = document.getElementById("followList");
  if (!list || !followListData) return;
  const counts = followListData.counts || {};
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = String(Number(value || 0)); };
  setText("followTabFollowing", counts.following);
  setText("followTabFollowers", counts.followers);
  document.querySelectorAll("#followTabs [data-follow-kind]").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.followKind === followListKind);
  });
  const users = followListData.users || [];
  const reward = friendsData?.invite_reward;
  const rewardNote = reward && Number(reward.left) > 0 ? ` За каждого из первых двух приглашённых — ${Number(reward.coins)} Адам коинов.` : "";
  const empty = followListKind === "followers"
    ? `<li class="follow-empty">Пока никто не подписан на тебя. Позови друга — и подписывайся на активных игроков ниже: часть подпишется в ответ.${rewardNote}
         <br><button type="button" class="friend-row__btn friend-row__btn--primary" data-follow-invite>✈️ Позвать друга</button></li>`
    : `<li class="follow-empty">Ты ни на кого не подписан. Подпишись на активных игроков ниже — следи за их сериями и подтягивайся.</li>`;
  // Подборка активных игроков (GET /api/people/active) под списком подписок и под пустым списком подписчиков: так знакомятся с теми, кого
  // не знают. Тех, на кого уже подписались, прячем сразу — следующая перезагрузка списка их и так уберёт.
  const known = new Set(users.map((u) => Number(u.telegram_id)));
  const recs = (followListRecs || []).filter((r) => !known.has(Number(r.telegram_id))).slice(0, 5);
  const showRecs = recs.length && (followListKind === "following" || !users.length);
  list.innerHTML = (users.length ? users.map(followRowHtml).join("") : empty)
    + (showRecs
      ? `<li class="follow-recs__head"><b>Активные игроки ADAM</b><small>Подпишись — если подпишутся в ответ, вы друзья и сможете брать задания вместе</small></li>${recs.map(followRowHtml).join("")}`
      : "");
}

async function loadFollowList(kind, { quiet = false } = {}) {
  followListKind = kind;
  const list = document.getElementById("followList");
  if (list && !quiet) list.innerHTML = `<li class="empty-hint">Загружаю…</li>`;
  try {
    followListData = await api(`/api/follows?kind=${encodeURIComponent(kind)}`, { timeoutMs: 8000 });
    syncFollowCounts(followListData.counts, followListData.new_followers);
    renderFollowList();
    // Список подписчиков открыт — новые подписчики стали «просмотренными» (метка на счётчике в шапке гаснет).
    if (kind === "followers" && Number(followListData.new_followers) > 0) markFollowersSeen();
  } catch (err) {
    if (list && !quiet) list.innerHTML = "";
    showToast(friendlyError(err), "error");
  }
}

async function markFollowersSeen() {
  try {
    const res = await api("/api/follows/seen", { method: "POST" });
    if (state?.user && res?.follow_counts) {
      state.user.follow_counts = res.follow_counts;
      renderFollowCounters();
    }
  } catch (_) { /* метка погаснет при следующем открытии списка */ }
}

// Подборка активных игроков для списков подписок; грузится параллельно со списком и один раз за открытие окна.
let followListRecs = [];

async function loadFollowListRecs() {
  try {
    const data = await api("/api/people/active", { timeoutMs: 8000 });
    followListRecs = data.users || [];
  } catch (_) {
    followListRecs = [];
  }
  const sheet = document.getElementById("followListSheet");
  if (sheet && !sheet.hidden && followListData) renderFollowList();
}

async function openFollowList(kind) {
  const sheet = document.getElementById("followListSheet");
  if (!sheet) return;
  haptic("light");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
  followListRecs = [];
  loadFollowListRecs();
  await loadFollowList(kind === "followers" ? "followers" : "following");
}

function closeFollowListSheet() {
  const sheet = document.getElementById("followListSheet");
  if (!sheet || sheet.hidden) return;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { sheet.hidden = true; }, 230);
}

// ---- Подарок другу ----
// Варианты и баланс приходят с сервера (/api/gifts/options): что дарить можно,
// что отправителю по карману, чем получатель уже владеет. Деньги списываются
// только после подтверждения.
let giftTarget = null;
let giftOptions = null;

function renderGiftSheet() {
  const o = giftOptions;
  const balances = document.getElementById("giftBalances");
  const list = document.getElementById("giftList");
  const title = document.getElementById("giftTitle");
  if (!o || !list) return;
  if (title) title.textContent = `Подарок для ${o.to.first_name}`;
  if (balances) {
    balances.innerHTML = `
      <span>У тебя: ${ADAM_COIN_ICON} <b>${Number(o.balances.coins)}</b> · 💎 <b>${Number(o.balances.diamonds)}</b></span>
      <span>Подарков сегодня: <b>${Number(o.left_today)}</b> из ${Number(o.max_per_day)}</span>`;
  }
  const noLeft = Number(o.left_today) <= 0;
  list.innerHTML = `
    <div class="gift-section">
      <div class="gift-section__title">Алмазы</div>
      <div class="gift-diamonds">${o.diamonds.map((d) => `
        <button type="button" class="gift-row__btn" data-gift-kind="diamonds" data-gift-amount="${Number(d.amount)}"
          ${d.affordable && !noLeft ? "" : "disabled"}>💎 ${Number(d.amount)}</button>`).join("")}</div>
    </div>
    ${o.freeze ? `<div class="gift-section">
      <div class="gift-section__title">Заморозка серии ❄️ <small>${o.freeze.can_send ? "сохранит серию, если друг пропустит день" : (o.freeze.reason === "full" ? "у друга уже полный запас" : "сегодня другу уже дарили")}</small></div>
      <ul class="gift-items"><li class="gift-row">
        <span class="gift-row__name">Заморозка · у друга ${Number(o.freeze.receiver_balance)} из ${Number(o.freeze.max_balance)}</span>
        <button type="button" class="gift-row__btn" data-gift-kind="freeze" data-gift-price="${Number(o.freeze.price)}"
          ${o.freeze.can_send && o.freeze.affordable && !noLeft ? "" : "disabled"}>${ADAM_COIN_ICON} ${Number(o.freeze.price)}</button>
      </li></ul>
    </div>` : ""}
    <div class="gift-section">
      <div class="gift-section__title">Предметы за Adam Coin <small>платишь ты, очки получателю не идут</small></div>
      <ul class="gift-items">${o.items.map((it) => {
        const disabled = noLeft || it.owned_by_receiver || !it.affordable;
        const label = it.owned_by_receiver ? "Уже есть" : `${ADAM_COIN_ICON} ${Number(it.price)}`;
        return `<li class="gift-row">
          <span class="gift-row__name">${escapeHtml(it.name)}</span>
          <button type="button" class="gift-row__btn" data-gift-kind="item" data-gift-item="${Number(it.id)}"
            data-gift-label="${escapeHtml(it.name)}" data-gift-price="${Number(it.price)}" ${disabled ? "disabled" : ""}>${label}</button>
        </li>`;
      }).join("")}</ul>
    </div>
    ${noLeft ? `<div class="up-note">На сегодня подарки закончились — завтра снова можно.</div>` : ""}`;
}

async function loadGiftOptions() {
  giftOptions = await api(`/api/gifts/options?to=${Number(giftTarget)}`, { timeoutMs: 8000 });
  renderGiftSheet();
}

async function openGiftSheet(userId, name) {
  const sheet = document.getElementById("giftSheet");
  if (!sheet) return;
  giftTarget = Number(userId);
  try {
    await loadGiftOptions();
  } catch (err) {
    showToast(friendlyError(err), "error");
    return;
  }
  haptic("light");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
}

function closeGiftSheet() {
  const sheet = document.getElementById("giftSheet");
  if (!sheet || sheet.hidden) return;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { sheet.hidden = true; }, 230);
}

async function sendGiftFromSheet(btn) {
  const kind = btn.dataset.giftKind;
  const to = giftOptions?.to?.first_name || "другу";
  let payload;
  if (kind === "diamonds") {
    payload = { kind, amount: Number(btn.dataset.giftAmount) };
    if (!confirm(`Подарить ${to} 💎 ${payload.amount}? Они спишутся с твоего баланса.`)) return;
  } else if (kind === "freeze") {
    payload = { kind: "freeze" };
    if (!confirm(`Подарить ${to} заморозку серии за ${btn.dataset.giftPrice} Adam Coin? Монеты спишутся с тебя.`)) return;
  } else {
    payload = { kind: "item", item_id: Number(btn.dataset.giftItem) };
    if (!confirm(`Подарить ${to} «${btn.dataset.giftLabel}» за ${btn.dataset.giftPrice} Adam Coin? Монеты спишутся с тебя.`)) return;
  }
  btn.disabled = true;
  try {
    const result = await api(`/api/users/${Number(giftTarget)}/gift`, { method: "POST", body: JSON.stringify(payload) });
    haptic("medium");
    showToast(`🎁 Подарок для ${to} отправлен`, "praise", 3200);
    applyActionPatch(result);
  } catch (err) {
    showToast(friendlyError(err), "error");
  }
  try { await loadGiftOptions(); } catch (_) { /* окно просто останется как было */ }
}

// ---- «Мне подарили» ----
// Подарок приходит и push-ом в бота, но push можно пропустить, поэтому в
// приложении есть и своё окно: само открывается при входе, если есть новые
// (после праздничных экранов и «Что нового»), и всегда доступно из Профиля.
let receivedGiftsTimer = null;

function renderGiftsBadge() {
  const badge = document.getElementById("giftsUnseenBadge");
  if (!badge) return;
  const n = Number(state?.gifts_unseen || 0);
  badge.hidden = n <= 0;
  badge.textContent = n > 99 ? "99+" : String(n);
}

function formatGiftWhen(createdAt) {
  const d = new Date(String(createdAt).replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((dayStart(now) - dayStart(d)) / 86400000);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  if (diffDays === 0) return `сегодня, ${time}`;
  if (diffDays === 1) return `вчера, ${time}`;
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

function receivedGiftRowHtml(g) {
  const from = g.from || {};
  const id = Number(from.telegram_id);
  return `
    <li class="friend-row received-gift${g.seen ? "" : " is-new"}" ${id ? `data-profile-id="${id}"` : ""}>
      <span class="friend-row__avatar ${avatarFrameClass(from)}">${avatarInner(from)}</span>
      <span class="friend-row__info">
        <span class="friend-row__name">${escapeHtml(from.first_name || "Игрок")}${g.seen ? "" : ' <i class="received-gift__new">Новое</i>'}</span>
        <span class="friend-row__meta">подарил(а) · ${escapeHtml(formatGiftWhen(g.created_at))}</span>
      </span>
      <span class="received-gift__what">${escapeHtml(g.label)}</span>
    </li>`;
}

async function openReceivedGifts() {
  const sheet = document.getElementById("receivedGiftsSheet");
  const list = document.getElementById("receivedGiftsList");
  if (!sheet || !list) return;
  let data;
  try {
    data = await api("/api/gifts/received", { timeoutMs: 8000 });
  } catch (err) {
    showToast(friendlyError(err), "error");
    return;
  }
  const gifts = data.gifts || [];
  const title = document.getElementById("receivedGiftsTitle");
  if (title) title.textContent = data.unseen > 0 ? "Тебе подарили 🎉" : "Мои подарки";
  list.innerHTML = gifts.length
    ? gifts.map(receivedGiftRowHtml).join("")
    : `<li class="empty-hint">Пока никто ничего не дарил. Друзьям можно дарить самому — кнопка «🎁 Подарить» в их профиле.</li>`;
  haptic("light");
  sheet.hidden = false;
  sheet.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => sheet.classList.add("is-open"));
  if (data.unseen > 0) {
    // Показали — отмечаем; в списке «Новое» остаётся только на этот показ.
    // Подарок мог поменять алмазы, значок и купленные рамки — без этого
    // магазин и выбор рамки показывали бы подаренное как «Купить»/«Не куплена»
    // до перезапуска. Сначала отметка «просмотрено», чтобы bootstrap не вернул
    // счётчик обратно.
    api("/api/gifts/seen", { method: "POST" })
      .catch(() => {})
      .then(() => loadBootstrap())
      .then(invalidateProfileData)
      .catch(() => {});
    if (state) state.gifts_unseen = 0;
    renderGiftsBadge();
  }
}

function closeReceivedGifts() {
  const sheet = document.getElementById("receivedGiftsSheet");
  if (!sheet || sheet.hidden) return;
  sheet.classList.remove("is-open");
  sheet.setAttribute("aria-hidden", "true");
  setTimeout(() => { sheet.hidden = true; }, 230);
}

// После входа, если есть непросмотренные подарки: ждём, пока уйдут стартовые
// и праздничные экраны (и «Что нового»), плюс пару секунд тишины.
function scheduleReceivedGiftsPrompt() {
  if (!(Number(state?.gifts_unseen) > 0)) return;
  clearInterval(receivedGiftsTimer);
  const startedAt = Date.now();
  let quietTicks = 0;
  receivedGiftsTimer = setInterval(() => {
    if (Date.now() - startedAt > 3 * 60 * 1000) {
      clearInterval(receivedGiftsTimer);
      receivedGiftsTimer = null;
      return;
    }
    if (celebrationOverlayOpen() || document.hidden) { quietTicks = 0; return; }
    quietTicks += 1;
    if (quietTicks < 3) return;
    clearInterval(receivedGiftsTimer);
    receivedGiftsTimer = null;
    openReceivedGifts();
  }, 800);
}

function initProfileOverlays() {
  document.getElementById("openGiftsBtn")?.addEventListener("click", () => openReceivedGifts());
  document.getElementById("receivedGiftsClose")?.addEventListener("click", () => { haptic("light"); closeReceivedGifts(); });
  document.getElementById("receivedGiftsBackdrop")?.addEventListener("click", closeReceivedGifts);
  document.getElementById("receivedGiftsList")?.addEventListener("click", async (e) => {
    const row = e.target.closest("[data-profile-id]");
    if (!row) return;
    closeReceivedGifts();
    await openUserProfile(Number(row.dataset.profileId));
  });
  document.getElementById("giftClose")?.addEventListener("click", () => { haptic("light"); closeGiftSheet(); });
  document.getElementById("giftBackdrop")?.addEventListener("click", closeGiftSheet);
  document.getElementById("giftList")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-gift-kind]");
    if (btn && !btn.disabled) await sendGiftFromSheet(btn);
  });

  document.getElementById("userProfileClose")?.addEventListener("click", closeUserProfile);
  document.getElementById("userProfileBackdrop")?.addEventListener("click", closeUserProfile);

  document.getElementById("userProfileBody")?.addEventListener("click", async (e) => {
    const listBtn = e.target.closest("[data-up-list]");
    if (listBtn) { await openFollowList(listBtn.dataset.upList); return; }
    const btn = e.target.closest("[data-up]");
    if (!btn || !profileData) return;
    const action = btn.dataset.up;
    if (action === "report-toggle") {
      haptic("light");
      profileReportOpen = !profileReportOpen;
      renderUserProfile();
      if (profileReportOpen) document.querySelector(".up-report")?.scrollIntoView({ block: "center" });
    } else if (action === "report-send") {
      await sendProfileReport();
    } else if (action === "gift") {
      await openGiftSheet(profileData.telegram_id, profileData.first_name);
    } else if (action === "evo") {
      openEvolutionSheet({
        evo: profileData.evolution,
        showcase: profileData.showcase,
        name: profileData.first_name,
        self: !!profileData.relation?.self,
      });
    } else {
      await changeRelation(profileData.telegram_id, action, profileData.first_name);
    }
  });

  document.getElementById("followListClose")?.addEventListener("click", () => { haptic("light"); closeFollowListSheet(); });
  document.getElementById("followListBackdrop")?.addEventListener("click", closeFollowListSheet);
  document.getElementById("followTabs")?.addEventListener("click", async (e) => {
    const tab = e.target.closest("[data-follow-kind]");
    if (!tab) return;
    haptic("light");
    await loadFollowList(tab.dataset.followKind);
  });
  document.getElementById("playerFollow")?.addEventListener("click", (e) => {
    const openBtn = e.target.closest("[data-follow-open]");
    if (openBtn) openFollowList(openBtn.dataset.followOpen);
  });
  document.getElementById("followList")?.addEventListener("click", async (e) => {
    if (e.target.closest("[data-follow-invite]")) {
      closeFollowListSheet();
      await openFriendInvite();
      return;
    }
    const actBtn = e.target.closest("[data-follow-act]");
    if (actBtn) {
      await changeRelation(Number(actBtn.dataset.followId), actBtn.dataset.followAct, actBtn.dataset.followName);
      return;
    }
    const row = e.target.closest("[data-profile-id]");
    if (row) await openUserProfile(Number(row.dataset.profileId));
  });
}

function initStatsVisibilityToggle() {
  const toggle = document.getElementById("statsVisibilityToggle");
  if (!toggle) return;
  const value = () => (state?.settings?.stats_visibility === "friends" ? "friends" : "subscribers");
  const render = () => {
    toggle.textContent = value() === "friends" ? "Только друзьям" : "Подписчикам";
    toggle.setAttribute("aria-pressed", value() === "friends" ? "true" : "false");
  };
  render();
  toggle.addEventListener("click", async () => {
    const next = value() === "friends" ? "subscribers" : "friends";
    try {
      const res = await api("/api/settings/stats-visibility", {
        method: "POST",
        body: JSON.stringify({ visibility: next }),
      });
      if (state.settings) state.settings.stats_visibility = res.visibility;
      haptic("light");
      render();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

function initShareHabitsToggle() {
  const toggle = document.getElementById("shareHabitsToggle");
  if (!toggle) return;
  const isOn = () => !!state?.settings?.share_habits;
  const render = () => {
    toggle.setAttribute("aria-pressed", isOn() ? "true" : "false");
    toggle.textContent = isOn() ? "Вкл" : "Выкл";
  };
  render();
  toggle.addEventListener("click", async () => {
    try {
      const res = await api("/api/settings/share-habits", {
        method: "POST",
        body: JSON.stringify({ enabled: !isOn() }),
      });
      if (state.settings) state.settings.share_habits = res.enabled;
      haptic("light");
      render();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// «Вибрация» — настройка устройства, а не аккаунта: хранится в localStorage
// (см. haptics.js), на сервер не уходит. Если модуль не загрузился, строка
// просто прячется — переключать нечего.
function initHapticsToggle() {
  const toggle = document.getElementById("hapticsToggle");
  if (!toggle) return;
  const engine = window.AdamHaptics;
  if (!engine) {
    const row = toggle.closest(".settings-row");
    if (row) row.hidden = true;
    return;
  }
  const render = () => {
    toggle.setAttribute("aria-pressed", engine.isEnabled() ? "true" : "false");
    toggle.textContent = engine.isEnabled() ? "Вкл" : "Выкл";
  };
  render();
  toggle.addEventListener("click", () => {
    engine.setEnabled(!engine.isEnabled());
    render();
    // При включении сразу даём «пробный» отклик; при выключении — тишина.
    haptic("confirm");
  });
}

function initFriendNudgesToggle() {
  const toggle = document.getElementById("friendNudgesToggle");
  if (!toggle) return;
  const isOn = () => state?.settings?.friend_nudges !== false;
  const render = () => {
    toggle.setAttribute("aria-pressed", isOn() ? "true" : "false");
    toggle.textContent = isOn() ? "Вкл" : "Выкл";
  };
  render();
  toggle.addEventListener("click", async () => {
    try {
      const res = await api("/api/settings/friend-nudges", {
        method: "POST",
        body: JSON.stringify({ enabled: !isOn() }),
      });
      if (state.settings) state.settings.friend_nudges = res.enabled;
      haptic("light");
      render();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

function renderSeasonList() {
  const list = document.getElementById("seasonRatingList");
  if (!list || !state.season) return;
  const rows = state.season.leaderboard || [];
  if (rows.length === 0) {
    list.innerHTML = `<li class="empty-hint">В этом сезоне пока пусто</li>`;
    return;
  }
  const myId = state.user.telegram_id;
  list.innerHTML = rows.map((r, i) => `
    <li class="rating-item ${r.telegram_id === myId ? "is-me" : ""}" data-profile-id="${Number(r.telegram_id)}">
      <span class="rating-item__rank">${i + 1}</span>
      <span class="rating-avatar">${escapeHtml((r.first_name || "A")[0].toUpperCase())}</span>
      <span class="rating-item__name"><span class="rating-item__name-line"><span class="rating-item__name-text">${escapeHtml(r.first_name || r.username || "Игрок")}</span>${r.handle ? `<span class="rating-item__handle">@${escapeHtml(r.handle)}</span>` : ""}</span></span>
      <span class="rating-item__meta"><span class="rating-stat">${ADAM_COIN_ICON}${r.season_xp}</span></span>
    </li>`).join("");
}

function initRatingScopeSwitch() {
  const switcher = document.getElementById("ratingScopeSwitch");
  if (!switcher) return;
  switcher.addEventListener("click", (e) => {
    const btn = e.target.closest(".rating-scope-btn");
    if (!btn) return;
    haptic("light");
    switcher.querySelectorAll(".rating-scope-btn").forEach(b => b.classList.toggle("is-active", b === btn));
    const isSeason = btn.dataset.scope === "season";
    document.getElementById("ratingPodium").hidden = isSeason;
    document.getElementById("ratingList").hidden = isSeason;
    document.querySelector(".rating-rest-head").hidden = isSeason;
    document.getElementById("seasonRatingList").hidden = !isSeason;
  });
}

// Roadmap #18 — лента активности друзей, в Профиле.
async function loadActivityFeed() {
  const box = document.getElementById("activityFeed");
  if (!box) return;
  try {
    const data = await api("/api/activity-feed");
    const events = data.events || [];
    box.innerHTML = events.length === 0
      ? `<div class="empty-hint">Пока тихо — добавь друга в команду или обменяйтесь поддержкой 💌</div>`
      : events.map(e => `
          <div class="activity-feed__row">
            <span class="activity-feed__name">${escapeHtml(e.first_name || "Игрок")}</span>
            ${e.handle ? `<span class="activity-feed__handle">@${escapeHtml(e.handle)}</span>` : ""}
            <span class="activity-feed__label">${e.label}${e.detail ? " «" + escapeHtml(e.detail) + "»" : ""}</span>
          </div>`).join("");
  } catch (err) {
    box.innerHTML = "";
  }
}

// Roadmap #19 — реакции/стикеры поддержки другу прямо из рейтинга.
function initRatingActions() {
  const list = document.getElementById("ratingList");
  const podium = document.getElementById("ratingPodium");
  if (!list) return;

  // Сезонный лидерборд — тоже тап по игроку открывает профиль.
  document.getElementById("seasonRatingList")?.addEventListener("click", (e) => {
    const row = e.target.closest("[data-profile-id]");
    if (row) openUserProfile(Number(row.dataset.profileId));
  });

  async function sendReaction(targetId, emoji) {
    try {
      await api(`/api/friends/${targetId}/react`, {
        method: "POST",
        body: JSON.stringify({ emoji }),
      });
      haptic("light");
      showToast("Поддержка отправлена " + emoji, "success");
      // loadBootstrapSecondary кэширует по ключу — без сброса кэша
      // повторный вызов был no-op, и 💌 не превращался в ✓ до
      // перезахода на вкладку (тот же приём, что и для Профиля).
      secondaryLoaded.delete("rating");
      await loadBootstrapSecondary("rating");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  }

  list.addEventListener("click", async (e) => {
    const emojiChip = e.target.closest(".rating-react-chip");
    if (emojiChip) {
      const targetId = reactPickerForId;
      reactPickerForId = null;
      renderRating();
      await sendReaction(targetId, emojiChip.dataset.emoji);
      return;
    }
    const reactBtn = e.target.closest("[data-react-target]");
    if (reactBtn) {
      haptic("light");
      const targetId = Number(reactBtn.dataset.reactTarget);
      reactPickerForId = reactPickerForId === targetId ? null : targetId;
      renderRating();
      return;
    }
    // Тап по самой строке игрока (мимо кнопок реакции) — его профиль, откуда
    // можно подписаться.
    if (e.target.closest("button, .rating-react-picker")) return;
    const row = e.target.closest("[data-profile-id]");
    if (row) openUserProfile(Number(row.dataset.profileId));
  });

  // Топ-3 живут в отдельном узком гриде подиума — полноразмерный пикер из
  // 6 эмодзи туда просто не влезает по ширине, поэтому один тап сразу шлёт
  // дефолтную поддержку 🔥 вместо открытия выбора (было: топ-3 вообще
  // нельзя было поддержать с экрана рейтинга — у них не было этой кнопки).
  podium?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-podium-react-target]");
    if (!btn) {
      const card = e.target.closest("[data-profile-id]");
      if (card) openUserProfile(Number(card.dataset.profileId));
      return;
    }
    const targetId = Number(btn.dataset.podiumReactTarget);
    btn.disabled = true;
    await sendReaction(targetId, "🔥");
  });
}

function initProgressActions() {
  const btn = document.getElementById("progressAiBtn");
  const resultBox = document.getElementById("progressAiResult");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    haptic("light");
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "🤖 Анализирую...";
    try {
      const data = await api("/api/progress/ai-analysis", { method: "POST", timeoutMs: 30000 });
      resultBox.hidden = false;
      resultBox.textContent = data.text || "";
      resultBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      showToast(friendlyError(err) || "Не получилось сформировать анализ", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });
}

// ===================== НАСТРОЙКИ (стиль AI, сброс прогресса) =====================
function initSettingsActions() {
  const picker = document.getElementById("aiStylePicker");
  const resetBtn = document.getElementById("resetProgressBtn");

  function markActiveStyle() {
    const current = (state.settings && state.settings.ai_style) || "neutral";
    picker?.querySelectorAll(".ai-style-btn").forEach(b => {
      b.classList.toggle("is-active", b.dataset.style === current);
    });
  }
  markActiveStyle();

  picker?.addEventListener("click", async (e) => {
    const btn = e.target.closest(".ai-style-btn");
    if (!btn) return;
    try {
      await api("/api/settings/ai-style", {
        method: "POST",
        body: JSON.stringify({ style: btn.dataset.style })
      });
      if (state.settings) state.settings.ai_style = btn.dataset.style;
      markActiveStyle();
      haptic("light");
      showToast("Стиль сохранён", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  resetBtn?.addEventListener("click", async () => {
    if (!window.confirm("Точно сбросить весь прогресс? Это действие необратимо.")) return;
    try {
      await api("/api/settings/reset-progress", { method: "POST" });
      haptic("medium");
      showToast("Прогресс сброшен", "success");
      await loadBootstrap();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  // Необратимо (см. db/account.py::request_account_deletion) — двойное
  // подтверждение вместо одного, в отличие от "Сбросить" выше: там теряется
  // только прогресс, здесь весь аккаунт (профиль, привычки, переписка).
  // window.prompt() здесь не используем — не гарантированно поддерживается
  // Telegram WebView, в отличие от window.confirm (уже используется выше).
  document.getElementById("deleteAccountBtn")?.addEventListener("click", async () => {
    if (!window.confirm("Удалить аккаунт БЕЗВОЗВРАТНО? Пропадут профиль, все привычки, серия, переписка с ADAM и покупки. Отменить это будет нельзя.")) return;
    if (!window.confirm("Точно-точно? Это последнее предупреждение — восстановить аккаунт после этого будет невозможно.")) return;
    try {
      await api("/api/account/delete", { method: "POST" });
      haptic("warning");
      showToast("Аккаунт удалён", "success");
      if (tg && typeof tg.close === "function") {
        setTimeout(() => tg.close(), 1200);
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  initQuietHoursActions();
  initHapticsToggle();
  initFriendNudgesToggle();
  initStatsVisibilityToggle();
  initShareHabitsToggle();
  initReminderSettingsActions();
  initHabitCheckpointStylePicker();
  initHomeLayoutActions();
}

// Главный экран: порядок разделов "План дня"/"Привычки" и видимость
// "Плана дня". Многие пользуются сторонними планировщиками задач, а для
// нас главное — привычки, поэтому План дня можно скрыть с главной и
// вернуть обратно тем же тумблером здесь, в Настройках.
function applyHomeLayout() {
  const planCard = document.querySelector('section[data-tab="home"] .plan-card');
  const habitsPanel = document.querySelector('section[data-tab="home"] .habits-panel');
  if (!planCard || !habitsPanel) return;
  const layout = (state.settings && state.settings.home_layout) || {};
  planCard.hidden = !!layout.plan_hidden;
  const parent = habitsPanel.parentNode;
  if (layout.habits_first) {
    parent.insertBefore(habitsPanel, planCard);
  } else {
    parent.insertBefore(planCard, habitsPanel);
  }
}

function initHomeLayoutActions() {
  const orderToggle = document.getElementById("homeHabitsFirstToggle");
  const visibilityToggle = document.getElementById("homePlanVisibleToggle");
  if (!orderToggle || !visibilityToggle) return;

  function render() {
    const layout = (state.settings && state.settings.home_layout) || {};
    orderToggle.setAttribute("aria-pressed", layout.habits_first ? "true" : "false");
    orderToggle.textContent = layout.habits_first ? "Вкл" : "Выкл";
    const planVisible = !layout.plan_hidden;
    visibilityToggle.setAttribute("aria-pressed", planVisible ? "true" : "false");
    visibilityToggle.textContent = planVisible ? "Вкл" : "Выкл";
  }
  render();

  async function updateLayout(body) {
    try {
      const res = await api("/api/settings/home-layout", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (state.settings) state.settings.home_layout = res.home_layout;
      render();
      applyHomeLayout();
      haptic("light");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  }

  orderToggle.addEventListener("click", () => {
    const layout = (state.settings && state.settings.home_layout) || {};
    updateLayout({ habits_first: !layout.habits_first });
  });

  visibilityToggle.addEventListener("click", () => {
    const layout = (state.settings && state.settings.home_layout) || {};
    updateLayout({ plan_hidden: !layout.plan_hidden });
  });
}

// Роадмап: стиль контрольной точки по привычкам. 'full' (по умолчанию) —
// как раньше: "X из Y выполнено..." + доп. строка про привычки со своим
// ещё не наступившим временем ("Не забудь в HH:MM — ..."). 'simple' — для
// тех, у кого почти все привычки уже на таймере и своя система в голове
// выстроена: без сверки прогресса и без упоминания таймерных привычек
// вообще (по ним и так придёт отдельное напоминание ровно в их время).
function initHabitCheckpointStylePicker() {
  const picker = document.getElementById("habitCheckpointStylePicker");
  if (!picker) return;

  function markActive() {
    const current = (state.settings && state.settings.habit_checkpoint_style) || "full";
    picker.querySelectorAll(".ai-style-btn").forEach(b => {
      b.classList.toggle("is-active", b.dataset.checkpointStyle === current);
    });
  }
  markActive();

  picker.addEventListener("click", async (e) => {
    const btn = e.target.closest(".ai-style-btn");
    if (!btn) return;
    const style = btn.dataset.checkpointStyle;
    try {
      await api("/api/settings/habit-checkpoint-style", {
        method: "POST",
        body: JSON.stringify({ style }),
      });
      if (state.settings) state.settings.habit_checkpoint_style = style;
      markActive();
      haptic("light");
      showToast("Стиль сохранён", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// ===================== «ЧТО НОВОГО» =====================
// Раньше об обновлениях узнавали только по факту (или никак). Вызывается
// один раз за сессию, после первого успешного bootstrap (см. boot()) —
// не на каждый последующий reload данных.
async function initChangelogCheck() {
  try {
    const { entries } = await api("/api/changelog/unseen");
    if (!entries || !entries.length) return;

    const list = document.getElementById("changelogList");
    if (list) {
      list.innerHTML = entries.map(e => `
        <div class="changelog-item">
          <div class="changelog-item__title">${escapeHtml(e.title)}</div>
          <div class="changelog-item__body">${escapeHtml(e.body)}</div>
        </div>
      `).join("");
    }

    const sheet = document.getElementById("changelogSheet");
    if (!sheet) return;
    sheet.hidden = false;
    requestAnimationFrame(() => sheet.classList.add("is-open"));
    sheet.setAttribute("aria-hidden", "false");

    const close = async () => {
      haptic("light");
      sheet.classList.remove("is-open");
      sheet.setAttribute("aria-hidden", "true");
      setTimeout(() => { sheet.hidden = true; }, 230);
      try { await api("/api/changelog/seen", { method: "POST" }); } catch (e) {}
    };
    document.getElementById("changelogClose")?.addEventListener("click", close, { once: true });
    document.getElementById("changelogBackdrop")?.addEventListener("click", close, { once: true });
  } catch (err) {
    // Не критично — просто не показываем "что нового" в этот раз.
  }
}

// Roadmap #35 — "тихие часы": окно локальных часов, в которое не приходят
// повседневные напоминания. UI сам по себе простой (тумблер + два
// select'а), вся логика подавления — на бэкенде (db/settings.py::in_quiet_hours).
function initQuietHoursActions() {
  const toggle = document.getElementById("quietHoursToggle");
  const row = document.getElementById("quietHoursRow");
  const startSelect = document.getElementById("quietHoursStart");
  const endSelect = document.getElementById("quietHoursEnd");
  if (!toggle || !row || !startSelect || !endSelect) return;

  if (!startSelect.options.length) {
    for (let h = 0; h < 24; h++) {
      const label = `${String(h).padStart(2, "0")}:00`;
      startSelect.add(new Option(label, h));
      endSelect.add(new Option(label, h));
    }
  }

  const qh = state.settings && state.settings.quiet_hours;
  const enabled = !!qh;
  toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
  toggle.textContent = enabled ? "Вкл" : "Выкл";
  row.hidden = !enabled;
  startSelect.value = qh ? qh.start : 23;
  endSelect.value = qh ? qh.end : 7;

  async function save() {
    try {
      await api("/api/settings/quiet-hours", {
        method: "POST",
        body: JSON.stringify({ start: Number(startSelect.value), end: Number(endSelect.value) }),
      });
      if (state.settings) state.settings.quiet_hours = { start: Number(startSelect.value), end: Number(endSelect.value) };
      haptic("light");
      showToast("Тихие часы сохранены", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  }

  toggle.addEventListener("click", async () => {
    const nowEnabled = toggle.getAttribute("aria-pressed") === "true";
    if (nowEnabled) {
      try {
        await api("/api/settings/quiet-hours", { method: "POST", body: JSON.stringify({}) });
        if (state.settings) state.settings.quiet_hours = null;
        toggle.setAttribute("aria-pressed", "false");
        toggle.textContent = "Выкл";
        row.hidden = true;
        haptic("light");
        showToast("Тихие часы выключены", "success");
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
    } else {
      toggle.setAttribute("aria-pressed", "true");
      toggle.textContent = "Вкл";
      row.hidden = false;
      await save();
    }
  });

  startSelect.addEventListener("change", save);
  endSelect.addEventListener("change", save);
}

// Роадмап: "Умные напоминания" — раньше единственный раздел настроек,
// оставшийся только в панели бота (см. keyboards.py::main_menu,
// handlers/settings.py). Тот же общий тумблер + 3 гранулярные категории,
// теперь и в Mini App — бэкенд уже был готов (db/settings.py::toggle_reminders/
// toggle_reminder_category), нужен был только UI.
function initReminderSettingsActions() {
  const toggle = document.getElementById("remindersToggle");
  const categoriesRow = document.getElementById("remindersCategoriesRow");
  if (!toggle || !categoriesRow) return;

  function render() {
    const s = state.settings || {};
    const enabled = !!s.reminders;
    toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
    toggle.textContent = enabled ? "Вкл" : "Выкл";
    categoriesRow.hidden = !enabled;
    categoriesRow.querySelectorAll("[data-category]").forEach(btn => {
      const on = s[`reminders_${btn.dataset.category}`] !== false;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "Вкл" : "Выкл";
    });
  }
  render();

  toggle.addEventListener("click", async () => {
    try {
      const res = await api("/api/settings/reminders/toggle", { method: "POST" });
      if (state.settings) state.settings.reminders = res.reminders;
      haptic("light");
      render();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  categoriesRow.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-category]");
    if (!btn) return;
    const category = btn.dataset.category;
    try {
      const res = await api("/api/settings/reminders/category", {
        method: "POST",
        body: JSON.stringify({ category }),
      });
      if (state.settings) state.settings[`reminders_${category}`] = res.enabled;
      haptic("light");
      render();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// Roadmap #39 — короткий тест на архетип личности. Подсчёт целиком на
// клиенте (4 вопроса, каждый вариант тянет к одному из 4 архетипов),
// на сервер уходит только готовый ключ-результат. ARCHETYPE_QUIZ_QUESTIONS
// теперь объявлен выше (см. комментарий там) — он же используется для
// версии теста, встроенной в стартовый онбординг-квиз.
function initArchetypeQuizActions() {
  const openBtn = document.getElementById("archetypeQuizBtn");
  const overlay = document.getElementById("archetypeQuizOverlay");
  const closeBtn = document.getElementById("archetypeQuizClose");
  const body = document.getElementById("archetypeQuizBody");
  if (!openBtn || !overlay || !body) return;

  let answers = [];
  let step = 0;

  function renderStep() {
    if (step >= ARCHETYPE_QUIZ_QUESTIONS.length) {
      const tally = {};
      answers.forEach(a => { tally[a] = (tally[a] || 0) + 1; });
      const winner = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0];
      body.innerHTML = `<div class="archetype-quiz-loading">Считаю результат…</div>`;
      api("/api/settings/archetype", { method: "POST", body: JSON.stringify({ archetype: winner }) })
        .then((res) => {
          if (state.user) state.user.archetype = res.archetype;
          if (openBtn) openBtn.textContent = res.archetype;
          body.innerHTML = `
            <div class="archetype-quiz-result">
              <div class="archetype-quiz-result__label">Твой архетип</div>
              <div class="archetype-quiz-result__value">${escapeHtml(res.archetype)}</div>
              <button type="button" class="archetype-quiz-done">Готово</button>
            </div>`;
          body.querySelector(".archetype-quiz-done")?.addEventListener("click", () => {
            overlay.hidden = true;
          });
          haptic("medium");
        })
        .catch(() => { body.innerHTML = `<div class="archetype-quiz-loading">Не получилось сохранить результат</div>`; });
      return;
    }
    const question = ARCHETYPE_QUIZ_QUESTIONS[step];
    body.innerHTML = `
      <div class="archetype-quiz-progress">${step + 1}/${ARCHETYPE_QUIZ_QUESTIONS.length}</div>
      <div class="archetype-quiz-question">${escapeHtml(question.q)}</div>
      <div class="archetype-quiz-options">
        ${question.options.map((o, i) => `<button type="button" class="archetype-quiz-option" data-idx="${i}">${escapeHtml(o[0])}</button>`).join("")}
      </div>`;
    body.querySelectorAll(".archetype-quiz-option").forEach(btn => {
      btn.addEventListener("click", () => {
        answers.push(question.options[Number(btn.dataset.idx)][1]);
        step++;
        haptic("light");
        renderStep();
      });
    });
  }

  openBtn.addEventListener("click", () => {
    answers = [];
    step = 0;
    overlay.hidden = false;
    renderStep();
  });
  closeBtn?.addEventListener("click", () => { overlay.hidden = true; });
}

// Roadmap #46 — переключатель языка интерфейса.
function initLanguageActions() {
  const picker = document.getElementById("languagePicker");
  if (!picker) return;
  picker.addEventListener("click", async (e) => {
    const btn = e.target.closest(".color-mode-btn");
    if (!btn) return;
    const lang = btn.dataset.lang;
    if (state.settings.language === lang) return;
    try {
      await api("/api/settings/language", { method: "POST", body: JSON.stringify({ language: lang }) });
      state.settings.language = lang;
      applyLanguage();
      haptic("light");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// Фидбек #4 — пол, чтобы Адам согласовывал "Ты" в напоминаниях правильно.
function initGenderActions() {
  const picker = document.getElementById("genderPicker");
  if (!picker) return;
  picker.addEventListener("click", async (e) => {
    const btn = e.target.closest(".color-mode-btn");
    if (!btn) return;
    const gender = btn.dataset.gender;
    if (state.settings.gender === gender) return;
    try {
      await api("/api/settings/gender", { method: "POST", body: JSON.stringify({ gender }) });
      state.settings.gender = gender;
      applyGender();
      haptic("light");
      showToast("Сохранено", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });
}

// Уникальный @ник (в духе Duolingo) — генерируется автоматически при
// регистрации (см. db/handles.py), но пользователь может сменить его сам.
function initUserHandleActions() {
  const input = document.getElementById("userHandleInput");
  const saveBtn = document.getElementById("userHandleSaveBtn");
  if (!input || !saveBtn) return;
  input.value = (state.user && state.user.handle) || "";

  saveBtn.addEventListener("click", async () => {
    const value = input.value.trim().replace(/^@/, "");
    saveBtn.disabled = true;
    try {
      const res = await api("/api/settings/handle", {
        method: "POST",
        body: JSON.stringify({ handle: value }),
      });
      if (state.user) state.user.handle = res.handle;
      input.value = res.handle;
      haptic("light");
      showToast("Ник сохранён", "success");
      renderPlayerCard();
    } catch (err) {
      showToast(friendlyError(err), "error");
    } finally {
      saveBtn.disabled = false;
    }
  });
}

// Roadmap #25 — долгосрочные цели пользователя для AI-наставника.
function initGoalsActions() {
  const input = document.getElementById("longTermGoalsInput");
  const saveBtn = document.getElementById("longTermGoalsSaveBtn");
  if (!input || !saveBtn) return;
  input.value = (state.settings && state.settings.long_term_goals) || "";
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      await api("/api/settings/goals", { method: "POST", body: JSON.stringify({ text: input.value }) });
      if (state.settings) state.settings.long_term_goals = input.value.trim();
      haptic("light");
      showToast("Цель сохранена", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    } finally {
      saveBtn.disabled = false;
    }
  });
}

// Roadmap #17 — публичный шаринг-профиль: тумблер + кнопка "скопировать
// ссылку" в настройках, плюс лента полученных реакций (roadmap #19) там же.
function initPublicProfileActions() {
  const toggle = document.getElementById("publicProfileToggle");
  const row = document.getElementById("publicProfileRow");
  const shareBtn = document.getElementById("publicProfileShareBtn");
  if (!toggle || !row) return;

  const enabled = !!(state.settings && state.settings.public_profile_enabled);
  toggle.setAttribute("aria-pressed", enabled ? "true" : "false");
  toggle.textContent = enabled ? "Вкл" : "Выкл";
  row.hidden = !enabled;

  toggle.addEventListener("click", async () => {
    const nowEnabled = toggle.getAttribute("aria-pressed") === "true";
    const next = !nowEnabled;
    try {
      await api("/api/settings/public-profile", { method: "POST", body: JSON.stringify({ enabled: next }) });
      if (state.settings) state.settings.public_profile_enabled = next;
      toggle.setAttribute("aria-pressed", next ? "true" : "false");
      toggle.textContent = next ? "Вкл" : "Выкл";
      row.hidden = !next;
      haptic("light");
      showToast(next ? "Публичный профиль включён" : "Публичный профиль выключен", "success");
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  if (shareBtn) {
    shareBtn.addEventListener("click", async () => {
      const url = `${window.location.origin}/u/${state.user.telegram_id}`;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(url);
        } else {
          throw new Error("no_clipboard");
        }
        haptic("light");
        showToast("Ссылка скопирована", "success");
      } catch (_) {
        showToast(url, "success", 6000);
      }
    });
  }

  renderReactionsFeed();
}

async function renderReactionsFeed() {
  const box = document.getElementById("reactionsFeed");
  if (!box) return;
  try {
    const data = await api("/api/reactions");
    const reactions = data.reactions || [];
    box.innerHTML = reactions.length === 0
      ? `<div class="empty-hint">Пока никто не отправлял поддержку — но всё впереди 💌</div>`
      : reactions.map(r =>
          `<div class="reaction-row"><span class="reaction-row__emoji">${r.emoji}</span><span class="reaction-row__name">${escapeHtml(r.from_name)}</span></div>`
        ).join("");
  } catch (_) {
    box.innerHTML = "";
  }
}

// ===================== ДАННЫЕ И ПОДДЕРЖКА =====================
// Экспорт CSV, шаринг недельного итога, форма бага/фидбека прямо из
// Mini App — раньше единственным каналом было написать разработчику
// лично, что резко снижает вероятность честного отчёта о проблеме.
function initDataSupportActions() {
  // Просьба пользователя: для тех, кто когда-то нажал "Пропустить" в
  // онбординге, а теперь сам захотел пересмотреть подсказки — не только
  // текстовая модалка "Это ADAM", но и весь интерактивный сценарий заново
  // (см. db/product_experience.py::restart_onboarding). Переключаем на
  // Главную ДО показа — там живёт цель первого шага (#addHabitTrigger),
  // а к моменту, когда человек долистает вводные слайды, вкладка уже
  // будет той, что нужно.
  document.getElementById("replayOnboardingBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await api('/api/onboarding/restart', { method: 'POST' });
      hideProductHint();
      if (state) {
        state.show_app_tour = true;
        state.product_onboarding = { onboarding_stage: 0, onboarding_started_at: null };
      }
      appTourShownThisSession = false;
      onboardingReplayPending = true;
      document.querySelector('.tab-bar__item[data-tab="home"]')?.click();
      haptic("light");
      maybeShowAppTour();
    } catch (err) {
      showToast(friendlyError(err), "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("shareWeeklyBtn")?.addEventListener("click", () => {
    const completed = Number(document.getElementById("progressStatCompleted")?.textContent || 0);
    const activeDays = document.getElementById("progressStatActiveDays")?.textContent || "0/7";
    openAchievementShare({
      title: "Итог недели",
      big: `${completed} ${pluralRu(completed, "привычка", "привычки", "привычек")}`,
      status: `Активных дней: ${activeDays}`,
    });
  });

  document.getElementById("exportDataBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      // Не через api() — тот всегда парсит JSON, а тут нужен сырой CSV.
      const res = await fetch("/api/export/habits.csv", {
        headers: { "Authorization": "tma " + initData() },
      });
      if (!res.ok) throw new Error("export_failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "adam_habits.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      haptic("light");
      showToast("Файл готов", "success");
    } catch (err) {
      showToast("Не получилось скачать данные", "error");
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("exportFullDataBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const res = await fetch("/api/account/export", {
        headers: { "Authorization": "tma " + initData() },
      });
      if (!res.ok) throw new Error("export_failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "adam_data.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      haptic("light");
      showToast("Файл готов", "success");
    } catch (err) {
      showToast("Не получилось скачать данные", "error");
    } finally {
      btn.disabled = false;
    }
  });

  // Уведомления "в 100 раз лучше": не чёрный ящик — история реально
  // отправленных плановых сообщений, а не только "включено/выключено".
  document.getElementById("notificationHistoryBtn")?.addEventListener("click", async (e) => {
    haptic("light");
    const box = document.getElementById("notificationHistoryBox");
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `<div class="empty-hint">Загружаю…</div>`;
    try {
      const data = await api("/api/notifications/history");
      const items = data.history || [];
      box.innerHTML = items.length === 0
        ? `<div class="empty-hint">Пока ничего не отправляли</div>`
        : items.map(h => {
            const dt = new Date(h.sent_at.replace(" ", "T") + "Z");
            const when = isNaN(dt) ? h.sent_at : dt.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
            return `<div class="notification-history__row"><span>${h.label}</span><small>${when}</small></div>`;
          }).join("");
    } catch (err) {
      box.innerHTML = `<div class="empty-hint">Не получилось загрузить</div>`;
    }
  });

  // Roadmap #8 — импорт привычек из CSV: один файл, одна привычка на
  // строку ("Название" или "Название,категория"), без каких-либо
  // изменений на сервере — просто цикл по уже существующему POST
  // /api/habits (тот же приём, что и у готовых "программ", roadmap #38).
  document.getElementById("importCsvInput")?.addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = ""; // разрешаем повторно выбрать тот же файл
    if (!file) return;
    const text = await file.text();
    const rows = text.split(/\r?\n/).map(r => r.trim()).filter(Boolean);
    if (rows.length === 0) {
      showToast("Файл пустой", "error");
      return;
    }
    let added = 0;
    let failed = 0;
    for (const row of rows) {
      const cols = row.split(",").map(c => c.trim().replace(/^"|"$/g, ""));
      const title = cols[0];
      const category = cols[1] && HABIT_CATEGORY_META[cols[1]] ? cols[1] : undefined;
      if (!title || title.length < 2) { failed++; continue; }
      try {
        await api("/api/habits", { method: "POST", body: JSON.stringify({ title, category }) });
        added++;
      } catch (err) {
        failed++;
        if (err && err.data && err.data.error === "habit_limit") break; // дальше всё равно упрётся в лимит
      }
    }
    haptic("light");
    showToast(
      added
        ? `Импортировано привычек: ${added}${failed ? `, пропущено: ${failed}` : ""}`
        : "Не получилось импортировать ни одной строки",
      added ? "success" : "error",
    );
    await loadBootstrap();
  });

  // Roadmap #28 — экспортируемый PDF-отчёт о прогрессе (график + сводка).
  document.getElementById("pdfReportBtn")?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "📄 Готовлю отчёт...";
    try {
      const res = await fetch("/api/progress/pdf-report", {
        headers: { "Authorization": "tma " + initData() },
      });
      if (!res.ok) throw new Error("pdf_export_failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "adam_report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      haptic("light");
      showToast("PDF готов", "success");
    } catch (err) {
      showToast("Не получилось сформировать отчёт", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

  const feedbackSheet = document.getElementById("feedbackSheet");
  const feedbackText = document.getElementById("feedbackText");
  const closeFeedback = () => {
    if (!feedbackSheet) return;
    feedbackSheet.classList.remove("is-open");
    feedbackSheet.setAttribute("aria-hidden", "true");
    setTimeout(() => { feedbackSheet.hidden = true; }, 230);
  };
  document.getElementById("openFeedbackBtn")?.addEventListener("click", () => {
    if (!feedbackSheet) return;
    feedbackSheet.hidden = false;
    requestAnimationFrame(() => feedbackSheet.classList.add("is-open"));
    feedbackSheet.setAttribute("aria-hidden", "false");
    haptic("light");
    setTimeout(() => feedbackText?.focus(), 250);
  });
  // Юридические документы (privacy policy требует Telegram у ботов с
  // платежами) — открываем системным браузером через tg.openLink, а не
  // обычным <a href>: внутри Telegram WebView обычная навигация со
  // страницы Mini App ненадёжна.
  const _openLegalDoc = (path) => {
    haptic("light");
    const url = location.origin + path;
    if (tg && typeof tg.openLink === "function") {
      tg.openLink(url);
    } else {
      window.open(url, "_blank", "noopener");
    }
  };
  document.getElementById("openPrivacyBtn")?.addEventListener("click", () => _openLegalDoc("/privacy"));
  document.getElementById("openTermsBtn")?.addEventListener("click", () => _openLegalDoc("/terms"));
  document.getElementById("feedbackCancel")?.addEventListener("click", closeFeedback);
  document.getElementById("feedbackBackdrop")?.addEventListener("click", closeFeedback);
  document.getElementById("feedbackSend")?.addEventListener("click", async () => {
    const text = (feedbackText?.value || "").trim();
    if (text.length < 5) {
      showToast("Опиши проблему чуть подробнее", "error");
      return;
    }
    const sendBtn = document.getElementById("feedbackSend");
    if (sendBtn) sendBtn.disabled = true;
    try {
      const activeTab = document.querySelector(".tab-bar__item.is-active")?.dataset.tab || "неизвестно";
      await api("/api/feedback", {
        method: "POST",
        body: JSON.stringify({ text, tab: activeTab }),
      });
      haptic("medium");
      showToast("Спасибо! Уже читаем", "success");
      if (feedbackText) feedbackText.value = "";
      closeFeedback();
    } catch (err) {
      showToast(friendlyError(err), "error");
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
  });
}

// Улучшение #60: кнопка "Повторить" в bootRetryBanner вызывает boot() ещё
// раз при неудачной первой попытке. Без этих флагов повторный вызов заново
// регистрировал бы все обработчики кликов ниже (они уже были навешаны в
// первой попытке, до провала на loadBootstrap()) — каждый клик после этого
// срабатывал бы дважды (двойные API-запросы, двойные тосты и т.д.).
let preBootstrapInitDone = false;
let postBootstrapInitDone = false;

async function boot() {
    try {
        if (!preBootstrapInitDone) {
            initTelegram();
            initStartQuiz();
            initAdamIntro();
            initFreezeHelp();
            initRescueHelp();
            initInbox();
            initHabitEdit();
            initTrial();
            initAppTour();
            initHandleIntro();
            initSelfRewardActions();
            initTabs();
            initSubpageOverlays();
            initHabitActions();
            initHabitUndo();
            initDailyQuestActions();
            initRatingActions();
            initRatingScopeSwitch();
            initFriendsActions();
            initPairQuest();
            initEvolution();
            initProfileOverlays();
            initArchetypeQuizActions();
            initPlanActions();
            initShopActions();
            initProfileAvatarActions();
            initThemeActions();
            initStreakUI();
            initStreakPopupClick();
            initHeroWidget();
            initProgressActions();
            preBootstrapInitDone = true;
        }
        // ВАЖНО: настройки используют state.settings, поэтому их нельзя
        // инициализировать до первого bootstrap. Иначе boot() падал на
        // state === null, а навигация и вторичные вкладки не запускались.
        // Критический экран готов сразу после bootstrap. Часовой пояс не должен
        // удерживать loading-overlay и мешать первому paint (особенно в Telegram WebView).
        await loadBootstrap();
        if (!postBootstrapInitDone) {
            initSettingsActions();
            initDataSupportActions();
            initChangelogCheck();
            renderGiftsBadge();
            scheduleReceivedGiftsPrompt();
            // Эти три читают state.settings/state.user СИНХРОННО в момент своей
            // инициализации (не только внутри later-колбэков) — как и
            // initSettingsActions/initDataSupportActions выше, обязаны идти
            // ПОСЛЕ первого bootstrap, иначе boot() падает на state === null
            // (см. комментарий над loadBootstrap() выше) и вся остальная
            // инициализация после падения просто не происходит.
            initPublicProfileActions();
            initUserHandleActions();
            initIdentityEditor();
            initGoalsActions();
            initLanguageActions();
            initGenderActions();
            postBootstrapInitDone = true;
            maybeShowGraceScreen();
            initLabMode();
            initSimMode();
            renderAdamIntroSettings();
            initPartner();
            setTimeout(() => { rescueHelpReady = maybeShowRescueHelp(); }, 4000);
            setTimeout(() => maybeShowFreezeHelp(), 5000);
        }
        if (document.getElementById("archetypeQuizBtn") && state?.user?.archetype) {
          document.getElementById("archetypeQuizBtn").textContent = state.user.archetype;
        }
        requestAnimationFrame(() => {
            // Принудительно отдаём браузеру один чистый кадр для компоновки
            // верхней карточки + Ударного режима после тяжёлого bootstrap.
            // decor-settled здесь больше не снимается — см. scheduleDecorSettle().
            void document.getElementById("content")?.offsetHeight;
        });
        // Некритичная синхронизация — только после первого интерактивного кадра.
        setTimeout(() => syncTimezone(), 0);
    } catch (err) {
        console.error("boot() failed:", err);
        if (err?.data?.error === "trial_expired") return;      // пробный период закончился: окно оплаты уже открыто (api() выше)
        // Улучшение #60: если ПЕРВЫЙ bootstrap так и не смог загрузиться
        // (state всё ещё пуст — значит рендерить вообще нечего), обычный
        // toast — тупик: он исчезнет через пару секунд, а пользователь
        // останется на пустом экране без способа повторить попытку, кроме
        // полного перезапуска Mini App. Показываем полноэкранный баннер с
        // кнопкой "Повторить" вместо этого. Если же bootstrap когда-то уже
        // прошёл успешно (упала только более поздняя, некритичная часть
        // инициализации) — интерфейс уже отрисован, toast достаточно.
        if (!state) {
            const banner = document.getElementById("bootRetryBanner");
            const bannerText = banner?.querySelector(".boot-retry-banner__text");
            if (bannerText) bannerText.textContent = friendlyError(err) || "Не удалось загрузить данные";
            if (banner) banner.hidden = false;
        } else {
            showToast(friendlyError(err) || "Не удалось загрузить данные", "error");
        }
    } finally {
        const overlay = document.getElementById("loadingOverlay");
        if (overlay) overlay.hidden = true;
    }
}

document.getElementById("bootRetryBtn")?.addEventListener("click", () => {
    const banner = document.getElementById("bootRetryBanner");
    const overlay = document.getElementById("loadingOverlay");
    if (banner) banner.hidden = true;
    if (overlay) overlay.hidden = false;
    haptic("light");
    boot();
});

document.addEventListener("DOMContentLoaded", boot);

// Жалоба пользователя: открываешь Mini App повторно — на экране старые
// уровень/монеты/серия (например level 9 вместо реальных 37), хотя
// сервер уже давно отдаёт актуальные данные. Причина: Telegram при
// повторном открытии часто РЕЗЮМИРУЕТ уже загруженную WebView вместо
// полной перезагрузки страницы — DOMContentLoaded не срабатывает снова,
// а boot() выше запускается только один раз за всё время жизни этой
// WebView. Раз полной перезагрузки может не случиться сама собой,
// подгружаем свежий /api/bootstrap вручную при возврате в приложение
// (только если оно реально было скрыто заметное время — иначе спамили
// бы запросом на каждое мимолётное переключение вкладок).
let _hiddenAt = null;
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        _hiddenAt = Date.now();
        return;
    }
    const wasHiddenMs = _hiddenAt ? Date.now() - _hiddenAt : 0;
    _hiddenAt = null;
    if (!state || !postBootstrapInitDone || wasHiddenMs < 15000) return;
    loadBootstrap().then(invalidateProfileData).catch(() => {});
});

document.getElementById("aiCoachBtn").addEventListener("click", () => {
    hideProductHint();
    api('/api/onboarding/stage', { method: 'POST', body: JSON.stringify({ stage: 4 }) }).catch(() => {});
    haptic("light");
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.hidden = false;
    // небольшая пауза, чтобы браузер успел отрисовать монетку до ухода со страницы
    setTimeout(() => { window.location.href = adamCoachUrl(); }, 60);
});

// «Показать напоминание» в Настройках — как выглядит подсказка и первый вопрос Адама.
document.getElementById("adamNudgeDemoBtn")?.addEventListener("click", () => {
    haptic("light");
    closeAllSubpageOverlays();
    document.querySelector('.tab-bar__item[data-tab="home"]')?.click();
    setTimeout(() => showAdamNudge({ demo: true }), 450);
});

// Плитка «статус» в профиле → окно со всеми статусами серии.
(() => {
  const tile = document.getElementById("profileMetricStatus");
  const overlay = document.getElementById("statusLadderOverlay");
  tile?.addEventListener("click", openStatusLadder);
  tile?.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openStatusLadder(); } });
  overlay?.addEventListener("click", (e) => {
    if (performance.now() - statusLadderOpenedAt < 300) return;        // двойной тап по плитке не должен тут же закрыть окно
    if (e.target === overlay || e.target.closest("#statusLadderClose")) { haptic("light"); closeStatusLadder(); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeStatusLadder(); });
})();

document.getElementById("adminPanelBtn")?.addEventListener("click", () => {
    haptic("light");
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.hidden = false;
    setTimeout(() => { window.location.href = "/admin"; }, 60);
});

})();