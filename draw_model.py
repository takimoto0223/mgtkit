# -*- coding: utf-8 -*-
"""MIDASモデル構成図描画 (Midas_model.m / MIDAS_model_plot_fig.m の移植).

MATLAB GUIツール Midas_model (privatetool_function/MIDAS/model/) を
Python/matplotlib に移植したもの。GUIダイアログ(uigetfile/listdlg)は
すべて plot_model() の引数に置き換えている。

移植元:
  MIDAS_model_plot_fig.m   : 本体 (通り芯係数計算・図種ループ)
  figure_node.m            : 鉛直構面・節点ID図
  figure_element_ID.m      : 鉛直構面・要素ID図
  figure_material_ID.m     : 鉛直構面・使用材料図
  figure_section_ID.m      : 鉛直構面・断面符号図
  figure_end.m             : 鉛直構面・要素境界条件図
  figure_*_floor.m         : 水平構面(伏図)の各図
  chg_CONST.m / chg_CONST_ele.m / plot_plan_axis.m / tick.m / tick_xy.m
  line_projection.m / column_beam_judge.m / plate_plot.m / unit_add_ysub.m

デフォルト値は Midas_model.m の OpeningFcn の初期値
(line_location=2.0, axisname_location=3.0, mergins=[5 2 5 5],
 fontsize=[5 6 8], dummy_no=9000, cross_axis=全節点, judge_XY=var判別)。

MATLAB版との意図的差異:
  - print_format の分岐は PDF 保存に一本化 (要件による)。
  - 用紙サイズのデフォルトは A3 (GUI初期値はA4だが要件によりA3)。
    横長/縦長は MATLAB 同様に図の縦横比から自動決定。
  - 交差通り芯符号は丸囲み(○付き)で描画 (要件による。MATLAB版は無枠テキスト)。
  - グループの節点リストが空/1点のみで通り芯の一次近似ができない場合、
    MATLAB版は stop (エラー) するが、本移植では警告を出してスキップする。
  - 伏図の壁要素注記で MATLAB 版は wallelement_plot に axis=2 (スカラ) を
    渡しており実行するとエラーになるため、[2 3] (X,Y) に修正した。
  - 図形ラインのみの要素(type 0)が要素テーブルに存在しない場合
    (弾性連結など) は MATLAB 版はエラーになるが、警告してスキップする。
"""

import os
import re
import sys
import math

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

try:
    from .mgt import (
        mgtopen_node, mgtopen_element, mgtopen_plate, mgtopen_beam,
        mgtopen_truss, mgtopen_material, mgtopen_material_name,
        mgtopen_thickness, mgtopen_group, mgtopen_elasticlink,
        mgtopen_frm_rls, mgtopen_constraint, mgtopen_unit_2015,
        mgtopen_wall, node_index_get,
    )
    from .section import mgtopen_section
    from .util import (
        find_index, find_zero_index, doublecheck, doublecheck2, space_erace,
    )
except ImportError:  # スクリプト実行時
    from mgt import (
        mgtopen_node, mgtopen_element, mgtopen_plate, mgtopen_beam,
        mgtopen_truss, mgtopen_material, mgtopen_material_name,
        mgtopen_thickness, mgtopen_group, mgtopen_elasticlink,
        mgtopen_frm_rls, mgtopen_constraint, mgtopen_unit_2015,
        mgtopen_wall, node_index_get,
    )
    from section import mgtopen_section
    from util import (
        find_index, find_zero_index, doublecheck, doublecheck2, space_erace,
    )


# ---------------------------------------------------------------------------
# 日本語フォント設定 (Noto Sans CJK JP)
# ---------------------------------------------------------------------------

_FONT_DONE = False

_FONT_CANDIDATE_PATHS = [
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    'C:/Windows/Fonts/NotoSansCJKjp-Regular.otf',
    'C:/Windows/Fonts/YuGothM.ttc',
    'C:/Windows/Fonts/msgothic.ttc',
]


def _setup_japanese_font():
    """matplotlibにNoto Sans CJK JP(無ければ代替CJKフォント)を設定する."""
    global _FONT_DONE
    if _FONT_DONE:
        return
    names = {f.name for f in font_manager.fontManager.ttflist}
    if 'Noto Sans CJK JP' not in names:
        for p in _FONT_CANDIDATE_PATHS:
            if os.path.exists(p):
                try:
                    font_manager.fontManager.addfont(p)
                except Exception:
                    continue
        names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ('Noto Sans CJK JP', 'Noto Sans JP', 'Yu Gothic', 'MS Gothic'):
        if cand in names:
            matplotlib.rcParams['font.family'] = [cand]
            break
    else:
        print('WARNING: 日本語フォント(Noto Sans CJK JP)が見つかりません')
    matplotlib.rcParams['axes.unicode_minus'] = False
    matplotlib.rcParams['pdf.fonttype'] = 42
    _FONT_DONE = True


# ---------------------------------------------------------------------------
# 用紙設定 (figure_*.m 末尾の set(gcf, 'Paper...') 相当)
# ---------------------------------------------------------------------------

# paper_size: 1=A1, 2=A2, 3=A3, 4=A4 ; (横長時の幅, 高さ) [inch]
_PAPER_LAND = {1: (33.14, 23.40), 2: (23.40, 16.55),
               3: (16.53, 11.69), 4: (11.69, 8.27)}


def _finish_figure(fig, ax, title, xlabel_str, xlim, ylim,
                   title_fontsize, paper_size):
    """軸範囲・タイトル・用紙サイズを設定する (figure_*.m 末尾共通処理)."""
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.set_title(title, fontsize=title_fontsize)
    ax.set_xlabel(xlabel_str, fontsize=title_fontsize)
    # MATLAB: 図の縦横比で landscape/portrait を自動決定
    if (xlim[1] - xlim[0]) > (ylim[1] - ylim[0]):
        w, h = _PAPER_LAND[paper_size]
    else:
        h, w = _PAPER_LAND[paper_size]
    fig.set_size_inches(w, h)
    ax.set_aspect('equal', adjustable='box')  # daspect([1 1 1])
    ax.set_position([0.04, 0.06, 0.92, 0.87])
    for s in ax.spines.values():              # box on
        s.set_linewidth(0.5)


# ---------------------------------------------------------------------------
# 小物関数 (逐語移植)
# ---------------------------------------------------------------------------

def chg_CONST(const):
    """chg_CONST.m: 支持条件の6桁数値を文字列(Fx..Rz)へ変換."""
    STR = ''
    const = float(const)
    for val, name in ((100000, 'Fx'), (10000, 'Fy'), (1000, 'Fz'),
                      (100, 'Rx'), (10, 'Ry'), (1, 'Rz')):
        if const >= val:
            STR += name
            const -= val
    return STR


def chg_CONST_ele(const):
    """chg_CONST_ele.m: 端部解放の6桁数値を文字列(Fx..Mz)へ変換."""
    STR = ''
    const = float(const)
    for val, name in ((100000, 'Fx'), (10000, 'Fy'), (1000, 'Fz'),
                      (100, 'Mx'), (10, 'My'), (1, 'Mz')):
        if const >= val:
            STR += name
            const -= val
    return STR


def line_projection(node_xy, line_vec):
    """line_projection.m: XY座標を単位ベクトル方向へ投影した座標値."""
    node_xy = np.atleast_2d(np.asarray(node_xy, dtype=float))
    return node_xy[:, 0] * line_vec[0] + node_xy[:, 1] * line_vec[1]


def column_beam_judge_one(element_index, element, node):
    """column_beam_judge.m (単一要素): 柱2 / 梁1 / 斜め材1.9 を返す."""
    e_no = element[int(element_index), 0]
    ni, nj = node_index_get(e_no, element, node)
    XY = math.hypot(node[ni, 1] - node[nj, 1], node[ni, 2] - node[nj, 2])
    Z = abs(node[ni, 3] - node[nj, 3])
    if XY > Z:
        return 1.0
    elif XY < Z / 100.0:
        return 2.0
    else:
        return 1.9


def _tick(node, base_node_no, col):
    """tick.m: 節点番号の集合から座標(col: 1=X,2=Y,3=Z)を重複なく抽出."""
    vals = []
    for nn in np.atleast_1d(np.asarray(base_node_no, dtype=float)).ravel():
        idx = int(np.argmin(np.abs(node[:, 0] - nn)))
        vals.append(node[idx, col])
    return doublecheck(np.asarray(vals, dtype=float))


def _tick_xy(node, base_node):
    """tick_xy.m: 節点番号の集合から(X,Y)を重複なく抽出."""
    rows = []
    for nn in np.atleast_1d(np.asarray(base_node, dtype=float)).ravel():
        idx = find_index(node[:, 0], nn)
        rows.append([node[idx, 1], node[idx, 2]])
    rows = np.asarray(rows, dtype=float)
    if rows.shape[0] > 1:
        rows = doublecheck2(rows)
    return rows


