# -*- coding: utf-8 -*-
"""RCスラブの応力図と検討書 PDF (matplotlib).

図はどれも平面 (XY) への投影で、モデル全体の範囲を同じ縮尺で描く。
    背景   : 全要素 (線材・板要素の辺) を灰色の細線
    曲げ   : 対象の板要素を塗り、要素中心に主曲げモーメントの矢印
             (Mmax の向きと Mmin の向き。長さは絶対値に比例、色は値の凡例)
             正 (下端引張) は外向き、負 (上端引張) は内向きの矢じり
    せん断 : Vmax (要素中心値) で要素を塗り分けるコンター
             Vmax = Vxx と Vyy のうち絶対値が大きい方 (符号付き、要素座標系)
最大・最小の要素に「最大 : 12.2」「最小 : -28.5」と注記する。

検討書の構成は事務所のひな型 (Pages 版「RCスラブの検討」) に合わせる。
    ■ RCスラブの検討
    □ S28 → <TL> 曲げ・せん断 (大きく縦に2図) → 本文
    以下に短期荷重時の検討を示す. → <TL+KX時> 曲げ・せん断 (横に2図) ...
    以上より最大応力は以下の通り. 最大正曲げ / 最大負曲げ / 最大せん断力
"""

import math
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa
from matplotlib.lines import Line2D  # noqa: E402

MM = 1 / 25.4
A4 = (210.0, 297.0)

#: MIDAS のコンターに近い12色 (最小 → 最大)
PALETTE = ['#0000ff', '#0049ff', '#00a0ff', '#00e0e0', '#00e070', '#40ff00',
           '#a0ff00', '#e8ff00', '#ffd000', '#ff9000', '#ff5000', '#ff0000']
BG_COLOR = '#9c9c9c'
M_FILL = '#5b9be0'

#: 本文の既定 (画面で断面ごとに書き換えられる)
DEFAULT_POST = ('次項に上記の応力に対する検討結果を示す.\n'
                'なお、長期の検定比が75%を下回っていることから、'
                '鉛直深度k=1.0に対しても問題ないことを確認している。')
DEFAULT_LONG_ONLY_PRE = ('最もクリティカルな荷重条件である長期荷重時の検討を'
                         '示す.')

CAP_M = '曲げモーメント(ベクトル表示, 単位：kNm/m)'
CAP_V = 'せん断力（最大方向表示, 単位：kN/m）'


def _font(family):
    plt.rcParams['font.family'] = family
    plt.rcParams['pdf.fonttype'] = 42


# ---------------------------------------------------------------------------
# 幾何
# ---------------------------------------------------------------------------

def _local_axes(P, angle_deg=0.0):
    """板要素の要素座標軸 (x, y) を返す.

    MIDAS: x は辺 N1-N4 の中点 → 辺 N2-N3 の中点 (三角形は N1→N2)、
    z は面の法線。/db/elem の ANGLE で z 軸まわりに回転する。
    """
    P = np.asarray(P, float)
    if len(P) >= 4:
        x = (P[1] + P[2]) / 2 - (P[0] + P[3]) / 2
        z = np.cross(P[2] - P[0], P[3] - P[1])
    else:
        x = P[1] - P[0]
        z = np.cross(P[1] - P[0], P[2] - P[0])
    z = z / (np.linalg.norm(z) or 1.0)
    x = x / (np.linalg.norm(x) or 1.0)
    y = np.cross(z, x)
    if angle_deg:
        a = math.radians(angle_deg)
        x, y = math.cos(a) * x + math.sin(a) * y, \
            -math.sin(a) * x + math.cos(a) * y
    return x, y


