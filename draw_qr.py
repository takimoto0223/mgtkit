# -*- coding: utf-8 -*-
"""QR図 (Midas_QR.fig) の統合層.

原典: MatlabT\\privatetool_function\\MIDAS\\QR\\MIDAS_QRplot_fig.m (1311行)
      + Midas_QR.m (GUI既定値)

機能: 反力図 / 柱脚設計用データ(CB) / せん断力分担図 / 層間変形角 /
      重心・剛心・偏心率。描画・計算の本体は qr_reaction / qr_shear /
      qr_center の各モジュール (原典の figure_*.m / shear_pick/ / hensin/)。

本モジュールは原典の対話ダイアログ(uigetfile/listdlg/questdlg/inputdlg)を
引数に置き換えた入力整形・共通前処理を持つ:
  - 応力/反力/変形テキストの読込と終局形式(2行/要素)の3行展開
  - wall_stress の2行1組整形 (wall_stress_ID 分離)
  - 荷重ケース区切り (doublecheck + find_index)
  - 柱脚レベル z_point の列挙 (柱要素の下端Z + 壁要素ベースレベル)
  - pick_coefi (構面の一次式係数)
"""
import os

import numpy as np

from .util import find_index, doublecheck, loadtxt_tolerant
from .mgt import (mgtopen_node, mgtopen_element, mgtopen_plate,
                  mgtopen_wall, mgtopen_elasticlink, mgtopen_unit_2015,
                  mgtopen_group, node_index_get)
from .section import mgtopen_section
from .ratio_pipeline import column_beam_judge, sectionget
from .draw_stress import (read_beam_stress, read_truss_stress,
                          read_plate_stress, read_reaction, get_wstress,
                          _load_case_split)


# ---------------------------------------------------------------------------
# 変形データの読込 (MIDAS_QRplot_fig.m 223-234行)
# ---------------------------------------------------------------------------

def read_deformation(path):
    """deformation.txt: 1:節点番号 2:荷重ケース 3:dx 4:dy 5:dz"""
    a = np.atleast_2d(loadtxt_tolerant(path))
    if a.size and a.shape[1] < 5:
        raise ValueError('deformationファイルの列数が不足しています '
                         '(節点番号, 荷重ケース, dx, dy, dz の5列)')
    return a[:, 0:5] if a.size else np.zeros((0, 5))


# ---------------------------------------------------------------------------
# モデル・応力の一括読込 (MIDAS_QRplot_fig.m 42-242行の非対話版)
# ---------------------------------------------------------------------------

class QRData(object):
    """QR図の入力一式 (モデル+応力+ケース区切り)."""
    pass


