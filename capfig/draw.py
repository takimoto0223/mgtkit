# -*- coding: utf-8 -*-
"""断面符号図 (MIDAS風) の作図.

1ページ = 1図 (伏図1枚 または 軸組図1枚)。紙面の構成:

    ┌──────────────────────────────────────────┐
    │ 伏図  2F1 (Z=3.245m)            断面符号図 │  ← 表題
    │┌──────────────────────────────┐┌──────┐│
    ││  (X1)   (X2)   (X3)            ││断面一覧││
    ││   ┊ WG1  ┊  WG1 ┊             ││ ─ WG1 ││
    ││ ──┼──────┼──────┼──  ← 部材色=  ││ ─ WC3 ││
    ││   ┊      ┊      ┊     SECT-COLOR││       ││
    ││ Y                              ││       ││
    ││ └X                             │└──────┘│
    │└──────────────────────────────┘ 作図情報 │
    └──────────────────────────────────────────┘

座標は mgt の値 (m) をそのまま使い、縦横の縮尺は等しくする。
"""

import datetime
import math
import os
import re

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Circle, Polygon, Rectangle, FancyArrowPatch

from mgtkit.mgt import (mgtopen_node, mgtopen_element, mgtopen_plate,
                        mgtopen_thickness, mgtopen_group, mgtopen_wall,
                        _lines)
from mgtkit.draw_model import _setup_japanese_font

#: 用紙 [mm] (横向き)
PAPERS = {2: (594.0, 420.0), 3: (420.0, 297.0), 4: (297.0, 210.0)}
_MM = 1.0 / 25.4

#: 伏図の水平な板要素 (スラブ) の塗りの不透明度
_HPLATE_ALPHA = 0.3

#: SECT-COLOR が無い断面の色 (MIDAS の既定色に近い濃いめの色)
_FALLBACK = ['#1f4e9a', '#b3261e', '#1d7a3a', '#7b3fa0', '#b86e00',
             '#0f7d86', '#8a2b5a', '#4d5a12', '#5b4636', '#2f2f8f']


# ---------------------------------------------------------------------------
# mgt 読み込み (既存パーサ + 断面名・断面色)
# ---------------------------------------------------------------------------

def _block(lines, marker):
    """'*MARKER' 行の次から、次の '*' 行の手前までのデータ行 (コメント除く)."""
    out, on = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith('*'):
            if on:
                break
            on = (s.split(';')[0].strip().upper() == marker)
            continue
        if on and s and not s.startswith(';'):
            out.append(s)
    return out


def _section_names(lines):
    """*SECTION から {断面番号: 断面名}。

    寸法の復元は不要なので section.mgtopen_section (DB名の復元に失敗すると
    例外になる) は使わず、1行目の iSEC, TYPE, SNAME だけを読む。
    2行目以降 (数値行・'DB, NAME1, NAME2' 行) は先頭が整数でないので除外される。
    """
    names = {}
    for s in _block(lines, '*SECTION'):
        f = [x.strip() for x in s.split(',')]
        if len(f) < 3 or not re.fullmatch(r'\d+', f[0]):
            continue
        if re.fullmatch(r'[-+.\deE]+', f[1] or '0'):
            continue
        names[int(f[0])] = f[2]
    return names


#: 円形の断面形状 (真正面から見ると円に描く)
_ROUND = {'P', 'SR'}


def _num_list(fields):
    out = []
    for f in fields:
        try:
            out.append(float(f))
        except ValueError:
            break
    return out


def _section_geom(lines):
    """*SECTION から {断面番号: {'shape', 'H', 'B', 'off'}} (寸法は m).

    DBUSER / VALUE の1行目は ... , SHAPE, 1|2, ... の並び:
      2 (数値入力): D1=せい H, D2=幅 B (P・SR は D1=直径)
      1 (DB名指定): 'H 400x200x8/13' 等の名前から H, B [mm] を読む
    SRC は2行目の D1, D2 (外形)。読めない断面は含めない (線で描く)。
    オフセットは [OFFSET] の先頭 (CC, CT, LB …) のみ使う。
    """
    geo = {}
    rows = _block(lines, '*SECTION')
    for k, s in enumerate(rows):
        f = [x.strip() for x in s.split(',')]
        if len(f) < 14 or not re.fullmatch(r'\d+', f[0]):
            continue
        sec, typ = int(f[0]), f[1].upper()
        off = f[3].upper() if len(f[3]) == 2 else 'CC'
        shape = f[12].upper()
        H = B = None
        try:
            if typ == 'SRC':
                d = _num_list([x.strip() for x in rows[k + 1].split(',')])
                if len(d) >= 2:
                    H, B = d[0], d[1]
            elif f[13] == '2':
                d = _num_list(f[14:])
                if d:
                    H = d[0]
                    B = d[0] if shape in _ROUND else (d[1] if len(d) > 1
                                                      else d[0])
            elif f[13] == '1' and len(f) > 15:
                d = [float(v) for v in re.findall(r'\d+(?:\.\d+)?', f[15])]
                if d:
                    H = d[0] / 1000.0
                    B = H if shape in _ROUND else (d[1] / 1000.0
                                                   if len(d) > 1 else H)
        except (IndexError, ValueError):
            H = None
        if H and B and H > 0 and B > 0:
            geo[sec] = dict(shape=shape, H=H, B=B, off=off)
    return geo


def _members(lines):
    """*MEMBER (MIDAS の単一部材指定) -> [[要素番号, ...], ...].

    書式: iKEY, ELEM, bREVERSE, AELEM1, AELEM2, ...  (行末 '\\' で継続)
    """
    out, buf = [], ''
    for s in _block(lines, '*MEMBER'):
        buf += s
        if buf.endswith('\\'):
            buf = buf[:-1] + ','
            continue
        f = [x.strip() for x in buf.split(',') if x.strip()]
        buf = ''
        try:
            els = [int(f[1])] + [int(v) for v in f[3:]]
        except (IndexError, ValueError):
            continue
        if len(els) >= 2:
            out.append(els)
    return out


def _colors(lines, marker):
    """*SECT-COLOR / *THIK-COLOR から {番号: (線色, 面色, 面の縁色)} (RGB 0-1)."""
    out = {}
    for s in _block(lines, marker):
        f = [x.strip() for x in s.split(',')]
        try:
            no = int(f[0])
            w = tuple(int(v) / 255.0 for v in f[1:4])
            hf = tuple(int(v) / 255.0 for v in f[4:7])
            he = tuple(int(v) / 255.0 for v in f[7:10])
        except (ValueError, IndexError):
            continue
        out[no] = (w, hf, he)
    return out


