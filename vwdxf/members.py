# -*- coding: utf-8 -*-
"""部材の組み立てと、モデルへの問い合わせ (通り・フロア・柱・壁).

規則は tools/struct_cad_py と同じ (members.py / merge.py / draw/floor_plan.py /
draw/axis_elevation.py から移植、2026-09-19)。構造図β由来の規則はここには残していない。

Model IR (read/) から、作図で使う「部材」(member) を組み立てる。member は dict で、
*MEMBER をピンで区切って結合した線要素 1 本に当たる:
  eles (元の要素ID), elem (表示用の要素ID), n1, n2, p1, p2 [m], mid_nodes (途中の通過節点),
  sec_no, sec (Section), sec_name, code (符号), etype (要素タイプ), truss,
  kind ('column' / 'beam' / 'brace'), b (幅), d (せい), d_ends (i・j 端のせい), beta,
  is_round, pin1, pin2
作図の関数 (draw/) はこの StructModel だけを見る。
"""
import math

import numpy as np

from . import sections
from .merge import merge_members
from .read import read_mgt
from .style import DEFAULT_LIMIT_SEC_NO, LEVEL_FLAT_TOL, _NAME_DIM_RE

TRUSS_TYPES = {'TRUSS', 'TENSTR', 'COMPTR', 'CABLE'}
TRUSS_END_FACTOR = -3.0      # トラスは端部を大きく引き込む (struct_cad と同じ)
INCLINE_THR_DEG = 60.0       # 面の傾きがこれ以下 = 伏図 (床・屋根)、超え = 軸組図 (鉛直構面)
FRAME_Z_THR = 1.0            # 軸組図の構面は Z の広がりがこれ以上 [m]
GRID_OVERHANG_M = 0.8        # 通り芯の構造外への出 [m] (struct_cad と同じ)


class StructModel(object):
    """作図用のモデル. ir (Model IR) と、組み立てた members などを持つ."""
    pass


# ---------------------------------------------------------------------------
# 部材の判定・幾何 (struct_cad members.py)
# ---------------------------------------------------------------------------

def classify(p1, p2):
    """柱 / 梁 / 斜材. 水平長 > 高さなら梁、水平長 < 高さ/100 なら柱、それ以外は斜材."""
    xy = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
    z = abs(p1[2] - p2[2])
    if xy > z:
        return 'beam'
    if xy < z / 100.0:
        return 'column'
    return 'brace'


def _axis3(m):
    v = np.asarray(m['p2'], dtype=float) - np.asarray(m['p1'], dtype=float)
    L = float(np.linalg.norm(v))
    return v / L if L > 1e-12 else None


def _section_axes(o, beta_deg=0.0):
    """部材軸 o → 断面の局所軸 (e1=せい方向, e2=幅方向).

    β=0 の基準: 梁・斜材は e2=水平で部材に直交、e1=鉛直面内。柱 (ほぼ鉛直) は e1=x, e2=y。
    そこから β だけ断面内で回す。
    """
    z = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(o, z))) > 0.99:
        e1, e2 = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    else:
        e2 = np.cross(o, z)
        e2 = e2 / np.linalg.norm(e2)
        e1 = np.cross(e2, o)
        e1 = e1 / np.linalg.norm(e1)
    if beta_deg:
        c, s = math.cos(math.radians(beta_deg)), math.sin(math.radians(beta_deg))
        e1, e2 = c * e1 + s * e2, -s * e1 + c * e2
    return e1, e2


def apparent_half(cur, other):
    """相手 other の断面が、cur の軸方向に占める半幅 [m] = D1/2·|c·e1| + D2/2·|c·e2|."""
    c, o = _axis3(cur), _axis3(other)
    if c is None or o is None:
        return 0.0
    d1, d2 = other['d'], other['b']
    if d1 <= 0 and d2 <= 0:
        return 0.0
    e1, e2 = _section_axes(o, other['beta'])
    return d1 / 2.0 * abs(float(np.dot(c, e1))) + d2 / 2.0 * abs(float(np.dot(c, e2)))


