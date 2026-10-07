"""Напоминание «Адам хочет спросить, как дела».

Если человек сегодня ещё не писал Адаму (или давно не заходил в чат), в Mini App
после его действий (отметил привычку/задачу) или вечером при входе приходит подсказка
в том же стиле, что и подсказки онбординга: кнопка «ИИ» в нижней панели подсвечивается,
на ней загорается красная метка «новое сообщение». Когда человек заходит в чат, Адам сам
задаёт первый вопрос («Привет, Имя! Как у тебя дела? Как прошёл день?»).

Состояние — две локальные даты на пользователе (ISO, по его часовому поясу):
  ai_nudge_hint_day — когда подсказка уже показывалась (не чаще раза в день);
  ai_greet_day      — когда Адам уже поздоровался в чате (сообщение «прочитано»).
«Писал ли сегодня» берётся из самой переписки (ai_messages, role='user'), поэтому ничего
отдельно обновлять при отправке сообщения не нужно.
"""
import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .ai import add_ai_message
from .core import connect
from .streak import get_timezone

# Первые полчаса после регистрации — время стартового сценария (тур, шаг про ИИ и пуш
# «Привет! Я Adam…» через 4–30 минут, см. coach.py::run_ai_welcome_nudge): второе
# напоминание про того же Адама было бы навязчивым.
FRESH_ACCOUNT_MINUTES = 30

# До 12:00 «как прошёл день» не подходит — отдельные варианты на утро.
GREETINGS_MORNING = (
    "Привет, {name}! Как настроение с утра? Какие планы на сегодня?",
    "Привет, {name}! Как твоё утро? С чего начнёшь день?",
)
GREETINGS_DAY = (
    "Привет, {name}! Как у тебя дела? Как прошёл день?",
    "Привет, {name}! Что нового было сегодня?",
    "Привет, {name}! Как проходит день? Что уже получилось сделать?",
)
# В фразах нет «успел/успела»: пол пользователя не обязательно известен.
FALLBACK_NAME = "друг"
MAX_NAME_CHARS = 40


def _now_local(user_id):
    return datetime.now(ZoneInfo(get_timezone(user_id)))


def _parse_utc(value):
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def get_ai_nudge_state(user_id):
    """Что показывать в Mini App (поле ai_nudge в /api/bootstrap).

    pending          — сегодня человек не писал Адаму и Адам ещё не здоровался;
    eligible         — pending и аккаунт не новый: можно показывать подсказку;
    hint_shown_today — подсказка сегодня уже была (метка на «ИИ» остаётся до захода в чат);
    has_chatted      — переписка с Адамом уже была когда-либо.
    """
    conn = connect()
    try:
        row = conn.execute(
            "SELECT created_at, ai_intro_shown, ai_nudge_hint_day, ai_greet_day FROM users WHERE telegram_id=?",
            (user_id,),
        ).fetchone()
        last = conn.execute(
            "SELECT created_at FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"pending": False, "eligible": False, "hint_shown_today": False, "has_chatted": False}

    now = _now_local(user_id)
    today = now.date().isoformat()
    last_utc = _parse_utc(last["created_at"]) if last else None
    chatted_today = bool(last_utc) and last_utc.astimezone(now.tzinfo).date().isoformat() == today
    pending = not chatted_today and row["ai_greet_day"] != today

    created = _parse_utc(row["created_at"])
    fresh = bool(created) and (datetime.now(timezone.utc) - created).total_seconds() < FRESH_ACCOUNT_MINUTES * 60
    return {
        "pending": pending,
        "eligible": pending and not fresh,
        "hint_shown_today": row["ai_nudge_hint_day"] == today,
        "has_chatted": bool(row["ai_intro_shown"]),
    }


def mark_ai_nudge_hint_shown(user_id):
    today = _now_local(user_id).date().isoformat()
    conn = connect()
    try:
        conn.execute("UPDATE users SET ai_nudge_hint_day=? WHERE telegram_id=?", (today, user_id))
        conn.commit()
    finally:
        conn.close()


def pick_greeting(user_id, name, hour, day_iso):
    """Вариант не случайный, а зависит от (пользователь, день): повторный вызов в тот же день
    даёт ту же фразу, а на следующий день — другую."""
    pool = GREETINGS_MORNING if hour < 12 else GREETINGS_DAY
    seed = int(hashlib.sha1(f"{user_id}:{day_iso}".encode()).hexdigest(), 16)
    return pool[seed % len(pool)].format(name=name)


def claim_ai_greeting(user_id, demo=False):
    """Первый вопрос Адама при заходе в чат. Возвращает {"id", "message", "created_at"} или None.

    Не расходует дневной лимит ответов (модель не вызывается). Сообщение пишется в историю как
    реплика ассистента — следующий ответ человека пойдёт в ИИ уже с этим вопросом в контексте.
    Только для тех, кто уже общался с Адамом: у новичка в пустом чате и так стоит приветственный
    экран с быстрыми действиями. demo=True («Показать напоминание» в Настройках) — всегда, без
    отметки «поздоровался сегодня».
    """
    now = _now_local(user_id)
    today = now.date().isoformat()
    if not demo:
        state = get_ai_nudge_state(user_id)
        if not (state["pending"] and state["has_chatted"]):
            return None

    conn = connect()
    try:
        if not demo:
            cur = conn.execute(
                "UPDATE users SET ai_greet_day=? WHERE telegram_id=? AND COALESCE(ai_greet_day,'') != ?",
                (today, user_id, today),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
        user = conn.execute("SELECT first_name FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()

    name = ((user["first_name"] if user else "") or "").strip()[:MAX_NAME_CHARS] or FALLBACK_NAME
    text = pick_greeting(user_id, name, now.hour, today)
    message_id = add_ai_message(user_id, "assistant", text)

    conn = connect()
    try:
        row = conn.execute("SELECT created_at FROM ai_messages WHERE id=?", (message_id,)).fetchone()
    finally:
        conn.close()
    return {"id": message_id, "message": text, "created_at": row["created_at"] if row else None}
