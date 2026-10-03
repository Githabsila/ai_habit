"""
Друзья и «Напомнить друзьям» (по мотивам Duolingo).

Друг = ВЗАИМНАЯ подписка (db/follows.py: ссылка «Добавить друга» или подписка
в ответ) + участники твоей команды (db/teams.py — они вступили по коду
друга). Обмен реакциями друзьями НЕ делает, как и односторонняя подписка:
это слишком слабый сигнал, чтобы присылать человеку напоминания.

«Напомнить»: после первой отметки привычки за день Mini App предлагает
подтолкнуть друзей, которые сегодня ещё ничего не отмечали. Правила, чтобы
функция не превратилась в спам:
  • напоминать может только тот, кто сам уже отметился сегодня — текст
    напоминания говорит именно об этом, и он должен быть правдой;
  • раз в день на пару отправитель→получатель (UNIQUE по дню ПОЛУЧАТЕЛЯ);
  • не больше MAX_NUDGES_PER_DAY напоминаний в день одному человеку;
  • не ночью по местному времени получателя и не в его «тихие часы»;
  • не тем, кто выключил напоминания (общий тумблер или friend_nudges),
    заблокировал бота, забанен или у кого нет невыполненных привычек;
  • тем, кто УЖЕ отметился сегодня, напоминать нечего.
Причину «нельзя» отправителю не раскрываем (настройки друга — его дело):
наружу уходит только состояние done / can_remind / reminded / unavailable.
"""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import connect

MAX_NUDGES_PER_DAY = 3
# Окно местного времени получателя, когда напоминать можно: [8:00; 22:00).
NUDGE_HOUR_FROM = 8
NUDGE_HOUR_TO = 22
# Сколько друзей максимум просматривает окно «Напомнить» после отметки
# привычки — оно считается на каждой отметке, пока не показано, и не должно
# тормозить ответ.
PROMPT_SCAN_LIMIT = 40
PROMPT_MAX_ROWS = 8

STATE_DONE = "done"
STATE_CAN_REMIND = "can_remind"
STATE_REMINDED = "reminded"
STATE_UNAVAILABLE = "unavailable"

_STATE_ORDER = {STATE_CAN_REMIND: 0, STATE_REMINDED: 1, STATE_UNAVAILABLE: 2, STATE_DONE: 3}


def _local_now(user_id):
    from .streak import get_timezone
    return datetime.now(ZoneInfo(get_timezone(user_id)))


def _local_day(user_id):
    from .streak import local_today
    return str(local_today(user_id))


# ---------------------------------------------------------------------------
# ДРУЖБА
# ---------------------------------------------------------------------------

def add_friendship(user_id, friend_id):
    """Дружба по ссылке — взаимная подписка разом (db/follows.py::make_friends).
    True — пара стала друзьями только что; False — нельзя (сам с собой, нет
    такого пользователя, бан, блокировка, лимит) или уже были друзьями."""
    from .follows import make_friends
    return make_friends(user_id, friend_id)


def remove_friendship(user_id, friend_id):
    """Убирает подписки с обеих сторон. True, если что-то было удалено."""
    from .follows import remove_mutual
    return remove_mutual(user_id, friend_id)


