import json
import time
import hashlib
import asyncio
import logging
from datetime import date, datetime

# ✅ Импортируем общие утилиты из ai_utils (не из ai_service)
from webapp.services.ai_utils import (
    build_history_text,
    build_user_context,
    build_proactive_context,
    _cache_key,
)

# ❌ Удалён глобальный импорт chat из ai_service
# from webapp.services.ai_service import chat

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
    claim_ai_first_message,
)

from keyboards import (
    ai_keyboard,
    back_menu_keyboard,
    ai_feedback_keyboard,
    ai_feedback_reason_keyboard,
    crisis_keyboard,
)

from multi_agent import solve_task_multiagent, generate_daily_tip, summarize_user_memory
from habit_intents import try_handle_habit_intent, try_handle_habit_intent_ai
from handlers.helpers import send_long_message, edit_or_split_message

router = Router()
logger = logging.getLogger("handlers.ai")

# Раз в столько сообщений пересобираем долгосрочный профиль пользователя
MEMORY_UPDATE_EVERY = 6


# =====================================
# СОСТОЯНИЯ
# =====================================

class AiState(StatesGroup):
    chatting = State()


# =====================================
# ТРОТТЛИНГ
# =====================================

MIN_INTERVAL_SECONDS = 3.0


def _is_throttled(user_id: int) -> float | None:
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

_suggested_habits: dict[int, str] = {}


# =====================================
# ФОНОВОЕ ОБНОВЛЕНИЕ ДОЛГОСРОЧНОЙ ПАМЯТИ
# =====================================

async def _update_memory(user_id: int):
    try:
        from datetime import datetime, timedelta

        profile = get_user_profile(user_id)
        existing_summary = profile["summary"] if profile else ""

        # Совместимость со старой версией: раньше результат summarize_user_memory
        # ошибочно записывался целиком как строковое представление dict.
        if isinstance(existing_summary, str) and existing_summary.startswith("{") and "summary" in existing_summary:
            try:
                legacy = json.loads(existing_summary)
                existing_summary = str(legacy.get("summary") or "")
            except Exception:
                pass

        recent_history = build_history_text(user_id, limit=MEMORY_UPDATE_EVERY + 2)
        memory = await summarize_user_memory(existing_summary, recent_history)

        if isinstance(memory, dict):
            summary = str(memory.get("summary") or existing_summary).strip()
            followup = str(memory.get("followup") or "").strip()
        else:
            summary = str(memory or existing_summary).strip()
            followup = ""

        # followup — только одна тема и максимум на следующие 24 часа.
        proactive_until = (
            (datetime.utcnow() + timedelta(hours=24)).isoformat()
            if followup else None
        )
        update_user_profile(
            user_id,
            summary,
            proactive_topic=followup,
            proactive_until=proactive_until,
        )
    except Exception as e:
        logger.exception(f"Не удалось обновить профиль памяти для {user_id}")
        log_error("memory_update", e, user_id)


def _schedule_memory_update(user_id: int):
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

    user_id = message.from_user.id
    first_message = claim_ai_first_message(user_id)

    await message.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    # ✅ Команды управления привычками («добавь привычку …», «удали привычку
    # …» и т.п.) выполняются напрямую, в обход мультиагентного пайплайна —
    # быстро и без риска, что модель что-то не так поймёт.
    habit_reply = try_handle_habit_intent(user_id, message.text)

    # ✅ Резервный путь: если явный шаблон не совпал (человек написал своими
    # словами, например «я сделал зарядку»), пробуем распознать команду
    # через лёгкий AI-классификатор — иначе сообщение ушло бы в обычный чат,
    # который мог бы РАЗГОВОРНО подтвердить действие, ничего не изменив в
    # базе (см. habit_intents.try_handle_habit_intent_ai).
    if habit_reply is None:
        habit_reply = await try_handle_habit_intent_ai(user_id, message.text)

    if habit_reply is not None:
        add_ai_message(user_id, "user", message.text)
        add_ai_message(user_id, "assistant", habit_reply)
        await message.answer(habit_reply, reply_markup=ai_keyboard())
        return

    thinking_msg = await message.answer("🤔 Думаю над ответом...")

    # ✅ ЛОКАЛЬНЫЙ импорт chat — только здесь, внутри функции
    from webapp.services.ai_service import chat

    try:
        result = await chat(
            user_id=user_id,
            message=message.text,
        )
        answer = result["answer"]
        is_crisis = result["is_crisis"]
        suggested_habit = result["suggested_habit"]
    except Exception as e:
        logger.exception(f"Ошибка AI-пайплайна для {user_id}")
        log_error("ai_pipeline", e, user_id)
        answer = (
            "❌ Не получилось сформировать ответ. "
            "Попробуйте ещё раз через минуту."
        )
        is_crisis = False
        suggested_habit = None

    add_ai_message(user_id, "user", message.text)
    assistant_message_id = add_ai_message(user_id, "assistant", answer)

    if not is_crisis:
        _schedule_memory_update(user_id)

    if is_crisis:
        keyboard = crisis_keyboard()
    else:
        if suggested_habit:
            _suggested_habits[assistant_message_id] = suggested_habit
        keyboard = ai_feedback_keyboard(assistant_message_id, suggested_habit)

    try:
        # Проблема №1/№3: если ответ всё-таки длиннее лимита Telegram
        # (обычный случай — влезает и редактируется одним сообщением, как
        # раньше), edit_or_split_message редактирует thinking_msg первой
        # частью и, если нужно, досылает остальные части отдельными
        # сообщениями, не обрывая ни слова, ни предложения.
        await edit_or_split_message(thinking_msg, message, answer, reply_markup=keyboard)
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
    # Совет дня НЕ кэшируем: при повторном нажатии ADAM заново читает
    # актуальные задачи и привычки. Поэтому вечером он уже не опирается на
    # утренний снимок прогресса.
    style = get_ai_style(user_id)
    user_context = build_proactive_context(user_id)
    try:
        tip = await generate_daily_tip(user_context, style)
    except Exception as e:
        logger.exception(f"Не удалось сформировать совет дня для {user_id}")
        log_error("daily_tip", e, user_id)
        tip = "❌ Не получилось сформировать совет, попробуйте позже."
    await send_long_message(
        callback.message,
        tip,
        parse_mode="HTML",
        reply_markup=back_menu_keyboard(),
        header="💡 <b>Совет дня</b>",
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
    try:
        await callback.message.edit_reply_markup(
            reply_markup=ai_feedback_keyboard(message_id, suggested_habit=None)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise