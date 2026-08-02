from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Можно указать несколько ID через запятую: ADMIN_ID=8695214950,123456789
_admin_ids_raw = os.getenv("ADMIN_ID", "8695214950")
ADMIN_IDS = [int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()]
# Оставлено для обратной совместимости — первый ID из списка
ADMIN_ID = ADMIN_IDS[0]

# Через сколько часов анкета "pending" одобряется автоматически, если
# админ не одобрил вручную (эффект "закрытого доступа" + защита от того,
# что все залипнут в ожидании навсегда)
AUTO_APPROVE_HOURS = int(os.getenv("AUTO_APPROVE_HOURS", "3"))


