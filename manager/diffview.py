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
import contextlib
import difflib
import html
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import zipfile

from . import diffcache, ghcli, paths
from .gitcli import GitError, ensure_work_repo, run_git
from .submit import _is_dist_scope, workrepo_dir

log = logging.getLogger(__name__)

MAX_HUNK_CONTEXT = 3      # 変更行の前後に見せる行数
MAX_CHANGED_LINES = 800   # これを超えるファイルは省略表示
MAX_DL_MB = 20            # 確認用データ ZIP の埋め込み上限 (超えたらボタン非表示)

BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip',
               '.xlsx', '.pyc', '.exe', '.dll'}

# 表示上のパスの先頭に付けるフォルダ名。メンバーの手元では
# C:\Users\(自分)\mgtkit\... に展開されるため、その見え方に合わせる
DISPLAY_ROOT = 'mgtkit'

# ダウンロードの印はアプリ全体で Material の ⬇ (トレイ + 矢印) に統一
# (更新ログの「ZIP を保存」と同じ見た目をリンクの文字色で描く)
_DL_ICON_SVG = (
    '<svg width="13" height="13" viewBox="0 0 13 13" '
    'style="vertical-align:-2px;margin-right:3px">'
    '<path d="M6.5 1 v7 M3.2 5 l3.3 3.3 L9.8 5" stroke="currentColor" '
    'stroke-width="1.8" fill="none" stroke-linecap="round" '
    'stroke-linejoin="round"/>'
    '<path d="M1.5 11.5 h10" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"/></svg>')


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


def _decode_lines(data):
    """ファイル内容 (bytes) を行リストに。バイナリ等は None."""
    if data is None:
        return None
    for enc in ('utf-8', 'cp932'):
        try:
            return data.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return None


def _file_lines(workrepo, rev, path):
    """rev 時点のファイル内容を行リストで。バイナリ等は None."""
    return _decode_lines(_git_bytes(workrepo, ['show',
                                               '%s:%s' % (rev, path)]))


