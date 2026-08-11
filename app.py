# -*- coding: utf-8 -*-
"""mgtkit ローカルブラウザアプリ (Flask).

MIDAS mgt ファイルを入力として、モデル図/応力図PDF・S造断面検定・
TeX表・DXF を生成するローカルWebアプリ。

起動:
    python app.py
    → ブラウザで http://127.0.0.1:8765 を開く

出力は mgt ファイルと同じフォルダの ./mgtkit_out/ に保存される。
UI: templates/index.html + static/app.js (three.js は CDN)。
"""

import io
import os
import sys
import traceback
import contextlib
import threading
import tempfile
import zipfile
import datetime
import re

import math

import numpy as np

from flask import (Flask, request, jsonify, send_file, render_template)

# mgtkit パッケージ (app.py は mgtkit/ 内にあるため親ディレクトリを追加)
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from mgtkit.mgt import (mgtopen_node, mgtopen_element, mgtopen_plate,
                        mgtopen_thickness,
                        mgtopen_group, mgtopen_FL, mgtopen_wood_materials,
                        mgtopen_material, mgtopen_RCbeam,
                        mgtopen_RCcolumn)
from mgtkit.section import mgtopen_section
from mgtkit.util import (space_erace, find_index, pick_text,
                         loadtxt_tolerant)
from mgtkit.draw_model import plot_model
from mgtkit.draw_stress import plot_stress, _default_case_names
from mgtkit.draw_ratio import plot_ratio
from mgtkit.ratio_pipeline import (run_steel_check,
                                   export_maxratio_xlsx,
                                   export_full_ratio_xlsx,
                                   default_case_types)
from mgtkit.w_check import w_al_match, w_al_names
from mgtkit.export_tex import export_model_tex, export_ratio_detail_tex
from mgtkit.export_dxf import export_dxf

app = Flask(__name__)

# 生成ファイルのホワイトリスト (これ以外は /api/file で配信しない)
_ALLOWED_FILES = set()
_ALLOWED_LOCK = threading.Lock()

# matplotlib (pyplot) はスレッドセーフでないため描画系は直列化する
_PLOT_LOCK = threading.Lock()

# アップロード一時フォルダ。β版など複数インスタンス起動時は
# MGTKIT_UPLOAD_DIR で分離できる (アプリマネージャーが設定する)
_UPLOAD_DIR = (os.environ.get('MGTKIT_UPLOAD_DIR', '').strip()
               or os.path.join(tempfile.gettempdir(), 'mgtkit_uploads'))

# 配布チャネル (stable / beta)。β版はマネージャーが MGTKIT_CHANNEL=beta で
# 起動し、画面上部にβ版バナーを表示する
_CHANNEL = os.environ.get('MGTKIT_CHANNEL', 'stable').strip() or 'stable'

# 直近の検定結果キャッシュ (検定比図の生成用。検定条件が一致する場合のみ再利用)
_CHECK_CACHE = {'key': None, 'result': None}


def _check_cache_key(p):
    """検定条件のキャッシュキー (入力ファイルはmtime込みで変更検知)."""
    keys = ['mgt_path', 'beam_stress_path', 'truss_stress_path',
            'select_length', 'judge_H', 'H_beam_RCsupport', 'limit_sec_no',
            'c_up_STKR', 'c_up_BCR', 'c_up_BCP', 'c_up_BSH',
            'case_types', 'wal_up', 'w_material_map', 'w_panel_co',
            'qup_mode', 'qup_beam', 'qup_wall', 'qup_src', 'rc',
            'src_cover', 'select_unit',
            'pile_cover', 'pc', 'wall_stress_path', 'plate_stress_path',
            'plate_up', 'pl_long']
    d = {k: p.get(k) for k in keys}
    for f in ('mgt_path', 'beam_stress_path', 'truss_stress_path',
              'wall_stress_path', 'plate_stress_path'):
        pth = str(p.get(f) or '').strip()
        if pth and os.path.isfile(pth):
            d[f + '_mtime'] = os.path.getmtime(pth)
    _pc = p.get('pc') or {}
    for f in ('pc_stress_path', 'pc_cable_path'):
        pth = str(_pc.get(f) or '').strip()
        if pth and os.path.isfile(pth):
            d['pc_' + f + '_mtime'] = os.path.getmtime(pth)
    import json as _json
    return _json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)

# mgt記述由来の問題を示すキーワード (エラーメッセージ判定用)
_MGT_ISSUE_KEYWORDS = (
    'グループ', '通り芯', 'NODE_LIST', '定義ミス', '見つかりません',
    '不揃い', 'ダミー材が混在', '荷重ケース', '列数が', '入力荷重数過多',
    '復元できません', 'データベース名指定',
)


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------

def _register_file(path):
    """生成ファイルを配信許可リストへ登録し、配信URLを返す."""
    apath = os.path.abspath(path)
    with _ALLOWED_LOCK:
        _ALLOWED_FILES.add(apath)
    from urllib.parse import quote
    return '/api/file?path=' + quote(apath)


def _out_dir_for(mgt_path, sub=None, out_base=None):
    """出力フォルダ ./mgtkit_out/<sub>/ を返す (無ければ作成).

    基点は out_base (UIの「出力先フォルダ」欄) があればそのフォルダ、
    無ければ mgt と同じフォルダ。sub はタブ別サブフォルダ
    (model/stress/ratio_tex/ratio_plot/tex/dxf/qr)。
    """
    base = str(out_base or '').strip()
    if base:
        base = os.path.abspath(base)
        if not os.path.isdir(base):
            raise ValueError('出力先フォルダが存在しません: %s '
                             '(存在するフォルダを指定してください)' % base)
    else:
        base = os.path.dirname(os.path.abspath(mgt_path))
    d = os.path.join(base, 'mgtkit_out')
    if sub:
        d = os.path.join(d, sub)
    os.makedirs(d, exist_ok=True)
    return d


def _is_upload_tmp(path):
    """パスがアップロード一時フォルダ(mgtkit_uploads)配下か."""
    try:
        ap = os.path.abspath(str(path))
        return os.path.commonpath(
            [ap, os.path.abspath(_UPLOAD_DIR)]) == os.path.abspath(_UPLOAD_DIR)
    except (ValueError, TypeError):
        return False


def _out_dir(p, sub):
    """リクエスト p からタブ別出力フォルダを解決する.

    優先順位:
      1. 「出力先フォルダ」欄 (out_base)
      2. mgtの場所 (一時フォルダのアップロードコピーでない場合)
      3. リクエスト中の他の入力パス (beam_stress等) のうち
         一時フォルダ外にある実ファイルの場所 ← アップロード時の自動推定
      4. 一時フォルダ (その旨と対処を注記)
    """
    out_base = str(p.get('out_base') or '').strip()
    if not out_base and _is_upload_tmp(p.get('mgt_path')):
        cands = []
        for k in ('beam_stress_path', 'truss_stress_path',
                  'plate_stress_path', 'wall_stress_path',
                  'reaction_path', 'deformation_path'):
            cands.append(p.get(k))
        cands.extend(p.get('path_hints') or [])
        for c in cands:
            c = str(c or '').strip()
            if c and os.path.isfile(c) and not _is_upload_tmp(c):
                out_base = os.path.dirname(os.path.abspath(c))
                print('注記: mgtがアップロードコピー(一時フォルダ)のため、'
                      '出力先を %s に自動設定しました (入力ファイルの場所から'
                      '推定。変更する場合は共通欄の「出力先フォルダ」へ入力)。'
                      % out_base)
                break
        else:
            print('注記: mgtをアップロードで読み込んだため、生成物は一時'
                  'フォルダ(%s)に保存されました。元のデータフォルダへ出力'
                  'するには、共通欄の「出力先フォルダ」に事例フォルダを'
                  '指定するか、mgtのフルパスを直接入力してください。'
                  % _UPLOAD_DIR)
    return _out_dir_for(p['mgt_path'], sub, out_base or p.get('out_base'))


def _check_input_file(path, label):
    """入力ファイルの存在チェック。問題があればエラーメッセージを返す."""
    if not path or not str(path).strip():
        return '%sのパスを入力してください。' % label
    if not os.path.isfile(path):
        return '%sが見つかりません: %s' % (label, path)
    return None


def _error_response(exc):
    """例外を日本語の要点メッセージへ変換して返す (トレースバックは端末のみ)."""
    traceback.print_exc()
    msg = str(exc)
    if isinstance(exc, KeyError):
        msg = exc.args[0] if exc.args else msg
    if any(k in str(msg) for k in _MGT_ISSUE_KEYWORDS):
        text = ('mgtファイル(または応力ファイル)の記述に問題がある可能性が'
                'あります: %s' % msg)
    else:
        text = '処理中にエラーが発生しました: %s (%s)' % (
            msg, type(exc).__name__)
    return jsonify({'error': text}), 500


