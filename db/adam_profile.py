"""
«Знакомство с Адамом»: короткий тест сразу после онбординга. Адам узнаёт, что для человека важно, что мешает, когда у него больше сил
и сколько он может держать фокус, — и по ответам:
  • подбирает метод планирования дня (Помодоро 25 + 5, блоки 45 + 10, глубокая работа 90 + 20 или «три главных дела» без таймеров);
  • предлагает несколько первых привычек (человек добавляет их одним нажатием);
  • запоминает профиль: чат Адама (webapp/services/chat_brain.py) строит «План на день» по выбранному методу, а разговор о целях
    (краткосрочных и долгосрочных) начинается с учётом ответов.

Вопросы, методы и правила подбора лежат здесь, в одном месте: приложение получает их через /api/adam-profile и ничего не придумывает само.
Правка текстов вопросов не требует миграций: ответы хранятся по ключам, неизвестные ключи и значения при сохранении отбрасываются.
"""
import json
import logging

from .core import connect

logger = logging.getLogger(__name__)

VERSION = 1
FLAG_KEY = "adam_intro"             # флаг в «Feature flags» админки: включает знакомство и для тех, кто пришёл раньше NEW_USERS_SINCE
NEW_USERS_SINCE = "2026-10-20"      # аккаунты, созданные с этой даты, видят знакомство сразу после онбординга без всякого флага

# ---------------------------------------------------------------------------------------------------------------- вопросы

# (значение, значок, подпись)
QUESTIONS = (
    {"key": "focus", "title": "Что для тебя сейчас важнее всего?", "options": (
        ("study", "📚", "Учёба"), ("career", "💼", "Работа и карьера"), ("health", "💪", "Здоровье и спорт"),
        ("money", "💰", "Деньги и бизнес"), ("people", "❤️", "Близкие и отношения"), ("growth", "🌱", "Развитие себя"))},
    {"key": "struggle", "title": "Что чаще всего мешает?", "options": (
        ("start", "🛋️", "Откладываю, трудно начать"), ("quit", "🧗", "Бросаю на полпути"), ("chaos", "🌪️", "Не хватает времени, всё в хаосе"),
        ("distract", "📱", "Отвлекаюсь, теряю фокус"), ("no_goal", "🧭", "Нет чёткой цели"))},
    {"key": "energy", "title": "Когда у тебя больше всего сил?", "options": (
        ("morning", "🌅", "Утром"), ("day", "☀️", "Днём"), ("evening", "🌆", "Вечером"), ("night", "🌙", "Ночью"), ("varies", "🎲", "По-разному"))},
    {"key": "span", "title": "Сколько ты можешь работать без перерыва?", "options": (
        ("s25", "⏱️", "До 25 минут"), ("s45", "⏳", "Около 45 минут"), ("s90", "🧠", "Час–полтора"), ("unknown", "🤷", "Не знаю"))},
    {"key": "hours", "title": "Сколько времени в день можно отдавать главным делам?", "options": (
        ("h1", "🕐", "До часа"), ("h2", "🕑", "1–2 часа"), ("h4", "🕓", "2–4 часа"), ("h4p", "🕗", "Больше 4 часов"))},
    {"key": "talk", "title": "С чего начнём разговор?", "options": (
        ("month", "🎯", "Цель на ближайший месяц"), ("year", "🏔️", "Большая цель на год и дальше"),
        ("today", "📋", "План на сегодня"), ("look", "👀", "Пока просто осмотреться"))},
)
QUESTION_KEYS = tuple(q["key"] for q in QUESTIONS)
_VALUES = {q["key"]: {opt[0]: opt[2] for opt in q["options"]} for q in QUESTIONS}

# энергия: подпись и окно часов, куда Адам ставит главное дело
ENERGY = {
    "morning": ("утро", "до 12:00"), "day": ("день", "с 12:00 до 17:00"), "evening": ("вечер", "с 17:00 до 22:00"),
    "night": ("ночь", "после 22:00"), "varies": ("по-разному", None),
}

# ---------------------------------------------------------------------------------------------------------------- методы

