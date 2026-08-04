# -*- coding: utf-8 -*-
"""MIDASデータからのモデル構成図DXF出力 (Midas_dxf の逐語移植).

元コード: msrc/privatetool_function/MIDAS/dxf/ 配下の
  Midas_dxf.m (GUI, calculate_Callback), MIDAS_dxf_fig.m,
  figure_dxf.m, figure_dxf_floor.m, end_add.m, end_add_plan.m,
  section_form_dxf.m, section_name_dxf.m, List_dxf.m,
  plot_line_dxf.m, plot_name_dxf.m, dxf_box.m, dxf_list_text.m,
  dxf_RCbeam_bar.m, dxf_RCcolumn_bar.m, dxf_RC_in_bar.m,
および tick.m, sectionget.m, sectionget2.m, column_beam_judge.m,
line_projection.m, get_RC_section.m, get_S_section.m, get_W_section.m。

- DXFはMATLAB版と同じテキスト直書き (ezdxf不使用)。レイヤ名
  (S-GRAY09, S-CYAN20, S-YELLOW20, S-GREEN30, S-MAGENDA30, S-RED09)・
  BLOCK構成・文字高さを踏襲。
- 出力は同梱の dxf_Template.dxf (R12ヘッダ+TABLES+BLOCKS途中) に追記し、
  CP932で書き出す (CADで日本語が読める)。
- GUI (uigetfile / listdlg / G_input) は export_dxf() の引数に置換。

API: export_dxf(mgt_path, out_path=None, limit_sec_no=9000, fontsize=400,
                space_ele=(39.0, 0.0), v_select=None, h_select=None,
                g_level=None, section_out_path=None) -> 出力パス
"""

import math
import io
import os

import numpy as np

from .util import (
    find_index,
    find_zero_index,
    doublecheck,
    doublecheck2,
    space_erace,
    cell2matrix,
)
from .mgt import (
    node_index_get,
    mgtopen_node,
    mgtopen_element,
    mgtopen_plate,
    mgtopen_beam,
    mgtopen_truss,
    mgtopen_material,
    mgtopen_material_name,
    mgtopen_group,
    mgtopen_wall,
    mgtopen_elasticlink,
    mgtopen_frm_rls,
    mgtopen_constraint,
    mgtopen_RCbeam,
    mgtopen_RCcolumn,
    mgtopen_RCwall,
    mgtopen_SRCbeam,
    mgtopen_unit_2015,
)
from .section import mgtopen_section

# ------------------------------------------------------------------
# MATLAB互換 基本関数
# ------------------------------------------------------------------

def num2str(x, prec=None):
    """MATLABのnum2strの逐語再現.

    - 文字列はそのまま返す (MATLABのnum2str(char)=char)
    - prec指定時: sprintf('%.<prec>g') と同一 (prec=0はC規約で1桁扱い,
      例 num2str(200,0)='2e+02' … MATLAB実出力out.dxfで確認済み)
    - 整数値のdouble: '%d'
    - それ以外: 有効桁 = max(floor(log10(|x|))+1,1)+4 の %g
      (num2str(pi)='3.1416', num2str(12345.6789)='12345.6789')
    """
    if isinstance(x, str):
        return x
    x = float(x)
    if math.isnan(x):
        return 'NaN'    # MATLAB: num2str(NaN)
    if math.isinf(x):
        return 'Inf' if x > 0 else '-Inf'  # MATLAB: num2str(±Inf)
    if prec is not None:
        return '%.*g' % (int(prec), x)
    if x == int(x) and abs(x) < 1e15:
        return '%d' % int(x)
    d = max(int(math.floor(math.log10(abs(x)))) + 1, 1) + 4
    return '%.*g' % (d, x)


def dec2hex(d):
    """MATLABのdec2hex (大文字16進、最小幅なし)."""
    return format(int(d), 'X')


def sind(a):
    return math.sin(math.radians(a))


def cosd(a):
    return math.cos(math.radians(a))


def atand(a):
    return math.degrees(math.atan(a))


def _isempty(a):
    """MATLABのisempty相当 (None / [] / 空ndarray)."""
    if a is None:
        return True
    if isinstance(a, np.ndarray):
        return a.size == 0
    try:
        return len(a) == 0
    except TypeError:
        return False


def _var(a):
    """MATLABのvar (標本分散)。要素1個以下なら0."""
    a = np.asarray(a, dtype=float).ravel()
    if a.size <= 1:
        return 0.0
    return float(np.var(a, ddof=1))


# ------------------------------------------------------------------
# tick.m : 節点の集合からx座標のみ，またはy座標のみを重複せずに抽出
#   xy は MATLABの列番号 (2=X, 3=Y, 4=Z) のまま渡すこと
# ------------------------------------------------------------------

def tick(node_info, base_node_no, xy):
    base = np.atleast_1d(np.asarray(base_node_no, dtype=float)).ravel()
    axisnode = []
    for nn in base:
        idx = int(np.argmin(np.abs(node_info[:, 0] - nn)))
        axisnode.append(node_info[idx, xy - 1])
    return doublecheck(np.asarray(axisnode, dtype=float))


# ------------------------------------------------------------------
# line_projection.m
# ------------------------------------------------------------------

def line_projection(node_xy, line_vec):
    node_xy = np.atleast_2d(np.asarray(node_xy, dtype=float))
    line_index = np.zeros(node_xy.shape[0])
    for i in range(node_xy.shape[0]):
        line_index[i] = (node_xy[i, 0] * line_vec[0]
                         + node_xy[i, 1] * line_vec[1])
    return line_index


# ------------------------------------------------------------------
# column_beam_judge.m : 部材が梁(1)か柱(2)かブレース・斜め柱(1.9)か
#   element_index : element_info の行index (0-based) のベクトル
#   MATLAB版の element_info(element_index(i)) は線形indexで
#   1列目(要素番号)を返すため、それを踏襲。
# ------------------------------------------------------------------

def column_beam_judge(element_index, element_info, node_info):
    idx = np.atleast_1d(np.asarray(element_index, dtype=int)).ravel()
    c_b_result = np.zeros(idx.shape, dtype=float)
    for i in range(idx.size):
        ele_no = element_info[idx[i], 0]
        nodei_index, nodej_index = node_index_get(ele_no, element_info,
                                                  node_info)
        XY = math.sqrt(
            (node_info[nodei_index, 1] - node_info[nodej_index, 1]) ** 2
            + (node_info[nodei_index, 2] - node_info[nodej_index, 2]) ** 2)
        Z = abs(node_info[nodei_index, 3] - node_info[nodej_index, 3])
        if XY > Z:
            c_b_result[i] = 1      # 梁
        elif XY < Z / 100:
            c_b_result[i] = 2      # 完全に鉛直な柱
        else:
            c_b_result[i] = 1.9    # ブレースorななめ柱
    return c_b_result


# ------------------------------------------------------------------
# sectionget.m / sectionget2.m
#   section_info: mgtopen_sectionの戻り値 sections
#                 (長さ17のlist, index1..16, 各要素 None or ndarray)
#   戻り値 section_size: 1D ndarray
#     [断面番号, 断面寸法..., section_index1(MATLAB1-based形式種別),
#      section_index2(MATLAB1-based行番号)]
# ------------------------------------------------------------------

def sectionget(ele_no, element_info, section_info, limit_sec_no):
    element_index = find_index(element_info[:, 0], ele_no)
    section_no = element_info[element_index, 2]
    if limit_sec_no <= section_no:
        return np.array([section_no, 0, 0, 0], dtype=float)
    return sectionget2(section_info, section_no)


def sectionget2(section_info, section_no):
    section_index2 = 0
    section_index1 = None
    m = len(section_info)
    for i in range(1, m):
        si = section_info[i]
        if _isempty(si):
            continue
        fi = find_index(si[:, 0], section_no)
        if fi != -1:
            section_index2 += fi + 1   # MATLAB 1-based indexを加算
            section_index1 = i
    if section_index1 is None:
        # MATLAB版は未定義変数エラーで停止する
        raise KeyError('sectionget: 断面番号 %s が断面リストに見つかりません'
                       % section_no)
    row = section_info[section_index1][section_index2 - 1]
    return np.concatenate((
        [section_no],
        row[1:],
        [float(section_index1), float(section_index2)]))


# ------------------------------------------------------------------
# get_RC_section.m / get_S_section.m / get_W_section.m
# ------------------------------------------------------------------

def _get_material_sections(element, material, mat_types):
    material_no = []
    material = np.atleast_2d(np.asarray(material, dtype=float)) \
        if not _isempty(material) else np.zeros((0, 2))
    for i in range(material.shape[0]):
        if material[i, 1] in mat_types:
            material_no.append(material[i, 0])
    section_no = np.array([])
    for m_no in material_no:
        zi = find_zero_index(element[:, 1] - m_no)
        section_no = np.concatenate((section_no, element[zi, 2]))
    if section_no.size == 0:
        return np.array([])
    return np.sort(doublecheck(section_no))


def get_RC_section(element, material):
    return _get_material_sections(element, material, (3.0, 4.0, 8.0))


def get_S_section(element, material):
    return _get_material_sections(element, material, (1.0, 2.0))


def get_W_section(element, material):
    return _get_material_sections(element, material, (10.0,))


# ------------------------------------------------------------------
# plot_line_dxf.m : 芯線(LINE)または幅付き部材(POLYLINE)のdxfテキスト
#   nodei_index, nodej_index : nodeの行index (0-based)
#   angle : MATLABの列番号ペア (例 [2,4]=X-Z面, [2,3]=X-Y面) のまま渡す
#   width : スカラ or 長さ2 (テーパー断面)
#   G_up  : スカラ or 長さ2
#   戻り値: (text:list[str], element_max)
# ------------------------------------------------------------------

def plot_line_dxf(nodei_index, nodej_index, ele_no, node, base_point,
                  width, element_max, class_name, ij_end, angle, G_up):
    a1, a2 = int(angle[0]) - 1, int(angle[1]) - 1
    w = np.atleast_1d(np.asarray(width, dtype=float)).ravel()
    text = []
    if np.all(w == 0):  # MATLAB: if width==0
        text += ['  0', 'LINE', '  8', class_name, '  5', dec2hex(ele_no)]
        text += [' 10', num2str((node[nodei_index, a1] + base_point[0]) * 1000),
                 ' 20', num2str((node[nodei_index, a2] + base_point[1]) * 1000),
                 ' 30', '0.0',
                 ' 11', num2str((node[nodej_index, a1] + base_point[0]) * 1000),
                 ' 21', num2str((node[nodej_index, a2] + base_point[1]) * 1000),
                 ' 31', '0.0']
        return text, element_max

    dvec = np.array([node[nodej_index, a1] - node[nodei_index, a1],
                     node[nodej_index, a2] - node[nodei_index, a2]])
    vec_v = dvec / np.linalg.norm(dvec)
    g = np.atleast_1d(np.asarray(G_up, dtype=float)).ravel()
    perp = np.array([-1 * vec_v[1], vec_v[0]])

    if w.size == 2:  # テーパー断面
        g1 = g[0]
        g2 = g[1] if g.size == 2 else g[0]
        pi_ = np.array([node[nodei_index, a1] - vec_v[0] * ij_end[0],
                        node[nodei_index, a2] - vec_v[1] * ij_end[0]])
        pj_ = np.array([node[nodej_index, a1] + vec_v[0] * ij_end[1],
                        node[nodej_index, a2] + vec_v[1] * ij_end[1]])
        node1 = pi_ + w[0] / 2 * perp + g1 * perp
        node4 = pi_ - w[0] / 2 * perp + g1 * perp
        node2 = pj_ + w[1] / 2 * perp + g2 * perp
        node3 = pj_ - w[1] / 2 * perp + g2 * perp
    else:
        g1 = g[0]
        pi_ = np.array([node[nodei_index, a1] - vec_v[0] * ij_end[0],
                        node[nodei_index, a2] - vec_v[1] * ij_end[0]])
        pj_ = np.array([node[nodej_index, a1] + vec_v[0] * ij_end[1],
                        node[nodej_index, a2] + vec_v[1] * ij_end[1]])
        node1 = pi_ + w[0] / 2 * perp + g1 * perp
        node4 = pi_ - w[0] / 2 * perp + g1 * perp
        node2 = pj_ + w[0] / 2 * perp + g1 * perp
        node3 = pj_ - w[0] / 2 * perp + g1 * perp

    text += ['  0', 'POLYLINE', '  8', class_name, '  6', 'CONTINUOUS',
             '  5', dec2hex(ele_no), ' 66', '1']
    text += [' 10', '0.0', ' 20', '0.0', ' 30', '0.0', ' 70', '1',
             ' 75', '5']
    for nd in (node1, node2, node3, node4):
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name, '  6', 'CONTINUOUS',
                 '  5', dec2hex(element_max)]
        text += [' 10', num2str((nd[0] + base_point[0]) * 1000),
                 ' 20', num2str((nd[1] + base_point[1]) * 1000),
                 ' 30', '0.0']
    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  6', 'CONTINUOUS',
             '  5', dec2hex(element_max)]
    return text, element_max


# ------------------------------------------------------------------
# plot_name_dxf.m : 符号BLOCKのINSERT
#   戻り値: (text:list[str], element_max)
# ------------------------------------------------------------------

def plot_name_dxf(base_point, width, element_max, name, rotd,
                  node1, node2, name_class, G_up):
    w = np.atleast_1d(np.asarray(width, dtype=float)).ravel()
    if np.all(w == 0):  # MATLAB: if width==0
        return [], element_max
    element_max = element_max + 1
    text = ['  0', 'INSERT', '  8', name_class, '  5', dec2hex(element_max)]
    text += ['  2', name,
             ' 10', num2str((node1[0] * 0.5 + node2[0] * 0.5
                             + base_point[0]) * 1000),
             ' 20', num2str((node1[1] * 0.5 + node2[1] * 0.5
                             + base_point[1]) * 1000),
             ' 30', '0.0', ' 50', num2str(rotd)]
    return text, element_max



# ========================================================================
# section_form_dxf.m / section_name_dxf.m
# ========================================================================

# -*- coding: utf-8 -*-
# ============================================================
# part_section.py : 断面形状BLOCK・断面符号BLOCKのDXFテキスト生成
# 元コード: section_form_dxf.m, section_name_dxf.m
# (import文なし。num2str/dec2hex/find_index/space_erace/sectionget2/
#  _isempty 等は part_common.py 定義済みのものを使用)
# ============================================================


# 元: section_form_dxf.m L1-L220
def section_form_dxf(RC_section_no, S_section_no, W_section_no,
                     SRC_section_no, section_no, sections, section_name,
                     element_max, column_section_class, girder_section_class,
                     column_class, girder_class):
    # MATLABの X_section_no(i,1) / section_no(:,1) 参照に合わせて
    # 1列目のみの1Dベクトルへ正規化する (値の意味は変えない)
    def _c1(a):
        if _isempty(a):
            return np.zeros(0)
        a = np.asarray(a, dtype=float)
        if a.ndim > 1:
            return a[:, 0]
        return a.ravel()

    RC_section_no = _c1(RC_section_no)
    S_section_no = _c1(S_section_no)
    W_section_no = _c1(W_section_no)
    SRC_section_no = _c1(SRC_section_no)
    section_no_c1 = _c1(section_no)

    text = []
    for i in range(len(S_section_no)):
        # find_indexは0-based (見つからない場合-1。MATLAB版は0でindexエラー停止)
        pick_section_name = section_name[
            find_index(section_no_c1, S_section_no[i])]
        section_size = sectionget2(sections, S_section_no[i])

        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name
        else:
            # MATLAB: pick_section_name(1,1:cut_index(1)-1)
            pick_name = pick_section_name[:cut_index]
        pick_name1 = space_erace(pick_name) + '_section'
        pick_name2 = space_erace(pick_name) + '_section2'

        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            class_name1 = column_section_class
            class_name2 = column_class
        elif findC == -1:  # 梁
            class_name1 = girder_section_class
            class_name2 = girder_class
        else:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'

        # MATLAB: section_size(length(section_size)-1) = section_size[-2]
        if section_size[-2] == 1:  # H鋼
            text, element_max = dxf_H(section_size, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_H(section_size, text, element_max,
                                      pick_name2, class_name2)
        elif section_size[-2] == 2:  # SB
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name1, class_name1)
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name2, class_name2)
        elif section_size[-2] == 4:  # P
            text, element_max = dxf_P(section_size, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_P(section_size, text, element_max,
                                      pick_name2, class_name2)
        elif section_size[-2] == 5:  # C
            text, element_max = dxf_C(section_size, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_C(section_size, text, element_max,
                                      pick_name2, class_name2)
        elif section_size[-2] == 7:  # B
            text, element_max = dxf_B(section_size, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_B(section_size, text, element_max,
                                      pick_name2, class_name2)
        elif section_size[-2] == 9:  # L
            text, element_max = dxf_L(section_size, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_L(section_size, text, element_max,
                                      pick_name2, class_name2)

    # 配筋なし，外形SRC断面
    for i in range(len(SRC_section_no)):
        pick_section_name = section_name[
            find_index(section_no_c1, SRC_section_no[i])]
        section_size = sectionget2(sections, SRC_section_no[i])

        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name
        else:
            pick_name = pick_section_name[:cut_index]

        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            class_name1 = column_section_class
            class_name2 = column_class
        elif findC == -1:  # 梁
            class_name1 = girder_section_class
            class_name2 = girder_class
        else:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'

        if section_size[-2] == 10:
            pick_name1 = space_erace(pick_name) + '_section'
            pick_name2 = space_erace(pick_name) + '_section2'
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name1, class_name1)
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name2, class_name2)
        else:
            pass

    # SRC内蔵鉄骨断面
    for i in range(len(SRC_section_no)):
        pick_section_name = section_name[
            find_index(section_no_c1, SRC_section_no[i])]
        section_size = sectionget2(sections, SRC_section_no[i])

        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name + '_S'
        else:
            pick_name = pick_section_name[:cut_index] + '_S'

        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            class_name1 = column_section_class
            class_name2 = column_class
        elif findC == -1:  # 梁
            class_name1 = girder_section_class
            class_name2 = girder_class
        else:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'

        if section_size[-2] == 10:
            pick_name1 = space_erace(pick_name) + '_section'
            pick_name2 = space_erace(pick_name) + '_section2'
            # MATLAB: dxf_H([section_size(1,[1 4:9]) 0],...)
            #   → 列1,4,5,6,7,8,9 (0-basedで0,3,4,5,6,7,8) に 0 を付加
            ss = np.concatenate((section_size[[0, 3, 4, 5, 6, 7, 8]], [0.0]))
            text, element_max = dxf_H(ss, text, element_max,
                                      pick_name1, class_name1)
            text, element_max = dxf_H(ss, text, element_max,
                                      pick_name2, class_name2)
        else:
            pass

    # 配筋なし，外形RC断面
    for i in range(len(RC_section_no)):
        pick_section_name = section_name[
            find_index(section_no_c1, RC_section_no[i])]
        section_size = sectionget2(sections, RC_section_no[i])

        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name
        else:
            pick_name = pick_section_name[:cut_index]

        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            class_name1 = column_section_class
            class_name2 = column_class
        elif findC == -1:  # 梁
            class_name1 = girder_section_class
            class_name2 = girder_class
        else:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'

        if section_size[-2] == 2:
            pick_name1 = space_erace(pick_name) + '_section'
            pick_name2 = space_erace(pick_name) + '_section2'
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name1, class_name1)
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name2, class_name2)
        elif section_size[-2] == 12:
            pick_name11 = space_erace(pick_name) + '_1_section'
            pick_name12 = space_erace(pick_name) + '_1_section2'
            # MATLAB: section_size(1,1:3) → [0:3]
            text, element_max = dxf_SB(section_size[0:3], text, element_max,
                                       pick_name11, class_name1)
            text, element_max = dxf_SB(section_size[0:3], text, element_max,
                                       pick_name12, class_name2)
            pick_name21 = space_erace(pick_name) + '_2_section'
            pick_name22 = space_erace(pick_name) + '_2_section2'
            # MATLAB: (section_size(1,1:3)+section_size(1,[1 4 5]))/2
            ss_mid = (section_size[0:3] + section_size[[0, 3, 4]]) / 2
            text, element_max = dxf_SB(ss_mid, text, element_max,
                                       pick_name21, class_name1)
            text, element_max = dxf_SB(ss_mid, text, element_max,
                                       pick_name22, class_name2)
            pick_name31 = space_erace(pick_name) + '_3_section'
            pick_name32 = space_erace(pick_name) + '_3_section2'
            # MATLAB: section_size(1,[1 4 5]) → [0,3,4]
            text, element_max = dxf_SB(section_size[[0, 3, 4]], text,
                                       element_max, pick_name31, class_name1)
            text, element_max = dxf_SB(section_size[[0, 3, 4]], text,
                                       element_max, pick_name32, class_name2)

    # 外形＿木部材断面
    for i in range(len(W_section_no)):
        pick_section_name = section_name[
            find_index(section_no_c1, W_section_no[i])]
        section_size = sectionget2(sections, W_section_no[i])

        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name
        else:
            pick_name = pick_section_name[:cut_index]
        pick_name1 = space_erace(pick_name) + '_section'
        pick_name2 = space_erace(pick_name) + '_section2'

        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            class_name1 = column_section_class
            class_name2 = column_class
        elif findC == -1:  # 梁
            class_name1 = girder_section_class
            class_name2 = girder_class
        else:
            class_name1 = 'S-ORANGE40'
            class_name2 = 'S-ORANGE40'

        if section_size[-2] == 2:
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name1, class_name1)
            text, element_max = dxf_SB(section_size, text, element_max,
                                       pick_name2, class_name2)

    return text, element_max


# 元: section_form_dxf.m L223-L235 (dxf_P)
def dxf_P(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:3)*1000 → [1:3]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:3] * 1000

    text = list(text)
    text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
             ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

    element_max = element_max + 1
    text += ['  0', 'CIRCLE', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 40', num2str(section_size[0] / 2)]
    element_max = element_max + 1
    text += ['  0', 'CIRCLE', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 40', num2str(section_size[0] / 2 - section_size[1])]
    element_max = element_max + 1  # MATLAB版通り (ENDBLKでは番号を使わない)
    text += ['  0', 'ENDBLK']
    return text, element_max


# 元: section_form_dxf.m L238-L260 (dxf_SB)
def dxf_SB(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:3)*1000 → [1:3]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:3] * 1000

    text = list(text)
    text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
             ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

    element_max = element_max + 1
    text += ['  0', 'POLYLINE', '  8', class_name, '  5', dec2hex(element_max),
             ' 66', '1', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 70', '129']

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 8

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 9
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 16
    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  5', dec2hex(element_max),
             '  0', 'ENDBLK']
    return text, element_max


# 元: section_form_dxf.m L262-L303 (dxf_B)
def dxf_B(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:4)*1000 → [1:4]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:4] * 1000

    text = list(text)
    text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
             ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

    element_max = element_max + 1
    text += ['  0', 'POLYLINE', '  8', class_name, '  5', dec2hex(element_max),
             ' 66', '1', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 70', '129']

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 8

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 9
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 16
    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  5', dec2hex(element_max)]

    element_max = element_max + 1
    text += ['  0', 'POLYLINE', '  8', class_name, '  5', dec2hex(element_max),
             ' 66', '1', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 70', '129']

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(-1 * section_size[0] / 2 + section_size[2]),
             ' 30', '0.0']  # 1
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(section_size[0] / 2 - section_size[2]),
             ' 30', '0.0']  # 8

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2 - section_size[2]),
             ' 20', num2str(section_size[0] / 2 - section_size[2]),
             ' 30', '0.0']  # 9
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2 - section_size[2]),
             ' 20', num2str(-1 * section_size[0] / 2 + section_size[2]),
             ' 30', '0.0']  # 16
    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  5', dec2hex(element_max),
             '  0', 'ENDBLK']
    return text, element_max