def _var(a):
    """MATLAB var (標本分散)。要素1個以下なら0."""
    a = np.asarray(a, dtype=float).ravel()
    if a.size < 2:
        return 0.0
    return float(np.var(a, ddof=1))


def _num(v):
    """num2str(x,'%15.0f') 相当."""
    return str(int(round(float(v))))


def _pick_before_underscore(name):
    """断面名/材料名の '_' より前を取り出す (figure_*_ID.m の共通処理)."""
    if not isinstance(name, str):
        return str(name)
    cut = name.find('_')
    return name if cut < 0 else name[:cut]


def _members(table, values):
    """find_index(table(:,1), values) で見つかった要素の位置(0-based)を返す.

    MATLAB: idx = find_index(...); find(idx) 相当。
    戻り値: values 内で見つかった位置の配列。
    """
    if table is None or np.size(table) == 0 or np.size(values) == 0:
        return np.array([], dtype=int)
    idx = np.atleast_1d(find_index(np.asarray(table)[:, 0], values))
    return np.where(idx >= 0)[0]


def _classify_axis_elements(axis_elems, element, beam, truss, plate, wall_base):
    """通り芯上要素の種別付け (figure_*.m 冒頭共通処理).

    戻り値: (etype, on_axis_element_index, on_axis_wall_ID)
      etype: 0=その他(線のみ), 1=梁, 2=壁, 3=トラス, 4=板
    """
    n = axis_elems.size
    etype = np.zeros(n, dtype=int)
    # MATLABと同順で上書き: truss=3 -> beam=1 -> wall=2 -> plate=4
    etype[_members(truss, axis_elems)] = 3
    etype[_members(beam, axis_elems)] = 1
    wall_pos = _members(wall_base, axis_elems)
    etype[wall_pos] = 2
    etype[_members(plate, axis_elems)] = 4
    if np.size(element) > 0:
        on_axis_element_index = np.atleast_1d(
            find_index(element[:, 0], axis_elems))
    else:
        on_axis_element_index = np.full(n, -1, dtype=int)
    on_axis_wall_ID = axis_elems[wall_pos]
    return etype, on_axis_element_index, on_axis_wall_ID


def _project_nodes(node, direction):
    """節点XYを構面方向へ投影 (figure_*.m 冒頭).

    戻り値: [節点番号, 面内座標s, 面外座標t, Z]
    """
    node2 = np.zeros((node.shape[0], 2))
    node2[:, 0] = line_projection(node[:, 1:3], direction)
    node2[:, 1] = line_projection(node[:, 1:3],
                                  [-direction[1], direction[0]])
    return np.column_stack([node[:, 0], node2, node[:, 3]])


# ---------------------------------------------------------------------------
# 描画プリミティブ
# ---------------------------------------------------------------------------

_VA_MAP = {'middle': 'center', 'cap': 'top', 'bottom': 'bottom',
           'top': 'top', 'baseline': 'baseline', 'center': 'center'}


def _text(ax, x, y, s, ha='center', va='middle', fs=5.0, rot=0.0,
          boxed=False, circled=False):
    kw = dict(ha=ha, va=_VA_MAP.get(va, va), fontsize=fs, rotation=rot,
              rotation_mode='anchor', color='k', zorder=5)
    if circled:
        kw['bbox'] = dict(boxstyle='circle,pad=0.35', facecolor='white',
                          edgecolor='black', linewidth=0.5)
    elif boxed:
        kw['bbox'] = dict(boxstyle='square,pad=0.2', facecolor='white',
                          edgecolor='black', linewidth=0.3)
    ax.text(float(x), float(y), s, **kw)


def _kline(ax, xs, ys, **kw):
    kw.setdefault('color', 'k')
    kw.setdefault('linewidth', 0.5)
    kw.setdefault('linestyle', '-')
    ax.plot(xs, ys, **kw)


def _plate_plot(ax, plate_no, plate, nodeM, x, y):
    """plate_plot.m: 板(壁)要素の外形線を85%縮小して描画.

    x, y: nodeM(:,2:4) 内の1-based列番号 (1=第2列, 2=第3列, 3=第4列)。
    """
    ridx = find_index(plate[:, 0], plate_no)
    node4_no = list(plate[ridx, 3:7])
    if np.prod(node4_no) == 0:
        node4_no = node4_no[:3]
    node4_index = [find_index(nodeM[:, 0], nn) for nn in node4_no]
    coords = nodeM[node4_index, 1:4]
    center = coords.sum(axis=0) / len(node4_no)
    node4_data = (coords - center) * 0.85 + center
    node4_data = np.vstack([node4_data, node4_data[0]])
    for i in range(node4_data.shape[0] - 1):
        _kline(ax, [node4_data[i, x - 1], node4_data[i + 1, x - 1]],
               [node4_data[i, y - 1], node4_data[i + 1, y - 1]])


def _wallelement_plot(ax, wall_row, nodeM, cols, fs, text_lines, boxed):
    """wallelement_plot (figure_*_ID.m サブ関数): 壁の外形+中心線+注記.

    cols: nodeM の0-based列 (面内座標, 高さ/Y)。
    """
    a, b = cols
    Node = wall_row[3:7]
    ni = [find_index(nodeM[:, 0], nn) for nn in Node]
    xs = [nodeM[ni[0], a], nodeM[ni[1], a], nodeM[ni[2], a],
          nodeM[ni[3], a], nodeM[ni[0], a]]
    ys = [nodeM[ni[0], b], nodeM[ni[1], b], nodeM[ni[2], b],
          nodeM[ni[3], b], nodeM[ni[0], b]]
    _kline(ax, xs, ys)
    _kline(ax, [nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5,
                nodeM[ni[2], a] * 0.5 + nodeM[ni[3], a] * 0.5],
           [nodeM[ni[0], b], nodeM[ni[3], b]], linestyle=':')
    _text(ax, nodeM[ni[0], a] * 0.5 + nodeM[ni[1], a] * 0.5,
          nodeM[ni[3], b] * 0.5 + nodeM[ni[0], b] * 0.5,
          '\n'.join(text_lines) if isinstance(text_lines, (list, tuple))
          else text_lines,
          ha='left', va='middle', fs=fs, boxed=boxed)


def _beam_text(ax, ni, nj, nodeM, cols, label, fs, boxed):
    """beam_element_plot: 部材中央に沿わせて注記."""
    a, b = cols
    if (nodeM[ni, a] - nodeM[nj, a]) == 0:
        rotd = 90.0
    else:
        rotd = math.degrees(math.atan(
            (nodeM[ni, b] - nodeM[nj, b]) / (nodeM[ni, a] - nodeM[nj, a])))
    _text(ax, nodeM[ni, a] * 0.5 + nodeM[nj, a] * 0.5,
          nodeM[ni, b] * 0.5 + nodeM[nj, b] * 0.5, label,
          ha='center', va='bottom', fs=fs - 1, rot=rotd, boxed=boxed)


def _column_text(ax, ni, nj, nodeM, cols, angle1, label, fs, boxed):
    """column_element_plot: 柱中央に注記 (傾斜柱は回転)."""
    a, b = cols
    if angle1 == 2:
        _text(ax, nodeM[ni, a] * 0.5 + nodeM[nj, a] * 0.5,
              nodeM[ni, b] * 0.5 + nodeM[nj, b] * 0.5, label,
              ha='left', va='middle', fs=fs - 1, boxed=boxed)
    elif angle1 == 1.9:
        if (nodeM[ni, a] - nodeM[nj, a]) == 0:
            rotd = 0.0
        else:
            rotd = math.degrees(math.atan(
                (nodeM[ni, b] - nodeM[nj, b]) /
                (nodeM[ni, a] - nodeM[nj, a])))
            rotd = rotd - 90 if rotd > 0 else rotd + 90
        _text(ax, nodeM[ni, a] * 0.5 + nodeM[nj, a] * 0.5,
              nodeM[ni, b] * 0.5 + nodeM[nj, b] * 0.5, label,
              ha='left', va='middle', fs=fs - 1, rot=rotd, boxed=boxed)


def _truss_text(ax, ni, nj, nodeM, cols, label, fs, boxed):
    """truss_element_plot: ブレースの向きに応じて注記位置を選ぶ."""
    a, b = cols
    xi, yi = nodeM[ni, a], nodeM[ni, b]
    xj, yj = nodeM[nj, a], nodeM[nj, b]
    XY = abs(xi - xj)
    Z = abs(yi - yj)
    if (xi - xj) == 0:
        rotd = 90.0
    else:
        rotd = math.degrees(math.atan((yi - yj) / (xi - xj)))
    if xi < xj:
        if yi < yj:  # 右上あがり
            x, y = xi * 0.3 + xj * 0.7, yi * 0.3 + yj * 0.7
            va = 'baseline' if XY > Z else 'cap'
        else:        # 左上あがり
            x, y = xi * 0.7 + xj * 0.3, yi * 0.7 + yj * 0.3
            va = 'baseline'
    else:
        if yj < yi:  # 右上あがり
            x, y = xj * 0.3 + xi * 0.7, yj * 0.3 + yi * 0.7
            va = 'baseline' if XY > Z else 'cap'
        else:        # 左上あがり
            x, y = xj * 0.7 + xi * 0.3, yj * 0.7 + yi * 0.3
            va = 'baseline'
    _text(ax, x, y, label, ha='center', va=va, fs=fs - 1, rot=rotd,
          boxed=boxed)


