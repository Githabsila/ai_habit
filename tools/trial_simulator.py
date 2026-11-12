"""
Симулятор триала (просьба владельца 12.11): несколько людей по-разному проходят пробный период — кто-то платит в первый день,
кто-то во второй, кто-то только на четвёртый; кто-то заходит редко, кто-то постоянно, у кого-то пояс приходит поздно. Нужно увидеть,
какие сообщения и экраны получит каждый: когда, почему, при каких условиях. Ждать неделю на живых аккаунтах не нужно.

Как это устроено: временная база, время «сжато» (freezegun двигает часы по 15 минут), и на каждом шаге работает НАСТОЯЩИЙ код:
  • человек «открывает приложение» — настоящий /api/bootstrap (старт триала, состояние доступа), пояс — /api/streak/timezone;
  • заводит и отмечает привычки — /api/habits и /api/habits/{id}/complete;
  • платит звёздами — настоящий обработчик успешной оплаты бота (handlers/payments.successful_payment), картой/криптой —
    заказ /api/pay/order и подписанный вебхук Crypto Bot (тот же путь, что у Tribute/Lava);
  • бот — все рассылки из main.py по расписанию (точки дня, серия, триал, возврат, сроки подписки…), сообщения перехватываются.
ИИ подменён (внешней сети нет): там, где текст пишет модель, в отчёте — запасной текст, который бот шлёт без ИИ.

Запуск:  python tools/trial_simulator.py  [--days 6]  [--out отчёт.html]
Результат — HTML-отчёт: по каждому человеку лента событий (его действия, состояние доступа, каждое сообщение бота с местным
временем, названием рассылки из карты сообщений и кнопками) и сводка.

Ограничение: время подменено для Python, а не для SQLite — отметки CURRENT_TIMESTAMP в базе ставятся по реальным часам. Сроки
триала, подписки, оплаты и дедупликация рассылок считаются в Python, поэтому на них это не влияет.
"""
import argparse
import asyncio
import hashlib
import hmac
import html
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SIM_TOKEN = "123:sim"
CRYPTO_TOKEN = "sim-crypto-token"
os.environ.setdefault("RAILWAY_VOLUME_MOUNT_PATH", tempfile.mkdtemp(prefix="adam_sim_"))
os.environ["BOT_TOKEN"] = SIM_TOKEN
os.environ["SUBSCRIPTION_GATE_ENABLED"] = "true"
os.environ["SUBSCRIPTION_GATE_CUTOVER"] = "all"
os.environ["EXTERNAL_PAYMENTS_ENABLED"] = "true"
os.environ["CRYPTOBOT_API_TOKEN"] = CRYPTO_TOKEN
os.environ["ADMIN_ID"] = "1"
os.environ["OPENAI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""

from freezegun import freeze_time  # noqa: E402

UTC = timezone.utc
TICK = timedelta(minutes=15)


# ---------------------------------------------------------------------------------------------------------------- люди

@dataclass
class Persona:
    uid: int
    name: str
    title: str
    tz: str
    about: str
    plan: dict                     # (день, "ЧЧ:ММ") → [действия]
    tz_late_hours: int = 0         # пояс приходит не при первом открытии, а через N часов
    events: list = field(default_factory=list)
    habit_ids: list = field(default_factory=list)


def _daily(days, times, actions):
    return {(d, t): list(actions) for d in days for t in times}


def personas():
    def merge(*plans):
        out = {}
        for p in plans:
            for k, v in p.items():
                out.setdefault(k, []).extend(v)
        return out

    start = ["open", "add_habit:Пить воду", "add_habit:Читать 20 минут", "mark"]
    return [
        Persona(900001, "Аня", "Платит в первый день (звёзды)", "Europe/Moscow",
                "Завела привычки утром, всё отмечает, вечером первого дня оформляет подписку звёздами.",
                merge({(1, "09:00"): start, (1, "20:00"): ["open", "pay_stars"]}, _daily(range(2, 7), ["09:00"], ["open", "mark"]))),
        Persona(900002, "Борис", "Платит на второй день (крипта/карта)", "Europe/Moscow",
                "Активный, заходит утром и вечером. На второй день вечером платит картой/криптой.",
                merge({(1, "08:30"): start}, _daily(range(2, 7), ["08:30"], ["open", "mark"]), _daily(range(1, 7), ["20:30"], ["open", "mark"]), {(2, "21:00"): ["pay_card"]})),
        Persona(900003, "Вика", "Платит на 4-й день, после конца триала", "Asia/Almaty",
                "Активна три дня, на четвёртый утром видит закрытый доступ и только тогда платит звёздами.",
                merge({(1, "10:00"): start}, _daily(range(2, 4), ["10:00", "19:00"], ["open", "mark"]), {(4, "10:00"): ["open", "pay_stars", "open", "mark"]},
                      _daily(range(5, 7), ["10:00"], ["open", "mark"]))),
        Persona(900004, "Гоша", "Заходит редко, не платит", "Europe/Moscow",
                "Открыл в первый день, завёл привычку и пропал. Вернулся один раз на пятый день.",
                {(1, "12:00"): ["open", "add_habit:Зарядка", "mark"], (5, "18:00"): ["open"]}),
        Persona(900005, "Дина", "Очень активная, не платит", "Asia/Novosibirsk",
                "Отмечает каждый день утром, днём и вечером, но подписку не оформляет — что будет после триала.",
                merge({(1, "07:30"): start}, _daily(range(2, 7), ["07:30"], ["open", "mark"]), _daily(range(1, 7), ["13:00", "21:00"], ["open", "mark"]))),
        Persona(900006, "Ева", "США, пояс пришёл поздно", "America/Los_Angeles",
                "Открыла приложение с устройства, где пояс не виден; настоящий пояс пришёл через 8 часов. Платит на третий день.",
                merge({(1, "08:00"): start}, _daily(range(2, 7), ["08:00"], ["open", "mark"]), {(3, "19:00"): ["pay_stars"]}), tz_late_hours=8),
    ]


def sign_init_data(telegram_id, first_name="Sim", username="sim"):
    """initData, подписанный токеном бота симуляции (как его подписывает Telegram), с текущим «симулированным» временем."""
    import time
    from urllib.parse import quote
    user = json.dumps({"id": telegram_id, "first_name": first_name, "username": username, "language_code": "ru"}, ensure_ascii=False)
    params = {"user": user, "auth_date": str(int(time.time())), "query_id": "AAsim"}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", SIM_TOKEN.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())


# ---------------------------------------------------------------------------------------------------------------- время в SQLite

def _parse_sql_time(value):
    if value is None:
        return None
    text = str(value).strip().replace("T", " ")
    if text == "now":
        return datetime.now(UTC).replace(tzinfo=None)
    for cut in ("+", "Z"):
        if cut in text[10:]:
            text = text[:10] + text[10:].split(cut)[0]
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _apply_modifiers(moment, modifiers):
    import calendar
    for mod in modifiers:
        m = str(mod or "").strip().lower()
        if not m or m in ("localtime", "utc"):
            continue
        if m == "start of day":
            moment = moment.replace(hour=0, minute=0, second=0, microsecond=0)
            continue
        if m == "start of month":
            moment = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            continue
        parts = m.split()
        if len(parts) != 2:
            return None
        try:
            n = float(parts[0])
        except ValueError:
            return None
        unit = parts[1].rstrip("s")
        if unit == "day":
            moment += timedelta(days=n)
        elif unit == "hour":
            moment += timedelta(hours=n)
        elif unit == "minute":
            moment += timedelta(minutes=n)
        elif unit == "second":
            moment += timedelta(seconds=n)
        elif unit == "month":
            month = moment.month - 1 + int(n)
            year = moment.year + month // 12
            month = month % 12 + 1
            moment = moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))
        else:
            return None
    return moment


