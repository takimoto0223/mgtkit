# -*- coding: utf-8 -*-
"""Midas_QR: 層間変形角・重心・剛心・偏心率 (QR/ の移植).

MATLAB GUIツール Midas_QR (privatetool_function/MIDAS/QR/) のうち
層間変形角と偏心率 (重心・剛心・ねじり剛性・弾力半径・Re・Fe) に関する
関数群を Python/matplotlib へ移植したもの。

移植元:
  pick_sheardrift.m         : 層間変形角用 collect_node 行列の組立て
  figure_drift.m            : 層間変形角の平面図 (3図/呼出し)
  figure_rekr.m             : 剛性(Q/δ)収集 (梁・トラス・壁・板要素)
  hensin/wg_center.m        : 重心・剛心座標
  hensin/kr_re.m            : ねじり剛性kr・弾力半径re
  hensin/figure_w_center.m  : 重心図
  hensin/figure_g_center.m  : 剛心図
  MIDAS_QRplot_fig.m 972-977行 : REXY計算 (calc_REXY)
  MIDAS_QRplot_fig.m 1272-1311行 : display_textable_REXY (textable_REXY)
  shear_pick/wall_shear_pick.m : 壁せん断力抽出 (_wall_shear_pick; 私用)
  MIDAS/plot_plan_axis2.m   : 通り芯符号記入 (_plot_plan_axis2; 私用)

MATLAB版との意図的差異:
  - print_format/close_set の分岐は PDF 保存に一本化 (共通規約)。
    figure_drift はMATLAB版で print_format==3 のときのみ保存された
    2枚目・3枚目 (下端/上端変形値) も常にPDF保存する。
  - listdlg/questdlg で選んでいたケース番号 (delta_case/N_case/KX_case/
    KY_case) は 1-based index (load_case_name への添字) の引数のまま受ける。
  - load_case_index_* (deformation等の各ケース先頭行) は既存
    draw_stress._load_case_split の戻り値どおり 0-based で受ける
    (MATLAB版の 1-based 行番号 + find_index(1-based) - 1 と、
     0-based + 0-based の加算は同値)。
  - case_height は list[list[int]] (z_point への 1-based 選択index列)。
    原典の最終要素の空セルは含めないため、原典の size(case_height,2)-1 は
    len(case_height) に読み替えている (読替え箇所は各所コメント)。

主な collect_node 列 (0-based; 詳細は各docstring):
  pick_sheardrift : [層番号, 要素番号, 下端節点, 上端節点,
                     (dL, |下端dx|, |下端dy|, |上端dx|, |上端dy|) x ケース]
  figure_rekr     : [層番号, 要素番号(壁ID/板要素番号), 下端節点, 上端節点,
                     dL x delta_case, N x N_case, Kx x KX_case, Ky x KY_case]
"""

import cmath
import math
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    from .util import (
        find_index, find_zero_index, doublecheck2, space_erace, _matlab_round,
    )
    from .mgt import node_index_get
    from .ratio_pipeline import column_beam_judge, sectionget
    from .draw_model import _setup_japanese_font, _PAPER_LAND
    from .draw_stress import _wallinfo_get
except ImportError:  # スクリプト実行時
    from util import (
        find_index, find_zero_index, doublecheck2, space_erace, _matlab_round,
    )
    from mgt import node_index_get
    from ratio_pipeline import column_beam_judge, sectionget
    from draw_model import _setup_japanese_font, _PAPER_LAND
    from draw_stress import _wallinfo_get


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------

def _n2s(v, fmt):
    """MATLAB num2str(v, fmt) 相当 (前後空白を除去)."""
    return (fmt % v).strip()


_VA_MAP = {'middle': 'center', 'top': 'top', 'bottom': 'bottom',
           'center': 'center'}


def _qtext(ax, x, y, s, ha='center', va='middle', fs=5.0, rot=0.0,
           color='k', boxed=False):
    """MATLAB text(...) 相当 (色・枠付き対応)."""
    kw = dict(ha=ha, va=_VA_MAP.get(va, va), fontsize=fs, rotation=rot,
              rotation_mode='anchor', color=color, zorder=5)
    if boxed:
        kw['bbox'] = dict(boxstyle='square,pad=0.2', facecolor='white',
                          edgecolor='black', linewidth=0.3)
    ax.text(float(x), float(y), s, **kw)


def _set_paper(fig, paper_orient, paper_size):
    """figure_*.m 末尾の set(gcf,'Paper...') 相当 (常にPDF保存のため寸法のみ).

    paper_orient: 1=横長(landscape), その他=縦長(portrait)
    paper_size  : 1=A1, 2=A2, 3=A3, 4=A4
    """
    if paper_orient == 1:
        w, h = _PAPER_LAND[paper_size]
    else:
        h, w = _PAPER_LAND[paper_size]
    fig.set_size_inches(w, h)


def _new_fig():
    _setup_japanese_font()
    fig, ax = plt.subplots()
    return fig, ax


def _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM):
    """set(gca,'XTick',[],...XLim/YLim) + daspect + box on 相当."""
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(np.min(axis_xtick) - mergin_LEFT,
                np.max(axis_xtick) + mergin_RIGHT)
    ax.set_ylim(np.min(axis_ytick) - mergin_BOTTOM,
                np.max(axis_ytick) + mergin_TOP)
    ax.set_aspect('equal', adjustable='box')  # daspect([1 1 1])
    for s in ax.spines.values():              # box on
        s.set_linewidth(0.5)


def _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name, axis_node,
                     axis_plot_coefi, mergin_LEFT, mergin_BOTTOM,
                     stress_fontsize):
    """plot_plan_axis2.m: 伏図に通り芯符号を記入する.

    axis_name : list[str] (通り芯名), axis_node : list[ndarray] (通り芯上の
    節点番号列), axis_plot_coefi : ndarray (n x 3) [1(X通り)or2(Y通り), A, B]
    """
    axis_plot_coefi = np.atleast_2d(np.asarray(axis_plot_coefi, dtype=float))
    for i in range(len(axis_name)):
        pick_node_id = np.atleast_1d(
            find_index(node[:, 0], np.asarray(axis_node[i]).ravel()))
        if axis_plot_coefi[i, 0] == 1:  # X通り
            B = int(np.argmin(node[pick_node_id, 2]))
            _qtext(ax, node[pick_node_id[B], 1],
                   np.min(axis_ytick) - mergin_BOTTOM / 2,
                   space_erace(axis_name[i]), ha='center', va='middle',
                   fs=stress_fontsize, rot=0, boxed=True)
        else:  # Y通り
            B = int(np.argmin(node[pick_node_id, 1]))
            _qtext(ax, np.min(axis_xtick) - mergin_LEFT / 2,
                   node[pick_node_id[B], 2],
                   space_erace(axis_name[i]), ha='center', va='middle',
                   fs=stress_fontsize, rot=0, boxed=True)


def _plot_level_beams(ax, element, node, h2_axis_plot, limit_sec_no):
    """figure_drift.m 21-40行ほか共通: 指定レベルにある梁要素の平面プロット."""
    if np.size(element) == 0:
        return
    c_b_result = column_beam_judge(
        np.arange(element.shape[0]), element, node)
    b_index = np.where(c_b_result - 2)[0]  # 柱(2)以外 = 梁+斜め材
    h2 = np.atleast_1d(np.asarray(h2_axis_plot, dtype=float)).ravel()
    for j in range(h2.size):
        for i_beam in range(b_index.size):
            # i端の支点のZ座標
            inode_index, jnode_index = node_index_get(
                element[b_index[i_beam], 0], element, node)
            z_inode = node[inode_index, 3]
            if float(z_inode == h2[j]) == 0:
                pass
            elif element[b_index[i_beam], 2] < limit_sec_no:
                ax.plot([node[inode_index, 1], node[jnode_index, 1]],
                        [node[inode_index, 2], node[jnode_index, 2]],
                        linestyle='-', color='k', linewidth=0.5)


# ---------------------------------------------------------------------------
# pick_sheardrift.m
# ---------------------------------------------------------------------------

