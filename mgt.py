# -*- coding: utf-8 -*-
"""MIDAS/Gen mgtファイルパーサ群の逐語移植 (mgtopen_section を除く).

元コード: msrc/privatetool_function/MIDAS/mgt/ 配下の各 mgtopen_*.m、
wall/mgtopen_wall.m, wall/mgtopen_RCwall.m, wall/wall_unit.m,
および MIDAS/node_index_get.m。

共通事項:
- mgtファイルは CP932。open(filename, encoding='cp932', errors='replace') で読む。
- 戻り値の数値行列は numpy.ndarray (float)。列の意味はMATLAB版と同一。
- 節点番号・要素番号・断面番号等のID値はデータ値なので 1-based のまま保持。
- MATLABのcell配列に対応する戻り値は Python の list。
- セクションの探索は MATLAB 版と同一のロジック
  (マーカー文字列を含む行を探し、開始行 = マーカー行 + オフセット、
   終了行 = 次に '*' を含む行 - 2) を再現する。
- MATLAB版でマーカー行が存在しないとファイル末尾で無限ループ/エラーとなる
  関数は、Python版では行リストの範囲外アクセスで IndexError となる。
"""

import numpy as np

from .util import (
    find_index,
    find_zero_index,
    space_erace,
    get_byto,
    _str2double,
    _str2num,
    _deblank,
)


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _lines(filename):
    """mgtファイルを読み、fgets相当の行リスト(改行付き)を返す。CP932。"""
    with open(filename, encoding='cp932', errors='replace') as f:
        return f.readlines()


def _tlinenum(tline):
    """MATLABの tlinenum = [0 findstr(tline,',') length(tline)+1] を再現.

    値はMATLABの1-based位置のまま保持する。
    フィールド ii (1-based) は _field() で取り出す。
    """
    return [0] + [p + 1 for p, c in enumerate(tline) if c == ','] \
               + [len(tline) + 1]


def _field(tline, tn, ii):
    """MATLABの tline(tlinenum(ii)+1 : tlinenum(ii+1)-1) を再現 (iiは1-based)."""
    return tline[tn[ii - 1]: tn[ii] - 1]


def _slice(tline, a, b):
    """MATLABの tline(a:b) を再現 (a,bは1-based・両端含む)."""
    return tline[a - 1: b]


def _scan_section(lines, marker, offset, guard=None, guard_first=False):
    """セクション開始/終了行番号(1-based)を探すMATLAB共通パターンの再現.

    marker行(行番号L)を見つけたら start = L + offset。
    その後最初に '*' を含む行(行番号M)で end = M - 2。

    guard(通常 '*ENDDATA')が先に見つかった場合:
      guard_first=True  -> (-1, 0)  を返す (start=-1 系: thickness等)
      guard_first=False -> (0, 0)   を返す (end=0 系: elasticlink等)
    """
    i = 1
    while True:
        if i - 1 >= len(lines):
            raise ValueError(
                'mgtファイルが途中で終わっています(*ENDDATAが見つかりません)。'
                'エクスポートやコピーでファイル末尾が欠けていないか確認してください')
        tline = lines[i - 1]
        if guard_first and guard is not None and guard in tline:
            return -1, 0
        if marker in tline:
            start = i + offset
            j = i + 1
            while True:
                if '*' in lines[j - 1]:
                    return start, j - 2
                j += 1
        if (not guard_first) and guard is not None and guard in tline:
            return 0, 0
        i += 1


def _strcat(a, b):
    """MATLAB strcat (char配列): 各入力の末尾空白を除去して連結."""
    return a.rstrip() + b.rstrip()


def _join_continuation(lines, pos, tline):
    """'\\'で折り返された行を結合するMATLAB共通パターンの再現.

    posは次に読む行のindex(0-based)。結合後の(tline, pos, 消費行数)を返す。
    """
    consumed = 0
    while '\\' in tline:
        tline = _deblank(tline)          # 末尾空白削除(最後の文字が\となる)
        tline = tline[:len(tline) - 1]   # \削除
        tline2 = lines[pos]              # 続く次の行を読み込み
        pos += 1
        tline = _strcat(tline, tline2)   # 合体
        consumed += 1
    return tline, pos, consumed


def _to_matrix(rows):
    """行リストをndarrayへ。空なら空配列(MATLABの[])を返す。

    行長が不揃いの場合はMATLABの縦連結と同様エラー(ValueError)。
    """
    if not rows:
        return np.array([])
    n = len(rows[0])
    for r in rows:
        if len(r) != n:
            raise ValueError('行の要素数が不揃いです (MATLAB版でも縦連結エラー)')
    return np.array(rows, dtype=float)


def _grow_assign(mat, r, c, val):
    """MATLABの mat(r,c)=val (必要なら0埋めで自動拡張) を再現.

    r, c は 1-based。拡張後の行列を返す。
    """
    nr, nc = mat.shape
    if r > nr or c > nc:
        new = np.zeros((max(r, nr), max(c, nc)))
        new[:nr, :nc] = mat
        mat = new
    mat[r - 1, c - 1] = val
    return mat


# ---------------------------------------------------------------------------
# mgtopen_node.m
# ---------------------------------------------------------------------------

def mgtopen_node(filename):
    """mgtファイルより節点情報を取得する．

    戻り値 node: ndarray
      列: [節点番号, X, Y, Z] (*NODEセクションのカンマ区切り数値をそのまま)
      非数値フィールドがあった場合はMATLAB版同様、文字コードが並ぶ。
    """
    lines = _lines(filename)
    startnode, endnode = _scan_section(lines, '*NODE', 2)

    node = []
    for i in range(1, endnode + 1):
        tline = lines[i - 1]
        if i < startnode:
            continue
        tn = _tlinenum(tline)
        node_one = []
        for ii in range(1, len(tn)):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is None:
                # MATLAB: 文字列を数値配列に連結 -> 文字コード化
                node_one.extend(float(ord(ch)) for ch in _field(tline, tn, ii))
            else:
                node_one.extend(ttmp)
        node.append(node_one)
    return _to_matrix(node)


# ---------------------------------------------------------------------------
# mgtopen_element.m
# ---------------------------------------------------------------------------

def mgtopen_element(filename):
    """mgtファイルより要素情報を取得する．

    板要素(PLATE/PLSTRS/PLSTRN/AXISYM)・壁要素(WALL)は除外。
    先頭7フィールドの数値のみ取得 (梁・トラス要素のβ角度まで)。

    戻り値 element: ndarray
      列: [要素番号, 材料番号, 断面番号, 節点i, 節点j, β角度]
    """
    lines = _lines(filename)
    startelement, endelement = _scan_section(lines, '*ELEMENT', 5)

    element = []
    for i in range(1, endelement + 1):
        tline = lines[i - 1]
        if i < startelement:
            continue
        if ('WALL' in tline or 'PLATE' in tline or 'PLSTRS' in tline
                or 'PLSTRN' in tline or 'AXISYM' in tline):
            continue
        tn = _tlinenum(tline)
        element_one = []
        for ii in range(1, 8):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is None:
                pass  # BEAM等の文字データはとばす
            else:
                element_one.extend(ttmp)
        element.append(element_one)
    return _to_matrix(element)


# ---------------------------------------------------------------------------
# mgtopen_plate.m
# ---------------------------------------------------------------------------

def mgtopen_plate(filename):
    """mgtファイルより板要素情報を取得する．

    *ELEMENTセクション中の PLATE/PLSTRS/PLSTRN/AXISYM 行のみ取得。

    戻り値 plate: ndarray
      列: [要素番号, 材料番号, 厚さ番号, 節点1, 節点2, 節点3, 節点4, iSUB, iWID]
      (PLSTRS/PLSTRN/AXISYM の場合は8列目に要素種別 3/4/5 が上書きされる。
       PLATE=1, PLSTRS=3(平面応力要素), PLSTRN=4(平面変形要素),
       AXISYM=5(軸対称要素))
    """
    lines = _lines(filename)
    startplate, endplate = _scan_section(lines, '*ELEMENT', 6)

    plate = []
    plate_one1 = None  # MATLAB版ではループ間で値が持ち越される
    for i in range(1, endplate + 1):
        tline = lines[i - 1]
        if i < startplate:
            continue
        if ('PLATE' in tline or 'PLSTRS' in tline or 'PLSTRN' in tline
                or 'AXISYM' in tline):
            tn = _tlinenum(tline)
            plate_one = []
            for ii in range(1, len(tn)):
                ttmp = _str2num(_field(tline, tn, ii))
                if ttmp is None:
                    t = space_erace(_field(tline, tn, ii))
                    if t == 'PLATE':
                        plate_one1 = 1
                    elif t == 'PLSTRS':  # 平面応力要素
                        plate_one1 = 3
                    elif t == 'PLSTRN':  # 平面変形要素
                        plate_one1 = 4
                    elif t == 'AXISYM':  # 軸対称要素
                        plate_one1 = 5
                else:
                    plate_one.extend(ttmp)
            if plate_one1 is not None and plate_one1 > 2:
                while len(plate_one) < 8:
                    plate_one.append(0.0)
                plate_one[7] = float(plate_one1)
            plate.append(plate_one)
    return _to_matrix(plate)


# ---------------------------------------------------------------------------
# mgtopen_material.m
# ---------------------------------------------------------------------------

