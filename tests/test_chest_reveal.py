"""
Окно «Сундук» (22.10, п.2 владельца; 07.11 — стиль «Обсидиан» и открытие «Заряд»): сундук зажимают и держат, пока кольцо не заполнится,
потом он трясётся, пока идёт запрос, крышка откидывается, вылетают искры, появляются награды и «Забрать». Плюс маленькая галочка на уже открытом сундуке дорожки месяца:
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
    for needle in ('id="chestCard"', 'data-phase="wait"', 'data-charging="0"', 'id="chestEyebrow"', 'class="chest-box"', 'class="chest-body"', 'class="chest-lid"',
                   'class="chest-ring"', 'class="chest-ring__fg"', 'class="chest-seam"', 'class="chest-ribbon"', 'class="chest-bow"', 'id="chestMark"',
                   'id="chestTitle"', 'id="chestSub"', 'id="chestHint"', 'id="chestPrizes"'):
        assert needle in block, needle
    assert 'id="chestClose"' in INDEX and "Не сейчас" in INDEX[INDEX.index('id="chestClose"'):][:80], "в ожидании тапов кнопка — «Не сейчас», «Забрать» она становится после открытия"
    stage = INDEX[INDEX.index('id="chestStage"') - 40:][:260]
    assert 'role="button"' in stage and 'tabindex="0"' in stage and "держи" in stage, "сундук — кнопка: зажать пальцем или держать Enter/Пробел"
    assert block.index('id="chestStage"') < block.index('class="chest-ring"') < block.index('id="chestTitle"') < block.index('id="chestHint"')
    assert block.count('class="chest-spark') >= 8, "искры вылетают при открытии"
    assert INDEX.index('id="chestOverlay"') > INDEX.index('id="dailyQuestsOverlay"'), "окно лежит в документе после квестов дня"


def test_the_flying_lid_stays_inside_the_drawing():
    """07.11, скрин владельца с iPhone: крышка при открытии обрезалась по верхнему краю рисунка — WebKit не рисует SVG за пределами
    viewBox, хотя overflow:visible. Отлетевшая крышка поднимается до y≈-61, поэтому у рисунка запас сверху."""
    assert '<svg class="chest-svg" viewBox="0 -70 200 270"' in INDEX
    box = re.search(r"\.chest-box\{([^}]*)\}", CSS).group(1)
    assert "height:202.5px" in box and "top:-27.5px" in box, "рамка выросла вверх, сундук остался по центру кольца"
    assert "@keyframes chestLid{to{transform:translate(-4px,-40px) rotate(-32deg)}}" in CSS


def test_script_and_style_versions_follow_the_release():
    assert "style.css?v=20261111_HUB_V101" in INDEX and "app.js?v=20261111_HUB_V120" in INDEX


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


def test_window_waits_for_a_full_charge_then_asks_the_server_shows_prizes_and_hands_errors_back():
    body = APP_JS[APP_JS.index("async function openChestReveal("):][:9800]
    assert "const CHEST_CHARGE_MS = 1200;" in APP_JS and "const CHEST_DRAIN_MS = 500;" in APP_JS and "CHEST_SHAKE_MS" in body
    # запрос уходит только после полного заряда: до этого «Не сейчас» закрывает окно, ничего не списывая
    shake = body.index('card.dataset.phase = "shake";')
    assert body.index("await new Promise((resolve) => {") < body.index('finish("charged")') < shake < body.index("result = await request();", shake)
    assert 'card.dataset.phase = "wait";' in body and 'card.dataset.phase = "shake";' in body
    assert 'if (outcome === "later") {' in body and "return null;" in body, "«Не сейчас» — без запроса и без награды"
    assert body.index('if (outcome === "later")') < shake
    assert body.index("catch (err)") < body.index("throw err;") < body.index("chestPrizeItems("), "ошибка запроса закрывает окно и уходит вызывающему"
    assert 'card.dataset.phase = "open";' in body
    lead = body.index('playChestSound("open");')
    assert lead < body.index("setTimeout(resolve, CHEST_OPEN_LEAD_MS)", lead) < body.index('card.dataset.phase = "open";') < body.index('haptic("triple");'), \
        "«вдох» звука начинается заранее, хлопок и тройная вибрация — вместе с отлётом крышки"
    assert 'card.dataset.ready = "1";' in body, "«Забрать» оживает только после наград"
    assert "if (closed || card.dataset.ready !== \"1\") return;" in body, "раньше времени окно не закрывается"
    assert 'e.key === "Escape"' in body and "removeEventListener" in body
    assert "reduceMotion" in body, "при «уменьшить движение» ждём недолго"


def test_holding_charges_the_ring_and_letting_go_drains_it():
    body = APP_JS[APP_JS.index("async function openChestReveal("):][:9800]
    hold = body[body.index("function frame(now) {"):][:1800]
    assert "charge + (holding ? dt / CHEST_CHARGE_MS : -dt / CHEST_DRAIN_MS)" in hold, "держишь — растёт, отпустил — спадает"
    assert 'card.style.setProperty("--charge", charge.toFixed(3));' in hold and "setRing(charge);" in hold
    assert "hapticKnock(step);" in hold and 'playChestSound("tap", step);' in hold, "на трети, двух третях и полном заряде — удар"
    assert "if (charge >= 1) { finish(\"charged\"); return; }" in hold
    assert "phase += dt * (0.010 + 0.030 * charge * charge);" in hold and "const amp = reduceHold ? 1.2 : 1.5 + 4.5 * charge;" in hold, \
        "пока держишь, сундук качается — к концу чаще и сильнее (07.11: просьба владельца, раньше стоял статично)"
    assert ".chest-card[data-phase=\"wait\"] .chest-box{animation" not in CSS, "анимация с fill на .chest-box перекрыла бы тряску из скрипта"
    assert "stage.setPointerCapture(e.pointerId)" in body, "лёгкий сдвиг пальца не обрывает заряд"
    assert '"pointerup", "pointercancel", "lostpointercapture"' in body
    assert 'stage.addEventListener("contextmenu", onMenu);' in body, "долгое нажатие не открывает меню"
    assert '(e.key === "Enter" || e.key === " ") && document.activeElement === stage' in body and "if (!e.repeat) { unlockChestAudio(); start(); }" in body, "с клавиатуры — держать Enter или Пробел"
    assert 'CHEST_HINTS = { idle: "Зажми и держи", hold: "Держи…", early: "Почти! Держи дольше" }' in APP_JS
    sound = APP_JS[APP_JS.index("function playChestSound("):][:1800]
    assert "chestNote([12, 16, 19][step - 1])" in sound, "удары «Нарастающий тон»: до, ми, соль — каждый шаг выше (выбор владельца 07.11)"
    assert 's.boom(.22,' in sound and 'type: "highpass"' in sound, "открытие «Вспышка»: вдох, хлопок с басом, воздух"
    assert "const CHEST_OPEN_LEAD_MS = 220;" in APP_JS
    assert body.count("unlockChestAudio();") == 2, "звук будится в самом жесте (палец и клавиатура) — иначе iPhone молчит"
    assert "if (evolutionSilence) return;" in APP_JS[APP_JS.index("function unlockChestAudio("):][:120]
    assert "window.AdamHaptics.knock(n)" in APP_JS[APP_JS.index("function hapticKnock("):][:200]


def test_each_chest_kind_has_its_own_colour_and_mark_that_the_royal_theme_cannot_repaint():
    """07.11: «Royal Gold» (royal.js) перекрашивает синие и фиолетовые цвета таблиц стилей в золото — сталь и сундук месяца становились
    золотыми, как золотой квест. Цвет ценности задаётся инлайн из скрипта, его royal.js не трогает."""
    assert 'const CHEST_ACCENTS = { wood: "#D08A4C", blue: "#B9CDE8", gold: "#FFCF4A", month: "#A98BFF", final: "#FF5A5F", pair: "#3FE0C5" };' in APP_JS
    body = APP_JS[APP_JS.index("async function openChestReveal("):][:3000]
    assert 'const kind = final ? "final" : (["wood", "blue", "gold", "pair"].includes(tone) ? tone : "month");' in body
    assert 'card.style.setProperty("--acc", CHEST_ACCENTS[kind]);' in body and "mark.innerHTML = chestMarkSvg(kind);" in body
    marks = APP_JS[APP_JS.index("function chestMarkSvg("):][:1200]
    for kind in ("wood:", "blue:", "gold:", "month:", "final:", "pair:"):
        assert kind in marks, kind
    side = APP_JS[APP_JS.index("function questSideHtml("):][:1400]
    assert side.count('style="--acc:${CHEST_ACCENTS[kind] || CHEST_ACCENTS.wood}"') == 3, "маленькие сундуки квестов — тоже инлайн"
    pair = APP_JS[APP_JS.index("async function pairCardAction"):][:2400]
    assert 'tone: "pair"' in pair


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
    for name in ("chestDrop", "chestInvite", "chestBurst"):
        assert f"@keyframes {name}" in css, name
    assert ".chest-card[data-phase=\"wait\"] .chest-svg{animation:chestDrop" in css, "сундук «появляется» (у рисунка, не у .chest-box — иначе тряска заряда не видна)"
    assert "opacity:calc(.1 + var(--charge) * .45)" in css and ".chest-seam{opacity:calc(.55 + var(--charge) * .45)}" in css, "свечение и щель растут с зарядом"
    assert ".chest-ring__fg{stroke:var(--acc)" in css, "кольцо заряда — цвета ценности сундука"
    assert ".chest-card:not([data-phase=\"open\"]) .chest-prizes{display:none}" in css
    stage = css[css.index(".chest-stage{"):][:400]
    assert "cursor:pointer" in stage and "touch-action:none" in stage and "-webkit-touch-callout:none" in stage, "долгое нажатие не листает и не выделяет"
    assert ".chest-card[data-phase=\"wait\"] .chest-btn{opacity:1" in css, "«Не сейчас» видна, пока ждём заряд"
    assert re.search(r"chestInvite 1\.1s ease-in-out 1s 3\}", css), "подсказка-«приглашение» конечная: три раза"


def test_the_button_does_not_flash_gold_while_the_chest_shakes_and_the_card_does_not_jump():
    """23.10, видео владельца: после третьего тапа «Не сейчас» на 0,25 с становилась золотой «Забрать» и гасла (правило фазы wait
    переставало действовать, а цвет переключался мгновенно), а при открытии карточка схлопывалась на 22 px (пропадала шкала и рамка)."""
    css = _chest_css()
    ghost = re.search(r'\.chest-card\[data-phase="wait"\] \.chest-btn,\.chest-card\[data-phase="shake"\] \.chest-btn\{([^}]*)\}', css)
    assert ghost, "пока сундук трясётся, кнопка всё ещё «Не сейчас»: бесцветная и гаснет"
    assert "background:transparent" in ghost.group(1) and "color:#A8A6B8" in ghost.group(1)
    base = re.search(r"\.chest-btn\{([^}]*)\}", css).group(1)
    assert "border:0" in base and "opacity:0" in base, "обычная (золотая) кнопка скрыта, пока не готова"
    assert "border:" not in ghost.group(1).replace("border-radius", ""), "рамка — тенью: высота кнопки не меняется между фазами"
    assert "inset 0 0 0 1px" in ghost.group(1)
    assert '.chest-card[data-phase="open"] .chest-hint{display:none}' in css, "подсказку заменяют награды той же высоты"
    assert re.search(r"\.chest-hint\{[^}]*min-height:96px", css) and re.search(r"\.chest-prizes\{[^}]*min-height:96px", css)
    # золотая «Забрать» появляется только после того, как всё показано (data-ready)
    assert '.chest-card[data-ready="1"] .chest-btn{opacity:1' in css


def test_chest_styles_are_light_for_weak_webviews():
    css = _chest_css()
    assert not re.search(r"(?<![\w-])(?:-webkit-)?filter\s*:", css) and "backdrop-filter" not in css
    assert "infinite" not in css, "все анимации окна конечные"
    assert ".chest-overlay[hidden]{display:none !important}" in css
    for name in ("chestShake", "chestLid", "chestSpark", "chestPrize", "chestBurst"):
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
