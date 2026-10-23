"""
Окно «Сундук» (22.10, п.2 владельца): открытие сундука месяца и парного задания — закрытый сундук трясётся, пока идёт запрос, потом
крышка откидывается, вылетают искры, появляются награды и «Забрать». Плюс маленькая галочка на уже открытом сундуке дорожки месяца:
узел непрозрачный, жёлтая полоса сквозь него не просвечивает. Браузера в тестах нет — разметку, скрипт и стили держат проверки по тексту,
событие учёта — через настоящий маршрут.
"""
import itertools
import re
from pathlib import Path

import pytest

from db import add_user
from db.core import connect
from db.quests import QUEST_KEYS
import db.month_quests as month_quests
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
SERVER = (STATIC.parent / "webapp_server.py").read_text(encoding="utf-8")

_uid_counter = itertools.count(853_000_000, 100)


@pytest.fixture
def uid():
    return next(_uid_counter)


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}", "Content-Type": "application/json"}


def _give_points(uid_, n):
    key = month_quests.month_key(month_quests._today(uid_))
    conn = connect()
    for i in range(n):
        conn.execute(
            "INSERT INTO daily_quests(user_id, day, quest_key, title, target, progress, reward_coins, claimed) VALUES (?, ?, ?, 't', 1, 1, 5, 1)",
            (uid_, f"{key}-{(i % 28) + 1:02d}", QUEST_KEYS[i // 28]),
        )
    conn.commit()
    conn.close()


def _chest_css():
    return CSS[CSS.index("Окно «Сундук» (открытие сундука месяца"):]


# ---------------------------------------------------------------------------------------------------------------- разметка

def test_chest_window_markup_is_a_hidden_dialog_with_all_parts():
    block = INDEX[INDEX.index('id="chestOverlay"'):]
    block = block[:block.index("</ul>")]
    assert re.search(r'<div class="chest-overlay" id="chestOverlay" hidden aria-hidden="true" role="dialog" aria-modal="true"', INDEX)
    for needle in ('id="chestCard"', 'data-phase="wait"', 'data-taps="0"', 'id="chestEyebrow"', 'class="chest-box"', 'class="chest-body"', 'class="chest-lid"',
                   'id="chestTitle"', 'id="chestSub"', 'id="chestHint"', 'id="chestPrizes"'):
        assert needle in block, needle
    assert 'id="chestClose"' in INDEX and "Не сейчас" in INDEX[INDEX.index('id="chestClose"'):][:80], "в ожидании тапов кнопка — «Не сейчас», «Забрать» она становится после открытия"
    stage = INDEX[INDEX.index('id="chestStage"') - 40:][:260]
    assert 'role="button"' in stage and 'tabindex="0"' in stage and "три раза" in stage, "сундук — кнопка: тап, Enter и Пробел"
    assert block.count("<i></i>") == 3, "шкала из трёх точек"
    assert block.index('id="chestStage"') < block.index('class="chest-pips"') < block.index('id="chestTitle"') < block.index('id="chestHint"')
    assert block.count('class="chest-spark') >= 8, "искры вылетают при открытии"
    assert INDEX.index('id="chestOverlay"') > INDEX.index('id="dailyQuestsOverlay"'), "окно лежит в документе после квестов дня"


def test_script_and_style_versions_follow_the_release():
    assert "style.css?v=20261021_QUESTS_V82" in INDEX and "app.js?v=20261021_QUESTS_V92" in INDEX


# ---------------------------------------------------------------------------------------------------------------- скрипт

def test_both_chests_open_through_the_window_and_apply_data_when_it_closes():
    assert "async function openChestReveal(" in APP_JS
    month = APP_JS[APP_JS.index('document.getElementById("monthCardOpen")?.addEventListener'):][:1400]
    assert "openChestReveal({" in month and '"/api/month-quests/claim"' in month
    assert "onDone: (result) => applyActionPatch(result)" in month
    pair = APP_JS[APP_JS.index("async function pairCardAction"):][:2400]
    assert "openChestReveal({" in pair and "/api/pair-quest/${id}/claim" in pair
    assert "onDone: (result) => applyActionPatch(result)" in pair
    assert "Сундук открыт: +" not in APP_JS, "вместо тоста — окно с наградами"


def test_window_waits_for_three_taps_then_asks_the_server_shows_prizes_and_hands_errors_back():
    body = APP_JS[APP_JS.index("async function openChestReveal("):][:7200]
    assert "const CHEST_TAPS = 3;" in APP_JS and "CHEST_SHAKE_MS" in body
    # запрос уходит только после трёх тапов: до этого «Не сейчас» закрывает окно, ничего не списывая
    shake = body.index('card.dataset.phase = "shake";')
    assert body.index("await new Promise((resolve) => {") < body.index('finish("tapped")') < shake < body.index("result = await request();", shake)
    assert 'card.dataset.phase = "wait";' in body and 'card.dataset.phase = "shake";' in body
    assert 'if (outcome === "later") {' in body and "return null;" in body, "«Не сейчас» — без запроса и без награды"
    assert body.index('if (outcome === "later")') < shake
    assert body.index("catch (err)") < body.index("throw err;") < body.index("chestPrizeItems("), "ошибка запроса закрывает окно и уходит вызывающему"
    assert 'card.dataset.phase = "open";' in body
    assert body.index('haptic("triple");') < body.index('playChestSound("open");'), "открытие — тройная долгая вибрация"
    assert 'card.dataset.ready = "1";' in body, "«Забрать» оживает только после наград"
    assert "if (closed || card.dataset.ready !== \"1\") return;" in body, "раньше времени окно не закрывается"
    assert 'e.key === "Escape"' in body and "removeEventListener" in body
    assert "reduceMotion" in body, "при «уменьшить движение» ждём недолго"


def test_every_tap_knocks_with_its_own_sound_and_vibration_and_the_hint_counts_down():
    body = APP_JS[APP_JS.index("async function openChestReveal("):][:7200]
    tap = body[body.index("function onTap() {"):][:900]
    assert "taps += 1;" in tap and 'card.dataset.taps = String(taps);' in tap
    assert "hapticKnock(taps);" in tap and 'playChestSound("tap", taps);' in tap
    assert 'stage.classList.add("is-knock")' in tap and "void stage.offsetWidth" in tap, "каждый удар перезапускает анимацию"
    assert "if (taps >= CHEST_TAPS) finish(\"tapped\");" in tap
    assert 'CHEST_TAP_HINTS = ["Тапни 3 раза", "Ещё 2 раза", "Ещё один!"]' in APP_JS
    assert 'e.key === "Enter" || e.key === " "' in body, "с клавиатуры тоже три нажатия"
    sound = APP_JS[APP_JS.index("function playChestSound("):][:1800]
    assert sound.count('"triangle"') >= 6 and "knock[Math.min(Math.max(n, 1), CHEST_TAPS) - 1]" in sound, "у каждого из трёх ударов свой звук"
    assert "window.AdamHaptics.knock(n)" in APP_JS[APP_JS.index("function hapticKnock("):][:200]


def test_cancelling_leaves_the_chest_waiting_and_gives_the_pair_button_back():
    pair = APP_JS[APP_JS.index("async function pairCardAction"):][:2800]
    assert "const opened = await openChestReveal({" in pair and "if (!opened) button.disabled = false;" in pair
    month = APP_JS[APP_JS.index('document.getElementById("monthCardOpen")?.addEventListener'):][:1500]
    assert "btn.disabled = false;" in month, "кнопка месяца возвращается и после «Не сейчас»"


def test_prizes_cover_coins_diamonds_month_points_and_badge():
    items = APP_JS[APP_JS.index("function chestPrizeItems("):][:1500]
    for needle in ("ADAM_COIN_ICON", "reward.diamonds", "reward.month_points", "reward.badge", "escapeHtml(reward.badge)", 'pluralRu(diamonds, "алмаз"'):
        assert needle in items, needle


def test_window_blocks_other_popups_and_sits_above_the_quests_modal():
    assert '"chestOverlay"' in APP_JS[APP_JS.index("const REMIND_BLOCKING_OVERLAYS"):][:600]
    z = int(re.search(r"\.chest-overlay\{[^}]*z-index:(\d+)", CSS).group(1))
    assert z > 9999, "выше окна квестов дня и подсказок (9999)"


def test_opened_chest_on_the_month_track_is_a_small_svg_check_not_a_text_glyph():
    node = APP_JS[APP_JS.index('<div class="month-chest ${stateClass}"'):][:420]
    assert "ch.claimed ? ICON_CHECK" in node and '"✓"' not in node
    assert ".month-chest__icon .ui-icon{width:15px;height:15px}" in CSS


# ---------------------------------------------------------------------------------------------------------------- стили

def test_month_nodes_are_opaque_so_the_progress_bar_does_not_shine_through():
    block = CSS[CSS.index("Задания месяца (db/month_quests.py)"):CSS.index("Подарок другу (db/gifts.py)")]
    icon = re.search(r"\.month-chest__icon\{[^}]*\}", block).group(0)
    assert "opacity" not in icon and "rgba(" not in icon, "кружок непрозрачный: сквозь него не видно жёлтую полосу"
    claimed = re.search(r"\.month-chest\.is-claimed \.month-chest__icon\{([^}]*)\}", block).group(1)
    ready = re.search(r"\.month-chest\.is-ready \.month-chest__icon\{([^}]*)\}", block).group(1)
    for rule in (claimed, ready):
        assert re.search(r"background:#[0-9a-fA-F]{6}", rule) and "opacity" not in rule and "rgba(" not in re.sub(r"box-shadow:[^;]*", "", rule)
    assert ".month-chest__glyph{display:block;line-height:1;opacity:.62}" in block, "тусклым остаётся только значок внутри"


def test_chest_styles_cover_the_waiting_knocking_and_opening_phases():
    css = _chest_css()
    for name in ("chestDrop", "chestInvite", "chestKnock"):
        assert f"@keyframes {name}" in css, name
    assert ".chest-stage.is-knock{animation:chestKnock" in css and "var(--kn,1)" in css, "сила удара растёт с номером тапа"
    assert ".chest-card[data-phase=\"wait\"] .chest-box{animation:chestDrop" in css, "сундук «появляется»"
    assert "data-taps=\"1\"] .chest-pips i:nth-child(-n+1)" in css and "data-taps=\"3\"] .chest-pips i{" in css, "шкала заполняется"
    assert ".chest-card:not([data-phase=\"open\"]) .chest-prizes{display:none}" in css
    assert "cursor:pointer" in css[css.index(".chest-stage{"):][:400] and "touch-action:manipulation" in css[css.index(".chest-stage{"):][:400]
    assert ".chest-card[data-phase=\"wait\"] .chest-btn{opacity:1" in css, "«Не сейчас» видна, пока ждём тапы"
    assert ".chest-stage.is-knock" in css[css.index("@media (prefers-reduced-motion: reduce)"):], "при «уменьшить движение» удары не трясут"
    assert re.search(r"chestInvite 1\.1s ease-in-out 1s 3\}", css), "подсказка-«приглашение» конечная: три раза"


def test_the_button_does_not_flash_gold_while_the_chest_shakes_and_the_card_does_not_jump():
    """23.10, видео владельца: после третьего тапа «Не сейчас» на 0,25 с становилась золотой «Забрать» и гасла (правило фазы wait
    переставало действовать, а цвет переключался мгновенно), а при открытии карточка схлопывалась на 22 px (пропадала шкала и рамка)."""
    css = _chest_css()
    ghost = re.search(r'\.chest-card\[data-phase="wait"\] \.chest-btn,\.chest-card\[data-phase="shake"\] \.chest-btn\{([^}]*)\}', css)
    assert ghost, "пока сундук трясётся, кнопка всё ещё «Не сейчас»: бесцветная и гаснет"
    assert "background:transparent" in ghost.group(1) and "color:#B9B3D6" in ghost.group(1)
    base = re.search(r"\.chest-btn\{([^}]*)\}", css).group(1)
    assert "border:0" in base and "opacity:0" in base, "обычная (золотая) кнопка скрыта, пока не готова"
    assert "border:" not in ghost.group(1).replace("border-radius", ""), "рамка — тенью: высота кнопки не меняется между фазами"
    assert "inset 0 0 0 1px" in ghost.group(1)
    assert '.chest-card[data-phase="open"] .chest-pips{visibility:hidden}' in css, "шкала держит место, карточка не схлопывается"
    assert 'data-phase="open"] .chest-pips,' not in css and not re.search(r'\.chest-pips\{[^}]*display:none', css)
    assert '.chest-card[data-phase="open"] .chest-hint{display:none}' in css, "подсказку заменяют награды той же высоты"
    assert re.search(r"\.chest-hint\{[^}]*min-height:96px", css) and re.search(r"\.chest-prizes\{[^}]*min-height:96px", css)
    # золотая «Забрать» появляется только после того, как всё показано (data-ready)
    assert '.chest-card[data-ready="1"] .chest-btn{opacity:1' in css


def test_chest_styles_are_light_for_weak_webviews():
    css = _chest_css()
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", css) and "backdrop-filter" not in css
    assert "infinite" not in css, "все анимации окна конечные"
    assert ".chest-overlay[hidden]{display:none !important}" in css
    for name in ("chestShake", "chestLid", "chestSpark", "chestPrize", "chestGlow"):
        assert f"@keyframes {name}" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".chest-card.is-final{" in css, "главный сундук отличается цветом"


# ---------------------------------------------------------------------------------------------------------------- учёт

def test_chest_event_is_labelled_and_belongs_to_the_quests_feature():
    from db.tracking import EVENT_LABELS
    from webapp.feature_map import FEATURES
    assert "chest_opened" in EVENT_LABELS
    owners = [f.id for f in FEATURES if "chest_opened" in f.events]
    assert owners == ["quests"]
    quests = next(f for f in FEATURES if f.id == "quests")
    assert "chest" in quests.screens and "chestClose" in quests.clicks
    assert SERVER.count('"chest_opened"') == 2, "и месяц, и парное задание пишут событие"


async def test_opening_a_month_chest_writes_the_event(client, uid):
    add_user(uid, f"u{uid}", "Игрок")
    _give_points(uid, 15)
    r = await client.post("/api/month-quests/claim", json={"at": 15}, headers=_headers(uid))
    assert r.status == 200
    conn = connect()
    try:
        rows = conn.execute("SELECT step FROM user_events WHERE user_id=? AND name='chest_opened'", (uid,)).fetchall()
    finally:
        conn.close()
    assert [row["step"] for row in rows] == ["month_15"]
    # повторное открытие ошибка — и второго события нет
    again = await client.post("/api/month-quests/claim", json={"at": 15}, headers=_headers(uid))
    assert again.status == 400
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM user_events WHERE user_id=? AND name='chest_opened'", (uid,)).fetchone()["n"]
    finally:
        conn.close()
    assert total == 1