def _sql_datetime(value=None, *modifiers):
    moment = _parse_sql_time("now" if value is None else value)
    moment = _apply_modifiers(moment, modifiers) if moment is not None else None
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else None


def _sql_date(value=None, *modifiers):
    moment = _parse_sql_time("now" if value is None else value)
    moment = _apply_modifiers(moment, modifiers) if moment is not None else None
    return moment.strftime("%Y-%m-%d") if moment else None


def patch_sqlite_clock():
    """datetime('now', …) и date('now', …) в запросах — по «сжатому» времени симуляции, а не по реальным часам."""
    import sqlite3
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.create_function("datetime", -1, _sql_datetime, deterministic=False)
        conn.create_function("date", -1, _sql_date, deterministic=False)
        return conn
    sqlite3.connect = connect


# ---------------------------------------------------------------------------------------------------------------- бот

class CaptureSession:
    """Сессия бота без сети: запоминаем каждый вызов (кому, что, кнопки, какая рассылка — outbox.kind)."""

    def __init__(self, sink):
        from aiogram.client.session.base import BaseSession

        class _S(BaseSession):
            async def make_request(self_inner, bot, method, timeout=None):
                return sink(method)

            async def stream_content(self_inner, *a, **k):
                if False:
                    yield b""

            async def close(self_inner):
                pass
        self.session = _S()