METHODS = {
    "pomodoro": {
        "icon": "🍅", "title": "Помодоро 25 + 5", "work": 25, "rest": 5,
        "blurb": "25 минут работы, 5 минут отдыха, после четырёх кругов — отдых подольше. Легко начать и трудно отвлечься.",
        "rule": "Метод «Помодоро»: рабочие блоки по 25 минут, между ними отдых 5 минут, после четвёртого блока — отдых 15–20 минут. "
                "Разложи оставшиеся дела по блокам (большое дело дели на части), у каждого блока укажи время начала, начиная с ближайшего "
                "получаса; отдых — отдельной короткой строкой. Не больше 6 блоков; привычки со своим временем ставь на это время.",
    },
    "focus45": {
        "icon": "⏳", "title": "Блоки 45 + 10", "work": 45, "rest": 10,
        "blurb": "45 минут сосредоточенной работы и 10 минут отдыха. После двух-трёх блоков — большой перерыв. Подходит для учёбы и работы.",
        "rule": "Метод «45 + 10»: рабочие блоки по 45 минут, между ними отдых 10 минут, после двух-трёх блоков — отдых 20–30 минут. "
                "Разложи оставшиеся дела по блокам, у каждого блока укажи время начала, начиная с ближайшего получаса; отдых — отдельной "
                "короткой строкой. Не больше 4 блоков; привычки со своим временем ставь на это время.",
    },
    "deep90": {
        "icon": "🧠", "title": "Глубокая работа 90 + 20", "work": 90, "rest": 20,
        "blurb": "90 минут одного дела без переключений и 20 минут отдыха. Два-три таких блока в день дают больше, чем весь день урывками.",
        "rule": "Метод «Глубокая работа 90 + 20»: один блок — 90 минут одного дела без переключений, затем отдых 20 минут. Не больше трёх таких "
                "блоков в день; самое важное дело — в первый блок, в пик энергии человека. Мелкие дела и привычки собери в короткие окна между "
                "блоками. У каждого блока укажи время начала, начиная с ближайшего получаса.",
    },
    "priorities": {
        "icon": "🎯", "title": "Три главных дела", "work": None, "rest": None,
        "blurb": "Без таймеров: каждый день три главных дела по важности, остальное — если останутся силы. Хорошо, когда всего много или цель пока размыта.",
        "rule": "Метод «Три главных дела»: без таймеров. Выбери из оставшегося не больше трёх главных дел по важности (самое важное — первым), "
                "остальное назови «если останутся силы». Время указывай ориентировочно: утро, день или вечер; привычки со своим временем ставь на это время.",
    },
}
METHOD_ORDER = ("pomodoro", "focus45", "deep90", "priorities")     # порядок при равенстве очков и порядок кнопок выбора
DEFAULT_METHOD = "focus45"

PLAN_RULE_TAIL = ("Этот формат плана заменяет ограничение «максимум 4 шага». Время начала привязывай к текущему времени и к пику энергии человека; "
                  "то, что время уже прошло, не планируй.")


def recommend(answers):
    """Метод по ответам: {"method", "reasons" (до двух коротких причин), "scores"}. Правила простые и объяснимые — человек видит, ПОЧЕМУ так."""
    a = clean_answers(answers)
    score = {key: 0 for key in METHOD_ORDER}
    why = {key: [] for key in METHOD_ORDER}

    def add(method, points, reason):
        score[method] += points
        why[method].append(reason)

    span, struggle, hours = a.get("span"), a.get("struggle"), a.get("hours")
    if span == "s25":
        add("pomodoro", 3, "Фокус держится до 25 минут — короткие подходы как раз под это")
    elif span == "s45":
        add("focus45", 3, "Около 45 минут сосредоточения — это длина одного блока")
    elif span == "s90":
        add("deep90", 3, "Ты можешь надолго уходить в одно дело — длинные блоки дадут больше")
    if struggle == "start":
        add("pomodoro", 2, "Когда трудно начать, 25 минут — не страшно")
    elif struggle == "distract":
        add("pomodoro", 2, "Таймер и короткий отдых возвращают фокус")
    elif struggle == "quit":
        add("focus45", 2, "Блок с понятным концом помогает дойти до конца")
    elif struggle == "chaos":
        add("priorities", 2, "В хаосе помогает выбрать три главных дела вместо длинного списка")
    elif struggle == "no_goal":
        add("priorities", 2, "Пока цель не ясна, важнее выбрать главное, чем нарезать время")
    if hours == "h1":
        add("priorities", 1, "На час в день хватит трёх главных дел")
    elif hours == "h2":
        add("focus45", 1, "Один–два часа — это два-три блока")
    elif hours == "h4":
        add("deep90", 1, "Несколько часов в день — хватит на длинные блоки")
    elif hours == "h4p":
        add("deep90", 2, "Много времени на главное — можно уходить в глубокую работу")
    if span == "s25" or hours == "h1":
        score["deep90"] = -1                    # 90 минут подряд при коротком фокусе или часе в день — не про этого человека

    best = max(METHOD_ORDER, key=lambda m: (score[m], -METHOD_ORDER.index(m)))
    if score[best] <= 0:
        best = DEFAULT_METHOD
    return {"method": best, "reasons": why[best][:2], "scores": score}