# 元: section_form_dxf.m L306-L426 (dxf_H)
def dxf_H(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:8)*1000 → [1:8]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:8] * 1000
    if section_size[4] == 0 or section_size[5] == 0:
        section_size[4] = section_size[1]
        section_size[5] = section_size[3]
    else:
        pass

    text = list(text)
    if section_size[6] == 0:
        text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
                 ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

        element_max = element_max + 1
        text += ['  0', 'POLYLINE', '  8', class_name,
                 '  5', dec2hex(element_max), ' 66', '1', ' 10', '0.0',
                 ' 20', '0.0', ' 30', '0.0', ' 70', '129']

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 2
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 3

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 6
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 7
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 8

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 9
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 10
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 11

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 14
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 15
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 16

        element_max = element_max + 1
        text += ['  0', 'SEQEND', '  8', class_name,
                 '  5', dec2hex(element_max), '  0', 'ENDBLK']
    else:
        text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
                 ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

        element_max = element_max + 1
        text += ['  0', 'POLYLINE', '  8', class_name,
                 '  5', dec2hex(element_max), ' 66', '1', ' 10', '0.0',
                 ' 20', '0.0', ' 30', '0.0', ' 70', '129']

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 2
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2 - section_size[6]),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 3
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]
                                + section_size[6]),
                 ' 30', '0.0']  # 4

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]
                                - section_size[6]),
                 ' 30', '0.0']  # 5
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[2] / 2 - section_size[6]),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 6
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 7
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(-1 * section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 8

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 9
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[1] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 10
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2 + section_size[6]),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]),
                 ' 30', '0.0']  # 11
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2),
                 ' 20', num2str(section_size[0] / 2 - section_size[3]
                                - section_size[6]),
                 ' 30', '0.0']  # 12

        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]
                                + section_size[6]),
                 ' 30', '0.0']  # 13
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[2] / 2 + section_size[6]),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 14
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2 + section_size[5]),
                 ' 30', '0.0']  # 15
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name,
                 '  5', dec2hex(element_max),
                 ' 10', num2str(section_size[4] / 2),
                 ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 16

        element_max = element_max + 1
        text += ['  0', 'SEQEND', '  8', class_name,
                 '  5', dec2hex(element_max), '  0', 'ENDBLK']
    return text, element_max


# 元: section_form_dxf.m L428-L461 (dxf_C)
def dxf_C(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:5)*1000 → [1:5]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:5] * 1000

    text = list(text)
    text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
             ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

    element_max = element_max + 1
    text += ['  0', 'POLYLINE', '  8', class_name, '  5', dec2hex(element_max),
             ' 66', '1', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 70', '129']

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 2
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 3
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2 - section_size[3]),
             ' 30', '0.0']  # 4
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(section_size[0] / 2 - section_size[3]),
             ' 30', '0.0']  # 5
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(-1 * section_size[0] / 2 + section_size[3]),
             ' 30', '0.0']  # 6
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2 + section_size[3]),
             ' 30', '0.0']  # 7
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 8
    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  5', dec2hex(element_max),
             '  0', 'ENDBLK']
    return text, element_max


# 元: section_form_dxf.m L463-L491 (dxf_L)
def dxf_L(section_size, text, element_max, pick_name, class_name):
    # MATLAB: section_size(1,2:5)*1000 → [1:5]
    section_size = np.asarray(section_size, dtype=float).ravel()[1:5] * 1000

    text = list(text)
    text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
             ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー

    element_max = element_max + 1
    text += ['  0', 'POLYLINE', '  8', class_name, '  5', dec2hex(element_max),
             ' 66', '1', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
             ' 70', '129']

    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 1
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 2
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2), ' 30', '0.0']  # 3
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(section_size[1] / 2),
             ' 20', num2str(section_size[0] / 2 - section_size[3]),
             ' 30', '0.0']  # 4
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(section_size[0] / 2 - section_size[3]),
             ' 30', '0.0']  # 5
    element_max = element_max + 1
    text += ['  0', 'VERTEX', '  8', class_name, '  5', dec2hex(element_max),
             ' 10', num2str(-1 * section_size[1] / 2 + section_size[2]),
             ' 20', num2str(-1 * section_size[0] / 2), ' 30', '0.0']  # 6

    element_max = element_max + 1
    text += ['  0', 'SEQEND', '  8', class_name, '  5', dec2hex(element_max),
             '  0', 'ENDBLK']
    return text, element_max


# 元: section_name_dxf.m L1-L27
def section_name_dxf(section_no, section_name, element_max,
                     name_fontsize, name_class):
    text = []
    # MATLAB: for i=1:size(section_no,1)
    for i in range(len(section_no)):
        pick_section_name = section_name[i]
        cut_index = pick_section_name.find('_')
        if cut_index == -1:
            pick_name = pick_section_name
        else:
            pick_name = pick_section_name[:cut_index]

        pick_name = space_erace(pick_name) + '_NAME1'

        text += ['  0', 'BLOCK', '  2', pick_name, ' 70', '0', ' 10', '0.0',
                 ' 20', '0.0', ' 30', '0.0', '  3', pick_name]  # ダミー
        element_max = element_max + 1

        cut_index = pick_name.find('_')
        if cut_index == -1:
            pass  # MATLAB: pick_name=pick_name
        else:
            pick_name = pick_name[:cut_index]

        text += ['  0', 'TEXT', '  8', name_class,
                 '  5', dec2hex(element_max)]  # 第1節点
        text += [' 10', '0.0', ' 20', '0.0', ' 30', '0.0',
                 ' 40', num2str(name_fontsize, 0)]
        text += ['  1', pick_name, '  7', 'ARIAL', ' 72', '1', ' 11', '0',
                 ' 21', '0', ' 31', '0', ' 73', '1', '  0', 'ENDBLK']

    return text, element_max



# ========================================================================
# figure_dxf.m / end_add.m
# ========================================================================

# ============================================================
# part_figure.py : figure_dxf.m / end_add.m の逐語移植
# 元コード: msrc/privatetool_function/MIDAS/dxf/figure_dxf.m (L1-554 メイン,
#           L557-693 サブ関数 unit_element_dxf),
#           msrc/privatetool_function/MIDAS/dxf/end_add.m (L1-231)
# import文なし (part_common.py の定義済みヘルパーを直接使用)
# ============================================================


# 元: figure_dxf.m / end_add.m 内で繰り返される Glevel 参照パターン
#   if find_index(Glevel(:,1),sec_no)>0
#       if Glevel(...,2)==0 → G_up=0
#       elseif ==Inf  → G_up=-1*width/2
#       elseif ==-Inf → G_up= width/2
#       elseif ==1    → G_up=Glevel(...,3)-1*width/2
#   else G_up=0
# の共通化 (逐語意味は同一。*1000 が付く箇所は呼び出し側で乗算)。
# MATLAB版: Glevelが空[]またはスカラ0のときfind_indexは0(不一致)を返し
# else節でG_up=0となる → ここでは空/スカラを「見つからない」扱いで0を返す。
def _Glevel_G_up(Glevel, sec_no, width):
    if _isempty(Glevel) or np.ndim(Glevel) < 2:
        return 0
    gi = find_index(Glevel[:, 0], sec_no)
    if gi == -1:  # MATLAB: find_index(...)>0 の否定
        return 0
    if Glevel[gi, 1] == 0:
        G_up = 0
    elif Glevel[gi, 1] == np.inf:
        G_up = -1 * width / 2
    elif Glevel[gi, 1] == -np.inf:
        G_up = width / 2
    elif Glevel[gi, 1] == 1:
        G_up = Glevel[gi, 2] - 1 * width / 2
    else:
        # MATLAB版はどの分岐にも該当しない場合G_upが未定義(または前回値)の
        # まま後続処理に進む → 未定義変数エラー相当として停止する
        raise RuntimeError('MATLAB版stop相当: Glevel(:,2)が0/Inf/-Inf/1以外')
    return G_up


# 元: figure_dxf.m L1-554
def figure_dxf(limit_sec_no, unit_element, axis_element, axis_node, node,
               wall_base, wall_element, element, uniele_ID, axis_name, i_axis,
               axis_ztick, axis_ytick, axis_xtick, axis_plot_coefi,
               plate, beam, truss, pick_axis_node_no, axis_direction,
               section_nos, section_name, frm_rls, constraint,
               text_single, text_double, text_name, base_point, sections,
               element_max, single_class, column_class, girder_section_class,
               girder_class, name_class, Glevel, ori_point):
    # i_axis は0-basedで渡される (axis_element[i_axis]等にそのまま使用)
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)

    node2 = np.zeros((node.shape[0], 2))
    node2[:, 0] = line_projection(node[:, 1:3], axis_direction[i_axis, :])
    node2[:, 1] = line_projection(node[:, 1:3],
                                  [axis_direction[i_axis, 1] * -1,
                                   axis_direction[i_axis, 0]])

    ori_point2 = line_projection(ori_point, axis_direction[i_axis, :])

    node = np.column_stack((node[:, 0], node2, node[:, 3]))
    # node(:,2)=node(:,2)-min(node(find_index(node(:,1),axis_node{i_axis,1}),2)); (元コードのコメントアウト)
    node[:, 1] = node[:, 1] - ori_point2

    # axis_element{i_axis,1} : 通り上要素番号 (MATLABは行ベクトル)
    ax_ele = np.atleast_1d(np.asarray(axis_element[i_axis][0],
                                      dtype=float)).ravel()

    # beamにある要素番号から通芯上の梁要素とりだし
    # 0が入力されているものをはじいて，beam要素であるもののindexを保持
    # (MATLAB: find_indexは不一致=0を返すのでfindで通り上位置を得る。
    #  Python: 不一致=-1なので !=-1 の位置を得る。位置は0-based)
    if _isempty(beam):
        on_axis_beam_index = np.array([], dtype=int)  # MATLAB: find(0)=[]
    else:
        _fi = np.atleast_1d(find_index(beam[:, 0], ax_ele))
        on_axis_beam_index = np.where(_fi != -1)[0]

    # trussにある要素番号から通芯上の梁要素とりだし
    if _isempty(truss):
        on_axis_truss_index = np.array([], dtype=int)
    else:
        _fi = np.atleast_1d(find_index(truss[:, 0], ax_ele))
        on_axis_truss_index = np.where(_fi != -1)[0]

    # 板要素
    if _isempty(plate):
        on_axis_plate_index = np.array([], dtype=int)
    else:
        _fi = np.atleast_1d(find_index(plate[:, 0], ax_ele))
        on_axis_plate_index = np.where(_fi != -1)[0]

    # wall_stressにある要素番号から通芯上のtruss要素とりだし
    if _isempty(wall_base):
        on_axis_wall_index = np.array([], dtype=int)
    else:
        _fi = np.atleast_1d(find_index(wall_base[:, 0], ax_ele))
        on_axis_wall_index = np.where(_fi != -1)[0]
    on_axis_wall_ID = ax_ele[on_axis_wall_index]

    # trussには3，beamには1をふる(この数字で梁かトラスかを以下で判別する．)
    # MATLAB: axis_element{i_axis,2}(1:n)=0 …
    # Pythonではcell書き換え(呼び出し側への副作用)を避けローカル変数で保持
    axis_element_type = np.zeros(ax_ele.size)
    axis_element_type[on_axis_truss_index] = 3
    axis_element_type[on_axis_beam_index] = 1
    axis_element_type[on_axis_wall_index] = 2
    axis_element_type[on_axis_plate_index] = 4
    on_axis_element_index = np.atleast_1d(
        find_index(element[:, 0], ax_ele)).astype(int)

    axis_ysubtick = []  # (元コード通り。未使用)

    num_element_on_axis = ax_ele.size

    if _isempty(frm_rls):
        ele_end = []
    else:
        on_element = doublecheck(frm_rls[:, 0])
        rls_index = np.atleast_1d(find_index(ax_ele, on_element))
        rls_index = rls_index[np.where(rls_index != -1)[0]]  # MATLAB: rls_index(find(rls_index))
        ele_end = np.zeros((rls_index.size, 3))
        for i in range(rls_index.size):
            ele_no = ax_ele[rls_index[i]]
            ele_end[i, 0] = ele_no
            if frm_rls[find_index(frm_rls[:, 0], ax_ele[rls_index[i]]), 1] > 0:
                ele_end[i, 1] = 1
            # i端の行の次行がj端 (MATLAB: find_index(...)+1)
            if frm_rls[find_index(frm_rls[:, 0], ax_ele[rls_index[i]]) + 1, 1] > 0:
                ele_end[i, 2] = 1

    # 通り上にあるelement行の抽出 (MATLABの
    # element(on_axis_element_index(find(on_axis_element_index)),:) 相当。
    # on_axis_element_indexは以後不変なので一度だけ計算)
    element_on_axis_pick = element[
        on_axis_element_index[np.where(on_axis_element_index != -1)[0]], :]

    # %%%%%%%%%%%%%%%%%%%%%%%%直交梁をプロット%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    node_no_on = np.atleast_1d(np.asarray(axis_node[i_axis][0],
                                          dtype=float)).ravel()
    not_on_element = np.delete(
        element,
        on_axis_element_index[np.where(on_axis_element_index != -1)[0]],
        axis=0)
    girder_no_on = np.zeros((0, 3))  # MATLAB: []
    for j in range(node_no_on.size):
        element_id_on = find_zero_index(not_on_element[:, 3] - node_no_on[j])  # ｉ端
        element_id_on = np.concatenate(
            (element_id_on,
             find_zero_index(not_on_element[:, 4] - node_no_on[j]))).astype(int)  # ｊ端
        element_no_on = not_on_element[element_id_on, :]
        element_no_on = np.delete(
            element_no_on,
            np.where(element_no_on[:, 2] > limit_sec_no)[0], axis=0)
        element_no_on = element_no_on[:, 0]
        if _isempty(element_no_on):
            pass
        else:
            # MATLABの自動列拡張 (element_no_on(jj,2)=... で3列に拡張) を
            # 先取りして3列で確保 (挙動同一: 拡張は必ず起こる)
            _tmp = np.zeros((element_no_on.size, 3))
            _tmp[:, 0] = element_no_on
            element_no_on = _tmp
            # 注意(MATLABバグ踏襲): element_id_on はダミー断面除去前の行index
            # のままjjでループする (element_no_onは除去後) — ずれごと再現
            for jj in range(element_no_on.shape[0]):
                if not_on_element[element_id_on[jj], 3] == node_no_on[j]:  # ｉ端が算定高さにある
                    end_node_id = find_index(node[:, 0],
                                             not_on_element[element_id_on[jj], 4])  # ｊ端
                    if node[end_node_id, 2] == node[find_index(node[:, 0], node_no_on[j]), 2]:
                        element_no_on[jj, 1] = 0
                        element_no_on[jj, 2] = 0
                    elif node[end_node_id, 2] < node[find_index(node[:, 0], node_no_on[j]), 2]:
                        element_no_on[jj, 1] = node_no_on[j]
                        element_no_on[jj, 2] = node[end_node_id, 0]
                    else:
                        element_no_on[jj, 1] = -1 * node_no_on[j]
                        element_no_on[jj, 2] = node[end_node_id, 0]

                elif not_on_element[element_id_on[jj], 4] == node_no_on[j]:  # ｊ端が算定高さにある
                    end_node_id = find_index(node[:, 0],
                                             not_on_element[element_id_on[jj], 3])
                    if node[end_node_id, 2] == node[find_index(node[:, 0], node_no_on[j]), 2]:
                        element_no_on[jj, 1] = 0
                        element_no_on[jj, 2] = 0
                    elif node[end_node_id, 2] < node[find_index(node[:, 0], node_no_on[j]), 2]:
                        element_no_on[jj, 1] = node_no_on[j]
                        element_no_on[jj, 2] = node[end_node_id, 0]
                    else:
                        element_no_on[jj, 1] = -1 * node_no_on[j]
                        element_no_on[jj, 2] = node[end_node_id, 0]
                else:
                    element_no_on[jj, 0] = 0
            element_no_on = element_no_on[np.where(element_no_on[:, 0])[0], :]

            # 手前の梁がある場合(断面部材を配置)
            if element_no_on.shape[1] > 2 and np.max(element_no_on[:, 1]) > 0:
                for jj in range(element_no_on.shape[0]):
                    if element_no_on[jj, 1] > 0:
                        end_node_id = find_index(node[:, 0], element_no_on[jj, 1])
                        section_size = sectionget(element_no_on[jj, 0], element,
                                                  sections, limit_sec_no)
                        sec_no = section_size[0]
                        pick_section_name = section_name[
                            find_index(section_nos, sec_no)]

                        _ei = find_index(element[:, 0], element_no_on[jj, 0])
                        c_b_index = np.array(
                            [column_beam_judge(_ei, element, node)[0],
                             element[_ei, 5]])

                        cut_index = pick_section_name.find('_')  # MATLAB: strfind
                        if cut_index == -1:
                            pick_name = pick_section_name
                        else:
                            pick_name = pick_section_name[:cut_index]
                        pick_name = space_erace(pick_name)
                        put_block = pick_name + '_section'  # strcat
                        put_class = girder_section_class

                        if section_size[-2] == 1:
                            # MATLAB版はこの行のみセミコロン無し(表示のみで同値)
                            width = (section_size[1] * abs(cosd(c_b_index[1]))
                                     + section_size[2] * abs(sind(c_b_index[1])))
                        elif (section_size[-2] == 2 or section_size[-2] == 5
                              or section_size[-2] == 7 or section_size[-2] == 9
                              or section_size[-2] == 10):
                            width = (section_size[1] * abs(cosd(c_b_index[1]))
                                     + section_size[2] * abs(sind(c_b_index[1])))
                        elif section_size[-2] == 4 or section_size[-2] == 3:
                            width = section_size[1]
                        elif section_size[-2] == 12:
                            width1 = (section_size[1] * abs(cosd(c_b_index[1]))
                                      + section_size[2] * abs(sind(c_b_index[1])))
                            width3 = (section_size[3] * abs(cosd(c_b_index[1]))
                                      + section_size[4] * abs(sind(c_b_index[1])))
                            width = np.array([width1, width3])

                        # (この箇所のみG_upはmm単位: 元コードは各分岐で*1000)
                        G_up = _Glevel_G_up(Glevel, sec_no, width) * 1000

                        if sec_no > limit_sec_no:
                            pass
                        else:
                            element_max = element_max + 1
                            text_column = ['  0', 'INSERT', '  8', put_class,
                                           '  5', dec2hex(element_max)]
                            text_column += [
                                '  2', put_block,
                                ' 10', num2str(base_point[0] * 1000
                                               + node[end_node_id, 1] * 1000),
                                ' 20', num2str(base_point[1] * 1000
                                               + node[end_node_id, 3] * 1000
                                               + G_up),
                                ' 30', '0.0']
                            text_double += text_column
        if not _isempty(element_no_on):
            # MATLAB: girder_no_on=[girder_no_on;element_no_on] (空は無視)
            girder_no_on = np.vstack((girder_no_on, element_no_on))

    # %%%%%%%%%%%%%%通り上にある単一要素の芯線をプロット%%%%%%%%%%%%%%%%%%%%%%%
    for i_element in range(num_element_on_axis):
        if axis_element_type[i_element] == 1:  # 梁要素のplot%%%%%%%%%%%%%%%

            # プロット部材の断面番号取得
            sec_no = element[on_axis_element_index[i_element], 2]

            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')

            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            # プロット部材の要素番号取得
            ele_no = element[on_axis_element_index[i_element], 0]
            section_size = sectionget(ele_no, element, sections, limit_sec_no)

            nodeifig_index, nodejfig_index = node_index_get(
                ax_ele[i_element], element, node)

            ele_judge_length = np.linalg.norm(
                node[nodeifig_index, 1:4] - node[nodejfig_index, 1:4])  # (元コード通り。未使用)

            # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
            text_add, element_max = plot_line_dxf(
                nodeifig_index, nodejfig_index, ele_no, node, base_point, 0,
                element_max, single_class, [0, 0], [2, 4], 0)
            text_single += text_add

            # プロット部材がcolumnかbeamか，またbeta角度を保存している．6つ目はβ角度
            c_b_index = np.array(
                [column_beam_judge(on_axis_element_index[i_element],
                                   element, node)[0],
                 element[on_axis_element_index[i_element], 5]])

            if sec_no >= limit_sec_no:
                # ダミー材でないかのチェック
                pass
            elif c_b_index[0] == 2:
                # 柱
                vec_v = np.array(
                    [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                     node[nodejfig_index, 3] - node[nodeifig_index, 3]])
                vec_v = vec_v / np.linalg.norm(vec_v)

                if _isempty(uniele_ID):

                    # 柱サイズでダブルラインをdxf(テキスト)で書き出し
                    if (section_size[-2] == 1 or section_size[-2] == 2
                            or section_size[-2] == 7):
                        width = (section_size[1]
                                 * abs(axis_direction[i_axis, 0] * cosd(c_b_index[1])
                                       + axis_direction[i_axis, 1] * sind(c_b_index[1]))
                                 + section_size[2]
                                 * math.sqrt(1 - (axis_direction[i_axis, 0] * cosd(c_b_index[1])
                                                  + axis_direction[i_axis, 1] * sind(c_b_index[1])) ** 2))
                    elif section_size[-2] == 3 or section_size[-2] == 4:
                        width = section_size[1]
                    ij_add = end_add(ele_no, element_on_axis_pick, node,
                                     ele_end, c_b_index[0], width, sections,
                                     limit_sec_no, axis_direction, i_axis,
                                     Glevel, girder_no_on, element)

                    text_add, element_max = plot_line_dxf(
                        nodeifig_index, nodejfig_index, ele_no, node,
                        base_point, width, element_max, column_class, ij_add,
                        [2, 4], 0)
                    text_double += text_add

                    # 幅のあるポリラインの点を作成
                    _perp = np.array([-1 * vec_v[1], vec_v[0]])
                    node1 = np.array([node[nodeifig_index, 1],
                                      node[nodeifig_index, 3]]) + width / 2 * _perp
                    node4 = np.array([node[nodeifig_index, 1],
                                      node[nodeifig_index, 3]]) - width / 2 * _perp
                    node2 = np.array([node[nodejfig_index, 1],
                                      node[nodejfig_index, 3]]) + width / 2 * _perp
                    node3 = np.array([node[nodejfig_index, 1],
                                      node[nodejfig_index, 3]]) - width / 2 * _perp

                    pick_name = pick_name + '_NAME1'
                    rotd = 0
                    # MATLAB版はG_up引数を渡していない(plot_name_dxf内で未使用) → 0を渡す
                    text_add, element_max = plot_name_dxf(
                        base_point, width, element_max, pick_name, rotd,
                        node3, node4, name_class, 0)
                    text_name += text_add

                else:
                    judge_unit = find_index(
                        np.asarray(uniele_ID, dtype=float).ravel(), ele_no)
                    if judge_unit == -1:  # MATLAB: judge_unit==0 (不一致)
                        # 柱サイズでダブルラインをdxf(テキスト)で書き出し
                        if (section_size[-2] == 1 or section_size[-2] == 2
                                or section_size[-2] == 7):
                            width = (section_size[1]
                                     * abs(axis_direction[i_axis, 0] * cosd(c_b_index[1])
                                           + axis_direction[i_axis, 1] * sind(c_b_index[1]))
                                     + section_size[2]
                                     * math.sqrt(1 - (axis_direction[i_axis, 0] * cosd(c_b_index[1])
                                                      + axis_direction[i_axis, 1] * sind(c_b_index[1])) ** 2))
                        elif section_size[-2] == 4 or section_size[-2] == 3:
                            width = section_size[1]
                        ij_add = end_add(ele_no, element_on_axis_pick, node,
                                         ele_end, c_b_index[0], width,
                                         sections, limit_sec_no,
                                         axis_direction, i_axis, Glevel,
                                         girder_no_on, element)

                        text_add, element_max = plot_line_dxf(
                            nodeifig_index, nodejfig_index, ele_no, node,
                            base_point, width, element_max, column_class,
                            ij_add, [2, 4], 0)
                        text_double += text_add

                        # 幅のあるポリラインの点を作成
                        _perp = np.array([-1 * vec_v[1], vec_v[0]])
                        node1 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3]]) + width / 2 * _perp
                        node4 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3]]) - width / 2 * _perp
                        node2 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3]]) + width / 2 * _perp
                        node3 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3]]) - width / 2 * _perp

                        pick_name = pick_name + '_NAME1'
                        rotd = 0
                        text_add, element_max = plot_name_dxf(
                            base_point, width, element_max, pick_name, rotd,
                            node3, node4, name_class, 0)
                        text_name += text_add

            elif c_b_index[0] == 1 or c_b_index[0] == 1.9:

                if _isempty(uniele_ID):
                    # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                    if section_size[-2] == 1:
                        width = (section_size[1] * abs(cosd(c_b_index[1]))
                                 + section_size[2] * abs(sind(c_b_index[1])))
                    elif section_size[-2] == 2:
                        width = (section_size[1] * abs(cosd(c_b_index[1]))
                                 + section_size[2] * abs(sind(c_b_index[1])))
                    elif section_size[-2] == 3 or section_size[-2] == 4:
                        width = section_size[1]
                    elif section_size[-2] == 12:
                        width1 = (section_size[1] * abs(cosd(c_b_index[1]))
                                  + section_size[2] * abs(sind(c_b_index[1])))
                        width3 = (section_size[3] * abs(cosd(c_b_index[1]))
                                  + section_size[4] * abs(sind(c_b_index[1])))
                        width = np.array([width1, width3])

                    G_up = _Glevel_G_up(Glevel, sec_no, width)

                    ij_add = end_add(ele_no, element_on_axis_pick, node,
                                     ele_end, c_b_index[0], width, sections,
                                     limit_sec_no, axis_direction, i_axis, 0,
                                     girder_no_on, element)

                    if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                        nodeifig_index2 = nodejfig_index
                        nodejfig_index2 = nodeifig_index
                        nodeifig_index = nodeifig_index2
                        nodejfig_index = nodejfig_index2

                        ij_add = np.array([ij_add[1], ij_add[0]])

                    vec_v = np.array(
                        [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                         node[nodejfig_index, 3] - node[nodeifig_index, 3]])
                    vec_v = vec_v / np.linalg.norm(vec_v)

                    text_add, element_max = plot_line_dxf(
                        nodeifig_index, nodejfig_index, ele_no, node,
                        base_point, width, element_max, girder_class, ij_add,
                        [2, 4], G_up)
                    text_double += text_add

                    # MATLABのG_up(1)/G_up(2)参照 (スカラでG_up(2)参照時は
                    # MATLAB版もエラー → PythonではIndexError)
                    G_up_v = np.atleast_1d(np.asarray(G_up, dtype=float))
                    _perp = np.array([-1 * vec_v[1], vec_v[0]])
                    if np.size(width) == 2:
                        node1 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3] + G_up_v[0]]) + width[0] / 2 * _perp
                        node4 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3] + G_up_v[0]]) - width[0] / 2 * _perp
                        node2 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3] + G_up_v[1]]) + width[1] / 2 * _perp
                        node3 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3] + G_up_v[1]]) - width[1] / 2 * _perp
                    else:
                        # 幅のあるポリラインの点を作成
                        node1 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3] + G_up_v[0]]) + width / 2 * _perp
                        node4 = np.array([node[nodeifig_index, 1],
                                          node[nodeifig_index, 3] + G_up_v[0]]) - width / 2 * _perp
                        node2 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3] + G_up_v[0]]) + width / 2 * _perp
                        node3 = np.array([node[nodejfig_index, 1],
                                          node[nodejfig_index, 3] + G_up_v[0]]) - width / 2 * _perp

                    pick_name = pick_name + '_NAME1'
                    rotd = atand(vec_v[1] / vec_v[0])
                    text_add, element_max = plot_name_dxf(
                        base_point, width, element_max, pick_name, rotd,
                        node1, node2, name_class, 0)
                    text_name += text_add

                else:
                    judge_unit = find_index(
                        np.asarray(uniele_ID, dtype=float).ravel(), ele_no)
                    if judge_unit == -1:  # MATLAB: judge_unit==0 (不一致)
                        # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                        if section_size[-2] == 1:
                            width = (section_size[1] * abs(cosd(c_b_index[1]))
                                     + section_size[2] * abs(sind(c_b_index[1])))
                        elif section_size[-2] == 2:
                            width = (section_size[1] * abs(cosd(c_b_index[1]))
                                     + section_size[2] * abs(sind(c_b_index[1])))
                        elif section_size[-2] == 3 or section_size[-2] == 4:
                            width = section_size[1]
                        elif section_size[-2] == 12:
                            width1 = (section_size[1] * abs(cosd(c_b_index[1]))
                                      + section_size[2] * abs(sind(c_b_index[1])))
                            width3 = (section_size[3] * abs(cosd(c_b_index[1]))
                                      + section_size[4] * abs(sind(c_b_index[1])))
                            width = np.array([width1, width3])

                        G_up = _Glevel_G_up(Glevel, sec_no, width)

                        ij_add = end_add(ele_no, element_on_axis_pick, node,
                                         ele_end, c_b_index[0], width,
                                         sections, limit_sec_no,
                                         axis_direction, i_axis, 0,
                                         girder_no_on, element)

                        if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                            nodeifig_index2 = nodejfig_index
                            nodejfig_index2 = nodeifig_index
                            nodeifig_index = nodeifig_index2
                            nodejfig_index = nodejfig_index2

                            ij_add = np.array([ij_add[1], ij_add[0]])

                        vec_v = np.array(
                            [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                             node[nodejfig_index, 3] - node[nodeifig_index, 3]])
                        vec_v = vec_v / np.linalg.norm(vec_v)

                        text_add, element_max = plot_line_dxf(
                            nodeifig_index, nodejfig_index, ele_no, node,
                            base_point, width, element_max, girder_class,
                            ij_add, [2, 4], G_up)
                        text_double += text_add

                        G_up_v = np.atleast_1d(np.asarray(G_up, dtype=float))
                        _perp = np.array([-1 * vec_v[1], vec_v[0]])
                        if np.size(width) == 2:
                            node1 = np.array([node[nodeifig_index, 1],
                                              node[nodeifig_index, 3] + G_up_v[0]]) + width[0] / 2 * _perp
                            node4 = np.array([node[nodeifig_index, 1],
                                              node[nodeifig_index, 3] + G_up_v[0]]) - width[0] / 2 * _perp
                            node2 = np.array([node[nodejfig_index, 1],
                                              node[nodejfig_index, 3] + G_up_v[1]]) + width[1] / 2 * _perp
                            node3 = np.array([node[nodejfig_index, 1],
                                              node[nodejfig_index, 3] + G_up_v[1]]) - width[1] / 2 * _perp
                        else:
                            # 幅のあるポリラインの点を作成
                            node1 = np.array([node[nodeifig_index, 1],
                                              node[nodeifig_index, 3] + G_up_v[0]]) + width / 2 * _perp
                            node4 = np.array([node[nodeifig_index, 1],
                                              node[nodeifig_index, 3] + G_up_v[0]]) - width / 2 * _perp
                            node2 = np.array([node[nodejfig_index, 1],
                                              node[nodejfig_index, 3] + G_up_v[0]]) + width / 2 * _perp
                            node3 = np.array([node[nodejfig_index, 1],
                                              node[nodejfig_index, 3] + G_up_v[0]]) - width / 2 * _perp

                        pick_name = pick_name + '_NAME1'
                        rotd = atand(vec_v[1] / vec_v[0])
                        text_add, element_max = plot_name_dxf(
                            base_point, width, element_max, pick_name, rotd,
                            node1, node2, name_class, 0)
                        text_name += text_add

            else:
                print('柱梁タイプが定義されていません')
                raise RuntimeError('MATLAB版stop相当: 柱梁タイプが定義されていません')

        elif axis_element_type[i_element] == 3:  # トラス要素の検定値plot%

            # プロット部材の断面番号取得
            sec_no = element[on_axis_element_index[i_element], 2]

            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')
            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            # プロット部材の要素番号取得
            ele_no = element[on_axis_element_index[i_element], 0]

            section_size = sectionget(ele_no, element, sections, limit_sec_no)
            # プロット部材の構成節点
            nodeifig_index, nodejfig_index = node_index_get(
                ax_ele[i_element], element, node)

            if sec_no >= limit_sec_no:
                # ダミー材でないかのチェック
                pass
            else:

                # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
                text_add, element_max = plot_line_dxf(
                    nodeifig_index, nodejfig_index, ele_no, node, base_point,
                    0, element_max, single_class, [0, 0], [2, 4], 0)
                text_single += text_add

                # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                # 注意(MATLAB踏襲): c_b_indexはこの分岐内で再計算されず，
                # 前回ループ(梁要素)の値をそのまま使用する
                # (最初の要素がトラスの場合MATLAB版は未定義変数エラー →
                #  PythonではNameError)
                if section_size[-2] == 1 or section_size[-2] == 2:  # H鋼と■断面
                    width = (section_size[2] * abs(cosd(c_b_index[1]))
                             + section_size[1] * abs(sind(c_b_index[1])))
                elif section_size[-2] == 3 or section_size[-2] == 4:
                    width = section_size[1]
                else:
                    print('断面タイプ未対応')
                ij_add = end_add(ele_no, element_on_axis_pick, node, ele_end,
                                 c_b_index[0], width, sections, limit_sec_no,
                                 axis_direction, i_axis, 0, girder_no_on,
                                 element)

                if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                    nodeifig_index2 = nodejfig_index
                    nodejfig_index2 = nodeifig_index
                    nodeifig_index = nodeifig_index2
                    nodejfig_index = nodejfig_index2
                    ij_add = np.array([ij_add[1], ij_add[0]])

                vec_v = np.array(
                    [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                     node[nodejfig_index, 3] - node[nodeifig_index, 3]])
                vec_v = vec_v / np.linalg.norm(vec_v)

                text_add, element_max = plot_line_dxf(
                    nodeifig_index, nodejfig_index, ele_no, node, base_point,
                    width, element_max, girder_class, -3 * ij_add, [2, 4], 0)
                text_double += text_add

                # 幅のあるポリラインの点を作成
                _perp = np.array([-1 * vec_v[1], vec_v[0]])
                node1 = np.array([node[nodeifig_index, 1],
                                  node[nodeifig_index, 3]]) + width / 2 * _perp
                node4 = np.array([node[nodeifig_index, 1],
                                  node[nodeifig_index, 3]]) - width / 2 * _perp
                node2 = np.array([node[nodejfig_index, 1],
                                  node[nodejfig_index, 3]]) + width / 2 * _perp
                node3 = np.array([node[nodejfig_index, 1],
                                  node[nodejfig_index, 3]]) - width / 2 * _perp

                pick_name = pick_name + '_NAME1'
                rotd = atand(vec_v[1] / vec_v[0])
                text_add, element_max = plot_name_dxf(
                    base_point, width, element_max, pick_name, rotd,
                    node1, node2, name_class, 0)
                text_name += text_add

    # %%%%%%%%%%%%%%%%%%%%%%%結合部材のplot%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    for i_unit in range(len(unit_element)):

        # 結合部材を構成する全ての部材が当該通り芯内にあるかどうかの検討
        if _isempty(ax_ele):
            pass
        else:
            # MATLABのfind_indexは1-based(不一致=0)なので +1 して総和・総積を再現
            _fi = np.atleast_1d(find_index(ax_ele, unit_element[i_unit][1]))
            sum_group = np.sum(_fi + 1)
            prod_group = np.prod(_fi + 1)

            ele_no = np.asarray(unit_element[i_unit][1]).ravel()[0]
            sec_no = element[find_index(element[:, 0], ele_no), 2]

            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')

            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            if sum_group > 0 and prod_group > 0:

                text_single, text_double, text_name, element_max = unit_element_dxf(
                    unit_element[i_unit][1], unit_element[i_unit][2],
                    element, node, limit_sec_no, pick_name, sections,
                    base_point, element_max, text_single, text_double,
                    text_name, on_axis_element_index, ele_end,
                    axis_direction, i_axis, single_class, column_class,
                    girder_class, name_class, Glevel, girder_no_on)

            elif sum_group == 0 and prod_group == 0:
                pass
            else:
                print('結合要素作成でエラー！？')
                # stop (元コードでもコメントアウト)

    # %%%%%%%%%%%%%%%%%%%%%%%壁要素のplot%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if _isempty(on_axis_wall_index):
        pass
    else:
        on_axis_wall_index = find_index(wall_base[:, 0], on_axis_wall_ID)

    return text_single, text_double, text_name, element_max


