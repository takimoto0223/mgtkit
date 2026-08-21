# -*- coding: utf-8 -*-
"""荷重分布図の作図 (matplotlib).

荷重ケース1つにつき1図。骨組を薄い立体図で描き、そのケースに属する荷重
だけを強調する。draw_model.py の作法 (matplotlib / Agg / PDF出力 /
日本語フォント設定) に合わせてあり、既存モジュールは変更していない。

**作図はセル内 mm 座標で行う。** 図の縮尺が変わっても文字高と引き出し線の
間隔を一定にしたいので、用紙への当てはめ (フィット) を先に済ませ、以降は
用紙 mm をそのままデータ座標として扱う (Axes を実寸で置き、aspect=equal)。
こうすると mm 指定がそのまま紙面 mm になる。
"""

import math
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import LineCollection, PolyCollection

from mgtkit.mgt import mgtopen_element, mgtopen_node, mgtopen_plate
from mgtkit.draw_model import _setup_japanese_font
from mgtkit.loadmap.mgt_loads import read_loads

MM_PER_INCH = 25.4
PT_PER_MM = 72.0 / 25.4

#: 用紙 [mm] (縦向き)。paper_size=4 -> A4
PAPERS = {2: (420.0, 594.0), 3: (297.0, 420.0), 4: (210.0, 297.0),
          5: (148.0, 210.0)}

#: 1ページあたりの図数 -> (列数, 行数)
GRID = {1: (1, 1), 2: (1, 2), 3: (1, 3), 4: (2, 2), 6: (2, 3), 8: (2, 4),
        9: (3, 3)}

#: 見た目 (色は draw_model と揃えず、荷重図として読みやすい配色にしてある)
C_FRAME = '#7f7f8c'          # 骨組
C_PLATE = '#b8b8bf'          # プレート要素の外形
C_FILL = '#6bb8e6'           # 荷重領域の塗り
C_EDGE = '#9e8515'           # 荷重領域の外形
C_LEADER = '#8c8c92'         # 引き出し線
C_LABEL = '#006b28'          # 荷重ラベル
C_ARROW = '#c71b8c'          # 荷重の矢印
C_VALUE = '#004d99'          # 荷重値
C_NOTE = '#59595e'           # 凡例

#: 荷重方向の記号 -> 全体座標の単位ベクトル
_DIR_VEC = {'GX': (1.0, 0.0, 0.0), 'GY': (0.0, 1.0, 0.0),
            'GZ': (0.0, 0.0, 1.0), 'LX': (1.0, 0.0, 0.0),
            'LY': (0.0, 1.0, 0.0), 'LZ': (0.0, 0.0, 1.0)}


# ---------------------------------------------------------------------------
# 投影とフィット
# ---------------------------------------------------------------------------

def project(p, azimuth, elevation):
    """立体図の投影。azimuth=平面での視線方向[deg]、elevation=見下ろし角[deg]."""
    x, y, z = p
    a = math.radians(azimuth)
    e = math.radians(elevation)
    u = -x * math.sin(a) + y * math.cos(a)
    v = -(x * math.cos(a) + y * math.sin(a)) * math.sin(e) + z * math.cos(e)
    return (u, v)


def screen_dir(vec, azimuth, elevation):
    """全体座標のベクトルを画面上の単位ベクトルにする (投影は線形)."""
    ox, oy = project((0.0, 0.0, 0.0), azimuth, elevation)
    px, py = project(vec, azimuth, elevation)
    dx, dy = px - ox, py - oy
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-9 else (0.0, -1.0)