def pick_sheardrift(case_height, node, element, deformation, beam_stress,
                    link, limit_sec_no, sections, delta_case,
                    load_case_index_defo, z_point):
    """pick_sheardrift.m: 層間変形角算定用の collect_node 行列を組み立てる.

    各算定レベル上の節点に取り付く鉛直部材 (柱・斜め柱; 梁は除外) を集め、
    弾性連結要素の場合には接続先上部の部材へ読み替えたうえで、
    各荷重ケースの上下端変形から層間変形角を計算する。

    引数:
      case_height : list[list[int]]  各層の z_point への 1-based 選択index列
                    (原典 listdlg の選択結果。最終要素の空セルは含めない。
                     原典の size(case_height,2)-1 は len(case_height) に読替え)
      node        : ndarray (N x 4+) [節点番号, X, Y, Z, ...]
      element     : ndarray (N x 6+) [要素番号, 材料, 断面, 節点i, 節点j, β]
      deformation : ndarray (N x 5) [節点, ケース, dx, dy, dz]
      beam_stress : ndarray (N x 8) [要素, ケース, Fx, Fy, Fz, Mx, My, Mz]
      link        : ndarray (N x 3) [連結番号, 節点1, 節点2] (弾性連結) or 空
      limit_sec_no: ダミー断面のしきい値 (断面番号 > limit_sec_no の要素を除外
                    ※原典どおり最終フィルタは「>」判定)
      sections    : sectionget 用の断面テーブル list
      delta_case  : 層間変形角を計算する荷重ケースの 1-based index 列
      load_case_index_defo : deformation の各ケース先頭行 (0-based)
      z_point     : 柱脚・壁ベースの Z 座標一覧 (m)

    戻り値 collect_node : ndarray (M x (4+5*len(delta_case)))
      列 (0-based):
        0: 層番号 (1-based; case_height の何番目のレベルか)
        1: 要素番号
        2: 当該高さにある節点 (下端節点)
        3: 要素接続先の節点 (上端節点)
        4+5*j  : ケース delta_case[j] の dL = round(L_V/δ)
                 (層間変形角 1/dL。δ=上下端の水平変形差ノルム, L_V=鉛直長)
        5+5*j  : 下端節点の |dx|
        6+5*j  : 下端節点の |dy|
        7+5*j  : 上端節点の |dx|
        8+5*j  : 上端節点の |dy|
      # NOTE: 原典195-197行は梁応力に無い要素のとき列 4+j (1-based) に 0 を
      #   書き込む (5列ブロックの先頭 4+5*(j-1)+1 とは j>=2 でずれる)。
      #   原典どおり移植 (0-based では collect_node[i, 4+j0] = 0)。
    """
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    deformation = np.asarray(deformation, dtype=float)
    beam_stress = np.asarray(beam_stress, dtype=float)
    z_point = np.asarray(z_point, dtype=float).ravel()
    delta_case = [int(c) for c in np.atleast_1d(delta_case)]
    lci_defo = np.asarray(load_case_index_defo, dtype=int).ravel()
    have_link = link is not None and np.size(link) > 0
    if have_link:
        link = np.asarray(link, dtype=float)

    base_rows = []
    # 原典: for i=1:size(case_height,2)-1 → len(case_height) に読替え
    for i in range(1, len(case_height) + 1):
        ch = np.asarray(case_height[i - 1], dtype=int).ravel()
        z_level = z_point[ch - 1]
        # その高さにある節点を抽出
        node_no_on = []
        for i_h in range(1, z_level.size + 1):
            node_id_on = find_zero_index(node[:, 3] - z_level[i_h - 1])
            for nid in node_id_on:
                # NOTE: 原典は node_no./node_no.*i_h (節点番号が非零前提で=i_h)
                node_no_on.append((node[nid, 0], i_h))

        for (nno, ntag) in node_no_on:
            # その節点にある鉛直部材/ブレース材を抽出 (i端 → j端)
            # NOTE: 原典は idx./idx.*tag (1-based idx が非零のため=tag)。
            #   Python 0-based では idx=0 で NaN となるためタグ値を直接持つ。
            eid = np.concatenate((find_zero_index(element[:, 3] - nno),
                                  find_zero_index(element[:, 4] - nno)))
            eno = np.zeros((eid.size, 4))
            if eid.size:
                eno[:, 0] = element[eid, 0]
                eno[:, 1] = ntag

            # その節点にある部材が算定対象となる鉛直部材かどうかを判定
            if eno.shape[0] == 0:
                continue
            for jj in range(eno.shape[0]):
                erow = int(eid[jj])
                if element[erow, 3] == nno:  # i端が算定高さにある
                    end_node_id = find_index(node[:, 0], element[erow, 4])
                    if column_beam_judge(erow, element, node)[0] > 1:
                        if node[end_node_id, 3] <= z_level[int(eno[jj, 1]) - 1]:
                            eno[jj, 2] = 0
                            eno[jj, 3] = 0
                        else:
                            eno[jj, 2] = nno              # 当該高さにある節点
                            eno[jj, 3] = node[end_node_id, 0]  # 接続先の節点
                    else:
                        eno[jj, 2] = 0
                        eno[jj, 3] = 0
                elif element[erow, 4] == nno:  # j端が算定高さにある
                    end_node_id = find_index(node[:, 0], element[erow, 3])
                    if column_beam_judge(erow, element, node)[0] > 1:
                        if node[end_node_id, 3] <= z_level[int(eno[jj, 1]) - 1]:
                            eno[jj, 2] = 0
                            eno[jj, 3] = 0
                        else:
                            eno[jj, 2] = nno
                            eno[jj, 3] = node[end_node_id, 0]
                    else:
                        eno[jj, 2] = 0
                        eno[jj, 3] = 0
            # 梁部材は消去
            cut_id = find_zero_index(eno[:, 2])
            eno = np.delete(eno, cut_id, axis=0)

            # 弾性連結要素の場合には上の要素を探す
            for jj in range(eno.shape[0]):
                onlink = np.zeros((0, 4))
                lidx = np.zeros(0, dtype=int)
                if have_link:
                    fi2 = find_index(link[:, 1], nno)
                    fi3 = find_index(link[:, 2], nno)
                    if fi2 != -1:  # 弾性連結のi端 (原典: find_index>0)
                        if link[fi2, 2] == eno[jj, 3]:
                            # 弾性連結部材であるときには接続先の部材を探す
                            lidx = np.concatenate(
                                (find_zero_index(element[:, 3] - eno[jj, 3]),
                                 find_zero_index(element[:, 4] - eno[jj, 3])))
                            onlink = np.zeros((lidx.size, 4))
                            onlink[:, 0] = element[lidx, 0]
                    elif fi3 != -1:  # 弾性連結のj端
                        if link[fi3, 1] == eno[jj, 3]:
                            lidx = np.concatenate(
                                (find_zero_index(element[:, 3] - eno[jj, 3]),
                                 find_zero_index(element[:, 4] - eno[jj, 3])))
                            onlink = np.zeros((lidx.size, 4))
                            onlink[:, 0] = element[lidx, 0]

                    if onlink.shape[0] > 0:
                        for ll in range(onlink.shape[0]):
                            lrow = int(lidx[ll])
                            zi = node[find_index(node[:, 0],
                                                 element[lrow, 3]), 3]
                            zj = node[find_index(node[:, 0],
                                                 element[lrow, 4]), 3]
                            if zi > z_level[int(eno[jj, 1]) - 1]:
                                # i端が算定高さにある
                                end_node_id = find_index(node[:, 0],
                                                         element[lrow, 4])
                                if column_beam_judge(lrow, element,
                                                     node)[0] > 1:
                                    if node[end_node_id, 3] <= zi:
                                        onlink[ll, 1:4] = 0
                                    else:
                                        onlink[ll, 1] = eno[jj, 1]
                                        onlink[ll, 2] = eno[jj, 3]
                                        onlink[ll, 3] = node[end_node_id, 0]
                                else:
                                    onlink[ll, 1:4] = 0
                            elif zj > z_level[int(eno[jj, 1]) - 1]:
                                # j端が算定高さにある
                                end_node_id = find_index(node[:, 0],
                                                         element[lrow, 3])
                                if column_beam_judge(lrow, element,
                                                     node)[0] > 1:
                                    if node[end_node_id, 3] <= zj:
                                        onlink[ll, 1:4] = 0
                                    else:
                                        onlink[ll, 1] = eno[jj, 1]
                                        onlink[ll, 2] = eno[jj, 3]
                                        onlink[ll, 3] = node[end_node_id, 0]
                                else:
                                    onlink[ll, 1:4] = 0
                        # 梁部材は消去
                        cut_id = find_zero_index(onlink[:, 1])
                        onlink = np.delete(onlink, cut_id, axis=0)

                if onlink.shape[0] > 0:
                    if onlink.shape[0] == 1:
                        eno[jj, :] = onlink[0, :]
                    else:
                        i_link = 0  # MATLAB: i_link=1
                        print(onlink)  # 原典143行: 裸の式表示
                        # NOTE: 原典144-155行の while(1) はダミー材以外が
                        #   複数残ると index が範囲外となり得る (原典どおり)。
                        while True:
                            cut_check = sectionget(onlink[i_link, 0], element,
                                                   sections, limit_sec_no)
                            if cut_check[0] >= limit_sec_no:
                                onlink = np.delete(onlink, i_link, axis=0)
                            if onlink.shape[0] == 1:
                                eno[jj, :] = onlink[0, :]
                                break
                            else:
                                i_link += 1
            if eno.shape[0] > 0:
                for jj in range(eno.shape[0]):
                    base_rows.append([i, eno[jj, 0], eno[jj, 2], eno[jj, 3]])

    collect_node = np.asarray(base_rows, dtype=float)
    # NOTE: 対象部材が1本も無い場合、原典171行の element(...) 添字で
    #   エラーになるのと同等 (ここでは明示的に停止)。
    if collect_node.size == 0:
        raise RuntimeError('pick_sheardrift: 算定対象の鉛直部材がありません')

    # 各鉛直部材ごとに変形，剛性，および軸力を計算
    sec_no_list = element[np.atleast_1d(
        find_index(element[:, 0], collect_node[:, 1])), 2]
    cut_index = np.where(sec_no_list > limit_sec_no)[0]
    collect_node = np.delete(collect_node, cut_index, axis=0)

    # 層間変形にする: 柱がトラス下弦材などの中間節点で分割されている場合、
    # 原典は部材上端 (=中間節点) までの変形角になるため、鉛直に連続する
    # 部材をたどって上端節点を層の上端レベルまで持ち上げる
    # (原典から意図的に修正 2026-07-18)
    z_tops = []
    for i2 in range(len(case_height)):
        if i2 + 1 < len(case_height):
            nx = np.asarray(case_height[i2 + 1], dtype=int).ravel()
            z_tops.append(float(np.min(z_point[nx - 1])))
        else:
            z_tops.append(float('inf'))
    for r in range(collect_node.shape[0]):
        z_top = z_tops[int(collect_node[r, 0]) - 1]
        for _guard in range(50):
            top_no = collect_node[r, 3]
            tid = find_index(node[:, 0], top_no)
            if node[tid, 3] >= z_top - 1e-6:
                break
            cand = np.concatenate(
                (find_zero_index(element[:, 3] - top_no),
                 find_zero_index(element[:, 4] - top_no)))
            nxt = None
            for c in cand:
                c = int(c)
                if element[c, 2] > limit_sec_no:
                    continue
                if column_beam_judge(c, element, node)[0] <= 1:
                    continue
                other = (element[c, 4] if element[c, 3] == top_no
                         else element[c, 3])
                oid = find_index(node[:, 0], other)
                if node[oid, 3] > node[tid, 3] + 1e-9:
                    nxt = other
                    break
            if nxt is None:
                break
            collect_node[r, 3] = nxt

    full = np.zeros((collect_node.shape[0], 4 + 5 * len(delta_case)))
    full[:, :4] = collect_node
    collect_node = full

    for i in range(collect_node.shape[0]):
        # NOTE: 原典177-181行: 要素i/j端で求めた index を直後に
        #   collect_node 3/4列の節点で上書きしている (原典どおり)。
        erow = find_index(element[:, 0], collect_node[i, 1])
        nodei_index = find_index(node[:, 0], element[erow, 3])
        nodej_index = find_index(node[:, 0], element[erow, 4])

        nodei_index = find_index(node[:, 0], collect_node[i, 2])
        nodej_index = find_index(node[:, 0], collect_node[i, 3])

        # まずは部材の鉛直を検討する
        XAXIS = node[nodej_index, 1:4] - node[nodei_index, 1:4]
        XAXIS = XAXIS / np.linalg.norm(XAXIS)

        L = np.linalg.norm(node[nodei_index, 1:4] - node[nodej_index, 1:4])
        L_H = np.linalg.norm(node[nodei_index, 1:3] - node[nodej_index, 1:3])
        L_X = np.linalg.norm(node[nodei_index, 1] - node[nodej_index, 1])
        L_Y = np.linalg.norm(node[nodei_index, 2] - node[nodej_index, 2])
        L_V = np.linalg.norm(node[nodei_index, 3] - node[nodej_index, 3])

        for j0, dc in enumerate(delta_case):  # 層間変形角
            if find_index(beam_stress[:, 0], collect_node[i, 1]) == -1:
                # 層間変形角の算定対象は梁要素とする
                collect_node[i, 4 + j0] = 0  # NOTE: 原典 4+j 列 (上記docstring)
            else:
                get_defo_id = (np.array(
                    [find_index(deformation[:, 0], collect_node[i, 2]),
                     find_index(deformation[:, 0], collect_node[i, 3])])
                    + lci_defo[dc - 1])
                delta = np.linalg.norm(deformation[get_defo_id[0], 2:4]
                                       - deformation[get_defo_id[1], 2:4])
                dL = _matlab_round(L_V / delta)

                collect_node[i, 4 + 5 * j0] = dL
                collect_node[i, 5 + 5 * j0:7 + 5 * j0] = np.abs(
                    deformation[get_defo_id[0], 2:4])
                collect_node[i, 7 + 5 * j0:9 + 5 * j0] = np.abs(
                    deformation[get_defo_id[1], 2:4])

    return collect_node


# ---------------------------------------------------------------------------
# figure_drift.m
# ---------------------------------------------------------------------------

