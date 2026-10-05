# -*- coding: utf-8 -*-
"""梁接合部検定の検討書 (A4縦 PDF) を組む.

wallqty/report.py の紙面ヘルパ (Page / table / 定数) をそのまま流用する。
wallqty の table() は幅しか自動調整しないため、行数の多い表は
_paged_table() で紙面の残り高さに合わせて分割し、ページ送りする。

構成 (CB指示 2026-09 反映: 表紙情報・検討方針・検定ケース展開表・注記は
親の検討書側で記載する運用のため載せない):
  1. 接合金物と許容耐力
  2. 荷重ケース (入力ケースと種別指定)
  3. 検定結果総括 (符号/グループ・接合種別ごとの最大検定比)
  4. 検定表 (符号/グループ・検定ケースごとの最大検定比)
  5. 接合部伏図 (グループ指定部材。特記=色付き / 一般部=灰色)
"""

import os

import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_pdf import PdfPages

import numpy as np

from mgtkit.draw_model import _setup_japanese_font
from mgtkit.jointchk.calc import TERM_LABEL, JT_CB, JT_BB
from mgtkit.mgt import mgtopen_beam, mgtopen_node
from mgtkit.wallqty.report import (Page, table, A4, ML, MR, MB,
                                   FS_SMALL, C_NG)

#: 「未検定」行の背景色 (index.html の td.warn と同系)
C_WARN = '#fbe9b8'

#: 伏図のグループ色 (順に割当)
_PLAN_COLORS = ['#2b6cb0', '#2f855a', '#b45309', '#8b3a9c',
                '#b3232a', '#0e7490', '#846a12', '#555']


def _status(ng, nc):
    """判定セルの文字列と行背景色。NG > 未検定 > OK の優先順."""
    if ng:
        return 'NG', C_NG
    if nc:
        return '未検定', C_WARN
    return 'OK', None


def _f1(v):
    return '-' if v is None else '%.1f' % v


def _f2(v):
    return '-' if v is None else '%.2f' % v


def _ratio_cell(row, key):
    """検定比セルの文字列 (None = 応力の向きが逆 or 耐力未入力)."""
    v = row[key]
    if v is not None:
        s = '%.2f' % v
        if key == 'rs' and row.get('q_rev'):
            s += ' (逆)'
        return s
    if key == 'rt' and not row['tension']:
        return '(圧縮)'
    if key == 'rc' and not row.get('compression'):
        return '(引張)'
    return '-'


def _drop_col(cols, widths, body, idx):
    """表から idx 列を除く (接合種別の非表示用)。幅は先頭列に足す."""
    w = widths[idx]
    cols = cols[:idx] + cols[idx + 1:]
    widths = list(widths[:idx]) + list(widths[idx + 1:])
    widths[0] += w
    body = [r[:idx] + r[idx + 1:] for r in body]
    return cols, widths, body


