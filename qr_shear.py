# -*- coding: utf-8 -*-
"""せん断力分担図・柱脚設計用データ (Midas_QR の figure_shear.m / figure_CB.m 移植).

MATLAB GUIツール Midas_QR (privatetool_function/MIDAS/QR/) のうち、
せん断力分担図 (figure_shear.m と shear_pick/ 配下の8関数) と
柱脚設計用データ作成 (figure_CB.m) を Python/matplotlib(Agg) に移植したもの。

移植元:
  figure_shear.m               : せん断力分担図1ケース×1レベルセット分
  figure_CB.m                  : 柱脚設計用応力 RESULT の組立てと平面図
  shear_pick/wall_shear_pick.m   / wall_shear_plot.m
  shear_pick/truss_shear_pick.m  / truss_shear_plot.m
  shear_pick/column_shear_pick.m / column_shear_plot.m
  shear_pick/cb_shear_pick.m     / cb_shear_plot.m

MATLAB版との意図的差異:
  - listdlg('水平力方向') は figure_shear の引数 sei_direction
    (ケースごとの list、1=X方向/2=Y方向/3=45度方向/4=135度方向) に置換。
  - print_format/close_set は廃止。1レベルセット×1ケース=PDF1枚を
    out_dir へ保存する (print_format=3 相当)。
  - 原典 figure_shear は [fig_no QR_TeX_txt] を返すが、本移植は
    (pdf_paths, QR_TeX_txt, Q_c, Q_w, fig_no) を返す (内容保存のため)。
    Q_c/Q_w は原典ではケースごとに上書きされる作業配列だが、
    本移植では全ケース分を (ケース数×レベルセット数) の2次元で返す。
  - 原典 figure_CB は RESULT のみ返し Midas_QR.m 140行の `save CB.mat` で
    保存していた。保存は統合層が行うので本関数は (RESULT, pdf_paths) を
    返すだけ。
  - 荷重ケースの区切り load_case_index_* は Python 版 find_index の
    0-based 値を受ける (MATLAB原典は1-based行番号。内部の添字計算は
    0-based に一貫置換済み)。ケース番号 case_S/case_CB や
    case_height の中身は原典と同じ 1-based のまま。
  - case_height: 原典は listdlg ループの cell で最終要素が空
    ([] でループ終了) のため size(case_height,2)-1 を使う。本移植は
    空要素を含まない list[list[int]] を受け、len(case_height) と読み替える。
  - 原典 figure_CB 393行の x_label 組立て (支点レベルが複数の場合) は
    未定義変数 num_z_reac を参照しエラー停止する。x_label は関数内で
    未使用の死にコードのため、本移植ではエラーを再現せずスキップする
    (レポート参照)。

原典疑義 (挙動は変えず # NOTE: 参照):
  - figure_shear.m 205-206行: z1_inode/z2_inode がともに i端節点を参照
    (j端を見ていない)。
  - figure_shear.m 159-166行: ダミー断面除去ループが i_link=1 固定で
    先頭行しか判定しない。
  - figure_shear.m 260/263行: wall_element を応力行番号 j で添字参照する。
  - wall_shear_pick.m 29行は WQ_Z*qz_cos - WQ_Y*qy_cos (マイナス)、
    wall_shear_plot.m 35行は + (プラス) で符号が食い違う。
  - figure_CB.m 129-133行: 柱 (i端が下) の Y方向せん断力が j端行
    plot_stress(3,:) と angle+90 を使う (X方向は i端行と angle-90)。
"""

import math
import cmath
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    from .util import (
        find_index, find_zero_index, doublecheck, doublecheck2, _matlab_round,
    )
    from .mgt import node_index_get
    from .ratio_pipeline import column_beam_judge, sectionget
    from .draw_stress import _wallinfo_get
    from .draw_model import _setup_japanese_font, _PAPER_LAND, _safe_name
except ImportError:  # スクリプト実行時
    from util import (
        find_index, find_zero_index, doublecheck, doublecheck2, _matlab_round,
    )
    from mgt import node_index_get
    from ratio_pipeline import column_beam_judge, sectionget
    from draw_stress import _wallinfo_get
    from draw_model import _setup_japanese_font, _PAPER_LAND, _safe_name


# ---------------------------------------------------------------------------
# 小物ヘルパー
# ---------------------------------------------------------------------------

def _n(x, fmt='%15.1f'):
    """MATLAB num2str(x, fmt) 相当 (前後の空白を除去)."""
    return (fmt % float(x)).strip()


def _cosd(a):
    return math.cos(math.radians(a))


def _sind(a):
    return math.sin(math.radians(a))


def _shear_direction4(sei_direction):
    """pick系関数冒頭の4方向 shear_direction (1=X/2=Y/3=45度/4=135度)."""
    if sei_direction == 1:
        return np.array([1.0, 0.0])
    elif sei_direction == 2:
        return np.array([0.0, 1.0])
    elif sei_direction == 3:
        return np.array([1.0 / math.sqrt(2), 1.0 / math.sqrt(2)])
    elif sei_direction == 4:
        return np.array([-1.0 / math.sqrt(2), 1.0 / math.sqrt(2)])
    raise RuntimeError('原典: sei_direction が1-4以外 (shear_direction未定義)')


def _dir_coord(node_info, idx, sei_direction):
    """加力方向の水平座標 (符号判定用の射影).

    原典は node(:, sei_direction+1) を参照するため 45度でZ座標列、
    135度で範囲外列 (IndexError) になる。X / Y / (X+Y)/√2 / (Y-X)/√2
    への射影に置き換える (原典から意図的に修正 2026-07-18)。
    """
    if sei_direction == 1:
        return float(node_info[idx, 1])
    if sei_direction == 2:
        return float(node_info[idx, 2])
    if sei_direction == 3:
        return float(node_info[idx, 1] + node_info[idx, 2]) / math.sqrt(2.0)
    return float(node_info[idx, 2] - node_info[idx, 1]) / math.sqrt(2.0)


def _shear_direction3(sei_direction):
    """plot系関数冒頭の3分岐版 (else が45度/135度共用).

    NOTE: 原典 plot系 (wall/truss/column/cb_shear_plot.m) は
    if/elseif/else の3分岐のため sei_direction==4 (135度) でも
    [1/sqrt(2) 1/sqrt(2)] (45度) になる (pick系4分岐と食い違う)。原典ママ。
    """
    if sei_direction == 1:
        return np.array([1.0, 0.0])
    elif sei_direction == 2:
        return np.array([0.0, 1.0])
    return np.array([1.0 / math.sqrt(2), 1.0 / math.sqrt(2)])


def _rot4(n, ang):
    """cb_shear_pick/plot.m の4x4 Rodrigues回転行列 (複素対応、逐語)."""
    nx = complex(n[0])
    ny = complex(n[1])
    nz = complex(n[2])
    c = cmath.cos(ang)
    s = cmath.sin(ang)
    return np.array([
        [nx * nx * (1 - c) + c, nx * ny * (1 - c) - nz * s,
         nz * nx * (1 - c) + ny * s, 0],
        [nx * ny * (1 - c) + nz * s, ny * ny * (1 - c) + c,
         ny * nz * (1 - c) - nx * s, 0],
        [nz * nx * (1 - c) - ny * s, ny * nz * (1 - c) + nx * s,
         nz * nz * (1 - c) + c, 0],
        [0, 0, 0, 1]], dtype=complex)


def _rot4_apply(mat, vec3):
    """MATLAB: mat*[vec;1] の後 4成分目を削除."""
    v = np.append(np.asarray(vec3, dtype=complex), 1.0)
    return (mat @ v)[:3]


def _set_paper(fig, paper_orient, paper_size):
    """figure_*.m 末尾の set(gcf,'Paper...') 相当 (PaperSizeのinch値を使用).

    paper_orient: 1=landscape(横), その他=portrait(縦)
    paper_size  : 1=A1, 2=A2, 3=A3, 4=A4
    """
    w, h = _PAPER_LAND[int(paper_size)]
    if paper_orient == 1:
        fig.set_size_inches(w, h)
    else:
        fig.set_size_inches(h, w)


def _empty(x):
    return x is None or np.size(x) == 0


def _ax_text(ax, x, y, s, ha, va, fs, rot=0.0, color='k'):
    """MATLAB text(...) 相当 (VerticalAlignment 'middle'→'center')."""
    va_map = {'middle': 'center', 'top': 'top', 'bottom': 'bottom',
              'baseline': 'baseline'}
    ax.text(float(x), float(y), s, ha=ha, va=va_map.get(va, va),
            fontsize=fs, rotation=rot, rotation_mode='anchor', color=color,
            zorder=5)


# ---------------------------------------------------------------------------
# shear_pick/wall_shear_pick.m
# ---------------------------------------------------------------------------

def wall_shear_pick(wall_stress_ID, wall_stress, wall_element, node, axis,
                    load_case):
    """壁のせん断力を水平力方向へ射影して抽出する (wall_shear_pick.m).

    引数:
      wall_stress_ID : (1x3 or Nx3) [ベースレベル, 壁ID, 荷重ケース]
      wall_stress    : 上記IDに対応する壁応力行 (2行/壁: 頭・脚)。
                       列: [N, Qy, Qz, My, Mz, ...] (整形後の4-9列目)
      wall_element   : mgtopen_wall の壁要素表 (Nx10)
      node           : mgtopen_node の節点表
      axis           : 1+sei_direction (2=X/3=Y/4=45度/5=135度)
      load_case      : 荷重ケース番号 (load_case_no_wall の値)

    戻り値: w_shear_i (float) 指定方向のせん断力

    NOTE: 原典29行は WQ_Z*qz_cos - WQ_Y*qy_cos (減算)。
          wall_shear_plot.m 35行は加算で食い違う (原典ママ)。
    """
    wall_stress_ID = np.atleast_2d(np.asarray(wall_stress_ID, dtype=float))
    wall_stress = np.atleast_2d(np.asarray(wall_stress, dtype=float))

    sei_direction = axis - 1
    shear_direction = _shear_direction4(sei_direction)

    w_shear_i = None
    for i in range(wall_stress_ID.shape[0]):
        if wall_stress_ID[i, 2] == load_case:
            WQ_Z = wall_stress[2 * i + 1, 2]  # 耐力壁脚のせん断力
            WQ_Y = wall_stress[2 * i + 1, 1]
            select_wall, stress_ID = _wallinfo_get(wall_element,
                                                   wall_stress_ID[i, 0:2])
            if np.size(stress_ID) == 0:
                print('ERROR:応力に対応する要素判定ミス')
                raise RuntimeError('原典stop: 応力に対応する要素判定ミス '
                                   '(wall_shear_pick)')
            # MATLAB: wall_element(stress_ID,9:10) の線形添字 (1),(2)
            wv = wall_element[np.asarray(stress_ID, dtype=int), 8:10]
            wall_vec = wv.ravel(order='F')
            # wall_normal = [real((wx+wy*j)*j) imag((wx+wy*j)*j)] = [-wy, wx]
            wall_normal = np.array([-wall_vec[1], wall_vec[0]])
            qz_cos = (shear_direction[0] * wall_vec[0]
                      + shear_direction[1] * wall_vec[1])
            qy_cos = (shear_direction[0] * wall_normal[0]
                      + shear_direction[1] * wall_normal[1])
            w_shear_i = WQ_Z * qz_cos - WQ_Y * qy_cos
    if w_shear_i is None:
        # MATLAB: 出力未定義のままreturnしエラーとなる
        raise RuntimeError('原典: wall_shear_pick で load_case に一致する'
                           '応力行がなく w_shear_i 未定義')
    return float(w_shear_i)


