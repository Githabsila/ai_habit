"""
Тексты Адама не должны выглядеть «сырыми»: склонения («1 привычка, 2 привычки, 5 привычек»), единый «ты» без родовых форм
(бот не знает, мужчина перед ним или женщина), никаких недоформатированных шаблонов и склеенных предложений
(«Главная: «Отчёт» Можно двигаться…»). Проверка идёт по всем шаблонным сообщениям из adam_messages.py и по ошибкам чата.
"""
import random
import re
from pathlib import Path

import pytest

import adam_messages as m

ROOT = Path(__file__).resolve().parent.parent

NOUNS = {
    "привыч": (m.habit_word, m.habit_genitive),
    "задач": (m.task_word, m.task_genitive),
    "алмаз": (m.diamond_word, None),
    "дн": (m.day_word, None),
}
NOUN_RE = re.compile(r"(из )?(\d+) (привыч[а-яё]*|задач[а-яё]*|алмаз[а-яё]*|дн[а-яё]*)")


def _problems(text):
    """Что в готовом сообщении выглядит сырым. [] — всё в порядке."""
    found = []
    if re.search(r"[{}]|None|\bnan\b", text):
        found.append("недоформатированный шаблон")
    if re.search(r"\S  +\S", text):
        found.append("двойной пробел")
    if re.search(r"\s[.,!?;:]", text.replace("…", "")):
        found.append("пробел перед знаком препинания")
    if re.search(r"[»)] [А-ЯЁ]", text):
        found.append("предложения склеены без точки")
    for match in NOUN_RE.finditer(text):
        after_iz, number, word = match.group(1), int(match.group(2)), match.group(3)
        nominative, genitive = next(v for k, v in NOUNS.items() if word.startswith(k))
        expected = {genitive(number)} if (after_iz and genitive) else {nominative(number)}
        if after_iz and not genitive:
            expected = {nominative(number)}
        if word not in expected:
            found.append(f"«{match.group(0)}» — нужно «{' / '.join(sorted(expected))}»")
    return found