def batch_blobs(workrepo, specs):
    """複数の "リビジョン:パス" の中身を git 1 回でまとめて取り出す.

    速度の要 (管理者指示 2026-08)。ファイルごとに git show を起動すると
    Windows では 1 回 100〜400 ms (ウイルス対策の走査) かかり、変更
    ファイル数に比例して待ち時間が伸びる。cat-file --batch なら
    ファイル数によらず git は 1 回で済む。

    戻り値: {spec: bytes}。取れなかったものは入らない。
    """
    specs = [s for s in specs if s]
    if not specs:
        return {}
    # --batch の入力は 1 行 1 件。改行を含むパスだけは個別に取る
    inline = [s for s in specs if '\n' not in s]
    out = {}
    for s in set(specs) - set(inline):
        data = _git_bytes(workrepo, ['show', s])
        if data is not None:
            out[s] = data
    if not inline:
        return out
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
    proc = None
    try:
        proc = subprocess.run(
            ['git', 'cat-file', '--batch'], cwd=workrepo,
            input=('\n'.join(inline) + '\n').encode('utf-8'),
            capture_output=True, timeout=300, **kw)
    except (OSError, subprocess.TimeoutExpired):
        log.warning('まとめ読みに失敗 (1 件ずつ取り直します)',
                    exc_info=True)
    if proc is not None:
        # 応答は "<oid> <型> <バイト数>\n<中身>\n"、無いものは
        # "<名前> missing"。異常終了でも読めたぶんは活かす
        buf, pos = proc.stdout, 0
        for spec in inline:
            nl = buf.find(b'\n', pos)
            if nl < 0:
                break
            head = buf[pos:nl].decode('utf-8', 'replace').split()
            if len(head) != 3 or not head[2].isdigit():
                pos = nl + 1              # missing 等はそのまま読み飛ばす
                continue
            size = int(head[2])
            out[spec] = buf[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1       # 中身の後ろの改行ぶん
    # 取れなかったぶんは 1 件ずつ取り直す (まとめ読みが失敗しても
    # 「全ファイルがバイナリ扱い」「中身の無い ZIP」にはしない)
    rest = [s for s in inline if s not in out]
    if rest:
        log.warning('まとめ読みで %d 件取れなかったため個別に取得します',
                    len(rest))
        for s in rest:
            data = _git_bytes(workrepo, ['show', s])
            if data is not None:
                out[s] = data
    return out


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
tr.change td.code.l { background: #fecaca; }
tr.change td.code.r, tr.add td.code.r { background: #bbf7d0; }
tr.del td.code.l { background: #d3ddee; color: #64748b;
                   text-decoration: line-through; }
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
        ' 一式/%s      … β版まるごと (最新の正式版 + この提出の変更)。'
        'そのまま動作チェックに使えます' % DISPLAY_ROOT,
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


def refresh_base(model, workrepo):
    """一式 ZIP の土台 (最新の正式版) を最新化する。成功なら True.

    表示 (build_model) は fetch を省くことがあるため、確認用データを
    作る直前にここで正式版を取り直す。他スレッドの先読みと git 操作が
    重ならないよう同じ鍵で直列化する。失敗は False (手元の状態で続行
    できるが、呼び出し側はその旨をユーザーに伝えること)。
    """
    with _fetch_lock:
        try:
            base = model['base_ref'].split('/', 1)[-1]
            run_git(['fetch', 'origin', base], cwd=workrepo, timeout=300)
            return True
        except GitError:
            log.warning('正式版の最新化に失敗 (手元の状態で作成します)')
            return False


def build_review_zip(model, workrepo, max_mb=None, fetch_base=False):
    """確認用データ ZIP のバイト列を組み立てる (HTML・ダイアログ共通).

    一式 = 「最新の正式版 + この提出の変更」(= β版の中身)。
    提出ブランチの木をそのまま使うと、基点時点の古いファイル
    (その後 main 側で削除・移動されたもの) が紛れ込むため、
    最新の正式版を土台に変更ファイルだけを重ねる。
    version.json はあえて入れない (この一式をそのまま提出の土台に
    できないようにするため)。
    max_mb 指定時、超えたら None (HTML への base64 埋め込み用の上限)。
    fetch_base=True で土台 (正式版) を先に最新化する。
    """
    if fetch_base:
        refresh_base(model, workrepo)
    # 変更ファイルの中身は表示時ではなくここで取り出す (表示の高速化)。
    # 一式に入れる正式版のファイルとまとめて git 1 回で読む
    base_paths = [p.strip() for p
                  in run_git(['ls-tree', '-r', '--name-only',
                              model['base_ref']],
                             cwd=workrepo).splitlines()
                  if p.strip() and _is_dist_scope(p.strip())]
    blobs = batch_blobs(
        workrepo,
        ['%s:%s' % (model['head_ref'], p) for p in model['dl_paths']]
        + ['%s:%s' % (model['base_ref'], p) for p in base_paths])
    dl_files = []
    for p in model['dl_paths']:
        data = blobs.get('%s:%s' % (model['head_ref'], p))
        if data is not None:
            dl_files.append((p, data))
    if model['dl_paths'] and not dl_files:
        # 中身を 1 つも取り出せないまま「確認用データ」を配ると
        # 空の ZIP を渡すことになるため、作らずに知らせる
        raise GitError('確認用データを作成できませんでした。'
                       '時間をおいて再試行してください。')
    dl_deleted = model['dl_deleted']
    full = {}
    for p in base_paths:
        data = blobs.get('%s:%s' % (model['base_ref'], p))
        if data is not None:
            full[p] = data
    for path, data in dl_files:      # この提出で追加・変更されたファイル
        full[path] = data
    for p in dl_deleted:             # この提出で削除されたファイル
        full.pop(p, None)
    if max_mb is not None and sum(len(d) for d in full.values()) \
            + sum(len(d) for _, d in dl_files) > max_mb * 1024 * 1024:
        return None
    changed = [p for p, _ in dl_files]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('確認用データについて.txt',
                    _dl_readme(model, changed, dl_deleted))
        for path, data in sorted(full.items()):
            zf.writestr('一式/%s/%s' % (DISPLAY_ROOT, path), data)
        for path, data in dl_files:
            zf.writestr('変更のみ/%s/%s' % (DISPLAY_ROOT, path), data)
    return buf.getvalue()


def _download_link(model, workrepo):
    """「β版データ (確認用) をダウンロード」ボタンとリスク確認モーダルの HTML.

    β版一覧に置くと誤って開発の土台にする人が出るため、差分ビューワの
    右上にだけ置き、クリック時に資料 4.2 の 3 リスクをイラスト付きで
    確認してからダウンロードさせる。ZIP には「一式」(動作チェック用の
    β版まるごと) と「変更のみ」(中身チェック用) の両方を入れる。
    戻り値: (ボタン HTML, モーダル HTML)。出さないときは ('', '')。
    """
    if not model['dl_paths']:
        return '', ''
    data = build_review_zip(model, workrepo, max_mb=MAX_DL_MB,
                            fetch_base=True)
    if data is None:
        return '', ''
    b64 = base64.b64encode(data).decode('ascii')

    btn = ('<a class="dl" id="dlbtn" href="#">'
           + _DL_ICON_SVG + ' β版データ (確認用) をダウンロード</a>')
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
        '%s リスクを理解した上でダウンロード</a>'
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
        % (cards, model['number'], b64, _DL_ICON_SVG))
    return btn, modal


def collect_model(meta, base_ref, head_ref, workrepo, merge_base=None):
    """差分モデル (表示技術に依存しないデータ) を組み立てる.

    HTML (render_html) とアプリ内ダイアログ (diffdialog) の共通データ。
    戻り値 dict:
      number/title/author/beta: meta の転載 / has_notes: 説明の有無
      n_add/n_del: 追加・削除行数の合計
      files: [{path, status, status_jp, tag_cls, note, kind,
               adds, dels, changed, rows}]
        kind: 'diff' | 'binary' | 'big'。rows は kind=='diff' のみ
        (collapse_context 済みの行ペア)
      dl_paths: [path] / dl_deleted: [path] / base_ref / head_ref:
        確認用データ ZIP の構築用 (中身の取り出しは保存時まで遅延)

    merge_base を与えると比較の基点をそれにする (スナップショットが
    持つ分岐点 SHA。ローカルでの merge-base 計算を省く)。
    """
    mb = merge_base or run_git(['merge-base', base_ref, head_ref],
                               cwd=workrepo).strip()
    out = run_git(['diff', '--name-status', '%s..%s' % (mb, head_ref)],
                  cwd=workrepo)
    raw = [line.split('\t', 1) for line in out.splitlines()
           if line.strip()]
    notes = meta.get('notes') or {}

    def _ext(path):
        return ('.' + path.rsplit('.', 1)[-1].lower()) if '.' in path else ''

    # 比較に要るファイルの中身を git 1 回でまとめて取る (起動回数を
    # ファイル数に比例させない)。拡張子でバイナリと分かるものは読まない
    specs = []
    for status, path in ((s[0], p) for s, p in raw):
        if _ext(path) in BINARY_EXTS:
            continue
        if status != 'A':
            specs.append('%s:%s' % (mb, path))
        if status != 'D':
            specs.append('%s:%s' % (head_ref, path))
    blobs = batch_blobs(workrepo, specs)

    files, dl_paths, dl_deleted = [], [], []
    n_add = n_del = 0
    for status, path in ((s[0], p) for s, p in raw):
        jp, cls = _STATUS_JP.get(status, ('変更', 'tagM'))
        # 確認用データ ZIP の対象を控える (中身の取り出しは保存時)
        if status == 'D':
            dl_deleted.append(path)
        else:
            dl_paths.append(path)
        note = notes.get(path)
        ext = _ext(path)
        old = ([] if status == 'A'
               else _decode_lines(blobs.get('%s:%s' % (mb, path))))
        new = ([] if status == 'D'
               else _decode_lines(blobs.get('%s:%s' % (head_ref, path))))
        entry = {'path': path, 'status': status, 'status_jp': jp,
                 'tag_cls': cls, 'note': note, 'kind': 'diff',
                 'adds': 0, 'dels': 0, 'changed': 0, 'rows': None}

        if ext in BINARY_EXTS or old is None or new is None:
            entry['kind'] = 'binary'
            files.append(entry)
            continue

        rows = side_by_side(old, new)
        entry['changed'] = sum(1 for r in rows if r[0] != 'same')
        entry['adds'] = sum(1 for r in rows
                            if r[0] in ('add', 'change') and r[3])
        entry['dels'] = sum(1 for r in rows
                            if r[0] in ('del', 'change') and r[1])
        n_add += entry['adds']
        n_del += entry['dels']
        if entry['changed'] > MAX_CHANGED_LINES:
            entry['kind'] = 'big'
        else:
            entry['rows'] = collapse_context(rows)
        files.append(entry)

    return {'number': meta['number'], 'title': meta['title'],
            'author': meta.get('author', '?'), 'beta': meta.get('beta'),
            'has_notes': bool(notes), 'n_add': n_add, 'n_del': n_del,
            'files': files, 'dl_paths': dl_paths,
            'dl_deleted': dl_deleted, 'base_ref': base_ref,
            'head_ref': head_ref}


def build_html(meta, base_ref, head_ref, workrepo):
    """差分ビューワの HTML 全体を組み立てて文字列で返す.

    meta: dict(number, title, author, beta=None)
    base_ref/head_ref: 比較する 2 つの git リファレンス
    """
    return render_html(collect_model(meta, base_ref, head_ref, workrepo),
                       workrepo)


def render_html(model, workrepo):
    """collect_model のモデルから差分ビューワ HTML を組み立てる."""
    sums, sections = [], []
    for e in model['files']:
        path, note = e['path'], e['note']
        jp, cls = e['status_jp'], e['tag_cls']
        anchor = 'f-%s' % path.replace('/', '-').replace('.', '-')
        if e['kind'] == 'binary':
            sums.append((jp, cls, path, anchor, '(表示対象外)', note))
            sections.append(_file_section(
                anchor, path, '',
                '<p class="note">画像・バイナリ形式のため差分表示の'
                '対象外です。</p>', note))
            continue
        stat = ('<span class="stat"><b class="add">+%d</b> / '
                '<b class="del">-%d</b></span>' % (e['adds'], e['dels']))
        sums.append((jp, cls, path, anchor, stat, note))
        if e['kind'] == 'big':
            sections.append(_file_section(
                anchor, path, stat,
                '<p class="note">%d 行の変更があります。大きすぎるため'
                '全体は省略しました。</p>' % e['changed'], note))
            continue
        body = []
        for kind, lno, lt, rno, rt in e['rows']:
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
                    if model['has_notes'] else '')
    dl_html, dl_modal = _download_link(model, workrepo)
    beta = ('・ β版 %s ' % html.escape(model['beta'])) \
        if model.get('beta') else ''
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
        '<span style="background:#fecaca">&nbsp;赤&nbsp;</span>'
        '= 変更前、'
        '<span style="background:#bbf7d0">&nbsp;緑&nbsp;</span>'
        '= 変更後・追加、'
        '<span style="background:#d3ddee; text-decoration:line-through">'
        '&nbsp;紺&nbsp;</span>'
        '= 削除された行。%s</p></div>'
        '<h2 class="sec-h">&lt;2&gt; ファイル比較</h2>%s</div>'
        '<a class="top" href="#">▲ 先頭へ</a>%s</body></html>'
        % (model['number'], _CSS, model['number'],
           html.escape(model['title']), html.escape(model['author']),
           beta, model['n_add'], model['n_del'], dl_html,
           len(model['files']), sum_rows, notes_caveat,
           ''.join(sections), dl_modal))


