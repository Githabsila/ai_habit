"""
Сценарий сообщений и приглашений триала (ТЗ «Триал, подписка и приглашения» 28.10.2026, §12–§16 и §18) — всё, что решает «показывать ли, что и когда»,
без Telegram и без экрана:

• журнал предложений `trial_offers` — где человеку показали предложение подписки (app — карточка в приложении после полезного действия, chat — под ответом Адама,
  push — внешнее сообщение в Telegram) и что он сделал (shown/sent, clicked, dismissed). По нему предложения не повторяются в нескольких местах подряд, «Не сейчас»
  гасит автоматические предложения до конца дня, а в статистике «отправлено», «показано» и «нажато» — разные события;
• предложение внутри приложения во второй день после настоящего действия (отметка, работа с планом, содержательный ответ Адама) — `app_offer`;
• умное приглашение к разговору (§15): вопрос Адама по данным человека (`open_invite`), краткая сводка по просьбе (`chat_summary`) и одно предложение подписки
  после содержательного ответа, не чаще раза в день (`chat_offer`);
• редкие сообщения после триала (§18): пауза между ними, длиннее при отсутствии реакции, отказ, остановка после оплаты (`winback_state`).

Тексты берут только настоящие факты (число отметок, названия привычек из самого приложения, выполненные задачи); цены и сроки сюда не попадают — их показывает экран
подписки из правил приложения. На экран блокировки личные подробности не выходят: названия привычек и задач живут только внутри приложения.
"""
import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import connect

UTC = timezone.utc
FMT = "%Y-%m-%d %H:%M:%S"

# Приглашение к разговору — не продажа: его показ не гасит последующее предложение подписки в тот же день.
SALE_FREE_KEYS = {"d3_talk"}

# Редкие сообщения после триала. Точные интервалы владелец уточнит перед включением (ТЗ §18) — безопасные значения по умолчанию собраны здесь.
WINBACK_FIRST_AFTER_DAYS = 3          # первое — через столько суток после конца дополнительного окна
WINBACK_GAPS_DAYS = (4, 7, 14, 21)    # пауза после 1-го, 2-го, 3-го и следующих сообщений
WINBACK_MAX_MESSAGES = 5
WINBACK_SLOW_AFTER = 2                # после стольких сообщений подряд без захода паузы вдвое длиннее
WINBACK_STOP_AFTER = 3                # …а после стольких — серия останавливается сама
WINBACK_PREFIX = "wb_"

PLAN_TRIGGERS = ("habit", "plan", "adam")


# ---------------------------------------------------------------------------------------------------------------- время

def _tz(user_id):
    from .streak import get_timezone
    try:
        return ZoneInfo(get_timezone(user_id))
    except Exception:
        return ZoneInfo("UTC")


def local_date_of(user_id, now_utc=None):
    now = now_utc or datetime.now(UTC)
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    return now.astimezone(_tz(user_id)).date().isoformat()


def day_bounds_utc(local_date, tz):
    """Границы местных суток в UTC (строки формата базы): [начало, конец)."""
    day = date.fromisoformat(local_date)
    start = datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
    return start.strftime(FMT), end.strftime(FMT)


def _pick(user_id, tag, options):
    """Вариант текста не случайный: зависит от человека и метки (день, номер сообщения) — повторный вызов даёт тот же текст, а соседние дни — разные."""
    seed = int(hashlib.sha1(f"{user_id}:{tag}".encode()).hexdigest(), 16)
    return options[seed % len(options)]


# ---------------------------------------------------------------------------------------------------------------- журнал предложений

def record(user_id, place, key, event, local_date=None):
    local_date = local_date or local_date_of(user_id)
    conn = connect()
    try:
        conn.execute("INSERT INTO trial_offers(user_id, local_date, place, key, event) VALUES (?, ?, ?, ?, ?)", (user_id, local_date, place, key, event))
        conn.commit()
    finally:
        conn.close()


