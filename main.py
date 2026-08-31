import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, MenuButtonDefault, WebAppInfo

from config import BOT_TOKEN, WEBAPP_URL, PORT, ADMIN_IDS
from db import create_tables, get_user, give_premium_admin
from webapp.webapp_server import run_webapp
from logging_config import setup_logging
from scheduler import scheduler
from streak_scheduler import run_streak_rollover, run_streak_risk_notifications, run_streak_reengagement_notifications, run_weekly_streak_bonus
from coach import (
    run_weekly_report,
    run_weekly_habit_analysis, run_task_reminder_check,
    run_habit_checkpoint_10, run_habit_checkpoint_12, run_day_progress_check,
    run_week_start_ping, run_week_end_ping, run_month_start_ping, run_month_end_ping,
    run_planned_time_reminders,
)
from onboarding_auto import run_auto_approve
from goal_feedback import run_goal_feedback
from morning_ping import run_morning_ping
from subscription_scheduler import run_trial_reminders
from admin_digest_scheduler import run_admin_daily_digest, DIGEST_HOUR_UTC
from error_alert_scheduler import run_error_spike_check
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

# Администраторы бота всегда получают Premium навсегда, без покупки.
# add_user() уже делает это для НОВЫХ пользователей (db/users.py
# _ensure_admin_premium) — здесь то же самое для админов, которые уже
# существуют в базе с прошлых запусков.
for _admin_id in ADMIN_IDS:
    if get_user(_admin_id):
        give_premium_admin(_admin_id)

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
    scheduler.add_job(run_task_reminder_check, "interval", minutes=15, args=[bot])
    # Своё время напоминания у привычки (habits.planned_time) — опционально,
    # молчит для тех, у кого не задано. Каждую минуту, как и остальные
    # job'ы, завязанные на точную локальную минуту пользователя.
    scheduler.add_job(run_planned_time_reminders, "interval", minutes=1, args=[bot])
    # Контрольная точка проверяется каждую минуту и сама учитывает
    # локальный часовой пояс каждого пользователя. Это важно на Railway,
    # где timezone контейнера может отличаться от timezone пользователя.
    scheduler.add_job(run_weekly_report, "cron", day_of_week="sun", hour=19, minute=0, args=[bot])
    scheduler.add_job(run_weekly_habit_analysis, "cron", day_of_week="sun", hour=19, minute=15, args=[bot])
    # Утреннее приветствие — в 06:00, не в 12:00/08:00 (промт п.12)
    scheduler.add_job(run_morning_ping, "interval", minutes=1, args=[bot])
    scheduler.add_job(run_goal_feedback, "cron", day_of_week="mon", hour=10, minute=0, args=[bot])
    scheduler.add_job(run_auto_approve, "interval", minutes=15, args=[bot])
    # Ударный режим работает по локальному времени каждого пользователя.
    # Rollover идёт каждую минуту и идемпотентно обрабатывает только тех,
    # у кого наступил новый локальный день.
    scheduler.add_job(run_streak_rollover, "interval", minutes=1)
    scheduler.add_job(run_streak_risk_notifications, "interval", minutes=1, args=[bot])
    # Возврат в ударный режим: 10:00 / 16:00 / 21:00 после срыва, затем
    # постепенно реже для тех, кто пропал надолго. Проверка каждую минуту
    # учитывает локальный час пользователя и останавливается после первого
    # выполненного действия в текущем дне.
    scheduler.add_job(run_streak_reengagement_notifications, "interval", minutes=1, args=[bot])
    scheduler.add_job(run_weekly_streak_bonus, "interval", minutes=1, args=[bot])

    # --- Умные напоминания ---
    # 10:00 — единая контрольная точка по привычкам (локальное время каждого пользователя).
    # Job тикает каждую минуту, а сама функция пропускает всех, кто не в своём 10:00.
    scheduler.add_job(run_habit_checkpoint_10, "interval", minutes=1, args=[bot])
    # 12:00 — дневная проверка привычек. Отдельные 2-часовые пинги по привычкам
    # отключены: здесь одно сводное сообщение только по невыполненным привычкам.
    scheduler.add_job(run_habit_checkpoint_12, "interval", minutes=1, args=[bot])
    scheduler.add_job(run_day_progress_check, "interval", minutes=1, args=[bot])
    # 19:00 — единая сверка главной + второстепенных задач.

    # --- Промт п.8: начало/конец недели и месяца ---
    scheduler.add_job(run_week_start_ping, "cron", day_of_week="mon", hour=7, minute=0, args=[bot])
    scheduler.add_job(run_week_end_ping, "cron", day_of_week="sun", hour=18, minute=0, args=[bot])
    scheduler.add_job(run_month_start_ping, "cron", day=1, hour=7, minute=30, args=[bot])
    scheduler.add_job(run_month_end_ping, "cron", day="last", hour=18, minute=30, args=[bot])

    # --- Пром 13: напоминания триала → подписки (сам гейт по умолчанию
    # выключен, см. config.SUBSCRIPTION_GATE_ENABLED) ---
    scheduler.add_job(run_trial_reminders, "interval", minutes=1, args=[bot])

    # Ежедневная проактивная сводка админам (DAU, ошибки за 24ч, расход
    # AI-квоты против общего потолка, воронки, конверсия подписки) — не
    # ждём, пока кто-то сам зайдёт в /admin → "Статистика".
    scheduler.add_job(run_admin_daily_digest, "cron", hour=DIGEST_HOUR_UTC, minute=0, args=[bot])

    # Реалтайм-алерт при всплеске ошибок — не ждём до утренней сводки.
    scheduler.add_job(run_error_spike_check, "interval", minutes=10, args=[bot])

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
        web_runner = await run_webapp(PORT, bot)
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