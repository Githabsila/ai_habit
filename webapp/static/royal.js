/* Оформление «Royal Gold» (db/style_mode.py, решение владельца 28.10.2026). Подключается только тем, кому включён режим (см. index.html и app.js::applyRoyalStyle) — у остальных
   этого файла нет вообще, прежний вид не затронут.
   Что делает: один раз проходит по правилам стилей страницы и перекрашивает «фиолетовую» гамму в золотую (оттенок, насыщенность смягчена, светлота сохранена), а тёмные
   холодные фоны делает тёплыми чёрными; белый текст на ставшем светлым золотом фоне темнеет, чтобы читался. Изменения живут только в памяти страницы (CSSOM): выключить — значит
   не загружать этот файл (или перезагрузить страницу). Работает и на странице чата. Без CSS-фильтров и постоянных анимаций. */
(function () {
  "use strict";
  if (window.__royalApplied) return;

  const SELF = (document.currentScript && document.currentScript.src) || "/static/royal.js?v=20261028_ROYAL_V1";
  const INK = "#1B1408";                                                       // тёмный текст на золотой плашке

  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    let h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
    return [h, s, l];
  }

  function hslToRgb(h, s, l) {
    const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
    let r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; } else if (h < 180) { g = c; b = x; } else if (h < 240) { g = x; b = c; } else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
    return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
  }

  // Один цвет → золотой аналог или null (цвет не трогаем).
  function recolor(r, g, b) {
    const [h, s, l] = rgbToHsl(r, g, b);
    if (l > 0.97 || l < 0.015) return null;
    if (h >= 175 && h <= 300 && s > 0.06) {                                  // фиолетовые, синие и бирюзовые акценты, свечения, градиенты, серо-лиловый текст → золото
      const nh = 40 + (h - 258) * 0.05, ns = s < 0.35 ? s * 0.9 + 0.04 : Math.min(0.8, 0.34 + s * 0.5);
      return hslToRgb(nh, ns, l);
    }
    if (h > 300 && h <= 345 && s > 0.25) {                                   // розовые и пурпурные → розовое золото
      return hslToRgb(22, Math.min(0.7, 0.3 + s * 0.4), l);
    }
    return null;
  }

  const COLOR_RE = /#([0-9a-fA-F]{6})\b|#([0-9a-fA-F]{3})\b|rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*(?:[,/]\s*([\d.]+%?)\s*)?\)/g;

  // Текст объявлений правила → новый текст (или null, если красить нечего). Правим весь cssText сразу: так попадают и сокращённые записи с var(), вроде
  // `border: var(--w) solid rgba(139,92,246,.3) !important`, которые по отдельным свойствам не прочитать.
  function mapValue(text) {
    let changed = false;
    const out = text.replace(COLOR_RE, (all, hex6, hex3, rr, gg, bb, alpha) => {
      let r, g, b;
      if (hex6) { r = parseInt(hex6.slice(0, 2), 16); g = parseInt(hex6.slice(2, 4), 16); b = parseInt(hex6.slice(4, 6), 16); }
      else if (hex3) { r = parseInt(hex3[0] + hex3[0], 16); g = parseInt(hex3[1] + hex3[1], 16); b = parseInt(hex3[2] + hex3[2], 16); }
      else { r = +rr; g = +gg; b = +bb; }
      const next = recolor(r, g, b);
      if (!next) return all;
      changed = true;
      const [nr, ng, nb] = next;
      if (hex6 || hex3) return "#" + [nr, ng, nb].map((v) => v.toString(16).padStart(2, "0")).join("");
      return alpha !== undefined ? `rgba(${nr}, ${ng}, ${nb}, ${alpha})` : `rgb(${nr}, ${ng}, ${nb})`;
    });
    return changed ? out : null;
  }

  function processStyle(style) {
    const text = style.cssText;
    if (!text) return 0;
    const next = mapValue(text);
    if (next === null) return 0;
    style.cssText = next;
    return 1;
  }

  function walk(rules, fn) {
    let count = 0;
    for (const rule of rules) {
      if (rule.style) count += fn(rule.style);
      if (rule.cssRules && rule.cssRules.length) count += walk(rule.cssRules, fn);
    }
    return count;
  }

  // Второй проход: белый текст на плотном светлом золотом фоне (в том числе когда фон задан переменной, например background: var(--accent)) не читается — делаем его тёмным.
  const WHITE = /^(#fff|#ffffff|white|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))$/i;

  function firstColor(text) {
    const m = /#([0-9a-fA-F]{6})\b|rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*(?:[,/]\s*([\d.]+)\s*)?/.exec(text);
    if (!m) return null;
    if (m[1]) return { rgb: [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)], a: 1 };
    return { rgb: [+m[2], +m[3], +m[4]], a: m[5] === undefined ? 1 : +m[5] };
  }

  let rootStyle = null;
  function resolveVars(text, depth) {
    if (!text || depth > 3) return text;
    rootStyle = rootStyle || getComputedStyle(document.documentElement);
    return text.replace(/var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)/g, (all, name) => {
      const value = rootStyle.getPropertyValue(name).trim();
      return value ? resolveVars(value, depth + 1) : all;
    });
  }

  function contrastStyle(style) {
    const color = style.getPropertyValue("color");
    if (!color || !WHITE.test(color.trim())) return 0;
    const bg = style.getPropertyValue("background") || style.getPropertyValue("background-color") || style.getPropertyValue("background-image");
    if (!bg || bg.indexOf("url(") >= 0) return 0;
    const first = firstColor(resolveVars(bg, 0));
    if (!first || first.a < 0.75) return 0;                                    // полупрозрачный тон поверх тёмного — текст остаётся светлым
    const [, s, l] = rgbToHsl(first.rgb[0], first.rgb[1], first.rgb[2]);
    if (l < 0.55 || s < 0.3) return 0;
    style.setProperty("color", INK, style.getPropertyPriority("color"));
    return 1;
  }

  function run() {
    if (window.__royalApplied) return;
    window.__royalApplied = true;
    let total = 0;
    const sheets = [];
    for (const sheet of Array.from(document.styleSheets)) {
      let rules;
      try { rules = sheet.cssRules; } catch (_) { continue; }                  // чужие таблицы (шрифты) читать нельзя — они и не нужны
      sheets.push(rules);
      total += walk(rules, processStyle);
    }
    for (const rules of sheets) total += walk(rules, contrastStyle);
    document.documentElement.setAttribute("data-royal", String(total));
    addLinks();
  }

  // Дополнения: шрифт с засечками для заголовков (если Google Fonts недоступен, остаётся Georgia) и royal.css рядом с этим скриптом.
  function addLinks() {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = SELF.replace(/royal\.js/, "royal.css");
    document.head.appendChild(css);
    const font = document.createElement("link");
    font.rel = "stylesheet";
    font.href = "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800&subset=cyrillic&display=swap";
    document.head.appendChild(font);
  }

  // Файл подключается из <head> после основной таблицы стилей, поэтому она уже загружена и красить можно сразу, не дожидаясь картинок и шрифтов (иначе мелькнёт фиолетовый).
  function stylesReady() {
    return Array.from(document.styleSheets).some((sheet) => {
      try { return sheet.cssRules.length > 20; } catch (_) { return false; }
    });
  }

  window.RoyalGold = { recolor, mapValue, run, rgbToHsl, hslToRgb };
  if (stylesReady()) run(); else window.addEventListener("load", run);
})();
