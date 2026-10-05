# -*- coding: utf-8 -*-
"""RCスラブの検定ページ (A4 縦1枚) — calc.check の結果を読みやすく並べる.

上から 設計条件 → 設計用応力 → 曲げ → せん断 → 構造規定 → 記号・出典。
式は数値を代入した形で1行ずつ書き、判定は行末に OK/NG で示す。
(元の Excel の印刷体裁には合わせない。2026-09-28 ユーザー要望)
"""

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

MM = 1 / 25.4
A4 = (210.0, 297.0)
L, R = 25.0, 187.0          # 左右の余白 (応力図のページと同じ左端)
OK_C = '#1d4ed8'
NG_C = '#c00000'
RULE = '#555555'
SUB = '#666666'
TOP = 22.0                  # 本文の上端 (mm)
BOTTOM = 32.0               # 下の余白 (計算書のページ番号用に空ける)


class _Flow:
    """mm 座標 (左上原点) で上から順に書く."""

    def __init__(self, fig, k=1.0):
        self.f = fig
        self.k = k          # 縦方向と文字の縮小率 (下の余白を確保するため)
        self.y = TOP

    def Y(self, y):
        """論理 y (mm) → 紙面の y (上端から縮小率 k で詰める)."""
        return TOP + (y - TOP) * self.k

    def t(self, x, s, size=9.0, ha='left', color='k', weight='normal',
          y=None):
        yy = self.Y(self.y if y is None else y)
        self.f.text(x / A4[0], 1 - yy / A4[1], s, fontsize=size * self.k,
                    ha=ha, va='baseline', color=color, weight=weight)

    def hline(self, x0=L, x1=R, lw=0.5, color=RULE, y=None):
        yy = self.Y(self.y if y is None else y)
        self.f.add_artist(Line2D([x0 / A4[0], x1 / A4[0]],
                                 [1 - yy / A4[1]] * 2, lw=lw, color=color))

    def vline(self, x, y0, y1, lw=0.4, color=RULE):
        self.f.add_artist(Line2D([x / A4[0]] * 2,
                                 [1 - self.Y(y0) / A4[1],
                                  1 - self.Y(y1) / A4[1]],
                                 lw=lw, color=color))

    def rect(self, x, y, w, h, fc='none', ec='k', lw=0.5):
        self.f.add_artist(Rectangle(
            (x / A4[0], 1 - self.Y(y + h) / A4[1]), w / A4[0],
            h * self.k / A4[1], fc=fc, ec=ec, lw=lw,
            transform=self.f.transFigure))

    def head(self, no, title, ref=''):
        self.y += 8.5
        self.t(L, '%d.  %s' % (no, title), size=10.5, weight='bold')
        if ref:
            self.t(R, ref, size=7.5, ha='right', color=SUB)
        self.y += 2.0
        self.hline(lw=0.6)
        self.y += 5.2

    def judge(self, x, ok, size=9.0, ha='right', y=None):
        self.t(x, 'OK' if ok else 'NG', size=size, ha=ha,
               color=OK_C if ok else NG_C, weight='bold', y=y)


def _n(v, nd=1):
    return ('{:,.%df}' % nd).format(v)


def _bar_txt(b):
    return b['spec']