class Model:
    """取得データから作図用の配列を作る (図ごとに作り直さない)."""

    def __init__(self, data):
        self.d = data
        nodes = data['nodes']
        self.xy = {n: (v[0], v[1]) for n, v in nodes.items()}
        segs = [[self.xy[a], self.xy[b]] for a, b in data['lines']
                if a in self.xy and b in self.xy]
        seen = set()
        for p in data['plates'].values():
            nd = p['nodes']
            for i in range(len(nd)):
                a, b = nd[i], nd[(i + 1) % len(nd)]
                k = (min(a, b), max(a, b))
                if k in seen or a not in self.xy or b not in self.xy:
                    continue
                seen.add(k)
                segs.append([self.xy[a], self.xy[b]])
        self.bg = segs
        pts = np.array(list(self.xy.values()))
        self.xmin, self.ymin = pts.min(axis=0)
        self.xmax, self.ymax = pts.max(axis=0)
        self.poly, self.cen, self.axes, self.size = {}, {}, {}, {}
        for e, p in data['plates'].items():
            P3 = [nodes[n] for n in p['nodes']]
            poly = [self.xy[n] for n in p['nodes']]
            self.poly[e] = poly
            self.cen[e] = tuple(np.mean(poly, axis=0))
            self.axes[e] = _local_axes(P3, p.get('angle', 0.0))
            a = np.asarray(poly)
            self.size[e] = math.sqrt(abs(0.5 * np.sum(
                a[:, 0] * np.roll(a[:, 1], -1) - np.roll(a[:, 0], -1)
                * a[:, 1])))

    def aspect(self):
        return (self.ymax - self.ymin) / max(self.xmax - self.xmin, 1e-9)


def vmax_of(r):
    """Vxx と Vyy のうち絶対値が大きい方 (符号付き)."""
    vx, vy = r[6], r[7]
    return vx if abs(vx) >= abs(vy) else vy


def section_stats(data, tid, case):
    """(Mmax の最大, その要素), (Mmin の最小, 要素), (Vmax 最大, 要素),
    (Vmax 最小, 要素)."""
    rows = data['forces'].get(case, {})
    els = [e for e in data['targets'][tid]['elems'] if e in rows]
    if not els:
        return None
    em = max(els, key=lambda e: rows[e][3])
    en = min(els, key=lambda e: rows[e][4])
    ev = [(vmax_of(rows[e]), e) for e in els]
    vmx = max(ev)
    vmn = min(ev)
    return {'mmax': (rows[em][3], em), 'mmin': (rows[en][4], en),
            'vmax': vmx, 'vmin': vmn}


# ---------------------------------------------------------------------------
# 1図
# ---------------------------------------------------------------------------

def _levels(vmin, vmax, n=len(PALETTE)):
    if not (vmax > vmin):
        vmax = vmin + 1.0
    return np.linspace(vmin, vmax, n + 1)


def _color_of(v, lv):
    i = int(np.searchsorted(lv, v, side='right')) - 1
    return PALETTE[max(0, min(len(PALETTE) - 1, i))]


def _legend(fig, rect, lv, title):
    """右側の凡例 (上が最大)。rect = [x0, y0, w, h] (figure 比)."""
    ax = fig.add_axes(rect)
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.add_patch(plt.Rectangle((0.005, 0.005), 0.99, 0.99, fill=False,
                               lw=0.4, ec='#666666', clip_on=False))
    ax.text(0.5, 0.975, 'PLATE FORCE', ha='center', va='top', fontsize=4.0)
    ax.plot([0.05, 0.95], [0.925, 0.925], lw=0.3, color='#666666')
    ax.text(0.5, 0.905, title, ha='center', va='top',
            fontsize=4.2 if len(title) <= 7 else 3.4)
    n = len(PALETTE)
    top, h = 0.83, 0.06
    for i in range(n):
        y = top - (i + 1) * h
        ax.add_patch(plt.Rectangle((0.08, y), 0.2, h, lw=0,
                                   fc=PALETTE[n - 1 - i]))
    for i in range(n + 1):
        ax.text(0.94, top - i * h, '%.2f' % lv[n - i], ha='right',
                va='center', fontsize=3.9)
    return ax


def _mark(ax, xy, text, size):
    ax.plot([xy[0]], [xy[1]], marker='x', ms=2.2, mew=0.6, color='k',
            zorder=6)
    ax.annotate(text, xy, xytext=(-2, 1.5), textcoords='offset points',
                ha='right', va='bottom', fontsize=size, zorder=7,
                bbox=dict(boxstyle='square,pad=0.08', fc='white', ec='none',
                          alpha=0.75))


