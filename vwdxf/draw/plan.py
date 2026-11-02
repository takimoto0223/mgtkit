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
from ..members import (columns_at_node, ensure_surfaces, frame_dir, group_floor_nodes,
                       group_members, member_ends, plan_key_z, plan_width, plane_inclination,
                       plans_extent)
from ..style import (DASHED_SUFFIX, LAYER_DOWN_COLUMN, LAYER_MEMBER, LAYER_SECTION, LAYER_TEXT,
                     LAYER_TITLE, LAYER_TRUSS, LAYER_WALL, WALL_INCLINE_DEG)
from .figure import (LABEL_GAP_RATIO, auto_scale, grid_lines, label_beside, level_lines,
                     plan_grid_prims, plan_text_angle, symbol_half_extent, text_width,
                     width_rect)
from .primitives import (Figure, Line, NameRef, Poly, SectionRef, Text, figure_id, member_tag,
                         prims_bounds, stamp, wall_tag)


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


GRID_AXIS_TOL = 0.01      # 付属 JSON で X / Y 通りとして座標 1 つで書く向きの許容 (約 0.57°。傾きは dir で添える)


def plan_grid_meta(lines):
    """付属 JSON の grids と基準点 (図の座標 [mm]) → (grids, origin or None).

    grids: ほぼ真っすぐな X 通り (縦線) は X 位置、Y 通り (横線) は Y 位置 (通る点の平均。
    わずかに傾いた通りは向き dir も添える)。それより傾いた通りは other に通る点と向き
    (family = frame_dir の 'x' / 'y' / None)。
    origin: いちばん左の X 系の通り × いちばん下の Y 系の通り (frame_dir で分けた通り) の交点。
    """
    out = {'X': [], 'Y': [], 'other': []}
    fx, fy = [], []
    for label, p, d, _t in lines:
        fam = frame_dir(d)
        if fam == 'x':
            fx.append((p, d))
        elif fam == 'y':
            fy.append((p, d))
        if abs(d[0]) < GRID_AXIS_TOL:
            out['X'].append({'name': label, 'coord': float(p[0]) * 1000.0})
            if abs(d[0]) > 1e-9:
                out['X'][-1]['dir'] = [float(d[0]), float(d[1])]
        elif abs(d[1]) < GRID_AXIS_TOL:
            out['Y'].append({'name': label, 'coord': float(p[1]) * 1000.0})
            if abs(d[1]) > 1e-9:
                out['Y'][-1]['dir'] = [float(d[0]), float(d[1])]
        else:
            out['other'].append({'name': label, 'family': fam,
                                 'point': [float(p[0]) * 1000.0, float(p[1]) * 1000.0],
                                 'dir': [float(d[0]), float(d[1])]})
    for k in ('X', 'Y'):
        out[k].sort(key=lambda g: g['coord'])
    origin = None
    if fx and fy:
        (p1, d1), (p2, d2) = min(fx, key=lambda g: g[0][0]), min(fy, key=lambda g: g[0][1])
        cross = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(cross) > 1e-9:
            t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / cross
            origin = ((p1[0] + d1[0] * t) * 1000.0, (p1[1] + d1[1] * t) * 1000.0)
    return out, origin


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
    """床レベル fz から立ち上がる壁 → [(4 隅 [m], 中央 [m], 板厚名称, 要素番号)] (struct_cad _wall_rect).

    壁 = 面の傾き 60° 以上の面要素で、下端がこの床レベルにあるもの。外面を下の梁の外側の縁
    (床の重心から遠い側) に合わせ、板厚だけ内側へ広げた矩形。板厚 0 の壁は描かない。
    """
    ensure_surfaces(M)
    out = []
    for ele, tid, nodes in M._surfaces:
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
        out.append(([o1, o2, i2, i1], ctr, name, ele))
    return out


