# -*- coding: utf-8 -*-
"""S造NMMQ断面検定 (*_analysis / *_analysis_text / 引張材 / S_ratio_analysis) の逐語移植.

元コード:
  msrc/structural_function/S/S_section_analysis/NMMQ_analysis/*.m
  msrc/structural_function/S/S_section_analysis/NMMQ_analysis_text/*.m
  msrc/structural_function/S/S_section_analysis/tension_analysis/*.m
  msrc/structural_function/S/S_section_analysis/tension_analysis_text/*.m
  msrc/privatetool_function/MIDAS/ratio/S/S_ratio_analysis.m

共通規約 (元コードから):
  - 断面力 stress は kN・m 系。列 = [軸力N, せん断力弱軸方向Fy, せん断力強軸方向Fz,
    曲げモーメント強軸My, 曲げモーメント弱軸Mz] (MIDAS準拠)。
    行 = 要素あたり3行 (i端/中央/j端)。
  - 内部で N/mm2 系へ変換 (N→×10^3, M→×10^6)。断面性能は cm 系 (B*関数出力)。
  - length = [座屈長さ(強)m, 座屈長さ(弱)m, 圧縮フランジ間距離(横座屈長さ)m]。
  - timecase: 1=長期, 9=長期扱い(H_analysis系のみ許容), >=10=短期。
  - SN = [系統, F値(または鋼種)] 系統1=SN系(ALST_S参照), 2=冷間成形角形鋼管等(F値直接)。
    BOX系では SN(3) = 1:STKR, 2:BCR, 3:BCP, 4:BSH で c_up(応力割増係数配列)を参照し、
    短期時に 設計応力 = 長期 + c_up×(短期-長期) とする (t<6mm は割増なし)。
  - ratio 出力は [ratio_cb ratio_sz ratio_sy ratio_combi] の 行数×4列。
  - MATLABの `if ベクトル>0` は全要素が真のときのみ真 → np.all で再現。
  - *_analysis_text は strvcat による行の積み上げ → Python版は list[str] を返す
    (strvcatの空白パディングは行わない。書式・桁数は num2str 互換で維持)。

元コードの怪しい挙動もそのまま再現している (改変禁止のため)。詳細は各関数コメント参照。
"""

import functools
import math

import numpy as np

from .util import find_index
from .alst import ALST_S
from .secprops import (
    BH, BBOX, BBOX_H, BP, BC, BC2, BCC, BCT, BL, BL2, BSB, BSR, BHwCP,
)


# ---------------------------------------------------------------------------
# 内部ヘルパー (MATLAB挙動再現)
# ---------------------------------------------------------------------------

