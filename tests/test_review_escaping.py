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
