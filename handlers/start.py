import html
import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config import ADMIN_IDS
from keyboards import welcome_menu

from db import (
    add_user,
    get_user,
    add_friendship,
    get_access_status,
)
from db.referrals import REWARD_COINS, register_invite, mark_notified

from handlers.onboarding import begin_survey
from db.traffic import attribute as attribute_source, parse_payload

router = Router()
logger = logging.getLogger(__name__)

# «?start=friend_<id>» — личная ссылка «Добавить друга» из Mini App.
FRIEND_LINK_PREFIX = "friend_"


def _display_name(user, fallback):
    if user is None:
        return fallback
    return user["first_name"] or user["username"] or fallback


def _reward_line(invite, my_name):
    """Строка о награде для пригласившего: выплачено / ждёт первой отметки / ничего (слоты кончились, пауза)."""
    if not invite or not invite.get("counted"):
        return ""
    if invite["status"] == "paid":
        return (f"\n\n🎁 +{invite['inviter_coins']} Адам коинов за приглашение "
                f"({invite['slot']} из {invite['limit']}).")
    if invite["status"] == "pending":
        return f"\n\n🎁 +{REWARD_COINS} Адам коинов за приглашение придут, когда <b>{my_name}</b> отметит первую привычку."
    return ""


async def _announce_friendship(message: Message, inviter_id: int, invite=None, friends=True):
    """Обе стороны узнают, что подружились: пригласившему — push (иначе он
    не заметит, что по его ссылке пришли) со строкой о награде, перешедшему —
    короткое подтверждение. Сбой доставки пригласившему не должен ломать /start."""
    me = message.from_user
    my_name = html.escape(me.first_name or me.username or "Друг")
    inviter_name = html.escape(_display_name(get_user(inviter_id), "друг"))
    head = (f"🤝 <b>{my_name}</b> теперь в твоих друзьях в ADAM! Смотри, кто сегодня отметился, и подталкивай друг друга."
            if friends else f"👋 По твоей ссылке пришёл новый игрок: <b>{my_name}</b>.")
    try:
        await message.bot.send_message(inviter_id, head + _reward_line(invite, my_name), parse_mode="HTML")
    except Exception:
        logger.info("Не удалось уведомить %s о новом друге", inviter_id, exc_info=True)
    if invite and invite.get("invite_id") and invite.get("status") == "paid":
        mark_notified(invite["invite_id"])
    if friends:
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
    # друга) и «?start=friend_<id>» (кнопка «Добавить друга» в Mini App).
    # Обе сводят в друзья; награда за приглашение — по правилам
    # db/referrals.py (только за НОВОГО игрока и только за первых двоих).
    args = message.text.split()
    invite = None
    inviter_id = None
    via_friend_link = False

    if len(args) > 1:
        payload = args[1]
        # Ссылка канала/блогера t.me/<бот>?start=src_<код> (db/traffic.py): запоминаем, откуда пришёл человек.
        payload_kind, payload_value = parse_payload(payload)
        if payload_kind == "src":
            attribute_source(message.from_user.id, payload_value, via="start")
            payload = ""
        if payload.startswith(FRIEND_LINK_PREFIX):
            payload = payload[len(FRIEND_LINK_PREFIX):]
            via_friend_link = True
        try:
            inviter_id = int(payload)
        except ValueError:
            inviter_id = None

    # Нельзя пригласить самого себя
    if inviter_id is not None and inviter_id != message.from_user.id:
        via = "friend_link" if via_friend_link else "invite"

        # Приглашение засчитывается только новому аккаунту и только если
        # у пригласившего остались «слоты» награды (db/referrals.py): ссылка
        # раздаётся и тем, кто уже пользуется ADAM, — для них только дружба.
        invite = register_invite(inviter_id, message.from_user.id, via=via)
        if invite["counted"]:
            attribute_source(message.from_user.id, "ref", via=via)

        # Пришедший по ссылке становится другом пригласившего
        # (db/friends.py): так «Напомнить друзьям» есть кому слать.
        # Дружба от награды не зависит.
        became_friends = add_friendship(message.from_user.id, inviter_id)
        if became_friends or (invite["counted"] and invite["status"] in ("paid", "pending")):
            await _announce_friendship(message, inviter_id, invite, friends=became_friends)

    referred_bonus_given = bool(invite and invite.get("invitee_coins"))

    # Проверяем бан ДО отправки меню
    user = get_user(message.from_user.id)

    if user and user["banned"] == 1:
        await message.answer(
            "🚫 Твой аккаунт заблокирован администрацией."
        )
        return

    # Админов анкета не касается — сразу в меню
    is_admin = message.from_user.id in ADMIN_IDS
    status = get_access_status(message.from_user.id)

    if not is_admin and status == "new":
        if referred_bonus_given:
            await message.answer(f"🎁 Бонус за переход по приглашению друга: +{invite['invitee_coins']} Adam Coin!")
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
        await message.answer(f"🎁 Бонус за переход по приглашению друга: +{invite['invitee_coins']} Adam Coin!")

    # Приветственное сообщение
    await message.answer(
        """
👋 Добро пожаловать! Я — ADAM.

Вместе мы будем:

- 🎯 Развивать привычки
- 💬 Говорить о твоих планах и целях
- 📈 Следить за прогрессом
- 👥 Искать единомышленников

Всё это — в приложении. Открой его кнопкой ниже 👇
        """,
        reply_markup=welcome_menu(is_admin=message.from_user.id in ADMIN_IDS)
    )