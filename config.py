from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# Быстрый провайдер для пользовательского чата. Если ключ задан, ADAM
# использует Groq как основной канал, OpenAI остаётся запасным вариантом.
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# ================= AI COST / INPUT LIMITS =================
# Telegram сам ограничивает обычное текстовое сообщение, но ADAM также
# проверяет длину на сервере, чтобы Mini App и бот имели одинаковые правила.
AI_MAX_INPUT_CHARS = int(os.getenv("AI_MAX_INPUT_CHARS", "6000"))
AI_TELEGRAM_MAX_INPUT_CHARS = int(os.getenv("AI_TELEGRAM_MAX_INPUT_CHARS", "4000"))
# Стоимость одного AI-ответа в существующей системе дневной квоты.
# Длинные запросы расходуют больше квоты, защищая экономику проекта.
AI_LONG_COST_CHARS = int(os.getenv("AI_LONG_COST_CHARS", "2200"))
AI_VERY_LONG_COST_CHARS = int(os.getenv("AI_VERY_LONG_COST_CHARS", "4000"))
AI_DAILY_FREE_COST_UNITS = int(os.getenv("AI_DAILY_FREE_COST_UNITS", "15"))
# Пром 9: включённый дневной лимит Pro снижен с 50 до 15 — сверх него
# доступны разовые докупки в Adam Store (см. shop_items 20/21/22/23:
# +10/+20 за Adam Coin, +50/+100 за Telegram Stars — каждый пакет не
# больше 1 раза в день, см. db/shop.py count_purchases_today).
AI_DAILY_PRO_COST_UNITS = int(os.getenv("AI_DAILY_PRO_COST_UNITS", "15"))
# Общий дневной потолок расхода AI-квоты по ВСЕМ пользователям сразу —
# защита бюджета от скрапера/бага/скомпрометированного аккаунта, которых
# персональные лимиты выше не остановят. Это ТОЛЬКО алерт админам в
# ежедневном дайджесте (admin_digest_scheduler.py), не жёсткая блокировка —
# сервис не должен сам себя выключать по неверно подобранному порогу.
#
# ВАЖНО: это единицы ВНУТРЕННЕЙ квоты ручного чата (см. handlers/ai.py
# consume_ai_answer) — НЕ реальные токены API и НЕ учитывает автоматические
# фичи на LLM (совет дня, утренние сообщения, еженедельный разбор,
# анализ анкеты). Для реального расхода — AI_DAILY_TOKEN_CEILING ниже,
# который считает вообще все вызовы LLM через multi_agent.py::_ask.
AI_GLOBAL_DAILY_UNIT_CEILING = int(os.getenv("AI_GLOBAL_DAILY_UNIT_CEILING", "5000"))
# Реальный дневной потолок в токенах API (вход+выход, все фичи на LLM
# сразу) — подбери под свой тариф у провайдера: посмотри, сколько токенов
# в день ты готов/можешь тратить по факту оплаченного лимита, и поставь
# сюда с запасом ~20%, чтобы алерт приходил ДО того, как реально кончится.
AI_DAILY_TOKEN_CEILING = int(os.getenv("AI_DAILY_TOKEN_CEILING", "2000000"))

# Сколько ошибок за последний час считается "всплеском" — при превышении
# админам сразу шлётся алерт (error_alert_scheduler.py), не дожидаясь
# ежедневной сводки в 8 утра.
ERROR_SPIKE_THRESHOLD = int(os.getenv("ERROR_SPIKE_THRESHOLD", "10"))

# Можно указать несколько ID через запятую: ADMIN_ID=8695214950,123456789
_admin_ids_raw = os.getenv("ADMIN_ID", "8695214950")
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()]
# Оставлено для обратной совместимости — первый ID из списка
ADMIN_ID = ADMIN_IDS[0]

# Через сколько часов анкета "pending" одобряется автоматически, если
# админ не одобрил вручную (эффект "закрытого доступа" + защита от того,
# что все залипнут в ожидании навсегда)
AUTO_APPROVE_HOURS = int(os.getenv("AUTO_APPROVE_HOURS", "3"))

# Цена Premium в Telegram Stars — встроенная валюта Telegram, не требует
# подключения платёжного провайдера (ЮKassa и т.п.), работает сразу "из коробки".
PREMIUM_PRICE_STARS = int(os.getenv("PREMIUM_PRICE_STARS", "150"))

# ================= ПОДПИСКА: ТРИАЛ → ОПЛАТА → ЗАКРЫТЫЙ КАНАЛ (пром 13) =================
# ВАЖНО: цены в Stars ниже — ПЛЕЙСХОЛДЕР, требуют вашего ревью перед
# включением. Курс Stars→USD задаёт сама Telegram и время от времени
# меняется (ориентир на момент написания: 1 Star ≈ $0.013), поэтому точную
# сумму для 1.99$/5.99$ нужно свериться в актуальном курсе Telegram.
SUBSCRIPTION_TRIAL_DAYS = int(os.getenv("SUBSCRIPTION_TRIAL_DAYS", "3"))
SUBSCRIPTION_INTRO_PRICE_STARS = int(os.getenv("SUBSCRIPTION_INTRO_PRICE_STARS", "153"))   # ≈ $1.99
SUBSCRIPTION_RENEWAL_PRICE_STARS = int(os.getenv("SUBSCRIPTION_RENEWAL_PRICE_STARS", "461"))  # ≈ $5.99
# Сколько ДНЕЙ УДАРНОГО РЕЖИМА подряд нужно набрать, чтобы получить доступ
# в закрытый канал (после того как подписка оплачена).
SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL = int(os.getenv("SUBSCRIPTION_STREAK_DAYS_FOR_CHANNEL", "2"))
# ID закрытого Telegram-канала (бот должен быть в нём админом с правом
# приглашать пользователей) — пока не задан, выдача доступа просто
# пропускается без ошибки.
CLOSED_CHANNEL_ID = os.getenv("CLOSED_CHANNEL_ID", "")
# ГЛАВНЫЙ ПРЕДОХРАНИТЕЛЬ: пока False, блокировка доступа к боту после
# истечения триала НЕ включена — бот работает как раньше для всех. Явно
# включите (SUBSCRIPTION_GATE_ENABLED=true), только когда цены/тексты/
# оплата Stars проверены вручную. Гейт при этом всё равно применяется
# ТОЛЬКО к пользователям, зарегистрированным ПОСЛЕ SUBSCRIPTION_GATE_CUTOVER —
# уже существующие пользователи бота не блокируются задним числом.
SUBSCRIPTION_GATE_ENABLED = os.getenv("SUBSCRIPTION_GATE_ENABLED", "false").lower() == "true"
SUBSCRIPTION_GATE_CUTOVER = os.getenv("SUBSCRIPTION_GATE_CUTOVER", "2100-01-01")

# =====================================
# MINIAPP
# =====================================
# Публичный HTTPS-адрес MiniApp — на Railway это домен, который платформа
# выдаёт сервису после первого деплоя (Settings → Networking → Generate
# Domain). Задаётся вручную ПОСЛЕ первого деплоя (курица/яйцо: домена ещё
# нет, пока сервис не задеплоен ни разу) — см. README.
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# Порт, на котором слушает встроенный веб-сервер MiniApp — Railway передаёт
# его сам через переменную PORT, локально по умолчанию 8080.
PORT = int(os.getenv("PORT", "8080"))


