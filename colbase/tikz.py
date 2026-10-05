# -*- coding: utf-8 -*-
"""柱脚仕様図の TikZ 版 (検討書に直接書き込む簡易図).

figure.py (matplotlib、画面表示と PDF 取込み用) と同じ内容を、計算書側で
描ける TikZ コードとして出力する。計算書のプリアンブルに
\\usepackage{tikz} が必要 (dvipdfmx で動作)。

座標は実寸 [mm] に縮尺 k を掛けた紙面上の mm。平面図・断面図はそれぞれ
幅に収まる縮尺を選び、図の下に縮尺 (1/xx) を書く。
"""

import math

from mgtkit.colbase.calc import bolt_positions, AB_TYPES

#: 色定義 (説明図・figure.py と同じ配色)
COLORS = [r'\definecolor{cbnavy}{RGB}{43,74,111}',
          r'\definecolor{cborange}{RGB}{185,93,24}',
          r'\definecolor{cborangef}{RGB}{246,220,198}',
          r'\definecolor{cbconc}{RGB}{238,241,245}',
          r'\definecolor{cbbeam}{RGB}{232,237,244}',
          r'\definecolor{cbconce}{RGB}{138,151,168}',
          r'\definecolor{cbbp}{RGB}{215,224,234}',
          r'\definecolor{cbcol}{RGB}{143,163,186}',
          r'\definecolor{cbcone}{RGB}{112,125,144}']


def _f(v):
    return '%.2f' % v


class _Pic:
    """紙面 mm 座標で描くための小さなヘルパ (実寸×k)."""

    def __init__(self, k):
        self.k = k
        self.L = []

    def p(self, x, y):
        return '(%s,%s)' % (_f(x * self.k), _f(y * self.k))

    def rect(self, x0, y0, w, h, style):
        self.L.append(r'\path[%s] %s rectangle %s;'
                      % (style, self.p(x0, y0), self.p(x0 + w, y0 + h)))

    def line(self, pts, style):
        self.L.append(r'\draw[%s] %s;'
                      % (style, ' -- '.join(self.p(*q) for q in pts)))

    def circle(self, x, y, r, style, min_mm=0.0):
        rr = max(r * self.k, min_mm)
        self.L.append(r'\path[%s] %s circle (%smm);'
                      % (style, self.p(x, y), _f(rr)))

    def text(self, x, y, s, opt=''):
        self.L.append(r'\node[%s] at %s {%s};' % (opt, self.p(x, y), s))

    def dim(self, p1, p2, off, text):
        """寸法線 (p1→p2 の左法線方向に off ずらす。off は紙面 mm)."""
        x1, y1 = p1[0] * self.k, p1[1] * self.k
        x2, y2 = p2[0] * self.k, p2[1] * self.k
        L = math.hypot(x2 - x1, y2 - y1)
        if L <= 0:
            return
        ux, uy = (x2 - x1) / L, (y2 - y1) / L
        nx, ny = -uy, ux
        s = 1.0 if off >= 0 else -1.0
        a = (x1 + nx * off, y1 + ny * off)
        b = (x2 + nx * off, y2 + ny * off)
        g = 0.6
        for (px, py), (qx, qy) in (((x1, y1), a), ((x2, y2), b)):
            self.L.append(r'\draw[cbnavy,line width=0.2pt] (%s,%s) -- (%s,%s);'
                          % (_f(px + nx * s * g), _f(py + ny * s * g),
                             _f(qx + nx * s * 0.8), _f(qy + ny * s * 0.8)))
        self.L.append(r'\draw[cbnavy,line width=0.3pt] (%s,%s) -- (%s,%s);'
                      % (_f(a[0]), _f(a[1]), _f(b[0]), _f(b[1])))
        t = 0.7
        for qx, qy in (a, b):
            self.L.append(r'\draw[cbnavy,line width=0.3pt] (%s,%s) -- (%s,%s);'
                          % (_f(qx - (ux + nx) * t), _f(qy - (uy + ny) * t),
                             _f(qx + (ux + nx) * t), _f(qy + (uy + ny) * t)))
        ang = math.degrees(math.atan2(uy, ux))
        if ang > 90.1 or ang < -89.9:
            ang -= 180
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        d = 1.6 * s
        self.L.append(r'\node[cbnavy,font=\tiny,fill=white,inner sep=0.3pt,'
                      r'rotate=%s] at (%s,%s) {%s};'
                      % (_f(ang), _f(mx + nx * d), _f(my + ny * d), text))


