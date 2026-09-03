import logging
import os
import sqlite3
import sys
import threading
import time

from datetime import datetime

logger = logging.getLogger("backups")

# Даёт доступ к DATA_DIR/DB_PATH из db.py, чтобы бэкапы 100% указывали
# на тот же файл, что и сама база, а не на случайную копию в другом месте.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import DATA_DIR, DB_PATH, log_error


# =====================================
# НАСТРОЙКИ
# =====================================

# Бэкапы кладём тоже внутрь постоянного Volume (в подпапку) — если положить
# их рядом с кодом, как было раньше, они будут стираться при каждом
# редеплое точно так же, как раньше стиралась сама users.db.
BACKUP_FOLDER = os.path.join(DATA_DIR, "backups")
DATABASE = DB_PATH
MAX_BACKUPS = 10


# =====================================
# СОЗДАТЬ БЭКАП
# =====================================

def _verify_backup(path):
    """PRAGMA integrity_check на свежесозданном бэкапе. Раньше бэкап
    считался "готовым", если файл просто скопировался без исключений —
    о том, реально ли он восстановится, узнали бы только в момент
    настоящего восстановления после потери данных, когда уже поздно
    что-то исправлять. Возвращает True, только если проверка прошла
    и явно вернула "ok"."""
    # sqlite3.connect() на несуществующий путь молча СОЗДАЁТ новый пустой
    # файл БД вместо ошибки — а integrity_check пустой (но валидной) базы
    # тривиально проходит. Без этой проверки отсутствующий бэкап отчитался
    # бы как "успешно проверен".
    if not os.path.exists(path):
        return False
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        conn.close()
        return bool(result) and result[0] == "ok"
    except Exception:
        return False


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
        # sqlite3 Backup API вместо сырого shutil.copy2: база работает в
        # WAL-режиме (db/core.py PRAGMA journal_mode=WAL) — часть уже
        # закоммиченных данных в момент копирования может лежать ещё в
        # сайдкар-файле -wal, а не в самом .db, так что побайтовая копия
        # рисковала получить неполный или структурно нецелостный снапшот
        # при неудачном стечении времени. Backup API снимает консистентный
        # снапшот безопасно, независимо от состояния журнала.
        src_conn = sqlite3.connect(DATABASE)
        dst_conn = sqlite3.connect(destination)
        with dst_conn:
            src_conn.backup(dst_conn)
        src_conn.close()
        dst_conn.close()
        print(f"💾 Создан бэкап: {filename}")

    except Exception as e:
        print(f"❌ Ошибка создания бэкапа: {e}")
        try:
            log_error("backup_create", e)
        except Exception:
            pass
        return

    # Проверка восстанавливаемости сразу после создания — если бэкап битый,
    # хотим узнать об этом в тот же день, а не через полгода при попытке
    # реально восстановиться. Видна в admin_digest_scheduler.py через
    # общий error_log (🩺 Мониторинг ошибок в ежедневной сводке).
    if not _verify_backup(destination):
        print(f"⚠️ Бэкап {filename} не прошёл проверку целостности!")
        try:
            log_error("backup_integrity", f"integrity check failed: {filename}")
        except Exception:
            pass

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


# =====================================
# ОФФСАЙТ-КОПИЯ (за пределы Railway volume)
# =====================================
# create_backup() выше защищает от порчи самой users.db, но кладёт бэкап
# РЯДОМ, на тот же Railway volume — если весь volume будет потерян целиком
# (инцидент платформы, случайное удаление сервиса), пропадут и база, и все
# её бэкапы одновременно. Единственная реально независимая копия — та, что
# уехала за пределы Railway. Раз в неделю (см. main.py) отправляем свежий
# бэкап админу в Telegram документом: бесплатно, не требует внешнего
# хранилища, и лежит в облаке Telegram, а не на диске сервиса.

async def send_offsite_backup(bot):
    from aiogram.types import FSInputFile

    from config import ADMIN_ID
    from db import log_error

    if not os.path.exists(BACKUP_FOLDER):
        return

    backups = sorted(f for f in os.listdir(BACKUP_FOLDER) if f.endswith(".db"))
    if not backups:
        return

    latest = os.path.join(BACKUP_FOLDER, backups[-1])
    try:
        await bot.send_document(
            chat_id=ADMIN_ID,
            document=FSInputFile(latest),
            caption=f"💾 Еженедельный оффсайт-бэкап базы: {backups[-1]}",
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить оффсайт-бэкап админу: {e}")
        try:
            log_error("backup_offsite_send", e)
        except Exception:
            pass