def load_qr_data(mgt_path, beam_stress_path=None, truss_stress_path=None,
                 plate_stress_path=None, wall_stress_path=None,
                 reaction_path=None, deformation_path=None):
    """mgt と各応力テキストを読み込み前処理する (原典 42-242行).

    いずれの応力ファイルも省略可 (原典のキャンセル相当で空配列)。
    """
    D = QRData()
    D.mgt_path = mgt_path
    D.node = mgtopen_node(mgt_path)
    D.element = mgtopen_element(mgt_path)
    D.plate = mgtopen_plate(mgt_path)
    (D.sections, D.section_no, D.section_name) = mgtopen_section(mgt_path)
    (D.axis_name, D.axis_element, D.axis_node) = mgtopen_group(mgt_path)
    (D.wall_element, D.wall_base) = mgtopen_wall(mgt_path, D.node)
    D.link = mgtopen_elasticlink(mgt_path)

    # ---- 梁要素応力 (終局形式は3行/要素へ展開: 74-83行) ----
    if beam_stress_path:
        D.beam_stress = read_beam_stress(beam_stress_path)
    else:
        D.beam_stress = np.zeros((0, 8))

    # ---- トラス要素応力 ----
    if truss_stress_path:
        D.truss_stress = read_truss_stress(truss_stress_path)
    else:
        D.truss_stress = np.zeros((0, 4))

    # ---- 板要素応力 (原典101-126行: 板要素が無いモデルでは読まない) ----
    if plate_stress_path and np.size(D.plate):
        D.plate_stress = read_plate_stress(plate_stress_path)
    else:
        D.plate_stress = np.zeros((0, 4))

    # ---- 壁要素応力 (原典129-156行: 2行1組の整形) ----
    D.wall_stress = np.zeros((0, 6))
    D.wall_stress_ID = np.zeros((0, 3))
    if wall_stress_path and np.size(D.wall_element):
        raw = get_wstress(wall_stress_path)
        if np.size(raw):
            n_pair = raw.shape[0] // 2
            wall_stress_ID = np.zeros((n_pair, 3))
            ws = np.zeros((2 * n_pair, 6))
            for i in range(1, n_pair + 1):
                wall_stress_ID[i - 1, :] = raw[2 * i - 2, 0:3]
                # 原典150行: 2行目の先頭6値を4-9列へ移す → ID3列削除で
                # [1行目の4-9列 ; 2行目の1-6列] の6列2行になる
                ws[2 * i - 2, :] = raw[2 * i - 2, 3:9]
                ws[2 * i - 1, :] = raw[2 * i - 1, 0:6]
            D.wall_stress = ws
            D.wall_stress_ID = wall_stress_ID

    # ---- 反力 ----
    if reaction_path:
        D.reaction = read_reaction(reaction_path)
    else:
        D.reaction = np.zeros((0, 8))

    # ---- 変形 ----
    if deformation_path:
        D.deformation = read_deformation(deformation_path)
    else:
        D.deformation = np.zeros((0, 5))

    # ---- 荷重ケース区切り (原典158-242行) ----
    #  load_case_index_* は 0-based 先頭行 (MATLAB版は1-based。
    #  各 qr_* モジュールは 0-based 前提で移植されている)
    def _split(a, col):
        if not np.size(a):
            return np.zeros(0), np.zeros(0, dtype=int)
        no, idx = _load_case_split(a[:, col])
        return no, np.atleast_1d(np.asarray(idx, dtype=int))

    D.load_case_no_beam, D.load_case_index_beam = _split(D.beam_stress, 1)
    D.load_case_no_truss, D.load_case_index_truss = _split(D.truss_stress, 1)
    D.load_case_no_plate, D.load_case_index_plate = _split(D.plate_stress, 1)
    D.load_case_no_reaction, D.load_case_index_reaction = _split(
        D.reaction, 1)
    D.load_case_no_defo, D.load_case_index_defo = _split(D.deformation, 1)
    if np.size(D.wall_stress_ID):
        D.load_case_no_wall, D.load_case_index_wall = _split(
            D.wall_stress_ID, 2)
    else:
        D.load_case_no_wall = np.zeros(0)
        D.load_case_index_wall = np.zeros(0, dtype=int)

    # 原典221行: 検討対象の荷重ケース数 (梁・トラス・壁・反力の最大)
    D.num_load_case = int(max(
        D.load_case_no_beam.shape[0], D.load_case_no_truss.shape[0],
        D.load_case_no_wall.shape[0], D.load_case_no_reaction.shape[0]))

    # ---- 結合部材 (原典280-316行) ----
    D.unit_element = mgtopen_unit_2015(mgt_path, D.element, D.node)
    uniele_ID = []
    for iu in range(len(D.unit_element)):
        uniele_ID.extend(np.asarray(D.unit_element[iu][1]).ravel().tolist())
    D.uniele_ID = np.sort(np.asarray(uniele_ID, dtype=float))

    return D


def check_unit_dummy(D, limit_sec_no):
    """結合部材にダミー材が混在していないかチェック (原典283-309行).

    端部のみダミーの場合は結合情報から端部を削る。中間にある場合は
    原典どおりエラー (stop)。unit_element を書き換える。
    """
    for iunit in range(len(D.unit_element)):
        unit_ele_index = np.atleast_1d(
            find_index(D.element[:, 0], D.unit_element[iunit][1]))
        unit_ele_sec_no = D.element[unit_ele_index, 2]
        unit_ele_sec_no = (unit_ele_sec_no < limit_sec_no).astype(float)
        s = unit_ele_sec_no.sum()
        p = unit_ele_sec_no.prod()
        if s == 0 and p == 0:
            pass  # 全て非表示部材
        elif s > 0 and p > 0:
            pass  # 全て表示部材
        elif s != p:
            L = unit_ele_sec_no.shape[0]
            if np.prod(unit_ele_sec_no[1:L - 1]) == 0:
                raise RuntimeError(
                    '原典stop: 結合部材の中間にダミー材が混在しています '
                    '(要素 %s)' % np.asarray(
                        D.unit_element[iunit][1]).ravel().tolist())
            nodes = np.asarray(D.unit_element[iunit][2], dtype=float).ravel()
            if unit_ele_sec_no[L - 1] == 0:
                nodes = nodes[:-1]
            if unit_ele_sec_no[0] == 0:
                nodes = nodes[1:]
            D.unit_element[iunit][2] = nodes
            eles = np.asarray(D.unit_element[iunit][1], dtype=float).ravel()
            D.unit_element[iunit][1] = eles[unit_ele_sec_no > 0]


# ---------------------------------------------------------------------------
# 柱脚レベルの列挙 (原典434-456行ほか各機能で共通のz_point組立て)
# ---------------------------------------------------------------------------

