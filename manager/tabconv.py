# -*- coding: utf-8 -*-
"""古い書き方のタブ追加を、新しい書き方に直してから提出する (UI 非依存).

本体はタブのつなぎ方を「各タブのファイル側に書く」方式に変えた
(本体の CLAUDE.md「タブの足し方」、tabreg.py、static/app.js 冒頭)。
それより前の書き方 (= 古い書き方) でタブを足した提出は、つなぎ込みの
3 ファイル (app.py / templates/index.html / static/app.js) の同じ行を
最新版と取り合ってぶつかる。そこで提出の差分のうち、この 3 ファイルへの
変更が機械的に読み取れる「登録の追記」だけなら、それを送らずに次へ直す:

=====================================  =====================================
古い書き方 (3 ファイルへの追記)          直した先 (そのタブのファイル)
=====================================  =====================================
app.py の import と register_blueprint  送らない (起動時の自動登録に任せる)
index.html の nav のボタン・include      templates/_tab_<x>.html の先頭行に見出し
                                        (order は nav に差し込まれた位置の
                                        前後のタブの order の間の値)
index.html の出力フォルダ案内文の名前    見出しの out="..."
app.js の SYNC_GROUPS などの id の一覧  その入力欄に data-sync / data-persist
app.js の loadMgt / clearCaseTable に    そのタブの JS で mgtkit:mgt-loaded /
足したタブ固有の処理                    mgtkit:stress-changed を受け取って実行
=====================================  =====================================

読み取れない変更が 1 つでも混じるときは何も直さず、今までどおり
そのまま提出する (呼び出し側が画面の警告とログで知らせる。黙って捨てない)。
API (Claude) は使わない。判断の記録は manager/docs/decisions.md
「古い書き方のタブ追加を提出時に直す」。
"""
import difflib
import html
import logging
import math
import os
import re
import subprocess

from .gitcli import GitError, _popen_kwargs, run_git

log = logging.getLogger(__name__)

HUB_APP = 'app.py'
HUB_INDEX = 'templates/index.html'
HUB_JS = 'static/app.js'
HUB_FILES = (HUB_APP, HUB_INDEX, HUB_JS)

# tabreg.NOT_TABS と同じ (routes.py があってもタブとしては読まないフォルダ)
_NOT_TABS = frozenset({'manager', 'tests', 'docs', 'data', 'static',
                       'templates', 'readme'})

# 利用者向けの文言 (Git 用語を出さない)
_KEPT_MESSAGE = (
    '画面のタブの登録が古い書き方 (app.py / index.html / app.js への追記) '
    'のままで、自動では新しい書き方に直せない変更が含まれているため、'
    'そのまま提出します。最新版とぶつかって、承認の前に手直しが要ることが'
    'あります (直せなかった箇所: %s)。')
_REPORT_HEAD = '# マネージャーが直した箇所 (古い書き方のタブの登録)'


class _Unclassified(Exception):
    """機械的に読み取れない変更 (where = 利用者に見せる場所の説明)."""

    def __init__(self, where, detail):
        super().__init__('%s: %s' % (where, detail))
        self.where = where
        self.detail = detail


# ---------------------------------------------------------------------------
# 読み書きの下回り
# ---------------------------------------------------------------------------

def _git_bytes(workrepo, ref, rel):
    """ref:rel の中身をバイト列で (無ければ None)。改行もそのまま."""
    proc = subprocess.run(['git', 'cat-file', 'blob', '%s:%s' % (ref, rel)],
                          cwd=workrepo, capture_output=True,
                          **_popen_kwargs())
    return proc.stdout if proc.returncode == 0 else None


def _git_text(workrepo, ref, rel):
    data = _git_bytes(workrepo, ref, rel)
    return None if data is None else _decode(data)[0]


def _decode(data):
    """バイト列 → (改行を LF にそろえた文字列, 書き戻し用の情報)."""
    bom = data.startswith(b'\xef\xbb\xbf')
    text = data[3:].decode('utf-8') if bom else data.decode('utf-8')
    crlf = '\r\n' in text
    return text.replace('\r\n', '\n'), {'bom': bom, 'crlf': crlf}


def _encode(text, fmt):
    if fmt.get('crlf'):
        text = text.replace('\n', '\r\n')
    data = text.encode('utf-8')
    return (b'\xef\xbb\xbf' + data) if fmt.get('bom') else data


class _Tree:
    """提出された ZIP の展開先 (読み取りと、書き換え予定の保持)."""

    def __init__(self, root):
        self.root = root
        self.pending = {}      # rel -> (text, fmt)

    def path(self, rel):
        return os.path.join(self.root, rel.replace('/', os.sep))

    def exists(self, rel):
        return rel in self.pending or os.path.isfile(self.path(rel))

    def read(self, rel):
        if rel in self.pending:
            return self.pending[rel][0]
        with open(self.path(rel), 'rb') as f:
            return _decode(f.read())[0]

    def write(self, rel, text):
        if rel in self.pending:
            self.pending[rel] = (text, self.pending[rel][1])
            return
        with open(self.path(rel), 'rb') as f:
            fmt = _decode(f.read())[1]
        self.pending[rel] = (text, fmt)

    def tab_templates(self):
        d = self.path('templates')
        if not os.path.isdir(d):
            return []
        return sorted('templates/' + n for n in os.listdir(d)
                      if re.fullmatch(r'_tab_\w+\.html', n))


# ---------------------------------------------------------------------------
# JS の字句 (文字列・コメントを読み飛ばして括弧を数えるための最小限)
# ---------------------------------------------------------------------------