def _has_commit(workrepo, sha):
    """コミット sha が手元にあるか (あれば fetch を省ける)."""
    if not sha:
        return False
    try:
        run_git(['cat-file', '-e', '%s^{commit}' % sha], cwd=workrepo)
        return True
    except GitError:
        return False


def build_model(pr, config=None, workrepo=None, beta_tag=None):
    """提出 pr の差分モデルを取得する (アプリ内ダイアログ用).

    pr: reviews.list_pending の要素 (number/title/author/branch のほか、
    あれば head_sha/fork_sha/body を高速化に使う)。
    戻り値: (model, workrepo)。workrepo は確認用データ ZIP の構築と
    「ブラウザで開く」(render_html) に使い回す。

    速度の要はネットワーク往復を消すこと (管理者指示 2026-08):
    - 提出の先端 (head_sha) と分岐点 (fork_sha) が手元にあれば
      fetch しない。無いときだけ 1 回で fetch する
    - 説明文 (PR 本文) はスナップショットに同乗した body を使い、
      無いとき (旧キャッシュ) だけ gh で取りに行く
    """
    if workrepo is None:
        with _fetch_lock:       # クローンは書き込みなので直列化する
            workrepo = ensure_work_repo(paths.repo_slug(config),
                                        workrepo_dir(config), fetch=False)
    base = (config or {}).get('base_branch', 'main')
    head_sha, fork_sha = pr.get('head_sha'), pr.get('fork_sha')
    if not (_has_commit(workrepo, head_sha)
            and _has_commit(workrepo, fork_sha)):
        with _fetch_lock:       # 取得も書き込み (読み出しは並行して安全)
            run_git(['fetch', 'origin', base, pr['branch']], cwd=workrepo,
                    timeout=300)
    head_ref = (head_sha if _has_commit(workrepo, head_sha)
                else 'origin/%s' % pr['branch'])
    # 分岐点はスナップショットの fork_sha (GitHub 側の真値) を優先。
    # ただし先端も head_sha に固定できたときだけ (提出が置き換えられて
    # いて先端がブランチ参照に落ちた場合、古い分岐点と組み合わせると
    # 他人の変更が混ざった差分になるため、merge-base 計算に戻す)
    merge_base = (fork_sha if head_ref == head_sha
                  and _has_commit(workrepo, fork_sha) else None)
    # 提出時に生成されたファイル別説明 (PR 本文)。取れなくても表示は続行
    body = pr.get('body')
    if body is None:
        try:
            body = ghcli.run_gh(['pr', 'view', str(pr['number']),
                                 '--repo', paths.repo_slug(config),
                                 '--json', 'body', '--jq', '.body'])
        except ghcli.GhError:
            log.warning('PR #%s の本文取得に失敗 (説明なしで表示します)',
                        pr['number'])
            body = ''
    meta = {'number': pr['number'], 'title': pr['title'],
            'author': pr.get('author', '?'), 'beta': beta_tag,
            'notes': parse_file_notes(body)}
    model = collect_model(meta, 'origin/%s' % base, head_ref, workrepo,
                          merge_base=merge_base)
    # 先端と分岐点の両方を SHA で固定できたか。固定できていないものは
    # 手元の main の状態しだいで内容が変わるので保存の対象にしない
    model['pinned'] = bool(head_ref == head_sha and merge_base)
    return model, workrepo


