# -*- coding: utf-8 -*-
"""伏図・軸組図の共通部品: 部材の外形・通り芯・レベル線・縮尺.

規則は tools/struct_cad_py と同じ (draw/elevation.py の _width_rect、floor_plan.py /
axis_elevation.py の通り芯)。通り芯・レベル線にする通りとフロアは画面で選ぶ (vwdxf の追加)。
座標は原寸 mm。
"""
import math

import numpy as np

from ..style import (GRID_BUBBLE_R_RATIO, LAYER_GRID, LAYER_TEXT, PAPER_MARGIN_MM, PAPER_MM,
                     SCALE_SERIES)
from ..members import (GRID_OVERHANG_M, _note_once, floor_levels, frame_dir,
                       frame_geometry, plan_key_z)
from .primitives import Circle, Line, Text

OVER = GRID_OVERHANG_M * 1000.0     # 通り芯の構造外への出 [mm]


def auto_scale(width_mm, height_mm, paper='A3', extra_paper_mm=0.0):
    """用紙に収まる縮尺 (縮尺を指定しないときだけ使う)."""
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    aw = pw - 2 * PAPER_MARGIN_MM - extra_paper_mm
    ah = ph - 2 * PAPER_MARGIN_MM - 17.0 - extra_paper_mm   # 17 = 見出し (18pt × 2.5 行)
    need = max(width_mm / max(aw, 1.0), height_mm / max(ah, 1.0))
    for s in SCALE_SERIES:
        if s >= need:
            return s
    return SCALE_SERIES[-1]


def canon_normal(vx, vy):
    """部材方向 (vx, vy) の法線を、常に上向き (ny >= 0) にそろえて返す."""
    nx, ny = -vy, vx
    if ny < 0 or (ny == 0 and nx < 0):
        nx, ny = -nx, -ny
    return nx, ny


def width_rect(p1, p2, w, ij_end=(0.0, 0.0), g_up=0.0):
    """p1-p2 を中心線とする幅 w の矩形 (w が (wi, wj) なら台形) の 4 隅. 描けなければ None.

    ij_end = (i 端の延長, j 端の延長) (正 = 外へ、負 = 内へ)。g_up = 法線 (上向き) 方向の平行移動。
    struct_cad draw/elevation.py の _width_rect と同じ (単位は呼ぶ側にそろえる)。
    """
    wi, wj = (w[0], w[1]) if isinstance(w, (tuple, list)) else (w, w)
    if wi <= 0 and wj <= 0:
        return None
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    vx, vy = dx / length, dy / length
    nx, ny = canon_normal(vx, vy)
    hi, hj = wi / 2.0, wj / 2.0
    gi, gj = ij_end
    i0 = (p1[0] - vx * gi + nx * g_up, p1[1] - vy * gi + ny * g_up)
    j0 = (p2[0] + vx * gj + nx * g_up, p2[1] + vy * gj + ny * g_up)
    return [(i0[0] + nx * hi, i0[1] + ny * hi), (j0[0] + nx * hj, j0[1] + ny * hj),
            (j0[0] - nx * hj, j0[1] - ny * hj), (i0[0] - nx * hi, i0[1] - ny * hi)]


def plan_text_angle(p1, p2):
    """p1→p2 の平面角 [deg] を、文字が読める向き (-90, 90] にそろえる."""
    ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
    if ang > 90.0:
        ang -= 180.0
    elif ang <= -90.0:
        ang += 180.0
    return ang


# ---------------------------------------------------------------------------
# 符号の置き場所 (部材の真上を避けて脇へ出す。struct_cad は部材の中点に中央揃えだが、
# VW10J で見にくいため vwdxf で変えた 2026-09-19。ずらしは置く位置で行い、
# <符号>_NAME1 の中身は中央揃えの文字のまま → VW10J で 1 つずつ動かして手直しできる)
# ---------------------------------------------------------------------------

LABEL_GAP_RATIO = 0.3     # 部材の縁と文字のすき間 = 文字高さ × これ


def text_width(text, th):
    """文字幅の目安 [mm] (prims_bounds と同じ 0.5 × 高さ × 文字数)."""
    return 0.5 * th * max(len(text), 1)


