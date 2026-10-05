# -*- coding: utf-8 -*-
"""RC定着・付着・柱梁接合部検定のTeX書き出しとプレビューコンパイル.

計算書本文へ \\input で組み込む断片を3ファイルに分けて出力する:
  rcdetail_teichaku.tex  … 定着 (+通し配筋17.3式)
  rcdetail_fuchaku.tex   … 付着
  rcdetail_setsugobu.tex … 柱梁接合部

各断片は冒頭に検定式 (記号定義・出典つき) を掲載し、続けて検定表
(longtable) を置く。断片は onecolumn を前提とし、先頭で \\onecolumn に
切り替える (検定詳細 10detail.tex と同じ「ファイル内で切替える」流儀)。

プレビュー: export_tex.py の計算書プリアンブル (_DETAIL_TEX_PREAMBLE)
で断片を包んだドライバを platex + dvipdfmx でコンパイルし、pdftoppm で
PNG 化してブラウザ表示用に返す。
"""

import os
import subprocess

from mgtkit.export_tex import _DETAIL_TEX_PREAMBLE

#: 定着の判定方法の表示名
_AM_LABEL = {'rc': 'RC規準17.2式', 'k432': '平23国交告第432号',
             'proj': '投影定着長さ0.75D (RC規準17条細則)'}


def _esc(s):
    """表セル用の軽量TeXエスケープ."""
    out = str(s)
    for ch in ('_', '%', '&', '#'):
        out = out.replace(ch, '\\' + ch)
    return out


def _f0(v):
    return '--' if v is None else '%.0f' % v


def _f1(v):
    return '--' if v is None else '%.1f' % v


def _f2(v):
    return '--' if v is None else '%.2f' % v


def _ox(ok):
    if ok is None:
        return '--'
    return 'OK' if ok else 'NG'


def _longtable(cols, rows, align=None, caption=None):
    """longtable のTeX行 list を返す。cols/rows は文字列2次元."""
    n = len(cols)
    al = align or ('|' + 'c|' * n)
    L = []
    L.append(r'\begin{longtable}{%s}' % al)
    if caption:
        L.append(r'\multicolumn{%d}{l}{%s}\\' % (n, caption))
    L.append(r'\hline')
    L.append(' & '.join(cols) + r' \\ \hline')
    L.append(r'\endhead')
    L.append(r'\hline \endfoot')
    for r in rows:
        L.append(' & '.join(r) + r' \\')
    L.append(r'\end{longtable}')
    return L


def _frag_open(title):
    return [
        '%% mgtkit rcdetail — %s (本文へ \\input で組込む)' % title,
        r'\onecolumn',
        r'\footnotesize',
        r'\setlength{\tabcolsep}{3pt}',
        r'\noindent\textbf{%s}\par\medskip' % title,
    ]


_FRAG_CLOSE = [r'\setlength{\tabcolsep}{6pt}', r'\normalsize', '']


# ---------------------------------------------------------------------------
# 1. 定着
# ---------------------------------------------------------------------------

