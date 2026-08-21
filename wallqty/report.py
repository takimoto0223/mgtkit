# -*- coding: utf-8 -*-
"""木造壁量計算の検討書 (A4縦 PDF) を組む.

draw_model.py / loadmap の作法に合わせてある (matplotlib / Agg / PdfPages /
日本語フォント / 紙面 mm をそのままデータ座標にする)。

  1. 表紙・設計条件・階の定義
  2. 存在壁量 と 壁量検定表
  3. 4分割法
  4. 各階の耐力壁配置図 (1ページ1階)
  5. 耐力壁の一覧 (行数に応じてページ送り)
"""

import datetime
import math
import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from mgtkit.draw_model import _setup_japanese_font

MM = 25.4                      # 1 inch [mm]
A4 = (210.0, 297.0)            # 用紙 [mm]
ML, MR, MT, MB = 15.0, 15.0, 18.0, 15.0     # 余白 [mm]

FS_TITLE = 13
FS_HEAD = 9.5
FS_BODY = 7.6
FS_SMALL = 6.6

C_LINE = '#7a8493'
C_HEAD = '#e8edf4'
C_NG = '#f6c8c4'
C_OFF = '#f2f3f5'
C_X = '#2b6cb0'
C_Y = '#2f855a'
C_D = '#b45309'
C_GRAY = '#b3b8c2'


# ---------------------------------------------------------------------------
# 紙面のヘルパ (単位は mm。左下原点ではなく「上からの送り」で書く)
# ---------------------------------------------------------------------------

class Page(object):
    """A4 1ページ。y は上端からの送り [mm] で管理する."""

    def __init__(self, pdf, title=None):
        """表題を出すのは1ページ目だけ (title を与えたページのみ)."""
        self.pdf = pdf
        self.fig = plt.figure(figsize=(A4[0] / MM, A4[1] / MM))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, A4[0])
        self.ax.set_ylim(0, A4[1])
        self.ax.invert_yaxis()
        self.ax.axis('off')
        self.y = MT
        if title:
            y0 = self.y
            self.ax.text(ML, y0, title, fontsize=FS_TITLE, ha='left',
                         va='top', fontweight='bold', color='#222')
            self.y = y0 + FS_TITLE * 0.52 + 1.5
            self.line()
            self.y += 3.0

    # -- 基本 ---------------------------------------------------------------
    def text(self, x, s, size=FS_BODY, ha='left', weight=None, color='#222',
             dy=None):
        self.ax.text(x, self.y, s, fontsize=size, ha=ha, va='top',
                     color=color, fontweight=(weight or 'normal'))
        if dy is None:
            self.y += size * 0.52
        else:
            self.y += dy

    def line(self, color=C_LINE, lw=0.8):
        self.ax.plot([ML, A4[0] - MR], [self.y, self.y], color=color, lw=lw)

    def heading(self, s):
        self.y += 2.0
        self.text(ML, s, FS_HEAD, weight='bold', color='#2b4a6f')
        self.y += 0.5
        self.line(color='#2b4a6f', lw=1.0)
        self.y += 2.2

    def note(self, s):
        for ln in _wrap(s, 104):
            self.text(ML, ln, FS_SMALL, color='#556')

    def space(self, mm):
        self.y += mm

    def room(self):
        return A4[1] - MB - self.y

    def close(self):
        self.pdf.savefig(self.fig)
        plt.close(self.fig)


def _wrap(s, n):
    """日本語まじりの雑な折り返し (全角は2文字ぶんとして数える)."""
    out, cur, w = [], '', 0
    for ch in str(s):
        cw = 1 if ord(ch) < 0x100 else 2
        if ch == '\n' or (w + cw > n and ch not in '。、）」)'):
            out.append(cur)
            cur, w = '', 0
            if ch == '\n':
                continue
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out or ['']


