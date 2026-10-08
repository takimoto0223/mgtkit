# -*- coding: utf-8 -*-
"""提出に含まれる「単独で開くファイル」 (mgtkit の画面からは開けないもの).

単独で動く計算ツールの HTML やスクリプトは、β版を起動しても mgtkit の
画面 (タブ) からはたどり着けない。β版は普段見えないフォルダ
(.manager/beta/<版>/mgtkit/) に展開されるので、確認する人はファイルの
場所も分からない (提出 #204 のフィードバックで発覚)。β版のカードに
「単独の〇〇を開く」を出して、ファイルを直接開けるようにする。

単独のファイル = 次のどれにも当たらないフォルダの中のファイル:
- 本体直下のファイル (app.py など。本体そのもの)
- 本体の共通フォルダ (templates/ static/ data/ など) と配布しないフォルダ
- タブのフォルダ (routes.py がある = 画面から開ける)
そのうち、人が開いて使う種類 (KINDS) だけを挙げる (.json・.css・.js
などは単独のツールの部品なので挙げない)。
"""
import os
import pathlib
import subprocess
import sys
import webbrowser

# (呼び名, 拡張子, 開き方)。開き方: browser = ブラウザで開く /
# folder = フォルダを開いてファイルを選んだ状態にする (スクリプトは
# 開くと実行されてしまうため) / app = 関連付けられたアプリで開く
KINDS = (
    ('HTML', ('.html', '.htm'), 'browser'),
    ('スクリプト', ('.py',), 'folder'),
    ('メモ', ('.md', '.txt'), 'app'),
    ('CSV', ('.csv',), 'app'),
    ('DXF', ('.dxf',), 'app'),
)

# 本体の共通フォルダ・配布しないフォルダ (tabreg.NOT_TABS と
# release.yml の除外)。ここに置かれたファイルは単独のツールではない
APP_DIRS = frozenset({'templates', 'static', 'data', 'readme', 'manager',
                      'tests', 'docs', 'scripts'})


def _kind(path):
    ext = os.path.splitext(path)[1].lower()
    for i, (label, exts, how) in enumerate(KINDS):
        if ext in exts:
            return i, label, how
    return None


def find(paths, app_dir=None):
    """変更ファイルのパス一覧 (リポジトリ相対、/ 区切り) → 単独のファイル.

    戻り値: [{'path', 'kind', 'how'}] (種類の順 → パスの順)。
    app_dir を渡すと、そこに routes.py がある (= すでにタブの) フォルダも
    除く。提出の中に routes.py があるフォルダは app_dir が無くても除く。
    """
    paths = [p.replace('\\', '/') for p in paths or []]
    tab_dirs = {p.split('/')[0] for p in paths
                if p.count('/') == 1 and p.endswith('/routes.py')}
    out = []
    for p in sorted(set(paths)):
        parts = p.split('/')
        if len(parts) < 2:
            continue
        top = parts[0]
        if top in APP_DIRS or top.startswith('.') or top in tab_dirs:
            continue
        if app_dir and os.path.isfile(os.path.join(app_dir, top,
                                                   'routes.py')):
            continue
        kind = _kind(p)
        if kind is None:
            continue
        out.append((kind[0], p, {'path': p, 'kind': kind[1],
                                 'how': kind[2]}))
    return [item for _, _, item in sorted(out, key=lambda t: t[:2])]


def button_label(files):
    """カードのボタンの文言。「単独の〇〇を開く」 (種類が混ざればファイル)."""
    kinds = []
    for f in files:
        if f['kind'] not in kinds:
            kinds.append(f['kind'])
    name = kinds[0] if len(kinds) == 1 else 'ファイル'
    sep = ' ' if name.isascii() else ''
    return '単独の%s%s%sを開く' % (sep, name, sep)


def local_path(app_dir, rel):
    """β版のアプリのフォルダ + リポジトリ相対パス → 手元のパス."""
    return os.path.join(app_dir, *rel.split('/'))


def open_file(path, how):
    """単独のファイルを開く (開き方は KINDS の 3 つ)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if how == 'browser':
        webbrowser.open(pathlib.Path(path).resolve().as_uri(), new=1)
    elif how == 'folder':
        reveal(path)
    else:
        _open_with_app(path)


def reveal(path):
    """ファイルのあるフォルダを開き、そのファイルを選んだ状態にする."""
    if sys.platform.startswith('win'):
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path])
    else:
        subprocess.Popen(['xdg-open', os.path.dirname(path)])


def _open_with_app(path):
    if sys.platform.startswith('win'):
        os.startfile(path)      # noqa: 関連付けられたアプリで開く
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])
