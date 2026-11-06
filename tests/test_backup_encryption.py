"""
Шифрование резервных копий и архив кода (план Б, 05.11; backups/crypto.py, backups/backup.py::send_code_archive).

С ключом BACKUP_ENCRYPTION_KEY копия базы уходит админу как «….db.gz.enc» и восстанавливается только с этим ключом (restore.py --key или
переменная). Без ключа — как раньше, но подпись предупреждает. Раз в неделю — архив кода, без секретов и базы.
"""
import zipfile

import pytest

import backups.backup as backup_mod
from backups import restore
from backups.crypto import new_key
from tests.test_backup_offsite import FakeBot


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "BACKUP_FOLDER", str(tmp_path))
    monkeypatch.setattr(backup_mod, "_offsite_running", False)
    return tmp_path


async def test_with_a_key_the_copy_is_encrypted_and_restores_only_with_it(folder, tmp_path_factory, monkeypatch):
    key = new_key()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", key)
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    chat_id, data, caption, silent, name = bot.documents[0]
    assert name.endswith(".db.gz.enc") and "🔒 зашифрована" in caption
    assert not data.startswith(b"\x1f\x8b"), "внутри не обычный gzip — зашифровано"

    out = tmp_path_factory.mktemp("restore")
    sent = out / name
    sent.write_bytes(data)
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY")
    assert restore.main([str(sent), "--check"]) == 1, "без ключа — отказ"
    assert restore.main([str(sent), "--check", "--key", new_key()]) == 1, "чужой ключ — отказ"
    assert restore.main([str(sent), "--check", "--key", key]) == 0
    target = out / "users.db"
    assert restore.main([str(sent), "--to", str(target), "--key", key]) == 0 and target.exists()
    assert not list(out.glob("*.decrypted")), "временный расшифрованный файл не остаётся"


async def test_without_a_key_it_works_as_before_and_warns(folder, monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    _chat, data, caption, _silent, name = bot.documents[0]
    assert name.endswith(".db.gz") and data.startswith(b"\x1f\x8b") and "без шифрования" in caption


def test_the_code_archive_has_the_code_but_no_secrets_or_database(tmp_path):
    root = tmp_path / "proj"
    (root / "db").mkdir(parents=True)
    (root / "backups").mkdir()
    (root / "main.py").write_text("print(1)")
    (root / "db" / "core.py").write_text("x = 1")
    (root / "Procfile").write_text("web: python main.py")
    (root / ".env").write_text("BOT_TOKEN=secret")
    (root / ".env.example").write_text("BOT_TOKEN=your_token")
    (root / "users.db").write_bytes(b"SQLite")
    (root / "backups" / "2026-11-06.db").write_bytes(b"SQLite")
    dest = tmp_path / "code.zip"
    count = backup_mod.build_code_archive(str(root), str(dest))
    names = set(zipfile.ZipFile(dest).namelist())
    assert names == {"main.py", "db/core.py", "Procfile", ".env.example"} and count == 4


async def test_the_code_archive_goes_weekly_and_encrypted(folder, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", new_key())
    bot = FakeBot()
    assert await backup_mod.send_code_archive(bot) is True
    _chat, _data, caption, silent, name = bot.documents[0]
    assert name.startswith("adam-code-") and name.endswith(".zip.enc") and "🔒" in caption and silent
    assert await backup_mod.send_code_archive(bot) is False, "второй раз в ту же неделю — не уходит"
    assert len(bot.documents) == 1
    assert not [p for p in folder.iterdir() if p.suffix in (".zip", ".enc")], "временные архивы удалены"
