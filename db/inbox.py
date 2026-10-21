"""
Центр уведомлений: одна лента всего, что приложение «сообщило» человеку, с разделами (вкладки сверху: Все, Системные, Адам, Друзья,
Подписчики, Подарки). Открывается колокольчиком в шапке; на колокольчике — число новых.

Лента СОБИРАЕТСЯ при открытии из того, что и так хранится в базе (подарки, поддержка и напоминания от друзей, подписки, парные задания,
сообщения Адама, события серии, «Что нового», приглашения), поэтому: ничего не надо дописывать в каждое место, где что-то происходит, и
история видна сразу, а не только с момента появления центра. Новый вид уведомления = ещё один «источник» в SOURCES: функция, которая по
человеку и дате отдаёт свежие записи нужного раздела. Тест `tests/test_inbox.py` следит, что у каждого источника есть раздел и что
разделы совпадают с вкладками в приложении.

«Новое» — всё, что появилось позже момента, когда человек последний раз открывал центр (users.inbox_seen_at). Первый раз метка ставится
«сейчас»: у старых пользователей бейдж не вспыхивает числом из прошлого. Заблокированных (в любую сторону) в ленте нет.
Тексты — без догадок о поле: «подарил(а)», «подписался(ась)».
"""
import logging
from datetime import datetime, timedelta, timezone

from .core import connect

logger = logging.getLogger(__name__)

# (ключ, название на вкладке). Порядок = порядок вкладок.
CATEGORIES = (
    ("all", "Все"),
    ("system", "Системные"),
    ("adam", "Адам"),
    ("friends", "Друзья"),
    ("followers", "Подписчики"),
    ("gifts", "Подарки"),
)
CATEGORY_KEYS = tuple(key for key, _ in CATEGORIES)

PER_SOURCE_LIMIT = 40       # сколько свежих записей берёт каждый источник
FEED_LIMIT = 80             # сколько всего отдаём в приложение
RECENT_DAYS = 45            # старше — не показываем: центр про свежее
PREVIEW_CHARS = 160


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _ts(value):
    """Время записи в одном виде: «ГГГГ-ММ-ДД ЧЧ:ММ:СС» (UTC)."""
    return str(value or "").replace("T", " ")[:19]


def _short(text, limit=PREVIEW_CHARS):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _item(key, category, kind, at, icon, title, text="", actor_id=None, action=None):
    """Запись ленты до подстановки имени: в title место для имени — {name}."""
    return {"id": key, "cat": category, "kind": kind, "at": _ts(at), "icon": icon, "title": title, "text": text,
            "actor_id": actor_id, "action": action or {}}


# ---------------------------------------------------------------------------------------------------------------- источники

def _gifts(conn, user_id, since):
    rows = conn.execute(
        "SELECT g.id, g.from_user_id, g.kind, g.amount, g.created_at, s.name AS item_name FROM gifts g "
        "LEFT JOIN shop_items s ON s.id = g.item_id WHERE g.to_user_id=? AND g.created_at>=? ORDER BY g.id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall()
    items = []
    for r in rows:
        if r["kind"] == "diamonds":
            icon, what = "💎", f"{int(r['amount'] or 1)} 💎"
        elif r["kind"] == "freeze":
            icon, what = "❄️", "заморозку серии"
        else:
            icon, what = "🎁", f"«{r['item_name'] or 'подарок'}»"
        items.append(_item(f"gift:{r['id']}", "gifts", f"gift_{r['kind']}", r["created_at"], icon,
                           f"{{name}} подарил(а) тебе {what}", actor_id=r["from_user_id"], action={"type": "gifts"}))
    for r in conn.execute(
        "SELECT id, note, created_at FROM admin_gifts WHERE user_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        items.append(_item(f"admin_gift:{r['id']}", "gifts", "gift_medal", r["created_at"], "🏅",
                           "Администратор подарил(а) тебе медаль", _short(r["note"]), action={"type": "gifts"}))
    return items


