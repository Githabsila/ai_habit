"""
Доступ к приложению под контролем админа.

Три режима аккаунта (users.access_mode):
  open  — как у первых тестировщиков: без ограничений, без триала и без сообщений об оплате. Так у всех по умолчанию.
  timed — временный доступ, который выдаёт админ (3 / 7 / 14 дней): по access_until доступ закрывается, пока админ не продлит.
  trial — аккаунт идёт по сценарию триала (db/subscription.py: 3 дня с trial_started_at → доступ закрывается → оплата).
          Нужен, чтобы проверять триал с новых аккаунтов, не трогая остальных.

Подарочные дни (gift_days) — для тех, кто уже платит или прошёл триал: у платящего сдвигается дата окончания подписки
(subscription_paid_until, она же «срок списания»), у остальных удлиняется бесплатный период (access_until).

Любое изменение пишется в access_log: кто, кому, на сколько, что было до и после.
"""
from datetime import datetime, timedelta, timezone

from .core import connect
from .users import get_user

MODE_OPEN, MODE_TIMED, MODE_TRIAL = "open", "timed", "trial"
MODES = (MODE_OPEN, MODE_TIMED, MODE_TRIAL)
ACCESS_MODES = MODES
MODE_LABELS = {MODE_OPEN: "Свободный (тестировщик)", MODE_TIMED: "Временный доступ", MODE_TRIAL: "Сценарий триала"}

GRANT_DAYS = (3, 7, 14)          # временный доступ
GIFT_DAYS = (3, 7, 14, 30)       # подарочные дни
MAX_DAYS = 365


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(moment):
    return moment.isoformat() if moment else None


def _row_mode(user):
    mode = (user["access_mode"] if user is not None and "access_mode" in user.keys() else None) or MODE_OPEN
    return mode if mode in MODES else MODE_OPEN


def _field(user, name):
    return _parse(user[name]) if user is not None and name in user.keys() else None


def trial_window_end(user):
    """Когда кончается триал в режиме 'trial' (местная полночь человека после TRIAL_DAYS дней от trial_started_at; в UTC без tzinfo), иначе None."""
    from .trial import window_end_utc
    if user is None or _row_mode(user) != MODE_TRIAL:
        return None
    return window_end_utc(user)


def free_end(user):
    """Конец бесплатного периода: позже из access_until и конца триала (режим 'trial'). None — бесплатного периода нет."""
    ends = [x for x in (_field(user, "access_until"), trial_window_end(user)) if x is not None]
    return max(ends) if ends else None


def free_period_active(user):
    end = free_end(user)
    return bool(end and end > _now())


def trial_scenario_for_user(user):
    """Идёт ли для человека сценарий триала (напоминания «день 2», «последний день», окно оплаты в стартовом квизе): режим 'trial'
    или общий гейт подписки (config.SUBSCRIPTION_GATE_ENABLED) для обычного аккаунта. У тестировщиков (open/timed) — нет.
    Принимает строку пользователя (users.*) — планировщик перебирает всех, лишний запрос на каждого не нужен."""
    if user is None:
        return False
    mode = _row_mode(user)
    if mode == MODE_TRIAL:
        return True
    if mode == MODE_TIMED:
        return False
    from .subscription import gate_applies_to_user
    return gate_applies_to_user(user)


def trial_scenario_active(user_id):
    return trial_scenario_for_user(get_user(user_id))


