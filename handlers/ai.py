from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from db import (
    add_ai_message,
    get_ai_history,
    get_progress,
    get_habits,
    save_ai_feedback,
)

from keyboards import ai_keyboard, back_menu_keyboard, ai_feedback_keyboard

from multi_agent import solve_task_multiagent

router = Router()


# =====================================
# СОСТОЯНИЯ
# =====================================

class AiState(StatesGroup):
    chatting = State()


# =====================================
# ПЕРСОНАЛИЗАЦИЯ -> КОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ
# =====================================

def build_user_context(user_id: int) -> str:
    """
    Собирает реальные данные пользователя (уровень, Adam Coin, серию дней
    подряд, привычки и что из них выполнено сегодня) в компактный текстовый
    блок для мультиагентного пайплайна. Передаётся отдельным полем, не
    смешиваясь с историей переписки — чтобы советы опирались на конкретные
    факты, а не на общие фразы.
    """
    progress = get_progress(user_id)
    if not progress:
        return ""

    habits = get_habits(user_id)

    lines = [
        f"Уровень: {progress['level']}, Adam Coin: {progress['xp']}",
        f"Серия дней подряд: {progress['streak']}",
    ]

    if habits:
        lines.append("Привычки пользователя (выполнено сегодня?):")
        for h in habits:
            status = "да" if h["completed"] else "нет"
            lines.append(f"  • {h['title']} — {status}")
    else:
        lines.append("Привычек пока не добавлено.")

    return "\n".join(lines)


# =====================================
# ИСТОРИЯ ПЕРЕПИСКИ -> ТЕКСТОВЫЙ КОНТЕКСТ
# =====================================

def build_history_text(user_id: int, limit: int = 4, max_chars_per_msg: int = 200) -> str:
    """
    Берёт последние сообщения переписки с AI-наставником из БД и
    превращает их в текстовый блок-контекст для мультиагентного пайплайна.

    limit уменьшен до 4 (было 10), а сообщения обрезаются — история
    используется в двух стадиях пайплайна, и её длина напрямую бьёт
    по лимиту токенов в минуту (TPM) на бесплатном тарифе Groq.
    """
    history = get_ai_history(user_id)
    if not history:
        return ""

    lines = []
    for row in history[-limit:]:
        role = "Пользователь" if row["role"] == "user" else "Наставник"
        text = row["message"]
        if len(text) > max_chars_per_msg:
            text = text[:max_chars_per_msg] + "…"
        lines.append(f"{role}: {text}")

    return "\n".join(lines)


# =====================================
# ВХОД В РЕЖИМ ОБЩЕНИЯ С AI
# =====================================

@router.callback_query(F.data == "ai")
async def ai_start(callback: CallbackQuery, state: FSMContext):

    await state.set_state(AiState.chatting)

    text = """
🤖 <b>AI-наставник</b>

Просто напишите мне сообщение — я отвечу как персональный
коуч по привычкам и продуктивности.

Чтобы выйти, нажмите «Главное меню».
"""

    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=ai_keyboard()
        )
    except TelegramBadRequest as e:
        # Пользователь повторно открыл AI-режим, когда сообщение уже
        # с таким же текстом и клавиатурой — это не ошибка, просто
        # Telegram запрещает "обновлять" сообщение тем же содержимым.
        if "message is not modified" not in str(e):
            raise

    await callback.answer()


# =====================================
# ОБЩЕНИЕ С AI (МУЛЬТИАГЕНТНАЯ СИСТЕМА)
# =====================================

@router.message(AiState.chatting)
async def ai_chat(message: Message, state: FSMContext):

    await message.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    thinking_msg = await message.answer("🤔 Думаю над ответом...")

    history_text = build_history_text(message.from_user.id)
    user_context = build_user_context(message.from_user.id)

    try:
        answer = await solve_task_multiagent(
            task=message.text,
            history=history_text,
            user_context=user_context
        )
    except Exception as e:
        # Пользователю — общее сообщение, сырой текст исключения только в лог
        print(f"❌ Ошибка AI-пайплайна для {message.from_user.id}: {e}")
        answer = (
            "❌ Не получилось сформировать ответ. Попробуйте ещё раз "
            "через минуту."
        )

    add_ai_message(message.from_user.id, "user", message.text)
    assistant_message_id = add_ai_message(message.from_user.id, "assistant", answer)

    try:
        await thinking_msg.edit_text(
            answer,
            reply_markup=ai_feedback_keyboard(assistant_message_id)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# =====================================
# ОБРАТНАЯ СВЯЗЬ ПО ОТВЕТУ AI
# =====================================

@router.callback_query(F.data.startswith("ai_fb_"))
async def ai_feedback(callback: CallbackQuery):

    _, _, rating, message_id = callback.data.split("_")

    save_ai_feedback(int(message_id), callback.from_user.id, rating)

    await callback.answer(
        "Спасибо за оценку! 🙏" if rating == "up" else "Спасибо, учту в следующий раз 🙏"
    )
