"""
Парное задание — как «Задания с друзьями» в Duolingo.

Двое друзей (db/friends.py) берутся за общую цель на неделю и тянут её вместе:
вклад каждого — дни, когда он отметил привычку (запись дня в streak_days
со статусом completed, ровно как в ударном режиме). Цель — PAIR_GOAL дней на
двоих из окна в PAIR_WINDOW_DAYS: у каждого вклад не больше семи, значит
в одиночку задание не вытянуть — нужны оба. Награда — сундук каждому
(Adam Coin и алмаз) плюс очки в «Заданиях месяца».

Как проходит задание:
  1. Один зовёт друга (send_pair_invite) — выбирает союзника из друзей, которые
     недавно отмечались. Приглашение живёт INVITE_TTL_DAYS суток.
  2. Друг принимает (accept_pair_invite) или отказывается. Задание стартует
     НА СЛЕДУЮЩИЙ день — у обоих полные семь дней, в каком бы часовом поясе
     они ни жили (на экране «Парное задание стартует через 1 день»).
  3. Цель набрана — задание выполнено сразу, не дожидаясь конца окна; каждый
     открывает свой сундук (claim_pair_chest). Не набрана к концу окна —
     задание просто закрывается без награды.
  4. Следующее задание можно начать, когда закончится окно этого (и сундук
     открыт): так награда не чаще раза в неделю — не фармится парой аккаунтов.

Один человек — одно задание за раз (приглашение, ожидающее ответа, не
считается: оно отменяется, как только человек начинает другое). Если друзья
перестали быть друзьями, заблокировали друг друга или кого-то забанили —
задание отменяется само.

Прогресс нигде не хранится отдельным счётчиком — он считается по streak_days
в момент запроса (_settle), поэтому отметка привычки где угодно (Mini App,
бот, «восстановление» дня) засчитывается без дополнительных хуков. Хуки в
маршрутах нужны только для мгновенных пушей (on_day_completed).
"""
import sqlite3
from datetime import date, timedelta

from .core import connect

# Сколько дней на двоих нужно набрать. Окно — 7 дней, вклад каждого не больше 7:
# 10 значит «каждому хотя бы 3 дня, если напарник не пропустил ни одного».
PAIR_GOAL = 10
PAIR_WINDOW_DAYS = 7
# Награда каждому участнику; коины идут через add_xp, как и награды за обычные
# задания (в уровень — да, подарить нельзя).
PAIR_REWARD_COINS = 60
PAIR_REWARD_DIAMONDS = 1
# Столько очков «Заданий месяца» даёт каждое забранное парное задание.
PAIR_MONTH_POINTS = 3
INVITE_TTL_DAYS = 3
# Друг считается «живым» для приглашения, если отмечался за последние N дней —
# чтобы задание не зависало на человеке, который давно не заходит.
ACTIVE_FRIEND_DAYS = 7
# Сколько дней после неудачи на карточке видна подсказка «В этот раз не вышло».
RECENT_FAIL_DAYS = 3
MAX_INCOMING_SHOWN = 5
# Сколько друзей показываем сверху как «Рекомендуем» (самые активные за последнюю неделю,
# прежний напарник — в конце: как в Duolingo, новый союзник вместо вечного одного и того же).
MAX_RECOMMENDED = 3
# Сколько дней после конца задания зовём начать новое (два напоминания: на следующий день и
# через три дня — см. streak_scheduler.run_pair_quest_notifications).
RESTART_NUDGE_DAYS = 10

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

_OPEN_STATUSES = ("pending", "active")


# ---------------------------------------------------------------------------
# МЕЛОЧИ
# ---------------------------------------------------------------------------

def _local_today(user_id):
    from .streak import local_today
    return local_today(user_id)


def _partner_of(q, user_id):
    return q["invitee_id"] if q["inviter_id"] == user_id else q["inviter_id"]


def _is_member(q, user_id):
    return user_id in (q["inviter_id"], q["invitee_id"])


def _has_habits(user_id):
    conn = connect()
    try:
        return conn.execute("SELECT 1 FROM habits WHERE user_id=? LIMIT 1", (user_id,)).fetchone() is not None
    finally:
        conn.close()