def _readable(rgb):
    """白い紙で読めるよう、明るすぎる色を暗くする."""
    r, g, b = rgb
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum <= 0.62:
        return (r, g, b)
    k = 0.62 / lum
    return (r * k, g * k, b * k)


def load_model(mgt_path):
    """作図に必要なデータをまとめて読む."""
    lines = _lines(mgt_path)
    node = mgtopen_node(mgt_path)
    pos = {int(r[0]): np.array([float(r[1]), float(r[2]), float(r[3])])
           for r in np.atleast_2d(node)}

    # 線材: [要素, 材料, 断面, i, j]
    lines_el, beta = {}, {}
    el = mgtopen_element(mgt_path)
    if np.size(el):
        el = np.atleast_2d(el)
        for r in el:
            ni, nj = int(r[3]), int(r[4])
            if ni in pos and nj in pos:
                lines_el[int(r[0])] = (int(r[2]), ni, nj)
                beta[int(r[0])] = float(r[5]) if el.shape[1] > 5 else 0.0

    # 面材 (板・壁): {要素: (厚さID, [節点...])}
    planar = {}
    pl = mgtopen_plate(mgt_path)
    if np.size(pl):
        for r in np.atleast_2d(pl):
            nds = [int(v) for v in r[3:7] if int(v) != 0 and int(v) in pos]
            if len(nds) >= 3:
                planar[int(r[0])] = (int(r[2]), nds)
    try:
        _we, wall_base = mgtopen_wall(mgt_path, node)
    except Exception:  # noqa: BLE001  壁が無いモデルでは読めないことがある
        wall_base = np.array([])
    if np.size(wall_base):
        for r in np.atleast_2d(wall_base):
            nds = [int(v) for v in r[3:7] if int(v) != 0 and int(v) in pos]
            if len(nds) >= 3:
                planar[int(r[0])] = (int(r[2]), nds)

    thk = {}
    th = mgtopen_thickness(mgt_path)
    if np.size(th):
        for r in np.atleast_2d(th):
            thk[int(r[0])] = float(r[1])

    g_name, g_elem, g_node = mgtopen_group(mgt_path)
    groups = {}
    for i, nm in enumerate(g_name):
        groups[str(nm).strip()] = (
            {int(v) for v in np.atleast_1d(np.asarray(g_elem[i],
                                                      dtype=float)).ravel()},
            {int(v) for v in np.atleast_1d(np.asarray(g_node[i],
                                                      dtype=float)).ravel()})

    # 節点ごとの線材の本数 (分割要素をまとめるときの切れ目判定)
    deg = {}
    for _sec, ni, nj in lines_el.values():
        deg[ni] = deg.get(ni, 0) + 1
        deg[nj] = deg.get(nj, 0) + 1

    return dict(path=mgt_path, pos=pos, lines=lines_el, planar=planar,
                thk=thk, groups=groups, deg=deg, beta=beta,
                sec_name=_section_names(lines),
                sec_geom=_section_geom(lines),
                sec_color=_colors(lines, '*SECT-COLOR'),
                thk_color=_colors(lines, '*THIK-COLOR'),
                members=_members(lines))


# ---------------------------------------------------------------------------
# グループの幾何 (通り芯の直線・階レベル)
# ---------------------------------------------------------------------------

def _group_pts(M, name):
    _el, nds = M['groups'].get(name, (set(), set()))
    return np.array([M['pos'][n] for n in nds if n in M['pos']])


def group_line(M, name):
    """鉛直グループの平面上の直線 (中心 c, 単位方向 d)。直線でなければ None.

    d の向きは MIDAS の立面表示に合わせる: X方向の構面は +X が右、
    Y方向の構面は +Y が右 (いずれも正面から見る向き)。
    """
    p = _group_pts(M, name)
    if p.shape[0] < 2:
        return None
    xy = p[:, :2]
    c = xy.mean(axis=0)
    q = xy - c
    if np.max(np.linalg.norm(q, axis=1)) < 1e-6:
        return None  # 平面上で1点 (柱1本だけの構面など)
    _u, s, vt = np.linalg.svd(q, full_matrices=False)
    d = vt[0]
    off = np.abs(q @ np.array([-d[1], d[0]]))
    if off.max() > 0.05:  # 5cm 以上ずれる点がある -> 直線状の構面ではない
        return None
    if abs(d[0]) >= abs(d[1]):
        d = d if d[0] > 0 else -d
    else:
        d = d if d[1] > 0 else -d
    return c, d


def floor_levels(M):
    """床のグループ -> [(Z, 名前)] (Z昇順).

    平面的に広がるグループのうち、節点の半数以上が同じ高さ (1mm単位) に
    あるものをその高さの床とみなす (段差のある床は主たるレベル、
    勾配屋根のように高さがばらつくものは対象外)。
    """
    out = []
    for nm in M['groups']:
        p = _group_pts(M, nm)
        if p.shape[0] < 3 or group_line(M, nm) is not None:
            continue
        xy = p[:, :2]
        if np.ptp(xy[:, 0]) <= 0.01 or np.ptp(xy[:, 1]) <= 0.01:
            continue
        zs, cnt = np.unique(np.round(p[:, 2], 3), return_counts=True)
        k = int(np.argmax(cnt))
        if cnt[k] >= 0.5 * p.shape[0]:
            out.append((float(zs[k]), nm))
    return sorted(out)


def floor_z(M, name):
    """伏図の表題に書く高さ (主たるレベル)。無ければ中央値."""
    for z, nm in floor_levels(M):
        if nm == name:
            return z
    p = _group_pts(M, name)
    return float(np.median(p[:, 2])) if p.size else 0.0


def _dir_label(d):
    if abs(d[1]) < 1e-3:
        return 'X'
    if abs(d[0]) < 1e-3:
        return 'Y'
    return '%.2fX%+.2fY' % (d[0], d[1])


# ---------------------------------------------------------------------------
# 図の中身 (1ビュー)
# ---------------------------------------------------------------------------

class View:
    """1図ぶんの投影と対象要素."""

    def __init__(self, kind, name, title, proj, line_ids, planar_ids,
                 columns=(), grids=(), levels=(), axes=('X', 'Y')):
        self.kind = kind            # 'plan' / 'elev'
        self.name = name
        self.title = title
        self.proj = proj            # xyz -> (u, v)
        self.line_ids = list(line_ids)
        self.planar_ids = list(planar_ids)
        self.columns = list(columns)  # 伏図に点で描く柱 (要素番号, 平面位置の節点)
        self.grids = list(grids)      # [(符号, (u0,v0), (u1,v1))]
        self.levels = list(levels)    # [(v, ラベル)]  (軸組図のみ)
        self.axes = axes              # 座標軸の表示名 (横, 縦)
        self.toward = np.array([0.0, 0.0, 1.0])  # 見る人の側 (奥行きの並べ替え)


