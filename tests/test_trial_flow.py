"""
Сценарий сообщений и приглашений триала (ТЗ «Триал, подписка и приглашения» 28.10.2026, §12–§16, §18): журнал предложений, предложение внутри приложения во второй день,
вопрос Адама по данным человека, «Показать короткую сводку?» в чате, отказ от редкой серии и защита напоминаний от приглашений в закрытое.
"""
import itertools
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import pytest

import coach
import keyboards
from db import add_habit, add_user, get_all_users, get_settings, set_timezone, start_trial_mode
from db import trial_flow
from db.access import shift_trial_day
from db.core import connect
from db.sim_lab import SCENES
from db.subscription import record_subscription_payment
from handlers import payments
from tests.conftest import sign_init_data

STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CHAT_JS = (STATIC / "ai_coach.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
_counter = itertools.count(864_100_000, 100)
ADMIN = 777


@pytest.fixture
def uid():
    return next(_counter)


def _sql(query, params=()):
    conn = connect()
    try:
        cursor = conn.execute(query, params)
        row = cursor.fetchone() if query.lstrip().upper().startswith("SELECT") else None
        conn.commit()
        return row
    finally:
        conn.close()


def _person(uid_, day=2):
    add_user(uid_, f"u{uid_}", "Аня")
    _sql("UPDATE users SET access_status='approved', created_at=datetime('now', '-40 days') WHERE telegram_id=?", (uid_,))
    set_timezone(uid_, "UTC")
    start_trial_mode(uid_, ADMIN)
    shift_trial_day(uid_, day, ADMIN)
    return uid_


def _mark(uid_, habit_id):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _sql("INSERT INTO habit_completion_events(user_id, habit_id, completed_at) VALUES (?, ?, ?)", (uid_, habit_id, now))


def _headers(uid_):
    return {"Authorization": f"tma {sign_init_data(uid_)}"}


def _chat(uid_, count):
    for _ in range(count):
        _sql("INSERT INTO ai_messages(user_id, role, message) VALUES (?, 'user', 'привет')", (uid_,))


# ---------------------------------------------------------------------------------------------------------------- предложение внутри приложения (§12)

def test_the_offer_appears_after_a_real_action_on_day_two_only_once(uid):
    _person(uid, day=2)
    assert trial_flow.app_offer(uid, "habit") is None, "действия не было — предлагать нечего"
    habit = add_habit(uid, "Зарядка")
    _mark(uid, habit)
    offer = trial_flow.app_offer(uid, "habit")
    assert offer["key"] == "d2_app_habit" and "«Зарядка»" in offer["text"] and offer["terms"] == "Условия подписки" and offer["later"] == "Не сейчас"
    assert trial_flow.offer_state(uid)["sale_shown"] is True
    assert trial_flow.app_offer(uid, "habit") is None, "больше одного в день не бывает"


def test_the_offer_is_not_shown_after_not_now_on_other_days_or_after_payment(uid):
    _person(uid, day=2)
    habit = add_habit(uid, "Зарядка")
    _mark(uid, habit)
    trial_flow.mark_offer_event(uid, "app", "d2_app_habit", "dismissed")
    assert trial_flow.app_offer(uid, "habit") is None, "«Не сейчас» — сегодня автоматом не предлагаем"
    for day in (1, 3):
        other = uid + day
        _person(other, day=day)
        _mark(other, add_habit(other, "Вода"))
        assert trial_flow.app_offer(other, "habit") is None, f"день {day}: предложение в приложении только во второй день"
    paid = uid + 10
    _person(paid, day=2)
    _mark(paid, add_habit(paid, "Вода"))
    record_subscription_payment(paid, months=1)
    assert trial_flow.app_offer(paid, "habit") is None


def test_each_trigger_needs_its_own_real_fact(uid):
    _person(uid, day=2)
    assert trial_flow.app_offer(uid, "plan") is None and trial_flow.app_offer(uid, "adam") is None and trial_flow.app_offer(uid, "boom") is None
    _chat(uid, 2)
    offer = trial_flow.app_offer(uid, "adam")
    assert offer and offer["key"] == "d2_app_adam"
    other = uid + 1
    _person(other, day=2)
    _sql("INSERT OR IGNORE INTO daily_plans(user_id, plan_date, main_goal, main_goal_completed) VALUES (?, date('now'), 'Отчёт', 1)", (other,))
    offer = trial_flow.app_offer(other, "plan")
    assert offer and offer["key"] == "d2_app_plan" and "1" in offer["text"] and ("План" in offer["text"] or "плане" in offer["text"])


async def test_the_offer_routes_answer_the_app_and_record_what_the_person_did(client, uid):
    _person(uid, day=2)
    _mark(uid, add_habit(uid, "Зарядка"))
    response = await client.post("/api/trial/offer", headers=_headers(uid), json={"trigger": "habit"})
    body = await response.json()
    assert response.status == 200 and body["offer"]["key"] == "d2_app_habit"
    again = await (await client.post("/api/trial/offer", headers=_headers(uid), json={"trigger": "habit"})).json()
    assert again["offer"] is None
    ok = await client.post("/api/trial/offer-event", headers=_headers(uid), json={"place": "app", "key": "d2_app_habit", "event": "clicked"})
    assert ok.status == 200
    assert (await client.post("/api/trial/offer-event", headers=_headers(uid), json={"place": "push", "key": "x", "event": "clicked"})).status == 400
    assert (await client.post("/api/trial/offer-event", headers=_headers(uid), json={"place": "app", "key": "x", "event": "boom"})).status == 400
    names = {r["name"] for r in connect().execute("SELECT name FROM user_events WHERE user_id=?", (uid,)).fetchall()}
    assert {"trial_offer_shown", "trial_offer_clicked"} <= names


# ---------------------------------------------------------------------------------------------------------------- вопрос Адама и сводка в чате (§15)

def test_the_invite_question_follows_the_data_the_person_really_has(uid):
    _person(uid, day=2)
    snap = {"name": "Аня", "local_date": "2026-10-28"}
    habits = trial_flow.invite_question(uid, snap, {"marks_total": 4, "top_habits": [{"title": "Зарядка", "count": 3}]})
    assert "привыч" in habits and "задач" not in habits and "план" not in habits
    plan = trial_flow.invite_question(uid, snap, {"tasks_added": 2})
    assert "план" in plan and "отметк" not in plan, "только план — отметок привычек не выдумываем"
    both = trial_flow.invite_question(uid, snap, {"marks_total": 2, "tasks_added": 1})
    assert "привычк" in both and "план" in both
    nothing = trial_flow.invite_question(uid, snap, {})
    assert "данных мало" in nothing or "начнём с малого" in nothing, "данных почти нет — помогаем выбрать первый шаг"


def test_the_invite_is_created_once_a_day_and_not_regenerated_or_given_after_payment(uid):
    _person(uid, day=2)
    first = trial_flow.open_invite(uid)
    assert first["new"] is True and first["message"]
    second = trial_flow.open_invite(uid)
    assert second["new"] is False and second["id"] == first["id"] and second["message"] == first["message"], "уже подготовленное сообщение заново не генерируем"
    assert _sql("SELECT COUNT(*) AS n FROM ai_messages WHERE user_id=? AND role='assistant'", (uid,))["n"] == 1
    record_subscription_payment(uid, months=1)
    assert trial_flow.open_invite(uid) is None, "старое приглашение после оплаты — обычный разговор без продажи"
    day_one = uid + 1
    _person(day_one, day=1)
    assert trial_flow.open_invite(day_one) is None


def test_the_subscription_offer_goes_only_under_the_first_real_answer_to_the_invite(uid):
    _person(uid, day=2)
    assert trial_flow.chat_offer(uid, "Мне помогает утро и музыка, мешает усталость вечером") is None, "приглашения не было"
    trial_flow.open_invite(uid)
    assert trial_flow.chat_offer(uid, "ок") is None, "короткая отмашка — не ответ"
    assert trial_flow.chat_offer(uid, "А как мне составить план на неделю, подскажи, пожалуйста?") is None, "человек спросил другое — сначала отвечаем на его вопрос"
    offer = trial_flow.chat_offer(uid, "Мне помогает утро и музыка, мешает усталость вечером")
    assert offer == {"key": "summary_ask", "text": "Показать короткую сводку?", "yes": "Да", "later": "Не сейчас", "terms": "Условия подписки"}
    assert trial_flow.chat_offer(uid, "Мне помогает утро и музыка, мешает усталость вечером") is None, "раз в день"


def test_a_second_answer_or_an_offer_shown_elsewhere_blocks_the_chat_offer(uid):
    _person(uid, day=2)
    trial_flow.open_invite(uid)
    _chat(uid, 1)
    assert trial_flow.chat_offer(uid, "Мне помогает утро и музыка, мешает усталость вечером") is None, "это уже не первый ответ на вопрос"
    other = uid + 1
    _person(other, day=2)
    trial_flow.open_invite(other)
    trial_flow.record(other, "app", "d2_app_habit", "shown")
    assert trial_flow.chat_offer(other, "Мне помогает утро и музыка, мешает усталость вечером") is None, "в этот день предложение уже показывали"


def test_yes_puts_a_real_summary_into_the_chat_and_not_now_is_remembered(uid):
    _person(uid, day=2)
    _mark(uid, add_habit(uid, "Зарядка"))
    result = trial_flow.chat_summary(uid, "yes")
    assert result["ok"] and result["terms"] == "Условия подписки" and "Коротко за" in result["message"]["message"] and "отметок — 1" in result["message"]["message"]
    stored = _sql("SELECT role, message FROM ai_messages WHERE id=?", (result["message"]["id"],))
    assert stored["role"] == "assistant" and stored["message"] == result["message"]["message"], "сводка остаётся в истории чата"
    assert trial_flow.chat_summary(uid, "later") == {"ok": True}
    assert trial_flow.offer_state(uid)["dismissed"] is True
    assert trial_flow.chat_summary(uid, "boom") == {"error": "invalid_answer"}


async def test_the_chat_routes_open_the_invite_and_answer_the_summary_question(client, uid):
    _person(uid, day=3)
    init = sign_init_data(uid)
    invite = await (await client.post("/api/ai/trial-invite", json={"init_data": init, "m": "d3_talk"})).json()
    assert invite["invite"]["message"]
    again = await (await client.post("/api/ai/trial-invite", json={"init_data": init})).json()
    assert again["invite"]["id"] == invite["invite"]["id"]
    summary = await client.post("/api/ai/trial-summary", json={"init_data": init, "answer": "yes"})
    body = await summary.json()
    assert summary.status == 200 and body["message"]["message"].startswith("Коротко за")
    assert (await client.post("/api/ai/trial-summary", json={"init_data": init, "answer": "boom"})).status == 400
    names = {r["name"] for r in connect().execute("SELECT name, step FROM user_events WHERE user_id=?", (uid,)).fetchall()}
    assert {"trial_msg_open", "trial_invite_open", "trial_offer_clicked"} <= names


# ---------------------------------------------------------------------------------------------------------------- закрытый доступ и напоминания (§14)

def test_reminder_jobs_skip_people_whose_main_actions_are_closed(uid):
    trial, grace, expired, tester = _person(uid, day=2), _person(uid + 1, day=4), _person(uid + 2, day=7), uid + 3
    add_user(tester, "t", "Аня")
    _sql("UPDATE users SET access_status='approved' WHERE telegram_id=?", (tester,))
    ids = {u["telegram_id"] for u in get_all_users(include_blocked=False, unlocked_only=True)}
    assert trial in ids and tester in ids
    assert grace not in ids and expired not in ids, "в окне и после него напоминания не зовут в недоступное"
    everyone = {u["telegram_id"] for u in get_all_users(include_blocked=False)}
    assert grace in everyone and expired in everyone, "а рассылки триала по-прежнему видят всех"


def test_the_checkpoint_verdict_names_the_locked_access_as_the_reason(uid):
    grace = _person(uid, day=4)
    now = datetime.now(timezone.utc)
    verdict = coach.checkpoint_verdict(grace, get_settings(grace), now, 10, check_window=False)
    assert verdict["reason"] == "access_locked"
    from db.notification_skips import REASON_LABELS
    assert "access_locked" in REASON_LABELS
    open_ = _person(uid + 1, day=2)
    assert coach.checkpoint_verdict(open_, get_settings(open_), now, 10, check_window=False)["reason"] != "access_locked"


# ---------------------------------------------------------------------------------------------------------------- кнопки бота и отказ

def test_the_message_buttons_open_the_right_screen_and_carry_the_message_key(monkeypatch):
    monkeypatch.setattr(keyboards, "WEBAPP_URL", "https://adam.example/")
    def rows(*buttons, key="d3_end"):
        return [b for row in keyboards.trial_keyboard(buttons, 153, key).inline_keyboard for b in row]
    sub = rows("sub")[0]
    assert sub.text == "Условия подписки" and sub.web_app.url == "https://adam.example/?sub=1&m=d3_end"
    talk = rows("talk", key="d3_talk")[0]
    assert talk.text == "Поговорить с Адамом" and talk.web_app.url == "https://adam.example/coach?intro=trial&m=d3_talk"
    assert rows("open")[0].web_app.url == "https://adam.example/?m=d3_end"
    both = rows("open", "sub", "optout")
    assert [b.text for b in both] == ["Открыть ADAM", "Условия подписки", "Не присылать такое"] and both[2].callback_data == "winback_off"
    assert keyboards.trial_keyboard((), 153) is None


async def test_the_opt_out_button_stops_the_series_and_is_allowed_for_a_locked_person(uid):
    from middlewares.access_control import _EXEMPT_CALLBACKS
    assert "winback_off" in _EXEMPT_CALLBACKS
    person = _person(uid, day=9)
    message = SimpleNamespace(edit_reply_markup=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=person), message=message, answer=AsyncMock())
    await payments.winback_off(callback)
    assert _sql("SELECT winback_optout FROM users WHERE telegram_id=?", (person,))["winback_optout"] == 1
    assert trial_flow.winback_state(person, __import__("db").trial.snapshot(person))["reason"] == "optout"
    message.edit_reply_markup.assert_awaited_once()
    callback.answer.assert_awaited_once()


# ---------------------------------------------------------------------------------------------------------------- приложение и чат

def test_the_app_and_the_chat_know_the_new_screens():
    for marker in ('id="trialOfferCard"', 'id="trialOfferGo"', 'id="trialOfferLater"'):
        assert marker in INDEX
    for needle in ("function scheduleTrialOffer", "function handleTrialEntry", "/api/trial/offer", "trial_msg_open", 'params.get("sub") === "1"', 'scheduleTrialOffer("habit")', 'scheduleTrialOffer("plan")'):
        assert needle in APP_JS, needle
    assert "trialDismissedToday()" in APP_JS.split("function scheduleTrialOffer")[1][:400], "после «Не сейчас» сегодня автоматом не предлагаем"
    assert "SUB_DISMISS_KEY" in APP_JS.split("function trialDismissedToday")[1][:200]
    for needle in ("/api/ai/trial-invite", "/api/ai/trial-summary", "intro') === 'trial'", "trialOffer", "SIM_TRIAL_CHAT"):
        assert needle in CHAT_JS, needle
    assert {"trial_offer_app", "trial_chat_offer"} <= {scene[0] for scene in SCENES}
    assert "trial_offer_app:" in APP_JS and "trial_chat_offer:" in APP_JS
