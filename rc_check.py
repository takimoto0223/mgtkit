# -*- coding: utf-8 -*-
"""RC断面検定 (MATLAB原典からの逐語移植).

元コード (msrc/):
  privatetool_function/MIDAS/ratio/RC/RC_ratio_analysis.m
      RC梁(中実角)・RC壁柱(中実角, walldesign_index=1/2)の検定分岐と
      ローカルサブ関数 sub_RC4beam_ALWM (同ファイル1399行以降)
  privatetool_function/MIDAS/mgt/mgtopen_RCbeam.m (mgt.pyに追加)
  structural_function/RC/RC_section_analysis/beam/SA_RCbeamQratio.m
  structural_function/RC/RC_section_analysis/beam/SA_RCbeamratio_text.m
  structural_function/RC/RC_section_analysis/wall/SA_RW4_HMD.m
  structural_function/RC/RC_section_analysis/wall/SA_RW4Qratio.m
  structural_function/RC/RC_section_analysis/wall/SA_RW4_HMD_text.m
  structural_function/RC/ALST_etc/ALST/ALST_RC_AIJ.m
  structural_function/RC/ALST_etc/ALST/ALST_steelbar_KJ.m
  structural_function/RC/ALST_etc/ALST/E_RC_AIJ.m
  structural_function/RC/ALST_etc/steeelbar/Area_steelbar.m
  structural_function/RC/ALST_etc/steeelbar/outf_steelbar_JIS.m
  structural_function/RC/ALST_etc/steeelbar/bar_table.mat (data/bar_table.json)
  privatetool_function/MIDAS/ratio/RC/RCW_input.m (rcw_input_row: ダイアログ→引数化)

スコープ: NKD物件(RC壁式)で通る経路のみ。
  ・RC梁: 中実角断面(sectionsize末尾-1==2000) + *REBAR-BEAM配筋
  ・RC壁柱: 中実角断面 walldesign_index=1(耐力壁付ラーメン)/2(壁式)
  未移植分岐 (杭・接合部等) は NotImplementedError。

単位系: RC_ratio_analysis 入口で sectionsize×1000 (m→mm)。応力はkN,kNm。
インデックス規約: util.find_index は 0-based/-1 (MATLABは1-based/0)。
"""

import json
import math
import os

import numpy as np

from .util import find_index
from .s_check import _num2str


# ===========================================================================
# bar_table.mat (data/bar_table.json)
# ===========================================================================

_BAR_TABLE = None


# 検定中の断面ラベル (規定チェックNGの注記に対象断面を明記するための文脈。
# RC_ratio_analysis の入口で設定する。原典displayへの付加情報で計算は不変)
_WARN_CTX = {'label': ''}

# ユーザー承認による原典(MATLAB)バグ修正 2026-07-11:
# RC角柱のNM検定は原典の if section_no==4444 || 5555 が恒真(||の右がリテラル)
# のため常によせ筋版 SA_RC4_HMD_yose(2段目主筋を端から76.5mmに寄せる、
# 箱根物件対応の残置) が使われていた。通常の等間隔配筋 SA_RC4_HMD を使う。
# せい方向4段以上の面で差が出るため、適用検知時は注記を出す。
RC4_YOSE_FIX = {'hit': False}

# ユーザー承認による原典(MATLAB)バグ修正 2026-07-12:
# RC丸柱の弱軸NM検定で、max(曲げ項, 軸力項) を直後に曲げ項のみで上書きし
# 軸力項が無視される原典バグ(RC_ratio_analysis.m 1062行、非安全側)を修正。
# 強軸と同様に max を採用する。修正が結果に効いた場合は注記を出す。
RCSR_NMZ_FIX = {'hit': False}


def _warn(msg):
    """規定チェック等の注記print (対象断面の符号・番号を付記)."""
    if _WARN_CTX['label']:
        print(msg + '　[' + _WARN_CTX['label'] + ']')
    else:
        print(msg)


def _bar_table():
    """bar_table.mat 相当のテーブルを返す.

    返り値: (diameters(np.ndarray), areas(list of list))
    diameters = bar_table{1,1} (14径: 6,10,13,16,19,22,25,29,32,35,38,41,51,9)
    areas[i][k] = bar_table{i+1,2}(k+1) = 径diameters[i]の (k+1)本分の断面積表
    """
    global _BAR_TABLE
    if _BAR_TABLE is None:
        path = os.path.join(os.path.dirname(__file__), 'data', 'bar_table.json')
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        _BAR_TABLE = (np.asarray(d['diameters'], dtype=float), d['areas'])
    return _BAR_TABLE


def bar_table_next_diameter(di):
    """bar_table{1,1}(find_index(bar_table{1,1},di)+1,1) 相当 (一つ太い径).

    RCW_input.m / RC_ratio_analysis.m の端部補強筋径の決め方。
    MATLAB find_indexは1-based。Python側 find_index(0-based)+1 で同じ要素。
    """
    diameters, _ = _bar_table()
    idx = find_index(diameters, di)  # 0-based
    return float(diameters[int(idx) + 1])


def Area_steelbar(diameter, number):
    """Area_steelbar.m の逐語移植 (bar_tableはモジュール内で読み込み).

    diameter: スカラまたは配列。number: 本数 (スカラ)。
    MATLAB同様 number>10 は10本分を積み増して残数で参照。
    di_index==0(見つからない)のときMATLABはERROR文字列を作るだけで続行し
    bar_table{0,...}参照で実行時エラーになるため、ここではValueError。
    """
    diameters, areas = _bar_table()
    diameter = np.atleast_2d(np.asarray(diameter, dtype=float))
    Ast = np.zeros(diameter.shape)
    ii, jj = diameter.shape
    for i in range(ii):
        for j in range(jj):
            di_index = find_index(diameters, diameter[i, j])  # 0-based, -1=なし
            if di_index == -1:
                raise ValueError('鉄筋径エラー (Area_steelbar): D%g' % diameter[i, j])
            num = number
            while num > 10:
                Ast[i, j] = Ast[i, j] + areas[int(di_index)][9]
                num = num - 10
            Ast[i, j] = Ast[i, j] + areas[int(di_index)][int(num) - 1]
    if Ast.size == 1:
        return float(Ast[0, 0])
    return Ast


def outf_steelbar_JIS(diameter):
    """outf_steelbar_JIS.m の逐語移植 (異形鉄筋の最外径)."""
    diameter = np.atleast_2d(np.asarray(diameter, dtype=float))
    out_d = np.zeros(diameter.shape)
    ii, jj = diameter.shape
    for i in range(ii):
        for j in range(jj):
            d = diameter[i, j]
            if (d - 10) * (d - 13) == 0:
                out_d[i, j] = d + 1
            elif (d - 16) * (d - 19) == 0:
                out_d[i, j] = d + 2
            elif (d - 22) * (d - 25) == 0:
                out_d[i, j] = d + 3
            elif (d - 29) * (d - 32) == 0:
                out_d[i, j] = d + 4
            elif (d - 35) * (d - 38) == 0:
                out_d[i, j] = d + 5
            elif (d - 41) * (d - 51) == 0:
                out_d[i, j] = d + 6
            else:
                raise ValueError('鉄筋情報エラー (outf_steelbar_JIS): D%g' % d)
    if out_d.size == 1:
        return float(out_d[0, 0])
    return out_d


# ===========================================================================
# 許容応力度 (ALST_etc/ALST)
# ===========================================================================

def ALST_RC_AIJ(Fc):
    """ALST_RC_AIJ.m の逐語移植.

    Fc=[Fc値, 種別(0普通/1軽量1種/2軽量2種)] またはスカラ。
    返り値 2x5: [圧縮 引張 せん断 付着(上端) 付着(その他)] 1行目長期/2行目短期
    """
    Fc = np.asarray(Fc, dtype=float).ravel()
    if Fc.size == 1:
        co_shear = 1.0
    elif Fc[1] == 0:
        co_shear = 1.0
    elif Fc[1] == 1:
        co_shear = 0.9
    elif Fc[1] == 2:
        co_shear = 0.9
    else:
        raise ValueError('材料定義ミス (ALST_RC_AIJ)')

    ALstressRC = np.zeros((2, 5))
    # 圧縮
    ALstressRC[0, 0] = Fc[0] / 3
    ALstressRC[1, 0] = Fc[0] * 2 / 3
    # 引張（引っ張りは0と定義する．）
    # せん断
    ALstressRC[0, 2] = min(Fc[0] / 30, 0.5 + Fc[0] / 100) * co_shear
    ALstressRC[1, 2] = ALstressRC[0, 2] * 1.5
    # 付着
    ALstressRC[0, 3] = min(Fc[0] / 15, 0.9 + 2 * Fc[0] / 75)
    ALstressRC[0, 4] = min(Fc[0] / 10, 1.35 + Fc[0] / 25)
    ALstressRC[1, 3:5] = ALstressRC[0, 3:5] * 1.5
    return ALstressRC


def ALST_steelbar_KJ(SD):
    """ALST_steelbar_KJ.m の逐語移植.

    SD=[径, 鋼種(295/345/390/1275ウルボン)]。
    返り値 2x2: [圧縮/引張, せん断補強] 1行目長期/2行目短期
    """
    SD = np.asarray(SD, dtype=float).ravel()
    kind = SD[1]
    di = SD[0]
    if di >= 19 and kind == 295:
        raise ValueError('鉄筋情報エラー(径19以上にSD295利用)')
    if kind == 1275:  # ウルボン
        return np.array([[195.0, 195.0], [585.0, 585.0]])
    if kind == 295:
        return np.array([[196.0, 195.0], [295.0, 295.0]])  # SD295A/B
    elif di < 29 and kind == 345:
        return np.array([[215.0, 195.0], [345.0, 345.0]])  # SD345
    elif di >= 29 and kind == 345:
        return np.array([[195.0, 195.0], [345.0, 345.0]])  # SD345 D29以上
    elif di < 29 and kind == 390:
        return np.array([[215.0, 195.0], [390.0, 390.0]])  # SD390
    elif di >= 29 and kind == 390:
        return np.array([[195.0, 195.0], [390.0, 390.0]])  # SD390 D29以上
    elif di >= 29 and kind == 490:
        return np.array([[195.0, 195.0], [490.0, 490.0]])  # SD490 D29以上
    raise ValueError('鉄筋情報エラー (ALST_steelbar_KJ)')


def E_RC_AIJ(Fc):
    """E_RC_AIJ.m の逐語移植.

    返り値 [ヤング係数(N/mm2), ヤング係数比n, 単位体積重量γ(kN/m3)]
    MATLAB版はFcオーバー時にERROR文字列表示のみで続行するため同様に続行。
    """
    Fc = np.asarray(Fc, dtype=float).ravel()
    E_RC = np.zeros(3)
    if Fc.size == 1 or Fc[1] == 0:
        if Fc[0] <= 36:
            E_RC[2] = 24
        elif Fc[0] <= 48:
            E_RC[2] = 24.5
        elif Fc[0] <= 60:
            E_RC[2] = 25
        else:
            E_RC[2] = 25  # 普通コンFc60オーバー (MATLAB: 表示のみで続行)
    elif Fc[1] == 1:
        if Fc[0] <= 27:
            E_RC[2] = 20
        elif Fc[0] <= 36:
            E_RC[2] = 22
        else:
            E_RC[2] = 22  # 軽1コンFc36オーバー
    elif Fc[1] == 2:
        if Fc[0] <= 27:
            E_RC[2] = 18
        else:
            E_RC[2] = 18  # 軽2コンFc27オーバー
    else:
        raise ValueError('材料定義ミス (E_RC_AIJ)')

    E_RC[0] = 3.35 * 10 ** 4 * (E_RC[2] - 1) ** 2 / 24 ** 2 * (Fc[0] / 60) ** (1.0 / 3)

    if Fc[0] <= 27:
        E_RC[1] = 15
    elif Fc[0] <= 36:
        E_RC[1] = 13
    elif Fc[0] <= 48:
        E_RC[1] = 11
    elif Fc[0] <= 60:
        E_RC[1] = 9
    else:
        E_RC[1] = 9
    return E_RC


# ===========================================================================
# 壁柱の断面解析 (SA_RW4_HMD.m / SA_RW4Qratio.m)
# ===========================================================================

