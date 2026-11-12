"""
Текст в личке, который никто не взял, получает ответ со входом в чат с Адамом (handlers/fallback.py).

Найдено в живом чате @aihabbit_bot: «привет, как добавить привычку?» оставалось без ответа. Проверяем через настоящий
Dispatcher с настоящими роутерами: обычный текст — ответ; команды и режимы (FSM) других роутеров перехват не ломает.
"""
from datetime import datetime

from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

import handlers.fallback as fallback
import handlers.habits as habits
import handlers.payments as payments
import keyboards


class _RecordingBot(Bot):
    """Вместо Telegram — список вызванных методов."""

    def __init__(self):
        super().__init__(token="123456:TEST_TOKEN_NOT_REAL")
        self.calls = []
        self.next_message_id = 50

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):                # вопрос «Добавить в план?» запоминается по номеру сообщения (12.11)
            self.next_message_id += 1
            return SimpleNamespace(message_id=self.next_message_id - 1)
        return True


@pytest.fixture(scope="module")
def dp():
    dispatcher = Dispatcher()
    dispatcher.include_router(payments.router)
    dispatcher.include_router(habits.router)
    dispatcher.include_router(fallback.router)        # как в main.py — последним
    return dispatcher


_ids = iter(range(1, 10_000))


def _update(user_id, text, chat_type="private"):
    chat = Chat(id=user_id if chat_type == "private" else -100500, type=chat_type)
    message = Message(message_id=next(_ids), date=datetime.now(), chat=chat, text=text,
                      from_user=User(id=user_id, is_bot=False, first_name="Аня"))
    return Update(update_id=next(_ids), message=message)


def _sent_texts(bot):
    return [c.text for c in bot.calls if isinstance(c, SendMessage)]


async def test_plain_text_gets_a_way_into_the_chat_with_adam(dp, monkeypatch):
    monkeypatch.setattr(keyboards, "WEBAPP_URL", "https://example.test")
    monkeypatch.setattr(fallback, "capture_enabled", lambda _uid: False)         # захват задач выключен — прежний ответ
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(871_000_001, "привет, как добавить привычку?"))
    sent = [c for c in bot.calls if isinstance(c, SendMessage)]
    assert len(sent) == 1 and sent[0].text == fallback.FALLBACK_TEXT
    button = sent[0].reply_markup.inline_keyboard[0][0]
    assert button.web_app.url == "https://example.test/coach"


async def test_commands_of_other_routers_still_win(dp, monkeypatch):
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(871_000_002, "/refund"))
    texts = _sent_texts(bot)
    assert len(texts) == 1 and "Возврат оплаты" in texts[0], "ответил /refund, а не запасной обработчик"


async def test_text_inside_a_mode_is_not_intercepted(dp):
    user_id = 871_000_003
    bot = _RecordingBot()
    key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
    await FSMContext(storage=dp.storage, key=key).set_state(habits.HabitState.title)
    await dp.feed_update(bot, _update(user_id, "Читать 20 минут"))
    assert fallback.FALLBACK_TEXT not in _sent_texts(bot)


async def test_groups_are_left_alone(dp):
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(871_000_004, "всем привет", chat_type="supergroup"))
    assert _sent_texts(bot) == []


# ---------------------------------------------------------------- захват задач из чата (план, волна 1, п.2)

def _callback(user_id, data, message_id):
    from aiogram.types import CallbackQuery
    chat = Chat(id=user_id, type="private")
    msg = Message(message_id=message_id, date=datetime.now(), chat=chat, text="Добавить в план?",
                  from_user=User(id=1, is_bot=True, first_name="ADAM"))
    return Update(update_id=next(_ids), callback_query=CallbackQuery(id=str(next(_ids)), from_user=User(id=user_id, is_bot=False, first_name="Аня"),
                                                                       chat_instance="ci", data=data, message=msg))


async def test_capture_adds_the_text_to_today_or_tomorrow(dp, monkeypatch, uid):
    from aiogram.methods import EditMessageText
    from db import add_user
    from db.daily_plan import get_daily_plan, tomorrow_plan_date
    add_user(uid, "a", "Аня")
    monkeypatch.setattr(fallback, "capture_enabled", lambda _uid: True)
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(uid, "Купить подарок маме"))
    question = [c for c in bot.calls if isinstance(c, SendMessage)][-1]
    assert "Добавить в план?" in question.text and "Купить подарок маме" in question.text
    assert [b.callback_data for row in question.reply_markup.inline_keyboard for b in row] == ["capture:today", "capture:tomorrow", "capture:no"]

    await dp.feed_update(bot, _callback(uid, "capture:today", 50))
    assert [t["text"] for t in get_daily_plan(uid)["tasks"]] == ["Купить подарок маме"]
    edited = [c for c in bot.calls if isinstance(c, EditMessageText)][-1]
    assert "В плане на сегодня" in edited.text

    await dp.feed_update(bot, _update(uid, "Записаться к врачу"))
    await dp.feed_update(bot, _callback(uid, "capture:tomorrow", 51))
    assert [t["text"] for t in get_daily_plan(uid, tomorrow_plan_date(uid))["tasks"]] == ["Записаться к врачу"]
    assert len(get_daily_plan(uid)["tasks"]) == 1

    await dp.feed_update(bot, _callback(uid, "capture:today", 52))            # повторное нажатие — второй раз не добавляет
    assert len(get_daily_plan(uid)["tasks"]) == 1


async def test_capture_can_be_declined_and_commands_are_not_captured(dp, monkeypatch, uid):
    from db import add_user
    from db.daily_plan import get_daily_plan
    add_user(uid, "a", "Аня")
    monkeypatch.setattr(fallback, "capture_enabled", lambda _uid: True)
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(uid, "Позвонить"))
    await dp.feed_update(bot, _callback(uid, "capture:no", 60))
    assert get_daily_plan(uid)["tasks"] == []
    await dp.feed_update(bot, _update(uid, "/unknowncmd"))
    assert _sent_texts(bot)[-1] == fallback.FALLBACK_TEXT


def test_capture_is_admins_only_until_the_flag_is_on(uid, monkeypatch):
    from db.feature_flags import set_feature_flag
    monkeypatch.setattr(fallback, "ADMIN_IDS", [uid])
    assert fallback.capture_enabled(uid) is True
    assert fallback.capture_enabled(uid + 1) is False
    set_feature_flag(fallback.CAPTURE_FLAG, True, 100)
    try:
        assert fallback.capture_enabled(uid + 1) is True
    finally:
        set_feature_flag(fallback.CAPTURE_FLAG, False, 100)


async def test_two_thoughts_in_a_row_each_button_adds_its_own(dp, monkeypatch, uid):
    """Прогон 11.11: кнопка под первым вопросом добавляла вторую мысль, а первая терялась."""
    from db import add_user
    from db.daily_plan import get_daily_plan
    add_user(uid, "a", "Аня")
    monkeypatch.setattr(fallback, "capture_enabled", lambda _uid: True)
    bot = _RecordingBot()
    await dp.feed_update(bot, _update(uid, "Купить подарок маме"))       # вопрос №50
    await dp.feed_update(bot, _update(uid, "Позвонить врачу завтра"))    # вопрос №51
    await dp.feed_update(bot, _callback(uid, "capture:today", 50))
    assert [t["text"] for t in get_daily_plan(uid)["tasks"]] == ["Купить подарок маме"]
