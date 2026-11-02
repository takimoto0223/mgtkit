# -*- coding: utf-8 -*-
"""軸組図 (通りごと). 座標系 (u, Z) [mm]、u = 平面座標を構面の向き dvec へ投影した値.

規則は tools/struct_cad_py の draw/axis_elevation.py と同じ:
  - 構面 = 鉛直構面のグループ。向きはグループ節点の主成分、部材はグループの ELEM_LIST
  - 部材はせい (テーパーは i・j 端のせい) の矩形 + 端部処理 (end_extension)。
    梁の高さは画面で選ぶ (既定は梁天端 = 節点で、梁だけを見付の半分下げる)。節点 = 芯 / 下端 /
    天端を節点から ○mm ずらす、と符号・断面番号ごとの指定もできる (MATLAB 版の Glevel と同じ。
    members.beam_shift_fn)。柱の端・符号・面外の梁の断面記号・木造の X も同じ高さに合わせる。レイヤ: 柱 S-Cyan20 / 梁・斜材 S-Yellow20 / トラス S-Gray09
  - 符号は水平。置き場所は vwdxf で変えた: 梁は天端の上、斜材は外形の上側、柱は左 (部材の真上を避ける)
  - 面外から取り付く梁 (構面に十分直交するもの) の断面記号を節点ごとに 1 つ
  - 壁・面要素 (グループの ELEM_LIST) は外形 + ハッチ + 板厚の符号 (重心) (S-Gray09)
  - 見出しは通りの名前だけを、構面の上 1m の中央に
vwdxf の追加: 通り芯・レベル線にする通りとフロアは画面で選ぶ。文字は pt 指定。
"""
from .. import sections
from ..members import (beam_shift_fn, elevation_section_angle, frame_dir, frame_geometry,
                       group_members, member_ends, plan_dir, section_extent)
from ..style import (DASHED_SUFFIX, LAYER_MEMBER, LAYER_SECTION, LAYER_TEXT, LAYER_TITLE,
                     LAYER_TRUSS, LAYER_WALL_ELEV, WALL_HATCH_PAPER_MM)
from .figure import (LABEL_GAP_RATIO, auto_scale, canon_normal, elevation_grid_prims,
                     frame_common_extent, grid_lines, level_lines, level_prims, text_width,
                     width_rect)
from .primitives import Figure, Hatch, Line, NameRef, Poly, SectionRef, Text, prims_bounds
from ..ids import member_floor, member_meta, plain_meta, wall_meta


MIN_BAY_MM = 1.0       # X を付ける区画の最小の幅・高さ [mm] (300 → 1: 狭い区画にも描く。利用者の指定 2026-10-31)


