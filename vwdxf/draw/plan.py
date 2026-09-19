# -*- coding: utf-8 -*-
"""伏図 (フロアごと). 座標系 (X, Y) [mm].

規則は tools/struct_cad_py の draw/floor_plan.py と同じ:
  - 床グループの梁・トラスを見付け幅の矩形 + 端部処理 (end_extension、トラスは -3 倍) で描く。
    レイヤは要素タイプ (梁 = S-Yellow20 / トラス = S-Gray09)。符号は梁の向きで、梁の脇 (上 / 縦の梁は左) に置く
  - 床の節点に取り付く柱を断面記号で置く (上に立つ柱。下柱は × 印)。符号は断面記号の右上に置く
  - 木造 (最上階以外) は 2 枚: 梁伏図 = 柱の符号なし / 柱伏図 = 梁は破線・梁の符号なし、
    その床から立ち上がる壁 (板厚の矩形 + 板厚の符号)
  - 見出しは床の名前だけを、伏図全体の範囲の上 1m の中央に
vwdxf の追加: 通り芯にする通りは画面で選ぶ。文字は pt 指定 (紙面 mm × 縮尺)。
"""
import math

from .. import sections
from ..members import (columns_at_node, ensure_surfaces, group_floor_nodes,
                       group_members, member_ends, plan_key_z, plan_width, plane_inclination,
                       plans_extent)
from ..style import (DASHED_SUFFIX, LAYER_DOWN_COLUMN, LAYER_MEMBER, LAYER_SECTION, LAYER_TEXT,
                     LAYER_TITLE, LAYER_TRUSS, LAYER_WALL, WALL_INCLINE_DEG)
from .figure import (LABEL_GAP_RATIO, auto_scale, grid_lines, label_beside, plan_grid_prims,
                     plan_text_angle, symbol_half_extent, text_width, width_rect)
from .primitives import Figure, Line, NameRef, Poly, SectionRef, Text, prims_bounds


def _wall_plan_segment(M, nodes):
    """鉛直な壁の平面の線 (最も離れた 2 点) [m]."""
    pts = [(round(float(M.node_xyz[n][0]), 4), round(float(M.node_xyz[n][1]), 4))
           for n in nodes if n in M.node_xyz]
    uniq = list(dict.fromkeys(pts))
    if len(uniq) < 2:
        return None
    best, bd = None, -1.0
    for i in range(len(uniq)):
        for j in range(i + 1, len(uniq)):
            d = (uniq[i][0] - uniq[j][0]) ** 2 + (uniq[i][1] - uniq[j][1]) ** 2
            if d > bd:
                bd, best = d, (uniq[i], uniq[j])
    return best


def _wall_support_width(M, nodes, t):
    """壁の下端 2 節点を結ぶ梁の平面見付け幅 [m]。無ければ板厚."""
    zs = [float(M.node_xyz[n][2]) for n in nodes if n in M.node_xyz]
    if zs:
        zmin = min(zs)
        bottom = {n for n in nodes if n in M.node_xyz and abs(float(M.node_xyz[n][2]) - zmin) < 1e-6}
        if len(bottom) >= 2:
            for m in M.members:
                if {m['n1'], m['n2']} == bottom:
                    return plan_width(m)
    return t if t > 0 else 0.2


def rising_walls(M, fz, cx, cy):
    """床レベル fz から立ち上がる壁 → [(4 隅 [m], 中央 [m], 板厚名称)] (struct_cad _wall_rect).

    壁 = 面の傾き 60° 以上の面要素で、下端がこの床レベルにあるもの。外面を下の梁の外側の縁
    (床の重心から遠い側) に合わせ、板厚だけ内側へ広げた矩形。板厚 0 の壁は描かない。
    """
    ensure_surfaces(M)
    out = []
    for _ele, tid, nodes in M._surfaces:
        zs = [float(M.node_xyz[n][2]) for n in nodes if n in M.node_xyz]
        if len(zs) < 3 or plane_inclination(M, nodes) < WALL_INCLINE_DEG:
            continue
        if abs(min(zs) - fz) > 0.3:
            continue
        t, name = M._thickness.get(tid, (0.0, ''))
        seg = _wall_plan_segment(M, nodes)
        if seg is None or t <= 0.0:
            continue
        (ax, ay), (bx, by) = seg
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-9:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux
        if nx * ((ax + bx) / 2 - cx) + ny * ((ay + by) / 2 - cy) < 0:
            nx, ny = -nx, -ny
        outer = _wall_support_width(M, nodes, t) / 2.0
        inner = outer - t
        o1 = (ax + nx * outer, ay + ny * outer)
        o2 = (bx + nx * outer, by + ny * outer)
        i2 = (bx + nx * inner, by + ny * inner)
        i1 = (ax + nx * inner, ay + ny * inner)
        ctr = ((o1[0] + i2[0]) / 2.0, (o1[1] + i2[1]) / 2.0)
        out.append(([o1, o2, i2, i1], ctr, name))
    return out