def table(pg, cols, rows, widths, align=None, rowcol=None, head_lines=1,
          size=FS_BODY):
    """表を1つ描く。cols=見出し, rows=[[文字列,...]], widths=各列の幅[mm].

    align: 各列の 'l'/'c'/'r' (既定 'c')。rowcol: 行ごとの背景色 or None。
    """
    align = align or ['c'] * len(cols)
    x0 = ML
    total = sum(widths)
    if total > A4[0] - ML - MR:                      # はみ出したら縮める
        f = (A4[0] - ML - MR) / total
        widths = [w * f for w in widths]
    hh = size * 0.42 * head_lines + 2.2
    rh = size * 0.52 + 1.6

    def cell(x, w, y, h, s, a, size, bold=False, color='#222'):
        if a == 'l':
            tx, ha = x + 1.2, 'left'
        elif a == 'r':
            tx, ha = x + w - 1.2, 'right'
        else:
            tx, ha = x + w / 2.0, 'center'
        pg.ax.text(tx, y + h / 2.0, s, fontsize=size, ha=ha, va='center',
                   color=color, fontweight=('bold' if bold else 'normal'),
                   linespacing=1.05)

    # セルからはみ出す文字は2段に折り返す
    rows = [[_fit(v, w - 2.6, size) for v, w in zip(r, widths)] for r in rows]
    nline = [max(s.count('\n') + 1 for s in r) for r in rows] or [1]

    # 見出し
    x = x0
    for c, w in zip(cols, widths):
        pg.ax.add_patch(plt.Rectangle((x, pg.y), w, hh, facecolor=C_HEAD,
                                      edgecolor=C_LINE, lw=0.5))
        cell(x, w, pg.y, hh, c, 'c', size, bold=True)
        x += w
    pg.y += hh
    # 本文
    for i, r in enumerate(rows):
        bg = (rowcol[i] if rowcol else None)
        h = rh + (nline[i] - 1) * size * 0.40
        x = x0
        for j, (v, w) in enumerate(zip(r, widths)):
            pg.ax.add_patch(plt.Rectangle((x, pg.y), w, h,
                                          facecolor=(bg or 'white'),
                                          edgecolor=C_LINE, lw=0.4))
            cell(x, w, pg.y, h, v, align[j], size)
            x += w
        pg.y += h
    pg.y += 2.0


def _tw(s, size):
    """文字列の幅 [mm] の見積り (全角は半角2つぶん)."""
    n = sum(1 if ord(c) < 0x100 else 2 for c in str(s))
    return n * size * 0.353 * 0.5


def _fit(s, w_mm, size):
    """セル幅に収まらない文字列を2段に折り返す (区切りの良い所で切る)."""
    s = str(s)
    if _tw(s, size) <= w_mm or len(s) < 2:
        return s
    for sep in (' / ', ': ', ' + ', '、', ' '):
        i = s.rfind(sep, 0, int(len(s) * 0.75) + len(sep))
        if i > 0:
            a, b = s[:i], s[i + len(sep):]
            if _tw(a, size) <= w_mm:
                return a + '\n' + b.lstrip()
    half = len(s) // 2
    return s[:half] + '\n' + s[half:]


def _f(v, n=3):
    return '' if v is None else ('%.*f' % (n, v))


#: Tc [s] -> 地盤種別の呼び名 (令88条)
SOIL = {0.4: '第一種地盤', 0.6: '第二種地盤', 0.8: '第三種地盤'}


def _soil(tc):
    """地盤種別を「0.6 (第二種地盤)」の形で書く."""
    name = SOIL.get(round(float(tc), 3))
    return ('%g (%s)' % (tc, name)) if name else '%g' % tc


# ---------------------------------------------------------------------------
# 平面図 (画面の SVG と同じ描き方を紙面 mm で)
# ---------------------------------------------------------------------------