_JS_ID = re.compile(r'[A-Za-z_$][\w$]*')
_JS_NUM = re.compile(r'\d[\w.]*')
_JS_PUNCT = re.compile(
    r'=>|===|!==|\?\?=|\?\?|\?\.|\.\.\.|==|!=|<=|>=|&&|\|\||\+\+|--|'
    r'[+\-*/%&|^]=|[{}()\[\];,.<>+\-*/%=!&|^~?:]')
_REGEX_BEFORE_WORDS = {'return', 'typeof', 'case', 'do', 'else', 'in', 'of',
                       'new', 'delete', 'void', 'throw', 'instanceof'}
_OPEN = {'(': ')', '[': ']', '{': '}'}
_CLOSE = {')', ']', '}'}


def _skip_string(src, i):
    q = src[i]
    j = i + 1
    n = len(src)
    while j < n:
        c = src[j]
        if c == '\\':
            j += 2
            continue
        if c == q:
            return j + 1
        if q == '`' and src.startswith('${', j):
            j = _skip_template_expr(src, j + 2)
            continue
        if c == '\n' and q != '`':
            break
        j += 1
    raise ValueError('文字列が閉じていません')


def _skip_template_expr(src, j):
    depth = 1
    n = len(src)
    while j < n:
        c = src[j]
        if c in '\'"`':
            j = _skip_string(src, j)
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError('テンプレート文字列が閉じていません')


def _skip_regex(src, i):
    j = i + 1
    n = len(src)
    in_class = False
    while j < n:
        c = src[j]
        if c == '\\':
            j += 2
            continue
        if c == '\n':
            break
        if c == '[':
            in_class = True
        elif c == ']':
            in_class = False
        elif c == '/' and not in_class:
            j += 1
            while j < n and (src[j].isalnum() or src[j] == '_'):
                j += 1
            return j
        j += 1
    raise ValueError('正規表現が閉じていません')


