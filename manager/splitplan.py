# -*- coding: utf-8 -*-
"""大きな提出をタブごとの提出に分ける案を作る (UI 非依存・API 不使用).

方針は manager/docs/decisions.md「大きな提出をタブごとに分けて出す」。

- 分ける単位はタブ。タブのフォルダ (routes.py を持つサブパッケージ) と、
  その見出し `templates/_tab_<x>.html`・画面の `static/<x>.js|.css` を 1 本にする
- タブのフォルダ以外の新しいフォルダ (単独の HTML ツールなど) は、まとめて 1 本
- それ以外 (本体直下のファイルの修正・既存の共通部分) はまとめて 1 本
- 呼び出し・import の関係があるタブどうしは 1 本にまとめる
- タブが「この提出で変えた共通部分」を使っているときは、その共通部分の
  変更をタブの提出にも同じ内容で入れる (複製)。同じ変更どうしは取り込みで
  ぶつからないので、承認の順番に縛りができない。使っていなければ入れない
  (計算結果を変える共通部分の修正が、新しいタブの提出に混ざらないように)

判定はファイル単位。1 つのファイルの中の変更を行ごとに分けることはしない。
"""
import ast
import os
import posixpath
import re
import subprocess

from . import tabconv
from .gitcli import _popen_kwargs

# tabreg.NOT_TABS と同じ (routes.py があってもタブとしては読まないフォルダ)
NOT_TABS = frozenset({'manager', 'tests', 'docs', 'data', 'static',
                      'templates', 'readme'})
_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
_TAB_TEMPLATE = re.compile(r'^templates/_tab_(\w+)\.html$')
_TEXT_EXT = {'.py', '.js', '.html', '.css', '.json', '.txt', '.md', '.csv',
             '.yml', '.yaml', '.cfg', '.ini', '.toml'}

COMMON = 'common'
TOOLS = 'tools'


def _git(workrepo, args):
    proc = subprocess.run(['git'] + args, cwd=workrepo, capture_output=True,
                          **_popen_kwargs())
    return proc.stdout if proc.returncode == 0 else None


class _Files:
    """基点と提出 (ZIP の展開先) のファイルを読む窓口."""

    def __init__(self, workrepo, base_commit, extract_dir, changes):
        self.workrepo = workrepo
        self.base = base_commit
        self.extract_dir = extract_dir
        self.added = set(changes['added'])
        self.modified = set(changes['modified'])
        self.deleted = set(changes['deleted'])
        self.changed = self.added | self.modified
        out = _git(workrepo, ['ls-tree', '-r', '--name-only', base_commit])
        self.base_tree = set((out or b'').decode('utf-8', 'replace')
                             .splitlines())
        self._new_cache = {}
        self._base_cache = {}

    def exists_new(self, rel):
        if rel in self.deleted:
            return False
        return (rel in self.changed or rel in self.base_tree
                or os.path.isfile(self._path(rel)))

    def _path(self, rel):
        return os.path.join(self.extract_dir, rel.replace('/', os.sep))

    def new_text(self, rel):
        """提出の中身 (変えていないファイルは基点の中身)."""
        if rel not in self._new_cache:
            text = None
            if rel in self.changed or os.path.isfile(self._path(rel)):
                try:
                    with open(self._path(rel), 'rb') as f:
                        text = f.read().decode('utf-8', 'replace')
                except OSError:
                    text = None
            if text is None and rel not in self.deleted:
                text = self.base_text(rel)
            self._new_cache[rel] = text
        return self._new_cache[rel]

    def base_text(self, rel):
        if rel not in self._base_cache:
            data = None
            if rel in self.base_tree:
                data = _git(self.workrepo,
                            ['cat-file', 'blob', '%s:%s' % (self.base, rel)])
            self._base_cache[rel] = (None if data is None
                                     else data.decode('utf-8', 'replace'))
        return self._base_cache[rel]


