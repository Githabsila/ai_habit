"""
Аналитика продукта для страницы /analytics: обзор, удержание, функции, экраны и кнопки, версии, здоровье, ритм, достоверность, сигналы.

Принципы, чтобы цифрам можно было верить:
  • «Люди» — только настоящие: без админов, забаненных и аккаунтов, помеченных «не учитывать в статистике» (users.stats_excluded).
    Это одно условие на всё (real_where), поэтому воронка, удержание, функции и каналы считаются на одном и том же наборе людей.
  • Дни — по UTC (для России сутки начинаются в 03:00 — на ночном затишье, поэтому разрез почти не искажает вечерние пики).
  • День активности человека — запись в daily_active (любое обращение к приложению или боту). Удержание D-N считается по календарным
    дням от даты регистрации; когорта, у которой день N ещё не закончился, в расчёт не входит и помечена как «не созрела».
  • Если данных мало, мы не рисуем процент: возвращаем None и размер выборки, а страница показывает «мало данных».
  • Что именно относится к функции, решает карта функций (webapp/feature_map.py); неописанное показывается отдельным списком.

Любая функция только читает. Ошибка в аналитике не должна ломать продукт — но и молча врать она не должна: сбои поднимаются наверх.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .core import connect
from . import tracking
from webapp import feature_map as fm

MARKS = (1, 3, 7, 14, 30)
MIN_SAMPLE = 8            # меньше людей — процент не показываем
NEW_FEATURE_DAYS = 30
NEW_BUTTON_DAYS = 14
STATIC_DIR = Path(__file__).resolve().parent.parent / "webapp" / "static"


# ---------------------------------------------------------------------------------------------------------------- общее

def _today():
    return datetime.now(timezone.utc).date()


def _clamp(value, low, high, default):
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


def real_where(alias="u"):
    """Условие «настоящий человек» для запроса, где users имеет псевдоним alias."""
    sql = f"COALESCE({alias}.stats_excluded,0)=0 AND COALESCE({alias}.banned,0)=0"
    ids = tracking._admin_ids()
    if ids:
        sql += f" AND {alias}.telegram_id NOT IN ({','.join(str(int(i)) for i in ids)})"
    return sql


def _source_where(source, alias="u"):
    if source == "none":
        return f" AND ({alias}.source_code IS NULL OR {alias}.source_code='')", []
    if source:
        return f" AND {alias}.source_code=?", [source]
    return "", []


def _pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def _enough(part, whole):
    """Процент и размер выборки: pct=None, если людей слишком мало, чтобы делать выводы."""
    return {"pct": _pct(part, whole) if whole >= MIN_SAMPLE else None, "n": part, "of": whole}


def _feature_name(feature_id):
    feature = fm.feature(feature_id) if feature_id else None
    return feature.name if feature else None


def _day_list(since, until):
    out, day = [], since
    while day <= until:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _parse_day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------- обзор

def overview(days=30, source=None):
    days = _clamp(days, 1, 180, 30)
    today = _today()
    since = today - timedelta(days=days - 1)
    real = real_where()
    src, src_params = _source_where(source)
    conn = connect()
    try:
        users_total = conn.execute(f"SELECT COUNT(*) FROM users u WHERE {real}{src}", src_params).fetchone()[0]
        new_rows = {r[0]: r[1] for r in conn.execute(
            f"SELECT date(u.created_at) d, COUNT(*) FROM users u WHERE {real}{src} AND date(u.created_at)>=? GROUP BY d",
            src_params + [since.isoformat()]).fetchall()}
        dau_rows = {r[0]: r[1] for r in conn.execute(
            f"SELECT a.day, COUNT(*) FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real}{src} AND a.day>=? GROUP BY a.day",
            src_params + [(today - timedelta(days=max(days, 31))).isoformat()]).fetchall()}
        sess_rows = {r[0]: (r[1], r[2] or 0) for r in conn.execute(
            f"SELECT date(s.started_at) d, COUNT(*), SUM(s.active_seconds) FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id "
            f"WHERE {real}{src} AND date(s.started_at)>=? GROUP BY d", src_params + [since.isoformat()]).fetchall()}

        def distinct_active(window):
            return conn.execute(
                f"SELECT COUNT(DISTINCT a.user_id) FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real}{src} AND a.day>=?",
                src_params + [(today - timedelta(days=window - 1)).isoformat()]).fetchone()[0]

        wau, mau, active_period = distinct_active(7), distinct_active(30), distinct_active(days)
        cohort_cut = (today - timedelta(days=1)).isoformat()      # «созревшие» хотя бы на сутки — иначе у вчерашних не было времени
        mature = conn.execute(
            f"SELECT COUNT(*) FROM users u WHERE {real}{src} AND date(u.created_at)>=? AND date(u.created_at)<=?",
            src_params + [since.isoformat(), cohort_cut]).fetchone()[0]
        habit = conn.execute(
            f"SELECT COUNT(*) FROM users u WHERE {real}{src} AND date(u.created_at)>=? AND date(u.created_at)<=? "
            "AND EXISTS(SELECT 1 FROM habits h WHERE h.user_id=u.telegram_id)", src_params + [since.isoformat(), cohort_cut]).fetchone()[0]
        asked_ai = conn.execute(
            f"SELECT COUNT(*) FROM users u WHERE {real}{src} AND date(u.created_at)>=? AND date(u.created_at)<=? "
            "AND EXISTS(SELECT 1 FROM ai_messages m WHERE m.user_id=u.telegram_id AND m.role='user')",
            src_params + [since.isoformat(), cohort_cut]).fetchone()[0]
        paid = conn.execute(
            f"SELECT COUNT(DISTINCT p.user_id), COALESCE(SUM(p.amount),0) FROM stars_payments p JOIN users u ON u.telegram_id=p.user_id "
            f"WHERE {real}{src} AND date(p.created_at)>=? AND COALESCE(p.is_test,0)=0", src_params + [since.isoformat()]).fetchone()
        first_session = conn.execute("SELECT MIN(started_at) FROM app_sessions").fetchone()[0]
    finally:
        conn.close()

    day_keys = _day_list(since, today)
    series = {
        "days": day_keys,
        "new_users": [new_rows.get(d, 0) for d in day_keys],
        "dau": [dau_rows.get(d, 0) for d in day_keys],
        "sessions": [sess_rows.get(d, (0, 0))[0] for d in day_keys],
        "active_minutes": [round(sess_rows.get(d, (0, 0))[1] / 60, 1) for d in day_keys],
    }
    sessions_total = sum(v[0] for v in sess_rows.values())
    seconds_total = sum(v[1] for v in sess_rows.values())
    last7 = [dau_rows.get((today - timedelta(days=i)).isoformat(), 0) for i in range(7)]
    avg_dau7 = sum(last7) / 7
    return {
        "days": days, "source": source, "since": since.isoformat(), "today": today.isoformat(),
        "tiles": {
            "users_total": users_total,
            "new_users": sum(new_rows.values()),
            "dau_today": dau_rows.get(today.isoformat(), 0),
            "dau_yesterday": dau_rows.get((today - timedelta(days=1)).isoformat(), 0),
            "wau": wau, "mau": mau, "active_period": active_period,
            "stickiness": _pct(avg_dau7, mau) if mau >= MIN_SAMPLE else None,
            "sessions": sessions_total,
            "avg_session_seconds": int(seconds_total / sessions_total) if sessions_total else None,
            "sessions_per_active_user": round(sessions_total / active_period, 1) if active_period else None,
            "activation_habit": _enough(habit, mature),
            "activation_ai": _enough(asked_ai, mature),
            "payers": paid[0], "revenue_stars": int(paid[1]),
        },
        "series": series,
        "tracking_since": first_session,
    }


# ---------------------------------------------------------------------------------------------------------------- удержание

def retention(source=None, days=60):
    """Удержание D1/D3/D7/D14/D30 по неделям регистрации и общая кривая. День N считается «пройденным», если он уже закончился."""
    days = _clamp(days, 7, 365, 60)
    today = _today()
    since = today - timedelta(days=days)
    real = real_where()
    src, src_params = _source_where(source)
    conn = connect()
    try:
        users = {r[0]: _parse_day(r[1]) for r in conn.execute(
            f"SELECT u.telegram_id, date(u.created_at) FROM users u WHERE {real}{src} AND date(u.created_at)>=?",
            src_params + [since.isoformat()]).fetchall()}
        active = defaultdict(set)
        for uid, day in conn.execute(
                f"SELECT a.user_id, a.day FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real}{src} AND date(u.created_at)>=? "
                "AND a.day>=date(u.created_at)", src_params + [since.isoformat()]).fetchall():
            active[uid].add(day)
    finally:
        conn.close()

    def stat(group, mark):
        eligible = [(uid, reg) for uid, reg in group if reg and reg + timedelta(days=mark) < today]
        back = sum(1 for uid, reg in eligible if (reg + timedelta(days=mark)).isoformat() in active[uid])
        return _enough(back, len(eligible))

    groups = defaultdict(list)
    for uid, reg in users.items():
        if reg:
            groups[reg - timedelta(days=reg.weekday())].append((uid, reg))
    cohorts = []
    for start in sorted(groups, reverse=True):
        group = groups[start]
        end = start + timedelta(days=6)
        row = {"start": start.isoformat(), "label": f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}", "n": len(group)}
        for mark in MARKS:
            row[f"d{mark}"] = stat(group, mark)
        cohorts.append(row)
    everyone = [(uid, reg) for uid, reg in users.items() if reg]
    overall = {"n": len(everyone)}
    for mark in MARKS:
        overall[f"d{mark}"] = stat(everyone, mark)
    curve = []
    for mark in range(1, min(30, days) + 1):
        point = stat(everyone, mark)
        if point["of"]:
            curve.append({"day": mark, "pct": point["pct"], "of": point["of"]})
    return {"days": days, "source": source, "marks": list(MARKS), "overall": overall, "cohorts": cohorts, "curve": curve}


# ---------------------------------------------------------------------------------------------------------------- функции

def _usage_rows(conn, since, until=None):
    """Использование за период (настоящие люди): (feature_id|None, kind, key, screen, day, user_id, count). Только то, что дал человек."""
    real = real_where()
    until_sql, until_params = (" AND {col}<=?", [until]) if until else ("", [])
    rows = []
    for r in conn.execute(
            f"SELECT c.day, c.user_id, c.label, c.screen, c.clicks FROM ui_clicks c JOIN users u ON u.telegram_id=c.user_id "
            f"WHERE {real} AND c.day>=?{until_sql.format(col='c.day')}", [since] + until_params).fetchall():
        rows.append((fm.feature_for_click(r["label"], r["screen"]), "click", r["label"], r["screen"], r["day"], r["user_id"], r["clicks"]))
    for r in conn.execute(
            f"SELECT s.day, s.user_id, s.screen, s.views, s.seconds FROM ui_screens s JOIN users u ON u.telegram_id=s.user_id "
            f"WHERE {real} AND s.day>=?{until_sql.format(col='s.day')}", [since] + until_params).fetchall():
        rows.append((fm.feature_for_screen(r["screen"]), "screen", r["screen"], r["screen"], r["day"], r["user_id"], r["views"] or 1))
    for r in conn.execute(
            f"SELECT date(e.at) day, e.user_id, e.name, COUNT(*) n FROM user_events e JOIN users u ON u.telegram_id=e.user_id "
            f"WHERE {real} AND date(e.at)>=?{until_sql.format(col='date(e.at)')} GROUP BY day, e.user_id, e.name", [since] + until_params).fetchall():
        rows.append((fm.feature_for_event(r["name"]), "event", r["name"], "", r["day"], r["user_id"], r["n"]))
    for r in conn.execute(
            f"SELECT a.day, a.user_id, a.route, a.calls FROM api_usage a JOIN users u ON u.telegram_id=a.user_id "
            f"WHERE {real} AND a.user_id>0 AND a.day>=?{until_sql.format(col='a.day')}", [since] + until_params).fetchall():
        rows.append((fm.feature_for_api(r["route"]), "api", r["route"], "", r["day"], r["user_id"], r["calls"]))
    return rows


def _active_users(conn, since, until=None):
    real = real_where()
    sql = f"SELECT COUNT(DISTINCT a.user_id) FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real} AND a.day>=?"
    params = [since]
    if until:
        sql += " AND a.day<=?"
        params.append(until)
    return conn.execute(sql, params).fetchone()[0]


def features(days=14):
    """Использование функций: сколько людей, как часто возвращаются, динамика и влияние на удержание. Источник — карта функций."""
    days = _clamp(days, 1, 90, 14)
    today = _today()
    start = today - timedelta(days=days - 1)
    prev_start = start - timedelta(days=days)
    impact_start = today - timedelta(days=60)
    window_start = min(prev_start, impact_start)
    conn = connect()
    try:
        rows = _usage_rows(conn, window_start.isoformat())
        active_now = _active_users(conn, start.isoformat())
        active_prev = _active_users(conn, prev_start.isoformat(), (start - timedelta(days=1)).isoformat())
        regs = {r[0]: _parse_day(r[1]) for r in conn.execute(
            f"SELECT u.telegram_id, date(u.created_at) FROM users u WHERE {real_where()} AND date(u.created_at)>=?", (impact_start.isoformat(),)).fetchall()}
        returns = defaultdict(set)
        for uid, day in conn.execute(
                f"SELECT a.user_id, a.day FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real_where()} AND date(u.created_at)>=? "
                "AND a.day>=date(u.created_at)", (impact_start.isoformat(),)).fetchall():
            returns[uid].add(day)
    finally:
        conn.close()

    start_s, prev_s = start.isoformat(), prev_start.isoformat()
    per = defaultdict(lambda: {"days": defaultdict(set), "uses": 0, "prev": set(), "buttons": defaultdict(lambda: [0, set()]), "first": None,
                               "all_days": defaultdict(set)})
    for fid, kind, key, screen, day, uid, count in rows:
        if not fid:
            continue
        slot = per[fid]
        slot["all_days"][uid].add(day)
        if slot["first"] is None or day < slot["first"]:
            slot["first"] = day
        if day >= start_s:
            slot["days"][uid].add(day)
            slot["uses"] += count if kind != "screen" else 0
            if kind == "click":
                slot["buttons"][key][0] += count
                slot["buttons"][key][1].add(uid)
        elif day >= prev_s:
            slot["prev"].add(uid)

    out = []
    for f in fm.FEATURES:
        if f.hidden:
            continue
        slot = per.get(f.id)
        users = slot["days"] if slot else {}
        n = len(users)
        repeat = sum(1 for d in users.values() if len(d) >= 2)
        per_day = defaultdict(set)
        if slot:
            for uid, ds in users.items():
                for d in ds:
                    per_day[d].add(uid)
        age = (today - _parse_day(f.since)).days if _parse_day(f.since) else None
        first = slot["first"] if slot else None
        is_new = (age is not None and age <= NEW_FEATURE_DAYS) or (first is not None and (today - _parse_day(first)).days <= NEW_FEATURE_DAYS and age is None)
        tried, not_tried = [], []
        if slot:
            for uid, reg in regs.items():
                if not reg or reg + timedelta(days=7) >= today:
                    continue
                used_early = any(d <= (reg + timedelta(days=2)).isoformat() for d in slot["all_days"].get(uid, ()))
                came_back = (reg + timedelta(days=7)).isoformat() in returns[uid]
                (tried if used_early else not_tried).append(came_back)
        impact = None
        if len(tried) >= MIN_SAMPLE and len(not_tried) >= MIN_SAMPLE:
            impact = {"tried": {"n": len(tried), "d7": _pct(sum(tried), len(tried))}, "not_tried": {"n": len(not_tried), "d7": _pct(sum(not_tried), len(not_tried))}}
        buttons = sorted(({"label": k, "title": fm.click_label(k), "clicks": v[0], "users": len(v[1])} for k, v in (slot["buttons"].items() if slot else [])),
                         key=lambda x: -x["clicks"])[:6]
        out.append({
            "id": f.id, "name": f.name, "group": f.group, "group_name": fm.GROUPS[f.group], "since": f.since, "age_days": age, "new": bool(is_new),
            "note": f.note, "users": n, "uses": slot["uses"] if slot else 0,
            "adoption": _enough(n, active_now), "repeat": _enough(repeat, n),
            "prev_users": len(slot["prev"]) if slot else 0, "prev_active": active_prev,
            "series": [len(per_day.get(d, ())) for d in _day_list(start, today)],
            "buttons": buttons, "first_seen": first, "impact": impact,
        })
    unmapped = _unmapped(days)
    return {"days": days, "since": start_s, "active_users": active_now, "active_users_prev": active_prev, "features": out,
            "groups": [{"id": k, "name": v} for k, v in fm.GROUPS.items()], "unmapped": unmapped["items"], "unmapped_events": unmapped["events"]}


def _unmapped(days):
    """Всё, что люди нажимали, открывали и вызывали, но чего нет в карте функций: страж «ничего не потеряно»."""
    start = (_today() - timedelta(days=days - 1)).isoformat()
    real = real_where()
    conn = connect()
    try:
        catalog = conn.execute("SELECT * FROM ui_catalog WHERE kind IN ('click','screen','api')").fetchall()
        clicks = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
            f"SELECT c.label, c.screen, SUM(c.clicks), COUNT(DISTINCT c.user_id) FROM ui_clicks c JOIN users u ON u.telegram_id=c.user_id "
            f"WHERE {real} AND c.day>=? GROUP BY c.label, c.screen", (start,)).fetchall()}
        screens = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT s.screen, SUM(s.views), COUNT(DISTINCT s.user_id) FROM ui_screens s JOIN users u ON u.telegram_id=s.user_id "
            f"WHERE {real} AND s.day>=? GROUP BY s.screen", (start,)).fetchall()}
        apis = {r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT a.route, SUM(a.calls), COUNT(DISTINCT a.user_id) FROM api_usage a JOIN users u ON u.telegram_id=a.user_id "
            f"WHERE {real} AND a.user_id>0 AND a.day>=? GROUP BY a.route", (start,)).fetchall()}
        events = conn.execute(
            f"SELECT e.name, COUNT(*) n, COUNT(DISTINCT e.user_id) u, MIN(e.at) first FROM user_events e JOIN users u ON u.telegram_id=e.user_id "
            f"WHERE {real} AND date(e.at)>=? GROUP BY e.name", (start,)).fetchall()
    finally:
        conn.close()
    items = []
    for row in catalog:
        kind, key = row["kind"], row["key"]
        feature_id, ignored = fm.classify(kind, key, row["first_screen"])
        if feature_id or ignored:
            continue
        if kind == "click":
            uses = sum(v[0] for (label, _screen), v in clicks.items() if label == key)
            users = max((v[1] for (label, _screen), v in clicks.items() if label == key), default=0)
        elif kind == "screen":
            uses, users = screens.get(key, (0, 0))
        else:
            uses, users = apis.get(key, (0, 0))
        if not uses:
            continue
        hint = {
            "click": "кнопка на главной или в окне без владельца — добавь шаблон в clicks нужной функции (или окно в screens)",
            "screen": "новое окно — добавь его имя в screens нужной функции",
            "api": "новый маршрут — добавь в api функции (осознанное действие) или в PASSIVE_API (фоновая загрузка)",
        }[kind]
        items.append({"kind": kind, "key": key, "title": fm.click_label(key) if kind == "click" else fm.screen_label(key) if kind == "screen" else key,
                      "screen": row["first_screen"], "first_seen": row["first_seen"], "first_version": row["first_version"],
                      "uses": int(uses), "users": int(users), "hint": hint})
    items.sort(key=lambda x: (-x["uses"], x["key"]))
    unknown_events = [{"name": r["name"], "label": tracking.EVENT_LABELS.get(r["name"], ("", r["name"]))[1], "count": r["n"], "users": r["u"], "first": r["first"]}
                      for r in events if not fm.feature_for_event(r["name"]) and r["name"] not in fm.IGNORED_EVENTS]
    return {"items": items[:60], "events": unknown_events}


# ---------------------------------------------------------------------------------------------------------------- экраны и кнопки

def screens(days=14):
    days = _clamp(days, 1, 90, 14)
    start = (_today() - timedelta(days=days - 1)).isoformat()
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT s.screen, SUM(s.views) v, SUM(s.seconds) sec, COUNT(DISTINCT s.user_id) u FROM ui_screens s JOIN users u ON u.telegram_id=s.user_id "
            f"WHERE {real_where()} AND s.day>=? GROUP BY s.screen ORDER BY sec DESC", (start,)).fetchall()
        first = {r[0]: r[1] for r in conn.execute("SELECT key, first_seen FROM ui_catalog WHERE kind='screen'").fetchall()}
    finally:
        conn.close()
    total = sum(r["sec"] or 0 for r in rows)
    out = [{
        "screen": r["screen"], "title": fm.screen_label(r["screen"]), "feature": _feature_name(fm.feature_for_screen(r["screen"])),
        "views": int(r["v"] or 0), "users": r["u"], "seconds": int(r["sec"] or 0), "avg_seconds_per_view": round((r["sec"] or 0) / r["v"], 1) if r["v"] else None,
        "share": _pct(r["sec"] or 0, total), "first_seen": first.get(r["screen"]),
    } for r in rows if r["screen"] != "bot"]
    return {"days": days, "since": start, "items": out, "total_seconds": int(total)}


def _declared_buttons():
    """Кнопки с id, нарисованные в index.html: чтобы показать те, на которые никто не нажимал (мёртвые или спрятанные)."""
    try:
        text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        return []
    ids = re.findall(r'<button\b[^>]*\bid="([A-Za-z][\w-]*)"', text)
    return sorted({i for i in ids if "admin" not in i.lower() and not i.lower().startswith("lab")})


def buttons(days=14, screen=None, limit=80):
    days = _clamp(days, 1, 90, 14)
    limit = _clamp(limit, 10, 300, 80)
    today = _today()
    start = (today - timedelta(days=days - 1)).isoformat()
    real = real_where()
    conn = connect()
    try:
        params = [start]
        extra = ""
        if screen:
            extra, params = " AND c.screen=?", [start, screen]
        rows = conn.execute(
            f"SELECT c.label, c.screen, SUM(c.clicks) n, COUNT(DISTINCT c.user_id) u FROM ui_clicks c JOIN users u ON u.telegram_id=c.user_id "
            f"WHERE {real} AND c.day>=?{extra} GROUP BY c.label, c.screen ORDER BY n DESC LIMIT ?", params + [limit]).fetchall()
        first = {r[0]: r[1] for r in conn.execute("SELECT key, first_seen FROM ui_catalog WHERE kind='click'").fetchall()}
        clicked_ever = {r[0] for r in conn.execute("SELECT DISTINCT label FROM ui_clicks WHERE day>=?", (start,)).fetchall()}
    finally:
        conn.close()
    cut = (today - timedelta(days=NEW_BUTTON_DAYS)).isoformat()
    items = []
    for r in rows:
        fid = fm.feature_for_click(r["label"], r["screen"])
        seen = first.get(r["label"])
        items.append({
            "label": r["label"], "title": fm.click_label(r["label"]), "screen": r["screen"], "screen_title": fm.screen_label(r["screen"]) if r["screen"] else "",
            "clicks": int(r["n"]), "users": r["u"], "feature": _feature_name(fid),
            "new": bool(seen and seen[:10] >= cut), "first_seen": seen,
        })
    never = [] if screen else [b for b in _declared_buttons() if b not in clicked_ever]
    return {"days": days, "since": start, "items": items, "never_clicked": [{"label": b, "title": fm.click_label(b)} for b in never]}


# ---------------------------------------------------------------------------------------------------------------- версии

def versions(limit=10):
    """Выпуски приложения: как вели себя люди на каждой версии и как удержались новички, пришедшие на ней."""
    limit = _clamp(limit, 2, 40, 10)
    real = real_where()
    today = _today()
    conn = connect()
    try:
        catalog = conn.execute("SELECT key, first_seen FROM ui_catalog WHERE kind='version' ORDER BY first_seen DESC LIMIT ?", (limit,)).fetchall()
        stats = {r["version"]: r for r in conn.execute(
            f"SELECT s.version, COUNT(*) n, COUNT(DISTINCT s.user_id) u, AVG(s.active_seconds) act, AVG(s.actions) acts, "
            f"SUM(CASE WHEN s.active_seconds<10 THEN 1 ELSE 0 END) bounce "
            f"FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id WHERE {real} AND s.version IS NOT NULL GROUP BY s.version").fetchall()}
        users = [(r[0], r[1]) for r in conn.execute(
            f"SELECT u.telegram_id, u.created_at FROM users u WHERE {real} AND u.created_at>=?",
            ((catalog[-1]["first_seen"] if catalog else "9999"),)).fetchall()]
        active = defaultdict(set)
        if users:
            for uid, day in conn.execute(
                    f"SELECT a.user_id, a.day FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real} AND u.created_at>=? "
                    "AND a.day>=date(u.created_at)", ((catalog[-1]["first_seen"] if catalog else "9999"),)).fetchall():
                active[uid].add(day)
    finally:
        conn.close()
    items = []
    for index, row in enumerate(catalog):
        begins = row["first_seen"]
        ends = catalog[index - 1]["first_seen"] if index > 0 else None
        newcomers = [(uid, _parse_day(created)) for uid, created in users if created >= begins and (ends is None or created < ends)]
        eligible = [(uid, reg) for uid, reg in newcomers if reg and reg + timedelta(days=1) < today]
        back = sum(1 for uid, reg in eligible if (reg + timedelta(days=1)).isoformat() in active[uid])
        st = stats.get(row["key"])
        items.append({
            "version": row["key"], "first_seen": begins, "sessions": st["n"] if st else 0, "users": st["u"] if st else 0,
            "avg_active_seconds": int(st["act"]) if st and st["act"] is not None else None, "avg_actions": round(st["acts"], 1) if st and st["acts"] is not None else None,
            "bounce_pct": _pct(st["bounce"], st["n"]) if st and st["n"] >= MIN_SAMPLE else None,
            "newcomers": len(newcomers), "newcomers_d1": _enough(back, len(eligible)),
        })
    return {"items": items}


# ---------------------------------------------------------------------------------------------------------------- здоровье

def health(days=7):
    days = _clamp(days, 1, 60, 7)
    start = (_today() - timedelta(days=days - 1)).isoformat()
    conn = connect()
    try:
        routes = conn.execute(
            "SELECT route, SUM(calls) calls, SUM(errors) errors, SUM(client_errors) cerr, SUM(total_ms) ms, MAX(max_ms) mx, "
            "COUNT(DISTINCT CASE WHEN user_id>0 THEN user_id END) users FROM api_usage WHERE day>=? GROUP BY route", (start,)).fetchall()
        by_day = conn.execute("SELECT day, SUM(calls) calls, SUM(errors) errors FROM api_usage WHERE day>=? GROUP BY day ORDER BY day", (start,)).fetchall()
        crashes = conn.execute(
            "SELECT substr(message,1,140) m, COUNT(*) n, COUNT(DISTINCT user_id) u, MAX(created_at) last FROM client_errors WHERE date(created_at)>=? "
            "GROUP BY m ORDER BY n DESC LIMIT 8", (start,)).fetchall()
        crash_total = conn.execute("SELECT COUNT(*) FROM client_errors WHERE date(created_at)>=?", (start,)).fetchone()[0]
    finally:
        conn.close()
    items = []
    for r in routes:
        calls = int(r["calls"] or 0)
        items.append({
            "route": r["route"], "feature": _feature_name(fm.feature_for_api(r["route"])),
            "calls": calls, "users": r["users"], "errors": int(r["errors"] or 0), "client_errors": int(r["cerr"] or 0),
            "error_pct": _pct(r["errors"] or 0, calls), "avg_ms": int((r["ms"] or 0) / calls) if calls else None, "max_ms": int(r["mx"] or 0),
        })
    items.sort(key=lambda x: (-x["errors"], -(x["avg_ms"] or 0)))
    total_calls = sum(i["calls"] for i in items)
    total_errors = sum(i["errors"] for i in items)
    slow = sorted((i for i in items if i["calls"] >= 5 and i["avg_ms"] is not None), key=lambda x: -x["avg_ms"])[:6]
    return {
        "days": days, "since": start, "calls": total_calls, "errors": total_errors, "error_pct": _pct(total_errors, total_calls),
        "routes": items[:60], "slowest": slow,
        "series": [{"day": r["day"], "calls": int(r["calls"] or 0), "errors": int(r["errors"] or 0)} for r in by_day],
        "crashes": [{"message": r["m"], "count": r["n"], "users": r["u"], "last": r["last"]} for r in crashes], "crash_total": crash_total,
    }


# ---------------------------------------------------------------------------------------------------------------- ритм и вовлечённость

def rhythm(days=30):
    """Когда люди заходят: неделя × час по местному времени человека (по сдвигу часового пояса из сессии)."""
    days = _clamp(days, 7, 90, 30)
    start = (_today() - timedelta(days=days - 1)).isoformat()
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT s.started_at, s.tz_offset FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id WHERE {real_where()} AND date(s.started_at)>=?",
            (start,)).fetchall()
    finally:
        conn.close()
    grid = [[0] * 24 for _ in range(7)]
    unknown = 0
    for r in rows:
        moment = tracking._parse(r["started_at"])
        if moment is None or r["tz_offset"] is None:
            unknown += 1
            continue
        local = moment + timedelta(minutes=int(r["tz_offset"]))
        grid[local.weekday()][local.hour] += 1
    by_hour = [sum(grid[d][h] for d in range(7)) for h in range(24)]
    return {"days": days, "grid": grid, "by_hour": by_hour, "sessions": len(rows), "unknown_tz": unknown, "peak_hour": by_hour.index(max(by_hour)) if any(by_hour) else None}


def engagement(days=14):
    days = _clamp(days, 1, 90, 14)
    start = (_today() - timedelta(days=days - 1)).isoformat()
    real = real_where()
    conn = connect()
    try:
        sessions = conn.execute(
            f"SELECT s.active_seconds a, s.actions n FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id WHERE {real} AND date(s.started_at)>=?",
            (start,)).fetchall()
        # у тех, кто зарегистрировался не позже 7 дней назад: в скольких из первых 7 дней были активны
        depth = conn.execute(
            f"SELECT COUNT(*) c, u.telegram_id FROM users u JOIN daily_active a ON a.user_id=u.telegram_id AND a.day>=date(u.created_at) "
            f"AND a.day<=date(u.created_at,'+6 days') WHERE {real} AND date(u.created_at)>=? AND date(u.created_at)<=? GROUP BY u.telegram_id",
            ((_today() - timedelta(days=60)).isoformat(), (_today() - timedelta(days=7)).isoformat())).fetchall()
        eligible = conn.execute(
            f"SELECT COUNT(*) FROM users u WHERE {real} AND date(u.created_at)>=? AND date(u.created_at)<=?",
            ((_today() - timedelta(days=60)).isoformat(), (_today() - timedelta(days=7)).isoformat())).fetchone()[0]
    finally:
        conn.close()
    bins = [("до 10 с", 0, 10), ("10–30 с", 10, 30), ("30–60 с", 30, 60), ("1–3 мин", 60, 180), ("3–10 мин", 180, 600), ("10+ мин", 600, 10 ** 9)]
    length = [{"label": label, "count": sum(1 for s in sessions if low <= (s["a"] or 0) < high)} for label, low, high in bins]
    action_bins = [("0", 0, 1), ("1–2", 1, 3), ("3–5", 3, 6), ("6–10", 6, 11), ("11+", 11, 10 ** 9)]
    actions = [{"label": label, "count": sum(1 for s in sessions if low <= (s["n"] or 0) < high)} for label, low, high in action_bins]
    days_active = defaultdict(int)
    for r in depth:
        days_active[r["c"]] += 1
    depth_rows = [{"days": d, "count": days_active.get(d, 0)} for d in range(1, 8)]
    return {"days": days, "sessions": len(sessions), "length": length, "actions": actions,
            "week_one": {"eligible": eligible, "active_at_least_once": len(depth), "rows": depth_rows}}


# ---------------------------------------------------------------------------------------------------------------- квесты дня

QUEST_GO_LABELS = {"two_habits": "К привычкам", "plan_day": "К плану дня", "talk_adam": "К чату с Адамом"}


def quests(days=14):
    """«Квесты дня» (db/quests.py): сколько людей закрыло каждый из трёх, куда уходят из окна, возвращаются ли на следующий день.

    Выполненный квест = строка в daily_quests (награда выдаётся сама в момент выполнения), поэтому «выполнили» считаем по ней, а не по событиям.
    День квеста — местный день человека, день активности и событий — по UTC: на границе суток цифры могут разойтись на день, это оговорено в notes.
    """
    from .quests import QUEST_DEFINITIONS, QUEST_KEYS

    days = _clamp(days, 1, 90, 14)
    today = _today()
    start = (today - timedelta(days=days - 1)).isoformat()
    real = real_where()
    marks = ",".join("?" for _ in QUEST_KEYS)
    conn = connect()
    try:
        done_rows = conn.execute(
            f"SELECT q.user_id, q.day, q.quest_key, COALESCE(q.chest_opened,0) FROM daily_quests q JOIN users u ON u.telegram_id=q.user_id "
            f"WHERE {real} AND q.day>=? AND q.quest_key IN ({marks})", (start, *QUEST_KEYS)).fetchall()
        event_rows = conn.execute(
            f"SELECT e.user_id, e.name, e.step, date(e.at) FROM user_events e JOIN users u ON u.telegram_id=e.user_id "
            f"WHERE {real} AND date(e.at)>=? AND e.name IN ('quests_window_auto','quests_window_open','quests_go')", (start,)).fetchall()
        active_rows = conn.execute(
            f"SELECT a.user_id, a.day FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real} AND a.day>=?", (start,)).fetchall()
        # активность на день дальше окна нужна только для «вернулся на следующий день» — берём с запасом в 3 дня
        later_rows = conn.execute(
            f"SELECT a.user_id, a.day FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real} AND a.day>=?",
            ((today - timedelta(days=days + 3)).isoformat(),)).fetchall()
    finally:
        conn.close()

    active_days = defaultdict(set)
    for uid, day in later_rows:
        active_days[uid].add(day)
    active_users = {uid for uid, _day in active_rows}

    done_by_day = defaultdict(lambda: defaultdict(set))      # day -> quest -> {user}
    done_per_user_day = defaultdict(set)                     # (user, day) -> {quest}
    chests_opened = defaultdict(int)                         # quest -> сколько раз открыли сундук (человеко-дней)
    for uid, day, key, opened in done_rows:
        done_by_day[day][key].add(uid)
        done_per_user_day[(uid, day)].add(key)
        if opened:
            chests_opened[key] += 1

    per_quest = []
    for key in QUEST_KEYS:
        users = {uid for (uid, _day), keys in done_per_user_day.items() if key in keys}
        count = sum(1 for keys in done_per_user_day.values() if key in keys)
        per_quest.append({"key": key, "title": QUEST_DEFINITIONS[key]["title"], "emoji": QUEST_DEFINITIONS[key]["emoji"], "users": len(users),
                          "completions": count, "chests_opened": chests_opened[key], "share": _enough(len(users), len(active_users))})
    any_users = {uid for (uid, _day) in done_per_user_day}
    all_three = [(uid, day) for (uid, day), keys in done_per_user_day.items() if len(keys) == len(QUEST_KEYS)]

    series = []
    for offset in range(days):
        day = (today - timedelta(days=days - 1 - offset)).isoformat()
        row = {"day": day}
        for key in QUEST_KEYS:
            row[key] = len(done_by_day[day][key]) if day in done_by_day else 0
        series.append(row)

    window = {"auto": {"users": set(), "count": 0}, "open": {"users": set(), "count": 0}}
    go = {key: {"users": set(), "count": 0, "done_same_day": set()} for key in QUEST_KEYS}
    for uid, name, step, day in event_rows:
        if name == "quests_go" and step in go:
            go[step]["users"].add(uid)
            go[step]["count"] += 1
            if step in done_per_user_day.get((uid, day), ()):
                go[step]["done_same_day"].add(uid)
        elif name in ("quests_window_auto", "quests_window_open"):
            slot = window["auto" if name == "quests_window_auto" else "open"]
            slot["users"].add(uid)
            slot["count"] += 1
    window_users = window["auto"]["users"] | window["open"]["users"]
    went = {uid for key in QUEST_KEYS for uid in go[key]["users"]}

    # Вернулся ли человек на следующий день (и в ближайшие три): у тех, кто закрыл хотя бы один квест, и у тех, кто был активен, но не закрыл ни одного.
    def came_back(uid, day, horizon):
        base = _parse_day(day)
        return any((base + timedelta(days=n)).isoformat() in active_days[uid] for n in range(1, horizon + 1)) if base else False

    def returned(pairs, horizon):
        # день возврата должен уже закончиться (по UTC), иначе вчерашние «не вернулись» — просто ещё не успели
        eligible = [(uid, day) for uid, day in pairs if _parse_day(day) and _parse_day(day) + timedelta(days=horizon) < today]
        return _enough(sum(1 for uid, day in eligible if came_back(uid, day, horizon)), len(eligible))

    done_pairs = list(done_per_user_day.keys())
    quiet_pairs = [(uid, day) for uid, day in set(active_rows) if (uid, day) not in done_per_user_day]
    comeback = {"done": {"d1": returned(done_pairs, 1), "d3": returned(done_pairs, 3)},
                "not_done": {"d1": returned(quiet_pairs, 1), "d3": returned(quiet_pairs, 3)}}

    return {
        "days": days, "active_users": len(active_users), "any_done_users": len(any_users),
        "any_done_share": _enough(len(any_users), len(active_users)),
        "all_three": {"user_days": len(all_three), "users": len({uid for uid, _day in all_three})},
        "quests": per_quest, "series": series,
        "window": {"auto_users": len(window["auto"]["users"]), "auto_count": window["auto"]["count"],
                   "open_users": len(window["open"]["users"]), "open_count": window["open"]["count"], "users": len(window_users)},
        "go": [{"key": key, "label": QUEST_GO_LABELS[key], "users": len(go[key]["users"]), "clicks": go[key]["count"],
                "done_same_day": len(go[key]["done_same_day"]), "share_of_window": _enough(len(go[key]["users"] & window_users), len(window_users))}
               for key in QUEST_KEYS],
        "went_users": len(went),
        "comeback": comeback,
        "notes": [
            "«Выполнили» — по записям квестов: награда выдаётся сама в момент выполнения. Доля — от людей, заходивших за период.",
            "«Вернулись» — был активен на следующий день (или в ближайшие три). Это не доказывает, что причина в квестах: кто закрывает квесты, и так заходит чаще.",
            "День квеста — местный день человека, активность и нажатия — по UTC: на границе суток цифры могут разойтись на день.",
        ],
    }


# ---------------------------------------------------------------------------------------------------------------- достоверность

def quality():
    """Насколько можно верить цифрам: с какого дня данные, охват трекером, исключённые аккаунты, версии, оговорки."""
    today = _today()
    real = real_where()
    week_ago = (today - timedelta(days=6)).isoformat()
    conn = connect()
    try:
        first_session = conn.execute("SELECT MIN(started_at) FROM app_sessions").fetchone()[0]
        first_usage = conn.execute("SELECT MIN(day) FROM ui_clicks").fetchone()[0]
        first_api = conn.execute("SELECT MIN(day) FROM api_usage").fetchone()[0]
        active_week = conn.execute(
            f"SELECT COUNT(DISTINCT a.user_id) FROM daily_active a JOIN users u ON u.telegram_id=a.user_id WHERE {real} AND a.day>=?", (week_ago,)).fetchone()[0]
        tracked_week = conn.execute(
            f"SELECT COUNT(DISTINCT s.user_id) FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id WHERE {real} AND date(s.started_at)>=?", (week_ago,)).fetchone()[0]
        sessions_week = conn.execute(
            f"SELECT COUNT(*), SUM(CASE WHEN s.ended=0 AND s.last_at<? THEN 1 ELSE 0 END), SUM(CASE WHEN s.version IS NULL THEN 1 ELSE 0 END) "
            f"FROM app_sessions s JOIN users u ON u.telegram_id=s.user_id WHERE {real} AND date(s.started_at)>=?",
            ((datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), week_ago)).fetchone()
        excluded = conn.execute("SELECT telegram_id, username, first_name FROM users WHERE COALESCE(stats_excluded,0)=1").fetchall()
        users_real = conn.execute(f"SELECT COUNT(*) FROM users u WHERE {real}").fetchone()[0]
        users_all = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        last_event = conn.execute("SELECT MAX(at) FROM user_events").fetchone()[0]
        last_batch = conn.execute("SELECT MAX(last_at) FROM app_sessions").fetchone()[0]
        daily_first = conn.execute("SELECT MIN(day) FROM daily_active").fetchone()[0]
    finally:
        conn.close()
    notes = [
        "Дни считаются по UTC: сутки начинаются в 03:00 по Москве.",
        "Удержание D-N считает только тех, у кого день N уже закончился; свежие когорты помечены «не созрели».",
        "В процентах меньше 8 человек выводов делать нельзя — вместо цифры показываем «мало данных».",
        "Аккаунты, помеченные «не учитывать», и админы не входят ни в воронку, ни в удержание, ни в функции.",
        "Клики и время на экранах собирает приложение: если человек отключил Telegram до отправки (офлайн), последние секунды могут потеряться.",
    ]
    sessions_n = int(sessions_week[0] or 0)
    return {
        "tracking_since": first_session, "clicks_since": first_usage, "api_since": first_api, "active_days_since": daily_first,
        "users_real": users_real, "users_all": users_all,
        "coverage": {"active_week": active_week, "with_sessions": tracked_week, "pct": _pct(tracked_week, active_week) if active_week >= MIN_SAMPLE else None},
        "sessions_week": sessions_n,
        "unclosed_pct": _pct(int(sessions_week[1] or 0), sessions_n) if sessions_n >= MIN_SAMPLE else None,
        "without_version_pct": _pct(int(sessions_week[2] or 0), sessions_n) if sessions_n >= MIN_SAMPLE else None,
        "excluded": [{"telegram_id": r["telegram_id"], "username": r["username"], "first_name": r["first_name"]} for r in excluded],
        "last_event": last_event, "last_session_batch": last_batch, "notes": notes,
    }


# ---------------------------------------------------------------------------------------------------------------- сигналы

def signals():
    """Правила-«датчики»: что изменилось и требует взгляда. Без модели — быстро и бесплатно; модель потом объясняет и ищет причины."""
    out = []
    ov = overview(14)
    new = ov["series"]["new_users"]
    dau = ov["series"]["dau"]
    if len(new) >= 8:
        base = sum(new[-8:-1]) / 7
        if base >= 3 and new[-1] <= base * 0.4 and datetime.now(timezone.utc).hour >= 18:
            out.append({"level": "warn", "key": "reg_drop", "title": "Регистраций сегодня мало", "text": f"Сегодня {new[-1]}, обычно около {base:.0f} в день."})
        if base >= 3 and new[-1] >= base * 2.5:
            out.append({"level": "info", "key": "reg_spike", "title": "Всплеск регистраций", "text": f"Сегодня {new[-1]}, обычно около {base:.0f}. Проверь каналы: нет ли ботов."})
    if len(dau) >= 8:
        base = sum(dau[-8:-1]) / 7
        if base >= 8 and dau[-2] <= base * 0.6:
            out.append({"level": "warn", "key": "dau_drop", "title": "Вчера заходили заметно реже", "text": f"Вчера {dau[-2]} активных, неделей ранее около {base:.0f} в день."})
    h = health(2)
    if h["calls"] >= 50 and h["error_pct"] and h["error_pct"] >= 2:
        out.append({"level": "bad", "key": "errors", "title": "Много ошибок сервера", "text": f"За 2 дня {h['errors']} ошибок на {h['calls']} вызовов ({h['error_pct']}%)."})
    for route in h["routes"][:3]:
        if route["errors"] >= 5:
            out.append({"level": "bad", "key": f"route:{route['route']}", "title": "Маршрут часто падает", "text": f"{route['route']}: {route['errors']} ошибок за 2 дня."})
    if h["crash_total"] >= 5:
        out.append({"level": "warn", "key": "crashes", "title": "Ошибки в приложении у людей", "text": f"{h['crash_total']} сбоев JavaScript за 2 дня."})
    fe = features(14)
    if fe["unmapped"] or fe["unmapped_events"]:
        parts = []
        if fe["unmapped"]:
            parts.append(f"{len(fe['unmapped'])} кнопок, экранов или запросов")
        if fe["unmapped_events"]:
            parts.append(f"{len(fe['unmapped_events'])} событий")
        out.append({"level": "info", "key": "unmapped", "count": len(fe["unmapped"]) + len(fe["unmapped_events"]), "title": "Есть новое, не описанное в карте функций",
                    "text": " и ".join(parts) + " — учтены в данных, но не попали в разделы. Допиши в webapp/feature_map.py."})
    quiet = sorted((f for f in fe["features"] if f["new"] and (f["age_days"] or 0) >= 5 and fe["active_users"] >= MIN_SAMPLE
                    and (f["adoption"]["pct"] or 0) < 10), key=lambda f: (f["adoption"]["pct"] or 0, -(f["age_days"] or 0)))
    for f in quiet[:3]:
        said = "ни один из %d активных людей не воспользовался" % fe["active_users"] if not f["users"] else "попробовали %s%% активных людей" % f["adoption"]["pct"]
        out.append({"level": "info", "key": f"low_adoption:{f['id']}", "title": f"Мало пользуются новой функцией: {f['name']}",
                    "text": f"За {f['age_days']} дн. после выпуска {said}."})
    if len(quiet) > 3:
        out.append({"level": "info", "key": "low_adoption:more", "title": f"Ещё {len(quiet) - 3} новых функций почти не используют", "text": "Список — во вкладке «Функции»."})
    versions_now = versions(3)["items"]
    if len(versions_now) >= 2:
        newest, previous = versions_now[0], versions_now[1]
        if newest["bounce_pct"] is not None and previous["bounce_pct"] is not None and newest["bounce_pct"] >= previous["bounce_pct"] + 15:
            out.append({"level": "warn", "key": "version_bounce", "title": f"На версии {newest['version']} чаще закрывают сразу",
                        "text": f"Сессий короче 10 секунд: {newest['bounce_pct']}% против {previous['bounce_pct']}% на прошлой версии."})
    ch = None
    try:
        from . import traffic
        ch = [c for c in traffic.channel_stats(14) if c["suspicious"]]
    except Exception:
        ch = []
    for c in ch:
        out.append({"level": "warn", "key": f"channel:{c['code']}", "title": f"Канал «{c['title']}» выглядит подозрительно",
                    "text": f"{c['dead_pct']}% пришедших ничего не сделали, {c['burst_pct']}% регистраций — одним всплеском."})
    order = {"bad": 0, "warn": 1, "info": 2}
    out.sort(key=lambda s: order.get(s["level"], 3))
    return {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "items": out}


# ---------------------------------------------------------------------------------------------------------------- исключённые аккаунты

def set_excluded(user_id, excluded=True):
    """Пометить человека «не учитывать в статистике» (тестовый аккаунт, проверка триала). Возвращает False, если такого нет."""
    conn = connect()
    try:
        done = conn.execute("UPDATE users SET stats_excluded=? WHERE telegram_id=?", (1 if excluded else 0, int(user_id))).rowcount
        conn.commit()
        return bool(done)
    finally:
        conn.close()


def is_excluded(user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT COALESCE(stats_excluded,0) FROM users WHERE telegram_id=?", (int(user_id),)).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()
