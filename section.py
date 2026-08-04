# -*- coding: utf-8 -*-
"""mgtopen_section.m (747行) の逐語移植.

MIDAS/Gen mgtファイルから断面情報を取得する。
元コード: msrc/privatetool_function/MIDAS/mgt/mgtopen_section.m
(サブ関数 pick_HBtwtf を含む)

インターフェイス:
    mgtopen_section(filename) -> (sections, section_no, section_name)

sections はMATLABのcell相当の list 長17 (index 0 は未使用、1..16 を使用)。
各要素は None (該当断面なし) または np.ndarray (float)。

各断面種の列構成 (1列目は常に断面番号 iSEC):
   1: H鋼         'DBUSER' ', H  ,'   8列 [secno, H, B, tw, tf1, B2, tf2, r1]
                  (JIS DB名復元時は [secno, H, B, tw, tf, 0, 0, 0])
   2: 中実角 SB   'DBUSER' ', SB ,'   3列 [secno, H, B]
   3: 中実丸 SR   'DBUSER' ', SR ,'   2列 [secno, D]
   4: 鋼管 P      'DBUSER' ', P  ,'   3列 [secno, D, t]
   5: 溝形 C      'DBUSER' ', C  ,'   5列 [secno, H, B, tw, tf]
   6: H+カバーPL  'COMBINED' 'HP'     7列 [secno, v3..v8]
                  (v=1行目の正値列。元コード列選択 [1 3:6 7:8])
   7: 角形鋼管BOX 'DBUSER' ', B  ,'/', CB ,'
                                      6列 [secno, H, B, tw, tf, 0] (r=0固定)
   8: CT          'DBUSER' ', T  ,'   5列 [secno, H, B, tw, tf]
   9: L           'DBUSER' ', L  ,'   5列 [secno, H, B, tw, tf]
  10: SRC RHB     'SRC' 'RHB'         9列 [secno, v1, v2, v4..v9]
                  (v=2行目の正値列。7個しか無い場合は末尾 0,0 を付加、
                   v8==v5 かつ v9==v7 のとき v8=v9=0)
  11: テーパーH   'TAPERED' ', H  ,' 15列 [secno, v1..v7, v9..v15] (v=2行目, 0許容)
  12: テーパーSB  'TAPERED' ', SB ,'  可変 [secno, v...] (v=2行目の正値列)
  13: SRC CPO     'SRC' 'CPO'         4列 [secno, v1, v3, v4]
  14: SRC CHB     'SRC' 'CHB'         8列 [secno, v1, v3..v8] (6個の場合0,0付加)
  15: SRC RH2T    'SRC' 'RH2T'       11列 [secno, v1, v2, v4..v11]
  16: CFT         'SRC' 'EPC'         3列 [secno, v3, v4]
      CFT         'SRC' 'EBC'         5列 [secno, v4..v7]

注意 (MATLAB版との対応):
- MATLAB版と同じく EPC を sections[16]、EBC を sections[17] に分けて返す
  (リスト長18、index 0 は未使用)。sectionget の section_index1 がそのまま
  MATLAB のテーブル番号となり、検定側の×1000 で 16000(CFT円形)/
  17000(CFT角形) の断面形式コードになる。
- MATLAB版は '*SECT-COLOR' 行で必ず break する前提 (無いと無限ループ/エラー)。
  本移植は安全のため EOF でも break する。
- str2double が NaN を返す非数値フィールドは MATLAB 同様スキップされる
  (NaN>=0, NaN>0 はいずれも False)。
- 断面名は space_erace で半角スペースのみ除去 (全角スペースは残る)。
  MATLABの strvcat は空文字列を無視するため、空名は section_name に
  追加されない (section_no と長さがずれ得るのも元コード同様)。

拡張 (MATLAB原典に無い機能):
- 現行mgtフォーマットの DBデータベース名指定断面 (iCEL=1、例
  '401, DBUSER, nC1_○-60.5x4, CC, 0,0,0,0,0,0, YES, NO, P  , 1, JIS, PG 60.5x4')
  を検出し、行末のDB名から寸法を復元して数値入力 (iCEL=2) と同じ列構成にする。
  対応断面種: H / SB / SR / P / C / BOX(B,CB) / T(CT) / L。
  復元した断面は mgtopen_section.derived_sections (dictのlist、キー
  secno/name/shape/db_name/dims) で参照できる (呼び出しごとにリセット)。
  寸法が復元できないDB名の場合は日本語メッセージの ValueError を送出する。
"""

