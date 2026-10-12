"""
Расход ИИ по людям и функциям.

Все вызовы модели идут через multi_agent._ask и пишут расход в ai_token_log (db/analytics.py::log_ai_tokens) — но раньше без
«чей это вызов». Теперь код, который зовёт модель ради конкретного человека, оборачивается в scope(user_id, feature): чат, «Адам
пишет первым», утреннее сообщение, совет дня, разбор недели, анкета и т.д. Что вызвано вне scope — в сводке «без владельца».

Ограничения (под контролем админа, на случай бага или перерасхода):
  • ai_blocked_until — ИИ для человека отключён до этого момента (1 час / 24 часа / неделя);
  • ai_tokens_day_limit — личный дневной лимит токенов (None — свой лимит не задан; общий — config.AI_USER_DAY_TOKENS, 0 = выключен).
Проверка стоит прямо в multi_agent._ask: любая функция на модели у ограниченного человека получает AIRestricted и уходит в запасной
текст/сообщение об ошибке, а чат заранее отвечает по-человечески (webapp/routes_ai_miniapp.py).
"""
import contextvars
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from .core import connect

FEATURES = {
    "chat": "Чат с Адамом", "intent": "Команды в чате", "checkin": "Адам пишет первым", "morning": "Утреннее сообщение",
    "tip": "Совет дня", "progress": "Разбор прогресса", "weekly": "Недельный разбор", "monthly": "Месячный разбор",
    "survey": "Анкета и первый шаг", "memory": "Память о человеке", "goal": "Прогресс по цели", "summary": "ИИ-сводка для админа",
    "voice": "Голос", "other": "Прочее",
}
BLOCK_HOURS = (1, 24, 168)
DAY_LIMITS = (20_000, 50_000, 100_000, 200_000)

_current = contextvars.ContextVar("ai_usage_scope", default=None)


class AIRestricted(RuntimeError):
    """Модель для этого человека сейчас отключена админом или исчерпан его дневной лимит токенов."""

    def __init__(self, reason, until=None):
        super().__init__(reason)
        self.reason = reason
        self.until = until


@contextmanager
def scope(user_id, feature):
    """Вызовы модели внутри блока записываются на этого человека и эту функцию."""
    token = _current.set({"user_id": int(user_id) if user_id is not None else None, "feature": feature})
    try:
        yield
    finally:
        _current.reset(token)


def current():
    return _current.get() or {}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(value):
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------- запись