def label_beside(mid, rot_deg, half_w, th):
    """部材の中点 mid から、文字の上方向 (回転 rot_deg の文字の「上」) へ部材の半幅 + すき間 + 文字の半分.

    水平の梁は上側、縦の梁 (文字 90°) は左側に出る。
    """
    r = math.radians(rot_deg)
    up = (-math.sin(r), math.cos(r))
    d = half_w + LABEL_GAP_RATIO * th + th / 2.0
    return (mid[0] + up[0] * d, mid[1] + up[1] * d)


def symbol_half_extent(sec, beta_deg, down=False):
    """断面記号 (局所 X = せい) を β だけ回した平面の半幅・半高 [mm]."""
    from ..sections import symbol_shape
    c, s = math.cos(math.radians(beta_deg)), math.sin(math.radians(beta_deg))
    xs, ys = [0.0], [0.0]
    for it in symbol_shape(sec, down):
        pts = it[1] if it[0] == 'poly' else [(it[1][0] + dx * it[2], it[1][1] + dy * it[2])
                                             for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        for x, y in pts:
            xs.append((x * c - y * s) * 1000.0)
            ys.append((x * s + y * c) * 1000.0)
    return max(abs(v) for v in xs), max(abs(v) for v in ys)


# ---------------------------------------------------------------------------
# 通り芯・レベル線
# ---------------------------------------------------------------------------

def grid_lines(M, keys):
    """通り芯にする通り (auto_frames のキー) → [(表示名, 通る点 [m], 向き, 自分の節点の範囲 (t0, t1))].

    同じ位置の通りは 1 本。
    """
    out = []
    for key in keys or []:
        try:
            dvec, nodes, gname = frame_geometry(M, str(key))
        except ValueError:
            continue
        pts = np.array([M.node_xyz[n][:2] for n in nodes], dtype=float)
        p = pts.mean(axis=0)
        ts = (pts - p) @ np.asarray(dvec, dtype=float)
        dup = None
        for label, q, d, _t in out:
            if abs(d[0] * dvec[1] - d[1] * dvec[0]) < 1e-3 \
                    and abs((p[0] - q[0]) * d[1] - (p[1] - q[1]) * d[0]) < 0.01:
                dup = label
                break
        if dup is not None:
            _note_once(M, '注意: 通り芯 %s は %s と同じ位置のため省略します' % (gname, dup))
            continue
        out.append((gname, p, dvec, (float(ts.min()), float(ts.max()))))
    return out


def _bubble(center, label, r, th):
    return [Circle(center, r, LAYER_TEXT[0]), Text(center, label, th, LAYER_TEXT[0], 'MC')]


def plan_grid_prims(M, lines, n_scale, grid_mm):
    """伏図の通り芯 (一点鎖線 + 丸記号). 全階で同じ (struct_cad floor_plan と同じ、原寸 mm).

    X 通り (縦線) は Y 通りの位置の範囲 ± 0.8m、Y 通りは X 通りの範囲 ± 0.8m
    (直交する通りが無ければ自分の節点の範囲)。斜めの通りは自分の節点の範囲 ± 0.8m。
    丸記号は線の始点側 (X 通りは下、Y 通りは左、斜めは向きの手前側)。
    """
    th = grid_mm * n_scale
    r = GRID_BUBBLE_R_RATIO * th
    xs = [p[0] for _l, p, d, _t in lines if frame_dir(d) == 'x']     # X 通りの X 位置
    ys = [p[1] for _l, p, d, _t in lines if frame_dir(d) == 'y']     # Y 通りの Y 位置
    out = []
    for label, p, d, (ta, tb) in lines:
        kind = frame_dir(d)
        if kind == 'x' and ys:
            t0, t1 = min(ys) - p[1], max(ys) - p[1]
        elif kind == 'y' and xs:
            t0, t1 = min(xs) - p[0], max(xs) - p[0]
        else:
            t0, t1 = ta, tb
        t0, t1 = t0 - GRID_OVERHANG_M, t1 + GRID_OVERHANG_M
        a = ((p[0] + d[0] * t0) * 1000.0, (p[1] + d[1] * t0) * 1000.0)
        b = ((p[0] + d[0] * t1) * 1000.0, (p[1] + d[1] * t1) * 1000.0)
        out.append(Line(a, b, LAYER_GRID[0], 'grid'))
        out += _bubble((a[0] - d[0] * r, a[1] - d[1] * r), label, r, th)
    return out


def elevation_grid_prims(M, dvec, c, u0, span, lines, n_scale, grid_mm, self_name):
    """軸組図の通り芯 (この構面と交わる通りの縦線 + 下の丸記号). struct_cad と同じ.

    縦線の上下 = 全フロアのレベルの範囲 ± 0.8m。構面の範囲 ± 0.8m で、30° 以上で交わる通りだけ。
    """
    th = grid_mm * n_scale
    r = GRID_BUBBLE_R_RATIO * th
    lv = floor_levels(M)
    z_lo = (min(lv) if lv else 0.0) * 1000.0
    z_hi = (max(lv) if lv else 1.0) * 1000.0
    out = []
    seen = []
    for label, p, d, _t in lines:
        if label == self_name:
            continue
        cross = dvec[0] * d[1] - dvec[1] * d[0]
        if abs(cross) < 0.5:
            continue          # 構面と 30° 未満でしか交わらない通りは描かない
        # 構面の直線 c + dvec·t と通り芯 p + d·k の交点 → その u (= 交点・dvec)
        t = ((p[0] - c[0]) * d[1] - (p[1] - c[1]) * d[0]) / cross
        s = c[0] * dvec[0] + c[1] * dvec[1] + t
        if not (u0 - GRID_OVERHANG_M <= s <= u0 + span + GRID_OVERHANG_M):
            continue          # 構面の範囲の外で交わる通りは描かない
        u = s * 1000.0
        if any(abs(u - v) < 1.0 for v in seen):
            continue
        seen.append(u)
        out.append(Line((u, z_lo - OVER), (u, z_hi + OVER), LAYER_GRID[0], 'grid'))
        out += _bubble((u, z_lo - OVER - r), label, r, th)
    return out


def level_lines(M, keys):
    """レベル線にするフロアのキー → list of (表示名, Z [m]). 同じ高さは先の方だけ."""
    out = []
    for key in (keys or []):
        key = str(key)
        try:
            z = plan_key_z(M, key)
        except (ValueError, IndexError):
            _note_once(M, '注意: レベル %s の高さを決められないため描きません' % key)
            continue
        label = key[2:] if key.startswith('G:') else key
        dup = next((lb for lb, zz in out if abs(zz - z) < 0.001), None)
        if dup is not None:
            _note_once(M, '注意: レベル %s は %s と同じ高さのため省略します' % (label, dup))
            continue
        out.append((label, z))
    return out


def level_prims(levels, u0, span, n_scale, grid_mm):
    """軸組図のレベル線 (一点鎖線) と左の「▽レベル名」. struct_cad と同じ."""
    th = grid_mm * n_scale
    r = GRID_BUBBLE_R_RATIO * th
    a, b = u0 * 1000.0 - OVER, (u0 + span) * 1000.0 + OVER
    out = []
    for label, z in levels:
        zz = z * 1000.0
        out.append(Line((a, zz), (b, zz), LAYER_GRID[0], 'grid'))
        out.append(Text((a - 2 * r, zz), '▽' + label, th, LAYER_TEXT[0], 'MC'))
    return out


def frame_common_extent(M, dvec, nodes):
    """構面の横の範囲 (u0, span) [m]: 同じ向きの構面の共通範囲 (斜めは自分の範囲)."""
    from ..members import frame_dir_extents
    d = frame_dir(dvec)
    ext = frame_dir_extents(M)
    if d in ext:
        return ext[d]
    us = [float(M.node_xyz[n][0]) * dvec[0] + float(M.node_xyz[n][1]) * dvec[1] for n in nodes]
    return min(us), max(us) - min(us)


__all__ = ['LABEL_GAP_RATIO', 'OVER', 'auto_scale', 'canon_normal', 'elevation_grid_prims',
           'frame_common_extent', 'label_beside', 'symbol_half_extent', 'text_width',
           'grid_lines', 'level_lines', 'level_prims', 'plan_grid_prims', 'plan_text_angle',
           'width_rect']
