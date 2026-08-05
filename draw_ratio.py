# -*- coding: utf-8 -*-
"""検定比図 (構面別PDF) の作成.

MIDASratioplot_fig.m 1565-1810行 (鉛直構面の検定比図ループ) +
figure_ratio_axis.m + ratio/ratio_plot/ の逐語移植:
  column_maxratio_plot.m / beam_maxratio_plot.m / truss_ratio_plot.m /
  unit_ratio_plot.m

構面スキャフォールド (通り芯近似・寸法線・交差通り芯符号・用紙設定) は
figure_stress_axis.m と同一文のため draw_stress.py / draw_model.py の
検証済みヘルパーを共用する。

未対応 (MATLAB版の対応状況に合わせる):
  - 水平構面(伏図)の検定比図 (figure_ratio_floor.m) は応力図と同様に対象外
  - 壁要素(wall_ratio)・板要素(plate_ratio)の検定比 (検定エンジン未移植)
  - select13(位置別表示)は原典に beam_ratio_plot.m / column_ratio_plot.m が
    存在しない(呼ぶとエラー)ため最大値表示のみ
"""

import os
import shutil
import tempfile
import sys
import math

import numpy as np
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    from .mgt import (
        mgtopen_node, mgtopen_element, mgtopen_group, node_index_get,
    )
    from .util import find_index, doublecheck, space_erace
    from .draw_model import (
        _setup_japanese_font, _finish_figure, _text, _kline,
        _project_nodes, _tick, _axis_coefficients, _unit_elements,
        _resolve_select, _safe_name, column_beam_judge_one, line_projection,
        _save_fig_pdf_or_pgf, _write_pgf_bundle,
    )
    from .draw_stress import _load_case_split, _unit_add_ysub
except ImportError:  # スクリプト実行時
    from mgt import (
        mgtopen_node, mgtopen_element, mgtopen_group, node_index_get,
    )
    from util import find_index, doublecheck, space_erace
    from draw_model import (
        _setup_japanese_font, _finish_figure, _text, _kline,
        _project_nodes, _tick, _axis_coefficients, _unit_elements,
        _resolve_select, _safe_name, column_beam_judge_one, line_projection,
        _save_fig_pdf_or_pgf, _write_pgf_bundle,
    )
    from draw_stress import _load_case_split, _unit_add_ysub


NOTE_GAP = 0.05  # 曲げ/せん断注記の離隔 [m] (片側。合計で約0.1m相当)


def _f2(v):
    """MATLAB num2str(x,'%15.2f') 相当."""
    return ('%15.2f' % float(v)).strip()


def _case_split_blocks(case_col):
    """検定ケースの区切り検出 (出現順維持; result.LCNAME と同順).

    合成ケース番号 (長期*10*i_pm+水平) は出現順が昇順とは限らないため、
    昇順ソートする _load_case_split を使うと LCNAME (ケース名) との
    対応がずれる。doublecheck_nonsort と同じ「連続重複のみ削除」で
    ブロック先頭行を検出する。
    """
    case_col = np.asarray(case_col, dtype=float).ravel()
    if case_col.size == 0:
        return case_col, np.array([], dtype=int)
    keep = np.concatenate(([True], np.diff(case_col) != 0))
    return case_col[keep], np.where(keep)[0]


# ---------------------------------------------------------------------------
# ratio_plot/column_maxratio_plot.m
# ---------------------------------------------------------------------------

