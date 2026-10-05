# -*- coding: utf-8 -*-
"""RC定着・付着・柱梁接合部検定の検討書 (A4縦 PDF).

wallqty/report.py の紙面ヘルパを流用する (jointchk/report.py と同じ作法)。

構成:
  1. 検定方針 (準拠規準・検定式・モデル解釈の仮定)
  2. 共通条件
  3. 梁主筋の定着の検定 (ト形・L形接合部)
  4. 通し配筋の径の検定 (十字形・T形接合部)
  5. 付着の検定 (梁、断面ごとの最大せん断力で代表)
  6. 柱梁接合部のせん断の検定
"""

import os

import matplotlib
matplotlib.use('Agg')
from matplotlib.backends.backend_pdf import PdfPages

from mgtkit.draw_model import _setup_japanese_font
from mgtkit.wallqty.report import Page, table, FS_SMALL, C_NG


def _f0(v):
    return '-' if v is None else '%.0f' % v


def _f1(v):
    return '-' if v is None else '%.1f' % v


def _f2(v):
    return '-' if v is None else '%.2f' % v


def _ox(ok):
    return 'OK' if ok else 'NG'


def _paged_table(pdf, pg, cols, rows, widths, cont_title=None, rowcol=None,
                 head_lines=1, size=FS_SMALL):
    """jointchk/report.py と同じページ送りつき表."""
    rh = size * 0.52 + 1.6 + size * 0.20
    hh = size * 0.42 * head_lines + 2.2
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


#: 定着の判定方法ごとの方針文
_ANCHOR_POLICY = {
    'rc': '定着: la ≥ lab = α・S・σt・db/(10・fb) (RC規準17.1、17.2式) '
          'による。σt は原則の短期許容引張応力度、fb は表16.1「その他の'
          '鉄筋」欄 (Fc/40+0.9)、S は表17.1 による。2段配筋は投影定着'
          '長さが短くなる内側段筋についても検定する (17条解説により、'
          '定着では多段配筋でも fb に0.6を乗じる必要はない)。',
    'k432': '定着: 平成23年国土交通省告示第432号 l ≥ k・σ・d/(F/4+9) '
            '(k=1.57) による。σ は短期の応力度とするが短期許容応力度を'
            '下限とするため、本検討では短期許容応力度を用いる。',
    'proj': '定着: 折曲げ定着の投影定着長さを仕口部材全せいの0.75倍以上'
            '確保する (RC規準17条1.(5)3) の基本値) ことにより検定する。',
}

_ANCHOR_LABEL = {'rc': 'RC規準17.2式', 'k432': '告示432号',
                 'proj': '0.75D (RC規準17条細則)'}

_POLICY = [
    '本検討は日本建築学会「鉄筋コンクリート構造計算規準・同解説 2010」'
    '(以下、RC規準) の 17条 (定着)、16条 (付着)、15条3項 (柱梁接合部) '
    'による。',
    '@ANCHOR@',
    '通し配筋 (十字形・T形): db/D ≤ 3.6・(1.5+0.1Fc)/ft (RC規準17.3式)。',
    '付着: 長期 τ=QL/(Σψ・j)≤Lfa (16.1式)、短期 τ=Qs/(Σψ・j)≤sfa '
    '(16.3式)、大地震動に対する安全性 τy=σy・db/(4(ld−d))≤K・fb '
    '(16.5〜16.7式)。fa は表6.3 (短期は長期の1.5倍)、fb は表16.1 による。'
    '付着長さ ld は通し配筋として扱う。',
    '柱梁接合部: QDj=Σ(My/j)(1−ξ) ≤ QAj=κA(fs−0.5)bj・D '
    '(15.10、15.11、15.13式)。梁の降伏曲げは My=0.9・at・σy・d、'
    'σy は鉄筋の規格降伏点とする。',
    '【モデル解釈の仮定】柱断面 H×B の向きはβ角から判定 (β=0: Hが'
    'X方向)。接合部形式 (十字/T/ト/L) は柱・梁の接続から自動判定して'
    'おり、設計者が伏図と照合して確認すること。鉄筋材種は径から自動決定'
    '(D19以下SD295、D29未満SD345、D29以上SD390) している。',
    '梁の内法長さは節点間距離から両端柱せいの1/2を控除した値とする。'
    '梁の有効せいは かぶり+あばら筋径+主筋径/2 (2段配筋は重心) による。',
]


