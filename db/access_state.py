"""
Состояния доступа (ТЗ «Адам. Триал, подписка и приглашения друзей», 28.10.2026, §1, §4, §7): одно приложение, у человека одно понятное состояние.

  open         — свободный доступ (первые тестировщики и все, пока общий гейт подписки выключен): триала и оплаты у такого аккаунта нет;
  timed        — временный доступ от админа (access_until);
  not_started  — аккаунт идёт по сценарию триала, но приложение ещё ни разу не открывалось: триал не расходуется (одно «Старт» в боте — не старт);
  trial        — пробные дни: три календарных дня по местному времени, считая день первого открытия; в 00:00 после третьего дня основные действия закрываются;
  gift         — подарочные/тестовые дни от админа поверх триала (access_until позже конца триала);
  grace        — дополнительное окно 24 часа после бесплатного периода: новые действия закрыты, история видна, можно оплатить и сохранить серию
                 (db/trial_protection.py). Это не ещё один бесплатный день; срок — фактические 24 часа, а не «до ближайшей полуночи»;
  expired      — доступ закрыт, пока человек не оплатит (история и данные остаются);
  paid         — действующая подписка (в том числе после ранней покупки: оставшееся бесплатное время добавляется к платному сроку).

Триал стартует при ПЕРВОМ УСПЕШНОМ открытии приложения (`ensure_trial_started` из /api/bootstrap); срок записывается один раз (users.trial_ends_at, пояс —
users.trial_tz) и больше не пересчитывается: повторный вход и смена часового пояса новый триал не дают и срок не двигают.
Для каждого состояния отдаём `reason_text` («почему доступ открыт и когда заканчивается») — его показывают приложение, бот и админка.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect
from .users import get_user

UTC = timezone.utc
GRACE_HOURS = 24

STATE_OPEN, STATE_TIMED, STATE_NOT_STARTED, STATE_TRIAL = "open", "timed", "not_started", "trial"
STATE_GIFT, STATE_GRACE, STATE_EXPIRED, STATE_PAID = "gift", "grace", "expired", "paid"
ALLOWED_STATES = (STATE_OPEN, STATE_TIMED, STATE_NOT_STARTED, STATE_TRIAL, STATE_GIFT, STATE_PAID)

_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _parse(value):
    """Строка из базы (UTC) → наивный datetime в UTC."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _iso(moment):
    return moment.isoformat() if moment else None


def _naive_utc(now_utc=None):
    now = now_utc or datetime.now(UTC)
    return now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now


def user_tz_name(user_id):
    from .streak import get_timezone
    try:
        name = get_timezone(user_id)
        ZoneInfo(name)
        return name
    except Exception:
        return "UTC"


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def fmt_local(moment_utc, tz_name):
    """«30 октября, 00:00» — конкретная дата и время по часам человека."""
    if moment_utc is None:
        return ""
    local = moment_utc.replace(tzinfo=UTC).astimezone(_zone(tz_name))
    return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M}"


def compute_trial_end(started_utc, tz_name, days=None):
    """Конец бесплатных дней: местная полночь после последнего (по умолчанию третьего) календарного дня, считая день старта (UTC без tzinfo)."""
    from config import SUBSCRIPTION_TRIAL_DAYS
    zone = _zone(tz_name)
    first_day = started_utc.replace(tzinfo=UTC).astimezone(zone).date()
    end_local = datetime.combine(first_day + timedelta(days=int(days or SUBSCRIPTION_TRIAL_DAYS)), time.min, tzinfo=zone)
    return end_local.astimezone(UTC).replace(tzinfo=None)


def trial_bounds(user):
    """{started, ends, tz} (UTC без tzinfo) или None, если триал ещё не начинался. Срок берём из записи, а не пересчитываем по нынешнему поясу."""
    if user is None or "trial_started_at" not in user.keys():
        return None
    started = _parse(user["trial_started_at"])
    if started is None:
        return None
    tz_name = (user["trial_tz"] if "trial_tz" in user.keys() else None) or user_tz_name(user["telegram_id"])
    ends = _parse(user["trial_ends_at"]) if "trial_ends_at" in user.keys() else None
    if ends is None:
        ends = compute_trial_end(started, tz_name)         # аккаунт, запущенный до ТЗ: срок считаем один раз так же, как раньше
    return {"started": started, "ends": ends, "tz": tz_name}


def start_trial(user_id, now_utc=None, force=False):
    """Назначить триал: старт сейчас, срок — три календарных дня по местному поясу. Уже начатый триал не перезапускается (force — только админка)."""
    user = get_user(user_id)
    if user is None:
        return None
    if not force and "trial_started_at" in user.keys() and user["trial_started_at"]:
        return trial_bounds(user)
    started = _naive_utc(now_utc)
    tz_name = user_tz_name(user_id)
    ends = compute_trial_end(started, tz_name)
    conn = connect()
    try:
        conn.execute("UPDATE users SET trial_started_at=?, trial_ends_at=?, trial_tz=? WHERE telegram_id=?", (_iso(started), _iso(ends), tz_name, user_id))
        conn.commit()
    finally:
        conn.close()
    return {"started": started, "ends": ends, "tz": tz_name}


def set_trial_start(user_id, started_utc):
    """Назначить триал с указанного момента (для проверки и «машины времени»): записываются старт, срок и пояс."""
    started = started_utc.astimezone(UTC).replace(tzinfo=None) if getattr(started_utc, "tzinfo", None) else started_utc
    tz_name = user_tz_name(user_id)
    ends = compute_trial_end(started, tz_name)
    conn = connect()
    try:
        conn.execute("UPDATE users SET trial_started_at=?, trial_ends_at=?, trial_tz=? WHERE telegram_id=?", (_iso(started), _iso(ends), tz_name, user_id))
        conn.commit()
    finally:
        conn.close()
    return {"started": started, "ends": ends, "tz": tz_name}