def offer_state(user_id, local_date=None):
    """Что за этот местный день уже было: {sale_shown, dismissed, places, keys}. sale_shown — человеку уже показали предложение подписки (в любом месте)."""
    local_date = local_date or local_date_of(user_id)
    conn = connect()
    try:
        rows = conn.execute("SELECT place, key, event FROM trial_offers WHERE user_id=? AND local_date=?", (user_id, local_date)).fetchall()
    finally:
        conn.close()
    sale = any(r["event"] in ("shown", "sent") and not (r["place"] == "push" and r["key"] in SALE_FREE_KEYS) and not str(r["key"]).startswith("invite")
               for r in rows)
    return {
        "sale_shown": sale, "dismissed": any(r["event"] == "dismissed" for r in rows),
        "places": sorted({r["place"] for r in rows}), "keys": sorted({str(r["key"]) for r in rows if r["key"]}),
    }


def opened_today(user_id, local_date=None):
    """Заходил ли человек в приложение в этот местный день (событие «Открыл приложение»; обращения к боту не считаются)."""
    local_date = local_date or local_date_of(user_id)
    start, end = day_bounds_utc(local_date, _tz(user_id))
    conn = connect()
    try:
        row = conn.execute("SELECT 1 FROM user_events WHERE user_id=? AND name='session_start' AND at>=? AND at<? LIMIT 1", (user_id, start, end)).fetchone()
    finally:
        conn.close()
    return row is not None


def _count(user_id, query, params):
    conn = connect()
    try:
        return int(conn.execute(query, params).fetchone()[0] or 0)
    finally:
        conn.close()


def chats_today(user_id, local_date=None):
    """Сколько сообщений человек написал Адаму в этот местный день."""
    local_date = local_date or local_date_of(user_id)
    start, end = day_bounds_utc(local_date, _tz(user_id))
    return _count(user_id, "SELECT COUNT(*) FROM ai_messages WHERE user_id=? AND role='user' AND created_at>=? AND created_at<?", (user_id, start, end))


def _today_marks(user_id, local_date):
    """[(название привычки, сколько раз отмечена сегодня)] — самые частые первыми."""
    start, end = day_bounds_utc(local_date, _tz(user_id))
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT COALESCE(h.title, '') AS title, COUNT(*) AS n FROM habit_completion_events e LEFT JOIN habits h ON h.id=e.habit_id "
            "WHERE e.user_id=? AND e.completed_at>=? AND e.completed_at<? GROUP BY e.habit_id ORDER BY n DESC, MAX(e.id) DESC", (user_id, start, end)).fetchall()
    finally:
        conn.close()
    return [(r["title"], int(r["n"])) for r in rows if r["title"]]


def _tasks_done_today(user_id, local_date):
    start, end = day_bounds_utc(local_date, _tz(user_id))
    plan_day = _count(user_id, "SELECT COUNT(*) FROM daily_plan_tasks t JOIN daily_plans p ON p.id=t.plan_id WHERE p.user_id=? AND p.plan_date=? AND t.completed=1",
                      (user_id, local_date))
    goal = _count(user_id, "SELECT COUNT(*) FROM daily_plans WHERE user_id=? AND plan_date=? AND main_goal_completed=1", (user_id, local_date))
    return plan_day + goal


def context(user_id, snap):
    """Всё, что нужно воротам сообщений кроме самого снимка триала: заходил ли сегодня, что показывали, сколько писал Адаму, как идёт серия после триала."""
    local_date = snap["local_date"]
    state = offer_state(user_id, local_date)
    winback = winback_state(user_id, snap)
    ctx = {
        "opened_today": opened_today(user_id, local_date), "sale_shown": state["sale_shown"], "dismissed": state["dismissed"],
        "chats_today": chats_today(user_id, local_date), "winback": winback,
    }
    if winback["allowed"]:
        ctx["wb_kind"] = winback_kind(user_id, _facts(user_id), winback["sent"])
    return ctx


