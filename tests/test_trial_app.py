"""
Триал в приложении (ТЗ владельца: «окно о том, что триал закончился», серия сохранилась или нет, последний день скидки, дальше полная цена):
маршруты, доступные и закрытому аккаунту, карточка на Главной, окно «Мой пробный период», блокирующее окно с оплатой Stars, симуляции.
Тексты окон прогоняем в Node на заглушках DOM — те же правила, что у остальных окон: от первого лица Адама, без повторов слов.
"""
import itertools
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from db import add_user, start_trial_mode
from db.access import shift_trial_day
from db.core import connect
from db.sim_lab import SCENES
from tests.conftest import sign_init_data
from tests.test_window_copy import shared

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")
_counter = itertools.count(859_900_000, 100)
ADMIN = 777


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    conn.execute(query, params)
    conn.commit()
    conn.close()


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _person(uid_, day=None):
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    if day:
        start_trial_mode(uid_, ADMIN)
        shift_trial_day(uid_, day, ADMIN)
    return uid_


class _Bot:
    token = ""

    def __init__(self):
        self.create_invoice_link = AsyncMock(return_value="https://t.me/$invoice")


# ---------------------------------------------------------------------------------------------------------------- сервер

async def test_a_locked_account_still_sees_its_trial_and_can_pay(client, uid):
    person = _person(uid, day=4)
    client.app["bot"] = _Bot()
    locked = await client.get("/api/bootstrap", headers=_headers(person))
    body = await locked.json()
    assert locked.status == 403 and body["error"] == "trial_expired"
    assert body["trial"]["phase"] == "expired_intro" and body["trial"]["price_stars"] == 153 and body["trial"]["intro_active"] is True
    status = await client.get("/api/trial", headers=_headers(person))
    data = await status.json()
    assert status.status == 200 and data["allowed"] is False and data["trial"]["day"] == 4
    invoice = await client.post("/api/subscription/invoice", headers=_headers(person))
    assert invoice.status == 200
    result = await invoice.json()
    assert result == {"ok": True, "invoice_url": "https://t.me/$invoice", "price_stars": 153}
    kwargs = client.app["bot"].create_invoice_link.await_args.kwargs
    assert kwargs["currency"] == "XTR" and kwargs["payload"] == f"subscription:{person}" and kwargs["prices"][0].amount == 153
    assert "Первый месяц по особой цене" in kwargs["description"] and kwargs["provider_token"] == ""


@pytest.mark.parametrize("day,price", [(1, 153), (3, 153), (4, 153), (5, 461), (9, 461)])
async def test_the_invoice_amount_follows_the_day(client, uid, day, price):
    person = _person(uid, day=day)
    client.app["bot"] = _Bot()
    resp = await client.post("/api/subscription/invoice", headers=_headers(person))
    assert (await resp.json())["price_stars"] == price
    assert client.app["bot"].create_invoice_link.await_args.kwargs["prices"][0].amount == price
    if price == 461:
        assert "Первый месяц" not in client.app["bot"].create_invoice_link.await_args.kwargs["description"]


async def test_testers_and_payers_cannot_get_an_invoice(client, uid):
    client.app["bot"] = _Bot()
    tester = _person(uid)
    resp = await client.post("/api/subscription/invoice", headers=_headers(tester))
    assert resp.status == 403 and (await resp.json())["error"] == "not_available", "тестировщик вне триала счёт не получает"
    assert client.app["bot"].create_invoice_link.await_count == 0
    payer = _person(uid + 1, day=2)
    _sql("UPDATE users SET subscription_paid_until=datetime('now', '+20 days') WHERE telegram_id=?", (payer,))
    resp = await client.post("/api/subscription/invoice", headers=_headers(payer))
    assert resp.status == 400 and (await resp.json())["error"] == "already_paid"
    no_bot = _person(uid + 2, day=2)
    client.app["bot"] = None
    resp = await client.post("/api/subscription/invoice", headers=_headers(no_bot))
    assert resp.status == 503


async def test_bootstrap_carries_the_trial_only_for_the_scenario(client, uid):
    tester = _person(uid)
    data = await (await client.get("/api/bootstrap", headers=_headers(tester))).json()
    assert "trial" not in data["access"] and data["access"]["paywall"] is False
    person = _person(uid + 1, day=2)
    data = await (await client.get("/api/bootstrap", headers=_headers(person))).json()
    trial = data["access"]["trial"]
    assert data["access"]["paywall"] is True and trial["day"] == 2 and trial["phase"] == "day2" and trial["intro_active"] is True
    assert "user_id" not in trial and "name" not in trial and "timezone" not in trial, "наружу — только то, что нужно окнам"