def _node_label(ax, nodeM, nidx, cols, ha, va, fs, node_finish):
    """figure_node*.m: 節点番号ラベル (重複はnode_finishで抑止)."""
    nn = nodeM[nidx, 0]
    if nn in node_finish:
        return
    _text(ax, nodeM[nidx, cols[0]], nodeM[nidx, cols[1]], _num(nn),
          ha=ha, va=va, fs=fs - 1, boxed=True)
    node_finish.add(nn)


# ---------------------------------------------------------------------------
# 注記ラベルの決定 (図種ごと)
# ---------------------------------------------------------------------------

def _section_label(C, sec_no):
    idx = find_index(C['section_no'], sec_no)
    if idx < 0:
        return ''
    return _pick_before_underscore(C['section_name'][idx])


def _material_label(C, mat_no):
    if np.size(C['material']) == 0:
        return ''
    idx = find_index(C['material'][:, 0], mat_no)
    if idx < 0 or idx >= len(C['material_name']):
        return ''
    return space_erace(_pick_before_underscore(C['material_name'][idx]))


def _joined_ele_no(ele_no):
    arr = np.atleast_1d(np.asarray(ele_no, dtype=float)).ravel()
    return '/'.join(_num(v) for v in arr)


# ---------------------------------------------------------------------------
# 鉛直構面図 (figure_node/element_ID/material_ID/section_ID/end)
# ---------------------------------------------------------------------------

_V_TITLES = {'node': '鉛直構面・節点ID図',
             'element': '鉛直構面・要素ID図',
             'material': '鉛直構面・使用材料図',
             'section': '鉛直構面・断面符号図',
             'end': '鉛直構面・要素境界条件図'}

_F_TITLES = {'node': '水平構面・節点番号図',
             'element': '水平構面・要素ID図',
             'material': '水平構面・使用材料図',
             'section': '水平構面・断面符号図',
             'end': '水平構面・要素境界条件図'}


def _member_label(kind, C, ele_no, sec_no, mat_no):
    """図種に応じた注記文字列と枠の有無を返す."""
    if kind == 'element':
        return _joined_ele_no(ele_no), True
    if kind == 'material':
        return _material_label(C, np.atleast_1d(mat_no).ravel()[0]), True
    if kind == 'section':
        return _section_label(C, np.atleast_1d(sec_no).ravel()[0]), False
    return None, False