def draw_panel(fig, rect, M, tid, case, kind, font_pt=7.0):
    """rect=[x0,y0,w,h] (figure 比) に1図を描く。kind='M'|'V'."""
    d = M.d
    rows = d['forces'].get(case, {})
    els = [e for e in d['targets'][tid]['elems'] if e in rows]
    x0, y0, w, h = rect
    lw_ = w * 0.15       # 凡例の幅
    ax = fig.add_axes([x0, y0, w - lw_ - w * 0.02, h])
    ax.set_axis_off()
    pad = 0.03 * max(M.xmax - M.xmin, M.ymax - M.ymin)
    ax.set_xlim(M.xmin - pad, M.xmax + pad)
    ax.set_ylim(M.ymin - pad, M.ymax + pad)
    ax.set_aspect('equal', adjustable='box', anchor='C')
    ax.add_collection(LineCollection(M.bg, colors=BG_COLOR, linewidths=0.18,
                                     zorder=1))
    st = section_stats(d, tid, case)
    if st is None:
        ax.text(0.5, 0.5, '断面力なし', transform=ax.transAxes,
                ha='center', va='center')
        return
    polys = [M.poly[e] for e in els]
    if kind == 'V':
        vals = [vmax_of(rows[e]) for e in els]
        lv = _levels(min(vals), max(vals))
        ax.add_collection(PolyCollection(
            polys, facecolors=[_color_of(v, lv) for v in vals],
            edgecolors='#555555', linewidths=0.15, zorder=2))
        _legend(fig, [x0 + w - lw_, y0 + h * 0.52, lw_, h * 0.48], lv,
                'せん断力-最大')
        (vmx, e1), (vmn, e2) = st['vmax'], st['vmin']
        _mark(ax, M.cen[e1], '最大 : %.1f' % vmx, font_pt)
        _mark(ax, M.cen[e2], '最小 : %.1f' % vmn, font_pt)
        return
    # --- 曲げ: 塗り + 主曲げの矢印
    ax.add_collection(PolyCollection(polys, facecolors=M_FILL,
                                     edgecolors='#3a6fb0', linewidths=0.15,
                                     zorder=2))
    mvals = [v for e in els for v in (rows[e][3], rows[e][4])]
    lv = _levels(min(mvals), max(mvals))
    amax = max(abs(v) for v in mvals) or 1.0
    seg = np.median([M.size[e] for e in els]) * 0.62
    X, Y, U, V, C = [], [], [], [], []
    for e in els:
        r = rows[e]
        ex, ey = M.axes[e]
        a = math.radians(r[5])
        d1 = math.cos(a) * ex + math.sin(a) * ey           # Mmax の向き
        d2 = -math.sin(a) * ex + math.cos(a) * ey          # Mmin の向き
        cx, cy = M.cen[e]
        for val, dv in ((r[3], d1), (r[4], d2)):
            L = seg * abs(val) / amax
            if L <= 1e-9:
                continue
            dxy = np.array(dv[:2])
            nrm = np.linalg.norm(dxy)
            if nrm < 1e-6:
                continue
            dxy = dxy / nrm * L
            col = _color_of(val, lv)
            for sgn in (1, -1):
                if val >= 0:     # 外向き
                    X.append(cx); Y.append(cy)
                    U.append(sgn * dxy[0]); V.append(sgn * dxy[1])
                else:            # 内向き
                    X.append(cx + sgn * dxy[0]); Y.append(cy + sgn * dxy[1])
                    U.append(-sgn * dxy[0]); V.append(-sgn * dxy[1])
                C.append(col)
    if X:
        ax.quiver(X, Y, U, V, color=C, angles='xy', scale_units='xy',
                  scale=1.0, width=0.0022, headwidth=3.2, headlength=3.2,
                  headaxislength=2.8, zorder=4)
    _legend(fig, [x0 + w - lw_, y0 + h * 0.52, lw_, h * 0.48], lv,
            'モーメント-ベクトル')
    (mx, e1), (mn, e2) = st['mmax'], st['mmin']
    _mark(ax, M.cen[e1], '最大 : %.1f' % mx, font_pt)
    _mark(ax, M.cen[e2], '最小 : %.1f' % mn, font_pt)


