# -*- coding: utf-8 -*-
"""鉄骨造露出柱脚の検定のTeX書き出しとプレビューコンパイル.

計算書本文へ \\input で組み込む断片 colbase.tex を出力する。冒頭に
検討方針・検定式 (記号定義・出典つき) を掲載し、続けて柱脚仕様・
告示1456号の仕様規定・回転剛性・許容応力度検定・保有耐力接合等・
基礎コンクリートの破壊防止の各表 (longtable) を置く。
プレビューは rcdetail と同じく計算書プリアンブルで包んでコンパイルする。
"""

import os
import subprocess

from mgtkit.export_tex import _DETAIL_TEX_PREAMBLE
from mgtkit.colbase.calc import ROUTES, AB_TYPES

_DIR = {'H': 'H', 'B': 'B'}
_CASE = {0: 'N=0', 1: '1', 2: '2', 3: '3', 4: '4', 5: '5'}


def _esc(s):
    out = str(s)
    for ch in ('_', '%', '&', '#'):
        out = out.replace(ch, '\\' + ch)
    return out


def _f(v, fmt='%.1f'):
    return '--' if v is None else fmt % v


def _ox(ok):
    if ok is None:
        return '--'
    return 'OK' if ok else r'\textbf{NG}'


def _longtable(cols, rows, caption=None):
    n = len(cols)
    L = [r'\begin{longtable}{%s}' % ('|' + 'c|' * n)]
    if caption:
        L.append(r'\multicolumn{%d}{l}{%s}\\' % (n, caption))
    L += [r'\hline', ' & '.join(cols) + r' \\ \hline', r'\endhead',
          r'\hline \endfoot']
    for r in rows:
        L.append(' & '.join(r) + r' \\')
    L.append(r'\end{longtable}')
    return L


def _policy(res):
    p = res['params']
    route = p['route']
    L = [r'\noindent\textbf{(1) 検討方針}\par\smallskip',
         r'\noindent 露出柱脚は「2025年版 建築物の構造関係技術基準解説書」'
         r'付録1-2.6 柱脚の設計の考え方 (2) 露出型柱脚 (以下「技術基準」) '
         r'に従い、%s として検討する。' % ROUTES[route]]
    if route == '1-1':
        L.append(r'柱脚の回転剛性を考慮した応力に対し、許容応力度の検定'
                 r'のみを行う (技術基準 付図1.2-25 フロー1)。\par')
    else:
        L.append(r'柱脚の回転剛性を考慮した応力に対する許容応力度の検定'
                 r'(フロー1) に加え、一次設計の地震時応力を $\gamma=%.2f$ '
                 r'倍した応力 ($N^*=N_L+\gamma N_E$、$M^*=M_L+\gamma M_E$、'
                 r'$Q^*=Q_L+\gamma Q_E$) により、アンカーボルトの伸び能力の'
                 r'有無に応じて保有耐力接合の判定等 (フロー2〜5) と'
                 r'基礎コンクリートの破壊防止 (フロー6) を確認する。'
                 % p['gamma'])
        if route == '3':
            L.append(r'ルート3では保有耐力接合の判定 (フロー8・11) の結果を'
                     r'保有水平耐力計算における1階の$D_s$の扱い (保有耐力'
                     r'接合でない場合は$D_s$を0.05割増し、伸び能力なしの'
                     r'場合は種別D相当) に反映する。本検討の軸力は崩壊'
                     r'メカニズム時の軸力を$\gamma$倍応力で近似したもの'
                     r'であり、保有水平耐力計算の結果で確認すること。')
        L.append(r'\par')
    L.append(r'\noindent 応力は各柱脚要素の下端 (柱脚側) の値を用い、'
             r'柱断面のせい方向 (H方向: 強軸曲げ$M_y$・せん断$Q_z$) と'
             r'幅方向 (B方向: 弱軸曲げ$M_z$・せん断$Q_y$) をそれぞれ'
             r'一軸曲げとして検定する。軸力$N$は圧縮を正とする。\par'
             r'\medskip')
    return L


def _syms(items):
    """符号の説明を1符号1行で並べる (符号 : 説明 の2列の表).

    items: [(符号 (TeX数式), 説明)]。説明が長ければ列内で折り返す。
    """
    L = [r'\par\noindent\begin{tabular}{@{\hspace{1zw}}l@{\ :\ }'
         r'p{0.86\linewidth}}']
    for sym, desc in items:
        L.append(r'%s & %s \\' % (sym, desc))
    L.append(r'\end{tabular}\par\smallskip')
    return L