class _Fit(object):
    """モデル座標 -> セル内 mm。"""

    def __init__(self, pts, x0, y0, x1, y1):
        us = [p[0] for p in pts] or [0.0]
        vs = [p[1] for p in pts] or [0.0]
        self.umin, self.umax = min(us), max(us)
        self.vmin, self.vmax = min(vs), max(vs)
        du = max(self.umax - self.umin, 1e-6)
        dv = max(self.vmax - self.vmin, 1e-6)
        self.scale = min((x1 - x0) / du, (y1 - y0) / dv)
        self.ox = x0 + ((x1 - x0) - du * self.scale) / 2 - self.umin * self.scale
        self.oy = y0 + ((y1 - y0) - dv * self.scale) / 2 - self.vmin * self.scale
        self.box = (x0, y0, x1, y1)
        # 図が実際に占める範囲。ラベルはこの外側に置く (枠いっぱいを基準に
        # すると、細長い建物で引き出し線が無駄に長くなる)
        self.fig_box = (self.ox + self.umin * self.scale,
                        self.oy + self.vmin * self.scale,
                        self.ox + self.umax * self.scale,
                        self.oy + self.vmax * self.scale)

    def __call__(self, uv):
        return (self.ox + uv[0] * self.scale, self.oy + uv[1] * self.scale)


class _Style(object):
    """文字と余白の寸法[mm]。枠の大きさから自動で決める。"""

    def __init__(self, cell_w, cell_h):
        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        self.label = clamp(cell_h / 70.0, 1.5, 2.6)
        self.value = self.label
        self.title = clamp(cell_h / 40.0, 2.4, 5.0)
        self.note = clamp(cell_h / 65.0, 1.6, 2.8)
        self.gutter = clamp(cell_w * 0.18, 12.0, 32.0)
        self.top = clamp(cell_h / 20.0, 5.0, 12.0)
        self.bottom = clamp(cell_h / 28.0, 4.0, 9.0)
        self.line_gap = 1.35


def _centroid(pts):
    n = len(pts) or 1
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _extent(pts):
    """点群の画面上の大きさ (短辺)[mm]。矢印の長さを決めるのに使う。"""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(max(xs) - min(xs), max(ys) - min(ys))


def _place_labels(items, fit, st, cell_w):
    """引き出し先のラベル位置を決める。

    items は (アンカー, 文字列)。図の左右に振り分け、アンカーの高さから
    始めて、重なるぶんだけ下へ押して解く。
    返り値は (アンカー, ラベル基点, ha, 文字列)。
    """
    x0, _y0, x1, _y1 = fit.fig_box
    y0, y1 = fit.box[1], fit.box[3]          # 縦は枠いっぱい使ってよい
    mid = (x0 + x1) / 2
    step = st.label * st.line_gap
    cap = max(1, int((y1 - y0) / step) + 1)

    left = [it for it in items if it[0][0] < mid]
    right = [it for it in items if it[0][0] >= mid]
    for src, dst in ((left, right), (right, left)):
        while len(src) > cap and len(dst) < cap:
            src.sort(key=lambda it: it[0][1])
            dst.append(src.pop(0))

    placed = []
    for side, group in (('L', left), ('R', right)):
        if not group:
            continue
        group.sort(key=lambda it: -it[0][1])
        ys = []
        for anchor, _t in group:
            y = min(anchor[1], y1)
            if ys:
                y = min(y, ys[-1] - step)
            ys.append(y)
        deficit = y0 - ys[-1]
        if deficit > 0:
            shift = min(deficit, y1 - ys[0])
            ys = [y + shift for y in ys]
            for i in range(1, len(ys)):
                ys[i] = min(ys[i], ys[i - 1] - step)
        for (anchor, text), ty in zip(group, ys):
            tx = (x0 - 1.5) if side == 'L' else (x1 + 1.5)
            tx = max(1.5, min(cell_w - 1.5, tx))
            placed.append((anchor, (tx, ty), 'right' if side == 'L' else 'left',
                           text))
    return placed


# ---------------------------------------------------------------------------
# モデルの読み込み
# ---------------------------------------------------------------------------

