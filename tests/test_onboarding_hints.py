"""
Подсказки онбординга («Шаг N из 10», app.js::showProductHint): «Далее»/«Назад»
не должны застревать на шаге, цель которого сейчас не на экране, а карточка
подсказки — вылезать за низ экрана при «печати» текста.

Поведение без браузера не проверить — здесь страж от возврата старых ошибок.
"""
import re
from pathlib import Path

APP_JS = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


def _function_body(name):
    start = APP_JS.index(f"function {name}(")
    end = APP_JS.index("\n  }\n", start)
    return APP_JS[start:end]


def test_steps_3_and_4_have_fallback_targets():
    """Главное дело уже заполнено — #mainGoalEditor скрыт; форма «Добавить
    задачу» раскрыта — скрыта кнопка. Раньше шаг при этом молча не открывался."""
    assert re.search(r"3: \{ target: \['#mainGoalEditor', '#mainGoalView', '\.plan-main'\]", APP_JS)
    assert re.search(r"4: \{ target: \['#addPlanTaskTrigger', '#addPlanTaskCollapse'\]", APP_JS)
    body = _function_body("resolveStepTarget")
    assert "Array.isArray(step.target)" in body and "offsetParent !== null" in body


def test_next_and_back_skip_steps_without_a_visible_target():
    assert "goToOnboardingStage(stage + 1, 1)" in APP_JS
    assert "goToOnboardingStage(stage - 1, -1)" in APP_JS
    body = _function_body("goToOnboardingStage")
    assert "!showProductHint(stage) && dir" in body
    # showProductHint сообщает, показала ли шаг
    show = _function_body("showProductHint")
    assert "if (!target) return false;" in show and "return true;" in APP_JS[APP_JS.index(show):][:9000]


def test_page_is_unlocked_before_scrolling_to_the_next_target():
    """Фон блокируется (position:fixed на body), на заблокированной странице
    scrollIntoView молчит — цель следующего шага оставалась за экраном."""
    show = _function_body("showProductHint")
    unlock = show.index("unlockBackgroundScroll();")
    scroll = show.index("scrollIntoView(")
    assert unlock < scroll
    assert "whenScrollSettled(target" in show


def test_hint_card_is_measured_with_the_full_text_and_keeps_its_height():
    show = _function_body("showProductHint")
    assert show.index("textEl.textContent = step.text") < show.index("positionHintCardNear(target, el)")
    assert show.index("positionHintCardNear(target, el)") < show.index("typewriteHintText(textEl, step.text)")
    place = _function_body("positionHintCardNear")
    assert "el.style.minHeight = `${cardHeight}px`" in place
    assert "vh - cardHeight - navClearance" in place, "запасной вариант — над нижней навигацией"
    assert "el.style.minHeight = ''" in _function_body("hideProductHint")
