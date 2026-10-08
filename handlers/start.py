import html
import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config import ADMIN_IDS
from keyboards import main_menu

from db import (
    add_user,
    get_user,
    set_referrer,
    add_referral,
    add_xp,
    add_friendship,
    get_access_status,
)

from handlers.onboarding import begin_survey

router = Router()
logger = logging.getLogger(__name__)

# «?start=friend_<id>» — личная ссылка «Добавить друга» из Mini App.
FRIEND_LINK_PREFIX = "friend_"


def _display_name(user, fallback):
    if user is None:
        return fallback
    return user["first_name"] or user["username"] or fallback


async def _announce_friendship(message: Message, inviter_id: int):
    """Обе стороны узнают, что подружились: пригласившему — push (иначе он
    не заметит, что по его ссылке пришли), перешедшему — короткое
    подтверждение. Сбой доставки пригласившему не должен ломать /start."""
    me = message.from_user
    my_name = html.escape(me.first_name or me.username or "Друг")
    inviter_name = html.escape(_display_name(get_user(inviter_id), "друг"))
    try:
        await message.bot.send_message(
            inviter_id,
            f"🤝 <b>{my_name}</b> теперь в твоих друзьях в ADAM! "
            "Смотри, кто сегодня отметился, и подталкивай друг друга.",
            parse_mode="HTML",
        )
    except Exception:
        logger.info("Не удалось уведомить %s о новом друге", inviter_id, exc_info=True)
    await message.answer(
        f"🤝 Теперь вы с <b>{inviter_name}</b> друзья в ADAM — "
        "смотрите, кто сегодня отметился, и подталкивайте друг друга.",
        parse_mode="HTML",
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):

    # Регистрируем пользователя (для уже существующих — no-op благодаря
    # INSERT OR IGNORE внутри add_user)
    add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name
    )

    # Проверяем реферальную ссылку. Две формы: «?start=<id>» (пригласи
    # друга — бонус за нового игрока) и «?start=friend_<id>» (кнопка
    # «Добавить друга» в Mini App — дружба даже между уже зарегистрированными).
    args = message.text.split()
    referred_bonus_given = False
    inviter_id = None
    via_friend_link = False

    if len(args) > 1:
        payload = args[1]
        if payload.startswith(FRIEND_LINK_PREFIX):
            payload = payload[len(FRIEND_LINK_PREFIX):]
            via_friend_link = True
        try:
            inviter_id = int(payload)
        except ValueError:
            inviter_id = None

    if inviter_id is not None:
        referrer_id = inviter_id

        # Нельзя пригласить самого себя
        if referrer_id != message.from_user.id:

            user = get_user(message.from_user.id)

            # Бонус за приглашение — за НОВОГО игрока. Ссылка «Добавить
            # друга» шлётся и тем, кто уже пользуется ADAM: для них она
            # только создаёт дружбу, без XP-бонусов (иначе её можно
            # раздавать налево и направо ради очков).
            bonus_allowed = not via_friend_link or get_access_status(message.from_user.id) == "new"

            # Если пригласивший ещё не записан
            if user["referrer_id"] is None and bonus_allowed:

                set_referrer(
                    message.from_user.id,
                    referrer_id
                )

                # Засчитываем приглашение
                add_referral(referrer_id)

                # Начисляем бонус пригласившему
                add_xp(referrer_id, 100)

                # Бонус и приглашённому — раньше выгоду от рефералки
                # получала только одна сторона, что слабее мотивирует
                # переходить по ссылке (в отличие от "пригласи и оба
                # получите бонус").
                add_xp(message.from_user.id, 50)
                referred_bonus_given = True

            # Пришедший по приглашению становится другом пригласившего
            # (db/friends.py): так «Напомнить друзьям» есть кому слать.
            if via_friend_link or referred_bonus_given:
                if add_friendship(message.from_user.id, referrer_id):
                    await _announce_friendship(message, referrer_id)

    # Проверяем бан ДО отправки меню
    user = get_user(message.from_user.id)

    if user and user["banned"] == 1:
        await message.answer(
            "🚫 Ваш аккаунт заблокирован администрацией."
        )
        return

    # Админов анкета не касается — сразу в меню
    is_admin = message.from_user.id in ADMIN_IDS
    status = get_access_status(message.from_user.id)

    if not is_admin and status == "new":
        if referred_bonus_given:
            await message.answer("🎁 Бонус за переход по приглашению друга: +50 Adam Coin!")
        await begin_survey(message, state)
        return

    if not is_admin and status == "pending":
        await message.answer(
            "<b>◆ Заявка на проверке</b>\n\n"
            "Как только доступ к <b>Project ADAM</b> откроется — напишем сразу.",
            parse_mode="HTML"
        )
        return

    if referred_bonus_given:
        await message.answer("🎁 Бонус за переход по приглашению друга: +50 Adam Coin!")

    # Приветственное сообщение
    await message.answer(
        """
👋 Добро пожаловать!

Это Project ADAM.

Вместе мы будем:

- 🎯 Развивать привычки
- 🤖 Общаться с ИИ
- 📈 Отслеживать прогресс
- 👥 Искать единомышленников

Выберите раздел 👇
        """,
        reply_markup=main_menu(is_admin=message.from_user.id in ADMIN_IDS)
    )