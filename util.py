# -*- coding: utf-8 -*-
"""privatetool_function 直下の共通ヘルパー群の逐語移植.

元コード:
  msrc/privatetool_function/find_index.m
  msrc/privatetool_function/find_index_rough.m
  msrc/privatetool_function/find_zero_index.m
  msrc/privatetool_function/MIDAS/doublecheck.m
  msrc/privatetool_function/MIDAS/doublecheck2.m
  msrc/privatetool_function/space_erace.m
  msrc/privatetool_function/skip_erace.m
  msrc/privatetool_function/pick_text.m
  msrc/privatetool_function/cell2matrix.m
  msrc/privatetool_function/excelround.m
  msrc/privatetool_function/MIDAS/mgt/get_byto.m  (mgtパーサ専用だが共通利用のためここに置く)

インデックス規約:
  MATLAB版 find_index / find_zero_index は 1-based index を返し、
  見つからない場合 0 を返す(呼び側が ==0 判定)。
  Python版は 0-based index を返し、見つからない場合 -1 を返す。
"""

import math

import numpy as np


# ---------------------------------------------------------------------------
# 内部ユーティリティ (MATLAB組込み関数の挙動再現)
# ---------------------------------------------------------------------------

def _str2double(s):
    """MATLAB str2double 相当。数値に変換できない場合は NaN を返す。"""
    if not isinstance(s, str):
        # MATLAB str2double(数値) は NaN (文字列以外は変換不可)
        try:
            return float(s)
        except (TypeError, ValueError):
            return float('nan')
    try:
        return float(s.strip())
    except ValueError:
        return float('nan')


def _str2num(s):
    """MATLAB str2num 相当(mgtフィールド用の簡易版)。

    空白区切りの全トークンが数値なら float のリストを返す。
    空文字列・非数値を含む場合は None を返す (MATLABの [] に対応)。
    """
    toks = s.split()
    if not toks:
        return None
    vals = []
    for t in toks:
        try:
            vals.append(float(t))
        except ValueError:
            return None
    return vals


def _matlab_round(x):
    """MATLAB round: 0.5は0から遠い方へ丸める (Pythonのroundは偶数丸めのため)."""
    return np.trunc(x + np.copysign(0.5, x))


def _deblank(s):
    """MATLAB deblank: 末尾の空白文字(空白・タブ・改行等)を除去."""
    return s.rstrip()


def _colon(a, step, b):
    """MATLAB のコロン演算子 a:step:b 相当 (1次元 float 配列を返す)."""
    if any(isinstance(v, float) and math.isnan(v) for v in (a, step, b)):
        return np.array([], dtype=float)
    if step == 0:
        return np.array([], dtype=float)
    n = math.floor((b - a) / step)
    if n < 0:
        return np.array([], dtype=float)
    return a + step * np.arange(n + 1, dtype=float)


# ---------------------------------------------------------------------------
# find_index.m
# ---------------------------------------------------------------------------

def find_index(base_data, search):
    """あるベクトルデータの中にある探索値のインデックスを返す.

    返るインデックスはインデックスのうちで最小のものとする．

    MATLAB版: 見つからない場合は 0 を返す (1-based)。
    Python版: 0-based index を返し、見つからない場合は -1 を返す。

    探索値もベクトルでの入力が可能 (MATLAB版と同様)。
    searchがスカラの場合は int を、ベクトルの場合は同じ長さの
    int ndarray を返す。

    MATLAB版同様、一致判定は min(abs(base_data - search)) == 0
    による完全一致 (最小インデックスの一致要素を返す)。
    base_data が空の場合は MATLAB 版同様エラーとなる。
    """
    base = np.asarray(base_data, dtype=float).ravel()
    scalar_in = np.isscalar(search) or np.asarray(search).ndim == 0
    s = np.atleast_1d(np.asarray(search, dtype=float)).ravel()
    index = np.full(s.shape, -1, dtype=int)
    for i in range(s.size):
        d = np.abs(base - s[i])
        j = int(np.argmin(d))  # base空ならMATLAB同様エラー(ValueError)
        if d[j] == 0:
            index[i] = j
        else:
            index[i] = -1
    if scalar_in:
        return int(index[0])
    return index


# ---------------------------------------------------------------------------
# find_zero_index.m
# ---------------------------------------------------------------------------

def find_zero_index(data):
    """ベクトルデータの中で0である要素のindexを返す.

    MATLAB版は 1-based、Python版は 0-based の int ndarray を返す。
    該当なしの場合は空配列。
    """
    arr = np.asarray(data, dtype=float).ravel()
    return np.where(arr == 0)[0]


# ---------------------------------------------------------------------------
# MIDAS/doublecheck.m
# ---------------------------------------------------------------------------