def export_report(res, out_path, mgt_path='', project=''):
    """検討書PDFを out_path に書き出す。パスを返す."""
    _setup_japanese_font()
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    p = res['params']
    chap = [0]

    def _heading(pg, title):
        chap[0] += 1
        pg.heading('%d. %s' % (chap[0], title))
        return pg

    with PdfPages(out_path) as pdf:
        pg = Page(pdf, title='RC定着・付着・柱梁接合部の検定'
                  + (' — ' + project if project else ''))
        if mgt_path:
            pg.note('モデル: %s' % os.path.basename(mgt_path))
            pg.space(2)

        # 1. 検定方針
        method = p.get('anchor_method', 'rc')
        pg = _heading(pg, '検定方針')
        for s in _POLICY:
            if s == '@ANCHOR@':
                s = _ANCHOR_POLICY[method]
            pg.note('・' + s)
        pg.space(3)

        # 2. 共通条件
        pg = _heading(pg, '共通条件')
        rows = [
            ['梁の主筋かぶり (帯筋外まで)', '%.0f mm' % p['beam_cover']],
            ['定着の控除長さ (柱せい−投影定着長さ)',
             '%.0f mm' % p['anchor_offset']],
            ['2段目筋の投影長さ控除',
             '自動 (主筋径+段あき)' if p.get('anchor_offset2', -1) < 0
             else '%.0f mm' % p['anchor_offset2']],
            ['定着の判定方法', _ANCHOR_LABEL[method]],
        ]
        if method == 'rc':
            rows += [
                ['17.2式 α (1.0=コア内定着)', '%.2f' % p['alpha']],
                ['17.2式 S (0.7=標準フック / 1.25=直線)', '%.2f' % p['S']],
            ]
        rows += [
            ['付着長さ ld', '内法長さ L' if p['ld_mode'] == 'L'
             else '(L+d)/2 (両端曲げ降伏)'],
            ['検定ケース', ', '.join(c['name'] for c in res['cases'])],
        ]
        ss = res.get('sec_select')
        if ss:
            rows.append(['接合部形式判定に算入した梁断面',
                         ', '.join(ss['form']) or '(なし)'])
            if ss['form_off']:
                rows.append(['形式判定に算入しない梁断面 (ダミー梁等)',
                             ', '.join(ss['form_off'])])
            rows.append(['定着・通し配筋・付着の検定対象断面',
                         ', '.join(ss['check']) or '(なし)'])
        pg = _paged_table(pdf, pg, ['項目', '値'], rows, [70, 105])
        pg.space(3)

        # 3. 定着 (選択した判定方法の列のみ載せる)
        if res['anchor']:
            pg = _heading(pg, '梁主筋の定着の検定 (ト形・L形接合部、'
                          + _ANCHOR_LABEL[method] + 'による)')
            base_cols = ['節点', '方向', '形式', '柱', 'D\n(mm)', '梁',
                         '位置', '配筋']
            base_w = [10, 9, 10, 16, 10, 16, 9, 14]
            if method == 'rc':
                cols = base_cols + ['σt\n(N/mm2)', 'lab\n(mm)',
                                    'la\n(mm)', 'la2段\n(mm)', '判定']
                widths = base_w + [14, 13, 13, 13, 10]
                vals = lambda r: [_f0(r['sigma']), _f0(r['lab']),
                                  _f0(r['la']), _f0(r['la2']),
                                  _ox(r['ok'])]
                note = ('la: 確保できる投影定着長さ (柱せい−控除長さ)。'
                        'la2段: 2段配筋の内側段筋の投影定着長さ (la−'
                        '主筋径−段あき)。lab: RC規準17.2式の必要定着'
                        '長さ。2段配筋は la・la2段 の両方を lab と比較'
                        'する (17条解説により fb の0.6倍は乗じない)。')
            elif method == 'k432':
                cols = base_cols + ['σ\n(N/mm2)', '必要l\n(mm)',
                                    'la\n(mm)', 'la2段\n(mm)', '判定']
                widths = base_w + [14, 14, 13, 13, 10]
                vals = lambda r: [_f0(r['sigma']), _f0(r['l432']),
                                  _f0(r['la']), _f0(r['la2']),
                                  _ox(r['ok'])]
                note = ('la: 確保できる投影定着長さ (柱せい−控除長さ)。'
                        'la2段: 2段配筋の内側段筋の投影定着長さ。'
                        '必要l: 告示432号 l≥k・σ・d/(F/4+9) (k=1.57) '
                        'による必要定着長さ。')
            else:
                cols = base_cols + ['0.75D\n(mm)', 'la\n(mm)', '判定']
                widths = base_w + [16, 16, 12]
                vals = lambda r: [_f0(r['proj']), _f0(r['la']),
                                  _ox(r['ok'])]
                note = ('la: 確保できる投影定着長さ (柱せい−控除長さ)。'
                        '0.75D: RC規準17条1.(5)3) による投影定着長さの'
                        '基本値 (仕口部材全せいの0.75倍)。')
            body, rc = [], []
            for r in res['anchor']:
                body.append(['%d' % r['node'], r['dir'], r['form'],
                             r['col'], _f0(r['Dc']), r['beam'], r['pos'],
                             '%d-D%d' % (r['n'], r['di'])] + vals(r))
                rc.append(None if r['ok'] else C_NG)
            pg = _paged_table(pdf, pg, cols, body, widths,
                              cont_title='梁主筋の定着の検定',
                              rowcol=rc, head_lines=2)
            pg.note(note)
            pg.space(3)

        # 4. 通し配筋
        if res['through']:
            pg = _heading(pg, '通し配筋の径の検定 (十字形・T形接合部)')
            cols = ['節点', '方向', '形式', '柱', 'D\n(mm)', '梁', '位置',
                    'db\n(mm)', '上限db\n(mm)', '判定']
            body, rc = [], []
            for r in res['through']:
                body.append(['%d' % r['node'], r['dir'], r['form'],
                             r['col'], _f0(r['Dc']), r['beam'], r['pos'],
                             '%d' % r['di'], _f1(r['limit']),
                             _ox(r['ok'])])
                rc.append(None if r['ok'] else C_NG)
            pg = _paged_table(pdf, pg, cols, body,
                              [10, 9, 10, 18, 11, 18, 9, 11, 13, 10],
                              cont_title='通し配筋の径の検定',
                              rowcol=rc, head_lines=2)
            pg.note('RC規準17.3式。主筋の降伏が生じない部材では緩和'
                    'できる (17条1.(4))。')
            pg.space(3)

        # 5. 付着
        if res['bond']:
            pg = _heading(pg, '付着の検定 (断面ごとの最大せん断力で代表)')
            cols = ['符号', '位置', '配筋', 'Σψ\n(mm)', 'd\n(mm)',
                    'QL\n(kN)', 'τL\n(N/mm2)', 'Lfa\n(N/mm2)', '判定',
                    'Qs\n(kN)', 'τs\n(N/mm2)', 'sfa\n(N/mm2)', '判定']
            body, rc = [], []
            for r in res['bond']:
                body.append([
                    r['beam'], r['pos'], '%d-D%d' % (r['n'], r['di']),
                    _f0(r['psi']), _f0(r['d']), _f1(r['ql']),
                    _f2(r['tau_l']), _f2(r['fa_l']), _ox(r['ok_l']),
                    _f1(r['qs']), _f2(r['tau_s']), _f2(r['fa_s']),
                    _ox(r['ok_s'])])
                rc.append(None if (r['ok_l'] and r['ok_s']) else C_NG)
            pg = _paged_table(pdf, pg, cols, body,
                              [18, 9, 14, 12, 11, 12, 13, 13, 9, 12,
                               13, 13, 9],
                              cont_title='付着の検定 (長期・短期)',
                              rowcol=rc, head_lines=2)
            pg.space(2)
            if p.get('do_bond_safety', True):
                pg.note('大地震動に対する安全性 (16.5式):')
                cols2 = ['符号', '位置', 'ld\n(mm)', 'τy\n(N/mm2)', 'K',
                         'fb\n(N/mm2)', 'K・fb\n(N/mm2)', '判定']
                body2, rc2 = [], []
                for r in res['bond']:
                    body2.append([r['beam'], r['pos'], _f0(r['ld']),
                                  _f2(r['tau_y']), _f2(r['K']),
                                  _f2(r['fb']), _f2(r['kfb']),
                                  _ox(r['ok_y'])])
                    rc2.append(None if r['ok_y'] else C_NG)
                pg = _paged_table(pdf, pg, cols2, body2,
                                  [24, 10, 14, 15, 12, 15, 15, 10],
                                  cont_title='付着の検定 (安全性)',
                                  rowcol=rc2, head_lines=2)
                pg.note('(16.5)式は付着割裂強度算定式 (藤井・森田式) を'
                        '安全側に簡略化した設計式であり、本条1項(4)2)に'
                        'より短期設計を行い、かつ付着割裂強度を別途算定'
                        'して安全性を検討する場合、ならびに短期荷重に対し'
                        'て付着割裂破壊を生じるおそれがない曲げ材 (スラブ'
                        '・小梁などの長期荷重が支配的な部材や、耐震壁が'
                        '地震力の大半を負担する建物の柱・梁部材など) の'
                        '場合は、この算定を省略してよい (RC規準2010 16条'
                        '解説 p.212)。')
            else:
                pg.note('大地震動に対する安全性 (16.5式) の検討は省略'
                        'した。本建物は耐震壁が地震力の大半を負担し、'
                        '短期荷重に対して付着割裂破壊を生じるおそれがない'
                        '曲げ材に該当するため、RC規準2010 16条解説 '
                        '(p.212) により (16.5)〜(16.7)式による算定を省略'
                        'できる。付着は長期 (16.1式)・短期 (16.3式) の'
                        '存在応力に対する検定によった。')
            pg.space(3)

        # 6. 柱梁接合部
        if res['joints']:
            pg = _heading(pg, '柱梁接合部のせん断の検定 (RC規準15条3項)')
            cols = ['節点', '方向', '形式', '柱', 'D×b\n(mm)', '梁',
                    'bj\n(mm)', 'QAj\n(kN)', 'ΣMy/j\n(kN)', 'ξ',
                    'QDj\n(kN)', 'QDj/QAj', '判定']
            body, rc = [], []
            for r in res['joints']:
                body.append([
                    '%d' % r['node'], r['dir'], r['form'], r['col'],
                    '%s×%s' % (_f0(r['Dc']), _f0(r['bc'])), r['beams'],
                    _f0(r['bj']), _f1(r['QAj']), _f1(r['sum_myj']),
                    _f2(r['xi']), _f1(r['QDj']), _f2(r['ratio']),
                    _ox(r['ok'])])
                rc.append(None if r['ok'] else C_NG)
            pg = _paged_table(pdf, pg, cols, body,
                              [10, 8, 9, 14, 17, 22, 11, 12, 13, 9,
                               12, 12, 9],
                              cont_title='柱梁接合部のせん断の検定',
                              rowcol=rc, head_lines=2)
            hoops = [r for r in res['joints'] if r.get('hoop')]
            if hoops:
                pg.space(2)
                pg.note('接合部内帯筋の細則 (15条3.(4): D10以上・帯筋比'
                        '0.2%以上・間隔150mm以下かつ隣接柱の帯筋間隔の'
                        '1.5倍以下)。接合部内の帯筋間隔は柱の帯筋間隔 '
                        '(MIDAS入力) の'
                        + ('%.2f' % p.get('joint_hoop_factor', 1.5))
                        + '倍として評価した:')
                cols3 = ['節点', '方向', '柱', '帯筋', '柱間隔\n(mm)',
                         '接合部間隔\n(mm)', 'pw\n(%)', '判定']
                body3, rc3 = [], []
                for r in hoops:
                    h = r['hoop']
                    body3.append(['%d' % r['node'], r['dir'], r['col'],
                                  'D%d' % h['hoop_di'],
                                  _f0(h.get('pitch_col')),
                                  _f0(h['pitch']),
                                  _f2(h['pw']), _ox(h['ok'])])
                    rc3.append(None if h['ok'] else C_NG)
                pg = _paged_table(pdf, pg, cols3, body3,
                                  [12, 10, 20, 13, 14, 15, 13, 10],
                                  cont_title='接合部内帯筋の細則',
                                  rowcol=rc3, head_lines=2)
        pg.close()
    return out_path