# ---------------------------------------------------------------------------
# shear_pick/truss_shear_pick.m
# ---------------------------------------------------------------------------

def truss_shear_pick(stress, nodei, nodej, node_info, axis):
    """トラス(ブレース)材の軸力を水平力方向へ射影 (truss_shear_pick.m).

    引数:
      stress    : truss_stress の1行 (1x4) [要素, ケース, Fx(i), Fx(j)]
      nodei/nodej : node_info 内の行index (0-based)
      node_info : 節点表
      axis      : 1+sei_direction (2=X/3=Y/4=45度/5=135度)

    NOTE: 原典22行 node_info(nodei,axis) は axis=4 (45度) でZ座標列、
          axis=5 (135度) で範囲外列を参照する。加力方向への射影
          (_dir_coord) に置き換えた (意図的修正)。
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    sei_direction = axis - 1
    shear_direction = _shear_direction4(sei_direction)

    truss_vec = np.array([
        abs(node_info[nodei, 1] - node_info[nodej, 1]),
        abs(node_info[nodei, 2] - node_info[nodej, 2])])
    truss_vec = truss_vec / np.linalg.norm(truss_vec)

    # せん断力表示のための準備
    XY = math.sqrt((node_info[nodei, 1] - node_info[nodej, 1]) ** 2
                   + (node_info[nodei, 2] - node_info[nodej, 2]) ** 2)
    Z = abs(node_info[nodei, 3] - node_info[nodej, 3])
    cos_ = XY / math.sqrt(XY ** 2 + Z ** 2)
    q_cos = (shear_direction[0] * truss_vec[0]
             + shear_direction[1] * truss_vec[1])
    if (_dir_coord(node_info, nodei, sei_direction)
            < _dir_coord(node_info, nodej, sei_direction)):
        if node_info[nodei, 3] < node_info[nodej, 3]:
            t_shear_i = q_cos * cos_ * stress[0, 3]
        else:
            t_shear_i = -1 * q_cos * cos_ * stress[0, 3]
    else:
        if node_info[nodej, 3] < node_info[nodei, 3]:
            t_shear_i = q_cos * cos_ * stress[0, 3]
        else:
            t_shear_i = -1 * q_cos * cos_ * stress[0, 3]
    return float(t_shear_i)


# ---------------------------------------------------------------------------
# shear_pick/column_shear_pick.m
# ---------------------------------------------------------------------------

def column_shear_pick(stress, nodei, nodej, node_info, axis, angle):
    """完全鉛直柱の脚部せん断力を水平力方向へ射影 (column_shear_pick.m).

    引数:
      stress    : beam_stress の3行 (3x8) [要素,ケース,Fx,Fy,Fz,Mx,My,Mz]
                  (i端・中央・j端)
      nodei/nodej : node_info 内の行index (0-based)
      axis      : 1+sei_direction (2=X/3=Y/4=45度/5=135度)
      angle     : β角 (要素表6列目, 度)
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    sei_direction = axis - 1
    shear_direction = _shear_direction4(sei_direction)

    # 基本的にi端を下とする．
    if node_info[nodei, 3] > node_info[nodej, 3]:
        q_reverse = -1
        angle = angle * -1
        qz_shear = (_cosd(angle) * shear_direction[0]
                    + _sind(angle) * shear_direction[1])
        qy_shear = (_cosd(angle + 90) * shear_direction[0]
                    + _sind(angle + 90) * shear_direction[1])
        c_shear_i = (stress[2, 4] * qz_shear
                     + stress[2, 3] * qy_shear) * q_reverse
    elif node_info[nodei, 3] < node_info[nodej, 3]:
        q_reverse = 1
        qz_shear = (_cosd(angle) * shear_direction[0]
                    + _sind(angle) * shear_direction[1])
        qy_shear = (_cosd(angle - 90) * shear_direction[0]
                    + _sind(angle - 90) * shear_direction[1])
        c_shear_i = (stress[0, 4] * qz_shear
                     + stress[0, 3] * qy_shear) * q_reverse
    else:
        print('柱部材の定義ミス？')
        raise RuntimeError('原典stop: 柱部材の定義ミス？ (column_shear_pick)')
    return float(c_shear_i)


# ---------------------------------------------------------------------------
# shear_pick/cb_shear_pick.m
# ---------------------------------------------------------------------------

def _cb_shear_core(stress, nodei_index, nodej_index, node, sei_direction,
                   angle):
    """cb_shear_pick.m / cb_shear_plot.m 共通のせん断力算定部 (逐語).

    斜め柱・ブレース (角度付き梁要素) の断面力 (Fx,Fy,Fz) を部材座標軸の
    全体系表現 (XAXIS/YAXIS/ZAXIS) を介して水平力方向へ射影する。
    """
    XAXIS = node[nodej_index, 1:4] - node[nodei_index, 1:4]
    XAXIS = XAXIS / np.linalg.norm(XAXIS)

    L = np.linalg.norm(node[nodei_index, 1:4] - node[nodej_index, 1:4])
    L_H = np.linalg.norm(node[nodei_index, 1:3] - node[nodej_index, 1:3])  # noqa: F841 原典ママ(未使用)
    L_X = abs(node[nodei_index, 1] - node[nodej_index, 1])
    L_Y = abs(node[nodei_index, 2] - node[nodej_index, 2])
    L_V = abs(node[nodei_index, 3] - node[nodej_index, 3])  # noqa: F841 原典ママ(未使用)

    if node[nodei_index, 3] < node[nodej_index, 3]:
        Q = stress[0, 2:5]
    else:
        Q = stress[2, 2:5]

    Q_x = Q[0]
    Q_y = Q[1]
    Q_z = Q[2]

    # 部材の方向計算 (MATLAB asin/cos は複素数となり得るため cmath を使用)
    if XAXIS[0] >= 0:
        fai = cmath.asin(-1 * complex(XAXIS[2]))
    else:
        fai = -cmath.pi - cmath.asin(-1 * complex(XAXIS[2]))
    sita = cmath.asin(-1 * complex(XAXIS[1]) / cmath.cos(fai))
    Rz = np.array([[cmath.cos(sita), cmath.sin(sita), 0],
                   [-1 * cmath.sin(sita), cmath.cos(sita), 0],
                   [0, 0, 1]], dtype=complex)
    ZAXIS = Rz @ np.array([0, 0, 1], dtype=complex)
    YAXIS = Rz @ np.array([0, 1, 0], dtype=complex)

    ZAXIS = _rot4_apply(_rot4(YAXIS, fai), ZAXIS)

    # ここでもしZ軸のZ座標が負だと，Z軸周りに180度回転させる．
    if ZAXIS[2].real < 0:  # MATLAB: 複素数の比較は実部
        beta = cmath.pi
        ZAXIS = _rot4_apply(_rot4(XAXIS, beta), ZAXIS)
        YAXIS = _rot4_apply(_rot4(XAXIS, beta), YAXIS)

    angle = -1 * angle
    beta = -1 * angle / 180 * cmath.pi
    ZAXIS = _rot4_apply(_rot4(XAXIS, beta), ZAXIS)

    beta = -1 * angle / 180 * cmath.pi
    YAXIS = _rot4_apply(_rot4(XAXIS, beta), YAXIS)

    if sei_direction == 1:
        Q_x = np.real(L_X / L * Q_x)
        Q_y = np.real(Q_y * YAXIS[0])
        Q_z = np.real(Q_z * ZAXIS[0])
    elif sei_direction == 2:
        Q_x = np.real(L_Y / L * Q_x)
        Q_y = np.real(Q_y * YAXIS[1])
        Q_z = np.real(Q_z * ZAXIS[1])
    else:
        # NOTE: 原典は45度/135度とも同一 (else共用) で [X成分+Y成分]/sqrt(2)
        Q_x = (np.real(L_X / L * Q_x) + np.real(L_Y / L * Q_x)) / math.sqrt(2)
        Q_y = (np.real(Q_y * YAXIS[0]) + np.real(Q_y * YAXIS[1])) / math.sqrt(2)
        Q_z = (np.real(Q_z * ZAXIS[0]) + np.real(Q_z * ZAXIS[1])) / math.sqrt(2)

    # NOTE: 原典は node(:,sei_direction+1) を参照 (45度=Z列/135度=範囲外)。
    #       加力方向への射影 (_dir_coord) に置き換えた (意図的修正)。
    if ((node[nodej_index, 3] - node[nodei_index, 3])
            * (_dir_coord(node, nodej_index, sei_direction)
               - _dir_coord(node, nodei_index, sei_direction))
            >= 0):
        pass
    else:
        Q_x = Q_x * -1
    if node[nodej_index, 3] > node[nodei_index, 3]:
        pass
    else:
        Q_y = Q_y * -1
        Q_z = Q_z * -1
    return float(Q_x + Q_y + Q_z)