def _formulas(res):
    p = res['params']
    route = p['route']
    fts = (r'$\min(f_t,\,1.4f_t-1.6\tau)$ (鋼構造設計規準2005)'
           if p['bolt_method'] != '2019' else
           r'$\sqrt{f_t^2-3\tau^2}$ (鋼構造許容応力度設計規準2019)')
    L = [r'\noindent\textbf{(2) 検定式}\par\smallskip',
         r'\noindent\textbf{回転剛性} (技術基準 (付1.2-20)式)',
         r'\begin{equation*}',
         r'K_{BS}=\frac{E\cdot n_t\cdot A_b\,(d_t+d_c)^2}{2\,l_b}',
         r'\end{equation*}',
         r'\noindent ここに、']
    L += _syms([
        (r'$E$', r'アンカーボルトのヤング係数 ($=2.05\times10^5$ '
                 r'N/mm$^2$)'),
        (r'$n_t$', r'引張側アンカーボルトの本数'),
        (r'$A_b$', r'アンカーボルト1本の軸部断面積'),
        (r'$d_t$', r'柱断面の図心から引張側アンカーボルト群の図心までの距離'),
        (r'$d_c$', r'柱断面の図心から圧縮側の柱フランジ外縁までの距離'),
        (r'$l_b$', r'アンカーボルトの有効長さ'),
    ])
    L += [r'\noindent 計算方向にアンカーボルトがない場合は $n_t$=全本数、'
          r'$d_t=0$ とする。\par\medskip',
          r'\noindent\textbf{許容応力度の検定} (技術基準 付録1-2.6 設計例)',
          r'\begin{equation*}',
          r'\sigma_c\leq f_c,\qquad \sigma_t=T/a_{te}\leq f_{ts},\qquad '
          r'\tau=Q/a_{ge}\leq f_s',
          r'\end{equation*}',
          r'\noindent $e=M/N>D/6+d_{tl}/3$ のとき、中立軸 $x_n$ は次式の'
          r'根とし、$\sigma_c$・$T$ を求める。',
          r'\begin{gather*}',
          r'x_n^3+3\Bigl(e-\frac{D}{2}\Bigr)x_n^2-\frac{6n\,a_t}{b}'
          r'\Bigl(e+\frac{D}{2}-d_{tl}\Bigr)(D-d_{tl}-x_n)=0\\',
          r'\sigma_c=\frac{2N\,(e+D/2-d_{tl})}{b\,x_n\,(D-d_{tl}-x_n/3)},'
          r'\qquad T=\frac{N\,(e-D/2+x_n/3)}{D-d_{tl}-x_n/3}',
          r'\end{gather*}',
          r'\noindent ここに、']
    L += _syms([
        (r'$\sigma_c$', r'ベースプレート下面のコンクリートの圧縮応力度'),
        (r'$f_c$', r'コンクリートの許容圧縮応力度 ($F_c/3$ (長期)、'
                   r'$2F_c/3$ (短期))'),
        (r'$T$', r'引張側アンカーボルト群の引張力'),
        (r'$a_{te}$', r'引張側アンカーボルトのねじ部有効断面積の和'),
        (r'$a_t$', r'引張側アンカーボルトの軸部断面積の和'),
        (r'$a_{ge}$', r'全アンカーボルトのねじ部有効断面積の和'),
        (r'$N$, $M$, $Q$', r'柱脚の軸力 (圧縮を正)・曲げモーメント・'
                           r'せん断力'),
        (r'$e$', r'偏心距離 ($=M/N$)'),
        (r'$D$, $b$', r'ベースプレートのせい (曲げ方向)・幅'),
        (r'$d_{tl}$', r'ベースプレート縁から引張側アンカーボルト芯までの'
                      r'距離'),
        (r'$n$', r'コンクリートに対する鋼材のヤング係数比 (%s)' % (
            '$=%.0f$' % res['specs'][0]['n_ratio']
            if len({sp['n_ratio'] for sp in res['specs']}) == 1
            else r'柱脚の仕様表による') +
         r'。鉄筋コンクリート構造計算規準の値 ($F_c\leq27$: 15、'
         r'$\leq36$: 13、$\leq48$: 11、$\leq60$: 9) を標準とする'),
        (r'$f_t$, $f_s$', r'アンカーボルトの許容引張・せん断応力度 '
                          r'($F/1.5$・$F/(1.5\sqrt{3})$ (長期)、短期は'
                          r'1.5倍)'),
        (r'$f_{ts}$', r'せん断力を同時に受けるときの許容引張応力度 '
                      r'($=$%s)' % fts),
    ])
    L += [r'\noindent 全面圧縮 ($e\leq D/6$)、アンカーボルトに引張が生じ'
          r'ない場合、引張軸力で両側のアンカーボルトが引張となる場合も、'
          r'同じ仮定 (平面保持、コンクリートの引張とアンカーボルトの圧縮'
          r'を無視) による弾性解で求める。せん断力は摩擦 '
          r'$Q_A=0.4\,(T+N)$ で負担できる場合はアンカーボルトに負担させ'
          r'ない。\par']
    if p.get('do_bp'):
        L += [r'\noindent ベースプレートはリブのない片持ち板とし、次式で'
              r'検定する (H形鋼柱は強軸方向のみ)。',
              r'\begin{equation*}',
              r"\sigma_b=\frac{M_b}{t^2/6}\leq f_b',\qquad "
              r'M_b\ (\text{引張側})=\frac{P\,L}{2L+d}',
              r'\end{equation*}',
              r'\noindent ここに、']
        L += _syms([
            (r'$M_b$', r'単位幅あたりの曲げモーメント (圧縮側は柱外縁位置、'
                       r'反力 $w=\sigma_c$ の片持ち板)'),
            (r'$t$', r'ベースプレートの厚さ'),
            (r"$f_b'$", r'ベースプレートの許容曲げ応力度 ($F/1.3$ (長期)、'
                        r'短期は1.5倍)'),
            (r'$P$', r'アンカーボルト1本の引張力 ($=T/n_t$)'),
            (r'$L$', r'アンカーボルト芯から柱面までの距離'),
            (r'$d$', r'アンカーボルトの径 (45°分散の有効幅 $2L+d$)'),
        ])
    L.append(r'\medskip')
    if route == '1-1':
        return L
    L += [
        r'\noindent\textbf{最大耐力} (技術基準 (付1.2-31)〜(付1.2-41)式)',
        r'\begin{gather*}',
        r'N_u=0.85\,B\,D\,F_c,\qquad T_u=n_t\,A_b\,F,\qquad '
        r'S_u=n_t\,A_b\,F/\sqrt{3}\\',
        r'M_u=\begin{cases}(N_u-N)\,d_t & (N_u\geq N>N_u-T_u)\\'
        r'T_u\,d_t+(N+T_u)\dfrac{D}{2}\Bigl(1-\dfrac{N+T_u}{N_u}\Bigr)'
        r' & (N_u-T_u\geq N>-T_u)\\'
        r'(N+2T_u)\,d_t & (-T_u\geq N>-2T_u)\end{cases}\\',
        r'Q_u=\max(Q_{fu},\,Q_{bu})\\',
        r'Q_{fu}=0.5N_u,\ \ 0.5(N+T_u),\ \ 0,\qquad '
        r'Q_{bu}=S_u\Bigl\{1+\sqrt{1-\Bigl(\frac{N_u-N}{T_u}\Bigr)^2}\Bigr\},'
        r'\ \ S_u,\ \ S_u\sqrt{1-\Bigl(-\frac{N}{T_u}-1\Bigr)^2}',
        r'\end{gather*}',
        r'\noindent ここに ($Q_{fu}$・$Q_{bu}$ の軸力の範囲は $M_u$ と'
        r'同じ順)、']
    L += _syms([
        (r'$N_u$', r'基礎コンクリートの最大圧縮耐力'),
        (r'$T_u$', r'引張側アンカーボルトの最大引張耐力'),
        (r'$S_u$', r'引張側アンカーボルトの最大せん断耐力'),
        (r'$Q_{fu}$', r'摩擦による最大せん断耐力'),
        (r'$Q_{bu}$', r'アンカーボルトによる最大せん断耐力'),
        (r'$B$, $D$', r'ベースプレートの幅・せい'),
        (r'$F$', r'アンカーボルトのF値'),
        (r'$d_t$', r'$=D/2-d_{tl}$'),
    ])
    L += [
        r'\medskip',
        r'\noindent\textbf{伸び能力あり} (フロー3・4)',
        r'\begin{gather*}',
        r'\text{保有耐力接合: } M_u>\alpha\,M_{pc}\ \text{(付1.2-21)},\qquad '
        r'Q_u>Q^*\ \text{(付1.2-22)}\\',
        r'\text{保有耐力接合でない場合: } M_u>M^*',
        r'\end{gather*}',
        r'\noindent ここに、']
    L += _syms([
        (r'$M_{pc}$', r'軸力 $N^*$ を考慮した柱の全塑性モーメント'
                      r'\newline 角形鋼管・H形鋼強軸: $N/N_y>A_w/2A$ のとき '
                      r'$\frac{2A}{2A-A_w}(1-N/N_y)M_p$'
                      r'\newline H形鋼弱軸: $N/N_y>A_w/A$ のとき '
                      r'$\{1-((N-A_w\sigma_y)/(N_y-A_w\sigma_y))^2\}M_p$'
                      r'\newline 円形鋼管: $\cos(\frac{\pi}{2}N/N_y)M_p$'),
        (r'$\alpha$', r'保有耐力接合の安全率 (400N級 1.3、490N級 1.2。'
                      r'付表1.2-2 仕口部)'),
        (r'$M^*$, $Q^*$', r'$\gamma$ 倍した地震時応力による曲げモーメント・'
                          r'せん断力'),
    ])
    L += [
        r'\noindent $Q_u>Q^*$ は常に満たす必要がある。\par\medskip',
        r'\noindent\textbf{伸び能力なし} (フロー5)',
        r'\begin{equation*}',
        r'\sigma_c<F_c\ \text{(付1.2-23)},\qquad '
        r'T<P_b=n_t\,a_e\,F\ \text{(付1.2-24)},\qquad '
        r'Q_y=\max(0.4(T+N^*),\ {}_bQ_A)>Q^*',
        r'\end{equation*}',
        r'\noindent ここに、']
    L += _syms([
        (r'$\sigma_c$, $T$', r'$N^*$・$M^*$ から許容応力度の検定と同じ方法'
                             r'で求めた値'),
        (r'$P_b$', r'引張側アンカーボルトのねじ部降伏耐力'),
        (r'$a_e$', r'アンカーボルト1本のねじ部有効断面積'),
        (r'${}_bQ_A$', r'アンカーボルトのせん断耐力 (引張との組合せを考慮し、'
                       r'$f_t=F$ として許容応力度の検定と同じ組合せ式で'
                       r'求める)'),
        (r'$M_y$', r'ルート3の保有耐力接合の判定に用いる降伏曲げ耐力 '
                   r'$n_t\,a_e\,F\,(d_t+d_c)+N\,d_c$ (付1.2-46)。'
                   r'$M_u$ との小さい方を用いる'),
    ])
    L += [
        r'\medskip',
        r'\noindent\textbf{基礎コンクリートの破壊防止} (フロー6)',
        r'\begin{gather*}',
        r'\frac{C_y}{2B_2X}<F_c\ \text{(付1.2-25)},\qquad '
        r'\frac{C_y}{B_0}<\frac{F_c}{3}\ \text{(付1.2-27)},\qquad '
        r'C_y=n_t\,A_b\,F+N^*\ \text{(付1.2-26)}\\',
        r'e>0.54\sqrt{F/{}_c\sigma_t}\;d,\quad {}_c\sigma_t=0.31\sqrt{F_c}'
        r'\ \text{(付1.2-29)}',
        r'\end{gather*}',
        r'\noindent ここに、']
    L += _syms([
        (r'$B_2$', r'基礎柱形の幅'),
        (r'$X$', r'ベースプレート端から柱形端までの距離'),
        (r'$B_0$', r'ベースプレートの底面積'),
        (r'$e$', r'アンカーボルト芯から柱形縁までの距離'),
        (r'${}_c\sigma_t$', r'コンクリートの引張強度'),
    ])
    L += [r'\noindent 端部のせん断による剥落の検討は、アンカーボルトが'
          r'せん断力を負担する場合 ($Q_{bu}>Q_{fu}$ 等) に限る。'
          r'\par\medskip']
    return L