def plan_width(m):
    """伏図 (真上) での見付け幅 [m]. 柱は max(せい, 幅)."""
    o = _axis3(m)
    if o is None:
        return 0.0
    d1, d2 = m['d'], m['b']
    if d1 <= 0 and d2 <= 0:
        return 0.0
    ln = math.hypot(o[0], o[1])
    if ln < 1e-9:
        return max(d1, d2)
    n = np.array([-o[1] / ln, o[0] / ln, 0.0])
    e1, e2 = _section_axes(o, m['beta'])
    return d1 * abs(float(np.dot(n, e1))) + d2 * abs(float(np.dot(n, e2)))


def section_extent(m, dvec3d):
    """断面を軸組図の面 (水平 = dvec3d, 鉛直 = z) へ投影した (水平半幅, 鉛直半高) [m]."""
    o = _axis3(m)
    if o is None:
        return (0.0, 0.0)
    d1, d2 = m['d'], m['b']
    if d1 <= 0 and d2 <= 0:
        return (0.0, 0.0)
    e1, e2 = _section_axes(o, m['beta'])
    dv = np.asarray(dvec3d, dtype=float)
    z = np.array([0.0, 0.0, 1.0])
    hw = d1 / 2.0 * abs(float(np.dot(dv, e1))) + d2 / 2.0 * abs(float(np.dot(dv, e2)))
    hv = d1 / 2.0 * abs(float(np.dot(z, e1))) + d2 / 2.0 * abs(float(np.dot(z, e2)))
    return (hw, hv)


def elevation_section_angle(m, dvec):
    """軸組図の断面記号の回転 [deg] = 断面のせい方向 e1 を構面 (水平 dvec, 鉛直 z) へ投影した角度."""
    o = _axis3(m)
    if o is None:
        return 90.0
    e1, _e2 = _section_axes(o, m['beta'])
    ex = e1[0] * dvec[0] + e1[1] * dvec[1]
    return math.degrees(math.atan2(e1[2], ex))


def plan_dir(m):
    v = (np.asarray(m['p2'], dtype=float) - np.asarray(m['p1'], dtype=float))[:2]
    L = float(np.hypot(v[0], v[1]))
    return v / L if L > 1e-12 else None


def end_extension(M, mi, beam_level='center'):
    """部材 mi の (1 端, 2 端) の軸方向の延長量 [m] (struct_cad members.end_extension).

    正 = 端を外へ延ばす / 負 = 手前に引く。
      - 剛接: 取り付く「種類の違う」相手 (柱↔梁・斜材) の投影半幅だけ延ばし、相手の向こうの面まで
        重ねる。柱は梁レベル (beam_level='top' なら梁天端 = 節点) の差を織り込む
      - ピン: 相手 (直交する支持梁も含む) の投影半幅 + 自分のせい/2 だけ手前に引く
      - 相手が無い、または剛で同じ種類 (小梁→大梁) だけなら 0 (節点まで)
    相手はモデル全体から探す (貫通する部材の途中節点も含む。描かないダミー断面の部材も相手には数える:
    struct_cad と同じ)。トラスの -3 倍は呼ぶ側で掛ける。
    """
    me = M.members[mi]
    kind = me['kind']
    own_half = me['d'] / 2.0
    released = (bool(me['pin1']), bool(me['pin2']))
    z1, z2 = float(me['p1'][2]), float(me['p2'][2])
    if z1 != z2:
        order = (1.0, -1.0) if z1 > z2 else (-1.0, 1.0)   # 高い方の端を +1
    else:
        order = (0.0, 0.0)
    my_pd = plan_dir(me)

    def _gup(half):
        if beam_level == 'top':
            return -half
        if beam_level == 'bottom':
            return half
        return 0.0

    ext = [0.0, 0.0]
    for k, node in enumerate((int(me['n1']), int(me['n2']))):
        best = 0.0
        best_sup = 0.0
        for j in M.node_all.get(node, []):
            if j == mi:
                continue
            other = M.all_members[j]
            if other['kind'] == kind:
                if kind != 'beam':
                    continue
                opd = plan_dir(other)
                if my_pd is not None and opd is not None \
                        and abs(float(np.dot(my_pd, opd))) > 0.985:
                    continue          # 平行 (継手) は相手にしない
                best_sup = max(best_sup, apparent_half(me, other))   # 直交 = 支持梁
            else:
                best = max(best, apparent_half(me, other))
        if released[k]:
            supp = max(best, best_sup)
            if supp <= 0:
                ext[k] = 0.0
            elif kind == 'column':
                ext[k] = -(supp + own_half) + _gup(supp) * order[k]
            else:
                ext[k] = -(supp + own_half)
        elif best <= 0:
            ext[k] = 0.0
        elif kind == 'column':
            ext[k] = best + _gup(best) * order[k]
        else:
            ext[k] = best
    return ext[0], ext[1]


