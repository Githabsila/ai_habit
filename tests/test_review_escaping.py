"""
Прогон 07.11: тексты, которые пишут сами люди (имена, названия привычек, задачи, отзывы) и ответы ИИ, уходили в сообщения бота с HTML
(DefaultBotProperties(parse_mode=HTML)) без экранирования. Символ «<» ломал разбор — сообщение не доходило вовсе (отзыв из Mini App,
жалоба на игрока, ответ ИИ после списанного лимита, сверка плана в 19:00, разбор недели), а «<a href>» в имени давал ссылку от имени бота
в общем рейтинге и в пушах «тебя обогнали».
"""
import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import config
from db import add_user
from tests.conftest import sign_init_data

ROOT = Path(__file__).resolve().parent.parent
_counter = itertools.count(871_000_000, 100)


@pytest.fixture
def uid():
    return next(_counter)


def _src(path):
    return (ROOT / path).read_text(encoding="utf-8")


async def test_review_feedback_with_angle_brackets_reaches_the_admins(client, uid, monkeypatch):
    add_user(uid, "f", "Аня")
    monkeypatch.setattr(config, "ADMIN_IDS", [uid + 1])
    monkeypatch.setattr("webapp.webapp_server.ADMIN_IDS", [uid + 1], raising=False)
    bot = SimpleNamespace(send_message=AsyncMock())
    client.app["bot"] = bot
    r = await client.post("/api/feedback", json={"text": "Кнопка <Сохранить> не работает", "tab": "home"},
                          headers={"Authorization": f"tma {sign_init_data(uid)}"})
    assert r.status == 200
    assert bot.send_message.await_args.kwargs.get("parse_mode", "HTML") is None, "текст человека — без HTML-разбора"


def test_review_bot_texts_escape_what_people_write():
    assert "html.escape(str(user['first_name'] or ''))" in _src("handlers/rating.py"), "имя в общем рейтинге"
    assert 'name = html.escape(str(item["overtaker_name"] or "Кто-то"))' in _src("streak_scheduler.py"), "пуш «тебя обогнали»"
    assert "html.escape(answer, quote=False)" in _src("handlers/ai.py"), "ответ ИИ в чате бота"
    habits = _src("handlers/habits.py")
    for needle in ("{html.escape(str(title))}", "{html.escape(str(habit['title']))}", "{html.escape(str(new_title))}"):
        assert needle in habits, needle
    coach = _src("coach.py")
    assert "«{html.escape(str(item['text']), quote=False)}»" in coach, "сверка плана в 19:00"
    assert coach.count("+ html.escape(ai_text, quote=False)") == 2, "разбор недели и месяца"
    assert _src("adam_proactive.py").count("parse_mode=None") >= 2, "«Адам пишет первым» — обычный текст"
    server = _src("webapp/webapp_server.py")
    assert server.count("text=message, parse_mode=None)") >= 2, "отзыв из Mini App и жалоба на игрока"


async def test_review_a_huge_but_tiny_png_avatar_is_refused_before_decoding(client, uid):
    """Прогон 07.11 (miniapp-api): маленький PNG огромного разрешения разворачивался в сотни мегабайт памяти."""
    from io import BytesIO
    from aiohttp import FormData
    from PIL import Image
    add_user(uid, "a", "Аня")
    buf = BytesIO()
    Image.new("L", (6000, 6000), 0).save(buf, "PNG")            # 36 млн пикселей, файл — десятки КБ
    assert len(buf.getvalue()) < 5 * 1024 * 1024
    form = FormData()
    form.add_field("avatar", buf.getvalue(), filename="a.png", content_type="image/png")
    r = await client.post("/api/profile/avatar", data=form, headers={"Authorization": f"tma {sign_init_data(uid)}"})
    assert r.status == 400 and (await r.json())["error"] == "unsupported_image"


async def test_review_the_daily_tip_cannot_be_hammered(client, uid, monkeypatch):
    """Прогон 07.11 (miniapp-api): совет дня звал платную модель на каждый запрос без ограничений."""
    import webapp.routes_ai_miniapp as r
    add_user(uid, "t", "Аня")
    calls = []

    async def fake_tip(ctx, style):
        calls.append(1)
        return "Совет"

    monkeypatch.setattr(r, "generate_daily_tip", fake_tip)
    statuses = []
    for _ in range(r.AI_BURST_LIMIT + 3):
        resp = await client.post("/api/ai/tip", json={"init_data": sign_init_data(uid)})
        statuses.append(resp.status)
    assert len(calls) == r.AI_BURST_LIMIT and statuses[-1] == 429


