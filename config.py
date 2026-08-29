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
AI_DAILY_PRO_COST_UNITS = int(os.getenv("AI_DAILY_PRO_COST_UNITS", "50"))

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


