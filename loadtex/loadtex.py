# -*- coding: utf-8 -*-
"""荷重表 xls -> 計算書(荷重外力)用TeX断片 + 風・地震力の計算 (Matlab不要版).

従来の Excel マクロ ``MacroXLStoTex`` (Matlab Excel Link 経由で
``MatlabT\\privatetool_function\\TeX`` の DRAFT_TeX / SELF_TeX / DL_TeX / LL_TeX /
SNOW_TeX / W_TeX / Weight_TeX / Weight_F_TeX / C_i / LOAD_TeX / loadxls2tex を
呼んでいた) と同じ処理を Python に移植したもの。

  1. 荷重表の名前付き範囲を Excel から読む (COM)。
  2. 風圧力 (W_TeX) と地震力 (C_i) を計算し、結果をセルへ書き戻す
     (風: W_output = Gf,kz,Cf,Er,E,q / 地震力: Qs_Ai, Qs_Ci, Qs_Qi)。
  3. 各表のTeXソースを excel2tex.txt (UTF-8) に区切り線付きで書き出す。
     基礎設計用の Weight_F.txt も同時に書き出す。

使い方:
    python loadtex.py "08_荷重表_xxx.xls" [--out 出力フォルダ] [--no-writeback]

出力先の既定は xls と同じフォルダ。

Matlab版との差異 (意図的):
  * 空白セルは Matlab 版では数値0として渡され、文字列に連結されると NUL 文字が混入していた。
    本版では空文字にする (数値欄は従来どおり 0 と表示)。
  * 固定荷重表(DL_TeX)は Matlab 版だと各範囲の最終行が出力されなかった。本版は最終行まで出力する。
"""

import argparse
import math
import os
import sys

SEP = '-------------------------------------'


# ---------------------------------------------------------------- 書式

def _s(x):
    """Matlab の文字連結相当: 空白は空文字、数値は整数なら整数表記。"""
    if x is None:
        return ''
    if isinstance(x, float) and x == int(x):
        return str(int(x))
    return str(x)


def nf(x, spec):
    """num2str(x, '%15.<spec>f') 相当 (前後の空白は除去)。
    文字列はそのまま返し (Matlab は数値書式に文字を渡すと文字をそのまま出す)、
    空白セルは Matlab 版と同じく 0 として扱う。"""
    if x is None:
        x = 0.0
    if isinstance(x, str):
        return x
    if isinstance(x, float) and math.isnan(x):
        return 'NaN'
    return ('%' + spec + 'f') % x


def n0(x):
    return nf(x, '.0')


def n1(x):
    return nf(x, '.1')


def n2(x):
    return nf(x, '.2')


def isblank(x):
    return x is None or x == '' or (isinstance(x, float) and x == 0.0)


# ---------------------------------------------------------------- 計算

ROMAN = {1: 'I', 2: 'I\\hspace{-1pt}I', 3: 'I\\hspace{-1pt}I\\hspace{-1pt}I',
         4: 'I\\hspace{-1pt}V', 5: 'V', 6: 'V\\hspace{-1pt}I',
         7: 'V\\hspace{-1pt}I\\hspace{-1pt}I',
         8: 'V\\hspace{-1pt}I\\hspace{-1pt}I\\hspace{-1pt}I',
         9: 'I\\hspace{-1pt}X', 10: 'X'}


