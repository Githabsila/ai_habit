import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

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

# ====================== ИМПОРТЫ ХЕНДЛЕРОВ ======================
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
from handlers.achievements import router as achievements_router
from handlers.daily import router as daily_router
from handlers.community import router as community_router

# ====================== НАСТРОЙКА ЛОГГИРОВАНИЯ ======================
setup_logging()
logger = logging.getLogger("main")

# ====================== ИНИЦИАЛИЗАЦИЯ БД ======================
create_tables()
logger.info("✅ База данных подключена")

# ====================== БЭКАПЫ ======================
start_backup_scheduler()

# ====================== ОСНОВНАЯ ФУНКЦИЯ ======================
async def main():
    # --- БОТ ---
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # --- ДИСПЕТЧЕР ---
    dp = Dispatcher()

    # Middleware (доступ)
    dp.message.outer_middleware(AccessControlMiddleware())
    dp.callback_query.outer_middleware(AccessControlMiddleware())

    # Роутеры
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
    dp.include_router(achievements_router)
    dp.include_router(daily_router)
    dp.include_router(community_router)

    # --- ПЛАНИРОВЩИК ---
    scheduler.add_job(send_reminders, "interval", minutes=120, args=[bot])
    scheduler.add_job(run_streak_risk_check, "cron", hour=20, minute=0, args=[bot])
    scheduler.add_job(run_weekly_report, "cron", day_of_week="sun", hour=19, minute=0, args=[bot])
    scheduler.add_job(run_morning_ping, "cron", hour=8, minute=0, args=[bot])
    scheduler.add_job(run_goal_feedback, "cron", day_of_week="mon", hour=10, minute=0, args=[bot])
    scheduler.add_job(run_auto_approve, "interval", minutes=15, args=[bot])
    scheduler.add_job(new_day, "cron", hour=0, minute=0)
    scheduler.start()
    logger.info("✅ Планировщик запущен")

    # --- КНОПКА MINIAPP ---
    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть ADAM",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        )
        logger.info(f"✅ MiniApp кнопка установлена: {WEBAPP_URL}")
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        logger.warning("⚠️ WEBAPP_URL не задан — кнопка MiniApp не установлена")

    # --- ЗАПУСК ВЕБ-СЕРВЕРА ---
    logger.info("🌐 Запуск веб-сервера...")
    try:
        web_runner = await run_webapp(PORT)
        logger.info(f"✅ Веб-сервер запущен на порту {PORT}")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        web_runner = None

    # --- СТАРТОВОЕ СООБЩЕНИЕ ---
    print("\n" + "=" * 50)
    print("🤖 Project ADAM v1.0")
    print("✅ База данных подключена")
    print("✅ Планировщик запущен")
    print(f"🌐 Веб-сервер: порт {PORT}" + (f" ({WEBAPP_URL})" if WEBAPP_URL else ""))
    print("🚀 Бот успешно запущен")
    print("=" * 50 + "\n")
    logger.info("🚀 Бот успешно запущен")

    # --- ЗАПУСК ПОЛЛИНГА ---
    try:
        await dp.start_polling(bot)
    finally:
        if web_runner:
            await web_runner.cleanup()
            logger.info("🛑 Веб-сервер остановлен")

# ====================== ТОЧКА ВХОДА ======================
if __name__ == "__main__":
    asyncio.run(main())