def is_horizontal_planar(M, e, tol=1e-3):
    """面材 e が水平 (全節点が同じ高さ) か."""
    zs = [M['pos'][n][2] for n in M['planar'][e][1]]
    return max(zs) - min(zs) <= tol


def floor_plates(M, name):
    """伏図 name と同じ高さにある水平な板要素 (グループ未登録のものも含む).

    床グループの節点の高さ (1mm 単位) のどれかに載る水平な板要素を返す。
    MIDAS では床スラブを床グループに入れていないことが多いため。
    床グループが建物の一部 (梁のある範囲) だけでも、同じ高さの床は
    すべて描く。
    """
    p = _group_pts(M, name)
    if not p.size:
        return []
    levels = set(np.round(p[:, 2], 3))
    out = []
    for e, (_t, nds) in M['planar'].items():
        zs = [M['pos'][n][2] for n in nds]
        if max(zs) - min(zs) <= 1e-3 and round(float(zs[0]), 3) in levels:
            out.append(e)
    return out


def plan_view(M, name, show_columns=True, grid_names=(), show_hplates=True):
    g_el, g_nd = M['groups'][name]
    line_ids = [e for e in g_el if e in M['lines']]
    planar_ids = [e for e in g_el if e in M['planar']]
    if show_hplates:
        have = set(planar_ids)
        planar_ids += [e for e in floor_plates(M, name) if e not in have]
    else:
        planar_ids = [e for e in planar_ids
                      if not is_horizontal_planar(M, e)]
    z = floor_z(M, name)
    proj = (lambda xyz: (xyz[0], xyz[1]))

    cols = []
    if show_columns:
        # 床の節点で上端が止まる鉛直材 = その階の柱 (下階の柱)
        on_floor = set(g_nd)
        for e in line_ids:
            on_floor.update(M['lines'][e][1:])
        for e, (sec, ni, nj) in M['lines'].items():
            if e in g_el:
                continue
            a, b = M['pos'][ni], M['pos'][nj]
            dz = abs(a[2] - b[2])
            if dz < 1e-6 or math.hypot(a[0] - b[0], a[1] - b[1]) > dz * 0.01:
                continue
            top = ni if a[2] > b[2] else nj
            if top in on_floor:
                cols.append((e, top))

    grids = []
    for gname in grid_names:
        if gname == name:
            continue
        ln = group_line(M, gname)
        if ln is not None:
            grids.append((gname, ln))
    title = '伏図　%s　(Z = %.3f m)' % (name, z)
    v = View('plan', name, title, proj, line_ids, planar_ids, cols,
             axes=('X', 'Y'))
    v._grid_lines = grids
    return v


def elev_view(M, name, grid_names=(), show_levels=True):
    g_el, _g_nd = M['groups'][name]
    ln = group_line(M, name)
    if ln is None:
        raise ValueError('グループ %s は平面上で直線に並んでいないため、'
                         '軸組図にできません (伏図として選んでください)。'
                         % name)
    c, d = ln
    proj = (lambda xyz, d=d: (xyz[0] * d[0] + xyz[1] * d[1], xyz[2]))
    line_ids = [e for e in g_el if e in M['lines']]
    planar_ids = [e for e in g_el if e in M['planar']]

    # 交差する通り芯 -> 立面上の縦線
    grids = []
    for gname in grid_names:
        if gname == name:
            continue
        l2 = group_line(M, gname)
        if l2 is None:
            continue
        c2, d2 = l2
        cr = d[0] * d2[1] - d[1] * d2[0]
        if abs(cr) < 0.2:  # ほぼ平行 (約11.5°未満) は交差しない扱い
            continue
        # c + a d = c2 + b d2
        A = np.array([[d[0], -d2[0]], [d[1], -d2[1]]])
        a, _b = np.linalg.solve(A, c2 - c)
        P = c + a * d
        grids.append((gname, float(P @ d)))
    levels = []
    if show_levels:
        for zl, nm in floor_levels(M):
            levels.append((zl, nm))
    v = View('elev', name, '軸組図　' + name, proj, line_ids, planar_ids,
             axes=(_dir_label(d), 'Z'))
    v.toward = np.array([d[1], -d[0], 0.0])  # 右=d, 上=Z のとき手前 = d×Z
    v._grid_s = grids
    v._levels_all = levels
    return v


# ---------------------------------------------------------------------------
# 注記のまとめ (分割要素 -> 1部材)
# ---------------------------------------------------------------------------

def _find(par, x):
    while par[x] != x:
        par[x] = par[par[x]]
        x = par[x]
    return x


def line_chains(M, ids, merge=True):
    """同一断面で、途中に他の部材が取り付かない要素をまとめる.

    分割しただけの節点 (線材が2本だけ集まる節点) でつながり、折れ角が
    45°未満のものを1部材とする。曲線梁 (円弧を分割した梁) も1部材になる。
    MIDAS で単一部材 (*MEMBER) に指定した要素は、途中に他の部材が
    取り付いていても同じ断面どうしを1部材にまとめ、逆に別の単一部材とは
    (折れ角が小さく、節点に線材が2本しか無くても) つながない。
    戻り値: [[要素番号, ...], ...]
    """
    par = {e: e for e in ids}
    if merge:
        mem_of = {}  # 要素 -> 単一部材の番号
        for k, mem in enumerate(M.get('members', ())):
            for e in mem:
                mem_of[e] = k
        for mem in M.get('members', ()):
            by_sec = {}
            for e in mem:
                if e in par:
                    by_sec.setdefault(M['lines'][e][0], []).append(e)
            for es in by_sec.values():
                for e2 in es[1:]:
                    par[_find(par, es[0])] = _find(par, e2)
        at = {}
        for e in ids:
            _s, ni, nj = M['lines'][e]
            at.setdefault(ni, []).append(e)
            at.setdefault(nj, []).append(e)
        for n, es in at.items():
            if len(es) != 2 or M['deg'].get(n, 0) != 2:
                continue
            e1, e2 = es
            # 単一部材 (*MEMBER) に入っている要素は、MIDAS で指定した部材の
            # 区切りを優先する (別の単一部材や部材外の要素とはつながない)
            if (e1 in mem_of or e2 in mem_of) and                     mem_of.get(e1) != mem_of.get(e2):
                continue
            s1, a1, b1 = M['lines'][e1]
            s2, a2, b2 = M['lines'][e2]
            if s1 != s2:
                continue
            u1 = M['pos'][b1] - M['pos'][a1]
            u2 = M['pos'][b2] - M['pos'][a2]
            n1, n2 = np.linalg.norm(u1), np.linalg.norm(u2)
            if n1 < 1e-9 or n2 < 1e-9:
                continue
            if abs(u1 @ u2) / (n1 * n2) < 0.7071:
                continue
            par[_find(par, e1)] = _find(par, e2)
    out = {}
    for e in ids:
        out.setdefault(_find(par, e), []).append(e)
    return list(out.values())