def _read_geometry(mgt_path):
    """節点・線材・面要素を読む (既存の mgtopen_* をそのまま使う)."""
    node = np.atleast_2d(mgtopen_node(mgt_path))
    element = np.atleast_2d(mgtopen_element(mgt_path))
    plate = np.atleast_2d(mgtopen_plate(mgt_path))

    nodes = {}
    for row in node:
        if len(row) >= 4:
            nodes[int(row[0])] = (float(row[1]), float(row[2]), float(row[3]))

    lines = {}
    if element.size:
        for row in element:
            if len(row) >= 5:
                # 要素番号をキーにする（部材荷重の対象要素を引くため）
                lines[int(row[0])] = (int(row[3]), int(row[4]))

    plates = {}
    if plate.size:
        for row in plate:
            if len(row) >= 7:
                ns = [int(v) for v in row[3:7] if int(v) > 0]
                if len(ns) >= 3:
                    plates[int(row[0])] = ns
    return nodes, lines, plates


# ---------------------------------------------------------------------------
# 1図ぶんの作図
# ---------------------------------------------------------------------------

def _draw_case(ax, geom, loads, case, cell_w, cell_h,
               azimuth, elevation, title=None, show_labels=True,
               show_arrows=True, show_note=True):
    nodes, lines, plates = geom
    st = _Style(cell_w, cell_h)
    ax.set_xlim(0, cell_w)
    ax.set_ylim(0, cell_h)
    ax.set_aspect('equal')
    ax.axis('off')

    proj = dict((nid, project(p, azimuth, elevation))
                for nid, p in nodes.items())
    if not proj:
        return
    fit = _Fit(list(proj.values()), st.gutter, st.bottom,
               cell_w - st.gutter, cell_h - st.top)
    P = lambda nid: fit(proj[nid])          # noqa: E731

    # 1) 骨組
    segs = [[P(a), P(b)] for a, b in lines.values() if a in proj and b in proj]
    if segs:
        ax.add_collection(LineCollection(segs, colors=C_FRAME, linewidths=0.28))
    faces = [[P(n) for n in ns if n in proj] for ns in plates.values()]
    faces = [f for f in faces if len(f) >= 3]
    if faces:
        ax.add_collection(PolyCollection(faces, facecolors='none',
                                         edgecolors=C_PLATE, linewidths=0.2))

    # 2) 床荷重 (領域＋ラベル)
    items = []
    fills = []
    for fl in loads.floor_loads_of(case):
        pts = [fit(project(nodes[n], azimuth, elevation))
               for n in fl.nodes if n in nodes]
        if len(pts) < 3:
            continue
        fills.append(pts)
        items.append((_centroid(pts), fl.label()))

    # 3) 面荷重 (プレート要素＋矢印)
    arrows = []
    n_press = 0
    for pl in loads.pressures_of(case):
        vec = _DIR_VEC.get(str(pl.direction).upper(), (0.0, 0.0, 1.0))
        sign = -1.0 if pl.value < 0 else 1.0
        sdir = screen_dir(tuple(sign * c for c in vec), azimuth, elevation)
        for eid in pl.elements:
            ns = plates.get(eid)
            if not ns:
                continue
            pts = [P(n) for n in ns if n in proj]
            if len(pts) < 3:
                continue
            n_press += 1
            fills.append(pts)
            if show_arrows:
                # 矢印は要素の大きさに合わせる (密なメッシュでも潰れない)
                length = max(min(_extent(pts) * 0.75, 4.0), 1.0)
                arrows.append((_centroid(pts), sdir, length))

    if fills:
        # 塗りと外形線を分ける。1つの PolyCollection にすると alpha が
        # 外形線にもかかり、境界がぼやけて領域の切れ目が読めなくなる。
        ax.add_collection(PolyCollection(fills, facecolors=C_FILL, alpha=0.45,
                                         edgecolors='none'))
        ax.add_collection(PolyCollection(fills, facecolors='none',
                                         edgecolors=C_EDGE, linewidths=0.55))

    # 4) 部材荷重 (荷重図＋矢印＋値)
    n_beam = 0
    for bl in loads.beam_loads_of(case):
        vec = _DIR_VEC.get(str(bl.direction).upper(), (0.0, 0.0, 1.0))
        sign = -1.0 if bl.value < 0 else 1.0
        sdir = screen_dir(tuple(sign * c for c in vec), azimuth, elevation)
        for eid in bl.elements:
            ends = lines.get(eid)
            if not ends or ends[0] not in proj or ends[1] not in proj:
                continue
            n_beam += 1
            _draw_beam_load(ax, P(ends[0]), P(ends[1]), sdir, bl, st, arrows)

    # 5) 節点荷重 (矢印＋値)
    for nl in loads.nodal_loads_of(case):
        val = nl.fz if abs(nl.fz) >= max(abs(nl.fx), abs(nl.fy)) else (
            nl.fx if abs(nl.fx) >= abs(nl.fy) else nl.fy)
        sdir = screen_dir((nl.fx, nl.fy, nl.fz), azimuth, elevation)
        for nid in nl.nodes:
            if nid not in proj:
                continue
            tip = P(nid)
            arrows.append((tip, sdir, 4.5))
            ax.text(tip[0] + 0.8, tip[1] - sdir[1] * 4.5, '%.2f' % val,
                    color=C_VALUE, fontsize=st.value * PT_PER_MM,
                    ha='left', va='center')

    for tip, sdir, length in arrows:
        _arrow(ax, tip, sdir, length)

    # 6) ラベル (引き出し)
    if show_labels:
        for anchor, target, ha, text in _place_labels(items, fit, st, cell_w):
            elbow = target[0] + (2.0 if ha == 'left' else -2.0)
            ax.plot([anchor[0], elbow, target[0]],
                    [anchor[1], target[1], target[1]],
                    color=C_LEADER, linewidth=0.2, solid_joinstyle='miter')
            ax.text(target[0] + (2.0 if ha == 'left' else -2.0), target[1],
                    text, color=C_LABEL, fontsize=st.label * PT_PER_MM,
                    ha=ha, va='center')

    # 7) 図題と凡例
    ax.text(cell_w / 2, cell_h - st.title * 0.6, title or ('荷重分布図－%s' % case),
            fontsize=st.title * PT_PER_MM, ha='center', va='top', color='k')
    if show_note:
        ax.text(cell_w / 2, 1.0, _note_text(loads, case, n_press, n_beam),
                fontsize=st.note * PT_PER_MM, ha='center', va='bottom',
                color=C_NOTE)


