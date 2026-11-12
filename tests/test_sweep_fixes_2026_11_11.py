"""
Прогон 10 проверяющих (11.11). Каждый тест падал до исправления.
"""
import itertools
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage

from db import add_user, get_user
from db.core import connect

_counter = itertools.count(876_000_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _exec(sql, args=()):
    conn = connect()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


class _Session(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message="Bad Request: there is no text in the message to edit")
        return True

    async def stream_content(self, *a, **k):
        pass

    async def close(self):
        pass


# ------------------------------------------------------------------------------------------------ кнопки под фото-баннером /start

async def test_button_under_a_photo_banner_answers_with_a_new_message():
    from bot_middlewares import edit_photo_fallback
    session = _Session()
    bot = Bot("123:abc", session=session)
    bot.session.middleware(edit_photo_fallback)
    await bot.edit_message_text(chat_id=876_000_001, message_id=5, text="🔔 Умные напоминания")
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert sent and sent[0].text == "🔔 Умные напоминания" and sent[0].chat_id == 876_000_001


async def test_other_edit_errors_still_raise():
    from bot_middlewares import edit_photo_fallback

    class Other(_Session):
        async def make_request(self, bot, method, timeout=None):
            raise TelegramBadRequest(method=method, message="Bad Request: message is not modified")
    bot = Bot("123:abc", session=Other())
    bot.session.middleware(edit_photo_fallback)
    with pytest.raises(TelegramBadRequest):
        await bot.edit_message_text(chat_id=1, message_id=5, text="x")


# ------------------------------------------------------------------------------------------------ шапка оформления и текст без HTML

def test_royal_header_is_not_added_to_plain_text_messages():
    from bot_style import _parse_mode_ok
    plain = SendMessage(chat_id=1, text="Доброе утро", parse_mode=None)
    default = SendMessage(chat_id=1, text="Доброе утро")
    html = SendMessage(chat_id=1, text="Доброе утро", parse_mode="HTML")
    assert _parse_mode_ok(plain) is False
    assert _parse_mode_ok(default) is True and _parse_mode_ok(html) is True


# ------------------------------------------------------------------------------------------------ доступ под общим гейтом

def _gate(monkeypatch):
    import config
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_GATE_CUTOVER", "all")


def test_gift_and_unlimited_work_for_a_regular_user_under_the_gate(uid, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from db.access import gift_days, set_unlimited, trial_scenario_for_user
    from db.access_state import access_state, set_trial_start
    _gate(monkeypatch)
    add_user(uid, f"u{uid}", "Аня")
    set_trial_start(uid, datetime.now(timezone.utc) - timedelta(days=6))
    assert access_state(uid)["state"] == "expired"
    assert gift_days(uid, 7).get("error") is None                  # раньше: {"error": "unlimited"}
    assert access_state(uid)["state"] == "gift"
    set_unlimited(uid)
    assert not trial_scenario_for_user(get_user(uid))
    later = datetime.now(timezone.utc) + timedelta(days=10)
    assert access_state(uid, later)["allowed"]                      # раньше: новый триал и снова закрыто


def test_gift_during_the_trial_moves_the_end_and_silences_the_last_day_message(uid, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import trial_messages
    from db import trial as trial_db
    from db.access import gift_days
    from db.access_state import access_state, set_trial_start
    _gate(monkeypatch)
    add_user(uid, f"u{uid}", "Аня")
    set_trial_start(uid, datetime.now(timezone.utc) - timedelta(days=1))
    gift_days(uid, 7)
    info = access_state(uid)
    assert info["state"] == "trial" and info["ends_at"] == info["free_end"] != info["trial_ends_at"]
    snap = trial_db.snapshot(uid)
    assert not trial_messages.BY_KEY["d3_end"].condition(dict(snap, ends_at=info["ends_at"], trial_ends_at=info["trial_ends_at"]))


def test_late_timezone_is_still_adopted_while_the_trial_runs_in_utc(uid):
    from datetime import datetime, timedelta, timezone
    from db.access_state import adopt_first_timezone, set_trial_start
    add_user(uid, f"u{uid}", "Аня")
    set_trial_start(uid, datetime.now(timezone.utc) - timedelta(hours=10))   # пояса не было — триал по UTC
    assert adopt_first_timezone(uid, "America/Los_Angeles") is True           # раньше: только первые 6 часов
    assert get_user(uid)["trial_tz"] == "America/Los_Angeles"
    assert adopt_first_timezone(uid, "Europe/Moscow") is False                 # настоящий пояс уже есть — срок не двигаем


# ------------------------------------------------------------------------------------------------ рассылки

async def test_last_hour_is_not_sent_to_someone_gone_for_weeks_without_a_streak(uid, monkeypatch):
    from datetime import datetime as _dt, timedelta
    import streak_scheduler
    from db import add_habit
    from db.streak import local_today
    add_user(uid, f"u{uid}", "Аня")
    add_habit(uid, "Бег")
    today = local_today(uid)
    _exec("UPDATE users SET access_status='approved', streak=0 WHERE telegram_id=?", (uid,))
    _exec("INSERT INTO streak_days(user_id,day,status,streak_after) VALUES(?,?,?,?)", (uid, (today - timedelta(days=20)).isoformat(), "completed", 3))

    class FakeDT(_dt):
        @classmethod
        def now(cls, tz=None):
            return _dt.combine(today, _dt.min.time()).replace(hour=23, minute=1, tzinfo=tz)
    monkeypatch.setattr(streak_scheduler, "datetime", FakeDT)
    monkeypatch.setattr(streak_scheduler, "get_streak_users", lambda include_blocked=False: [uid])
    bot = SimpleNamespace(token="1:x", send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    await streak_scheduler.run_streak_risk_notifications(bot)
    assert not [c for c in bot.send_message.await_args_list if c.args[0] == uid]


async def test_pair_push_failure_does_not_swallow_the_rest(monkeypatch):
    from aiogram.exceptions import TelegramRetryAfter
    import streak_scheduler
    monkeypatch.setattr(streak_scheduler, "get_settings", lambda _u: {"reminders": 1})
    monkeypatch.setattr(streak_scheduler, "_push_allowed", lambda _u: True)
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramRetryAfter(method=SimpleNamespace(), message="flood", retry_after=5)))
    assert await streak_scheduler._send_pair_push(bot, 1, "x") is False        # раньше исключение вылетало и рвало цикл событий


# ------------------------------------------------------------------------------------------------ API

def test_dislike_reason_is_capped_in_the_ai_context(uid):
    from db import save_feedback_reason
    from db.ai import add_ai_message
    from webapp.services.ai_utils import build_user_context
    add_user(uid, f"u{uid}", "Аня")
    mid = add_ai_message(uid, "assistant", "ответ")
    save_feedback_reason(mid, uid, "x" * 50_000)                   # старая запись без ограничения
    assert len(build_user_context(uid)) < 5_000


def test_plan_save_caps_text_and_public_profile_hides_the_banned(uid):
    from db import ban_user
    from db.daily_plan import save_daily_plan, get_daily_plan
    from db.public_profile import get_public_profile
    add_user(uid, f"u{uid}", "Аня")
    save_daily_plan(uid, "г" * 10_000, ["з" * 10_000])
    plan = get_daily_plan(uid)
    assert len(plan["main_goal"]) <= 80
    _exec("UPDATE users SET public_profile_enabled=1 WHERE telegram_id=?", (uid,))
    assert get_public_profile(uid) is not None
    ban_user(uid)
    assert get_public_profile(uid) is None


# ------------------------------------------------------------------------------------------------ журнал отправок

def test_journal_tells_checkpoints_and_trial_messages_apart():
    import bot_messages_map
    from db import outbox
    with outbox.kind("checkpoint_17"):
        outbox.record(876_999_001, "sendMessage", "coach._run_habit_checkpoint", "coach._run_habit_checkpoint < coach.run_habit_checkpoint_17", "т")
    with outbox.kind("d3_end"):
        outbox.record(876_999_001, "sendMessage", "subscription_scheduler.run_trial_reminders", "subscription_scheduler.run_trial_reminders", "т")
    rows = outbox.for_user(876_999_001)
    assert [bot_messages_map.find_row(r).key for r in rows] == ["d3_end", "checkpoint_17"]
    stats = outbox.source_stats(days=1)
    by_key = {r["key"]: r["week_count"] for r in bot_messages_map.as_rows(stats)}
    assert by_key["checkpoint_17"] >= 1 and by_key["d3_end"] >= 1


def test_abuse_alerts_keep_their_sender():
    import os
    from bot_middlewares import _SKIP_FILES
    assert os.path.basename("/app/abuse_alerts.py") not in _SKIP_FILES


# ------------------------------------------------------------------------------------------------ онбординг (проверено в браузере)

def test_onboarding_fixes_are_in_place():
    from pathlib import Path
    app = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    render = app[app.index("function renderStartQuizStep()"):][:1500]
    assert "continueBtn.hidden = false" in render                     # стрелка «Назад» с переспроса 16+ — не тупик
    assert "const savedStage = stage === 11 ? 3 : stage;" in app       # перезагрузка на шаге 2 — снова шаг 2
    assert "reachedStage < 3" in app[app.index("function scheduleProductOnboarding()"):][:2400]
    assert "boot-retry-banner__support" in app                         # 16+ при повторном входе — кнопка в поддержку


async def test_age_restricted_403_carries_the_support_link(client, uid, monkeypatch):
    import config
    from tests.conftest import sign_init_data
    monkeypatch.setattr(config, "SUPPORT_CONTACT", "@adam_help")
    add_user(uid, f"u{uid}", "Аня")
    _exec("UPDATE users SET access_status='age_restricted' WHERE telegram_id=?", (uid,))
    r = await client.get("/api/bootstrap", headers={"Authorization": f"tma {sign_init_data(uid)}"})
    body = await r.json()
    assert r.status == 403 and body["error"] == "access_age_restricted" and body["support_url"] == "https://t.me/adam_help"


# ------------------------------------------------------------------------------------------------ бот: оплата Premium, админ-ввод

async def test_old_premium_invoice_is_refused_when_premium_is_closed(monkeypatch):
    import config
    from handlers.payments import pre_checkout
    monkeypatch.setattr(config, "PREMIUM_TIER_ENABLED", False)
    query = SimpleNamespace(invoice_payload="premium_555", from_user=SimpleNamespace(id=555), answer=AsyncMock(), total_amount=150)
    await pre_checkout(query)
    assert query.answer.await_args.kwargs["ok"] is False


async def test_admin_id_input_survives_photos_commands_and_unknown_ids(uid, monkeypatch):
    import handlers.admin as admin
    monkeypatch.setattr(admin, "ADMIN_IDS", [777000333])

    class _St:
        def __init__(self):
            self.cleared = False

        async def clear(self):
            self.cleared = True

    def msg(text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(id=777000333), answer=AsyncMock())
    photo, cmd, huge, unknown = msg(None), msg("/start"), msg("9" * 30), msg("123")
    for m in (photo, huge, unknown):
        await admin.premium_user(m, _St())
        assert "успешно" not in m.answer.await_args.args[0]
    st = _St()
    await admin.premium_user(cmd, st)
    assert st.cleared and "Отменено" in cmd.answer.await_args.args[0]


def test_fixing_a_typo_keeps_the_main_goal_done(uid):
    from db.daily_plan import toggle_daily_main_goal, get_daily_plan, set_daily_main_goal
    add_user(uid, f"u{uid}", "Аня")
    set_daily_main_goal(uid, "Сдлать отчёт")
    toggle_daily_main_goal(uid)
    set_daily_main_goal(uid, "Сделать отчёт")
    assert get_daily_plan(uid)["main_goal_completed"] == 1


# ------------------------------------------------------------------------------------------------ скорость рассылок

def test_bulk_preload_serves_timezones_and_settings_from_one_read(uid, monkeypatch):
    import db.core as core
    from db.bulk import preload
    from db.settings import get_settings
    from db.streak import get_timezone, set_timezone
    add_user(uid, f"u{uid}", "Аня")
    set_timezone(uid, "Asia/Vladivostok")
    with preload():
        calls = []
        real = core.connect
        monkeypatch.setattr("db.streak.connect", lambda *a, **k: calls.append(1) or real(*a, **k))
        monkeypatch.setattr("db.settings.connect", lambda *a, **k: calls.append(1) or real(*a, **k))
        assert get_timezone(uid) == "Asia/Vladivostok"
        assert get_settings(uid) is not None
        assert calls == [], "внутри прохода — без новых подключений к базе"
    assert get_timezone(uid) == "Asia/Vladivostok"
