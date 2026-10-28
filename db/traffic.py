"""
Каналы трафика: у каждого канала/блогера своя ссылка t.me/<бот>?start=src_<код>, человек запоминается за тем каналом, из которого
пришёл первым, а статистика показывает не только «сколько пришло», но и качество: сколько дошло до привычки, вернулось, сколько
«мёртвых» (нажали /start и пропали), нет ли подозрительного всплеска регистраций, сколько друзей позвали и сколько заплатили.

Приглашения друзей (t.me/<бот>?start=<id> и friend_<id>) идут как отдельный канал «ref»; все, у кого канала нет, — «none».
"""
import re
import secrets
from datetime import datetime, timedelta, timezone

from .core import connect
from . import tracking

SOURCE_RE = re.compile(r"^[a-z0-9_]{3,24}$")
RESERVED = ("ref", "none", "organic", "admin")
KINDS = {"channel": "Телеграм-канал", "blogger": "Блогер", "ads": "Реклама", "friend": "Партнёр", "other": "Другое"}
ATTRIBUTION_WINDOW_MIN = 30          # привязать канал можно только свежему аккаунту: «первое касание» не перезаписывается
JUDGE_AFTER_HOURS = 24               # «мёртвым» человека считаем, только если с регистрации прошли сутки
SUSPICIOUS_DEAD_PCT = 70
SUSPICIOUS_BURST_SHARE = 0.6         # доля регистраций канала в одном десятиминутном окне
LABELS = {"ref": "Приглашения друзей", "none": "Без канала"}

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                                                           "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def slug(title):
    """Код ссылки из названия: «Фитнес-канал Аня» → fitnes_kanal_anya."""
    raw = "".join(_TRANSLIT.get(ch, ch) for ch in str(title or "").lower())
    code = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")[:24]
    return code


def parse_payload(payload):
    """Что пришло в /start: ('src', код) | ('partner', код ссылки участника) | ('friend', id) | ('ref', id) | (None, None)."""
    text = str(payload or "")
    if text.startswith("src_"):
        code = text[4:]
        return ("src", code) if SOURCE_RE.match(code) else (None, None)
    if text.startswith("p_") and 4 <= len(text) <= 24 and text[2:].isalnum():
        return "partner", text[2:].lower()
    if text.startswith("friend_") and text[7:].isdigit():
        return "friend", int(text[7:])
    if text.isdigit():
        return "ref", int(text)
    return None, None


def link(code, bot_username):
    return f"https://t.me/{bot_username}?start=src_{code}" if bot_username and code else None


# ---------------------------------------------------------------------------------------------------------------- каналы

