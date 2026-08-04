# -*- coding: utf-8 -*-
"""MIDAS法定応力図作成 (Midasstress / MIDASstressplot_fig.m の移植).

MATLAB GUIツール Midasstress (privatetool_function/MIDAS/stress/) の
鉛直構面応力図 (figure_stress_axis.m と stress_plot/ 配下の描画関数群) を
Python/matplotlib に移植したもの。GUIダイアログ(uigetfile/listdlg/questdlg)は
すべて plot_stress() の引数に置き換えている。

移植元:
  MIDASstressplot_fig.m        : 本体 (データ読込・荷重ケース区切り・通り芯ループ)
  figure_stress_axis.m         : 鉛直構面の応力図1枚分
  stress_plot/column_maxstress_plot.m / column_stress_plot.m
  stress_plot/beam_maxstress_plot.m   / beam_stress_plot.m
  stress_plot/uni_column_maxstress_plot.m / uni_column_stress_plot.m
  stress_plot/uni_beam_maxstress_plot.m   / uni_beam_stress_plot.m
  stress_plot/truss_stress_plot.m / plate_stress_plot.m / wallstress_plot.m
  stress_plot/unit_plot.m / secondmoment.m
  get_wstress.m / unit_add_ysub.m / wallinfo_get.m (ratio/)
  l_case_ratio_analysis.m (ratio/) : 対話部を除く既定荷重ケース名のみ移植

デフォルト値は Midasstress.m の OpeningFcn / calculate_Callback の初期値:
  line_location=2.0, axisname_location=3.0, mergins=[5 2 5 5],
  reactiondis=0.5, fontsize=[4 5 6 8 5] (応力値/寸法値/通り芯符号/タイトル/反力値),
  dot=[1 1 1 1] (柱/梁/トラス/壁の小数点桁: 1->%.1f, その他->%.2f),
  dummy_no(limit_sec_no)=9000, mini_length=7, mscale=0.0005,
  select_unit=Inf (分割部材をバラバラに表示), select13=[1 1] (柱梁とも全応力表示),
  cross_axis=1 (全節点で通り芯近似), print_format=PDF, close_set=1。

MATLAB版との意図的差異:
  - GUIダイアログ(ファイル選択・構面選択・荷重ケース選択・荷重ケース名入力)は
    関数引数化。荷重ケース名は load_case_names 引数、省略時は
    l_case_ratio_analysis.m の既定パターン (1:G+P / 2:中長期,中短期 /
    3:G+P,KX,KY / 5:G+P,±KX,±KY / その他:CASE番号) を非対話で適用。
  - 出力はPDF保存に一本化 (print_format=3 相当)。ファイル名は
    'stress_<連番>_<荷重ケース名>_<構面名>.pdf' (MATLAB版は '<連番>stress.pdf')。
  - 1画面あたりの構面数 (figure_plot_num) はGUI初期値の1x1に固定
    (1構面=1ページ)。subplotによる複数構面/枚のレイアウトは省略。
  - 用紙サイズのデフォルトはA3 (GUI初期値はA4だが要件によりA3)。
    横長/縦長は MATLAB 同様に図の縦横比から自動決定。
  - components 引数で N/M/Q の注記・M図の表示を選択できる (MATLAB版は常に全て)。
    梁の下段注記はQとNが1つの文字列に結合されているため、'Q'または'N'の
    いずれかが指定されていれば表示する。
  - 水平構面(床伏せ)の応力図 (figure_stress_floor.m 系) は要件対象外のため未移植。
  - mgtと応力txtの要素番号不整合はプログラムで吸収せず、NOTEとして報告する。
  - secondmoment (M図の2次曲線) は3点のx座標が同一の場合スキップする
    (MATLAB版はpolyfit警告の上で不定形を描く)。
"""

import os
import sys
import math

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    from .mgt import (
        mgtopen_node, mgtopen_element, mgtopen_plate, mgtopen_group,
        mgtopen_wall, node_index_get,
    )
    from .util import (find_index, find_zero_index, doublecheck,
                       space_erace, _colon, loadtxt_tolerant)
    from .draw_model import (
        _setup_japanese_font, _finish_figure, _text, _kline, _plate_plot,
        _project_nodes, _tick, _axis_coefficients, _unit_elements,
        _resolve_select, _safe_name, column_beam_judge_one, line_projection,
    )
except ImportError:  # スクリプト実行時
    from mgt import (
        mgtopen_node, mgtopen_element, mgtopen_plate, mgtopen_group,
        mgtopen_wall, node_index_get,
    )
    from util import (find_index, find_zero_index, doublecheck,
                      space_erace, _colon, loadtxt_tolerant)
    from draw_model import (
        _setup_japanese_font, _finish_figure, _text, _kline, _plate_plot,
        _project_nodes, _tick, _axis_coefficients, _unit_elements,
        _resolve_select, _safe_name, column_beam_judge_one, line_projection,
    )


# ---------------------------------------------------------------------------
# 応力・反力ファイルの読み込み (MIDASstressplot_fig.m のtextread部)
# ---------------------------------------------------------------------------

def read_beam_stress(path):
    """beam_stress.txt を読む.

    列: 1:要素番号 2:荷重ケース 3:Fx 4:Fy 5:Fz 6:Mx 7:My 8:Mz
    1要素あたり3行 (i端/中央/j端)。2行 (i端/j端: 終局形式) の場合は
    MATLAB版同様、中央=両端平均の3行形式へ展開する。
    """
    beam_stress = loadtxt_tolerant(path)
    if beam_stress.size == 0:
        return np.array([])
    if beam_stress.shape[1] != 8:
        raise ValueError('beam_stressの列数が8ではありません: %s' % path)
    if beam_stress.shape[0] >= 3 and \
            beam_stress[0, 0] == beam_stress[2, 0]:
        pass
    else:  # 終局 (2行/要素) -> 3行/要素へ展開
        num_b = beam_stress.shape[0] // 2
        out = np.zeros((3 * num_b, 8))
        for k in range(num_b):
            out[3 * k, :] = beam_stress[2 * k, :]
            out[3 * k + 1, :] = (beam_stress[2 * k, :] * 0.5
                                 + beam_stress[2 * k + 1, :] * 0.5)
            out[3 * k + 2, :] = beam_stress[2 * k + 1, :]
        beam_stress = out
    return beam_stress


def read_truss_stress(path):
    """truss_stress.txt を読む. 列: 1:要素番号 2:荷重ケース 3:Fx(i) 4:Fx(j)"""
    a = loadtxt_tolerant(path)
    if a.size and a.shape[1] != 4:
        raise ValueError('truss_stressの列数が4ではありません: %s' % path)
    return a


def read_plate_stress(path):
    """plate_stress.txt を読む. 列: 1:要素番号 2:荷重ケース 3:節点 4:Fxy"""
    a = loadtxt_tolerant(path)
    if a.size and a.shape[1] != 4:
        raise ValueError('plate_stressの列数が4ではありません: %s' % path)
    return a


def read_reaction(path):
    """reaction.txt を読む. 列: 1:節点番号 2:荷重ケース 3:FX 4:FY 5:FZ 6-8:M"""
    a = loadtxt_tolerant(path)
    if a.size and a.shape[1] != 8:
        raise ValueError('reactionの列数が8ではありません: %s' % path)
    return a


