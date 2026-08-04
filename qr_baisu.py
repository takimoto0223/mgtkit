# -*- coding: utf-8 -*-
"""壁倍率ベースの剛心・偏心率 (新規実装、MATLAB原典なし).

現行の figure_rekr (Q/δ方式) の代替として、耐震要素の剛性を

    板 (面材壁)  : K = 壁倍率 × 壁長   (厚み=壁倍率のモデル化規約)
    ブレース     : K = 指定倍率 × 平面投影長 (断面番号→倍率の指定)

で評価し、方向配分は Kx = K·(Lx/Lh)², Ky = K·(Ly/Lh)² とする
(X方向壁は Kx=K、45度壁は Kx=Ky=K/2)。品確法・グレー本の
壁量ベースの偏心率計算と同じ考え方で、変形・板応力ファイルは不要。

重心は原典 (figure_rekr) と同じく、層下端レベルにある鉛直部材の
長期軸力 (beam_stress) による加重平均。

戻り値は figure_rekr 互換の collect_node (M×7:
[層, 要素, 下端代表節点, 上端代表節点, N, Kx, Ky]) と、
壁・ブレースの代表節点を追加した node 行列。下流の
wg_center / kr_re / figure_w_center / figure_g_center をそのまま使える。
"""

import math

import numpy as np

from mgtkit.qr_center import column_beam_judge
from mgtkit.util import find_index, find_zero_index

Z_TOL = 0.005


def _story_bands(z_point, case_height):
    """層番号ごとの (下端レベル群, 次層下端) を返す."""
    z_point = np.asarray(z_point, dtype=float).ravel()
    bands = []
    for i in range(len(case_height)):
        zs = z_point[np.asarray(case_height[i], dtype=int).ravel() - 1]
        if i + 1 < len(case_height):
            nx = z_point[np.asarray(case_height[i + 1],
                                    dtype=int).ravel() - 1]
            z_top = float(np.min(nx))
        else:
            z_top = float('inf')
        bands.append((np.asarray(zs, dtype=float), z_top))
    return bands


def _story_of(z_bottom, bands):
    """下端Zがどの層か (下端レベル一致を優先、なければ層の帯で判定)."""
    for i, (zs, _zt) in enumerate(bands):
        if np.any(np.abs(zs - z_bottom) <= Z_TOL):
            return i + 1
    for i, (zs, zt) in enumerate(bands):
        if float(np.min(zs)) - Z_TOL <= z_bottom < zt - Z_TOL:
            return i + 1
    return 0