def planar_regions(M, ids, merge=True):
    """同じ厚さで辺を共有する面材をまとめる."""
    par = {e: e for e in ids}
    if merge:
        edge = {}
        for e in ids:
            t, nds = M['planar'][e]
            for k in range(len(nds)):
                key = (t,) + tuple(sorted((nds[k], nds[(k + 1) % len(nds)])))
                edge.setdefault(key, []).append(e)
        for es in edge.values():
            for e2 in es[1:]:
                par[_find(par, es[0])] = _find(par, e2)
    out = {}
    for e in ids:
        out.setdefault(_find(par, e), []).append(e)
    return list(out.values())


# ---------------------------------------------------------------------------
# 作図
# ---------------------------------------------------------------------------

class Style:
    def __init__(self, color_mode='midas', short_name=True, merge=True,
                 show_nodes=False, show_legend=True, show_grid=True,
                 label_pt=6.0, limit_sec_no=9000, paper_size=3,
                 orient='landscape', solid=True, full_secs=(),
                 show_footer=False, hplate_label=True, level_names=None):
        self.solid = solid
        self.show_footer = show_footer  # 右下の作図情報 (mgt名・作図元)
        # False: 水平な板要素には板厚を書かず、色 (断面一覧) だけで示す
        self.hplate_label = hplate_label
        # 軸組図の階レベル線に書く水平グループ名 (None = 全部)
        self.level_names = (None if level_names is None
                            else {str(v) for v in level_names})
        # short_name でも「_」以降まで書く断面番号 (断面一覧の表で選ぶ)
        self.full_secs = {int(v) for v in full_secs}
        self.color_mode = color_mode
        self.short_name = short_name
        self.merge = merge
        self.show_nodes = show_nodes
        self.show_legend = show_legend
        self.show_grid = show_grid
        self.label_pt = float(label_pt)
        self.limit_sec_no = float(limit_sec_no)
        self.paper_size = int(paper_size)
        self.orient = orient


def _sec_label(M, sec, st):
    nm = M['sec_name'].get(sec, '断面%d' % sec)
    if st.short_name and '_' in nm and int(sec) not in st.full_secs:
        nm = nm[:nm.find('_')]
    return nm


def _line_color(M, sec, st, order):
    if st.color_mode == 'mono':
        return (0, 0, 0)
    c = M['sec_color'].get(sec)
    if c is not None:
        return _readable(c[0])
    return matplotlib.colors.to_rgb(_FALLBACK[order % len(_FALLBACK)])


def _planar_colors(M, t, st, order):
    if st.color_mode == 'mono':
        return (0, 0, 0), (0.88, 0.88, 0.88)
    c = M['thk_color'].get(t)
    if c is not None:
        return _readable(c[0]), c[1]
    rgb = matplotlib.colors.to_rgb(_FALLBACK[(order + 5) % len(_FALLBACK)])
    return rgb, tuple(0.75 + 0.25 * v for v in rgb)


def _fill_colors(M, key, st, fallback_rgb):
    """塗りの (面色, 縁色)。MIDAS の隠線表示の HF / HE 色."""
    if st.color_mode == 'mono':
        return (0.93, 0.93, 0.93), (0.1, 0.1, 0.1)
    tbl = M['sec_color'] if key[0] == 's' else M['thk_color']
    c = tbl.get(key[1])
    if c is not None:
        return c[1], tuple(v * 0.6 for v in c[2])
    return tuple(0.6 + 0.4 * v for v in fallback_rgb), fallback_rgb


def _thk_label(M, t):
    tv = M['thk'].get(t)
    return ('t=%g' % round(tv, 1)) if tv else '厚%d' % t


def _poly_area(pts):
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


# ---------------------------------------------------------------------------
# 断面形状 (せい・幅) を持った部材の投影
# ---------------------------------------------------------------------------

