"""
Последний обработчик: текст в личке, который никто не взял.

Найдено в живом чате @aihabbit_bot (05.11): «привет, как добавить привычку?» и /refund от обычного человека оставались
без ответа — у бота нет общего текстового обработчика, все разговоры живут в приложении. А Адам сам пишет в бот
(«У меня к тебе вопрос») — люди отвечают прямо в чате и получали тишину, как будто бот сломан.

Подключается В САМОМ КОНЦЕ (main.py): анкета, добавление привычки и прочие режимы (FSM-состояния) идут раньше и
этот обработчик не трогает — он ловит только сообщения без состояния. Группы не трогаем.
"""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from keyboards import adam_chat_markup

router = Router()

FALLBACK_TEXT = (
    "Я отвечаю в приложении — там наш чат, твои привычки и план дня. "
    "Нажми кнопку ниже и напиши мне там 👇"
)


@router.message(StateFilter(None), F.chat.type == "private", F.text)
async def unhandled_text(message: Message):
    await message.answer(FALLBACK_TEXT, reply_markup=adam_chat_markup())
