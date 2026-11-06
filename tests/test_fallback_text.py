"""
Текст в личке, который никто не взял, получает ответ со входом в чат с Адамом (handlers/fallback.py).

Найдено в живом чате @aihabbit_bot: «привет, как добавить привычку?» оставалось без ответа. Проверяем через настоящий
Dispatcher с настоящими роутерами: обычный текст — ответ; команды и режимы (FSM) других роутеров перехват не ломает.
"""
from datetime import datetime

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

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
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
