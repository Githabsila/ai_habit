from email import message
import time
import hashlib
import asyncio
import logging
from datetime import date, datetime
from webapp.services.ai_service import chat

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
    get_ai_style,
    add_habit,
    save_feedback_reason,
    get_recent_negative_reasons,
    get_user_profile,
    update_user_profile,
    bump_profile_counter,
    cache_get,
    cache_set,
    log_error,
    get_last_ai_message_at,
    touch_last_ai_message,
)

from keyboards import (
    ai_keyboard,
    back_menu_keyboard,
    ai_feedback_keyboard,
    ai_feedback_reason_keyboard,
    crisis_keyboard,
)

from multi_agent import solve_task_multiagent, generate_daily_tip, summarize_user_memory

router = Router()
logger = logging.getLogger("handlers.ai")

# Раз в столько сообщений пересобираем долгосрочный профиль пользователя
# (этап 2 AI Core: постоянная память).
MEMORY_UPDATE_EVERY = 6


# =====================================
# СОСТОЯНИЯ
# =====================================

class AiState(StatesGroup):
    chatting = State()


# =====================================
# ТРОТТЛИНГ
# =====================================
# Каждое сообщение — это несколько платных вызовов Groq, а состояние
# AiState.chatting ловит любое сообщение без ограничений. Троттлинг хранится
# в БД (users.last_ai_message_at), а не в памяти процесса — переживает
# рестарт/редеплой без Redis. Для нескольких инстансов бота за балансировщиком
# этого тоже достаточно, так как источник правды один — БД.

MIN_INTERVAL_SECONDS = 3.0


def _is_throttled(user_id: int) -> float | None:
    """Возвращает, сколько секунд осталось ждать, или None, если можно слать."""
    last_str = get_last_ai_message_at(user_id)
    if last_str:
        try:
            last_dt = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.utcnow() - last_dt).total_seconds()
            if elapsed < MIN_INTERVAL_SECONDS:
                return round(MIN_INTERVAL_SECONDS - elapsed, 1)
        except ValueError:
            pass
    touch_last_ai_message(user_id)
    return None


# =====================================
# КЭШ ПРЕДЛОЖЕННЫХ ПРИВЫЧЕК
# =====================================
# Судья мог явно посоветовать конкретную привычку — кнопка "➕ Добавить"
# рядом с ответом должна знать, что именно добавлять. Хранить название в
# callback_data напрямую рискованно (лимит Telegram — 64 байта, а название
# плюс префикс легко его превышают для длинных/кириллических строк), поэтому
# используем message_id как ключ к короткоживущему кэшу в памяти.

_suggested_habits: dict[int, str] = {}


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

    # Долгосрочная память (этап 2): факты о пользователе, накопленные из
    # прошлых разговоров, не только из текущей короткой истории.
    profile = get_user_profile(user_id)
    if profile and profile["summary"]:
        lines.append("\nЧто известно о пользователе из прошлых разговоров:")
        lines.append(profile["summary"])

    # "Обучение" на дизлайках (этап 2): не повторяем недавно отмеченные
    # проблемы в ответах этому конкретному пользователю.
    reasons = get_recent_negative_reasons(user_id, limit=3)
    if reasons:
        unique_reasons = list(dict.fromkeys(reasons))
        lines.append(
            "\nВ недавних ответах пользователь отмечал проблемы: "
            + "; ".join(unique_reasons)
            + ". Постарайся их не повторять."
        )

    return "\n".join(lines)


# =====================================
# КЭШ ОТВЕТОВ (простые/повторяющиеся сообщения)
# =====================================

def _cache_key(text: str, style: str) -> str:
    normalized = " ".join(text.strip().lower().split())
    return hashlib.md5(f"{normalized}|{style}".encode("utf-8")).hexdigest()


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
# ФОНОВОЕ ОБНОВЛЕНИЕ ДОЛГОСРОЧНОЙ ПАМЯТИ
# =====================================

async def _update_memory(user_id: int):
    try:
        profile = get_user_profile(user_id)
        existing_summary = profile["summary"] if profile else ""
        recent_history = build_history_text(user_id, limit=MEMORY_UPDATE_EVERY + 2)
        new_summary = await summarize_user_memory(existing_summary, recent_history)
        update_user_profile(user_id, new_summary)
    except Exception as e:
        logger.exception(f"Не удалось обновить профиль памяти для {user_id}")
        log_error("memory_update", e, user_id)


def _schedule_memory_update(user_id: int):
    """Каждые MEMORY_UPDATE_EVERY сообщений — фоновое пересобирание профиля.
    Запускается как отдельная задача (не await), чтобы не задерживать ответ
    пользователю — попутно даёт этап 4 "ускорение ответа"."""
    count = bump_profile_counter(user_id)
    if count >= MEMORY_UPDATE_EVERY:
        asyncio.create_task(_update_memory(user_id))


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

