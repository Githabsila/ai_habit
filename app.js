(() => {
  "use strict";

  const tg = window.Telegram ? window.Telegram.WebApp : null;
  const RING_CIRCUMFERENCE = 326.7; // 2 * PI * 52

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

  // ===================== ЕЖЕДНЕВНЫЙ «УДАРНЫЙ ДЕНЬ» =====================
  // Показываем специальное окно один раз в день на пользователя.
  // Дата хранится локально, поэтому повторные открытия Mini App в тот же день
  // не раздражают пользователя. На следующий календарный день окно появляется снова.
  let impactDayTimer = null;

  function impactDayStorageKey() {
    const userId = state?.user?.telegram_id || "guest";
    return `adam_impact_day_seen_${userId}`;
  }

  function getLocalDayKey() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function openImpactDayPopup() {
    const overlay = document.getElementById("impactDayOverlay");
    if (!overlay || !state?.user) return;

    const habits = state.habits || [];
    const done = habits.filter(h => h.completed).length;
    const streak = Number(state.user.streak || 0);
    const streakEl = document.getElementById("impactDayStreak");
    const progressEl = document.getElementById("impactDayProgress");
    const messageEl = document.getElementById("impactDayMessage");

    if (streakEl) streakEl.textContent = streak;
    if (progressEl) progressEl.textContent = `${done}/${habits.length}`;
    if (messageEl) {
      if (habits.length === 0) {
        messageEl.textContent = "Добавь первую привычку и сделай сегодняшний день первым в своей серии.";
      } else if (done === habits.length) {
        messageEl.textContent = "Все привычки уже закрыты. Отличное начало — продолжай в том же духе!";
      } else if (streak > 0) {
        messageEl.textContent = `Твоя серия — ${streak} дн. Не дай ей прерваться сегодня.`;
      } else {
        messageEl.textContent = "Начни с одного простого действия — именно так появляется новая серия.";
      }
    }

    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    requestAnimationFrame(() => overlay.classList.add("show"));
    haptic("medium");
  }

  function closeImpactDayPopup() {
    const overlay = document.getElementById("impactDayOverlay");
    if (!overlay) return;
    overlay.classList.remove("show");
    overlay.setAttribute("aria-hidden", "true");
    clearTimeout(impactDayTimer);
    impactDayTimer = setTimeout(() => { overlay.hidden = true; }, 250);
  }

  function markImpactDaySeen() {
    try { localStorage.setItem(impactDayStorageKey(), getLocalDayKey()); } catch (e) {}
  }

  function scheduleImpactDayPopup() {
    if (!state?.user) return;
    const today = getLocalDayKey();
    let seen = null;
    try { seen = localStorage.getItem(impactDayStorageKey()); } catch (e) {}
    if (seen === today) return;

    // Небольшая задержка после загрузки, чтобы пользователь успел увидеть главный экран.
    clearTimeout(impactDayTimer);
    impactDayTimer = setTimeout(() => {
      openImpactDayPopup();
      markImpactDaySeen();
    }, 650);
  }

  function initImpactDayPopup() {
    const overlay = document.getElementById("impactDayOverlay");
    if (!overlay) return;

    const close = () => closeImpactDayPopup();
    document.getElementById("impactDayClose")?.addEventListener("click", close);
    document.getElementById("impactDayLater")?.addEventListener("click", close);
    document.getElementById("impactDayStart")?.addEventListener("click", () => {
      haptic("light");
      closeImpactDayPopup();
      document.querySelector('[data-tab="home"]')?.scrollIntoView({ behavior: "smooth", block: "start" });
      document.querySelector('[data-tab="home"]')?.classList.add("impactday-highlight");
      setTimeout(() => document.querySelector('[data-tab="home"]')?.classList.remove("impactday-highlight"), 900);
    });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !overlay.hidden) close();
    });
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

  function cosmeticEmoji(id) {
    return id === "adam" ? "🤖" : "A";
  }

  // ===================== RENDER: PLAYER CARD =====================
  function renderPlayerCard() {
    const u = state.user;
    document.getElementById("playerName").textContent = u.first_name || "Игрок";
    const avatar = document.getElementById("adamAvatar");
    const avatarWrap = document.getElementById("adamAvatarWrap");
    if (avatar) avatar.textContent = cosmeticEmoji(u.avatar_id);
    if (avatarWrap) avatarWrap.dataset.frame = u.frame_id || "default";
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
        <span class="habit-item__title">${escapeHtml(h.title)}${h.planned_time ? `<small class="habit-item__time">⏰ ${escapeHtml(h.planned_time)}</small>` : ""}</span>
        <button class="habit-item__del" data-action="delete" aria-label="Удалить">✕</button>
      </li>
    `).join("");
  }

  // ===================== RENDER: SHOP =====================
  function renderShop() {
    const list = document.getElementById("shopList");
    const items = state.shop_items || [];
    if (items.length === 0) {
      list.innerHTML = `<li class="empty-hint">Магазин пока пуст</li>`;
      return;
    }

    const quota = state.user.ai_quota || {pro: !!state.user.premium, remaining: 0, limit: 0, bonus: 0};
    const packs = items.filter(it => it.item_type === "answer_pack");
    const cosmetics = items.filter(it => ["premium", "avatar", "frame", "theme", "badge"].includes(it.item_type));

    const section = (title, subtitle, arr, extraClass = "") => `
      <li class="shop-section-title ${extraClass}">
        <div class="shop-section-title__main">
          <strong>${title}</strong>
          <span>${subtitle}</span>
        </div>
        ${arr.length ? `<span class="shop-section-title__count">${arr.length}</span>` : ""}
      </li>
      <li class="shop-grid ${extraClass}">
      ${arr.map(it => {
        const canAfford = state.user.xp >= it.price;
        const isCosmetic = it.item_type === "avatar" || it.item_type === "frame";
        const equipped = isCosmetic && ((it.item_type === "avatar" && state.user.avatar_id === it.payload) || (it.item_type === "frame" && state.user.frame_id === it.payload));
        let btnLabel = `<span class="shop-buy-price"><span class="material-symbols-rounded stat-icon">diamond</span>${it.price}</span>`;
        let btnClass = "buy-btn";
        let disabled = "";
        let action = "buy";
        if (equipped) {
          btnLabel = "Надето";
          btnClass += " is-owned is-equipped";
          disabled = "disabled";
          action = "none";
        } else if (it.owned && isCosmetic) {
          btnLabel = "Надеть";
          btnClass += " is-owned";
          action = "equip";
        } else if (it.owned && !it.repeatable) {
          btnLabel = "Куплено";
          btnClass += " is-owned";
          disabled = "disabled";
          action = "none";
        } else if (!canAfford) {
          disabled = "disabled";
        }
        return `
          <li class="shop-item ${isCosmetic ? 'shop-item--cosmetic' : ''}" data-id="${it.id}">
            <div class="shop-item__body">
              <div class="shop-item__name">${escapeHtml(it.name)}</div>
              <div class="shop-item__desc">${escapeHtml(it.description || "")}</div>
              ${it.item_type === 'answer_pack' ? `<div class="shop-item__meta">Добавит ${escapeHtml(it.payload)} ответов к сегодняшнему лимиту</div>` : ''}
              ${isCosmetic ? `<div class="shop-item__meta">${equipped ? 'Сейчас используется' : (it.owned ? 'Куплено — можно надеть' : 'После покупки можно надеть')}</div>` : ''}
              <div class="shop-item__price"><span class="material-symbols-rounded stat-icon">diamond</span><span>${it.price}</span></div>
            </div>
            <button class="${btnClass}" data-action="${action}" ${disabled}>${btnLabel}</button>
          </li>`;
      }).join('')}
      </li>`;

    const modeTitle = quota.pro ? "ADAM PRO" : "ADAM Standard";
    const modeSubtitle = quota.pro
      ? "Расширенный режим активен"
      : "Твой персональный режим ADAM";
    const modeClass = quota.pro ? "is-pro" : "is-standard";

    list.innerHTML = `
      <li class="shop-quota-card ${modeClass}">
        <div class="shop-quota-card__icon">
          <span class="material-symbols-rounded">smart_toy</span>
        </div>
        <div class="shop-quota-card__identity">
          <div class="shop-quota-card__eyebrow">ТВОЙ ADAM</div>
          <div class="shop-quota-card__title">${modeTitle}</div>
          <div class="shop-quota-card__sub">${modeSubtitle}</div>
        </div>
        <div class="shop-quota-card__quota">
          <strong>${quota.remaining}</strong>
          <span>ответов сегодня</span>
        </div>
      </li>
      ${section('💬 Ответы ADAM', 'Пополни лимит, если хочешь больше ответов сегодня', packs, 'shop-section--packs')}
      ${section('✨ PRO и персонализация', 'Открывай новые возможности и меняй стиль ADAM', cosmetics, 'shop-section--cosmetics')}
    `;
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
    document.body.setAttribute("data-theme", current);

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

    // Backend отдаёт достижения от новых к старым, поэтому первые 3 — самые свежие.
    const latest = items.slice(0, 3);
    const older = items.slice(3);

    latestList.innerHTML = latest.map(renderAchievementItem).join("");

    if (archive && archiveList) {
      archive.hidden = older.length === 0;
      archiveList.innerHTML = older.map(renderAchievementItem).join("");
    }
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
          <span class="rating-item__name">${escapeHtml(name)}${r.badge ? " 🏅" : ""}${isMe ? " (ты)" : ""}</span>
          <span class="rating-item__meta">
    <span class="rating-stat">
        <span class="material-symbols-rounded stat-icon">local_fire_department</span>
        ${r.streak}
    </span>

    <span class="rating-stat">
        <span class="material-symbols-rounded stat-icon">diamond</span>
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

  // Собирает актуальный список задач: значения двух быстрых полей (2 и 3
  // задача) + все задачи за пределами этих двух (index 2+), которые попали
  // в план через форму «Добавить новую задачу» и не имеют своего поля.
  function collectPlanTasks(extraText) {
    const quickTasks = [...document.querySelectorAll(".plan-task-input")]
      .map(i => i.value.trim());
    const plan = state.daily_plan || { tasks: [] };
    const restTasks = plan.tasks.slice(2).map(t => t.text);
    const tasks = [...quickTasks, ...restTasks];
    if (extraText) tasks.push(extraText);
    return tasks.map(t => t.trim()).filter(Boolean);
  }

  document.getElementById("savePlanBtn").addEventListener("click", async () => {
    const main_goal = document.getElementById("mainGoalInput").value.trim();
    const tasks = collectPlanTasks();

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

  document.getElementById("addPlanTaskForm").addEventListener("submit", async (e) => {
    e.preventDefault();

    const input = document.getElementById("newPlanTaskInput");
    const text = input.value.trim();
    if (!text) return;

    const plan = state.daily_plan || { main_goal: "" };
    const main_goal = document.getElementById("mainGoalInput").value.trim() || plan.main_goal || "";
    const tasks = collectPlanTasks(text);

    try {
      await api("/api/plan/save", {
        method: "POST",
        body: JSON.stringify({ main_goal, tasks })
      });
      input.value = "";
      haptic("light");
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
      const btn = e.target.closest("button[data-action]");
      if (!btn || btn.disabled || btn.dataset.action === "none") return;
      const li = btn.closest(".shop-item");
      const itemId = li.dataset.id;
      try {
        btn.disabled = true;
        if (btn.dataset.action === "equip") {
          await api("/api/cosmetics/equip", { method: "POST", body: JSON.stringify({ item_id: Number(itemId) }) });
          haptic("light");
          showToast("✨ Предмет надет", "success");
        } else {
          await api(`/api/buy/${itemId}`, { method: "POST" });
          haptic("medium");
          showToast("Покупка совершена!", "success");
        }
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
        initImpactDayPopup();
        await loadBootstrap();
    } catch (err) {
        console.error("boot() failed:", err);
        showToast(friendlyError(err) || "Не удалось загрузить данные", "error");
    } finally {
        const overlay = document.getElementById("loadingOverlay");
        if (overlay) overlay.hidden = true;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    boot();

    const aiCoachBtn = document.getElementById("aiCoachBtn");
    if (aiCoachBtn) {
        aiCoachBtn.addEventListener("click", () => {
            haptic("light");
            const overlay = document.getElementById("loadingOverlay");
            if (overlay) overlay.hidden = false;
            setTimeout(() => {
                window.location.href = "/coach";
            }, 60);
        });
    }
});

})();