def _spec_table(res):
    cols = ['グループ', 'BP $D\\times B\\times t$', 'AB', '本数',
            '$n_t$(H/B)', '$d_{tl}$(H/B)', '$A_b$/$a_e$', '伸び',
            '$l_b$', '$F_c$', '$n$', '柱形$D\\times B$']
    rows = []
    for s in res['specs']:
        rows.append([
            _esc(s['group']),
            '%.0f$\\times$%.0f$\\times$%.0f' % (s['bp_D'], s['bp_B'],
                                                  s['bp_t']),
            '%s M%.0f' % (_esc(s['ab_type']), s['ab_d']),
            '%d' % s['n_all'],
            '%d/%d' % (s['nt_H'], s['nt_B']),
            '%.0f/%.0f' % (s['dtl_H'], s['dtl_B']),
            '%.0f/%.0f' % (s['ab_Ab'], s['ab_Abe']),
            'あり' if s['ductile'] else 'なし',
            '%.0f' % s['lb'], '%.0f' % s['Fc'], '%.0f' % s['n_ratio'],
            ('%.0f$\\times$%.0f' % (s['fnd_D'], s['fnd_B'])
             if s.get('fnd_D') and s.get('fnd_B') else '--')])
    L = [r'\noindent\textbf{(3) 柱脚の仕様}\par\smallskip']
    L += _longtable(cols, rows, caption=r'(単位: mm、mm$^2$、N/mm$^2$。'
                    r'H: 柱せい方向、B: 柱幅方向。BP F値=%s)'
                    % '・'.join(sorted({'%.0f' % s['bp_F']
                                         for s in res['specs']})))
    L.append(r'\noindent アンカーボルトのF値: ' + '、'.join(
        '%s %.0f' % (_esc(k), AB_TYPES[k][0]) for k in
        sorted({s['ab_type'] for s in res['specs']}) if k in AB_TYPES)
        + r' N/mm$^2$。\par\medskip')
    return L