def _user_row(cursor, user_id):
    return cursor.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()


def _person(row):
    keys = row.keys()
    return {
        "telegram_id": row["telegram_id"],
        "first_name": row["first_name"] or row["username"] or "Друг",
        "handle": row["handle"] if "handle" in keys else None,
        "avatar_id": (row["avatar_id"] if "avatar_id" in keys else None) or "default",
        "frame_id": (row["frame_id"] if "frame_id" in keys else None) or "default",
        "streak": int(row["streak"] or 0),
    }


def _compatible(q):
    """Оба на месте, не забанены и всё ещё друзья (блокировка в любую сторону
    дружбу снимает — см. friends.get_friend_sources)."""
    from .friends import are_friends

    conn = connect()
    try:
        for uid in (q["inviter_id"], q["invitee_id"]):
            row = _user_row(conn, uid)
            if row is None or row["banned"]:
                return False
    finally:
        conn.close()
    return are_friends(q["inviter_id"], q["invitee_id"])


def _contribution_days(cursor, user_id, start, end):
    rows = cursor.execute(
        "SELECT day FROM streak_days WHERE user_id=? AND status='completed' AND day>=? AND day<=?",
        (user_id, start, end),
    ).fetchall()
    return {r["day"] for r in rows}


def _finish(conn, quest_id, from_status, to_status):
    """Переводит задание в итоговый статус ровно один раз: True только тому,
    кто реально сменил статус (параллельный запрос получит False)."""
    cursor = conn.execute(
        "UPDATE pair_quests SET status=?, finished_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",
        (to_status, quest_id, from_status),
    )
    conn.commit()
    return cursor.rowcount > 0


def _settle(quest_id):
    """Приводит задание в актуальное состояние: просроченное приглашение
    закрывается, распавшаяся дружба отменяет задание, набранная цель
    засчитывается, истёкшее окно закрывает задание. Возвращает
    (строка после обновления | None, "completed"/"expired"/"cancelled"/None —
    что произошло именно сейчас)."""
    conn = connect()
    try:
        q = conn.execute("SELECT * FROM pair_quests WHERE id=?", (quest_id,)).fetchone()
        if q is None:
            return None, None
        status = q["status"]
        transition = None
        if status == "pending":
            stale = conn.execute(
                "SELECT 1 FROM pair_quests WHERE id=? AND created_at <= datetime('now', ?)",
                (quest_id, f"-{INVITE_TTL_DAYS} days"),
            ).fetchone()
            if stale:
                transition = "expired" if _finish(conn, quest_id, "pending", "expired") else None
            elif not _compatible(q):
                transition = "cancelled" if _finish(conn, quest_id, "pending", "cancelled") else None
        elif status == "active":
            if not _compatible(q):
                transition = "cancelled" if _finish(conn, quest_id, "active", "cancelled") else None
            else:
                mine = _contribution_days(conn, q["inviter_id"], q["start_day"], q["end_day"])
                theirs = _contribution_days(conn, q["invitee_id"], q["start_day"], q["end_day"])
                if len(mine) + len(theirs) >= q["goal"]:
                    transition = "completed" if _finish(conn, quest_id, "active", "completed") else None
                else:
                    last_day = min(
                        _local_today(q["inviter_id"]), _local_today(q["invitee_id"])
                    )
                    if last_day > date.fromisoformat(q["end_day"]):
                        transition = "expired" if _finish(conn, quest_id, "active", "expired") else None
        row = conn.execute("SELECT * FROM pair_quests WHERE id=?", (quest_id,)).fetchone()
        return row, transition
    finally:
        conn.close()


def _settle_user(user_id):
    """Settle всех открытых заданий человека. {quest_id: transition} для тех,
    что только что сменили статус."""
    conn = connect()
    try:
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM pair_quests WHERE status IN ('pending','active') "
                "AND (inviter_id=? OR invitee_id=?)",
                (user_id, user_id),
            ).fetchall()
        ]
    finally:
        conn.close()
    changed = {}
    for quest_id in ids:
        _, transition = _settle(quest_id)
        if transition:
            changed[quest_id] = transition
    return changed


