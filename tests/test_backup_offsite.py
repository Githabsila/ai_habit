"""
Резервные копии после инцидента Railway (23.10.2026): хранение по дням (серия перезапусков не вытесняет вчерашнюю
копию), ежедневная сжатая отправка админу в Telegram, отметка «когда уходила» (после перезапуска копия не дублируется),
свежий снимок вместо вчерашнего, предупреждения вместо тихого молчания. Файл восстановления — tests/test_restore.py.
"""
import gzip
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backups.backup as backup_mod
from backups import restore
from db import get_error_stats

ROOT = Path(__file__).resolve().parent.parent


class FakeBot:
    def __init__(self, fail_document=False):
        self.documents = []          # (chat_id, распакованные байты, подпись, без звука?, имя файла)
        self.messages = []
        self.fail_document = fail_document

    async def send_document(self, chat_id, document, caption=None, disable_notification=False, **kwargs):
        if self.fail_document:
            raise RuntimeError("Telegram недоступен")
        with open(document.path, "rb") as f:          # файл удаляется сразу после отправки, читаем сейчас
            data = f.read()
        self.documents.append((chat_id, data, caption, disable_notification, os.path.basename(document.path)))

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_mod, "BACKUP_FOLDER", str(tmp_path))
    monkeypatch.setattr(backup_mod, "_offsite_running", False)
    return tmp_path


def _touch(folder, name):
    (folder / name).write_bytes(b"x")


# ---------------------------------------------------------------------------------------------------- хранение по дням

def test_prune_keeps_the_five_latest_and_one_copy_for_each_of_the_last_14_days(folder):
    names = [f"2026-09-{day:02d}_{hhmm}.db" for day in range(1, 21) for hhmm in ("01-00-00_000001", "12-00-00_000001", "23-59-59_000001")]
    for name in names:
        _touch(folder, name)
    backup_mod._prune_backups()
    expected = {f"2026-09-{day:02d}_23-59-59_000001.db" for day in range(7, 21)} | set(sorted(names)[-5:])
    assert {p.name for p in folder.iterdir()} == expected


def test_a_restart_storm_does_not_push_out_yesterdays_copy(folder):
    """Раньше хранились «последние 10 файлов»: вечер деплоев с серией перезапусков вытеснял всё, что было раньше."""
    _touch(folder, "2026-10-22_20-00-00_000001.db")
    for minute in range(12):
        _touch(folder, f"2026-10-23_18-{minute:02d}-00_000001.db")
    backup_mod._prune_backups()
    left = sorted(p.name for p in folder.iterdir())
    assert "2026-10-22_20-00-00_000001.db" in left
    assert len([n for n in left if n.startswith("2026-10-23")]) == 5


def test_prune_leaves_foreign_files_alone_and_clears_stale_packed_copies(folder):
    _touch(folder, "notes.txt")
    _touch(folder, "manual-copy.db")
    _touch(folder, "2026-10-23_10-00-00_000001.db.gz")
    backup_mod._prune_backups()
    assert (folder / "notes.txt").exists() and (folder / "manual-copy.db").exists()
    assert not (folder / "2026-10-23_10-00-00_000001.db.gz").exists()


def test_create_backup_returns_the_path_only_for_a_verified_copy(folder, monkeypatch):
    path = backup_mod.create_backup()
    assert path and os.path.exists(path) and backup_mod._verify_backup(path)
    monkeypatch.setattr(backup_mod, "_verify_backup", lambda p: False)
    assert backup_mod.create_backup() is None
    monkeypatch.setattr(backup_mod, "DATABASE", str(folder / "no_such_users.db"))
    assert backup_mod.create_backup() is None


# ---------------------------------------------------------------------------------------------------- отправка в Telegram

async def test_offsite_sends_a_packed_valid_copy_silently_to_the_admin(folder, tmp_path_factory):
    from config import ADMIN_ID
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    assert len(bot.documents) == 1
    chat_id, data, caption, silent, filename = bot.documents[0]
    assert chat_id == ADMIN_ID and silent is True, "каждый день копия приходит без звука"
    assert filename.endswith(".db.gz") and "Копия базы" in caption and "МБ" in caption
    unpacked = tmp_path_factory.mktemp("unpacked") / "users.db"
    unpacked.write_bytes(gzip.decompress(data))
    info = restore.inspect_backup(str(unpacked))
    assert info["integrity"] == "ok" and info["users"] is not None, "по Telegram уезжает настоящая, открывающаяся база"
    assert not [p for p in folder.iterdir() if p.name.endswith(".gz")], "временный архив удалён"
    assert (folder / backup_mod.OFFSITE_MARKER).exists()