import math

import numpy as np

try:
    from .util import space_erace  # 並行作業中の util.py (同挙動)
except ImportError:  # util.py 未整備時のフォールバック (space_erace.m の逐語移植)
    def space_erace(string):
        """space_erace.m: 文字列から半角スペース(0x20)を全て除去する。"""
        a = 0.5
        while a < 1:
            if ' ' not in string:
                a = 1
            else:
                string = string.replace(' ', '')
                a = 0
        return string


def _str2double(s):
    """MATLAB str2double 相当: 変換不能なら NaN を返す。"""
    try:
        return float(s)
    except (TypeError, ValueError):
        return math.nan


def _comma_bounds(tline):
    """MATLAB: tlinenum = [0 findstr(tline,',') length(tline)+1] (1始まり位置)。"""
    pos = [i + 1 for i, ch in enumerate(tline) if ch == ',']
    return [0] + pos + [len(tline) + 1]


def _field(tline, tn, i):
    """MATLAB: tline((tlinenum(i)+1):(tlinenum(i+1)-1)) (i は1始まり)。"""
    return tline[tn[i - 1]:tn[i] - 1]


def pick_HBtwtf(HBtwtf, JIS_2K):
    """サブ関数 pick_HBtwtf の逐語移植.

    JISデータベース断面名 (例 'H-200x100x5.5x8' / 'H 200x100x5.5/8') から
    寸法値を復元し、[値... 0 0 0 0 0 0]/1000 を返す (mm -> m)。
    """
    section_one = []
    if HBtwtf is not None and JIS_2K == 'JIS2K':  # strcmp(JIS_2K,'JIS2K')
        t1 = [i + 1 for i, c in enumerate(HBtwtf) if c == '-']
        t2 = [i + 1 for i, c in enumerate(HBtwtf) if c == 'x']
        tn = [0] + t1 + t2 + [len(HBtwtf) + 1]
    else:
        t1 = [i + 1 for i, c in enumerate(HBtwtf) if c == ' ']
        t2 = [i + 1 for i, c in enumerate(HBtwtf) if c == 'x']
        t3 = [i + 1 for i, c in enumerate(HBtwtf) if c == '/']
        tn = [0] + t1 + t2 + t3 + [len(HBtwtf) + 1]

    for isection in range(2, len(tn)):  # MATLAB: for isection=2:(length(tn)-1)
        seg = HBtwtf[tn[isection - 1]:tn[isection] - 1]
        ttmp = _str2double(seg)
        if ttmp > 0:
            section_one.append(ttmp)
    return [v / 1000.0 for v in (section_one + [0, 0, 0, 0, 0, 0])]


# --- 現行mgtフォーマットの DBデータベース名指定 (iCEL=1) 対応 --------------
# 旧フォーマット (MATLAB原典が想定) と異なり bSD/bWE が第11・12フィールドに
# 入るため、第13フィールドは形状記号 (非数値)、第14フィールドが
# 1(DB名指定)/2(数値入力) となる。DB名指定行は第15=DB名称(JIS等)、
# 第16=断面DB名 (例 'PG 60.5x4', 'H 125x125x6.5/9') の16フィールド構成。

#: 形状記号 -> (復元寸法個数, 3個をtw=tfとみなし4個に展開可か, 復元行の全長)
#: 復元行の全長は同一ファイル内の数値入力(iCEL=2)行と vertcat できる長さ。
_DB_SHAPE_SPEC = {
    'H': (4, False, 12),   # [secno, 1, H, B, tw, tf, 0,0,0,0,0,0] (r=0)
    'SB': (2, False, 4),   # [secno, 1, H, B]
    'SR': (1, False, 3),   # [secno, 1, D]
    'P': (2, False, 4),    # [secno, 1, D, t]
    'C': (4, True, 6),     # [secno, 1, H, B, tw, tf]
    'B': (4, True, 6),     # 角形鋼管 (r=0列は後段の列選別で付加)
    'T': (4, True, 6),     # CT
    'L': (4, True, 6),
}


