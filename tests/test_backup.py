"""
backups/backup.py — раньше бэкап делался сырым shutil.copy2 (рискованно
при включённом WAL, см. db/core.py) и никогда не проверялся на
восстановимость: "файл скопировался без исключений" считалось достаточным.
Теперь — sqlite3 Backup API + PRAGMA integrity_check сразу после создания.
"""
import os

from db import get_error_stats


def test_create_backup_produces_a_restorable_file(uid, tmp_path):
    import backups.backup as backup_mod

    backup_mod.BACKUP_FOLDER = str(tmp_path)

    backup_mod.create_backup()

    files = [f for f in os.listdir(tmp_path) if f.endswith(".db")]
    assert len(files) == 1

    backup_path = os.path.join(tmp_path, files[0])
    assert backup_mod._verify_backup(backup_path) is True


def test_verify_backup_rejects_a_non_database_file(tmp_path):
    import backups.backup as backup_mod

    fake = tmp_path / "not_a_real.db"
    fake.write_text("this is definitely not a sqlite database")

    assert backup_mod._verify_backup(str(fake)) is False


def test_verify_backup_rejects_a_missing_file(tmp_path):
    import backups.backup as backup_mod

    missing = tmp_path / "does_not_exist.db"
    assert backup_mod._verify_backup(str(missing)) is False


def test_create_backup_logs_error_on_integrity_failure(monkeypatch, tmp_path):
    import backups.backup as backup_mod

    backup_mod.BACKUP_FOLDER = str(tmp_path)
    monkeypatch.setattr(backup_mod, "_verify_backup", lambda path: False)

    before = get_error_stats(hours=24)["total"]
    backup_mod.create_backup()
    after = get_error_stats(hours=24)["total"]

    assert after == before + 1


# =====================================
# ОФФСАЙТ-КОПИЯ (send_offsite_backup)
# =====================================
# Единственная копия бэкапа за пределами Railway volume — см. комментарий
# в backups/backup.py. Проверяем, что она реально уезжает админу и что
# отсутствие бэкапов/сбой отправки не роняют планировщик.

class _FakeBot:
    def __init__(self, raise_on_send=False):
        self.sent = []
        self.raise_on_send = raise_on_send

    async def send_document(self, chat_id, document, caption=None):
        if self.raise_on_send:
            raise RuntimeError("Telegram недоступен")
        self.sent.append((chat_id, document, caption))


async def test_send_offsite_backup_sends_latest_file(tmp_path):
    import backups.backup as backup_mod

    backup_mod.BACKUP_FOLDER = str(tmp_path)
    backup_mod.create_backup()

    bot = _FakeBot()
    await backup_mod.send_offsite_backup(bot)

    assert len(bot.sent) == 1


async def test_send_offsite_backup_noop_when_no_backups_exist(tmp_path):
    import backups.backup as backup_mod

    backup_mod.BACKUP_FOLDER = str(tmp_path)

    bot = _FakeBot()
    await backup_mod.send_offsite_backup(bot)

    assert bot.sent == []


async def test_send_offsite_backup_swallows_send_failure(tmp_path):
    """Сбой отправки (Telegram недоступен и т.п.) не должен ронять
    планировщик — только залогироваться (см. error_log)."""
    import backups.backup as backup_mod

    backup_mod.BACKUP_FOLDER = str(tmp_path)
    backup_mod.create_backup()

    bot = _FakeBot(raise_on_send=True)
    before = get_error_stats(hours=24)["total"]
    await backup_mod.send_offsite_backup(bot)
    after = get_error_stats(hours=24)["total"]

    assert after == before + 1