def calc_wind(G, Vo, H, W):
    """風圧力 (W_TeX.m)。G=地表面粗度区分(1-4), Vo=基準風速, H=建物高さ, W=建物幅.
    戻り値は辞書 (Gf,kz,Cf,Er,E,q,Qw と、下限処理後の H)。"""
    G = int(G)
    if not 1 <= G <= 4:
        raise ValueError('地表面粗度区分Gは1〜4: %r' % G)
    alph = (0.10, 0.15, 0.20, 0.27)[G - 1]
    Zb = (5, 5, 5, 10)[G - 1]
    Zg = (250, 350, 450, 550)[G - 1]
    if H <= Zb:
        H = Zb
    g1, g2 = ((2.0, 1.8), (2.2, 2.0), (2.5, 2.1), (3.1, 2.3))[G - 1]
    if H < 10:
        Gf = g1
    elif H > 40:
        Gf = g2
    else:
        Gf = g2 * (H - 10) / 30 + g1 * (40 - H) / 30
    kz = (max(Zb, H) / H) ** (2 * alph) if H > Zb else 1.0
    Cf = 0.8 * kz + 0.4
    Er = 1.7 * (H / Zg) ** alph
    E = Er ** 2 * Gf
    q = 0.6 * E * Vo ** 2
    Qw = Cf * q / 1000 * H * W
    return dict(G=G, Vo=Vo, H=H, W=W, Gf=Gf, kz=kz, Cf=Cf, Er=Er, E=E, q=q, Qw=Qw)


def calc_ci(Z, G_class, h, alph, Co, wi):
    """Ai分布に基づく層せん断力 (C_i.m)。wi=[1F/基礎, 2F, ..., RF] の重量。"""
    G_class = int(G_class)
    try:
        Tc = {1: 0.4, 2: 0.6, 3: 0.8}[G_class]
    except KeyError:
        raise ValueError('地盤種別G_classは1〜3: %r' % G_class)
    T = h * (0.02 + 0.01 * alph)
    if T < Tc:
        Rt = 1.0
    elif T < 2 * Tc:
        Rt = 1 - 0.2 * (T / Tc - 1) ** 2
    else:
        Rt = 1.6 * Tc / T
    n = len(wi)
    W = sum(wi[1:])
    alph_i = [1.0] + [sum(wi[i:]) / W for i in range(1, n)]
    wwi = [wi[0]] + [sum(wi[i:]) for i in range(1, n)]
    Ai = [1. + (1. / math.sqrt(a) - a) * 2 * T / (1 + 3. * T) for a in alph_i]
    Ai[0] = 0.5
    Ci = [Z * Rt * a * Co for a in Ai]
    Qi = [w * c for w, c in zip(wwi, Ci)]
    Pi = list(Qi)
    for i in range(1, n - 1):
        Pi[i] = Qi[i] - Qi[i + 1]
    return dict(T=T, Rt=Rt, Ai=Ai, Ci=Ci, Qi=Qi, Pi=Pi)


# ---------------------------------------------------------------- 範囲処理

def _col(rng, c=0):
    return [r[c] for r in rng]


def _first_zero(ids):
    """find_zero_index.m: ID列で最初の 0 (または空白) の位置 (0始まり)。"""
    for i, v in enumerate(ids):
        if v is None or (not isinstance(v, str) and v == 0):
            return i
    raise ValueError('概要のB列(荷重ID)に区切り行(0)がありません')


# ---------------------------------------------------------------- TeX

def tex_draft(LOAD_ID, LOAD_NAME, LOAD_table, fin_id, fin_name, ll_id):
    """DRAFT_TeX.m: 設計用荷重一覧表。"""
    n = len(LOAD_ID)
    z = _first_zero(LOAD_ID)

    def row(i, tail):
        t = LOAD_table[i]
        return (n0(LOAD_ID[i]) + ' & ' + _s(LOAD_NAME[i]) +
                ' & ' + n0(fin_id[i]) + ' & ' + _s(fin_name[i]) +
                ' & ' + n0(ll_id[i]) +
                ' & ' + n2(t[0]) + ' & ' + n2(t[1]) + ' & ' + n2(t[2]) +
                ' & ' + n2(t[3]) + ' & ' + n1(t[4]) + tail)

    out = [n0(LOAD_ID[0]) + ' & ' + _s(LOAD_NAME[0]) +
           r' & - & - & - & - & - & - & - & - \\ \hline']
    for i in list(range(1, z)) + list(range(z + 1, n - 1)):
        out.append(row(i, r' \\ \hline'))
    out.append(row(n - 1, r' \\ \bottomrule'))
    return out


