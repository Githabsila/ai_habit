import os
import shutil
import threading
import time

from datetime import datetime


# =====================================
# НАСТРОЙКИ
# =====================================

BACKUP_FOLDER = "backups"
DATABASE = "users.db"
MAX_BACKUPS = 10


# =====================================
# СОЗДАТЬ БЭКАП
# =====================================

def create_backup():

    os.makedirs(BACKUP_FOLDER, exist_ok=True)

    if not os.path.exists(DATABASE):
        print("❌ База данных не найдена.")
        return

    # имя с миллисекундами, чтобы не было совпадений
    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + ".db"

    destination = os.path.join(
        BACKUP_FOLDER,
        filename
    )

    try:
        shutil.copy2(DATABASE, destination)
        print(f"💾 Создан бэкап: {filename}")

    except Exception as e:
        print(f"❌ Ошибка создания бэкапа: {e}")
        return

    # Получаем только .db файлы
    backups = sorted(
        f for f in os.listdir(BACKUP_FOLDER)
        if f.endswith(".db")
    )

    # Удаляем старые
    while len(backups) > MAX_BACKUPS:

        oldest = os.path.join(
            BACKUP_FOLDER,
            backups.pop(0)
        )

        if os.path.exists(oldest):
            try:
                os.remove(oldest)
                print(f"🗑 Удалён старый бэкап: {os.path.basename(oldest)}")
            except Exception as e:
                print(f"❌ Не удалось удалить {oldest}: {e}")


# =====================================
# ЦИКЛ АВТОБЭКАПОВ
# =====================================

def backup_loop():

    while True:

        create_backup()

        # 24 часа
        time.sleep(60 * 60 * 24)


# =====================================
# ЗАПУСК
# =====================================

def start_backup_scheduler():

    thread = threading.Thread(
        target=backup_loop,
        daemon=True,
        name="BackupThread"
    )

    thread.start()

    print("💾 Автобэкап запущен")