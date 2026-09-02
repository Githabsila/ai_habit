
import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from config import WEBAPP_URL

from db import (
    rollover_all_users, get_streak_users, get_timezone, create_daily_tasks,
    has_completed_today, claim_notification, release_notification, notification_scope, in_time_window,
    RISK_15, RISK_23,
    get_weekly_bonus_available, get_streak_reengagement_state, get_settings,
    get_recent_streak_message_keys, record_streak_message_key,
    reminder_category_enabled, in_quiet_hours,
    get_freeze_upsell_eligibility, week_key,
    get_users_near_personal_record,
    get_rank_overtakes_and_update_snapshot,
    get_gender, gender_forms,
)

logger = logging.getLogger("streak_scheduler")

async def run_streak_rollover(bot=None):
    try:
        changed = rollover_all_users()
        for uid in changed:
            try:
                create_daily_tasks(uid)
            except Exception:
                logger.exception("Не удалось создать ежедневные задания для %s", uid)
        if changed:
            logger.info("Streak rollover: %s users", len(changed))
    except Exception:
        logger.exception("Ошибка rollover ударного режима")

# В памяти держим активные таймеры, чтобы не создавать второй таймер на
# одного пользователя при следующем минутном проходе планировщика.
_active_countdowns = {}


RISK_23_FIRST = [
    "⚡️ 23:00. Ударный режим под угрозой. Остались невыполненные привычки — ещё есть время всё закрыть.",
    "🔥 23:00. Последний час начался. Не оставляй ударный режим без внимания.",
    "😠 23:00. Финиш близко. Проверь невыполненные привычки и сохрани свою серию.",
]


RISK_23_30 = [
    "😡 30 минут до сброса! Закрой хотя бы 1 привычку и спаси свой ударный режим. Время пошло! ⏱️",
    "⚡️ Твой ударный режим сгорит через 30 минут. Сделаешь хотя бы 1 привычку или сдашься❓️",
    "❗️ Не сливай весь свой прогресс за 30 минут! Закрой одну привычку прямо сейчас. 🔥",
    "😡 Осталось 30 минут. Все твои слова про дисциплину — правда или пустой звук? Докажи! 🎯",
    "🚨 Критический момент: 30 минут до обнуления! Закрывай 1 привычку и сохраняй статус. ⏳",
    "❗️ Ты реально {gotov_lower} сжечь ударный режим? У тебя 30 минут, чтобы сделать хотя бы 1 шаг! 🚨",
    "⏱️ 30 минут. Выполни 1 привычку! Сохрани свой результат или начнёшь с полного нуля завтра! 💥",
    "⚡️ Не смей бросать день на финише! 30 минут — закрой одну привычку и с чистой совестью отдыхай! 🏆",
    "😡 30 минут до сброса. Покажи характер или признай, что лень сегодня победила! 🥊",
    "🚨 Осталось 30 минут! Зайди и отметь 1 привычку, чтобы удержать ударный режим! ⚡️",
]


def _countdown_keyboard():
    if not WEBAPP_URL:
        return None
    # Раньше здесь была обычная url-кнопка — она открывала ссылку во внешнем
    # браузере, и Telegram не передавал туда initData, поэтому Mini App не
    # мог авторизовать пользователя. web_app=WebAppInfo(...) открывает то же
    # приложение как Telegram Mini App (как и постоянная кнопка меню в
    # main.py) — авторизация сохраняется.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Открыть ADAM и закрыть привычку", web_app=WebAppInfo(url=WEBAPP_URL))]
        ]
    )


