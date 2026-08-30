import ast, builtins, os, sys

root = '.'
exclude_dirs = ('aiwork', 'shop_final', '.git', 'backups', '_legacy', 'tests', '__pycache__')
scope_files = []

# Only audit the files explicitly in scope per the task
scope_list = [
 'main.py','config.py',
 'keyboards.py','adam_messages.py','multi_agent.py','coach.py',
 'streak_scheduler.py','subscription_scheduler.py','habit_intents.py',
 'morning_ping.py','goal_feedback.py','onboarding_auto.py','query_router.py',
 'reminders.py','shop.py','alerts.py',
]

for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in exclude_dirs and not d.startswith('.')]
    if dirpath in ('.',):
        continue
    rel = os.path.relpath(dirpath, root)
    if rel.split(os.sep)[0] in ('db','handlers','middlewares','webapp'):
        for f in filenames:
            if f.endswith('.py'):
                scope_files.append(os.path.join(dirpath, f))

for f in scope_list:
    if os.path.exists(f):
        scope_files.append(f)

builtin_names = set(dir(builtins))
builtin_names.update(['self','cls','__name__','__file__','__doc__','True','False','None'])

report = {}

for fpath in scope_files:
    try:
        src = open(fpath, encoding='utf-8').read()
    except Exception as e:
        report[fpath] = f'READ ERROR: {e}'
        continue
    try:
        tree = ast.parse(src, filename=fpath)
    except SyntaxError as e:
        report[fpath] = f'SYNTAX ERROR: {e}'
        continue

    bound = set()
    used = []  # (name, lineno)

    class BindCollector(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                bound.add(name)
        def visit_ImportFrom(self, node):
            for alias in node.names:
                name = alias.asname or alias.name
                bound.add(name)
        def visit_FunctionDef(self, node):
            bound.add(node.name)
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                bound.add(a.arg)
            if node.args.vararg: bound.add(node.args.vararg.arg)
            if node.args.kwarg: bound.add(node.args.kwarg.arg)
            self.generic_visit(node)
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_ClassDef(self, node):
            bound.add(node.name)
            self.generic_visit(node)
        def visit_Lambda(self, node):
            for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                bound.add(a.arg)
            if node.args.vararg: bound.add(node.args.vararg.arg)
            if node.args.kwarg: bound.add(node.args.kwarg.arg)
            self.generic_visit(node)
        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            else:
                used.append((node.id, node.lineno))
            self.generic_visit(node)
        def visit_ExceptHandler(self, node):
            if node.name:
                bound.add(node.name)
            self.generic_visit(node)
        def visit_Global(self, node):
            for n in node.names: bound.add(n)
        def visit_Nonlocal(self, node):
            for n in node.names: bound.add(n)
        def visit_With(self, node):
            self.generic_visit(node)
        def visit_comprehension(self, node):
            self.generic_visit(node)

    BindCollector().visit(tree)

    missing = []
    for name, lineno in used:
        if name in bound: continue
        if name in builtin_names: continue
        missing.append((name, lineno))

    if missing:
        report[fpath] = missing

for fpath, res in report.items():
    if isinstance(res, str):
        print(f'{fpath}: {res}')
    else:
        names = sorted(set(n for n,_ in res))
        print(f'{fpath}: UNBOUND NAMES USED: {names}')
        # print first few line numbers per name
        from collections import defaultdict
        d = defaultdict(list)
        for n, ln in res:
            d[n].append(ln)
        for n in names:
            print(f'    {n}: lines {d[n][:10]}')
