import re, os

root = '.'
exclude = ('aiwork', 'shop_final', '.git', 'backups', '_legacy')
scoped_files = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith('.')]
    for f in filenames:
        if f.endswith('.py') and not f.startswith('scratch_'):
            scoped_files.append(os.path.join(dirpath, f))

names = set()
per_file = {}
for f in scoped_files:
    try:
        src = open(f, encoding='utf-8').read()
    except Exception as e:
        continue
    for m in re.finditer(r'from db import\s*\(([^)]*)\)', src, re.S):
        block = m.group(1)
        items = [x.strip() for x in block.replace('\n',' ').split(',') if x.strip()]
        items = [x.split(' as ')[0].strip() for x in items if x.strip()]
        per_file.setdefault(f, set()).update(items)
        names.update(items)
    for m in re.finditer(r'^from db import ([^\n(][^\n]*)$', src, re.M):
        block = m.group(1)
        if '(' in block: continue
        items = [x.strip() for x in block.split(',') if x.strip()]
        items = [x.split(' as ')[0].strip() for x in items if x.strip()]
        per_file.setdefault(f, set()).update(items)
        names.update(items)

print('TOTAL UNIQUE NAMES IMPORTED FROM db ACROSS SCOPED FILES:', len(names))
with open('scratch_all_db_import_names.txt','w', encoding='utf-8') as out:
    for n in sorted(names):
        out.write(n+'\n')

with open('scratch_per_file_db_imports.txt', 'w', encoding='utf-8') as out:
    for f in sorted(per_file):
        out.write(f + ': ' + ', '.join(sorted(per_file[f])) + '\n')