def _tex_teichaku(res):
    p = res['params']
    m = p['anchor_method']
    L = _frag_open('梁主筋の定着の検定 (ト形・L形接合部)')

    # ---- 冒頭: 検討式 ----
    L.append(r'\noindent 検討方法: %s による。\par' % _AM_LABEL[m])
    if m == 'rc':
        L += [
            r'\begin{equation*}',
            r'l_a \geq l_{ab} = \alpha \cdot S \cdot '
            r'\frac{\sigma_t \cdot d_b}{10 f_b}'
            r'\qquad \text{(RC規準2010 (17.1),(17.2)式)}',
            r'\end{equation*}',
            r'\noindent ここに、$l_a$: 確保できる投影定着長さ (柱せい$-$'
            r'控除長さ%.0f mm)、$l_{ab}$: 必要定着長さ、' % p['anchor_offset'],
            r'$\alpha$: 鉄筋配置による係数 ($=%.2f$)、' % p['alpha'],
            r'$S$: 必要定着長さの修正係数 (表17.1、$=%.2f$)、' % p['S'],
            r'$\sigma_t$: 仕口面における鉄筋の応力度 (短期許容引張応力度)、',
            r'$d_b$: 主筋径 (呼び名)、$f_b$: 付着割裂の基準となる強度 '
            r'(表16.1「その他の鉄筋」欄 $=F_c/40+0.9$)。'
            r'2段配筋の場合は、投影定着長さが短くなる内側段筋についても'
            r'それぞれ検定する (17条解説により、定着では多段配筋でも '
            r'$f_b$ に0.6を乗じる必要はない)。内側段筋の投影定着長さは'
            r'1段目から %s 短いものとした。\par'
            % ('主筋径$+$段あき ($\\max(25,\\,1.5d_b)$)'
               if p.get('anchor_offset2', -1) < 0
               else '%.0f mm' % p['anchor_offset2']),
        ]
    elif m == 'k432':
        L += [
            r'\begin{equation*}',
            r'l \geq \frac{k \cdot \sigma \cdot d}{F/4+9}'
            r'\qquad \text{(平成23年国土交通省告示第432号 第1第三号)}',
            r'\end{equation*}',
            r'\noindent ここに、$l$: 柱に定着される部分の水平投影の長さ、'
            r'$k=1.57$ (普通コンクリート)、$\sigma$: 短期の応力度 (短期許容'
            r'応力度を下限とするため短期許容応力度を用いる)、$F$: 設計基準'
            r'強度、$d$: 引張り鉄筋の径。\par',
        ]
    else:
        L += [
            r'\begin{equation*}',
            r'l_a \geq 0.75\,D_c \qquad'
            r'\text{(RC規準2010 17条1.(5)3)、$D_c$: 仕口部材全せい)}',
            r'\end{equation*}',
            r'\noindent ここに、$l_a$: 確保できる投影定着長さ (柱せい$-$'
            r'控除長さ%.0f mm)。\par' % p['anchor_offset'],
        ]
    L.append(r'\medskip')

    # ---- 表 ----
    if m == 'rc':
        cols = ['節点', '方向', '形式', '柱', '$D$', '梁', '位置', '配筋',
                r'$\sigma_t$', '$l_{ab}$', '$l_a$', '$l_a$(2段)', '判定']
        rows = [['%d' % r['node'], r['dir'], r['form'], _esc(r['col']),
                 _f0(r['Dc']), _esc(r['beam']), r['pos'],
                 '%d-D%d' % (r['n'], r['di']), _f0(r['sigma']),
                 _f0(r['lab']), _f0(r['la']), _f0(r['la2']), _ox(r['ok'])]
                for r in res['anchor']]
        unit = (r'(単位: mm、$\sigma_t$: N/mm$^2$。$l_a$(2段): 内側段筋'
                r'の投影定着長さ)')
    elif m == 'k432':
        cols = ['節点', '方向', '形式', '柱', '$D$', '梁', '位置', '配筋',
                r'$\sigma$', '必要$l$', '$l_a$', '$l_a$(2段)', '判定']
        rows = [['%d' % r['node'], r['dir'], r['form'], _esc(r['col']),
                 _f0(r['Dc']), _esc(r['beam']), r['pos'],
                 '%d-D%d' % (r['n'], r['di']), _f0(r['sigma']),
                 _f0(r['l432']), _f0(r['la']), _f0(r['la2']),
                 _ox(r['ok'])]
                for r in res['anchor']]
        unit = (r'(単位: mm、$\sigma$: N/mm$^2$。$l_a$(2段): 内側段筋'
                r'の投影定着長さ)')
    else:
        cols = ['節点', '方向', '形式', '柱', '$D$', '梁', '位置', '配筋',
                '$0.75D$', '$l_a$', '判定']
        rows = [['%d' % r['node'], r['dir'], r['form'], _esc(r['col']),
                 _f0(r['Dc']), _esc(r['beam']), r['pos'],
                 '%d-D%d' % (r['n'], r['di']), _f0(r['proj']),
                 _f0(r['la']), _ox(r['ok'])]
                for r in res['anchor']]
        unit = '(単位: mm)'
    if rows:
        L += _longtable(cols, rows, caption=unit)
    else:
        L.append(r'\noindent 対象となるト形・L形接合部はない。\par')

    # ---- 通し配筋 (17.3式) ----
    if res['through']:
        L += [
            r'\medskip',
            r'\noindent\textbf{通し配筋の径の検定 (十字形・T形接合部)}'
            r'\par\medskip',
            r'\begin{equation*}',
            r'\frac{d_b}{D} \leq 3.6\,\frac{1.5+0.1F_c}{f_t}'
            r'\qquad \text{(RC規準2010 (17.3)式)}',
            r'\end{equation*}',
            r'\noindent ここに、$D$: 通し配筋される部材 (柱) の全せい、'
            r'$f_t$: 主筋の短期許容引張応力度。'
            r'主筋の降伏が生じない部材ではこれを緩和してよい '
            r'(RC規準17条1.(4))。\par\medskip',
        ]
        cols = ['節点', '方向', '形式', '柱', '$D$ (mm)', '梁', '位置',
                '$d_b$ (mm)', '上限$d_b$ (mm)', '判定']
        rows = [['%d' % r['node'], r['dir'], r['form'], _esc(r['col']),
                 _f0(r['Dc']), _esc(r['beam']), r['pos'], '%d' % r['di'],
                 _f1(r['limit']), _ox(r['ok'])]
                for r in res['through']]
        L += _longtable(cols, rows)
    return L + _FRAG_CLOSE