def tex_load(LOAD_ID, LOAD_NAME, LOAD_table):
    """LOAD_TeX.m: 荷重ID・DL・LL2 の簡易表。"""
    n = len(LOAD_ID)
    z = _first_zero(LOAD_ID)

    def row(i, tail):
        return (n0(LOAD_ID[i]) + ' & ' + _s(LOAD_NAME[i]) +
                ' & ' + n2(LOAD_table[i][0]) + ' & ' + n2(LOAD_table[i][2]) + tail)

    out = [n0(LOAD_ID[0]) + ' & ' + _s(LOAD_NAME[0]) + r' & - & - \\ \hline']
    for i in list(range(1, z)) + list(range(z + 1, n - 1)):
        out.append(row(i, r' \\ \hline'))
    out.append(row(n - 1, r' \\ \bottomrule'))
    return out


def tex_dl(DL_ID, DL_table, out):
    """DL_TeX.m: 固定荷重表。out に追記して返す (F/S/W の3範囲で累積)。
    Matlab版は最終行を出力しなかったが、本版は範囲の最終行まで出力する。"""
    n = len(DL_ID)
    starts = [i for i, v in enumerate(DL_ID) if not isblank(v)]
    bounds = starts + [n]
    for k in range(len(starts)):
        i = starts[k]
        t = DL_table[i]
        out.append(n0(DL_ID[i]) + ' & ' + _s(t[0]) + ' & ' + _s(t[1]) +
                   ' & ' + n2(t[2]) + ' & ' + _s(t[3]) + ' & ' + n0(t[4]) +
                   ' & ' + n1(t[5]) + ' & ' + n0(t[6]) + r'\\')
        if i + 1 < bounds[k + 1]:
            t = DL_table[i + 1]
            out.append('& & ' + _s(t[1]) + ' & ' + n0(t[2]) + '$kg/m^2$ & ' +
                       _s(t[3]) + ' & ' + n0(t[4]) + ' & ' + n1(t[5]) +
                       ' & ' + n0(t[6]) + r'\\')
        for j in range(i + 2, bounds[k + 1]):
            t = DL_table[j]
            out.append('& & & & ' + _s(t[3]) + ' & ' + n0(t[4]) + ' & ' +
                       n1(t[5]) + ' & ' + n0(t[6]) + r'\\')
        if k != len(starts) - 1:
            out.append(r'\hline')
    out.append(r'\bottomrule')
    return out


def _cut_material_name(name):
    name = _s(name)
    i = name.find('_')
    return name if i < 0 else name[:i]


def tex_self(SELF_ID, t1, t2):
    """SELF_TeX.m: 構造材自重 (longtable)。"""
    fl = len(t2[0])
    head_add = '&&&&& $(kN)$ & ' + ''.join('%d' % i + 'F & ' for i in range(1, fl)) + r'RF \\'
    head1 = (r'\multirow{2}{*}{部材種別} & \centering\multirow{2}{*}{層} &  '
             r'\centering\multirow{2}{*}{部位} & \multirow{2}{*}{材料ID} &　'
             r'\multirow{2}{*}{材料名} & 躯体自重')
    head2 = (r'& \multicolumn{%d}{c}{各層への躯体自重の参入値$(kN)$} \\ \cline{7-%d}'
             % (fl, 6 + fl))
    out = [r'\begin{longtable}{>{\centering\arraybackslash}p{6zw}p{6zw}p{4zw}'
           r'*{3}{>{\centering\arraybackslash}p{4zw}}',
           '*{%d}{>{\\centering\\arraybackslash}p{2zw}}}' % fl,
           r'\caption{構造材自重}', r'\label{SELF_table}', r'\\\toprule',
           head1, head2, head_add, r'\bottomrule', r'\endfirsthead',
           r'\toprule', head1, head2, head_add, r'\bottomrule', r'\endhead']

    def line(j, lead, tail):
        t = t1[j]
        add = ''.join(' & ' + n0(v) for v in t2[j])
        return (lead + _s(t[0]) + ' & ' + _s(t[1]) + ' & ' + n0(t[2]) + ' & ' +
                _cut_material_name(t[3]) + ' & ' + n0(t[4]) + add + tail)

    # 1行を1グループ (\multirow{1}) として出力する。Matlab版も空白セルを NaN 扱いにできず
    # (数値0として渡る) 実際にはこの挙動だったため、そのまま踏襲する。
    for i in range(len(SELF_ID)):
        out.append(line(i, r'\multirow{1}{*}{%s} & ' % _s(SELF_ID[i]), r' \\ \bottomrule'))
    return out