def SA_RW4_HMD(Nd, Form, steelbar, HOOP, Fc, timecase):
    """msrc/structural_function/RC/RC_section_analysis/wall/SA_RW4_HMD.m の逐語移植.

    戻り値: (M_AL, maxN)  maxNは長さ2のndarray (MATLABの maxN(1),maxN(2))
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Nd = float(Nd)

    def _mcolon(a, d, b):
        # NOTE: MATLABのコロン演算子 a:d:b の再現（終端の浮動小数点誤差を許容して要素数を決定）
        if d == 0:
            return np.zeros(0)
        nn = (b - a) / d
        if nn < 0:
            return np.zeros(0)
        m = int(math.floor(nn + 1e-10 * max(1.0, abs(nn))))
        return a + d * np.arange(m + 1)

    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない（後続のtimecase2参照で実行時エラーになる）

    # RC断面の外形情報
    t = Form[0]; D = Form[1]
    # NOTE: load bar_table.mat はヘルパー関数(Area_steelbar等)が内部保持するため不要

    if steelbar[0] == 1:  # 端部補強筋なしの場合
        di_main = steelbar[2]; SD_main = steelbar[5]
        a = Area_steelbar(di_main, 1)
        ai = steelbar[4] * a

        num = steelbar[1]
        nv = steelbar[1] / steelbar[4]

        rfc = ALST_steelbar_KJ([di_main, SD_main])
        f_c = ALST_RC_AIJ(Fc)

        n = E_RC_AIJ(Fc)
        n = n[1]
        fc = f_c[timecase2 - 1, 0]
        ft = rfc[timecase2 - 1, 0]

        tol = 0.01  # 許容誤差
        ndiv = 200  # コンクリート部分の分割数
        dy = D / ndiv

        di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]
        dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5

        bar_space = max(2.5 * di_main, di_main + 25 * 1.25)
        if nv > 3:
            by = np.concatenate(([dc],
                                 _mcolon(dc + bar_space,
                                         (D - 2 * dc - 2 * bar_space) / (nv - 3),
                                         D - dc - bar_space),
                                 [D - dc]))
        elif nv == 3:
            by = np.array([dc, D / 2, D - dc])
        elif nv == 2:
            by = np.array([dc, D - dc])
        elif nv == 1:
            nv = 2
            by = np.array([0.0, D])
            # display('壁長さ短すぎ要チェック')
            # stop
        cy = _mcolon(dy / 2, dy, D - dy / 2)

        xnlmax = 10
        xn = -1 * xnlmax * D
        xn0 = 2 * xn
        xndiv = D

        Nd = Nd * 1000

        maxN = np.zeros(2)
        maxN[0] = min((Form[0] * Form[1] + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                      ((Form[0] * Form[1] - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
        maxN[1] = num * a * rfc[timecase2 - 1, 0]

        while True:
            bfmax = 0; bf = by - xn

            bfmax = np.max(np.abs(bf))
            if bf.size > 0 and np.all(bf == 0):  # NOTE: MATLABの if bf==0 はベクトル全要素が0のとき真
                bf = np.inf
            else:
                bf = bf / bfmax
            cfmax = 0; cf = np.minimum(cy - xn, 0)
            cfmax = np.max(np.abs(cf))

            if cfmax == 0:
                cf = 0
            else:
                cf = cf / cfmax

            if (cfmax * ft / n) > bfmax * fc:
                bf = bf * fc * n; cf = cf * fc
            else:
                bf = bf * ft; cf = cf * ft / n

            Ny = np.sum(bf * ai)
            M_AL = np.sum(bf * ai * (by - D / 2))

            Ny = Ny + np.sum(cf * dy * t)
            M_AL = M_AL + np.sum(cf * dy * t * (cy - D / 2))

            Ny = Ny * -1

            s = abs(Ny - Nd) / t / D
            if s < tol:
                break

            if Ny > Nd:
                xndiv = 0.5 * xndiv
                if xndiv < 2 * D / ndiv:
                    break
                xn = xn0 + xndiv

            else:
                xn0 = xn
                xn = xn + xndiv
                if xn > xnlmax * D:
                    break

    # NOTE: 原典118-232行目 (elseif steelbar(1)==2 「端部補強筋のみor縦筋のみ」) は
    #       MATLAB原典でも全行コメントアウトされているため移植対象外

    elif steelbar[0] >= 10:  # 壁式面内曲げ, steelbar(1)10:端部のみ,20:縦筋のみ,30:端部+縦筋

        wall_cover = HOOP[5]

        num_v = steelbar[1] - steelbar[7]  # 241107國江_端部補強筋を抜いた本数に変更,縦筋本数
        di_main = steelbar[2]; SD_main = steelbar[5]
        if steelbar[0] == 10:
            nv_v = 0
            a_v = 0
        else:
            nv_v = steelbar[3] - steelbar[7] / steelbar[4]  # 241107國江_端部補強筋を抜いた本数に変更,縦筋列数
            a_v = Area_steelbar(di_main, 1)

        di_sp = steelbar[6]
        n_sp = steelbar[7]  # 端部補強筋数
        nv_sp = steelbar[7] / steelbar[4]  # 端部補強筋列数
        pitch_sp = steelbar[8]; SD_sp = steelbar[9]
        a_sp = Area_steelbar(di_sp, 1)

        # 許容応力度
        if steelbar[0] == 10:
            rfc = ALST_steelbar_KJ([di_sp, SD_sp])  # 「10:端部のみ」の場合は、材料は端部に揃える
        else:
            rfc = ALST_steelbar_KJ([di_main, SD_main])  # 「20:縦筋のみ」「30:端部+縦筋」の場合は、材料は縦筋に揃える
        f_c = ALST_RC_AIJ(Fc)
        n = E_RC_AIJ(Fc)
        n = n[1]
        fc = f_c[timecase2 - 1, 0]
        ft = rfc[timecase2 - 1, 0]

        tol = 0.01  # 許容誤差
        ndiv = 200  # コンクリート部分の分割数
        dy = D / ndiv

        # NOTE: MATLABの zeros(1,nv_v+nv_sp)。サイズは整数値のはず（非整数ならMATLABはエラー）
        ai = np.zeros(int(round(nv_v + nv_sp))) + steelbar[4] * a_v
        if nv_v > 0:
            if nv_sp >= 4:
                by = np.concatenate((
                    _mcolon(wall_cover, pitch_sp,
                            wall_cover + (nv_sp / 2 - 2) * pitch_sp),
                    _mcolon(wall_cover + (nv_sp / 2 - 1) * pitch_sp,
                            (D - 2 * wall_cover - 2 * (nv_sp / 2 - 1) * pitch_sp) / (nv_v + 1),
                            D - wall_cover - (nv_sp / 2 - 1) * pitch_sp),
                    _mcolon(D - wall_cover - (nv_sp / 2 - 2) * pitch_sp, pitch_sp,
                            D - wall_cover)))
            else:
                by = _mcolon(wall_cover + (nv_sp / 2 - 1) * pitch_sp,
                             (D - 2 * wall_cover - 2 * (nv_sp / 2 - 1) * pitch_sp) / (nv_v + 1),
                             D - wall_cover - (nv_sp / 2 - 1) * pitch_sp)
            # ai(1:nv_sp/2)
            ai[0:int(round(nv_sp / 2))] = steelbar[4] * a_sp
            # ai(nv_v+nv_sp/2+1:nv_v+nv_sp)
            ai[int(round(nv_v + nv_sp / 2)):int(round(nv_v + nv_sp))] = steelbar[4] * a_sp
        elif nv_sp == 4:  # 端部補強筋のみ,かつ4列のとき
            by = np.array([70.0, 70 + pitch_sp, (D - 70 - pitch_sp), (D - 70)])
            ai = ai + steelbar[4] * a_sp
            # by = [wall_cover:(D-2*wall_cover)/(n_sp/2-1):D-wall_cover];
            # ai = ai+steelbar(5)*a_v;
        else:
            _warn('壁長さ短すぎ要チェック')
            raise ValueError('壁長さ短すぎ要チェック')  # NOTE: MATLABのstop（未定義関数呼び出しで実行時エラー）相当
        cy = _mcolon(dy / 2, dy, D - dy / 2)

        xnlmax = 10
        xn = -1 * xnlmax * D
        xn0 = 2 * xn
        xndiv = D

        Nd = Nd * 1000

        maxN = np.zeros(2)
        maxN[0] = min((Form[0] * Form[1] + (n - 1) * (num_v * a_v + n_sp * a_sp)) * f_c[timecase2 - 1, 0],
                      ((Form[0] * Form[1] - (num_v * a_v + n_sp * a_sp)) / n + (num_v * a_v + n_sp * a_sp)) * (rfc[timecase2 - 1, 0]))
        maxN[1] = (num_v * a_v + n_sp * a_sp) * rfc[timecase2 - 1, 0]

        while True:
            bfmax = 0; bf = by - xn

            bfmax = np.max(np.abs(bf))
            if bf.size > 0 and np.all(bf == 0):  # NOTE: MATLABの if bf==0 はベクトル全要素が0のとき真
                bf = np.inf
            else:
                bf = bf / bfmax
            cfmax = 0; cf = np.minimum(cy - xn, 0)
            cfmax = np.max(np.abs(cf))

            if cfmax == 0:
                cf = 0
            else:
                cf = cf / cfmax

            if (cfmax * ft / n) > bfmax * fc:
                bf = bf * fc * n; cf = cf * fc
            else:
                bf = bf * ft; cf = cf * ft / n

            Ny = np.sum(bf * ai)
            M_AL = np.sum(bf * ai * (by - D / 2))

            Ny = Ny + np.sum(cf * dy * t)
            M_AL = M_AL + np.sum(cf * dy * t * (cy - D / 2))

            Ny = Ny * -1

            s = abs(Ny - Nd) / t / D
            if s < tol:
                break

            if Ny > Nd:
                xndiv = 0.5 * xndiv
                if xndiv < 2 * D / ndiv:
                    break
                xn = xn0 + xndiv

            else:
                xn0 = xn
                xn = xn + xndiv
                if xn > xnlmax * D:
                    break

        # display(xn)

    # NOTE: 原典360-456行目 (elseif steelbar(1)==3 「端部補強筋あり<弱軸>」) は
    #       MATLAB原典でも全行コメントアウトされているため移植対象外

    else:
        pass  # NOTE: MATLAB原典も空のelse。この場合M_AL,maxNが未定義のまま→MATLAB同様に実行時エラー(UnboundLocalError)

    return M_AL, maxN


# %%%%%%%%%RC■断面柱の許容せん断力算定
def SA_RW4Qratio(Form, L, steelbar, HOOP, Fc, stress, timecase, QL, qup_wall, RCQ):
    """msrc/structural_function/RC/RC_section_analysis/wall/SA_RW4Qratio.m の逐語移植.

    戻り値: (ratio_Q, ALW_Q, Qs1)  ratio_Qは(n,2)のndarray（MATLABと同次元）
    """
    Form = np.atleast_2d(np.asarray(Form, dtype=float))
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.atleast_2d(np.asarray(QL, dtype=float))

    # RC断面の外形情報[mm]
    b = Form[:, 0]; D = Form[:, 1]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        # t_case ='短期'
    elif timecase == 1:
        timecase = 1
        # t_case ='長期'
    else:
        ERROR = '長短期設定ミス'
        raise ValueError(ERROR)  # NOTE: MATLABのstop（未定義関数呼び出しで実行時エラー）相当

    # 帯筋情報を読み込み
    # NOTE: load bar_table.mat はヘルパー関数(Area_steelbar等)が内部保持するため不要
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[4]
    cover_depth = HOOP[5]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, 2)  # Fyせん断補強筋本数

    # 断面情報（配筋断面積，許容応力度など）
    f_c = ALST_RC_AIJ(Fc)

    # その３：横筋の規定
    if di_support == 10 and pitch <= 300:
        pass
    elif di_support > 10 and pitch <= 300:
        pass
    else:
        _warn('帯筋間隔NG')
    # その４：せん断補強筋比の規定
    if aw / (np.max(D) * pitch) >= 0.25 / 100:
        pass
    else:
        _warn('壁のせん断補強筋比不足（0.25％未満）')

    nrow = b.shape[0]

    # 許容せん断力の算定（長期）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if timecase == 1:
        ALW_Q = np.zeros((nrow, 2))
        ratio_Q = np.zeros((nrow, 2))
        # Fz(参考)
        ALW_Q[:, 0] = b * D * f_c[0, 2] / 10 ** 3
        ratio_Q[:, 0] = np.abs(stress[:, 2]) / ALW_Q[:, 0]

        # Fy（面内）
        ALW_Q[:, 1] = b * D * f_c[0, 2] / 10 ** 3
        ratio_Q[:, 1] = np.abs(stress[:, 1]) / ALW_Q[:, 1]
        Qs1 = np.array([])

    if timecase == 2:
        # 許容せん断力（短期）
        ALW_Q1 = (b * D * f_c[1, 2] / 10 ** 3).reshape(-1, 1)
        ALW_Q2 = (aw / (D * pitch) * b * D * wft[1, 1] / 10 ** 3).reshape(-1, 1)
        # NOTE: MATLABの max([ALW_Q1 ALW_Q2],[],2) → 行ごとの最大（結果はn×1列ベクトル）
        ALW_Q = np.max(np.hstack((ALW_Q1, ALW_Q2)), axis=1).reshape(-1, 1)
        Qs1 = np.zeros((2, nrow))
        ratio_Q = np.zeros((nrow, 2))
        for iyz in range(1, 3):
            # せん断割増しから決まる設計用せん断力
            # MATLAB: Qs1(iyz,:) = qup_wall*(stress(:,4-iyz)-QL(:,3-iyz))'+(QL(:,3-iyz))'
            Qs1[iyz - 1, :] = qup_wall * (stress[:, 3 - iyz] - QL[:, 2 - iyz]) + QL[:, 2 - iyz]
            # MATLAB: ratio_Q(:,iyz) = abs(Qs1(iyz,:)')./ALW_Q  (ALW_Qはn×1列)
            ratio_Q[:, iyz - 1] = np.abs(Qs1[iyz - 1, :]) / ALW_Q[:, 0]

    return ratio_Q, ALW_Q, Qs1


# ===========================================================================
# RC梁の断面解析 (RC_ratio_analysis.m ローカルsub_RC4beam_ALWM / SA_RCbeamQratio.m)
# ===========================================================================

def sub_RC4beam_ALWM(Form, steelbar_u, steelbar_d, HOOP, Fc, timecase, L43):
    """RC梁の許容曲げモーメント算定（逐語移植）

    元ファイル: privatetool_function/MIDAS/ratio/RC/RC_ratio_analysis.m
    行範囲: 1399-1695 (ローカルサブ関数 sub_RC4beam_ALWM)
    ※別ファイル privatetool_function/MIDAS/sub_RC4beam_ALWM.m は旧6/7列書式で
      内容が異なる。MATLABはローカル関数を優先するため本ローカル版を移植。

    返り値: (ALWM, up)
      ALWM: 3x2 ndarray（i端/中央/j端 × 正曲げ/負曲げ、負曲げは負値）[kNm]
      up:   引張鉄筋断面積不足時の割増係数（pt<0.4%かつ長期のときL43、それ以外1.0）
    """
    # 建築学会RC基準1999等/RC梁断面算定
    # 2009/03/30ソース作成
    # エクセルファイルとの対応確認済
    #
    # ひび割れモーメントに対する検討を追加すること．
    #
    # Form：[はり幅b,はりせいD]
    # L:梁のスパン
    # steelbar_u = [段数，一段目本数，二段目本数，三段目本数，一段目径D，二段目径D，三段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    # steelbar_d = [段数，一段目本数，二段目本数，三段目本数，一段目径D，二段目径D，三段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    # HOOP=[径D，pitch, n, SD, cover_depth] Fc=[21 1]   %[Fc 普通(0)軽量1種(1)軽量2種(2)を示す]
    # stress = [軸力，強軸方向せん断，弱軸方向せん断，強軸まわり曲げモーメント，弱軸まわり曲げモーメント]これをi端，中央，j端の三行
    # timecase：1（長期) 10以上で短期

    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()

    # %%長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    elif timecase == 9:
        timecase = 1
    else:
        ERROR = '長短期設定ミス'  # MATLAB原典は stop なし（表示のみで続行）
        print('ERROR =', ERROR)

    # RC断面の外形情報
    b = Form[0]; D = Form[1]

    # 配筋情報
    # 配筋情報の入力されたmatファイル呼び出し
    # load bar_table.mat → ヘルパー関数で代替
    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[3]; cover_depth = HOOP[4]

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    SD_main = np.zeros((2, 3))
    num[0, :] = np.sum(steelbar_u[:, 1:4], axis=1)
    di_main[0, :] = steelbar_u[:, 4]
    SD_main[0, :] = steelbar_u[:, 7]

    num[1, :] = np.sum(steelbar_d[:, 1:4], axis=1)
    di_main[1, :] = steelbar_d[:, 4]
    SD_main[1, :] = steelbar_d[:, 7]

    # 梁の上端筋と下端筋の段数から重心距離の計算

    dd = np.zeros((2, 3)) + 1000  # 二段配筋の際には1000が書き換えられる．
    d_out = np.zeros((2, 3))
    d = np.zeros((2, 3))

    # 上端筋
    for j in range(3):
        d_out[0, j] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[0, j]) * 0.5
        if steelbar_u[j, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[0, j] = d_out[0, j]
            if steelbar_u[j, 2] == 0:
                pass
            else:
                ERROR = '配筋情報ミス（配筋段数）'
                raise ValueError(ERROR)  # MATLAB: stop

        elif steelbar_u[j, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔
            if steelbar_u.shape[1] == 9:
                dd[0, j] = steelbar_u[j, 8]
                print('dd =\n', dd)  # MATLAB原典はセミコロン無し（表示のみ）
            elif steelbar_u.shape[1] == 8:
                dd[0, j] = math.ceil(max(di_main[0, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, j])
            d[0, j] = d_out[0, j] * (steelbar_u[j, 1]) / num[0, j] + (d_out[0, j] + dd[0, j]) * (steelbar_u[j, 2]) / num[0, j]

        elif steelbar_u[j, 0] == 3:
            # 主筋の重心距離（三段配筋）

            # 主筋間隔
            if steelbar_u.shape[1] == 9:
                dd[0, j] = steelbar_u[j, 8]
                print('dd =\n', dd)  # MATLAB原典はセミコロン無し（表示のみ）
            elif steelbar_u.shape[1] == 8:
                dd[0, j] = math.ceil(max(di_main[0, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, j])
            d[0, j] = d_out[0, j] * (steelbar_u[j, 1]) / num[0, j] + (d_out[0, j] + dd[0, j]) * (steelbar_u[j, 2]) / num[0, j] + (d_out[0, j] + 2 * dd[0, j]) * (steelbar_u[j, 3]) / num[0, j]

        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    # 下端筋
    for j in range(3):
        d_out[1, j] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[1, j]) * 0.5
        if steelbar_d[j, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[1, j] = d_out[1, j]
            if steelbar_d[j, 2] == 0:
                pass
            else:
                ERROR = '配筋情報ミス（配筋段数）'
                raise ValueError(ERROR)  # MATLAB: stop

        elif steelbar_d[j, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔
            if steelbar_d.shape[1] == 9:
                dd[1, j] = steelbar_d[j, 8]
            elif steelbar_d.shape[1] == 8:
                dd[1, j] = math.ceil(max(di_main[1, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, j])
            d[1, j] = d_out[1, j] * (steelbar_d[j, 1]) / num[1, j] + (d_out[1, j] + dd[1, j]) * (steelbar_d[j, 2]) / num[1, j]

        elif steelbar_d[j, 0] == 3:
            # 主筋の重心距離（３段配筋）

            # 主筋間隔
            if steelbar_d.shape[1] == 9:
                dd[1, j] = steelbar_d[j, 8]
            elif steelbar_d.shape[1] == 8:
                dd[1, j] = math.ceil(max(di_main[1, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, j])
            d[1, j] = d_out[1, j] * (steelbar_d[j, 1]) / num[1, j] + (d_out[1, j] + dd[1, j]) * (steelbar_d[j, 2]) / num[1, j] + (d_out[1, j] + 2 * dd[1, j]) * (steelbar_d[j, 3]) / num[1, j]

        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    a = Area_steelbar(di_main, 1)  # MATLAB: Area_steelbar(bar_table,di_main,1)
    f_c = ALST_RC_AIJ(Fc)

    rfc = [[None, None, None], [None, None, None]]
    for ii in range(2):
        for jj in range(3):
            rfc[ii][jj] = ALST_steelbar_KJ([di_main[ii, jj], SD_main[ii, jj]])

    # ヤング係数比(RC基準で設定された断面解析用のヤング係数比）
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    max_u = np.max(steelbar_u[:, 1:4], axis=1)  # max(steelbar_u(:,2:4),[],2)'
    max_d = np.max(steelbar_d[:, 1:4], axis=1)  # max(steelbar_d(:,2:4),[],2)'
    if np.all(np.maximum(max_u, max_d) == 1):
        dd2 = b / 2
    else:
        dd2 = (b - 2 * d_out) / (np.vstack([max_u, max_d]) - 1)
    check_dd = dd - outf_steelbar_JIS(di_main)
    check_dd2 = dd2 - outf_steelbar_JIS(di_main)

    checkdd = np.minimum(check_dd, check_dd2)
    min_dd = np.maximum(1.5 * di_main, 25 * 1.25)

    judge_dd = checkdd - min_dd

    if np.min(judge_dd) >= 0:
        pass
    else:
        # display('鉄筋あき不足(梁せいおよび梁幅方向の検討）')
        # display(['梁せい方向あき：' num2str(min(min(check_dd)),'%15.2f') 'mm'])
        # display(['梁幅方向あき：' num2str(min(min(check_dd2)),'%15.2f') 'mm'])
        # display(['あきの最小値：' num2str(min(min(min_dd)),'%15.2f') 'mm'])
        # stop
        pass

    # その１：主筋の規定
    if np.min(di_main) >= 13:
        pass
    else:
        ERROR = '主筋規定外(D13以上，配筋段数については既に検討済)'
        raise ValueError(ERROR)  # MATLAB: stop

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b * D)
    pt = np.max(pt)
    if pt < 0.4 / 100 and timecase < 2:
        up = L43
    else:
        up = 1.0
        # ERROR='引張鉄筋断面積不足（0.4％未満）'
        # stop

    # その３：HOOP間隔の規定
    if di_support <= 10 and pitch <= max(D / 2, 250):
        pass
    elif di_support > 10 and pitch <= max(D / 2, 450):
        pass
    else:
        _warn('STRP筋間隔NG→STRPピッチ%.0fmm，　STRP径：D%.0f，　梁せい：%.0fmm' % (pitch, di_support, D))
        # stop

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, HOOP[2])  # MATLAB: Area_steelbar(bar_table,di_support,HOOP(3))

    if aw / (b * pitch) >= 0.2 / 100:
        pass
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw=%.2f%%→STRPピッチ%.0fmm，　STRP径：D%.0f，　梁幅b：%.0fmm'
              % (aw / (b * pitch) * 100, pitch, di_support, b))
        # stop

    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        ERROR = '鉄筋かぶり厚再検討（40mm未満）'
        raise ValueError(ERROR)  # MATLAB: stop

    # 複筋長方形梁の中立軸算定（その１・正曲げ）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    dc = d[0, :]
    dt = D - d[1, :]
    ac = a[0, :] * num[0, :]
    at = a[1, :] * num[1, :]

    xn1 = np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2 + 2 / b * ((n - 1) * ac * dc + n * at * dt)) - ((n - 1) * ac + n * at) / b

    M1 = np.zeros((2, 3))
    M2 = np.zeros((2, 3))
    M3 = np.zeros((2, 3))
    rfc_t = np.zeros(3)
    rfc_c = np.zeros(3)

    # 許容曲げモーメントの算出
    # １：圧縮コンクリートの圧壊

    sig_c = f_c[timecase - 1, 0]

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M1[0, :] = Cs * (dt - dc) + Cc * (dt - xn1 / 3)

    # ２：引張鉄筋の降伏
    rfc_t[0] = rfc[1][0][timecase - 1, 0]
    rfc_t[1] = rfc[1][1][timecase - 1, 0]
    rfc_t[2] = rfc[1][2][timecase - 1, 0]
    sig_c = rfc_t * xn1 / (D - d_out[1, :] - xn1) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M2[0, :] = Cs * (dt - dc) + Cc * (dt - xn1 / 3)

    # ３：圧縮鉄筋の降伏
    rfc_c[0] = rfc[0][0][timecase - 1, 0]
    rfc_c[1] = rfc[0][1][timecase - 1, 0]
    rfc_c[2] = rfc[0][2][timecase - 1, 0]

    sig_c = rfc_c * xn1 / (xn1 - d_out[0, :]) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M3[0, :] = Cs * (dt - dc) + Cc * (dt - xn1 / 3)

    # 複筋長方形梁の中立軸算定（その２・負曲げ）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    dc = d[1, :]
    dt = D - d[0, :]

    ac = a[1, :] * num[1, :]
    at = a[0, :] * num[0, :]

    xn2 = D - np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2 + 2 / b * ((n - 1) * ac * dc + n * at * dt)) + ((n - 1) * ac + n * at) / b

    # 許容曲げモーメントの算出
    # １：圧縮コンクリートの圧壊
    sig_c = f_c[timecase - 1, 0]

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M1[1, :] = Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3)

    # ２：引張鉄筋の降伏
    rfc_t[0] = rfc[0][0][timecase - 1, 0]
    rfc_t[1] = rfc[0][1][timecase - 1, 0]
    rfc_t[2] = rfc[0][2][timecase - 1, 0]
    sig_c = rfc_t * (D - xn2) / (xn2 - d_out[0, :]) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M2[1, :] = Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3)

    # ３：圧縮鉄筋の降伏
    rfc_c[0] = rfc[1][0][timecase - 1, 0]
    rfc_c[1] = rfc[1][1][timecase - 1, 0]
    rfc_c[2] = rfc[1][2][timecase - 1, 0]
    sig_c = rfc_c * (D - xn2) / ((D - d_out[1, :]) - xn2) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M3[1, :] = Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3)

    # 許容曲げモーメントの算出%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    M = np.minimum(M1, M3)
    M = np.minimum(M, M2)
    ALWM = M / 10 ** 6
    ALWM[1, :] = ALWM[1, :] * -1
    ALWM = ALWM.T

    return ALWM, up


def SA_RCbeamQratio(Form, L, steelbar_u, steelbar_d, HOOP, Fc, stress, timecase, QL, qup_beam, RCQ):
    """RC梁の許容せん断力算定（逐語移植）

    元ファイル: structural_function/RC/RC_section_analysis/beam/SA_RCbeamQratio.m
    行範囲: 1-184（ファイル全体）
    MATLAB原典の返り値どおり (ratio_Q, ALW_Q, alph, j, Mmax, Qmax) を返す。
    本移植では仕様により (ratio_Q, ALW_Q) のみを返す。

    qup_beam が空（None または空配列）の場合：MATLAB原典には空の分岐は存在せず
    Qs1 の算定行（[]との積）でエラーとなる。本移植では Qs1 を使わない経路
    （RCQ が 1,3 以外 → Qs=Qs2）では続行し，Qs1 が必要な経路では ValueError。

    返り値: (ratio_Q, ALW_Q) いずれも長さ3のndarray（i端/中央/j端）
    """
    # %%%%%%%%%RC梁の許容せん断力算定

    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.asarray(stress, dtype=float)
    QL = np.asarray(QL, dtype=float).ravel()  # 列ベクトル → 1次元化（QL'相当の読み替え）
    if qup_beam is None:
        qup_empty = True
    else:
        qup_arr = np.asarray(qup_beam, dtype=float)
        qup_empty = (qup_arr.size == 0)
        if not qup_empty:
            qup_beam = float(qup_arr.ravel()[0]) if qup_arr.size == 1 else qup_arr

    # RC断面の外形情報[mm単位で入力]
    b = Form[0]; D = Form[1]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        # t_case ='短期';
    elif timecase == 1:
        timecase = 1
        # t_case ='長期';
    elif timecase == 9:
        timecase = 1
        # t_case ='長期';
    else:
        ERROR = '長短期設定ミス'
        raise ValueError(ERROR)  # MATLAB: stop

    # 帯筋情報を読み込み
    # load bar_table.mat → ヘルパー関数で代替
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[3]; cover_depth = HOOP[4]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, HOOP[2])  # MATLAB: Area_steelbar(bar_table,di_support,HOOP(3))

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    SD_main = np.zeros((2, 3))
    num[0, :] = np.sum(steelbar_u[:, 1:4], axis=1)
    di_main[0, :] = steelbar_u[:, 4]
    SD_main[0, :] = steelbar_u[:, 7]

    num[1, :] = np.sum(steelbar_d[:, 1:4], axis=1)
    di_main[1, :] = steelbar_d[:, 4]
    SD_main[1, :] = steelbar_d[:, 7]
    # 梁の上端筋と下端筋の段数から重心距離の計算

    dd = np.zeros((2, 3)) + 1000  # 二段配筋の際には1000が書き換えられる．
    d_out = np.zeros((2, 3))
    d = np.zeros((2, 3))

    # 上端筋
    for i in range(3):
        d_out[0, i] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[0, i]) * 0.5
        if steelbar_u[i, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[0, i] = d_out[0, i]
            if steelbar_u[i, 2] == 0:
                pass
            else:
                ERROR = '配筋情報ミス（配筋段数）'
                raise ValueError(ERROR)  # MATLAB: stop

        elif steelbar_u[i, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔
            if steelbar_u.shape[1] == 9:
                dd[0, i] = steelbar_u[i, 8]
            elif steelbar_u.shape[1] == 8:
                dd[0, i] = math.ceil(max(di_main[0, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, i])
            d[0, i] = d_out[0, i] * (steelbar_u[i, 1]) / num[0, i] + (d_out[0, i] + dd[0, i]) * (steelbar_u[i, 2]) / num[0, i]

        elif steelbar_u[i, 0] == 3:
            # 主筋の重心距離（3段配筋）

            # 主筋間隔
            if steelbar_u.shape[1] == 9:
                dd[0, i] = steelbar_u[i, 8]
            elif steelbar_u.shape[1] == 8:
                dd[0, i] = math.ceil(max(di_main[0, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, i])
            d[0, i] = d_out[0, i] * (steelbar_u[i, 1]) / num[0, i] + (d_out[0, i] + dd[0, i]) * (steelbar_u[i, 2]) / num[0, i] + (d_out[0, i] + 2 * dd[0, i]) * (steelbar_u[i, 3]) / num[0, i]

        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    # 下端筋
    for i in range(3):
        d_out[1, i] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[1, i]) * 0.5
        if steelbar_d[i, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[1, i] = d_out[1, i]
            if steelbar_d[i, 2] == 0:
                pass
            else:
                ERROR = '配筋情報ミス（配筋段数）'
                raise ValueError(ERROR)  # MATLAB: stop

        elif steelbar_d[i, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔
            if steelbar_d.shape[1] == 9:
                dd[1, i] = steelbar_d[i, 8]
            elif steelbar_d.shape[1] == 8:
                dd[1, i] = math.ceil(max(di_main[1, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, i])
            d[1, i] = d_out[1, i] * (steelbar_d[i, 1]) / num[1, i] + (d_out[1, i] + dd[1, i]) * (steelbar_d[i, 2]) / num[1, i]

        elif steelbar_d[i, 0] == 3:
            # 主筋の重心距離（3段配筋）

            # 主筋間隔
            if steelbar_d.shape[1] == 9:
                dd[1, i] = steelbar_d[i, 8]
            elif steelbar_d.shape[1] == 8:
                dd[1, i] = math.ceil(max(di_main[1, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, i])
            d[1, i] = d_out[1, i] * (steelbar_d[i, 1]) / num[1, i] + (d_out[1, i] + dd[1, i]) * (steelbar_d[i, 2]) / num[1, i] + (d_out[1, i] + 2 * dd[1, i]) * (steelbar_d[i, 3]) / num[1, i]

        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    a = Area_steelbar(di_main, 1)  # MATLAB: Area_steelbar(bar_table,di_main,1)
    f_c = ALST_RC_AIJ(Fc)
    rfc = [[None, None, None], [None, None, None]]
    for i in range(2):
        for j in range(3):
            rfc[i][j] = ALST_steelbar_KJ([di_main[i, j], SD_main[i, j]])

    ratio_Q = None
    ALW_Q = None

    # 許容せん断力の算定（長期）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if timecase == 1:
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(2, 4 / (1 + (Mmax / Qmax / np.max(D - d, axis=0) * 1000))))
        j = 0.875 * np.max(D - d, axis=0)
        ALW_Q = b * j * (alph * f_c[0, 2] + 0.5 * (min(0.6 / 100, aw / (b * pitch)) - 0.002) * wft[0, 1]) / 10 ** 3
        ALW_Q = ALW_Q  # MATLAB: ALW_Q' （列ベクトル化。1次元ndarrayのため形状不変）
        ratio_Q = np.abs(stress[:, 2]) / ALW_Q

    if timecase >= 2:
        # 梁の降伏モーメントから決定する設計用せん断力
        rfc_y = np.zeros((2, 3))
        rfc_y[0, 0] = rfc[0][0][1, 1]
        rfc_y[0, 1] = rfc[0][1][1, 1]
        rfc_y[0, 2] = rfc[0][2][1, 1]
        rfc_y[1, 0] = rfc[1][0][1, 1]
        rfc_y[1, 1] = rfc[1][1][1, 1]
        rfc_y[1, 2] = rfc[1][2][1, 1]

        My = 0.9 * rfc_y * a * num * (D - d) / 10 ** 6
        Qe2 = max(My[0, 0] + My[1, 2], My[1, 0] + My[0, 2]) / (L)

        # ここで入力された応力QLは長期と仮定して足している．（本当は単純梁としたときの応力に修正必要）
        Qs2 = np.abs(QL) + Qe2
        # せん断割増しから決まる設計用せん断力

        if qup_empty:
            Qs1 = None  # MATLAB原典では[]との積でエラーになる箇所
        else:
            Qs1 = np.abs(qup_beam * (stress[:, 2] - QL) + (QL))

        if RCQ == 3:
            if Qs1 is None:
                raise ValueError('qup_beamが空のためQs1を算定できません')
            Qs = np.minimum(Qs1, Qs2)
        elif RCQ == 1:
            if Qs1 is None:
                raise ValueError('qup_beamが空のためQs1を算定できません')
            Qs = Qs1
        else:
            Qs = Qs2

        # 許容せん断力（短期）
        Mmax = np.max(np.abs(stress[:, 3]))  # kNm
        Qmax = np.max(np.abs(stress[:, 2]))  # kN
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(2, 4 / (1 + (Mmax / Qmax / np.max(D - d, axis=0) * 1000))))
        j = (7 / 8) * np.max(D - d, axis=0)
        if SD_support == 1275:
            ALW_Q = b * j * (alph * f_c[1, 2] + 0.5 * (min(1.0 / 100, aw / (b * pitch)) - 0.001) * wft[1, 1]) / 10 ** 3
        else:
            ALW_Q = b * j * (alph * f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002) * wft[1, 1]) / 10 ** 3  # 230131安全性確保のための検討で、2/3αsfs=>αsfsに変更(kunie)
        ALW_Q = ALW_Q  # MATLAB: ALW_Q'
        ratio_Q = Qs / ALW_Q

    return ratio_Q, ALW_Q, alph, j, Mmax, Qmax


# ===========================================================================
# RC柱 (中実角) 断面算定
# structural_function/RC/RC_section_analysis/column/rectangular/
# ===========================================================================

def _mcolon(a, d, b):
    """MATLABのコロン演算子 a:d:b の再現 (SA_RW4_HMD内の同名関数と同一)."""
    if d == 0:
        return np.zeros(0)
    nn = (b - a) / d
    if nn < 0:
        return np.zeros(0)
    m = int(math.floor(nn + 1e-10 * max(1.0, abs(nn))))
    return a + d * np.arange(m + 1)


def get_RC_slender(a):
    """get_RC_slender.m の逐語移植 (柱の細長比割増)."""
    if a <= 15:
        up_slender = 1.0
    elif a <= 20:
        up_slender = 1.0 * (20 - a) / 5 + 1.25 * (a - 15) / 5
    elif a <= 25:
        up_slender = 1.25 * (25 - a) / 5 + 1.75 * (a - 20) / 5
    else:
        up_slender = 1.75
        _warn('柱の細長比規定チェック！(最大の25として検定)')
    return up_slender


def _SA_RC4_HMD_core(Nd, Form, steelbar, HOOP, Fc, timecase, by):
    """SA_RC4_HMD.m / SA_RC4_HMD_yose.m 共通部 (byの組立てのみ両者で異なる).

    戻り値: (M_AL, maxN)  maxNは長さ2のndarray
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    Nd = float(Nd)

    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    # RC断面の外形情報
    b = Form[0]; D = Form[1]
    di_main = steelbar[2]
    a = Area_steelbar(di_main, 1)
    ai = 2 * a + np.zeros(int(steelbar[3]))
    ai[0] = steelbar[4] * a
    ai[int(steelbar[3]) - 1] = steelbar[4] * a

    num = steelbar[1]
    SD_main = steelbar[5]
    rfc = ALST_steelbar_KJ([di_main, SD_main])
    f_c = ALST_RC_AIJ(Fc)

    n = E_RC_AIJ(Fc)
    n = n[1]
    fc = f_c[timecase2 - 1, 0]
    ft = rfc[timecase2 - 1, 0]

    tol = 0.01  # 許容誤差
    ndiv = 200  # コンクリート部分の分割数
    dy = D / ndiv

    cy = _mcolon(dy / 2, dy, D - dy / 2)

    xnlmax = 10
    xn = -1 * xnlmax * D
    xn0 = 2 * xn
    xndiv = D

    Nd = Nd * 1000

    maxN = np.zeros(2)
    maxN[0] = min((Form[0] * Form[1] + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                  ((Form[0] * Form[1] - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
    maxN[1] = num * a * rfc[timecase2 - 1, 0]

    while True:
        bfmax = 0; bf = by - xn

        bfmax = np.max(np.abs(bf))
        if bf.size > 0 and np.all(bf == 0):  # NOTE: MATLABの if bf==0 は全要素0のとき真
            bf = np.inf
        else:
            bf = bf / bfmax
        cfmax = 0; cf = np.minimum(cy - xn, 0)
        cfmax = np.max(np.abs(cf))

        if cfmax == 0:
            cf = 0
        else:
            cf = cf / cfmax

        if (cfmax * ft / n) > bfmax * fc:
            bf = bf * fc * n; cf = cf * fc
        else:
            bf = bf * ft; cf = cf * ft / n

        Ny = np.sum(bf * ai)
        M_AL = np.sum(bf * ai * (by - D / 2))

        Ny = Ny + np.sum(cf * dy * b)
        M_AL = M_AL + np.sum(cf * dy * b * (cy - D / 2))

        Ny = Ny * -1

        s = abs(Ny - Nd) / b / D
        if s < tol:
            break

        if Ny > Nd:
            xndiv = 0.5 * xndiv
            if xndiv < 2 * D / ndiv:
                break
            xn = xn0 + xndiv

        else:
            xn0 = xn
            xn = xn + xndiv
            if xn > xnlmax * D:
                break

    return M_AL, maxN


def _rc4_dc(steelbar, HOOP):
    """主筋重心の縁距離 dc (SA_RC4_HMD.m 33-34行相当)."""
    di_main = float(np.asarray(steelbar, dtype=float).ravel()[2])
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    di_support = HOOP[0]; cover_depth = HOOP[5]
    return cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5


def SA_RC4_HMD(Nd, Form, steelbar, HOOP, Fc, timecase):
    """SA_RC4_HMD.m の逐語移植 (RC柱 中実角のNM許容曲げ).

    戻り値: (M_AL, maxN)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    D = Form[1]
    dc = _rc4_dc(steelbar, HOOP)
    by = _mcolon(dc, (D - 2 * dc) / (steelbar[3] - 1), D - dc)
    return _SA_RC4_HMD_core(Nd, Form, steelbar, HOOP, Fc, timecase, by)


_YOSE_NOTED = set()


def SA_RC4_HMD_yose(Nd, Form, steelbar, HOOP, Fc, timecase, yose):
    """SA_RC4_HMD_yose.m の逐語移植 (柱主筋のよせ筋設定での算定).

    せい方向本数>3のとき2段目〜(n-1)段目の主筋を端からyose[mm]の位置に
    寄せて配置する。原典は呼び出し毎に display するが同文のため1回に集約。
    戻り値: (M_AL, maxN)
    """
    if 'yose' not in _YOSE_NOTED:
        _YOSE_NOTED.add('yose')
        print('藤本くん箱根対応で柱主筋のよせ筋設定での算定')
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    D = Form[1]
    dc = _rc4_dc(steelbar, HOOP)
    if steelbar[3] > 3:
        by = np.concatenate((
            [dc],
            _mcolon(dc + yose,
                    (D - 2 * dc - 2 * yose) / (steelbar[3] - 3),
                    D - dc - yose),
            [D - dc]))
    else:
        by = _mcolon(dc, (D - 2 * dc) / (steelbar[3] - 1), D - dc)
    return _SA_RC4_HMD_core(Nd, Form, steelbar, HOOP, Fc, timecase, by)


def SA_RC4columnQratio(Form, L, steelbar, HOOP, Fc, stress, timecase, QL,
                       qup_collumn, RCQ):
    """SA_RC4columnQratio.m の逐語移植 (RC■断面柱の許容せん断力算定).

    戻り値: (ratio_Q(3x2), ALW_Q(2,), Qs1, Qs2)
      長期: Qs1=Qs2=空。短期: Qs1/Qs2は(2x3) [強軸(z);弱軸(y)] x [i,中,j]
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.atleast_2d(np.asarray(QL, dtype=float))

    # RC断面の外形情報[mm]
    b = Form[0]; D = Form[1]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        raise ValueError('長短期設定ミス (SA_RC4columnQratio)')  # 原典: ERROR+stop

    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[3])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[2])  # Fzせん断補強筋数

    # steelbar = [type 総本数num_steel，径D，せい方向本数nv，幅方向本数nh, SD]
    num = steelbar[1]; di_main = steelbar[2]; nv = steelbar[3]; nh = steelbar[4]; SD_main = steelbar[5]
    if (nh - 1) * 2 + (nv - 1) * 2 == num:
        pass
    else:
        # 原典: ERROR='配筋情報ミス'+stop
        raise ValueError(
            'RC柱の配筋情報ミス: 主筋総本数%g が 2x(せい方向%g-1)+2x(幅方向%g-1)'
            ' と一致しません' % (num, nv, nh))

    # 有効せいの算出
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    d = np.zeros(2)
    d[0] = D - dc
    d[1] = b - dc

    # 断面情報（配筋断面積，許容応力度など）
    a = Area_steelbar(di_main, 1)
    f_c = ALST_RC_AIJ(Fc)
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        _warn('帯筋間隔NG（D10@100以下もしくはD13以上@200以下）')

    # その４：せん断補強筋比の規定
    if np.min(aw / (np.array([D, b]) * pitch)) >= 0.2 / 100:
        pass
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw='
              + _num2str(np.min(aw / (np.array([D, b]) * pitch)) * 100, '%15.2f')
              + '%→HOOPピッチ' + _num2str(pitch, '%15.0f')
              + 'mm，　HOOP径：D' + _num2str(di_support, '%15.0f')
              + '，　柱幅b：' + _num2str(b, '%15.0f')
              + 'mm，　柱せいD：' + _num2str(D, '%15.0f') + 'mm')

    ratio_Q = np.zeros((3, 2))
    ALW_Q = np.zeros(2)
    Qs1 = np.zeros((0,))
    Qs2 = np.zeros((0,))

    # 許容せん断力の算定（長期）
    if timecase == 1:
        # 強軸せん断
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = 1
        else:
            alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d[0] * 1000))))
        ALW_Q[0] = b * .875 * d[0] * (alph * f_c[0, 2]) / 10 ** 3
        ratio_Q[:, 0] = np.abs(stress[:, 2]) / ALW_Q[0]

        # 弱軸せん断
        Mmax = np.max(np.abs(stress[:, 4]))
        Qmax = np.max(np.abs(stress[:, 1]))
        if Qmax == 0:
            alph = 1
        else:
            alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d[1] * 1000))))
        ALW_Q[1] = D * .875 * d[1] * (alph * f_c[0, 2]) / 10 ** 3
        ratio_Q[:, 1] = np.abs(stress[:, 1]) / ALW_Q[1]

    if timecase == 2:
        Qs1 = np.zeros((2, 3))
        Qs2 = np.zeros((2, 3))
        Qs = np.zeros((2, 3))
        for iyz in (1, 2):
            Mmax = np.max(np.abs(stress[:, 2 + iyz]))   # stress(:,3+iyz)
            Qmax = np.max(np.abs(stress[:, 3 - iyz]))   # stress(:,4-iyz)
            if Qmax == 0:
                alph = 1
            else:
                # NOTE: 原典どおり両軸とも d(2) を使用 (d(iyz)ではない)
                alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d[1] * 1000))))

            # 梁の降伏モーメントから決定する設計用せん断力
            rfc_y = rfc[1, 1]

            My = (0.8 * nh * a * rfc_y * D
                  + max(0, 0.5 * stress[0, 0] * 10 ** 3 * D
                        * (1 - stress[0, 0] * 10 ** 3 / (b * D * Fc[0])))) / 10 ** 6  # kNm
            if My < 0:
                print(My)  # MATLAB: My (値表示のみ)
            Qe2 = 2 * My / L  # kN

            Qs2[iyz - 1, :] = [Qe2, Qe2, Qe2]

            # せん断割増しから決まる設計用せん断力
            Qs1[iyz - 1, :] = np.abs(
                qup_collumn * (stress[:, 3 - iyz] - QL[:, 2 - iyz])
                + QL[:, 2 - iyz])

            if RCQ == 3:
                Qs[iyz - 1, :] = np.minimum(Qs1[iyz - 1, :], Qs2[iyz - 1, :])
            elif RCQ == 1:
                Qs[iyz - 1, :] = Qs1[iyz - 1, :]
            else:
                Qs[iyz - 1, :] = Qs2[iyz - 1, :]

            # 許容せん断力（短期）230310 大地震動に対する安全性の確保(2/3αfs→fs)に変更
            if SD_support == 1275:
                ALW_Q[iyz - 1] = Form[iyz - 1] * (7 / 8) * d[iyz - 1] * (
                    f_c[1, 2] + 0.5 * (min(1.0 / 100, aw[2 - iyz] / (Form[iyz - 1] * pitch)) - 0.001)
                    * wft[1, 1]) / 10 ** 3
            else:
                ALW_Q[iyz - 1] = Form[iyz - 1] * (7 / 8) * d[iyz - 1] * (
                    f_c[1, 2] + 0.5 * (min(1.2 / 100, aw[2 - iyz] / (Form[iyz - 1] * pitch)) - 0.002)
                    * wft[1, 1]) / 10 ** 3
            ratio_Q[:, iyz - 1] = np.abs(Qs[iyz - 1, :]) / ALW_Q[iyz - 1]

    return ratio_Q, ALW_Q, Qs1, Qs2


