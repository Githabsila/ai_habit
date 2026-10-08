import asyncio
import html
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from keyboards import main_menu
from config import ADMIN_IDS, WEBAPP_URL

from db import (
    save_survey_answers,
    save_survey_analysis,
    set_access_status,
    save_milestones,
    log_error,
    get_user,
    get_survey,
    add_habit,
    survey_variant,
)

from multi_agent import analyze_onboarding_survey, suggest_first_step
from alerts import notify_admins

router = Router()
logger = logging.getLogger("handlers.onboarding")


# =====================================
# СОСТОЯНИЯ АНКЕТЫ
# =====================================

class Onboarding(StatesGroup):
    business = State()   # 1/3 — чем занимается
    focus = State()      # 2/3 — что прокачать в первую очередь
    goal = State()       # 3/3 — главная цель одной фразой


# =====================================
# ТЕКСТЫ И КНОПКИ
# =====================================
# Анкета — это первое, что человек видит в боте, поэтому она короткая и «дорогая»:
# три вопроса вместо четырёх, на каждый можно ответить одним нажатием (или написать
# своё), одно сообщение «перелистывается» на месте вместо ленты из вопросов, а ответ
# приходит сразу — ИИ-разбор анкеты считается уже после ответа, в фоне.
#
# Куда пишутся ответы (user_survey, на них опираются ИИ-разбор цели, первая привычка,
# карточка в админке): business ← «чем занимается», life_goal ← «что прокачать»,
# bot_goal ← «главная цель». Колонка hobbies больше не заполняется (пустая строка).

SURVEY_ROLES = (
    ("💼", "Работаю по найму"),
    ("🚀", "Свой бизнес"),
    ("🎓", "Учусь"),
    ("🌱", "Ищу своё дело"),
)

SURVEY_FOCUS = (
    ("⚡", "Дисциплина"),
    ("🧠", "Фокус и продуктивность"),
    ("💪", "Здоровье и спорт"),
    ("📚", "Знания и навыки"),
    ("🌙", "Режим и сон"),
    ("💰", "Карьера и деньги"),
)

# Подсказки цели под выбранную сферу — по нажатию цель сразу записана.
SURVEY_GOAL_IDEAS = (
    ("Делать главное без откладывания", "Держать слово, которое даю себе", "Выстроить железный распорядок дня"),
    ("Работать 2 часа без отвлечений", "Закрывать главную задачу до обеда", "Меньше соцсетей — больше дела"),
    ("Тренироваться 3 раза в неделю", "Ходить 8000 шагов каждый день", "Привести себя в форму"),
    ("Читать 20 минут каждый день", "Выучить иностранный язык", "Освоить новую профессию"),
    ("Ложиться и вставать в одно время", "Высыпаться — 7–8 часов", "Утренний ритуал на 15 минут"),
    ("Вырасти в должности", "Запустить свой проект", "Регулярно откладывать деньги"),
)
SURVEY_GOAL_IDEAS_DEFAULT = (
    "Выстроить дисциплину",
    "Держать режим дня",
    "Заняться собой",
)

# A/B-тест вступления (db.survey_variant — чистая функция от telegram_id, без
# миграций и хранения): A — «закрытый клуб» на «вы», B — тёплое «ты». Конверсию по
# вариантам видно в /admin → Статистика (db.analytics.get_survey_funnel_by_variant).
# Остальные вопросы без местоимений — одни на оба варианта.
SURVEY_INTRO_VARIANTS = {
    "A": (
        "<b>◆ PROJECT ADAM</b>\n"
        "<i>Закрытый клуб привычек с личным ИИ-наставником.</i>\n\n"
        "Доступ — по короткой заявке: три вопроса, полминуты.\n\n"
        "<b>●○○</b>  Чем вы занимаетесь?"
    ),
    "B": (
        "<b>◆ Привет, я ADAM</b>\n"
        "<i>Твой личный ИИ-наставник по привычкам.</i>\n\n"
        "Три быстрых вопроса — и советы будут про тебя, а не общие фразы. Полминуты.\n\n"
        "<b>●○○</b>  Чем ты занимаешься?"
    ),
}