def _column_maxratio_plot(ax, ratio, ni, nj, nodeM, axis_cols, angle, fs):
    """柱要素の最大検定比の注記 (column_maxratio_plot.m)."""
    ratio = np.round(100.0 * np.atleast_2d(np.asarray(ratio, dtype=float))) / 100.0

    # NOTE: 原典7-17行のβ角正規化は以降で未使用のため省略 (結果に影響なし)

    ax.plot([nodeM[ni, 1]], [nodeM[ni, axis_cols[1]]], 'kx', markersize=2)
    ax.plot([nodeM[nj, 1]], [nodeM[nj, axis_cols[1]]], 'kx', markersize=2)

    if nodeM[ni, axis_cols[1]] == nodeM[nj, axis_cols[1]]:
        print('ERROR = 柱部材の定義ミス？')  # 原典: セミコロン無し代入のecho

    # 回転角
    if (nodeM[ni, 1] - nodeM[nj, 1]) == 0:
        rotd = 0.0
    else:
        rotd = math.degrees(math.atan(
            (nodeM[ni, axis_cols[1]] - nodeM[nj, axis_cols[1]])
            / (nodeM[ni, 1] - nodeM[nj, 1])))
        if rotd > 0:
            rotd = rotd - 90
        else:
            rotd = rotd + 90

    xm = nodeM[ni, 1] * 0.5 + nodeM[nj, 1] * 0.5
    ym = nodeM[ni, axis_cols[1]] * 0.5 + nodeM[nj, axis_cols[1]] * 0.5

    # 曲げ値とせん断値が近すぎるため、文字の向きに沿って少し離す
    # (原典から意図的調整 2026-08-03)
    _r = math.radians(rotd)
    _d = NOTE_GAP
    _ux, _uy = math.cos(_r) * _d, math.sin(_r) * _d
    _vx, _vy = -math.sin(_r) * _d, math.cos(_r) * _d

    # 曲げ(NM)の最大検定比 (列ごとの最大→全体最大の行列位置)
    sub = np.abs(ratio[0:3, 0:2])
    ci = np.argmax(sub, axis=0)
    c2 = int(np.argmax(sub[ci, np.arange(2)]))
    r1 = int(ci[c2])
    _text(ax, xm - _ux - _vx, ym - _uy - _vy, _f2(ratio[r1, c2]),
          ha='right', va='top', fs=fs, rot=rotd)

    # せん断の最大検定比
    sub = np.abs(ratio[0:3, 2:4])
    ci = np.argmax(sub, axis=0)
    c2 = int(np.argmax(sub[ci, np.arange(2)]))
    r1 = int(ci[c2])
    _text(ax, xm + _ux + _vx, ym + _uy + _vy, _f2(ratio[r1, c2 + 2]),
          ha='left', va='bottom', fs=fs, rot=rotd)


# ---------------------------------------------------------------------------
# ratio_plot/beam_maxratio_plot.m
# ---------------------------------------------------------------------------

def _beam_maxratio_plot(ax, ratio, ni, nj, nodeM, axis_cols, angle, fs):
    """梁要素の最大検定比の注記 (beam_maxratio_plot.m)."""
    ratio = np.round(100.0 * np.atleast_2d(np.asarray(ratio, dtype=float))) / 100.0

    # 回転角
    if (nodeM[ni, axis_cols[0]] - nodeM[nj, axis_cols[0]]) == 0:
        rotd = 90.0
    else:
        rotd = math.degrees(math.atan(
            (nodeM[ni, axis_cols[1]] - nodeM[nj, axis_cols[1]])
            / (nodeM[ni, axis_cols[0]] - nodeM[nj, axis_cols[0]])))

    ax.plot([nodeM[ni, axis_cols[0]]], [nodeM[ni, axis_cols[1]]],
            'kx', markersize=2)
    ax.plot([nodeM[nj, axis_cols[0]]], [nodeM[nj, axis_cols[1]]],
            'kx', markersize=2)

    xm = nodeM[ni, axis_cols[0]] * 0.5 + nodeM[nj, axis_cols[0]] * 0.5
    ym = nodeM[ni, axis_cols[1]] * 0.5 + nodeM[nj, axis_cols[1]] * 0.5

    # 曲げ値(上)とせん断値(下)が近すぎるため、文字の上下方向に少し離す
    # (原典から意図的調整 2026-08-03)
    _r = math.radians(rotd)
    _vx = -math.sin(_r) * NOTE_GAP
    _vy = math.cos(_r) * NOTE_GAP

    # 曲げに対する検定比
    mi = int(np.argmax(np.abs(ratio[0:3, 0])))
    _text(ax, xm + _vx, ym + _vy, _f2(ratio[mi, 0]),
          ha='center', va='baseline', fs=fs, rot=rotd)

    # せん断に対する検定比
    sub = np.abs(ratio[0:3, 1:4])
    ci = np.argmax(sub, axis=0)
    c2 = int(np.argmax(sub[ci, np.arange(3)]))
    r1 = int(ci[c2])
    _text(ax, xm - _vx, ym - _vy, _f2(ratio[r1, c2 + 1]),
          ha='center', va='top', fs=fs, rot=rotd)


# ---------------------------------------------------------------------------
# ratio_plot/truss_ratio_plot.m
# ---------------------------------------------------------------------------