def _pick_db_name_dims(db_name):
    """DBデータベース名 (例 'PG 60.5x4') から寸法値 [mm -> m] を復元する。

    区切りの扱いは pick_HBtwtf と同じ ('-', 'x', ' ', '/' 分割、/1000)。
    先頭トークン (形状記号 PG/H/SR/SB/B/T/C/L 等) は捨てる。
    """
    t = [i + 1 for i, c in enumerate(db_name) if c in '-x /']
    tn = [0] + sorted(t) + [len(db_name) + 1]
    dims = []
    for isection in range(2, len(tn)):  # 先頭の形状記号トークンは捨てる
        ttmp = _str2double(db_name[tn[isection - 1]:tn[isection] - 1])
        if ttmp > 0:
            dims.append(ttmp / 1000.0)
    return dims


#: JIS G 3192 圧延H形鋼のフィレット(隅角部)半径 r[mm]。
#: キー=(H,B,tw,tf)[mm](標準断面寸法=DB名の実寸)。出典: JIS G 3192 断面性能表。
#: iCEL=1(DB名指定)のH断面は寸法数値がmgtに無いため、DB名から H,B,tw,tf を復元し、
#: この表の r を付与して BH に渡すことで、User(数値入力)と同じフィレット込みの
#: 正確な断面性能を得る。BH算定値が公表JIS(A/Ix/Iy/Zx/Zy)と一致することを確認済。
JIS_H_FILLET = {
    (100.0, 50.0, 5.0, 7.0): 8, (100.0, 100.0, 6.0, 8.0): 10,
    (125.0, 60.0, 6.0, 8.0): 9, (125.0, 125.0, 6.5, 9.0): 10,
    (150.0, 75.0, 5.0, 7.0): 8, (148.0, 100.0, 6.0, 9.0): 11,
    (150.0, 150.0, 7.0, 10.0): 11, (175.0, 90.0, 5.0, 8.0): 9,
    (175.0, 175.0, 7.5, 11.0): 12, (198.0, 99.0, 4.5, 7.0): 11,
    (200.0, 100.0, 5.5, 8.0): 11, (194.0, 150.0, 6.0, 9.0): 13,
    (200.0, 200.0, 8.0, 12.0): 13, (200.0, 204.0, 12.0, 12.0): 13,
    (248.0, 124.0, 5.0, 8.0): 12, (250.0, 125.0, 6.0, 9.0): 12,
    (244.0, 175.0, 7.0, 11.0): 16, (250.0, 250.0, 9.0, 14.0): 16,
    (250.0, 255.0, 14.0, 14.0): 16, (298.0, 149.0, 5.5, 8.0): 13,
    (300.0, 150.0, 6.5, 9.0): 13, (294.0, 200.0, 8.0, 12.0): 18,
    (294.0, 302.0, 12.0, 12.0): 18, (300.0, 300.0, 10.0, 15.0): 18,
    (300.0, 305.0, 15.0, 15.0): 18, (346.0, 174.0, 6.0, 9.0): 14,
    (350.0, 175.0, 7.0, 11.0): 14, (340.0, 250.0, 9.0, 14.0): 20,
    (344.0, 348.0, 10.0, 16.0): 20, (350.0, 350.0, 12.0, 19.0): 20,
    (396.0, 199.0, 7.0, 11.0): 16, (400.0, 200.0, 8.0, 13.0): 16,
    (390.0, 300.0, 10.0, 16.0): 22, (388.0, 402.0, 15.0, 15.0): 22,
    (394.0, 398.0, 11.0, 18.0): 22, (400.0, 400.0, 13.0, 21.0): 22,
    (400.0, 408.0, 21.0, 21.0): 22, (414.0, 405.0, 18.0, 28.0): 22,
    (428.0, 407.0, 20.0, 35.0): 22, (458.0, 417.0, 30.0, 50.0): 22,
    (498.0, 432.0, 45.0, 70.0): 22, (446.0, 199.0, 8.0, 12.0): 18,
    (450.0, 200.0, 9.0, 14.0): 18, (440.0, 300.0, 11.0, 18.0): 24,
    (496.0, 199.0, 9.0, 14.0): 20, (500.0, 200.0, 10.0, 16.0): 20,
    (506.0, 201.0, 11.0, 19.0): 20, (482.0, 300.0, 11.0, 15.0): 26,
    (488.0, 300.0, 11.0, 18.0): 26, (596.0, 199.0, 10.0, 15.0): 22,
    (600.0, 200.0, 11.0, 17.0): 22, (606.0, 201.0, 12.0, 20.0): 22,
    (582.0, 300.0, 12.0, 17.0): 28, (588.0, 300.0, 12.0, 20.0): 28,
    (594.0, 302.0, 14.0, 23.0): 28, (692.0, 300.0, 13.0, 20.0): 28,
    (700.0, 300.0, 13.0, 24.0): 28, (792.0, 300.0, 14.0, 22.0): 28,
    (800.0, 300.0, 14.0, 26.0): 28, (890.0, 299.0, 15.0, 23.0): 28,
    (900.0, 300.0, 16.0, 28.0): 28, (912.0, 302.0, 18.0, 34.0): 28,
}