def _hull(P):
    """2次元点群の凸包 (反時計回り)."""
    P = sorted(set((round(float(x), 9), round(float(y), 9)) for x, y in P))
    if len(P) <= 2:
        return np.array(P)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for q in P:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(P):
        while len(up) >= 2 and cross(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return np.array(lo[:-1] + up[:-1])


def local_axes(pi, pj, beta_deg):
    """MIDAS の線材の要素座標系 (x, y, z).

    鉛直材: β=0 で z が全体 +X、正のβで +X→+Y へ回る
    (要素座標軸表示で確認済みの規約)。
    その他: z は x と全体Z を含む面内で上向き、y = z × x。β で x 軸回りに回す。
    """
    x = pj - pi
    L = np.linalg.norm(x)
    if L < 1e-12:
        return None
    x = x / L
    b = math.radians(beta_deg)
    if math.hypot(x[0], x[1]) < 1e-3:
        z = np.array([math.cos(b), math.sin(b), 0.0])
        y = np.cross(z, x)
        return x, y / np.linalg.norm(y), z
    z0 = np.array([0.0, 0.0, 1.0]) - x[2] * x
    z0 /= np.linalg.norm(z0)
    y0 = np.cross(z0, x)
    y = y0 * math.cos(b) + z0 * math.sin(b)
    z = -y0 * math.sin(b) + z0 * math.cos(b)
    return x, y, z


def _sec_box(g):
    """断面の y, z 範囲 (節点=原点)。オフセット記号 [L|C|R][T|C|B]."""
    H, B = g['H'], g['B']
    h, v = g['off'][0], g['off'][1]
    yr = {'L': (-B, 0.0), 'R': (0.0, B)}.get(h, (-B / 2, B / 2))
    zr = {'T': (-H, 0.0), 'B': (0.0, H)}.get(v, (-H / 2, H / 2))
    return yr, zr


def member_shape(M, view, e):
    """線材 e の図上の外形。('poly', 点列) / ('circle', 中心, 半径) / None."""
    sec, ni, nj = M['lines'][e]
    g = M['sec_geom'].get(sec)
    if g is None:
        return None
    pi, pj = M['pos'][ni], M['pos'][nj]
    ax_ = local_axes(pi, pj, M['beta'].get(e, 0.0))
    if ax_ is None:
        return None
    _x, y, z = ax_
    (y0, y1), (z0, z1) = _sec_box(g)
    a = np.array(view.proj(pi), dtype=float)
    b = np.array(view.proj(pj), dtype=float)
    if g['shape'] in _ROUND and np.linalg.norm(b - a) < g['H'] * 0.5:
        c = np.array(view.proj(pi + y * (y0 + y1) / 2 + z * (z0 + z1) / 2))
        return ('circle', c, g['H'] / 2)
    pts = []
    for p in (pi, pj):
        for yy in (y0, y1):
            for zz in (z0, z1):
                pts.append(view.proj(p + y * yy + z * zz))
    return ('poly', _hull(pts))


def planar_shape(M, view, e):
    """面材 e の図上の外形 (厚さを持った板として)."""
    t, nds = M['planar'][e]
    P = np.array([M['pos'][n] for n in nds])
    nrm = np.cross(P[1] - P[0], P[2] - P[0])
    nn = np.linalg.norm(nrm)
    th = M['thk'].get(t, 0.0) / 1000.0
    if nn < 1e-12:
        return _hull([view.proj(p) for p in P])
    nrm /= nn
    pts = [view.proj(p + nrm * s * th / 2) for p in P for s in (-1, 1)]
    return _hull(pts)


def _shape_pts(sh):
    if sh is None:
        return np.zeros((0, 2))
    if sh[0] == 'circle':
        c, r = sh[1], sh[2]
        return np.array([c + (r, 0), c - (r, 0), c + (0, r), c - (0, r)])
    return np.asarray(sh[1], dtype=float).reshape(-1, 2)


def _member_item(M, view, e, sh, st, col):
    """塗り部材の (奥行き, 順位, patch)。順位: 面材0 < 横材1 < 縦材2."""
    sec, ni, nj = M['lines'][e]
    pi, pj = M['pos'][ni], M['pos'][nj]
    fc, ec = _fill_colors(M, ('s', sec), st, col)
    kw = dict(facecolor=fc, edgecolor=ec, linewidth=0.3)
    if sh[0] == 'circle':
        patch = Circle(sh[1], sh[2], **kw)
    else:
        patch = Polygon(sh[1], closed=True, **kw)
    d = pj - pi
    vertical = math.hypot(d[0], d[1]) < abs(d[2]) * 0.01
    return (float(((pi + pj) / 2) @ view.toward), 2 if vertical else 1, patch)


def _extent(M, view):
    pts = []
    for e in view.line_ids:
        _s, ni, nj = M['lines'][e]
        pts += [view.proj(M['pos'][ni]), view.proj(M['pos'][nj])]
    for e in view.planar_ids:
        pts += [view.proj(M['pos'][n]) for n in M['planar'][e][1]]
    for _e, n in view.columns:
        pts.append(view.proj(M['pos'][n]))
    if not pts:
        return None
    a = np.array(pts, dtype=float)
    if getattr(view, 'solid', False):
        extra = [_shape_pts(member_shape(M, view, e))
                 for e in view.line_ids + [c for c, _n in view.columns]]
        extra += [planar_shape(M, view, e) for e in view.planar_ids]
        extra = [q for q in extra if len(q)]
        if extra:
            a = np.vstack([a] + extra)
    return a[:, 0].min(), a[:, 0].max(), a[:, 1].min(), a[:, 1].max()


def _chain_mid(M, view, ch, cnt):
    """まとめた部材の、図上の長さで中央の点とそこでの向き.

    端 (1回しか現れない節点) からたどり、長さの半分の位置を返す。
    端が無い (リング状) ときは任意の節点から一周する。
    """
    adj = {}
    for e in ch:
        _s, ni, nj = M['lines'][e]
        adj.setdefault(ni, []).append((e, nj))
        adj.setdefault(nj, []).append((e, ni))
    ends = [n for n, k in cnt.items() if k == 1]
    n = ends[0] if ends else M['lines'][ch[0]][1]
    segs, used = [], set()
    while True:
        nxt = [(e, m) for e, m in adj[n] if e not in used]
        if not nxt:
            break
        e, m = nxt[0]
        used.add(e)
        a = np.array(view.proj(M['pos'][n]), dtype=float)
        b = np.array(view.proj(M['pos'][m]), dtype=float)
        segs.append((a, b, e))
        n = m
    total = sum(np.linalg.norm(b - a) for a, b, _e in segs)
    if total < 1e-6:
        return None, None, None
    half, acc = total / 2, 0.0
    for a, b, e in segs:
        L = np.linalg.norm(b - a)
        if acc + L >= half and L > 0:
            return a + (b - a) * (half - acc) / L, b - a, e
        acc += L
    a, b, e = segs[-1]
    return (a + b) / 2, b - a, e


class _Placer:
    """注記どうしの重なりを避ける (候補位置を順に試す簡易版)."""

    def __init__(self, fig):
        self.fig = fig
        self.r = fig.canvas.get_renderer()
        self.boxes = []

    def put(self, ax, xy, text, cands, **kw):
        t = None
        for cand in cands:
            if len(cand) == 5:
                xy, dx, dy, ha, va = cand
            else:
                dx, dy, ha, va = cand
            if t is not None:
                t.remove()
            t = ax.annotate(text, xy, xytext=(dx, dy),
                            textcoords='offset points', ha=ha, va=va, **kw)
            bb = t.get_window_extent(self.r).expanded(1.02, 1.05)
            if not any(bb.overlaps(b) for b in self.boxes):
                break
        self.boxes.append(t.get_window_extent(self.r))
        return t


def render_page(M, view, st, fig=None, file_label=''):
    """1ビューを1ページに描く。fig を返す."""
    _setup_japanese_font()
    W, H = PAPERS.get(st.paper_size, PAPERS[3])
    view.solid = st.solid
    ext = _extent(M, view)
    if ext is None:
        raise ValueError('%s に描画できる要素がありません。' % view.name)
    u0, u1, v0, v1 = ext
    du, dv = max(u1 - u0, 1.0), max(v1 - v0, 1.0)
    if st.orient == 'portrait' or (st.orient == 'auto' and dv > du * 1.15):
        W, H = H, W
    if fig is None:
        fig = plt.figure(figsize=(W * _MM, H * _MM))
    fs = st.label_pt

    # --- 紙面の割付 [mm] ---
    mg = 10.0
    title_h = 9.0
    info_h = 14.0 if st.show_footer else 0.0
    leg_w = 58.0 if st.show_legend else 0.0
    box_l, box_b = mg, mg + info_h + 2.0
    box_w = W - 2 * mg - (leg_w + 3.0 if leg_w else 0.0)
    box_h = H - box_b - mg - title_h

    ax = fig.add_axes([box_l / W, box_b / H, box_w / W, box_h / H])
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_linewidth(0.6)

    # 等縮尺で枠に収める (周囲に通り芯符号ぶん 16mm 空ける)
    reserve = 16.0 if st.show_grid else 8.0
    scale = min((box_w - 2 * reserve) / du, (box_h - 2 * reserve) / dv)  # mm/m
    cu, cv = (u0 + u1) / 2, (v0 + v1) / 2
    ax.set_xlim(cu - box_w / 2 / scale, cu + box_w / 2 / scale)
    ax.set_ylim(cv - box_h / 2 / scale, cv + box_h / 2 / scale)
    mm = 1.0 / scale  # 紙上 1mm のモデル長さ [m]

    # --- 通り芯・階レベル ---
    grid_kw = dict(color='0.45', linewidth=0.4, linestyle=(0, (8, 2, 1, 2)),
                   zorder=0.5)

    def bubble(name):
        # 短い符号は丸囲み (MIDAS の通り芯と同じ)、長い名前は角丸の枠
        return dict(boxstyle=('circle,pad=0.25' if len(name) <= 3
                              else 'round,pad=0.3,rounding_size=0.6'),
                    facecolor='white', edgecolor='0.35', linewidth=0.4)
    gfs = fs + 0.5
    if st.show_grid and view.kind == 'plan':
        pad = 7 * mm
        for gname, (c, d) in view._grid_lines:
            # 図の範囲 (+pad) と直線の交わる区間
            ts = []
            for k, (lo, hi) in enumerate(((u0 - pad, u1 + pad),
                                          (v0 - pad, v1 + pad))):
                if abs(d[k]) > 1e-9:
                    ts += [(lo - c[k]) / d[k], (hi - c[k]) / d[k]]
            ts.sort()
            if len(ts) < 2:
                continue
            ta, tb = ts[len(ts) // 2 - 1], ts[len(ts) // 2]
            pa, pb = c + ta * d, c + tb * d
            if not (u0 - pad - 1e-6 <= (pa[0] + pb[0]) / 2 <= u1 + pad + 1e-6
                    and v0 - pad - 1e-6 <= (pa[1] + pb[1]) / 2
                    <= v1 + pad + 1e-6):
                continue
            ax.plot([pa[0], pb[0]], [pa[1], pb[1]], **grid_kw)
            for p, sgn in ((pa, -1), (pb, 1)):
                q = p + sgn * d * 3.5 * mm
                ax.text(q[0], q[1], gname, ha='center', va='center',
                        fontsize=gfs, color='0.2', bbox=bubble(gname), zorder=6)
    if st.show_grid and view.kind == 'elev':
        pad = 7 * mm
        for gname, s in view._grid_s:
            if not (u0 - 0.3 <= s <= u1 + 0.3):
                continue
            ax.plot([s, s], [v0 - pad, v1 + pad], **grid_kw)
            ax.text(s, v0 - pad - 3.5 * mm, gname, ha='center', va='center',
                    fontsize=gfs, color='0.2', bbox=bubble(gname), zorder=6)
        seen = {}
        for zl, nm in view._levels_all:
            if st.level_names is not None and nm not in st.level_names:
                continue
            if v0 - 0.01 <= zl <= v1 + 0.01:
                key = round(zl, 3)
                seen.setdefault(key, []).append(nm)
        for zl, names in seen.items():
            ax.plot([u0 - pad, u1 + pad], [zl, zl], color='0.55',
                    linewidth=0.35, linestyle=(0, (4, 2)), zorder=0.5)
            ax.text(u1 + pad + 1.0 * mm, zl, '/'.join(names), ha='left',
                    va='center', fontsize=fs, color='0.25', zorder=6)

    placer = _Placer(fig)
    legend = {}  # key -> (色, 表示名, 種別)
    items = []   # 塗りの部材 (奥行き, 順位, patch) -> 最後に奥から描く
    translucent = set()  # 半透明で描いた板の厚さ番号 (凡例も合わせる)
    txt_kw = dict(fontsize=fs, zorder=7,
                  bbox=dict(boxstyle='square,pad=0.08', facecolor='white',
                            edgecolor='none', alpha=0.85))

    # --- 面材 (板・壁) ---
    thk_order = {t: i for i, t in enumerate(sorted(
        {M['planar'][e][0] for e in view.planar_ids}))}
    for region in planar_regions(M, view.planar_ids, st.merge):
        t = M['planar'][region[0]][0]
        ec, fc = _planar_colors(M, t, st, thk_order[t])
        best, best_a, cen = None, 0.0, np.zeros(2)
        area_sum = 0.0
        for e in region:
            pts = np.array([view.proj(M['pos'][n])
                            for n in M['planar'][e][1]], dtype=float)
            a = _poly_area(pts)
            if st.solid:
                ffc, fec = _fill_colors(M, ('t', t), st, ec)
                P3 = np.array([M['pos'][n] for n in M['planar'][e][1]])
                lw = 0.3
                if view.kind == 'plan' and is_horizontal_planar(M, e):
                    # 伏図のスラブは半透明にして、梁・符号を読めるようにする
                    ffc = tuple(ffc[:3]) + (_HPLATE_ALPHA,)
                    fec = tuple(fec[:3]) + (0.35,)
                    lw = 0.2
                    translucent.add(t)
                items.append((float(P3.mean(axis=0) @ view.toward), 0,
                              Polygon(planar_shape(M, view, e), closed=True,
                                      facecolor=ffc, edgecolor=fec,
                                      linewidth=lw)))
            if a > 1e-6:
                c0 = pts.mean(axis=0)
                if not st.solid:
                    shr = c0 + (pts - c0) * 0.92  # MIDAS の縮小表示に近づける
                    ax.add_patch(Polygon(shr, closed=True, facecolor=fc,
                                         edgecolor=ec, linewidth=0.3,
                                         alpha=0.55, zorder=1))
                cen += c0 * a
                area_sum += a
                if a > best_a:
                    best, best_a = c0, a
            elif not st.solid:  # 真横から見た面 -> 線
                ax.plot(pts[:, 0], pts[:, 1], color=ec, linewidth=0.8,
                        zorder=1)
        no_label = (not st.hplate_label and
                    all(is_horizontal_planar(M, e) for e in region))
        if best is not None and not no_label:
            cen = cen / area_sum
            # 領域の重心に最も近い要素の中心に書く (L字等で外に出ないように)
            cands = []
            for e in region:
                pts = np.array([view.proj(M['pos'][n])
                                for n in M['planar'][e][1]], dtype=float)
                if _poly_area(pts) > 1e-6:
                    cands.append(pts.mean(axis=0))
            cands = np.array(cands)
            at = cands[np.argmin(np.linalg.norm(cands - cen, axis=1))]
            placer.put(ax, at, _thk_label(M, t),
                       [(0, 0, 'center', 'center'), (0, 6, 'center', 'bottom'),
                        (0, -6, 'center', 'top')],
                       color=ec, **txt_kw)
        legend[('t', t)] = (ec, '%s（厚さ番号 %d）' % (_thk_label(M, t), t),
                            'planar')

    # --- 線材 ---
    secs = sorted({M['lines'][e][0] for e in view.line_ids})
    sec_order = {s: i for i, s in enumerate(secs)}
    nodes_drawn = set()
    chains = line_chains(M, view.line_ids, st.merge)
    for ch in chains:
        sec = M['lines'][ch[0]][0]
        dummy = sec >= st.limit_sec_no
        col = (0.6, 0.6, 0.6) if dummy else _line_color(M, sec, st,
                                                       sec_order[sec])
        cnt = {}
        for e in ch:
            _s, ni, nj = M['lines'][e]
            a, b = view.proj(M['pos'][ni]), view.proj(M['pos'][nj])
            sh = member_shape(M, view, e) if (st.solid and not dummy) else None
            if sh is not None:
                items.append(_member_item(M, view, e, sh, st, col))
            else:
                ax.plot([a[0], b[0]], [a[1], b[1]], color=col,
                        linewidth=(0.4 if dummy else 0.9),
                        linestyle=((0, (3, 2)) if dummy else '-'),
                        solid_capstyle='butt', zorder=2)
            nodes_drawn.update((ni, nj))
            cnt[ni] = cnt.get(ni, 0) + 1
            cnt[nj] = cnt.get(nj, 0) + 1
        if dummy:
            continue
        mid, dvec, e_mid = _chain_mid(M, view, ch, cnt)
        if mid is None:
            continue  # 真正面から見た部材 (軸組図の直交梁など) は点になる
        du_, dv_ = dvec
        ang = abs(math.degrees(math.atan2(dv_, du_))) % 180
        if ang <= 30 or ang >= 150:     # 横向き -> 上 (だめなら下)
            cands = [(0, 1.2, 'center', 'bottom'), (0, -1.2, 'center', 'top')]
            pdir = np.array([0.0, 1.0])
        elif 60 <= ang <= 120:          # 縦向き -> 右 (だめなら左)
            cands = [(2.0, 0, 'left', 'center'), (-2.0, 0, 'right', 'center')]
            pdir = np.array([1.0, 0.0])
        else:                           # 斜め -> 右上 / 左下
            cands = [(2.0, 1.0, 'left', 'bottom'),
                     (-2.0, -1.0, 'right', 'top')]
            pdir = np.array([-dv_, du_]) / math.hypot(du_, dv_)
            if pdir[1] < 0:
                pdir = -pdir
        if st.solid:
            # 断面の外形の外側に書く (せいの分だけ中心線から離す)
            q = _shape_pts(member_shape(M, view, e_mid)) - mid
            if len(q):
                hi, lo = float((q @ pdir).max()), float((q @ pdir).min())
                cands = [(tuple(mid + pdir * max(hi, 0.0)),) + cands[0],
                         (tuple(mid + pdir * min(lo, 0.0)),) + cands[1]]
        rot = {}
        if view.kind == 'plan':
            # 伏図: 部材中央に、部材の平面上の角度に沿わせて書く。
            # 文字が逆さにならないよう角度は (-90°, 90°] にそろえ、
            # 文字の上側 (法線 n の側) に置く。重なるときは反対側。
            th = math.degrees(math.atan2(dv_, du_))
            if th <= -90:
                th += 180
            elif th > 90:
                th -= 180
            n = np.array([-math.sin(math.radians(th)),
                          math.cos(math.radians(th))])
            hi = lo = 0.0
            if st.solid:
                q = _shape_pts(member_shape(M, view, e_mid)) - mid
                if len(q):
                    hi = max(float((q @ n).max()), 0.0)
                    lo = min(float((q @ n).min()), 0.0)
            g = 1.2  # 外形からのすき間 [pt]
            cands = [(tuple(mid + n * hi), n[0] * g, n[1] * g,
                      'center', 'bottom'),
                     (tuple(mid + n * lo), -n[0] * g, -n[1] * g,
                      'center', 'top')]
            rot = dict(rotation=th, rotation_mode='anchor')
        placer.put(ax, mid, _sec_label(M, sec, st), cands, color=col,
                   **rot, **txt_kw)
        legend[('s', sec)] = (col, M['sec_name'].get(sec, '断面%d' % sec),
                              'line')

    # --- 伏図の柱 (下階の柱を点で) ---
    if view.columns:
        done = set()
        for e, top in view.columns:
            sec = M['lines'][e][0]
            if top in done or sec >= st.limit_sec_no:
                continue
            done.add(top)
            col = _line_color(M, sec, st, len(sec_order) + sec % 7)
            p = view.proj(M['pos'][top])
            sh = member_shape(M, view, e) if st.solid else None
            if sh is not None:
                it = _member_item(M, view, e, sh, st, col)
                items.append((it[0], 3, it[2]))  # 伏図では柱を梁より手前に
                q = _shape_pts(sh)
                u_lo, v_lo = q.min(axis=0)
                u_hi, v_hi = q.max(axis=0)
                cands = [((u_hi, v_hi), 1.0, 1.0, 'left', 'bottom'),
                         ((u_lo, v_hi), -1.0, 1.0, 'right', 'bottom'),
                         ((u_hi, v_lo), 1.0, -1.0, 'left', 'top'),
                         ((u_lo, v_lo), -1.0, -1.0, 'right', 'top')]
            else:
                ax.plot([p[0]], [p[1]], marker='s', markersize=4.2,
                        markerfacecolor=col, markeredgecolor=col, zorder=3,
                        linestyle='none')
                cands = [(2.5, 2.5, 'left', 'bottom'),
                         (-2.5, 2.5, 'right', 'bottom'),
                         (2.5, -2.5, 'left', 'top'),
                         (-2.5, -2.5, 'right', 'top')]
            placer.put(ax, p, _sec_label(M, sec, st), cands,
                       color=col, **txt_kw)
            legend[('s', sec)] = (col, M['sec_name'].get(sec, '断面%d' % sec),
                                  'column')

    # --- 塗りの部材を奥から順に (MIDAS の隠線表示) ---
    items.sort(key=lambda it: (round(it[0], 2), it[1]))
    for k, (_d, _o, patch) in enumerate(items):
        patch.set_zorder(1.0 + 3.0 * k / max(len(items), 1))
        ax.add_patch(patch)

    # --- 節点 ---
    if st.show_nodes and nodes_drawn:
        P = np.array([view.proj(M['pos'][n]) for n in nodes_drawn])
        ax.plot(P[:, 0], P[:, 1], linestyle='none', marker='o',
                markersize=1.1, color='0.15', zorder=4)

    # --- 座標軸 (左下) ---
    ox, oy = box_l + 7.0, box_b + 7.0
    L = 9.0
    for (ex, ey), lab in (((L, 0), view.axes[0]), ((0, L), view.axes[1])):
        fig.add_artist(FancyArrowPatch(
            (ox / W, oy / H), ((ox + ex) / W, (oy + ey) / H),
            transform=fig.transFigure, arrowstyle='-|>', mutation_scale=6,
            linewidth=0.7, color='#1a1a1a'))
        fig.text((ox + ex * 1.18) / W, (oy + ey * 1.18) / H, lab,
                 ha='center', va='center', fontsize=fs + 0.5,
                 color='#1a1a1a')

    # --- 表題 ---
    fig.text(mg / W, (H - mg - 2.0) / H, view.title, ha='left', va='top',
             fontsize=12, fontweight='bold')
    fig.text((mg + box_w) / W, (H - mg - 2.0) / H, '断面符号図',
             ha='right', va='top', fontsize=10)

    # --- 作図情報 (右下。既定は出さない) ---
    if st.show_footer:
        today = datetime.date.today().strftime('%Y-%m-%d')
        info = ('解析モデル: %s\n'
                '作図: mgtkit（mgt の要素・断面・断面色データより作図）  %s'
                % (file_label or os.path.basename(M['path']), today))
        fig.text((W - mg) / W, (mg + 1.0) / H, info, ha='right', va='bottom',
                 fontsize=6.5, color='0.25', linespacing=1.5)

    # --- 断面一覧 (右) ---
    if st.show_legend and legend:
        lx = W - mg - leg_w
        top = H - mg - title_h
        fig.add_artist(Rectangle((lx / W, box_b / H), leg_w / W,
                                 (top - box_b) / H, transform=fig.transFigure,
                                 facecolor='none', edgecolor='0.3',
                                 linewidth=0.5))
        fig.text((lx + 3) / W, (top - 3) / H, '断面一覧', ha='left',
                 va='top', fontsize=8, fontweight='bold')
        items = sorted(legend.items(),
                       key=lambda kv: (kv[0][0] != 's', kv[0][1]))
        avail = top - 10.0 - box_b - 2.0
        row = min(4.6, avail / max(len(items), 1))
        lfs = min(6.5, row / 0.46 * 0.9 * 0.72 * 2.2)
        y = top - 10.0
        for (kind, no), (col, text, how) in items:
            yc = (y - row / 2) / H
            if st.solid and st.color_mode != 'mono':
                tbl = M['sec_color'] if kind == 's' else M['thk_color']
                cc = tbl.get(no)
                fa = (_HPLATE_ALPHA if kind == 't' and no in translucent
                      else 1.0)
                fig.add_artist(Rectangle(((lx + 3) / W, yc - 1.2 / H),
                                         7.0 / W, 2.4 / H,
                                         transform=fig.transFigure,
                                         facecolor=matplotlib.colors.to_rgba(
                                             cc[1] if cc else col, fa),
                                         edgecolor=(cc[2] if cc else col),
                                         linewidth=0.3))
            elif how == 'planar':
                fig.add_artist(Rectangle(((lx + 3) / W, yc - 1.0 / H),
                                         7.0 / W, 2.0 / H,
                                         transform=fig.transFigure,
                                         facecolor=col, alpha=0.35,
                                         edgecolor=col, linewidth=0.3))
            else:
                fig.add_artist(matplotlib.lines.Line2D(
                    [(lx + 3) / W, (lx + 10) / W], [yc, yc],
                    transform=fig.transFigure, color=col, linewidth=1.2))
            lab = text if kind == 't' else '%s（%d）' % (text, no)
            fig.text((lx + 12) / W, yc, lab, ha='left', va='center',
                     fontsize=lfs, color=col)
            y -= row
    return fig


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def build_views(M, floors=(), axes=(), grids=(), show_columns=True,
                show_levels=True, show_hplates=True):
    views, notes = [], []
    for nm in floors:
        if nm not in M['groups']:
            notes.append('グループ %s が見つかりません。' % nm)
            continue
        views.append(plan_view(M, nm, show_columns, grids, show_hplates))
    for nm in axes:
        if nm not in M['groups']:
            notes.append('グループ %s が見つかりません。' % nm)
            continue
        try:
            views.append(elev_view(M, nm, grids, show_levels))
        except ValueError as e:
            notes.append(str(e))
    # 要素の無い図は除く
    out = []
    for v in views:
        if _extent(M, v) is None:
            notes.append('%s には描画できる要素がありません (スキップ)。'
                         % v.name)
        else:
            out.append(v)
    return out, notes


def _safe(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s)


def list_sections(mgt_path):
    """断面一覧 (表での選択用)。モデルで使われている線材の断面のみ.

    戻り値: [{'no', 'name', 'short', 'n_elems'}] (断面番号順)
    """
    M = load_model(mgt_path)
    cnt = {}
    for sec, _ni, _nj in M['lines'].values():
        cnt[sec] = cnt.get(sec, 0) + 1
    out = []
    for sec in sorted(cnt):
        nm = M['sec_name'].get(sec, '断面%d' % sec)
        out.append(dict(no=int(sec), name=nm,
                        short=(nm[:nm.find('_')] if '_' in nm else nm),
                        n_elems=cnt[sec]))
    return out


def plot_capfig(mgt_path, out_dir, floors=(), axes=(), grids=(),
                show_columns=True, show_levels=True, show_hplates=True,
                style=None,
                file_label=None):
    """全ビューを1冊の PDF に出力する。戻り値: ([pdfパス], notes)."""
    st = style or Style()
    M = load_model(mgt_path)
    views, notes = build_views(M, floors, axes, grids, show_columns,
                               show_levels, show_hplates)
    if not views:
        return [], notes
    base = os.path.splitext(os.path.basename(mgt_path))[0]
    out = os.path.join(out_dir, '断面符号図_MIDAS風_%s.pdf' % _safe(base))
    with PdfPages(out) as pdf:
        for v in views:
            fig = render_page(M, v, st, file_label=file_label)
            pdf.savefig(fig)
            plt.close(fig)
    return [out], notes


def preview_png(mgt_path, out_dir, page=1, floors=(), axes=(), grids=(),
                show_columns=True, show_levels=True, show_hplates=True,
                style=None,
                file_label=None, dpi=110):
    """page 枚目 (1始まり) を PNG にする。戻り値: (path, page, pages, 名前, notes)."""
    st = style or Style()
    M = load_model(mgt_path)
    views, notes = build_views(M, floors, axes, grids, show_columns,
                               show_levels, show_hplates)
    if not views:
        return None, 0, 0, '', notes
    page = max(1, min(int(page), len(views)))
    v = views[page - 1]
    fig = render_page(M, v, st, file_label=file_label)
    out = os.path.join(out_dir, '_preview_capfig.png')
    fig.savefig(out, dpi=dpi, facecolor='white')
    plt.close(fig)
    return out, page, len(views), v.title, notes
