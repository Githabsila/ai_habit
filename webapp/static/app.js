(() => {
  "use strict";

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
        const err = new Error((data && data.error) || "request_failed");
        err.data = data;
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

  function renderAll() {
    // Критический путь: сначала только то, что пользователь видит на Главной.
    // Привычки и Ударный режим больше не конкурируют за CPU с магазином,
    // рейтингом и архивом достижений.
    renderPlayerCard();
    renderHabits();
    renderPlan();
    renderStreak();
    const bw = state?.bonus_window;
    setBonusWindow(bw && bw.active ? bw.until : null);
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

  function setTabLoading(key, loading, message = "") {
    const panel = getTabPanel(key);
    if (!panel) return;
    panel.classList.toggle("tab-panel--loading", !!loading);
    panel.setAttribute("aria-busy", loading ? "true" : "false");
    let layer = panel.querySelector(":scope > .tab-loading-state");
    if (loading) {
      if (!layer) {
        layer = document.createElement("div");
        layer.className = "tab-loading-state";
        layer.innerHTML = '<div class="tab-loading-state__spinner" aria-hidden="true"></div><span>Загрузка…</span>';
        panel.prepend(layer);
      }
      layer.hidden = false;
      layer.innerHTML = '<div class="tab-loading-state__spinner" aria-hidden="true"></div><span>Загрузка…</span>';
    } else if (layer) {
      // Полностью удаляем индикатор после загрузки, а не просто скрываем его.
      // Так он не сможет остаться поверх нижнего меню из-за CSS/кэша WebView.
      layer.remove();
      panel.classList.remove("tab-panel--loading");
      panel.setAttribute("aria-busy", "false");
    }
    if (message) {
      if (!layer) {
        layer = document.createElement("div");
        layer.className = "tab-loading-state";
        panel.prepend(layer);
      }
      layer.hidden = false;
      layer.innerHTML = `<div class="tab-loading-state__error">${escapeHtml(message)}</div>`;
      panel.classList.remove("tab-panel--loading");
      panel.setAttribute("aria-busy", "false");
    }
  }

  async function loadBootstrapSecondary(section) {
    const key = section || "profile";
    if (secondaryLoaded.has(key)) return state;
    if (secondaryPromises.has(key)) return secondaryPromises.get(key);

    setTabLoading(key, true);
    const promise = (async () => {
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
          } else if (key === "rating") {
            state.leaderboard = data.leaderboard || [];
            renderRating();
          } else if (key === "calendar") {
            state.calendar_events = data.calendar_events || [];
            renderCalendar();
          }
          secondaryLoaded.add(key);
          setTabLoading(key, false);
        }
      } catch (err) {
        console.error(`bootstrap-secondary(${key}) failed:`, err);
        setTabLoading(key, false, friendlyError(err) || "Не удалось загрузить раздел");
        showToast(friendlyError(err) || "Не удалось загрузить раздел", "error");
      }
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
    document.getElementById("shareAchievementBtn")?.addEventListener("click", async () => {
      const days = Number(state?.streak?.days || 0);
      const status = state?.streak?.temp_status || "Ударный режим";
      const overlay = document.getElementById("achievementShareOverlay");
      const daysEl = document.getElementById("achievementShareDays");
      const statusEl = document.getElementById("achievementShareStatus");
      if (daysEl) daysEl.textContent = formatDays(days);
      if (statusEl) statusEl.textContent = status;
      if (overlay) {
        overlay.hidden = false;
        requestAnimationFrame(() => overlay.classList.add("show"));
        overlay.setAttribute("aria-hidden", "false");
      }
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
  function showToast(message, kind, duration) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.className = "toast is-visible" + (kind ? " is-" + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.classList.remove("is-visible"); }, duration || 2200);
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
  function renderHabits() {
    const list = document.getElementById("habitList");
    const habits = state.habits;
    const done = habits.filter(h => h.completed).length;
    const progressLabel = document.getElementById("habitsProgressLabel");
    if (progressLabel) {
      progressLabel.textContent = `${done}/${habits.length}`;
    }

    if (habits.length === 0) {
      list.innerHTML = `<li class="empty-hint">Пока нет привычек — добавь первую ниже 👇</li>`;
      return;
    }

    list.innerHTML = habits.map(h => `
      <li class="habit-item ${h.completed ? "is-done" : ""}" data-id="${h.id}">
        <button class="habit-item__check" data-action="complete" ${h.completed ? "disabled" : ""}>${h.completed ? "✓" : ""}</button>
        <span class="habit-item__title">${escapeHtml(h.title)}</span>
        <button class="habit-item__del" data-action="delete" aria-label="Удалить">✕</button>
      </li>
    `).join("");
  }

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
      const isStars = it.item_type === "frame_stars" || it.item_type === "answer_pack_stars";
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
        <span class="achievement-item__icon">🏆</span>
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
          ${status ? `<small class="rating-item__status">${escapeHtml(status)}</small>` : ""}
        </span>
        <span class="rating-item__meta"><span class="rating-stat"><span class="material-symbols-rounded stat-icon">local_fire_department</span>${Number(r.streak || 0)}</span><span class="rating-stat">${ADAM_COIN_ICON}${Number(r.xp || 0)}</span></span>
      </li>`;
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

// ===================== HABIT ACTIONS =====================
function initHabitActions() {
  const habitList = document.getElementById("habitList");
  const addHabitForm = document.getElementById("addHabitForm");
  if (!habitList || !addHabitForm) return;

  habitList.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const li = btn.closest(".habit-item");
    if (!li) return;
    const habitId = li.dataset.id;
    const action = btn.dataset.action;
    try {
      if (action === "complete") {
        btn.disabled = true;
        const result = await api(`/api/habits/${habitId}/complete`, { method: "POST" });
        haptic("medium");
        await loadBootstrap();
        const coinText = `+${result.coins || 10} Adam Coin` + (result.doubled ? " ⚡️×2" : "");
        showToast(coinText, "praise");
        if (result.streak_event) {
          pendingBonusIntro = !!result.show_bonus_intro;
          openStreakCelebration(result.streak_event);
        }
        // Пром 8 (доп.): "идеальный день" и, раз в месяц, награда за
        // идеальный месяц — показываем следом за тостом монет, со сдвигом,
        // чтобы не перекрывать друг друга в одном #toast элементе.
        if (result.perfect_day_message) {
          setTimeout(() => showToast(result.perfect_day_message, "praise", 4200), 2400);
        }
        if (result.month_end_reward?.message) {
          setTimeout(() => showToast(result.month_end_reward.message, "praise", 5500),
            result.perfect_day_message ? 6800 : 2400);
        }
      } else if (action === "delete") {
        if (!confirm("Удалить эту привычку?")) return;
        await api(`/api/habits/${habitId}`, { method: "DELETE" });
        await loadBootstrap();
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
      await loadBootstrap();
    }
  });

  addHabitForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("newHabitInput");
    const submitBtn = addHabitForm.querySelector('button[type="submit"]');
    const title = input.value.trim();
    if (title.length < 2) {
      showToast("Название слишком короткое", "error");
      input.focus();
      return;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
      const result = await api("/api/habits", {
        method: "POST",
        body: JSON.stringify({ title })
      });

      // Сразу показываем созданную привычку, не заставляя интерфейс ждать
      // повторной загрузки всего bootstrap-состояния.
      if (result && result.habit && state) {
        state.habits = [result.habit, ...(state.habits || [])];
        renderHabits();
      }

      input.value = "";
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
      } catch (err) {
        showToast(friendlyError(err), "error");
      }
    });
  }

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
        habit_limit: "Можно добавить не больше 7 привычек",
        daily_limit_reached: "Этот пакет уже куплен сегодня — доступен снова завтра",
        habit_add_locked: "Сегодня уже была отметка и удаление привычки — добавление новых открыто с 00:00",
        invalid_init_data: "Telegram не передал данные авторизации. Закройте Mini App и откройте его снова."
    };

    return map[code] || (err && err.message) || "Неизвестная ошибка";
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
  } catch (err) {
    console.error("loadProgressStats failed:", err);
  }
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
}

async function boot() {
    try {
        initTelegram();
        initTabs();
        initHabitActions();
        initPlanActions();
        initShopActions();
        initProfileAvatarActions();
        initThemeActions();
        initStreakUI();
        initStreakPopupClick();
        initProgressActions();
        // ВАЖНО: настройки используют state.settings, поэтому их нельзя
        // инициализировать до первого bootstrap. Иначе boot() падал на
        // state === null, а навигация и вторичные вкладки не запускались.
        // Критический экран готов сразу после bootstrap. Часовой пояс не должен
        // удерживать loading-overlay и мешать первому paint (особенно в Telegram WebView).
        await loadBootstrap();
        initSettingsActions();
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
        showToast(friendlyError(err) || "Не удалось загрузить данные", "error");
    } finally {
        const overlay = document.getElementById("loadingOverlay");
        if (overlay) overlay.hidden = true;
    }
}

document.addEventListener("DOMContentLoaded", boot);

document.getElementById("aiCoachBtn").addEventListener("click", () => {
    haptic("light");
    const overlay = document.getElementById("loadingOverlay");
    if (overlay) overlay.hidden = false;
    // небольшая пауза, чтобы браузер успел отрисовать монетку до ухода со страницы
    setTimeout(() => { window.location.href = "/coach"; }, 60);
});

})();