def _paged_table(pdf, pg, cols, rows, widths, cont_title=None, rowcol=None,
                 head_lines=1, size=FS_SMALL):
    """表を紙面の残り高さに合わせて分割して描く。描画後の Page を返す.

    cont_title: 改ページ後の続きページに出す見出し (None = 見出しなし)。
    行高は table() と同じ見積り (2段折返し行のぶん少し余裕をみる)。
    """
    rh = size * 0.52 + 1.6 + size * 0.20      # 1行 + 折返しの余裕 [mm]
    hh = size * 0.42 * head_lines + 2.2       # 見出し行 [mm]
    while rows:
        room = pg.room() - hh - 4.0
        per = max(1, int(room // rh))
        chunk, rows = rows[:per], rows[per:]
        rc = None
        if rowcol is not None:
            rc, rowcol = rowcol[:per], rowcol[per:]
        table(pg, cols, chunk, widths, rowcol=rc, head_lines=head_lines,
              size=size)
        if rows:
            pg.close()
            pg = Page(pdf)
            if cont_title:
                pg.heading(cont_title + ' (続き)')
    return pg


_CTYPE_LABEL = {'L': '長期', 'H': '水平のみ (長期と±組合せ)',
                'S': '短期 (組合せ済み)', 'M': '中短期 (組合せ済み)'}


# ---------------------------------------------------------------------------
# 接合部伏図 (グループ指定部材のピン端に接合の種類をプロット)
# ---------------------------------------------------------------------------

def _plan_collect(res, mgt_path):
    """伏図データを組む。グループ指定部材が無ければ None.

    戻り値: {'levels': [z...], 'ctx': {z: [(x1,y1,x2,y2), ...]},
             'members': {z: [(m, グループ名)]}, 'groups': [g...],
             'color': {g: 色}, 'label': {g: 表示名+金物}, 'xyz': {...}}
    """
    gmembers = [m for m in res['members']
                if str(m.get('unit', '')).startswith('g|')]
    if not gmembers:
        return None
    node = mgtopen_node(mgt_path)
    beam = mgtopen_beam(mgt_path)
    xyz = {}
    for r in np.atleast_2d(node):
        xyz[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]))

    def _lvl(ni, nj):
        return round((xyz[ni][2] + xyz[nj][2]) / 2.0, 1)

    groups = []
    for m in gmembers:
        g = m['unit'][2:]
        if g not in groups:
            groups.append(g)
    color = {g: _PLAN_COLORS[i % len(_PLAN_COLORS)]
             for i, g in enumerate(groups)}
    # 表示名と金物 (summary から。表示名は「グループ名(特記)」等の上書き後)
    label = {}
    for s in res['summary']:
        if str(s['sec']).startswith('g|'):
            g = s['sec'][2:]
            if g not in label:
                fit = s['fit'] or '手入力'
                if s.get('species'):
                    fit += ' (%s)' % s['species']
                label[g] = '%s: %s' % (s['name'], fit)

    # レベルのクラスタリング: 0.5m 以内は同一レベル (勾配梁・段差対策で
    # ページが乱立しないようにする)。代表値はクラスタ内の平均Z
    raw = sorted(((_lvl(m['node_i'], m['node_j']), m) for m in gmembers),
                 key=lambda t: t[0])
    clusters = []
    for z, m in raw:
        if clusters and z - clusters[-1][0][0] <= 0.5:
            clusters[-1].append((z, m))
        else:
            clusters.append([(z, m)])
    members = {}
    for cl in clusters:
        zc = round(sum(z for z, _m in cl) / len(cl), 2)
        members[zc] = [(m, m['unit'][2:]) for _z, m in cl]
    levels = sorted(members)

    # 文脈: 各レベルの水平梁 (両端のZがレベル±0.5m以内)
    ctx = {z: [] for z in levels}
    tol = 0.5
    if beam.size:
        for r in np.atleast_2d(beam):
            ni, nj = int(r[3]), int(r[4])
            if ni not in xyz or nj not in xyz:
                continue
            zi, zj = xyz[ni][2], xyz[nj][2]
            for z in levels:
                if abs(zi - z) < tol and abs(zj - z) < tol:
                    ctx[z].append((xyz[ni][0], xyz[ni][1],
                                   xyz[nj][0], xyz[nj][1]))
    return {'levels': levels, 'ctx': ctx, 'members': members,
            'groups': groups, 'color': color, 'label': label, 'xyz': xyz}


