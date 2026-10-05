# -*- coding: utf-8 -*-
"""木造水平構面の検討書 (A4縦 PDF) を組む.

wallqty/report.py の紙面ヘルパ (Page / table) と jointchk/report.py の
_paged_table を流用する。構成は手書き検討書のひな型に合わせる:

  1. 検討方法
  2. 検討対象の荷重ケース
  3. 検定結果総括 (床倍率ごとの最大検定比)
  4. 床倍率ごとの検定 (ケース別の最大応力表 + 応力コンター図)

コンター図は構面の平面投影 (X-Y) を要素ごとの最大|Fxy|で塗り分け、
最大値の発生位置に値を注記する (MIDASのコンター図の代わり)。
"""

import os

import matplotlib
matplotlib.use('Agg')
from matplotlib import colormaps
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from mgtkit.draw_model import _setup_japanese_font
from mgtkit.jointchk.report import _paged_table
from mgtkit.wallqty.report import (Page, A4, ML, MR,
                                   FS_SMALL, FS_BODY, C_NG, _fit, _wrap)

#: 荷重ケース種別の表示名 (UIの種別指定と同じ)
_CTYPE_LABEL = {'L': '長期', 'H': '水平のみ', 'S': '短期 (組合せ済み)',
                'M': '中短期 (組合せ済み)'}

#: コンター配色 (MIDASのレインボー配色に似せる)
_CMAP = colormaps['rainbow']

#: 図の1段あたりの列数と寸法 [mm]
_NCOL = 2
_GAP = 6.0
_CAP = 4.5           # 図上のケース名キャプション高さ
_HMIN, _HMAX = 28.0, 72.0


def _f2(v):
    return '%.2f' % v


def _color(v, vmax):
    return _CMAP(0.0 if vmax <= 0 else min(max(v / vmax, 0.0), 1.0))


def _worst_row(g):
    """床倍率グループの最大検定比のケース行 (決定ケースの決定要素)."""
    return next((r for r in g['cases'] if r['case'] == g['worst_case']),
                None)


def _draw_contour(pg, x0, y0, w, h, cluster, case, vmax):
    """1ケースぶんの平面コンターを (x0,y0)-(x0+w,y0+h) に描く."""
    xs = [p[0] for pl in cluster['polys'] for p in pl['xy']]
    ys = [p[1] for pl in cluster['polys'] for p in pl['xy']]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = max(xmax - xmin, 0.1)
    dy = max(ymax - ymin, 0.1)
    sc = min((w - 2.0) / dx, (h - 2.0) / dy)
    ox = x0 + (w - dx * sc) / 2.0
    oy = y0 + (h - dy * sc) / 2.0

    def P(x, y):
        # 世界座標Y (北) をページ上方向 (=yが小さい方) に対応させる
        return (ox + (x - xmin) * sc, oy + (ymax - y) * sc)

    # 方位 (X右・Y上) の小矢印を枠の左下に描く
    ax0, ay0, al = x0 + 3.0, y0 + h - 3.0, 5.0
    pg.ax.annotate('', xy=(ax0 + al, ay0), xytext=(ax0, ay0),
                   arrowprops=dict(arrowstyle='->', color='#556', lw=0.7))
    pg.ax.annotate('', xy=(ax0, ay0 - al), xytext=(ax0, ay0),
                   arrowprops=dict(arrowstyle='->', color='#556', lw=0.7))
    pg.ax.text(ax0 + al + 0.8, ay0, 'X', fontsize=5.0, ha='left',
               va='center', color='#556')
    pg.ax.text(ax0, ay0 - al - 0.6, 'Y', fontsize=5.0, ha='center',
               va='bottom', color='#556')

    ev = cluster['evals'].get(case, {})
    for pl in cluster['polys']:
        v = ev.get(pl['ele'])
        pg.ax.add_patch(plt.Polygon(
            [P(p[0], p[1]) for p in pl['xy']], closed=True,
            facecolor=(_color(v, vmax) if v is not None else '#f2f3f5'),
            edgecolor='#666', lw=0.15))
    mx = cluster['emax'].get(case)
    if mx and mx['x'] is not None:
        px, py = P(mx['x'], mx['y'])
        # 枠の縁でもマーカーが枠内に収まるようクランプする
        px = min(max(px, x0 + 1.2), x0 + w - 1.2)
        py = min(max(py, y0 + 1.2), y0 + h - 1.2)
        pg.ax.plot([px], [py], marker='v', ms=3.5, mfc='white',
                   mec='#222', mew=0.8, lw=0)
        ha = 'left' if px < x0 + w / 2 else 'right'
        # 枠の上端付近ではみ出さないよう、注記は下側へ振る
        if py < y0 + 6.0:
            ty, va = py + 2.0, 'top'
        else:
            ty, va = py - 1.5, 'bottom'
        pg.ax.text(px + (1.5 if ha == 'left' else -1.5), ty,
                   '|平均Fxy|=%.2f (要素%d)' % (abs(mx['val']), mx['ele']),
                   fontsize=6.0, ha=ha, va=va, color='#222',
                   bbox=dict(facecolor='white', edgecolor='none',
                             alpha=0.75, pad=0.6))