def figure_png(data, tid, case, kind, path, width_mm=120.0, font='Yu Gothic'):
    """画面プレビュー用に1図を PNG で書く."""
    _font(font)
    M = data if isinstance(data, Model) else Model(data)
    h_mm = width_mm * 0.82 * M.aspect() + 4
    fig = plt.figure(figsize=(width_mm * MM, h_mm * MM))
    draw_panel(fig, [0.0, 0.0, 1.0, 1.0], M, tid, case, kind)
    fig.savefig(path, dpi=220, facecolor='white')
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# 検討書 PDF
# ---------------------------------------------------------------------------

def short_label(label):
    """'TL+KX' → 'TL+KX時' (画面で付けた表記をそのまま使う)."""
    return str(label).strip() + '時'


def summary(data, tid, cases):
    """短期ケースの最大応力: 最大正曲げ・最大負曲げ・最大せん断力."""
    best = {'pos': None, 'neg': None, 'shear': None}
    for c in cases:
        st = section_stats(data, tid, c['name'])
        if not st:
            continue
        v = st['mmax'][0]
        if best['pos'] is None or v > best['pos'][0]:
            best['pos'] = (v, c['label'])
        v = st['mmin'][0]
        if best['neg'] is None or v < best['neg'][0]:
            best['neg'] = (v, c['label'])
        v = max(abs(st['vmax'][0]), abs(st['vmin'][0]))
        if best['shear'] is None or v > best['shear'][0]:
            best['shear'] = (v, c['label'])
    return best


def _num(v):
    try:
        return None if v is None or str(v).strip() == '' else float(v)
    except ValueError:
        raise ValueError('検定条件の入力が数値ではありません: %s' % v)


def design_forces(data, sec, short):
    """検定に使う (正曲げ, 負曲げ, せん断, 荷重ケース表記) — 注記と同じ小数1桁.

    長期: 先頭ケースの max(Mmax)・min(Mmin)・max|Vmax|
    短期: 短期ケースのまとめ (summary)
    """
    key = sec['key']
    cases = data['cases']
    if short:
        sm = summary(data, key, cases[1:])
        mp, lp = sm['pos'] if sm['pos'] else (0.0, '')
        mn, ln = sm['neg'] if sm['neg'] else (0.0, '')
        q, lq = sm['shear'] if sm['shear'] else (0.0, '')
        lab = {'pos': short_label(lp) if lp else '',
               'neg': short_label(ln) if ln else '',
               'q': short_label(lq) if lq else ''}
    else:
        st = section_stats(data, key, cases[0]['name'])
        mp, mn = (st['mmax'][0], st['mmin'][0]) if st else (0.0, 0.0)
        q = max(abs(st['vmax'][0]), abs(st['vmin'][0])) if st else 0.0
        lab = {k: cases[0]['label'] for k in ('pos', 'neg', 'q')}
    return (max(round(mp, 1), 0.0), min(round(mn, 1), 0.0), round(q, 1),
            lab)


def check_spec(data, sec):
    """画面の検定条件 → calc.check の spec (板厚の既定は MIDAS の厚さ)."""
    ck = sec.get('check') or {}
    t = _num(ck.get('t')) or data['targets'][sec['key']]['t']
    return {'Fc': _num(ck.get('Fc')) or 24.0,
            'ctype': ck.get('ctype') or '普通',
            'sd': ck.get('sd') or 'SD295', 't': t,
            'single': bool(ck.get('single')),
            'dir': 'main' if ck.get('dir') == 'main' else 'dist',
            'cover_up': _num(ck.get('cover_up')) or 30.0,
            'cover_dn': _num(ck.get('cover_dn')) or 30.0,
            'bar_up': ck.get('bar_up') or 'D13@200',
            'bar_dn': ck.get('bar_dn') or ck.get('bar_up') or 'D13@200'}