def enum_z_points(node, element, wall_element):
    """柱要素(斜め柱含む)の下端Z + 壁要素ベースレベル → 昇順ユニーク列.

    原典: column_beam_judge で柱(判定2)と斜め材(判定3)を拾い
    (find(c_b_result-1) は判定1=梁 以外を返す)、min(節点Z)を集める。
    """
    z_point = []
    if np.size(element):
        # column_beam_judge (Python版) は0-based行indexを受ける
        c_b_result = np.atleast_1d(column_beam_judge(
            np.arange(element.shape[0]), element, node))
        c_index = np.where(c_b_result - 1 != 0)[0]  # 柱+斜め材
        for i_column in c_index:
            inode_index, jnode_index = node_index_get(
                element[i_column, 0], element, node)
            z_inode = min(node[inode_index, 3], node[jnode_index, 3])
            z_point.append(z_inode)
    z_point = np.asarray(z_point, dtype=float)
    if np.size(wall_element):
        z_point = np.concatenate(
            [z_point, np.asarray(wall_element)[:, 0].ravel()])
    if not np.size(z_point):
        return np.zeros(0)
    return doublecheck(z_point)


# ---------------------------------------------------------------------------
# pick_coefi (MIDAS_QRplot_fig.m 1224-1270行の非対話版)
# ---------------------------------------------------------------------------

def pick_coefi(v_axis_index, node, axis_node, axis_name, axis_element):
    """選択した鉛直構面の一次式係数を求める.

    v_axis_index: 1-based の構面index列 (原典listdlgの戻り相当)

    戻り値: (axis_plot_coefi, axis_name_select, axis_element_select,
             axis_node_select)
      axis_plot_coefi: (n,3) [1|2, A, B]
        1: X通り (X=AY+B) / 2: Y通り (Y=AX+B)

    NOTE: 原典1231行は axis_element_select{i}=axis_element{v_axis_index(1)}
          (常に最初の選択構面の要素列) となっており添字バグの疑いがあるが
          逐語再現する (axis_node_select は正しく {v_axis_index(i)})。
    """
    v_axis_index = [int(v) for v in v_axis_index]
    axis_name_select = [axis_name[i - 1] for i in v_axis_index]
    axis_element_select = []
    axis_node_select = []
    for i in range(len(v_axis_index)):
        axis_element_select.append(axis_element[v_axis_index[0] - 1])
        axis_node_select.append(axis_node[v_axis_index[i] - 1])

    axis_plot_coefi = np.zeros((len(v_axis_index), 3))
    for i_axis in range(len(v_axis_index)):
        on_idx = np.atleast_1d(find_index(
            node[:, 0], np.asarray(axis_node_select[i_axis],
                                   dtype=float).ravel()))
        i_axis_plotline = node[on_idx, 1:3]
        if i_axis_plotline.shape[0] > 1:
            # doublecheck2: 重複行の削除 (出現順)
            _, uidx = np.unique(i_axis_plotline, axis=0, return_index=True)
            i_axis_plotline = i_axis_plotline[np.sort(uidx)]
        X_var = float(np.var(i_axis_plotline[:, 0], ddof=1)) \
            if i_axis_plotline.shape[0] > 1 else 0.0
        Y_var = float(np.var(i_axis_plotline[:, 1], ddof=1)) \
            if i_axis_plotline.shape[0] > 1 else 0.0
        if Y_var > X_var:  # X通り: X=AY+B
            co = np.polyfit(i_axis_plotline[:, 1], i_axis_plotline[:, 0], 1)
            axis_plot_coefi[i_axis, :] = [1, co[0], co[1]]
        elif X_var >= Y_var:  # Y通り: Y=AX+B
            co = np.polyfit(i_axis_plotline[:, 0], i_axis_plotline[:, 1], 1)
            axis_plot_coefi[i_axis, :] = [2, co[0], co[1]]
        else:
            raise RuntimeError('原典stop: 通り芯の向きを判定できません')
    return (axis_plot_coefi, axis_name_select, axis_element_select,
            axis_node_select)


# ---------------------------------------------------------------------------
# 既定ケース名 (原典244-278行: 3ケースなら TL/KX/KY)
# ---------------------------------------------------------------------------

def default_qr_case_names(num_load_case):
    if num_load_case == 3:
        return ['TL', 'KX', 'KY']
    return ['CASE%d' % (i + 1) for i in range(num_load_case)]


# ---------------------------------------------------------------------------
# UI入力 → 原典引数 の共通変換
# ---------------------------------------------------------------------------

def resolve_axes(D, axes_names, vertical_names):
    """表示構面のグループ名リスト → 1-based index列.

    axes_names が None (UI全選択) の場合は鉛直構面すべて。
    """
    if axes_names is None:
        axes_names = vertical_names
    idx = []
    for nm in axes_names:
        if nm in D.axis_name:
            idx.append(D.axis_name.index(nm) + 1)
    if not idx:
        raise ValueError('表示構面(鉛直構面)が選択されていません。'
                         'mgtの*GROUPにNODE_LIST付きの鉛直構面が必要です。')
    return idx