async def test_the_trial_routes_need_a_login(client):
    assert (await client.get("/api/trial")).status in (401, 403)
    assert (await client.post("/api/subscription/invoice")).status in (401, 403)


# ---------------------------------------------------------------------------------------------------------------- разметка и скрипт

def test_markup_has_the_card_the_sheet_and_the_blocking_window():
    for needle in ('id="trialCard"', 'id="trialCardVoice"', 'id="trialCardTitle"', 'id="trialCardSub"', 'id="trialCardDays"',
                   'class="feedback-sheet trial-sheet" id="trialSheet" hidden', 'id="trialSheetBackdrop"', 'id="trialSheetDays"', 'id="trialSheetRule"',
                   'id="trialSheetOffer"', 'id="trialSheetLater"', 'id="trialSheetPay"',
                   'class="trial-paywall" id="trialPaywallOverlay" hidden', 'role="dialog" aria-modal="true" aria-labelledby="trialPaywallTitle"',
                   'id="trialPaywallLead"', 'id="trialPaywallOffer"', 'id="trialPaywallPay"', 'id="trialPaywallClose"'):
        assert needle in INDEX, needle
    assert INDEX.index('id="trialCard"') < INDEX.index('id="todayFocus"'), "карточка — первая на Главной"
    assert "команда /paysupport" in INDEX and "Telegram Stars" in INDEX


def test_the_script_wires_the_windows_into_the_app():
    assert "renderTodayFocus();\n    renderTrialCard();" in APP_JS.replace("\r\n", "\n")
    assert 'if (res.status === 403 && code === "trial_expired") showTrialPaywall(data?.trial);' in APP_JS
    assert 'if (err?.data?.error === "trial_expired") return;' in APP_JS
    assert "initHabitEdit();\n            initTrial();" in APP_JS.replace("\r\n", "\n")
    assert '"trialSheet", "trialPaywallOverlay",' in APP_JS
    pay = APP_JS[APP_JS.index("async function payTrial(source)"):][:2400]
    assert pay.index("if (trialDemo)") < pay.index('api("/api/subscription/invoice"'), "в демо ничего не оплачивается"
    assert 'tg.openInvoice(invoice.invoice_url' in pay and 'status !== "paid"' in pay and "waitForTrialAccess()" in pay
    assert 'trk("trial_pay_tap"' in pay and 'trk("trial_paid"' in pay and 'trk("trial_sheet_open"' in APP_JS and 'trk("trial_paywall_shown"' in APP_JS
    assert "const SIM_TRIAL = {" in APP_JS and "SIM_TRIAL_INTRO_EXPIRED" in APP_JS and "SIM_TRIAL_FULL_EXPIRED" in APP_JS


def test_the_simulations_are_listed_and_run_the_real_windows():
    keys = [row[0] for row in SCENES]
    assert {"trial_card", "trial_sheet", "trial_paywall_intro", "trial_paywall_full"} <= set(keys)
    for needle in ("trial_card: () => showTrialCardDemo(SIM_TRIAL),", "trial_paywall_intro: () => showTrialPaywall(SIM_TRIAL_INTRO_EXPIRED, { demo: true }),",
                   "trial_paywall_full: () => showTrialPaywall(SIM_TRIAL_FULL_EXPIRED, { demo: true }),", "openTrialSheet({ demo: SIM_TRIAL })"):
        assert needle in APP_JS, needle
    play = APP_JS[APP_JS.index("function playSimScene() {"):][:260]
    assert "closeTrialPaywall();" in play and "closeTrialSheet();" in play, "окна триала не перекрывают следующую сцену"
    assert "z-index:10300" in CSS[CSS.index(".lab-bar{"):][:200], "панель симуляций выше блокирующего окна"