def _figure_vertical(kind, C, i_axis, out_path):
    """鉛直構面の1図を描画してPDF保存する (figure_*.m 相当)."""
    node = C['node']
    element = C['element']
    beam, truss, plate = C['beam'], C['truss'], C['plate']
    wall_base = C['wall_base']
    unit_element = C['unit_element']
    uniele_ID = C['uniele_ID']
    dim_fs, axis_fs, title_fs = C['fontsize']
    mL, mR, mT, mB = C['mergins']
    limit_sec_no = math.inf if kind == 'node' else C['limit_sec_no']

    direction = C['axis_direction'][i_axis]
    nodeM = _project_nodes(node, direction)  # [no, s, t, Z]

    axis_elems = C['axis_element_sel'][i_axis]
    axis_nodes = C['axis_node_sel'][i_axis]
    judge_axis = C['axis_plot_coefi'][i_axis, 0]

    # 通り芯上要素の座標範囲 (MIDAS_model_plot_fig.m ループ内での再計算)
    on_idx_nodes = np.atleast_1d(find_index(node[:, 0], axis_nodes))
    on_idx_nodes = on_idx_nodes[on_idx_nodes >= 0]
    pl = node[on_idx_nodes, 1:4]
    axis_xtick, axis_ytick, axis_ztick = pl[:, 0], pl[:, 1], pl[:, 2]

    etype, on_ele_idx, wall_ID = _classify_axis_elements(
        axis_elems, element, beam, truss, plate, wall_base)

    fig = plt.figure()
    ax = fig.add_subplot(111)

    axis_ysubtick = []          # 柱端点の節点番号
    marked_nodes = []           # 'kx'を打つ節点番号 (element/material/section)
    node_finish = {0.0}         # figure_node の既ラベル節点

    # %%%%%% 通り上にある単一要素のプロット %%%%%%
    for i_e in range(axis_elems.size):
        et = etype[i_e]
        if et == 1:  # 梁要素
            row = on_ele_idx[i_e]
            sec_no = element[row, 2]
            mat_no = element[row, 1]
            ele_no = element[row, 0]
            ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            _kline(ax, [nodeM[ni, 1], nodeM[nj, 1]],
                   [nodeM[ni, 3], nodeM[nj, 3]])
            c_b = column_beam_judge_one(row, element, node)
            if sec_no >= limit_sec_no:
                pass  # ダミー材
            elif c_b in (2.0, 1.9):
                in_unit = (np.size(uniele_ID) > 0 and
                           find_index(uniele_ID, ele_no) >= 0)
                if not in_unit:
                    if kind == 'node':
                        _node_label(ax, nodeM, ni, (1, 3), 'right', 'bottom',
                                    dim_fs, node_finish)
                        _node_label(ax, nodeM, nj, (1, 3), 'left', 'top',
                                    dim_fs, node_finish)
                    elif kind in ('element', 'material', 'section'):
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        _column_text(ax, ni, nj, nodeM, (1, 3), c_b, label,
                                     dim_fs, boxed)
                    axis_ysubtick += [nodeM[ni, 0], nodeM[nj, 0]]
                elif kind == 'end':
                    pass
                if kind == 'end':
                    # figure_end は unit の有無に関わらず柱端点を集める
                    axis_ysubtick += [nodeM[ni, 0], nodeM[nj, 0]]
            elif c_b == 1.0:
                in_unit = (np.size(uniele_ID) > 0 and
                           find_index(uniele_ID, ele_no) >= 0)
                if not in_unit:
                    if kind == 'node':
                        _node_label(ax, nodeM, ni, (1, 3), 'left', 'bottom',
                                    dim_fs, node_finish)
                        _node_label(ax, nodeM, nj, (1, 3), 'right', 'bottom',
                                    dim_fs, node_finish)
                    elif kind in ('element', 'material', 'section'):
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        _beam_text(ax, ni, nj, nodeM, (1, 3), label,
                                   dim_fs, boxed)
            else:
                print('柱梁タイプが定義されていません')

        elif et == 3:  # トラス要素
            row = on_ele_idx[i_e]
            sec_no = element[row, 2]
            mat_no = element[row, 1]
            ele_no = element[row, 0]
            ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            if kind == 'node' and sec_no >= limit_sec_no:
                pass
            else:
                ax.plot([nodeM[ni, 1] * 0.85 + nodeM[nj, 1] * 0.15,
                         nodeM[ni, 1] * 0.15 + nodeM[nj, 1] * 0.85],
                        [nodeM[ni, 3] * 0.85 + nodeM[nj, 3] * 0.15,
                         nodeM[ni, 3] * 0.15 + nodeM[nj, 3] * 0.85],
                        'ko-', markersize=1.5, linewidth=0.2)
                if kind == 'node':
                    _node_label(ax, nodeM, ni, (1, 3), 'right', 'bottom',
                                dim_fs, node_finish)
                    _node_label(ax, nodeM, nj, (1, 3), 'left', 'top',
                                dim_fs, node_finish)
                elif kind in ('element', 'material', 'section'):
                    marked_nodes += [element[row, 3], element[row, 4]]
                    label, boxed = _member_label(kind, C, ele_no,
                                                 sec_no, mat_no)
                    _truss_text(ax, ni, nj, nodeM, (1, 3), label,
                                dim_fs, boxed)

        elif et == 4:  # 板要素
            plate_no = axis_elems[i_e]
            pridx = find_index(plate[:, 0], plate_no)
            node4_no = list(plate[pridx, 3:7])
            if np.prod(node4_no) == 0:
                node4_no = node4_no[:3]
            if kind == 'node':
                _plate_plot(ax, plate_no, plate, nodeM,
                            int(3 - judge_axis), 3)
            elif kind in ('element', 'material'):
                _plate_plot(ax, plate_no, plate, nodeM,
                            int(3 - judge_axis), 3)
            elif kind == 'section':
                _plate_plot(ax, plate_no, plate, nodeM, 1, 3)
            if kind in ('element', 'material', 'section'):
                marked_nodes += list(node4_no)
                node4_index = [find_index(nodeM[:, 0], nn) for nn in node4_no]
                center = nodeM[node4_index, 1:4].sum(axis=0) / len(node4_no)
                if kind == 'element':
                    lab = _num(plate[pridx, 0])
                elif kind == 'material':
                    lab = _material_label(C, plate[pridx, 1])
                else:
                    lab = '厚:' + _num(plate[pridx, 2])
                _text(ax, center[0], center[2], lab, ha='center', va='middle',
                      fs=dim_fs - 1, boxed=(kind != 'section'))

        elif et == 0:  # 図形ラインのみ
            try:
                ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            except IndexError:
                print('WARNING: 要素 %d が*ELEMENTに見つからないため'
                      'スキップします' % int(axis_elems[i_e]))
                continue
            _kline(ax, [nodeM[ni, 1], nodeM[nj, 1]],
                   [nodeM[ni, 3], nodeM[nj, 3]])

    # %%%%%% 結合部材のplot %%%%%%
    if kind in ('node', 'element', 'material', 'section'):
        for unit in unit_element:
            if axis_elems.size == 0:
                continue
            fidx = np.atleast_1d(find_index(axis_elems, unit[1]))
            found = fidx >= 0
            if np.all(found):
                ele_no = unit[1]
                row0 = find_index(element[:, 0], unit[1][0])
                sec_no = element[row0, 2]
                mat_no = element[row0, 1]
                ni = find_index(nodeM[:, 0], unit[2][0])
                nj = find_index(nodeM[:, 0], unit[2][-1])
                if kind == 'node':
                    _node_label(ax, nodeM, ni, (1, 3), 'left', 'bottom',
                                dim_fs, node_finish)
                    _node_label(ax, nodeM, nj, (1, 3), 'right', 'bottom',
                                dim_fs, node_finish)
                else:
                    # unit_element_plot
                    flags = (element[np.atleast_1d(
                        find_index(element[:, 0], unit[1])), 2]
                        < limit_sec_no).astype(float)
                    if flags.sum() > 0 and flags.prod() > 0:
                        c_b = column_beam_judge_one(row0, element, node)
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        if c_b in (2.0, 1.9):
                            _column_text(ax, ni, nj, nodeM, (1, 3), c_b,
                                         label, dim_fs, boxed)
                        elif c_b == 1.0:
                            _beam_text(ax, ni, nj, nodeM, (1, 3), label,
                                       dim_fs, boxed)
                        else:
                            print('柱梁タイプが定義されていません')
                # unit_add_ysub
                flags = (element[np.atleast_1d(
                    find_index(element[:, 0], unit[1])), 2]
                    < limit_sec_no).astype(float)
                if flags.sum() > 0 and flags.prod() > 0:
                    c_b = column_beam_judge_one(
                        find_index(element[:, 0], unit[1][0]), element, node)
                    if c_b in (2.0, 1.9):
                        axis_ysubtick += [unit[2][0], unit[2][-1]]
            elif not np.any(found):
                pass
            else:
                print('結合要素作成でエラー！？（構成要素の一部しか登録されていません）')

    # %%%%%% 壁要素のplot %%%%%%
    if wall_ID.size > 0:
        widx = np.atleast_1d(find_index(wall_base[:, 0], wall_ID))
        for wi in widx:
            wall_row = wall_base[wi, :]
            if kind in ('node', 'end'):
                _plate_plot(ax, wall_row[0], wall_base, nodeM,
                            int(3 - judge_axis), 3)
            elif kind == 'element':
                marked_nodes += list(wall_row[3:7])
                _wallelement_plot(ax, wall_row, nodeM, (1, 3), dim_fs,
                                  [_num(wall_row[0]),
                                   '壁ID' + _num(wall_row[8])], True)
            elif kind == 'material':
                marked_nodes += list(wall_row[3:7])
                _wallelement_plot(ax, wall_row, nodeM, (1, 3), dim_fs,
                                  _material_label(C, wall_row[1]), True)
            elif kind == 'section':
                marked_nodes += list(wall_row[3:7])
                thickness = C['thickness']
                if np.size(thickness) > 0:
                    tno = thickness[find_index(thickness[:, 0],
                                               wall_row[2]), 1]
                else:
                    tno = 0.0
                _wallelement_plot(ax, wall_row, nodeM, (1, 3), dim_fs,
                                  ['厚ID:' + _num(wall_row[2]),
                                   _num(tno) + 'mm'], False)

    # %%%%%% 節点のplot ('kx') %%%%%%
    if kind in ('element', 'material', 'section'):
        if marked_nodes:
            for nn in doublecheck(np.asarray(marked_nodes, dtype=float)):
                ii = find_index(nodeM[:, 0], nn)
                ax.plot(nodeM[ii, 1], nodeM[ii, 3], 'kx', markersize=2)
    elif kind == 'end':
        for nn in doublecheck(np.asarray(axis_nodes, dtype=float)):
            ii = find_index(nodeM[:, 0], nn)
            ax.plot(nodeM[ii, 1], nodeM[ii, 3], 'kx', markersize=2)

    # %%%%%% 要素端・支点 (figure_end のみ) %%%%%%
    if kind == 'end':
        frm_rls = C['frm_rls']
        constraint = C['constraint']
        if np.size(frm_rls) > 0 and axis_elems.size > 0:
            on_element = doublecheck(frm_rls[:, 0])
            ridx = np.atleast_1d(find_index(axis_elems, on_element))
            ridx = ridx[ridx >= 0]
            for r in ridx:
                ele_no = axis_elems[r]
                fr = find_index(frm_rls[:, 0], ele_no)
                CON1 = chg_CONST_ele(frm_rls[fr, 1])
                CON2 = chg_CONST_ele(frm_rls[fr + 1, 1]) \
                    if fr + 1 < frm_rls.shape[0] else ''
                ni, nj = node_index_get(ele_no, element, nodeM)
                if CON1:
                    ax.plot(nodeM[ni, 1] * 0.9 + 0.1 * nodeM[nj, 1],
                            nodeM[ni, 3] * 0.9 + 0.1 * nodeM[nj, 3],
                            'k.', markersize=5)
                if CON2:
                    ax.plot(nodeM[ni, 1] * 0.1 + 0.9 * nodeM[nj, 1],
                            nodeM[ni, 3] * 0.1 + 0.9 * nodeM[nj, 3],
                            'k.', markersize=5)
        if np.size(constraint) > 0 and np.size(axis_nodes) > 0:
            axis_nodes_v = np.atleast_1d(
                np.asarray(axis_nodes, dtype=float)).ravel()
            cidx = np.atleast_1d(find_index(axis_nodes_v, constraint[:, 0]))
            cidx = cidx[cidx >= 0]
            for c in cidx:
                nn = axis_nodes_v[c]
                CON = chg_CONST(constraint[find_index(constraint[:, 0], nn),
                                           1])
                ii = find_index(nodeM[:, 0], nn)
                ax.plot(nodeM[ii, 1], nodeM[ii, 3], marker='h', color='b',
                        markersize=3, linestyle='none')
                _text(ax, nodeM[ii, 1], nodeM[ii, 3] - 0.5, CON,
                      ha='center', va='top', fs=dim_fs - 1, rot=-45,
                      boxed=True)

    # %%%%%% 通り芯情報 (交差通り芯・寸法線) %%%%%%
    if axis_ysubtick:
        ysub = _tick(nodeM,
                     doublecheck(np.asarray(axis_ysubtick, dtype=float)), 3)
    else:
        ysub = np.array([])

    pick_axis_node_no = C['pick_axis_node_no']
    axis_plot_coefi = C['axis_plot_coefi']
    num_axis = axis_plot_coefi.shape[0]
    select_tick = []
    zmin = float(np.min(axis_ztick))
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
                      ha='center', va='middle', fs=axis_fs, circled=True)
                select_tick.append(list(pick_axis_xy))
                break

    # 図枠
    end_node = np.array([[np.min(axis_xtick), np.min(axis_ytick)],
                         [np.max(axis_xtick), np.max(axis_ytick)],
                         [np.min(axis_xtick), np.max(axis_ytick)],
                         [np.max(axis_xtick), np.min(axis_ytick)]])
    end_node = line_projection(end_node, direction)
    xlim = (float(np.min(end_node)) - mL, float(np.max(end_node)) + mR)
    ylim = (zmin - mB, float(np.max(axis_ztick)) + mT)

    # 柱間隔・階高寸法
    select_tick = np.asarray(select_tick, dtype=float)
    if select_tick.shape[0] >= 2:
        select_tick = select_tick[np.argsort(select_tick[:, 0])]
        ax.plot(select_tick[:, 0],
                np.full(select_tick.shape[0], zmin - C['line_location']),
                'kx', markersize=3, linestyle='none')

    if np.size(C['h_axis_plot']) > 0:
        ysub = np.asarray(C['h_axis_plot'], dtype=float).ravel()

    if ysub.size > 0:
        ax.plot(np.full(ysub.size, xlim[0] + mL - C['line_location']),
                ysub, 'kx', markersize=3, linestyle='none')

    line_y = zmin - C['line_location']
    for il in range(select_tick.shape[0] - 1):
        dd = np.linalg.norm(select_tick[il, 0:2] - select_tick[il + 1, 0:2])
        if abs(dd) > 0.1:
            _kline(ax, [select_tick[il, 0], select_tick[il + 1, 0]],
                   [line_y, line_y])
            _text(ax, select_tick[il, 0] * 0.5 + select_tick[il + 1, 0] * 0.5,
                  line_y, _num(round(dd * 1000)),
                  ha='center', va='baseline', fs=dim_fs)

    line_x = float(np.min(end_node)) - C['line_location']
    for il in range(ysub.size - 1):
        _kline(ax, [line_x, line_x], [ysub[il], ysub[il + 1]])
        _text(ax, line_x, ysub[il] * 0.5 + ysub[il + 1] * 0.5,
              _num(round(abs(ysub[il + 1] - ysub[il]) * 1000)),
              ha='center', va='baseline', fs=dim_fs, rot=90)

    _finish_figure(fig, ax, _V_TITLES[kind], C['axis_name_sel'][i_axis],
                   xlim, ylim, title_fs, C['paper_size'])
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# 水平構面(伏図) (figure_*_floor)
# ---------------------------------------------------------------------------

def _plot_plan_axis(ax, axis_xtick, axis_ytick, node, v_names, v_nodes,
                    v_coefi, mL, mB, fs):
    """plot_plan_axis.m: 伏図に通り芯符号を記入し、tickを更新して返す."""
    axis_xtick = list(np.asarray(axis_xtick, dtype=float).ravel())
    axis_ytick = list(np.asarray(axis_ytick, dtype=float).ravel())
    for i in range(len(v_names)):
        nn = np.atleast_1d(np.asarray(v_nodes[i], dtype=float)).ravel()
        if nn.size == 0:
            continue
        pick_id = np.atleast_1d(find_index(node[:, 0], nn))
        pick_id = pick_id[pick_id >= 0]
        if pick_id.size == 0:
            continue
        if v_coefi[i, 0] == 1:  # X通り (X一定, 平面上では縦線)
            B = int(np.argmin(node[pick_id, 2]))
            _text(ax, node[pick_id[B], 1], node[pick_id[B], 2] - mB / 2,
                  space_erace(v_names[i]), ha='center', va='middle', fs=fs,
                  boxed=True)
            if node[pick_id[B], 2] < min(axis_ytick):
                axis_ytick.append(node[pick_id[B], 2])
        else:  # Y通り
            B = int(np.argmin(node[pick_id, 1]))
            _text(ax, node[pick_id[B], 1] - mL / 2, node[pick_id[B], 2],
                  space_erace(v_names[i]), ha='center', va='middle', fs=fs,
                  boxed=True)
            if node[pick_id[B], 1] < min(axis_xtick):
                axis_xtick.append(node[pick_id[B], 1])
    return np.asarray(axis_xtick), np.asarray(axis_ytick)