# ---------------------------------------------------------------------------
# 2. 付着
# ---------------------------------------------------------------------------

def _tex_fuchaku(res):
    p = res['params']
    L = _frag_open('梁主筋の付着の検定')
    L += [
        r'\noindent 検討方法: RC規準2010 16条による。長期・短期の付着'
        r'応力度を次式で検定する。\par',
        r'\begin{equation*}',
        r'\tau_{a1} = \frac{Q_L}{\sum \psi \cdot j} \leq {}_L f_a'
        r'\quad \text{(16.1式)},\qquad',
        r'\tau_{a1} = \frac{Q_L + Q_E}{\sum \psi \cdot j} \leq {}_S f_a'
        r'\quad \text{(16.3式)}',
        r'\end{equation*}',
        r'\noindent ここに、$Q_L$, $Q_L+Q_E$: 長期・短期のせん断力、'
        r'$\sum\psi$: 引張鉄筋の周長の総和、$j$: 応力中心間距離 '
        r'($=(7/8)d$)、$f_a$: 許容付着応力度 (表6.3。長期: 上端筋 '
        r'$\min(F_c/15,\,0.9+2F_c/75)$、その他 $\min(F_c/10,\,'
        r'1.35+F_c/25)$。短期は長期の1.5倍)。\par',
    ]
    if p.get('do_bond_safety', True):
        L += [
            r'\noindent 大地震動に対する安全性は次式で検定する。\par',
            r'\begin{equation*}',
            r'\tau_y = \frac{\sigma_y \cdot d_b}{4(l_d - d)} \leq '
            r'K \cdot f_b \quad \text{(16.5式)},\qquad',
            r'K = 0.3\,\frac{C+W}{d_b} + 0.4 \leq 2.5 \quad'
            r'\text{(16.6式)},\qquad',
            r'W = 80\,\frac{A_{st}}{s\,N} \leq 2.5\,d_b \quad'
            r'\text{(16.7式)}',
            r'\end{equation*}',
            r'\noindent ここに、$\sigma_y$: 鉄筋の降伏強度 (規格降伏点)、'
            r'$l_d$: 付着長さ (%s)、$C$: 鉄筋間のあきと最小かぶり厚さの'
            r'3倍のうち小さいほう ($\leq 5d_b$)、$A_{st}$: 付着割裂面を'
            r'横切る一組の横補強筋全断面積、$s$: その間隔、$N$: 割裂面'
            r'における鉄筋本数、$f_b$: 付着割裂の基準となる強度 (表16.1)。'
            r'\par' % ('内法長さ$L$' if p['ld_mode'] == 'L'
                       else '$(L+d)/2$'),
        ]
    else:
        L += [
            r'\noindent なお、大地震動に対する安全性 ((16.5)式) の検討は、'
            r'本建物が耐震壁が地震力の大半を負担する建物であり、短期荷重'
            r'に対して付着割裂破壊を生じるおそれがない曲げ材に該当する'
            r'ため、RC規準2010 16条解説 (p.212) により省略する。\par',
        ]
    L.append(r'\medskip')

    cols = ['符号', '位置', '配筋', r'$\sum\psi$', '$d$', '$Q_L$',
            r'$\tau_L$', '$_Lf_a$', '判定', '$Q_s$', r'$\tau_s$',
            '$_Sf_a$', '判定']
    rows = [[_esc(r['beam']), r['pos'], '%d-D%d' % (r['n'], r['di']),
             _f0(r['psi']), _f0(r['d']), _f1(r['ql']), _f2(r['tau_l']),
             _f2(r['fa_l']), _ox(r['ok_l']), _f1(r['qs']),
             _f2(r['tau_s']), _f2(r['fa_s']), _ox(r['ok_s'])]
            for r in res['bond']]
    if rows:
        L += _longtable(cols, rows, caption=r'(長さ: mm、力: kN、'
                        r'応力度: N/mm$^2$。断面ごとの最大せん断力で代表)')
    else:
        L.append(r'\noindent 対象となる梁はない。\par')

    if p.get('do_bond_safety', True) and res['bond']:
        cols2 = ['符号', '位置', '$l_d$', r'$\tau_y$', '$K$', '$f_b$',
                 r'$K \cdot f_b$', '判定']
        rows2 = [[_esc(r['beam']), r['pos'], _f0(r['ld']),
                  _f2(r['tau_y']), _f2(r['K']), _f2(r['fb']),
                  _f2(r['kfb']), _ox(r['ok_y'])] for r in res['bond']]
        L += [r'\medskip',
              r'\noindent 大地震動に対する安全性 (16.5式):\par\smallskip']
        L += _longtable(cols2, rows2, caption=r'(長さ: mm、'
                        r'応力度: N/mm$^2$)')
    return L + _FRAG_CLOSE