def _fig_section(res):
    r"""(3) の続き: 入力した仕様に基づく仕様図 (グループごと、TikZ).

    計算書側のプリアンブルに \usepackage{tikz} が必要 (図のファイルは
    使わないので、colbase.tex だけで完結する)。
    """
    figs = res.get('figs') or []
    if not figs:
        return []
    from mgtkit.colbase.tikz import spec_tikz, COLORS
    L = [r'\noindent\textbf{柱脚の仕様図} (入力した仕様に基づく簡易図。'
         r'代表柱は各グループで断面積が最大の柱)\par\smallskip']
    L += COLORS
    for f in figs:
        L += spec_tikz(f['spec'], f['colobj'])
    L.append(r'\noindent 図中の寸法は mm。オレンジの枠は引張側の列 '
             r'(H方向・B方向)、破線円は孔径、断面図の破線は定着から 45° '
             r'に広がるコーン状破壊面を示す。\par\medskip')
    return L


def _kokuji_table(res):
    if not res.get('kokuji'):
        return []
    L = [r'\noindent\textbf{(4) 仕様規定 (平12建告第1456号 第1第一号)}'
         r'\par\smallskip']
    rows = []
    for k in res['kokuji']:
        for it in k['items']:
            rows.append([_esc(k['group']), _esc(k['col']), it['item'],
                         it['req'].replace('≧', '$\\geq$')
                         .replace('≦', '$\\leq$'),
                         it['have'],
                         {'OK': 'OK', 'NG': r'\textbf{NG}'}.get(
                             it['mark'], '--'),
                         {'令82条': '適用除外 (令82条)',
                          'ただし書き': '適用除外 (ただし書き)'}.get(
                             it['basis'], '--')])
    L += _longtable(['グループ', '柱', '項目', '規定', '設計', '判定',
                     '適用'], rows)
    L.append(r'\noindent イ・ニ・ホ・ヘは令82条一号〜三号の計算 (許容応力度'
             r'計算) を行うことで適用除外となる (技術基準 3.6.4)。'
             r'ロ (座金・二重ナット等の戻り止め) は図面で確認する。')
    if any(it['basis'] == 'ただし書き' for k in res['kokuji']
           for it in k['items']):
        L.append(r'ハ (定着) は、(9) の定着部のコーン状破壊の検討により'
                 r'アンカーボルトの抜け出し及びコンクリートの破壊が生じない'
                 r'ことを確かめたため、ハのただし書きにより適用除外とする。')
    else:
        L.append(r'ハ (定着) は許容応力度計算を行っても適用除外とならない '
                 r'(ただし書きによる場合を除く)。')
    L.append(r'判定欄の「--」は適用除外のため判定の対象としない項目。'
             r'\par\medskip')
    return L