def test_review_2026_11_09_more_texts_people_write_are_escaped():
    """Прогон 09.11: ещё места, где текст людей или ответ ИИ уходил в HTML-сообщение без экранирования (вехи, разбор цели, утро в 6:00,
    карточка в админке, сообщество, профиль, простая точка дня, напоминание о главной цели) и сообщения админам с полями вебхука."""
    import adam_messages
    simple = adam_messages.format_simple_habit_checkpoint_message([{"title": "Сон <23:00 & чтение"}])
    assert "&lt;23:00 &amp; чтение" in simple and "<23:00" not in simple
    goal = adam_messages.format_goal_reminder_message("A&B <x>")
    assert "A&amp;B &lt;x&gt;" in goal
    assert _src("handlers/goals.py").count("html.escape(str(survey['bot_goal'] or ''), quote=False)") == 2, "экран вех"
    assert "html.escape(feedback, quote=False)" in _src("goal_feedback.py"), "разбор цели от ИИ"
    assert "reply_markup=adam_chat_markup(), parse_mode=None)" in _src("morning_ping.py"), "утреннее сообщение ИИ"
    admin = _src("handlers/admin.py")
    for needle in ("_e(survey['business'] or '-')", "_e(survey['ai_summary'])", "_e(user['first_name'] or '-')", 'snippet = _e(row["message"][:120])'):
        assert needle in admin, needle
    community = _src("handlers/community.py")
    assert "return html.escape(f\"@{row['username']}\"" in community and "html.escape(', '.join(match['common_tags']), quote=False)" in community
    assert 'html.escape(str(user["first_name"] or ""), quote=False)' in _src("handlers/profile.py")
    assert 'text=f"🩺 {text}", parse_mode=None)' in _src("alerts.py") and "send_message(admin_id, text, parse_mode=None)" in _src("pay_tribute.py")


def test_review_the_process_stops_when_the_web_server_cannot_start():
    """Прогон 09.11: без веб-сервера (Mini App, вебхуки оплаты) процесс жил дальше одним ботом — Railway не перезапускал, никто не замечал."""
    main = _src("main.py")
    block = main[main.index("web_runner = await run_webapp(PORT, bot)"):][:600]
    assert "raise" in block and "web_runner = None" not in block


def test_review_a_like_minded_match_shows_only_what_unites_them(uid):
    """Решение владельца 09.11: «Найти единомышленника» показывает незнакомцу только общие интересы — не ИИ-резюме чужой анкеты."""
    import json
    from db.core import connect
    from db.onboarding import find_match_by_tags
    me, other = uid, uid + 1
    for person, tags, summary in ((me, ["бег", "чтение"], "мой разбор"), (other, ["Бег", "бизнес"], "Разведён, долги, ищет работу")):
        add_user(person, f"m{person}", "Аня")
        conn = connect()
        try:
            conn.execute("UPDATE users SET access_status='approved', banned=0 WHERE telegram_id=?", (person,))
            conn.execute("INSERT OR REPLACE INTO user_survey(user_id, ai_tags, ai_summary) VALUES (?,?,?)", (person, json.dumps(tags, ensure_ascii=False), summary))
            conn.commit()
        finally:
            conn.close()
    found = [m for m in find_match_by_tags(me, limit=50) if m["telegram_id"] == other]
    assert found and found[0]["common_tags"] == ["бег"]
    assert "ai_summary" not in found[0] and "Разведён" not in str(found[0])


def test_review_2026_11_09_clear_wording_for_a_newcomer():
    """Проверка удобства 09.11: новичку без серии не пишем «Серия сброшена», анкета и первое сообщение Адаму — без мужского рода."""
    app = _src("webapp/static/app.js")
    assert "Отметь первую привычку — и серия начнётся." in app and "Зачем тебе ADAM?" in app and "Зачем ты пришёл" not in app
    assert "только что прошёл тест" not in _src("webapp/static/ai_coach.js")