async def _run_countdown(bot, uid, message_id, tz_name, deadline):
    """Редактирует одно сообщение таймера раз в ~30 секунд до локальной полуночи.
    Редактирование не создаёт новые Telegram-уведомления, поэтому пользователь
    не получает спам из 60 отдельных сообщений."""
    key = (uid, message_id)
    try:
        tz = ZoneInfo(tz_name)
        while True:
            now = datetime.now(tz)
            end_dt = datetime.combine(deadline, time(0, 0), tzinfo=tz) + timedelta(days=1)
            remaining = int((end_dt - now).total_seconds())
            if remaining <= 0:
                break

            minutes, seconds = divmod(remaining, 60)
            # Чтобы сообщение не менялось слишком часто, округляем секунды до
            # ближайших 30 секунд. Пользователь всё равно видит живой отсчёт.
            shown_seconds = 30 if seconds > 15 else 0
            text = (
                "🚨 <b>ФИНИШНЫЙ ТАЙМЕР</b>\n\n"
                "😡 Ударный режим под угрозой.\n"
                f"⏱️ Осталось: <b>{minutes:02d}:{shown_seconds:02d}</b>\n\n"
                "Закрой хотя бы одну привычку — и серия будет спасена."
            )
            try:
                await bot.edit_message_text(
                    chat_id=uid,
                    message_id=message_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=_countdown_keyboard(),
                )
            except Exception:
                logger.exception("Не удалось обновить таймер для %s", uid)

            await asyncio.sleep(30)

            # Если человек выполнил хотя бы одну привычку, прекращаем таймер:
            # серия уже спасена.
            if has_completed_today(uid):
                try:
                    await bot.edit_message_text(
                        chat_id=uid,
                        message_id=message_id,
                        text="🔥 <b>Ударный режим спасён!</b>\n\nТы успел закрыть привычку. Хорошая работа.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                break
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Ошибка countdown для %s", uid)
    finally:
        _active_countdowns.pop(key, None)


# Порог, с которого предлагаем заморозку: короткие серии люди обычно не
# жалко потерять, а вот длинную (неделя+) — самое время предложить страховку
# ДО того, как она сгорит, а не постфактум.
FREEZE_UPSELL_MIN_STREAK = 7
FREEZE_UPSELL_COST_XP = 200


async def _maybe_send_freeze_upsell(bot, uid, now, scope):
    """Улучшение #38 ("стрик-страховка"): в момент 23:00, когда серия реально
    под угрозой (0 привычек за день), а у пользователя нет ни одной заморозки
    в запасе — мягко напоминаем, что её можно купить, вместо того чтобы просто
    молча дать серии сгореть. Не чаще раза в неделю на человека, чтобы не
    превратиться в рекламный спам."""
    try:
        info = get_freeze_upsell_eligibility(uid)
    except Exception:
        logger.exception("Не удалось получить данные для freeze-upsell для %s", uid)
        return
    if info["streak"] < FREEZE_UPSELL_MIN_STREAK:
        return
    if info["freeze_balance"] > 0:
        return
    if info["xp"] < FREEZE_UPSELL_COST_XP:
        return

    wk = week_key(now.date())
    if not claim_notification(uid, wk, "freeze_upsell", scope):
        return
    try:
        await bot.send_message(
            uid,
            f"❄️ У тебя серия в {info['streak']} дн. — и ноль заморозок в запасе.\n\n"
            "Заморозка спасает день, если не успеешь закрыть привычки: серия не "
            f"обнулится. Стоит {FREEZE_UPSELL_COST_XP} Adam Coin, купить можно в разделе «Ударный режим».",
            reply_markup=_countdown_keyboard(),
        )
    except Exception:
        release_notification(uid, wk, "freeze_upsell", scope)
        raise


async def run_streak_risk_notifications(bot):
    """23:00 — короткий факт о риске; 23:30 — главный срочный пинг + живой таймер.
    Оба события отправляются только если за день ещё не выполнена ни одна
    привычка, а 23:30 имеет отдельный одноразовый ключ."""
    if not bot:
        return

    scope = notification_scope(bot)

    for uid in get_streak_users():
        try:
            settings = get_settings(uid)
            if not reminder_category_enabled(settings, "streak"):
                continue

            tz_name = get_timezone(uid)
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)
            if in_quiet_hours(settings, now):
                continue
            # Окна допуска вместо "== ровно эта минута" — от повторов
            # защищает claim_notification ниже, а не точность тика
            # планировщика. Окна не пересекаются (23:00-23:04 и 23:30-23:34),
            # так что перепутать 23:00 и 23:30 они не могут.
            in_2300 = in_time_window(now, hour=23, minute=0)
            in_2330 = in_time_window(now, hour=23, minute=30)
            if not (in_2300 or in_2330):
                continue
            if has_completed_today(uid):
                # Если человек уже спас серию, никакого финишного спама.
                continue

            day = now.date().isoformat()

            if in_2300:
                if not claim_notification(uid, day, "risk23", scope):
                    continue
                try:
                    await bot.send_message(
                        uid,
                        random.choice(RISK_23_FIRST),
                        parse_mode="HTML",
                        reply_markup=_countdown_keyboard(),
                    )
                except Exception:
                    release_notification(uid, day, "risk23", scope)
                    raise
                await _maybe_send_freeze_upsell(bot, uid, now, scope)
                continue

            # 23:30 — главное сообщение. После него одно сообщение редактируется
            # в реальном времени до полуночи.
            if not claim_notification(uid, day, "risk2330", scope):
                continue
            try:
                risk_text = random.choice(RISK_23_30).format(**gender_forms(get_gender(uid)))
                sent = await bot.send_message(
                    uid,
                    risk_text,
                    parse_mode="HTML",
                    reply_markup=_countdown_keyboard(),
                )
            except Exception:
                release_notification(uid, day, "risk2330", scope)
                raise
            task = asyncio.create_task(
                _run_countdown(bot, uid, sent.message_id, tz_name, now.date())
            )
            _active_countdowns[(uid, sent.message_id)] = task
        except Exception:
            logger.exception("Ошибка streak-risk для %s", uid)