def _F_235_295_325(sub):
    """BCR/BCP/BSH系のF値判定 (235 -> 295 -> 325 の優先順、無ければエラー)."""
    if '235' in sub:
        return 235
    if '295' in sub:
        return 295
    if '325' in sub:
        return 325
    print('ERROR = 鉄骨材料の定義ミス')
    raise RuntimeError('鉄骨材料の定義ミス')  # MATLAB: stop


def mgtopen_material(filename, w_select=None):
    """mgtファイルより材料情報を取得する．

    materialファイルは4列より構成され，
    一列目はmidas上での材料番号(要素データの二列目の材料番号と対応）
    二列目は鉄骨（SN系）の場合「1」，鋼管（BCR,BCP系）の場合「2」，RCの場合「3」
    PCの場合「4」，SRCの場合「5」，CFTの場合「6」，RM（組積）造の場合「8」，
    木造の場合「10」，杭RCを「7」
    三列目は鉄骨（SN系）の場合「SNに続く数字」，鋼管（BCR,BCP系）の場合「F値」，
    RC・PCの場合「Fc」
    四列目は基本的に使わないが，鋼管（BCR,BCP系）の場合BCRかBCPかSTKR，BSHかを
    判別する。STKRが1，BCRが2，BCPが3，BSHが4を入力する

    戻り値 material: ndarray (N x 4)

    Python版の相違点 (対話UIのため):
      MATLAB版は 'W-' を含むUSER材料で W_AL.mat を読み listdlg により木材種別
      W_type_index を対話選択する。Python版は引数 w_select
      (材料名文字列 -> index を返す関数) が与えられればそれを使い、
      無ければ W_type_index=0 とし警告を表示する。
    """
    lines = _lines(filename)
    startmaterial, endmaterial = _scan_section(lines, '*MATERIAL', 7)

    material = []
    i = 1
    while i <= endmaterial:
        tline = lines[i - 1]
        if i < startmaterial:
            i += 1
            continue

        tn = _tlinenum(tline)
        material_one = []

        materialtype = space_erace(_field(tline, tn, 2))
        f1 = _str2double(_field(tline, tn, 1))

        if materialtype == 'STEEL':
            f3 = _field(tline, tn, 3)
            if 'BCR' in f3:
                material_one = [f1, 2, _F_235_295_325(f3), 2]
            elif 'BCP' in f3:
                material_one = [f1, 2, _F_235_295_325(f3), 3]
            elif 'BSH' in f3:
                material_one = [f1, 2, _F_235_295_325(f3), 4]
            elif 'STKR' in f3:
                if '400' in f3:
                    F = 235
                elif '490' in f3:
                    F = 325
                else:
                    print('ERROR = 鉄骨材料の定義ミス')
                    raise RuntimeError('鉄骨材料の定義ミス')  # MATLAB: stop
                material_one = [f1, 2, F, 1]
            elif '400' in f3:
                material_one = [f1, 1, 400, 0]
            elif '490' in f3:
                material_one = [f1, 1, 490, 0]
            elif '520' in f3:
                material_one = [f1, 1, 520, 0]
            elif 'SBPR' in f3:
                # SBPR1080系の短期許容応力度は1080x0.6=648
                material_one = [f1, 1, 1080, 0.6]
            else:
                print("ERROR = 鉄骨材料の定義ミス(400N割当)")
                material_one = [f1, 1, 400, 0]

        elif materialtype == 'CONC':
            f3 = _field(tline, tn, 3)
            findPc = f3.find('Pc')
            findFc = f3.find('Fc')
            findpile = f3.find('pile')
            findFm = f3.find('Fm')
            if findpile == -1:
                if findFc == -1 and findPc == -1 and findFm == -1:
                    print('ERROR = コンクリート材料の定義ミス')
                    raise RuntimeError('コンクリート材料の定義ミス')  # MATLAB: stop
                elif findFc != -1:
                    # MATLAB: tline(tlinenum(3)+findFc+2 : tlinenum(3)+findFc+3)
                    Fc = _slice(tline, tn[2] + (findFc + 1) + 2,
                                tn[2] + (findFc + 1) + 3)
                    Fc = space_erace(Fc)
                    Fc = _str2double(Fc)
                    Fc = space_erace(Fc)
                    # MATLAB: isempty(str2double(...))は常にfalse (NaNは非空)
                    material_one = [f1, 3, Fc, 0]
                elif findPc != -1:
                    Pc = _slice(tline, tn[2] + (findPc + 1) + 2,
                                tn[2] + (findPc + 1) + 3)
                    Pc = space_erace(Pc)
                    Pc = _str2double(Pc)
                    Pc = space_erace(Pc)
                    material_one = [f1, 4, Pc, 0]
                elif findFm != -1:
                    Fm = _slice(tline, tn[2] + (findFm + 1) + 2,
                                tn[2] + (findFm + 1) + 3)
                    Fm = space_erace(Fm)
                    Fm = _str2double(Fm)
                    Fm = space_erace(Fm)
                    material_one = [f1, 8, Fm, 0]
            else:
                if findFc == -1:
                    Fcpile = float('nan')  # MATLAB: 空レンジ -> str2double('')=NaN
                else:
                    Fcpile = _str2double(space_erace(
                        _slice(tline, tn[2] + (findFc + 1) + 2,
                               tn[2] + (findFc + 1) + 3)))
                material_one = [f1, 7, Fcpile, 0]

        elif materialtype == 'SRC':
            f3 = _field(tline, tn, 3)
            findFc = f3.find('Fc')
            F = 0
            if findFc == -1:
                print('ERROR = SRC材料の定義ミス')
                raise RuntimeError('SRC材料の定義ミス')  # MATLAB: stop
            else:
                Fc = _slice(tline, tn[2] + (findFc + 1) + 2,
                            tn[2] + (findFc + 1) + 3)
                Fc = space_erace(Fc)
                Fc = _str2double(Fc)
                Fc = space_erace(Fc)
            f13 = _field(tline, tn, 13)
            # 13番目のフィールドに鉄骨情報が無ければ3番目のフィールドを使う
            if ('400' not in f13 and '490' not in f13 and '520' not in f13
                    and 'BCR' not in f13 and 'BCP' not in f13
                    and 'BSH' not in f13 and 'STKR' not in f13):
                fsteel = f3
            else:
                fsteel = f13
            if 'BCR' in fsteel:
                F = _F_235_295_325(f13)
            elif 'BCP' in fsteel:
                F = _F_235_295_325(f13)
            elif 'BSH' in fsteel:
                F = _F_235_295_325(f13)
            elif 'STKR' in fsteel:
                if '400' in f13:
                    F = 235
                elif '490' in f13:
                    F = 325
                else:
                    print('ERROR = 鉄骨材料の定義ミス')
                    raise RuntimeError('鉄骨材料の定義ミス')  # MATLAB: stop
            elif '400' in fsteel:
                F = 400
            elif '490' in fsteel:
                F = 490
            elif '520' in fsteel:
                F = 520
            else:
                print('ERROR = 鉄骨材料の定義ミス')
            if 'CFT' not in tline:
                material_one = [f1, 5, Fc, F]
            else:
                material_one = [f1, 6, Fc, F]

        elif materialtype == 'USER':
            f3 = _field(tline, tn, 3)
            findW = f3.find('W-')
            if findW == -1:
                mname_user = space_erace(f3)
                if 'CLT' in mname_user or 'W_' in mname_user or 'W-' in mname_user:
                    # CLT/timber defined as USER -> register as wood (type 10);
                    # col3=0 placeholder
                    material_one = [f1, 10, 0, 0]
                else:
                    print('材料の定義ミス：' + f3)
                    # unrecognized USER -> placeholder (keeps material aligned
                    # with material_name; prevents find_index=0)
                    material_one = [f1, 0, 0, 0]
            else:
                # MATLAB版: load W_AL.mat; listdlg で木材種別を対話選択
                W = space_erace(f3)
                if w_select is not None:
                    W_type_index = w_select(W)
                else:
                    print('警告: 木材種別の対話選択(listdlg)は非対応のため '
                          'W_type_index=0 とします: ' + W)
                    W_type_index = 0
                if W == '':
                    print('ERROR = 木の定義ミス')
                    raise RuntimeError('木の定義ミス')  # MATLAB: stop
                material_one = [f1, 10, W_type_index, 0]

        else:
            print('ERROR = 材料タイプの定義ミス')
            print(_field(tline, tn, 3))
            # MATLAB版はstopがコメントアウトされており行は追加されない

        if material_one:
            material.append(material_one)
        i += 1
    return _to_matrix(material)