def test_the_styles_are_light_and_the_block_sits_before_the_chest():
    css = CSS[CSS.index("Триал (app.js::renderTrialCard"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    for needle in (".trial-card{", ".trial-dot.is-now", ".trial-days{", ".trial-rule__bar", ".trial-offer__tag", ".trial-paywall{", ".trial-paywall__pay{",
                   "background:#07050d", "z-index:10080"):
        assert needle in css, needle
    for forbidden in ("filter", "backdrop-filter", "infinite", "animation"):
        assert forbidden not in css, forbidden
    assert "Окно «Сундук»" not in css.split("*/", 1)[1]
    assert "style.css?v=20261021_QUESTS_V79" in INDEX and "app.js?v=20261021_QUESTS_V89" in INDEX


# ---------------------------------------------------------------------------------------------------------------- тексты (Node на заглушках DOM)

def _source(marker_start, marker_end):
    return APP_JS[APP_JS.index(marker_start):APP_JS.index(marker_end)]


HARNESS = r"""
const els = {};
const el = (id) => (els[id] = els[id] || { id, innerHTML: "", textContent: "", hidden: true, disabled: false, style: {},
  classList: { add() {}, remove() {}, contains() { return false; } }, setAttribute() {}, addEventListener() {}, querySelector() { return null; } });
const document = { getElementById: el };
const state = { access: { paywall: true, trial: null } };
const requestAnimationFrame = (fn) => fn();
const haptic = () => {}; const trk = () => {}; const hideProductHint = () => {};
const adamVoiceHtml = (size, sticker) => `<voice ${size} ${sticker || ""}>`;
%(helpers)s
%(block)s
const cases = %(cases)s;
const out = [];
for (const t of cases) {
  state.access.trial = t;
  for (const k of Object.keys(els)) { els[k].innerHTML = ""; els[k].textContent = ""; }
  renderTrialCard(); renderTrialSheet(t); showTrialPaywall(t, { demo: true });
  const strip = (s) => s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  out.push({
    card: { hidden: els.trialCard.hidden, title: els.trialCardTitle.textContent, sub: els.trialCardSub.textContent, dots: els.trialCardDays.innerHTML.match(/trial-dot [a-z-]*/g) },
    sheet: { days: strip(els.trialSheetDays.innerHTML), rule: strip(els.trialSheetRule.innerHTML), offer: strip(els.trialSheetOffer.innerHTML),
             pay: els.trialSheetPay.textContent, payHidden: !!els.trialSheetPay.hidden, later: els.trialSheetLater.textContent },
    paywall: { lead: els.trialPaywallLead.textContent, offer: strip(els.trialPaywallOffer.innerHTML), pay: els.trialPaywallPay.textContent, shown: !els.trialPaywallOverlay.hidden },
  });
}
process.stdout.write(JSON.stringify(out));
"""


def _run(cases):
    text = APP_JS.replace("\r\n", "\n")

    def _function(header):
        start = text.index(header)
        return text[start:text.index("\n  }\n", start) + 5]

    helpers = _function("  function pluralRu(n, one, few, many) {")
    esc = _function("  function escapeHtml(value) {")
    block = _source("function trialDays(n) {", "// Симуляция «карточка на Главной»")
    script = HARNESS % {"helpers": helpers + "\n" + esc, "block": block, "cases": json.dumps(cases)}
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _t(**kw):
    base = {"scenario": True, "day": 2, "days_total": 3, "phase": "day2", "paid": False, "intro_active": True, "price_stars": 153, "intro_price_stars": 153,
            "renewal_price_stars": 461, "intro_usd": "$1.99", "renewal_usd": "$5.99", "streak": 0, "needed": 2, "marked_today": False,
            "marked_yesterday": False, "channel_configured": True, "channel_granted": False, "channel_eligible": False}
    base.update(kw)
    return base


CASES = [
    _t(day=1, phase="day1"),
    _t(),
    _t(marked_yesterday=True, streak=1),
    _t(marked_today=True, streak=1),
    _t(streak=2, marked_today=True, marked_yesterday=True, channel_eligible=True),
    _t(day=3, phase="day3", streak=1, marked_yesterday=True),
    _t(day=4, phase="expired_intro", streak=2, channel_eligible=True),
    _t(day=4, phase="expired_intro", streak=0),
    _t(day=6, phase="expired_full", intro_active=False, price_stars=461),
    _t(day=2, phase="paid", paid=True, streak=1),
    _t(day=2, phase="paid", paid=True, streak=2, channel_eligible=True, channel_granted=True),
    _t(day=2, phase="paid", paid=True, channel_configured=False),
]


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_card_follows_the_day_and_the_goal():
    out = _run(CASES)
    day1, day2, with_yesterday, with_today, ready, day3, expired_a, expired_b, full, paid, granted, no_channel = out
    assert day1["card"]["title"] == "Пробный период · день 1/3" and day1["card"]["dots"] == ["trial-dot is-now", "trial-dot ", "trial-dot "]
    assert day2["card"]["sub"] == "Цель — два дня подряд: они открывают путь в закрытый канал"
    assert with_yesterday["card"]["sub"] == "Отметь привычку — и два дня подряд будут твоими"
    assert with_today["card"]["sub"] == "Сегодня отмечено — завтра отметь привычку ещё раз"
    assert ready["card"]["sub"] == "Два дня подряд уже есть — канал откроется после оплаты"
    assert day3["card"]["title"] == "Последний день пробного периода" and "особая цена 153 ⭐" in day3["card"]["sub"]
    assert day2["card"]["dots"] == ["trial-dot is-done", "trial-dot is-now", "trial-dot "]
    assert expired_a["card"]["hidden"] and expired_b["card"]["hidden"] and full["card"]["hidden"], "после триала карточки нет — есть окно оплаты"
    assert paid["card"]["title"] == "Pro активен" and "До закрытого канала — 1 день ударного режима" in paid["card"]["sub"]
    assert granted["card"]["hidden"] and no_channel["card"]["hidden"], "канал выдан или не подключён — карточку не показываем"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_sheet_shows_days_the_rule_and_the_price():
    out = _run(CASES)
    day2, ready, paid = out[1], out[4], out[9]
    assert day2["sheet"]["days"] == "День 1 позади День 2 сегодня День 3 впереди"
    assert day2["sheet"]["rule"] == "Закрытый канал Открывается за 2 дня ударного режима подряд — у тебя 0 из 2."
    assert day2["sheet"]["offer"] == "Pro-доступ · 153 ⭐ ≈ $1.99 за первый месяц по особой цене, дальше $5.99."
    assert day2["sheet"]["pay"] == "Оформить за 153 ⭐" and day2["sheet"]["later"] == "Позже" and day2["sheet"]["payHidden"] is False
    assert "Условие выполнено: два дня подряд у тебя уже есть." in ready["sheet"]["rule"]
    assert paid["sheet"]["offer"] == "Pro активен Спасибо, что остаёшься со мной." and paid["sheet"]["payHidden"] is True and paid["sheet"]["later"] == "Хорошо"
    assert paid["sheet"]["days"] == "День 1 позади День 2 позади День 3 позади"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_blocking_window_tells_the_streak_and_the_price_of_today():
    out = _run(CASES)
    intro_kept, intro_empty, full = out[6]["paywall"], out[7]["paywall"], out[8]["paywall"]
    assert intro_kept["shown"] and intro_kept["lead"] == "Твой ударный режим — 2 дня подряд — сохранился. Вернись ко мне, пока он не пропал."
    assert intro_kept["offer"] == "Последний день скидки 153 ⭐ ≈ $1.99 за первый месяц, завтра — 461 ⭐ ($5.99)" and intro_kept["pay"] == "Оформить за 153 ⭐"
    assert intro_empty["lead"] == "Ударный режим начнётся, как только ты вернёшься."
    assert full["offer"] == "461 ⭐ ≈ $5.99 за месяц доступа" and full["pay"] == "Оформить за 461 ⭐" and "скидки" not in full["offer"].lower()


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_no_window_repeats_its_own_words_or_speaks_in_the_third_person():
    for case, result in zip(CASES, _run(CASES)):
        sheet, paywall = result["sheet"], result["paywall"]
        assert not shared("Мой пробный период", sheet["rule"], sheet["offer"], sheet["pay"], sheet["later"]), (case["phase"], sheet)
        assert not shared("Пробный период закончился", paywall["lead"], paywall["offer"], paywall["pay"]), (case["phase"], paywall)
        for text in (sheet["rule"], sheet["offer"], paywall["lead"], result["card"]["sub"]):
            assert not re.search(r"\bбот\w*|\bсистем\w*|\bAI\b|Адам написал|ADAM будет", text), text
    assert not shared("Пробный период закончился", "Продолжу планировать твой день и разбирать цели"), "список выгод не повторяет заголовок"
