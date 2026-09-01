import json
import time
import hashlib
import asyncio
import logging
from datetime import date, datetime, timezone

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
from aiogram.types import CallbackQuery, Message, BufferedInputFile
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from db import (
    add_ai_message,
    get_ai_message_text,
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
    has_premium, get_ai_quota, consume_ai_answer,
)

from keyboards import (
    ai_keyboard,
    back_menu_keyboard,
    ai_feedback_keyboard,
    ai_feedback_reason_keyboard,
    crisis_keyboard,
)

from multi_agent import solve_task_multiagent, generate_daily_tip, summarize_user_memory
from config import AI_TELEGRAM_MAX_INPUT_CHARS, AI_LONG_COST_CHARS, AI_VERY_LONG_COST_CHARS
from habit_intents import try_handle_habit_intent, try_handle_habit_intent_ai
from handlers.helpers import send_long_message, edit_or_split_message

router = Router()
logger = logging.getLogger("handlers.ai")

# Раз в столько сообщений пересобираем долгосрочный профиль пользователя
MEMORY_UPDATE_EVERY = 6


def _looks_like_habit_action(text: str) -> bool:
    """Дешёвый локальный фильтр перед AI-классификатором привычек.
    Обычные вопросы не должны делать дополнительный LLM-вызов."""
    t = (text or "").strip().lower()
    if len(t) > 700:
        return False
    markers = (
        "добавь привыч", "удали привыч", "убери привыч", "удалить привыч",
        "выполни привыч", "отметь привыч", "отметить привыч", "привычка готов",
        "я сделал", "я выполнил", "я выполнила", "я сделалa", "готово, я",
        "я пробежал", "я почитал", "я помедитировал",
    )
    return any(m in t for m in markers)


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
            elapsed = (datetime.now(timezone.utc).replace(tzinfo=None) - last_dt).total_seconds()
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
        from datetime import datetime, timedelta, timezone

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
            (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=24)).isoformat()
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
    text = (message.text or "").strip()

    # Roadmap #21 — голосовые сообщения AI-коучу: расшифровываем через
    # Whisper и дальше ведём ТОЧНО так же, как обычный текст (распознавание
    # команд про привычки, весь мультиагентный пайплайн и т.д.) — голос
    # это просто альтернативный способ ввести text, не отдельная ветка.
    if not text and message.voice:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        try:
            file = await message.bot.get_file(message.voice.file_id)
            buf = await message.bot.download_file(file.file_path)
            audio_bytes = buf.read()
        except Exception:
            audio_bytes = None
        if audio_bytes:
            from multi_agent import transcribe_voice
            text = (await transcribe_voice(audio_bytes)) or ""
        if not text:
            await message.answer("🎙️ Не получилось распознать голосовое — попробуй ещё раз или напиши текстом.")
            return
    elif not text:
        # Ни текста, ни голоса (стикер, фото и т.п. в этом состоянии чата) —
        # обрабатывать нечего.
        return

    if len(text) > AI_TELEGRAM_MAX_INPUT_CHARS:
        await message.answer(
            f"✂️ Сообщение слишком длинное. Сократи его до {AI_TELEGRAM_MAX_INPUT_CHARS} символов — я сохраню главное и отвечу точнее."
        )
        return

    first_message = claim_ai_first_message(user_id)

    await message.bot.send_chat_action(
        chat_id=message.chat.id,
        action="typing"
    )

    # ✅ Команды управления привычками («добавь привычку …», «удали привычку
    # …» и т.п.) выполняются напрямую, в обход мультиагентного пайплайна —
    # быстро и без риска, что модель что-то не так поймёт.
    habit_reply = try_handle_habit_intent(user_id, text)

    # ✅ Резервный путь: если явный шаблон не совпал (человек написал своими
    # словами, например «я сделал зарядку»), пробуем распознать команду
    # через лёгкий AI-классификатор — иначе сообщение ушло бы в обычный чат,
    # который мог бы РАЗГОВОРНО подтвердить действие, ничего не изменив в
    # базе (см. habit_intents.try_handle_habit_intent_ai).
    if habit_reply is None and _looks_like_habit_action(text):
        habit_reply = await try_handle_habit_intent_ai(user_id, text)

    if habit_reply is not None:
        add_ai_message(user_id, "user", text)
        add_ai_message(user_id, "assistant", habit_reply)
        await message.answer(habit_reply, reply_markup=ai_keyboard())
        return

    is_pro = has_premium(user_id)
    quota = get_ai_quota(user_id, is_pro)
    cost = 3 if len(text) > AI_VERY_LONG_COST_CHARS else 2 if len(text) > AI_LONG_COST_CHARS else 1
    if quota["remaining"] < cost:
        await message.answer(
            "💬 Для такого длинного запроса сейчас недостаточно доступного лимита ADAM. "
            "Сократи запрос или используй дополнительные ответы из магазина."
        )
        return

    thinking_msg = await message.answer("Формирую ответ")

    # ✅ ЛОКАЛЬНЫЙ импорт chat — только здесь, внутри функции
    from webapp.services.ai_service import chat

    try:
        result = await chat(
            user_id=user_id,
            message=text,
            first_message=first_message,
        )
        answer = result["answer"]
        is_crisis = result["is_crisis"]
        suggested_habit = result["suggested_habit"]
        cached_answer = bool(result.get("cached"))
    except Exception as e:
        logger.exception(f"Ошибка AI-пайплайна для {user_id}")
        log_error("ai_pipeline", e, user_id)
        answer = (
            "❌ Не получилось сформировать ответ. "
            "Попробуйте ещё раз через минуту."
        )
        is_crisis = False
        suggested_habit = None
        cached_answer = False

    # Списываем лимит только после успешного ответа. Длинный запрос
    # расходует 2–3 единицы вместо одной: пользователь получает полный
    # качественный ответ, а экономика проекта защищена.
    if not cached_answer and not consume_ai_answer(user_id, is_pro, cost=cost):
        await message.answer("💬 Лимит ADAM закончился до завершения запроса. Попробуй ещё раз позже.")
        return

    add_ai_message(user_id, "user", text)
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
# Roadmap #47 — озвучка ответа AI (TTS)
# =====================================