def cb_shear_pick(stress, nodei_index, nodej_index, node, axis, angle):
    """斜め柱・ブレース材のせん断力を水平力方向へ射影 (cb_shear_pick.m).

    引数:
      stress      : beam_stress の3行 (3x8)
      nodei_index/nodej_index : node 内の行index (0-based)
      axis        : 1+sei_direction (2=X/3=Y/4=45度/5=135度)
      angle       : β角 (度)

    NOTE: 原典冒頭で shear_direction を組み立てるが以降未使用 (原典ママ)。
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    sei_direction = axis - 1
    _shear_direction4(sei_direction)  # 原典ママ: 組み立てるが未使用
    return _cb_shear_core(stress, nodei_index, nodej_index, node,
                          sei_direction, angle)


# ---------------------------------------------------------------------------
# shear_pick/wall_shear_plot.m
# ---------------------------------------------------------------------------

def wall_shear_plot(ax, wall_stress_ID, wall_stress, wall_element, node, axis,
                    load_case, Q_ALL, fontsize):
    """壁のせん断力値と分担率を平面図へ注記する (wall_shear_plot.m).

    引数は wall_shear_pick と同じ + Q_ALL (層の総せん断力), fontsize。
    NOTE: せん断力は WQ_Z*qz_cos + WQ_Y*qy_cos (pick版は減算、原典ママ)。
    """
    frac_part = '%15.2f'
    wall_stress_ID = np.atleast_2d(np.asarray(wall_stress_ID, dtype=float))
    wall_stress = np.atleast_2d(np.asarray(wall_stress, dtype=float))
    sei_direction = axis - 1
    shear_direction = _shear_direction3(sei_direction)

    for i in range(wall_stress_ID.shape[0]):
        if wall_stress_ID[i, 2] == load_case:
            WQ_Z = wall_stress[2 * i + 1, 2]  # 耐力壁脚のせん断力
            WQ_Y = wall_stress[2 * i + 1, 1]
            select_wall, stress_ID = _wallinfo_get(wall_element,
                                                   wall_stress_ID[i, 0:2])
            if np.size(stress_ID) == 0:
                print('ERROR:応力に対応する要素判定ミス')
                raise RuntimeError('原典stop: 応力に対応する要素判定ミス '
                                   '(wall_shear_plot)')
            sid = np.asarray(stress_ID, dtype=int)
            # MATLAB: wall_element(stress_ID,3:6)/(stress_ID,9:10) の線形添字
            Node = wall_element[sid, 2:6].ravel(order='F')
            wall_vec = wall_element[sid, 8:10].ravel(order='F')
            wall_normal = np.array([-wall_vec[1], wall_vec[0]])
            if wall_vec[0] == 0:
                rot = 90.0
            else:
                rot = math.degrees(math.atan(wall_vec[1] / wall_vec[0]))
            qz_cos = (shear_direction[0] * wall_vec[0]
                      + shear_direction[1] * wall_vec[1])
            qy_cos = (shear_direction[0] * wall_normal[0]
                      + shear_direction[1] * wall_normal[1])

            WQ = WQ_Z * qz_cos + WQ_Y * qy_cos

            Node_index = find_index(node[:, 0], Node)
            w_ID = wall_stress_ID[i, 1]

            ax.plot([node[Node_index[0], 1], node[Node_index[1], 1]],
                    [node[Node_index[0], 2], node[Node_index[1], 2]],
                    color='k', linewidth=0.5)
            _ax_text(ax,
                     node[Node_index[0], 1] * 0.5 + node[Node_index[1], 1] * 0.5,
                     node[Node_index[0], 2] * 0.5 + node[Node_index[1], 2] * 0.5,
                     '壁ID：' + _n(w_ID, '%15.0f') + '[' + _n(WQ, frac_part)
                     + 'kN(WQ)：' + _n(100 * WQ / Q_ALL, '%15.1f') + '%]',
                     ha='center', va='top', fs=fontsize, rot=rot)


# ---------------------------------------------------------------------------
# shear_pick/truss_shear_plot.m
# ---------------------------------------------------------------------------

def truss_shear_plot(ax, stress, nodei, nodej, node_info, axis, Q_ALL,
                     fontsize):
    """トラス(ブレース)材のせん断力値と分担率を注記する (truss_shear_plot.m)."""
    frac_part = '%15.1f'
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    sei_direction = axis - 1
    shear_direction = _shear_direction3(sei_direction)

    truss_vec = np.array([
        abs(node_info[nodei, 1] - node_info[nodej, 1]),
        abs(node_info[nodei, 2] - node_info[nodej, 2])])
    truss_vec = truss_vec / np.linalg.norm(truss_vec)
    if truss_vec[1] == 0:
        rot = -90.0
    else:
        rot = math.degrees(math.atan(truss_vec[0] / truss_vec[1]))

    # せん断力表示のための準備
    XY = math.sqrt((node_info[nodei, 1] - node_info[nodej, 1]) ** 2
                   + (node_info[nodei, 2] - node_info[nodej, 2]) ** 2)
    Z = abs(node_info[nodei, 3] - node_info[nodej, 3])
    cos_ = XY / math.sqrt(XY ** 2 + Z ** 2)
    q_cos = (shear_direction[0] * truss_vec[0]
             + shear_direction[1] * truss_vec[1])
    if q_cos == 0:
        return
    mx = node_info[nodei, 1] * 0.5 + node_info[nodej, 1] * 0.5
    my = node_info[nodei, 2] * 0.5 + node_info[nodej, 2] * 0.5
    if (_dir_coord(node_info, nodei, sei_direction)
            < _dir_coord(node_info, nodej, sei_direction)):
        if node_info[nodei, 3] < node_info[nodej, 3]:
            _ax_text(ax, mx, my,
                     '(br.Qr)' + _n(q_cos * cos_ * stress[0, 3], frac_part)
                     + '\n' + '分担率：'
                     + _n(100 * q_cos * cos_ * stress[0, 3] / Q_ALL, '%15.1f')
                     + '%',
                     ha='left', va='bottom', fs=fontsize, rot=rot)
        else:
            _ax_text(ax, mx, my,
                     '(br.Ql)' + _n(-1 * q_cos * cos_ * stress[0, 3], frac_part)
                     + '\n' + '分担率：'
                     + _n(100 * -1 * q_cos * cos_ * stress[0, 3] / Q_ALL,
                          '%15.1f') + '%',
                     ha='right', va='top', fs=fontsize, rot=rot)
    else:
        if node_info[nodej, 3] < node_info[nodei, 3]:
            _ax_text(ax, mx, my,
                     '(br.Qr)' + _n(q_cos * cos_ * stress[0, 3], frac_part)
                     + '\n' + '分担率：'
                     + _n(100 * q_cos * cos_ * stress[0, 3] / Q_ALL, '%15.1f')
                     + '%',
                     ha='left', va='top', fs=fontsize, rot=rot)
        else:
            _ax_text(ax, mx, my,
                     '(br.Ql)' + _n(-1 * q_cos * cos_ * stress[0, 3], frac_part)
                     + '\n' + '分担率：'
                     + _n(100 * -1 * q_cos * cos_ * stress[0, 3] / Q_ALL,
                          '%15.1f') + '%',
                     ha='right', va='bottom', fs=fontsize, rot=rot)


# ---------------------------------------------------------------------------
# shear_pick/column_shear_plot.m
# ---------------------------------------------------------------------------

def column_shear_plot(ax, stress, nodei, nodej, node_info, axis, angle, Q_ALL,
                      fontsize):
    """柱脚のせん断力値と分担率を注記する (column_shear_plot.m)."""
    frac_part = '%15.1f'
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    ele_no = stress[0, 0]
    # β角度の調整
    if 0 <= angle and angle <= 360:
        pass
    elif angle < 0:
        while angle < 0:
            angle = angle + 360
    elif angle > 360:
        while angle > 360:
            angle = angle - 360

    sei_direction = axis - 1
    shear_direction = _shear_direction3(sei_direction)

    # 基本的にi端を下とする．
    if node_info[nodei, 3] > node_info[nodej, 3]:
        ax.plot(node_info[nodej, 1], node_info[nodej, 2], 'rx', markersize=2)
        q_reverse = -1
        angle = -1 * angle
        qz_shear = (_cosd(angle) * shear_direction[0]
                    + _sind(angle) * shear_direction[1])
        qy_shear = (_cosd(angle + 90) * shear_direction[0]
                    + _sind(angle + 90) * shear_direction[1])
    elif node_info[nodei, 3] < node_info[nodej, 3]:
        ax.plot(node_info[nodei, 1], node_info[nodei, 2], 'rx', markersize=2)
        q_reverse = 1
        qz_shear = (_cosd(angle) * shear_direction[0]
                    + _sind(angle) * shear_direction[1])
        qy_shear = (_cosd(angle - 90) * shear_direction[0]
                    + _sind(angle - 90) * shear_direction[1])
    else:
        print('柱部材の定義ミス？')
        raise RuntimeError('原典stop: 柱部材の定義ミス？ (column_shear_plot)')
    q_stress = (stress[:, 4] * qz_shear + stress[:, 3] * qy_shear) * q_reverse

    # 柱の場合β角度は強軸がX方向から何度回転しているかを示す．
    # せん断力
    if node_info[nodei, 3] < node_info[nodej, 3]:
        if abs(q_stress[0]) < 0.1:
            pass
        else:
            color = 'k' if q_stress[0] > 0 else 'red'
            _ax_text(ax, node_info[nodei, 1], node_info[nodei, 2],
                     '要素：' + _n(ele_no, '%15.0f') + '\n'
                     + '[' + _n(q_stress[0], frac_part) + 'kN(Q):'
                     + _n(100 * q_stress[0] / Q_ALL, '%15.1f') + '%]',
                     ha='left', va='top', fs=fontsize, rot=45, color=color)
    elif node_info[nodei, 3] > node_info[nodej, 3]:
        if abs(q_stress[2]) < 0.1:
            pass
        else:
            color = 'k' if q_stress[2] > 0 else 'red'
            _ax_text(ax, node_info[nodej, 1], node_info[nodej, 2],
                     '要素：' + _n(ele_no, '%15.0f') + '\n'
                     + '[' + _n(q_stress[2], frac_part) + 'kN(Q):'
                     + _n(100 * q_stress[2] / Q_ALL, '%15.1f') + '%]',
                     ha='left', va='top', fs=fontsize, rot=45, color=color)


# ---------------------------------------------------------------------------
# shear_pick/cb_shear_plot.m
# ---------------------------------------------------------------------------

def cb_shear_plot(ax, stress, nodei_index, nodej_index, node, axis, angle,
                  Q_ALL, fontsize):
    """斜め柱・ブレース材のせん断力値と分担率を注記する (cb_shear_plot.m).

    NOTE: 原典冒頭で shear_direction (3分岐版) を組み立てるが以降未使用。
    """
    frac_part = '%15.1f'
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    ele_no = stress[0, 0]
    sei_direction = axis - 1
    _shear_direction3(sei_direction)  # 原典ママ: 組み立てるが未使用

    c_shear_i = _cb_shear_core(stress, nodei_index, nodej_index, node,
                               sei_direction, angle)

    ax.plot(node[nodej_index, 1], node[nodej_index, 2], 'rx', markersize=2)
    ax.plot(node[nodei_index, 1], node[nodei_index, 2], 'rx', markersize=2)
    ax.plot([node[nodei_index, 1], node[nodej_index, 1]],
            [node[nodei_index, 2], node[nodej_index, 2]],
            linestyle='-', color='k', linewidth=0.1)

    if abs(c_shear_i) < 0.1:
        pass
    else:
        color = 'k' if c_shear_i > 0 else 'red'
        _ax_text(ax,
                 node[nodei_index, 1] * 0.5 + node[nodej_index, 1] * 0.5,
                 node[nodei_index, 2] * 0.5 + node[nodej_index, 2] * 0.5,
                 '要素：' + _n(ele_no, '%15.0f') + '\n'
                 + '[' + _n(c_shear_i, frac_part) + 'kN(Q):'
                 + _n(100 * c_shear_i / Q_ALL, '%15.1f') + '%]',
                 ha='left', va='middle', fs=fontsize, rot=0, color=color)


# ---------------------------------------------------------------------------
# figure_shear.m 内部ヘルパー
# ---------------------------------------------------------------------------

def _pick_link_upper(element, node, top_node, cut_height):
    """figure_shear.m 79-110/116-147行: 弾性連結の上にある鉛直要素を探す.

    top_node に接続する要素のうち、他端が cut_height より上にあるものを
    [要素番号, top_node, 他端節点番号] で返す (原典は同一コードを2回展開)。
    """
    element_id_onlink = np.concatenate([
        find_zero_index(element[:, 3] - top_node),   # i端
        find_zero_index(element[:, 4] - top_node)])  # j端
    if element_id_onlink.size == 0:
        return np.zeros((0, 3))
    element_no_onlink = np.zeros((element_id_onlink.size, 3))
    element_no_onlink[:, 0] = element[element_id_onlink, 0]
    for ll in range(element_id_onlink.size):
        if element[element_id_onlink[ll], 3] == top_node:  # i端が算定高さにある
            end_node_id = find_index(node[:, 0],
                                     element[element_id_onlink[ll], 4])  # j端
            if node[end_node_id, 3] <= cut_height:
                element_no_onlink[ll, 1] = 0
                element_no_onlink[ll, 2] = 0
            else:
                element_no_onlink[ll, 1] = top_node
                element_no_onlink[ll, 2] = node[end_node_id, 0]
        elif element[element_id_onlink[ll], 4] == top_node:  # j端が算定高さにある
            end_node_id = find_index(node[:, 0],
                                     element[element_id_onlink[ll], 3])
            if node[end_node_id, 3] <= cut_height:
                element_no_onlink[ll, 1] = 0
                element_no_onlink[ll, 2] = 0
            else:
                element_no_onlink[ll, 1] = top_node
                element_no_onlink[ll, 2] = node[end_node_id, 0]
    # 水平部材は消去
    cut_id = find_zero_index(element_no_onlink[:, 1])
    element_no_onlink = np.delete(element_no_onlink, cut_id, axis=0)
    return element_no_onlink


def _qsum_add(Q_SUM, c_shear_i, section_no,
              c_section_no, w_section_no, v_section_no):
    """figure_shear.m 333-370行: 断面番号分類によるQ_SUMへの加算.

    原典は柱分岐・斜め柱分岐に同一コードを2回展開しているため共通化。
    """
    if _empty(w_section_no):
        pass
    else:
        if find_index(w_section_no, section_no) >= 0:  # MATLAB: >0 (発見)
            Q_SUM[0] = Q_SUM[0] + c_shear_i  # 壁
    if _empty(v_section_no):
        pass
    else:
        if find_index(v_section_no, section_no) >= 0:
            Q_SUM[1] = Q_SUM[1] + c_shear_i  # ブレース
    if _empty(c_section_no):
        pass
    else:
        if find_index(c_section_no, section_no) >= 0:
            Q_SUM[2] = Q_SUM[2] + c_shear_i  # 柱


# ---------------------------------------------------------------------------
# figure_shear.m
# ---------------------------------------------------------------------------

def figure_shear(limit_sec_no, stress_fontsize, node,
                 wall_stress_ID, wall_stress, wall_element,
                 beam_stress, truss_stress, element,
                 mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
                 line_location, title_fontsize,
                 load_case_index_beam, load_case_index_truss,
                 load_case_index_wall, load_case_no_wall, load_case_name,
                 paper_orient, paper_size, case_S, z_point,
                 link, sections, case_height,
                 c_section_no, g_section_no, w_section_no, v_section_no,
                 sei_direction, out_dir, QR_TeX_txt=None, fig_no=1):
    """水平荷重時・せん断力分担図を作成する (figure_shear.m).

    1荷重ケース×1レベルセット(case_heightの1要素)=PDF1枚を out_dir へ保存。

    引数 (MIDAS_QRplot_fig.m 624-631行の呼出しに対応):
      limit_sec_no : ダミー材とみなす断面番号の下限 (これ以上は計算対象外)
      stress_fontsize : せん断力値注記のフォントサイズ
      node    : mgtopen_node の節点表 [節点番号, X, Y, Z, ...] (m)
      wall_stress_ID : 壁応力ヘッダ (Nx3) [ベースレベル, 壁ID, 荷重ケース]
      wall_stress    : 壁応力 (2Nx6) 整形後 [N,Qy,Qz,My,Mz,...] 2行/壁
      wall_element   : mgtopen_wall の壁要素表 (Nx10)
                       [ベースレベル, 壁ID, N1..N4, 材料, 厚さ, 方向x, 方向y]
      beam_stress    : 梁要素応力 (Nx8) [要素,ケース,Fx,Fy,Fz,Mx,My,Mz] 3行/要素
      truss_stress   : トラス要素応力 (Nx4) [要素,ケース,Fx(i),Fx(j)]
      element : mgtopen_element の要素表 [要素番号,材料,断面,節点i,節点j,β,...]
      mergin_LEFT/RIGHT/TOP/BOTTOM : 図枠の余白 (モデル座標, m)
      line_location  : 分担率テキストの図枠からのオフセット係数
      title_fontsize : タイトルのフォントサイズ (分担率テキストは -2)
      load_case_index_beam/truss/wall : 各応力表の荷重ケース先頭行index
                       (Python版 find_index の 0-based 値)
      load_case_no_wall : 壁応力の荷重ケース番号一覧 (doublecheck済)
      load_case_name : 荷重ケース名 list[str]
      paper_orient : 1=横 その他=縦 / paper_size : 1=A1..4=A4
      case_S  : せん断力分担図を作成する荷重ケース (1-based index の list、
                load_case_name への添字)
      z_point : 柱脚・壁ベースのZ座標一覧 (m) 昇順 (doublecheck済)。
                原典 MIDAS_QRplot_fig.m 578-601行で柱要素の下端Z座標と
                壁ベースレベルから作成される。
      link    : mgtopen_elasticlink の弾性連結表 [番号?, 節点i, 節点j, ...]
                (2-3列目の節点対を参照)。無ければ空。
      sections: mgtopen_section の断面表 (sectionget用)
      case_height : list[list[int]]。1要素=1枚の図で集計する支点レベルの
                    組で、中身は z_point への 1-based 添字
                    (原典listdlgの選択index)。原典cellの最終空要素は
                    含めない (size(case_height,2)-1 → len(case_height))。
      c_section_no/g_section_no/w_section_no/v_section_no :
                    断面番号の分類 (柱/梁/壁/ブレース)。鉛直材のせん断力を
                    断面番号でQ_SUM(壁/ブレース/柱)へ振り分ける。
                    原典 MIDAS_QRplot_fig.m 494-564行で listdlg により
                    断面リストから選択される。g_section_no は原典でも
                    figure_shear 内では未使用。
      sei_direction : ケースごとの水平力方向 list (len(case_S))。
                      1=X方向 / 2=Y方向 / 3=45度方向 / 4=135度方向
                      (原典の listdlg('水平力方向') の置換)
      out_dir : PDF保存先ディレクトリ
      QR_TeX_txt : 既存のTeX表行 list[str] (継続呼出し用)。None なら新規。
      fig_no  : PDF連番の開始値 (原典の fig_no 引数)

    戻り値: (pdf_paths, QR_TeX_txt, Q_c, Q_w, fig_no)
      pdf_paths : 保存したPDFパスの list[str]
      QR_TeX_txt: 層別TeX表行の list[str] (入力に追記)
      Q_c : (len(case_S) x len(case_height)) 柱のせん断力 Q_SUM(3)
      Q_w : 同上、壁+ブレースのせん断力 Q_SUM(1)+Q_SUM(2)
            (原典はケースごとに上書きされる作業配列)
      fig_no : 次の連番
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    z_point = np.asarray(z_point, dtype=float).ravel()
    pdf_paths = []
    QR_TeX_txt = [] if QR_TeX_txt is None else list(QR_TeX_txt)

    n_ele = element.shape[0] if element.size else 0
    c_b_result = column_beam_judge(np.arange(n_ele), element, node)

    num_height = len(case_height)  # 原典: size(case_height,2)-1 の読替え
    Q_c = np.zeros(num_height)
    Q_w = np.zeros(num_height)
    Q_c_all = np.zeros((len(case_S), num_height))
    Q_w_all = np.zeros((len(case_S), num_height))

    for i_lcase in range(len(case_S)):
        cs = int(case_S[i_lcase])           # 1-based (load_case_name への添字)
        sd_case = int(sei_direction[i_lcase])
        case_name = load_case_name[cs - 1]
        for i_height0 in range(num_height):
            i_height = i_height0 + 1        # 原典の1-based i_height
            collect_node = np.zeros((0, 3))
            ch = np.asarray(case_height[i_height0], dtype=int)  # 1-based
            z1_point = z_point[ch - 1]
            # plotするfigureの設定
            fig, ax = plt.subplots()
            axis_xtick = []
            axis_ytick = []
            # その高さにある節点を抽出
            Q_SUM = np.zeros(3)  # 壁・トラス・柱　の順序
            Q_ALL = 0.0
            w_ratio = br_ratio = c_ratio = float('nan')
            if _empty(truss_stress):
                truss_ele_no = np.array([])
            else:
                truss_ele_no = doublecheck(truss_stress[:, 0])
            for base_i in range(z1_point.size):
                z1b = float(z1_point[base_i])
                node_id_on = find_zero_index(node[:, 3] - z1b)
                node_no_on = node[node_id_on, 0]
                axis_xtick.extend(node[node_id_on, 1].tolist())
                axis_ytick.extend(node[node_id_on, 2].tolist())

                for j in range(node_no_on.size):
                    # その節点にある鉛直部材/ブレース材を抽出
                    element_id_on = np.concatenate([
                        find_zero_index(element[:, 3] - node_no_on[j]),   # i端
                        find_zero_index(element[:, 4] - node_no_on[j])])  # j端
                    element_no_on = element[element_id_on, 0] \
                        if element_id_on.size else np.array([])

                    # その節点にある部材が算定対象となる鉛直部材かどうかを判定
                    if element_no_on.size == 0:
                        continue
                    eno = np.zeros((element_no_on.size, 3))
                    eno[:, 0] = element_no_on
                    for jj in range(eno.shape[0]):
                        if element[element_id_on[jj], 3] == node_no_on[j]:
                            # i端が算定高さにある
                            end_node_id = find_index(
                                node[:, 0], element[element_id_on[jj], 4])
                            if node[end_node_id, 3] <= z1b:
                                eno[jj, 1] = 0
                                eno[jj, 2] = 0
                            else:
                                eno[jj, 1] = node_no_on[j]
                                eno[jj, 2] = node[end_node_id, 0]
                        elif element[element_id_on[jj], 4] == node_no_on[j]:
                            # j端が算定高さにある
                            end_node_id = find_index(
                                node[:, 0], element[element_id_on[jj], 3])
                            if node[end_node_id, 3] <= z1b:
                                eno[jj, 1] = 0
                                eno[jj, 2] = 0
                            else:
                                eno[jj, 1] = node_no_on[j]
                                eno[jj, 2] = node[end_node_id, 0]
                    # 水平部材は消去
                    cut_id = find_zero_index(eno[:, 1])
                    eno = np.delete(eno, cut_id, axis=0)

                    # 弾性連結要素の場合には上の要素を探す
                    add_element_no_on = np.zeros((0, 3))
                    for jj in range(eno.shape[0]):
                        element_no_onlink = np.zeros((0, 3))
                        cut_height = node[
                            find_index(node[:, 0], eno[jj, 2]), 3]
                        if _empty(link):
                            pass
                        else:
                            li = find_index(link[:, 1], node_no_on[j])
                            if li >= 0:  # MATLAB: find_index(...)>0
                                if link[li, 2] == eno[jj, 2]:
                                    element_no_onlink = _pick_link_upper(
                                        element, node, eno[jj, 2], cut_height)
                            li = find_index(link[:, 2], node_no_on[j])
                            if li >= 0:
                                if link[li, 1] == eno[jj, 2]:
                                    element_no_onlink = _pick_link_upper(
                                        element, node, eno[jj, 2], cut_height)
                        if element_no_onlink.shape[0] == 0:
                            pass
                        else:
                            if element_no_onlink.shape[0] == 1:
                                eno[jj, :] = element_no_onlink[0, :]
                            else:
                                i_link = 0  # NOTE: 原典159-166行は i_link=1
                                #       固定で先頭行しか判定しない (原典ママ)
                                for i_cycle in range(
                                        element_no_onlink.shape[0]):
                                    cut_check = sectionget(
                                        element_no_onlink[i_link, 0],
                                        element, sections, limit_sec_no)
                                    if cut_check[0] >= limit_sec_no:
                                        element_no_onlink[i_link, 1] = 0
                                cut_id = find_zero_index(
                                    element_no_onlink[:, 1])
                                element_no_onlink = np.delete(
                                    element_no_onlink, cut_id, axis=0)
                                eno[jj, :] = element_no_onlink[0, :]
                                if element_no_onlink.shape[0] > 1:
                                    add_element_no_on = np.vstack([
                                        add_element_no_on,
                                        element_no_onlink[1:, :]])
                    eno = np.vstack([eno, add_element_no_on])
                    if eno.shape[0] == 0:
                        pass
                    else:
                        for jj in range(eno.shape[0]):
                            # 層間変形角を計算する相手の節点を探索
                            end_node_no = eno[jj, 2]  # 直結部材の他方の節点番号
                            collect_node = np.vstack([
                                collect_node,
                                [eno[jj, 0], eno[jj, 1], end_node_no]])

                # 重複要素を削除
                if collect_node.shape[0]:
                    collect_node = doublecheck2(collect_node)

                # ------------- 支点レベルにある梁要素のプロット -------------
                if _empty(element):
                    pass
                else:
                    for i_beam in range(element.shape[0]):
                        inode_index, jnode_index = node_index_get(
                            element[i_beam, 0], element, node)
                        ele_no = element[i_beam, 0]
                        # NOTE: 原典205-206行は両方とも inode を参照
                        #       (j端のZ座標を見ていない)。原典ママ。
                        z1_inode = min(node[inode_index, 3],
                                       node[inode_index, 3])
                        z2_inode = max(node[inode_index, 3],
                                       node[inode_index, 3])
                        if z1_inode == z1b and z2_inode == z1b:
                            element_index = find_index(element[:, 0], ele_no)
                            section_no = element[element_index, 2]
                            ls = '--' if limit_sec_no <= section_no else '-'
                            ax.plot([node[inode_index, 1],
                                     node[jnode_index, 1]],
                                    [node[inode_index, 2],
                                     node[jnode_index, 2]],
                                    linestyle=ls, color='k', linewidth=0.1)

                # ------------- 支点レベルにある壁要素のプロット -------------
                n_wall = wall_element.shape[0] if np.size(wall_element) else 0
                for i_wall_on in range(n_wall):
                    if wall_element[i_wall_on, 0] >= z1b:
                        node_index = find_index(
                            node[:, 0], wall_element[i_wall_on, 2:4])
                        ax.plot(node[node_index[0], 1], node[node_index[0], 2],
                                'kx', markersize=2)
                        ax.plot(node[node_index[1], 1], node[node_index[1], 2],
                                'kx', markersize=2)

                # ------------------- せん断力値の収集 -----------------------
                # %%壁%%
                if _empty(wall_element):
                    pass
                else:
                    if cs == np.size(load_case_index_wall):
                        cut_index = np.arange(
                            int(load_case_index_wall[cs - 1]),
                            wall_stress_ID.shape[0])
                    else:
                        cut_index = np.arange(
                            int(load_case_index_wall[cs - 1]),
                            int(load_case_index_wall[cs]))
                    current_wall_stress_ID = wall_stress_ID[cut_index, :]

                    for i_wall_shear in range(
                            current_wall_stress_ID.shape[0]):
                        pick_ID = []
                        if current_wall_stress_ID[i_wall_shear, 0] >= z1b:
                            for j2 in range(current_wall_stress_ID.shape[0]):
                                check_height = 0
                                check_ID = 0
                                # NOTE: 原典260/263行は wall_element を
                                #       応力行番号 j で添字参照する (原典ママ)
                                if (wall_element[j2, 0]
                                        == current_wall_stress_ID[
                                            i_wall_shear, 0]):
                                    check_height = 1
                                if (wall_element[j2, 1]
                                        == current_wall_stress_ID[
                                            i_wall_shear, 1]):
                                    check_ID = 1
                                if check_height == 1 and check_ID == 1:
                                    pick_ID.append(i_wall_shear)  # 0-based
                                    if len(pick_ID) > 1:
                                        raise RuntimeError(
                                            '原典stop: 壁応力の対応行が'
                                            '複数 (figure_shear)')
                        if not pick_ID:
                            pass
                        else:
                            pick_ID2 = (np.asarray(pick_ID)
                                        + int(load_case_index_wall[cs - 1]))
                            w_shear_i = wall_shear_pick(
                                current_wall_stress_ID[
                                    i_wall_shear:i_wall_shear + 1, :],
                                wall_stress[2 * int(pick_ID2.min()):
                                            2 * int(pick_ID2.max()) + 2, :],
                                wall_element, node, 1 + sd_case,
                                load_case_no_wall[cs - 1])
                            Q_SUM[0] = Q_SUM[0] + w_shear_i

                # %%トラス%%
                if _empty(truss_stress):
                    pass
                else:
                    for i_truss in range(truss_ele_no.size):
                        inode_index, jnode_index = node_index_get(
                            truss_ele_no[i_truss], element, node)
                        if (min(node[inode_index, 3], node[jnode_index, 3])
                                == z1b
                                and max(node[inode_index, 3],
                                        node[jnode_index, 3]) > z1b):
                            tr_st_index = find_index(
                                truss_stress[:, 0], truss_ele_no[i_truss])
                            r = tr_st_index + int(
                                load_case_index_truss[cs - 1])
                            plot_stress = truss_stress[r:r + 1, 0:4]
                            t_shear_i = truss_shear_pick(
                                plot_stress, inode_index, jnode_index, node,
                                1 + sd_case)
                            Q_SUM[1] = Q_SUM[1] + t_shear_i

                # %%＜梁要素＞%%
                if _empty(beam_stress):
                    pass
                else:
                    for i_cb in range(collect_node.shape[0]):  # 柱と判断されたもの
                        ele_no = collect_node[i_cb, 0]
                        inode_index, jnode_index = node_index_get(
                            ele_no, element, node)
                        ele_index = find_index(element[:, 0], ele_no)
                        section_no = element[ele_index, 2]
                        if limit_sec_no <= section_no:
                            pass
                        else:
                            ele_st_index = find_index(
                                beam_stress[:, 0], ele_no)
                            if ele_st_index == -1:  # MATLAB: ==0
                                if _empty(truss_stress):
                                    print('柱梁と認識された要素の応力ファイルなし')
                                    print('ele_no=')
                                    print(ele_no)
                                else:
                                    truss_st_index = find_index(
                                        truss_stress[:, 0], ele_no)
                                    if truss_st_index == -1:
                                        print('柱梁と認識された要素の'
                                              '応力ファイルなし')
                                        print('ele_no=')
                                        print(ele_no)
                            else:
                                angle = [c_b_result[ele_index],
                                         element[ele_index, 5]]
                                off = int(load_case_index_beam[cs - 1])
                                plot_stress = beam_stress[
                                    ele_st_index + off:
                                    ele_st_index + off + 3, 0:8]
                                if angle[0] == 2:  # 完全な柱
                                    c_shear_i = column_shear_pick(
                                        plot_stress, inode_index, jnode_index,
                                        node, 1 + sd_case, angle[1])
                                else:  # 斜めの柱と梁
                                    c_shear_i = cb_shear_pick(
                                        plot_stress, inode_index, jnode_index,
                                        node, 1 + sd_case, angle[1])
                                # 原典は両分岐に同一の振り分けコードを展開
                                _qsum_add(Q_SUM, c_shear_i, section_no,
                                          c_section_no, w_section_no,
                                          v_section_no)

                # ---------- せん断力分担率，層せん断力値の集計 ----------
                Q_ALL = float(np.sum(Q_SUM))
                w_ratio = Q_SUM[0] / Q_ALL * 100
                br_ratio = Q_SUM[1] / Q_ALL * 100
                c_ratio = Q_SUM[2] / Q_ALL * 100

                # ---------------- 柱のせん断力値のplot ----------------
                if _empty(element):
                    pass
                else:
                    for i_cb in range(collect_node.shape[0]):
                        ele_no = collect_node[i_cb, 0]
                        inode_index, jnode_index = node_index_get(
                            ele_no, element, node)
                        ele_index = find_index(element[:, 0], ele_no)
                        section_no = element[ele_index, 2]
                        if limit_sec_no <= section_no:
                            pass
                        else:
                            ele_st_index = find_index(
                                beam_stress[:, 0], ele_no)
                            if ele_st_index == -1:
                                pass
                            else:
                                angle = [c_b_result[ele_index],
                                         element[ele_index, 5]]
                                off = int(load_case_index_beam[cs - 1])
                                plot_stress = beam_stress[
                                    ele_st_index + off:
                                    ele_st_index + off + 3, 0:8]
                                if angle[0] == 2:  # 完全な柱
                                    column_shear_plot(
                                        ax, plot_stress, inode_index,
                                        jnode_index, node, 1 + sd_case,
                                        angle[1], Q_ALL, stress_fontsize)
                                else:  # 斜めの柱と梁
                                    cb_shear_plot(
                                        ax, plot_stress, inode_index,
                                        jnode_index, node, 1 + sd_case,
                                        angle[1], Q_ALL, stress_fontsize)

                # ---------------- 壁のせん断力値のplot ----------------
                if _empty(wall_element):
                    pass
                else:
                    if cs == np.size(load_case_index_wall):
                        cut_index = np.arange(
                            int(load_case_index_wall[cs - 1]),
                            wall_stress_ID.shape[0])
                    else:
                        cut_index = np.arange(
                            int(load_case_index_wall[cs - 1]),
                            int(load_case_index_wall[cs]))
                    current_wall_stress_ID = wall_stress_ID[cut_index, :]

                    for i_wall_shear in range(
                            current_wall_stress_ID.shape[0]):
                        pick_ID = []
                        if current_wall_stress_ID[i_wall_shear, 0] >= z1b:
                            for j2 in range(current_wall_stress_ID.shape[0]):
                                check_height = 0
                                check_ID = 0
                                if (wall_element[j2, 0]
                                        == current_wall_stress_ID[
                                            i_wall_shear, 0]):
                                    check_height = 1
                                if (wall_element[j2, 1]
                                        == current_wall_stress_ID[
                                            i_wall_shear, 1]):
                                    check_ID = 1
                                if check_height == 1 and check_ID == 1:
                                    pick_ID.append(i_wall_shear)
                                    if len(pick_ID) > 1:
                                        raise RuntimeError(
                                            '原典stop: 壁応力の対応行が'
                                            '複数 (figure_shear)')
                        if not pick_ID:
                            pass
                        else:
                            pick_ID2 = (np.asarray(pick_ID)
                                        + int(load_case_index_wall[cs - 1]))
                            wall_shear_plot(
                                ax,
                                current_wall_stress_ID[
                                    i_wall_shear:i_wall_shear + 1, :],
                                wall_stress[2 * int(pick_ID2.min()):
                                            2 * int(pick_ID2.max()) + 2, :],
                                wall_element, node, 1 + sd_case,
                                load_case_no_wall[cs - 1], Q_ALL,
                                stress_fontsize)

                # ---------- トラスのせん断力値のplot ----------
                # 支点レベルにあるトラス要素のプロット
                if _empty(element):
                    pass
                else:
                    for i_truss in range(truss_ele_no.size):
                        inode_index, jnode_index = node_index_get(
                            truss_ele_no[i_truss], element, node)
                        if (min(node[inode_index, 3], node[jnode_index, 3])
                                == z1b
                                and max(node[inode_index, 3],
                                        node[jnode_index, 3]) > z1b):
                            ax.plot(
                                [node[inode_index, 1] * 0.8
                                 + node[jnode_index, 1] * 0.2,
                                 node[inode_index, 1] * 0.2
                                 + node[jnode_index, 1] * 0.8],
                                [node[inode_index, 2] * 0.8
                                 + node[jnode_index, 2] * 0.2,
                                 node[inode_index, 2] * 0.2
                                 + node[jnode_index, 2] * 0.8],
                                'bo-', markersize=2, linewidth=0.5)
                            tr_st_index = find_index(
                                truss_stress[:, 0], truss_ele_no[i_truss])
                            r = tr_st_index + int(
                                load_case_index_truss[cs - 1])
                            plot_stress = truss_stress[r:r + 1, 0:4]
                            truss_shear_plot(
                                ax, plot_stress, inode_index, jnode_index,
                                node, 1 + sd_case, Q_ALL, stress_fontsize)

            # ------------------ 応力図の枠の大きさを設定 ------------------
            axis_xtick = np.asarray(axis_xtick)
            axis_ytick = np.asarray(axis_ytick)
            ax.set_xticks([])
            ax.set_xlim(axis_xtick.min() - mergin_LEFT,
                        axis_xtick.max() + mergin_RIGHT)
            ax.set_yticks([])
            ax.set_ylim(axis_ytick.min() - mergin_BOTTOM,
                        axis_ytick.max() + mergin_TOP)

            # ------------------ せん断力分担率など記載 ------------------
            Q_ratio_plot = '\n'.join([
                '総せん断力：' + _n(Q_ALL, '%15.1f') + 'kN',
                'せん断力分担率',
                '壁：' + _n(w_ratio, '%15.1f') + '%　→'
                + _n(Q_SUM[0], '%15.1f') + 'kN',
                'ブレース：' + _n(br_ratio, '%15.1f') + '%　→'
                + _n(Q_SUM[1], '%15.1f') + 'kN',
                '柱：' + _n(c_ratio, '%15.1f') + '%　→'
                + _n(Q_SUM[2], '%15.1f') + 'kN'])
            Q_c[i_height0] = Q_SUM[2]
            Q_w[i_height0] = Q_SUM[0] + Q_SUM[1]

            _ax_text(ax,
                     axis_xtick.min() - mergin_LEFT + line_location * 0.1,
                     axis_ytick.max() + mergin_TOP - line_location * 0.1,
                     Q_ratio_plot, ha='left', va='top',
                     fs=title_fontsize - 2)

            # 柱間隔ならびに階高を記入する (原典: x_label 組立て、未使用)
            x_label = '支点レベル：'
            if z_point.size == 1:
                x_label = x_label + _n(z_point[0], '%15.2f') + 'm'
            else:
                x_label = (x_label + '　'
                           + _n(z_point[ch[0] - 1], '%15.2f') + 'm')

            ax.set_title('水平荷重時・せん断力分担図' + '[　' + '　荷重ケース：'
                         + case_name + '　]', fontsize=title_fontsize)
            ax.set_xlabel('表示レベル：' + _n(z_point[ch[0] - 1], '%15.2f')
                          + 'm', fontsize=title_fontsize)

            ax.set_aspect('equal', adjustable='box')  # daspect([1 1 1])
            _set_paper(fig, paper_orient, paper_size)

            out_path = os.path.join(
                out_dir, 'shear_%d_%s_L%s.pdf'
                % (fig_no, _safe_name(case_name),
                   _n(z_point[ch[0] - 1], '%15.2f')))
            fig.savefig(out_path, format='pdf')
            plt.close(fig)
            pdf_paths.append(out_path)
            fig_no = fig_no + 1

        # ------------------ 層別TeX表行の組立て ------------------
        if sd_case == 1:
            direction = 'X'
        elif sd_case == 2:
            direction = 'Y'
        elif sd_case == 3:
            direction = '45度'
        elif sd_case == 4:
            direction = '135度'
        if num_height == 1:
            ihm = 1  # MATLAB: ループ終了後の i_height (=1)
            QR_TeX_txt.append(
                '\\multirow{' + _n(ihm, '%15.0f') + '}{*}{' + direction
                + '} &' + _n(ihm, '%15.0f') + 'F &'
                + _n(Q_c[ihm - 1], '%15.1f') + ' & '
                + _n(Q_w[ihm - 1], '%15.1f') + ' & '
                + _n(Q_c[ihm - 1] + Q_w[ihm - 1], '%15.1f') + ' & '
                + _n(Q_w[ihm - 1] / (Q_c[ihm - 1] + Q_w[ihm - 1]), '%15.3f')
                + ' & '
                + _n(100 * Q_c[ihm - 1] / (Q_c[ihm - 1] + Q_w[ihm - 1]),
                     '%15.1f')
                + ' & '
                + _n(100 * Q_w[ihm - 1] / (Q_c[ihm - 1] + Q_w[ihm - 1]),
                     '%15.1f')
                + ' \\\\ \\bottomrule')
        else:
            for ihm in range(num_height, 0, -1):
                qc = Q_c[ihm - 1]
                qw = Q_w[ihm - 1]
                if ihm == num_height:
                    QR_TeX_txt.append(
                        '\\multirow{' + _n(ihm, '%15.0f') + '}{*}{'
                        + direction + '} &' + _n(ihm, '%15.0f') + 'F &'
                        + _n(qc, '%15.1f') + ' & ' + _n(qw, '%15.1f') + ' & '
                        + _n(qc + qw, '%15.1f') + ' & '
                        + _n(qw / (qc + qw), '%15.3f') + ' & '
                        + _n(100 * qc / (qc + qw), '%15.1f') + ' & '
                        + _n(100 * qw / (qc + qw), '%15.1f')
                        + ' \\\\ \\cline{2-8}')
                elif ihm == 1:
                    QR_TeX_txt.append(
                        '& 1F & ' + _n(qc, '%15.1f') + ' & '
                        + _n(qw, '%15.1f') + ' & ' + _n(qc + qw, '%15.1f')
                        + ' & ' + _n(qw / (qc + qw), '%15.3f') + ' & '
                        + _n(100 * qc / (qc + qw), '%15.1f') + ' & '
                        + _n(100 * qw / (qc + qw), '%15.1f')
                        + ' \\\\ \\bottomrule')
                else:
                    QR_TeX_txt.append(
                        '& ' + _n(ihm, '%15.0f') + 'F & ' + _n(qc, '%15.1f')
                        + ' & ' + _n(qw, '%15.1f') + ' & '
                        + _n(qc + qw, '%15.1f') + ' & '
                        + _n(qw / (qc + qw), '%15.3f') + ' & '
                        + _n(100 * qc / (qc + qw), '%15.1f') + ' & '
                        + _n(100 * qw / (qc + qw), '%15.1f')
                        + ' \\\\ \\cline{2-8}')
        Q_c_all[i_lcase, :] = Q_c
        Q_w_all[i_lcase, :] = Q_w

    return pdf_paths, QR_TeX_txt, Q_c_all, Q_w_all, fig_no