def figure_drift(stress_fontsize, mergin_LEFT, mergin_RIGHT, mergin_TOP,
                 mergin_BOTTOM, title_fontsize, load_case_name, collect_node,
                 i, node, axis_xtick, axis_ytick, delta_case, z_point,
                 paper_orient, paper_size, fig_no, case_height,
                 axis_name_select, axis_element_select, axis_node_select,
                 axis_plot_coefi, element, limit_sec_no, out_dir='.'):
    """figure_drift.m: 層間変形角の平面図をPDF保存する (3図).

    1図目: 層間変形角一覧 (1/dL。最大層間変形角に注記)
    2図目: 鉛直部材下端変形値 (DX,DY) [mm]
    3図目: 鉛直部材上端変形値 (DX,DY) [mm]
    保存名はいずれも '<fig_no>-drift.pdf' (fig_no は1図ごとに+1)。
    # NOTE: 原典は1図目のみ常時 eps 保存し、2・3図目は print_format==3 の
    #   ときのみ保存 (かつ2図目保存後に fig_no を増やさず同名上書きの癖)。
    #   本移植は常にPDF保存・毎回 fig_no を増やす。

    引数:
      collect_node : pick_sheardrift の戻り値のうち当該レベルの行のみ
      i            : delta_case ループの 1-based index (5列ブロックの選択)
      delta_case   : 荷重ケースの 1-based index (スカラ; load_case_name への添字)
      case_height  : 当該レベルの z_point への 1-based 選択index列
                     (原典呼出しの case_height{ij})
      axis_name_select/axis_node_select/axis_plot_coefi :
        通り芯符号 (plot_plan_axis2 用)。axis_element_select は原典どおり未使用。
      z_point      : Z 座標一覧 (m)
    戻り値: 更新後の fig_no
    """
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    collect_node = np.asarray(collect_node, dtype=float).copy()
    delta_case = int(np.atleast_1d(delta_case)[0])
    z_point = np.asarray(z_point, dtype=float).ravel()
    ch = np.asarray(case_height, dtype=int).ravel()

    stress_fontsize = stress_fontsize + 2
    h2_axis_plot = z_point[ch - 1]
    if ch.size == 1:
        h_axis_plot = _n2s(z_point[ch[0] - 1], '%15.2f')
    else:
        h_axis_plot = '/'.join(_n2s(z_point[c - 1], '%15.2f') for c in ch)

    c0 = 4 + 5 * (i - 1)  # dL列 (0-based; 原典 4+5*(i-1)+1 列)

    # ---------------- 1図目: 層間変形角一覧 ----------------
    fig, ax = _new_fig()

    _plot_level_beams(ax, element, node, h2_axis_plot, limit_sec_no)

    cut_id = find_zero_index(collect_node[:, c0])
    collect_node = np.delete(collect_node, cut_id, axis=0)
    # MATLAB: min([]) は空を返しループも回らないため、空なら添字計算しない
    a_index = (int(np.argmin(collect_node[:, c0]))
               if collect_node.shape[0] > 0 else -1)
    for j in range(collect_node.shape[0]):
        if collect_node[j, c0] > 0:
            node_index = find_index(node[:, 0], collect_node[j, 2])
            if j == a_index:
                _qtext(ax, node[node_index, 1], node[node_index, 2],
                       '最大層間変形角：', ha='right', va='top',
                       fs=stress_fontsize + 1)
                _qtext(ax, node[node_index, 1], node[node_index, 2],
                       '1/' + _n2s(collect_node[j, c0], '%15.0f'),
                       ha='left', va='top', fs=stress_fontsize + 1)
            else:
                _qtext(ax, node[node_index, 1], node[node_index, 2],
                       '1/' + _n2s(collect_node[j, c0], '%15.0f'),
                       ha='left', va='top', fs=stress_fontsize, rot=45)

    # 通り芯情報を記入する
    _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name_select,
                     axis_node_select, axis_plot_coefi, mergin_LEFT,
                     mergin_BOTTOM, stress_fontsize)

    ax.set_title('層間変形角一覧[　　荷重ケース：'
                 + str(load_case_name[delta_case - 1]).rstrip() + '　]',
                 fontsize=title_fontsize)
    ax.set_xlabel('表示レベル：' + h_axis_plot + 'm', fontsize=title_fontsize)
    _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM)
    _set_paper(fig, paper_orient, paper_size)
    fig.savefig(os.path.join(out_dir, '%d-drift.pdf' % fig_no), format='pdf')
    plt.close(fig)
    fig_no = fig_no + 1

    # ---------------- 2図目: 鉛直部材下端変形値 ----------------
    fig, ax = _new_fig()
    _plot_level_beams(ax, element, node, h2_axis_plot, limit_sec_no)

    cut_id = find_zero_index(collect_node[:, c0])
    collect_node = np.delete(collect_node, cut_id, axis=0)
    for j in range(collect_node.shape[0]):
        if collect_node[j, c0] > 0:
            node_index = find_index(node[:, 0], collect_node[j, 2])
            _qtext(ax, node[node_index, 1], node[node_index, 2], '(DX,DY)=',
                   ha='center', va='bottom', fs=stress_fontsize)
            _qtext(ax, node[node_index, 1], node[node_index, 2],
                   '(' + _n2s(collect_node[j, c0 + 1] * 1000, '%15.2f') + ','
                   + _n2s(collect_node[j, c0 + 2] * 1000, '%15.2f') + ')',
                   ha='center', va='top', fs=stress_fontsize)

    _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name_select,
                     axis_node_select, axis_plot_coefi, mergin_LEFT,
                     mergin_BOTTOM, stress_fontsize)
    ax.set_title('鉛直部材下端変形値(mm)[　　荷重ケース：'
                 + str(load_case_name[delta_case - 1]).rstrip() + '　]',
                 fontsize=title_fontsize)
    ax.set_xlabel('表示レベル：' + h_axis_plot + 'm', fontsize=title_fontsize)
    _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM)
    _set_paper(fig, paper_orient, paper_size)
    fig.savefig(os.path.join(out_dir, '%d-drift.pdf' % fig_no), format='pdf')
    plt.close(fig)
    fig_no = fig_no + 1

    # ---------------- 3図目: 鉛直部材上端変形値 ----------------
    fig, ax = _new_fig()
    _plot_level_beams(ax, element, node, h2_axis_plot, limit_sec_no)

    cut_id = find_zero_index(collect_node[:, c0])
    collect_node = np.delete(collect_node, cut_id, axis=0)
    for j in range(collect_node.shape[0]):
        if collect_node[j, c0] > 0:
            node_index = find_index(node[:, 0], collect_node[j, 2])
            _qtext(ax, node[node_index, 1], node[node_index, 2], '(DX,DY)=',
                   ha='center', va='bottom', fs=stress_fontsize)
            _qtext(ax, node[node_index, 1], node[node_index, 2],
                   '(' + _n2s(collect_node[j, c0 + 3] * 1000, '%15.2f') + ','
                   + _n2s(collect_node[j, c0 + 4] * 1000, '%15.2f') + ')',
                   ha='center', va='top', fs=stress_fontsize)

    _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name_select,
                     axis_node_select, axis_plot_coefi, mergin_LEFT,
                     mergin_BOTTOM, stress_fontsize)
    ax.set_title('鉛直部材上端変形値(mm)[　　荷重ケース：'
                 + str(load_case_name[delta_case - 1]).rstrip() + '　]',
                 fontsize=title_fontsize)
    ax.set_xlabel('表示レベル：' + h_axis_plot + 'm', fontsize=title_fontsize)
    _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM)
    _set_paper(fig, paper_orient, paper_size)
    fig.savefig(os.path.join(out_dir, '%d-drift.pdf' % fig_no), format='pdf')
    plt.close(fig)
    fig_no = fig_no + 1

    return fig_no


# ---------------------------------------------------------------------------
# figure_rekr.m 用の内部関数
# ---------------------------------------------------------------------------

def _crot3(n, ang):
    """figure_rekr.m の 4x4 回転行列 (軸n, 角ang) の 3x3 部分 (複素対応).

    MATLAB版は asin の引数が [-1,1] を外れると複素数のまま計算を続け、
    最後に real() をとるため、cmath による複素演算で忠実に再現する。
    """
    nx, ny, nz = complex(n[0]), complex(n[1]), complex(n[2])
    c, s = cmath.cos(ang), cmath.sin(ang)
    return np.array([
        [nx * nx * (1 - c) + c, nx * ny * (1 - c) - nz * s,
         nz * nx * (1 - c) + ny * s],
        [nx * ny * (1 - c) + nz * s, ny * ny * (1 - c) + c,
         ny * nz * (1 - c) - nx * s],
        [nz * nx * (1 - c) - ny * s, ny * nz * (1 - c) + nx * s,
         nz * nz * (1 - c) + c]], dtype=complex)


def _skew_yz_axes(XAXIS, beta_deg):
    """figure_rekr.m 392-435行 (=520-553行): 斜め柱の局所Y/Z軸を計算する.

    X方向・Y方向の剛性計算で全く同一のコードが重複しているため共通化。
    戻り値 (YAXIS, ZAXIS) : complex ndarray (成分は最後に real をとる)。
    """
    if XAXIS[0] >= 0:
        fai = cmath.asin(-1 * complex(XAXIS[2]))
    else:
        fai = -math.pi - cmath.asin(-1 * complex(XAXIS[2]))
    sita = cmath.asin(-1 * complex(XAXIS[1]) / cmath.cos(fai))
    R = np.array([[cmath.cos(sita), cmath.sin(sita), 0],
                  [-cmath.sin(sita), cmath.cos(sita), 0],
                  [0, 0, 1]], dtype=complex)
    ZAXIS = R @ np.array([0, 0, 1], dtype=complex)
    YAXIS = R @ np.array([0, 1, 0], dtype=complex)

    ZAXIS = _crot3(YAXIS, fai) @ ZAXIS

    # ここでもしZ軸のZ座標が負だと，Z軸周りに180度回転させる．
    # NOTE: MATLABの複素数比較は実部で行われるため .real で比較。
    if ZAXIS[2].real < 0:
        Rf = _crot3(XAXIS, math.pi)
        ZAXIS = Rf @ ZAXIS
        YAXIS = Rf @ YAXIS

    beta = beta_deg / 180 * math.pi
    ZAXIS = _crot3(XAXIS, beta) @ ZAXIS
    YAXIS = _crot3(XAXIS, beta) @ YAXIS
    return YAXIS, ZAXIS


def _wall_shear_pick(wall_stress_ID, wall_stress, wall_element, node, axis,
                     load_case):
    """wall_shear_pick.m: 耐力壁1枚の指定方向せん断力を取り出す (私用).

    # NOTE: shear_pick/ 一式は別モジュール担当のため、ここでは figure_rekr が
    #   必要とする分のみ私用関数として逐語移植した (重複の可能性あり)。

    引数:
      wall_stress_ID : 当該壁の [レベル, 壁ID, ケース] 行 (1行 or 複数行)
      wall_stress    : 対応する応力 2行ブロック (2N x 6)
      axis           : 2=X方向, 3=Y方向 (原典: sei_direction=axis-1)
      load_case      : 実荷重ケース番号 (load_case_no_wall の値)
    """
    wall_stress_ID = np.atleast_2d(np.asarray(wall_stress_ID, dtype=float))
    wall_stress = np.atleast_2d(np.asarray(wall_stress, dtype=float))

    sei_direction = axis - 1
    if sei_direction == 1:
        shear_direction = (1.0, 0.0)
    elif sei_direction == 2:
        shear_direction = (0.0, 1.0)
    elif sei_direction == 3:
        shear_direction = (1 / math.sqrt(2), 1 / math.sqrt(2))
    elif sei_direction == 4:
        shear_direction = (-1 / math.sqrt(2), 1 / math.sqrt(2))

    w_shear_i = None
    for i0 in range(wall_stress_ID.shape[0]):
        if wall_stress_ID[i0, 2] == load_case:
            WQ_Z = wall_stress[2 * i0 + 1, 2]  # 耐力壁脚のせん断力
            WQ_Y = wall_stress[2 * i0 + 1, 1]
            select_wall, stress_ID = _wallinfo_get(wall_element,
                                                   wall_stress_ID[i0, 0:2])
            if np.size(stress_ID) == 0:
                print('ERROR:応力に対応する要素判定ミス')
                raise RuntimeError(
                    '原典stop: wall_shear_pick 応力に対応する要素判定ミス')
            else:
                # NOTE: 原典 wall_vec(1)/(2) は線形index (複数行時は列優先)。
                wv = np.asarray(wall_element, dtype=float)[
                    np.atleast_1d(stress_ID), 8:10].flatten(order='F')
                wall_vec = (wv[0], wv[1])
                zc = (wall_vec[0] + wall_vec[1] * 1j) * 1j
                wall_normal = (zc.real, zc.imag)
                qz_cos = (shear_direction[0] * wall_vec[0]
                          + shear_direction[1] * wall_vec[1])
                qy_cos = (shear_direction[0] * wall_normal[0]
                          + shear_direction[1] * wall_normal[1])
                w_shear_i = WQ_Z * qz_cos - WQ_Y * qy_cos
    if w_shear_i is None:
        # 原典は未代入のままreturnしエラーになる
        raise RuntimeError(
            '原典stop: wall_shear_pick 荷重ケース %s の壁応力が見つかりません'
            % load_case)
    return w_shear_i


# ---------------------------------------------------------------------------
# figure_rekr.m
# ---------------------------------------------------------------------------

