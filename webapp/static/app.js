(() => {
  "use strict";

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  const RING_CIRCUMFERENCE = 326.7; // 2 * PI * 52

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

  // ===================== TOAST =====================
  let toastTimer = null;
  function showToast(message, kind) {
    const el = document.getElementById("toast");
    el.textContent = message;
    el.className = "toast is-visible" + (kind ? " is-" + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.classList.remove("is-visible"); }, 2200);
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

    const xpIntoLevel = u.xp % 100;
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
    const items = state.shop_items;
    if (items.length === 0) {
      list.innerHTML = `<li class="empty-hint">Магазин пока пуст</li>`;
      return;
    }
    list.innerHTML = items.map(it => {
      const canAfford = state.user.xp >= it.price;
      let btnLabel = `
<span style="display:flex;align-items:center;gap:4px;justify-content:center">
    ${ADAM_COIN_ICON}
    ${it.price}
</span>`;
      let btnClass = "buy-btn";
      let disabled = "";
      if (it.owned) {
        btnLabel = "Куплено";
        btnClass += " is-owned";
        disabled = "disabled";
      } else if (!canAfford) {
        disabled = "disabled";
      }
      return `
        <li class="shop-item" data-id="${it.id}">
          <div class="shop-item__body">
            <div class="shop-item__name">${escapeHtml(it.name)}</div>
            <div class="shop-item__desc">${escapeHtml(it.description || "")}</div>
            <div class="shop-item__price">
    ${ADAM_COIN_ICON}
    <span>${it.price}</span>
</div>
          </div>
          <button class="${btnClass}" data-action="buy" ${disabled}>${btnLabel}</button>
        </li>
      `;
    }).join("");
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
    const rows = state.leaderboard;
    if (rows.length === 0) {
      list.innerHTML = `<li class="empty-hint">Рейтинг пока пуст</li>`;
      return;
    }
    const myId = state.user.telegram_id;
    list.innerHTML = rows.map((r, i) => {
      const rank = i + 1;
      const isMe = r.telegram_id === myId;
      const name = r.first_name || r.username || "Игрок";
      return `
        <li class="rating-item ${isMe ? "is-me" : ""} rank-${rank}">
          <span class="rating-item__rank">${rank}</span>
          <span class="rating-item__name">${escapeHtml(name)}${r.badge ? '<span class="rating-item__badge">🏅</span>' : ""}${isMe ? " (ты)" : ""}</span>
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
    const byDay = {};
    (state.calendar_events || []).forEach(ev => { byDay[ev.day] = ev.completed; });

    const days = [];
    const today = new Date();
    for (let i = 34; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      days.push({ key, count: byDay[key] || 0, dayNum: d.getDate() });
    }

    grid.innerHTML = days.map(d => {
      const level = d.count === 0 ? 0 : d.count <= 1 ? 1 : d.count <= 3 ? 2 : 3;
      return `<div class="cal-cell cal-cell--${level}" title="${d.key}: ${d.count}"></div>`;
    }).join("");
  }



// ===================== DAILY PLAN =====================
function renderPlan() {
  const plan = state.daily_plan || { main_goal: "", tasks: [] };
  const list = document.getElementById("planList");

  document.getElementById("mainGoalInput").value = plan.main_goal || "";

  const inputs = document.querySelectorAll(".plan-task-input");
  inputs.forEach((i, idx) => {
    i.value = plan.tasks[idx] ? plan.tasks[idx].text : "";
  });

  const done = plan.tasks.filter(t => t.completed).length;
  document.getElementById("planProgressLabel").textContent = `${done}/${plan.tasks.length}`;

  list.innerHTML = plan.tasks.map(t => `
    <li class="plan-item ${t.completed ? "is-done" : ""}">
      <input type="checkbox" class="plan-toggle" data-id="${t.id}" ${t.completed ? "checked" : ""}>
      <span class="plan-item__text">${escapeHtml(t.text)}</span>
    </li>
  `).join("");
}


  function renderAll() {
    renderPlayerCard();
    renderPlan();
    renderHabits();
    renderShop();
    renderThemePicker();
    renderAchievements();
    renderRating();
    renderCalendar();
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  // ===================== TABS =====================
  function initTabs() {
    document.getElementById("tabBar").addEventListener("click", (e) => {
      const btn = e.target.closest(".tab-bar__item");
      if (!btn) return;
      const tab = btn.dataset.tab;
      if (!tab) return; // кнопки без data-tab (например ИИ) не переключают панели
      document.querySelectorAll(".tab-bar__item").forEach(b => b.classList.toggle("is-active", b === btn));
      document.querySelectorAll(".tab-panel").forEach(p => { p.hidden = p.dataset.tab !== tab; });
      haptic("light");
    });
  }

  // ===================== HABIT ACTIONS =====================
  function initHabitActions() {
    document.getElementById("habitList").addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const li = btn.closest(".habit-item");
      const habitId = li.dataset.id;
      const action = btn.dataset.action;

      try {
        if (action === "complete") {
          btn.disabled = true;
          await api(`/api/habits/${habitId}/complete`, { method: "POST" });
          haptic("medium");
          await loadBootstrap();
          showToast("+10 Adam Coin", "success");
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

 document.getElementById("addHabitForm").addEventListener("submit", async (e) => {
    e.preventDefault();

    const input = document.getElementById("newHabitInput");
    const title = input.value.trim();

    if (title.length < 2) {
        showToast("Название слишком короткое", "error");
        return;
    }

    try {
        await api("/api/habits", {
            method: "POST",
            body: JSON.stringify({ title })
        });

        input.value = "";
        haptic("light");
        await loadBootstrap();

    } catch (err) {
        showToast(friendlyError(err), "error");
    }
});

  document.getElementById("savePlanBtn").addEventListener("click", async () => {
    const main_goal = document.getElementById("mainGoalInput").value.trim();
    const tasks = [...document.querySelectorAll(".plan-task-input")]
      .map(i => i.value.trim())
      .filter(Boolean);

    try {
      await api("/api/plan/save", {
        method: "POST",
        body: JSON.stringify({ main_goal, tasks })
      });
      showToast("План сохранён");
      await loadBootstrap();
    } catch (err) {
      showToast(friendlyError(err), "error");
    }
  });

  document.addEventListener("change", async (e) => {
    if (e.target.classList.contains("plan-toggle")) {
      try {
        await api("/api/plan/task/toggle", {
          method: "POST",
          body: JSON.stringify({ task_id: e.target.dataset.id })
        });
        await loadBootstrap();
      } catch (err) {
        showToast(friendlyError(err), "error");
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
        initShopActions();
        initThemeActions();
        await loadBootstrap();
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