def _kbs_table(res):
    seen = {}
    for r in res['rows']:
        key = (r['group'], r['sec'], r['dir'])
        if key not in seen:
            seen[key] = r
    rows = [[_esc(g), _esc(s), d, '%d' % r['kbs']['nt'],
             _f(r['kbs']['dt'], '%.0f'), _f(r['kbs']['dc'], '%.0f'),
             '{:,.0f}'.format(r['kbs']['K'])]
            for (g, s, d), r in seen.items()]
    L = [r'\noindent\textbf{(5) 柱脚の回転剛性}\par\smallskip']
    L += _longtable(['グループ', '柱断面', '方向', '$n_t$', '$d_t$ (mm)',
                     '$d_c$ (mm)', '$K_{BS}$ (kN$\\cdot$m/rad)'], rows)
    L.append(r'\noindent 応力解析では柱脚の回転ばねとしてこの値を用いる'
             r'こと (解析モデルの柱脚ばね値と一致していることを確認する)。'
             r'\par\medskip')
    return L


def _allow_table(res):
    do_bp = res['params'].get('do_bp')
    cols = ['グループ', '要素', '方向', 'ケース', '$N$', '$M$',
            '$Q$', '状態', '$x_n$', '$\\sigma_c/f_c$',
            '$\\sigma_t/f_{ts}$', '$\\tau/f_s$']
    if do_bp:
        cols.append('$\\sigma_b/f_b\'$')
    cols.append('判定')
    rows = []
    for r in res['rows']:
        a = r['allow']
        if a is None:
            continue
        st, bt = a['st'], a['bolt']
        row = [_esc(r['group']), '%d' % r['ele'], r['dir'],
               _esc(a['case']), _f(a['N'] / 1e3), _f(a['M'] / 1e6),
               _f(a['Q'] / 1e3), _CASE[st['case']],
               _f(st['xn'], '%.0f'), '%.2f' % a['r_c'],
               '%.2f' % bt['r_t'],
               ('%.2f' % bt['r_s']) if not bt['fric'] else '摩擦']
        if do_bp:
            row.append('%.2f' % a['bp']['r'] if 'bp' in a else '--')
        row.append(_ox(a['r'] <= 1.0 + 1e-9))
        rows.append(row)
    L = [r'\noindent\textbf{(6) 許容応力度の検定} (各柱脚・方向で検定比'
         r'最大のケース)\par\smallskip']
    L += _longtable(cols, rows, caption=r'(単位: kN、kN$\cdot$m、mm)')
    L.append(r'\noindent 状態: 応力状態の区分 (1: 全面圧縮、'
             r'2: アンカーボルトの引張なし、3: 引張側アンカーボルトのみ引張、'
             r'4: 両側のアンカーボルトが引張、5: 圧縮なし)。'
             r'$\tau/f_s$ 欄の「摩擦」は $Q\leq 0.4(T+N)$ でアンカーボルトが'
             r'せん断力を負担しないもの。\par\medskip')
    return L


