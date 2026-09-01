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