def mgtopen_wood_materials(filename, w_select=None):
    """mgtの木材料 (mgtopen_material で type 10 となる材料) の一覧を返す (UI用).

    mgtopen_material の USER 材料の木判定 ('W-' を含むフィールド、または
    空白除去後の名前に CLT/W_/W- を含む) と同一の条件で抽出する。
    w_select (材料名 -> W_AL 行番号(1-based) を返す関数, 例 w_check.w_al_match)
    を与えると自動対応の結果を w_index に入れる (不明は 0)。

    戻り値: list[dict] {'no': 材料番号, 'name': 材料名(空白除去済),
                        'w_index': W_AL行番号 (0=未確定)}
    """
    lines = _lines(filename)
    startmaterial, endmaterial = _scan_section(lines, '*MATERIAL', 7)

    out = []
    i = 1
    while i <= endmaterial:
        tline = lines[i - 1]
        if i < startmaterial:
            i += 1
            continue
        tn = _tlinenum(tline)
        materialtype = space_erace(_field(tline, tn, 2))
        if materialtype == 'USER':
            f3 = _field(tline, tn, 3)
            name = space_erace(f3)
            if ('W-' in f3 or 'CLT' in name or 'W_' in name
                    or 'W-' in name):
                w_index = int(w_select(name)) if w_select is not None else 0
                out.append({'no': _str2double(_field(tline, tn, 1)),
                            'name': name, 'w_index': w_index})
        i += 1
    return out


# ---------------------------------------------------------------------------
# mgtopen_material_name.m
# ---------------------------------------------------------------------------

def mgtopen_material_name(filename):
    """mgtファイルより材料名(*MATERIALの3番目フィールド)を取得する．

    戻り値 material_name: list of str (MATLAB版はstrvcatによるchar行列)。
    空白除去して空になる名前はスキップされる (MATLAB版と同一)。
    文字列は生のフィールド (前後の空白を含む) のまま返す。
    """
    lines = _lines(filename)
    startmaterial, endmaterial = _scan_section(lines, '*MATERIAL', 7)

    material_name = []
    i = 1
    while i <= endmaterial:
        tline = lines[i - 1]
        if i < startmaterial:
            i += 1
            continue
        tn = _tlinenum(tline)
        materialname = _field(tline, tn, 3)
        if space_erace(materialname) == '':
            pass
        else:
            material_name.append(materialname)
        i += 1
    return material_name


# ---------------------------------------------------------------------------
# mgtopen_thickness.m
# ---------------------------------------------------------------------------

def mgtopen_cable(filename):
    """mgtファイルよりケーブル位置情報(*PRESTRESS)を取得する (mgtopen_cable.m).

    戻り値 cable_delta: ndarray (N x 4)
      1列目: 要素番号 / 2-4列目: i端・中央・j端でのケーブル偏心
      (原典: 各行のカンマ区切り [1 4 5 6] 列を採用)
    *PRESTRESSが無い場合は空配列
    (原典は無限ループ→エラー停止だが、PC無しモデルでの呼出しを考慮して空を返す)。
    """
    lines = _lines(filename)
    n = -1
    for i, tline in enumerate(lines):
        if '*PRESTRESS' in tline:
            n = i + 1  # 1-based
            break
    if n == -1:
        return np.array([])
    m = -1
    for k in range(n, len(lines)):  # 次の '*' を探す (0-based k = 1-based k+1行)
        if '*' in lines[k]:
            m = k + 1  # 1-based
            break
    if m == -1:
        m = len(lines) + 1
    startcable = n + 2
    endcable = m - 4  # NOTE: 原典どおりのオフセット (mgtopen_cable.m 22行)
    cable_delta = []
    for i in range(startcable, endcable + 1):  # 1-based [startcable, endcable]
        tline = lines[i - 1]
        parts = tline.split(',')
        cable_one = []
        for p in parts:
            p = space_erace(p)
            try:
                cable_one.append(float(p))
            except ValueError:
                pass
        if len(cable_one) >= 6:
            cable_delta.append([cable_one[0], cable_one[3],
                                cable_one[4], cable_one[5]])
    return np.asarray(cable_delta, dtype=float)


def mgtopen_thickness(filename):
    """mgtファイルより板要素・壁要素厚み情報を取得する．

    戻り値 thickness: ndarray (N x 3)
      1列目: 厚さID
      2列目: 厚みサイズ (THIK-IN * 1000)
      3列目: 厚みサイズ (THIK-OUTが0ならTHIK-IN、それ以外はTHIK-OUT * 1000)
    *THICKNESSが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startthick, endthick = _scan_section(lines, '*THICKNESS', 11,
                                         guard='*ENDDATA', guard_first=True)
    if startthick == -1:
        return np.array([])

    NO_thick = endthick + 1 - startthick
    thickness = np.zeros((NO_thick, 3))
    for iwall in range(1, NO_thick + 1):
        tline = lines[startthick - 1 + (iwall - 1)]
        tn = _tlinenum(tline)
        thickness[iwall - 1, 0] = _str2double(_field(tline, tn, 1))  # 厚さID
        thickness[iwall - 1, 1] = _str2double(_field(tline, tn, 5)) * 1000  # 厚みサイズ
        if _str2double(_field(tline, tn, 6)) * 1000 == 0:
            thickness[iwall - 1, 2] = _str2double(_field(tline, tn, 5)) * 1000
        else:
            thickness[iwall - 1, 2] = _str2double(_field(tline, tn, 6)) * 1000
    return thickness


def mgtopen_thickness_name(filename):
    """mgtファイルより厚さIDと名称(*THICKNESSの3番目フィールド)の対応を取得する.

    新規追加 (MATLAB原典なし、UIの厚み一覧表示用)。mgtopen_thickness と同じ
    区間を走査し、3番目フィールド(NAME、例 'S20')を厚さIDごとに集める。

    戻り値: {厚さID(int): 名称(str)}。*THICKNESSが無い場合は空dict。
    """
    lines = _lines(filename)
    startthick, endthick = _scan_section(lines, '*THICKNESS', 11,
                                         guard='*ENDDATA', guard_first=True)
    if startthick == -1:
        return {}

    names = {}
    for iwall in range(1, endthick + 2 - startthick):
        tline = lines[startthick - 1 + (iwall - 1)]
        tn = _tlinenum(tline)
        tid = int(_str2double(_field(tline, tn, 1)))
        names[tid] = space_erace(_field(tline, tn, 3))
    return names


# ---------------------------------------------------------------------------
# mgtopen_group.m
# ---------------------------------------------------------------------------

def mgtopen_group(filename):
    """mgtファイルよりグループ情報（通り情報）を取得する．

    戻り値: (axis_name, axis_elenum, axis_nodnum)
      axis_name  : list of str  グループ名 (空白除去済み)
      axis_elenum: list of ndarray  各グループに属する要素番号
      axis_nodnum: list of ndarray  各グループに属する節点番号
      (要素・節点の無いグループは空配列)
    *GROUPより先に*MATERIALが見つかった場合は全て空。
    """
    lines = _lines(filename)
    startgroup, endgroup = _scan_section(lines, '*GROUP', 2,
                                         guard='*MATERIAL', guard_first=False)

    axis_name = []     # グループ名
    axis_node = []     # グループに属する節点 (文字列)
    axis_element = []  # グループに属する要素 (文字列)

    pos = 0
    i = 1
    while i <= endgroup:
        tline = lines[pos]
        pos += 1
        i += 1
        if i - 1 < startgroup:
            continue
        # 折り返し行('\')の結合
        tline, pos, consumed = _join_continuation(lines, pos, tline)
        i += consumed
        tn = _tlinenum(tline)

        axisname_one = _deblank(_field(tline, tn, 1)).replace(' ', '')
        axis_name.append(axisname_one)
        axisnode_one = _deblank(_field(tline, tn, 2))
        axisele_one = _deblank(_field(tline, tn, 3))

        # 要素や節点がないグループがあった場合には0を入力しておく
        if axisnode_one == '':
            axis_node.append('0')
        else:
            axis_node.append(_deblank(_field(tline, tn, 2)))
        if axisele_one == '':
            axis_element.append('0')
        else:
            axis_element.append(_deblank(_field(tline, tn, 3)))

    axis_number = len(axis_name)

    def _expand(strings, igroup):
        s = strings[igroup]
        out = np.array([], dtype=float)
        axisnum = [0] + [p + 1 for p, c in enumerate(s) if c == ' '] \
                      + [len(s) + 1]
        for k in range(1, len(axisnum)):
            axismp = s[axisnum[k - 1]: axisnum[k] - 1]
            # スペースが連続している場合はaxismpが空となり飛ばされる
            if axismp == '':
                continue
            out = np.concatenate([out, get_byto(axismp)])
        return out

    # 通り芯ごとの要素番号抽出
    axis_elenum = []
    for igroup in range(axis_number):
        axis_elenum.append(_expand(axis_element, igroup))
    # グループに属する要素がないときの検出（仮想的にその場合0が入っている）
    for igroup in range(axis_number):
        a = axis_elenum[igroup]
        if a.size > 0 and np.all(a == 0):
            axis_elenum[igroup] = np.array([], dtype=float)

    # 通り芯ごとの節点番号抽出
    axis_nodnum = []
    for igroup in range(axis_number):
        axis_nodnum.append(_expand(axis_node, igroup))
    for igroup in range(axis_number):
        a = axis_nodnum[igroup]
        if a.size > 0 and np.all(a == 0):
            axis_nodnum[igroup] = np.array([], dtype=float)

    return axis_name, axis_elenum, axis_nodnum


# ---------------------------------------------------------------------------
# mgtopen_beam.m
# ---------------------------------------------------------------------------

def mgtopen_beam(filename):
    """mgtファイルより梁要素(BEAM)情報を取得する．

    戻り値 beam: ndarray
      列: [要素番号, 材料番号, 断面番号, 節点i, 節点j, β角度]
    """
    lines = _lines(filename)
    startbeam, endbeam = _scan_section(lines, '*ELEMENT', 5)

    beam = []
    for i in range(1, endbeam + 1):
        tline = lines[i - 1]
        if i < startbeam:
            continue
        if 'BEAM' not in tline:
            continue
        tn = _tlinenum(tline)
        beam_one = []
        for ii in range(1, 8):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is not None:
                beam_one.extend(ttmp)
        beam.append(beam_one)
    return _to_matrix(beam)


# ---------------------------------------------------------------------------
# mgtopen_truss.m
# ---------------------------------------------------------------------------

def mgtopen_truss(filename):
    """mgtファイルよりトラス要素(TRUSS/TENSTR)情報を取得する．

    戻り値 truss: ndarray
      列: [要素番号, 材料番号, 断面番号, 節点i, 節点j, β角度]
    注意: MATLAB版は開始行がELEMENTマーカー+6行 (mgtopen_beamは+5行) のため、
    データ1行目がトラスの場合は取得されない (元コードの挙動を踏襲)。
    """
    lines = _lines(filename)
    starttruss, endtruss = _scan_section(lines, '*ELEMENT', 6)

    truss = []
    for i in range(1, endtruss + 1):
        tline = lines[i - 1]
        if i < starttruss:
            continue
        if 'TRUSS' not in tline and 'TENSTR' not in tline:
            continue
        tn = _tlinenum(tline)
        truss_one = []
        for ii in range(1, 8):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is not None:
                truss_one.extend(ttmp)
        truss.append(truss_one)
    return _to_matrix(truss)


# ---------------------------------------------------------------------------
# mgtopen_elasticlink.m
# ---------------------------------------------------------------------------

def mgtopen_framerls(filename):
    """mgtファイルより梁端解放 (*FRAME-RLS, Beam End Release) を取得する.

    書式 (2行/要素):
      1行目: ELEM_LIST, bVALUE, FLAG-i, Fxi, Fyi, Fzi, Mxi, Myi, Mzi
      2行目:                    FLAG-j, Fxj, Fyj, Fzj, Mxj, Myj, Mzj, GROUP
    FLAGは 'FxFyFzMxMyMz' の6桁 (1=解放)。

    戻り値: dict {要素番号: (flag_i, flag_j)}  flagは6文字の文字列。
    ELEM_LIST が '1 to 5' 等の範囲表記の場合は展開する。
    構造図の描画用途 (曲げ解放 My/Mz を持つ端をピン接合とみなす)。
    """
    lines = _lines(filename)
    start, end = _scan_section(lines, '*FRAME-RLS', 3,
                               guard='*ENDDATA', guard_first=True)
    rls = {}
    if start == -1:
        return rls
    i = start - 1
    while i + 1 <= end:
        l1 = lines[i]
        l2 = lines[i + 1] if i + 1 < len(lines) else ''
        i += 2
        f1 = [s.strip() for s in l1.split(',')]
        f2 = [s.strip() for s in l2.split(',')]
        if len(f1) < 3 or len(f2) < 1:
            continue
        flag_i = f1[2].zfill(6)
        flag_j = f2[0].zfill(6)
        # 要素リスト ('1' / '1 2 3' / '1 to 5' / '1by2' はスペース区切り併用)
        for tok in f1[0].replace('to', ' to ').split():
            pass
        eles = []
        toks = f1[0].split()
        k = 0
        while k < len(toks):
            t = toks[k]
            if t.lower() == 'to' and eles and k + 1 < len(toks):
                for v in range(int(eles[-1]) + 1, int(float(toks[k + 1])) + 1):
                    eles.append(v)
                k += 2
                continue
            try:
                eles.append(int(float(t)))
            except ValueError:
                pass
            k += 1
        for e in eles:
            rls[int(e)] = (flag_i, flag_j)
    return rls


def mgtopen_elasticlink(filename):
    """mgtファイルより弾性連結(*ELASTICLINK)情報を取得する．

    戻り値 link: ndarray (N x 3)
      列: [連結番号, 節点1, 節点2]
      (リンク種別GEN/RIGID/TENS/COMPで列数が変わるため先頭3列のみ保持)
    *ELASTICLINKが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startlink, endlink = _scan_section(lines, '*ELASTICLINK', 5,
                                       guard='*ENDDATA', guard_first=False)

    link = []
    for i in range(1, endlink + 1):
        tline = lines[i - 1]
        if i < startlink:
            continue
        # 新しいMIDASはヘッダコメントが5行 (MULTI LINEAR行追加) あり
        # 原典想定のヘッダ行数を超えるため、コメント行・空行は読み飛ばす
        if not tline.strip() or tline.lstrip().startswith(';'):
            continue
        tn = _tlinenum(tline)
        link_one = []
        for ii in range(1, len(tn)):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is not None:
                link_one.extend(ttmp)
        if len(link_one) >= 3:
            link_one = link_one[:3]  # keep iNO,NODE1,NODE2 only
        if link_one:
            link.append(link_one)
    return _to_matrix(link)


