import asyncio
import gzip
import logging
import os
import re
import shutil
import sqlite3
import sys
import threading
import time

from datetime import datetime, timezone

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

# Сколько копий держим на томе. Раньше было «последние 10 файлов», а копия
# делается при КАЖДОМ запуске процесса: за один вечер деплоев (серия
# перезапусков) все десять становились копиями последних минут, и вчерашней
# базы на томе уже не было. Теперь: пять самых свежих + по одной (самой
# поздней) за каждый из последних 14 дней.
KEEP_RECENT = 5
KEEP_DAYS = 14

# Telegram принимает документ до 50 МБ; оставляем запас.
TELEGRAM_FILE_LIMIT = 49 * 1024 * 1024
# Когда последний раз копия уехала в Telegram (хранится рядом с копиями, на томе:
# переживает деплои, поэтому серия перезапусков не засыпает админа файлами).
OFFSITE_MARKER = "offsite_last.txt"

_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_.*\.db$")
_offsite_running = False


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


def _backup_files():
    """Имена копий (.db с датой в начале имени), от старых к новым."""
    if not os.path.isdir(BACKUP_FOLDER):
        return []
    return sorted(f for f in os.listdir(BACKUP_FOLDER) if _NAME_RE.match(f))


def _prune_backups():
    """Оставляет пять самых свежих копий и по одной (самой поздней) за каждый
    из последних KEEP_DAYS дней. Чужие файлы в папке не трогает; забытые
    временные .db.gz (упаковка для Telegram прервалась) убирает."""
    names = _backup_files()
    keep = set(names[-KEEP_RECENT:])
    newest_of_day = {}
    for name in names:                      # отсортировано: поздняя копия дня перезапишет раннюю
        newest_of_day[name[:10]] = name
    for day in sorted(newest_of_day)[-KEEP_DAYS:]:
        keep.add(newest_of_day[day])

    for name in names:
        if name in keep:
            continue
        old = os.path.join(BACKUP_FOLDER, name)
        try:
            os.remove(old)
            print(f"🗑 Удалён старый бэкап: {name}")
        except Exception as e:
            print(f"❌ Не удалось удалить {old}: {e}")

    for name in os.listdir(BACKUP_FOLDER):
        if name.endswith(".db.gz"):
            try:
                os.remove(os.path.join(BACKUP_FOLDER, name))
            except Exception:
                pass


def create_backup():
    """Делает копию базы. Возвращает путь к ней, если она создана И прошла
    проверку целостности; иначе None (ошибка уже записана в error_log)."""

    os.makedirs(BACKUP_FOLDER, exist_ok=True)

    if not os.path.exists(DATABASE):
        print("❌ База данных не найдена.")
        return None

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
        return None

    # Проверка восстанавливаемости сразу после создания — если бэкап битый,
    # хотим узнать об этом в тот же день, а не через полгода при попытке
    # реально восстановиться. Видна в admin_digest_scheduler.py через
    # общий error_log (🩺 Мониторинг ошибок в ежедневной сводке).
    verified = _verify_backup(destination)
    if not verified:
        print(f"⚠️ Бэкап {filename} не прошёл проверку целостности!")
        try:
            log_error("backup_integrity", f"integrity check failed: {filename}")
        except Exception:
            pass

    _prune_backups()
    return destination if verified else None


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
# уехала за пределы Railway. Раз в сутки (05:00 UTC, см. main.py) и через пару
# минут после запуска процесса, если за последние ~20 часов копия не уходила,
# отправляем свежий снимок админу в Telegram документом, сжатым gzip:
# бесплатно, не требует внешнего хранилища и карты, лежит в облаке Telegram.
# Как восстановиться из такого файла — RESTORE.md и backups/restore.py.

def _read_offsite_marker():
    try:
        with open(os.path.join(BACKUP_FOLDER, OFFSITE_MARKER), encoding="utf-8") as f:
            return datetime.fromisoformat(f.read().strip())
    except Exception:
        return None


def _write_offsite_marker():
    try:
        os.makedirs(BACKUP_FOLDER, exist_ok=True)
        with open(os.path.join(BACKUP_FOLDER, OFFSITE_MARKER), "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.warning(f"Не удалось записать отметку об оффсайт-копии: {e}")


def _gzip_file(src, dst):
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)


def _fresh_backup_path(max_age_hours=6):
    """Копия для отправки: самая свежая, если ей меньше max_age_hours, иначе
    снимается новая (в 05:00 «вчерашний» снимок суточного цикла уже устарел)."""
    names = _backup_files()
    latest = os.path.join(BACKUP_FOLDER, names[-1]) if names else None
    if latest and time.time() - os.path.getmtime(latest) < max_age_hours * 3600:
        return latest
    return create_backup() or latest


async def send_offsite_backup(bot, min_interval_hours=None):
    """Отправляет админу сжатую копию базы. min_interval_hours — не отправлять,
    если копия уходила недавно (для запуска после перезапуска процесса)."""
    global _offsite_running
    from aiogram.types import FSInputFile

    from config import ADMIN_ID
    from db import log_error

    if _offsite_running:
        return

    if min_interval_hours is not None:
        last = _read_offsite_marker()
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last).total_seconds() < min_interval_hours * 3600:
                return

    _offsite_running = True
    packed = None
    try:
        source = await asyncio.to_thread(_fresh_backup_path)
        if not source:
            return

        name = os.path.basename(source)
        if not await asyncio.to_thread(_verify_backup, source):
            try:
                log_error("backup_offsite_integrity", f"offsite copy failed integrity check: {name}")
            except Exception:
                pass
            try:
                await bot.send_message(ADMIN_ID, f"⚠️ Копия базы {name} не прошла проверку целостности, в Telegram не отправляю.")
            except Exception as e:
                logger.warning(f"Не удалось предупредить админа о битой копии: {e}")
            return

        packed = source + ".gz"
        await asyncio.to_thread(_gzip_file, source, packed)
        size = os.path.getsize(packed)

        if size > TELEGRAM_FILE_LIMIT:
            try:
                log_error("backup_offsite_size", f"offsite copy too big for Telegram: {size} bytes")
            except Exception:
                pass
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Копия базы ({size / 1024 / 1024:.0f} МБ после сжатия) больше лимита Telegram (50 МБ). "
                    "Нужно внешнее хранилище, см. RESTORE.md.",
                )
            except Exception as e:
                logger.warning(f"Не удалось предупредить админа о размере копии: {e}")
            return

        await bot.send_document(
            chat_id=ADMIN_ID,
            document=FSInputFile(packed),
            caption=f"💾 Копия базы · {name[:10]} · {max(size / 1024 / 1024, 0.1):.1f} МБ · целостность проверена",
            disable_notification=True,
        )
        _write_offsite_marker()
        logger.info(f"Копия базы отправлена админу в Telegram: {name[:10]}, {max(size / 1024 / 1024, 0.1):.1f} МБ")   # по этой строке в журнале видно, что файл ушёл
    except Exception as e:
        logger.warning(f"Не удалось отправить оффсайт-бэкап админу: {e}")
        try:
            log_error("backup_offsite_send", e)
        except Exception:
            pass
    finally:
        _offsite_running = False
        if packed:
            try:
                os.remove(packed)
            except Exception:
                pass