# 元: figure_dxf.m L557-693 (サブ関数)
def unit_element_dxf(unit_element, unit_node, element, node, limit_sec_no,
                     pick_name, sections, base_point, element_max,
                     text_single, text_double, text_name,
                     on_axis_element_index, ele_end, axis_direction, i_axis,
                     single_class, column_class, girder_class, name_class,
                     Glevel, girder_no_on):

    unit_element = np.atleast_1d(np.asarray(unit_element, dtype=float)).ravel()
    unit_node = np.atleast_1d(np.asarray(unit_node, dtype=float)).ravel()

    # 応力をplotしていいかの判別
    unit_ele_in = np.atleast_1d(find_index(element[:, 0], unit_element))
    unit_ele_sec_no = element[unit_ele_in, 2]
    sec_no = unit_ele_sec_no[0]
    unit_ele_sec_no = (unit_ele_sec_no < limit_sec_no).astype(float)

    ele_no = unit_element
    section_size = sectionget(ele_no[0], element, sections, limit_sec_no)

    if np.sum(unit_ele_sec_no) == 0 and np.prod(unit_ele_sec_no) == 0:
        # 応力をプロットしない部材
        pass

    elif np.sum(unit_ele_sec_no) > 0 and np.prod(unit_ele_sec_no) > 0:
        # 全てプロットするのでOK

        # この要素がcolumnかbeamか，またbeta角度を保存している．
        axiselement_index = find_index(element[:, 0], unit_element[0])
        c_b_index = np.array(
            [column_beam_judge(axiselement_index, element, node)[0],
             element[axiselement_index, 5]])

        nodeifig_index = find_index(node[:, 0], unit_node[0])
        nodejfig_index = find_index(node[:, 0], unit_node[unit_node.size - 1])

        # MATLABの element(on_axis_element_index(find(on_axis_element_index)),:)
        element_on_axis_pick = element[
            on_axis_element_index[np.where(on_axis_element_index != -1)[0]], :]

        # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
        text_add, element_max = plot_line_dxf(
            nodeifig_index, nodejfig_index, ele_no[0], node, base_point, 0,
            element_max, single_class, [0, 0], [2, 4], 0)
        text_single += text_add

        vec_v = np.array(
            [node[nodejfig_index, 1] - node[nodeifig_index, 1],
             node[nodejfig_index, 3] - node[nodeifig_index, 3]])
        vec_v = vec_v / np.linalg.norm(vec_v)

        if c_b_index[0] == 2:
            # 柱サイズでダブルラインをdxf(テキスト)で書き出し
            if (section_size[-2] == 1 or section_size[-2] == 2
                    or section_size[-2] == 6 or section_size[-2] == 7):
                width = (section_size[1]
                         * abs(axis_direction[i_axis, 0] * cosd(c_b_index[1])
                               + axis_direction[i_axis, 1] * sind(c_b_index[1]))
                         + section_size[2]
                         * math.sqrt(1 - (axis_direction[i_axis, 0] * cosd(c_b_index[1])
                                          + axis_direction[i_axis, 1] * sind(c_b_index[1])) ** 2))
            elif section_size[-2] == 4 or section_size[-2] == 3:
                width = section_size[1]
            ij_add1 = end_add(ele_no[0], element_on_axis_pick, node, ele_end,
                              c_b_index[0], width, sections, limit_sec_no,
                              axis_direction, i_axis, Glevel, girder_no_on,
                              element)
            ij_add2 = end_add(ele_no[ele_no.size - 1], element_on_axis_pick,
                              node, ele_end, c_b_index[0], width, sections,
                              limit_sec_no, axis_direction, i_axis, Glevel,
                              girder_no_on, element)
            ij_add = np.array([ij_add1[0], ij_add2[1]])

            text_add, element_max = plot_line_dxf(
                nodeifig_index, nodejfig_index, ele_no[0], node, base_point,
                width, element_max, column_class, ij_add, [2, 4], 0)
            text_double += text_add

            # 幅のあるポリラインの点を作成
            _perp = np.array([-1 * vec_v[1], vec_v[0]])
            node1 = np.array([node[nodeifig_index, 1],
                              node[nodeifig_index, 3]]) + width / 2 * _perp
            node4 = np.array([node[nodeifig_index, 1],
                              node[nodeifig_index, 3]]) - width / 2 * _perp
            node2 = np.array([node[nodejfig_index, 1],
                              node[nodejfig_index, 3]]) + width / 2 * _perp
            node3 = np.array([node[nodejfig_index, 1],
                              node[nodejfig_index, 3]]) - width / 2 * _perp

            pick_name = pick_name + '_NAME1'
            rotd = 0
            # MATLAB版はG_up引数を渡していない(plot_name_dxf内で未使用) → 0を渡す
            text_add, element_max = plot_name_dxf(
                base_point, width, element_max, pick_name, rotd,
                node3, node4, name_class, 0)
            text_name += text_add

        elif c_b_index[0] == 1 or c_b_index[0] == 1.9:

            # 梁サイズでダブルラインをdxf(テキスト)で書き出し
            if section_size[-2] == 1 or section_size[-2] == 6:
                width = (section_size[1] * abs(cosd(c_b_index[1]))
                         + section_size[2] * abs(sind(c_b_index[1])))
            elif section_size[-2] == 2 or section_size[-2] == 7:
                width = (section_size[1] * abs(cosd(c_b_index[1]))
                         + section_size[2] * abs(sind(c_b_index[1])))
            elif section_size[-2] == 4 or section_size[-2] == 3:
                width = section_size[1]
            elif section_size[-2] == 5 or section_size[-2] == 9:
                width = (section_size[1] * abs(cosd(c_b_index[1]))
                         + section_size[2] * abs(sind(c_b_index[1])))
            elif section_size[-2] == 10:
                width = (section_size[1] * abs(cosd(c_b_index[1]))
                         + section_size[2] * abs(sind(c_b_index[1])))
            elif section_size[-2] == 12:
                width1 = (section_size[1] * abs(cosd(c_b_index[1]))
                          + section_size[2] * abs(sind(c_b_index[1])))
                width3 = (section_size[3] * abs(cosd(c_b_index[1]))
                          + section_size[4] * abs(sind(c_b_index[1])))
                width = np.array([width1, width3])

            ij_add1 = end_add(ele_no[0], element_on_axis_pick, node, ele_end,
                              c_b_index[0], width, sections, limit_sec_no,
                              axis_direction, i_axis, 0, girder_no_on,
                              element)
            ij_add2 = end_add(ele_no[ele_no.size - 1], element_on_axis_pick,
                              node, ele_end, c_b_index[0], width, sections,
                              limit_sec_no, axis_direction, i_axis, 0,
                              girder_no_on, element)
            ij_add = np.array([ij_add1[0], ij_add2[1]])

            if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                nodeifig_index2 = nodejfig_index
                nodejfig_index2 = nodeifig_index
                nodeifig_index = nodeifig_index2
                nodejfig_index = nodejfig_index2

                ij_add = np.array([ij_add[1], ij_add[0]])

            vec_v = np.array(
                [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                 node[nodejfig_index, 3] - node[nodeifig_index, 3]])
            vec_v = vec_v / np.linalg.norm(vec_v)

            G_up = _Glevel_G_up(Glevel, sec_no, width)

            text_add, element_max = plot_line_dxf(
                nodeifig_index, nodejfig_index, ele_no[0], node, base_point,
                width, element_max, girder_class, ij_add, [2, 4], G_up)
            text_double += text_add

            # 幅のあるポリラインの点を作成
            # (MATLAB版はここではwidthが2要素でもG_up(1)のみ・width/2を
            #  そのまま要素ごとに乗算 → numpyの要素積で同一挙動)
            G_up_v = np.atleast_1d(np.asarray(G_up, dtype=float))
            _perp = np.array([-1 * vec_v[1], vec_v[0]])
            node1 = np.array([node[nodeifig_index, 1],
                              node[nodeifig_index, 3] + G_up_v[0]]) + width / 2 * _perp
            node4 = np.array([node[nodeifig_index, 1],
                              node[nodeifig_index, 3] + G_up_v[0]]) - width / 2 * _perp
            node2 = np.array([node[nodejfig_index, 1],
                              node[nodejfig_index, 3] + G_up_v[0]]) + width / 2 * _perp
            node3 = np.array([node[nodejfig_index, 1],
                              node[nodejfig_index, 3] + G_up_v[0]]) - width / 2 * _perp

            rotd = atand(vec_v[1] / vec_v[0])
            pick_name = pick_name + '_NAME1'
            text_add, element_max = plot_name_dxf(
                base_point, width, element_max, pick_name, rotd,
                node1, node2, name_class, 0)
            text_name += text_add

        else:
            ERROR = '柱梁タイプが定義されていません'
            print(ERROR)  # MATLAB: セミコロン無し代入(コマンドウィンドウ表示)

    return text_single, text_double, text_name, element_max