def _ult_table(res):
    route = res['params']['route']
    duct = [r for r in res['rows']
            if r['ult'] is not None and 'Mpc' in r['ult']
            and 'st' not in r['ult']]
    brit = [r for r in res['rows']
            if r['ult'] is not None and 'st' in r['ult']]
    L = []
    if not duct and not brit:
        return L
    L.append(r'\noindent\textbf{(7) 保有耐力接合の判定等} ($\gamma=%.2f$、'
             r'各柱脚・方向で検定比最大のケース)\par\smallskip'
             % res['params']['gamma'])
    if duct:
        cols = ['グループ', '要素', '方向', 'ケース', '$N^*$', '$M^*$',
                '$Q^*$', '$M_u$', '$\\alpha M_{pc}$', '保有耐力接合',
                '$M^*/M_u$', '$Q_u$', '$Q^*/Q_u$', '判定']
        rows = []
        for r in duct:
            u = r['ult']
            rows.append([
                _esc(r['group']), '%d' % r['ele'], r['dir'], _esc(u['case']),
                _f(u['N'] / 1e3), _f(u['M'] / 1e6), _f(u['Q'] / 1e3),
                _f(u['u']['Mu'] / 1e6), _f(u['alpha'] * u['Mpc'] / 1e6),
                '○' if u['hoyu'] else '×',
                '--' if u['hoyu'] else '%.2f' % u['r_m'],
                _f(u['u']['Qu'] / 1e3), '%.2f' % u['r_q'], _ox(u['ok'])])
        L += _longtable(cols, rows, caption=r'伸び能力あり (単位: kN、'
                        r'kN$\cdot$m。保有耐力接合 ○: $M_u>\alpha M_{pc}$。'
                        r'×の場合は $M_u>M^*$ で確認)')
        if route == '3':
            ng = sorted({'%s(%d)' % (_esc(r['group']), r['ele'])
                         for r in duct if not r['ult']['hoyu']})
            L.append(r'\noindent 保有耐力接合とならない柱脚 '
                     r'(1階の$D_s$を0.05割増す): %s\par'
                     % ('、'.join(ng) if ng else 'なし'))
    if brit:
        cols = ['グループ', '要素', '方向', 'ケース', '$N^*$', '$M^*$',
                '$Q^*$', '$\\sigma_c/F_c$', '$T/P_b$', '$Q_y$',
                '$Q^*/Q_y$', '判定']
        rows = []
        for r in brit:
            u = r['ult']
            rows.append([
                _esc(r['group']), '%d' % r['ele'], r['dir'], _esc(u['case']),
                _f(u['N'] / 1e3), _f(u['M'] / 1e6), _f(u['Q'] / 1e3),
                '%.2f' % (u['st']['sc'] / res_fc(res, r)),
                '%.2f' % (u['st']['T'] / u['Pb']),
                _f(u['Qy'] / 1e3),
                '%.2f' % (u['Q'] / u['Qy'] if u['Qy'] > 0 else 99.9),
                _ox(u['ok'])])
        L += _longtable(cols, rows, caption=r'伸び能力なし (単位: kN、'
                        r'kN$\cdot$m)')
    L.append(r'\medskip')
    return L


def res_fc(res, r):
    for s in res['specs']:
        if s['group'] == r['group']:
            return s['Fc']
    return 1.0