# ============================================================
# ВОЗВРАТ В УДАРНЫЙ РЕЖИМ
# ============================================================
# Это отдельная система, не зависящая от текущего streak. Если человек уже
# когда-то выполнял привычки, но выпал из режима, ADAM продолжает мягко
# возвращать его обратно. Тексты выбираются без LLM — быстро и без затрат
# токенов, но с учётом срока отсутствия и времени суток.

# Фидбек: раньше часть сообщений говорила об Адаме в 3-м лице ("ADAM тебя
# не списал", "вопрос от Адама", "Адам оставляет дверь открытой") — читалось
# как безличное системное уведомление, а не как личное сообщение ОТ Адама.
# Переписано на "я" (Адам обращается напрямую), плюс {gotov}/{hotel}/
# {vyshel}/{vybral}/{propustil}/{byl} — плейсхолдеры под согласование рода
# "Ты"-обращения к ПОЛЬЗОВАТЕЛЮ (см. db.users.gender_forms), там где в
# тексте раньше было зашито мужское "готов"/"хотел"/"вышел"/"выбрал"/
# "пропустил"/"был". Реплики САМОГО Адама о себе ("я не списал") намеренно
# остаются в одном роде везде по проекту — это голос персонажа, а не
# обращение к пользователю, менять не нужно.
REENGAGE_MESSAGES = {
    "10": [
        ("r10_1", "🫵🤨 <b>Ты сдался, или докажешь обратное?</b>\n\nВчерашний день уже не вернуть. Зато сегодня можно начать новую серию. Открой ADAM и закрой хотя бы одну привычку. 🔥"),
        ("r10_2", "⚡️ <b>Серия погасла. Характер — нет.</b>\n\nНе пытайся вернуть всё сразу. Сделай один шаг сегодня — и новый ударный режим уже начнётся. {gotov}?"),
        ("r10_3", "😈 <b>Ну что, возвращаемся?</b>\n\nОдин пропуск не решает, кем ты будешь дальше. Отметь одну привычку и снова зажги свою серию. 🔥"),
        ("r10_4", "🔥 <b>Я тебя не списал.</b>\n\nТы просто выпал из ритма. Вернуться можно в любой момент — даже сегодня. Начни с одной привычки."),
        ("r10_5", "⏱️ <b>Новый старт не требует идеального дня.</b>\n\nТребуется только первое действие. Закрой одну привычку — остальное построим дальше вместе."),
    ],
    "16": [
        ("r16_1", "🤬 <b>Полдня прошло. И что теперь?</b>\n\nСдаться окончательно или всё-таки сделать первый шаг? До вечера ещё есть время. Вернись в ADAM. 🔥"),
        ("r16_2", "🫵 <b>Ты всё ещё можешь перевернуть этот день.</b>\n\nНе нужен подвиг. Одна закрытая привычка — и ты снова в игре. Сделаешь?"),
        ("r16_3", "⚡️ <b>Пауза затянулась.</b>\n\nНо это не финал. Сегодня ещё можно начать новую серию. Выбери одну привычку и действуй."),
        ("r16_4", "😡 <b>Ты {hotel} изменить себя — помнишь?</b>\n\nНе позволяй нескольким пропущенным дням решить всё за тебя. Вернись хотя бы на один шаг сегодня."),
        ("r16_5", "🎯 <b>Сегодня ещё не проигран.</b>\n\nТвоя задача сейчас простая: одна привычка. Не думай о неделе. Думай о следующем действии."),
    ],
    "21": [
        ("r21_1", "🌙 <b>Вечер. Последний шанс начать заново сегодня.</b>\n\nНе жди понедельника, первого числа или «подходящего момента». Одна привычка — и ты снова в движении. 🔥"),
        ("r21_2", "😈 <b>Завтра можно начать. Но почему не сегодня?</b>\n\nЗакрой одну привычку сейчас и покажи себе, что ты ещё не {vyshel} из игры."),
        ("r21_3", "⏳ <b>Ещё один день уходит.</b>\n\nВопрос простой: ты оставишь паузу паузой или превратишь этот вечер в точку возврата?"),
        ("r21_4", "🔥 <b>Не нужен идеальный перезапуск.</b>\n\nНужен первый вечер, когда ты снова {vybral} себя. Начни с одной привычки."),
        ("r21_5", "🫵🤨 <b>У меня к тебе последний вопрос.</b>\n\nТы действительно хочешь вернуться — или просто ждёшь, пока мотивация придёт сама? Сделай действие первым."),
    ],
    "long": [
        ("rlong_1", "🔥 <b>Ты давно не заходил. Я это заметил.</b>\n\nТебе не нужно оправдываться. После недели или месяца паузы можно просто начать заново. Одна привычка сегодня — первый кирпич новой серии."),
        ("rlong_2", "🫵 <b>Прошло уже {days} дн.</b>\n\nНо знаешь что? Твоя история не закончилась. Возвращайся без попытки сделать всё сразу. Начни с одной привычки."),
        ("rlong_3", "⚡️ <b>Пауза стала длинной. Значит, нужен не упрёк, а новый старт.</b>\n\nОткрой ADAM. Выбери одну привычку. Сделай её сегодня — и дальше пойдём по шагам."),
        ("rlong_4", "😈 <b>Месяц? Три дня? Неважно.</b>\n\nВажен следующий выбор. Ты можешь продолжить старую паузу или сегодня поставить ей точку. Начни с одной привычки."),
        ("rlong_5", "🚀 <b>Возвращение начинается не с мотивации.</b>\n\nОно начинается с действия. Не обещай себе новую жизнь — просто закрой одну привычку сегодня."),
        ("rlong_6", "🧠 <b>Не пытайся наверстать всё сразу.</b>\n\nТы {propustil} {days} дн. — и это уже факт. Следующий факт можешь создать сам: одна выполненная привычка сегодня."),
        ("rlong_7", "🔥 <b>Твоя серия закончилась. Твой путь — нет.</b>\n\nИногда дисциплина — это не идеальная неделя, а способность вернуться после паузы. Начни сегодня."),
        ("rlong_8", "⚡️ <b>Я оставляю дверь открытой.</b>\n\nНеважно, сколько дней ты {byl} вне режима. Не нужно объяснений. Просто зайди и сделай первое действие."),
    ],
}



