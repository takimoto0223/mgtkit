# -*- coding: utf-8 -*-
"""柱脚仕様図 (入力した仕様どおりの縮尺の平面図・断面図).

グループごとに matplotlib で描き、画面表示用の PNG を書き出す (検討書の図は
tikz.py で TeX に直接描く)。記号・色はタブの説明図
(templates/_colbase_figure.html) とそろえる: BP・寸法は紺、引張側の列とそのボルトはオレンジ、コンクリートは
グレー。寸法値は mm。

平面図 (x=H方向=柱せい、y=B方向): 基礎柱形・BP・柱断面・アンカーボルト
(孔径を破線円)、引張側の列 (角丸枠)、BP・柱形の寸法と縁〜芯。
断面図 (H方向): 柱・BP・モルタル (lb−la−BP厚)・基礎柱形 (立上り高さ)・
アンカーボルトと定着 (定着金物/フック)、45°のコーン破壊面、la・lb。
"""

import math
import os

from mgtkit.colbase.calc import bolt_positions, AB_TYPES

NAVY = '#2b4a6f'
ORANGE = '#b95d18'
ORANGE_F = '#f6dcc6'
CONC = '#eef1f5'
BEAM = '#e8edf4'
CONC_E = '#8a97a8'
BP_F = '#d7e0ea'
COL_F = '#8fa3ba'
SUB = '#667788'
CONE = '#707d90'

FS = 6.5          # 文字の大きさ [pt]


def _font():
    from mgtkit.draw_model import _setup_japanese_font
    _setup_japanese_font()


def _dim(ax, p1, p2, off, text, color=NAVY, text_side=1, fs=FS):
    """寸法線。p1→p2 の測定線を法線方向に off [mm] ずらして描く.

    端部は斜めのティック、補助線は対象から少し離して始める。
    text_side=+1 は寸法線の外側 (off と同じ側)、−1 は内側に文字を置く。
    """
    x1, y1 = p1
    x2, y2 = p2
    L = math.hypot(x2 - x1, y2 - y1)
    if L <= 0:
        return
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy, ux
    a = (x1 + nx * off, y1 + ny * off)
    b = (x2 + nx * off, y2 + ny * off)
    s = math.copysign(1.0, off) if off else 1.0
    gap = 0.12 * abs(off)
    for (px, py), (qx, qy) in ((p1, a), (p2, b)):
        ax.plot([px + nx * s * gap, qx + nx * s * gap * 0.6],
                [py + ny * s * gap, qy + ny * s * gap * 0.6],
                color=color, lw=0.4)
    ax.plot([a[0], b[0]], [a[1], b[1]], color=color, lw=0.6)
    t = 0.035 * max(abs(off), 60)
    for (qx, qy) in (a, b):
        ax.plot([qx - (ux + nx) * t, qx + (ux + nx) * t],
                [qy - (uy + ny) * t, qy + (uy + ny) * t],
                color=color, lw=0.6)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    ang = math.degrees(math.atan2(uy, ux))
    if ang > 90.1 or ang < -89.9:
        ang -= 180
    d = 0.09 * max(abs(off), 60) * text_side * s
    ax.text(mx + nx * d, my + ny * d, text, ha='center', va='center',
            rotation=ang, fontsize=fs, color=color,
            bbox=dict(fc='white', ec='none', pad=0.3))


def _rect(ax, x0, y0, w, h, **kw):
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((x0, y0), w, h, **kw))


def _col_plan(ax, col):
    """柱断面 (平面)。H: せい H を x 方向."""
    from matplotlib.patches import Circle
    s = col['shape']
    H, B, tw, tf = col['H'], col['B'], col['tw'], col['tf']
    kw = dict(fc=COL_F, ec=NAVY, lw=0.6, zorder=3)
    if s == 'H':
        _rect(ax, -H / 2, -B / 2, tf, B, **kw)
        _rect(ax, H / 2 - tf, -B / 2, tf, B, **kw)
        _rect(ax, -H / 2 + tf, -tw / 2, H - 2 * tf, tw, **kw)
    elif s == 'BOX':
        _rect(ax, -H / 2, -B / 2, H, B, **kw)
        _rect(ax, -H / 2 + tf, -B / 2 + tw, H - 2 * tf, B - 2 * tw,
              fc=BP_F, ec=NAVY, lw=0.4, zorder=3)
    elif s == 'P':
        ax.add_patch(Circle((0, 0), H / 2, **kw))
        ax.add_patch(Circle((0, 0), H / 2 - tw, fc=BP_F, ec=NAVY, lw=0.4,
                            zorder=3))