# 元: end_add.m L1-231
def end_add(ele_no, element_on_axis, node, ele_end, c_b_index, width,
            sections, limit_sec_no, axis_direction, i_axis, Glevel,
            girder_no_on, element):
    if np.size(width) == 2:  # MATLAB: size(width,2)==2
        width = np.max(width)

    nodeij = element_on_axis[
        np.atleast_1d(find_zero_index(element_on_axis[:, 0] - ele_no)).astype(int),
        3:5]
    # MATLABの線形インデックス nodeij(i) 相当 (通常は1行のみヒット)
    nodeij = nodeij.ravel(order='F')
    ij_add = np.zeros(2) - 10000
    ij_add_g = np.zeros(2)
    nodeij_info = node[np.atleast_1d(find_index(node[:, 0], nodeij)).astype(int), :]

    if nodeij_info[0, 3] < nodeij_info[1, 3]:
        ij_order = np.array([-1.0, 1.0])
    else:
        ij_order = np.array([1.0, -1.0])

    for i in range(2):
        # 端部接合条件抽出
        if _isempty(ele_end):
            joint = 0
        elif find_index(ele_end[:, 0], ele_no) != -1:  # MATLAB: >0
            joint = ele_end[find_index(ele_end[:, 0], ele_no), i + 1]
        else:
            joint = 0

        # 端部接続要素抽出
        element_co = np.concatenate(
            (find_zero_index(element_on_axis[:, 3] - nodeij[i]),
             find_zero_index(element_on_axis[:, 4] - nodeij[i]))).astype(int)
        element_co = element_on_axis[element_co, :]
        element_co = element_co[np.where(element_co[:, 0] - ele_no)[0], :]

        if _isempty(element_co) and _isempty(girder_no_on):
            pass
        else:
            if _isempty(element_co):
                pass
            else:
                # MATLAB: column_beam_judge(1:size(element_co,1),...) → 全行(0-based)
                c_b_result = column_beam_judge(
                    np.arange(element_co.shape[0]), element_co, node)
                cut_index = []
                for j in range(element_co.shape[0]):
                    if c_b_result[j] == 3 - c_b_index:  # 柱と梁
                        pass
                    elif c_b_result[j] == 3.9 - c_b_index:  # 柱と斜め梁
                        pass
                    else:
                        cut_index.append(j)
                element_co = np.delete(element_co, cut_index, axis=0)

            if _isempty(element_co) and _isempty(girder_no_on):
                pass
            elif c_b_index == 2:  # 柱
                # 通り外で接続する梁を探す
                if _isempty(girder_no_on):
                    # MATLAB: find_index(element(:,1),girder_no_on(:,1))→[] で空行列
                    element_on = element[np.zeros(0, dtype=int), :]
                else:
                    element_on = element[
                        np.atleast_1d(find_index(element[:, 0],
                                                 girder_no_on[:, 0])).astype(int), :]
                element_id_on = find_zero_index(element_on[:, 3] - nodeij[i])
                element_id_on = np.concatenate(
                    (element_id_on,
                     find_zero_index(element_on[:, 4] - nodeij[i]))).astype(int)
                element_no_on = element_on[element_id_on, 0]
                for j in range(element_no_on.size):

                    # 注意(MATLABバグ踏襲): element_id_onはelement_on(通り外梁の
                    # 部分行列)の行indexだが，column_beam_judge/element(...,6)は
                    # 全体elementに適用している
                    c_b_index2 = np.array(
                        [column_beam_judge(element_id_on[j], element, node)[0],
                         element[element_id_on[j], 5]])
                    section_size = sectionget(element_no_on[j], element,
                                              sections, limit_sec_no)
                    sec_no = section_size[0]

                    if (section_size[-2] == 1 or section_size[-2] == 2
                            or section_size[-2] == 5 or section_size[-2] == 7
                            or section_size[-2] == 9 or section_size[-2] == 10):
                        width2 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                  + section_size[2] * abs(sind(c_b_index2[1])))
                    elif section_size[-2] == 3:
                        width2 = section_size[1]
                    elif section_size[-2] == 4:
                        width2 = section_size[1]
                    elif section_size[-2] == 12:
                        width1 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                  + section_size[2] * abs(sind(c_b_index2[1])))
                        width3 = (section_size[3] * abs(cosd(c_b_index2[1]))
                                  + section_size[4] * abs(sind(c_b_index2[1])))
                        width2 = max(width1, width3)

                    G_up = _Glevel_G_up(Glevel, sec_no, width2)

                    if ij_add_g[i] < width2 / 2:
                        if joint == 0:
                            ij_add_g[i] = width2 / 2 + G_up * ij_order[i]
                        else:
                            ij_add_g[i] = (-1 * width2 / 2 - width / 2
                                           + G_up * ij_order[i])

                for j in range(element_co.shape[0]):  # MATLAB: for j=1:size(element_co)
                    c_b_index2 = np.array(
                        [column_beam_judge(j, element_co, node)[0],
                         element_co[j, 5]])
                    section_size = sectionget(element_co[j, 0], element_co,
                                              sections, limit_sec_no)
                    sec_no = section_size[0]
                    if sec_no > limit_sec_no:
                        pass
                    else:
                        if section_size[-2] == 1:
                            width2 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                      + section_size[2] * abs(sind(c_b_index2[1])))
                        elif section_size[-2] == 2 or section_size[-2] == 7:
                            width2 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                      + section_size[2] * abs(sind(c_b_index2[1])))
                        elif section_size[-2] == 3 or section_size[-2] == 4:
                            width2 = section_size[1]
                        elif section_size[-2] == 10:
                            width2 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                      + section_size[2] * abs(sind(c_b_index2[1])))
                        elif section_size[-2] == 12:
                            width1 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                      + section_size[2] * abs(sind(c_b_index2[1])))
                            width3 = (section_size[3] * abs(cosd(c_b_index2[1]))
                                      + section_size[4] * abs(sind(c_b_index2[1])))
                            width2 = max(width1, width3)

                        sec_no = section_size[0]
                        G_up = _Glevel_G_up(Glevel, sec_no, width2)

                        if ij_add[i] < width2 / 2:
                            if joint == 0:
                                ij_add[i] = width2 / 2 + G_up * ij_order[i]
                            else:
                                ij_add[i] = (-1 * width2 / 2 - width / 2
                                             + G_up * ij_order[i])

            elif c_b_index == 1 or c_b_index == 1.9:

                # 通り外で接続する梁を探す
                if _isempty(girder_no_on):
                    _girder_rows = np.zeros(0, dtype=int)
                else:
                    _girder_rows = np.atleast_1d(
                        find_index(element[:, 0], girder_no_on[:, 0])).astype(int)
                element_id_on = find_zero_index(
                    element[_girder_rows, 3] - nodeij[i])
                element_id_on = np.concatenate(
                    (element_id_on,
                     find_zero_index(element[_girder_rows, 4] - nodeij[i]))).astype(int)
                # 注意(MATLABバグ踏襲): element_id_onは部分行列の行indexだが
                # 全体elementの行indexとして使用している
                element_no_on = element[element_id_on, 0]
                for j in range(element_no_on.size):

                    c_b_index2 = np.array(
                        [column_beam_judge(element_id_on[j], element, node)[0],
                         element[element_id_on[j], 5]])
                    section_size = sectionget(element_no_on[j], element,
                                              sections, limit_sec_no)
                    sec_no = section_size[0]
                    if sec_no > limit_sec_no:
                        pass
                    else:
                        if section_size[-2] == 1 or section_size[-2] == 6:
                            width2 = (section_size[2] * abs(cosd(c_b_index2[1]))
                                      + section_size[1] * abs(sind(c_b_index2[1])))
                        elif section_size[-2] == 2 or section_size[-2] == 7:
                            width2 = (section_size[2] * abs(cosd(c_b_index2[1]))
                                      + section_size[1] * abs(sind(c_b_index2[1])))
                        elif section_size[-2] == 3 or section_size[-2] == 4:
                            width2 = section_size[1]
                        elif section_size[-2] == 12:
                            width1 = (section_size[2] * abs(cosd(c_b_index2[1]))
                                      + section_size[1] * abs(sind(c_b_index2[1])))
                            width3 = (section_size[4] * abs(cosd(c_b_index2[1]))
                                      + section_size[3] * abs(sind(c_b_index2[1])))
                            width2 = max(width1, width3)

                        if ij_add_g[i] < width2 / 2:
                            if joint == 0:
                                ij_add_g[i] = width2 / 2
                            else:
                                ij_add_g[i] = -1 * width2 / 2 - width / 2

                for j in range(element_co.shape[0]):  # MATLAB: for j=1:size(element_co)
                    c_b_index2 = np.array(
                        [column_beam_judge(j, element_co, node)[0],
                         element_co[j, 5]])
                    section_size = sectionget(element_co[j, 0], element_co,
                                              sections, limit_sec_no)
                    sec_no = section_size[0]
                    if sec_no > limit_sec_no:
                        pass
                    else:
                        if section_size[-2] == 1 or section_size[-2] == 6:
                            width2 = (section_size[1]
                                      * abs(axis_direction[i_axis, 0] * cosd(c_b_index2[1])
                                            + axis_direction[i_axis, 1] * sind(c_b_index2[1]))
                                      + section_size[2]
                                      * math.sqrt(1 - (axis_direction[i_axis, 0] * cosd(c_b_index2[1])
                                                       + axis_direction[i_axis, 1] * sind(c_b_index2[1])) ** 2))
                        elif section_size[-2] == 2 or section_size[-2] == 7:
                            width2 = (section_size[1]
                                      * abs(axis_direction[i_axis, 0] * cosd(c_b_index2[1])
                                            + axis_direction[i_axis, 1] * sind(c_b_index2[1]))
                                      + section_size[2]
                                      * math.sqrt(1 - (axis_direction[i_axis, 0] * cosd(c_b_index2[1])
                                                       + axis_direction[i_axis, 1] * sind(c_b_index2[1])) ** 2))
                        elif section_size[-2] == 4 or section_size[-2] == 3:
                            width2 = section_size[1]
                        elif section_size[-2] == 12:
                            width1 = (section_size[1] * abs(cosd(c_b_index2[1]))
                                      + section_size[2] * abs(sind(c_b_index2[1])))
                            width3 = (section_size[3] * abs(cosd(c_b_index2[1]))
                                      + section_size[4] * abs(sind(c_b_index2[1])))
                            width2 = max(width1, width3)
                        if ij_add[i] < width2 / 2:
                            if joint == 0:
                                ij_add[i] = width2 / 2
                            else:
                                ij_add[i] = -1 * width2 / 2 - width / 2

        if ij_add[i] == -10000:
            ij_add[i] = ij_add_g[i]

    return ij_add



# ========================================================================
# figure_dxf_floor.m / end_add_plan.m
# ========================================================================

# -*- coding: utf-8 -*-
# ============================================================
# part_floor.py : 伏図(水平構面)DXF出力
# 元コード: figure_dxf_floor.m (メイン関数 + サブ関数 unit_element_dxf),
#           end_add_plan.m
# (import文なし。np / math / part_common.py のヘルパーが定義済み前提)
# ============================================================


