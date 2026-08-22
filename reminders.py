from db import (
    get_all_users,
    get_settings,
    get_timezone,
)
from datetime import datetime
from zoneinfo import ZoneInfo


# =====================================
# РАССЫЛКА НАПОМИНАНИЙ
# =====================================

async def send_reminders(bot):

    users = get_all_users()

    print(f"📨 Отправка напоминаний ({len(users)} пользователей)")

    for user in users:

        telegram_id = user["telegram_id"]

        # Ночью после 00:00 напоминания полностью запрещены.
        # Утренний цикл снова разрешён с 06:00.
        try:
            now_local = datetime.now(ZoneInfo(get_timezone(telegram_id)))
            if 0 <= now_local.hour < 6:
                continue
        except Exception:
            pass

        settings = get_settings(telegram_id)

        if not settings:
            continue

        if settings["reminders"] == 0:
            continue

        try:
            await bot.send_message(
                telegram_id,
                """
⏰ <b>Напоминание!</b>

Не забудьте сегодня выполнить свои привычки 💪

🔥 Даже одна выполненная привычка делает вас лучше, чем вчера.

Удачного дня! 🚀
""",
                parse_mode="HTML"
            )

            print(f"✅ Напоминание отправлено {telegram_id}")

        except Exception as e:
            print(f"❌ Ошибка отправки {telegram_id}: {e}")