def tex_ll(LL_table, LL_tag):
    """LL_TeX.m: 積載荷重表。"""
    out = []
    for i, r in enumerate(LL_table):
        out.append(_s(LL_tag[i]) + ' & ' + n0(r[0]) + ' & ' + n2(r[1]) + ' & ' +
                   n2(r[2]) + ' & ' + n2(r[3]) + r'\\')
        out.append(r'\bottomrule' if i == len(LL_table) - 1 else r'\hline')
    return out


def tex_snow(in1, in2, in3):
    """SNOW_TeX.m: 積雪荷重。in1=D5:D11(α,β,γ,R,ls,rs,d), in2=条例の積雪深, in3=条例名。"""
    if isblank(in2):
        alpha, beta, gamma, R, ls, rs, d = [x if x is not None else 0.0 for x in in1]
        out = [r'垂直積雪深の算定 & 告示式による \\',
               r'$\alpha$ & ' + nf(alpha, '.4') + r' \\',
               r'$\beta$ & ' + n2(beta) + r' \\',
               r'$\gamma$ & ' + n2(gamma) + r' \\',
               r'$R$ & ' + n0(R) + r' \\',
               r'標高~$l_s$ & $' + n0(ls) + r'[m]$ \\',
               r'海率~$r_s$ & $' + n2(rs) + r'$ \\']
    else:
        d = in2
        out = [r'垂直積雪深の算定 & 条例による \\',
               r'参照した条例 & ' + _s(in3) + r' \\']
    if d >= 100:
        rho, ms = 30, '該当する'
    else:
        rho, ms = 20, '該当しない'
    out += [r'&\\',
            r'多雪区域 & ' + ms + r'\\',
            r'垂直積雪深 & $d=' + n1(d) + r'~cm$\\',
            r'積雪の単位荷重 & $\rho=' + n1(rho) + r'~N/cm/m^2$\\',
            r'積雪荷重 & $S=d\cdot\rho=' + nf(d * rho / 1000, '.3') + r'~kN/m^2$']
    return out


def tex_wind(w):
    """W_TeX.m: 風圧力 (calc_wind の結果 w を使う)。"""
    return [r'地表面粗度区分 & ' + ROMAN[w['G']] + r' \\',
            r'基準風速 & $V_{0} = ' + n1(w['Vo']) + r'~m/s$ \\',
            r'ガスト影響係数 & $Gf=' + n2(w['Gf']) + r'$ \\',
            r'Erの数値 & $Er = ' + n2(w['Er']) + r'$ \\',
            r'Eの数値 & $E$ =$Er^2 \times Gf$= ' + n2(w['E']) + r' \\',
            r'速度圧 & $q =0.6E \times V_0^2$ = $' + n0(w['q']) + r'~N/m^2$ \\',
            r'風力係数 & $C_f = ' + n2(w['Cf']) + r'$ \\',
            r'&\\',
            r'建物高さ & $H=' + n1(w['H']) + r'~m$\\',
            r'建物幅 & $W=' + n1(w['W']) + r'~m$\\',
            r'風圧力 & $Q_w=' + n1(w['Qw']) + r'~kN$']