def doublecheck(original):
    """一列のベクトルを重複削り昇順並び替え.

    1次元 ndarray を返す (MATLAB版は列ベクトル)。空入力はそのまま返す。
    """
    arr = np.asarray(original, dtype=float).ravel()
    if arr.size == 0:
        return arr
    arr = np.sort(arr)
    judge = np.diff(arr)
    remain = np.where(judge)[0] + 1
    remain = np.concatenate(([0], remain))
    return arr[remain]


# ---------------------------------------------------------------------------
# MIDAS/doublecheck2.m
# ---------------------------------------------------------------------------

def doublecheck2(original):
    """二次元要素を重複削り昇順並び替え (行単位の重複除去).

    2次元 ndarray を返す。1行のみの場合はそのまま返す (MATLAB版同様)。
    """
    arr = np.asarray(original, dtype=float)
    n = arr.shape[1]
    if arr.shape[0] == 1:
        return arr
    # sortrows(original, 1:n) : 1列目から順の辞書式ソート
    order = np.lexsort(tuple(arr[:, k] for k in reversed(range(n))))
    arr = arr[order]
    judge = np.abs(np.diff(arr, axis=0))
    judge_deleet = judge.sum(axis=1)
    remain = np.where(judge_deleet)[0] + 1
    remain = np.concatenate(([0], remain))
    return arr[remain, :]


# ---------------------------------------------------------------------------
# space_erace.m
# ---------------------------------------------------------------------------

def space_erace(string):
    """文字列中の半角スペースを全て除去する.

    MATLAB版は数値が渡された場合そのまま返る(strfindが空を返す)ため、
    文字列以外の入力はそのまま返す。
    """
    if not isinstance(string, str):
        return string
    return string.replace(' ', '')


# ---------------------------------------------------------------------------
# skip_erace.m
# ---------------------------------------------------------------------------

def skip_erace(string):
    """連続する半角スペースのうち後続を持つものを除去する(1パスのみ).

    MATLAB版同様、スペース位置を1回だけ走査し、直後もスペースである
    位置の文字を削除する (例: 'a   b' -> 'a b')。
    """
    clear_index = []
    find_space = [i for i, ch in enumerate(string) if ch == ' ']
    if find_space:
        for i in range(len(find_space) - 1):
            if find_space[i + 1] - find_space[i] == 1:
                clear_index.append(find_space[i])
        if clear_index:
            drop = set(clear_index)
            string = ''.join(ch for j, ch in enumerate(string) if j not in drop)
    return string


# ---------------------------------------------------------------------------
# pick_text.m
# ---------------------------------------------------------------------------

def pick_text(original_text, target, range_):
    """targetの位置を区切りとして文字列を切り出す (pick_text.m の逐語移植).

    range_=-1: target出現位置の直前まで (original_text(1:cut-1))
    range_= 1: target出現位置の次の文字から末尾まで (original_text(cut+1:end))
               ※MATLAB版同様、targetが複数文字でも1文字しか飛ばさない。
    見つからない場合は original_text をそのまま返す。
    """
    cut_index = original_text.find(target)
    if cut_index == -1:
        return original_text
    elif range_ == -1:
        return original_text[:cut_index]
    elif range_ == 1:
        return original_text[cut_index + 1:]
    return original_text


# ---------------------------------------------------------------------------
# cell2matrix.m
# ---------------------------------------------------------------------------

def cell2matrix(cell_data, direction, pickup_num):
    """セルデータの列，もしくは行に数値データのみが格納されている場合に
    行列データへと変換するサブプログラム.

    directionは[1]のとき列方向を書き出し[2]のとき行方向を書き出し。
    cell_data は2次元の list of list (MATLAB cell 相当)。
    pickup_num は MATLAB互換の 1-based 指定 (取り出す列/行の番号)。

    direction=1: 各行 i の cell_data[i][pickup_num-1] を縦に連結
    direction=2: 各列 i の cell_data[pickup_num-1][i] を横に連結
    """
    matrix = None
    if direction == 1:
        rows = [np.atleast_2d(np.asarray(cell_data[i][pickup_num - 1], dtype=float))
                for i in range(len(cell_data))]
        rows = [r for r in rows if r.size > 0]
        matrix = np.vstack(rows) if rows else np.array([])
    elif direction == 2:
        cols = [np.atleast_2d(np.asarray(cell_data[pickup_num - 1][i], dtype=float))
                for i in range(len(cell_data[pickup_num - 1]))]
        cols = [c for c in cols if c.size > 0]
        matrix = np.hstack(cols) if cols else np.array([])
    else:
        print('取り出し行・列の指定ミス')
        raise RuntimeError('cell2matrix: 取り出し行・列の指定ミス')
    return matrix


# ---------------------------------------------------------------------------
# excelround.m
# ---------------------------------------------------------------------------