def _draw_beam_load(ax, a, b, sdir, bl, st, arrows):
    """部材荷重を MIDAS の画面と同じ形（荷重図＋矢印＋値）で描く。

    a, b は部材両端の画面座標[mm]。sdir は荷重の向き（画面上の単位ベクトル）。
    等分布・台形は荷重図の高さを値に比例させ、集中荷重は矢印1本にする。
    """
    ax_, ay = a
    bx, by = b
    length = math.hypot(bx - ax_, by - ay)
    if length < 1e-6:
        return
    peak = max(abs(p) for _d, p in bl.points) or 1.0
    h_max = min(max(length * 0.18, 1.6), 4.5)     # 荷重図の高さ[mm]

    def on_member(d):
        return (ax_ + (bx - ax_) * d, ay + (by - ay) * d)

    def top_of(d, p):
        x, y = on_member(d)
        h = h_max * abs(p) / peak
        return (x - sdir[0] * h, y - sdir[1] * h)

    if bl.is_moment:                              # モーメントは値だけ示す
        mx, my = on_member(0.5)
        ax.text(mx, my, '%.2f' % bl.value, color=C_VALUE,
                fontsize=st.value * PT_PER_MM, ha='center', va='bottom')
        return

    pts = [(d, p) for d, p in bl.points if p]
    if not pts:
        return
    if len(pts) == 1 or 'CON' in bl.type:         # 集中荷重
        for d, p in pts:
            tip = on_member(d)
            arrows.append((tip, sdir, max(h_max, 3.0)))
            tx, ty = top_of(d, p)
            ax.text(tx + 0.6, ty, '%.2f' % p, color=C_VALUE,
                    fontsize=st.value * PT_PER_MM, ha='left', va='center')
        return

    # 等分布・台形: 荷重図の外形を描き、内側に矢印を並べる
    d0, d1 = pts[0][0], pts[-1][0]
    outline = [on_member(d0)] + [top_of(d, p) for d, p in pts] + [on_member(d1)]
    ax.plot([q[0] for q in outline], [q[1] for q in outline],
            color=C_ARROW, linewidth=0.35)
    n = max(2, min(int((d1 - d0) * length / 3.0) + 1, 8))
    for k in range(n + 1):
        d = d0 + (d1 - d0) * k / n
        p = _interp(pts, d)
        if not p:
            continue
        arrows.append((on_member(d), sdir, h_max * abs(p) / peak))
    mx, my = top_of((d0 + d1) / 2, bl.value)
    ax.text(mx, my - sdir[1] * 0.8, '%.2f' % bl.value, color=C_VALUE,
            fontsize=st.value * PT_PER_MM, ha='center',
            va='bottom' if sdir[1] < 0 else 'top')


