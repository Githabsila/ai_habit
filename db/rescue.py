"""
Спасение серии друга (21.10, п.12 владельца: «дружеский забег» в Duolingo — друг сорвался, но серию ещё можно спасти).

У ADAM уже есть свой бесплатный возврат серии раз в месяц (db/streak.py::restore_streak_free) и подарок-заморозка (db/gifts.py). Здесь —
то, чего не хватало: серия друга ПОГАСЛА, а друзья могут вернуть её сами. Окно «Серию <Имя> ещё можно спасти!» (приложение), пуш друзьям
(streak_scheduler.run_rescue_notifications), маркер у друга в рейтинге.

Правила (всё считает сервер):
  • спасать можно только ДРУЗЬЯМ (взаимная подписка) и только свежий срыв: серия была не короче RESCUE_MIN_STREAK, прошло не больше
    FREE_RESTORE_GRACE_DAYS дней, и между срывом и сегодня нет второго пропуска (иначе серия пережила бы два пустых дня — как у
    обычного возврата, но без дыр в журнале);
  • это БЕСПЛАТНО для спасателя («Восстановить бесплатно» как в Duolingo), но не чаще раза в календарный месяц на человека, а у того, кого
    спасают, — не чаще раза в RESCUE_RECEIVER_COOLDOWN_DAYS дней: иначе двух аккаунтов хватило бы, чтобы «не терять» серию вечно;
  • свой возврат раз в месяц (free_restore_month) спасение не тратит: после спасения снимок срыва очищается, и возвращать больше нечего;
  • серия возвращается вместе с тем, что человек уже набрал после срыва: потерянные дни + сегодняшний, если он уже отметился.
"""
from datetime import date, timedelta

from .core import connect

RESCUE_MIN_STREAK = 3                   # короче — терять почти нечего, друзей не беспокоим
RESCUE_RECEIVER_COOLDOWN_DAYS = 14      # одного человека спасают не чаще раза в две недели
RESCUE_HELP_LIMIT = 3                   # сколько срывов предлагает окно при входе
RESCUE_PUSH_FRIENDS = 5                 # скольким друзьям уходит пуш об одном срыве


def _month_key(day):
    return f"{day.year}-{day.month:02d}"


def _off(reason="not_available"):
    return {"available": False, "lost_streak": 0, "reason": reason}


def _state(conn, owner_id, today):
    """Можно ли сейчас спасти серию человека. today — его местная дата. {"available", "lost_streak", "broken_date", "current_streak"} или
    {"available": False, "reason": "not_available" | "cooldown"}."""
    from .streak import FREE_RESTORE_GRACE_DAYS

    row = conn.execute(
        "SELECT m.last_broken_streak AS lost, m.last_broken_date AS broken, u.banned AS banned, u.streak AS streak "
        "FROM streak_meta m JOIN users u ON u.telegram_id = m.user_id WHERE m.user_id=?",
        (owner_id,),
    ).fetchone()
    if row is None or row["banned"]:
        return _off()
    lost = int(row["lost"] or 0)
    if lost < RESCUE_MIN_STREAK or not row["broken"]:
        return _off()
    try:
        broken = date.fromisoformat(row["broken"])
    except (TypeError, ValueError):
        return _off()
    gap = (today - broken).days
    if gap < 1 or gap > FREE_RESTORE_GRACE_DAYS:
        return _off()
    for offset in range(1, gap):                       # дни между срывом и сегодняшним: каждый должен быть закрыт
        mid = conn.execute(
            "SELECT status FROM streak_days WHERE user_id=? AND day=?", (owner_id, (broken + timedelta(days=offset)).isoformat())
        ).fetchone()
        if mid is None or mid["status"] not in ("completed", "freeze"):
            return _off()
    if conn.execute(
        "SELECT 1 FROM streak_rescues WHERE owner_id=? AND created_at >= datetime('now', ?) LIMIT 1",
        (owner_id, f"-{RESCUE_RECEIVER_COOLDOWN_DAYS} days"),
    ).fetchone():
        return _off("cooldown")
    return {"available": True, "lost_streak": lost, "broken_date": row["broken"], "current_streak": int(row["streak"] or 0)}


def rescue_state(owner_id):
    from .streak import local_today

    today = local_today(owner_id)
    conn = connect()
    try:
        return _state(conn, owner_id, today)
    finally:
        conn.close()


def rescue_states(owner_ids):
    """{человек: {"lost_streak", "broken_date"}} — только те, чью серию сейчас можно спасти (маркер у друга в рейтинге)."""
    ids = [int(i) for i in owner_ids]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    conn = connect()
    try:
        pre = [
            r["user_id"]
            for r in conn.execute(
                f"SELECT user_id FROM streak_meta WHERE user_id IN ({marks}) AND last_broken_streak >= ? AND last_broken_date IS NOT NULL",
                [*ids, RESCUE_MIN_STREAK],
            ).fetchall()
        ]
    finally:
        conn.close()
    result = {}
    for owner_id in pre:
        state = rescue_state(owner_id)
        if state["available"]:
            result[owner_id] = {"lost_streak": state["lost_streak"], "broken_date": state["broken_date"]}
    return result