def _scale_label(k):
    """紙面 mm / 実寸 mm → 1/xx 表記 (切りのよい値に丸めない実値)."""
    return '1/%d' % round(1.0 / k)


def _plan(sp, col):
    D, B = sp['bp_D'], sp['bp_B']
    fD, fB = sp.get('fnd_D'), sp.get('fnd_B')
    ext = max(fD or D, fB or B)
    k = 52.0 / ext
    P = _Pic(k)
    if fD and fB:
        P.rect(-fD / 2, -fB / 2, fD, fB,
               'fill=cbconc,draw=cbconce,line width=0.4pt')
    P.rect(-D / 2, -B / 2, D, B, 'fill=cbbp,draw=cbnavy,line width=0.6pt')
    s = col['shape']
    H, Bc, tw, tf = col['H'], col['B'], col['tw'], col['tf']
    cs = 'fill=cbcol,draw=cbnavy,line width=0.3pt'
    if s == 'H':
        P.rect(-H / 2, -Bc / 2, tf, Bc, cs)
        P.rect(H / 2 - tf, -Bc / 2, tf, Bc, cs)
        P.rect(-H / 2 + tf, -tw / 2, H - 2 * tf, tw, cs)
    elif s == 'BOX':
        P.rect(-H / 2, -Bc / 2, H, Bc, cs)
        P.rect(-H / 2 + tf, -Bc / 2 + tw, H - 2 * tf, Bc - 2 * tw,
               'fill=cbbp,draw=cbnavy,line width=0.2pt')
    elif s == 'P':
        P.circle(0, 0, H / 2, cs)
        P.circle(0, 0, H / 2 - tw, 'fill=cbbp,draw=cbnavy,line width=0.2pt')
    d = sp['ab_d']
    pts = bolt_positions(sp)
    xt = max(q[0] for q in pts)
    yt = max(q[1] for q in pts)
    tens = set()
    pad = max(d, 12.0)
    if sp['nt_H'] > 0:
        row = [q for q in pts if abs(q[0] - xt) < 1e-6]
        tens.update(row)
        ys = [q[1] for q in row]
        P.L.append(r'\draw[cborange,line width=0.5pt,rounded corners=1pt] '
                   r'%s rectangle %s;' % (P.p(xt - pad, min(ys) - pad),
                                          P.p(xt + pad, max(ys) + pad)))
    if sp['nt_B'] > 0:
        row = [q for q in pts if abs(q[1] - yt) < 1e-6]
        tens.update(row)
        xs = [q[0] for q in row]
        P.L.append(r'\draw[cborange,line width=0.5pt,rounded corners=1pt] '
                   r'%s rectangle %s;' % (P.p(min(xs) - pad, yt - pad),
                                          P.p(max(xs) + pad, yt + pad)))
    for q in pts:
        t = q in tens
        P.circle(q[0], q[1], d / 2,
                 'fill=cborangef,draw=cborange,line width=0.3pt' if t
                 else 'fill=white,draw=black,line width=0.3pt', min_mm=0.45)
        if sp.get('hole'):
            P.circle(q[0], q[1], sp['hole'] / 2,
                     'draw=cbnavy,dashed,line width=0.2pt', min_mm=0.6)
    # 寸法 (紙面 mm でずらす)
    ob = ((fB or B) - B) / 2 * k
    od = ((fD or D) - D) / 2 * k
    P.dim((-D / 2, -B / 2), (D / 2, -B / 2), -(ob + 3.5), 'BP せい %.0f' % D)
    P.dim((-D / 2, B / 2), (-D / 2, -B / 2), -(od + 3.5), 'BP 幅 %.0f' % B)
    if fD and fB:
        P.dim((-fD / 2, -fB / 2), (fD / 2, -fB / 2), -7.5,
              '柱形せい %.0f' % fD)
        P.dim((-fD / 2, fB / 2), (-fD / 2, -fB / 2), -7.5,
              '柱形幅 %.0f' % fB)
    if sp['nt_H'] > 0:
        P.dim((xt, B / 2), (D / 2, B / 2), ob + 3.0, '%.0f' % sp['dtl_H'])
    if sp['nt_B'] > 0:
        P.dim((D / 2, yt), (D / 2, B / 2), -(od + 3.0), '%.0f' % sp['dtl_B'])
    return P, k


