"""
Экран "вот твой @ник" сразу после app-tour (Слой A онбординга) —
показывается один раз, независимо от app_tour_seen, тем же способом, что и
should_show_app_tour/mark_app_tour_seen (db/users.py).
"""
from db import add_user
from db.users import should_show_handle_intro, mark_handle_intro_seen, mark_app_tour_seen


def test_new_user_should_see_handle_intro(uid):
    add_user(uid, "u", "Test")
    assert should_show_handle_intro(uid) is True


def test_mark_handle_intro_seen_hides_it(uid):
    add_user(uid, "u", "Test")
    mark_handle_intro_seen(uid)
    assert should_show_handle_intro(uid) is False


def test_handle_intro_independent_from_app_tour(uid):
    """Пользователь, уже видевший app-tour (например, до появления этого
    экрана), всё равно должен увидеть ник хотя бы раз."""
    add_user(uid, "u", "Test")
    mark_app_tour_seen(uid)
    assert should_show_handle_intro(uid) is True