def _weight_head(caption, ll_label, fl, add_text):
    h1 = (r'\multirow{2}{*}{荷重ID} & \multirow{2}{*}{概要} & DL & %s & エリア面積' % ll_label)
    h2 = (r'& \multicolumn{%d}{c}{建物重量への参入値$(kN)$} \\ \cline{6-%d}' % (fl, 5 + fl))
    return [r'\begin{longtable}{>{\centering\arraybackslash}p{3zw}p{8zw}'
            r'*{3}{>{\centering\arraybackslash}p{5zw}}',
            '*{%d}{>{\\centering\\arraybackslash}p{3zw}}}' % fl,
            r'\caption{' + caption + r'の建物重量(単位$kN$)}',
            r'\\\toprule', h1, h2, add_text, r'\bottomrule', r'\endfirsthead',
            r'\toprule', h1, h2, add_text, r'\bottomrule', r'\endhead']


def _weight_rows(LOAD_ID, LOAD_NAME, LOAD_table, W, ll_col):
    """Weight_TeX / Weight_F_TeX 共通の表本体と合計行。"""
    fl = len(W[0])
    n = len(LOAD_ID)
    z = _first_zero(LOAD_ID)

    def add(i):
        return ''.join(' & ' + n0(v) for v in W[i])

    out = [n0(LOAD_ID[0]) + ' & ' + _s(LOAD_NAME[0]) + ' & - & - & -' + add(0) +
           r' \\ \cline{1-%d}' % (5 + fl)]
    for i in range(1, z):
        t = LOAD_table[i]
        out.append(n0(LOAD_ID[i]) + ' & ' + _s(LOAD_NAME[i]) + ' & ' + n2(t[0]) +
                   ' & ' + n2(t[ll_col]) + ' & ' + n1(t[4]) + add(i) + r' \\')
        if i != z - 1:
            out.append(r'\hline')
    out.append(r'\bottomrule')
    for i in range(z + 1, n):
        t = LOAD_table[i]
        out.append(n0(LOAD_ID[i]) + ' & ' + _s(LOAD_NAME[i]) + ' & ' + n2(t[0]) +
                   ' & ' + n2(t[ll_col]) + ' & ' + n1(t[4]) + add(i) + r' \\')
        if i != n - 1:
            out.append(r'\hline')
    num = [[v if isinstance(v, (int, float)) else 0.0 for v in r] for r in W]
    total = n1(sum(sum(r) for r in num))
    for j in range(fl):
        total += ' & ' + n1(sum(r[j] for r in num))
    out.append(r'\bottomrule')
    out.append(r'\multicolumn{3}{r}{} & 合計 &' + total + r' \\ \hline')
    return out


def _floor_labels(fl):
    return '&& $kN/m~2$ & $kN/m~2$ & $m^2$ & ' + ''.join('%dF & ' % i for i in range(1, fl)) + r'RF \\'


def tex_weight(LOAD_ID, LOAD_NAME, LOAD_table, W, caption):
    """Weight_TeX.m: 地震力算定用の建物重量表 (LL3)。"""
    fl = len(W[0])
    return (_weight_head(caption, 'LL3', fl, _floor_labels(fl)) +
            _weight_rows(LOAD_ID, LOAD_NAME, LOAD_table, W, 3))