def _section(sp, col):
    D = sp['bp_D']
    t = sp['bp_t']
    la = sp.get('la') or 20 * sp['ab_d']
    lb = sp['lb']
    d = sp['ab_d']
    top = max(lb - la, t)
    mort = top - t
    fD = sp.get('fnd_D') or (D + 200.0)
    fh = sp.get('fnd_h')
    hc = max(0.45 * D, 150.0)
    bottom = -(la + max(150.0, 0.25 * la))
    beam = bool(fh and fh < la + 50)
    wb = fD * 1.5 if beam else fD
    # 紙面の幅 (約 90mm) から寸法線・文字の分 (左右 約 26mm) を除いて収める
    k = min(64.0 / wb, 58.0 / (top + hc - bottom))
    P = _Pic(k)
    if beam:
        P.rect(-wb / 2, bottom, wb, -fh - bottom, 'fill=cbbeam')
        P.rect(-fD / 2, -fh, fD, fh, 'fill=cbconc')
        P.line([(-wb / 2, -fh), (-fD / 2, -fh), (-fD / 2, 0), (fD / 2, 0),
                (fD / 2, -fh), (wb / 2, -fh)], 'cbconce,line width=0.4pt')
    else:
        P.rect(-fD / 2, bottom, fD, -bottom, 'fill=cbconc')
        P.line([(-fD / 2, bottom), (-fD / 2, 0), (fD / 2, 0),
                (fD / 2, bottom)], 'cbconce,line width=0.4pt')
    if mort > 0:
        P.rect(-D / 2 + 10, 0, D - 20, mort,
               'fill=white,draw=cbcone,line width=0.2pt')
    P.rect(-D / 2, mort, D, t, 'fill=cbbp,draw=cbnavy,line width=0.5pt')
    H = col['H']
    tc = col['tf'] if col['shape'] in ('H', 'BOX') else col['tw']
    P.rect(-H / 2, top, H, hc, 'fill=cbbp,draw=cbnavy,line width=0.4pt')
    P.rect(-H / 2, top, tc, hc, 'fill=cbcol,draw=cbnavy,line width=0.2pt')
    P.rect(H / 2 - tc, top, tc, hc, 'fill=cbcol,draw=cbnavy,line width=0.2pt')
    P.text(0, top + hc * 0.55, '柱', 'font=\\tiny')
    hook = sp['anc_type'] == 'hook'
    Dp = sp.get('anc_Dp') or 0.0
    if sp['nt_H'] > 0:
        xr = D / 2 - sp['dtl_H']
        xs = [(-xr, False), (xr, True)]
    else:
        xs = [(0.0, True)]
    lw = max(0.8, min(1.6, d * k * 1.0))
    for x, tens in xs:
        c = 'cborange' if tens else 'black'
        P.line([(x, top + 1.6 * d), (x, -la)],
               '%s,line width=%spt' % (c, _f(lw)))
        P.rect(x - 0.9 * d, top, 1.8 * d, 1.6 * d, 'fill=%s' % c)
        if hook:
            r = 2.0 * d
            sgn = -1 if x > 0 else 1
            # 180°フック: 下端で半円に折り返して余長 4d を上へ
            # 左のボルトは右へ (180→360°)、右のボルトは左へ (0→−180°)
            ang = '180:360' if sgn > 0 else '0:-180'
            P.L.append(r'\draw[%s,line width=%spt] %s arc (%s:%smm) '
                       r'-- ++(0,%s);'
                       % (c, _f(lw), P.p(x, -la), ang, _f(r * k),
                          _f(4 * d * k)))
        else:
            w = Dp if Dp else 3 * d
            tp = max(0.25 * d, 6.0)
            P.rect(x - w / 2, -la - tp, w, tp, 'fill=%s' % c)
    xt = xs[-1][0]
    w = 0.0 if hook else (Dp if Dp else 3 * d) / 2
    # コーン破壊面 (45°)。コンクリートの外 (図の左右端) に出る分は描かない
    for sgn in (-1, 1):
        x0 = xt + sgn * w
        run = max(0.0, min(la, wb / 2 - sgn * x0))
        P.line([(x0, -la), (x0 + sgn * run, -la + run)],
               'cbcone,dashed,line width=0.3pt')
    right = wb / 2
    P.dim((right, 0), (right, -la), 3.5, 'la %.0f' % la)
    P.dim((right, top), (right, -la), 8.0, 'lb %.0f' % lb)
    if fh and fh < la + 50:
        P.dim((-fD / 2, -fh), (-fD / 2, 0), 3.5, '立上り %.0f' % fh)
    if not hook and Dp:
        P.dim((xt - Dp / 2, -la - 10), (xt + Dp / 2, -la - 10), -3.0,
              'Dp %.0f' % Dp)
    P.L.append(r'\draw[cbnavy,line width=0.2pt] %s -- ++(-5,4) node[above,'
               r'font=\tiny,cbnavy]{BP厚 %.0f};' % (P.p(-D / 2, mort + t / 2),
                                                   t))
    if mort > 0:
        P.text(0, mort / 2, r'モルタル %.0f' % mort,
               r'font=\tiny,text=cbcone,inner sep=0pt')
    return P, k


