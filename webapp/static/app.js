(() => {
  "use strict";

  const tg = window.Telegram ? window.Telegram.WebApp : null;
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

  // ===================== API =====================
  async function api(path, options = {}) {


    const res = await fetch(path, {
      ...options,
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
  }

  async function loadBootstrap() {
    state = await api("/api/bootstrap");
    const newLevel = state.user.level;
    if (knownLevel !== null && newLevel > knownLevel) {
      showLevelUp(newLevel);
    }
    knownLevel = newLevel;
    renderAll();
  }

  // ===================== RENDER ALL =====================
  function renderAll() {
    renderPlayerCard();
    renderHabits();
    renderShop();
    renderThemePicker();
    renderAchievements();
    renderRating();
    renderCalendar();
    renderPlan();
    renderStreak();
    maybeShowStreakOnboarding();
    maybeShowWeeklyBonus();
  }

  // ===================== УДАРНЫЙ РЕЖИМ =====================
  let streakCelebrationTimer = null;

  function renderStreak() {
    const streak = state?.streak;
    if (!streak) return;
    const daysEl = document.getElementById("streakWidgetDays");
    if (daysEl) daysEl.textContent = formatDays(streak.days || 0);

    const days = document.getElementById("streakDays");
    if (days) {
      days.innerHTML = (streak.last7 || []).map((d) => {
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
    if (balance) balance.textContent = `Заморозок: ${streak.freeze_balance || 0}/2`;
    const buy = document.getElementById("freezeBuyBtn");
    if (buy) buy.disabled = (streak.freeze_balance || 0) >= 2 || (streak.freeze_purchased_count || 0) >= 2;

    const status = document.getElementById("streakStatusLabel");
    if (status) {
      status.textContent = streak.days > 0
        ? `Огонь горит. Не дай ему погаснуть.`
        : `Серия сброшена. Сегодня можно начать заново.`;
    }

    const profileStatus = document.getElementById("profileStreakStatus");
    const profileFrame = document.getElementById("profileStreakFrame");
    let streakStatus = streak.temp_status || "";
    if (/^Огонь\s+/i.test(streakStatus)) {
      streakStatus = streakStatus.replace(/^Огонь/i, "В ударе");
    }
    if (profileStatus) profileStatus.textContent = streakStatus || (streak.days ? `В ударе ${streak.days} дн.` : "Серия не начата");
    if (profileFrame) {
      const reward = (streak.rewards || [])[0];
      profileFrame.textContent = reward ? `🏆 ${reward.frame}` : "🔥 Твоя ударная серия!";
      profileFrame.className = "streak-profile-frame frame-" + (streak.temp_frame || "none");
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
    try {
      if (navigator.vibrate) navigator.vibrate([35, 25, 55]);
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) {
        const ctx = new Ctx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(520, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(820, ctx.currentTime + 0.12);
        gain.gain.setValueAtTime(0.0001, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(); osc.stop(ctx.currentTime + 0.2);
        osc.onended = () => ctx.close();
      }
    } catch (_) {}
    const fire = document.getElementById("streakCelebrationFire");
    fire?.classList.remove("ignite");
    requestAnimationFrame(() => fire?.classList.add("ignite"));
    clearTimeout(streakCelebrationTimer);
    streakCelebrationTimer = setTimeout(closeStreakCelebration, 4200);
  }

  function closeStreakCelebration() {
    const overlay = document.getElementById("streakCelebrationOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    clearTimeout(streakCelebrationTimer);
    setTimeout(() => { overlay.hidden = true; }, 350);
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
    document.getElementById("streakCelebrationContinue")?.addEventListener("click", closeStreakCelebration);
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
    document.getElementById("freezeBuyBtn")?.addEventListener("click", async () => {
      try {
        const res = await api("/api/streak/freeze/buy", {method: "POST"});
        haptic("light");
        showToast("❄️ Заморозка куплена", "success");
        await loadBootstrap();
      } catch (e) {
        const map = {
          weekly_limit: "Лимит 2 заморозки на неделю уже достигнут",
          max_balance: "У тебя уже максимум 2 заморозки",
          not_enough_coins: "Нужно 200 Adam Coin",
        };
        showToast(map[e?.data?.error] || friendlyError(e), "error");
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
    document.addEventListener("click", (e) => {
      const overlay = document.getElementById("streakCelebrationOverlay");
      if (overlay && e.target === overlay) closeStreakCelebration();
    });
  }

  // ===================== TOAST =====================
  let toastTimer = null;
  function showToast(message, kind, duration) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.className = "toast is-visible" + (kind ? " is-" + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.classList.remove("is-visible"); }, duration || 2200);
  }

  // ===================== RENDER: PLAYER CARD =====================
  function renderPlayerCard() {
    const u = state.user;
    document.getElementById("playerName").textContent = u.first_name || "Игрок";
    document.getElementById("levelNumber").textContent = u.level;
    document.getElementById("streakValue").textContent = u.streak;
    document.getElementById("coinValue").textContent = u.xp;

    const badgeEl = document.getElementById("playerBadge");
    if (badgeEl) badgeEl.style.display = u.badge ? "inline" : "none";

    const xpIntoLevel = (u.total_xp ?? u.xp) % 100;
    document.getElementById("xpLabel").textContent = `${xpIntoLevel} / 100 XP`;
    document.getElementById("xpBarFill").style.width = xpIntoLevel + "%";

    const ringFill = document.getElementById("levelRingFill");
    const offset = RING_CIRCUMFERENCE * (1 - xpIntoLevel / 100);
    ringFill.style.strokeDashoffset = offset;
  }

  // ===================== RENDER: HABITS =====================
  function renderHabits() {
    const list = document.getElementById("habitList");
    const habits = state.habits;
    const done = habits.filter(h => h.completed).length;
    document.getElementById("habitsProgressLabel").textContent = `${done}/${habits.length} сегодня`;

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

    // Сначала показываем лоты ответов — это основная покупка магазина.
    const answerItems = items.filter(it => it.item_type === "answer_pack" || /ответ/i.test(it.name || ""));
    const otherItems = items.filter(it => !answerItems.includes(it));

    const renderItem = (it) => {
      const canAfford = Number(state.user?.xp || 0) >= Number(it.price || 0);
      const isAnswer = it.item_type === "answer_pack" || /ответ/i.test(it.name || "");
      const amountMatch = String(it.payload || it.name || "").match(/(\d+)/);
      const amount = amountMatch ? amountMatch[1] : "";

      let title = escapeHtml(it.name || "Товар ADAM");
      let desc = escapeHtml(it.description || "");

      if (isAnswer) {
        title = amount ? `+${amount} ответов` : title;
        desc = amount ? `Ещё ${amount} запросов к ADAM сегодня` : desc;
      }

      // Цена всегда показывается слева в одном месте. Если денег хватает,
      // сама кнопка становится жёлтой и содержит только «Купить» — без
      // повторения цены/«100 A» внутри кнопки.
      let btnLabel = "Купить";
      let btnClass = "buy-btn";
      let disabled = "";

      if (it.owned && !isAnswer) {
        btnLabel = "✓ Куплено";
        btnClass += " is-owned";
        disabled = "disabled";
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
            <div class="shop-item__price">
              ${ADAM_COIN_ICON}
              <span>${Number(it.price || 0).toLocaleString("ru-RU")}</span>
            </div>
            <button class="${btnClass}" data-action="buy" ${disabled}>${btnLabel}</button>
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
  function renderAchievements() {
    const list = document.getElementById("achievementList");
    const items = state.achievements;
    document.getElementById("achievementsCountLabel").textContent = `${items.length} наград`;
    if (items.length === 0) {
      list.innerHTML = `<li class="empty-hint">Пока нет достижений — выполняй привычки, чтобы открыть первое 🏆</li>`;
      return;
    }
    list.innerHTML = items.map(a => `
      <li class="achievement-item">
        <span class="achievement-item__icon">🏆</span>
        <div>
          <div class="achievement-item__title">${escapeHtml(a.title)}</div>
          <div class="achievement-item__desc">${escapeHtml(a.description || "")}</div>
        </div>
      </li>
    `).join("");
  }

  // ===================== RENDER: RATING =====================
  function renderRating() {
    const list = document.getElementById("ratingList");
    if (!list) return;
    let rows = Array.isArray(state.leaderboard) ? state.leaderboard.slice() : [];
    // Если сервер временно вернул пустой рейтинг, всё равно показываем текущего пользователя.
    if (rows.length === 0 && state.user) {
      rows = [{
        telegram_id: state.user.telegram_id,
        username: "",
        first_name: state.user.first_name || "Игрок",
        xp: state.user.xp || 0,
        level: state.user.level || 1,
        streak: state.user.streak || 0,
        badge: !!state.user.badge
      }];
    }
    if (rows.length === 0) {
      list.innerHTML = `<li class="empty-hint">Рейтинг пока пуст</li>`;
      return;
    }
    const myId = state.user.telegram_id;
    list.innerHTML = rows.map((r, i) => {
      const rank = i + 1;
      const isMe = r.telegram_id === myId;
      const name = r.first_name || r.username || "Игрок";
      const ss = r.streak_status || {};
      const reward = (ss.rewards || [])[0];
      const frame = ss.temp_frame || "none";
      let status = ss.temp_status || (reward ? reward.status : "");
      if (/^Огонь\s+/i.test(status)) status = status.replace(/^Огонь/i, "В ударе");
      return `
        <li class="rating-item ${isMe ? "is-me" : ""} rank-${rank}">
          <span class="rating-item__rank">${rank}</span>
          <span class="rating-avatar frame-${escapeHtml(frame)}">${escapeHtml((name[0] || "A").toUpperCase())}</span>
          <span class="rating-item__name">
            <span class="rating-item__name-line"><span class="rating-item__name-text">${escapeHtml(name)}</span>${r.badge ? '<span class="rating-item__badge">🏅</span>' : ""}${isMe ? ' <span class="rating-item__me">(ты)</span>' : ""}</span>
            ${status ? `<small class="rating-item__status">${escapeHtml(status)}</small>` : ""}
          </span>
          <span class="rating-item__meta">
    <span class="rating-stat">
        <span class="material-symbols-rounded stat-icon">local_fire_department</span>
        ${r.streak}
    </span>

    <span class="rating-stat">
        ${ADAM_COIN_ICON}
        ${r.xp}
    </span>
</span>
        </li>
      `;
    }).join("");
  }

  // ===================== RENDER: CALENDAR =====================
  function renderCalendar() {
    const grid = document.getElementById("calendarGrid");
    if (!grid) return;
    const byDay = {};
    (state.calendar_events || []).forEach(ev => { byDay[ev.day] = { completed: ev.completed, total: ev.total }; });

    const days = [];
    const today = new Date();
    for (let i = 34; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      const info = byDay[key] || { completed: 0, total: 0 };
      days.push({ key, ...info, dayNum: d.getDate() });
    }

    grid.innerHTML = days.map(d => {
      // Красим по ДОЛЕ выполненных привычек за день, а не по абсолютному
      // числу — иначе при малом количестве привычек клетка никогда не
      // становилась полностью золотой, даже если всё было выполнено.
      let level = 0;
      if (d.completed > 0 && d.total > 0) {
        const ratio = d.completed / d.total;
        level = ratio >= 1 ? 3 : ratio >= 0.5 ? 2 : 1;
      }
      const label = d.total > 0 ? `${d.key}: ${d.completed}/${d.total}` : `${d.key}: 0`;
      return `<div class="cal-cell cal-cell--${level}" title="${label}"></div>`;
    }).join("");
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
    document.querySelectorAll(".tab-panel").forEach(panel => { panel.hidden = panel.dataset.tab !== tab; });
    haptic("light");
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
        showToast("+10 Adam Coin", "success");
        if (result.streak_event) {
          openStreakCelebration(result.streak_event);
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
    const title = input.value.trim();
    if (title.length < 2) {
      showToast("Название слишком короткое", "error");
      return;
    }
    try {
      const result = await api("/api/habits", { method: "POST", body: JSON.stringify({ title }) });
      input.value = "";
      haptic("light");
      await loadBootstrap();
      if (result.first_habit) {
        maybeShowStreakOnboarding();
      }
    } catch (err) {
      showToast(friendlyError(err), "error");
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
        if (res && res.message) showToast(res.message, "success", 4500);
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
        if (res && res.message) showToast(res.message, "success", 4500);
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
    document.getElementById("shopList").addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action=buy]");
      if (!btn || btn.disabled) return;
      const li = btn.closest(".shop-item");
      const itemId = li.dataset.id;
      try {
        btn.disabled = true;
        await api(`/api/buy/${itemId}`, { method: "POST" });
        haptic("medium");
        showToast("Покупка совершена!", "success");
        await loadBootstrap();
      } catch (err) {
        showToast(friendlyError(err), "error");
        await loadBootstrap();
      }
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
        already_completed: "Уже выполнено сегодня",
        not_enough_xp_or_not_found: "Не хватает Adam Coin",
        not_found: "Не найдено",
        banned: "Доступ ограничен",
        theme_not_owned: "Сначала купи «Тема оформления» в магазине",
        invalid_theme: "Такой темы не существует",
        task_limit: "Можно добавить не больше 5 задач",
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
async function boot() {
    try {
        initTelegram();
        initTabs();
        initHabitActions();
        initPlanActions();
        initShopActions();
        initThemeActions();
        initStreakUI();
        initStreakPopupClick();
        await loadBootstrap();
        await syncTimezone();
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