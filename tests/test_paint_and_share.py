"""
Правки по скринам с телефона (07.10): шеринг в Telegram «подпись → ссылка», стрелка квиза,
подиум рейтинга из двух игроков, текст шага 4 тура и пустые блоки после перерисовки списков.
Браузера здесь нет — тесты держат от возврата ошибки, а не проверяют отрисовку.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def _css_tail(marker):
    return CSS[CSS.rindex(marker):]


# --- шеринг -----------------------------------------------------------------

def test_share_helper_puts_the_caption_first_and_the_link_after_it():
    """t.me/share/url склеивает «url + \n + text»; ссылка шла первой, а подпись «…добавляйся:» под ней."""
    helper = APP_JS[APP_JS.index("function telegramShareUrl("):][:300]
    assert "url=${encodeURIComponent(text)}&text=${encodeURIComponent(link)}" in helper


def test_every_share_goes_through_the_helper():
    assert "https://t.me/share/url" not in APP_JS.replace(
        "https://t.me/share/url?url=${encodeURIComponent(text)}&text=${encodeURIComponent(link)}", "")
    assert APP_JS.count("telegramShareUrl(") == 4          # объявление + друг, квиз, карточка прогресса
    assert "telegramShareUrl(text, refLink)" in APP_JS and "telegramShareUrl(text, link)" in APP_JS


# --- тур --------------------------------------------------------------------

def test_tour_step_four_text_is_a_plain_sentence():
    step = APP_JS[APP_JS.index("title: 'Второстепенные задачи'"):][:260]
    assert "несколько небольших задач, чтобы ничего не забыть." in step
    assert "— необязательных" not in step and "но чтобы" not in step


# --- стрелка в стартовом тесте ------------------------------------------------

def test_quiz_back_arrow_is_a_centered_svg():
    button = INDEX[INDEX.index('id="startQuizBack"'):][:420]
    assert "<svg" in button and "←" not in button
    tail = _css_tail("Стрелка «назад» в стартовом тесте")
    assert ".start-quiz-back svg{display:block;width:18px;height:18px" in tail


# --- подиум ----------------------------------------------------------------

def test_podium_reactions_row_is_not_placed_like_a_podium_card():
    """При 1-2 игроках ряд 💌/«ты» оказывался nth-child(2|3) и получал grid-column/transform карточки."""
    tail = _css_tail("Подиум рейтинга из 1-2 игроков.")
    rule = re.search(r"\.rating-podium > \.rating-podium-reactions,\s*\.rating-podium > \.rating-podium-reactions:hover\{([^}]*)\}", tail).group(1)
    for decl in ("grid-column:auto", "grid-row:auto", "transform:none"):
        assert decl in rule, decl


# --- пустые блоки после перерисовки ------------------------------------------

def test_reveal_animations_are_off_for_redrawn_lists():
    tail = _css_tail("Пустые прямоугольники на месте привычки/задачи/дня серии")
    group = tail[tail.index(".streak-widget,"):tail.index("animation:none !important;")]
    for selector in (".habit-item", ".plan-list > li", ".rating-item", ".shop-item", ".habit-list", ".plan-card"):
        assert selector in group, selector


def test_streak_day_icons_have_no_filters_or_flicker():
    """Фильтры у иконок дней серии (7 штук, у done — два drop-shadow + бесконечное мерцание) заменены
    тенью текста: на Android WebView такие элементы рисовались пустыми «квадратами»."""
    start = CSS.rindex("STREAK DAY — ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ")
    block = CSS[start:start + 3200]
    span = re.search(r"\.streak-day span\{([^}]*)\}", block).group(1)
    assert "filter" not in span
    done = re.search(r"\.streak-day\.is-done span\{([^}]*)\}", block).group(1)
    assert "text-shadow:" in done and "filter" not in done and "animation" not in done


def test_first_paint_nudge_touches_only_redrawn_containers_and_never_sticks():
    fn = APP_JS[APP_JS.index("function stabilizeFirstPaint("):][:4200]
    assert "const targets = dynamic.concat(extra);" in fn
    assert "critical.concat" not in fn
    # busy-флаг: второй вызов подряд не запоминает «0.999» как исходное значение
    assert "_fpBusy" in fn and "el._fpPrev" in fn


# --- звезда в плане дня и текст окна «Удвоение очков» -----------------------

def test_plan_label_star_is_a_centered_svg():
    label = INDEX[INDEX.index('class="plan-main__label"'):][:520]
    assert "<svg" in label and "✦" not in label
    assert ".plan-label-icon svg{display:block;width:12px;height:12px" in _css_tail("Звезда в «Главная задача дня»")


def test_double_bonus_text_has_no_repeated_promise():
    """Заголовок уже говорит про удвоение — «каждая отметка приносит вдвое больше» повторяла его."""
    block = INDEX[INDEX.index('id="doubleBonusOverlay"'):][:700]
    assert "Успевай выполнить и отметить ещё одну привычку, пока действует бонус.</p>" in block
    assert "вдвое больше" not in block
