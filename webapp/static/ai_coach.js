const { useState, useEffect, useRef, useCallback } = React;
try {
    const lowPower = (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) || (navigator.deviceMemory && navigator.deviceMemory <= 4) || (navigator.connection && navigator.connection.saveData);
    if (lowPower) document.documentElement.classList.add('performance-lite');
} catch (e) {}
const tg = window.Telegram.WebApp;
tg.ready();
try {
    tg.expand();
}
catch (e) { }
try {
    tg.setHeaderColor('#07040E');
    tg.setBackgroundColor('#07040E');
}
catch (e) { }
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
function loadStoredMessages() {
    try {
        const raw = sessionStorage.getItem(CHAT_STORAGE_KEY);
        return raw ? JSON.parse(raw) : [];
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
                text: m.message || '',
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
        const text = (rawText || '').trim();
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
            setMessages(p => [...p, { id: data.message_id, role: 'assistant', text: data.answer, time: formatTime(), isCrisis: data.is_crisis, habit: data.suggested_habit, canRate: true, sourcePrompt: text }]);
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
            setMessages(p => [...p, { id: Date.now(), role: 'assistant', text: '✦ Совет дня\n\n' + (data.tip || 'Сделай сегодня один маленький шаг в сторону своей цели.'), time: formatTime(), canRate: false }]);
        }
        catch (e) {
            setMessages(p => [...p, { id: Date.now(), role: 'system', text: 'Не удалось получить совет дня.', type: 'error' }]);
        }
        finally {
            setLoading(false);
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
                    React.createElement("img", { src: "/static/assets/adam-avatar.png", alt: "ADAM" })),
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
                React.createElement("img", { src: "/static/assets/adam-avatar.png", alt: "ADAM" })),
            React.createElement("div", { className: "empty-title" }, "\u041F\u0440\u0438\u0432\u0435\u0442, \u044F ADAM \uD83D\uDC4B"),
            React.createElement("div", { className: "empty-text" }, "\u042F \u043F\u043E\u043C\u043E\u0433\u0430\u044E \u043F\u043B\u0430\u043D\u0438\u0440\u043E\u0432\u0430\u0442\u044C \u0434\u0435\u043D\u044C, \u0440\u0430\u0431\u043E\u0442\u0430\u0442\u044C \u0441 \u043F\u0440\u0438\u0432\u044B\u0447\u043A\u0430\u043C\u0438, \u0440\u0430\u0437\u0431\u0438\u0440\u0430\u0442\u044C \u043F\u0440\u043E\u0431\u043B\u0435\u043C\u044B \u0438 \u0432\u0438\u0434\u0435\u0442\u044C \u0442\u0432\u043E\u0439 \u043F\u0440\u043E\u0433\u0440\u0435\u0441\u0441."),
            React.createElement("div", { className: "welcome-prompts" }, quickPrompts.slice(0, 3).map(([icon, label]) => React.createElement("button", { key: label, onClick: () => sendText(label) },
                React.createElement("span", null, icon),
                label)))) : React.createElement(React.Fragment, null,
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
                        React.createElement("img", { src: "/static/assets/adam-avatar.png", alt: "" })),
                    React.createElement("div", { className: "assistant-message-wrap" },
                        m.isCrisis && React.createElement("div", { className: "crisis-alert" }, "\u26A0\uFE0F \u0412\u0430\u0436\u043D\u043E"),
                        React.createElement("div", { className: "message-bubble" },
                            React.createElement("div", { style: { whiteSpace: 'pre-wrap' } }, m.text)),
                        React.createElement("div", { className: "message-meta" },
                            React.createElement("span", null, m.time),
                            React.createElement("div", { className: "message-mini-actions" },
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
                        React.createElement("img", { src: "/static/assets/adam-avatar.png", alt: "" })),
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
                    React.createElement("textarea", { ref: textareaRef, maxLength: 6000, value: input, onChange: e => { setInput(e.target.value); resizeInput(); }, onKeyDown: e => { if (e.key === 'Enter' && !e.shiftKey) {
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