def spec_tikz(sp, col):
    """1グループの仕様図 (平面図・断面図を横に並べる) の TeX 行 list."""
    kind = {'each': '定着金物 (個別 $D_p$角)',
            'plate': '定着金物 (列を連結 幅$D_p$)',
            'hook': '180°フック'}[sp['anc_type']]
    ab = AB_TYPES.get(sp['ab_type'], (0, 0, sp['ab_type']))[2]
    pl, k1 = _plan(sp, col)
    se, k2 = _section(sp, col)
    name = str(col['name']).replace('_', r'\_')
    grp = str(sp['group']).replace('_', r'\_')
    # 表題と図が別ページに分かれないよう1グループを minipage にまとめる
    L = [r'\par\noindent\begin{minipage}{\linewidth}\centering',
         r'\textbf{%s} (柱 %s)\par\smallskip' % (grp, name),
         r'\begin{minipage}[t]{0.47\linewidth}\centering',
         r'{\footnotesize 平面図 (横: H方向、縦: B方向)}\par\smallskip',
         r'\begin{tikzpicture}[x=1mm,y=1mm]']
    L += pl.L
    L += [r'\end{tikzpicture}\par',
          r'{\tiny %d-M%.0f %s\quad 孔径 %s\quad 引張側 H%d本・B%d本\quad '
          r'縮尺 %s}'
          % (sp['n_all'], sp['ab_d'], ab.split(' (')[0],
             '%.0f' % sp['hole'] if sp.get('hole') else '--',
             sp['nt_H'], sp['nt_B'], _scale_label(k1)),
          r'\end{minipage}\hfill',
          r'\begin{minipage}[t]{0.5\linewidth}\centering',
          r'{\footnotesize 断面図 (H方向) 定着: %s}\par\smallskip' % kind,
          r'\begin{tikzpicture}[x=1mm,y=1mm]']
    L += se.L
    L += [r'\end{tikzpicture}\par',
          r'{\tiny 縮尺 %s}' % _scale_label(k2),
          r'\end{minipage}',
          r'\end{minipage}\par\bigskip']
    return L
