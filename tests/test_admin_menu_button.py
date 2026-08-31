"""
Кнопка "Админ-панель" в главном меню бота — видна только тем, чей
telegram_id есть в config.ADMIN_IDS (main_menu(is_admin=...)).
"""
from keyboards import main_menu


def _button_texts(kb):
    return [btn.text for row in kb.inline_keyboard for btn in row]


def test_admin_button_shown_for_admin():
    texts = _button_texts(main_menu(is_admin=True))
    assert any("Админ" in t for t in texts)


def test_admin_button_hidden_for_regular_user():
    texts = _button_texts(main_menu(is_admin=False))
    assert not any("Админ" in t for t in texts)


def test_admin_button_hidden_by_default():
    """Если кто-то забудет передать is_admin явно — по умолчанию кнопка
    НЕ должна показываться (безопасное значение по умолчанию)."""
    texts = _button_texts(main_menu())
    assert not any("Админ" in t for t in texts)


def test_reminders_button_always_present():
    for is_admin in (True, False):
        texts = _button_texts(main_menu(is_admin=is_admin))
        assert any("напоминания" in t for t in texts)