def _reengagement_keyboard():
    return _countdown_keyboard()

def _choose_reengagement_message(uid, slot, inactive_days):
    import hashlib
    pool = REENGAGE_MESSAGES["long"] if inactive_days >= 4 else REENGAGE_MESSAGES[slot]
    recent = set(get_recent_streak_message_keys(uid, limit=12))
    available = [item for item in pool if item[0] not in recent]
    if not available:
        available = pool
    # Детерминированный выбор не даёт двум параллельным тикам случайно выбрать
    # один и тот же текст и делает поведение воспроизводимым.
    seed = f"{uid}:{slot}:{inactive_days}:{datetime.now(ZoneInfo(get_timezone(uid))).date().isoformat()}"
    idx = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(available)
    key, text = available[idx]
    forms = gender_forms(get_gender(uid))
    text = text.format(days=inactive_days, **forms)
    return key, text

async def run_streak_reengagement_notifications(bot):
    """Возвращает выпавших пользователей в ударный режим.

    1–3 дня отсутствия: 10:00, 16:00, 21:00.
    4–7 дней: 12:00 и 20:00.
    8–30 дней: один мягкий пинг в 18:00 через день.
    31+ дней: один пинг в 18:00 раз в три дня.

    В любой момент после первой выполненной привычки на текущий день все
    дальнейшие сообщения автоматически прекращаются.
    """
    if not bot:
        return

    for uid in get_streak_users():
        try:
            settings = get_settings(uid)
            if not reminder_category_enabled(settings, "streak"):
                continue
            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if in_quiet_hours(settings, now):
                continue
            state = get_streak_reengagement_state(uid)
            inactive = int(state["inactive_days"] or 0)
            if not state["has_history"] or inactive <= 0:
                continue
            if has_completed_today(uid):
                continue

            # Для первого дня после срыва — три разных точки, как задумано.
            # in_time_window вместо "== ровно эта минута": окна по 4 минуты
            # внутри одного часа не пересекаются между собой, поэтому slot
            # определяется однозначно, а повтор в пределах окна всё равно
            # блокируется claim_notification ниже.
            if inactive <= 3:
                slot = next((h for h in ("10", "16", "21") if in_time_window(now, hour=int(h))), None)
                if slot is None:
                    continue
            elif inactive <= 7:
                slot = next((h for h in ("12", "20") if in_time_window(now, hour=int(h))), None)
                if slot is None:
                    continue
            elif inactive <= 30:
                if not in_time_window(now, hour=18) or inactive % 2 != 0:
                    continue
                slot = "long"
            else:
                if not in_time_window(now, hour=18) or inactive % 3 != 0:
                    continue
                slot = "long"

            day = now.date().isoformat()
            kind = f"streak_reengage_{slot}"
            scope = notification_scope(bot)
            if not claim_notification(uid, day, kind, scope):
                continue

            key, text = _choose_reengagement_message(uid, slot if slot in ("10", "16", "21") else "long", inactive)
            try:
                await bot.send_message(uid, text, parse_mode="HTML", reply_markup=_reengagement_keyboard())
                record_streak_message_key(uid, key)
            except Exception:
                release_notification(uid, day, kind, scope)
                raise
        except Exception:
            logger.exception("Ошибка streak-reengagement для %s", uid)


