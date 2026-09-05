const { useState, useEffect, useRef, useCallback } = React;
try {
    const lowPower = (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) || (navigator.deviceMemory && navigator.deviceMemory <= 4) || (navigator.connection && navigator.connection.saveData);
    if (lowPower) document.documentElement.classList.add('performance-lite');
} catch (e) {}
const tg = window.Telegram.WebApp;
// Тема (светлая/тёмная) — раньше эта страница ВСЕГДА была тёмной,
// независимо от выбора в настройках основного приложения (Профиль →
// Тема оформления): --bg был захардкожен, body{background:...#06040b}
// не читал ничего из state.settings.color_mode. Header/toolbar/composer/
// пузыри сообщений намеренно ОСТАЮТСЯ тёмным "стеклом" в обеих темах
// (у них у всех непрозрачный тёмный фон сам по себе — см. комментарий
// у :root[data-mode="light"] body ниже) — меняется только сам холст
// (body) и текст приветственного экрана, который раньше лежал прямо на
// холсте без тёмной подложки.
// localStorage — синхронный кэш, применяется СРАЗУ (до первого рендера),
// чтобы не было вспышки неправильной темы; /api/bootstrap ниже лишь
// подтверждает/поправляет его в фоне на случай, если тема поменялась
// в основном приложении с прошлого открытия чата.
const COLOR_MODE_CACHE_KEY = 'adam_color_mode';
function applyColorMode(mode) {
    const isLight = mode === 'light';
    document.documentElement.setAttribute('data-mode', isLight ? 'light' : 'dark');
    try {
        tg.setBackgroundColor(isLight ? '#EFEAF9' : '#07040E');
    }
    catch (e) { }
}
let cachedColorMode = 'dark';
try {
    cachedColorMode = localStorage.getItem(COLOR_MODE_CACHE_KEY) || 'dark';
}
catch (e) { }
applyColorMode(cachedColorMode);
tg.ready();
try {
    tg.expand();
}
catch (e) { }
try {
    // Хедер и системная шапка Telegram намеренно остаются тёмными в обеих
    // темах — .header это непрозрачная тёмная "стеклянная" карточка сама
    // по себе (см. ADAM SIGNATURE CHAT REDESIGN в CSS), а не часть холста.
    tg.setHeaderColor('#07040E');
}
catch (e) { }
fetch('/api/bootstrap', { headers: { 'X-Telegram-Init-Data': tg.initData } })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
    const mode = data && data.settings && data.settings.color_mode;
    if (mode && mode !== cachedColorMode) {
        applyColorMode(mode);
    }
    if (mode) {
        try {
            localStorage.setItem(COLOR_MODE_CACHE_KEY, mode);
        }
        catch (e) { }
    }
})
    .catch(() => { });
