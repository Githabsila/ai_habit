from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")

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


