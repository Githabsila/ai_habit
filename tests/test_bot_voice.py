"""
Голос Адама в сообщениях бота (п.9 владельца, 21.10, вторая часть — пуши и меню бота): Адам говорит «ты» и от первого лица, без формального
«вы / нажмите / откройте», без «AI-разбор», «ИИ-наставник», «Адам написал» и «бот напомнит». Проверка по строкам исходников (без docstring
и без записей в лог), кроме осознанных исключений: анкета при входе, вариант A (A/B «вы» против «ты» ради воронки), и тексты для админов.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = sorted(
    [p for p in (ROOT / "handlers").glob("*.py") if p.name not in ("admin.py", "__init__.py")]
    + [ROOT / name for name in (
        "coach.py", "goal_feedback.py", "referral_mailing.py", "keyboards.py", "adam_messages.py", "trial_messages.py", "subscription_flow.py",
        "subscription_scheduler.py", "morning_ping.py", "onboarding_auto.py", "streak_scheduler.py",
    )]
    + [ROOT / "middlewares" / "access_control.py", ROOT / "db" / "streak.py", ROOT / "db" / "inbox.py", ROOT / "db" / "core.py"]
)

FORMAL = re.compile(
    r"\b(?:Вы|Вам|Вас|Ваш[а-яё]*|ваш[а-яё]*|[Нн]ажмите|[Оо]ткройте|[Нн]апишите|[Вв]ыберите|[Вв]ведите|[Оо]тправьте|[Пп]опробуйте|"
    r"[Зз]аходите|[Вв]озвращайтесь|[Пп]ришлите|[Уу]кажите|[Пп]одождите|[Оо]братитесь|[Пп]ерейдите|[Дд]обавьте|[Сс]ледуйте)\b"
)
THIRD_PERSON = re.compile(r"\bAI-(?:разбор|анализ|наставник)|\bИИ-наставник|Адам написал|\b[Бб]от напомн|ADAM будет|Бот временно")

# Осознанные исключения (строка содержит один из фрагментов).
ALLOWED = (
    "закрытая платформа",                                   # анкета при входе, вариант A (формальное «вы» — часть эксперимента A/B)
    "Нажмите на вариант или напишите ответ текстом",         # подсказка анкеты (общая для A и B)
    "Эта заявка уже закрыта. Отправьте /start",
    "REPLACE(description, 'к вашему текущему лимиту'",       # миграция описаний товаров: ищет старый текст, чтобы заменить на «твоему»
    "Нажмите на вариант или ответьте одним сообщением",
    "Чем вы сейчас занимаетесь",
)


def _strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search("[А-Яа-яЁё]", node.value)):
            continue
        holder = parent.get(node)
        if isinstance(holder, ast.Expr) and isinstance(parent.get(holder), (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                       # docstring
        call = holder
        while call is not None and not isinstance(call, ast.Call):
            call = parent.get(call)
        if call is not None and isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.value.id in ("logger", "logging", "log"):
            continue                                       # запись в лог — для админов, не для людей
        yield node.lineno, node.value


def _problems(pattern):
    found = []
    for path in FILES:
        for line, text in _strings(path):
            if any(fragment in text for fragment in ALLOWED):
                continue
            if pattern.search(text):
                found.append(f"{path.relative_to(ROOT).as_posix()}:{line}: {text.strip().replace(chr(10), ' ')[:90]}")
    return found


def test_the_bot_talks_to_you_not_to_you_formally():
    assert not _problems(FORMAL), "\n" + "\n".join(_problems(FORMAL))


def test_no_third_person_or_ai_wording_is_left_in_the_bot_texts():
    assert not _problems(THIRD_PERSON), "\n" + "\n".join(_problems(THIRD_PERSON))


def test_the_new_wording_is_in_place():
    from handlers import ai, community, daily, goals, habits, payments, shop, start
    import adam_messages
    import keyboards
    from handlers import onboarding

    assert "Я здесь" in (ROOT / "handlers" / "ai.py").read_text(encoding="utf-8") and "Просто напиши мне сообщение" in (ROOT / "handlers" / "ai.py").read_text(encoding="utf-8")
    assert "Мой разбор твоей недели по привычкам" in (ROOT / "coach.py").read_text(encoding="utf-8")
    assert "Мой разбор твоего месяца по привычкам" in (ROOT / "coach.py").read_text(encoding="utf-8")
    assert "Разобрать мой прогресс" in (ROOT / "keyboards.py").read_text(encoding="utf-8")
    assert "Мой разбор твоего прогресса" in (ROOT / "handlers" / "progress.py").read_text(encoding="utf-8")
    assert "Привет, я ADAM</b> — твой личный наставник" in (ROOT / "handlers" / "onboarding.py").read_text(encoding="utf-8")
    assert "Добро пожаловать! Я — ADAM, всё готово. Открой приложение кнопкой ниже" in onboarding.APPROVED_INTRO
    assert "Со мной интереснее, когда рядом друзья" in (ROOT / "referral_mailing.py").read_text(encoding="utf-8")
    assert not [name for name in dir(adam_messages) if name.startswith("format_trial_")], "старые тексты триала заменил trial_messages.py"
    assert ai and community and daily and goals and habits and payments and shop and start and keyboards


def test_the_welcome_message_speaks_for_adam():
    source = (ROOT / "handlers" / "start.py").read_text(encoding="utf-8")
    assert "Добро пожаловать! Я — ADAM." in source and "Открой его кнопкой ниже" in source and "Общаться с ИИ" not in source
    assert "Твой аккаунт заблокирован администрацией" in source


def test_shop_descriptions_say_ty_for_new_and_existing_databases():
    from db.core import connect
    conn = connect()
    try:
        conn.execute("UPDATE shop_items SET description='20 дополнительных ответов ADAM к вашему текущему лимиту' WHERE id=21")
        conn.commit()
        from db.core import create_tables
        create_tables()
        rows = conn.execute("SELECT description FROM shop_items WHERE description LIKE '%лимиту%'").fetchall()
    finally:
        conn.close()
    assert rows and all("к твоему текущему лимиту" in r["description"] and "вашему" not in r["description"] for r in rows)