def figure_rekr(limit_sec_no, unit_element, node, wall_base, wall_stress_ID,
                wall_stress, wall_element, beam_stress, truss_stress, element,
                uniele_ID, load_case_index_beam, load_case_index_truss,
                load_case_index_wall, load_case_no_beam, load_case_no_wall,
                load_case_name, z_point, link, sections, case_height,
                deformation, delta_case, N_case, KX_case, KY_case,
                load_case_index_defo, plate, plate_stress,
                load_case_index_plate, load_case_no_plate):
    """figure_rekr.m: 偏心率算定用の剛性(Q/δ)・軸力を collect_node に集める.

    各算定レベル上の鉛直部材 (梁要素=柱/斜め柱・トラス・壁要素・板要素) を
    集め、N/KX/KY 各ケースの変形とせん断力から剛性を計算する。

    引数 (主なもの):
      case_height : list[list[int]]  z_point への 1-based 選択index列
                    (原典の size(case_height,2)-1 は len(case_height) に読替え)
      delta_case  : 層間変形角ケースの 1-based index 列 (原典呼出しは [] 固定)
      N_case/KX_case/KY_case : 重心/X剛性/Y剛性ケースの 1-based index
                    (listdlg 'single' 選択のためスカラ可)
      load_case_index_beam/truss/wall/defo/plate :
        各応力/変形データのケース先頭行 (0-based; _load_case_split の戻り値)
      load_case_no_wall/beam/plate : 実荷重ケース番号列
      wall_stress_ID : (N x 3) [レベル, 壁ID, ケース]
      wall_stress    : (2N x 6) 壁応力 (get_wstress 整形後)
      plate          : (N x 9) [要素, 材料, 厚さ, 節点1..4, iSUB, iWID]
      plate_stress   : (N x 4) [要素, ケース, 節点, Fxy]
      unit_element / wall_base / uniele_ID / load_case_no_beam /
      load_case_name : 原典どおり未使用 (シグネチャ維持のため受ける)

    戻り値: (collect_node, node)
      collect_node : ndarray (M x (4+len(delta)+len(N)+len(KX)+len(KY)))
        列 (0-based):
          0: 層番号 (1-based) / 1: 要素番号(壁は壁ID・板は板要素番号)
          2: 下端節点 (壁・板は新設代表節点) / 3: 上端節点 (同)
          4..: dLブロック, Nブロック, Kxブロック, Kyブロック
      node : 入力 node の末尾に壁・板要素の代表節点 (上下中央点; 節点番号は
        max(node番号)+連番) を追加したもの。
        # 原典は引数 node を書き換えて返す。追加されるのは
        #   壁: 2行/壁 (下端中央・上端中央), 板: 2行/板要素。
        #   既存行は変更されない。

    # NOTE (原典の癖・疑義):
    #   - 163-171行: 弾性連結先が複数のとき i_link=1 のまま for を回すため
    #     常に先頭行のみ断面チェックされる。
    #   - 130-131行: i端側リンク先の非鉛直部材で else 側が零消去でなく
    #     代入になっている (j端側と非対称)。
    #   - 181行: add_element_no_on を jj ループ内で毎回連結するため
    #     重複行が発生する (後段 doublecheck2 で解消)。
    #   - 300行: 最上層で対象部材が無いと L_std がエラー (原典はstale jで
    #     不定動作; 本移植では RuntimeError)。
    #   - 597-600行: 壁形状は find_index による最初の一致行から取る
    #     (同一壁IDが複数レベルにある場合は最初のレベルの形状)。
    #   - 226行: 板要素 k端分岐の other_node_id が [4 4 7] 列と
    #     重複しており (5列が欠落)、原典どおり移植。
    """
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    deformation = np.asarray(deformation, dtype=float)
    beam_stress = np.asarray(beam_stress, dtype=float)
    z_point = np.asarray(z_point, dtype=float).ravel()
    delta_case = [int(c) for c in np.atleast_1d(delta_case)] \
        if delta_case is not None and np.size(delta_case) else []
    N_case = [int(c) for c in np.atleast_1d(N_case)]
    KX_case = [int(c) for c in np.atleast_1d(KX_case)]
    KY_case = [int(c) for c in np.atleast_1d(KY_case)]
    lci_defo = np.asarray(load_case_index_defo, dtype=int).ravel()
    lci_beam = np.asarray(load_case_index_beam, dtype=int).ravel()
    lci_truss = (np.asarray(load_case_index_truss, dtype=int).ravel()
                 if np.size(load_case_index_truss) else np.zeros(0, dtype=int))
    lci_wall = (np.asarray(load_case_index_wall, dtype=int).ravel()
                if np.size(load_case_index_wall) else np.zeros(0, dtype=int))
    lci_plate = (np.asarray(load_case_index_plate, dtype=int).ravel()
                 if np.size(load_case_index_plate) else np.zeros(0, dtype=int))
    have_link = link is not None and np.size(link) > 0
    if have_link:
        link = np.asarray(link, dtype=float)
    have_plate = plate is not None and np.size(plate) > 0
    if have_plate:
        plate = np.asarray(plate, dtype=float)
    have_wall = wall_element is not None and np.size(wall_element) > 0
    if have_wall:
        wall_element = np.asarray(wall_element, dtype=float)
        wall_stress_ID = np.asarray(wall_stress_ID, dtype=float)
        wall_stress = np.asarray(wall_stress, dtype=float)
    have_truss = truss_stress is not None and np.size(truss_stress) > 0
    if have_truss:
        truss_stress = np.asarray(truss_stress, dtype=float)

    jd, jn, jx, jy = (len(delta_case), len(N_case), len(KX_case),
                      len(KY_case))
    ncols = 4 + jd + jn + jx + jy
    n_level = len(case_height)  # 原典: size(case_height,2)-1

    collect_node = np.zeros((0, 4))
    collect_node_plate = np.zeros((0, 4))
    collect_node_wall = np.zeros((0, 6))
    collect_shear = np.zeros((n_level, 2))

    for i_height in range(1, n_level + 1):
        z1_point = z_point[
            np.asarray(case_height[i_height - 1], dtype=int).ravel() - 1]

        # その高さにある節点を抽出
        for base_i in range(z1_point.size):
            node_id_on = find_zero_index(node[:, 3] - z1_point[base_i])
            node_no_on = node[node_id_on, 0]
            for j in range(node_no_on.size):
                nno = node_no_on[j]
                # その節点にある鉛直部材/ブレース材を抽出 (i端 → j端)
                eid = np.concatenate(
                    (find_zero_index(element[:, 3] - nno),
                     find_zero_index(element[:, 4] - nno)))
                eno = np.zeros((eid.size, 3))
                if eid.size:
                    eno[:, 0] = element[eid, 0]

                # その節点にある板要素を抽出
                if not have_plate:
                    pid = np.zeros(0, dtype=int)
                    pno = np.zeros((0, 3))
                else:
                    pid = np.concatenate(
                        (find_zero_index(plate[:, 3] - nno),   # i端
                         find_zero_index(plate[:, 4] - nno),   # j端
                         find_zero_index(plate[:, 5] - nno),   # k端
                         find_zero_index(plate[:, 6] - nno)))  # l端
                    pno = np.zeros((pid.size, 3))
                    if pid.size:
                        pno[:, 0] = plate[pid, 0]

                # その節点にある壁要素を抽出
                if not have_wall:
                    wid = np.zeros(0, dtype=int)
                    wID = np.zeros(0)
                else:
                    wid = np.concatenate(
                        (find_zero_index(wall_element[:, 2] - nno),   # i端
                         find_zero_index(wall_element[:, 3] - nno)))  # j端
                    wID = wall_element[wid, 1]

                # その節点にある部材が算定対象となる鉛直部材かどうかを判定
                if eno.shape[0] > 0:
                    add_eno = np.zeros((0, 3))
                    for jj in range(eno.shape[0]):
                        erow = int(eid[jj])
                        if element[erow, 3] == nno:  # i端が算定高さにある
                            end_node_id = find_index(node[:, 0],
                                                     element[erow, 4])
                            if column_beam_judge(erow, element, node)[0] > 1:
                                if node[end_node_id, 3] <= z1_point[base_i]:
                                    eno[jj, 1] = 0
                                    eno[jj, 2] = 0
                                else:
                                    eno[jj, 1] = nno
                                    eno[jj, 2] = node[end_node_id, 0]
                            else:
                                eno[jj, 1] = 0
                                eno[jj, 2] = 0
                        elif element[erow, 4] == nno:  # j端が算定高さにある
                            end_node_id = find_index(node[:, 0],
                                                     element[erow, 3])
                            if column_beam_judge(erow, element, node)[0] > 1:
                                if node[end_node_id, 3] <= z1_point[base_i]:
                                    eno[jj, 1] = 0
                                    eno[jj, 2] = 0
                                else:
                                    eno[jj, 1] = nno
                                    eno[jj, 2] = node[end_node_id, 0]
                            else:
                                eno[jj, 1] = 0
                                eno[jj, 2] = 0
                    # 水平部材は消去
                    cut_id = find_zero_index(eno[:, 1])
                    eno = np.delete(eno, cut_id, axis=0)

                    # 弾性連結要素の場合には上の要素を探す
                    n0 = eno.shape[0]  # MATLAB for の範囲はループ開始時に確定
                    for jj in range(n0):
                        onlink = np.zeros((0, 3))
                        lidx = np.zeros(0, dtype=int)
                        cut_height = node[
                            find_index(node[:, 0], eno[jj, 2]), 3]
                        if have_link:
                            fi2 = find_index(link[:, 1], nno)
                            fi3 = find_index(link[:, 2], nno)
                            if fi2 != -1:
                                if link[fi2, 2] == eno[jj, 2]:
                                    lidx = np.concatenate(
                                        (find_zero_index(
                                            element[:, 3] - eno[jj, 2]),
                                         find_zero_index(
                                            element[:, 4] - eno[jj, 2])))
                                    onlink = np.zeros((lidx.size, 3))
                                    onlink[:, 0] = element[lidx, 0]
                            elif fi3 != -1:
                                if link[fi3, 1] == eno[jj, 2]:
                                    lidx = np.concatenate(
                                        (find_zero_index(
                                            element[:, 3] - eno[jj, 2]),
                                         find_zero_index(
                                            element[:, 4] - eno[jj, 2])))
                                    onlink = np.zeros((lidx.size, 3))
                                    onlink[:, 0] = element[lidx, 0]

                        if onlink.shape[0] > 0:
                            for ll in range(onlink.shape[0]):
                                lrow = int(lidx[ll])
                                if element[lrow, 3] == eno[jj, 2]:
                                    # i端が算定高さにある
                                    end_node_id = find_index(
                                        node[:, 0], element[lrow, 4])
                                    if column_beam_judge(
                                            lrow, element, node)[0] > 1:
                                        if node[end_node_id, 3] <= cut_height:
                                            onlink[ll, 1] = 0
                                            onlink[ll, 2] = 0
                                        else:
                                            onlink[ll, 1] = eno[jj, 2]
                                            onlink[ll, 2] = node[
                                                end_node_id, 0]
                                    else:
                                        # NOTE: 原典130-131行 (j端側と非対称に
                                        #   零消去でなく代入)
                                        onlink[ll, 1] = eno[jj, 2]
                                        onlink[ll, 2] = node[end_node_id, 0]
                                elif element[lrow, 4] == eno[jj, 2]:
                                    # j端が算定高さにある
                                    end_node_id = find_index(
                                        node[:, 0], element[lrow, 3])
                                    if column_beam_judge(
                                            lrow, element, node)[0] > 1:
                                        if node[end_node_id, 3] <= cut_height:
                                            onlink[ll, 1] = 0
                                            onlink[ll, 2] = 0
                                        else:
                                            onlink[ll, 1] = eno[jj, 2]
                                            onlink[ll, 2] = node[
                                                end_node_id, 0]
                                    else:
                                        onlink[ll, 1] = 0
                                        onlink[ll, 2] = 0
                            # 水平部材は消去
                            cut_id = find_zero_index(onlink[:, 1])
                            onlink = np.delete(onlink, cut_id, axis=0)

                        if onlink.shape[0] > 0:
                            if onlink.shape[0] == 1:
                                eno[jj, :] = onlink[0, :]
                            else:
                                i_link = 0  # MATLAB: i_link=1 のまま固定
                                # NOTE: 原典164-171行は i_link を増やさないため
                                #   先頭行のみ断面チェックされる (原典どおり)。
                                for _i_cycle in range(onlink.shape[0]):
                                    cut_check = sectionget(
                                        onlink[i_link, 0], element, sections,
                                        limit_sec_no)
                                    if cut_check[0] >= limit_sec_no:
                                        onlink[i_link, 1] = 0
                                cut_id = find_zero_index(onlink[:, 1])
                                onlink = np.delete(onlink, cut_id, axis=0)
                                eno[jj, :] = onlink[0, :]
                                if onlink.shape[0] > 1:
                                    add_eno = np.vstack(
                                        (add_eno, onlink[1:, :]))

                        # NOTE: 原典181行 (jjループ内で毎回連結 → 重複発生)
                        if add_eno.shape[0] > 0:
                            eno = np.vstack((eno, add_eno))

                    # 梁要素の算定対象部材
                    for jj in range(eno.shape[0]):
                        cut_check = sectionget(eno[jj, 0], element, sections,
                                               limit_sec_no)
                        if cut_check[0] >= limit_sec_no:
                            pass
                        else:
                            collect_node = np.vstack(
                                (collect_node,
                                 [i_height, eno[jj, 0], eno[jj, 1],
                                  eno[jj, 2]]))

                # 板要素の算定対象部材確認
                if pno.shape[0] > 0:
                    for jj in range(pno.shape[0]):
                        prow = int(pid[jj])
                        if np.prod(plate[prow, 3:7]) > 0:  # 四角要素
                            hit = None
                            if plate[prow, 3] == nno:      # i端が算定高さ
                                other_cols = [4, 5, 6]
                            elif plate[prow, 4] == nno:    # j端が算定高さ
                                other_cols = [3, 5, 6]
                            elif plate[prow, 5] == nno:    # k端が算定高さ
                                # NOTE: 原典226行は [4 4 7] 列 (重複; 原典どおり)
                                other_cols = [3, 3, 6]
                            elif plate[prow, 6] == nno:    # l端が算定高さ
                                other_cols = [3, 4, 5]
                            else:
                                other_cols = None
                            if other_cols is not None:
                                other_node_id = np.array(
                                    [find_index(node[:, 0], plate[prow, c])
                                     for c in other_cols])
                                if np.sum(node[other_node_id, 3]
                                          > z1_point[base_i]) == 2:
                                    hit = np.where(
                                        node[other_node_id, 3]
                                        == z1_point[base_i])[0]
                                    pno[jj, 1] = nno
                                    # 一致が複数ならMATLAB同様エラー (.item())
                                    pno[jj, 2] = node[
                                        other_node_id[hit], 0].item()
                                else:
                                    pno[jj, 1:3] = 0
                        else:  # △要素
                            # 未対応
                            pno[jj, 1:3] = 0
                    # 水平部材は消去
                    cut_id = find_zero_index(pno[:, 1])
                    pno = np.delete(pno, cut_id, axis=0)

                    # 板要素の算定対象部材集計
                    for jj in range(pno.shape[0]):
                        collect_node_plate = np.vstack(
                            (collect_node_plate,
                             [i_height, pno[jj, 0], pno[jj, 1], pno[jj, 2]]))

                # 壁要素の算定対象部材
                for jj in range(wid.size):
                    collect_node_wall = np.vstack(
                        (collect_node_wall,
                         np.concatenate(([i_height, wID[jj]],
                                         wall_element[wid[jj], 2:6]))))

            # 重複要素を削除
            if collect_node.shape[0] > 0:
                collect_node = doublecheck2(collect_node)
            if collect_node_plate.shape[0] > 0:
                A_cp = collect_node_plate[:, 0:2]
                B_cp = np.sort(collect_node_plate[:, 2:4], axis=1)
                collect_node_plate = doublecheck2(np.hstack((A_cp, B_cp)))
            if collect_node_wall.shape[0] > 0:
                collect_node_wall = doublecheck2(collect_node_wall)

    # 板・壁要素が算定対象に含まれるのに応力が未入力の場合は明示エラー
    # (このまま進むと空配列への find_index で argmin エラーになる)
    if (np.asarray(collect_node_plate).shape[0] > 0
            and np.size(plate_stress) == 0):
        raise ValueError(
            '算定対象に板要素 (面材壁) が含まれていますが plate_stress が'
            '未入力です。QRタブの plate_stress 欄に板要素の面内せん断応力'
            ' (4列 [要素, ケース, 節点, Fxy]) を指定してください。')
    if (np.asarray(collect_node_wall).shape[0] > 0
            and np.size(wall_stress) == 0):
        raise ValueError(
            '算定対象に壁要素 (WALL) が含まれていますが wall_stress が'
            '未入力です。QRタブの wall_stress 欄を指定してください。')

    # 変形値の階高に対する等価換算
    L_std = np.zeros(n_level)
    for i_height in range(1, n_level + 1):
        z1_point = z_point[
            np.asarray(case_height[i_height - 1], dtype=int).ravel() - 1]
        if i_height < n_level:
            L_std[i_height - 1] = (
                z_point[int(np.asarray(case_height[i_height]).ravel()[0]) - 1]
                - z1_point[0])
        else:
            pick_node = find_zero_index(collect_node[:, 0] - i_height)
            L_V = 0.0
            for j in range(pick_node.size):
                nodei_index = find_index(node[:, 0],
                                         collect_node[pick_node[j], 2])
                nodej_index = find_index(node[:, 0],
                                         collect_node[pick_node[j], 3])
                L_V = L_V + abs(node[nodei_index, 3] - node[nodej_index, 3])
            if pick_node.size == 0:
                # NOTE: 原典は直前ループの j が残り不定動作
                raise RuntimeError(
                    '原典stop相当: 最上層に算定対象部材がありません (L_std)')
            L_std[i_height - 1] = L_V / pick_node.size

    # 列をフル幅に拡張
    base = collect_node
    collect_node = np.zeros((base.shape[0], ncols))
    collect_node[:, :4] = base

    # 各鉛直部材ごとに変形，剛性，および軸力を計算
    for i in range(collect_node.shape[0]):
        erow = find_index(element[:, 0], collect_node[i, 1])
        nodei_index = find_index(node[:, 0], element[erow, 3])
        nodej_index = find_index(node[:, 0], element[erow, 4])

        # まずは部材の鉛直を検討する
        XAXIS = node[nodej_index, 1:4] - node[nodei_index, 1:4]
        XAXIS = XAXIS / np.linalg.norm(XAXIS)

        L = np.linalg.norm(node[nodei_index, 1:4] - node[nodej_index, 1:4])
        L_H = np.linalg.norm(node[nodei_index, 1:3] - node[nodej_index, 1:3])
        L_X = np.linalg.norm(node[nodei_index, 1] - node[nodej_index, 1])
        L_Y = np.linalg.norm(node[nodei_index, 2] - node[nodej_index, 2])
        L_V = np.linalg.norm(node[nodei_index, 3] - node[nodej_index, 3])

        for j0, dc in enumerate(delta_case):  # 層間変形角
            if find_index(beam_stress[:, 0], collect_node[i, 1]) == -1:
                # 層間変形角の算定対象は梁要素とする
                collect_node[i, 4 + j0] = 0
            else:
                get_defo_id = (np.array(
                    [find_index(deformation[:, 0], collect_node[i, 2]),
                     find_index(deformation[:, 0], collect_node[i, 3])])
                    + lci_defo[dc - 1])
                delta = np.linalg.norm(deformation[get_defo_id[0], 2:4]
                                       - deformation[get_defo_id[1], 2:4])
                dL = _matlab_round(L / delta)
                collect_node[i, 4 + j0] = dL

        for jj0, nc in enumerate(N_case):  # 重心算定
            if find_index(beam_stress[:, 0], collect_node[i, 1]) == -1:
                # 重心算定の対象は梁要素とする
                collect_node[i, 4 + jd + jj0] = 0
            else:
                nodes_of_ele = element[erow, 3:5]
                # NOTE: find_index が -1 (未検出) の場合も MATLAB の
                #   0 (未検出) と同じ行にずれる (両者一致; 0-based恒等)
                get_N_id = (find_index(beam_stress[:, 0], collect_node[i, 1])
                            + lci_beam[nc - 1]
                            + 2 * find_index(nodes_of_ele, collect_node[i, 2]))
                N = beam_stress[get_N_id, 2] * L_V / L
                collect_node[i, 4 + jd + jj0] = N

        for jjj0, kc in enumerate(KX_case):  # X方向剛性計算
            get_defo_id = (np.array(
                [find_index(deformation[:, 0], collect_node[i, 2]),
                 find_index(deformation[:, 0], collect_node[i, 3])])
                + lci_defo[kc - 1])
            # 変形量δxの取得
            delta_x = (deformation[get_defo_id[1], 2]
                       - deformation[get_defo_id[0], 2])
            delta_x = delta_x * L_std[int(collect_node[i, 0]) - 1] / L_V
            if find_index(beam_stress[:, 0], collect_node[i, 1]) == -1:
                if have_truss:
                    if find_index(truss_stress[:, 0],
                                  collect_node[i, 1]) == -1:
                        pass
                    else:  # トラス部材の剛性計算
                        # トラス部材の軸力取得
                        get_T_id = (find_index(truss_stress[:, 0],
                                               collect_node[i, 1])
                                    + lci_truss[kc - 1])
                        T_x = truss_stress[get_T_id, 2]
                        # トラス部材の方向計算（XY・右上がり右下がり）
                        T_x = L_X / L * T_x
                        lvl = int(collect_node[i, 0]) - 1
                        if ((node[nodej_index, 3] - node[nodei_index, 3])
                                * (node[nodej_index, 1]
                                   - node[nodei_index, 1])) >= 0:
                            collect_node[i, 4 + jd + jn + jjj0] = T_x / delta_x
                            collect_shear[lvl, 0] = collect_shear[lvl, 0] + T_x
                        else:
                            collect_node[i, 4 + jd + jn + jjj0] = (
                                T_x / delta_x * -1)
                            collect_shear[lvl, 0] = collect_shear[lvl, 0] - T_x
            else:  # 梁部材の剛性計算
                # せん断力取得
                nodes_of_ele = element[erow, 0:6]
                get_Q_id = (find_index(beam_stress[:, 0], collect_node[i, 1])
                            + lci_beam[kc - 1]
                            + 2 * find_index(nodes_of_ele[3:5],
                                             collect_node[i, 2]))
                Q = beam_stress[get_Q_id, 2:5]
                # 部材の方向計算
                if L_H < 0.005:  # 柱
                    ang = nodes_of_ele[5]
                    if (node[find_index(node[:, 0], nodes_of_ele[3]), 3]
                            < node[find_index(node[:, 0],
                                              nodes_of_ele[4]), 3]):  # 上向き柱
                        Q_x = (Q[2] * math.cos(math.radians(ang))
                               + Q[1] * math.cos(math.radians(ang - 90)))
                    else:
                        Q_x = (Q[2] * math.cos(math.radians(-1 * ang))
                               + Q[1] * math.cos(math.radians(-1 * ang + 90)))
                        Q_x = Q_x * -1
                else:  # 斜め柱
                    Q_x = Q[0]
                    Q_y = Q[1]
                    Q_z = Q[2]
                    YAXIS, ZAXIS = _skew_yz_axes(XAXIS, nodes_of_ele[5])
                    Q_x = (L_X / L * Q_x).real
                    Q_y = (Q_y * YAXIS[0]).real
                    Q_z = (Q_z * ZAXIS[0]).real
                    if ((node[nodej_index, 3] - node[nodei_index, 3])
                            * (node[nodej_index, 1]
                               - node[nodei_index, 1])) >= 0:
                        pass
                    else:
                        Q_x = Q_x * -1
                    if node[nodej_index, 3] > node[nodei_index, 3]:
                        pass
                    else:
                        Q_y = Q_y * -1
                        Q_z = Q_z * -1
                    Q_x = Q_x + Q_y + Q_z

                lvl = int(collect_node[i, 0]) - 1
                if delta_x == 0:
                    print(collect_node[i, :])  # 原典454行: 裸の式表示
                    collect_node[i, 4 + jd + jn + jjj0] = 99999999999999
                else:
                    collect_node[i, 4 + jd + jn + jjj0] = Q_x / delta_x
                    collect_shear[lvl, 0] = collect_shear[lvl, 0] + Q_x

        for jjjj0, kc in enumerate(KY_case):  # Y方向剛性計算
            get_defo_id = (np.array(
                [find_index(deformation[:, 0], collect_node[i, 2]),
                 find_index(deformation[:, 0], collect_node[i, 3])])
                + lci_defo[kc - 1])
            # 変形量δyの取得
            delta_y = (deformation[get_defo_id[1], 3]
                       - deformation[get_defo_id[0], 3])
            delta_y = delta_y * L_std[int(collect_node[i, 0]) - 1] / L_V
            if find_index(beam_stress[:, 0], collect_node[i, 1]) == -1:
                if have_truss:
                    if find_index(truss_stress[:, 0],
                                  collect_node[i, 1]) == -1:
                        pass
                    else:  # トラス部材の剛性計算
                        get_T_id = (find_index(truss_stress[:, 0],
                                               collect_node[i, 1])
                                    + lci_truss[kc - 1])
                        T_y = truss_stress[get_T_id, 2]
                        # トラス部材の方向計算（XY・右上がり右下がり）
                        T_y = L_Y / L * T_y
                        lvl = int(collect_node[i, 0]) - 1
                        if ((node[nodej_index, 3] - node[nodei_index, 3])
                                * (node[nodej_index, 2]
                                   - node[nodei_index, 2])) >= 0:
                            collect_node[i, 4 + jd + jn + jx + jjjj0] = (
                                T_y / delta_y)
                            collect_shear[lvl, 1] = collect_shear[lvl, 1] + T_y
                        else:
                            collect_node[i, 4 + jd + jn + jx + jjjj0] = (
                                T_y / delta_y * -1)
                            collect_shear[lvl, 1] = collect_shear[lvl, 1] - T_y
            else:  # 梁部材の剛性計算
                # せん断力取得
                nodes_of_ele = element[erow, 0:6]
                get_Q_id = (find_index(beam_stress[:, 0], collect_node[i, 1])
                            + lci_beam[kc - 1]
                            + 2 * find_index(nodes_of_ele[3:5],
                                             collect_node[i, 2]))
                Q = beam_stress[get_Q_id, 2:5]
                # 部材の方向計算（XY・右上がり右下がり）
                if L_H < 0.005:  # 柱
                    ang = nodes_of_ele[5]
                    if (node[find_index(node[:, 0], nodes_of_ele[3]), 3]
                            < node[find_index(node[:, 0],
                                              nodes_of_ele[4]), 3]):  # 上向き柱
                        Q_y = (Q[2] * math.sin(math.radians(ang))
                               + Q[1] * math.sin(math.radians(ang - 90)))
                    else:
                        Q_y = (Q[2] * math.sin(math.radians(-1 * ang))
                               + Q[1] * math.sin(math.radians(-1 * ang + 90)))
                        Q_y = Q_y * -1
                else:  # 斜め柱（軸力のみで算定）
                    Q_x = Q[0]
                    Q_y = Q[1]
                    Q_z = Q[2]
                    YAXIS, ZAXIS = _skew_yz_axes(XAXIS, nodes_of_ele[5])
                    Q_x = (L_Y / L * Q_x).real
                    Q_y = (Q_y * YAXIS[1]).real
                    Q_z = (Q_z * ZAXIS[1]).real
                    if ((node[nodej_index, 3] - node[nodei_index, 3])
                            * (node[nodej_index, 2]
                               - node[nodei_index, 2])) >= 0:
                        pass
                    else:
                        Q_x = Q_x * -1
                    if node[nodej_index, 3] > node[nodei_index, 3]:
                        pass
                    else:
                        Q_y = Q_y * -1
                        Q_z = Q_z * -1
                    Q_y = Q_x + Q_y + Q_z

                lvl = int(collect_node[i, 0]) - 1
                if delta_y == 0:
                    print(collect_node[i, 2])  # 原典572-573行: 裸の式表示
                    print(collect_node[i, 3])
                    collect_node[i, 4 + jd + jn + jx + jjjj0] = 99999999999999
                else:
                    collect_node[i, 4 + jd + jn + jx + jjjj0] = Q_y / delta_y
                    collect_shear[lvl, 1] = collect_shear[lvl, 1] + Q_y

    # ------------------- 壁要素 -------------------
    collect_node_wall2 = np.zeros((collect_node_wall.shape[0], ncols))

    if collect_node_wall.shape[0] == 0:
        new_node = np.zeros((0, node.shape[1]))
    else:
        # 壁代表節点の作成(1)
        new_node = np.zeros((2 * collect_node_wall.shape[0], node.shape[1]))
        new_node[:, 0] = (np.arange(1, 2 * collect_node_wall.shape[0] + 1)
                          + np.max(node[:, 0]))

    # 各鉛直部材ごとに変形，剛性，および軸力を計算
    for i in range(collect_node_wall.shape[0]):
        # NOTE: find_index は最初の一致行 (同一壁IDが複数レベルにあると
        #   最初のレベルの形状が使われる; 原典どおり)
        wrow = find_index(wall_element[:, 1], collect_node_wall[i, 1])
        nodei1_index = find_index(node[:, 0], wall_element[wrow, 2])
        nodei2_index = find_index(node[:, 0], wall_element[wrow, 3])
        nodej1_index = find_index(node[:, 0], wall_element[wrow, 5])
        nodej2_index = find_index(node[:, 0], wall_element[wrow, 4])

        # 方向ベクトルなど
        XAXIS = node[nodej1_index, 1:4] - node[nodei1_index, 1:4]
        XAXIS = XAXIS / np.linalg.norm(XAXIS)
        L = np.linalg.norm(node[nodei1_index, 1:4] - node[nodej1_index, 1:4])
        L_H = np.linalg.norm(node[nodei1_index, 1:3]
                             - node[nodej1_index, 1:3])
        L_X = np.linalg.norm(node[nodei1_index, 1] - node[nodej1_index, 1])
        L_Y = np.linalg.norm(node[nodei1_index, 2] - node[nodej1_index, 2])
        L_V = np.linalg.norm(node[nodei1_index, 3] - node[nodej1_index, 3])

        # 壁代表節点の作成(2)
        new_node[2 * i, 1:4] = (node[nodei1_index, 1:4]
                                + node[nodei2_index, 1:4]) / 2
        new_node[2 * i + 1, 1:4] = (node[nodej1_index, 1:4]
                                    + node[nodej2_index, 1:4]) / 2

        collect_node_wall2[i, 0:2] = collect_node_wall[i, 0:2]
        collect_node_wall2[i, 2] = new_node[2 * i, 0]
        collect_node_wall2[i, 3] = new_node[2 * i + 1, 0]

        lvl = int(collect_node_wall[i, 0]) - 1

        for j0, dc in enumerate(delta_case):  # 層間変形角
            gd1 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 2]),
                 find_index(deformation[:, 0], collect_node_wall[i, 5])])
                + lci_defo[dc - 1])
            gd2 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 3]),
                 find_index(deformation[:, 0], collect_node_wall[i, 4])])
                + lci_defo[dc - 1])
            delta = (np.linalg.norm(deformation[gd1[0], 2:4]
                                    - deformation[gd1[1], 2:4])
                     + np.linalg.norm(deformation[gd2[0], 2:4]
                                      - deformation[gd2[1], 2:4])) / 2
            dL = _matlab_round(L / delta)
            collect_node_wall2[i, 4 + j0] = dL

        for jj0, nc in enumerate(N_case):  # 重心算定
            fi_ws = find_index(wall_stress_ID[:, 1], collect_node_wall[i, 1])
            get_N_id = 2 * fi_ws + 2 * lci_wall[nc - 1]
            N = wall_stress[get_N_id + 1, 0] * L_V / L
            collect_node_wall2[i, 4 + jd + jj0] = N

        for jjj0, kc in enumerate(KX_case):  # X方向剛性計算
            # 変形量δxの取得
            gd1 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 2]),
                 find_index(deformation[:, 0], collect_node_wall[i, 5])])
                + lci_defo[kc - 1])
            gd2 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 3]),
                 find_index(deformation[:, 0], collect_node_wall[i, 4])])
                + lci_defo[kc - 1])
            delta_x = (abs(deformation[gd1[0], 2] - deformation[gd1[1], 2])
                       + abs(deformation[gd2[0], 2]
                             - deformation[gd2[1], 2])) / 2
            delta_x = delta_x * L_std[lvl] / L_V

            # せん断力取得
            fi_ws = find_index(wall_stress_ID[:, 1], collect_node_wall[i, 1])
            get_Q_id = 2 * fi_ws + 2 * lci_wall[kc - 1]
            Q_x = _wall_shear_pick(
                wall_stress_ID[fi_ws + lci_wall[kc - 1], :],
                wall_stress[get_Q_id:get_Q_id + 2, :], wall_element, node, 2,
                np.asarray(load_case_no_wall).ravel()[kc - 1])
            # 部材の方向計算
            if delta_x == 0:
                print(collect_node_wall[i, :])  # 原典654行: 裸の式表示
                collect_node_wall2[i, 4 + jd + jn + jjj0] = 99999999999999
            else:
                collect_node_wall2[i, 4 + jd + jn + jjj0] = Q_x / delta_x
                collect_shear[lvl, 0] = collect_shear[lvl, 0] + Q_x

        for jjjj0, kc in enumerate(KY_case):  # Y方向剛性計算
            # 変形量δyの取得
            gd1 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 2]),
                 find_index(deformation[:, 0], collect_node_wall[i, 5])])
                + lci_defo[kc - 1])
            gd2 = (np.array(
                [find_index(deformation[:, 0], collect_node_wall[i, 3]),
                 find_index(deformation[:, 0], collect_node_wall[i, 4])])
                + lci_defo[kc - 1])
            delta_y = (abs(deformation[gd1[0], 3] - deformation[gd1[1], 3])
                       + abs(deformation[gd2[0], 3]
                             - deformation[gd2[1], 3])) / 2
            delta_y = delta_y * L_std[lvl] / L_V

            # せん断力取得
            fi_ws = find_index(wall_stress_ID[:, 1], collect_node_wall[i, 1])
            get_Q_id = 2 * fi_ws + 2 * lci_wall[kc - 1]
            Q_y = _wall_shear_pick(
                wall_stress_ID[fi_ws + lci_wall[kc - 1], :],
                wall_stress[get_Q_id:get_Q_id + 2, :], wall_element, node, 3,
                np.asarray(load_case_no_wall).ravel()[kc - 1])
            # 部材の方向計算（XY・右上がり右下がり）
            if delta_y == 0:
                print(collect_node_wall[i, 2])  # 原典683-684行: 裸の式表示
                print(collect_node_wall[i, 3])
                collect_node_wall2[i, 4 + jd + jn + jx + jjjj0] = (
                    99999999999999)
            else:
                collect_node_wall2[i, 4 + jd + jn + jx + jjjj0] = (
                    Q_y / delta_y)
                collect_shear[lvl, 1] = collect_shear[lvl, 1] + Q_y

    node = np.vstack((node, new_node))

    # ------------------- 板要素 -------------------
    collect_node_plate2 = np.zeros((collect_node_plate.shape[0], ncols))
    if collect_node_plate.shape[0] == 0:
        new_node = np.zeros((0, node.shape[1]))
    else:
        # 壁代表節点の作成(1)
        new_node = np.zeros((2 * collect_node_plate.shape[0], node.shape[1]))
        new_node[:, 0] = (np.arange(1, 2 * collect_node_plate.shape[0] + 1)
                          + np.max(node[:, 0]))

    for i in range(collect_node_plate.shape[0]):
        nodei1_index = find_index(node[:, 0], collect_node_plate[i, 2])
        nodei2_index = find_index(node[:, 0], collect_node_plate[i, 3])

        prow = find_index(plate[:, 0], collect_node_plate[i, 1])
        p4 = plate[prow, 3:7]
        topj_index = np.where(
            (p4 - collect_node_plate[i, 2])
            * (p4 - collect_node_plate[i, 3]))[0] + 3

        nodej1_index = find_index(node[:, 0], plate[prow, topj_index[0]])
        nodej2_index = find_index(node[:, 0], plate[prow, topj_index[1]])

        # 方向ベクトルなど
        XAXIS = node[nodej1_index, 1:4] - node[nodei1_index, 1:4]
        XAXIS = XAXIS / np.linalg.norm(XAXIS)
        L = np.linalg.norm(node[nodei1_index, 1:4] - node[nodej1_index, 1:4])
        L_H = np.linalg.norm(node[nodei1_index, 1:3]
                             - node[nodei2_index, 1:3])
        L_X = np.linalg.norm(node[nodei1_index, 1] - node[nodei2_index, 1])
        L_Y = np.linalg.norm(node[nodei1_index, 2] - node[nodei2_index, 2])
        L_V = np.linalg.norm(node[nodei1_index, 3] - node[nodej1_index, 3])

        # 壁代表節点の作成(2)
        new_node[2 * i, 1:4] = (node[nodei1_index, 1:4]
                                + node[nodei2_index, 1:4]) / 2
        new_node[2 * i + 1, 1:4] = (node[nodej1_index, 1:4]
                                    + node[nodej2_index, 1:4]) / 2

        collect_node_plate2[i, 0:2] = collect_node_plate[i, 0:2]
        collect_node_plate2[i, 2] = new_node[2 * i, 0]
        collect_node_plate2[i, 3] = new_node[2 * i + 1, 0]

        lvl = int(collect_node_plate[i, 0]) - 1

        for j0, dc in enumerate(delta_case):  # 層間変形角
            gdi = (np.array(
                [find_index(deformation[:, 0], node[nodei1_index, 0]),
                 find_index(deformation[:, 0], node[nodei2_index, 0])])
                + lci_defo[dc - 1])
            gdj = (np.array(
                [find_index(deformation[:, 0], node[nodej1_index, 0]),
                 find_index(deformation[:, 0], node[nodej2_index, 0])])
                + lci_defo[dc - 1])
            delta = np.linalg.norm(
                deformation[gdi[0], 2:4] / 2 + deformation[gdi[1], 2:4] / 2
                - deformation[gdj[0], 2:4] / 2
                - deformation[gdj[1], 2:4] / 2)
            dL = _matlab_round(L / delta)
            collect_node_plate2[i, 4 + j0] = dL

        for jj0, nc in enumerate(N_case):  # 重心算定
            collect_node_plate2[i, 4 + jd + jj0] = 0

        for jjj0, kc in enumerate(KX_case):  # X方向剛性計算
            gdi = (np.array(
                [find_index(deformation[:, 0], node[nodei1_index, 0]),
                 find_index(deformation[:, 0], node[nodei2_index, 0])])
                + lci_defo[kc - 1])
            gdj = (np.array(
                [find_index(deformation[:, 0], node[nodej1_index, 0]),
                 find_index(deformation[:, 0], node[nodej2_index, 0])])
                + lci_defo[kc - 1])
            # 変形量δxの取得
            delta_x = abs(
                deformation[gdi[0], 2] / 2 + deformation[gdi[1], 2] / 2
                - deformation[gdj[0], 2] / 2 - deformation[gdj[1], 2] / 2)
            delta_x = delta_x * L_std[lvl] / L_V

            # せん断力取得
            Q_x = (abs(plate_stress[
                find_index(plate_stress[:, 0], collect_node_plate[i, 1])
                + lci_plate[kc - 1], 3] * L_H) * (L_X / L_H))
            # 部材の方向計算
            if delta_x == 0:
                print(collect_node_plate[i, :])  # 原典767行: 裸の式表示
                collect_node_plate2[i, 4 + jd + jn + jjj0] = 99999999999999
            else:
                collect_node_plate2[i, 4 + jd + jn + jjj0] = Q_x / delta_x
                collect_shear[lvl, 0] = collect_shear[lvl, 0] + Q_x

        for jjjj0, kc in enumerate(KY_case):  # Y方向剛性計算
            gdi = (np.array(
                [find_index(deformation[:, 0], node[nodei1_index, 0]),
                 find_index(deformation[:, 0], node[nodei2_index, 0])])
                + lci_defo[kc - 1])
            gdj = (np.array(
                [find_index(deformation[:, 0], node[nodej1_index, 0]),
                 find_index(deformation[:, 0], node[nodej2_index, 0])])
                + lci_defo[kc - 1])
            # 変形量δyの取得
            delta_y = abs(
                deformation[gdi[0], 3] / 2 + deformation[gdi[1], 3] / 2
                - deformation[gdj[0], 3] / 2 - deformation[gdj[1], 3] / 2)
            delta_y = delta_y * L_std[lvl] / L_V

            # せん断力取得
            Q_y = (abs(plate_stress[
                find_index(plate_stress[:, 0], collect_node_plate[i, 1])
                + lci_plate[kc - 1], 3] * L_H) * (L_Y / L_H))
            # 部材の方向計算（XY・右上がり右下がり）
            if delta_y == 0:
                print(collect_node_plate[i, 2])  # 原典791-792行: 裸の式表示
                print(collect_node_plate[i, 3])
                collect_node_plate2[i, 4 + jd + jn + jx + jjjj0] = (
                    99999999999999)
            else:
                collect_node_plate2[i, 4 + jd + jn + jx + jjjj0] = (
                    Q_y / delta_y)
                collect_shear[lvl, 1] = collect_shear[lvl, 1] + Q_y

    node = np.vstack((node, new_node))

    collect_node = np.vstack((collect_node, collect_node_wall2,
                              collect_node_plate2))

    print('偏心率算定じの集計せん断力（X/Y）')  # 原典805行 (原文ママ)
    print(collect_shear)
    return collect_node, node


