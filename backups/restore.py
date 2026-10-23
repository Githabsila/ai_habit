"""
Восстановление базы ADAM из копии — на Railway, на новом сервере или на своём компьютере.

Работает на любом компьютере с Python 3.9+, БЕЗ зависимостей проекта (только стандартная
библиотека), чтобы им можно было воспользоваться, когда всё остальное уже не запускается.

Копию присылает бот в Telegram (раз в сутки, файл «….db.gz»). Либо берётся с тома: папка backups/.

    python backups/restore.py КОПИЯ.db.gz --check              # только проверить: копия цела и открывается
    python backups/restore.py КОПИЯ.db.gz --to /data/users.db  # положить на место базы
    python backups/restore.py КОПИЯ.db.gz --to users.db --force  # то же, если там уже есть база (старая отложится рядом)

Куда класть по умолчанию (--to не указан): RAILWAY_VOLUME_MOUNT_PATH/users.db, а если переменной нет — users.db рядом
с проектом. Подробная инструкция — RESTORE.md в корне проекта.
"""
import argparse
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_NAME = "users.db"
MAIN_TABLE = "users"          # без неё файл — не база ADAM


class RestoreError(Exception):
    """Копию нельзя восстановить: сообщение понятно человеку."""


def default_target():
    base = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, DB_NAME)


def _unpack(src, dest):
    """Кладёт обычный файл базы в dest: распаковывает .gz или просто копирует."""
    if not os.path.isfile(src):
        raise RestoreError(f"Файл не найден: {src}")
    try:
        if str(src).lower().endswith(".gz"):
            with gzip.open(src, "rb") as fi, open(dest, "wb") as fo:
                shutil.copyfileobj(fi, fo)
        else:
            shutil.copyfile(src, dest)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise RestoreError(f"Не удалось прочитать копию (файл повреждён или обрезан): {e}") from e


def inspect_backup(path):
    """Открывает базу только на чтение и проверяет её: целостность и число строк в таблицах.
    Возвращает {"integrity": "ok" | текст ошибки, "tables": {имя: строк}, "users": число|None, "size": байт}."""
    # immutable=1: файл копии заведомо не меняется, SQLite не создаёт рядом служебные -wal/-shm
    # (копия сделана в режиме журнала WAL, и обычное чтение оставило бы их на диске).
    uri = Path(os.path.abspath(path)).as_uri() + "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return {"integrity": f"не открывается: {e}", "tables": {}, "users": None, "size": os.path.getsize(path)}
    try:
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            tables = {}
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                tables[name] = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        except sqlite3.Error as e:
            return {"integrity": f"не читается: {e}", "tables": {}, "users": None, "size": os.path.getsize(path)}
        return {"integrity": integrity, "tables": tables, "users": tables.get(MAIN_TABLE), "size": os.path.getsize(path)}
    finally:
        conn.close()


def check_backup(src):
    """Распаковывает копию во временный файл рядом с ней, проверяет и убирает за собой. Возвращает inspect_backup()."""
    tmp = str(src) + ".check.tmp"
    try:
        _unpack(src, tmp)
        info = inspect_backup(tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return info


def _validate(info):
    if info["integrity"] != "ok":
        raise RestoreError(f"Копия не прошла проверку целостности: {info['integrity']}")
    if info["users"] is None:
        raise RestoreError(f"В файле нет таблицы «{MAIN_TABLE}» — это не база ADAM. Восстановление отменено.")


def restore_backup(src, dest, force=False):
    """Кладёт копию на место базы dest. Ничего не затирает молча: существующую базу (вместе с её -wal и -shm)
    без force не трогает, а с force откладывает рядом как «….before-restore-ДАТА». Возвращает описание результата."""
    dest = os.path.abspath(dest)
    tmp = dest + ".restoring"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        _unpack(src, tmp)
        info = inspect_backup(tmp)
        _validate(info)

        moved = []
        if os.path.exists(dest) and not force:
            raise RestoreError(f"По адресу {dest} уже лежит база. Чтобы заменить её (старая сохранится рядом), добавь --force.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            old = dest + suffix
            if os.path.exists(old):
                aside = f"{dest}.before-restore-{stamp}{suffix}"
                shutil.move(old, aside)
                moved.append(aside)
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"dest": dest, "moved_aside": moved, **info}


def _print_info(info):
    print(f"  целостность: {info['integrity']}")
    print(f"  размер: {info['size'] / 1024 / 1024:.1f} МБ, таблиц: {len(info['tables'])}")
    if info["users"] is not None:
        print(f"  людей в базе: {info['users']}")
    for name in ("habits", "habit_logs", "daily_quests", "user_events"):
        if name in info["tables"]:
            print(f"  {name}: {info['tables'][name]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Восстановление базы ADAM из копии (.db или .db.gz)")
    parser.add_argument("backup", help="файл копии")
    parser.add_argument("--check", action="store_true", help="только проверить копию, ничего не записывать")
    parser.add_argument("--to", dest="dest", default=None, help="куда положить базу (по умолчанию — место базы проекта)")
    parser.add_argument("--force", action="store_true", help="заменить уже существующую базу (старая сохранится рядом)")
    args = parser.parse_args(argv)

    try:
        if args.check:
            info = check_backup(args.backup)
            _validate(info)
            print("Копия цела, из неё можно восстановить:")
            _print_info(info)
            return 0
        result = restore_backup(args.backup, args.dest or default_target(), force=args.force)
    except RestoreError as e:
        print(f"Ошибка: {e}")
        return 1

    print(f"Готово: база восстановлена в {result['dest']}")
    _print_info(result)
    for aside in result["moved_aside"]:
        print(f"  прежняя база сохранена: {aside}")
    print("Дальше: запусти бота и проверь /health (см. RESTORE.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
