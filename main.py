import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
PORT = int(os.getenv("PORT", 8080))
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, MenuButtonDefault, WebAppInfo

from config import BOT_TOKEN, WEBAPP_URL, PORT
from db import create_tables
from webapp.server import run_webapp

from logging_config import setup_logging

from scheduler import scheduler, new_day
from reminders import send_reminders
from coach import run_streak_risk_check, run_weekly_report
from onboarding_auto import run_auto_approve
from goal_feedback import run_goal_feedback
from morning_ping import run_morning_ping

from backups.backup import start_backup_scheduler

from middlewares.access_control import AccessControlMiddleware

# ====================== ИМПОРТЫ (Этап 1 + Этап 2) ======================
from handlers.admin import router as admin_router
from handlers.calendar import router as calendar_router
from handlers.start import router as start_router
from handlers.onboarding import router as onboarding_router
from handlers.goals import router as goals_router
from handlers.payments import router as payments_router
from handlers.menu import router as menu_router
from handlers.profile import router as profile_router
from handlers.habits import router as habits_router
from handlers.ai import router as ai_router
from handlers.progress import router as progress_router
from handlers.settings import router as settings_router
from handlers.rating import router as rating_router
from handlers.shop import router as shop_router
# =====================================================================

setup_logging()
logger = logging.getLogger("main")

create_tables()

print("✅ База данных подключена")

start_backup_scheduler()


async def main():

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher()

    # Middleware (закрытый доступ)
    dp.message.outer_middleware(AccessControlMiddleware())
    dp.callback_query.outer_middleware(AccessControlMiddleware())

    # Все роутеры (теперь MiniApp полностью работает)
    dp.include_router(admin_router)
    dp.include_router(shop_router)
    dp.include_router(calendar_router)
    dp.include_router(start_router)
    dp.include_router(onboarding_router)
    dp.include_router(goals_router)
    dp.include_router(payments_router)
    dp.include_router(menu_router)
    dp.include_router(profile_router)
    dp.include_router(habits_router)
    dp.include_router(ai_router)
    dp.include_router(progress_router)
    dp.include_router(settings_router)
    dp.include_router(rating_router)
   
    # Планировщик (без изменений)
    scheduler.add_job(send_reminders, "interval", minutes=120, args=[bot])
    scheduler.add_job(run_streak_risk_check, "cron", hour=20, minute=0, args=[bot])
    scheduler.add_job(run_weekly_report, "cron", day_of_week="sun", hour=19, minute=0, args=[bot])
    scheduler.add_job(run_morning_ping, "cron", hour=8, minute=0, args=[bot])
    scheduler.add_job(run_goal_feedback, "cron", day_of_week="mon", hour=10, minute=0, args=[bot])
    scheduler.add_job(run_auto_approve, "interval", minutes=15, args=[bot])
    scheduler.add_job(new_day, "cron", hour=0, minute=0)
    scheduler.start()

    # MiniApp кнопка
    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Открыть ADAM", web_app=WebAppInfo(url=WEBAPP_URL))
        )
        logger.info(f"MiniApp установлена: {WEBAPP_URL}")
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        logger.warning("WEBAPP_URL не задан — кнопка не установлена")

    logger.info("STARTING WEB SERVER")
    print("STARTING WEB SERVER", flush=True)

    web_runner = await run_webapp(PORT)

    logger.info(f"WEB SERVER STARTED ON PORT {PORT}")
    print(f"WEB SERVER STARTED ON PORT {PORT}", flush=True)

    logger.info(f"WEB SERVER STARTED ON PORT {PORT}")
    print(f"WEB SERVER STARTED ON PORT {PORT}")

    print("=" * 40)
    print("🤖 Project ADAM v1.0")
    print("✅ База данных подключена")
    print("✅ Планировщик запущен")
    print(f"🌐 MiniApp сервер: порт {PORT}" + (f" ({WEBAPP_URL})" if WEBAPP_URL else " (без домена)"))
    print("🚀 Бот успешно запущен")
    print("=" * 40)
    logger.info("Бот успешно запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())