def _plan(ax, sp, col):
    from matplotlib.patches import Circle, FancyBboxPatch
    D, B = sp['bp_D'], sp['bp_B']
    fD, fB = sp.get('fnd_D'), sp.get('fnd_B')
    if fD and fB:
        _rect(ax, -fD / 2, -fB / 2, fD, fB, fc=CONC, ec=CONC_E, lw=0.7)
    _rect(ax, -D / 2, -B / 2, D, B, fc=BP_F, ec=NAVY, lw=0.9, zorder=2)
    _col_plan(ax, col)
    d = sp['ab_d']
    pts = bolt_positions(sp)
    xt = max(p[0] for p in pts)
    yt = max(p[1] for p in pts)
    tens = set()
    pad = max(d, 12.0)
    if sp['nt_H'] > 0:
        row = [p for p in pts if abs(p[0] - xt) < 1e-6]
        tens.update(row)
        ys = [p[1] for p in row]
        ax.add_patch(FancyBboxPatch(
            (xt - pad, min(ys) - pad), 2 * pad, max(ys) - min(ys) + 2 * pad,
            boxstyle='round,pad=0,rounding_size=%g' % (pad * 0.8),
            fc='none', ec=ORANGE, lw=0.9, zorder=5))
    if sp['nt_B'] > 0:
        row = [p for p in pts if abs(p[1] - yt) < 1e-6]
        tens.update(row)
        xs = [p[0] for p in row]
        ax.add_patch(FancyBboxPatch(
            (min(xs) - pad, yt - pad), max(xs) - min(xs) + 2 * pad, 2 * pad,
            boxstyle='round,pad=0,rounding_size=%g' % (pad * 0.8),
            fc='none', ec=ORANGE, lw=0.9, zorder=5))
    for p in pts:
        t = p in tens
        ax.add_patch(Circle(p, d / 2, fc=ORANGE_F if t else 'white',
                            ec=ORANGE if t else '#223344', lw=0.7, zorder=6))
        if sp.get('hole'):
            ax.add_patch(Circle(p, sp['hole'] / 2, fc='none', ec=NAVY,
                                lw=0.4, ls=(0, (2, 2)), zorder=6))
    # 寸法
    oy = -(fB / 2 if fB else B / 2)
    step = 0.09 * max(D, B, fD or 0, fB or 0)
    _dim(ax, (-D / 2, -B / 2), (D / 2, -B / 2), -(abs(oy) - B / 2 + step),
         'BP せい %.0f' % D)
    if fD and fB:
        _dim(ax, (-fD / 2, -fB / 2), (fD / 2, -fB / 2), -step * 2,
             '柱形せい %.0f' % fD)
        _dim(ax, (-fD / 2, fB / 2), (-fD / 2, -fB / 2), -step * 2,
             '柱形幅 %.0f' % fB)
    ox = (fD / 2 if fD else D / 2)
    _dim(ax, (-D / 2, B / 2), (-D / 2, -B / 2), -((ox - D / 2) + step),
         'BP 幅 %.0f' % B)
    if sp['nt_H'] > 0:
        top = fB / 2 if fB else B / 2
        _dim(ax, (xt, B / 2), (D / 2, B / 2), (top - B / 2) + step * 0.8,
             '%.0f' % sp['dtl_H'])
    if sp['nt_B'] > 0:
        right = fD / 2 if fD else D / 2
        _dim(ax, (D / 2, yt), (D / 2, B / 2), -((right - D / 2) + step * 0.8),
             '%.0f' % sp['dtl_B'])
    ax.set_aspect('equal')
    ax.axis('off')
    lim = max(fD or D, fB or B) / 2 + step * 3.2
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax._cb_title = '平面図 (横: H方向=柱せい、縦: B方向)'
    ab = AB_TYPES.get(sp['ab_type'], (0, 0, sp['ab_type']))[2]
    ax.text(-lim, -lim, '%d-M%.0f %s  孔径 %s  引張側 H%d本・B%d本'
            % (sp['n_all'], d, ab.split(' (')[0],
               '%.0f' % sp['hole'] if sp.get('hole') else '—',
               sp['nt_H'], sp['nt_B']),
            fontsize=FS, color='#223344', va='bottom')


def _col_elev(ax, col, z0, h):
    """柱 (H方向断面)。せい方向の板を太線で."""
    s = col['shape']
    H = col['H'] if s != 'P' else col['H']
    t = col['tf'] if s in ('H', 'BOX') else col['tw']
    _rect(ax, -H / 2, z0, H, h, fc='#dbe3ec', ec=NAVY, lw=0.7, zorder=3)
    _rect(ax, -H / 2, z0, t, h, fc=COL_F, ec=NAVY, lw=0.4, zorder=3)
    _rect(ax, H / 2 - t, z0, t, h, fc=COL_F, ec=NAVY, lw=0.4, zorder=3)
    ax.text(0, z0 + h * 0.55, '柱\n%s' % col['name'], ha='center',
            va='center', fontsize=FS - 1, color='#223344', zorder=4,
            bbox=dict(fc='#dbe3ec', ec='none', pad=0.5, alpha=0.9))