def _figure_floor(kind, C, i_axis, out_path):
    """水平構面(伏図)の1図を描画してPDF保存する (figure_*_floor.m 相当)."""
    node = C['node']
    element = C['element']
    beam, truss, plate = C['beam'], C['truss'], C['plate']
    wall_base = C['wall_base']
    unit_element = C['unit_element']
    uniele_ID = C['uniele_ID']
    dim_fs, axis_fs, title_fs = C['fontsize']
    mL, mR, mT, mB = C['mergins']
    limit_sec_no = math.inf if kind == 'node' else C['limit_sec_no']

    axis_elems = C['floor_element_sel'][i_axis]
    axis_nodes = C['floor_node_sel'][i_axis]

    on_idx_nodes = np.atleast_1d(find_index(node[:, 0], axis_nodes))
    on_idx_nodes = on_idx_nodes[on_idx_nodes >= 0]
    pl = node[on_idx_nodes, 1:4]
    axis_xtick, axis_ytick = pl[:, 0], pl[:, 1]

    etype, on_ele_idx, wall_ID = _classify_axis_elements(
        axis_elems, element, beam, truss, plate, wall_base)

    # %%%%%% 関係のない通り芯グループの削除 %%%%%%
    v_names = list(C['v_axis_name'])
    v_elems = list(C['v_axis_element'])
    v_nodes = list(C['v_axis_node'])
    v_coefi = np.array(C['v_axis_plot_coefi'], dtype=float)

    found_rows = on_ele_idx[on_ele_idx >= 0]
    floor_ele_nos = element[found_rows, 0] if found_rows.size else []
    cut_index = np.zeros(len(v_elems))
    for e_no in np.atleast_1d(floor_ele_nos):
        for j in range(len(v_elems)):
            vj = np.atleast_1d(np.asarray(v_elems[j], dtype=float)).ravel()
            if vj.size > 0 and find_index(vj, e_no) >= 0:
                cut_index[j] += 1
    keep = np.where(cut_index)[0]
    v_names = [v_names[j] for j in keep]
    v_nodes = [v_nodes[j] for j in keep]
    v_coefi = v_coefi[keep, :] if keep.size else np.zeros((0, 3))
    # 各鉛直通りの節点を床面上の節点に限定
    fl_nodes = np.atleast_1d(np.asarray(axis_nodes, dtype=float)).ravel()
    for j in range(len(v_nodes)):
        vn = np.atleast_1d(np.asarray(v_nodes[j], dtype=float)).ravel()
        if vn.size and fl_nodes.size:
            sel = np.array([find_index(fl_nodes, x) >= 0 for x in vn])
            v_nodes[j] = vn[sel]
        else:
            v_nodes[j] = np.array([])

    fig = plt.figure()
    ax = fig.add_subplot(111)

    marked_nodes = []
    node_finish = {0.0}

    for i_e in range(axis_elems.size):
        et = etype[i_e]
        if et == 1:  # 梁要素
            row = on_ele_idx[i_e]
            sec_no = element[row, 2]
            mat_no = element[row, 1]
            ele_no = element[row, 0]
            ni, nj = node_index_get(axis_elems[i_e], element, node)
            _kline(ax, [node[ni, 1], node[nj, 1]],
                   [node[ni, 2], node[nj, 2]])
            c_b = column_beam_judge_one(row, element, node)
            if kind in ('element', 'material', 'section'):
                marked_nodes += [element[row, 3], element[row, 4]]
            if sec_no >= limit_sec_no:
                pass
            elif c_b == 1.9:
                in_unit = (np.size(uniele_ID) > 0 and
                           find_index(uniele_ID, ele_no) >= 0)
                if not in_unit:
                    if kind == 'node':
                        _node_label(ax, node, ni, (1, 2), 'right', 'bottom',
                                    dim_fs, node_finish)
                        _node_label(ax, node, nj, (1, 2), 'left', 'top',
                                    dim_fs, node_finish)
                    elif kind in ('element', 'material', 'section'):
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        _column_text(ax, ni, nj, node, (1, 2), c_b, label,
                                     dim_fs, boxed)
            elif c_b == 1.0:
                in_unit = (np.size(uniele_ID) > 0 and
                           find_index(uniele_ID, ele_no) >= 0)
                if not in_unit:
                    if kind == 'node':
                        _node_label(ax, node, ni, (1, 2), 'left', 'bottom',
                                    dim_fs, node_finish)
                        _node_label(ax, node, nj, (1, 2), 'right', 'bottom',
                                    dim_fs, node_finish)
                    elif kind in ('element', 'material', 'section'):
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        _beam_text(ax, ni, nj, node, (1, 2), label,
                                   dim_fs, boxed)
            else:
                pass  # 鉛直柱(c_b==2)は伏図では点になるため注記しない

        elif et == 3:  # トラス要素
            row = on_ele_idx[i_e]
            sec_no = element[row, 2]
            mat_no = element[row, 1]
            ele_no = element[row, 0]
            ni, nj = node_index_get(axis_elems[i_e], element, node)
            xs = [node[ni, 1] * 0.85 + node[nj, 1] * 0.15,
                  node[ni, 1] * 0.15 + node[nj, 1] * 0.85]
            ys = [node[ni, 2] * 0.85 + node[nj, 2] * 0.15,
                  node[ni, 2] * 0.15 + node[nj, 2] * 0.85]
            if kind != 'end' and sec_no >= limit_sec_no:
                ax.plot(xs, ys, 'ko:', markersize=1.5, linewidth=0.2)
            else:
                ax.plot(xs, ys, 'ko-', markersize=1.5, linewidth=0.2)
                if kind == 'node':
                    _node_label(ax, node, ni, (1, 2), 'right', 'bottom',
                                dim_fs, node_finish)
                    _node_label(ax, node, nj, (1, 2), 'left', 'top',
                                dim_fs, node_finish)
                elif kind in ('element', 'material', 'section'):
                    marked_nodes += [element[row, 3], element[row, 4]]
                    label, boxed = _member_label(kind, C, ele_no,
                                                 sec_no, mat_no)
                    _truss_text(ax, ni, nj, node, (1, 2), label,
                                dim_fs, boxed)

        elif et == 4:  # 板要素
            plate_no = axis_elems[i_e]
            pridx = find_index(plate[:, 0], plate_no)
            node4_no = list(plate[pridx, 3:7])
            if np.prod(node4_no) == 0:
                node4_no = node4_no[:3]
            if kind in ('node', 'element', 'material', 'section'):
                _plate_plot(ax, plate_no, plate, node, 1, 2)
            if kind in ('element', 'material', 'section'):
                marked_nodes += list(node4_no)
                node4_index = [find_index(node[:, 0], nn) for nn in node4_no]
                center = node[node4_index, 1:4].sum(axis=0) / len(node4_no)
                if kind == 'element':
                    lab = _num(plate[pridx, 0])
                elif kind == 'material':
                    lab = _material_label(C, plate[pridx, 1])
                else:
                    lab = '厚:' + _num(plate[pridx, 2])
                _text(ax, center[0], center[1], lab, ha='center', va='middle',
                      fs=dim_fs - 1, boxed=True)

        elif et == 0:
            try:
                ni, nj = node_index_get(axis_elems[i_e], element, node)
            except IndexError:
                print('WARNING: 要素 %d が*ELEMENTに見つからないため'
                      'スキップします' % int(axis_elems[i_e]))
                continue
            _kline(ax, [node[ni, 1], node[nj, 1]],
                   [node[ni, 2], node[nj, 2]])

    # %%%%%% 結合部材のplot (end_floorはMATLAB版でもコメントアウト) %%%%%%
    if kind in ('node', 'element', 'material', 'section'):
        for unit in unit_element:
            if axis_elems.size == 0:
                continue
            fidx = np.atleast_1d(find_index(axis_elems, unit[1]))
            found = fidx >= 0
            if np.all(found):
                ele_no = unit[1]
                row0 = find_index(element[:, 0], unit[1][0])
                sec_no = element[row0, 2]
                mat_no = element[row0, 1]
                ni = find_index(node[:, 0], unit[2][0])
                nj = find_index(node[:, 0], unit[2][-1])
                if kind == 'node':
                    _node_label(ax, node, ni, (1, 2), 'left', 'bottom',
                                dim_fs, node_finish)
                    _node_label(ax, node, nj, (1, 2), 'right', 'bottom',
                                dim_fs, node_finish)
                else:
                    flags = (element[np.atleast_1d(
                        find_index(element[:, 0], unit[1])), 2]
                        < limit_sec_no).astype(float)
                    if flags.sum() > 0 and flags.prod() > 0:
                        c_b = column_beam_judge_one(row0, element, node)
                        label, boxed = _member_label(kind, C, ele_no,
                                                     sec_no, mat_no)
                        if c_b in (2.0, 1.9):
                            _column_text(ax, ni, nj, node, (1, 2), c_b,
                                         label, dim_fs, boxed)
                        elif c_b == 1.0:
                            _beam_text(ax, ni, nj, node, (1, 2), label,
                                       dim_fs, boxed)
            elif not np.any(found):
                pass
            else:
                print('結合要素作成でエラー！？（構成要素の一部しか登録されていません）')

    # %%%%%% 壁要素のplot %%%%%%
    if wall_ID.size > 0 and kind in ('element', 'material', 'section'):
        widx = np.atleast_1d(find_index(wall_base[:, 0], wall_ID))
        for wi in widx:
            wall_row = wall_base[wi, :]
            marked_nodes += list(wall_row[3:7])
            if kind == 'element':
                _wallelement_plot(ax, wall_row, node, (1, 2), dim_fs,
                                  [_num(wall_row[0]),
                                   '壁ID' + _num(wall_row[8])], True)
            elif kind == 'material':
                _wallelement_plot(ax, wall_row, node, (1, 2), dim_fs,
                                  _material_label(C, wall_row[1]), True)
            else:
                thickness = C['thickness']
                if np.size(thickness) > 0:
                    tno = thickness[find_index(thickness[:, 0],
                                               wall_row[2]), 1]
                else:
                    tno = 0.0
                _wallelement_plot(ax, wall_row, node, (1, 2), dim_fs,
                                  ['厚ID:' + _num(wall_row[2]),
                                   _num(tno) + 'mm'], False)

    # %%%%%% 要素端・支点 (end_floor のみ) %%%%%%
    if kind == 'end':
        frm_rls = C['frm_rls']
        constraint = C['constraint']
        if np.size(frm_rls) > 0 and axis_elems.size > 0:
            on_element = doublecheck(frm_rls[:, 0])
            ridx = np.atleast_1d(find_index(axis_elems, on_element))
            ridx = ridx[ridx >= 0]
            for r in ridx:
                ele_no = axis_elems[r]
                fr = find_index(frm_rls[:, 0], ele_no)
                CON1 = chg_CONST_ele(frm_rls[fr, 1])
                CON2 = chg_CONST_ele(frm_rls[fr + 1, 1]) \
                    if fr + 1 < frm_rls.shape[0] else ''
                ni, nj = node_index_get(ele_no, element, node)
                if CON1:
                    ax.plot(node[ni, 1] * 0.9 + 0.1 * node[nj, 1],
                            node[ni, 2] * 0.9 + 0.1 * node[nj, 2],
                            'k.', markersize=5)
                if CON2:
                    ax.plot(node[ni, 1] * 0.1 + 0.9 * node[nj, 1],
                            node[ni, 2] * 0.1 + 0.9 * node[nj, 2],
                            'k.', markersize=5)
        if np.size(constraint) > 0 and fl_nodes.size > 0:
            cidx = np.atleast_1d(find_index(fl_nodes, constraint[:, 0]))
            cidx = cidx[cidx >= 0]
            for c in cidx:
                nn = fl_nodes[c]
                CON = chg_CONST(constraint[find_index(constraint[:, 0], nn),
                                           1])
                ii = find_index(node[:, 0], nn)
                ax.plot(node[ii, 1], node[ii, 2], marker='h', color='b',
                        markersize=3, linestyle='none')
                _text(ax, node[ii, 1], node[ii, 2], CON, ha='left', va='top',
                      fs=dim_fs - 1, rot=-45, boxed=True)

    # %%%%%% 通り芯符号 %%%%%%
    axis_xtick, axis_ytick = _plot_plan_axis(
        ax, axis_xtick, axis_ytick, node, v_names, v_nodes, v_coefi,
        mL, mB, axis_fs)

    xlim = (float(np.min(axis_xtick)) - mL, float(np.max(axis_xtick)) + mR)
    ylim = (float(np.min(axis_ytick)) - mB, float(np.max(axis_ytick)) + mT)

    _finish_figure(fig, ax, _F_TITLES[kind], C['floor_name_sel'][i_axis],
                   xlim, ylim, title_fs, C['paper_size'])
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# 通り芯の一次近似 (MIDAS_model_plot_fig.m 137-241行 相当)
# ---------------------------------------------------------------------------

