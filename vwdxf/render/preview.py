# -*- coding: utf-8 -*-
"""Drawing IR → 用紙プレビュー PNG.

DXF と同じ Figure をそのまま描く (図形・レイヤ色・鉄筋記号・文字の実寸・見出し)。
用紙枠 (用紙 × 縮尺) と余白の破線を、図の中心に合わせて重ねる。
"""
import math

from ..style import PAPER_MARGIN_MM, PAPER_MM
from .. import sections
from ..draw.primitives import (Circle, Hatch, Line, NameRef, Poly, RebarRef, SectionRef,
                               Text, layer_color)

# ACI 色 → 画面の色 (白地で見えるように黄は濃いめ)
_ACI_HEX = {1: '#d33', 2: '#c8b400', 3: '#2a9d2a', 4: '#00a0c8', 6: '#c040c0',
            7: '#222222', 8: '#888888', 30: '#e07b00', 161: '#4f8fd0'}
_LS = {None: '-', 'dashed': '--', 'grid': '-.'}
_HA = {'BC': ('center', 'bottom'), 'MC': ('center', 'center'), 'BL': ('left', 'bottom')}


def _color(layer):
    return _ACI_HEX.get(layer_color(layer), '#444444')


def _rot(pts, pos, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(pos[0] + x * c - y * s, pos[1] + x * s + y * c) for x, y in pts]


def _rebar_glyph(ax, dia, c, r, color):
    import matplotlib.pyplot as plt
    x, y = c
    d = r / math.sqrt(2.0)

    def circle(rr, fill=False):
        ax.add_patch(plt.Circle((x, y), rr, fill=fill, ec=color, fc=color, lw=0.4))

    def line(x1, y1, x2, y2):
        ax.plot([x + x1, x + x2], [y + y1, y + y2], color=color, lw=0.4)

    dia = int(dia)
    if dia == 10:
        circle(r * 0.55, True)
    elif dia == 13:
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia == 16:
        circle(r)
        line(-d, d, d, -d)
    elif dia == 19:
        circle(r, True)
    elif dia == 25:
        circle(r)
        circle(r * 0.28, True)
    elif dia == 29:
        circle(r)
        line(-d, -d, d, d)
        line(-d, d, d, -d)
    elif dia in (32, 35, 38, 41, 51):
        circle(r)
        circle(r * 0.55)
    else:
        circle(r)


def preview_png(fig, out_path, paper='A3', dpi=None):
    """Figure → 用紙プレビュー PNG. 戻り値 (出力パス, 縮尺)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    try:
        from ...draw_model import _setup_japanese_font  # mgtkit 共通の日本語フォント設定
        _setup_japanese_font()
    except Exception:  # noqa: BLE001
        pass
    n = fig.scale
    pw, ph = PAPER_MM.get(paper, PAPER_MM['A3'])
    fw, fh = pw * n, ph * n
    x0, y0, x1, y1 = fig.bounds                  # 見出しも含む範囲
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    mpl, ax = plt.subplots(figsize=(9, 9 * ph / pw))
    ax.add_patch(plt.Rectangle((cx - fw / 2, cy - fh / 2), fw, fh, fill=False, ec='#888', lw=1.2))
    m_mm = PAPER_MARGIN_MM * n
    ax.add_patch(plt.Rectangle((cx - fw / 2 + m_mm, cy - fh / 2 + m_mm), fw - 2 * m_mm,
                               fh - 2 * m_mm, fill=False, ec='#ccc', lw=0.8, ls='--'))
    pad = 0.05 * max(fw, fh)
    pt_per_mm = 9 * 72.0 / (fw + 2 * pad)       # 文字を実寸で出す (図の横幅 9in)
    for p in fig.prims:
        if isinstance(p, Hatch):
            xs = [q[0] for q in p.points] + [p.points[0][0]]
            ys = [q[1] for q in p.points] + [p.points[0][1]]
            ax.fill(xs, ys, fill=False, hatch='////', ec='#f5c48a', lw=0.4, zorder=0)
        elif isinstance(p, Poly):
            pts = list(p.points) + ([p.points[0]] if p.closed else [])
            ax.plot([q[0] for q in pts], [q[1] for q in pts], color=_color(p.layer),
                    lw=0.6, ls=_LS.get(p.ltype, '-'))
        elif isinstance(p, Line):
            ax.plot([p.p1[0], p.p2[0]], [p.p1[1], p.p2[1]], color=_color(p.layer),
                    lw=0.6, ls=_LS.get(p.ltype, '-'))
        elif isinstance(p, Circle):
            ax.add_patch(plt.Circle(p.center, p.r, fill=False, ec=_color(p.layer), lw=0.6))
        elif isinstance(p, SectionRef):
            col = _color(p.layer)
            for it in sections.symbol_shape(p.sec, p.down):
                if it[0] == 'poly':
                    pts = _rot([(x * 1000.0, y * 1000.0) for x, y in it[1]], p.pos, p.rotation)
                    if it[2]:
                        pts = pts + [pts[0]]
                    ax.plot([q[0] for q in pts], [q[1] for q in pts], color=col, lw=0.5)
                else:
                    c = _rot([(it[1][0] * 1000.0, it[1][1] * 1000.0)], p.pos, p.rotation)[0]
                    ax.add_patch(plt.Circle(c, it[2] * 1000.0, fill=False, ec=col, lw=0.5))
        elif isinstance(p, NameRef):
            ax.text(p.pos[0], p.pos[1], p.text, fontsize=max(2.0, p.height * pt_per_mm),
                    color=_color(p.layer), ha='center', va='center', rotation=p.rotation,
                    rotation_mode='anchor')
        elif isinstance(p, RebarRef):
            _rebar_glyph(ax, p.dia, p.pos, p.r, _color(p.layer))
        elif isinstance(p, Text):
            ha, va = _HA.get(p.align, ('center', 'bottom'))
            ax.text(p.pos[0], p.pos[1], p.text, fontsize=max(2.0, p.height * pt_per_mm),
                    color=_color(p.layer), ha=ha, va=va, rotation=p.rotation,
                    rotation_mode='anchor')
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(cx - fw / 2 - pad, cx + fw / 2 + pad)
    ax.set_ylim(cy - fh / 2 - pad, cy + fh / 2 + pad)
    mpl.tight_layout()
    mpl.savefig(out_path, dpi=dpi or (180 if fig.kind == 'list' else 110))
    plt.close(mpl)
    return out_path, n
