"""
Статистика триала для админки (ТЗ «Триал, подписка и приглашения» 28.10.2026, §26–§27): путь людей от входа до оплаты и после неё, дни 2/3/4, ранние покупки, возвраты после окончания,
«отправлено / показано / нажато» как разные события (слова «прочитано» нет — Telegram этого не отдаёт), время активности, функции, ошибки, расход ИИ, нагрузка уведомлениями и
сравнение источников с переходом к людям. Переписки не читаем и не собираем: только счётчики и события.

Группы людей не смешиваются: «реальные» — те, к кому сценарий применяется общим гейтом подписки (после включения гейта), «проверка» — аккаунты в режиме «trial» из админки,
админы и тестовые оплаты. Тестовые и подарочные дни в реальные показатели не попадают (scope = real | test | all).
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from .core import connect

UTC = timezone.utc
FMT = "%Y-%m-%d %H:%M:%S"
SCOPES = ("real", "test", "all")
SUSPICIOUS_WINDOW_MIN = 10
SUSPICIOUS_COUNT = 5            # столько регистраций с одного источника за десять минут — повод проверить (это не ярлык «бот»)


def _parse(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], FMT)
    except ValueError:
        return None


def _is_real(user):
    """К человеку сценарий триала применяется общим гейтом (а не вручную из админки), и это не админ."""
    from config import ADMIN_IDS, SUBSCRIPTION_GATE_ENABLED, SUBSCRIPTION_GATE_CUTOVER
    if int(user["telegram_id"]) in {int(i) for i in ADMIN_IDS}:
        return False
    if not SUBSCRIPTION_GATE_ENABLED:
        return False
    from .subscription import registered_after_cutover
    return registered_after_cutover(user["created_at"], SUBSCRIPTION_GATE_CUTOVER)


def cohort(days=30, source=None, scope="real", now=None):
    """Люди, у которых триал начался за последние `days` дней: список строк users с полем group ('real' | 'test')."""
    if scope not in SCOPES:
        scope = "real"
    now = now or datetime.now(UTC).replace(tzinfo=None)
    since = (now - timedelta(days=int(days))).strftime(FMT)
    conn = connect()
    try:
        query = "SELECT * FROM users WHERE trial_started_at IS NOT NULL AND trial_started_at >= ?"
        params = [since]
        if source:
            query += " AND source_code = ?"
            params.append(source)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        group = "real" if _is_real(row) else "test"
        if scope == "all" or scope == group:
            result.append({"row": row, "group": group})
    return result


def _in_clause(ids):
    return ",".join("?" for _ in ids)


def suspicious_registrations(source_code=None, days=30):
    """Регистрации «подозрительные для проверки»: много аккаунтов с одного источника за считанные минуты (db/traffic.py::_burst_share считает долю, здесь — какие именно).
    Возвращает множество telegram_id. Никаких ярлыков про людей: только повод посмотреть."""
    since = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=int(days))).strftime(FMT)
    conn = connect()
    try:
        query = "SELECT telegram_id, source_code, created_at FROM users WHERE source_code IS NOT NULL AND source_code != '' AND created_at >= ?"
        params = [since]
        if source_code:
            query += " AND source_code = ?"
            params.append(source_code)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    by_source = {}
    for row in rows:
        moment = _parse(row["created_at"])
        if moment:
            by_source.setdefault(row["source_code"], []).append((moment, row["telegram_id"]))
    flagged = set()
    for items in by_source.values():
        items.sort()
        left = 0
        for right in range(len(items)):
            while items[right][0] - items[left][0] > timedelta(minutes=SUSPICIOUS_WINDOW_MIN):
                left += 1
            if right - left + 1 >= SUSPICIOUS_COUNT:
                flagged.update(uid for _, uid in items[left:right + 1])
    return flagged


def overview(days=30, source=None, scope="real", now=None):
    """Сводка раздела «Триал»: путь, дни 2/3/4, покупки, возвраты, сообщения, активность, функции, ошибки, расход ИИ и нагрузка уведомлениями."""
    from .access_state import local_day_number, trial_bounds
    now = now or datetime.now(UTC).replace(tzinfo=None)
    people = cohort(days, source, scope, now)
    ids = [int(p["row"]["telegram_id"]) for p in people]
    result = {"scope": scope if scope in SCOPES else "real", "days": int(days), "source": source, "started": len(ids), "groups": dict(Counter(p["group"] for p in people))}
    empty = {"day2": 0, "day3": 0, "day4": 0, "early_purchases": 0, "window_purchases": 0, "late_purchases": 0, "paid": 0, "returned_after_window": 0,
             "messages": [], "offers": [], "hours": [0] * 24, "functions": [], "client_errors": 0, "payment_failures": 0, "ai_tokens": 0, "ai_per_user": 0,
             "notifications": {"total": 0, "per_user": 0, "max_per_user": 0, "by_kind": []}, "suspicious": 0, "by_source": []}
    if not ids:
        return {**result, **empty}
    marks = _in_clause(ids)
    since = (now - timedelta(days=int(days))).strftime(FMT)
    conn = connect()
    try:
        opens = conn.execute(f"SELECT user_id, at FROM user_events WHERE name='session_start' AND user_id IN ({marks}) AND at>=?", (*ids, since)).fetchall()
        payments = conn.execute(
            f"SELECT user_id, COALESCE(purchased_at, created_at) AS at FROM stars_payments WHERE user_id IN ({marks}) AND COALESCE(is_test,0)=0 AND refunded_at IS NULL "
            "AND COALESCE(status,'done')='done' ORDER BY COALESCE(purchased_at, created_at)", ids).fetchall()
        failures = conn.execute(f"SELECT COUNT(*) AS n FROM stars_payments WHERE user_id IN ({marks}) AND status='failed'", ids).fetchone()["n"]
        errors = conn.execute(f"SELECT COUNT(*) AS n FROM client_errors WHERE user_id IN ({marks}) AND created_at>=?", (*ids, since)).fetchone()["n"]
        events = conn.execute(
            f"SELECT name, COUNT(*) AS n FROM user_events WHERE user_id IN ({marks}) AND at>=? AND name NOT IN ('session_start') AND name NOT LIKE 'trial_%' AND name NOT LIKE 'sub_%' "
            "GROUP BY name ORDER BY n DESC LIMIT 12", (*ids, since)).fetchall()
        sent = conn.execute(
            f"SELECT step AS key, COUNT(*) AS n FROM user_events WHERE name='trial_msg_sent' AND user_id IN ({marks}) AND at>=? GROUP BY step", (*ids, since)).fetchall()
        opened = conn.execute(
            f"SELECT step AS key, COUNT(*) AS n FROM user_events WHERE name='trial_msg_open' AND user_id IN ({marks}) AND at>=? GROUP BY step", (*ids, since)).fetchall()
        offers = conn.execute(
            f"SELECT place, key, event, COUNT(*) AS n FROM trial_offers WHERE user_id IN ({marks}) AND created_at>=? GROUP BY place, key, event", (*ids, since)).fetchall()
        tokens = conn.execute(f"SELECT COALESCE(SUM(tokens),0) AS n FROM ai_token_log WHERE user_id IN ({marks}) AND day>=?", (*ids, since[:10])).fetchone()["n"]
        notes = conn.execute(
            f"SELECT user_id, category, COUNT(*) AS n FROM notification_log WHERE user_id IN ({marks}) AND sent_at>=? GROUP BY user_id, category", (*ids, since)).fetchall()
    finally:
        conn.close()

    bounds = {p["row"]["telegram_id"]: trial_bounds(p["row"]) for p in people}
    day_users = {2: set(), 3: set(), 4: set()}
    returned, hours = set(), [0] * 24
    for event in opens:
        info = bounds.get(event["user_id"])
        moment = _parse(event["at"])
        if not info or not moment:
            continue
        number = local_day_number(info["started"], info["tz"], moment)
        if number in day_users:
            day_users[number].add(event["user_id"])
        grace_end = info["ends"] + timedelta(hours=24)
        if moment >= grace_end:
            returned.add(event["user_id"])
        hours[(moment + _offset(info["tz"], moment)).hour] += 1
    early = window = late = 0
    paid = set()
    for payment in payments:
        info = bounds.get(payment["user_id"])
        moment = _parse(payment["at"])
        if payment["user_id"] in paid or not info or not moment:
            continue
        paid.add(payment["user_id"])
        if moment < info["ends"]:
            early += 1
        elif moment < info["ends"] + timedelta(hours=24):
            window += 1
        else:
            late += 1

    sent_map, open_map = {r["key"]: r["n"] for r in sent}, {r["key"]: r["n"] for r in opened}
    messages = [{"key": key, "sent": sent_map.get(key, 0), "opened": open_map.get(key, 0)} for key in sorted(set(sent_map) | set(open_map))]
    offer_map = {}
    for row in offers:
        slot = offer_map.setdefault((row["place"], row["key"]), {"place": row["place"], "key": row["key"], "shown": 0, "sent": 0, "clicked": 0, "dismissed": 0})
        slot[row["event"]] = slot.get(row["event"], 0) + row["n"]
    per_user, kinds = Counter(), Counter()
    for row in notes:
        per_user[row["user_id"]] += row["n"]
        kinds[row["category"]] += row["n"]
    total_notes = sum(per_user.values())
    suspicious = suspicious_registrations(source, days) & set(ids)
    by_source = {}
    for person in people:
        slot = by_source.setdefault(person["row"]["source_code"] or "", {"source": person["row"]["source_code"] or "", "users": 0, "paid": 0})
        slot["users"] += 1
        slot["paid"] += 1 if int(person["row"]["telegram_id"]) in paid else 0
    return {**result, "day2": len(day_users[2]), "day3": len(day_users[3]), "day4": len(day_users[4]), "early_purchases": early, "window_purchases": window, "late_purchases": late,
            "paid": len(paid), "returned_after_window": len(returned), "messages": messages, "offers": sorted(offer_map.values(), key=lambda s: (s["place"], s["key"])), "hours": hours,
            "functions": [{"name": r["name"], "count": r["n"]} for r in events], "client_errors": errors, "payment_failures": failures, "ai_tokens": int(tokens),
            "ai_per_user": round(int(tokens) / len(ids)), "notifications": {"total": total_notes, "per_user": round(total_notes / len(ids), 1), "max_per_user": max(per_user.values(), default=0),
                                                                              "by_kind": [{"kind": k, "count": n} for k, n in kinds.most_common(10)]},
            "suspicious": len(suspicious), "by_source": sorted(by_source.values(), key=lambda s: -s["users"])}


def _offset(tz_name, moment_utc):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(tz_name).utcoffset(moment_utc.replace(tzinfo=UTC)) or timedelta(0)
    except Exception:
        return timedelta(0)


def source_people(source, days=30, limit=100):
    """Переход к людям источника (§27): ник/id, путь по воронке и отметка «подозрительная регистрация» для проверки."""
    people = cohort(days, source, "all")
    flagged = suspicious_registrations(source, days)
    ids = [int(p["row"]["telegram_id"]) for p in people][:limit]
    if not ids:
        return []
    marks = _in_clause(ids)
    conn = connect()
    try:
        active = {r["user_id"] for r in conn.execute(f"SELECT DISTINCT user_id FROM habit_completion_events WHERE user_id IN ({marks})", ids).fetchall()}
        paid = {r["user_id"] for r in conn.execute(f"SELECT DISTINCT user_id FROM stars_payments WHERE user_id IN ({marks}) AND COALESCE(is_test,0)=0 AND refunded_at IS NULL", ids).fetchall()}
    finally:
        conn.close()
    return [{"user_id": p["row"]["telegram_id"], "name": p["row"]["first_name"], "username": p["row"]["username"], "group": p["group"],
             "started_at": p["row"]["trial_started_at"], "useful_action": int(p["row"]["telegram_id"]) in active, "paid": int(p["row"]["telegram_id"]) in paid,
             "suspicious": int(p["row"]["telegram_id"]) in flagged} for p in people if int(p["row"]["telegram_id"]) in ids]
