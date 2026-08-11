# -*- coding: utf-8 -*-
"""提出差分の HTML ビューワ生成 (UI 非依存).

「差分」ダイアログの「詳細ビューワで開く」から使う。フォルダ比較
(変更ファイル一覧) と左右並びのファイル比較を、依存なしの自己完結
HTML 1 枚として一時フォルダへ書き出し、パスを返す (開くのは呼び出し側)。

重さ対策:
  - 変更のない行は前後 MAX_HUNK_CONTEXT 行だけ残して折りたたむ
  - 変更が MAX_CHANGED_LINES 行を超えるファイルは省略表示
  - 画像・バイナリは「表示対象外」と明記
"""
import difflib
import html
import logging
import os
import subprocess
import sys
import tempfile

from . import paths
from .gitcli import ensure_work_repo, run_git
from .submit import workrepo_dir

log = logging.getLogger(__name__)

MAX_HUNK_CONTEXT = 3      # 変更行の前後に見せる行数
MAX_CHANGED_LINES = 800   # これを超えるファイルは省略表示

BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip',
               '.xlsx', '.pyc', '.exe', '.dll'}

# 表示上のパスの先頭に付けるフォルダ名。メンバーの手元では
# C:\Users\(自分)\mgtkit\... に展開されるため、その見え方に合わせる
DISPLAY_ROOT = 'mgtkit'