def member_ends(M, mi, beam_level):
    """作図に使う端の延長量 [m]: end_extension、トラスはその -3 倍."""
    e1, e2 = end_extension(M, mi, beam_level)
    if M.members[mi]['truss']:
        return TRUSS_END_FACTOR * e1, TRUSS_END_FACTOR * e2
    return e1, e2


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def build_struct_model(path, limit_sec_no=DEFAULT_LIMIT_SEC_NO):
    """mgt / mgtx → StructModel (部材の組み立てまで)."""
    ir = read_mgt(path)
    M = StructModel()
    M.path = path
    M.ir = ir
    M.limit_sec_no = float(limit_sec_no)
    M.groups = ir.groups
    M.node_xyz = {nid: np.array([n.x, n.y, n.z], dtype=float) for nid, n in ir.nodes.items()}
    M.sec_names = {sid: (s.name or '').replace(' ', '') for sid, s in ir.sections.items()}

    lines, releases, covered, origin = merge_members(ir)
    members = []
    dummies = []      # ダミー断面の部材 (描かない。端部処理の相手にだけ数える)
    bad_dims = set()
    for el in lines:
        n1, n2 = int(el.nodes[0]), int(el.nodes[1])
        if n1 not in M.node_xyz or n2 not in M.node_xyz:
            continue
        sec = ir.sections.get(el.prop)
        dummy = sections.is_dummy(sec, M.limit_sec_no)
        p1, p2 = M.node_xyz[n1], M.node_xyz[n2]
        d, b = sections.depth(sec), sections.width(sec)
        if d <= 0 and b <= 0 and not dummy:
            bad_dims.add(el.prop)
        cov = covered.get(el.id, [n1, n2])
        rel = releases.get(el.id, (False, False))
        (dummies if dummy else members).append({
            'eles': list(origin.get(el.id, [el.id])), 'elem': el.id,
            'n1': n1, 'n2': n2, 'p1': p1, 'p2': p2,
            'mid_nodes': [int(n) for n in cov if int(n) not in (n1, n2)],
            'sec_no': int(el.prop), 'sec': sec,
            'sec_name': M.sec_names.get(el.prop, str(el.prop)),
            'code': sections.symbol(sec),
            'etype': el.type, 'truss': el.type in TRUSS_TYPES,
            'kind': classify(p1, p2),
            'b': b, 'd': d, 'd_ends': sections.depth_ends(sec), 'beta': float(el.angle),
            'is_round': bool(sec is not None and sec.shape in sections.ROUND_SHAPES),
            'pin1': bool(rel[0]), 'pin2': bool(rel[1]),
        })
    if bad_dims:
        print('注意: 断面寸法を取得できない断面は線で描きます: 断面番号 '
              + ', '.join(str(s) for s in sorted(bad_dims)[:10]))
    _warn_swapped_names(members)
    M.members = members
    node_members = {}
    for i, m in enumerate(members):
        for n in [m['n1'], m['n2']] + m['mid_nodes']:
            node_members.setdefault(int(n), []).append(i)
    M.node_members = node_members
    M.all_members = members + dummies      # 先頭は members と同じ番号
    node_all = {}
    for i, m in enumerate(M.all_members):
        for n in [m['n1'], m['n2']] + m['mid_nodes']:
            node_all.setdefault(int(n), []).append(i)
    M.node_all = node_all
    return M


def _warn_swapped_names(members):
    """断面名の寸法 (幅x せい) と登録値の食い違いを知らせる."""
    swapped, seen = [], set()
    for m in members:
        if m['sec_no'] in seen or m['is_round']:
            continue
        seen.add(m['sec_no'])
        mm = _NAME_DIM_RE.search(m['sec_name'])
        if not mm:
            continue
        a, bdim = float(mm.group(1)), float(mm.group(2))
        wb, wd = round(m['b'] * 1000), round(m['d'] * 1000)
        if abs(a - bdim) < 0.5:
            continue
        if abs(a - wb) < 0.5 and abs(bdim - wd) < 0.5:
            continue
        if abs(a - wd) < 0.5 and abs(bdim - wb) < 0.5:
            swapped.append('%s (登録: 幅%d×せい%d)' % (m['sec_name'], wb, wd))
    if swapped:
        print('mgt記述の注意: 断面名の寸法 (幅x せい) と登録値 (B=幅, H=せい)'
              ' が逆になっている疑いがあります。mgtの断面登録 (H/B) を'
              '確認してください (描画は登録値に従うため向きが90度違って'
              '見えます): ' + ', '.join(swapped[:8]))