def create_source(title, kind="channel", note="", code=None):
    title = " ".join(str(title or "").split())[:60]
    if len(title) < 2:
        return {"error": "invalid_title"}
    kind = kind if kind in KINDS else "other"
    wanted = (code or slug(title) or "").lower()
    if not code and len(wanted) < 3:
        wanted = f"ch_{secrets.token_hex(3)}"                                # название не дало код (иероглифы, эмодзи) — случайный
    if not SOURCE_RE.match(wanted) or wanted in RESERVED:
        return {"error": "invalid_code"}
    conn = connect()
    try:
        base, attempt = wanted, 1
        while conn.execute("SELECT 1 FROM traffic_sources WHERE code=?", (wanted,)).fetchone():
            if code:                                                      # код задан вручную — не подменяем молча
                return {"error": "code_taken"}
            attempt += 1
            wanted = f"{base[:20]}_{attempt}"
        conn.execute("INSERT INTO traffic_sources(code, title, kind, note) VALUES (?,?,?,?)", (wanted, title, kind, " ".join(str(note or "").split())[:200]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "code": wanted, "title": title, "kind": kind}


def list_sources(include_archived=False):
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM traffic_sources" + ("" if include_archived else " WHERE COALESCE(archived,0)=0") + " ORDER BY created_at DESC, code").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def archive_source(code):
    conn = connect()
    try:
        cur = conn.execute("UPDATE traffic_sources SET archived=1 WHERE code=?", (code,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def source_exists(code):
    conn = connect()
    try:
        return bool(conn.execute("SELECT 1 FROM traffic_sources WHERE code=?", (code,)).fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- привязка человека

def attribute(user_id, code, via="start"):
    """Запомнить, откуда пришёл человек. Первое касание не перезаписывается; привязать можно только новому аккаунту (моложе
    ATTRIBUTION_WINDOW_MIN минут). Возвращает True, если канал записан."""
    code = str(code or "").lower()
    if code != "ref" and (not SOURCE_RE.match(code) or not source_exists(code)):
        return False
    conn = connect()
    try:
        user = conn.execute("SELECT created_at, source_code FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if user is None or user["source_code"]:
            return False
        created = tracking._parse(user["created_at"])
        if created is None or _now() - created > timedelta(minutes=ATTRIBUTION_WINDOW_MIN):
            return False
        conn.execute("UPDATE users SET source_code=?, source_at=? WHERE telegram_id=? AND (source_code IS NULL OR source_code='')",
                     (code, tracking._fmt(_now()), user_id))
        conn.commit()
    finally:
        conn.close()
    tracking.track_server(user_id, "source_attributed", step=code, meta={"via": via})
    return True


def attribute_from_init_data(user_id, init_data):
    """Mini App открыли по ссылке t.me/<бот>/<приложение>?startapp=src_<код>: код приходит в initData как start_param."""
    try:
        from urllib.parse import parse_qsl
        param = dict(parse_qsl(init_data or "", keep_blank_values=True)).get("start_param", "")
    except Exception:
        return False
    kind, value = parse_payload(param)
    return attribute(user_id, value, via="app") if kind == "src" else False


# ---------------------------------------------------------------------------------------------------------------- статистика

def _burst_share(created_list):
    """Доля регистраций, попавших в самое плотное десятиминутное окно (всплеск = подозрение на накрутку)."""
    times = sorted(t for t in created_list if t)
    if len(times) < 2:
        return 0.0
    best, left = 1, 0
    for right in range(len(times)):
        while times[right] - times[left] > timedelta(minutes=10):
            left += 1
        best = max(best, right - left + 1)
    return best / len(times)


def channel_stats(days=30):
    """По каждому каналу (созданные ссылки, «ref», «none»): сколько пришло, дошли до шагов, возвращаются, «мёртвые», друзья, оплаты, подозрения."""
    days = max(1, min(int(days), 365))
    titles = {s["code"]: s for s in list_sources(include_archived=True)}
    codes = list(titles) + ["ref", "none"]
    out = []
    for code in codes:
        where, params = tracking._cohort(days, code)
        conn = connect()
        try:
            users, ids, marks = tracking._collect_marks(conn, where, params)
            if not ids:
                out.append(_empty(code, titles.get(code)))
                continue
            in_clause = ",".join("?" for _ in ids)
            active = conn.execute(f"SELECT COALESCE(SUM(active_seconds),0) AS n, COUNT(*) AS c FROM app_sessions WHERE user_id IN ({in_clause})", ids).fetchone()
            friends = conn.execute(f"SELECT COALESCE(SUM(referrals),0) AS n FROM users WHERE telegram_id IN ({in_clause})", ids).fetchone()
            revenue = conn.execute(f"SELECT COALESCE(SUM(amount),0) AS n FROM stars_payments WHERE user_id IN ({in_clause}) AND COALESCE(is_test,0)=0", ids).fetchone()
        finally:
            conn.close()
        total = len(ids)
        reached = lambda key: sum(1 for uid in ids if marks[key].get(uid))  # noqa: E731
        cutoff = _now() - timedelta(hours=JUDGE_AFTER_HOURS)
        judged = [uid for uid in ids if marks["registered"][uid] and marks["registered"][uid] <= cutoff]
        dead = [uid for uid in judged if not any(marks[k].get(uid) for k in ("survey", "opened", "habit", "ai"))]
        burst = _burst_share([marks["registered"][uid] for uid in ids])
        dead_pct = round(100 * len(dead) / len(judged), 1) if judged else 0.0
        pct = lambda n: round(100 * n / total, 1)  # noqa: E731
        habit_done, d1 = pct(reached("habit_done")), pct(reached("d1"))
        quality = round(min(100.0, 0.4 * habit_done + 0.3 * d1 + 0.2 * pct(reached("opened")) + 0.1 * (100 - dead_pct)), 1)
        suspicious = (len(judged) >= 10 and dead_pct >= SUSPICIOUS_DEAD_PCT) or (total >= 15 and burst >= SUSPICIOUS_BURST_SHARE)
        out.append({
            **_empty(code, titles.get(code)),
            "joined": total, "survey": pct(reached("survey")), "opened": pct(reached("opened")), "habit": pct(reached("habit")),
            "habit_done": habit_done, "ai": pct(reached("ai")), "d1": d1, "d7": pct(reached("d7")),
            "dead": len(dead), "dead_pct": dead_pct, "judged": len(judged), "burst_pct": round(100 * burst, 1),
            "avg_active_seconds": int(active["n"] / total), "sessions": int(active["c"]), "friends_invited": int(friends["n"]),
            "paid": reached("paid"), "revenue_stars": int(revenue["n"]), "quality": quality, "suspicious": bool(suspicious),
        })
    out.sort(key=lambda x: (-x["joined"], x["code"]))
    return out


def _empty(code, source):
    return {
        "code": code, "title": (source or {}).get("title") or LABELS.get(code, code), "kind": (source or {}).get("kind"),
        "note": (source or {}).get("note"), "archived": bool((source or {}).get("archived")), "created_at": (source or {}).get("created_at"),
        "joined": 0, "survey": 0.0, "opened": 0.0, "habit": 0.0, "habit_done": 0.0, "ai": 0.0, "d1": 0.0, "d7": 0.0, "dead": 0, "dead_pct": 0.0,
        "judged": 0, "burst_pct": 0.0, "avg_active_seconds": 0, "sessions": 0, "friends_invited": 0, "paid": 0, "revenue_stars": 0,
        "quality": 0.0, "suspicious": False,
    }