def _axis_coefficients(node, axis_node_sel, axis_names, cross_axis=1,
                       cross_height=None, judge_XY=1, with_direction=True):
    """選択した各構面の一次近似係数・方向ベクトル・構成節点を求める.

    戻り値: (axis_plot_coefi, axis_direction, pick_axis_node_no,
             axis_xtick, axis_ytick, h_axis_plot)
    """
    num_axis = len(axis_node_sel)
    axis_plot_coefi = np.zeros((num_axis, 3))
    axis_direction = np.zeros((num_axis, 2))
    pick_axis_node_no = []
    axis_xtick, axis_ytick = [], []
    h_axis_plot = np.array([])

    for i_axis in range(num_axis):
        nn = np.atleast_1d(np.asarray(axis_node_sel[i_axis],
                                      dtype=float)).ravel()
        if nn.size == 0:
            pick_axis_node_no.append(np.array([]))
            print('WARNING: 構面 %s のグループに節点が登録されていません'
                  '(mgtの*GROUP記述を確認してください)。スキップします。'
                  % axis_names[i_axis])
            continue
        on_idx = np.atleast_1d(find_index(node[:, 0], nn))
        on_idx = on_idx[on_idx >= 0]
        h_axis_plot = doublecheck(np.concatenate(
            [h_axis_plot, node[on_idx, 3]]))

        if cross_axis == 1:
            plotline = node[on_idx, 1:3]
            plotline_no = node[on_idx, 0]
        else:
            sel = np.abs(node[on_idx, 3] - cross_height) == 0
            plotline = node[on_idx][sel][:, 1:3]
            plotline_no = node[on_idx][sel][:, 0]

        on_idx = np.atleast_1d(find_index(node[:, 0], plotline_no)) \
            if plotline_no.size else np.array([], dtype=int)
        if plotline.shape[0] > 1:
            plotline = doublecheck2(plotline)
        pick_axis_node_no.append(plotline_no)

        if plotline.shape[0] < 2:
            print('WARNING: 構面 %s の節点が%d点しかないため'
                  '通り芯を近似できません。スキップします。'
                  % (axis_names[i_axis], plotline.shape[0]))
            continue

        # XY各座標のばらつきを評価 <XY通り芯の判別>
        if judge_XY == 2:
            name = axis_names[i_axis]
            hasX = 'X' in name
            hasY = 'Y' in name
            if hasX and hasY:
                raise RuntimeError('通り芯名の設定ミス: %s' % name)
            elif hasX:
                X_var, Y_var = 0.0, 1.0
            elif hasY:
                X_var, Y_var = 1.0, 0.0
            else:
                raise RuntimeError('通り芯名の設定ミス: %s' % name)
        else:
            X_var = _var(plotline[:, 0])
            Y_var = _var(plotline[:, 1])

        if X_var < 0.0001 and Y_var < 0.0001:
            # MATLAB版もstopをコメントアウトしており係数0のまま (スキップ)
            pass
        elif X_var < 0.0001:  # X通り (X一定)
            co = np.polyfit(plotline[:, 1], plotline[:, 0], 1)  # X=AY+B
            axis_plot_coefi[i_axis, :] = [1, co[0], co[1]]
            axis_direction[i_axis, :] = [0, 1]
            pl3 = node[on_idx, 1:4]
            axis_xtick += list(pl3[:, 0])
            axis_ytick += list(pl3[:, 1])
        elif Y_var < 0.0001:  # Y通り (Y一定)
            co = np.polyfit(plotline[:, 0], plotline[:, 1], 1)  # Y=AX+B
            axis_plot_coefi[i_axis, :] = [2, co[0], co[1]]
            axis_direction[i_axis, :] = [1, 0]
            pl3 = node[on_idx, 1:4]
            axis_xtick += list(pl3[:, 0])
            axis_ytick += list(pl3[:, 1])
        elif Y_var > X_var:  # X通り (斜め)
            co = np.polyfit(plotline[:, 1], plotline[:, 0], 1)  # X=AY+B
            axis_plot_coefi[i_axis, :] = [1, co[0], co[1]]
            th = math.atan(1.0 / co[0])
            axis_direction[i_axis, :] = [math.cos(th), math.sin(th)]
            pl3 = node[on_idx, 1:4]
            axis_xtick += list(pl3[:, 0])
            axis_ytick += list(pl3[:, 1])
        else:  # X_var >= Y_var : Y通り (斜め)
            co = np.polyfit(plotline[:, 0], plotline[:, 1], 1)  # Y=AX+B
            axis_plot_coefi[i_axis, :] = [2, co[0], co[1]]
            th = math.atan(co[0])
            axis_direction[i_axis, :] = [math.cos(th), math.sin(th)]
            pl3 = node[on_idx, 1:4]
            axis_xtick += list(pl3[:, 0])
            axis_ytick += list(pl3[:, 1])

    return (axis_plot_coefi, axis_direction, pick_axis_node_no,
            np.asarray(axis_xtick), np.asarray(axis_ytick), h_axis_plot)


# ---------------------------------------------------------------------------
# 分割部材の一括表示オプション (MIDAS_model_plot_fig.m 54-97行)
# ---------------------------------------------------------------------------

def _unit_elements(filename, element, node, limit_sec_no):
    """unit_element取得とダミー材混在チェック・端部除去、uniele_ID作成."""
    unit_element = mgtopen_unit_2015(filename, element, node)
    for unit in unit_element:
        idx = np.atleast_1d(find_index(element[:, 0], unit[1]))
        flags = (element[idx, 2] < limit_sec_no).astype(float)
        if flags.sum() == 0 and flags.prod() == 0:
            pass  # 全て応力をプロットしない部材
        elif flags.sum() > 0 and flags.prod() > 0:
            pass  # 全てプロットする
        else:
            L = len(flags)
            if np.prod(flags[1:L - 1]) == 0:
                raise RuntimeError(
                    '結合部材の中間にダミー材が混在しています: %s'
                    % str(unit[1]))
            c3 = list(unit[2])
            if flags[L - 1] == 0:
                del c3[L]  # 端部の節点番号削除 (末尾)
            if flags[0] == 0:
                del c3[0]  # 端部の節点番号削除 (先頭)
            unit[2] = np.asarray(c3, dtype=float)
            unit[1] = np.asarray(unit[1], dtype=float)[flags > 0]
    uniele_ID = []
    for unit in unit_element:
        uniele_ID += list(np.asarray(unit[1]).ravel())
    uniele_ID = np.sort(np.asarray(uniele_ID, dtype=float))
    return unit_element, uniele_ID


# ---------------------------------------------------------------------------
# メインAPI
# ---------------------------------------------------------------------------

def _resolve_select(names, select):
    """グループ名/インデックスの選択リストを0-basedインデックス列へ解決."""
    if select is None:
        return list(range(len(names)))
    idx = []
    for s in select:
        if isinstance(s, (int, np.integer)):
            idx.append(int(s))
        else:
            stripped = [space_erace(n) for n in names]
            if space_erace(str(s)) in stripped:
                idx.append(stripped.index(space_erace(str(s))))
            else:
                raise KeyError('グループ %r が見つかりません (グループ一覧: %s)'
                               % (s, ', '.join(stripped)))
    return idx


def _safe_name(name):
    return re.sub(r'[^0-9A-Za-z_@\-぀-ヿ一-鿿]', '_',
                  space_erace(str(name)))