def _interp(pts, d):
    """(位置, 値) の列を線形補間する（範囲外は端の値）。"""
    if d <= pts[0][0]:
        return pts[0][1]
    if d >= pts[-1][0]:
        return pts[-1][1]
    for (d0, p0), (d1, p1) in zip(pts, pts[1:]):
        if d0 <= d <= d1:
            if d1 - d0 < 1e-9:
                return p1
            return p0 + (p1 - p0) * (d - d0) / (d1 - d0)
    return pts[-1][1]


def _arrow(ax, tip, sdir, length):
    """先端 tip に向かう矢印 (軸線＋矢じり)。"""
    dx, dy = sdir
    tx, ty = tip
    ax.plot([tx - dx * length, tx], [ty - dy * length, ty],
            color=C_ARROW, linewidth=0.45)
    hl = max(length * 0.35, 0.5)
    hw = hl * 0.45
    bx, by = tx - dx * hl, ty - dy * hl
    px, py = -dy, dx
    ax.plot([bx + px * hw, tx, bx - px * hw], [by + py * hw, ty, by - py * hw],
            color=C_ARROW, linewidth=0.45, solid_joinstyle='miter')


def _note_text(loads, case, n_press, n_beam=0):
    """図の下に出す1行の凡例。"""
    parts = []
    fl = loads.floor_loads_of(case)
    if fl:
        vals = set()
        for f in fl:
            t = loads.floor_type(f.type_name)
            if t is not None and t.value_of(case) is not None:
                vals.add(t.value_of(case))
        v = '／'.join('%.2f' % x for x in sorted(vals)) if vals else '-'
        parts.append('床荷重 %d面  w=%s kN/m2' % (len(fl), v))
    if n_press:
        vals = set(p.value for p in loads.pressures_of(case))
        parts.append('面荷重 %d要素  w=%s kN/m2'
                     % (n_press, '／'.join('%.2f' % x for x in sorted(vals))))
    if n_beam:
        vals = set(b.value for b in loads.beam_loads_of(case))
        parts.append('部材荷重 %d要素  w=%s kN/m'
                     % (n_beam, '／'.join('%.2f' % x for x in sorted(vals))))
    nl = loads.nodal_loads_of(case)
    if nl:
        parts.append('節点荷重 %d点' % sum(len(x.nodes) for x in nl))
    return ('[%s]  ' % case) + '　'.join(parts) if parts else ('[%s]' % case)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def page_layout(per_page=4, cols=None, rows=None, paper_size=4,
                paper_orient=None, margin=8.0, gap=4.0):
    """用紙と割付から (用紙幅, 用紙高, 列, 行, 枠幅, 枠高)[mm] を返す。

    PDF 出力とプレビューで同じ紙面になるよう、計算はここ1箇所に置く。
    """
    pw, ph = PAPERS.get(int(paper_size), PAPERS[4])
    if str(paper_orient or '').lower().startswith('land'):
        pw, ph = ph, pw
    if cols or rows:
        cols, rows = int(cols or 1), int(rows or 1)
    else:
        cols, rows = GRID.get(int(per_page), (2, 2))
    cell_w = (pw - 2 * margin - (cols - 1) * gap) / cols
    cell_h = (ph - 2 * margin - (rows - 1) * gap) / rows
    if cell_w <= 20 or cell_h <= 20:
        raise ValueError('枠が小さすぎます (%.0f×%.0fmm)。用紙を大きくするか'
                         '1ページの図数を減らしてください。' % (cell_w, cell_h))
    return pw, ph, cols, rows, cell_w, cell_h


