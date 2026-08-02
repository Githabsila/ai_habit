"""
Интеграция с Google Calendar.

Как это работает:
1. Пользователь жмёт "Подключить Google Calendar" в настройках бота.
2. get_auth_url() строит ссылку на экран согласия Google.
   В параметр state кладём telegram_id пользователя, чтобы потом
   понять, кому принадлежит код авторизации.
3. Google после согласия пользователя редиректит на наш веб-сервер
   (см. oauth_server.py) по адресу GOOGLE_REDIRECT_URI с ?code=...&state=...
4. Веб-сервер вызывает exchange_code() — он меняет code на access/refresh
   токены и сохраняет их в таблице google_tokens.
5. sync_habit_reminder() создаёт (или обновляет) один ежедневно
   повторяющийся ивент в календаре пользователя со списком его
   текущих привычек — на время, указанное в настройках напоминаний бота.
"""

from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    GOOGLE_CALENDAR_TIMEZONE,
)
from db import (
    get_google_tokens,
    save_google_tokens,
    update_google_access_token,
    save_google_event_id,
    delete_google_tokens,
    get_habits,
    get_settings,
)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

EVENT_SUMMARY = "🎯 Привычки — ADAM"


def _client_config():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


# =====================================
# АВТОРИЗАЦИЯ
# =====================================

def get_auth_url(user_id: int) -> str:
    """Ссылка на экран согласия Google для конкретного пользователя бота."""

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",       # чтобы получить refresh_token
        include_granted_scopes="true",
        prompt="consent",            # гарантирует refresh_token даже при повторном подключении
        state=str(user_id),
    )

    return auth_url


def exchange_code(code: str, user_id: int) -> None:
    """Меняет код авторизации на токены и сохраняет их в БД. Вызывается веб-сервером."""

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )

    flow.fetch_token(code=code)
    creds = flow.credentials

    save_google_tokens(
        user_id,
        creds.token,
        creds.refresh_token,
        creds.expiry.isoformat() if creds.expiry else None,
    )


def _get_credentials(user_id: int):
    """Возвращает валидные Credentials, при необходимости обновляя access_token."""

    row = get_google_tokens(user_id)

    if not row or not row["refresh_token"]:
        return None

    creds = Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    if not creds.valid:
        try:
            creds.refresh(GoogleRequest())
            update_google_access_token(
                user_id,
                creds.token,
                creds.expiry.isoformat() if creds.expiry else None,
            )
        except Exception as e:
            print(f"❌ Не удалось обновить Google-токен для {user_id}: {e}")
            return None

    return creds


def is_connected(user_id: int) -> bool:
    return get_google_tokens(user_id) is not None


def disconnect(user_id: int) -> None:
    delete_google_tokens(user_id)


# =====================================
# СИНХРОНИЗАЦИЯ ПРИВЫЧЕК
# =====================================

def sync_habit_reminder(user_id: int) -> bool:
    """
    Создаёт или обновляет один ежедневно повторяющийся ивент
    "🎯 Привычки — ADAM" в календаре пользователя со списком его
    текущих привычек, на время его напоминания в настройках бота.
    Возвращает True при успехе.
    """

    creds = _get_credentials(user_id)
    if not creds:
        return False

    habits = get_habits(user_id)
    settings = get_settings(user_id)

    if not habits:
        description = "Привычек пока нет — добавьте их в боте ADAM."
    else:
        description = "\n".join(f"• {h['title']}" for h in habits)

    hour = settings["reminder_hour"] if settings else 9
    minute = settings["reminder_minute"] if settings else 0

    today = datetime.now().date()
    start_dt = datetime.combine(today, datetime.min.time()).replace(hour=hour, minute=minute)
    end_dt = start_dt + timedelta(minutes=30)

    body = {
        "summary": EVENT_SUMMARY,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": GOOGLE_CALENDAR_TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": GOOGLE_CALENDAR_TIMEZONE},
        "recurrence": ["RRULE:FREQ=DAILY"],
        "reminders": {"useDefault": True},
    }

    try:
        service = build("calendar", "v3", credentials=creds)

        row = get_google_tokens(user_id)
        event_id = row["calendar_event_id"] if row else None

        if event_id:
            try:
                service.events().update(
                    calendarId="primary", eventId=event_id, body=body
                ).execute()
                return True
            except Exception:
                # Ивент могли удалить вручную прямо в календаре — создаём заново
                event_id = None

        created = service.events().insert(calendarId="primary", body=body).execute()
        save_google_event_id(user_id, created["id"])
        return True

    except Exception as e:
        print(f"❌ Ошибка синхронизации Google Calendar для {user_id}: {e}")
        return False


def delete_habit_reminder(user_id: int) -> None:
    """Удаляет ивент из календаря пользователя (например, при отключении интеграции)."""

    creds = _get_credentials(user_id)
    if not creds:
        return

    row = get_google_tokens(user_id)
    if not row or not row["calendar_event_id"]:
        return

    try:
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(
            calendarId="primary", eventId=row["calendar_event_id"]
        ).execute()
    except Exception as e:
        print(f"⚠️ Не удалось удалить ивент Google Calendar для {user_id}: {e}")