def get_wstress(path):
    """get_wstress.m: 壁応力ファイル(2行/1データ)を読む.

    loadtxt_tolerant と同様に、見出し行・空行のスキップと
    文字コード (UTF-8/CP932) の自動判定を行う (2行1組は数値行のみで対を組む)。
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
    num_lines = []
    skipped = 0
    for ln in text.splitlines():
        t = ln.strip()
        if not t:
            continue
        try:
            num_lines.append([float(v)
                              for v in t.replace(',', ' ').split()])
        except ValueError:
            skipped += 1
    if skipped:
        print('注記: %s の見出しなど数値でない %d 行を読み飛ばしました'
              % (_os.path.basename(str(path)), skipped))
    if len(num_lines) % 2 != 0:
        raise ValueError('%s: 壁応力は2行1組ですが数値行が奇数(%d行)です'
                         % (_os.path.basename(str(path)), len(num_lines)))
    wall_stress = []
    i = 0
    while i < len(num_lines):
        wall_stress.append(num_lines[i])
        wall_stress.append(num_lines[i + 1] + [0.0, 0.0, 0.0])
        i += 2
    return np.array(wall_stress, dtype=float)


def _load_case_split(case_col):
    """荷重ケースの区切り検出 (doublecheck + find_index).

    戻り値: (load_case_no, load_case_index)
      load_case_no   : 昇順・重複なしの荷重ケース番号列
      load_case_index: 各ケースの先頭行 (0-based; MATLAB版は1-based)
    """
    case_col = np.asarray(case_col, dtype=float).ravel()
    load_case_no = doublecheck(case_col)
    load_case_index = np.atleast_1d(find_index(case_col, load_case_no))
    return load_case_no, load_case_index


def _default_case_names(load_case_no):
    """l_case_ratio_analysis.m の既定パターンによる荷重ケース名 (非対話)."""
    n = len(load_case_no)
    if n == 1:
        return ['G+P']
    if n == 2:
        return ['中長期', '中短期']
    if n == 3:
        return ['G+P', 'KX', 'KY']
    if n == 5:
        return ['G+P', '+KX', '-KX', '+KY', '-KY']
    return ['CASE' + str(int(c)) for c in load_case_no]


# ---------------------------------------------------------------------------
# 小物 (逐語移植)
# ---------------------------------------------------------------------------

def _fmt(dot):
    """小数点表示設定 (dot==1 -> %.1f, その他 -> %.2f)."""
    return '%.1f' if dot == 1 else '%.2f'


def _beta_norm(beta):
    """β角度の調整 (0-360度へ)."""
    if 0 <= beta <= 360:
        return beta
    while beta < 0:
        beta += 360
    while beta > 360:
        beta -= 360
    return beta


def _maxabs(v):
    """[MV, maxindex] = max(abs(v)) 相当。0-based indexの値を返す."""
    v = np.asarray(v, dtype=float).ravel()
    return v[int(np.argmax(np.abs(v)))]


def _secondmoment(ax, xnodes, ynodes):
    """secondmoment.m: 3点を通る2次曲線でM図を描く."""
    xnodes = np.asarray(xnodes, dtype=float)
    ynodes = np.asarray(ynodes, dtype=float)
    if xnodes[2] == xnodes[0] or len(set(xnodes.tolist())) < 3:
        return  # x座標が重複するとpolyfitが特異 (MATLAB版は警告の上不定形)
    P = np.polyfit(xnodes, ynodes, 2)
    mesh = (xnodes[2] - xnodes[0]) / 20.0
    px = _colon(xnodes[0], mesh, xnodes[2])
    py = P[0] * px ** 2 + P[1] * px + P[2]
    _kline(ax, px, py, linewidth=0.2)


def _rodrigues(n, beta):
    """MATLAB版の4x4回転行列 (軸n, 角beta) の3x3部分."""
    nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
    c, s = math.cos(beta), math.sin(beta)
    return np.array([
        [nx * nx * (1 - c) + c, nx * ny * (1 - c) - nz * s,
         nz * nx * (1 - c) + ny * s],
        [nx * ny * (1 - c) + nz * s, ny * ny * (1 - c) + c,
         ny * nz * (1 - c) - nx * s],
        [nz * nx * (1 - c) - ny * s, ny * nz * (1 - c) + nx * s,
         nz * nz * (1 - c) + c]])


def _column_local_axes(nodeM, ni, nj, beta_deg):
    """傾斜柱(1.9)の局所Y/Z軸を求める (column_*stress_plot.m の共通部).

    nodeM: 投影後の節点行列 [no, s, t, Z]。MATLAB版同様、投影後の
    座標(2:4列)を三次元座標として扱う。
    """
    XAXIS = nodeM[nj, 1:4] - nodeM[ni, 1:4]
    XAXIS = XAXIS / np.linalg.norm(XAXIS)

    if XAXIS[0] >= 0:
        fai = math.asin(-1 * XAXIS[2])
    else:
        fai = -math.pi - math.asin(-1 * XAXIS[2])
    sita = math.asin(-1 * XAXIS[1] / math.cos(fai))
    Rz = np.array([[math.cos(sita), math.sin(sita), 0],
                   [-math.sin(sita), math.cos(sita), 0],
                   [0, 0, 1]])
    ZAXIS = Rz @ np.array([0.0, 0.0, 1.0])
    YAXIS = Rz @ np.array([0.0, 1.0, 0.0])

    ZAXIS = _rodrigues(YAXIS, fai) @ ZAXIS

    # Z軸のZ座標が負ならX軸まわりに180度回転
    if ZAXIS[2] < 0:
        R = _rodrigues(XAXIS, math.pi)
        ZAXIS = R @ ZAXIS
        YAXIS = R @ YAXIS

    angle2 = -1 * beta_deg
    beta_r = -1 * angle2 / 180 * math.pi
    R = _rodrigues(XAXIS, beta_r)
    ZAXIS = R @ ZAXIS
    YAXIS = R @ YAXIS
    return YAXIS, ZAXIS


def _column_rotd(nodeM, ni, nj, a2):
    """傾斜柱の注記回転角 (column_*stress_plot.m)."""
    if (nodeM[ni, 1] - nodeM[nj, 1]) == 0:
        return 0.0
    rotd = math.degrees(math.atan(
        (nodeM[ni, a2] - nodeM[nj, a2]) / (nodeM[ni, 1] - nodeM[nj, 1])))
    return rotd - 90 if rotd > 0 else rotd + 90


def _beam_rotd(nodeM, ni, nj, a1, a2):
    """梁の注記回転角 (beam_*stress_plot.m)."""
    if (nodeM[ni, a1] - nodeM[nj, a1]) == 0:
        return 90.0
    return math.degrees(math.atan(
        (nodeM[ni, a2] - nodeM[nj, a2]) / (nodeM[ni, a1] - nodeM[nj, a1])))


# ---------------------------------------------------------------------------
# 柱の応力plot (column_maxstress_plot.m / column_stress_plot.m)
# ---------------------------------------------------------------------------

def _column_maxstress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                           mscale, fontsize, dot, axis_direction, comp):
    """column_maxstress_plot.m: 柱の最大応力をplot (axis=[2 4]相当)."""
    fp = _fmt(dot)
    a2 = axis_cols[1]
    beta = angle[1] - math.degrees(math.asin(axis_direction[1]))
    converse_M = 1.0

    mid_x = nodeM[ni, 1] * 0.5 + nodeM[nj, 1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    if angle[0] == 2:
        beta = _beta_norm(beta)
        if (0 <= beta <= 45) or (135 < beta < 225) or (315 <= beta <= 360):
            # 強軸がX方向: M=col5(My), Q=col3(Fz)
            if 'M' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 4]),
                      ha='right', va='middle', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 2]),
                      ha='left', va='top', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='left', va='baseline', fs=fontsize)
            if 'M' in comp and mscale > 0:
                trig = math.cos(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, 1],
                        nodeM[ni, 1] - stress[0, 4] * trig * mscale * converse_M,
                        mid_x - stress[1, 4] * trig * mscale * converse_M,
                        nodeM[nj, 1] - stress[2, 4] * trig * mscale * converse_M,
                        nodeM[nj, 1]],
                       [nodeM[ni, a2], nodeM[ni, a2], mid_y,
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

        elif (45 < beta <= 135) or (225 <= beta < 315):
            # 弱軸がX方向: M=col6(Mz), Q=col2(Fy)
            if nodeM[ni, a2] > nodeM[nj, a2]:
                converse_M = -1.0
            elif nodeM[ni, a2] < nodeM[nj, a2]:
                converse_M = 1.0
            else:
                raise RuntimeError('柱部材の定義ミス？')

            if 'M' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 5]),
                      ha='right', va='middle', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 1]),
                      ha='left', va='top', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='left', va='baseline', fs=fontsize)
            if 'M' in comp and mscale > 0:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, 1],
                        nodeM[ni, 1] - stress[0, 5] * trig * mscale * converse_M,
                        mid_x - stress[1, 5] * trig * mscale * converse_M,
                        nodeM[nj, 1] - stress[2, 5] * trig * mscale * converse_M,
                        nodeM[nj, 1]],
                       [nodeM[ni, a2], nodeM[ni, a2], mid_y,
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

    elif angle[0] == 1.9:  # 傾いている柱のplot
        rotd = _column_rotd(nodeM, ni, nj, a2)
        shear_direction = np.array([1.0, 0.0, 0.0])
        YAXIS, ZAXIS = _column_local_axes(nodeM, ni, nj, beta)

        if abs(shear_direction @ ZAXIS) <= abs(shear_direction @ YAXIS):
            # MzとFy
            direction_M = 1.0 if (shear_direction @ YAXIS) < 0 else -1.0
            mcol, qcol = 5, 1
        else:  # MyとFz
            direction_M = 1.0 if (shear_direction @ ZAXIS) < 0 else -1.0
            mcol, qcol = 4, 2

        if 'M' in comp:
            _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, mcol]),
                  ha='right', va='middle', fs=fontsize, rot=rotd)
        if 'Q' in comp:
            _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, qcol]),
                  ha='left', va='top', fs=fontsize, rot=rotd)
        if 'N' in comp:
            _text(ax, mid_x, mid_y, fp % stress[1, 0],
                  ha='left', va='baseline', fs=fontsize, rot=rotd)
        if 'M' in comp and mscale > 0:
            dM = direction_M * mscale * converse_M
            _kline(ax, [nodeM[ni, 1], nodeM[ni, 1] + stress[0, mcol] * dM],
                   [nodeM[ni, a2], nodeM[ni, a2]], linewidth=0.2)
            _kline(ax, [nodeM[nj, 1] + stress[2, mcol] * dM, nodeM[nj, 1]],
                   [nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)
            if max(abs(stress[:, mcol])) < 1:
                pass
            else:
                _kline(ax, [nodeM[ni, 1] + stress[0, mcol] * dM,
                            nodeM[nj, 1] + stress[2, mcol] * dM],
                       [nodeM[ni, a2], nodeM[nj, a2]], linewidth=0.2)


def _column_stress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                        mscale, fontsize, dot, axis_direction, comp):
    """column_stress_plot.m: 柱の応力を端部別にplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = angle[1] - math.degrees(math.asin(axis_direction[1]))
    converse_M = 1.0

    p73x = nodeM[ni, 1] * 0.7 + nodeM[nj, 1] * 0.3
    p73y = nodeM[ni, a2] * 0.7 + nodeM[nj, a2] * 0.3
    p37x = nodeM[ni, 1] * 0.3 + nodeM[nj, 1] * 0.7
    p37y = nodeM[ni, a2] * 0.3 + nodeM[nj, a2] * 0.7
    mid_x = nodeM[ni, 1] * 0.5 + nodeM[nj, 1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    if angle[0] == 2:
        beta = _beta_norm(beta)
        if (0 <= beta <= 45) or (135 < beta < 225) or (315 <= beta <= 360):
            # 強軸まわりのモーメント(5)をplot
            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 2],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='right', va='middle', fs=fontsize)
            if 'M' in comp:
                # axis(1)=2 -> 3+axis(1)=5 (My, 0-based 4)
                trig = math.cos(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, 1],
                        nodeM[ni, 1] - stress[0, 4] * trig * mscale * converse_M,
                        mid_x - stress[1, 4] * trig * mscale * converse_M,
                        nodeM[nj, 1] - stress[2, 4] * trig * mscale * converse_M,
                        nodeM[nj, 1]],
                       [nodeM[ni, a2], nodeM[ni, a2], mid_y,
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

        elif (45 < beta <= 135) or (225 <= beta < 315):
            if nodeM[ni, a2] > nodeM[nj, a2]:
                converse_M = -1.0
            elif nodeM[ni, a2] < nodeM[nj, a2]:
                converse_M = 1.0
            else:
                raise RuntimeError('柱部材の定義ミス？')

            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 1],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='right', va='middle', fs=fontsize)
            if 'M' in comp:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, 1],
                        nodeM[ni, 1] - stress[0, 5] * trig * mscale * converse_M,
                        mid_x - stress[1, 5] * trig * mscale * converse_M,
                        nodeM[nj, 1] - stress[2, 5] * trig * mscale * converse_M,
                        nodeM[nj, 1]],
                       [nodeM[ni, a2], nodeM[ni, a2], mid_y,
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

    elif angle[0] == 1.9:  # 傾いている柱のplot
        shear_direction = np.array([1.0, 0.0, 0.0])
        YAXIS, ZAXIS = _column_local_axes(nodeM, ni, nj, beta)

        if abs(shear_direction @ ZAXIS) <= abs(shear_direction @ YAXIS):
            # MzとFy (注記はMATLAB版どおり My/Mz 両方・Q=Fz)
            direction_M = 1.0 if (shear_direction @ YAXIS) < 0 else -1.0
            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 2],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='right', va='middle', fs=fontsize)
            mcol = 5
        else:
            direction_M = 1.0 if (shear_direction @ ZAXIS) < 0 else -1.0
            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 1],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='right', va='middle', fs=fontsize)
            mcol = 4

        if 'M' in comp:
            dM = direction_M * mscale * converse_M
            _kline(ax, [nodeM[ni, 1], nodeM[ni, 1] + stress[0, mcol] * dM],
                   [nodeM[ni, a2], nodeM[ni, a2]], linewidth=0.2)
            _kline(ax, [nodeM[nj, 1] + stress[2, mcol] * dM, nodeM[nj, 1]],
                   [nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)
            if max(abs(stress[:, mcol])) < 1:
                pass
            else:
                xnodes = [nodeM[ni, 1] + stress[0, mcol] * dM,
                          mid_x + stress[1, mcol] * dM,
                          nodeM[nj, 1] + stress[2, mcol] * dM]
                ynodes = [nodeM[ni, a2], mid_y, nodeM[nj, a2]]
                _secondmoment(ax, xnodes, ynodes)


# ---------------------------------------------------------------------------
# 梁の応力plot (beam_maxstress_plot.m / beam_stress_plot.m)
# ---------------------------------------------------------------------------

def _beam_maxstress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                         mscale, fontsize, dot, comp):
    """beam_maxstress_plot.m: 梁要素の最大応力をplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1])
    rotd = _beam_rotd(nodeM, ni, nj, a1, a2)
    mid_x = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    if angle[0] == 1:
        if (0 <= beta <= 45) or (135 <= beta <= 225) or (315 <= beta <= 360):
            # 通常の強軸つかい (My=col5をplot)
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 4]) + '|'
                      + fp % _maxabs(stress[0:3, 5]),
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 2]) + '|'
                      + fp % _maxabs(stress[0:3, 0]),
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.cos(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 4] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 4] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], mid_x, nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 4] * trig * mscale,
                     mid_y - stress[1, 4] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 4] * trig * mscale])

        elif (45 < beta < 135) or (225 < beta < 315):
            # ヨコヅカイ (Mz=col6をplot)
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 5])
                      + fp % _maxabs(stress[0:3, 4]),
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 1])
                      + fp % _maxabs(stress[0:3, 0]),
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 5] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 5] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], mid_x, nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 5] * trig * mscale,
                     mid_y - stress[1, 5] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 5] * trig * mscale])
    elif angle[0] == 1.9:
        pass  # MATLAB版もstopをコメントアウト


def _beam_stress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                      mscale, fontsize, dot, comp):
    """beam_stress_plot.m: 梁要素の応力を端部・中央別にplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1])
    rotd = _beam_rotd(nodeM, ni, nj, a1, a2)
    mid_x = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    # MATLAB版: if angle(1)==1 || 1.9 は常に真
    if (0 <= beta <= 45) or (135 <= beta <= 225) or (315 <= beta <= 360):
        if 'M' in comp:
            _text(ax, mid_x, mid_y,
                  fp % stress[0, 4] + '｜' + fp % stress[1, 4]
                  + '｜' + fp % stress[2, 4],
                  ha='center', va='bottom', fs=fontsize, rot=rotd)
        if ('Q' in comp) or ('N' in comp):
            _text(ax, mid_x, mid_y,
                  fp % stress[0, 2] + '｜' + fp % _maxabs(stress[0:3, 0])
                  + '｜' + fp % _maxabs(stress[0:3, 5])
                  + '｜' + fp % stress[2, 2],
                  ha='center', va='cap', fs=fontsize, rot=rotd)
        if 'M' in comp and mscale > 0:
            trig = math.cos(beta * math.pi / 180)
            _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                   [nodeM[ni, a2],
                    nodeM[ni, a2] - stress[0, 4] * trig * mscale],
                   linewidth=0.2)
            _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                   [nodeM[nj, a2] - stress[2, 4] * trig * mscale,
                    nodeM[nj, a2]], linewidth=0.2)
            _secondmoment(
                ax, [nodeM[ni, a1], mid_x, nodeM[nj, a1]],
                [nodeM[ni, a2] - stress[0, 4] * trig * mscale,
                 mid_y - stress[1, 4] * trig * mscale,
                 nodeM[nj, a2] - stress[2, 4] * trig * mscale])

    elif (45 < beta < 135) or (225 < beta < 315):
        if 'M' in comp:
            _text(ax, mid_x, mid_y,
                  fp % stress[0, 5] + '｜' + fp % stress[1, 5]
                  + '｜' + fp % stress[2, 5],
                  ha='center', va='bottom', fs=fontsize, rot=rotd)
        if ('Q' in comp) or ('N' in comp):
            _text(ax, mid_x, mid_y,
                  fp % stress[0, 1] + '｜' + fp % _maxabs(stress[0:3, 0])
                  + '｜' + fp % _maxabs(stress[0:3, 4])
                  + '｜' + fp % stress[2, 1],
                  ha='center', va='cap', fs=fontsize, rot=rotd)
        if 'M' in comp and mscale > 0:
            trig = math.sin(beta * math.pi / 180)
            _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                   [nodeM[ni, a2],
                    nodeM[ni, a2] - stress[0, 5] * trig * mscale],
                   linewidth=0.2)
            _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                   [nodeM[nj, a2] - stress[2, 5] * trig * mscale,
                    nodeM[nj, a2]], linewidth=0.2)
            _secondmoment(
                ax, [nodeM[ni, a1], mid_x, nodeM[nj, a1]],
                [nodeM[ni, a2] - stress[0, 5] * trig * mscale,
                 mid_y - stress[1, 5] * trig * mscale,
                 nodeM[nj, a2] - stress[2, 5] * trig * mscale])


# ---------------------------------------------------------------------------
# トラス・板の応力plot (truss_stress_plot.m / plate_stress_plot.m)
# ---------------------------------------------------------------------------

def _truss_stress_plot(ax, stress, ni, nj, nodeM, fontsize, dot, comp):
    """truss_stress_plot.m: トラス材の軸力値をplot (axis=2相当)."""
    if 'N' not in comp:
        return
    fp = _fmt(dot)
    a = 1  # 投影後の面内座標列 (MATLAB: node(:,2))
    xi, zi = nodeM[ni, a], nodeM[ni, 3]
    xj, zj = nodeM[nj, a], nodeM[nj, 3]
    XY = math.hypot(nodeM[ni, 1] - nodeM[nj, 1], nodeM[ni, 2] - nodeM[nj, 2])
    Z = abs(nodeM[ni, 3] - nodeM[nj, 3])
    if (xi - xj) == 0:
        rotd = 90.0
    else:
        rotd = math.degrees(math.atan((zi - zj) / (xi - xj)))
    val = fp % stress[0]
    if xi < xj:
        if zi < zj:  # 右上あがり
            x, y = xi * 0.3 + xj * 0.7, zi * 0.3 + zj * 0.7
            va = 'baseline' if XY > Z else 'cap'
        else:        # 左上あがり
            x, y = xi * 0.7 + xj * 0.3, zi * 0.7 + zj * 0.3
            va = 'baseline'
    else:
        if zj < zi:  # 右上あがり
            x, y = xj * 0.3 + xi * 0.7, zj * 0.3 + zi * 0.7
            va = 'baseline' if XY > Z else 'cap'
        else:        # 左上あがり
            x, y = xj * 0.7 + xi * 0.3, zj * 0.7 + zi * 0.3
            va = 'baseline'
    _text(ax, x, y, val, ha='center', va=va, fs=fontsize, rot=rotd)


def _plate_stress_plot(ax, stress, node4_no, nodeM, fontsize, dot):
    """plate_stress_plot.m: 板要素の最大応力(絶対値)を中心に注記."""
    fp = _fmt(dot)
    stress_max = float(np.max(np.abs(np.asarray(stress, dtype=float))))
    node4_index = [find_index(nodeM[:, 0], nn) for nn in node4_no]
    center = nodeM[node4_index, 1:4].sum(axis=0) / len(node4_no)
    _text(ax, center[0], center[2], fp % stress_max,
          ha='center', va='middle', fs=fontsize, rot=0.0)


# ---------------------------------------------------------------------------
# 壁の応力plot (wallstress_plot.m / wallinfo_get.m)
# ---------------------------------------------------------------------------

def _wallinfo_get(wall_element, wall_ID):
    """wallinfo_get.m: レベル・壁IDから壁要素の行indexを返す (0-based)."""
    if np.size(wall_element) == 0:
        return np.array([]), np.array([], dtype=int)
    height_index = find_zero_index(wall_element[:, 0] - wall_ID[0])
    if height_index.size == 0:
        return np.array([]), np.array([], dtype=int)
    hwe = wall_element[height_index, :]
    ID_index = find_zero_index(hwe[:, 1] - wall_ID[1])
    return hwe[ID_index, :], height_index[ID_index]


def _wallstress_plot(ax, wall_stress_ID, wall_stress, wall_element, nodeM,
                     load_case, mscale, fontsize, dot):
    """wallstress_plot.m: 耐力壁の応力とM図をplot (axis=2相当)."""
    fp = _fmt(dot)
    a = 1  # MATLAB: node(:,axis)=node(:,2) (投影後の面内座標)
    for i in range(wall_stress_ID.shape[0]):
        if wall_stress_ID[i, 2] != load_case:
            continue
        WMyt = wall_stress[2 * i, 4]      # 耐力壁頭の曲げモーメント
        WMzt = wall_stress[2 * i, 5]
        WMyb = wall_stress[2 * i + 1, 4]  # 耐力壁脚の曲げモーメント
        WMzb = wall_stress[2 * i + 1, 5]
        WQz = wall_stress[2 * i + 1, 2]
        WN = wall_stress[2 * i + 1, 0]

        select_wall, stress_ID = _wallinfo_get(wall_element,
                                               wall_stress_ID[i, 0:2])
        if stress_ID.size == 0:
            print('ERROR:応力に対応する要素判定ミス')
            raise RuntimeError('応力に対応する要素判定ミス')
        r = int(stress_ID[0])
        Node = wall_element[r, 2:6]
        direction = wall_element[r, 8]  # MATLAB: wall_element(:,axis+7)=col9
        ni = [find_index(nodeM[:, 0], nn) for nn in Node]

        _kline(ax, [nodeM[ni[0], a], nodeM[ni[1], a], nodeM[ni[2], a],
                    nodeM[ni[3], a], nodeM[ni[0], a]],
               [nodeM[ni[0], 3], nodeM[ni[1], 3], nodeM[ni[2], 3],
                nodeM[ni[3], 3], nodeM[ni[0], 3]])
        _kline(ax, [nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5,
                    nodeM[ni[2], a] * 0.5 + nodeM[ni[3], a] * 0.5],
               [nodeM[ni[0], 3], nodeM[ni[3], 3]], linestyle=':')
        _kline(ax, [nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5,
                    nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5
                    - direction * mscale * WMyb,
                    nodeM[ni[2], a] * 0.5 + nodeM[ni[3], a] * 0.5
                    - direction * mscale * WMyt,
                    nodeM[ni[2], a] * 0.5 + nodeM[ni[3], a] * 0.5],
               [nodeM[ni[0], 3], nodeM[ni[0], 3],
                nodeM[ni[3], 3], nodeM[ni[3], 3]])

        top_x = nodeM[ni[2], a] * 0.5 + nodeM[ni[3], a] * 0.5
        top_y = nodeM[ni[0], 3] * 0.2 + nodeM[ni[3], 3] * 0.8
        bot_x = nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5
        bot_y = nodeM[ni[3], 3] * 0.2 + nodeM[ni[0], 3] * 0.8
        mid_y = nodeM[ni[3], 3] * 0.5 + nodeM[ni[0], 3] * 0.5
        _text(ax, top_x, top_y, fp % WMyt, ha='right', va='top', fs=fontsize)
        _text(ax, top_x, top_y, fp % WMzt, ha='left', va='bottom', fs=fontsize)
        _text(ax, bot_x, bot_y, fp % WMyb, ha='right', va='top', fs=fontsize)
        _text(ax, bot_x, bot_y, fp % WMzb, ha='left', va='bottom', fs=fontsize)
        _text(ax, bot_x, mid_y, fp % WN, ha='right', va='top', fs=fontsize)
        _text(ax, bot_x, mid_y, fp % WQz, ha='left', va='bottom', fs=fontsize)