# ---------------------------------------------------------------------------
# figure_CB.m 内部ヘルパー
# ---------------------------------------------------------------------------

def _same_base_accum(qxx_stress, qyy_stress, N_C, plot_stress,
                     inode_index, jnode_index, node):
    """figure_CB.m 203-250行 (=252-296行): 柱脚に集まるブレース等の加算.

    原典は ijnode の i端/j端どちらが柱脚かで同一コードを2回展開している。
    戻り値: (qxx_stress, qyy_stress, N_C)
    """
    cb_vec = np.array([
        abs(node[inode_index, 1] - node[jnode_index, 1]),
        abs(node[inode_index, 2] - node[jnode_index, 2])])
    cb_vec = cb_vec / np.linalg.norm(cb_vec)
    XY = math.sqrt((node[inode_index, 1] - node[jnode_index, 1]) ** 2
                   + (node[inode_index, 2] - node[jnode_index, 2]) ** 2)
    Z = abs(node[inode_index, 3] - node[jnode_index, 3])
    cos_ = XY / math.sqrt(XY ** 2 + Z ** 2)  # noqa: F841 原典ママ(下では別式)
    # <X方向せん断力>
    shear_direction = [1, 0]
    q_cos = (shear_direction[0] * cb_vec[0]
             + shear_direction[1] * cb_vec[1])
    if node[inode_index, 1] < node[jnode_index, 1]:
        if node[inode_index, 3] < node[jnode_index, 3]:
            qxx_stress = qxx_stress + q_cos * cos_ * plot_stress[0, 2]
            N_C = N_C + plot_stress[0, 2] * Z / math.sqrt(XY ** 2 + Z ** 2)
        else:
            qxx_stress = qxx_stress + -1 * q_cos * cos_ * plot_stress[2, 2]
            N_C = N_C + plot_stress[2, 2] * Z / math.sqrt(XY ** 2 + Z ** 2)
    else:
        if node[jnode_index, 3] < node[inode_index, 3]:
            qxx_stress = qxx_stress + q_cos * cos_ * plot_stress[2, 2]
            N_C = N_C + plot_stress[2, 2] * Z / math.sqrt(XY ** 2 + Z ** 2)
        else:
            qxx_stress = qxx_stress + -1 * q_cos * cos_ * plot_stress[0, 2]
            N_C = N_C + plot_stress[0, 2] * Z / math.sqrt(XY ** 2 + Z ** 2)

    # <Y方向せん断力>
    shear_direction = [0, 1]
    q_cos = (shear_direction[0] * cb_vec[0]
             + shear_direction[1] * cb_vec[1])
    if node[inode_index, 2] < node[jnode_index, 2]:
        if node[inode_index, 3] < node[jnode_index, 3]:
            qyy_stress = qyy_stress + q_cos * cos_ * plot_stress[0, 2]
        else:
            qyy_stress = qyy_stress + -1 * q_cos * cos_ * plot_stress[2, 2]
    else:
        if node[jnode_index, 3] < node[inode_index, 3]:
            qyy_stress = qyy_stress + q_cos * cos_ * plot_stress[2, 2]
        else:
            qyy_stress = qyy_stress + -1 * q_cos * cos_ * plot_stress[0, 2]
    return qxx_stress, qyy_stress, N_C