def tex_weight_f(LOAD_ID, LOAD_NAME, LOAD_table, W, caption):
    """Weight_F_TeX.m: 基礎設計用の建物重量表 (LL2) + 接地圧の節。"""
    fl = len(W[0])
    out = [r'\section{直接基礎の設計（ベタ基礎の設計）}',
           r'\subsection{接地圧の確認}',
           r'\subsubsection{(A)基礎設計用の建物重量}',
           '荷重外力計算書に基づいて求まる建物重量について以下に示す．',
           r'\def\arraystretch{1.2}', r'\begin{center}', r'\footnotesize']
    out += _weight_head(caption, 'LL2', fl, _floor_labels(fl))
    out += _weight_rows(LOAD_ID, LOAD_NAME, LOAD_table, W, 2)
    out += [r'\end{longtable}', r'\end{center}',
            r'\subsubsection{(B)接地圧の算定/許容接地圧の確認}',
            r'耐圧版の有効面積は$\FSone m^2$であるため，(A)にて求めた建物重量($\FStwo kN$)を耐圧版の有効面積で除して',
            r'接地圧は$\FSthree kN/m^2$と求まる．',
            r'これは第\ref{jiban}章で求めた地盤の許容地耐力以下の値であり，接地圧に問題のないことが確認された．',
            r'\clearpage', r'\subsection{耐圧版の設計}',
            '前節で確認をした接地圧によって耐圧版に生じる応力の確認を行う．\\par',
            '耐圧版の設計においては，耐圧版の自重は接地圧とキャンセルされるため接地圧から耐圧版自重を除いた値を地盤からの反力として荷重の入力を行う．\\par',
            '次ページに耐圧版に裁荷した荷重図および応力解析結果，耐圧版の許容耐力の算定表をそれぞれ示す．',
            '接地圧によって耐圧版に生じる曲げモーメントおよびせん断力が許容耐力以下であることが確認された．',
            r'\clearpage', '%耐圧版の裁荷図/許容曲げ/応力図挿入', r'\newpage',
            r'\ifnum \pdfinput=1 {',
            r'\includeAllPages{./link/11/耐圧版耐力応力.pdf}{1.0} %全ページにわたってバラバラに \includegraphics を繰り返す',
            r'\clearpage', '}', r'\else {', r'\vspace*{5zh}', r'\begin{center}',
            r'{\LARGE \textgt{耐圧版の荷重分布図}}\par', r'\vspace*{5zh}',
            r'{\LARGE \textgt{耐圧版の許容曲げ算定シート}}\par', r'\vspace*{5zh}',
            r'{\LARGE \textgt{耐圧版の解析結果(曲げ/せん断)}}', r'\end{center}',
            r'\clearpage', r'}\fi']
    return out


def tex_ci(inp, wi, area, r):
    """C_i.m のTeX部分: 地震力算定条件と設計用地震力の表。inp=(Z,G_class,h,alph,Co)。"""
    Z, G_class, h, alph, Co = inp
    Ai, Ci, Qi, Pi = r['Ai'], r['Ci'], r['Qi'], r['Pi']
    n = len(Ai)

    def floor_row(i, label):
        return (label + r'階 & ' + n1(wi[i]) + ' & ' + n2(Ai[i]) + ' & ' + n2(Ci[i]) +
                ' & ' + n1(Qi[i]) + ' & ' + n1(Pi[i]) + ' & ' + n1(wi[i] / area[i]) +
                ' & ' + n1(area[i]) + r'$m^2$ \\')

    out = [r'\begin{table}[h]', r'\begin{center}',
           r'\caption[ToRt]{地震力算定条件}', r'\label{table:ToRt}', r'\centering',
           r'\setlength{\tabcolsep}{3pt}', r'\footnotesize',
           r'\begin{tabular}{p{20zw}p{6zw}p{15zw}} \toprule',
           r'地域係数 & $Z$ & ' + n1(Z) + r' \\',
           r'地盤種別 & & 第' + n0(G_class) + r'種地盤 \\',
           r'振動特性係数 & $Rt$ & ' + n2(r['Rt']) + r' \\',
           r'建物高さ & $H$ & ' + n2(h) + r' [$m$] \\',
           r'略算式係数 & $\alpha$ & ' + n2(alph) + r' \\',
           r'Ai算定用固有周期 & $To$ & ' + n2(r['T']) + r' [$s$] \\',
           r'標準せん断力係数(1次設計) & $Co$ & ' + n2(Co) + r' \\ \hline',
           r'地下部分の水平震度 & $k$ & $0.1\times (1-H/40)Z$ \\ \bottomrule',
           r'\end{tabular}', r'\vspace{3zh}',
           r'\caption[AiCi算定表]{本建物における設計用地震力(単位$kN$)}',
           r'\label{table:AiCiQi}', r'\centering', r'\setlength{\tabcolsep}{3pt}',
           r'\footnotesize',
           r'\begin{tabular}{*{2}{>{\centering\arraybackslash}p{6zw}}*{4}{>{\centering\arraybackslash}p{6zw}}'
           r'>{\centering\arraybackslash}p{5zw}>{\centering\arraybackslash}p{5zw}}',
           r'\toprule',
           r'\multirow{2}{*}{層} & 層重量 & \multicolumn{4}{c}{設計用地震力} & \multicolumn{2}{c}{備考} \\ \cline{3-8}',
           r'& $w_i$ & $A_i$ & $Ci$ & $Q_i$ & $P_i$ & $w_i/A$ & 床面積$A$ \\ \midrule']
    out.append(floor_row(n - 1, 'R'))
    for i in range(n - 2, 0, -1):
        out.append(floor_row(i, '%d' % (i + 1)))
    out += [r'\hline',
            r'基礎/地階& 基礎重量 & \multicolumn{2}{c}{水平震度$k$} & $Q_B$ & $P_B$ & $w_i/A$ & 床面積$A$ \\ \hline',
            r'1階 & ' + n1(wi[0]) + r' & \multicolumn{2}{c}{' + n2(0.1 * Z) + '} & ' +
            n1(Qi[0] + Qi[1]) + ' & ' + n1(Pi[0]) + ' & ' + n1(wi[0] / area[0]) +
            ' & ' + n1(area[0]) + r'$m^2$ \\ \bottomrule']
    return out