def run_checks(data, sections):
    """検定を載せる断面ごとの結果 [{'key','name','long','short'}]."""
    from mgtkit.rcslab.calc import check
    out = []
    for sec in sections:
        ck = sec.get('check') or {}
        if not ck.get('enabled'):
            continue
        key = sec['key']
        spec = check_spec(data, sec)
        name = sec.get('name') or data['targets'][key]['name']
        row = {'key': key, 'name': name, 'long': None, 'short': None}
        for term, short in (('long', False), ('short', True)):
            if short and not (sec.get('short', True)
                              and len(data['cases']) > 1):
                continue
            mp, mn, q, lab = design_forces(data, sec, short)
            row[term] = check(spec, mp, mn, q, short=short)
            row[term]['labels'] = lab
        out.append(row)
    return out


class _Page:
    """A4 縦の1ページ。y は上端からの mm で下へ進める."""

    def __init__(self, pdf, left=25.0, top=24.0, bottom=32.0):
        self.pdf = pdf
        self.left, self.top, self.bottom = left, top, bottom
        self.fig = None
        self.y = top

    def new(self):
        self.flush()
        self.fig = plt.figure(figsize=(A4[0] * MM, A4[1] * MM))
        self.y = self.top

    def flush(self):
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
            self.fig = None

    def put(self, fig):
        """別に作った1ページ (検定ページ) を差し込む."""
        self.flush()
        self.pdf.savefig(fig)
        plt.close(fig)

    def room(self):
        return A4[1] - self.bottom - self.y

    def ensure(self, h):
        if self.fig is None or self.room() < h:
            self.new()

    def text(self, s, x=None, size=10.0, weight='normal', underline=False,
             dy=None):
        x = self.left if x is None else x
        lh = size * 0.3528 * 1.55
        t = self.fig.text(x / A4[0], 1 - (self.y + size * 0.3528) / A4[1],
                          s, fontsize=size, weight=weight, va='baseline')
        if underline:
            r = self.fig.canvas.get_renderer()
            bb = t.get_window_extent(renderer=r)
            wmm = bb.width / self.fig.dpi * 25.4
            yl = self.y + size * 0.3528 + 1.0
            self.fig.add_artist(Line2D([x / A4[0], (x + wmm) / A4[0]],
                                       [1 - yl / A4[1]] * 2, lw=0.6,
                                       color='k'))
        self.y += lh if dy is None else dy
        return t

    def para(self, s, size=10.0, width_mm=160.0):
        """改行入りの本文。1行の幅を超えたら折り返す (全角1文字=size pt)."""
        per = max(8, int(width_mm / (size * 0.3528)))
        for raw in str(s).split('\n'):
            line = raw
            if not line:
                self.y += size * 0.3528 * 1.55
                continue
            while line:
                # 半角は0.55文字ぶんで数える
                n, wsum = 0, 0.0
                while n < len(line) and wsum < per:
                    wsum += 0.55 if ord(line[n]) < 0x2000 else 1.0
                    n += 1
                self.text(line[:n], size=size)
                line = line[n:]

    def panel(self, x_mm, w_mm, h_mm, M, tid, case, kind):
        rect = [x_mm / A4[0], 1 - (self.y + h_mm) / A4[1], w_mm / A4[0],
                h_mm / A4[1]]
        draw_panel(self.fig, rect, M, tid, case, kind,
                   font_pt=7.0 if w_mm > 100 else 5.5)