# ---------------------------------------------------------------------------
# 3. 柱梁接合部
# ---------------------------------------------------------------------------

def _tex_setsugobu(res):
    L = _frag_open('柱梁接合部のせん断の検定')
    L += [
        r'\noindent 検討方法: RC規準2010 15条3項による。柱梁接合部の'
        r'設計用せん断力 $Q_{Dj}$ が許容せん断力 $Q_{Aj}$ を超えない'
        r'ことを確かめる。\par',
        r'\begin{equation*}',
        r'Q_{Aj} = \kappa_A (f_s - 0.5)\, b_j\, D \quad'
        r'\text{(15.10式)},\qquad',
        r'Q_{Dj} = \sum \frac{M_y}{j}\,(1-\xi) \quad'
        r'\text{(15.11式)},\qquad',
        r'\xi = \frac{j}{H_c\left(1-\dfrac{D}{L_b}\right)} \quad'
        r'\text{(15.13式)}',
        r'\end{equation*}',
        r'\noindent ここに、$\kappa_A$: 接合部の形状による係数 (十字形10、'
        r'T形7、ト形5、L形3)、$f_s$: コンクリートの短期許容せん断応力度、'
        r'$b_j$: 接合部の有効幅 ($=b_b+b_{a1}+b_{a2}$、'
        r'$b_{ai}=\min(b_i/2,\,D/4)$)、$D$: 柱せい、'
        r'$\sum M_y/j$: 接合部左右の梁の降伏曲げモーメントの絶対値を応力'
        r'中心距離で除した値の和 (一方が上端引張、他方が下端引張。'
        r'$M_y = 0.9\,a_t\,\sigma_y\,d$ とする)、'
        r'$H_c$: 上下柱の平均高さ (最上階は柱高さの1/2)、'
        r'$L_b$: 左右の梁の平均長さ。\par\medskip',
    ]
    cols = ['節点', '方向', '形式', '柱', r'$D\times b$', '梁', '$b_j$',
            '$Q_{Aj}$', r'$\sum M_y/j$', r'$\xi$', '$Q_{Dj}$',
            r'$Q_{Dj}/Q_{Aj}$', '判定']
    rows = [['%d' % r['node'], r['dir'], r['form'], _esc(r['col']),
             '%s$\\times$%s' % (_f0(r['Dc']), _f0(r['bc'])),
             _esc(r['beams']), _f0(r['bj']), _f1(r['QAj']),
             _f1(r['sum_myj']), _f2(r['xi']), _f1(r['QDj']),
             _f2(r['ratio']), _ox(r['ok'])] for r in res['joints']]
    if rows:
        L += _longtable(cols, rows, caption=r'(長さ: mm、力: kN)')
    else:
        L.append(r'\noindent 対象となる柱梁接合部はない。\par')

    hoops = [r for r in res['joints'] if r.get('hoop')]
    if hoops:
        p = res['params']
        L += [r'\medskip',
              r'\noindent 接合部内帯筋の細則 (15条3.(4): D10以上・帯筋比'
              r'0.2\%以上・間隔150mm以下かつ隣接柱の帯筋間隔の1.5倍'
              r'以下)。接合部内の帯筋間隔は柱の帯筋間隔の'
              + ('%.2f' % p.get('joint_hoop_factor', 1.5))
              + r'倍として評価した。\par\smallskip']
        cols3 = ['節点', '方向', '柱', '帯筋', '柱間隔 (mm)',
                 '接合部間隔 (mm)', '$p_w$ (\\%)', '判定']
        rows3 = [['%d' % r['node'], r['dir'], _esc(r['col']),
                  'D%d' % r['hoop']['hoop_di'],
                  _f0(r['hoop'].get('pitch_col')),
                  _f0(r['hoop']['pitch']),
                  _f2(r['hoop']['pw']), _ox(r['hoop']['ok'])]
                 for r in hoops]
        L += _longtable(cols3, rows3)
    return L + _FRAG_CLOSE