def _colorbar(pg, qa, vmax):
    """横向きの凡例バーを現在位置に描く (0〜vmax、許容qaの位置に印)."""
    pg.y += 3.0                       # 直前の図との間隔 (qa注記のぶん)
    x0 = ML
    w, h = 90.0, 3.6
    n = 48
    for i in range(n):
        pg.ax.add_patch(plt.Rectangle(
            (x0 + w * i / n, pg.y), w / n + 0.05, h,
            facecolor=_CMAP((i + 0.5) / n), edgecolor='none'))
    pg.ax.add_patch(plt.Rectangle((x0, pg.y), w, h, fill=False,
                                  edgecolor='#666', lw=0.4))
    # 目盛 (0〜vmax を4等分)。qaがほぼ右端のときは右端ラベルが兼ねる
    at_end = qa >= vmax - 1e-9
    for k in range(5):
        xt = x0 + w * k / 4.0
        v = vmax * k / 4.0
        pg.ax.plot([xt, xt], [pg.y + h, pg.y + h + 0.8], color='#666',
                   lw=0.4)
        lab = '%.2f (=許容qa)' % v if (k == 4 and at_end) \
            else ('0' if k == 0 else '%.2f' % v)
        pg.ax.text(xt, pg.y + h + 1.2, lab, fontsize=5.5, ha='center',
                   va='top', color='#556')
    if 0 < qa <= vmax and not at_end:
        xq = x0 + w * qa / vmax
        pg.ax.plot([xq, xq], [pg.y - 1.0, pg.y + h + 1.0], color='#222',
                   lw=0.8)
        pg.ax.text(xq, pg.y - 1.4, '許容 qa=%.2f' % qa, fontsize=5.5,
                   ha='center', va='bottom', color='#222')
    pg.ax.text(x0 + w + 14.0, pg.y + h / 2,
               '要素の|平均Fxy| [kN/m]', fontsize=5.5, ha='left',
               va='center', color='#556')
    pg.y += h + 5.0