def case_positions(D, case_numbers, cases_all):
    """ケース番号のリスト → load_case_name への1-based位置リスト."""
    pos = []
    for c in (case_numbers or []):
        i = int(np.atleast_1d(find_index(
            np.asarray(cases_all, dtype=float), float(c)))[0])
        if i == -1:
            raise ValueError('荷重ケース%gが応力ファイルにありません。'
                             '「ケース・レベル読込」をやり直してください。'
                             % float(c))
        pos.append(i + 1)
    return pos


def stories_to_case_height(stories, z_point):
    """UIの層構成 (層ごとのz値リスト) → case_height (1-based index列のlist)."""
    z_point = np.asarray(z_point, dtype=float).ravel()
    case_height = []
    for zs in (stories or []):
        idxs = []
        for z in zs:
            i = int(np.atleast_1d(find_index(z_point, float(z)))[0])
            if i != -1:
                idxs.append(i + 1)
        if idxs:
            case_height.append(idxs)
    return case_height


def group_nodes_elements(D, group_names):
    """算定グループ名リスト → (節点番号列, 要素番号列) (重複なし)."""
    sel_n = []
    sel_e = []
    for nm in (group_names or []):
        if nm not in D.axis_name:
            continue
        gi = D.axis_name.index(nm)
        sel_n.extend(np.atleast_1d(
            np.asarray(D.axis_node[gi], dtype=float)).ravel().tolist())
        sel_e.extend(np.atleast_1d(
            np.asarray(D.axis_element[gi], dtype=float)).ravel().tolist())
    return (doublecheck(np.asarray(sel_n, dtype=float))
            if sel_n else np.zeros(0),
            doublecheck(np.asarray(sel_e, dtype=float))
            if sel_e else np.zeros(0))


# ---------------------------------------------------------------------------
# 反力図 (MIDAS_QRplot_fig.m 322-420行の非対話版)
# ---------------------------------------------------------------------------

def plot_qr_reaction(D, out_dir, load_case_name, case_L, case_S, case_H,
                     axes_idx, scope='all', calc_groups=None,
                     mergins=(3.0, 3.0, 3.0, 5.0), fontsize=(3, 5, 5, 7, 5),
                     axisname_location=1.7, line_location=1.5,
                     paper_orient=2, paper_size=4):
    """反力図PDFの一括生成. 戻り値: 生成PDFパスのlist.

    case_L/case_S/case_H: load_case_name への1-based位置リスト
    (case_SとHは排他。原典の listdlg 選択に対応)
    scope='group' のとき calc_groups (グループ名list) の節点だけで算定
    (原典 byGroup)。
    """
    from .qr_reaction import figure_reaction
    os.makedirs(out_dir, exist_ok=True)
    if not np.size(D.reaction):
        raise ValueError('反力図には reaction ファイルの入力が必要です。')
    if not case_L:
        # 原典の癖: 鉛直ケース無しでは支点ID図の組立てでエラーとなるため
        # 明示エラーにする (qr_reaction NOTE1)
        raise ValueError('反力図では鉛直荷重ケースを1つ以上選択してください。')

    (axis_plot_coefi, axis_name_select, axis_element_select,
     axis_node_select) = pick_coefi(axes_idx, D.node, D.axis_node,
                                    D.axis_name, D.axis_element)
    mL, mR, mT, mB = [float(v) for v in mergins]
    f_s, f_d, f_a, f_t, f_re = [float(v) for v in fontsize]
    fig_no = 100

    if scope == 'group':
        # 原典 byGroup (374-406行): グループ節点の反力だけへ絞り込み
        select_group_node, _ = group_nodes_elements(D, calc_groups)
        if not np.size(select_group_node):
            raise ValueError('byGroup算定のグループが選択されていません。')
        sn_idx = np.atleast_1d(find_index(D.node[:, 0], select_group_node))
        select_node = D.node[sn_idx[sn_idx >= 0].astype(int), :]
        plot_reaction = []
        for iid in range(select_node.shape[0]):
            pick = np.where(D.reaction[:, 0] - select_node[iid, 0] == 0)[0]
            if pick.size:
                plot_reaction.append(D.reaction[pick, :])
        if not plot_reaction:
            raise ValueError('選択グループの節点に反力データがありません。')
        plot_reaction = np.vstack(plot_reaction)
        # sortrows(plot_reaction, [2 1])
        order = np.lexsort((plot_reaction[:, 0], plot_reaction[:, 1]))
        plot_reaction = plot_reaction[order, :]
        lc_no = doublecheck(plot_reaction[:, 1])
        lc_idx = np.atleast_1d(np.asarray(
            find_index(plot_reaction[:, 1], lc_no), dtype=int))
        (fig_no, made) = figure_reaction(
            D.unit_element, axis_element_select, plot_reaction,
            axis_node_select, D.node, lc_idx, D.element, D.uniele_ID,
            axis_name_select, mL, mR, mT, mB, axisname_location,
            line_location, f_d, f_a, f_t, f_re, load_case_name,
            paper_orient, paper_size, case_L, case_S, D.wall_element,
            D.truss_stress, case_H, fig_no, axis_plot_coefi, out_dir)
    else:
        # 原典 All (360-370行): axis_element は全グループのまま渡す
        (fig_no, made) = figure_reaction(
            D.unit_element, D.axis_element, D.reaction,
            axis_node_select, D.node, D.load_case_index_reaction,
            D.element, D.uniele_ID, axis_name_select,
            mL, mR, mT, mB, axisname_location, line_location,
            f_d, f_a, f_t, f_re, load_case_name,
            paper_orient, paper_size, case_L, case_S, D.wall_element,
            D.truss_stress, case_H, fig_no, axis_plot_coefi, out_dir)
    return made


