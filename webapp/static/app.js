(() => {
  "use strict";

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
          url: location.href,
        }),
        keepalive: true,
      }).catch(() => {});
    } catch (_) {}
  }
  window.addEventListener("error", (e) => {
    reportClientError(e.message, e.error && e.error.stack);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason;
    reportClientError(
      reason && reason.message ? reason.message : String(reason),
      reason && reason.stack
    );
  });

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  try {
    const lowPower =
      (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) ||
      (navigator.deviceMemory && navigator.deviceMemory <= 4) ||
      (navigator.connection && navigator.connection.saveData);
    if (lowPower) document.documentElement.classList.add("performance-lite");
  } catch (_) {}
  const RING_CIRCUMFERENCE = 326.7; // 2 * PI * 52

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
  function openAchievementShare({ title, big, status }) {
    const overlay = document.getElementById("achievementShareOverlay");
    const titleEl = document.getElementById("achievementShareTitle");
    const daysEl = document.getElementById("achievementShareDays");
    const statusEl = document.getElementById("achievementShareStatus");
    const levelEl = document.getElementById("achievementShareLevel");
    const coinsEl = document.getElementById("achievementShareCoins");
    if (titleEl) titleEl.textContent = title;
    if (daysEl) daysEl.textContent = big;
    if (statusEl) statusEl.textContent = status;
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
  let skipPromptHabitId = null; // id привычки, для которой сейчас открыт выбор причины пропуска
  let currentLanguage = "ru"; // roadmap #46 — язык интерфейса, обновляется из state.settings.language
  let reactPickerForId = null; // roadmap #19 — telegram_id, для которого сейчас открыт выбор эмодзи-реакции в рейтинге

  const HABIT_CATEGORY_META = {
    health: { emoji: "🩺", label: "Здоровье" },
    work: { emoji: "💼", label: "Работа" },
    study: { emoji: "📚", label: "Учёба" },
    mind: { emoji: "🧘", label: "Разум" },
    other: { emoji: "✨", label: "Другое" },
  };
  const SKIP_REASONS = ["Болею", "В отъезде", "Просто пропускаю"];


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



  
  function initTelegram() {
    if (!tg) return;
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0E0B14");
      tg.setBackgroundColor("#0E0B14");
    } catch (e) { /* старые клиенты могут не поддерживать */ }
  }

  function initData() {
    return tg ? tg.initData : "";
  }

  function haptic(style) {
    if (tg && tg.HapticFeedback) {
      try { tg.HapticFeedback.impactOccurred(style || "light"); } catch (e) {}
    }
  }

  // Короткий приятный "дзынь" для микро-побед (похвала за задачу, монеты,
  // бонусное окно) — не громкий системный звук, а мягкий синтезированный
  // тон через WebAudio, чтобы не требовать отдельного аудиофайла.
  function playChime(variant) {
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
    const timeoutMs = Number(options.timeoutMs || (path === "/api/bootstrap" ? 12000 : 15000));
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    const fetchOptions = { ...options, signal: controller.signal };
    delete fetchOptions.timeoutMs;

    try {
      const res = await fetch(path, {
        ...fetchOptions,
        headers: {
          "Content-Type": "application/json",
          "Authorization": "tma " + initData(),
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
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          state = await api("/api/bootstrap");
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
            showLevelUp(newLevel);
          }
          knownLevel = newLevel;
          renderAll();
          return state;
        } catch (err) {
          lastError = err;
          // Один короткий повтор только для временной сетевой/серверной ошибки.
          if (attempt === 0 && (!err.status || err.status >= 500 || err.code === "timeout")) {
            await new Promise(resolve => setTimeout(resolve, 350));
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
      sub.textContent = "Ты закрыл всё, что планировал — отличная работа.";
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
  // "Забрать" у выполненных.
  function renderDailyQuests() {
    const wrap = document.getElementById("dailyQuests");
    const list = document.getElementById("dailyQuestsList");
    if (!wrap || !list) return;
    const quests = Array.isArray(state.daily_quests) ? state.daily_quests : [];
    if (quests.length === 0) { wrap.hidden = true; return; }
    wrap.hidden = false;
    list.innerHTML = quests.map(q => {
      const pct = Math.min(100, Math.round(100 * q.progress / q.target));
      const stateClass = q.claimed ? "is-claimed" : (q.completed ? "is-ready" : "");
      return `
      <li class="daily-quest ${stateClass}">
        <span class="daily-quest__emoji">${q.emoji}</span>
        <span class="daily-quest__body">
          <span class="daily-quest__title">${escapeHtml(q.title)}</span>
          <span class="daily-quest__bar"><span class="daily-quest__bar-fill" style="width:${pct}%"></span></span>
        </span>
        ${q.claimed
          ? `<span class="daily-quest__done">✓</span>`
          : q.completed
            ? `<button type="button" class="daily-quest__claim" data-quest="${q.key}">+${q.reward} ${ADAM_COIN_ICON}</button>`
            : `<span class="daily-quest__progress">${q.progress}/${q.target}</span>`
        }
      </li>`;
    }).join("");
  }

  // Roadmap #11 — виртуальный питомец.
  function renderPetWidget() {
    const wrap = document.getElementById("petWidget");
    if (!wrap) return;
    const pet = state.pet;
    if (!pet) { wrap.hidden = true; return; }
    wrap.hidden = false;
    document.getElementById("petWidgetEmoji").textContent = pet.emoji;
    document.getElementById("petWidgetName").textContent = pet.stage_name;
    const bar = document.getElementById("petWidgetBarFill");
    const hint = document.getElementById("petWidgetHint");
    if (pet.is_max_stage) {
      bar.style.width = "100%";
      hint.textContent = "Максимальная стадия — легенда!";
    } else {
      const prevThreshold = PET_STAGE_THRESHOLDS.filter(t => t <= pet.care_points).slice(-1)[0] || 0;
      const span = pet.next_stage_points - prevThreshold;
      const into = pet.care_points - prevThreshold;
      bar.style.width = `${Math.min(100, Math.round(100 * into / span))}%`;
      hint.textContent = `Ещё ${pet.next_stage_points - pet.care_points} привычек до ${pet.next_stage_emoji}`;
    }
  }
  const PET_STAGE_THRESHOLDS = [0, 10, 30, 70, 150];

  // Roadmap #13 — тир лиги + прогресс до следующего, в профиле.
  function renderLeagueInfo() {
    const el = document.getElementById("leagueInfo");
    if (!el) return;
    const tier = state.user?.league_tier;
    if (!tier) { el.hidden = true; return; }
    el.hidden = false;
    const progress = state.user?.league_progress;
    el.textContent = progress
      ? `${tier} · до «${progress.next_tier}» ещё ${progress.xp_needed} XP`
      : `${tier} · максимальная лига`;
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

  function applyColorMode() {
    const mode = state?.settings?.color_mode === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-mode", mode);
    document.querySelectorAll(".color-mode-btn").forEach(btn => {
      btn.classList.toggle("is-active", btn.dataset.mode === mode);
    });
  }

  function renderAll() {
    // Критический путь: сначала только то, что пользователь видит на Главной.
    // Привычки и Ударный режим больше не конкурируют за CPU с магазином,
    // рейтингом и архивом достижений.
    renderPlayerCard();
    renderHabits();
    renderPlan();
    renderTodayFocus();
    renderStreak();
    renderBoosterBanner();
    renderDailyQuests();
    renderLeagueInfo();
    applyColorMode();
    applyLanguage();
    renderPetWidget();
    const bw = state?.bonus_window;
    setBonusWindow(bw && bw.active ? bw.until : null);
    maybeShowAppTour();
    maybeShowStreakOnboarding();

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

    setTabLoading(key, true);
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
              renderProfileAvatarControls();
              renderThemePicker();
              renderAchievements();
              loadActivityFeed();
              // Roadmap #26 — тепловая карта года живёт в Профиле, но
              // данные общие с вкладкой "Календарь" — подгружаем их же,
              // не дублируя на сервере (loadBootstrapSecondary дедуплицирует
              // повторные вызовы сама, см. secondaryLoaded выше).
              loadBootstrapSecondary("calendar");
            } else if (key === "rating") {
              state.leaderboard = data.leaderboard || [];
              renderRating();
              loadTeamAndSeason();
            } else if (key === "calendar") {
              state.calendar_events = data.calendar_events || [];
              renderCalendar();
              renderYearHeatmap();
            }
            secondaryLoaded.add(key);
            setTabLoading(key, false);
          }
          return state;
        } catch (err) {
          lastError = err;
          if (attempt === 0 && (!err.status || err.status >= 500 || err.code === "timeout")) {
            await new Promise(resolve => setTimeout(resolve, 350));
            continue;
          }
          break;
        }
      }
      console.error(`bootstrap-secondary(${key}) failed:`, lastError);
      setTabLoading(key, false, friendlyError(lastError) || "Не удалось загрузить раздел", key);
      showToast(friendlyError(lastError) || "Не удалось загрузить раздел", "error");
      return state;
    })().finally(() => secondaryPromises.delete(key));

    secondaryPromises.set(key, promise);
    return promise;
  }

  // Пока Mini App не виден, декоративные анимации не должны тратить батарею/CPU.
  // При возврате браузер продолжает их с текущего состояния без резкого скачка.
  document.addEventListener("visibilitychange", () => {
    document.documentElement.classList.toggle(
      "app-performance-paused",
      document.hidden
    );
  });

  // Чисто атмосферные бесконечные эффекты (искры, блики) нужны только в первые
  // секунды после открытия — дальше это просто лишняя нагрузка на GPU и повод
  // для перегрева. Через паузу "успокаиваем" их классом decor-settled, а при
  // возврате в приложение/смене вкладки даём короткое "оживление" заново.
  let decorSettleTimer = null;
  function scheduleDecorSettle(delayMs = 3500) {
    if (decorSettleTimer) clearTimeout(decorSettleTimer);
    document.documentElement.classList.remove("decor-settled");
    decorSettleTimer = window.setTimeout(() => {
      document.documentElement.classList.add("decor-settled");
    }, delayMs);
  }
  scheduleDecorSettle();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleDecorSettle();
  });

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
    if (profileStatus) profileStatus.textContent = streakStatus || (streakDays ? "В ударе" : "Серия не начата");
    if (profileFrame) {
      if (reward) {
        profileFrame.textContent = `🏆 ${reward.frame}`;
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
    const metricReward = document.getElementById("profileMetricReward");
    if (profileDays) profileDays.textContent = streakDays;
    if (profileDaysLabel) profileDaysLabel.textContent = pluralRu(streakDays, "день подряд", "дня подряд", "дней подряд");
    if (metricStreak) metricStreak.textContent = streakDays;
    if (metricFreeze) metricFreeze.textContent = `${streak.freeze_balance || 0}/2`;
    if (metricReward) metricReward.textContent = reward ? `${reward.milestone} дн.` : "—";

    // Пром 8 (доп.): счётчик "идеальных дней месяца" (2+ привычки подряд).
    // Показываем только если есть хоть один балл — иначе просто шум для
    // тех, кто ещё не встретил механику удвоения.
    const monthly = state?.monthly_progress;
    const monthlyRow = document.getElementById("monthlyProgressRow");
    const monthlyLabel = document.getElementById("monthlyProgressLabel");
    if (monthlyRow && monthlyLabel) {
      if (monthly && monthly.points > 0) {
        monthlyRow.hidden = false;
        monthlyLabel.textContent = `${monthly.points}/${monthly.total}`;
      } else {
        monthlyRow.hidden = true;
      }
    }
  }

  function openStreakCelebration(event) {
    const overlay = document.getElementById("streakCelebrationOverlay");
    const message = document.getElementById("streakCelebrationMessage");
    const seven = document.getElementById("celebrationSeven");
    if (!overlay || !event) return;
    if (message) {
      const reward = (state.streak?.rewards || []).find(r => Number(r.milestone) === Number(event.streak));
      message.textContent = reward
        ? `🏆 ${reward.status} — открыта рамка «${reward.frame}». ${event.message || ""}`
        : (event.message || "День закрыт. Продолжай.");
    }
    if (seven) {
      seven.innerHTML = (state.streak?.last7 || []).map(d => {
        const cls = d.status === "completed" ? "is-done" : d.status === "freeze" ? "is-freeze" : "is-empty";
        return `<span class="streak-seven__day ${cls}">${d.status === "completed" ? "🔥" : d.status === "freeze" ? "❄️" : "○"}</span>`;
      }).join("");
    }
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => overlay.classList.add("show"));
    haptic("medium");
    try { if (navigator.vibrate) navigator.vibrate([35, 25, 55]); } catch (_) {}
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
    haptic("medium");
    try { if (navigator.vibrate) navigator.vibrate([30, 40, 30, 40, 70]); } catch (_) {}
    playChime("bonus");
  }

  function closeBonusIntro() {
    const overlay = document.getElementById("doubleBonusOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    setTimeout(() => { overlay.hidden = true; }, 300);
  }

  // ===================== ОБУЧЕНИЕ ПРИ ПЕРВОМ ВХОДЕ =====================
  const APP_TOUR_STEPS = [
    {
      icon: "👋",
      title: "Добро пожаловать в Project ADAM",
      text: "Я — твой личный ИИ-наставник по привычкам. Покажу за минуту, что тут где и зачем.",
    },
    {
      icon: "🎯",
      title: "Привычки держат серию",
      text: "Отмечай хотя бы одну привычку в день, чтобы не терять ударный режим. Вторая подряд в течение 30 минут — уже двойные Adam Coin.",
    },
    {
      icon: "✦",
      title: "План дня — отдельно от привычек",
      text: "Одна главная задача и до 5 обычных. Своя логика, свой темп — не смешивается с привычками.",
    },
    {
      // Эмодзи монеты (U+1FA99) не везде рендерится — вместо него та же
      // SVG-иконка Adam Coin, что используется по всему приложению
      // (профиль, магазин, рейтинг), гарантированно отрисовывается всегда.
      icon: ADAM_COIN_ICON,
      title: "Adam Coin открывают вещи",
      text: "Зарабатывай монеты за привычки и задачи — трать их в магазине на рамки, темы и заморозки серии.",
    },
    {
      icon: "🤖",
      title: "Спроси у ADAM",
      text: "Обсуди цель, разбери день или просто спроси совет — отвечаю прямо во вкладке «ИИ».",
    },
    {
      icon: "🏆",
      title: "Сравнивай и настраивай",
      text: "Смотри своё место в рейтинге, собирай рамки за серию, настраивай профиль. Погнали!",
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
    api("/api/tour/seen", { method: "POST" }).catch(() => {});
  }

  let appTourShownThisSession = false;
  function maybeShowAppTour() {
    if (!state?.show_app_tour || appTourShownThisSession) return;
    appTourShownThisSession = true;
    openAppTour();
  }

  function initAppTour() {
    document.getElementById("appTourNext")?.addEventListener("click", () => {
      if (appTourStep >= APP_TOUR_STEPS.length - 1) {
        closeAppTour();
        return;
      }
      appTourStep += 1;
      renderAppTourStep(true);
      haptic("light");
    });
    document.getElementById("appTourBack")?.addEventListener("click", () => {
      if (appTourStep === 0) return;
      appTourStep -= 1;
      renderAppTourStep(true);
      haptic("light");
    });
    document.getElementById("appTourSkip")?.addEventListener("click", closeAppTour);
  }

  function maybeShowStreakOnboarding() {
    const data = state?.streak_onboarding;
    if (!data?.show || !data.message) return;
    const overlay = document.getElementById("streakOnboardingOverlay");
    const coach = document.getElementById("streakOnboardingCoach");
    if (!overlay) return;
    if (coach) coach.textContent = `🤖 Адам: ${data.message}`;
    overlay.hidden = false;
    overlay.classList.add("show");
  }

  function closeStreakOnboarding() {
    const overlay = document.getElementById("streakOnboardingOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    setTimeout(() => { overlay.hidden = true; }, 300);
    api("/api/streak/onboarding/seen", {method: "POST"}).catch(() => {});
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
      showToast(day.getAttribute("title") || "День серии", "success", 1800);
    });
    document.getElementById("streakCelebrationContinue")?.addEventListener("click", () => {
      closeStreakCelebration();
      if (pendingBonusIntro) {
        pendingBonusIntro = false;
        setTimeout(openBonusIntro, 380);
      }
    });
    document.getElementById("doubleBonusContinue")?.addEventListener("click", closeBonusIntro);
    document.getElementById("streakOnboardingContinue")?.addEventListener("click", closeStreakOnboarding);
    document.getElementById("shareAchievementBtn")?.addEventListener("click", () => {
      const days = Number(state?.streak?.days || 0);
      openAchievementShare({
        title: "Ударный режим",
        big: formatDays(days),
        status: state?.streak?.temp_status || "Ударный режим",
      });
    });
    document.getElementById("achievementShareClose")?.addEventListener("click", () => {
      const overlay = document.getElementById("achievementShareOverlay");
      if (!overlay) return;
      overlay.classList.remove("show");
      overlay.setAttribute("aria-hidden", "true");
      setTimeout(() => { overlay.hidden = true; }, 220);
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

  function showToast(message, kind, duration, action) {
    toastQueue.push({ message, kind, duration, action });
    if (!toastActive) _drainToastQueue();
  }

  function _drainToastQueue() {
    const next = toastQueue.shift();
    if (!next) { toastActive = false; return; }
    toastActive = true;
    const { message, kind, duration, action } = next;
    const el = document.getElementById("toast");
    el.className = "toast is-visible" + (kind ? " is-" + kind : "");
    clearTimeout(toastTimer);
    if (action && action.label) {
      el.innerHTML = "";
      const span = document.createElement("span");
      span.textContent = message;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = action.label;
      btn.style.cssText = "margin-left:10px;background:none;border:none;color:inherit;font:inherit;font-weight:700;text-decoration:underline;text-underline-offset:2px;cursor:pointer;padding:0;";
      btn.addEventListener("click", () => {
        clearTimeout(toastTimer);
        el.classList.remove("is-visible");
        toastActive = false;
        try { action.onClick && action.onClick(); } finally { _drainToastQueue(); }
      });
      el.appendChild(span);
      el.appendChild(btn);
    } else {
      el.textContent = message;
    }
    const ms = duration || 2200;
    toastTimer = setTimeout(() => {
      el.classList.remove("is-visible");
      toastActive = false;
      _drainToastQueue();
    }, ms);
    // Промт 7.1: короткие микро-победы (похвала за задачу, монеты за
    // привычку) сопровождаются вибрацией и мягким звуком — обычные тосты
    // (сохранено, ошибка и т.п.) молчат, чтобы не звенеть по любому поводу.
    if (kind === "praise") {
      haptic("light");
      try { if (navigator.vibrate) navigator.vibrate([25, 20, 25]); } catch (_) {}
      playChime();
    }
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
        <span class="frame-choice__text"><b>${escapeHtml(f.title)}</b><small>${f.available ? (f.type === "achievement" ? "Награда" : f.type === "paid" ? "Premium" : "Доступна") : (f.type === "achievement" ? `Нужно ${f.id === "streak_14" ? 14 : 30} дней` : "Не куплена")}</small></span>
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
    document.getElementById("streakValue").textContent = u.streak || 0;
    document.getElementById("coinValue").textContent = u.xp || 0;

    const badgeEl = document.getElementById("playerBadge");
    if (badgeEl) badgeEl.style.display = u.badge ? "inline" : "none";

    const adminBtn = document.getElementById("adminPanelBtn");
    if (adminBtn) {
        adminBtn.hidden = !u.is_admin;
        // Резервируем место под кнопку в строке с именем, иначе длинное
        // имя/приветствие визуально и по кликам перекрывает кнопку.
        adminBtn.closest(".player-card")?.classList.toggle("has-admin-btn", !!u.is_admin);
    }

    const xpIntoLevel = Math.max(0, Math.min(99.999, (u.total_xp ?? u.xp ?? 0) % 100));
    document.getElementById("xpLabel").textContent = `${Math.floor(xpIntoLevel)} / 100 XP`;

    // Force the browser to animate the level number only when its value changes.
    const levelValue = u.level || 1;
    const previousLevel = levelEl?.dataset.level;
    if (levelEl) {
      levelEl.dataset.level = String(levelValue);
      levelEl.textContent = levelValue;
      if (previousLevel !== undefined && previousLevel !== String(levelValue)) {
        badge?.classList.remove("is-changing");
        void badge?.offsetWidth;
        badge?.classList.add("is-changing");
        setTimeout(() => badge?.classList.remove("is-changing"), 560);
      }
    }

    if (xpBarFill) {
      requestAnimationFrame(() => {
        xpBarFill.style.width = xpIntoLevel + "%";
      });
    }

    if (ringFill) {
      const offset = RING_CIRCUMFERENCE * (1 - xpIntoLevel / 100);
      ringFill.classList.add("is-progressing");
      requestAnimationFrame(() => {
        ringFill.style.strokeDashoffset = offset;
      });
      clearTimeout(ringFill._progressTimer);
      ringFill._progressTimer = setTimeout(() => ringFill.classList.remove("is-progressing"), 760);
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

  function renderHabits() {
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
        strugglingBanner.innerHTML = `
          <span class="struggling-habit-banner__icon">🤖</span>
          <span class="struggling-habit-banner__text">«${escapeHtml(top.title)}» не получается ${top.missed} из последних дней — может, снизить планку (реже в неделю или счётчик поменьше)?</span>
          <button type="button" class="struggling-habit-banner__close" aria-label="Закрыть">✕</button>
        `;
        strugglingBanner.querySelector(".struggling-habit-banner__close")?.addEventListener("click", () => {
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

    list.innerHTML = visibleHabits.map(h => {
      const catMeta = h.category ? HABIT_CATEGORY_META[h.category] : null;
      const isCounter = (h.target_count || 1) > 1;
      const badges =
        (h.priority === 2 ? `<span class="habit-item__badge" title="Важная привычка">⭐</span>` : "") +
        (catMeta ? `<span class="habit-item__badge" title="${escapeHtml(catMeta.label)}">${catMeta.emoji}</span>` : "") +
        (h.frequency_per_week ? `<span class="habit-item__badge habit-item__badge--freq" title="Гибкая периодичность">${h.weekly_progress ?? 0}/${h.frequency_per_week} нед.</span>` : "");
      const noteBtn = h.completed
        ? `<button class="habit-item__note" data-action="note" aria-label="Заметка/фото">📝</button>`
        : "";
      // Roadmap #23/#36 — подсказка времени, только пока у привычки ещё
      // нет своего planned_time (иначе она и так уже видна как чип ⏰).
      const suggestBtn = h.suggested_time
        ? `<button class="habit-item__suggest-time" data-action="accept-suggested-time" data-time="${h.suggested_time}" title="AI заметил: обычно ты делаешь это в это время">🤖 ${h.suggested_time}?</button>`
        : "";

      if (h.skip_reason) {
        return `
      <li class="habit-item is-skipped" data-id="${h.id}">
        <button class="habit-item__check" disabled>⏭</button>
        ${badges}<span class="habit-item__title">${escapeHtml(h.title)}</span>
        <button class="habit-item__del" data-action="delete" aria-label="Удалить">✕</button>
        <div class="habit-item__skip-note">Пропущено: ${escapeHtml(h.skip_reason)} · <button type="button" data-action="unskip">вернуть</button></div>
      </li>`;
      }

      if (skipPromptHabitId === h.id) {
        return `
      <li class="habit-item" data-id="${h.id}">
        <button class="habit-item__check" data-action="complete"></button>
        ${badges}<span class="habit-item__title">${escapeHtml(h.title)}</span>
        <button class="habit-item__del" data-action="delete" aria-label="Удалить">✕</button>
        <div class="habit-skip-reasons">
          ${SKIP_REASONS.map(r => `<button type="button" class="habit-skip-reason-chip" data-reason="${escapeHtml(r)}">${escapeHtml(r)}</button>`).join("")}
          <button type="button" class="habit-skip-reason-cancel">Отмена</button>
        </div>
      </li>`;
      }

      const checkLabel = h.completed
        ? "✓"
        : (isCounter ? `${h.progress_count || 0}/${h.target_count}` : "");
      const checkClass = isCounter && !h.completed ? "habit-item__check habit-item__check--counter" : "habit-item__check";

      return `
      <li class="habit-item ${h.completed ? "is-done" : ""}" data-id="${h.id}">
        <button class="${checkClass}" data-action="${isCounter && !h.completed ? "progress" : "complete"}" ${h.completed ? "disabled" : ""}>${checkLabel}</button>
        ${badges}<span class="habit-item__title">${escapeHtml(h.title)}</span>
        <button class="habit-item__time ${h.planned_time ? "is-set" : ""}" data-action="edit-time" data-time="${h.planned_time || ""}" aria-label="Своё время напоминания">${h.planned_time ? "⏰ " + h.planned_time : "⏰"}</button>
        ${suggestBtn}
        ${noteBtn}
        ${h.completed ? "" : `<button class="habit-item__skip" data-action="skip" aria-label="Пропустить сегодня">⏭</button>`}
        <button class="habit-item__del" data-action="delete" aria-label="Удалить">✕</button>
      </li>`;
    }).join("");
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
    const items = Array.isArray(state.shop_items) ? state.shop_items : [];

    if (balance) balance.textContent = Number(state.user?.xp || 0).toLocaleString("ru-RU");
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
      const amountMatch = String(it.payload || it.name || "").match(/(\d+)/);
      const amount = amountMatch ? amountMatch[1] : "";

      let title = escapeHtml(it.name || "Товар ADAM");
      let desc = escapeHtml(it.description || "");

      if (isAnswer) {
        title = amount ? `+${amount} ответов` : title;
        desc = amount ? `Ещё ${amount} запросов к ADAM` : desc;
      }

      // Цена находится только слева. На кнопке никогда не дублируем
      // стоимость: если хватает коинов — только «Купить».
      let btnLabel = "Купить";
      let btnClass = "buy-btn";
      let disabled = "";
      const isStars = it.item_type === "frame_stars" || it.item_type === "answer_pack_stars" || it.item_type === "booster_stars";
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
        <li class="shop-item ${isAnswer ? "shop-item--answers" : ""}" data-id="${it.id}">
          <div class="shop-item__top">
            <span class="shop-item__icon">${isAnswer ? "💬" : "✦"}</span>
            ${isAnswer ? `<span class="shop-item__tag">ДОП. ОТВЕТЫ</span>` : ""}
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
    let rows = Array.isArray(state.leaderboard) ? state.leaderboard.slice() : [];
    if (count) count.textContent = rows.length;
    if (countLabel) countLabel.textContent = pluralRu(rows.length, "игрок", "игрока", "игроков");
    if (rows.length === 0) {
      if (podium) podium.innerHTML = "";
      list.innerHTML = `<li class="empty-hint">Рейтинг пока пуст</li>`;
      return;
    }
    const myId = state.user.telegram_id;
    const getStatus = (r) => {
      const ss = r.streak_status || {};
      const reward = (ss.rewards || [])[0];
      let status = ss.temp_status || (reward ? reward.status : "");
      if (/^Огонь\s+/i.test(status)) status = status.replace(/^Огонь/i, "В ударе");
      return {ss, reward, status};
    };
    const medal = ["🥇", "🥈", "🥉"];
    if (podium) {
      podium.innerHTML = rows.slice(0, 3).map((r, idx) => {
        const rank = idx + 1, isMe = r.telegram_id === myId, name = r.first_name || r.username || "Игрок";
        const {ss, reward, status} = getStatus(r);
        const frame = ss.temp_frame || "none";
        return `<div class="rating-podium-card rank-${rank} ${isMe ? "is-me" : ""}">
          <div class="rating-podium-card__crown">${medal[idx]}</div>
          <div class="rating-podium-card__avatar frame-${escapeHtml(r.frame_id || frame)}">${String(r.avatar_id || "default").startsWith("upload:") ? `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(String(r.avatar_id).split(":")[1])}.jpg" alt="">` : escapeHtml((name[0] || "A").toUpperCase())}</div>
          <div class="rating-podium-card__rank">#${rank}</div>
          <div class="rating-podium-card__name">${escapeHtml(name)} ${r.badge ? "🏅" : ""}</div>
          ${r.league_tier ? `<div class="rating-podium-card__league">${escapeHtml(r.league_tier)}</div>` : ""}
          ${status ? `<div class="rating-podium-card__status">${escapeHtml(status)}</div>` : ""}
          <div class="rating-podium-card__stats"><span>🔥 ${Number(r.streak || 0)}</span><span>${ADAM_COIN_ICON} ${Number(r.xp || 0)}</span></div>
        </div>`;
      }).join("");
    }
    list.innerHTML = rows.slice(3).map((r, i) => {
      const rank = i + 4, isMe = r.telegram_id === myId, name = r.first_name || r.username || "Игрок";
      const {ss, status} = getStatus(r);
      const frame = ss.temp_frame || "none";
      return `<li class="rating-item ${isMe ? "is-me" : ""} rank-${rank}">
        <span class="rating-item__rank">${rank}</span>
        <span class="rating-avatar frame-${escapeHtml(r.frame_id || frame)}">${String(r.avatar_id || "default").startsWith("upload:") ? `<img class="avatar-photo" src="/media/avatars/${encodeURIComponent(String(r.avatar_id).split(":")[1])}.jpg" alt="" loading="lazy">` : escapeHtml((name[0] || "A").toUpperCase())}</span>
        <span class="rating-item__name">
          <span class="rating-item__name-line"><span class="rating-item__name-text">${escapeHtml(name)}</span>${r.badge ? '<span class="rating-item__badge">🏅</span>' : ""}${isMe ? ' <span class="rating-item__me">(ты)</span>' : ""}</span>
          ${r.league_tier ? `<small class="rating-item__league">${escapeHtml(r.league_tier)}</small>` : ""}
          ${status ? `<small class="rating-item__status">${escapeHtml(status)}</small>` : ""}
        </span>
        <span class="rating-item__meta"><span class="rating-stat"><span class="material-symbols-rounded stat-icon">local_fire_department</span>${Number(r.streak || 0)}</span><span class="rating-stat">${ADAM_COIN_ICON}${Number(r.xp || 0)}</span></span>
        ${r.can_react ? `<button type="button" class="rating-item__react-btn" data-react-target="${r.telegram_id}" aria-label="Поддержать">💌</button>` : ""}
        ${reactPickerForId === r.telegram_id ? `
        <div class="rating-react-picker">
          ${REACTION_EMOJIS.map(e => `<button type="button" class="rating-react-chip" data-emoji="${e}">${e}</button>`).join("")}
        </div>` : ""}
      </li>`;
    }).join("");
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
    document.querySelectorAll(".tab-bar__item").forEach(b => b.classList.toggle("is-active", b === btn));
    document.querySelectorAll(".tab-panel").forEach(panel => {
      const active = panel.dataset.tab === tab;
      panel.hidden = !active;
      if (active) {
        panel.classList.remove("tab-enter");
        requestAnimationFrame(() => {
          panel.classList.add("tab-enter");
          setTimeout(() => panel.classList.remove("tab-enter"), 400);
        });
      }
    });
    // Загружаем только открытый раздел. Никаких фоновых запросов к
    // календарю/рейтингу/профилю при нахождении на Главной.
    if (tab === "profile" || tab === "rating" || tab === "calendar") {
      loadBootstrapSecondary(tab);
    }
    if (tab === "profile") {
      loadProgressStats();
    }
    haptic("light");
    scheduleDecorSettle();
  });
}

// ===================== СВОРАЧИВАЕМЫЕ ФОРМЫ ДОБАВЛЕНИЯ =====================
// "Новая задача"/"Новая привычка" раньше были видны всегда — по умолчанию
// теперь спрятаны за компактной кнопкой "+" (см. .add-collapse в index.html
// и style.css), сама форма и её id/JS не менялись.
function openAddCollapse(collapseId) {
  const collapse = document.getElementById(collapseId);
  if (!collapse) return;
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
// Общая "победная" реакция после выполнения привычки — вызывается и из
// /complete, и из /progress (когда счётчик как раз достиг цели), чтобы не
// дублировать монеты/streak/идеальный-день/цепочку в двух местах.
async function celebrateHabitCompletion(result) {
  const boostTag = result.xp_boosted ? " ⚡x2 бустер" : (result.doubled ? " ⚡️×2" : "");
  const coinText = `+${result.coins || 10} Adam Coin` + boostTag;
  showToast(coinText, "praise");
  if (result.streak_event) {
    pendingBonusIntro = !!result.show_bonus_intro;
    openStreakCelebration(result.streak_event);
  }
  // Пром 8 (доп.): "идеальный день" и, раз в месяц, награда за идеальный
  // месяц — показываем следом за тостом монет, со сдвигом, чтобы не
  // перекрывать друг друга в одном #toast элементе.
  if (result.perfect_day_message) {
    setTimeout(() => showToast(result.perfect_day_message, "praise", 4200), 2400);
  }
  if (result.month_end_reward?.message) {
    setTimeout(() => showToast(result.month_end_reward.message, "praise", 5500),
      result.perfect_day_message ? 6800 : 2400);
  }
  // Roadmap #11 — питомец эволюционировал.
  if (result.pet && result.pet.evolved) {
    setTimeout(() => {
      showToast(`${result.pet.emoji} Питомец вырос: ${result.pet.stage_name}!`, "praise", 4000);
    }, 1200);
  }
  // Roadmap #7 — цепочки привычек: мягкая подсказка "сделал А → предложи Б".
  if (result.chain_suggestion) {
    setTimeout(() => {
      showToast(`👉 Может, теперь «${result.chain_suggestion.title}»?`, "success", 4000);
    }, result.perfect_day_message ? 6800 : 2400);
  }
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

// Roadmap #12 — клик "Забрать" у выполненного квеста дня.
function initDailyQuestActions() {
  const list = document.getElementById("dailyQuestsList");
  if (!list) return;
  list.addEventListener("click", async (e) => {
    const btn = e.target.closest(".daily-quest__claim");
    if (!btn) return;
    const questKey = btn.dataset.quest;
    btn.disabled = true;
    try {
      const result = await api(`/api/quests/${questKey}/claim`, { method: "POST" });
      haptic("medium");
      showToast(`+${result.reward} Adam Coin`, "praise");
      await loadBootstrap();
    } catch (err) {
      showToast(friendlyError(err), "error");
      btn.disabled = false;
    }
  });
}

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
    advToggle.addEventListener("click", () => { advPanel.hidden = !advPanel.hidden; });
  }
  const targetStepper = document.getElementById("newHabitTargetStepper");
  const targetValueEl = document.getElementById("newHabitTargetValue");
  if (targetStepper && targetValueEl) {
    targetStepper.addEventListener("click", (e) => {
      const stepBtn = e.target.closest("[data-step]");
      if (!stepBtn) return;
      const next = Math.max(1, Math.min(20, Number(targetValueEl.textContent) + Number(stepBtn.dataset.step)));
      targetValueEl.textContent = String(next);
    });
  }
  const freqChips = document.getElementById("newHabitFreqChips");
  if (freqChips) {
    freqChips.addEventListener("click", (e) => {
      const chip = e.target.closest(".habit-add-form__freq-chip");
      if (!chip) return;
      freqChips.querySelectorAll(".habit-add-form__freq-chip").forEach(c => c.classList.remove("is-active"));
      chip.classList.add("is-active");
    });
  }

  const filterRow = document.getElementById("habitFilterRow");
  if (filterRow) {
    filterRow.addEventListener("click", (e) => {
      const chip = e.target.closest(".habit-filter-chip");
      if (!chip) return;
      activeHabitFilter = chip.dataset.category || "";
      renderHabits();
    });
  }

  habitList.addEventListener("click", async (e) => {
    // Причина пропуска — выбор одного из готовых чипов ("Болею" и т.п.).
    const reasonChip = e.target.closest(".habit-skip-reason-chip");
    if (reasonChip) {
      const li = reasonChip.closest(".habit-item");
      if (!li) return;
      skipPromptHabitId = null;
      try {
        await api(`/api/habits/${li.dataset.id}/skip`, {
          method: "POST",
          body: JSON.stringify({ reason: reasonChip.dataset.reason }),
        });
        haptic("light");
        await loadBootstrap();
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
      return;
    }
    if (e.target.closest(".habit-skip-reason-cancel")) {
      skipPromptHabitId = null;
      renderHabits();
      return;
    }
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
    const habitId = li.dataset.id;
    const action = btn.dataset.action;

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

    if (action === "skip") {
      // Не шлём запрос сразу — сначала даём выбрать причину (готовые чипы
      // ниже строки), сам API-вызов уходит по клику на конкретный чип
      // (см. обработчик .habit-skip-reason-chip выше).
      skipPromptHabitId = habitId;
      renderHabits();
      return;
    }

    try {
      if (action === "complete") {
        btn.disabled = true;
        const result = await api(`/api/habits/${habitId}/complete`, { method: "POST" });
        haptic("medium");
        await loadBootstrap();
        await celebrateHabitCompletion(result);
      } else if (action === "progress") {
        btn.disabled = true;
        // Roadmap #1 — счётчик: +1 к прогрессу. Если это нажатие как раз
        // закрыло цель, ответ содержит те же поля, что и /complete (монеты,
        // streak и т.д.) — празднуем точно так же.
        const result = await api(`/api/habits/${habitId}/progress`, { method: "POST" });
        haptic(result.just_completed ? "medium" : "light");
        await loadBootstrap();
        if (result.just_completed) {
          await celebrateHabitCompletion(result);
        } else {
          showToast(`${result.progress_count}/${result.target_count}`, "success");
        }
      } else if (action === "note") {
        openHabitNotePrompt(habitId);
      } else if (action === "unskip") {
        await api(`/api/habits/${habitId}/unskip`, { method: "POST" });
        haptic("light");
        await loadBootstrap();
      } else if (action === "delete") {
        deleteHabitWithUndo(habitId);
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
      await loadBootstrap();
    }
  });

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
    const targetValueEl = document.getElementById("newHabitTargetValue");
    const targetCount = targetValueEl ? Number(targetValueEl.textContent) || 1 : 1;
    const activeFreqChip = document.querySelector(".habit-add-form__freq-chip.is-active");
    const frequencyPerWeek = activeFreqChip ? Number(activeFreqChip.dataset.freq) || 0 : 0;
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
          target_count: targetCount > 1 ? targetCount : undefined,
          frequency_per_week: frequencyPerWeek > 0 ? frequencyPerWeek : undefined,
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
      if (targetValueEl) targetValueEl.textContent = "1";
      document.querySelectorAll(".habit-add-form__freq-chip").forEach(c => c.classList.remove("is-active"));
      document.querySelector('.habit-add-form__freq-chip[data-freq="0"]')?.classList.add("is-active");
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
        maybeShowStreakOnboarding();
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
          <button type="button" class="plan-icon-btn" data-main-action="edit" aria-label="Редактировать">✎</button>
          <button type="button" class="plan-icon-btn plan-icon-btn--delete" data-main-action="delete" aria-label="Удалить">✕</button>
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
        <button type="button" class="plan-icon-btn" data-action="edit" aria-label="Редактировать">✎</button>
        <button type="button" class="plan-icon-btn plan-icon-btn--delete" data-action="delete" aria-label="Удалить">✕</button>
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

  addBtn.disabled = plan.tasks.length >= 5 && !editingId;
  if (plan.tasks.length >= 5 && !editingId) {
    addBtn.textContent = "Максимум 5 задач";
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
  if (state?.daily_plan?.tasks) btn.disabled = state.daily_plan.tasks.length >= 5;
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
    try {
      await api("/api/plan/main/save", {
        method: "POST",
        body: JSON.stringify({ text })
      });
      delete mainInput.dataset.editingValue;
      haptic("light");
      await loadBootstrap();
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
        await api(`/api/plan/task/${editingId}`, {
          method: "PUT",
          body: JSON.stringify({ text })
        });
        showToast("Задача обновлена", "success");
      } else {
        await api("/api/plan/task", {
          method: "POST",
          body: JSON.stringify({ text })
        });
      }
      resetPlanTaskEditor();
      haptic("light");
      await loadBootstrap();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  document.getElementById("planList").addEventListener("click", async (e) => {
    const li = e.target.closest(".plan-item");
    if (!li) return;

    const actionBtn = e.target.closest("button[data-action]");
    if (actionBtn) {
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
          document.getElementById("addPlanTaskBtn").textContent = "✓ Сохранить изменения";
          taskInput.focus();
          taskInput.select();
          taskInput.scrollIntoView({ behavior: "smooth", block: "center" });
        } else if (actionBtn.dataset.action === "delete") {
          await api(`/api/plan/task/${taskId}`, { method: "DELETE" });
          if (String(taskInput.dataset.editingTaskId) === String(taskId)) resetPlanTaskEditor();
          await loadBootstrap();
        }
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
    }
  });

  document.addEventListener("change", async (e) => {
    if (e.target.classList.contains("plan-toggle--main")) {
      try {
        const res = await api("/api/plan/main/toggle", { method: "POST" });
        if (res && res.message) showToast(res.message, "praise", 4500);
        await loadBootstrap();
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
        await loadBootstrap();
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

  function initProfileAvatarActions() {
    const trigger = document.getElementById("profilePhotoBtn");
    const input = document.getElementById("profilePhotoInput");
    if (!trigger || !input) return;
    trigger.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) { showToast("Выбери JPG, PNG или WEBP", "error"); return; }
      if (file.size > 5 * 1024 * 1024) { showToast("Фото должно быть не больше 5 МБ", "error"); return; }
      try {
        trigger.disabled = true;
        const form = new FormData();
        form.append("avatar", file, file.name);
        const res = await fetch("/api/profile/avatar", { method: "POST", headers: { "Authorization": "tma " + initData() }, body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data?.error || "upload_failed");
        state.user.avatar_id = data.avatar_id;
        renderProfileAvatarControls();
        renderRating();
        haptic("medium");
        showToast("Аватар обновлён", "success");
      } catch (err) {
        showToast(friendlyError(err), "error");
      } finally { trigger.disabled = false; input.value = ""; }
    });

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
    task_limit: "You can add up to 5 tasks",
    habit_limit: "You can add up to 10 habits",
    daily_limit_reached: "Already bought today — available again tomorrow",
    habit_add_locked: "You already logged and deleted a habit today — adding new ones reopens at midnight",
    invalid_init_data: "Telegram didn't pass auth data. Close the Mini App and reopen it.",
    request_failed: "Couldn't reach the server. Check your connection and try again.",
    not_admin: "Admins only",
    bot_unavailable: "The bot is temporarily unavailable, try again later",
    rate_limited: "Too many requests — wait a couple seconds and try again",
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
        invalid_theme: "Такой темы не существует",
        task_limit: "Можно добавить не больше 5 задач",
        habit_limit: "Можно добавить не больше 10 привычек",
        daily_limit_reached: "Этот пакет уже куплен сегодня — доступен снова завтра",
        habit_add_locked: "Сегодня уже была отметка и удаление привычки — добавление новых открыто с 00:00",
        invalid_init_data: "Telegram не передал данные авторизации. Закройте Mini App и откройте его снова.",
        request_failed: "Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.",
        not_admin: "Доступно только администраторам",
        bot_unavailable: "Бот временно недоступен, попробуйте позже",
        rate_limited: "Слишком много запросов подряд — подожди пару секунд и попробуй ещё раз"
    };

    const activeMap = currentLanguage === "en" ? ERROR_MAP_EN : map;
    const fallback = currentLanguage === "en" ? "Unknown error" : "Неизвестная ошибка";
    return activeMap[code] || (err && err.message) || fallback;
}

  // ===================== LEVEL UP =====================
  let levelUpTimer = null;

  function burstCoins() {
    for (let i = 0; i < 8; i++) {
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

  function showLevelUp(level) {
    const overlay = document.getElementById("levelupOverlay");
    const value = document.getElementById("levelupValue");
    if (!overlay || !value) return;

    value.textContent = level;
    overlay.hidden = false;
    overlay.classList.add("show");
    burstCoins();
    if (tg && tg.HapticFeedback) {
      try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) {}
    }

    clearTimeout(levelUpTimer);
    levelUpTimer = setTimeout(() => {
      overlay.classList.remove("show");
      setTimeout(() => { overlay.hidden = true; }, 300);
    }, 2200);
  }

  // ===================== BOOT =====================
function stabilizeFirstPaint() {
    const critical = [
        document.querySelector("header.player-card"),
        document.querySelector('section[data-tab="home"]'),
        document.getElementById("streakWidget")
    ].filter(Boolean);
    if (!critical.length) return;
    // Не заставляем WebView держать большие слои в compositor-cache.
    critical.forEach(el => {
        el.style.visibility = "visible";
        el.style.contain = el === critical[1] ? "layout style" : "layout paint";
    });
    requestAnimationFrame(() => {
        critical.forEach(el => void el.offsetHeight);
    });
}

// ===================== ПРОГРЕСС + AI-АНАЛИЗ =====================
async function loadProgressStats() {
  try {
    const data = await api("/api/progress/stats");
    const w = data.weekly || {};
    document.getElementById("progressStatCompleted").textContent = w.completed || 0;
    document.getElementById("progressStatActiveDays").textContent = `${w.active_days || 0}/7`;
    document.getElementById("progressStatXp").textContent = w.xp || 0;

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
  try {
    const [teamData, seasonData] = await Promise.all([
      api("/api/team"),
      api("/api/season"),
    ]);
    state.team = teamData.team;
    state.season = seasonData;
    renderTeamCard();
    renderSeasonList();
  } catch (err) {
    console.error("loadTeamAndSeason failed:", err);
  }
}

function renderTeamCard() {
  const box = document.getElementById("teamCard");
  if (!box) return;
  box.hidden = false;
  const team = state.team;

  if (!team) {
    box.innerHTML = `
      <div class="team-card__title">🤝 Групповой челлендж</div>
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
      <span class="team-card__member-name">${escapeHtml(m.first_name || "Игрок")}</span>
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
    <li class="rating-item ${r.telegram_id === myId ? "is-me" : ""}">
      <span class="rating-item__rank">${i + 1}</span>
      <span class="rating-avatar">${escapeHtml((r.first_name || "A")[0].toUpperCase())}</span>
      <span class="rating-item__name"><span class="rating-item__name-line"><span class="rating-item__name-text">${escapeHtml(r.first_name || r.username || "Игрок")}</span></span></span>
      <span class="rating-item__meta"><span class="rating-stat">${ADAM_COIN_ICON}${r.season_xp}</span></span>
    </li>`).join("");
}

function initRatingScopeSwitch() {
  const switcher = document.getElementById("ratingScopeSwitch");
  if (!switcher) return;
  switcher.addEventListener("click", (e) => {
    const btn = e.target.closest(".rating-scope-btn");
    if (!btn) return;
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
            <span class="activity-feed__label">${e.label}${e.detail ? " «" + escapeHtml(e.detail) + "»" : ""}</span>
          </div>`).join("");
  } catch (err) {
    box.innerHTML = "";
  }
}

// Roadmap #19 — реакции/стикеры поддержки другу прямо из рейтинга.
function initRatingActions() {
  const list = document.getElementById("ratingList");
  if (!list) return;
  list.addEventListener("click", async (e) => {
    const emojiChip = e.target.closest(".rating-react-chip");
    if (emojiChip) {
      const targetId = reactPickerForId;
      reactPickerForId = null;
      renderRating();
      try {
        await api(`/api/friends/${targetId}/react`, {
          method: "POST",
          body: JSON.stringify({ emoji: emojiChip.dataset.emoji }),
        });
        haptic("light");
        showToast("Поддержка отправлена " + emojiChip.dataset.emoji, "success");
        await loadBootstrapSecondary("rating");
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
      return;
    }
    const reactBtn = e.target.closest("[data-react-target]");
    if (reactBtn) {
      const targetId = Number(reactBtn.dataset.reactTarget);
      reactPickerForId = reactPickerForId === targetId ? null : targetId;
      renderRating();
    }
  });
}

function initProgressActions() {
  const btn = document.getElementById("progressAiBtn");
  const resultBox = document.getElementById("progressAiResult");
  if (!btn) return;
  btn.addEventListener("click", async () => {
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

  initQuietHoursActions();
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

// Roadmap #39 — короткий тест на архетип личности. Подсчёт целиком на
// клиенте (4 вопроса, каждый вариант тянет к одному из 4 архетипов),
// на сервер уходит только готовый ключ-результат.
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

// Roadmap #48 — переключатель светлой/тёмной темы.
function initColorModeActions() {
  const picker = document.getElementById("colorModePicker");
  if (!picker) return;
  picker.addEventListener("click", async (e) => {
    const btn = e.target.closest(".color-mode-btn");
    if (!btn) return;
    const mode = btn.dataset.mode;
    if (state.settings.color_mode === mode) return;
    document.documentElement.setAttribute("data-mode", mode); // мгновенно, не ждём ответ сервера
    picker.querySelectorAll(".color-mode-btn").forEach(b => b.classList.toggle("is-active", b === btn));
    try {
      await api("/api/settings/color-mode", { method: "POST", body: JSON.stringify({ mode }) });
      state.settings.color_mode = mode;
      haptic("light");
    } catch (err) {
      applyColorMode(); // откатываем визуально, если сервер отказал
      showToast(friendlyError(err), "error");
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

  // Уведомления "в 100 раз лучше": не чёрный ящик — история реально
  // отправленных плановых сообщений, а не только "включено/выключено".
  document.getElementById("notificationHistoryBtn")?.addEventListener("click", async (e) => {
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
            initAppTour();
            initTabs();
            initHabitActions();
            initDailyQuestActions();
            initRatingActions();
            initRatingScopeSwitch();
            initArchetypeQuizActions();
            initPlanActions();
            initShopActions();
            initProfileAvatarActions();
            initThemeActions();
            initStreakUI();
            initStreakPopupClick();
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
            // Эти три читают state.settings/state.user СИНХРОННО в момент своей
            // инициализации (не только внутри later-колбэков) — как и
            // initSettingsActions/initDataSupportActions выше, обязаны идти
            // ПОСЛЕ первого bootstrap, иначе boot() падает на state === null
            // (см. комментарий над loadBootstrap() выше) и вся остальная
            // инициализация после падения просто не происходит.
            initPublicProfileActions();
            initGoalsActions();
            initColorModeActions();
            initLanguageActions();
            postBootstrapInitDone = true;
        }
        if (document.getElementById("archetypeQuizBtn") && state?.user?.archetype) {
          document.getElementById("archetypeQuizBtn").textContent = state.user.archetype;
        }
        requestAnimationFrame(() => {
            document.documentElement.classList.remove("decor-settled");
            // Принудительно отдаём браузеру один чистый кадр для компоновки
            // верхней карточки + Ударного режима после тяжёлого bootstrap.
            void document.getElementById("content")?.offsetHeight;
        });
        // Некритичная синхронизация — только после первого интерактивного кадра.
        setTimeout(() => syncTimezone(), 0);
    } catch (err) {
        console.error("boot() failed:", err);
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

document.getElementById("aiCoachBtn").addEventListener("click", () => {
    haptic("light");
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.hidden = false;
    // небольшая пауза, чтобы браузер успел отрисовать монетку до ухода со страницы
    setTimeout(() => { window.location.href = "/coach"; }, 60);
});

document.getElementById("adminPanelBtn")?.addEventListener("click", () => {
    haptic("light");
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.hidden = false;
    setTimeout(() => { window.location.href = "/admin"; }, 60);
});

})();