# ---------------------------------------------------------------------------------------------------------------- предложение внутри приложения (§12)

def _app_offer_text(user_id, trigger, local_date):
    """Короткая мысль Адама, привязанная к настоящему действию. Факт подставляем только тогда, когда он был; цены и сроки — на экране подписки."""
    if trigger == "habit":
        marks = _today_marks(user_id, local_date)
        if not marks:
            return None
        title = marks[0][0]
        marked_days = _count(user_id, "SELECT COUNT(DISTINCT substr(completed_at,1,10)) FROM habit_completion_events WHERE user_id=?", (user_id,))
        if marked_days >= 2:
            return _pick(user_id, f"{local_date}:habit2", (
                f"«{title}» отмечена не впервые — ритм уже заметен. Я могу и дальше помогать его держать.",
                f"Вот уже не первый день «{title}» в деле. Я могу и дальше смотреть, что помогает держать этот ритм."))
        return _pick(user_id, f"{local_date}:habit1", (
            f"Первая отметка «{title}» есть — с неё и начинается ритм. Я могу помогать его держать.",
            f"«{title}» сегодня в деле. Я могу и дальше смотреть, что помогает держать этот ритм."))
    if trigger == "plan":
        done = _tasks_done_today(user_id, local_date)
        if done <= 0:
            return None
        return _pick(user_id, f"{local_date}:plan", (
            f"План на сегодня уже сдвинулся: выполнено — {done}. Я могу и дальше помогать разбирать день.",
            f"В плане есть выполненное: {done}. Я могу и дальше помогать выбирать главное."))
    if trigger == "adam":
        if chats_today(user_id, local_date) < 2:
            return None
        return _pick(user_id, f"{local_date}:adam", (
            "Разговор получился. Я могу продолжать его и дальше — с учётом того, что ты делаешь.",
            "Хороший разговор. Я могу и дальше отвечать с учётом твоих действий."))
    return None


def app_offer(user_id, trigger, now_utc=None):
    """Спокойное предложение подписки внутри приложения (ТЗ §12): только во второй день пробного периода, только после настоящего действия, не больше одного в день
    и не после «Не сейчас». Показ записывается сразу — внешнее приглашение в этот день уже не уйдёт. None — предлагать нечего."""
    from . import trial as trial_db
    if trigger not in PLAN_TRIGGERS:
        return None
    snap = trial_db.snapshot(user_id, now_utc)
    if not snap or not snap["scenario"] or snap["phase"] != "day2" or snap["paid"]:
        return None
    local_date = snap["local_date"]
    state = offer_state(user_id, local_date)
    if state["sale_shown"] or state["dismissed"]:
        return None
    text = _app_offer_text(user_id, trigger, local_date)
    if not text:
        return None
    key = f"d2_app_{trigger}"
    record(user_id, "app", key, "shown", local_date)
    return {"key": key, "text": text, "terms": "Условия подписки", "later": "Не сейчас"}


def mark_offer_event(user_id, place, key, event):
    """Нажатие или отказ: event — clicked | dismissed."""
    if event not in ("clicked", "dismissed") or place not in ("app", "chat", "push"):
        return False
    record(user_id, place, str(key or "")[:40], event)
    return True


# ---------------------------------------------------------------------------------------------------------------- умное приглашение к разговору (§15)

def _facts(user_id, now_utc=None):
    from .trial_summary import collect
    return collect(user_id, now_utc) or {}