def open_bay_crosses(cols, beams, braces, walls, tol=5.0):
    """壁でない区画 (隣り合う柱の間 × 梁と梁の間) の X の線 → [((u, z), (u, z)), ...] [mm].

    cols   = [(u, z_lo, z_hi, 幅)]、beams = [(u_lo, u_hi, 節点の z, せい[, 天端 z, 下端 z])]、
    braces = [(u, z)] (斜材の中点)、walls = [(u_lo, z_lo, u_hi, z_hi)] (壁・面要素の範囲。(u, z) の点でも可)。
    区画は節点の高さで分ける。天端・下端を省くと 天端 = 節点 (vwdxf の既定)。
    区画に壁が重なる (幅・高さとも区画の半分以上) か、斜材の中点が区画の中にあれば X を付けない。X は柱の内側の面から、下の梁の天端と上の梁の下端の間に引く。
    """
    def _top(bm):
        return bm[4] if len(bm) > 4 else bm[2]

    def _bot(bm):
        return bm[5] if len(bm) > 5 else bm[2] - bm[3]
    out = []
    # 同じ位置で上下につながる柱の部材 (階ごと・節点ごとに分かれたもの) は 1 本の柱にまとめる。
    # 部材ごとに区画を決めると、梁の無い継ぎ目の高さで X が分かれていた (利用者の指摘 2026-11-02: 両側に柱が無い区画は不要)
    merged = []
    for c in sorted(cols, key=lambda c: (round(c[0] / tol), c[1])):
        m = merged[-1] if merged else None
        if m and abs(m[0] - c[0]) <= tol and c[1] <= m[2] + tol:
            merged[-1] = (m[0], m[1], max(m[2], c[2]), max(m[3], c[3]))
        else:
            merged.append(tuple(c))
    cs = sorted(merged, key=lambda c: c[0])
    # 同じ高さで続く梁 (途中の節点で分かれたもの) も 1 本にまとめる。柱から柱まで架かっているかを見るため
    bms = []
    for bm in sorted(beams, key=lambda bm: (round(bm[2] / tol), bm[0])):
        bm = (bm[0], bm[1], bm[2], bm[3], _top(bm), _bot(bm))
        m = bms[-1] if bms else None
        if m and abs(m[2] - bm[2]) <= tol and bm[0] <= m[1] + tol:
            bms[-1] = (m[0], max(m[1], bm[1]), m[2], max(m[3], bm[3]), max(m[4], bm[4]), min(m[5], bm[5]))
        else:
            bms.append(bm)
    beams = bms
    def _panels(a):
        """柱 a の右側の区画: 高さごとに、その高さに柱がある一番近い右の柱を相手にする → [(相手, z_lo, z_hi)]."""
        free = [(a[1], a[2])]          # まだ相手の決まっていない a の高さの範囲
        res = []
        for c in cs:
            if c[0] - a[0] <= tol or not free:
                continue
            nxt = []
            for lo, hi in free:
                ol, oh = max(lo, c[1]), min(hi, c[2])
                if oh - ol > tol:
                    res.append((c, ol, oh))
                    if ol - lo > tol:
                        nxt.append((lo, ol))
                    if hi - oh > tol:
                        nxt.append((oh, hi))
                else:
                    nxt.append((lo, hi))
            free = nxt
        return res

    for a in cs:
        for b, zl, zh in _panels(a):
            ua, ub = a[0], b[0]
            spans = [bm for bm in beams if bm[0] <= ua + tol and bm[1] >= ub - tol and zl - tol <= bm[2] <= zh + tol]
            zs = sorted({zl, zh} | {bm[2] for bm in spans})
            for z0, z1 in zip(zs, zs[1:]):
                if z1 - z0 <= tol:
                    continue
                inside = lambda p: ua + tol < p[0] < ub - tol and z0 + tol < p[1] < z1 - tol

                def covered(w):
                    if len(w) == 2:
                        return inside(w)
                    ou = min(ub, w[2]) - max(ua, w[0])
                    oz = min(z1, w[3]) - max(z0, w[1])
                    return ou >= 0.5 * (ub - ua) and oz >= 0.5 * (z1 - z0)
                if any(covered(w) for w in walls) or any(inside(br) for br in braces):
                    continue
                top = next((bm for bm in spans if abs(bm[2] - z1) <= tol), None)
                low = next((bm for bm in spans if abs(bm[2] - z0) <= tol), None)
                x0, x1 = ua + a[3] / 2.0, ub - b[3] / 2.0
                y0 = max(z0, _top(low)) if low else z0
                y1 = min(z1, _bot(top)) if top else z1
                if x1 - x0 < MIN_BAY_MM or y1 - y0 < MIN_BAY_MM:
                    continue
                out.append(((x0, y0), (x1, y1)))
                out.append(((x0, y1), (x1, y0)))
    return out