# ===========================================================================
# RC柱 (中実丸) 断面算定
# structural_function/RC/RC_section_analysis/column/circle/
# ===========================================================================

def SA_RCSRcolumn_AIJ(e, Form, steelbar, HOOP, Fc, timecase):
    """SA_RCSRcolumn_AIJ.m の逐語移植 (建築学会RC基準1999等/RC円形柱断面算定).

    入力: Form=[R] steelbar=[総本数,径D,SD] HOOP=[径D,pitch,SD,cover_depth]
          Fc=[Fc 種別] timecase=1長期/10以上短期
    戻り値: (ALW_N, ALW_M, xn)
    """
    from .src_check import find_e  # 循環import回避のため遅延import
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    e = float(e)
    ALW_N = 0.0
    ALW_M = 0.0
    xn = 0.0

    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    # 外形情報など
    D = Form[0]
    r = Form[0] / 2
    num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[2]; cover_depth = HOOP[3]

    a = Area_steelbar(di_main, 1)

    f_c = ALST_RC_AIJ(Fc)
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # NOTE: 原典33-37行 (num>=8チェック) は本体もコメントアウト済みのため省略

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    rr = r - dc

    # 鉄筋間隔チェック (原典はERROR変数を置くのみで停止しない)
    if (2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 1.5 * di_main
            and 2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 25):
        pass
    else:
        ERROR = '配筋ミス'

    pg = num * a / (math.pi * r ** 2)
    g = r

    if e > 0:  # 偏心率が正の場合
        Ae = math.pi * r ** 2 + n * num * a
        Ig = (1 + 2 * n * pg * (rr / r) ** 2) * math.pi * r ** 4 / 4
        xnout = Ig / (Ae * e) + g
        # 中立軸の算定１ =中立軸が断面内にある場合
        if xnout < D:
            theta = find_e(e, r, rr, pg, n)
            xnin = r * (1 - math.cos(theta))

            Sn = (1.0 / 3 * math.sin(theta) * (2 + math.cos(theta) ** 2)
                  - theta * math.cos(theta)
                  - n * pg * math.pi * math.cos(theta)) * r ** 3
            In = (theta * (1.0 / 4 + math.cos(theta) ** 2)
                  - math.sin(theta) * math.cos(theta)
                  * (13.0 / 12 + 1.0 / 6 * math.cos(theta) ** 2)
                  + n * pg * math.pi
                  * (1.0 / 2 * (rr / r) ** 2 + math.cos(theta) ** 2)) * r ** 4

            N1 = f_c[timecase - 1, 0] * Sn / xnin  # 圧縮側コンクリートの降伏
            if xnin < dc:
                N2 = np.inf
            else:
                N2 = rfc[timecase - 1, 0] * Sn / (n * (xnin - dc))  # 圧縮側鉄筋の降伏
            if xnin > D - dc:
                N3 = np.inf
            else:
                N3 = rfc[timecase - 1, 0] * Sn / (n * (D - dc - xnin))  # 引張側鉄筋の降伏

            ALW_N = min([N1, N2, N3])
            ALW_M = ALW_N * e

        # 中立軸の算定２ =中立軸が断面外にある場合
        if xnout >= D or xnout < 0:
            N1 = f_c[timecase - 1, 0] / (1 / Ae + (g + e - D / 2) / Ig * g)
            N2 = rfc[timecase - 1, 0] / (n * (1 / Ae + (g + e - D / 2) / Ig * (g - dc)))
            ALW_N = min(N1, N2)
            ALW_M = ALW_N * e
            xn = xnout
    elif e < 0:
        Ae = n * num * a
        Ig = n * pg * rr ** 2 * math.pi * r ** 2 / 2
        xnout = Ig / (Ae * e) + g

        if xnout > 0:
            theta = find_e(e, r, rr, pg, n)
            xnin = r * (1 - math.cos(theta))

            Sn = (1.0 / 3 * math.sin(theta) * (2 + math.cos(theta) ** 2)
                  - theta * math.cos(theta)
                  - n * pg * math.pi * math.cos(theta)) * r ** 3
            In = (theta * (1.0 / 4 + math.cos(theta) ** 2)
                  - math.sin(theta) * math.cos(theta)
                  * (13.0 / 12 + 1.0 / 6 * math.cos(theta) ** 2)
                  + n * pg * math.pi
                  * (1.0 / 2 * (rr / r) ** 2 + math.cos(theta) ** 2)) * r ** 4

            if xnin == 0:
                N1 = -np.inf
            else:
                N1 = f_c[timecase - 1, 0] * Sn / xnin  # 圧縮側コンクリートの降伏
            if xnin < dc:
                N2 = -np.inf
            else:
                N2 = rfc[timecase - 1, 0] * Sn / (n * (xnin - dc))  # 圧縮側鉄筋の降伏
            if xnin > D - dc:
                N3 = -np.inf
            else:
                N3 = rfc[timecase - 1, 0] * Sn / (n * (D - dc - xnin))  # 引張側鉄筋の降伏

            # NOTE: 原典124-126行はセミコロン無し(変数のecho表示)のみで計算は同じ
            ALW_N = max([N1, N2, N3])
            ALW_M = ALW_N * e
        elif xnout <= 0:
            # 中立軸が断面外にある時の断面係数（コンクリートは引張状態より無効，鉄筋の引張降伏で決まる)
            Sn = (xnout - g) * Ae
            ALW_N = rfc[timecase - 1, 0] * Sn / (n * (D - xnout - dc))
            ALW_M = ALW_N * e
            xn = xnout
    else:
        pass  # 原典: e==0 は空のelse (ALW_N=ALW_M=xn=0のまま返す)

    return ALW_N, ALW_M, xn


