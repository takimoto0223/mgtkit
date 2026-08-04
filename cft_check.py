# -*- coding: utf-8 -*-
"""CFT柱の断面検定 (CFT_ratio_analysis 系) の逐語移植.

元コード:
  privatetool_function/MIDAS/ratio/SRC/CFT_ratio_analysis.m
  structural_function/SRC/CFT_section_analysis/column/circle/SA_CFTSRcolumn_AIJ.m
  structural_function/SRC/CFT_section_analysis/column/rectangular/SA_CFTSBcolumn_AIJ.m
  structural_function/SRC/CFT_section_analysis/column/circle/CFT_Rcolumn_text.m
  structural_function/RC/RC_section_analysis/column/circle/nobar_C_SR.m
  structural_function/RC/RC_section_analysis/column/rectangular/nobar_C_SB.m

注意 (原典の挙動をそのまま維持している点):
  - CFT_ratio_analysis は冒頭で stress(:,1) を符号反転した後、
    stressCFT 構築時に再度 -1 倍する (二重反転で結局元の符号のまま)。
  - ele_lengthCFT = min(ele_length(1:2)) …… 3要素座屈長ベクトルの
    先頭2要素のminを座屈長lkとする。minは非安全側の可能性 (設計要確認)。
  - 角形分岐(17000)は詳細文 CFT_Rcolumn_text の呼び出しが原典で
    コメントアウトされているため、検定値のみで maxratios_text は更新しない。
  - SA_CFTSRcolumn_AIJ の短期(timecase==2)検定で、中央のratio_c1 が
    stress(2,1)ではなく stress(1,1) (i端軸力) を使う (原典483行のまま)。
  - SA_CFTSBcolumn_AIJ は原典が BBOX 更新(2020/09/04 板厚違い対応版,
    [D,B,tx,ty]の4要素必須) より前 (2009) のままなので、3要素
    [B D t] を渡すと MATLAB でも添字エラーで動作しない。
    本移植では tx=ty=t を補って BBOX を呼ぶ (SB_BBOX_T4 参照)。
  - plotCFT != 0 のときの figure/plot 出力は移植対象外 (何もしない)。
"""

import math

import numpy as np

from .util import _colon
from .rc_check import ALST_RC_AIJ, E_RC_AIJ
from .alst import ALST_S, ALST_S_CFT, sfc
from .secprops import steelForm
from .s_check import _num2str


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _sortrows1(A):
    """MATLAB sortrows(A,1) 相当 (1列目で安定ソート)."""
    A = np.atleast_2d(np.asarray(A, dtype=float))
    order = np.argsort(A[:, 0], kind='stable')
    return A[order, :]


# ---------------------------------------------------------------------------
# nobar_C_SR.m
# ---------------------------------------------------------------------------

def nobar_C_SR(xn, cD, fc):
    """nobar_C_SR.m の逐語移植.

    円柱無筋コンクリートのMN曲線
    入力: xn 中立軸位置(mm), cD コンクリート径(mm), fc 許容圧縮応力度(N/mm2)
    返り値 (cM[Nmm], cN[N])
    """
    if xn <= 0:
        print("ERROR = '中立軸位置設定ミス'")
        raise ValueError('中立軸位置設定ミス (nobar_C_SR)')

    if xn < cD:
        theta = math.acos(1 - 2 * xn / cD)
        cN = cD ** 3 / 8 / xn * ((math.cos(theta) ** 2 + 2) * math.sin(theta) / 3
                                 - theta * math.cos(theta)) * fc
        cM = cD ** 4 / 64 / xn * (theta + (math.cos(theta) ** 2 - 5 / 2)
                                  * math.sin(2 * theta) / 3) * fc
    else:
        cN = math.pi * cD ** 2 / 4 * (1 - cD / 2 / xn) * fc
        cM = math.pi * cD ** 4 / 64 / xn * fc
    return cM, cN


# ---------------------------------------------------------------------------
# nobar_C_SB.m
# ---------------------------------------------------------------------------

def nobar_C_SB(xn, cD, fc):
    """nobar_C_SB.m の逐語移植.

    角柱無筋コンクリートのMN曲線
    角のサイズは正方形とする
    入力: xn 中立軸位置(mm), cD コンクリート辺長(mm), fc 許容圧縮応力度(N/mm2)
    返り値 (cM[Nmm], cN[N])
    """
    if xn <= 0:
        print("ERROR = '中立軸位置設定ミス'")
        raise ValueError('中立軸位置設定ミス (nobar_C_SB)')

    if xn < cD:
        cN = cD * xn * fc / 2
        cM = (3 * cD - 2 * xn) / 12 * cD * xn * fc
    else:
        cN = cD ** 2 * (1 - cD / 2 / xn) * fc
        cM = cD ** 4 / 12 / xn * fc
    return cM, cN


# ---------------------------------------------------------------------------
# SA_CFTSRcolumn_AIJ.m
# ---------------------------------------------------------------------------