def _defo_case_positions(D, case_numbers):
    """deformationのケース番号リスト → load_case_no_defo への1-based位置."""
    pos = []
    for c in (case_numbers or []):
        i = int(np.atleast_1d(find_index(
            D.load_case_no_defo, float(c)))[0])
        if i == -1:
            raise ValueError('荷重ケース%gがdeformationファイルにありません。'
                             % float(c))
        pos.append(i + 1)
    return pos


def _select_element(D, calc_groups):
    """byGroup: 選択グループの要素だけの element 行列 (原典の select_element)."""
    _, sel_e = group_nodes_elements(D, calc_groups)
    if not np.size(sel_e):
        raise ValueError('byGroup算定のグループが選択されていません。')
    eidx = np.atleast_1d(find_index(D.element[:, 0], sel_e))
    # 原典1068-1069行: elementに無い要素番号は除く
    return D.element[eidx[eidx >= 0].astype(int), :]


def _new_pdfs(out_dir, t0):
    """t0以降に生成・更新されたPDF一覧 (再実行の上書きも拾う)."""
    out = []
    for f in sorted(os.listdir(out_dir)):
        if not f.lower().endswith('.pdf'):
            continue
        fp = os.path.join(out_dir, f)
        if os.path.getmtime(fp) >= t0 - 1.0:
            out.append(fp)
    return out


# ---------------------------------------------------------------------------
# 層間変形角 (MIDAS_QRplot_fig.m 720-885行の非対話版)
# ---------------------------------------------------------------------------

def plot_qr_drift(D, out_dir, load_case_name, delta_case, axes_idx,
                  z_point, case_height, scope='all', calc_groups=None,
                  mergins=(3.0, 3.0, 3.0, 5.0), fontsize=(3, 5, 5, 7, 5),
                  paper_orient=2, paper_size=4, limit_sec_no=9000.0):
    """層間変形角図PDFの一括生成. 戻り値: 生成PDFパスのlist.

    delta_case: load_case_no_defo への1-based位置リスト
    case_height: 層ごとの z_point 1-based index列 (stories_to_case_height)
    """
    from .qr_center import pick_sheardrift, figure_drift
    os.makedirs(out_dir, exist_ok=True)
    if not np.size(D.deformation):
        raise ValueError('層間変形角には deformation ファイルの入力が必要です。')
    if not case_height:
        raise ValueError('層構成 (レベル→層番号) を設定してください。')
    if not delta_case:
        raise ValueError('層間変形角の荷重ケースを選択してください。')

    element = (D.element if scope != 'group'
               else _select_element(D, calc_groups))
    (axis_plot_coefi, axis_name_select, axis_element_select,
     axis_node_select) = pick_coefi(axes_idx, D.node, D.axis_node,
                                    D.axis_name, D.axis_element)
    collect_node = pick_sheardrift(
        case_height, D.node, element, D.deformation, D.beam_stress,
        D.link, limit_sec_no, D.sections, delta_case,
        D.load_case_index_defo, z_point)

    mL, mR, mT, mB = [float(v) for v in mergins]
    f_s, f_d, f_a, f_t, f_re = [float(v) for v in fontsize]
    import time as _time
    _t0 = _time.time()
    fig_no = 100
    collect_node = np.atleast_2d(np.asarray(collect_node, dtype=float))
    for ii in range(1, len(delta_case) + 1):
        for ij in range(1, len(case_height) + 1):
            # 原典784行: 層番号 ij の行を抽出
            pick_id = np.where(collect_node[:, 0] - ij == 0)[0]
            collect_node2 = collect_node[pick_id, :]
            if not collect_node2.shape[0]:
                print('注意: 層%d に層間変形角の算定対象部材がありません' % ij)
                continue
            on_idx = np.atleast_1d(find_index(
                D.node[:, 0], collect_node2[:, 2]))
            plotline = D.node[on_idx[on_idx >= 0].astype(int), 1:4]
            fig_no = figure_drift(
                f_s, mL, mR, mT, mB, f_t, load_case_name, collect_node2,
                ii, D.node, plotline[:, 0], plotline[:, 1],
                delta_case[ii - 1], z_point, paper_orient, paper_size,
                fig_no, case_height[ij - 1], axis_name_select,
                axis_element_select, axis_node_select, axis_plot_coefi,
                element, limit_sec_no, out_dir=out_dir)
    return _new_pdfs(out_dir, _t0)


