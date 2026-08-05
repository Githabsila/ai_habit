from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import (
    reset_habits,
    get_all_users,
    create_daily_tasks,
    check_achievements
)

scheduler = AsyncIOScheduler()


# =====================================
# НОВЫЙ ДЕНЬ
# =====================================

def new_day():

    print("=" * 40)
    print("🌅 Новый день")

    try:

        # Сбросить выполнение привычек
        reset_habits()

        print("✅ Привычки сброшены")

    except Exception as e:

        print(f"❌ Ошибка сброса привычек: {e}")

    users = get_all_users()

    for user in users:

        try:

            telegram_id = user["telegram_id"]

            # Создать ежедневные задания
            create_daily_tasks(telegram_id)

            # Проверить достижения
            check_achievements(telegram_id)

        except Exception as e:

            print(f"Ошибка пользователя {user['telegram_id']}: {e}")

    print("✅ Ежедневные задания созданы")
    print("🏁 Новый день подготовлен")
    print("=" * 40)


# =====================================
# ПЛАНИРОВЩИК
# =====================================

scheduler.add_job(
    new_day,
    "cron",
    hour=0,
    minute=0
)