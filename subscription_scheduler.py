"""
Доставка сообщений триала (каталог и тексты — trial_messages.py, состояние человека — db/trial.py, журнал предложений и паузы — db/trial_flow.py).

Раз в минуту проходит по людям, у которых идёт СЦЕНАРИЙ триала (режим «trial» у аккаунта или общий гейт подписки; у тестировщиков со свободным или временным
доступом триал-сообщений нет), и шлёт то, что подходит им по дню триала, местному времени, заходам и уже показанным предложениям.

Правила частоты (ТЗ §12–§14, §18): у каждого вида своё ограничение — одноразовые сообщения уходят один раз за всё время, редкая серия после триала идёт по паузам из
db/trial_flow.py; общего жёсткого предела «сообщений в день» нет (вся нагрузка видна в статистике). Сообщение можно выключить в админке (все сразу или по одному),
не слать в тихие часы и тому, кто выключил напоминания (сообщения о настоящем изменении доступа — исключение). Приглашение дня 2 не уходит, если человек уже заходил
или получил предложение в приложении; после оплаты ничего из этого не приходит (фаза «paid» не входит в каталог). От повторов защищает claim_notification; при ошибке
отправки claim откатывается и следующий тик повторит.

Выключено по умолчанию на уровне гейта (SUBSCRIPTION_GATE_ENABLED=false): без режима «trial» у аккаунта сообщений нет.
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import bot_style
from db import (
    get_all_users, get_timezone, get_settings,
    claim_notification, release_notification, notification_scope,
)
from db.settings import in_quiet_hours
from db.access import trial_scenario_for_user
from db import trial as trial_db
from db import trial_flow
from db.tracking import track_server
from db.users import is_bot_forbidden_error, mark_bot_blocked
from keyboards import trial_keyboard
import trial_messages

logger = logging.getLogger("subscription_scheduler")

PHASES = {message.phase for message in trial_messages.CATALOG}

# Сообщения, которые при включённом оформлении «Royal Gold» приходят с баннером (bot_style.py): приглашение второго дня и «пробный доступ заканчивается».
BANNER_KEYS = {"d2_invite", "d3_end"}


def build_message(key, snap, link=None):
    """(текст, клавиатура) сообщения триала для данного состояния человека — и для рассылки, и для «отправить» из админки."""
    message = trial_messages.BY_KEY[key]
    text = trial_messages.render(key, snap, link=link)
    return text, trial_keyboard(message.buttons, snap["price_stars"], key)


async def run_trial_reminders(bot):
    """Один проход по людям в сценарии триала."""
    users = get_all_users(include_blocked=False)
    scope = notification_scope(bot)
    sent = 0

    for user in users:
        telegram_id = user["telegram_id"]
        if not trial_scenario_for_user(user):
            continue

        try:
            settings = get_settings(telegram_id)
            if not settings:
                continue
            try:
                now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            except Exception:
                continue
            snap = trial_db.snapshot(telegram_id, now_local.astimezone(timezone.utc))
            if snap["phase"] not in PHASES:
                continue
            ctx = trial_flow.context(telegram_id, snap)
            due = trial_messages.due_messages(snap, ctx)
            if not due:
                continue
            snap = dict(snap, wb=ctx["winback"])
            reminders_off = settings["reminders"] == 0
            quiet = in_quiet_hours(settings, now_local)

            for message in due:
                if not trial_db.message_enabled(message.key):
                    continue
                if quiet or (reminders_off and not message.service):
                    continue
                if not message.series and trial_db.sent_ever(telegram_id, message.key):
                    continue
                kind = f"trial:{message.key}"
                if not claim_notification(telegram_id, snap["local_date"], kind, scope):
                    continue
                try:
                    text, keyboard = build_message(message.key, snap)
                    # Оформление «Royal Gold» (bot_style.py): приглашение на второй день и «пробный доступ заканчивается» приходят с золотым баннером, если оно включено у человека.
                    with_banner = message.key in BANNER_KEYS and await bot_style.send_banner(bot, telegram_id, "trial", text, keyboard)
                    if not with_banner:
                        await bot.send_message(telegram_id, text, reply_markup=keyboard)
                except Exception as exc:
                    release_notification(telegram_id, snap["local_date"], kind, scope)
                    if is_bot_forbidden_error(exc):
                        mark_bot_blocked(telegram_id)
                        break
                    raise
                trial_flow.record(telegram_id, "push", message.key, "sent", snap["local_date"])
                track_server(telegram_id, "trial_msg_sent", message.key, meta={"phase": snap["phase"], "day": snap["day"]})
                sent += 1
                if message.series:
                    break                                           # редкая серия: за раз не больше одного сообщения
        except Exception:
            logger.exception("Ошибка сообщения триала для %s", telegram_id)

    if sent:
        logger.info("Сообщения триала/подписки: отправлено %s", sent)