def plot_model(mgt_path, out_dir,
               axes_select=None, heights_select=None,
               limit_sec_no=9000,
               end_onoff=True, element_onoff=False, node_onoff=False,
               floors_select=None, floor_ref_select=None,
               symbols_select=None,
               axisname_location=3.0, line_location=2.0,
               mergins=(5.0, 2.0, 5.0, 5.0), fontsize=(5.0, 6.0, 8.0),
               paper_size=3, paper_orient=None,
               cross_axis=1, cross_height=None, judge_XY=1,
               w_select=None):
    """MIDAS mgtファイルからモデル構成図PDFを作成する.

    Parameters
    ----------
    mgt_path : str
        mgtファイルパス (MATLAB版の uigetfile に相当)。
    out_dir : str
        PDF出力ディレクトリ。
    axes_select : list of (str|int) or None
        鉛直構面として描くグループ名(またはindex)。None=全グループ
        (節点が登録されている構面のみ描画; MATLAB版のlistdlgに相当)。
    heights_select : list of float or None
        高さ方向寸法線に用いるZレベル。None=構面上の全節点レベル
        (MATLAB版の「鉛直部材の端点」選択に相当)。
    limit_sec_no : int
        ダミー材と見なす断面番号の下限 (GUI: dummy_no, 初期値9000)。
    end_onoff / element_onoff / node_onoff : bool
        end: 要素境界条件図,
        element: 要素ID図+使用材料図+断面符号図,
        node: 節点ID図 を出力するか。
    floors_select : list of (str|int) or None
        伏図として描くグループ。None=伏図を出力しない。
    floor_ref_select : list of (str|int) or None
        伏図の通り芯符号に用いる鉛直構面グループ。None=axes_selectと同じ。
    symbols_select : list of (str|int) or None
        軸組図の交差通り芯符号・間隔寸法として表示するグループ。
        None=従来動作 (axes_select で描画する構面のみを符号対象とする)。
        リスト指定時は描画構面と独立に符号対象を選べる (挙動追加)。
    axisname_location / line_location : float
        交差通り芯符号・寸法線の図枠からのオフセット (GUI初期値 3.0 / 2.0)。
    mergins : (L, R, T, B)
        図の外周マージン (GUI初期値 5, 2, 5, 5)。
    fontsize : (寸法値, 通り芯符号, タイトル)
        フォントサイズ (GUI初期値 5, 6, 8)。
    paper_size : int
        1=A1, 2=A2, 3=A3(既定), 4=A4。
    paper_orient : None
        MATLAB版同様、図の縦横比から自動決定 (引数は体裁維持のため保持)。
    cross_axis : int
        1=全節点で通り芯を近似 (GUI初期値), 2=cross_heightレベルの節点のみ。
    cross_height : float
        cross_axis=2 のときの交差通り芯レベル。
    judge_XY : int
        1=座標分散でXY通りを判別 (MATLAB版callbackで固定), 2=通り名のX/Yで判別。
    w_select : callable or None
        mgtopen_material の木材種別選択関数 (既定: 先頭を選択)。

    Returns
    -------
    list of str : 生成したPDFファイルパス。
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)
    if w_select is None:
        w_select = lambda *a, **k: 0  # noqa: E731  対話選択の無効化

    # %%%%%% モデルデータ読み込み %%%%%%
    node = mgtopen_node(mgt_path)
    element = mgtopen_element(mgt_path)
    plate = mgtopen_plate(mgt_path)
    beam = mgtopen_beam(mgt_path)
    truss = mgtopen_truss(mgt_path)
    material = mgtopen_material(mgt_path, w_select)
    material_name = mgtopen_material_name(mgt_path)
    sections, section_no, section_name = mgtopen_section(mgt_path)
    axis_name, axis_element, axis_node = mgtopen_group(mgt_path)
    wall_element, wall_base = mgtopen_wall(mgt_path, node)
    link = mgtopen_elasticlink(mgt_path)  # noqa: F841 MATLAB版でも未使用
    frm_rls = mgtopen_frm_rls(mgt_path)
    constraint = mgtopen_constraint(mgt_path)
    thickness = mgtopen_thickness(mgt_path)

    # mgt記述チェック: 節点/要素が空のグループを事前に警告
    empty_groups = [space_erace(axis_name[i]) for i in range(len(axis_name))
                    if np.size(axis_node[i]) == 0]
    if empty_groups:
        print('NOTE: 以下のグループは*GROUPのNODE_LISTが空のため構面として'
              '描画できません (mgt側の記述を確認してください): '
              + ', '.join(empty_groups))

    # %%%%%% 分割部材の一括表示オプション %%%%%%
    unit_element, uniele_ID = _unit_elements(mgt_path, element, node,
                                             limit_sec_no)

    # %%%%%% 鉛直構面の選択 %%%%%%
    v_idx = _resolve_select(axis_name, axes_select)
    if axes_select is None:
        # 全構面指定時は節点の登録がある構面のみ対象
        v_idx = [i for i in v_idx if np.size(axis_node[i]) > 0]

    made = []
    fig_no = 100

    # 通り芯符号の表示対象 (symbols_select) を axes_select と切り離す。
    # u_idx = 描画構面(先頭) + 符号のみの構面(後続)。None なら従来どおり
    # 描画構面のみが符号対象 (既存既定値の出力は不変)。
    if symbols_select is None:
        u_idx = list(v_idx)
    else:
        s_idx = _resolve_select(axis_name, symbols_select)
        s_idx = [i for i in s_idx if np.size(axis_node[i]) > 0]
        u_idx = list(v_idx) + [i for i in s_idx if i not in v_idx]

    if v_idx:
        axis_name_sel = [axis_name[i] for i in u_idx]
        axis_element_sel = [np.atleast_1d(
            np.asarray(axis_element[i], dtype=float)).ravel() for i in u_idx]
        axis_node_sel = [np.atleast_1d(
            np.asarray(axis_node[i], dtype=float)).ravel() for i in u_idx]

        (axis_plot_coefi, axis_direction, pick_axis_node_no,
         axis_xtick, axis_ytick, h_axis_plot) = _axis_coefficients(
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

        # 寸法線表示する高さ
        if heights_select is not None:
            h_axis_plot = np.sort(np.asarray(heights_select,
                                             dtype=float).ravel())

        C = dict(node=node, element=element, beam=beam, truss=truss,
                 plate=plate, wall_base=wall_base,
                 unit_element=unit_element, uniele_ID=uniele_ID,
                 material=material, material_name=material_name,
                 section_no=section_no, section_name=section_name,
                 thickness=thickness, frm_rls=frm_rls, constraint=constraint,
                 axis_name_sel=axis_name_sel,
                 axis_element_sel=axis_element_sel,
                 axis_node_sel=axis_node_sel,
                 axis_plot_coefi=axis_plot_coefi,
                 axis_direction=axis_direction,
                 pick_axis_node_no=pick_axis_node_no,
                 h_axis_plot=h_axis_plot,
                 limit_sec_no=limit_sec_no,
                 mergins=tuple(mergins), fontsize=tuple(fontsize),
                 axisname_location=axisname_location,
                 line_location=line_location, paper_size=paper_size)

        def _run_kind(kind, prefix):
            nonlocal fig_no
            for i_axis in range(len(v_idx)):
                if axis_plot_coefi[i_axis, 0] == 0:
                    continue  # 通芯の近似ができなかった構面
                out = os.path.join(
                    out_dir, '%s_%d_%s.pdf'
                    % (prefix, fig_no, _safe_name(axis_name_sel[i_axis])))
                made.append(_figure_vertical(kind, C, i_axis, out))
                fig_no += 1

        if node_onoff:
            _run_kind('node', '01_node')
        if element_onoff:
            _run_kind('element', '02_element')
            _run_kind('material', '03_material')
            _run_kind('section', '04_section')
        if end_onoff:
            _run_kind('end', '05_end')

    # %%%%%% 水平構面(伏図) %%%%%%
    if floors_select:
        f_idx = _resolve_select(axis_name, floors_select)
        r_idx = _resolve_select(axis_name,
                                floor_ref_select
                                if floor_ref_select is not None
                                else axes_select)
        r_idx = [i for i in r_idx if np.size(axis_node[i]) > 0
                 and i not in f_idx]

        floor_name_sel = [axis_name[i] for i in f_idx]
        floor_element_sel = [np.atleast_1d(
            np.asarray(axis_element[i], dtype=float)).ravel() for i in f_idx]
        floor_node_sel = [np.atleast_1d(
            np.asarray(axis_node[i], dtype=float)).ravel() for i in f_idx]

        v_name2 = [axis_name[i] for i in r_idx]
        v_elem2 = [np.atleast_1d(
            np.asarray(axis_element[i], dtype=float)).ravel() for i in r_idx]
        v_node2 = [np.atleast_1d(
            np.asarray(axis_node[i], dtype=float)).ravel() for i in r_idx]

        (v_coefi, _vdir, _vpick, _vx, _vy, _vh) = _axis_coefficients(
            node, v_node2, v_name2, cross_axis=1, judge_XY=judge_XY)

        C2 = dict(node=node, element=element, beam=beam, truss=truss,
                  plate=plate, wall_base=wall_base,
                  unit_element=unit_element, uniele_ID=uniele_ID,
                  material=material, material_name=material_name,
                  section_no=section_no, section_name=section_name,
                  thickness=thickness, frm_rls=frm_rls,
                  constraint=constraint,
                  floor_name_sel=floor_name_sel,
                  floor_element_sel=floor_element_sel,
                  floor_node_sel=floor_node_sel,
                  v_axis_name=v_name2, v_axis_element=v_elem2,
                  v_axis_node=v_node2, v_axis_plot_coefi=v_coefi,
                  limit_sec_no=limit_sec_no,
                  mergins=tuple(mergins), fontsize=tuple(fontsize),
                  axisname_location=axisname_location,
                  line_location=line_location, paper_size=paper_size)

        def _run_floor(kind, prefix):
            fno = 100
            for i_axis in range(len(f_idx)):
                if floor_node_sel[i_axis].size == 0:
                    print('WARNING: 伏図 %s は節点が空のためスキップします'
                          % floor_name_sel[i_axis])
                    continue
                out = os.path.join(
                    out_dir, '%s_%d_%s.pdf'
                    % (prefix, fno, _safe_name(floor_name_sel[i_axis])))
                made.append(_figure_floor(kind, C2, i_axis, out))
                fno += 1

        if node_onoff:
            _run_floor('node', '01_node_floor')
        if element_onoff:
            _run_floor('element', '02_element_floor')
            _run_floor('material', '03_material_floor')
            _run_floor('section', '04_section_floor')
        if end_onoff:
            _run_floor('end', '05_end_floor')

    return made


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv):
    import argparse
    p = argparse.ArgumentParser(
        description='MIDAS mgtファイルからモデル構成図PDFを作成する')
    p.add_argument('mgt')
    p.add_argument('out_dir')
    p.add_argument('--axes', nargs='*', default=None,
                   help='鉛直構面グループ名 (省略時: 全構面)')
    p.add_argument('--floors', nargs='*', default=None,
                   help='伏図グループ名 (省略時: 伏図なし)')
    p.add_argument('--heights', nargs='*', type=float, default=None)
    p.add_argument('--limit-sec-no', type=int, default=9000)
    p.add_argument('--element', action='store_true',
                   help='要素ID図・使用材料図・断面符号図も出力')
    p.add_argument('--node', action='store_true', help='節点ID図も出力')
    p.add_argument('--no-end', action='store_true',
                   help='要素境界条件図を出力しない')
    p.add_argument('--paper-size', type=int, default=3,
                   help='1=A1 2=A2 3=A3 4=A4')
    a = p.parse_args(argv)
    paths = plot_model(a.mgt, a.out_dir, axes_select=a.axes,
                       heights_select=a.heights,
                       limit_sec_no=a.limit_sec_no,
                       end_onoff=not a.no_end, element_onoff=a.element,
                       node_onoff=a.node, floors_select=a.floors,
                       paper_size=a.paper_size)
    for pth in paths:
        print(pth)
    print('%d files generated' % len(paths))


if __name__ == '__main__':
    _main(sys.argv[1:])