def get_friend_sources(user_id):
    """{friend_id: "friend" | "team"} — друзья (ВЗАИМНАЯ подписка, новые
    сверху), затем участники команды. Если человек и то и другое — считается
    другом. Тот, на кого ты просто подписан, другом не считается."""
    conn = connect()
    try:
        cursor = conn.cursor()
        sources = {}
        cursor.execute(
            """
            SELECT f.followee_id AS friend_id
            FROM follows f
            JOIN follows r ON r.follower_id = f.followee_id AND r.followee_id = f.follower_id
            WHERE f.follower_id=?
            ORDER BY f.created_at DESC, f.rowid DESC
            """,
            (user_id,),
        )
        for row in cursor.fetchall():
            sources[row["friend_id"]] = "friend"
        cursor.execute("SELECT team_id FROM team_members WHERE user_id=?", (user_id,))
        team_row = cursor.fetchone()
        if team_row:
            cursor.execute(
                "SELECT user_id FROM team_members WHERE team_id=? AND user_id != ?",
                (team_row["team_id"], user_id),
            )
            for row in cursor.fetchall():
                sources.setdefault(row["user_id"], "team")
        # Блокировка сильнее общей команды: заблокированный (в любую сторону)
        # не считается другом и не получает/не шлёт напоминания.
        if sources:
            cursor.execute(
                "SELECT blocked_id AS other FROM user_blocks WHERE blocker_id=? "
                "UNION SELECT blocker_id AS other FROM user_blocks WHERE blocked_id=?",
                (user_id, user_id),
            )
            for row in cursor.fetchall():
                sources.pop(row["other"], None)
        return sources
    finally:
        conn.close()


def are_friends(user_id, other_id):
    return other_id in get_friend_sources(user_id)


# ---------------------------------------------------------------------------
# СОСТОЯНИЕ ДРУГА «СЕГОДНЯ»
# ---------------------------------------------------------------------------

def has_completed_today(user_id):
    """«Отметился сегодня» — ровно в том смысле, в каком его понимает ударный
    режим: запись дня в streak_days по локальному календарю пользователя."""
    from .streak import has_completed_today as streak_completed_today
    return streak_completed_today(user_id)


def _nudges_received_today(to_user_id, day):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM friend_nudges WHERE to_user_id=? AND day=?",
            (to_user_id, day),
        ).fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


def _already_nudged(from_user_id, to_user_id, day):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT 1 FROM friend_nudges WHERE from_user_id=? AND to_user_id=? AND day=? LIMIT 1",
            (from_user_id, to_user_id, day),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _can_receive_nudge(user_row):
    """Все «получатель не против и достижим» проверки, кроме дневных лимитов."""
    from .habits import get_incomplete_habits
    from .settings import get_settings, in_quiet_hours, friend_nudges_enabled

    if user_row is None or user_row["banned"]:
        return False
    if "bot_blocked_at" in user_row.keys() and user_row["bot_blocked_at"]:
        return False
    friend_id = user_row["telegram_id"]
    settings_row = get_settings(friend_id)
    if not settings_row or not settings_row["reminders"] or not friend_nudges_enabled(settings_row):
        return False
    now_local = _local_now(friend_id)
    if not (NUDGE_HOUR_FROM <= now_local.hour < NUDGE_HOUR_TO):
        return False
    if in_quiet_hours(settings_row, now_local):
        return False
    return bool(get_incomplete_habits(friend_id))


def get_friend_state(viewer_id, friend_id, user_row=None):
    """done — друг уже отметился сегодня; reminded — ты его уже подтолкнул
    сегодня; can_remind — можно напомнить; unavailable — нельзя (причину
    не раскрываем)."""
    if user_row is None:
        conn = connect()
        try:
            user_row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (friend_id,)).fetchone()
        finally:
            conn.close()
    if user_row is None or user_row["banned"]:
        return STATE_UNAVAILABLE
    if has_completed_today(friend_id):
        return STATE_DONE
    day = _local_day(friend_id)
    if _already_nudged(viewer_id, friend_id, day):
        return STATE_REMINDED
    if _nudges_received_today(friend_id, day) >= MAX_NUDGES_PER_DAY:
        return STATE_UNAVAILABLE
    if not _can_receive_nudge(user_row):
        return STATE_UNAVAILABLE
    return STATE_CAN_REMIND


def _load_users(ids):
    if not ids:
        return {}
    conn = connect()
    try:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM users WHERE telegram_id IN ({placeholders})", tuple(ids)
        ).fetchall()
        return {row["telegram_id"]: row for row in rows}
    finally:
        conn.close()


def _display(row, key):
    return row[key] if key in row.keys() else None


