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
import base64
import difflib
import html
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import zipfile

from . import ghcli, paths
from .gitcli import ensure_work_repo, run_git
from .submit import _is_dist_scope, workrepo_dir

log = logging.getLogger(__name__)

MAX_HUNK_CONTEXT = 3      # 変更行の前後に見せる行数
MAX_CHANGED_LINES = 800   # これを超えるファイルは省略表示
MAX_DL_MB = 20            # 更新データ ZIP の埋め込み上限 (超えたらボタン非表示)

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
header { background: #1e3a5f; color: #fff; padding: 14px 24px;
         display: flex; justify-content: space-between;
         align-items: center; gap: 16px; }
header h1 { font-size: 17px; margin: 0 0 4px; }
header .meta { font-size: 12px; opacity: .85; }
a.dl { flex: none; border: 1px solid rgba(255,255,255,.55); color: #fff;
       border-radius: 6px; padding: 7px 14px; font-size: 12px;
       text-decoration: none; white-space: nowrap; }
a.dl:hover { background: rgba(255,255,255,.12); }
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
tr.change td.code.l, tr.change td.code.r { background: #fef3c7; }
tr.del td.code.l { background: #fecaca; }
tr.add td.code.r { background: #bbf7d0; }
tr.del td.code.r, tr.add td.code.l { background: #e5e7eb; }
tr.gap td { background: #eef2f7; color: #6b7280; text-align: center;
            font-size: 11px; padding: 3px; }
.note { color: #6b7280; font-size: 12px; margin: 6px 0 0; }
.fnote2 { font-weight: normal; font-size: 12px; color: #1f2937;
          background: #eff6ff; border-left: 3px solid #93c5fd;
          padding: 2px 8px; border-radius: 0 4px 4px 0; margin-left: 8px; }
.top { position: fixed; right: 18px; bottom: 18px; background: #1e3a5f;
       color: #fff; border-radius: 20px; padding: 8px 16px; font-size: 12px;
       text-decoration: none; box-shadow: 0 2px 6px rgba(0,0,0,.25); }
.ovl { position: fixed; inset: 0; background: rgba(15,23,42,.55);
       display: none; align-items: center; justify-content: center;
       z-index: 50; }
.ovl.show { display: flex; }
.modal { background: #fff; border-radius: 10px; padding: 22px 26px;
         max-width: 760px; width: 92%; max-height: 88vh; overflow: auto;
         box-shadow: 0 8px 30px rgba(0,0,0,.35); }
.modal h2 { font-size: 16px; margin: 0 0 10px; color: #b45309; }
.mlead { font-size: 13px; margin: 0 0 12px; }
.rcards { display: flex; gap: 12px; }
.rcard { flex: 1; border: 1px solid #e2e8f0; border-radius: 8px;
         padding: 8px 12px; background: #fff; }
.rcard.bad { border-color: #fecaca; background: #fff7f7; }
.rcard.warn { border-color: #fde68a; background: #fffcf0; }
.rcard .fig { text-align: center; margin: 6px 0 2px; }
.rcard svg { width: 100%; height: auto; max-height: 96px; }
.rcard h3 { font-size: 12.5px; margin: 0 0 2px; }
.rcard.bad h3 { color: #b91c1c; }
.rcard.warn h3 { color: #b45309; }
.rcard p { font-size: 11.5px; color: #334155; margin: 4px 0 0;
           line-height: 1.6; }
.mfoot { font-size: 12px; color: #475569; margin: 12px 0 14px; }
.mbtns { display: flex; justify-content: flex-end; gap: 10px; }
.mcancel { font-size: 13px; color: #475569; text-decoration: none;
           padding: 8px 16px; border-radius: 6px; }
.mcancel:hover { background: #f1f5f9; }
.mgo { font-size: 13px; color: #fff; background: #1e3a5f;
       text-decoration: none; padding: 8px 16px; border-radius: 6px; }
.mgo:hover { background: #2b4a6f; }
"""

_STATUS_JP = {'M': ('変更', 'tagM'), 'A': ('追加', 'tagA'),
              'D': ('削除', 'tagD'), 'R': ('移動', 'tagM')}

# PR 本文の「## 変更ファイルの説明」の 1 行 (- パス — 説明)。
# 生成のゆらぎに備え、区切り (— ― -- - : :) と `パス` の装飾は緩く許容する
_NOTES_HEADING = re.compile(r'^#{2,4}\s*変更ファイルの説明\s*$', re.M)
_NOTE_LINE = re.compile(
    r'^\s*[-*]\s+[`*]*([^\s`*]+)[`*]*[ \t　]*'
    r'(?:—|―|--|-|:|：)?[ \t　]*(\S.*)$')


def parse_file_notes(body):
    """PR 本文からファイルごとの変更説明を取り出す.

    提出時に Claude が生成した「## 変更ファイルの説明」節を読む。
    節が無い・形式が崩れている行は黙って無視する (表示は説明なしになる
    だけで、差分ビューワ自体は常に成立させる)。
    戻り値: {リポジトリ相対パス: 説明}
    """
    m = _NOTES_HEADING.search(body or '')
    if not m:
        return {}
    notes = {}
    for line in body[m.end():].splitlines():
        if re.match(r'^#{1,6}\s', line):    # 次の見出しで節が終わる
            break
        lm = _NOTE_LINE.match(line)
        if not lm:
            continue
        path = lm.group(1).replace('\\', '/')
        if path.startswith(DISPLAY_ROOT + '/'):
            path = path[len(DISPLAY_ROOT) + 1:]
        notes[path] = lm.group(2).strip()
    return notes


def _display_path(path):
    return '%s/%s' % (DISPLAY_ROOT, path)


def _file_section(anchor, path, stat_html, inner, note=None):
    # 説明はファイル名と同じ行の右横に置く (見出しだけで内容がわかるように)
    note_html = (' <span class="fnote2">%s</span>' % html.escape(note)) \
        if note else ''
    return ('<div class="card" id="%s"><h2>%s %s%s</h2>%s</div>'
            % (anchor, html.escape(_display_path(path)), stat_html,
               note_html, inner))


# 資料「バージョン管理と同時開発のしくみ」4.2 の 3 リスクカード。
# イラスト (SVG)・文面・配色は資料の正本と完全に同一にそろえる
# (cls, タイトル, 本文 HTML, SVG)
_RISK_CARDS = [
    ('bad', '① 基点の消滅',
     '親 PR が却下確定・取り下げで消えると枝ごと削除され、基点 SHA が'
     '辿れなくなる。A-1-1 は「基点となる版が履歴に見つかりません」で'
     '<b>提出不能</b>。安定版から移植し直しになる',
     '<svg width="180" height="74" viewBox="0 0 180 74" '
     'font-family="IPAPGothic, sans-serif">'
     '<line x1="8" y1="52" x2="80" y2="52" stroke="#1e3a5f" '
     'stroke-width="2.5"/>'
     '<path d="M 40 50 C 60 32, 78 28, 96 28" stroke="#d97706" '
     'stroke-width="2" fill="none" stroke-dasharray="4 3" opacity="0.6"/>'
     '<circle cx="100" cy="28" r="6" fill="#d97706" opacity="0.45"/>'
     '<line x1="92" y1="20" x2="108" y2="36" stroke="#dc2626" '
     'stroke-width="2.5"/>'
     '<line x1="108" y1="20" x2="92" y2="36" stroke="#dc2626" '
     'stroke-width="2.5"/>'
     '<text x="100" y="12" font-size="7.5" text-anchor="middle" '
     'fill="#dc2626">A-1 が削除</text>'
     '<path d="M 106 26 C 126 22, 138 26, 148 30" stroke="#94a3b8" '
     'stroke-width="1.5" fill="none" stroke-dasharray="3 3"/>'
     '<circle cx="155" cy="32" r="6" fill="#059669"/>'
     '<text x="155" y="48" font-size="7.5" text-anchor="middle" '
     'fill="#475569">A-1-1</text>'
     '<text x="95" y="70" font-size="7.4" text-anchor="middle" '
     'fill="#64748b">土台を失って宙に浮き、提出不能に</text>'
     '</svg>'),
    ('warn', '② 親リリース後の縮退',
     '親が squash マージされると main には「同内容だが別 SHA」が入る。'
     '子の合流はたいてい自動解決するが、autofix 等で親の最終形が違うと'
     '<b>衝突化しやすい</b> (→ 統合待ちフローで解消)',
     '<svg width="180" height="74" viewBox="0 0 180 74" '
     'font-family="IPAPGothic, sans-serif">'
     '<line x1="8" y1="52" x2="172" y2="52" stroke="#1e3a5f" '
     'stroke-width="2.5"/>'
     '<circle cx="120" cy="52" r="6" fill="#0d9488"/>'
     '<text x="118" y="68" font-size="7.2" text-anchor="middle" '
     'fill="#475569">A-1&#8242; (squash 済・別 SHA)</text>'
     '<path d="M 26 50 C 44 30, 60 24, 76 24" stroke="#d97706" '
     'stroke-width="2" fill="none"/>'
     '<circle cx="78" cy="24" r="5" fill="#d97706"/>'
     '<text x="46" y="14" font-size="7.4" text-anchor="middle" '
     'fill="#92400e">A-1 (旧)</text>'
     '<circle cx="112" cy="20" r="5" fill="#059669"/>'
     '<path d="M 83 23 C 93 21, 100 20, 107 20" stroke="#059669" '
     'stroke-width="2" fill="none"/>'
     '<text x="130" y="14" font-size="7.4" text-anchor="middle" '
     'fill="#14532d">A-1-1</text>'
     '<path d="M 117 24 C 132 30, 142 38, 148 46" stroke="#dc2626" '
     'stroke-width="1.6" fill="none" stroke-dasharray="4 3"/>'
     '<text x="152" y="36" font-size="8.5" fill="#dc2626" '
     'font-weight="bold">!</text>'
     '</svg>'),
    ('warn', '③ レビュー責任の曖昧化',
     '親が未承認のまま子を承認すると「親の内容込みの承認か」が不明瞭に。'
     '<b>親の決着 (リリース or 却下) を待ってから子を審査</b>するのが原則',
     '<svg width="180" height="74" viewBox="0 0 180 74" '
     'font-family="IPAPGothic, sans-serif">'
     '<rect x="22" y="36" width="120" height="22" rx="4" fill="#fffbeb" '
     'stroke="#b45309" stroke-width="1.2"/>'
     '<text x="82" y="50" font-size="7.6" text-anchor="middle" '
     'fill="#92400e">A-1 (親) &#8212; まだ未承認</text>'
     '<rect x="22" y="8" width="120" height="22" rx="4" fill="#f0fdf4" '
     'stroke="#059669" stroke-width="1.2"/>'
     '<text x="82" y="22" font-size="7.6" text-anchor="middle" '
     'fill="#14532d">A-1-1 (子) &#8212; 承認した</text>'
     '<path d="M 150 12 l5 6 l9 -10" stroke="#059669" stroke-width="2.5" '
     'fill="none"/>'
     '<text x="156" y="52" font-size="12" text-anchor="middle" '
     'fill="#dc2626" font-weight="bold">?</text>'
     '<text x="88" y="70" font-size="7.4" text-anchor="middle" '
     'fill="#64748b">親の分まで承認したことになる&#8230;?</text>'
     '</svg>'),
]


def _dl_readme(meta, changed, dl_deleted):
    lines = [
        '#%d の確認用データ' % meta['number'],
        'タイトル: %s' % meta['title'],
        '提出者: %s' % meta.get('author', '?'),
    ]
    if meta.get('beta'):
        lines.append('β版: %s' % meta['beta'])
    lines += [
        '',
        '[フォルダ構成]',
        ' 一式/%s      … β版まるごと。そのまま動作チェックに使えます'
        % DISPLAY_ROOT,
        ' 変更のみ/%s  … この提出で追加・変更されたファイルだけ'
        ' (中身チェック用)' % DISPLAY_ROOT,
        '',
        'この提出で追加・変更されたファイル:',
    ]
    lines += [' - %s/%s' % (DISPLAY_ROOT, p) for p in changed]
    if dl_deleted:
        lines += ['', 'この提出で削除されたファイル (ZIP には含まれません):']
        lines += [' - %s/%s' % (DISPLAY_ROOT, p) for p in dl_deleted]
    lines += [
        '',
        '※ 一式には版の情報 (version.json) を意図的に含めていません。',
        '  そのため、このフォルダを ZIP にしてもマネージャーから提出は',
        '  できません (誤って開発の土台にしない仕組みです)。',
        '',
        '【注意】',
        'このデータを保存して開発の土台にすることには、資料',
        '「バージョン管理と同時開発のしくみ」4.2 に示した 3 つのリスク',
        '(①基点の消滅 ②親リリース後の縮退 ③レビュー責任の曖昧化) が',
        'あります。このリスクが理解できる人のみ利用してください。',
    ]
    return '\n'.join(lines)


def _download_link(meta, dl_files, dl_deleted, workrepo, head_ref):
    """「更新データをダウンロード」ボタンとリスク確認モーダルの HTML.

    β版一覧に置くと誤って開発の土台にする人が出るため、差分ビューワの
    右上にだけ置き、クリック時に資料 4.2 の 3 リスクをイラスト付きで
    確認してからダウンロードさせる。ZIP には「一式」(動作チェック用の
    β版まるごと) と「変更のみ」(中身チェック用) の両方を入れる。
    戻り値: (ボタン HTML, モーダル HTML)。出さないときは ('', '')。
    """
    if not dl_files:
        return '', ''
    # 一式 = 提出後の配布相当ファイル全部 (version.json はあえて入れない
    # ことで、この一式をそのまま提出の土台にできないようにする)
    full = []
    for p in run_git(['ls-tree', '-r', '--name-only', head_ref],
                     cwd=workrepo).splitlines():
        p = p.strip()
        if not p or not _is_dist_scope(p):
            continue
        data = _git_bytes(workrepo, ['show', '%s:%s' % (head_ref, p)])
        if data is not None:
            full.append((p, data))
    if sum(len(d) for _, d in full) + sum(len(d) for _, d in dl_files) \
            > MAX_DL_MB * 1024 * 1024:
        return '', ''
    changed = [p for p, _ in dl_files]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('更新データについて.txt',
                    _dl_readme(meta, changed, dl_deleted))
        for path, data in full:
            zf.writestr('一式/%s/%s' % (DISPLAY_ROOT, path), data)
        for path, data in dl_files:
            zf.writestr('変更のみ/%s/%s' % (DISPLAY_ROOT, path), data)
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')

    btn = ('<a class="dl" id="dlbtn" href="#">'
           '⬇ 更新データをダウンロード</a>')
    cards = ''.join(
        '<div class="rcard %s"><h3>%s</h3><div class="fig">%s</div>'
        '<p>%s</p></div>' % (cls, title, svg, text)
        for cls, title, text, svg in _RISK_CARDS)
    modal = (
        '<div class="ovl" id="dlovl"><div class="modal">'
        '<h2>ダウンロードの前に【注意】</h2>'
        '<p class="mlead">ここからダウンロードしたデータを保存して'
        '<b>開発の土台にする</b>ことには、資料「バージョン管理と同時開発の'
        'しくみ」4.2 に示した 3 つのリスクがあります。</p>'
        '<div class="rcards">%s</div>'
        '<p class="mfoot">このリスクが理解できる人のみダウンロードして'
        'ください。動作チェックやプログラムの中身の確認に使うのは'
        '問題ありません (ZIP には動作チェック用の「一式」と中身チェック用の'
        '「変更のみ」が入っています)。</p>'
        '<div class="mbtns">'
        '<a class="mcancel" id="dlcancel" href="#">キャンセル</a>'
        '<a class="mgo" id="dlgo" download="#%d_確認用.zip" '
        'href="data:application/zip;base64,%s">'
        '⬇ リスクを理解した上でダウンロード</a>'
        '</div></div></div>'
        '<script>(function(){'
        'var o=document.getElementById("dlovl");'
        'document.getElementById("dlbtn").addEventListener("click",'
        'function(e){e.preventDefault();o.classList.add("show");});'
        'document.getElementById("dlcancel").addEventListener("click",'
        'function(e){e.preventDefault();o.classList.remove("show");});'
        'document.getElementById("dlgo").addEventListener("click",'
        'function(){o.classList.remove("show");});'
        'o.addEventListener("click",function(e){'
        'if(e.target===o)o.classList.remove("show");});'
        '})();</script>'
        % (cards, meta['number'], b64))
    return btn, modal


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
    notes = meta.get('notes') or {}

    sums, sections = [], []
    dl_files, dl_deleted = [], []
    n_add = n_del = 0
    for status, path in ((s[0], p) for s, p in files):
        jp, cls = _STATUS_JP.get(status, ('変更', 'tagM'))
        # 更新データ ZIP 用に提出後の内容を控える (削除ファイルは一覧のみ)
        if status == 'D':
            dl_deleted.append(path)
        else:
            data = _git_bytes(workrepo, ['show',
                                         '%s:%s' % (head_ref, path)])
            if data is not None:
                dl_files.append((path, data))
        note = notes.get(path)
        anchor = 'f-%s' % path.replace('/', '-').replace('.', '-')
        ext = ('.' + path.rsplit('.', 1)[-1].lower()) if '.' in path else ''
        old = [] if status == 'A' else _file_lines(workrepo, mb, path)
        new = [] if status == 'D' else _file_lines(workrepo, head_ref, path)

        if ext in BINARY_EXTS or old is None or new is None:
            sums.append((jp, cls, path, anchor, '(表示対象外)', note))
            sections.append(_file_section(
                anchor, path, '',
                '<p class="note">画像・バイナリ形式のため差分表示の'
                '対象外です。</p>', note))
            continue

        rows = side_by_side(old, new)
        changed = sum(1 for r in rows if r[0] != 'same')
        adds = sum(1 for r in rows if r[0] in ('add', 'change') and r[3])
        dels = sum(1 for r in rows if r[0] in ('del', 'change') and r[1])
        n_add += adds
        n_del += dels
        stat = ('<span class="stat"><b class="add">+%d</b> / '
                '<b class="del">-%d</b></span>' % (adds, dels))
        sums.append((jp, cls, path, anchor, stat, note))

        if changed > MAX_CHANGED_LINES:
            sections.append(_file_section(
                anchor, path, stat,
                '<p class="note">%d 行の変更があります。大きすぎるため'
                '全体は省略しました。</p>' % changed, note))
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
            % ''.join(body), note))

    sum_rows = ''.join(
        '<tr><td width="60"><span class="%s">%s</span></td>'
        '<td><a href="#%s">%s</a>%s</td><td width="120">%s</td></tr>'
        % (cls, jp, anchor, html.escape(_display_path(path)),
           (' <span class="fnote2">%s</span>' % html.escape(note))
           if note else '', stat)
        for jp, cls, path, anchor, stat, note in sums)
    notes_caveat = ('<br>※ 各ファイルの青枠の説明は提出時に自動生成された'
                    'ものです (その後の自動修正・統合は反映されません)。'
                    if notes else '')
    dl_html, dl_modal = _download_link(meta, dl_files, dl_deleted,
                                       workrepo, head_ref)
    beta = ('・ β版 %s ' % html.escape(meta['beta'])) if meta.get('beta') \
        else ''
    return (
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<title>#%d の差分</title><style>%s</style></head><body>'
        '<header><div><h1>#%d %s</h1>'
        '<div class="meta">提出者: %s %s・ 基点との比較 '
        '(追加 <b>+%d</b> 行 / 削除 <b>-%d</b> 行)</div></div>%s</header>'
        '<div class="wrap">'
        '<h2 class="sec-h">&lt;1&gt; フォルダ比較 '
        '(変更されたファイル %d 件)</h2>'
        '<div class="card"><table class="sum">%s</table>'
        '<p class="note">ファイル名クリックでその場所へ移動します。'
        '左 = 変更前 (現在の正式版) / 右 = 変更後 (提出内容)。'
        '<span style="background:#fef3c7">&nbsp;黄&nbsp;</span>'
        '= 変更された行 (左が変更前・右が変更後)、'
        '<span style="background:#fecaca">&nbsp;赤&nbsp;</span>'
        '= 削除された行、'
        '<span style="background:#bbf7d0">&nbsp;緑&nbsp;</span>'
        '= 追加された行。%s</p></div>'
        '<h2 class="sec-h">&lt;2&gt; ファイル比較</h2>%s</div>'
        '<a class="top" href="#">▲ 先頭へ</a>%s</body></html>'
        % (meta['number'], _CSS, meta['number'],
           html.escape(meta['title']), html.escape(meta['author']), beta,
           n_add, n_del, dl_html, len(files), sum_rows, notes_caveat,
           ''.join(sections), dl_modal))


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
    # 提出時に生成されたファイル別説明 (PR 本文)。取れなくても表示は続行
    try:
        body = ghcli.run_gh(['pr', 'view', str(pr['number']),
                             '--repo', paths.repo_slug(config),
                             '--json', 'body', '--jq', '.body'])
    except ghcli.GhError:
        log.warning('PR #%s の本文取得に失敗 (説明なしで表示します)',
                    pr['number'])
        body = ''
    text = build_html(
        {'number': pr['number'], 'title': pr['title'],
         'author': pr.get('author', '?'), 'beta': beta_tag,
         'notes': parse_file_notes(body)},
        'origin/%s' % base, 'origin/%s' % pr['branch'], workrepo)
    out = os.path.join(tempfile.gettempdir(),
                       'mgtkit_diff_%d.html' % pr['number'])
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    return out