def export_report(res, out_path):
    """検定結果 res (calc.run_check の戻り値) から検討書PDFを作る."""
    _setup_japanese_font()
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    sec_no = 0

    def _heading(pg, text, need=40.0):
        nonlocal sec_no
        if pg.room() < need:
            pg.close()
            pg = Page(pdf)
        sec_no += 1
        pg.heading('%d. %s' % (sec_no, text))
        return pg

    qa_base = res['qa_base']
    with PdfPages(out_path) as pdf:
        pg = Page(pdf, title='木造水平構面の検討')
        pg.note('解析モデルより得られた木造水平構面 (床・屋根面) の面内'
                'せん断力 (単位幅あたり) の最大値が、床倍率ごとの許容'
                'せん断耐力を下回っていることを確認する。')
        pg.space(2)

        pg = _heading(pg, '検討方法')
        pg.note('・モデル上の板要素の厚みを床倍率として入力している '
                '(例: 厚み0.00269m → 床倍率2.69。木合板耐力壁の検定と'
                '同じ規約)。床倍率の根拠仕様 (面材の種類・厚さ・釘打ち・'
                '火打等) は別紙による。')
        pg.note('・許容せん断耐力 (短期) qa = %.2f kN/m × 床倍率。'
                '%.2f kN/m は床倍率1あたりの短期許容せん断耐力 '
                '(木合板耐力壁の壁倍率1あたりの許容せん断耐力と同値)。'
                % (qa_base, qa_base))
        pg.note('・検定比 = 板要素ごとの節点 (3または4点) の面内せん断力 '
                'Fxy [kN/m] の平均の絶対値 |平均Fxy| ÷ qa。要素ごとに'
                '検定し、床倍率ごとに全要素の最大検定比で判定する '
                '(節点値の平均をとる考え方は木合板耐力壁の検定と同じ)。')
        pg.note('・対象は板要素のうち鉛直壁でないもの (水平から60°以内の'
                '勾配面。勾配屋根の構面を含む)。鉛直壁 (木合板耐力壁) は'
                '別途検定する。')
        pg.note('・検討対象は短期 (水平荷重時) の荷重ケースを基本とする。')
        pg.space(2)

        pg = _heading(pg, '検討対象の荷重ケース')
        body = [['ケース %d' % c['case'], c['label'],
                 _CTYPE_LABEL.get(c.get('type'), '-')]
                for c in res['cases']]
        pg = _paged_table(pdf, pg, ['応力ファイルのケース', 'ケース名',
                                    '種別'],
                          body, [42, 50, 40],
                          cont_title='%d. 検討対象の荷重ケース' % sec_no)
        pg.space(2)

        pg = _heading(pg, '検定結果総括 (床倍率ごとの最大検定比)')
        cols = ['床倍率', '対象\n要素数', 'Z範囲 [m]', '許容 qa\n[kN/m]',
                '|平均Fxy|\n[kN/m]', '決定ケース', '決定要素',
                '検定比', '判定']
        body = []
        rowcol = []
        for g in res['groups']:
            r = _worst_row(g)
            zr = ('%.2f' % g['zmin'] if g['zmin'] == g['zmax']
                  else '%.2f〜%.2f' % (g['zmin'], g['zmax']))
            body.append(['%g' % g['beta'], '%d' % g['n_eles'], zr,
                         _f2(g['qa']),
                         _f2(abs(r['mean'])) if r else '-',
                         g['worst_label'] or '-',
                         '%d' % r['ele'] if r else '-',
                         _f2(g['worst_ratio']),
                         'OK' if (r and r['ok']) else 'NG'])
            rowcol.append(None if (r and r['ok']) else C_NG)
        pg = _paged_table(pdf, pg, cols, body,
                          [15, 15, 26, 18, 20, 32, 16, 15, 12],
                          cont_title='%d. 検定結果総括' % sec_no,
                          rowcol=rowcol, head_lines=2)
        pg.space(2)

        # ------- 床倍率ごとの検定 (結論 + コンター図) ---------------------
        for g in res['groups']:
            pg = _heading(pg, '床倍率 %g 倍の検定' % g['beta'], need=70.0)
            r = _worst_row(g)
            if r:
                s = ('最大検定比の要素 %d: |平均Fxy| = %.2f kN/m (%s)  %s  '
                     '許容せん断耐力: %.2f kN/m × %g = %.2f kN/m'
                     % (r['ele'], abs(r['mean']), r['label'],
                        '≦' if r['ok'] else '＞',
                        qa_base, g['beta'], g['qa']))
                for ln in _wrap(s, 110):
                    pg.text(ML, ln, FS_BODY)
                pg.text(ML, 'より %s (検定比 %.2f)'
                        % ('OK' if r['ok'] else 'NG', r['ratio']), FS_BODY,
                        weight='bold',
                        color=('#222' if r['ok'] else '#b3232a'))
            pg.space(2)

            # 図の色スケールは床倍率グループで共通 (許容qa以上で最大色)
            vmax = max([g['qa']] + [abs(x['mean']) for x in g['cases']])
            for cl in g['clusters']:
                # 図の寸法 (クラスタの縦横比から決める)
                xs = [p[0] for pl in cl['polys'] for p in pl['xy']]
                ys = [p[1] for pl in cl['polys'] for p in pl['xy']]
                dx = max(max(xs) - min(xs), 0.1)
                dy = max(max(ys) - min(ys), 0.1)
                w_sub = (A4[0] - ML - MR - _GAP * (_NCOL - 1)) / _NCOL
                h_sub = min(max(w_sub * dy / dx, _HMIN), _HMAX)
                row_h = _CAP + h_sub + 3.0

                if pg.room() < 8.0 + row_h:
                    pg.close()
                    pg = Page(pdf)
                    pg.heading('床倍率 %g 倍の検定 (続き)' % g['beta'])
                if cl['zmin'] == cl['zmax']:
                    lvl = 'Z = %.2f m' % cl['zmin']
                else:
                    lvl = 'Z = %.2f〜%.2f m' % (cl['zmin'], cl['zmax'])
                pg.text(ML, '構面レベル %s (要素の|平均Fxy|で塗り分け。'
                        '▽=最大検定比の要素)' % lvl,
                        FS_SMALL, color='#556')
                pg.space(1)

                cases = [c for c in res['cases']
                         if c['case'] in cl['evals']]
                for i0 in range(0, len(cases), _NCOL):
                    if pg.room() < row_h:
                        pg.close()
                        pg = Page(pdf)
                        pg.heading('床倍率 %g 倍の検定 (続き)' % g['beta'])
                        pg.text(ML, '構面レベル %s' % lvl, FS_SMALL,
                                color='#556')
                        pg.space(1)
                    for j, c in enumerate(cases[i0:i0 + _NCOL]):
                        x0 = ML + j * (w_sub + _GAP)
                        cap = _fit(c['label'], w_sub - 4.0,
                                   FS_SMALL).split('\n')[0]
                        if cap != c['label']:
                            cap += '…'
                        pg.ax.text(x0, pg.y + 1.0, cap,
                                   fontsize=FS_SMALL, ha='left', va='top',
                                   color='#222', fontweight='bold')
                        pg.ax.add_patch(plt.Rectangle(
                            (x0, pg.y + _CAP), w_sub, h_sub, fill=False,
                            edgecolor='#c3c8d0', lw=0.5))
                        _draw_contour(pg, x0, pg.y + _CAP, w_sub, h_sub,
                                      cl, c['case'], vmax)
                    pg.y += row_h
            # 凡例は床倍率グループの末尾に1回だけ描く
            if g['clusters']:
                if pg.room() < 16.0:
                    pg.close()
                    pg = Page(pdf)
                    pg.heading('床倍率 %g 倍の検定 (続き)' % g['beta'])
                _colorbar(pg, g['qa'], vmax)
        pg.close()
    return out_path