SURVEY_FOCUS_TEXT = "<b>●●○</b>  Что прокачиваем в первую очередь?"
SURVEY_GOAL_TEXT = (
    "<b>●●●</b>  Главная цель — одной фразой.\n"
    "<i>Вариант ниже или своими словами сообщением.</i>"
)
SURVEY_TEXT_NUDGE = "Нажмите на вариант или напишите ответ текстом 🙂"
SURVEY_STALE_TOAST = "Эта заявка уже закрыта. Отправьте /start, чтобы начать заново."

SURVEY_PENDING_TEXT = (
    "<b>◆ Заявка принята</b>\n\n"
    "Модератор смотрит заявки вручную. Как только доступ откроется — напишем сюда."
)
SURVEY_DEMO_NOTE = "\n\n<i>Демо: ответы не сохранены, доступ не менялся.</i>"
SURVEY_PREMIUM_TEXT ="<b>◆ Заявка принята</b>\n\nУ вас Premium 💎 — доступ открываем сразу."

ANSWER_MAX_LEN = 300

# Фоновые задачи (ИИ-разбор анкеты): ссылка нужна, чтобы задачу не собрал сборщик мусора.
_background_tasks = set()


def _spawn(coro):
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _clean(text):
    return " ".join(str(text or "").split())[:ANSWER_MAX_LEN]


def _chips_keyboard(kind, items, per_row=2):
    buttons = [
        InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"survey:{kind}:{i}")
        for i, (icon, label) in enumerate(items)
    ]
    rows = [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _goal_keyboard(ideas):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=idea, callback_data=f"survey:goal:{i}")] for i, idea in enumerate(ideas)
    ])


def _goal_ideas(focus_index):
    if focus_index is None or not 0 <= focus_index < len(SURVEY_GOAL_IDEAS):
        return SURVEY_GOAL_IDEAS_DEFAULT
    return SURVEY_GOAL_IDEAS[focus_index]


