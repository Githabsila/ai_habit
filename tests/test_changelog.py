"""
«Что нового» (db/changelog.py) — короткий журнал обновлений, показывается
пользователю модальным окном один раз при следующем открытии Mini App
после того, как админ добавил запись (/changelog в боте, см.
handlers/admin.py).

changelog_entries — общая таблица на все тесты (не изолирована по uid,
в отличие от привычек/AI-сообщений и т.п.), поэтому проверяем через
членство конкретной записи в ответе, а не точное количество — иначе
тесты были бы хрупкими к порядку запуска.
"""
from db import (
    add_user, add_changelog_entry, get_unseen_changelog_entries, mark_changelog_seen,
)


def _titles(entries):
    return {e["title"] for e in entries}


def test_new_user_sees_existing_entries(uid):
    """last_seen_changelog_id=0 (ещё ни разу не отмечал просмотренным) —
    значит показать то, что уже есть в журнале."""
    title = f"Новая фича {uid}"
    add_changelog_entry(title, "Теперь можно то-то и то-то.")
    add_user(uid, "tester", "Test")

    entries = get_unseen_changelog_entries(uid, limit=1000)

    assert title in _titles(entries)


def test_marking_seen_hides_already_shown_entries(uid):
    title = f"Старая запись {uid}"
    add_changelog_entry(title, "Текст")
    add_user(uid, "tester", "Test")

    mark_changelog_seen(uid)
    entries = get_unseen_changelog_entries(uid, limit=1000)

    assert title not in _titles(entries)


def test_new_entry_after_seen_appears_again(uid):
    add_changelog_entry(f"Первая запись {uid}", "Текст 1")
    add_user(uid, "tester", "Test")
    mark_changelog_seen(uid)

    second_title = f"Вторая запись {uid}"
    add_changelog_entry(second_title, "Текст 2")
    entries = get_unseen_changelog_entries(uid, limit=1000)

    assert _titles(entries) == {second_title}


def test_limit_caps_number_of_entries_for_new_user(uid):
    for i in range(10):
        add_changelog_entry(f"Запись {uid}-{i}", "Текст")
    add_user(uid, "tester", "Test")

    entries = get_unseen_changelog_entries(uid, limit=3)

    assert len(entries) == 3


def test_entries_are_returned_oldest_to_newest(uid):
    add_user(uid, "tester", "Test")
    mark_changelog_seen(uid)  # обнуляет "непрочитанное" до текущего максимума id

    first = f"Первым {uid}"
    second = f"Вторым {uid}"
    add_changelog_entry(first, "Текст")
    add_changelog_entry(second, "Текст")

    entries = get_unseen_changelog_entries(uid, limit=1000)

    assert [e["title"] for e in entries] == [first, second]