def _valid_cases(loads, cases):
    """指定ケースのうち荷重があるものだけにする (無いものは注記して落とす)."""
    names = list(cases) if cases else loads.cases_with_load()
    if not names:
        print('注記: 荷重が付いている荷重ケースがありません。')
        return []
    skipped = [n for n in names if not loads.has_load(n)]
    if skipped:
        print('注記: 荷重が無いケースは飛ばしました: %s' % ', '.join(skipped))
    names = [n for n in names if loads.has_load(n)]
    if not names:
        print('注記: 荷重が付いている荷重ケースがありません。')
    return names


def preview_page(mgt_path, out_dir, cases=None, page=1, per_page=4,
                 cols=None, rows=None, paper_size=4, paper_orient=None,
                 margin=8.0, gap=4.0, azimuth=-40.0, elevation=24.0,
                 show_arrows=True, dpi=110, filename=None):
    """PDF を作る前の確認用に、1ページぶんを PNG で描く。

    紙面の計算は PDF と同じ page_layout() を使うので、**見えているものが
    そのまま出力になる**。戻り値は (画像パス, ページ番号, 総ページ数,
    そのページのケース名)。
    """
    _setup_japanese_font()
    loads = read_loads(mgt_path)
    names = _valid_cases(loads, cases)
    if not names:
        return None, 0, 0, []
    geom = _read_geometry(mgt_path)
    if not geom[0]:
        raise ValueError('節点が読めませんでした。mgtファイルを確認してください。')
    pw, ph, cols, rows, cell_w, cell_h = page_layout(
        per_page, cols, rows, paper_size, paper_orient, margin, gap)

    n_per = cols * rows
    n_pages = (len(names) + n_per - 1) // n_per
    page = max(1, min(int(page), n_pages))
    on_page = names[(page - 1) * n_per: page * n_per]

    fig = plt.figure(figsize=(pw / MM_PER_INCH, ph / MM_PER_INCH))
    fig.patch.set_facecolor('white')
    for k, case in enumerate(on_page):
        cx, cy = k % cols, k // cols
        x0 = margin + cx * (cell_w + gap)
        y0 = ph - margin - (cy + 1) * cell_h - cy * gap
        ax = fig.add_axes([x0 / pw, y0 / ph, cell_w / pw, cell_h / ph])
        _draw_case(ax, geom, loads, case, cell_w, cell_h, azimuth, elevation,
                   show_arrows=show_arrows)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, (filename or '11loadmap_preview') + '.png')
    fig.savefig(path, dpi=dpi, facecolor='white')
    plt.close(fig)
    return path, page, n_pages, on_page


def list_load_cases(mgt_path):
    """荷重が付いている荷重ケースと、その内訳を返す (UI のチェックボックス用)."""
    loads = read_loads(mgt_path)
    out = []
    for name in loads.cases_with_load():
        out.append({
            'name': name,
            'floor': len(loads.floor_loads_of(name)),
            'pressure': sum(len(p.elements) for p in loads.pressures_of(name)),
            'nodal': sum(len(n.nodes) for n in loads.nodal_loads_of(name)),
            'beam': sum(len(b.elements) for b in loads.beam_loads_of(name)),
        })
    return out