def plan(pg, res, st, h_mm):
    """1階ぶんの耐力壁配置図を、いま位置から高さ h_mm の枠に描く.

    紙面は縦長、建物は横長ということがあるので、そのままの向きと 90°回転の
    両方で縮尺を計算し、**大きく描ける方の向き**を選ぶ。回転したときは方位
    記号と注記でそれが分かるようにする (回転は反時計回り: モデルの +X が
    紙面の上、+Y が紙面の左を向く)。
    """
    walls = [w for w in res['walls'] if w['story'] == st['no']]
    m = res['model']
    dx = max(m['x1'] - m['x0'], 0.001)
    dy = max(m['y1'] - m['y0'], 0.001)
    pad = max(dx, dy) * 0.10 + 0.4
    bx0, bx1 = m['x0'] - pad, m['x1'] + pad
    by0, by1 = m['y0'] - pad, m['y1'] + pad
    W, H = bx1 - bx0, by1 - by0
    fw, fh = A4[0] - ML - MR - 16.0, h_mm - 12.0
    s0 = min(fw / W, fh / H)                 # そのまま
    s1 = min(fw / H, fh / W)                 # 90°回転
    rot = s1 > s0 * 1.15                     # はっきり有利なときだけ回す
    s = s1 if rot else s0
    dw, dh = ((H, W) if rot else (W, H))
    ox = ML + 14.0 + (fw - dw * s) / 2.0
    oy = pg.y + 5.0 + (fh - dh * s) / 2.0

    if rot:
        def P(x, y):
            return (ox + (by1 - y) * s, oy + (bx1 - x) * s)
    else:
        def P(x, y):
            return (ox + (x - bx0) * s, oy + (by1 - y) * s)

    def rect(x0, y0, x1, y1, **kw):
        a = P(x0, y0)
        b = P(x1, y1)
        pg.ax.add_patch(plt.Rectangle(
            (min(a[0], b[0]), min(a[1], b[1])), abs(b[0] - a[0]),
            abs(b[1] - a[1]), **kw))

    def seg(x0, y0, x1, y1, **kw):
        a = P(x0, y0)
        b = P(x1, y1)
        pg.ax.plot([a[0], b[0]], [a[1], b[1]], **kw)

    bb = st.get('bbox')
    if bb:
        qx, qy = (bb[1] - bb[0]) / 4.0, (bb[3] - bb[2]) / 4.0
        for (x0, y0, x1, y1, col) in (
                (bb[0], bb[2], bb[1], bb[2] + qy, C_X),
                (bb[0], bb[3] - qy, bb[1], bb[3], C_X),
                (bb[0], bb[2], bb[0] + qx, bb[3], C_Y),
                (bb[1] - qx, bb[2], bb[1], bb[3], C_Y)):
            rect(x0, y0, x1, y1, facecolor=col, alpha=0.08, edgecolor='none')
        cx, cy = (bb[0] + bb[1]) / 2.0, (bb[2] + bb[3]) / 2.0
        # X方向の帯は紙面で横長 (回転時は縦長) になるので、字の向きも合わせる
        for (x, y, t, col, along_x) in (
                (cx, bb[2] + qy / 2, 'X方向 側端①', C_X, True),
                (cx, bb[3] - qy / 2, 'X方向 側端②', C_X, True),
                (bb[0] + qx / 2, cy, 'Y方向 側端①', C_Y, False),
                (bb[1] - qx / 2, cy, 'Y方向 側端②', C_Y, False)):
            sx, sy = P(x, y)
            horiz = (along_x != rot)         # 紙面で横書きにするか
            pg.ax.text(sx, sy, t, fontsize=FS_SMALL, ha='center',
                       va='center', color=col,
                       rotation=(0 if horiz else 90))
        for v in (bb[0] + qx, bb[1] - qx):
            seg(v, bb[2], v, bb[3], color='#d98b3a', lw=0.5,
                dashes=(6, 2, 1, 2))
        for v in (bb[2] + qy, bb[3] - qy):
            seg(bb[0], v, bb[1], v, color='#d98b3a', lw=0.5,
                dashes=(6, 2, 1, 2))

    # 通り芯 (符号は紙面の下端・左端に置く)
    ax_ = res.get('axes') or {'x': [], 'y': []}
    for a in ax_.get('x', []):
        if not (bx0 <= a['v'] <= bx1):
            continue
        seg(a['v'], by0, a['v'], by1, color='#e3e7ee', lw=0.4)
        sx, sy = P(a['v'], by0 if not rot else by0)
        if rot:
            pg.ax.text(P(a['v'], by0)[0] - 1.5, P(a['v'], by0)[1], a['name'],
                       fontsize=FS_SMALL, ha='right', va='center',
                       color='#556')
        else:
            pg.ax.text(sx, sy + 2.6, a['name'], fontsize=FS_SMALL,
                       ha='center', va='top', color='#556')
    for a in ax_.get('y', []):
        if not (by0 <= a['v'] <= by1):
            continue
        seg(bx0, a['v'], bx1, a['v'], color='#e3e7ee', lw=0.4)
        if rot:
            px_, py_ = P(bx0, a['v'])
            pg.ax.text(px_, py_ + 2.6, a['name'], fontsize=FS_SMALL,
                       ha='center', va='top', color='#556')
        else:
            px_, py_ = P(bx0, a['v'])
            pg.ax.text(px_ - 1.5, py_, a['name'], fontsize=FS_SMALL,
                       ha='right', va='center', color='#556')

    # 外形と柱
    rect(m['x0'], m['y0'], m['x1'], m['y1'], fill=False, edgecolor='#ccd',
         lw=0.4, linestyle=(0, (3, 2)))
    for c in (st.get('cols') or []):
        sx, sy = P(c[0], c[1])
        pg.ax.add_patch(plt.Rectangle((sx - 0.7, sy - 0.7), 1.4, 1.4,
                                      facecolor='#5b6673',
                                      edgecolor='#39424e', lw=0.2))
    # 壁
    for w in walls:
        col = (C_GRAY if not w['used']
               else (C_X if w['dir'] == 'X'
                     else (C_Y if w['dir'] == 'Y' else C_D)))
        kw = {}
        if not w['used']:
            kw['dashes'] = (3, 2)
        elif w['sloped']:
            kw['dashes'] = (6, 2)
        seg(w['x1'], w['y1'], w['x2'], w['y2'], color=col, lw=1.6,
            solid_capstyle='butt', **kw)
        a = P(w['x1'], w['y1'])
        b = P(w['x2'], w['y2'])
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        ddx, ddy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(ddx, ddy) or 1.0
        pg.ax.text(mx + (-ddy / ln) * 2.6, my + (ddx / ln) * 2.6, w['name'],
                   fontsize=FS_SMALL - 0.6, ha='center', va='center',
                   color='#223',
                   bbox=dict(boxstyle='square,pad=0.18', facecolor='white',
                             edgecolor=col, linewidth=0.4))

    # 方位 (回転しても向きが分かるように必ず描く)
    axy = oy + dh * s - 2.0
    axx = (ML + 11.0) if rot else (ML + 2.0)
    arw = dict(arrowstyle='->', color='#889', lw=0.7)
    pg.ax.annotate('', xy=(axx, axy - 8), xytext=(axx, axy), arrowprops=arw)
    if rot:                       # 反時計回り90°なので X が上、Y が左
        pg.ax.annotate('', xy=(axx - 8, axy), xytext=(axx, axy),
                       arrowprops=arw)
        pg.ax.text(axx + 0.6, axy - 9.0, 'X', fontsize=FS_SMALL, color='#889')
        pg.ax.text(axx - 9.6, axy + 0.6, 'Y', fontsize=FS_SMALL, color='#889',
                   va='top')
    else:
        pg.ax.annotate('', xy=(axx + 8, axy), xytext=(axx, axy),
                       arrowprops=arw)
        pg.ax.text(axx + 8.4, axy + 0.6, 'X', fontsize=FS_SMALL, color='#889',
                   va='top')
        pg.ax.text(axx + 0.6, axy - 9.0, 'Y', fontsize=FS_SMALL, color='#889')
    pg.y += h_mm
    return rot




# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------

def export_report(res, chk, out_path, mgt_path='', project=''):
    """検討書 PDF を書き出す (戻り値: パス)."""
    _setup_japanese_font()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    p = chk['params']
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    name = project or os.path.splitext(os.path.basename(mgt_path or ''))[0]

    with PdfPages(out_path) as pdf:
        # ---- 1ページ目: 条件 -------------------------------------------
        pg = Page(pdf, '木造壁量計算 検討書')

        pg.heading('1. 設計条件')
        table(pg, ['項目', '値', '項目', '値'],
              [['重要度係数 I', '%.2f' % p.get('imp', 1.0),
                '地域係数 Z', '%.1f' % p['z']],
               ['地盤種別 Tc [s]', _soil(p['tc']),
                '標準せん断力係数 C₀', '%g' % p['c0']],
               ['建築物の高さ h [m]', '%.3f' % p['h_bldg'],
                '架構係数 α', '%.1f' % p.get('alpha', 1.0)],
               ['一次固有周期 T [s]', '%.4f' % p['T'],
                '振動特性係数 Rt', '%.4f' % p['Rt']],
               ['風圧力の係数 [cm/m2]', '%g' % p['wind_k'], '', '']],
              [46, 26, 46, 26], ['l', 'r', 'l', 'r'])
        pg.note('一次固有周期 T = h × (0.02 + 0.01α)。'
                'α は木造・鉄骨造の階の高さの合計を h で割った値 '
                '(木造のみなら α = 1)。')
        pg.note('地震力 Ci = I・Z・Rt・Ai・C₀、Qi = Ci・ΣWi、'
                '必要壁量 = Qi / %g [kN/m]。重要度係数は地震力にのみ乗じる。'
                % p['qa'])
        pg.note('風圧力の必要壁量 = 見付面積 × %g [cm/m2] / 100。'
                '地震力・風圧力の大きい方で判定する。' % p['wind_k'])

        pg.heading('2. 算入条件')
        rules = [
            ['壁とみなす勾配', '%g° 以上 (これ未満は床・屋根として対象外)'
             % res['slope_min']],
            ['壁長', '水平投影長さ。開口で分かれた区間は別の壁として扱う'],
            ['最小の壁長', ('%g mm 未満は算入しない' % (res['min_len'] * 1000))
             if res['min_len'] else '判定しない'],
            ['階高/壁長', ('%g を超えるものは算入しない (階高は壁自身の高さ H)'
                           % res['max_aspect'])
             if res['max_aspect'] else '判定しない'],
        ]
        table(pg, ['項目', '内容'], rules, [34, 110], ['l', 'l'])

        pg.heading('3. 階の定義と入力')
        rows = []
        for r in chk['rows']:
            rows.append([r['name'],
                         ' / '.join('%.3f' % z for z in r['levels']),
                         r['h_disp'], '%d' % r['nwall'], '%.1f' % r['w'],
                         '%.2f' % r['x']['af'], '%.2f' % r['y']['af']])
        table(pg, ['階', '床レベル [m]', '壁の高さ [m]', '壁面数',
                   '層重量 Wi [kN]', '見付面積 X [m2]', '見付面積 Y [m2]'],
              rows, [16, 34, 26, 14, 26, 26, 26],
              ['l', 'c', 'c', 'r', 'r', 'r', 'r'])
        pg.note('壁の高さはその階の耐力壁の高さの範囲。'
                '階高は壁ごとにその壁自身の高さ H を用いる。')
        pg.close()

        # ---- 2ページ目: 存在壁量・検定 ---------------------------------
        pg = Page(pdf)
        pg.heading('4. 存在壁量')
        rows = []
        for r in chk['rows']:
            for d in ('x', 'y'):
                v = r[d]
                rows.append([r['name'] if d == 'x' else '', d.upper(),
                             '%.3f' % v['exist'], '%.2f' % v['exist_q']])
        table(pg, ['階', '方向', '存在壁量 ΣLβ [m]', '許容耐力 [kN]'],
              rows, [24, 20, 50, 50], ['l', 'c', 'r', 'r'])
        pg.note('存在壁量 = Σ(壁長 L × 壁倍率 β)、許容耐力 = ΣLβ × %g kN/m。'
                % p['qa'])

        pg.heading('5. 壁量の検定')
        rows, rc = [], []
        for r in chk['rows']:
            for d in ('x', 'y'):
                v = r[d]
                rows.append([
                    r['name'] if d == 'x' else '', d.upper(),
                    '%.1f' % r['wsum'], '%.3f' % r['ai'], '%.3f' % r['ci'],
                    '%.2f' % r['qi'], '%.3f' % v['req_eq'],
                    '%.3f' % v['req_w'], '%.3f' % v['req'], v['gov'],
                    '%.3f' % v['exist'],
                    _f(v['ratio'], 2) if v['ratio'] is not None else '-',
                    'OK' if v['ok'] else 'NG'])
                rc.append(None if v['ok'] else C_NG)
        table(pg, ['階', '方向', 'ΣWi\n[kN]', 'Ai', 'Ci', 'Qi\n[kN]',
                   '必要壁量\n地震 [m]', '必要壁量\n風 [m]', '必要壁量\n採用 [m]',
                   '支配', '存在壁量\n[m]', '検定比', '判定'],
              rows, [14, 12, 16, 12, 12, 14, 18, 18, 18, 12, 16, 12, 12],
              ['l', 'c', 'r', 'r', 'r', 'r', 'r', 'r', 'r', 'c', 'r', 'r',
               'c'], rc, head_lines=2)
        pg.note('検定比 = 必要壁量 / 存在壁量。1.00 以下で OK。')
        pg.note('層重量の根拠は別紙参照。')

        pg.heading('6. 4分割法 (令46条 / 平12建告1352号)')
        rows, rc = [], []
        for r in chk['rows']:
            for d in ('x', 'y'):
                q = r[d].get('q4')
                if not q:
                    continue
                s1, s2 = q['side']
                rows.append([
                    r['name'], d.upper(), q['axis'],
                    '%.3f' % s1['exist'],
                    _f(s1['ratio'], 2) if s1['ratio'] is not None else '-',
                    '%.3f' % s2['exist'],
                    _f(s2['ratio'], 2) if s2['ratio'] is not None else '-',
                    '%.3f' % q['req_side'],
                    _f(q['wr'], 3) if q['wr'] is not None else '-',
                    'OK' if q['ok'] else 'NG'])
                rc.append(None if q['ok'] else C_NG)
        table(pg, ['階', '方向', '分割軸', '側端① 壁量\n[m]', '側端①\n充足率',
                   '側端② 壁量\n[m]', '側端②\n充足率', '側端の必要\n壁量 [m]',
                   '壁率比', '判定'],
              rows, [16, 14, 14, 24, 18, 24, 18, 24, 16, 12],
              ['l', 'c', 'c', 'r', 'r', 'r', 'r', 'r', 'r', 'c'], rc,
              head_lines=2)
        pg.note('X方向の壁は Y軸で、Y方向の壁は X軸で平面を4分割し、両端1/4 '
                '(側端部分) の壁量充足率と壁率比を求める。'
                '両側の充足率がともに1.0超、または壁率比0.5以上でOK。')
        pg.note('側端部分の必要壁量は「その階の地震力による必要壁量 × 1/4」'
                'とした。壁率比は両側を同じ値で割るためこの仮定の影響を'
                '受けない。')
        pg.close()

        # ---- 各階の配置図 -----------------------------------------------
        for st in list(reversed(res['stories'])):
            pg = Page(pdf)
            ex = res['exist'].get(st['no'], {'X': 0, 'Y': 0})
            pg.heading('7. 耐力壁の配置 — %s' % st['name'])
            pg.text(ML, '床レベル %s m　/　ΣLβ  X = %.3f m,  Y = %.3f m'
                    % (' / '.join('%.3f' % z for z in st['levels']),
                       ex['X'], ex['Y']), FS_BODY)
            pg.space(1)
            pg.note('青=X方向の壁 / 緑=Y方向の壁 / 橙=斜め壁 / 灰破線=算入'
                    'しない壁。長い破線は勾配壁。■は柱 (鉛直な線材)。'
                    '薄い塗りは4分割法の側端部分。□の数字は壁符号。')
            hold = pg.y
            rot = plan(pg, res, st, pg.room() - 4.0)
            if rot:                     # 回した図は向きを明記する
                pg.y = hold
                pg.note('※ 用紙に大きく載るよう、図を反時計回りに90°'
                        '回している (紙面の上が X 方向、左が Y 方向)。')
            pg.close()

        # ---- 耐力壁の一覧 -------------------------------------------------
        cols = ['壁符号', '方向', '高さ範囲 [m]', '浮き\n[m]', 'L\n[m]',
                'Lx\n[m]', 'Ly\n[m]', 'β', 'Lβx\n[m]', 'Lβy\n[m]',
                'H\n[m]', '階高/L', '勾配\n[°]', '4分割法', '算入']
        wid = [16, 10, 21, 10, 10, 10, 10, 8, 12, 12, 10, 10, 9, 20, 20]
        al = ['l', 'c', 'c', 'r', 'r', 'r', 'r', 'r', 'r', 'r', 'r', 'r',
              'r', 'l', 'l']
        rows, rc = [], []
        for w in res['walls']:
            rows.append([
                '%s-%s' % (w['story_name'], w['name']),
                w['dir'].replace('斜め', ''),
                w['zrange'], '%.3f' % w['gap'], '%.3f' % w['L'],
                '%.3f' % w['lx'] if w['lx'] > 0 else '-',
                '%.3f' % w['ly'] if w['ly'] > 0 else '-',
                ('%g' % w['beta']) + ('*' if w['beta_capped'] else ''),
                '%.3f' % w['lbx'] if w['lbx'] > 0 else '-',
                '%.3f' % w['lby'] if w['lby'] > 0 else '-',
                '%.3f' % w['H'],
                '%.2f' % w['aspect'] if math.isfinite(w['aspect']) else '-',
                '%.1f' % w['slope'], _q4_short(w),
                'する' if w['used'] else _why_short(w['why'])])
            rc.append(None if w['used'] else C_OFF)
        per = 40
        for i in range(0, len(rows), per):
            pg = Page(pdf)
            pg.heading('8. 耐力壁の一覧 (%d / %d)'
                       % (i // per + 1, (len(rows) + per - 1) // per))
            table(pg, cols, rows[i:i + per], wid, al, rc[i:i + per],
                  head_lines=2, size=FS_SMALL)
            if i + per >= len(rows):
                pg.note('Lx / Ly は各方向へ算入した壁長、Lβx / Lβy はそれに'
                        '壁倍率を乗じた値 (斜め壁は方向余弦で分解)。'
                        'β の * は壁倍率の上限で頭打ちにしたもの。'
                        '「4分割法」の ①② は側端部分へ算入した長さ [m]。'
                        '算入しない壁の理由は 2. の条件に対応する。')
            pg.close()
    return out_path


def _why_short(why):
    """除外理由を一覧の狭い欄に収まる長さに詰める (詳細は CSV にある)."""
    s = str(why or '')
    for key, lab in (('垂れ壁', '垂れ壁'), ('壁長', '壁長不足'),
                     ('階高/壁長', '細長い'), ('斜め壁', '斜め壁')):
        if s.startswith(key):
            return 'しない: ' + lab
    return 'しない'


def _q4_short(w):
    out = []
    for d in ('X', 'Y'):
        q = (w.get('q4') or {}).get(d)
        if not q:
            continue
        pp = []
        if q[0] > 0.0005:
            pp.append('①%.2f' % q[0])
        if q[1] > 0.0005:
            pp.append('②%.2f' % q[1])
        if pp:
            out.append('%s %s' % (d, '+'.join(pp)))
    return ' / '.join(out)