def _member_in_group(m, eles):
    return any(int(e) in eles for e in m['eles'])


def group_members(M, gname):
    """グループの ELEM_LIST に入っている部材の番号 (結合した部材は元の要素が 1 つでも入っていれば)."""
    eles = set(int(e) for e in M.groups[gname].elem_ids)
    return [i for i, m in enumerate(M.members) if _member_in_group(m, eles)]


def ensure_surfaces(M):
    """面要素 (PLATE / WALL) と板厚表 → M._surfaces [(要素, 厚さID, 節点)], M._thickness {ID: (厚[m], 名称)}."""
    if not hasattr(M, '_surfaces'):
        ir = M.ir
        M._surfaces = [(e.id, e.prop, list(e.nodes)) for e in ir.elements
                       if not e.is_line and len(e.nodes) >= 3]
        M._thickness = {tid: (t, ir.thickness_names.get(tid, ''))
                        for tid, t in ir.thicknesses.items()}


# ---------------------------------------------------------------------------
# グループ: 軸組図の構面・伏図の床 (struct_cad axis_elevation.py / floor_plan.py)
# ---------------------------------------------------------------------------

def _group_nodes(M, gname):
    return [int(n) for n in M.groups[gname].node_ids if int(n) in M.node_xyz]


def plane_inclination(M, node_ids):
    """節点群にフィットした面の水平からの傾き [deg] (0 = 水平, 90 = 鉛直)."""
    pts = np.array([M.node_xyz[n] for n in node_ids if n in M.node_xyz], dtype=float)
    if len(pts) == 0:
        return 0.0
    zr = float(np.ptp(pts[:, 2]))
    if len(pts) < 3:
        return 90.0 if zr >= 1e-6 else 0.0
    cov = np.cov((pts - pts.mean(axis=0)).T)
    evals, evecs = np.linalg.eigh(cov)
    if evals[1] <= 1e-9 * max(evals[2], 1e-12):
        return 90.0 if zr >= 1e-6 else 0.0
    normal = evecs[:, 0]
    nz = abs(normal[2]) / (np.linalg.norm(normal) or 1.0)
    return float(math.degrees(math.acos(min(1.0, nz))))


def _zrange(M, node_ids):
    zs = [float(M.node_xyz[n][2]) for n in node_ids if n in M.node_xyz]
    return (max(zs) - min(zs)) if zs else 0.0


def axis_direction(pts):
    """平面点群の主方向 (単位ベクトル、右 / 上向き). X 通り → (0,1)、Y 通り → (1,0)."""
    n = len(pts)
    if n == 0:
        return (1.0, 0.0)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    cxx = sum((p[0] - mx) ** 2 for p in pts) / n
    cyy = sum((p[1] - my) ** 2 for p in pts) / n
    cxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / n
    theta = 0.5 * math.atan2(2 * cxy, cxx - cyy)
    dx, dy = math.cos(theta), math.sin(theta)
    if abs(dx) < 1e-9:
        dx = 0.0
    if abs(dy) < 1e-9:
        dy = 0.0
    if dx < 0 or (dx == 0 and dy < 0):
        dx, dy = -dx, -dy
    return (dx, dy)


def is_frame_group(M, gname):
    nodes = _group_nodes(M, gname)
    return (len(nodes) >= 2 and plane_inclination(M, nodes) > INCLINE_THR_DEG
            and _zrange(M, nodes) >= FRAME_Z_THR)


def is_floor_group(M, gname):
    g = M.groups[gname]
    if not g.elem_ids:
        return False
    nodes = _group_nodes(M, gname)
    if len(nodes) < 2 or plane_inclination(M, nodes) > INCLINE_THR_DEG:
        return False
    eles = set(int(e) for e in g.elem_ids)
    return any(e.is_line and e.id in eles for e in M.ir.elements)