def _render_everything():
    """Все сообщения Адама на разных числах (по несколько раз: шаблоны и эмодзи выбираются случайно)."""
    random.seed(7)
    out = []
    for _ in range(40):
        for left in range(1, 26):
            habits = [{"title": f"Привычка {i}"} for i in range(left)]
            for completed in (0, 1, 2, 5):
                out.append(("checkpoint", left, m.format_habit_checkpoint_message(habits, 12, completed=completed, total=left + completed)))
        for total in range(1, 26):
            for done in (0, 1, total // 2):
                items = {"main": ["Отчёт"], "secondary": ["Позвонить", "Купить"]}
                out.append(("day_progress", total, m.format_day_progress_message(done, total, items)))
                out.append(("day_progress_plain", total, m.format_day_progress_message(done, total, None)))
        for count in range(0, 9):
            out.append(("evening", count, m.format_evening_progress_message(0, count, [f"Задача {i}" for i in range(count)], None)))
            out.append(("main_done", count, m.format_main_goal_done_message(count)))
        for n in range(2, 14):
            out.append(("perfect", n, m.format_perfect_habit_streak_message(n)))
        for diamonds in range(1, 26):
            out.append(("month_reward", diamonds, m.format_month_end_reward_message(30, 500, diamonds)))
        for streak in range(0, 3):
            for needed in (1, 2, 5):
                out.append(("trial_channel", needed, m.format_trial_channel_progress_nudge(streak, needed)))
        out.append(("all_done", 0, m.format_all_tasks_done_message()))
        out.append(("first_main", 0, m.format_first_plan_action_message("главная задача")))
        out.append(("first_task", 0, m.format_first_plan_action_message("задача")))
        out.append(("goal", 0, m.format_goal_reminder_message("Выучить английский")))
        out.append(("habit_reminder", 0, m.format_habit_reminder_message("Чтение")))
        out.append(("planned", 0, m.format_planned_time_reminder_message("Зарядка")))
        out.append(("simple", 0, m.format_simple_habit_checkpoint_message([{"title": "Чтение"}, {"title": "Спорт"}])))
        out.append(("week_end_done", 0, m.format_week_end_message(True)))
        out.append(("week_end", 0, m.format_week_end_message(False)))
        out.append(("morning", 0, m.format_morning_greeting_message()))
    for streak in range(0, 3):
        out.append(("trial_day2", streak, m.format_trial_day2_nudge(streak)))
    out.append(("trial_last", 0, m.format_trial_last_day_banner(153)))
    out.append(("trial_locked", 0, m.format_trial_locked_message(153)))
    return out


def test_no_message_looks_raw_for_any_count():
    seen, bad = set(), {}
    for kind, number, text in _render_everything():
        if (kind, number, text) in seen:
            continue
        seen.add((kind, number, text))
        problems = _problems(text)
        if problems:
            bad[(kind, number)] = (problems, text)
    assert not bad, "\n".join(f"{k}: {p} :: {t}" for k, (p, t) in list(bad.items())[:12])


@pytest.mark.parametrize("n,expected", [
    (1, "привычка"), (2, "привычки"), (4, "привычки"), (5, "привычек"), (11, "привычек"), (12, "привычек"), (21, "привычка"), (22, "привычки"),
])
def test_declensions(n, expected):
    assert m.habit_word(n) == expected


@pytest.mark.parametrize("n,habits,tasks", [(1, "привычки", "задачи"), (2, "привычек", "задач"), (11, "привычек", "задач"), (21, "привычки", "задачи"), (25, "привычек", "задач")])
def test_genitive_after_iz(n, habits, tasks):
    assert m.habit_genitive(n) == habits and m.task_genitive(n) == tasks


def test_left_phrase_agrees_with_the_number():
    assert [m.left_phrase_tasks(n) for n in (1, 2, 5, 21)] == ["Осталась одна задача", "Осталось 2 задачи", "Осталось 5 задач", "Осталось 21 задача"]


def test_the_last_habit_is_never_called_one_habits_in_any_checkpoint_template():
    for _ in range(60):
        text = m.format_habit_checkpoint_message([{"title": "Чтение"}], 12, completed=3, total=4)
        assert "Осталось одна" not in text and "осталось одна" not in text


# --- «ты» и никакого рода --------------------------------------------------------------------------------------------

GENDERED = re.compile(r"\b(?:[Тт]ы|[Сс]егодня ты|[Уу]же ты) (?:уже |всё |все |сегодня |точно )?[а-яё]+(?:ил|ыл|ал|ел|ёл)\b|\b(?:Закрыл|Начал|Выполнил|Сделал|Довёл|Отметил)\b")
FORMAL = re.compile(r"\b(?:Вы|Вам|Вас|Ваш[а-яё]*|ваш[а-яё]*)\b|Попробуйте|Заходите|Возвращайтесь")


def _all_templates():
    for name, value in vars(m).items():
        if name.startswith("_"):
            continue
        values = value if isinstance(value, (list, tuple)) else value.values() if isinstance(value, dict) else [value]
        for item in values:
            for part in (item if isinstance(item, (list, tuple)) else [item]):
                if isinstance(part, str) and len(part) > 12:
                    yield name, part


def test_templates_do_not_guess_the_gender():
    bad = [(name, text) for name, text in _all_templates() if GENDERED.search(text)]
    assert not bad, bad[:5]


def test_adam_always_says_ty():
    bad = [(name, text) for name, text in _all_templates() if FORMAL.search(text)]
    assert not bad, bad[:5]


@pytest.mark.parametrize("path", ["webapp/routes_ai_miniapp.py", "webapp/services/ai_jobs.py", "handlers/ai.py", "streak_scheduler.py", "coach.py"])
def test_chat_errors_and_pushes_address_the_user_as_ty(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert "Попробуйте" not in source
    assert "Ты успел закрыть привычку" not in source and "Ты прошёл предыдущие" not in source and "Ты пропустил" not in source


def test_waiting_time_is_a_whole_number_of_seconds():
    for path in ("webapp/routes_ai_miniapp.py", "handlers/ai.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "подожди {max(1, math.ceil(wait))} сек." in source and "подожди {wait} сек." not in source
    app = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
    assert "Ты закрыл всё, что планировал" not in app


# --- запасные тексты «Адам пишет первым» (когда модель недоступна) ------------------------------------------------------

def test_checkin_fallbacks_are_clean_for_every_scenario_and_count():
    from db import adam_checkin as ac
    from tests.test_adam_tz_v2 import _tstate

    bad = []
    for scenario in ac.SCENARIOS:
        for habits_total in (1, 2, 3, 5, 6, 11):
            for done in range(0, min(habits_total, 3) + 1):
                habits = [(f"Привычка {i}", i < done) for i in range(habits_total)]
                state = _tstate(habits, [("Отчёт", False, True)], hour=15, struggling={"title": "Чтение", "missed": 4},
                                missed={"title": "Зарядка", "usual_minute": 9 * 60, "kind": "usual"})
                for day in range(3):
                    text = ac.fallback_text(state, scenario, user_id=100 + day, day_iso=f"2026-10-{12 + day}")
                    full = f"Анна, {text}"
                    problems = _problems(full) + (["род"] if GENDERED.search(full) else []) + (["вы"] if FORMAL.search(full) else [])
                    if problems:
                        bad.append((scenario, habits_total, done, problems, text))
    assert not bad, bad[:6]
