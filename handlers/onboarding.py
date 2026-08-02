import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from keyboards import main_menu

from db import (
    save_survey_answers,
    save_survey_analysis,
    set_access_status,
    log_error,
)

from multi_agent import analyze_onboarding_survey

router = Router()
logger = logging.getLogger("handlers.onboarding")


# =====================================
# СОСТОЯНИЯ АНКЕТЫ
# =====================================

class Onboarding(StatesGroup):
    business = State()
    hobbies = State()
    life_goal = State()
    bot_goal = State()


# =====================================
# СТАРТ АНКЕТЫ
# =====================================
# Вызывается из handlers/start.py для новых пользователей (access_status
# == 'new'). Отдельная функция, а не хендлер на команду — анкета всегда
# начинается только из /start, когда мы уже знаем, что пользователь новый.

async def begin_survey(message: Message, state: FSMContext):
    await state.set_state(Onboarding.business)
    await message.answer(
        "🔒 <b>Project ADAM</b> — закрытый проект. Доступ открывается не всем "
        "и не сразу — сначала короткая анкета, дальше её смотрит модератор.\n\n"
        "Отвечайте свободным текстом, в одном сообщении на каждый вопрос.\n\n"
        "<b>1/4.</b> Чем вы занимаетесь — работа, бизнес, дело?",
        parse_mode="HTML"
    )


# =====================================
# ШАГИ АНКЕТЫ
# =====================================

@router.message(Onboarding.business)
async def survey_business(message: Message, state: FSMContext):
    await state.update_data(business=message.text or "")
    await state.set_state(Onboarding.hobbies)
    await message.answer("<b>2/4.</b> Чем увлекаетесь в свободное время?", parse_mode="HTML")


@router.message(Onboarding.hobbies)
async def survey_hobbies(message: Message, state: FSMContext):
    await state.update_data(hobbies=message.text or "")
    await state.set_state(Onboarding.life_goal)
    await message.answer(
        "<b>3/4.</b> Какую главную цель хотите достичь в жизни в ближайшее время?",
        parse_mode="HTML"
    )


@router.message(Onboarding.life_goal)
async def survey_life_goal(message: Message, state: FSMContext):
    await state.update_data(life_goal=message.text or "")
    await state.set_state(Onboarding.bot_goal)
    await message.answer(
        "<b>4/4.</b> А чего хотите добиться конкретно здесь, вместе с "
        "ИИ-наставником в боте?",
        parse_mode="HTML"
    )


@router.message(Onboarding.bot_goal)
async def survey_bot_goal(message: Message, state: FSMContext):
    data = await state.get_data()
    business = data.get("business", "")
    hobbies = data.get("hobbies", "")
    life_goal = data.get("life_goal", "")
    bot_goal = message.text or ""

    user_id = message.from_user.id

    save_survey_answers(user_id, business, hobbies, life_goal, bot_goal)

    await message.answer("⏳ Обрабатываю анкету...")

    try:
        analysis = await analyze_onboarding_survey(business, hobbies, life_goal, bot_goal)
        save_survey_analysis(user_id, analysis["summary"], analysis["tags"])
    except Exception as e:
        logger.exception(f"Не удалось проанализировать анкету для {user_id}")
        log_error("survey_analysis", e, user_id)
        # Анализ — не критичен для самого доступа, анкета всё равно уходит
        # на модерацию/автоапрув даже если AI-разбор не удался.

    set_access_status(user_id, "pending")
    await state.clear()

    await message.answer(
        "✅ Анкета получена.\n\n"
        "🕓 Идёт проверка модератором — скоро вы получите открытый "
        "эксклюзивный доступ к <b>Project ADAM</b>.\n\n"
        "Мы напишем сразу, как только доступ откроется.",
        parse_mode="HTML"
    )


# =====================================
# УВЕДОМЛЕНИЕ ОБ ОДОБРЕНИИ
# =====================================
# Общий текст — используется и при ручном одобрении админом (handlers/admin.py),
# и при автоодобрении по таймеру (onboarding_auto.py).

APPROVED_TEXT = (
    "🎉 Доступ открыт!\n\n"
    "Добро пожаловать в <b>Project ADAM</b>. Теперь доступны все разделы бота 👇"
)


async def notify_approved(bot, user_id: int):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=APPROVED_TEXT,
            parse_mode="HTML",
            reply_markup=main_menu()
        )
    except Exception:
        logger.warning(f"Не удалось уведомить пользователя {user_id} об одобрении доступа")