def invite_question(user_id, snap, facts=None):
    """Вопрос Адама по данным человека. Только привычки — про привычки; ещё и план — учитываем задачи; только план — не выдумываем отметки; данных почти нет — помогаем
    выбрать первый шаг."""
    facts = facts if facts is not None else _facts(user_id)
    name = (snap.get("name") or "").strip()
    hello = f"{name}, " if name else ""
    tag = f"{snap['local_date']}:invite"
    marks, tasks, goals = facts.get("marks_total", 0), facts.get("tasks_added", 0), facts.get("goal_days", 0)
    top = (facts.get("top_habits") or [None])[0]
    untouched = (facts.get("untouched") or [None])[0]
    if marks and (tasks or goals):
        text = _pick(user_id, tag + ":both", (
            f"{hello}за эти дни у тебя есть и отметки привычек, и записанные задачи. Что из этого даётся легче — привычки или план дня, и почему?",
            f"{hello}вижу в твоих днях и привычки, и план. Какое дело сегодня самое важное — и с чего удобнее начать?"))
    elif marks:
        habit = f" — особенно «{top['title']}»" if top else ""
        text = _pick(user_id, tag + ":habits", (
            f"{hello}за эти дни у тебя уже есть отметки привычек{habit}. Что помогает возвращаться к ним, а что мешает?",
            f"{hello}привычки пошли{habit}. Когда в течение дня тебе проще всего за них браться?"))
    elif tasks or goals:
        text = _pick(user_id, tag + ":plan", (
            f"{hello}в плане дня у тебя уже есть записи. Какая задача самая важная на сегодня, и что поможет её сдвинуть?",
            f"{hello}ты уже работаешь с планом дня. Какую одну задачу стоит довести до конца в первую очередь?"))
    else:
        habit = f" Например, «{untouched}» — с чего начать с ней?" if untouched else ""
        text = _pick(user_id, tag + ":none", (
            f"{hello}пока данных мало, и это нормально. Давай выберем один посильный шаг на сегодня: что для тебя сейчас важнее всего?{habit}",
            f"{hello}давай начнём с малого: какое одно действие сегодня сделает день лучше?{habit}"))
    return text


def open_invite(user_id, now_utc=None):
    """Первый вопрос Адама по приглашению (/coach?intro=trial): создаётся один раз в день и записывается в историю; повторное открытие возвращает тот же вопрос, а не
    генерирует новый (ТЗ §16). После оплаты и вне сценария приглашения нет — чат обычный. Возвращает {"id", "message", "created_at", "new"} или None."""
    from . import trial as trial_db
    from .ai import add_ai_message
    snap = trial_db.snapshot(user_id, now_utc)
    if not snap or not snap["scenario"] or snap["paid"] or snap["phase"] not in ("day2", "day3"):
        return None
    local_date = snap["local_date"]
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT key FROM trial_offers WHERE user_id=? AND local_date=? AND place='chat' AND key LIKE 'invite:%' AND event='shown' ORDER BY id DESC LIMIT 1",
            (user_id, local_date)).fetchone()
        if existing:
            message_id = int(str(existing["key"]).split(":", 1)[1])
            row = conn.execute("SELECT id, message, created_at FROM ai_messages WHERE id=? AND user_id=?", (message_id, user_id)).fetchone()
            if row:
                return {"id": row["id"], "message": row["message"], "created_at": row["created_at"], "new": False}
    finally:
        conn.close()
    text = invite_question(user_id, snap)
    message_id = add_ai_message(user_id, "assistant", text)
    record(user_id, "chat", f"invite:{message_id}", "shown", local_date)
    conn = connect()
    try:
        row = conn.execute("SELECT created_at FROM ai_messages WHERE id=?", (message_id,)).fetchone()
    finally:
        conn.close()
    return {"id": message_id, "message": text, "created_at": row["created_at"] if row else None, "new": True}


def is_substantive_answer(text):
    """Ответ на вопрос Адама, а не другой вопрос: не короткая отмашка и без «?» (если человек спросил своё, сначала отвечаем на него и ничего не предлагаем)."""
    cleaned = " ".join(str(text or "").split())
    return len(cleaned) >= 20 and len(cleaned.split()) >= 4 and "?" not in cleaned