# 元: figure_dxf_floor.m L1-501
# i_axis は 0-based (MATLAB版は1-based)。
# text_single/text_double/text_name は list[str]。
def figure_dxf_floor(limit_sec_no, unit_element, axis_element,
                     axis_node, node, wall_base, wall_element, element,
                     uniele_ID, axis_name, i_axis,
                     plate, beam, truss, section_nos, section_name,
                     frm_rls, constraint,
                     text_single, text_double, text_name, base_point,
                     sections, element_max, single_class, column_class,
                     girder_class, column_section_class, name_class):
    # 呼び出し側リストとのエイリアスを避けるためコピーして蓄積(内容はMATLABと同一)
    text_single = list(text_single)
    text_double = list(text_double)
    text_name = list(text_name)

    # axis_element{i_axis,1} (通芯上の要素番号 行ベクトル) の別名
    axis_ele_no = np.asarray(axis_element[i_axis][0], dtype=float).ravel()

    # beamにある要素番号から通芯上の梁要素とりだし
    if _isempty(beam):
        # MATLAB: on_axis_beam_index=0 → find(0)=[] で空になる
        on_axis_beam_index = np.array([], dtype=int)
    else:
        fi = np.atleast_1d(find_index(beam[:, 0], axis_ele_no))
        # 0が入力されているものをはじいて，beam要素であるもののindexを保持
        # (MATLAB: find(on_axis_beam_index)。Python版find_indexは未発見=-1)
        on_axis_beam_index = np.where(fi != -1)[0]

    # trussにある要素番号から通芯上の梁要素とりだし
    if _isempty(truss):
        on_axis_truss_index = np.array([], dtype=int)
        truss_sec_no = 0
    else:
        fi = np.atleast_1d(find_index(truss[:, 0], axis_ele_no))
        truss_sec_no = doublecheck(truss[:, 2])
        # 0が入力されているものをはじいて，truss要素であるもののindexを保持
        on_axis_truss_index = np.where(fi != -1)[0]

    # 板要素
    if _isempty(plate):
        on_axis_plate_index = np.array([], dtype=int)
    else:
        fi = np.atleast_1d(find_index(plate[:, 0], axis_ele_no))
        # 0が入力されているものをはじいて，plate要素であるもののindexを保持
        on_axis_plate_index = np.where(fi != -1)[0]

    # wall_stressにある要素番号から通芯上のtruss要素とりだし
    if _isempty(wall_base):
        on_axis_wall_index = np.array([], dtype=int)
    else:
        fi = np.atleast_1d(find_index(wall_base[:, 0], axis_ele_no))
        # 0が入力されているものをはじいて，wall要素であるもののindexを保持
        on_axis_wall_index = np.where(fi != -1)[0]
    on_axis_wall_ID = axis_ele_no[on_axis_wall_index]

    # trussには3，beamには1をふる(この数字で梁かトラスかを以下で判別する．)
    # MATLAB: axis_element{i_axis,2}(1:n)=0; ... の再現
    _flags = np.zeros(axis_ele_no.size)
    _flags[on_axis_truss_index] = 3
    _flags[on_axis_beam_index] = 1
    _flags[on_axis_wall_index] = 2
    _flags[on_axis_plate_index] = 4
    if len(axis_element[i_axis]) < 2:
        axis_element[i_axis].append(_flags)
    else:
        axis_element[i_axis][1] = _flags

    on_axis_element_index = np.atleast_1d(
        find_index(element[:, 0], axis_ele_no))

    num_element_on_axis = axis_ele_no.size

    # 材端ピン設定の取り出し
    if _isempty(frm_rls):
        ele_end = np.zeros((0, 3))  # MATLAB: []
    else:
        on_element = doublecheck(frm_rls[:, 0])
        rls_index = np.atleast_1d(find_index(axis_ele_no, on_element))
        rls_index = rls_index[rls_index != -1]  # MATLAB: rls_index(find(rls_index))
        ele_end = np.zeros((rls_index.size, 3))
        for i in range(rls_index.size):
            ele_no = axis_ele_no[rls_index[i]]
            ele_end[i, 0] = ele_no
            # frm_rlsはi端行の直後にj端行が並ぶ前提(MATLAB版と同じ)
            if frm_rls[find_index(frm_rls[:, 0], ele_no), 1] > 0:
                ele_end[i, 1] = 1
            if frm_rls[find_index(frm_rls[:, 0], ele_no) + 1, 1] > 0:
                ele_end[i, 2] = 1

    # %%%%%%%%%%%%%%%%%%%%%%%%柱データをプロット%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    node_no_on = np.asarray(axis_node[i_axis][0], dtype=float).ravel()
    not_on_element = element.copy()
    # MATLAB: not_on_element(on_axis_element_index(find(on_axis_element_index)),:)=[]
    not_on_element = np.delete(
        not_on_element,
        on_axis_element_index[on_axis_element_index != -1], axis=0)
    column_no_on = np.array([])  # MATLAB: []
    if _isempty(not_on_element):
        pass
    else:
        for j in range(node_no_on.size):
            element_id_on = find_zero_index(not_on_element[:, 3] - node_no_on[j])  # ｉ端
            element_id_on = np.concatenate(
                [element_id_on,
                 find_zero_index(not_on_element[:, 4] - node_no_on[j])])  # ｊ端
            element_no_on = not_on_element[element_id_on, :]
            # MATLAB: element_no_on(find(element_no_on(:,3)>limit_sec_no),:)=[]
            element_no_on = np.delete(
                element_no_on,
                np.where(element_no_on[:, 2] > limit_sec_no)[0], axis=0)
            # 1列目(要素番号)のみ保持。MATLAB同様、後で列2,3が代入されると幅3へ拡張する
            element_no_on = element_no_on[:, 0:1].copy()
            if _isempty(element_no_on):
                pass
            else:
                for jj in range(element_no_on.shape[0]):
                    # 注意: ダミー断面除去後、element_id_on と element_no_on の行が
                    # ずれる場合があるが、MATLAB版の挙動をそのまま踏襲する
                    if not_on_element[element_id_on[jj], 3] == node_no_on[j]:  # ｉ端が算定高さにある
                        end_node_id = find_index(node[:, 0], not_on_element[element_id_on[jj], 4])  # ｊ端
                        if element_no_on.shape[1] < 3:  # MATLABの代入による自動拡張を再現
                            element_no_on = np.hstack(
                                [element_no_on,
                                 np.zeros((element_no_on.shape[0], 2))])
                        if node[end_node_id, 3] == node[find_index(node[:, 0], node_no_on[j]), 3]:
                            element_no_on[jj, 1] = 0
                            element_no_on[jj, 2] = 0
                        elif node[end_node_id, 3] > node[find_index(node[:, 0], node_no_on[j]), 3]:
                            element_no_on[jj, 1] = node_no_on[j]
                            element_no_on[jj, 2] = node[end_node_id, 0]
                        else:
                            element_no_on[jj, 1] = -1 * node_no_on[j]
                            element_no_on[jj, 2] = node[end_node_id, 0]

                    elif not_on_element[element_id_on[jj], 4] == node_no_on[j]:  # ｊ端が算定高さにある
                        end_node_id = find_index(node[:, 0], not_on_element[element_id_on[jj], 3])
                        if element_no_on.shape[1] < 3:
                            element_no_on = np.hstack(
                                [element_no_on,
                                 np.zeros((element_no_on.shape[0], 2))])
                        if node[end_node_id, 3] == node[find_index(node[:, 0], node_no_on[j]), 3]:
                            element_no_on[jj, 1] = 0
                            element_no_on[jj, 2] = 0
                        elif node[end_node_id, 3] > node[find_index(node[:, 0], node_no_on[j]), 3]:
                            element_no_on[jj, 1] = node_no_on[j]
                            element_no_on[jj, 2] = node[end_node_id, 0]
                        else:
                            element_no_on[jj, 1] = -1 * node_no_on[j]
                            element_no_on[jj, 2] = node[end_node_id, 0]
                    else:
                        element_no_on[jj, 0] = 0
                element_no_on = element_no_on[np.where(element_no_on[:, 0] != 0)[0], :]

                # 上へと連続する柱がある場合(符号と部材を配置)
                if element_no_on.shape[1] > 2 and np.max(element_no_on[:, 1]) > 0:
                    for jj in range(element_no_on.shape[0]):
                        if element_no_on[jj, 1] > 0:
                            end_node_id = find_index(node[:, 0], element_no_on[jj, 1])
                            end_node_id2 = find_index(node[:, 0], element_no_on[jj, 2])
                            sec_no = element[find_index(element[:, 0], element_no_on[jj, 0]), 2]
                            rotd = element[find_index(element[:, 0], element_no_on[jj, 0]), 5] + 90
                            pick_section_name = section_name[find_index(section_nos, sec_no)]

                            cut_index = pick_section_name.find('_')  # MATLAB: strfind
                            if cut_index == -1:
                                pick_name = pick_section_name
                            else:
                                pick_name = pick_section_name[:cut_index]
                            pick_name = space_erace(pick_name)
                            put_block = pick_name + '_section'
                            put_class = column_section_class

                            if sec_no > limit_sec_no:
                                pass
                            elif find_index(truss_sec_no, sec_no) == -1:  # MATLAB: ==0(未発見)

                                element_max = element_max + 1
                                text_column = ['  0', 'INSERT', '  8', put_class,
                                               '  5', dec2hex(element_max)]
                                text_column += ['  2', put_block,
                                                ' 10', num2str(base_point[0] * 1000 + node[end_node_id, 1] * 1000),
                                                ' 20', num2str(base_point[1] * 1000 + node[end_node_id, 2] * 1000),
                                                ' 30', '0.0', ' 50', num2str(rotd)]
                                text_double += text_column

                                put_block = pick_name + '_NAME1'
                                put_class = name_class

                                element_max = element_max + 1
                                text_column = ['  0', 'INSERT', '  8', put_class,
                                               '  5', dec2hex(element_max)]
                                text_column += ['  2', put_block,
                                                ' 10', num2str(base_point[0] * 1000 + node[end_node_id, 1] * 1000),
                                                ' 20', num2str(base_point[1] * 1000 + node[end_node_id, 2] * 1000),
                                                ' 30', '0.0']
                                text_double += text_column

                            elif find_index(truss_sec_no, sec_no) >= 0:  # MATLAB: >0(発見)

                                # プロット部材の要素番号取得
                                ele_no = element_no_on[jj, 0]
                                # プロット部材の断面サイズ取得
                                section_size = sectionget(ele_no, element, sections, limit_sec_no)
                                # プロット部材の構成節点
                                nodeifig_index = end_node_id
                                nodejfig_index = end_node_id2

                                node_new = np.vstack(
                                    [node,
                                     np.concatenate(
                                         [[np.max(node[:, 0]) + 1],
                                          0.6 * node[nodeifig_index, 1:4]
                                          + 0.4 * node[nodejfig_index, 1:4]])])
                                # MATLAB: nodejfig_index=size(node_new,1) (最終行) → 0-based化
                                nodejfig_index = node_new.shape[0] - 1

                                _dvec = np.array(
                                    [node_new[nodejfig_index, 1] - node_new[nodeifig_index, 1],
                                     node_new[nodejfig_index, 2] - node_new[nodeifig_index, 2]])
                                vec_v = _dvec / np.linalg.norm(_dvec)

                                # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
                                text_add, element_max = plot_line_dxf(
                                    nodeifig_index, nodejfig_index, ele_no, node_new,
                                    base_point, 0, element_max, single_class,
                                    [0, 0], [2, 3], 0)
                                text_single += text_add

                                # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                                if section_size[-2] == 1 or section_size[-2] == 2 or section_size[-2] == 7:  # H鋼と■断面
                                    width = section_size[2] * abs(cosd(rotd - 90)) + section_size[1] * abs(sind(rotd - 90))
                                elif section_size[-2] == 3 or section_size[-2] == 4:
                                    width = section_size[1]
                                else:
                                    print('断面タイプ未対応')
                                ij_add = np.array([0.0, 0.0])

                                text_add, element_max = plot_line_dxf(
                                    nodeifig_index, nodejfig_index, ele_no, node_new,
                                    base_point, width, element_max, girder_class,
                                    -3 * ij_add, [2, 3], 0)
                                text_double += text_add

                                # 幅のあるポリラインの点を作成
                                node1 = np.array([node_new[nodeifig_index, 1], node_new[nodeifig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                                node4 = np.array([node_new[nodeifig_index, 1], node_new[nodeifig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                                node2 = np.array([node_new[nodejfig_index, 1], node_new[nodejfig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                                node3 = np.array([node_new[nodejfig_index, 1], node_new[nodejfig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])

                                pick_name = pick_name + '_NAME1'
                                if vec_v[0] == 0:
                                    rotd = 90
                                else:
                                    rotd = atand(vec_v[1] / vec_v[0])
                                # MATLAB版はG_up引数省略(plot_name_dxf内で未使用)→0を渡す
                                text_add, element_max = plot_name_dxf(
                                    base_point, width, element_max, pick_name, rotd,
                                    node1, node2, name_class, 0)
                                text_name += text_add
                    # 下からの柱のみの場合(部材のみ配置)
                elif element_no_on.shape[1] > 2 and np.min(element_no_on[:, 1]) < 0:
                    for jj in range(element_no_on.shape[0]):
                        if element_no_on[jj, 1] < 0:
                            end_node_id = find_index(node[:, 0], -1 * element_no_on[jj, 1])
                            sec_no = element[find_index(element[:, 0], element_no_on[jj, 0]), 2]
                            rotd = element[find_index(element[:, 0], element_no_on[jj, 0]), 5] + 90
                            pick_section_name = section_name[find_index(section_nos, sec_no)]

                            cut_index = pick_section_name.find('_')
                            if cut_index == -1:
                                pick_name = pick_section_name
                            else:
                                pick_name = pick_section_name[:cut_index]
                            pick_name = space_erace(pick_name)
                            put_block = pick_name + '_section2'
                            put_class = column_class

                            if sec_no > limit_sec_no:
                                pass
                            else:
                                element_max = element_max + 1
                                text_column = ['  0', 'INSERT', '  8', put_class,
                                               '  5', dec2hex(element_max)]
                                text_column += ['  2', put_block,
                                                ' 10', num2str(base_point[0] * 1000 + node[end_node_id, 1] * 1000),
                                                ' 20', num2str(base_point[1] * 1000 + node[end_node_id, 2] * 1000),
                                                ' 30', '0.0', ' 50', num2str(rotd)]
                                text_double += text_column

            # MATLAB: column_no_on=[column_no_on;element_no_on] (空配列は無視される)
            if not _isempty(element_no_on):
                if _isempty(column_no_on):
                    column_no_on = element_no_on.copy()
                else:
                    column_no_on = np.vstack([column_no_on, element_no_on])

    # %%%%%%%%%%%%%%伏図上にある単一要素の芯線をプロット%%%%%%%%%%%%%%%%%%%%%%%
    for i_element in range(num_element_on_axis):
        if axis_element[i_axis][1][i_element] == 1:  # 梁要素のplot%%%%%%%%%%%%%%%

            # プロット部材の断面番号取得
            # (on_axis_element_indexが-1の場合、MATLAB版はindex0エラーで停止する)
            sec_no = element[on_axis_element_index[i_element], 2]

            # プロット部材の断面名取得
            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')
            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            # プロット部材の要素番号取得
            ele_no = element[on_axis_element_index[i_element], 0]
            # プロット部材の断面サイズ取得
            section_size = sectionget(ele_no, element, sections, limit_sec_no)
            # プロット部材の構成節点
            nodeifig_index, nodejfig_index = node_index_get(
                axis_ele_no[i_element], element, node)
            # プロット部材がcolumnかbeamか，またbeta角度を保存している．6つ目はβ角度
            c_b_index = np.concatenate(
                [column_beam_judge(on_axis_element_index[i_element], element, node),
                 [element[on_axis_element_index[i_element], 5]]])

            if sec_no >= limit_sec_no:
                # ダミー材でないかのチェック
                pass
            elif c_b_index[0] == 2:
                # 柱部材のプロットは伏図なので基本なし
                pass
            elif c_b_index[0] == 1 or c_b_index[0] == 1.9:
                # 梁部材のプロット(形状および符号)

                # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
                text_add, element_max = plot_line_dxf(
                    nodeifig_index, nodejfig_index, ele_no, node, base_point,
                    0, element_max, single_class, [0, 0], [2, 3], 0)
                text_single += text_add

                if _isempty(uniele_ID):
                    # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                    # (該当断面形式以外ではwidthが未定義/前回値のまま使われる
                    #  MATLAB版の挙動を踏襲)
                    if section_size[-2] == 1 or section_size[-2] == 2:
                        width = section_size[2] * abs(cosd(c_b_index[1])) + section_size[1] * abs(sind(c_b_index[1]))
                    ij_add = end_add_plan(
                        ele_no,
                        element[on_axis_element_index[on_axis_element_index != -1], :],
                        node, ele_end, c_b_index[0], width, sections,
                        limit_sec_no, column_no_on, element)

                    if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                        nodeifig_index2 = nodejfig_index
                        nodejfig_index2 = nodeifig_index
                        nodeifig_index = nodeifig_index2
                        nodejfig_index = nodejfig_index2

                        ij_add2 = np.array([ij_add[1], ij_add[0]])
                        ij_add = ij_add2
                    _dvec = np.array(
                        [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                         node[nodejfig_index, 2] - node[nodeifig_index, 2]])
                    vec_v = _dvec / np.linalg.norm(_dvec)

                    text_add, element_max = plot_line_dxf(
                        nodeifig_index, nodejfig_index, ele_no, node, base_point,
                        width, element_max, girder_class, ij_add, [2, 3], 0)
                    text_double += text_add

                    # 幅のあるポリラインの点を作成
                    node1 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                    node4 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                    node2 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                    node3 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])

                    pick_name = pick_name + '_NAME1'
                    if vec_v[0] == 0:
                        rotd = 90
                    else:
                        rotd = atand(vec_v[1] / vec_v[0])

                    text_add, element_max = plot_name_dxf(
                        base_point, width, element_max, pick_name, rotd,
                        node1, node2, name_class, 0)
                    text_name += text_add

                else:
                    # MATLAB: judge_unit=find_index(uniele_ID(:,1),ele_no)
                    judge_unit = find_index(np.asarray(uniele_ID).ravel(), ele_no)
                    if judge_unit == -1:  # MATLAB: ==0(未発見)
                        # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                        if (section_size[-2] == 1 or section_size[-2] == 2
                                or section_size[-2] == 5 or section_size[-2] == 9
                                or section_size[-2] == 11 or section_size[-2] == 7):
                            width = section_size[2] * abs(cosd(c_b_index[1])) + section_size[1] * abs(sind(c_b_index[1]))
                        ij_add = end_add_plan(
                            ele_no,
                            element[on_axis_element_index[on_axis_element_index != -1], :],
                            node, ele_end, c_b_index[0], width, sections,
                            limit_sec_no, column_no_on, element)

                        if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                            nodeifig_index2 = nodejfig_index
                            nodejfig_index2 = nodeifig_index
                            nodeifig_index = nodeifig_index2
                            nodejfig_index = nodejfig_index2

                            ij_add2 = np.array([ij_add[1], ij_add[0]])
                            ij_add = ij_add2
                        _dvec = np.array(
                            [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                             node[nodejfig_index, 2] - node[nodeifig_index, 2]])
                        vec_v = _dvec / np.linalg.norm(_dvec)

                        text_add, element_max = plot_line_dxf(
                            nodeifig_index, nodejfig_index, ele_no, node, base_point,
                            width, element_max, girder_class, ij_add, [2, 3], 0)
                        text_double += text_add

                        # 幅のあるポリラインの点を作成
                        node1 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                        node4 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                        node2 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                        node3 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])

                        if vec_v[0] == 0:
                            rotd = 90
                        else:
                            rotd = atand(vec_v[1] / vec_v[0])
                        pick_name = pick_name + '_NAME1'
                        text_add, element_max = plot_name_dxf(
                            base_point, width, element_max, pick_name, rotd,
                            node1, node2, name_class, 0)
                        text_name += text_add

            else:
                print('柱梁タイプが定義されていません')
                raise RuntimeError('MATLAB版stop相当: 柱梁タイプが定義されていません')

        elif axis_element[i_axis][1][i_element] == 3:  # トラス要素の検定値plot%

            # プロット部材の断面番号取得
            sec_no = element[on_axis_element_index[i_element], 2]

            # プロット部材の断面名取得
            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')
            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            # プロット部材の要素番号取得
            ele_no = element[on_axis_element_index[i_element], 0]
            # プロット部材の断面サイズ取得
            section_size = sectionget(ele_no, element, sections, limit_sec_no)
            # プロット部材の構成節点
            nodeifig_index, nodejfig_index = node_index_get(
                axis_ele_no[i_element], element, node)
            # プロット部材がcolumnかbeamか，またbeta角度を保存している．6つ目はβ角度
            c_b_index = np.concatenate(
                [column_beam_judge(on_axis_element_index[i_element], element, node),
                 [element[on_axis_element_index[i_element], 5]]])

            if sec_no >= limit_sec_no:
                # ダミー材でないかのチェック
                pass
            else:

                # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し
                text_add, element_max = plot_line_dxf(
                    nodeifig_index, nodejfig_index, ele_no, node, base_point,
                    0, element_max, single_class, [0, 0], [2, 3], 0)
                text_single += text_add

                # 梁サイズでダブルラインをdxf(テキスト)で書き出し
                if section_size[-2] == 1 or section_size[-2] == 2:  # H鋼と■断面
                    width = section_size[2] * abs(cosd(c_b_index[1])) + section_size[1] * abs(sind(c_b_index[1]))
                elif section_size[-2] == 3:
                    width = section_size[1]
                else:
                    print('断面タイプ未対応')
                ij_add = end_add_plan(
                    ele_no,
                    element[on_axis_element_index[on_axis_element_index != -1], :],
                    node, ele_end, c_b_index[0], width, sections,
                    limit_sec_no, column_no_on, element)

                if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                    nodeifig_index2 = nodejfig_index
                    nodejfig_index2 = nodeifig_index
                    nodeifig_index = nodeifig_index2
                    nodejfig_index = nodejfig_index2

                    ij_add2 = np.array([ij_add[1], ij_add[0]])
                    ij_add = ij_add2

                _dvec = np.array(
                    [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                     node[nodejfig_index, 2] - node[nodeifig_index, 2]])
                vec_v = _dvec / np.linalg.norm(_dvec)

                text_add, element_max = plot_line_dxf(
                    nodeifig_index, nodejfig_index, ele_no, node, base_point,
                    width, element_max, girder_class, -3 * ij_add, [2, 3], 0)
                text_double += text_add

                # 幅のあるポリラインの点を作成
                node1 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                node4 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                node2 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
                node3 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])

                pick_name = pick_name + '_NAME1'
                # MATLAB版はゼロ割ガードなし(vec_v(1)=0ならatand(Inf)=90となる)
                rotd = atand(vec_v[1] / vec_v[0])
                text_add, element_max = plot_name_dxf(
                    base_point, width, element_max, pick_name, rotd,
                    node1, node2, name_class, 0)
                text_name += text_add

    # %%%%%%%%%%%%%%%%%%%%%%%結合部材のplot%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    for i_unit in range(len(unit_element)):

        # 結合部材を構成する全ての部材が当該通り芯内にあるかどうかの検討
        if _isempty(axis_ele_no):
            pass
        else:
            # MATLAB版find_indexは1-based/0(未発見)なので、+1してから和・積を取り
            # 「全て見つかった/全て見つからない」の判定を逐語再現する
            _fi = np.atleast_1d(find_index(axis_ele_no, unit_element[i_unit][1]))
            sum_group = np.sum(_fi + 1)
            prod_group = np.prod(_fi + 1)

            ele_no = np.asarray(unit_element[i_unit][1]).ravel()[0]  # unit_element{i_unit,2}(1,1)
            sec_no = element[find_index(element[:, 0], ele_no), 2]

            pick_section_name = section_name[find_index(section_nos, sec_no)]
            cut_index = pick_section_name.find('_')

            if cut_index == -1:
                pick_name = pick_section_name
            else:
                pick_name = pick_section_name[:cut_index]

            if sum_group > 0 and prod_group > 0:
                pick_name = pick_name + '_NAME1'

                text_single, text_double, text_name, element_max = unit_element_dxf_floor(
                    unit_element[i_unit][1], unit_element[i_unit][2],
                    element, node, limit_sec_no, pick_name, sections, base_point,
                    element_max, text_single, text_double, text_name,
                    on_axis_element_index, ele_end, [1, 0], i_axis,
                    single_class, column_class, girder_class, name_class,
                    column_no_on)

            elif sum_group == 0 and prod_group == 0:
                pass
            else:
                print('結合要素作成でエラー！？')
                # stop (MATLAB版でもコメントアウト)

    # %%%%%%%%%%%%%%%%%%%%%%%壁要素のplot%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if _isempty(on_axis_wall_index):
        pass
    else:
        # (MATLAB版でも以後未使用の再計算)
        on_axis_wall_index = find_index(wall_base[:, 0], on_axis_wall_ID)

    return text_single, text_double, text_name, element_max


# 元: figure_dxf_floor.m L506-587 (サブ関数 unit_element_dxf)
# 名前衝突回避のため関数名を unit_element_dxf_floor とする
def unit_element_dxf_floor(unit_element, unit_node,
                           element, node, limit_sec_no, pick_name, sections,
                           base_point, element_max, text_single, text_double,
                           text_name, on_axis_element_index, ele_end,
                           axis_direction, i_axis, single_class, column_class,
                           girder_class, name_class, column_no_on):
    text_single = list(text_single)
    text_double = list(text_double)
    text_name = list(text_name)

    # 応力をplotしていいかの判別
    unit_ele_in = np.atleast_1d(find_index(element[:, 0], unit_element))
    unit_ele_sec_no = element[unit_ele_in, 2]
    unit_ele_sec_no = (unit_ele_sec_no < limit_sec_no).astype(float)

    ele_no = np.asarray(unit_element, dtype=float).ravel()
    section_size = sectionget(ele_no[0], element, sections, limit_sec_no)

    if np.sum(unit_ele_sec_no) == 0 and np.prod(unit_ele_sec_no) == 0:
        # 応力をプロットしない部材
        pass

    elif np.sum(unit_ele_sec_no) > 0 and np.prod(unit_ele_sec_no) > 0:
        # 全てプロットするのでOK

        # この要素がcolumnかbeamか，またbeta角度を保存している．
        axiselement_index = find_index(element[:, 0], ele_no[0])
        c_b_index = np.concatenate(
            [column_beam_judge(axiselement_index, element, node),
             [element[axiselement_index, 5]]])

        _un = np.asarray(unit_node, dtype=float).ravel()
        nodeifig_index = find_index(node[:, 0], _un[0])
        # MATLAB: unit_node(size(unit_node,2)) = 行ベクトルの最終要素
        nodejfig_index = find_index(node[:, 0], _un[_un.size - 1])

        # 通り上にある単一要素の芯線をdxf(テキスト)で書き出し

        if c_b_index[0] == 2:
            pass

        elif c_b_index[0] == 1 or c_b_index[0] == 1.9:
            text_add, element_max = plot_line_dxf(
                nodeifig_index, nodejfig_index, ele_no[0], node, base_point,
                0, element_max, single_class, [0, 0], [2, 3], 0)
            text_single += text_add

            # 梁サイズでダブルラインをdxf(テキスト)で書き出し
            if (section_size[-2] == 1 or section_size[-2] == 2 or section_size[-2] == 5
                    or section_size[-2] == 6 or section_size[-2] == 7 or section_size[-2] == 9
                    or section_size[-2] == 10 or section_size[-2] == 11):
                width = section_size[2] * abs(cosd(c_b_index[1])) + section_size[1] * abs(sind(c_b_index[1]))
            ij_add1 = end_add_plan(
                ele_no[0],
                element[on_axis_element_index[on_axis_element_index != -1], :],
                node, ele_end, c_b_index[0], width, sections, limit_sec_no,
                column_no_on, element)
            # MATLAB: ele_no(size(ele_no,2)) = 行ベクトルの最終要素
            ij_add2 = end_add_plan(
                ele_no[ele_no.size - 1],
                element[on_axis_element_index[on_axis_element_index != -1], :],
                node, ele_end, c_b_index[0], width, sections, limit_sec_no,
                column_no_on, element)
            ij_add = np.array([ij_add1[0], ij_add2[1]])

            if node[nodeifig_index, 1] > node[nodejfig_index, 1]:
                nodeifig_index2 = nodejfig_index
                nodejfig_index2 = nodeifig_index
                nodeifig_index = nodeifig_index2
                nodejfig_index = nodejfig_index2

                ij_add2[0] = ij_add[1]
                ij_add2[1] = ij_add[0]
                ij_add = ij_add2
            _dvec = np.array(
                [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                 node[nodejfig_index, 2] - node[nodeifig_index, 2]])
            vec_v = _dvec / np.linalg.norm(_dvec)

            text_add, element_max = plot_line_dxf(
                nodeifig_index, nodejfig_index, ele_no[0], node, base_point,
                width, element_max, girder_class, ij_add, [2, 3], 0)
            text_double += text_add

            # 幅のあるポリラインの点を作成
            node1 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
            node4 = np.array([node[nodeifig_index, 1], node[nodeifig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
            node2 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) + width / 2 * np.array([-1 * vec_v[1], vec_v[0]])
            node3 = np.array([node[nodejfig_index, 1], node[nodejfig_index, 2]]) - width / 2 * np.array([-1 * vec_v[1], vec_v[0]])

            if vec_v[0] == 0:
                rotd = 90
            else:
                rotd = atand(vec_v[1] / vec_v[0])

            text_add, element_max = plot_name_dxf(
                base_point, width, element_max, pick_name, rotd,
                node1, node2, name_class, 0)
            text_name += text_add

        else:
            ERROR = '柱梁タイプが定義されていません'
            print(ERROR)  # MATLAB版はセミコロン無し代入による表示のみ(停止しない)

    return text_single, text_double, text_name, element_max


# 元: end_add_plan.m L1-124
def end_add_plan(ele_no, element_on_axis, node, ele_end, c_b_index, width,
                 sections, limit_sec_no, column_no_on, element):
    ij_add = np.zeros(2)
    ij_add_c = np.zeros(2)

    # 対象要素のij端節点情報
    nodeifig_index, nodejfig_index = node_index_get(ele_no, element_on_axis, node)
    # MATLAB: nodeij=element_on_axis(該当行,4:5)。nodeij(i)は線形(列優先)index
    # なのでorder='F'でravelして再現(通常は該当行1行でnodeij=[i端,j端])
    nodeij = element_on_axis[
        find_zero_index(element_on_axis[:, 0] - ele_no), 3:5].ravel(order='F')

    # 対象要素の方向ベクトル
    _dvec = np.array([node[nodejfig_index, 1] - node[nodeifig_index, 1],
                      node[nodejfig_index, 2] - node[nodeifig_index, 2]])
    vec_v1 = _dvec / np.linalg.norm(_dvec)

    for i in range(2):
        # 端部接合条件抽出
        if _isempty(ele_end):
            joint = 0
        elif find_index(ele_end[:, 0], ele_no) != -1:  # MATLAB: >0(発見)
            # MATLAB: ele_end(行,i+1) → Python列index i+1 (i:0-based)
            joint = ele_end[find_index(ele_end[:, 0], ele_no), i + 1]
        else:
            joint = 0

        # 接続する柱を探す
        if _isempty(column_no_on):
            pass
        else:
            element_on = element[
                np.atleast_1d(find_index(element[:, 0], column_no_on[:, 0])), :]
            element_id_on = find_zero_index(element_on[:, 3] - nodeij[i])
            element_id_on = np.concatenate(
                [element_id_on, find_zero_index(element_on[:, 4] - nodeij[i])])
            element_no_on = element_on[element_id_on, 0]
            for j in range(element_no_on.size):
                section_size = sectionget(element_no_on[j], element, sections,
                                          limit_sec_no)
                # MATLAB版は element(element_id_on(j),6) と、element_on ではなく
                # element を element_id_on で引いている(元コードの挙動を踏襲)
                beta = element[element_id_on[j], 5]
                if section_size[-2] == 0:
                    width2 = np.inf
                elif (section_size[-2] == 1 or section_size[-2] == 2
                        or section_size[-2] == 5 or section_size[-2] == 6
                        or section_size[-2] == 7 or section_size[-2] == 11):
                    width2 = section_size[2] * abs(cosd(beta)) + section_size[1] * abs(sind(beta))
                elif section_size[-2] == 3 or section_size[-2] == 4:
                    width2 = section_size[1]
                # (上記以外の断面形式ではwidth2が未定義/前回値のまま使われる
                #  MATLAB版の挙動を踏襲)
                if ij_add_c[i] < width2 / 2:
                    if joint == 0:
                        ij_add_c[i] = width2 / 2
                    elif ij_add[i] > -1 * width2 / 2 - width / 2:
                        ij_add_c[i] = -1 * width2 / 2 - width / 2

        # 端部接続要素抽出
        element_co = np.concatenate(
            [find_zero_index(element_on_axis[:, 3] - nodeij[i]),
             find_zero_index(element_on_axis[:, 4] - nodeij[i])])
        element_co = element_on_axis[element_co, :]
        # MATLAB: element_co=element_co(find(element_co(:,1)-ele_no),:) (自身を除外)
        element_co = element_co[np.where(element_co[:, 0] - ele_no != 0)[0], :]

        if _isempty(element_co):
            pass
        else:
            # MATLAB: 1:size(element_co,1) (Python版column_beam_judgeは0-based)
            c_b_result = column_beam_judge(
                np.arange(element_co.shape[0]), element_co, node)
            cut_index = []
            for j in range(element_co.shape[0]):

                # MATLAB: element_co(j) 線形index=1列目(要素番号)
                nodeifig_index, nodejfig_index = node_index_get(
                    element_co[j, 0], element_co, node)
                _dvec2 = np.array(
                    [node[nodejfig_index, 1] - node[nodeifig_index, 1],
                     node[nodejfig_index, 2] - node[nodeifig_index, 2]])
                vec_v2 = _dvec2 / np.linalg.norm(_dvec2)

                # 連続する要素は対象外
                if abs(vec_v1[0] * vec_v2[0] + vec_v1[1] * vec_v2[1]) > 0.99:
                    cut_index.append(j)

                # (MATLAB版でコメントアウトされていた材端ピンによるcut処理は省略)

            element_co = np.delete(element_co, cut_index, axis=0)

            if _isempty(element_co):
                pass
            elif c_b_index == 2:
                pass
            elif c_b_index == 1 or c_b_index == 1.9:
                # MATLAB: for j=1:size(element_co) → 1:行数
                for j in range(element_co.shape[0]):

                    c_b_index2 = np.concatenate(
                        [column_beam_judge(j, element_co, node),
                         [element_co[j, 5]]])
                    section_size = sectionget(element_co[j, 0], element_co,
                                              sections, limit_sec_no)
                    if section_size[-2] == 0:
                        width2 = np.inf
                    elif (section_size[-2] == 1 or section_size[-2] == 2
                            or section_size[-2] == 5 or section_size[-2] == 6
                            or section_size[-2] == 7 or section_size[-2] == 9
                            or section_size[-2] == 10 or section_size[-2] == 11):
                        width2 = section_size[2] * abs(cosd(c_b_index2[1])) + section_size[1] * abs(sind(c_b_index2[1]))
                    elif section_size[-2] == 3:
                        width2 = section_size[1]
                    # (上記以外はwidth2未定義/前回値のまま: MATLAB版踏襲)
                    if ij_add[i] < width2 / 2:
                        if joint == 0:
                            ij_add[i] = width2 / 2
                        elif ij_add[i] > -1 * width2 / 2 - width / 2:
                            ij_add[i] = -1 * width2 / 2 - width / 2
            else:
                pass
        if ij_add[i] == 0:
            ij_add[i] = ij_add_c[i]
    return ij_add



# ========================================================================
# List_dxf.m ほか部材リスト図
# ========================================================================

# -*- coding: utf-8 -*-
# ============================================================
# part_list.py : 部材リスト図のdxfテキスト生成
# 元コード: List_dxf.m, dxf_box.m, dxf_list_text.m,
#           dxf_RCbeam_bar.m, dxf_RCcolumn_bar.m, dxf_RC_in_bar.m,
#           outf_steelbar_JIS.m (依存関数のためここに同梱)
# (import文なし。np/math/part_commonのヘルパーは定義済みとして使用)
# ============================================================


# 元: outf_steelbar_JIS.m L1-31
# 異形鉄筋/最外径参照
# out_d = Outf_steelbar_JIS(22)
# ※dxf_RCbeam_bar/dxf_RCcolumn_barの依存関数。本移植ではスカラ入力のみ使用。
def outf_steelbar_JIS(diameter):
    d = float(diameter)
    if (d - 10) * (d - 13) == 0:
        return d + 1
    elif (d - 16) * (d - 19) == 0:
        return d + 2
    elif (d - 22) * (d - 25) == 0:
        return d + 3
    elif (d - 29) * (d - 32) == 0:
        return d + 4
    elif (d - 35) * (d - 38) == 0:
        return d + 5
    elif (d - 41) * (d - 51) == 0:
        return d + 6
    else:
        # MATLAB版: ERROR = '鉄筋情報エラー' (セミコロン無しで表示、out_d=0のまま続行)
        print('ERROR = 鉄筋情報エラー')
        return 0.0


def _pl_colon(a, d, b):
    """MATLABのコロン演算子 a:d:b 相当 (part_list内部ヘルパー)."""
    n = int(np.floor((b - a) / d + 1e-10))
    if n < 0:
        return np.array([], dtype=float)
    return a + d * np.arange(n + 1, dtype=float)


# 元: dxf_box.m L1-33
def dxf_box(base_point, width, height, class_name, element_max, text):
    node1 = [base_point[0], base_point[1]]
    node2 = [base_point[0] + width, base_point[1]]
    node3 = [base_point[0] + width, base_point[1] - height]
    node4 = [base_point[0], base_point[1] - height]

    element_max = element_max + 1
    # POLYLINEヘッダ
    text += ['  0', 'POLYLINE', '  8', class_name, '  6', 'CONTINUOUS',
             '  5', dec2hex(element_max), ' 66', '1']
    # ダミー
    text += [' 10', '0.0', ' 20', '0.0', ' 30', '0.0', ' 70', '1',
             ' 75', '5']

    # 第1〜第4節点
    for nd in (node1, node2, node3, node4):
        element_max = element_max + 1
        text += ['  0', 'VERTEX', '  8', class_name, '  6', 'CONTINUOUS',
                 '  5', dec2hex(element_max)]
        text += [' 10', num2str(nd[0]), ' 20', num2str(nd[1]), ' 30', '0.0']

    element_max = element_max + 1
    # エンド
    text += ['  0', 'SEQEND', '  8', class_name, '  6', 'CONTINUOUS',
             '  5', dec2hex(element_max)]

    base_point = node4
    return text, element_max, base_point


# 元: dxf_list_text.m L1-20
# ※MATLABファイル内の関数名は dxf_steel_text だが、MATLABはファイル名で
#   関数を解決するため呼び出し名は dxf_list_text。Pythonでは dxf_list_text
#   の名前で定義する。
def dxf_list_text(base_point, width, height, class_name, plot_text, plot_h,
                  element_max, text):
    node1 = [base_point[0], base_point[1]]
    node2 = [base_point[0] + width, base_point[1]]
    node3 = [base_point[0] + width, base_point[1] - height]
    node4 = [base_point[0], base_point[1] - height]

    element_max = element_max + 1
    # 第1節点
    text += ['  0', 'TEXT', '  8', class_name, '  5', dec2hex(element_max)]
    text += [' 72', '1', ' 73', '2',
             ' 10', num2str(node1[0] * 0.5 + node3[0] * 0.5),
             ' 20', num2str(node1[1] * 0.5 + node3[1] * 0.5),
             ' 30', '0.0', ' 40', plot_h]
    text += ['  1', plot_text, '  7', 'ARIAL', ' 72', '1',
             ' 11', num2str(node1[0] * 0.5 + node3[0] * 0.5),
             ' 21', num2str(node1[1] * 0.5 + node3[1] * 0.5),
             ' 31', '0', ' 73', '2']

    element_max = element_max + 1
    # 第1節点
    text += ['  0', 'POINT', '  8', 'S-Cyan20', '  6', 'CONTINUOUS',
             '  5', dec2hex(element_max)]
    text += [' 10', num2str(node1[0] * 0.5 + node3[0] * 0.5),
             ' 20', num2str(node1[1] * 0.5 + node3[1] * 0.5),
             ' 30', '0.0', ' 40', plot_h]

    base_point = node4
    return text, element_max, base_point


# 元: dxf_RCbeam_bar.m L1-72
def dxf_RCbeam_bar(base_point, nh, d, b, di_main, class_name, element_max,
                   text, type):
    if type == 1:  # 主筋
        d1 = np.zeros(4) + base_point[1]
        d1[0] = d1[0] + d / 2 - outf_steelbar_JIS(di_main[0]) / 2
        d1[3] = d1[3] - d / 2 + outf_steelbar_JIS(di_main[3]) / 2

        d1[1] = d1[0] - math.ceil(2.5 * max(outf_steelbar_JIS(di_main[0]),
                                            outf_steelbar_JIS(di_main[1])))
        d1[2] = d1[3] + math.ceil(2.5 * max(outf_steelbar_JIS(di_main[2]),
                                            outf_steelbar_JIS(di_main[3])))

        x_b = None
        out_d = None
        for i in range(len(nh)):
            if nh[i] > 0:
                put_block = 'D' + num2str(di_main[i])
                out_d = outf_steelbar_JIS(di_main[i])

                b1 = b - out_d

                x_b = np.zeros(int(nh[i])) + base_point[0]
                if nh[i] == 1:
                    pass
                else:
                    x_b = x_b + _pl_colon(-b1 / 2, b1 / (nh[i] - 1), b1 / 2)
                y_d = d1[i]

                for j in range(int(nh[i])):
                    element_max = element_max + 1
                    text += ['  0', 'INSERT', '  8', class_name,
                             '  5', dec2hex(element_max)]
                    text += ['  2', put_block, ' 10', num2str(x_b[j]),
                             ' 20', num2str(y_d), ' 30', '0.0']
        if x_b is None:
            # MATLAB版はx_b未定義参照でエラー停止
            raise RuntimeError('MATLAB版stop相当: dxf_RCbeam_bar 主筋本数が全て0')
        x_area = np.concatenate((x_b, [out_d]))

    else:  # 腹筋

        put_block = 'D' + num2str(di_main)
        out_d = outf_steelbar_JIS(di_main)
        b = b - out_d
        y_d = np.zeros(int(nh)) + base_point[1]
        y_d = y_d + _pl_colon(d / 2 - 1 * d / (nh + 1), -1 * d / (nh + 1),
                              d / (nh + 1) + -d / 2)
        x_b = np.zeros(2) + base_point[0]
        x_b = x_b + _pl_colon(-b / 2, b, b / 2)

        for j in range(int(nh)):
            element_max = element_max + 1
            text += ['  0', 'INSERT', '  8', class_name,
                     '  5', dec2hex(element_max)]
            text += ['  2', put_block, ' 10', num2str(x_b[0]),
                     ' 20', num2str(y_d[j]), ' 30', '0.0']
            element_max = element_max + 1
            text += ['  0', 'INSERT', '  8', class_name,
                     '  5', dec2hex(element_max)]
            text += ['  2', put_block, ' 10', num2str(x_b[1]),
                     ' 20', num2str(y_d[j]), ' 30', '0.0']

            element_max = element_max + 1
            text += ['  0', 'LINE', '  8', 'S-Yellow20dot',
                     '  5', dec2hex(element_max)]
            text += [' 10', num2str(x_b[0] - out_d / 2),
                     ' 20', num2str(y_d[j] + out_d / 2), ' 30', '0.0']
            text += [' 11', num2str(x_b[1] + out_d / 2),
                     ' 21', num2str(y_d[j] + out_d / 2), ' 31', '0.0']

        x_area = np.concatenate((y_d.ravel(), [out_d]))

    return text, element_max, x_area


# 元: dxf_RCcolumn_bar.m L1-46
def dxf_RCcolumn_bar(base_point, nv, d, nh, b, di_main, class_name,
                     element_max, text):
    put_block = 'D' + num2str(di_main)
    out_d = outf_steelbar_JIS(di_main)
    d = d - out_d
    b = b - out_d

    x_b = np.zeros(int(nh)) + base_point[0]
    y_d = np.zeros(int(nv)) + base_point[1]

    if nh == 1:
        pass
    else:
        x_b = x_b + _pl_colon(-b / 2, b / (nh - 1), b / 2)

    if nv == 1:
        pass
    else:
        y_d = y_d + _pl_colon(d / 2, -1 * d / (nv - 1), -d / 2)

    for i in range(int(nh)):
        element_max = element_max + 1
        text += ['  0', 'INSERT', '  8', class_name,
                 '  5', dec2hex(element_max)]
        text += ['  2', put_block, ' 10', num2str(x_b[i]),
                 ' 20', num2str(y_d[0]), ' 30', '0.0']
        element_max = element_max + 1
        text += ['  0', 'INSERT', '  8', class_name,
                 '  5', dec2hex(element_max)]
        text += ['  2', put_block, ' 10', num2str(x_b[i]),
                 ' 20', num2str(y_d[int(nv) - 1]), ' 30', '0.0']

    for j in range(1, int(nv) - 1):
        element_max = element_max + 1
        text += ['  0', 'INSERT', '  8', class_name,
                 '  5', dec2hex(element_max)]
        text += ['  2', put_block, ' 10', num2str(x_b[0]),
                 ' 20', num2str(y_d[j]), ' 30', '0.0']
        element_max = element_max + 1
        text += ['  0', 'INSERT', '  8', class_name,
                 '  5', dec2hex(element_max)]
        text += ['  2', put_block, ' 10', num2str(x_b[int(nh) - 1]),
                 ' 20', num2str(y_d[j]), ' 30', '0.0']

    x_area = np.concatenate((x_b, [out_d]))
    y_area = np.concatenate((y_d.ravel(), [out_d]))
    return text, element_max, x_area, y_area


# 元: dxf_RC_in_bar.m L1-31
#   xy: MATLABの列選択そのまま (1 or 2)。base_point(xy)→base_point[xy-1]
def dxf_RC_in_bar(base_point, nbar, DB, class_name, element_max, text,
                  xy, xy_area):
    xy_area = np.asarray(xy_area, dtype=float).ravel()

    xy_point = np.zeros(int(nbar)) + base_point[xy - 1]
    xy_point = xy_point + _pl_colon(-DB[0] / 2, DB[0] / (nbar - 1), DB[0] / 2)

    # MATLAB: for i=2:nbar-1 (xy_point(i)は1-based) → Python index i-1
    for i in range(1, int(nbar) - 1):
        if xy_area.size == 3:  # MATLAB: size(xy_area,2)==3
            xy_plot = xy_point[i]
        else:
            # [a b]=min(abs(xy_area(1:end-1)-xy_point(i)))
            bidx = int(np.argmin(np.abs(xy_area[0:xy_area.size - 1]
                                        - xy_point[i])))
            if xy_area[bidx] - xy_point[i] > 0:
                xy_plot = xy_area[bidx] - xy_area[xy_area.size - 1] / 2
            else:
                xy_plot = xy_area[bidx] + xy_area[xy_area.size - 1] / 2
            xy_area = np.delete(xy_area, bidx)  # xy_area(b)=[]
        if xy == 2:  # y方向
            element_max = element_max + 1
            text += ['  0', 'LINE', '  8', class_name,
                     '  5', dec2hex(element_max)]
            text += [' 10', num2str(base_point[0] - DB[1] / 2),
                     ' 20', num2str(xy_plot), ' 30', '0.0']
            text += [' 11', num2str(base_point[0] + DB[1] / 2),
                     ' 21', num2str(xy_plot), ' 31', '0.0']
        elif xy == 1:  # y方向 (MATLABコメントのまま)
            element_max = element_max + 1
            text += ['  0', 'LINE', '  8', class_name,
                     '  5', dec2hex(element_max)]
            text += [' 10', num2str(xy_plot),
                     ' 20', num2str(base_point[1] - DB[1] / 2), ' 30', '0.0']
            text += [' 11', num2str(xy_plot),
                     ' 21', num2str(base_point[1] + DB[1] / 2), ' 31', '0.0']

    return text, element_max


# 元: List_dxf.m L1-427 (メイン関数)
def List_dxf(S_section_no, W_section_no, section_no, sections, section_name,
             element_max, RCbeams, RCcolumns, SRCbeams,
             column_section_class, girder_section_class, name_class):
    text = []
    base_point = [0.0, 0.0]
    # Ｓ部材リスト図

    # section_no(:,1) 相当の列 (1D/2D両対応)
    _sec_no_arr = np.asarray(section_no, dtype=float)
    _sec_no_col = _sec_no_arr[:, 0] if _sec_no_arr.ndim == 2 \
        else _sec_no_arr.ravel()

    def _pick_name_of(sec_no_val):
        # pick_section_name=section_name(find_index(section_no(:,1),...),:)
        idx = find_index(_sec_no_col, sec_no_val)
        if idx == -1:
            # MATLAB版: find_index=0 → section_name(0,:) でインデックスエラー停止
            raise RuntimeError('MATLAB版stop相当: 断面番号 %s がsection_noに'
                               '見つからない' % sec_no_val)
        pick_section_name = section_name[idx]
        cut_index = pick_section_name.find('_')  # strfind
        if cut_index == -1:  # isempty(cut_index)
            pick_name = pick_section_name
        else:
            pick_name = pick_section_name[0:cut_index]
        return space_erace(pick_name)

    def _put_class_of(pick_name):
        # findstr(pick_name,'C') : pick_name中の'C'の出現位置 (無ければ空)
        findC = pick_name.find('C')
        findG = pick_name.find('G')
        findB = pick_name.find('B')
        if findC == -1 and findG == -1 and findB == -1:
            return 'S-ORANGE40'
        elif findG == -1 and findB == -1:  # 柱
            return column_section_class
        elif findC == -1:  # 梁
            return girder_section_class
        else:
            return 'S-ORANGE40'

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 1000, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 形状
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 備考

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 1000, 250, name_class, '符号', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '位置', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 2500, name_class, '断面', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '形状', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '鋼種', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '備考', num2str(125), element_max, text)

    base_point = [base_point[0] + 1000, base_point[1]]

    _S_sec = np.array([]) if _isempty(S_section_no) \
        else np.atleast_1d(np.asarray(S_section_no, dtype=float)).ravel()
    for i in range(_S_sec.size):
        pick_name = _pick_name_of(_S_sec[i])
        section_size = sectionget2(sections, _S_sec[i])

        put_class = _put_class_of(pick_name)

        if section_size[len(section_size) - 2] == 1:  # H鋼
            text, element_max, base_point = dxf_H_list(section_size, text, element_max, pick_name, name_class, base_point, put_class)

        elif section_size[len(section_size) - 2] == 2:  # SB
            text, element_max, base_point = dxf_SB_list(section_size, text, element_max, pick_name, name_class, base_point, put_class)

        elif section_size[len(section_size) - 2] == 4:  # 鋼管
            text, element_max, base_point = dxf_P_list(section_size, text, element_max, pick_name, name_class, base_point, put_class)

        elif section_size[len(section_size) - 2] == 7:  # 角形鋼管
            text, element_max, base_point = dxf_B_list(section_size, text, element_max, pick_name, name_class, base_point, put_class)

    # 木造部材リスト図
    base_point = [0, -5000]

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 1000, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 形状
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class, element_max, text)  # 備考

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 1000, 250, name_class, '符号', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '位置', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 2500, name_class, '断面', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '形状', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '材種', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class, '備考', num2str(125), element_max, text)

    base_point = [base_point[0] + 1000, base_point[1]]

    _W_sec = np.array([]) if _isempty(W_section_no) \
        else np.atleast_1d(np.asarray(W_section_no, dtype=float)).ravel()
    for i in range(_W_sec.size):
        pick_name = _pick_name_of(_W_sec[i])
        section_size = sectionget2(sections, _W_sec[i])

        put_class = _put_class_of(pick_name)

        if section_size[len(section_size) - 2] == 2:  # SB
            text, element_max, base_point = dxf_SB_list_W(section_size, text, element_max, pick_name, name_class, base_point, put_class)
        else:
            pass

    # ＲＣ部材リスト図＜柱＞
    base_point = [0, -10000]
    name_class1 = 'S-Red09'
    name_class2 = 'S-Magenda30'
    name_class3 = 'S-Yellow20'

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 1000, 250, name_class1, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # BxD
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 主筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # HOOP
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 500, name_class1, element_max, text)  # 備考

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 1000, 250, name_class1, '符号', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '位置', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 2500, name_class1, '断面', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'BxD', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '主筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'HOOP', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 500, name_class1, '備考', num2str(125), element_max, text)

    base_point = [base_point[0] + 1000, base_point[1]]

    _RCcol = np.zeros((0, 10)) if _isempty(RCcolumns) \
        else np.atleast_2d(np.asarray(RCcolumns, dtype=float))
    for i in range(_RCcol.shape[0]):
        pick_name = _pick_name_of(_RCcol[i, 0])
        section_size = sectionget2(sections, _RCcol[i, 0])

        if section_size[len(section_size) - 2] == 2:
            text, element_max, base_point = dxf_RCC_SB_list(
                section_size, text, element_max, pick_name,
                name_class1, name_class2, name_class3, base_point,
                _RCcol[i, :])
        else:
            pass

    base_point = [0, -15000]
    name_class1 = 'S-Red09'
    name_class2 = 'S-Magenda30'
    name_class3 = 'S-Yellow20'

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 1000, 250, name_class1, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # bxD
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 上端筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 下端筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # STP
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 腹筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 500, name_class1, element_max, text)  # 備考

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 1000, 250, name_class1, '符号', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '位置', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 2500, name_class1, '断面', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'bxD', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '上端筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '下端筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'STP', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '腹筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 500, name_class1, '備考', num2str(125), element_max, text)

    base_point = [base_point[0] + 1000, base_point[1]]
    RCbeam_secNO = cell2matrix(RCbeams, 1, 1) if len(RCbeams) > 0 \
        else np.array([])
    _n_rcb = 0 if _isempty(RCbeam_secNO) \
        else np.atleast_2d(RCbeam_secNO).shape[0]
    RCbeam_secNO = np.atleast_2d(RCbeam_secNO)

    # ＲＣ部材リスト図＜梁＞
    for i in range(_n_rcb):
        pick_name = _pick_name_of(RCbeam_secNO[i, 0])
        section_size = sectionget2(sections, RCbeam_secNO[i, 0])

        pick_bar = RCbeams[i][1]

        if section_size[len(section_size) - 2] == 2:

            check_same = np.zeros(3)
            pick_bar = np.vstack((pick_bar, pick_bar[0:1, :]))

            for j in range(3):
                for jj in range(pick_bar.shape[1]):
                    check_same[j] = check_same[j] + abs(pick_bar[j, jj]
                                                        - pick_bar[j + 1, jj])
                if check_same[j] > 0:
                    check_same[j] = 1

            if np.sum(check_same) < 2:  # 全断面同じ
                position = '全断面'
                text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号

                text, element_max, base_point = dxf_RCG_SB_list(
                    section_size, text, element_max, pick_name,
                    name_class1, name_class2, name_class3, base_point,
                    pick_bar[0, :], position, 2800)

            elif np.sum(check_same) == 2:
                text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号

                # MATLAB: find_zero_index(...)==1 (1-based) → Python 0-based
                _fz = find_zero_index(check_same)
                pick_bar2 = np.zeros((2, pick_bar.shape[1]))
                if int(_fz[0]) == 0:  # i=c
                    position = ['ｉ端/中央', 'ｊ端']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[2, :]
                elif int(_fz[0]) == 1:  # c=j
                    position = ['ｉ端', '中央/ｊ端']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[2, :]
                else:  # i=j
                    position = ['端部', '中央']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[1, :]
                # ※MATLAB版はpick_bar2を作成するが以下ではpick_bar(j,:)を渡す
                #   (pick_bar2未使用のバグ)。そのまま踏襲。
                for j in range(2):
                    text, element_max, base_point = dxf_RCG_SB_list(
                        section_size, text, element_max, pick_name,
                        name_class1, name_class2, name_class3, base_point,
                        pick_bar[j, :], position[j], 1400)

            elif np.sum(check_same) == 3:  # 全て断面異なる
                position = ['ｉ端', '中央', 'ｊ端']
                text, element_max, base_point1 = dxf_box(base_point, 4200, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 4200, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号
                for j in range(3):
                    text, element_max, base_point = dxf_RCG_SB_list(
                        section_size, text, element_max, pick_name,
                        name_class1, name_class2, name_class3, base_point,
                        pick_bar[j, :], position[j], 1400)

        elif section_size[len(section_size) - 2] == 12:
            position = ['ｉ端', '中央', 'ｊ端']
            text, element_max, base_point1 = dxf_box(base_point, 4200, 250, name_class1, element_max, text)  # 符号
            text, element_max, base_point1 = dxf_list_text(base_point, 4200, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号

            position = 'ｉ端'
            # ※MATLAB版はpick_nameに'_1','_2','_3'を累積連結する
            #   (例 G1_1, G1_1_2, G1_1_2_3)。そのまま踏襲。
            pick_name = pick_name + '_1'
            text, element_max, base_point = dxf_RCG_SB_list(
                section_size[0:3], text, element_max, pick_name,
                name_class1, name_class2, name_class3, base_point,
                pick_bar[0, :], position, 1400)

            position = '中央'
            pick_name = pick_name + '_2'
            text, element_max, base_point = dxf_RCG_SB_list(
                (section_size[0:3] + section_size[np.array([0, 3, 4])]) / 2,
                text, element_max, pick_name,
                name_class1, name_class2, name_class3, base_point,
                pick_bar[1, :], position, 1400)

            position = 'ｊ端'
            pick_name = pick_name + '_3'
            text, element_max, base_point = dxf_RCG_SB_list(
                section_size[np.array([0, 3, 4])], text, element_max,
                pick_name, name_class1, name_class2, name_class3, base_point,
                pick_bar[2, :], position, 1400)

    base_point = [0, -20000]
    name_class1 = 'S-Red09'
    name_class2 = 'S-Magenda30'
    name_class3 = 'S-Yellow20'

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 1000, 250, name_class1, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # bxD
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 内蔵鉄骨
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 上端筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 下端筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # STP
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 250, name_class1, element_max, text)  # 腹筋
    text, element_max, base_point1 = dxf_box(base_point1, 1000, 500, name_class1, element_max, text)  # 備考

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 1000, 250, name_class1, '符号', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '位置', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 2500, name_class1, '断面', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'bxD', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '内蔵鉄骨', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '上端筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '下端筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, 'STP', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 250, name_class1, '腹筋', num2str(125), element_max, text)
    text, element_max, base_point1 = dxf_list_text(base_point1, 1000, 500, name_class1, '備考', num2str(125), element_max, text)

    base_point = [base_point[0] + 1000, base_point[1]]
    SRCbeam_secNO = cell2matrix(SRCbeams, 1, 1) if len(SRCbeams) > 0 \
        else np.array([])
    _n_src = 0 if _isempty(SRCbeam_secNO) \
        else np.atleast_2d(SRCbeam_secNO).shape[0]
    SRCbeam_secNO = np.atleast_2d(SRCbeam_secNO)

    # ＳＲＣ部材リスト図＜梁＞
    for i in range(_n_src):
        pick_name = _pick_name_of(SRCbeam_secNO[i, 0])
        # MATLAB版はセミコロン無し(コンソール表示のみ) — 出力には影響しない
        section_size = sectionget2(sections, SRCbeam_secNO[i, 0])

        pick_bar = SRCbeams[i][1]
        # pick_bar=[pick_bar(:,1:4) pick_bar(:,4) pick_bar(:,5:8)
        #           pick_bar(:,8) pick_bar(:,9:11)]
        pick_bar = np.hstack((pick_bar[:, 0:4], pick_bar[:, 3:4],
                              pick_bar[:, 4:8], pick_bar[:, 7:8],
                              pick_bar[:, 8:11]))

        if section_size[len(section_size) - 2] == 10:

            H_section = section_size[3:9]  # (MATLAB版でも未使用)
            check_same = np.zeros(3)
            pick_bar = np.vstack((pick_bar, pick_bar[0:1, :]))
            for j in range(3):
                for jj in range(pick_bar.shape[1]):
                    check_same[j] = check_same[j] + abs(pick_bar[j, jj]
                                                        - pick_bar[j + 1, jj])
                if check_same[j] > 0:
                    check_same[j] = 1

            if np.sum(check_same) < 2:  # 全断面同じ
                position = '全断面'
                text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号

                text, element_max, base_point = dxf_SRCG_SB_list(
                    section_size, text, element_max, pick_name,
                    name_class1, name_class2, name_class3, base_point,
                    pick_bar[0, :], position, 2800)

            elif np.sum(check_same) == 2:
                text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号

                _fz = find_zero_index(check_same)
                pick_bar2 = np.zeros((2, pick_bar.shape[1]))
                if int(_fz[0]) == 0:  # i=c
                    position = ['ｉ端/中央', 'ｊ端']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[2, :]
                elif int(_fz[0]) == 1:  # c=j
                    position = ['ｉ端', '中央/ｊ端']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[2, :]
                else:  # i=j
                    position = ['端部', '中央']
                    pick_bar2[0, :] = pick_bar[0, :]
                    pick_bar2[1, :] = pick_bar[1, :]
                # ※pick_bar2未使用 (MATLAB版のまま踏襲)
                for j in range(2):
                    text, element_max, base_point = dxf_SRCG_SB_list(
                        section_size, text, element_max, pick_name,
                        name_class1, name_class2, name_class3, base_point,
                        pick_bar[j, :], position[j], 1400)

            elif np.sum(check_same) == 3:  # 全て断面異なる
                position = ['ｉ端', '中央', 'ｊ端']
                text, element_max, base_point1 = dxf_box(base_point, 4200, 250, name_class1, element_max, text)  # 符号
                text, element_max, base_point1 = dxf_list_text(base_point, 4200, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号
                for j in range(3):
                    text, element_max, base_point = dxf_SRCG_SB_list(
                        section_size, text, element_max, pick_name,
                        name_class1, name_class2, name_class3, base_point,
                        pick_bar[j, :], position[j], 1400)

        else:
            pass

    return text, element_max


# 元: List_dxf.m L428-504 (サブ関数 dxf_SRCG_SB_list)
def dxf_SRCG_SB_list(section_size, text, element_max, pick_name,
                     name_class1, name_class2, name_class3, base_point,
                     RCbeam, position, width):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 断面形状(シンボル)の追加
    # 外形
    put_block = pick_name + '_section'
    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', name_class2, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + width / 2),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # STRP(外周)
    text, element_max, _ = dxf_box(
        [base_point[0] + width / 2 - section_size[2] * 1000 / 2 + 50,
         base_point[1] - 1750 + section_size[1] * 1000 / 2 - 50],
        section_size[2] * 1000 - 100, section_size[1] * 1000 - 100,
        name_class3, element_max, text)  # 主筋

    # H鋼（内蔵）
    put_block = pick_name + '_S_section'
    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', name_class2, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + width / 2),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # 主筋
    nh = [RCbeam[1], RCbeam[2], RCbeam[7], RCbeam[6]]
    di_main = [RCbeam[3], RCbeam[4], RCbeam[9], RCbeam[8]]
    text, element_max, x_area = dxf_RCbeam_bar(
        [base_point[0] + width / 2, base_point[1] - 1750],
        nh, section_size[1] * 1000 - 100, section_size[2] * 1000 - 100,
        di_main, name_class3, element_max, text, 1)

    # 腹筋/巾止め筋
    n_sub = np.floor(section_size[1] * 1000 / 500)
    di_sub = 13
    text, element_max, y_area = dxf_RCbeam_bar(
        [base_point[0] + width / 2, base_point[1] - 1750],
        n_sub, section_size[1] * 1000 - 100, section_size[2] * 1000 - 100,
        di_sub, name_class3, element_max, text, 2)

    # STRP(中筋)
    if RCbeam[12] > 2:
        text, element_max = dxf_RC_in_bar(
            [base_point[0] + width / 2, base_point[1] - 1750],
            RCbeam[12],
            [section_size[2] * 1000 - 100, section_size[1] * 1000 - 100],
            name_class3, element_max, text, 1, x_area)

    base_point1 = [base_point[0], base_point[1] - 250]
    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, width, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # bxD
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 内蔵鉄骨
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 上端筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 下端筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # STP
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 腹筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 500, name_class1, element_max, text)  # 備考

    # テキスト/基準点追加
    base_point1 = [base_point[0], base_point[1] - 250]
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, position, num2str(125), element_max, text)  # 位置（中央/端部など）
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = num2str(section_size[2] * 1000) + 'x' + num2str(section_size[1] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # BxD
    plot_text = 'H-' + num2str(section_size[3] * 1000) + 'x' + num2str(section_size[4] * 1000) + 'x' + num2str(section_size[5] * 1000) + 'x' + num2str(section_size[6] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 内蔵鉄骨
    plot_text = num2str(RCbeam[1] + RCbeam[2]) + '-D' + num2str(RCbeam[3])  # 上端筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 上端筋
    plot_text = num2str(RCbeam[6] + RCbeam[7]) + '-D' + num2str(RCbeam[8])  # 下端筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 下端筋

    plot_text = num2str(RCbeam[12]) + '-D' + num2str(RCbeam[10]) + '@' + num2str(RCbeam[11])  # STRP
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # STRP

    plot_text = num2str(n_sub * 2) + '-D13'  # 腹筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 腹筋

    text, element_max, base_point1 = dxf_list_text(base_point1, width, 500, name_class1, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + width, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L505-572 (サブ関数 dxf_RCG_SB_list)
def dxf_RCG_SB_list(section_size, text, element_max, pick_name,
                    name_class1, name_class2, name_class3, base_point,
                    RCbeam, position, width):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 断面形状(シンボル)の追加
    # 外形
    put_block = pick_name + '_section'
    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', name_class2, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + width / 2),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # STRP(外周)
    text, element_max, _ = dxf_box(
        [base_point[0] + width / 2 - section_size[2] * 1000 / 2 + 50,
         base_point[1] - 1750 + section_size[1] * 1000 / 2 - 50],
        section_size[2] * 1000 - 100, section_size[1] * 1000 - 100,
        name_class3, element_max, text)  # 主筋

    # 主筋 %220330國江修正
    nh = [RCbeam[1], RCbeam[2], RCbeam[9], RCbeam[8]]
    di_main = [RCbeam[4], RCbeam[5], RCbeam[12], RCbeam[11]]
    text, element_max, x_area = dxf_RCbeam_bar(
        [base_point[0] + width / 2, base_point[1] - 1750],
        nh, section_size[1] * 1000 - 100, section_size[2] * 1000 - 100,
        di_main, name_class3, element_max, text, 1)

    # 腹筋/巾止め筋
    n_sub = np.floor(section_size[1] * 1000 / 500)
    di_sub = 13
    text, element_max, y_area = dxf_RCbeam_bar(
        [base_point[0] + width / 2, base_point[1] - 1750],
        n_sub, section_size[1] * 1000 - 100, section_size[2] * 1000 - 100,
        di_sub, name_class3, element_max, text, 2)

    # STRP(中筋)%220330國江修正
    if RCbeam[16] > 2:
        text, element_max = dxf_RC_in_bar(
            [base_point[0] + width / 2, base_point[1] - 1750],
            RCbeam[16],
            [section_size[2] * 1000 - 100, section_size[1] * 1000 - 100],
            name_class3, element_max, text, 1, x_area)

    base_point1 = [base_point[0], base_point[1] - 250]
    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, width, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # bxD
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 上端筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 下端筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # STP
    text, element_max, base_point1 = dxf_box(base_point1, width, 250, name_class1, element_max, text)  # 腹筋
    text, element_max, base_point1 = dxf_box(base_point1, width, 500, name_class1, element_max, text)  # 備考

    # テキスト/基準点追加 %220330國江修正
    base_point1 = [base_point[0], base_point[1] - 250]
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, position, num2str(125), element_max, text)  # 位置（中央/端部など）
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = num2str(section_size[2] * 1000) + 'x' + num2str(section_size[1] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # BxD
    plot_text = num2str(RCbeam[1] + RCbeam[2]) + '-D' + num2str(RCbeam[4])  # 上端筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 上端筋
    plot_text = num2str(RCbeam[8] + RCbeam[9]) + '-D' + num2str(RCbeam[11])  # 下端筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 下端筋

    plot_text = num2str(RCbeam[16]) + '-D' + num2str(RCbeam[14]) + '@' + num2str(RCbeam[15])  # STRP
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # STRP

    plot_text = num2str(n_sub * 2) + '-D13'  # 腹筋
    text, element_max, base_point1 = dxf_list_text(base_point1, width, 250, name_class1, plot_text, num2str(125), element_max, text)  # 腹筋

    text, element_max, base_point1 = dxf_list_text(base_point1, width, 500, name_class1, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + width, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L573-631 (サブ関数 dxf_RCC_SB_list)
def dxf_RCC_SB_list(section_size, text, element_max, pick_name,
                    name_class1, name_class2, name_class3, base_point,
                    RCcolumns):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class1, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class1, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class1, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class1, element_max, text)  # BxD
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class1, element_max, text)  # 主筋
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class1, element_max, text)  # HOOP
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 500, name_class1, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    # 外形
    put_block = pick_name + '_section'
    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', name_class2, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # HOOP(外周)
    text, element_max, _ = dxf_box(
        [base_point[0] + 1400 - section_size[2] * 1000 / 2 + 50,
         base_point[1] - 1750 + section_size[1] * 1000 / 2 - 50],
        section_size[2] * 1000 - 100, section_size[1] * 1000 - 100,
        name_class3, element_max, text)  # 主筋

    # 主筋
    nv = RCcolumns[4]
    nh = RCcolumns[5]
    di_main = RCcolumns[3]
    text, element_max, x_area, y_area = dxf_RCcolumn_bar(
        [base_point[0] + 1400, base_point[1] - 1750],
        nv, section_size[1] * 1000 - 100, nh, section_size[2] * 1000 - 100,
        di_main, name_class3, element_max, text)

    # HOOP(中筋)-X
    if RCcolumns[8] > 2:
        text, element_max = dxf_RC_in_bar(
            [base_point[0] + 1400, base_point[1] - 1750],
            RCcolumns[8],
            [section_size[1] * 1000 - 100, section_size[2] * 1000 - 100],
            name_class3, element_max, text, 2, y_area)
    # HOOP(中筋)-Y
    if RCcolumns[9] > 2:
        text, element_max = dxf_RC_in_bar(
            [base_point[0] + 1400, base_point[1] - 1750],
            RCcolumns[9],
            [section_size[2] * 1000 - 100, section_size[1] * 1000 - 100],
            name_class3, element_max, text, 1, x_area)

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class1, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class1, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class1,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = num2str(section_size[2] * 1000) + 'x' + num2str(section_size[1] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class1, plot_text, num2str(125), element_max, text)  # BxD
    plot_text = num2str(RCcolumns[2]) + '-D' + num2str(RCcolumns[3])
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class1, plot_text, num2str(125), element_max, text)  # 主筋
    plot_text = num2str(RCcolumns[8]) + '(x)/' + num2str(RCcolumns[9]) + '(y)-D' + num2str(RCcolumns[6]) + '@' + num2str(RCcolumns[7])
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class1, plot_text, num2str(125), element_max, text)  # HOOP
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 500, name_class1, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L632-662 (サブ関数 dxf_P_list)
def dxf_P_list(section_size, text, element_max, pick_name, name_class,
               base_point, put_class):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 断面形状(P-***x***x**x**)
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    put_block = pick_name + '_section'

    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', put_class, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = 'P-' + num2str(section_size[1] * 1000) + 'x' + num2str(section_size[2] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, plot_text, num2str(125), element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, 'STKN***', num2str(125), element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L663-692 (サブ関数 dxf_H_list)
def dxf_H_list(section_size, text, element_max, pick_name, name_class,
               base_point, put_class):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    put_block = pick_name + '_section'

    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', put_class, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = 'H-' + num2str(section_size[1] * 1000) + 'x' + num2str(section_size[2] * 1000) + 'x' + num2str(section_size[3] * 1000) + 'x' + num2str(section_size[4] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, plot_text, num2str(125), element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, 'SN4**', num2str(125), element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L693-722 (サブ関数 dxf_SB_list)
def dxf_SB_list(section_size, text, element_max, pick_name, name_class,
                base_point, put_class):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    put_block = pick_name + '_section'

    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', put_class, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = '□-' + num2str(section_size[1] * 1000) + 'x' + num2str(section_size[2] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, plot_text, num2str(125), element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '***', num2str(125), element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L723-754 (サブ関数 dxf_SB_list_W)
def dxf_SB_list_W(section_size, text, element_max, pick_name, name_class,
                  base_point, put_class):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    put_block = pick_name + '_section'

    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', put_class, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = '□-' + num2str(section_size[2] * 1000) + 'x' + num2str(section_size[1] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, plot_text, num2str(125), element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '***', num2str(125), element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point


# 元: List_dxf.m L755-782 (サブ関数 dxf_B_list)
def dxf_B_list(section_size, text, element_max, pick_name, name_class,
               base_point, put_class):
    base_point = [base_point[0], base_point[1]]  # MATLAB値渡し再現

    # 枠の作成
    text, element_max, base_point1 = dxf_box(base_point, 2800, 250, name_class, element_max, text)  # 符号
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 位置（中央/端部など）
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 2500, name_class, element_max, text)  # 断面
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 断面形状(B-***x***x**x**)
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_box(base_point1, 2800, 250, name_class, element_max, text)  # 備考

    # 断面形状(シンボル)の追加
    put_block = pick_name + '_section'
    element_max = element_max + 1
    text += ['  0', 'INSERT', '  8', put_class, '  5', dec2hex(element_max)]
    text += ['  2', put_block, ' 10', num2str(base_point[0] + 1400),
             ' 20', num2str(base_point[1] - 1750), ' 30', '0.0']

    # テキスト/基準点追加
    text, element_max, base_point1 = dxf_list_text(base_point, 2800, 250, name_class, pick_name, num2str(125), element_max, text)  # 符号
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '全断面', num2str(125), element_max, text)  # 位置（中央/端部など）
    # [text element_max base_point1]=dxf_list_text(base_point1,2800,2500,name_class,plot_text,125,element_max); %断面
    base_point1 = [base_point1[0], base_point1[1] - 2500]
    plot_text = 'B-' + num2str(section_size[1] * 1000) + 'x' + num2str(section_size[2] * 1000) + 'x' + num2str(section_size[3] * 1000)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, plot_text, num2str(125), element_max, text)  # 断面形状(H-***x***x**x**)
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, 'STKR4**', num2str(125), element_max, text)  # 鋼種
    text, element_max, base_point1 = dxf_list_text(base_point1, 2800, 250, name_class, '-', num2str(125), element_max, text)  # 備考

    base_point = [base_point[0] + 2800, base_point[1]]
    return text, element_max, base_point



# ========================================================================
# MIDAS_dxf_fig.m / Midas_dxf.m (ドライバ)
# ========================================================================

# ============================================================
# ドライバ部 : MIDAS_dxf_fig.m + Midas_dxf.m(calculate_Callback) の移植
# GUI (uigetfile / listdlg / G_input) は関数引数に置き換え。
# ============================================================


# ------------------------------------------------------------------
# MIDAS_dxf_fig.m L96-135 : 分割部材の一括表示オプション
# (統合された1部材の中にダミー材などが混在していないかチェック)
# ------------------------------------------------------------------

def _unit_elements_dxf(filename, element, node, limit_sec_no):
    unit_element = mgtopen_unit_2015(filename, element, node)
    for unit in unit_element:
        unit_ele_index = np.atleast_1d(find_index(element[:, 0], unit[1]))
        unit_ele_sec_no = element[unit_ele_index, 2]
        # 断面番号がダミー材番号より小さいかチェック
        unit_ele_sec_no = (unit_ele_sec_no < limit_sec_no).astype(float)
        if unit_ele_sec_no.sum() == 0 and unit_ele_sec_no.prod() == 0:
            pass    # 全て応力をプロットしない部材→OK
        elif unit_ele_sec_no.sum() > 0 and unit_ele_sec_no.prod() > 0:
            pass    # 全てプロットする→OK
        else:       # 応力表示すべきものとすべきでないものが混じっている
            L = len(unit_ele_sec_no)
            # もし両端以外にそういう部材がある場合はエラーを返す
            if np.prod(unit_ele_sec_no[1:L - 1]) == 0:
                raise RuntimeError(
                    'MATLAB版stop相当: 結合部材(*MEMBER)の中間にダミー材が'
                    '混在しています (mgtの*MEMBER記述を確認してください): %s'
                    % str(unit[1]))
            c3 = list(np.atleast_1d(unit[2]))
            if unit_ele_sec_no[L - 1] == 0:
                del c3[L]       # 端部の節点番号削除 (末尾)
            if unit_ele_sec_no[0] == 0:
                del c3[0]       # 端部の節点番号削除 (先頭)
            unit[2] = np.asarray(c3, dtype=float)
            # 端部の要素番号削除
            unit[1] = np.asarray(unit[1], dtype=float)[unit_ele_sec_no > 0]

    # 結合された部材のoriginalIDをひろっておく
    uniele_ID = []
    for unit in unit_element:
        uniele_ID += list(np.asarray(unit[1], dtype=float).ravel())
    uniele_ID = np.sort(np.asarray(uniele_ID, dtype=float))
    return unit_element, uniele_ID


# ------------------------------------------------------------------
# MIDAS_dxf_fig.m L169-241 : 各通りの一次近似係数・方向ベクトル取得
# ------------------------------------------------------------------

def _axis_coefficients_dxf(node, axis_node_select, axis_name_select):
    num_axis = len(axis_node_select)
    axis_plot_coefi = np.zeros((num_axis, 3))
    axis_direction = np.zeros((num_axis, 2))
    pick_axis_node_no = []

    for i_axis in range(num_axis):
        # 各通りにある節点のXY座標だけをまずは抽出
        nn = np.atleast_1d(np.asarray(axis_node_select[i_axis][0],
                                      dtype=float)).ravel()
        on_i_axis_node_index = np.atleast_1d(find_index(node[:, 0], nn))

        i_axis_plotline = node[on_i_axis_node_index, 1:3]
        i_axis_plotline_no = node[on_i_axis_node_index, 0]

        on_i_axis_node_index = np.atleast_1d(
            find_index(node[:, 0], i_axis_plotline_no))
        # 通りを構成する接点情報から重複するものを削除
        if i_axis_plotline.shape[0] > 1:
            i_axis_plotline = doublecheck2(i_axis_plotline)
        pick_axis_node_no.append(i_axis_plotline_no)

        # XY各座標のばらつきを評価<XY通り芯の判別>
        X_var = _var(i_axis_plotline[:, 0])
        Y_var = _var(i_axis_plotline[:, 1])

        if (X_var < 0.0001) and (Y_var < 0.0001):
            # MATLAB版 stop 相当
            raise RuntimeError(
                'MATLAB版stop相当: 鉛直構面グループ %r の節点がXY平面上で'
                '1点に集中しており通り芯を判別できません '
                '(mgtの*GROUP記述を確認してください)'
                % axis_name_select[i_axis])
        elif X_var < 0.0001:  # X通り
            co = np.polyfit(i_axis_plotline[:, 1], i_axis_plotline[:, 0], 1)
            axis_plot_coefi[i_axis, :] = [1, co[0], co[1]]  # X=AY+B
            axis_direction[i_axis, :] = [0, 1]
        elif Y_var < 0.0001:  # Y通り
            # ここで，各通り芯を一次関数としてあらわした時の係数を取得
            co = np.polyfit(i_axis_plotline[:, 0], i_axis_plotline[:, 1], 1)
            axis_plot_coefi[i_axis, :] = [2, co[0], co[1]]  # Y=AX+B
            axis_direction[i_axis, :] = [1, 0]
        elif Y_var > X_var:  # X通り (斜め)
            co = np.polyfit(i_axis_plotline[:, 1], i_axis_plotline[:, 0], 1)
            axis_plot_coefi[i_axis, :] = [1, co[0], co[1]]  # X=AY+B
            axis_direction[i_axis, :] = [
                math.cos(math.atan(1 / co[0])),
                math.sin(math.atan(1 / co[0]))]
            if axis_direction[i_axis, 1] < 0:
                axis_direction[i_axis, :] = axis_direction[i_axis, :] * -1
        else:  # X_var >= Y_var : Xのばらつきが大きいのでY通り (斜め)
            co = np.polyfit(i_axis_plotline[:, 0], i_axis_plotline[:, 1], 1)
            axis_plot_coefi[i_axis, :] = [2, co[0], co[1]]  # Y=AX+B
            axis_direction[i_axis, :] = [
                math.cos(math.atan(co[0])),
                math.sin(math.atan(co[0]))]
            if axis_direction[i_axis, 0] < 0:
                axis_direction[i_axis, :] = axis_direction[i_axis, :] * -1

    return axis_plot_coefi, axis_direction, pick_axis_node_no


# ------------------------------------------------------------------
# グループ選択 (listdlg の代替)
# ------------------------------------------------------------------

def _resolve_group_select(axis_name, select, kind):
    """グループ名(またはindex)のリストを0-basedインデックス列へ解決."""
    idx = []
    stripped = [space_erace(str(n)) for n in axis_name]
    for s in select:
        if isinstance(s, (int, np.integer)) and not isinstance(s, bool):
            idx.append(int(s))
        elif space_erace(str(s)) in stripped:
            idx.append(stripped.index(space_erace(str(s))))
        else:
            raise KeyError(
                '%s グループ %r が見つかりません (mgtの*GROUP一覧: %s)'
                % (kind, s, ', '.join(stripped)))
    return idx


def _auto_classify_groups(node, axis_node, axis_name, axis_element=None):
    """全グループを鉛直構面/水平構面(床)へ自動分類する.

    MATLAB版のlistdlgによる手動選択の代替:
      - 節点が登録されていないグループは対象外
      - 要素が登録されていないグループも対象外 (節点のみのグループを
        構面として描くとfigure_dxf/figure_dxf_floorの
        find_index(空の要素リスト, ...) がMATLAB版同様エラーとなるため。
        MATLAB版では人間がlistdlgで選択しないことで回避されていた)
      - 節点のZ座標のばらつきが小さい(分散<0.5m^2) → 水平構面(床)
        (勾配屋根の床グループも含めるためZ許容は緩め)
      - それ以外で、節点がXY平面上でほぼ一直線に並ぶ(一次近似の
        残差分散<1.0m^2) → 鉛直構面(通り芯)
      - どちらでもないグループは対象外(注記を表示)
    """
    v_idx, h_idx = [], []
    for j in range(len(axis_node)):
        nn = np.atleast_1d(np.asarray(axis_node[j], dtype=float)).ravel()
        if nn.size == 0 or (nn.size == 1 and nn[0] == 0):
            continue    # 節点未登録グループ
        if axis_element is not None:
            ee = np.atleast_1d(
                np.asarray(axis_element[j], dtype=float)).ravel()
            if ee.size == 0 or (ee.size == 1 and ee[0] == 0):
                print('注記: グループ %s は要素が登録されていない(節点のみ)'
                      'ため構面図の対象外とします' % axis_name[j])
                continue    # 要素未登録グループ
        on_idx = np.atleast_1d(find_index(node[:, 0], nn))
        on_idx = on_idx[on_idx >= 0]
        if on_idx.size < 2:
            continue
        coords = node[on_idx, 1:4]
        X_var = _var(coords[:, 0])
        Y_var = _var(coords[:, 1])
        Z_var = _var(coords[:, 2])
        if Z_var < 0.5:
            h_idx.append(j)
        elif not (X_var < 0.0001 and Y_var < 0.0001):
            xy = doublecheck2(coords[:, 0:2]) if coords.shape[0] > 1 \
                else coords[:, 0:2]
            if X_var < 0.0001 or Y_var < 0.0001:
                v_idx.append(j)
            else:
                # 斜め通り: MATLAB版と同じ判別順で一次近似し残差を確認
                if Y_var > X_var:
                    co = np.polyfit(xy[:, 1], xy[:, 0], 1)
                    resid = xy[:, 0] - np.polyval(co, xy[:, 1])
                else:
                    co = np.polyfit(xy[:, 0], xy[:, 1], 1)
                    resid = xy[:, 1] - np.polyval(co, xy[:, 0])
                if _var(resid) < 1.0:
                    v_idx.append(j)
                else:
                    print('注記: グループ %s は鉛直構面/水平構面のいずれにも'
                          '自動分類できないため対象外とします '
                          '(v_select/h_selectで明示指定可能)' % axis_name[j])
    return v_idx, h_idx


# ------------------------------------------------------------------
# 梁レベル指定 (G_input.m / listdlg+inputdlg の代替)
# ------------------------------------------------------------------

def _build_glevel(g_level, section_no, section_name):
    """g_level指定をMATLABのG_level行列 [断面番号, flag, 値(m)] へ変換.

    g_level: None → 空 (全梁とも既定挙動)
             dict {断面番号 or 断面名: 値}
               値 0 or 'center'  : 梁芯をモデル芯で描画   (flag=0)
               値 'top'          : 梁天端をモデル芯で描画 (flag=Inf)
               値 'bottom'       : 梁下端をモデル芯で描画 (flag=-Inf)
               値 数値(mm)       : 梁天端レベルをモデル芯からのズレ[mm]で
                                   指定 (flag=1, 値はm換算 = G_input.mの
                                   str2double(...)/1000 と同じ)
             ndarray/list of [sec_no, flag, value] はそのまま使用
    """
    if g_level is None:
        return np.array([])
    if isinstance(g_level, (list, tuple, np.ndarray)) \
            and not isinstance(g_level, dict):
        arr = np.atleast_2d(np.asarray(g_level, dtype=float))
        return arr
    stripped = [space_erace(str(n)) for n in section_name]
    rows = []
    for key, val in g_level.items():
        if isinstance(key, str):
            if space_erace(key) not in stripped:
                raise KeyError('g_level: 断面名 %r が見つかりません '
                               '(断面一覧: %s)' % (key, ', '.join(stripped)))
            sec_no = float(section_no[stripped.index(space_erace(key))])
        else:
            sec_no = float(key)
        if isinstance(val, str):
            v = val.lower()
            if v in ('center', 'shin', '芯'):
                rows.append([sec_no, 0.0, 0.0])
            elif v in ('top', '天端', '天'):
                rows.append([sec_no, np.inf, np.inf])
            elif v in ('bottom', '下端', '下'):
                rows.append([sec_no, -np.inf, -np.inf])
            else:
                raise ValueError("g_level: 値は 0/'center'/'top'/'bottom'/"
                                 "数値[mm] のいずれか: %r" % val)
        elif val == 0:
            rows.append([sec_no, 0.0, 0.0])
        else:
            rows.append([sec_no, 1.0, float(val) / 1000])  # mm→m (G_input.m)
    return np.asarray(rows, dtype=float)


# ------------------------------------------------------------------
# DXFファイル書き出し (Midas_dxf.m calculate_Callback の移植)
#   dxf_Template.dxf (R12ヘッダ+TABLES+BLOCKS途中まで) をコピーし、
#   ' ' 1行を挟んで生成テキストを追記する。CP932で書き出す。
# ------------------------------------------------------------------

_TEMPLATE_NAME = 'dxf_Template.dxf'


def _inject_arial_style(template_lines):
    """DXF_STYLE_FIX: テンプレートのSTYLEテーブルへ ARIAL を追加する.

    原典(MATLAB)はTEXTエンティティにスタイル名ARIALを直書きするが、
    STYLEテーブルにはSTANDARDしか無く、AutoCADは未定義スタイル参照の
    DXFを読込拒否する (Rhinoは許容)。ユーザー承認による原典修正
    (2026-07-13)。テンプレートファイル自体は変更せず書き出し時に注入。
    """
    out = []
    i = 0
    n = len(template_lines)
    in_style = False
    done = False
    while i + 1 < n:
        code = template_lines[i].strip()
        value = template_lines[i + 1]
        if not done and code == '0' and value.strip() == 'TABLE' \
                and i + 3 < n and template_lines[i + 3].strip() == 'STYLE':
            in_style = True
        if in_style and code == '70':
            # スタイル数を+1 (右詰め幅は元の書式を踏襲)
            cnt = int(value)
            value = str(cnt + 1).rjust(len(value))
            in_style = False  # カウントは先頭の1回だけ
        if not done and code == '0' and value.strip() == 'ENDTAB' \
                and any(v.strip() == 'ARIAL' or v.strip() == 'STANDARD'
                        for v in out[-40:]):
            # STANDARD定義の直後 (STYLEテーブルのENDTAB直前) にARIALを挿入
            out.extend(['  0', 'STYLE', '  2', 'ARIAL', ' 70',
                        '               0', ' 40', '0.0', ' 41', '1.0',
                        ' 50', '0.0', ' 71', '               0', ' 42',
                        '0.0', '  3', 'arial.ttf', '  4', ''])
            done = True
        out.append(template_lines[i])
        out.append(template_lines[i + 1])
        i += 2
    out.extend(template_lines[i:])
    return out, done