# ---------------------------------------------------------------------------------------------------------------- привычки

HABIT_IDEAS = {
    "study": ("Учёба 25 минут", "Повторить конспект 10 минут", "Прочитать 10 страниц"),
    "career": ("План дня на 5 минут", "Главное дело первым делом", "Разобрать почту за 15 минут"),
    "health": ("Зарядка 10 минут", "Стакан воды утром", "Прогулка 20 минут"),
    "money": ("Записать траты за день", "Финансы: 15 минут чтения", "Отложить на цель"),
    "people": ("Написать близкому человеку", "Вечер без телефона 30 минут", "Сказать кому-то спасибо"),
    "growth": ("Чтение 15 минут", "Дневник 5 минут", "Английский 15 минут"),
}
STRUGGLE_HABITS = {
    "start": "Начать главное дело на 2 минуты",
    "quit": "Вечером отметить, что получилось",
    "chaos": "План дня на 5 минут утром",
    "distract": "Фокус-блок 25 минут без телефона",
    "no_goal": "Записать цель на неделю",
}


def suggest_habits(answers, existing_titles=(), limit=3):
    """До `limit` названий привычек по ответам (одна — под то, что мешает). Уже имеющиеся у человека не предлагаем."""
    a = clean_answers(answers)
    ideas = list(HABIT_IDEAS.get(a.get("focus"), ()))
    ordered = []
    if ideas:
        ordered.append(ideas[0])
    if STRUGGLE_HABITS.get(a.get("struggle")):
        ordered.append(STRUGGLE_HABITS[a["struggle"]])
    ordered.extend(ideas[1:])
    have = {str(t).strip().casefold() for t in existing_titles}
    result = []
    for title in ordered:
        if title.casefold() not in have and title not in result:
            result.append(title)
    return result[:limit]


# ---------------------------------------------------------------------------------------------------------------- данные для приложения

def quiz_payload():
    """Вопросы и методы для окна знакомства."""
    return {
        "questions": [
            {"key": q["key"], "title": q["title"], "options": [{"value": v, "icon": i, "label": l} for v, i, l in q["options"]]}
            for q in QUESTIONS
        ],
        "methods": [{"key": key, **{k: METHODS[key][k] for k in ("icon", "title", "work", "rest", "blurb")}} for key in METHOD_ORDER],
    }


def clean_answers(raw):
    """Только известные вопросы и допустимые ответы."""
    if not isinstance(raw, dict):
        return {}
    return {key: str(raw[key]) for key in QUESTION_KEYS if str(raw.get(key)) in _VALUES[key]}


def build_result(answers, existing_titles=()):
    """Итог теста для экрана результата: метод, причины, лучшее время, предложенные привычки."""
    rec = recommend(answers)
    a = clean_answers(answers)
    energy = ENERGY.get(a.get("energy"))
    return {
        "method": rec["method"], "reasons": rec["reasons"],
        "peak": {"label": energy[0], "window": energy[1]} if energy and energy[1] else None,
        "habits": suggest_habits(a, existing_titles),
    }


# ---------------------------------------------------------------------------------------------------------------- хранение