def _buttons(markup):
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b.text for row in rows for b in row]


# ---------------------------------------------------------------------------------------------------------------- прогон

async def simulate(days, start_utc):
    import config
    from aiohttp.test_utils import TestClient, TestServer
    from aiogram import Bot
    from aiogram.methods import EditMessageText, SendMessage, SendPhoto

    import bot_messages_map
    from bot_middlewares import edit_photo_fallback, log_outbox, retry_flood_control
    from bot_style import royal_dress
    from db import add_user, outbox
    from db.access_state import access_state
    from db.core import connect, create_tables
    from webapp.webapp_server import create_app

    config.ADMIN_IDS = [1]
    patch_sqlite_clock()
    create_tables()
    people = personas()
    by_uid = {p.uid: p for p in people}
    message_counter = [1000]

    def local(p, t=None):
        return (t or datetime.now(UTC)).astimezone(ZoneInfo(p.tz))

    def sink(method):
        chat = getattr(method, "chat_id", None)
        p = by_uid.get(chat)
        if p is not None and isinstance(method, (SendMessage, SendPhoto)):
            text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
            kind = outbox.current_kind()
            hit = bot_messages_map.BY_KEY.get(kind) if kind else None
            p.events.append({"at": datetime.now(UTC), "type": "bot", "title": hit.title if hit else None, "kind": kind,
                             "text": text, "buttons": _buttons(getattr(method, "reply_markup", None))})
        if isinstance(method, EditMessageText):
            return True
        message_counter[0] += 1
        return SimpleNamespace(message_id=message_counter[0], chat=SimpleNamespace(id=chat), date=datetime.now(UTC))

    bot = Bot(SIM_TOKEN, session=CaptureSession(sink).session)
    for mw in (log_outbox, retry_flood_control, royal_dress, edit_photo_fallback):
        bot.session.middleware(mw)

    # ИИ недоступен: модель «падает» сразу — рассылки берут запасные тексты (как на проде при сбое модели).
    async def _no_ai(*a, **k):
        raise RuntimeError("симуляция: ИИ отключён")
    for mod in list(sys.modules.values()):
        for name in ("_ask", "generate_morning_message", "generate_adam_checkin", "generate_weekly_habit_feedback",
                     "generate_monthly_habit_feedback", "analyze_goal_progress", "_generate"):
            if getattr(mod, "__name__", "").split(".")[0] in ("multi_agent", "morning_ping", "adam_proactive", "coach", "goal_feedback") and hasattr(mod, name):
                setattr(mod, name, _no_ai)

    import pay_providers
    crypto = pay_providers.PROVIDERS["cryptobot"]

    async def fake_call(method, body):
        return {"ok": True, "result": {"invoice_id": 1, "status": "active", "bot_invoice_url": "https://t.me/CryptoBot?start=IVsim"}}
    crypto._call = fake_call

    app = create_app()
    app["bot"] = bot
    client = TestClient(TestServer(app))
    await client.start_server()

    def headers(p):
        return {"Authorization": f"tma {sign_init_data(p.uid, first_name=p.name, username=f'sim{p.uid}')}", "Content-Type": "application/json"}

    def note(p, text):
        p.events.append({"at": datetime.now(UTC), "type": "action", "text": text})

    def state_line(p):
        s = access_state(p.uid) or {}
        streak = connect().execute("SELECT streak FROM users WHERE telegram_id=?", (p.uid,)).fetchone()
        parts = [f"доступ: {s.get('state')}"]
        if s.get("phase"):
            parts.append(f"фаза {s.get('phase')}")
        if s.get("ends_at_text"):
            parts.append(f"до {s.get('ends_at_text')}")
        parts.append(f"серия {streak['streak'] if streak else 0}")
        return " · ".join(parts)

    async def act(p, action):
        if action == "open":
            r = await client.get("/api/bootstrap", headers=headers(p))
            body = await r.json()
            if r.status != 200:
                note(p, f"📱 Открыл приложение → экран «{body.get('error')}» ({html.escape(str(body.get('message') or ''))[:90]}) · {state_line(p)}")
                return
            if not p.tz_late_hours:
                await client.post("/api/streak/timezone", headers=headers(p), json={"timezone": p.tz})
            note(p, f"📱 Открыл приложение · {state_line(p)}")
        elif action.startswith("add_habit:"):
            r = await client.post("/api/habits", headers=headers(p), json={"title": action.split(":", 1)[1]})
            body = await r.json()
            habit = body.get("habit") or {}
            if habit.get("id"):
                p.habit_ids.append(habit["id"])
            note(p, f"➕ Добавил привычку «{action.split(':', 1)[1]}»" + ("" if r.status == 200 else f" → ошибка {body.get('error')}"))
        elif action == "mark":
            done = 0
            for hid in p.habit_ids:
                r = await client.post(f"/api/habits/{hid}/complete", headers=headers(p), json={})
                done += r.status == 200
            note(p, f"✅ Отметил привычки: {done} из {len(p.habit_ids)} · {state_line(p)}")
        elif action == "pay_stars":
            from handlers.payments import successful_payment
            from subscription_flow import invoice_spec
            spec = invoice_spec(p.uid) or {"price": 0}
            replies = []

            async def answer(text=None, **kw):
                replies.append((text, kw.get("reply_markup")))
            async def answer_photo(photo=None, caption=None, **kw):
                replies.append((caption, kw.get("reply_markup")))
            msg = SimpleNamespace(
                successful_payment=SimpleNamespace(invoice_payload=f"subscription:{p.uid}", telegram_payment_charge_id=f"sim-{p.uid}-{datetime.now(UTC).timestamp()}",
                                                   total_amount=int(spec["price"]), is_recurring=False, subscription_expiration_date=None),
                from_user=SimpleNamespace(id=p.uid), date=datetime.now(UTC), bot=bot, answer=answer, answer_photo=answer_photo, chat=SimpleNamespace(id=p.uid))
            await successful_payment(msg)
            note(p, f"⭐ Оплатил подписку звёздами ({spec['price']} ⭐) · {state_line(p)}")
            for text, markup in replies:
                p.events.append({"at": datetime.now(UTC), "type": "bot", "title": "Ответ на оплату", "kind": "pay_done", "text": text or "",
                                 "buttons": _buttons(markup)})
        elif action == "pay_card":
            r = await client.post("/api/pay/order", headers=headers(p), json={"method": "crypto", "kind": "subscription"})
            body = await r.json()
            if r.status != 200:
                note(p, f"💳 Оплата картой/криптой не открылась: {body.get('error')}")
                return
            from db import pay_orders
            order = pay_orders.get_order(body["order_id"])
            raw = json.dumps({"update_id": 1, "update_type": "invoice_paid", "payload": {
                "invoice_id": 1, "status": "paid", "currency_type": "fiat", "fiat": "USD", "amount": f"{order['amount_cents'] / 100:.2f}",
                "payload": order["order_id"]}}).encode()
            sig = hmac.new(hashlib.sha256(CRYPTO_TOKEN.encode()).digest(), raw, hashlib.sha256).hexdigest()
            r = await client.post("/api/pay/webhook/cryptobot", data=raw, headers={"Content-Type": "application/json", "crypto-pay-api-signature": sig})
            note(p, f"💳 Оплатил подписку картой/криптой (${order['amount_cents'] / 100:.2f}) → вебхук {r.status} · {state_line(p)}")

    # Все рассылки из main.py — тем же списком, что в планировщике (кроме служебных: бэкап, админам, синхронизация ников).
    import adam_proactive, channel_sync, coach, goal_feedback, morning_ping, onboarding_auto, referral_mailing, streak_scheduler, \
        subscription_renewal, subscription_scheduler  # noqa: E401
    from db.bulk import preload
    jobs = [
        streak_scheduler.run_streak_rollover, coach.run_task_reminder_check, coach.run_planned_time_reminders, coach.run_weekly_report,
        coach.run_weekly_habit_analysis, coach.run_monthly_habit_analysis, morning_ping.run_morning_ping, goal_feedback.run_goal_feedback,
        onboarding_auto.run_auto_approve, streak_scheduler.run_streak_risk_notifications, channel_sync.run_channel_sync,
        subscription_renewal.run_renewal_reminders, streak_scheduler.run_streak_reengagement_notifications, streak_scheduler.run_weekly_streak_bonus,
        streak_scheduler.run_personal_record_notifications, streak_scheduler.run_pair_quest_notifications, streak_scheduler.run_rescue_notifications,
        coach.run_habit_checkpoint_12, coach.run_habit_checkpoint_17, coach.run_habit_checkpoint_22, coach.run_day_progress_check,
        coach.run_ai_welcome_nudge, adam_proactive.run_adam_checkins, referral_mailing.run_referral_mailing, coach.run_week_start_ping,
        coach.run_week_end_ping, coach.run_month_start_ping, coach.run_month_end_ping, subscription_scheduler.run_trial_reminders,
    ]
    errors = {}

    # Люди появляются в базе, как после /start: аккаунт, анкета пройдена, доступ открыт (дальше — триал по общему гейту).
    for p in people:
        add_user(p.uid, f"sim{p.uid}", p.name)
        c = connect()
        c.execute("UPDATE users SET access_status='approved', created_at=? WHERE telegram_id=?",
                  (start_utc.strftime("%Y-%m-%d %H:%M:%S"), p.uid))           # CURRENT_TIMESTAMP — по реальным часам, ставим время симуляции
        c.commit()
        c.close()

    t = start_utc
    end = start_utc + timedelta(days=days + 1)
    while t < end:
        freezer.move_to(t)
        for p in people:
            loc = local(p, t)
            day = (loc.date() - local(p, start_utc).date()).days        # день 1 — первый ПОЛНЫЙ местный день после старта
            for action in p.plan.get((day, loc.strftime("%H:%M")), []):
                try:
                    await act(p, action)
                except Exception as exc:
                    note(p, f"⚠️ Действие «{action}» упало: {type(exc).__name__}: {exc}")
            if p.tz_late_hours and t >= start_utc + timedelta(hours=p.tz_late_hours) and not getattr(p, "_tz_sent", False):
                p._tz_sent = True
                await client.post("/api/streak/timezone", headers=headers(p), json={"timezone": p.tz})
                note(p, f"🌍 Приложение сообщило настоящий пояс {p.tz} · {state_line(p)}")
        for job in jobs:
            try:
                with preload():
                    if job is streak_scheduler.run_streak_rollover:
                        await job()
                    else:
                        await job(bot)
            except Exception as exc:
                errors.setdefault(job.__name__, f"{type(exc).__name__}: {exc}")
        outbox.flush()
        t += TICK
    for task in [x for x in asyncio.all_tasks() if x is not asyncio.current_task()]:
        task.cancel()
    await client.close()
    return people, errors


