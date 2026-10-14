/* Раздел «Аналитика» (страница /analytics, данные — /api/analytics/*, считает db/stats.py, карта функций — webapp/feature_map.py).
   Всё, что приходит с сервера как текст (подписи кнопок, ники, сообщения об ошибках), выводится только через esc(). */
(function () {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  try { tg.ready(); tg.expand(); } catch (e) { /* вне Telegram */ }
  try { tg.setHeaderColor("#07060b"); tg.setBackgroundColor("#07060b"); } catch (e) { /* то же */ }
  function applySafeArea() {
    try {
      const top = ((tg.contentSafeAreaInset && tg.contentSafeAreaInset.top) || 0) + ((tg.safeAreaInset && tg.safeAreaInset.top) || 0);
      document.documentElement.style.setProperty("--tg-safe-top", top + "px");
    } catch (e) { /* то же */ }
  }
  applySafeArea();
  try { tg.onEvent("safeAreaChanged", applySafeArea); tg.onEvent("contentSafeAreaChanged", applySafeArea); } catch (e) { /* то же */ }

  const root = document.getElementById("root");
  const tipEl = document.getElementById("tip");
  const toastEl = document.getElementById("toast");

  const TABS = [
    ["overview", "Обзор"], ["retention", "Удержание"], ["funnel", "Воронка"], ["features", "Функции"], ["screens", "Экраны и кнопки"],
    ["versions", "Версии"], ["health", "Здоровье"], ["rhythm", "Ритм"], ["channels", "Каналы"], ["analyst", "ИИ-аналитик"], ["quality", "Достоверность"],
  ];
  const DAYS = [7, 14, 30, 60, 90];
  const SOURCE_TABS = ["overview", "retention", "funnel"];
  const DAYS_TABS = ["overview", "retention", "funnel", "features", "screens", "health", "rhythm", "channels", "analyst"];

  const store = {
    get(key, fallback) { try { const v = localStorage.getItem("an_" + key); return v == null ? fallback : v; } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem("an_" + key, value); } catch (e) { /* без памяти тоже работает */ } },
  };
  const S = {
    tab: (location.hash || "").slice(1) || store.get("tab", "overview"),
    days: Number(store.get("days", 30)),
    source: store.get("source", ""),
    sort: store.get("sort", "users"),
    screenFilter: "",
    sources: [],
    unmapped: 0,
  };
  if (!TABS.some((t) => t[0] === S.tab)) S.tab = "overview";
  if (!DAYS.includes(S.days)) S.days = 30;

  // ---------------------------------------------------------------------------------------------------------------- утилиты
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("ru-RU"));
  const pct = (x, digits) => (x == null ? "—" : (digits === 0 ? Math.round(x) : Number(x).toFixed(1).replace(/\.0$/, "").replace(".", ",")) + "%");
  const haptic = (kind) => { try { tg.HapticFeedback.impactOccurred(kind || "light"); } catch (e) { /* то же */ } };
  const initData = () => (tg && tg.initData) || "";
  const dayShort = (d) => (d ? d.slice(8, 10) + "." + d.slice(5, 7) : "");
  const dateShort = (s) => (s ? s.slice(8, 10) + "." + s.slice(5, 7) + " " + s.slice(11, 16) : "—");
  const shortVersion = (v) => String(v || "").replace(/^\d{8}_/, "");

  function dur(sec) {
    sec = Math.round(Number(sec) || 0);
    if (sec < 60) return sec + " с";
    const m = Math.floor(sec / 60);
    if (m < 60) return m + " мин" + (sec % 60 ? " " + (sec % 60) + " с" : "");
    return Math.floor(m / 60) + " ч " + (m % 60) + " мин";
  }
  function after(min) {
    if (min == null) return "";
    if (min < 1) return "сразу";
    if (min < 60) return "через " + min + " мин";
    if (min < 1440) return "через " + Math.floor(min / 60) + " ч" + (min % 60 ? " " + (min % 60) + " мин" : "");
    return "через " + Math.floor(min / 1440) + " дн.";
  }
  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => toastEl.classList.remove("show"), 2300);
  }

  const cache = new Map();
  async function api(path, opts, fresh) {
    opts = opts || {};
    const isGet = !opts.method || opts.method === "GET";
    if (isGet && !fresh && cache.has(path)) return cache.get(path);
    const headers = Object.assign({ Authorization: "tma " + initData() }, opts.headers || {});
    if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    let data = null;
    try { data = await res.json(); } catch (e) { /* пустое тело */ }
    if (!res.ok) {
      const err = new Error((data && (data.error || data.message)) || "http_" + res.status);
      err.status = res.status; err.data = data;
      throw err;
    }
    if (isGet) cache.set(path, data);
    return data;
  }
  const get = (path) => api(path);
  const query = (extra) => "days=" + S.days + (S.source ? "&source=" + encodeURIComponent(S.source) : "") + (extra || "");

  // ---------------------------------------------------------------------------------------------------------------- диаграммы
  const CH = {};
  let chartSeq = 0;
  const SVG_W = 640;

  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }
  function pickLabels(n, max) {
    if (n <= max) return Array.from({ length: n }, (_, i) => i);
    const step = Math.ceil((n - 1) / (max - 1));
    const out = [];
    for (let i = 0; i < n; i += step) out.push(i);
    if (out[out.length - 1] !== n - 1) { if (n - 1 - out[out.length - 1] < step / 2) out.pop(); out.push(n - 1); }
    return out;
  }
  function frameGeom(height) { return { L: 38, R: 8, T: 10, B: 24, W: SVG_W, H: height || 190 }; }

  // Для счётчиков с маленьким максимумом шаг шкалы — целое число (0, 1, 2, 3…), а не 1,25 человека.
  function tickCount(max, integer) { return integer && max <= 8 ? max : 4; }

  function axesSvg(g, max, labels, fmt, ticks) {
    let out = "";
    for (let i = 0; i <= ticks; i++) {
      const y = g.T + (g.H - g.T - g.B) * (1 - i / ticks);
      out += `<line class="grid-line" x1="${g.L}" x2="${g.W - g.R}" y1="${y}" y2="${y}"/>`;
      out += `<text class="axis" x="${g.L - 6}" y="${y + 3.5}" text-anchor="end" style="fill:var(--muted);font-size:10px">${esc(fmt(max * i / ticks))}</text>`;
    }
    return out;
  }

  function lineChart(labels, series, opt) {
    opt = opt || {};
    const fmt = opt.fmt || num;
    const g = frameGeom(opt.height);
    const n = labels.length;
    const all = [];
    series.forEach((s) => s.values.forEach((v) => { if (v != null) all.push(v); }));
    let max = opt.yMax || niceMax(Math.max.apply(null, all.concat([1])));
    if (!opt.yMax && all.every(Number.isInteger) && max < 4) max = 4;       // счётчики: шкала 0…4, а не 0, 0,25, 0,5…
    const x = (i) => g.L + (n === 1 ? (g.W - g.L - g.R) / 2 : i * (g.W - g.L - g.R) / (n - 1));
    const y = (v) => g.T + (g.H - g.T - g.B) * (1 - v / max);
    const id = "ch" + (++chartSeq);
    let svg = axesSvg(g, max, labels, fmt, tickCount(max, all.every(Number.isInteger)));
    pickLabels(n, 6).forEach((i) => {
      svg += `<text x="${x(i)}" y="${g.H - 6}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" style="fill:var(--muted);font-size:10px">${esc(labels[i])}</text>`;
    });
    (opt.markers || []).forEach((m) => {
      svg += `<line class="rel" x1="${x(m.index)}" x2="${x(m.index)}" y1="${g.T}" y2="${g.H - g.B}"/><circle cx="${x(m.index)}" cy="${g.T}" r="3" fill="#F0B429"/>`;
    });
    series.forEach((s) => {
      let d = "", pen = false, firstX = null, lastX = null;
      s.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " ";
        if (firstX == null) firstX = x(i);
        lastX = x(i); pen = true;
      });
      if (series.length === 1 && d && firstX != null && s.values.every((v) => v != null)) {
        svg += `<defs><linearGradient id="${id}g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".28"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs>`;
        svg += `<path d="${d}L${lastX.toFixed(1)} ${g.H - g.B} L${firstX.toFixed(1)} ${g.H - g.B} Z" fill="url(#${id}g)"/>`;
      }
      svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      let last = -1;
      s.values.forEach((v, i) => { if (v != null) last = i; });
      if (last >= 0) svg += `<circle cx="${x(last)}" cy="${y(s.values[last])}" r="4" fill="${s.color}" stroke="#15121e" stroke-width="2"/>`;
    });
    svg += `<line class="xh" id="${id}x" x1="0" x2="0" y1="${g.T}" y2="${g.H - g.B}" visibility="hidden"/>`;
    series.forEach((s, si) => { svg += `<circle id="${id}d${si}" r="4" fill="${s.color}" stroke="#15121e" stroke-width="2" visibility="hidden"/>`; });
    CH[id] = { type: "line", labels, series, fmt, g, x, y, n, markers: opt.markers || [], label: opt.label || "" };
    const legend = series.length > 1
      ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
    const aria = `${opt.label || "График"}: ${series.map((s) => s.name).join(", ")}`;
    return `<div class="viz"><svg viewBox="0 0 ${g.W} ${g.H}" data-chart="${id}" role="img" aria-label="${esc(aria)}">${svg}</svg>${legend}</div>`;
  }

  function barChart(labels, values, opt) {
    opt = opt || {};
    const fmt = opt.fmt || num;
    const g = frameGeom(opt.height || 170);
    const n = labels.length;
    const present = values.filter((v) => v != null);
    let max = opt.yMax || niceMax(Math.max.apply(null, present.concat([1])));
    if (!opt.yMax && present.every(Number.isInteger) && max < 4) max = 4;
    const slot = (g.W - g.L - g.R) / n;
    const bw = Math.max(3, slot - 2);
    const y = (v) => g.T + (g.H - g.T - g.B) * (1 - v / max);
    const base = g.H - g.B;
    let svg = axesSvg(g, max, labels, fmt, tickCount(max, present.every(Number.isInteger)));
    pickLabels(n, opt.maxLabels || 7).forEach((i) => {
      svg += `<text x="${g.L + slot * i + slot / 2}" y="${g.H - 6}" text-anchor="middle" style="fill:var(--muted);font-size:10px">${esc(labels[i])}</text>`;
    });
    values.forEach((v, i) => {
      const x0 = g.L + slot * i + (slot - bw) / 2;
      const tip = `${labels[i]}\n${opt.name || "Значение"}: ${v == null ? "мало данных" : fmt(v)}${opt.extra && opt.extra[i] ? "\n" + opt.extra[i] : ""}`;
      if (v != null && v > 0) {
        const top = y(v), r = Math.min(4, bw / 2, base - top);
        svg += `<path d="M${x0} ${base} V${top + r} Q${x0} ${top} ${x0 + r} ${top} H${x0 + bw - r} Q${x0 + bw} ${top} ${x0 + bw} ${top + r} V${base} Z" fill="${(opt.colors && opt.colors[i]) || opt.color || "var(--s1)"}"/>`;
      }
      svg += `<rect x="${g.L + slot * i}" y="${g.T}" width="${slot}" height="${g.H - g.T - g.B}" fill="transparent" data-tip="${esc(tip)}"/>`;
    });
    const aria = `${opt.label || "Столбики"}: ${n} значений`;
    return `<div class="viz"><svg viewBox="0 0 ${g.W} ${g.H}" role="img" aria-label="${esc(aria)}">${svg}</svg></div>`;
  }

  function sparkline(values, color) {
    const w = 70, h = 22, n = values.length;
    if (!n) return `<svg class="sparkline" viewBox="0 0 ${w} ${h}"></svg>`;
    const max = Math.max.apply(null, values.concat([1]));
    const pts = values.map((v, i) => [n === 1 ? w / 2 : 2 + i * (w - 4) / (n - 1), h - 3 - (h - 6) * (v / max)]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const last = pts[pts.length - 1];
    return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="none" stroke="${color || "var(--s1)"}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${last[0]}" cy="${last[1]}" r="2.4" fill="${color || "var(--s1)"}"/></svg>`;
  }

  // Одна синяя шкала величины: от почти поверхности до светлого. t в [0, 1].
  const RAMP = [[0, [29, 36, 51]], [0.35, [24, 79, 149]], [0.7, [57, 135, 229]], [1, [158, 197, 244]]];
  function ramp(t) {
    t = Math.max(0, Math.min(1, t));
    for (let i = 1; i < RAMP.length; i++) {
      if (t <= RAMP[i][0]) {
        const a = RAMP[i - 1], b = RAMP[i], k = (t - a[0]) / (b[0] - a[0]);
        return "rgb(" + a[1].map((c, j) => Math.round(c + (b[1][j] - c) * k)).join(",") + ")";
      }
    }
    return "rgb(158,197,244)";
  }
  const inkOn = (t) => (t >= 0.55 ? "#07060b" : "#fff");

  function vizCard(title, sub, chart, table, extra) {
    const id = "viz" + (++chartSeq);
    return `<div class="card" data-viz="${id}"><div class="card-head"><div class="grow"><div class="card-title">${esc(title)}</div>${sub ? `<div class="card-sub">${esc(sub)}</div>` : ""}</div>` +
      `${extra || ""}<button class="mini-btn" data-toggle-table="${id}" aria-pressed="false">Таблица</button></div>` +
      `<div data-pane="chart">${chart}</div><div class="viz-table" data-pane="table" hidden>${table}</div></div>`;
  }
  function simpleTable(headers, rows) {
    return `<table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  }

  // ---------------------------------------------------------------------------------------------------------------- всплывающие подсказки
  function showTip(text, cx, cy) {
    tipEl.textContent = text;
    tipEl.classList.add("show");
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    let left = cx + 14, top = cy - h - 12;
    if (left + w > window.innerWidth - 8) left = cx - w - 14;
    if (left < 8) left = 8;
    if (top < 8) top = cy + 18;
    tipEl.style.left = left + "px";
    tipEl.style.top = top + "px";
  }
  function hideTip() {
    tipEl.classList.remove("show");
    document.querySelectorAll(".xh").forEach((l) => l.setAttribute("visibility", "hidden"));
    document.querySelectorAll("svg[data-chart] circle[id]").forEach((c) => c.setAttribute("visibility", "hidden"));
  }
  function onPointer(e) {
    const target = e.target;
    if (!target || !target.closest) return;
    const svg = target.closest("svg[data-chart]");
    if (svg) {
      const id = svg.getAttribute("data-chart");
      const cfg = CH[id];
      if (!cfg) return;
      const rect = svg.getBoundingClientRect();
      const px = (e.clientX - rect.left) * (cfg.g.W / rect.width);
      const step = cfg.n > 1 ? (cfg.g.W - cfg.g.L - cfg.g.R) / (cfg.n - 1) : 1;
      const idx = Math.max(0, Math.min(cfg.n - 1, Math.round((px - cfg.g.L) / step)));
      const xs = cfg.x(idx);
      const line = document.getElementById(id + "x");
      line.setAttribute("x1", xs); line.setAttribute("x2", xs); line.setAttribute("visibility", "visible");
      let text = cfg.labels[idx];
      cfg.series.forEach((s, si) => {
        const dot = document.getElementById(id + "d" + si);
        const v = s.values[idx];
        if (v == null) { dot.setAttribute("visibility", "hidden"); text += "\n" + s.name + ": мало данных"; return; }
        dot.setAttribute("cx", xs); dot.setAttribute("cy", cfg.y(v)); dot.setAttribute("visibility", "visible");
        text += "\n" + (cfg.series.length > 1 ? s.name + ": " : "") + cfg.fmt(v);
      });
      cfg.markers.filter((m) => m.index === idx).forEach((m) => { text += "\n🚀 выпуск " + m.label; });
      showTip(text, e.clientX, e.clientY);
      return;
    }
    const holder = target.closest("[data-tip]");
    if (holder) { showTip(holder.getAttribute("data-tip"), e.clientX, e.clientY); return; }
    hideTip();
  }
  document.addEventListener("pointermove", onPointer);
  document.addEventListener("pointerdown", onPointer);
  document.addEventListener("pointerleave", hideTip);
  window.addEventListener("scroll", hideTip, { passive: true });

  // ---------------------------------------------------------------------------------------------------------------- каркас
  let view = null;
  let renderToken = 0;

  function frame() {
    const tabs = TABS.map(([id, name]) =>
      `<button class="tab" role="tab" data-tab="${id}" aria-selected="${id === S.tab}">${esc(name)}${id === "features" && S.unmapped ? `<span class="dot" title="Есть новое, не описанное в карте функций">${S.unmapped}</span>` : ""}</button>`).join("");
    root.innerHTML =
      `<div class="top"><div class="top-icon">📊</div><div class="top-copy"><div class="top-title">Аналитика</div><div class="top-sub" id="topSub">ADAM · цифры только по настоящим людям</div></div>` +
      `<button class="icon-btn" id="refreshBtn" aria-label="Обновить">⟳</button><button class="icon-btn" id="adminBtn" aria-label="В админку">🛠</button></div>` +
      `<div class="filters" id="filters"></div><div class="tabs" role="tablist" id="tabs">${tabs}</div><div id="view"></div>`;
    view = document.getElementById("view");
    renderFilters();
  }

  function renderFilters() {
    const box = document.getElementById("filters");
    let html = "";
    if (DAYS_TABS.includes(S.tab)) {
      html += `<select class="select" id="daysSel" aria-label="Период">${DAYS.map((d) => `<option value="${d}"${d === S.days ? " selected" : ""}>${d} дн.</option>`).join("")}</select>`;
    }
    if (SOURCE_TABS.includes(S.tab)) {
      html += `<select class="select" id="srcSel" aria-label="Канал"><option value="">Все каналы</option>${S.sources.map((s) => `<option value="${esc(s.code)}"${s.code === S.source ? " selected" : ""}>${esc(s.title)}</option>`).join("")}</select>`;
    }
    box.innerHTML = html + `<span class="hint">${S.tab === "retention" ? "Окно регистраций: не меньше 60 дней, иначе день 30 не успевает наступить" : ""}</span>`;
    box.hidden = !html;
  }

  async function render() {
    const token = ++renderToken;
    hideTip();
    view.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
    try {
      const html = await RENDER[S.tab]();
      if (token !== renderToken) return;
      view.innerHTML = html;
    } catch (e) {
      if (token !== renderToken) return;
      view.innerHTML = `<div class="card"><div class="empty">Не получилось загрузить: ${esc(e.message || "ошибка")}.<br><br><button class="btn" id="retryBtn">Повторить</button></div></div>`;
    }
  }

  // ---------------------------------------------------------------------------------------------------------------- обзор
  function tile(label, value, foot, spark, small) {
    return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}${small ? `<small>${esc(small)}</small>` : ""}</div>` +
      `<div class="tile-foot">${foot || ""}</div>${spark || ""}</div>`;
  }
  function block(b) {
    return b && b.pct != null ? pct(b.pct) : (b && b.of ? "мало данных" : "—");
  }
  function blockFoot(b) { return b && b.of ? `${num(b.n)} из ${num(b.of)}` : "ещё нет людей"; }

  async function renderOverview() {
    const [ov, sg, vs] = await Promise.all([
      get("/api/analytics/overview?" + query()), get("/api/analytics/signals"), get("/api/analytics/versions?limit=12"),
    ]);
    const unmapped = sg.items.find((s) => s.key === "unmapped");
    setFeatureDot(unmapped ? unmapped.count : 0);
    const t = ov.tiles, se = ov.series;
    document.getElementById("topSub").textContent = "ADAM · " + num(t.users_total) + " человек · данные трекера с " + (ov.tracking_since ? dayShort(ov.tracking_since.slice(0, 10)) : "—");
    const signals = sg.items.length
      ? sg.items.map((s) => `<div class="signal sig-${esc(s.level)}"><span class="sig-ico">${s.level === "bad" ? "!" : s.level === "warn" ? "▲" : "i"}</span><span><b>${esc(s.title)}</b><span class="t">${esc(s.text)}</span></span></div>`).join("")
      : '<div class="empty">Датчики ничего не нашли: ошибок нет, резких скачков нет.</div>';
    const tiles = [
      tile("Новые люди", num(t.new_users), "за " + ov.days + " дн.", sparkline(se.new_users)),
      tile("Активны сегодня", num(t.dau_today), `вчера ${num(t.dau_yesterday)} · день ещё идёт`, sparkline(se.dau, "var(--s3)")),
      tile("Активны за 7 дней", num(t.wau), `за 30 дней: ${num(t.mau)}`, ""),
      tile("Липкость DAU/MAU", t.stickiness == null ? "—" : pct(t.stickiness), t.stickiness == null ? "мало данных (нужно 8+ человек)" : "доля месяца, когда человек заходит", ""),
      tile("Время в приложении", t.avg_session_seconds == null ? "—" : dur(t.avg_session_seconds), `${num(t.sessions)} сессий · ${t.sessions_per_active_user == null ? "—" : t.sessions_per_active_user} на человека`, sparkline(se.active_minutes)),
      tile("Дошли до привычки", block(t.activation_habit), blockFoot(t.activation_habit) + " (новые, старше суток)", ""),
      tile("Написали Адаму", block(t.activation_ai), blockFoot(t.activation_ai) + " (новые, старше суток)", ""),
      tile("Оплаты", num(t.payers), t.revenue_stars ? num(t.revenue_stars) + " ⭐ за период" : "оплат пока нет", ""),
    ].join("");
    const markers = [];
    vs.items.forEach((v) => {
      const i = se.days.indexOf((v.first_seen || "").slice(0, 10));
      if (i >= 0) markers.push({ index: i, label: shortVersion(v.version) });
    });
    const labels = se.days.map(dayShort);
    const dauChart = vizCard("Активные люди по дням", "Золотые линии — выпуски приложения: видно, что изменилось после релиза",
      lineChart(labels, [{ name: "Активные люди", color: "var(--s1)", values: se.dau }], { markers, label: "Активные люди по дням" }),
      simpleTable(["День", "Активных", "Новых", "Сессий"], se.days.map((d, i) => [dayShort(d), se.dau[i], se.new_users[i], se.sessions[i]])));
    const newChart = vizCard("Новые люди по дням", "", barChart(labels, se.new_users, { name: "Новых", label: "Новые люди по дням" }),
      simpleTable(["День", "Новых"], se.days.map((d, i) => [dayShort(d), se.new_users[i]])));
    return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Что требует внимания</div><div class="card-sub">Датчики сравнивают сегодня с обычными днями. Разбор причин — во вкладке «ИИ-аналитик»</div></div></div>${signals}</div>` +
      `<div class="grid g4" style="margin-bottom:14px">${tiles}</div>${dauChart}${newChart}`;
  }

  // ---------------------------------------------------------------------------------------------------------------- удержание
  function heatCell(b, label) {
    if (!b || !b.of) return `<div class="hcell na" data-tip="${esc(label + ": этот день ещё не наступил у людей когорты")}">—</div>`;
    if (b.pct == null) return `<div class="hcell na" data-tip="${esc(label + ": " + b.n + " из " + b.of + " — мало данных для вывода")}">мало</div>`;
    const t = Math.min(1, b.pct / 60);
    return `<div class="hcell" style="background:${ramp(t)};color:${inkOn(t)}" data-tip="${esc(label + ": вернулись " + b.n + " из " + b.of + " (" + pct(b.pct) + ")")}">${pct(b.pct, 0)}</div>`;
  }

  async function renderRetention() {
    const days = Math.max(60, S.days);
    const [ret, eng] = await Promise.all([
      get("/api/analytics/retention?days=" + days + (S.source ? "&source=" + encodeURIComponent(S.source) : "")), get("/api/analytics/engagement?days=14"),
    ]);
    const marks = ret.marks;
    const cols = "grid-template-columns:minmax(70px,1.3fr) 34px repeat(" + marks.length + ",minmax(36px,1fr))";
    let grid = `<div class="heat" style="${cols}"><div class="hcell head" style="justify-items:start">Когорта</div><div class="hcell head">Чел.</div>${marks.map((m) => `<div class="hcell head">День ${m}</div>`).join("")}`;
    grid += `<div class="hcell rowl">Все<small>за ${ret.days} дн.</small></div><div class="hcell head" style="color:#fff">${num(ret.overall.n)}</div>${marks.map((m) => heatCell(ret.overall["d" + m], "Все, день " + m)).join("")}`;
    ret.cohorts.forEach((c) => {
      grid += `<div class="hcell rowl">${esc(c.label)}<small>неделя регистрации</small></div><div class="hcell head" style="color:#fff">${num(c.n)}</div>${marks.map((m) => heatCell(c["d" + m], c.label + ", день " + m)).join("")}`;
    });
    grid += "</div>";
    const legend = '<div class="ramp"></div><div class="ramp-l"><span>0%</span><span>60% и выше</span></div>';
    const rowsT = ret.cohorts.map((c) => [c.label, c.n].concat(marks.map((m) => block(c["d" + m]))));
    const heat = `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Удержание по неделям регистрации</div><div class="card-sub">Какая доля людей из когорты была активна ровно на N-й день после регистрации</div></div></div>${grid}${legend}` +
      `<div class="note" style="margin-top:10px">«—» — этот день ещё не наступил; «мало» — в когорте меньше 8 человек, процент ничего не доказывает. Дни по UTC.</div></div>`;
    const curvePts = ret.curve.map((p) => p.pct);
    const curve = vizCard("Кривая возвращаемости", "Доля людей, активных в день N после регистрации (все когорты вместе, созревшие)",
      ret.curve.length ? lineChart(ret.curve.map((p) => "День " + p.day), [{ name: "Вернулись", color: "var(--s1)", values: curvePts }], { fmt: (v) => pct(v), yMax: 100, label: "Кривая возвращаемости" }) : '<div class="empty">Пока нет созревших когорт.</div>',
      simpleTable(["День", "Вернулись", "Из"], ret.curve.map((p) => [p.day, p.pct == null ? "мало данных" : pct(p.pct), p.of])));
    const w = eng.week_one;
    const depth = vizCard("Сколько дней из первой недели люди были активны", `Люди, зарегистрированные 7–60 дней назад: ${num(w.eligible)}, хоть раз активны — ${num(w.active_at_least_once)}`,
      w.eligible ? barChart(w.rows.map((r) => r.days + " дн."), w.rows.map((r) => r.count), { name: "Человек", label: "Дней активности из первых семи" }) : '<div class="empty">Пока нет людей старше недели.</div>',
      simpleTable(["Дней активности", "Человек"], w.rows.map((r) => [r.days, r.count])));
    return heat + curve + depth;
  }

  // ---------------------------------------------------------------------------------------------------------------- воронка
  const SKIP_NAMES = { tour_skip: "Пропустили вводный тур", hint_skip: "Пропустили подсказки" };
  // Онбординг по шагам: сколько людей дошло до каждого шага и сколько ушло именно на нём. Шаги и подписи приходят с сервера
  // (db/tracking.py::onboarding_steps): новый шаг в приложении появляется здесь сам, пока ему не дали название — под своим ключом.
  function onboardingCards(f) {
    if (!f.onboarding || !f.onboarding.length) return "";
    const base = f.onboarding_base || 0;
    const rowsHtml = (g) => g.rows.map((s) => {
      const w = Math.max(s.users ? 2 : 0, s.pct);
      const meta = [s.left ? "ушли здесь: " + num(s.left) : null, s.unnamed ? "без названия — добавьте подпись" : null].filter(Boolean).join(" · ");
      return `<div class="hrow" data-tip="${esc(s.label + "\n" + num(s.users) + " из " + num(base) + " (" + pct(s.pct) + ")" + (meta ? "\n" + meta : ""))}">` +
        `<div class="lbl">${esc(s.label)}${meta ? `<div class="muted" style="font-weight:500;font-size:10.5px">${esc(meta)}</div>` : ""}</div>` +
        `<div class="bar"><i style="width:${w}%;background:${ramp(0.8)}"></i></div><div class="val"><b style="color:#fff">${num(s.users)}</b> · ${pct(s.pct, 0)}</div></div>`;
    }).join("");
    const chart = f.onboarding.map((g) => `<div class="card-title" style="margin:14px 0 4px;font-size:12px;color:var(--text-2)">${esc(g.title)}</div>${rowsHtml(g)}`).join("");
    const table = simpleTable(["Блок", "Шаг", "Человек", "Доля", "Ушли здесь"],
      f.onboarding.flatMap((g) => g.rows.map((s) => [g.title, s.label, s.users, pct(s.pct), s.left == null ? "—" : s.left])));
    const entries = f.entries && f.entries.length
      ? f.entries.map((x) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">${esc(x.label)}</div><div class="val">${num(x.users)} чел. · ${num(x.sessions)} входов</div></div>`).join("")
      : '<div class="empty">Пока нет данных о входах.</div>';
    return vizCard("Онбординг по шагам", `Среди ${num(base)} человек, открывших приложение. «Ушли здесь» — были на шаге и не дошли до следующего`, chart, table) +
      `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Как открывают приложение</div>` +
      `<div class="card-sub">Кнопка в сообщении бота против меню Telegram. Считается с 14.10 — раньше источник входа не записывался</div></div></div>${entries}</div>`;
  }

  async function renderFunnel() {
    const f = await get("/api/analytics/funnel?" + query());
    if (!f.total) return '<div class="card"><div class="empty">За этот период новых людей нет.</div></div>';
    const n = f.steps.length;
    const rows = f.steps.map((s, i) => {
      const w = Math.max(s.count ? 2 : 0, s.pct_of_all);
      const shade = ramp(0.28 + 0.52 * (1 - i / Math.max(1, n - 1)));
      const meta = [s.pct_of_prev != null ? "от прошлого шага " + pct(s.pct_of_prev) : null, s.median_minutes != null ? "медиана: " + after(s.median_minutes) : null].filter(Boolean).join(" · ");
      return `<div class="hrow" data-tip="${esc(s.label + "\n" + num(s.count) + " из " + num(f.total) + " (" + pct(s.pct_of_all) + ")" + (meta ? "\n" + meta : ""))}">` +
        `<div class="lbl">${esc(s.label)}${meta ? `<div class="muted" style="font-weight:500;font-size:10.5px">${esc(meta)}</div>` : ""}</div>` +
        `<div class="bar"><i style="width:${w}%;background:${shade}"></i></div><div class="val"><b style="color:#fff">${num(s.count)}</b> · ${pct(s.pct_of_all, 0)}</div></div>`;
    }).join("");
    const stuck = f.stuck.length ? f.stuck.map((x) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">${esc(x.label)}</div><div class="val"><b style="color:#fff">${num(x.count)}</b> чел.</div></div>`).join("") : '<div class="empty">Все дошли до конца основного пути.</div>';
    const skips = f.skips.length ? f.skips.map((x) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">${esc(SKIP_NAMES[x.name] || x.name)}${x.step ? ` <span class="muted">· шаг ${esc(x.step)}</span>` : ""}</div><div class="val">${num(x.users)} чел.</div></div>`).join("") : '<div class="empty">Пропусков нет.</div>';
    const exits = f.exits.length ? f.exits.map((x) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">${esc(x.label)}${x.screen ? ` <span class="muted">· ${esc(x.screen)}</span>` : ""}</div><div class="val">×${num(x.count)}</div></div>`).join("") : '<div class="empty">Пока нет данных о выходах.</div>';
    const onboarding = onboardingCards(f);
    return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Воронка новых людей</div><div class="card-sub">${num(f.total)} человек пришли за ${f.days} дн. Шаги после «Отметили привычку» идут параллельно, а не друг за другом</div></div></div>${rows}</div>` +
      onboarding +
      `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Где застревают</div><div class="card-sub">Первый шаг основного пути, до которого человек не дошёл</div></div></div>${stuck}</div>` +
      `<div class="grid g2"><div class="card"><div class="card-title" style="margin-bottom:10px">Что пропускают</div>${skips}</div><div class="card"><div class="card-title" style="margin-bottom:10px">После чего выходят</div>${exits}</div></div>`;
  }

  // ---------------------------------------------------------------------------------------------------------------- функции
  function featureRow(f, activeUsers) {
    const ratio = activeUsers ? Math.min(100, 100 * f.users / activeUsers) : 0;
    const prevPct = f.prev_active ? 100 * f.prev_users / f.prev_active : null;
    const nowPct = f.adoption.pct;
    let trend = "";
    if (nowPct != null && prevPct != null && (nowPct > 0 || prevPct > 0)) {
      const d = nowPct - prevPct;
      trend = Math.abs(d) < 1 ? '<span class="delta flat">≈ без изменений</span>' : `<span class="delta ${d > 0 ? "up" : "down"}">${d > 0 ? "▲ +" : "▼ "}${d.toFixed(1).replace(/\.0$/, "")} п.п. к прошлому периоду</span>`;
    }
    const meta = [
      `охват ${f.adoption.pct == null ? (f.adoption.of ? "мало данных" : "—") : pct(f.adoption.pct)} (${num(f.users)} из ${num(f.adoption.of)})`,
      f.users ? `возвращаются: ${f.repeat.pct == null ? "мало данных" : pct(f.repeat.pct, 0)}` : null,
      f.uses ? `действий: ${num(f.uses)}` : null,
      trend,
    ].filter(Boolean).map((x) => `<span>${x}</span>`).join("");
    const impact = f.impact
      ? `<div class="note" style="margin:8px 0">Возвращаемость на 7-й день: у тех, кто попробовал в первые 3 дня, — <b>${pct(f.impact.tried.d7)}</b> (${f.impact.tried.n} чел.), у остальных — <b>${pct(f.impact.not_tried.d7)}</b> (${f.impact.not_tried.n} чел.). Это связь, а не доказательство причины.</div>` : "";
    const buttons = f.buttons.length
      ? `<table class="tbl"><thead><tr><th>Кнопка</th><th>Нажатий</th><th>Людей</th></tr></thead><tbody>${f.buttons.map((b) => `<tr><td>${esc(b.title)}</td><td>${num(b.clicks)}</td><td>${num(b.users)}</td></tr>`).join("")}</tbody></table>` : "";
    const launched = f.age_days != null ? `Выпущена ${esc(f.since)} (${f.age_days} дн. назад)` : "";
    return `<div class="feat" data-feat="${esc(f.id)}"><button class="feat-head" data-toggle-feat="${esc(f.id)}" aria-expanded="false">` +
      `<span class="feat-name">${esc(f.name)}${f.new ? '<span class="badge badge-new">НОВАЯ</span>' : ""}</span>` +
      `<span class="feat-num">${num(f.users)}<small>человек</small></span>` +
      `<span class="feat-bar"><span class="bar"><i style="width:${ratio}%"></i></span>${sparkline(f.series)}</span><span class="feat-meta">${meta}</span></button>` +
      `<div class="feat-body" hidden>${f.note ? `<div class="note" style="margin:8px 0">Считается использованием: ${esc(f.note)}</div>` : ""}${impact}${buttons}` +
      `<div class="note muted" style="margin-top:8px">${launched}${f.first_seen ? " · первое использование в данных: " + esc(f.first_seen) : ""}</div></div></div>`;
  }

  // Золотая точка на вкладке «Функции»: есть кнопки, экраны или запросы, которых нет в карте функций.
  function setFeatureDot(count) {
    S.unmapped = count;
    const tabBtn = document.querySelector('.tab[data-tab="features"]');
    if (!tabBtn) return;
    const dot = tabBtn.querySelector(".dot");
    if (dot) dot.remove();
    if (count) tabBtn.insertAdjacentHTML("beforeend", `<span class="dot" title="Есть новое, не описанное в карте функций">${count}</span>`);
  }

  async function renderFeatures() {
    const d = await get("/api/analytics/features?days=" + S.days);
    setFeatureDot(d.unmapped.length + d.unmapped_events.length);
    const sorters = {
      users: (a, b) => b.users - a.users || a.name.localeCompare(b.name),
      new: (a, b) => (b.new - a.new) || (b.users - a.users),
      name: (a, b) => a.name.localeCompare(b.name),
    };
    const list = d.features.slice().sort(sorters[S.sort] || sorters.users);
    const fresh = d.features.filter((f) => f.new);
    let html = "";
    if (S.unmapped) {
      html += `<div class="warnbox"><b>Есть новое, не описанное в карте функций (${S.unmapped})</b><div class="note" style="margin-top:6px">Это уже учтено в данных, но не попало в разделы ниже. ` +
        `Чтобы оно появилось с названием и датой выпуска, нужно дописать <b>webapp/feature_map.py</b> — скажи Claude: «добавь в карту функций».</div>` +
        d.unmapped.slice(0, 12).map((u) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">${esc(u.title)} <span class="muted">· ${u.kind === "click" ? "кнопка" : u.kind === "screen" ? "экран" : "запрос"}${u.screen ? ", " + esc(u.screen) : ""}${u.first_version ? ", с " + esc(shortVersion(u.first_version)) : ""}</span><div class="muted" style="font-weight:500;font-size:10.5px">${esc(u.hint)}</div></div><div class="val">${num(u.uses)} × · ${num(u.users)} чел.</div></div>`).join("") +
        d.unmapped_events.map((e) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl">Событие «${esc(e.label)}» <span class="muted">· ${esc(e.name)}</span></div><div class="val">${num(e.count)} × · ${num(e.users)} чел.</div></div>`).join("") + "</div>";
    }
    html += `<div class="grid g3" style="margin-bottom:14px">${tile("Активных людей", num(d.active_users), `в прошлом периоде: ${num(d.active_users_prev)}`)}${tile("Функций в карте", num(d.features.length), `новых за 30 дн.: ${fresh.length}`)}${tile("Пользуются сейчас", num(d.features.filter((f) => f.users).length), "хоть один человек за период")}</div>`;
    html += `<div class="filters"><select class="select" id="sortSel" aria-label="Сортировка"><option value="users"${S.sort === "users" ? " selected" : ""}>Сначала популярные</option><option value="new"${S.sort === "new" ? " selected" : ""}>Сначала новые</option><option value="name"${S.sort === "name" ? " selected" : ""}>По алфавиту</option></select>` +
      `<span class="hint">Нажми на функцию — увидишь кнопки и влияние на возвращаемость</span></div>`;
    if (fresh.length) {
      html += `<div class="section-label">Новые функции (последние 30 дней)</div>` + fresh.sort(sorters.users).map((f) => featureRow(f, d.active_users)).join("");
    }
    d.groups.forEach((g) => {
      const items = list.filter((f) => f.group === g.id && !f.new);
      if (items.length) html += `<div class="section-label">${esc(g.name)}</div>` + items.map((f) => featureRow(f, d.active_users)).join("");
    });
    return html;
  }

  // ---------------------------------------------------------------------------------------------------------------- экраны и кнопки
  async function renderScreens() {
    const [sc, bt] = await Promise.all([
      get("/api/analytics/screens?days=" + S.days),
      get("/api/analytics/buttons?days=" + S.days + (S.screenFilter ? "&screen=" + encodeURIComponent(S.screenFilter) : "")),
    ]);
    const top = Math.max.apply(null, sc.items.map((i) => i.seconds).concat([1]));
    const screens = sc.items.length ? sc.items.map((i) =>
      `<div class="hrow" data-tip="${esc(i.title + "\nПоказов: " + num(i.views) + ", людей: " + num(i.users) + "\nВремя: " + dur(i.seconds) + (i.avg_seconds_per_view != null ? " (в среднем " + i.avg_seconds_per_view + " с за показ)" : ""))}">` +
      `<div class="lbl">${esc(i.title)}${i.feature ? `<div class="muted" style="font-weight:500;font-size:10.5px">${esc(i.feature)}</div>` : ""}</div><div class="bar"><i style="width:${100 * i.seconds / top}%"></i></div>` +
      `<div class="val">${dur(i.seconds)} · ${num(i.users)} чел.</div></div>`).join("") : '<div class="empty">Данных об экранах пока нет — они копятся с каждым открытием приложения.</div>';
    const screenOpts = '<option value="">Все экраны</option>' + sc.items.map((i) => `<option value="${esc(i.screen)}"${i.screen === S.screenFilter ? " selected" : ""}>${esc(i.title)}</option>`).join("") + `<option value="bot"${S.screenFilter === "bot" ? " selected" : ""}>Бот</option>`;
    const rows = bt.items.map((b) => `<tr><td>${esc(b.title)}${b.new ? ' <span class="badge badge-new">НОВАЯ</span>' : ""}<div class="muted" style="font-weight:500;font-size:10.5px">${esc(b.screen_title || "")}${b.feature ? " · " + esc(b.feature) : ""}</div></td><td>${num(b.clicks)}</td><td>${num(b.users)}</td></tr>`).join("");
    const never = bt.never_clicked.length ? `<div class="section-label" style="margin-top:14px">Кнопки, на которые за период никто не нажал</div><div class="chips">${bt.never_clicked.map((b) => `<span class="chip">${esc(b.title)}</span>`).join("")}</div>` : "";
    return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Время на экранах</div><div class="card-sub">Где люди проводят время (только пока приложение открыто и были касания)</div></div></div>${screens}</div>` +
      `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Кнопки и действия</div><div class="card-sub">Новые кнопки появляются здесь сами, как только на них нажмёт первый человек</div></div><select class="select" id="screenSel" aria-label="Экран">${screenOpts}</select></div>` +
      (rows ? `<div class="scroll-x"><table class="tbl"><thead><tr><th>Кнопка</th><th>Нажатий</th><th>Людей</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">Нажатий пока нет.</div>') + never + "</div>";
  }

  // ---------------------------------------------------------------------------------------------------------------- версии
  async function renderVersions() {
    const v = await get("/api/analytics/versions?limit=12");
    if (!v.items.length) return '<div class="card"><div class="empty">Версии появятся, когда приложение обновится и первые люди откроют его после этого.</div></div>';
    const older = v.items.slice().reverse();
    const chart = vizCard("Вернулись на второй день: новички по версиям", "Люди, пришедшие, пока была актуальна версия; слева старые, справа новые",
      barChart(older.map((i) => shortVersion(i.version)), older.map((i) => i.newcomers_d1.pct), { fmt: (x) => pct(x), yMax: 100, name: "Вернулись", maxLabels: 6, label: "Возвращаемость новичков по версиям", extra: older.map((i) => "Новичков: " + i.newcomers_d1.n + " из " + i.newcomers_d1.of) }),
      simpleTable(["Версия", "Новичков", "Вернулись"], older.map((i) => [shortVersion(i.version), i.newcomers, i.newcomers_d1.pct == null ? "мало данных" : pct(i.newcomers_d1.pct)])));
    const rows = v.items.map((i) => `<tr><td>${esc(shortVersion(i.version))}<div class="muted" style="font-weight:500;font-size:10.5px">с ${esc(dateShort(i.first_seen))}</div></td><td>${num(i.sessions)}</td><td>${num(i.users)}</td><td>${i.avg_active_seconds == null ? "—" : dur(i.avg_active_seconds)}</td><td>${i.avg_actions == null ? "—" : i.avg_actions}</td><td>${i.bounce_pct == null ? "мало" : pct(i.bounce_pct, 0)}</td><td>${num(i.newcomers)}</td><td>${i.newcomers_d1.pct == null ? "мало" : pct(i.newcomers_d1.pct, 0)}</td></tr>`).join("");
    return chart + `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Выпуски приложения</div><div class="card-sub">«Закрыли за &lt;10 с» — сессии короче десяти секунд: рост после выпуска — повод посмотреть, не сломалось ли что-то</div></div></div>` +
      `<div class="scroll-x"><table class="tbl"><thead><tr><th>Версия</th><th>Сессий</th><th>Людей</th><th>Актив. в среднем</th><th>Действий</th><th>&lt;10 с</th><th>Новичков</th><th>День 2</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }

  // ---------------------------------------------------------------------------------------------------------------- здоровье
  async function renderHealth() {
    const h = await get("/api/analytics/health?days=" + S.days);
    const errTile = h.error_pct == null ? "—" : pct(h.error_pct);
    const tiles = `<div class="grid g4" style="margin-bottom:14px">${tile("Вызовов сервера", num(h.calls), "за " + h.days + " дн.")}${tile("Ошибки сервера", num(h.errors), "код 5xx")}${tile("Доля ошибок", errTile, h.error_pct != null && h.error_pct >= 2 ? '<span class="badge badge-bad">выше нормы</span>' : '<span class="badge badge-good">в норме</span>')}${tile("Сбои в приложении", num(h.crash_total), "JavaScript у людей")}</div>`;
    const chart = vizCard("Ошибки сервера по дням", "", barChart(h.series.map((p) => dayShort(p.day)), h.series.map((p) => p.errors), { name: "Ошибок", color: "var(--s2)", label: "Ошибки сервера по дням", extra: h.series.map((p) => "Вызовов: " + p.calls) }),
      simpleTable(["День", "Вызовов", "Ошибок"], h.series.map((p) => [dayShort(p.day), p.calls, p.errors])));
    const routeRow = (r) => `<tr><td>${esc(r.route)}${r.feature ? `<div class="muted" style="font-weight:500;font-size:10.5px">${esc(r.feature)}</div>` : ""}</td><td>${num(r.calls)}</td><td>${r.errors ? `<span class="badge badge-bad">${num(r.errors)}</span>` : "0"}</td><td>${num(r.client_errors)}</td><td>${r.avg_ms == null ? "—" : r.avg_ms + " мс"}</td><td>${num(r.max_ms)} мс</td></tr>`;
    const failing = h.routes.filter((r) => r.errors).slice(0, 10);
    const failingHtml = failing.length ? `<div class="scroll-x"><table class="tbl"><thead><tr><th>Маршрут</th><th>Вызовов</th><th>5xx</th><th>4xx</th><th>Среднее</th><th>Макс.</th></tr></thead><tbody>${failing.map(routeRow).join("")}</tbody></table></div>` : '<div class="empty">Падающих маршрутов нет.</div>';
    const slow = h.slowest.length ? `<div class="scroll-x"><table class="tbl"><thead><tr><th>Маршрут</th><th>Вызовов</th><th>5xx</th><th>4xx</th><th>Среднее</th><th>Макс.</th></tr></thead><tbody>${h.slowest.map(routeRow).join("")}</tbody></table></div>` : '<div class="empty">Мало вызовов для вывода.</div>';
    const crashes = h.crashes.length ? h.crashes.map((c) => `<div class="hrow" style="grid-template-columns:1fr auto"><div class="lbl" style="font-weight:500">${esc(c.message)}<div class="muted" style="font-size:10.5px">последний раз ${esc(dateShort(c.last))}</div></div><div class="val">×${num(c.count)} · ${num(c.users)} чел.</div></div>`).join("") : '<div class="empty">Сбоев в приложении нет.</div>';
    return tiles + chart + `<div class="card"><div class="card-title" style="margin-bottom:10px">Падающие маршруты</div>${failingHtml}</div><div class="card"><div class="card-title" style="margin-bottom:10px">Самые медленные</div>${slow}</div><div class="card"><div class="card-title" style="margin-bottom:10px">Сбои JavaScript у людей</div>${crashes}</div>`;
  }

  // ---------------------------------------------------------------------------------------------------------------- ритм
  const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  async function renderRhythm() {
    const [r, e] = await Promise.all([get("/api/analytics/rhythm?days=" + Math.max(7, S.days)), get("/api/analytics/engagement?days=" + S.days)]);
    const max = Math.max.apply(null, r.grid.map((row) => Math.max.apply(null, row)).concat([1]));
    let heat = '<div class="heat" style="grid-template-columns:30px repeat(24,minmax(0,1fr));gap:2px"><div></div>';
    for (let h = 0; h < 24; h++) heat += `<div class="hcell head" style="font-size:9.5px">${h % 3 === 0 ? h : ""}</div>`;
    r.grid.forEach((row, d) => {
      heat += `<div class="hcell head" style="justify-items:start">${WEEKDAYS[d]}</div>`;
      row.forEach((v, h) => {
        const t = v / max;
        heat += `<div class="hcell" style="min-height:20px;border-radius:4px;background:${v ? ramp(0.12 + 0.88 * t) : "rgba(255,255,255,.035)"}" data-tip="${esc(WEEKDAYS[d] + ", " + h + ":00 — " + v + " сесс.")}"></div>`;
      });
    });
    heat += "</div>";
    const hours = Array.from({ length: 24 }, (_, h) => h + ":00");
    const heatCard = `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Когда заходят</div><div class="card-sub">Начало сессий по местному времени человека. Всего ${num(r.sessions)} сессий${r.unknown_tz ? ", ещё " + num(r.unknown_tz) + " — без часового пояса" : ""}</div></div></div>${heat}<div class="ramp"></div><div class="ramp-l"><span>реже</span><span>чаще</span></div></div>`;
    const byHour = vizCard("Сессии по часам", r.peak_hour != null ? "Пик — около " + r.peak_hour + ":00" : "", barChart(hours.map((h, i) => (i % 3 === 0 ? String(i) : "")), r.by_hour, { name: "Сессий", label: "Сессии по часам", maxLabels: 24 }),
      simpleTable(["Час", "Сессий"], r.by_hour.map((v, i) => [i + ":00", v])));
    const len = vizCard("Длительность сессии", "Сколько человек реально был в приложении", barChart(e.length.map((x) => x.label), e.length.map((x) => x.count), { name: "Сессий", label: "Длительность сессии", maxLabels: 6 }),
      simpleTable(["Длительность", "Сессий"], e.length.map((x) => [x.label, x.count])));
    const act = vizCard("Действий за сессию", "Нажатий на кнопки за одно посещение", barChart(e.actions.map((x) => x.label), e.actions.map((x) => x.count), { name: "Сессий", label: "Действий за сессию", maxLabels: 6 }),
      simpleTable(["Действий", "Сессий"], e.actions.map((x) => [x.label, x.count])));
    return heatCard + byHour + len + act;
  }

  // ---------------------------------------------------------------------------------------------------------------- каналы
  async function renderChannels() {
    const d = await get("/api/admin/traffic?days=" + S.days);
    const kinds = d.kinds || {};
    const cards = d.channels.map((c) => {
      const flag = c.suspicious ? '<span class="badge badge-bad">⚠ подозрительный</span>' : c.joined >= 15 ? '<span class="badge badge-good">без подозрений</span>' : '<span class="badge badge-muted">мало данных</span>';
      const link = c.link ? `<div class="row" style="margin-top:10px"><input type="text" readonly value="${esc(c.link)}" style="flex:1;min-width:160px"><button class="mini-btn" data-copy="${esc(c.link)}">Копировать</button>${c.code !== "ref" && c.code !== "none" ? `<button class="mini-btn" data-archive="${esc(c.code)}">В архив</button>` : ""}</div>` : "";
      const stat = (l, v) => `<div class="tile" style="padding:8px 10px"><div class="tile-label">${esc(l)}</div><div class="tile-value" style="font-size:17px">${v}</div></div>`;
      return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">${esc(c.title)} ${flag}</div><div class="card-sub">${esc(kinds[c.kind] || "")}${c.note ? " · " + esc(c.note) : ""}${c.created_at ? " · создан " + esc(dayShort(String(c.created_at).slice(0, 10))) : ""}</div></div>` +
        `<div class="feat-num" data-tip="Оценка качества: 40% дошли до привычки, 30% вернулись на следующий день, 20% открыли приложение, 10% не «мёртвые»">${c.joined ? num(c.quality) : "—"}<small>качество</small></div></div>` +
        `<div class="grid g4">${stat("Пришло", num(c.joined))}${stat("Открыли", pct(c.opened, 0))}${stat("До привычки", pct(c.habit_done, 0))}${stat("День 2", pct(c.d1, 0))}${stat("Неделя", pct(c.d7, 0))}${stat("«Мёртвые»", c.judged ? pct(c.dead_pct, 0) : "—")}${stat("Позвали друзей", num(c.friends_invited))}${stat("Оплаты", num(c.paid) + (c.revenue_stars ? " (" + num(c.revenue_stars) + " ⭐)" : ""))}</div>` +
        (c.suspicious ? `<div class="note" style="margin-top:10px">${num(c.dead_pct)}% ничего не сделали после регистрации, ${num(c.burst_pct)}% регистраций пришлись на одно десятиминутное окно — похоже на ботов.</div>` : "") + link + "</div>";
    }).join("");
    const kindOpts = Object.keys(kinds).map((k) => `<option value="${esc(k)}">${esc(kinds[k])}</option>`).join("");
    const form = `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Новая ссылка для канала</div><div class="card-sub">Дай каждому каналу или блогеру свою ссылку — тогда видно, сколько пришло и какого качества</div></div></div>` +
      `<div class="grid g2"><input type="text" id="chTitle" placeholder="Название, например «Канал про бег»" maxlength="60"><select class="select" id="chKind" style="width:100%">${kindOpts}</select></div>` +
      `<div class="row" style="margin-top:8px"><input type="text" id="chNote" placeholder="Заметка (необязательно): цена, дата, договорённость" maxlength="120" style="flex:1;min-width:200px"><button class="btn btn-accent" id="chCreate">Создать ссылку</button></div></div>`;
    return form + (cards || '<div class="card"><div class="empty">Каналов пока нет.</div></div>');
  }

  // ---------------------------------------------------------------------------------------------------------------- ИИ-аналитик
  const IMPACT = { high: ["badge-bad", "важно"], medium: ["badge-warn", "заметно"], low: ["badge-muted", "мелочь"] };
  const AREAS = { funnel: "воронка", retention: "удержание", feature: "функция", release: "выпуск", bug: "ошибка", channel: "канал", cost: "расходы", data: "данные" };
  const KIND_NAMES = { finding: "находка", watch: "следить", owner: "моя заметка" };
  let analystBusy = false;

  function reportHtml(entry) {
    if (!entry) return '<div class="empty">Разборов ещё не было. Нажми «Сделать разбор» — аналитик прочитает цифры и скажет, что важно.</div>';
    const r = entry.report;
    const head = `<div class="card-sub" style="margin-bottom:10px">Разбор от ${esc(dateShort(entry.created_at))} · период ${entry.days} дн. · ${entry.trigger === "weekly" ? "еженедельный" : "по кнопке"}</div>`;
    if (!r) return head + `<div class="note" style="white-space:pre-line">${esc(entry.text)}</div>`;
    return head + (r.headline ? `<div style="font:700 14px/1.4 'Space Grotesk',sans-serif;margin-bottom:12px">${esc(r.headline)}</div>` : "") +
      r.findings.map((f) => `<div class="find"><span class="badge ${IMPACT[f.impact][0]}">${IMPACT[f.impact][1]}</span> <span class="badge badge-muted">${esc(AREAS[f.area] || f.area)}</span><br><b>${esc(f.title)}</b>${f.evidence ? `<div class="ev">${esc(f.evidence)}</div>` : ""}${f.action ? `<div class="ac">${esc(f.action)}</div>` : ""}</div>`).join("") +
      (r.watch.length ? `<div class="section-label" style="margin-top:14px">Следить дальше</div>${r.watch.map((w) => `<div class="note">• ${esc(w)}</div>`).join("")}` : "") +
      (r.questions.length ? `<div class="section-label" style="margin-top:14px">Вопросы к тебе</div>${r.questions.map((w) => `<div class="note">• ${esc(w)}</div>`).join("")}` : "");
  }
  function notesHtml(notes) {
    if (!notes.length) return '<div class="empty">Пока ничего. Наблюдения аналитика и твои заметки появятся здесь.</div>';
    return notes.map((n) => `<div class="note-row"><span class="badge badge-muted">${esc(KIND_NAMES[n.kind] || n.kind)}</span><div class="t">${esc(n.text)}<div class="muted" style="font-size:10.5px">#${n.id} · ${esc(n.created_at.slice(0, 10))}</div></div>` +
      `<button class="mini-btn" data-note="${n.id}" data-status="resolved">Решено</button><button class="mini-btn" data-note="${n.id}" data-status="dismissed">Не актуально</button></div>`).join("");
  }
  async function renderAnalyst() {
    const d = await api("/api/analytics/analyst", {}, true);
    const hist = d.history.length > 1 ? `<div class="card"><div class="card-title" style="margin-bottom:8px">Прошлые разборы</div>${d.history.slice(1).map((h) => `<div class="note-row"><div class="t"><b>${esc(dateShort(h.created_at))}</b> · ${esc(h.headline || "без заголовка")}</div></div>`).join("")}</div>` : "";
    return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">ИИ-аналитик</div><div class="card-sub">Читает цифры, находит изменения и причины, помнит прошлые выводы и сверяет с ними свежие данные. Раз в неделю делает разбор сам</div></div></div>` +
      `<div class="row" style="margin-bottom:14px"><button class="btn btn-accent" id="analystRun">🧠 Сделать разбор</button><select class="select" id="analystDays"><option value="7">за 7 дн.</option><option value="14" selected>за 14 дн.</option><option value="30">за 30 дн.</option></select><span class="hint muted" id="analystState"></span></div>` +
      `<div id="analystReport">${reportHtml(d.latest)}</div></div>` +
      `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Память аналитика</div><div class="card-sub">Открытые наблюдения попадают в следующий разбор. Добавь заметку — например, что и когда меняли: аналитик учтёт это в выводах</div></div></div>` +
      `<textarea id="noteText" rows="2" maxlength="400" placeholder="Например: «13.10 выкатили новый онбординг — проверь, вырос ли переход к первой привычке»"></textarea><div class="row" style="margin:8px 0 12px"><button class="btn" id="noteAdd">Добавить заметку</button></div><div id="notesBox">${notesHtml(d.notes)}</div></div>` + hist;
  }

  // ---------------------------------------------------------------------------------------------------------------- достоверность
  async function renderQuality() {
    const q = await api("/api/analytics/quality", {}, true);
    const cov = q.coverage;
    const rows = [
      ["Данные трекера (сессии)", q.tracking_since ? "с " + dateShort(q.tracking_since) : "ещё нет"],
      ["Клики и экраны", q.clicks_since ? "с " + dayShort(q.clicks_since) : "ещё нет"],
      ["Вызовы сервера", q.api_since ? "с " + dayShort(q.api_since) : "ещё нет"],
      ["Дни активности (удержание)", q.active_days_since ? "с " + dayShort(q.active_days_since) : "ещё нет"],
      ["Настоящих людей / всего в базе", num(q.users_real) + " / " + num(q.users_all)],
      ["Охват трекером (активные за неделю)", cov.pct == null ? "мало данных (" + cov.with_sessions + " из " + cov.active_week + ")" : pct(cov.pct) + " (" + cov.with_sessions + " из " + cov.active_week + ")"],
      ["Сессии без закрытия (старше 2 ч)", q.unclosed_pct == null ? "мало данных" : pct(q.unclosed_pct)],
      ["Сессии без версии приложения", q.without_version_pct == null ? "мало данных" : pct(q.without_version_pct)],
      ["Последнее событие", q.last_event ? dateShort(q.last_event) : "—"],
    ].map((r) => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join("");
    const excluded = q.excluded.length ? q.excluded.map((u) => `<div class="note-row"><div class="t"><b>${esc(u.first_name || "Без имени")}</b> ${u.username ? "@" + esc(u.username) : ""} <span class="muted">· ${u.telegram_id}</span></div><button class="mini-btn" data-unexclude="${u.telegram_id}">Вернуть в статистику</button></div>`).join("") : '<div class="empty">Исключённых аккаунтов нет.</div>';
    return `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Насколько можно верить цифрам</div><div class="card-sub">Откуда данные и что в них не попало</div></div></div><div class="scroll-x"><table class="tbl"><tbody>${rows}</tbody></table></div></div>` +
      `<div class="card"><div class="card-title" style="margin-bottom:8px">Как считаем</div>${q.notes.map((n) => `<div class="note">• ${esc(n)}</div>`).join("")}</div>` +
      `<div class="card"><div class="card-head"><div class="grow"><div class="card-title">Не учитывать в статистике</div><div class="card-sub">Тестовые аккаунты (проверка триала, новые аккаунты владельца). Админы исключены всегда. Так воронка и удержание не искажаются</div></div></div>` +
      `<div class="row" style="margin-bottom:12px"><input type="text" id="exclQuery" placeholder="@ник или id" style="flex:1;min-width:160px"><button class="btn" id="exclAdd">Не учитывать</button></div><div id="exclList">${excluded}</div></div>`;
  }

  const RENDER = {
    overview: renderOverview, retention: renderRetention, funnel: renderFunnel, features: renderFeatures, screens: renderScreens,
    versions: renderVersions, health: renderHealth, rhythm: renderRhythm, channels: renderChannels, analyst: renderAnalyst, quality: renderQuality,
  };

  // ---------------------------------------------------------------------------------------------------------------- действия
  async function runAnalyst() {
    if (analystBusy) return;
    analystBusy = true;
    const btn = document.getElementById("analystRun"), state = document.getElementById("analystState");
    btn.disabled = true;
    const started = Date.now();
    const timer = setInterval(() => { state.textContent = "Аналитик читает цифры… " + Math.round((Date.now() - started) / 1000) + " с"; }, 1000);
    try {
      const res = await api("/api/analytics/analyst/run", { method: "POST", body: JSON.stringify({ days: Number(document.getElementById("analystDays").value) }) });
      document.getElementById("analystReport").innerHTML = reportHtml(res.report);
      document.getElementById("notesBox").innerHTML = notesHtml(res.notes || []);
      haptic("medium");
      toast("Разбор готов");
    } catch (e) {
      toast(e.status === 429 ? "Только что был разбор — подожди полминуты" : "Модель сейчас недоступна: " + (e.data && e.data.message ? e.data.message : e.message));
    } finally {
      clearInterval(timer);
      state.textContent = "";
      btn.disabled = false;
      analystBusy = false;
    }
  }

  async function changeNote(id, status) {
    const res = await api("/api/analytics/analyst/notes/" + id, { method: "POST", body: JSON.stringify({ status }) });
    document.getElementById("notesBox").innerHTML = notesHtml(res.notes);
    haptic("light");
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Ссылка скопирована"); } catch (e) {
      const input = document.createElement("input");
      input.value = text; document.body.appendChild(input); input.select();
      try { document.execCommand("copy"); toast("Ссылка скопирована"); } catch (err) { toast("Скопируй вручную"); }
      input.remove();
    }
  }

  async function exclude(query, excluded) {
    try {
      const res = await api("/api/analytics/exclude", { method: "POST", body: JSON.stringify({ query, excluded }) });
      cache.clear();
      toast((excluded ? "Исключён: " : "Возвращён: ") + (res.username ? "@" + res.username : res.telegram_id));
      render();
    } catch (e) {
      if (e.status === 404) toast("Такого человека нет в базе");
      else if (e.status === 409) toast("Несколько совпадений — введи точный @ник или id");
      else toast("Не получилось");
    }
  }

  root.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.tab) {
      haptic("light");
      S.tab = t.dataset.tab; store.set("tab", S.tab); history.replaceState(null, "", "#" + S.tab);
      document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === S.tab)));
      renderFilters(); render();
    } else if (t.id === "refreshBtn") { haptic("light"); cache.clear(); render(); }
    else if (t.id === "adminBtn") { window.location.href = "/admin"; }
    else if (t.id === "retryBtn") { cache.clear(); render(); }
    else if (t.dataset.toggleTable) {
      const card = t.closest("[data-viz]");
      const on = t.getAttribute("aria-pressed") !== "true";
      t.setAttribute("aria-pressed", String(on));
      card.querySelector('[data-pane="chart"]').hidden = on;
      card.querySelector('[data-pane="table"]').hidden = !on;
    } else if (t.dataset.toggleFeat) {
      const row = t.closest(".feat"), body = row.querySelector(".feat-body");
      body.hidden = !body.hidden; t.setAttribute("aria-expanded", String(!body.hidden));
    } else if (t.id === "analystRun") runAnalyst();
    else if (t.id === "noteAdd") {
      const box = document.getElementById("noteText");
      if (!box.value.trim()) return;
      try {
        const res = await api("/api/analytics/analyst/notes", { method: "POST", body: JSON.stringify({ text: box.value }) });
        box.value = ""; document.getElementById("notesBox").innerHTML = notesHtml(res.notes); toast("Заметка сохранена");
      } catch (err) { toast("Не получилось сохранить"); }
    } else if (t.dataset.note) changeNote(t.dataset.note, t.dataset.status).catch(() => toast("Не получилось"));
    else if (t.dataset.copy) copy(t.dataset.copy);
    else if (t.dataset.archive) {
      if (!window.confirm("Убрать канал в архив? Ссылка продолжит работать, но канал пропадёт из списка.")) return;
      try { await api("/api/admin/traffic/" + encodeURIComponent(t.dataset.archive) + "/archive", { method: "POST" }); cache.clear(); render(); } catch (err) { toast("Не получилось"); }
    } else if (t.id === "chCreate") {
      const title = document.getElementById("chTitle").value.trim();
      if (title.length < 2) { toast("Напиши название канала"); return; }
      try {
        await api("/api/admin/traffic", { method: "POST", body: JSON.stringify({ title, kind: document.getElementById("chKind").value, note: document.getElementById("chNote").value }) });
        cache.clear(); haptic("medium"); toast("Ссылка создана"); render();
      } catch (err) { toast(err.data && err.data.error === "code_taken" ? "Такое название уже есть" : "Не получилось создать"); }
    } else if (t.id === "exclAdd") {
      const q = document.getElementById("exclQuery").value.trim();
      if (q) exclude(q, true);
    } else if (t.dataset.unexclude) exclude(t.dataset.unexclude, false);
  });

  root.addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "daysSel") { S.days = Number(t.value); store.set("days", S.days); render(); }
    else if (t.id === "srcSel") { S.source = t.value; store.set("source", S.source); render(); }
    else if (t.id === "sortSel") { S.sort = t.value; store.set("sort", S.sort); render(); }
    else if (t.id === "screenSel") { S.screenFilter = t.value; render(); }
  });

  // ---------------------------------------------------------------------------------------------------------------- запуск
  function gate(message, icon) {
    root.innerHTML = `<div class="gate"><div class="gate-icon">${icon}</div><div style="font:700 14px Inter,sans-serif;color:#D8D4E6">${esc(message)}</div></div>`;
  }

  async function boot() {
    if (!initData()) { gate("Открой эту страницу внутри Telegram", "🔐"); return; }
    try {
      const src = await api("/api/analytics/sources");
      S.sources = src.sources;
    } catch (e) {
      gate(e.status === 403 ? "⛔ Доступ только для администраторов" : "Не получилось открыть аналитику", "⛔");
      return;
    }
    frame();
    render();
  }

  boot();
})();