def _collect_block_names(pairs):
    """(code,value)ペア列からBLOCK定義名の一覧を返す."""
    names = []
    i = 0
    n = len(pairs)
    while i < n:
        if pairs[i][0] == '0' and pairs[i][1] == 'BLOCK':
            j = i + 1
            while j < n and pairs[j][0] != '0':
                if pairs[j][0] == '2':
                    names.append(pairs[j][1])
                    break
                j += 1
        i += 1
    return names


def _write_dxf_file(out_path, dxf_lines):
    """テンプレート+生成本文を検査・修復して書き出す (両DXF共通の出口).

    実施内容 (ユーザー承認による原典修正 2026-07-13):
      DXF_STYLE_FIX   : STYLEテーブルへARIAL定義を注入
      DXF_INSERT_FIX  : 未定義ブロックへのINSERTを省略+注記
      DXF_HANDLE_FIX  : ハンドル(グループ5)の全面再採番+$HANDSEED更新
                        (原典は採番カウンタを使い回すため重複し、AutoCADが
                        「重複ハンドル」で読込中止する)
      DXF_DUPBLOCK_FIX: 同名ブロック定義の自動リネーム (AutoCADが読込拒否
                        するため。mgtの符号重複はあわせて注記で指摘)
    """
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 _TEMPLATE_NAME)
    with io.open(template_path, encoding='cp932', errors='replace') as f:
        template_text = f.read()
    template_lines = template_text.split('\n')

    # ---- DXF_STYLE_FIX ----
    template_lines, style_ok = _inject_arial_style(template_lines)
    if not style_ok:
        print('注意: DXFテンプレートのSTYLEテーブルが見つからず、ARIAL'
              'スタイルを追加できませんでした (原典どおりの出力)。')

    body_lines = [str(s) for s in dxf_lines]
    if len(template_lines) % 2 == 1 or len(body_lines) % 2 == 1:
        # グループ行の交互が崩れている場合は無加工で書き出す (保険)
        print('注意: DXFのグループ行が奇数のため検査をスキップしました '
              '(原典どおりの出力)。')
        with open(out_path, 'wb') as f:
            f.write('\n'.join(template_lines).encode('cp932',
                                                     errors='replace'))
            f.write(b' \n')
            f.write(('\n'.join(body_lines) + '\n').encode(
                'cp932', errors='replace'))
        return out_path

    def _pairs(lines):
        return [(lines[k].strip(), lines[k + 1].rstrip())
                for k in range(0, len(lines) - 1, 2)]

    # テンプレート末尾はBLOCKS途中で終わり本文が続く (原典はここに ' ' 1行を
    # 挟むが、ハンドル再採番のため結合して扱い、フィラー行は挟まない)
    pairs = _pairs(template_lines) + _pairs(body_lines)

    # ---- ブロック定義の収集 ----
    defined = _collect_block_names(pairs)
    from collections import Counter
    def_count = Counter(defined)
    dups = sorted(k for k, v in def_count.items() if v > 1)
    if dups:
        print('mgt記述の注意: 断面符号の重複により、DXF内で同名ブロックが'
              '重複定義されています。2つ目以降を自動リネームして出力します'
              'が、符号と断面の対応が紛らわしいためmgtの断面符号を一意に'
              'することを推奨します: '
              + ', '.join(dups[:12])
              + ('' if len(dups) <= 12 else ' ほか%d件' % (len(dups) - 12)))

    # INSERT参照数 (順序対応リネームの可否判定)
    ins_count = Counter()
    for k in range(len(pairs)):
        if pairs[k] == ('0', 'INSERT'):
            j = k + 1
            while j < len(pairs) and pairs[j][0] != '0':
                if pairs[j][0] == '2':
                    ins_count[pairs[j][1]] += 1
                j += 1

    def _rename(name, occ):
        # 既存名と衝突しない連番サフィックス
        cand = '%s_%d' % (name, occ)
        while cand in def_count:
            cand += '_'
        return cand

    # ---- 走査・修復 ----
    out = []
    section = None
    next_handle = 0x100
    handseed_pos = None
    def_seen = Counter()
    ins_seen = Counter()
    dropped = []
    i = 0
    n = len(pairs)
    while i < n:
        code, value = pairs[i]

        if code == '0' and value == 'SECTION' and i + 1 < n:
            section = pairs[i + 1][1]
        elif code == '0' and value == 'ENDSEC':
            section = None

        # $HANDSEED の位置を記録 (最後に採番結果で置換)
        if section == 'HEADER' and code == '9' and value == '$HANDSEED':
            out.append(pairs[i])
            handseed_pos = len(out) + 0  # 次の(5,seed)ペアの位置
            i += 1
            continue

        # DXF_HANDLE_FIX: BLOCKS/ENTITIES内のグループ5を再採番
        if section in ('BLOCKS', 'ENTITIES') and code == '5':
            out.append(('5', format(next_handle, 'X')))
            next_handle += 1
            i += 1
            continue

        # DXF_DUPBLOCK_FIX: BLOCK定義の重複リネーム + レイヤ補完
        if section == 'BLOCKS' and code == '0' and value == 'BLOCK':
            j = i + 1
            name = None
            name_positions = []
            has_layer = False
            while j < n and pairs[j][0] != '0':
                if pairs[j][0] == '2' and name is None:
                    name = pairs[j][1]
                if pairs[j][0] in ('2', '3'):
                    name_positions.append(j)
                if pairs[j][0] == '8':
                    has_layer = True
                j += 1
            out.append(pairs[i])
            if not has_layer:
                out.append(('8', '0'))
            if name is not None:
                def_seen[name] += 1
                newname = name
                if def_seen[name] > 1:
                    newname = _rename(name, def_seen[name])
                for k in range(i + 1, j):
                    c2, v2 = pairs[k]
                    if c2 == '5':
                        out.append(('5', format(next_handle, 'X')))
                        next_handle += 1
                    elif k in name_positions and v2 == name:
                        out.append((c2, newname))
                    else:
                        out.append(pairs[k])
                i = j
                continue
            i += 1
            continue

        # INSERT: 未定義参照の省略 + 重複ブロックの順序対応リネーム
        if section == 'ENTITIES' and code == '0' and value == 'INSERT':
            j = i + 1
            name = None
            name_pos = None
            while j < n and pairs[j][0] != '0':
                if pairs[j][0] == '2' and name is None:
                    name = pairs[j][1]
                    name_pos = j
                j += 1
            if name is not None and name not in def_count:
                dropped.append(name)  # DXF_INSERT_FIX
                i = j
                continue
            newname = name
            if name is not None and def_count[name] > 1 \
                    and ins_count[name] == def_count[name]:
                ins_seen[name] += 1
                if ins_seen[name] > 1:
                    newname = _rename(name, ins_seen[name])
            for k in range(i, j):
                c2, v2 = pairs[k]
                if c2 == '5':
                    out.append(('5', format(next_handle, 'X')))
                    next_handle += 1
                elif k == name_pos:
                    out.append((c2, newname))
                else:
                    out.append(pairs[k])
            i = j
            continue

        out.append(pairs[i])
        i += 1

    if dropped:
        uniq = sorted(set(dropped))
        print('注記: 断面ブロックが未定義のため、その配置(INSERT)を'
              '%d箇所省略しました (配筋(*REBAR-*)が未入力のRC断面など。'
              '原典(MATLAB)は未定義のまま配置しAutoCADで開けないDXFに'
              'なる。ユーザー承認による原典修正 2026-07-13): %s'
              % (len(dropped), ', '.join(uniq[:10])))

    # $HANDSEED を再採番結果に合わせる
    if handseed_pos is not None and handseed_pos < len(out) \
            and out[handseed_pos][0] == '5':
        out[handseed_pos] = ('5', format(next_handle, 'X'))

    # ---- 空ENTITIES (構成図で描ける構面が無い) の指摘 ----
    ent_count = 0
    in_ent = False
    for k in range(len(out)):
        if out[k] == ('2', 'ENTITIES'):
            in_ent = True
            continue
        if in_ent and out[k] == ('0', 'ENDSEC'):
            break
        if in_ent and out[k][0] == '0':
            ent_count += 1
    if ent_count == 0:
        print('mgt記述の注意: DXF(%s)に描画要素がありません。構成図は'
              'グループ(*GROUP)=構面ごとに描くため、各グループに'
              'NODE_LISTとELEM_LISTの登録が必要です (モデル図・応力図と'
              '同じ要件)。' % os.path.basename(out_path))

    lines_out = []
    for c, v in out:
        lines_out.append(c.rjust(3))
        lines_out.append(v)
    with open(out_path, 'wb') as f:
        f.write(('\n'.join(lines_out) + '\n').encode('cp932',
                                                      errors='replace'))
    return out_path

# ------------------------------------------------------------------
# メインAPI : MIDAS_dxf_fig.m の移植
# ------------------------------------------------------------------

def export_dxf(mgt_path, out_path=None, limit_sec_no=9000, fontsize=400,
               space_ele=(39.0, 0.0), v_select=None, h_select=None,
               g_level=None, section_out_path=None):
    """MIDASデータからのモデル構成図作成 (DXF出力)。

    MATLAB版 Midas_dxf(GUI) + MIDAS_dxf_fig.m の逐語移植。
    レイヤ名・BLOCK構成・文字高さ・座標値はMATLAB版と同一のDXFテキストを
    直接生成し、dxf_Template.dxf に追記して書き出す (CP932)。

    Parameters
    ----------
    mgt_path : str
        mgtファイルパス (uigetfileの代替)。
    out_path : str or None
        モデル構成図DXFの出力パス (MATLAB版のout.dxf)。
        None なら mgtと同じ場所に <mgt名>.dxf。
    limit_sec_no : int
        ダミー材と見なす断面番号の下限 (GUI: dummy_no, 初期値9000)。
    fontsize : float or sequence
        符号文字サイズ (GUI: f_d, 初期値400)。MATLAB版同様先頭要素のみ使用。
    space_ele : (float, float)
        構面図の横並び間隔 (GUI: space_eleX=39, space_eleY=0)。
        0のときは構面寸法から自動 (MATLAB版と同じ式)。
    v_select : list or None
        鉛直構面として描くグループ名(またはindex)のリスト。
        None=自動判別(節点Z座標にばらつきのあるグループ全て)。[]=描かない。
        (MATLAB版listdlg「鉛直構面を指定してください」の代替)
    h_select : list or None
        水平構面(床伏図)として描くグループ名(またはindex)のリスト。
        None=自動判別(節点Zが一定のグループ全て)。[]=描かない。
        (MATLAB版listdlg「水平構面を指定してください」の代替)
    g_level : dict or array or None
        梁レベル指定 (G_input.mの代替)。_build_glevel参照。
    section_out_path : str or None
        部材リスト図DXFの出力パス (MATLAB版のsection.dxf)。
        None なら <out_path の拡張子前>_section.dxf。

    Returns
    -------
    str : out_path (モデル構成図DXFのパス)
    """
    filename = mgt_path
    if out_path is None:
        out_path = os.path.splitext(mgt_path)[0] + '.dxf'
    if section_out_path is None:
        section_out_path = os.path.splitext(out_path)[0] + '_section.dxf'

    single_class = 'S-GRAY09'
    column_class = 'S-CYAN20'
    girder_class = 'S-YELLOW20'
    column_section_class = 'S-GREEN30'
    girder_section_class = 'S-MAGENDA30'
    name_class = 'S-RED09'

    text_single = []
    text_double = []
    text_name = []

    # 出力結果のフォントサイズ調整
    fs = np.atleast_1d(np.asarray(fontsize, dtype=float)).ravel()
    name_fontsize = fs[0]

    # モデルデータ読み込み
    node = mgtopen_node(filename)          # 節点情報
    element = mgtopen_element(filename)    # 要素情報
    plate = mgtopen_plate(filename)        # 板要素
    beam = mgtopen_beam(filename)
    truss = mgtopen_truss(filename)
    material = mgtopen_material(filename)  # 材料情報
    material_name = mgtopen_material_name(filename)  # 材料情報 # noqa: F841

    sections, section_no, section_name = mgtopen_section(filename)  # 断面形状
    axis_name, axis_element, axis_node = mgtopen_group(filename)  # グループ情報
    wall_element, wall_base = mgtopen_wall(filename, node)  # 壁要素情報
    link = mgtopen_elasticlink(filename)   # 弾性連結要素 # noqa: F841
    frm_rls = mgtopen_frm_rls(filename)    # 梁端接合条件
    constraint = mgtopen_constraint(filename)  # 支持点情報

    # （設定されている場合）RC配筋情報
    RCbeams = mgtopen_RCbeam(filename)
    RCbeam_secNO = cell2matrix(RCbeams, 1, 1) if RCbeams else np.array([])  # noqa: F841,E501
    RCcolumns = mgtopen_RCcolumn(filename)
    if _isempty(RCcolumns):
        RCcolumn_secNO = np.array([])  # noqa: F841
    else:
        RCcolumn_secNO = np.atleast_2d(RCcolumns)[:, 0]  # noqa: F841
    RCwalls = mgtopen_RCwall(filename, wall_element)  # noqa: F841

    # （設定されている場合）SRC配筋情報
    SRCbeams = mgtopen_SRCbeam(filename)
    SRCbeam_secNO = cell2matrix(SRCbeams, 1, 1) if SRCbeams else np.array([])

    element_max = int(np.max(element[:, 0]))

    # %%%%符号データBLOCK部分作成%%%%
    text_block, element_max = section_name_dxf(
        section_no, section_name, element_max, name_fontsize, name_class)

    # %%%%断面形状データBLOCK部分作成%%%%
    RC_section_no = get_RC_section(element, material)
    S_section_no = get_S_section(element, material)
    W_section_no = get_W_section(element, material)
    SRC_section_no = SRCbeam_secNO

    text_block2, element_max = section_form_dxf(
        RC_section_no, S_section_no, W_section_no, SRC_section_no,
        section_no, sections, section_name, element_max,
        column_section_class, girder_section_class,
        column_class, girder_class)

    # %%%%部材リスト図データ作成%%%%
    text_list, element_max2 = List_dxf(  # noqa: F841
        S_section_no, W_section_no, section_no, sections, section_name,
        element_max, RCbeams, RCcolumns, SRCbeams,
        column_section_class, girder_section_class, name_class)

    # %%%%分割部材の一括表示オプション%%%%
    # 全体の中で，小梁などがモデル上分割されているような，そういう部材を見つける
    unit_element, uniele_ID = _unit_elements_dxf(
        filename, element, node, limit_sec_no)

    # Glevel (梁レベル指定; G_input.mの代替)
    Glevel = _build_glevel(g_level, section_no, section_name)

    # グループ選択 (listdlgの代替)
    auto_v, auto_h = _auto_classify_groups(node, axis_node, axis_name,
                                           axis_element)
    v_axis_index = auto_v if v_select is None \
        else _resolve_group_select(axis_name, v_select, '鉛直構面')
    h_axis_index = auto_h if h_select is None \
        else _resolve_group_select(axis_name, h_select, '水平構面')

    # %%%%%%通り芯情報・寸法値など作成 (鉛直構面) %%%%%%
    num_axis = len(v_axis_index)  # 通芯の数（plotする構面の数）
    if num_axis > 0:
        axis_name_select = [axis_name[j] for j in v_axis_index]
        axis_element_select = [[axis_element[j]] for j in v_axis_index]
        axis_node_select = [[axis_node[j]] for j in v_axis_index]

        # 各通りにある節点の座標データ（Z=0に投影）を抽出
        axis_plot_coefi, axis_direction, pick_axis_node_no = \
            _axis_coefficients_dxf(node, axis_node_select, axis_name_select)

        axis_xtick = tick(node, node[:, 0].T, 2)
        axis_xtick = np.array([np.min(axis_xtick), np.max(axis_xtick)])
        axis_ytick = tick(node, node[:, 0].T, 3)
        axis_ytick = np.array([np.min(axis_ytick), np.max(axis_ytick)])
        axis_ztick = tick(node, node[:, 0].T, 4)
        axis_ztick = np.array([np.min(axis_ztick), np.max(axis_ztick)])

        base_point = [0.0, 0.0]

        for i_axis in range(num_axis):
            if axis_plot_coefi[i_axis, 0] == 1:  # X通り
                text_single, text_double, text_name, element_max = figure_dxf(
                    limit_sec_no, unit_element, axis_element_select,
                    axis_node_select, node, wall_base, wall_element, element,
                    uniele_ID, axis_name_select, i_axis,
                    axis_ztick, axis_ytick, axis_xtick, axis_plot_coefi,
                    plate, beam, truss, pick_axis_node_no, axis_direction,
                    section_no, section_name, frm_rls, constraint,
                    text_single, text_double, text_name, list(base_point),
                    sections, element_max,
                    single_class, column_class, girder_section_class,
                    girder_class, name_class, Glevel, [0, axis_ytick[0]])

                if space_ele[0] == 0:
                    base_point[0] = base_point[0] + 5 * (math.ceil(
                        (axis_ytick[1] - axis_ytick[0]) / 5) + 1)
                else:
                    base_point[0] = base_point[0] + space_ele[0]

            elif axis_plot_coefi[i_axis, 0] == 2:  # Y通り
                text_single, text_double, text_name, element_max = figure_dxf(
                    limit_sec_no, unit_element, axis_element_select,
                    axis_node_select, node, wall_base, wall_element, element,
                    uniele_ID, axis_name_select, i_axis,
                    axis_ztick, axis_ytick, axis_xtick, axis_plot_coefi,
                    plate, beam, truss, pick_axis_node_no, axis_direction,
                    section_no, section_name, frm_rls, constraint,
                    text_single, text_double, text_name, list(base_point),
                    sections, element_max,
                    single_class, column_class, girder_section_class,
                    girder_class, name_class, Glevel, [axis_xtick[0], 0])

                if space_ele[1] == 0:
                    base_point[0] = base_point[0] + 5 * (math.ceil(
                        (axis_xtick[1] - axis_xtick[0]) / 5) + 1)
                else:
                    base_point[0] = base_point[0] + space_ele[1]

    # %%%%%%通り芯情報・寸法値など作成 (水平構面) %%%%%%
    num_axis = len(h_axis_index)
    if num_axis > 0:
        axis_xtick = tick(node, node[:, 0].T, 2)
        axis_xtick = np.array([np.min(axis_xtick), np.max(axis_xtick)])
        axis_ytick = tick(node, node[:, 0].T, 3)
        axis_ytick = np.array([np.min(axis_ytick), np.max(axis_ytick)])
        axis_ztick = tick(node, node[:, 0].T, 4)
        axis_ztick = np.array([np.min(axis_ztick), np.max(axis_ztick)])

        axis_name_select = [axis_name[j] for j in h_axis_index]
        axis_element_select = [[axis_element[j]] for j in h_axis_index]
        axis_node_select = [[axis_node[j]] for j in h_axis_index]

        floor_num_axis = num_axis  # 床面の数（plotする床構面の数）
        base_point = [
            0.0,
            -1 * 5 * (math.ceil((axis_ztick[1] - axis_ztick[0]) / 5) + 1)
            - max(5 * (math.ceil((axis_ytick[1] - axis_ytick[0]) / 5) + 1),
                  5 * (math.ceil((axis_xtick[1] - axis_xtick[0]) / 5) + 1))]
        # 原点補正 (MATLAB版からの意図的な逸脱):
        # MATLAB版の床伏図は節点の全体座標をそのまま描くため、モデルが
        # 原点から離れている(例: 測量座標系のmgt)と鉛直構面図と重なる。
        # 鉛直構面図でori_point([0 ytick(1)]等)により原点補正しているのと
        # 同様に、床伏図もモデル最小座標を差し引く。モデルが原点付近に
        # あればMATLAB版と同一出力となる。
        base_point[0] = base_point[0] - axis_xtick[0]
        base_point[1] = base_point[1] - axis_ytick[0]

        for i_axis in range(floor_num_axis):
            text_single, text_double, text_name, element_max = \
                figure_dxf_floor(
                    limit_sec_no, unit_element, axis_element_select,
                    axis_node_select, node, wall_base, wall_element, element,
                    uniele_ID, axis_name_select, i_axis,
                    plate, beam, truss, section_no, section_name,
                    frm_rls, constraint,
                    text_single, text_double, text_name, list(base_point),
                    sections, element_max,
                    single_class, column_class, girder_class,
                    column_section_class, name_class)

            base_point[0] = base_point[0] + 5 * (math.ceil(
                (axis_xtick[1] - axis_xtick[0]) / 5) + 1)

    # %%%% DXFテキスト組み立て (MIDAS_dxf_fig.m 末尾) %%%%
    dxf_text = []
    dxf_text += text_block
    dxf_text += text_block2
    dxf_text += ['  0', 'ENDSEC', '  0', 'SECTION', '  2', 'ENTITIES']
    dxf_text += text_double
    dxf_text += text_single
    dxf_text += text_name
    dxf_text += ['  0', 'ENDSEC', '  0', 'EOF']

    dxf_section = []
    dxf_section += text_block
    dxf_section += text_block2
    dxf_section += ['  0', 'ENDSEC', '  0', 'SECTION', '  2', 'ENTITIES']
    dxf_section += text_list
    dxf_section += ['  0', 'ENDSEC', '  0', 'EOF']

    # %%%% ファイル書き出し (Midas_dxf.m calculate_Callback) %%%%
    # mgt記述に起因する不正座標(Inf/NaN)の指摘
    # (MATLAB版 end_add_plan.m はダミー材(断面番号>=limit_sec_no)の柱に
    #  剛接続された梁の端部延長を width2=inf とするため、床伏図の座標が
    #  Infになる。これはプログラムではなくmgtの記述方法の問題:
    #  床グループにダミー柱・ダミーブレースと剛接続の梁が含まれている)
    _bad = sum(1 for t in dxf_text if t in ('Inf', '-Inf', 'NaN'))
    if _bad:
        print('警告: DXF座標にInf/NaNが%d箇所含まれます。mgtの記述方法の'
              '問題です: 床グループ内の梁がダミー材(断面番号>=limit_sec_no=%s)'
              'の鉛直材と剛接続されています。対処: (1)床グループからダミー材'
              'に接続する梁を除外, (2)limit_sec_noを調整, (3)該当梁に材端解放'
              '(*FRAME-RLS)を定義, のいずれかを検討してください。'
              '(MATLAB版 end_add_plan.m の width2=inf と同一挙動)'
              % (_bad, num2str(limit_sec_no)))
    _write_dxf_file(out_path, dxf_text)
    _write_dxf_file(section_out_path, dxf_section)

    return out_path


def _main(argv):
    import argparse
    p = argparse.ArgumentParser(
        description='MIDAS mgt → モデル構成図DXF (Midas_dxf移植版)')
    p.add_argument('mgt_path')
    p.add_argument('-o', '--out', default=None)
    p.add_argument('--limit-sec-no', type=float, default=9000)
    p.add_argument('--fontsize', type=float, default=400)
    p.add_argument('--space-x', type=float, default=39.0)
    p.add_argument('--space-y', type=float, default=0.0)
    p.add_argument('--v-select', nargs='*', default=None)
    p.add_argument('--h-select', nargs='*', default=None)
    a = p.parse_args(argv)
    out = export_dxf(a.mgt_path, a.out, limit_sec_no=a.limit_sec_no,
                     fontsize=a.fontsize, space_ele=(a.space_x, a.space_y),
                     v_select=a.v_select, h_select=a.h_select)
    print(out)


if __name__ == '__main__':
    import sys
    _main(sys.argv[1:])
