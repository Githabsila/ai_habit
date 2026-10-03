"""
Релизные записи «Что нового» (db/changelog.py::RELEASE_NOTES): публикуются при
старте один раз по заголовку, перезапуски не плодят дубли.
"""
from pathlib import Path

from db import (
    add_user, publish_release_notes, RELEASE_NOTES, get_unseen_changelog_entries, mark_changelog_seen,
)
from db.core import connect

ROOT = Path(__file__).resolve().parent.parent


def _count(title):
    conn = connect()
    n = conn.execute("SELECT COUNT(*) AS n FROM changelog_entries WHERE title=?", (title,)).fetchone()["n"]
    conn.close()
    return n


def test_release_notes_are_published_once():
    publish_release_notes()
    publish_release_notes()  # второй запуск сервера
    for note in RELEASE_NOTES:
        assert _count(note["title"]) == 1


def test_a_user_sees_the_release_note_once(uid):
    publish_release_notes()
    add_user(uid, "tester", "Test")
    title = RELEASE_NOTES[0]["title"]
    assert title in {e["title"] for e in get_unseen_changelog_entries(uid, limit=1000)}
    mark_changelog_seen(uid)
    assert title not in {e["title"] for e in get_unseen_changelog_entries(uid, limit=1000)}


def test_the_note_describes_what_was_shipped_without_overpromising():
    body = RELEASE_NOTES[0]["body"]
    for feature in ("Подписки", "Напомнить друзьям", "Подарки", "Задания месяца", "Приватность"):
        assert feature in body, feature
    # Очки уровня не дарятся — это сказано прямо; привычки видны только по включению.
    assert "очки уровня не дарятся" in body
    assert "только друзья" in body and "сам это включишь" in body


def test_server_publishes_notes_on_startup():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "publish_release_notes()" in main_py
    # Публикация идёт после create_tables (иначе таблицы журнала ещё нет).
    assert main_py.index("create_tables()") < main_py.index("publish_release_notes()")