# ---------------------------------------------------------------- Excel

class Book:
    """荷重表ブック (Excel COM)。既に Excel で開いていればそのブックを使う。"""

    def __init__(self, path):
        import pythoncom
        import win32com.client as wc
        pythoncom.CoInitialize()
        self.path = os.path.abspath(path)
        self.app = None
        self.opened_by_us = False
        self.wb = None
        try:
            self.app = wc.GetActiveObject('Excel.Application')
            for wb in self.app.Workbooks:
                if os.path.normcase(wb.FullName) == os.path.normcase(self.path):
                    self.wb = wb
                    break
        except Exception:
            self.app = None
        if self.wb is None:
            self.app = wc.DispatchEx('Excel.Application')
            self.app.Visible = False
            self.app.DisplayAlerts = False
            self.wb = self.app.Workbooks.Open(self.path)
            self.opened_by_us = True

    def get(self, name):
        """名前付き範囲 -> 2次元リスト (空白セルは None)。"""
        v = self.wb.Names(name).RefersToRange.Value2
        if not isinstance(v, tuple):
            return [[v]]
        return [list(r) for r in v]

    def put(self, name, rows):
        rng = self.wb.Names(name).RefersToRange
        rng.Value2 = tuple(tuple(float(x) for x in r) for r in rows)

    def close(self, save):
        if self.opened_by_us:
            if save:
                self.wb.Save()
            self.wb.Close(False)
            self.app.Quit()


def _flat(rows):
    return [x for r in rows for x in r]