def reset_trial(user_id):
    """Для проверки: вернуть аккаунт в «триал ещё не начинался»."""
    conn = connect()
    try:
        conn.execute("UPDATE users SET trial_started_at=NULL, trial_ends_at=NULL, trial_tz=NULL WHERE telegram_id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def ensure_trial_started(user_id, now_utc=None):
    """Первое успешное открытие приложения: аккаунту в сценарии триала назначаем срок. True — триал начался именно сейчас."""
    from .access import trial_scenario_for_user
    user = get_user(user_id)
    if user is None or not trial_scenario_for_user(user):
        return False
    if "trial_started_at" in user.keys() and user["trial_started_at"]:
        return False
    if start_trial(user_id, now_utc) is None:
        return False
    try:
        from .tracking import track_server
        track_server(user_id, "trial_started", meta={"tz": user_tz_name(user_id)})
    except Exception:                       # учёт не должен мешать открыть приложение
        pass
    return True


def _grace_end(free_end):
    return free_end + timedelta(hours=GRACE_HOURS)


def local_day_number(started_utc, tz_name, now_utc):
    """1 — день первого открытия, 2 — следующий и так далее; по календарю пояса, в котором назначен триал."""
    zone = _zone(tz_name)
    first = started_utc.replace(tzinfo=UTC).astimezone(zone).date()
    today = now_utc.replace(tzinfo=UTC).astimezone(zone).date()
    return max(1, (today - first).days + 1)


def reason_text(info):
    """Одной фразой: почему доступ открыт или закрыт и когда это меняется (по часам человека)."""
    tz = info["tz"]
    state = info["state"]
    if state == STATE_OPEN:
        return "Свободный доступ"
    if state == STATE_TIMED:
        return f"Временный доступ до {fmt_local(_parse(info['ends_at']), tz)}"
    if state == STATE_NOT_STARTED:
        return "Пробный период начнётся при первом открытии приложения"
    if state == STATE_TRIAL:
        return f"Пробный доступ до {fmt_local(_parse(info['ends_at']), tz)}"
    if state == STATE_GIFT:
        return f"Подарочные дни до {fmt_local(_parse(info['ends_at']), tz)}"
    if state == STATE_GRACE:
        return f"Пробный доступ завершён. До {fmt_local(_parse(info['ends_at']), tz)} можно продолжить и сохранить серию"
    if state == STATE_PAID:
        return f"Подписка до {fmt_local(_parse(info['ends_at']), tz)}"
    return "Доступ закрыт: подписка снова откроет привычки, план и Адама"


def access_state(user_id, now_utc=None):
    """Сводка состояния доступа человека (см. описание модуля). None — человека нет."""
    from config import SUBSCRIPTION_TRIAL_DAYS
    from .access import MODE_TIMED, _row_mode, free_period_active, trial_scenario_for_user
    user = get_user(user_id)
    if user is None:
        return None
    now = _naive_utc(now_utc)
    mode = _row_mode(user)
    scenario = trial_scenario_for_user(user)
    paid_until = _parse(user["subscription_paid_until"]) if "subscription_paid_until" in user.keys() else None
    access_until = _parse(user["access_until"]) if "access_until" in user.keys() else None
    bounds = trial_bounds(user) if scenario else None
    tz = bounds["tz"] if bounds else user_tz_name(user_id)
    trial_ends = bounds["ends"] if bounds else None
    free_end = max([x for x in (trial_ends, access_until) if x is not None], default=None)
    grace_end = _grace_end(free_end) if (scenario and bounds and free_end) else None
    day = local_day_number(bounds["started"], tz, now) if bounds else None

    ends_at = None
    if paid_until and paid_until > now:
        state, ends_at = STATE_PAID, paid_until
    elif mode == MODE_TIMED and not scenario:
        active = free_period_active(user)
        state, ends_at = (STATE_TIMED, access_until) if active else (STATE_EXPIRED, None)
    elif not scenario:
        state = STATE_OPEN
    elif bounds is None:
        state = STATE_NOT_STARTED
    elif now < trial_ends:
        state, ends_at = STATE_TRIAL, trial_ends
    elif access_until and now < access_until:
        state, ends_at = STATE_GIFT, access_until
    elif now < grace_end:
        state, ends_at = STATE_GRACE, grace_end
    else:
        state = STATE_EXPIRED

    phase = {STATE_PAID: "paid", STATE_NOT_STARTED: "not_started", STATE_GIFT: "gift", STATE_GRACE: "grace", STATE_EXPIRED: "expired"}.get(state)
    if state == STATE_TRIAL:
        phase = f"day{min(day, int(SUBSCRIPTION_TRIAL_DAYS))}"
    info = {
        "state": state, "phase": phase, "mode": mode, "scenario": scenario, "tz": tz,
        "allowed": state in ALLOWED_STATES, "read_only": state == STATE_GRACE,
        "day": day, "days_total": int(SUBSCRIPTION_TRIAL_DAYS),
        "started_at": _iso(bounds["started"]) if bounds else None,
        "trial_ends_at": _iso(trial_ends), "free_end": _iso(free_end), "grace_ends_at": _iso(grace_end),
        "paid_until": _iso(paid_until if paid_until and paid_until > now else None), "ends_at": _iso(ends_at),
    }
    info["reason_text"] = reason_text(info)
    info["ends_at_text"] = fmt_local(ends_at, tz) if ends_at else ""
    return info