# ---------------------------------------------------------------------------
# 重心・剛心・偏心率 (MIDAS_QRplot_fig.m 890-1219行の非対話版)
# ---------------------------------------------------------------------------

def plot_qr_center(D, out_dir, load_case_name, N_case, KX_case, KY_case,
                   axes_idx, z_point, case_height, scope='all',
                   calc_groups=None, plate_up=1.0,
                   mergins=(3.0, 3.0, 3.0, 5.0), fontsize=(3, 5, 5, 7, 5),
                   axisname_location=1.7, line_location=1.5,
                   paper_orient=2, paper_size=4, limit_sec_no=9000.0,
                   k_mode='fem', brace_baisu=None, thickness=None):
    """重心・剛心・偏心率の図+表の一括生成.

    N_case/KX_case/KY_case: load_case_no_defo への1-based位置 (スカラ)
    k_mode: 'fem'   = 原典どおり各部材の Q/δ から剛性を評価
            'baisu' = 壁倍率×壁長で剛性を評価 (板=厚み値が倍率、
                      ブレース=brace_baisu {断面番号: 倍率}。
                      deformation・plate_stress 不要)
    戻り値: (pdf_paths, table_rows, tex_lines)
      table_rows: [層, gx, gy, lx, ly, KR, rex, rey, Rex, Rey, Fex, Fey]
    NOTE: plate_up は原典では板要素応力の読込直後に尋ねるが割増の適用箇所は
          figure_rekr 内には無い (板剛性は変形のみ使用)。互換のため受けるが
          未使用 (原典どおり)。
    """
    from .qr_center import (figure_rekr, wg_center, kr_re, calc_REXY,
                            textable_REXY, figure_w_center, figure_g_center)
    os.makedirs(out_dir, exist_ok=True)
    if k_mode != 'baisu' and not np.size(D.deformation):
        raise ValueError('偏心率 (Q/δ方式) には deformation ファイルの'
                         '入力が必要です。')
    if not np.size(D.beam_stress):
        raise ValueError('偏心率には beam_stress ファイルの入力が必要です。')
    if not case_height:
        raise ValueError('層構成 (レベル→層番号) を設定してください。')

    element = (D.element if scope != 'group'
               else _select_element(D, calc_groups))
    (axis_plot_coefi, axis_name_select, axis_element_select,
     axis_node_select) = pick_coefi(axes_idx, D.node, D.axis_node,
                                    D.axis_name, D.axis_element)

    if k_mode == 'baisu':
        from .qr_baisu import figure_rekr_baisu
        (collect_node, node2) = figure_rekr_baisu(
            limit_sec_no, D.node, element, D.beam_stress,
            D.load_case_index_beam, z_point, case_height, N_case,
            D.plate, thickness, brace_baisu=brace_baisu)
        # 剛心図タイトル用: 剛性ケースの概念が無いため重心ケースを流用
        KX_case = N_case
        KY_case = N_case
    else:
        (collect_node, node2) = figure_rekr(
            limit_sec_no, D.unit_element, D.node, D.wall_base,
            D.wall_stress_ID, D.wall_stress, D.wall_element, D.beam_stress,
            D.truss_stress, element, D.uniele_ID,
            D.load_case_index_beam, D.load_case_index_truss,
            D.load_case_index_wall, D.load_case_no_beam, D.load_case_no_wall,
            load_case_name, z_point, D.link, D.sections, case_height,
            D.deformation, [], N_case, KX_case, KY_case,
            D.load_case_index_defo, D.plate, D.plate_stress,
            D.load_case_index_plate, D.load_case_no_plate)
    collect_node = np.atleast_2d(np.asarray(collect_node, dtype=float))

    if scope == 'group':
        # 原典1116-1125行: グループ要素の行だけ残す
        # NOTE: 壁・板の代表行は要素番号がグループ登録に無ければ落ちる
        #       (原典どおり)
        _, sel_e = group_nodes_elements(D, calc_groups)
        keep = []
        for iii in range(collect_node.shape[0]):
            fi = int(np.atleast_1d(find_index(
                sel_e, collect_node[iii, 1]))[0])
            if fi != -1:
                keep.append(iii)
        collect_node = collect_node[keep, :]

    # 原典970-978行: j=0, jj=jjj=jjjj=1 (single選択) → 列は5/6/7 (1-based)
    n_story = len(case_height)
    center = np.zeros((n_story, 4))
    kr = np.zeros((n_story, 1))
    re = np.zeros((n_story, 2))
    for ii in range(1, n_story + 1):
        center[ii - 1, :] = wg_center(ii, element, collect_node, node2,
                                      5, 6, 7)
        kr_i, re_i = kr_re(ii, element, collect_node, node2, 5, 6, 7,
                           center[ii - 1, :])
        kr[ii - 1, 0] = kr_i
        re[ii - 1, :] = re_i
    REXY = np.atleast_2d(calc_REXY(center, re))
    tex_lines = textable_REXY(case_height, kr, re, center, REXY)

    # UI表 (Fe は textable と同式)
    FEXY = np.minimum(1.5, np.maximum(
        1.0, ((REXY - 0.15) * 1.5 + (0.3 - REXY) * 1.0) / 0.15))
    table = []
    for ii in range(n_story):
        table.append(['%dF' % (ii + 1)]
                     + ['%.2f' % v for v in center[ii, :]]
                     + ['%.3e' % kr[ii, 0]]
                     + ['%.1f' % v for v in re[ii, :]]
                     + ['%.3f' % v for v in REXY[ii, :]]
                     + ['%.3f' % v for v in FEXY[ii, :]])

    mL, mR, mT, mB = [float(v) for v in mergins]
    f_s, f_d, f_a, f_t, f_re = [float(v) for v in fontsize]
    import time as _time
    _t0 = _time.time()
    for ii in range(1, n_story + 1):
        pick_id = np.where(collect_node[:, 0] - ii == 0)[0]
        collect_node2 = collect_node[pick_id, :]
        if not collect_node2.shape[0]:
            print('注意: 層%d に偏心率の算定対象部材がありません' % ii)
            continue
        on_idx = np.atleast_1d(find_index(node2[:, 0], collect_node2[:, 2]))
        plotline = node2[on_idx[on_idx >= 0].astype(int), 1:4]
        z_sel = np.asarray(z_point, dtype=float)[
            [i - 1 for i in case_height[ii - 1]]]
        # 重心 (plot_no=5)
        figure_w_center(
            f_s, element, mL, mR, mT, mB, axisname_location, line_location,
            f_d, f_a, f_t, load_case_name, collect_node2, node2,
            plotline[:, 0], plotline[:, 1], N_case, z_sel,
            paper_orient, paper_size, 5, center[ii - 1, :],
            axis_name_select, axis_element_select, axis_node_select,
            axis_plot_coefi, limit_sec_no, out_dir=out_dir)
        # X方向剛心 (plot_no=6, xy=1)
        figure_g_center(
            f_s, element, mL, mR, mT, mB, axisname_location, line_location,
            f_d, f_a, f_t, load_case_name, collect_node2, node2,
            plotline[:, 0], plotline[:, 1], KX_case, z_sel,
            paper_orient, paper_size, 6, center[ii - 1, :],
            kr[ii - 1, :], re[ii - 1, :], REXY[ii - 1, :], 1,
            axis_name_select, axis_element_select, axis_node_select,
            axis_plot_coefi, limit_sec_no, out_dir=out_dir)
        # Y方向剛心 (plot_no=7, xy=2)
        figure_g_center(
            f_s, element, mL, mR, mT, mB, axisname_location, line_location,
            f_d, f_a, f_t, load_case_name, collect_node2, node2,
            plotline[:, 0], plotline[:, 1], KY_case, z_sel,
            paper_orient, paper_size, 7, center[ii - 1, :],
            kr[ii - 1, :], re[ii - 1, :], REXY[ii - 1, :], 2,
            axis_name_select, axis_element_select, axis_node_select,
            axis_plot_coefi, limit_sec_no, out_dir=out_dir)
    return _new_pdfs(out_dir, _t0), table, tex_lines


