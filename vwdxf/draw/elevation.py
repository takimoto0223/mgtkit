# -*- coding: utf-8 -*-
"""軸組図 (通りごと). 座標系 (u, Z) [mm]、u = 平面座標を構面の向き dvec へ投影した値.

規則は tools/struct_cad_py の draw/axis_elevation.py と同じ:
  - 構面 = 鉛直構面のグループ。向きはグループ節点の主成分、部材はグループの ELEM_LIST
  - 部材はせい (テーパーは i・j 端のせい) の矩形 + 端部処理 (梁天端 = 節点、end_extension)。
    梁だけを見付の半分下げる。レイヤ: 柱 S-Cyan20 / 梁・斜材 S-Yellow20 / トラス S-Gray09
  - 符号は水平。置き場所は vwdxf で変えた: 梁は天端の上、斜材は外形の上側、柱は左 (部材の真上を避ける)
  - 面外から取り付く梁 (構面に十分直交するもの) の断面記号を節点ごとに 1 つ
  - 壁・面要素 (グループの ELEM_LIST) は外形 + ハッチ + 板厚の符号 (重心) (S-Gray09)
  - 見出しは通りの名前だけを、構面の上 1m の中央に
vwdxf の追加: 通り芯・レベル線にする通りとフロアは画面で選ぶ。文字は pt 指定。
"""
from .. import sections
from ..members import (elevation_section_angle, frame_dir, frame_geometry, group_members,
                       member_ends, plan_dir, section_extent)
from ..style import (LAYER_MEMBER, LAYER_SECTION, LAYER_TEXT, LAYER_TITLE, LAYER_TRUSS,
                     LAYER_WALL_ELEV, WALL_HATCH_PAPER_MM)
from .figure import (LABEL_GAP_RATIO, auto_scale, canon_normal, elevation_grid_prims,
                     frame_common_extent, grid_lines, level_lines, level_prims, text_width,
                     width_rect)
from .primitives import Figure, Hatch, Line, NameRef, Poly, SectionRef, Text, prims_bounds


def build_elevation(M, key, scale=None, paper='A3', text_paper_mm=2.5, grids=None,
                    levels=None, wood=False, grid_paper_mm=6.35):
    """軸組図 1 枚 → Figure. key = 'G:<構面グループ名>'. wood は使わない (壁は常に描く)."""
    dvec, nodes, gname = frame_geometry(M, str(key))
    group = M.groups[gname]
    idx = group_members(M, gname)
    if not idx:
        raise ValueError('通り %s に描画できる部材がありません' % gname)
    xy = [(float(M.node_xyz[k][0]), float(M.node_xyz[k][1])) for k in nodes]
    c = (sum(p[0] for p in xy) / len(xy), sum(p[1] for p in xy) / len(xy))
    u0, span = frame_common_extent(M, dvec, nodes)
    us = [p[0] * dvec[0] + p[1] * dvec[1] for p in xy]
    zs = [float(M.node_xyz[k][2]) for k in nodes]

    def to2d(p):
        return ((float(p[0]) * dvec[0] + float(p[1]) * dvec[1]) * 1000.0, float(p[2]) * 1000.0)

    w_mm = (max(us) - min(us)) * 1000.0
    h_mm = (max(zs) - min(zs)) * 1000.0
    n = int(scale) if scale else auto_scale(w_mm, h_mm, paper)
    th = text_paper_mm * n
    P = []

    # 通り芯とレベル線 (背面)
    P += elevation_grid_prims(M, dvec, c, u0, span, grid_lines(M, grids), n, grid_paper_mm, gname)
    P += level_prims(level_lines(M, levels), u0, span, n, grid_paper_mm)

    # 壁・面要素 (部材の背面)
    eles = set(int(e) for e in group.elem_ids)
    for el in M.ir.elements:
        if el.is_line or el.id not in eles or any(k not in M.node_xyz for k in el.nodes):
            continue
        pts = [to2d(M.node_xyz[k]) for k in el.nodes]
        P.append(Hatch(pts, LAYER_WALL_ELEV[0], WALL_HATCH_PAPER_MM * n))
        P.append(Poly(pts, LAYER_WALL_ELEV[0]))
        name = M.ir.thickness_names.get(el.prop, '')
        if name:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            P.append(NameRef(sections.name_block(name), name, (cx, cy), th, 0.0, LAYER_TEXT[0]))

    # 部材
    for i in idx:
        m = M.members[i]
        p1, p2 = to2d(m['p1']), to2d(m['p2'])
        wi, wj = (v * 1000.0 for v in m['d_ends'])
        w = wi if wi == wj else (wi, wj)
        g_up = -max(wi, wj) / 2.0 if m['kind'] == 'beam' else 0.0    # 梁天端 = 節点
        if m['truss']:
            layer = LAYER_TRUSS[0]
        else:
            layer = LAYER_MEMBER['column' if m['kind'] == 'column' else 'beam'][0]
        e1, e2 = member_ends(M, i, 'top')
        rect = width_rect(p1, p2, w, (e1 * 1000.0, e2 * 1000.0), g_up)
        if rect is None:
            P.append(Line(p1, p2, layer))
        else:
            P.append(Poly(rect, layer))
        if m['code']:
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            L = (dx * dx + dy * dy) ** 0.5
            if L > 0:
                mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
                gap = LABEL_GAP_RATIO * th
                wmax = max(wi, wj)
                if m['kind'] == 'column':
                    # 柱の左へ (高さの中央)
                    pos = (mid[0] - (wmax / 2.0 + gap + text_width(m['code'], th) / 2.0), mid[1])
                else:
                    # 梁は天端 (= 節点の線) の上、斜材・トラスは外形の上側へ
                    nx, ny = canon_normal(dx / L, dy / L)
                    d = (0.0 if m['kind'] == 'beam' else wmax / 2.0) + gap + th / 2.0
                    pos = (mid[0] + nx * d, mid[1] + ny * d)
                P.append(NameRef(sections.name_block(m['code']), m['code'], pos, th, 0.0,
                                 LAYER_TEXT[0]))

    # 面外から取り付く梁の断面記号 (節点ごとに 1 つ)
    in_group = set(idx)
    n_plan = (-dvec[1], dvec[0])
    dvec3d = (dvec[0], dvec[1], 0.0)
    for k in nodes:
        for j in M.node_members.get(k, []):
            if j in in_group:
                continue
            o = M.members[j]
            if o['kind'] != 'beam':
                continue
            pd = plan_dir(o)
            if pd is None or abs(pd[0] * n_plan[0] + pd[1] * n_plan[1]) < 0.5:
                continue
            hw, hv = section_extent(o, dvec3d)
            if hw <= 0 or hv <= 0:
                continue
            u, z = to2d(M.node_xyz[k])
            sym = o['code'] or 'S%d' % o['sec_no']
            P.append(SectionRef(sections.block_name(sym), o['sec'], (u, z - hv * 1000.0),
                                elevation_section_angle(o, dvec), LAYER_SECTION['beam'][0]))
            break

    # 見出し: 通りの名前を構面の上 1m、中央に
    P.append(Text(((min(us) + max(us)) / 2.0 * 1000.0, max(zs) * 1000.0 + 1000.0), gname,
                  grid_paper_mm * n, LAYER_TITLE[0], 'MC'))
    return Figure('axis', str(key), gname, n, prims_bounds(P), P,
                  frame_box=(u0 * 1000.0, min(zs) * 1000.0, (u0 + span) * 1000.0,
                             max(zs) * 1000.0),
                  dirkey=frame_dir(dvec))


__all__ = ['build_elevation']
