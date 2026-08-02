"""
logging_config.py
Единая настройка логирования для всего проекта — этап 4 "Оптимизация"
("логирование" + основа для "мониторинга ошибок").

Пишет и в консоль (как раньше print), и в файл logs/bot.log с ротацией
(чтобы файл не рос бесконечно на диске). Ошибки самого AI-пайплайна
дополнительно попадают в БД через db.ai.log_error() — это отдельный канал
для админ-панели, не заменяющий обычные логи, а дополняющий их.

Использование (main.py):
    from logging_config import setup_logging
    setup_logging()
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

MAX_BYTES = 5 * 1024 * 1024  # 5 МБ на файл
BACKUP_COUNT = 3             # + до 3 старых файлов


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Не дублируем хендлеры, если setup_logging() вызвали дважды.
    if root.handlers:
        return root

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Библиотеки логируют довольно болтливо на INFO — приглушаем до WARNING,
    # чтобы в файле не тонули собственные логи проекта.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("groq").setLevel(logging.WARNING)

    return root