# ---------------------------------------------------------------------------
# hensin/wg_center.m
# ---------------------------------------------------------------------------

def wg_center(i_level, element, collect_node, node, w_index, kx_index,
              ky_index):
    """wg_center.m: 指定層の重心・剛心座標を計算する.

    引数:
      i_level      : 層番号 (1-based; collect_node 1列目の値)
      element      : 原典どおり未使用 (シグネチャ維持)
      collect_node : figure_rekr の戻り値
      w_index/kx_index/ky_index : 軸力N・X剛性Kx・Y剛性Ky の列番号
        (MATLAB 1-based 列番号のまま受ける。呼び出し側の
         4+j+jj / 4+j+jj+jjj / 4+j+jj+jjj+jjjj (j=0, jj=jjj=jjjj=1固定なら
         5/6/7) をそのまま渡す)

    戻り値 center : ndarray(4) [重心X, 重心Y, 剛心X, 剛心Y]
      重心 = Σ(N*xy)/ΣN (下端節点位置による加重平均)
      剛心X = Σ(Ky*x)/ΣKy, 剛心Y = Σ(Kx*y)/ΣKx (負剛性は0扱い)
    """
    node = np.asarray(node, dtype=float)
    collect_node = np.asarray(collect_node, dtype=float)
    w = int(w_index) - 1
    kx = int(kx_index) - 1
    ky = int(ky_index) - 1

    pick_id = find_zero_index(collect_node[:, 0] - i_level)
    cn = collect_node[pick_id, :].copy()
    w_center = np.zeros(2)
    g_center = np.zeros(2)
    for i in range(cn.shape[0]):
        xy = node[find_index(node[:, 0], cn[i, 2]), 1:3]
        w_center = w_center + cn[i, w] * xy * -1
        if cn[i, ky] > 0:
            XX = cn[i, ky]
        else:
            XX = 0.0
            cn[i, ky] = 0
        if cn[i, kx] > 0:
            YY = cn[i, kx]
        else:
            YY = 0.0
            cn[i, kx] = 0
        g_center = g_center + np.array([XX * xy[0], YY * xy[1]])

    w_center = w_center / np.sum(cn[:, w]) * -1
    g_center[0] = g_center[0] / np.sum(cn[:, ky])
    g_center[1] = g_center[1] / np.sum(cn[:, kx])

    return np.concatenate((w_center, g_center))