# ---------------------------------------------------------------------------
# mgtopen_frm_rls.m
# ---------------------------------------------------------------------------

def mgtopen_frm_rls(filename):
    """mgtファイルより梁端の接合条件(*FRAME-RLS)を取得する．

    戻り値 frm_rls: ndarray (N x 8)
      列: [要素番号, 解放フラグ(6桁数値), Fx, Fy, Fz, Mx, My, Mz]
      i端行・j端行が交互に並ぶ (2行目=j端は数値7個のため、直前行の
      要素番号を先頭に補って8列に揃える。MATLAB版と同一)。
    *FRAME-RLSが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startfrm, endfrm = _scan_section(lines, '*FRAME-RLS', 3,
                                     guard='*ENDDATA', guard_first=False)

    frm_rls = []
    for i in range(1, endfrm + 1):
        tline = lines[i - 1]
        if i < startfrm:
            continue
        tn = _tlinenum(tline)
        frm_rls_one = []
        for ii in range(1, len(tn)):
            ttmp = _str2num(_field(tline, tn, ii))
            if ttmp is not None:
                frm_rls_one.extend(ttmp)
        if len(frm_rls_one) == 7:
            # 直前行の要素番号を先頭に付加 (先頭行が7個ならMATLAB版同様エラー)
            frm_rls.append([frm_rls[len(frm_rls) - 1][0]] + frm_rls_one)
        else:
            frm_rls.append(frm_rls_one)
    return _to_matrix(frm_rls)


# ---------------------------------------------------------------------------
# mgtopen_constraint.m
# ---------------------------------------------------------------------------

def mgtopen_constraint(filename):
    """mgtファイルより支点情報(*CONSTRAINT)を取得する．

    戻り値 constraint: ndarray (N x 2)
      1列目: 節点番号 (NODE_LISTの'to','by'表記は展開される)
      2列目: 拘束条件 (例 111000 -> 数値111000)
    *CONSTRAINTが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startconstraint, endconstraint = _scan_section(
        lines, '*CONSTRAINT', 2, guard='*ENDDATA', guard_first=False)

    constraint = []
    for i in range(1, endconstraint + 1):
        tline = lines[i - 1]
        if i < startconstraint:
            continue
        tn = _tlinenum(tline)
        ttmp1 = _field(tline, tn, 1)
        ttmp2 = _str2num(_field(tline, tn, 2))

        axisnod = []
        axisnum = [0] + [p + 1 for p, c in enumerate(ttmp1) if c == ' '] \
                      + [len(ttmp1) + 1]
        for k in range(1, len(axisnum)):
            axismp = ttmp1[axisnum[k - 1]: axisnum[k] - 1]
            # スペースが連続している場合はaxismpが空となり飛ばされる
            if axismp == '':
                continue
            axisnod.extend(get_byto(axismp).tolist())

        if ttmp2 is None:
            # MATLAB: constraint_one(:,2)=[] は列削除となる(通常発生しない)
            for nno in axisnod:
                constraint.append([nno])
        else:
            for nno in axisnod:
                constraint.append([nno, ttmp2[0]])
    return _to_matrix(constraint)


# ---------------------------------------------------------------------------
# mgtopen_secscale.m
# ---------------------------------------------------------------------------

