"""
Подсказка про постоянно проваливаемую привычку (app.js, #strugglingHabitBanner).

1) Не советует функции, которых в приложении больше нет: «реже в неделю»
   (частота привычки по дням недели) и «счётчик поменьше» (привычки-счётчики
   с target_count) убраны из формы привычки.
2) Тап по подсказке открывает чат с ADAM, и обсуждение решения начинается
   сразу: app.js ведёт на /coach?intro=struggle&t=<название>&m=<дней>, а
   ai_coach.js сам отправляет первое сообщение про эту привычку.
"""
import re
from pathlib import Path

import pytest

from habit_intents import try_handle_habit_intent
from webapp.routes_ai_miniapp import _looks_like_habit_action

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = STATIC / "app.js"
COACH_JS = STATIC / "ai_coach.js"


def test_struggling_hint_does_not_mention_removed_features():
    js = APP_JS.read_text(encoding="utf-8")
    assert "— Может уменьшим нагрузку или обсудим решение 📩 ?</span>" in js
    assert "снизить планку" not in js
    assert "реже в неделю" not in js
    assert "счётчик поменьше" not in js


def test_tapping_the_hint_opens_the_chat_with_the_habit_context():
    js = APP_JS.read_text(encoding="utf-8")
    assert 'class="struggling-habit-banner__open"' in js
    assert "/coach?intro=struggle&t=${encodeURIComponent(title)}&m=${encodeURIComponent(String(top.missed))}" in js
    # ✕ — отдельная кнопка рядом (а не внутри кнопки открытия): закрывает
    # подсказку, чат не открывает.
    open_start = js.index('class="struggling-habit-banner__open"')
    open_end = js.index("</button>", open_start)
    assert 'class="struggling-habit-banner__close"' not in js[open_start:open_end]
    assert 'class="struggling-habit-banner__close"' in js[open_end:open_end + 200]


def test_chat_sends_the_struggle_intro_even_when_history_exists():
    """В отличие от онбординга (шлёт только при пустой истории), это явный тап
    пользователя — первое сообщение должно уйти и в уже начатый диалог."""
    js = COACH_JS.read_text(encoding="utf-8")
    struggle = js.index("params.get('intro') === 'struggle'")
    history_guard = js.index("if (hasHistory)", struggle)
    assert struggle < history_guard, "ветка struggle должна стоять ДО проверки hasHistory"
    # URL чистим после отправки — иначе перезагрузка чата повторила бы сообщение.
    block = js[struggle:history_guard]
    assert "sendText(text)" in block
    assert "history.replaceState" in block


def _intro_message(title, missed=5):
    js = COACH_JS.read_text(encoding="utf-8")
    head = "Мне сложно держать привычку «"
    tail = ". Давай вместе разберёмся, как уменьшить нагрузку или упростить её. Что предложишь?"
    assert head in js and tail in js, "формулировка стартового сообщения изменилась — обновите тест"
    return f"{head}{title}» — не получается {missed} из последних дней{tail}"


@pytest.mark.parametrize("title", [
    "Вечерний ретуал (куда и сколько времени вложил, что улучшить)",
    "Кайдзен час (план дня, размышления, книга, всё про...)",
    "Отдых (от 30 мин днём)",
    "Холодный душ",
])
def test_intro_message_is_not_taken_for_a_habit_command(uid, title):
    """Чат сначала проверяет сообщение на команды «выполни/удали/добавь
    привычку…» и выполняет их напрямую в обход ИИ. Стартовое сообщение
    должно дойти до ADAM как обычный разговор."""
    message = _intro_message(title)
    assert try_handle_habit_intent(uid, message) is None
    assert not _looks_like_habit_action(message)


def test_intro_message_fits_the_chat_input_limit():
    from webapp.routes_ai_miniapp import AI_MAX_INPUT_CHARS

    longest_title = "я" * 80  # app.js режет название до 80 символов
    assert len(_intro_message(longest_title)) < AI_MAX_INPUT_CHARS


def test_banner_open_button_is_styled_like_plain_text_not_a_browser_button():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.struggling-habit-banner__open\s*\{([^}]*)\}", css)
    assert block, "нет стиля кнопки-обёртки подсказки"
    for rule in ("background:none", "border:0", "text-align:left", "cursor:pointer"):
        assert rule in block.group(1)