# ---------------------------------------------------------------------------
# hensin/kr_re.m
# ---------------------------------------------------------------------------

def kr_re(i_level, element, collect_node, node, w_index, kx_index, ky_index,
          center):
    """kr_re.m: 指定層のねじり剛性 kr と弾力半径 re を計算する.

    引数は wg_center と同じ (列番号は MATLAB 1-based)。
    center は wg_center の戻り値 [重心X, 重心Y, 剛心X, 剛心Y]。

    戻り値: (kr, re)
      kr : float  ねじり剛性 Σ(Ky*(x-gx)^2 + Kx*(y-gy)^2)  (負剛性は0扱い)
      re : ndarray(2) [X方向弾力半径 sqrt(kr/ΣKx), Y方向 sqrt(kr/ΣKy)]
    """
    node = np.asarray(node, dtype=float)
    collect_node = np.asarray(collect_node, dtype=float)
    center = np.asarray(center, dtype=float).ravel()
    w = int(w_index) - 1
    kx = int(kx_index) - 1
    ky = int(ky_index) - 1

    pick_id = find_zero_index(collect_node[:, 0] - i_level)
    cn = collect_node[pick_id, :].copy()
    kr = 0.0
    re = np.zeros(2)
    g_center = center[2:4]
    for i in range(cn.shape[0]):
        xy = node[find_index(node[:, 0], cn[i, 2]), 1:3]
        if cn[i, ky] > 0:
            pass
        else:
            cn[i, ky] = 0
        if cn[i, kx] > 0:
            pass
        else:
            cn[i, kx] = 0
        kr = (kr + cn[i, ky] * (xy[0] - g_center[0]) ** 2
              + cn[i, kx] * (xy[1] - g_center[1]) ** 2)

    re[0] = math.sqrt(kr / np.sum(cn[:, kx]))
    re[1] = math.sqrt(kr / np.sum(cn[:, ky]))
    return kr, re


# ---------------------------------------------------------------------------
# MIDAS_QRplot_fig.m 972-977行 (偏心率 Re)
# ---------------------------------------------------------------------------

def calc_REXY(center, re):
    """MIDAS_QRplot_fig.m 976-977行: 偏心率 Re = 偏心距離/弾力半径.

    引数:
      center : ndarray (n_level x 4) 各層の [重心X, 重心Y, 剛心X, 剛心Y]
      re     : ndarray (n_level x 2) 各層の弾力半径 [re_x, re_y]
    戻り値 REXY : ndarray (n_level x 2)
      REXY[:,0] = |重心Y-剛心Y| / re_x  (X方向加力時)
      REXY[:,1] = |重心X-剛心X| / re_y  (Y方向加力時)
    """
    center = np.atleast_2d(np.asarray(center, dtype=float))
    re = np.atleast_2d(np.asarray(re, dtype=float))
    REXY = np.zeros((center.shape[0], 2))
    REXY[:, 0] = np.abs(center[:, 1] - center[:, 3]) / re[:, 0]
    REXY[:, 1] = np.abs(center[:, 0] - center[:, 2]) / re[:, 1]
    return REXY


# ---------------------------------------------------------------------------
# MIDAS_QRplot_fig.m 1272-1311行 display_textable_REXY
# ---------------------------------------------------------------------------