# 先読みキャッシュ: 一覧が届いたら裏でモデルを組んでおき、「差分」
# クリック時は組み立て済みをそのまま出す (体感ほぼ 0 秒)。キーは
# 番号:先端:分岐点 なので、提出が更新されれば自然に作り直す
_MODEL_CACHE_MAX = 8
_model_cache = {}
# 提出ごとの組み立て鍵 (同じ提出の二重組み立てだけを防ぐ)。全体を
# 1 本の鍵にすると、他の提出の先読み中のクリックが待たされるため
_build_locks = {}
_build_guard = threading.Lock()
# 差分表示まわりの workrepo への書き込み (clone / fetch) を直列化する。
# 読み出し (cat-file / diff / show) は鍵を取らない (他モジュールの
# 提出・統合処理は別途 fetch するため、完全な排他ではない)
_fetch_lock = threading.Lock()


def model_key(pr):
    """先読みキャッシュのキー。作れないときは None (キャッシュ対象外)."""
    if not pr.get('head_sha') or not pr.get('fork_sha'):
        # 分岐点が分からないと手元の main の状態しだいで内容が変わる
        # ため、キャッシュ (特に再起動をまたぐ保存) の対象にしない
        return None
    return '%s-%s-%s' % (pr['number'], pr['head_sha'][:12],
                         pr['fork_sha'][:12])


