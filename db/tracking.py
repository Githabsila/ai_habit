"""
Путь человека в приложении: события, сессии, «после какого действия вышел», воронка и пропуски.

Что где живёт:
  • user_events — значимые шаги (регистрация, анкета, онбординг, «пропустить», первая привычка/задача, сообщение Адаму);
    пишут и сервер (track_server — из мест, где это случилось), и приложение (tracker.js → /api/track → ingest);
  • app_sessions — одна запись на сессию: сколько человек реально был в приложении, сколько действий сделал, последние 10
    действий и экран — по ним видно, ПОСЛЕ ЧЕГО он вышел;
  • всё остальное (когда добавил первую привычку, когда написал Адаму и ЧТО) берётся из таблиц, которые уже есть
    (habits, daily_plans, ai_messages…), поэтому путь виден и у тех, кто пришёл до появления этого учёта.

Ничего из этого не должно ломать основной сценарий: любая запись обёрнута в try/except.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from .core import connect

EVENT_LABELS = {
    "registered": ("🆕", "Зарегистрировался"),
    "survey_started": ("📝", "Начал анкету"),
    "survey_step": ("📝", "Ответил на вопрос анкеты"),
    "survey_completed": ("✅", "Закончил анкету"),
    "access_approved": ("🔓", "Открыли доступ"),
    "session_start": ("📱", "Открыл приложение"),
    "screen": ("🧭", "Открыл раздел"),
    "onb_streak_shown": ("🔥", "Увидел «Ударный режим»"),
    "onb_streak_ok": ("🔥", "Нажал «Я в игре»"),
    "quiz_start": ("❓", "Начал стартовый квиз"),
    "quiz_step": ("❓", "Шаг стартового квиза"),
    "quiz_done": ("❓", "Закончил стартовый квиз"),
    "tour_start": ("👋", "Увидел вводный тур"),
    "tour_step": ("👋", "Шаг вводного тура"),
    "tour_skip": ("⏭", "Пропустил вводный тур"),
    "tour_done": ("👋", "Закончил вводный тур"),
    "handle_intro_shown": ("🏷", "Увидел экран с @ником"),
    "handle_intro_done": ("🏷", "Продолжил на экране с @ником"),
    "hint_shown": ("💡", "Подсказка онбординга"),
    "hint_next": ("💡", "Следующая подсказка"),
    "hint_skip": ("⏭", "Пропустил подсказки"),
    "habit_form_open": ("✏️", "Открыл форму привычки"),
    "habit_added": ("➕", "Добавил привычку"),
    "habit_deleted": ("🗑", "Удалил привычку"),
    "habit_moved": ("↕️", "Сдвинул привычку в списке"),
    "habit_edit_open": ("✏️", "Открыл «Изменить привычку»"),
    "habit_edit_save": ("💾", "Изменил привычку"),
    "habit_actions": ("🔘", "Кнопки привычки: открыл или спрятал (шаг: open, arrow, double, outside)"),
    "habit_delete_armed": ("🛑", "Нажал ✕ у привычки — ждёт подтверждения «Удалить?»"),
    "abuse_flagged": ("🛑", "Подозрение на накрутку наград"),
    "habit_completed": ("✅", "Отметил привычку"),
    "habit_undo_open": ("↩️", "Двойной тап по галочке: спросили «Убрать отметку о выполнении?»"),
    "habit_undone": ("↩️", "Убрал отметку о выполнении"),
    "habit_undo_cancel": ("✋", "Оставил отметку: ответил «Оставить»"),
    "habit_undo_denied": ("🚫", "Отметку убрать не вышло (шаг: код причины)"),
    "main_goal_set": ("🎯", "Записал главную задачу"),
    "main_goal_done": ("🏁", "Выполнил главную задачу"),
    "task_added": ("🗒", "Добавил задачу"),
    "task_done": ("☑️", "Выполнил задачу"),
    "ai_message": ("💬", "Написал Адаму"),
    "friend_added": ("🤝", "Добавил друга"),
    "pair_invite": ("🤝", "Позвал в парное задание"),
    "pair_left_early": ("🚪", "Вышел из парного задания (напарник пропал)"),
    "chest_opened": ("🎁", "Открыл сундук (квеста дня, месяца или парного задания)"),
    "quest_done": ("🗺️", "Выполнил квест дня (награда выдана сама)"),
    "quests_window_auto": ("🗺️", "Окно «Квесты дня» открылось само после действия"),
    "quests_window_open": ("🗺️", "Открыл «Квесты дня» кнопкой"),
    "quests_go": ("🗺️", "Нажал на невыполненный квест — перешёл к его шагу"),
    "inbox_open": ("🔔", "Открыл «Уведомления»"),
    "inbox_tab": ("🔔", "Переключил раздел в «Уведомлениях»"),
    "inbox_item": ("🔔", "Нажал на уведомление"),
    "trial_sheet_open": ("⏳", "Открыл «Мой пробный период»"),
    "trial_pay_tap": ("⭐", "Нажал «Оформить» (Pro)"),
    "trial_paywall_shown": ("🔒", "Увидел «Пробный период закончился»"),
    "trial_paid": ("✅", "Оплатил Pro в приложении"),
    "subscription_invoice": ("🧾", "Выставлен счёт на Pro"),
    "subscription_paid": ("💳", "Оплатил Pro (Stars)"),
    "freeze_gift_sent": ("❄️", "Подарил другу заморозку серии"),
    "freeze_help_shown": ("❄️", "Увидел «Надо помочь другу» (у друга кончились заморозки)"),
    "freeze_help_open": ("❄️", "Нажал серую заморозку у друга"),
    "rescue_help_shown": ("💛", "Увидел «Серию друга ещё можно спасти» (при входе)"),
    "rescue_help_open": ("💛", "Нажал значок погасшей серии у друга"),
    "rescue_sent": ("💛", "Вернул другу погасшую серию (бесплатно)"),
    "adam_intro_shown": ("🧠", "Увидел знакомство с Адамом"),
    "adam_intro_step": ("🧠", "Вопрос знакомства с Адамом"),
    "adam_intro_done": ("🧠", "Прошёл знакомство с Адамом (метод планирования выбран)"),
    "adam_intro_skip": ("🧠", "Отложил знакомство с Адамом («Позже»)"),
    "adam_intro_habit": ("🌱", "Добавил привычку из знакомства с Адамом"),
    "adam_intro_chat": ("💬", "После знакомства пошёл говорить с Адамом о планах"),
    "username_changed": ("✏️", "Сменил @ник в Telegram"),
    "referral_joined": ("👋", "Пришёл по приглашению друга"),
    "referral_rejected": ("🚫", "Переход по ссылке не засчитан"),
    "referral_paid": ("🎁", "Получил награду за приглашение"),
    "referral_activated": ("✅", "Приглашённый друг отметил привычку"),
    "referral_revoked": ("↩️", "Админ отозвал награду за приглашение"),
    "referral_mail": ("📨", "Получил «позови друга» в боте"),
    "purchase": ("🛍", "Покупка в магазине"),
}
SKIP_EVENTS = ("tour_skip", "hint_skip")

# Подписи для админки: что нажал человек (id кнопки → по-русски) и где он был (вкладка или окно). Всё, чего здесь нет, показывается как есть.
UI_LABELS = {
    "addHabitTrigger": "Добавить привычку", "newHabitPriorityBtn": "Приоритет привычки", "newHabitAdvancedToggle": "Расширенные настройки привычки",
    "saveMainGoalBtn": "Сохранить главную задачу", "addPlanTaskTrigger": "Добавить задачу",
    "aiCoachBtn": "Открыть чат с Адамом", "progressAiBtn": "ИИ-разбор прогресса", "openShopBtn": "Магазин", "openStatsBtn": "Статистика",
    "openGiftsBtn": "Подарки", "dailyQuestsBtn": "Квесты дня", "monthCardOpen": "Задания месяца", "openSettingsBtn": "Настройки",
    "appTourSkip": "Пропустить вводный тур", "appTourNext": "Далее (вводный тур)", "appTourBack": "Назад (вводный тур)",
    "startQuizContinue": "Далее (стартовый квиз)", "startQuizBack": "Назад (стартовый квиз)",
    "productOnboardingHintSkip": "Пропустить подсказки", "streakOnboardingContinue": "Я в игре (ударный режим)",
    "handleIntroContinue": "Продолжить на экране @ника", "bootRetryBtn": "Повторить загрузку", "feedbackSend": "Отправить отзыв",
    "openFeedbackBtn": "Написать отзыв", "adminPanelBtn": "Админ-панель", "heroWidgetPortrait": "Портрет Адама", "pairGuideClose": "Понятно (парное задание)",
    "replayOnboardingBtn": "Повторить онбординг", "deleteAccountBtn": "Удалить аккаунт", "resetProgressBtn": "Сбросить прогресс",
    "remindersToggle": "Переключатель напоминаний", "userHandleSaveBtn": "Сохранить @ник", "publicProfileToggle": "Публичный профиль",
    "selfRewardActionBtn": "Вознаградить себя", "freezeBuyBtn": "Купить заморозку", "streakCelebrationContinue": "Продолжить (праздник серии)",
}
SCREEN_LABELS = {
    "home": "Главная", "calendar": "Календарь", "rating": "Рейтинг", "profile": "Профиль", "coach": "Чат с Адамом", "ai": "Чат с Адамом",
    "startQuiz": "Стартовый квиз", "appTour": "Вводный тур", "handleIntro": "Экран @ника", "streakOnboarding": "Ударный режим",
    "shop": "Магазин", "settings": "Настройки", "stats": "Статистика", "dailyQuests": "Квесты дня", "pairGuide": "Парное задание: инструкция",
    "userProfile": "Профиль игрока", "statusLadder": "Статусы серии", "streakCelebration": "Праздник серии", "levelup": "Новый уровень", "evolution": "Эволюция",
    "doubleBonus": "Двойные очки", "archetypeQuiz": "Тест архетипа", "avatarEdit": "Аватар", "identity": "Имя и ник", "adminGift": "Подарок от админа",
}


def ui_label(label):
    return UI_LABELS.get(label, label)


def screen_label(screen):
    return SCREEN_LABELS.get(screen, screen)
# Шаги онбординга по порядку — для карточки «Онбординг по шагам» (/analytics → Воронка) и карточки человека.
# Ключи — то, что приложение шлёт в события: quiz_step (ключ шага квиза из app.js::START_QUIZ_STEPS_ALL) и hint_shown (номер подсказки).
# Новый шаг в приложении попадёт в учёт сам и покажется под своим ключом с пометкой «без названия»; тест test_onboarding_tracking
# требует дать ему подпись здесь — как карта функций требует поставить новую кнопку в функцию.
QUIZ_STEP_LABELS = {
    "welcome": "Приветствие", "age_range": "Возраст", "goal": "Зачем пришёл",
    "archetype_q0": "Тест архетипа: вопрос 1", "archetype_q1": "Тест архетипа: вопрос 2", "archetype_q2": "Тест архетипа: вопрос 3",
    "archetype_q3": "Тест архетипа: вопрос 4", "archetype_result": "Результат архетипа",
    "testimonials": "Отзывы (шаг убран 14.10)", "firststeps": "Первые шаги", "referral": "Позови друзей", "paywall": "Окно цен (только триал)",
}
QUIZ_OPTIONAL_STEPS = ("testimonials", "paywall")          # без данных в списке не показываем
TOUR_LABELS = {"1": "Это ADAM", "2": "Формируй привычки", "3": "Погнали"}
HINT_LABELS = {
    "1": "Добавить первую привычку", "2": "Список привычек", "3": "Главная задача дня", "4": "Второстепенные задачи", "5": "Календарь",
    "6": "Рейтинг", "7": "Профиль", "8": "ADAM Store", "9": "Прогресс и достижения", "10": "Настройки", "11": "Чат с Адамом",
}
# Как человек открыл приложение (?from= в адресе кнопки из бота; у меню Telegram и ссылок параметра нет).
ENTRY_LABELS = {"": "Меню Telegram или ссылка", "bot_btn": "Кнопка «Открыть ADAM» в сообщении бота"}


def quiz_step_label(step):
    return QUIZ_STEP_LABELS.get(step, step) if step else None


def _clean_entry(value):
    text = str(value or "")
    return text if re.match(r"^[a-z0-9_]{1,20}$", text) else None
NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,40}$")
MAX_EVENTS_PER_REQUEST = 40
MAX_EVENTS_PER_MINUTE = 200
MAX_META_CHARS = 500
MAX_LAST_ACTIONS = 10


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _parse(value):
    if not value:
        return None
    text = str(value).replace("T", " ")[:19]
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _short(value, limit):
    return " ".join(str(value or "").split())[:limit] or None


def _admin_ids():
    try:
        from config import ADMIN_IDS
        return [int(i) for i in ADMIN_IDS]
    except Exception:
        return []


# ---------------------------------------------------------------------------------------------------------------- запись

def track_server(user_id, name, step=None, meta=None, surface="server", screen=None, session_id=None, at=None):
    """Одно событие с сервера. Никогда не бросает исключение: учёт не должен мешать тому, что делает человек."""
    try:
        if not user_id or not NAME_RE.match(str(name or "")):
            return False
        payload = None
        if meta:
            payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))[:MAX_META_CHARS]
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO user_events(user_id, at, name, step, screen, meta, session_id, surface) VALUES (?,?,?,?,?,?,?,?)",
                (int(user_id), _fmt(at or _now()), name, _short(step, 40), _short(screen, 40), payload, _short(session_id, 40), surface),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def _clean_sid(value):
    text = str(value or "")
    return text if re.match(r"^[A-Za-z0-9_-]{6,40}$", text) else None


def _clean_actions(raw):
    out = []
    for item in (raw or [])[-MAX_LAST_ACTIONS:]:
        if not isinstance(item, dict):
            continue
        label = _short(item.get("l"), 40)
        if label:
            out.append({"l": label, "s": _short(item.get("s"), 40), "t": int(item.get("t") or 0) if str(item.get("t") or "0").lstrip("-").isdigit() else 0})
    return out


def _clean_version(value):
    text = str(value or "")
    return text if re.match(r"^[A-Za-z0-9._-]{1,24}$", text) else None


def ingest(user_id, session, events, client_now=None, usage=None, version=None):
    """Пакет от приложения: снимок сессии и события. Возвращает сколько событий принято. Ошибки не пробрасываем.
    usage — приращения кликов и экранов за пакет ({"clicks": [[подпись, экран, раз]], "screens": {экран: {"v":.., "s":..}}}),
    version — версия приложения: попадает в сессию и в каталог «что впервые увидели» (db/usage.py)."""
    accepted = 0
    version = _clean_version(version)
    try:
        now = _now()
        conn = connect()
        try:
            sid = _clean_sid((session or {}).get("sid"))
            if sid:
                started = now
                try:
                    started_ms, now_ms = int(session.get("started")), int(client_now)
                    started = now - timedelta(seconds=max(0, min((now_ms - started_ms) / 1000, 24 * 3600)))
                except (TypeError, ValueError):
                    pass
                active = max(0, min(int(session.get("active") or 0), 24 * 3600))
                actions = max(0, min(int(session.get("actions") or 0), 100000))
                last = _clean_actions(session.get("last"))
                last_action = last[-1]["l"] if last else None
                entry = _clean_entry(session.get("entry"))
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO app_sessions(id, user_id, started_at, last_at, platform, tz_offset, version) VALUES (?,?,?,?,?,?,?)",
                    (sid, user_id, _fmt(started), _fmt(now), _short(session.get("platform"), 20), _int_or_none(session.get("tz")), version),
                ).rowcount
                conn.execute(
                    "UPDATE app_sessions SET last_at=?, active_seconds=MAX(active_seconds,?), actions=MAX(actions,?), "
                    "last_action=COALESCE(?, last_action), last_screen=COALESCE(?, last_screen), "
                    "last_actions=CASE WHEN ? IS NULL THEN last_actions ELSE ? END, ended=?, ended_at=CASE WHEN ?=1 THEN ? ELSE NULL END "
                    "WHERE id=? AND user_id=?",
                    (_fmt(now), active, actions, last_action, _short(session.get("screen"), 40),
                     json.dumps(last, ensure_ascii=False) if last else None, json.dumps(last, ensure_ascii=False) if last else None,
                     1 if session.get("hidden") else 0, 1 if session.get("hidden") else 0, _fmt(now), sid, user_id),
                )
                if inserted:
                    conn.execute(
                        "INSERT INTO user_events(user_id, at, name, session_id, surface, meta) VALUES (?,?,?,?,?,?)",
                        (user_id, _fmt(started), "session_start", sid, "app",
                         json.dumps({"platform": _short(session.get("platform"), 20), **({"entry": entry} if entry else {})}, ensure_ascii=False)),
                    )
            recent = conn.execute(
                "SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND at>=?", (user_id, _fmt(now - timedelta(minutes=1)))
            ).fetchone()["n"]
            budget = max(0, MAX_EVENTS_PER_MINUTE - int(recent))
            for event in (events or [])[:min(MAX_EVENTS_PER_REQUEST, budget)]:
                if not isinstance(event, dict) or not NAME_RE.match(str(event.get("n") or "")):
                    continue
                at = now
                try:
                    at = now - timedelta(seconds=max(0, min((int(client_now) - int(event["t"])) / 1000, 3600)))
                except (KeyError, TypeError, ValueError):
                    pass
                meta = event.get("m")
                payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))[:MAX_META_CHARS] if isinstance(meta, (dict, list)) and meta else None
                conn.execute(
                    "INSERT INTO user_events(user_id, at, name, step, screen, meta, session_id, surface) VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, _fmt(at), event["n"], _short(event.get("s"), 40), _short(event.get("sc"), 40), payload, sid, "app"),
                )
                accepted += 1
            if isinstance(usage, dict) or version:
                from .usage import record_ui
                usage = usage if isinstance(usage, dict) else {}
                try:
                    record_ui(conn, user_id, clicks=usage.get("clicks") if isinstance(usage.get("clicks"), list) else None,
                              screens=usage.get("screens") if isinstance(usage.get("screens"), dict) else None, version=version)
                except Exception:
                    pass        # кривой пакет счётчиков не должен отнимать у человека принятые события
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return accepted
    return accepted


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------------------- чтение: человек

def _one(conn, query, params=()):
    row = conn.execute(query, params).fetchone()
    return row[0] if row is not None else None


def _minutes(created, moment):
    return int((moment - created).total_seconds() // 60) if created and moment else None


def _earliest(*values):
    parsed = [_parse(v) for v in values if v]
    parsed = [p for p in parsed if p]
    return min(parsed) if parsed else None


def sessions(user_id, limit=10):
    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM app_sessions WHERE user_id=? ORDER BY started_at DESC LIMIT ?", (user_id, int(limit))).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            last = json.loads(r["last_actions"]) if r["last_actions"] else []
        except ValueError:
            last = []
        started, last_at = _parse(r["started_at"]), _parse(r["last_at"])
        out.append({
            "id": r["id"], "started_at": r["started_at"], "last_at": r["last_at"],
            "span_seconds": int((last_at - started).total_seconds()) if started and last_at else 0,
            "active_seconds": int(r["active_seconds"] or 0), "actions": int(r["actions"] or 0),
            "last_action": ui_label(r["last_action"]) if r["last_action"] else None,
            "last_screen": screen_label(r["last_screen"]) if r["last_screen"] else None,
            "last_actions": [{"l": ui_label(a["l"]), "s": screen_label(a["s"]) if a.get("s") else None, "t": a.get("t", 0)} for a in last],
            "ended": bool(r["ended"]), "platform": r["platform"],
        })
    return out


def journey(user_id):
    """Сводка пути человека: когда что сделал (и через сколько минут после регистрации), онбординг, пропуски, время в приложении,
    после чего выходит. Часть берётся из событий, часть — из обычных таблиц (habits, daily_plans, ai_messages …)."""
    conn = connect()
    try:
        user = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if user is None:
            return None
        created = _parse(user["created_at"])
        keys = user.keys()

        def event_first(name, step=None):
            q, p = "SELECT MIN(at) FROM user_events WHERE user_id=? AND name=?", [user_id, name]
            if step:
                q += " AND step=?"
                p.append(step)
            return _one(conn, q, p)

        first_open = _earliest(_one(conn, "SELECT MIN(started_at) FROM app_sessions WHERE user_id=?", (user_id,)),
                               _one(conn, "SELECT MIN(at) FROM app_opens WHERE user_id=?", (user_id,)))
        first_habit = _earliest(_one(conn, "SELECT MIN(created_at) FROM habits WHERE user_id=?", (user_id,)), event_first("habit_added"))
        habit_row = conn.execute("SELECT title FROM habits WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
        first_done = _earliest(user["first_habit_completed_at"] if "first_habit_completed_at" in keys else None,
                               _one(conn, "SELECT MIN(completed_at) FROM habit_completion_events WHERE user_id=?", (user_id,)))
        first_main = _earliest(event_first("main_goal_set"),
                               _one(conn, "SELECT MIN(created_at) FROM daily_plans WHERE user_id=? AND COALESCE(main_goal,'')!=''", (user_id,)))
        first_task = _earliest(event_first("task_added"),
                               _one(conn, "SELECT MIN(t.created_at) FROM daily_plan_tasks t JOIN daily_plans p ON p.id=t.plan_id WHERE p.user_id=?", (user_id,)))
        first_ai = conn.execute("SELECT created_at, message FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id LIMIT 1", (user_id,)).fetchone()
        ai_count = _one(conn, "SELECT COUNT(*) FROM ai_messages WHERE user_id=? AND role='user'", (user_id,)) or 0
        first_friend = _one(conn, "SELECT MIN(created_at) FROM friendships WHERE user_id=?", (user_id,))
        paid = _earliest(user["subscription_first_payment_at"] if "subscription_first_payment_at" in keys else None,
                         _one(conn, "SELECT MIN(created_at) FROM stars_payments WHERE user_id=?", (user_id,)))
        survey_done = _earliest(user["survey_completed_at"] if "survey_completed_at" in keys else None, event_first("survey_completed"))
        approved = _earliest(event_first("access_approved"))

        def milestone(key, label, moment, extra=None):
            return {"key": key, "label": label, "at": _fmt(moment) if moment else None, "minutes": _minutes(created, moment),
                    "minutes_since_open": _minutes(first_open, moment) if first_open and moment and key != "opened" else None, "extra": extra}

        milestones = [
            milestone("registered", "Регистрация", created),
            milestone("survey", "Закончил анкету", survey_done),
            milestone("approved", "Открыли доступ", approved if approved else (survey_done if (user["access_status"] if "access_status" in keys else None) == "approved" else None)),
            milestone("opened", "Первый заход в приложение", first_open),
            milestone("habit", "Первая привычка", first_habit, habit_row["title"] if habit_row else None),
            milestone("habit_done", "Первая отметка привычки", first_done),
            milestone("main_goal", "Главная задача", first_main),
            milestone("task", "Второстепенная задача", first_task),
            milestone("ai", "Первое сообщение Адаму", _parse(first_ai["created_at"]) if first_ai else None, _short(first_ai["message"], 200) if first_ai else None),
            milestone("friend", "Первый друг", _parse(first_friend)),
            milestone("paid", "Первая оплата", paid),
        ]

        # онбординг: до какого шага дошёл и где пропускал
        quiz_steps = [r["step"] for r in conn.execute(
            "SELECT step FROM user_events WHERE user_id=? AND name='quiz_step' AND step IS NOT NULL ORDER BY id", (user_id,)).fetchall()]
        tour_steps = [r["step"] for r in conn.execute(
            "SELECT step FROM user_events WHERE user_id=? AND name='tour_step' ORDER BY id", (user_id,)).fetchall()]
        skips = [{"name": r["name"], "step": r["step"], "at": r["at"]} for r in conn.execute(
            "SELECT name, step, at FROM user_events WHERE user_id=? AND name IN ('tour_skip','hint_skip') ORDER BY id", (user_id,)).fetchall()]
        onboarding = {
            "stage": int(user["onboarding_stage"] or 0) if "onboarding_stage" in keys else 0,
            "streak_seen": bool(event_first("onb_streak_shown")), "streak_ok": bool(event_first("onb_streak_ok")),
            "quiz_seen": bool(user["start_quiz_seen"]) if "start_quiz_seen" in keys else False,
            "quiz_last_step": quiz_steps[-1] if quiz_steps else None, "quiz_steps": len(set(quiz_steps)),
            "quiz_last_label": quiz_step_label(quiz_steps[-1]) if quiz_steps else None,
            "tour_seen": bool(user["app_tour_seen"]) if "app_tour_seen" in keys else False,
            "tour_last_step": tour_steps[-1] if tour_steps else None,
            "tour_last_label": TOUR_LABELS.get(tour_steps[-1]) if tour_steps else None,
            "skips": skips,
        }

        sess = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(active_seconds),0) AS active, COALESCE(MAX(started_at),'') AS last_start FROM app_sessions WHERE user_id=?",
            (user_id,)).fetchone()
        days_active = _one(conn, "SELECT COUNT(DISTINCT date(at)) FROM app_opens WHERE user_id=?", (user_id,)) or 0
        exits = [{"label": ui_label(r["last_action"]), "count": r["n"]} for r in conn.execute(
            "SELECT last_action, COUNT(*) AS n FROM app_sessions WHERE user_id=? AND last_action IS NOT NULL GROUP BY last_action ORDER BY n DESC LIMIT 5",
            (user_id,)).fetchall()]
        returns = {}
        if created:
            for key, delta in (("d1", 1), ("d7", 7)):
                later = _one(conn, "SELECT MIN(at) FROM app_opens WHERE user_id=? AND date(at)>=date(?, ?)", (user_id, _fmt(created), f"+{delta} days"))
                returns[key] = bool(later)
    finally:
        conn.close()

    last_sessions = sessions(user_id, limit=1)
    return {
        "user_id": user_id,
        "registered_at": _fmt(created) if created else None,
        "source": user["source_code"] if "source_code" in user.keys() else None,
        "milestones": milestones,
        "onboarding": onboarding,
        "sessions": {
            "count": int(sess["n"]), "active_seconds": int(sess["active"]), "days_active": int(days_active),
            "avg_active_seconds": int(sess["active"] / sess["n"]) if sess["n"] else 0,
            "last": last_sessions[0] if last_sessions else None, "top_exits": exits,
        },
        "ai_messages": int(ai_count),
        "returns": returns,
    }


def timeline(user_id, limit=60):
    """Лента действий человека, новые сверху: события, отметки привычек, сообщения Адаму (с текстом), сессии."""
    limit = max(1, min(int(limit), 300))
    items = []
    conn = connect()
    try:
        for r in conn.execute("SELECT at, name, step, screen, meta FROM user_events WHERE user_id=? AND name!='session_start' ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall():
            icon, label = EVENT_LABELS.get(r["name"], ("•", r["name"]))
            meta = None
            if r["meta"]:
                try:
                    meta = json.loads(r["meta"])
                except ValueError:
                    meta = None
            items.append({"at": r["at"], "kind": "event", "name": r["name"], "icon": icon, "label": label, "step": r["step"],
                          "screen": screen_label(r["screen"]) if r["screen"] else None, "meta": meta})
        for r in conn.execute(
            "SELECT e.completed_at AS at, h.title AS title FROM habit_completion_events e LEFT JOIN habits h ON h.id=e.habit_id "
            "WHERE e.user_id=? ORDER BY e.id DESC LIMIT ?", (user_id, limit)).fetchall():
            items.append({"at": r["at"], "kind": "habit", "name": "habit_completed", "icon": "✅", "label": "Отметил привычку", "step": None, "screen": None,
                          "meta": {"title": r["title"]} if r["title"] else None})
        for r in conn.execute("SELECT created_at AS at, message FROM ai_messages WHERE user_id=? AND role='user' ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall():
            items.append({"at": r["at"], "kind": "ai", "name": "ai_message", "icon": "💬", "label": "Написал Адаму", "step": None, "screen": None,
                          "meta": {"text": _short(r["message"], 220)}})
        for s in sessions(user_id, limit=min(limit, 30)):
            exit_text = f"вышел после «{s['last_action']}»" if s["last_action"] else "вышел"
            if s["last_screen"]:
                exit_text += f" · экран {s['last_screen']}"
            items.append({"at": s["started_at"], "kind": "session", "name": "session", "icon": "📱", "label": "Сессия", "step": None, "screen": s["last_screen"],
                          "meta": {"active_seconds": s["active_seconds"], "actions": s["actions"], "exit": exit_text}})
    finally:
        conn.close()
    items.sort(key=lambda x: x["at"] or "", reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------------------------------------------- чтение: все люди

FUNNEL = (
    ("registered", "Зарегистрировались"),
    ("survey", "Закончили анкету"),
    ("approved", "Получили доступ"),
    ("opened", "Открыли приложение"),
    ("habit", "Добавили привычку"),
    ("habit_done", "Отметили привычку"),
    ("main_goal", "Записали главную задачу"),
    ("task", "Добавили второстепенную задачу"),
    ("ai", "Написали Адаму"),
    ("d1", "Вернулись со следующего дня и позже"),
    ("d7", "Вернулись через неделю и позже"),
    ("friend", "Добавили друга"),
    ("paid", "Оплатили"),
)
CORE_PATH = ("registered", "survey", "approved", "opened", "habit", "habit_done")


def _cohort(days, source=None):
    since = _fmt(_now() - timedelta(days=max(1, min(int(days), 365))))
    admins = _admin_ids()
    where = "created_at>=? AND COALESCE(banned,0)=0 AND COALESCE(stats_excluded,0)=0"
    params = [since]
    if admins:
        where += f" AND telegram_id NOT IN ({','.join('?' for _ in admins)})"
        params += admins
    if source == "none":
        where += " AND (source_code IS NULL OR source_code='')"
    elif source:
        where += " AND source_code=?"
        params.append(source)
    return where, params


def _collect_marks(conn, where, params):
    """Кто из когорты (users по условию where) дошёл до каких шагов воронки и когда: (users, ids, marks[ключ][user_id] → время|None)."""
    users = {r["telegram_id"]: r for r in conn.execute(f"SELECT * FROM users WHERE {where}", params).fetchall()}
    ids = list(users)
    marks = {key: {} for key, _ in FUNNEL}
    if not ids:
        return users, ids, marks

    def grouped(query):
        return {r[0]: _parse(r[1]) for r in conn.execute(query.replace("{ids}", ",".join("?" for _ in ids)), ids).fetchall()}

    def earlier(key, found):
        for uid, moment in found.items():
            if moment and (not marks[key].get(uid) or moment < marks[key][uid]):
                marks[key][uid] = moment

    for uid, row in users.items():
        keys = row.keys()
        marks["registered"][uid] = _parse(row["created_at"])
        marks["survey"][uid] = _parse(row["survey_completed_at"]) if "survey_completed_at" in keys else None
        if (row["access_status"] if "access_status" in keys else None) == "approved":
            marks["approved"][uid] = marks["survey"][uid] or marks["registered"][uid]
        marks["paid"][uid] = _parse(row["subscription_first_payment_at"]) if "subscription_first_payment_at" in keys else None
        marks["habit_done"][uid] = _parse(row["first_habit_completed_at"]) if "first_habit_completed_at" in keys else None
    earlier("approved", grouped("SELECT user_id, MIN(at) FROM user_events WHERE user_id IN ({ids}) AND name='access_approved' GROUP BY user_id"))
    sessions_first = grouped("SELECT user_id, MIN(started_at) FROM app_sessions WHERE user_id IN ({ids}) GROUP BY user_id")
    opens_first = grouped("SELECT user_id, MIN(at) FROM app_opens WHERE user_id IN ({ids}) GROUP BY user_id")
    for uid in ids:
        marks["opened"][uid] = min([m for m in (sessions_first.get(uid), opens_first.get(uid)) if m], default=None)
    earlier("habit", grouped("SELECT user_id, MIN(created_at) FROM habits WHERE user_id IN ({ids}) GROUP BY user_id"))
    earlier("habit", grouped("SELECT user_id, MIN(at) FROM user_events WHERE user_id IN ({ids}) AND name='habit_added' GROUP BY user_id"))
    earlier("habit_done", grouped("SELECT user_id, MIN(completed_at) FROM habit_completion_events WHERE user_id IN ({ids}) GROUP BY user_id"))
    earlier("main_goal", grouped("SELECT user_id, MIN(created_at) FROM daily_plans WHERE user_id IN ({ids}) AND COALESCE(main_goal,'')!='' GROUP BY user_id"))
    earlier("main_goal", grouped("SELECT user_id, MIN(at) FROM user_events WHERE user_id IN ({ids}) AND name='main_goal_set' GROUP BY user_id"))
    earlier("task", grouped("SELECT p.user_id, MIN(t.created_at) FROM daily_plan_tasks t JOIN daily_plans p ON p.id=t.plan_id WHERE p.user_id IN ({ids}) GROUP BY p.user_id"))
    earlier("task", grouped("SELECT user_id, MIN(at) FROM user_events WHERE user_id IN ({ids}) AND name='task_added' GROUP BY user_id"))
    earlier("ai", grouped("SELECT user_id, MIN(created_at) FROM ai_messages WHERE user_id IN ({ids}) AND role='user' GROUP BY user_id"))
    earlier("friend", grouped("SELECT user_id, MIN(created_at) FROM friendships WHERE user_id IN ({ids}) GROUP BY user_id"))
    earlier("paid", grouped("SELECT user_id, MIN(created_at) FROM stars_payments WHERE user_id IN ({ids}) GROUP BY user_id"))
    for uid in ids:
        created = marks["registered"][uid]
        if not created:
            continue
        for key, delta in (("d1", 1), ("d7", 7)):
            # первый день активности не раньше дня N: вечные дни активности (daily_active) плюс заходы за последние 45 дней (app_opens)
            row = conn.execute(
                "SELECT MIN(d) FROM (SELECT day AS d FROM daily_active WHERE user_id=? UNION ALL SELECT date(at) FROM app_opens WHERE user_id=?) "
                "WHERE d>=date(?, ?)", (uid, uid, _fmt(created), f"+{delta} days")).fetchone()
            marks[key][uid] = _parse(f"{row[0]} 00:00:00") if row and row[0] else None
    return users, ids, marks



# Знакомство с Адамом (db/adam_profile.py): подписи шагов для воронки. Ключи — ключи вопросов; новый вопрос без подписи виден «без названия».
ADAM_INTRO_STEP_LABELS = {
    "focus": "Вопрос 1: что сейчас важнее всего", "struggle": "Вопрос 2: что мешает", "energy": "Вопрос 3: когда больше сил",
    "span": "Вопрос 4: сколько держит фокус", "hours": "Вопрос 5: сколько времени в день", "talk": "Вопрос 6: с чего начать разговор",
}


def onboarding_steps(conn, base_ids):
    """Онбординг по шагам для людей base_ids (открывали приложение): сколько человек дошло до каждого шага и сколько ушло именно здесь.
    Порядок — как в приложении: квиз → тур → экран @ника → окно про ударный режим → подсказки. «Ушли после шага» считается только там,
    где шаги идут строго друг за другом (квиз, тур, ник, ударный режим); подсказки идут параллельно — у них только охват."""
    if not base_ids:
        return []
    marks = ",".join("?" for _ in base_ids)
    seen = {}
    for r in conn.execute(
        f"SELECT name, COALESCE(step,'') AS step, COUNT(DISTINCT user_id) AS u FROM user_events WHERE user_id IN ({marks}) "
        "AND name IN ('quiz_start','quiz_step','quiz_done','tour_start','tour_step','tour_done','handle_intro_shown','handle_intro_done',"
        "'onb_streak_shown','onb_streak_ok','hint_shown','adam_intro_shown','adam_intro_step','adam_intro_skip','adam_intro_habit') GROUP BY name, step", base_ids,
    ).fetchall():
        seen[(r["name"], r["step"])] = r["u"]
    total = len(base_ids)

    def users_of(name, step=""):
        return seen.get((name, step), 0)

    def extras(name, known):
        return sorted({step for (n, step) in seen if n == name and step and step not in known})

    def row(key, label, users, unnamed=False):
        return {"key": key, "label": label, "users": users, "pct": round(100 * users / total, 1), "left": None, "unnamed": unnamed}

    def sequential(rows):
        for index, item in enumerate(rows[:-1]):
            item["left"] = max(0, item["users"] - rows[index + 1]["users"])
        return rows

    quiz = []
    for step, label in QUIZ_STEP_LABELS.items():
        if step in QUIZ_OPTIONAL_STEPS and not users_of("quiz_step", step):
            continue
        quiz.append(row(step, label, users_of("quiz_step", step)))
    quiz += [row(step, step, users_of("quiz_step", step), unnamed=True) for step in extras("quiz_step", QUIZ_STEP_LABELS)]
    quiz.append(row("quiz_done", "Закончили квиз", users_of("quiz_done")))

    tour = [row("tour:" + step, label, users_of("tour_start") if step == "1" else users_of("tour_step", step)) for step, label in TOUR_LABELS.items()]
    tour.append(row("tour_done", "Закончили тур", users_of("tour_done")))

    def distinct_users(name):
        return conn.execute(f"SELECT COUNT(DISTINCT user_id) FROM user_events WHERE user_id IN ({marks}) AND name=?", [*base_ids, name]).fetchone()[0]

    # Знакомство с Адамом: «готово» и «пошли в чат» пишутся с шагом (метод, тема), поэтому считаются по людям, а не по шагам.
    from .adam_profile import METHODS, QUESTION_KEYS
    adam = [row("adam_intro_shown", "Увидели знакомство с Адамом", users_of("adam_intro_shown"))]
    adam += [row("adam_q:" + key, ADAM_INTRO_STEP_LABELS.get(key, key), users_of("adam_intro_step", key), unnamed=key not in ADAM_INTRO_STEP_LABELS)
             for key in QUESTION_KEYS]
    adam += [row("adam_intro_done", "Дошли до итога и получили метод", distinct_users("adam_intro_done")),
             row("adam_intro_chat", "Пошли говорить с Адамом о планах", distinct_users("adam_intro_chat"))]
    adam = sequential(adam)
    adam += [row("adam_intro_habit", "Добавили привычку из итога", users_of("adam_intro_habit")),
             row("adam_intro_skip", "Нажали «Позже»", users_of("adam_intro_skip"))]
    chosen = {r["step"]: r["n"] for r in conn.execute(
        f"SELECT step, COUNT(*) AS n FROM user_events WHERE name='adam_intro_done' AND user_id IN ({marks}) "
        "AND id IN (SELECT MAX(id) FROM user_events WHERE name='adam_intro_done' GROUP BY user_id) GROUP BY step", base_ids).fetchall()}
    methods = [row("adam_method:" + key, method["title"], chosen.get(key, 0)) for key, method in METHODS.items()]

    hints = [row("hint:" + step, label, users_of("hint_shown", step)) for step, label in HINT_LABELS.items()]
    hints += [row("hint:" + step, "Подсказка " + step, users_of("hint_shown", step), unnamed=True) for step in extras("hint_shown", HINT_LABELS)]

    return [
        {"id": "quiz", "title": "Стартовый квиз", "rows": sequential(quiz)},
        {"id": "tour", "title": "Вводный тур", "rows": sequential(tour)},
        {"id": "nick", "title": "Экран @ника", "rows": sequential([
            row("handle_intro_shown", "Увидели экран с @ником", users_of("handle_intro_shown")),
            row("handle_intro_done", "Нажали «Продолжить»", users_of("handle_intro_done"))])},
        {"id": "streak", "title": "Окно «Не дай серии погаснуть»", "rows": sequential([
            row("onb_streak_shown", "Увидели окно", users_of("onb_streak_shown")),
            row("onb_streak_ok", "Нажали «Я в игре»", users_of("onb_streak_ok"))])},
        {"id": "adam_intro", "title": "Знакомство с Адамом", "rows": adam},
        {"id": "adam_method", "title": "Какой метод планирования выбрали (последний сохранённый)", "rows": methods},
        {"id": "hints", "title": "Подсказки в приложении", "rows": hints},
    ]


def entry_sources(conn, ids):
    """Как люди открывают приложение: по полю entry у события session_start (кнопка из бота — «bot_btn», остальное — меню Telegram или ссылка)."""
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    found = {}
    for r in conn.execute(f"SELECT user_id, meta FROM user_events WHERE user_id IN ({marks}) AND name='session_start'", ids).fetchall():
        try:
            entry = str((json.loads(r["meta"]) if r["meta"] else {}).get("entry") or "")
        except (TypeError, ValueError, AttributeError):
            entry = ""
        slot = found.setdefault(entry, {"sessions": 0, "users": set()})
        slot["sessions"] += 1
        slot["users"].add(r["user_id"])
    return [{"entry": entry, "label": ENTRY_LABELS.get(entry, entry or ENTRY_LABELS[""]), "sessions": slot["sessions"], "users": len(slot["users"])}
            for entry, slot in sorted(found.items(), key=lambda kv: -kv[1]["sessions"])]


def funnel(days=14, source=None):
    """Воронка когорты (зарегистрировались за N дней, без админов): сколько дошло до каждого шага, медиана минут от регистрации и
    на каком шаге застревают («не дошли до …»). source — код канала, 'none' — без канала."""
    where, params = _cohort(days, source)
    conn = connect()
    try:
        users, ids, marks = _collect_marks(conn, where, params)
        if not ids:
            return {"days": days, "source": source, "total": 0, "steps": [], "stuck": [], "skips": [], "exits": [], "onboarding": [], "entries": []}
        marks_in = ",".join("?" for _ in ids)
        skips = [{"name": r["name"], "step": r["step"], "events": r["c"], "users": r["u"]} for r in conn.execute(
            f"SELECT name, step, COUNT(*) AS c, COUNT(DISTINCT user_id) AS u FROM user_events WHERE user_id IN ({marks_in}) "
            "AND name IN ('tour_skip','hint_skip') GROUP BY name, step ORDER BY u DESC", ids).fetchall()]
        exits = [{"label": ui_label(r["last_action"]), "screen": screen_label(r["last_screen"]) if r["last_screen"] else None, "count": r["n"]} for r in conn.execute(
            f"SELECT last_action, last_screen, COUNT(*) AS n FROM app_sessions WHERE user_id IN ({marks_in}) AND last_action IS NOT NULL "
            "GROUP BY last_action, last_screen ORDER BY n DESC LIMIT 10", ids).fetchall()]
        opened_ids = [uid for uid in ids if marks["opened"].get(uid)]
        onboarding = onboarding_steps(conn, opened_ids)
        entries = entry_sources(conn, opened_ids)
    finally:
        conn.close()

    total = len(ids)
    steps, previous = [], total
    for key, label in FUNNEL:
        reached = [uid for uid in ids if marks[key].get(uid)]
        minutes = sorted(m for m in (_minutes(marks["registered"][uid], marks[key][uid]) for uid in reached) if m is not None)
        count = len(reached)
        steps.append({
            "key": key, "label": label, "count": count,
            "pct_of_all": round(100 * count / total, 1) if total else 0.0,
            # «от предыдущего шага» — только на основном пути: дальше шаги идут параллельно, и такое сравнение ничего не значит
            "pct_of_prev": (min(100.0, round(100 * count / previous, 1)) if previous else 0.0) if key in CORE_PATH and key != "registered" else None,
            "median_minutes": minutes[len(minutes) // 2] if minutes else None,
        })
        previous = count
    stuck = {}
    labels = dict(FUNNEL)
    for uid in ids:
        for key in CORE_PATH:
            if not marks[key].get(uid):
                stuck[key] = stuck.get(key, 0) + 1
                break
    return {
        "days": days, "source": source, "total": total, "steps": steps, "skips": skips, "exits": exits,
        "onboarding": onboarding, "entries": entries, "onboarding_base": len(opened_ids),
        "stuck": [{"key": key, "label": f"Не дошли до: {labels[key].lower()}", "count": stuck[key]} for key in CORE_PATH if key in stuck],
    }
