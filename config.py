from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

ADMIN_ID = int(os.getenv("ADMIN_ID", "8695214950"))

# ---------------- GOOGLE CALENDAR ----------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
# Публичный URL веб-сервера бота + /oauth2callback
# (на Railway это https://<ваш-домен>.up.railway.app/oauth2callback —
# тот же адрес нужно добавить в Google Cloud Console → Authorized redirect URIs)
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8080/oauth2callback")
# Часовой пояс, в котором стоят напоминания пользователей
GOOGLE_CALENDAR_TIMEZONE = os.getenv("GOOGLE_CALENDAR_TIMEZONE", "Europe/Moscow")
# Порт веб-сервера, принимающего OAuth callback (Railway передаёт его сам через $PORT)
PORT = int(os.getenv("PORT", "8080"))