@contextlib.contextmanager
def _building(key):
    """同じ提出の二重組み立てだけを防ぐ (提出ごとの鍵).

    使っている人数を数えておき、誰もいなくなった時点で鍵を捨てる
    (常駐アプリなので溜めない)。「捨ててよいか」を数で判断するため、
    鍵を取る直前に他スレッドが捨ててしまう隙間がない。
    """
    with _build_guard:
        lock, users = _build_locks.get(key) or (threading.Lock(), 0)
        _build_locks[key] = (lock, users + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _build_guard:
            lock, users = _build_locks[key]
            if users <= 1:
                _build_locks.pop(key, None)
            else:
                _build_locks[key] = (lock, users - 1)


def _remember(key, hit):
    """メモリキャッシュへの登録と古いものの追い出し (複数スレッド安全)."""
    with _build_guard:
        _model_cache[key] = hit
        while len(_model_cache) > _MODEL_CACHE_MAX:
            _model_cache.pop(next(iter(_model_cache)), None)


def _from_disk(key, pr, config):
    """保存済みモデルを使えるなら (model, workrepo) を返す.

    先端と分岐点の両方が作業クローンに無いと確認用データを取り出せず、
    比較の基点も再現できないため、その場合は使わず組み直す。
    """
    saved = diffcache.load(key, config)
    if saved is None:
        return None
    with _fetch_lock:
        repo = ensure_work_repo(paths.repo_slug(config),
                                workrepo_dir(config), fetch=False)
    if not (_has_commit(repo, pr.get('head_sha'))
            and _has_commit(repo, pr.get('fork_sha'))):
        return None
    return saved, repo


def build_model_cached(pr, config=None, beta_tag=None):
    """build_model のキャッシュ付き版 (先読みとクリックの共通入口).

    メモリ → ディスク → 組み立て の順に探す。鍵は提出ごとなので、
    別の提出を先読み中でもクリックした提出はすぐ組み立てに入れる。
    """
    key = model_key(pr)
    if key is None:
        return build_model(pr, config, beta_tag=beta_tag)
    with _building(key):
        hit = _model_cache.get(key)
        if hit is None:
            hit = _from_disk(key, pr, config)
            if hit is None:
                hit = build_model(pr, config, beta_tag=beta_tag)
                if hit[0].get('pinned'):
                    diffcache.save(key, hit[0], config)
            _remember(key, hit)
    model, workrepo = hit
    # β版タグだけは呼び出しごとに違い得るので差し替えて返す
    return dict(model, beta=beta_tag), workrepo


def write_html_from_model(model, workrepo):
    """モデルから HTML を一時フォルダへ書き出す (「ブラウザで開く」用)."""
    out = os.path.join(tempfile.gettempdir(),
                       'mgtkit_diff_%d.html' % model['number'])
    with open(out, 'w', encoding='utf-8') as f:
        f.write(render_html(model, workrepo))
    return out


def write_diff_html(pr, config=None, workrepo=None, beta_tag=None):
    """提出 pr の差分ビューワ HTML を一時フォルダに書き出す.

    pr: reviews.list_pending の要素 (number/title/author/branch を使用)。
    戻り値: HTML ファイルのパス (ブラウザで開くのは呼び出し側)。
    """
    model, workrepo = build_model(pr, config, workrepo, beta_tag)
    return write_html_from_model(model, workrepo)
