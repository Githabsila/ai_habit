"""
«Прочее» в расходе ИИ (16.10): под одной строкой прятались два разных случая — записи ДО учёта по функциям и людям (колонки появились 12.10,
у старых строк они пустые) и вызовы модели, которые код забыл обернуть в scope. Теперь это две строки: «Старые записи» и «Без метки».
"""
from datetime import date, timedelta
from pathlib import Path

from db import ai_usage
from db.core import connect

ADMIN_HTML = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "admin_panel.html").read_text(encoding="utf-8")


def _insert(day, tokens, feature, user_id=None):
    conn = connect()
    try:
        conn.execute("INSERT INTO ai_token_log(day, tokens, provider, user_id, feature) VALUES (?,?,?,?,?)", (str(day), tokens, "test", user_id, feature))
        conn.commit()
    finally:
        conn.close()


def _row(overview, key):
    return next((f for f in overview["by_feature"] if f["feature"] == key), {"tokens": 0, "calls": 0})


def test_old_untagged_rows_are_legacy_and_new_untagged_calls_are_other():
    before = ai_usage.overview(days=30)
    old_day = date.today() - timedelta(days=20)
    _insert(old_day, 700, None)
    _insert(old_day, 300, None)
    _insert(date.today(), 50, "other")
    after = ai_usage.overview(days=30)

    assert _row(after, "legacy")["tokens"] - _row(before, "legacy")["tokens"] == 1000
    assert _row(after, "legacy")["calls"] - _row(before, "legacy")["calls"] == 2
    assert _row(after, "other")["tokens"] - _row(before, "other")["tokens"] == 50
    assert after["legacy"]["calls"] - before["legacy"]["calls"] == 2 and after["legacy"]["until"] >= str(old_day)
    assert after["unlabeled"]["calls"] - before["unlabeled"]["calls"] == 1 and after["unlabeled"]["until"] == str(date.today())
    assert _row(after, "legacy")["label"].startswith("Старые записи") and _row(after, "other")["label"].startswith("Без метки")
    assert "Прочее" not in {f["label"] for f in after["by_feature"]}


def test_a_person_with_old_rows_sees_them_as_legacy_too():
    uid = 846_000_001
    _insert(date.today(), 400, None, user_id=uid)
    _insert(date.today(), 100, "chat", user_id=uid)
    usage = ai_usage.user_usage(uid)
    assert {f["feature"]: f["tokens"] for f in usage["by_feature"]} == {"legacy": 400, "chat": 100}


def test_the_panel_explains_both_service_rows():
    for needle in ("d.legacy", "d.unlabeled", "Старые записи", "Без метки", "shortDay("):
        assert needle in ADMIN_HTML, needle
