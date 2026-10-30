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
    readable = await client.get("/api/bootstrap", headers=_headers(person))
    assert readable.status == 200, "дополнительное окно: данные и история видны (только чтение)"
    assert (await readable.json())["access"]["state"] == "grace"
    locked = await client.post("/api/habits", headers=_headers(person), json={"title": "Новая привычка"})
    body = await locked.json()
    assert locked.status == 403 and body["error"] == "trial_expired", "а новые действия закрыты"
    assert body["trial"]["phase"] == "grace" and body["trial"]["price_stars"] == 153 and body["trial"]["intro_active"] is True
    status = await client.get("/api/trial", headers=_headers(person))
    data = await status.json()
    assert status.status == 200 and data["allowed"] is False and data["trial"]["day"] == 4
    invoice = await client.post("/api/subscription/invoice", headers=_headers(person))
    assert invoice.status == 200
    result = await invoice.json()
    assert result == {"ok": True, "invoice_url": "https://t.me/$invoice", "price_stars": 153, "recurring": False}
    kwargs = client.app["bot"].create_invoice_link.await_args.kwargs
    assert kwargs["currency"] == "XTR" and kwargs["payload"] == f"subscription:{person}" and kwargs["prices"][0].amount == 153
    assert "Первый месяц по вводной цене" in kwargs["description"] and "канал" not in kwargs["description"] and kwargs["provider_token"] == ""
    assert "subscription_period" not in kwargs, "первая покупка — разовый счёт на 30 дней (Stars не умеют другую цену первого периода внутри подписки)"


@pytest.mark.parametrize("day,price", [(1, 153), (3, 153), (4, 153), (5, 153), (9, 153)])
async def test_the_invoice_amount_follows_the_day(client, uid, day, price):
    person = _person(uid, day=day)
    client.app["bot"] = _Bot()
    resp = await client.post("/api/subscription/invoice", headers=_headers(person))
    assert (await resp.json())["price_stars"] == price
    assert client.app["bot"].create_invoice_link.await_args.kwargs["prices"][0].amount == price
    assert "Первый месяц по вводной цене" in client.app["bot"].create_invoice_link.await_args.kwargs["description"], "вводная цена — для первой покупки в любой день"


async def test_testers_and_payers_cannot_get_an_invoice(client, uid):
    client.app["bot"] = _Bot()
    tester = _person(uid)
    resp = await client.post("/api/subscription/invoice", headers=_headers(tester))
    assert resp.status == 200 and (await resp.json())["ok"] is True, "решение владельца 28.10: заплатить может любой, кто захотел, даже вне сценария триала"
    assert client.app["bot"].create_invoice_link.await_count == 1
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

def test_markup_has_the_card_the_timer_and_the_subscription_screen():
    for needle in ('id="trialCard"', 'id="trialCardVoice"', 'id="trialCardTitle"', 'id="trialCardSub"', 'id="trialCardDays"', 'id="trialCardTimer"', 'id="trialCardHint"',
                   'class="trial-paywall" id="trialPaywallOverlay" hidden', 'role="dialog" aria-modal="true" aria-labelledby="trialPaywallTitle"',
                   'id="trialPaywallStatus"', 'id="trialPaywallLead"', 'id="trialPaywallPerks"', 'id="trialPaywallOffer"', 'id="trialPaywallPay"',
                   'id="trialPaywallPending"', 'id="trialPaywallRenewal"', 'id="trialPaywallRenewToggle"', 'id="trialSummary"', 'id="trialPaywallSummaryBtn"',
                   'id="trialMission"', 'id="trialMissionText"', 'id="trialPaywallClose"', 'id="trialPaywallNote"',
                   'id="subscriptionSettingsRow"', 'id="subscriptionSettingsBtn"'):
        assert needle in INDEX, needle
    assert INDEX.index('id="trialCard"') < INDEX.index('id="todayFocus"'), "карточка — первая на Главной"
    assert 'id="trialSheet"' not in INDEX, "окно «Мой пробный период» заменено экраном подписки"
    screen = INDEX[INDEX.index('id="trialPaywallOverlay"'):][:2400]
    assert ">Продолжить с Адамом</button>" in screen and "<summary>О проекте и миссии</summary>" in screen and ">Показать короткую сводку</button>" in screen
    assert "канал" not in screen.lower() and "Premium" not in screen and "форум" not in screen.lower(), "закрытый канал, форумы и Premium не продаём"