def auto_frames(M):
    """軸組図の候補 = 鉛直構面のグループ (ELEM_LIST があり、面の傾き > 60°、Z の広がり ≥ 1m)."""
    if not hasattr(M, '_frames'):
        out = []
        for gname, g in M.groups.items():
            if not g.elem_ids or not is_frame_group(M, gname):
                continue
            n_col = sum(1 for i in group_members(M, gname) if M.members[i]['kind'] == 'column')
            out.append({'key': 'G:' + gname, 'label': gname, 'n_col': n_col})
        M._frames = out
    return M._frames


def frame_label(M, key):
    return key[2:] if str(key).startswith('G:') else str(key)


def frame_geometry(M, key):
    """軸組図キー → (向き dvec (2,), 構面の節点 list, グループ名)."""
    gname = str(key)[2:] if str(key).startswith('G:') else str(key)
    if gname not in M.groups:
        raise ValueError('グループ %s がありません' % gname)
    nodes = _group_nodes(M, gname)
    if len(nodes) < 2:
        raise ValueError('構面 %s の節点が不足しています (NODE_LIST を確認してください)' % gname)
    d = axis_direction([(float(M.node_xyz[n][0]), float(M.node_xyz[n][1])) for n in nodes])
    return np.array(d, dtype=float), nodes, gname


def frame_dir(dvec):
    """'x' = X 通りの構面 (水平 = Y 方向) / 'y' = Y 通りの構面 / None = 斜め."""
    if abs(dvec[0]) < 0.1:
        return 'x'
    if abs(dvec[1]) < 0.1:
        return 'y'
    return None


def frame_dir_extents(M):
    """向きごとの共通原点と幅 {'x': (u0, span), 'y': ...} [m] (同じ向きの構面をそろえる)."""
    if not hasattr(M, '_frame_ext'):
        lo, hi = {}, {}
        for f in auto_frames(M):
            try:
                dvec, nodes, _g = frame_geometry(M, f['key'])
            except ValueError:
                continue
            d = frame_dir(dvec)
            if d is None:
                continue
            us = [float(M.node_xyz[n][0]) * dvec[0] + float(M.node_xyz[n][1]) * dvec[1]
                  for n in nodes]
            lo[d] = min(lo.get(d, min(us)), min(us))
            hi[d] = max(hi.get(d, max(us)), max(us))
        M._frame_ext = {d: (lo[d], hi[d] - lo[d]) for d in lo}
    return M._frame_ext


def group_floor_nodes(M, gname):
    """床グループの節点. NODE_LIST が空なら ELEM_LIST の部材の節点で代える."""
    nodes = _group_nodes(M, gname)
    if nodes:
        return nodes
    out = set()
    for i in group_members(M, gname):
        m = M.members[i]
        out.update([m['n1'], m['n2']] + m['mid_nodes'])
    return [n for n in out if n in M.node_xyz]


def plan_keys(M):
    """伏図の候補 = 床・屋根のグループ (ELEM_LIST に線要素があり、面の傾き ≤ 60°). 低い順."""
    if not hasattr(M, '_plans'):
        names = [n for n in M.groups if is_floor_group(M, n)]
        names.sort(key=lambda n: plan_key_z(M, 'G:' + n))
        M._plans = [{'key': 'G:' + n, 'label': n} for n in names]
    return [dict(p) for p in M._plans]


def plan_levels(M):
    return [plan_key_z(M, p['key']) for p in plan_keys(M)]


def plan_key_z(M, key):
    """伏図キー → 床レベル Z [m] = 節点 Z の平均 (struct_cad と同じ)."""
    gname = str(key)[2:] if str(key).startswith('G:') else str(key)
    zs = [float(M.node_xyz[n][2]) for n in group_floor_nodes(M, gname)]
    if not zs:
        raise ValueError('グループ %s に節点がありません' % gname)
    return sum(zs) / len(zs)


def plan_key_is_flat(M, key):
    """水平な床か (勾配屋根などはレベル線の既定から外す)."""
    gname = str(key)[2:] if str(key).startswith('G:') else str(key)
    return _zrange(M, group_floor_nodes(M, gname)) < LEVEL_FLAT_TOL