def _friend_entry(row, state, source):
    return {
        "telegram_id": row["telegram_id"],
        "first_name": row["first_name"] or row["username"] or "Друг",
        "handle": _display(row, "handle"),
        "avatar_id": _display(row, "avatar_id") or "default",
        "frame_id": _display(row, "frame_id") or "default",
        "streak": int(row["streak"] or 0),
        "state": state,
        "can_remove": source == "friend",
    }


def get_friends_overview(user_id):
    """Для карточки «Друзья» во вкладке рейтинга."""
    sources = get_friend_sources(user_id)
    users = _load_users(list(sources))
    friends = []
    for friend_id, source in sources.items():
        row = users.get(friend_id)
        if row is None or row["banned"]:
            continue
        friends.append(_friend_entry(row, get_friend_state(user_id, friend_id, row), source))
    friends.sort(key=lambda f: (_STATE_ORDER[f["state"]], -f["streak"], f["first_name"].lower()))
    return {"friends": friends, "viewer_done": has_completed_today(user_id)}


# ---------------------------------------------------------------------------
# НАПОМИНАНИЕ
# ---------------------------------------------------------------------------

def send_nudge(from_user_id, to_user_id):
    """Резервирует напоминание. Возвращает {"ok": True} или {"error": код}:
    not_friends / sender_not_done / already_done / already_reminded /
    unavailable. Саму отправку в Telegram делает вызывающий код; если
    доставка не удалась — cancel_nudge() возвращает возможность напомнить."""
    if from_user_id == to_user_id or not are_friends(from_user_id, to_user_id):
        return {"error": "not_friends"}
    if not has_completed_today(from_user_id):
        return {"error": "sender_not_done"}
    state = get_friend_state(from_user_id, to_user_id)
    if state == STATE_DONE:
        return {"error": "already_done"}
    if state == STATE_REMINDED:
        return {"error": "already_reminded"}
    if state != STATE_CAN_REMIND:
        return {"error": "unavailable"}
    day = _local_day(to_user_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO friend_nudges(from_user_id, to_user_id, day) VALUES (?, ?, ?)",
            (from_user_id, to_user_id, day),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # UNIQUE(from, to, day) — параллельный запрос успел раньше.
        return {"error": "already_reminded"}
    finally:
        conn.close()
    return {"ok": True}


def cancel_nudge(from_user_id, to_user_id):
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM friend_nudges WHERE from_user_id=? AND to_user_id=? AND day=?",
            (from_user_id, to_user_id, _local_day(to_user_id)),
        )
        conn.commit()
    finally:
        conn.close()


def claim_remind_prompt(user_id):
    """Окно «Напомнить друзьям» после отметки привычки: показывается не
    чаще раза в день и только если есть кому напомнить. Помечает показ
    атомарно — повторная отметка или перезагрузка окно не повторят."""
    if not has_completed_today(user_id):
        return None
    day = _local_day(user_id)
    conn = connect()
    try:
        shown = conn.execute(
            "SELECT 1 FROM friend_remind_prompts WHERE user_id=? AND day=?", (user_id, day)
        ).fetchone()
    finally:
        conn.close()
    if shown:
        return None

    sources = get_friend_sources(user_id)
    if not sources:
        return None
    ids = list(sources)[:PROMPT_SCAN_LIMIT]
    users = _load_users(ids)
    candidates = []
    for friend_id in ids:
        row = users.get(friend_id)
        if row is None:
            continue
        if get_friend_state(user_id, friend_id, row) == STATE_CAN_REMIND:
            candidates.append(_friend_entry(row, STATE_CAN_REMIND, sources[friend_id]))
    if not candidates:
        return None
    candidates.sort(key=lambda f: (-f["streak"], f["first_name"].lower()))

    conn = connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO friend_remind_prompts(user_id, day) VALUES (?, ?)", (user_id, day)
        )
        claimed = cursor.rowcount > 0
        conn.commit()
    finally:
        conn.close()
    if not claimed:
        return None
    return {"friends": candidates[:PROMPT_MAX_ROWS]}
