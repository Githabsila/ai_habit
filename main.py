import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from db import create_tables

from scheduler import scheduler, new_day
from reminders import send_reminders

from backups.backup import start_backup_scheduler

from handlers.admin import router as admin_router
from handlers.calendar import router as calendar_router
from handlers.start import router as start_router
from handlers.menu import router as menu_router
from handlers.profile import router as profile_router
from handlers.habits import router as habits_router
from handlers.ai import router as ai_router
from handlers.progress import router as progress_router
from handlers.settings import router as settings_router
from handlers.rating import router as rating_router
from handlers.achievements import router as achievements_router
from handlers.community import router as community_router
from handlers.daily import router as daily_router
from handlers.bonus import router as bonus_router
from handlers.shop import router as shop_router


# =====================================
# ИНИЦИАЛИЗАЦИЯ
# =====================================

create_tables()

print("✅ База данных подключена")

start_backup_scheduler()


# =====================================
# ОСНОВНАЯ ФУНКЦИЯ
# =====================================

async def main():

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    dp = Dispatcher()

    # ==========================
    # Роутеры
    # ==========================

    dp.include_router(admin_router)
    dp.include_router(shop_router)
    dp.include_router(calendar_router)
    dp.include_router(start_router)
    dp.include_router(menu_router)
    dp.include_router(profile_router)
    dp.include_router(habits_router)
    dp.include_router(ai_router)
    dp.include_router(progress_router)
    dp.include_router(settings_router)
    dp.include_router(rating_router)
    dp.include_router(achievements_router)
    dp.include_router(community_router)
    dp.include_router(daily_router)
    dp.include_router(bonus_router)

    # ==========================
    # Напоминания
    # ==========================

    scheduler.add_job(
        send_reminders,
        "interval",
        minutes=120,
        args=[bot]
    )

    # ==========================
    # Новый день
    # ==========================

    scheduler.add_job(
        new_day,
        "cron",
        hour=0,
        minute=0
    )

    scheduler.start()

    print("=" * 40)
    print("🤖 Project ADAM v1.0")
    print("✅ База данных подключена")
    print("✅ Планировщик запущен")
    print("🚀 Бот успешно запущен")
    print("=" * 40)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())