def run(xls, out_dir=None, writeback=True):
    out_dir = out_dir or os.path.dirname(os.path.abspath(xls))
    bk = Book(xls)
    try:
        g = bk.get
        LOAD_ID = _col(g('LOAD_ID'))
        LOAD_NAME = _col(g('LOAD_NAME'))
        LOAD_table = g('LOAD_table')
        fin_id = _col(g('LOAD_finish_ID'))
        fin_name = _col(g('LOAD_finish_name'))
        ll_id = _col(g('LOAD_LL_ID'))
        W_found = g('LOAD_WEIGHT_FOUNDATION')
        W_eq = g('LOAD_WEIGHT')

        blocks = {}
        blocks['01_DRAFT'] = tex_draft(LOAD_ID, LOAD_NAME, LOAD_table, fin_id, fin_name, ll_id)
        blocks['09_LOAD'] = tex_load(LOAD_ID, LOAD_NAME, LOAD_table)

        dl = []
        for k in ('F', 'S', 'W'):
            dl = tex_dl(_col(g('DL_%s_ID' % k)), g('DL_%s_table' % k), dl)
        blocks['03_DL'] = dl
        blocks['02_SELF'] = tex_self(_col(g('SELF_ID')), g('SELF_table1'), g('SELF_table2'))
        blocks['04_LL'] = tex_ll(g('LL_table1'), _col(g('LL_tag')))
        blocks['05_SNOW'] = tex_snow(_flat(g('SNOW_input1')), g('SNOW_input2')[0][0],
                                     g('SNOW_input3')[0][0])

        Gc, Vo, H, W = [x[0] for x in g('W_input')]
        wind = calc_wind(Gc, Vo, H, W)
        blocks['06_W'] = tex_wind(wind)
        blocks['07_Weight'] = tex_weight(LOAD_ID, LOAD_NAME, LOAD_table, W_eq, '地震力算定用')
        blocks['07_Weight_F'] = tex_weight_f(LOAD_ID, LOAD_NAME, LOAD_table, W_found, '基礎設計用')

        # せん断力算定 (Qs_input = Z, G_class, h, alph, Co)
        Z, G_class, h, alph, Co = [x[0] for x in g('Qs_input')]
        wi = _flat(g('Qs_wi'))
        area = _flat(g('Qs_A'))
        ci = calc_ci(Z, G_class, h, alph, Co, wi)
        blocks['08_Ci'] = tex_ci((Z, G_class, h, alph, Co), wi, area, ci)

        if writeback:
            bk.put('W_output', [[wind[k]] for k in ('Gf', 'kz', 'Cf', 'Er', 'E', 'q')])
            bk.put('Qs_Ai', [ci['Ai']])
            bk.put('Qs_Ci', [ci['Ci']])
            bk.put('Qs_Qi', [ci['Qi']])
        saved = writeback and bk.opened_by_us
        bk.close(saved)
    except Exception:
        bk.close(False)
        raise

    order = ['01_DRAFT', '02_SELF', '03_DL', '04_LL', '05_SNOW', '06_W', '07_Weight',
             '08_Ci', '09_LOAD', '07_Weight_F']
    path = os.path.join(out_dir, 'excel2tex.txt')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for k in order:
            f.write('\n'.join(blocks[k]) + '\n' + SEP + '\n')
    with open(os.path.join(out_dir, 'Weight_F.txt'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(blocks['07_Weight_F']) + '\n')
    return dict(path=path, wind=wind, ci=ci, wi=wi, writeback=writeback,
                saved=saved, in_excel=not bk.opened_by_us)


def main(argv=None):
    ap = argparse.ArgumentParser(description='荷重表xls -> excel2tex.txt (Matlab不要)')
    ap.add_argument('xls')
    ap.add_argument('--out', help='出力フォルダ (既定: xlsと同じ)')
    ap.add_argument('--no-writeback', action='store_true',
                    help='風・地震力の計算結果をxlsのセルへ書き戻さない')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    r = run(a.xls, a.out, not a.no_writeback)
    w, c = r['wind'], r['ci']
    print('出力: ' + r['path'])
    print('風  : Gf=%.2f kz=%.2f Cf=%.2f Er=%.3f E=%.3f q=%.1f N/m2  Qw=%.1f kN'
          % (w['Gf'], w['kz'], w['Cf'], w['Er'], w['E'], w['q'], w['Qw']))
    print('地震: T=%.3f Rt=%.3f' % (c['T'], c['Rt']))
    for lab, k in (('Ai', 'Ai'), ('Ci', 'Ci'), ('Qi', 'Qi')):
        print('  %s: %s' % (lab, '  '.join('%.4g' % x for x in c[k])))
    if not r['writeback']:
        print('セルへの書き戻し: なし')
    elif r['in_excel']:
        print('セルへ書き戻しました (Excelで開いているブックのため未保存。必要なら保存してください)')
    else:
        print('セルへ書き戻して保存しました')


if __name__ == '__main__':
    main()
