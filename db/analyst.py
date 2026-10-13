"""
ИИ-аналитик продукта: раз в неделю (и по кнопке) читает цифры аналитики, находит, что изменилось и почему это важно, и «помнит» свои
прошлые выводы — поэтому со временем разбор становится точнее, а не начинается с нуля.

Как он «саморазвивается на наших данных»:
  • Каждый разбор получает ДАННЫЕ ЗА ПЕРИОД (воронка, удержание, функции и их влияние на возвращаемость, версии, здоровье, каналы,
    расход ИИ, оговорки о достоверности) и ПАМЯТЬ — открытые наблюдения из прошлых разборов, заметки владельца («выкатили новый онбординг
    13.10») и итоги прошлого разбора.
  • Ответ — строгий JSON: главное, находки (с цифрами и действием), что следить дальше, какие прошлые наблюдения подтвердились
    или закрыты. Новые «следить» и важные находки сохраняются как заметки (analyst_notes); закрытые помечаются resolved. В следующий
    раз модель проверяет их на свежих цифрах.
  • Заметки может добавлять и сам владелец: они попадают в контекст всех следующих разборов (так можно пометить релиз или гипотезу).

Ограничения честности: модель видит только цифры досье и обязана называть размер выборки; при малых выборках — писать «мало данных».
Цифры считаются кодом (db/stats.py), модель их не придумывает, а объясняет и сопоставляет.
"""
import json
import re
from datetime import datetime, timezone

from .core import connect
from . import stats, tracking

MIN_SECONDS_BETWEEN = 20
MAX_DIGEST_CHARS = 9000
MAX_NOTES_IN_CONTEXT = 12
NOTE_KINDS = ("finding", "watch", "owner")
NOTE_STATUSES = ("open", "resolved", "dismissed")