# ---------------------------------------------------------------------------
# せん断力分担図 (MIDAS_QRplot_fig.m 482-715行の非対話版)
# ---------------------------------------------------------------------------

def _sec_class_arrays(sec_class):
    """UIの断面分類 {断面番号: 'g'|'c'|'w'|'v'|'x'} → 4配列."""
    g, c, w, v = [], [], [], []
    for no, cls in (sec_class or {}).items():
        no = float(no)
        if cls == 'g':
            g.append(no)
        elif cls == 'c':
            c.append(no)
        elif cls == 'w':
            w.append(no)
        elif cls == 'v':
            v.append(no)
    return (np.asarray(c, dtype=float), np.asarray(g, dtype=float),
            np.asarray(w, dtype=float), np.asarray(v, dtype=float))


def plot_qr_shear(D, out_dir, load_case_name, case_S, sei_direction,
                  stories, sec_class, scope='all', calc_groups=None,
                  mergins=(3.0, 3.0, 3.0, 5.0), fontsize=(3, 5, 5, 7, 5),
                  line_location=1.5, paper_orient=2, paper_size=4,
                  limit_sec_no=9000.0):
    """せん断力分担図PDFの一括生成.

    case_S: load_case_name への1-based位置リスト
    sei_direction: case_S と同順の加力方向リスト (1=X/2=Y/3=45度/4=135度)
    stories: 層ごとの z値リスト (UIの層構成)
    戻り値: (pdf_paths, tex_lines)
    """
    from .qr_shear import figure_shear
    os.makedirs(out_dir, exist_ok=True)
    if not case_S:
        raise ValueError('せん断力分担図の荷重ケースを選択してください。')

    if scope == 'group':
        # 原典 byGroup (636-707行): グループの要素・節点だけで算定。
        # z_point もグループ内の柱要素の下端Zのみから作る (壁レベルは
        # 含めない: 原典665-678行どおり)
        sel_n, sel_e = group_nodes_elements(D, calc_groups)
        eidx = np.atleast_1d(find_index(D.element[:, 0], sel_e))
        element = D.element[eidx[eidx >= 0].astype(int), :]
        nidx = np.atleast_1d(find_index(D.node[:, 0], sel_n))
        node = D.node[nidx[nidx >= 0].astype(int), :]
        z_point = enum_z_points(node, element, np.zeros(0))
    else:
        element = D.element
        node = D.node
        z_point = enum_z_points(D.node, D.element, D.wall_element)
    case_height = stories_to_case_height(stories, z_point)
    if not case_height:
        raise ValueError('層構成 (レベル→層番号) を設定してください。')

    (c_no, g_no, w_no, v_no) = _sec_class_arrays(sec_class)
    mL, mR, mT, mB = [float(v) for v in mergins]
    f_s, f_d, f_a, f_t, f_re = [float(v) for v in fontsize]

    (pdf_paths, tex_lines, Q_c, Q_w, fig_no) = figure_shear(
        limit_sec_no, f_s, node, D.wall_stress_ID, D.wall_stress,
        D.wall_element, D.beam_stress, D.truss_stress, element,
        mL, mR, mT, mB, line_location, f_t,
        D.load_case_index_beam, D.load_case_index_truss,
        D.load_case_index_wall, D.load_case_no_wall, load_case_name,
        paper_orient, paper_size, case_S, z_point, D.link, D.sections,
        case_height, c_no, g_no, w_no, v_no, sei_direction, out_dir,
        QR_TeX_txt=None, fig_no=100)
    return pdf_paths, list(tex_lines or [])


