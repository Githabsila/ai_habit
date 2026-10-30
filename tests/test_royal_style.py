"""
Оформление «Royal Gold» (решение владельца 28.10.2026): премиальная графика бота и приложения с возвратом «как было».
Режимы classic / preview / bot / premium (db/style_mode.py), золотая шапка и баннеры в чате (bot_style.py), перекраска приложения (royal.js / royal.css) только тем, кому включено.
"""
import itertools
import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import EditMessageText, SendMessage

import bot_style
import config
import subscription_scheduler
import trial_messages
from db import add_user, style_mode
from tests.conftest import sign_init_data
from webapp import routes_admin

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STATIC = os.path.join(ROOT, "webapp", "static")
_ids = itertools.count(870_300_000, 10)
LONG = "Это длинное сообщение от Адама про привычки и серию: " + "я рядом и напомню вовремя. " * 3


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(autouse=True)
def default_mode():
    """Режим хранится в общей базе прогона: тест начинает с «предпросмотра», заканчивает «классикой» (как в tests/conftest.py, чтобы остальные тесты видели обычные сообщения)."""
    style_mode.set_mode("preview")
    yield
    style_mode.set_mode("classic")


@pytest.fixture
def admin(monkeypatch):
    person = next(_ids)
    monkeypatch.setattr(config, "ADMIN_IDS", [person])
    monkeypatch.setattr(routes_admin, "ADMIN_IDS", [person])
    return person


def _headers(person):
    return {"Authorization": f"tma {sign_init_data(person)}"}


# ---------------------------------------------------------------------------------------------------------------- режим

def test_the_default_is_the_bot_for_everyone_and_unknown_modes_are_refused(admin):
    assert style_mode.DEFAULT_MODE == "bot"
    assert style_mode.set_mode("neon") is False and style_mode.get_mode() == "preview"
    stranger = next(_ids)
    assert style_mode.premium_active(admin) is True and style_mode.premium_active(stranger) is False
    assert style_mode.bot_active(admin) is True and style_mode.bot_active(stranger) is False
    assert style_mode.premium_active(None) is False and style_mode.bot_active(0) is False


def test_each_mode_says_who_gets_the_app_and_who_gets_the_bot(admin):
    stranger = next(_ids)
    expected = {"classic": [(False, False), (False, False)], "preview": [(True, True), (False, False)],
                "bot": [(True, True), (False, True)], "premium": [(True, True), (True, True)]}      # (приложение, бот) для админа и для чужого
    for mode, (for_admin, for_stranger) in expected.items():
        assert style_mode.set_mode(mode, admin) is True
        assert [(style_mode.premium_active(p), style_mode.bot_active(p)) for p in (admin, stranger)] == [for_admin, for_stranger], mode


# ---------------------------------------------------------------------------------------------------------------- шапка и списки

def test_a_long_message_gets_the_gold_header_once_and_short_ones_stay_as_they_are():
    dressed = bot_style.dress(LONG)
    assert dressed.startswith(bot_style.HEADER + "\n") and dressed.endswith(LONG.strip())
    assert bot_style.dress(dressed) == dressed, "повторно не оформляем"
    assert bot_style.dress(bot_style.PLAIN_HEADER + "\n" + LONG) == bot_style.PLAIN_HEADER + "\n" + LONG, "хендлер вернул старый текст из message.text — шапка без тегов не дублируется"
    for short in ("Готово ✓", "Принято, спасибо!", ""):
        assert bot_style.dress(short) == short
    assert bot_style.dress(None) is None
    huge = "я " * 2500
    assert bot_style.dress(huge) == huge, "не пробиваем лимит Telegram в 4096 знаков"


def test_list_dashes_become_diamonds_and_dashes_before_an_icon_just_go_away():
    text = "Вместе мы будем:\n\n- 🎯 Развивать привычки\n- Следить за прогрессом\n• Искать единомышленников\n- 5 коинов за серию\n\nВсё это — в приложении, открой его кнопкой ниже."
    dressed = bot_style.dress(text)
    assert "\n🎯 Развивать привычки" in dressed and "\n◆ Следить за прогрессом" in dressed and "\n◆ Искать единомышленников" in dressed
    assert "\n- 5 коинов за серию" in dressed, "дефис перед числом не трогаем"
    assert "Всё это — в приложении" in dressed, "тире внутри предложения остаётся"


# ---------------------------------------------------------------------------------------------------------------- мидлварь Bot API

async def _through(method):
    seen = AsyncMock(return_value="sent")
    result = await bot_style.royal_dress(seen, "bot", method)
    return result, seen.call_args.args[1]