def chat_offer(user_id, message_text, now_utc=None):
    """Предложение под ответом Адама в чате: только на ответ человека на вопрос-приглашение (первое его сообщение после него), раз в день и если в этот день предложение
    ещё не показывали. Вместо продажи — вопрос «Показать короткую сводку?» (Да / Не сейчас); условия подписки идут отдельной тихой кнопкой."""
    from . import trial as trial_db
    if not is_substantive_answer(message_text):
        return None
    snap = trial_db.snapshot(user_id, now_utc)
    if not snap or not snap["scenario"] or snap["paid"] or snap["phase"] not in ("day2", "day3"):
        return None
    local_date = snap["local_date"]
    state = offer_state(user_id, local_date)
    if state["sale_shown"] or state["dismissed"]:
        return None
    conn = connect()
    try:
        invite = conn.execute(
            "SELECT key FROM trial_offers WHERE user_id=? AND local_date=? AND place='chat' AND key LIKE 'invite:%' AND event='shown' ORDER BY id DESC LIMIT 1",
            (user_id, local_date)).fetchone()
        if not invite:
            return None
        invite_id = int(str(invite["key"]).split(":", 1)[1])
        answered = conn.execute("SELECT 1 FROM ai_messages WHERE user_id=? AND role='user' AND id>? LIMIT 1", (user_id, invite_id)).fetchone()
    finally:
        conn.close()
    if answered:
        return None
    record(user_id, "chat", "summary_ask", "shown", local_date)
    return {"key": "summary_ask", "text": "Показать короткую сводку?", "yes": "Да", "later": "Не сейчас", "terms": "Условия подписки"}


def chat_summary(user_id, answer):
    """Ответ на «Показать короткую сводку?»: answer — yes | later. «Да» кладёт настоящую сводку в чат сообщением Адама и в историю."""
    from .ai import add_ai_message
    from .trial_summary import build_summary
    if answer == "later":
        record(user_id, "chat", "summary_ask", "dismissed")
        return {"ok": True}
    if answer != "yes":
        return {"error": "invalid_answer"}
    summary = build_summary(user_id)
    if summary is None:
        return {"error": "not_found"}
    record(user_id, "chat", "summary_ask", "clicked")
    parts = [summary["title"], *summary["lines"]]
    if summary["note"]:
        parts.append(summary["note"])
    text = "\n\n".join(parts)
    message_id = add_ai_message(user_id, "assistant", text)
    conn = connect()
    try:
        row = conn.execute("SELECT created_at FROM ai_messages WHERE id=?", (message_id,)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "message": {"id": message_id, "message": text, "created_at": row["created_at"] if row else None}, "terms": "Условия подписки",
            "enough": summary["enough"]}


# ---------------------------------------------------------------------------------------------------------------- редкие сообщения после триала (§18)