def _section(ax, sp, col):
    from matplotlib.patches import Arc
    D = sp['bp_D']
    t = sp['bp_t']
    la = sp.get('la') or 20 * sp['ab_d']
    lb = sp['lb']
    d = sp['ab_d']
    top = max(lb - la, t)            # 柱形上面から BP 上面まで
    mort = top - t
    fD = sp.get('fnd_D') or (D + 200.0)
    fh = sp.get('fnd_h')
    bottom = -(la + max(150.0, 0.25 * la))
    # 基礎 (柱形+基礎梁)
    if fh and fh < la + 50:
        wbeam = fD + 0.5 * fD
        _rect(ax, -wbeam / 2, bottom, wbeam, -fh - bottom, fc=BEAM,
              ec='none')
        ax.plot([-wbeam / 2, -fD / 2], [-fh, -fh], color=CONC_E, lw=0.7)
        ax.plot([fD / 2, wbeam / 2], [-fh, -fh], color=CONC_E, lw=0.7)
        _rect(ax, -fD / 2, -fh, fD, fh, fc=CONC, ec='none')
        ax.plot([-fD / 2, -fD / 2, fD / 2, fD / 2], [-fh, 0, 0, -fh],
                color=CONC_E, lw=0.7)
        ax.text(wbeam / 2 - 10, bottom + 15, '基礎梁', ha='right',
                va='bottom', fontsize=FS - 0.5, color=SUB)
    else:
        _rect(ax, -fD / 2, bottom, fD, -bottom, fc=CONC, ec='none')
        ax.plot([-fD / 2, -fD / 2, fD / 2, fD / 2], [bottom, 0, 0, bottom],
                color=CONC_E, lw=0.7)
    # モルタル・BP・柱
    if mort > 0:
        _rect(ax, -D / 2 + 10, 0, D - 20, mort, fc='#f4f5f7', ec=CONE,
              lw=0.4, zorder=2)
    _rect(ax, -D / 2, mort, D, t, fc=BP_F, ec=NAVY, lw=0.8, zorder=3)
    hc = max(0.45 * D, 150.0)
    _col_elev(ax, col, top, hc)
    # アンカーボルト (H方向の両端列。引張側=右)
    xs = []
    if sp['nt_H'] > 0:
        xr = D / 2 - sp['dtl_H']
        xs = [(-xr, False), (xr, True)]
    else:
        xs = [(0.0, True)]
    Dp = sp.get('anc_Dp') or 0.0
    hook = sp['anc_type'] == 'hook'
    for x, tens in xs:
        c = ORANGE if tens else '#223344'
        z_end = -la
        ax.plot([x, x], [top + 1.6 * d, z_end], color=c,
                lw=max(1.2, d / 12), solid_capstyle='butt', zorder=4)
        # ダブルナット
        for k in range(2):
            _rect(ax, x - 0.9 * d, top + k * 0.8 * d, 1.8 * d, 0.8 * d,
                  fc=c, ec='none', zorder=5)
        if hook:
            # 180°フック: 下端で半円に折り返し、余長 4d を上向きに延ばす。
            # 折返しは柱形の内側へ (曲げ内法半径 2d を目安に描く)
            r = 2.0 * d
            sgn = -1 if x > 0 else 1
            cx = x + sgn * r
            ax.add_patch(Arc((cx, z_end), 2 * r, 2 * r, theta1=180,
                             theta2=360, color=c, lw=max(1.2, d / 12),
                             zorder=4))
            ax.plot([cx + sgn * r, cx + sgn * r], [z_end, z_end + 4 * d],
                    color=c, lw=max(1.2, d / 12), solid_capstyle='butt',
                    zorder=4)
        else:
            w = Dp if Dp else 3 * d
            tp = max(0.25 * d, 6.0)
            _rect(ax, x - w / 2, z_end - tp, w, tp, fc=c, ec='none',
                  zorder=5)
            _rect(ax, x - 0.9 * d, z_end - tp - 0.8 * d, 1.8 * d, 0.8 * d,
                  fc=c, ec='none', zorder=5)
    # コーン破壊面 (引張側、45°)
    xt = xs[-1][0]
    w = 0.0 if hook else (Dp if Dp else 3 * d) / 2
    for sgn in (-1, 1):
        x0 = xt + sgn * w
        ax.plot([x0, x0 + sgn * la], [-la, 0], color=CONE, lw=0.5,
                ls=(0, (4, 3)), zorder=1)
    # 寸法
    right = max(fD / 2, D / 2)
    step = 0.09 * max(fD, la)
    _dim(ax, (right, 0), (right, -la), step, 'la %.0f' % la)
    _dim(ax, (right, top), (right, -la), 2.2 * step, 'lb %.0f' % lb)
    if fh and fh < la + 50:
        _dim(ax, (-fD / 2, -fh), (-fD / 2, 0), step, '立上り %.0f' % fh)
    if not hook and Dp:
        _dim(ax, (xt - Dp / 2, -la - 10), (xt + Dp / 2, -la - 10),
             -step * 0.9, 'Dp %.0f' % Dp)
    ax.annotate('BP厚 %.0f' % t, xy=(-D / 2, mort + t / 2),
                xytext=(-D / 2 - step * 1.6, top + hc * 0.5),
                fontsize=FS, color=NAVY, ha='center',
                arrowprops=dict(arrowstyle='-', color=NAVY, lw=0.5))
    if mort > 0:
        ax.text(0, mort / 2, 'モルタル %.0f' % mort, ha='center',
                va='center', fontsize=FS - 1, color=SUB, zorder=4)
    ax.set_aspect('equal')
    ax.axis('off')
    xl = max(fD * 0.75, right + 3.2 * step)
    ax.set_xlim(-xl, xl)
    ax.set_ylim(bottom - step * 1.2, top + hc + step * 0.3)
    kind = {'each': '定着金物 (個別 Dp角)',
            'plate': '定着金物 (列を連結 幅Dp)',
            'hook': 'フック'}[sp['anc_type']]
    ax._cb_title = '断面図 (H方向)  定着: %s' % kind
    if lb < la + t:
        ax.text(-xl, bottom - step, '注: lb が la+BP厚 より小さいため、'
                'BP 下面を柱形上面として描いています', fontsize=FS - 0.5,
                color=ORANGE)