def mgtopen_secscale(filename):
    """mgtファイルより断面剛性低減係数(*SECT-SCALE)を取得する．

    戻り値 section_scale: ndarray (N x 8)
      列: [iSEC, AREA_SF, ASY_SF, ASZ_SF, IXX_SF, IYY_SF, IZZ_SF, WGT_SF]
    *SECT-SCALEが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startscale, endscale = _scan_section(lines, '*SECT-SCALE', 2,
                                         guard='*ENDDATA', guard_first=True)
    if startscale == -1:
        return np.array([])

    NO_scale = endscale + 1 - startscale
    section_scale = np.zeros((NO_scale, 8))
    for iscale in range(1, NO_scale + 1):
        tline = lines[startscale - 1 + (iscale - 1)]
        tn = _tlinenum(tline)
        for j in range(1, 9):
            section_scale[iscale - 1, j - 1] = _str2double(_field(tline, tn, j))
    return section_scale


# ---------------------------------------------------------------------------
# mgtopen_FL.m
# ---------------------------------------------------------------------------

def mgtopen_FL(filename):
    """mgtファイルより階情報(*STORY)を取得する．

    戻り値 FL_name: list (N x 2 のcell相当)
      各要素: [フロア名(str, 空白除去済み・'NAME='接頭辞付きのまま),
               フロア高さ位置(float)]
    *STORYが無い場合(先に*ENDDATA)は空list。
    """
    lines = _lines(filename)
    startfloor, endfloor = _scan_section(lines, '*STORY', 3,
                                         guard='*ENDDATA', guard_first=True)
    if startfloor == -1:
        return []

    NO_floor = endfloor + 1 - startfloor
    FL_name = []
    for ifloor in range(1, NO_floor + 1):
        tline = lines[startfloor - 1 + (ifloor - 1)]
        tn = _tlinenum(tline)
        FL_name.append([space_erace(_field(tline, tn, 1)),      # フロア名
                        _str2double(_field(tline, tn, 2))])     # フロア高さ位置
    return FL_name


# ---------------------------------------------------------------------------
# mgtopen_length.m
# ---------------------------------------------------------------------------

def mgtopen_length(filename):
    """mgtファイルより設計長さ情報(*LENGTH)を取得する．

    戻り値: (lky, lkz, lb) 各 ndarray (N x 2)
      1列目: 要素番号、2列目: lky / lkz / lb の値
      2列目が0の行は削除される (MATLAB版と同一)。
    *LENGTHが無い場合(先に*ENDDATA)は全て空配列。
    """
    lines = _lines(filename)
    startlength, endlength = _scan_section(lines, '*LENGTH', 2,
                                           guard='*ENDDATA', guard_first=False)

    lky = []
    lkz = []
    lb = []

    pos = 0
    i = 1
    while i <= endlength:
        tline = lines[pos]
        pos += 1
        i += 1
        if i - 1 < startlength:
            continue
        tline, pos, consumed = _join_continuation(lines, pos, tline)
        i += consumed
        tn = _tlinenum(tline)
        # ','で「要素番号」「lky」「lkz」「Y/N」「lb」「Y/N」が区切られている
        ele_no_one = _field(tline, tn, 1).strip()
        lky_one = _str2double(_deblank(_field(tline, tn, 2)))
        lkz_one = _str2double(_deblank(_field(tline, tn, 3)))
        lb_one = _str2double(_deblank(_field(tline, tn, 5)))

        tsp = [0] + [p + 1 for p, c in enumerate(ele_no_one) if c == ' '] \
                  + [len(ele_no_one) + 1]
        for ispace in range(1, len(tsp)):
            tok = ele_no_one[tsp[ispace - 1]: tsp[ispace] - 1]
            if tok == '':
                continue
            ids = get_byto(tok)
            for e in ids:
                lky.append([e, lky_one])
                lkz.append([e, lkz_one])
                lb.append([e, lb_one])

    lky = _to_matrix(lky)
    lkz = _to_matrix(lkz)
    lb = _to_matrix(lb)

    if lky.size > 0:
        lky = lky[lky[:, 1] != 0, :]
    if lkz.size > 0:
        lkz = lkz[lkz[:, 1] != 0, :]
    if lb.size > 0:
        lb = lb[lb[:, 1] != 0, :]
    return lky, lkz, lb


# ---------------------------------------------------------------------------
# MIDAS/node_index_get.m (mgtopen_unit_2015が使用する共通関数)
# ---------------------------------------------------------------------------

def node_index_get(element_no, element_info, node_info):
    """要素番号から i端節点・j端節点の node_info 内での行indexを返す.

    戻り値: (nodei_index, nodej_index)  いずれも 0-based の int。
    要素・節点が見つからない場合は IndexError
    (MATLAB版はindex 0アクセスでエラーとなるのと同等)。
    """
    element_index = find_index(element_info[:, 0], element_no)
    if element_index == -1:
        raise IndexError('node_index_get: 要素番号 %s が見つかりません' % element_no)
    node_i_no = element_info[element_index, 3]
    nodei_index = find_index(node_info[:, 0], node_i_no)
    node_j_no = element_info[element_index, 4]
    nodej_index = find_index(node_info[:, 0], node_j_no)
    if nodei_index == -1 or nodej_index == -1:
        raise IndexError('node_index_get: 節点が見つかりません (要素 %s)' % element_no)
    return nodei_index, nodej_index


# ---------------------------------------------------------------------------
# mgtopen_unit_2015.m  (ファイル名は mgtopen_unit_2015.m、関数名は
#                       mgtopen_unit2015 だがここではファイル名に合わせる)
# ---------------------------------------------------------------------------

def mgtopen_unit_2015(filename, element, node):
    """mgtファイルより単一部材指定情報(*MEMBER)を取得する．

    引数:
      element: mgtopen_element の戻り値
      node   : mgtopen_node の戻り値

    戻り値 member: list (N x 3 のcell相当)。各要素は [c1, c2, c3]
      c1: ndarray [部材連番i]
      c2: ndarray 部材を構成する要素番号列 (先頭のiKEYは除去済み)
      c3: ndarray 部材に沿った節点番号列 (要素数+1個)
      bREVERSE=YES の部材は c2, c3 が逆順になる
      (MATLAB版の reverse 配列はフィルタ前の行番号で記録されフィルタ後の
       行番号で参照されるため1行ずれる。この挙動も踏襲)。
    *MEMBERが無い場合は空list。
    """
    lines = _lines(filename)
    member = []
    startmember, endmember = _scan_section(lines, '*MEMBER ', 2,
                                           guard='*ENDDATA', guard_first=False)
    if startmember == 0:
        startmember = 0  # MATLAB: startmember=0 のまま

    reverse = {}

    def _cell_row():
        return [[], [], []]

    if startmember > 0:
        member = [_cell_row() for _ in range(endmember - startmember + 1)]
        pos = 0
        i = 1
        while i <= endmember + 1:
            if pos >= len(lines):
                raise ValueError(
                    'mgtファイルが途中で終わっています(*MEMBERの後に*ENDDATAが'
                    'ありません)。エクスポートやコピーでファイルが欠けていないか'
                    '確認してください')
            tline = lines[pos]
            pos += 1
            i += 1
            if i < startmember + 1:
                continue
            tline, pos, consumed = _join_continuation(lines, pos, tline)
            i += consumed
            if i > endmember + 1:
                break
            tn = _tlinenum(tline)
            if len(tn) == 2:
                member = member[0: i - startmember]  # MATLAB: member(1:i-startmember,1:3)
            else:
                ridx = i - startmember + 1  # 1-based 行index
                # MATLAB: tline(tlinenum(2)+2 : tlinenum(3)-1)
                ttmp = _slice(tline, tn[1] + 2, tn[2] - 1)
                if ttmp == 'YES':
                    reverse[ridx] = -1
                else:
                    reverse[ridx] = 1
                member_one = []
                for ii in range(1, len(tn)):
                    tv = _str2num(_field(tline, tn, ii))
                    if tv is not None:
                        member_one.extend(tv)
                while len(member) < ridx:
                    member.append(_cell_row())
                member[ridx - 1][1] = member_one

    # 空行(要素の無い行)を除去
    cut = [k for k in range(len(member)) if len(member[k][1]) > 0]
    member = [member[k] for k in cut]

    for i in range(1, len(member) + 1):
        row = member[i - 1]
        # vriGen2015で修正: 先頭のiKEYを除去
        row[1] = list(row[1][1:])
        row[0] = [float(i)]
        n = len(row[1])
        row[2] = [0.0] * (n + 1)
        inode_index = []
        jnode_index = []
        for j in range(n):
            ii, jj = node_index_get(row[1][j], element, node)
            inode_index.append(ii)
            jnode_index.append(jj)
        for j in range(1, n):  # MATLAB j=2..n
            if jnode_index[j - 1] == inode_index[j]:
                row[2][j] = node[inode_index[j], 0]
            elif jnode_index[j - 1] == jnode_index[j]:
                row[2][j] = node[jnode_index[j], 0]
            elif inode_index[j - 1] == inode_index[j]:
                row[2][j] = node[inode_index[j], 0]
            elif inode_index[j - 1] == jnode_index[j]:
                row[2][j] = node[jnode_index[j], 0]
            else:
                raise RuntimeError(
                    'mgtopen_unit_2015: 部材を構成する要素が連結していません')  # MATLAB: stop
        if row[2][1] == node[inode_index[0], 0]:
            row[2][0] = node[jnode_index[0], 0]
        else:
            row[2][0] = node[inode_index[0], 0]
        if row[2][n - 1] == node[jnode_index[n - 1], 0]:
            row[2][n] = node[inode_index[n - 1], 0]
        else:
            row[2][n] = node[jnode_index[n - 1], 0]
        if reverse.get(i, 0) == -1:
            row[1] = row[1][::-1]
            row[2] = row[2][::-1]
        row[0] = np.array(row[0], dtype=float)
        row[1] = np.array(row[1], dtype=float)
        row[2] = np.array(row[2], dtype=float)
    return member


# ---------------------------------------------------------------------------
# mgtopen_nodeload.m
# ---------------------------------------------------------------------------

def mgtopen_nodeload(filename, constraint):
    """mgtファイルより節点荷重情報を取得する．

    引数 constraint: mgtopen_constraint の戻り値
      (支点に作用している節点荷重は削除される)

    戻り値: (node_load, load_name)
      node_load: ndarray (N x 8)
        列: [荷重ケース連番, 節点番号, FX, FY, FZ, MX, MY, MZ]
      load_name: 常に空list (MATLAB版も抽出前に[]で初期化したまま
        設定されないため。元コードの挙動を踏襲)

    Python版の相違点:
      'Nodal Loads' セクションを持たない荷重ケースのみの場合、MATLAB版は
      空配列へのインデックスアクセスでエラーとなるが、Python版は空の
      node_load を返す。node_loadが空の場合の支点荷重削除もスキップする。
    """
    lines = _lines(filename)
    startnodeload = []
    endnodeload = []
    loadname = []

    pos = 0
    inodeload = 1
    while True:
        tline = lines[pos]
        pos += 1
        inodeload += 1
        found_use_stld = '*USE-STLD' in tline
        found_end = '*ENDDATA' in tline
        if found_use_stld:
            loadname.append(inodeload - 1)
            while True:
                tline = lines[pos]
                pos += 1
                if 'Nodal Loads' in tline:
                    startnodeload.append(inodeload + 2)
                if 'End of data for load case' in tline:
                    endnodeload.append(inodeload - 2)
                    break
                inodeload += 1
            inodeload += 1
        if found_end:
            break

    node_load = []
    load_name = []
    i_count = 1

    if endnodeload:
        for i in range(1, endnodeload[-1] + 1):
            tline = lines[i - 1]
            in_range = (i_count <= len(startnodeload)
                        and i_count <= len(endnodeload)
                        and i >= startnodeload[i_count - 1]
                        and i <= endnodeload[i_count - 1])
            if in_range:
                tn = [0] + [p + 1 for p, c in enumerate(tline) if c == ',']
                nodeload_one = []
                for k in range(1, 8):
                    ttmp1 = _str2num(tline[tn[k - 1]: tn[k] - 1])
                    if ttmp1 is not None:
                        nodeload_one.extend(ttmp1)
                node_load.append([float(i_count)] + nodeload_one)
            if (i_count <= len(endnodeload)
                    and i == endnodeload[i_count - 1]):
                i_count += 1

    node_load = _to_matrix(node_load)

    # 支点に作用している節点荷重は削除
    if node_load.size > 0:
        constraint = np.asarray(constraint, dtype=float)
        for i in range(constraint.shape[0]):
            pick_id = find_zero_index(node_load[:, 1] - constraint[i, 0])
            node_load = np.delete(node_load, pick_id, axis=0)

    return node_load, load_name


# ---------------------------------------------------------------------------
# mgtopen_RCbeam.m
# ---------------------------------------------------------------------------

def mgtopen_RCbeam(filename):
    """mgtファイルよりRC梁配筋情報(*REBAR-BEAM)を取得する．

    RCbeamsはセルデータ(list)とし，二列目に各断面のi端・中央・j端の
    配筋情報が入力される。
    配筋情報は1-5列目が上端筋，6-10列目が下端筋の情報で，
    11-13列目にあばら筋情報となる。
    上端下端筋については，段数，一段目，二段目の本数，1段目の径D,2段目の径D
    あばら筋については，径D，ピッチ，本数となる。

    戻り値 RCbeams: list (N x 2 のcell相当)。各要素は [sec_no, mat]
      sec_no: 断面番号 (float)
      mat: ndarray (3 x 17)  行 = [i端, 中央, j端]
        列: [上端段数, 上1段目本数, 上2段目本数, 0, 上1段目径D, 上2段目径D, 0,
             下端段数, 下1段目本数, 下2段目本数, 0, 下1段目径D, 下2段目径D, 0,
             HOOP径, ピッチ*1000, あばら本数]
    *REBAR-BEAMが無い場合(先に*ENDDATA)は空list。
    """
    lines = _lines(filename)
    startRCBEAM, endRCBEAM = _scan_section(lines, '*REBAR-BEAM', 5,
                                           guard='*ENDDATA', guard_first=True)
    if startRCBEAM == -1:
        return []

    NO_RCBEAM = (endRCBEAM + 1 - startRCBEAM) / 4
    if round(NO_RCBEAM) != NO_RCBEAM:
        print('ERROR:梁配筋情報読取')
    RCbeams = []
    pos = startRCBEAM - 1
    for _ in range(int(np.floor(NO_RCBEAM))):
        tline = lines[pos]  # 一行目（断面番号とHOOP径）
        pos += 1
        tn = _tlinenum(tline)
        sec_no = _str2double(_field(tline, tn, 1))
        # MATLAB: tline(tlinenum(3)+3 : tlinenum(4)-1)
        HOOP = _str2double(_slice(tline, tn[2] + 3, tn[3] - 1))

        steelbar = np.zeros((3, 12))
        for j in range(2, 5):  # 2-4行目（i端・中央・j端配筋）
            tline = lines[pos]
            pos += 1
            tn = _tlinenum(tline)
            for jj in range(1, 4):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))
            steelbar[j - 2, 3] = _str2double(_slice(tline, tn[3] + 3, tn[4] - 1))
            steelbar[j - 2, 4] = _str2double(_slice(tline, tn[4] + 3, tn[5] - 1))
            for jj in range(6, 9):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))
            steelbar[j - 2, 8] = _str2double(_slice(tline, tn[8] + 3, tn[9] - 1))
            steelbar[j - 2, 9] = _str2double(_slice(tline, tn[9] + 3, tn[10] - 1))
            for jj in range(11, 13):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))

        for j in range(3):
            if steelbar[j, 1] * steelbar[j, 2] > 0:
                steelbar[j, 0] = 2
            else:
                steelbar[j, 0] = 1
            if steelbar[j, 6] * steelbar[j, 7] > 0:
                steelbar[j, 5] = 2
            else:
                steelbar[j, 5] = 1

        zeros3 = np.zeros((3, 1))
        hoop3 = np.full((3, 1), HOOP)
        mat = np.hstack([steelbar[:, 0:3], zeros3, steelbar[:, 3:5], zeros3,
                         steelbar[:, 5:8], zeros3, steelbar[:, 8:10], zeros3,
                         hoop3, steelbar[:, 10:11] * 1000, steelbar[:, 11:12]])
        RCbeams.append([sec_no, mat])
    return RCbeams


# ---------------------------------------------------------------------------
# mgtopen_RCcolumn.m
# ---------------------------------------------------------------------------

def mgtopen_RCcolumn(filename):
    """mgtファイルよりRC柱配筋情報(*REBAR-COLUMN)を取得する．

    戻り値 RCcolumns: ndarray (N x 10)
      1列目: 断面番号
      2列目: 配筋タイプ (常に1: Midasで入力可能なのは等間隔配置のtype1)
      3列目: 鉄筋総本数
      4列目: 主筋径
      5列目: 列本数
      6列目: 幅本数 (総本数/2+2-列本数)
      7列目: HOOP径
      8列目: HOOPピッチ (*1000)
      9列目: Fy方向せん断補強筋本数
     10列目: Fz方向せん断補強筋本数
    *REBAR-COLUMNが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startRC, endRC = _scan_section(lines, '*REBAR-COLUMN', 2,
                                   guard='*ENDDATA', guard_first=True)
    if startRC == -1:
        return np.array([])

    NO_RC = endRC + 1 - startRC
    RCcolumns = np.zeros((NO_RC, 8))
    RCcolumns[:, 1] = 1  # Midasで入力可能な配筋タイプは等間隔配置のtype1
    for icolumn in range(1, NO_RC + 1):
        tline = lines[startRC - 1 + (icolumn - 1)]
        tn = _tlinenum(tline)
        if len(tn) > 3:
            RCcolumns = _grow_assign(RCcolumns, icolumn, 1,
                                     _str2double(_field(tline, tn, 1)))  # 断面番号
            RCcolumns = _grow_assign(RCcolumns, icolumn, 4,
                                     _str2double(_slice(tline, tn[3] + 3, tn[4] - 1)))  # 主筋径
            RCcolumns = _grow_assign(RCcolumns, icolumn, 3,
                                     _str2double(_field(tline, tn, 7)))  # 鉄筋総本数
            RCcolumns = _grow_assign(RCcolumns, icolumn, 5,
                                     _str2double(_field(tline, tn, 8)))  # 列本数
            RCcolumns = _grow_assign(
                RCcolumns, icolumn, 6,
                RCcolumns[icolumn - 1, 2] / 2 + 2 - RCcolumns[icolumn - 1, 4])  # 幅本数
            RCcolumns = _grow_assign(RCcolumns, icolumn, 7,
                                     _str2double(_slice(tline, tn[9] + 3, tn[10] - 1)))  # HOOP径
            RCcolumns = _grow_assign(RCcolumns, icolumn, 8,
                                     _str2double(_field(tline, tn, 11)) * 1000)  # HOOPピッチ
            RCcolumns = _grow_assign(RCcolumns, icolumn, 9,
                                     _str2double(_field(tline, tn, 12)))  # Fy方向本数
            RCcolumns = _grow_assign(RCcolumns, icolumn, 10,
                                     _str2double(_field(tline, tn, 13)))  # Fz方向本数
        else:
            RCcolumns = np.delete(RCcolumns, icolumn - 1, axis=0)
            break
    return RCcolumns


