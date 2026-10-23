"""
Шапка профиля (23.10.2026): тап по колокольчику «Уведомления» открывал окно «Имя и ник» — слой с именем
(.player-card__info, z-index:4) лежал выше колокольчика (z-index:3) и перехватывал тап. Карандаш рядом с именем убран:
имя и ник по-прежнему меняются тапом по самому имени. Реальный тап проверен в браузере на 375 px: у админа и у обычного
человека, с коротким и длинным именем.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8").replace("\r\n", "\n")


def _z(selector):
    """z-index последнего правила с точно таким селектором (позже в файле — сильнее при равной специфичности)."""
    values = re.findall(re.escape(selector) + r"\s*\{[^}]*?z-index:\s*(\d+)", CSS)
    assert values, selector
    return int(values[-1])


def test_the_bell_sits_above_the_name_layer_so_taps_reach_it():
    assert _z(".player-card__info") == 4
    assert _z(".player-card__bell") > _z(".player-card__info"), "иначе тап по колокольчику уходит в «Имя и ник»"
    assert _z(".player-card__bell") >= _z(".player-card__admin-btn"), "колокольчик не ниже кнопки админки"


def test_the_name_still_leaves_room_for_the_bell():
    assert ".player-card__identity{padding-right:46px}" in CSS, "имя не заезжает под колокольчик (38 px + отступ)"
    bell = CSS[CSS.index(".player-card__bell{"):][:260]
    assert "position:absolute;top:12px;right:12px" in bell and "width:38px;height:38px" in bell


def test_the_pencil_next_to_the_name_is_gone():
    assert "player-card__edit" not in INDEX and "player-card__edit" not in CSS
    assert "player-card__edit" not in APP_JS


def test_tapping_the_name_still_opens_name_and_handle_editing():
    assert 'id="playerIdentityBtn" role="button" tabindex="0" aria-label="Изменить имя и ник"' in INDEX
    editor = APP_JS[APP_JS.index("function initIdentityEditor("):][:1600]
    assert 'trigger.addEventListener("click", open);' in editor
    assert 'e.key === "Enter"' in editor, "и с клавиатуры тоже"
    assert 'openCenterModal("identityOverlay")' in editor
    assert 'id="identityOverlay"' in INDEX and 'id="identityTitle">Имя и ник<' in INDEX


def test_the_bell_still_opens_the_notification_centre():
    assert 'getElementById("inboxBtn")?.addEventListener("click", () => openInbox())' in APP_JS
    assert 'id="inboxBtn" type="button" aria-label="Уведомления"' in INDEX