# ---------------------------------------------------------------------------
# 4. 耐震壁周辺柱の断面
# ---------------------------------------------------------------------------

def _tex_kabewaku(res):
    p = res['params']
    L = _frag_open('耐震壁周辺の柱の断面の検討')
    L += [
        r'\noindent 検討方法: RC規準2010 19条解説「6. 壁部材の柱と梁の'
        r'断面と配筋」(p.318--320) による。耐震壁周辺の柱の断面 (拘束域、'
        r'かぶり厚さを含む) が次を満足することを確認する。\par',
        r'\begin{equation*}',
        r"A \geq \frac{s \cdot t'}{2}, \qquad",
        r"D_{\min} \geq \sqrt{\frac{s \cdot t'}{3}} \ \text{かつ}\ 2t'",
        r'\end{equation*}',
        r"\noindent ここに、$A$: 柱の断面積、$D_{\min}$: 柱の最小径、"
        r'$s$: 壁部材の高さと壁長さの短いほうの長さ、'
        r"$t'$: 設計用せん断力 $Q_D$ に対して必要な壁板の最小壁厚で、"
        r'せん断補強筋比 $p_s\prime$ が上限値0.012となるときの壁厚として'
        r'次式による (解19.58式)。\par',
        r'\begin{equation*}',
        r"t' = \frac{Q_D \times \dfrac{Q_w}{Q_w + \sum Q_c}}"
        r'{0.012 \cdot l_e \cdot f_t}',
        r'\end{equation*}',
        r'\noindent ここに、$Q_D$: 壁部材の短期設計用せん断力 (強軸方向'
        r'の最大値)、$Q_w/(Q_w+\sum Q_c)$: 壁板の負担率 (本検討では '
        r'$%.2f$ とした)、$l_e$: 壁の長さ、$f_t$: 壁せん断補強筋の短期'
        r'許容引張応力度 ($=%.0f$ N/mm$^2$)。\par'
        % (p['wall_wr'], p['wall_ft']),
        r'\noindent なお、連層耐震壁の中間階の梁など、周辺部材の断面が'
        r'必ずしも必要でない場合が解説に例示されている (p.319 ①〜⑤)。'
        r'本検討は枠柱を対象とする。\par\medskip',
    ]
    if not res.get('walls'):
        L.append(r'\noindent 対象となる耐震壁はない。\par')
        return L + _FRAG_CLOSE

    cols = ['符号', '要素', '方向', '壁長$l_e$', '壁厚$t$', '高さ$h$',
            '$s$', '$Q_D$ (kN)', 'ケース', "$t'$",
            '必要$A$', '必要$D_{\\min}$']
    rows = []
    for w in res['walls']:
        rows.append([
            _esc(w['name']), '%d' % w['ele'], w['axis'],
            _f0(w['lw']), _f0(w['t']), _f0(w['h']), _f0(w['s']),
            _f1(w['qd']) if w['qd'] is not None else '--',
            _esc(w['qd_case']) if w['qd_case'] else '--',
            _f1(w['tp']),
            '%.0f$\\times 10^3$' % (w['a_req'] / 1000.0),
            _f0(w['d_req'])])
    L += _longtable(cols, rows, caption=r'(長さ: mm、必要$A$: mm$^2$。'
                    r"$Q_D$無しの行は $t'$=モデル壁厚)")
    L += [r'\medskip', r'\noindent 枠柱の判定:\par\smallskip']
    cols2 = ['壁符号', '端', '柱', '$B\\times D$', '$A$', '$D_{\\min}$',
             '必要$A$', '必要$D_{\\min}$', '判定']
    rows2 = []
    for w in res['walls']:
        for c in w['cols']:
            rows2.append([
                _esc(w['name']),
                '始端' if c['side'] == 'minus' else '終端',
                _esc(c['name']),
                ('%s$\\times$%s' % (_f0(c['B']), _f0(c['H'])))
                if c['B'] is not None else '--',
                ('%.0f$\\times 10^3$' % (c['A'] / 1000.0))
                if c['A'] is not None else '--',
                _f0(c['dmin']),
                '%.0f$\\times 10^3$' % (w['a_req'] / 1000.0),
                _f0(w['d_req']), _ox(c['ok'])])
    L += _longtable(cols2, rows2, caption=r'(長さ: mm、$A$: mm$^2$)')
    return L + _FRAG_CLOSE