def winback_sent(user_id):
    """[(ключ, момент отправки)] редких сообщений, которые уже ушли человеку, от старых к новым."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT category, sent_at FROM notification_log WHERE user_id=? AND category LIKE ? ORDER BY id", (user_id, f"trial:{WINBACK_PREFIX}%")).fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        try:
            moment = datetime.strptime(str(row["sent_at"])[:19], FMT)
        except ValueError:
            continue
        result.append((row["category"].split(":", 1)[1], moment))
    return result


def _opened_after(user_id, moment):
    return _count(user_id, "SELECT COUNT(*) FROM user_events WHERE user_id=? AND name='session_start' AND at>?", (user_id, moment.strftime(FMT))) > 0


def winback_state(user_id, snap, now_utc=None):
    """Можно ли сейчас прислать редкое сообщение после триала. {"allowed", "reason", "sent", "ignored", "next_at"}.
    Правила: только после дополнительного окна и пока нет оплаты; не раньше первой паузы; между сообщениями растущие паузы; если человек не заходил после двух сообщений подряд
    — паузы вдвое длиннее, после трёх серия останавливается; отказ от сообщений и потолок в пять сообщений."""
    now = (now_utc or datetime.now(UTC))
    now = now.astimezone(UTC).replace(tzinfo=None) if now.tzinfo else now
    sent = winback_sent(user_id)
    result = {"allowed": False, "reason": "", "sent": len(sent), "ignored": 0, "next_at": None}
    if not snap or not snap.get("scenario") or snap.get("phase") != "expired" or snap.get("paid"):
        result["reason"] = "not_expired"
        return result
    conn = connect()
    try:
        row = conn.execute("SELECT winback_optout FROM users WHERE telegram_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    if row and row["winback_optout"]:
        result["reason"] = "optout"
        return result
    if len(sent) >= WINBACK_MAX_MESSAGES:
        result["reason"] = "max"
        return result
    ignored = 0
    for _key, moment in reversed(sent):
        if _opened_after(user_id, moment):
            break
        ignored += 1
    result["ignored"] = ignored
    if ignored >= WINBACK_STOP_AFTER:
        result["reason"] = "ignored"
        return result
    if sent:
        gap = WINBACK_GAPS_DAYS[min(len(sent) - 1, len(WINBACK_GAPS_DAYS) - 1)]
        if ignored >= WINBACK_SLOW_AFTER:
            gap *= 2
        next_at = sent[-1][1] + timedelta(days=gap)
    else:
        from .access_state import _parse
        grace_end = _parse(snap.get("grace_ends_at"))
        if grace_end is None:
            result["reason"] = "no_window"
            return result
        next_at = grace_end + timedelta(days=WINBACK_FIRST_AFTER_DAYS)
    result["next_at"] = next_at.strftime(FMT)
    if now < next_at:
        result["reason"] = "wait"
        return result
    result["allowed"] = True
    return result


def winback_kind(user_id, facts, sent_count):
    """Какое по счёту редкое сообщение: полезный вопрос → вывод по прошлым действиям → продолжить начатое; без данных вывод и «продолжить» не выдумываем."""
    order = ("question", "insight", "continue")
    for step in range(len(order)):
        kind = order[(sent_count + step) % len(order)]
        if kind == "insight" and not facts.get("marks_total"):
            continue
        if kind == "continue" and not (facts.get("habits_total") or facts.get("tasks_added")):
            continue
        return kind
    return "question"


def winback_text(user_id, facts, sent_count, kind=None):
    """Текст редкого сообщения: повод вернуться, без текста про оплату (условия — кнопкой), без личных подробностей на экране блокировки."""
    kind = kind or winback_kind(user_id, facts, sent_count)
    tag = f"wb:{sent_count}:{kind}"
    if kind == "insight":
        n = facts.get("marks_total", 0)
        days = facts.get("days_with_marks", 0)
        return _pick(user_id, tag, (
            f"За те дни, что мы были вместе, у тебя набралось отметок привычек: {n}. Это уже опора, на которую можно вернуться.",
            f"Я помню твои первые дни: отметок было {n}, дней с отметками — {days}. Из этого можно собрать новый, более посильный ритм."))
    if kind == "continue":
        return _pick(user_id, tag, (
            "Начатое никуда не делось: твои привычки и записи ждут. Можно вернуться с одного маленького шага.",
            "Твоя история сохранена. Если захочется продолжить, начни с самого лёгкого дела — остальное подтянется."))
    return _pick(user_id, tag, (
        "Один вопрос на сегодня: какое маленькое дело стоило бы сделать регулярным?",
        "Что в последнее время давалось тебе легче всего — и можно ли сделать этого чуть больше?",
        "Если выбрать одно дело на ближайшую неделю, которое стоит повторять каждый день, что бы это было?"))


def set_winback_optout(user_id, optout=True):
    conn = connect()
    try:
        conn.execute("UPDATE users SET winback_optout=? WHERE telegram_id=?", (1 if optout else 0, user_id))
        conn.commit()
    finally:
        conn.close()