# ---------------------------------------------------------------------------
# 結合部材の応力plot (unit_plot.m / uni_*_plot.m)
# ---------------------------------------------------------------------------

def _uni_column_maxstress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                               mscale, center_point, fontsize, dot,
                               axis_direction, comp):
    """uni_column_maxstress_plot.m: 結合部材の柱の最大応力をplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1] - math.degrees(math.asin(axis_direction[1])))

    if nodeM[ni, a2] > nodeM[nj, a2]:
        converse_M = -1.0  # noqa: F841 (MATLAB版でも図には未使用)
    elif nodeM[ni, a2] < nodeM[nj, a2]:
        converse_M = 1.0  # noqa: F841
    else:
        raise RuntimeError('柱部材の定義ミス？')

    rotd = 90.0 if (nodeM[ni, a1] - nodeM[nj, a1]) == 0 else \
        math.degrees(math.atan((nodeM[ni, a2] - nodeM[nj, a2])
                               / (nodeM[ni, a1] - nodeM[nj, a1])))
    mid_x = nodeM[ni, 1] * 0.5 + nodeM[nj, 1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5
    mid_x2 = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5

    if angle[0] == 2:
        if (0 <= beta <= 45) or (135 < beta < 225) or (315 <= beta <= 360):
            if 'M' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 4]),
                      ha='right', va='middle', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 2]),
                      ha='left', va='top', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='left', va='baseline', fs=fontsize)
            if 'M' in comp and mscale > 0:
                # axis(1)=2 -> stress(:,3+2)=col5 (0-based 4)
                trig = math.cos(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, a1],
                        nodeM[ni, a1] - stress[0, 4] * trig * mscale,
                        center_point[0] - stress[1, 4] * trig * mscale,
                        nodeM[nj, a1] - stress[2, 4] * trig * mscale,
                        nodeM[nj, a1]],
                       [nodeM[ni, a2], nodeM[ni, a2], center_point[2],
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)
        elif (45 < beta <= 135) or (225 <= beta < 315):
            if 'M' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 5]),
                      ha='right', va='middle', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % _maxabs(stress[0:3, 1]),
                      ha='left', va='top', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='left', va='baseline', fs=fontsize)
            if 'M' in comp and mscale > 0:
                # 8-axis(1)=6 (0-based 5)
                trig = math.sin(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, a1],
                        nodeM[ni, a1] - stress[0, 5] * trig * mscale,
                        center_point[0] - stress[1, 5] * trig * mscale,
                        nodeM[nj, a1] - stress[2, 5] * trig * mscale,
                        nodeM[nj, a1]],
                       [nodeM[ni, a2], nodeM[ni, a2], center_point[2],
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

    elif angle[0] == 1.9:  # 傾いている柱のplot
        if (0 <= beta <= 45) or (135 <= beta <= 225) or (315 <= beta <= 360):
            mcol, qcol = 4, 2
            trig = math.cos(beta * math.pi / 180)
        elif (45 < beta < 135) or (225 < beta < 315):
            mcol, qcol = 5, 1
            trig = math.sin(beta * math.pi / 180)
        else:
            return
        if 'M' in comp:
            _text(ax, mid_x2, mid_y, fp % _maxabs(stress[0:3, mcol]),
                  ha='center', va='bottom', fs=fontsize, rot=rotd)
        if ('Q' in comp) or ('N' in comp):
            _text(ax, mid_x2, mid_y,
                  fp % _maxabs(stress[0:3, qcol]) + '｜' + fp % stress[1, 0],
                  ha='center', va='cap', fs=fontsize, rot=rotd)
        if 'M' in comp and mscale > 0:
            _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                   [nodeM[ni, a2],
                    nodeM[ni, a2] - stress[0, mcol] * trig * mscale],
                   linewidth=0.2)
            _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                   [nodeM[nj, a2] - stress[2, mcol] * trig * mscale,
                    nodeM[nj, a2]], linewidth=0.2)
            _secondmoment(
                ax, [nodeM[ni, a1], mid_x2, nodeM[nj, a1]],
                [nodeM[ni, a2] - stress[0, mcol] * trig * mscale,
                 mid_y - stress[1, mcol] * trig * mscale,
                 nodeM[nj, a2] - stress[2, mcol] * trig * mscale])


def _uni_column_stress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                            mscale, center_point, fontsize, dot,
                            axis_direction, comp):
    """uni_column_stress_plot.m: 結合部材の柱の応力を端部別にplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1] - math.degrees(math.asin(axis_direction[1])))

    if nodeM[ni, a2] > nodeM[nj, a2]:
        pass  # converse_M=-1 (MATLAB版でも図には未使用)
    elif nodeM[ni, a2] < nodeM[nj, a2]:
        pass
    else:
        raise RuntimeError('柱部材の定義ミス？')

    p73x = nodeM[ni, 1] * 0.7 + nodeM[nj, 1] * 0.3
    p73y = nodeM[ni, a2] * 0.7 + nodeM[nj, a2] * 0.3
    p37x = nodeM[ni, 1] * 0.3 + nodeM[nj, 1] * 0.7
    p37y = nodeM[ni, a2] * 0.3 + nodeM[nj, a2] * 0.7
    mid_x = nodeM[ni, 1] * 0.5 + nodeM[nj, 1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5
    mid_x2 = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5

    if angle[0] == 2:
        if (0 <= beta <= 45) or (135 < beta < 225) or (315 <= beta <= 360):
            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 2],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='right', va='middle', fs=fontsize)
            if 'M' in comp and mscale > 0:
                trig = math.cos(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, a1],
                        nodeM[ni, a1] - stress[0, 4] * trig * mscale,
                        center_point[0] - stress[1, 4] * trig * mscale,
                        nodeM[nj, a1] - stress[2, 4] * trig * mscale,
                        nodeM[nj, a1]],
                       [nodeM[ni, a2], nodeM[ni, a2], center_point[2],
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)
        elif (45 < beta <= 135) or (225 <= beta < 315):
            if 'M' in comp:
                _text(ax, p73x, p73y, fp % stress[0, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p73x, p73y, fp % stress[0, 4],
                      ha='left', va='baseline', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 5],
                      ha='right', va='top', fs=fontsize)
                _text(ax, p37x, p37y, fp % stress[2, 4],
                      ha='left', va='baseline', fs=fontsize)
            if 'Q' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 1],
                      ha='left', va='middle', fs=fontsize)
            if 'N' in comp:
                _text(ax, mid_x, mid_y, fp % stress[1, 0],
                      ha='left', va='middle', fs=fontsize)
            if 'M' in comp and mscale > 0:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax,
                       [nodeM[ni, a1],
                        nodeM[ni, a1] - stress[0, 5] * trig * mscale,
                        center_point[0] - stress[1, 5] * trig * mscale,
                        nodeM[nj, a1] - stress[2, 5] * trig * mscale,
                        nodeM[nj, a1]],
                       [nodeM[ni, a2], nodeM[ni, a2], center_point[2],
                        nodeM[nj, a2], nodeM[nj, a2]], linewidth=0.2)

    elif angle[0] == 1.9:
        if (0 <= beta <= 45) or (135 <= beta <= 225) or (315 <= beta <= 360):
            mcol, qcol = 4, 2
            trig = math.cos(beta * math.pi / 180)
            dcol = 4
        elif (45 < beta < 135) or (225 < beta < 315):
            mcol, qcol = 5, 1
            trig = math.sin(beta * math.pi / 180)
            dcol = 5
        else:
            return
        if 'M' in comp:
            _text(ax, p73x, p73y, fp % stress[0, mcol],
                  ha='right', va='top', fs=fontsize)
            _text(ax, p73x, p73y, fp % stress[0, 9 - mcol - 0],
                  ha='left', va='baseline', fs=fontsize)
            _text(ax, p37x, p37y, fp % stress[2, mcol],
                  ha='right', va='top', fs=fontsize)
            _text(ax, p37x, p37y, fp % stress[2, 9 - mcol - 0],
                  ha='left', va='baseline', fs=fontsize)
        if 'Q' in comp:
            _text(ax, mid_x, mid_y, fp % stress[1, qcol],
                  ha='left', va='middle', fs=fontsize)
        if 'N' in comp:
            ha_n = 'right' if mcol == 4 else 'left'
            _text(ax, mid_x, mid_y, fp % stress[1, 0],
                  ha=ha_n, va='middle', fs=fontsize)
        if 'M' in comp and mscale > 0:
            _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                   [nodeM[ni, a2],
                    nodeM[ni, a2] - stress[0, dcol] * trig * mscale],
                   linewidth=0.2)
            _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                   [nodeM[nj, a2] - stress[2, dcol] * trig * mscale,
                    nodeM[nj, a2]], linewidth=0.2)
            _secondmoment(
                ax, [nodeM[ni, a1], mid_x2, nodeM[nj, a1]],
                [nodeM[ni, a2] - stress[0, dcol] * trig * mscale,
                 mid_y - stress[1, dcol] * trig * mscale,
                 nodeM[nj, a2] - stress[2, dcol] * trig * mscale])