def test_the_script_wires_the_screen_into_the_app():
    assert "renderTodayFocus();\n    renderTrialCard();" in APP_JS.replace("\r\n", "\n")
    assert 'if (res.status === 403 && code === "trial_expired") showTrialPaywall(data?.trial);' in APP_JS
    assert 'if (err?.data?.error === "trial_expired") return;' in APP_JS
    assert "initHabitEdit();\n            initTrial();" in APP_JS.replace("\r\n", "\n")
    assert "maybeShowGraceScreen();" in APP_JS
    assert '"habitUndoSheet", "trialPaywallOverlay",' in APP_JS
    pay = APP_JS[APP_JS.index("async function payTrial(source)"):][:3400]
    assert pay.index("if (trialDemo)") < pay.index('api("/api/subscription/invoice"'), "в демо ничего не оплачивается"
    assert 'tg.openInvoice(invoice.invoice_url' in pay and 'status !== "paid"' in pay and "waitForTrialAccess()" in pay
    assert "res?.trial?.paid" in APP_JS[APP_JS.index("async function waitForTrialAccess()"):][:400], "ждём именно подтверждённую подписку, а не просто «доступ есть»"
    for event in ("trial_pay_tap", "trial_paid", "trial_paywall_shown", "sub_screen_open", "sub_summary_open", "sub_mission_open", "sub_not_now", "trial_timer_shown", "trial_timer_tap"):
        assert f'trk("{event}"' in APP_JS, event
    assert "const SIM_TRIAL = {" in APP_JS and "SIM_TRIAL_INTRO_EXPIRED" in APP_JS and "SIM_TRIAL_FULL_EXPIRED" in APP_JS
    assert "Повторно платить не нужно" in APP_JS, "при ожидании подтверждения повторно платить не просим"


def test_the_simulations_are_listed_and_run_the_real_windows():
    keys = [row[0] for row in SCENES]
    wanted = {"trial_card", "trial_sheet", "trial_paywall_intro", "trial_paywall_full", "trial_timer", "sub_paid", "sub_pending", "sub_summary"}
    assert wanted <= set(keys)
    for needle in ("trial_card: () => showTrialCardDemo(SIM_TRIAL),", "trial_timer: () => showTrialCardDemo(SIM_TRIAL_LAST),",
                   "trial_paywall_intro: () => showTrialPaywall(SIM_TRIAL_INTRO_EXPIRED, { demo: true }),",
                   "trial_paywall_full: () => showTrialPaywall(SIM_TRIAL_FULL_EXPIRED, { demo: true }),", "openSubscriptionScreen({ source: \"sim\"", "showTrialSummary()"):
        assert needle in APP_JS, needle
    play = APP_JS[APP_JS.index("function playSimScene() {"):][:260]
    assert "closeTrialPaywall();" in play, "окна триала не перекрывают следующую сцену"
    assert "z-index:10300" in CSS[CSS.index(".lab-bar{"):][:200], "панель симуляций выше блокирующего окна"


def test_the_styles_are_light_and_the_block_sits_before_the_chest():
    css = CSS[CSS.index("Триал (app.js::renderTrialCard"):CSS.index("Окно «Сундук» (открытие сундука месяца")]
    for needle in (".trial-card{", ".trial-card__timer{", ".trial-dot.is-now", ".trial-paywall{", ".trial-paywall__pay{", ".trial-summary{", ".trial-mission{",
                   "background:#07050d", "z-index:10080"):
        assert needle in css, needle
    for forbidden in ("filter", "backdrop-filter", "infinite", "animation"):
        assert forbidden not in css, forbidden
    assert "Окно «Сундук»" not in css.split("*/", 1)[1]
    assert "style.css?v=20261028_HUB_V89" in INDEX and "app.js?v=20261030_HUB_V104" in INDEX