def _restore_db_section(tline, tn, section_no, section_name, shape, derived):
    """現行フォーマットのDB名指定行 (iCEL=1) を数値入力相当の行に復元する。

    shape=='H' の標準JIS圧延H形鋼は、DB名から H,B,tw,tf を復元し JIS_H_FILLET の
    フィレット半径 r を付与して数値入力(iCEL=2)と同一列構成の行を返す
    (mgtopen_section.restored_sections に記録、注記のみ・エラーにしない)。
    JIS表に無いH断面・H以外のDB名断面は従来どおり derived に記録し
    (mgtopen_section の最後で)エラーとする。
    """
    sec_no = _str2double(_field(tline, tn, 1))
    section_no.append(sec_no)
    name = space_erace(_field(tline, tn, 3))
    if name != '':  # strvcat は空文字列を無視する
        section_name.append(name)
    db_name = _field(tline, tn, 16).strip()
    n_dim, allow3, row_len = _DB_SHAPE_SPEC[shape]

    if shape == 'H':
        dims = _pick_db_name_dims(db_name)  # [H,B,tw,tf,...] (m)
        if len(dims) >= 4 and all(d > 0 for d in dims[:4]):
            key = (round(dims[0] * 1000, 1), round(dims[1] * 1000, 1),
                   round(dims[2] * 1000, 1), round(dims[3] * 1000, 1))
            r_mm = JIS_H_FILLET.get(key)
            if r_mm is not None:
                # [secno, iCEL, H, B, tw, tf, B2, tf2, r, 0, 0, 0] と同一列構成
                row = ([sec_no, 1.0, dims[0], dims[1], dims[2], dims[3],
                        0.0, 0.0, r_mm / 1000.0]
                       + [0.0] * (row_len - 9))
                rec = {'secno': int(sec_no), 'name': name, 'db_name': db_name,
                       'r': r_mm, 'H': key[0], 'B': key[1], 'tw': key[2],
                       'tf': key[3]}
                getattr(mgtopen_section, 'restored_sections', []).append(rec)
                return row

    # H以外 or JIS表に無いH → 復元不可としてエラー記録
    if derived is not None:
        derived.append({'secno': int(sec_no), 'name': name, 'shape': shape,
                        'db_name': db_name})
    return [sec_no, 1.0] + [0.0] * (row_len - 2)


def _parse_dbuser_line(tline, section_no, section_name, h_branch,
                       shape=None, derived=None):
    """DBUSER系1行断面の共通処理 (H/SB/SR/P/C/HxCP/B/CT/L の各ブロック).

    h_branch=True のとき H鋼ブロック: 値の採用条件が >=0 (0を含む) となり、
    第13フィールド==1 の場合は JIS DB名から pick_HBtwtf で寸法復元して break
    (旧フォーマットのDB名指定)。それ以外のブロックは >0 のみ採用。

    拡張: shape が指定され、現行フォーマットのDB名指定行 (第13フィールドが
    形状記号=非数値、第14フィールド==1、16フィールド以上) を検出した場合は
    _restore_db_section でDB名から寸法を復元した行を返す。
    """
    tn = _comma_bounds(tline)
    if (shape is not None and len(tn) - 1 >= 16
            and math.isnan(_str2double(_field(tline, tn, 13)))
            and _str2double(_field(tline, tn, 14)) == 1):
        # 現行フォーマットの DBデータベース名指定 (iCEL=1)
        return _restore_db_section(tline, tn, section_no, section_name,
                                   shape, derived)
    section_one = []
    for isection in range(1, len(tn)):  # MATLAB: 1:(length(tn)-1)
        if isection == 2 or (isection > 3 and isection < 11):
            pass
        elif isection == 3:
            sec_no = _str2double(_field(tline, tn, 1))
            section_no.append(sec_no)
            ttmp = _field(tline, tn, isection)
            ttmp = space_erace(ttmp)
            if ttmp != '':  # strvcat は空文字列を無視する
                section_name.append(ttmp)
        elif h_branch and isection == 13:
            ttmp = _str2double(_field(tline, tn, isection))
            if ttmp == 1:
                # MATLAB: tline((tlinenum(15)+2):(tlinenum(16)-1)) 等
                HBtwtf = tline[tn[14] + 1:tn[15] - 1]
                JIS_2K = tline[tn[13] + 1:tn[14] - 1]
                HBtwtf = pick_HBtwtf(HBtwtf, JIS_2K)
                section_one = section_one + [1] + HBtwtf
                break
            else:
                ttmp = _str2double(_field(tline, tn, isection))
                if ttmp >= 0:
                    section_one.append(ttmp)
        else:
            ttmp = _str2double(_field(tline, tn, isection))
            if h_branch:
                if ttmp >= 0:
                    section_one.append(ttmp)
            else:
                if ttmp > 0:
                    section_one.append(ttmp)
    return section_one