async def test_the_middleware_dresses_messages_only_for_people_with_the_style(admin):
    stranger = next(_ids)
    _, mine = await _through(SendMessage(chat_id=admin, text=LONG))
    assert mine.text.startswith(bot_style.HEADER)
    _, other = await _through(SendMessage(chat_id=stranger, text=LONG))
    assert other.text == LONG, "в режиме предпросмотра чужие чаты не меняются"
    for mode in ("bot", "premium"):
        style_mode.set_mode(mode)
        _, everyone = await _through(SendMessage(chat_id=stranger, text=LONG))
        assert everyone.text.startswith(bot_style.HEADER), mode
    style_mode.set_mode("classic")
    _, classic = await _through(SendMessage(chat_id=admin, text=LONG))
    assert classic.text == LONG, "«Классика» возвращает прежний вид целиком"


async def test_the_middleware_edits_messages_too_and_leaves_risky_ones_alone(admin):
    result, edited = await _through(EditMessageText(chat_id=admin, message_id=5, text=LONG))
    assert result == "sent" and edited.text.startswith(bot_style.HEADER)
    _, markdown = await _through(SendMessage(chat_id=admin, text=LONG, parse_mode="Markdown"))
    assert markdown.text == LONG, "в Markdown шапка с HTML-тегом сломала бы разбор"
    _, html_mode = await _through(SendMessage(chat_id=admin, text=LONG, parse_mode="HTML"))
    assert html_mode.text.startswith(bot_style.HEADER)
    _, with_entities = await _through(SendMessage(chat_id=admin, text=LONG, entities=[{"type": "bold", "offset": 0, "length": 4}]))
    assert with_entities.text == LONG, "у готовых entities сдвинулись бы смещения"
    _, channel = await _through(SendMessage(chat_id="@channel", text=LONG))
    assert channel.text == LONG
    _, inline = await _through(EditMessageText(inline_message_id="abc", text=LONG))
    assert inline.text == LONG


async def test_a_failure_while_dressing_never_stops_the_message(admin, monkeypatch):
    monkeypatch.setattr(bot_style, "dress", lambda text: (_ for _ in ()).throw(RuntimeError("сбой")))
    result, sent = await _through(SendMessage(chat_id=admin, text=LONG))
    assert result == "sent" and sent.text == LONG


# ---------------------------------------------------------------------------------------------------------------- баннеры

def _bot():
    return SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())


async def test_a_banner_goes_only_to_people_with_the_style_and_carries_the_text(admin):
    bot = _bot()
    stranger = next(_ids)
    assert await bot_style.send_banner(bot, stranger, "welcome", LONG) is False and not bot.send_photo.called
    assert await bot_style.send_banner(bot, admin, "welcome", "- Привычки\n- Цели", reply_markup="kb") is True
    args, kwargs = bot.send_photo.call_args
    assert args[0] == admin and kwargs["caption"] == "◆ Привычки\n◆ Цели" and kwargs["reply_markup"] == "kb" and not bot.send_message.called


async def test_a_long_caption_sends_the_picture_and_then_the_text(admin):
    bot = _bot()
    text = "длинный текст " * 120
    assert await bot_style.send_banner(bot, admin, "trial", text, reply_markup="kb") is True
    assert bot.send_photo.call_args.kwargs.get("caption") is None and bot.send_message.call_args.args == (admin, text)


async def test_a_banner_that_cannot_be_sent_hands_back_to_the_plain_message(admin):
    bot = _bot()
    bot.send_photo.side_effect = RuntimeError("Telegram отказал")
    assert await bot_style.send_banner(bot, admin, "paid", LONG) is False
    assert await bot_style.send_banner(bot, admin, "unknown", LONG) is False, "неизвестного баннера нет"
    assert await bot_style.send_banner(None, admin, "paid", LONG) is False


async def test_a_reply_without_bot_or_user_is_just_not_dressed():
    assert await bot_style.send_banner_reply(SimpleNamespace(), "welcome", LONG) is False


def test_every_banner_exists_and_is_a_proper_picture():
    from PIL import Image
    for kind in bot_style.BANNER_KINDS:
        path = bot_style.banner_path(kind)
        assert path, f"нет баннера {kind}: запусти tools/make_royal_banners.py"
        assert os.path.getsize(path) < 5_000_000
        with Image.open(path) as picture:
            assert picture.size == (1280, 640) and picture.format == "PNG"


def test_trial_banners_belong_to_real_trial_messages():
    assert subscription_scheduler.BANNER_KEYS <= set(trial_messages.BY_KEY)