def _uni_beam_maxstress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                             mscale, center_point, fontsize, dot, comp):
    """uni_beam_maxstress_plot.m: 結合部材の梁の最大応力をplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1])
    rotd = _beam_rotd(nodeM, ni, nj, a1, a2)
    mid_x = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    if angle[0] == 1:
        if (0 <= beta < 45) or (135 < beta < 225) or (315 < beta <= 360):
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 4]) + '|'
                      + fp % _maxabs(stress[0:3, 5]),
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 2]) + '|'
                      + fp % _maxabs(stress[0:3, 0]),
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.cos(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 4] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 4] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], center_point[a1 - 1], nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 4] * trig * mscale,
                     center_point[2] - stress[1, 4] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 4] * trig * mscale])
        elif (45 < beta < 135) or (225 < beta < 315):
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 5])
                      + fp % _maxabs(stress[0:3, 4]),
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % _maxabs(stress[0:3, 1])
                      + fp % _maxabs(stress[0:3, 0]),
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 5] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 5] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], center_point[a1 - 1], nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 5] * trig * mscale,
                     center_point[2] - stress[1, 5] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 5] * trig * mscale])
    elif angle[0] == 1.9:
        raise RuntimeError('uni_beam_maxstress_plot: angle=1.9 (MATLAB: stop)')


def _uni_beam_stress_plot(ax, stress, ni, nj, nodeM, axis_cols, angle,
                          mscale, center_point, fontsize, dot, comp):
    """uni_beam_stress_plot.m: 結合部材の梁の応力を端部・中央別にplot."""
    fp = _fmt(dot)
    a1, a2 = axis_cols
    beta = _beta_norm(angle[1])
    rotd = _beam_rotd(nodeM, ni, nj, a1, a2)
    mid_x = nodeM[ni, a1] * 0.5 + nodeM[nj, a1] * 0.5
    mid_y = nodeM[ni, a2] * 0.5 + nodeM[nj, a2] * 0.5

    if angle[0] == 1:
        if (0 <= beta < 45) or (135 < beta < 225) or (315 < beta <= 360):
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % stress[0, 4] + '｜' + fp % stress[1, 4]
                      + '｜' + fp % stress[2, 4],
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % stress[0, 2] + '｜' + fp % _maxabs(stress[0:3, 0])
                      + '｜' + fp % _maxabs(stress[0:3, 5])
                      + '｜' + fp % stress[2, 2],
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.cos(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 4] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 4] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], center_point[a1 - 1], nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 4] * trig * mscale,
                     center_point[2] - stress[1, 4] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 4] * trig * mscale])
        elif (45 < beta < 135) or (225 < beta < 315):
            if 'M' in comp:
                _text(ax, mid_x, mid_y,
                      fp % stress[0, 5] + '｜' + fp % stress[1, 5]
                      + '｜' + fp % stress[2, 5],
                      ha='center', va='bottom', fs=fontsize, rot=rotd)
            if ('Q' in comp) or ('N' in comp):
                _text(ax, mid_x, mid_y,
                      fp % stress[0, 1] + '｜' + fp % _maxabs(stress[0:3, 0])
                      + '｜' + fp % _maxabs(stress[0:3, 4])
                      + '｜' + fp % stress[2, 1],
                      ha='center', va='cap', fs=fontsize, rot=rotd)
            if 'M' in comp and mscale > 0:
                trig = math.sin(beta * math.pi / 180)
                _kline(ax, [nodeM[ni, a1], nodeM[ni, a1]],
                       [nodeM[ni, a2],
                        nodeM[ni, a2] - stress[0, 5] * trig * mscale],
                       linewidth=0.2)
                _kline(ax, [nodeM[nj, a1], nodeM[nj, a1]],
                       [nodeM[nj, a2] - stress[2, 5] * trig * mscale,
                        nodeM[nj, a2]], linewidth=0.2)
                _secondmoment(
                    ax, [nodeM[ni, a1], center_point[a1 - 1], nodeM[nj, a1]],
                    [nodeM[ni, a2] - stress[0, 5] * trig * mscale,
                     center_point[2] - stress[1, 5] * trig * mscale,
                     nodeM[nj, a2] - stress[2, 5] * trig * mscale])
    elif angle[0] == 1.9:
        raise RuntimeError('uni_beam_stress_plot: angle=1.9 (MATLAB: stop)')


def _unit_plot(ax, unit_element, unit_node, element, nodeM, stress,
               axis_cols, mscale, limit_sec_no, i_load, load_case_index,
               fontsize, dot, select13, mini_length, axis_direction, comp):
    """unit_plot.m: 結合部材の応力plot."""
    unit_ele_in = np.atleast_1d(find_index(element[:, 0], unit_element))
    flags = (element[unit_ele_in, 2] < limit_sec_no).astype(float)

    if flags.sum() == 0 and flags.prod() == 0:
        return  # 応力をプロットしない部材
    if not (flags.sum() > 0 and flags.prod() > 0):
        return

    m = len(unit_element)
    axiselement_index = find_index(element[:, 0], unit_element[0])
    c_b = (column_beam_judge_one(axiselement_index, element, nodeM),
           element[axiselement_index, 5])

    # 結合elementの応力のindex (0-based; 見つからなければ-1)
    elefig_index = np.atleast_1d(find_index(stress[:, 0], unit_element))
    eleedge_index = np.atleast_1d(find_index(element[:, 0], unit_element))

    found = elefig_index >= 0
    if not np.any(found):
        return
    if not np.all(found):
        # 一部の要素のみ応力がある場合: 最大値で代表しmscale=0でplot
        ni = find_index(nodeM[:, 0], unit_node[0])
        nj = find_index(nodeM[:, 0], unit_node[-1])
        center_point = nodeM[ni, 1:4] * 0.5 + nodeM[nj, 1:4] * 0.5
        rows = elefig_index[found]
        Fx_max = _maxabs(stress[rows, 2])
        Fy_max = _maxabs(stress[rows, 3])
        Fz_max = _maxabs(stress[rows, 4])
        My_max = _maxabs(stress[rows, 6])
        Mz_max = _maxabs(stress[rows, 7])
        plot_s = np.array([[0, 0, 0, 0, 0, 0],
                           [Fx_max, Fy_max, Fz_max, 0, My_max, Mz_max],
                           [0, 0, 0, 0, 0, 0]], dtype=float)
        if c_b[0] in (2.0, 1.9):
            _uni_column_maxstress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                       c_b, 0, center_point, fontsize,
                                       dot[0], axis_direction, comp)
        elif c_b[0] == 1.0:
            _uni_beam_maxstress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                     c_b, 0, center_point, fontsize,
                                     dot[1], comp)
        else:
            print('柱梁タイプが定義されていません')
        return

    lci = load_case_index[i_load]  # 0-based 先頭行

    # i端の応力値index
    if element[eleedge_index[0], 3] == unit_node[0]:
        eleifig_index = elefig_index[0] + lci
    elif element[eleedge_index[0], 4] == unit_node[0]:
        eleifig_index = elefig_index[0] + lci + 2
    else:
        print('端点の定義ミス')
        return
    # j端の応力値index
    if element[eleedge_index[m - 1], 3] == unit_node[-1]:
        elejfig_index = elefig_index[m - 1] + lci
    elif element[eleedge_index[m - 1], 4] == unit_node[-1]:
        elejfig_index = elefig_index[m - 1] + lci + 2
    else:
        print('端点の定義ミス')
        return

    # 中央部分の応力
    if m % 2 == 0:
        center_node = unit_node[(len(unit_node) + 1) // 2 - 1]
        center_point = nodeM[find_index(nodeM[:, 0], center_node), 1:4]
        if element[eleedge_index[m // 2 - 1], 4] == center_node:
            elecenter_index = elefig_index[m // 2 - 1] + lci + 2
        elif element[eleedge_index[m // 2 - 1], 3] == center_node:
            elecenter_index = elefig_index[m // 2 - 1] + lci
        else:
            print('中央点の定義ミス')
            return
    else:
        ce = (m + 1) // 2 - 1
        elecenter_index = elefig_index[ce] + lci + 1
        nci, ncj = node_index_get(unit_element[ce], element, nodeM)
        center_point = (nodeM[nci, 1:4] + nodeM[ncj, 1:4]) / 2.0

    plot_s = stress[[int(eleifig_index), int(elecenter_index),
                     int(elejfig_index)], 2:8]

    ni = find_index(nodeM[:, 0], unit_node[0])
    nj = find_index(nodeM[:, 0], unit_node[-1])
    ele_judge_length = float(np.linalg.norm(nodeM[ni, 1:4] - nodeM[nj, 1:4]))

    if c_b[0] in (2.0, 1.9):
        if nodeM[ni, 3] > nodeM[nj, 3]:
            plot_s = stress[[int(elejfig_index), int(elecenter_index),
                             int(eleifig_index)], 2:8]
        if select13[0] == 0 or ele_judge_length <= mini_length:
            _uni_column_maxstress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                       c_b, mscale, center_point, fontsize,
                                       dot[0], axis_direction, comp)
        elif select13[0] == 1:
            _uni_column_stress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                    c_b, mscale, center_point, fontsize,
                                    dot[0], axis_direction, comp)
    elif c_b[0] == 1.0:
        if select13[1] == 0 or ele_judge_length <= mini_length:
            _uni_beam_maxstress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                     c_b, mscale, center_point, fontsize,
                                     dot[1], comp)
        elif select13[1] == 1:
            _uni_beam_stress_plot(ax, plot_s, ni, nj, nodeM, axis_cols,
                                  c_b, mscale, center_point, fontsize,
                                  dot[1], comp)
    else:
        print('柱梁タイプが定義されていません')


def _unit_add_ysub(axis_ysubtick, unit_element, unit_node, element, nodeM,
                   limit_sec_no):
    """unit_add_ysub.m: 結合部材が柱なら端点の節点番号をysubに加える."""
    unit_ele_in = np.atleast_1d(find_index(element[:, 0], unit_element))
    flags = (element[unit_ele_in, 2] < limit_sec_no).astype(float)
    if flags.sum() == 0 and flags.prod() == 0:
        return axis_ysubtick
    if flags.sum() > 0 and flags.prod() > 0:
        axiselement_index = find_index(element[:, 0], unit_element[0])
        c_b0 = column_beam_judge_one(axiselement_index, element, nodeM)
        if c_b0 in (2.0, 1.9):
            return axis_ysubtick + [unit_node[0], unit_node[-1]]
        elif c_b0 == 1.0:
            return axis_ysubtick
        else:
            print('柱梁タイプが定義されていません')
            raise RuntimeError('柱梁タイプが定義されていません')
    return axis_ysubtick


# ---------------------------------------------------------------------------
# 鉛直構面の応力図1枚分 (figure_stress_axis.m)
# ---------------------------------------------------------------------------

def _figure_stress_axis(C, i_axis, i_load, case_no, case_name, truss_Lplot,
                        out_path):
    """figure_stress_axis.m: 1構面・1荷重ケースの応力図を描きPDF保存する."""
    node = C['node']
    element = C['element']
    plate = C['plate']
    wall_base = C['wall_base']
    wall_element = C['wall_element']
    beam_stress = C['beam_stress']
    truss_stress = C['truss_stress']
    plate_stress = C['plate_stress']
    wall_stress = C['wall_stress']
    wall_stress_ID = C['wall_stress_ID']
    reaction = C['reaction']
    lci_beam = C['lci_beam']
    lci_truss = C['lci_truss']
    lci_plate = C['lci_plate']
    lci_wall = C['lci_wall']
    lci_reaction = C['lci_reaction']
    unit_element = C['unit_element']
    uniele_ID = C['uniele_ID']
    comp = C['components']
    select13 = C['select13']
    select_unit = C['select_unit']
    mini_length = C['mini_length']
    limit_sec_no = C['limit_sec_no']
    mscale = C['mscale']
    dot = C['dot']
    stress_fs, dim_fs, axis_fs, title_fs, reac_fs = C['fontsize']
    mL, mR, mT, mB = C['mergins']

    direction = C['axis_direction'][i_axis]
    judge_axis = C['axis_plot_coefi'][i_axis, 0]
    axis_elems = C['axis_element_sel'][i_axis]
    axis_nodes = C['axis_node_sel'][i_axis]

    # 節点を構面方向に投影: nodeM = [no, s, t, Z]
    nodeM = _project_nodes(node, direction)

    # 構面の座標範囲 (MIDASstressplot_fig.m ループ内での再計算)
    on_idx_nodes = np.atleast_1d(find_index(node[:, 0], axis_nodes))
    on_idx_nodes = on_idx_nodes[on_idx_nodes >= 0]
    pl = node[on_idx_nodes, 1:4]
    axis_xtick, axis_ytick, axis_ztick = pl[:, 0], pl[:, 1], pl[:, 2]
    zmin = float(np.min(axis_ztick))

    fig = plt.figure()
    ax = fig.add_subplot(111)

    # %%%%%% 反力値のplot %%%%%%
    if np.size(reaction) > 0:
        support_index = np.atleast_1d(find_index(reaction[:, 0], axis_nodes))
        support_index = support_index[support_index >= 0]
        if support_index.size > 0 and i_load < len(lci_reaction):
            reac_nod_index = np.atleast_1d(
                find_index(nodeM[:, 0], reaction[support_index, 0]))
            for k in range(support_index.size):
                row = int(support_index[k] + lci_reaction[i_load])
                v1 = reaction[row, 4]
                v2 = (reaction[row, 2] * direction[0]
                      + reaction[row, 3] * direction[1])
                _text(ax, nodeM[reac_nod_index[k], 1],
                      nodeM[reac_nod_index[k], 3] - C['reactiondis'],
                      ('%.1f' % v1) + '\n' + ('%.1f' % v2),
                      ha='center', va='top', fs=reac_fs)

    # %%%%%% 要素の種別判定 (応力ファイル内の存在で判定) %%%%%%
    n_el = axis_elems.size
    etype = np.zeros(n_el, dtype=int)

    def _mark(table, code):
        if np.size(table) == 0 or n_el == 0:
            return np.array([], dtype=int)
        idx = np.atleast_1d(find_index(table[:, 0], axis_elems))
        pos = np.where(idx >= 0)[0]
        etype[pos] = code
        return pos

    on_axis_truss_index = _mark(truss_stress, 3)
    on_axis_beam_index = _mark(beam_stress, 1)
    on_axis_wall_index = _mark(wall_base, 2)
    _mark(plate_stress, 4)
    on_axis_wall_ID = axis_elems[on_axis_wall_index] \
        if on_axis_wall_index.size else np.array([])

    # 各要素の応力データ参照行 (0-based; MATLAB版はbeam_stress非空時のみ作成)
    stress_row = np.full(n_el, -1, dtype=int)
    on_axis_element_index = np.full(n_el, -1, dtype=int)
    if np.size(beam_stress) > 0:
        if on_axis_beam_index.size:
            stress_row[on_axis_beam_index] = np.atleast_1d(find_index(
                beam_stress[:, 0], axis_elems[on_axis_beam_index]))
        if on_axis_truss_index.size and np.size(truss_stress) > 0:
            stress_row[on_axis_truss_index] = np.atleast_1d(find_index(
                truss_stress[:, 0], axis_elems[on_axis_truss_index]))
        on_axis_element_index = np.atleast_1d(
            find_index(element[:, 0], axis_elems))

    axis_ysubtick = []

    # %%%%%% 通り上にある単一要素の応力plot %%%%%%
    for i_e in range(n_el):
        et = etype[i_e]
        if et == 1:  # 梁要素
            row = on_axis_element_index[i_e]
            sec_no = element[row, 2]
            ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            _kline(ax, [nodeM[ni, 1], nodeM[nj, 1]],
                   [nodeM[ni, 3], nodeM[nj, 3]])

            c_b = (column_beam_judge_one(row, element, nodeM),
                   element[row, 5])
            srow = stress_row[i_e]
            plot_s = beam_stress[srow + lci_beam[i_load]:
                                 srow + lci_beam[i_load] + 3, 2:8]

            ele_no = element[row, 0]
            ele_judge_length = float(np.linalg.norm(
                nodeM[ni, 1:4] - nodeM[nj, 1:4]))
            if np.size(uniele_ID) == 0:
                plot_ok = True
            else:
                judge_unit = find_index(uniele_ID, ele_no) + 1  # MATLAB 1-based
                plot_ok = judge_unit <= select_unit

            if sec_no >= limit_sec_no:
                pass  # ダミー材
            elif c_b[0] in (2.0, 1.9):
                if plot_ok:
                    if select13[0] == 0 or ele_judge_length <= mini_length:
                        _column_maxstress_plot(
                            ax, plot_s, ni, nj, nodeM, (1, 3), c_b, mscale,
                            stress_fs, dot[0], direction, comp)
                    elif select13[0] == 1:
                        _column_stress_plot(
                            ax, plot_s, ni, nj, nodeM, (1, 3), c_b, mscale,
                            stress_fs, dot[0], direction, comp)
                    axis_ysubtick += [nodeM[ni, 0], nodeM[nj, 0]]
            elif c_b[0] == 1.0:
                if plot_ok:
                    if select13[1] == 0 or ele_judge_length <= mini_length:
                        _beam_maxstress_plot(
                            ax, plot_s, ni, nj, nodeM, (1, 3), c_b, mscale,
                            stress_fs, dot[1], comp)
                    elif select13[1] == 1:
                        _beam_stress_plot(
                            ax, plot_s, ni, nj, nodeM, (1, 3), c_b, mscale,
                            stress_fs, dot[1], comp)
            else:
                print('柱梁タイプが定義されていません')
                raise RuntimeError('柱梁タイプが定義されていません')

        elif et == 3:  # トラス要素
            if truss_Lplot:
                ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
                ax.plot([nodeM[ni, 1] * 0.85 + nodeM[nj, 1] * 0.15,
                         nodeM[ni, 1] * 0.15 + nodeM[nj, 1] * 0.85],
                        [nodeM[ni, 3] * 0.85 + nodeM[nj, 3] * 0.15,
                         nodeM[ni, 3] * 0.15 + nodeM[nj, 3] * 0.85],
                        'ko-', markersize=1.5, linewidth=0.2)
                srow = stress_row[i_e]
                plot_s = truss_stress[srow + lci_truss[i_load], 2:4]
                _truss_stress_plot(ax, plot_s, ni, nj, nodeM, stress_fs,
                                   dot[2], comp)

        elif et == 4:  # 板要素
            plate_no = axis_elems[i_e]
            pridx = find_index(plate[:, 0], plate_no)
            node4_no = list(plate[pridx, 3:7])
            if np.prod(node4_no) == 0:
                node34 = 3
                node4_no = node4_no[:3]
            else:
                node34 = 4
            _plate_plot(ax, plate_no, plate, nodeM, int(3 - judge_axis), 3)
            p0 = find_index(plate_stress[:, 0], plate_no)
            plot_s = plate_stress[p0 + lci_plate[i_load]:
                                  p0 + lci_plate[i_load] + node34, 3]
            _plate_stress_plot(ax, plot_s, node4_no, nodeM, stress_fs, dot[2])

        elif et == 0:  # 図形ラインのみプロット
            try:
                ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            except IndexError:
                print('WARNING: 要素 %d が*ELEMENTに見つからないため'
                      'スキップします' % int(axis_elems[i_e]))
                continue
            _kline(ax, [nodeM[ni, 1], nodeM[nj, 1]],
                   [nodeM[ni, 3], nodeM[nj, 3]])

    # %%%%%% 結合部材のplot %%%%%%
    for unit in unit_element:
        if axis_elems.size == 0:
            continue
        fidx = np.atleast_1d(find_index(axis_elems, unit[1]))
        found = fidx >= 0
        if np.all(found):
            if select_unit == 0:
                _unit_plot(ax, unit[1], unit[2], element, nodeM,
                           beam_stress, (1, 3), mscale, limit_sec_no,
                           i_load, lci_beam, stress_fs, dot, select13,
                           mini_length, direction, comp)
                axis_ysubtick = _unit_add_ysub(
                    axis_ysubtick, unit[1], unit[2], element, nodeM,
                    limit_sec_no)
        elif not np.any(found):
            pass
        else:
            print('結合要素作成でエラー！？（構成要素の一部しか登録されていません）')

    # %%%%%% 壁要素のplot %%%%%%
    if on_axis_wall_index.size > 0 and np.size(wall_stress_ID) > 0 \
            and i_load < len(lci_wall):
        if i_load == len(lci_wall) - 1:
            cut = np.arange(lci_wall[i_load], wall_stress_ID.shape[0])
        else:
            cut = np.arange(lci_wall[i_load], lci_wall[i_load + 1])
        current_ID = wall_stress_ID[cut, :]
        widx = np.atleast_1d(find_index(wall_base[:, 0], on_axis_wall_ID))
        for wi in widx:
            pick_ID = []
            for j in range(current_ID.shape[0]):
                if (wall_base[wi, 9] == current_ID[j, 0]
                        and wall_base[wi, 8] == current_ID[j, 1]):
                    pick_ID.append(j)
                    if len(pick_ID) > 1:
                        raise RuntimeError(
                            '壁応力IDが重複しています (MATLAB: stop)')
            if pick_ID:
                j = pick_ID[0]
                pick2 = j + lci_wall[i_load]
                _wallstress_plot(
                    ax, current_ID[[j], :],
                    wall_stress[2 * pick2: 2 * pick2 + 2, :],
                    wall_element, nodeM, C['load_case_no_wall'][i_load],
                    mscale / 10.0, stress_fs, dot[3])

    # %%%%%% 通り芯情報 %%%%%%
    if axis_ysubtick:
        ysub = _tick(nodeM,
                     doublecheck(np.asarray(axis_ysubtick, dtype=float)), 3)
    else:
        ysub = np.array([])

    # 交差する通り芯
    pick_axis_node_no = C['pick_axis_node_no']
    num_axis = C['axis_plot_coefi'].shape[0]
    select_tick = []
    for iname in range(num_axis):
        if iname == i_axis:
            continue
        for nn in np.atleast_1d(pick_axis_node_no[i_axis]).ravel():
            if np.size(pick_axis_node_no[iname]) == 0:
                continue
            if find_index(pick_axis_node_no[iname], nn) >= 0:
                ii = find_index(nodeM[:, 0], nn)
                pick_axis_xy = nodeM[ii, 1:3]
                _text(ax, pick_axis_xy[0], zmin - C['axisname_location'],
                      space_erace(C['axis_name_sel'][iname]),
                      ha='center', va='middle', fs=axis_fs)
                select_tick.append(list(pick_axis_xy))
                break

    # %%%%%% 応力図の枠の大きさ %%%%%%
    end_node = np.array([[np.min(axis_xtick), np.min(axis_ytick)],
                         [np.max(axis_xtick), np.max(axis_ytick)],
                         [np.min(axis_xtick), np.max(axis_ytick)],
                         [np.max(axis_xtick), np.min(axis_ytick)]])
    end_node = line_projection(end_node, direction)
    xlim = (float(np.min(end_node)) - mL, float(np.max(end_node)) + mR)
    ylim = (zmin - mB, float(np.max(axis_ztick)) + mT)

    # %%%%%% 柱間隔ならびに階高を記入する %%%%%%
    select_tick = np.asarray(select_tick, dtype=float)
    if select_tick.shape[0] >= 2:
        select_tick = select_tick[np.argsort(select_tick[:, 0])]
        ax.plot(select_tick[:, 0],
                np.full(select_tick.shape[0], zmin - C['line_location']),
                'kx', markersize=3, linestyle='none')

    if np.size(C['h_axis_plot']) > 0:
        ysub = np.asarray(C['h_axis_plot'], dtype=float).ravel()

    if ysub.size > 0:
        ax.plot(np.full(ysub.size,
                        float(np.min(end_node)) - C['line_location']),
                ysub, 'kx', markersize=3, linestyle='none')

    line_y = zmin - C['line_location']
    for il in range(select_tick.shape[0] - 1):
        dd = np.linalg.norm(select_tick[il, 0:2] - select_tick[il + 1, 0:2])
        if abs(dd) > 0.1:
            _kline(ax, [select_tick[il, 0], select_tick[il + 1, 0]],
                   [line_y, line_y])
            _text(ax, select_tick[il, 0] * 0.5 + select_tick[il + 1, 0] * 0.5,
                  line_y, str(int(round(dd * 1000))),
                  ha='center', va='baseline', fs=dim_fs)

    line_x = float(np.min(end_node)) - C['line_location']
    for il in range(ysub.size - 1):
        _kline(ax, [line_x, line_x], [ysub[il], ysub[il + 1]])
        _text(ax, line_x, ysub[il] * 0.5 + ysub[il + 1] * 0.5,
              str(int(round(abs(ysub[il + 1] - ysub[il]) * 1000))),
              ha='center', va='baseline', fs=dim_fs, rot=90)

    # %%%%%% タイトル・用紙設定 %%%%%%
    if case_no < 10:
        title = '鉛直荷重時・応力図[　　荷重ケース：' + case_name + '　]'
    else:
        title = '水平荷重時・応力図[　　荷重ケース：' + case_name + '　]'

    _finish_figure(fig, ax, title, C['axis_name_sel'][i_axis],
                   xlim, ylim, title_fs, C['paper_size'])
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# mgt・応力txtの要素番号整合チェック (要件: プログラムで吸収せず報告)
# ---------------------------------------------------------------------------

def _check_id_consistency(element, beam_stress, truss_stress):
    notes = []
    if np.size(element) == 0:
        return notes
    ele_ids = set(element[:, 0].tolist())
    if np.size(beam_stress) > 0:
        bs_ids = set(beam_stress[:, 0].tolist())
        missing = sorted(bs_ids - ele_ids)
        if missing:
            notes.append('beam_stressに存在しmgtに無い要素番号: %d個 (例: %s)'
                         % (len(missing),
                            ', '.join(str(int(v)) for v in missing[:10])))
    if np.size(truss_stress) > 0:
        ts_ids = set(truss_stress[:, 0].tolist())
        missing = sorted(ts_ids - ele_ids)
        if missing:
            notes.append('truss_stressに存在しmgtに無い要素番号: %d個 (例: %s)'
                         % (len(missing),
                            ', '.join(str(int(v)) for v in missing[:10])))
    for n in notes:
        print('NOTE(データ不整合): ' + n)
    return notes


# ---------------------------------------------------------------------------
# メインAPI (MIDASstressplot_fig.m)
# ---------------------------------------------------------------------------

def plot_stress(mgt_path, beam_stress_path, out_dir,
                truss_stress_path=None, plate_stress_path=None,
                wall_stress_path=None, reaction_path=None,
                axes_select=None, load_cases=None, load_case_names=None,
                symbols_select=None,
                components=('N', 'M', 'Q'),
                truss_Lplot=False,
                heights_select=None,
                mscale=0.0005, select_unit=math.inf, select13=(1, 1),
                mini_length=7.0, limit_sec_no=9000,
                dot=(1, 1, 1, 1),
                axisname_location=3.0, line_location=2.0,
                mergins=(5.0, 2.0, 5.0, 5.0), reactiondis=0.5,
                fontsize=(4.0, 5.0, 6.0, 8.0, 5.0),
                paper_size=3, cross_axis=1, cross_height=None):
    """MIDAS mgt+応力txtから通り芯(鉛直構面)別の法定応力図PDFを作成する.

    Parameters
    ----------
    mgt_path : str
        mgtファイルパス。
    beam_stress_path : str
        梁要素応力ファイル (要素番号,荷重ケース,Fx,Fy,Fz,Mx,My,Mz を
        要素あたり3行 i端/中央/j端。2行形式は自動で3行に展開)。
    out_dir : str
        PDF出力ディレクトリ。
    truss_stress_path / plate_stress_path / wall_stress_path / reaction_path
        : str or None
        トラス・板・壁応力/反力ファイル (Noneでスキップ=MATLAB版のキャンセル)。
    axes_select : list of (str|int) or None
        描画する鉛直構面グループ名(またはindex)。None=全グループ。
    symbols_select : list of (str|int) or None
        交差通り芯符号・間隔寸法として表示するグループ。
        None=従来動作 (axes_select で描画する構面のみを符号対象とする)。
    load_cases : list of int or None
        描画する荷重ケース番号 (応力txtの2列目の値)。None=全ケース。
        ケース番号<10 は鉛直荷重時、>=10 は水平荷重時として扱う (MATLAB版同様)。
    load_case_names : list of str, dict or None
        荷重ケース名。listは昇順ケース番号順、dictは {ケース番号: 名前}。
        None=l_case_ratio_analysis.m の既定パターン。
    components : iterable of str
        描画成分。'M'=曲げ(注記+M図), 'Q'=せん断注記, 'N'=軸力注記。
        既定は全成分 (MATLAB版と同じ)。
    truss_Lplot : bool
        鉛直荷重ケースでもトラス(ブレース)応力を描くか
        (MATLAB版questdlgの既定 No)。水平荷重ケースでは常に描く。
    heights_select : list of float or None
        高さ寸法線に用いるZレベル。None=構面上の全節点レベル。
    mscale : float
        曲げモーメント図の倍率 (GUI初期値 0.0005)。0でM図省略。
    select_unit : float
        Inf=分割部材をバラバラに表示 (GUI初期値), 0=結合して一括表示。
    select13 : (柱, 梁)
        1=i端/中央/j端の応力を表示 (GUI初期値), 0=最大値のみ表示。
    mini_length : float
        この長さ以下の部材は最大値表示にする (GUI初期値 7)。
    limit_sec_no : int
        ダミー材と見なす断面番号の下限 (GUI: dummy_no, 初期値9000)。
    dot : (柱, 梁, トラス, 壁)
        小数点表示 1->%.1f, その他->%.2f (GUI初期値 全て1)。
    axisname_location / line_location : float
        交差通り芯符号・寸法線のオフセット (GUI初期値 3.0 / 2.0)。
    mergins : (L, R, T, B)
        図の外周マージン (GUI初期値 5, 2, 5, 5)。
    reactiondis : float
        反力値の節点からの下方オフセット (GUI初期値 0.5)。
    fontsize : (応力値, 寸法値, 通り芯符号, タイトル, 反力値)
        フォントサイズ (GUI初期値 4, 5, 6, 8, 5)。
    paper_size : int
        1=A1, 2=A2, 3=A3(既定), 4=A4。横/縦はMATLAB同様に縦横比から自動。
    cross_axis : int
        1=全節点で通り芯を近似 (GUI初期値), 2=cross_heightレベルの節点のみ。
    cross_height : float
        cross_axis=2 のときの交差通り芯レベル。

    Returns
    -------
    list of str : 生成したPDFファイルパス。
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)
    comp = set(components)

    # %%%%%% モデルデータ読み込み %%%%%%
    node = mgtopen_node(mgt_path)
    element = mgtopen_element(mgt_path)
    plate = mgtopen_plate(mgt_path)
    axis_name, axis_element, axis_node = mgtopen_group(mgt_path)
    wall_element, wall_base = mgtopen_wall(mgt_path, node)

    # %%%%%% 断面力読み込み %%%%%%
    beam_stress = read_beam_stress(beam_stress_path)
    truss_stress = read_truss_stress(truss_stress_path) \
        if truss_stress_path else np.array([])
    plate_stress = read_plate_stress(plate_stress_path) \
        if plate_stress_path else np.array([])

    wall_stress = np.array([])
    wall_stress_ID = np.array([])
    if np.size(wall_element) > 0 and wall_stress_path:
        raw = get_wstress(wall_stress_path)
        # 2行/1データ: ヘッダID(3列)をwall_stress_IDへ、応力6列を2行に整形
        n_pair = raw.shape[0] // 2
        wall_stress_ID = np.zeros((n_pair, 3))
        ws = np.zeros((2 * n_pair, 6))
        for i in range(n_pair):
            wall_stress_ID[i, :] = raw[2 * i, 0:3]
            ws[2 * i, :] = raw[2 * i, 3:9]
            ws[2 * i + 1, :] = raw[2 * i + 1, 0:6]
        wall_stress = ws

    # %%%%%% 荷重ケースの区切り検出 (doublecheck + find_index) %%%%%%
    if np.size(beam_stress) > 0:
        load_case_no_beam, lci_beam = _load_case_split(beam_stress[:, 1])
    else:
        load_case_no_beam, lci_beam = np.array([]), np.array([], dtype=int)
    if np.size(truss_stress) > 0:
        load_case_no_truss, lci_truss = _load_case_split(truss_stress[:, 1])
    else:
        load_case_no_truss, lci_truss = np.array([]), np.array([], dtype=int)
    if np.size(plate_stress) > 0:
        load_case_no_plate, lci_plate = _load_case_split(plate_stress[:, 1])
    else:
        load_case_no_plate, lci_plate = np.array([]), np.array([], dtype=int)
    if np.size(wall_stress) > 0:
        load_case_no_wall, lci_wall = _load_case_split(wall_stress_ID[:, 2])
    else:
        load_case_no_wall, lci_wall = np.array([]), np.array([], dtype=int)

    num_load_case = max(load_case_no_beam.size, load_case_no_truss.size,
                        load_case_no_plate.size, load_case_no_wall.size)
    load_case_no = doublecheck(np.concatenate(
        [load_case_no_beam, load_case_no_truss,
         load_case_no_plate, load_case_no_wall]))

    # %%%%%% 反力データ読み込み %%%%%%
    reaction = read_reaction(reaction_path) if reaction_path else np.array([])
    if np.size(reaction) > 0:
        load_case_no_reaction, lci_reaction = _load_case_split(reaction[:, 1])
    else:
        load_case_no_reaction = np.array([])
        lci_reaction = np.array([], dtype=int)
    if num_load_case != load_case_no_reaction.size and reaction_path:
        print('反力の荷重ケース数と応力の荷重ケース数が異なります．')

    # 荷重ケース名 (l_case_ratio_analysis.m 相当)
    if load_case_names is None:
        case_names = _default_case_names(load_case_no)
    elif isinstance(load_case_names, dict):
        case_names = [load_case_names.get(int(c), 'CASE' + str(int(c)))
                      for c in load_case_no]
    else:
        case_names = list(load_case_names)
        if len(case_names) != load_case_no.size:
            raise ValueError('load_case_namesの個数(%d)が荷重ケース数(%d)と'
                             '一致しません' % (len(case_names),
                                          load_case_no.size))

    # データ不整合チェック (要件: 吸収せず報告)
    _check_id_consistency(element, beam_stress, truss_stress)

    # %%%%%% 通り芯情報 %%%%%%
    v_idx = _resolve_select(axis_name, axes_select)
    if axes_select is None:
        v_idx = [i for i in v_idx if np.size(axis_node[i]) > 0]
    if not v_idx:
        print('WARNING: 描画対象の構面がありません')
        return []

    # 通り芯符号の表示対象 (symbols_select) を axes_select と切り離す。
    # u_idx = 描画構面(先頭) + 符号のみの構面(後続)。None=従来動作。
    if symbols_select is None:
        u_idx = list(v_idx)
    else:
        s_idx = _resolve_select(axis_name, symbols_select)
        s_idx = [i for i in s_idx if np.size(axis_node[i]) > 0]
        u_idx = list(v_idx) + [i for i in s_idx if i not in v_idx]

    axis_name_sel = [axis_name[i] for i in u_idx]
    axis_element_sel = [np.atleast_1d(
        np.asarray(axis_element[i], dtype=float)).ravel() for i in u_idx]
    axis_node_sel = [np.atleast_1d(
        np.asarray(axis_node[i], dtype=float)).ravel() for i in u_idx]

    (axis_plot_coefi, axis_direction, pick_axis_node_no,
     _axtick, _aytick, h_axis_plot) = _axis_coefficients(
        node, axis_node_sel, axis_name_sel,
        cross_axis=cross_axis, cross_height=cross_height, judge_XY=1)

    # 符号のみの構面を追加した場合、高さ寸法チェーンの既定レベルは
    # 従来どおり「描画構面上の節点Z」に限定する
    if len(u_idx) > len(v_idx):
        h2 = np.array([])
        for i in range(len(v_idx)):
            on = np.atleast_1d(find_index(node[:, 0], axis_node_sel[i]))
            on = on[on >= 0]
            h2 = doublecheck(np.concatenate([h2, node[on, 3]]))
        h_axis_plot = h2

    if heights_select is not None:
        h_axis_plot = np.sort(np.asarray(heights_select, dtype=float).ravel())

    # %%%%%% 分割部材の一括表示オプション %%%%%%
    unit_element, uniele_ID = _unit_elements(mgt_path, element, node,
                                             limit_sec_no)

    # %%%%%% 荷重ケースの選択 %%%%%%
    if load_cases is None:
        sel_cases = list(load_case_no)
    else:
        sel_cases = []
        for c in load_cases:
            if find_index(load_case_no, float(c)) < 0:
                raise KeyError('荷重ケース %s が応力データにありません '
                               '(存在するケース: %s)'
                               % (c, ', '.join(str(int(v))
                                               for v in load_case_no)))
            sel_cases.append(float(c))

    C = dict(node=node, element=element, plate=plate,
             wall_base=wall_base, wall_element=wall_element,
             beam_stress=beam_stress, truss_stress=truss_stress,
             plate_stress=plate_stress, wall_stress=wall_stress,
             wall_stress_ID=wall_stress_ID, reaction=reaction,
             lci_beam=lci_beam, lci_truss=lci_truss, lci_plate=lci_plate,
             lci_wall=lci_wall, lci_reaction=lci_reaction,
             load_case_no_wall=load_case_no_wall,
             unit_element=unit_element, uniele_ID=uniele_ID,
             components=comp, select13=tuple(select13),
             select_unit=select_unit, mini_length=mini_length,
             limit_sec_no=limit_sec_no, mscale=mscale, dot=tuple(dot),
             fontsize=tuple(fontsize), mergins=tuple(mergins),
             axisname_location=axisname_location,
             line_location=line_location, reactiondis=reactiondis,
             paper_size=paper_size,
             axis_name_sel=axis_name_sel,
             axis_element_sel=axis_element_sel,
             axis_node_sel=axis_node_sel,
             axis_plot_coefi=axis_plot_coefi,
             axis_direction=axis_direction,
             pick_axis_node_no=pick_axis_node_no,
             h_axis_plot=h_axis_plot)

    made = []
    fig_no = 100
    for case in sel_cases:
        i_load = int(find_index(load_case_no, case))
        case_name = case_names[i_load]
        # 荷重ケース番号<10:鉛直荷重時 (トラスはtruss_Lplotに従う)
        # >=10:水平荷重時 (トラスは常に描く) -- MATLAB版の case_L/case_S 相当
        if np.size(load_case_no_beam) > 0 and \
                i_load < load_case_no_beam.size:
            title_case_no = float(load_case_no_beam[i_load])
        else:
            title_case_no = float(case)
        is_vertical = title_case_no < 10
        t_plot = bool(truss_Lplot) if is_vertical else True
        if np.size(truss_stress) == 0:
            t_plot = False

        for i_axis in range(len(v_idx)):
            if axis_plot_coefi[i_axis, 0] == 0:
                print('WARNING: 構面 %s は通り芯を近似できないため'
                      'スキップします' % axis_name_sel[i_axis])
                continue
            out = os.path.join(
                out_dir, 'stress_%d_%s_%s.pdf'
                % (fig_no, _safe_name(case_name),
                   _safe_name(axis_name_sel[i_axis])))
            made.append(_figure_stress_axis(
                C, i_axis, i_load, title_case_no, case_name, t_plot, out))
            fig_no += 1

    return made


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv):
    import argparse
    p = argparse.ArgumentParser(
        description='MIDAS mgt+応力txtから法定応力図PDFを作成する')
    p.add_argument('mgt')
    p.add_argument('beam_stress')
    p.add_argument('out_dir')
    p.add_argument('--truss-stress', default=None)
    p.add_argument('--plate-stress', default=None)
    p.add_argument('--wall-stress', default=None)
    p.add_argument('--reaction', default=None)
    p.add_argument('--axes', nargs='*', default=None,
                   help='鉛直構面グループ名 (省略時: 全構面)')
    p.add_argument('--cases', nargs='*', type=int, default=None,
                   help='荷重ケース番号 (省略時: 全ケース)')
    p.add_argument('--case-names', nargs='*', default=None)
    p.add_argument('--components', default='NMQ',
                   help="描画成分 (例: 'NMQ', 'M')")
    p.add_argument('--mscale', type=float, default=0.0005)
    p.add_argument('--limit-sec-no', type=int, default=9000)
    p.add_argument('--truss-lplot', action='store_true',
                   help='鉛直荷重ケースでもブレース応力を描く')
    p.add_argument('--unit', action='store_true',
                   help='分割部材を結合して一括表示 (select_unit=0)')
    p.add_argument('--max-only', action='store_true',
                   help='柱梁とも最大値のみ表示 (select13=[0 0])')
    p.add_argument('--paper-size', type=int, default=3,
                   help='1=A1 2=A2 3=A3 4=A4')
    a = p.parse_args(argv)
    paths = plot_stress(
        a.mgt, a.beam_stress, a.out_dir,
        truss_stress_path=a.truss_stress, plate_stress_path=a.plate_stress,
        wall_stress_path=a.wall_stress, reaction_path=a.reaction,
        axes_select=a.axes, load_cases=a.cases, load_case_names=a.case_names,
        components=tuple(a.components),
        truss_Lplot=a.truss_lplot,
        mscale=a.mscale, limit_sec_no=a.limit_sec_no,
        select_unit=(0 if a.unit else math.inf),
        select13=((0, 0) if a.max_only else (1, 1)),
        paper_size=a.paper_size)
    for pth in paths:
        print(pth)
    print('%d files generated' % len(paths))


if __name__ == '__main__':
    _main(sys.argv[1:])