# ---------------------------------------------------------------------------
# 持ち主 (どの単位に属するファイルか)
# ---------------------------------------------------------------------------

def _tab_folders(files):
    """routes.py を持つフォルダ (基点か提出のどちらかにあればタブ)."""
    names = set()
    for rel in files.base_tree | files.changed:
        parts = rel.split('/')
        if (len(parts) == 2 and parts[1] == 'routes.py'
                and parts[0] not in NOT_TABS and _NAME.match(parts[0])
                and files.exists_new(rel)):
            names.add(parts[0])
    return names


def _owner(rel, tabs, files):
    """'tab:<x>' / 'tools:<x>' / COMMON."""
    parts = rel.split('/')
    top = parts[0]
    if len(parts) > 1 and top not in NOT_TABS:
        if top in tabs:
            return 'tab:' + top
        # 基点に無い新しいフォルダ (タブではない単独のツールなど)
        if _NAME.match(top) and not any(
                p.startswith(top + '/') for p in files.base_tree):
            return 'tools:' + top
        return COMMON
    m = _TAB_TEMPLATE.match(rel)
    if m and m.group(1) in tabs:
        return 'tab:' + m.group(1)
    if len(parts) >= 2 and top in ('static', 'templates'):
        if len(parts) > 2 and parts[1] in tabs:
            return 'tab:' + parts[1]           # static/<x>/... など
        stem, ext = posixpath.splitext(parts[1])
        if len(parts) == 2 and top == 'static' and stem in tabs \
                and ext in ('.js', '.css'):
            return 'tab:' + stem
    return COMMON


# ---------------------------------------------------------------------------
# Python の import
# ---------------------------------------------------------------------------