def render(people, errors, days, start_utc):
    def esc(x):
        return html.escape(str(x or ""))

    cards = []
    for p in people:
        rows = []
        last_day = None
        for e in sorted(p.events, key=lambda e: e["at"]):
            loc = e["at"].astimezone(ZoneInfo(p.tz))
            day = (loc.date() - start_utc.astimezone(ZoneInfo(p.tz)).date()).days
            if day != last_day:
                label = "Регистрация (день до старта)" if day < 1 else f"День {day}"
                rows.append(f'<div class="day">{label} · {loc.strftime("%d.%m")}</div>')
                last_day = day
            if e["type"] == "action":
                rows.append(f'<div class="ev act"><time>{loc:%H:%M}</time><div>{esc(e["text"])}</div></div>')
            else:
                text = esc(e["text"]).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>").replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>").replace("\n", "<br>")
                btns = "".join(f"<span class=btn>{esc(b)}</span>" for b in e["buttons"])
                rows.append(f'<div class="ev bot"><time>{loc:%H:%M}</time><div><div class="ttl">🤖 {esc(e["title"] or e["kind"] or "Сообщение бота")}</div>'
                            f'<div class="txt">{text}</div>{"<div class=btns>" + btns + "</div>" if btns else ""}</div></div>')
        n_bot = sum(1 for e in p.events if e["type"] == "bot")
        cards.append(f'<section class="card"><h2>{esc(p.name)} — {esc(p.title)}</h2><p class="about">{esc(p.about)}<br>Пояс: {esc(p.tz)} · '
                     f'сообщений бота за {days} дн.: <b>{n_bot}</b></p>{"".join(rows)}</section>')
    err = "".join(f"<li><code>{esc(k)}</code>: {esc(v)}</li>" for k, v in errors.items())
    summary = "".join(
        f"<tr><td>{esc(p.name)}</td><td>{esc(p.title)}</td><td class=n>{sum(1 for e in p.events if e['type'] == 'bot')}</td>"
        f"<td>{esc(next((e['text'] for e in reversed(p.events) if e['type'] == 'action' and 'доступ:' in e['text']), ''))[-120:]}</td></tr>"
        for p in people)
    return f"""<title>Симуляция триала ADAM</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@600;700&display=swap">
<style>
/* Лента событий: по карточке на человека, внутри — дни и строки «время · событие». Действия человека и сообщения бота — разным фоном. */
:root{{--bg:#f5f4f9;--card:#ffffff;--ink:#1c1a28;--mut:#646078;--act:#eaf0ff;--bot:#ecf7ea;--line:#dfdce9;--accent:#5b45e0;
  --display:"Space Grotesk",system-ui,sans-serif;--body:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#13111b;--card:#1c1928;--ink:#ebe8f5;--mut:#9b97ae;--act:#1d2340;--bot:#16281a;--line:#2d2940;--accent:#a593ff;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#13111b;--card:#1c1928;--ink:#ebe8f5;--mut:#9b97ae;--act:#1d2340;--bot:#16281a;--line:#2d2940;--accent:#a593ff;color-scheme:dark}}
body{{background:var(--bg);color:var(--ink);font:15px/1.5 var(--body)}}
main{{max-width:860px;margin:0 auto;padding-block:20px 40px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}}
h1,h2{{font-family:var(--display);text-wrap:balance}} h1{{font-size:24px;margin:4px 0 8px}} .lead{{color:var(--mut);max-width:65ch}}
.tbl{{overflow-x:auto;margin:14px 0 18px}} table{{width:100%;border-collapse:collapse;font-size:14px;min-width:520px}}
td,th{{border-bottom:1px solid var(--line);padding:7px 6px;text-align:left;vertical-align:top}} th{{color:var(--mut);font-weight:600;font-size:12px;letter-spacing:.04em;text-transform:uppercase}}
td.n{{font-variant-numeric:tabular-nums}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:16px 0}} .card h2{{font-size:18px;margin:0 0 4px}}
.about{{color:var(--mut);margin:0 0 8px;font-size:14px}} .day{{font-family:var(--display);font-weight:700;margin:16px 0 6px;color:var(--accent)}}
.ev{{display:flex;gap:10px;padding:8px 10px;border-radius:8px;margin:4px 0}} .ev>div{{min-width:0}}
.ev time{{font-variant-numeric:tabular-nums;color:var(--mut);min-width:42px}}
.act{{background:var(--act)}} .bot{{background:var(--bot)}} .ttl{{font-weight:600;font-size:13px;color:var(--mut)}} .txt{{overflow-wrap:anywhere}}
.btns{{margin-top:6px;display:flex;flex-wrap:wrap;gap:6px}} .btn{{border:1px solid var(--line);border-radius:6px;padding:2px 8px;font-size:13px}}
</style>
<main>
<h1>Симуляция триала: {len(people)} человек, {days} дней</h1>
<p class="lead">Временная база, время «сжато» по 15 минут, всё остальное — настоящий код: открытие приложения, отметки, оплата и все рассылки бота
по расписанию. Время у каждого человека — местное. ИИ отключён: где текст пишет модель, видно запасной текст. Регистрация: {start_utc:%d.%m %H:%M} UTC.
Голубые строки — действия человека, зелёные — сообщения бота.</p>
<div class="tbl"><table><tr><th>Человек</th><th>Сценарий</th><th>Сообщений бота</th><th>Чем закончилось</th></tr>{summary}</table></div>
{"<p><b>Ошибки рассылок за прогон:</b></p><ul>" + err + "</ul>" if err else "<p>Ошибок в рассылках за прогон не было.</p>"}
{"".join(cards)}
</main>"""


def main():
    global freezer
    parser = argparse.ArgumentParser(description="Симуляция триала на настоящем коде")
    parser.add_argument("--days", type=int, default=6)
    parser.add_argument("--out", default=os.path.join(ROOT, "trial_simulation.html"))
    args = parser.parse_args()
    now = datetime.now(UTC)
    start = now.replace(hour=12, minute=0, second=0, microsecond=0)      # регистрация сегодня в 12:00 UTC; день 1 — следующий полный местный день
    with freeze_time(start, real_asyncio=True) as frozen:
        freezer = frozen
        people, errors = asyncio.run(simulate(args.days, start))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render(people, errors, args.days, start))
    print(f"Готово: {args.out}")
    for p in people:
        print(f"  {p.name}: сообщений бота {sum(1 for e in p.events if e['type'] == 'bot')}, событий {len(p.events)}")
    if errors:
        print("Ошибки рассылок:", errors)


freezer = None

if __name__ == "__main__":
    main()