# ---------------------------------------------------------------------------------------------------------------- тексты (Node на заглушках DOM)

def _source(marker_start, marker_end):
    return APP_JS[APP_JS.index(marker_start):APP_JS.index(marker_end)]


HARNESS = r"""
const els = {};
const el = (id) => (els[id] = els[id] || { id, innerHTML: "", textContent: "", hidden: true, disabled: false, style: {}, scrollTop: 0,
  classList: { add() {}, remove() {}, contains() { return false; } }, setAttribute() {}, addEventListener() {}, querySelector() { return null; }, scrollIntoView() {} });
const document = { getElementById: el };
let state = { access: { paywall: true, trial: null } };
const requestAnimationFrame = (fn) => fn();
const haptic = () => {}; const trk = () => {}; const hideProductHint = () => {};
const localStorage = { getItem() { return null; }, setItem() {} };
const setInterval = () => 1; const clearInterval = () => {};
const adamVoiceHtml = (size, sticker) => `<voice ${size} ${sticker || ""}>`;
const api = async () => ({}); const showToast = () => {}; const tg = null; const friendlyError = () => ""; const loadBootstrap = async () => {};
%(helpers)s
%(block)s
const cases = %(cases)s;
const out = [];
(async () => {
  for (const item of cases) {
    const t = item.trial, ov = item.overview || { auto_renew: false, can_toggle: false, autorenew_available: false, paid_until_text: "30 ноября, 00:00", support: "@egor_support" };
    state.access.trial = t;
    for (const k of Object.keys(els)) { els[k].innerHTML = ""; els[k].textContent = ""; }
    renderTrialCard();
    await openSubscriptionScreen({ source: "sim", demo: t, overview: ov, blocking: !!item.blocking });
    const strip = (s) => s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    out.push({
      card: { hidden: els.trialCard.hidden, title: els.trialCardTitle.textContent, sub: els.trialCardSub.textContent, dots: els.trialCardDays.innerHTML.match(/trial-dot [a-z-]*/g),
              timer: els.trialCardTimer.hidden ? null : els.trialCardTimer.textContent, hint: els.trialCardHint.hidden ? null : els.trialCardHint.textContent,
              settingsRowHidden: !!els.subscriptionSettingsRow.hidden },
      screen: { title: els.trialPaywallTitle.textContent, status: els.trialPaywallStatus.textContent, lead: els.trialPaywallLead.textContent, offer: strip(els.trialPaywallOffer.innerHTML),
                offerHidden: !!els.trialPaywallOffer.hidden, perks: strip(els.trialPaywallPerks.innerHTML), pay: els.trialPaywallPay.textContent, payHidden: !!els.trialPaywallPay.hidden,
                payDisabled: !!els.trialPaywallPay.disabled, renewal: els.trialPaywallRenewal.textContent, pending: els.trialPaywallPending.hidden ? null : els.trialPaywallPending.textContent,
                toggle: els.trialPaywallRenewToggle.hidden ? null : els.trialPaywallRenewToggle.textContent, shown: !els.trialPaywallOverlay.hidden,
                close: els.trialPaywallClose.hidden ? null : els.trialPaywallClose.textContent, mission: strip(els.trialMissionText.innerHTML), note: strip(els.trialPaywallNote.innerHTML) },
    });
  }
  renderTrialSummary({ title: "Коротко за 3 дня с Адамом", lines: ["Привычки: отмечено 5 раз в 3 из 3 дней.", "План дня: добавлено задач — 4, выполнено — 2."], note: "После семи и больше дней..." });
  out.summary = { text: els.trialSummary.innerHTML.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(), shown: !els.trialSummary.hidden, button: !!els.trialPaywallSummaryBtn.hidden };
  process.stdout.write(JSON.stringify({ cases: out, summary: out.summary }));
})();
"""