@router.callback_query(F.data.startswith("ai_voice_"))
async def ai_voice(callback: CallbackQuery):
    message_id = int(callback.data.split("_")[-1])
    text = get_ai_message_text(message_id, callback.from_user.id)
    if not text:
        await callback.answer("Не нашёл этот ответ — возможно, история уже очищена", show_alert=True)
        return
    await callback.answer("🔊 Озвучиваю...")
    # send_audio (не send_voice) — send_voice в Bot API ожидает конкретно
    # OGG/OPUS для отрисовки как "голосовое сообщение" с волной, обычный
    # mp3 от OpenAI TTS туда лучше не пытаться протащить; send_audio даёт
    # такой же результат для пользователя (проигрывается тут же в чате),
    # просто как обычный аудио-файл, а не "голосовая" бабблом.
    await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action="upload_voice")
    from multi_agent import generate_speech
    audio_bytes = await generate_speech(text)
    if not audio_bytes:
        await callback.message.answer("❌ Не получилось озвучить — попробуй чуть позже.")
        return
    await callback.message.answer_audio(
        BufferedInputFile(audio_bytes, filename="adam_voice.mp3"),
        title="Ответ ADAM",
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
    try:
        add_habit(callback.from_user.id, habit_title)
    except ValueError as exc:
        if str(exc) == "habit_limit":
            await callback.answer("⚠️ Можно добавить не больше 7 привычек.", show_alert=True)
        elif str(exc) == "habit_add_locked":
            await callback.answer(
                "⚠️ Сегодня уже была отметка и удаление привычки — "
                "добавление новых открыто с 00:00.",
                show_alert=True,
            )
        else:
            raise
        return
    await callback.answer(f"✅ Добавлено: «{habit_title}»", show_alert=True)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=ai_feedback_keyboard(message_id, suggested_habit=None)
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise