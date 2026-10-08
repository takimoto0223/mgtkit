# -*- coding: utf-8 -*-
"""提出に含まれる「単独で開くファイル」 (mgtkit の画面からは開けないもの).

単独で動く計算ツールの HTML やスクリプトは、β版を起動しても mgtkit の
画面 (タブ) からはたどり着けない。β版は普段見えないフォルダ
(.manager/beta/<版>/mgtkit/) に展開されるので、確認する人はファイルの
場所も分からない (提出 #204 のフィードバック)。β版のカードに
「単独の〇〇を開く」を種類ごとに出して、ファイルを直接開けるようにする。

単独のファイル = 次のどれにも当たらないフォルダの中のファイル:
- 本体直下のファイル (app.py など。本体そのもの)
- 本体の共通フォルダ (templates/ static/ data/ など) と配布しないフォルダ
- タブのフォルダ (routes.py がある = 画面から開ける)
そのうち、人が開いて使う種類 (KINDS) だけを挙げる。単独のツールの部品
(PARTS: .js・.css・.json・.csv) は挙げないが、部品だけが変わった提出でも
動きは変わるので、同じフォルダの HTML を手元のアプリから拾って挙げる。
"""
import os
import pathlib
import subprocess
import sys
import webbrowser

# (呼び名, 拡張子, 開き方)。並びはボタンを並べる順。開き方:
# browser = ブラウザで開く / folder = ファイルの場所を開いて選んだ状態に
# する (スクリプトはダブルクリックで開くと実行されてしまうため) /
# app = 関連付けられたアプリで開く (無ければ場所を開く)
KINDS = (
    ('HTML', ('.html', '.htm'), 'browser'),
    ('スクリプト', ('.py',), 'folder'),
    ('メモ', ('.md', '.txt'), 'app'),
    ('DXF', ('.dxf',), 'app'),
)

# 単独のツールの部品 (HTML から読み込まれる側)。人が開くものではない
PARTS = ('.js', '.css', '.json', '.csv')

# 種類が合っても人が開くものではないファイル
SKIP_NAMES = frozenset({'requirements.txt'})

# 本体の共通フォルダ・配布しないフォルダ (tabreg.NOT_TABS を含む。
# test_manager_standalone が照合する)。ここのファイルは単独のツールではない
APP_DIRS = frozenset({'templates', 'static', 'data', 'readme', 'manager',
                      'tests', 'docs', 'scripts'})


def _kind(path):
    if os.path.basename(path) in SKIP_NAMES:
        return None
    ext = os.path.splitext(path)[1].lower()
    for i, (label, exts, how) in enumerate(KINDS):
        if ext in exts:
            return i, label, how
    return None


def _outside_app(path, tab_dirs, app_dir):
    parts = path.split('/')
    if len(parts) < 2:
        return False
    top = parts[0]
    if top in APP_DIRS or top.startswith('.') or top in tab_dirs:
        return False
    return not (app_dir and os.path.isfile(
        os.path.join(app_dir, top, 'routes.py')))


def find(paths, app_dir=None):
    """変更ファイルのパス一覧 (リポジトリ相対、/ 区切り) → 単独のファイル.

    戻り値: [{'path', 'kind', 'how'}] (KINDS の順 → パスの順)。
    app_dir を渡すと、そこに routes.py がある (= すでにタブの) フォルダを
    除き、部品だけが変わったフォルダの HTML をそこから拾う。提出の中に
    routes.py があるフォルダは app_dir が無くても除く。
    """
    paths = sorted({p.replace('\\', '/') for p in paths or []})
    tab_dirs = {p.split('/')[0] for p in paths
                if p.count('/') == 1 and p.endswith('/routes.py')}
    picked = set()
    for p in paths:
        if not _outside_app(p, tab_dirs, app_dir):
            continue
        if _kind(p) is not None:
            picked.add(p)
        elif (app_dir and os.path.splitext(p)[1].lower() in PARTS):
            folder = p.rsplit('/', 1)[0]
            local = os.path.join(app_dir, *folder.split('/'))
            if os.path.isdir(local):
                for name in os.listdir(local):
                    q = folder + '/' + name
                    if _kind(q) and _kind(q)[2] == 'browser':
                        picked.add(q)
    out = []
    for p in picked:
        i, label, how = _kind(p)
        out.append((i, p, {'path': p, 'kind': label, 'how': how}))
    return [item for _, _, item in sorted(out, key=lambda t: t[:2])]


def groups(files):
    """種類ごとにまとめる: [(呼び名, 開き方, [ファイル])] (KINDS の順)."""
    out = []
    for f in files:
        if out and out[-1][0] == f['kind']:
            out[-1][2].append(f)
        else:
            out.append((f['kind'], f['how'], [f]))
    return out


def button_label(kind, how, n):
    """カードのボタンの文言「単独の〇〇を開く」 (スクリプトは場所を開く)."""
    name = (' %s ' % kind) if kind.isascii() else kind
    text = ('単独の%sの場所を開く' if how == 'folder' else '単独の%sを開く') \
        % name
    return text.replace('  ', ' ') + (' (%d)' % n if n > 1 else '')


def folders(files):
    """ファイルのあるフォルダ (重複なし・出てきた順)."""
    out = []
    for f in files:
        d = f['path'].rsplit('/', 1)[0] + '/'
        if d not in out:
            out.append(d)
    return out


def local_path(app_dir, rel):
    """β版のアプリのフォルダ + リポジトリ相対パス → 手元のパス."""
    return os.path.join(app_dir, *rel.split('/'))


def open_file(path, how):
    """単独のファイルを開く。戻り値: 'opened' | 'revealed' (場所を開いた).

    関連付けられたアプリが無い種類 (.md・.dxf など) は、開く代わりに
    ファイルの場所を開く (Windows はアプリが決まっていないとエラーになる)。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if how == 'browser':
        if not webbrowser.open(pathlib.Path(path).resolve().as_uri(), new=1):
            raise OSError('ブラウザを開けませんでした')
        return 'opened'
    if how == 'folder':
        reveal(path)
        return 'revealed'
    try:
        _open_with_app(path)
        return 'opened'
    except OSError:
        reveal(path)
        return 'revealed'


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
        os.startfile(path)      # 関連付けられたアプリで開く
    elif sys.platform == 'darwin':
        subprocess.check_call(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])
