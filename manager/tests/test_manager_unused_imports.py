# -*- coding: utf-8 -*-
"""manager/ に使っていない import が残っていないかの静的検査.

使わなくなった import は、読む人に「まだこの機能を使っている」と誤解
させる (submit.py には、提出の絞り込みをやめたあとの fnmatch と、
保存を safeio に移したあとの json が残っていた)。掃除しても歯止めが
無ければまた溜まるので、検査で止める。

ルート本体 (mgtkit) は対象にしない。``__init__.py`` が意図的に再
エクスポートしており (`from .util import ...`)、同じ基準では測れない。
アプリマネージャーは manager/ に完結させる約束なので、ここだけ見る。

新しく引っかかったら、まず本当に使っていないかを確かめて消すこと。
再エクスポートなど意図があるものは ``# noqa`` を付ける。
"""
import ast
import os

MANAGER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py_files():
    """manager/ 直下と tests/ の .py (__init__.py は再エクスポート用途)."""
    for folder in (MANAGER_DIR, os.path.join(MANAGER_DIR, 'tests')):
        for name in sorted(os.listdir(folder)):
            if name.endswith('.py') and name != '__init__.py':
                yield os.path.relpath(os.path.join(folder, name),
                                      MANAGER_DIR)


def _bound_names(tree):
    """import が束縛する名前と、その行 (as 名があればそちら)."""
    bound = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import a.b は 'a' を束縛する
                name = alias.asname or alias.name.split('.')[0]
                bound.append((name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == '*':
                    continue            # 何を束縛するか静的には分からない
                bound.append((alias.asname or alias.name, node.lineno))
    return bound


def _used_names(tree):
    """import 以外で名前として現れるもの (属性アクセスの土台を含む)."""
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                used.add(base.id)
    # 文字列の型注釈 ("List[int]" 等) は使っていない扱いにしない
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.update(w for w in node.value.replace('[', ' ')
                        .replace(']', ' ').replace(',', ' ').split())
    return used


def _unused(path_rel):
    full = os.path.join(MANAGER_DIR, path_rel)
    with open(full, encoding='utf-8') as f:
        source = f.read()
    lines = source.split('\n')
    tree = ast.parse(source, full)
    used = _used_names(tree)
    found = []
    for name, lineno in _bound_names(tree):
        if name in used:
            continue
        if 'noqa' in lines[lineno - 1]:
            continue                    # 意図がある (理由はコードの側に)
        if '__all__' in source and name in source.split('__all__')[1][:400]:
            continue                    # 再エクスポート
        found.append((path_rel, lineno, name))
    return found


def test_no_unused_imports_in_manager():
    offenders = []
    for path_rel in _py_files():
        offenders += _unused(path_rel)
    assert offenders == [], (
        '使っていない import があります (消すか、意図があれば # noqa を'
        '付けてください):\n'
        + '\n'.join('  %s:%d %s' % o for o in offenders))


def test_the_check_actually_catches_one(tmp_path):
    """検査そのものが機能していること (素通りする検査を残さない)."""
    sample = tmp_path / 'sample.py'
    sample.write_text('import os\nimport sys\n\nprint(sys.argv)\n',
                      encoding='utf-8')
    tree = ast.parse(sample.read_text(encoding='utf-8'))
    used = _used_names(tree)
    unused = [n for n, _ in _bound_names(tree) if n not in used]
    assert unused == ['os']