def _truss_ratio_plot(ax, ratio, ni, nj, nodeM, fs):
    """トラス要素の検定比の注記 (truss_ratio_plot.m。axis=2列目=構面内座標)."""
    ratio = np.round(100.0 * np.asarray(ratio, dtype=float).ravel()) / 100.0
    val = _f2(ratio[1])  # MATLAB ratio(1,2)

    XY = math.sqrt((nodeM[ni, 1] - nodeM[nj, 1]) ** 2
                   + (nodeM[ni, 2] - nodeM[nj, 2]) ** 2)
    Z = abs(nodeM[ni, 3] - nodeM[nj, 3])

    # 回転角
    if (nodeM[ni, 1] - nodeM[nj, 1]) == 0:
        rotd = 90.0
    else:
        rotd = math.degrees(math.atan(
            (nodeM[ni, 3] - nodeM[nj, 3]) / (nodeM[ni, 1] - nodeM[nj, 1])))

    if nodeM[ni, 1] < nodeM[nj, 1]:
        x = nodeM[ni, 1] * 0.3 + nodeM[nj, 1] * 0.7
        y = nodeM[ni, 3] * 0.3 + nodeM[nj, 3] * 0.7
        if nodeM[ni, 3] < nodeM[nj, 3]:
            va = 'top' if XY > Z else 'bottom'
        else:
            va = 'bottom' if XY > Z else 'top'
    else:
        x = nodeM[nj, 1] * 0.3 + nodeM[ni, 1] * 0.7
        y = nodeM[nj, 3] * 0.3 + nodeM[ni, 3] * 0.7
        if nodeM[nj, 3] < nodeM[ni, 3]:
            va = 'bottom' if XY > Z else 'top'
        else:
            va = 'top' if XY > Z else 'bottom'
    _text(ax, x, y, val, ha='center', va=va, fs=fs, rot=rotd)


# ---------------------------------------------------------------------------
# ratio_plot/unit_ratio_plot.m
# ---------------------------------------------------------------------------

def _unit_ratio_plot(ax, unit_elems, unit_nodes, element, nodeM, ratio,
                     axis_cols, limit_sec_no, i_load, lci, fs, select13):
    """結合部材の検定比plot (unit_ratio_plot.m)."""
    unit_elems = np.atleast_1d(np.asarray(unit_elems, dtype=float)).ravel()
    unit_nodes = np.atleast_1d(np.asarray(unit_nodes, dtype=float)).ravel()

    # 検定比をplotしていいかの判別
    unit_ele_in = np.atleast_1d(find_index(element[:, 0], unit_elems))
    unit_sec = element[unit_ele_in, 2]
    flags = (unit_sec < limit_sec_no).astype(float)
    if np.sum(flags) == 0 and np.prod(flags) == 0:
        return  # 応力をプロットしない部材
    if not (np.sum(flags) > 0 and np.prod(flags) > 0):
        print('ERROR = 検定比plot要素と非表示要素が結合部材のの中に混在！！')
        return

    ai = int(find_index(element[:, 0], unit_elems[0]))
    c_b = (column_beam_judge_one(ai, element, nodeM), element[ai, 5])

    # 端部elementのindex (原典: 先頭ケースブロック内で探索し i_load 分ずらす)
    block_end = int(lci[1]) if len(lci) > 1 else ratio.shape[0]
    eleratio_index = np.atleast_1d(find_index(
        ratio[0:block_end, 0], unit_elems)) + int(lci[i_load])
    eleedge_index = unit_ele_in

    # 結合部材のｉ端
    if element[eleedge_index[0], 3] == unit_nodes[0]:
        elei = int(eleratio_index[0])
    elif element[eleedge_index[0], 4] == unit_nodes[0]:
        elei = int(eleratio_index[0]) + 2
    else:
        print('ERROR = 端点の定義ミス')
        return
    # 結合部材のｊ端
    if element[eleedge_index[-1], 3] == unit_nodes[-1]:
        elej = int(eleratio_index[-1])
    elif element[eleedge_index[-1], 4] == unit_nodes[-1]:
        elej = int(eleratio_index[-1]) + 2
    else:
        print('ERROR = 端点の定義ミス')
        return

    # 結合部材の最大検定比 (全構成要素のi端/中央/j端)
    rows = np.concatenate([eleratio_index, eleratio_index + 1,
                           eleratio_index + 2])
    max_ratios = ratio[rows, :]
    MV = np.max(max_ratios[:, 2])
    QV1 = np.max(max_ratios[:, 3])
    QV2 = np.max(max_ratios[:, 4])
    MQ = np.max(max_ratios[:, 5])
    max_ratio = np.array([MV, QV1, QV2, MQ])

    # NOTE: 原典57-74行の中央部index(elecenter_index)は算出のみで未使用

    plot_ratio_max = np.vstack([ratio[elei, 2:6], max_ratio,
                                ratio[elej, 2:6]])

    ni = int(find_index(nodeM[:, 0], unit_nodes[0]))
    nj = int(find_index(nodeM[:, 0], unit_nodes[-1]))

    if select13[0] != 0:
        raise ValueError('検定比図の位置別表示(select13)は原典に対応関数が'
                         '無いため未対応です (最大値表示のみ)')
    if c_b[0] in (2.0, 1.9):
        _column_maxratio_plot(ax, plot_ratio_max, ni, nj, nodeM,
                              axis_cols, c_b, fs)
    elif c_b[0] == 1.0:
        _beam_maxratio_plot(ax, plot_ratio_max, ni, nj, nodeM,
                            axis_cols, c_b, fs)
    else:
        print('ERROR = 柱梁タイプが定義されていません')