SYSTEM = (
    "Ты — старший продуктовый аналитик стартапа ADAM (Telegram-бот и мини-приложение для привычек, задач и дисциплины с ИИ-собеседником Адамом). "
    "Тебе дают ДОСЬЕ: цифры за период, посчитанные кодом (воронка, удержание, функции, версии приложения, здоровье сервера, каналы трафика, "
    "расход ИИ), ПАМЯТЬ (открытые наблюдения из прошлых разборов, заметки владельца) и оговорки о достоверности. Твоя задача — помочь "
    "основателю принять решения: что чинить, что усиливать, что проверить.\n\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Опирайся только на цифры досье. Ничего не выдумывай. К каждому выводу приводи цифру и размер выборки (например «3 из 14»).\n"
    "2. Если выборка меньше 8 человек, не делай вывод, а пиши «мало данных» и что нужно, чтобы проверить.\n"
    "3. Корреляция — не причина: про влияние функции на удержание пиши «связано с», а не «вызывает».\n"
    "4. Не повторяй то, что уже в открытых наблюдениях, если ничего не изменилось: либо отметь его закрытым (resolved), либо обнови одной строкой.\n"
    "5. Пиши по-русски, коротко, без markdown, без воды и без общих советов вроде «улучшите онбординг». Только конкретное: где, сколько, что сделать.\n"
    "6. Про людей — нейтрально, без догадок о поле и возрасте.\n\n"
    "ВЕРНИ ТОЛЬКО JSON без пояснений и без ```:\n"
    '{"headline": "одно предложение — главное за период",'
    ' "findings": [{"title": "коротко", "evidence": "цифры из досье", "area": "funnel|retention|feature|release|bug|channel|cost|data", '
    '"impact": "high|medium|low", "action": "конкретный шаг"}],'
    ' "watch": [{"text": "что следить дальше и по какой метрике"}],'
    ' "resolved": [id закрытых наблюдений из ПАМЯТИ],'
    ' "questions": ["что мне нужно знать от владельца, чтобы выводы были точнее"]}\n'
    "Находок не больше 6, самых важных первыми. watch — не больше 4."
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short(text, limit):
    return " ".join(str(text or "").split())[:limit]


# ---------------------------------------------------------------------------------------------------------------- досье

def _fmt_pct(block):
    if not block:
        return "—"
    if block.get("pct") is None:
        return f"мало данных ({block['n']} из {block['of']})"
    return f"{block['pct']}% ({block['n']} из {block['of']})"


def build_digest(days=14):
    """Текст досье для модели. Все цифры — из db/stats.py; здесь только раскладка по строкам."""
    days = max(3, min(int(days), 60))
    ov = stats.overview(days)
    t = ov["tiles"]
    lines = [
        f"ПЕРИОД: последние {days} дн. (по UTC), сегодня {ov['today']}",
        f"ЛЮДИ: всего настоящих {t['users_total']}, новых за период {t['new_users']}, активных за период {t['active_period']}, "
        f"сегодня {t['dau_today']}, вчера {t['dau_yesterday']}, за 7 дн. {t['wau']}, за 30 дн. {t['mau']}, DAU/MAU {t['stickiness'] if t['stickiness'] is not None else 'мало данных'}%",
        f"СЕССИИ: {t['sessions']}, средняя активная длительность {t['avg_session_seconds'] if t['avg_session_seconds'] is not None else '—'} с, "
        f"на человека {t['sessions_per_active_user'] if t['sessions_per_active_user'] is not None else '—'}",
        f"АКТИВАЦИЯ новых: добавили привычку — {_fmt_pct(t['activation_habit'])}; написали Адаму — {_fmt_pct(t['activation_ai'])}",
        f"ОПЛАТЫ: плативших {t['payers']}, выручка {t['revenue_stars']} ⭐",
        "РЯД ПО ДНЯМ (новые / активные): " + ", ".join(f"{d[5:]}:{n}/{a}" for d, n, a in zip(ov["series"]["days"], ov["series"]["new_users"], ov["series"]["dau"]))[-700:],
    ]

    ret = stats.retention(days=max(days * 2, 30))
    o = ret["overall"]
    lines.append("\nУДЕРЖАНИЕ (когорта за " + str(ret["days"]) + " дн.; день N — активен именно в этот день после регистрации): "
                 + "; ".join(f"D{m} {_fmt_pct(o[f'd{m}'])}" for m in stats.MARKS))
    for c in ret["cohorts"][:5]:
        lines.append(f"  неделя {c['label']}: {c['n']} чел.; D1 {_fmt_pct(c['d1'])}; D7 {_fmt_pct(c['d7'])}")

    f = tracking.funnel(days=days)
    lines.append(f"\nВОРОНКА новых за {days} дн. ({f['total']} чел.):")
    for s in f["steps"]:
        lines.append(f"  {s['label']}: {s['count']} ({s['pct_of_all']}%)" + (f", медиана {s['median_minutes']} мин" if s["median_minutes"] is not None else ""))
    if f["stuck"]:
        lines.append("  где застревают: " + "; ".join(f"{x['label']} — {x['count']}" for x in f["stuck"]))
    if f["skips"]:
        lines.append("  пропуски: " + "; ".join(f"{x['name']}/{x['step']} — {x['users']} чел." for x in f["skips"][:6]))
    if f["exits"]:
        lines.append("  после чего выходят: " + "; ".join(f"«{x['label']}» ×{x['count']}" for x in f["exits"][:6]))

    fe = stats.features(days)
    lines.append(f"\nФУНКЦИИ (за {days} дн., активных людей {fe['active_users']}; «охват» = доля активных, «возврат» = доля, кто пользовался в 2+ разных дня):")
    ranked = sorted(fe["features"], key=lambda x: -x["users"])
    for x in ranked:
        if not x["users"] and not x["new"]:
            continue
        trend = ""
        if x["prev_users"] or x["users"]:
            trend = f", прошлый период {x['prev_users']}"
        impact = ""
        if x["impact"]:
            i = x["impact"]
            impact = f"; D7 у тех, кто попробовал в первые 3 дня, {i['tried']['d7']}% ({i['tried']['n']} чел.), у остальных {i['not_tried']['d7']}% ({i['not_tried']['n']} чел.)"
        lines.append(f"  {x['name']}{' [НОВАЯ, с ' + x['since'] + ']' if x['new'] else ''}: {x['users']} чел., охват {_fmt_pct(x['adoption'])}, "
                     f"возврат {_fmt_pct(x['repeat'])}{trend}{impact}")
    if fe["unmapped"] or fe["unmapped_events"]:
        lines.append("  НЕ ПРИВЯЗАНО К ФУНКЦИЯМ (учтено, но без карты): " + "; ".join(
            f"{u['title']} ×{u['uses']}" for u in fe["unmapped"][:8]) + ("; события: " + ", ".join(e["name"] for e in fe["unmapped_events"][:5]) if fe["unmapped_events"] else ""))

    vs = stats.versions(5)["items"]
    if vs:
        lines.append("\nВЕРСИИ ПРИЛОЖЕНИЯ (новые сверху): ")
        for v in vs:
            lines.append(f"  {v['version']} (с {v['first_seen'][:16]}): сессий {v['sessions']}, людей {v['users']}, средняя активность "
                         f"{v['avg_active_seconds'] if v['avg_active_seconds'] is not None else '—'} с, закрыли за <10 с: "
                         f"{v['bounce_pct'] if v['bounce_pct'] is not None else 'мало данных'}%; новичков {v['newcomers']}, вернулись на 2-й день {_fmt_pct(v['newcomers_d1'])}")

    h = stats.health(min(days, 14))
    lines.append(f"\nЗДОРОВЬЕ: вызовов API {h['calls']}, ошибок сервера {h['errors']} ({h['error_pct'] if h['error_pct'] is not None else '—'}%), сбоев JS у людей {h['crash_total']}")
    for r in h["routes"][:4]:
        if r["errors"]:
            lines.append(f"  падает: {r['route']} — {r['errors']} из {r['calls']}")
    for r in h["slowest"][:3]:
        lines.append(f"  медленно: {r['route']} — в среднем {r['avg_ms']} мс")
    for c in h["crashes"][:3]:
        lines.append(f"  сбой в приложении: «{c['message'][:90]}» ×{c['count']} ({c['users']} чел.)")

    try:
        from . import traffic
        channels = [c for c in traffic.channel_stats(days) if c["joined"]]
        if channels:
            lines.append("\nКАНАЛЫ: " + "; ".join(
                f"{c['title']} — пришло {c['joined']}, до привычки {c['habit_done']}%, D1 {c['d1']}%, мёртвых {c['dead_pct']}%" + (", ПОДОЗРИТЕЛЬНО" if c["suspicious"] else "")
                for c in channels[:8]))
    except Exception:
        pass
    try:
        from . import ai_usage
        ai = ai_usage.overview(days, limit=3)
        lines.append(f"\nРАСХОД ИИ: {ai['tokens']} токенов за {days} дн., на человека в среднем {ai['avg_per_user']}; по функциям: "
                     + ", ".join(f"{x['label']} {x['share']}%" for x in ai["by_feature"][:5]))
    except Exception:
        pass

    q = stats.quality()
    lines.append(f"\nДОСТОВЕРНОСТЬ: данные трекера с {q['tracking_since'] or 'нет данных'}; охват трекером активных за неделю: "
                 f"{q['coverage']['pct'] if q['coverage']['pct'] is not None else 'мало данных'}%; исключено из статистики аккаунтов: {len(q['excluded'])}")

    sg = stats.signals()["items"]
    if sg:
        lines.append("\nСИГНАЛЫ ДАТЧИКОВ: " + "; ".join(f"[{s['level']}] {s['title']} — {s['text']}" for s in sg[:8]))

    memory = memory_for_prompt()
    if memory:
        lines.append("\nПАМЯТЬ (прошлые наблюдения и заметки владельца; id нужен, чтобы закрыть):\n" + memory)
    return "\n".join(lines)[:MAX_DIGEST_CHARS]


# ---------------------------------------------------------------------------------------------------------------- память

def memory_for_prompt():
    conn = connect()
    try:
        notes = conn.execute(
            "SELECT id, kind, text, created_at FROM analyst_notes WHERE status='open' ORDER BY (kind='owner') DESC, id DESC LIMIT ?", (MAX_NOTES_IN_CONTEXT,)).fetchall()
        last = conn.execute("SELECT created_at, text FROM analyst_reports ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    kinds = {"finding": "находка", "watch": "следить", "owner": "заметка владельца"}
    parts = [f"  #{n['id']} [{kinds.get(n['kind'], n['kind'])}, {n['created_at'][:10]}] {_short(n['text'], 220)}" for n in notes]
    if last:
        parts.append(f"  прошлый разбор от {last['created_at'][:10]}: {_short(last['text'], 500)}")
    return "\n".join(parts)


def add_note(text, kind="owner"):
    text = _short(text, 400)
    if not text or kind not in NOTE_KINDS:
        return None
    conn = connect()
    try:
        exists = conn.execute("SELECT id FROM analyst_notes WHERE status='open' AND kind=? AND text=?", (kind, text)).fetchone()
        if exists:
            return exists["id"]
        cursor = conn.execute("INSERT INTO analyst_notes(created_at, kind, text) VALUES (?,?,?)", (tracking._fmt(_now()), kind, text))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def set_note_status(note_id, status):
    if status not in NOTE_STATUSES:
        return False
    conn = connect()
    try:
        done = conn.execute("UPDATE analyst_notes SET status=? WHERE id=?", (status, int(note_id))).rowcount
        conn.commit()
        return bool(done)
    finally:
        conn.close()


def list_notes(status="open", limit=40):
    conn = connect()
    try:
        if status == "all":
            rows = conn.execute("SELECT * FROM analyst_notes ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM analyst_notes WHERE status=? ORDER BY id DESC LIMIT ?", (status, int(limit))).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- разбор модели

def parse_answer(text):
    """JSON из ответа модели (с запасом на ```-ограды и текст вокруг). None — не удалось: тогда разбор сохраняется как обычный текст."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    findings = []
    for item in (data.get("findings") or [])[:6]:
        if isinstance(item, dict) and _short(item.get("title"), 140):
            findings.append({
                "title": _short(item.get("title"), 140), "evidence": _short(item.get("evidence"), 360), "action": _short(item.get("action"), 300),
                "area": _short(item.get("area"), 20) or "data", "impact": item.get("impact") if item.get("impact") in ("high", "medium", "low") else "medium",
            })
    watch = [_short(w.get("text") if isinstance(w, dict) else w, 300) for w in (data.get("watch") or [])[:4]]
    resolved = []
    for raw in data.get("resolved") or []:
        digits = str(raw).lstrip("#")
        if digits.isdigit():
            resolved.append(int(digits))
    return {
        "headline": _short(data.get("headline"), 300), "findings": findings, "watch": [w for w in watch if w],
        "resolved": resolved, "questions": [_short(q, 240) for q in (data.get("questions") or [])[:4] if _short(q, 240)],
    }


def render(report):
    """Человеческий текст разбора (для истории, пушей и вкладки без JS)."""
    lines = [report["headline"]] if report.get("headline") else []
    impact = {"high": "важно", "medium": "заметно", "low": "мелочь"}
    for i, f in enumerate(report.get("findings", []), 1):
        lines.append(f"{i}. [{impact.get(f['impact'], f['impact'])}] {f['title']}. {f['evidence']} → {f['action']}".strip())
    if report.get("watch"):
        lines.append("Следить: " + "; ".join(report["watch"]))
    if report.get("questions"):
        lines.append("Вопросы к тебе: " + " ".join(report["questions"]))
    return "\n".join(lines)


def _remember(report_id, report):
    conn = connect()
    try:
        for note_id in report["resolved"]:
            conn.execute("UPDATE analyst_notes SET status='resolved' WHERE id=? AND status='open'", (note_id,))
        stamp = tracking._fmt(_now())
        for kind, texts in (("finding", [f"{f['title']}: {f['evidence']}" for f in report["findings"] if f["impact"] == "high"]), ("watch", report["watch"])):
            for text in texts:
                text = _short(text, 400)
                if conn.execute("SELECT 1 FROM analyst_notes WHERE status='open' AND text=?", (text,)).fetchone():
                    continue
                conn.execute("INSERT INTO analyst_notes(created_at, report_id, kind, text) VALUES (?,?,?,?)", (stamp, report_id, kind, text))
        conn.commit()
    finally:
        conn.close()


async def _ask_model(system, user, max_tokens):
    from multi_agent import MODEL, _ask
    from . import ai_usage
    with ai_usage.scope(None, "analyst"):
        return await _ask(system, user, temperature=0.2, max_tokens=max_tokens, model=MODEL)


def latest():
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM analyst_reports ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return _public(row)


def history(limit=10):
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM analyst_reports ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    return [_public(r) for r in rows]


def _public(row):
    if row is None:
        return None
    try:
        report = json.loads(row["report"]) if row["report"] else None
    except ValueError:
        report = None
    return {"id": row["id"], "created_at": row["created_at"], "days": row["days"], "trigger": row["trigger"], "report": report, "text": row["text"]}


async def run(days=14, trigger="manual"):
    """Новый разбор. {"ok": True, "report": {...}} либо {"error": ...}."""
    days = max(3, min(int(days), 60))
    previous = latest()
    if previous:
        created = tracking._parse(previous["created_at"])
        if created and (_now() - created).total_seconds() < MIN_SECONDS_BETWEEN:
            return {"error": "too_soon", "report": previous}
    digest = build_digest(days)
    try:
        answer = await _ask_model(SYSTEM, digest, 1400)
    except Exception as exc:
        return {"error": "model_failed", "message": str(exc)[:200]}
    answer = (answer or "").strip()
    if not answer:
        return {"error": "model_failed", "message": "пустой ответ"}
    report = parse_answer(answer)
    text = render(report) if report else answer
    conn = connect()
    try:
        cursor = conn.execute(
            "INSERT INTO analyst_reports(created_at, days, trigger, report, text) VALUES (?,?,?,?,?)",
            (tracking._fmt(_now()), days, trigger, json.dumps(report, ensure_ascii=False) if report else None, text))
        conn.commit()
        report_id = cursor.lastrowid
    finally:
        conn.close()
    if report:
        _remember(report_id, report)
    return {"ok": True, "report": latest(), "notes": list_notes("open")}