def _git_bytes(workrepo, args):
    """バイト列が要る git 出力 (ファイル内容)。失敗は None."""
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.run(['git'] + list(args), cwd=workrepo,
                              capture_output=True, timeout=120, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _file_lines(workrepo, rev, path):
    """rev 時点のファイル内容を行リストで。バイナリ等は None."""
    data = _git_bytes(workrepo, ['show', '%s:%s' % (rev, path)])
    if data is None:
        return None
    for enc in ('utf-8', 'cp932'):
        try:
            return data.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return None


def side_by_side(old, new):
    """difflib で左右並びの行ペアを作る.

    戻り値: [(kind, lno_l, text_l, lno_r, text_r)]
      kind: 'same' | 'change' | 'del' | 'add'
    """
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                rows.append(('same', i1 + k + 1, old[i1 + k],
                             j1 + k + 1, new[j1 + k]))
        elif tag == 'replace':
            for k in range(max(i2 - i1, j2 - j1)):
                lno = i1 + k + 1 if i1 + k < i2 else None
                rno = j1 + k + 1 if j1 + k < j2 else None
                rows.append(('change', lno, old[i1 + k] if lno else '',
                             rno, new[j1 + k] if rno else ''))
        elif tag == 'delete':
            for k in range(i2 - i1):
                rows.append(('del', i1 + k + 1, old[i1 + k], None, ''))
        elif tag == 'insert':
            for k in range(j2 - j1):
                rows.append(('add', None, '', j1 + k + 1, new[j1 + k]))
    return rows


def collapse_context(rows):
    """変更のない行は前後 MAX_HUNK_CONTEXT 行だけ残して折りたたむ.

    折りたたんだ箇所は ('gap', None, 行数, None, 行数) を挟む。
    """
    keep = [False] * len(rows)
    for i, r in enumerate(rows):
        if r[0] != 'same':
            for j in range(max(0, i - MAX_HUNK_CONTEXT),
                           min(len(rows), i + MAX_HUNK_CONTEXT + 1)):
                keep[j] = True
    out, skipped = [], 0
    for i, r in enumerate(rows):
        if keep[i]:
            if skipped:
                out.append(('gap', None, skipped, None, skipped))
                skipped = 0
            out.append(r)
        else:
            skipped += 1
    if skipped:
        out.append(('gap', None, skipped, None, skipped))
    return out


def _esc(s):
    return html.escape(str(s)).replace(' ', '&nbsp;') or '&nbsp;'


_CSS = """
body { font-family: 'Yu Gothic UI', 'Meiryo', sans-serif; margin: 0;
       background: #f5f7fa; color: #1f2937; }
header { background: #1e3a5f; color: #fff; padding: 14px 24px; }
header h1 { font-size: 17px; margin: 0 0 4px; }
header .meta { font-size: 12px; opacity: .85; }
.wrap { margin: 0 auto; padding: 16px 20px 48px; }
.sec-h { font-size: 14px; margin: 18px 0 10px; color: #1e3a5f;
         border-left: 4px solid #1e3a5f; padding-left: 8px; }
.card { background: #fff; border-radius: 8px; padding: 16px 20px;
        margin: 14px 0; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card h2 { font-size: 14px; margin: 0 0 10px; color: #1e3a5f;
           border-left: 4px solid #1e3a5f; padding-left: 8px; }
table.sum { border-collapse: collapse; font-size: 13px; width: 100%; }
table.sum td { padding: 4px 10px; border-bottom: 1px solid #eef1f5; }
table.sum a { color: #1d4ed8; text-decoration: none; }
table.sum a:hover { text-decoration: underline; }
.tagM { color: #92400e; background: #fef3c7; border-radius: 3px;
        padding: 1px 7px; font-size: 11px; }
.tagA { color: #14532d; background: #bbf7d0; border-radius: 3px;
        padding: 1px 7px; font-size: 11px; }
.tagD { color: #7f1d1d; background: #fecaca; border-radius: 3px;
        padding: 1px 7px; font-size: 11px; }
.stat { color: #6b7280; font-size: 12px; }
.stat b.add { color: #15803d; } .stat b.del { color: #b91c1c; }
table.diff { border-collapse: collapse; width: 100%; table-layout: fixed;
             font-family: Consolas, 'BIZ UDGothic', monospace;
             font-size: 12px; }
table.diff td { padding: 0 6px; vertical-align: top;
                white-space: pre-wrap; word-break: break-all; }
td.ln { color: #9ca3af; text-align: right; user-select: none;
        background: #fafbfc; border-right: 1px solid #eef1f5; }
tr.same td.code { background: #fff; }
tr.change td.code.l, tr.del td.code.l { background: #fecaca; }
tr.change td.code.r, tr.add td.code.r { background: #bbf7d0; }
tr.del td.code.r, tr.add td.code.l { background: #e5e7eb; }
tr.gap td { background: #eef2f7; color: #6b7280; text-align: center;
            font-size: 11px; padding: 3px; }
.note { color: #6b7280; font-size: 12px; margin: 6px 0 0; }
.top { position: fixed; right: 18px; bottom: 18px; background: #1e3a5f;
       color: #fff; border-radius: 20px; padding: 8px 16px; font-size: 12px;
       text-decoration: none; box-shadow: 0 2px 6px rgba(0,0,0,.25); }
"""

_STATUS_JP = {'M': ('変更', 'tagM'), 'A': ('追加', 'tagA'),
              'D': ('削除', 'tagD'), 'R': ('移動', 'tagM')}


def _display_path(path):
    return '%s/%s' % (DISPLAY_ROOT, path)


def _file_section(anchor, path, stat_html, inner):
    return ('<div class="card" id="%s"><h2>%s %s</h2>%s</div>'
            % (anchor, html.escape(_display_path(path)), stat_html, inner))


def build_html(meta, base_ref, head_ref, workrepo):
    """差分ビューワの HTML 全体を組み立てて文字列で返す.

    meta: dict(number, title, author, beta=None)
    base_ref/head_ref: 比較する 2 つの git リファレンス
    """
    mb = run_git(['merge-base', base_ref, head_ref], cwd=workrepo).strip()
    out = run_git(['diff', '--name-status', '%s..%s' % (mb, head_ref)],
                  cwd=workrepo)
    files = [line.split('\t', 1) for line in out.splitlines()
             if line.strip()]

    sums, sections = [], []
    n_add = n_del = 0
    for status, path in ((s[0], p) for s, p in files):
        jp, cls = _STATUS_JP.get(status, ('変更', 'tagM'))
        anchor = 'f-%s' % path.replace('/', '-').replace('.', '-')
        ext = ('.' + path.rsplit('.', 1)[-1].lower()) if '.' in path else ''
        old = [] if status == 'A' else _file_lines(workrepo, mb, path)
        new = [] if status == 'D' else _file_lines(workrepo, head_ref, path)

        if ext in BINARY_EXTS or old is None or new is None:
            sums.append((jp, cls, path, anchor, '(表示対象外)'))
            sections.append(_file_section(
                anchor, path, '',
                '<p class="note">画像・バイナリ形式のため差分表示の'
                '対象外です。</p>'))
            continue

        rows = side_by_side(old, new)
        changed = sum(1 for r in rows if r[0] != 'same')
        adds = sum(1 for r in rows if r[0] in ('add', 'change') and r[3])
        dels = sum(1 for r in rows if r[0] in ('del', 'change') and r[1])
        n_add += adds
        n_del += dels
        stat = ('<span class="stat"><b class="add">+%d</b> / '
                '<b class="del">-%d</b></span>' % (adds, dels))
        sums.append((jp, cls, path, anchor, stat))

        if changed > MAX_CHANGED_LINES:
            sections.append(_file_section(
                anchor, path, stat,
                '<p class="note">%d 行の変更があります。大きすぎるため'
                '全体は省略しました。</p>' % changed))
            continue

        body = []
        for kind, lno, lt, rno, rt in collapse_context(rows):
            if kind == 'gap':
                body.append('<tr class="gap"><td colspan="4">… 変更のない '
                            '%d 行 …</td></tr>' % lt)
            else:
                body.append(
                    '<tr class="%s"><td class="ln">%s</td>'
                    '<td class="code l">%s</td><td class="ln">%s</td>'
                    '<td class="code r">%s</td></tr>'
                    % (kind, lno or '', _esc(lt), rno or '', _esc(rt)))
        # 列幅は colgroup で固定 (行番号 48px、コード欄は左右均等)。
        # td 側の幅指定だと colspan の折りたたみ行に引っ張られてズレる
        sections.append(_file_section(
            anchor, path, stat,
            '<table class="diff"><colgroup>'
            '<col style="width:48px"><col>'
            '<col style="width:48px"><col></colgroup>%s</table>'
            % ''.join(body)))

    sum_rows = ''.join(
        '<tr><td width="60"><span class="%s">%s</span></td>'
        '<td><a href="#%s">%s</a></td><td width="120">%s</td></tr>'
        % (cls, jp, anchor, html.escape(_display_path(path)), stat)
        for jp, cls, path, anchor, stat in sums)
    beta = ('・ β版 %s ' % html.escape(meta['beta'])) if meta.get('beta') \
        else ''
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<title>提出 #%d の差分</title><style>%s</style></head><body>'
        '<header><h1>提出 #%d %s</h1>'
        '<div class="meta">提出者: %s %s・ 基点との比較 '
        '(追加 <b>+%d</b> 行 / 削除 <b>-%d</b> 行)</div></header>'
        '<div class="wrap">'
        '<h2 class="sec-h">&lt;1&gt; フォルダ比較 '
        '(変更されたファイル %d 件)</h2>'
        '<div class="card"><table class="sum">%s</table>'
        '<p class="note">ファイル名クリックでその場所へ移動します。'
        '左 = 変更前 (現在の正式版) / 右 = 変更後 (提出内容)。'
        '<span style="background:#fecaca">&nbsp;赤&nbsp;</span>'
        '= 削除された行、'
        '<span style="background:#bbf7d0">&nbsp;緑&nbsp;</span>'
        '= 追加された行。</p></div>'
        '<h2 class="sec-h">&lt;2&gt; ファイル比較</h2>%s</div>'
        '<a class="top" href="#">▲ 先頭へ</a></body></html>'
        % (meta['number'], _CSS, meta['number'],
           html.escape(meta['title']), html.escape(meta['author']), beta,
           n_add, n_del, len(files), sum_rows, ''.join(sections)))


def write_diff_html(pr, config=None, workrepo=None, beta_tag=None):
    """提出 pr の差分ビューワ HTML を一時フォルダに書き出す.

    pr: reviews.list_pending の要素 (number/title/author/branch を使用)。
    戻り値: HTML ファイルのパス (ブラウザで開くのは呼び出し側)。
    """
    if workrepo is None:
        workrepo = ensure_work_repo(paths.repo_slug(config),
                                    workrepo_dir(config))
    base = (config or {}).get('base_branch', 'main')
    run_git(['fetch', 'origin', base, pr['branch']], cwd=workrepo,
            timeout=300)
    text = build_html(
        {'number': pr['number'], 'title': pr['title'],
         'author': pr.get('author', '?'), 'beta': beta_tag},
        'origin/%s' % base, 'origin/%s' % pr['branch'], workrepo)
    out = os.path.join(tempfile.gettempdir(),
                       'mgtkit_diff_%d.html' % pr['number'])
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    return out