# ---------------------------------------------------------------------------
# figure_ratio_axis.m (1構面・1検定ケース)
# ---------------------------------------------------------------------------

def _figure_ratio_axis(C, i_axis, i_load, case_no, case_name, out_path):
    """1構面・1検定ケースの検定比図を描きPDF保存する."""
    node = C['node']
    element = C['element']
    beam_ratio = C['beam_ratio']
    truss_ratio = C['truss_ratio']
    lci_beam = C['lci_beam']
    lci_truss = C['lci_truss']
    unit_element = C['unit_element']
    uniele_ID = C['uniele_ID']
    select13 = C['select13']
    select_unit = C['select_unit']
    limit_sec_no = C['limit_sec_no']
    ratio_fs, dim_fs, axis_fs, title_fs = C['fontsize']
    mL, mR, mT, mB = C['mergins']

    direction = C['axis_direction'][i_axis]
    axis_elems = C['axis_element_sel'][i_axis]
    axis_nodes = C['axis_node_sel'][i_axis]

    nodeM = _project_nodes(node, direction)

    on_idx_nodes = np.atleast_1d(find_index(node[:, 0], axis_nodes))
    on_idx_nodes = on_idx_nodes[on_idx_nodes >= 0]
    pl = node[on_idx_nodes, 1:4]
    axis_xtick, axis_ytick, axis_ztick = pl[:, 0], pl[:, 1], pl[:, 2]
    zmin = float(np.min(axis_ztick))

    fig = plt.figure()
    ax = fig.add_subplot(111)

    # %%%%%% 要素の種別判定 (検定比データ内の存在で判定) %%%%%%
    n_el = axis_elems.size
    etype = np.zeros(n_el, dtype=int)

    def _mark(table, code):
        if np.size(table) == 0 or n_el == 0:
            return np.array([], dtype=int)
        idx = np.atleast_1d(find_index(table[:, 0], axis_elems))
        pos = np.where(idx >= 0)[0]
        etype[pos] = code
        return pos

    on_axis_truss_index = _mark(truss_ratio, 3)
    on_axis_beam_index = _mark(beam_ratio, 1)

    ratio_row = np.full(n_el, -1, dtype=int)
    on_axis_element_index = np.full(n_el, -1, dtype=int)
    if np.size(beam_ratio) > 0:
        if on_axis_beam_index.size:
            ratio_row[on_axis_beam_index] = np.atleast_1d(find_index(
                beam_ratio[:, 0], axis_elems[on_axis_beam_index]))
        if on_axis_truss_index.size and np.size(truss_ratio) > 0:
            ratio_row[on_axis_truss_index] = np.atleast_1d(find_index(
                truss_ratio[:, 0], axis_elems[on_axis_truss_index]))
        on_axis_element_index = np.atleast_1d(
            find_index(element[:, 0], axis_elems))

    axis_ysubtick = []

    # %%%%%% 通り上にある単一要素の検定値plot %%%%%%
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
            srow = ratio_row[i_e]
            plot_r = beam_ratio[srow + lci_beam[i_load]:
                                srow + lci_beam[i_load] + 3, 2:6]

            ele_no = element[row, 0]
            if np.size(uniele_ID) == 0:
                plot_ok = True
            else:
                judge_unit = find_index(uniele_ID, ele_no) + 1  # MATLAB 1-based
                plot_ok = judge_unit <= select_unit

            if sec_no >= limit_sec_no:
                pass  # ダミー材
            elif c_b[0] in (2.0, 1.9):
                if plot_ok:
                    if select13[0] != 0:
                        raise ValueError('検定比図の位置別表示(select13)は'
                                         '原典に対応関数が無いため未対応です')
                    _column_maxratio_plot(ax, plot_r, ni, nj, nodeM, (1, 3),
                                          c_b, ratio_fs)
                    axis_ysubtick += [nodeM[ni, 0], nodeM[nj, 0]]
            elif c_b[0] == 1.0:
                if plot_ok:
                    if select13[1] != 0:
                        raise ValueError('検定比図の位置別表示(select13)は'
                                         '原典に対応関数が無いため未対応です')
                    _beam_maxratio_plot(ax, plot_r, ni, nj, nodeM, (1, 3),
                                        c_b, ratio_fs)
            else:
                print('柱梁タイプが定義されていません')
                raise RuntimeError('柱梁タイプが定義されていません')

        elif et == 3:  # トラス要素
            ni, nj = node_index_get(axis_elems[i_e], element, nodeM)
            ax.plot([nodeM[ni, 1] * 0.85 + nodeM[nj, 1] * 0.15,
                     nodeM[ni, 1] * 0.15 + nodeM[nj, 1] * 0.85],
                    [nodeM[ni, 3] * 0.85 + nodeM[nj, 3] * 0.15,
                     nodeM[ni, 3] * 0.15 + nodeM[nj, 3] * 0.85],
                    'ko-', markersize=1.5, linewidth=0.2)
            srow = ratio_row[i_e]
            plot_r = truss_ratio[srow + lci_truss[i_load], 2:4]
            _truss_ratio_plot(ax, plot_r, ni, nj, nodeM, ratio_fs)

        elif et == 2:
            # NOTE: 原典figure_ratio_axis.mの壁要素(etype2)分岐は全行
            #       コメントアウト済み (何も描かない)
            pass

        else:  # 図形ラインのみプロット (検定比データに無い要素)
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
                _unit_ratio_plot(ax, unit[1], unit[2], element, nodeM,
                                 beam_ratio, (1, 3), limit_sec_no,
                                 i_load, lci_beam, ratio_fs, select13)
                axis_ysubtick = _unit_add_ysub(
                    axis_ysubtick, unit[1], unit[2], element, nodeM,
                    limit_sec_no)
        elif not np.any(found):
            pass
        else:
            print('結合要素作成でエラー！？（構成要素の一部しか登録されていません）')

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

    # %%%%%% 図の枠の大きさ %%%%%%
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
        title = '長期荷重時・検定比図[　　荷重ケース：' + case_name + '　]'
    else:
        title = '組合荷重時・検定比図[　　組合荷重：' + case_name + '　]'

    _finish_figure(fig, ax, title, C['axis_name_sel'][i_axis],
                   xlim, ylim, title_fs, C['paper_size'])
    fw, fh = _save_fig_pdf_or_pgf(fig, out_path)
    plt.close(fig)
    return out_path, fw, fh