def _fnd_table(res):
    rows = []
    for r in res['rows']:
        f = r['fnd']
        if not f or f.get('cone_only'):
            continue
        e_txt = '--'
        if 'e_req' in f:
            e_txt = ('%.0f/%.0f %s' % (f['e_have'], f['e_req'],
                                       'OK' if f['ok_e'] else 'NG')
                     if f['shear_by_bolt'] else '省略')
        oks = [f['ok_b']] + ([f['ok_a']] if 'ok_a' in f else []) + \
              ([f['ok_e']] if 'ok_e' in f and f['shear_by_bolt'] else [])
        rows.append([_esc(r['group']), '%d' % r['ele'], r['dir'],
                     _esc(f['case']), _f(f['cy'] / 1e3),
                     _f(f.get('c1'), '%.2f'), _f(f['c2'], '%.2f'),
                     _f(f['c2a'], '%.2f'), e_txt, _ox(all(oks))])
    if not rows:
        return []
    L = [r'\noindent\textbf{(8) 基礎コンクリートの破壊防止}\par\smallskip']
    L += _longtable(['グループ', '要素', '方向', 'ケース', '$C_y$',
                     '$C_y/2B_2X$', '$C_y/B_0$', '$F_c/3$',
                     '$e$/必要$e$', '判定'], rows,
                    caption=r'(単位: kN、N/mm$^2$、mm。$C_y/2B_2X<F_c$、'
                    r'柱形寸法未入力の欄は--)')
    return L + _cone_table(res)


def _cone_table(res):
    """コーン状破壊 (技術基準 (付1.2-30)・付図1.2-35/37)."""
    rows = []
    for r in res['rows']:
        f = r['fnd'] or {}
        ct, cs = f.get('cone_t'), f.get('cone_s')
        if not ct and not cs:
            continue
        if ct:
            t = ['%.0f' % (ct['Ac'] / 1e3), _f(ct['Tp'] / 1e3),
                 '省略' if ct['skip'] else _f(ct['Tu'] / 1e3),
                 '--' if ct['skip'] else _ox(ct['ok'])]
        else:
            t = (['\\multicolumn{4}{c|}{対象外 (引張側の列なし)}']
                 if f.get('cone_t_na') == 'no_row' else ['--'] * 4)
        if cs:
            s = ['%.0f' % cs['c'], '%.0f' % (cs['Acv'] / 1e3),
                 _f(cs['Qc'] / 1e3),
                 '省略' if cs['skip'] else _f(cs['QD'] / 1e3),
                 '--' if cs['skip'] else _ox(cs['ok'])]
        else:
            s = ['--'] * 5
        rows.append([_esc(r['group']), '%d' % r['ele'], r['dir']] + t + s)
    if not rows:
        return [r'\noindent アンカーボルトのコーン状破壊は、基礎柱形の寸法・'
                r'定着長さ・定着金物寸法が未入力のため検討していない。\par']
    L = [r'\medskip',
         r'\noindent\textbf{(9) コーン状破壊} (技術基準 (付1.2-30)式、'
         r'付図1.2-35・1.2-37)\par\smallskip',
         r'\begin{equation*}',
         r'T_u<T_p=0.31\,\phi_1\sqrt{F_c}\,A_c,\qquad '
         r'Q_D<Q_c=0.31\,\phi_1\sqrt{F_c}\,A_{cv},\qquad \phi_1=0.6',
         r'\end{equation*}',
         r'\noindent ここに、']
    L += _syms([
        (r'$T_u$', r'引張側アンカーボルトの軸部の引張降伏耐力 '
                   r'($=n_t\,A_b\,F$)'),
        (r'$A_c$', r'定着のコーン状破壊面の有効水平投影面積。定着金物 '
                   r'(頭部) から水平距離 $l_a$ (定着長さ) 以内の範囲を基礎'
                   r'柱形の上面で切り取り、定着金物の面積を除いたもの (45°の'
                   r'コーン。柱形の外への広がりは安全側に無視する)'),
        (r'$A_{cv}$', r'列状せん断のコーン状破壊の有効投影面積。せん断方向の'
                      r'縁に最も近いアンカーボルト列の各ボルトから柱形側面'
                      r'までの距離 $c$ を半径とする半円 (側面上) の和集合を、'
                      r'側面の幅と立上り高さで切り取ったもの'),
        (r'$Q_D$', r'$\gamma$ 倍したせん断力の最大'),
        (r'$\phi_1$', r'低減係数 (短期)'),
    ])
    L += [r'\noindent アンカーボルトに引張が生じない場合は定着の、'
          r'アンカーボルトがせん断力を負担しない場合 ($Q_{fu}\geq Q_{bu}$、'
          r'伸び能力なしは摩擦$\geq{}_bQ_A$) は列状せん断の検討を省略する。'
          r'\par\smallskip']
    L += _longtable(['グループ', '要素', '方向', '$A_c$', '$T_p$', '$T_u$',
                     '判定', '$c$', '$A_{cv}$', '$Q_c$', '$Q_D$', '判定'],
                    rows, caption=r'(単位: kN、mm。$A_c$・$A_{cv}$: '
                    r'$\times10^3$mm$^2$)')
    specs = []
    for s in res['specs']:
        if not s.get('la'):
            continue
        if s['anc_type'] == 'hook':
            specs.append('%s: フック (定着金物なしとしてボルト芯から半径'
                         '$l_a$の範囲で算定), $l_a$=%.0f'
                         % (_esc(s['group']), s['la']))
        elif s.get('anc_Dp'):
            specs.append('%s: %s $D_p$=%.0f, $l_a$=%.0f' % (
                _esc(s['group']),
                '連結アンカープレート (幅$D_p$)' if s['anc_type'] == 'plate'
                else '個別の定着金物 ($D_p$角)', s['anc_Dp'], s['la']))
    if specs:
        L.append(r'\noindent 定着: ' + '、'.join(specs) + r'。\par')
    return L


