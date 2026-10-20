"""
Тексты окон-праздников без повторов (п.6 владельца, 20.10): на экране рубежа «👑 Легенда» стояло «Легенда ADAM. Продолжай.» —
два раза одно слово и ещё «Продолжай» над кнопкой «Продолжить». Правило: заголовок, подпись и кнопка одного окна не повторяют
одни и те же слова. Проверка по основам слов (первые 4 буквы слов от 5 букв), чтобы «серия / серии / серию» считались повтором.
"""
import re
from pathlib import Path

from db.streak import PRAISE_A

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

# Служебные слова и названия, которые в окнах допустимо называть несколько раз (бренд, валюта).
ALLOWED = {"adam", "coin", "level", "adam,"}


def stems(text):
    words = re.findall(r"[a-zа-яё]+", str(text).lower().replace("ё", "е"))
    return {w[:4] for w in words if len(w) >= 5 and w not in ALLOWED and w[:4] not in ALLOWED}


def shared(*texts):
    """Основы слов, которые встречаются сразу в двух и более текстах окна."""
    seen, repeated = {}, set()
    for index, text in enumerate(texts):
        for stem in stems(text):
            if seen.setdefault(stem, index) != index:
                repeated.add(stem)
    return repeated


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _block(marker, size=1500):
    start = INDEX.index(marker)
    return INDEX[start:start + size]


def _tag_text(block, pattern):
    match = re.search(pattern, block, re.S)
    assert match, pattern
    return _plain(match.group(1))


def test_the_helper_sees_the_old_legend_screen_as_a_repeat():
    assert shared("👑 Легенда", "Легенда ADAM. Продолжай.", "Продолжить") == {"леге", "прод"}
    assert shared("Серия", "Не дай серии погаснуть") == {"сери"}
    assert not shared("СУНДУК МЕСЯЦА", "Тебя ждёт награда", "Тапни 3 раза")


def test_every_milestone_tier_text_differs_from_its_name_and_the_button():
    tiers = re.findall(r'\{ name: "([^"]+)", text: "([^"]+)", a: "#', APP_JS)
    assert len(tiers) == 5
    button = _tag_text(_block('id="levelupMilestoneContinue"', 120), r">([^<]+)</button>")
    for name, text in tiers:
        assert not shared(name, text, button), (name, text)
    assert "Легенда ADAM. Продолжай." not in APP_JS


def test_the_plain_level_up_says_it_once():
    block = _block('id="levelupOverlay"', 500)
    title = _tag_text(block, r'class="levelup-title">([^<]+)<')
    level = _tag_text(block, r'class="levelup-level">(.*?)</div>')
    assert "Новый уровень" not in block and level.startswith("LVL")
    assert not shared(title, level)


def test_the_streak_celebration_does_not_repeat_itself():
    block = _block('id="streakCelebrationOverlay"', 1400)
    kicker = _tag_text(block, r'streak-celebration__kicker">([^<]+)<')
    title = _tag_text(block, r'streak-celebration__title">([^<]+)<')
    button = _tag_text(block, r'id="streakCelebrationContinue"[^>]*>([^<]+)<')
    assert not shared(kicker, title, button), (kicker, title, button)
    # похвала под заголовком: ни «серия», ни «ударный», ни «день», ни «продолжай» — всё это уже в окне
    for text in PRAISE_A:
        assert not re.search(r"сери|ударн|продолж|\bдень\b|\bдня\b|\bдню\b", text.lower()), text
        assert not shared(text.replace("{name}", ""), title, button), text
    assert '"День закрыт. Продолжай."' not in APP_JS


def test_the_status_ladder_and_the_weekly_reward_say_things_once():
    ladder = _block('id="statusLadderOverlay"', 700)
    assert not shared(_tag_text(ladder, r'pq-guide-eyebrow">([^<]+)<'), _tag_text(ladder, r'id="statusLadderTitle">([^<]+)<'))
    weekly = _block('id="streakWeeklyOverlay"', 700)
    assert not shared(_tag_text(weekly, r"<h2>([^<]+)</h2>"), _tag_text(weekly, r"<p>([^<]+)</p>"))


def test_the_streak_intro_text_does_not_retell_its_chips():
    block = _block('id="streakOnboardingOverlay"', 1300)
    title = _tag_text(block, r"<h2>([^<]+)</h2>")
    body = _tag_text(block, r"<p>([^<]+)</p>")
    chips = _tag_text(block, r'streak-onboarding__rewards">(.*?)</div>')
    assert not shared(title, body, chips), (title, body, chips)


def test_the_evolution_text_does_not_repeat_its_button():
    text = re.search(r'const EVOLUTION_REVEAL_TEXT = "([^"]+)"', APP_JS).group(1)
    button = _tag_text(_block('id="evolutionToProfile"', 120), r">([^<]+)</button>")
    assert not shared(text, button), (text, button)


def test_the_chest_window_names_the_chest_once():
    # подпись над сундуком («СУНДУК МЕСЯЦА»), заголовок, подсказка под ним и итог не повторяют друг друга
    eyebrows = re.findall(r'eyebrow: ([^,]+),', APP_JS)
    assert any("СУНДУК МЕСЯЦА" in e for e in eyebrows)
    waiting = re.search(r'title\.textContent = "([^"]+)";\n\s*sub\.textContent = caption;', APP_JS).group(1)
    hints = re.search(r"const CHEST_TAP_HINTS = \[([^\]]+)\]", APP_JS).group(1)
    opened = re.search(r'title\.textContent = final \? "([^"]+)" : "([^"]+)";', APP_JS).groups()
    captions = re.findall(r'caption: (?:`([^`]+)`|"([^"]+)")', APP_JS)
    assert len(captions) >= 4
    for eyebrow in ("СУНДУК МЕСЯЦА", "ГЛАВНЫЙ СУНДУК МЕСЯЦА", "СУНДУК НА ДВОИХ"):
        for caption in [a or b for a, b in captions]:
            caption = caption.replace("${at}", "30").replace("${mq?.goal || 60}", "60")
            assert not shared(eyebrow, waiting, hints, caption), (eyebrow, waiting, hints, caption)
        for title in opened:
            assert not shared(eyebrow, title, "Забрать"), (eyebrow, title)