def SA_RCSR_Qratio(Form, L, steelbar, HOOP, Fc, stress, timecase, QL,
                   qup_collumn, RCQ):
    """SA_RCSR_Qratio.m の逐語移植 (RC●断面柱の許容せん断力算定).

    戻り値: (ratio_Q(3x2), ALW_Q(2,), Qs1, Qs2)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.atleast_2d(np.asarray(QL, dtype=float))

    # RC断面の外形情報[mm]
    D = Form[0]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        raise ValueError('長短期設定ミス (SA_RCSR_Qratio)')  # 原典: ERROR+stop

    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[3])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[2])  # Fzせん断補強筋数

    num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]

    # 有効せいの算出
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    d = D - dc

    # 断面情報（配筋断面積，許容応力度など）
    a = Area_steelbar(di_main, 1)
    f_c = ALST_RC_AIJ(Fc)
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        _warn('帯筋間隔NG（D10@100以下もしくはD13以上@200以下）')

    # その４：せん断補強筋比の規定
    if np.min(aw / (D * pitch)) >= 0.2 / 100:
        pass
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw='
              + _num2str(np.min(aw / (D * pitch)) * 100, '%15.2f')
              + '%→HOOPピッチ' + _num2str(pitch, '%15.0f')
              + 'mm，　HOOP径：D' + _num2str(di_support, '%15.0f')
              + '，　柱幅DR：' + _num2str(D, '%15.0f') + 'mm')

    ratio_Q = np.zeros((3, 2))
    ALW_Q = np.zeros(2)
    Qs1 = np.zeros((0,))
    Qs2 = np.zeros((0,))

    # 許容せん断力の算定（長期）
    if timecase == 1:
        # 強軸せん断
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = 1
        else:
            alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d * 1000))))
        ALW_Q[0] = d ** 2 / 4 * math.pi * (alph * f_c[0, 2]) / 10 ** 3
        ratio_Q[:, 0] = np.abs(stress[:, 2]) / ALW_Q[0]

        # 弱軸せん断
        Mmax = np.max(np.abs(stress[:, 4]))
        Qmax = np.max(np.abs(stress[:, 1]))
        if Qmax == 0:
            alph = 1
        else:
            alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d * 1000))))
        ALW_Q[1] = d ** 2 / 4 * math.pi * (alph * f_c[0, 2]) / 10 ** 3
        ratio_Q[:, 1] = np.abs(stress[:, 1]) / ALW_Q[1]

    if timecase == 2:
        Qs1 = np.zeros((2, 3))
        Qs2 = np.zeros((2, 3))
        Qs = np.zeros((2, 3))
        for iyz in (1, 2):
            Mmax = np.max(np.abs(stress[:, 2 + iyz]))   # stress(:,3+iyz)
            Qmax = np.max(np.abs(stress[:, 3 - iyz]))   # stress(:,4-iyz)
            if Qmax == 0:
                alph = 1
            else:
                alph = max(1, min(2, 4. / (1 + (Mmax / Qmax / d * 1000))))

            # 柱の降伏モーメントから決定する設計用せん断力
            rfc_y = rfc[1, 1]
            nh = num / 4 + 1
            D1 = math.sqrt(D ** 2 / 4 * math.pi)

            My = (0.8 * nh * a * rfc_y * D1
                  + 0.5 * stress[0, 0] * 10 ** 3 * D1
                  * (1 - stress[0, 0] * 10 ** 3 / (D1 ** 2 * Fc[0]))) / 10 ** 6  # kNm
            Qe2 = 2 * My / (L / 1000)  # kN (NOTE: 原典どおり L/1000)

            Qs2[iyz - 1, :] = [Qe2, Qe2, Qe2]

            # せん断割増しから決まる設計用せん断力
            Qs1[iyz - 1, :] = (qup_collumn
                               * np.abs(stress[:, 3 - iyz] - QL[:, 2 - iyz])
                               + np.abs(QL[:, 2 - iyz]))

            if RCQ == 3:
                Qs[iyz - 1, :] = np.minimum(Qs1[iyz - 1, :], Qs2[iyz - 1, :])
            elif RCQ == 1:
                Qs[iyz - 1, :] = Qs1[iyz - 1, :]
            else:
                Qs[iyz - 1, :] = Qs2[iyz - 1, :]

            # 許容せん断力（短期）
            ALW_Q[iyz - 1] = d ** 2 / 4 * math.pi * (
                2.0 / 3 * alph * f_c[1, 2]
                + 0.5 * (min(1.2 / 100, aw[2 - iyz] / (D * pitch)) - 0.002)
                * wft[1, 1]) / 10 ** 3
            ratio_Q[:, iyz - 1] = Qs[iyz - 1, :] / ALW_Q[iyz - 1]

    return ratio_Q, ALW_Q, Qs1, Qs2


# ===========================================================================
# 検定詳細テキスト (SA_RW4_HMD_text.m / SA_RCbeamratio_text.m)
# ===========================================================================

def SA_RW4_HMD_text(ele_length, steelbar_y, steelbar_z, HOOP, Fc, stress, timecase,
                    QL, ele_no, section_no, qup_wall, reduction, LOAS_CASE_NAME, Form_Q, WL_ef,
                    sectionsize, RCQ, section_name, walldesign_index, v_pitch, v_num, method_rcw=None):
    """SA_RW4_HMD_text.m の逐語移植
    RC壁の断面算定詳細のテキスト出力
    軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    """
    S = np.asarray(stress, dtype=float)
    A_HOOP = np.concatenate([np.asarray(HOOP, dtype=float).ravel()[:5], [sectionsize[0] / 2]])
    e = np.zeros(3)
    M_AL = np.zeros((3, 2))
    ratio_output = np.zeros((3, 4))
    for ie in range(1, 4):
        Form_y = [WL_ef[2 * ie - 2], sectionsize[0]]
        if S[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(S[ie - 1, 3]) / S[ie - 1, 0] * 1000
        M_AL[ie - 1, 0] = SA_RW4_HMD(S[ie - 1, 0], Form_y, steelbar_y, HOOP, Fc, timecase)[0]
        if e[ie - 1] == 0:
            if S[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 0] = abs(S[ie - 1, 3]) * 10 ** 6 / abs(M_AL[ie - 1, 0])
            else:  # 引張
                ratio_output[ie - 1, 0] = abs(S[ie - 1, 3]) * 10 ** 6 / abs(M_AL[ie - 1, 0])
        else:
            ratio_output[ie - 1, 0] = abs(S[ie - 1, 3]) * 10 ** 6 / abs(M_AL[ie - 1, 0])
    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
    # 許容NM曲線を呼び出し
    for ie in range(1, 4):
        Form_z = [sectionsize[0], WL_ef[2 * ie - 2]]
        if S[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(S[ie - 1, 4]) / S[ie - 1, 0] * 1000
        M_AL[ie - 1, 1] = SA_RW4_HMD(S[ie - 1, 0], Form_z, steelbar_z, A_HOOP, Fc, timecase)[0]
        if e[ie - 1] == 0:
            if S[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])
            else:  # 引張
                ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])
        else:
            ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])

    # せん断(Q)に対する断面算定
    ratio_Q, ALW_Q, Qs1 = SA_RW4Qratio(Form_Q, ele_length, steelbar_y, HOOP, Fc, stress, timecase, QL, qup_wall, RCQ)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
    ratio_output[1, :] = 0
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel()
    Qs1 = np.asarray(Qs1, dtype=float)
    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]
    QLw = np.asarray(QL, dtype=float)

    # 以上で検定値の算出終了

    # RC断面の外形情報
    b = sectionsize[1]
    D = sectionsize[0]

    # 帯筋情報を読み込み
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[4]
    cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[2])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[3])  # Fzせん断補強筋数

    # 主筋情報：steelbar = [type 総本数num_steel，径D，壁長さ方向本数nv，壁厚方向本数nh, SD]
    num = steelbar_z[1]
    di_main = steelbar_z[2]
    nv = steelbar_z[3]
    nh = steelbar_z[4]
    SD_main = steelbar_z[5]

    if di_main < 19:  # SD_main:縦筋材料
        SD_main = 295
    elif di_main < 29:
        SD_main = 345
    elif di_main < 51:
        SD_main = 390

    if walldesign_index == 2:
        di_sp = steelbar_z[6]
        SD_sp = steelbar_z[9]
        nsp = steelbar_z[7]
        if di_sp < 19:  # SD_sp:端部筋材料
            SD_sp = 295
        elif di_sp < 29:
            SD_sp = 345
        elif di_sp < 51:
            SD_sp = 390

    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    if nh == 1:
        dd = b
    else:
        dd = (b - 2 * dc) / (nv - 1)

    if nv == 1 and num == nh:
        pass
    elif nh == 1 and num == nv:
        pass
    elif (nh - 1) * 2 + (nv - 1) * 2 == num:
        pass
    else:
        raise ValueError('配筋情報ミス')
    a = Area_steelbar(di_main, 1)

    if walldesign_index == 1:
        text = ['RC壁の断面算定内容（最大検定値の検定内容）']
    elif walldesign_index == 2:
        text = ['壁式RC造のRC壁の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と強軸および弱軸周りの曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    if walldesign_index == 2:
        if method_rcw == 1:
            text.append('面内の曲げについては端部補強筋のみを考慮して断面算定を行い，面外曲げについては曲げ補強筋を考慮しない')
        elif method_rcw == 2:
            text.append('面内の曲げについては縦筋のみを考慮して断面算定を行い，面外曲げについては曲げ補強筋を考慮しない')
        elif method_rcw == 3:
            text.append('面内の曲げについては縦筋及び端部補強筋を考慮して断面算定を行い，面外曲げについては曲げ補強筋を考慮しない')
        text.append('また直交方向壁などの縦筋，補強筋も考慮せず当該壁のみの鉄筋で設計を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC壁柱サイズ(モデル内)　：Lxt-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('NM検定サイズ（ i　端 ）　：Lxt-' + _num2str(WL_ef[0]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('NM検定サイズ（ j　端 ）　：Lxt-' + _num2str(WL_ef[4]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('Q検定サイズ（ i　端 ）　：Lxt-' + _num2str(WL_ef[1]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('Q検定サイズ（ j　端 ）　：Lxt-' + _num2str(WL_ef[5]) + '[mm] x' + _num2str(D) + '[mm]')

    text.append('　　')
    text.append('*****配筋情報*****')
    if walldesign_index == 1:
        text.append('縦筋配筋　：' + _num2str(num - 4) + '-D' + _num2str(di_main) + '　横筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    elif walldesign_index == 2:
        if method_rcw == 1:
            text.append('縦端部補強筋両側合計：' + _num2str(nsp) + '-D' + _num2str(di_sp) + '　縦筋 :' + _num2str(v_num) + '-D' + _num2str(di_main) + '@' + _num2str(v_pitch) + '　横筋　：' + _num2str(v_num) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))  # 2410607金澤追記
        elif method_rcw == 2:
            text.append('縦筋合計(縦筋配筋)：' + _num2str(num) + '-D' + _num2str(di_main) + '(' + _num2str(v_num) + '-D' + _num2str(di_main) + '@' + _num2str(v_pitch) + ')　　横筋　：' + _num2str(v_num) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))
        elif method_rcw == 3:
            text.append('縦端部補強筋両側合計：' + _num2str(nsp) + '-D' + _num2str(di_sp) + '　縦筋 :' + _num2str(v_num) + '-D' + _num2str(di_main) + '@' + _num2str(v_pitch) + '　横筋　：' + _num2str(v_num) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))

    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    if walldesign_index == 1:
        text.append('縦筋　：SD' + _num2str(SD_main) + '　　横筋　：SD' + _num2str(SD_support))
    elif walldesign_index == 2:
        if method_rcw == 1:
            text.append('縦端部補強筋　：SD' + _num2str(SD_sp) + '　　縦筋　：SD' + _num2str(SD_main) + '　　横筋　：SD' + _num2str(SD_support))
        elif method_rcw == 2:
            text.append('縦筋　：SD' + _num2str(SD_main) + '　　横筋　：SD' + _num2str(SD_support))
        elif method_rcw == 3:
            text.append('縦端部補強筋　：SD' + _num2str(SD_sp) + '　　縦筋　：SD' + _num2str(SD_main) + '　　横筋　：SD' + _num2str(SD_support))
    text.append('コンクリート　：Fc' + _num2str(Fc[0]))
    text.append('　　')

    # 純圧縮のplot
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        raise ValueError('長短期設定ミス')

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊横筋間隔の規定')
    if di_support == 10 and pitch <= 300:
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch) + '　：OK')
    elif di_support > 10 and pitch <= 300:
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch) + '　：OK')
    else:
        _warn('横筋間隔NG')
        text.append('横筋径　：D' + _num2str(di_support) + '　横筋間隔　：' + _num2str(pitch) + '　：横筋間隔NG')
        # stop

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧40mm　：OK')
    else:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜40mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）')
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力（Myが面外曲げ／Mzが面内曲げ）*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    text.append('軸力　：' + _num2str(Fxx[0] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[0] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QLw[0, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QLw[0, 0], '%15.1f') + '[kN]')
        # 26.04.23富岡追記　地震時せん断力の表記
        text.append('地震時せん断力Qz　：' + _num2str(S[0, 2] - QLw[0, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(S[0, 1] - QLw[0, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 0], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 0], '%15.1f') + '[kN]')

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    text.append('軸力　：' + _num2str(Fxx[2] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[2] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[2] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QLw[2, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QLw[2, 0], '%15.1f') + '[kN]')
        # 26.04.23富岡追記 地震時せん断力の表記
        text.append('地震時せん断力Qz　：' + _num2str(S[2, 2] - QLw[2, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(S[2, 1] - QLw[2, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 2], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 2], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    e_y = []
    e_z = []
    for ie in range(1, 4):  # e_y
        if S[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e_y.append('Inf')
            e_z.append('Inf')
        else:
            e_y.append(_num2str(abs(S[ie - 1, 3]) / S[ie - 1, 0] * 1000, '%15.0f'))
            e_z.append(_num2str(abs(S[ie - 1, 4]) / S[ie - 1, 0] * 1000, '%15.0f'))

    text.append('*****許容耐力・検定比*****')
    text.append('　　')
    text.append('*せん断耐力低減(r)：　' + _num2str(reduction, '%15.2f'))
    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　i　端')
    text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[0, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[0, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]' + '　せん断力Qy　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]' + '　低減後のせん断耐力Qy　：' +
                _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMy]　：' + _num2str(ratio_output[0, 0], '%15.2f') + '　　[NMz]　：' + _num2str(ratio_output[0, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[0, 2], '%15.2f') + '　　[Qy]　：' + _num2str(ratio_output[0, 3], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[2, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[2, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(ALW_Q[2], '%15.1f') + '　[kN]' + '　せん断力Qy　：' + _num2str(ALW_Q[2], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[2] * reduction, '%15.1f') + '　[kN]' + '　低減後のせん断耐力Qy　：' +
                _num2str(ALW_Q[2] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMy]　：' + _num2str(ratio_output[2, 0], '%15.2f') + '　　[NMz]　：' + _num2str(ratio_output[2, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[2, 2], '%15.2f') + '　　[Qy]　：' + _num2str(ratio_output[2, 3], '%15.2f'))
    return text


def SA_RCbeamratio_text(Form, ele_length, steelbar_u, steelbar_d, STRP, Fc, stress, timecase,
                        QL, ele_no, section_no, ALW_M, qup_beam, up, LOAS_CASE_NAME, TAPERED, RCQ, section_name):
    """SA_RCbeamratio_text.m の逐語移植
    RC梁の断面算定詳細のテキスト出力
    曲げ(MM)，せん断(Q)に対する断面算定
    TAPERED テーパー断面の検定
    """
    S = np.asarray(stress, dtype=float)
    AM = np.asarray(ALW_M, dtype=float)
    QLb = np.asarray(QL, dtype=float).ravel()
    su = np.asarray(steelbar_u)
    sd = np.asarray(steelbar_d)

    # 曲げ(MM)に対する断面算定
    if TAPERED == 0:
        up = [up, up, up]
    else:
        Fm0 = np.asarray(Form)
        Form_1 = Fm0[0, :]
        Form_2 = Fm0[1, :]
        Form_3 = Fm0[2, :]
        up = np.asarray(up).ravel()  # MATLABのup(ie)添字互換
    ratio_output = np.zeros((3, 2))
    for ie in range(1, 4):
        if S[ie - 1, 3] >= 0:
            ratio_output[ie - 1, 0] = S[ie - 1, 3] * up[ie - 1] / AM[ie - 1, 0]
        else:
            ratio_output[ie - 1, 0] = S[ie - 1, 3] * up[ie - 1] / AM[ie - 1, 1]

    ALW_Q = np.zeros((3, 3))
    if TAPERED == 0:
        # せん断(Q)に対する断面算定
        rq, aq, alph, j_dis, Mmax, Qmax = SA_RCbeamQratio(Form, ele_length, steelbar_u, steelbar_d, STRP, Fc, stress, timecase, QL, qup_beam, RCQ)
        ratio_output[:, 1] = np.asarray(rq, dtype=float).ravel()
        ALW_Q[:, 0] = np.asarray(aq, dtype=float).ravel()
        ALW_Q[:, 1] = ALW_Q[:, 0]
        ALW_Q[:, 2] = ALW_Q[:, 0]
        alph = np.asarray(alph, dtype=float).ravel()
        j_dis = np.asarray(j_dis, dtype=float).ravel()
    else:
        # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
        rq1, aq1, alph1, j_dis1, Mmax, Qmax = SA_RCbeamQratio(Form_1, ele_length, steelbar_u, steelbar_d, STRP, Fc, stress, timecase, QL, qup_beam, RCQ)
        rq2, aq2, alph2, j_dis2, Mmax, Qmax = SA_RCbeamQratio(Form_2, ele_length, steelbar_u, steelbar_d, STRP, Fc, stress, timecase, QL, qup_beam, RCQ)
        rq3, aq3, alph3, j_dis3, Mmax, Qmax = SA_RCbeamQratio(Form_3, ele_length, steelbar_u, steelbar_d, STRP, Fc, stress, timecase, QL, qup_beam, RCQ)
        rq1 = np.asarray(rq1, dtype=float).ravel()
        rq2 = np.asarray(rq2, dtype=float).ravel()
        rq3 = np.asarray(rq3, dtype=float).ravel()
        ALW_Q[:, 0] = np.asarray(aq1, dtype=float).ravel()
        ALW_Q[:, 1] = np.asarray(aq2, dtype=float).ravel()
        ALW_Q[:, 2] = np.asarray(aq3, dtype=float).ravel()
        alph1 = np.asarray(alph1, dtype=float).ravel()
        alph2 = np.asarray(alph2, dtype=float).ravel()
        alph3 = np.asarray(alph3, dtype=float).ravel()
        j_dis1 = np.asarray(j_dis1, dtype=float).ravel()
        j_dis2 = np.asarray(j_dis2, dtype=float).ravel()
        j_dis3 = np.asarray(j_dis3, dtype=float).ravel()

        ratio_output[:, 1] = [rq1[0], rq2[1], rq3[2]]
        alph = np.array([alph1[0], alph2[1], alph3[2]])
        j_dis = np.array([j_dis1[0], j_dis2[1], j_dis3[2]])

    # 以上で検定値の算出終了

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # RC断面の外形情報[mm単位で入力]
    if TAPERED == 0:
        b = Form[0]
        D = Form[1]
        dd = np.zeros((2, 3)) + D  # 二段配筋の際には1000が書き換えられる．
    else:
        Fm = np.asarray(Form)
        b = Fm[:, 0]
        D = Fm[:, 1]
        dd = np.zeros((2, 3)) + np.array([[D[0], D[1], D[2]], [D[0], D[1], D[2]]])  # 二段配筋の際には1000が書き換えられる．

    # あばら筋情報を読み込み
    di_support = STRP[0]
    pitch = STRP[1]
    SD_support = STRP[3]
    cover_depth = STRP[4]
    aw = Area_steelbar(di_support, STRP[2])

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.vstack([su[:, 1:4].sum(axis=1), sd[:, 1:4].sum(axis=1)])
    di_main = np.vstack([su[:, 4], sd[:, 4]])
    SD_main = np.vstack([su[:, 7], sd[:, 7]])
    # 梁の上端筋と下端筋の段数から重心距離の計算

    d_out = np.zeros((2, 3))
    d = np.zeros((2, 3))
    # 上端筋
    for i in range(1, 4):
        d_out[0, i - 1] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[0, i - 1]) * 0.5
        if su[i - 1, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[0, i - 1] = d_out[0, i - 1]
            if su[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数）')
        elif su[i - 1, 0] == 2:
            # 主筋の重心距離（二段配筋）
            # 主筋間隔
            if su.shape[1] == 9:
                dd[0, i - 1] = su[i - 1, 8]
            elif su.shape[1] == 8:
                dd[0, i - 1] = math.ceil(max(di_main[0, i - 1] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, i - 1])
            d[0, i - 1] = d_out[0, i - 1] * su[i - 1, 1] / num[0, i - 1] + (d_out[0, i - 1] + dd[0, i - 1]) * su[i - 1, 2] / num[0, i - 1]
        elif su[i - 1, 0] == 3:
            # 主筋の重心距離（3段配筋）
            # 主筋間隔
            if su.shape[1] == 9:
                dd[0, i - 1] = su[i - 1, 8]
            elif su.shape[1] == 8:
                dd[0, i - 1] = math.ceil(max(di_main[0, i - 1] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, i - 1])
            d[0, i - 1] = d_out[0, i - 1] * su[i - 1, 1] / num[0, i - 1] + (d_out[0, i - 1] + dd[0, i - 1]) * su[i - 1, 2] / num[0, i - 1] + (d_out[0, i - 1] + 2 * dd[0, i - 1]) * su[i - 1, 3] / num[0, i - 1]
        else:
            raise ValueError('配筋情報エラー（配筋段数）')

    # 下端筋
    for i in range(1, 4):
        d_out[1, i - 1] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[1, i - 1]) * 0.5
        if sd[i - 1, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[1, i - 1] = d_out[1, i - 1]
            if sd[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数）')
        elif sd[i - 1, 0] == 2:
            # 主筋の重心距離（二段配筋）
            # 主筋間隔
            if sd.shape[1] == 7:
                dd[1, i - 1] = sd[i - 1, 6]
            elif sd.shape[1] == 6:
                dd[1, i - 1] = math.ceil(max(di_main[1, i - 1] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, i - 1])
            d[1, i - 1] = d_out[1, i - 1] * sd[i - 1, 1] / num[1, i - 1] + (d_out[1, i - 1] + dd[1, i - 1]) * sd[i - 1, 2] / num[1, i - 1]
        elif sd[i - 1, 0] == 3:
            # 主筋の重心距離（3段配筋）
            # 主筋間隔
            if sd.shape[1] == 9:
                dd[1, i - 1] = sd[i - 1, 8]
            elif sd.shape[1] == 8:
                dd[1, i - 1] = math.ceil(max(di_main[1, i - 1] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, i - 1])
            d[1, i - 1] = d_out[1, i - 1] * sd[i - 1, 1] / num[1, i - 1] + (d_out[1, i - 1] + dd[1, i - 1]) * sd[i - 1, 2] / num[1, i - 1] + (d_out[1, i - 1] + 2 * dd[1, i - 1]) * sd[i - 1, 3] / num[1, i - 1]
        else:
            raise ValueError('配筋情報エラー（配筋段数）')

    # a=Area_steelbar(bar_table,di_main,1); di_mainは2x3行列→要素毎に適用
    a = np.array([[Area_steelbar(di_main[i_, j_], 1) for j_ in range(3)] for i_ in range(2)], dtype=float)

    text = ['RC梁の断面算定内容（最大検定値の検定内容）']
    text.append('強軸周りの曲げとせん断に対して断面算定を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    if TAPERED == 0:
        text.append('RC梁サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]　RC梁スパン[m]：' + _num2str(ele_length, '%15.2f'))
    else:
        text.append('RC梁サイズ<i端>：bxD-' + _num2str(b[0]) + '[mm] x' + _num2str(D[0]) + '[mm]　RC梁サイズ<j端>：bxD-' +
                    _num2str(b[2]) + '[mm] x' + _num2str(D[2]) + '[mm]　RC梁スパン[m]：' + _num2str(ele_length, '%15.2f'))
    text.append('　　')
    text.append('*****配筋情報（本数のあとの数字は段数を示す）*****')
    text.append('　　')
    text.append('上端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[0, 0]) + '(' + _num2str(su[0, 0]) + ')-D' + _num2str(di_main[0, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[0, 1]) + '(' + _num2str(su[1, 0]) + ')-D' + _num2str(di_main[0, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[0, 2]) + '(' + _num2str(su[2, 0]) + ')-D' + _num2str(di_main[0, 2]))
    text.append('　　')
    text.append('下端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[1, 0]) + '(' + _num2str(sd[0, 0]) + ')-D' + _num2str(di_main[1, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[1, 1]) + '(' + _num2str(sd[1, 0]) + ')-D' + _num2str(di_main[1, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[1, 2]) + '(' + _num2str(sd[2, 0]) + ')-D' + _num2str(di_main[1, 2]))
    text.append('　　')
    if SD_support == 1275:
        if di_support == 10:
            text.append('あばら筋：' + _num2str(STRP[2]) + '-U10.7@' + _num2str(pitch))
        elif di_support == 13:
            text.append('あばら筋：' + _num2str(STRP[2]) + '-U12.6@' + _num2str(pitch))
        elif di_support == 9:
            text.append('あばら筋：' + _num2str(STRP[2]) + '-U9.0@' + _num2str(pitch))
    else:
        text.append('あばら筋：' + _num2str(STRP[2]) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    if SD_support == 1275:
        text.append('主筋：SD' + _num2str(SD_main[0, 0]) + '　あばら筋（ウルボン利用）：SBPD' + _num2str(SD_support))
    else:
        text.append('主筋：SD' + _num2str(SD_main[0, 0]) + '　あばら筋：SD' + _num2str(SD_support))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    text.append('　　')

    # 純圧縮のplot
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        raise ValueError('長短期設定ミス')

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    if TAPERED == 0:
        b1 = np.zeros((2, 3)) + b
        D1 = np.zeros((2, 3)) + D
    else:
        b1 = np.vstack([b, b])
        D1 = np.vstack([D, D])
    max_u = np.max(su[:, 1:4], axis=1)
    max_d = np.max(sd[:, 1:4], axis=1)
    if np.all(np.maximum(max_u, max_d) == 1):
        dd2 = b1 / 2
    else:
        with np.errstate(divide='ignore', invalid='ignore'):
            dd2 = (b1 - 2 * d_out) / (np.vstack([max_u, max_d]).astype(float) - 1)
    dd2 = np.min(dd2)

    outf_main = np.array([[outf_steelbar_JIS(di_main[i_, j_]) for j_ in range(3)] for i_ in range(2)], dtype=float)
    check_dd = dd - outf_main
    check_dd2 = dd2 - outf_main

    checkdd = np.minimum(check_dd, check_dd2)
    min_dd = np.maximum(1.5 * di_main, 25 * 1.25)

    judge_dd = checkdd - min_dd

    if np.min(judge_dd) >= 0:
        pass
    else:
        print('鉄筋あき不足(梁せいおよび梁幅方向の検討）')
        print('梁せい方向あき：' + _num2str(np.min(check_dd), '%15.2f') + 'mm')
        print('梁幅方向あき：' + _num2str(np.min(check_dd2), '%15.2f') + 'mm')
        print('あきの最小値：' + _num2str(np.min(min_dd), '%15.2f') + 'mm')
        print('断面番号：' + _num2str(section_no))
    text.append('　　')
    text.append('主筋のあきの検討')
    text.append('最低あき寸法（径の1.5倍，粗骨材寸法の1.25倍，25mmの最大値）：' + _num2str(np.min(min_dd), '%15.1f'))
    text.append('梁幅方向のあき[mm]：' + _num2str(np.min(check_dd2), '%15.1f') + '　梁せい方向のあき[mm]：' + _num2str(np.min(check_dd), '%15.1f') + '[mm]：')

    # その１：主筋の規定
    text.append('　　')
    text.append('主筋の規定（径D13以上）')
    text.append('使用鉄筋の径：D' + _num2str(np.min(di_main)))
    if np.min(di_main) >= 13:
        text.append('主筋径の規定：OK')
    else:
        text.append('主筋径の規定：NG')
        # ERROR='主筋規定外(D13以上，配筋段数については既に検討済)' （MATLAB原典は続行）
        # stop

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b1 * D1)
    pt_ID1 = np.argmin(pt, axis=0)
    pt1 = np.min(pt, axis=0)
    pt_ID2 = int(np.argmin(pt1))
    min_pt = pt[pt_ID1[pt_ID2], pt_ID2]

    text.append('　　')
    text.append('引張鉄筋必要最低断面積（全断面の0.4％以上）')
    text.append('最小引張鉄筋仕様：' + _num2str(num[pt_ID1[pt_ID2], pt_ID2]) + '-D' + _num2str(di_main[pt_ID1[pt_ID2], pt_ID2]))
    text.append('鉄筋総断面積' + _num2str(num[pt_ID1[pt_ID2], pt_ID2] * a[pt_ID1[pt_ID2], pt_ID2] / 100, '%15.2f') + '[cm2]')

    if min_pt >= 0.4 / 100:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[％]　"OK"')
        up = 1.0
    else:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[％]　"0.4\\%以下→長期応力割増"')
        up = 4 / 3

    # その３：STRP間隔の規定
    text.append('　　')
    text.append('あばら筋径・間隔の規定')
    if di_support <= 10 and pitch <= min(np.min(np.asarray(D) * 3 / 4), 250):
        text.append('あばら筋径：D' + _num2str(di_support) + '　あばら筋間隔：' + _num2str(pitch) + '　"OK(せいの3/4かつ250mm以下)"')
    elif di_support > 10 and pitch <= min(np.min(np.asarray(D) * 3 / 4), 450):
        text.append('あばら筋径：D' + _num2str(di_support) + '　あばら筋間隔：' + _num2str(pitch) + '　"OK(せいの3/4かつ450mm以下)"')
    else:
        if np.ndim(D) == 0:
            D_str = _num2str(D, '%15.0f')
        else:
            D_str = (''.join(['%15.0f' % x for x in np.asarray(D).ravel()])).strip()
        text.append('あばら筋径：D' + _num2str(di_support) + '　あばら筋間隔：' + _num2str(pitch) + '　"あばら筋間隔NG"　梁せい：' + D_str + 'mm')
        # stop

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('せん断補強筋比の規定(0.2％以上)')
    text.append('あばら筋の仕様：' + _num2str(STRP[2]) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (np.min(np.asarray(b)) * pitch) >= 0.2 / 100:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (np.min(np.asarray(b)) * pitch) * 100, '%15.2f') + '\\%＞0.2\\%：OK')
    else:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (np.min(np.asarray(b)) * pitch) * 100, '%15.2f') + '\\%＜0.2\\%：NG')
        _warn('せん断補強筋比不足（0.2％未満）　pw=' + _num2str(aw / (np.min(np.asarray(b)) * pitch) * 100, '%15.2f') + '\\%→STRPピッチ' + _num2str(pitch, '\\%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '\\%15.0f') + '，　梁幅b：' + _num2str(np.min(np.asarray(b)), '%15.0f') + 'mm')
    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚：' + _num2str(cover_depth) + 'mm≧40mm：OK')
    else:
        # ERROR='鉄筋かぶり厚再検討（40mm未満）' （MATLAB原典は続行）
        text.append('かぶり厚：' + _num2str(cover_depth) + 'mm＜40mm：NG')

    text.append('　　')
    text.append('*****部材応力*****')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　i　端')
    text.append('曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　中央')
    text.append('曲げMy　：' + _num2str(My[1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　j　端')
    text.append('曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')

    if timecase == 1:
        designQ = np.abs(S[:, 2])
    else:
        rfc = [[None, None, None], [None, None, None]]
        for i in range(1, 3):
            for j in range(1, 4):
                rfc[i - 1][j - 1] = ALST_steelbar_KJ([di_main[i - 1, j - 1], SD_main[i - 1, j - 1]])
        rfc_y = np.zeros((2, 3))
        rfc_y[0, 0] = rfc[0][0][1][1]
        rfc_y[0, 1] = rfc[0][1][1][1]
        rfc_y[0, 2] = rfc[0][2][1][1]
        rfc_y[1, 0] = rfc[1][0][1][1]
        rfc_y[1, 1] = rfc[1][1][1][1]
        rfc_y[1, 2] = rfc[1][2][1][1]

        Myi = 0.9 * rfc_y * a * num * (D1 - d) / 10 ** 6
        Qe2 = max(Myi[0, 0] + Myi[1, 2], Myi[1, 0] + Myi[0, 2]) / (ele_length)

        # ここで入力された応力QLは長期と仮定して足している．（本当は単純梁としたときの応力に修正必要）
        Qs2 = np.abs(QLb) + Qe2

        # せん断割増しから決まる設計用せん断力
        # 2026.04.23富岡修正
        Qs1 = np.abs(qup_beam * (S[:, 2] - QLb) + (QLb))

        if RCQ == 3:
            designQ = np.minimum(Qs1, Qs2)
        elif RCQ == 1:
            designQ = Qs1
        else:
            designQ = Qs2

    text.append('*****設計用応力*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    if timecase == 1:
        pass
    else:
        # 2026.04.23富岡修正 地震時せん断力表示
        text.append('長期荷重時せん断力：' + _num2str(QLb[0], '%15.1f') + '　[kN]　地震時せん断力：' + _num2str(S[0, 2] - QLb[0], '%15.1f') + '　[kN]')
        text.append('梁の降伏モーメントから決定する設計用せん断力：' + _num2str(Qs2[0], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(Qs1[0], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(up * My[0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    if timecase == 1:
        pass
    else:
        # 2026.04.23富岡修正 地震時せん断力表示
        text.append('長期荷重時せん断力：' + _num2str(QLb[1], '%15.1f') + '　[kN]　地震時せん断力：' + _num2str(S[1, 2] - QLb[1], '%15.1f') + '　[kN]')
        text.append('梁の降伏モーメントから決定する設計用せん断力：' + _num2str(Qs2[1], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(Qs1[1], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(up * My[1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[1], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    if timecase == 1:
        pass
    else:
        # 2026.04.23富岡修正 地震時せん断力表示
        text.append('長期荷重時せん断力：' + _num2str(QLb[2], '%15.1f') + '　[kN]　地震時せん断力：' + _num2str(S[2, 2] - QLb[2], '%15.1f') + '　[kN]')
        text.append('梁の降伏モーメントから決定する設計用せん断力：' + _num2str(Qs2[2], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(Qs1[2], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(up * My[2] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[2], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('　　')

    text.append('せん断耐力算定α<ｉ端・中央・ｊ端>：' + _num2str(alph[0], '%15.1f') + '/' + _num2str(alph[1], '%15.1f') + '/' + _num2str(alph[2], '%15.1f'))
    text.append('梁の有効せい` ｊ(mm)<ｉ端・中央・ｊ端>：' + _num2str(j_dis[0], '%15.1f') + '/' + _num2str(j_dis[1], '%15.1f') + '/' + _num2str(j_dis[2], '%15.1f'))
    text.append('Mmax(kNm)：' + _num2str(Mmax, '%15.1f'))
    text.append('Qmax(kN)：' + _num2str(Qmax, '%15.1f'))
    text.append('　　')
    text.append('　　')

    text.append('*****許容耐力・検定比*****')
    text.append('*許容耐力　[' + t_case + ']　i　端')
    text.append('正曲げMy　：' + _num2str(AM[0, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(AM[0, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[0, 0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[0, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　中央')
    text.append('正曲げMy　：' + _num2str(AM[1, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(AM[1, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[1, 0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[1, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('正曲げMy　：' + _num2str(AM[2, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(AM[2, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[2, 2], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[2, 1], '%15.2f'))
    return text


# ===========================================================================
# RCW_input.m (壁柱の配筋情報決定) のダイアログ引数化
# ===========================================================================

def SA_RC4columnratioHMD_text(Form_y, Form_z, ele_length, steelbar_y,
                              steelbar_z, HOOP, Fc, stress, timecase, QL,
                              ele_no, section_no, qup_column, up_column,
                              LOAS_CASE_NAME, RCQ, up_slender, section_name):
    """SA_RC4columnratioHMD_text.m の逐語移植
    RC柱の断面算定詳細のテキスト出力
    軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    """
    S = np.atleast_2d(np.asarray(stress, dtype=float))
    QLc = np.atleast_2d(np.asarray(QL, dtype=float))
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    steelbar_y = np.asarray(steelbar_y, dtype=float).ravel()
    steelbar_z = np.asarray(steelbar_z, dtype=float).ravel()
    up_slender = np.asarray(up_slender, dtype=float).ravel()
    e = np.zeros(3)
    M_AL = np.zeros((3, 2))
    ratio_output = np.zeros((3, 4))

    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 3]) / S[ie, 0] * 1000
        # 等間隔配筋 SA_RC4_HMD を使用 (原典バグ修正 2026-07-11: RC4_YOSE_FIX)
        M_AL[ie, 0], maxN = SA_RC4_HMD(S[ie, 0], Form_y, steelbar_y,
                                       HOOP, Fc, timecase)
        if e[ie] == 0:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 0] = max(abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie, 0] = max(abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1]))

    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 4]) / S[ie, 0] * 1000
        M_AL[ie, 1], maxN = SA_RC4_HMD(S[ie, 0], Form_z, steelbar_z,
                                       HOOP, Fc, timecase)
        if e[ie] == 0:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 1] = max(abs(S[ie, 4]) * 10 ** 6 / abs(M_AL[ie, 1]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie, 1] = max(abs(S[ie, 4]) * 10 ** 6 / abs(M_AL[ie, 1]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1]))

    # せん断(Q)に対する断面算定
    ratio_Q, ALW_Q, Qs1, Qs2 = SA_RC4columnQratio(
        Form_y, ele_length, steelbar_y, HOOP, Fc, S, timecase, QLc,
        qup_column, RCQ)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel()
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float))
    Qs2 = np.atleast_2d(np.asarray(Qs2, dtype=float))

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # 以上で検定値の算出終了

    # RC断面の外形情報
    Form_y = np.asarray(Form_y, dtype=float).ravel()
    b = Form_y[0]; D = Form_y[1]

    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[3])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[2])  # Fzせん断補強筋数

    # 主筋情報：steelbar = [type 総本数num_steel，径D，せい方向本数nv，幅方向本数nh, SD]
    num = steelbar_y[1]; di_main = steelbar_y[2]; nv = steelbar_y[3]; nh = steelbar_y[4]; SD_main = steelbar_y[5]
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    dd = (D - 2 * dc) / (nv - 1)

    if (nh - 1) * 2 + (nv - 1) * 2 == num:
        pass
    else:
        # 原典: ERROR='配筋情報ミス'+stop
        raise ValueError(
            'RC柱の配筋情報ミス: 主筋総本数%g が 2x(せい方向%g-1)+2x(幅方向%g-1)'
            ' と一致しません' % (num, nv, nh))
    a = Area_steelbar(di_main, 1)

    text = ['RC柱の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と強軸および弱軸周りの曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    text.append('　　')

    text.append('断面符号：' + str(section_name) + '断面番号：' + _num2str(section_no)
                + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC柱サイズ　：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('　　')
    text.append('*****配筋情報*****')

    text.append('主筋配筋　：' + _num2str(num) + '-D' + _num2str(di_main))
    if SD_support == 1275:
        if di_support == 10:
            text.append('帯筋：' + _num2str(HOOP[3]) + 'x' + _num2str(HOOP[2])
                        + '-U10.7@' + _num2str(pitch))
        elif di_support == 13:
            text.append('帯筋：' + _num2str(HOOP[3]) + 'x' + _num2str(HOOP[2])
                        + '-U12.6@' + _num2str(pitch))
        elif di_support == 9:
            text.append('帯筋：' + _num2str(HOOP[3]) + 'x' + _num2str(HOOP[2])
                        + '-U9.0@' + _num2str(pitch))
    else:
        text.append('帯筋：' + _num2str(HOOP[3]) + 'x' + _num2str(HOOP[2])
                    + '-D' + _num2str(di_support) + '@' + _num2str(pitch))

    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋　：SD' + _num2str(SD_main) + '　帯筋　：SD' + _num2str(SD_support))
    text.append('コンクリート　：Fc' + _num2str(Fc[0]))
    text.append('　　')

    # 純圧縮のplot
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    if steelbar_y[0] == 1:
        dd2 = (b - 2 * dc) / (nh - 1)
    elif steelbar_y[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    min_dd = max(1.5 * di_main, 25 * 1.25)
    text.append('＊主筋のあきの検討')
    text.append('最低あき寸法（「径の1.5倍」・「粗骨材寸法(25mm)の1.25倍」・「25mm」の最大値）　：'
                + _num2str(min_dd, '%15.2f') + '[mm]')
    text.append('柱幅方向のあき　：' + _num2str(dd2 - outf_steelbar_JIS(di_main), '%15.1f')
                + '[mm]　　柱せい方向のあき[mm]　：'
                + _num2str(dd - outf_steelbar_JIS(di_main), '%15.1f') + '[mm]')

    if check_dd >= min_dd:
        text.append('主筋のあき間隔の検討　：OK')
    else:
        _warn('鉄筋あき不足→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋のあき間隔の検討　：NG')

    # その１：主筋の規定
    text.append('　　')
    text.append('＊主筋の規定（径4本以上，径D13以上）')
    text.append('使用鉄筋の径　：' + _num2str(num) + '-D' + _num2str(di_main))
    if num >= 4 and di_main >= 13:
        text.append('主筋径・本数の規定　：OK')
    else:
        _warn('[主筋規定外]主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋径・本数の規定　：NG')

    # その２：主筋断面積の規定
    text.append('　　')
    text.append('＊主筋必要最低断面積（全断面の0.8％以上）')
    text.append('主筋仕様　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　鉄筋断面積(1本)　：' + _num2str(a) + '[mm2]')
    text.append('鉄筋総断面積　：' + _num2str(num * a / 100, '%15.2f') + '[cm2]')
    if num * a / (b * D) >= 0.8 / 100:
        text.append('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　OK')
    else:
        _warn('主筋断面積不足（0.8％未満）→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        _warn('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　NG')
        text.append('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　NG')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= 100:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    elif di_support > 10 and pitch <= 200:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    else:
        _warn('帯筋間隔NG')
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　帯筋間隔NG')

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('＊せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様　：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if np.min(aw / (np.array([D, b]) * pitch)) >= 0.2 / 100:
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '%＞0.2%　：OK')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (b * pitch) * 100, '%15.2f')
                    + '%＞0.2%　：OK')
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw='
              + _num2str(np.min(aw / (np.array([D, b]) * pitch)) * 100, '%15.2f')
              + '%→HOOPピッチ' + _num2str(pitch, '%15.0f')
              + 'mm，　HOOP径：D' + _num2str(di_support, '%15.0f')
              + '，　柱幅b：' + _num2str(b, '%15.0f')
              + 'mm，　柱せいD：' + _num2str(D, '%15.0f') + 'mm')
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '%＜0.2%　：NG')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (b * pitch) * 100, '%15.2f')
                    + '%＜0.2%　：NG')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧40mm　：OK')
    else:
        # 原典: ERROR+stop (かぶり40mm未満は停止)
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜40mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）: かぶり%gmm' % cover_depth)
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増値nから決まる値による')
    text.append('主要支点間距離に対する柱径による割増係数：' + _num2str(up_slender[0], '%15.2f'))
    text.append('主要支点間距離に対する柱径の比(幅および径の順に示す)：(ho/b)'
                + _num2str(up_slender[1], '%15.1f') + '／(ho/D)'
                + _num2str(up_slender[2], '%15.1f'))

    _pos = ['i　端', '中央', 'j　端']
    for ie in range(3):
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　' + _pos[ie])
        text.append('軸力　：' + _num2str(Fxx[ie] / 1000, '%15.1f') + '　[kN]　　曲げMy　：'
                    + _num2str(My[ie] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：'
                    + _num2str(Mz[ie] / 10 ** 6, '%15.1f') + '　[kNm]')
        if timecase == 1:
            text.append('せん断力Qz　：' + _num2str(Fz[ie] / 10 ** 3, '%15.1f') + '　[kN]'
                        + '　　せん断力Qy　：' + _num2str(Fy[ie] / 10 ** 3, '%15.1f') + '　[kN]')
        else:
            text.append('長期荷重時せん断力Qz　：' + _num2str(QLc[ie, 1], '%15.1f') + '[kN]'
                        + '　　Qy　：' + _num2str(QLc[ie, 0], '%15.1f') + '[kN]')
            text.append('降伏モーメントから決定する設計用せん断力Qz：'
                        + _num2str(Qs2[0, ie], '%15.1f') + '[kN]' + '　　Qy　：'
                        + _num2str(Qs2[1, ie], '%15.1f') + '[kN]')
            text.append('せん断割増し(n=' + _num2str(qup_column, '%15.1f')
                        + ')から決まる設計用せん断力Qz：' + _num2str(Qs1[0, ie], '%15.1f')
                        + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, ie], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # NOTE: 原典292-301行の e_y/e_z 組立てはtextに出力されない (デッドコード)

    text.append('*****許容耐力・検定比*****')
    text.append('　　')
    text.append('*剛節架構負担割増(長期は1.00)' + _num2str(up_column, '%15.2f'))
    for ie in range(3):
        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　' + _pos[ie])
        text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[ie, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[ie, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]'
                    + '　せん断力Qy　：' + _num2str(ALW_Q[1], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('検定比　[NMy]　：' + _num2str(ratio_output[ie, 0], '%15.2f')
                    + '　　[NMz]　：' + _num2str(ratio_output[ie, 1], '%15.2f'))
        text.append('検定比　[Qz]　：' + _num2str(ratio_output[ie, 2], '%15.2f')
                    + '　　[Qy]　：' + _num2str(ratio_output[ie, 3], '%15.2f'))
    return text


def SA_RCSR_columnratio_text(Form, ele_length, steelbar, HOOP, Fc, stress,
                             timecase, QL, ele_no, section_no, qup_column,
                             up_column, LOAD_CASE_NAME, RCQ, section_name):
    """SA_RCSR_columnratio_text.m の逐語移植 (RC●柱の断面算定詳細)."""
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    S = np.atleast_2d(np.asarray(stress, dtype=float))
    QLc = np.atleast_2d(np.asarray(QL, dtype=float))

    # 配筋情報
    num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support4 = HOOP[2]; cover_depth4 = HOOP[3]
    a = Area_steelbar(di_main, 1)

    # 許容応力度
    f_c = ALST_RC_AIJ(Fc)
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]
    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    e = np.zeros(3)
    M_AL = np.zeros((3, 2))
    ratio_output = np.zeros((3, 4))
    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 3]) / S[ie, 0] * 1000
        N, M_AL[ie, 0], Xn = SA_RCSRcolumn_AIJ(
            e[ie], Form, steelbar, HOOP[[0, 1, 4, 5]], Fc, timecase)

        if M_AL[ie, 0] < 100:
            if S[ie, 0] > 0:  # 圧縮
                N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                        ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(N)
            else:  # 引張
                N = rfc[timecase2 - 1, 0] * num * a
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(N)
        else:
            ratio_output[ie, 0] = abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0])

    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 4]) / S[ie, 0] * 1000
        N, M_AL[ie, 1], Xn = SA_RCSRcolumn_AIJ(
            e[ie], Form, steelbar, HOOP[[0, 1, 4, 5]], Fc, timecase)

        if M_AL[ie, 1] < 100:
            if S[ie, 0] > 0:  # 圧縮
                N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                        ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(N)
            else:  # 引張
                N = rfc[timecase2 - 1, 0] * num * a
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(N)
        else:
            ratio_output[ie, 1] = abs(S[ie, 4]) * 10 ** 6 / abs(M_AL[ie, 1])

    # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
    ratio_Q, ALW_Q, Qs1, Qs2 = SA_RCSR_Qratio(
        Form, ele_length, steelbar, HOOP, Fc, S, timecase, QLc,
        qup_column, RCQ)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel()
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float))
    Qs2 = np.atleast_2d(np.asarray(Qs2, dtype=float))

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # 以上で検定値の算出終了

    # RC断面の外形情報
    D = Form[0]
    r = Form[0] / 2

    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[3])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[2])  # Fzせん断補強筋数

    # 主筋情報：steelbar
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5

    text = ['RC●柱の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と強軸および弱軸周りの曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    text.append('　　')

    text.append('断面符号：' + str(section_name) + '　断面番号：' + _num2str(section_no)
                + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC柱サイズ　：D-' + _num2str(D) + '[mm]')
    text.append('　　')
    text.append('*****配筋情報*****')
    text.append('主筋配筋　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　帯筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋　：SD' + _num2str(SD_main) + '　帯筋　：SD' + _num2str(SD_support))
    text.append('コンクリート　：Fc' + _num2str(Fc[0]))
    text.append('　　')

    if timecase >= 2:
        t_case = LOAD_CASE_NAME
    elif timecase == 1:
        t_case = LOAD_CASE_NAME
    else:
        ERROR = '長短期設定ミス'

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その０：鉄筋あきのチェック
    rr = r - dc
    if (2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 1.5 * di_main
            and 2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 20 * 1.25):
        pass
    else:
        ERROR = '配筋ミス'
    check_dd = 2 * math.pi * rr / num - outf_steelbar_JIS(di_main)
    min_dd = max(1.5 * di_main, 20 * 1.25)
    text.append('　　')
    text.append('＊主筋のあきの検討')
    text.append('最低あき寸法（「径の1.5倍」・「粗骨材寸法(20mm)の1.25倍」・「25mm」の最大値）　：'
                + _num2str(min_dd, '%15.2f') + '[mm]')
    text.append('鉄筋のあき　：' + _num2str(check_dd, '%15.1f') + '[mm]')

    if check_dd >= min_dd:
        text.append('主筋のあき間隔の検討　：OK')
    else:
        _warn('鉄筋あき不足→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋のあき間隔の検討　：NG')

    # その１：主筋の規定
    text.append('　　')
    text.append('＊主筋の規定（径4本以上，径D13以上）')
    text.append('使用鉄筋の径　：' + _num2str(num) + '-D' + _num2str(di_main))
    if num >= 4 and di_main >= 13:
        text.append('主筋径・本数の規定　：OK')
    else:
        _warn('[主筋規定外]主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋径・本数の規定　：NG')

    # その２：主筋断面積の規定
    text.append('　　')
    text.append('＊主筋必要最低断面積（全断面の0.8％以上）')
    text.append('主筋仕様　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　鉄筋断面積(1本)　：' + _num2str(a) + '[mm2]')
    text.append('鉄筋総断面積　：' + _num2str(num * a / 100, '%15.1f') + '[cm2]')
    if num * a / (D ** 2 * math.pi / 4) >= 0.8 / 100:
        text.append('主筋比　：' + _num2str(num * a / (D ** 2 * math.pi / 4) * 100, '%15.2f')
                    + '[％]　OK')
    else:
        _warn('主筋断面積不足（0.8％未満）→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        _warn('主筋比　：' + _num2str(num * a / (D ** 2 * math.pi / 4) * 100, '%15.2f')
              + '[％]　NG')
        text.append('主筋比　：' + _num2str(num * a / (D ** 2 * math.pi / 4) * 100, '%15.2f')
                    + '[％]　NG')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= 100:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    elif di_support > 10 and pitch <= 200:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    else:
        _warn('帯筋間隔NG')
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　帯筋間隔NG')

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('＊せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様　：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if np.min(aw / (D * pitch)) >= 0.2 / 100:
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '％＞0.2％　：OK')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (D * pitch) * 100, '%15.2f')
                    + '％＞0.2％　：OK')
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw='
              + _num2str(np.min(aw / (D * pitch)) * 100, '%15.2f')
              + '％→HOOPピッチ' + _num2str(pitch, '%15.0f')
              + 'mm，　HOOP径：D' + _num2str(di_support, '%15.0f')
              + '，　柱幅D：' + _num2str(D, '%15.0f') + 'mm')
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '％＜0.2％　：NG')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (D * pitch) * 100, '%15.2f')
                    + '％＜0.2％　：NG')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧40mm　：OK')
    else:
        # 原典: ERROR+stop (かぶり40mm未満は停止)
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜40mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）: かぶり%gmm' % cover_depth)
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力*****')
    # NOTE: 原典どおり (RCQ==1の表記が「部材端終局モーメント〜」となっている
    #       ラベル取り違えもそのまま再現)
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 1:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')

    _pos = ['i　端', '中央', 'j　端']
    for ie in range(3):
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　' + _pos[ie])
        text.append('軸力　：' + _num2str(Fxx[ie] / 1000, '%15.1f') + '　[kN]　　曲げMy　：'
                    + _num2str(My[ie] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：'
                    + _num2str(Mz[ie] / 10 ** 6, '%15.1f') + '　[kNm]')
        if timecase == 1:
            text.append('せん断力Qz　：' + _num2str(Fz[ie] / 10 ** 3, '%15.1f') + '　[kN]'
                        + '　　せん断力Qy　：' + _num2str(Fy[ie] / 10 ** 3, '%15.1f') + '　[kN]')
        else:
            text.append('長期荷重時せん断力Qz　：' + _num2str(QLc[ie, 1], '%15.1f') + '[kN]'
                        + '　　Qy　：' + _num2str(QLc[ie, 0], '%15.1f') + '[kN]')
            text.append('降伏モーメントから決定する設計用せん断力Qz：'
                        + _num2str(Qs2[0, ie], '%15.1f') + '[kN]' + '　　Qy　：'
                        + _num2str(Qs2[1, ie], '%15.1f') + '[kN]')
            text.append('せん断割増し(n=' + _num2str(qup_column, '%15.1f')
                        + ')から決まる設計用せん断力Qz：' + _num2str(Qs1[0, ie], '%15.1f')
                        + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, ie], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # NOTE: 原典271-280行の e_y/e_z 組立てはtextに出力されない (デッドコード)

    text.append('*****許容耐力・検定比*****')
    text.append('　　')
    text.append('*剛節架構負担割増(長期は1.00)' + _num2str(up_column, '%15.2f'))
    for ie in range(3):
        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　' + _pos[ie])
        text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[ie, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[ie, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]'
                    + '　せん断力Qy　：' + _num2str(ALW_Q[1], '%15.1f') + '　[kN]')
        text.append('検定比　[NMy]　：' + _num2str(ratio_output[ie, 0], '%15.2f')
                    + '　　[NMz]　：' + _num2str(ratio_output[ie, 1], '%15.2f'))
        text.append('検定比　[Qz]　：' + _num2str(ratio_output[ie, 2], '%15.2f')
                    + '　　[Qy]　：' + _num2str(ratio_output[ie, 3], '%15.2f'))
    return text


def SA_RC4TAPERcolumnratioHMD_text(Form_ymin, Form_y, Form_z, ele_length,
                                   steelbar_y, steelbar_z, HOOP, Fc, stress,
                                   timecase, QL, ele_no, section_no,
                                   qup_column, up_column, LOAS_CASE_NAME,
                                   RCQ, up_slender, section_name):
    """SA_RC4TAPERcolumnratioHMD_text.m の逐語移植
    RCテーパー柱の断面算定詳細のテキスト出力
    軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    """
    S = np.atleast_2d(np.asarray(stress, dtype=float))
    QLc = np.atleast_2d(np.asarray(QL, dtype=float))
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    steelbar_y = np.asarray(steelbar_y, dtype=float).ravel()
    steelbar_z = np.asarray(steelbar_z, dtype=float).ravel()
    up_slender = np.asarray(up_slender, dtype=float).ravel()
    e = np.zeros(3)
    M_AL = np.zeros((3, 2))
    ratio_output = np.zeros((3, 4))

    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 3]) / S[ie, 0] * 1000
        M_AL[ie, 0], maxN = SA_RC4_HMD(S[ie, 0], Form_y[ie], steelbar_y,
                                       HOOP, Fc, timecase)
        if e[ie] == 0:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                ratio_output[ie, 0] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 0] = max(abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie, 0] = max(abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1]))

    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            e[ie] = abs(S[ie, 4]) / S[ie, 0] * 1000
        M_AL[ie, 1], maxN = SA_RC4_HMD(S[ie, 0], Form_z[ie], steelbar_z,
                                       HOOP, Fc, timecase)
        if e[ie] == 0:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0])
            else:  # 引張
                ratio_output[ie, 1] = abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1])
        else:
            if S[ie, 0] > 0:  # 圧縮
                ratio_output[ie, 1] = max(abs(S[ie, 4]) * 10 ** 6 / abs(M_AL[ie, 1]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[0]))
            else:  # 引張
                ratio_output[ie, 1] = max(abs(S[ie, 4]) * 10 ** 6 / abs(M_AL[ie, 1]),
                                          abs(S[ie, 0]) * 10 ** 3 / abs(maxN[1]))

    # せん断(Q)に対する断面算定
    ratio_Q, ALW_Q, Qs1, Qs2 = SA_RC4columnQratio(
        Form_ymin, ele_length, steelbar_y, HOOP, Fc, S, timecase, QLc,
        qup_column, RCQ)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel()
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float))
    Qs2 = np.atleast_2d(np.asarray(Qs2, dtype=float))

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # 以上で検定値の算出終了

    # RC断面の外形情報
    b1 = Form_y[0][0]; D1 = Form_y[0][1]
    b2 = Form_y[2][0]; D2 = Form_y[2][1]

    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[3])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[2])  # Fzせん断補強筋数

    # 主筋情報：steelbar = [type 総本数num_steel，径D，せい方向本数nv，幅方向本数nh, SD]
    num = steelbar_y[1]; di_main = steelbar_y[2]; nv = steelbar_y[3]; nh = steelbar_y[4]; SD_main = steelbar_y[5]
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    dd = (D2 - 2 * dc) / (nv - 1)

    if (nh - 1) * 2 + (nv - 1) * 2 == num:
        pass
    else:
        # 原典: ERROR='配筋情報ミス'+stop
        raise ValueError(
            'RC柱の配筋情報ミス: 主筋総本数%g が 2x(せい方向%g-1)+2x(幅方向%g-1)'
            ' と一致しません' % (num, nv, nh))
    a = Area_steelbar(di_main, 1)

    text = ['RCテーパー柱の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と強軸および弱軸周りの曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    text.append('　　')

    text.append('断面符号：' + str(section_name) + '断面番号：' + _num2str(section_no)
                + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC柱サイズ1　：b1xD1-' + _num2str(b1) + '[mm] x' + _num2str(D1) + '[mm]')
    text.append('RC柱サイズ2　：b2xD2-' + _num2str(b2) + '[mm] x' + _num2str(D2) + '[mm]')
    text.append('　　')
    text.append('*****配筋情報*****')
    text.append('主筋配筋　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　帯筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋　：SD' + _num2str(SD_main) + '　帯筋　：SD' + _num2str(SD_support))
    text.append('コンクリート　：Fc' + _num2str(Fc[0]))
    text.append('　　')

    b = min(b1, b2)
    D = min(D1, D2)

    # 純圧縮のplot
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    if steelbar_y[0] == 1:
        dd2 = (b2 - 2 * dc) / (nh - 1)
    elif steelbar_y[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    min_dd = max(1.5 * di_main, 25 * 1.25)
    text.append('＊主筋のあきの検討')
    text.append('最低あき寸法（「径の1.5倍」・「粗骨材寸法(25mm)の1.25倍」・「25mm」の最大値）　：'
                + _num2str(min_dd, '%15.2f') + '[mm]')
    text.append('柱幅方向のあき　：' + _num2str(dd2 - outf_steelbar_JIS(di_main), '%15.1f')
                + '[mm]　　柱せい方向のあき[mm]　：'
                + _num2str(dd - outf_steelbar_JIS(di_main), '%15.1f') + '[mm]')

    if check_dd >= min_dd:
        text.append('主筋のあき間隔の検討　：OK')
    else:
        _warn('鉄筋あき不足→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋のあき間隔の検討　：NG')

    # その１：主筋の規定
    text.append('　　')
    text.append('＊主筋の規定（径4本以上，径D13以上）')
    text.append('使用鉄筋の径　：' + _num2str(num) + '-D' + _num2str(di_main))
    if num >= 4 and di_main >= 13:
        text.append('主筋径・本数の規定　：OK')
    else:
        _warn('[主筋規定外]主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋径・本数の規定　：NG')

    # その２：主筋断面積の規定
    text.append('　　')
    text.append('＊主筋必要最低断面積（全断面の0.8％以上）')
    text.append('主筋仕様　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　鉄筋断面積(1本)　：' + _num2str(a) + '[mm2]')
    text.append('鉄筋総断面積　：' + _num2str(num * a / 100, '%15.2f') + '[cm2]')
    if num * a / (b * D) >= 0.8 / 100:
        text.append('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　OK')
    else:
        _warn('主筋断面積不足（0.8％未満）→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        _warn('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　NG')
        text.append('主筋比　：' + _num2str(num * a / (b * D) * 100, '%15.2f') + '[%]　NG')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= 100:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    elif di_support > 10 and pitch <= 200:
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　OK')
    else:
        _warn('帯筋間隔NG')
        text.append('HOOP筋径　：D' + _num2str(di_support) + '　HOOP筋間隔　：'
                    + _num2str(pitch) + '　帯筋間隔NG')

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('＊せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様　：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if np.min(aw / (np.array([D, b]) * pitch)) >= 0.2 / 100:
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '%＞0.2%　：OK')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (b * pitch) * 100, '%15.2f')
                    + '%＞0.2%　：OK')
    else:
        _warn('せん断補強筋比不足（0.2％未満）　pw='
              + _num2str(np.min(aw / (np.array([D, b]) * pitch)) * 100, '%15.2f')
              + '%→HOOPピッチ' + _num2str(pitch, '%15.0f')
              + 'mm，　HOOP径：D' + _num2str(di_support, '%15.0f')
              + '，　柱幅b：' + _num2str(b, '%15.0f')
              + 'mm，　柱せいD：' + _num2str(D, '%15.0f') + 'mm')
        text.append('柱幅方向のせん断補強筋比　：' + _num2str(aw[0] / (D * pitch) * 100, '%15.2f')
                    + '%＜0.2%　：NG')
        text.append('柱せい方向のせん断補強筋比　：' + _num2str(aw[1] / (b * pitch) * 100, '%15.2f')
                    + '%＜0.2%　：NG')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧40mm　：OK')
    else:
        # 原典: ERROR+stop (かぶり40mm未満は停止)
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜40mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）: かぶり%gmm' % cover_depth)
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増値nから決まる値による')
    text.append('主要支点間距離に対する柱径による割増係数：' + _num2str(up_slender[0], '%15.2f'))
    text.append('主要支点間距離に対する柱径の比(幅および径の順に示す)：(ho/b)'
                + _num2str(up_slender[1], '%15.1f') + '／(ho/D)'
                + _num2str(up_slender[2], '%15.1f'))

    _pos = ['i　端', '中央', 'j　端']
    for ie in range(3):
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　' + _pos[ie])
        text.append('軸力　：' + _num2str(Fxx[ie] / 1000, '%15.1f') + '　[kN]　　曲げMy　：'
                    + _num2str(My[ie] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：'
                    + _num2str(Mz[ie] / 10 ** 6, '%15.1f') + '　[kNm]')
        if timecase == 1:
            text.append('せん断力Qz　：' + _num2str(Fz[ie] / 10 ** 3, '%15.1f') + '　[kN]'
                        + '　　せん断力Qy　：' + _num2str(Fy[ie] / 10 ** 3, '%15.1f') + '　[kN]')
        else:
            text.append('長期荷重時せん断力Qz　：' + _num2str(QLc[ie, 1], '%15.1f') + '[kN]'
                        + '　　Qy　：' + _num2str(QLc[ie, 0], '%15.1f') + '[kN]')
            text.append('降伏モーメントから決定する設計用せん断力Qz：'
                        + _num2str(Qs2[0, ie], '%15.1f') + '[kN]' + '　　Qy　：'
                        + _num2str(Qs2[1, ie], '%15.1f') + '[kN]')
            text.append('せん断割増し(n=' + _num2str(qup_column, '%15.1f')
                        + ')から決まる設計用せん断力Qz：' + _num2str(Qs1[0, ie], '%15.1f')
                        + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, ie], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # NOTE: 原典270-279行の e_y/e_z 組立てはtextに出力されない (デッドコード)

    text.append('*****許容耐力・検定比*****')
    text.append('　　')
    text.append('*剛節架構負担割増(長期は1.00)' + _num2str(up_column, '%15.2f'))
    for ie in range(3):
        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　' + _pos[ie])
        text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[ie, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[ie, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]'
                    + '　せん断力Qy　：' + _num2str(ALW_Q[1], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('検定比　[NMy]　：' + _num2str(ratio_output[ie, 0], '%15.2f')
                    + '　　[NMz]　：' + _num2str(ratio_output[ie, 1], '%15.2f'))
        text.append('検定比　[Qz]　：' + _num2str(ratio_output[ie, 2], '%15.2f')
                    + '　　[Qy]　：' + _num2str(ratio_output[ie, 3], '%15.2f'))
    return text


def rcw_input_row(sec_no, sections, mode, sd, di, bar_interval, di2,
                  bar_interval2, num_rebar=2):
    """RCW_input.m の RCWcolumns 1行を作る (ダイアログ→引数化).

    mode=1: 鉄筋径とピッチを指定して配筋 (端部補強筋は縦筋の一段太径4本相当を
            自動配置。nh = 縦筋を配置する長さ dw から算定)
    mode=2: 端部の曲げ補強筋を指定 (num_rebar=片側本数)
    sd: 2=ダブル配筋 / 1=シングル配筋
    di/bar_interval: 縦筋径・ピッチ, di2/bar_interval2: 横筋径・ピッチ

    RCWcolumns列: [断面番号, タイプ(1|2), 本数, 縦筋径, 列数sd, nh, 横筋径,
                   横筋ピッチ, sd, sd, 縦筋ピッチ] (221014國江11列)
    """
    sections = np.atleast_2d(np.asarray(sections, dtype=float))
    if mode == 1:
        sp_di = bar_table_next_diameter(di)  # 端部補強筋径
        sp_pitch = outf_steelbar_JIS(sp_di) + max(25, 1.5 * sp_di)  # 端部補強筋ピッチ
        sec_idx = find_index(sections[:, 0], sec_no)
        if sec_idx == -1:
            raise ValueError('RCW_input: 断面番号%sがsectionsにありません' % sec_no)
        # 縦筋を配置する長さ dw = D-t-sp_pitch*2 (241107 鉄筋本数:(D-t-sp_pix2)/@200-1+4)
        dw = (sections[sec_idx, 2] * 1000 - sections[sec_idx, 1] * 1000
              - sp_pitch * 2)
        if dw < bar_interval:
            nh = 4
        else:
            nh = math.floor(dw / bar_interval) + 3
        return [float(sec_no), 1.0, float(sd * nh), float(di), float(sd),
                float(nh), float(di2), float(bar_interval2), float(sd),
                float(sd), float(bar_interval)]
    elif mode == 2:
        return [float(sec_no), 2.0, float(num_rebar * 2), float(di), float(sd),
                float(num_rebar * 2 / sd), float(di2), float(bar_interval2),
                float(sd), float(sd), float(bar_interval)]
    raise ValueError('RCW_input: mode は 1(径とピッチ指定) か 2(端部補強筋指定)')


# ===========================================================================
# RC_ratio_analysis.m (鉄筋コンクリート部材の断面検定) の逐語移植
# ===========================================================================

def RC_ratio_analysis(sectionsize, ele_length, stress, timecase, Fc, ele_no,
                      maxratios, maxratios_text, section_no,
                      RCcolumns, RCbeams, RCWcolumns, QL, qup_beam, qup_wall,
                      column_cover, beam_cover, wall_cover, RCbeam_secNO,
                      wall_r, node, element, load_direction, load_no,
                      ij_select, ij_reverse, LOAD_CASE_NAME, w_l, L_43, RCQ,
                      buck_length, pick_section_name, walldesign_index,
                      method_rcw):
    """RC_ratio_analysis.m の逐語移植 (NKD物件で通る経路のみ).

    移植済み経路:
      - RC梁 (中実角断面, sectionsize末尾-1==2000)
      - RC柱 (中実角断面2000, *REBAR-COLUMN/UI柱入力。主筋は等間隔配筋
        SA_RC4_HMD。原典は section_no==4444||5555 恒真バグで常によせ筋版
        だったがユーザー承認により修正 2026-07-11: RC4_YOSE_FIX)
      - RC円形柱 (中実丸断面3000, SA_RCSRcolumn_AIJ。弱軸のe!=0で
        軸力項が無視される原典1062行のバグはユーザー承認により修正
        2026-07-12: RCSR_NMZ_FIX)
      - RC壁柱 (中実角断面, walldesign_index=1 耐力壁付ラーメン / 2 壁式)
      - RC TAPERED梁・柱 (中実角12000。i端/中央/j端の断面で各3回算定。
        柱はSA_RC4_HMD直呼び=よせ筋恒真バグはTAPER分岐に存在しない)
    未移植経路 (到達時 ValueError/NotImplementedError):
      - 長方形・円形・TAPER以外の断面形状

    返り値: (ratio_output(3x4), maxratios(1x2), maxratios_text)
    MATLAB同様、梁は列1=曲げ・列3=せん断、壁柱は列1=NMy 列2=NMz
    列3=Qz 列4=Qy (中央行は壁ではゼロ)。
    """
    # 規定チェックNG注記用の断面ラベル (要素番号は含めず断面単位で重複除去)
    _WARN_CTX['label'] = '断面%d %s' % (int(section_no),
                                        str(pick_section_name).strip())
    stress = np.array(np.atleast_2d(stress), dtype=float, copy=True)
    QL = np.array(np.atleast_2d(QL), dtype=float, copy=True)
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    maxratios = np.array(np.asarray(maxratios, dtype=float).ravel(), copy=True)

    stress[:, 0] = stress[:, 0] * -1
    QL[:, 0] = QL[:, 0] * -1
    sectionsize = sectionsize * 1000
    ratio_output = np.zeros((3, 4))

    if section_no > 2999:
        walldesign_index = 2
    elif section_no < 2999:
        walldesign_index = 1

    # RCcolumns/RCbeams/RCWcolumnに断面番号があるかで柱断面か梁断面かを判定する
    # (MATLAB find_indexは1-based/見つからない=0。+1して同じ値にする)
    if RCcolumns is None or len(RCcolumns) == 0:
        column_judge = 0
    else:
        column_judge = find_index(np.atleast_2d(np.asarray(RCcolumns, dtype=float))[:, 0], section_no) + 1
    if RCWcolumns is None or np.asarray(RCWcolumns).size == 0:
        wall_judge = 0
    else:
        wall_judge = find_index(np.atleast_2d(np.asarray(RCWcolumns, dtype=float))[:, 0], section_no) + 1
    if RCbeam_secNO is None or np.asarray(RCbeam_secNO).size == 0:
        beam_judge = 0
    else:
        beam_judge = find_index(np.asarray(RCbeam_secNO, dtype=float).ravel(), section_no) + 1

    # 梁として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if column_judge == 0 and wall_judge == 0 and beam_judge != 0:
        QL = QL[:, 1:3]
        if L_43 is None or np.asarray(L_43).size == 0:
            L43 = 1.0
        elif find_index(np.atleast_2d(np.asarray(L_43, dtype=float))[:, 0], section_no) + 1 > 0:
            L43 = 4.0 / 3
        else:
            L43 = 1.0

        if sectionsize[len(sectionsize) - 2] == 2000:  # 中実角断面の検定
            Form = np.array([sectionsize[1], sectionsize[0]])
            Fc = np.array([Fc[1], 0.0])  # 普通コンとする
            # MATLABは値渡しのためRCbeamsセルの書換えは呼出し側へ波及しない。
            # Python側は共有参照になるので複製してから扱う。
            rcb2 = np.array(RCbeams[beam_judge - 1][1], dtype=float, copy=True)
            # HOOP:あばら筋径D，ピッチ，本数，SD295，かぶり40mm
            if rcb2[0, 14] == 51:  # ウルボンU13
                SD = 1275
                rcb2[0, 14] = 13
            elif rcb2[0, 14] == 41:  # ウルボンU10
                SD = 1275
                rcb2[0, 14] = 10
            elif rcb2[0, 14] == 38:  # ウルボンU9
                SD = 1275
                rcb2[0, 14] = 9
            else:
                if rcb2[0, 14] > 28:
                    SD = 390
                elif rcb2[0, 14] > 18:
                    SD = 345
                else:
                    SD = 295
            HOOP = np.concatenate([rcb2[0, 14:17], [SD, beam_cover]])
            # 上主筋の設定
            if rcb2[0, 4] > 28:
                SD = 390
            elif rcb2[0, 4] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)

            def _rows(rows, cols):
                """rcb2の行インデックス(1-based)列を並べて3x7を作る."""
                return np.vstack([rcb2[r - 1, cols[0] - 1:cols[1]] for r in rows])

            # steelbar_u:段数(up)，一段目本数(up)，二段目本数(up)，径D(up)，SD
            if ij_select == 0:
                steelbar_u = np.hstack([rcb2[:, 0:7], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 7)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 7)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 7)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([1, 1, 2], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([1, 2, 2], (1, 7)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([2, 3, 3], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 3], (1, 7)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 7)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 7)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 7)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([3, 3, 2], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([3, 2, 2], (1, 7)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([2, 1, 1], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 1], (1, 7)), SD3])

            # 下主筋の設定
            if rcb2[0, 11] > 28:
                SD = 390
            elif rcb2[0, 11] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)
            # steelbar_d:段数(down)，一段目本数(down)，二段目本数(down)，径D(down)，SD
            if ij_select == 0:
                steelbar_d = np.hstack([rcb2[:, 7:14], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (8, 14)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (8, 14)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (8, 14)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([1, 1, 2], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([1, 2, 2], (8, 14)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([2, 3, 3], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 3], (8, 14)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (8, 14)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (8, 14)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (8, 14)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([3, 3, 2], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([3, 2, 2], (8, 14)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([2, 1, 1], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 1], (8, 14)), SD3])

            # 強軸周りの許容曲げモーメントに対して検定を行う
            RC_ALW_M, up = sub_RC4beam_ALWM(Form, steelbar_u, steelbar_d,
                                            HOOP, Fc, timecase, L43)
            # 曲げモーメントの検定
            for ie in range(3):
                if stress[ie, 3] >= 0:
                    ratio_output[ie, 0] = stress[ie, 3] * up / RC_ALW_M[ie, 0]
                else:
                    ratio_output[ie, 0] = stress[ie, 3] * up / RC_ALW_M[ie, 1]
            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            q6 = SA_RCbeamQratio(Form, ele_length, steelbar_u, steelbar_d,
                                 HOOP, Fc, stress, timecase, QL[:, 1],
                                 qup_beam, RCQ)
            ratio_output[:, 2] = np.asarray(q6[0], dtype=float).ravel()
            ALW_Q = q6[1]

            # 最大検定値の指定
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RCbeamratio_text(
                    Form, ele_length, steelbar_u, steelbar_d, HOOP, Fc,
                    stress, timecase, QL[:, 1], ele_no, section_no, RC_ALW_M,
                    qup_beam, up, LOAD_CASE_NAME, 0, RCQ, pick_section_name)

        elif sectionsize[len(sectionsize) - 2] == 12000:  # 中実角断面<TAPEREED>の検定
            Form_1 = np.array([sectionsize[1], sectionsize[0]])
            Form_3 = np.array([sectionsize[3], sectionsize[2]])
            Form_2 = Form_1 * 0.5 + Form_3 * 0.5
            Fc = np.array([Fc[1], 0.0])  # 普通コンとする
            # MATLABは値渡しのためRCbeamsセルの書換えは呼出し側へ波及しない。
            # Python側は共有参照になるので複製してから扱う。
            rcb2 = np.array(RCbeams[beam_judge - 1][1], dtype=float, copy=True)
            # HOOP:あばら筋径D，ピッチ，本数，SD295，かぶり40mm
            if rcb2[0, 14] == 51:  # ウルボンU13
                SD = 1275
                rcb2[0, 14] = 13
            elif rcb2[0, 14] == 41:  # ウルボンU10
                SD = 1275
                rcb2[0, 14] = 10
            elif rcb2[0, 14] == 38:  # ウルボンU9
                SD = 1275
                rcb2[0, 14] = 9
            else:
                if rcb2[0, 14] > 28:
                    SD = 390
                elif rcb2[0, 14] > 18:
                    SD = 345
                else:
                    SD = 295
            HOOP = np.concatenate([rcb2[0, 14:17], [SD, beam_cover]])
            # 上主筋の設定
            if rcb2[0, 4] > 28:
                SD = 390
            elif rcb2[0, 4] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)

            def _rows(rows, cols):
                """rcb2の行インデックス(1-based)列を並べて3x7を作る."""
                return np.vstack([rcb2[r - 1, cols[0] - 1:cols[1]] for r in rows])

            # steelbar_u:段数(up)，一段目本数(up)，二段目本数(up)，径D(up)，SD
            if ij_select == 0:
                steelbar_u = np.hstack([rcb2[:, 0:7], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 7)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 7)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 7)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([1, 1, 2], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([1, 2, 2], (1, 7)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([2, 3, 3], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 3], (1, 7)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 7)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 7)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 7)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([3, 3, 2], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([3, 2, 2], (1, 7)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 1:4]) >= np.sum(rcb2[1, 1:4]):
                            steelbar_u = np.hstack([_rows([2, 1, 1], (1, 7)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 1], (1, 7)), SD3])

            # 下主筋の設定
            if rcb2[0, 11] > 28:
                SD = 390
            elif rcb2[0, 11] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)
            # steelbar_d:段数(down)，一段目本数(down)，二段目本数(down)，径D(down)，SD
            if ij_select == 0:
                steelbar_d = np.hstack([rcb2[:, 7:14], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (8, 14)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (8, 14)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (8, 14)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([1, 1, 2], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([1, 2, 2], (8, 14)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([2, 3, 3], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 3], (8, 14)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (8, 14)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (8, 14)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (8, 14)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([3, 3, 2], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([3, 2, 2], (8, 14)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 8:11]) >= np.sum(rcb2[1, 8:11]):
                            steelbar_d = np.hstack([_rows([2, 1, 1], (8, 14)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 1], (8, 14)), SD3])

            # (原典212-356行: 配筋選択は中実角2000と同一文)
            # 強軸周りの許容曲げモーメントに対して検定を行う
            RC_ALW_Msub = np.zeros((3, 3, 2))
            up = np.zeros(3)
            RC_ALW_Msub[0], up[0] = sub_RC4beam_ALWM(
                Form_1, steelbar_u, steelbar_d, HOOP, Fc, timecase, L43)
            RC_ALW_Msub[1], up[1] = sub_RC4beam_ALWM(
                Form_2, steelbar_u, steelbar_d, HOOP, Fc, timecase, L43)
            RC_ALW_Msub[2], up[2] = sub_RC4beam_ALWM(
                Form_3, steelbar_u, steelbar_d, HOOP, Fc, timecase, L43)

            RC_ALW_M = np.vstack([RC_ALW_Msub[0, 0, :],
                                  RC_ALW_Msub[1, 1, :],
                                  RC_ALW_Msub[2, 2, :]])

            # 曲げモーメントの検定
            for ie in range(3):
                if stress[ie, 3] >= 0:
                    ratio_output[ie, 0] = stress[ie, 3] * up[ie] / RC_ALW_M[ie, 0]
                else:
                    ratio_output[ie, 0] = stress[ie, 3] * up[ie] / RC_ALW_M[ie, 1]

            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            ratio_output_Q = np.zeros((3, 3))
            q1 = SA_RCbeamQratio(Form_1, ele_length, steelbar_u, steelbar_d,
                                 HOOP, Fc, stress, timecase, QL[:, 1],
                                 qup_beam, RCQ)
            ratio_output_Q[:, 0] = np.asarray(q1[0], dtype=float).ravel()
            q2 = SA_RCbeamQratio(Form_2, ele_length, steelbar_u, steelbar_d,
                                 HOOP, Fc, stress, timecase, QL[:, 1],
                                 qup_beam, RCQ)
            ratio_output_Q[:, 1] = np.asarray(q2[0], dtype=float).ravel()
            q3 = SA_RCbeamQratio(Form_3, ele_length, steelbar_u, steelbar_d,
                                 HOOP, Fc, stress, timecase, QL[:, 1],
                                 qup_beam, RCQ)
            ratio_output_Q[:, 2] = np.asarray(q3[0], dtype=float).ravel()
            ALW_Q = q3[1]  # MATLABは3回の呼出しで上書き (最後=Form_3)

            ratio_output[:, 2] = [ratio_output_Q[0, 0], ratio_output_Q[1, 1],
                                  ratio_output_Q[2, 2]]

            # 最大検定値の指定
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RCbeamratio_text(
                    np.vstack([Form_1, Form_2, Form_3]), ele_length,
                    steelbar_u, steelbar_d, HOOP, Fc, stress, timecase,
                    QL[:, 1], ele_no, section_no, RC_ALW_M, qup_beam, up,
                    LOAD_CASE_NAME, 1, RCQ, pick_section_name)
        else:
            raise ValueError(
                'ERROR:RC梁の断面形状設定ミス＜長方形でない梁＞ (断面番号%d)'
                % int(section_no))

    # 柱として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    elif column_judge != 0 and wall_judge == 0 and beam_judge == 0:
        # NOTE: 原典390-560行の剛節架構割増(Q_xy)の幾何計算は、割増式が
        #       コメントアウト済みで up_column=1.0 固定 (結果に影響しない)
        #       のため省略する
        up_column = 1.0

        if sectionsize[len(sectionsize) - 2] == 2000:  # 中実角断面の検定
            # 剛節架構割増
            if up_column > qup_beam:
                qup_beam_text = qup_beam
                qup_beam = 1.0
            else:
                qup_beam_text = qup_beam
                qup_beam = qup_beam / up_column

            stress[:, 1:5] = (stress[:, 1:5] - QL[:, 1:5]) * up_column + QL[:, 1:5]

            # 柱の細長比割増
            ly = buck_length[0] * 1000 / sectionsize[0]  # ly
            lz = buck_length[0] * 1000 / sectionsize[1]  # ly
            up_slender = get_RC_slender(max(ly, lz))
            stress = stress * up_slender

            up_slender = [up_slender, lz, ly]

            QL = QL[:, 1:3]
            Fc = np.array([Fc[1], 0.0])  # 普通コンとする

            # MATLABは値渡しのためRCcolumns行の書換えはローカル (Pythonはコピーで再現)
            cj = column_judge - 1  # 0-based行
            RCcol = np.array(
                np.atleast_2d(np.asarray(RCcolumns, dtype=float))[cj, :],
                copy=True)

            # HOOP:あばら筋径D，ピッチ，本数，SD295，かぶり40mm
            if RCcol[6] == 51:  # ウルボンU13
                SD = 1275
                RCcol[6] = 13
            elif RCcol[6] == 41:  # ウルボンU10
                SD = 1275
                RCcol[6] = 10
            elif RCcol[6] == 38:  # ウルボンU9
                SD = 1275
                RCcol[6] = 9
            else:
                if RCcol[6] > 28:
                    SD = 390
                elif RCcol[6] > 18:
                    SD = 345
                else:
                    SD = 295

            HOOP = np.concatenate([RCcol[6:10], [SD, column_cover]])

            Form_y = np.array([sectionsize[1], sectionsize[0]])
            if RCcol[3] > 28:
                SD = 390
            elif RCcol[3] > 18:
                SD = 345
            else:
                SD = 295
            steelbar_y = np.concatenate([RCcol[1:6], [SD, 0]])
            # 軸力と強軸周りの許容曲げモーメントに対して検定を行う

            e = np.zeros(3)
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 3]) / stress[ie, 0] * 1000
                # 原典は恒真バグ(section_no==4444||5555)で常によせ筋版
                # だったが、等間隔配筋 SA_RC4_HMD を使用する
                # (ユーザー承認による原典バグ修正 2026-07-11: RC4_YOSE_FIX)
                if steelbar_y[3] > 3:
                    RC4_YOSE_FIX['hit'] = True
                M, maxN = SA_RC4_HMD(stress[ie, 0], Form_y, steelbar_y,
                                     HOOP, Fc, timecase)

                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                    else:  # 引張
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 0] = max(
                            abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                    else:  # 引張
                        ratio_output[ie, 0] = max(
                            abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

            # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
            Form_z = np.array([sectionsize[0], sectionsize[1]])
            steelbar_z = np.concatenate([RCcol[1:4], [RCcol[5], RCcol[4]],
                                         [SD, 0]])

            # 許容NM曲線を呼び出し
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 4]) / stress[ie, 0] * 1000

                if steelbar_z[3] > 3:
                    RC4_YOSE_FIX['hit'] = True
                M, maxN = SA_RC4_HMD(stress[ie, 0], Form_z, steelbar_z,
                                     HOOP, Fc, timecase)

                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                    else:  # 引張
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 1] = max(
                            abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                    else:  # 引張
                        ratio_output[ie, 1] = max(
                            abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            ratio_Q, ALW_Q, Qs1, Qs2 = SA_RC4columnQratio(
                Form_y, ele_length, steelbar_y, HOOP, Fc, stress, timecase,
                QL, qup_beam, RCQ)
            ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)

            # 最大検定値の保存
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RC4columnratioHMD_text(
                    Form_y, Form_z, ele_length, steelbar_y, steelbar_z, HOOP,
                    Fc, stress, timecase, QL, ele_no, section_no,
                    qup_beam_text, up_column, LOAD_CASE_NAME, RCQ,
                    up_slender, pick_section_name)

        elif sectionsize[len(sectionsize) - 2] == 12000:  # 中実角断面<TAPEREED>の検定
            # 剛節架構割増
            if up_column > qup_beam:
                qup_beam_text = qup_beam
                qup_beam = 1.0
            else:
                qup_beam_text = qup_beam
                qup_beam = qup_beam / up_column

            stress[:, 1:5] = (stress[:, 1:5] - QL[:, 1:5]) * up_column + QL[:, 1:5]

            Form_y = [None, None, None]  # MATLAB cell Form_y{:,ie}
            Form_y[0] = np.array([sectionsize[1], sectionsize[0]])
            Form_y[2] = np.array([sectionsize[3], sectionsize[2]])
            Form_y[1] = Form_y[0] * 0.5 + Form_y[2] * 0.5
            Form_ymin = np.array([min(sectionsize[1], sectionsize[3]),
                                  min(sectionsize[2], sectionsize[0])])

            # 柱の細長比割増
            ly = buck_length[0] * 1000 / min(sectionsize[0], sectionsize[2])  # ly
            lz = buck_length[0] * 1000 / min(sectionsize[1], sectionsize[3])  # ly
            up_slender = get_RC_slender(max(ly, lz))
            stress = stress * up_slender

            up_slender = [up_slender, lz, ly]

            QL = QL[:, 1:3]
            Fc = np.array([Fc[1], 0.0])  # 普通コンとする
            cj = column_judge - 1  # 0-based行
            RCcol = np.array(
                np.atleast_2d(np.asarray(RCcolumns, dtype=float))[cj, :],
                copy=True)
            # NOTE: 原典853-860行はウルボン(51/41/38)の読替なし (2000分岐と異なる)
            if RCcol[6] > 28:
                SD = 390
            elif RCcol[6] > 18:
                SD = 345
            else:
                SD = 295
            HOOP = np.concatenate([RCcol[6:10], [SD, column_cover]])  # SD295と仮定

            if RCcol[3] > 28:
                SD = 390
            elif RCcol[3] > 18:
                SD = 345
            else:
                SD = 295
            steelbar_y = np.concatenate([RCcol[1:6], [SD, 0]])
            # 軸力と強軸周りの許容曲げモーメントに対して検定を行う

            e = np.zeros(3)
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 3]) / stress[ie, 0] * 1000
                # NOTE: 原典880行はTAPER分岐では SA_RC4_HMD 直呼び (よせ筋の
                #       恒真バグは2000分岐のみ)
                M, maxN = SA_RC4_HMD(stress[ie, 0], Form_y[ie], steelbar_y,
                                     HOOP, Fc, timecase)
                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                    else:  # 引張
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 0] = max(
                            abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                    else:  # 引張
                        ratio_output[ie, 0] = max(
                            abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

            # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
            Form_z = [None, None, None]
            Form_z[0] = np.array([sectionsize[0], sectionsize[1]])
            Form_z[2] = np.array([sectionsize[2], sectionsize[3]])
            Form_z[1] = Form_z[0] * 0.5 + Form_z[2] * 0.5

            steelbar_z = np.concatenate([RCcol[1:4], [RCcol[5], RCcol[4]],
                                         [SD, 0]])

            # 許容NM曲線を呼び出し
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 4]) / stress[ie, 0] * 1000
                M, maxN = SA_RC4_HMD(stress[ie, 0], Form_z[ie], steelbar_z,
                                     HOOP, Fc, timecase)
                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                    else:  # 引張
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        ratio_output[ie, 1] = max(
                            abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                    else:  # 引張
                        ratio_output[ie, 1] = max(
                            abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                            abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            ratio_Q, ALW_Q, Qs1, Qs2 = SA_RC4columnQratio(
                Form_ymin, ele_length, steelbar_y, HOOP, Fc, stress,
                timecase, QL, qup_beam, RCQ)
            ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)

            # 最大検定値の保存
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RC4TAPERcolumnratioHMD_text(
                    Form_ymin, Form_y, Form_z, ele_length, steelbar_y,
                    steelbar_z, HOOP, Fc, stress, timecase, QL, ele_no,
                    section_no, qup_beam_text, up_column, LOAD_CASE_NAME,
                    RCQ, up_slender, pick_section_name)
        elif sectionsize[len(sectionsize) - 2] == 3000:  # 中実丸断面の検定
            stress = (stress - QL) * up_column + QL
            QL = QL[:, 1:3]
            Fc = np.array([Fc[1], 0.0])  # 普通コンとする
            cj = column_judge - 1  # 0-based行
            RCcol = np.array(
                np.atleast_2d(np.asarray(RCcolumns, dtype=float))[cj, :],
                copy=True)
            if RCcol[6] > 28:
                SD = 390
            elif RCcol[6] > 18:
                SD = 345
            else:
                SD = 295

            HOOP = np.concatenate([RCcol[6:10], [SD, column_cover]])  # SD295と仮定
            Form = np.array([sectionsize[0]])

            if RCcol[3] > 28:
                SD = 390
            elif RCcol[3] > 18:
                SD = 345
            else:
                SD = 295
            steelbar = np.array([RCcol[2], RCcol[3], SD])
            # 軸力と強軸周りの許容曲げモーメントに対して検定を行う

            # 軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
            # 配筋情報
            num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
            a = Area_steelbar(di_main, 1)

            # 許容応力度
            f_c = ALST_RC_AIJ(Fc)
            rfc = ALST_steelbar_KJ([di_main, SD_main])

            # ヤング係数比
            n = E_RC_AIJ(Fc)
            n = n[1]

            if timecase >= 10:
                timecase2 = 2
            elif timecase == 1:
                timecase2 = 1
            else:
                ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

            e = np.zeros(3)
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 3]) / stress[ie, 0] * 1000
                N, M, Xn = SA_RCSRcolumn_AIJ(e[ie], Form, steelbar,
                                             HOOP[[0, 1, 4, 5]], Fc, timecase)
                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                                ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
                    else:  # 引張
                        N = rfc[timecase2 - 1, 0] * num * a
                        ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                                ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                        ratio_output[ie, 0] = max(abs(stress[ie, 0]) * 10 ** 3 / abs(N),
                                                  abs(stress[ie, 3]) * 10 ** 6 / abs(M))
                    else:  # 引張
                        N = rfc[timecase2 - 1, 0] * num * a
                        ratio_output[ie, 0] = max(abs(stress[ie, 0]) * 10 ** 3 / abs(N),
                                                  abs(stress[ie, 3]) * 10 ** 6 / abs(M))

            # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
            for ie in range(3):
                if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie] = 10 ** 6
                else:
                    e[ie] = abs(stress[ie, 4]) / stress[ie, 0] * 1000
                N, M, Xn = SA_RCSRcolumn_AIJ(e[ie], Form, steelbar,
                                             HOOP[[0, 1, 4, 5]], Fc, timecase)
                if e[ie] == 0:
                    if stress[ie, 0] > 0:  # 圧縮
                        N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                                ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
                    else:  # 引張
                        N = rfc[timecase2 - 1, 0] * num * a
                        ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
                else:
                    if stress[ie, 0] > 0:  # 圧縮
                        N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                                ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                        ratio_output[ie, 1] = max(abs(stress[ie, 0]) * 10 ** 3 / abs(N),
                                                  abs(stress[ie, 4]) * 10 ** 6 / abs(M))
                    else:  # 引張
                        N = rfc[timecase2 - 1, 0] * num * a
                        ratio_output[ie, 1] = max(abs(stress[ie, 0]) * 10 ** 3 / abs(N),
                                                  abs(stress[ie, 4]) * 10 ** 6 / abs(M))

                    # 原典1062行は直前の max(曲げ項, 軸力項) を曲げ項のみで
                    # 上書きし軸力項が無視される(非安全側)。ユーザー承認に
                    # よる原典バグ修正 2026-07-12 (RCSR_NMZ_FIX): maxを採用
                    if ratio_output[ie, 1] != abs(stress[ie, 4]) * 10 ** 6 / abs(M):
                        RCSR_NMZ_FIX['hit'] = True

            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            ratio_Q, ALW_Q, Qs1, Qs2 = SA_RCSR_Qratio(
                Form, ele_length, steelbar, HOOP, Fc, stress, timecase,
                QL, qup_beam, RCQ)
            ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)

            # 最大検定値の保存
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RCSR_columnratio_text(
                    Form, ele_length, steelbar, HOOP, Fc, stress, timecase,
                    QL, ele_no, section_no, qup_beam, up_column,
                    LOAD_CASE_NAME, RCQ, pick_section_name)
        else:
            raise ValueError(
                'ERROR:RC柱の断面形状設定ミス＜長方形でも円でもない柱＞ '
                '(断面番号%d)' % int(section_no))

    # 壁として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    elif column_judge == 0 and wall_judge != 0 and beam_judge == 0:
        RCWcolumns = np.atleast_2d(np.asarray(RCWcolumns, dtype=float))
        QL = QL[:, 1:3]
        if wall_r is None or np.asarray(wall_r).size == 0:
            reduction = 1.0
        else:
            wall_r = np.atleast_2d(np.asarray(wall_r, dtype=float))
            if find_index(wall_r[:, 0], section_no) + 1 > 0:
                reduction = wall_r[find_index(wall_r[:, 0], section_no), 1]
            else:
                reduction = 1.0

        WL_ef = np.zeros(6)
        if w_l is None or np.asarray(w_l).size == 0:
            WL_ef[0:6] = sectionsize[1]
        else:
            w_l = np.atleast_2d(np.asarray(w_l, dtype=float))
            if find_index(w_l[:, 0], section_no) + 1 > 0:
                wli = find_index(w_l[:, 0], section_no)
                WL_ef[0:2] = w_l[wli, 1:3] * 1000
                WL_ef[2:4] = sectionsize[1]
                WL_ef[4:6] = w_l[wli, 3:5] * 1000
            else:
                WL_ef[0:6] = sectionsize[1]

        if sectionsize[len(sectionsize) - 2] == 2000:  # 中実角断面の検定
            wj = wall_judge - 1  # 0-based行

            if walldesign_index == 1:  # 耐力壁付きラーメンの壁
                Fc = np.array([Fc[1], 0.0])  # 普通コンとする
                if RCWcolumns[wj, 6] > 28:
                    SD = 390
                elif RCWcolumns[wj, 6] > 18:
                    SD = 345
                else:
                    SD = 295
                HOOP = np.concatenate([RCWcolumns[wj, 6:10], [SD, wall_cover]])

                if RCWcolumns[wj, 3] > 28:
                    SD = 390
                elif RCWcolumns[wj, 3] > 18:
                    SD = 345
                else:
                    SD = 295
                steelbar_y = np.concatenate([RCWcolumns[wj, 1:6], [SD, 0]])
                # 軸力と強軸周りの許容曲げモーメントに対して検定を行う
                e = np.zeros(3)
                for ie in range(3):
                    Form_y = np.array([WL_ef[2 * ie], sectionsize[0]])
                    if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                        e[ie] = 10 ** 6
                    else:
                        e[ie] = abs(stress[ie, 3]) / stress[ie, 0] * 1000
                    M, maxN = SA_RW4_HMD(stress[ie, 0], Form_y, steelbar_y,
                                         HOOP, Fc, timecase)
                    if e[ie] == 0:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                        else:  # 引張
                            ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                    else:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 0] = max(abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                        else:  # 引張
                            ratio_output[ie, 0] = max(abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

                # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
                steelbar_z = np.concatenate([RCWcolumns[wj, 1:4],
                                             [RCWcolumns[wj, 5], RCWcolumns[wj, 4]],
                                             [SD, 0]])
                # 許容NM曲線を呼び出し
                for ie in range(3):
                    Form_z = np.array([sectionsize[0], WL_ef[2 * ie]])
                    if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                        e[ie] = 10 ** 6
                    else:
                        e[ie] = abs(stress[ie, 4]) / stress[ie, 0] * 1000
                    A_HOOP = np.concatenate([HOOP[0:5], [sectionsize[0] / 2]])
                    M, maxN = SA_RW4_HMD(stress[ie, 0], Form_z, steelbar_z,
                                         A_HOOP, Fc, timecase)
                    if e[ie] == 0:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                        else:  # 引張
                            ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                    else:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 1] = max(abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                        else:  # 引張
                            ratio_output[ie, 1] = max(abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

                v_pitch = RCWcolumns[wj, 10]  # 221014國江追記v_pithは縦筋ピッチ
                v_num = RCWcolumns[wj, 4]  # 221014國江追記v_numは縦筋本数(ダブルかシングルか）

                # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
                Form_Q = np.array([[WL_ef[1], sectionsize[0]],
                                   [WL_ef[3], sectionsize[0]],
                                   [WL_ef[5], sectionsize[0]]])
                rq, ALW_Q, Qs1 = SA_RW4Qratio(Form_Q, ele_length, steelbar_y,
                                              HOOP, Fc, stress, timecase, QL,
                                              qup_wall, RCQ)
                ratio_output[:, 2:4] = np.atleast_2d(rq)
                ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
                ratio_output[1, :] = 0
                # 最大検定値の保存
                if np.max(ratio_output) > maxratios[1]:
                    maxratios[0] = ele_no
                    maxratios[1] = np.max(ratio_output)
                    maxratios_text = SA_RW4_HMD_text(
                        ele_length, steelbar_y, steelbar_z, HOOP, Fc, stress,
                        timecase, QL, ele_no, section_no, qup_wall, reduction,
                        LOAD_CASE_NAME, Form_Q, WL_ef, sectionsize, RCQ,
                        pick_section_name, walldesign_index, v_pitch, v_num)

            elif walldesign_index == 2:  # 壁式の壁
                Fc = np.array([Fc[1], 0.0])  # 普通コンとする

                # 軸力と面外の許容曲げモーメントに対して検定を行う
                # 横筋の材料指定
                if RCWcolumns[wj, 6] > 28:
                    SD = 390
                elif RCWcolumns[wj, 6] > 18:
                    SD = 345
                else:
                    SD = 295
                HOOP = np.concatenate([RCWcolumns[wj, 6:10], [SD, wall_cover]])

                # 縦筋の材料指定
                if RCWcolumns[wj, 3] > 28:
                    SD = 390
                elif RCWcolumns[wj, 3] > 18:
                    SD = 345
                else:
                    SD = 295

                steelbar_y = np.concatenate([RCWcolumns[wj, 1:6], [SD, 0]])

                e = np.zeros(3)
                for ie in range(3):
                    Form_y = np.array([WL_ef[2 * ie], sectionsize[0]])
                    if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                        e[ie] = 10 ** 6
                    else:
                        e[ie] = abs(stress[ie, 3]) / stress[ie, 0] * 1000
                    M, maxN = SA_RW4_HMD(stress[ie, 0], Form_y, steelbar_y,
                                         HOOP, Fc, timecase)
                    if e[ie] == 0:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                        else:  # 引張
                            ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                    else:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 0] = max(abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                        else:  # 引張
                            ratio_output[ie, 0] = max(abs(stress[ie, 3]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

                # 軸力と面内の許容曲げモーメントに対して検定を行う
                # (A_RCWcolumns の組立てはMATLAB原典では以後未使用のため省略)

                # SD:縦筋材料
                if RCWcolumns[wj, 3] > 28:
                    SD = 390
                elif RCWcolumns[wj, 3] > 18:
                    SD = 345
                else:
                    SD = 295

                # SD2 端部補強筋の材料
                sp_di = bar_table_next_diameter(RCWcolumns[wj, 3])  # 端部補強筋径

                # 箱根対応
                if section_no == 3333:
                    sp_di = 29
                    print('sp_di = %d' % sp_di)  # MATLAB: セミコロン無し表示

                sp_pitch = outf_steelbar_JIS(sp_di) + max(25, 1.5 * sp_di)  # 端部補強筋ピッチ

                if sp_di > 28:
                    SD2 = 390
                elif sp_di > 18:
                    SD2 = 345
                else:
                    SD2 = 295

                v_pitch = RCWcolumns[wj, 10]  # 221014國江追記v_pithは縦筋ピッチ
                v_num = RCWcolumns[wj, 4]  # 221014國江追記v_numは縦筋本数(ダブルかシングルか）

                # steelbar_z : 1.端部の有無 2.端部+縦筋本数 3.縦筋径 4.端部+縦筋列数
                # 5.シングルorダブル 6.縦筋材料 7.端部径 8.端部本数 9.端部ピッチ 10.端部材料
                # method_rcw:壁の検定方法.
                if method_rcw == 1:  # 1.端部のみ有効
                    steelbar_z = np.concatenate([
                        [10], RCWcolumns[wj, 2:4], [RCWcolumns[wj, 5]],
                        [RCWcolumns[wj, 4]], [SD, sp_di,
                        4 * RCWcolumns[wj, 4], sp_pitch, SD2]])
                elif method_rcw == 2:  # 2.縦筋有効 %端部補強筋径と材料を縦筋と同じにしている
                    steelbar_z = np.concatenate([
                        [20], RCWcolumns[wj, 2:4], [RCWcolumns[wj, 5]],
                        [RCWcolumns[wj, 4]], [SD, RCWcolumns[wj, 3],
                        4 * RCWcolumns[wj, 4], sp_pitch, SD]])
                elif method_rcw == 3:  # 3.全鉄筋有効(補強+縦)
                    steelbar_z = np.concatenate([
                        [30], RCWcolumns[wj, 2:4], [RCWcolumns[wj, 5]],
                        [RCWcolumns[wj, 4]], [SD, sp_di,
                        4 * RCWcolumns[wj, 4], sp_pitch, SD2]])
                else:
                    raise ValueError('method_rcw は 1/2/3 を指定してください')

                # 許容NM曲線を呼び出し
                for ie in range(3):
                    Form_z = np.array([sectionsize[0], WL_ef[2 * ie]])
                    if stress[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                        e[ie] = 10 ** 6
                    else:
                        e[ie] = abs(stress[ie, 4]) / stress[ie, 0] * 1000
                    A_HOOP = np.concatenate([HOOP[0:5], [sectionsize[0] / 2]])
                    M, maxN = SA_RW4_HMD(stress[ie, 0], Form_z, steelbar_z,
                                         A_HOOP, Fc, timecase)
                    if e[ie] == 0:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0])
                        else:  # 引張
                            ratio_output[ie, 1] = abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1])
                    else:
                        if stress[ie, 0] > 0:  # 圧縮
                            ratio_output[ie, 1] = max(abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[0]))
                        else:  # 引張
                            ratio_output[ie, 1] = max(abs(stress[ie, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie, 0]) * 10 ** 3 / abs(maxN[1]))

                # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
                Form_Q = np.array([[WL_ef[1], sectionsize[0]],
                                   [WL_ef[3], sectionsize[0]],
                                   [WL_ef[5], sectionsize[0]]])
                rq, ALW_Q, Qs1 = SA_RW4Qratio(Form_Q, ele_length, steelbar_y,
                                              HOOP, Fc, stress, timecase, QL,
                                              qup_wall, RCQ)
                ratio_output[:, 2:4] = np.atleast_2d(rq)
                ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
                ratio_output[1, :] = 0
                # 最大検定値の保存
                if np.max(ratio_output) > maxratios[1]:
                    maxratios[0] = ele_no
                    maxratios[1] = np.max(ratio_output)
                    maxratios_text = SA_RW4_HMD_text(
                        ele_length, steelbar_y, steelbar_z, HOOP, Fc, stress,
                        timecase, QL, ele_no, section_no, qup_wall, reduction,
                        LOAD_CASE_NAME, Form_Q, WL_ef, sectionsize, RCQ,
                        pick_section_name, walldesign_index, v_pitch, v_num,
                        method_rcw)
        else:
            raise ValueError('ERROR:RC壁断面形状設定ミス (断面番号%d)'
                             % int(section_no))

    elif column_judge != 0 and beam_judge != 0:
        raise ValueError('柱断面・梁断面の識別失敗 (断面番号%d)' % int(section_no))
    else:  # ここでRCの断面情報を決めないといけない
        # MATLABは表示のみで検定比0のまま続行するが、mgtkitでは検定漏れ防止の
        # ため明示エラーとする (パイプライン側で事前チェックも行う)
        raise ValueError(
            'RC断面（配筋情報）が定義されていません (断面番号%d)。'
            '検定タブの「RC壁配筋設定」で壁として配筋を指定するか、'
            'MIDASの梁配筋(*REBAR-BEAM)を設定してください' % int(section_no))

    return ratio_output, maxratios, maxratios_text
