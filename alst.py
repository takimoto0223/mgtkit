# -*- coding: utf-8 -*-
"""S造許容応力度 (ALST_S 等) の逐語移植.

元コード:
  msrc/structural_function/S/ALST_etc/ALST/ALST_S.m
  msrc/structural_function/S/ALST_etc/ALST/ALST_S_CFT.m
  msrc/structural_function/S/ALST_etc/ALST/sfc.m

注意 (元コードの挙動をそのまま維持):
  - ALST_S は最初の分岐が `length(SN)==1 || length(SN)==2` のため、
    [鋼種 板厚] の2要素入力でも常に板厚40mm以下扱いのF値となる
    (板厚による低減は事実上デッドコード。コメント「40mm低減解除のとき」)。
  - ALST_S_CFT は `length(SN)==1` のみのため板厚低減が有効。
  - SN情報エラー時、MATLABは ERROR 変数へ代入表示後そのまま進み
    F未定義でエラー停止する。Python版は print 後 UnboundLocalError となる。
"""

import math

import numpy as np


# ---------------------------------------------------------------------------
# ALST_S.m
# ---------------------------------------------------------------------------

def ALST_S(SN):
    """告示・令/S許容応力度計算.

    入力例 SN=400 もしくは SN=[400 50(mm)]
    [鋼種 板厚]を示し，鋼種は400 or 490 or 520
    板厚省略の時は40mm以下とする
    出力はALstressS =[引張 せん断]で
    1行目に長期，2行目に短期
    また三行目にF値と限界細長比を出力
    """
    SN = np.atleast_2d(np.asarray(SN, dtype=float))
    n = max(SN.shape) if SN.size > 0 else 0  # MATLAB length()

    ALstressS = np.zeros((3, 2))
    if n == 1 or n == 2:  # (40mm低減解除のとき)
        if SN[0, 0] == 400:
            F = 235
        elif SN[0, 0] == 490:
            F = 325
        elif SN[0, 0] == 520:
            F = 355
        elif SN[0, 0] == 295:
            F = 295
        elif SN[0, 0] == 235:
            F = 235
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 40:
        if SN[0, 0] == 400:
            F = 235
        elif SN[0, 0] == 490:
            F = 325
        elif SN[0, 0] == 520:
            F = 355
        elif SN[0, 0] == 295:
            F = 295
        elif SN[0, 0] == 235:
            F = 235
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 75:
        if SN[0, 0] == 400:
            F = 215
        elif SN[0, 0] == 490:
            F = 295
        elif SN[0, 0] == 520:
            F = 335
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 100:
        if SN[0, 0] == 400:
            F = 215
        elif SN[0, 0] == 490:
            F = 295
        elif SN[0, 0] == 520:
            F = 325
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 250:
        if SN[0, 0] == 400 or SN[0, 0] == 490:
            F = 215
        else:
            print("ERROR = 'SN情報エラー'")
    else:
        print("ERROR = 'SN情報エラー'")

    # 引張
    ALstressS[0:2, 0] = [F / 1.5, F]

    # せん断
    ALstressS[0:2, 1] = [F / math.sqrt(3) / 1.5, F / math.sqrt(3)]

    # F値および限界細長比
    ALstressS[2, :] = [F, 1500 / math.sqrt(F / 1.5)]

    return ALstressS


# ---------------------------------------------------------------------------
# ALST_S_CFT.m
# ---------------------------------------------------------------------------

def ALST_S_CFT(SN):
    """告示・令/S許容応力度計算 (CFT用: 板厚によるF値低減が有効).

    入力例 SN=400 もしくは SN=[400 50(mm)]
    出力構成は ALST_S と同じ。
    """
    SN = np.atleast_2d(np.asarray(SN, dtype=float))
    n = max(SN.shape) if SN.size > 0 else 0  # MATLAB length()

    ALstressS = np.zeros((3, 2))
    if n == 1:  # || length(SN)==2 %(40mm低減解除のとき) ← 元コードでコメントアウト
        if SN[0, 0] == 400:
            F = 235
        elif SN[0, 0] == 490:
            F = 325
        elif SN[0, 0] == 520:
            F = 355
        elif SN[0, 0] == 295:
            F = 295
        elif SN[0, 0] == 235:
            F = 235
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 40:
        if SN[0, 0] == 400:
            F = 235
        elif SN[0, 0] == 490:
            F = 325
        elif SN[0, 0] == 520:
            F = 355
        elif SN[0, 0] == 295:
            F = 295
        elif SN[0, 0] == 235:
            F = 235
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 75:
        if SN[0, 0] == 400:
            F = 215
        elif SN[0, 0] == 490:
            F = 295
        elif SN[0, 0] == 520:
            F = 335
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 100:
        if SN[0, 0] == 400:
            F = 215
        elif SN[0, 0] == 490:
            F = 295
        elif SN[0, 0] == 520:
            F = 325
        else:
            print("ERROR = 'SN情報エラー'")
    elif SN[0, 1] <= 250:
        if SN[0, 0] == 400 or SN[0, 0] == 490:
            F = 215
        else:
            print("ERROR = 'SN情報エラー'")
    else:
        print("ERROR = 'SN情報エラー'")

    # 引張
    ALstressS[0:2, 0] = [F / 1.5, F]

    # せん断
    ALstressS[0:2, 1] = [F / math.sqrt(3) / 1.5, F / math.sqrt(3)]

    # F値および限界細長比
    ALstressS[2, :] = [F, 1500 / math.sqrt(F / 1.5)]

    return ALstressS


# ---------------------------------------------------------------------------
# sfc.m
# ---------------------------------------------------------------------------

def sfc(ixs, iys, length, SN, t_max):
    """鉄骨許容圧縮応力度.

    座屈長さ，断面二次半径ともにmm単位系で入力
    戻り値 s_f_c = [長期, 短期] (要素2のndarray)
    """
    length = np.asarray(length, dtype=float).ravel()

    # 長期圧縮許容応力度計算
    SN_arr = np.atleast_1d(np.asarray(SN, dtype=float)).ravel()
    ALstressS = ALST_S(np.concatenate([SN_arr, [float(t_max)]]))
    F = ALstressS[2, 0]

    lamdax = length[0] / ixs
    lamday = length[1] / iys
    lamdadfc = max(lamdax, lamday)
    Lamda = 1500 / math.sqrt(F / 1.5)

    s_f_c = np.zeros(2)
    if lamdadfc > Lamda:
        s_f_c[0] = 18 / 65 / (lamdadfc / Lamda) ** 2 * F
    else:
        s_f_c[0] = (1 - 0.4 * (lamdadfc / Lamda) ** 2) / (1.5 + 2 / 3 * (lamdadfc / Lamda) ** 2) * F
    s_f_c[1] = 1.5 * s_f_c[0]
    return s_f_c