def _blocking_quest(cursor, user_id, today):
    """Задание, которое не даёт начать новое: идущее/назначенное, либо
    выполненное, у которого не открыт сундук или ещё не кончилось окно."""
    today_s = str(today)
    row = cursor.execute(
        "SELECT * FROM pair_quests WHERE status='active' AND (inviter_id=? OR invitee_id=?) LIMIT 1",
        (user_id, user_id),
    ).fetchone()
    if row:
        return row
    for q in cursor.execute(
        "SELECT * FROM pair_quests WHERE status='completed' AND (inviter_id=? OR invitee_id=?) "
        "ORDER BY id DESC LIMIT 3",
        (user_id, user_id),
    ).fetchall():
        if _claimed_by(q, user_id) is None or q["end_day"] >= today_s:
            return q
    return None


def _claimed_by(q, user_id):
    return q["inviter_claimed_day"] if q["inviter_id"] == user_id else q["invitee_claimed_day"]


# ---------------------------------------------------------------------------
# СОСТОЯНИЕ ДЛЯ ЭКРАНА
# ---------------------------------------------------------------------------

def _quest_view(cursor, q, user_id, today):
    from .friends import get_friend_state

    partner_id = _partner_of(q, user_id)
    partner_row = _user_row(cursor, partner_id)
    if partner_row is None:
        return None
    start = date.fromisoformat(q["start_day"])
    end = date.fromisoformat(q["end_day"])
    my_days = _contribution_days(cursor, user_id, q["start_day"], q["end_day"])
    partner_days = _contribution_days(cursor, partner_id, q["start_day"], q["end_day"])
    total = len(my_days) + len(partner_days)

    status = q["status"]
    if status == "active":
        phase = "scheduled" if today < start else "active"
    else:
        phase = status                                   # completed / expired

    days = []
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        key = str(d)
        days.append({
            "day": key,
            "weekday": WEEKDAYS[d.weekday()],
            "label": d.day,
            "state": "past" if d < today else ("today" if d == today else "future"),
            "me": key in my_days,
            "partner": key in partner_days,
        })
    claimed = _claimed_by(q, user_id) is not None
    return {
        "id": q["id"],
        "phase": phase,
        "partner": _person(partner_row),
        "goal": int(q["goal"]),
        "progress": min(total, int(q["goal"])) if status == "completed" else total,
        "mine": len(my_days),
        "partner_count": len(partner_days),
        "start_day": q["start_day"],
        "end_day": q["end_day"],
        "starts_in": max(0, (start - today).days) if phase == "scheduled" else 0,
        "days_left": max(0, (end - today).days + 1) if phase == "active" else 0,
        "days": days,
        "claimable": status == "completed" and not claimed,
        "claimed": claimed,
        # Можно ли подтолкнуть напарника — решает то же правило, что и у кнопки
        # «Напомнить» в друзьях (db/friends.py).
        "partner_state": get_friend_state(user_id, partner_id, partner_row) if phase == "active" else None,
        "can_cancel": phase == "scheduled",
    }