def draw_spec(sp, col, out_dir, stem):
    """1グループの仕様図を PNG と PDF で書き出し、(png, pdf) を返す."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _font()
    fig, (a1, a2) = plt.subplots(
        1, 2, figsize=(170 / 25.4, 88 / 25.4),
        gridspec_kw={'width_ratios': [1, 1], 'wspace': 0.05})
    fig.subplots_adjust(left=0.01, right=0.99, top=0.86, bottom=0.03)
    _plan(a1, sp, col)
    _section(a2, sp, col)
    # 2図の表題は同じ高さにそろえる (aspect=equal で軸の高さが違うため)
    for ax in (a1, a2):
        x0 = ax.get_position().x0
        fig.text(x0 + 0.01, 0.9, ax._cb_title, fontsize=FS + 1,
                 ha='left', va='bottom')
    fig.suptitle('%s  (柱 %s)' % (sp['group'], col['name']),
                 fontsize=FS + 2, x=0.01, y=0.985, ha='left',
                 fontweight='bold')
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, stem + '.png')
    fig.savefig(png, dpi=170)
    plt.close(fig)
    return png


def group_reps(mgt_path, specs, notes=None):
    """選んだグループの (正規化した仕様, 代表柱) を specs の順に返す.

    代表柱は断面積が最大の柱 (告示1456号の判定と同じ)。検討書の TikZ 図と
    画面の仕様図 (PNG) の両方で使う。
    """
    from mgtkit.colbase.calc import read_model, normalize_spec, col_props
    notes = notes if notes is not None else []
    m = read_model(mgt_path, notes)
    gmap = {g['name']: g for g in m['groups']}
    out = []
    for i, raw in enumerate(specs or [], 1):
        sp = normalize_spec(raw)
        g = gmap.get(sp['group'])
        if g is None:
            continue
        cols = [m['bases'][e] for e in g['eles']
                if m['bases'][e]['shape'] in ('H', 'BOX', 'P')]
        if not cols:
            notes.append('グループ %s は対応断面の柱が無いため仕様図を'
                         '描けません。' % sp['group'])
            continue
        rep = max(cols, key=lambda c: col_props(c, 'H')['A'])
        out.append({'group': sp['group'], 'col': rep['name'],
                    'spec': sp, 'colobj': rep})
    return out


def draw_groups(mgt_path, specs, out_dir, notes=None, prefix='spec'):
    """画面表示用の仕様図 (PNG) をまとめて描く."""
    out = []
    for i, f in enumerate(group_reps(mgt_path, specs, notes), 1):
        f = dict(f)
        f['png'] = draw_spec(f['spec'], f['colobj'], out_dir,
                             '%s_%d' % (prefix, i))
        out.append(f)
    return out
