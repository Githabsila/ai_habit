"""
Самообслуживание аккаунта (db/account.py) — раньше выгрузить свои данные
целиком или удалить аккаунт можно было только письмом на email из
privacy.html (см. тот файл, раздел "Удаление"). Теперь оба действия
доступны сразу пользователю.
"""
import os

from db import (
    add_user, add_habit, add_ai_message, save_survey_answers, get_user,
    export_full_account_data, request_account_deletion,
)


def test_export_includes_profile_and_habits(uid):
    add_user(uid, username="tester", first_name="Test")
    add_habit(uid, "Пить воду")

    data = export_full_account_data(uid)

    assert data["profile"]["telegram_id"] == uid
    assert data["profile"]["username"] == "tester"
    assert len(data["habits"]) == 1
    assert data["habits"][0]["title"] == "Пить воду"


def test_export_includes_ai_chat_history(uid):
    add_user(uid, "tester", "Test")
    add_ai_message(uid, "user", "Привет, ADAM!")
    add_ai_message(uid, "assistant", "Привет! Как дела с привычками?")

    data = export_full_account_data(uid)

    assert len(data["ai_chat_history"]) == 2
    assert data["ai_chat_history"][0]["message"] == "Привет, ADAM!"


def test_deletion_scrubs_pii_and_bans_account(uid):
    add_user(uid, username="realname", first_name="Реальное Имя")
    save_survey_answers(uid, "бизнес", "хобби", "цель жизни", "цель в боте")
    add_ai_message(uid, "user", "личное сообщение")

    request_account_deletion(uid)

    user = get_user(uid)
    assert user["username"] is None
    assert user["first_name"] == "Удалённый пользователь"
    assert user["banned"] == 1
    assert user["public_profile_enabled"] == 0

    data = export_full_account_data(uid)
    assert data["survey"] is None
    assert data["ai_chat_history"] == []


def test_deletion_removes_avatar_file(uid, tmp_path, monkeypatch):
    import db.account as account_mod

    monkeypatch.setattr(account_mod, "DATA_DIR", str(tmp_path))
    avatars_dir = tmp_path / "avatars"
    avatars_dir.mkdir()
    avatar_file = avatars_dir / f"{uid}.jpg"
    avatar_file.write_bytes(b"fake jpeg bytes")

    add_user(uid, "tester", "Test")
    request_account_deletion(uid)

    assert not avatar_file.exists()


def test_deletion_is_safe_when_no_avatar_file_exists(uid):
    """Удаление не должно падать, если аватарки никогда не было."""
    add_user(uid, "tester", "Test")
    request_account_deletion(uid)  # не должно бросить исключение
    assert get_user(uid)["banned"] == 1


def _rows(table, column, value):
    from db.core import connect
    conn = connect()
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{column}"=?', (value,)).fetchone()[0]
    finally:
        conn.close()


def test_deletion_erases_everything_tied_to_the_person_except_payments(uid):
    """Приложение обещает: пропадут привычки, переписка и покупки; платежи хранятся по закону, чужие данные не трогаются."""
    from db import add_friendship, create_daily_tasks, follow
    from db.shop import begin_payment
    other = uid + 1
    for person in (uid, other):
        add_user(person, f"u{person}", f"Имя {person}")
        add_habit(person, f"Привычка {person}")
        create_daily_tasks(person)
    add_friendship(uid, other)
    follow(other, uid)
    begin_payment(f"charge-del-{uid}", uid, f"subscription:{uid}", 153)

    request_account_deletion(uid)

    for table in ("habits", "daily_tasks", "follows", "friendships"):
        assert _rows(table, "user_id" if table in ("habits", "daily_tasks") else "follower_id" if table == "follows" else "user_id", uid) == 0, table
    assert _rows("follows", "followee_id", uid) == 0 and _rows("friendships", "friend_id", uid) == 0, "связи с другими людьми стираются с обеих сторон"
    assert _rows("stars_payments", "user_id", uid) == 1, "запись о платеже хранится по закону"
    assert _rows("habits", "user_id", other) == 1 and _rows("daily_tasks", "user_id", other) > 0, "данные другого человека не тронуты"
    assert get_user(uid)["banned"] == 1 and get_user(uid)["first_name"] == "Удалённый пользователь"


def test_the_export_lists_what_else_is_stored_about_the_person(uid):
    from db import create_daily_tasks
    add_user(uid, "tester", "Test")
    create_daily_tasks(uid)
    data = export_full_account_data(uid)
    assert data["other_records"]["daily_tasks"], "задачи дня попадают в выгрузку"
    assert "abuse_flags" not in data["other_records"] and "users" not in data["other_records"]