def get_profile(user_id):
    conn = connect()
    try:
        row = conn.execute("SELECT answers, method, version, completed_at, skipped_at FROM adam_profile WHERE user_id=?", (int(user_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        answers = json.loads(row["answers"]) if row["answers"] else {}
    except ValueError:
        answers = {}
    return {"answers": clean_answers(answers), "method": row["method"], "version": row["version"],
            "completed_at": row["completed_at"], "skipped_at": row["skipped_at"]}


def save_profile(user_id, answers, method=None):
    """Сохраняет пройденный тест. Нужны ответы на все вопросы; неизвестный метод заменяется рекомендованным.
    Возвращает {"ok": True, "method", "answers"} либо {"error": "incomplete"}."""
    a = clean_answers(answers)
    if len(a) < len(QUESTION_KEYS):
        return {"error": "incomplete"}
    chosen = method if method in METHODS else recommend(a)["method"]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO adam_profile(user_id, answers, method, version, completed_at, skipped_at, updated_at) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP,NULL,CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET answers=excluded.answers, method=excluded.method, version=excluded.version, "
            "completed_at=CURRENT_TIMESTAMP, skipped_at=NULL, updated_at=CURRENT_TIMESTAMP",
            (int(user_id), json.dumps(a, ensure_ascii=False, separators=(",", ":")), chosen, VERSION),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "method": chosen, "answers": a}


def skip_profile(user_id):
    """«Позже»: автоматически окно больше не открывается (оно остаётся в Настройках). Пройденный профиль не трогаем."""
    conn = connect()
    try:
        conn.execute("INSERT OR IGNORE INTO adam_profile(user_id, skipped_at, updated_at) VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", (int(user_id),))
        conn.commit()
    finally:
        conn.close()


def reset_profile(user_id):
    """Админ-утилита вместе со сбросом онбординга: чтобы тестировщик мог пройти знакомство заново."""
    conn = connect()
    try:
        conn.execute("DELETE FROM adam_profile WHERE user_id=?", (int(user_id),))
        conn.commit()
    finally:
        conn.close()


def should_show(user_id):
    """Открывать ли знакомство само: профиля и отказа нет, и человек либо пришёл не раньше NEW_USERS_SINCE, либо включён флаг adam_intro."""
    if get_profile(user_id) is not None:
        return False
    from .users import get_user
    user = get_user(user_id)
    if not user:
        return False
    created = str(user["created_at"] or "")[:10] if "created_at" in user.keys() else ""
    if created and created >= NEW_USERS_SINCE:
        return True
    from .feature_flags import is_feature_enabled
    return is_feature_enabled(FLAG_KEY, user_id)


def public_state(user_id):
    """Кусок /api/state: показывать ли окно и какой метод выбран."""
    profile = get_profile(user_id)
    done = bool(profile and profile["completed_at"])
    method = profile["method"] if done and profile["method"] in METHODS else None
    return {"show": should_show(user_id), "done": done, "method": method, "method_title": METHODS[method]["title"] if method else None}


# ---------------------------------------------------------------------------------------------------------------- для чата Адама

def profile_lines(profile):
    """Строки для модели: что Адам знает о человеке по знакомству. [] — знакомство не пройдено."""
    if not profile or not profile.get("completed_at"):
        return []
    a = profile["answers"]
    lines = []

    def label(key):
        return _VALUES[key].get(a.get(key))

    parts = []
    if label("focus"):
        parts.append(f"сейчас важнее всего — {label('focus').lower()}")
    if label("struggle"):
        parts.append(f"чаще всего мешает: {label('struggle').lower()}")
    energy = ENERGY.get(a.get("energy"))
    if energy:
        parts.append(f"больше всего сил — {energy[0]}" + (f" ({energy[1]})" if energy[1] else ""))
    if label("span"):
        parts.append(f"без перерыва работает: {label('span').lower()}")
    if label("hours"):
        parts.append(f"времени на главные дела в день: {label('hours').lower()}")
    if parts:
        lines.append("Из знакомства с Адамом: " + "; ".join(parts))
    method = profile.get("method")
    if method in METHODS:
        lines.append(f"Метод планирования дня, который выбрал человек: «{METHODS[method]['title']}»")
    return lines


def plan_rule(profile):
    """Инструкция модели для «Плана на день»: строить план блоками выбранного метода. "" — метода нет."""
    method = (profile or {}).get("method")
    if method not in METHODS or not (profile or {}).get("completed_at"):
        return ""
    energy = ENERGY.get((profile["answers"] or {}).get("energy"))
    peak = f" Пик энергии человека: {energy[0]} ({energy[1]})." if energy and energy[1] else ""
    return f"{METHODS[method]['rule']}{peak} {PLAN_RULE_TAIL}"