Стиль общения можно поменять в ⚙️ Настройках.

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

    wait = _is_throttled(message.from_user.id)
    if wait is not None:
        await message.answer(
            f"⏳ Не так быстро — подожди {wait} сек. и напиши ещё раз."
        )
        return

    await message.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    thinking_msg = await message.answer("🤔 Думаю над ответом...")

    user_id = message.from_user.id

    try:
        result = await chat(
            user_id=user_id,
            message=message.text,
        )

        answer = result["answer"]
        is_crisis = result["is_crisis"]
        suggested_habit = result["suggested_habit"]
        complexity = result["complexity"]

    except Exception as e:
        logger.exception(f"Ошибка AI-пайплайна для {user_id}")
        log_error("ai_pipeline", e, user_id)

        answer = (
            "❌ Не получилось сформировать ответ. "
            "Попробуйте ещё раз через минуту."
        )

        is_crisis = False
        suggested_habit = None
        complexity = None

    add_ai_message(user_id, "user", message.text)
    assistant_message_id = add_ai_message(user_id, "assistant", answer)

    if not is_crisis:
        _schedule_memory_update(user_id)

    if is_crisis:
        keyboard = crisis_keyboard()
    else:
        if suggested_habit:
            _suggested_habits[assistant_message_id] = suggested_habit

        keyboard = ai_feedback_keyboard(
            assistant_message_id,
            suggested_habit,
        )

    try:
        await thinking_msg.edit_text(
            answer,
            reply_markup=keyboard,
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
    message_id = int(message_id)

    save_ai_feedback(message_id, callback.from_user.id, rating)

    if rating == "down":
        # Необязательный уточняющий вопрос — что именно не понравилось.
        # Ответ (если выберут) идёт в db.ai.save_feedback_reason() и потом
        # подмешивается в промпты этого пользователя (см. build_user_context).
        try:
            await callback.message.edit_reply_markup(
                reply_markup=ai_feedback_reason_keyboard(message_id)
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise

    await callback.answer(
        "Спасибо за оценку! 🙏" if rating == "up" else "Жаль! Уточни, что не так — по желанию 👇"
    )


# =====================================
# ПРИЧИНА ДИЗЛАЙКА
# =====================================

_FEEDBACK_REASONS = {
    "long": "ответ был слишком длинным/затянутым",
    "off": "ответ был не по теме, что нужно",
    "unclear": "ответ был непонятно объяснён",
    "other": "другое",
}


@router.callback_query(F.data.startswith("ai_fbr_"))
async def ai_feedback_reason(callback: CallbackQuery):

    _, _, code, message_id = callback.data.split("_")
    message_id = int(message_id)

    reason = _FEEDBACK_REASONS.get(code)
    if reason:
        save_feedback_reason(message_id, callback.from_user.id, reason)

    # Возвращаем обычную клавиатуру — с кнопкой добавления привычки,
    # если она ещё актуальна для этого сообщения.
    try:
        await callback.message.edit_reply_markup(
            reply_markup=ai_feedback_keyboard(message_id, _suggested_habits.get(message_id))
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await callback.answer("Спасибо, учту 🙏" if reason else "Ок 👌")


# =====================================
# СОВЕТ ДНЯ
# =====================================

@router.callback_query(F.data == "ai_tip")
async def ai_daily_tip(callback: CallbackQuery):

    user_id = callback.from_user.id
    await callback.answer("💡 Готовлю совет...")

    cache_key = f"tip:{user_id}:{date.today()}"
    tip = cache_get(cache_key)

    if tip is None:
        style = get_ai_style(user_id)
        user_context = build_user_context(user_id)
        try:
            tip = await generate_daily_tip(user_context, style)
        except Exception as e:
            logger.exception(f"Не удалось сформировать совет дня для {user_id}")
            log_error("daily_tip", e, user_id)
            tip = "❌ Не получилось сформировать совет, попробуйте позже."

        if tip and "[ошибка агента" not in tip:
            cache_set(cache_key, tip)

    await callback.message.answer(
        f"💡 <b>Совет дня</b>\n\n{tip}",
        parse_mode="HTML",
        reply_markup=back_menu_keyboard()
    )


# =====================================
# ДОБАВИТЬ ПРЕДЛОЖЕННУЮ ПРИВЫЧКУ ОДНИМ ТАПОМ
# =====================================

@router.callback_query(F.data.startswith("ai_addhabit_"))
async def ai_add_suggested_habit(callback: CallbackQuery):

    message_id = int(callback.data.removeprefix("ai_addhabit_"))
    habit_title = _suggested_habits.pop(message_id, None)

    if not habit_title:
        await callback.answer(
            "⚠️ Это предложение уже неактуально, добавь привычку вручную.",
            show_alert=True
        )
        return

    add_habit(callback.from_user.id, habit_title)

    await callback.answer(f"✅ Добавлено: «{habit_title}»", show_alert=True)

    # Убираем кнопку добавления из-под уже обработанного сообщения, чтобы
    # нельзя было случайно добавить одну и ту же привычку дважды.
    try:
        await callback.message.edit_reply_markup(
            reply_markup=ai_feedback_keyboard(message_id, suggested_habit=None)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