def excelround(b, c):
    """Excel互換丸め: bを有効数字c桁に丸める.

    元コードの式を正確に再現:
      a = round((b/10^floor(log10(b)))*10^(c-1)) * 10^(floor(log10(b))-(c-1))
    MATLABのroundは0.5を0から遠い方へ丸めるため _matlab_round を使用。
    """
    e = np.floor(np.log10(b))
    return float(_matlab_round((b / 10.0 ** e) * 10.0 ** (c - 1)) *
                 10.0 ** (e - (c - 1)))


# ---------------------------------------------------------------------------
# MIDAS/mgt/get_byto.m
# ---------------------------------------------------------------------------

def get_byto(string_byto):
    """'1to9by2' 等のmgt番号リスト表記を数値列に展開する.

    - 'by'を含む場合: start to end by step -> start:step:end
    - 'to'のみの場合: start to end -> start:end
    - どちらも無い場合: str2double した値 (1要素配列; 非数値ならNaN)
    戻り値は1次元 float ndarray (MATLAB版は行ベクトル/スカラ)。
    """
    findby = string_byto.find('by')
    if findby == -1:
        findto = string_byto.find('to')
        if findto == -1:
            return np.array([_str2double(string_byto)], dtype=float)
        else:
            findto1 = _str2double(string_byto[:findto])
            findto2 = _str2double(string_byto[findto + 2:])
            return _colon(findto1, 1.0, findto2)
    else:
        # byがあるときは必ずtoがある
        by = _str2double(string_byto[findby + 2:])  # byに続く数字を拾う
        string_byto = string_byto[:findby]
        findto = string_byto.find('to')
        if findto == -1:
            findby1 = float('nan')
            findby2 = float('nan')
        else:
            findby1 = _str2double(string_byto[:findto])  # 区間のスタート部分
            findby2 = _str2double(string_byto[findto + 2:])  # 区間の終了部分
        return _colon(findby1, by, findby2)
# ---------------------------------------------------------------------------
# find_index_rough.m
# ---------------------------------------------------------------------------

def find_index_rough(base_data, search):
    """あるベクトルデータの中で探索値に最も近い要素のインデックスを返す.

    min(abs(base_data - search)) による最近傍探索 (必ず見つかる)。
    MATLAB版は 1-based、Python版は 0-based のインデックスを返す。
    探索値はスカラまたはベクトルで入力可能 (ベクトル入力時は同じ長さの
    int ndarray を返す)。MATLAB版同様、探索値が行列(2次元)の場合はエラー。
    最小値が複数ある場合は最小インデックス (MATLAB min と同じ)。
    """
    base = np.asarray(base_data, dtype=float).ravel()
    scalar_in = np.isscalar(search) or np.asarray(search).ndim == 0
    s = np.atleast_2d(np.asarray(search, dtype=float))
    m, n = s.shape
    ONE = min(m, n)
    if ONE != 1:
        raise ValueError('入力データエラー (find_index_rough)')  # MATLAB: stop
    sv = s.ravel()
    index = np.zeros(sv.size, dtype=int)
    for i in range(sv.size):
        index[i] = int(np.argmin(np.abs(base - sv[i])))
    if scalar_in:
        return int(index[0])
    return index


def loadtxt_tolerant(path):
    """数値テキストの寛容読込 (応力txt等のユーザー入力ファイル用).

    - 文字コードは UTF-8 (BOM可) / CP932 を自動判定
    - 見出し行 (「要素 荷重 軸力…」等)・空行・タブのみの行はスキップ
      (MIDASの見出しつき書き出しへの対応。読み飛ばした行数は注記表示)
    - 数値行の列数が不揃いの場合は行番号つきでエラー
    戻り値: ndarray (N x M, float, 2次元)
    """
    import os as _os
    with open(path, 'rb') as f:
        raw = f.read()
    text = None
    for enc in ('utf-8-sig', 'cp932', 'utf-8'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode('utf-8', errors='replace')
    rows = []
    skipped = 0
    ncol = None
    ncol_line = 0
    for lineno, ln in enumerate(text.splitlines(), 1):
        t = ln.strip()
        if not t:
            continue
        toks = t.replace(',', ' ').split()
        try:
            vals = [float(v) for v in toks]
        except ValueError:
            skipped += 1
            continue
        if ncol is None:
            ncol = len(vals)
            ncol_line = lineno
        elif len(vals) != ncol:
            raise ValueError(
                '%s の %d 行目の列数(%d)が %d 行目(%d列)と一致しません'
                % (_os.path.basename(str(path)), lineno, len(vals),
                   ncol_line, ncol))
        rows.append(vals)
    if skipped:
        print('注記: %s の見出しなど数値でない %d 行を読み飛ばしました'
              % (_os.path.basename(str(path)), skipped))
    if not rows:
        raise ValueError('%s に数値データがありません'
                         % _os.path.basename(str(path)))
    return np.asarray(rows, dtype=float)