def _tap_index(data, size):
    """«survey:role:2» → 2; всё, что не похоже на номер варианта, → None."""
    try:
        index = int(str(data).rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < size else None


async def _say(message, text, markup=None, edit=False):
    """Следующий шаг: по нажатию кнопки карточка меняется на месте, по тексту — новое сообщение."""
    if edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            logger.info("Не удалось отредактировать карточку анкеты — отправляю новым сообщением", exc_info=True)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


# =====================================
# СТАРТ АНКЕТЫ
# =====================================
# Вызывается из handlers/start.py для новых пользователей (access_status == 'new').
# Отдельная функция, а не хендлер на команду — анкета всегда начинается только из /start,
# когда мы уже знаем, что пользователь новый.

async def begin_survey(message: Message, state: FSMContext, variant=None, demo=False):
    await state.clear()
    await state.set_state(Onboarding.business)
    if demo:
        await state.update_data(demo=True)
    variant = variant if variant in SURVEY_INTRO_VARIANTS else survey_variant(message.from_user.id)
    await message.answer(
        SURVEY_INTRO_VARIANTS[variant],
        parse_mode="HTML",
        reply_markup=_chips_keyboard("role", SURVEY_ROLES),
    )


@router.message(Command("survey_preview"))
async def survey_preview(message: Message, state: FSMContext):
    """Админам: пройти анкету «как новичок» — ничего не сохраняется, доступ не меняется.
    /survey_preview B — показать второй вариант вступления (по умолчанию — по чётности id)."""
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = (message.text or "").split()
    variant = parts[1].upper() if len(parts) > 1 else None
    await begin_survey(message, state, variant=variant, demo=True)


# =====================================
# ШАГИ АНКЕТЫ
# =====================================

async def _ask_focus(message, state, edit):
    await state.set_state(Onboarding.focus)
    await _say(message, SURVEY_FOCUS_TEXT, _chips_keyboard("focus", SURVEY_FOCUS), edit)


async def _ask_goal(message, state, focus_index, edit):
    await state.update_data(focus_index=focus_index)
    await state.set_state(Onboarding.goal)
    await _say(message, SURVEY_GOAL_TEXT, _goal_keyboard(_goal_ideas(focus_index)), edit)


@router.callback_query(Onboarding.business, F.data.startswith("survey:role:"))
async def survey_role_tap(callback: CallbackQuery, state: FSMContext):
    index = _tap_index(callback.data, len(SURVEY_ROLES))
    if index is None:
        await callback.answer()
        return
    await state.update_data(business=SURVEY_ROLES[index][1])
    await _ask_focus(callback.message, state, edit=True)
    await callback.answer()


@router.message(Onboarding.business)
async def survey_role_text(message: Message, state: FSMContext):
    text = _clean(message.text)
    if not text:
        await message.answer(SURVEY_TEXT_NUDGE)
        return
    await state.update_data(business=text)
    await _ask_focus(message, state, edit=False)


@router.callback_query(Onboarding.focus, F.data.startswith("survey:focus:"))
async def survey_focus_tap(callback: CallbackQuery, state: FSMContext):
    index = _tap_index(callback.data, len(SURVEY_FOCUS))
    if index is None:
        await callback.answer()
        return
    await state.update_data(life_goal=SURVEY_FOCUS[index][1])
    await _ask_goal(callback.message, state, index, edit=True)
    await callback.answer()


@router.message(Onboarding.focus)
async def survey_focus_text(message: Message, state: FSMContext):
    text = _clean(message.text)
    if not text:
        await message.answer(SURVEY_TEXT_NUDGE)
        return
    await state.update_data(life_goal=text)
    await _ask_goal(message, state, None, edit=False)


@router.callback_query(Onboarding.goal, F.data.startswith("survey:goal:"))
async def survey_goal_tap(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ideas = _goal_ideas(data.get("focus_index"))
    index = _tap_index(callback.data, len(ideas))
    if index is None:
        await callback.answer()
        return
    await callback.answer()
    await _finish(callback.bot, callback.from_user, state, ideas[index], callback.message, edit=True)


@router.message(Onboarding.goal)
async def survey_goal_text(message: Message, state: FSMContext):
    text = _clean(message.text)
    if not text:
        await message.answer(SURVEY_TEXT_NUDGE)
        return
    await _finish(message.bot, message.from_user, state, text, message, edit=False)


@router.callback_query(F.data.startswith("survey:"))
async def survey_stale_tap(callback: CallbackQuery):
    """Нажатие на кнопку старой карточки (анкета уже отправлена, бот перезапускался и т.п.)."""
    await callback.answer(SURVEY_STALE_TOAST)


# =====================================
# ЗАВЕРШЕНИЕ АНКЕТЫ
# =====================================

async def _analyze_in_background(user_id, business, life_goal, bot_goal):
    """ИИ-разбор нужен модератору и недельной сверке цели, но не самому доступу: считаем его уже
    после того, как человек получил ответ (раньше анкета висела на «Обрабатываю…» несколько секунд)."""
    try:
        analysis = await analyze_onboarding_survey(business, "", life_goal, bot_goal)
        save_survey_analysis(user_id, analysis["summary"], analysis["tags"])
    except Exception as e:
        logger.exception(f"Не удалось проанализировать анкету для {user_id}")
        log_error("survey_analysis", e, user_id)


async def _notify_admins_about_application(bot, user):
    user_id = user.id
    username = f"@{user.username}" if user.username else user.full_name
    try:
        # Уведомление приходит сразу и содержит inline-кнопку одобрения — админу не нужно
        # отдельно открывать админ-панель.
        approval_markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Быстро одобрить", callback_data=f"admin_approve_{user_id}"),
            InlineKeyboardButton(text="👀 Открыть заявки", callback_data="admin_pending"),
        ]])
        survey = get_survey(user_id)

        def _short(key, limit=220):
            # get_survey отдаёт sqlite3.Row — у него нет .get(): раньше именно здесь падало
            # уведомление, и админ не получал заявку (ошибку глушил внешний except).
            value = survey[key] if survey is not None else None
            value = str(value or "—").strip().replace("\n", " ")
            return html.escape(value if len(value) <= limit else value[:limit - 1] + "…")

        admin_text = (
            f"🆕 <b>Новая анкета</b>\n\n"
            f"👤 {html.escape(str(username))}\n"
            f"🆔 <code>{user_id}</code>\n\n"
            f"💼 {_short('business')}\n"
            f"🎯 {_short('life_goal')}\n"
            f"🤖 {_short('bot_goal')}\n\n"
            "Можно одобрить прямо здесь одной кнопкой."
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id, text=admin_text, parse_mode="HTML", reply_markup=approval_markup
                )
            except Exception as e:
                logger.exception("Не удалось уведомить админа %s о заявке %s", admin_id, user_id)
                log_error("notify_admin_pending_inline", e, user_id)
    except Exception as e:
        logger.exception(f"Не удалось уведомить админов о новой заявке {user_id}")
        log_error("notify_admins_pending", e, user_id)