def _js_tokens(src):
    """[(kind, text, start, end)]。kind は str / com / re / id / num / punc."""
    toks = []
    i = 0
    n = len(src)
    prev = None
    while i < n:
        c = src[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if src.startswith('//', i):
            j = src.find('\n', i)
            j = n if j < 0 else j
            toks.append(('com', src[i:j], i, j))
            i = j
            continue
        if src.startswith('/*', i):
            j = src.find('*/', i + 2)
            if j < 0:
                raise ValueError('コメントが閉じていません')
            toks.append(('com', src[i:j + 2], i, j + 2))
            i = j + 2
            continue
        if c in '\'"`':
            j = _skip_string(src, i)
            tok = ('str', src[i:j], i, j)
        elif c == '/' and (prev is None or (prev[0] == 'punc' and prev[1] not in (')', ']', '}'))
                           or (prev[0] == 'id' and prev[1] in _REGEX_BEFORE_WORDS)):
            j = _skip_regex(src, i)
            tok = ('re', src[i:j], i, j)
        else:
            m = _JS_ID.match(src, i)
            kind = 'id'
            if not m:
                m = _JS_NUM.match(src, i)
                kind = 'num'
            if not m:
                m = _JS_PUNCT.match(src, i)
                kind = 'punc'
            if not m:
                raise ValueError('読めない文字があります: %r' % c)
            tok = (kind, m.group(0), i, m.end())
            j = m.end()
        toks.append(tok)
        prev = tok
        i = j
    return toks


def _match_close(toks, k):
    """toks[k] (開き括弧) に対応する閉じ括弧の位置."""
    stack = []
    for x in range(k, len(toks)):
        kind, t = toks[x][0], toks[x][1]
        if kind != 'punc':
            continue
        if t in _OPEN:
            stack.append(_OPEN[t])
        elif t in _CLOSE:
            if not stack or stack.pop() != t:
                raise ValueError('括弧の対応が取れません')
            if not stack:
                return x
    raise ValueError('括弧が閉じていません')


def _balanced(toks):
    stack = []
    for kind, t, _s, _e in toks:
        if kind != 'punc':
            continue
        if t in _OPEN:
            stack.append(_OPEN[t])
        elif t in _CLOSE:
            if not stack or stack.pop() != t:
                return False
    return not stack


def _str_value(tok):
    return tok[1][1:-1]


def _declared(toks):
    """const / let / var / function / class で宣言された名前と、引数名."""
    names = set()
    for x, tok in enumerate(toks[:-1]):
        if tok[0] == 'id' and tok[1] in ('const', 'let', 'var', 'function',
                                         'class', 'catch'):
            nxt = toks[x + 1]
            if nxt[0] == 'id':
                names.add(nxt[1])
            elif nxt[1] == '(' and tok[1] == 'catch' and x + 2 < len(toks):
                names.add(toks[x + 2][1])
    return names


def _refs(toks):
    """参照している名前 (プロパティ名 a.b の b は除く) と文字列の値."""
    ids, strs = set(), set()
    for x, tok in enumerate(toks):
        if tok[0] == 'id':
            if x and toks[x - 1][1] in ('.', '?.'):
                continue
            ids.add(tok[1])
        elif tok[0] == 'str':
            strs.add(_str_value(tok))
    return ids, strs


def _top_level_names(src):
    """JS ファイルの一番外側で宣言された名前."""
    try:
        toks = _js_tokens(src)
    except ValueError:
        return set()
    names = set()
    depth = 0
    for x, tok in enumerate(toks):
        if tok[0] == 'punc':
            if tok[1] in _OPEN:
                depth += 1
            elif tok[1] in _CLOSE:
                depth -= 1
            continue
        if (depth == 0 and tok[0] == 'id'
                and tok[1] in ('function', 'const', 'let', 'var', 'class')
                and x + 1 < len(toks) and toks[x + 1][0] == 'id'):
            names.add(toks[x + 1][1])
    return names


# ---------------------------------------------------------------------------
# 行の差分 (追記だけを取り出す)
# ---------------------------------------------------------------------------

def _inserts(base_lines, sub_lines, where):
    """基点 → 提出 の行の差分を「追記」の並びにする.

    戻り値: [(基点の行番号 i (この行の前に入る), 提出側の開始行 j, 行のリスト)]。
    消えた行・書き換えた行は空行だけを許す (空行の増減は変換で元に戻すだけ
    で、意味は変わらない)。それ以外は _Unclassified。
    """
    out = []
    sm = difflib.SequenceMatcher(None, base_lines, sub_lines, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            continue
        gone = [ln for ln in base_lines[i1:i2] if ln.strip()]
        if gone:
            raise _Unclassified('%s %d 行目' % (where, j1 + 1),
                                '既存の行を消した・書き換えた: %r' % gone[0])
        if j2 > j1:
            out.append((i1, j1, sub_lines[j1:j2]))
    return out


def _slides(base_lines, sub_lines, i, j, block):
    """同じ差分の別の切り方 (前後の同じ行をまたいでずらしたもの) を返す.

    difflib は「}」などの同じ行が続くと、追記の切れ目を 1 行ずらして
    返すことがある。括弧の釣り合いで判定するときはずらし直して試す。
    """
    k = len(block)
    cands = [(i, j, block)]
    a, s = i, j
    while s > 0 and a > 0 and sub_lines[s - 1] == sub_lines[s - 1 + k] \
            and base_lines[a - 1] == sub_lines[s - 1 + k]:
        s -= 1
        a -= 1
        cands.append((a, s, sub_lines[s:s + k]))
    a, s = i, j
    while s + k < len(sub_lines) and a < len(base_lines) \
            and sub_lines[s] == sub_lines[s + k] \
            and base_lines[a] == sub_lines[s]:
        s += 1
        a += 1
        cands.append((a, s, sub_lines[s:s + k]))
    return cands


def _line_starts(text):
    starts = [0]
    for m in re.finditer('\n', text):
        starts.append(m.end())
    return starts


# ---------------------------------------------------------------------------
# 最新版 (変換先) の情報
# ---------------------------------------------------------------------------

_TAG_LINE = re.compile(r'\{#\s*tab:(.*?)#\}')
_TAG_ATTR = re.compile(r'(\w+)="([^"]*)"')
_BUILTIN = re.compile(r"\{'id':\s*'([\w-]+)',\s*'label':\s*'[^']*',\s*"
                      r"'order':\s*([\d.]+)")


def _read_tag(first_line):
    m = _TAG_LINE.fullmatch(first_line.strip())
    if not m:
        return None
    return dict(_TAG_ATTR.findall(m.group(1)))


class _Target:
    """変換先 (最新版) のタブの並び・同期グループ."""

    def __init__(self, workrepo, ref):
        self.ok = False
        tabreg = _git_text(workrepo, ref, 'tabreg.py') or ''
        index = _git_text(workrepo, ref, HUB_INDEX) or ''
        if 'def collect_tabs' not in tabreg or 'for t in tabs' not in index:
            return       # 新しい書き方がまだ無い (古い書き方のままが正しい)
        self.ok = True
        self.orders = {}        # id -> order (組み込み + 見出し)
        self.templates = {}     # テンプレート名 (_tab_x.html) -> 見出し dict
        app = _git_text(workrepo, ref, HUB_APP) or ''
        for tid, order in _BUILTIN.findall(app):
            self.orders[tid] = float(order)
        names = run_git(['ls-tree', '--name-only', ref, 'templates/'],
                        cwd=workrepo).split('\n')
        for rel in sorted(n.strip() for n in names if n.strip()):
            fn = rel.split('/')[-1]
            if not re.fullmatch(r'_tab_\w+\.html', fn):
                continue
            first = (_git_text(workrepo, ref, rel) or '').split('\n', 1)[0]
            tag = _read_tag(first) or {}
            self.templates[fn] = tag
            if tag.get('id'):
                try:
                    self.orders.setdefault(tag['id'],
                                           float(tag.get('order', 'inf')))
                except ValueError:
                    pass
        self.sync = {}          # グループ名 -> [id]
        js = _git_text(workrepo, ref, HUB_JS) or ''
        m = re.search(r'const\s+SYNC_GROUPS\s*=\s*\{(.*?)\};', js, re.S)
        if m:
            for key, body in re.findall(r'(\w+)\s*:\s*\[([^\]]*)\]',
                                        m.group(1)):
                self.sync[key] = re.findall(r"'([\w-]+)'", body)

    def finite_orders(self):
        return [v for v in self.orders.values() if v != float('inf')]


# ---------------------------------------------------------------------------
# 3 ファイルの読み取り
# ---------------------------------------------------------------------------

class _Found:
    """3 ファイルから読み取った登録 (と、読み取れなかった箇所)."""

    def __init__(self):
        self.packages = []        # app.py で登録していたタブのパッケージ名
        self.nav = []             # nav の並び [('tab', id) | ('loop',) | ('new', id, label)]
        self.new_buttons = []     # [(id, label)]
        self.includes = []        # index.html に足した include のテンプレート名
        self.out_names = []       # 出力フォルダ案内文に足した名前
        self.ids = {}             # 入力欄 id -> {'sync': [基点のグループの id], 'persist', 'hints', 'where'}
        self.moves = []           # [{'event', 'lines', 'refs', 'strs', 'where', 'provide'}]
        self.problems = []        # [_Unclassified]
        self.any = False          # 登録らしい変更が 1 つでもあったか


_IMPORT = re.compile(r'^from mgtkit\.(\w+)\.routes import make_blueprint '
                     r'as (\w+)\s*(#.*)?$')
_REGISTER = re.compile(r'^app\.register_blueprint\((\w+)\(sys\.modules'
                       r'\[__name__\]\)\)\s*(#.*)?$')


def _scan_app_py(base, sub, tree, found):
    imported, registered = {}, []
    for _i, j, block in _inserts(base.split('\n'), sub.split('\n'), HUB_APP):
        has_reg = any(_IMPORT.match(ln) or _REGISTER.match(ln) for ln in block)
        for k, ln in enumerate(block):
            where = '%s %d 行目' % (HUB_APP, j + k + 1)
            m = _IMPORT.match(ln)
            if m:
                imported[m.group(2)] = m.group(1)
                found.any = True
                continue
            m = _REGISTER.match(ln)
            if m:
                registered.append((m.group(1), where))
                found.any = True
                continue
            if not ln.strip() or (has_reg and ln.lstrip().startswith('#')):
                continue
            raise _Unclassified(where, '登録以外の行: %r' % ln.strip())
    for alias, where in registered:
        if alias not in imported:
            raise _Unclassified(where, '%s の import が見つかりません' % alias)
    for alias, pkg in imported.items():
        if alias not in [a for a, _w in registered]:
            raise _Unclassified(HUB_APP, '%s を import したが登録していない' % pkg)
        if (pkg in _NOT_TABS or pkg.startswith(('_', '.'))
                or not tree.exists('%s/__init__.py' % pkg)
                or not tree.exists('%s/routes.py' % pkg)
                or 'def make_blueprint' not in tree.read('%s/routes.py' % pkg)):
            raise _Unclassified(HUB_APP, '%s は自動登録の対象になりません' % pkg)
        found.packages.append(pkg)


_HINT = re.compile(r'\((\s*model\s*/[^()]*?)\)(\s*に保存されます)', re.S)
_BUTTON = re.compile(r'^\s*<button\s+data-tab="([A-Za-z][\w-]*)"\s*>'
                     r'([^<"]*)</button>\s*$')
_INCLUDE = re.compile(r"""^\s*\{%-?\s*include\s+['"](_tab_\w+\.html)['"]\s*-?%\}\s*$""")


def _hint_items(content):
    return [' '.join(t.split()) for t in content.split('/')]


def _normalize_hint(base, sub, found):
    """出力フォルダの案内文に足された名前を取り出し、文は基点に戻す."""
    mb, ms = list(_HINT.finditer(base)), list(_HINT.finditer(sub))
    if len(mb) != 1 or len(ms) != 1:
        return sub
    b_items, s_items = _hint_items(mb[0].group(1)), _hint_items(ms[0].group(1))
    if b_items == s_items:
        return sub
    found.any = True
    extra, k = [], 0
    for item in s_items:
        if k < len(b_items) and item == b_items[k]:
            k += 1
        elif re.fullmatch(r'[A-Za-z0-9_-]+', item):
            extra.append(item)
        else:
            return sub          # 名前の追加ではない (行の差分で読めない箇所になる)
    if k != len(b_items):
        return sub
    found.out_names.extend(extra)
    return sub[:ms[0].start(1)] + mb[0].group(1) + sub[ms[0].end(1):]


def _scan_index(base, sub, found):
    sub = _normalize_hint(base, sub, found)
    base_lines, sub_lines = base.split('\n'), sub.split('\n')
    try:
        nav_open = next(k for k, ln in enumerate(base_lines)
                        if ln.strip() == '<nav>')
        nav_close = next(k for k, ln in enumerate(base_lines)
                         if ln.strip() == '</nav>')
    except StopIteration:
        nav_open = nav_close = -1
    # 基点の nav の並び: 手書きのボタンと、見出しから作るループ (1 つの塊)
    items, loop_inside = {}, set()
    k = nav_open + 1
    while 0 <= nav_open < k < nav_close:
        ln = base_lines[k]
        m = re.match(r'^\s*<button\s+data-tab="([A-Za-z][\w-]*)"', ln)
        if m:
            items[k] = ('tab', m.group(1))
        elif re.match(r'^\s*\{%-?\s*for\s+t\s+in\s+tabs', ln):
            end = k
            while end < nav_close and 'endfor' not in base_lines[end]:
                end += 1
            items[k] = ('loop',)
            loop_inside.update(range(k + 1, end + 1))
            k = end
        k += 1
    inserted = {}
    for i, j, block in _inserts(base_lines, sub_lines, HUB_INDEX):
        for off, ln in enumerate(block):
            where = '%s %d 行目' % (HUB_INDEX, j + off + 1)
            if not ln.strip():
                continue
            m = _BUTTON.match(ln)
            if m and nav_open < i <= nav_close and i not in loop_inside:
                found.any = True
                label = html.unescape(m.group(2).strip())
                if not label or '"' in label or '#}' in label:
                    raise _Unclassified(where, 'ボタンの表示名を見出しに書けません')
                inserted.setdefault(i, []).append(('new', m.group(1), label))
                found.new_buttons.append((m.group(1), label))
                continue
            m = _INCLUDE.match(ln)
            if m and i > nav_close:
                found.any = True
                found.includes.append(m.group(1))
                continue
            if m or _BUTTON.match(ln):
                found.any = True
            raise _Unclassified(where, '登録以外の行: %r' % ln.strip())
    for k in range(nav_open + 1, max(nav_close, nav_open) + 1):
        found.nav.extend(inserted.get(k, []))
        if k in items:
            found.nav.append(items[k])


_JS_LISTS = (
    ('sync', re.compile(r'const\s+SYNC_GROUPS\s*=\s*([\[{])')),
    ('persist', re.compile(r"(\[)\s*'mgt_path'\s*,\s*'mgt_out_base'")),
    ('hints', re.compile(r'body\.path_hints\s*=\s*(\[)')),
)


def _list_span(src, toks, rx):
    ms = list(rx.finditer(src))
    if len(ms) != 1:
        return None
    pos = ms[0].start(1)
    k = next((x for x, t in enumerate(toks) if t[2] == pos), None)
    if k is None:
        return None
    return k, _match_close(toks, k)


def _normalize_js_lists(base, sub, found):
    """SYNC_GROUPS などの一覧に足された id を取り出し、一覧は基点に戻す."""
    for name, rx in _JS_LISTS:
        bt, st = _js_tokens(base), _js_tokens(sub)
        bspan, sspan = _list_span(base, bt, rx), _list_span(sub, st, rx)
        if not bspan or not sspan:
            continue
        bl = bt[bspan[0]:bspan[1] + 1]
        sl = st[sspan[0]:sspan[1] + 1]
        if [t[1] for t in bl] == [t[1] for t in sl]:
            if base[bl[0][2]:bl[-1][3]] != sub[sl[0][2]:sl[-1][3]]:
                sub = sub[:sl[0][2]] + base[bl[0][2]:bl[-1][3]] + sub[sl[-1][3]:]
            continue
        found.any = True
        sm = difflib.SequenceMatcher(None, [t[1] for t in bl],
                                     [t[1] for t in sl], autojunk=False)
        added = []
        ok = True
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == 'equal':
                continue
            ins = sl[j1:j2]
            if (op != 'insert' or not any(t[0] == 'str' for t in ins)
                    or any(t[0] != 'str' and t[1] != ',' for t in ins)):
                ok = False
                break
            # 差し込まれた位置を囲む一覧 (SYNC_GROUPS なら内側のグループ)
            stack = []
            for x in range(i1):
                if bl[x][1] in ('[', '{'):
                    stack.append(x)
                elif bl[x][1] in (']', '}'):
                    stack.pop()
            want = 2 if name == 'sync' else 1
            if len(stack) != want or bl[stack[-1]][1] != '[':
                ok = False
                break
            group = []
            if name == 'sync':
                close = _match_close(bl, stack[-1])
                group = [_str_value(t) for t in bl[stack[-1]:close]
                         if t[0] == 'str']
            for t in ins:
                if t[0] == 'str':
                    v = _str_value(t)
                    if not re.fullmatch(r'[A-Za-z_][\w-]*', v):
                        ok = False
                    added.append((v, group))
        if not ok:
            continue            # 行の差分で読めない箇所として出る
        for v, group in added:
            e = found.ids.setdefault(v, {'sync': None, 'persist': False,
                                         'hints': False})
            if name == 'sync':
                e['sync'] = group
            else:
                e[name] = True
        sub = sub[:sl[0][2]] + base[bl[0][2]:bl[-1][3]] + sub[sl[-1][3]:]
    return sub


# app.js の関数に足されたタブ固有の処理 → そのタブ側のイベントの受け取りへ
_HOOKS = (
    {'func': 'loadMgt', 'event': 'mgtkit:mgt-loaded',
     'after': r"const\s+j\s*=\s*await\s+api\(\s*'/api/mgt_info'",
     'provide': {'j': 'detail'}},
    {'func': 'clearCaseTable', 'event': 'mgtkit:stress-changed',
     'after': None, 'provide': {}},
)
_FORBIDDEN_IN_MOVE = {'return', 'await', 'yield', 'break', 'continue',
                      'arguments', 'this'}


def _hook_spans(base, toks):
    spans = []
    for h in _HOOKS:
        m = re.search(r'(?:async\s+)?function\s+%s\s*\(' % h['func'], base)
        if not m:
            continue
        k = next((x for x, t in enumerate(toks)
                  if t[2] >= m.end() and t[1] == '{'), None)
        if k is None:
            continue
        close = _match_close(toks, k)
        anchor = toks[k][3]
        if h['after']:
            ma = re.compile(h['after']).search(base, toks[k][3],
                                               toks[close][2])
            if not ma:
                continue
            # その文の行末 (api( の括弧の中ではなく、文の外の深さで比べる)
            eol = base.find('\n', ma.end())
            anchor = len(base) if eol < 0 else eol
        body = toks[k:close + 1]
        spans.append(dict(h, open=toks[k][3], close=toks[close][2],
                          anchor=anchor, toks=body,
                          locals=_declared(body) - set(h['provide'])))
    return spans


def _depth(toks, start, end):
    d = 0
    for t in toks:
        if start <= t[2] < end and t[0] == 'punc':
            if t[1] in _OPEN:
                d += 1
            elif t[1] in _CLOSE:
                d -= 1
    return d


def _try_move(base, btoks, starts, spans, i, block):
    """追記 block (基点の i 行目の前) が、関数に足した処理として移せるか."""
    off = starts[i] if i < len(starts) else len(base)
    text = '\n'.join(block)
    try:
        bt = _js_tokens(text)
    except ValueError:
        return None
    sig = [t for t in bt if t[0] != 'com']
    if not sig or not _balanced(bt) or sig[-1][1] not in (';', '}'):
        return None
    for sp in spans:
        if not (sp['open'] < off <= sp['close'] and off > sp['anchor']):
            continue
        if _depth(sp['toks'], sp['open'], off) != \
                _depth(sp['toks'], sp['open'], sp['anchor']):
            continue
        before = [t for t in btoks if t[3] <= off and t[0] != 'com']
        if not before or before[-1][1] not in (';', '{', '}'):
            continue
        ids, strs = _refs(bt)
        if ids & _FORBIDDEN_IN_MOVE:
            continue
        if ids & (sp['locals'] - _declared(bt)):
            continue           # 関数の中の変数を使っている (移すと動かない)
        return {'event': sp['event'], 'lines': block, 'refs': ids,
                'strs': strs, 'provide': sp['provide'], 'func': sp['func']}
    return None


def _scan_app_js(base, sub, found):
    sub = _normalize_js_lists(base, sub, found)
    btoks = _js_tokens(base)
    spans = _hook_spans(base, btoks)
    starts = _line_starts(base)
    base_lines, sub_lines = base.split('\n'), sub.split('\n')
    for i, j, block in _inserts(base_lines, sub_lines, HUB_JS):
        if not any(ln.strip() for ln in block):
            continue
        move = None
        for ci, cj, cblock in _slides(base_lines, sub_lines, i, j, block):
            move = _try_move(base, btoks, starts, spans, ci, cblock)
            if move:
                move['where'] = '%s %d 行目' % (HUB_JS, cj + 1)
                break
        if not move:
            raise _Unclassified('%s %d 行目' % (HUB_JS, j + 1),
                                '移せない追記: %r' % next(
                                    ln.strip() for ln in block if ln.strip()))
        found.any = True
        found.moves.append(move)


# ---------------------------------------------------------------------------
# 直す (見出し・属性・イベントの受け取り)
# ---------------------------------------------------------------------------

def _nice_step(span, k):
    """span を k+1 等分した幅以下の、きりのよい刻み (1・2・5 の 10 の冪倍)."""
    raw = span / (k + 1)
    if raw <= 0:
        return 0
    p = 10.0 ** math.floor(math.log10(raw))
    for mult in (5, 2, 1):
        if mult * p <= raw:
            return mult * p
    return raw


def _fmt(v):
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'


def _assign_orders(nav, target, template_of):
    """nav の並び (新しいボタンを含む) から、新しいタブの order を決める.

    前後のタブの order の間に等間隔 (きりのよい刻み) で入れる。右端に足した
    タブは order を書かない (見出しの order 省略 = 右端)。ただし右端に
    2 つ以上足して、ファイル名順が nav の並びと違うときは値を書く。
    戻り値: {id: order の文字列 (省略は None)}
    """
    finite = target.finite_orders()

    def order_of(item):
        if item[0] == 'tab':
            return target.orders.get(item[1])
        return None

    out = {}
    k = 0
    while k < len(nav):
        if nav[k][0] != 'new':
            k += 1
            continue
        run_end = k
        while run_end < len(nav) and nav[run_end][0] == 'new':
            run_end += 1
        run = [nav[x][1] for x in range(k, run_end)]
        left = right = None
        for x in range(k - 1, -1, -1):
            if nav[x][0] == 'loop':
                left = max(finite) if finite else None
                break
            if order_of(nav[x]) is not None and order_of(nav[x]) != float('inf'):
                left = order_of(nav[x])
                break
        for x in range(run_end, len(nav)):
            if nav[x][0] == 'loop':
                right = min(finite) if finite else None
                break
            if order_of(nav[x]) is not None and order_of(nav[x]) != float('inf'):
                right = order_of(nav[x])
                break
        if right is not None and left is not None and right <= left:
            right = None
        n = len(run)
        if right is None:
            files = [template_of[t] for t in run]
            if left is None or files == sorted(files):
                for t in run:
                    out[t] = None
            else:
                for x, t in enumerate(run):
                    out[t] = _fmt(left + 10 * (x + 1))
        elif left is None:
            for x, t in enumerate(run):
                out[t] = _fmt(right - 10 * (n - x))
        else:
            step = _nice_step(right - left, n)
            for x, t in enumerate(run):
                out[t] = _fmt(left + step * (x + 1))
        k = run_end
    return out


def _tag_line(tid, label, order, out):
    parts = ['id="%s"' % tid, 'label="%s"' % label]
    if order is not None:
        parts.append('order="%s"' % order)
    if out:
        parts.append('out="%s"' % out)
    return '{# tab: %s #}' % ' '.join(parts)


def _section_template(tree, tid):
    hits = [rel for rel in tree.tab_templates()
            if re.search(r'<section\b[^>]*\bid="tab-%s"' % re.escape(tid),
                         tree.read(rel))]
    return hits[0] if len(hits) == 1 else None


def _tab_script(tree, rel):
    text = tree.read(rel)
    for m in re.finditer(r"""<script\s+src=["']/static/([\w./-]+?\.js)[^"']*["']"""
                         r"""|url_for\(\s*'static'\s*,\s*filename\s*=\s*'([\w./-]+?\.js)'""",
                         text):
        js = 'static/' + (m.group(1) or m.group(2))
        if tree.exists(js):
            return js
    return None


def _build(found, tree, target, base_tree_tag, report, warnings):
    """読み取った登録を、各タブのファイルへの書き換えにする."""
    # --- 新しいタブ (nav に足したボタン) と見出し ---
    template_of, tags = {}, {}
    for tid, label in found.new_buttons:
        rel = _section_template(tree, tid)
        if not rel:
            raise _Unclassified(HUB_INDEX, 'タブ %s の本体 (tab-%s) が '
                                'templates/_tab_*.html に見つかりません'
                                % (tid, tid))
        fn = rel.split('/')[-1]
        if fn not in found.includes:
            raise _Unclassified(HUB_INDEX, '%s の include がありません' % fn)
        if tid in target.orders or fn in target.templates:
            raise _Unclassified(HUB_INDEX, 'タブ %s は最新版にもう有ります'
                                % tid)
        template_of[tid] = fn
        first = tree.read(rel).split('\n', 1)[0]
        own = _read_tag(first)
        if own is not None:
            if own.get('id') != tid:
                raise _Unclassified(rel, '見出しの id がボタンと違います')
            tags[tid] = None          # 提出者が書いた見出しをそのまま使う
        else:
            tags[tid] = {'label': label, 'out': ''}
    for fn in found.includes:
        if fn in template_of.values():
            continue
        first = (tree.read('templates/' + fn).split('\n', 1)[0]
                 if tree.exists('templates/' + fn) else '')
        if _read_tag(first) is None:
            raise _Unclassified(HUB_INDEX, '%s の include に nav のボタンが'
                                'ありません' % fn)
    orders = _assign_orders(found.nav, target, template_of)

    # --- 出力フォルダ案内文の名前 → 見出しの out ---
    for name in found.out_names:
        tid = next((t for t, fn in template_of.items()
                    if name in (t, fn[len('_tab_'):-len('.html')])), None)
        if tid:
            if tags[tid] is None:
                raise _Unclassified(HUB_INDEX, '案内文の %s は提出者の見出しに'
                                    '書いてください' % name)
            tags[tid]['out'] = name
            continue
        fn = next((f for f, tag in target.templates.items()
                   if name in (tag.get('id'), f[len('_tab_'):-len('.html')])),
                  None)
        if fn is None:
            raise _Unclassified(HUB_INDEX, '案内文の %s に当たるタブが'
                                'ありません' % name)
        tag = target.templates[fn]
        if tag.get('out') == name:
            continue            # 最新版でもう載っている
        rel = 'templates/' + fn
        first = tree.read(rel).split('\n', 1)[0] if tree.exists(rel) else ''
        own = _read_tag(first) if base_tree_tag(rel) else None
        if own is not None and own.get('id') == tag.get('id'):
            text = tree.read(rel)
            line = re.sub(r'\s*out="[^"]*"', '', first)
            line = re.sub(r'\s*#\}\s*$', ' out="%s" #}' % name, line)
            tree.write(rel, line + text[len(first):])
            report.append('- %s: 見出しに out="%s" を追加 (出力フォルダの'
                          '案内文に載せる)' % (rel, name))
        else:
            label = tag.get('label') or fn
            warnings.append(
                '出力フォルダの案内文に「%s」を足す変更は、自動では反映できな'
                'かったため外しました。必要なら「%s」の見出しに out="%s" を書いて'
                '提出し直してください。' % (name, label, name))

    for tid, spec in tags.items():
        if spec is None:
            continue
        rel = 'templates/' + template_of[tid]
        line = _tag_line(tid, spec['label'], orders.get(tid), spec['out'])
        tree.write(rel, line + '\n' + tree.read(rel))
        where = ('右端' if orders.get(tid) is None
                 else 'order=%s' % orders[tid])
        report.append('- %s: 先頭行に見出し `%s` を追加 (nav の並び: %s)'
                      % (rel, line, where))

    # --- app.js の一覧の id → 入力欄の data-sync / data-persist ---
    for eid, spec in found.ids.items():
        if spec['hints'] and not spec['persist']:
            raise _Unclassified(HUB_JS, '%s は記憶する欄の一覧に無いのに出力先'
                                '推定 (path_hints) にだけ足されています' % eid)
        hits = []
        for rel in tree.tab_templates():
            for m in re.finditer(r'<(?:input|select|textarea)\b[^>]*?\bid="%s"'
                                 % re.escape(eid), tree.read(rel)):
                hits.append((rel, m))
        if len(hits) != 1:
            raise _Unclassified(HUB_JS, '入力欄 %s がタブのテンプレートに '
                                '1 つだけ見つかりません' % eid)
        rel, m = hits[0]
        text = tree.read(rel)
        tag_end = text.index('>', m.start())
        element = text[m.start():tag_end]
        attrs = ''
        if spec['sync'] is not None:
            keys = [k for k, ids in target.sync.items()
                    if set(ids) & set(spec['sync'])]
            if len(keys) != 1:
                raise _Unclassified(HUB_JS, '%s の同期グループが最新版で'
                                    '決まりません' % eid)
            have = re.search(r'\bdata-sync="([^"]*)"', element)
            if have and have.group(1) != keys[0]:
                raise _Unclassified(rel, '%s の data-sync が一覧と違います'
                                    % eid)
            if not have:
                attrs += ' data-sync="%s"' % keys[0]
        if spec['persist'] and not re.search(r'\bdata-persist\b', element):
            attrs += ' data-persist'
        if attrs:
            at = m.end()
            tree.write(rel, text[:at] + attrs + text[at:])
            report.append('- %s: 入力欄 %s に%s を追加 (app.js の一覧に足す'
                          '代わり)' % (rel, eid, attrs))

    # --- app.js の関数に足した処理 → タブの JS でイベントを受け取る ---
    names = {}
    for rel in tree.tab_templates():
        ns = set(re.findall(r'\bid="([^"{}]+)"', tree.read(rel)))
        js = _tab_script(tree, rel)
        if js:
            ns |= _top_level_names(tree.read(js))
        names[rel] = ns
    grouped = {}
    for mv in found.moves:
        owners = [rel for rel, ns in names.items()
                  if ns & (mv['refs'] | mv['strs'])]
        if len(owners) != 1:
            raise _Unclassified(mv['where'], 'どのタブの処理か決まりません'
                                ' (候補 %d 件)' % len(owners))
        grouped.setdefault((owners[0], mv['event']), []).append(mv)
    for (rel, event), mvs in grouped.items():
        js = _tab_script(tree, rel)
        used = set().union(*(mv['refs'] for mv in mvs))
        param = next(p for p in ('e', 'ev', 'evt', 'mgtkitEvent')
                     if p not in used)
        body = []
        for name, field in mvs[0]['provide'].items():
            body.append('  const %s = %s.%s;' % (name, param, field))
        for mv in mvs:
            lines = mv['lines']
            while lines and not lines[0].strip():
                lines = lines[1:]
            while lines and not lines[-1].strip():
                lines = lines[:-1]
            ind = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
            body.extend(('  ' + ln[ind:]) if ln.strip() else '' for ln in lines)
        func = mvs[0]['func']
        head = ('%s =>' % param) if mvs[0]['provide'] else '() =>'
        listener = ['// 共通欄の出来事 (%s) を受け取る。以前は app.js の %s に'
                    '直接書いていた処理' % (event, func),
                    "document.addEventListener('%s', %s {" % (event, head)]
        listener += body + ['});']
        if js:
            text = tree.read(js)
            sep = '' if text.endswith('\n') or not text else '\n'
            tree.write(js, text + sep + '\n' + '\n'.join(listener) + '\n')
            target_rel = js
        else:
            text = tree.read(rel)
            sep = '' if text.endswith('\n') or not text else '\n'
            tree.write(rel, text + sep + '<script>\n' + '\n'.join(listener)
                       + '\n</script>\n')
            target_rel = rel
        report.append('- %s: app.js の %s に足されていた処理を %s の受け取り'
                      'へ移動' % (target_rel, func, event))
    for pkg in found.packages:
        report.append('- %s/: app.py への import・登録の行は送らず、起動時の'
                      '自動登録に任せる' % pkg)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def plan(workrepo, base_commit, extract_dir, changes, target_ref):
    """提出 (extract_dir) の古い書き方を直す計画を立てる (まだ書き換えない).

    戻り値 dict:
      status   'none'      直すものが無い (古い書き方の登録が無い・最新版が
                           まだ古い書き方・つなぎ込みの 3 ファイルを触っていない)
               'converted' 直せる。writes を apply() で書き込む
               'kept'      古い書き方だが読み取れない変更が混じるので、
                           そのまま提出する (warnings を画面に出す)
      writes   {相対パス: バイト列}。つなぎ込みの 3 ファイルは基点の中身に戻す
      report   PR 本文に載せる「直した箇所」の行 (承認する人向け)
      warnings 提出者に見せる文 (平易な日本語)
      problems 読み取れなかった箇所 (ログ向け)
      needs_merge 基点に新しい書き方が無い (直したタブを画面に出すには
                  最新版を取り込む必要がある)
    """
    result = {'status': 'none', 'writes': {}, 'report': [], 'warnings': [],
              'problems': [], 'needs_merge': False}
    hubs = [h for h in HUB_FILES if h in changes.get('modified', ())]
    if not hubs:
        return result
    try:
        target = _Target(workrepo, target_ref)
    except GitError:
        log.warning('最新版 (%s) を読めないため、タブの登録は直しません',
                    target_ref)
        return result
    if not target.ok:
        return result
    tree = _Tree(extract_dir)
    found = _Found()
    base_texts = {}
    scanners = {HUB_APP: lambda b, s: _scan_app_py(b, s, tree, found),
                HUB_INDEX: lambda b, s: _scan_index(b, s, found),
                HUB_JS: lambda b, s: _scan_app_js(b, s, found)}
    for rel in hubs:
        base = _git_text(workrepo, base_commit, rel)
        if base is None:
            continue
        base_texts[rel] = base
        try:
            scanners[rel](base, tree.read(rel))
        except _Unclassified as e:
            found.problems.append(e)
        except (ValueError, UnicodeDecodeError) as e:
            found.problems.append(_Unclassified(rel, '読み取れません (%s)' % e))
    report, warnings = [], []
    if not found.problems and found.any:
        try:
            _build(found, tree, target,
                   lambda rel: (_read_tag((_git_text(workrepo, base_commit, rel)
                                           or '').split('\n', 1)[0])
                                is not None),
                   report, warnings)
        except _Unclassified as e:
            found.problems.append(e)
        except (ValueError, UnicodeDecodeError, OSError) as e:
            found.problems.append(_Unclassified('タブのファイル', str(e)))
    if not found.any:
        return result
    if found.problems:
        result['status'] = 'kept'
        result['problems'] = [str(p) for p in found.problems]
        where = '、'.join(dict.fromkeys(p.where for p in found.problems[:3]))
        result['warnings'] = [_KEPT_MESSAGE % where]
        log.warning('古い書き方のタブの登録を直せなかったため、そのまま提出します: %s',
                    ' / '.join(result['problems']))
        return result
    writes = {}
    for rel in hubs:
        data = _git_bytes(workrepo, base_commit, rel)
        if data is not None:
            writes[rel] = data
    for rel, (text, fmt) in tree.pending.items():
        writes[rel] = _encode(text, fmt)
    result.update(status='converted', writes=writes, warnings=warnings,
                  report=[_REPORT_HEAD,
                          '提出には app.py / templates/index.html / '
                          'static/app.js へのタブの登録の追記がありましたが、'
                          'そのままでは最新版とぶつかるため送らず、次のとおり'
                          '各タブのファイル側に書き直しました '
                          '(提出者の画面と同じ並び・同じ動き)。'] + report,
                  needs_merge=not has_auto_registration(workrepo,
                                                        base_commit))
    log.info('古い書き方のタブの登録を直しました: %s', ' / '.join(report))
    return result


def apply(result, extract_dir):
    """plan() の writes を展開先へ書き込む."""
    for rel, data in result.get('writes', {}).items():
        path = os.path.join(extract_dir, rel.replace('/', os.sep))
        with open(path, 'wb') as f:
            f.write(data)


def has_auto_registration(workrepo, ref):
    """ref の版に見出し・自動登録の仕組み (tabreg.py) があるか."""
    return _git_bytes(workrepo, ref, 'tabreg.py') is not None