def _sketch(fl, x, y, w, h, r):
    """スラブ断面図 (板厚・かぶり・鉄筋径は実寸比、縦横同縮尺).

    外側の段の鉄筋は断面 (円)、内側の段は直交方向の鉄筋を帯で描く
    (元の Excel の図と同じ表し方)。
    """
    from mgtkit.rcslab.calc import BAR_OUTER
    sp = r['spec']
    t = float(sp['t'])
    ax = fl.f.add_axes([x / A4[0], 1 - fl.Y(y + h) / A4[1], w / A4[0],
                        h * fl.k / A4[1]])
    ax.set_axis_off()
    width = t * w / h                     # 軸の縦横比に合わせた断面の幅 (mm)
    ax.set_xlim(0, width)
    ax.set_ylim(0, t)
    ax.set_aspect('equal', adjustable='box', anchor='C')
    ax.add_patch(Rectangle((0, 0), width, t, fc='#dcdcdc', ec='k', lw=0.6))

    def _layer(cover, bar, top):
        o = BAR_OUTER[bar['dmax']]
        yc = t - cover - o / 2 if top else cover + o / 2      # 円の中心
        yb = t - cover - o * 1.5 if top else cover + o * 1.5  # 帯の中心
        ax.add_patch(Rectangle((width * 0.02, yb - o * 0.35), width * 0.96,
                               o * 0.7, fc='#999999', ec='k', lw=0.5))
        n = 6
        for i in range(n):
            xc = width * (i + 0.5) / n
            ax.add_patch(Circle((xc, yc), o / 2, fc='#999999', ec='k',
                                lw=0.5))
        return yc

    labs = []
    yc = _layer(float(sp['cover_up']), r['bar_up'], True)
    labs.append((yc, ('' if r['single'] else '上端 ') + r['bar_up']['spec']))
    if not r['single']:
        yc = _layer(float(sp['cover_dn']), r['bar_dn'], False)
        labs.append((yc, '下端 ' + r['bar_dn']['spec']))
    for yc, s in labs:
        fl.t(x + w + 2, s, size=7.5, y=y + h * (1 - yc / t) + 1.0)
    fl.t(x + w / 2, 't = %.0f mm' % t, size=7.5, ha='center', y=y + h + 4)


