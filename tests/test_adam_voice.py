"""
Голос Адама (п.9 владельца, 21.10): сообщения в приложении говорят от первого лица Адама («я напомню», а не «бот напомнит» и не
«Адам написал тебе»), а рядом с ними — маленькая премиальная аватарка. Здесь: аватарка и её стили, где она стоит (тосты, окна,
подсказки, плашки, «Уведомления»), тексты без третьего лица, симуляции в админке.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from db.sim_lab import SCENES
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
INBOX_PY = (STATIC.parent.parent / "db" / "inbox.py").read_text(encoding="utf-8")


def _voice_css():
    return CSS[CSS.index("Голос Адама (app.js::adamVoiceHtml)"):CSS.index("Окно «Сундук» (открытие сундука месяца")]


# ---------------------------------------------------------------------------------------------------------------- аватарка

def test_the_small_avatar_exists_and_is_light():
    from PIL import Image
    path = STATIC / "assets" / "adam-avatar-96.webp"
    assert path.exists() and path.stat().st_size < 8_000, "для тоста в 24 px не нужен файл на 50 КБ"
    assert Image.open(path).size == (96, 96)
    assert (STATIC / "assets" / "adam-avatar.webp").exists(), "большая аватарка чата на месте"


def test_the_avatar_component_has_sizes_a_ring_and_no_heavy_effects():
    css = _voice_css()
    for needle in (".adam-voice{", ".adam-voice--xs", ".adam-voice--sm", ".adam-voice--md", ".adam-voice--lg", ".adam-voice__sticker",
                   ".toast.has-voice::before", "/static/assets/adam-avatar-96.webp", ".inbox-item__voice", ".product-onboarding-hint__icon.is-voice",
                   ".feedback-card__icon.is-voice"):
        assert needle in css, needle
    for forbidden in ("filter", "backdrop-filter", "infinite", "animation"):
        assert forbidden not in css, forbidden
    assert "Окно «Сундук»" not in css.split("*/", 1)[1], "блок лежит до сундука, как требуют срезы стилей в тестах"
    for kind in ("is-error", "is-success", "is-praise"):
        assert f".toast.has-voice.{kind}::before" in css, "цвет вида уведомления остаётся — в кольце аватарки"


def test_the_helper_builds_the_avatar_with_an_optional_sticker():
    helper = APP_JS[APP_JS.index("function adamVoiceHtml("):][:520]
    assert 'class="adam-voice adam-voice--${size}" aria-hidden="true"' in helper and "ADAM_VOICE_SRC" in helper
    assert '<i class="adam-voice__sticker">${sticker}</i>' in helper and "decoding=\"async\"" in helper
    assert 'const ADAM_VOICE_SRC = "/static/assets/adam-avatar-96.webp";' in APP_JS


# ---------------------------------------------------------------------------------------------------------------- где стоит

@pytest.mark.skipif(shutil.which("node") is None, reason="нужен Node")
def test_toasts_get_the_avatar_except_coins_and_counters():
    fn = APP_JS[APP_JS.index("function toastHasVoice(message)"):]
    fn = fn[:fn.index("\n  }\n") + 5]
    cases = ["Привычка обновлена", "Сейчас не могу отправить — попробуй чуть позже", "🎁 Серия восстановлена: 5 дн.", "Отмечено ✓ Монеты за сегодня уже выданы",
             "+10 Adam Coin", "+5 Adam Coin x2", "2/3", "  +1", ""]
    script = fn + f"\nprocess.stdout.write(JSON.stringify({json.dumps(cases)}.map(toastHasVoice)));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [True, True, True, True, False, False, False, False, True]
    assert '(toastHasVoice(message) ? " has-voice" : "")' in APP_JS


def test_message_windows_show_adam_with_a_sticker():
    remind = INDEX[INDEX.index('id="remindFriendsSheet"'):][:900]
    assert 'class="feedback-card__icon is-voice"' in remind and 'adam-avatar-96.webp' in remind and '<i class="adam-voice__sticker">👋</i>' in remind
    assert "Одно нажатие — и я напомню им." in remind and "бот" not in remind
    changelog = INDEX[INDEX.index('id="changelogSheet"'):][:900]
    assert 'class="feedback-card__icon is-voice"' in changelog and '<i class="adam-voice__sticker">✨</i>' in changelog


def test_hints_banners_and_the_time_chip_use_the_avatar_instead_of_emoji():
    assert 'iconEl.innerHTML = adamVoiceHtml("lg", step.icon || "💡"); iconEl.classList.add("is-voice");' in APP_JS
    assert 'iconEl.innerHTML = adamVoiceHtml("lg", "💬"); iconEl.classList.add("is-voice");' in APP_JS
    assert "'Я написал тебе в чат'" in APP_JS
    assert '<span class="struggling-habit-banner__icon">${adamVoiceHtml("sm")}</span>' in APP_JS and "🤖</span>" not in APP_JS
    chip = APP_JS[APP_JS.index('class="habit-item__suggest-time"'):][:330]
    assert 'title="Я заметил:' in chip and '${adamVoiceHtml("xs")}${h.suggested_time}?' in chip


def test_the_inbox_shows_adam_for_his_own_messages_and_friends_keep_their_avatars():
    row = APP_JS[APP_JS.index("function inboxItemHtml(it)"):][:900]
    assert row.index("it.actor") < row.index("it.voice") < row.index("inbox-item__icon")
    assert '<span class="inbox-item__voice">${adamVoiceHtml("md", it.icon)}</span>' in row
    assert "VOICE_CATEGORIES = (\"adam\", \"system\")" in INBOX_PY and '"voice": not actor and it["cat"] in VOICE_CATEGORIES' in INBOX_PY
    for title in ("Я написал тебе", "Я сохранил твой день заморозкой", "Новое от меня:", "Дарю бонус за вход по приглашению"):
        assert title in INBOX_PY, title
    demo = APP_JS[APP_JS.index("const SIM_INBOX = (() => {"):][:3400]
    assert demo.count("voice: true") == 3 and '"Я написал тебе"' in demo and '"Адам написал тебе"' not in demo


# ---------------------------------------------------------------------------------------------------------------- тексты

def test_no_third_person_voice_is_left_in_the_app_texts():
    banned = (
        "бот напомнит", "Бот временно", "Друг откроет бота", "Что и когда бот", "AI заметил", "Стиль AI-наставника",
        "ADAM будет", "ADAM поможет", "ADAM стал сильнее", "Новое сообщение от Адама", "Адам написал тебе",
        "Напомнили ✓", "Напоминание отправлено 👋", "Когда Адам напишет", "новости ADAM напишем",
        "Это ADAM — приложение", "ИИ-наставник подскажет", "твой личный ИИ-наставник", "Система для привычек", "AI-анализ твоего", "и AI-анализ",
    )
    for phrase in banned:
        assert phrase not in APP_JS, phrase
        assert phrase not in INDEX, phrase
    for phrase in ("Я буду помогать раскладывать", "Я буду держать твой ритм", "Я буду подкидывать", "Я помогу находить свежие привычки",
                   "✨ Я стал сильнее", "Сейчас не могу отправить — попробуй чуть позже", "Друг откроет меня в Telegram", "Напомнил ✓",
                   "Напомнил другу 👋", "Когда я напишу сам", "Про серию, заморозки и новости напишу сюда",
                   "Я — Адам. Здесь привычки и задачи на день", "Я в чате", "Подскажу, как спланировать день", "Я — ADAM", "Помогаю с привычками",
                   "Я — твой личный наставник", "Планирую день, разбираю проблемы", "мой разбор твоего прогресса"):
        assert phrase in APP_JS, phrase
    assert "Что и когда я тебе отправлял" in INDEX and "Как я с тобой говорю" in INDEX and "и мой разбор</small>" in INDEX


def test_the_remind_window_does_not_repeat_the_avatar_sticker_words():
    block = INDEX[INDEX.index('id="remindFriendsSheet"'):][:1100]
    text = re.search(r"<p>([^<]+)</p>", block).group(1)
    assert "я" in text.split() or " я " in text
    assert not shared(text, "Не сейчас"), "подпись и кнопка не повторяют слова"


# ---------------------------------------------------------------------------------------------------------------- симуляции

def test_the_simulations_show_the_remind_window_and_the_toasts_without_sending_anything():
    keys = [row[0] for row in SCENES]
    assert "remind_friends" in keys and "adam_voice" in keys
    assert "remind_friends: () => openRemindSheetDemo(SIM_REMIND_FRIENDS)," in APP_JS and "adam_voice: () => showVoiceToastsDemo()," in APP_JS
    demo = APP_JS[APP_JS.index("function showVoiceToastsDemo()"):][:520]
    assert demo.count("showToast(") == 4 and '"+10 Adam Coin"' in demo, "монетный тост в демо — без аватарки, для сравнения"
    handler = APP_JS[APP_JS.index('document.getElementById("remindFriendsList")?.addEventListener'):][:520]
    assert handler.index("if (remindSheetDemo)") < handler.index("await nudgeFriend("), "в демо «Напомнить» ничего не отправляет"
    close = APP_JS[APP_JS.index("function closeRemindSheet()"):][:420]
    assert "remindSheetDemo = false;" in close