async def test_after_a_restart_the_copy_waits_if_one_went_out_recently(folder):
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    await backup_mod.send_offsite_backup(bot, min_interval_hours=20)
    assert len(bot.documents) == 1, "серия перезапусков не засыпает админа файлами"
    await backup_mod.send_offsite_backup(bot)
    assert len(bot.documents) == 2, "суточная отправка по расписанию отметку не проверяет"
    stale = (datetime.now(timezone.utc) - timedelta(hours=21)).isoformat()
    (folder / backup_mod.OFFSITE_MARKER).write_text(stale, encoding="utf-8")
    await backup_mod.send_offsite_backup(bot, min_interval_hours=20)
    assert len(bot.documents) == 3, "после долгого простоя копия уходит, не дожидаясь утра"


async def test_a_broken_marker_never_blocks_the_copy(folder):
    (folder / backup_mod.OFFSITE_MARKER).write_text("не дата", encoding="utf-8")
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot, min_interval_hours=20)
    assert len(bot.documents) == 1


async def test_a_stale_latest_copy_is_replaced_by_a_fresh_snapshot(folder):
    first = backup_mod.create_backup()
    long_ago = time.time() - 8 * 3600
    os.utime(first, (long_ago, long_ago))
    before = len(backup_mod._backup_files())
    await backup_mod.send_offsite_backup(FakeBot())
    assert len(backup_mod._backup_files()) == before + 1, "в 05:00 уходит снимок на этот момент, а не вчерашний"


async def test_a_fresh_latest_copy_is_reused(folder):
    backup_mod.create_backup()
    before = len(backup_mod._backup_files())
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    assert len(backup_mod._backup_files()) == before and len(bot.documents) == 1


async def test_a_copy_that_fails_the_check_is_not_sent_and_the_admin_is_told(folder, monkeypatch):
    backup_mod.create_backup()
    monkeypatch.setattr(backup_mod, "_verify_backup", lambda path: False)
    before = get_error_stats(hours=24)["total"]
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    assert bot.documents == []
    assert len(bot.messages) == 1 and "не прошла проверку" in bot.messages[0][1]
    assert get_error_stats(hours=24)["total"] == before + 1
    assert not (folder / backup_mod.OFFSITE_MARKER).exists(), "неотправленная копия не считается отправленной"


async def test_a_copy_over_the_telegram_limit_warns_instead_of_failing_silently(folder, monkeypatch):
    monkeypatch.setattr(backup_mod, "TELEGRAM_FILE_LIMIT", 10)
    bot = FakeBot()
    await backup_mod.send_offsite_backup(bot)
    assert bot.documents == []
    assert len(bot.messages) == 1 and "лимита Telegram" in bot.messages[0][1]
    assert not (folder / backup_mod.OFFSITE_MARKER).exists()
    assert not [p for p in folder.iterdir() if p.name.endswith(".gz")]


async def test_a_failed_send_leaves_no_marker_and_no_temporary_archive(folder):
    before = get_error_stats(hours=24)["total"]
    await backup_mod.send_offsite_backup(FakeBot(fail_document=True))
    assert get_error_stats(hours=24)["total"] == before + 1
    assert not (folder / backup_mod.OFFSITE_MARKER).exists()
    assert not [p for p in folder.iterdir() if p.name.endswith(".gz")]
    assert backup_mod._offsite_running is False, "сбой не блокирует следующие отправки"


# ---------------------------------------------------------------------------------------------------- проводка и файлы

def test_main_sends_daily_and_once_after_each_start():
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'send_offsite_backup, "cron", hour=5, minute=0' in text
    assert 'day_of_week="sun", hour=5' not in text, "копия больше не раз в неделю"
    assert 'send_offsite_backup, "date"' in text and '"min_interval_hours": 20' in text


def test_the_docker_files_stay_out_of_the_railway_build():
    """Dockerfile в корне заставил бы Railway сменить сборку (Railpack + Procfile) на Docker — прод так не ломаем."""
    assert not (ROOT / "Dockerfile").exists() and not (ROOT / "railway.json").exists() and not (ROOT / "railway.toml").exists()
    dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "main.py"]' in dockerfile and "RAILWAY_VOLUME_MOUNT_PATH=/data" in dockerfile
    assert "COPY .env" not in dockerfile and "ENV BOT_TOKEN" not in dockerfile and "OPENAI_API_KEY=" not in dockerfile
    ignore = (ROOT / "deploy" / "Dockerfile.dockerignore").read_text(encoding="utf-8")
    for line in (".env", "users.db", "*.db.gz", ".git"):
        assert line in ignore.splitlines(), line
    assert (ROOT / "Procfile").read_text(encoding="utf-8").strip() == "web: python main.py"


def test_the_restore_guide_matches_the_real_schedule():
    guide = (ROOT / "RESTORE.md").read_text(encoding="utf-8")
    assert "backups/restore.py" in guide and "05:00 UTC" in guide and "--check" in guide and "--force" in guide
    assert "deploy/Dockerfile" in guide and "long polling" in guide