@contextlib.contextmanager
def _capture_notes(notes_out):
    """ライブラリのprint出力を捕捉し、重複除去して notes_out へ格納する."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield
    seen = []
    for line in buf.getvalue().splitlines():
        line = line.strip()
        if line and line not in seen:
            seen.append(line)
    notes_out.extend(seen[:60])
    if len(seen) > 60:
        notes_out.append('... (他 %d 件の注記)' % (len(seen) - 60))


def _derived_section_notes():
    """直近の mgtopen_section でDB名から寸法復元した断面の注記一覧を返す."""
    return ['断面番号%dはDB名(%s)から寸法復元しました'
            '(JIS標準フィレット半径 r=%dmm を付与)'
            % (d['secno'], d['db_name'], d['r'])
            for d in getattr(mgtopen_section, 'restored_sections', [])]


def _group_info(mgt_path):
    """グループ一覧を鉛直構面/水平構面(伏図)/節点未登録に分類して返す."""
    node = mgtopen_node(mgt_path)
    axis_name, axis_element, axis_node = mgtopen_group(mgt_path)
    node_no = node[:, 0] if node.size else np.zeros(0)
    groups = []
    for i, name in enumerate(axis_name):
        nn = np.atleast_1d(np.asarray(axis_node[i], dtype=float)).ravel()
        kind = 'empty'
        if nn.size >= 2:
            idx = np.atleast_1d(find_index(node_no, nn))
            idx = np.asarray(idx)
            idx = idx[idx >= 0] if idx.size else idx
            if np.size(idx) >= 2:
                z = node[np.asarray(idx, dtype=int), 3]
                # 10mmまでの座標ずれは同一レベル (水平構面) とみなす
                kind = ('vertical' if (z.max() - z.min()) > 0.01
                        else 'floor')
        groups.append({'name': space_erace(str(name)),
                       'n_nodes': int(nn.size), 'kind': kind})
    return groups


def _story_guess_from_groups(mgt_path, z_points, tol=0.005):
    """フロアグループから層番号の初期値を推定する.

    節点が平面上で面的に広がるグループ (フロア: 1F/2F/RF等) の節点Zに
    一致するレベルへ、グループの最低Zの昇順に 1,2,... を振る。
    同一グループ内の複数レベルは同じ層番号 (勾配屋根のRF等)。
    どのグループにも属さないレベル (トラス下弦材など) は None (対象外)。
    フロアグループが見つからなければ None を返す (呼び出し側で従来の
    全レベル昇順にフォールバック)。
    """
    node = mgtopen_node(mgt_path)
    axis_name, _axis_element, axis_node = mgtopen_group(mgt_path)
    if not np.size(node) or not axis_name:
        return None
    pos = {int(r[0]): (float(r[1]), float(r[2]), float(r[3]))
           for r in np.atleast_2d(node)}
    floors = []
    for i, name in enumerate(axis_name):
        nn = np.atleast_1d(np.asarray(axis_node[i], dtype=float)).ravel()
        pts = [pos[int(v)] for v in nn if int(v) in pos]
        if len(pts) < 3:
            continue
        xy = np.asarray([(q0[0], q0[1]) for q0 in pts], dtype=float)
        q = xy - xy.mean(axis=0)
        w = np.linalg.eigvalsh(q.T @ q)
        if float(np.sqrt(max(float(w[0]), 0.0) / len(pts))) < 0.05:
            continue  # 平面上で一直線 (鉛直構面) は除外
        zs = sorted(set(float(q0[2]) for q0 in pts))
        floors.append((min(zs), space_erace(str(name)), zs))
    if not floors:
        return None

    def _is_roof(name):
        s = str(name).upper()
        return (s.startswith('RF') or s.startswith('PH')
                or s.startswith('ROOF') or s in ('R',)
                or (s.startswith('R') and s[1:2].isdigit())
                or ('屋根' in str(name)) or ('小屋' in str(name)))

    floors.sort(key=lambda t: t[0])
    guess = [None] * len(z_points)
    used = []
    roofs = []
    num = 0
    for _zmin, gname, zs in floors:
        if _is_roof(gname):
            roofs.append(gname)
            continue  # 屋根レベルは層の下端にしない (小屋組は最上層に含む)
        hit = [k for k, v in enumerate(z_points)
               if guess[k] is None
               and any(abs(float(v) - z0) <= tol for z0 in zs)]
        if not hit:
            continue
        num += 1
        used.append(gname)
        for k in hit:
            guess[k] = num
    if num == 0:
        return None
    msg = ('層構成をフロアグループ (%s) から自動設定しました。'
           % ', '.join(used))
    if roofs:
        msg += '屋根グループ (%s) は層にしていません。' % ', '.join(roofs)
    msg += ('どのフロアにも属さないレベル (トラス下弦材など) は'
            '空欄=対象外です。必要に応じて手動で修正してください。')
    print(msg)
    return guess


def _height_candidates(mgt_path):
    """高さ寸法レベルの候補一覧を返す.

    全節点の distinct Z (1mm相当で丸め) を昇順に列挙し、各レベルに
    柱端点レベルかどうか(col)と *STORY の階名(fl, あれば)を付ける。
    col は「鉛直部材の下端レベル + 最上部の柱頭レベル」とする
    (勾配屋根では束・柱頭が斜面に沿って多数の中間レベルを作るため、
    上端は最上部のみ採用して階高寸法チェーンを読みやすくする)。
    長さ単位がm(座標値が小さい)なら小数3桁, mmなら整数で丸める。
    """
    node = mgtopen_node(mgt_path)
    element = mgtopen_element(mgt_path)
    if not np.size(node):
        return []
    node = np.atleast_2d(node)
    zmax = float(np.max(np.abs(node[:, 3])))
    nd = 3 if zmax < 1000.0 else 0  # m単位なら小数3桁(=1mm), mm単位なら整数

    def _r(v):
        return round(float(v), 3) if nd else float(int(round(float(v))))

    pos = {}
    for r in range(node.shape[0]):
        pos[int(node[r, 0])] = (float(node[r, 1]), float(node[r, 2]),
                                float(node[r, 3]))
    col_z = set()
    col_top = None
    ele = np.atleast_2d(element) if np.size(element) else np.zeros((0, 6))
    for r in range(ele.shape[0] if ele.shape[1] >= 5 else 0):
        ni, nj = int(ele[r, 3]), int(ele[r, 4])
        if ni not in pos or nj not in pos:
            continue
        xi, yi, zi = pos[ni]
        xj, yj, zj = pos[nj]
        dz = abs(zi - zj)
        if dz > 1e-9 and math.hypot(xi - xj, yi - yj) < 1e-3 * dz:
            col_z.add(_r(min(zi, zj)))          # 柱下端レベル
            zt = _r(max(zi, zj))                # 柱頭は最上部のみ採用
            col_top = zt if col_top is None else max(col_top, zt)
    if col_top is not None:
        col_z.add(col_top)
    fl = {}
    try:
        for row in mgtopen_FL(mgt_path):
            name = re.sub(r'^NAME=', '', str(row[0])).strip()
            z = float(row[1])
            if math.isfinite(z) and name:
                fl[_r(z)] = name
    except Exception:  # noqa: BLE001  *STORYが変則でも候補提示は続行
        pass
    zset = sorted({_r(node[r, 3]) for r in range(node.shape[0])})
    return [{'z': z, 'col': (z in col_z), 'fl': fl.get(z)} for z in zset]


def _parse_heights(p):
    """リクエストの heights (高さ寸法レベル選択) を float列 or None へ."""
    heights = p.get('heights')
    if not heights:
        return None
    return [float(v) for v in heights]


# ---------------------------------------------------------------------------
# エンドポイント: ページ・ファイル配信・アップロード
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    # app.jsのmtimeをクエリに付けてブラウザキャッシュの旧JS混在を防ぐ
    try:
        ver = int(os.path.getmtime(os.path.join(_HERE, 'static', 'app.js')))
    except OSError:
        ver = 0
    return render_template('index.html', app_ver=ver, channel=_CHANNEL)


@app.route('/api/file')
def api_file():
    path = os.path.abspath(request.args.get('path', ''))
    with _ALLOWED_LOCK:
        allowed = path in _ALLOWED_FILES
    if not allowed or not os.path.isfile(path):
        return jsonify({'error': '指定ファイルは配信対象ではありません。'}), 404
    ext = os.path.splitext(path)[1].lower()
    mime = {'.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.'
                     'spreadsheetml.sheet',
            '.dxf': 'application/dxf'}.get(ext, 'application/octet-stream')
    as_attach = request.args.get('dl') == '1' or ext not in ('.pdf', '.txt')
    return send_file(path, mimetype=mime, as_attachment=as_attach,
                     download_name=os.path.basename(path))


# ---------------------------------------------------------------------------
# エンドポイント: ネイティブのファイル/フォルダ選択 (自己完結運用)
# ---------------------------------------------------------------------------

_PICK_CHILD = (
    "import sys, tkinter as tk\n"
    "from tkinter import filedialog\n"
    "root = tk.Tk(); root.withdraw()\n"
    "root.attributes('-topmost', True)\n"
    "kind, initdir, filt = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "ft = {'mgt': [('MIDAS mgt', '*.mgt'), ('All', '*.*')],\n"
    "      'txt': [('Text', '*.txt'), ('All', '*.*')],\n"
    "      'all': [('All', '*.*')]}[filt]\n"
    "if kind == 'dir':\n"
    "    r = filedialog.askdirectory(initialdir=initdir or None,\n"
    "                                parent=root)\n"
    "else:\n"
    "    r = filedialog.askopenfilename(initialdir=initdir or None,\n"
    "                                   filetypes=ft, parent=root)\n"
    "sys.stdout.buffer.write((r or '').encode('utf-8'))\n"
)


@app.route('/api/pick_path', methods=['POST'])
def api_pick_path():
    """Windows標準のファイル/フォルダ選択ダイアログを開き実パスを返す.

    ブラウザのfile inputはセキュリティ上実パスを渡せず、一時フォルダへの
    コピーが必要になる。本アプリはブラウザとサーバーが同一PCで動く
    自己完結運用のため、サーバー側でネイティブダイアログを開いて
    実パスを取得する (tkinterはFlaskスレッドと相性が悪いため子プロセス)。
    キャンセル時は path='' を返す。
    """
    import subprocess
    p = request.get_json(force=True)
    kind = 'dir' if p.get('kind') == 'dir' else 'file'
    filt = str(p.get('filter') or 'all')
    if filt not in ('mgt', 'txt', 'all'):
        filt = 'all'
    initial = str(p.get('initial') or '').strip()
    initdir = ''
    if initial:
        d = initial if os.path.isdir(initial) else os.path.dirname(initial)
        if os.path.isdir(d):
            initdir = d
    try:
        flags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
        out = subprocess.run(
            [sys.executable, '-c', _PICK_CHILD, kind, initdir, filt],
            capture_output=True, timeout=600, creationflags=flags)
        path = out.stdout.decode('utf-8', errors='replace').strip()
        return jsonify({'path': os.path.normpath(path) if path else ''})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/scan_case_dir', methods=['POST'])
def api_scan_case_dir():
    """事例フォルダを走査し mgt・応力ファイル群の実パスを返す.

    同名候補が複数ある場合は更新日時が最新のものを採用する。
    """
    import glob as _glob
    p = request.get_json(force=True)
    d = str(p.get('dir') or '').strip()
    if not os.path.isdir(d):
        return jsonify({'error': 'フォルダが見つかりません: %s' % d}), 400

    def newest(pat):
        hits = [h for h in _glob.glob(os.path.join(d, pat))
                if os.path.isfile(h)]
        return max(hits, key=os.path.getmtime) if hits else ''

    return jsonify({
        'mgt_path': newest('*.mgt'),
        'beam_stress_path': newest('*beam_stress*.txt'),
        'truss_stress_path': newest('*truss_stress*.txt'),
        'plate_stress_path': newest('*plate_stress*.txt'),
        'wall_stress_path': newest('*wall_stress*.txt'),
        'reaction_path': newest('*reaction*.txt'),
        'deformation_path': newest('*deformation*.txt'),
    })


@app.route('/api/upload', methods=['POST'])
def api_upload():
    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify({'error': 'ファイルが選択されていません。'}), 400
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    name = os.path.basename(f.filename)
    path = os.path.join(_UPLOAD_DIR, name)
    f.save(path)
    return jsonify({'path': path})


@app.route('/api/open_folder', methods=['POST'])
def api_open_folder():
    """出力先/アップロードフォルダをエクスプローラで開く (ローカル専用).

    開けるのはアップロードフォルダと、生成ファイルを配信登録した
    出力フォルダのみ (任意パスを開かせないための制限)。
    """
    p = request.get_json(force=True) or {}
    if p.get('target') == 'uploads':
        d = os.path.abspath(_UPLOAD_DIR)
        os.makedirs(d, exist_ok=True)
    else:
        d = os.path.abspath(str(p.get('path', '')))
        up = os.path.abspath(_UPLOAD_DIR)
        ok = (d == up or d.startswith(up + os.sep))
        if not ok:
            with _ALLOWED_LOCK:
                ok = any(os.path.dirname(f) == d for f in _ALLOWED_FILES)
        if not ok:
            return jsonify({'error': 'このフォルダは開けません: %s' % d}), 400
        if not os.path.isdir(d):
            return jsonify({'error': 'フォルダが見つかりません: %s' % d}), 404
    try:
        os.startfile(d)
    except Exception as exc:  # noqa: BLE001 (Windows以外はos.startfile無し)
        return jsonify({'error': 'フォルダを開けませんでした: %s' % exc}), 500
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# エンドポイント: モデル情報 (グループ一覧・3D表示用データ)
# ---------------------------------------------------------------------------

@app.route('/api/mgt_info', methods=['POST'])
def api_mgt_info():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        groups = _group_info(p['mgt_path'])
        empty = [g['name'] for g in groups if g['kind'] == 'empty']
        note = ''
        if empty:
            note = ('mgt記述の注意: グループ %s は *GROUP の NODE_LIST が'
                    '未登録(または節点1点のみ)のため構面として描画できません。'
                    % ', '.join(empty))
        heights = []
        try:
            heights = _height_candidates(p['mgt_path'])
        except Exception:  # noqa: BLE001  高さ候補は補助情報なので失敗許容
            traceback.print_exc()
        try:  # DB名指定断面の寸法復元注記 (断面情報は補助なので失敗許容)
            with contextlib.redirect_stdout(io.StringIO()):
                mgtopen_section(p['mgt_path'])
            sec_notes = _derived_section_notes()
            if sec_notes:
                note = (note + '\n' if note else '') + '\n'.join(sec_notes)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return jsonify({'groups': groups, 'note': note,
                        'heights': heights,
                        'n_z_all': len(heights),
                        'n_z_col': sum(1 for h in heights if h['col']),
                        'mgt_path': os.path.abspath(p['mgt_path'])})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/model3d', methods=['POST'])
def api_model3d():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        node = mgtopen_node(p['mgt_path'])
        element = mgtopen_element(p['mgt_path'])
        plate = mgtopen_plate(p['mgt_path'])
        sections, section_no, section_name = mgtopen_section(p['mgt_path'])
        sec_names = {}
        sn = np.atleast_1d(np.asarray(section_no, dtype=float)).ravel()
        for i in range(sn.size):
            sec_names[int(sn[i])] = space_erace(str(section_name[i]))

        pos = {}
        for r in range(node.shape[0]):
            pos[int(node[r, 0])] = (float(node[r, 1]), float(node[r, 2]),
                                    float(node[r, 3]))

        lines = []      # [x1,y1,z1,x2,y2,z2] * n
        ele_nos = []
        ele_secs = []
        for r in range(element.shape[0]):
            ni, nj = int(element[r, 3]), int(element[r, 4])
            if ni not in pos or nj not in pos:
                continue
            lines.extend(pos[ni])
            lines.extend(pos[nj])
            ele_nos.append(int(element[r, 0]))
            secno = int(element[r, 2])
            ele_secs.append(sec_names.get(secno, '断面%d' % secno))

        plate_lines = []
        if np.size(plate):
            plate = np.atleast_2d(plate)
            for r in range(plate.shape[0]):
                nds = [int(v) for v in plate[r, 3:7] if int(v) != 0]
                nds = [n for n in nds if n in pos]
                for k in range(len(nds)):
                    a, b = nds[k], nds[(k + 1) % len(nds)]
                    plate_lines.extend(pos[a])
                    plate_lines.extend(pos[b])

        return jsonify({'lines': lines, 'ele_nos': ele_nos,
                        'ele_secs': ele_secs, 'plate_lines': plate_lines,
                        'n_node': int(node.shape[0])})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: モデル構成図PDF
# ---------------------------------------------------------------------------

@app.route('/api/plot_model', methods=['POST'])
def api_plot_model():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    axes = p.get('axes') or []
    floors = p.get('floors') or []
    if not axes and not floors:
        return jsonify({'error': '構面(軸組図または伏図)を1つ以上選択して'
                                 'ください。'}), 400
    try:
        notes = []
        with _PLOT_LOCK, _capture_notes(notes):
            out_dir = _out_dir(p, 'model')
            symbols = p.get('symbols') or None
            pdfs = plot_model(
                p['mgt_path'], out_dir,
                axes_select=axes,
                heights_select=_parse_heights(p),
                floors_select=(floors if floors else None),
                symbols_select=symbols,
                floor_ref_select=symbols,
                node_onoff=bool(p.get('node_onoff', False)),
                element_onoff=bool(p.get('element_onoff', True)),
                end_onoff=bool(p.get('end_onoff', False)),
                limit_sec_no=float(p.get('limit_sec_no', 9000)),
                paper_size=int(p.get('paper_size', 4)),
                fontsize=(float(p.get('f_d', 5.0)),
                          float(p.get('f_a', 6.0)),
                          float(p.get('f_t', 8.0))),
                line_location=float(p.get('line_location', 2.0)),
                axisname_location=float(p.get('axisname_location', 3.0)),
                mergins=(float(p.get('mg_l', 5.0)),
                         float(p.get('mg_r', 2.0)),
                         float(p.get('mg_t', 5.0)),
                         float(p.get('mg_b', 5.0))),
                fig_format=str(p.get('fig_format') or 'pdf'))
        if not pdfs:
            return jsonify({'error': '出力対象の図がありません。図種(断面符号'
                            '図等)のチェックと構面選択を確認してください。',
                            'notes': notes}), 400
        return jsonify({'pdfs': [{'name': os.path.basename(f),
                                  'url': _register_file(f),
                                  'path': os.path.abspath(f)}
                                 for f in pdfs],
                        'notes': notes, 'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: 応力図
# ---------------------------------------------------------------------------

@app.route('/api/load_cases', methods=['POST'])
def api_load_cases():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('beam_stress_path'), 'beam_stressファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        bs = np.atleast_2d(loadtxt_tolerant(p['beam_stress_path']))
        if bs.shape[1] < 2:
            return jsonify({'error': 'beam_stressファイルの列数が不足して'
                                     'います (8列必要)。'}), 400
        case_no = np.unique(bs[:, 1])
        names = _default_case_names(case_no)
        return jsonify({'cases': [{'no': float(c), 'name': str(n)}
                                  for c, n in zip(case_no, names)]})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/plot_stress', methods=['POST'])
def api_plot_stress():
    p = request.get_json(force=True)
    for key, label in (('mgt_path', 'mgtファイル'),
                       ('beam_stress_path', 'beam_stressファイル')):
        err = _check_input_file(p.get(key), label)
        if err:
            return jsonify({'error': err}), 400
    truss_path = (p.get('truss_stress_path') or '').strip() or None
    if truss_path:
        err = _check_input_file(truss_path, 'truss_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    plate_path = (p.get('plate_stress_path') or '').strip() or None
    if plate_path:
        err = _check_input_file(plate_path, 'plate_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    axes = p.get('axes') or None
    cases = p.get('cases') or None
    comps = p.get('components') or ['N', 'M', 'Q']
    if not comps:
        return jsonify({'error': '成分(N/M/Q)を1つ以上選択してください。'}), 400
    # 手入力のケース名 {ケース番号: 名前} (図タイトル・ファイル名に使用)
    cnames = None
    if p.get('case_names'):
        cnames = {int(float(k)): str(v).strip()
                  for k, v in dict(p['case_names']).items()
                  if str(v).strip()}
    try:
        notes = []
        with _PLOT_LOCK, _capture_notes(notes):
            out_dir = _out_dir(p, 'stress')
            pdfs = plot_stress(
                p['mgt_path'], p['beam_stress_path'], out_dir,
                truss_stress_path=truss_path,
                plate_stress_path=plate_path,
                axes_select=axes,
                heights_select=_parse_heights(p),
                load_case_names=cnames,
                load_cases=[float(c) for c in cases] if cases else None,
                symbols_select=p.get('symbols') or None,
                components=tuple(comps),
                truss_Lplot=bool(p.get('truss_Lplot', False)),
                select_unit=(0 if p.get('unit_merge', True)
                             else float('inf')),
                mscale=float(p.get('mscale', 0.0005)),
                limit_sec_no=float(p.get('limit_sec_no', 9000)),
                paper_size=int(p.get('paper_size', 4)),
                fontsize=(float(p.get('f_s', 4.0)),
                          float(p.get('f_d', 5.0)),
                          float(p.get('f_a', 6.0)),
                          float(p.get('f_t', 8.0)),
                          float(p.get('f_r', 5.0))),
                line_location=float(p.get('line_location', 2.0)),
                axisname_location=float(p.get('axisname_location', 3.0)),
                mergins=(float(p.get('mg_l', 5.0)),
                         float(p.get('mg_r', 2.0)),
                         float(p.get('mg_t', 5.0)),
                         float(p.get('mg_b', 5.0))),
                fig_format=str(p.get('fig_format') or 'pdf'))
        if not pdfs:
            return jsonify({'error': '出力対象の図がありません。構面・荷重'
                            'ケースの選択を確認してください。',
                            'notes': notes}), 400
        return jsonify({'pdfs': [{'name': os.path.basename(f),
                                  'url': _register_file(f),
                                  'path': os.path.abspath(f)}
                                 for f in pdfs],
                        'notes': notes, 'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: 生成PDFの一括ダウンロード (zip / 結合PDF)
# ---------------------------------------------------------------------------

def _page_is_landscape(page):
    """表示上の向きが横長(幅>高さ)のPDFページか判定する (/Rotate考慮)."""
    w = float(page.mediabox.width)
    h = float(page.mediabox.height)
    if int(page.rotation or 0) % 180 == 90:
        w, h = h, w
    return w > h


def _portrait_pdf_bytes(pdf_path):
    """横向きページを左90度回転して縦統一したPDFを返す.

    戻り値: (バイト列 or None, 回転ページ数)。回転不要なら (None, 0)
    (呼び出し側で元ファイルをそのまま使う)。元ファイルは変更しない。
    """
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path)
    if not any(_page_is_landscape(pg) for pg in reader.pages):
        return None, 0
    writer = PdfWriter()
    writer.append(reader)
    n_rot = 0
    for page in writer.pages:
        if _page_is_landscape(page):
            page.rotate(270)  # pypdfは時計回り指定→270=左90度回転
            n_rot += 1
    buf = io.BytesIO()
    writer.write(buf)
    writer.close()
    return buf.getvalue(), n_rot


@app.route('/api/bundle', methods=['POST'])
def api_bundle():
    """今回生成したPDF群を zip または1つの結合PDFにまとめる.

    files は今回のレスポンスで返した mgtkit_out/ 内の生成ファイルのみ許可
    (/api/file と同じホワイトリスト + mgtkit_out 限定でパストラバーサル対策)。
    """
    p = request.get_json(force=True)
    files = [os.path.abspath(str(f)) for f in (p.get('files') or [])]
    mode = str(p.get('mode', 'zip'))
    if mode not in ('zip', 'merge'):
        return jsonify({'error': 'mode は zip か merge を指定して'
                                 'ください。'}), 400
    orient = str(p.get('orient', 'mixed'))
    if orient not in ('mixed', 'portrait'):
        return jsonify({'error': 'orient は mixed か portrait を指定して'
                                 'ください。'}), 400
    if not files:
        return jsonify({'error': '対象ファイルがありません。先にPDFを生成'
                                 'してください。'}), 400
    with _ALLOWED_LOCK:
        allowed = set(_ALLOWED_FILES)
    for f in files:
        _d = os.path.dirname(f)
        _in_out = (os.path.basename(_d) == 'mgtkit_out'
                   or os.path.basename(os.path.dirname(_d)) == 'mgtkit_out')
        if f not in allowed or not os.path.isfile(f) or not _in_out:
            return jsonify({'error': '配信対象外のファイルが含まれています: '
                            '%s' % os.path.basename(f)}), 400
        if mode == 'merge' and os.path.splitext(f)[1].lower() != '.pdf':
            return jsonify({'error': 'PDF以外のファイルは結合できません: '
                            '%s' % os.path.basename(f)}), 400
    try:
        out_dir = os.path.dirname(files[0])
        prefix = re.sub(r'[^\w\-]', '_', str(p.get('prefix', 'mgtkit')))
        prefix = prefix.strip('_') or 'mgtkit'
        stamp = datetime.datetime.now().strftime('%y%m%d_%H%M')
        n_rotated = 0
        if mode == 'zip':
            out_path = os.path.join(out_dir, '%s_%s.zip' % (prefix, stamp))
            with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    data = None
                    if (orient == 'portrait'
                            and os.path.splitext(f)[1].lower() == '.pdf'):
                        data, n_rot = _portrait_pdf_bytes(f)
                        n_rotated += n_rot
                    if data is not None:
                        zf.writestr(os.path.basename(f), data)
                    else:
                        zf.write(f, os.path.basename(f))
        else:
            from pypdf import PdfWriter
            out_path = os.path.join(out_dir,
                                    '%s_%s_merged.pdf' % (prefix, stamp))
            writer = PdfWriter()
            for f in files:
                writer.append(f)
            if orient == 'portrait':
                for page in writer.pages:
                    if _page_is_landscape(page):
                        page.rotate(270)  # pypdfは時計回り指定→270=左90度
                        n_rotated += 1
            with open(out_path, 'wb') as fp:
                writer.write(fp)
            writer.close()
        return jsonify({'name': os.path.basename(out_path),
                        'url': _register_file(out_path) + '&dl=1',
                        'n_files': len(files), 'orient': orient,
                        'rotated': n_rotated})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: S造断面検定
# ---------------------------------------------------------------------------

def _build_check_json(result):
    """CheckResult をテーブル表示用のJSONへ変換する."""
    case_nos = [float(c) for c in np.atleast_1d(result.load_case_no)]
    case_names = [str(n).strip() for n in result.LCNAME]
    ncase = len(case_nos)

    def _cidx(case):
        i = find_index(np.asarray(case_nos, dtype=float), case)
        i = np.atleast_1d(np.asarray(i))
        return int(i[0]) if i.size else -1

    # 要素 → 断面番号
    ele_sec = {}
    ei = np.asarray(result.element_info)
    if ei.size:
        for r in range(ei.shape[0]):
            ele_sec[int(ei[r, 0])] = int(ei[r, 2])

    # 要素ごと・ケースごとの最大検定値
    ele_case = {}
    order = []
    br = np.asarray(result.beam_ratio)
    for r0 in range(0, br.shape[0], 3):
        ele = int(br[r0, 0])
        ci = _cidx(float(br[r0, 1]))
        v = float(np.max(br[r0:r0 + 3, 2:6]))
        if ele not in ele_case:
            ele_case[ele] = [None] * ncase
            order.append(ele)
        if ci >= 0:
            prev = ele_case[ele][ci]
            ele_case[ele][ci] = v if prev is None else max(prev, v)
    tr = np.asarray(result.truss_ratio)
    for r in range(tr.shape[0]):
        ele = int(tr[r, 0])
        ci = _cidx(float(tr[r, 1]))
        v = float(np.max(tr[r, 2:4]))
        if ele not in ele_case:
            ele_case[ele] = [None] * ncase
            order.append(ele)
        if ci >= 0:
            prev = ele_case[ele][ci]
            ele_case[ele][ci] = v if prev is None else max(prev, v)

    # 断面別: maxratios[ケース] 各行 [断面番号, 最大要素番号, 最大検定比]
    sec_nos = [int(s) for s in np.atleast_1d(result.section_nos)]
    sec_names = [str(n).strip() for n in result.section_names]
    sections = []
    for si, (sno, sname) in enumerate(zip(sec_nos, sec_names)):
        cells = [None] * ncase
        for ci in range(min(ncase, len(result.maxratios))):
            m = np.atleast_2d(np.asarray(result.maxratios[ci]))
            if m.size == 0:
                continue
            rows = np.where(m[:, 0].astype(int) == sno)[0]
            if rows.size:
                r = int(rows[0])
                if m[r, 2] > 0 or int(m[r, 1]) != 0:
                    cells[ci] = {'ele': int(m[r, 1]),
                                 'ratio': float(m[r, 2])}
        details = []
        for ele in order:
            if ele_sec.get(ele) == sno:
                details.append({'ele': int(ele), 'vals': ele_case[ele]})
        texts = []
        if si < len(result.maxratios_text):
            for ci in range(ncase):
                tlist = (result.maxratios_text[si][ci]
                         if ci < len(result.maxratios_text[si]) else [])
                texts.append([str(t) for t in tlist])
        vmax = max([c['ratio'] for c in cells if c], default=None)
        sections.append({'no': sno, 'name': sname, 'cells': cells,
                         'max': vmax, 'details': details, 'texts': texts})

    skipped = []
    for s in result.skipped:
        row = {}
        for k, v in s.items():
            if isinstance(v, (int, np.integer)):
                row[k] = int(v)
            elif isinstance(v, (float, np.floating)):
                row[k] = int(v) if float(v) == int(v) else float(v)
            else:
                row[k] = str(v)
        skipped.append(row)

    # 板・壁要素の断面別最大 (maxratios の num_section 以降の行)
    thick_table = []
    _thick_nos = np.asarray(getattr(result, 'thick_nos',
                                    np.zeros(0)), dtype=float).ravel()
    _n_sec = int(getattr(result, 'num_section', len(sec_nos)))
    _n_tw = int(getattr(result, 'num_thick_wall', 0))
    _ttbl = np.atleast_2d(np.asarray(getattr(result, 'thickness',
                                             np.zeros((0, 3))), dtype=float))
    for ti in range(_thick_nos.shape[0]):
        row_i = _n_sec + ti
        cells = [None] * ncase
        any_val = False
        for ci in range(min(ncase, len(result.maxratios))):
            m = np.atleast_2d(np.asarray(result.maxratios[ci]))
            if row_i < m.shape[0] and (m[row_i, 2] > 0
                                       or int(m[row_i, 1]) != 0):
                cells[ci] = {'ele': int(m[row_i, 1]),
                             'ratio': float(m[row_i, 2])}
                any_val = True
        if not any_val:
            continue
        texts = []
        if row_i < len(result.maxratios_text):
            for ci in range(ncase):
                tlist = (result.maxratios_text[row_i][ci]
                         if ci < len(result.maxratios_text[row_i]) else [])
                texts.append([str(t) for t in tlist])
        tid = _thick_nos[ti]
        name = ''
        if _ttbl.size:
            _tidx = int(np.atleast_1d(find_index(_ttbl[:, 0], tid))[0])
            if _tidx != -1:
                name = 't=%gmm' % _ttbl[_tidx, 1]
        vmax = max([c['ratio'] for c in cells if c], default=None)
        thick_table.append({'kind': '壁' if ti < _n_tw else '板',
                            'no': int(tid), 'name': name, 'cells': cells,
                            'max': vmax, 'texts': texts})

    return {'case_names': case_names, 'case_nos': case_nos,
            'sections': sections, 'skipped': skipped,
            'thick_table': thick_table}


def _plywood_col_map(result):
    """検定ケース列 → 対応する生の水平(または短期)ケース番号 (無ければNone).

    長期+水平の合成 (analysis_case) の列順
    [長期, +H1, -H1, +H2, -H2, ...] を L/H_Lcase から再構成する。
    """
    raw = [float(v) for v in
           np.atleast_1d(getattr(result, 'raw_case_no', np.zeros(0))).ravel()]
    L = [int(v) for v in
         np.atleast_1d(getattr(result, 'L_Lcase', np.zeros(0))).ravel()]
    H = [int(v) for v in
         np.atleast_1d(getattr(result, 'H_Lcase', np.zeros(0))).ravel()]
    S = [int(v) for v in
         np.atleast_1d(getattr(result, 'S_Lcase', np.zeros(0))).ravel()]
    pm = int(getattr(result, 'pm_direction', 2))
    ncase = len(result.LCNAME)
    cols = [None] * ncase
    if L and H and not S:
        per = 1 + (2 if pm == 2 else 1) * len(H)
        if ncase == len(L) * per:
            k = 0
            for _il in L:
                k += 1  # 長期列
                for ih in H:
                    cols[k] = raw[ih - 1]
                    k += 1
                    if pm == 2:
                        cols[k] = raw[ih - 1]
                        k += 1
    elif ncase == len(raw):  # 短期(組合せ済み)や水平のみ: 列=生ケース
        for ci in range(ncase):
            if (ci + 1) not in L:
                cols[ci] = raw[ci]
    return cols


def _plywood_check_rows(result, res):
    """plywood_check の結果を断面一覧の行 (sections互換) に変換する."""
    cols = _plywood_col_map(result)
    ncase = len(result.LCNAME)
    if all(c is None for c in cols):
        print('注意: 木合板検定の結果を検定ケース列へ対応付けできなかった'
              'ため一覧表に追加していません (CSVを参照してください)')
        return []
    rows = []
    for beta in sorted(set(w['beta'] for w in res['walls'])):
        walls = [w for w in res['walls'] if w['beta'] == beta]
        cells = [None] * ncase
        best = [None] * ncase   # (壁, ケース結果) 最大検定比の壁
        details = []
        for w in walls:
            vals = [None] * ncase
            rmap = {int(r['case']): r for r in w['cases']}
            for ci in range(ncase):
                if cols[ci] is None:
                    continue
                r = rmap.get(int(cols[ci]))
                if r is None:
                    continue
                vals[ci] = float(r['ratio'])
                if cells[ci] is None or r['ratio'] > cells[ci]['ratio']:
                    cells[ci] = {'ele': '%s (%s)' % (w['eles'][0], w['name']),
                                 'ratio': float(r['ratio'])}
                    best[ci] = (w, r)
            details.append({'ele': '%s %s %s z=%s [要素 %s]'
                            % (w['name'], w['loc'], w['range'], w['zrange'],
                               ','.join(str(e) for e in w['eles'])),
                            'vals': vals})
        # 検定詳細文: MATLAB (W_plate_analysis_text) と同じ段組で
        # ケースごとに最大の壁1件のみ出力する
        texts = []
        for ci in range(ncase):
            if best[ci] is None:
                texts.append([])
                continue
            w, r = best[ci]
            texts.append([
                '木材耐力壁のせん断に対する断面算定',
                '壁倍率から求まる許容せん断力に対して断面算定を行う．'
                '壁の作用せん断力は壁内の面内せん断応力Fxyの平均値とする．',
                '　　',
                '壁：%s（%s　%s　z=%s）' % (w['name'], w['loc'],
                                            w['range'], w['zrange']),
                '壁長：%.2fm　要素番号：%s'
                % (w['L'], ','.join(str(e) for e in w['eles'])),
                '　　',
                '壁倍率：%g倍' % beta,
                '*木材情報:せん断耐力',
                'せん断：%.2fkN/m（%.2f×%g）'
                % (w['qa'], res['qa_base'], beta),
                '*設計用応力',
                'せん断力(面内・壁平均)Fxy：%.2fkN/m' % abs(r['mean']),
                '*検定比',
                'せん断：%.3f' % r['ratio']])
        vmax = max([c['ratio'] for c in cells if c], default=None)
        rows.append({'no': '合板', 'name': '木合板壁 倍率%g' % beta,
                     'cells': cells, 'max': vmax, 'details': details,
                     'texts': texts})
    return rows


def _rc_sections_info(mgt_path, limit_sec_no=9000):
    """RC断面(material type 3)の一覧を返す (RC配筋設定のUI表示用).

    戻り値: {'norebar': [...], 'beams': [...], 'columns': [...],
             'has_src': bool} または None (RC断面もSRC要素も無い)
      norebar: mgtに配筋(*REBAR-BEAM/*REBAR-COLUMN)の無い断面。
               UIで種別(壁/梁/柱)と配筋の指定が必要
               (MATLAB版はlistdlgで分類し RCG/RCC/RCW_input で入力)。
               {'no','name','prefix','t','D'} (t/Dは中実角SBのときmm、他はNone)
      beams:   *REBAR-BEAM に配筋がある断面 {'no','name'}
      columns: *REBAR-COLUMN に配筋がある断面 {'no','name'}
    """
    element = mgtopen_element(mgt_path)
    material = mgtopen_material(mgt_path, w_select=w_al_match)
    sections, section_no, section_name = mgtopen_section(mgt_path)
    section_no = np.asarray(section_no, dtype=float).ravel()
    rcbeams = mgtopen_RCbeam(mgt_path)
    rebar_secs = set(int(r[0]) for r in rcbeams) if rcbeams else set()
    rccols = mgtopen_RCcolumn(mgt_path)
    if np.asarray(rccols).size:
        col_secs = set(int(v) for v in
                       np.atleast_2d(np.asarray(rccols, dtype=float))[:, 0])
    else:
        col_secs = set()

    rc_secs = []
    for r in range(element.shape[0]):
        m_idx = find_index(material[:, 0], element[r, 1])
        if m_idx == -1 or material[m_idx, 1] != 3:
            continue
        s_no = int(element[r, 2])
        if s_no >= limit_sec_no or s_no in [x[0] for x in rc_secs]:
            continue
        rc_secs.append((s_no, r))
    # SRC要素(material type 5)の有無 (壁が無くても割増ルートUIを出すため)
    has_src = False
    for r in range(element.shape[0]):
        m_idx = find_index(material[:, 0], element[r, 1])
        if m_idx != -1 and material[m_idx, 1] == 5:
            has_src = True
            break
    if not rc_secs and not has_src:
        return None

    sb_table = (np.atleast_2d(np.asarray(sections[2], dtype=float))
                if (len(sections) > 2 and sections[2] is not None
                    and np.asarray(sections[2]).size) else np.zeros((0, 3)))
    norebar, beams, columns = [], [], []
    for s_no, _r in sorted(rc_secs):
        si = find_index(section_no, s_no)
        name = str(section_name[si]).strip() if si != -1 else str(s_no)
        if s_no in rebar_secs:
            beams.append({'no': s_no, 'name': name})
            continue
        if s_no in col_secs:
            columns.append({'no': s_no, 'name': name})
            continue
        t = D = None
        sbi = find_index(sb_table[:, 0], s_no) if sb_table.size else -1
        if sbi != -1:
            t = round(float(sb_table[sbi, 1]) * 1000, 3)  # D1=厚
            D = round(float(sb_table[sbi, 2]) * 1000, 3)  # D2=壁長
        norebar.append({'no': s_no, 'name': name,
                        'prefix': pick_text(name, '_', -1), 't': t, 'D': D})
    return {'norebar': norebar, 'beams': beams, 'columns': columns,
            'has_src': has_src}


@app.route('/api/check_cases', methods=['POST'])
def api_check_cases():
    """断面検定用: 応力ファイルの荷重ケース一覧と種別の既定値(自動判定)を返す.

    beam_stress (+任意で truss_stress) のケース番号を集計し、
    ratio_pipeline.default_case_types による既定の種別
    ('L'=長期 / 'H'=水平のみ / 'S'=短期(組合せ済み)) とケース名を返す。

    mgt_path が指定された場合は木材料 (material type 10) の一覧と
    材料名からの W_AL 行自動対応の結果 (w_index, 0=未確定)、
    W_AL の行ラベル一覧 (木材種別ドロップダウン用) も返す。
    """
    p = request.get_json(force=True)
    err = _check_input_file(p.get('beam_stress_path'), 'beam_stressファイル')
    if err:
        return jsonify({'error': err}), 400
    truss_path = (p.get('truss_stress_path') or '').strip() or None
    if truss_path:
        err = _check_input_file(truss_path, 'truss_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    try:
        bs = np.atleast_2d(loadtxt_tolerant(p['beam_stress_path']))
        if bs.shape[1] < 2:
            return jsonify({'error': 'beam_stressファイルの列数が不足して'
                                     'います (8列必要)。'}), 400
        cases = bs[:, 1]
        if truss_path:
            ts = np.atleast_2d(loadtxt_tolerant(truss_path))
            if ts.size and ts.shape[1] >= 2:
                cases = np.concatenate([cases, ts[:, 1]])
        case_no = np.unique(cases)  # run_steel_check の doublecheck と同じ昇順
        out = {'cases': default_case_types(case_no)}
        # 木材料の一覧 (材料名からの W_AL 行自動対応の結果つき)
        mgt_path = (p.get('mgt_path') or '').strip()
        if mgt_path and os.path.isfile(mgt_path):
            wood = mgtopen_wood_materials(mgt_path, w_select=w_al_match)
            if wood:
                out['wood_materials'] = [
                    {'no': int(w['no']), 'name': str(w['name']),
                     'w_index': int(w['w_index'])} for w in wood]
                out['w_al_names'] = w_al_names()
            # RC断面 (material type 3) の検出 → RC壁配筋設定表の表示用
            rc = _rc_sections_info(
                mgt_path, limit_sec_no=float(p.get('limit_sec_no', 9000)))
            if rc:
                out['rc_sections'] = rc
        return jsonify(out)
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/steel_check', methods=['POST'])
def api_steel_check():
    p = request.get_json(force=True)
    for key, label in (('mgt_path', 'mgtファイル'),
                       ('beam_stress_path', 'beam_stressファイル')):
        err = _check_input_file(p.get(key), label)
        if err:
            return jsonify({'error': err}), 400
    truss_path = (p.get('truss_stress_path') or '').strip() or None
    if truss_path:
        err = _check_input_file(truss_path, 'truss_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    # plate_stress: 板要素(S板/RC板/木面材壁)の応力 (検定は未検証経路)
    plate_path = (p.get('plate_stress_path') or '').strip() or None
    if plate_path:
        err = _check_input_file(plate_path, 'plate_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    # wall_stress: 壁要素(RC板壁)の応力 (検定は未検証経路)
    wall_path = (p.get('wall_stress_path') or '').strip() or None
    if wall_path:
        err = _check_input_file(wall_path, 'wall_stressファイル')
        if err:
            return jsonify({'error': err}), 400
    try:
        c_up = {'STKR': float(p.get('c_up_STKR', 1.4)),
                'BCR': float(p.get('c_up_BCR', 1.3)),
                'BCP': float(p.get('c_up_BCP', 1.2)),
                'BSH': float(p.get('c_up_BSH', 1.0))}
        # ケース種別の明示指定 (省略時は従来どおり自動判定)
        case_types = None
        if p.get('case_types'):
            case_types = {}
            for row in p['case_types']:
                case_types[float(row['no'])] = (
                    str(row.get('type', 'H')), str(row.get('name', '')))
        # ケース表が古い(別の応力ファイルのもの)場合は明示エラー
        if case_types is not None:
            _bs = np.atleast_2d(loadtxt_tolerant(p['beam_stress_path']))
            _cases = _bs[:, 1]
            if truss_path:
                _ts = np.atleast_2d(loadtxt_tolerant(truss_path))
                if _ts.size and _ts.shape[1] >= 2:
                    _cases = np.concatenate([_cases, _ts[:, 1]])
            _actual = set(float(v) for v in np.unique(_cases))
            if set(case_types.keys()) != _actual:
                fmt = lambda vs: ', '.join(str(int(v)) if float(v).is_integer()
                                           else str(v) for v in sorted(vs))
                return jsonify({'error':
                    'ケース表(ケース %s)が現在の応力ファイルの荷重ケース(%s)と'
                    '一致しません。応力ファイルを変更した場合は「ケース読込(種別指定)」を'
                    'やり直してください。' % (fmt(case_types.keys()),
                                              fmt(_actual))}), 400
        # 木材料の W_AL 行の明示指定 (UIの木材種別ドロップダウン)
        w_material_map = None
        if p.get('w_material_map'):
            w_material_map = {}
            for row in p['w_material_map']:
                if int(row.get('w_index', 0)) > 0:
                    w_material_map[float(row['no'])] = int(row['w_index'])
        # 木造トラス(筋かい・壁面)の壁倍率 {断面番号: 壁倍率}
        w_panel_co = None
        if p.get('w_panel_co'):
            w_panel_co = {float(k): float(v)
                          for k, v in dict(p['w_panel_co']).items()}
        # ケース読込を経ずに検定した場合の保護 (2026-07-10):
        # RC/SRC部材があるのに設定パネル(qup_mode等)が未送信のまま実行すると
        # 既定の「割増なし」で黙って検定される (UI表示既定=ルート1と不整合)。
        if 'qup_mode' not in p:
            _rc_info = _rc_sections_info(
                p['mgt_path'], limit_sec_no=float(p.get('limit_sec_no', 9000)))
            if _rc_info:
                return jsonify({'error':
                    'RC/SRC部材を含むモデルです。「ケース読込(種別指定)」を'
                    '実行し、RC壁配筋・せん断割増ルート等の設定を確認してから'
                    '検定を実行してください。'}), 400
        # RCせん断力の割増 (qup_case.m のルート選択の引数化)
        qup = None
        qm = str(p.get('qup_mode') or 'none')
        if qm == 'route1':
            qup = {'beam': 1.5, 'wall': 2.0, 'src': 1.0}
        elif qm == 'route2':
            qup = {'beam': 2.0, 'wall': 2.0, 'src': 2.0}
        elif qm == 'route3':
            qup = {'beam': 1.5, 'wall': 1.0, 'src': 1.0}
        elif qm == 'custom':
            qup = {'beam': float(p.get('qup_beam', 2.0)),
                   'wall': float(p.get('qup_wall', 2.0)),
                   'src': float(p.get('qup_src', 2.0))}
            # 空欄はUI側で0になり Qs=QL(割増なし相当)と危険なため明示エラー
            _bad = {'柱・梁': qup['beam'], '壁': qup['wall'],
                    'SRC': qup['src']}
            _bad = {k: v for k, v in _bad.items() if not (v > 0)}
            if _bad:
                return jsonify({'error':
                    'せん断割増係数(数値指定)は正の数値を入力してください '
                    '(0以下・空欄は不可): ' + ', '.join(
                        '%s=%s' % (k, v) for k, v in _bad.items())}), 400
        # RC検定の設定 (RC壁配筋・かぶり・method_rcw・RCQ)
        rc_params = None
        if p.get('rc'):
            rc = dict(p['rc'])
            rcw = {}
            for row in (rc.get('rcw') or []):
                rcw[float(row['no'])] = {
                    'mode': int(row.get('mode', 1)),
                    'sd': int(row.get('sd', 2)),
                    'v_di': float(row.get('v_di', 10)),
                    'v_pitch': float(row.get('v_pitch', 200)),
                    'h_di': float(row.get('h_di', 10)),
                    'h_pitch': float(row.get('h_pitch', 200)),
                    'num_rebar': float(row.get('num_rebar', 2))}
            # 梁として配筋指定した断面 (RCG_input.m の引数化):
            # 17列行 [上端段数,本数x3,径x3, 下端段数,本数x3,径x3,
            #         STRP径,ピッチ,本数] を組み立てる (段数超の段は0)
            rcg = {}
            for row in (rc.get('rcg') or []):
                u_dan = int(row.get('u_dan', 1))
                d_dan = int(row.get('d_dan', u_dan))
                u_num = [float(v) for v in (row.get('u_num') or [0, 0, 0])]
                u_di = [float(v) for v in (row.get('u_di') or [0, 0, 0])]
                d_num = [float(v) for v in (row.get('d_num') or u_num)]
                d_di = [float(v) for v in (row.get('d_di') or u_di)]
                for k in range(3):
                    if k >= u_dan:
                        u_num[k] = 0.0
                        u_di[k] = 0.0
                    if k >= d_dan:
                        d_num[k] = 0.0
                        d_di[k] = 0.0
                rcg[float(row['no'])] = (
                    [float(u_dan)] + u_num + u_di
                    + [float(d_dan)] + d_num + d_di
                    + [float(row.get('s_di', 10)),
                       float(row.get('s_pitch', 200)),
                       float(row.get('s_num', 2))])
            # 柱として配筋指定した断面 (RCC_input.m の引数化)。
            # 注意: RC柱(ラーメン柱)の検定計算は未移植 (到達時明示エラー)
            rcc = {}
            for row in (rc.get('rcc') or []):
                m_num = float(row.get('m_num', 8))
                if not (m_num > 0) or m_num % 4 != 0:
                    return jsonify({'error':
                        'RC柱(断面%s)の主筋本数は4の倍数を入力してください '
                        '(入力値: %s)' % (row.get('no'),
                                          row.get('m_num'))}), 400
                rcc[float(row['no'])] = {
                    'm_di': float(row.get('m_di', 13)),
                    'm_num': m_num,
                    'h_di': float(row.get('h_di', 10)),
                    'h_pitch': float(row.get('h_pitch', 200)),
                    'h_num': float(row.get('h_num', 2))}
            rc_params = {
                'rcw': rcw, 'rcg': rcg, 'rcc': rcc,
                'wall_cover': float(rc.get('wall_cover', 40)),
                'beam_cover': float(rc.get('beam_cover', 40)),
                'column_cover': float(rc.get('column_cover', 40)),
                'method_rcw': int(rc.get('method_rcw', 3)),
                'RCQ': int(rc.get('RCQ', 1)),
                'L_43': [float(v) for v in (rc.get('L_43') or [])],
                'walldesign_index': int(rc.get('walldesign_index', 1))}
        # 部材長の扱い (''/None=要素単位, 0=結合部材単位<MATLAB一括表示相当>)
        su_raw = p.get('select_unit')
        select_unit = (float(su_raw) if su_raw not in (None, '')
                       else float('inf'))
        src_cover = float(p.get('src_cover', 40.0))
        if not (src_cover > 0):
            return jsonify({'error':
                'SRCコンクリートかぶり厚は正の数値(mm)を入力してください '
                '(入力値: %s。空欄は0扱いになります)' % src_cover}), 400
        pile_cover = float(p.get('pile_cover', 100.0))
        if not (pile_cover > 0):
            return jsonify({'error':
                'RC杭かぶり厚は正の数値(mm)を入力してください '
                '(入力値: %s。空欄は0扱いになります)' % pile_cover}), 400
        plate_up = float(p.get('plate_up', 1.0))
        if not (plate_up > 0):
            return jsonify({'error':
                '板要素の水平応力割増係数は正の数値を入力してください '
                '(入力値: %s)' % plate_up}), 400
        _plr = p.get('pl_long')
        pl_long = None if _plr in (None, '') else int(_plr)
        # PC検定の設定 (原典のPCダイアログ群の引数化)
        pc_params = None
        if p.get('pc'):
            pc = dict(p['pc'])
            if pc.get('skip'):
                pc_params = {'skip': True}
            else:
                pc_params = {
                    'PC_eff': float(pc.get('PC_eff', 0.85)),
                    'PC_type': float(pc.get('PC_type', 0.1)),
                    'DL_ratio': float(pc.get('DL_ratio', 0.6)),
                }
                _sl_path = (pc.get('pc_slab_path') or '').strip()
                if _sl_path:
                    err = _check_input_file(_sl_path, 'PC_slabファイル')
                    if err:
                        return jsonify({'error': err}), 400
                    pc_params['PC_slab'] = np.atleast_2d(
                        loadtxt_tolerant(_sl_path))
                else:
                    _slab = []
                    for row in (pc.get('pc_slab') or []):
                        _slab.append([float(row.get('no', 0)),
                                      float(row.get('t', 0)),
                                      float(row.get('tbm', 0)),
                                      float(row.get('tbn', 0))])
                    pc_params['PC_slab'] = (np.asarray(_slab, dtype=float)
                                            if _slab else np.zeros((0, 4)))
                _ps_path = (pc.get('pc_stress_path') or '').strip()
                if _ps_path:
                    err = _check_input_file(_ps_path, 'pc_stressファイル')
                    if err:
                        return jsonify({'error': err}), 400
                    pc_params['pc_stress'] = np.atleast_2d(
                        loadtxt_tolerant(_ps_path))
                else:
                    pc_params['pc_stress'] = None
                _cb_path = (pc.get('pc_cable_path') or '').strip()
                if _cb_path:
                    err = _check_input_file(_cb_path, 'pc_cableファイル')
                    if err:
                        return jsonify({'error': err}), 400
                    pc_params['cable_select'] = np.atleast_2d(
                        loadtxt_tolerant(_cb_path))
                else:
                    _cs = []
                    for row in (pc.get('cable') or []):
                        _cs.append([float(row.get('no', 0)),
                                    float(row.get('cable_idx', 1)),
                                    float(row.get('num', 14))])
                    if not _cs:
                        return jsonify({'error':
                            'PC検定にはケーブル情報 (pc_cableファイル、または'
                            '断面ごとの鋼種・本数) の入力が必要です。'}), 400
                    pc_params['cable_select'] = np.asarray(_cs, dtype=float)
                from mgtkit.mgt import mgtopen_cable
                _cd = mgtopen_cable(p['mgt_path'])
                if not np.size(_cd):
                    return jsonify({'error':
                        'mgt記述の注意: PC検定にはmgtの *PRESTRESS (ケーブル'
                        '偏心) が必要ですが、見つかりませんでした。MIDASで'
                        'プレストレス(テンドン)を定義したmgtを使用して'
                        'ください。'}), 400
                pc_params['cable_delta'] = _cd
        notes = []
        with _capture_notes(notes):
            result = run_steel_check(
                p['mgt_path'], p['beam_stress_path'],
                truss_stress_path=truss_path,
                select_length=int(p.get('select_length', 1)),
                c_up=c_up,
                H_beam_RCsupport=int(p.get('H_beam_RCsupport', 2)),
                judge_H=int(p.get('judge_H', 2)),
                limit_sec_no=float(p.get('limit_sec_no', 9000)),
                case_types=case_types,
                wal_up=float(p.get('wal_up', 1.0)),
                w_material_map=w_material_map,
                w_panel_co=w_panel_co,
                qup=qup,
                rc_params=rc_params,
                src_cover=src_cover,
                select_unit=select_unit,
                pile_cover=pile_cover,
                pc_params=pc_params,
                wall_stress_path=wall_path,
                plate_stress_path=plate_path,
                plate_up=plate_up,
                pl_long=pl_long)
        data = _build_check_json(result)
        data['case_nos'] = [float(v) for v in
                            np.asarray(result.load_case_no).ravel()]
        _CHECK_CACHE['key'] = _check_cache_key(p)
        _CHECK_CACHE['result'] = result

        with _capture_notes(notes):
            out_dir = _out_dir(p, 'ratio_tex')
        base = os.path.splitext(os.path.basename(p['mgt_path']))[0]
        xlsx_path = os.path.join(out_dir, base + '_maxratio.xlsx')
        export_maxratio_xlsx(result, xlsx_path)
        data['xlsx_url'] = _register_file(xlsx_path) + '&dl=1'
        data['xlsx_name'] = os.path.basename(xlsx_path)
        full_ratio_path = os.path.join(out_dir, base + '_full_ratio.xlsx')
        export_full_ratio_xlsx(result, full_ratio_path)
        data['full_ratio_url'] = _register_file(full_ratio_path) + '&dl=1'
        data['full_ratio_name'] = os.path.basename(full_ratio_path)
        # 木合板耐力壁の検定 (用途=木合板のとき plate_stress で実行し、
        # 検定ケース列に対応付けて断面一覧へ行を追加する)
        pw_path = (p.get('plywood_stress_path') or '').strip()
        if pw_path:
            with _capture_notes(notes):
                try:
                    from mgtkit.plywood import plywood_check, plywood_csv
                    # CSVのケース名はケース表 (ケース読込) の名前を使う
                    _pw_labels = ', '.join(
                        '%g:%s' % (float(row['no']), row.get('name') or '')
                        for row in (p.get('case_types') or [])
                        if row.get('name'))
                    pw_res = plywood_check(
                        p['mgt_path'], pw_path,
                        labels=_pw_labels,
                        qa_base=float(p.get('pw_qa', 1.96)))
                    data['sections'].extend(
                        _plywood_check_rows(result, pw_res))
                    _pw_csv = plywood_csv(
                        pw_res,
                        os.path.join(_out_dir(p, 'plywood'),
                                     'plywood_check.csv'))
                    data['plywood_csv'] = {
                        'name': os.path.basename(_pw_csv),
                        'url': _register_file(_pw_csv) + '&dl=1'}
                except Exception as e:  # noqa: BLE001
                    print('注意: 木合板検定でエラーのためスキップしました:'
                          ' %s' % e)
        data['out_dir'] = out_dir
        data['notes'] = notes
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: QR図 (反力・せん断分担・偏心率)
# ---------------------------------------------------------------------------

def _qr_load(p):
    """QRタブの共通入力読込 (パス検証込み)."""
    from mgtkit.draw_qr import load_qr_data
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        raise ValueError(err)
    paths = {}
    for key, label in (('beam_stress_path', 'beam_stressファイル'),
                       ('truss_stress_path', 'truss_stressファイル'),
                       ('plate_stress_path', 'plate_stressファイル'),
                       ('wall_stress_path', 'wall_stressファイル'),
                       ('reaction_path', 'reactionファイル'),
                       ('deformation_path', 'deformationファイル')):
        pth = (p.get(key) or '').strip() or None
        if pth:
            err = _check_input_file(pth, label)
            if err:
                raise ValueError(err)
        paths[key] = pth
    return load_qr_data(p['mgt_path'], **paths)


def _qr_sec_guess(name):
    """断面名から梁(g)/柱(c)/壁(w)/ブレース(v)の初期推定."""
    n = str(name).strip().upper()
    if not n:
        return 'g'
    if n.startswith(('V', 'BR')):
        return 'v'
    if n.startswith(('EW', 'RW', 'CW', 'SW', 'W')):
        return 'w'
    if 'G' in n.split('_')[0][:3]:
        return 'g'
    if n.startswith(('C', 'P', 'SC', 'RC')):
        return 'c'
    if n.startswith('B'):
        return 'g'
    return 'g'


@app.route('/api/qr_info', methods=['POST'])
def api_qr_info():
    """QRタブ: 荷重ケース・柱脚レベル・断面一覧を返す."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import enum_z_points, default_qr_case_names
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)

        # ケース番号列 (num_load_case と同数の列を採用: 原典221行)
        cand = [D.load_case_no_beam, D.load_case_no_truss,
                D.load_case_no_wall, D.load_case_no_reaction]
        cases = []
        for c in cand:
            if c.shape[0] == D.num_load_case and c.shape[0] > 0:
                cases = [float(v) for v in c]
                break
        default_names = default_qr_case_names(len(cases))

        z = enum_z_points(D.node, D.element, D.wall_element)
        z_points = [float(v) for v in z]
        # 層番号の初期推定: フロアグループ (1F/2F/RF等) から自動設定。
        # グループが無いモデルは従来どおり全レベルへ昇順に1,2,...
        with _capture_notes(notes):
            story_guess = _story_guess_from_groups(p['mgt_path'], z_points)
        if story_guess is None:
            story_guess = []
            seen = {}
            for v in z_points:
                key = round(v, 3)
                if key not in seen:
                    seen[key] = 0
            for i, key in enumerate(sorted(seen.keys())):
                seen[key] = i + 1
            for v in z_points:
                story_guess.append(seen[round(v, 3)])

        limit = float(p.get('limit_sec_no', 9000))
        sections = []
        for no, name in zip(np.atleast_1d(D.section_no),
                            list(D.section_name)):
            if float(no) >= limit:
                continue
            sections.append({'no': float(no), 'name': str(name).strip(),
                             'guess': _qr_sec_guess(name)})

        defo_cases = [float(v) for v in D.load_case_no_defo]
        return jsonify({'cases': cases, 'default_names': default_names,
                        'defo_cases': defo_cases, 'z_points': z_points,
                        'story_guess': story_guess, 'sections': sections,
                        'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)




def _qr_common(p, D):
    """QR実行系エンドポイントの共通パラメータ解釈."""
    from mgtkit.draw_qr import (resolve_axes, case_positions,
                                stories_to_case_height, enum_z_points)
    limit = float(p.get('limit_sec_no', 9000))
    from mgtkit.draw_qr import check_unit_dummy
    check_unit_dummy(D, limit)

    # ケース番号列 (qr_info と同じ規則)
    cand = [D.load_case_no_beam, D.load_case_no_truss,
            D.load_case_no_wall, D.load_case_no_reaction]
    cases_all = []
    for c in cand:
        if c.shape[0] == D.num_load_case and c.shape[0] > 0:
            cases_all = [float(v) for v in c]
            break
    # ケース名 (UIの表を位置合わせ。未指定は CASE<番号>)
    name_map = {float(r['no']): str(r.get('name') or '')
                for r in (p.get('case_names') or [])}
    load_case_name = [name_map.get(c) or ('CASE%g' % c) for c in cases_all]

    vertical = [g['name'] for g in _group_info(p['mgt_path'])
                if g['kind'] == 'vertical']
    axes_idx = resolve_axes(D, p.get('axes'), vertical)

    z_point = enum_z_points(D.node, D.element, D.wall_element)
    case_height = stories_to_case_height(p.get('stories'), z_point)

    common = {
        'limit': limit, 'cases_all': cases_all,
        'load_case_name': load_case_name, 'axes_idx': axes_idx,
        'z_point': z_point, 'case_height': case_height,
        'scope': str(p.get('scope') or 'all'),
        'calc_groups': p.get('calc_groups'),
        'mergins': (float(p.get('mg_l', 3.0)), float(p.get('mg_r', 3.0)),
                    float(p.get('mg_t', 3.0)), float(p.get('mg_b', 5.0))),
        'fontsize': (float(p.get('f_s', 3)), float(p.get('f_d', 5)),
                     float(p.get('f_a', 5)), float(p.get('f_t', 7)),
                     float(p.get('f_re', 5))),
        'axisname_location': float(p.get('axisname_location', 1.7)),
        'line_location': float(p.get('line_location', 1.5)),
        'paper_orient': int(p.get('paper_orient', 2)),
        'paper_size': int(p.get('paper_size', 4)),
    }
    return common


def _qr_pdf_json(made, out_dir, notes):
    pdfs = []
    for f in made:
        pdfs.append({'name': os.path.basename(f),
                     'url': _register_file(f)})
    return jsonify({'pdfs': pdfs, 'out_dir': out_dir, 'notes': notes})


@app.route('/api/qr_reaction', methods=['POST'])
def api_qr_reaction():
    """QR: 反力図PDF."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import plot_qr_reaction, case_positions
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)
            C = _qr_common(p, D)
            case_L = case_positions(D, p.get('case_L'), C['cases_all'])
            sel_S = case_positions(D, p.get('case_S'), C['cases_all'])
            if str(p.get('case_kind') or 'H') == 'H':
                case_S, case_H = [], sel_S
            else:
                case_S, case_H = sel_S, []
            out_dir = _out_dir(p, os.path.join('qr', '反力図'))
            with _PLOT_LOCK:
                made = plot_qr_reaction(
                    D, out_dir, C['load_case_name'], case_L, case_S,
                    case_H, C['axes_idx'], scope=C['scope'],
                    calc_groups=C['calc_groups'], mergins=C['mergins'],
                    fontsize=C['fontsize'],
                    axisname_location=C['axisname_location'],
                    line_location=C['line_location'],
                    paper_orient=C['paper_orient'],
                    paper_size=C['paper_size'])
        return _qr_pdf_json(made, out_dir, notes)
    except Exception as e:  # noqa: BLE001
        return _error_response(e)




@app.route('/api/qr_drift', methods=['POST'])
def api_qr_drift():
    """QR: 層間変形角PDF."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import plot_qr_drift, _defo_case_positions
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)
            C = _qr_common(p, D)
            delta_case = _defo_case_positions(D, p.get('delta_case'))
            out_dir = _out_dir(p, os.path.join('qr', '層間変形角'))
            with _PLOT_LOCK:
                made = plot_qr_drift(
                    D, out_dir, C['load_case_name'], delta_case,
                    C['axes_idx'], C['z_point'], C['case_height'],
                    scope=C['scope'], calc_groups=C['calc_groups'],
                    mergins=C['mergins'], fontsize=C['fontsize'],
                    paper_orient=C['paper_orient'],
                    paper_size=C['paper_size'], limit_sec_no=C['limit'])
        return _qr_pdf_json(made, out_dir, notes)
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/qr_center', methods=['POST'])
def api_qr_center():
    """QR: 重心・剛心・偏心率 (図+表)."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import plot_qr_center, _defo_case_positions
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)
            C = _qr_common(p, D)
            k_mode = str(p.get('k_mode') or 'fem')
            if k_mode == 'baisu':
                # 壁倍率方式: 重心ケースは beam_stress のケース一覧から
                # 解決する (deformation ファイル不要)
                from mgtkit.draw_qr import case_positions
                N_case = case_positions(D, [p.get('N_case')],
                                        C['cases_all'])[0]
                KX_case = KY_case = N_case
                thickness = mgtopen_thickness(p['mgt_path'])
                brace_baisu = {}
                for tok in str(p.get('brace_baisu') or '').replace(
                        '、', ',').split(','):
                    tok = tok.strip()
                    if not tok or ':' not in tok:
                        continue
                    k0, v0 = tok.split(':', 1)
                    try:
                        brace_baisu[int(float(k0))] = float(v0)
                    except ValueError:
                        continue
            else:
                N_case = _defo_case_positions(D, [p.get('N_case')])[0]
                KX_case = _defo_case_positions(D, [p.get('KX_case')])[0]
                KY_case = _defo_case_positions(D, [p.get('KY_case')])[0]
                thickness = None
                brace_baisu = None
            out_dir = _out_dir(
                p, os.path.join('qr', '重心・剛心・偏心率'))
            with _PLOT_LOCK:
                made, table, tex_lines = plot_qr_center(
                    D, out_dir, C['load_case_name'], N_case, KX_case,
                    KY_case, C['axes_idx'], C['z_point'], C['case_height'],
                    scope=C['scope'], calc_groups=C['calc_groups'],
                    plate_up=float(p.get('plate_up', 1.0)),
                    mergins=C['mergins'], fontsize=C['fontsize'],
                    axisname_location=C['axisname_location'],
                    line_location=C['line_location'],
                    paper_orient=C['paper_orient'],
                    paper_size=C['paper_size'], limit_sec_no=C['limit'],
                    k_mode=k_mode, brace_baisu=brace_baisu,
                    thickness=thickness)
        pdfs = [{'name': os.path.basename(f), 'url': _register_file(f)}
                for f in made]
        return jsonify({'pdfs': pdfs, 'out_dir': out_dir, 'notes': notes,
                        'table': table, 'tex_lines': tex_lines})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)




@app.route('/api/qr_shear', methods=['POST'])
def api_qr_shear():
    """QR: せん断力分担図PDF + 層別TeX表."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import plot_qr_shear, case_positions
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)
            C = _qr_common(p, D)
            case_S = case_positions(D, p.get('case_S'), C['cases_all'])
            dir_map = {float(k): int(v)
                       for k, v in (p.get('directions') or {}).items()}
            sei_direction = [dir_map.get(float(c), 1)
                             for c in (p.get('case_S') or [])]
            out_dir = _out_dir(
                p, os.path.join('qr', 'せん断力分担図'))
            with _PLOT_LOCK:
                made, tex_lines = plot_qr_shear(
                    D, out_dir, C['load_case_name'], case_S, sei_direction,
                    p.get('stories'), p.get('sec_class'),
                    scope=C['scope'], calc_groups=C['calc_groups'],
                    mergins=C['mergins'], fontsize=C['fontsize'],
                    line_location=C['line_location'],
                    paper_orient=C['paper_orient'],
                    paper_size=C['paper_size'], limit_sec_no=C['limit'])
        pdfs = [{'name': os.path.basename(f), 'url': _register_file(f)}
                for f in made]
        return jsonify({'pdfs': pdfs, 'out_dir': out_dir, 'notes': notes,
                        'tex_lines': tex_lines})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/qr_cb', methods=['POST'])
def api_qr_cb():
    """QR: 柱脚設計用データ (xlsx + 平面図PDF)."""
    p = request.get_json(force=True)
    try:
        from mgtkit.draw_qr import plot_qr_cb, case_positions
        notes = []
        with _capture_notes(notes):
            D = _qr_load(p)
            C = _qr_common(p, D)
            case_CB = case_positions(D, p.get('case_CB'), C['cases_all'])
            out_dir = _out_dir(
                p, os.path.join('qr', '柱脚設計用データ(CB)'))
            with _PLOT_LOCK:
                RESULT, made = plot_qr_cb(
                    D, out_dir, C['load_case_name'], case_CB,
                    p.get('cb_levels'), C['axes_idx'],
                    mergins=C['mergins'], fontsize=C['fontsize'],
                    axisname_location=C['axisname_location'],
                    line_location=C['line_location'],
                    paper_orient=C['paper_orient'],
                    paper_size=C['paper_size'], limit_sec_no=C['limit'])

        # RESULT を xlsx へ (原典は save CB.mat)
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '柱脚設計用データ'
        ws.append(['柱脚節点', '荷重ケース', 'ケース名', '軸力N [kN]',
                   'X方向Q [kN]', 'Y方向Q [kN]', 'My [kNm]', 'Mz [kNm]'])
        cases_all = np.asarray(C['cases_all'], dtype=float)
        RESULT = np.atleast_2d(np.asarray(RESULT, dtype=float))
        for r in range(RESULT.shape[0]):
            ci = int(np.atleast_1d(find_index(
                cases_all, RESULT[r, 1]))[0]) if cases_all.size else -1
            cname = (C['load_case_name'][ci]
                     if 0 <= ci < len(C['load_case_name']) else '')
            ws.append([int(RESULT[r, 0]), int(RESULT[r, 1]), cname]
                      + [float(v) for v in RESULT[r, 2:7]])
        base = os.path.splitext(os.path.basename(p['mgt_path']))[0]
        xlsx_path = os.path.join(out_dir, base + '_CB.xlsx')
        wb.save(xlsx_path)

        pdfs = [{'name': os.path.basename(f), 'url': _register_file(f)}
                for f in made]
        return jsonify({'pdfs': pdfs, 'out_dir': out_dir, 'notes': notes,
                        'xlsx_url': _register_file(xlsx_path) + '&dl=1',
                        'xlsx_name': os.path.basename(xlsx_path)})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)




@app.route('/api/struct_info', methods=['POST'])
def api_struct_info():
    """構造図(新規版): 伏図レベル候補と部材集計を返す."""
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        from mgtkit.dxf_struct import (load_struct_model, plan_levels,
                                       auto_frames, plan_keys)
        notes = []
        with _capture_notes(notes):
            M = load_struct_model(p['mgt_path'],
                                  limit_sec_no=float(p.get('limit_sec_no',
                                                           9000)))
            levels = plan_levels(M)
            frames = auto_frames(M)
            plans = plan_keys(M)
        kinds = {'column': 0, 'beam': 0, 'brace': 0}
        for m in M.members:
            kinds[m['kind']] = kinds.get(m['kind'], 0) + 1
        pins = sum(1 for m in M.members if m['pin1'] or m['pin2'])
        return jsonify({'levels': [float(v) for v in levels],
                        'plans': plans,
                        'frames': [{'key': f['key'], 'label': f['label'],
                                    'n_col': f['n_col']} for f in frames],
                        'n_members': len(M.members), 'kinds': kinds,
                        'n_pin': pins, 'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/struct_preview', methods=['POST'])
def api_struct_preview():
    """構造図(新規版): 用紙プレビューPNG."""
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        from mgtkit.dxf_struct import preview_png
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'dxf')
        out_path = os.path.join(out_dir, '_struct_preview.png')
        scale = p.get('scale')
        scale = int(scale) if scale else None
        with _PLOT_LOCK, _capture_notes(notes):
            out_path, n = preview_png(
                p['mgt_path'], out_path, str(p.get('kind') or 'axis'),
                p.get('key'), paper=str(p.get('paper') or 'A3'),
                scale=scale, pin_paper_mm=float(p.get('pin_mm', 1.5)),
                limit_sec_no=float(p.get('limit_sec_no', 9000)))
        return jsonify({'png_url': _register_file(out_path)
                        + '&t=%d' % int(os.path.getmtime(out_path)),
                        'scale': n, 'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/struct_dxf', methods=['POST'])
def api_struct_dxf():
    """構造図(新規版): DXF生成."""
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        from mgtkit.dxf_struct import export_struct_dxf
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'dxf')
            scale = p.get('scale')
            scale = int(scale) if scale else None
            made, info = export_struct_dxf(
                p['mgt_path'], out_dir,
                axes=p.get('axes') or [],
                levels=list(p.get('levels') or []),
                paper=str(p.get('paper') or 'A3'), scale=scale,
                pin_paper_mm=float(p.get('pin_mm', 1.5)),
                text_paper_mm=float(p.get('text_mm', 2.5)),
                limit_sec_no=float(p.get('limit_sec_no', 9000)),
                one_file=bool(p.get('one_file', True)))
        files = [{'name': os.path.basename(f),
                  'url': _register_file(f) + '&dl=1'} for f in made]
        return jsonify({'files': files, 'info': info, 'out_dir': out_dir,
                        'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/plywood_check', methods=['POST'])
def api_plywood_check():
    """木合板 (面材耐力壁) の検定: 厚み=壁倍率、qa=1.96×倍率 [kN/m]."""
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    err = _check_input_file(p.get('stress_path'), 'plate_stress')
    if err:
        return jsonify({'error': err}), 400
    try:
        from mgtkit.plywood import plywood_check, plywood_csv
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'plywood')
            res = plywood_check(p['mgt_path'], p['stress_path'],
                                labels=str(p.get('labels') or ''),
                                qa_base=float(p.get('qa', 1.96)))
            csv_path = plywood_csv(
                res, os.path.join(out_dir, 'plywood_check.csv'))
        return jsonify({'walls': res['walls'], 'cases': res['cases'],
                        'qa_base': res['qa_base'],
                        'csv': {'name': os.path.basename(csv_path),
                                'url': _register_file(csv_path) + '&dl=1'},
                        'out_dir': out_dir, 'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


# ---------------------------------------------------------------------------
# エンドポイント: TeX表・DXF
# ---------------------------------------------------------------------------

@app.route('/api/plot_ratio', methods=['POST'])
def api_plot_ratio():
    """検定比図 (構面別PDF) の生成.

    直近の「検定実行」の結果 (beam_ratio/truss_ratio) を使う。
    検定条件 (入力ファイル・設定) が変わっている場合は明示エラーとし、
    再度「検定実行」を求める (黙って古い結果で描かない)。
    """
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    if (_CHECK_CACHE['result'] is None
            or _CHECK_CACHE['key'] != _check_cache_key(p)):
        return jsonify({'error':
            '検定結果がありません (または検定条件・入力ファイルが変更されて'
            'います)。先に「検定実行」を行ってから検定比図を生成して'
            'ください。'}), 400
    result = _CHECK_CACHE['result']
    axes = p.get('axes') or []
    if not axes:
        return jsonify({'error': '構面(鉛直構面)を1つ以上選択して'
                                 'ください。'}), 400
    try:
        notes = []
        with _PLOT_LOCK, _capture_notes(notes):
            out_dir = _out_dir(p, 'ratio_plot')
            cases = p.get('cases') or None
            if cases:
                cases = [float(c) for c in cases]
            # 図の「一部材として表記」は応力図と同じく独立オプション
            # (検定値の算定に使ったselect_unitとは別。既定ON)
            fig_unit = bool(p.get('fig_unit', True))
            select_unit = 0.0 if fig_unit else float('inf')
            pdfs = plot_ratio(
                p['mgt_path'], out_dir,
                result.beam_ratio, result.truss_ratio,
                [str(n) for n in result.LCNAME],
                cases_select=cases,
                axes_select=axes,
                symbols_select=(p.get('symbols') or None),
                heights_select=_parse_heights(p),
                select_unit=select_unit,
                limit_sec_no=float(p.get('limit_sec_no', 9000)),
                axisname_location=float(p.get('axisname_location', 3.0)),
                line_location=float(p.get('line_location', 2.0)),
                mergins=(float(p.get('mg_l', 5.0)),
                         float(p.get('mg_r', 2.0)),
                         float(p.get('mg_t', 5.0)),
                         float(p.get('mg_b', 5.0))),
                fontsize=(float(p.get('f_s', 4.0)),
                          float(p.get('f_d', 5.0)),
                          float(p.get('f_a', 6.0)),
                          float(p.get('f_t', 8.0))),
                paper_size=int(p.get('paper_size', 4)),
                fig_format=str(p.get('fig_format') or 'pdf'))
        if not pdfs:
            return jsonify({'error': '出力対象の図がありません。構面・検定'
                            'ケースの選択を確認してください。',
                            'notes': notes}), 400
        return jsonify({'pdfs': [{'name': os.path.basename(f),
                                  'url': _register_file(f),
                                  'path': os.path.abspath(f)}
                                 for f in pdfs],
                        'notes': notes, 'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/export_tex', methods=['POST'])
def api_export_tex():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'tex')
        base = os.path.splitext(os.path.basename(p['mgt_path']))[0]
        out_path = os.path.join(out_dir, base + '_model_tex.txt')
        with _capture_notes(notes):
            export_model_tex(p['mgt_path'], out_path,
                             limit_sec_no=float(p.get('limit_sec_no', 9000)))
        with open(out_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return jsonify({'text': text, 'url': _register_file(out_path),
                        'name': os.path.basename(out_path), 'notes': notes,
                        'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/ratio_table_tex', methods=['POST'])
def api_ratio_table_tex():
    """検定比図と同内容のTeX表 (新規機能。PDF貼り込みより軽量)."""
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    if (_CHECK_CACHE['result'] is None
            or _CHECK_CACHE['key'] != _check_cache_key(p)):
        return jsonify({'error':
            '検定結果がありません (または検定条件・入力ファイルが変更されて'
            'います)。先に「検定実行」を行ってから生成してください。'}), 400
    result = _CHECK_CACHE['result']
    try:
        from mgtkit.draw_ratio import export_ratio_tex
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'ratio_plot')
            cases = p.get('cases') or None
            if cases:
                cases = [float(c) for c in cases]
            fig_unit = bool(p.get('fig_unit', True))
            made, lines = export_ratio_tex(
                p['mgt_path'], out_dir,
                result.beam_ratio, result.truss_ratio,
                [str(n) for n in result.LCNAME],
                cases_select=cases, axes_select=None,
                select_unit=(0.0 if fig_unit else float('inf')),
                limit_sec_no=float(p.get('limit_sec_no', 9000)))
        return jsonify({'files': [{'name': os.path.basename(f),
                                   'url': _register_file(f)}
                                  for f in made],
                        'tex_lines': lines[:600],
                        'out_dir': out_dir, 'notes': notes})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/ratio_detail_tex', methods=['POST'])
def api_ratio_detail_tex():
    """検定詳細TeX (10detail_matlabN.tex + 10detail.tex) の生成.

    直近の「検定実行」の結果 (maxratios_text) を使う。plot_ratio と同じく
    検定条件が変わっている場合は明示エラー。
    """
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    if (_CHECK_CACHE['result'] is None
            or _CHECK_CACHE['key'] != _check_cache_key(p)):
        return jsonify({'error':
            '検定結果がありません (または検定条件・入力ファイルが変更されて'
            'います)。先に「検定実行」を行ってから生成してください。'}), 400
    result = _CHECK_CACHE['result']
    mode = str(p.get('mode') or 'all')
    if mode not in ('all', 'pick'):
        return jsonify({'error': 'mode は all か pick を指定して'
                                 'ください。'}), 400
    try:
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'ratio_tex')
            # 木合板検定の詳細文も追加 (検定実行と同じ条件で再計算)
            extra_rows = None
            pw_path = (p.get('plywood_stress_path') or '').strip()
            if pw_path and os.path.isfile(pw_path):
                try:
                    from mgtkit.plywood import plywood_check
                    _pw_labels = ', '.join(
                        '%g:%s' % (float(row['no']), row.get('name') or '')
                        for row in (p.get('case_types') or [])
                        if row.get('name'))
                    pw_res = plywood_check(
                        p['mgt_path'], pw_path, labels=_pw_labels,
                        qa_base=float(p.get('pw_qa', 1.96)))
                    _rows = _plywood_check_rows(result, pw_res)
                    extra_rows = [
                        {'texts': r['texts'],
                         'ratios': [(c['ratio'] if c else None)
                                    for c in r['cells']]}
                        for r in _rows]
                except Exception as e:  # noqa: BLE001
                    print('注意: 木合板検定の詳細文生成でエラーのため'
                          'スキップしました: %s' % e)
            files = export_ratio_detail_tex(result, out_dir, mode=mode,
                                            extra_rows=extra_rows)
        return jsonify({'files': [{'name': os.path.basename(fp),
                                   'url': _register_file(fp)}
                                  for fp in files],
                        'notes': notes, 'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


@app.route('/api/export_dxf', methods=['POST'])
def api_export_dxf():
    p = request.get_json(force=True)
    err = _check_input_file(p.get('mgt_path'), 'mgtファイル')
    if err:
        return jsonify({'error': err}), 400
    try:
        notes = []
        with _capture_notes(notes):
            out_dir = _out_dir(p, 'dxf')
        base = os.path.splitext(os.path.basename(p['mgt_path']))[0]
        out_path = os.path.join(out_dir, base + '.dxf')
        sec_path = os.path.join(out_dir, base + '_section.dxf')
        with _capture_notes(notes):
            export_dxf(p['mgt_path'], out_path=out_path,
                       section_out_path=sec_path,
                       limit_sec_no=float(p.get('limit_sec_no', 9000)),
                       fontsize=float(p.get('fontsize', 400)))
        files = []
        for f in (out_path, sec_path):
            if os.path.isfile(f):
                files.append({'name': os.path.basename(f),
                              'url': _register_file(f) + '&dl=1'})
        return jsonify({'files': files, 'notes': notes, 'out_dir': out_dir})
    except Exception as e:  # noqa: BLE001
        return _error_response(e)


app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True


@app.after_request
def _no_cache(resp):
    ct = resp.headers.get('Content-Type', '')
    if 'text/html' in ct or 'javascript' in ct or 'text/css' in ct:
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
    return resp


if __name__ == '__main__':
    port = int(os.environ.get('MGTKIT_PORT', '8765'))
    import socket
    _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if _s.connect_ex(('127.0.0.1', port)) == 0:
        _s.close()
        print('=' * 60)
        print('mgtkitは既に起動しています(別の黒いウィンドウが開いたままです)。')
        print('古いバージョンが動き続けている可能性があるので、')
        print('すべてのmgtkitウィンドウを閉じてから、起動.batをやり直してください。')
        print('=' * 60)
        try:
            input('Enterキーでこのウィンドウを閉じます...')
        except EOFError:
            pass
        raise SystemExit(1)
    _s.close()
    print('mgtkit app: http://127.0.0.1:%d' % port)
    if os.environ.get('MGTKIT_NO_BROWSER') != '1':
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:%d' % port)).start()
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