def _parse_first_line_src(tline, section_no, section_name):
    """SRC/TAPERED系ブロックの1行目: 断面番号と断面名の取得。"""
    tn = _comma_bounds(tline)
    sec_no = _str2double(_field(tline, tn, 1))
    section_no.append(sec_no)
    ttmp = _field(tline, tn, 3)
    ttmp = space_erace(ttmp)
    if ttmp != '':  # strvcat は空文字列を無視する
        section_name.append(ttmp)
    return sec_no


def _parse_second_line(tline, allow_zero):
    """SRC/TAPERED系ブロックの2行目: 数値列の取得 (allow_zero: H_TAPERED のみ True)。"""
    tn = _comma_bounds(tline)
    section_one = []
    for isection in range(1, len(tn)):
        ttmp = _str2double(_field(tline, tn, isection))
        if allow_zero:
            if ttmp >= 0:
                section_one.append(ttmp)
        else:
            if ttmp > 0:
                section_one.append(ttmp)
    return section_one


def _vertcat(rows):
    """MATLAB の [A ; row] 相当。行長が不揃いなら (MATLAB同様) エラー。"""
    if not rows:
        return None
    n = len(rows[0])
    for r in rows:
        if len(r) != n:
            raise ValueError(
                'CAT arguments dimensions are not consistent '
                '(行長不一致: {} vs {})'.format(n, len(r)))
    return np.array(rows, dtype=float)


def _cols(a, matlab_cols):
    """MATLAB の A(:, [c1 c2 ...]) 相当 (1始まり列指定)。"""
    return a[:, [c - 1 for c in matlab_cols]]