async def _finish(bot, user, state, bot_goal, message, edit):
    data = await state.get_data()
    if data.get("demo"):
        await state.clear()
        await _say(message, SURVEY_PENDING_TEXT + SURVEY_DEMO_NOTE, None, edit)
        return
    business = data.get("business", "")
    life_goal = data.get("life_goal", "")
    user_id = user.id

    save_survey_answers(user_id, business, "", life_goal, bot_goal)
    await state.clear()
    _spawn(_analyze_in_background(user_id, business, life_goal, bot_goal))

    # Premium-пользователи (выданы админом заранее) получают доступ сразу, минуя очередь
    # модерации — это одна из premium-плюшек.
    db_user = get_user(user_id)
    if db_user and db_user["premium"] == 1:
        await _say(message, SURVEY_PREMIUM_TEXT, None, edit)
        await grant_access(bot, user_id, bot_goal=bot_goal)
        return

    set_access_status(user_id, "pending")

    # ВАЖНО: уведомление админу уходит сразу после перевода заявки в pending — без ожидания
    # scheduler / открытия админки (раньше здесь вызывалась несуществующая функция, и админ
    # вообще не узнавал о заявке).
    await _notify_admins_about_application(bot, user)
    await _say(message, SURVEY_PENDING_TEXT, None, edit)


# =====================================
# ВЫДАЧА ДОСТУПА (одобрение)
# =====================================
# Общая точка входа — используется и при ручном одобрении админом
# (handlers/admin.py), и при автоодобрении по таймеру (onboarding_auto.py),
# и при мгновенном доступе для Premium выше. Помимо самого доступа сразу
# подбирает первую привычку и вехи под цель из анкеты (bot_goal), чтобы
# человек не оставался один на один с пустым меню.

APPROVED_INTRO = (
    "<b>◆ Доступ открыт</b>\n\n"
    "Добро пожаловать в <b>Project ADAM</b>. Всё готово — выбирайте раздел 👇"
)


async def grant_access(bot, user_id: int, bot_goal: str = None):
    set_access_status(user_id, "approved")

    extra_text = ""
    if bot_goal:
        try:
            step = await suggest_first_step(bot_goal)
            add_habit(user_id, step["habit"])
            save_milestones(user_id, bot_goal, step["milestones"])
            milestones_lines = "\n".join(f"▫️ {html.escape(str(m))}" for m in step["milestones"])
            extra_text = (
                f"\n\n<b>Первый шаг уже подготовлен</b>\n"
                f"✅ {html.escape(str(step['habit']))}\n\n"
                f"Вехи на пути к цели:\n{milestones_lines}\n\n"
                f"Всё можно поменять в любой момент."
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