async def run_weekly_streak_bonus(bot):
    if not bot:
        return
    scope = notification_scope(bot)
    for uid in get_streak_users():
        try:
            # Раньше этот job вообще не смотрел на настройки напоминаний —
            # награда за неделю без пропусков приходила даже тем, кто
            # отключил пуши про ударный режим.
            settings = get_settings(uid)
            if not reminder_category_enabled(settings, "streak"):
                continue

            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if in_quiet_hours(settings, now):
                continue
            if now.weekday() != 6 or not in_time_window(now, hour=10, minute=0):
                continue
            day = now.date().isoformat()
            if not get_weekly_bonus_available(uid):
                continue
            if not claim_notification(uid, day, "weekly_bonus", scope):
                continue
            try:
                await bot.send_message(
                    uid,
                    "🎁 Неделя в огне!\n\nТы прошёл предыдущие 7 дней без пропусков. "
                    "Открой ADAM и выбери награду: 200 Adam Coin или временную рамку на 7 дней."
                )
            except Exception:
                release_notification(uid, day, "weekly_bonus", scope)
                raise
        except Exception:
            logger.exception("Ошибка weekly streak bonus для %s", uid)


async def run_personal_record_notifications(bot):
    """Улучшение #49: пользователю, чья текущая серия ровно на 1 день короче
    его собственного исторического рекорда, в 9:00 приходит мотивирующий
    пуш. get_users_near_personal_record() уже отфильтровала кандидатов одним
    SQL-запросом — здесь только фильтры настроек/тихих часов + дедуп."""
    if not bot:
        return
    scope = notification_scope(bot)
    for row in get_users_near_personal_record():
        uid = row["user_id"]
        try:
            settings = get_settings(uid)
            if not reminder_category_enabled(settings, "streak"):
                continue
            tz = ZoneInfo(get_timezone(uid))
            now = datetime.now(tz)
            if in_quiet_hours(settings, now):
                continue
            if not in_time_window(now, hour=9, minute=0):
                continue
            day = now.date().isoformat()
            if not claim_notification(uid, day, "personal_record", scope):
                continue
            try:
                await bot.send_message(
                    uid,
                    f"🏆 Ещё один день — и это твой личный рекорд!\n\n"
                    f"Серия сейчас {row['streak']} дн., рекорд — {row['best_streak']} дн. "
                    "Закрой сегодня хотя бы одну привычку и завтра сравняешься с лучшим результатом.",
                    reply_markup=_countdown_keyboard(),
                )
            except Exception:
                release_notification(uid, day, "personal_record", scope)
                raise
        except Exception:
            logger.exception("Ошибка personal-record для %s", uid)


