"""
backups/restore.py — восстановление базы из копии (.db или .db.gz, как присылает бот). Должно работать без проекта,
ничего не затирать молча и не оставлять мусора. Отправку копий проверяет tests/test_backup_offsite.py.
"""
import gzip
import os
import sqlite3

import pytest

import backups.backup as backup_mod
from backups import restore


@pytest.fixture
def real_copy(tmp_path, monkeypatch):
    """Настоящая копия живой тестовой базы + её же в виде .db.gz, как уходит в Telegram."""
    folder = tmp_path / "backups"
    folder.mkdir()
    monkeypatch.setattr(backup_mod, "BACKUP_FOLDER", str(folder))
    plain = backup_mod.create_backup()
    packed = str(tmp_path / "copy.db.gz")
    with open(plain, "rb") as fi, gzip.open(packed, "wb") as fo:
        fo.write(fi.read())
    return {"plain": plain, "packed": packed, "info": restore.inspect_backup(plain)}


def _tiny_db(path, with_users=True):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER)" if with_users else "CREATE TABLE other (id INTEGER)")
    if with_users:
        conn.executemany("INSERT INTO users VALUES (?)", [(1,), (2,), (3,)])
    conn.commit()
    conn.close()


def test_a_packed_copy_restores_to_an_identical_database(real_copy, tmp_path):
    dest = str(tmp_path / "data" / "users.db")
    result = restore.restore_backup(real_copy["packed"], dest)
    assert result["dest"] == dest and result["moved_aside"] == []
    again = restore.inspect_backup(dest)
    assert again["integrity"] == "ok"
    assert again["tables"] == real_copy["info"]["tables"], "те же таблицы и столько же строк в каждой"
    assert not os.path.exists(dest + ".restoring"), "временного файла не осталось"


def test_a_plain_copy_restores_too(real_copy, tmp_path):
    dest = str(tmp_path / "users.db")
    restore.restore_backup(real_copy["plain"], dest)
    assert restore.inspect_backup(dest)["users"] == real_copy["info"]["users"]


def test_check_only_reads_and_writes_nothing(real_copy, tmp_path):
    before = sorted(os.listdir(tmp_path))
    info = restore.check_backup(real_copy["packed"])
    assert info["integrity"] == "ok" and info["users"] == real_copy["info"]["users"]
    assert sorted(os.listdir(tmp_path)) == before


def test_an_existing_database_is_never_replaced_without_force(real_copy, tmp_path):
    dest = tmp_path / "users.db"
    _tiny_db(str(dest))
    with pytest.raises(restore.RestoreError, match="--force"):
        restore.restore_backup(real_copy["packed"], str(dest))
    assert restore.inspect_backup(str(dest))["users"] == 3, "прежняя база на месте"
    assert not os.path.exists(str(dest) + ".restoring")


def test_force_sets_the_old_database_and_its_journal_files_aside(real_copy, tmp_path):
    dest = tmp_path / "users.db"
    _tiny_db(str(dest))
    (tmp_path / "users.db-wal").write_bytes(b"old wal")
    (tmp_path / "users.db-shm").write_bytes(b"old shm")
    result = restore.restore_backup(real_copy["packed"], str(dest), force=True)
    assert len(result["moved_aside"]) == 3 and all(os.path.exists(p) for p in result["moved_aside"])
    assert restore.inspect_backup(str(dest))["users"] == real_copy["info"]["users"]
    assert not (tmp_path / "users.db-wal").exists(), "старый журнал не должен достаться восстановленной базе"
    aside_main = [p for p in result["moved_aside"] if not p.endswith(("-wal", "-shm"))][0]
    assert restore.inspect_backup(aside_main)["users"] == 3


def test_damaged_or_foreign_files_are_refused_and_leave_no_trace(tmp_path):
    dest = str(tmp_path / "users.db")
    garbage_gz = tmp_path / "garbage.db.gz"
    garbage_gz.write_bytes("это не архив".encode("utf-8"))
    not_a_db = tmp_path / "not_a_db.db"
    not_a_db.write_text("и это не база данных", encoding="utf-8")
    no_users = tmp_path / "other.db"
    _tiny_db(str(no_users), with_users=False)
    good = tmp_path / "good.db"
    _tiny_db(str(good))
    packed = gzip.compress(good.read_bytes())
    truncated = tmp_path / "cut.db.gz"
    truncated.write_bytes(packed[: len(packed) // 2])

    for bad in (garbage_gz, not_a_db, no_users, truncated, tmp_path / "missing.db.gz"):
        with pytest.raises(restore.RestoreError):
            restore.restore_backup(str(bad), dest)
    assert not os.path.exists(dest) and not os.path.exists(dest + ".restoring")


def test_the_command_line_reports_success_and_failure_with_exit_codes(real_copy, tmp_path, capsys):
    assert restore.main([real_copy["packed"], "--check"]) == 0
    assert "Копия цела" in capsys.readouterr().out
    dest = str(tmp_path / "users.db")
    assert restore.main([real_copy["packed"], "--to", dest]) == 0
    assert "Готово" in capsys.readouterr().out
    assert restore.main([real_copy["packed"], "--to", dest]) == 1
    assert "--force" in capsys.readouterr().out
    assert restore.main([str(tmp_path / "missing.db.gz"), "--check"]) == 1


def test_the_default_target_follows_the_volume_path(monkeypatch):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert restore.default_target() == os.path.join("/data", "users.db")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH")
    assert restore.default_target().endswith("users.db")


def test_the_script_needs_nothing_from_the_project():
    """Восстанавливаться надо уметь, когда всё остальное уже не запускается: только стандартная библиотека."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(restore.__file__).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert modules <= {"argparse", "gzip", "os", "shutil", "sqlite3", "sys", "datetime", "pathlib"}, modules