def floor_levels(M):
    """軸組図の通り芯の上下端に使う床レベル [m] (Z の広がりが 1m 未満のグループの平均 Z)."""
    if not hasattr(M, '_floor_levels'):
        zs = []
        for gname in M.groups:
            nodes = _group_nodes(M, gname)
            if nodes and _zrange(M, nodes) < FRAME_Z_THR:
                zs.append(sum(float(M.node_xyz[n][2]) for n in nodes) / len(nodes))
        M._floor_levels = sorted(zs)
    return M._floor_levels


def plans_extent(M):
    """伏図の候補全体の平面範囲 (x0, y0, x1, y1) [m] (全階で通り芯・原点をそろえる)."""
    if not hasattr(M, '_plans_ext'):
        xs, ys = [], []
        for p in plan_keys(M):
            for n in group_floor_nodes(M, p['key'][2:]):
                xs.append(float(M.node_xyz[n][0]))
                ys.append(float(M.node_xyz[n][1]))
        M._plans_ext = (min(xs), min(ys), max(xs), max(ys)) if xs else None
    return M._plans_ext


def wood_top_z(M):
    """木造の最上階レベル = 伏図候補の最大 Z."""
    zs = [plan_key_z(M, p['key']) for p in plan_keys(M)]
    return max(zs) if zs else None


def wood_plan_sheets(M, key, top_z=None):
    """木造での伏図: 最上階は ['full']、それ以外は ['beam' (梁伏図), 'column' (柱伏図)]."""
    if top_z is None:
        top_z = wood_top_z(M)
    z = plan_key_z(M, key)
    if top_z is None or z >= top_z - 1e-9:
        return ['full']
    return ['beam', 'column']


def columns_at_node(M, node, floor_z):
    """床節点に置く柱 (struct_cad _columns_at_node) → (specs [(部材番号, down)], 符号を付ける部材 or None).

      - 通しの柱が節点を貫通 → その柱 1 本 (上柱扱い)
      - 上柱と下柱が別部材で、どちらかがその節点でピン → 上柱 + 下柱
      - 剛で分かれているだけ → 上柱のみ / 片側しか無ければその側 (下柱は × 印)
    """
    up, down, through = [], [], None
    for j in M.node_members.get(int(node), []):
        m = M.members[j]
        if m['kind'] != 'column':
            continue
        if node in (m['n1'], m['n2']):
            far = m['p2'] if m['n1'] == node else m['p1']
            if far[2] > floor_z + 1e-6:
                up.append(j)
            elif far[2] < floor_z - 1e-6:
                down.append(j)
        else:
            zs = [float(M.node_xyz[n][2]) for n in [m['n1'], m['n2']] + m['mid_nodes']]
            if any(z > floor_z + 1e-6 for z in zs) and any(z < floor_z - 1e-6 for z in zs):
                through = j
    if through is not None:
        return [(through, False)], through

    def released(j):
        m = M.members[j]
        return m['pin1'] if m['n1'] == node else m['pin2']

    pin_split = bool(up) and bool(down) and any(released(j) for j in up + down)
    specs, name_j = [], None
    if up:
        specs.append((up[0], False))
        name_j = up[0]
    if down and (pin_split or not up):
        specs.append((down[0], True))
    return specs, name_j


def _note_once(M, msg):
    """図ごとに同じ注意が並ばないよう、モデル単位で 1 回だけ出す."""
    seen = getattr(M, '_notes_seen', None)
    if seen is None:
        seen = M._notes_seen = set()
    if msg not in seen:
        seen.add(msg)
        print(msg)


__all__ = [
    'GRID_OVERHANG_M', 'StructModel', 'TRUSS_END_FACTOR', 'TRUSS_TYPES',
    '_member_in_group', '_note_once', 'apparent_half', 'auto_frames', 'axis_direction',
    'build_struct_model', 'classify', 'columns_at_node', 'elevation_section_angle',
    'end_extension', 'ensure_surfaces', 'floor_levels', 'frame_dir', 'frame_dir_extents',
    'frame_geometry', 'frame_label', 'group_floor_nodes', 'group_members', 'member_ends',
    'plan_dir', 'plan_key_is_flat', 'plan_key_z', 'plan_keys', 'plan_levels', 'plan_width',
    'plane_inclination', 'plans_extent', 'section_extent', 'wood_plan_sheets', 'wood_top_z',
]