# ---------------------------------------------------------------------------
# figure_CB.m
# ---------------------------------------------------------------------------

def figure_CB(limit_sec_no, stress_fontsize, node,
              wall_element, beam_stress, truss_stress, element,
              axis_name, mergin_LEFT, mergin_RIGHT, mergin_TOP, mergin_BOTTOM,
              axisname_location, line_location, dimension_fontsize,
              axis_fontsize, title_fontsize,
              load_case_index_beam, axis_ytick, axis_xtick,
              load_case_name, paper_orient, paper_size, case_CB,
              axis_plot_coefi, z_point, out_dir):
    """水平荷重時・柱脚設計用応力 RESULT の組立てと平面図 (figure_CB.m).

    1荷重ケース×1支点レベル=PDF1枚を out_dir へ保存する。

    引数 (MIDAS_QRplot_fig.m 469-475行の呼出しに対応):
      limit_sec_no : ダミー材とみなす断面番号の下限
      stress_fontsize : 柱脚応力注記のフォントサイズ
      node    : mgtopen_node の節点表 [節点番号, X, Y, Z, ...] (m)
      wall_element : mgtopen_wall の壁要素表 (Nx10)
      beam_stress  : 梁要素応力 (Nx8) [要素,ケース,Fx,Fy,Fz,Mx,My,Mz] 3行/要素
      truss_stress : トラス要素応力 (Nx4) (柱応力が無い場合の代替検索に使用)
      element : mgtopen_element の要素表 [要素番号,材料,断面,節点i,節点j,β,...]
      axis_name : 通り芯符号 list[str] (axis_plot_coefi と同順)
      mergin_* / axisname_location / line_location : 図の余白・注記位置 (m)
      dimension_fontsize / axis_fontsize / title_fontsize : フォントサイズ
      load_case_index_beam : 梁応力の荷重ケース先頭行index
                             (Python版 find_index の 0-based 値)
      axis_ytick / axis_xtick : 平面図の枠・寸法線の基準となるY/X座標一覧。
             NOTE: 原典の呼出し (MIDAS_QRplot_fig.m 474行) では反力図
             ブロックの残存変数が渡る (CBのみ実行時は未定義エラー)。
             本移植では通常 node のX/Y座標一覧を渡せばよい。
             (axis_ztick は原典 figure_CB 内で未使用のため引数から削除)
      load_case_name : 荷重ケース名 list[str]
      paper_orient : 1=横 その他=縦 / paper_size : 1=A1..4=A4
      case_CB : 柱脚設計用データを作成する荷重ケース (1-based index の list)
      axis_plot_coefi : pick_coefi の通り芯係数 (Nx3)
             [1=X通り/2=Y通り, 一次式の傾きA, 切片B]。X通りは X=AY+B、
             Y通りは Y=AX+B で、3列目(切片)を通り芯の代表座標として使う。
      z_point : 柱脚レベルのZ座標一覧 (m)。原典 MIDAS_QRplot_fig.m
             433-465行で柱要素の下端Z座標・壁ベースレベルから作成し
             listdlg で選択されたもの。
      out_dir : PDF保存先ディレクトリ

    戻り値: (RESULT, pdf_paths)
      RESULT : ndarray (Nx7) 柱脚設計用データ。列の意味 (figure_CB.m 308行):
        1: CB_node    柱脚の節点番号
        2: load_no    荷重ケース番号 (応力ファイル2列目の値)
        3: N_C        柱脚軸力 Fx [kN] (同一柱脚に集まる斜材の鉛直成分を加算)
        4: qxx_stress X方向せん断力 [kN] (柱のFy/Fzをβ角で全体X方向へ射影、
                      斜材の軸力水平成分を加算)
        5: qyy_stress Y方向せん断力 [kN] (同、全体Y方向)
        6: My         柱脚の要素座標y軸回り曲げモーメント [kN m]
        7: Mz         柱脚の要素座標z軸回り曲げモーメント [kN m]
        原典では本データを Midas_QR.m 140行の `save CB.mat` で保存していた。
        保存は統合層が行う (本関数はデータを返すのみ)。
      pdf_paths : 保存したPDFパスの list[str]

    NOTE (原典ママの疑義):
      - i端が下の柱の qyy_stress は plot_stress(3,:) (j端行) と angle+90 を
        使う (qxx は i端行と angle-90。コピーミスと思われるが原典どおり)。
      - 斜め柱分岐 (angle(1)==1.9) は c_index が完全柱(=2)のみのため
        実行されない死にコード。実行されると N_C/My/Mz 未定義で原典同様
        エラーとなる (Python では NameError)。
      - 同一柱脚に集まる要素の応力検索 find_index(beam_stress(:,1),...) が
        未発見 (=トラス等) の場合、原典は行番号0起点の誤参照
        (荷重ケースによってはエラー) となる。Python では負のindexとなり
        別行を参照する (いずれも不定動作、原典の癖)。
    """
    _setup_japanese_font()
    os.makedirs(out_dir, exist_ok=True)
    node = np.asarray(node, dtype=float)
    element = np.asarray(element, dtype=float)
    z_point = np.asarray(z_point, dtype=float).ravel()
    axis_xtick = np.asarray(axis_xtick, dtype=float).ravel()
    axis_ytick = np.asarray(axis_ytick, dtype=float).ravel()
    axis_plot_coefi = np.asarray(axis_plot_coefi, dtype=float)

    fig_no = 1
    pdf_paths = []
    RESULT = np.zeros((0, 7))
    frac_part = '%15.1f'
    if _empty(element):
        c_index = np.array([], dtype=int)
        b_index = np.array([], dtype=int)
        c_b_result = np.array([])
    else:
        c_b_result = column_beam_judge(
            np.arange(element.shape[0]), element, node)
        c_index = find_zero_index(c_b_result - 2)  # 柱＋ななめの柱
        c_index = np.asarray(c_index, dtype=int)
        b_index = find_zero_index(c_b_result - 1)  # 梁と斜めの柱
        b_index = np.asarray(b_index, dtype=int)

    for i_lcase in range(len(case_CB)):
        cs = int(case_CB[i_lcase])  # 1-based
        case_name = load_case_name[cs - 1]
        for i_height in range(z_point.size):
            fig, ax = plt.subplots()

            # ---------- 支点レベルにある梁要素のプロット ----------
            if _empty(element):
                pass
            else:
                for i_beam in range(b_index.size):
                    inode_index, jnode_index = node_index_get(
                        element[b_index[i_beam], 0], element, node)
                    ele_no = element[b_index[i_beam], 0]
                    z_inode = node[inode_index, 3]
                    if z_inode == z_point[i_height]:
                        element_index = find_index(element[:, 0], ele_no)
                        section_no = element[element_index, 2]
                        if limit_sec_no <= section_no:
                            pass
                        else:
                            ax.plot([node[inode_index, 1],
                                     node[jnode_index, 1]],
                                    [node[inode_index, 2],
                                     node[jnode_index, 2]],
                                    linestyle='-', color='k', linewidth=0.25)

            # ---------- 支点レベルにある壁要素のプロット ----------
            n_wall = wall_element.shape[0] if np.size(wall_element) else 0
            for i_wall_on in range(n_wall):
                if wall_element[i_wall_on, 0] == z_point[i_height]:
                    node_index = find_index(node[:, 0],
                                            wall_element[i_wall_on, 2:4])
                    ax.plot(node[node_index[0], 1], node[node_index[0], 2],
                            'kx', markersize=2)
                    ax.plot(node[node_index[1], 1], node[node_index[1], 2],
                            'kx', markersize=2)

            if _empty(truss_stress):
                truss_ele_no = np.array([])  # noqa: F841 原典ママ(未使用)
            else:
                truss_ele_no = doublecheck(truss_stress[:, 0])  # noqa: F841

            # ---------- 柱脚応力値のplotとデータ保存 ----------
            if _empty(element):
                pass
            else:
                for i_column in range(c_index.size):
                    inode_index, jnode_index = node_index_get(
                        element[c_index[i_column], 0], element, node)
                    ele_no = element[c_index[i_column], 0]
                    if (min(node[inode_index, 3], node[jnode_index, 3])
                            == z_point[i_height]):
                        element_index = find_index(element[:, 0], ele_no)
                        section_no = element[element_index, 2]
                        if limit_sec_no <= section_no:
                            pass
                        else:
                            ele_st_index = find_index(
                                beam_stress[:, 0], ele_no)
                            if ele_st_index == -1:  # MATLAB: ==0
                                if _empty(truss_stress):
                                    print('柱と認識された要素の応力ファイルなし')
                                    raise RuntimeError(
                                        '原典stop: 柱と認識された要素の'
                                        '応力ファイルなし (figure_CB)')
                                else:
                                    truss_st_index = find_index(
                                        truss_stress[:, 0],
                                        element[c_index[i_column], 0])
                                    if truss_st_index == -1:
                                        print('柱と認識された要素の'
                                              '応力ファイルなし')
                                        raise RuntimeError(
                                            '原典stop: 柱と認識された要素の'
                                            '応力ファイルなし (figure_CB)')
                            else:
                                angle = [c_b_result[c_index[i_column]],
                                         element[c_index[i_column], 5]]
                                off = int(load_case_index_beam[cs - 1])
                                plot_stress = beam_stress[
                                    ele_st_index + off:
                                    ele_st_index + off + 3, 0:8]
                                load_no = plot_stress[0, 1]

                                if angle[0] == 2:  # 柱の場合
                                    # せん断力と軸力の算定
                                    if (node[inode_index, 3]
                                            > node[jnode_index, 3]):
                                        CB_node = node[jnode_index, 0]
                                        N_C = plot_stress[2, 2]
                                        My = plot_stress[2, 6]
                                        Mz = plot_stress[2, 7]
                                        q_reverse = -1

                                        # <X方向せん断力>
                                        sd = [1, 0]
                                        qz_shear = (_cosd(angle[1]) * sd[0]
                                                    + _sind(angle[1]) * sd[1])
                                        qy_shear = (
                                            _cosd(angle[1] + 90) * sd[0]
                                            + _sind(angle[1] + 90) * sd[1])
                                        qxx_stress = (
                                            plot_stress[2, 4] * qz_shear
                                            + plot_stress[2, 3] * qy_shear
                                        ) * q_reverse

                                        # <Y方向せん断力>
                                        sd = [0, 1]
                                        qz_shear = (_cosd(angle[1]) * sd[0]
                                                    + _sind(angle[1]) * sd[1])
                                        qy_shear = (
                                            _cosd(angle[1] + 90) * sd[0]
                                            + _sind(angle[1] + 90) * sd[1])
                                        qyy_stress = (
                                            plot_stress[2, 4] * qz_shear
                                            + plot_stress[2, 3] * qy_shear
                                        ) * q_reverse

                                    elif (node[inode_index, 3]
                                          < node[jnode_index, 3]):
                                        CB_node = node[inode_index, 0]
                                        N_C = plot_stress[0, 2]
                                        My = plot_stress[0, 6]
                                        Mz = plot_stress[0, 7]
                                        q_reverse = 1

                                        # <X方向せん断力>
                                        sd = [1, 0]
                                        qz_shear = (_cosd(angle[1]) * sd[0]
                                                    + _sind(angle[1]) * sd[1])
                                        qy_shear = (
                                            _cosd(angle[1] - 90) * sd[0]
                                            + _sind(angle[1] - 90) * sd[1])
                                        qxx_stress = (
                                            plot_stress[0, 4] * qz_shear
                                            + plot_stress[0, 3] * qy_shear
                                        ) * q_reverse

                                        # <Y方向せん断力>
                                        # NOTE: 原典129-133行は j端行
                                        # plot_stress(3,:) と angle+90 を参照
                                        # (原典ママ)
                                        sd = [0, 1]
                                        qz_shear = (_cosd(angle[1]) * sd[0]
                                                    + _sind(angle[1]) * sd[1])
                                        qy_shear = (
                                            _cosd(angle[1] + 90) * sd[0]
                                            + _sind(angle[1] + 90) * sd[1])
                                        qyy_stress = (
                                            plot_stress[2, 4] * qz_shear
                                            + plot_stress[2, 3] * qy_shear
                                        ) * q_reverse
                                    else:
                                        print('柱部材の定義ミス？')
                                        raise RuntimeError(
                                            '原典stop: 柱部材の定義ミス？ '
                                            '(figure_CB)')

                                elif angle[0] == 1.9:  # 斜め柱の場合
                                    # NOTE: c_index は完全柱(=2)のみのため
                                    # 原典でも実行されない死にコード。
                                    # 実行時は N_C/My/Mz 未定義で原典同様
                                    # エラー (NameError) となる。
                                    cb_vec = np.array([
                                        abs(node[inode_index, 1]
                                            - node[jnode_index, 1]),
                                        abs(node[inode_index, 2]
                                            - node[jnode_index, 2])])
                                    cb_vec = cb_vec / np.linalg.norm(cb_vec)
                                    XY = math.sqrt(
                                        (node[inode_index, 1]
                                         - node[jnode_index, 1]) ** 2
                                        + (node[inode_index, 2]
                                           - node[jnode_index, 2]) ** 2)
                                    Z = abs(node[inode_index, 3]
                                            - node[jnode_index, 3])
                                    cos_ = XY / math.sqrt(XY ** 2 + Z ** 2)
                                    # <X方向せん断力>
                                    sd = [1, 0]
                                    q_cos = (sd[0] * cb_vec[0]
                                             + sd[1] * cb_vec[1])
                                    if (node[inode_index, 1]
                                            < node[jnode_index, 1]):
                                        if (node[inode_index, 3]
                                                < node[jnode_index, 3]):
                                            qxx_stress = (q_cos * cos_
                                                          * plot_stress[0, 2])
                                            CB_node = node[inode_index, 0]
                                        else:
                                            qxx_stress = (-1 * q_cos * cos_
                                                          * plot_stress[2, 2])
                                            CB_node = node[jnode_index, 0]
                                    else:
                                        if (node[jnode_index, 3]
                                                < node[inode_index, 3]):
                                            qxx_stress = (q_cos * cos_
                                                          * plot_stress[2, 2])
                                            CB_node = node[jnode_index, 0]
                                        else:
                                            qxx_stress = (-1 * q_cos * cos_
                                                          * plot_stress[0, 2])
                                            CB_node = node[inode_index, 0]

                                    # <Y方向せん断力>
                                    sd = [0, 1]
                                    q_cos = (sd[0] * cb_vec[0]
                                             + sd[1] * cb_vec[1])
                                    if (node[inode_index, 2]
                                            < node[jnode_index, 2]):
                                        if (node[inode_index, 3]
                                                < node[jnode_index, 3]):
                                            qyy_stress = (q_cos * cos_
                                                          * plot_stress[0, 2])
                                        else:
                                            qyy_stress = (-1 * q_cos * cos_
                                                          * plot_stress[2, 2])
                                    else:
                                        if (node[jnode_index, 3]
                                                < node[inode_index, 3]):
                                            qyy_stress = (q_cos * cos_
                                                          * plot_stress[2, 2])
                                        else:
                                            qyy_stress = (-1 * q_cos * cos_
                                                          * plot_stress[0, 2])

                                # 同じ柱脚に集まってくるブレースなどの
                                # 応力ピックアップ
                                same_base_el = element[np.concatenate([
                                    find_zero_index(element[:, 3] - CB_node),
                                    find_zero_index(element[:, 4] - CB_node)
                                ]), 0]
                                same_base_el = same_base_el[
                                    (same_base_el - ele_no) != 0]

                                for i_same in range(same_base_el.size):
                                    # NOTE: inode_index/jnode_index を上書き
                                    # (原典ママ)
                                    inode_index, jnode_index = node_index_get(
                                        same_base_el[i_same], element, node)
                                    Z = abs(node[inode_index, 3]
                                            - node[jnode_index, 3])
                                    if Z < 0.01:
                                        pass
                                    else:
                                        # NOTE: 応力未発見時 (-1) は原典同様
                                        # 不定動作 (誤行参照/エラー)
                                        ele_st_index = find_index(
                                            beam_stress[:, 0],
                                            element[find_index(
                                                element[:, 0],
                                                same_base_el[i_same]), 0])
                                        plot_stress = beam_stress[
                                            ele_st_index + off:
                                            ele_st_index + off + 3, 0:8]
                                        ijnode = element[find_index(
                                            element[:, 0],
                                            same_base_el[i_same]), 3:5]
                                        if ijnode[0] == CB_node:
                                            if (node[find_index(
                                                    node[:, 0],
                                                    ijnode[1]), 3]
                                                    > node[find_index(
                                                        node[:, 0],
                                                        CB_node), 3]):
                                                (inode_index,
                                                 jnode_index) = node_index_get(
                                                    same_base_el[i_same],
                                                    element, node)
                                                (qxx_stress, qyy_stress,
                                                 N_C) = _same_base_accum(
                                                    qxx_stress, qyy_stress,
                                                    N_C, plot_stress,
                                                    inode_index, jnode_index,
                                                    node)
                                        else:
                                            if (node[find_index(
                                                    node[:, 0],
                                                    ijnode[0]), 3]
                                                    > node[find_index(
                                                        node[:, 0],
                                                        CB_node), 3]):
                                                (inode_index,
                                                 jnode_index) = node_index_get(
                                                    same_base_el[i_same],
                                                    element, node)
                                                (qxx_stress, qyy_stress,
                                                 N_C) = _same_base_accum(
                                                    qxx_stress, qyy_stress,
                                                    N_C, plot_stress,
                                                    inode_index, jnode_index,
                                                    node)

                                ni = find_index(node[:, 0], CB_node)
                                _ax_text(
                                    ax, node[ni, 1], node[ni, 2],
                                    '節点番号：' + _n(CB_node, '%15.0f')
                                    + '\n' + 'N' + _n(N_C, frac_part) + 'kN'
                                    + '\n' + 'QX' + _n(qxx_stress, frac_part)
                                    + 'kN'
                                    + '\n' + 'QY' + _n(qyy_stress, frac_part)
                                    + 'kN',
                                    ha='left', va='top', fs=stress_fontsize,
                                    rot=0)
                                RESULT = np.vstack([
                                    RESULT,
                                    [CB_node, load_no, N_C, qxx_stress,
                                     qyy_stress, My, Mz]])

            # ---------- 通り芯情報を記入する ----------
            # XYの通り芯のスタート（X1 Y1など）の符号を記す
            num_axis = len(axis_name)  # noqa: F841 原典ママ(未使用)

            x_axis_index = find_zero_index(axis_plot_coefi[:, 0] - 1) \
                if np.size(axis_plot_coefi) else np.array([], dtype=int)
            y_axis_index = find_zero_index(axis_plot_coefi[:, 0] - 2) \
                if np.size(axis_plot_coefi) else np.array([], dtype=int)

            # X通りのプロット
            for i_x_axis in range(x_axis_index.size):
                _ax_text(ax,
                         axis_plot_coefi[x_axis_index[i_x_axis], 2],
                         axis_ytick.min() - axisname_location,
                         axis_name[int(x_axis_index[i_x_axis])],
                         ha='center', va='top', fs=axis_fontsize)

            # Y通りのプロット
            for i_y_axis in range(y_axis_index.size):
                _ax_text(ax,
                         axis_xtick.min() - axisname_location,
                         axis_plot_coefi[y_axis_index[i_y_axis], 2],
                         axis_name[int(y_axis_index[i_y_axis])],
                         ha='right', va='middle', fs=axis_fontsize)

            # ---------- 応力図の枠の大きさを設定 ----------
            ax.set_xticks([])
            ax.set_xlim(axis_xtick.min() - mergin_LEFT,
                        axis_xtick.max() + mergin_RIGHT)
            ax.set_yticks([])
            ax.set_ylim(axis_ytick.min() - mergin_BOTTOM,
                        axis_ytick.max() + mergin_TOP)

            # ---------- 柱間隔ならびに階高を記入する ----------
            # 水平方向の寸法線の点をplot
            if axis_xtick.size == 0:
                pass
            else:
                ax.plot(axis_plot_coefi[x_axis_index, 2],
                        np.full(x_axis_index.size,
                                axis_ytick.min() - line_location),
                        'kx', markersize=2)
            if axis_ytick.size == 0:
                pass
            else:
                ax.plot(np.full(y_axis_index.size,
                                axis_xtick.min() - line_location),
                        axis_plot_coefi[y_axis_index, 2],
                        'kx', markersize=2)

            for i_x_axis in range(x_axis_index.size - 1):
                ax.plot([axis_plot_coefi[x_axis_index[i_x_axis], 2],
                         axis_plot_coefi[x_axis_index[i_x_axis + 1], 2]],
                        [axis_ytick.min() - line_location,
                         axis_ytick.min() - line_location],
                        linestyle='-', color='k')
                _ax_text(ax,
                         axis_plot_coefi[x_axis_index[i_x_axis], 2] * 0.5
                         + axis_plot_coefi[x_axis_index[i_x_axis + 1], 2]
                         * 0.5,
                         axis_ytick.min() - line_location,
                         _n(_matlab_round(abs(
                             axis_plot_coefi[x_axis_index[i_x_axis], 2]
                             - axis_plot_coefi[x_axis_index[i_x_axis + 1], 2])
                             * 1000), '%15.0f'),
                         ha='center', va='baseline', fs=dimension_fontsize)

            for i_y_axis in range(y_axis_index.size - 1):
                ax.plot([axis_xtick.min() - line_location,
                         axis_xtick.min() - line_location],
                        [axis_plot_coefi[y_axis_index[i_y_axis], 2],
                         axis_plot_coefi[y_axis_index[i_y_axis + 1], 2]],
                        linestyle='-', color='k')
                _ax_text(ax,
                         axis_xtick.min() - line_location,
                         axis_plot_coefi[y_axis_index[i_y_axis], 2] * 0.5
                         + axis_plot_coefi[y_axis_index[i_y_axis + 1], 2]
                         * 0.5,
                         _n(_matlab_round(abs(
                             axis_plot_coefi[y_axis_index[i_y_axis], 2]
                             - axis_plot_coefi[y_axis_index[i_y_axis + 1], 2])
                             * 1000), '%15.0f'),
                         ha='center', va='baseline', fs=dimension_fontsize,
                         rot=90)

            x_label = '支点レベル：'
            if z_point.size == 1:
                x_label = x_label + _n(z_point[0], '%15.2f') + 'm'
            else:
                # NOTE: 原典 figure_CB.m 393行は未定義変数 num_z_reac を
                # 参照するため z_point が複数レベルの場合ここでエラー停止
                # する。x_label は本関数内で未使用の死にコードのため、
                # 移植ではエラーを再現せず未加算のまま進める (レポート参照)。
                pass

            ax.set_title('水平荷重時・柱脚設計用応力' + '[　' + '　荷重ケース：'
                         + case_name + '　]', fontsize=title_fontsize)
            ax.set_xlabel('表示レベル：' + _n(z_point[i_height], '%15.2f')
                          + 'm', fontsize=title_fontsize)

            ax.set_aspect('equal', adjustable='box')  # daspect([1 1 1])
            _set_paper(fig, paper_orient, paper_size)

            out_path = os.path.join(
                out_dir, 'CB_%d_%s_L%s.pdf'
                % (fig_no, _safe_name(case_name),
                   _n(z_point[i_height], '%15.2f')))
            fig.savefig(out_path, format='pdf')
            plt.close(fig)
            pdf_paths.append(out_path)
            fig_no = fig_no + 1

    return RESULT, pdf_paths
