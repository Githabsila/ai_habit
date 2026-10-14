"""
Учёт онбординга (14.10): «Онбординг по шагам» в /analytics → Воронка (охват шага и «ушли здесь»), источник входа (кнопка из бота / меню Telegram),
подписи шагов в карточке человека. Новый шаг в app.js обязан получить подпись — как новая кнопка обязана попасть в карту функций.
"""
import itertools
import re
from datetime import datetime, timezone
from pathlib import Path

from db import tracking
from db.core import connect

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_ids = itertools.count(900_500_000)
SOURCE = "onbtrack"


def _user(source=SOURCE):
    uid = next(_ids)
    conn = connect()
    conn.execute("INSERT INTO users(telegram_id, username, first_name, created_at, access_status, source_code) VALUES (?,?,?,?,?,?)",
                 (uid, f"u{uid}", "Аня", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "approved", source))
    conn.commit()
    conn.close()
    return uid


def _send(uid, steps, entry=None):
    """Один заход человека: сессия (с источником входа) и события (имя, шаг)."""
    sid = f"sid-{uid}"
    session = {"sid": sid, "started": 1_000_000, "active": 5, "actions": 1, "screen": "home", "hidden": False, "platform": "ios", "tz": 180, "last": []}
    if entry is not None:
        session["entry"] = entry
    events = [{"n": name, "s": step, "t": 1_000_000} for name, step in steps]
    tracking.ingest(uid, session, events, 1_000_000)


def _rows(funnel, group):
    return {r["key"]: r for g in funnel["onboarding"] if g["id"] == group for r in g["rows"]}


QUIZ_ALL = ["welcome", "age_range", "goal", "archetype_q0", "archetype_q1", "archetype_q2", "archetype_q3", "archetype_result", "firststeps", "referral"]


def test_every_onboarding_step_in_the_app_has_a_name_in_the_tracking():
    block = APP[APP.index("const START_QUIZ_STEPS_ALL = ["):]
    block = block[:block.index("\n  ];")]
    keys = set(re.findall(r'key: "(\w+)"', block)) | set(re.findall(r'type: "(?!choice)(\w+)"', block))
    questions = APP[APP.index("const ARCHETYPE_QUIZ_QUESTIONS = ["):]
    questions = questions[:questions.index("\n  ];")].count('q: "')
    keys |= {f"archetype_q{i}" for i in range(questions)}
    assert {"welcome", "age_range", "goal", "archetype_q3", "archetype_result", "firststeps", "referral", "paywall"} <= keys
    assert not [k for k in keys if k not in tracking.QUIZ_STEP_LABELS], "шагу квиза нужна подпись в db/tracking.py::QUIZ_STEP_LABELS"
    steps = APP[APP.index("const PRODUCT_ONBOARDING_STEPS"):]
    stages = set(re.findall(r"^ {4}(\d+): \{ target:", steps[:steps.index("\n  };")], flags=re.M))
    assert stages == set(tracking.HINT_LABELS), "каждой подсказке нужна подпись в db/tracking.py::HINT_LABELS"
    tour = APP[APP.index("const APP_TOUR_STEPS = ["):]
    assert tour[:tour.index("];")].count("title:") == len(tracking.TOUR_LABELS)


def test_the_funnel_shows_how_many_reached_each_step_and_where_they_left():
    a, b, c = _user(), _user(), _user()
    _send(a, [("quiz_start", None)] + [("quiz_step", s) for s in QUIZ_ALL] + [("quiz_step", "mystery_step"), ("quiz_done", None), ("hint_shown", "2"), ("hint_shown", "3")])
    _send(b, [("quiz_step", "welcome"), ("quiz_step", "age_range")])
    _send(c, [("quiz_step", "welcome")])
    funnel = tracking.funnel(30, source=SOURCE)
    assert funnel["onboarding_base"] == 3
    quiz = _rows(funnel, "quiz")
    assert (quiz["welcome"]["users"], quiz["welcome"]["left"]) == (3, 1)
    assert (quiz["age_range"]["users"], quiz["age_range"]["left"]) == (2, 1)
    assert quiz["goal"]["users"] == 1 and quiz["quiz_done"]["users"] == 1 and quiz["quiz_done"]["left"] is None
    assert quiz["welcome"]["label"] == "Приветствие" and quiz["welcome"]["pct"] == 100.0 and quiz["goal"]["pct"] == 33.3
    assert "paywall" not in quiz and "testimonials" not in quiz, "шаги без данных не показываем"
    # шаг, которого нет в подписях, всё равно виден — под своим ключом и с пометкой
    assert quiz["mystery_step"]["unnamed"] is True and quiz["mystery_step"]["label"] == "mystery_step" and quiz["mystery_step"]["users"] == 1
    hints = _rows(funnel, "hints")
    assert hints["hint:2"]["users"] == 1 and hints["hint:2"]["label"] == "Список привычек" and hints["hint:1"]["users"] == 0
    assert all(row["left"] is None for row in hints.values()), "подсказки идут параллельно — «ушли здесь» им не считается"


def test_the_entry_source_separates_the_bot_button_from_the_telegram_menu():
    a, b, c = _user(SOURCE + "_e"), _user(SOURCE + "_e"), _user(SOURCE + "_e")
    _send(a, [("quiz_step", "welcome")], entry="bot_btn")
    _send(b, [("quiz_step", "welcome")])
    _send(c, [("quiz_step", "welcome")], entry="<script>")
    entries = {e["entry"]: e for e in tracking.funnel(30, source=SOURCE + "_e")["entries"]}
    assert entries["bot_btn"]["users"] == 1 and "Кнопка «Открыть ADAM»" in entries["bot_btn"]["label"]
    assert entries[""]["users"] == 2 and entries[""]["label"] == "Меню Telegram или ссылка", "мусор в параметре не становится источником"
    assert not [e for e in entries if "script" in e]


def test_the_person_card_names_the_step_where_the_quiz_stopped():
    uid = _user()
    _send(uid, [("quiz_step", "welcome"), ("quiz_step", "archetype_q2"), ("tour_step", "2")])
    onboarding = tracking.journey(uid)["onboarding"]
    assert onboarding["quiz_last_step"] == "archetype_q2" and onboarding["quiz_last_label"] == "Тест архетипа: вопрос 3"
    assert onboarding["tour_last_label"] == "Формируй привычки"


def test_the_tracker_sends_where_the_app_was_opened_from_and_the_bot_button_carries_it():
    tracker = (ROOT / "webapp" / "static" / "tracker.js").read_text(encoding="utf-8")
    assert "from=([a-z0-9_]{1,20})" in tracker and "entry: entry()" in tracker
    import keyboards
    source = (ROOT / "keyboards.py").read_text(encoding="utf-8")
    assert "from=bot_btn" in source and keyboards.WEBAPP_URL is not None
