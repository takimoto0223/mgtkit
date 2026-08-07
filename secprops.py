# -*- coding: utf-8 -*-
"""S造断面性能 (B*系) + section_ability の逐語移植.

元コード:
  msrc/structural_function/S/S_sectionability/BH.m
  msrc/structural_function/S/S_sectionability/BBOX.m
  msrc/structural_function/S/S_sectionability/BBOX_H.m
  msrc/structural_function/S/S_sectionability/BP.m
  msrc/structural_function/S/S_sectionability/BC.m
  msrc/structural_function/S/S_sectionability/BCC.m
  msrc/structural_function/S/S_sectionability/BCT.m
  msrc/structural_function/S/S_sectionability/BL.m
  msrc/structural_function/S/S_sectionability/BSB.m
  msrc/structural_function/S/S_sectionability/BSR.m
  msrc/structural_function/S/S_sectionability/BHwCP.m
  msrc/structural_function/S/S_sectionability/steelForm.m
  msrc/privatetool_function/MIDAS/ratio/section_ability.m

移植方針:
  - 式・分岐・丸め(excelround)・単位換算(入力mm, 出力cm系)は元コードのまま。
  - MATLABの stop (未定義関数エラーで停止) は RuntimeError で再現。
  - MATLABで範囲外indexエラーとなる呼び方 (例: BH に4要素入力) は
    Python でも IndexError となる (挙動維持のため救済しない)。
  - BC/BCC は C_data.mat の CB 行列、BL は L_data.mat の L 行列を参照する。
    原典の .mat (structural_function/S/S_sectionability/) をJSONへ変換して
    data/c_data.json / data/l_data.json に同梱し、自動読込する
    (2026-07-31。CC_data.mat は C_data.mat と同一内容のため C を使用)。
    set_C_data()/set_L_data() による差し替えも引き続き可能。
"""

import json
import math
import os

import numpy as np

from .util import excelround


# ---------------------------------------------------------------------------
# C_data.mat / L_data.mat 相当のデータ (data/*.json から自動読込)
# ---------------------------------------------------------------------------

def _load_table(fname, key):
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'data', fname)
        with open(path, encoding='utf-8') as f:
            return np.asarray(json.load(f)[key], dtype=float)
    except (OSError, KeyError, ValueError):
        return None


C_DATA = _load_table('c_data.json', 'CB')
# C_data.mat の変数 CB (溝形鋼テーブル, 列: 1:H 2:B 3:tw 4:tf 7:a 11:Ix 12:Iy 13:ix 14:iy 15:Zx 16:Zy)
L_DATA = _load_table('l_data.json', 'L')
# L_data.mat の変数 L  (山形鋼テーブル, 列: 1:H 2:B 3:t 6:a 12:ix 13:iy 15:Zx 16:Zy)


def set_C_data(CB):
    """C_data.mat の CB 行列を設定する."""
    global C_DATA
    C_DATA = np.asarray(CB, dtype=float)


def set_L_data(L):
    """L_data.mat の L 行列を設定する."""
    global L_DATA
    L_DATA = np.asarray(L, dtype=float)


def _stop(msg):
    """MATLABの stop (未定義関数エラー) 相当."""
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# BH.m
# ---------------------------------------------------------------------------