def build_plan(M, level, scale=None, paper='A3', text_paper_mm=2.5, grids=None,
               wood_sheet=None, grid_paper_mm=6.35):
    """伏図 1 枚 → Figure. level = 'G:<床グループ名>'.

    wood_sheet: None / 'full' (1 枚で全部) / 'beam' (梁伏図) / 'column' (柱伏図)。
    """
    key = str(level)
    gname = key[2:] if key.startswith('G:') else key
    if gname not in M.groups:
        raise ValueError('グループ %s がありません' % gname)
    fz = plan_key_z(M, key)
    beams = [i for i in group_members(M, gname) if M.members[i]['kind'] != 'column']
    floor_nodes = group_floor_nodes(M, gname)
    ext = plans_extent(M) or (0.0, 0.0, 1.0, 1.0)
    gx0, gy0, gx1, gy1 = [v * 1000.0 for v in ext]
    n = int(scale) if scale else auto_scale(gx1 - gx0, gy1 - gy0, paper)
    th = text_paper_mm * n
    P = []

    # 通り芯 (背面)
    P += plan_grid_prims(M, grid_lines(M, grids), n, grid_paper_mm)

    # 梁・トラス
    for i in beams:
        m = M.members[i]
        p1 = (float(m['p1'][0]) * 1000.0, float(m['p1'][1]) * 1000.0)
        p2 = (float(m['p2'][0]) * 1000.0, float(m['p2'][1]) * 1000.0)
        if wood_sheet == 'column' and not m['truss']:
            layer, ltype = LAYER_MEMBER['beam'][0] + DASHED_SUFFIX, 'dashed'
        else:
            layer, ltype = (LAYER_TRUSS[0] if m['truss'] else LAYER_MEMBER['beam'][0]), None
        e1, e2 = member_ends(M, i, 'center')
        rect = width_rect(p1, p2, plan_width(m) * 1000.0, (e1 * 1000.0, e2 * 1000.0))
        if rect is None:
            P.append(Line(p1, p2, layer, ltype))
        else:
            P.append(Poly(rect, layer, True, ltype))
        if wood_sheet != 'column' and m['code']:
            # 梁の脇 (文字の上側 = 水平の梁は上、縦の梁は左) へ、見付け幅の半分 + すき間だけ出す
            rot = plan_text_angle(p1, p2)
            mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            P.append(NameRef(sections.name_block(m['code']), m['code'],
                             label_beside(mid, rot, plan_width(m) * 500.0, th), th, rot,
                             LAYER_TEXT[0]))

    # 柱 (断面記号)。符号は梁伏図では出さない
    drawn = set()
    for node in floor_nodes:
        if node in drawn:
            continue
        specs, name_j = columns_at_node(M, node, fz)
        if not specs:
            continue
        x, y = float(M.node_xyz[node][0]) * 1000.0, float(M.node_xyz[node][1]) * 1000.0
        for j, down in specs:
            m = M.members[j]
            sym = m['code'] or 'S%d' % m['sec_no']
            P.append(SectionRef(sections.block_name(sym, down), m['sec'], (x, y), m['beta'],
                                LAYER_DOWN_COLUMN[0] if down else LAYER_SECTION['column'][0], down))
        if name_j is not None and wood_sheet != 'beam' and M.members[name_j]['code']:
            # 柱の断面記号の右上へ
            mc = M.members[name_j]
            code = mc['code']
            hx, hy = symbol_half_extent(mc['sec'], mc['beta'])
            gap = LABEL_GAP_RATIO * th
            P.append(NameRef(sections.name_block(code), code,
                             (x + hx + gap + text_width(code, th) / 2.0, y + hy + gap + th / 2.0),
                             th, 0.0, LAYER_TEXT[0]))
        drawn.add(node)

    # 木造の柱伏図: その床から立ち上がる壁
    if wood_sheet == 'column':
        xs = [float(M.node_xyz[k][0]) for k in floor_nodes]
        ys = [float(M.node_xyz[k][1]) for k in floor_nodes]
        cx = sum(xs) / len(xs) if xs else 0.0
        cy = sum(ys) / len(ys) if ys else 0.0
        for corners, ctr, name in rising_walls(M, fz, cx, cy):
            P.append(Poly([(a * 1000.0, b * 1000.0) for a, b in corners], LAYER_WALL[0]))
            if name:
                P.append(NameRef(sections.name_block(name), name,
                                 (ctr[0] * 1000.0, ctr[1] * 1000.0), th, 0.0, LAYER_TEXT[0]))

    if not beams and not drawn:
        raise ValueError('伏図 %s に描画できる部材がありません' % gname)

    # 見出し: 床の名前を伏図全体の範囲の上 1m、中央に
    tg = grid_paper_mm * n
    P.append(Text(((gx0 + gx1) / 2.0, gy1 + 1000.0), gname, tg, LAYER_TITLE[0], 'MC'))
    if not wood_sheet or wood_sheet == 'full':
        title = gname
    else:
        title = '%s (%s)' % (gname, {'beam': '梁伏図', 'column': '柱伏図'}[wood_sheet])
    return Figure('plan', key, title, n, prims_bounds(P), P, wood_sheet,
                  frame_box=(gx0, gy0, gx1, gy1))


__all__ = ['build_plan', 'rising_walls']