function applySafeArea() {
    const top = (tg.contentSafeAreaInset && tg.contentSafeAreaInset.top || 0)
        + (tg.safeAreaInset && tg.safeAreaInset.top || 0);
    document.documentElement.style.setProperty('--tg-safe-top', top + 'px');
}
applySafeArea();
try {
    tg.onEvent('safeAreaChanged', applySafeArea);
    tg.onEvent('contentSafeAreaChanged', applySafeArea);
}
catch (e) { }
function goHome() {
    try {
        tg.HapticFeedback.impactOccurred('light');
    }
    catch (e) { }
    window.location.href = '/';
}
try {
    tg.BackButton.show();
    tg.onEvent('backButtonClicked', goHome);
}
catch (e) { }
const CHAT_STORAGE_KEY = 'adam_chat_history';
// Чинит "кривые" сообщения — перенос строки, случайно воткнутый прямо
// в середину слова (некоторые мобильные клавиатуры вставляют его вместо
// отправки, событие insertLineBreak — теперь перехватывается на вводе,
// но старые сообщения, уже сохранённые в sessionStorage/истории ДО этого
// фикса, так и останутся "Д\nа" навсегда без этой чистки при отрисовке).
// Схлопывает перенос строки, только если по обе стороны от него нет
// пробела — то есть он реально рвёт одно слово пополам, а не отделяет
// два разных предложения/абзаца (настоящий Shift+Enter трогать нельзя).
function fixBrokenText(text) {
    if (typeof text !== 'string' || !text)
        return text;
    // ВАЖНО: было (\S)\n+(\S) — "любой не-пробельный символ" включает
    // знаки препинания и цифры, поэтому легитимные переносы строк перед
    // нумерованным/маркированным списком в ответах ADAM ("...работают:\n\n1. ...")
    // тоже съедались ("работают:1. ..."), ломая markdown-разметку (см.
    // renderMarkdown выше). \p{L} (буква) вместо \S — тот же самый фикс
    // разорванного слова ("Д\nа" → "Да", обе стороны буквы), но больше не
    // трогает перенос после ":"/"."/цифры перед пунктом списка.
    return text.replace(/(\p{L})\n+(\p{L})/gu, '$1$2');
}
function loadStoredMessages() {
    try {
        const raw = sessionStorage.getItem(CHAT_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.map(m => ({ ...m, text: fixBrokenText(m.text) })) : [];
    }
    catch (e) {
        return [];
    }
}
function saveStoredMessages(messages) {
    try {
        sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages));
    }
    catch (e) { }
}
function formatTime(value) {
    if (!value)
        return new Date().toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
    const d = new Date(String(value).replace(' ', 'T') + (String(value).includes('Z') ? '' : 'Z'));
    return isNaN(d.getTime()) ? String(value).slice(-5) : d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
}
function vibrate(type = 'light') {
    try {
        tg.HapticFeedback.impactOccurred(type);
    }
    catch (e) { }
}
// Раньше ответ ADAM рендерился голым pre-wrap текстом — звёздочки/дефисы
// из markdown-разметки, которую генерирует модель, показывались буквально
// вместо жирного/списков. Экранируем HTML СНАЧАЛА (защита от prompt-
// injection в стиле "ответь только вот таким HTML/<script>") и только
// потом применяем разметку к уже безопасному тексту — dangerouslySetInnerHTML
// ниже никогда не видит сырой, неэкранированный ввод.
function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}
function renderMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    const lines = html.split('\n');
    const out = [];
    let listType = null;
    for (const line of lines) {
        const bullet = line.match(/^[-•]\s+(.*)/);
        const numbered = line.match(/^\d+[.)]\s+(.*)/);
        if (bullet || numbered) {
            const type = bullet ? 'ul' : 'ol';
            if (listType !== type) {
                if (listType) out.push(`</${listType}>`);
                out.push(`<${type}>`);
                listType = type;
            }
            out.push(`<li>${(bullet || numbered)[1]}</li>`);
        }
        else {
            if (listType) {
                out.push(`</${listType}>`);
                listType = null;
            }
            out.push(line);
        }
    }
    if (listType)
        out.push(`</${listType}>`);
    return out.join('\n');
}
// Имитация печатающегося ответа — раньше текст появлялся одним куском
// сразу после "Формирую ответ", теперь проявляется постепенно, что
// ощущается быстрее и живее. НЕ настоящий стриминг токенов от модели
// (сервер по-прежнему отдаёт готовый ответ целиком одним запросом —
// для реального стриминга пришлось бы переписывать весь AI-пайплайн
// см. multi_agent.py, включая подсчёт квоты и обрезку по finish_reason,
// которые сейчас требуют полного текста) — чисто визуальный эффект на
// уже полученном тексте. Во время анимации показываем экранированный
// голый текст (частичная markdown-разметка выглядела бы криво —
// незакрытый "**" на середине слова), полное форматирование "проявляется"
// только по завершении анимации.
function TypewriterText({ id, text, active }) {
    const [shown, setShown] = useState(active ? '' : text);
    useEffect(() => {
        if (!active) {
            setShown(text);
            return;
        }
        const reduceMotion = document.documentElement.classList.contains('performance-lite')
            || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        if (reduceMotion) {
            setShown(text);
            return;
        }
        let i = 0;
        const step = Math.max(1, Math.ceil(text.length / 40));
        const timer = setInterval(() => {
            i += step;
            if (i >= text.length) {
                setShown(text);
                clearInterval(timer);
            }
            else {
                setShown(text.slice(0, i));
            }
        }, 18);
        return () => clearInterval(timer);
        // Намеренно только [id] — при смене текста того же сообщения (не
        // бывает в реальности) не хотим перезапускать анимацию заново.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [id]);
    const complete = shown.length >= text.length;
    const html = complete ? renderMarkdown(text) : escapeHtml(shown);
    return React.createElement("div", { style: { whiteSpace: 'pre-wrap' }, dangerouslySetInnerHTML: { __html: html } });
}
// См. _FEEDBACK_REASONS в handlers/ai.py — те же формулировки, чтобы
// причины дизлайка из бота и из Mini App попадали в одну и ту же
// админ-статистику в одинаковом виде.
const FEEDBACK_REASONS = {
    long: 'ответ был слишком длинным/затянутым',
    off: 'ответ был не по теме, что нужно',
    unclear: 'ответ был непонятно объяснён',
};
function AiChat() {
    const [messages, setMessages] = useState(loadStoredMessages);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [throttle, setThrottle] = useState(null);
    const [quota, setQuota] = useState(null);
    const [listening, setListening] = useState(false);
    const [showTools, setShowTools] = useState(false);
    const [showEmoji, setShowEmoji] = useState(false);
    const [emojiTab, setEmojiTab] = useState('smile');
    const [copiedId, setCopiedId] = useState(null);
    const [toast, setToast] = useState('');
    const [online, setOnline] = useState(navigator.onLine);
    const messagesEnd = useRef(null);
    const textareaRef = useRef(null);
    const recognitionRef = useRef(null);
    const scroll = (behavior = 'smooth') => messagesEnd.current?.scrollIntoView({ behavior, block: 'end' });
    useEffect(() => { scroll('auto'); }, []);
    useEffect(() => { saveStoredMessages(messages); scroll(); }, [messages]);
    const loadHistory = useCallback(async () => {
        try {
            const res = await fetch('/api/ai/history?init_data=' + encodeURIComponent(tg.initData) + '&limit=80');
            if (!res.ok)
                return;
            const data = await res.json();
            if (!Array.isArray(data.history))
                return;
            const normalized = data.history.map((m, i) => ({
                id: m.id || `history-${i}`,
                role: m.role === 'assistant' ? 'assistant' : 'user',
                text: fixBrokenText(m.message || ''),
                time: formatTime(m.timestamp || m.created_at),
                canRate: m.role === 'assistant'
            }));
            if (normalized.length)
                setMessages(normalized);
        }
        catch (e) { }
    }, []);
    useEffect(() => {
        fetch('/api/ai/quota', { headers: { 'X-Telegram-Init-Data': tg.initData } })
            .then(r => r.json()).then(setQuota).catch(() => { });
        if (!loadStoredMessages().length)
            loadHistory();
        const onOnline = () => setOnline(true), onOffline = () => setOnline(false);
        window.addEventListener('online', onOnline);
        window.addEventListener('offline', onOffline);
        return () => { window.removeEventListener('online', onOnline); window.removeEventListener('offline', onOffline); };
    }, [loadHistory]);
    const resizeInput = () => {
        const el = textareaRef.current;
        if (!el)
            return;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 132) + 'px';
    };
    const sendText = useCallback(async (rawText) => {
        const text = fixBrokenText((rawText || '').trim());
        if (!text || loading || throttle)
            return;
        vibrate('light');
        setInput('');
        if (textareaRef.current)
            textareaRef.current.style.height = '40px';
        setMessages(p => [...p, { id: Date.now(), role: 'user', text, time: formatTime() }]);
        setLoading(true);
        try {
            const res = await fetch('/api/ai/chat', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ init_data: tg.initData, message: text })
            });
            const data = await res.json();
            if (!res.ok) {
                if (data.quota)
                    setQuota(data.quota);
                if (res.status === 429) {
                    setThrottle(data.wait_seconds);
                    setTimeout(() => setThrottle(null), data.wait_seconds * 1000);
                }
                setMessages(p => [...p, { id: Date.now(), role: 'system', text: data.message || 'Не получилось выполнить запрос.', type: 'error' }]);
                vibrate('heavy');
                return;
            }
            if (data.quota)
                setQuota(data.quota);
            setMessages(p => [...p, { id: data.message_id, role: 'assistant', text: data.answer, time: formatTime(), isCrisis: data.is_crisis, habit: data.suggested_habit, canRate: true, sourcePrompt: text, isNew: true }]);
            vibrate('light');
        }
        catch (e) {
            setMessages(p => [...p, { id: Date.now(), role: 'system', text: online ? 'Не удалось получить ответ ADAM. Попробуй ещё раз.' : 'Нет соединения с интернетом.', type: 'error' }]);
            vibrate('heavy');
        }
        finally {
            setLoading(false);
        }
    }, [loading, throttle, online]);
    const sendMsg = () => sendText(input);
    const EMOJIS = {
        smile: '😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 🙃 😉 😌 😍 🥰 😘 😗 😙 😚 😋 😛 😜 🤪 🤨 🧐 🤓 😎 🤩 🥳 😏 😭 😂 😤 😱 😴 🤔 🤗 🤭 🤫 🤠 🫡',
        hearts: '❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 💕 💞 💓 💗 💖 💘 💝 💟 ❣️ 💔 ❤️‍🔥 ❤️‍🩹',
        hands: '👍 👎 👌 ✌️ 🤞 🤟 🤘 🤙 👋 🫶 🙌 👏 👐 🤝 🙏 ✍️ 💪 🫵 ☝️ ✋ 🖐️',
        fun: '🔥 ✨ ⭐ 🌟 💫 ⚡ 🎉 🎊 🎯 🚀 🧠 💡 🏆 🥇 🎮 🎵 🎨 ☕ 🍕 🍔 🍩 🥤 🦄 🐸 🐼 🐱 🐶 🦊 🐻'
    };
    const addEmoji = (emoji) => {
        setInput(v => v + emoji);
        setShowEmoji(false);
        requestAnimationFrame(resizeInput);
        vibrate('light');
    };
    const showToast = (text) => {
        setToast(text);
        setTimeout(() => setToast(''), 1500);
    };
    // Бэкенд (/api/ai/feedback, webapp/routes_ai_miniapp.py) был полностью
    // готов, но ни одна кнопка в интерфейсе его не вызывала — 👍/👎 нигде
    // не отображались. save_ai_feedback() делает UPSERT по (message_id,
    // user_id), так что повторный тап меняет оценку, а не дублирует её —
    // поэтому можно не блокировать кнопку после первого клика.
    const rateMessage = async (id, rating, reason) => {
        setMessages(p => p.map(m => m.id === id ? { ...m, rated: rating } : m));
        vibrate('light');
        try {
            const body = { init_data: tg.initData, message_id: id, rating };
            if (reason) body.reason = reason;
            const res = await fetch('/api/ai/feedback', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!res.ok) throw new Error();
        }
        catch (e) {
            // Не критично — просто откатываем визуальное состояние, чтобы
            // не врать пользователю, что оценка сохранилась.
            setMessages(p => p.map(m => m.id === id ? { ...m, rated: null } : m));
        }
    };
    // При 👎 необязательно спрашиваем причину — те же формулировки, что и
    // в боте (см. FEEDBACK_REASONS выше), чтобы попадать в одну статистику.
    // tg.showPopup поддерживает максимум 3 кнопки — закрытие попапа без
    // выбора (или платформа без поддержки showPopup) само по себе
    // считается "пропустить", оценка всё равно сохраняется без причины.
    const rateDown = (id) => {
        try {
            tg.showPopup({
                title: 'Что не так?',
                message: 'Необязательно — поможет сделать ADAM лучше',
                buttons: [
                    { id: 'long', type: 'default', text: 'Длинно' },
                    { id: 'off', type: 'default', text: 'Не по теме' },
                    { id: 'unclear', type: 'default', text: 'Непонятно' },
                ],
            }, (buttonId) => rateMessage(id, 'down', FEEDBACK_REASONS[buttonId]));
        }
        catch (e) {
            rateMessage(id, 'down');
        }
    };
    const copyMessage = async (text, id) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopiedId(id);
            setTimeout(() => setCopiedId(null), 1400);
            vibrate('light');
        }
        catch (e) { }
    };
    const regenerate = (message) => {
        if (message?.sourcePrompt)
            sendText(message.sourcePrompt);
    };
    const addHabit = async (id, habit) => {
        try {
            const res = await fetch('/api/ai/habit/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ init_data: tg.initData, habit_title: habit }) });
            if (!res.ok)
                throw new Error();
            setMessages(p => p.map(m => m.id === id ? { ...m, habit: null } : m));
            tg.showPopup({ title: 'Привычка добавлена', message: `«${habit}» добавлена в твой план.`, buttons: [{ type: 'ok' }] });
            vibrate('medium');
        }
        catch (e) {
            tg.showAlert('Не удалось добавить привычку. Попробуй ещё раз.');
        }
    };
    const getTip = async () => {
        if (loading)
            return;
        setLoading(true);
        vibrate('light');
        try {
            const res = await fetch('/api/ai/tip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ init_data: tg.initData }) });
            const data = await res.json();
            setMessages(p => [...p, { id: Date.now(), role: 'assistant', text: '✦ Совет дня\n\n' + (data.tip || 'Сделай сегодня один маленький шаг в сторону своей цели.'), time: formatTime(), canRate: false, isNew: true }]);
        }
        catch (e) {
            setMessages(p => [...p, { id: Date.now(), role: 'system', text: 'Не удалось получить совет дня.', type: 'error' }]);
        }
        finally {
            setLoading(false);
        }
    };
    // Раньше можно было только "Повторить" последний вопрос — сбросить
    // весь видимый диалог было нельзя, старые сообщения копились до
    // бесконечности. Чистит только ЭКРАН (sessionStorage + локальное
    // состояние) — история на сервере (get_ai_history) не трогается,
    // так что долгосрочная память ADAM о пользователе не теряется,
    // просто следующий openHistory-запрос при перезаходе всё равно
    // подтянет её обратно. Это осознанный выбор: "чистый экран" ощущается
    // как новый диалог, не требуя отдельного эндпоинта на удаление истории.
    const startNewDialog = () => {
        const clear = () => {
            setMessages([]);
            try { sessionStorage.removeItem(CHAT_STORAGE_KEY); } catch (e) { }
            vibrate('medium');
        };
        if (!messages.length) { clear(); return; }
        try {
            tg.showConfirm('Начать новый диалог? Текущий останется в истории, но исчезнет с экрана.', (ok) => { if (ok) clear(); });
        }
        catch (e) {
            if (window.confirm('Начать новый диалог? Текущий останется в истории, но исчезнет с экрана.')) clear();
        }
    };
    const quickPrompts = [
        ['☀️', 'План на день', 'Составим план и расставим приоритеты'],
        ['🧠', 'Разбор ситуации', 'Разберём проблему и найдём решение'],
        ['⚡', 'Главный фокус', 'Определим самое важное действие'],
        ['🎯', 'Помощь с задачей', 'Разделим задачу на понятные шаги'],
        ['💡', 'Новая идея', 'Придумаем варианты и сильное решение'],
        ['🌙', 'Итоги дня', 'Разберём результат и следующий шаг']
    ];
    const startVoice = () => {
        const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SR) {
            tg.showAlert('Голосовой ввод не поддерживается этим браузером.');
            return;
        }
        if (listening) {
            recognitionRef.current?.stop();
            return;
        }
        const r = new SR();
        recognitionRef.current = r;
        r.lang = 'ru-RU';
        r.interimResults = true;
        r.continuous = false;
        r.onstart = () => { setListening(true); vibrate('medium'); };
        r.onresult = e => { let text = ''; for (let i = e.resultIndex; i < e.results.length; i++)
            text += e.results[i][0].transcript; setInput(text); setTimeout(resizeInput, 0); };
        r.onerror = () => setListening(false);
        r.onend = () => setListening(false);
        r.start();
    };
    return React.createElement("div", { className: "app-container" },
        React.createElement("div", { className: "header" },
            React.createElement("div", { className: "header-title" },
                React.createElement("div", { className: "header-title-icon" },
                    React.createElement("img", { src: "/static/assets/adam-avatar.webp", alt: "ADAM" })),
                React.createElement("div", { className: "header-title-copy" },
                    React.createElement("div", { className: "header-title-top" },
                        React.createElement("div", { className: "header-brand" }, "ADAM"),
                        quota && React.createElement("div", { className: `quota-panel ${quota.pro ? 'quota-panel--pro' : ''}` },
                            React.createElement("span", null, "\u2726"),
                            React.createElement("strong", null, quota.pro ? 'PRO' : 'STANDARD'),
                            React.createElement("span", null,
                                React.createElement("b", null, quota.remaining),
                                "/",
                                quota.limit))),
                    React.createElement("div", { className: "header-status" },
                        React.createElement("span", { className: "status-dot" }),
                        online ? 'онлайн' : 'нет соединения'))),
            React.createElement("div", { className: "header-buttons" },
                React.createElement("button", { className: "icon-btn premium-icon", onClick: getTip, title: "\u0421\u043E\u0432\u0435\u0442 \u0434\u043D\u044F" },
                    React.createElement("span", null, "\u2726")),
                React.createElement("button", { className: "icon-btn premium-icon", onClick: startNewDialog, title: "\u041D\u043E\u0432\u044B\u0439 \u0434\u0438\u0430\u043B\u043E\u0433" },
                    React.createElement("span", null, "\uD83E\uDDF9")),
                React.createElement("button", { className: "icon-btn premium-icon home-nav-btn", onClick: goHome, title: "\u0412 \u043C\u0435\u043D\u044E" },
                    React.createElement("span", null, "\uD83C\uDFE0")))),
        React.createElement("div", { className: "chat-toolbar" },
            React.createElement("div", { className: "chat-toolbar-copy" },
                React.createElement("span", { className: "chat-toolbar-orb" }, "\u2726"),
                React.createElement("div", null,
                    React.createElement("div", { className: "chat-toolbar-title" }, "\u041B\u0438\u0447\u043D\u044B\u0439 \u043D\u0430\u0441\u0442\u0430\u0432\u043D\u0438\u043A"),
                    React.createElement("div", { className: "chat-toolbar-subtitle" }, "ADAM \u00B7 \u0443\u043C\u043D\u044B\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044F"))),
            React.createElement("button", { className: `quick-toggle ${showTools ? 'is-open' : ''}`, onClick: () => { setShowTools(v => !v); vibrate('light'); } },
                React.createElement("span", { className: "quick-toggle-icon" }, showTools ? '×' : '✦'),
                React.createElement("span", null, showTools ? 'Закрыть' : 'Быстрые действия'),
                React.createElement("span", { className: "quick-toggle-chevron" }, "\u2304"))),
        showTools && React.createElement("div", { className: "quick-prompts" },
            React.createElement("div", { className: "quick-prompts-head" },
                React.createElement("span", null, "\u0427\u0442\u043E \u0441\u0434\u0435\u043B\u0430\u0442\u044C \u0441\u0435\u0439\u0447\u0430\u0441?"),
                React.createElement("small", null, "\u041E\u0434\u0438\u043D \u0442\u0430\u043F \u2014 \u0438 ADAM \u043D\u0430\u0447\u043D\u0451\u0442")),
            React.createElement("div", { className: "quick-grid" }, quickPrompts.map(([icon, label, desc]) => React.createElement("button", { key: label, onClick: () => sendText(label) },
                React.createElement("span", { className: "quick-icon" }, icon),
                React.createElement("span", { className: "quick-copy" },
                    React.createElement("b", null, label),
                    React.createElement("small", null, desc)),
                React.createElement("span", { className: "quick-arrow" }, "\u2192"))))),
        React.createElement("div", { className: "messages-container" }, messages.length === 0 ? React.createElement("div", { className: "empty-state" },
            React.createElement("div", { className: "empty-icon" },
                React.createElement("img", { src: "/static/assets/adam-avatar.webp", alt: "ADAM" })),
            React.createElement("div", { className: "empty-title" }, "\u041F\u0440\u0438\u0432\u0435\u0442, \u044F ADAM \uD83D\uDC4B"),
            React.createElement("div", { className: "empty-text" }, "\u042F \u043F\u043E\u043C\u043E\u0433\u0430\u044E \u043F\u043B\u0430\u043D\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u0434\u0435\u043D\u044C, \u0440\u0430\u0431\u043E\u0442\u0430\u0442\u044C \u0441 \u043F\u0440\u0438\u0432\u044B\u0447\u043A\u0430\u043C\u0438, \u0440\u0430\u0437\u0431\u0438\u0440\u0430\u0442\u044C \u043F\u0440\u043E\u0431\u043B\u0435\u043C\u044B \u0438 \u0432\u0438\u0434\u0435\u0442\u044C \u0442\u0432\u043E\u0439 \u043F\u0440\u043E\u0433\u0440\u0435\u0441\u0441."),
            React.createElement("div", { className: "welcome-prompts" }, quickPrompts.slice(0, 3).map(([icon, label]) => React.createElement("button", { key: label, onClick: () => sendText(label) },
                React.createElement("span", null, icon),
                label)))) : React.createElement(React.Fragment, null,
            React.createElement("div", { className: "messages-spacer", "aria-hidden": "true" }),
            messages.map(m => React.createElement("div", { key: m.id },
                m.type === 'error' && React.createElement("div", { className: "error-card" },
                    "\u26A0\uFE0F ",
                    m.text),
                m.role === 'user' && React.createElement("div", { className: "message user" },
                    React.createElement("div", null,
                        React.createElement("div", { className: "message-bubble" }, m.text),
                        React.createElement("div", { className: "message-time user-time" }, m.time))),
                m.role === 'assistant' && React.createElement("div", { className: "message assistant" },
                    React.createElement("div", { className: "msg-avatar" },
                        React.createElement("img", { src: "/static/assets/adam-avatar.webp", alt: "" })),
                    React.createElement("div", { className: "assistant-message-wrap" },
                        React.createElement("div", { className: "msg-sender" },
                            React.createElement("span", { className: "msg-sender-dot" }),
                            "ADAM"),
                        m.isCrisis && React.createElement("div", { className: "crisis-alert" }, "\u26A0\uFE0F \u0412\u0430\u0436\u043D\u043E"),
                        React.createElement("div", { className: "message-bubble" },
                            React.createElement(TypewriterText, { key: 'tw-' + m.id, id: m.id, text: m.text, active: !!m.isNew })),
                        React.createElement("div", { className: "message-meta" },
                            React.createElement("span", null, m.time),
                            React.createElement("div", { className: "message-mini-actions" },
                                m.canRate !== false && React.createElement(React.Fragment, null,
                                    React.createElement("button", { className: `rate-btn ${m.rated === 'up' ? 'is-active' : ''}`, onClick: () => rateMessage(m.id, 'up'), title: "Полезный ответ" }, "\uD83D\uDC4D"),
                                    React.createElement("button", { className: `rate-btn ${m.rated === 'down' ? 'is-active' : ''}`, onClick: () => rateDown(m.id), title: "Не помогло" }, "\uD83D\uDC4E")),
                                React.createElement("button", { onClick: () => { copyMessage(m.text, m.id); showToast('Скопировано ✨'); } }, copiedId === m.id ? '✓' : '⧉'),
                                !m.isCrisis && React.createElement("button", { onClick: () => regenerate(m), disabled: !m.sourcePrompt }, "\u21BB"))),
                        m.habit && React.createElement("div", { className: "habit-suggestion" },
                            React.createElement("div", null,
                                React.createElement("span", { className: "habit-label" }, "\u041F\u0420\u0415\u0414\u041B\u041E\u0416\u0415\u041D\u0418\u0415"),
                                React.createElement("strong", null,
                                    "\u2795 ",
                                    m.habit)),
                            React.createElement("button", { className: "add-habit-btn", onClick: () => addHabit(m.id, m.habit) }, "\u0414\u043E\u0431\u0430\u0432\u0438\u0442\u044C")))))),
            loading && React.createElement("div", { className: "message assistant" },
                React.createElement("div", { className: "thinking" },
                    React.createElement("div", { className: "thinking-avatar" },
                        React.createElement("img", { src: "/static/assets/adam-avatar.webp", alt: "" })),
                    React.createElement("div", null,
                        React.createElement("div", { className: "thinking-title typing-shimmer" }, "\u0424\u043E\u0440\u043C\u0438\u0440\u0443\u044E \u043E\u0442\u0432\u0435\u0442"),
                        React.createElement("div", { className: "thinking-dots" },
                            React.createElement("i", null),
                            React.createElement("i", null),
                            React.createElement("i", null))))),
            React.createElement("div", { ref: messagesEnd }))),
        throttle && React.createElement("div", { className: "throttle-warning" },
            "\u23F3 \u041F\u043E\u0434\u043E\u0436\u0434\u0438 ",
            throttle,
            " \u0441\u0435\u043A."),
        React.createElement("div", { className: "input-area" },
            showEmoji && React.createElement("div", { className: "emoji-panel" },
                React.createElement("div", { className: "emoji-tabs" }, Object.keys(EMOJIS).map(t => React.createElement("button", { key: t, className: `emoji-tab ${emojiTab === t ? 'active' : ''}`, onClick: () => setEmojiTab(t) }, t === 'smile' ? '😊' : t === 'hearts' ? '❤️' : t === 'hands' ? '👍' : '🔥'))),
                React.createElement("div", { className: "emoji-grid" }, EMOJIS[emojiTab].split(' ').map((e, i) => React.createElement("button", { key: i, onClick: () => addEmoji(e) }, e)))),
            React.createElement("div", { className: "input-row composer-row" },
                React.createElement("div", { className: "composer-glow" }),
                React.createElement("button", { className: "emoji-launcher", onClick: () => { setShowEmoji(v => !v); vibrate('light'); }, title: "\u042D\u043C\u043E\u0434\u0437\u0438" }, "\uD83D\uDE0A"),
                React.createElement("div", { className: "input-wrapper" },
                    React.createElement("textarea", { ref: textareaRef, maxLength: 6000, value: input, enterKeyHint: "send", onChange: e => {
                            // \u041D\u0430 \u0447\u0430\u0441\u0442\u0438 \u043C\u043E\u0431\u0438\u043B\u044C\u043D\u044B\u0445 \u043A\u043B\u0430\u0432\u0438\u0430\u0442\u0443\u0440 (Gboard/Samsung Keyboard \u0438 \u0442.\u0434.)
                            // \u0442\u0430\u043F \u043F\u043E \u043A\u043D\u043E\u043F\u043A\u0435 "return" \u043D\u0430 \u044D\u043A\u0440\u0430\u043D\u043D\u043E\u0439 \u043A\u043B\u0430\u0432\u0438\u0430\u0442\u0443\u0440\u0435 \u043D\u0435 \u0434\u043E\u043B\u0435\u0442\u0430\u0435\u0442
                            // \u0434\u043E onKeyDown \u043A\u0430\u043A \u043D\u043E\u0440\u043C\u0430\u043B\u044C\u043D\u043E\u0435 \u0441\u043E\u0431\u044B\u0442\u0438\u0435 Enter \u2014 \u0432\u043C\u0435\u0441\u0442\u043E \u044D\u0442\u043E\u0433\u043E
                            // \u0431\u0440\u0430\u0443\u0437\u0435\u0440 \u0441\u0430\u043C \u0432\u0441\u0442\u0430\u0432\u043B\u044F\u0435\u0442 \u043F\u0435\u0440\u0435\u0432\u043E\u0434 \u0441\u0442\u0440\u043E\u043A\u0438 \u0447\u0435\u0440\u0435\u0437 input-\u0441\u043E\u0431\u044B\u0442\u0438\u0435
                            // insertLineBreak. \u0420\u0430\u043D\u044C\u0448\u0435 \u044D\u0442\u043E \u043B\u043E\u043C\u0430\u043B\u043E \u043A\u043E\u0440\u043E\u0442\u043A\u0438\u0435 \u0441\u043E\u043E\u0431\u0449\u0435\u043D\u0438\u044F
                            // (\u0442\u0435\u043A\u0441\u0442 \u0442\u0438\u043F\u0430 "\u0414\u0430" \u043F\u0440\u0435\u0432\u0440\u0430\u0449\u0430\u043B\u0441\u044F \u0432 "\u0414\n\u0430" \u0438 \u0440\u0432\u0430\u043B\u0441\u044F \u043D\u0430 2 \u0441\u0442\u0440\u043E\u043A\u0438).
                            // \u041B\u043E\u0432\u0438\u043C \u044D\u0442\u043E \u0437\u0434\u0435\u0441\u044C \u0438 \u043E\u0442\u043F\u0440\u0430\u0432\u043B\u044F\u0435\u043C \u0441\u043E\u043E\u0431\u0449\u0435\u043D\u0438\u0435 \u0432\u043C\u0435\u0441\u0442\u043E \u043F\u0440\u043E\u0441\u0442\u043E\u0433\u043E
                            // \u043F\u0435\u0440\u0435\u043D\u043E\u0441\u0430 \u0441\u0442\u0440\u043E\u043A\u0438.
                            const isMobileBreak = e.nativeEvent && e.nativeEvent.inputType === 'insertLineBreak';
                            if (isMobileBreak) {
                                const clean = e.target.value.replace(/\n+$/, '');
                                setInput(clean);
                                requestAnimationFrame(() => sendText(clean));
                                return;
                            }
                            setInput(e.target.value); resizeInput();
                        }, onKeyDown: e => { if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            sendMsg();
                        } }, placeholder: "\u041D\u0430\u043F\u0438\u0448\u0438 ADAM...", disabled: loading || throttle, rows: "1" })),
                React.createElement("button", { className: `voice-btn ${listening ? 'is-listening' : ''}`, onClick: startVoice, disabled: loading, title: "\u0413\u043E\u043B\u043E\u0441\u043E\u0432\u043E\u0439 \u0432\u0432\u043E\u0434" }, listening ? '●' : '🎙'),
                React.createElement("button", { className: "send-btn", onClick: sendMsg, disabled: !input.trim() || loading || throttle, "aria-label": "\u041E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C" }, loading ? React.createElement("div", { className: "spinner" }) : '➤')),
            React.createElement("div", { className: "composer-hint" }, "Enter \u2014 \u043E\u0442\u043F\u0440\u0430\u0432\u0438\u0442\u044C \u00B7 Shift+Enter \u2014 \u043D\u043E\u0432\u0430\u044F \u0441\u0442\u0440\u043E\u043A\u0430")),
        toast && React.createElement("div", { className: "adam-toast show" }, toast));
}
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(React.createElement(AiChat, null));