def _matlabenv(func):
    """MATLAB同様、0除算等でエラーとせず Inf/NaN を返す環境で実行する."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            return func(*args, **kwargs)
    return wrapper


def _cosd(x):
    """MATLAB cosd."""
    return math.cos(math.radians(x))


def _m(v, i):
    """MATLAB風 1-based indexing。スカラは長さ1配列として扱う
    (範囲外は MATLAB 同様エラー: IndexError)."""
    return np.atleast_1d(v)[int(i) - 1]


def _mset(lst, i, val):
    """MATLAB風 1-based 代入 (必要なら0でパディングして配列を伸ばす)."""
    while len(lst) < i:
        lst.append(0.0)
    lst[i - 1] = val


def _num2str(x, fmt=None):
    """MATLAB num2str 相当.

    fmt指定時: sprintf(fmt) を各要素に適用して連結後、前後空白を除去
    (MATLAB num2str(A,format) と同じ)。
    fmt省略時: 整数値は '%d'、その他は有効桁 max(floor(log10|x|)+5, 5) の %g
    (MATLAB既定と同じ)。
    """
    if fmt is not None:
        arr = np.atleast_1d(np.asarray(x, dtype=float)).ravel()
        s = ''.join((fmt % float(v)) for v in arr)
        s = s.replace('inf', 'Inf').replace('nan', 'NaN')
        return s.strip()
    xv = float(x)
    if math.isnan(xv):
        return 'NaN'
    if math.isinf(xv):
        return 'Inf' if xv > 0 else '-Inf'
    if xv == int(xv) and abs(xv) < 1e15:
        return '%d' % int(xv)
    d = math.floor(math.log10(abs(xv)))
    prec = max(d + 5, 5)
    return '%.*g' % (prec, xv)


def _maxratio_index(ratio):
    """MATLABの
        [max_ratio index1] = max(ratio); [max_ratio index2] = max(max_ratio);
        max_index = index1(index2);
    相当。1-based の max_index を返す (最初の最大値を採用)。"""
    ratio = np.atleast_2d(np.asarray(ratio, dtype=float))
    index1 = ratio.argmax(axis=0)          # 各列の最大行 (0-based)
    max_ratio = ratio.max(axis=0)
    index2 = int(max_ratio.argmax())       # 最大となる列 (0-based)
    return int(index1[index2]) + 1


def _st_index(max_index):
    """max_index から i端行 (1-based) を求める共通ロジック."""
    if max_index % 3 == 0:
        return max_index - 2
    return (max_index - (max_index % 3)) + 1


def _timecase_stop():
    print("ERROR='長短期設定ミス'")
    raise RuntimeError('長短期設定ミス')


# ===========================================================================
# NMMQ_analysis
# ===========================================================================

# ---------------------------------------------------------------------------
# H_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def H_analysis(sectionsize, length, stress, timecase, SN,
               H_beam_RCsupport, cb_judge, judge_H):
    """H鋼の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定.

    入力する断面力はkN・m系 (MIDAS準拠):
    [軸力N せん断力弱軸方向Fy せん断力強軸方向Fz 曲げモーメント強軸My 曲げモーメント弱軸Mz]
    SN=[1 400] or [2 295] SN系かBCP系かでSNの違いあり！
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()
    cb_judge = np.asarray(cb_judge, dtype=float).ravel()

    # 鋼材断面性能 (入力断面サイズはmm単位)
    A = BH(sectionsize, 1)
    ixs = BH(sectionsize, 4)
    iys = BH(sectionsize, 5)
    Zx = BH(sectionsize, 6)
    Zy = BH(sectionsize, 7)
    ib = BH(sectionsize, 8)
    eta = BH(sectionsize, 9)
    Af = BH(sectionsize, 10)
    Aw = BH(sectionsize, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    elif timecase == 9:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3   # 軸力[N]
    My = stress[:, 3] * 10 ** 6    # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6    # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3    # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3    # せん断力弱軸方向[N]

    if judge_H == 2:
        Mz = 0.0  # MATLAB同様スカラ0になる

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        if H_beam_RCsupport == 1 and cb_judge[0] == 1:  # 剛床成立なのでRCスラブあり
            # 梁の両端，中央全てのモーメントが0以上であればfbはftとする
            if (My[i * 3 - 3] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 2] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 1] * _cosd(cb_judge[1]) >= 0):
                fby[i * 3 - 3:3 * i] = ft
                if judge_H == 1:
                    fbz[i * 3 - 3:3 * i] = ft
                else:
                    fbz[i * 3 - 3:3 * i] = np.inf
                CC[i * 3 - 3:3 * i] = 1.0
            else:
                ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
                abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

                # 中央部が最大の場合，C=1
                # (元コード: abs(My(i*3-1) >= abs(My(i*3))) は比較結果のabs — 括弧位置のまま再現)
                if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                    C = 1
                elif ABSM == abs_m:  # 単曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 - 1.05 * M + 0.3 * M ** 2
                else:  # 複曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 + 1.05 * M + 0.3 * M ** 2
                CC[i * 3 - 3:3 * i] = min(2.3, C)

                fb1 = 89000 * ib / eta / (length[2] * 100)
                fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

                fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
                if judge_H == 1:
                    fbz[i * 3 - 3:3 * i] = ft
                else:
                    fbz[i * 3 - 3:3 * i] = np.inf
        else:
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            # 中央部が最大の場合，C=1
            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)
            if length[2] == 0:
                fb1 = ft
            else:
                fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            if judge_H == 1:
                fbz[i * 3 - 3:3 * i] = ft
            else:
                fbz[i * 3 - 3:3 * i] = np.inf

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (A * 100)
    if np.all(sigma_cx > 0):  # MATLAB: if sigma_cx>0 (全要素判定)
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 2 * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    # sigma_combi = sqrt((|σcx|+|σby|+|σbz|)^2 + 3*max(τz,τy)^2)  (元コードで比は常に0)
    ratio_combi = np.zeros(3)
    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# BOX_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def BOX_analysis(sectionsize, length, stress, timecase, SN, stress_L, c_up):
    """□鋼管の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定.

    冷間成形角形鋼管 (SN(1)==2) は SN(3)=1:STKR/2:BCR/3:BCP/4:BSH で
    c_up配列から応力割増係数を取り、短期時に
    設計応力 = 長期 + c_up×(短期−長期) とする (板厚<6mmは割増なし)。
    フィレット半径は STKR:2t, BCR:2.5t, BCP:3.5t, BSH:1.25t。
    """
    sectionsize = list(np.asarray(sectionsize, dtype=float).ravel())
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 長期許容応力度計算
    if SN[0] == 1:  # SN系で応力の割増はなし
        t = sectionsize[2]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
        c_up = 1.0
    elif SN[0] == 2:  # 冷間角鋼なので応力の割増あり
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        # 応力割増係数の設定
        c_up = _m(np.asarray(c_up, dtype=float).ravel(), SN[2])
        if sectionsize[2] < 6:
            c_up = 1.0
        if SN[2] == 1:  # STKR
            _mset(sectionsize, 5, sectionsize[2] * 2)
        elif SN[2] == 2:  # BCR
            _mset(sectionsize, 5, sectionsize[2] * 2.5)
        elif SN[2] == 3:  # BCP
            _mset(sectionsize, 5, sectionsize[2] * 3.5)
        elif SN[2] == 4:  # BSH
            _mset(sectionsize, 5, sectionsize[2] * 1.25)
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 鋼材断面性能 (元コードは BBOX 呼び出しをコメントアウトし BBOX_H を使用)
    a = BBOX_H(sectionsize, 1)
    ixs = BBOX_H(sectionsize, 4)
    iys = BBOX_H(sectionsize, 5)
    Zx = BBOX_H(sectionsize, 6)
    Zy = BBOX_H(sectionsize, 7)

    Awx = BBOX_H(sectionsize, 11)
    Awy = BBOX_H(sectionsize, 12)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        stress_L = np.atleast_2d(np.asarray(stress_L, dtype=float))
        # 断面力取り込み（単位をN/mm2系にする．）
        Fxx = (stress_L[:, 0] + c_up * (stress[:, 0] - stress_L[:, 0])) * 10 ** 3
        My = (stress_L[:, 3] + c_up * (stress[:, 3] - stress_L[:, 3])) * 10 ** 6
        Mz = (stress_L[:, 4] + c_up * (stress[:, 4] - stress_L[:, 4])) * 10 ** 6
        Fz = (stress_L[:, 2] + c_up * (stress[:, 2] - stress_L[:, 2])) * 10 ** 3
        Fy = (stress_L[:, 1] + c_up * (stress[:, 1] - stress_L[:, 1])) * 10 ** 3
    elif timecase == 1:
        timecase = 1
        # 断面力取り込み（単位をN/mm2系にする．）
        Fxx = stress[:, 0] * 10 ** 3
        My = stress[:, 3] * 10 ** 6
        Mz = stress[:, 4] * 10 ** 6
        Fz = stress[:, 2] * 10 ** 3
        Fy = stress[:, 1] * 10 ** 3
    else:
        _timecase_stop()

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Awx * 100)
    sigma_sy = np.abs(Fy) / (Awy * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs
    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# P_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def P_analysis(sectionsize, length, stress, timecase, SN):
    """○鋼管の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BP(sectionsize, 1)
    ixs = BP(sectionsize, 4)
    iys = BP(sectionsize, 5)  # noqa: F841 (元コードでも未使用)
    Zx = BP(sectionsize, 6)
    Zy = BP(sectionsize, 7)  # noqa: F841 (元コードでも未使用)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[1]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮 (元コード: lamday も ixs で除している)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fb = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fb = 1.5 * fb

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_b = np.sqrt(np.abs(My) ** 2 + np.abs(Mz) ** 2) / (Zx * 1000)
    ratio_b = sigma_b / fb

    ratio_cb = ratio_cx + ratio_b

    sigma_s = np.sqrt(np.abs(Fz) ** 2 + np.abs(Fy) ** 2) / (a / 2 * 100)  # 231030(金澤)せん断断面積を1/2に変更

    ratio_s = sigma_s / fs

    sigma_combi = np.sqrt((sigma_cx + sigma_b) ** 2 + 3 * sigma_s ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_s, ratio_s, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# C_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def C_analysis(sectionsize, length, stress, timecase, SN):
    """溝形鋼の断面算定 (BC参照)."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC(sectionsize, 1)
    ixs = BC(sectionsize, 4)
    iys = BC(sectionsize, 5)
    Zx = BC(sectionsize, 6)
    Zy = BC(sectionsize, 7)

    Af = BC(sectionsize, 10)
    Aw = BC(sectionsize, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fb1 = 89000 * (Af * 100) / (length[2] * 1000 * sectionsize[0])

    fby = min(ft, fb1)
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 2 * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# CC_analysis.m (ファイル内の関数名は C_analysis だがファイル名で呼ばれるため CC_analysis)
# ---------------------------------------------------------------------------

@_matlabenv
def CC_analysis(sectionsize, length, stress, timecase, SN):
    """軽量形鋼の断面算定 (BCC参照).

    注意: 元ファイル CC_analysis.m 内の関数宣言は C_analysis と重複しているが、
    MATLABではファイル名で呼ばれるため CC_analysis として移植。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BCC(sectionsize, 1)
    ixs = BCC(sectionsize, 4)
    iys = BCC(sectionsize, 5)
    Zx = BCC(sectionsize, 6)
    Zy = BCC(sectionsize, 7)

    Af = BCC(sectionsize, 10)
    Aw = BCC(sectionsize, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fb1 = 89000 * (Af * 100) / (length[2] * 1000 * sectionsize[0])

    fby = min(ft, fb1)
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 2 * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# SB_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def SB_analysis(sectionsize, length, stress, timecase, SN):
    """■鋼管（ムク）,FBの軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSB(sectionsize, 1)
    ixs = BSB(sectionsize, 4)
    iys = BSB(sectionsize, 5)
    Zx = BSB(sectionsize, 6)
    Zy = BSB(sectionsize, 7)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = min(sectionsize[0], sectionsize[1])
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    D = sectionsize[0]
    B = sectionsize[1]
    st_size1 = stress.shape[0]
    if D == B:
        fbz = ft
        fby = ft
    elif D > B:
        ib = BSB([D, B], 8)
        eta = BSB([D, B], 9)
        fby = np.zeros(st_size1)
        fbz = np.zeros(st_size1)
        CC = np.zeros(st_size1)
        for i in range(1, int(st_size1 / 3) + 1):
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            # 中央部が最大の場合，C=1
            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fbz[i * 3 - 3:3 * i] = ft
    else:
        ib = BSB([B, D], 8)
        eta = BSB([B, D], 9)
        fby = np.zeros(st_size1)
        fbz = np.zeros(st_size1)
        CC = np.zeros(st_size1)
        for i in range(1, int(st_size1 / 3) + 1):
            ABSM = abs(Mz[i * 3 - 3]) + abs(Mz[i * 3 - 2]) + abs(Mz[i * 3 - 1])
            abs_m = abs(Mz[i * 3 - 3] + Mz[i * 3 - 2] + Mz[i * 3 - 1])

            # 中央部が最大の場合，C=1
            if abs(Mz[i * 3 - 2]) >= abs(Mz[i * 3 - 3]) and abs(Mz[i * 3 - 2] >= abs(Mz[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])) / max(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])) / max(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            # 元コード: この分岐のみ CC(i) を使用 (C(i)ではない) — そのまま再現
            fb2 = (2 / 3 - 4 / 15 / CC[i - 1] * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fbz[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fby[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * np.asarray(fby)
        fbz = 1.5 * np.asarray(fbz)

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (a * 100)
    sigma_sy = np.abs(Fy) / (a * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# SR_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def SR_analysis(sectionsize, length, stress, timecase, SN):
    """●鋼管（ムク）の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定."""
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSR(sectionsize, 1)
    ixs = BSR(sectionsize, 4)
    Zx = BSR(sectionsize, 6)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[0]
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs

    lamdadfc = lamdax
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fb = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fb = 1.5 * fb

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_b = np.sqrt(np.abs(My) ** 2 + np.abs(Mz) ** 2) / (Zx * 1000)
    ratio_b = sigma_b / fb

    ratio_cb = ratio_cx + ratio_b

    sigma_s = np.sqrt(np.abs(Fz) ** 2 + np.abs(Fy) ** 2) / (a * 100)

    ratio_s = sigma_s / fs

    sigma_combi = np.sqrt((sigma_cx + sigma_b) ** 2 + 3 * sigma_s ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_s, ratio_s, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# CT_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def CT_analysis(sectionsize, length, stress, timecase, SN):
    """CT鋼の軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定.

    幅厚比規定により圧縮時は有効断面 (H_e=min(H,235/sqrt(F)*tf*2),
    B_e=min(B,235/sqrt(F)*tw))、正曲げ/負曲げで有効断面係数を使い分ける。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    ratio_rows = []
    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 鋼材断面性能 (入力断面サイズはmm単位)
    # 幅厚比に関する規定の検討
    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    H_e = min(H, 235 / math.sqrt(F) * tf * 2)
    B_e = min(B, 235 / math.sqrt(F) * tw)

    a_t = BCT(sectionsize, 1)
    a_c = BCT([H_e, B_e, tw, tf], 1)

    # 圧縮時の断面性能なので有効断面
    ixs = BCT([H_e, B_e, tw, tf], 4)
    iys = BCT([H_e, B_e, tw, tf], 5)

    # 正曲げ時はフランジ側が有効部分のみ
    Zx_plus = min(BCT([H, B_e, tw, tf], 6), BCT([H, B_e, tw, tf], 7))
    # 負曲げ時はウェブ側が有効部分のみ
    Zx_minus = min(BCT([H_e, B, tw, tf], 6), BCT([H_e, B, tw, tf], 7))

    # 弱軸周りは全て有効とする
    Zy = BCT(sectionsize, 8)

    # 正曲げ時
    ib_t = BCT([H, B_e, tw, tf], 12)
    eta_t = BCT([H, B_e, tw, tf], 13)

    # 負曲げ時
    ib_b = BCT([H_e, B, tw, tf], 14)
    eta_b = BCT([H_e, B, tw, tf], 15)

    Af = BCT(sectionsize, 10)
    Aw = BCT(sectionsize, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby_t = np.zeros(st_size1)
    fby_b = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
        abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

        # 中央部が最大の場合，C=1
        if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
            C = 1
        elif ABSM == abs_m:  # 単曲率曲げ
            M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
            C = 1.75 - 1.05 * M + 0.3 * M ** 2
        else:  # 複曲率曲げ
            M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
            C = 1.75 + 1.05 * M + 0.3 * M ** 2
        CC[i * 3 - 3:3 * i] = min(2.3, C)

        fb1_t = 89000 * ib_t / eta_t / (length[2] * 100)
        fb2_t = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib_t) ** 2 / Lamda ** 2) * F

        fb1_b = 89000 * ib_b / eta_b / (length[2] * 100)
        fb2_b = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib_b) ** 2 / Lamda ** 2) * F

        fby_t[i * 3 - 3:3 * i] = min(ft, max(fb1_t, fb2_t))
        fby_b[i * 3 - 3:3 * i] = min(ft, max(fb1_b, fb2_b))

        fbz[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby_t = 1.5 * fby_t
        fby_b = 1.5 * fby_b
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    for i in range(1, st_size1 + 1):
        if Fxx[i - 1] > 0:
            sigma_cx = Fxx[i - 1] / (a_t * 100)
            ratio_cx = abs(sigma_cx) / ft
        else:
            sigma_cx = Fxx[i - 1] / (a_c * 100)
            ratio_cx = abs(sigma_cx) / fc

        if My[i - 1] > 0:
            sigma_by = abs(My[i - 1]) / (Zx_plus * 1000)
            ratio_by = sigma_by / fby_t[i - 1]
        else:
            sigma_by = abs(My[i - 1]) / (Zx_minus * 1000)
            ratio_by = sigma_by / fby_b[i - 1]

        sigma_bz = abs(Mz[i - 1]) / (Zy * 1000)
        ratio_bz = sigma_bz / fbz[i - 1]

        ratio_cb = ratio_cx + ratio_by + ratio_bz

        sigma_sz = abs(Fz[i - 1]) / (Aw * 100)
        sigma_sy = abs(Fy[i - 1]) / (Af * 100)

        ratio_sz = sigma_sz / fs
        ratio_sy = sigma_sy / fs

        sigma_combi = math.sqrt((abs(sigma_cx) + abs(sigma_by) + abs(sigma_bz)) ** 2
                                + 3 * max(sigma_sz, sigma_sy) ** 2)
        ratio_combi = sigma_combi / ft
        ratio_rows.append([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    return np.array(ratio_rows, dtype=float)


# ---------------------------------------------------------------------------
# L_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def L_analysis(sectionsize, length, stress, timecase, SN):
    """山形鋼の断面算定 (BL参照)."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL(sectionsize, 1)
    ixs = BL(sectionsize, 4)
    iys = BL(sectionsize, 5)
    Zx = BL(sectionsize, 6)
    Zy = BL(sectionsize, 7)

    Aw = BL(sectionsize, 11)
    Af = BL(sectionsize, 10)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[2]  # 元コード: max(sectionsize(3)) — 単一要素のmax
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# HT_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def HT_analysis(sectionsize, length, stress, timecase, SN,
                H_beam_RCsupport, cb_judge):
    """テーパーH鋼の断面算定.

    sectionsize は i端断面7要素 + j端断面7要素 の計14要素。
    曲げ許容応力度はテーパー中央 (両端平均) 断面の性能で算定し、
    検定は i端/中央/j端 それぞれの平均断面性能で行う。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()
    cb_judge = np.asarray(cb_judge, dtype=float).ravel()

    sectionsize2 = (sectionsize[0:7] + sectionsize[7:14]) / 2
    # 鋼材断面性能 (入力断面サイズはmm単位)
    A = BH(sectionsize2, 1)
    ixs = BH(sectionsize2, 4)
    iys = BH(sectionsize2, 5)
    Zx = BH(sectionsize2, 6)
    Zy = BH(sectionsize2, 7)
    ib = BH(sectionsize2, 8)
    eta = BH(sectionsize2, 9)
    Af = BH(sectionsize2, 10)
    Aw = BH(sectionsize2, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize2[2], sectionsize2[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        if H_beam_RCsupport == 1 and cb_judge[0] == 1:  # 剛床成立なのでRCスラブあり
            # 梁の両端，中央全てのモーメントが0以上であればfbはftとする
            if (My[i * 3 - 3] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 2] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 1] * _cosd(cb_judge[1]) >= 0):
                fby[i * 3 - 3:3 * i] = ft
                fbz[i * 3 - 3:3 * i] = ft
                CC[i * 3 - 3:3 * i] = 1.0
            else:
                ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
                abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

                if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                    C = 1
                elif ABSM == abs_m:  # 単曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 - 1.05 * M + 0.3 * M ** 2
                else:  # 複曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 + 1.05 * M + 0.3 * M ** 2
                CC[i * 3 - 3:3 * i] = min(2.3, C)

                fb1 = 89000 * ib / eta / (length[2] * 100)
                fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

                fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
                fbz[i * 3 - 3:3 * i] = ft
        else:
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fbz[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = np.zeros(0)
    ratio_cx = np.zeros(3)
    sigma_by = np.zeros(3)
    sigma_bz = np.zeros(3)
    ratio_by = np.zeros(3)
    ratio_bz = np.zeros(3)
    ratio_cb = np.zeros(3)
    sigma_sz = np.zeros(3)
    sigma_sy = np.zeros(3)
    ratio_sz = np.zeros(3)
    ratio_sy = np.zeros(3)
    sigma_combi = np.zeros(3)
    ratio_combi = np.zeros(3)
    for i in range(1, 4):
        f_ = math.floor((i + 1) / 2)
        c_ = math.ceil((i + 1) / 2)
        sectionsize2 = (sectionsize[f_ * 7 - 7:f_ * 7] + sectionsize[c_ * 7 - 7:c_ * 7]) / 2

        # 鋼材断面性能 (入力断面サイズはmm単位)
        A = BH(sectionsize2, 1)
        ixs = BH(sectionsize2, 4)   # noqa: F841 (元コードでも以降未使用)
        iys = BH(sectionsize2, 5)   # noqa: F841
        Zx = BH(sectionsize2, 6)
        Zy = BH(sectionsize2, 7)
        ib = BH(sectionsize2, 8)    # noqa: F841
        eta = BH(sectionsize2, 9)   # noqa: F841
        Af = BH(sectionsize2, 10)
        Aw = BH(sectionsize2, 11)

        sigma_cx = np.append(sigma_cx, Fxx[i - 1] / (A * 100))
        # 元コード: if sigma_cx >0 (その時点までの全要素判定)
        if np.all(sigma_cx > 0):
            ratio_cx[i - 1] = abs(sigma_cx[i - 1]) / ft
        else:
            ratio_cx[i - 1] = abs(sigma_cx[i - 1]) / fc

        sigma_by[i - 1] = abs(My[i - 1]) / (Zx * 1000)
        sigma_bz[i - 1] = abs(Mz[i - 1]) / (Zy * 1000)

        # 元コード: fby(1)/fbz(1) を常に使用
        ratio_by[i - 1] = sigma_by[i - 1] / fby[0]
        ratio_bz[i - 1] = sigma_bz[i - 1] / fbz[0]

        ratio_cb[i - 1] = ratio_cx[i - 1] + ratio_by[i - 1] + ratio_bz[i - 1]

        sigma_sz[i - 1] = abs(Fz[i - 1]) / (Aw * 100)
        sigma_sy[i - 1] = abs(Fy[i - 1]) / (Af * 2 * 100)

        ratio_sz[i - 1] = sigma_sz[i - 1] / fs
        ratio_sy[i - 1] = sigma_sy[i - 1] / fs

        sigma_combi[i - 1] = math.sqrt((abs(sigma_cx[i - 1]) + abs(sigma_by[i - 1]) + abs(sigma_bz[i - 1])) ** 2
                                       + 3 * max(sigma_sz[i - 1], sigma_sy[i - 1]) ** 2)
        ratio_combi[i - 1] = sigma_combi[i - 1] / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ---------------------------------------------------------------------------
# HwCP_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def HwCP_analysis(sectionsize, length, stress, timecase, SN):
    """H鋼wCPの軸力(N)2軸曲げ(MM)せん断(Q)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    A = BHwCP(sectionsize, 1)
    Ix = BHwCP(sectionsize, 2)  # noqa: F841 (元コードでも未使用)
    Iy = BHwCP(sectionsize, 3)  # noqa: F841
    ixs = BHwCP(sectionsize, 4)
    iys = BHwCP(sectionsize, 5)
    Zx = BHwCP(sectionsize, 6)
    Zy = BHwCP(sectionsize, 7)

    Awx = BHwCP(sectionsize, 11)
    Awy = BHwCP(sectionsize, 11)  # 元コードのまま (12ではなく11を使用)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (A * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Awx * 100)
    sigma_sy = np.abs(Fy) / (Awy * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    # 元コードのまま (絶対値を取らない)
    sigma_combi = np.sqrt((sigma_cx + sigma_by + sigma_bz) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])
    return ratio


# ===========================================================================
# tension_analysis (引張材)
# ===========================================================================

# ---------------------------------------------------------------------------
# TH_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TH_analysis(sectionsize, length, stress, timecase, SN):
    """H鋼の引張(軸力)に対する断面算定. stress列 = [軸力i端, 軸力j端] (kN)."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BH(sectionsize, 1)
    ixs = BH(sectionsize, 4)
    iys = BH(sectionsize, 5)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3  # 軸力[N]
    Fx2 = stress[:, 1] * 10 ** 3  # 軸力[N]

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TBOX_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TBOX_analysis(sectionsize, length, stress, timecase, SN):
    """角形鋼管の引張(軸力)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能 (元コードは sectionsize をecho表示している)
    print(sectionsize)
    a = BBOX(sectionsize, 1)
    ixs = BBOX(sectionsize, 4)
    iys = BBOX(sectionsize, 5)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[2]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        F = ALstressS[2, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TP_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TP_analysis(sectionsize, length, stress, timecase, SN):
    """○鋼管の引張(軸力)に対する断面算定."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BP(sectionsize, 1)
    ixs = BP(sectionsize, 4)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[1]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        F = ALstressS[2, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮 (元コード: lamday も ixs で除している)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TC_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TC_analysis(sectionsize, length, stress, timecase, SN, Lminus):
    """溝形鋼の引張力に対する断面算定 (Lminus: 断面欠損幅mm)."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC(sectionsize, 1)
    ixs = BC(sectionsize, 4)
    iys = BC(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - (Lminus / 10 * sectionsize[2] / 10)

    # 長期許容応力度計算
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TC2_analysis (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

@_matlabenv
def TC2_analysis(sectionsize, length, stress, timecase, SN, Lminus):
    """二丁溝形鋼(背中合わせ2C)の引張力に対する断面算定.

    TC_analysis(単材C)とロジックは同一で、断面性能のみ secprops.BC2 を
    用いる (Lminus: 断面欠損幅mm。ボルトは2枚のウェブを貫通する前提で
    単材の2倍を控除する)。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC2(sectionsize, 1)
    ixs = BC2(sectionsize, 4)
    iys = BC2(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - 2 * (Lminus / 10 * sectionsize[2] / 10)

    # 長期許容応力度計算
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TL_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TL_analysis(sectionsize, length, stress, timecase, SN, Lminus):
    """アングルL鋼の引張力に対する断面算定 (Lminus: 断面欠損幅mm).

    注意: 元コードは t=max(sectionsize(3),sectionsize(4)) のため
    4要素以上の sectionsize が必要 (3要素だとMATLAB同様indexエラー)。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL(sectionsize, 1)
    ixs = BL(sectionsize, 4)
    iys = BL(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - (Lminus / 10 * sectionsize[2] / 10)

    # 長期許容応力度計算
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TL2_analysis (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

@_matlabenv
def TL2_analysis(sectionsize, length, stress, timecase, SN, Lminus):
    """二丁山形鋼(背中合わせ2L・等辺のみ)の引張力に対する断面算定.

    TL_analysis(単材L)とロジックは同一で、断面性能のみ secprops.BL2 を
    用いる (Lminus: 断面欠損幅mm。ボルトは2枚の脚を貫通する前提で単材の
    2倍を控除する)。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL2(sectionsize, 1)
    ixs = BL2(sectionsize, 4)
    iys = BL2(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - 2 * (Lminus / 10 * sectionsize[2] / 10)

    # 長期許容応力度計算
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TSB_analysis.m
# ---------------------------------------------------------------------------

@_matlabenv
def TSB_analysis(sectionsize, length, stress, timecase, SN, Lminus):
    """■ﾑｸ角鋼管(FB)の引張力に対する断面算定 (Lminus: ボルト欠損幅mm)."""
    sectionsize = list(np.asarray(sectionsize, dtype=float).ravel())
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能 ([a a_index]=max(...) → 大きい辺から欠損を控除)
    if sectionsize[0] >= sectionsize[1]:  # MATLAB max は先頭優先
        a_index = 1
    else:
        a_index = 2
    sectionsize[a_index - 1] = sectionsize[a_index - 1] - Lminus
    a = BSB(sectionsize, 1)
    ixs = BSB(sectionsize, 4)
    iys = BSB(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = np.abs(stress[:, 0] * 10 ** 3)
    Fx2 = np.abs(stress[:, 1] * 10 ** 3)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 長期許容応力度計算
    if SN[0] == 1:
        t = min(sectionsize[0], sectionsize[1])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ---------------------------------------------------------------------------
# TSR_analysis.m
# ---------------------------------------------------------------------------

# ユーザー承認による原典(MATLAB)バグ修正 2026-07-08 の適用検知フラグ。
# TSR_analysis で引張材(丸鋼)が圧縮となり符号修正が働いた場合に True。
# ratio_pipeline.run_steel_check が実行毎にリセットし注記出力に使う。
TSR_SIGN_FIX = {'hit': False}


@_matlabenv
def TSR_analysis(sectionsize, length, stress, timecase, SN):
    """○鋼管（ムク・丸鋼ブレース）の引張力に対する断面算定.

    有効断面 0.75a で検定。SN=[1 1080 係数] のとき F=SN(2)*SN(3) (高強度ロッド)。
    """
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSR(sectionsize, 1)
    ixs = BSR(sectionsize, 4)
    iys = BSR(sectionsize, 5)

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        _timecase_stop()

    # 長期許容応力度計算
    if SN[0] == 1:
        if SN[1] == 1080:
            F = SN[1] * SN[2]
            ft = F / 1.5
        else:
            t = sectionsize[0]
            ALstressS = ALST_S([SN[1], t])
            F = ALstressS[2, 0]
            ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100 * 0.75)
    sigma_cx2 = Fx2 / (a * 100 * 0.75)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        # ユーザー承認による原典(MATLAB)バグ修正 2026-07-08: 負の応力(圧縮)で
        # 検定値が負 (原典 TSR_analysis.m 74-75行: ratio_cx=-1*abs(sigma_cx)/fc)。
        # 負の検定値は断面別最大値の選定(max)で無視されNG見落としとなるため
        # -1 を除去 (MATLAB版実行結果と差が出る場合あり)。
        TSR_SIGN_FIX['hit'] = True
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    ratio = np.column_stack([ratio_cx1, ratio_cx2])
    return ratio


# ===========================================================================
# NMMQ_analysis_text (検定詳細テキスト)  ※戻り値は list[str] (strvcatの各行)
# ===========================================================================

# ---------------------------------------------------------------------------
# H_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def H_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                    section_no, H_beam_RCsupport, cb_judge, LOAS_CASE_NAME,
                    section_name, judge_H):
    """H鋼の断面算定詳細テキスト (H_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()
    cb_judge = np.asarray(cb_judge, dtype=float).ravel()

    # 鋼材断面性能 (入力断面サイズはmm単位)
    A = BH(sectionsize, 1)
    Ix = BH(sectionsize, 2)
    Iy = BH(sectionsize, 3)
    ixs = BH(sectionsize, 4)
    iys = BH(sectionsize, 5)
    Zx = BH(sectionsize, 6)
    Zy = BH(sectionsize, 7)
    ib = BH(sectionsize, 8)
    eta = BH(sectionsize, 9)
    Af = BH(sectionsize, 10)
    Aw = BH(sectionsize, 11)

    text = ['H鋼（軸力および2軸曲げ・せん断）に対する断面算定']
    text.append('軸力を考慮したH鋼の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('またせん断についても弱軸および強軸方向の検討をおこない，直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('H鋼サイズ：H-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    elif timecase == 9:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        _timecase_stop()
    text.append('*断面性能')
    text.append('断面積：' + n2s(A) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    if judge_H == 2:
        Mz = 0.0

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    # 元コード: SN(1)==2 のとき ALstressS 未定義でエラーになる (そのまま再現)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(ALstressS[2, 1], '%15.0f'))

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        if cb_judge[0] == 1 and H_beam_RCsupport == 1:  # 剛床成立なのでRCスラブあり
            # 梁の両端，中央全てのモーメントが0以上であればfbはftとする
            if (My[i * 3 - 3] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 2] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 1] * _cosd(cb_judge[1]) >= 0):
                fby[i * 3 - 3:3 * i] = ft
                if judge_H == 1:
                    fbz[i * 3 - 3:3 * i] = ft
                else:
                    fbz[i * 3 - 3:3 * i] = np.inf
                CC[i * 3 - 3:3 * i] = 1.0
            else:
                ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
                abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

                # 中央部が最大の場合，C=1
                if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                    C = 1
                elif ABSM == abs_m:  # 単曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 - 1.05 * M + 0.3 * M ** 2
                else:  # 複曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 + 1.05 * M + 0.3 * M ** 2
                CC[i * 3 - 3:3 * i] = min(2.3, C)

                fb1 = 89000 * ib / eta / (length[2] * 100)
                fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

                fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
                if judge_H == 1:
                    fbz[i * 3 - 3:3 * i] = ft
                else:
                    fbz[i * 3 - 3:3 * i] = np.inf
        else:
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            # 中央部が最大の場合，C=1
            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            if length[2] == 0:
                fb1 = ft
            else:
                fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            if judge_H == 1:
                fbz[i * 3 - 3:3 * i] = ft
            else:
                fbz[i * 3 - 3:3 * i] = np.inf

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (A * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 2 * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    ratio_combi = np.zeros(3)
    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')

    if judge_H == 1:
        text.append('*設計用応力　[' + t_case + ']　i端')
        text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                    + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　中央')
        text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                    + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　j端')
        text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                    + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('　　')

        text.append('*設計用応力度　[' + t_case + ']　i端')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                    + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('*設計用応力度　[' + t_case + ']　中央')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                    + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('*設計用応力度　[' + t_case + ']　j端')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                    + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('　　')
        text.append('*許容応力度　[' + t_case + ']')
        text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                    + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

        text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2'
                    + '＜補正係数C：' + n2s(_m(CC, st_index), '%15.2f') + '＞　曲げ許容応力度(弱)：'
                    + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')
        text.append('　　')
        text.append('　　')
        text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
        text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                    + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
        text.append('せん断（強軸）：' + n2s(ratio[st_index - 1, 1], '%15.2f') + ',　'
                    + n2s(ratio[st_index, 1], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 1], '%15.2f'))
    else:
        text.append('*設計用応力　[' + t_case + ']　i端')
        text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　中央')
        text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　j端')
        text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                    + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
        text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                    + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
        text.append('　　')
        text.append('　　')

        text.append('*設計用応力度　[' + t_case + ']　i端')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('*設計用応力度　[' + t_case + ']　中央')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('*設計用応力度　[' + t_case + ']　j端')
        text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                    + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2')
        text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                    + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
        text.append('　　')
        text.append('　　')
        text.append('*許容応力度　[' + t_case + ']')
        text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                    + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

        text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2'
                    + '＜補正係数C：' + n2s(_m(CC, st_index), '%15.2f') + '＞')
        text.append('　　')
        text.append('　　')
        text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
        text.append('曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                    + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
        text.append('せん断（強軸）：' + n2s(ratio[st_index - 1, 1], '%15.2f') + ',　'
                    + n2s(ratio[st_index, 1], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 1], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# BOX_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def BOX_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                      section_no, stress_L, c_up, LOAS_CASE_NAME, section_name):
    """□鋼管の断面算定詳細テキスト (BOX_analysis_text.m)."""
    n2s = _num2str
    sectionsize = list(np.asarray(sectionsize, dtype=float).ravel())
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 長期許容応力度計算
    if SN[0] == 1:  # SN系で応力の割増はなし
        t = sectionsize[2]

        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
        c_up = 1.0
        print('CAUTION:BOX断面にSN材の使用？？')

        STEEL_NAME = 'SN材系'
    elif SN[0] == 2:  # 冷間角鋼なので応力の割増あり
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        # 応力割増係数の設定
        c_up = _m(np.asarray(c_up, dtype=float).ravel(), SN[2])
        if sectionsize[2] < 6:
            c_up = 1.0
        if SN[2] == 1:
            # 元コードのまま: text版のみ sectionsize(4) に代入 (analysis版は(5))
            _mset(sectionsize, 4, sectionsize[2] * 2)
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            _mset(sectionsize, 5, sectionsize[2] * 2.5)
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            _mset(sectionsize, 5, sectionsize[2] * 3.5)
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            _mset(sectionsize, 5, sectionsize[2] * 1.25)
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 鋼材断面性能 (元コードは BBOX をコメントアウトし BBOX_H を使用)
    a = BBOX_H(sectionsize, 1)
    Ix = BBOX_H(sectionsize, 2)
    Iy = BBOX_H(sectionsize, 3)
    ixs = BBOX_H(sectionsize, 4)
    iys = BBOX_H(sectionsize, 5)
    Zx = BBOX_H(sectionsize, 6)
    Zy = BBOX_H(sectionsize, 7)

    Awx = BBOX_H(sectionsize, 11)
    Awy = BBOX_H(sectionsize, 12)

    text = ['角鋼管（軸力および2軸曲げ・せん断力）に対する断面算定']
    text.append('軸力を考慮した角型鋼管の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこない，直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('角鋼管サイズ：□-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x' + n2s(sectionsize[2]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
        stress_L = np.atleast_2d(np.asarray(stress_L, dtype=float))
        # 断面力取り込み（単位をN/mm2系にする．）
        Fxx = (stress_L[:, 0] + c_up * (stress[:, 0] - stress_L[:, 0])) * 10 ** 3
        My = (stress_L[:, 3] + c_up * (stress[:, 3] - stress_L[:, 3])) * 10 ** 6
        Mz = (stress_L[:, 4] + c_up * (stress[:, 4] - stress_L[:, 4])) * 10 ** 6
        Fz = (stress_L[:, 2] + c_up * (stress[:, 2] - stress_L[:, 2])) * 10 ** 3
        Fy = (stress_L[:, 1] + c_up * (stress[:, 1] - stress_L[:, 1])) * 10 ** 3
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
        # 断面力取り込み（単位をN/mm2系にする．）
        Fxx = stress[:, 0] * 10 ** 3
        My = stress[:, 3] * 10 ** 6
        Mz = stress[:, 4] * 10 ** 6
        Fz = stress[:, 2] * 10 ** 3
        Fy = stress[:, 1] * 10 ** 3
    else:
        _timecase_stop()

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))
    Lamda = 1500 / math.sqrt(F / 1.5)
    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))
    text.append('冷間成形材使用による応力の割増：' + n2s(c_up, '%15.1f') + '倍' + '　使用材料：' + STEEL_NAME)

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Awx * 100)
    sigma_sy = np.abs(Fy) / (Awy * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f')
                + 'kN　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('*解析結果応力（割増前）　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(stress[st_index - 1, 0], '%15.1f') + 'kN　曲げM(強)：'
                + n2s(stress[st_index - 1, 3], '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(stress[st_index - 1, 4], '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(stress[st_index - 1, 2], '%15.1f')
                + 'kN　せん断力(弱)：' + n2s(stress[st_index - 1, 1], '%15.1f') + 'kN')
    text.append('　　')

    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
    text.append('*解析結果応力（割増前）　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(stress[st_index, 0], '%15.1f') + 'kN　曲げM(強)：'
                + n2s(stress[st_index, 3], '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(stress[st_index, 4], '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(stress[st_index, 2], '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(stress[st_index, 1], '%15.1f') + 'kN')
    text.append('　　')

    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('*解析結果応力（割増前）　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(stress[st_index + 1, 0], '%15.1f') + 'kN　曲げM(強)：'
                + n2s(stress[st_index + 1, 3], '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(stress[st_index + 1, 4], '%15.1f')
                + 'kNm　せん断力(強)：' + n2s(stress[st_index + 1, 2], '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(stress[st_index + 1, 1], '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　せん断許容応力度：'
                + n2s(fs, '%15.0f') + 'N/mm2　圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# P_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def P_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                    section_no, LOAS_CASE_NAME, section_name):
    """○鋼管の断面算定詳細テキスト (P_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BP(sectionsize, 1)
    Ix = BP(sectionsize, 2)
    ixs = BP(sectionsize, 4)
    Zx = BP(sectionsize, 6)

    text = ['○鋼管（軸力および2軸曲げ・せん断力）に対する断面算定']
    text.append('軸力を考慮した鋼管の断面算定を行う．曲げについては2軸曲げの合成値で検定する．')
    text.append('またせん断についても弱軸および強軸方向の合成値に対して検定を行い，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('○鋼管サイズ：○-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4')
    text.append('断面係数：' + n2s(Zx) + 'cm3' + '　断面二次半径：' + n2s(ixs) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[1]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')
    Lamda = 1500 / math.sqrt(F / 1.5)

    # 圧縮 (元コード: lamday も ixs で除している)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度
    fb = ft
    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fb = 1.5 * fb

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_b = np.sqrt(np.abs(My) ** 2 + np.abs(Mz) ** 2) / (Zx * 1000)
    ratio_b = sigma_b / fb

    ratio_cb = ratio_cx + ratio_b

    sigma_s = np.sqrt(np.abs(Fz) ** 2 + np.abs(Fy) ** 2) / (a * 100)

    ratio_s = sigma_s / fs

    sigma_combi = np.sqrt((sigma_cx + sigma_b) ** 2 + 3 * sigma_s ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_s, ratio_s, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index) ** 2 + _m(Mz, st_index) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index) ** 2 + _m(Fz, st_index) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index + 1) ** 2 + _m(Mz, st_index + 1) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index + 1) ** 2 + _m(Fz, st_index + 1) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index + 2) ** 2 + _m(Mz, st_index + 2) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index + 2) ** 2 + _m(Fz, st_index + 2) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index), '%15.1f') + 'N/mm2')

    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index + 1), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index + 1), '%15.1f') + 'N/mm2')

    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index + 2), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度：' + n2s(_m(fb, st_index), '%15.0f') + 'N/mm2')

    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index, 0], '%15.3f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.3f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.3f') + ',　'
                + n2s(ratio[st_index, 3], '%15.3f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.3f'))

    return text


# ---------------------------------------------------------------------------
# C_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def C_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                    section_no, LOAS_CASE_NAME, section_name):
    """溝形鋼の断面算定詳細テキスト (C_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC(sectionsize, 1)
    ixs = BC(sectionsize, 4)
    iys = BC(sectionsize, 5)
    Zx = BC(sectionsize, 6)
    Zy = BC(sectionsize, 7)

    Af = BC(sectionsize, 10)
    Aw = BC(sectionsize, 11)

    text = ['溝形鋼・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した溝形鋼の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこない，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('溝形鋼サイズ：C-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')
    Lamda = 1500 / math.sqrt(F / 1.5)
    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度
    fb1 = 89000 * (Af * 100) / (length[2] * 1000 * sectionsize[0])

    fby = min(ft, fb1)
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 2 * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm')
    text.append('せん断力(強)：' + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    # 元コードのまま: 'せん断応力度τy：z'
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：z' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')
    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')

    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# SB_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def SB_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, LOAS_CASE_NAME, section_name):
    """ムク角材の断面算定詳細テキスト (SB_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSB(sectionsize, 1)
    Ix = BSB(sectionsize, 2)
    Iy = BSB(sectionsize, 3)
    ixs = BSB(sectionsize, 4)
    iys = BSB(sectionsize, 5)
    Zx = BSB(sectionsize, 6)
    Zy = BSB(sectionsize, 7)

    text = ['ムク角材・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した角材の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこない，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('角材サイズ：■-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = min(sectionsize[0], sectionsize[1])
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        F = ALstressS[2, 0]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度 (text版は初期値としてスカラftを設定)
    fby = ft
    fbz = ft

    # 曲げ許容応力度
    D = sectionsize[0]
    B = sectionsize[1]
    st_size1 = stress.shape[0]
    CC = np.zeros(st_size1)
    if D == B:
        fbz = ft
        fby = ft
    elif D > B:
        ib = BSB([D, B], 8)
        eta = BSB([D, B], 9)
        fby = np.zeros(st_size1)
        fbz = np.zeros(st_size1)
        for i in range(1, int(st_size1 / 3) + 1):
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fbz[i * 3 - 3:3 * i] = ft
    else:
        ib = BSB([B, D], 8)
        eta = BSB([B, D], 9)
        fby = np.zeros(st_size1)
        fbz = np.zeros(st_size1)
        for i in range(1, int(st_size1 / 3) + 1):
            ABSM = abs(Mz[i * 3 - 3]) + abs(Mz[i * 3 - 2]) + abs(Mz[i * 3 - 1])
            abs_m = abs(Mz[i * 3 - 3] + Mz[i * 3 - 2] + Mz[i * 3 - 1])

            if abs(Mz[i * 3 - 2]) >= abs(Mz[i * 3 - 3]) and abs(Mz[i * 3 - 2] >= abs(Mz[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])) / max(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])) / max(abs(Mz[i * 3 - 3]), abs(Mz[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            # 元コードのまま CC(i) を使用
            fb2 = (2 / 3 - 4 / 15 / CC[i - 1] * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fbz[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fby[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * np.asarray(fby)
        fbz = 1.5 * np.asarray(fbz)

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (a * 100)
    sigma_sy = np.abs(Fy) / (a * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    # 元コードのまま: 'せん断応力度τy：z'
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：z' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')
    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')

    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# SR_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def SR_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, LOAS_CASE_NAME, section_name):
    """棒鋼の断面算定詳細テキスト (SR_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSR(sectionsize, 1)
    Ix = BSR(sectionsize, 2)
    ixs = BSR(sectionsize, 4)
    Zx = BSR(sectionsize, 6)

    text = ['棒鋼・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した棒鋼の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても2軸の方向に対して検討をおこない，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('棒鋼サイズ：●-' + n2s(sectionsize[0]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント：' + n2s(Ix) + 'cm4')
    text.append('断面係数：' + n2s(Zx) + 'cm3　断面二次半径：' + n2s(ixs) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[0]
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs

    lamdadfc = lamdax
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ：' + n2s(length[0] * 1000, '%15.0f') + 'mm')
    text.append('細長比：' + n2s(lamdax, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度
    fb = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fb = 1.5 * fb

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_b = np.sqrt(np.abs(My) ** 2 + np.abs(Mz) ** 2) / (Zx * 1000)
    ratio_b = sigma_b / fb

    ratio_cb = ratio_cx + ratio_b

    sigma_s = np.sqrt(np.abs(Fz) ** 2 + np.abs(Fy) ** 2) / (a * 100)

    ratio_s = sigma_s / fs

    sigma_combi = np.sqrt((sigma_cx + sigma_b) ** 2 + 3 * sigma_s ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_s, ratio_s, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index) ** 2 + _m(Mz, st_index) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index) ** 2 + _m(Fz, st_index) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index + 1) ** 2 + _m(Mz, st_index + 1) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index + 1) ** 2 + _m(Fz, st_index + 1) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('合成した最大応力')
    text.append('曲げM：' + n2s(math.sqrt(_m(My, st_index + 2) ** 2 + _m(Mz, st_index + 2) ** 2) / 10 ** 6, '%15.1f')
                + 'kNm　せん断力：'
                + n2s(math.sqrt(_m(Fy, st_index + 2) ** 2 + _m(Fz, st_index + 2) ** 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index), '%15.1f') + 'N/mm2')

    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index + 1), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index + 1), '%15.1f') + 'N/mm2')

    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σb：'
                + n2s(_m(sigma_b, st_index + 2), '%15.1f') + 'N/mm2' + '　せん断応力度τ：'
                + n2s(_m(sigma_s, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度：' + n2s(_m(fb, st_index), '%15.0f') + 'N/mm2')

    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.3f') + ',　'
                + n2s(ratio[st_index, 0], '%15.3f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.3f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.3f') + ',　'
                + n2s(ratio[st_index, 3], '%15.3f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.3f'))

    return text


# ---------------------------------------------------------------------------
# CT_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def CT_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, LOAS_CASE_NAME, section_name):
    """CT鋼の断面算定詳細テキスト (CT_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    ratio_rows = []
    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    text = ['CT材・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮したCT材の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこない，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('CTサイズ：CT-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    # 鋼材断面性能 (入力断面サイズはmm単位) — 幅厚比に関する規定の検討
    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    H_e = min(H, 235 / math.sqrt(F) * tf * 2)
    B_e = min(B, 235 / math.sqrt(F) * tw)

    a_t = BCT(sectionsize, 1)
    a_c = BCT([H_e, B_e, tw, tf], 1)

    Ix1 = BCT(sectionsize, 2)
    Ix2 = BCT([H_e, B, tw, tf], 2)
    Ix3 = BCT([H, B_e, tw, tf], 2)
    Iy = BCT(sectionsize, 3)

    # 圧縮時の断面性能なので有効断面
    ixs = BCT([H_e, B_e, tw, tf], 4)
    iys = BCT([H_e, B_e, tw, tf], 5)

    Zxb = BCT(sectionsize, 7)
    Zxt = BCT(sectionsize, 6)

    # 正曲げ時はフランジ側が有効部分のみ
    Zx_plus = min(BCT([H, B_e, tw, tf], 6), BCT([H, B_e, tw, tf], 7))
    # 負曲げ時はウェブ側が有効部分のみ
    Zx_minus = min(BCT([H_e, B, tw, tf], 6), BCT([H_e, B, tw, tf], 7))

    # 弱軸周りは全て有効とする
    Zy = BCT(sectionsize, 8)

    # 正曲げ時
    ib_t = BCT([H, B_e, tw, tf], 12)
    eta_t = BCT([H, B_e, tw, tf], 13)

    # 負曲げ時
    ib_b = BCT([H_e, B, tw, tf], 14)
    eta_b = BCT([H_e, B, tw, tf], 15)

    Af = BCT(sectionsize, 10)
    Aw = BCT(sectionsize, 11)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    text.append('*断面性能')
    text.append('断面積：' + n2s(a_t) + 'cm2' + '　断面積(圧縮時有効)：' + n2s(a_c))
    text.append('断面二次モーメント(強)：' + n2s(Ix1) + 'cm4' + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('有効断面二次モーメント(強/正曲)：' + n2s(Ix2) + 'cm4' + '　有効断面二次モーメント(強/負曲)：'
                + n2s(Ix3) + 'cm4')

    text.append('断面係数(強/上側)：' + n2s(Zxt) + 'cm3' + '　断面係数(強/下側)：' + n2s(Zxb) + 'cm3'
                + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('有効断面係数(強/正曲)：' + n2s(Zx_plus) + 'cm3' + '　有効断面係数(強/負曲)：'
                + n2s(Zx_minus) + 'cm3')
    text.append('有効断面二次半径(強)：' + n2s(ixs) + 'cm' + '　有効断面二次半径(弱)：' + n2s(iys) + 'cm')

    Lamda = 1500 / math.sqrt(F / 1.5)

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby_t = np.zeros(st_size1)
    fby_b = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
        abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

        # 中央部が最大の場合，C=1
        if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
            C = 1
        elif ABSM == abs_m:  # 単曲率曲げ
            M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
            C = 1.75 - 1.05 * M + 0.3 * M ** 2
        else:  # 複曲率曲げ
            M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
            C = 1.75 + 1.05 * M + 0.3 * M ** 2
        CC[i * 3 - 3:3 * i] = min(2.3, C)

        fb1_t = 89000 * ib_t / eta_t / (length[2] * 100)
        fb2_t = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib_t) ** 2 / Lamda ** 2) * F

        fb1_b = 89000 * ib_b / eta_b / (length[2] * 100)
        fb2_b = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib_b) ** 2 / Lamda ** 2) * F

        fby_t[i * 3 - 3:3 * i] = min(ft, max(fb1_t, fb2_t))
        fby_b[i * 3 - 3:3 * i] = min(ft, max(fb1_b, fb2_b))

        fbz[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby_t = 1.5 * fby_t
        fby_b = 1.5 * fby_b
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = np.zeros(st_size1)
    sigma_by = np.zeros(st_size1)
    sigma_bz = np.zeros(st_size1)
    sigma_sz = np.zeros(st_size1)
    sigma_sy = np.zeros(st_size1)
    for i in range(1, st_size1 + 1):
        if Fxx[i - 1] > 0:
            sigma_cx[i - 1] = Fxx[i - 1] / (a_t * 100)
            ratio_cx = abs(sigma_cx[i - 1]) / ft
        else:
            sigma_cx[i - 1] = Fxx[i - 1] / (a_c * 100)
            ratio_cx = abs(sigma_cx[i - 1]) / fc

        if My[i - 1] > 0:
            sigma_by[i - 1] = abs(My[i - 1]) / (Zx_plus * 1000)
            ratio_by = sigma_by[i - 1] / fby_t[i - 1]
        else:
            sigma_by[i - 1] = abs(My[i - 1]) / (Zx_minus * 1000)
            ratio_by = sigma_by[i - 1] / fby_b[i - 1]

        sigma_bz[i - 1] = abs(Mz[i - 1]) / (Zy * 1000)
        ratio_bz = sigma_bz[i - 1] / fbz[i - 1]

        ratio_cb = ratio_cx + ratio_by + ratio_bz

        sigma_sz[i - 1] = abs(Fz[i - 1]) / (Aw * 100)
        sigma_sy[i - 1] = abs(Fy[i - 1]) / (Af * 100)

        ratio_sz = sigma_sz[i - 1] / fs
        ratio_sy = sigma_sy[i - 1] / fs

        sigma_combi = math.sqrt((abs(sigma_cx[i - 1]) + abs(sigma_by[i - 1]) + abs(sigma_bz[i - 1])) ** 2
                                + 3 * max(sigma_sz[i - 1], sigma_sy[i - 1]) ** 2)
        ratio_combi = sigma_combi / ft
        ratio_rows.append([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    ratio = np.array(ratio_rows, dtype=float)

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τz' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度(強/正曲げ時)：' + n2s(_m(fby_t, st_index), '%15.0f') + 'N/mm2'
                + '　補正係数C：' + n2s(_m(CC, st_index), '%15.2f'))
    text.append('曲げ許容応力度(強/負曲げ時)：' + n2s(_m(fby_b, st_index), '%15.0f') + 'N/mm2'
                + '　補正係数C：' + n2s(_m(CC, st_index), '%15.2f'))

    text.append('曲げ許容応力度(弱)：' + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# L_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def L_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                    section_no, LOAS_CASE_NAME, section_name):
    """山形鋼の断面算定詳細テキスト (L_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL(sectionsize, 1)
    ixs = BL(sectionsize, 4)
    iys = BL(sectionsize, 5)
    Zx = BL(sectionsize, 6)
    Zy = BL(sectionsize, 7)

    Aw = BL(sectionsize, 11)
    Af = BL(sectionsize, 10)

    text = ['山形鋼・軸力および2軸曲げに対する断面算定']
    text.append('軸力を考慮した山形鋼の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('また，せん断についても弱軸および強軸方向の検討をおこない，')
    text.append('直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')
    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('山形鋼サイズ：L-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x' + n2s(sectionsize[2]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[2]  # 元コード: max(sectionsize(3))
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')
    Lamda = 1500 / math.sqrt(F / 1.5)
    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (a * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Aw * 100)
    sigma_sy = np.abs(Fy) / (Af * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    sigma_combi = np.sqrt((np.abs(sigma_cx) + np.abs(sigma_by) + np.abs(sigma_bz)) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    # 元コードのまま: 'せん断応力度τy：z'
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：z' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')
    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')

    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# HT_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def HT_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, H_beam_RCsupport, cb_judge, LOAS_CASE_NAME,
                     section_name):
    """テーパーH鋼の断面算定詳細テキスト (HT_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()
    cb_judge = np.asarray(cb_judge, dtype=float).ravel()

    # 鋼材断面性能
    sectionsize2 = (sectionsize[0:7] + sectionsize[7:14]) / 2
    # 入力断面サイズはmm単位
    A = BH(sectionsize2, 1)
    Ix = BH(sectionsize2, 2)
    Iy = BH(sectionsize2, 3)
    ixs = BH(sectionsize2, 4)
    iys = BH(sectionsize2, 5)
    Zx = BH(sectionsize2, 6)
    Zy = BH(sectionsize2, 7)
    ib = BH(sectionsize2, 8)
    eta = BH(sectionsize2, 9)
    Af = BH(sectionsize2, 10)
    Aw = BH(sectionsize2, 11)

    text = ['H鋼テーパー断面（軸力および2軸曲げ・せん断）に対する断面算定']
    text.append('軸力を考慮したH鋼の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('またせん断についても弱軸および強軸方向の検討をおこない，直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('H鋼サイズ(I)：H-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))
    text.append('H鋼サイズ(J)：H-' + n2s(sectionsize[7]) + 'x' + n2s(sectionsize[8]) + 'x'
                + n2s(sectionsize[9]) + 'x' + n2s(sectionsize[10]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        _timecase_stop()
    text.append('*断面性能(テーパー中央部の代表性能)')
    text.append('断面積：' + n2s(A) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3　断面二次半径(強)：'
                + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')
    text.append('*断面検定についてはi端・中央・j端それぞれの断面性能による)')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    # 元コード: SN(1)==2 のとき ALstressS 未定義でエラーになる (そのまま再現)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(ALstressS[2, 1], '%15.0f'))

    # 曲げ許容応力度
    st_size1 = stress.shape[0]
    fby = np.zeros(st_size1)
    fbz = np.zeros(st_size1)
    CC = np.zeros(st_size1)
    for i in range(1, int(st_size1 / 3) + 1):
        if cb_judge[0] == 1 and H_beam_RCsupport == 1:  # 剛床成立なのでRCスラブあり
            # 梁の両端，中央全てのモーメントが0以上であればfbはftとする
            if (My[i * 3 - 3] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 2] * _cosd(cb_judge[1]) >= 0 and
                    My[i * 3 - 1] * _cosd(cb_judge[1]) >= 0):
                fby[i * 3 - 3:3 * i] = ft
                fbz[i * 3 - 3:3 * i] = ft
                CC[i * 3 - 3:3 * i] = 1.0
            else:
                ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
                abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

                if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                    C = 1
                elif ABSM == abs_m:  # 単曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 - 1.05 * M + 0.3 * M ** 2
                else:  # 複曲率曲げ
                    M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                    C = 1.75 + 1.05 * M + 0.3 * M ** 2
                CC[i * 3 - 3:3 * i] = min(2.3, C)

                fb1 = 89000 * ib / eta / (length[2] * 100)
                fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

                fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
                fbz[i * 3 - 3:3 * i] = ft
        else:
            ABSM = abs(My[i * 3 - 3]) + abs(My[i * 3 - 2]) + abs(My[i * 3 - 1])
            abs_m = abs(My[i * 3 - 3] + My[i * 3 - 2] + My[i * 3 - 1])

            if abs(My[i * 3 - 2]) >= abs(My[i * 3 - 3]) and abs(My[i * 3 - 2] >= abs(My[i * 3 - 1])):
                C = 1
            elif ABSM == abs_m:  # 単曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 - 1.05 * M + 0.3 * M ** 2
            else:  # 複曲率曲げ
                M = abs(min(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])) / max(abs(My[i * 3 - 3]), abs(My[i * 3 - 1])))
                C = 1.75 + 1.05 * M + 0.3 * M ** 2
            CC[i * 3 - 3:3 * i] = min(2.3, C)

            fb1 = 89000 * ib / eta / (length[2] * 100)
            fb2 = (2 / 3 - 4 / 15 / C * (length[2] * 100 / ib) ** 2 / Lamda ** 2) * F

            fby[i * 3 - 3:3 * i] = min(ft, max(fb1, fb2))
            fbz[i * 3 - 3:3 * i] = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = np.zeros(0)
    ratio_cx = np.zeros(3)
    sigma_by = np.zeros(3)
    sigma_bz = np.zeros(3)
    ratio_by = np.zeros(3)
    ratio_bz = np.zeros(3)
    ratio_cb = np.zeros(3)
    sigma_sz = np.zeros(3)
    sigma_sy = np.zeros(3)
    ratio_sz = np.zeros(3)
    ratio_sy = np.zeros(3)
    sigma_combi = np.zeros(3)
    ratio_combi = np.zeros(3)
    for i in range(1, 4):
        f_ = math.floor((i + 1) / 2)
        c_ = math.ceil((i + 1) / 2)
        sectionsize2 = (sectionsize[f_ * 7 - 7:f_ * 7] + sectionsize[c_ * 7 - 7:c_ * 7]) / 2

        # 鋼材断面性能 (入力断面サイズはmm単位)
        A = BH(sectionsize2, 1)
        ixs = BH(sectionsize2, 4)
        iys = BH(sectionsize2, 5)
        Zx = BH(sectionsize2, 6)
        Zy = BH(sectionsize2, 7)
        ib = BH(sectionsize2, 8)
        eta = BH(sectionsize2, 9)
        Af = BH(sectionsize2, 10)
        Aw = BH(sectionsize2, 11)

        sigma_cx = np.append(sigma_cx, Fxx[i - 1] / (A * 100))
        if np.all(sigma_cx > 0):
            ratio_cx[i - 1] = abs(sigma_cx[i - 1]) / ft
        else:
            ratio_cx[i - 1] = abs(sigma_cx[i - 1]) / fc

        sigma_by[i - 1] = abs(My[i - 1]) / (Zx * 1000)
        sigma_bz[i - 1] = abs(Mz[i - 1]) / (Zy * 1000)

        ratio_by[i - 1] = sigma_by[i - 1] / fby[0]
        ratio_bz[i - 1] = sigma_bz[i - 1] / fbz[0]

        ratio_cb[i - 1] = ratio_cx[i - 1] + ratio_by[i - 1] + ratio_bz[i - 1]

        sigma_sz[i - 1] = abs(Fz[i - 1]) / (Aw * 100)
        sigma_sy[i - 1] = abs(Fy[i - 1]) / (Af * 2 * 100)

        ratio_sz[i - 1] = sigma_sz[i - 1] / fs
        ratio_sy[i - 1] = sigma_sy[i - 1] / fs

        sigma_combi[i - 1] = math.sqrt((abs(sigma_cx[i - 1]) + abs(sigma_by[i - 1]) + abs(sigma_bz[i - 1])) ** 2
                                       + 3 * max(sigma_sz[i - 1], sigma_sy[i - 1]) ** 2)
        ratio_combi[i - 1] = sigma_combi[i - 1] / ft
    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    # 最大検定比のものを記す
    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('　　')

    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2'
                + '＜補正係数C：' + n2s(_m(CC, st_index), '%15.2f') + '＞　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# HwCP_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def HwCP_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                       section_no, LOAS_CASE_NAME, section_name):
    """H鋼wCPの断面算定詳細テキスト (HwCP_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    A = BHwCP(sectionsize, 1)
    Ix = BHwCP(sectionsize, 2)
    Iy = BHwCP(sectionsize, 3)
    ixs = BHwCP(sectionsize, 4)
    iys = BHwCP(sectionsize, 5)
    Zx = BHwCP(sectionsize, 6)
    Zy = BHwCP(sectionsize, 7)

    Awx = BHwCP(sectionsize, 11)
    Awy = BHwCP(sectionsize, 11)  # 元コードのまま (12ではなく11を使用)

    text = ['H鋼(カバープレート付)（軸力および2軸曲げ・せん断）に対する断面算定']
    text.append('軸力を考慮したH鋼（w/カバープレート）の断面算定を行う．曲げについては2軸曲げを考慮する．')
    text.append('またせん断についても弱軸および強軸方向の検討をおこない，直応力とせん断応力の複合応力に対しても検討を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('H鋼サイズ：H-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))
    text.append('カバープレート厚み：t' + n2s(sectionsize[5]) + 'mm')

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        _timecase_stop()

    text.append('*断面性能')
    text.append('断面積：' + n2s(A) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次モーメント(弱)：' + n2s(Iy) + 'cm4')
    text.append('断面係数(強)：' + n2s(Zx) + 'cm3' + '　断面係数(弱)：' + n2s(Zy) + 'cm3　断面二次半径(強)：'
                + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fxx = stress[:, 0] * 10 ** 3
    My = stress[:, 3] * 10 ** 6
    Mz = stress[:, 4] * 10 ** 6
    Fz = stress[:, 2] * 10 ** 3
    Fy = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    # 元コード: SN(1)==2 のとき ALstressS 未定義でエラーになる (そのまま再現)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(ALstressS[2, 1], '%15.0f'))

    # 曲げ許容応力度
    fby = ft
    fbz = ft

    if timecase == 2:
        ft = F
        fs = F / math.sqrt(3)
        fc = 1.5 * fc
        fby = 1.5 * fby
        fbz = 1.5 * fbz

    # 応力度の計算ならびに検定値の計算
    sigma_cx = Fxx / (A * 100)
    if np.all(sigma_cx > 0):
        ratio_cx = np.abs(sigma_cx) / ft
    else:
        ratio_cx = np.abs(sigma_cx) / fc

    sigma_by = np.abs(My) / (Zx * 1000)
    sigma_bz = np.abs(Mz) / (Zy * 1000)

    ratio_by = sigma_by / fby
    ratio_bz = sigma_bz / fbz

    ratio_cb = ratio_cx + ratio_by + ratio_bz

    sigma_sz = np.abs(Fz) / (Awx * 100)
    sigma_sy = np.abs(Fy) / (Awy * 100)

    ratio_sz = sigma_sz / fs
    ratio_sy = sigma_sy / fs

    # 元コードのまま (絶対値を取らない)
    sigma_combi = np.sqrt((sigma_cx + sigma_by + sigma_bz) ** 2
                          + 3 * np.maximum(sigma_sz, sigma_sy) ** 2)
    ratio_combi = sigma_combi / ft

    ratio = np.column_stack([ratio_cb, ratio_sz, ratio_sy, ratio_combi])

    max_index = _maxratio_index(ratio)
    st_index = _st_index(max_index)
    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力：' + n2s(_m(Fxx, st_index) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 1) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 1) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 1) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 1) / 10 ** 3, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力：' + n2s(_m(Fxx, st_index + 2) / 1000, '%15.1f') + 'kN　曲げM(強)：'
                + n2s(_m(My, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　曲げM(弱)：'
                + n2s(_m(Mz, st_index + 2) / 10 ** 6, '%15.1f') + 'kNm　せん断力(強)：'
                + n2s(_m(Fz, st_index + 2) / 10 ** 3, '%15.1f') + 'kN'
                + '　せん断力(弱)：' + n2s(_m(Fy, st_index + 2) / 10 ** 3, '%15.1f') + 'kN')
    text.append('　　')
    text.append('　　')

    text.append('*設計用応力度　[' + t_case + ']　i端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　中央')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 1), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 1), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 1), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 1), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('*設計用応力度　[' + t_case + ']　j端')
    text.append('直応力度σx：' + n2s(_m(sigma_cx, st_index + 2), '%15.1f') + 'N/mm2　直応力度σy：'
                + n2s(_m(sigma_by, st_index + 2), '%15.1f') + 'N/mm2　直応力度σz：'
                + n2s(_m(sigma_bz, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('せん断応力度τz：' + n2s(_m(sigma_sz, st_index + 2), '%15.1f') + 'N/mm2'
                + '　せん断応力度τy：' + n2s(_m(sigma_sy, st_index + 2), '%15.1f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2　圧縮許容応力度：'
                + n2s(fc, '%15.0f') + 'N/mm2　せん断許容応力度：' + n2s(fs, '%15.0f') + 'N/mm2')

    text.append('曲げ許容応力度(強)：' + n2s(_m(fby, st_index), '%15.0f') + 'N/mm2　曲げ許容応力度(弱)：'
                + n2s(_m(fbz, st_index), '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　中央　j端の順に示す**')
    text.append('ニ軸曲げ＋圧縮：' + n2s(ratio[st_index - 1, 0], '%15.2f') + ',　'
                + n2s(ratio[st_index, 0], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 0], '%15.2f'))
    text.append('直応力＋せん断の複合：' + n2s(ratio[st_index - 1, 3], '%15.2f') + ',　'
                + n2s(ratio[st_index, 3], '%15.2f') + ',　' + n2s(ratio[st_index + 1, 3], '%15.2f'))

    return text


# ===========================================================================
# tension_analysis_text (引張材テキスト)
# ===========================================================================

# ---------------------------------------------------------------------------
# TH_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TH_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, LOAS_CASE_NAME, section_name):
    """H鋼（軸力）の断面算定詳細テキスト (TH_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BH(sectionsize, 1)
    ixs = BH(sectionsize, 4)
    iys = BH(sectionsize, 5)

    text = ['H鋼（軸力）に対する断面算定']
    text.append('軸力を考慮したH鋼の断面算定を行う')
    text.append('　　')
    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('H鋼サイズ：H-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = max(sectionsize[2], sectionsize[3])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('軸力検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TBOX_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TBOX_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                       section_no, LOAS_CASE_NAME, section_name):
    """角形鋼管（軸力）の断面算定詳細テキスト (TBOX_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BBOX(sectionsize, 1)
    ixs = BBOX(sectionsize, 4)
    iys = BBOX(sectionsize, 5)

    text = ['角形鋼管（軸力）に対する断面算定']
    text.append('軸力を考慮した鋼管の断面算定を行う')
    text.append('　　')
    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('角鋼管サイズ：□-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x' + n2s(sectionsize[2]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '断面二次半径(強)：' + n2s(ixs) + 'cm'
                + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[2]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        F = ALstressS[2, 0]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('軸力検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TP_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TP_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                     section_no, LOAS_CASE_NAME, section_name):
    """○鋼管（軸力）の断面算定詳細テキスト (TP_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BP(sectionsize, 1)
    Ix = BP(sectionsize, 2)
    ixs = BP(sectionsize, 4)

    text = ['○鋼管（軸力）に対する断面算定']
    text.append('軸力を考慮した鋼管の断面算定を行う')
    text.append('　　')
    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('○鋼管サイズ：○-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2' + '　断面二次モーメント(強)：' + n2s(Ix) + 'cm4'
                + '　断面二次半径：' + n2s(ixs) + 'cm')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    # 長期許容応力度計算
    if SN[0] == 1:
        t = sectionsize[1]
        ALstressS = ALST_S([SN[1], t])
        ft = ALstressS[0, 0]
        F = ALstressS[2, 0]
        STEEL_NAME = 'SN' + n2s(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + n2s(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + n2s(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + n2s(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + n2s(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + n2s(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise RuntimeError('角鋼管使用材料指定ミス')

    # 圧縮 (元コード: lamday も ixs で除している)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / ixs
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm' + '　圧縮フランジ間距離：'
                + n2s(length[2] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('使用材料：' + STEEL_NAME)
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('軸力検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TC_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TC_analysis_text(sectionsize, stress, timecase, SN, ele_no, section_no,
                     Lminus, LOAS_CASE_NAME, section_name):
    """溝形鋼の引張力に対する断面算定詳細テキスト (TC_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC(sectionsize, 1)

    text = ['溝形鋼の引張力に対する断面算定']
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('溝形鋼：C-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    text.append('断面欠損幅：' + n2s(Lminus) + 'mm')
    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 長期許容応力度計算
    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - (Lminus / 10 * sectionsize[2] / 10)
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
    if timecase == 2:
        ft = F

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')

    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    # 元コード: ext=strvcat(text,'　　'); のtypoにより '　　' は追加されない
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('引張検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TC2_analysis_text (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

def TC2_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                      section_no, Lminus, LOAS_CASE_NAME, section_name):
    """二丁溝形鋼(背中合わせ2C)の引張・圧縮力に対する断面算定詳細テキスト.

    TC2_analysis と同じく圧縮側は座屈を考慮したfcで検定する
    (length: 座屈長さ[m] [強軸, 弱軸]。TH_analysis_textと同じ扱い)。
    """
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BC2(sectionsize, 1)
    ixs = BC2(sectionsize, 4)
    iys = BC2(sectionsize, 5)

    text = ['二丁溝形鋼(背中合わせ)の引張・圧縮力に対する断面算定']
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('二丁溝形鋼：2C-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3])
                + '　(背中合わせ・すきま0を仮定)')

    text.append('断面欠損幅：' + n2s(Lminus) + 'mm　(両ウェブ貫通として2倍控除)')
    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 長期許容応力度計算
    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - 2 * (Lminus / 10 * sectionsize[2] / 10)
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮 (座屈)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('軸力検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TL_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TL_analysis_text(sectionsize, stress, timecase, SN, ele_no, section_no,
                     Lminus, LOAS_CASE_NAME, section_name):
    """山形鋼の引張力に対する断面算定詳細テキスト (TL_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL(sectionsize, 1)

    text = ['溝形鋼の引張力に対する断面算定']  # 元コードのまま (タイトルは溝形鋼表記)
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('山形鋼：L-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2]) + 'x' + n2s(sectionsize[3]))

    text.append('断面欠損幅：' + n2s(Lminus) + 'mm')
    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 長期許容応力度計算
    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - (Lminus / 10 * sectionsize[2] / 10)
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5
    if timecase == 2:
        ft = F

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')

    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    # 元コード: ext=strvcat(text,'　　'); のtypoにより '　　' は追加されない
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('引張検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TSB_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TSB_analysis_text(sectionsize, stress, timecase, SN, ele_no, section_no,
                      Lminus, LOAS_CASE_NAME, section_name):
    """中実角材（FBブレース）の引張力に対する断面算定詳細テキスト (TSB_analysis_text.m)."""
    n2s = _num2str
    sectionsize = list(np.asarray(sectionsize, dtype=float).ravel())
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能 ([a a_index]=max(...) → 大きい辺から欠損を控除)
    if sectionsize[0] >= sectionsize[1]:  # MATLAB max は先頭優先
        a_index = 1
    else:
        a_index = 2
    sectionsize[a_index - 1] = sectionsize[a_index - 1] - Lminus
    a = BSB(sectionsize, 1)

    text = ['中実角材（FBブレース）の引張力に対する断面算定/引張応力として検討']
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = np.abs(stress[:, 0] * 10 ** 3)
    Fx2 = np.abs(stress[:, 1] * 10 ** 3)

    print('ムク角鋼材のトラス要素は引張検定をおこなっている')

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('角材断面（中実）ボルト欠損考慮：■-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    # 長期許容応力度計算
    if SN[0] == 1:
        t = min(sectionsize[0], sectionsize[1])
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    if timecase == 2:
        ft = F
    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('引張検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TL2_analysis_text (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

def TL2_analysis_text(sectionsize, length, stress, timecase, SN, ele_no,
                      section_no, Lminus, LOAS_CASE_NAME, section_name):
    """二丁山形鋼(背中合わせ2L・等辺のみ)の引張・圧縮力に対する断面算定詳細テキスト.

    TL2_analysis と同じく圧縮側は座屈を考慮したfcで検定する
    (length: 座屈長さ[m] [強軸, 弱軸])。
    """
    n2s = _num2str
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    length = np.asarray(length, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BL2(sectionsize, 1)
    ixs = BL2(sectionsize, 4)
    iys = BL2(sectionsize, 5)

    text = ['二丁山形鋼(背中合わせ)の引張・圧縮力に対する断面算定']
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('二丁山形鋼：2L-' + n2s(sectionsize[0]) + 'x' + n2s(sectionsize[1]) + 'x'
                + n2s(sectionsize[2])
                + '　(背中合わせ・すきま0を仮定、等辺のみ対応)')

    text.append('断面欠損幅：' + n2s(Lminus) + 'mm　(両脚貫通として2倍控除)')
    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')

    # 長期許容応力度計算
    t = max(sectionsize[2], sectionsize[3])
    ALstressS = ALST_S([SN[1], t])
    F = ALstressS[2, 0]
    a = a - 2 * (Lminus / 10 * sectionsize[2] / 10)
    if SN[0] == 1:
        ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    # 圧縮 (座屈)
    lamdax = length[0] * 100 / ixs
    lamday = length[1] * 100 / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    if lamdadfc > Lamda:
        fc = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        fc = ((1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2)) * F

    if timecase == 2:
        ft = F
        fc = 1.5 * fc

    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')
    text.append('断面二次半径(強)：' + n2s(ixs) + 'cm' + '　断面二次半径(弱)：' + n2s(iys) + 'cm')

    text.append('　　')
    text.append('*部材支持条件')
    text.append('座屈長さ(強)：' + n2s(length[0] * 1000, '%15.0f') + 'mm' + '　座屈長さ(弱)：'
                + n2s(length[1] * 1000, '%15.0f') + 'mm')
    text.append('細長比(強)：' + n2s(lamdax, '%15.0f') + '　細長比(弱)：' + n2s(lamday, '%15.0f'))

    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2' + '　限界細長比：'
                + n2s(Lamda, '%15.0f'))

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100)
    sigma_cx2 = Fx2 / (a * 100)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        ratio_cx1 = np.abs(sigma_cx1) / fc
        ratio_cx2 = np.abs(sigma_cx2) / fc

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    text.append('圧縮許容応力度：' + n2s(fc, '%15.0f') + 'N/mm2')
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('軸力検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ---------------------------------------------------------------------------
# TSR_analysis_text.m
# ---------------------------------------------------------------------------

@_matlabenv
def TSR_analysis_text(sectionsize, stress, timecase, SN, ele_no, section_no,
                      LOAS_CASE_NAME, section_name):
    """中実鋼管（丸鋼ブレース）の引張力に対する断面算定詳細テキスト (TSR_analysis_text.m)."""
    n2s = _num2str
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    SN = np.asarray(SN, dtype=float).ravel()

    # 鋼材断面性能
    a = BSR(sectionsize, 1)

    RR = a  # 元コードのまま (断面積を径として表示)
    text = ['中実鋼管（丸鋼ブレース）の引張力に対する断面算定']
    text.append('　　')

    # 断面力取り込み（単位をN/mm2系にする．）
    Fx1 = stress[:, 0] * 10 ** 3
    Fx2 = stress[:, 1] * 10 ** 3

    text.append('断面符号：' + section_name + '断面番号：' + n2s(section_no) + '　要素番号：' + n2s(ele_no))
    text.append('　　')
    text.append('鋼管径（中実）：●-' + n2s(RR))

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        timecase = 1
        t_case = LOAS_CASE_NAME
    else:
        print('ERROR:長短期設定ミス')
        raise RuntimeError('長短期設定ミス')
    text.append('*断面性能')
    text.append('断面積：' + n2s(a) + 'cm2')

    # 長期許容応力度計算
    if SN[0] == 1:
        if SN[1] == 1080:
            F = SN[1] * SN[2]
            ft = F / 1.5
        else:
            t = sectionsize[0]
            ALstressS = ALST_S([SN[1], t])
            F = ALstressS[2, 0]
            ft = ALstressS[0, 0]
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5

    if timecase == 2:
        ft = F
    text.append('　　')
    text.append('*鋼材情報')
    text.append('F値(短期許容引張応力度)：' + n2s(F, '%15.0f') + 'N/mm2' + '　E：20500 N/mm2')

    # 応力度の計算ならびに検定値の計算
    sigma_cx1 = Fx1 / (a * 100 * 0.75)
    sigma_cx2 = Fx2 / (a * 100 * 0.75)

    if np.all(sigma_cx1 >= 0) and np.all(sigma_cx2 >= 0):
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft
    else:
        print('トラス材に圧縮！：断面ID/' + n2s(section_no, '%15.0f'))
        ratio_cx1 = np.abs(sigma_cx1) / ft
        ratio_cx2 = np.abs(sigma_cx2) / ft

    text.append('　　')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i端')
    text.append('軸力' + n2s(Fx1 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j端')
    text.append('軸力' + n2s(Fx2 / 1000, '%15.1f') + 'kN')

    text.append('　　')
    text.append('　　')
    text.append('*許容応力度　[' + t_case + ']')
    text.append('引張許容応力度：' + n2s(ft, '%15.0f') + 'N/mm2')
    # 元コード: ext=strvcat(text,'　　'); のtypoにより '　　' は追加されない
    text.append('　　')
    text.append('*検定比　[' + t_case + ']　**i端　j端の順に示す**')
    text.append('引張検定：' + n2s(ratio_cx1, '%15.2f') + ',　' + n2s(ratio_cx2, '%15.2f'))

    return text


# ===========================================================================
# S_ratio_analysis.m (privatetool_function/MIDAS/ratio/S/)
# ===========================================================================

@_matlabenv
def S_ratio_analysis(sectionsize, buck_length, stress, timecase, SN,
                     ele_no, maxratios, maxratios_text, section_no, stress_L,
                     c_up, H_beam_RCsupport, cb_judge, LOAS_CASE_NAME,
                     lb_table, pick_section_name, judge_H):
    """鉄骨部材の断面検定（曲げ圧縮部材の検定）ディスパッチャ.

    sectionsize は m 単位 (内部で×1000)。後ろから2番目の要素×1000が種別コード:
      1000:H, 2000:中実角, 3000:中実丸, 4000:○鋼管, 5000:溝形, 6000:HwCP,
      7000:角鋼管, 8000:CT, 9000:L形, 11000:テーパーH
    戻り値: (ratio_output, maxratios, maxratios_text, lb_table)
    maxratios = [要素番号, 最大検定比]。
    lb_table は list of list (行: [section_no ele_no lb iy H B tw tf])。
    ※MATLAB版 find_index は「見つからない=0」、Python版 util.find_index は
      「見つからない=-1」(0-based) のため判定を読み替えている。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel() * 1000
    buck_length = np.asarray(buck_length, dtype=float).ravel()
    maxratios = list(maxratios)
    if lb_table is None:
        lb_table = []
    else:
        lb_table = [list(r) for r in lb_table]
    n = max(sectionsize.shape)
    code = sectionsize[n - 2]

    if code == 1000:  # H鋼断面の検定
        ratio_output = H_analysis(sectionsize[0:n - 2], buck_length, stress, timecase,
                                  SN, H_beam_RCsupport, cb_judge, judge_H)
        # 断面サイズの後ろ二つはプログラム上でふった数字（どこから断面情報とってきたかのindex）なので検定では取り込まない．
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = H_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                             timecase, SN, ele_no, section_no,
                                             H_beam_RCsupport, cb_judge, LOAS_CASE_NAME,
                                             pick_section_name, judge_H)

        if len(lb_table) == 0 or find_index([r[0] for r in lb_table], section_no) == -1:
            iy = BH(sectionsize[0:n - 2], 5) * 10
            lb_table.append([section_no, ele_no, buck_length[2], iy]
                            + list(sectionsize[0:4]))
        else:
            idx = find_index([r[0] for r in lb_table], section_no)
            if lb_table[idx][1] < buck_length[2]:
                # 元コードのまま (2列目=要素番号, 3列目=横座屈長さ を更新)
                lb_table[idx][1] = ele_no
                lb_table[idx][2] = buck_length[2]

    elif code == 2000:  # 中実角断面の検定
        ratio_output = SB_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = SB_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                              timecase, SN, ele_no, section_no,
                                              LOAS_CASE_NAME, pick_section_name)

    elif code == 3000:  # 中実丸断面の検定
        ratio_output = SR_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = SR_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                              timecase, SN, ele_no, section_no,
                                              LOAS_CASE_NAME, pick_section_name)

    elif code == 4000:  # 鋼管断面の検定
        ratio_output = P_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = P_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                             timecase, SN, ele_no, section_no,
                                             LOAS_CASE_NAME, pick_section_name)

    elif code == 5000:  # 溝形鋼断面の検定
        ratio_output = C_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = C_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                             timecase, SN, ele_no, section_no,
                                             LOAS_CASE_NAME, pick_section_name)

    elif code == 6000:  # H鋼（ウェブカバープレート）断面の検定
        ratio_output = HwCP_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = HwCP_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                                timecase, SN, ele_no, section_no,
                                                LOAS_CASE_NAME, pick_section_name)

    elif code == 7000:  # 角鋼管断面の検定
        ratio_output = BOX_analysis(sectionsize[0:4], buck_length, stress, timecase, SN,
                                    stress_L, c_up)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = BOX_analysis_text(sectionsize[0:4], buck_length, stress,
                                               timecase, SN, ele_no, section_no,
                                               stress_L, c_up, LOAS_CASE_NAME,
                                               pick_section_name)

    elif code == 8000:  # CT断面の検定
        ratio_output = CT_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = CT_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                              timecase, SN, ele_no, section_no,
                                              LOAS_CASE_NAME, pick_section_name)

    elif code == 9000:  # 溝形鋼断面の検定 (元コメントのまま; 実際はL形鋼)
        ratio_output = L_analysis(sectionsize[0:n - 2], buck_length, stress, timecase, SN)
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = L_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                             timecase, SN, ele_no, section_no,
                                             LOAS_CASE_NAME, pick_section_name)

    elif code == 11000:  # テーパーH断面の検定
        ratio_output = HT_analysis(sectionsize[0:n - 2], buck_length, stress, timecase,
                                   SN, H_beam_RCsupport, cb_judge)
        # 断面サイズの後ろ二つはプログラム上でふった数字（どこから断面情報とってきたかのindex）なので検定では取り込まない．
        if np.max(np.max(ratio_output)) > max(np.atleast_1d(maxratios[1])):
            maxratios[0] = ele_no
            maxratios[1] = float(np.max(np.max(ratio_output)))
            maxratios_text = HT_analysis_text(sectionsize[0:n - 2], buck_length, stress,
                                              timecase, SN, ele_no, section_no,
                                              H_beam_RCsupport, cb_judge, LOAS_CASE_NAME,
                                              pick_section_name)

        # 元コード: この分岐は isempty ガードなし (lb_table 空だと find_index がエラー)
        if find_index([r[0] for r in lb_table], section_no) == -1:
            sectionsize2 = (sectionsize[0:7] + sectionsize[7:14]) / 2
            iy = BH(sectionsize2, 5) * 10
            lb_table.append([section_no, ele_no, buck_length[2], iy]
                            + list(sectionsize2[0:4]))
        else:
            idx = find_index([r[0] for r in lb_table], section_no)
            if lb_table[idx][1] < buck_length[2]:
                lb_table[idx][1] = ele_no
                lb_table[idx][2] = buck_length[2]

    # 該当コードなしの場合、MATLAB同様 ratio_output 未定義エラーとなる
    return ratio_output, maxratios, maxratios_text, lb_table
