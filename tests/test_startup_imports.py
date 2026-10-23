"""
Старт бота (24.10.2026, авария): чистка «неиспользуемых» импортов убрала из handlers/ai.py имя build_user_context, а его берёт
handlers/progress.py — `python main.py` падал с ImportError, прод отдавал 502, хотя все прежние тесты были зелёными (они не запускают main).
Этот тест запускает настоящий запуск приложения в отдельном процессе: импорт main и каждого рабочего модуля.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("_legacy", "aiwork", "shop_final", ".claude", "tests", "tools", "deploy", "venv", ".venv", "__pycache__", "backups")


def _env(volume):
    env = dict(os.environ)
    env.update({"BOT_TOKEN": "123456:TEST_TOKEN_NOT_REAL", "RAILWAY_VOLUME_MOUNT_PATH": volume, "PYTHONUTF8": "1"})
    return env


def test_main_imports_the_way_it_does_on_railway():
    with tempfile.TemporaryDirectory(prefix="adam_import_main_") as volume:
        result = subprocess.run([sys.executable, "-c", "import main"], cwd=ROOT, env=_env(volume), capture_output=True, text=True,
                                encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr[-1500:]
    assert "ImportError" not in result.stderr and "NameError" not in result.stderr


def test_every_live_module_imports():
    modules = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in SKIP_DIRS or any(part in SKIP_DIRS for part in rel.parts) or rel.name == "main.py":
            continue
        name = ".".join(rel.with_suffix("").parts)
        modules.append(name[:-9] if name.endswith(".__init__") else name)
    script = (
        "import importlib, sys\n"
        f"bad = []\nfor name in {modules!r}:\n"
        "    try:\n        importlib.import_module(name)\n"
        "    except Exception as exc:\n        bad.append(name + ': ' + type(exc).__name__ + ': ' + str(exc)[:160])\n"
        "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n"
    )
    with tempfile.TemporaryDirectory(prefix="adam_import_all_") as volume:
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=_env(volume), capture_output=True, text=True,
                                encoding="utf-8", timeout=240)
    assert result.returncode == 0, "не импортируются:\n" + result.stdout[-1500:] + result.stderr[-500:]
    assert len(modules) > 100, "список модулей собрался"