# ---------------------------------------------------------------------------------------------------------------- админка и приложение

async def test_the_admin_can_read_and_switch_the_mode_and_nobody_else(client, admin):
    stranger = next(_ids)
    assert (await client.get("/api/admin/style", headers=_headers(stranger))).status == 403
    assert (await client.get("/api/admin/style")).status == 401
    body = await (await client.get("/api/admin/style", headers=_headers(admin))).json()
    assert body["mode"] == "preview" and [m["mode"] for m in body["modes"]] == ["classic", "preview", "bot", "premium"]
    done = await client.post("/api/admin/style", headers=_headers(admin), json={"mode": "premium"})
    assert done.status == 200 and style_mode.get_mode() == "premium"
    assert (await client.post("/api/admin/style", headers=_headers(admin), json={"mode": "neon"})).status == 400
    assert (await client.post("/api/admin/style", headers=_headers(stranger), json={"mode": "classic"})).status == 403
    assert style_mode.get_mode() == "premium"


@pytest.fixture
async def bot_client():
    """Приложение с подменным ботом: проверяем, что админу уходят образцы."""
    from aiohttp.test_utils import TestClient, TestServer
    from webapp.webapp_server import create_app
    bot = _bot()
    app_client = TestClient(TestServer(create_app(bot=bot)))
    await app_client.start_server()
    app_client.bot = bot
    yield app_client
    await app_client.close()


async def test_the_admin_gets_samples_in_the_bot_only_while_the_style_is_on_for_them(client, bot_client, admin):
    stranger = next(_ids)
    assert (await client.post("/api/admin/style/preview", headers=_headers(admin))).status == 503, "без бота показывать нечем"
    assert (await bot_client.post("/api/admin/style/preview", headers=_headers(stranger))).status == 403
    style_mode.set_mode("classic")
    assert (await bot_client.post("/api/admin/style/preview", headers=_headers(admin))).status == 409
    assert not bot_client.bot.send_photo.called and not bot_client.bot.send_message.called
    style_mode.set_mode("preview")
    done = await bot_client.post("/api/admin/style/preview", headers=_headers(admin))
    assert done.status == 200 and (await done.json())["banners"] == 3
    assert [call.args[0] for call in bot_client.bot.send_photo.call_args_list] == [admin] * 3
    assert bot_client.bot.send_message.call_args.args[0] == admin


async def test_the_app_learns_from_bootstrap_whether_to_wear_the_style(client, admin):
    stranger = next(_ids)
    for person in (admin, stranger):
        add_user(person, f"u{person}", "Аня")
    flags = {}
    for mode in ("classic", "preview", "bot", "premium"):
        style_mode.set_mode(mode)
        flags[mode] = [(await (await client.get("/api/bootstrap", headers=_headers(p))).json())["premium_style"] for p in (admin, stranger)]
    assert flags == {"classic": [False, False], "preview": [True, False], "bot": [True, False], "premium": [True, True]}, "золотое приложение у чужих — только в «Премиум»"


def test_the_app_pages_load_the_style_only_when_the_flag_is_set():
    index = _read(STATIC, "index.html")
    chat = _read(STATIC, "ai_miniapp_styled.html")
    for page in (index, chat):
        assert 'localStorage.getItem("adam_royal")==="1"' in page and "/static/royal.js?v=" in page
    assert re.search(r'<script src="/static/royal\.js', index) is None, "royal.js не подключается всем подряд"
    app = _read(STATIC, "app.js")
    assert "function applyRoyalStyle(on)" in app and "applyRoyalStyle(!!state.premium_style);" in app
    versions = set(re.findall(r"royal\.js\?v=(\w+)", index + chat + app))
    assert len(versions) == 1, "версия royal.js одинакова везде, иначе кеш покажет разное"


def test_the_style_files_follow_the_paint_diet():
    for name in ("royal.js", "royal.css"):
        source = _read(STATIC, name)
        code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        assert not re.search(r"(?<![\w-])(backdrop-)?filter\s*:", code), f"{name}: фильтры тормозят отрисовку (правила «диеты»)"
        assert "infinite" not in code, f"{name}: бесконечные анимации запрещены"


def test_the_recolor_logic_turns_purple_into_gold_and_leaves_everything_else():
    source = _read(STATIC, "royal.js")
    assert "window.RoyalGold" in source and "h >= 175 && h <= 300" in source, "оттенки фиолетового, синего и бирюзового идут в золото"
    assert "l > 0.97 || l < 0.015" in source, "белое и почти чёрное не трогаем"