# ---------------------------------------------------------------------------
# 5. 壁の開口補強筋
# ---------------------------------------------------------------------------

def _tex_kaikou(res):
    L = _frag_open('耐震壁の開口補強筋の検討')
    L += [
        r'\noindent 検討方法: RC規準2010 19条5項による。各階の設計用'
        r'せん断力 $Q_D$ によって生じる開口隅角部の付加斜張力および'
        r'周辺部材の付加曲げモーメントに対し、開口周囲 (開口から500mm'
        r'以内、かつ開口端と壁端もしくは隣接開口端との中間線を越えない'
        r'範囲、図19.5) に有効に配置した補強筋で抵抗することを確認'
        r'する。\par',
        r'\begin{equation*}',
        r'A_d f_t + \frac{A_v f_t + A_h f_t}{\sqrt{2}} \geq '
        r'\frac{h_0 + l_0}{2\sqrt{2}\,l}\,Q_D \quad \text{(19.14式)}',
        r'\end{equation*}',
        r'\begin{equation*}',
        r'(l - l_{0p})\left(\frac{A_d f_t}{\sqrt{2}} + A_{v0} f_t\right)'
        r' + \frac{t (l-l_{0p})^2}{4(n_h+1)}\,p_{sv} f_t \geq '
        r'\frac{h_0}{2}\,Q_D \quad \text{(19.15式)}',
        r'\end{equation*}',
        r'\begin{equation*}',
        r'(h - h_{0p})\left(\frac{A_d f_t}{\sqrt{2}} + A_{h0} f_t\right)'
        r' + \frac{t (h-h_{0p})^2}{4 n_v}\,p_{sh} f_t \geq '
        r'\frac{l_0}{2}\,\frac{h}{l}\,Q_D \quad \text{(19.16式)}',
        r'\end{equation*}',
        r'\noindent ここに、$l_0$, $h_0$: 当該開口部の長さ・高さ、'
        r'$A_d$: 開口周囲の斜め筋の断面積、$A_v$, $A_h$: 開口周囲の'
        r'縦筋・横筋の断面積、$A_{v0}$, $A_{h0}$: 開口補強の目的で'
        r'通常の壁筋とは別に配筋される縦筋・横筋の断面積、$f_t$: 鉄筋'
        r'の短期許容引張応力度 (筋種ごとの値)、$n_h$, $n_v$: 当該層で'
        r'水平・鉛直方向に並ぶ開口の数、$p_{sv}$, $p_{sh}$: 壁板の'
        r'縦筋・横筋の補強筋比、$l_{0p}$, $h_{0p}$: 並列する開口長さ'
        r'・高さの和。単層壁およびピロティ壁の最下層では (19.16)式'
        r'第2項の $n_v$ を $n_v+1$ に置き換える。\par',
        r'\noindent 【算定上の仮定】$A_v$・$A_h$ は開口補強筋 '
        r'($A_{v0}$・$A_{h0}$) に開口の両側・上下各500mm以内の壁筋を'
        r'加えた値とし、柱・梁主筋の負担は安全側に無視する。壁筋は'
        r'縦横同仕様とする。$Q_D$ は当該壁部材の強軸方向せん断力の'
        r'短期最大値による。\par\medskip',
    ]
    if not res.get('openings'):
        L.append(r'\noindent 対象となる開口はない。\par')
        return L + _FRAG_CLOSE
    cols = ['符号', '$l\\times h\\times t$', '$l_0\\times h_0$',
            '$n_h$/$n_v$', '$Q_D$', '斜め', '縦', '横',
            '(19.14)', '(19.15)', '(19.16)', '判定']
    rows = []
    for r in res['openings']:
        rows.append([
            _esc(r['name']),
            '%s$\\times$%s$\\times$%s' % (_f0(r['l']), _f0(r['h']),
                                          _f0(r['t'])),
            '%s$\\times$%s' % (_f0(r['l0']), _f0(r['h0'])),
            '%d/%d%s' % (r['nh'], r['nv'],
                         ' (単層)' if r['single'] else ''),
            _f1(r['qd']), r['d_txt'], r['v_txt'], r['h_txt'],
            '%s/%s %s' % (_f0(r['lhs14']), _f0(r['rhs14']),
                          _ox(r['ok14'])),
            '%s/%s %s' % (_f0(r['lhs15']), _f0(r['rhs15']),
                          _ox(r['ok15'])),
            '%s/%s %s' % (_f0(r['lhs16']), _f0(r['rhs16']),
                          _ox(r['ok16'])),
            _ox(r['ok'])])
    L += _longtable(cols, rows, caption=r'(寸法: mm、$Q_D$: kN。'
                    r'(19.14)欄: 左辺/右辺 kN、(19.15)(19.16)欄: '
                    r'左辺/右辺 kN$\cdot$m)')
    return L + _FRAG_CLOSE


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def export_tex(res, out_dir):
    """3つのTeX断片を out_dir に書き出す。パスの list を返す."""
    os.makedirs(out_dir, exist_ok=True)
    made = []
    frags = [
        ('rcdetail_teichaku.tex', _tex_teichaku(res)),
        ('rcdetail_fuchaku.tex', _tex_fuchaku(res)),
        ('rcdetail_setsugobu.tex', _tex_setsugobu(res)),
    ]
    if res.get('walls'):
        frags.append(('rcdetail_kabewaku.tex', _tex_kabewaku(res)))
    if res.get('openings'):
        frags.append(('rcdetail_kaikou.tex', _tex_kaikou(res)))
    for fname, lines in frags:
        path = os.path.join(out_dir, fname)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\n'.join(lines) + '\n')
        made.append(path)
    return made