def draw_sheet(fig, r, name, term_label, k=1.0):
    """1ページを描き、論理上の最下端 y (mm) を返す."""
    fl = _Flow(fig, k)
    m, g, sp = r['mat'], r['geo'], r['spec']
    short = r['short']
    pos, neg, sh = r['pos'], r['neg'], r['shear']
    lab = r.get('labels') or {}

    # ---------------------------------------------------------------- 見出し
    fl.t(L, '□ %s　断面検定（%s）' % (name, term_label), size=13,
         weight='bold')
    ok = r['ok']
    fig.add_artist(FancyBboxPatch(
        ((R - 30) / A4[0], 1 - fl.Y(fl.y + 2.5) / A4[1]), 30 / A4[0],
        8.5 * k / A4[1], boxstyle='round,pad=0,rounding_size=0.006',
        transform=fig.transFigure, fc='none', ec=OK_C if ok else NG_C,
        lw=1.2))
    fl.t(R - 15, '判定  ' + ('OK' if ok else 'NG'), size=11, ha='center',
         color=OK_C if ok else NG_C, weight='bold', y=fl.y + 0.6)
    fl.y += 4.5
    fl.t(L, '%s配筋・%sで検定する。' % (
        'シングル' if r['single'] else 'ダブル',
        '主筋方向 (短辺・外側の段)' if r['main'] else
        '配力筋方向 (長辺・内側の段)'), size=8.5, color=SUB)

    # ---------------------------------------------------------------- 1
    fl.head(1, '設計条件')
    y0 = fl.y
    ft, fs = r['ft'], r['fs']
    term = '短期' if short else '長期'
    rows = [
        ('コンクリート', 'Fc%.0f（%s）' % (m['Fc'], m['ctype'])),
        ('', 'γ=%s kN/m³、Ec=%s N/mm²、n=%d' % (_n(m['gamma']),
                                                _n(m['Ec'], 0), m['n'])),
        ('鉄筋', m['sd']),
        ('スラブ厚', 't = %.0f mm' % float(sp['t'])),
    ]
    if r['single']:
        rows.append(('配筋', '%s（at=%s mm²/m）上かぶり %.0f mm' % (
            r['bar_up']['spec'], _n(r['bar_up']['at'], 0),
            float(sp['cover_up']))))
    else:
        rows.append(('上端筋', '%s（at=%s mm²/m）かぶり %.0f mm' % (
            r['bar_up']['spec'], _n(r['bar_up']['at'], 0),
            float(sp['cover_up']))))
        rows.append(('下端筋', '%s（at=%s mm²/m）かぶり %.0f mm' % (
            r['bar_dn']['spec'], _n(r['bar_dn']['at'], 0),
            float(sp['cover_dn']))))
    rows.append(('許容応力度（%s）' % term,
                 'ft = %.0f N/mm²、fs = %s N/mm²' % (ft, _n(fs, 3))))
    for a, b in rows:
        fl.t(L + 2, a, size=9)
        fl.t(L + 32, b, size=9)
        fl.y += 5.4
    _sketch(fl, R - 58, y0 - 3, 26, 22, r)
    fl.y -= 1.5

    # ---------------------------------------------------------------- 2
    fl.head(2, '設計用応力', '応力図の最大・最小 (要素中心値)')
    items = [('正曲げ', 'M', pos['M'], 'kN·m/m', lab.get('pos')),
             ('負曲げ', 'M', neg['M'], 'kN·m/m', lab.get('neg')),
             ('せん断力', 'Q', sh['Q'], 'kN/m', lab.get('q'))]
    for i, (a, s, v, u, lb) in enumerate(items):
        x = L + 2 + i * 54
        fl.t(x, a, size=9)
        fl.t(x + 15, '%s = %s %s' % (s, _n(v), u), size=9.5, weight='bold')
        if lb:
            fl.t(x + 15, '（%s）' % lb, size=8, color=SUB, y=fl.y + 4.3)
    fl.y += 5.0

    # ---------------------------------------------------------------- 3
    fl.head(3, '曲げモーメントに対する検定', 'RC規準 13条4. (13.1)式')
    fl.t(L + 2, '必要鉄筋量　at,req = |M| / (ft・j)、　j = 7/8・d', size=9)
    fl.y += 4.5
    cx = [L + 2, L + 70, L + 118]         # 項目 / 正曲げ / 負曲げ
    top = fl.y
    fl.hline(lw=0.6)
    fl.y += 4.6
    fl.t(cx[1] + 22, '正曲げ（下端筋）' if not r['single'] else '正曲げ',
         size=9, ha='center')
    fl.t(cx[2] + 22, '負曲げ（上端筋）' if not r['single'] else '負曲げ',
         size=9, ha='center')
    fl.y += 2.0
    fl.hline(lw=0.4)
    tb = [
        ('設計用曲げモーメント  M', 'kN·m/m', _n(pos['M']), _n(neg['M'])),
        ('有効せい  d', 'mm', _n(pos['d']), _n(neg['d'])),
        ('応力中心距離  j', 'mm', _n(pos['j']), _n(neg['j'])),
        ('必要鉄筋量  at,req', 'mm²/m', _n(pos['at_need'], 0),
         _n(neg['at_need'], 0)),
        ('配筋', '', pos['bar']['spec'], neg['bar']['spec']),
        ('鉄筋量  at', 'mm²/m', _n(pos['bar']['at'], 0),
         _n(neg['bar']['at'], 0)),
        ('検定比  at,req / at', '', '%.2f' % pos['ratio'],
         '%.2f' % neg['ratio']),
    ]
    for a, u, vp, vn in tb:
        fl.y += 5.0
        fl.t(cx[0], a, size=9)
        fl.t(cx[0] + 60, u, size=8, ha='right', color=SUB)
        fl.t(cx[1] + 22, vp, size=9, ha='center')
        fl.t(cx[2] + 22, vn, size=9, ha='center')
    fl.y += 5.2
    fl.t(cx[0], '判定', size=9)
    fl.judge(cx[1] + 22, pos['ok'], ha='center')
    fl.judge(cx[2] + 22, neg['ok'], ha='center')
    fl.y += 2.2
    fl.hline(lw=0.6)
    fl.vline(cx[1] - 3, top, fl.y)
    fl.y += 5.5
    for side, b in (('正曲げ', pos), ('負曲げ', neg)):
        fl.t(L + 2, '%s：at,req = %s×10⁶ / (%.0f × %s) = %s mm²/m  ≦  at = %s'
             % (side, _n(abs(b['M'])), ft, _n(b['j']), _n(b['at_need'], 0),
                _n(b['bar']['at'], 0)), size=8.5)
        fl.y += 4.6
    fl.t(L + 2, '参考：ひび割れモーメント Mc = 0.56√Fc・t²/6 = %s kN·m/m、'
         '|M|/Mc = %.2f（正）・%.2f（負）'
         % (_n(r['Mc']), pos['mc_ratio'], neg['mc_ratio']), size=8.5,
         color=SUB)

    # ---------------------------------------------------------------- 4
    fl.head(4, 'せん断力に対する検定', 'RC規準 15条・18条4.')
    fl.t(L + 2, '許容せん断力　Qa = fs・b・j = %s × 1000 × %s / 10³ = %s kN/m'
         % (_n(fs, 3), _n(sh['j']), _n(sh['fsj'])), size=9)
    fl.y += 5.4
    fl.t(L + 2, 'Q / Qa = %s / %s = %.2f  ≦ 1.0'
         % (_n(sh['Q']), _n(sh['fsj']), sh['ratio']), size=9)
    fl.judge(R, sh['ok'])

    # ---------------------------------------------------------------- 5
    fl.head(5, '構造規定', 'RC規準 18条')
    for rule in r['rules']:
        fl.t(L + 2, rule['name'], size=9)
        fl.t(L + 50, rule['lim'], size=9)
        fl.t(R - 16, rule['val'], size=9, ha='right')
        fl.judge(R, rule['ok'])
        fl.y += 5.2
    fl.t(L + 2, '最小スラブ厚は 80mm (軽量 100mm) を確認。表18.1 のスパンに'
         'よる最小厚さは別途確認する。', size=8, color=SUB)

    # ---------------------------------------------------------------- 記号
    fl.y += 9
    fl.t(L, 'ここに、', size=8.5)
    syms = [
        ('M, Q', '設計用曲げモーメント・せん断力 (単位幅あたり)'),
        ('d', '有効せい = t − (かぶり + %s×鉄筋最外径)'
              % ('0.5' if r['main'] else '1.5')
              + ('、正曲げは上かぶり + 0.5×最外径' if r['single'] else '')),
        ('j', '応力中心距離 = 7/8・d'),
        ('ft', '鉄筋の許容引張応力度 (表6.2、%s)' % term),
        ('fs', 'コンクリートの許容せん断応力度 (表6.1、%s)' % term),
        ('b', '単位幅 = 1000 mm'),
        ('at', '単位幅あたりの鉄筋断面積'),
    ]
    for s, d in syms:
        fl.y += 4.4
        fl.t(L + 6, s, size=8.5)
        fl.t(L + 20, ':  ' + d, size=8.5)
    fl.y += 7
    fl.t(L, '出典：日本建築学会「鉄筋コンクリート構造計算規準・同解説」(2010)'
         ' 表5.1・6.1・6.2・7.1・12.1、13条、15条、18条', size=7.5, color=SUB)
    return fl.y


def sheet_figure(r, name, term_label):
    """下に BOTTOM mm の余白が残るよう、縦方向と文字を一律に縮めて描く."""
    probe = plt.figure(figsize=(A4[0] * MM, A4[1] * MM))
    end = draw_sheet(probe, r, name, term_label)
    plt.close(probe)
    k = min(1.0, (A4[1] - BOTTOM - TOP) / max(end - TOP, 1.0))
    fig = plt.figure(figsize=(A4[0] * MM, A4[1] * MM))
    draw_sheet(fig, r, name, term_label, k)
    return fig
