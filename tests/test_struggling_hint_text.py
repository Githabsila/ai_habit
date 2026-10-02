"""
Подсказка про постоянно проваливаемую привычку (app.js, #strugglingHabitBanner)
не должна советовать функции, которых в приложении больше нет: «реже в
неделю» (частота привычки по дням недели) и «счётчик поменьше» (привычки-
счётчики с target_count) убраны из формы привычки.
"""
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js"


def test_struggling_hint_does_not_mention_removed_features():
    js = APP_JS.read_text(encoding="utf-8")
    assert "может, снизить планку?" in js
    assert "реже в неделю" not in js
    assert "счётчик поменьше" not in js