# Улучшение #40: в отличие от остальных job'ов этого файла (проверяют КАЖДОГО
# пользователя в его локальном времени), сравнение рейтинга — ГЛОБАЛЬНАЯ
# операция (один снимок на всех через get_rank_overtakes_and_update_snapshot).
# Гонять её на каждом минутном тике планировщика means "обогнал" фиксировался
# бы почти в реальном времени между соседними тиками — бессмысленно и
# избыточно. Однократный в памяти процесса гейт на календарный день (по UTC,
# единственный Railway-инстанс — см. комментарий про RATE_LIMIT выше)
# ограничивает сравнение одним разом в сутки.
_last_rank_check_day = None


async def run_rank_overtaken_notifications(bot):
    """Улучшение #40: раз в сутки (8:00 UTC) сравнивает сезонный рейтинг со
    вчерашним снимком и уведомляет тех, кого обогнали в пределах топ-100."""
    global _last_rank_check_day
    if not bot:
        return
    now_utc = datetime.now(ZoneInfo("UTC"))
    if not in_time_window(now_utc, hour=8, minute=0):
        return
    today_key = now_utc.date().isoformat()
    if _last_rank_check_day == today_key:
        return
    _last_rank_check_day = today_key

    try:
        overtaken = get_rank_overtakes_and_update_snapshot()
    except Exception:
        logger.exception("Ошибка получения rank overtakes")
        return

    scope = notification_scope(bot)
    for item in overtaken:
        uid = item["user_id"]
        try:
            settings = get_settings(uid)
            if not reminder_category_enabled(settings, "streak"):
                continue
            tz = ZoneInfo(get_timezone(uid))
            now_local = datetime.now(tz)
            if in_quiet_hours(settings, now_local):
                continue
            day = now_local.date().isoformat()
            if not claim_notification(uid, day, "rank_overtaken", scope):
                continue
            name = item["overtaker_name"] or "Кто-то"
            try:
                await bot.send_message(
                    uid,
                    f"📉 {name} обогнал тебя в сезонном рейтинге!\n\n"
                    f"Было место #{item['old_rank']}, теперь #{item['new_rank']}. "
                    "Заработай Adam Coin сегодня — и верни своё место.",
                )
            except Exception:
                release_notification(uid, day, "rank_overtaken", scope)
                raise
        except Exception:
            logger.exception("Ошибка rank-overtaken пуша для %s", uid)