def _last_partner_id(conn, user_id):
    """С кем был последний начавшийся (принятый) напарник — чтобы не предлагать его первым."""
    row = conn.execute(
        "SELECT inviter_id, invitee_id FROM pair_quests WHERE accepted_at IS NOT NULL "
        "AND status IN ('active','completed','expired') AND (inviter_id=? OR invitee_id=?) ORDER BY id DESC LIMIT 1",
        (user_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return row["invitee_id"] if row["inviter_id"] == user_id else row["inviter_id"]


def _has_any_quest(user_id):
    conn = connect()
    try:
        return conn.execute(
            "SELECT 1 FROM pair_quests WHERE inviter_id=? OR invitee_id=? LIMIT 1", (user_id, user_id)
        ).fetchone() is not None
    finally:
        conn.close()


def _state_template(needs_habit):
    return {
        "goal": PAIR_GOAL,
        "window_days": PAIR_WINDOW_DAYS,
        "reward": {"coins": PAIR_REWARD_COINS, "diamonds": PAIR_REWARD_DIAMONDS},
        "month_points": PAIR_MONTH_POINTS,
        "needs_habit": needs_habit,
        "can_choose": True,
        "choose_from_day": None,
        "quest": None,
        "outgoing": None,
        "incoming": [],
        "recent_fail": None,
        "completed_total": 0,
    }


def get_pair_quest(user_id):
    """Всё, что нужно карточке «Парное задание» на Главной."""
    needs_habit = not _has_habits(user_id)
    # Большинство людей заданий ещё не брали: им хватает одного лёгкого запроса —
    # состояние отдаётся в каждом /api/bootstrap, это критический путь первого экрана.
    if not _has_any_quest(user_id):
        return _state_template(needs_habit)
    _settle_user(user_id)
    today = _local_today(user_id)
    today_s = str(today)

    conn = connect()
    try:
        pending = conn.execute(
            "SELECT * FROM pair_quests WHERE status='pending' AND (inviter_id=? OR invitee_id=?) ORDER BY id DESC",
            (user_id, user_id),
        ).fetchall()
        active = conn.execute(
            "SELECT * FROM pair_quests WHERE status='active' AND (inviter_id=? OR invitee_id=?) "
            "ORDER BY id DESC LIMIT 1",
            (user_id, user_id),
        ).fetchone()
        completed = conn.execute(
            "SELECT * FROM pair_quests WHERE status='completed' AND (inviter_id=? OR invitee_id=?) "
            "ORDER BY id DESC LIMIT 1",
            (user_id, user_id),
        ).fetchone()
        failed = conn.execute(
            "SELECT * FROM pair_quests WHERE status='expired' AND accepted_at IS NOT NULL "
            "AND (inviter_id=? OR invitee_id=?) AND finished_at >= datetime('now', ?) "
            "ORDER BY id DESC LIMIT 1",
            (user_id, user_id, f"-{RECENT_FAIL_DAYS} days"),
        ).fetchone()
        completed_total = conn.execute(
            "SELECT COUNT(*) AS n FROM pair_quests WHERE status='completed' AND (inviter_id=? OR invitee_id=?)",
            (user_id, user_id),
        ).fetchone()["n"]

        current = active
        if current is None and completed is not None:
            # Выполненное показываем, пока не открыт сундук и пока идёт окно.
            if _claimed_by(completed, user_id) is None or completed["end_day"] >= today_s:
                current = completed
        quest = _quest_view(conn, current, user_id, today) if current is not None else None

        outgoing = None
        incoming = []
        for q in pending:
            other = _user_row(conn, _partner_of(q, user_id))
            if other is None:
                continue
            if q["inviter_id"] == user_id:
                outgoing = {"id": q["id"], "to": _person(other)}
            elif len(incoming) < MAX_INCOMING_SHOWN:
                incoming.append({"id": q["id"], "from": _person(other)})

        recent_fail = None
        if quest is None and failed is not None:
            other = _user_row(conn, _partner_of(failed, user_id))
            if other is not None:
                recent_fail = {"partner": _person(other)}

        blocking = _blocking_quest(conn, user_id, today)
        choose_from_day = None
        if blocking is not None and blocking["status"] == "completed" and _claimed_by(blocking, user_id) is not None:
            choose_from_day = str(date.fromisoformat(blocking["end_day"]) + timedelta(days=1))
    finally:
        conn.close()

    state = _state_template(needs_habit)
    state.update({
        "can_choose": blocking is None and outgoing is None,
        "choose_from_day": choose_from_day,
        "quest": quest,
        "outgoing": outgoing,
        "incoming": incoming,
        "recent_fail": recent_fail,
        "completed_total": int(completed_total),
    })
    return state


def list_pair_candidates(user_id):
    """Друзья для окна «Выберите союзника»: свободные и недавно отмечавшиеся
    — сверху, остальные — серыми с причиной (busy / inactive)."""
    from .friends import get_friend_sources

    _settle_user(user_id)
    today = _local_today(user_id)
    sources = get_friend_sources(user_id)
    ids = list(sources)
    candidates = []
    if ids:
        placeholders = ",".join("?" * len(ids))
        since = str(today - timedelta(days=ACTIVE_FRIEND_DAYS))
        conn = connect()
        try:
            users = {
                r["telegram_id"]: r
                for r in conn.execute(
                    f"SELECT * FROM users WHERE telegram_id IN ({placeholders})", tuple(ids)
                ).fetchall()
            }
            # Сколько дней за неделю друг отмечал привычку — это и «живой ли он», и рейтинг для рекомендаций.
            active_days = {
                r["user_id"]: min(int(r["n"]), ACTIVE_FRIEND_DAYS)
                for r in conn.execute(
                    f"SELECT user_id, COUNT(DISTINCT day) AS n FROM streak_days WHERE status='completed' AND day>=? "
                    f"AND user_id IN ({placeholders}) GROUP BY user_id",
                    (since, *ids),
                ).fetchall()
            }
            active_ids = set(active_days)
            last_partner = _last_partner_id(conn, user_id)
            busy_ids = set()
            for q in conn.execute(
                f"SELECT * FROM pair_quests WHERE status IN ('active','completed') "
                f"AND (inviter_id IN ({placeholders}) OR invitee_id IN ({placeholders}))",
                (*ids, *ids),
            ).fetchall():
                if q["status"] == "active" or q["end_day"] >= str(today):
                    busy_ids.update((q["inviter_id"], q["invitee_id"]))
        finally:
            conn.close()
        for friend_id in ids:
            row = users.get(friend_id)
            if row is None or row["banned"]:
                continue
            reason = None
            if friend_id in busy_ids:
                reason = "busy"
            elif friend_id not in active_ids:
                reason = "inactive"
            entry = _person(row)
            entry["available"] = reason is None
            entry["reason"] = reason
            entry["active_days"] = active_days.get(friend_id, 0)
            entry["was_partner"] = friend_id == last_partner
            entry["recommended"] = False
            candidates.append(entry)
    # Рекомендуем самых активных из свободных: прежний напарник — после остальных.
    pool = [c for c in candidates if c["available"] and c["active_days"] > 0]
    pool.sort(key=lambda c: (c["was_partner"], -c["active_days"], -c["streak"], c["first_name"].lower()))
    for c in pool[:MAX_RECOMMENDED]:
        c["recommended"] = True
    candidates.sort(key=lambda c: (
        not c["available"], not c["recommended"], -c["active_days"], -c["streak"], c["first_name"].lower()
    ))
    state = get_pair_quest(user_id)
    return {
        "friends": candidates,
        "can_choose": state["can_choose"],
        "needs_habit": state["needs_habit"],
        "goal": PAIR_GOAL,
        "window_days": PAIR_WINDOW_DAYS,
        "active_window": ACTIVE_FRIEND_DAYS,
    }


# ---------------------------------------------------------------------------
# ДЕЙСТВИЯ
# ---------------------------------------------------------------------------

def send_pair_invite(user_id, friend_id):
    """{"ok": True, "quest_id"} либо {"error": invalid_target | not_friends |
    needs_habit | busy | already_invited | invited_you | partner_busy |
    partner_inactive}."""
    from .friends import are_friends

    try:
        friend_id = int(friend_id)
    except (TypeError, ValueError):
        return {"error": "invalid_target"}
    if friend_id == user_id:
        return {"error": "invalid_target"}
    if not are_friends(user_id, friend_id):
        return {"error": "not_friends"}
    if not _has_habits(user_id):
        return {"error": "needs_habit"}

    _settle_user(user_id)
    _settle_user(friend_id)
    my_today = _local_today(user_id)
    friend_today = _local_today(friend_id)
    since = str(friend_today - timedelta(days=ACTIVE_FRIEND_DAYS))

    conn = connect()
    try:
        friend_row = _user_row(conn, friend_id)
        if friend_row is None or friend_row["banned"]:
            return {"error": "not_friends"}
        conn.execute("BEGIN IMMEDIATE")
        if _blocking_quest(conn, user_id, my_today) is not None:
            return {"error": "busy"}
        if conn.execute(
            "SELECT 1 FROM pair_quests WHERE status='pending' AND inviter_id=?", (user_id,)
        ).fetchone():
            return {"error": "already_invited"}
        reverse = conn.execute(
            "SELECT id FROM pair_quests WHERE status='pending' AND inviter_id=? AND invitee_id=?",
            (friend_id, user_id),
        ).fetchone()
        if reverse:
            return {"error": "invited_you", "quest_id": reverse["id"]}
        if _blocking_quest(conn, friend_id, friend_today) is not None:
            return {"error": "partner_busy"}
        if not conn.execute(
            "SELECT 1 FROM streak_days WHERE user_id=? AND status='completed' AND day>=? LIMIT 1",
            (friend_id, since),
        ).fetchone():
            return {"error": "partner_inactive"}
        try:
            cursor = conn.execute(
                "INSERT INTO pair_quests(inviter_id, invitee_id, status, goal) VALUES (?, ?, 'pending', ?)",
                (user_id, friend_id, PAIR_GOAL),
            )
        except sqlite3.IntegrityError:
            # Частичный UNIQUE по inviter_id: параллельный запрос успел раньше.
            return {"error": "already_invited"}
        conn.commit()
        return {"ok": True, "quest_id": cursor.lastrowid}
    finally:
        conn.close()


def accept_pair_invite(user_id, quest_id):
    """{"ok": True, "quest_id", "inviter_id", "start_day"} либо {"error":
    not_found | not_pending | needs_habit | busy}."""
    try:
        quest_id = int(quest_id)
    except (TypeError, ValueError):
        return {"error": "not_found"}
    q, _ = _settle(quest_id)
    if q is None or q["invitee_id"] != user_id:
        return {"error": "not_found"}
    if q["status"] != "pending":
        return {"error": "not_pending"}
    if not _has_habits(user_id):
        return {"error": "needs_habit"}

    inviter_id = q["inviter_id"]
    my_today = _local_today(user_id)
    inviter_today = _local_today(inviter_id)
    # Старт — на следующий день по календарю того, у кого «сегодня» позже: у
    # обоих тогда впереди полные семь дней.
    start = max(my_today, inviter_today) + timedelta(days=1)
    end = start + timedelta(days=PAIR_WINDOW_DAYS - 1)

    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _blocking_quest(conn, user_id, my_today) is not None or \
                _blocking_quest(conn, inviter_id, inviter_today) is not None:
            return {"error": "busy"}
        cursor = conn.execute(
            "UPDATE pair_quests SET status='active', start_day=?, end_day=?, accepted_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='pending'",
            (str(start), str(end), quest_id),
        )
        if cursor.rowcount == 0:
            return {"error": "not_pending"}
        # Остальные ожидающие приглашения обоих больше не нужны.
        conn.execute(
            "UPDATE pair_quests SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
            "WHERE status='pending' AND id<>? AND (inviter_id IN (?, ?) OR invitee_id IN (?, ?))",
            (quest_id, user_id, inviter_id, user_id, inviter_id),
        )
        conn.commit()
        return {"ok": True, "quest_id": quest_id, "inviter_id": inviter_id, "start_day": str(start)}
    finally:
        conn.close()


def decline_pair_invite(user_id, quest_id):
    try:
        quest_id = int(quest_id)
    except (TypeError, ValueError):
        return {"error": "not_found"}
    conn = connect()
    try:
        cursor = conn.execute(
            "UPDATE pair_quests SET status='declined', finished_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND invitee_id=? AND status='pending'",
            (quest_id, user_id),
        )
        conn.commit()
        return {"ok": True} if cursor.rowcount else {"error": "not_found"}
    finally:
        conn.close()


def cancel_pair_quest(user_id, quest_id):
    """Отозвать своё приглашение или выйти из задания, которое ещё не
    стартовало. Начавшееся задание отменить нельзя — иначе напарник остался бы
    без награды из-за чужого настроения."""
    try:
        quest_id = int(quest_id)
    except (TypeError, ValueError):
        return {"error": "not_found"}
    q, _ = _settle(quest_id)
    if q is None or not _is_member(q, user_id):
        return {"error": "not_found"}
    today = _local_today(user_id)
    if q["status"] == "pending" and q["inviter_id"] == user_id:
        from_status = "pending"
    elif q["status"] == "active" and today < date.fromisoformat(q["start_day"]):
        from_status = "active"
    else:
        return {"error": "cannot_cancel"}
    conn = connect()
    try:
        done = _finish(conn, quest_id, from_status, "cancelled")
    finally:
        conn.close()
    return {"ok": True, "partner_id": _partner_of(q, user_id)} if done else {"error": "cannot_cancel"}


def claim_pair_chest(user_id, quest_id):
    """Открыть сундук выполненного задания. {"ok": True, "coins", "diamonds",
    "month_points"} либо {"error": not_found | not_completed | already_claimed}.
    Пометка «забрал» ставится условным UPDATE — два одновременных нажатия
    выдадут награду один раз."""
    from .users import add_xp, add_diamonds

    try:
        quest_id = int(quest_id)
    except (TypeError, ValueError):
        return {"error": "not_found"}
    q, _ = _settle(quest_id)
    if q is None or not _is_member(q, user_id):
        return {"error": "not_found"}
    if q["status"] != "completed":
        return {"error": "not_completed"}
    column = "inviter_claimed_day" if q["inviter_id"] == user_id else "invitee_claimed_day"
    conn = connect()
    try:
        cursor = conn.execute(
            f"UPDATE pair_quests SET {column}=? WHERE id=? AND {column} IS NULL",
            (str(_local_today(user_id)), quest_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"error": "already_claimed"}
    finally:
        conn.close()
    add_xp(user_id, PAIR_REWARD_COINS)
    add_diamonds(user_id, PAIR_REWARD_DIAMONDS)
    from .activity_feed import log_activity_event
    log_activity_event(user_id, "pair_quest", {"detail": "парное задание"})
    return {
        "ok": True,
        "coins": PAIR_REWARD_COINS,
        "diamonds": PAIR_REWARD_DIAMONDS,
        "month_points": PAIR_MONTH_POINTS,
    }


def month_points(cursor, user_id, month_key):
    """Очки «Заданий месяца» за забранные в этом месяце парные задания."""
    like = f"{month_key}-%"
    row = cursor.execute(
        "SELECT COUNT(*) AS n FROM pair_quests WHERE status='completed' AND "
        "((inviter_id=? AND inviter_claimed_day LIKE ?) OR (invitee_id=? AND invitee_claimed_day LIKE ?))",
        (user_id, like, user_id, like),
    ).fetchone()
    return int(row["n"] or 0) * PAIR_MONTH_POINTS


# ---------------------------------------------------------------------------
# СОБЫТИЯ ДЛЯ ПУШЕЙ
# ---------------------------------------------------------------------------

def _active_quest_of(user_id):
    conn = connect()
    try:
        return conn.execute(
            "SELECT * FROM pair_quests WHERE status='active' AND (inviter_id=? OR invitee_id=?) "
            "ORDER BY id DESC LIMIT 1",
            (user_id, user_id),
        ).fetchone()
    finally:
        conn.close()


def _claim_ping(quest_id, to_user_id, day):
    conn = connect()
    try:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO pair_quest_pings(quest_id, to_user_id, day) VALUES (?, ?, ?)",
            (quest_id, to_user_id, day),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def on_day_completed(user_id):
    """Вызывается после первой отметки привычки за день. Возвращает события для
    пушей: {"type": "completed", ...} — цель набрана этой отметкой (по одному
    на задание), {"type": "partner_done", "to", ...} — напарнику, который ещё не
    отмечался, пора догонять (не чаще раза в день, и только если он не против
    напоминаний — те же правила, что у «Напомнить» в друзьях)."""
    from .friends import _can_receive_nudge, _local_day, has_completed_today

    q = _active_quest_of(user_id)
    if q is None:
        return []
    today_s = str(_local_today(user_id))
    if not (q["start_day"] <= today_s <= q["end_day"]):
        return []
    q, transition = _settle(q["id"])
    if q is None:
        return []
    partner_id = _partner_of(q, user_id)
    if transition == "completed":
        return [{
            "type": "completed", "quest_id": q["id"],
            "users": [q["inviter_id"], q["invitee_id"]],
        }]
    if q["status"] != "active" or has_completed_today(partner_id):
        return []
    conn = connect()
    try:
        partner_row = _user_row(conn, partner_id)
        mine = _contribution_days(conn, q["inviter_id"], q["start_day"], q["end_day"])
        theirs = _contribution_days(conn, q["invitee_id"], q["start_day"], q["end_day"])
    finally:
        conn.close()
    if partner_row is None or not _can_receive_nudge(partner_row):
        return []
    if not _claim_ping(q["id"], partner_id, _local_day(partner_id)):
        return []
    return [{
        "type": "partner_done", "to": partner_id, "from": user_id, "quest_id": q["id"],
        "progress": len(mine) + len(theirs), "goal": int(q["goal"]),
    }]


def settle_active_quests():
    """Для планировщика: засчитывает задания, цель которых набрана без
    маршрутов Mini App (отметка через бота), и возвращает события completed."""
    conn = connect()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM pair_quests WHERE status='active'").fetchall()]
    finally:
        conn.close()
    events = []
    for quest_id in ids:
        q, transition = _settle(quest_id)
        if transition == "completed" and q is not None:
            events.append({
                "type": "completed", "quest_id": quest_id,
                "users": [q["inviter_id"], q["invitee_id"]],
            })
    return events


def get_active_pair_quests():
    """Идущие задания (для вечернего напоминания планировщика)."""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM pair_quests WHERE status='active'").fetchall()]
    finally:
        conn.close()


def last_day_reminder(quest, user_id):
    """Текст-подсказка для вечернего пуша в ПОСЛЕДНИЙ день окна или None.
    Шлём, только если цель ещё не набрана, человек сегодня не отмечался и цель
    ещё достижима (хватит сегодняшних отметок тех, кто их не сделал)."""
    from .friends import has_completed_today

    today = _local_today(user_id)
    if str(today) != quest["end_day"]:
        return None
    if has_completed_today(user_id):
        return None
    partner_id = _partner_of(quest, user_id)
    conn = connect()
    try:
        partner_row = _user_row(conn, partner_id)
        mine = _contribution_days(conn, quest["inviter_id"], quest["start_day"], quest["end_day"])
        theirs = _contribution_days(conn, quest["invitee_id"], quest["start_day"], quest["end_day"])
    finally:
        conn.close()
    if partner_row is None:
        return None
    left = int(quest["goal"]) - len(mine) - len(theirs)
    if left <= 0:
        return None
    still_to_come = 1 + (0 if has_completed_today(partner_id) else 1)
    if left > still_to_come:
        return None
    return {"left": left, "partner_name": _person(partner_row)["first_name"]}


def users_due_for_new_quest():
    """Для планировщика: люди, у которых недавно закончилось парное задание (выполнено и сундук
    открыт, либо не набрано) и нового пока нет — им предлагаем выбрать союзника (как в Duolingo).
    [{"user_id", "quest_id", "end_day", "status"}] — по одному, последнее задание человека."""
    conn = connect()
    try:
        since = str(date.today() - timedelta(days=RESTART_NUDGE_DAYS + 2))
        rows = conn.execute(
            "SELECT id, inviter_id, invitee_id, status, end_day, inviter_claimed_day, invitee_claimed_day "
            "FROM pair_quests WHERE accepted_at IS NOT NULL AND status IN ('completed','expired') AND end_day>=? "
            "ORDER BY id",
            (since,),
        ).fetchall()
    finally:
        conn.close()
    latest = {}
    for q in rows:
        for uid in (q["inviter_id"], q["invitee_id"]):
            latest[uid] = q                      # id по возрастанию: последнее перезаписывает
    due = []
    for uid, q in latest.items():
        claimed = q["inviter_claimed_day"] if q["inviter_id"] == uid else q["invitee_claimed_day"]
        if q["status"] == "completed" and claimed is None:
            continue                              # сундук ещё не открыт — сначала он
        due.append({"user_id": uid, "quest_id": q["id"], "end_day": q["end_day"], "status": q["status"]})
    return due
