"""
ИИ-сводки для основателя: по каждому человеку (кто он, как пользуется, где застрял, что нравится и что отталкивает, риск ухода, что
сделать, сегмент для форума) и общий разбор «что повторяется у людей» за период.

Сводку можно запросить в любой момент заново: хранится история, у последней видно, изменились ли данные после неё (stale).
Досье собирается только из данных самого приложения (путь, привычки, задачи, сообщения человека Адаму, оценки); ответы Адама и чужие
данные в него не попадают. Расход токенов на сводки не списывается с человека (scope без user_id, функция «summary»).
"""
import hashlib
from datetime import datetime, timedelta, timezone

from .core import connect
from . import tracking

KINDS = ("short", "extended")
MAX_DOSSIER_CHARS = 7000
MIN_SECONDS_BETWEEN = 15

SUMMARY_SYSTEM = (
    "Ты — аналитик продукта ADAM (бот и приложение для привычек, задач и дисциплины). Тебе дают досье одного пользователя: его путь в "
    "приложении, привычки, задачи, сообщения Адаму и поведение. Напиши сводку для основателя продукта, чтобы он понял человека и решил, "
    "что с ним делать. Пиши по-русски, коротко и по делу, без воды. Опирайся ТОЛЬКО на данные досье, ничего не выдумывай: если данных "
    "мало — так и скажи. Диагнозов не ставь. Не используй markdown: ни звёздочек, ни решёток, ни дефисов-списков, только строки с названием "
    "раздела и двоеточием.\n\n"
    "Формат (каждый раздел с новой строки):\n"
    "Кто это: 1–2 предложения (чем занят, чего хочет — со слов человека).\n"
    "Как пользуется: паттерны (когда заходит, сколько в приложении, какие разделы, как отмечает привычки, о чём пишет Адаму).\n"
    "Где застрял или что пропустил: конкретные шаги онбординга, привычки, кнопки.\n"
    "Что ему нравится и на что опирается: по поведению и словам.\n"
    "Что может раздражать или отталкивать: по пропускам, выходам, негативным словам.\n"
    "Риск ухода: низкий, средний или высокий — и почему одной фразой.\n"
    "Что сделать: 1–3 конкретных шага (что написать человеку, что поправить в продукте).\n"
    "Сегмент для форума: короткая метка из 2–4 слов; теги: три слова через запятую."
)
SHORT_ADDON = (
    "\n\nСейчас нужна КОРОТКАЯ версия: максимум 5 строк — «Кто это», «Главное в поведении», «Риск ухода», «Что сделать» (один шаг), "
    "«Сегмент для форума»."
)
INSIGHTS_SYSTEM = (
    "Ты — аналитик продукта ADAM (бот и приложение для привычек и дисциплины). Тебе дают сводные данные за период: воронка новых людей, "
    "где застревают, что пропускают, после чего выходят из приложения, о чём люди пишут Адаму, оценки ответов, ошибки на устройствах, "
    "каналы трафика. Найди повторяющееся: на что люди опираются (что работает), что их отталкивает или ломает, какие ошибки повторяются. "
    "Пиши по-русски, коротко, ТОЛЬКО по данным, без выдумок и без markdown.\n\n"
    "Формат:\nЧто работает: 3–5 пунктов (каждый с цифрой из данных).\nЧто мешает: 3–5 пунктов (с цифрой).\nПовторяющиеся ошибки: список или «нет».\n"
    "Каналы: какие приводят живых людей, какие подозрительны.\nЧто сделать на этой неделе: 3–5 конкретных шагов по важности."
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short(text, limit):
    return " ".join(str(text or "").split())[:limit]


# ---------------------------------------------------------------------------------------------------------------- досье

def _stamp(user_id):
    """Отпечаток данных человека: меняется, когда у него появилось что-то новое (событие, сообщение, отметка, сессия, привычка)."""
    conn = connect()
    try:
        parts = [
            conn.execute("SELECT COALESCE(MAX(id),0) FROM user_events WHERE user_id=?", (user_id,)).fetchone()[0],
            conn.execute("SELECT COALESCE(MAX(id),0) FROM ai_messages WHERE user_id=? AND role='user'", (user_id,)).fetchone()[0],
            conn.execute("SELECT COALESCE(MAX(id),0) FROM habit_completion_events WHERE user_id=?", (user_id,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM app_sessions WHERE user_id=?", (user_id,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM habits WHERE user_id=?", (user_id,)).fetchone()[0],
        ]
    finally:
        conn.close()
    return hashlib.sha1(":".join(str(p) for p in parts).encode()).hexdigest()[:12]


def build_dossier(user_id, max_chars=MAX_DOSSIER_CHARS):
    """Текстовое досье для модели. None — человека нет."""
    journey = tracking.journey(user_id)
    if journey is None:
        return None
    conn = connect()
    try:
        user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        keys = user.keys()
        survey = conn.execute("SELECT business, life_goal, bot_goal, ai_summary FROM user_survey WHERE user_id=?", (user_id,)).fetchone()
        habits = conn.execute("SELECT id, title, planned_time FROM habits WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        since = (_now() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        done_days = conn.execute("SELECT COUNT(DISTINCT date(completed_at)) AS n, COUNT(*) AS c FROM habit_completion_events WHERE user_id=? AND completed_at>=?", (user_id, since)).fetchone()
        per_habit = {r["habit_id"]: r["n"] for r in conn.execute(
            "SELECT habit_id, COUNT(DISTINCT date(completed_at)) AS n FROM habit_completion_events WHERE user_id=? AND completed_at>=? GROUP BY habit_id", (user_id, since)).fetchall()}
        goals = conn.execute(
            "SELECT plan_date, main_goal, main_goal_completed FROM daily_plans WHERE user_id=? AND COALESCE(main_goal,'')!='' ORDER BY plan_date DESC LIMIT 7", (user_id,)).fetchall()
        tasks = conn.execute(
            "SELECT t.text, t.completed FROM daily_plan_tasks t JOIN daily_plans p ON p.id=t.plan_id WHERE p.user_id=? ORDER BY t.id DESC LIMIT 10", (user_id,)).fetchall()
        messages = conn.execute("SELECT message, created_at FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT 30", (user_id,)).fetchall()
        feedback = conn.execute("SELECT rating, COUNT(*) AS n FROM ai_feedback WHERE user_id=? GROUP BY rating", (user_id,)).fetchall()
        memory = conn.execute("SELECT summary FROM user_ai_profile WHERE user_id=?", (user_id,)).fetchone()
        friends = conn.execute("SELECT COUNT(*) FROM friendships WHERE user_id=?", (user_id,)).fetchone()[0]
        pairs = conn.execute("SELECT COUNT(*) FROM pair_quests WHERE status='completed' AND (inviter_id=? OR invitee_id=?)", (user_id, user_id)).fetchone()[0]
        errors = conn.execute("SELECT COUNT(*) FROM client_errors WHERE user_id=?", (user_id,)).fetchone()[0]
        tokens = conn.execute("SELECT COALESCE(SUM(tokens),0) FROM ai_token_log WHERE user_id=? AND day>=?", (user_id, str((_now() - timedelta(days=6)).date()))).fetchone()[0]
    finally:
        conn.close()

    created = tracking._parse(user["created_at"])
    days_in = (_now() - created).days if created else 0
    lines = ["ПРОФИЛЬ"]
    lines.append(f"Имя: {_short(user['first_name'], 40) or '—'}; в проекте {days_in} дн.; уровень {user['level']}; серия {user['streak']}"
                 + (f" (рекорд {user['best_streak']})" if "best_streak" in keys and user["best_streak"] else ""))
    for label, column in (("Архетип", "archetype"), ("Цель из квиза", "onboarding_goal"), ("Возраст", "onboarding_age_range"), ("Долгосрочная цель", "long_term_goals")):
        if column in keys and user[column]:
            lines.append(f"{label}: {_short(user[column], 160)}")
    if survey:
        lines.append(f"Анкета: занятие — {_short(survey['business'], 100) or '—'}; прокачать — {_short(survey['life_goal'], 100) or '—'}; цель — {_short(survey['bot_goal'], 140) or '—'}")
    if journey.get("source"):
        lines.append(f"Канал прихода: {journey['source']}")
    lines.append(f"Друзей: {friends}; выполненных парных заданий: {pairs}; ошибок на устройстве: {errors}; токенов ИИ за 7 дней: {tokens}")

    lines.append("\nПУТЬ (минуты — от регистрации)")
    for m in journey["milestones"]:
        if m["at"]:
            extra = f" — «{_short(m['extra'], 80)}»" if m.get("extra") else ""
            lines.append(f"✓ {m['label']}: {m['minutes']} мин{extra}")
        else:
            lines.append(f"✗ {m['label']}: нет")
    o = journey["onboarding"]
    skipped = ", ".join("%s(%s)" % (item["name"], item["step"]) for item in o["skips"]) or "нет"
    lines.append(f"Онбординг: ударный режим {'нажал' if o['streak_ok'] else 'не нажал'}; квиз последний шаг {o.get('quiz_last_label') or o['quiz_last_step'] or '—'}; "
                 f"тур шаг {o.get('tour_last_label') or o['tour_last_step'] or '—'}; пропуски: {skipped}")
    s = journey["sessions"]
    lines.append(f"Сессий: {s['count']}; в приложении всего {s['active_seconds'] // 60} мин, в среднем {s['avg_active_seconds']} с; дней с заходами: {s['days_active']}; "
                 f"вернулся на след. день: {'да' if journey['returns'].get('d1') else 'нет'}, через неделю: {'да' if journey['returns'].get('d7') else 'нет'}")
    if s["top_exits"]:
        lines.append("Чаще всего выходит после: " + ", ".join(f"«{e['label']}» ×{e['count']}" for e in s["top_exits"]))
    if s["last"]:
        lines.append(f"Последняя сессия: вышел после «{s['last']['last_action'] or '—'}», экран «{s['last']['last_screen'] or '—'}»")

    lines.append("\nПРИВЫЧКИ И ЗАДАЧИ")
    if habits:
        for h in habits:
            lines.append(f"• {_short(h['title'], 60)}" + (f" (время {h['planned_time']})" if h["planned_time"] else "") + f" — отмечена в {per_habit.get(h['id'], 0)} из 14 дней")
        lines.append(f"За 14 дней: отметки в {done_days['n']} днях, всего {done_days['c']}")
    else:
        lines.append("Привычек нет")
    if goals:
        lines.append("Главные задачи (новые сверху): " + "; ".join(f"«{_short(g['main_goal'], 60)}» {'✓' if g['main_goal_completed'] else '✗'}" for g in goals))
    if tasks:
        lines.append("Задачи: " + "; ".join(f"«{_short(t['text'], 50)}» {'✓' if t['completed'] else '✗'}" for t in tasks))

    lines.append("\nСООБЩЕНИЯ АДАМУ (новые сверху)")
    if messages:
        lines += [f"— {_short(m['message'], 180)}" for m in messages]
    else:
        lines.append("Адаму не писал")
    if feedback:
        lines.append("Оценки ответов Адама: " + ", ".join(f"{r['rating']} ×{r['n']}" for r in feedback))
    if memory and memory["summary"]:
        lines.append("Что Адам запомнил о человеке: " + _short(memory["summary"], 300))

    text = "\n".join(lines)
    return text[:max_chars]


# ---------------------------------------------------------------------------------------------------------------- хранение

def _store(user_id, kind, text, stamp, tokens=0):
    conn = connect()
    try:
        cur = conn.execute("INSERT INTO user_summaries(user_id, kind, text, stamp, tokens) VALUES (?,?,?,?,?)", (user_id, kind, text, stamp, tokens))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def latest(user_id, kind):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM user_summaries WHERE user_id=? AND kind=? ORDER BY id DESC LIMIT 1", (user_id, kind)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def get_summaries(user_id):
    """Последняя короткая и расширенная сводки; stale — данные человека изменились после неё."""
    current = _stamp(user_id)
    out = {"stamp": current}
    for kind in KINDS:
        row = latest(user_id, kind)
        out[kind] = {**row, "stale": row["stamp"] != current} if row else None
    return out


def history(user_id, limit=10):
    conn = connect()
    try:
        rows = conn.execute("SELECT id, kind, created_at, stamp FROM user_summaries WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, int(limit))).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------- генерация

async def _ask_model(system, user, max_tokens):
    from multi_agent import MODEL, _ask
    from db import ai_usage
    with ai_usage.scope(None, "summary"):
        return await _ask(system, user, temperature=0.3, max_tokens=max_tokens, model=MODEL)


async def generate(user_id, kind="short"):
    """Новая сводка по человеку. {"ok": True, "summary": {...}} либо {"error": ...}."""
    if kind not in KINDS:
        return {"error": "invalid_kind"}
    dossier = build_dossier(user_id)
    if dossier is None:
        return {"error": "user_not_found"}
    previous = latest(user_id, kind)
    if previous:
        created = tracking._parse(previous["created_at"])
        if created and (_now() - created).total_seconds() < MIN_SECONDS_BETWEEN:
            return {"error": "too_soon", "summary": get_summaries(user_id)[kind]}
    stamp = _stamp(user_id)
    system = SUMMARY_SYSTEM + (SHORT_ADDON if kind == "short" else "")
    try:
        text = await _ask_model(system, dossier, 350 if kind == "short" else 800)
    except Exception as exc:
        return {"error": "model_failed", "message": str(exc)[:200]}
    text = (text or "").strip()
    if not text:
        return {"error": "model_failed", "message": "пустой ответ"}
    _store(user_id, kind, text, stamp)
    return {"ok": True, "summary": get_summaries(user_id)[kind]}


# ---------------------------------------------------------------------------------------------------------------- общий разбор

def build_insights_dossier(days=14):
    from webapp.services import chat_brain
    from . import traffic
    f = tracking.funnel(days=days)
    lines = [f"ПЕРИОД: последние {days} дн.; новых людей: {f['total']}", "\nВОРОНКА (шаг — сколько, % от пришедших, медиана минут от регистрации)"]
    for s in f["steps"]:
        lines.append(f"{s['label']}: {s['count']} ({s['pct_of_all']}%)" + (f", ~{s['median_minutes']} мин" if s["median_minutes"] is not None else ""))
    if f["stuck"]:
        lines.append("Где застревают: " + "; ".join(f"{x['label']} — {x['count']}" for x in f["stuck"]))
    if f["skips"]:
        lines.append("Что пропускают: " + "; ".join(f"{x['name']} шаг {x['step']} — {x['users']} чел." for x in f["skips"]))
    if f["exits"]:
        lines.append("После чего выходят: " + "; ".join(f"«{x['label']}»{' (' + x['screen'] + ')' if x['screen'] else ''} ×{x['count']}" for x in f["exits"]))

    since = tracking._fmt(_now() - timedelta(days=days))
    conn = connect()
    try:
        questions = [r["message"] for r in conn.execute(
            "SELECT message FROM ai_messages WHERE role='user' AND created_at>=? ORDER BY id DESC LIMIT 500", (since,)).fetchall()]
        ratings = conn.execute("SELECT rating, COUNT(*) AS n FROM ai_feedback WHERE created_at>=? GROUP BY rating", (since,)).fetchall()
        reasons = conn.execute("SELECT reason, COUNT(*) AS n FROM ai_feedback WHERE created_at>=? AND rating='down' AND reason IS NOT NULL GROUP BY reason ORDER BY n DESC LIMIT 8", (since,)).fetchall()
        errors = conn.execute("SELECT substr(message,1,90) AS m, COUNT(*) AS n, COUNT(DISTINCT user_id) AS u FROM client_errors WHERE created_at>=? GROUP BY m ORDER BY n DESC LIMIT 8", (since,)).fetchall()
    finally:
        conn.close()
    scenarios, moods = {}, {}
    for text in questions:
        for key in chat_brain.detect_scenarios(text):
            scenarios[key] = scenarios.get(key, 0) + 1
        mood = chat_brain.detect_mood(text)
        if mood != "нейтрально":
            moods[mood] = moods.get(mood, 0) + 1
    lines.append(f"\nСООБЩЕНИЯ АДАМУ: всего {len(questions)}")
    if scenarios:
        ranked = sorted(scenarios.items(), key=lambda x: -x[1])[:10]
        lines.append("О чём пишут (тип сообщения — число): " + "; ".join("%s — %d" % (chat_brain.SCENARIO_LABELS.get(k, k), n) for k, n in ranked))
    if moods:
        lines.append("Настроение по словам: " + ", ".join(f"{m} ×{n}" for m, n in sorted(moods.items(), key=lambda x: -x[1])))
    if ratings:
        lines.append("Оценки ответов: " + ", ".join(f"{r['rating']} ×{r['n']}" for r in ratings))
    if reasons:
        lines.append("Причины дизлайков: " + "; ".join(f"{r['reason']} ×{r['n']}" for r in reasons))
    if errors:
        lines.append("Повторяющиеся ошибки на устройствах: " + "; ".join(f"«{r['m']}» ×{r['n']} ({r['u']} чел.)" for r in errors))
    channels = [c for c in traffic.channel_stats(days) if c["joined"]]
    if channels:
        lines.append("\nКАНАЛЫ: " + "; ".join(
            f"{c['title']} — пришло {c['joined']}, до привычки {c['habit_done']}%, D1 {c['d1']}%, мёртвых {c['dead_pct']}%" + (", ПОДОЗРИТЕЛЬНО" if c["suspicious"] else "") for c in channels[:8]))
    return "\n".join(lines)[:MAX_DOSSIER_CHARS]


async def generate_insights(days=14):
    dossier = build_insights_dossier(days)
    try:
        text = await _ask_model(INSIGHTS_SYSTEM, dossier, 900)
    except Exception as exc:
        return {"error": "model_failed", "message": str(exc)[:200]}
    text = (text or "").strip()
    if not text:
        return {"error": "model_failed", "message": "пустой ответ"}
    _store(0, "insights", text, f"{days}d")
    return {"ok": True, "insights": latest(0, "insights")}