def _top_names(text):
    """モジュールの一番外側で定義された名前 (if / try の中も含む)."""
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return None
    names = set()

    def visit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign,
                                   ast.AugAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            names.add(n.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    names.add(a.asname or a.name.split('.')[0])
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                for field in ('body', 'orelse', 'finalbody'):
                    visit(getattr(node, field, None) or [])
                for h in getattr(node, 'handlers', None) or []:
                    visit(h.body)
    visit(tree.body)
    return names


def _module_file(parts, files):
    """['a', 'b'] → 'a/b.py' か 'a/b/__init__.py' (無ければ None)."""
    if not parts:
        return None
    stem = '/'.join(parts)
    for rel in (stem + '.py', stem + '/__init__.py'):
        if files.exists_new(rel):
            return rel
    return None


def _attrs_of(tree, alias):
    """`alias.xxx` の xxx を集める (alias をそのまま渡す使い方は None)."""
    attrs = set()
    bare = False
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == alias:
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                attrs.add(parent.attr)
            elif not isinstance(parent, (ast.Import, ast.ImportFrom)):
                bare = True
    return None if bare else attrs


def _py_deps(rel, files):
    """rel が読み込む本体のモジュール: [(ファイル, 使う名前 or None)].

    使う名前が None のときは「何を使うか読み取れない」(全体を使うとみなす)。
    """
    text = files.new_text(rel)
    try:
        tree = ast.parse(text or '')
    except (SyntaxError, ValueError):
        return []
    pkg = rel.split('/')[:-1]
    if rel.endswith('/__init__.py'):
        pkg = rel.split('/')[:-1]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                parts = a.name.split('.')
                if parts[0] != 'mgtkit' or len(parts) < 2:
                    continue
                target = _module_file(parts[1:], files)
                if not target:
                    continue
                if a.asname:
                    out.append((target, _attrs_of(tree, a.asname)))
                else:
                    out.append((target, None))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                up = node.level - 1
                if up > len(pkg):
                    continue
                parts = pkg[:len(pkg) - up] + (node.module or '').split('.')
                parts = [p for p in parts if p]
            else:
                mod = (node.module or '').split('.')
                if mod[0] != 'mgtkit':
                    continue
                parts = mod[1:]
            names = []
            for a in node.names:
                sub = _module_file(parts + [a.name], files)
                if sub:
                    # from pkg import module
                    out.append((sub, _attrs_of(tree, a.asname or a.name)))
                else:
                    names.append(a.name)
            if names:
                target = _module_file(parts, files)
                if target:
                    out.append((target, None if '*' in names
                                else set(names)))
    return out


def _needs_change(target, names, files):
    """target の「この提出での変更」が無いと動かない使い方か."""
    if target in files.added:
        return True
    if target not in files.modified:
        return False
    if names is None:
        return True
    # モジュールに最初からある名前 (__file__ など) は変更と関係ない
    names = {n for n in names if not (n.startswith('__') and n.endswith('__'))}
    base = _top_names(files.base_text(target))
    if base is None:
        return True
    return bool(set(names) - base)


# ---------------------------------------------------------------------------
# 画面 (JS) と、名前で参照されるファイル
# ---------------------------------------------------------------------------

def _new_js_names(rel, files):
    new = tabconv._top_level_names(files.new_text(rel) or '')
    base = tabconv._top_level_names(files.base_text(rel) or '')
    return new - base


def _mentions(text, word):
    return re.search(r'(?<![\w$])%s(?![\w$])' % re.escape(word),
                     text or '') is not None


# ---------------------------------------------------------------------------
# 案を作る
# ---------------------------------------------------------------------------

def _tab_label(x, files):
    text = files.new_text('templates/_tab_%s.html' % x) or ''
    first = text.split('\n', 1)[0]
    attrs = tabconv._read_tag(first) or {}
    return attrs.get('label') or x


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 共通部分を代表にする (まとめた先が「共通」になるように)
            if rb == COMMON:
                ra, rb = rb, ra
            self.parent[rb] = ra


def plan(workrepo, base_commit, extract_dir, changes):
    """分け方の案.

    戻り値: dict(units=[...], splittable=bool)
    units の各要素: dict(key, kind ('tab' / 'tools' / 'common'), label,
    tabs (タブの名前のリスト), files (追加・変更したファイル),
    deleted (削除の候補), shared (ほかの単位と同じ内容で入れるファイル))
    splittable は 2 本以上に分けられるとき True。
    """
    files = _Files(workrepo, base_commit, extract_dir, changes)
    tabs = _tab_folders(files)
    every = sorted(files.changed | files.deleted)
    owner = {rel: _owner(rel, tabs, files) for rel in every}
    groups = _Union()
    for o in owner.values():
        groups.find(o)

    def owner_of_module(rel):
        return owner.get(rel) or _owner(rel, tabs, files)

    # 1. タブどうし (タブと共通部分) の import の関係でまとめる
    for rel in sorted(files.changed):
        if not rel.endswith('.py'):
            continue
        for target, names in _py_deps(rel, files):
            o_t = owner_of_module(target)
            if (o_t != COMMON and o_t != owner[rel]
                    and _needs_change(target, names, files)):
                groups.union(owner[rel], o_t)

    # 2. 各単位が必要とする共通部分の変更 (複製する分) を集める
    common_files = [r for r in sorted(files.changed)
                    if groups.find(owner[r]) == COMMON]
    js_new = {r: _new_js_names(r, files) for r in common_files
              if r.endswith('.js')}

    def needs_of(unit_files):
        """unit_files が動くのに要る、共通部分の変更 (ファイルの集合)."""
        need = set()
        todo = [r for r in unit_files if r.endswith('.py')]
        seen = set(todo)
        while todo:
            rel = todo.pop()
            for target, names in _py_deps(rel, files):
                if target in unit_files or target not in common_files:
                    continue
                if _needs_change(target, names, files):
                    if target not in need:
                        need.add(target)
                        if target not in seen:
                            seen.add(target)
                            todo.append(target)
        texts = [files.new_text(r) or '' for r in unit_files
                 if posixpath.splitext(r)[1] in _TEXT_EXT]
        for r, names in js_new.items():
            if r in unit_files:
                continue
            if any(_mentions(t, n) for n in names for t in texts):
                need.add(r)
        for r in common_files:
            if r in unit_files or r.endswith(('.py', '.js')):
                continue
            base = posixpath.basename(r)
            if r in tabconv.HUB_FILES:
                continue        # タブは自動で登録されるので要らない
            if r == 'requirements.txt':
                if any(u.endswith('.py') for u in unit_files):
                    need.add(r)
            elif any(base in t for t in texts):
                need.add(r)
        return need

    comps = {}
    for rel in every:
        comps.setdefault(groups.find(owner[rel]), []).append(rel)

    units = []
    tools_files = []
    for key, rels in sorted(comps.items()):
        if key == COMMON:
            continue
        members = sorted({owner[r] for r in rels})
        if all(m.startswith('tools:') for m in members):
            tools_files.extend(rels)
            continue
        tab_names = sorted(m[4:] for m in members if m.startswith('tab:'))
        units.append(_make_unit(key, 'tab', tab_names, rels, files))
    if tools_files:
        folders = sorted({r.split('/')[0] for r in tools_files})
        units.append(_make_unit(TOOLS, 'tools', [], tools_files, files,
                                folders=folders))

    shared_need = {}
    for u in units:
        u['shared'] = sorted(needs_of(set(u['files'])))
        for r in u['shared']:
            shared_need.setdefault(r, set()).add(u['key'])

    # 3. 共通部分: 新しく足したファイルのうち、タブの提出だけが使うものは
    #    共通部分からは外す (タブの提出で入る)
    rest = [r for r in (comps.get(COMMON) or [])]
    rest_set = set(rest)
    keep_common = set(rest)
    common_need = needs_of(rest_set - {r for r in rest_set
                                       if r in shared_need
                                       and r in files.added})
    for r in rest:
        if (r in files.added and r in shared_need
                and r not in common_need):
            keep_common.discard(r)
    common_unit = None
    common_rels = [r for r in rest if r in keep_common]
    if any(r in files.changed for r in common_rels) or any(
            r in files.deleted for r in common_rels):
        common_unit = _make_unit(COMMON, 'common', [], common_rels, files)
        common_unit['shared'] = []

    # 中身が削除だけの単位は単独で出さない (共通部分へ寄せる)
    for u in [u for u in units if not u['files']]:
        units.remove(u)
        if common_unit is None:
            common_unit = _make_unit(COMMON, 'common', [], [], files)
        common_unit['deleted'] = sorted(set(common_unit['deleted'])
                                        | set(u['deleted']))
    ordered = []
    if common_unit:
        ordered.append(common_unit)
    ordered += sorted([u for u in units if u['kind'] == 'tab'],
                      key=lambda u: u['label'])
    ordered += [u for u in units if u['kind'] == 'tools']
    return {'units': ordered,
            'splittable': len([u for u in ordered if u['files']]) >= 2}


def _make_unit(key, kind, tab_names, rels, files, folders=()):
    changed = sorted(r for r in rels if r in files.changed)
    deleted = sorted(r for r in rels if r in files.deleted)
    if kind == 'tab':
        labels = [_tab_label(x, files) for x in tab_names]
        new = all(not any(p.startswith(x + '/') for p in files.base_tree)
                  for x in tab_names)
        label = ('新しいタブ' if new else 'タブ') + ''.join(
            '「%s」' % lab for lab in labels)
        if not new:
            label += 'の改良'
    elif kind == 'tools':
        label = '単独のツール (フォルダ %s)' % '・'.join(folders)
    else:
        label = '既存の機能の修正・共通部分'
    return {'key': key, 'kind': kind, 'label': label,
            'tabs': list(tab_names), 'files': changed, 'deleted': deleted,
            'shared': []}


def unit_files(unit):
    """この単位の提出に入れるファイル (追加・変更 + 複製する共通部分)."""
    return sorted(set(unit['files']) | set(unit.get('shared') or []))