def compile_preview(tex_paths, out_dir, notes=None):
    """断片を計算書プリアンブルで包んで platex+dvipdfmx でコンパイルし、
    pdftoppm でPNG化する。(pdf_path, [png_paths]) を返す。
    コンパイル不成立時は notes に理由を積んで (None, []) を返す."""
    notes = notes if notes is not None else []
    drv = os.path.join(out_dir, 'rcdetail_preview.tex')
    L = [_DETAIL_TEX_PREAMBLE]
    for pth in tex_paths:
        L.append('\\input{%s}' % os.path.basename(pth).replace('\\', '/'))
    L.append(r'\end{document}')
    with open(drv, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')

    def _run(cmd):
        return subprocess.run(cmd, cwd=out_dir, capture_output=True,
                              timeout=120)

    try:
        # longtable の列幅確定のため2回まわす
        for _ in range(2):
            cp = _run(['platex', '-kanji=utf8',
                       '-interaction=nonstopmode', '-halt-on-error',
                       os.path.basename(drv)])
            if cp.returncode != 0:
                tail = cp.stdout.decode('utf-8', 'replace')[-1200:]
                notes.append('TeXプレビューのコンパイルに失敗しました '
                             '(platex)。texソースは出力済みです。ログ末尾: '
                             + tail)
                return None, []
        cp = _run(['dvipdfmx', 'rcdetail_preview.dvi'])
        if cp.returncode != 0:
            notes.append('TeXプレビューのPDF変換に失敗しました '
                         '(dvipdfmx)。texソースは出力済みです。')
            return None, []
        pdf = os.path.join(out_dir, 'rcdetail_preview.pdf')
        # 古いプレビュー画像を消してから作り直す
        for f in os.listdir(out_dir):
            if f.startswith('rcdetail_prev-') and f.endswith('.png'):
                try:
                    os.remove(os.path.join(out_dir, f))
                except OSError:
                    pass
        cp = _run(['pdftoppm', '-png', '-r', '110', 'rcdetail_preview.pdf',
                   'rcdetail_prev'])
        if cp.returncode != 0:
            notes.append('プレビュー画像の生成に失敗しました (pdftoppm)。'
                         'PDFは出力済みです。')
            return pdf, []
        pngs = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                      if f.startswith('rcdetail_prev-')
                      and f.endswith('.png'))
        return pdf, pngs
    except FileNotFoundError as e:
        notes.append('TeXコンパイラが見つかりません (%s)。texソースのみ'
                     '出力しました。' % e)
        return None, []
    except subprocess.TimeoutExpired:
        notes.append('TeXコンパイルがタイムアウトしました。texソースは'
                     '出力済みです。')
        return None, []
