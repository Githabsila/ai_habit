"""
Парное задание — как «Задания с друзьями» в Duolingo.

Двое друзей (db/friends.py) берутся за общую цель на неделю и тянут её вместе.

Правила (rules='habits', с 08.10): вклад — ЗАКРЫТЫЕ ПРИВЫЧКИ. Цель — PAIR_GOAL (20) привычек на двоих
за окно в PAIR_WINDOW_DAYS (7) дней, как в Duolingo: один может закрыть больше, другой меньше, важна сумма.
  • с человека в день засчитывается не больше PAIR_DAY_CAP (3) привычек — активные друзья набирают
    6 в день на двоих, и задание закрывается за 4–7 дней в зависимости от темпа;
  • цель закрывается только днём, когда ОБА закрыли хотя бы по одной привычке: если до цели осталось, скажем, 3,
    а друг утром закрыл три, зачтутся две, а последняя ждёт напарника; если осталось 2 — зачтётся одна, и т.д.
    (compute_progress — единственное место, где это записано);
  • закрыли цель раньше срока — задание выполнено сразу, сверх цели ничего не копится.
Старые задания (rules='days') доживают по прежним правилам: вклад — дни с отметкой (streak_days), цель 10.

Награда — сундук каждому (Adam Coin и алмаз) плюс очки в «Заданиях месяца».

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

Прогресс нигде не хранится отдельным счётчиком — он считается в момент запроса (_settle) по журналу выполнений
привычек (habit_completion_events; у старых заданий — по streak_days), поэтому отметка привычки где угодно (Mini App,
бот) засчитывается без дополнительных хуков. Хуки в маршрутах нужны только для мгновенных пушей (on_day_completed).
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect

# Сколько привычек на двоих нужно закрыть за окно (новые задания). Максимум в день — 3 + 3, значит быстрее всего
# задание закрывается за 4 дня (6 + 6 + 6 + 2), а неактивной паре нужно ~3 привычки в день на двоих.
PAIR_GOAL = 20
PAIR_WINDOW_DAYS = 7
# Больше стольки привычек в день с одного человека в парном задании не засчитывается.
PAIR_DAY_CAP = 3
# Старые задания, начатые до 08.10: вклад — дни с отметкой, цель 10.
LEGACY_PAIR_GOAL = 10
RULES_HABITS = "habits"
RULES_DAYS = "days"
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


def _rules(q):
    keys = q.keys()
    return RULES_HABITS if "rules" in keys and q["rules"] == RULES_HABITS else RULES_DAYS


def _habit_counts(cursor, user_id, start, end):
    """{день: сколько РАЗНЫХ привычек человек закрыл в этот день} в окне [start, end] (даты строками) — по журналу
    выполнений, в локальных днях человека. Журнал только дописывается: удалённая привычка уже засчитанное не
    стирает, а закрыть одну и ту же привычку дважды за день нельзя."""
    tz = ZoneInfo(_timezone_of(user_id))
    low = date.fromisoformat(start) - timedelta(days=1)
    high = date.fromisoformat(end) + timedelta(days=2)
    rows = cursor.execute(
        "SELECT habit_id, completed_at FROM habit_completion_events WHERE user_id=? AND completed_at>=? AND completed_at<?",
        (user_id, f"{low} 00:00:00", f"{high} 00:00:00"),
    ).fetchall()
    per_day = {}
    for r in rows:
        try:
            moment = datetime.fromisoformat(str(r["completed_at"]).replace("T", " ")).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        local = str(moment.astimezone(tz).date())
        if start <= local <= end:
            per_day.setdefault(local, set()).add(r["habit_id"])
    return {day: len(ids) for day, ids in per_day.items()}


def _timezone_of(user_id):
    from .streak import get_timezone
    return get_timezone(user_id)


def _window_days(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [str(first + timedelta(days=i)) for i in range((last - first).days + 1)]


def compute_progress(rules, goal, days, a_counts, b_counts):
    """Единственное место, где записаны правила подсчёта. days — даты окна по порядку (строки), a_counts / b_counts —
    {день: сколько привычек} приглашающего и приглашённого (у старых заданий — 1 за день с отметкой).

    {"total", "cap", "rows": [{"day", "a", "b"}] (a, b — уже с потолком на день), "done_day", "held_by_day"}.
    total — засчитанные привычки (для habits не больше goal). Для habits: если за день набралась бы цель, а один из
    двоих сегодня ещё ничего не закрыл, засчитывается goal-1 (цель ждёт напарника), «лишнее» — в held_by_day."""
    cap = PAIR_DAY_CAP if rules == RULES_HABITS else 1
    total = 0
    done_day = None
    held_by_day = {}
    rows = []
    for day in days:
        a = min(int(a_counts.get(day, 0)), cap)
        b = min(int(b_counts.get(day, 0)), cap)
        rows.append({"day": day, "a": a, "b": b})
        if done_day is not None:
            continue                                   # цель уже набрана — дальше ничего не копится
        gained = a + b
        if rules != RULES_HABITS:                       # старые задания: просто сумма дней
            total += gained
            if total >= goal:
                done_day = day
            continue
        if total + gained < goal:
            total += gained
        elif a >= 1 and b >= 1:
            held_by_day.pop(day, None)
            total, done_day = goal, day                  # в день завершения отметились оба
        else:
            held_by_day[day] = total + gained - (goal - 1)
            total = goal - 1                              # последняя привычка ждёт напарника
    return {"total": total, "cap": cap, "rows": rows, "done_day": done_day, "held_by_day": held_by_day}


def _calc_for_quest(cursor, q):
    """compute_progress по настоящим данным задания (нужны start_day / end_day)."""
    rules = _rules(q)
    start, end = q["start_day"], q["end_day"]
    if rules == RULES_HABITS:
        a_counts = _habit_counts(cursor, q["inviter_id"], start, end)
        b_counts = _habit_counts(cursor, q["invitee_id"], start, end)
    else:
        a_counts = {d: 1 for d in _contribution_days(cursor, q["inviter_id"], start, end)}
        b_counts = {d: 1 for d in _contribution_days(cursor, q["invitee_id"], start, end)}
    return compute_progress(rules, int(q["goal"]), _window_days(start, end), a_counts, b_counts)


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
                if _calc_for_quest(conn, q)["total"] >= q["goal"]:
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

def _build_view(*, quest_id, status, goal, rules, start_day, end_day, viewer_is_inviter, partner, calc, today,
                claimed, partner_state):
    """Чистая функция (без БД): то, что получает карточка. Её же использует «Лаборатория» в админке для демо-состояний."""
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    phase = ("scheduled" if today < start else "active") if status == "active" else status
    cap = calc["cap"]
    days = []
    for r in calc["rows"]:
        d = date.fromisoformat(r["day"])
        mine = r["a"] if viewer_is_inviter else r["b"]
        theirs = r["b"] if viewer_is_inviter else r["a"]
        days.append({
            "day": r["day"],
            "weekday": WEEKDAYS[d.weekday()],
            "label": d.day,
            "state": "past" if d < today else ("today" if d == today else "future"),
            "me": mine,                       # сколько привычек засчитано (старые задания: 1 за день с отметкой)
            "partner": theirs,
            "both": mine >= 1 and theirs >= 1,
            "done": r["day"] == calc["done_day"],
        })
    today_row = next((x for x in days if x["state"] == "today"), None)
    me_today = today_row["me"] if today_row else 0
    partner_today = today_row["partner"] if today_row else 0
    total = calc["total"]
    need = max(0, goal - total)
    # Сколько ещё можно набрать до конца: сегодня — остаток потолка у каждого, дальше — полный потолок у обоих.
    can = 0
    if phase == "active":
        for x in days:
            if x["state"] == "future":
                can += 2 * cap
            elif x["state"] == "today":
                can += max(0, cap - x["me"]) + max(0, cap - x["partner"])
    elif phase == "scheduled":
        can = len(days) * 2 * cap
    if phase == "completed" or need == 0:
        pace = "done"
    elif phase == "scheduled":
        pace = "ok"
    elif can < need:
        pace = "lost"
    elif can < need * 1.5:
        pace = "tight"
    else:
        pace = "ok"
    # Последняя привычка ждёт напарника: кто сегодня ещё ничего не закрыл.
    waiting_for = None
    if rules == RULES_HABITS and phase == "active" and need == 1:
        if me_today >= 1 and partner_today == 0:
            waiting_for = "partner"
        elif partner_today >= 1 and me_today == 0:
            waiting_for = "me"
        else:
            waiting_for = "both"
    return {
        "id": quest_id,
        "phase": phase,
        "partner": partner,
        "goal": goal,
        "rules": rules,
        "cap": cap,
        "progress": min(total, goal) if status == "completed" else total,
        "mine": sum(x["me"] for x in days),
        "partner_count": sum(x["partner"] for x in days),
        "start_day": start_day,
        "end_day": end_day,
        "starts_in": max(0, (start - today).days) if phase == "scheduled" else 0,
        "days_left": max(0, (end - today).days + 1) if phase == "active" else 0,
        "days": days,
        "today": {"me": me_today, "partner": partner_today, "held": calc["held_by_day"].get(str(today), 0)},
        "need": need,
        "can": can,
        "pace": pace,
        "waiting_for": waiting_for,
        "joint_days": sum(1 for x in days if x["both"]),
        "claimable": status == "completed" and not claimed,
        "claimed": claimed,
        # Можно ли подтолкнуть напарника — решает то же правило, что и у кнопки
        # «Напомнить» в друзьях (db/friends.py).
        "partner_state": partner_state,
        "can_cancel": phase == "scheduled",
    }


def _quest_view(cursor, q, user_id, today):
    from .friends import get_friend_state

    partner_id = _partner_of(q, user_id)
    partner_row = _user_row(cursor, partner_id)
    if partner_row is None:
        return None
    start = date.fromisoformat(q["start_day"])
    active_now = q["status"] == "active" and today >= start
    return _build_view(
        quest_id=q["id"],
        status=q["status"],
        goal=int(q["goal"]),
        rules=_rules(q),
        start_day=q["start_day"],
        end_day=q["end_day"],
        viewer_is_inviter=q["inviter_id"] == user_id,
        partner=_person(partner_row),
        calc=_calc_for_quest(cursor, q),
        today=today,
        claimed=_claimed_by(q, user_id) is not None,
        partner_state=get_friend_state(user_id, partner_id, partner_row) if active_now else None,
    )


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
        "day_cap": PAIR_DAY_CAP,
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
        "day_cap": PAIR_DAY_CAP,
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
                "INSERT INTO pair_quests(inviter_id, invitee_id, status, goal, rules) VALUES (?, ?, 'pending', ?, ?)",
                (user_id, friend_id, PAIR_GOAL, RULES_HABITS),
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
    if q["status"] != "active":
        return []
    conn = connect()
    try:
        partner_row = _user_row(conn, partner_id)
        calc = _calc_for_quest(conn, q)
    finally:
        conn.close()
    today_row = next((r for r in calc["rows"] if r["day"] == today_s), None)
    partner_today = 0 if today_row is None else (today_row["b"] if q["inviter_id"] == user_id else today_row["a"])
    if partner_today >= 1:
        return []                             # напарник сегодня уже в деле — подталкивать не нужно
    if partner_row is None or not _can_receive_nudge(partner_row):
        return []
    if not _claim_ping(q["id"], partner_id, _local_day(partner_id)):
        return []
    goal = int(q["goal"])
    return [{
        "type": "partner_done", "to": partner_id, "from": user_id, "quest_id": q["id"],
        "progress": calc["total"], "goal": goal,
        # до сундука осталась одна привычка и она за напарником
        "final": _rules(q) == RULES_HABITS and calc["total"] == goal - 1,
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
    """Подсказка для вечернего пуша в ПОСЛЕДНИЙ день окна или None. Шлём, только если цель ещё не набрана, у человека
    сегодня ещё есть что закрыть до потолка и цель достижима (хватит остатка потолков сегодняшнего дня у обоих)."""
    today = _local_today(user_id)
    if str(today) != quest["end_day"]:
        return None
    partner_id = _partner_of(quest, user_id)
    conn = connect()
    try:
        partner_row = _user_row(conn, partner_id)
        calc = _calc_for_quest(conn, quest)
    finally:
        conn.close()
    if partner_row is None:
        return None
    today_row = next((r for r in calc["rows"] if r["day"] == str(today)), None)
    if today_row is None:
        return None
    mine, theirs = (today_row["a"], today_row["b"]) if quest["inviter_id"] == user_id else (today_row["b"], today_row["a"])
    cap = calc["cap"]
    if mine >= cap:
        return None                            # свой максимум на сегодня уже закрыт
    left = int(quest["goal"]) - calc["total"]
    if left <= 0:
        return None
    if left > (cap - mine) + (cap - theirs):
        return None                            # до цели сегодня уже не дотянуть
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


# ---------------------------------------------------------------------------
# РЕЙТИНГ ПАР (сырая тестовая версия — только админка)
# ---------------------------------------------------------------------------

def get_pair_rating(limit=30):
    """Сырой рейтинг пар для админ-панели (пока без показа пользователям — смотрим, как он выглядит): по паре
    (неупорядоченные два человека) — сколько заданий выполнено, самое быстрое завершение в днях, сколько привычек
    засчитано за всё время и как идёт текущее задание. Порядок: больше выполненных → быстрее → больше привычек."""
    conn = connect()
    try:
        quests = conn.execute(
            "SELECT * FROM pair_quests WHERE accepted_at IS NOT NULL AND start_day IS NOT NULL "
            "AND status IN ('active','completed','expired') ORDER BY id"
        ).fetchall()
        pairs = {}
        for q in quests:
            key = tuple(sorted((q["inviter_id"], q["invitee_id"])))
            entry = pairs.setdefault(key, {
                "users": key, "completed": 0, "fastest_days": None, "habits_total": 0, "current": None, "quests": 0,
            })
            calc = _calc_for_quest(conn, q)
            goal = int(q["goal"])
            entry["quests"] += 1
            entry["habits_total"] += min(calc["total"], goal)
            if q["status"] == "completed":
                entry["completed"] += 1
                if calc["done_day"]:
                    days = [r["day"] for r in calc["rows"]].index(calc["done_day"]) + 1
                    if entry["fastest_days"] is None or days < entry["fastest_days"]:
                        entry["fastest_days"] = days
            elif q["status"] == "active":
                entry["current"] = {"progress": calc["total"], "goal": goal, "rules": _rules(q), "end_day": q["end_day"]}
        names = {}
        for key in pairs:
            for uid in key:
                if uid not in names:
                    row = _user_row(conn, uid)
                    names[uid] = _person(row) if row is not None else {"telegram_id": uid, "first_name": str(uid), "handle": None}
    finally:
        conn.close()
    ranked = sorted(
        pairs.values(),
        key=lambda e: (-e["completed"], e["fastest_days"] if e["fastest_days"] is not None else 99, -e["habits_total"]),
    )
    result = []
    for rank, entry in enumerate(ranked[:int(limit)], start=1):
        entry = dict(entry)
        entry["rank"] = rank
        entry["users"] = [
            {"telegram_id": uid, "first_name": names[uid]["first_name"], "handle": names[uid].get("handle")}
            for uid in entry["users"]
        ]
        result.append(entry)
    return result