def get_access(user_id):
    """Сводка для админки и приложения: режим, до какого числа бесплатный период, сколько дней осталось, сколько оплачено."""
    user = get_user(user_id)
    if user is None:
        return None
    mode = _row_mode(user)
    now = _now()
    until = _field(user, "access_until")
    end = free_end(user)
    paid_until = _field(user, "subscription_paid_until")
    paid_active = bool(paid_until and paid_until > now)
    from .subscription import bot_access_allowed, get_trial_day
    from config import SUBSCRIPTION_TRIAL_DAYS
    days_left = None
    if end is not None and end > now:
        seconds = (end - now).total_seconds()
        days_left = int(seconds // 86400) + (1 if seconds % 86400 else 0)
    return {
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "access_until": _iso(until),
        "free_end": _iso(end),
        "days_left": days_left,
        "free_active": bool(end and end > now),
        "paid_until": _iso(paid_until),
        "paid_active": paid_active,
        "trial_started_at": user["trial_started_at"] if "trial_started_at" in user.keys() else None,
        "trial_day": get_trial_day(user_id) if mode == MODE_TRIAL else None,
        "trial_days_total": SUBSCRIPTION_TRIAL_DAYS,
        "allowed": bot_access_allowed(user_id),
        "paywall": trial_scenario_active(user_id),
    }


def public_access(user_id):
    """То, что получает само приложение (поле access в /api/bootstrap): без дат оплаты и служебных полей."""
    info = get_access(user_id)
    if info is None:
        return {"mode": MODE_OPEN, "paywall": False, "allowed": True}
    result = {key: info[key] for key in ("mode", "paywall", "allowed", "days_left", "free_active")}
    if info["paywall"]:
        # Аккаунту в сценарии триала приложение показывает карточку и окна триала: день, цена сегодня, серия, условие канала (db/trial.py).
        from .trial import public_trial
        result["trial"] = public_trial(user_id)
    return result


# ---------------------------------------------------------------------------------------------------------------- изменения

def _log(conn, user_id, admin_id, action, days, before, after, note=None):
    conn.execute(
        """INSERT INTO access_log(user_id, admin_id, action, days, mode_before, mode_after, until_before, until_after,
                                  paid_before, paid_after, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, admin_id, action, days, before["mode"], after["mode"], before["until"], after["until"],
         before["paid"], after["paid"], note),
    )


def _snapshot(user):
    return {
        "mode": _row_mode(user),
        "until": user["access_until"] if "access_until" in user.keys() else None,
        "paid": user["subscription_paid_until"] if "subscription_paid_until" in user.keys() else None,
    }


def _check_days(days, allowed=None):
    try:
        days = int(days)
    except (TypeError, ValueError):
        return None
    if days < 1 or days > MAX_DAYS:
        return None
    if allowed is not None and days not in allowed:
        return None
    return days


def _apply(user_id, admin_id, action, days, fn, note=None):
    """Читает пользователя, применяет fn(user, conn) → ({поле: значение}) и пишет в журнал. Возвращает get_access или {"error"}."""
    user = get_user(user_id)
    if user is None:
        return {"error": "user_not_found"}
    before = _snapshot(user)
    conn = connect()
    try:
        outcome = fn(user, conn)
        if isinstance(outcome, dict) and "error" in outcome:
            return outcome
        changes = outcome or {}
        if changes:
            sets = ", ".join(f"{name}=?" for name in changes)
            conn.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", (*changes.values(), user_id))
        after = dict(before)
        if "access_mode" in changes:
            after["mode"] = changes["access_mode"]
        if "access_until" in changes:
            after["until"] = changes["access_until"]
        if "subscription_paid_until" in changes:
            after["paid"] = changes["subscription_paid_until"]
        _log(conn, user_id, admin_id, action, days, before, after, note)
        conn.commit()
    finally:
        conn.close()
    return get_access(user_id)


def _extended(user, days):
    """Новый конец бесплатного периода: считаем от самого позднего из «сейчас», access_until и конца триала."""
    now = _now()
    base = max([x for x in (now, _field(user, "access_until"), trial_window_end(user)) if x is not None])
    return _iso(base + timedelta(days=days))


def grant_access(user_id, days, admin_id=None):
    """Временный доступ на days дней (3 / 7 / 14). Свободному аккаунту включает режим 'timed' и отсчитывает от сегодня; у остальных
    удлиняет бесплатный период."""
    days = _check_days(days, GRANT_DAYS)
    if days is None:
        return {"error": "invalid_days"}

    def change(user, conn):
        mode = _row_mode(user)
        until = _extended(user, days)
        return {"access_mode": MODE_TIMED if mode == MODE_OPEN else mode, "access_until": until}

    return _apply(user_id, admin_id, "grant", days, change)


def gift_days(user_id, days, admin_id=None):
    """Подарочные дни (3 / 7 / 14 / 30). Платящему сдвигают дату окончания подписки — вместе с ней переносится и срок списания;
    остальным удлиняют бесплатный период. У свободного (безлимитного) аккаунта подарок не нужен."""
    days = _check_days(days, GIFT_DAYS)
    if days is None:
        return {"error": "invalid_days"}

    def change(user, conn):
        now = _now()
        paid = _field(user, "subscription_paid_until")
        if paid and paid > now:
            return {"subscription_paid_until": _iso(paid + timedelta(days=days))}
        if _row_mode(user) == MODE_OPEN:
            return {"error": "unlimited"}
        return {"access_until": _extended(user, days)}

    return _apply(user_id, admin_id, "gift", days, change)


def set_unlimited(user_id, admin_id=None):
    """Снять ограничения: режим 'open' (как у первых тестировщиков), бесплатный период и триал сбрасываются."""
    return _apply(user_id, admin_id, "unlimited", None,
                  lambda user, conn: {"access_mode": MODE_OPEN, "access_until": None, "trial_started_at": None})


def start_trial_mode(user_id, admin_id=None, restart=False):
    """Включить сценарий триала для аккаунта: триал идёт с сегодняшнего дня (день 1). restart=True — начать триал заново."""
    def change(user, conn):
        started = user["trial_started_at"] if "trial_started_at" in user.keys() else None
        if _row_mode(user) == MODE_TRIAL and started and not restart:
            return {}
        return {"access_mode": MODE_TRIAL, "trial_started_at": _iso(_now()), "access_until": None}

    return _apply(user_id, admin_id, "trial_restart" if restart else "trial_on", None, change)


def shift_trial_day(user_id, day, admin_id=None):
    """Для проверки триала: сдвинуть старт так, чтобы СЕГОДНЯ у аккаунта был день N (1…10) — «машина времени» вместо ожидания трёх суток.
    Только для аккаунтов в режиме «trial». Отметки привычек и серия не трогаются."""
    from datetime import time as _time
    from zoneinfo import ZoneInfo
    from .streak import get_timezone
    try:
        day = int(day)
    except (TypeError, ValueError):
        return {"error": "invalid_day"}
    if day < 1 or day > 10:
        return {"error": "invalid_day"}

    def change(user, conn):
        if _row_mode(user) != MODE_TRIAL:
            return {"error": "not_in_trial"}
        try:
            tz = ZoneInfo(get_timezone(user_id))
        except Exception:
            tz = ZoneInfo("UTC")
        today = datetime.now(timezone.utc).astimezone(tz).date()
        start_local = datetime.combine(today - timedelta(days=day - 1), _time(8, 0), tzinfo=tz)
        return {"trial_started_at": _iso(start_local.astimezone(timezone.utc).replace(tzinfo=None))}

    return _apply(user_id, admin_id, "trial_shift", day, change)


def get_access_log(user_id, limit=15):
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM access_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, int(limit))).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def access_overview(soon_days=3, limit=200):
    """Для админки: все, у кого не безлимит (временный доступ и сценарий триала), с остатком дней; ближайшие к концу — первыми."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT telegram_id FROM users WHERE COALESCE(access_mode,'open') IN ('timed','trial') AND COALESCE(banned,0)=0 LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        user = get_user(row["telegram_id"])
        info = get_access(row["telegram_id"])
        items.append({
            "telegram_id": user["telegram_id"], "username": user["username"], "first_name": user["first_name"],
            **{k: info[k] for k in ("mode", "mode_label", "days_left", "free_end", "free_active", "allowed", "paid_active", "trial_day")},
        })
    items.sort(key=lambda x: (x["free_end"] is None, x["free_end"] or ""))
    return {
        "items": items,
        "counts": {
            "timed": sum(1 for x in items if x["mode"] == MODE_TIMED),
            "trial": sum(1 for x in items if x["mode"] == MODE_TRIAL),
            "expired": sum(1 for x in items if not x["allowed"]),
            "soon": sum(1 for x in items if x["allowed"] and x["days_left"] is not None and x["days_left"] <= soon_days),
        },
    }