# ---------------------------------------------------------------------------
# mgtopen_SRCbeam.m
# ---------------------------------------------------------------------------

def mgtopen_SRCbeam(filename):
    """mgtファイルよりSRC梁配筋情報(*SRC-BEAM)を取得する．

    戻り値 SRCbeams: list (N x 2 のcell相当)。各要素は [sec_no, mat]
      sec_no: 断面番号 (float)
      mat: ndarray (3 x 11)  行 = [i端, 中央, j端]
        列: [上端段数, 上1段目本数, 上2段目本数, 主筋径D,
             下端段数, 下1段目本数, 下2段目本数, 下主筋径D,
             HOOP径, ピッチ*1000, あばら本数]
    *SRC-BEAMが無い場合(先に*ENDDATA)は空list。
    """
    lines = _lines(filename)
    startSRC, endSRC = _scan_section(lines, '*SRC-BEAM', 5,
                                     guard='*ENDDATA', guard_first=True)
    if startSRC == -1:
        return []

    NO_SRC = (endSRC + 1 - startSRC) / 4
    if round(NO_SRC) != NO_SRC:
        print('ERROR:梁配筋情報読取')
    SRCbeams = []
    pos = startSRC - 1
    for _ in range(int(np.floor(NO_SRC))):
        tline = lines[pos]  # 一行目（断面番号とHOOP径）
        pos += 1
        tn = _tlinenum(tline)
        sec_no = _str2double(_field(tline, tn, 1))
        # MATLAB: tline(tlinenum(2)+3 : tlinenum(3)-1)
        HOOP = _str2double(_slice(tline, tn[1] + 3, tn[2] - 1))

        steelbar = np.zeros((3, 10))
        for j in range(2, 5):  # 2-4行目
            tline = lines[pos]
            pos += 1
            tn = _tlinenum(tline)
            for jj in range(1, 4):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))
            steelbar[j - 2, 3] = _str2double(_slice(tline, tn[3] + 3, tn[4] - 1))
            for jj in range(5, 8):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))
            steelbar[j - 2, 7] = _str2double(_slice(tline, tn[7] + 3, tn[8] - 1))
            for jj in range(9, 11):
                steelbar[j - 2, jj - 1] = _str2double(_field(tline, tn, jj))

        for j in range(3):
            if steelbar[j, 1] * steelbar[j, 2] > 0:
                steelbar[j, 0] = 2
            else:
                steelbar[j, 0] = 1
            if steelbar[j, 5] * steelbar[j, 6] > 0:
                steelbar[j, 4] = 2
            else:
                steelbar[j, 4] = 1

        hoop3 = np.full((3, 1), HOOP)
        mat = np.hstack([steelbar[:, 0:8], hoop3,
                         steelbar[:, 8:9] * 1000, steelbar[:, 9:10]])
        SRCbeams.append([sec_no, mat])
    return SRCbeams


