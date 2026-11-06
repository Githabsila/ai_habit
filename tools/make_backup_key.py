"""Новый ключ шифрования резервных копий (backups/crypto.py). Запуск: python tools/make_backup_key.py

Ключ положи в Railway → Variables как BACKUP_ENCRYPTION_KEY и сохрани копию в менеджере паролей. Без ключа копии не открыть.
В чат, в код и в репозиторий ключ не кладём."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backups.crypto import new_key  # noqa: E402

if __name__ == "__main__":
    print(new_key())
