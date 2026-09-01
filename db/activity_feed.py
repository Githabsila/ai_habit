"""
Roadmap #18 — лента активности друзей. "Друзья" = участники твоей команды
(db/teams.py, roadmap #16) + все, с кем ты хоть раз обменялся реакцией
(db/reactions.py, roadmap #19) — без отдельной системы заявок в друзья.
События логируются точечно (не каждая мелочь) — достижение, рубеж серии,
вступление в команду — чтобы лента оставалась сигналом, не шумом.
"""
import json

from .core import connect

EVENT_LABELS = {
    "achievement": "🏅 получил(а) достижение",
    "streak_milestone": "🔥 держит серию",
    "joined_team": "🤝 вступил(а) в команду",
    "level_up": "⭐ достиг(ла) уровня",
}


def log_activity_event(user_id, event_type, payload=None):
    if event_type not in EVENT_LABELS:
        return
    conn = connect()
    conn.execute(
        "INSERT INTO activity_events(user_id, event_type, payload) VALUES (?,?,?)",
        (user_id, event_type, json.dumps(payload) if payload else None),
    )
    conn.commit()
    conn.close()


def _get_friend_ids(user_id):
    conn = connect()
    cursor = conn.cursor()
    friend_ids = set()

    cursor.execute("SELECT team_id FROM team_members WHERE user_id=?", (user_id,))
    team_row = cursor.fetchone()
    if team_row:
        cursor.execute("SELECT user_id FROM team_members WHERE team_id=? AND user_id != ?", (team_row["team_id"], user_id))
        friend_ids.update(r["user_id"] for r in cursor.fetchall())

    cursor.execute(
        "SELECT DISTINCT to_user_id as uid FROM friend_reactions WHERE from_user_id=? "
        "UNION SELECT DISTINCT from_user_id as uid FROM friend_reactions WHERE to_user_id=?",
        (user_id, user_id),
    )
    friend_ids.update(r["uid"] for r in cursor.fetchall())
    conn.close()
    return friend_ids


def get_friend_activity_feed(user_id, limit=20):
    friend_ids = _get_friend_ids(user_id)
    if not friend_ids:
        return []

    placeholders = ",".join("?" * len(friend_ids))
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(f"""
        SELECT e.event_type, e.payload, e.created_at, u.first_name, u.telegram_id
        FROM activity_events e
        JOIN users u ON u.telegram_id = e.user_id
        WHERE e.user_id IN ({placeholders})
        ORDER BY e.id DESC
        LIMIT ?
    """, (*friend_ids, limit))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        payload = json.loads(r["payload"]) if r["payload"] else {}
        label = EVENT_LABELS.get(r["event_type"], r["event_type"])
        detail = payload.get("detail", "")
        result.append({
            "telegram_id": r["telegram_id"],
            "first_name": r["first_name"],
            "event_type": r["event_type"],
            "label": label,
            "detail": detail,
            "created_at": str(r["created_at"]),
        })
    return result