def build_plan(M, level, scale=None, paper='A3', text_paper_mm=2.5, grids=None,
               wood_sheet=None, grid_paper_mm=6.35, levels=None):
    """伏図 1 枚 → Figure. level = 'G:<床グループ名>'.

    wood_sheet: None / 'full' (1 枚で全部) / 'beam' (梁伏図) / 'column' (柱伏図)。
    levels: 画面の「レベル線にするフロア」(伏図には描かない。付属 JSON の levels に入れる)。
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
    mids = {}       # 図に出た部材: MID → ('line', 部材番号 | 'wall', 要素番号, 主な図か) (出た順)

    # 通り芯 (背面)
    glines = grid_lines(M, grids)
    P += plan_grid_prims(M, glines, n, grid_paper_mm)

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
        tag = member_tag(m, 'outline', FL=gname)
        if rect is None:
            P.append(Line(p1, p2, layer, ltype, tag=tag))
        else:
            P.append(Poly(rect, layer, True, ltype, tag=tag))
        mids.setdefault(m['elem'], ('line', i, True))
        if wood_sheet != 'column' and m['code']:
            # 梁の脇 (文字の上側 = 水平の梁は上、縦の梁は左) へ、見付け幅の半分 + すき間だけ出す
            rot = plan_text_angle(p1, p2)
            mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            P.append(NameRef(sections.name_block(m['code']), m['code'],
                             label_beside(mid, rot, plan_width(m) * 500.0, th), th, rot,
                             LAYER_TEXT[0], tag=member_tag(m, 'code', FL=gname)))

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
                                LAYER_DOWN_COLUMN[0] if down else LAYER_SECTION['column'][0], down,
                                tag=member_tag(m, 'section', FL=gname)))
            mids.setdefault(m['elem'], ('line', j, not down))
        if name_j is not None and wood_sheet != 'beam' and M.members[name_j]['code']:
            # 柱の断面記号の右上へ
            mc = M.members[name_j]
            code = mc['code']
            hx, hy = symbol_half_extent(mc['sec'], mc['beta'])
            gap = LABEL_GAP_RATIO * th
            P.append(NameRef(sections.name_block(code), code,
                             (x + hx + gap + text_width(code, th) / 2.0, y + hy + gap + th / 2.0),
                             th, 0.0, LAYER_TEXT[0], tag=member_tag(mc, 'code', FL=gname)))
        drawn.add(node)

    # 木造の柱伏図: その床から立ち上がる壁
    if wood_sheet == 'column':
        xs = [float(M.node_xyz[k][0]) for k in floor_nodes]
        ys = [float(M.node_xyz[k][1]) for k in floor_nodes]
        cx = sum(xs) / len(xs) if xs else 0.0
        cy = sum(ys) / len(ys) if ys else 0.0
        for corners, ctr, name, ele in rising_walls(M, fz, cx, cy):
            P.append(Poly([(a * 1000.0, b * 1000.0) for a, b in corners], LAYER_WALL[0],
                          tag=wall_tag(ele, name, 'outline', FL=gname)))
            mids.setdefault(int(ele), ('wall', int(ele), True))
            if name:
                P.append(NameRef(sections.name_block(name), name,
                                 (ctr[0] * 1000.0, ctr[1] * 1000.0), th, 0.0, LAYER_TEXT[0],
                                 tag=wall_tag(ele, name, 'code', FL=gname)))

    if not beams and not drawn:
        raise ValueError('伏図 %s に描画できる部材がありません' % gname)

    # 見出し: 床の名前を伏図全体の範囲の上 1m、中央に
    tg = grid_paper_mm * n
    P.append(Text(((gx0 + gx1) / 2.0, gy1 + 1000.0), gname, tg, LAYER_TITLE[0], 'MC',
                  tag={'ROLE': 'title', 'FL': gname}))
    if not wood_sheet or wood_sheet == 'full':
        title = gname
    else:
        title = '%s (%s)' % (gname, {'beam': '梁伏図', 'column': '柱伏図'}[wood_sheet])
    fid = figure_id('plan', gname, wood_sheet)
    stamp(P, fid)
    # 基準点: いちばん左の X 通り × いちばん下の Y 通りの交点 (無ければ伏図全体の範囲の左下)
    grids_meta, origin = plan_grid_meta(glines)
    origin_from = 'grid' if origin else 'extent'
    origin = origin or (gx0, gy0)
    # levels: 軸組図と同じレベル線の一覧 (低い順)。この床は、同じ高さの行が無ければ足す
    # (同じ高さを 2 行にしない。この床の高さは figures[].z)
    lv = [{'name': lb, 'z': z * 1000.0} for lb, z in level_lines(M, levels)]
    if not any(abs(v['z'] - fz * 1000.0) < 1.0 for v in lv):
        lv.append({'name': gname, 'z': fz * 1000.0})
    lv.sort(key=lambda v: v['z'])
    meta = {'name': gname, 'z': fz * 1000.0, 'grids': grids_meta, 'levels': lv,
            'members': mids, 'origin': origin, 'origin_from': origin_from}
    return Figure('plan', key, title, n, prims_bounds(P), P, wood_sheet,
                  frame_box=(gx0, gy0, gx1, gy1), fig_id=fid, meta=meta)


__all__ = ['build_plan', 'rising_walls']