# ---------------------------------------------------------------------------
# mgtopen_SRCcolumn.m
# ---------------------------------------------------------------------------

def mgtopen_SRCcolumn(filename):
    """mgtファイルよりSRC柱配筋情報(*SRC-COLUMN)を取得する．

    戻り値 SRCcolumns: ndarray (N x 7)
      1列目: 断面番号
      2列目: 配筋タイプ (常に1)
      3列目: 主筋径
      4列目: 鉄筋総本数
      5列目: 列本数(柱幅方向の鉄筋数)
      6列目: HOOP径
      7列目: HOOPピッチ (*1000)
    *SRC-COLUMNが無い場合(先に*ENDDATA)は空配列。
    """
    lines = _lines(filename)
    startSRC, endSRC = _scan_section(lines, '*SRC-COLUMN', 2,
                                     guard='*ENDDATA', guard_first=True)
    if startSRC == -1:
        return np.array([])

    NO_SRC = endSRC + 1 - startSRC
    SRCcolumns = np.zeros((NO_SRC, 7))
    SRCcolumns[:, 1] = 1  # Midasで入力可能な配筋タイプは等間隔配置のtype1
    for icolumn in range(1, NO_SRC + 1):
        tline = lines[startSRC - 1 + (icolumn - 1)]
        tn = _tlinenum(tline)
        if len(tn) > 3:
            SRCcolumns[icolumn - 1, 0] = _str2double(_field(tline, tn, 1))  # 断面番号
            SRCcolumns[icolumn - 1, 2] = _str2double(
                _slice(tline, tn[3] + 3, tn[4] - 1))  # 主筋径
            SRCcolumns[icolumn - 1, 3] = _str2double(_field(tline, tn, 5))  # 鉄筋総本数
            SRCcolumns[icolumn - 1, 4] = _str2double(_field(tline, tn, 6))  # 列本数
            SRCcolumns[icolumn - 1, 5] = _str2double(
                _slice(tline, tn[7] + 3, tn[8] - 1))  # HOOP径
            SRCcolumns[icolumn - 1, 6] = _str2double(
                _field(tline, tn, 9)) * 1000  # HOOPピッチ
        else:
            SRCcolumns = np.delete(SRCcolumns, icolumn - 1, axis=0)
            break
    return SRCcolumns


# ---------------------------------------------------------------------------
# wall/wall_unit.m (mgtopen_wallが使用)
# ---------------------------------------------------------------------------

def wall_unit(pick, wall_base, node):
    """壁IDが同じ要素を結合する.

    引数:
      pick: 結合対象の壁要素番号(1次元配列)
      wall_base: mgtopen_wall内で作成される壁要素基本情報 (N x 10)
      node: mgtopen_node の戻り値

    戻り値 wall_element: ndarray (1 x 10)
      列: [ベースレベル, 壁ID, N1, N2, N3, N4, 材料番号, 厚さ番号,
           方向ベクトルx, 方向ベクトルy]
    """
    pick = np.atleast_1d(np.asarray(pick, dtype=float))
    wall_index = np.atleast_1d(find_index(wall_base[:, 0], pick))

    # 応力表示のための方向を決める最小IDの要素
    direction_index = find_index(wall_base[:, 0], np.min(pick))
    vec1to2_node = wall_base[direction_index, 3:5]
    vi1 = find_index(node[:, 0], vec1to2_node[0])
    vi2 = find_index(node[:, 0], vec1to2_node[1])
    vec1to2 = node[vi2, 1:3] - node[vi1, 1:3]
    vec1to2 = vec1to2 / np.linalg.norm(vec1to2)

    if pick.size > 1:
        # 全て同じ材料であるかをチェック
        if np.any(wall_base[wall_index, 1] - wall_base[wall_index[0], 1]):
            print('ERROR:同じ壁IDで結合された要素の材料不一致')
            print(pick)
            raise RuntimeError('壁材料不一致')  # MATLAB: stop
        # 全て同じ断面サイズであるかをチェック
        if np.any(wall_base[wall_index, 2] - wall_base[wall_index[0], 2]):
            print('ERROR:同じ壁IDで結合された要素の厚さ定義不一致')
            print(pick)
            raise RuntimeError('壁厚さ不一致')  # MATLAB: stop
        # 全て同じ高さの壁（上レベル）であるかをチェック
        high_node = wall_base[wall_index, 5]
        high_node_ID = np.atleast_1d(find_index(node[:, 0], high_node))
        high_level_check = node[high_node_ID, 3]
        if np.std(high_level_check, ddof=1) > 0.01:
            print('ERROR:同じ壁IDで結合された要素の壁高さ不一致')
            print(pick)
            raise RuntimeError('壁高さ不一致')  # MATLAB: stop
        # 全て同じ方向の壁であるかをチェック
        low_node1 = wall_base[wall_index, 3]
        low_node2 = wall_base[wall_index, 4]
        li1 = np.atleast_1d(find_index(node[:, 0], low_node1))
        li2 = np.atleast_1d(find_index(node[:, 0], low_node2))
        low_vec = np.column_stack([node[li2, 1] - node[li1, 1],
                                   node[li2, 2] - node[li1, 2]])
        while low_vec.shape[0] >= 2:
            for irow in range(low_vec.shape[0]):
                A = low_vec[0, :]
                B = low_vec[irow, :]
                if abs(np.dot(A, B)) / (np.linalg.norm(A) * np.linalg.norm(B)) > 0.98:
                    pass
                else:
                    print('ERROR:同じ壁IDで結合された壁の方向不一致')
                    print(pick)
                    raise RuntimeError('壁方向不一致')  # MATLAB: stop
            low_vec = low_vec[1:, :]

        node1 = wall_base[wall_index, 3]
        node2 = wall_base[wall_index, 4]
        node3 = wall_base[wall_index, 5]
        node4 = wall_base[wall_index, 6]

        def _uniq_ends(na, base):
            """base内に1回しか現れない na の要素を返す
            (MATLAB: size(find(base-na(i)),1)==size(base,1)-1)."""
            return np.asarray(
                [v for v in na
                 if np.count_nonzero(base - v) == base.size - 1],
                dtype=float)

        both12 = np.concatenate([node1, node2])
        both34 = np.concatenate([node3, node4])
        N1 = _uniq_ends(node1, both12)
        N2 = _uniq_ends(node2, both12)
        N3 = _uniq_ends(node3, both34)
        N4 = _uniq_ends(node4, both34)

        if (max(N1.size, 1) + max(N2.size, 1) == 2
                and max(N3.size, 1) + max(N4.size, 1) == 2):
            if min([N1.size, 1, N2.size, 1, N3.size, 1, N4.size, 1]) == 1:
                w0 = wall_index[0]
                wall_element = np.concatenate([
                    [wall_base[w0, 9], wall_base[w0, 8]],
                    N1, N2, N3, N4,
                    [wall_base[w0, 1], wall_base[w0, 2]], vec1to2])
            else:
                N1 = np.concatenate([N1, N2])
                N3 = np.concatenate([N3, N4])
                N1_index = np.atleast_1d(find_index(node[:, 0], N1))
                N3_index = np.atleast_1d(find_index(node[:, 0], N3))
                if (np.linalg.norm(node[N1_index[0], 1:3] - node[N3_index[0], 1:3]) <= 0.01
                        and np.linalg.norm(node[N1_index[1], 1:3] - node[N3_index[1], 1:3]) <= 0.01):
                    N11, N22, N33, N44 = N1[0], N1[1], N3[1], N3[0]
                elif (np.linalg.norm(node[N1_index[0], 1:3] - node[N3_index[1], 1:3]) <= 0.01
                        and np.linalg.norm(node[N1_index[1], 1:3] - node[N3_index[0], 1:3]) <= 0.01):
                    N11, N22, N33, N44 = N1[0], N1[1], N3[0], N3[1]
                else:
                    print('ERROR:同じ壁IDで結合された壁要素の形状エラー')
                    raise RuntimeError('壁形状エラー')  # MATLAB: stop
                w0 = wall_index[0]
                wall_element = np.concatenate([
                    [wall_base[w0, 9], wall_base[w0, 8]],
                    [N11, N22, N33, N44],
                    [wall_base[w0, 1], wall_base[w0, 2]], vec1to2])
        else:
            print('ERROR:同じ壁IDで結合された壁要素の形状エラー')
            raise RuntimeError('壁形状エラー')  # MATLAB: stop
    else:
        w0 = wall_index[0]
        wall_element = np.concatenate([
            [wall_base[w0, 9], wall_base[w0, 8]],
            wall_base[w0, 3:7],
            [wall_base[w0, 1], wall_base[w0, 2]], vec1to2])
    return wall_element.reshape(1, -1)