# ---------------------------------------------------------------------------
# 柱脚設計用データ CB (MIDAS_QRplot_fig.m 424-479行の非対話版)
# ---------------------------------------------------------------------------

def plot_qr_cb(D, out_dir, load_case_name, case_CB, cb_levels, axes_idx,
               mergins=(3.0, 3.0, 3.0, 5.0), fontsize=(3, 5, 5, 7, 5),
               axisname_location=1.7, line_location=1.5,
               paper_orient=2, paper_size=4, limit_sec_no=9000.0):
    """柱脚設計用データRESULTの組立てと平面図PDF.

    case_CB: load_case_name への1-based位置リスト
    cb_levels: 柱脚レベルのz値リスト (原典の listdlg 選択に対応)
    戻り値: (RESULT(N×7), pdf_paths)
    """
    from .qr_shear import figure_CB
    os.makedirs(out_dir, exist_ok=True)
    if not np.size(D.beam_stress):
        raise ValueError('柱脚データには beam_stress の入力が必要です。')
    if not case_CB:
        raise ValueError('柱脚データの荷重ケースを選択してください。')
    z_point = enum_z_points(D.node, D.element, D.wall_element)
    if cb_levels:
        z_sel = np.asarray([float(z) for z in cb_levels], dtype=float)
    else:
        z_sel = z_point  # 原典459行: レベルが1つなら選択なしで全レベル
    (axis_plot_coefi, axis_name_select, _es, _ns) = pick_coefi(
        axes_idx, D.node, D.axis_node, D.axis_name, D.axis_element)
    mL, mR, mT, mB = [float(v) for v in mergins]
    f_s, f_d, f_a, f_t, f_re = [float(v) for v in fontsize]
    (RESULT, pdf_paths) = figure_CB(
        limit_sec_no, f_s, D.node, D.wall_element, D.beam_stress,
        D.truss_stress, D.element, axis_name_select, mL, mR, mT, mB,
        axisname_location, line_location, f_d, f_a, f_t,
        D.load_case_index_beam, D.node[:, 2], D.node[:, 1],
        load_case_name, paper_orient, paper_size, case_CB,
        axis_plot_coefi, z_sel, out_dir)
    return RESULT, pdf_paths