def plot_loadmap(mgt_path, out_dir, cases=None, per_page=4,
                 cols=None, rows=None, paper_size=4, paper_orient=None,
                 margin=8.0, gap=4.0, azimuth=-40.0, elevation=24.0,
                 show_arrows=True, fig_format='pdf', filename=None):
    """mgt / mgtx から荷重分布図を作る。生成したファイルのパス一覧を返す。

    Parameters
    ----------
    cases : list of str or None
        出力する荷重ケース名。None = 荷重が付いているケース全部。
    per_page : int
        1ページあたりの図数 (1/2/3/4/6/8/9)。cols・rows 指定が優先。
    paper_size : int
        2=A2 / 3=A3 / 4=A4 / 5=A5。paper_orient='landscape' で横向き。
    azimuth, elevation : float
        視点[deg]。既定 (-40, 24) は床の領域が面として見え、かつ壁の領域が
        重ならない角度。建物の細長さで変わるので必要なら振り直す。
    """
    _setup_japanese_font()
    loads = read_loads(mgt_path)
    names = _valid_cases(loads, cases)
    if not names:
        return []
    geom = _read_geometry(mgt_path)
    if not geom[0]:
        raise ValueError('節点が読めませんでした。mgtファイルを確認してください。')
    pw, ph, cols, rows, cell_w, cell_h = page_layout(
        per_page, cols, rows, paper_size, paper_orient, margin, gap)

    os.makedirs(out_dir, exist_ok=True)
    base = filename or '11loadmap'
    ext = 'png' if str(fig_format).lower() == 'png' else 'pdf'
    made = []
    pdf = None
    try:
        if ext == 'pdf':
            path = os.path.join(out_dir, base + '.pdf')
            pdf = PdfPages(path)
            made.append(path)
        fig = None
        for i, case in enumerate(names):
            k = i % (cols * rows)
            if k == 0:
                fig = plt.figure(figsize=(pw / MM_PER_INCH, ph / MM_PER_INCH))
            cx, cy = k % cols, k // cols
            x0 = margin + cx * (cell_w + gap)
            y0 = ph - margin - (cy + 1) * cell_h - cy * gap
            ax = fig.add_axes([x0 / pw, y0 / ph, cell_w / pw, cell_h / ph])
            _draw_case(ax, geom, loads, case, cell_w, cell_h,
                       azimuth, elevation, show_arrows=show_arrows)
            last = (i == len(names) - 1)
            if k == cols * rows - 1 or last:
                if ext == 'pdf':
                    pdf.savefig(fig)
                else:
                    p = os.path.join(out_dir, '%s_%d.png' % (base, len(made) + 1))
                    fig.savefig(p, dpi=200)
                    made.append(p)
                plt.close(fig)
                fig = None
    finally:
        if pdf is not None:
            pdf.close()
    return made


def plot_view_sheet(mgt_path, out_dir, case, azimuths=(-60, -35, -20, 20, 35, 60),
                    elevations=(12, 20, 30), paper_size=3, margin=8.0, gap=3.0,
                    filename=None):
    """視点を選ぶための比較シート。1つの荷重ケースを角度違いで並べる。"""
    _setup_japanese_font()
    loads = read_loads(mgt_path)
    geom = _read_geometry(mgt_path)
    pw, ph = PAPERS.get(int(paper_size), PAPERS[3])
    pw, ph = max(pw, ph), min(pw, ph)          # 比較シートは横向き固定
    cols, rows = len(azimuths), len(elevations)
    cell_w = (pw - 2 * margin - (cols - 1) * gap) / cols
    cell_h = (ph - 2 * margin - (rows - 1) * gap) / rows

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, (filename or '11loadmap_view') + '.pdf')
    fig = plt.figure(figsize=(pw / MM_PER_INCH, ph / MM_PER_INCH))
    for i, (az, el) in enumerate([(a, e) for e in elevations for a in azimuths]):
        cx, cy = i % cols, i // cols
        x0 = margin + cx * (cell_w + gap)
        y0 = ph - margin - (cy + 1) * cell_h - cy * gap
        ax = fig.add_axes([x0 / pw, y0 / ph, cell_w / pw, cell_h / ph])
        _draw_case(ax, geom, loads, case, cell_w, cell_h, az, el,
                   title='az=%+.0f  el=%+.0f' % (az, el),
                   show_labels=False, show_note=False)
    fig.savefig(path)
    plt.close(fig)
    return [path]