# ---------------------------------------------------------------------------
# メインAPI (MIDASratioplot_fig.m 1565-1810行の検定比図ループ)
# ---------------------------------------------------------------------------

def plot_ratio(mgt_path, out_dir, beam_ratio, truss_ratio,
               case_names,
               cases_select=None,
               axes_select=None, symbols_select=None, heights_select=None,
               select_unit=math.inf, select13=(0, 0),
               limit_sec_no=9000,
               axisname_location=3.0, line_location=2.0,
               mergins=(5.0, 2.0, 5.0, 5.0),
               fontsize=(4.0, 5.0, 6.0, 8.0),
               paper_size=4, cross_axis=1, cross_height=None,
               fig_format='pdf'):
    """検定結果 (beam_ratio/truss_ratio) から構面別の検定比図PDFを作成する.

    Parameters
    ----------
    beam_ratio / truss_ratio : ndarray
        run_steel_check の結果 (beam: [要素,ケース,r1..r4] 3行/要素,
        truss: [要素,ケース,r1,r2] 1行/要素)。
    case_names : list of str
        検定ケース名 (result.LCNAME、ケースブロック順)。
    cases_select : list of float or None
        描画する検定ケース番号 (beam_ratio 2列目の値)。None=全ケース。
    select_unit : float
        Inf=分割部材をバラバラに表示, 0=結合して一括表示 (検定と同じ値を渡す)。
    fontsize : (検定値, 寸法値, 通り芯符号, タイトル)
    他の引数は plot_stress と同じ。

    Returns
    -------
    list of str : 生成したPDFファイルパス。
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)
    beam_ratio = np.atleast_2d(np.asarray(beam_ratio, dtype=float))
    truss_ratio = (np.atleast_2d(np.asarray(truss_ratio, dtype=float))
                   if np.size(truss_ratio) else np.array([]))

    # %%%%%% モデルデータ読み込み %%%%%%
    node = mgtopen_node(mgt_path)
    element = mgtopen_element(mgt_path)
    axis_name, axis_element, axis_node = mgtopen_group(mgt_path)

    # %%%%%% 検定ケースの区切り検出 %%%%%%
    load_case_no_beam, lci_beam = _case_split_blocks(beam_ratio[:, 1])
    if np.size(truss_ratio) > 0:
        load_case_no_truss, lci_truss = _case_split_blocks(truss_ratio[:, 1])
    else:
        load_case_no_truss, lci_truss = np.array([]), np.array([], dtype=int)

    load_case_no = load_case_no_beam if load_case_no_beam.size \
        else load_case_no_truss
    if len(case_names) != load_case_no.size:
        raise ValueError('検定ケース名の個数(%d)がケース数(%d)と一致しません'
                         % (len(case_names), load_case_no.size))

    # %%%%%% 通り芯情報 %%%%%%
    v_idx = _resolve_select(axis_name, axes_select)
    if axes_select is None:
        v_idx = [i for i in v_idx if np.size(axis_node[i]) > 0]
    if not v_idx:
        print('WARNING: 描画対象の構面がありません')
        return []

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

    # %%%%%% 検定ケースの選択 %%%%%%
    if cases_select is None:
        sel_cases = list(load_case_no)
    else:
        sel_cases = []
        for c in cases_select:
            if find_index(load_case_no, float(c)) < 0:
                raise KeyError('検定ケース %s が検定結果にありません '
                               '(存在するケース: %s)'
                               % (c, ', '.join(str(int(v))
                                               for v in load_case_no)))
            sel_cases.append(float(c))

    C = dict(node=node, element=element,
             beam_ratio=beam_ratio, truss_ratio=truss_ratio,
             lci_beam=lci_beam, lci_truss=lci_truss,
             unit_element=unit_element, uniele_ID=uniele_ID,
             select13=tuple(select13), select_unit=select_unit,
             limit_sec_no=limit_sec_no,
             fontsize=tuple(fontsize), mergins=tuple(mergins),
             axisname_location=axisname_location,
             line_location=line_location,
             paper_size=paper_size,
             axis_name_sel=axis_name_sel,
             axis_element_sel=axis_element_sel,
             axis_node_sel=axis_node_sel,
             axis_plot_coefi=axis_plot_coefi,
             axis_direction=axis_direction,
             pick_axis_node_no=pick_axis_node_no,
             h_axis_plot=h_axis_plot)

    made = []
    meta = []
    tex_tmp = None
    fig_no = 100
    for case in sel_cases:
        i_load = int(find_index(load_case_no, case))
        case_name = str(case_names[i_load]).strip()
        title_case_no = float(case)

        for i_axis in range(len(v_idx)):
            if axis_plot_coefi[i_axis, 0] == 0:
                print('WARNING: 構面 %s は通り芯を近似できないため'
                      'スキップします' % axis_name_sel[i_axis])
                continue
            if str(fig_format) == 'tex':
                # 個別図は一時フォルダに作る (出力先には一式のみ残す)
                if tex_tmp is None:
                    tex_tmp = tempfile.mkdtemp(prefix='mgtkit_pgf_')
                out = os.path.join(tex_tmp, 'ratiofig%d.tex' % fig_no)
            else:
                out = os.path.join(
                    out_dir, 'ratio_%d_%s_%s.pdf'
                    % (fig_no, _safe_name(case_name),
                       _safe_name(axis_name_sel[i_axis])))
            out2, fw, fh = _figure_ratio_axis(
                C, i_axis, i_load, title_case_no, case_name, out)
            made.append(out2)
            meta.append((case_name, space_erace(axis_name_sel[i_axis]),
                         fw, fh))
            fig_no += 1

    # TeX出力時は全図をまとめた一式ファイル 10ratioplot.tex のみ出力する
    # (1図1ページ・自己完結。詳細は draw_model._write_pgf_bundle)
    if str(fig_format) == 'tex' and made:
        combined = os.path.join(out_dir, '10ratioplot.tex')
        _write_pgf_bundle(
            combined, made,
            [('検定ケース %s / 構面 %s' % (cs, ax0), fw, fh)
             for (cs, ax0, fw, fh) in meta],
            'mgtkitratiobox')
        made = [combined]
    if tex_tmp is not None:
        shutil.rmtree(tex_tmp, ignore_errors=True)

    return made


# ---------------------------------------------------------------------------
# 検定比表TeX (新規機能: 検定比図と同内容の値を longtable へ出力)
# ---------------------------------------------------------------------------

def _tex_escape(s):
    """TeXテキストモード用エスケープ."""
    s = str(s)
    for a, b in (('\\', r'\textbackslash{}'), ('&', r'\&'),
                 ('%', r'\%'), ('#', r'\#'), ('_', r'\_'),
                 ('$', r'\$'), ('{', r'\{'), ('}', r'\}'),
                 ('^', r'\textasciicircum{}'), ('~', r'\textasciitilde{}')):
        s = s.replace(a, b)
    # '--' はリガチャでエンダッシュになるため分離 (例: G+P--KX)
    s = s.replace('--', '-{}-')
    return s


def export_ratio_tex(mgt_path, out_dir, beam_ratio, truss_ratio, case_names,
                     cases_select=None, axes_select=None,
                     select_unit=math.inf, limit_sec_no=9000):
    """断面検定値一覧表 (原典 ratiotemplate_tex 形式) をTeXで出力する.

    構面ごとではなくモデル全体を断面(符号)ごとに1行とし、検定ケース
    ごとに [最大検定値の要素ID, 検定値] を並べる。書式は原典計算書の
    テンプレート (longtable + multirow + booktabs) に従う。
    axes_select は使用しない (互換のため引数は残す)。

    戻り値: (生成ファイルパスのリスト, tex行のlist)
    """
    try:
        from .section import mgtopen_section
    except ImportError:
        from section import mgtopen_section

    os.makedirs(out_dir, exist_ok=True)
    beam_ratio = np.atleast_2d(np.asarray(beam_ratio, dtype=float))
    truss_ratio = (np.atleast_2d(np.asarray(truss_ratio, dtype=float))
                   if np.size(truss_ratio) else np.array([]))

    node = mgtopen_node(mgt_path)
    element = mgtopen_element(mgt_path)
    axis_name, axis_element, axis_node = mgtopen_group(mgt_path)
    _secs, section_no, section_name = mgtopen_section(mgt_path)
    sec_names = {}
    for k, n in enumerate(np.atleast_1d(
            np.asarray(section_no, dtype=float)).ravel()):
        sec_names[int(n)] = str(section_name[k]).strip()

    load_case_no_beam, lci_beam = _case_split_blocks(beam_ratio[:, 1])
    if np.size(truss_ratio) > 0:
        load_case_no_truss, lci_truss = _case_split_blocks(truss_ratio[:, 1])
    else:
        load_case_no_truss, lci_truss = np.array([]), np.array([], dtype=int)
    load_case_no = load_case_no_beam if load_case_no_beam.size \
        else load_case_no_truss

    if cases_select is None:
        sel_cases = list(load_case_no)
    else:
        sel_cases = [float(c) for c in cases_select]
    i_loads = []
    for c in sel_cases:
        i = int(find_index(load_case_no, float(c)))
        if i < 0:
            raise KeyError('検定ケース %s が検定結果にありません' % c)
        i_loads.append(i)

    # 断面(符号)ごとに全要素から各ケースの最大検定値と要素IDを拾う
    n_case = len(i_loads)

    def _beam_max(sr, il):
        blk = beam_ratio[int(sr) + int(lci_beam[il]):
                         int(sr) + int(lci_beam[il]) + 3, 2:6]
        return float(np.max(blk))

    sec_rows = []
    for sec in doublecheck(element[:, 2]):
        if sec >= limit_sec_no:
            continue  # ダミー材
        eles = element[np.where(element[:, 2] == sec)[0], 0]
        per_case = []
        any_data = False
        for il in i_loads:
            best_e = None
            best_v = -1.0
            for e0 in eles:
                v = None
                sr = int(find_index(beam_ratio[:, 0], e0)) \
                    if np.size(beam_ratio) else -1
                if sr >= 0 and beam_ratio[sr, 0] == e0:
                    v = _beam_max(sr, il)
                elif np.size(truss_ratio) > 0:
                    tr = int(find_index(truss_ratio[:, 0], e0))
                    if tr >= 0 and truss_ratio[tr, 0] == e0:
                        v = float(np.max(
                            truss_ratio[tr + int(lci_truss[il]), 2:4]))
                if v is not None and v > best_v:
                    best_v = v
                    best_e = int(e0)
            if best_e is None:
                per_case.append(None)
            else:
                any_data = True
                per_case.append((best_e, best_v))
        if any_data:
            # 符号名は断面名の '_' より前 (例: WC2_■-120x120 → WC2)
            name = sec_names.get(int(sec), '').split('_')[0].strip()
            sec_rows.append((int(sec), name, per_case))
    sec_rows.sort(key=lambda r: r[0])
    if not sec_rows:
        raise ValueError('検定値のある断面がありません。先に「検定実行」の'
                         '対象・結果を確認してください')

    heads_all = [_tex_escape(str(case_names[il]).strip())
                 for il in i_loads]

    lines = []
    lines.append('% mgtkit 断面検定値一覧表 (原典 ratiotemplate_tex 形式)')
    lines.append('% 各断面×検定ケースの最大検定値とその要素ID')
    lines.append('% 検定ケース5個ごとに表を分割 (A4幅に収まる上限が5ケース)')
    lines.append('% 要 \\usepackage{longtable,multirow,booktabs,array}')
    lines.append(r'\vspace*{1zh}')
    lines.append(r'\def\arraystretch{1.0}')
    lines.append(r'\footnotesize')
    PER_TABLE = 5
    for t0 in range(0, n_case, PER_TABLE):
        m = min(PER_TABLE, n_case - t0)
        heads = heads_all[t0:t0 + m]
        head1 = (r'\multirow{2}{*}{断面ID} & \multirow{2}{*}{符号名}'
                 + ''.join(r'& \multicolumn{2}{c}{%s}' % h for h in heads)
                 + r'\\')
        sub = '&& ' + ' & '.join([r'要素ID & 検定値'] * m)
        if t0 > 0:
            lines.append(r'\clearpage')
        lines.append(r'\begin{center}')
        lines.append(r'\begin{longtable}{>{\centering\arraybackslash}p{4zw}'
                     r'>{\centering\arraybackslash}p{5zw}'
                     r'*{%d}{>{\centering\arraybackslash}p{4zw}}}'
                     % (2 * m))
        lines.append(r'\caption{\normalsize{断面検定値一覧表}} ')
        lines.append(r'\\\toprule')
        lines.append(head1)
        lines.append(r'\cline{3-%d}' % (2 + 2 * m))
        lines.append(sub + r' \\ \hline \endfirsthead ')
        lines.append(r'\toprule')
        lines.append(head1)
        lines.append(r'\cline{3-%d}' % (2 + 2 * m))
        lines.append(sub + r' \\ \hline \endhead')
        for sec, name, per_case in sec_rows:
            cells = []
            for pc in per_case[t0:t0 + m]:
                cells.append('-- & --' if pc is None
                             else '%d & %.2f' % pc)
            lines.append('%d & %s & %s' % (sec, _tex_escape(name),
                                           ' & '.join(cells))
                         + r' \\\hline ')
        lines.append(r'\end{longtable}')
        lines.append(r'\end{center}')
    lines.append(r'\normalsize')

    # \input{10ratiotable.tex} で読めるようアンダースコア無しのASCII短名
    # (10ratioplot.tex / 09stressplot.tex と同じ規約)
    out_path = os.path.join(out_dir, '10ratiotable.tex')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return [out_path], lines