# ---------------------------------------------------------------------------
# wall/mgtopen_wall.m
# ---------------------------------------------------------------------------

def mgtopen_wall(filename, node):
    """mgtファイルより壁要素情報を取得する．

    引数 node: mgtopen_node の戻り値

    戻り値: (wall_element, wall_base)
      wall_element: ndarray (N x 10)  壁ID・ベースレベルごとに結合済み
        列: [ベースレベル, 壁ID, N1, N2, N3, N4, 材料番号, 厚さ番号,
             方向ベクトルx, 方向ベクトルy]
      wall_base: ndarray (M x 10)  要素単位の生データ
        列: [要素番号, 材料番号, 厚さ番号, 節点1, 節点2, 節点3, 節点4,
             iSUB, 壁ID, ベースレベル]
    'WALL 'を含む行が*GROUPより前に無い場合はいずれも空配列。
    """
    lines = _lines(filename)
    wall_base = []
    pos = 0
    while True:
        tline = lines[pos]
        pos += 1
        if 'WALL ' in tline:  # 壁要素情報の入力位置
            tn = _tlinenum(tline)
            wall_base_one = []
            for ii in range(1, 11):  # 10個目のフィールドまで(壁IDまで)を取得
                ttmp = _str2num(_field(tline, tn, ii))
                if ttmp is not None:
                    wall_base_one.extend(ttmp)
            wall_base.append(wall_base_one)
        elif '*GROUP' in tline:
            break
        # 場合によってはMATERIALの前にGROUPがくるけど、"WALL"の文字が
        # なければ拾われないのでOK

    wall_base = _to_matrix(wall_base)
    wall_element = np.array([])

    if wall_base.size > 0:
        # 各壁要素のベースレベルを求め、wall_baseに10列目として追記する
        wall_num = wall_base.shape[0]
        wall_base = np.hstack([wall_base, np.zeros((wall_num, 1))])
        for i in range(wall_num):
            node1_index = find_index(node[:, 0], wall_base[i, 3])
            wall_level = node[node1_index, 3]
            wall_base[i, 9] = wall_level

        # ベースレベルと壁IDの同じ要素を取り出して結合する
        wall_base2 = wall_base.copy()
        rows = []
        count = 2
        while count > 0:
            pick = [wall_base2[0, 0]]
            pick_i = [0]
            for i in range(1, wall_base2.shape[0]):
                if (wall_base2[i, 9] == wall_base2[0, 9]
                        and wall_base2[i, 8] == wall_base2[0, 8]):
                    pick.append(wall_base2[i, 0])
                    pick_i.append(i)
            wall_base2 = np.delete(wall_base2, pick_i, axis=0)
            rows.append(wall_unit(np.asarray(pick), wall_base, node))
            count = wall_base2.shape[0]
        wall_element = np.vstack(rows)

    return wall_element, wall_base


# ---------------------------------------------------------------------------
# wall/mgtopen_RCwall.m
# ---------------------------------------------------------------------------

def mgtopen_RCwall(filename, wall_element):
    """mgtファイルよりRC壁配筋情報(*REBAR-WALL)を取得する．

    引数 wall_element: mgtopen_wall の戻り値1つ目

    戻り値: (RCwalls, FL_name)
      RCwalls: ndarray (N x 9)
        1列目: 壁の高さ位置 (階名から変換したフロア高さ、または
                wall_elementのベースレベル)
        2列目: 壁ID
        3列目: 縦筋の径 / 4列目: 縦筋のピッチ(*1000)
        5列目: 横筋の径 / 6列目: 横筋のピッチ(*1000)
        7列目: 補強筋径 / 8列目: 補強筋間隔(*1000) / 9列目: 補強筋本数
      FL_name: mgtopen_FLと同じ list [[階名, 高さ], ...]
    *REBAR-WALLが無い場合はRCwallsは空配列。*STORYが無い場合はFL_nameは空list。
    """
    lines = _lines(filename)
    RCwall_position = np.array([])
    startRCwall, endRCwall = _scan_section(lines, '*REBAR-WALL', 3,
                                           guard='*ENDDATA', guard_first=True)

    NO_RCwall = None
    RCwalls = np.array([])
    RCwall_position_name = []
    if startRCwall != -1:
        NO_RCwall = endRCwall + 1 - startRCwall
        RCwalls = np.zeros((NO_RCwall, 9))  # MATLABはzeros(N,8)から9列目へ自動拡張
        RCwall_position_name = [None] * NO_RCwall
        RCwall_position = np.zeros(NO_RCwall)
        for iwall in range(1, NO_RCwall + 1):
            tline = lines[startRCwall - 1 + (iwall - 1)]
            tn = _tlinenum(tline)
            RCwalls[iwall - 1, 1] = _str2double(_field(tline, tn, 1))  # 壁ID
            RCwalls[iwall - 1, 2] = _str2double(
                _slice(tline, tn[4] + 3, tn[5] - 1))  # 縦筋の径
            RCwalls[iwall - 1, 3] = _str2double(
                _field(tline, tn, 6)) * 1000  # 縦筋のピッチ
            RCwalls[iwall - 1, 4] = _str2double(
                _slice(tline, tn[9] + 3, tn[10] - 1))  # 横筋の径
            RCwalls[iwall - 1, 5] = _str2double(
                _field(tline, tn, 11)) * 1000  # 横筋のピッチ

            if 'D' in _field(tline, tn, 7):
                RCwalls[iwall - 1, 6] = _str2double(
                    _slice(tline, tn[6] + 3, tn[7] - 1))  # 補強筋径
                RCwalls[iwall - 1, 7] = _str2double(
                    _field(tline, tn, 8)) * 1000  # 補強筋間隔
                RCwalls[iwall - 1, 8] = _str2double(
                    _field(tline, tn, 9))  # 補強筋本数
            else:
                RCwalls[iwall - 1, 6:9] = 0

            RCwall_position_name[iwall - 1] = space_erace(
                _field(tline, tn, 2))  # 階名

    # 階情報 (*STORY)
    startfloor, endfloor = _scan_section(lines, '*STORY', 3,
                                         guard='*ENDDATA', guard_first=True)
    if startfloor == -1:
        FL_name = []
    else:
        NO_floor = endfloor + 1 - startfloor
        FL_name = []
        for ifloor in range(1, NO_floor + 1):
            tline = lines[startfloor - 1 + (ifloor - 1)]
            tn = _tlinenum(tline)
            FL_name.append([space_erace(_field(tline, tn, 1)),
                            _str2double(_field(tline, tn, 2))])

    # 階数名をフロア高さに変換
    if NO_RCwall is not None:
        for i in range(NO_RCwall):
            RCwall_position_name[i] = space_erace(RCwall_position_name[i])
            if RCwall_position_name[i] == '':
                idx = find_index(wall_element[:, 1], RCwalls[i, 1])
                RCwall_position[i] = wall_element[idx, 0]
            else:
                j = 0
                while True:
                    if len(RCwall_position_name[i]) == len(FL_name[j][0]):
                        if RCwall_position_name[i] == FL_name[j][0]:
                            RCwall_position[i] = FL_name[j][1]
                            break
                    j += 1

    if RCwall_position.size > 0:
        RCwalls[:, 0] = RCwall_position

    return RCwalls, FL_name