def textable_REXY(case_height, kr, re, center, REXY):
    """display_textable_REXY: 偏心率一覧の TeX 表ソース行を組み立てる.

    引数:
      case_height : list[list[int]] (層数 = len(case_height);
                    原典の size(case_height,2)-1 の読替え)
      kr    : ndarray (n_level,) or (n_level x 1)  ねじり剛性
      re    : ndarray (n_level x 2)  弾力半径
      center: ndarray (n_level x 4)  [重心X, 重心Y, 剛心X, 剛心Y]
      REXY  : ndarray (n_level x 2)  偏心率 (calc_REXY の戻り値)

    Fe係数: FEXY = min(1.5, max(1.0, ((Re-0.15)*1.5+(0.3-Re)*1.0)/0.15))

    戻り値: list[str]  上層 → 1F の順の表行
      '<n>F & 重心X & 重心Y & 剛心X & 剛心Y & kr仮数 $\\times 10^{ 指数}$
       & re_x & re_y & Re_x & Re_y & Fe_x & Fe_y \\\\ \\hline'
      (1F 行のみ末尾 \\bottomrule)
    """
    kr = np.asarray(kr, dtype=float).ravel()
    re = np.atleast_2d(np.asarray(re, dtype=float))
    center = np.atleast_2d(np.asarray(center, dtype=float))
    REXY = np.atleast_2d(np.asarray(REXY, dtype=float))

    QR_TeX_txt = []
    n_level = len(case_height)  # 原典: size(case_height,2)-1
    for ii in range(n_level, 0, -1):
        # MATLAB: log10(0)=-Inf で続行する (Pythonのmath.log10は例外のため
        # np.log10 で -Inf を再現。剛性0の層は '-Inf ×10^' 表記になる)
        with np.errstate(divide='ignore'):
            _log_kr = float(np.log10(kr[ii - 1]))
        a = _n2s(np.floor(_log_kr), '%15.0f')
        b = _n2s(kr[ii - 1], '%15.2e')
        cut_index = b.find('e')
        if cut_index != -1:
            b = b[:cut_index]
        FEXY = np.minimum(1.5, np.maximum(
            1.0, ((REXY[ii - 1, :] - 0.15) * 1.5
                  + (0.3 - REXY[ii - 1, :]) * 1.0) / 0.15))
        label = '1F' if ii == 1 else _n2s(ii, '%15.0f') + 'F'
        tail = ' \\\\ \\bottomrule' if ii == 1 else ' \\\\ \\hline'
        QR_TeX_txt.append(
            label + ' & '
            + _n2s(center[ii - 1, 0], '%15.2f')
            + ' & ' + _n2s(center[ii - 1, 1], '%15.2f')
            + ' & ' + _n2s(center[ii - 1, 2], '%15.2f')
            + ' & ' + _n2s(center[ii - 1, 3], '%15.2f')
            + ' & ' + b + ' $\\times 10^{ ' + a + '}$'
            + ' & ' + _n2s(re[ii - 1, 0], '%15.1f')
            + ' & ' + _n2s(re[ii - 1, 1], '%15.1f')
            + ' & ' + _n2s(REXY[ii - 1, 0], '%15.3f')
            + ' & ' + _n2s(REXY[ii - 1, 1], '%15.3f')
            + ' & ' + _n2s(FEXY[0], '%15.3f')
            + ' & ' + _n2s(FEXY[1], '%15.3f')
            + tail)
    return QR_TeX_txt


# ---------------------------------------------------------------------------
# hensin/figure_w_center.m
# ---------------------------------------------------------------------------

def figure_w_center(stress_fontsize, element, mergin_LEFT, mergin_RIGHT,
                    mergin_TOP, mergin_BOTTOM, axisname_location,
                    line_location, dimension_fontsize, axis_fontsize,
                    title_fontsize, load_case_name, collect_node, node,
                    axis_xtick, axis_ytick, N_case, h_axis_plot, paper_orient,
                    paper_size, plot_no, center, axis_name, axis_element,
                    axis_node, axis_plot_coefi, limit_sec_no, out_dir='.'):
    """figure_w_center.m: 重心算定時軸力一覧の平面図をPDF保存する.

    引数:
      collect_node : figure_rekr の戻り値のうち当該レベルの行のみ
      N_case       : 重心算定ケースの 1-based index (load_case_name への添字)
      h_axis_plot  : 表示レベルの Z 座標列 (原典呼出しは z_point(case_height{ii}))
      plot_no      : プロットする列番号 (MATLAB 1-based; 呼び出し側の 4+j+jj)
      center       : 当該層の [重心X, 重心Y, 剛心X, 剛心Y]
      axis_name/axis_node/axis_plot_coefi : 通り芯符号 (plot_plan_axis2 用)。
        axis_element / axisname_location / dimension_fontsize /
        axis_fontsize は原典どおり未使用。
    保存名: '<ケース名>-<レベル>wcenter.pdf'
    # NOTE: 原典の size(center,3)>1 分岐は 3次元配列専用で本移植 (1層分の
    #   1次元 center) では到達しないため、単一分岐のみ移植。
    """
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    collect_node = np.atleast_2d(np.asarray(collect_node, dtype=float))
    center = np.asarray(center, dtype=float).ravel()
    h_axis_plot = np.atleast_1d(np.asarray(h_axis_plot, dtype=float)).ravel()
    N_case = int(np.atleast_1d(N_case)[0])
    p0 = int(plot_no) - 1

    fig, ax = _new_fig()
    stress_fontsize = stress_fontsize + 2
    title_fontsize = title_fontsize + 2

    # 通り芯情報を記入する
    _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name, axis_node,
                     axis_plot_coefi, mergin_LEFT, mergin_BOTTOM,
                     stress_fontsize)

    # レベルにある要素のプロット
    _plot_level_beams(ax, element, node, h_axis_plot, limit_sec_no)

    for j in range(collect_node.shape[0]):
        if collect_node[j, p0] == 0:
            pass
        else:
            node_index = find_index(node[:, 0], collect_node[j, 2])
            node_jndex = find_index(node[:, 0], collect_node[j, 3])
            xm = node[node_index, 1] * 0.5 + node[node_jndex, 1] * 0.5
            ym = node[node_index, 2] * 0.5 + node[node_jndex, 2] * 0.5
            ax.plot(xm, ym, 'rx', markersize=2)
            _qtext(ax, xm, ym, 'N:' + _n2s(collect_node[j, p0], '%15.1f'),
                   ha='left', va='top', fs=stress_fontsize, rot=45)

    # 重心位置など記載
    ax.plot(center[0], center[1], '*', markersize=10,
            markeredgecolor='r', markerfacecolor='r')
    _qtext(ax, np.min(axis_xtick) - mergin_LEFT + line_location + 0.5,
           np.max(axis_ytick) + mergin_TOP - 0.5,
           '重心位置＊(X：' + _n2s(center[0], '%15.1f') + 'm，　Y：'
           + _n2s(center[1], '%15.1f') + 'm)',
           ha='left', va='top', fs=title_fontsize)

    ax.set_title('重心算定時軸力一覧[　　荷重ケース：'
                 + str(load_case_name[N_case - 1]).rstrip() + '　]',
                 fontsize=title_fontsize)
    ax.set_xlabel('表示レベル：' + _n2s(h_axis_plot[0], '%15.2f') + 'm',
                  fontsize=title_fontsize)

    _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM)
    _set_paper(fig, paper_orient, paper_size)
    out_path = os.path.join(
        out_dir, str(load_case_name[N_case - 1]).rstrip() + '-'
        + _n2s(h_axis_plot[0], '%15.0f') + 'wcenter.pdf')
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# hensin/figure_g_center.m
# ---------------------------------------------------------------------------

def figure_g_center(stress_fontsize, element, mergin_LEFT, mergin_RIGHT,
                    mergin_TOP, mergin_BOTTOM, axisname_location,
                    line_location, dimension_fontsize, axis_fontsize,
                    title_fontsize, load_case_name, collect_node, node,
                    axis_xtick, axis_ytick, K_case, h_axis_plot, paper_orient,
                    paper_size, plot_no, center, kr, re, REXY, xy, axis_name,
                    axis_element, axis_node, axis_plot_coefi, limit_sec_no,
                    out_dir='.'):
    """figure_g_center.m: 剛心算定時剛性一覧の平面図をPDF保存する.

    引数:
      collect_node : figure_rekr の戻り値のうち当該レベルの行のみ
      K_case  : 剛心算定ケースの 1-based index (load_case_name への添字)
      plot_no : プロットする列番号 (MATLAB 1-based;
                X方向は 4+j+jj+jjj, Y方向は 4+j+jj+jjj+jjjj)
      center  : 当該層の [重心X, 重心Y, 剛心X, 剛心Y]
      kr      : 当該層のねじり剛性 (スカラ)
      re      : 当該層の弾力半径 [re_x, re_y]
      REXY    : 当該層の偏心率 [Re_x, Re_y]
      xy      : 1=X方向, 2=Y方向 (re/REXY への 1-based 添字)
    保存名: '<ケース名>-<レベル>gcenter.pdf'
    # NOTE: figure_w_center 同様、size(center,3)>1 分岐は移植対象外。
    """
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    collect_node = np.atleast_2d(np.asarray(collect_node, dtype=float))
    center = np.asarray(center, dtype=float).ravel()
    re = np.asarray(re, dtype=float).ravel()
    REXY = np.asarray(REXY, dtype=float).ravel()
    kr = float(np.asarray(kr, dtype=float).ravel()[0])
    h_axis_plot = np.atleast_1d(np.asarray(h_axis_plot, dtype=float)).ravel()
    K_case = int(np.atleast_1d(K_case)[0])
    p0 = int(plot_no) - 1
    xy = int(xy)

    fig, ax = _new_fig()
    stress_fontsize = stress_fontsize + 2
    title_fontsize = title_fontsize + 2

    for j in range(collect_node.shape[0]):
        if collect_node[j, p0] > 0:
            color_p = 'black'
        else:
            color_p = 'red'
        if collect_node[j, p0] == 0:
            continue

        nodei_index = find_index(node[:, 0], collect_node[j, 2])
        nodej_index = find_index(node[:, 0], collect_node[j, 3])
        label = 'Q/δ:' + _n2s(collect_node[j, p0], '%15.0f')
        xi, yi = node[nodei_index, 1], node[nodei_index, 2]
        xj, yj = node[nodej_index, 1], node[nodej_index, 2]

        if xi == xj and yi == yj:  # 直柱
            ax.plot(xi, yi, 'rx', markersize=5)
            _qtext(ax, xi, yi, label, ha='center', va='top',
                   fs=stress_fontsize, rot=45, color=color_p)
        else:  # ななめ材
            ax.plot([xi, xj], [yi, yj], 'r-')
            if xi < xj or yi < yj:
                va = 'top'
            else:
                va = 'bottom'
            if xi == xj:
                rot = 0
            elif yi == yj:
                rot = 90
            else:
                rot = 45
            _qtext(ax, xi * 0.5 + xj * 0.5, yi * 0.5 + yj * 0.5, label,
                   ha='right', va=va, fs=stress_fontsize, rot=rot,
                   color=color_p)
            ax.plot(xi, yi, 'rx', markersize=2)
            ax.plot(xj, yj, 'rx', markersize=2)

    # 重心位置など記載
    ax.plot(center[0], center[1], '*', markersize=10,
            markeredgecolor='r', markerfacecolor='r')
    ax.plot(center[2], center[3], '+', markersize=10,
            markeredgecolor='b', markerfacecolor='b')
    _qtext(ax, np.min(axis_xtick) - mergin_LEFT + line_location + 0.5,
           np.max(axis_ytick) + mergin_TOP - line_location,
           '重心位置＊(X：' + _n2s(center[0], '%15.1f') + 'm，　Y：'
           + _n2s(center[1], '%15.1f') + 'm)\n'
           + '剛心位置＋(X：' + _n2s(center[2], '%15.1f') + 'm，　Y：'
           + _n2s(center[3], '%15.1f') + 'm)',
           ha='left', va='top', fs=title_fontsize)

    ax.set_title('剛心算定時剛性一覧[　　荷重ケース：'
                 + str(load_case_name[K_case - 1]).rstrip() + '　]　　偏心率：'
                 + _n2s(REXY[xy - 1], '%16.2f'), fontsize=title_fontsize)
    ax.set_xlabel('表示レベル：' + _n2s(h_axis_plot[0], '%15.2f')
                  + 'm　ねじり剛性(kNm)：' + _n2s(kr, '%2.2e')
                  + '　弾力半径(m)：' + _n2s(re[xy - 1], '%15.2f'),
                  fontsize=title_fontsize)

    # 通り芯情報を記入する
    _plot_plan_axis2(ax, axis_xtick, axis_ytick, node, axis_name, axis_node,
                     axis_plot_coefi, mergin_LEFT, mergin_BOTTOM,
                     stress_fontsize)

    # レベルにある要素のプロット
    _plot_level_beams(ax, element, node, h_axis_plot, limit_sec_no)

    _finish_axes(ax, axis_xtick, axis_ytick,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM)
    _set_paper(fig, paper_orient, paper_size)
    out_path = os.path.join(
        out_dir, str(load_case_name[K_case - 1]).rstrip() + '-'
        + _n2s(h_axis_plot[0], '%15.0f') + 'gcenter.pdf')
    fig.savefig(out_path, format='pdf')
    plt.close(fig)
    return out_path