def helper_used_this_month(helper_id):
    from .streak import local_today

    month = _month_key(local_today(helper_id))
    conn = connect()
    try:
        return conn.execute("SELECT 1 FROM streak_rescues WHERE helper_id=? AND month=? LIMIT 1", (helper_id, month)).fetchone() is not None
    finally:
        conn.close()


def _friend_ids(user_id):
    from .friends import get_friend_sources

    return [fid for fid, source in get_friend_sources(user_id).items() if source == "friend"]


def rescue_candidates(viewer_id, limit=RESCUE_HELP_LIMIT):
    """Друзья, чью серию сейчас можно спасти, — для окна при входе. Самая длинная потеря — первой. Пусто, если свой спасательный раз в этом
    месяце человек уже потратил."""
    friend_ids = _friend_ids(viewer_id)
    if not friend_ids or helper_used_this_month(viewer_id):
        return []
    states = rescue_states(friend_ids)
    if not states:
        return []
    marks = ",".join("?" for _ in states)
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT telegram_id, first_name, username, handle, avatar_id, frame_id FROM users "
            f"WHERE telegram_id IN ({marks})",
            list(states),
        ).fetchall()
    finally:
        conn.close()
    people = [
        {
            "telegram_id": r["telegram_id"],
            "first_name": r["first_name"] or r["username"] or "Друг",
            "handle": r["handle"],
            "avatar_id": r["avatar_id"] or "default",
            "frame_id": r["frame_id"] or "default",
            "streak": 0,
            "lost_streak": states[r["telegram_id"]]["lost_streak"],
        }
        for r in rows
    ]
    people.sort(key=lambda p: (-p["lost_streak"], p["telegram_id"]))
    return people[: int(limit)]


def rescue_streak(helper_id, owner_id):
    """Вернуть серию друга. {"ok": True, "lost_streak", "streak"} либо {"error": not_friends | helper_limit | cooldown | not_available}."""
    from .gifts import _are_mutual_friends
    from .streak import local_today

    if not _are_mutual_friends(helper_id, owner_id):
        return {"error": "not_friends"}
    owner_today = local_today(owner_id)
    month = _month_key(local_today(helper_id))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")                # дальше всё под блокировкой записи: два спасателя разом серию дважды не вернут
        if conn.execute("SELECT 1 FROM streak_rescues WHERE helper_id=? AND month=? LIMIT 1", (helper_id, month)).fetchone():
            conn.rollback()
            return {"error": "helper_limit"}
        state = _state(conn, owner_id, owner_today)
        if not state["available"]:
            conn.rollback()
            return {"error": state.get("reason", "not_available")}
        lost, broken, current = state["lost_streak"], state["broken_date"], state["current_streak"]
        restored = lost + current
        conn.execute(
            "INSERT OR REPLACE INTO streak_days(user_id, day, status, streak_after, ai_message) VALUES (?, ?, 'freeze', ?, ?)",
            (owner_id, broken, lost, "💛 Друг спас(ла) твою серию."),
        )
        conn.execute(
            "UPDATE streak_days SET streak_after = streak_after + ? WHERE user_id=? AND day > ? AND status='completed'",
            (lost, owner_id, broken),
        )
        conn.execute(
            "UPDATE users SET streak=?, best_streak=MAX(COALESCE(best_streak, 0), ?) WHERE telegram_id=?", (restored, restored, owner_id)
        )
        conn.execute(
            "UPDATE streak_meta SET last_broken_streak=0, last_broken_date=NULL, temp_frame='streak_flame', temp_status=? WHERE user_id=?",
            (f"В ударе {restored} дн.", owner_id),
        )
        conn.execute(
            "INSERT INTO streak_rescues(owner_id, helper_id, lost_streak, restored_streak, broken_date, month) VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, helper_id, lost, restored, broken, month),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "lost_streak": lost, "streak": restored}


# ---------------------------------------------------------------------------------------------------------------- пуши друзьям

def owners_to_announce():
    """Люди, у которых серия погасла недавно и ещё не вернулась, — кандидаты для пуша друзьям. Точную проверку делает rescue_state."""
    conn = connect()
    try:
        return [
            r["user_id"]
            for r in conn.execute(
                "SELECT user_id FROM streak_meta WHERE last_broken_streak >= ? AND last_broken_date >= date('now', '-4 days')",
                (RESCUE_MIN_STREAK,),
            ).fetchall()
        ]
    finally:
        conn.close()


def friends_to_notify(owner_id, broken_date):
    """Друзья человека, которым про этот срыв ещё не писали и у которых не потрачен спасательный раз месяца (не больше RESCUE_PUSH_FRIENDS)."""
    result = []
    for friend_id in _friend_ids(owner_id):
        conn = connect()
        try:
            sent = conn.execute(
                "SELECT 1 FROM rescue_pushes WHERE owner_id=? AND broken_date=? AND helper_id=?", (owner_id, broken_date, friend_id)
            ).fetchone()
        finally:
            conn.close()
        if sent or helper_used_this_month(friend_id):
            continue
        result.append(friend_id)
        if len(result) >= RESCUE_PUSH_FRIENDS:
            break
    return result


def mark_push_sent(owner_id, broken_date, helper_id):
    conn = connect()
    try:
        conn.execute("INSERT OR IGNORE INTO rescue_pushes(owner_id, broken_date, helper_id) VALUES (?, ?, ?)", (owner_id, broken_date, helper_id))
        conn.commit()
    finally:
        conn.close()