def figure_rekr_baisu(limit_sec_no, node, element, beam_stress,
                      load_case_index_beam, z_point, case_height,
                      N_case, plate, thickness, brace_baisu=None):
    """壁倍率ベースの collect_node を組み立てる (figure_rekr 互換)."""
    node = np.asarray(node, dtype=float)
    element = np.atleast_2d(np.asarray(element, dtype=float))
    beam_stress = np.atleast_2d(np.asarray(beam_stress, dtype=float))
    z_point = np.asarray(z_point, dtype=float).ravel()
    lci_beam = np.asarray(load_case_index_beam, dtype=int).ravel()
    nc = int(np.atleast_1d(N_case)[0])
    brace_baisu = dict(brace_baisu or {})
    bands = _story_bands(z_point, case_height)

    pos = {int(r[0]): np.asarray(r[1:4], dtype=float) for r in node}

    rows = []
    # ---------------- 重心用: 鉛直部材の軸力 ----------------
    for i in range(1, len(case_height) + 1):
        zs = z_point[np.asarray(case_height[i - 1], dtype=int).ravel() - 1]
        for z0 in np.atleast_1d(zs):
            nid_on = find_zero_index(node[:, 3] - float(z0))
            for nid in nid_on:
                nno = node[nid, 0]
                eid = np.concatenate(
                    (find_zero_index(element[:, 3] - nno),
                     find_zero_index(element[:, 4] - nno)))
                for e0 in eid:
                    e0 = int(e0)
                    if element[e0, 2] > limit_sec_no:
                        continue
                    if column_beam_judge(e0, element, node)[0] <= 1:
                        continue
                    other = (element[e0, 4] if element[e0, 3] == nno
                             else element[e0, 3])
                    oid = int(np.atleast_1d(
                        find_index(node[:, 0], other))[0])
                    if node[oid, 3] <= float(z0) + Z_TOL:
                        continue  # 上へ伸びる部材のみ
                    fi = int(np.atleast_1d(
                        find_index(beam_stress[:, 0], element[e0, 0]))[0])
                    if fi == -1:
                        continue  # 重心算定の対象は梁要素 (原典と同じ)
                    L = float(np.linalg.norm(node[oid, 1:4] - node[nid, 1:4]))
                    L_V = abs(float(node[oid, 3] - node[nid, 3]))
                    end2 = 2 if element[e0, 4] == nno else 0
                    N = float(beam_stress[fi + int(lci_beam[nc - 1]) + end2,
                                          2]) * L_V / max(L, 1e-12)
                    rows.append([i, element[e0, 0], nno, node[oid, 0],
                                 N, 0.0, 0.0])

    # ---------------- 剛心用: 板要素 (倍率×長さ) ----------------
    new_nodes = []
    next_no = float(np.max(node[:, 0])) if node.size else 0.0

    def _rep_nodes(p_bot, p_top):
        nonlocal next_no
        next_no += 1
        n1 = next_no
        new_nodes.append([n1, p_bot[0], p_bot[1], p_bot[2]])
        next_no += 1
        n2 = next_no
        new_nodes.append([n2, p_top[0], p_top[1], p_top[2]])
        return n1, n2

    thick_map = {}
    th = np.atleast_2d(np.asarray(thickness, dtype=float)) \
        if np.size(thickness) else np.zeros((0, 3))
    for r in th:
        thick_map[int(r[0])] = float(r[1])

    plate = np.atleast_2d(np.asarray(plate, dtype=float)) \
        if np.size(plate) else np.zeros((0, 9))
    n_plate = 0
    for r in plate:
        nds = [int(v) for v in r[3:7] if int(v) > 0]
        pts = [pos[n] for n in nds if n in pos]
        if len(pts) < 3:
            continue
        xy = np.asarray([p[:2] for p in pts])
        zvals = [float(p[2]) for p in pts]
        q = xy - xy.mean(axis=0)
        w = np.linalg.eigvalsh(q.T @ q)
        if math.sqrt(max(float(w[0]), 0.0) / len(pts)) > Z_TOL:
            continue  # 水平床など面的な板は対象外 (鉛直壁のみ)
        if (max(zvals) - min(zvals)) < 0.01:
            continue
        beta = thick_map.get(int(r[2]))
        if beta is None:
            continue
        story = _story_of(min(zvals), bands)
        if story == 0:
            continue
        # 壁の平面ベクトル
        i_lo = int(np.argmin(zvals))
        zlo = zvals[i_lo]
        low = [p for p in pts if abs(float(p[2]) - zlo) < 1e-6]
        if len(low) < 2:
            low = pts
        L_X = float(max(p[0] for p in pts) - min(p[0] for p in pts))
        L_Y = float(max(p[1] for p in pts) - min(p[1] for p in pts))
        L_H = math.hypot(L_X, L_Y)
        if L_H < 1e-9:
            continue
        Kx = beta * L_X ** 2 / L_H
        Ky = beta * L_Y ** 2 / L_H
        cx, cy = float(xy[:, 0].mean()), float(xy[:, 1].mean())
        n1, n2 = _rep_nodes((cx, cy, min(zvals)), (cx, cy, max(zvals)))
        rows.append([story, r[0], n1, n2, 0.0, Kx, Ky])
        n_plate += 1

    # ---------------- 剛心用: ブレース (指定倍率×平面投影長) ----------------
    n_brace = 0
    if brace_baisu:
        for e0 in range(element.shape[0]):
            beta = brace_baisu.get(int(element[e0, 2]))
            if beta is None:
                continue
            i1 = int(np.atleast_1d(
                find_index(node[:, 0], element[e0, 3]))[0])
            j1 = int(np.atleast_1d(
                find_index(node[:, 0], element[e0, 4]))[0])
            p1, p2 = node[i1, 1:4], node[j1, 1:4]
            if p1[2] > p2[2]:
                p1, p2 = p2, p1
            L_X = abs(float(p2[0] - p1[0]))
            L_Y = abs(float(p2[1] - p1[1]))
            L_H = math.hypot(L_X, L_Y)
            L_V = abs(float(p2[2] - p1[2]))
            if L_V < 0.01:
                continue  # 水平材は対象外
            story = _story_of(float(p1[2]), bands)
            if story == 0:
                continue
            if L_H < 1e-9:
                continue  # 鉛直材 (方向が定まらない)
            Kx = beta * L_X ** 2 / L_H
            Ky = beta * L_Y ** 2 / L_H
            cx = float(p1[0] + p2[0]) / 2
            cy = float(p1[1] + p2[1]) / 2
            n1, n2 = _rep_nodes((cx, cy, float(p1[2])),
                                (cx, cy, float(p2[2])))
            rows.append([story, element[e0, 0], n1, n2, 0.0, Kx, Ky])
            n_brace += 1

    if not rows:
        raise ValueError('壁倍率ベースの偏心率: 算定対象の要素がありません '
                         '(板要素と層構成を確認してください)')
    collect_node = np.asarray(rows, dtype=float)
    node2 = (np.vstack((node, np.asarray(new_nodes, dtype=float)))
             if new_nodes else node)
    print('壁倍率ベースの剛心: 板 %d 要素, ブレース %d 要素を'
          '剛性 (倍率×長さ) として集計しました。' % (n_plate, n_brace))
    if not brace_baisu:
        print('注意: ブレース倍率が未指定のため、剛心はブレースを含めず'
              '板 (面材壁) のみで算定しています。')
    return collect_node, node2
