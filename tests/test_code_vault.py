"""Карта кода для Obsidian (tools/make_code_vault.py): каждая ссылка [[...]] ведёт на существующую заметку, у файла есть его функции, вызов через переэкспорт находится."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import make_code_vault  # noqa: E402


def test_the_vault_has_no_broken_links_and_ties_files_to_their_functions(tmp_path):
    assert make_code_vault.main(str(tmp_path)) == 0, "есть ссылки на несуществующие заметки"
    module = (tmp_path / "modules" / "bot_style.md").read_text(encoding="utf-8")
    assert "[[bot_style.send_banner]]" in module and "[[db.style_mode]]" in module
    function = (tmp_path / "functions" / "bot_style.active.md").read_text(encoding="utf-8")
    assert "[[db.style_mode.bot_active]]" in function and "[[bot_style]]" in function
    assert json.loads((tmp_path / ".obsidian" / "graph.json").read_text(encoding="utf-8"))["search"] == "path:modules"
    assert (tmp_path / "README.md").exists() and not (tmp_path / "modules" / "README.md").exists()