def BH(sectionsize, index):
    """H鋼（ビルト含）断面性能算定（JIS).

    入力はmm単位系
    入力例 BH([H,B1,tw,tf1,B2,tf2,r],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 8:ib, 9:eta,
         10:Af, 11:Aw 12:center 13:Zxp
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    if abs(sectionsize[4]) + abs(sectionsize[5]) > 0:
        if abs(sectionsize[4]) * abs(sectionsize[5]) == 0:
            print('上下フランジサイズの設定ミス')
            _stop('BH: 上下フランジサイズの設定ミス')
        H = sectionsize[0]
        B1 = sectionsize[1]
        tw = sectionsize[2]
        tf1 = sectionsize[3]
        B2 = sectionsize[4]
        tf2 = sectionsize[5]
        # 単位系のチェック(mm系になっているか？&mm系をさらに10^3してしまってないか？)
        if 10 <= H <= 10000:
            pass
        else:
            _stop('断面サイズ(mm)エラー値')
    else:
        H = sectionsize[0]
        B1 = sectionsize[1]
        tw = sectionsize[2]
        tf1 = sectionsize[3]
        B2 = sectionsize[1]
        tf2 = sectionsize[3]
        # 単位系のチェック
        if 10 <= H <= 10000:
            pass
        else:
            _stop('断面サイズ(mm)エラー値')

    if max(sectionsize.shape) < 7:
        r = 0.0
    else:
        r = sectionsize[6]

    rA = (1 - math.pi / 4) * r ** 2
    rd = (1 - 2 / (3 * (4 - math.pi))) * r
    rI = (1 / 3 - math.pi / 16 - 1 / (9 * (4 - math.pi))) * r ** 4

    a = (B1 * tf1) + (B2 * tf2) + tw * (H - tf1 - tf2) + 4 * rA

    # 図芯の算定
    center = ((B1 * tf1) * tf1 / 2 + (B2 * tf2) * (H - tf2 / 2)
              + tw * (H - tf1 - tf2) * ((H - tf1 - tf2) / 2 + tf1)
              + 2 * rA * (tf1 + rd) + 2 * rA * (H - tf2 - rd)) / a

    Ifl1 = (B1 * tf1 ** 3) / 12 + B1 * tf1 * (tf1 / 2 - center) ** 2
    Ifl2 = (B2 * tf2 ** 3) / 12 + B2 * tf2 * (H - tf2 / 2 - center) ** 2

    Iw = (tw * (H - tf1 - tf2) ** 3) / 12 + (tw * (H - tf1 - tf2)) * ((H - tf1 - tf2) / 2 + tf1 - center) ** 2
    Ir1 = rI + rA * (tf1 + rd - center) ** 2
    Ir2 = rI + rA * (H - tf2 - rd - center) ** 2

    Ix = Ifl1 + Ifl2 + Iw + 2 * Ir1 + 2 * Ir2

    Zx1 = Ix / center
    Zx2 = Ix / (H - center)

    Zx = min(Zx1, Zx2)

    Ifl1 = (tf1 * B1 ** 3) / 12
    Ifl2 = (tf2 * B2 ** 3) / 12
    Iw = ((H - tf1 - tf2) * tw ** 3) / 12
    Ir = rI + rA * (tw / 2 + rd) ** 2
    Iy = Ifl1 + Ifl2 + Iw + 4 * Ir

    Zy = Iy / (max(B1, B2) / 2)

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    Aff = min(B1 * tf1, B2 * tf2)
    if B1 * tf1 > B2 * tf2:
        Aww = tw * (H / 6 - tf2)
        AA = Aff + Aww + 2 * rA
        II = (tf2 * B2 ** 3) / 12 + ((H / 6 - tf2) * tw ** 3) / 12 + 2 * (rI + rA * (tw / 2 + rd) ** 2)
    else:
        Aww = tw * (H / 6 - tf1)
        AA = Aff + Aww + 2 * rA
        II = (tf1 * B1 ** 3) / 12 + ((H / 6 - tf1) * tw ** 3) / 12 + 2 * (rI + rA * (tw / 2 + rd) ** 2)

    ib = math.sqrt(II / AA)
    eta = ib * H / Aff

    Af = min(B1 * tf1, B2 * tf2)
    Aw = tw * (H - (tf1 + tf2) / 2)

    # Zxp
    Zxp = (B1 * tf1 * (center - tf1 / 2) + B2 * tf2 * (H - tf2 / 2 - center)
           + (center - tf1) * tw * (center - tf1) / 2
           + (H - tf2 - center) * tw * (H - tf2 - center) / 2
           + rA * (center - tf1 - rd) * 2 + rA * (H - tf2 - rd - center) * 2)

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    ib = ib / 10
    ib = excelround(ib, 3)
    eta = excelround(eta, 3)
    Af = Af / 100
    Af = excelround(Af, 4)
    Aw = Aw / 100
    Aw = excelround(Aw, 4)
    Zxp = Zxp / 1000
    Zxp = excelround(Zxp, 3)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    elif index == 8:
        return ib
    elif index == 9:
        return eta
    elif index == 10:
        return Af
    elif index == 11:
        return Aw
    elif index == 12:
        return center
    elif index == 13:
        return Zxp
    else:
        _stop('BH断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BBOX.m
# ---------------------------------------------------------------------------

def BBOX(sectionsize, index):
    """□角鋼管断面性能算定（JIS) 板厚違い対応版.

    入力はmm単位系 出力はcm系
    入力例 BBOX([D,B,tx,ty,(r)],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 11:Awx 12:Awy
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    D = sectionsize[0]
    B = sectionsize[1]
    tx = sectionsize[2]  # せい側板の板厚
    ty = sectionsize[3]  # はば側板の板厚

    t = min(tx, ty)  # フィレット半径算定用板厚は仮に板厚の小さい方にしておく

    if sectionsize.size == 5:
        r = sectionsize[4]
    elif sectionsize.size == 4:
        r = 0.0  # 材料がBCRなどではないのでビルトBOXと判定
        print('BOX断面はSN材よりビルト判定でr=0')
    # (それ以外のサイズは r 未定義 → MATLAB同様に後段でエラー)

    # 単位系のチェック
    if 10 <= D <= 10000:
        pass
    else:
        _stop('断面サイズ(mm)エラー値')

    if r == 0:  # フィレットない場合
        a = D * B - (D - 2 * ty) * (B - 2 * tx)
        Ix = B * D ** 3 / 12 - (B - 2 * tx) * (D - 2 * ty) ** 3 / 12
        Iy = D * B ** 3 / 12 - (D - 2 * ty) * (B - 2 * tx) ** 3 / 12

        Awx = 2 * D * tx  # x方向せん断面積
        Awy = 2 * B * ty  # y方向せん断面積
    else:  # フィレットある場合
        rA = math.pi / 4 * t * (2 * r - t)
        rd = 4 / (3 * math.pi) * (r ** 3 - (r - t) ** 3) / (r ** 2 - (r - t) ** 2)
        rI = math.pi / 16 * (r ** 4 - (r - t) ** 4) - (rA * rd ** 2)

        a = 2 * t * (D + B - 4 * r) + 4 * rA

        Ifx = ((B - 2 * r) * (D ** 3 - (D - 2 * ty) ** 3) + 2 * tx * (D - 2 * r) ** 3) / 12
        Ify = ((D - 2 * r) * (B ** 3 - (B - 2 * tx) ** 3) + 2 * ty * (B - 2 * r) ** 3) / 12
        Irx = rI + rA * ((D - 2 * r) / 2 + rd) ** 2
        Iry = rI + rA * ((B - 2 * r) / 2 + rd) ** 2

        Ix = Ifx + 4 * Irx
        Iy = Ify + 4 * Iry

        Awx = 2 * (tx * (D - 2 * r) + (math.pi * t * (2 * r - t)) / 4)
        Awy = 2 * (ty * (B - 2 * r) + (math.pi * t * (2 * r - t)) / 4)

    Zx = Ix / (D / 2)
    Zy = Iy / (B / 2)

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    Awx = Awx / 100
    Awx = excelround(Awx, 4)
    Awy = Awy / 100
    Awy = excelround(Awy, 4)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    elif index == 8:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 9:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 10:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 11:
        return Awx
    elif index == 12:
        return Awy
    else:
        _stop('BBOX断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BBOX_H.m
# ---------------------------------------------------------------------------

def BBOX_H(sectionsize, index):
    """□角鋼管断面性能算定（JIS).

    入力はmm単位系 出力はcm系
    入力例 BBOX_H([D,B,t,(dummy),(r)],型)  4要素時は r=2t
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 11:Awx 12:Awy
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    D = sectionsize[0]
    B = sectionsize[1]
    t = sectionsize[2]
    if sectionsize.size == 4:
        r = 2 * t
    elif sectionsize.size == 5:
        r = sectionsize[4]
    # (それ以外のサイズは r 未定義 → MATLAB同様に後段でエラー)

    # 単位系のチェック
    if 10 <= D <= 10000:
        pass
    else:
        _stop('断面サイズ(mm)エラー値')

    if r == 0:
        a = D * B - (D - 2 * t) * (B - 2 * t)
        Ix = B * D ** 3 / 12 - (B - 2 * t) * (D - 2 * t) ** 3 / 12
        Iy = D * B ** 3 / 12 - (D - 2 * t) * (B - 2 * t) ** 3 / 12

        Awx = 2 * D * t
        Awy = 2 * B * t
    else:
        rA = math.pi / 4 * t * (2 * r - t)
        rd = 4 / (3 * math.pi) * (r ** 3 - (r - t) ** 3) / (r ** 2 - (r - t) ** 2)
        rI = math.pi / 16 * (r ** 4 - (r - t) ** 4) - (rA * rd ** 2)

        a = 2 * t * (D + B - 4 * r) + 4 * rA

        Ifx = ((B - 2 * r) * (D ** 3 - (D - 2 * t) ** 3) + 2 * t * (D - 2 * r) ** 3) / 12
        Ify = ((D - 2 * r) * (B ** 3 - (B - 2 * t) ** 3) + 2 * t * (B - 2 * r) ** 3) / 12
        Irx = rI + rA * ((D - 2 * r) / 2 + rd) ** 2
        Iry = rI + rA * ((B - 2 * r) / 2 + rd) ** 2

        Ix = Ifx + 4 * Irx
        Iy = Ify + 4 * Iry

        Awx = 2 * (t * (D - 2 * r) + (math.pi * t * (2 * r - t)) / 4)
        Awy = 2 * (t * (B - 2 * r) + (math.pi * t * (2 * r - t)) / 4)

    Zx = Ix / (D / 2)
    Zy = Iy / (B / 2)

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    Awx = Awx / 100
    Awx = excelround(Awx, 4)
    Awy = Awy / 100
    Awy = excelround(Awy, 4)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    elif index == 8:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 9:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 10:
        _stop('BBOX断面性能データ型指定エラー')
    elif index == 11:
        return Awx
    elif index == 12:
        return Awy
    else:
        _stop('BBOX断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BP.m
# ---------------------------------------------------------------------------

def BP(sectionsize, index):
    """○鋼管断面性能算定（JIS).

    入力はmm単位系
    入力例 BP([D,t],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    D = sectionsize[0]
    t = sectionsize[1]

    # 単位系のチェック
    if 10 <= D <= 10000:
        pass
    else:
        _stop('断面サイズ(mm)エラー値')

    a = D * D * math.pi / 4 - (D - 2 * t) * (D - 2 * t) * math.pi / 4

    Ix = D ** 4 * math.pi / 64 - (D - 2 * t) ** 4 * math.pi / 64

    Zx = Ix / (D / 2)

    ixs = math.sqrt(Ix / a)

    # jt = 2 * pi * (D-t)^2 /4 * t;  # 231030(金澤)ねじれ抵抗係数追加 (コメントアウトのまま)

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Ix
    elif index == 4:
        return ixs
    elif index == 5:
        return ixs
    elif index == 6:
        return Zx
    elif index == 7:
        return Zx
    elif index == 8:
        _stop('BP断面性能データ型指定エラー')
    elif index == 9:
        _stop('BP断面性能データ型指定エラー')
    elif index == 10:
        _stop('BP断面性能データ型指定エラー')
    elif index == 11:
        _stop('BP断面性能データ型指定エラー')
    else:
        _stop('BP断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BC.m
# ---------------------------------------------------------------------------

def BC(sectionsize, index):
    """溝形鋼断面性能算定（JIS) C_data.mat(CB)参照.

    入力はmm単位系 出力はcm系
    入力例 BC([H B tw tf],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 10:Af 11:Aw
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    # 単位系のチェック
    if 10 <= H <= 10000:
        pass
    else:
        print('ERROR:断面サイズ(mm)エラー値')
        _stop('断面サイズ(mm)エラー値')

    if C_DATA is None:
        raise RuntimeError('C_data.mat のデータ(CB)が未設定です。'
                           'mgtkit.secprops.set_C_data(CB) で設定してください。')

    pickup_ability = None
    CB = C_DATA
    for i in range(CB.shape[0]):
        sub_CB = CB[i, :]
        if H == sub_CB[0] and B == sub_CB[1] and tw == sub_CB[2] and tf == sub_CB[3]:
            pickup_ability = sub_CB

    if pickup_ability is None:
        print('ERROR:溝形断面入力数値エラー')
        _stop('溝形断面入力数値エラー')

    a = pickup_ability[6] * 100
    Af = (a - (tw * (H - 2 * tf))) / 2
    Af = Af / 100
    Af = excelround(Af, 4)
    Aw = tw * H
    Aw = Aw / 100
    Aw = excelround(Aw, 4)

    if index == 1:
        return pickup_ability[6]
    elif index == 2:
        return pickup_ability[10]
    elif index == 3:
        return pickup_ability[11]
    elif index == 4:
        return pickup_ability[12]
    elif index == 5:
        return pickup_ability[13]
    elif index == 6:
        return pickup_ability[14]
    elif index == 7:
        return pickup_ability[15]
    elif index == 8:
        _stop('溝形鋼断面性能データ型指定エラー')
    elif index == 9:
        _stop('溝形鋼断面性能データ型指定エラー')
    elif index == 10:
        return Af
    elif index == 11:
        return Aw
    elif index == 12:
        _stop('溝形鋼断面性能データ型指定エラー')
    else:
        _stop('溝形鋼断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BCC.m
# ---------------------------------------------------------------------------

def BCC(sectionsize, index):
    """軽量形鋼断面性能算定（JIS) C_data.mat(CB)参照 (BCと同一テーブル).

    入力はmm単位系 出力はcm系
    入力例 BCC([D B tw tf],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 10:Af 11:Aw
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    if 10 <= H <= 10000:
        pass
    else:
        print('ERROR:断面サイズ(mm)エラー値')
        _stop('断面サイズ(mm)エラー値')

    if C_DATA is None:
        raise RuntimeError('C_data.mat のデータ(CB)が未設定です。'
                           'mgtkit.secprops.set_C_data(CB) で設定してください。')

    pickup_ability = None
    CB = C_DATA
    for i in range(CB.shape[0]):
        sub_CB = CB[i, :]
        if H == sub_CB[0] and B == sub_CB[1] and tw == sub_CB[2] and tf == sub_CB[3]:
            pickup_ability = sub_CB

    if pickup_ability is None:
        print('ERROR:軽量形鋼断面入力数値エラー')
        _stop('軽量形鋼断面入力数値エラー')

    a = pickup_ability[6] * 100
    Af = (a - (tw * (H - 2 * tf))) / 2
    Af = Af / 100
    Af = excelround(Af, 4)
    Aw = tw * H
    Aw = Aw / 100
    Aw = excelround(Aw, 4)

    if index == 1:
        return pickup_ability[6]
    elif index == 2:
        return pickup_ability[10]
    elif index == 3:
        return pickup_ability[11]
    elif index == 4:
        return pickup_ability[12]
    elif index == 5:
        return pickup_ability[13]
    elif index == 6:
        return pickup_ability[14]
    elif index == 7:
        return pickup_ability[15]
    elif index == 8:
        _stop('軽量形鋼断面性能データ型指定エラー')
    elif index == 9:
        _stop('軽量形鋼断面性能データ型指定エラー')
    elif index == 10:
        return Af
    elif index == 11:
        return Aw
    elif index == 12:
        _stop('軽量形鋼断面性能データ型指定エラー')
    else:
        _stop('軽量形鋼断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BC2 (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

def _BC_Cx(sectionsize):
    """単材溝形鋼のウェブ背面から図心までの距離Cx[cm] (C_data.mat CB列9参照).

    BC.m と同じ検索ロジックで CB テーブルから該当行を引き当てる
    (BC本体は改変しない: MATLAB逐語移植としての整合性を保つため、
    Cx取得専用の小関数として分離)。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    H, B, tw, tf = sectionsize[0], sectionsize[1], sectionsize[2], sectionsize[3]
    if C_DATA is None:
        raise RuntimeError('C_data.mat のデータ(CB)が未設定です。'
                           'mgtkit.secprops.set_C_data(CB) で設定してください。')
    pickup_ability = None
    CB = C_DATA
    for i in range(CB.shape[0]):
        sub_CB = CB[i, :]
        if H == sub_CB[0] and B == sub_CB[1] and tw == sub_CB[2] and tf == sub_CB[3]:
            pickup_ability = sub_CB
    if pickup_ability is None:
        print('ERROR:溝形断面入力数値エラー')
        _stop('溝形断面入力数値エラー')
    return pickup_ability[9]


def BC2(sectionsize, index):
    """二丁溝形鋼(背中合わせ2C)断面性能算定 - mgtkit独自拡張 (MATLAB原典なし).

    MIDAS/Genの組立断面 '2C' (二丁合わせ溝形鋼、ブレース等の圧縮引張材で
    使用) は、単材の溝形鋼(C)を隙間なく(すきま0)ウェブ同士を背中合わせに
    2枚組み合わせた断面として算定する。MIDASのmgtデータにはこの組合せの
    離隔寸法が別途出力されないため、最も一般的な「すきま0で密着」を前提
    とする。実際の詳細でウェブ間に隙間(ガセットプレート厚等)がある場合は
    本関数の ex 算定式を要修正 (ex = 単材Cx + 隙間/2 とする)。

    入力はmm単位系 出力はcm系
    入力例 BC2([H B tw tf],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy
      (Ix/ixs: 背中合わせ面に直交する軸=単材Cと同一の強軸。
       Iy/iys: 背中合わせ面を通る軸=新たな図心軸(合成弱軸)。)
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    H = sectionsize[0] / 10.0  # cm
    B = sectionsize[1] / 10.0  # cm

    a1 = BC(sectionsize, 1)
    Ix1 = BC(sectionsize, 2)
    Iy1 = BC(sectionsize, 3)
    ex = _BC_Cx(sectionsize)  # 単材の背面(合成断面の図心面)から単材図心までの距離 cm

    a = 2 * a1
    Ix = 2 * Ix1
    Iy = 2 * (Iy1 + a1 * ex ** 2)
    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)
    Zx = Ix / (H / 2)
    Zy = Iy / B

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    else:
        _stop('二丁溝形鋼断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BCT.m
# ---------------------------------------------------------------------------

def BCT(sectionsize, index):
    """CT断面性能算定（JIS).

    入力はmm単位系
    入力例 BCT([H,B,tw,tf,r],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zxt, 7:Zxb, 8:Zy, 9:c,
         10:Af, 11:Aw 12:ib_t 13:eta_t 14:ib_b 15:eta_b
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    # 単位系のチェック
    if 10 <= H <= 10000:
        pass
    else:
        _stop('断面サイズ(mm)エラー値')

    if max(sectionsize.shape) == 4:
        r = 0.0
    else:
        r = sectionsize[4]

    Af = B * tf
    Aw = tw * (H - tf)
    rA = (1 - math.pi / 4) * r ** 2
    rd = (1 - 2 / (3 * (4 - math.pi))) * r
    rI = (1 / 3 - math.pi / 16 - 1 / (9 * (4 - math.pi))) * r ** 4

    a = Af + Aw + 2 * rA

    sf = Af * (H - tf / 2)
    sw = Aw * ((H - tf) / 2)
    sr = 2 * rA * (H - tf - rd)

    # 断面1次モーメント
    s = sf + sw + sr

    # 図心位置
    c = s / a

    Ifxo = B * tf ** 3 / 12
    Iwxo = tw * (H - tf) ** 3 / 12

    Ifx = Ifxo + Af * (H - tf / 2 - c) ** 2
    Iwx = Iwxo + Aw * ((H - tf) / 2 - c) ** 2
    Irx = 2 * (rI + rA * (H - tf - rd - c) ** 2)
    Ix = Ifx + Iwx + Irx

    Ify = tf * B ** 3 / 12
    Iwy = (H - tf) * tw ** 3 / 12
    Iry = 2 * (rI + rA * (tw / 2 + rd) ** 2)
    Iy = Ify + Iwy + Iry

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    # 上側断面係数(負曲げで決まる場合）
    Zxt = Ix / (H - c)
    # 下側断面係数（正曲げで決まる場合）
    Zxb = Ix / c
    # 弱軸断面係数
    Zy = Iy / (B / 2)

    # 正曲げfb検定用（横座屈）
    Aff = B * min(H / 6, tf)
    Aww = tw * max(H / 6 - tf, 0)

    if H / 6 < tf:
        rI = 0.0
        rA = 0.0

    AA = Aff + Aww + 2 * rA
    II = (min(H / 6, tf) * B ** 3) / 12 + (max(H / 6 - tf, 0) * tw ** 3) / 12 + 2 * (rI + rA * (tw / 2 + rd) ** 2)
    ib_t = math.sqrt(II / AA)
    eta_t = ib_t * H / Aff

    # 負曲げfb検定用（横座屈）
    Aww = tw * H / 6

    AA = Aww
    II = H / 6 * tw ** 3 / 12
    ib_b = math.sqrt(II / AA)
    eta_b = ib_b * H / Aww

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zxt = Zxt / 1000
    Zxt = excelround(Zxt, 3)
    Zxb = Zxb / 1000
    Zxb = excelround(Zxb, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    c = c / 10
    c = excelround(c, 3)
    Af = Af / 100
    Af = excelround(Af, 4)
    Aw = Aw / 100
    Aw = excelround(Aw, 4)
    ib_t = ib_t / 10
    ib_t = excelround(ib_t, 3)
    eta_t = excelround(eta_t, 3)
    ib_b = ib_b / 10
    ib_b = excelround(ib_b, 3)
    eta_b = excelround(eta_b, 3)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zxt
    elif index == 7:
        return Zxb
    elif index == 8:
        return Zy
    elif index == 9:
        return c
    elif index == 10:
        return Af
    elif index == 11:
        return Aw
    elif index == 12:
        return ib_t
    elif index == 13:
        return eta_t
    elif index == 14:
        return ib_b
    elif index == 15:
        return eta_b
    else:
        _stop('BH断面性能データ型指定エラー')  # 元コードのメッセージのまま


# ---------------------------------------------------------------------------
# BL.m
# ---------------------------------------------------------------------------

def BL(sectionsize, index):
    """山形鋼断面性能算定（JIS) L_data.mat(L)参照.

    入力はmm単位系 出力はcm系
    入力例 BL([H B t],型)
    型は 1:a, 4:ixs, 5:iys, 6:Zx, 7:Zy, 10:Af, 11:Aw
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    H = sectionsize[0]
    B = sectionsize[1]
    t = sectionsize[2]

    # 単位系のチェック
    if 10 <= H <= 10000:
        pass
    else:
        print('ERROR:断面サイズ(mm)エラー値')
        _stop('断面サイズ(mm)エラー値')

    if L_DATA is None:
        raise RuntimeError('L_data.mat のデータ(L)が未設定です。'
                           'mgtkit.secprops.set_L_data(L) で設定してください。')

    pickup_ability = None
    L = L_DATA
    for i in range(L.shape[0]):
        sub_L = L[i, :]
        if H == sub_L[0] and B == sub_L[1] and t == sub_L[2]:
            pickup_ability = sub_L

    Aw = t * H
    Aw = Aw / 100
    Aw = excelround(Aw, 4)

    Af = t * B
    Af = Af / 100
    Af = excelround(Af, 4)

    if pickup_ability is None:
        print('ERROR:溝形断面入力数値エラー')  # 元コードのメッセージのまま
        _stop('溝形断面入力数値エラー')

    if index == 1:
        return pickup_ability[5]
    elif index == 4:
        return pickup_ability[11]
    elif index == 5:
        return pickup_ability[12]
    elif index == 6:
        return pickup_ability[14]
    elif index == 7:
        return pickup_ability[15]
    elif index == 8:
        _stop('山形鋼断面性能データ型指定エラー')
    elif index == 9:
        _stop('山形鋼断面性能データ型指定エラー')
    elif index == 10:
        return Af
    elif index == 11:
        return Aw
    elif index == 12:
        _stop('山形鋼断面性能データ型指定エラー')
    else:
        _stop('山形鋼断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BL2 (mgtkit独自拡張。MATLAB原典なし)
# ---------------------------------------------------------------------------

def _BL_pickup(sectionsize):
    """単材山形鋼のL_data.mat(L)該当行を引き当てる (BL.mと同じ検索ロジック)."""
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    H, B, t = sectionsize[0], sectionsize[1], sectionsize[2]
    if L_DATA is None:
        raise RuntimeError('L_data.mat のデータ(L)が未設定です。'
                           'mgtkit.secprops.set_L_data(L) で設定してください。')
    pickup_ability = None
    L = L_DATA
    for i in range(L.shape[0]):
        sub_L = L[i, :]
        if H == sub_L[0] and B == sub_L[1] and t == sub_L[2]:
            pickup_ability = sub_L
    if pickup_ability is None:
        print('ERROR:溝形断面入力数値エラー')  # 元コードのメッセージのまま
        _stop('溝形断面入力数値エラー')
    return pickup_ability


def BL2(sectionsize, index):
    """二丁山形鋼(背中合わせ2L)断面性能算定 - mgtkit独自拡張 (MATLAB原典なし).

    等辺山形鋼(H==B)のみ対応。JIS山形鋼テーブル(L_data.mat)には図心距離が
    Cx相当の1列(等辺の場合はCx=Cyと一致)しか含まれず、不等辺山形鋼のCyを
    復元できないため、H!=Bの場合はエラーとする。

    背中合わせの向き: 一方の脚同士を隙間0で密着させる、最も一般的な構成
    (BC2の背中合わせ溝形鋼と同じ考え方)。

    入力はmm単位系 出力はcm系
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys
    (Zx/Zy(6,7)は山形鋼の図心が非対称なため未対応。トラス材の引張圧縮検定
    はa/ixs/iysのみで足りるため実用上は問題ない)
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    H, B = sectionsize[0], sectionsize[1]
    if H != B:
        _stop('二丁山形鋼(2L)は等辺山形鋼(H=B)のみ対応です。'
             '不等辺山形鋼はJISテーブルの図心データ不足のため算定できません')

    pickup_ability = _BL_pickup(sectionsize)
    a1 = pickup_ability[5]
    Ix1 = pickup_ability[8]
    ex = pickup_ability[7]  # 等辺のため Cx=Cy

    a = 2 * a1
    Ix = 2 * Ix1
    Iy = 2 * (Ix1 + a1 * ex ** 2)  # 等辺のため Iy1=Ix1
    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    else:
        _stop('二丁山形鋼断面性能データ型指定エラー(Zx/Zyは未対応)')


# ---------------------------------------------------------------------------
# BSB.m
# ---------------------------------------------------------------------------

def BSB(sectionsize, index):
    """■ﾑｸ角鋼管断面性能算定（JIS).

    入力はmm単位系
    入力例 BSB([D,B],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 8:ib, 9:eta
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    D = sectionsize[0]
    B = sectionsize[1]
    # 単位系のチェック (元コードはチェック内容がコメントアウトされている)
    if 5 <= D <= 10000:
        pass
    else:
        pass  # 元コード: D表示・stopともコメントアウト

    a = D * B

    Ix = D ** 3 * B / 12

    Zx = Ix / (D / 2)

    Iy = B ** 3 * D / 12

    Zy = Iy / (B / 2)

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    # 負曲げfb検定用（横座屈）
    AA = D * B / 6

    II = (D / 6 * B ** 3) / 12
    ib = math.sqrt(II / AA)
    eta = ib * D / AA

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    ib = ib / 10
    ib = excelround(ib, 3)
    eta = excelround(eta, 3)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    elif index == 8:
        return ib
    elif index == 9:
        return eta
    elif index == 10:
        _stop('BSB断面性能データ型指定エラー')
    elif index == 11:
        _stop('BSB断面性能データ型指定エラー')
    else:
        _stop('BSB断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BSR.m
# ---------------------------------------------------------------------------

def BSR(sectionsize, index):
    """●ﾑｸ鋼管断面性能算定（JIS).

    入力はmm単位系
    入力例 BSR(D,型) もしくは BSR([D],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy
    """
    sectionsize = np.atleast_1d(np.asarray(sectionsize, dtype=float)).ravel()

    D = sectionsize[0]
    # 単位系のチェック
    if 10 <= D <= 10000:
        pass
    else:
        _stop('断面サイズ(mm)エラー値')

    a = D * D * math.pi / 4

    Ix = D ** 4 * math.pi / 64

    Zx = Ix / (D / 2)

    ixs = math.sqrt(Ix / a)

    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Ix
    elif index == 4:
        return ixs
    elif index == 5:
        return ixs
    elif index == 6:
        return Zx
    elif index == 7:
        return Zx
    elif index == 8:
        _stop('BSR断面性能データ型指定エラー')
    elif index == 9:
        _stop('BSR断面性能データ型指定エラー')
    elif index == 10:
        _stop('BSR断面性能データ型指定エラー')
    elif index == 11:
        _stop('BSR断面性能データ型指定エラー')
    else:
        _stop('BSR断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# BHwCP.m
# ---------------------------------------------------------------------------

def BHwCP(sectionsize, index):
    """カバープレート付H鋼断面性能算定（JIS).

    入力はmm単位系
    入力例 BHwCP([H,B,tw,tf,H_CP,t_CP],型)
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 11:Awx 12:Awy
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()

    H = sectionsize[0]
    B = sectionsize[1]
    tw = sectionsize[2]
    tf = sectionsize[3]

    H_CP = sectionsize[4]
    t_CP = sectionsize[5]

    a = 2 * B * tf + (tw + 2 * t_CP) * (H - 2 * tf)

    Ifl = (B * tf ** 3) / 12 + B * tf * ((H - tf) / 2) ** 2
    Iw = (tw * (H - 2 * tf) ** 3) / 12
    Icp = 2 * t_CP * (H - 2 * tf) ** 3 / 12
    Ix = 2 * Ifl + Iw + Icp

    Zx = Ix / (H / 2)

    Ifl = (tf * B ** 3) / 12
    Iw = ((H - 2 * tf) * tw ** 3) / 12
    Icp = ((H - 2 * tf) * t_CP ** 3) / 12 + (H - 2 * tf) * t_CP * (H_CP / 2) ** 2
    Iy = 2 * Ifl + Iw + 2 * Icp

    Zy = Iy / (B / 2)

    ixs = math.sqrt(Ix / a)
    iys = math.sqrt(Iy / a)

    Awx = (tw + 2 * t_CP) * (H - 2 * tf)
    Awy = 2 * B * tf
    # mm単位からcm系に変換
    a = a / 100
    a = excelround(a, 4)
    Ix = Ix / 10000
    Ix = excelround(Ix, 3)
    Iy = Iy / 10000
    Iy = excelround(Iy, 3)
    Zx = Zx / 1000
    Zx = excelround(Zx, 3)
    Zy = Zy / 1000
    Zy = excelround(Zy, 3)
    ixs = ixs / 10
    ixs = excelround(ixs, 3)
    iys = iys / 10
    iys = excelround(iys, 3)
    Awx = Awx / 100
    Awx = excelround(Awx, 4)
    Awy = Awy / 100
    Awy = excelround(Awy, 4)

    if index == 1:
        return a
    elif index == 2:
        return Ix
    elif index == 3:
        return Iy
    elif index == 4:
        return ixs
    elif index == 5:
        return iys
    elif index == 6:
        return Zx
    elif index == 7:
        return Zy
    elif index == 8:
        _stop('BHwCP断面性能データ型指定エラー')
    elif index == 9:
        _stop('BHwCP断面性能データ型指定エラー')
    elif index == 10:
        _stop('BHwCP断面性能データ型指定エラー')
    elif index == 11:
        return Awx
    elif index == 12:
        return Awy
    else:
        _stop('BHwCP断面性能データ型指定エラー')


# ---------------------------------------------------------------------------
# steelForm.m
# ---------------------------------------------------------------------------

def BPLUS(sectionsize, index):
    """BPLUS.m の逐語移植 (H鋼による十字型鋼断面性能算定, JIS).

    入力はmm単位系。要素数4(単一H由来の十字, フィレットr有り時は5)または
    8(2種のH鋼を十字配置: H1,B1,tw1,tf1,H2,B2,tw2,tf2)。
    型 index は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 8:ib, 9:eta,
    10:Af, 11:Aw。出力はcm系(BHと同じ)。
    SRC Cross-H(RH2T)では steelForm から要素数8で呼ばれる(ib/etaは未算定)。
    """
    ss = list(np.asarray(sectionsize, dtype=float).ravel())
    if len(ss) == 4 or len(ss) == 8:
        r = 0.0
    else:
        r = ss[4]  # MATLAB sectionsize(5)
        del ss[4]

    if len(ss) == 4:
        H, B, tw, tf = ss[0], ss[1], ss[2], ss[3]
        if not (10 <= H <= 10000):
            _stop('BPLUS: 断面サイズ(mm)エラー値')
        rA = (1 - math.pi / 4) * r ** 2
        rd = (1 - 2 / (3 * (4 - math.pi))) * r
        rI = (1 / 3 - math.pi / 16 - 1 / (9 * (4 - math.pi))) * r ** 4

        a = 2 * B * tf + tw * (H - 2 * tf) + 4 * rA
        a = 2 * a - tw ** 2

        Ifl = (B * tf ** 3) / 12 + B * tf * ((H - tf) / 2) ** 2
        Iw = (tw * (H - 2 * tf) ** 3) / 12
        Ir = rI + rA * ((H - 2 * tf) / 2 - rd) ** 2
        Ix = 2 * Ifl + Iw + 4 * Ir

        Ifl = (tf * B ** 3) / 12
        Iw = ((H - 2 * tf) * tw ** 3) / 12
        Ir = rI + rA * (tw / 2 + rd) ** 2
        Iy = 2 * Ifl + Iw + 4 * Ir

        Ix = Ix + Iy - tf ** 4 / 12
        Iy = Ix

        Zx = Ix / (H / 2)
        Zy = Iy / (H / 2)
        ixs = math.sqrt(Ix / a)
        iys = math.sqrt(Iy / a)

        Aff = B * tf
        Aww = tw * (H / 6 - tf)
        AA = Aff + Aww + 2 * rA
        II = (tf * B ** 3) / 12 + ((H / 6 - tf) * tw ** 3) / 12 \
            + 2 * (rI + rA * (tw / 2 + rd) ** 2)
        ib = math.sqrt(II / AA)
        eta = ib * H / Aff

        Af = B * tf
        Aw = tw * (H - 2 * tf)

        a = excelround(a / 100, 4)
        Ix = excelround(Ix / 10000, 3)
        Iy = excelround(Iy / 10000, 3)
        Zx = excelround(Zx / 1000, 3)
        Zy = excelround(Zy / 1000, 3)
        ixs = excelround(ixs / 10, 3)
        iys = excelround(iys / 10, 3)
        ib = excelround(ib / 10, 3)
        eta = excelround(eta, 3)
        Af = excelround(Af / 100, 4)
        Aw = excelround(Aw / 100, 4)
        # MATLAB原典の4要素分岐は index 8/9(ib/eta) の返却caseが無く
        # エラーになる (ib/eta は計算のみで未返却)。逐語再現のため含めない。
        table = {1: a, 2: Ix, 3: Iy, 4: ixs, 5: iys, 6: Zx, 7: Zy,
                 10: Af, 11: Aw}

    elif len(ss) == 8:
        H1, B1, tw1, tf1, H2, B2, tw2, tf2 = ss
        if not (10 <= H1 <= 10000):
            _stop('BPLUS: 断面サイズ(mm)エラー値')

        a1 = 2 * B1 * tf1 + tw1 * (H1 - 2 * tf1)
        a2 = 2 * B2 * tf2 + tw2 * H2 * 2
        a = a1 + a2

        Ifl1 = (B1 * tf1 ** 3) / 12 + B1 * tf1 * ((H1 - tf1) / 2) ** 2
        Iw1 = (tw1 * (H1 - 2 * tf1) ** 3) / 12
        Ix1 = 2 * Ifl1 + Iw1
        Ifl2 = (tf2 * B2 ** 3) / 12
        Iw2 = ((H2 * 2) * tw2 ** 3) / 12
        Ix2 = 2 * Ifl2 + Iw2
        Ix = Ix1 + Ix2

        Ifl1 = (B2 * tf2 ** 3) / 12 + B2 * tf2 * ((H2 * 2 - tf2) / 2) ** 2
        Iw1 = (tw2 * (H2 * 2 - 2 * tf2) ** 3) / 12
        Ix1 = 2 * Ifl1 + Iw1
        Ifl2 = (tf1 * B1 ** 3) / 12
        Iw2 = ((H1 - 2 * tf1) * tw1 ** 3) / 12
        Ix2 = 2 * Ifl2 + Iw2
        Iy = Ix1 + Ix2

        Zx = Ix / (H1 / 2)
        Zy = Iy / (H2)
        ixs = math.sqrt(Ix / a)
        iys = math.sqrt(Iy / a)

        Af = 2 * B1 * tf1 + 2 * H2 * tw2
        Aw = tw1 * (H1 - 2 * tf1) + 2 * B2 * tf2

        a = excelround(a / 100, 4)
        Ix = excelround(Ix / 10000, 3)
        Iy = excelround(Iy / 10000, 3)
        Zx = excelround(Zx / 1000, 3)
        Zy = excelround(Zy / 1000, 3)
        ixs = excelround(ixs / 10, 3)
        iys = excelround(iys / 10, 3)
        Af = excelround(Af / 100, 4)
        Aw = excelround(Aw / 100, 4)
        table = {1: a, 2: Ix, 3: Iy, 4: ixs, 5: iys, 6: Zx, 7: Zy,
                 10: Af, 11: Aw}
    else:
        _stop('BPLUS: 断面サイズ要素数エラー')

    if index in table:
        return table[index]
    _stop('BPLUS断面性能データ型指定エラー')


def steelForm(sForm, index):
    """鉄骨の断面種別を判別し，断面性能データを取得.

    CFT SRC断面内の鉄骨判別に利用するサブプログラム
    sForm は一要素目に断面種別を区別できるものを含む．
    例えばsForm(1)=1であればその2要素目以降に続くのはH鋼の断面情報
    型は 1:a, 2:Ix, 3:Iy, 4:ixs, 5:iys, 6:Zx, 7:Zy, 8:ib, 9:eta, 10:Af, 11:Aw
    """
    sForm = list(np.asarray(sForm, dtype=float).ravel())

    sc_type = sForm[0]

    if sc_type == 1:  # H鋼
        del sForm[0]
        return BH(sForm + [0, 0, 0], index)
    elif sc_type == 2:  # 十字形
        del sForm[0]
        return BPLUS(sForm, index)  # 十字形鋼 (BPLUS 移植済 2026-07-09)
    elif sc_type == 3:  # ○鋼管
        del sForm[0]
        return BP(sForm, index)
    elif sc_type == 4:  # □鋼管
        del sForm[0]
        return BBOX(sForm, index)
    # 該当なしの場合 MATLAB では sc_value 未割当エラー
    raise RuntimeError('steelForm: 断面種別判定不可 (MATLAB: sc_value未割当エラー)')


# ---------------------------------------------------------------------------
# section_ability.m (privatetool_function/MIDAS/ratio/)
# ---------------------------------------------------------------------------

def section_ability(sectionsize, index):
    """断面種別コードから該当するB*関数を呼び出す (入力はm単位系).

    sectionsize の後ろから2番目の要素が種別コード (×1000後):
      1000:H, 2000:中実角, 3000:中実丸, 4000:○鋼管, 5000:溝形,
      6000:HwCP, 7000:角鋼管, 8000:CT, 9000:L形, 11000:テーパーH,
      18000:二丁溝形鋼2C (mgtkit拡張), 19000:二丁山形鋼2L (mgtkit拡張)
    元コード同様、先頭要素と末尾2要素は断面情報に含めない
    (section_one = sectionsize(2:end-2))。
    """
    sectionsize = np.asarray(sectionsize, dtype=float).ravel() * 1000
    n = max(sectionsize.shape)
    section_one = sectionsize[1:n - 2]

    code = sectionsize[n - 2]
    if code == 1000:  # H鋼断面の検定
        return BH(section_one, index)
    elif code == 2000:  # 中実角断面の検定
        return BSB(section_one, index)
    elif code == 3000:  # 中実丸断面の検定
        return BSR(section_one, index)
    elif code == 4000:  # 鋼管断面の検定
        return BP(section_one, index)
    elif code == 5000:  # 溝形鋼断面の検定
        return BC(section_one, index)
    elif code == 6000:  # H鋼（ウェブカバープレート）断面の検定
        return BHwCP(section_one, index)
    elif code == 7000:  # 角鋼管断面の検定
        return BBOX(section_one, index)
    elif code == 8000:  # CT断面の検定
        return BCT(section_one, index)
    elif code == 9000:  # L形鋼断面の検定
        return BL(section_one, index)
    elif code == 11000:  # テーパーH断面の検定
        return BHT(section_one, index)  # noqa: F821  BHT.mは元ソースに存在しない (MATLAB同様、未定義エラーになる)
    elif code == 18000:  # 二丁溝形鋼(2C)断面の検定 (mgtkit拡張)
        return BC2(section_one, index)
    elif code == 19000:  # 二丁山形鋼(2L)断面の検定 (mgtkit拡張)
        return BL2(section_one, index)
    # 該当なしの場合 MATLAB では pick_ability 未割当エラー
    raise RuntimeError('section_ability: 断面種別コード不明 (MATLAB: pick_ability未割当エラー)')
