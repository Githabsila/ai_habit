"""
Шифрование резервных копий (старый план, п.2.4; план Б, раздел 3).

Копия базы (и раз в неделю — архив кода) уходит админу в Telegram. Без шифрования любой, кто получит файл, получит всю базу людей.
Если в Railway задан BACKUP_ENCRYPTION_KEY, файл шифруется (Fernet: AES-128-CBC + HMAC-SHA256) и получает окончание «.enc».
Ключ — только в Railway и в надёжном месте у владельца (менеджер паролей). ПОТЕРЯ КЛЮЧА = КОПИИ НЕ ОТКРЫТЬ.

Новый ключ: python tools/make_backup_key.py
Расшифровать и восстановить: python backups/restore.py КОПИЯ.db.gz.enc --check  (ключ — из BACKUP_ENCRYPTION_KEY или --key)
"""
import os

ENC_SUFFIX = ".enc"


def backup_key():
    """Ключ из окружения (BACKUP_ENCRYPTION_KEY) или None — тогда копии уходят как раньше, без шифрования."""
    value = os.getenv("BACKUP_ENCRYPTION_KEY", "").strip()
    return value or None


def new_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def encrypt_file(src, dst, key):
    from cryptography.fernet import Fernet
    with open(src, "rb") as fi:
        data = fi.read()
    with open(dst, "wb") as fo:
        fo.write(Fernet(key.encode() if isinstance(key, str) else key).encrypt(data))


def decrypt_file(src, dst, key):
    """Бросает cryptography.fernet.InvalidToken, если ключ не тот или файл повреждён."""
    from cryptography.fernet import Fernet
    with open(src, "rb") as fi:
        data = fi.read()
    with open(dst, "wb") as fo:
        fo.write(Fernet(key.encode() if isinstance(key, str) else key).decrypt(data))