def export_tex(res, out_dir):
    """TeX断片 colbase.tex を書き出す。パスの list を返す."""
    os.makedirs(out_dir, exist_ok=True)
    title = '鉄骨露出柱脚の検討'
    if res.get('project'):
        title += ' (' + _esc(res['project']) + ')'
    L = ['% mgtkit colbase — 鉄骨露出柱脚の検討 (本文へ \\input で組込む)',
         r'\onecolumn', r'\footnotesize', r'\setlength{\tabcolsep}{3pt}',
         r'\noindent\textbf{%s}\par\medskip' % title]
    L += _policy(res) + _formulas(res) + _spec_table(res) + \
        _fig_section(res) + \
        _kokuji_table(res) + _kbs_table(res) + _allow_table(res) + \
        _ult_table(res) + _fnd_table(res)
    L += [r'\setlength{\tabcolsep}{6pt}', r'\normalsize', '']
    path = os.path.join(out_dir, 'colbase.tex')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')
    return [path]


def compile_preview(tex_paths, out_dir, notes=None):
    """断片を計算書プリアンブルで包んで platex+dvipdfmx でコンパイルし、
    pdftoppm でPNG化する (rcdetail.tex.compile_preview と同じ手順)."""
    notes = notes if notes is not None else []
    drv = os.path.join(out_dir, 'colbase_preview.tex')
    # 仕様図を TikZ で描くのでプレビューでも tikz を読む (計算書側の
    # プリアンブルには元から \usepackage{tikz} がある前提)
    pre = _DETAIL_TEX_PREAMBLE
    if 'tikz' not in pre:
        pre = pre.replace(r'\begin{document}',
                          '\\usepackage{tikz}\n\\begin{document}', 1)
    L = [pre]
    for pth in tex_paths:
        L.append('\\input{%s}' % os.path.basename(pth))
    L.append(r'\end{document}')
    with open(drv, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L) + '\n')

    def _run(cmd):
        return subprocess.run(cmd, cwd=out_dir, capture_output=True,
                              timeout=120)
    try:
        for _ in range(2):
            cp = _run(['platex', '-kanji=utf8', '-interaction=nonstopmode',
                       '-halt-on-error', os.path.basename(drv)])
            if cp.returncode != 0:
                tail = cp.stdout.decode('utf-8', 'replace')[-1200:]
                notes.append('TeXプレビューのコンパイルに失敗しました '
                             '(platex)。texソースは出力済みです。ログ末尾: '
                             + tail)
                return None, []
        cp = _run(['dvipdfmx', 'colbase_preview.dvi'])
        if cp.returncode != 0:
            notes.append('TeXプレビューのPDF変換に失敗しました (dvipdfmx)。'
                         'texソースは出力済みです。')
            return None, []
        pdf = os.path.join(out_dir, 'colbase_preview.pdf')
        for f in os.listdir(out_dir):
            if f.startswith('colbase_prev-') and f.endswith('.png'):
                try:
                    os.remove(os.path.join(out_dir, f))
                except OSError:
                    pass
        cp = _run(['pdftoppm', '-png', '-r', '110', 'colbase_preview.pdf',
                   'colbase_prev'])
        if cp.returncode != 0:
            notes.append('プレビュー画像の生成に失敗しました (pdftoppm)。'
                         'PDFは出力済みです。')
            return pdf, []
        pngs = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                      if f.startswith('colbase_prev-') and f.endswith('.png'))
        return pdf, pngs
    except FileNotFoundError as e:
        notes.append('TeXコンパイラが見つかりません (%s)。texソースのみ'
                     '出力しました。' % e)
        return None, []
    except subprocess.TimeoutExpired:
        notes.append('TeXコンパイルがタイムアウトしました。texソースは'
                     '出力済みです。')
        return None, []