def build_report(data, sections, out_path, font='Yu Gothic',
                 title='RCスラブの検討'):
    """検討書 PDF を作る.

    sections: [{'key': 'T11', 'name': 'S28', 'short': True,
                'pre': '', 'post': DEFAULT_POST}, ...] (この順に並べる)
    """
    from mgtkit.rcslab.sheet import sheet_figure
    _font(font)
    M = Model(data)
    cases = data['cases']
    long_case, short_cases = cases[0], cases[1:]
    checks = {r['key']: r for r in run_checks(data, sections)}
    asp = M.aspect()
    big_w = 100.0
    big_h = big_w * 0.82 * asp
    sm_w = 80.0
    sm_h = sm_w * 0.82 * asp
    gap_x = 5.0
    with PdfPages(out_path) as pdf:
        pg = _Page(pdf)
        pg.new()
        pg.text('■ ' + title, size=10.5, dy=10)
        for si, sec in enumerate(sections):
            tid = sec['key']
            if si > 0:
                pg.new()
            pg.y += 2
            pg.text('□ ', size=11, dy=0)
            pg.text(sec.get('name') or data['targets'][tid]['name'],
                    x=pg.left + 4.2, size=12.5, weight='bold', dy=7.5)
            if sec.get('pre'):
                pg.para(sec['pre'])
                pg.y += 3
            pg.text('<%s>' % long_case['label'])
            pg.text(CAP_M, underline=True, dy=7)
            pg.ensure(big_h + 3)
            pg.panel(pg.left - 2, big_w, big_h, M, tid, long_case['name'], 'M')
            pg.y += big_h + 6
            pg.ensure(big_h + 14)
            pg.text(CAP_V, underline=True, dy=7)
            pg.panel(pg.left - 2, big_w, big_h, M, tid, long_case['name'], 'V')
            pg.y += big_h + 6
            if sec.get('post'):
                pg.ensure(15)
                pg.para(sec['post'])
            ck = checks.get(tid)
            if ck:
                pg.put(sheet_figure(ck['long'], ck['name'],
                                    '長期 (%s)' % long_case['label']))
            if not (sec.get('short', True) and short_cases):
                continue
            pg.new()
            pg.text('以下に短期荷重時の検討を示す.', dy=10)
            block = sm_h + 14
            for c in short_cases:
                pg.ensure(block)
                pg.text('<%s>' % short_label(c['label']))
                y_cap = pg.y
                pg.text(CAP_M, underline=True, size=10)
                pg.y = y_cap
                pg.text(CAP_V, x=pg.left + sm_w + gap_x, underline=True,
                        size=10, dy=7)
                pg.panel(pg.left - 4, sm_w, sm_h, M, tid, c['name'], 'M')
                pg.panel(pg.left - 4 + sm_w + gap_x, sm_w, sm_h, M, tid,
                         c['name'], 'V')
                pg.y += sm_h + 7
            sm = summary(data, tid, short_cases)
            pg.ensure(48)
            pg.y += 8
            pg.text('以上より最大応力は以下の通り.', dy=10)
            if sm['pos']:
                pg.text('最大正曲げ：%.1fkNm/m（%s）'
                        % (sm['pos'][0], short_label(sm['pos'][1])))
            if sm['neg']:
                pg.text('最大負曲げ：%.1fkNm/m（%s）'
                        % (sm['neg'][0], short_label(sm['neg'][1])))
            if sm['shear']:
                pg.text('最大せん断力：%.1fkN/m（%s）'
                        % (sm['shear'][0], short_label(sm['shear'][1])))
            pg.y += 5
            pg.text('上記の応力に対する検討を次項に示す.')
            if ck and ck['short']:
                pg.put(sheet_figure(ck['short'], ck['name'], '短期'))
        pg.flush()
    return out_path


def build_figures(data, sections, out_dir, font='Yu Gothic'):
    """検討書に貼らずに使う人向けに、1図ずつの PNG も書き出す."""
    M = Model(data)
    made = []
    os.makedirs(out_dir, exist_ok=True)
    for sec in sections:
        tid = sec['key']
        name = sec.get('name') or data['targets'][tid]['name']
        cs = data['cases'] if sec.get('short', True) else data['cases'][:1]
        for c in cs:
            for kind in ('M', 'V'):
                fn = '%s_%s_%s.png' % (_safe(name), _safe(c['label']),
                                        'moment' if kind == 'M' else 'shear')
                made.append(figure_png(M, tid, c['name'], kind,
                                       os.path.join(out_dir, fn), font=font))
    return made


def _safe(s):
    return ''.join(ch if ch not in '\\/:*?"<>| ' else '_' for ch in str(s))