def _run(cases):
    text = APP_JS.replace("\r\n", "\n")

    def _function(header):
        start = text.index(header)
        return text[start:text.index("\n  }\n", start) + 5]

    helpers = _function("  function pluralRu(n, one, few, many) {")
    esc = _function("  function escapeHtml(value) {")
    block = _source("const SIM_TRIAL = {", "// Симуляция «карточка на Главной»")
    script = HARNESS % {"helpers": helpers + "\n" + esc, "block": block, "cases": json.dumps(cases)}
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _t(**kw):
    base = {"scenario": True, "day": 2, "days_total": 3, "phase": "day2", "state": "trial", "paid": False, "read_only": False, "intro_active": True, "price_stars": 153,
            "intro_price_stars": 153, "renewal_price_stars": 461, "intro_usd": "$1.99", "renewal_usd": "$5.99", "streak": 0, "needed": 2, "marked_today": False,
            "marked_yesterday": False, "channel_configured": False, "channel_granted": False, "channel_eligible": False, "ends_at_text": "30 октября, 00:00",
            "ends_at": None, "payment_pending": False}
    base.update(kw)
    return base


def _case(trial, overview=None, blocking=False):
    return {"trial": trial, "overview": overview, "blocking": blocking}


CASES = [
    _case(_t(day=1, phase="day1")),                                                                                                       # 0 день 1
    _case(_t()),                                                                                                                         # 1 день 2
    _case(_t(day=3, phase="day3", timer_demo_ms=5 * 3600000 + 20 * 60000)),                                                              # 2 последние сутки, таймер
    _case(_t(day=4, phase="grace", state="grace", read_only=True, ends_at_text="29 октября, 00:00", streak=2, timer_demo_ms=14 * 3600000 + 5 * 60000), blocking=True),   # 3 окно
    _case(_t(day=6, phase="expired", state="expired"), blocking=True),                                                                    # 4 закрыто
    _case(_t(day=2, phase="paid", state="paid", paid=True, intro_active=False), overview={"auto_renew": True, "can_toggle": True, "autorenew_available": True,
                                                                                         "paid_until_text": "30 ноября, 00:00", "support": "@egor_support"}),   # 5 оплачено, автопродление
    _case(_t(day=2, phase="paid", state="paid", paid=True, intro_active=False)),                                                          # 6 оплачено вручную
    _case(_t(payment_pending=True), overview={"payment_pending": True, "support": "@egor_support", "autorenew_available": False}),         # 7 ждём подтверждение
    _case(_t(intro_active=False, price_stars=461)),                                                                                       # 8 уже платил раньше
]


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_card_shows_the_term_the_dots_and_the_timer_only_when_it_is_needed():
    cards = [item["card"] for item in _run(CASES)["cases"]]
    assert cards[0]["title"] == "Пробный период · день 1/3" and cards[0]["sub"] == "Доступ открыт до 30 октября, 00:00" and cards[0]["timer"] is None
    assert cards[0]["dots"] == ["trial-dot is-now", "trial-dot ", "trial-dot "] and cards[0]["settingsRowHidden"] is False, "кнопка подписки в настройках — с первого дня"
    assert cards[1]["dots"] == ["trial-dot is-done", "trial-dot is-now", "trial-dot "] and cards[1]["timer"] is None
    assert cards[2]["title"] == "Последние сутки пробного периода" and cards[2]["timer"] == "Осталось 5 ч 20 мин"
    assert cards[2]["hint"] == "Столько осталось до конца пробного периода", "подсказка про смысл индикатора — при первом показе"
    assert cards[3]["title"] == "Пробный доступ завершён" and cards[3]["sub"] == "Серию можно сохранить до 29 октября, 00:00"
    assert cards[3]["timer"] == "Защита серии: ещё 14 ч 5 мин" and cards[3]["hint"] == "Столько можно сохранить заработанную серию", "это срок защиты серии, а не таймер цены"
    assert cards[4]["hidden"] is True and cards[5]["hidden"] is True and cards[6]["hidden"] is True, "после закрытия доступа и после оплаты карточки нет"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_screen_has_the_short_offer_the_features_both_prices_and_the_mission():
    screen = [item["screen"] for item in _run(CASES)["cases"]]
    day2 = screen[1]
    assert day2["title"] == "Подписка на Адама" and day2["status"] == "Пробный доступ открыт до 30 октября, 00:00"
    assert day2["lead"].startswith("Собирай день вокруг того, что важно тебе: поддерживай привычки, выбирай главные задачи и обсуждай следующий шаг с Адамом.")
    assert day2["perks"] == "Привычки и серия План дня: главная и второстепенные задачи Адам, который знает контекст твоих действий Прогресс и разборы Рейтинг и друзья"
    assert day2["offer"] == "$1.99 первый месяц, дальше $5.99 в месяц", "обе цены — с первого дня, в долларах"
    assert day2["pay"] == "Продолжить с Адамом" and day2["payHidden"] is False and day2["payDisabled"] is False
    assert day2["renewal"] == "Само ничего не спишется: срок ты обновляешь вручную, я напомню заранее."
    assert day2["mission"].startswith("Адам создаётся, чтобы помогать превращать намерения в ежедневные действия.") and "субъектность" in day2["mission"]
    assert day2["mission"].endswith("Начать можно с одного посильного действия сегодня.") and "недельн" not in day2["mission"]
    assert "Условия" in day2["note"] and "@egor_support" in day2["note"] and "Telegram Stars" in day2["note"]
    assert screen[8]["offer"] == "$5.99 в месяц", "вводная цена — только первой покупке"
    for window in screen:
        assert "последний день" not in window["offer"].lower() and "только сегодня" not in (window["lead"] + window["status"]).lower()
        assert "канал" not in (window["lead"] + window["status"] + window["perks"]).lower()


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_after_the_trial_the_screen_names_the_deadline_the_streak_and_what_stays():
    out = _run(CASES)["cases"]
    window, closed = out[3]["screen"], out[4]["screen"]
    assert window["title"] == "Пробный доступ завершён" and window["status"] == "Серия сохранится, если оформить подписку до 29 октября, 00:00"
    assert window["lead"] == "Твоя история остаётся с тобой: вернутся привычки, планирование и разговор с Адамом."
    assert window["close"] == "Посмотреть историю", "в дополнительном окне историю можно посмотреть"
    assert closed["title"] == "Доступ пока закрыт" and closed["status"] == "Данные и серия ждут твоего возвращения" and closed["close"] == "Закрыть демо"
    assert closed["lead"] == "Подписка снова откроет привычки, планирование и разговор с Адамом. История остаётся с тобой."
    assert "потеря" not in closed["lead"].lower() and "удал" not in closed["lead"].lower(), "не пугаем потерей данных, которые сохраняются"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_paid_screen_shows_the_term_and_the_renewal_switch_and_the_pending_one_waits():
    out = _run(CASES)["cases"]
    auto, manual, waiting = out[5]["screen"], out[6]["screen"], out[7]["screen"]
    assert auto["title"] == "Подписка на Адама" and auto["status"] == "Доступ открыт до 30 ноября, 00:00" and auto["payHidden"] is True and auto["offerHidden"] is True
    assert auto["renewal"] == "Списание повторяется каждые 30 дней, пока ты не отключишь его: в приложении или в настройках Telegram."
    assert auto["toggle"] == "Отключить автоматическое списание"
    assert manual["toggle"] is None and manual["renewal"] == "Само ничего не спишется: срок ты обновляешь вручную, я напомню заранее."
    assert waiting["payDisabled"] is True and waiting["pending"].startswith("Ждём подтверждение оплаты") and "Повторно платить не нужно" in waiting["pending"]
    assert "@egor_support" in waiting["pending"], "запасной контакт при ожидании"


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_the_summary_is_shown_inside_the_screen_and_the_button_goes_away():
    summary = _run(CASES)["summary"]
    assert summary["shown"] is True and summary["button"] is True
    assert "Коротко за 3 дня с Адамом" in summary["text"] and "Привычки: отмечено 5 раз" in summary["text"] and "После семи и больше дней" in summary["text"]


@pytest.mark.skipif(NODE is None, reason="нужен Node")
def test_no_screen_repeats_its_own_words_or_speaks_in_the_third_person():
    for case, result in zip(CASES, _run(CASES)["cases"]):
        screen = result["screen"]
        assert not shared(screen["title"], screen["status"], screen["lead"], screen["offer"], screen["pay"], screen["renewal"]), (case["trial"]["phase"], screen)
