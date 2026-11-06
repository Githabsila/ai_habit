"""
Последний обработчик: текст в личке, который никто не взял.

Найдено в живом чате @aihabbit_bot (05.11): «привет, как добавить привычку?» и /refund от обычного человека оставались
без ответа — у бота нет общего текстового обработчика, все разговоры живут в приложении. А Адам сам пишет в бот
(«У меня к тебе вопрос») — люди отвечают прямо в чате и получали тишину, как будто бот сломан.

Захват задач (план, волна 1, п.2): написал боту «купить подарок маме» — «Добавить в план? На сегодня / На завтра / Не надо».
Самый быстрый способ поймать мысль, не открывая приложение. Пока у админов и по флагу bot_quick_capture (админка → флаги),
остальным — прежний ответ со входом в чат с Адамом.

Подключается В САМОМ КОНЦЕ (main.py): анкета, добавление привычки и прочие режимы (FSM-состояния) идут раньше и
этот обработчик не трогает — он ловит только сообщения без состояния. Группы не трогаем.
"""
import html

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_IDS
from keyboards import adam_chat_markup, open_plan_markup

router = Router()

FALLBACK_TEXT = (
    "Я отвечаю в приложении — там наш чат, твои привычки и план дня. "
    "Нажми кнопку ниже и напиши мне там 👇"
)
CAPTURE_FLAG = "bot_quick_capture"
CAPTURE_MIN_CHARS = 2


def capture_enabled(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        from db.feature_flags import is_feature_enabled
        return is_feature_enabled(CAPTURE_FLAG, user_id)
    except Exception:
        return False


def capture_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 На сегодня", callback_data="capture:today"),
         InlineKeyboardButton(text="📅 На завтра", callback_data="capture:tomorrow")],
        [InlineKeyboardButton(text="✖ Не надо", callback_data="capture:no")],
    ])


def capture_question(text):
    return f"Добавить в план?\n\n«{html.escape(text)}»"


@router.message(StateFilter(None), F.chat.type == "private", F.text)
async def unhandled_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if capture_enabled(message.from_user.id) and not text.startswith("/") and len(text) >= CAPTURE_MIN_CHARS:
        from db.daily_plan import MAX_DAILY_TASK_TEXT_LENGTH
        await state.update_data(capture_text=text[:MAX_DAILY_TASK_TEXT_LENGTH])
        await message.answer(capture_question(text[:MAX_DAILY_TASK_TEXT_LENGTH]), parse_mode="HTML", reply_markup=capture_keyboard())
        return
    await message.answer(FALLBACK_TEXT, reply_markup=adam_chat_markup())


@router.callback_query(F.data.startswith("capture:"))
async def capture_choice(callback: CallbackQuery, state: FSMContext):
    from db.daily_plan import add_daily_task, get_daily_plan, tomorrow_plan_date
    from db.tracking import track_server
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    text = data.get("capture_text")
    await state.update_data(capture_text=None)
    if choice == "no" or not text:
        await callback.message.edit_text("Ок, не добавляю." if text else "Это уже устарело — напиши задачу ещё раз.")
        await callback.answer()
        return
    user_id = callback.from_user.id
    plan_date = tomorrow_plan_date(user_id) if choice == "tomorrow" else None
    try:
        add_daily_task(user_id, text, plan_date=plan_date)
    except ValueError:
        await callback.message.edit_text("В плане уже 9 задач — это максимум. Закрой или убери что-нибудь в приложении.",
                                         reply_markup=open_plan_markup())
        await callback.answer()
        return
    count = len(get_daily_plan(user_id, plan_date)["tasks"])
    when = "на завтра" if plan_date else "на сегодня"
    track_server(user_id, "task_captured_in_bot", choice)
    await callback.message.edit_text(
        f"✅ В плане {when}: «{html.escape(text)}»\nЗадач в плане: {count}.",
        parse_mode="HTML", reply_markup=open_plan_markup(),
    )
    await callback.answer()
