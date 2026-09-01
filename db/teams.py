"""
Roadmap #16 — групповые челленджи: команда из нескольких человек,
вступление по инвайт-коду, общий прогресс = сумма выполнений участников
за текущую неделю (переиспользует habit_logs, отдельного счётчика не
заводим). Один пользователь состоит максимум в одной команде — вступление
в новую автоматически выводит из старой (простая модель, без вложенных
команд/ролей).
"""
import random
import string

from .core import connect

MAX_TEAM_MEMBERS = 12
MAX_TEAM_NAME_LENGTH = 40


def _generate_invite_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def create_team(user_id, name):
    name = (name or "").strip()[:MAX_TEAM_NAME_LENGTH]
    if not name:
        return None
    conn = connect()
    cursor = conn.cursor()
    code = _generate_invite_code()
    for _ in range(5):
        cursor.execute("SELECT 1 FROM teams WHERE invite_code=?", (code,))
        if cursor.fetchone() is None:
            break
        code = _generate_invite_code()
    cursor.execute(
        "INSERT INTO teams(name, invite_code, created_by) VALUES (?,?,?)",
        (name, code, user_id),
    )
    team_id = cursor.lastrowid
    cursor.execute("DELETE FROM team_members WHERE user_id=?", (user_id,))
    cursor.execute("INSERT INTO team_members(team_id, user_id) VALUES (?,?)", (team_id, user_id))
    conn.commit()
    conn.close()
    return {"id": team_id, "name": name, "invite_code": code}


def join_team(user_id, invite_code):
    invite_code = (invite_code or "").strip().upper()
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM teams WHERE invite_code=?", (invite_code,))
    team = cursor.fetchone()
    if team is None:
        conn.close()
        return None
    cursor.execute("SELECT COUNT(*) as cnt FROM team_members WHERE team_id=?", (team["id"],))
    if cursor.fetchone()["cnt"] >= MAX_TEAM_MEMBERS:
        conn.close()
        return {"error": "team_full"}
    cursor.execute("DELETE FROM team_members WHERE user_id=?", (user_id,))
    cursor.execute("INSERT INTO team_members(team_id, user_id) VALUES (?,?)", (team["id"], user_id))
    conn.commit()
    conn.close()

    from .activity_feed import log_activity_event
    log_activity_event(user_id, "joined_team", {"detail": team["name"]})

    return {"id": team["id"], "name": team["name"]}


def leave_team(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM team_members WHERE user_id=?", (user_id,))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def get_my_team(user_id):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.name, t.invite_code FROM team_members m
        JOIN teams t ON t.id = m.team_id
        WHERE m.user_id=?
    """, (user_id,))
    team = cursor.fetchone()
    if team is None:
        conn.close()
        return None

    # habit_logs — ночной снимок ЗАКОНЧЕННЫХ дней (см. db/habits.py::
    # log_daily_habits, пишется в scheduler.new_day() ДО reset_habits()),
    # сегодняшний день туда попадёт только завтра ночью — поэтому для
    # "сегодня" считаем напрямую из habits.completed, тот же приём, что и
    # get_weekly_progress() для гибкой периодичности (roadmap #2).
    cursor.execute("""
        SELECT u.telegram_id, u.first_name, u.avatar_id, u.frame_id,
               COALESCE((
                   SELECT SUM(hl.completed) FROM habit_logs hl
                   WHERE hl.user_id = u.telegram_id AND hl.day >= date('now','-7 days') AND hl.day < date('now')
               ), 0) + COALESCE((
                   SELECT COUNT(*) FROM habits h WHERE h.user_id = u.telegram_id AND h.completed=1
               ), 0) as week_completions
        FROM team_members m
        JOIN users u ON u.telegram_id = m.user_id
        WHERE m.team_id=?
        ORDER BY week_completions DESC
    """, (team["id"],))
    members = cursor.fetchall()
    conn.close()

    members_list = [
        {
            "telegram_id": m["telegram_id"],
            "first_name": m["first_name"],
            "avatar_id": m["avatar_id"],
            "frame_id": m["frame_id"],
            "week_completions": m["week_completions"] or 0,
        }
        for m in members
    ]
    return {
        "id": team["id"],
        "name": team["name"],
        "invite_code": team["invite_code"],
        "members": members_list,
        "team_week_total": sum(m["week_completions"] for m in members_list),
    }
