"""
Карта кода для Obsidian: файлы проекта и их функции → заметки со ссылками [[...]], которые Obsidian рисует графом.

    python tools/make_code_vault.py [папка_хранилища]      (по умолчанию ../ai_habit_vault рядом с проектом)

modules/   — по заметке на каждый .py (ссылки: что импортирует, какие функции в нём есть);
functions/ — по заметке на каждую функцию и метод (ссылки: где определена, кого вызывает).
Граф открывается с поиском `path:modules` — видны только файлы и их импорты; очисти поиск в настройках графа, чтобы увидеть и функции.
Вызовы ищутся по именам из импортов и по `self.`, поэтому динамические вызовы (через словари, getattr, колбэки) на карте не появятся.
Папки modules/ и functions/ при каждом запуске пересоздаются целиком; .obsidian/graph.json пишется только если его ещё нет.
"""
import ast
import json
import os
import shutil
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {"tests", "_legacy", "aiwork", "shop_final", ".claude", "__pycache__", "logs", "assets", "deploy", "node_modules", ".git"}
COLORS = {"root": 0xE8C06A, "db": 0x4C8BF5, "handlers": 0x3DBE7A, "webapp": 0xA06CF0, "middlewares": 0xE07A5F, "backups": 0xE07A5F, "tools": 0xE07A5F}


def scan():
    """{dotted_name: (путь, ast-дерево)} для всех .py проекта."""
    mods = {}
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
                dotted = rel[:-9] if rel.endswith(".__init__") else rel
                with open(path, encoding="utf-8") as handle:
                    mods[dotted] = (os.path.relpath(path, ROOT), ast.parse(handle.read()))
    return mods


def functions_of(tree, mod):
    """{id: (узел, имя_класса_или_None)} для функций верхнего уровня и методов."""
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[f"{mod}.{node.name}"] = (node, None)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{mod}.{node.name}.{sub.name}"] = (sub, node.name)
    return found


def imports_of(tree, mod, path, mods):
    """(модули, имена): модули — {псевдоним: модуль проекта}, имена — {имя: (модуль, имя)}; relative-импорты считаются от пакета файла."""
    package = mod if path.endswith("__init__.py") else mod.rpartition(".")[0]
    aliases, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name in mods:
                    aliases[item.asname or item.name] = item.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([base] if base else []))
            for item in node.names:
                local = item.asname or item.name
                if f"{base}.{item.name}" in mods:
                    aliases[local] = f"{base}.{item.name}"
                elif base in mods:
                    names[local] = (base, item.name)
    return aliases, names


def resolve(target, funcs, exports, depth=0):
    """(модуль, имя) → id функции; имена, которые модуль лишь переэкспортирует (db/__init__.py), прослеживаем до 3 шагов."""
    mod, name = target
    if f"{mod}.{name}" in funcs:
        return f"{mod}.{name}"
    if depth < 3 and name in exports.get(mod, {}):
        return resolve(exports[mod][name], funcs, exports, depth + 1)
    return None


def calls_of(node, cls, mod, aliases, names, funcs, exports):
    out = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        f, target = call.func, None
        if isinstance(f, ast.Name):
            target = f"{mod}.{f.id}" if f"{mod}.{f.id}" in funcs else resolve(names[f.id], funcs, exports) if f.id in names else None
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            if f.value.id in ("self", "cls") and cls:
                target = f"{mod}.{cls}.{f.attr}" if f"{mod}.{cls}.{f.attr}" in funcs else None
            elif f.value.id in aliases:
                target = f"{aliases[f.value.id]}.{f.attr}" if f"{aliases[f.value.id]}.{f.attr}" in funcs else None
        if target:
            out.add(target)
    return out


def doc_line(node):
    doc = ast.get_docstring(node)
    return doc.strip().splitlines()[0] if doc else ""


def group(path):
    top = path.split(os.sep)[0]
    return top if top in COLORS else "root"


def write(vault, folder, name, text):
    with open(os.path.join(vault, folder, name + ".md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(vault):
    mods = scan()
    all_funcs = {}
    for mod, (path, tree) in mods.items():
        for fid, item in functions_of(tree, mod).items():
            all_funcs[fid] = (mod, item)
    exports = {mod: imports_of(tree, mod, path, mods)[1] for mod, (path, tree) in mods.items()}
    for folder in ("modules", "functions"):
        shutil.rmtree(os.path.join(vault, folder), ignore_errors=True)
        os.makedirs(os.path.join(vault, folder))
    os.makedirs(os.path.join(vault, ".obsidian"), exist_ok=True)

    hubs, edges, links = Counter(), 0, []
    for mod, (path, tree) in mods.items():
        aliases, names = imports_of(tree, mod, path, mods)
        deps = sorted(({m for m in aliases.values()} | {m for m, _ in names.values()}) - {mod})
        hubs.update(deps)
        mine = sorted(fid for fid, (m, _) in all_funcs.items() if m == mod)
        tag = group(path)
        body = f"---\ntags: [module, {tag}]\npath: {path.replace(os.sep, '/')}\n---\n# {mod}\n{doc_line(tree)}\n\n## Импортирует\n"
        body += "".join(f"- [[{d}]]\n" for d in deps) or "- ничего из проекта\n"
        body += "\n## Функции\n" + ("".join(f"- [[{fid}]]\n" for fid in mine) or "- нет\n")
        write(vault, "modules", mod, body)
        links += deps + mine
        for fid in mine:
            node, cls = all_funcs[fid][1]
            called = sorted(calls_of(node, cls, mod, aliases, names, all_funcs, exports) - {fid})
            edges += len(called)
            text = f"---\ntags: [function, {tag}]\n---\n# {fid.rpartition('.')[2]}\nОпределена в [[{mod}]], строка {node.lineno}\n{doc_line(node)}\n\n## Вызывает\n"
            write(vault, "functions", fid, text + ("".join(f"- [[{c}]]\n" for c in called) or "- ничего из проекта\n"))
            links += called

    top = "".join(f"- [[{m}]] — импортируют {n} файлов\n" for m, n in hubs.most_common(10))
    write(vault, "", "README", f"# Карта кода ADAM\n{len(mods)} файлов, {len(all_funcs)} функций, {edges} связей «функция → функция».\n\n"
          f"Граф открывается с поиском `path:modules`: только файлы и импорты. Очисти поиск в настройках графа — появятся и функции; "
          f"у заметки функции открой «Локальный граф», чтобы увидеть, кого она вызывает.\n\n## Самые подключаемые файлы\n{top}")
    known = {os.path.splitext(n)[0] for f in ("modules", "functions") for n in os.listdir(os.path.join(vault, f))}
    broken = sorted({link for link in links if link not in known})
    config = os.path.join(vault, ".obsidian", "graph.json")
    if not os.path.exists(config):
        groups = [{"query": f"tag:#{name}", "color": {"a": 1, "rgb": rgb}} for name, rgb in COLORS.items()]
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"search": "path:modules", "showArrow": True, "colorGroups": groups}, handle, ensure_ascii=False, indent=2)
    print(f"{len(mods)} файлов, {len(all_funcs)} функций, {edges} вызовов; битых ссылок: {len(broken)} {broken[:5]}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main(os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "ai_habit_vault")))