def record(tokens, provider="", model="", prompt=0, completion=0):
    """Одна запись расхода. Вызывается из log_ai_tokens; сбой записи не должен ронять ответ человеку."""
    info = current()
    try:
        conn = connect()
        conn.execute(
            "INSERT INTO ai_token_log(day, tokens, provider, user_id, feature, model, prompt_tokens, completion_tokens) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(date.today()), int(tokens), provider or "", info.get("user_id"), info.get("feature") or "other", model or "",
             int(prompt or 0), int(completion or 0)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------------------------- статистика

def tokens_today(user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n FROM ai_token_log WHERE user_id=? AND day=?", (user_id, str(date.today()))).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0)


def user_usage(user_id):
    """Сводка по человеку: сегодня / 7 дней / всего, вызовов, по функциям за 7 дней, по дням за 14 дней, текущие ограничения."""
    conn = connect()
    try:
        today = date.today()
        since7, since14 = str(today - timedelta(days=6)), str(today - timedelta(days=13))
        total = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n, COUNT(*) AS c FROM ai_token_log WHERE user_id=?", (user_id,)).fetchone()
        day = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n, COUNT(*) AS c FROM ai_token_log WHERE user_id=? AND day=?", (user_id, str(today))).fetchone()
        week = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n, COUNT(*) AS c FROM ai_token_log WHERE user_id=? AND day>=?", (user_id, since7)).fetchone()
        by_feature = conn.execute(
            "SELECT COALESCE(feature,'other') AS feature, SUM(tokens) AS n, COUNT(*) AS c FROM ai_token_log "
            "WHERE user_id=? AND day>=? GROUP BY feature ORDER BY n DESC", (user_id, since7)).fetchall()
        series = conn.execute(
            "SELECT day, SUM(tokens) AS n FROM ai_token_log WHERE user_id=? AND day>=? GROUP BY day ORDER BY day", (user_id, since14)).fetchall()
        user = conn.execute("SELECT ai_blocked_until, ai_tokens_day_limit FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    blocked_until = _parse(user["ai_blocked_until"]) if user else None
    return {
        "today": int(day["n"]), "today_calls": int(day["c"]), "week": int(week["n"]), "week_calls": int(week["c"]),
        "total": int(total["n"]), "total_calls": int(total["c"]),
        "avg_per_call": int(week["n"] / week["c"]) if week["c"] else 0,
        "by_feature": [{"feature": r["feature"], "label": FEATURES.get(r["feature"], r["feature"]), "tokens": int(r["n"]), "calls": int(r["c"])} for r in by_feature],
        "series": [{"day": r["day"], "tokens": int(r["n"])} for r in series],
        "blocked_until": blocked_until.isoformat() if blocked_until and blocked_until > _now() else None,
        "day_limit": user["ai_tokens_day_limit"] if user else None,
    }


def overview(days=7, limit=15):
    """Сводка для админки: сколько всего за период, по функциям, по дням, кто больше всех тратит, сколько вызовов без владельца."""
    days = max(1, min(int(days), 90))
    since = str(date.today() - timedelta(days=days - 1))
    conn = connect()
    try:
        total = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n, COUNT(*) AS c FROM ai_token_log WHERE day>=?", (since,)).fetchone()
        unowned = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n FROM ai_token_log WHERE day>=? AND user_id IS NULL", (since,)).fetchone()
        today = conn.execute("SELECT COALESCE(SUM(tokens),0) AS n FROM ai_token_log WHERE day=?", (str(date.today()),)).fetchone()
        by_feature = conn.execute(
            "SELECT COALESCE(feature,'other') AS feature, SUM(tokens) AS n, COUNT(*) AS c FROM ai_token_log WHERE day>=? GROUP BY feature ORDER BY n DESC",
            (since,)).fetchall()
        series = conn.execute("SELECT day, SUM(tokens) AS n FROM ai_token_log WHERE day>=? GROUP BY day ORDER BY day", (since,)).fetchall()
        top = conn.execute(
            "SELECT l.user_id AS user_id, SUM(l.tokens) AS n, COUNT(*) AS c, u.username AS username, u.first_name AS first_name, "
            "u.ai_blocked_until AS blocked, u.ai_tokens_day_limit AS day_limit "
            "FROM ai_token_log l LEFT JOIN users u ON u.telegram_id = l.user_id "
            "WHERE l.day>=? AND l.user_id IS NOT NULL GROUP BY l.user_id ORDER BY n DESC LIMIT ?", (since, int(limit))).fetchall()
        users_active = conn.execute("SELECT COUNT(DISTINCT user_id) AS n FROM ai_token_log WHERE day>=? AND user_id IS NOT NULL", (since,)).fetchone()
    finally:
        conn.close()
    grand = int(total["n"])
    now = _now()
    return {
        "days": days, "tokens": grand, "calls": int(total["c"]), "today": int(today["n"]),
        "unowned": int(unowned["n"]), "users": int(users_active["n"]),
        "avg_per_user": int(grand / users_active["n"]) if users_active["n"] else 0,
        "by_feature": [{"feature": r["feature"], "label": FEATURES.get(r["feature"], r["feature"]), "tokens": int(r["n"]), "calls": int(r["c"]),
                        "share": round(100 * r["n"] / grand, 1) if grand else 0.0} for r in by_feature],
        "series": [{"day": r["day"], "tokens": int(r["n"])} for r in series],
        "top": [{
            "telegram_id": r["user_id"], "username": r["username"], "first_name": r["first_name"], "tokens": int(r["n"]), "calls": int(r["c"]),
            "share": round(100 * r["n"] / grand, 1) if grand else 0.0, "day_limit": r["day_limit"],
            "blocked": bool(_parse(r["blocked"]) and _parse(r["blocked"]) > now),
        } for r in top],
    }


# ---------------------------------------------------------------------------------------------------------------- ограничения

def _default_day_limit():
    try:
        from config import AI_USER_DAY_TOKENS
        return int(AI_USER_DAY_TOKENS or 0)
    except Exception:
        return 0


def check_allowed(user_id):
    """{"ok": True} либо {"ok": False, "reason": "blocked"|"day_limit", "until": iso|None}."""
    if user_id is None:
        return {"ok": True}
    conn = connect()
    try:
        row = conn.execute("SELECT ai_blocked_until, ai_tokens_day_limit FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": True}
    until = _parse(row["ai_blocked_until"])
    if until and until > _now():
        return {"ok": False, "reason": "blocked", "until": until.isoformat()}
    limit = row["ai_tokens_day_limit"] or _default_day_limit()
    if limit and tokens_today(user_id) >= int(limit):
        return {"ok": False, "reason": "day_limit", "until": None}
    return {"ok": True}


def restriction_message(reason):
    """Что сказать человеку, если ИИ для него ограничен (чат, пока не вернёмся к ответу)."""
    if reason == "day_limit":
        return "На сегодня разговоров с Адамом хватит — лимит исчерпан. Завтра он снова на связи."
    return "Адам сейчас отдыхает — чат временно недоступен. Загляни попозже."


def _admin_update(user_id, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    conn = connect()
    try:
        cur = conn.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", (*fields.values(), user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def block_ai(user_id, hours):
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        return {"error": "invalid_hours"}
    if hours not in BLOCK_HOURS:
        return {"error": "invalid_hours"}
    until = (_now() + timedelta(hours=hours)).isoformat()
    return {"ok": True, "until": until} if _admin_update(user_id, ai_blocked_until=until) else {"error": "user_not_found"}


def unblock_ai(user_id):
    return {"ok": True} if _admin_update(user_id, ai_blocked_until=None) else {"error": "user_not_found"}


def set_day_limit(user_id, tokens):
    """tokens=None — снять личный лимит."""
    if tokens is None:
        return {"ok": True, "limit": None} if _admin_update(user_id, ai_tokens_day_limit=None) else {"error": "user_not_found"}
    try:
        tokens = int(tokens)
    except (TypeError, ValueError):
        return {"error": "invalid_limit"}
    if tokens not in DAY_LIMITS:
        return {"error": "invalid_limit"}
    return {"ok": True, "limit": tokens} if _admin_update(user_id, ai_tokens_day_limit=tokens) else {"error": "user_not_found"}