def SA_CFTSRcolumn_AIJ(stress, lk, Form, Fc, SN, timecase, motion, plotCFT):
    """SA_CFTSRcolumn_AIJ.m の逐語移植.

    建築学会CFT基準/CFT○柱断面算定
    2009/06/18 ソース確認済
    基本的にはCFT柱は中間にせん断力は作用しないためにモーメント勾配は単曲率を仮定した．

    入力:
      stress : 3x6 [N Fy Fz 0 My Mz] (kN, kNm) 行=i端/中央/j端
               (Nは圧縮が負のまま。内部で-1倍して圧縮正で扱う)
      lk     : 座屈長さ (mm)
      Form   : [D t] (mm)
      Fc     : コンクリートFc (スカラ, N/mm2)
      SN     : 鋼管規格 (400/490等)
      timecase: 1長期 / >=10短期
      motion : 1節点移動なし / -1節点移動あり
      plotCFT: 0でplotなし (plot処理は移植対象外)
    返り値 (ratioNM(3,), ratioQ(3,))
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    lk = float(lk)

    # 算定結果保存セル (長柱・中柱・短柱の三種類)
    # → Python では領域ごとの2列配列で保持する

    # plotする際の刻み設定など
    # 軸力のきざみ
    n_divide = 50
    # 充填部の無筋コンクリートのMN曲線と無筋コンクリート純圧縮との差
    delta_N = 200
    # RCのMN曲線部の刻み
    delta_xn = 10

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        # NOTE: 原典29行 ERROR代入のみで停止しない (そのまま続行)
        print("ERROR = '長短期設定ミス'")

    # 節点移動の有無
    if motion == 1:  # 節点移動なし
        pass  # NOTE='CFT柱の節点移動なし'
    elif motion == -1:  # 節点移動あり
        pass  # NOTE='CFT柱の節点移動あり'
    else:
        print("ERROR = '柱の水平移動拘束設定ミス'")
        raise ValueError('柱の水平移動拘束設定ミス')

    # 外形情報
    D = Form[0]
    t = Form[1]
    sForm = np.concatenate([[3.0], Form])

    # 内蔵鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sIx = steelForm(sForm, 2) * 10000  # mm4
    ixs = steelForm(sForm, 4) * 10  # mm
    sZx = steelForm(sForm, 6) * 1000  # mm3
    # slamda = max(12*D,lk)/ixs;

    # 充填コンクリート情報
    cD = D - 2 * t  # mm
    cA = cD ** 2 * math.pi / 4  # mm2
    cI = cD ** 4 * math.pi / 64  # mm4
    cixs = math.sqrt(cI / cA)  # mm

    # 長柱・中柱・短柱の判定
    long_column = lk / D
    if long_column > 12:
        column_type = 'long'
    elif long_column <= 4:
        column_type = 'short'
    else:
        column_type = 'medium'

    # 細長比設定（長柱用，中柱用の二種類）
    clamda1 = max(12 * D, lk) / cixs  # 中柱の際のMN算出用に準備
    clamda2 = lk / cixs  # 実際のコンクリート部分の細長比

    # 許容応力度関係
    f_c = ALST_RC_AIJ(Fc)  # RC許容応力度
    sf = ALST_S_CFT(np.array([SN, t], dtype=float))  # 鉄骨の許容応力度

    # ヤング係数情報（cE,sE,nなど）
    n = E_RC_AIJ(Fc)  # ｎには[Ec n（RC規準に記載されているヤング係数比) r]
    cE = n[0]
    sE = 205000

    # 構造細則(計算外規定のチェック)
    # その１：径厚比の検討←追記すること

    # -------------------- 圧縮許容耐力の算定 --------------------
    # 短柱の場合（座屈長さが断面せいの4倍以下）

    # 鉄骨部分，RC部分の長期許容圧縮力
    CL_N_ca1 = cA * f_c[0, 0] / 10 ** 3
    SL_N_ca1 = sAe * sf[0, 0] / 10 ** 3
    # 鉄骨部分，RC部分の短期許容圧縮力
    CS_N_ca1 = cA * f_c[1, 0] / 10 ** 3
    SS_N_ca1 = sAe * sf[1, 0] / 10 ** 3
    # 短柱全体（CFT）の許容圧縮耐力
    L_N_ca1 = CL_N_ca1 + SL_N_ca1
    S_N_ca1 = CS_N_ca1 + SS_N_ca1

    # 長柱の場合（座屈長さが断面せいの12倍より大きい）

    # 圧縮強度時のひずみ CFT規準P.237による
    eu = 0.93 * Fc[0] ** (1 / 4) / 10 ** 3

    # 基準化細長比　一つ目は12Dと座屈長さのうち大きいほう(中柱の算出に利用）
    # 二つ目は本来の座屈長さなので，長柱では同じ値となる．　CFT規準P.237による
    clamda_1 = clamda1 / math.pi * math.sqrt(eu)
    clamda_2 = clamda2 / math.pi * math.sqrt(eu)
    # 係数Cc　CFT規準P.238による
    Cc = 0.568 + 0.00612 * Fc[0]

    # コンクリート座屈応力度
    if clamda_1 <= 1.0:
        sig_cr1 = Fc[0] * 2 / (1 + math.sqrt(clamda_1 ** 4 + 1))
    else:
        sig_cr1 = Fc[0] * 2 * (math.sqrt(2) - 1) * math.exp(Cc * (1 - clamda_1))
    if clamda_2 <= 1.0:
        sig_cr2 = Fc[0] * 2 / (1 + math.sqrt(clamda_2 ** 4 + 1))
    else:
        sig_cr2 = Fc[0] * 2 * (math.sqrt(2) - 1) * math.exp(Cc * (1 - clamda_2))

    # 座屈を考慮した鉄骨の許容圧縮応力度
    s_f_c1 = sfc(ixs, ixs, [max(12 * D, lk), max(12 * D, lk)], SN, t)
    # clamda1に対応するものを求めておけば，中柱であった場合には12Dのものが求まる．

    # 長柱の許容圧縮耐力（実際には細長比から中柱になる場合は12Dの仮想長柱の許容耐力が求まる．）
    CL_N_ca3 = cA * sig_cr1 / 3 / 10 ** 3  # RC部分の長期許容圧縮力 N/mm2*mm2/10^3=kN
    SL_N_ca3 = sAe * s_f_c1[0] / 10 ** 3  # 鉄骨部分の長期許容圧縮力kN

    CS_N_ca3 = cA * sig_cr1 / 1.5 / 10 ** 3  # RC部分の短期許容圧縮力
    SS_N_ca3 = sAe * s_f_c1[1] / 10 ** 3  # 鉄骨部分の短期許容圧縮力

    L_N_ca3 = CL_N_ca3 + SL_N_ca3  # 長柱全体（CFT）の長期許容圧縮耐力
    S_N_ca3 = CS_N_ca3 + SS_N_ca3  # 長柱全体（CFT）の短期許容圧縮耐力

    # 実際には細長比から中柱になる場合は12Dの仮想長柱のコンクリート部分許容耐力
    CL_N_ca32 = cA * sig_cr2 / 3 / 10 ** 3  # RC部分の長期許容圧縮力
    CS_N_ca32 = cA * sig_cr2 / 1.5 / 10 ** 3  # RC部分の短期許容圧縮力

    # 中柱の場合（座屈長さが断面せいの4-12倍）
    # もし中柱であれば補間して求める．
    # ただし短柱・長柱である場合には補完せずそれぞれの数値を代用
    if column_type == 'medium':
        L_N_ca2 = L_N_ca1 * (12 - long_column) / 8 + L_N_ca3 * (long_column - 4) / 8
        S_N_ca2 = S_N_ca1 * (12 - long_column) / 8 + S_N_ca3 * (long_column - 4) / 8
    elif column_type == 'long':
        L_N_ca2 = L_N_ca3
        S_N_ca2 = S_N_ca3
    elif column_type == 'short':
        L_N_ca2 = L_N_ca1
        S_N_ca2 = S_N_ca1

    # 許容引張耐力
    L_T = -1 * sAe * sf[0, 0] / 10 ** 3
    S_T = -1 * sAe * sf[1, 0] / 10 ** 3

    # ------------------------------------------------------------------
    # 短柱のM-N曲線（座屈長さが断面せいの4倍以下）
    # ------------------------------------------------------------------

    N_limit_up = [L_N_ca1, S_N_ca1]  # 純圧縮の時（最大軸力）
    N_limit_dn = [L_T, S_T]  # 純引張の時（最小軸力）
    CN_limit_up = [CL_N_ca1, CS_N_ca1]  # コンクリート部分の最大負担可能軸力

    Mi = np.array([stress[0, 4], stress[0, 5],
                   math.sqrt(stress[0, 4] ** 2 + stress[0, 5] ** 2)])
    Mc = np.array([stress[1, 4], stress[1, 5],
                   math.sqrt(stress[1, 4] ** 2 + stress[1, 5] ** 2)])
    Mj = np.array([stress[2, 4], stress[2, 5],
                   math.sqrt(stress[2, 4] ** 2 + stress[2, 5] ** 2)])
    Ncenter = stress[1, 0]

    # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
    # N大領域の長期曲線
    col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
    sN = col1 - CN_limit_up[0]  # RCで負担できない鉄骨の負担分
    ALW_NM_L_11 = np.column_stack([
        col1,
        (1 - 10 ** 3 * sN / (sAe * sf[0, 0])) * sZx * sf[0, 0] / 10 ** 6])
    # N大領域の短期曲線
    col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
    sN = col1 - CN_limit_up[1]
    ALW_NM_S_11 = np.column_stack([
        col1,
        (1 - 10 ** 3 * sN / (sAe * sf[1, 0])) * sZx * sf[1, 0] / 10 ** 6])

    # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
    # N中領域の長期曲線
    # 最初は中立軸が最外端でC部分の圧縮負担０，モーメントも０の点からスタート
    Ngap = CN_limit_up[0]  # 純圧縮時との差
    rows = [[0.0001, sZx * sf[0, 0] / 10 ** 6]]  # 中立軸最外部の時のC部分の軸力負担は０
    xn = 1
    while Ngap > delta_N:
        ALW_M_one, ALW_N_one = nobar_C_SR(xn, cD, f_c[0, 0])
        rows.append([ALW_N_one / 10 ** 3,
                     ALW_M_one / 10 ** 6 + sZx * sf[0, 0] / 10 ** 6])
        Ngap = CN_limit_up[0] - max(r[0] for r in rows)
        xn = xn + delta_xn
    ALW_NM_L_12 = np.array(rows, dtype=float)

    # N中領域の短期曲線（長期と同様）
    Ngap = CN_limit_up[1]
    rows = [[0.0001, sZx * sf[1, 0] / 10 ** 6]]
    xn = 1
    while Ngap > delta_N:
        ALW_M_one, ALW_N_one = nobar_C_SR(xn, cD, f_c[1, 0])
        rows.append([ALW_N_one / 10 ** 3,
                     ALW_M_one / 10 ** 6 + sZx * sf[1, 0] / 10 ** 6])
        Ngap = CN_limit_up[1] - max(r[0] for r in rows)
        xn = xn + delta_xn
    ALW_NM_S_12 = np.array(rows, dtype=float)

    # 引張時 N << 0
    # N引張領域の長期曲線
    col1 = _colon(-0.0001, -1 * n_divide, N_limit_dn[0])
    ALW_NM_L_13 = np.column_stack([
        col1,
        (1 - 10 ** 3 * np.abs(col1) / (sAe * sf[0, 0])) * sZx * sf[0, 0] / 10 ** 6])
    # N引張領域の短期曲線
    col1 = _colon(-0.0001, -1 * n_divide, N_limit_dn[1])
    ALW_NM_S_13 = np.column_stack([
        col1,
        (1 - 10 ** 3 * np.abs(col1) / (sAe * sf[1, 0])) * sZx * sf[1, 0] / 10 ** 6])

    short_NM_L = np.vstack([ALW_NM_L_11, ALW_NM_L_12, ALW_NM_L_13])
    short_NM_S = np.vstack([ALW_NM_S_11, ALW_NM_S_12, ALW_NM_S_13])

    short_NM_L = _sortrows1(short_NM_L)
    short_NM_S = _sortrows1(short_NM_S)

    long_NM_L = long_NM_S = None
    medium_NM_L = medium_NM_S = None

    # ------------------------------------------------------------------
    # 長柱のM-N曲線（座屈長さが断面せいの12倍以上）＜節点移動あり＞
    # ------------------------------------------------------------------
    if motion == -1 and column_type == 'long':
        N_limit_up = [L_N_ca3, S_N_ca3]
        CN_limit_up = [CL_N_ca3, CS_N_ca3]

        # CM算出のためのNk計算（2.8.11)式
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3
        # 節点移動ありなのでCM=1
        CMM = 1

        # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
        # N大領域の長期曲線
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        sN = col1 - CN_limit_up[0]
        ALW_NM_L_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[0])) * sZx * sf[0, 0] / 10 ** 6
            * (1 - 3 * CN_limit_up[0] / Nk) / CMM])
        # N大領域の短期曲線
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        sN = col1 - CN_limit_up[1]
        ALW_NM_S_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[1])) * sZx * sf[1, 0] / 10 ** 6
            * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        # cMmaxの算定
        cMmaxo = Fc[0] * D ** 3 / 12
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_1 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)  # (2.8.10)
        ALW_NM_L_32 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])  # (2.8.9)

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)  # (2.8.10)
        ALW_NM_S_32 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])  # (2.8.9)

        long_NM_L = _sortrows1(np.vstack([ALW_NM_L_31, ALW_NM_L_32]))
        long_NM_S = _sortrows1(np.vstack([ALW_NM_S_31, ALW_NM_S_32]))

    # ------------------------------------------------------------------
    # 長柱のM-N曲線（座屈長さが断面せいの12倍以上）＜節点移動なし＞
    # ------------------------------------------------------------------
    if motion == 1 and column_type == 'long':
        N_limit_up = [L_N_ca3, S_N_ca3]
        CN_limit_up = [CL_N_ca3, CS_N_ca3]

        # CM算出のためのNk計算（2.8.11)式
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3
        # モーメント勾配は単曲率を仮定した．
        porm = 1

        # CM算出のための計算（2.8.11)式
        M1 = max(Mi[2], Mj[2])
        M2 = min(Mi[2], Mj[2])
        M21 = porm * M2 / M1
        CMM = max(0.25, 1 - 0.5 * (1 - M21) * math.sqrt(abs(Ncenter) / Nk))

        # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
        # N大領域の長期曲線
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        sN = col1 - CN_limit_up[0]
        ALW_NM_L_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[0])) * sZx * sf[0, 0] / 10 ** 6
            * (1 - 3 * CN_limit_up[0] / Nk) / CMM])
        # N大領域の短期曲線
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        sN = col1 - CN_limit_up[1]
        ALW_NM_S_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[1])) * sZx * sf[1, 0] / 10 ** 6
            * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 12
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_1 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_32 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_32 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        long_NM_L = _sortrows1(np.vstack([ALW_NM_L_31, ALW_NM_L_32]))
        long_NM_S = _sortrows1(np.vstack([ALW_NM_S_31, ALW_NM_S_32]))

    # ------------------------------------------------------------------
    # 中柱のM-N曲線（座屈長さが断面せいの4-12倍)＜節点移動あり＞
    # ------------------------------------------------------------------
    if motion == -1 and column_type == 'medium':
        N_limit_up = [L_N_ca2, S_N_ca2]  # 中柱の負担可能軸力

        CN_limit_up = [CL_N_ca32, CS_N_ca32]  # RC部分の許容圧縮力
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3  # Nk計算
        CMM = 1

        # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
        # N大領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        ALW_NM_L_21 = np.column_stack([
            col1,
            (N_limit_up[0] - col1) * (sMo * (1 - 3 * CN_limit_up[0] / Nk) / CMM)
            / (N_limit_up[0] - CN_limit_up[0])])
        # CFT規準P208のNca2の説明では「短期」とされているが，ここでは長短期で区分している．

        # N大領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        ALW_NM_S_21 = np.column_stack([
            col1,
            (N_limit_up[1] - col1) * (sMo * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM)
            / (N_limit_up[1] - CN_limit_up[1])])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 12
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_2 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_22 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_22 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        medium_NM_L = _sortrows1(np.vstack([ALW_NM_L_21, ALW_NM_L_22]))
        medium_NM_S = _sortrows1(np.vstack([ALW_NM_S_21, ALW_NM_S_22]))

    # ------------------------------------------------------------------
    # 中柱のM-N曲線（座屈長さが断面せいの4-12倍）＜節点移動なし＞
    # ------------------------------------------------------------------
    if motion == 1 and column_type == 'medium':
        # ここではCFTは柱で中間にせん断力は作用しないためにモーメント勾配は単曲率を仮定した．
        N_limit_up = [L_N_ca2, S_N_ca2]

        CN_limit_up = [CL_N_ca32, CS_N_ca32]
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3

        # モーメント勾配は単曲率を仮定した．
        porm = 1

        M1 = max(Mi[2], Mj[2])
        M2 = min(Mi[2], Mj[2])
        M21 = porm * M2 / M1
        CMM = max(0.25, 1 - 0.5 * (1 - M21) * math.sqrt(abs(Ncenter) / Nk))

        # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
        # N大領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        ALW_NM_L_21 = np.column_stack([
            col1,
            (N_limit_up[0] - col1) * (sMo * (1 - 3 * CN_limit_up[0] / Nk) / CMM)
            / (N_limit_up[0] - CN_limit_up[0])])
        # CFT規準P208のNca2の説明では「短期」とされているが，ここでは長短期で区分している．

        # N大領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        ALW_NM_S_21 = np.column_stack([
            col1,
            (N_limit_up[1] - col1) * (sMo * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM)
            / (N_limit_up[1] - CN_limit_up[1])])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 12
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_2 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_22 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0.0001, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_22 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        medium_NM_L = _sortrows1(np.vstack([ALW_NM_L_21, ALW_NM_L_22]))
        medium_NM_S = _sortrows1(np.vstack([ALW_NM_S_21, ALW_NM_S_22]))

    # ------------------------------------------------------------------
    # 偏心率から検定比を求める
    # ------------------------------------------------------------------

    ei = Mi[2] / (-1 * stress[0, 0]) * 1000
    ec = Mc[2] / (-1 * stress[1, 0]) * 1000
    ej = Mj[2] / (-1 * stress[2, 0]) * 1000

    with np.errstate(divide='ignore', invalid='ignore'):
        e_short_L = np.column_stack(
            [short_NM_L[:, 1] / short_NM_L[:, 0] * 1000, short_NM_L[:, 0]])
        e_short_S = np.column_stack(
            [short_NM_S[:, 1] / short_NM_S[:, 0] * 1000, short_NM_S[:, 0]])

    if timecase == 2:
        ei1_index = int(np.argmin(np.abs(e_short_S[:, 0] - ei)))
        ratio_i1 = -1 * stress[0, 0] / e_short_S[ei1_index, 1]
        ec1_index = int(np.argmin(np.abs(e_short_S[:, 0] - ec)))
        # NOTE: 原典483行 ratio_c1 は stress(1,1) (i端軸力) を使用
        #       (長期側487-491行は stress(2,1)。短期のみi端軸力になっている)
        ratio_c1 = -1 * stress[0, 0] / e_short_S[ec1_index, 1]
        ej1_index = int(np.argmin(np.abs(e_short_S[:, 0] - ej)))
        ratio_j1 = -1 * stress[2, 0] / e_short_S[ej1_index, 1]
    elif timecase == 1:
        ei1_index = int(np.argmin(np.abs(e_short_L[:, 0] - ei)))
        ratio_i1 = -1 * stress[0, 0] / e_short_L[ei1_index, 1]
        ec1_index = int(np.argmin(np.abs(e_short_L[:, 0] - ec)))
        ratio_c1 = -1 * stress[1, 0] / e_short_L[ec1_index, 1]
        ej1_index = int(np.argmin(np.abs(e_short_L[:, 0] - ej)))
        ratio_j1 = -1 * stress[2, 0] / e_short_L[ej1_index, 1]

    ratio_i = ratio_i1
    ratio_c = ratio_c1
    ratio_j = ratio_j1
    if column_type == 'medium':
        with np.errstate(divide='ignore', invalid='ignore'):
            e_medium_L = np.column_stack(
                [medium_NM_L[:, 1] / medium_NM_L[:, 0] * 1000, medium_NM_L[:, 0]])
            e_medium_S = np.column_stack(
                [medium_NM_S[:, 1] / medium_NM_S[:, 0] * 1000, medium_NM_S[:, 0]])
        if timecase == 2:
            ei2_index = int(np.argmin(np.abs(e_medium_S[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_medium_S[ei2_index, 1]
            ec2_index = int(np.argmin(np.abs(e_medium_S[:, 0] - ec)))
            ratio_c2 = -1 * stress[1, 0] / e_medium_S[ec2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_medium_S[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_medium_S[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_c = max(ratio_c1, ratio_c2)
            ratio_j = max(ratio_j1, ratio_j2)
        elif timecase == 1:
            ei2_index = int(np.argmin(np.abs(e_medium_L[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_medium_L[ei2_index, 1]
            ec2_index = int(np.argmin(np.abs(e_medium_L[:, 0] - ec)))
            ratio_c2 = -1 * stress[1, 0] / e_medium_L[ec2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_medium_L[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_medium_L[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_c = max(ratio_c1, ratio_c2)
            ratio_j = max(ratio_j1, ratio_j2)
        else:
            print("ERROR = '柱の水平移動拘束設定ミス'")
            raise ValueError('柱の水平移動拘束設定ミス')
    elif column_type == 'long':
        with np.errstate(divide='ignore', invalid='ignore'):
            e_long_L = np.column_stack(
                [long_NM_L[:, 1] / long_NM_L[:, 0] * 1000, long_NM_L[:, 0]])
            e_long_S = np.column_stack(
                [long_NM_S[:, 1] / long_NM_S[:, 0] * 1000, long_NM_S[:, 0]])
        if timecase == 2:
            ei2_index = int(np.argmin(np.abs(e_long_S[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_long_S[ei2_index, 1]
            ec2_index = int(np.argmin(np.abs(e_long_S[:, 0] - ec)))
            ratio_c2 = -1 * stress[1, 0] / e_long_S[ec2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_long_S[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_long_S[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_c = max(ratio_c1, ratio_c2)
            ratio_j = max(ratio_j1, ratio_j2)
        elif timecase == 1:
            ei2_index = int(np.argmin(np.abs(e_long_L[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_long_L[ei2_index, 1]
            ec2_index = int(np.argmin(np.abs(e_long_L[:, 0] - ec)))
            ratio_c2 = -1 * stress[1, 0] / e_long_L[ec2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_long_L[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_long_L[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_c = max(ratio_c1, ratio_c2)
            ratio_j = max(ratio_j1, ratio_j2)
        else:
            print("ERROR = '柱の水平移動拘束設定ミス'")
            raise ValueError('柱の水平移動拘束設定ミス')

    ratioNM = np.array([ratio_i, ratio_c, ratio_j], dtype=float)

    # 柱の座屈長さに応じてplotする関数を決める
    if plotCFT == 0:
        pass
    else:
        # NOTE: 原典567-654行の figure/plot/legend/title は移植対象外 (何もしない)
        pass

    # せん断に対する検定＜M/QD>1＞
    Qi = math.sqrt(stress[0, 1] ** 2 + stress[0, 2] ** 2)
    Qc = math.sqrt(stress[1, 1] ** 2 + stress[1, 2] ** 2)
    Qj = math.sqrt(stress[2, 1] ** 2 + stress[2, 2] ** 2)

    maxM = max(abs(Mi[2]), abs(Mc[2]), abs(Mj[2]))
    maxQ = max(abs(Qi), abs(Qc), abs(Qj))

    if maxQ == 0:
        ratioQ = np.array([0.0, 0.0, 0.0])
    else:
        ratioQ = np.array([(maxQ * (D / 1000)) / maxM,
                           (maxQ * (D / 1000)) / maxM,
                           (maxQ * (D / 1000)) / maxM])

    if maxQ == 0:
        pass
    elif maxM / (maxQ * (D / 1000)) > 1:
        pass
    else:
        print('CFT柱のせん断検定NG（M/QD>1をみたさない）')

    return ratioNM, ratioQ


# ---------------------------------------------------------------------------
# SA_CFTSBcolumn_AIJ.m
# ---------------------------------------------------------------------------

def SA_CFTSBcolumn_AIJ(stress, lk, Form, Fc, SN, timecase, motion, plotCFT):
    """SA_CFTSBcolumn_AIJ.m の逐語移植.

    建築学会CFT基準/CFT■柱断面算定
    2009/07/31 ソース確認済
    基本的にはCFT柱は中間にせん断力は作用しないためにモーメント勾配は単曲率を仮定した．
    ニ軸曲げに対しては未対応（基本は正方形断面だと仮定して，曲げの大きいほうで検定する）

    入力:
      stress : 3x6 [N Fy Fz 0 My Mz] (kN, kNm)
      lk     : 座屈長さ (mm)
      Form   : [B D t] (mm)
      Fc, SN, timecase, motion, plotCFT : SA_CFTSRcolumn_AIJ と同じ
    返り値 (ratio_i, ratio_j) いずれもスカラ

    NOTE(SB_BBOX_T4): 原典は steelForm([4 B D t]) → BBOX([B D t]) を呼ぶが、
    BBOX.m は2020/09/04更新で [D,B,tx,ty] の4要素必須となっており
    MATLAB でも添字エラーで停止する (原典側の未整合)。本移植では
    tx=ty=t を補い BBOX([B D t t]) として動作させる。B!=D の場合は
    BBOX の D(せい)/B(はば) と引数順が入れ替わっている疑いも残る
    (正方形断面では影響なし)。
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    lk = float(lk)

    # 曲げの大きいほうを選ぶ
    absM = np.max(np.abs(stress[:, 4:6]), axis=0)  # max(abs(stress(:,5:6)))
    maxindex = int(np.argmax(absM)) + 1  # 1-based (先頭一致)
    if maxindex == 1:  # 強軸側の曲げが大きい
        Mstress_id = 5
    elif maxindex == 2:  # 弱軸側の曲げが大きい
        Mstress_id = 6
        Form = np.array([Form[1], Form[0], Form[2]])

    # plotする際の刻み設定など
    n_divide = 50
    delta_N = 200
    delta_xn = 10

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print("ERROR = '長短期設定ミス'")

    # 節点移動の有無
    if motion == 1:  # 節点移動なし
        print("NOTE = 'CFT柱の節点移動なし'")
    elif motion == -1:  # 節点移動あり
        print("NOTE = 'CFT柱の節点移動あり'")
    else:
        print("ERROR = '柱の水平移動拘束設定ミス'")
        raise ValueError('柱の水平移動拘束設定ミス')

    # 外形情報
    B = Form[0]
    D = Form[1]
    t = Form[2]
    # NOTE(SB_BBOX_T4): 原典 sForm=[4 Form] (3要素)。tx=ty=t を補って4要素にする。
    sForm = np.concatenate([[4.0], Form, [t]])

    # 内蔵鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sIx = steelForm(sForm, 2) * 10000  # mm4
    ixs = steelForm(sForm, 4) * 10  # mm
    sZx = steelForm(sForm, 6) * 1000  # mm3
    # slamda = max(12*D,lk)/ixs;

    # 充填コンクリート情報
    cB = B - 2 * t  # mm
    cD = D - 2 * t  # mm
    cA = cD * cB  # mm2
    cI = cB * cD ** 3 / 12  # mm4
    cixs = math.sqrt(cI / cA)  # mm
    clamda1 = max(12 * D, lk) / cixs  # 中柱の際のMN算出用に準備
    clamda2 = lk / cixs  # 実際のコンクリート部分の細長比

    # 長柱・中柱・短柱の判定
    long_column = lk / min(B, D)
    if long_column > 12:
        column_type = 'long'
    elif long_column <= 4:
        column_type = 'short'
    else:
        column_type = 'medium'

    # 細長比設定（長柱用，中柱用の二種類） (原典83-84行: 再計算・同値)
    clamda1 = max(12 * D, lk) / cixs
    clamda2 = lk / cixs

    # 許容応力度関係
    f_c = ALST_RC_AIJ(Fc)  # RC許容応力度
    sf = ALST_S(np.array([SN, t], dtype=float))  # 鉄骨の許容応力度
    # NOTE: 円形版はALST_S_CFT(板厚低減有効)だが角形版はALST_S (原典88行のまま)

    # ヤング係数情報（cE,sE,nなど）
    n = E_RC_AIJ(Fc)
    cE = n[0]
    sE = 205000

    # 構造細則(計算外規定のチェック)
    # その１：径厚比の検討←追記すること

    # -------------------- 圧縮許容耐力の算定 --------------------
    # 短柱の場合（座屈長さが断面せいの4倍以下）
    CL_N_ca1 = cA * f_c[0, 0] / 10 ** 3
    SL_N_ca1 = sAe * sf[0, 0] / 10 ** 3
    CS_N_ca1 = cA * f_c[1, 0] / 10 ** 3
    SS_N_ca1 = sAe * sf[1, 0] / 10 ** 3
    L_N_ca1 = CL_N_ca1 + SL_N_ca1
    S_N_ca1 = CS_N_ca1 + SS_N_ca1

    # 長柱の場合（座屈長さが断面せいの12倍より大きい）
    # 圧縮強度時のひずみ CFT規準P.237による
    eu = 0.93 * Fc[0] ** (1 / 4) / 10 ** 3

    clamda_1 = clamda1 / math.pi * math.sqrt(eu)
    clamda_2 = clamda2 / math.pi * math.sqrt(eu)
    # 係数Cc　CFT規準P.238による
    Cc = 0.568 + 0.00612 * Fc[0]

    # コンクリート座屈応力度
    if clamda_1 <= 1.0:
        sig_cr1 = Fc[0] * 2 / (1 + math.sqrt(clamda_1 ** 4 + 1))
    else:
        sig_cr1 = Fc[0] * 2 * (math.sqrt(2) - 1) * math.exp(Cc * (1 - clamda_1))
    if clamda_2 <= 1.0:
        sig_cr2 = Fc[0] * 2 / (1 + math.sqrt(clamda_2 ** 4 + 1))
    else:
        sig_cr2 = Fc[0] * 2 * (math.sqrt(2) - 1) * math.exp(Cc * (1 - clamda_2))

    # 座屈を考慮した鉄骨の許容圧縮応力度
    s_f_c1 = sfc(ixs, ixs, [max(12 * D, lk), max(12 * D, lk)], SN, t)

    # 長柱の許容圧縮耐力
    CL_N_ca3 = cA * sig_cr1 / 3 / 10 ** 3
    SL_N_ca3 = sAe * s_f_c1[0] / 10 ** 3
    CS_N_ca3 = cA * sig_cr1 / 1.5 / 10 ** 3
    SS_N_ca3 = sAe * s_f_c1[1] / 10 ** 3
    L_N_ca3 = CL_N_ca3 + SL_N_ca3
    S_N_ca3 = CS_N_ca3 + SS_N_ca3

    # 実際には細長比から中柱になる場合は12Dの仮想長柱のコンクリート部分許容耐力
    CL_N_ca32 = cA * sig_cr2 / 3 / 10 ** 3
    CS_N_ca32 = cA * sig_cr2 / 1.5 / 10 ** 3

    # 中柱の場合（座屈長さが断面せいの4-12倍）
    if column_type == 'medium':
        L_N_ca2 = L_N_ca1 * (12 - long_column) / 8 + L_N_ca3 * (long_column - 4) / 8
        S_N_ca2 = S_N_ca1 * (12 - long_column) / 8 + S_N_ca3 * (long_column - 4) / 8
    elif column_type == 'long':
        L_N_ca2 = L_N_ca3
        S_N_ca2 = S_N_ca3
    elif column_type == 'short':
        L_N_ca2 = L_N_ca1
        S_N_ca2 = S_N_ca1

    # 許容引張耐力
    L_T = -1 * sAe * sf[0, 0] / 10 ** 3
    S_T = -1 * sAe * sf[1, 0] / 10 ** 3

    # ------------------------------------------------------------------
    # 短柱のM-N曲線（座屈長さが断面せいの4倍以下）
    # ------------------------------------------------------------------
    N_limit_up = [L_N_ca1, S_N_ca1]
    N_limit_dn = [L_T, S_T]
    CN_limit_up = [CL_N_ca1, CS_N_ca1]

    Mi = abs(stress[0, Mstress_id - 1])
    Mj = abs(stress[2, Mstress_id - 1])
    Ncenter = stress[1, 0]

    # 軸力がコンクリートの圧縮を超えている場合 CN_limit_up << N << N_limit_up
    # N大領域の長期曲線
    col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
    sN = col1 - CN_limit_up[0]
    ALW_NM_L_11 = np.column_stack([
        col1,
        (1 - 10 ** 3 * sN / (sAe * sf[0, 0])) * sZx * sf[0, 0] / 10 ** 6])
    # N大領域の短期曲線
    col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
    sN = col1 - CN_limit_up[1]
    ALW_NM_S_11 = np.column_stack([
        col1,
        (1 - 10 ** 3 * sN / (sAe * sf[1, 0])) * sZx * sf[1, 0] / 10 ** 6])

    # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
    # N中領域の長期曲線
    Ngap = CN_limit_up[0]
    rows = [[0.0, sZx * sf[0, 0] / 10 ** 6]]  # 中立軸最外部の時のC部分の軸力負担は０
    xn = 1
    while Ngap > delta_N:
        ALW_M_one, ALW_N_one = nobar_C_SB(xn, cD, f_c[0, 0])
        # NOTE: 原典212行 nobar_C_SB へは cD のみ渡す (cB は使われない。
        #       nobar_C_SB は正方形断面を仮定)
        rows.append([ALW_N_one / 10 ** 3,
                     ALW_M_one / 10 ** 6 + sZx * sf[0, 0] / 10 ** 6])
        Ngap = CN_limit_up[0] - max(r[0] for r in rows)
        xn = xn + delta_xn
    ALW_NM_L_12 = np.array(rows, dtype=float)

    # N中領域の短期曲線（長期と同様）
    Ngap = CN_limit_up[1]
    rows = [[0.0, sZx * sf[1, 0] / 10 ** 6]]
    xn = 1
    while Ngap > delta_N:
        ALW_M_one, ALW_N_one = nobar_C_SB(xn, cD, f_c[1, 0])
        rows.append([ALW_N_one / 10 ** 3,
                     ALW_M_one / 10 ** 6 + sZx * sf[1, 0] / 10 ** 6])
        Ngap = CN_limit_up[1] - max(r[0] for r in rows)
        xn = xn + delta_xn
    ALW_NM_S_12 = np.array(rows, dtype=float)

    # 引張時 N << 0
    col1 = _colon(0, -1 * n_divide, N_limit_dn[0])
    ALW_NM_L_13 = np.column_stack([
        col1,
        (1 - 10 ** 3 * np.abs(col1) / (sAe * sf[0, 0])) * sZx * sf[0, 0] / 10 ** 6])
    col1 = _colon(0, -1 * n_divide, N_limit_dn[1])
    ALW_NM_S_13 = np.column_stack([
        col1,
        (1 - 10 ** 3 * np.abs(col1) / (sAe * sf[1, 0])) * sZx * sf[1, 0] / 10 ** 6])

    short_NM_L = _sortrows1(np.vstack([ALW_NM_L_11, ALW_NM_L_12, ALW_NM_L_13]))
    short_NM_S = _sortrows1(np.vstack([ALW_NM_S_11, ALW_NM_S_12, ALW_NM_S_13]))

    long_NM_L = long_NM_S = None
    medium_NM_L = medium_NM_S = None

    # ------------------------------------------------------------------
    # 長柱のM-N曲線（座屈長さが断面せいの12倍以上）＜節点移動あり＞
    # ------------------------------------------------------------------
    if motion == -1 and column_type == 'long':
        N_limit_up = [L_N_ca3, S_N_ca3]
        CN_limit_up = [CL_N_ca3, CS_N_ca3]

        # CM算出のためのNk計算（2.8.11)式
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3
        # 節点移動ありなのでCM=1
        CMM = 1

        # N大領域の長期曲線
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        sN = col1 - CN_limit_up[0]
        ALW_NM_L_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[0])) * sZx * sf[0, 0] / 10 ** 6
            * (1 - 3 * CN_limit_up[0] / Nk) / CMM])
        # N大領域の短期曲線
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        sN = col1 - CN_limit_up[1]
        ALW_NM_S_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[1])) * sZx * sf[1, 0] / 10 ** 6
            * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        # cMmaxの算定 (角形: D^3/8)
        cMmaxo = Fc[0] * D ** 3 / 8
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_1 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)  # (2.8.10)
        ALW_NM_L_32 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])  # (2.8.9)

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)  # (2.8.10)
        ALW_NM_S_32 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])  # (2.8.9)

        long_NM_L = _sortrows1(np.vstack([ALW_NM_L_31, ALW_NM_L_32]))
        long_NM_S = _sortrows1(np.vstack([ALW_NM_S_31, ALW_NM_S_32]))

    # ------------------------------------------------------------------
    # 長柱のM-N曲線（座屈長さが断面せいの12倍以上）＜節点移動なし＞
    # ------------------------------------------------------------------
    if motion == 1 and column_type == 'long':
        N_limit_up = [L_N_ca3, S_N_ca3]
        CN_limit_up = [CL_N_ca3, CS_N_ca3]

        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3
        # モーメント勾配は単曲率を仮定した．
        porm = 1

        M1 = max(Mi, Mj)
        M2 = min(Mi, Mj)
        M21 = porm * M2 / M1
        CMM = max(0.25, 1 - 0.5 * (1 - M21) * math.sqrt(abs(Ncenter) / Nk))

        # N大領域の長期曲線
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        sN = col1 - CN_limit_up[0]
        ALW_NM_L_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[0])) * sZx * sf[0, 0] / 10 ** 6
            * (1 - 3 * CN_limit_up[0] / Nk) / CMM])
        # N大領域の短期曲線
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        sN = col1 - CN_limit_up[1]
        ALW_NM_S_31 = np.column_stack([
            col1,
            (1 - 10 ** 3 * sN / (sAe * s_f_c1[1])) * sZx * sf[1, 0] / 10 ** 6
            * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 8
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_1 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_32 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_32 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        long_NM_L = _sortrows1(np.vstack([ALW_NM_L_31, ALW_NM_L_32]))
        long_NM_S = _sortrows1(np.vstack([ALW_NM_S_31, ALW_NM_S_32]))

    # ------------------------------------------------------------------
    # 中柱のM-N曲線（座屈長さが断面せいの4-12倍)＜節点移動あり＞
    # ------------------------------------------------------------------
    if motion == -1 and column_type == 'medium':
        N_limit_up = [L_N_ca2, S_N_ca2]  # 中柱の負担可能軸力

        CN_limit_up = [CL_N_ca32, CS_N_ca32]  # RC部分の許容圧縮力
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3
        CMM = 1

        # N大領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        ALW_NM_L_21 = np.column_stack([
            col1,
            (N_limit_up[0] - col1) * (sMo * (1 - 3 * CN_limit_up[0] / Nk) / CMM)
            / (N_limit_up[0] - CN_limit_up[0])])
        # CFT規準P208のNca2の説明では「短期」とされているが，ここでは長短期で区分している．

        # N大領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        ALW_NM_S_21 = np.column_stack([
            col1,
            (N_limit_up[1] - col1) * (sMo * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM)
            / (N_limit_up[1] - CN_limit_up[1])])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 8
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_2 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_22 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_22 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        medium_NM_L = _sortrows1(np.vstack([ALW_NM_L_21, ALW_NM_L_22]))
        medium_NM_S = _sortrows1(np.vstack([ALW_NM_S_21, ALW_NM_S_22]))

    # ------------------------------------------------------------------
    # 中柱のM-N曲線（座屈長さが断面せいの4-12倍）＜節点移動なし＞
    # ------------------------------------------------------------------
    if motion == 1 and column_type == 'medium':
        # ここではCFTは柱で中間にせん断力は作用しないためにモーメント勾配は単曲率を仮定した．
        N_limit_up = [L_N_ca2, S_N_ca2]

        CN_limit_up = [CL_N_ca32, CS_N_ca32]
        Nk = math.pi ** 2 * (cE * cI / 5 + sE * sIx) / lk ** 2 / 10 ** 3

        # モーメント勾配は単曲率を仮定した．
        porm = 1

        M1 = max(Mi, Mj)
        M2 = min(Mi, Mj)
        M21 = porm * M2 / M1
        CMM = max(0.25, 1 - 0.5 * (1 - M21) * math.sqrt(abs(Ncenter) / Nk))

        # N大領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[0], n_divide, N_limit_up[0])
        ALW_NM_L_21 = np.column_stack([
            col1,
            (N_limit_up[0] - col1) * (sMo * (1 - 3 * CN_limit_up[0] / Nk) / CMM)
            / (N_limit_up[0] - CN_limit_up[0])])
        # CFT規準P208のNca2の説明では「短期」とされているが，ここでは長短期で区分している．

        # N大領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(CN_limit_up[1], n_divide, N_limit_up[1])
        ALW_NM_S_21 = np.column_stack([
            col1,
            (N_limit_up[1] - col1) * (sMo * (1 - 1.5 * CN_limit_up[1] / Nk) / CMM)
            / (N_limit_up[1] - CN_limit_up[1])])

        # 軸力がコンクリートの許容圧縮力以内 0 << N << CN_limit_up
        cMmaxo = Fc[0] * D ** 3 / 8
        Cb = 0.923 - 0.0044 * Fc[0]
        cMmax = (Cb / (Cb + clamda_2 ** 2)) * cMmaxo / 10 ** 6

        # N中領域の長期曲線
        sMo = sZx * sf[0, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[0])
        cM = np.maximum(0, 4 * col1 / (0.9 * 3 * CN_limit_up[0])
                        * (1 - col1 / (0.9 * CN_limit_up[0])) * cMmax)
        ALW_NM_L_22 = np.column_stack([
            col1, (cM + sMo * (1 - 3 * col1 / Nk)) / CMM])

        # N中領域の短期曲線
        sMo = sZx * sf[1, 0] / 10 ** 6
        col1 = _colon(0, n_divide, CN_limit_up[1])
        cM = np.maximum(0, 4 * col1 / (0.9 * 1.5 * CN_limit_up[1])
                        * (1 - col1 / (0.9 * CN_limit_up[1])) * cMmax)
        ALW_NM_S_22 = np.column_stack([
            col1, (cM + sMo * (1 - 1.5 * col1 / Nk)) / CMM])

        medium_NM_L = _sortrows1(np.vstack([ALW_NM_L_21, ALW_NM_L_22]))
        medium_NM_S = _sortrows1(np.vstack([ALW_NM_S_21, ALW_NM_S_22]))

    # ------------------------------------------------------------------
    # 偏心率から検定比を求める
    # ------------------------------------------------------------------

    ei = Mi / (-1 * stress[0, 0]) * 1000
    ej = Mj / (-1 * stress[2, 0]) * 1000

    with np.errstate(divide='ignore', invalid='ignore'):
        e_short_L = np.column_stack(
            [short_NM_L[:, 1] / short_NM_L[:, 0] * 1000, short_NM_L[:, 0]])
        e_short_S = np.column_stack(
            [short_NM_S[:, 1] / short_NM_S[:, 0] * 1000, short_NM_S[:, 0]])

    if timecase == 2:
        ei1_index = int(np.argmin(np.abs(e_short_S[:, 0] - ei)))
        ratio_i1 = -1 * stress[0, 0] / e_short_S[ei1_index, 1]
        ej1_index = int(np.argmin(np.abs(e_short_S[:, 0] - ej)))
        ratio_j1 = -1 * stress[2, 0] / e_short_S[ej1_index, 1]
    elif timecase == 1:
        ei1_index = int(np.argmin(np.abs(e_short_L[:, 0] - ei)))
        ratio_i1 = -1 * stress[0, 0] / e_short_L[ei1_index, 1]
        ej1_index = int(np.argmin(np.abs(e_short_L[:, 0] - ej)))
        ratio_j1 = -1 * stress[2, 0] / e_short_L[ej1_index, 1]

    ratio_i = ratio_i1
    ratio_j = ratio_j1
    if column_type == 'medium':
        with np.errstate(divide='ignore', invalid='ignore'):
            e_medium_L = np.column_stack(
                [medium_NM_L[:, 1] / medium_NM_L[:, 0] * 1000, medium_NM_L[:, 0]])
            e_medium_S = np.column_stack(
                [medium_NM_S[:, 1] / medium_NM_S[:, 0] * 1000, medium_NM_S[:, 0]])
        if timecase == 2:
            ei2_index = int(np.argmin(np.abs(e_medium_S[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_medium_S[ei2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_medium_S[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_medium_S[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_j = max(ratio_j1, ratio_j2)
        elif timecase == 1:
            ei2_index = int(np.argmin(np.abs(e_medium_L[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_medium_L[ei2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_medium_L[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_medium_L[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_j = max(ratio_j1, ratio_j2)
        else:
            print("ERROR = '柱の水平移動拘束設定ミス'")
            raise ValueError('柱の水平移動拘束設定ミス')
    elif column_type == 'long':
        with np.errstate(divide='ignore', invalid='ignore'):
            e_long_L = np.column_stack(
                [long_NM_L[:, 1] / long_NM_L[:, 0] * 1000, long_NM_L[:, 0]])
            e_long_S = np.column_stack(
                [long_NM_S[:, 1] / long_NM_S[:, 0] * 1000, long_NM_S[:, 0]])
        if timecase == 2:
            ei2_index = int(np.argmin(np.abs(e_long_S[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_long_S[ei2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_long_S[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_long_S[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_j = max(ratio_j1, ratio_j2)
        elif timecase == 1:
            ei2_index = int(np.argmin(np.abs(e_long_L[:, 0] - ei)))
            ratio_i2 = -1 * stress[0, 0] / e_long_L[ei2_index, 1]
            ej2_index = int(np.argmin(np.abs(e_long_L[:, 0] - ej)))
            ratio_j2 = -1 * stress[2, 0] / e_long_L[ej2_index, 1]
            ratio_i = max(ratio_i1, ratio_i2)
            ratio_j = max(ratio_j1, ratio_j2)
        else:
            print("ERROR = '柱の水平移動拘束設定ミス'")
            raise ValueError('柱の水平移動拘束設定ミス')

    # 柱の座屈長さに応じてplotする関数を決める
    if plotCFT == 0:
        pass
    else:
        # NOTE: 原典565-648行の figure/plot/legend/title は移植対象外 (何もしない)
        pass

    # せん断に対する検定も行えるようにする．(原典651行: 未実装のまま)
    return ratio_i, ratio_j


# ---------------------------------------------------------------------------
# CFT_Rcolumn_text.m
# ---------------------------------------------------------------------------

def CFT_Rcolumn_text(stress, lk, Form, Fc, SN, timecase, motion,
                     ele_no, section_no, LOAS_CASE_NAME, ratio, section_name):
    """CFT_Rcolumn_text.m の逐語移植.

    CFT円形柱の断面算定詳細文の生成。
    返り値: list of str (MATLAB strvcat 相当)
    """
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    ratio = np.atleast_2d(np.asarray(ratio, dtype=float))
    lk = float(lk)

    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 4] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 5] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # 文字の定義

    # 長短期設定
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        print("ERROR = '長短期設定ミス'")

    # 外形情報
    D = Form[0]
    t = Form[1]
    # 鉄骨情報（断面積および強軸弱軸の断面係数）
    sForm = np.concatenate([[3.0], Form])
    sAe = steelForm(sForm, 1) * 100  # mm2
    sIx = steelForm(sForm, 2) * 10000  # mm4
    ixs = steelForm(sForm, 4) * 10  # mm
    sZx = steelForm(sForm, 6) * 1000  # mm3

    # 充填コンクリート情報
    cD = D - 2 * t  # mm
    cA = cD ** 2 * math.pi / 4  # mm2
    cI = cD ** 4 * math.pi / 64  # mm4
    cixs = math.sqrt(cI / cA)  # mm

    # 長柱・中柱・短柱の判定
    long_column = lk / D
    if long_column > 12:
        column_type = '長柱'
    elif long_column <= 4:
        column_type = '短柱'
    else:
        column_type = '中柱'

    text = []
    text.append('CFT柱(鋼管)の断面算定内容（最大検定値の検定内容）')
    text.append('軸力と曲げに対して断面算定を行う．せん断に対してはシアスパン比の確認を行う．')
    text.append('　　')

    text.append('断面符号：' + str(section_name) + '断面番号：' + _num2str(section_no)
                + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('CFT柱サイズ[mm]：○-' + _num2str(D) + 'x' + _num2str(t))
    text.append('　　')

    text.append('*****使用材料（鉄骨およびコンクリート）*****')
    text.append('鉄骨：SN' + _num2str(SN))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    text.append('　　')

    # 許容応力度関係
    sf = ALST_S_CFT(np.array([SN, t], dtype=float))  # 鉄骨の許容応力度

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    cE = n[0]
    sE = 205000
    n = n[1]

    text.append('*鋼材情報')
    # NOTE: 原典72行 「E：20500 N/mm2」は原文のまま (205000の誤記とみられる)
    text.append('F値：' + _num2str(sf[1, 0], '%15.0f') + 'N/mm2' + '　E：20500 N/mm2')
    text.append('*コンクリート情報')
    text.append('Fc：' + _num2str(Fc, '%15.0f') + 'N/mm2　ヤング係数比n：'
                + _num2str(n, '%15.0f'))

    # text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # その１：径厚比の検討←追記すること

    text.append('　　')
    text.append('*****設計用応力*****')
    text.append('曲げの検定方法：偏心距離による軸力余裕度による')
    text.append('せん断の検定方法：シアスパン比による確認を行う')

    text.append('　　')
    text.append('*設計用応力　[' + str(t_case) + ']　i　端')
    text.append('軸力　：' + _num2str(Fxx[0] / 1000, '%15.1f')
                + '　[kN]　　曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f')
                + '　[kNm]　　曲げMz　：' + _num2str(Mz[0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]'
                + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')

    text.append('　　')
    text.append('*設計用応力　[' + str(t_case) + ']　中央')
    text.append('軸力　：' + _num2str(Fxx[1] / 1000, '%15.1f')
                + '　[kN]　　曲げMy　：' + _num2str(My[1] / 10 ** 6, '%15.1f')
                + '　[kNm]　　曲げMz　：' + _num2str(Mz[1] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]'
                + '　　せん断力Qy　：' + _num2str(Fy[1] / 10 ** 3, '%15.1f') + '　[kN]')

    text.append('　　')
    text.append('*設計用応力　[' + str(t_case) + ']　j　端')
    text.append('軸力　：' + _num2str(Fxx[2] / 1000, '%15.1f')
                + '　[kN]　　曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f')
                + '　[kNm]　　曲げMz　：' + _num2str(Mz[2] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]'
                + '　　せん断力Qy　：' + _num2str(Fy[2] / 10 ** 3, '%15.1f') + '　[kN]')

    text.append('　　')
    text.append('　　')

    # ----------------------------------------------------------------------
    text.append('*****許容耐力・検定比*****')
    text.append('*算定条件：' + column_type)
    # NOTE: 原典109行 「柱長さ(m)」表記だが lk はmm単位のまま出力される
    text.append('柱長さ(m)　：' + _num2str(lk, '%15.1f')
                + '柱径(mm)　：' + _num2str(D, '%15.1f'))
    text.append('　　')
    text.append('*シアスパン比の確認')

    # シアスパン比の確認
    Mi = np.array([stress[0, 4], stress[0, 5],
                   math.sqrt(stress[0, 4] ** 2 + stress[0, 5] ** 2)])
    Mc = np.array([stress[1, 4], stress[1, 5],
                   math.sqrt(stress[1, 4] ** 2 + stress[1, 5] ** 2)])
    Mj = np.array([stress[2, 4], stress[2, 5],
                   math.sqrt(stress[2, 4] ** 2 + stress[2, 5] ** 2)])

    Qi = math.sqrt(stress[0, 1] ** 2 + stress[0, 2] ** 2)
    Qc = math.sqrt(stress[1, 1] ** 2 + stress[1, 2] ** 2)
    Qj = math.sqrt(stress[2, 1] ** 2 + stress[2, 2] ** 2)

    maxM = max(abs(Mi[2]), abs(Mc[2]), abs(Mj[2]))
    maxQ = max(abs(Qi), abs(Qc), abs(Qj))

    text.append('部材最大曲げMmax(kNm)：' + _num2str(maxM, '%15.1f'))
    text.append('部材最大せん断Qmax(kN)：' + _num2str(maxQ, '%15.1f'))

    if maxQ == 0:
        text.append('せん断力は0なので検定不要')
    else:
        ratioQ = maxM / (maxQ * (D / 1000))
        text.append('シアスパン比：' + _num2str(ratioQ, '%15.2f'))

    # NM余裕度の検定
    with np.errstate(divide='ignore', invalid='ignore'):
        ALW_N = Fxx / 1000 / ratio[:, 0]
    ei = Mi[2] / (-1 * stress[0, 0]) * 1000
    ec = Mc[2] / (-1 * stress[1, 0]) * 1000
    ej = Mj[2] / (-1 * stress[2, 0]) * 1000

    text.append('　　')

    text.append('*許容耐力　[' + str(t_case) + ']　i　端')
    text.append('許容軸力N（等偏心距離時）　：' + _num2str(ALW_N[0], '%15.1f') + '　[kN]')
    text.append('偏心距離　：' + _num2str(ei, '%15.2f') + '　[mm]')
    text.append('検定比　[N(e)]：' + _num2str(ratio[0, 0], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + str(t_case) + ']　中央')
    text.append('許容軸力N（等偏心距離時）　：' + _num2str(ALW_N[1], '%15.1f') + '　[kN]')
    text.append('偏心距離　：' + _num2str(ec, '%15.2f') + '　[mm]')
    text.append('検定比　[N(e)]：' + _num2str(ratio[1, 0], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + str(t_case) + ']　j　端')
    text.append('許容軸力N（等偏心距離時）　：' + _num2str(ALW_N[2], '%15.1f') + '　[kN]')
    text.append('偏心距離　：' + _num2str(ej, '%15.2f') + '　[mm]')
    text.append('検定比　[N(e)]：' + _num2str(ratio[2, 0], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# CFT_ratio_analysis.m
# ---------------------------------------------------------------------------

def CFT_ratio_analysis(sectionsize, buck_length, stress, timecase, Fc, ele_no,
                       maxratios, maxratios_text, section_no, LOAS_CASE_NAME,
                       pick_section_name):
    """CFT_ratio_analysis.m の逐語移植.

    CFT柱の断面検定 (円形16000 / 角形17000)。

    入力:
      sectionsize : 断面情報 (m単位, 内部で×1000)。後ろから2番目が形状マーカー
                    (16000:CFT円形, 17000:CFT角形)
      buck_length : 座屈長ベクトル (3要素, mm)。原典引数名は ele_length。
                    NOTE: 原典17/36行 ele_lengthCFT=min(ele_length(1:2)) を
                    そのまま再現 — minは非安全側の可能性 (設計要確認)
      stress      : 3x5 [N Fy Fz My Mz] (kN, kNm) 行=i端/中央/j端
      timecase    : 1長期 / >=10短期
      Fc          : [コンクリートFc, 鋼管規格SN] の2要素
      ele_no, maxratios(長さ2), maxratios_text, section_no,
      LOAS_CASE_NAME, pick_section_name : rc_check.RC_ratio_analysis と同様

    返り値: (ratio_output(3x4), maxratios(長さ2), maxratios_text)
      円形: 列1=NM検定比(i/中央/j), 列3=せん断(QD/M)
      角形: 列1=ratio_i(3行同値), 列3=ratio_j(3行同値)
            ※原典どおり角形の列3はせん断ではなくj端NM検定比
    """
    stress = np.array(np.atleast_2d(stress), dtype=float, copy=True)
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    maxratios = np.array(np.asarray(maxratios, dtype=float).ravel(), copy=True)
    buck_length = np.asarray(buck_length, dtype=float).ravel()

    stress[:, 0] = stress[:, 0] * -1
    sectionsize = sectionsize * 1000
    ratio_output = np.zeros((3, 4))

    # 柱として検討
    if sectionsize[len(sectionsize) - 2] == 16000:  # CFT (円形)

        sForm = sectionsize[0:2]  # 鋼管サイズD-t
        SN = Fc[1]  # 鋼管規格
        # NOTE: 冒頭で反転済みのN列を再度-1倍 (二重反転で入力時の符号に戻る)
        stressCFT = np.column_stack([-1 * stress[:, 0], stress[:, 1:3],
                                     np.zeros(3), stress[:, 3:5]])
        # NOTE: 原典 ele_lengthCFT=min(ele_length(1:2)) — minは非安全側の可能性(設計要確認)
        ele_lengthCFT = min(buck_length[0], buck_length[1])  # lk

        # 許容曲げモーメントに対して2軸周りの合算Mで検定を行う
        # せん断の検定
        ratioNM, ratioQ = SA_CFTSRcolumn_AIJ(
            stressCFT, ele_lengthCFT, sForm, Fc[0], SN, timecase, 1, 0)
        ratio_output[:, 0] = ratioNM
        ratio_output[:, 2] = ratioQ

        if np.max(ratio_output) > np.max(maxratios[1]):
            maxratios[0] = ele_no
            maxratios[1] = np.max(ratio_output)

            maxratios_text = CFT_Rcolumn_text(
                stressCFT, ele_lengthCFT, sForm, Fc[0], SN, timecase, 0,
                ele_no, section_no, LOAS_CASE_NAME, ratio_output,
                pick_section_name)

    elif sectionsize[len(sectionsize) - 2] == 17000:  # CFT (角形)

        sForm = sectionsize[0:3]  # 鋼管サイズD-t
        SN = Fc[1]  # 鋼管規格
        stressCFT = np.column_stack([-1 * stress[:, 0], stress[:, 1:3],
                                     np.zeros(3), stress[:, 3:5]])
        # NOTE: 原典 ele_lengthCFT=min(ele_length(1:2)) — minは非安全側の可能性(設計要確認)
        ele_lengthCFT = min(buck_length[0], buck_length[1])  # lk

        # 許容曲げモーメントに対して2軸周りの合算Mで検定を行う
        # せん断の検定
        ratio_i, ratio_j = SA_CFTSBcolumn_AIJ(
            stressCFT, ele_lengthCFT, sForm, Fc[0], SN, timecase, 1, 0)
        # MATLAB: ratio_output(:,1)=スカラ → 列全体に代入
        ratio_output[:, 0] = ratio_i
        ratio_output[:, 2] = ratio_j

        if np.max(ratio_output) > np.max(maxratios[1]):
            maxratios[0] = ele_no
            maxratios[1] = np.max(ratio_output)

            # NOTE: 原典47-48行で詳細文の呼び出しはコメントアウトされている
            #       (角形は検定値のみ・maxratios_textは更新しない)
            # maxratios_text = CFT_Rcolumn_text(stressCFT,ele_lengthCFT,sForm,
            #     Fc(1),SN,timecase,0, ele_no, section_no, LOAS_CASE_NAME,
            #     ratio_output, pick_section_name)

    else:
        print('ERROR:CFTの材料割当ミス')
        raise ValueError('ERROR:CFTの材料割当ミス')

    return ratio_output, maxratios, maxratios_text