def mgtopen_section(filename):
    """mgtファイルより断面情報を取得する (mgtopen_section.m の逐語移植).

    Returns
    -------
    sections : list 長17 (index 0 未使用)。各要素 None または np.ndarray。
    section_no : np.ndarray 全断面番号 (mgt内登場順)。
    section_name : list[str] 断面名 (スペース除去済み)。
    """
    section_name = []
    section_no = []
    derived = []  # JIS表に無く復元不可のDB名断面 (最後にエラー)
    mgtopen_section.derived_sections = derived
    mgtopen_section.restored_sections = []  # JIS表で復元したH断面 (注記)
    H_rows = []
    SB_rows = []
    SR_rows = []
    P_rows = []
    C_rows = []
    HxCP_rows = []
    B_rows = []
    CT_rows = []
    L_rows = []
    RHB_rows = []
    cut = [0] * 18  # MATLAB: cut=zeros(10) を線形index 1..17 で使用
    H_TAPERED_rows = []
    SB_TAPERED_rows = []
    CPO_rows = []
    CHB_rows = []
    RH2T_rows = []
    EPC_rows = []
    EBC_rows = []

    # %%%%%%%%%% 断面情報取得 %%%%%%%%%%
    with open(filename, 'r', encoding='cp932', errors='replace') as fid:
        while True:
            tline = fid.readline()
            if tline == '':
                # MATLAB版は '*SECT-COLOR' で必ず break する前提。EOF安全策。
                break
            f_DBUSER = 'DBUSER' in tline
            f_TAPERED = 'TAPERED' in tline
            f_COMBINED = 'COMBINED' in tline
            f_SRC = 'SRC' in tline

            f_H = ', H  ,' in tline
            f_SB = ', SB ,' in tline
            f_SR = ', SR ,' in tline
            f_P = ', P  ,' in tline
            f_C = ', C  ,' in tline
            f_HP = 'HP' in tline
            f_BOX = ', B  ,' in tline
            f_BOX2 = ', CB ,' in tline
            f_CT = ', T  ,' in tline
            f_L = ', L  ,' in tline
            f_RHB = 'RHB' in tline
            f_CPO = 'CPO' in tline
            f_CHB = 'CHB' in tline
            f_RH2T = 'RH2T' in tline
            f_EPC = 'EPC' in tline
            f_EBC = 'EBC' in tline
            findnext = '*SECT-COLOR' in tline

            if f_DBUSER and f_H:  # H鋼の断面情報取得
                cut[1] += 1
                H_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=True,
                    shape='H', derived=derived))

            if f_DBUSER and f_SB:  # 中実角断面の情報取得
                cut[2] += 1
                SB_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='SB', derived=derived))

            if f_DBUSER and f_SR:  # 中実丸断面の情報取得
                cut[3] += 1
                SR_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='SR', derived=derived))

            if f_DBUSER and f_P:  # 鋼管断面の情報取得
                cut[4] += 1
                P_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='P', derived=derived))

            if f_DBUSER and f_C:  # 溝形鋼断面の情報取得
                cut[5] += 1
                C_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='C', derived=derived))

            if f_COMBINED and f_HP:  # H鋼＋カバープレート断面の情報取得
                cut[6] += 1
                HxCP_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False))

            if f_DBUSER and (f_BOX or f_BOX2):  # 角形鋼管断面の情報取得
                cut[7] += 1
                B_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='B', derived=derived))

            if f_DBUSER and f_CT:  # cut-T断面の情報取得
                cut[8] += 1
                CT_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='T', derived=derived))

            if f_DBUSER and f_L:  # L形鋼断面の情報取得
                cut[9] += 1
                L_rows.append(_parse_dbuser_line(
                    tline, section_no, section_name, h_branch=False,
                    shape='L', derived=derived))

            if f_SRC and f_RHB:  # SRC_Hの情報取得 (2行目を読む)
                cut[10] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                if len(section_one) == 7:
                    section_one = section_one + [0, 0]
                elif (section_one[7] == section_one[4]
                      and section_one[8] == section_one[6]):
                    # MATLAB: section_one(8)==section_one(5) && (9)==(7)
                    section_one[7] = 0
                    section_one[8] = 0
                RHB_rows.append([sec_no] + section_one)

            if f_TAPERED and f_H:  # テーパーH鋼の断面情報取得 (2行目を読む)
                cut[11] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=True)
                H_TAPERED_rows.append([sec_no] + section_one)

            if f_TAPERED and f_SB:  # テーパーSBの断面情報取得 (2行目を読む)
                cut[12] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                SB_TAPERED_rows.append([sec_no] + section_one)

            if f_SRC and f_CPO:  # SRC_CPOの情報取得 (2行目を読む)
                cut[13] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                CPO_rows.append([sec_no] + section_one)

            if f_SRC and f_CHB:  # SRC_CHBの情報取得 (2行目を読む)
                cut[14] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                if len(section_one) == 6:
                    section_one = section_one + [0, 0]
                CHB_rows.append([sec_no] + section_one)

            if findnext:  # MATLAB: break位置1 (CHBブロック直後)
                break

            if f_SRC and f_RH2T:  # SRC_RH2Tの情報取得 (2行目を読む)
                cut[15] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                RH2T_rows.append([sec_no] + section_one)

            if findnext:  # MATLAB: break位置2 (RH2Tブロック直後)
                break

            if f_SRC and f_EPC:  # SRC_EPCの情報取得 (2行目を読む)
                cut[16] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                EPC_rows.append([sec_no] + section_one)

            if f_SRC and f_EBC:  # SRC_EBCの情報取得 (2行目を読む)
                cut[17] += 1
                sec_no = _parse_first_line_src(tline, section_no, section_name)
                tline = fid.readline()
                section_one = _parse_second_line(tline, allow_zero=False)
                EBC_rows.append([sec_no] + section_one)

            if findnext:  # MATLAB: break位置3 (ループ末尾)
                break

    # %%%%%%%%%% 列の選別 (MATLAB末尾の列選択、1始まり列番号) %%%%%%%%%%
    H_section = _vertcat(H_rows)
    if cut[1] > 0:
        H_section = _cols(H_section, [1, 3, 4, 5, 6, 7, 8, 9])
    SB_section = _vertcat(SB_rows)
    if cut[2] > 0:
        SB_section = _cols(SB_section, [1, 3, 4])
    SR_section = _vertcat(SR_rows)
    if cut[3] > 0:
        SR_section = _cols(SR_section, [1, 3])
    P_section = _vertcat(P_rows)
    if cut[4] > 0:
        P_section = _cols(P_section, [1, 3, 4])
    C_section = _vertcat(C_rows)
    if cut[5] > 0:
        C_section = _cols(C_section, [1, 3, 4, 5, 6])
    HxCP_section = _vertcat(HxCP_rows)
    if cut[6] > 0:
        HxCP_section = _cols(HxCP_section, [1, 3, 4, 5, 6, 7, 8])
    B_section = _vertcat(B_rows)
    if cut[7] > 0:
        B_section = _cols(B_section, [1, 3, 4, 5, 6])
        # MATLAB: B_section(:,6)=0 (5列→6列に拡張、角鋼管のr=0)
        B_section = np.hstack([B_section,
                               np.zeros((B_section.shape[0], 1))])
    CT_section = _vertcat(CT_rows)
    if cut[8] > 0:
        CT_section = _cols(CT_section, [1, 3, 4, 5, 6])
    L_section = _vertcat(L_rows)
    if cut[9] > 0:
        L_section = _cols(L_section, [1, 3, 4, 5, 6])
    RHB_section = _vertcat(RHB_rows)
    if cut[10] > 0:
        RHB_section = _cols(RHB_section, [1, 2, 3, 5, 6, 7, 8, 9, 10])
    H_TAPERED_section = _vertcat(H_TAPERED_rows)
    if cut[11] > 0:
        H_TAPERED_section = _cols(
            H_TAPERED_section,
            [1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16])
    SB_TAPERED_section = _vertcat(SB_TAPERED_rows)  # (:,:) 全列
    CPO_section = _vertcat(CPO_rows)
    if cut[13] > 0:
        CPO_section = _cols(CPO_section, [1, 2, 4, 5])
    CHB_section = _vertcat(CHB_rows)
    if cut[14] > 0:
        CHB_section = _cols(CHB_section, [1, 2, 4, 5, 6, 7, 8, 9])
    RH2T_section = _vertcat(RH2T_rows)
    if cut[15] > 0:
        RH2T_section = _cols(RH2T_section,
                             [1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12])
    EPC_section = _vertcat(EPC_rows)
    if cut[16] > 0:
        EPC_section = _cols(EPC_section, [1, 4, 5])
    EBC_section = _vertcat(EBC_rows)
    if cut[17] > 0:
        EBC_section = _cols(EBC_section, [1, 5, 6, 7, 8])

    # sections cell の構築 (index 0 未使用、1..16)
    sections = [None] * 18
    sections[1] = H_section
    sections[2] = SB_section
    sections[3] = SR_section
    sections[4] = P_section
    sections[5] = C_section
    sections[6] = HxCP_section
    sections[7] = B_section
    sections[8] = CT_section
    sections[9] = L_section
    sections[10] = RHB_section
    sections[11] = H_TAPERED_section
    sections[12] = SB_TAPERED_section
    sections[13] = CPO_section
    sections[14] = CHB_section
    sections[15] = RH2T_section
    # MATLAB版どおり EPC(円形CFT)->sections[16], EBC(角形CFT)->sections[17]
    # (旧版はindex16に集約していたが、角形CFTの形状マーカー17000が失われ
    #  CFT_ratio_analysisの分岐と不整合になるため2026-07-12に分離)
    sections[16] = EPC_section
    sections[17] = EBC_section

    section_no = np.array(section_no, dtype=float)
    if derived:
        items = '、'.join(
            '断面番号%d「%s」(DB名: %s)' % (d['secno'], d['name'], d['db_name'])
            for d in derived)
        raise ValueError(
            '以下のDB名指定(iCEL=1)断面はJIS標準断面表に一致せず寸法復元できません: '
            + items +
            '。標準寸法のH形鋼はDB名からフィレット込みで自動復元しますが、'
            '非標準寸法やH以外の形状は復元できません。MIDASで該当断面を'
            'User(数値入力)に変更して再エクスポートしてください')
    return sections, section_no, section_name


#: 直近の mgtopen_section 呼び出しでDB名から寸法復元した断面の一覧。
mgtopen_section.derived_sections = []