def _plan_draw(pg, data, z, hide_jtype=False):
    """1レベルぶんの伏図を現在ページの残り領域に描く.

    hide_jtype=True のときはピン端のマーカーを種別によらず●で描く。
    """
    xyz = data['xyz']
    segs = list(data['ctx'].get(z, []))
    mem = data['members'][z]
    for m, _g in mem:
        a, b = xyz[m['node_i']], xyz[m['node_j']]
        segs.append((a[0], a[1], b[0], b[1]))
    xs = [s[0] for s in segs] + [s[2] for s in segs]
    ys = [s[1] for s in segs] + [s[3] for s in segs]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = max(xmax - xmin, 0.1)
    dy = max(ymax - ymin, 0.1)
    x0, x1 = ML, A4[0] - MR
    y0 = pg.y + 4.0
    y1 = A4[1] - MB - 6.0
    sc = min((x1 - x0) / dx, (y1 - y0) / dy)
    ox = x0 + ((x1 - x0) - dx * sc) / 2.0
    oy = y0 + ((y1 - y0) - dy * sc) / 2.0

    def P(x, y):
        # 世界座標Y (北) をページ上方向 (=yが小さい方) に対応させる
        return (ox + (x - xmin) * sc, oy + (ymax - y) * sc)

    for s in data['ctx'].get(z, []):
        p1 = P(s[0], s[1])
        p2 = P(s[2], s[3])
        pg.ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                   color='#c3c8d0', lw=0.7, solid_capstyle='round')
    for m, g in mem:
        a, b = xyz[m['node_i']], xyz[m['node_j']]
        p1, p2 = P(a[0], a[1]), P(b[0], b[1])
        col = data['color'][g]
        pg.ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=col, lw=1.8,
                   solid_capstyle='round')
        pg.ax.text((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2 - 1.2,
                   '%d' % m['ele'], fontsize=5.0, ha='center', va='bottom',
                   color=col)
        # ピン端の接合種別マーカー (端から少し内側に寄せて描く)
        for e in m['ends']:
            if e['end'] == 'i':
                pe, po = p1, p2
            else:
                pe, po = p2, p1
            vx, vy = po[0] - pe[0], po[1] - pe[1]
            ln = max((vx ** 2 + vy ** 2) ** 0.5, 1e-6)
            off = min(2.5, 0.15 * ln)
            mx, my = pe[0] + vx / ln * off, pe[1] + vy / ln * off
            if hide_jtype or e['jtype'] == JT_CB:
                pg.ax.plot([mx], [my], marker='o', ms=4.2, mfc='#222',
                           mec='#222', mew=0.7, lw=0)
            elif e['jtype'] == JT_BB:
                pg.ax.plot([mx], [my], marker='o', ms=4.2, mfc='white',
                           mec='#222', mew=0.9, lw=0)
            else:
                pg.ax.plot([mx], [my], marker='^', ms=4.6, mfc='white',
                           mec='#b3232a', mew=0.9, lw=0)


def export_report(res, out_path, mgt_path='', project=''):
    """検定結果 res (calc.run_check の戻り値) から検討書PDFを作る."""
    _setup_japanese_font()
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    sec_no = 0

    def _heading(pg, text, need=40.0):
        """見出しを打つ。残り高さが足りなければ先に改ページする."""
        nonlocal sec_no
        if pg.room() < need:
            pg.close()
            pg = Page(pdf)
        sec_no += 1
        pg.heading('%d. %s' % (sec_no, text))
        return pg

    with PdfPages(out_path) as pdf:
        # 物件名・入力モデル・作成日・検討方針は載せない (親の検討書側で
        # 記載する運用のため。CB指示 2026-09)
        pg = Page(pdf, title='木造梁接合部の検定')

        comp = bool(res.get('check_compression'))
        hide = bool(res.get('hide_jtype'))

        pg = _heading(pg, '接合金物と許容耐力')
        cols = ['符号/グループ', '接合種別', '接合金物 (樹種)',
                '引張耐力\n長期 [kN]', '引張耐力\n短期 [kN]',
                'せん断耐力\n長期 [kN]', 'せん断耐力\n短期 [kN]',
                '逆せん断耐力\n短期 [kN]']
        widths = [24, 15, 44, 20, 20, 20, 20, 20]
        if comp:
            cols += ['圧縮耐力\n長期 [kN]', '圧縮耐力\n短期 [kN]']
            widths = [22, 13, 36, 17, 17, 17, 17, 17, 17, 17]
        body = []
        for s in res['summary']:
            fit = s['fit'] or '-'
            if s.get('species'):
                fit += ' (%s)' % s['species']
            row = [s['name'], s['jtype'], fit,
                   _f1(s['ta_l']), _f1(s['ta_s']),
                   _f1(s['qa_l']), _f1(s['qa_s']),
                   _f1(s.get('qr_s'))]
            if comp:
                row += [_f1(s.get('ca_l')), _f1(s.get('ca_s'))]
            body.append(row)
        multi_unit = False
        if hide:
            # 種別列を消し、耐力が同一の種別行 (手入力金物など) は1行に
            # まとめる。耐力が種別で異なる行はそのまま残す (残った場合は
            # 注記を出す)
            cols, widths, body = _drop_col(cols, widths, body, 1)
            dedup = []
            for row in body:
                if dedup and dedup[-1] == row:
                    continue
                if any(r[0] == row[0] for r in dedup):
                    if any(r == row for r in dedup):
                        continue
                    multi_unit = True
                dedup.append(row)
            body = dedup
        pg = _paged_table(pdf, pg, cols, body, widths,
                          cont_title='%d. 接合金物と許容耐力' % sec_no,
                          head_lines=2)
        if multi_unit:
            pg.note('同一符号に複数行がある場合は、接合部位に応じて'
                    '使い分ける耐力を示す。')
        pg.note('耐力は接合部1箇所あたりの許容耐力 (設計者入力。リスト選択時'
                'は金物リスト収録の基準耐力による — Stroog Strength Table '
                '2-4、または試験成績。SB系の長期せん断は短期×1.1/2、'
                '逆せん断は短期せん断×1/2 として収録)。')
        if any(s.get('ta_l_auto') for s in res['summary']):
            pg.note('長期の引張耐力は短期の値の1.1/2倍 (小数1位切捨て) '
                    'とした (金物リスト選択、または「-」指定による自動計算)。')
        if any(s.get('cap_auto') for s in res['summary']):
            pg.note('手入力で「-」を指定した耐力は、相手方の期の値から '
                    '1.1/2.0 の比率 (小数1位切捨て) で自動計算した。')
        if any(c['term'] == 'mid' for c in res['cases']):
            pg.note('中短期の検定に用いる耐力は、表の短期の値の1.6/2倍 '
                    '(小数1位切捨て) とした。')
        if comp:
            pg.note('圧縮耐力は設計者の入力による (めり込み・支圧等の検討に'
                    '基づく許容耐力。金物カタログに圧縮耐力の記載はない)。')
        pg.space(2)

        pg = _heading(pg, '荷重ケース')
        # 入力ケースと種別指定のみ載せる (展開後の検定ケース一覧は
        # 載せない。CB指示 2026-09)。種別が未指定のときだけ従来の
        # 検定ケース一覧で代用する
        if res.get('case_types_in'):
            body = [['ケース %g' % c['no'], _CTYPE_LABEL.get(c['type'],
                                                             c['type']),
                     c['name']] for c in res['case_types_in']]
            pg = _paged_table(pdf, pg, ['応力ファイルのケース', '種別指定',
                                        'ケース名'],
                              body, [40, 55, 40],
                              cont_title='%d. 荷重ケース' % sec_no)
        else:
            body = [[c['name'], TERM_LABEL.get(c['term'], c['term'])]
                    for c in res['cases']]
            pg = _paged_table(pdf, pg, ['検定ケース', '期別'], body,
                              [70, 30], cont_title='%d. 荷重ケース' % sec_no)
        pg.space(2)

        pg = _heading(pg, '検定結果総括 (符号/グループ%sごとの最大検定比)'
                          % ('' if hide else '・接合種別'))

        def _at_ele(at):
            return ('%d(%s)' % (at['ele'], at['end'])) if at else '-'

        # 接合種別の非表示時は統合版 (符号/グループ単位) を使う
        sdisp = res.get('summary_disp') or res['summary']
        body = []
        rowcol = []
        if comp:
            # 圧縮列を加えるぶん、応力値の列は省く (値は検定表に載る)
            cols = ['符号/\nグループ', '接合\n種別',
                    '引張\n検定比', '要素(端)', '決定ケース',
                    '圧縮\n検定比', '要素(端)', '決定ケース',
                    'せん断\n検定比', '要素(端)', '決定ケース', '判定']
            widths = [22, 12, 12, 15, 19, 12, 15, 19, 13, 15, 18, 8]
            for s in sdisp:
                rt_at = s['max_rt_at']
                rc_at = s.get('max_rc_at')
                rs_at = s['max_rs_at']
                body.append([
                    s['name'], s['jtype'],
                    _f2(s['max_rt']), _at_ele(rt_at),
                    rt_at['case'] if rt_at else '-',
                    _f2(s.get('max_rc')), _at_ele(rc_at),
                    rc_at['case'] if rc_at else '-',
                    (_f2(s['max_rs'])
                     + (' (逆)' if rs_at and rs_at.get('rev') else '')),
                    _at_ele(rs_at),
                    rs_at['case'] if rs_at else '-',
                    _status(s['ng'], s.get('nc'))[0]])
                rowcol.append(_status(s['ng'], s.get('nc'))[1])
        else:
            cols = ['符号/\nグループ', '接合\n種別', '引張\n検定比',
                    '要素(端)', '決定ケース', 'N [kN]',
                    'せん断\n検定比', '要素(端)', '決定ケース', 'Q [kN]',
                    '判定']
            widths = [24, 14, 13, 17, 22, 14, 13, 17, 22, 14, 10]
            for s in sdisp:
                rt_at, rs_at = s['max_rt_at'], s['max_rs_at']
                body.append([
                    s['name'], s['jtype'], _f2(s['max_rt']),
                    _at_ele(rt_at),
                    rt_at['case'] if rt_at else '-',
                    _f1(rt_at['n']) if rt_at else '-',
                    (_f2(s['max_rs'])
                     + (' (逆)' if rs_at and rs_at.get('rev') else '')),
                    _at_ele(rs_at),
                    rs_at['case'] if rs_at else '-',
                    _f1(rs_at['q']) if rs_at else '-',
                    _status(s['ng'], s.get('nc'))[0]])
                rowcol.append(_status(s['ng'], s.get('nc'))[1])
        if hide:
            cols, widths, body = _drop_col(cols, widths, body, 1)
        pg = _paged_table(pdf, pg, cols, body, widths,
                          cont_title='%d. 検定結果総括' % sec_no,
                          rowcol=rowcol, head_lines=2)
        rt_none = [s['name'] for s in sdisp if s['max_rt'] is None]
        if rt_none:
            pg.note('引張検定比が「-」の符号 (%s) は、全ピン端で軸力が'
                    '圧縮 (または引張耐力が未入力) のため引張検定を省略した。'
                    % '、'.join(rt_none))
        if comp:
            rc_none = [s['name'] for s in sdisp
                       if s.get('max_rc') is None]
            if rc_none:
                pg.note('圧縮検定比が「-」の符号 (%s) は、全ピン端で軸力が'
                        '引張 (または圧縮耐力が未入力) のため圧縮検定を'
                        '省略した。' % '、'.join(rc_none))
        pg.close()

        # ------- 検定表 (符号×検定ケースごとの最大。全部材の表は出さない) ---
        crows = res.get('case_rows') or []
        if crows:
            pg = Page(pdf)
            pg = _heading(pg, '検定表 (符号/グループ・検定ケースごとの'
                              '最大検定比)')
            pg.note('符号 (またはグループ)×検定ケースごとに、'
                    'ピン端の中で軸力 N が最大'
                    ' (引張側) となる端と、せん断の検定比が最大となる端の'
                    '値を示す (せん断は正/逆で許容耐力が異なるため、'
                    '|Q| 最大ではなく検定比最大で決定する)。'
                    + ('圧縮は軸力 N が最小 (圧縮側) となる端の値を示す。'
                       if comp else '')
                    + '全ピン端の内訳は mgtkit の画面で確認できる。')
            if comp:
                cols = ['符号/\nグループ', '接合\n種別', '検定ケース',
                        '期別',
                        '軸力N\n[kN]', '要素(端)', '引張\n検定比',
                        '圧縮N\n[kN]', '要素(端)', '圧縮\n検定比',
                        'せん断Q\n[kN]', '要素(端)', 'せん断\n検定比',
                        '判定']
                widths = [21, 12, 21, 10, 12, 13, 11, 12, 13, 11,
                          12, 13, 12, 8]
            else:
                cols = ['符号/\nグループ', '接合\n種別', '検定ケース',
                        '期別',
                        '軸力N\n[kN]', '要素(端)', '引張\n検定比',
                        'せん断Q\n[kN]', '要素(端)', 'せん断\n検定比',
                        '判定']
                widths = [24, 14, 24, 11, 14, 15, 12, 14, 15, 15, 10]
            body = []
            rowcol = []
            for r in crows:
                st, col = _status(r['ng'], r.get('nc'))
                row = [r['sec_name'], r['jtype'], r['case'],
                       TERM_LABEL.get(r['term'], r['term']),
                       _f1(r['n']), r['n_at'], _ratio_cell(r, 'rt')]
                if comp:
                    row += [_f1(r['cn']), r['cn_at'], _ratio_cell(r, 'rc')]
                row += [_f1(r['q']), r['q_at'], _ratio_cell(r, 'rs'), st]
                body.append(row)
                rowcol.append(col)
            if hide:
                cols, widths, body = _drop_col(cols, widths, body, 1)
            pg = _paged_table(pdf, pg, cols, body, widths,
                              cont_title='%d. 検定表' % sec_no,
                              rowcol=rowcol, head_lines=2)
            pg.close()

        # 注記の章は検討書には載せない (CB指示 2026-09。画面には従来どおり
        # 表示される)

        # ------- 接合部伏図 (グループ指定部材。検討書の最後に添付) -------
        plan = _plan_collect(res, mgt_path) if mgt_path else None
        if plan:
            first = True
            for z in plan['levels']:
                pg = Page(pdf)
                if first:
                    pg = _heading(pg, '接合部伏図')
                    first = False
                else:
                    pg.heading('接合部伏図 (続き)')
                pg.text(ML, 'レベル Z = %.2f m' % z, weight='bold')
                if hide:
                    pg.note('色付き: 接合部特記箇所、灰色: 接合部一般部。'
                            '部材脇の数字は要素番号。'
                            '●=検定対象のピン接合部。')
                else:
                    pg.note('色付き: 接合部特記箇所、灰色: 接合部一般部。'
                            '部材脇の数字は要素番号。'
                            'ピン端の記号は接合の種類: ●=柱-梁、○=梁-梁、'
                            '△=判定不可。')
                for g in plan['groups']:
                    if any(gg == g for _m, gg in plan['members'][z]):
                        pg.text(ML + 4, '─ ' + plan['label'].get(g, g),
                                FS_SMALL, color=plan['color'][g])
                _plan_draw(pg, plan, z, hide_jtype=hide)
                pg.close()

    return out_path