def _support_and_nudges(conn, user_id, since):
    items = []
    for r in conn.execute(
        "SELECT id, from_user_id, emoji, created_at FROM friend_reactions WHERE to_user_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        items.append(_item(f"reaction:{r['id']}", "friends", "friend_support", r["created_at"], "💌",
                           f"{{name}} поддержал(а) тебя {r['emoji'] or ''}".rstrip(), actor_id=r["from_user_id"], action={"type": "profile"}))
    for r in conn.execute(
        "SELECT id, from_user_id, created_at FROM friend_nudges WHERE to_user_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        items.append(_item(f"nudge:{r['id']}", "friends", "friend_nudge", r["created_at"], "👋",
                           "{name} напомнил(а) тебе отметить привычки", actor_id=r["from_user_id"], action={"type": "profile"}))
    return items


def _follows(conn, user_id, since):
    """Подписался на меня: если я подписан в ответ — это уже друг (раздел «Друзья», время — когда пара замкнулась)."""
    rows = conn.execute(
        "SELECT f.follower_id, f.created_at, b.created_at AS back_at FROM follows f "
        "LEFT JOIN follows b ON b.follower_id = f.followee_id AND b.followee_id = f.follower_id "
        "WHERE f.followee_id=? AND MAX(f.created_at, COALESCE(b.created_at, '')) >= ? "
        "ORDER BY MAX(f.created_at, COALESCE(b.created_at, '')) DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall()
    items = []
    for r in rows:
        if r["back_at"]:
            at = max(_ts(r["created_at"]), _ts(r["back_at"]))
            items.append(_item(f"friend:{r['follower_id']}", "friends", "friend_added", at, "🤝",
                               "{name} теперь в друзьях", "Подписаны друг на друга", actor_id=r["follower_id"], action={"type": "profile"}))
        else:
            items.append(_item(f"follow:{r['follower_id']}", "followers", "follower_new", r["created_at"], "➕",
                               "{name} подписался(ась) на тебя", actor_id=r["follower_id"], action={"type": "profile"}))
    return items


def _rescues(conn, user_id, since):
    """Друг вернул мне погасшую серию (db/rescue.py)."""
    rows = conn.execute(
        "SELECT id, helper_id, restored_streak, created_at FROM streak_rescues WHERE owner_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall()
    return [
        _item(f"rescue:{r['id']}", "friends", "friend_rescue", r["created_at"], "💛", "{name} спас(ла) твою серию",
              f"Серия {int(r['restored_streak'] or 0)} дн. снова горит", actor_id=r["helper_id"], action={"type": "home"})
        for r in rows
    ]


def _pair_quests(conn, user_id, since):
    items = []
    rows = conn.execute(
        "SELECT id, inviter_id, invitee_id, status, created_at, accepted_at, finished_at FROM pair_quests "
        "WHERE (inviter_id=? OR invitee_id=?) AND COALESCE(finished_at, accepted_at, created_at)>=? ORDER BY id DESC LIMIT ?",
        (user_id, user_id, since, PER_SOURCE_LIMIT),
    ).fetchall()
    for r in rows:
        mine = r["inviter_id"] == user_id
        partner = r["invitee_id"] if mine else r["inviter_id"]
        action = {"type": "pair"}
        if r["status"] == "pending" and not mine:
            items.append(_item(f"pair:{r['id']}:invite", "friends", "pair_invite", r["created_at"], "🤝",
                               "{name} зовёт тебя в парное задание", "Прими приглашение на Главной", partner, action))
        if mine and r["accepted_at"]:
            items.append(_item(f"pair:{r['id']}:accepted", "friends", "pair_accepted", r["accepted_at"], "✅",
                               "{name} принял(а) приглашение в парное задание", "", partner, action))
        if r["status"] == "completed" and r["finished_at"]:
            items.append(_item(f"pair:{r['id']}:done", "friends", "pair_done", r["finished_at"], "🏆",
                               "Парное задание с {name} выполнено", "Открой сундук на Главной", partner, action))
        if r["status"] == "expired" and r["finished_at"]:
            items.append(_item(f"pair:{r['id']}:expired", "friends", "pair_expired", r["finished_at"], "⌛",
                               "Парное задание с {name} закончилось", "Не хватило привычек — попробуй ещё раз", partner, action))
    return items


def _adam(conn, user_id, since):
    rows = conn.execute(
        "SELECT c.id, c.created_at, c.scenario, m.message FROM adam_checkins c LEFT JOIN ai_messages m ON m.id = c.message_id "
        "WHERE c.user_id=? AND c.status='sent' AND c.created_at>=? ORDER BY c.id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall()
    return [
        _item(f"adam:{r['id']}", "adam", "adam_checkin", r["created_at"], "💬", "Я написал тебе", _short(r["message"]), action={"type": "chat"})
        for r in rows
    ]


def _system(conn, user_id, since):
    items = []
    for r in conn.execute(
        "SELECT day, status, streak_after, ai_message, created_at FROM streak_days WHERE user_id=? AND status IN ('freeze','missed') AND created_at>=? "
        "ORDER BY day DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        if r["status"] == "freeze" and str(r["ai_message"] or "").startswith("💛"):
            continue                    # день вернул друг — об этом отдельная запись «спас(ла) твою серию» (_rescues), а не «заморозка»
        if r["status"] == "freeze":
            kept = int(r["streak_after"] or 0)
            items.append(_item(f"streak:{r['day']}:freeze", "system", "streak_freeze", r["created_at"], "❄️", "Я сохранил твой день заморозкой",
                               f"Серия {kept} дн. осталась с тобой" if kept else "Серия осталась с тобой", action={"type": "home"}))
        else:
            items.append(_item(f"streak:{r['day']}:missed", "system", "streak_lost", r["created_at"], "🔥", "Серия прервалась",
                               "Начнём заново сегодня — первый день самый важный", action={"type": "home"}))
    for r in conn.execute(
        "SELECT id, milestone, status, frame, created_at FROM streak_rewards WHERE user_id=? AND created_at>=? ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        items.append(_item(f"reward:{r['id']}", "system", "streak_status", r["created_at"], "🏅", f"Новый статус «{r['status']}»",
                           f"Серия {int(r['milestone'])} дн. — открыл для тебя рамку «{r['frame']}»", action={"type": "profile_self"}))
    for r in conn.execute(
        "SELECT id, title, body, created_at FROM changelog_entries WHERE created_at>=? ORDER BY id DESC LIMIT 5", (since,)
    ).fetchall():
        items.append(_item(f"changelog:{r['id']}", "system", "whats_new", r["created_at"], "✨", f"Новое от меня: {r['title']}", _short(r["body"])))
    for r in conn.execute(
        "SELECT id, invitee_id, inviter_coins, paid_at FROM referral_invites WHERE inviter_id=? AND status='paid' AND paid_at>=? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, since, PER_SOURCE_LIMIT),
    ).fetchall():
        items.append(_item(f"referral:{r['id']}", "system", "referral_paid", r["paid_at"], "🎉",
                           f"{{name}} пришёл(ла) по твоему приглашению: +{int(r['inviter_coins'] or 0)} Adam Coin", actor_id=r["invitee_id"],
                           action={"type": "profile"}))
    for r in conn.execute(
        "SELECT id, invitee_coins, paid_at FROM referral_invites WHERE invitee_id=? AND status='paid' AND paid_at>=? ORDER BY id DESC LIMIT 1",
        (user_id, since),
    ).fetchall():
        items.append(_item(f"referral_bonus:{r['id']}", "system", "referral_bonus", r["paid_at"], "🎁",
                           f"Дарю бонус за вход по приглашению: +{int(r['invitee_coins'] or 0)} Adam Coin"))
    return items


# Записи этих разделов без отправителя-человека — слова Адама: «Я написал тебе», «Я сохранил твой день заморозкой».
VOICE_CATEGORIES = ("adam", "system")

# Источник = функция (соединение, человек, «не раньше») → список записей одного или нескольких разделов.
SOURCES = (_gifts, _support_and_nudges, _follows, _rescues, _pair_quests, _adam, _system)


# ---------------------------------------------------------------------------------------------------------------- метка «прочитано»

def get_seen_at(user_id):
    """Когда человек последний раз открывал центр. Первый раз — ставим «сейчас» (старое считаем прочитанным)."""
    conn = connect()
    try:
        row = conn.execute("SELECT inbox_seen_at FROM users WHERE telegram_id=?", (int(user_id),)).fetchone()
        if row is None:
            return _now()
        if not row["inbox_seen_at"]:
            now = _now()
            conn.execute("UPDATE users SET inbox_seen_at=? WHERE telegram_id=?", (now, int(user_id)))
            conn.commit()
            return now
        return _ts(row["inbox_seen_at"])
    finally:
        conn.close()


def mark_seen(user_id):
    conn = connect()
    try:
        conn.execute("UPDATE users SET inbox_seen_at=? WHERE telegram_id=?", (_now(), int(user_id)))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------------------- лента

def _blocked_ids(conn, user_id):
    rows = conn.execute(
        "SELECT blocked_id AS other FROM user_blocks WHERE blocker_id=? UNION SELECT blocker_id AS other FROM user_blocks WHERE blocked_id=?",
        (user_id, user_id),
    ).fetchall()
    return {r["other"] for r in rows}


def _collect(user_id, since):
    conn = connect()
    try:
        items = []
        for source in SOURCES:
            try:
                items.extend(source(conn, int(user_id), since))
            except Exception:       # один сломанный источник не должен ронять всю ленту
                logger.exception("inbox: источник %s не отдал записи (%s)", getattr(source, "__name__", source), user_id)
        blocked = _blocked_ids(conn, int(user_id))
        items = [it for it in items if it["actor_id"] not in blocked]
        actor_ids = sorted({it["actor_id"] for it in items if it["actor_id"]})
        actors = {}
        if actor_ids:
            marks = ",".join("?" for _ in actor_ids)
            for r in conn.execute(
                f"SELECT telegram_id, first_name, username, handle, avatar_id, frame_id FROM users WHERE telegram_id IN ({marks})", actor_ids
            ).fetchall():
                actors[r["telegram_id"]] = {
                    "telegram_id": r["telegram_id"], "first_name": r["first_name"] or r["username"] or "Друг", "handle": r["handle"],
                    "avatar_id": r["avatar_id"] or "default", "frame_id": r["frame_id"] or "default",
                }
    finally:
        conn.close()
    return items, actors


def _decorate(items, actors, seen_at):
    out = []
    for it in items:
        actor = actors.get(it["actor_id"]) if it["actor_id"] else None
        name = actor["first_name"] if actor else "Друг"
        action = dict(it["action"])
        if action.get("type") == "profile" and actor:
            action["id"] = actor["telegram_id"]
        out.append({
            "id": it["id"], "cat": it["cat"], "kind": it["kind"], "at": it["at"], "icon": it["icon"],
            "title": it["title"].replace("{name}", name), "text": it["text"], "actor": actor,
            "action": action if action.get("type") != "profile" or actor else {},
            "voice": not actor and it["cat"] in VOICE_CATEGORIES,       # пишет Адам от первого лица — в приложении рядом его аватарка
            "unread": it["at"] > seen_at,
        })
    return out


def _category_counts(items):
    unread = {key: 0 for key in CATEGORY_KEYS}
    for it in items:
        if it["unread"]:
            unread["all"] += 1
            unread[it["cat"]] = unread.get(it["cat"], 0) + 1
    return [{"key": key, "label": label, "unread": unread[key]} for key, label in CATEGORIES]


def get_feed(user_id):
    """Лента для центра уведомлений: {"items": [...новые сверху], "categories": [{key, label, unread}], "seen_at"}."""
    seen_at = get_seen_at(user_id)
    items, actors = _collect(user_id, _days_ago(RECENT_DAYS))
    items.sort(key=lambda it: (it["at"], it["id"]), reverse=True)
    feed = _decorate(items[:FEED_LIMIT], actors, seen_at)
    return {"items": feed, "categories": _category_counts(feed), "seen_at": seen_at}


def get_summary(user_id):
    """Только числа — для колокольчика: берём лишь то, что новее метки «прочитано» (дёшево)."""
    seen_at = get_seen_at(user_id)
    items, actors = _collect(user_id, max(seen_at, _days_ago(RECENT_DAYS)))
    fresh = _decorate(items, actors, seen_at)
    counts = _category_counts(fresh)
    return {"unread": counts[0]["unread"], "categories": {c["key"]: c["unread"] for c in counts}}
