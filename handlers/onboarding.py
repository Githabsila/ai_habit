import logging

from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from keyboards import main_menu
from config import ADMIN_IDS

from db import (
    save_survey_answers,
    save_survey_analysis,
    set_access_status,
    save_milestones,
    log_error,
    get_user,
    add_habit,
)

from multi_agent import analyze_onboarding_survey, suggest_first_step
from alerts import notify_admins

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

    await state.clear()

    # Premium-пользователи (выданы админом заранее) получают доступ сразу,
    # минуя очередь модерации — это одна из premium-плюшек.
    user = get_user(user_id)
    if user and user["premium"] == 1:
        await message.answer(
            "✅ Анкета получена. У вас Premium — доступ открывается сразу, без очереди 💎",
            parse_mode="HTML"
        )
        await grant_access(message.bot, user_id, bot_goal=bot_goal)
        return

    set_access_status(user_id, "pending")

    # ВАЖНО: уведомление админу отправляется сразу после перевода заявки
    # в pending — без ожидания scheduler / открытия админки. Раньше здесь
    # вызывалась несуществующая notify_admins_new_application() — это
    # роняло весь хендлер с NameError на каждой обычной (не Premium)
    # анкете, поэтому пользователь никогда не видел подтверждение
    # "Анкета получена", а админ вообще не узнавал о новой заявке.
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    try:
        await notify_admins(
            message.bot,
            f"Новая заявка на доступ: {username} (id {user_id}).\nПосмотреть — «🕓 Заявки на доступ» в /admin.",
        )
    except Exception as e:
        logger.exception(f"Не удалось уведомить админов о новой заявке {user_id}")
        log_error("notify_admins_pending", e, user_id)

    await message.answer(
        "✅ Анкета получена.\n\n"
        "🕓 Идёт проверка модератором — скоро вы получите открытый "
        "эксклюзивный доступ к <b>Project ADAM</b>.\n\n"
        "Мы напишем сразу, как только доступ откроется.",
        parse_mode="HTML"
    )


# =====================================
# ВЫДАЧА ДОСТУПА (одобрение)
# =====================================
# Общая точка входа — используется и при ручном одобрении админом
# (handlers/admin.py), и при автоодобрении по таймеру (onboarding_auto.py),
# и при мгновенном доступе для Premium выше. Помимо самого доступа сразу
# подбирает первую привычку и вехи под цель из анкеты (bot_goal), чтобы
# человек не оставался один на один с пустым меню.

APPROVED_INTRO = (
    "🎉 Доступ открыт!\n\n"
    "Добро пожаловать в <b>Project ADAM</b>. Теперь доступны все разделы бота 👇"
)


async def grant_access(bot, user_id: int, bot_goal: str = None):
    set_access_status(user_id, "approved")

    extra_text = ""
    if bot_goal:
        try:
            step = await suggest_first_step(bot_goal)
            add_habit(user_id, step["habit"])
            save_milestones(user_id, bot_goal, step["milestones"])
            milestones_lines = "\n".join(f"▫️ {m}" for m in step["milestones"])
            extra_text = (
                f"\n\nЧтобы не начинать с пустого места, уже добавил первую привычку:\n"
                f"✅ <b>{step['habit']}</b>\n\n"
                f"И наметил вехи на пути к цели:\n{milestones_lines}\n\n"
                f"Привычку и вехи всегда можно поменять в разделах бота."
            )
        except Exception as e:
            logger.warning(f"Не удалось подобрать первый шаг для {user_id}: {e}")
            log_error("grant_access_first_step", e, user_id)

    try:
        await bot.send_message(
            chat_id=user_id,
            text=APPROVED_INTRO + extra_text,
            parse_mode="HTML",
            reply_markup=main_menu(is_admin=user_id in ADMIN_IDS)
        )
    except Exception:
        logger.warning(f"Не удалось уведомить пользователя {user_id} об одобрении доступа")


async def notify_approved(bot, user_id: int):
    """Обёртка для мест, где под рукой нет bot_goal (например автоодобрение
    по таймеру) — берёт цель из уже сохранённой анкеты, если она есть."""
    from db import get_survey
    survey = get_survey(user_id)
    bot_goal = survey["bot_goal"] if survey else None
    await grant_access(bot, user_id, bot_goal=bot_goal)