def build_elevation(M, key, scale=None, paper='A3', text_paper_mm=2.5, grids=None,
                    levels=None, wood=False, grid_paper_mm=6.35, beam_level=None):
    """軸組図 1 枚 → Figure. key = 'G:<構面グループ名>'.

    wood = True (木造) のとき、壁でない区画に黄色の破線の X を描く (利用者の指定 2026-10-29)。壁は常に描く。
    beam_level = 梁の高さの指定 (members.beam_shift_fn に渡すもの。None = 節点が天端) (利用者の指定 2026-10-31)。
    """
    gfn = beam_shift_fn(beam_level)
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
    wall_ctrs = []
    eles = set(int(e) for e in group.elem_ids)
    for el in M.ir.elements:
        if el.is_line or el.id not in eles or any(k not in M.node_xyz for k in el.nodes):
            continue
        pts = [to2d(M.node_xyz[k]) for k in el.nodes]
        wname = M.ir.thickness_names.get(el.prop, '')
        P.append(Hatch(pts, LAYER_WALL_ELEV[0], WALL_HATCH_PAPER_MM * n,
                       meta=wall_meta(el.id, wname, 'hatch', AX=gname)))
        P.append(Poly(pts, LAYER_WALL_ELEV[0], meta=wall_meta(el.id, wname, 'outline', AX=gname)))
        # 壁の範囲 (u, z の外接矩形)。区画に重なるかで判定する (利用者の指摘 2026-11-02: 柱 2 本をまたぐ W3 の両脇の柱間に X が付いた)
        wall_ctrs.append((min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)))
        name = M.ir.thickness_names.get(el.prop, '')
        if name:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            P.append(NameRef(sections.name_block(name), name, (cx, cy), th, 0.0, LAYER_TEXT[0],
                             meta=wall_meta(el.id, name, 'code', AX=gname)))

    # 部材
    for i in idx:
        m = M.members[i]
        p1, p2 = to2d(m['p1']), to2d(m['p2'])
        wi, wj = (v * 1000.0 for v in m['d_ends'])
        w = wi if wi == wj else (wi, wj)
        # 梁の芯を節点から上へ動かす量 [mm] (既定は 梁天端 = 節点 → -せい/2)
        g_up = gfn(m, max(wi, wj) / 2000.0) * 1000.0 if m['kind'] == 'beam' else 0.0
        if m['truss']:
            layer = LAYER_TRUSS[0]
        else:
            layer = LAYER_MEMBER['column' if m['kind'] == 'column' else 'beam'][0]
        e1, e2 = member_ends(M, i, gfn)
        rect = width_rect(p1, p2, w, (e1 * 1000.0, e2 * 1000.0), g_up)
        mk = dict(FL=member_floor(M, m), AX=gname)
        if rect is None:
            P.append(Line(p1, p2, layer, meta=member_meta(m, 'outline', **mk)))
        else:
            P.append(Poly(rect, layer, meta=member_meta(m, 'outline', **mk)))
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
                    # 梁は天端の上 (既定は 天端 = 節点の線)、斜材・トラスは外形の上側へ
                    nx, ny = canon_normal(dx / L, dy / L)
                    d = (g_up + wmax / 2.0 if m['kind'] == 'beam' else wmax / 2.0) + gap + th / 2.0
                    pos = (mid[0] + nx * d, mid[1] + ny * d)
                P.append(NameRef(sections.name_block(m['code']), m['code'], pos, th, 0.0,
                                 LAYER_TEXT[0], meta=member_meta(m, 'code', **mk)))

    # 木造: 壁でない区画 (隣り合う柱 × 梁と梁の間) に黄色の破線の X
    if wood:
        cols2, beams2, braces2 = [], [], []
        for i in idx:
            m = M.members[i]
            q1, q2 = to2d(m['p1']), to2d(m['p2'])
            wmax = max(m['d_ends']) * 1000.0
            if m['kind'] == 'column' and not m['truss']:
                cols2.append(((q1[0] + q2[0]) / 2.0, min(q1[1], q2[1]), max(q1[1], q2[1]), wmax))
            elif m['kind'] == 'beam' and not m['truss']:
                zc = (q1[1] + q2[1]) / 2.0 + gfn(m, wmax / 2000.0) * 1000.0     # 梁の芯
                beams2.append((min(q1[0], q2[0]), max(q1[0], q2[0]), (q1[1] + q2[1]) / 2.0, wmax,
                               zc + wmax / 2.0, zc - wmax / 2.0))
            else:
                braces2.append(((q1[0] + q2[0]) / 2.0, (q1[1] + q2[1]) / 2.0))
        layer_x = LAYER_MEMBER['beam'][0] + DASHED_SUFFIX
        for a, b in open_bay_crosses(cols2, beams2, braces2, wall_ctrs):
            P.append(Line(a, b, layer_x, 'dashed', meta=plain_meta('open-bay', AX=gname)))

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
            P.append(SectionRef(sections.block_name(sym), o['sec'], (u, z + gfn(o, hv) * 1000.0),
                                elevation_section_angle(o, dvec), LAYER_SECTION['beam'][0],
                                meta=member_meta(o, 'section', FL=member_floor(M, o), AX=gname)))
            break

    # 見出し: 通りの名前を構面の上 1m、中央に
    P.append(Text(((min(us) + max(us)) / 2.0 * 1000.0, max(zs) * 1000.0 + 1000.0), gname,
                  grid_paper_mm * n, LAYER_TITLE[0], 'MC', meta=plain_meta('title', AX=gname)))
    return Figure('axis', str(key), gname, n, prims_bounds(P), P,
                  frame_box=(u0 * 1000.0, min(zs) * 1000.0, (u0 + span) * 1000.0,
                             max(zs) * 1000.0),
                  dirkey=frame_dir(dvec),
                  info={'name': gname, 'title': gname, 'frame_dir': (float(dvec[0]), float(dvec[1]))})


__all__ = ['build_elevation', 'open_bay_crosses']
