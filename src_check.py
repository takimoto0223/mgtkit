# -*- coding: utf-8 -*-
"""SRC断面検定 (MATLAB原典からの逐語移植).

元コード (msrc/):
  privatetool_function/MIDAS/ratio/SRC/SRC_ratio_analysis.m
  privatetool_function/MIDAS/ratio/SRC/SRCcolumn_ALNM.m
  privatetool_function/MIDAS/ratio/SRC/SRCbeam_ALM.m
  structural_function/SRC/SRC_section_analysis/column/L_rNc.m
  structural_function/SRC/SRC_section_analysis/column/r_N_c.m
  structural_function/SRC/SRC_section_analysis/column/r_N_c_R.m
  structural_function/SRC/SRC_section_analysis/column/SA_L_SRC4column_AIJ.m
  structural_function/SRC/SRC_section_analysis/column/SA_RC4_SRC_HMD.m
  structural_function/SRC/SRC_section_analysis/column/SA_SRC4column_AIJ.m
  structural_function/SRC/SRC_section_analysis/column/SA_SRC4column_HMD.m
  structural_function/SRC/SRC_section_analysis/column/SA_SRC_4Hcolumn_Qratio.m
  structural_function/SRC/SRC_section_analysis/column/SA_SRC_Rcolumn_AIJ.m
  structural_function/SRC/SRC_section_analysis/column/SA_SRC_Rcolumn_Qratio.m
  structural_function/SRC/SRC_section_analysis/column/subSRC4column_AIJ.m
  structural_function/SRC/SRC_section_analysis/column/subSRC_Rcolumn_AIJ.m
  structural_function/SRC/SRC_section_analysis/beam/SRC4beam_ALWM.m
  structural_function/SRC/SRC_section_analysis/beam/SA_SRCbeamQratio.m
  structural_function/RC/ALST_etc/steeelbar/SD_check.m
  structural_function/RC/RC_section_analysis/column/rectangular/sig2.m
  structural_function/RC/RC_section_analysis/column/circle/find_e.m
  privatetool_function/car3d.m
  (privatetool_function/find_index_rough.m は util.py へ移植)

検定詳細文関数 SRC_H_beam_ALWM_text / SRC_Rcolumn_ALWNM_text /
SRC_4Hcolumn_ALWNM_text は src_text.py (並行作成中) にあり、
SRC_ratio_analysis 内で呼び出し直前に遅延importする。

データ構造 (MATLAB cell の Python 表現):
  SRC_ALW_NM: MATLAB cell(柱断面数,2,3) -> 3重の入れ子list
    SRC_ALW_NM[i][j][k]
      i: SRCcolumns の行 (0始まり) / j: 0=強軸(z軸/My), 1=弱軸(y軸/Mz)
      k: 0=長期NM曲線, 1=短期NM曲線, 2=HOOP配筋情報(j=0のみ)
    NM曲線は (N点数,2) ndarray [ALW_N(kN), ALW_M(kNm)]
    (鋼管被覆円柱では j=1 は未使用=None)
  SRC_ALW_M: MATLAB cell(梁断面数,3) -> 2重の入れ子list
    SRC_ALW_M[i][k]  k: 0=長期ALW_M(3x2), 1=短期ALW_M(3x2), 2=HOOP
    ALW_M は行=i端/中央/j端, 列=[正曲げ許容M, 負曲げ許容M(負値)] (kNm)

単位系: 断面寸法mm, 応力度N/mm2, N-M曲線・許容せん断はkN/kNm。
stress は 3x5 [N(kN), Qy(kN), Qz(kN), My(kNm), Mz(kNm)]。
timecase: 1=長期, 10以上=短期 (内部で1/2へ変換し配列は[timecase-1]参照)。
インデックス規約: util.find_index は 0-based/-1 (MATLAB 1-based/0 のため
呼び側で +1 補正)。util.find_index_rough は 0-based (必ず見つかる)。

MATLABの plot/hold/title/axis/tic/toc 等の描画・計測行は移植せず
コメントで残す。smooth(y,span,'sgolay') は _smooth_sgolay で再現
(次数2, 端部は窓を断面内へシフトした最小二乗評価。MATLAB Curve Fitting
Toolbox の実装と端部処理が完全一致する保証はない)。
"""

import cmath
import math

import numpy as np

from .util import (find_index, find_index_rough, find_zero_index,
                   doublecheck, excelround, _colon)
from .alst import ALST_S, sfc
from .secprops import steelForm
from .rc_check import (_bar_table, Area_steelbar, outf_steelbar_JIS,
                       ALST_RC_AIJ, ALST_steelbar_KJ, E_RC_AIJ)


# ===========================================================================
# 内部ユーティリティ
# ===========================================================================

def _std(x):
    """MATLAB std 相当 (標本標準偏差 N-1)。要素1個以下のとき0 (MATLAB同様)."""
    x = np.asarray(x, dtype=float).ravel()
    if x.size <= 1:
        return 0.0
    return float(np.std(x, ddof=1))


def _ensure_rows(cell, nrows):
    """MATLABセルの自動拡張を再現: cell を nrows 行 ([None,None,None]) に拡張."""
    while len(cell) < nrows:
        cell.append([None, None, None])


def _smooth_sgolay(y, span, degree=2):
    """MATLAB smooth(y,span,'sgolay') 相当 (等間隔データ・多項式次数2).

    内部点は中心対称窓の Savitzky-Golay 平滑化。端部は窓幅spanを保った
    まま窓をデータ内にシフトし、最小二乗フィットを該当位置で評価する
    (scipy savgol_filter mode='interp' 相当)。
    span はデータ長以下・奇数に調整 (MATLAB smooth と同様)。
    span <= degree となる場合は平滑化せずそのまま返す。
    """
    y = np.asarray(y, dtype=float).ravel()
    n = y.size
    span = int(min(span, n))
    if span % 2 == 0:
        span = span - 1
    if span <= degree:
        return y.copy()
    half = (span - 1) // 2
    x = np.arange(span, dtype=float)
    V = np.vander(x, degree + 1, increasing=True)
    P = V.dot(np.linalg.pinv(V))
    out = np.empty(n)
    prev_start = None
    fitted = None
    for i in range(n):
        start = i - half
        if start < 0:
            start = 0
        if start > n - span:
            start = n - span
        if start != prev_start:
            fitted = P.dot(y[start:start + span])
            prev_start = start
        out[i] = fitted[i - start]
    return out


# ===========================================================================
# SD_check.m (鉄筋径からSD種別を判別)
# ===========================================================================

def SD_check(di):
    """SD_check.m の逐語移植 (鉄筋径からSD種別を判別).

    元ファイル: msrc/structural_function/RC/ALST_etc/steeelbar/SD_check.m
    bar_table は rc_check._bar_table を利用。
    di>=29:390 / di>=19:345 / di<=16:295。16<di<19 はエラー (MATLAB同様)。
    """
    bar_name, _ = _bar_table()  # bar_table{1,1}
    if find_index(bar_name, di) + 1 == 0:
        raise ValueError('ERROR:適切な鉄筋径を入力してください (SD_check): '
                         'D%g' % di)  # MATLAB: disp+stop
    if di >= 29:
        SD = 390
    elif di >= 19:
        SD = 345
    elif di <= 16:
        SD = 295
    else:
        raise ValueError('ERROR:適切な鉄筋径を入力してください (SD_check): '
                         'D%g' % di)  # MATLAB: disp+stop
    return SD


# ===========================================================================
# car3d.m (3次方程式の解) / sig2.m
# ===========================================================================

def car3d(a3, a2, a1, a0):
    """car3d.m の逐語移植: a3*x^3+a2*x^2+a1*x+a0=0 の3根 (複素ndarray).

    元ファイル: msrc/privatetool_function/car3d.m
    MATLAB同様、判別式が負のときは複素演算 (sqrt(負)は複素数を返す)。
    """
    a2 = a2 / a3
    a1 = a1 / a3
    a0 = a0 / a3
    p = (a1 - a2 ** 2 / 3)
    q = (a0 - a1 * a2 / 3 + 2.0 / 27 * a2 ** 3)
    omega = math.cos(2 * math.pi / 3) + math.sin(2 * math.pi / 3) * 1j

    if (q / 2) ** 2 + (p / 3) ** 3 >= 0:
        au = -1 * q / 2 + math.sqrt((q / 2) ** 2 + (p / 3) ** 3)
        av = -1 * q / 2 - math.sqrt((q / 2) ** 2 + (p / 3) ** 3)
        if au >= 0:
            au_3 = (au) ** (1.0 / 3)
        else:
            au_3 = -1 * (-1 * au) ** (1.0 / 3)
        if av >= 0:
            av_3 = (av) ** (1.0 / 3)
        else:
            av_3 = -1 * (-1 * av) ** (1.0 / 3)
        Answers_car3d = np.array(
            [omega ** 0 * au_3 + omega ** 3 * av_3,
             omega ** 1 * au_3 + omega ** 2 * av_3,
             omega ** 2 * au_3 + omega ** 1 * av_3]) - a2 / 3
    else:
        # MATLABのsqrt(負)は複素数を返すため complex で計算 (numpyはnan)
        u3 = -1 * q / 2 + cmath.sqrt(complex((q / 2) ** 2 + (p / 3) ** 3))
        v3 = -1 * q / 2 - cmath.sqrt(complex((q / 2) ** 2 + (p / 3) ** 3))
        u = u3 ** (1.0 / 3)
        v = v3 ** (1.0 / 3)
        Answers_car3d = np.array(
            [omega ** 0 * u + omega ** 3 * v,
             omega ** 1 * u + omega ** 2 * v,
             omega ** 2 * u + omega ** 1 * v]) - a2 / 3
    return Answers_car3d


def sig2(end_num, dc, dd):
    """sig2.m の逐語移植 (鉄筋位置の2乗和計算用の閉形式).

    元ファイル: msrc/structural_function/RC/RC_section_analysis/column/
                rectangular/sig2.m
    """
    return (dd ** 2 / 6 * end_num * (end_num + 1) * (2 * end_num + 1)
            + 2 * (dc - dd) * dd / 2 * end_num * (end_num + 1)
            + end_num * (dc - dd) ** 2)


# ===========================================================================
# find_e.m (円形断面: 偏心量から中立軸角度θを探索)
# ===========================================================================

def find_e(true_e, r, rr, pg, n):
    """find_e.m の逐語移植 (円形柱の偏心量→中立軸角度θの反復探索).

    元ファイル: msrc/structural_function/RC/RC_section_analysis/column/
                circle/find_e.m
    """
    bound_d = 0.0
    bound_u = math.pi
    bound_gap = bound_u - bound_d

    while True:
        d_gap = bound_gap / 100
        # NOTE: MATLABのコロン演算子は終端の丸め誤差を補償して101点を返す。
        #       util._colon(許容誤差なし)では (b-a)/d の二重除算誤差で
        #       100点になる場合があるため、ここでは相対許容誤差付きで再現する
        nn = (bound_u - bound_d) / d_gap
        m = int(math.floor(nn + 1e-10 * max(1.0, abs(nn))))
        c = bound_d + d_gap * np.arange(m + 1, dtype=float)
        e = np.zeros(101)
        for i in range(101):
            e[i] = ((1.0 / 4 * c[i]
                     + math.sin(c[i]) * math.cos(c[i])
                     * (1.0 / 6 * math.cos(c[i]) - 5.0 / 12)
                     + 1.0 / 2 * n * pg * math.pi * (rr / r) ** 2)
                    / (1.0 / 3 * math.sin(c[i]) * (2 + math.cos(c[i]) ** 2)
                       - c[i] * math.cos(c[i])
                       - n * pg * math.pi * math.cos(c[i])) * r)
        # [gap_e theta_i] = min(abs(fix((e-true_e))))
        theta_i = int(np.argmin(np.abs(np.fix(e - true_e))))  # 0-based
        if theta_i == 100:  # MATLAB: theta_i==101
            if e[theta_i] > true_e and e[theta_i - 1] < true_e:
                bound_u = c[theta_i]
                bound_d = c[theta_i - 1]
            elif e[theta_i] < true_e and e[theta_i - 1] > true_e:
                bound_d = c[theta_i]
                bound_u = c[theta_i - 1]
            else:
                theta = c[theta_i]
                break
        elif theta_i == 0:  # MATLAB: theta_i==1
            if e[theta_i] > true_e and e[theta_i + 1] < true_e:
                bound_u = c[theta_i]
                bound_d = c[theta_i + 1]
            elif e[theta_i] < true_e and e[theta_i + 1] > true_e:
                bound_d = c[theta_i]
                bound_u = c[theta_i + 1]
            else:
                theta = c[theta_i]
                break
        elif e[theta_i] > true_e:
            bound_u = c[theta_i]
            if e[theta_i + 1] < true_e:
                bound_d = c[theta_i + 1]
            elif e[theta_i - 1] < true_e:
                bound_d = c[theta_i - 1]
            else:
                theta = c[theta_i]
                break
        elif e[theta_i] < true_e:
            bound_d = c[theta_i]
            if e[theta_i + 1] > true_e:
                bound_u = c[theta_i + 1]
            elif e[theta_i - 1] > true_e:
                bound_u = c[theta_i - 1]
            else:
                theta = c[theta_i]
                break
        if bound_gap == bound_u - bound_d:
            raise ValueError('find_e: 探索失敗 (boundが更新されない)')  # MATLAB: stop
        else:
            bound_gap = bound_u - bound_d
        theta = c[theta_i]

        gap_e = abs((true_e - e[theta_i]))
        if gap_e < 2:
            break
    return theta


# ===========================================================================
# r_N_c.m / r_N_c_R.m (コンクリート部分の最大負担圧縮力)
# ===========================================================================

def r_N_c(b, D, a, num, sAe, n, f_c, mfc, timecase):
    """r_N_c.m の逐語移植 (角形: RC部分の最大負担圧縮力[N]).

    timecase は変換済み (1=長期/2=短期)。
    """
    cA = b * D - sAe - a * num
    Ae = cA + n * a * num
    rNc1 = Ae * f_c[timecase - 1, 0]
    rNc2 = Ae * mfc[timecase - 1, 0] / n
    return min(rNc1, rNc2)


def r_N_c_R(D, a, num, sAe, n, f_c, rfc, timecase):
    """r_N_c_R.m の逐語移植 (円形: RC部分の最大負担圧縮力[N])."""
    cA = D ** 2 / 4 * math.pi - sAe - a * num
    Ae = cA + n * a * num
    rNc1 = Ae * f_c[timecase - 1, 0]
    rNc2 = Ae * rfc[timecase - 1, 0] / n
    return min(rNc1, rNc2)


# ===========================================================================
# L_rNc.m (5%偏心を考慮したSRC長柱の許容圧縮耐力)
# ===========================================================================

def L_rNc(rNk, rv, A, D):
    """L_rNc.m の逐語移植 (5%偏心考慮のSRC長柱許容圧縮耐力).

    注意: MATLAB原典は plot直後に無条件の `stop` (未定義変数エラー) があり
    必ず停止する (デバッグ途中のコードと思われる)。ここでも同位置で例外を
    送出する。呼び元 SA_L_SRC4column_AIJ もそのため原典どおり実行不能。
    """
    A = np.array(np.atleast_2d(A), dtype=float, copy=True)

    # 引張領域は削除
    N_size = A.shape[0]
    for j in range(1, N_size + 1):
        if A[j - 1, 0] < 0:
            A[j - 1, 0] = 0
    no_zero_i = np.nonzero(A[:, 0])[0]  # find(A(:,1))
    A = A[no_zero_i, :]
    # 削除完了
    # % plot(A(:,2)/10^6,A(:,1)/10^3,'k-') 描画は省略
    # % hold on

    # 与えられた軸力に対して偏心させた時に生じるモーメントを計算
    N_size = A.shape[0]
    rN = np.zeros(N_size)
    rMc = np.zeros(N_size)
    for i in range(1, N_size + 1):
        rN[i - 1] = A[i - 1, 0]
        r_delta = 1 / (1 - rv * rN[i - 1] / rNk)
        if r_delta < 1:
            r_delta = 1
        rMc[i - 1] = rN[i - 1] * 0.05 * D * r_delta

    # % plot(rMc/10^6,rN/10^3,'r-') 描画は省略
    # % hold on
    raise ValueError('L_rNc: MATLAB原典に無条件stopがあり必ず停止する'
                     ' (SA_L_SRC4column_AIJ は原典でも実行不能)')  # MATLAB: stop
    # --- 以下はMATLAB原典の stop 以降の到達不能コード (参考のため残す) ---
    # rMc = abs(rMc - A(:,2)');
    # [zero index]=min(rMc);
    # r_delta = 1/(1-rv*rN(index)/rNk);
    # r_M_c = rN(index)*0.05*D*r_delta;
    # if r_M_c / A(index,2) >=0.95 && r_M_c / A(index,2) <= 1.05
    #     rNc = rN(index);
    # else
    #     ERROR='rNc探索失敗'; stop
    # end


# ===========================================================================
# SA_RC4_SRC_HMD.m (SRC内RC部分の許容曲げ: HMD式)
# ===========================================================================

def SA_RC4_SRC_HMD(Nd, Form, steelbar, HOOP, Fc, timecase, f_c):
    """SA_RC4_SRC_HMD.m の逐語移植 (SRC内RC部分の許容曲げ, ファイバー式).

    Nd: 軸力(kN)。Form=[b,D](mm)。
    steelbar=[1,総本数,径D,せい方向本数nv,幅方向本数nh,SD]。
    HOOP は6要素 [径,pitch,2,2,SD,cover] (SA_SRC4column_HMD が変換した形)。
    f_c: 低減済みRC許容応力度 (2x5)。timecase: 1=長期/10以上=短期。
    返り値 (M_AL [N・mm], maxN [2要素 ndarray, N])
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()

    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # RC断面の外形情報
    b = Form[0]
    D = Form[1]
    di_main = steelbar[2]
    SD_main = steelbar[5]
    a = Area_steelbar(di_main, 1)
    ai = 2 * a + np.zeros(int(steelbar[3]))
    ai[0] = steelbar[4] * a
    ai[int(steelbar[3]) - 1] = steelbar[4] * a

    num = steelbar[1]
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    n = E_RC_AIJ(Fc)
    n = n[1]
    fc = f_c[timecase2 - 1, 0]
    ft = rfc[timecase2 - 1, 0]

    tol = 0.01  # 許容誤差
    ndiv = 200  # コンクリート部分の分割数
    dy = D / ndiv

    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[4]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[5]
    dc = (cover_depth + outf_steelbar_JIS(di_support)
          + outf_steelbar_JIS(di_main) * 0.5)

    by = _colon(dc, (D - 2 * dc) / (steelbar[3] - 1), D - dc)
    cy = _colon(dy / 2, dy, D - dy / 2)

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
        bf = by - xn
        bfmax = np.max(np.abs(bf))
        if np.all(bf == 0):  # MATLAB: if bf==0 (全要素0のとき真)
            bf = np.inf  # MATLAB: bf=inf (スカラ代入)
        else:
            bf = bf / bfmax
        cf = np.minimum(cy - xn, 0)
        cfmax = np.max(np.abs(cf))

        if cfmax == 0:
            cf = 0.0
        else:
            cf = cf / cfmax

        if (cfmax * ft / n) > bfmax * fc:
            bf = bf * fc * n
            cf = cf * fc
        else:
            bf = bf * ft
            cf = cf * ft / n

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
    return float(M_AL), maxN


# ===========================================================================
# subSRC4column_AIJ.m (偏心e→許容N,Mの1点算定: 角形RC部分)
# ===========================================================================

def subSRC4column_AIJ(e, Form, steelbar, HOOP, Fc, timecase, f_c):
    """subSRC4column_AIJ.m の逐語移植 (偏心距離e時の許容N・Mを1点算定).

    e: 偏心距離(mm, 正=圧縮側/負=引張側)。timecase は変換済み(1/2)。
    HOOP=[径,pitch,SD,cover]。f_c: 低減済みRC許容応力度(2x5)。
    どこにも該当しなかった場合 (0,0,0) を返す (MATLAB同様)。
    返り値 (ALW_N [N], ALW_M [N・mm], xn [mm])
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()

    # どこにも該当しなかった場合0を返す
    ALW_N = 0.0
    ALW_M = 0.0
    xn = 0.0

    # RC断面の外形情報
    b = Form[0]
    D = Form[1]

    # 帯筋情報を読み込み
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[3]

    # type1とtype2によって配筋形式が異なる．
    if steelbar[0] == 1:
        num = steelbar[1]
        di_main = steelbar[2]
        nv = int(steelbar[3])
        nh = steelbar[4]
        SD_main = steelbar[5]
        # 柱主筋の重心の縁距離
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = (D - 2 * dc) / (nv - 1)
        # 鉄筋重心位置の設定
        y = _colon(dc, dd, D - dc)
        if (nh - 1) * 2 + (nv - 1) * 2 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (subSRC4column_AIJ)')  # MATLAB: stop
    elif steelbar[0] == 2:
        num = steelbar[1]
        di_main = steelbar[2]
        ns = steelbar[3]  # noqa: F841 (MATLAB同様未使用)
        nss = int(steelbar[4])
        SD_main = steelbar[5]
        if 8 * nss - 4 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (subSRC4column_AIJ)')  # MATLAB: stop
        nv = 2 * nss
        nh = 2 * nss
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = steelbar[6]
        y = np.concatenate([_colon(dc, dd, dc + (nss - 1) * dd),
                            _colon(D - dc - (nss - 1) * dd, dd, D - dc)])
    else:
        raise ValueError('配筋情報エラー (subSRC4column_AIJ)')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    if steelbar[0] == 1:
        dd2 = (b - 2 * dc) / (nh - 1)
    elif steelbar[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)

    if check_dd >= 1.5 * di_main and check_dd >= 25:
        pass
    else:
        raise ValueError('鉄筋あき不足 (subSRC4column_AIJ)')  # MATLAB: stop
    # その１：主筋の規定
    if num >= 4 and di_main >= 13:
        pass
    else:
        raise ValueError('主筋規定外 (subSRC4column_AIJ)')  # MATLAB: stop
    # その２：主筋断面積の規定 (原典でコメントアウト)
    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        raise ValueError('帯筋間隔NG（0.8％未満） (subSRC4column_AIJ)')  # MATLAB: stop
    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    if aw / (max(b, D) * pitch) >= 0.2 / 100:
        pass
    else:
        pass  # 原典: ERROR代入もstopもコメントアウト
    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        raise ValueError('鉄筋かぶり厚再検討（40mm未満） (subSRC4column_AIJ)')  # MATLAB: stop

    # %%%%%%%%%以下eが正のとき（圧縮力作用時）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # 中立軸の算定1-① =中立軸が断面外にある場合
    # 全断面圧縮時の有効断面などを鉄筋の段数別に計算
    if nv > 2:
        Ae = b * D + (n - 1) * num * a
        g = (b * D * D / 2 + (n - 1) * (np.sum(y[1:nv - 1]) * 2 * a
                                        + nh * a * (y[0] + y[nv - 1]))) / Ae
        Ig = (b * D * (1.0 / 3 * D ** 2 - g * D + g ** 2)
              + (n - 1) * nh * a * (g - y[0]) ** 2
              + np.sum((n - 1) * 2 * a * (g - y[1:nv - 1]) ** 2)
              + (n - 1) * nh * a * (g - y[nv - 1]) ** 2)
    else:
        Ae = b * D + (n - 1) * num * a
        g = (b * D * D / 2 + (n - 1) * (nh * a * (y[0] + y[nv - 1]))) / Ae
        Ig = (b * D * (1.0 / 3 * D ** 2 - g * D + g ** 2)
              + (n - 1) * nh * a * (g - y[0]) ** 2
              + (n - 1) * nh * a * (g - y[nv - 1]) ** 2)

    # 中立軸が断面外にある条件に適合するかチェック
    if e > 0 and e <= Ig / (Ae * (D - g)) + D / 2 - g:
        xnout = Ig / (Ae * (g + e - D / 2)) + g
        Sn = Ae * (xnout - g)
        In = Ig + Ae * (xnout - g) ** 2  # noqa: F841 (MATLAB同様未使用)

        if xnout >= D:
            N1 = Sn / xnout * f_c[timecase - 1, 0]
            N2 = Sn / n / (xnout - dc) * rfc[timecase - 1, 0]
            ALW_N = min(N1, N2)
            ALW_M = ALW_N * e
            xn = xnout

    # 中立軸の算定1-② =中立軸が断面内にある場合
    # 1-2-1：k=nvの場合 (鉄筋が全て圧縮条件下)
    if e > 0 and e > Ig / (Ae * (D - g)) + D / 2 - g:
        if nv >= 3:
            sig1_2tonv_1 = (y[1] + y[nv - 2]) / 2 * (nv - 2)
            sig2_2tonv_1 = sig2(nv - 1, dc, dd) - sig2(1, dc, dd)

            S2 = b / 2
            S1 = 2 * (n - 1) * nh * a + (nv - 2) * (n - 1) * 2 * a
            S0 = -1 * (nh * a * (n - 1) * y[0] + nh * a * (n - 1) * y[nv - 1]
                       + 2 * a * (n - 1) * sig1_2tonv_1)

            I3 = b / 3
            I1 = (-2 * nh * a * ((n - 1) * y[0] + (n - 1) * y[nv - 1])
                  - 2 * 2 * a * ((n - 1) * sig1_2tonv_1))
            I0 = (nh * a * (n - 1) * (y[0] ** 2 + y[nv - 1] ** 2)
                  + 2 * a * (n - 1) * sig2_2tonv_1)

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                # MATLABの比較は実部で行われる
                if y[nv - 1] <= xnin[ic].real and xnin[ic].real <= D:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    ALW_N = min([N1, N2])
                    ALW_M = ALW_N * e
        else:
            # 鉄筋の段数が2の時
            S2 = b / 2
            S1 = 2 * n * nh * a
            S0 = -1 * (y[0] * n * nh * a + y[nv - 1] * n * nh * a)

            I3 = b / 3
            I1 = -2 * nh * a * (n * y[0] + n * y[nv - 1])
            I0 = nh * a * (n * y[0] ** 2 + n * y[nv - 1] ** 2)

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if y[nv - 1] <= xnin[ic].real and xnin[ic].real <= D:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    ALW_N = min([N1, N2])
                    ALW_M = ALW_N * e

    # k<nvの場合：以下では引張鉄筋のある場合の検討
    # 1-2-2：k=1の場合
    if e > 0:
        if nv >= 3:
            sig1_2tonv_1 = (y[1] + y[nv - 2]) / 2 * (nv - 2)
            sig2_2tonv_1 = sig2(nv - 1, dc, dd) - sig2(1, dc, dd)

            S2 = b / 2
            S1 = (2 * n - 1) * nh * a + (nv - 2) * 2 * a * n
            S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a
                       + sig1_2tonv_1 * n * 2 * a)

            I3 = b / 3
            I1 = (-2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
                  - 2 * 2 * a * (n * sig1_2tonv_1))
            I0 = (nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)
                  + 2 * a * (n * sig2_2tonv_1))

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if y[0] <= xnin[ic].real and xnin[ic].real <= y[1]:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                    ALW_N = min([N1, N2, N3])
                    ALW_M = ALW_N * e
        else:
            # 鉄筋の段数が2の時
            S2 = b / 2
            S1 = (2 * n - 1) * nh * a
            S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a)

            I3 = b / 3
            I1 = -2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
            I0 = nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if y[0] <= xnin[ic].real and xnin[ic].real <= y[1]:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                    ALW_N = min([N1, N2, N3])
                    ALW_M = ALW_N * e

    # 1-2-3：k>=2の場合，k=2からk=nv-1までforループにて検定
    if e > 0:
        if nv <= 2:
            pass  # nv=2でk=2の場合はk=nvの箇所ですでに検定している
        else:
            for k in range(2, nv):
                kk = k

                sig1_2tok = (y[1] + y[kk - 1]) / 2 * (kk - 1)
                sig1_k1tonv_1 = (y[kk] + y[nv - 2]) / 2 * (nv - kk - 1)
                sig2_2tok = sig2(kk, dc, dd) - sig2(1, dc, dd)
                sig2_k1tonv_1 = sig2(nv - 1, dc, dd) - sig2(kk, dc, dd)

                S2 = b / 2
                S1 = ((2 * n - 1) * nh * a + (nv - 2) * 2 * a * n
                      - (kk - 1) * 2 * a)
                S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a
                           + 2 * a * (n - 1) * sig1_2tok
                           + 2 * a * n * sig1_k1tonv_1)

                I3 = b / 3
                I1 = (-2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
                      - 2 * 2 * a * ((n - 1) * sig1_2tok + n * sig1_k1tonv_1))
                I0 = (nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)
                      + 2 * a * ((n - 1) * sig2_2tok + n * sig2_k1tonv_1))

                xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                             I1 - S0 + (D / 2 - e) * S1,
                             (I0 + (D / 2 - e) * S0))
                for ic in range(3):
                    if xnin[ic].imag < 0.001:
                        xnin[ic] = xnin[ic].real
                    else:
                        xnin[ic] = -1 * abs(xnin[ic].real)
                    if y[kk - 1] <= xnin[ic].real and xnin[ic].real <= y[kk]:
                        xn = xnin[ic].real
                        Sn = S2 * xn ** 2 + S1 * xn + S0
                        N1 = Sn * f_c[timecase - 1, 0] / xn
                        N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                        N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                        ALW_N = min([N1, N2, N3])
                        ALW_M = ALW_N * e

    # %%%%%%%%%以下eが負のとき（引張作用時）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # 中立軸の算定2-① =中立軸が断面外にある場合 (圧縮鉄筋の段数kが0)
    if nv >= 3:
        sig1_2tonv_1 = (y[1] + y[nv - 2]) / 2 * (nv - 2)
        sig2_2tonv_1 = sig2(nv - 1, dc, dd) - sig2(1, dc, dd)

        S1 = 2 * n * nh * a + (nv - 2) * n * a * 2
        S0 = -1 * ((y[0] + y[nv - 1]) * n * nh * a + 2 * a * n * sig1_2tonv_1)

        I1 = (-2 * nh * a * (n * y[0] + n * y[nv - 1])
              - 2 * 2 * a * (n * sig1_2tonv_1))
        I0 = (nh * a * (n * y[0] ** 2 + n * y[nv - 1] ** 2)
              + 2 * a * (n * sig2_2tonv_1))
    else:
        S1 = 2 * n * nh * a
        S0 = -1 * (y[0] * n * nh * a + y[nv - 1] * n * nh * a)

        I1 = -2 * nh * a * (n * y[0] + n * y[nv - 1])
        I0 = nh * a * (n * y[0] ** 2 + n * y[nv - 1] ** 2)

    eb = (S0 / S1 + I0 / S0)
    ec = (I0 / S1)
    # MATLAB: sqrt(負)は複素数を返す。比較は実部で行われるため complex で計算
    _sq = cmath.sqrt(complex(eb ** 2 - 4 * ec))
    e_down = D / 2 + (eb - _sq) / 2
    e_up = D / 2 + (eb + _sq) / 2

    # 偏心率による中立軸位置の検定
    if e < 0 and e >= e_down.real and e <= e_up.real:
        x_b = I1 - S0 + (D / 2 - e) * S1
        x_c = (I0 + (D / 2 - e) * S0)
        xn = -1 * x_c / x_b
        Sn = S1 * xn + S0
        for ic in range(1):
            if xn < 0:
                # xn負であればコンクリートの圧縮では決まらない
                N1 = -1 * np.inf
            else:
                N1 = Sn * f_c[timecase - 1, 0] / xn
            N2 = Sn * rfc[timecase - 1, 0] / n / (dc - xn)
            N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)

            ALW_N = max([N1, N2, N3])
            ALW_M = ALW_N * e

    # 中立軸の算定2-② =中立軸が断面内にあるが,圧縮鉄筋がない場合(k=0)
    if e < 0:
        if nv >= 3:
            sig1_2tonv_1 = (y[1] + y[nv - 2]) / 2 * (nv - 2)
            sig2_2tonv_1 = sig2(nv - 1, dc, dd) - sig2(1, dc, dd)

            S2 = b / 2
            S1 = 2 * n * nh * a + (nv - 2) * n * 2 * a
            S0 = -1 * (y[0] * n * nh * a + y[nv - 1] * n * nh * a
                       + sig1_2tonv_1 * n * a * 2)

            I3 = b / 3
            I1 = (-2 * nh * a * (n * y[0] + n * y[nv - 1])
                  - 2 * 2 * a * (n * sig1_2tonv_1))
            I0 = (nh * a * (n * y[0] ** 2 + n * y[nv - 1] ** 2)
                  + 2 * a * (n * sig2_2tonv_1))

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if xnin[ic].real <= y[0] and xnin[ic].real >= 0:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    if xn < 0:
                        N1 = -1 * np.inf
                    else:
                        N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (dc - xn)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)

                    ALW_N = max([N1, N2, N3])
                    ALW_M = ALW_N * e
        else:
            # 鉄筋の段数が2の時
            S2 = b / 2
            S1 = 2 * n * nh * a
            S0 = -1 * (y[0] * n * nh * a + y[nv - 1] * n * nh * a)

            I3 = b / 3
            I1 = -2 * nh * a * (n * y[0] + n * y[nv - 1])
            I0 = nh * a * (n * y[0] ** 2 + n * y[nv - 1] ** 2)

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if xnin[ic].real <= y[0] and xnin[ic].real >= 0:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    if xn < 0:
                        N1 = -1 * np.inf
                    else:
                        N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (dc - xn)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)

                    ALW_N = max([N1, N2, N3])
                    ALW_M = ALW_N * e

    # 中立軸の算定2-③ =中立軸が断面内にあり,圧縮鉄筋も存在する場合(k>0)
    # 2-3-1：k>=2の場合
    if e < 0:
        if nv <= 2:
            pass  # k=2でnv=2は全鉄筋圧縮となり断面方向力が引張とは考えられない
        else:
            for k in range(2, nv):
                sig1_2tok = (y[1] + y[k - 1]) / 2 * (k - 1)
                sig1_k1tonv_1 = (y[k] + y[nv - 2]) / 2 * (nv - k - 1)

                sig2_2tok = sig2(k, dc, dd) - sig2(1, dc, dd)
                sig2_k1tonv_1 = sig2(nv - 1, dc, dd) - sig2(k, dc, dd)

                S2 = b / 2
                S1 = ((2 * n - 1) * nh * a + n * (nv - 2) * 2 * a
                      - (k - 1) * 2 * a)
                S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a
                           + sig1_2tok * (n - 1) * 2 * a
                           + sig1_k1tonv_1 * n * 2 * a)

                I3 = b / 3
                I1 = (-2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
                      - 4 * a * ((n - 1) * sig1_2tok + n * sig1_k1tonv_1))
                I0 = (nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)
                      + 2 * a * ((n - 1) * sig2_2tok + n * sig2_k1tonv_1))

                xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                             I1 - S0 + (D / 2 - e) * S1,
                             (I0 + (D / 2 - e) * S0))
                for ic in range(3):
                    if xnin[ic].imag < 0.001:
                        xnin[ic] = xnin[ic].real
                    else:
                        xnin[ic] = -1 * abs(xnin[ic].real)
                    if y[k - 1] <= xnin[ic].real and xnin[ic].real <= y[k]:
                        xn = xnin[ic].real
                        Sn = S2 * xn ** 2 + S1 * xn + S0
                        N1 = Sn * f_c[timecase - 1, 0] / xn
                        N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                        N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                        ALW_N = max([N1, N2, N3])
                        ALW_M = ALW_N * e

    # 2-3-2：k=1の場合
    if e < 0:
        if nv >= 3:
            sig1_2tonv_1 = (y[1] + y[nv - 2]) / 2 * (nv - 2)
            sig2_2tonv_1 = sig2(nv - 1, dc, dd) - sig2(1, dc, dd)

            S2 = b / 2
            S1 = (2 * n - 1) * nh * a + (nv - 2) * 2 * a * n
            S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a
                       + sig1_2tonv_1 * n * 2 * a)

            I3 = b / 3
            I1 = (-2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
                  - 2 * 2 * a * (n * sig1_2tonv_1))
            I0 = (nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)
                  + 2 * a * (n * sig2_2tonv_1))

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if y[0] <= xnin[ic].real and xnin[ic].real <= y[1]:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                    ALW_N = max([N1, N2, N3])
                    ALW_M = ALW_N * e
        else:
            # 鉄筋の段数が2の時
            S2 = b / 2
            S1 = (2 * n - 1) * nh * a
            S0 = -1 * (y[0] * (n - 1) * nh * a + y[nv - 1] * n * nh * a)

            I3 = b / 3
            I1 = -2 * nh * a * ((n - 1) * y[0] + n * y[nv - 1])
            I0 = nh * a * ((n - 1) * y[0] ** 2 + n * y[nv - 1] ** 2)

            xnin = car3d(I3 - S2, (D / 2 - e) * S2,
                         I1 - S0 + (D / 2 - e) * S1, (I0 + (D / 2 - e) * S0))
            for ic in range(3):
                if xnin[ic].imag < 0.001:
                    xnin[ic] = xnin[ic].real
                else:
                    xnin[ic] = -1 * abs(xnin[ic].real)
                if y[0] <= xnin[ic].real and xnin[ic].real <= y[1]:
                    xn = xnin[ic].real
                    Sn = S2 * xn ** 2 + S1 * xn + S0
                    N1 = Sn * f_c[timecase - 1, 0] / xn
                    N2 = Sn * rfc[timecase - 1, 0] / n / (xn - dc)
                    N3 = Sn * rfc[timecase - 1, 0] / n / (D - xn - dc)
                    ALW_N = max([N1, N2, N3])
                    ALW_M = ALW_N * e

    return ALW_N, ALW_M, xn


# ===========================================================================
# SA_SRC4column_AIJ.m (SRC角柱: 許容N-M曲線, AIJ SRC基準 10-15式)
# ===========================================================================

def SA_SRC4column_AIJ(Form, sForm, steelbar, HOOP, Fc, SN, timecase):
    """SA_SRC4column_AIJ.m の逐語移植 (SRC角柱の許容N-M曲線).

    Form=[b,D](mm), sForm=[1,sH,sB,tw,tf,...](H鋼) / [2,...](十字形),
    steelbar=[1,総本数,径D,せい方向本数nv,幅方向本数nh,SD] または
    [2,総本数,径D,1group本数ns,1group幅せい方向本数nss,SD,鉄筋あきdd],
    HOOP=[径D,pitch,SD,cover], Fc=[Fc,種別], SN=鋼種400等,
    timecase=1(長期)/10以上(短期)。
    返り値 (ALW_N1[kN], ALW_M1[kNm], ALW_N2[kN], ALW_M2[kNm]) 各1次元ndarray
    (1:強軸まわり, 2:弱軸まわり)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    # % tic 計測は省略

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # 外形情報
    b = Form[0]
    D = Form[1]

    # 内蔵鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sZy = steelForm(sForm, 7) * 1000  # mm3
    sIx = steelForm(sForm, 2) * 10000  # mm4  # noqa: F841 (MATLAB同様未使用)
    sIy = steelForm(sForm, 3) * 10000  # mm4  # noqa: F841 (MATLAB同様未使用)

    # 配筋情報
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[3]

    if steelbar[0] == 1:
        num = steelbar[1]
        di_main = steelbar[2]
        nv = int(steelbar[3])
        nh = steelbar[4]
        SD_main = steelbar[5]
        # 柱主筋の重心の縁距離
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = (D - 2 * dc) / (nv - 1)
        # 鉄筋重心位置の設定
        y = np.zeros(nv)
        for i in range(1, nv + 1):
            y[i - 1] = dc + (i - 1) * dd
        if (nh - 1) * 2 + (nv - 1) * 2 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_SRC4column_AIJ)')  # MATLAB: stop
    elif steelbar[0] == 2:
        num = steelbar[1]
        di_main = steelbar[2]
        ns = steelbar[3]  # noqa: F841 (MATLAB同様未使用)
        nss = int(steelbar[4])
        SD_main = steelbar[5]
        if 8 * nss - 4 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_SRC4column_AIJ)')  # MATLAB: stop
        nv = 2 * nss
        nh = 2 * nss
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = steelbar[6]
        y = np.zeros(nv)
        for i in range(1, nss + 1):
            y[i - 1] = dc + (i - 1) * dd
        for i in range(nss + 1, nv + 1):
            y[i - 1] = D - dc - (nv - i) * dd
    else:
        raise ValueError('配筋情報エラー (SA_SRC4column_AIJ)')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    # 許容応力度関係
    # 鉄骨量に対するコンクリート圧縮許容応力度の低減 (圧縮側鉄骨量の算定)
    if sForm[0] == 1:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
        stype = 'H-'  # noqa: F841
    elif sForm[0] == 2:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)
        sb = np.max(sForm[1:3])
        sD = np.max(sForm[1:3])
        max_t = np.max(sForm[3:5])
        stype = '2H-'  # noqa: F841
    else:
        raise ValueError('現在H鋼/十字形鋼以外未対応 (SA_SRC4column_AIJ)')  # MATLAB: stop

    f_c = ALST_RC_AIJ(Fc) * (1 - 15 * ps)  # 鉄骨量による低減されたRC許容応力度
    mfc = ALST_steelbar_KJ([di_main, SD_main])  # 柱主筋の許容応力度
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                np.atleast_1d(max_t)]))  # 鉄骨の許容応力度

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    if steelbar[0] == 1:
        dd2 = (b - 2 * dc) / (nh - 1)
    elif steelbar[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    if check_dd >= 1.5 * di_main and check_dd >= 25:
        pass
    else:
        raise ValueError('鉄筋あき不足 (SA_SRC4column_AIJ)')  # MATLAB: stop
    # その１：主筋の規定
    if num >= 4 and di_main >= 13:
        pass
    else:
        raise ValueError('主筋規定外 (SA_SRC4column_AIJ)')  # MATLAB: stop
    # その２：主筋断面積の規定
    if (num * a + sAe) / (b * D) >= 0.8 / 100:
        pass
    else:
        raise ValueError('主筋断面積不足（0.8％未満） (SA_SRC4column_AIJ)')  # MATLAB: stop
    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        raise ValueError('帯筋間隔NG（0.8％未満） (SA_SRC4column_AIJ)')  # MATLAB: stop
    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    if aw / (max(b, D) * pitch) >= 0.2 / 100:
        pass
    else:
        print("ERROR = 'せん断補強筋比不足（0.2％未満）'")  # MATLAB: ERROR表示のみ(%stop)
    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        raise ValueError('鉄筋かぶり厚再検討（40mm未満） (SA_SRC4column_AIJ)')  # MATLAB: stop
    # その６：鉄骨かぶりあつ
    if min((b - sb) / 2, (D - sD) / 2) >= 50:
        pass
    else:
        raise ValueError('鉄骨かぶり厚再検討（50mm未満） (SA_SRC4column_AIJ)')  # MATLAB: stop

    # 以下SRC断面の検定（強軸周り用　10-12式）
    # RC部分の連成させた断面算定 (鉄骨部分はRCありと置換)
    maxN = min((Form[0] * Form[1] - sAe + (n - 1) * num * a) * f_c[timecase - 1, 0],
               ((Form[0] * Form[1] - sAe - num * a) / n + num * a) * (mfc[timecase - 1, 0]))

    cut_plus = -np.inf
    j = 1
    Nplus = []
    Mplus = []
    Xnplus = []
    # 圧縮側のplot
    while cut_plus < 15 and j < 1000:
        _N, _M, _X = subSRC4column_AIJ(j, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nplus.append(_N)
        Mplus.append(_M)
        Xnplus.append(_X)
        if Nplus[j - 1] == 0 and Mplus[j - 1] == 0:
            if j == 1:
                raise IndexError('subSRC4column_AIJ(1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nplus[j - 1] = Nplus[j - 2]
            Mplus[j - 1] = Mplus[j - 2]
        cut_plus = np.float64(max(Nplus)) / np.float64(min(Nplus))
        if j > 300:
            if Mplus[j - 1] < Mplus[j - 201]:
                cut_plus = 10
            else:
                cut_plus = 5
        j = j + 1

    # 引張側のplot
    jj = 1
    cut = -np.inf
    Nminus = []
    Mminus = []
    Xnminus = []
    while cut < 7 and jj < 1000:
        _N, _M, _X = subSRC4column_AIJ(-1 * jj, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nminus.append(_N)
        Mminus.append(_M)
        Xnminus.append(_X)
        if Nminus[jj - 1] == 0 and Mminus[jj - 1] == 0:
            if jj == 1:
                raise IndexError('subSRC4column_AIJ(-1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nminus[jj - 1] = Nminus[jj - 2]
            Mminus[jj - 1] = Mminus[jj - 2]
        cut = np.float64(min(Nminus)) / np.float64(max(Nminus))
        jj = jj + 1

    A = np.array([
        np.concatenate([[maxN], Nminus,
                        _colon(Nminus[jj - 2], (Nplus[j - 2] - Nminus[jj - 2]) / 100, Nplus[j - 2]),
                        Nplus]),
        np.concatenate([[0], Mminus,
                        _colon(Mminus[jj - 2], (Mplus[j - 2] - Mminus[jj - 2]) / 100, Mplus[j - 2]),
                        Mplus])])
    A = A.T
    A = A[np.argsort(A[:, 0], kind='stable')]  # sortrows(A,1)

    # コンクリート部分の最大負担圧縮力
    rNc = r_N_c(b, D, a, num, sAe, n, f_c, mfc, timecase)
    # コンクリート部分の最大負担引張力
    rNt = -1 * a * num * mfc[timecase - 1, 0]
    # 鉄骨部分の純曲げモーメント
    sMo = sZx * sf[timecase - 1, 0]

    sNo = sf[timecase - 1, 0] * sAe

    N_range = np.concatenate([_colon(-1 * sNo + rNt, 10000, sNo + rNc), [sNo + rNc]])
    N_time = len(N_range)

    ALW_N1 = np.zeros(N_time)
    ALW_M1r = np.zeros(N_time)
    ALW_M1s = np.zeros(N_time)
    ALW_M1 = np.zeros(N_time)
    for in1 in range(1, N_time + 1):
        N = N_range[in1 - 1]
        ALW_N1[in1 - 1] = N

        # 軸力がrNc以上の時(N-rNcを鉄骨が負担する）
        if N >= rNc:
            sN = N - rNc
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M1r[in1 - 1] = max(0, sig_b * sZx)
        # 軸力がrNt以上rNc以下であるとき（残りのRCの許容曲げと鉄骨の曲げ耐力の和）
        if rNt <= N and N <= rNc:
            N_index = find_index_rough(A[:, 0], N)
            rM = A[N_index, 1]
            ALW_M1r[in1 - 1] = rM + sMo
        # 軸力がrNt以下であるとき（残りの軸力をSが負担して，余裕分で曲げを負担する）
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M1r[in1 - 1] = max(0, sig_b * sZx)

        # 軸力がsNo以上の時(N-sNoをRCが負担する）
        if N >= sNo:
            rN = N - sNo
            N_index = find_index_rough(A[:, 0], rN)
            rM = A[N_index, 1]
            ALW_M1s[in1 - 1] = rM
        # 軸力がsNo以上sNo以下であるとき（残りのSの許容曲げとRCの曲げ耐力の和）
        if -1 * sNo <= N and N <= sNo:
            N_index = find_index_rough(A[:, 0], 0)
            rM = A[N_index, 1]
            sig_b = sf[timecase - 1, 0] - abs(N) / sAe
            ALW_M1s[in1 - 1] = rM + max(0, sig_b * sZx)
        # 軸力がrNt以下であるとき
        if N < -1 * sNo:
            rN = N + sNo
            N_index = find_index_rough(A[:, 0], rN)
            rM = A[N_index, 1]
            ALW_M1s[in1 - 1] = rM

        ALW_M1 = np.max(np.vstack([ALW_M1s, ALW_M1r]), axis=0)

    ALW_M1 = (ALW_M1 / 10 ** 6)
    ALW_N1 = (ALW_N1 / 10 ** 3)
    # % plot(A(:,2)/10^6,A(:,1)/10^3,'r:') 描画は省略
    # % hold on
    # % plot(ALW_M1,ALW_N1,'r') 描画は省略

    # 以下SRC断面の検定（弱軸周り用　13-15式）
    cut_plus = -np.inf
    j = 1
    Form2 = np.array([Form[1], Form[0]])
    steelbar2 = np.array([steelbar[0], steelbar[1], steelbar[2],
                          steelbar[4], steelbar[3], steelbar[5]])
    # 圧縮側のplot
    Nplus = []
    Mplus = []
    Xnplus = []
    while cut_plus < 15 and j < 1000:
        _N, _M, _X = subSRC4column_AIJ(j, Form2, steelbar2, HOOP, Fc, timecase, f_c)
        Nplus.append(_N)
        Mplus.append(_M)
        Xnplus.append(_X)
        if Nplus[j - 1] == 0 and Mplus[j - 1] == 0:
            if j == 1:
                raise IndexError('subSRC4column_AIJ(1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nplus[j - 1] = Nplus[j - 2]
            Mplus[j - 1] = Mplus[j - 2]
        cut_plus = np.float64(max(Nplus)) / np.float64(min(Nplus))
        if j > 300:
            if Mplus[j - 1] < Mplus[j - 201]:
                cut_plus = 10
            else:
                cut_plus = 5
        j = j + 1

    # 引張側のplot
    jj = 1
    cut = -np.inf
    Nminus = []
    Mminus = []
    Xnminus = []
    while cut < 7 and jj < 1000:
        _N, _M, _X = subSRC4column_AIJ(-1 * jj, Form2, steelbar2, HOOP, Fc, timecase, f_c)
        Nminus.append(_N)
        Mminus.append(_M)
        Xnminus.append(_X)
        if Nminus[jj - 1] == 0 and Mminus[jj - 1] == 0:
            if jj == 1:
                raise IndexError('subSRC4column_AIJ(-1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nminus[jj - 1] = Nminus[jj - 2]
            Mminus[jj - 1] = Mminus[jj - 2]
        cut = np.float64(min(Nminus)) / np.float64(max(Nminus))
        jj = jj + 1

    B = np.array([
        np.concatenate([[maxN], Nminus,
                        _colon(Nminus[jj - 2], (Nplus[j - 2] - Nminus[jj - 2]) / 100, Nplus[j - 2]),
                        Nplus]),
        np.concatenate([[0], Mminus,
                        _colon(Mminus[jj - 2], (Mplus[j - 2] - Mminus[jj - 2]) / 100, Mplus[j - 2]),
                        Mplus])])
    B = B.T
    B = B[np.argsort(B[:, 0], kind='stable')]  # sortrows(B,1)

    rNc = r_N_c(b, D, a, num, sAe, n, f_c, mfc, timecase)
    rNt = -1 * a * num * mfc[timecase - 1, 0]
    sMo = sZy * sf[timecase - 1, 0]
    sNo = sf[timecase - 1, 0] * sAe

    N_range = np.concatenate([_colon(-1 * sNo + rNt, 10000, sNo + rNc), [sNo + rNc]])
    N_time = len(N_range)

    # ここで本当はRCの純曲げの式をつくらないといけない．
    ALW_N2 = np.zeros(N_time)
    ALW_M2r = np.zeros(N_time)
    ALW_M2s = np.zeros(N_time)
    ALW_M2 = np.zeros(N_time)
    for in2 in range(1, N_time + 1):
        N = N_range[in2 - 1]
        # sM=zeros(N_time) は描画専用 (plot(sM...)) のため省略
        ALW_N2[in2 - 1] = N
        # 軸力がsNc以上の時(N-sNcをRCが負担する）
        if N >= rNc:
            sN = N - rNc
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M2r[in2 - 1] = max(0, sig_b * sZy)
            # sM(in2)=sig_b*sZx (描画専用のため省略)
        # 軸力がrNt以上rNc以下であるとき
        if rNt <= N and N <= rNc:
            N_index = find_index_rough(B[:, 0], N)
            rM = B[N_index, 1]
            ALW_M2r[in2 - 1] = rM + sMo
            # sM(in2) = sMo (描画専用のため省略)
        # 軸力がrNt以下であるとき
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M2r[in2 - 1] = max(0, sig_b * sZy)
            # sM(in2)=sig_b*sZy (描画専用のため省略)

        # 軸力がsNo以上の時(N-sNoをRCが負担する）
        if N >= sNo:
            rN = N - sNo
            N_index = find_index_rough(B[:, 0], rN)
            rM = B[N_index, 1]
            ALW_M2s[in2 - 1] = rM
        # 軸力がsNo以上sNo以下であるとき
        if -1 * sNo <= N and N <= sNo:
            N_index = find_index_rough(B[:, 0], 0)
            rM = B[N_index, 1]
            sig_b = sf[timecase - 1, 0] - abs(N) / sAe
            ALW_M2s[in2 - 1] = rM + max(0, sig_b * sZy)
        # 軸力がrNt以下であるとき
        if N < -1 * sNo:
            rN = N + sNo
            N_index = find_index_rough(B[:, 0], rN)
            rM = B[N_index, 1]
            ALW_M2s[in2 - 1] = rM

        ALW_M2 = np.max(np.vstack([ALW_M2s, ALW_M2r]), axis=0)

    ALW_M2 = (ALW_M2 / 10 ** 6)
    ALW_N2 = (ALW_N2 / 10 ** 3)
    # % plot(ALW_M2,ALW_N2,'b') / plot(sM/10^6,ALW_N2/10^3,'b:') 描画は省略
    # % toc / title(...) は省略
    return ALW_N1, ALW_M1, ALW_N2, ALW_M2


# ===========================================================================
# SA_SRC4column_HMD.m (SRC角柱: 許容N-M曲線, HMD式・実務利用版)
# ===========================================================================

def SA_SRC4column_HMD(Form, sForm, steelbar, HOOP, Fc, SN, timecase):
    """SA_SRC4column_HMD.m の逐語移植 (SRC角柱の許容N-M曲線, HMD式).

    引数構成は SA_SRC4column_AIJ と同じ。
    Form=[b,D](mm), sForm=[1,sH,sB,tw,tf,0,0](H鋼内蔵)/[2,...](Cross-H),
    steelbar=[1,総本数,径D,nv,nh,SD] / [2,総本数,径D,ns,nss,SD,dd],
    HOOP=[径D,pitch,SD,cover], Fc=[Fc,種別], SN=鋼種,
    timecase=1(長期)/10以上(短期)。
    返り値 (ALW_N1[kN], ALW_M1[kNm], ALW_N2[kN], ALW_M2[kNm]) 各1次元ndarray
    (ALW_M1/M2 は smooth(...,100,'sgolay') 相当で平滑化済み)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    # % tic 計測は省略

    # 長短期設定
    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # 外形情報
    b = Form[0]
    D = Form[1]

    # 内蔵鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sZy = steelForm(sForm, 7) * 1000  # mm3
    sIx = steelForm(sForm, 2) * 10000  # mm4  # noqa: F841 (MATLAB同様未使用)
    sIy = steelForm(sForm, 3) * 10000  # mm4  # noqa: F841 (MATLAB同様未使用)

    # 配筋情報
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[3]

    if steelbar[0] == 1:
        num = steelbar[1]
        di_main = steelbar[2]
        nv = int(steelbar[3])
        nh = steelbar[4]
        SD_main = steelbar[5]
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = (D - 2 * dc) / (nv - 1)
        y = np.zeros(nv)
        for i in range(1, nv + 1):
            y[i - 1] = dc + (i - 1) * dd
        if (nh - 1) * 2 + (nv - 1) * 2 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_SRC4column_HMD)')  # MATLAB: stop
    elif steelbar[0] == 2:
        num = steelbar[1]
        di_main = steelbar[2]
        ns = steelbar[3]  # noqa: F841 (MATLAB同様未使用)
        nss = int(steelbar[4])
        SD_main = steelbar[5]
        if 8 * nss - 4 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_SRC4column_HMD)')  # MATLAB: stop
        nv = 2 * nss
        nh = 2 * nss
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = steelbar[6]
        y = np.zeros(nv)
        for i in range(1, nss + 1):
            y[i - 1] = dc + (i - 1) * dd
        for i in range(nss + 1, nv + 1):
            y[i - 1] = D - dc - (nv - i) * dd
    else:
        raise ValueError('配筋情報エラー (SA_SRC4column_HMD)')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    # 許容応力度関係
    # 鉄骨量に対するコンクリート圧縮許容応力度の低減 (圧縮側鉄骨量の算定)
    if sForm[0] == 1:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
        stype = 'H-'  # noqa: F841
    elif sForm[0] == 2:
        sAf = max(sForm[2] * sForm[4], sForm[6] * sForm[8])
        ps = sAf / (b * D)
        sb = np.max(sForm[1:3])
        sD = np.max(sForm[1:3])
        max_t = np.maximum(sForm[3:5], sForm[7:9])  # MATLAB: max(A,B)は要素毎(2要素)
        stype = '2H-'  # noqa: F841
    else:
        raise ValueError('現在H鋼/十字形鋼以外未対応 (SA_SRC4column_HMD)')  # MATLAB: stop

    f_c = ALST_RC_AIJ(Fc) * (1 - 15 * ps)  # 鉄骨量による低減されたRC許容応力度
    mfc = ALST_steelbar_KJ([di_main, SD_main])  # 柱主筋の許容応力度
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                np.atleast_1d(max_t).ravel()]))  # 鉄骨の許容応力度

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    if steelbar[0] == 1:
        dd2 = (b - 2 * dc) / (nh - 1)
    elif steelbar[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    if check_dd >= 1.5 * di_main and check_dd >= 25:
        pass
    else:
        raise ValueError('鉄筋あき不足 (SA_SRC4column_HMD)')  # MATLAB: stop
    # その１：主筋の規定
    if num >= 4 and di_main >= 13:
        pass
    else:
        raise ValueError('主筋規定外 (SA_SRC4column_HMD)')  # MATLAB: stop
    # その２：主筋断面積の規定
    if (num * a + sAe) / (b * D) >= 0.8 / 100:
        pass
    else:
        raise ValueError('主筋断面積不足（0.8％未満） (SA_SRC4column_HMD)')  # MATLAB: stop
    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        raise ValueError('帯筋間隔NG（0.8％未満） (SA_SRC4column_HMD)')  # MATLAB: stop
    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    if aw / (max(b, D) * pitch) >= 0.2 / 100:
        pass
    else:
        print("ERROR = 'せん断補強筋比不足（0.2％未満）'")  # MATLAB: ERROR表示のみ(%stop)
    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        raise ValueError('鉄筋かぶり厚再検討（40mm未満） (SA_SRC4column_HMD)')  # MATLAB: stop
    # その６：鉄骨かぶりあつ
    if min((b - sb) / 2, (D - sD) / 2) >= 50:
        pass
    else:
        raise ValueError('鉄骨かぶり厚再検討（50mm未満） (SA_SRC4column_HMD)')  # MATLAB: stop

    HOOP = np.concatenate([HOOP[0:2], [2, 2], HOOP[2:4]])

    # 以下SRC断面の検定（強軸周り用　10-12式）
    # RC部分の連成させた断面算定
    # コンクリート部分の最大負担圧縮力
    rNc = r_N_c(b, D, a, num, sAe, n, f_c, mfc, timecase2)
    # コンクリート部分の最大負担引張力
    rNt = -1 * a * num * mfc[timecase2 - 1, 0]
    # 鉄骨部分の純曲げモーメント
    sMo = sZx * sf[timecase2 - 1, 0]
    sNo = sf[timecase2 - 1, 0] * sAe

    N_range = np.concatenate([_colon(-1 * sNo + rNt, 100000, sNo + rNc), [sNo + rNc]])
    N_time = len(N_range)

    ALW_N1 = np.zeros(N_time)
    ALW_M1 = np.zeros(N_time)
    for in1 in range(1, N_time + 1):
        N = N_range[in1 - 1]
        ALW_N1[in1 - 1] = N
        # 軸力がrNc以上の時(N-rNcを鉄骨が負担する）
        if N >= rNc:
            sN = N - rNc
            sig_b = sf[timecase2 - 1, 0] - sN / sAe
            ALW_M1[in1 - 1] = max(0, sig_b * sZx)
        # 軸力がrNt以上rNc以下であるとき（残りのRCの許容曲げと鉄骨の曲げ耐力の和）
        if rNt <= N and N <= rNc:
            M_AL, maxN = SA_RC4_SRC_HMD(N_range[in1 - 1] / 1000, Form, steelbar,
                                        HOOP, Fc, timecase, f_c)
            ALW_M1[in1 - 1] = M_AL + sMo
        # 軸力がrNt以下であるとき（残りの軸力をSが負担して，余裕分で曲げを負担する）
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase2 - 1, 0] - sN / sAe
            ALW_M1[in1 - 1] = max(0, sig_b * sZx)

    ALW_M11 = np.zeros(N_time)
    for in1 in range(1, N_time + 1):
        N = N_range[in1 - 1]
        # 軸力がsNo以上の時(N-sNoをRCが負担する）
        if N >= sNo:
            rN = N - sNo
            M_AL, maxN = SA_RC4_SRC_HMD(rN / 1000, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            ALW_M11[in1 - 1] = M_AL
        # 軸力が-sNo以上sNo以下であるとき
        if -1 * sNo <= N and N <= sNo:
            M_AL, maxN = SA_RC4_SRC_HMD(0, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            sig_b = sf[timecase2 - 1, 0] - abs(N) / sAe
            ALW_M11[in1 - 1] = max(0, sig_b * sZx) + M_AL
        # 軸力が-sNo以下であるとき（残りの軸力をRCが負担して，余裕分で曲げを負担する）
        if N < -1 * sNo:
            rN = abs(N) - abs(sNo)
            M_AL, maxN = SA_RC4_SRC_HMD(-rN / 1000, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            ALW_M11[in1 - 1] = M_AL

    # % hold on 描画は省略
    ALW_N1 = ALW_N1 / 10 ** 3
    ALW_M11 = ALW_M11 / 10 ** 6
    ALW_M1 = ALW_M1 / 10 ** 6
    ALW_M1 = np.max(np.vstack([ALW_M1, ALW_M11]), axis=0)
    ALW_M1 = _smooth_sgolay(ALW_M1, 100)  # smooth(ALW_M1,100,'sgolay')
    # % plot(ALW_M1,ALW_N1,'k') 描画は省略

    # 以下SRC断面の検定（弱軸周り用　13-15式）
    Form2 = np.array([Form[1], Form[0]])
    steelbar2 = np.array([steelbar[0], steelbar[1], steelbar[2],
                          steelbar[4], steelbar[3], steelbar[5]])

    # 鉄骨部分の純曲げモーメント
    sMo = sZy * sf[timecase2 - 1, 0]

    N_range = np.concatenate([_colon(-1 * sNo + rNt, 100000, sNo + rNc), [sNo + rNc]])
    N_time = len(N_range)

    ALW_N2 = np.zeros(N_time)
    ALW_M2 = np.zeros(N_time)
    for in2 in range(1, N_time + 1):
        N = N_range[in2 - 1]
        ALW_N2[in2 - 1] = N
        # 軸力がsNc以上の時(N-sNcをRCが負担する）
        if N >= rNc:
            sN = N - rNc
            sig_b = sf[timecase2 - 1, 0] - sN / sAe
            ALW_M2[in2 - 1] = max(0, sig_b * sZy)
        # 軸力がrNt以上rNc以下であるとき（残りのRCの許容曲げと鉄骨の曲げ耐力の和）
        if rNt <= N and N <= rNc:
            M_AL, maxN = SA_RC4_SRC_HMD(N_range[in2 - 1] / 1000, Form2, steelbar2,
                                        HOOP, Fc, timecase, f_c)
            ALW_M2[in2 - 1] = M_AL + sMo
        # 軸力がrNt以下であるとき
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase2 - 1, 0] - sN / sAe
            ALW_M2[in2 - 1] = max(0, sig_b * sZy)

    ALW_M22 = np.zeros(N_time)
    for in2 in range(1, N_time + 1):
        N = N_range[in2 - 1]
        # 軸力がsNo以上の時(N-sNoをRCが負担する）
        # (注: MATLAB原典はここで Form/steelbar (強軸用) を使っている)
        if N >= sNo:
            rN = N - sNo
            M_AL, maxN = SA_RC4_SRC_HMD(rN / 1000, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            ALW_M22[in2 - 1] = M_AL
        # 軸力が-sNo以上sNo以下であるとき
        if -1 * sNo <= N and N <= sNo:
            M_AL, maxN = SA_RC4_SRC_HMD(0, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            sig_b = sf[timecase2 - 1, 0] - abs(N) / sAe
            ALW_M22[in2 - 1] = max(0, sig_b * sZy) + M_AL
        # 軸力が-sNo以下であるとき
        if N < -1 * sNo:
            rN = abs(N) - abs(sNo)
            M_AL, maxN = SA_RC4_SRC_HMD(-rN / 1000, Form, steelbar, HOOP, Fc,
                                        timecase, f_c)
            ALW_M22[in2 - 1] = M_AL

    ALW_N2 = ALW_N2 / 10 ** 3

    ALW_M22 = ALW_M22 / 10 ** 6
    ALW_M2 = ALW_M2 / 10 ** 6
    ALW_M2 = np.max(np.vstack([ALW_M2, ALW_M22]), axis=0)
    ALW_M2 = _smooth_sgolay(ALW_M2, 100)  # smooth(ALW_M2,100,'sgolay')

    # % plot(ALW_M2,ALW_N2,'m') / toc / title(...) 描画・計測は省略
    return ALW_N1, ALW_M1, ALW_N2, ALW_M2


# ===========================================================================
# SA_L_SRC4column_AIJ.m (座屈考慮のSRC長柱N-M曲線: 原典は実行不能)
# ===========================================================================

def SA_L_SRC4column_AIJ(lk, N_range, Form, sForm, steelbar, HOOP, Fc, SN, timecase):
    """SA_L_SRC4column_AIJ.m の逐語移植 (座屈考慮SRC角柱の許容N-M曲線).

    lk=[lkx,lky](mm 座屈長), N_range: 検定軸力列[N]。他はSA_SRC4column_AIJと同じ。
    注意: 内部で呼ぶ L_rNc.m に無条件stopがあるため、MATLAB原典と同様に
    このルーチンは必ず途中で停止する (L_rNc内で例外送出)。
    """
    lk = np.asarray(lk, dtype=float).ravel()
    N_range = np.asarray(N_range, dtype=float).ravel()
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    # % tic 計測は省略

    N_time = len(N_range)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        rv = 1.5
        sv = 1.0
    elif timecase == 1:
        timecase = 1
        rv = 3.0
        sv = 1.5
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # SRC断面外形情報
    b = Form[0]
    D = Form[1]
    cIx = b * D ** 3 / 12
    cIy = D * b ** 3 / 12

    # 内蔵鉄骨情報
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sZy = steelForm(sForm, 7) * 1000  # mm3  # noqa: F841
    sIx = steelForm(sForm, 2) * 10000  # mm4
    sIy = steelForm(sForm, 3) * 10000  # mm4
    ixs = steelForm(sForm, 4) * 10  # mm
    iys = steelForm(sForm, 5) * 10  # mm

    # 配筋情報
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]  # noqa: F841
    cover_depth = HOOP[3]

    if steelbar[0] == 1:
        num = steelbar[1]
        di_main = steelbar[2]
        nv = int(steelbar[3])
        nh = steelbar[4]
        SD_main = steelbar[5]
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = (D - 2 * dc) / (nv - 1)
        y = np.zeros(nv)
        for i in range(1, nv + 1):
            y[i - 1] = dc + (i - 1) * dd
        if (nh - 1) * 2 + (nv - 1) * 2 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    elif steelbar[0] == 2:
        num = steelbar[1]
        di_main = steelbar[2]
        ns = steelbar[3]  # noqa: F841
        nss = int(steelbar[4])
        SD_main = steelbar[5]
        if 8 * nss - 4 == num:
            pass
        else:
            raise ValueError('配筋情報ミス (SA_L_SRC4column_AIJ)')  # MATLAB: stop
        nv = 2 * nss
        nh = 2 * nss
        dc = (cover_depth + outf_steelbar_JIS(di_support)
              + outf_steelbar_JIS(di_main) * 0.5)
        dd = steelbar[6]
        y = np.zeros(nv)
        for i in range(1, nss + 1):
            y[i - 1] = dc + (i - 1) * dd
        for i in range(nss + 1, nv + 1):
            y[i - 1] = D - dc - (nv - i) * dd
    else:
        raise ValueError('配筋情報エラー (SA_L_SRC4column_AIJ)')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    # 許容応力度関係
    if sForm[0] == 1:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
        stype = 'H-'  # noqa: F841
    elif sForm[0] == 2:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)
        sb = np.max(sForm[1:3])
        sD = np.max(sForm[1:3])
        max_t = np.max(sForm[3:5])
        stype = '2H-'  # noqa: F841
    else:
        raise ValueError('現在H鋼/十字形鋼以外未対応 (SA_L_SRC4column_AIJ)')  # MATLAB: stop

    f_c = ALST_RC_AIJ(Fc) * (1 - 15 * ps)
    mfc = ALST_steelbar_KJ([di_main, SD_main])
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                np.atleast_1d(max_t)]))
    s_f_c = sfc(ixs, iys, [lk[0], lk[1]], SN, max_t)  # 鉄骨の圧縮許容応力度

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    cE = n[0]
    n = n[1]
    sE = 205000

    # 構造細則(計算外規定のチェック)
    if steelbar[0] == 1:
        dd2 = (b - 2 * dc) / (nh - 1)
    elif steelbar[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    if check_dd >= 1.5 * di_main and check_dd >= 25:
        pass
    else:
        raise ValueError('鉄筋あき不足 (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    if num >= 4 and di_main >= 13:
        pass
    else:
        raise ValueError('主筋規定外 (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    if (num * a + sAe) / (b * D) >= 0.8 / 100:
        pass
    else:
        raise ValueError('主筋断面積不足（0.8％未満） (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        raise ValueError('帯筋間隔NG（0.8％未満） (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    aw = Area_steelbar(di_support, 2)
    if aw / (max(b, D) * pitch) >= 0.2 / 100:
        pass
    else:
        pass  # 原典: ERROR代入もstopもコメントアウト
    if cover_depth >= 40:
        pass
    else:
        raise ValueError('鉄筋かぶり厚再検討（40mm未満） (SA_L_SRC4column_AIJ)')  # MATLAB: stop
    if min((b - sb) / 2, (D - sD) / 2) >= 50:
        pass
    else:
        raise ValueError('鉄骨かぶり厚再検討（50mm未満） (SA_L_SRC4column_AIJ)')  # MATLAB: stop

    # RC部分の連成させた断面算定
    maxN = min((Form[0] * Form[1] + (n - 1) * num * a) * f_c[timecase - 1, 0],
               ((Form[0] * Form[1] - num * a) / n + num * a) * (mfc[timecase - 1, 0]))

    cut_plus = -np.inf
    j = 1
    Nplus = []
    Mplus = []
    Xnplus = []
    # 圧縮側のplot (whileの上限jなし: MATLAB同様)
    while cut_plus < 15:
        _N, _M, _X = subSRC4column_AIJ(j, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nplus.append(_N)
        Mplus.append(_M)
        Xnplus.append(_X)
        if Nplus[j - 1] == 0 and Mplus[j - 1] == 0:
            if j == 1:
                raise IndexError('subSRC4column_AIJ(1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nplus[j - 1] = Nplus[j - 2]
            Mplus[j - 1] = Mplus[j - 2]
        cut_plus = np.float64(max(Nplus)) / np.float64(min(Nplus))
        if j > 300:
            if Mplus[j - 1] < Mplus[j - 201]:
                pass
            else:
                cut_plus = 10
        j = j + 1

    # 引張側のplot
    jj = 1
    cut = -np.inf
    Nminus = []
    Mminus = []
    Xnminus = []
    while cut < 7:
        _N, _M, _X = subSRC4column_AIJ(-1 * jj, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nminus.append(_N)
        Mminus.append(_M)
        Xnminus.append(_X)
        if Nminus[jj - 1] == 0 and Mminus[jj - 1] == 0:
            if jj == 1:
                raise IndexError('subSRC4column_AIJ(-1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nminus[jj - 1] = Nminus[jj - 2]
            Mminus[jj - 1] = Mminus[jj - 2]
        cut = np.float64(min(Nminus)) / np.float64(max(Nminus))
        jj = jj + 1

    # Nminus(Mminus) Nplus(Mplus)の間を線形補間
    A = np.array([
        np.concatenate([[maxN], Nminus,
                        _colon(Nminus[jj - 2], (Nplus[j - 2] - Nminus[jj - 2]) / 100, Nplus[j - 2]),
                        Nplus]),
        np.concatenate([[0], Mminus,
                        _colon(Mminus[jj - 2], (Mplus[j - 2] - Mminus[jj - 2]) / 100, Mplus[j - 2]),
                        Mplus])])
    A = A.T
    A = A[np.argsort(A[:, 0], kind='stable')]  # sortrows(A,1)

    # 以下座屈考慮SRC断面の検定（強軸周り用　32-33式）
    # SRC断面の座屈耐力
    Nkx = math.pi ** 2 / lk[0] ** 2 * (cE * cIx / 5 + sE * sIx)
    Nky = math.pi ** 2 / lk[1] ** 2 * (cE * cIy / 5 + sE * sIy)
    Nk = min(Nkx, Nky)

    # RC断面の座屈耐力
    rNkx = 2 * math.pi ** 2 / lk[0] ** 2 * (cE * cIx / 5)
    rNky = 2 * math.pi ** 2 / lk[1] ** 2 * (cE * cIy / 5)
    rNk = min(rNkx, rNky)

    # RC断面の許容引張耐力
    rNt = -1 * a * num * mfc[timecase - 1, 0]

    # RC断面のみでの最低5%の偏心を考慮した許容圧縮力
    rNc = L_rNc(rNk, rv, A, max(b, D))  # 注: L_rNc内の無条件stopで必ず例外

    # 鉄骨の純曲げ時の許容曲げモーメント
    sMo = sZx * sf[timecase - 1, 0]

    ALW_N1 = np.zeros(N_time)
    ALW_M1 = np.zeros(N_time)
    for in1 in range(1, N_time + 1):
        N = N_range[in1 - 1]
        ALW_N1[in1 - 1] = N
        # 軸力がrNc以上の時(N-rNcを鉄骨が負担する）33式
        if N >= rNc:
            sN = N - rNc
            # 鉄骨のfbは引張強度ftを用いる．
            sig_b = sf[timecase - 1, 0] * (1 - sN / sAe / s_f_c[timecase - 1])
            ALW_M1[in1 - 1] = max(0, sig_b * sZx) * max(0, (1 - rv * rNc / Nk))
        # 軸力が0以上rNc以下であるとき 32式
        if 0 < N and N <= rNc:
            r_delta = 1 / (1 - rv * N / rNk)
            N_index = find_index_rough(A[:, 0], N)
            rM = A[N_index, 1] / r_delta
            ALW_M1[in1 - 1] = rM + sMo * (1 - rv * N / Nk)
        # 軸力が0以下rNt以上であるとき 10式
        if rNt <= N and N <= 0:
            N_index = find_index_rough(A[:, 0], N)
            rM = A[N_index, 1]
            ALW_M1[in1 - 1] = rM + sMo
        # 軸力がrNt以下であるとき 12式
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M1[in1 - 1] = max(0, sig_b * sZx)

    # % plot(...) x4 描画は省略
    # % hold on

    # 以下SRC断面の検定（弱軸周り用　34-35式）
    sNc = sAe * s_f_c[timecase - 1]
    sNt = -1 * sAe * sf[timecase - 1, 0]

    N0_index = find_index_rough(A[:, 0], 0)
    rMo = A[N0_index, 1]
    print('rMo =', rMo)  # MATLAB: セミコロン無しの表示
    # ここで本当はRCの純曲げモーメント算定プログラムがあるとよいのだが
    # sM=zeros(N_time) は描画専用 (plotコメントアウト済) のため1次元で保持
    sM = np.zeros(N_time)

    ALW_N2 = np.zeros(N_time)
    ALW_M2 = np.zeros(N_time)
    for in2 in range(1, N_time + 1):
        N = N_range[in2 - 1]
        ALW_N2[in2 - 1] = N
        # 軸力がsNc以上の時(N-sNcをRCが負担する）
        if N >= sNc:
            sM[in2 - 1] = 0
            rN = N - sNc
            r_delta = 1 / (1 - rv * rN / rNk)
            Nr_index = find_index_rough(A[:, 0], rN)
            rM = A[Nr_index, 1] / r_delta
            ALW_M2[in2 - 1] = rM * max(0, (1 - sv * sNc / Nk))
        # 軸力が0以上sNc以下であるとき
        if 0 < N and N <= sNc:
            sig_b = sf[timecase - 1, 0] * (1 - abs(N) / sAe / s_f_c[timecase - 1])
            sM[in2 - 1] = sig_b * sZx
            ALW_M2[in2 - 1] = sM[in2 - 1] + rMo * max(0, (1 - sv * N / Nk))
        # 軸力がsNt以上0以下であるとき
        if sNt <= N and N <= 0:
            sig_b = sf[timecase - 1, 0] - abs(N) / sAe
            sM[in2 - 1] = max(0, sig_b * sZx)
            ALW_M2[in2 - 1] = sM[in2 - 1] + rMo
        # 軸力がsNt以下であるとき
        if N < sNt:
            sM[in2 - 1] = 0
            rN = -1 * abs(sNt - N)
            if rN < np.min(A[:, 0]):
                rM = 0
            else:
                Nr_index = find_index_rough(A[:, 0], rN)
                rM = A[Nr_index, 1]
            ALW_M2[in2 - 1] = rM

    # % plot(...) / toc / title(...) 描画・計測は省略
    return ALW_N1, ALW_M1, ALW_N2, ALW_M2


# ===========================================================================
# subSRC_Rcolumn_AIJ.m (円形RC断面: 偏心e→許容N,Mの1点算定)
# ===========================================================================

def subSRC_Rcolumn_AIJ(e, Form, steelbar, HOOP, Fc, timecase, f_c):
    """subSRC_Rcolumn_AIJ.m の逐語移植 (円形柱: 偏心e時の許容N・M).

    Form=[D](mm), steelbar=[総本数,径D,SD], HOOP=[径D,pitch,SD,cover]。
    timecase は変換済み(1/2)。引数 f_c はMATLAB同様関数内で
    ALST_RC_AIJ(Fc) により上書きされる (原典どおり)。
    返り値 (ALW_N[N], ALW_M[N・mm], xn[mm])
    """
    Form = np.atleast_1d(np.asarray(Form, dtype=float)).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()

    # どこにも該当しなかった場合0を返す
    ALW_N = 0.0
    ALW_M = 0.0
    xn = 0.0

    # 外形情報など
    D = Form[0]
    r = Form[0] / 2
    num = steelbar[0]
    di_main = steelbar[1]
    SD_main = steelbar[2]
    di_support = HOOP[0]
    pitch = HOOP[1]  # noqa: F841 (MATLAB同様未使用)
    SD_support = HOOP[2]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[3]

    a = Area_steelbar(di_main, 1)

    f_c = ALST_RC_AIJ(Fc)  # MATLAB原典どおり引数f_cを上書き
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    if num >= 8:
        pass
    else:
        pass  # 原典: ERROR代入もstopもコメントアウト

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 柱主筋の重心の縁距離
    dc = (cover_depth + outf_steelbar_JIS(di_support)
          + outf_steelbar_JIS(di_main) * 0.5)
    rr = r - dc

    # 鉄筋間隔チェック
    if (2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 1.5 * di_main
            and 2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 25):
        pass
    else:
        print("ERROR = '配筋ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

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
            In = (theta * (1.0 / 4 + math.cos(theta) ** 2)  # noqa: F841
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
    else:
        Ae = n * num * a
        Ig = n * pg * rr ** 2 * math.pi * r ** 2 / 2
        xnout = Ig / (Ae * e) + g

        if xnout > 0:
            theta = find_e(e, r, rr, pg, n)
            xnin = r * (1 - math.cos(theta))

            Sn = (1.0 / 3 * math.sin(theta) * (2 + math.cos(theta) ** 2)
                  - theta * math.cos(theta)
                  - n * pg * math.pi * math.cos(theta)) * r ** 3
            In = (theta * (1.0 / 4 + math.cos(theta) ** 2)  # noqa: F841
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

            ALW_N = max([N1, N2, N3])
            ALW_M = ALW_N * e
        elif xnout <= 0:
            # 中立軸が断面外にある時 (コンクリート無効, 鉄筋の引張降伏で決まる)
            Sn = (xnout - g) * Ae
            ALW_N = rfc[timecase - 1, 0] * Sn / (n * (D - xnout - dc))
            ALW_M = ALW_N * e
            xn = xnout

    return ALW_N, ALW_M, xn


# ===========================================================================
# SA_SRC_Rcolumn_AIJ.m (鋼管被覆SRC円形柱: 許容N-M曲線)
# ===========================================================================

def SA_SRC_Rcolumn_AIJ(Form, sForm, steelbar, HOOP, Fc, SN, timecase):
    """SA_SRC_Rcolumn_AIJ.m の逐語移植 (鋼管被覆SRC円形柱の許容N-M曲線).

    Form=[D](RC外径mm), sForm=[3,鋼管D,鋼管t](mm),
    steelbar=[総本数,径D,SD], HOOP=[径D,pitch,SD,cover],
    Fc=[Fc,種別], SN=鋼種, timecase=1(長期)/10以上(短期)。
    返り値 (ALW_N[kN], ALW_M[kNm]) 各1次元ndarray
    (smooth(...,500,'sgolay') 相当で平滑化済み)
    """
    Form = np.atleast_1d(np.asarray(Form, dtype=float)).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    # % tic 計測は省略

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # 外形情報
    D = Form[0]
    r = Form[0] / 2  # noqa: F841 (MATLAB同様未使用)
    ts = sForm[2]  # noqa: F841 (title表示用)
    # 鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sIx = steelForm(sForm, 2) * 10000  # mm4  # noqa: F841 (MATLAB同様未使用)

    # 配筋情報
    num = steelbar[0]
    di_main = steelbar[1]
    SD_main = steelbar[2]
    di_support = HOOP[0]  # noqa: F841 (MATLAB同様未使用)
    pitch = HOOP[1]  # noqa: F841
    SD_support = HOOP[2]  # noqa: F841
    cover_depth = HOOP[3]  # noqa: F841

    a = Area_steelbar(di_main, 1)

    # 許容応力度関係
    f_c = ALST_RC_AIJ(Fc)  # RC許容応力度
    rfc = ALST_steelbar_KJ([di_main, SD_main])  # 柱主筋の許容応力度
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                [sForm[1]]]))  # MATLAB: ALST_S([SN sForm(2)])

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック): 原典に記載なし

    # 以下SRC断面の検定
    # RC部分の連成させた断面算定 (鉄骨部分はRCありと置換)
    # (注: MATLAB原典2項目の (D-(num*a+sAe))/n は面積でなく直径Dを使っており
    #  次元が合わない。maxNはその後未使用のためそのまま再現)
    maxN = min((D ** 2 / 4 * math.pi + (n - 1) * (num * a + sAe)) * f_c[timecase - 1, 0],
               ((D - (num * a + sAe)) / n + (num * a + sAe)) * (rfc[timecase - 1, 0]))  # noqa: F841

    cut_plus = -np.inf
    j = 1
    Nplus = []
    Mplus = []
    Xnplus = []
    # 圧縮側のplot (whileの上限jなし: MATLAB同様)
    while cut_plus < 15:
        _N, _M, _X = subSRC_Rcolumn_AIJ(j, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nplus.append(_N)
        Mplus.append(_M)
        Xnplus.append(_X)
        if Nplus[j - 1] == 0 and Mplus[j - 1] == 0:
            if j == 1:
                raise IndexError('subSRC_Rcolumn_AIJ(1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nplus[j - 1] = Nplus[j - 2]
            Mplus[j - 1] = Mplus[j - 2]
        cut_plus = np.float64(max(Nplus)) / np.float64(min(Nplus))
        if j > 300:
            if Mplus[j - 1] < Mplus[j - 201]:
                pass
            else:
                cut_plus = 10
        j = j + 1

    # Mplus=Mplus(find(Mplus)); Nplus=Nplus(find(Mplus));
    # (注: 2行目のfindはフィルタ後のMplusに対するもの=先頭len(Mplus)要素。原典どおり)
    Mplus = np.asarray(Mplus, dtype=float)
    Nplus = np.asarray(Nplus, dtype=float)
    Mplus = Mplus[np.nonzero(Mplus)[0]]
    Nplus = Nplus[np.nonzero(Mplus)[0]]

    # 引張側のplot
    jj = 1
    cut = -np.inf
    Nminus = []
    Mminus = []
    Xnminus = []
    while cut < 7:
        _N, _M, _X = subSRC_Rcolumn_AIJ(-1 * jj, Form, steelbar, HOOP, Fc, timecase, f_c)
        Nminus.append(_N)
        Mminus.append(_M)
        Xnminus.append(_X)
        if Nminus[jj - 1] == 0 and Mminus[jj - 1] == 0:
            if jj == 1:
                raise IndexError('subSRC_Rcolumn_AIJ(-1)が(0,0)を返した'
                                 ' (MATLAB: 添字0で実行時エラー)')
            Nminus[jj - 1] = Nminus[jj - 2]
            Mminus[jj - 1] = Mminus[jj - 2]
        cut = np.float64(min(Nminus)) / np.float64(max(Nminus))
        jj = jj + 1

    Mminus = np.asarray(Mminus, dtype=float)
    Nminus = np.asarray(Nminus, dtype=float)
    Mminus = Mminus[np.nonzero(Mminus)[0]]
    Nminus = Nminus[np.nonzero(Mminus)[0]]

    A = np.array([
        np.concatenate([Nminus,
                        _colon(Nminus[jj - 2], (Nplus[j - 2] - Nminus[jj - 2]) / 100, Nplus[j - 2]),
                        Nplus]),
        np.concatenate([Mminus,
                        _colon(Mminus[jj - 2], (Mplus[j - 2] - Mminus[jj - 2]) / 100, Mplus[j - 2]),
                        Mplus])])
    A = A.T
    A = A[np.argsort(A[:, 0], kind='stable')]  # 軸力でソート

    # コンクリート部分の最大負担圧縮力
    rNc = r_N_c_R(D, a, num, sAe, n, f_c, rfc, timecase)
    # コンクリート部分の最大負担引張力
    rNt = -1 * a * num * rfc[timecase - 1, 0]
    # 鉄骨部分の純曲げモーメント
    sMo = sZx * sf[timecase - 1, 0]
    sNo = sf[timecase - 1, 0] * sAe

    N_range = np.concatenate([_colon(-1 * sNo + rNt, 10000, sNo + rNc), [sNo + rNc]])
    N_time = len(N_range)

    ALW_N = np.zeros(N_time)
    ALW_M = np.zeros(N_time)
    for in1 in range(1, N_time + 1):
        N = N_range[in1 - 1]
        ALW_N[in1 - 1] = N
        # 軸力がrNc以上の時(N-rNcを鉄骨が負担する）
        if N >= rNc:
            sN = N - rNc
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M[in1 - 1] = max(0, sig_b * sZx)
        # 軸力がrNt以上rNc以下であるとき
        if rNt <= N and N <= rNc:
            N_index = find_index_rough(A[:, 0], N)
            rM = A[N_index, 1]
            ALW_M[in1 - 1] = rM + sMo
        # 軸力がrNt以下であるとき
        if N < rNt:
            sN = abs(rNt - N)
            sig_b = sf[timecase - 1, 0] - sN / sAe
            ALW_M[in1 - 1] = max(0, sig_b * sZx)

    # smoothing
    ALW_M = _smooth_sgolay(ALW_M, 500)  # smooth(ALW_M,500,'sgolay')
    ALW_N = _smooth_sgolay(ALW_N, 500)  # smooth(ALW_N,500,'sgolay')

    # % plot(A(:,2)/10^6,A(:,1)/10^3,'r:') 描画は省略
    ALW_M = ALW_M / 10 ** 6
    ALW_N = ALW_N / 10 ** 3
    # % hold on / plot(ALW_M,ALW_N,'r') / axis / toc / title(...) は省略
    return ALW_N, ALW_M


# ===========================================================================
# SRC4beam_ALWM.m (H鋼内蔵SRC角梁の許容曲げモーメント)
# ===========================================================================

def SRC4beam_ALWM(Form, sForm, steelbar_u, steelbar_d, HOOP, Fc, SN, timecase):
    """SRC4beam_ALWM.m の逐語移植 (H鋼内蔵SRC角梁の許容曲げ).

    Form=[b,D](mm), sForm=[1,sH,sB,tw,tf](mm),
    steelbar_u/d: 3行(i端/中央/j端)x5(or 6)列
      [段数(1or2), 1段目本数, 2段目本数, 径D, SD(, 2段目あきdd)],
    HOOP=[あばら筋径D, ピッチ, SD, かぶり], Fc=[Fc,種別], SN=鋼種,
    timecase=1(長期)/10以上(短期)。
    返り値 (ALW_M(3x2 [正曲げ許容M, 負曲げ許容M(負値)] kNm), sMo(kNm))
    """
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar_u = np.atleast_2d(np.asarray(steelbar_u, dtype=float))
    steelbar_d = np.atleast_2d(np.asarray(steelbar_d, dtype=float))
    HOOP = np.asarray(HOOP, dtype=float).ravel()

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print("ERROR = '長短期設定ミス'")  # MATLAB: ERROR代入表示のみ(stopなし)

    # 外形情報
    b = Form[0]
    D = Form[1]

    # 内蔵鉄骨情報
    sAe = steelForm(sForm, 1) * 100  # mm2  # noqa: F841 (MATLAB同様未使用)
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sZy = steelForm(sForm, 7) * 1000  # mm3  # noqa: F841
    sIx = steelForm(sForm, 2) * 10000  # mm4  # noqa: F841
    sIy = steelForm(sForm, 3) * 10000  # mm4  # noqa: F841

    # 配筋情報
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]  # noqa: F841 (MATLAB同様未使用)
    cover_depth = HOOP[3]

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    SD_main = np.zeros((2, 3))
    num[0, :] = np.sum(steelbar_u[:, 1:3], axis=1)
    di_main[0, :] = steelbar_u[:, 3]
    SD_main[0, :] = steelbar_u[:, 4]
    num[1, :] = np.sum(steelbar_d[:, 1:3], axis=1)
    di_main[1, :] = steelbar_d[:, 3]
    SD_main[1, :] = steelbar_d[:, 4]

    # 梁の上端筋と下端筋の段数から重心距離の計算
    dd = np.zeros((2, 3)) + 1000  # 二段配筋の際には1000が書き換えられる．
    d_out = np.zeros((2, 3))
    d = np.zeros((2, 3))
    # 上端筋
    for i in range(1, 4):
        d_out[0, i - 1] = (cover_depth + outf_steelbar_JIS(di_support)
                           + outf_steelbar_JIS(di_main[0, i - 1]) * 0.5)
        if steelbar_u[i - 1, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[0, i - 1] = d_out[0, i - 1]
            if steelbar_u[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数） (SRC4beam_ALWM)')  # MATLAB: stop
        elif steelbar_u[i - 1, 0] == 2:
            # 主筋の重心距離（二段配筋）
            if steelbar_u.shape[1] == 6:
                dd[0, i - 1] = steelbar_u[i - 1, 5]
            elif steelbar_u.shape[1] == 5:
                dd[0, i - 1] = (excelround(max(di_main[0, i - 1] * 1.5, 25 * 1.5), 1)
                                + outf_steelbar_JIS(di_main[0, i - 1]))
            d[0, i - 1] = (d_out[0, i - 1] * (steelbar_u[i - 1, 1]) / num[0, i - 1]
                           + (d_out[0, i - 1] + dd[0, i - 1])
                           * (steelbar_u[i - 1, 2]) / num[0, i - 1])
        else:
            raise ValueError('配筋情報エラー（配筋段数） (SRC4beam_ALWM)')  # MATLAB: stop

    # 下端筋
    for i in range(1, 4):
        d_out[1, i - 1] = (cover_depth + outf_steelbar_JIS(di_support)
                           + outf_steelbar_JIS(di_main[1, i - 1]) * 0.5)
        if steelbar_d[i - 1, 0] == 1:
            d[1, i - 1] = d_out[1, i - 1]
            if steelbar_d[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数） (SRC4beam_ALWM)')  # MATLAB: stop
        elif steelbar_d[i - 1, 0] == 2:
            if steelbar_d.shape[1] == 6:
                dd[1, i - 1] = steelbar_d[i - 1, 5]
            elif steelbar_d.shape[1] == 5:
                # (注: 上端筋は max(di*1.5, 25*1.5) だがここは max(di*1.5, 25)。原典どおり)
                dd[1, i - 1] = (excelround(max(di_main[1, i - 1] * 1.5, 25), 1)
                                + outf_steelbar_JIS(di_main[1, i - 1]))
            d[1, i - 1] = (d_out[1, i - 1] * (steelbar_d[i - 1, 1]) / num[1, i - 1]
                           + (d_out[1, i - 1] + dd[1, i - 1])
                           * (steelbar_d[i - 1, 2]) / num[1, i - 1])
        else:
            raise ValueError('配筋情報エラー（配筋段数） (SRC4beam_ALWM)')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    rfc = [[None] * 3 for _ in range(2)]
    for i in range(2):
        for jx in range(3):
            rfc[i][jx] = ALST_steelbar_KJ([di_main[i, jx], SD_main[i, jx]])

    # 許容応力度関係
    # 鉄骨量に対するコンクリート圧縮許容応力度の低減 (圧縮側鉄骨量の算定)
    if sForm[0] == 1:
        sAf = steelForm(sForm, 10) * 100  # mm2
        ps = sAf / (b * D)  # noqa: F841 (梁では低減未使用: 原典どおり)
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
        stype = 'H-'  # noqa: F841
    else:
        raise ValueError('現在H鋼以外未対応 (SRC4beam_ALWM)')  # MATLAB: stop
    f_c = ALST_RC_AIJ(Fc)  # (原典コメントは「低減された」だが低減していない)
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                [max_t]]))

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    dd2 = (b - 2 * d_out) / (np.vstack([np.max(steelbar_u[:, 1:3], axis=1),
                                        np.max(steelbar_d[:, 1:3], axis=1)]) - 1)
    check_dd = dd - outf_steelbar_JIS(di_main)
    check_dd2 = dd2 - outf_steelbar_JIS(di_main)
    checkdd = np.minimum(check_dd, check_dd2)
    min_dd = np.maximum(1.5 * di_main, 25 * 1.25)
    judge_dd = checkdd - min_dd
    if np.min(np.min(judge_dd)) >= 0:
        pass
    else:
        pass  # 原典: 空分岐 (%stopのみ)

    # その１：主筋の規定
    if np.min(np.min(di_main)) >= 13:
        pass
    else:
        print("ERROR = '主筋規定外(D13以上，配筋段数については既に検討済)'")  # MATLAB: 表示のみ(%stop)

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b * D)
    pt = np.max(np.max(pt))
    if pt < 0.4 / 100:
        print('SRC断面：引張鉄筋断面積不足（0.4％未満）')
    else:
        pass

    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= max(D / 2, 250):
        pass
    elif di_support > 10 and pitch <= max(D / 2, 450):
        pass
    else:
        print('STRP筋間隔NG→STRPピッチ' + ('%15.0f' % pitch).strip()
              + 'mm，　STRP径：D' + ('%15.0f' % di_support).strip()
              + '，　梁せい：' + ('%15.0f' % D).strip() + 'mm')

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    if aw / (b * pitch) >= 0.2 / 100:
        pass
    else:
        print('せん断補強筋比不足（0.2％未満）　pw='
              + ('%15.2f' % (aw / (b * pitch) * 100)).strip()
              + '%→STRPピッチ' + ('%15.0f' % pitch).strip()
              + 'mm，　STRP径：D' + ('%15.0f' % di_support).strip()
              + '，　梁幅b：' + ('%15.0f' % b).strip() + 'mm')

    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        raise ValueError('鉄筋かぶり厚再検討（40mm未満） (SRC4beam_ALWM)')  # MATLAB: stop
    # その６：鉄骨かぶりあつ
    if min((b - sb) / 2, (D - sD) / 2) >= 50:
        pass
    else:
        raise ValueError('鉄骨かぶり厚再検討（50mm未満） (SRC4beam_ALWM)')  # MATLAB: stop

    # 以下SRC断面の検定（強軸周り用　10-12式）
    # 鉄骨部分の純曲げモーメント
    sMo = sZx * sf[timecase - 1, 0] / 10 ** 6

    # 複筋長方形梁の中立軸算定（その１・正曲げ）
    dc = d[0, :]
    dt = D - d[1, :]
    ac = a[0, :] * num[0, :]
    at = a[1, :] * num[1, :]

    xn1 = (np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2
                   + 2 / b * ((n - 1) * ac * dc + n * at * dt))
           - ((n - 1) * ac + n * at) / b)

    M1 = np.zeros((2, 3))
    M2 = np.zeros((2, 3))
    M3 = np.zeros((2, 3))

    # 許容曲げモーメントの算出
    # １：圧縮コンクリートの圧壊
    sig_c = f_c[timecase - 1, 0]
    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M1[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(3):
        if M1[0, i] == 0:
            M1[0, i] = np.inf

    # ２：引張鉄筋の降伏
    rfc_t = np.zeros(3)
    rfc_t[0] = rfc[1][0][timecase - 1, 0]
    rfc_t[1] = rfc[1][1][timecase - 1, 0]
    rfc_t[2] = rfc[1][2][timecase - 1, 0]
    sig_c = rfc_t * xn1 / (D - d_out[1, :] - xn1) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M2[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(3):
        if M2[0, i] == 0:
            M2[0, i] = np.inf

    # ３：圧縮鉄筋の降伏
    rfc_c = np.zeros(3)
    rfc_c[0] = rfc[0][0][timecase - 1, 0]
    rfc_c[1] = rfc[0][1][timecase - 1, 0]
    rfc_c[2] = rfc[0][2][timecase - 1, 0]
    sig_c = rfc_c * xn1 / (xn1 - d_out[0, :]) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M3[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(3):
        if M3[0, i] == 0:
            M3[0, i] = np.inf

    # 複筋長方形梁の中立軸算定（その２・負曲げ）
    dc = d[1, :]
    dt = D - d[0, :]
    ac = a[1, :] * num[1, :]
    at = a[0, :] * num[0, :]

    xn2 = (D - np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2
                       + 2 / b * ((n - 1) * ac * dc + n * at * dt))
           + ((n - 1) * ac + n * at) / b)

    # １：圧縮コンクリートの圧壊
    sig_c = f_c[timecase - 1, 0]
    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M1[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(3):
        if M1[1, i] == 0:
            M1[1, i] = np.inf

    # ２：引張鉄筋の降伏
    rfc_t = np.zeros(3)
    rfc_t[0] = rfc[0][0][timecase - 1, 0]
    rfc_t[1] = rfc[0][1][timecase - 1, 0]
    rfc_t[2] = rfc[0][2][timecase - 1, 0]
    sig_c = rfc_t * (D - xn2) / (xn2 - d_out[0, :]) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M2[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(3):
        if M2[1, i] == 0:
            M2[1, i] = np.inf

    # ３：圧縮鉄筋の降伏
    rfc_c = np.zeros(3)
    rfc_c[0] = rfc[1][0][timecase - 1, 0]
    rfc_c[1] = rfc[1][1][timecase - 1, 0]
    rfc_c[2] = rfc[1][2][timecase - 1, 0]
    sig_c = rfc_c * (D - xn2) / ((D - d_out[1, :]) - xn2) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M3[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(3):
        if M3[1, i] == 0:
            M3[1, i] = np.inf

    # 許容曲げモーメントの算出
    M = np.minimum(M1, M3)
    M = np.minimum(M, M2)
    ALW_M = M / 10 ** 6 + sMo
    ALW_M[1, :] = ALW_M[1, :] * -1

    ALW_M = ALW_M.T
    return ALW_M, sMo


# ===========================================================================
# SA_SRCbeamQratio.m (SRC梁の許容せん断力算定)
# ===========================================================================

def SA_SRCbeamQratio(Form, sForm, steelbar_u, steelbar_d, HOOP, Fc, SN,
                     stress, timecase, QL, qup_beam, ALW_M, RCQ=None):
    """SA_SRCbeamQratio.m の逐語移植 (SRC梁の許容せん断力・検定比).

    Form=[b,D](mm), sForm=[1,sH,sB,tw,tf],
    steelbar_u/d: 3x5(-7)列 [段数,1段目,2段目,径D,SD(,あき)],
    HOOP=[あばら筋径D, ピッチ, 本数, SD, かぶり] (5要素),
    Fc=[Fc,0], SN=鋼種, stress: 3x5 (kN,kNm),
    timecase: 変換済み(1=長期/2=短期), QL: 長期Qz 3要素(kN),
    qup_beam: 短期せん断割増率, ALW_M: 3x2 許容曲げ(kNm), RCQ: 未使用。
    返り値 (ratio_Q(3x2 [RC負担,鉄骨負担]), ALW_sQ, ALW_RCQ, qrc, alph, j,
            Mmax, Qmax)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar_u = np.atleast_2d(np.asarray(steelbar_u, dtype=float))
    steelbar_d = np.atleast_2d(np.asarray(steelbar_d, dtype=float))
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.asarray(QL, dtype=float).ravel()
    ALW_M = np.atleast_2d(np.asarray(ALW_M, dtype=float))

    # RC断面の外形情報[mm単位で入力]
    b = Form[0]
    D = Form[1]

    sZx = steelForm(sForm, 6) * 1000  # mm3
    sAw = steelForm(sForm, 11) * 100  # mm2
    if sForm[0] == 1:
        max_t = np.max(sForm[3:5])
    else:
        raise ValueError('現在H鋼以外未対応 (SA_SRCbeamQratio)')  # MATLAB: stop
    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                [max_t]]))
    sMo = sZx * sf[timecase - 1, 0] / 10 ** 6
    sRC = np.min(np.abs(ALW_M), axis=1) - sMo
    qrc = (np.abs(sRC) / np.min(np.abs(ALW_M), axis=1))

    # 帯筋情報を読み込み
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[3]
    cover_depth = HOOP[4]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, HOOP[2])

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    num[0, :] = np.sum(steelbar_u[:, 1:3], axis=1)
    di_main[0, :] = steelbar_u[:, 3]
    num[1, :] = np.sum(steelbar_d[:, 1:3], axis=1)
    di_main[1, :] = steelbar_d[:, 3]

    # 梁の上端筋と下端筋の段数から重心距離の計算
    dd = np.zeros((2, 3)) + 1000
    d_out = np.zeros((2, 3))
    d = np.zeros((2, 3))
    # 上端筋
    for i in range(1, 4):
        d_out[0, i - 1] = (cover_depth + outf_steelbar_JIS(di_support)
                           + outf_steelbar_JIS(di_main[0, i - 1]) * 0.5)
        if steelbar_u[i - 1, 0] == 1:
            d[0, i - 1] = d_out[0, i - 1]
            if steelbar_u[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数） (SA_SRCbeamQratio)')  # MATLAB: stop
        elif steelbar_u[i - 1, 0] == 2:
            if steelbar_u.shape[1] == 7:
                dd[0, i - 1] = steelbar_u[i - 1, 6]
            else:  # if size(steelbar_u,2)==6
                dd[0, i - 1] = (excelround(max(di_main[0, i - 1] * 1.5, 25 * 1.25), 1)
                                + outf_steelbar_JIS(di_main[0, i - 1]))
            d[0, i - 1] = (d_out[0, i - 1] * (steelbar_u[i - 1, 1]) / num[0, i - 1]
                           + (d_out[0, i - 1] + dd[0, i - 1])
                           * (steelbar_u[i - 1, 2]) / num[0, i - 1])
        else:
            raise ValueError('配筋情報エラー（配筋段数） (SA_SRCbeamQratio)')  # MATLAB: stop

    # 下端筋
    for i in range(1, 4):
        d_out[1, i - 1] = (cover_depth + outf_steelbar_JIS(di_support)
                           + outf_steelbar_JIS(di_main[1, i - 1]) * 0.5)
        if steelbar_d[i - 1, 0] == 1:
            d[1, i - 1] = d_out[1, i - 1]
            if steelbar_d[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数） (SA_SRCbeamQratio)')  # MATLAB: stop
        elif steelbar_d[i - 1, 0] == 2:
            if steelbar_d.shape[1] == 7:
                dd[1, i - 1] = steelbar_d[i - 1, 6]
            else:  # if size(steelbar_d,2)==6
                dd[1, i - 1] = (excelround(max(di_main[1, i - 1] * 1.5, 25 * 1.25), 1)
                                + outf_steelbar_JIS(di_main[1, i - 1]))
            d[1, i - 1] = (d_out[1, i - 1] * (steelbar_d[i - 1, 1]) / num[1, i - 1]
                           + (d_out[1, i - 1] + dd[1, i - 1])
                           * (steelbar_d[i - 1, 2]) / num[1, i - 1])
        else:
            raise ValueError('配筋情報エラー（配筋段数） (SA_SRCbeamQratio)')  # MATLAB: stop

    f_c = ALST_RC_AIJ(Fc)

    # 許容せん断力の算定（長期）
    if timecase == 1:
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(
                2, 4 / (1 + (Mmax / Qmax / np.max((D - d), axis=0) * 1000))))
        j = 0.875 * np.max(D - d, axis=0)
        ALW_RCQ = (b * j * (alph * f_c[0, 2]
                            + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002)
                            * wft[0, 1]) / 10 ** 3)
        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = qrc * np.abs(stress[:, 2]) / ALW_RCQ
        ratio_Q[:, 1] = (1 - qrc) * np.abs(stress[:, 2]) / (sAw * sf[timecase - 1, 1] / 10 ** 3)

        ALW_sQ = sAw * sf[timecase - 1, 1] / 10 ** 3

    if timecase >= 2:
        # せん断割増しから決まる設計用せん断力
        Qs = qup_beam * (stress[:, 2] - QL) + (QL)

        # 許容せん断力（短期）
        Mmax = np.max(np.abs(stress[:, 3]))  # kNm
        Qmax = np.max(np.abs(stress[:, 2]))  # kN
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(
                2, 4 / (1 + (Mmax / Qmax / np.max((D - d), axis=0) * 1000))))
        j = (7.0 / 8) * np.max(D - d, axis=0)
        ALW_RCQ = (b * j * (alph * f_c[1, 2]
                            + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002)
                            * wft[1, 1]) / 10 ** 3)
        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = qrc * Qs / ALW_RCQ
        ratio_Q[:, 1] = (1 - qrc) * Qs / (sAw * sf[timecase - 1, 1] / 10 ** 3)

        ALW_sQ = sAw * sf[timecase - 1, 1] / 10 ** 3

    return ratio_Q, ALW_sQ, ALW_RCQ, qrc, alph, j, Mmax, Qmax


# ===========================================================================
# SA_SRC_4Hcolumn_Qratio.m (H内蔵/Cross-H内蔵SRC角柱の許容せん断力算定)
# ===========================================================================

def SA_SRC_4Hcolumn_Qratio(Form, sForm, steelbar, HOOP, Fc, SN, stress,
                           timecase, QL, qup_src, ALW_NM1, ALW_NM2, RCQ=None):
    """SA_SRC_4Hcolumn_Qratio.m の逐語移植 (SRC角柱のせん断検定比).

    Form=[b,D](mm), sForm=[1,...](H内蔵)/[2,...](Cross-H),
    steelbar: SRCcolumns(:,2:6) (2番目=主筋径のみ使用),
    HOOP=[径D,pitch,SD,cover], stress: 3x5(kN,kNm),
    timecase: 変換済み(1/2), QL: 3x2 長期[Qy,Qz](kN), qup_src: 割増率,
    ALW_NM1/2: (N点数,2) 許容NM曲線 [kN,kNm]。RCQ: 未使用。
    返り値 (ratio_Q(3x2 [強軸,弱軸]), ALW_sQ1, ALW_RCQ1, qrc1, alph1, d1,
            Mmax1, Qmax1, ALW_sQ2, ALW_RCQ2, qrc2, alph2, d2, Mmax2, Qmax2, Qs)
    """
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.atleast_2d(np.asarray(QL, dtype=float))
    ALW_NM1 = np.atleast_2d(np.asarray(ALW_NM1, dtype=float))
    ALW_NM2 = np.atleast_2d(np.asarray(ALW_NM2, dtype=float))

    # RC断面の外形情報[mm単位で入力]
    b = Form[0]
    D = Form[1]
    Qs = []

    if sForm[0] == 1:
        sZx = steelForm(sForm, 6) * 1000  # mm3
        sZy = steelForm(sForm, 7) * 1000  # mm3
        sA1 = steelForm(sForm, 11) * 100  # mm2
        sA2 = steelForm(sForm, 10) * 100 * 2  # mm2
        max_t = np.max([sForm[3], sForm[4], sForm[6]])
        sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                    [max_t]]))
        sMo1 = sZx * sf[timecase - 1, 0] / 10 ** 6
        sRC1 = ALW_NM1[find_index_rough(ALW_NM1[:, 0], 0), 1] - sMo1
        qrc1 = (np.abs(sRC1) / ALW_NM1[find_index_rough(ALW_NM1[:, 0], 0), 1])

        sMo2 = sZy * sf[timecase - 1, 0] / 10 ** 6
        sRC2 = ALW_NM2[find_index_rough(ALW_NM2[:, 0], 0), 1] - sMo2
        qrc2 = (np.abs(sRC2) / ALW_NM2[find_index_rough(ALW_NM2[:, 0], 0), 1])
    elif sForm[0] == 2:  # crossH
        sZx = steelForm(sForm, 6) * 1000  # mm3
        sZy = steelForm(sForm, 7) * 1000  # mm3
        sA1 = steelForm(sForm, 11) * 100  # mm2
        sA2 = steelForm(sForm, 10) * 100  # mm2
        max_t = np.max([sForm[3], sForm[4], sForm[7], sForm[8]])
        sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                    [max_t]]))
        sMo1 = sZx * sf[timecase - 1, 0] / 10 ** 6
        sRC1 = ALW_NM1[find_index_rough(ALW_NM1[:, 0], 0), 1] - sMo1
        qrc1 = (np.abs(sRC1) / ALW_NM1[find_index_rough(ALW_NM1[:, 0], 0), 1])

        sMo2 = sZy * sf[timecase - 1, 0] / 10 ** 6
        sRC2 = ALW_NM2[find_index_rough(ALW_NM2[:, 0], 0), 1] - sMo2
        qrc2 = (np.abs(sRC2) / ALW_NM2[find_index_rough(ALW_NM2[:, 0], 0), 1])
    # (注: sForm(1)が1/2以外の場合、MATLAB同様 qrc等未定義のまま後段でエラー)

    # 帯筋情報を読み込み
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]
    cover_depth = HOOP[3]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, 2)

    # 主筋
    di_main = steelbar[1]

    # 有効せいの算出
    dc = (cover_depth + outf_steelbar_JIS(di_support)
          + outf_steelbar_JIS(di_main) * 0.5)
    d1 = D - dc
    d2 = b - dc

    # 許容応力度
    f_c = ALST_RC_AIJ(Fc)

    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        pass  # 原典: 空分岐
    # その４：せん断補強筋比の規定
    if min(np.atleast_1d(aw / (D * pitch))) >= 0.2 / 100:
        pass
    else:
        pass  # 原典: 空分岐

    if timecase == 1:
        # 強軸せん断
        Mmax1 = np.max(np.abs(stress[:, 3]))
        Qmax1 = np.max(np.abs(stress[:, 2]))
        if Qmax1 == 0:
            alph1 = 1
        else:
            alph1 = max(1, min(2, 4 / (1 + (Mmax1 / Qmax1 / d1 * 1000))))
        j = 0.875 * d1
        ALW_RCQ1 = b * j * (alph1 * f_c[0, 2]) / 10 ** 3
        ALW_sQ1 = sA1 * sf[timecase - 1, 1] / 10 ** 3

        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = np.maximum(qrc1 * np.abs(stress[:, 2]) / ALW_RCQ1,
                                   (1 - qrc1) * np.abs(stress[:, 2]) / (ALW_sQ1))

        # 弱軸せん断
        Mmax2 = np.max(np.abs(stress[:, 4]))
        Qmax2 = np.max(np.abs(stress[:, 1]))
        if Qmax2 == 0:
            alph2 = 1
        else:
            alph2 = max(1, min(2, 4 / (1 + (Mmax2 / Qmax2 / d2 * 1000))))

        j = 0.875 * d2
        ALW_RCQ2 = D * j * (alph2 * f_c[0, 2]) / 10 ** 3
        ALW_sQ2 = sA2 * sf[timecase - 1, 1] / 10 ** 3

        ratio_Q[:, 1] = np.maximum(qrc2 * np.abs(stress[:, 1]) / ALW_RCQ2,
                                   (1 - qrc2) * np.abs(stress[:, 1]) / (ALW_sQ2))

    if timecase == 2:
        # せん断割増しから決まる設計用せん断力
        Qs = qup_src * (stress[:, 1:3] - QL) + (QL)
        alph1 = []
        alph2 = []
        Mmax1 = []
        Qmax1 = []
        Mmax2 = []
        Qmax2 = []

        # 許容せん断力（短期）
        # 強軸せん断
        j = 0.875 * d1
        ALW_RCQ1 = (b * j * (f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (b * pitch))
                                                - 0.002) * wft[1, 1]) / 10 ** 3)
        ALW_sQ1 = sA1 * sf[timecase - 1, 1] / 10 ** 3

        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = np.maximum(qrc1 * np.abs(Qs[:, 1]) / ALW_RCQ1,
                                   (1 - qrc1) * np.abs(Qs[:, 1]) / ALW_sQ1)

        j = 0.875 * d2
        ALW_RCQ2 = (D * j * (f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (D * pitch))
                                                - 0.002) * wft[1, 1]) / 10 ** 3)
        ALW_sQ2 = sA2 * sf[timecase - 1, 1] / 10 ** 3

        ratio_Q[:, 1] = np.maximum(qrc2 * np.abs(Qs[:, 0]) / ALW_RCQ2,
                                   (1 - qrc2) * np.abs(Qs[:, 0]) / (ALW_sQ2))

    return (ratio_Q, ALW_sQ1, ALW_RCQ1, qrc1, alph1, d1, Mmax1, Qmax1,
            ALW_sQ2, ALW_RCQ2, qrc2, alph2, d2, Mmax2, Qmax2, Qs)


# ===========================================================================
# SA_SRC_Rcolumn_Qratio.m (鋼管被覆SRC円形柱の許容せん断力算定)
# ===========================================================================

def SA_SRC_Rcolumn_Qratio(Form, sForm, steelbar, HOOP, Fc, SN, stress,
                          timecase, QL, qup_src, ALW_NM, RCQ=None):
    """SA_SRC_Rcolumn_Qratio.m の逐語移植 (鋼管被覆SRC円柱のせん断検定比).

    Form=[D](mm), sForm=[3,鋼管D,鋼管t], steelbar: SRCcolumns(:,2:6)
    (2番目=主筋径のみ使用), HOOP=[径D,pitch,SD,cover],
    stress: 3x5(kN,kNm), timecase: 変換済み(1/2), QL: 3x2 長期[Qy,Qz](kN),
    qup_src: 割増率, ALW_NM: (N点数,2) 許容NM曲線 [kN,kNm]。RCQ: 未使用。
    返り値 (ratio_Q(3x2), ALW_sQ, ALW_RCQ, qrc, alph, d, Mmax, Qmax, Qs)
    """
    Form = np.atleast_1d(np.asarray(Form, dtype=float)).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.atleast_2d(np.asarray(QL, dtype=float))
    ALW_NM = np.atleast_2d(np.asarray(ALW_NM, dtype=float))

    # RC断面の外形情報[mm単位で入力]
    D = Form[0]
    Qs = []

    sZx = steelForm(sForm, 6) * 1000  # mm3
    sA = steelForm(sForm, 1) * 100  # mm2
    max_t = sForm[2]

    sf = ALST_S(np.concatenate([np.atleast_1d(np.asarray(SN, dtype=float)).ravel(),
                                [max_t]]))
    sMo = sZx * sf[timecase - 1, 0] / 10 ** 6
    sRC = ALW_NM[find_index_rough(ALW_NM[:, 0], 0), 1] - sMo
    qrc = (np.abs(sRC) / ALW_NM[find_index_rough(ALW_NM[:, 0], 0), 1])

    # 帯筋情報を読み込み
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]
    cover_depth = HOOP[3]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, 2)

    # 主筋
    di_main = steelbar[1]

    # 有効せいの算出
    dc = (cover_depth + outf_steelbar_JIS(di_support)
          + outf_steelbar_JIS(di_main) * 0.5)
    d = D - dc

    # 許容応力度
    f_c = ALST_RC_AIJ(Fc)

    # その３：HOOP間隔の規定
    if di_support == 10 and pitch <= 100:
        pass
    elif di_support > 10 and pitch <= 200:
        pass
    else:
        pass  # 原典: 空分岐
    # その４：せん断補強筋比の規定
    if min(np.atleast_1d(aw / (D * pitch))) >= 0.2 / 100:
        pass
    else:
        pass  # 原典: 表示コメントアウト済

    if timecase == 1:
        # 強軸せん断
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph1 = 1
        else:
            alph1 = max(1, min(2, 4 / (1 + (Mmax / Qmax / d * 1000))))

        # 弱軸せん断
        Mmax = np.max(np.abs(stress[:, 4]))
        Qmax = np.max(np.abs(stress[:, 1]))
        if Qmax == 0:
            alph2 = 1
        else:
            alph2 = max(1, min(2, 4 / (1 + (Mmax / Qmax / d * 1000))))
        alph = min(alph1, alph2)

        ALW_RCQ = np.zeros(1)
        ALW_RCQ[0] = d ** 2 / 4 * math.pi * (alph * f_c[0, 2]) / 10 ** 3

        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = qrc * np.sqrt(stress[:, 1] ** 2 + stress[:, 2] ** 2) / ALW_RCQ
        ratio_Q[:, 1] = ((1 - qrc) * np.sqrt(stress[:, 1] ** 2 + stress[:, 2] ** 2)
                         / (sA * sf[timecase - 1, 1] / 1.5 / 10 ** 3))

        ALW_sQ = sA * sf[timecase - 1, 1] / 1.5 / 10 ** 3

    if timecase == 2:
        # せん断割増しから決まる設計用せん断力
        Qs = qup_src * (stress[:, 1:3] - QL) + (QL)
        alph = []
        Mmax = []
        Qmax = []

        # 許容せん断力（短期）
        ALW_RCQ = (d ** 2 / 4 * math.pi
                   * (f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (D * pitch)) - 0.002)
                      * wft[1, 1]) / 10 ** 3)

        ratio_Q = np.zeros((3, 2))
        ratio_Q[:, 0] = qrc * np.sqrt(Qs[:, 0] ** 2 + Qs[:, 1] ** 2) / ALW_RCQ
        ratio_Q[:, 1] = ((1 - qrc) * np.sqrt(Qs[:, 0] ** 2 + Qs[:, 1] ** 2)
                         / (sA * sf[timecase - 1, 1] / 1.5 / 10 ** 3))

        # (注: 長期は/1.5あり, ここは/1.5なし。原典どおり)
        ALW_sQ = sA * sf[timecase - 1, 1] / 10 ** 3

    return ratio_Q, ALW_sQ, ALW_RCQ, qrc, alph, d, Mmax, Qmax, Qs


# ===========================================================================
# SRCcolumn_ALNM / SRCbeam_ALM / SRC_ratio_analysis (MATLAB原典からの逐語移植)
#   privatetool_function/MIDAS/ratio/SRC/SRCcolumn_ALNM.m
#   privatetool_function/MIDAS/ratio/SRC/SRCbeam_ALM.m
#   privatetool_function/MIDAS/ratio/SRC/SRC_ratio_analysis.m
#
# 【検証状況 2026-07-09 照合完了】SRC は KCUA物件(KCUA最終モデル_支点確認.mgt +
# beam_stress_KCUA.txt + ratio.mat)で MATLAB全数照合済み。beam_ratio(3090×6)の
# 全4列・全3090行(SRC材料5・RC材料3・S造すべて)が MATLAB と最大差 2.2e-15 =
# 完全一致(差>1e-6 の行ゼロ)。照合設定: select_length=1/select_unit=0/judge_H=1/
# H_beam_RCsupport=0/limit_sec_no=9000/c_up全1.0/RCQ=1/src_cover=40/
# qup=ルート3(柱梁1.5・壁1.0・SRC1.0)。SRC柱=RH2T(Cross-H十字,section_index1=15)、
# 梁=RHB(H内蔵角,10)。十字形鋼の断面性能 secprops.BPLUS を新規移植して Cross-H
# 経路が通った。
#   移植は原典 privatetool_function/MIDAS/ratio/SRC/*.m の逐語写し。
# 【未照合】鋼管被覆円柱(CPO,section_index1=13)・純H内蔵角柱(10)の経路は当該
# データに無く合成テストのみ(照合ペア入手後に全数照合すること)。
# ===========================================================================


def _src_section_size(section_info, section_no):
    """sectionget2.m 相当 (SRCcolumn_ALNM/SRCbeam_ALM 内の断面抽出ループ)."""
    section_index2 = 0
    section_index1 = None
    for i in range(len(section_info)):
        tbl = section_info[i]
        if tbl is None or np.asarray(tbl).size == 0:
            continue
        fi = find_index(np.asarray(tbl)[:, 0], section_no)  # 0-based/-1
        section_index2 += (fi + 1)  # MATLAB find_index (1-based/0) 相当へ
        if fi != -1:
            section_index1 = i
    if section_index1 is None:
        raise ValueError(
            'SRC配筋設定の断面番号 %s が *SECTION に存在しません '
            '(mgtの記述を確認してください)' % section_no)
    tbl = np.asarray(section_info[section_index1], dtype=float)
    section_size = np.concatenate((
        [float(section_no)], tbl[section_index2 - 1, 1:tbl.shape[1]],
        [float(section_index1), float(section_index2)]))
    return section_size, section_index1


def _src_column_FcSN(material_info, element_info, section_no):
    """SRCcolumn_ALNM.m の材料(Fc,SN)取得部の逐語移植.

    戻り値 Fc=[Fc, 0], SN(スカラ)。SRC以外の材料が割り当てられている場合や
    同一断面に異材料が混在する場合はエラー(mgt記述の問題として明示)。
    """
    same_section_index = find_zero_index(element_info[:, 2] - section_no)
    material_no = element_info[np.atleast_1d(same_section_index), 1]
    material_no = doublecheck(material_no)
    material_index = np.atleast_1d(find_index(material_info[:, 0], material_no))
    material_type = np.atleast_2d(material_info[material_index, :])
    if np.std(material_type[:, 1]) == 0 and material_type[0, 1] == 5:
        Fc_col = np.atleast_1d(material_info[material_index, 2])
        SN_col = np.atleast_1d(material_info[material_index, 3])
        if np.std(Fc_col) == 0 and np.std(SN_col) == 0:
            Fc = np.array([float(Fc_col[0]), 0.0])
            SN = float(SN_col[0])
        else:
            raise ValueError(
                'ERROR:SRC断面に異なる材料を適用→材料ごとの断面とする '
                '(断面番号 %s)' % section_no)
    else:
        raise ValueError(
            'SRC配筋設定の断面番号 %s にSRC以外の材料が割り当てられています '
            '(mgtの材料・断面の記述を確認してください)' % section_no)
    return Fc, SN


def SRCcolumn_ALNM(SRCcolumns, section_info, material_info, element_info,
                   cover_src):
    """SRCcolumn_ALNM.m の逐語移植.

    配筋情報と断面形状からSRC柱の許容N-M曲線(長期・短期)を断面ごとに算定。
    RC被覆鋼管(section_index1=13) / H鋼内蔵角(10) / Cross-H(15) に対応。

    戻り値 SRC_ALW_NM: list(柱断面数)。各要素は 2x3 相当の入れ子list
      col[j][k]  j:0=強軸(My/z軸), 1=弱軸(Mz/y軸)
                 k:0=長期NM, 1=短期NM, 2=HOOP(j=0のみ)
      NM曲線は (N点数,2) ndarray [ALW_N(kN), ALW_M(kNm)]。
      鋼管被覆(13)は j=1 未使用(None)。
    """
    SRCcolumns = np.atleast_2d(np.asarray(SRCcolumns, dtype=float))
    if SRCcolumns.size == 0:
        return []
    columns_num = SRCcolumns.shape[0]
    SRC_ALW_NM = []

    for icolumn_sec in range(columns_num):
        section_no = SRCcolumns[icolumn_sec, 0]
        section_size, section_index1 = _src_section_size(section_info,
                                                         section_no)
        Fc, SN = _src_column_FcSN(material_info, element_info, section_no)

        col = [[None, None, None], [None, None, None]]

        if section_index1 == 13:  # SRC 鋼管被覆(円形)
            Form = section_size[1] * 1000
            sForm = np.array([3, section_size[2] * 1000, section_size[3] * 1000])
            SD1 = SD_check(SRCcolumns[icolumn_sec, 5])
            HOOP = np.array([SRCcolumns[icolumn_sec, 5],
                             SRCcolumns[icolumn_sec, 6], SD1, cover_src])
            col[0][2] = HOOP
            SD2 = SD_check(SRCcolumns[icolumn_sec, 2])
            steelbar = np.array([SRCcolumns[icolumn_sec, 3],
                                 SRCcolumns[icolumn_sec, 2], SD2])
            ALW_N, ALW_M = SA_SRC_Rcolumn_AIJ(Form, sForm, steelbar, HOOP,
                                              Fc, SN, 1)
            col[0][0] = np.column_stack([np.ravel(ALW_N), np.ravel(ALW_M)])
            ALW_N, ALW_M = SA_SRC_Rcolumn_AIJ(Form, sForm, steelbar, HOOP,
                                              Fc, SN, 10)
            col[0][1] = np.column_stack([np.ravel(ALW_N), np.ravel(ALW_M)])

        elif section_index1 == 10:  # H鋼内蔵角
            Form = np.array([section_size[2], section_size[1]]) * 1000
            sForm = np.concatenate(([1], section_size[3:9] * 1000))
            SD1 = SD_check(SRCcolumns[icolumn_sec, 5])
            HOOP = np.array([SRCcolumns[icolumn_sec, 5],
                             SRCcolumns[icolumn_sec, 6], SD1, cover_src])
            col[0][2] = HOOP
            SD2 = SD_check(SRCcolumns[icolumn_sec, 2])
            nh = SRCcolumns[icolumn_sec, 3] / 2 - SRCcolumns[icolumn_sec, 4] + 2
            steelbar = np.array([1, SRCcolumns[icolumn_sec, 3],
                                 SRCcolumns[icolumn_sec, 2], nh,
                                 SRCcolumns[icolumn_sec, 4], SD2])
            ALW_N1, ALW_M1, ALW_N2, ALW_M2 = SA_SRC4column_HMD(
                Form, sForm, steelbar, HOOP, Fc, SN, 1)
            col[0][0] = np.column_stack([np.ravel(ALW_N1), np.ravel(ALW_M1)])
            col[1][0] = np.column_stack([np.ravel(ALW_N2), np.ravel(ALW_M2)])
            ALW_N1, ALW_M1, ALW_N2, ALW_M2 = SA_SRC4column_HMD(
                Form, sForm, steelbar, HOOP, Fc, SN, 10)
            col[0][1] = np.column_stack([np.ravel(ALW_N1), np.ravel(ALW_M1)])
            col[1][1] = np.column_stack([np.ravel(ALW_N2), np.ravel(ALW_M2)])

        elif section_index1 == 15:  # Cross-H内蔵
            Form = np.array([section_size[2], section_size[1]]) * 1000
            sForm = np.concatenate(([2], section_size[3:11] * 1000))
            SD1 = SD_check(SRCcolumns[icolumn_sec, 5])
            HOOP = np.array([SRCcolumns[icolumn_sec, 5],
                             SRCcolumns[icolumn_sec, 6], SD1, cover_src])
            col[0][2] = HOOP
            SD2 = SD_check(SRCcolumns[icolumn_sec, 2])
            nh = SRCcolumns[icolumn_sec, 3] / 2 - SRCcolumns[icolumn_sec, 4] + 2
            steelbar = np.array([1, SRCcolumns[icolumn_sec, 3],
                                 SRCcolumns[icolumn_sec, 2], nh,
                                 SRCcolumns[icolumn_sec, 4], SD2])
            ALW_N1, ALW_M1, ALW_N2, ALW_M2 = SA_SRC4column_HMD(
                Form, sForm, steelbar, HOOP, Fc, SN, 1)
            col[0][0] = np.column_stack([np.ravel(ALW_N1), np.ravel(ALW_M1)])
            col[1][0] = np.column_stack([np.ravel(ALW_N2), np.ravel(ALW_M2)])
            ALW_N1, ALW_M1, ALW_N2, ALW_M2 = SA_SRC4column_HMD(
                Form, sForm, steelbar, HOOP, Fc, SN, 10)
            col[0][1] = np.column_stack([np.ravel(ALW_N1), np.ravel(ALW_M1)])
            col[1][1] = np.column_stack([np.ravel(ALW_N2), np.ravel(ALW_M2)])
        else:
            raise ValueError('ERROR:SRC柱断面形状設定 (section_index1=%s)'
                             % section_index1)

        SRC_ALW_NM.append(col)
    return SRC_ALW_NM


def SRCbeam_ALM(SRCbeams, section_info, material_info, element_info,
                cover_src):
    """SRCbeam_ALM.m の逐語移植.

    配筋情報と断面形状からSRC梁の許容曲げモーメント(長期・短期)を断面ごとに
    算定。H鋼内蔵角(section_index1=10)のみ対応。

    戻り値 SRC_ALW_M: list(梁断面数)。各要素は [長期ALW_M(3x2), 短期ALW_M(3x2),
      HOOP(4要素)]。ALW_M 列=[正曲げ許容M, 負曲げ許容M(負値)] (kNm)、
      行=i端/中央/j端。
    該当要素が無い断面は None を置いて行位置を保持する (承認済の原典変更
    2026-07-10。原典は cell 全リセット後に行番号を保って再登録するため、
    後続断面の対応は同じ・先行断面の曲線は原典では失われるが本版では保持)。
    """
    if SRCbeams is None or len(SRCbeams) == 0:
        return []
    beams_num = len(SRCbeams)
    SRC_ALW_M = []

    for ibeam_sec in range(beams_num):
        section_no = SRCbeams[ibeam_sec][0]
        section_size, section_index1 = _src_section_size(section_info,
                                                         section_no)

        # 材料(Fc,SN)取得 (SRCbeam_ALM.m の分岐: 単一材料 or 全SRC同一)
        same_section_index = find_zero_index(element_info[:, 2] - section_no)
        same_arr = np.atleast_1d(same_section_index)
        if same_arr.size == 0:
            # ユーザー承認による原典(MATLAB)からの変更 2026-07-10:
            # 原典は SRC_ALW_M=[] で cell 全体をリセット(先行断面の曲線消失)後、
            # 後続断面を元の行番号のまま再登録する。Python の append 方式では
            # 行が詰まって beam_judge との対応がずれ、別断面の許容曲線を参照
            # しうるため None を置いて行位置を保持する。
            # (mgt側の問題: 要素に使われていない断面が *SRC-BEAM に登録された
            # 状態。当該断面は要素が無いため検定自体は行われない)
            SRC_ALW_M.append(None)
            continue
        material_no = element_info[same_arr, 1]
        material_no = doublecheck(material_no)
        material_index = np.atleast_1d(find_index(material_info[:, 0],
                                                  material_no))
        material_type = np.atleast_2d(material_info[material_index, :])
        if (material_type.shape[0] == 1) or (
                np.std(material_type[:, 1]) == 0 and material_type[0, 1] == 5):
            Fc_col = np.atleast_1d(material_info[material_index, 2])
            SN_col = np.atleast_1d(material_info[material_index, 3])
            if np.std(Fc_col) == 0 and np.std(SN_col) == 0:
                Fc = np.array([float(Fc_col[0]), 0.0])
                SN = float(SN_col[0])
            else:
                raise ValueError(
                    'ERROR:SRC断面に異なる材料を適用→材料ごとの断面とする '
                    '(断面番号 %s)' % section_no)
        else:
            raise ValueError(
                'SRC梁断面 %s の材料設定を確認してください '
                '(mgtの記述の問題の可能性があります)' % section_no)

        if section_index1 == 10:  # SRC H鋼内蔵角
            Form = np.array([section_size[2] * 1000, section_size[1] * 1000])
            sForm = np.array([1, section_size[3] * 1000, section_size[4] * 1000,
                              section_size[5] * 1000, section_size[6] * 1000])
            mat = np.asarray(SRCbeams[ibeam_sec][1], dtype=float)
            SD1 = SD_check(mat[0, 8])
            HOOP = np.array([mat[0, 8], mat[0, 9], SD1, cover_src])
            SD2u = SD_check(mat[0, 3])
            steelbar_u = np.column_stack([mat[:, 0:4], [SD2u, SD2u, SD2u]])
            SD2d = SD_check(mat[0, 7])
            steelbar_d = np.column_stack([mat[:, 4:8], [SD2d, SD2d, SD2d]])
            ALW_M_long = SRC4beam_ALWM(Form, sForm, steelbar_u, steelbar_d,
                                       HOOP, Fc, SN, 1)[0]
            ALW_M_short = SRC4beam_ALWM(Form, sForm, steelbar_u, steelbar_d,
                                        HOOP, Fc, SN, 10)[0]
            SRC_ALW_M.append([np.asarray(ALW_M_long), np.asarray(ALW_M_short),
                              HOOP])
        else:
            raise ValueError('ERROR:SRC梁断面形状設定 (section_index1=%s)'
                             % section_index1)
    return SRC_ALW_M


def SRC_ratio_analysis(sectionsize, ele_length, stress, timecase, Fc, ele_no,
                       maxratios, maxratios_text, section_no,
                       SRCcolumns, SRCbeams, SRCbeam_secNO, SRC_ALW_NM,
                       SRC_ALW_M, QL, qup_beam, cover_src, LOAS_CASE_NAME,
                       RCQ, pick_section_name):
    """SRC_ratio_analysis.m の逐語移植 (SRC部材の断面検定).

    柱/梁は配筋情報(SRCcolumns/SRCbeams)への断面番号の登録で判定する
    (同一断面で柱梁を混用しないこと)。
    断面形状(sectionsize末尾-1: ×1000後 13000鋼管被覆/10000中実角H/15000Cross-H)
    で分岐。曲げ・軸・せん断を検定し 3x4 の ratio_output を返す。

    返り値: (ratio_output(3x4), maxratios(2要素[ele_no, 最大値]), maxratios_text)
      ratio_output 列: 梁=列1曲げ・列3,4せん断、柱=列1(強軸NM)・列2(弱軸NM)・
      列3,4せん断。
    """
    from .src_text import (SRC_H_beam_ALWM_text, SRC_Rcolumn_ALWNM_text,
                           SRC_4Hcolumn_ALWNM_text)

    stress = np.array(np.atleast_2d(stress), dtype=float, copy=True)
    QL = np.array(np.atleast_2d(QL), dtype=float, copy=True)
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    maxratios = np.array(np.atleast_1d(np.asarray(maxratios, dtype=float)),
                         copy=True)

    stress[:, 0] = stress[:, 0] * -1
    sectionsize = sectionsize * 1000
    ratio_output = np.zeros((3, 4))
    shape_code = sectionsize[len(sectionsize) - 2]  # section_index1 * 1000

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        raise ValueError('長短期設定ミス')

    # 柱か梁かを配筋情報で判定
    SRCcolumns_a = np.atleast_2d(np.asarray(SRCcolumns, dtype=float))
    if SRCcolumns_a.size == 0:
        column_judge = -1
    else:
        column_judge = find_index(SRCcolumns_a[:, 0], section_no)
    if SRCbeam_secNO is None or np.asarray(SRCbeam_secNO).size == 0:
        beam_judge = -1
    else:
        beam_judge = find_index(np.asarray(SRCbeam_secNO,
                                           dtype=float).ravel(), section_no)

    SN = Fc[1]

    if column_judge == -1 and beam_judge != -1:
        # ================= 梁として検討 =================
        if shape_code == 10000:  # 中実角断面H
            if SRC_ALW_M[beam_judge] is None:
                raise ValueError(
                    'SRC梁断面 %s の許容曲げが事前計算されていません '
                    '(*SRC-BEAM に配筋登録はあるが要素に使用されていない断面'
                    'です。mgtの記述を確認してください)' % section_no)
            ALW_M = np.asarray(SRC_ALW_M[beam_judge][timecase - 1], dtype=float)
            ratio_plus = stress[:, 3] / ALW_M[:, 0]
            ratio_minus = stress[:, 3] / ALW_M[:, 1]
            ratio_output[:, 0] = np.max(
                np.column_stack([ratio_plus, ratio_minus]), axis=1)

            Form = np.array([sectionsize[1], sectionsize[0]])
            sForm = np.concatenate(([1], sectionsize[2:6]))

            ibeam_sec = find_index(np.asarray(SRCbeam_secNO,
                                              dtype=float).ravel(), section_no)
            mat = np.asarray(SRCbeams[ibeam_sec][1], dtype=float)
            SD2u = SD_check(mat[0, 3])
            steelbar_u = np.column_stack([mat[:, 0:4], [SD2u, SD2u, SD2u]])
            SD2d = SD_check(mat[0, 7])
            steelbar_d = np.column_stack([mat[:, 4:8], [SD2d, SD2d, SD2d]])
            SD1 = SD_check(mat[0, 8])
            HOOP = np.array([mat[0, 8], mat[0, 9], mat[0, 10], SD1, cover_src])

            rq = SA_SRCbeamQratio(Form, sForm, steelbar_u, steelbar_d, HOOP,
                                  np.array([Fc[0], 0]), SN, stress, timecase,
                                  QL[:, 1], qup_beam, ALW_M, RCQ)
            ratio_output[:, 2:4] = np.asarray(rq[0], dtype=float)

            if np.max(ratio_output) >= np.max(maxratios[1]):
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SRC_H_beam_ALWM_text(
                    Form, sForm, steelbar_u, steelbar_d, HOOP,
                    np.array([Fc[0], 0]), SN, timecase, stress, ele_no,
                    section_no, QL[:, 1], qup_beam, LOAS_CASE_NAME,
                    pick_section_name)
        else:
            raise ValueError('ERROR:SRC梁の断面形状設定ミス＜長方形でない梁＞')

    elif column_judge != -1 and beam_judge == -1:
        # ================= 柱として検討 =================
        if shape_code == 13000:  # 鋼管被覆断面
            ALW_NM = np.asarray(SRC_ALW_NM[column_judge][0][timecase - 1],
                                dtype=float)
            ratio_output[:, 0] = (
                np.sqrt(stress[:, 3] ** 2 + stress[:, 4] ** 2)
                / ALW_NM[find_index_rough(ALW_NM[:, 0], stress[:, 0]), 1])

            Form = sectionsize[0]
            sForm = np.array([3, sectionsize[1], sectionsize[2]])
            HOOP = np.asarray(SRC_ALW_NM[column_judge][0][2], dtype=float)
            steelbar = SRCcolumns_a[column_judge, 1:6]

            rq = SA_SRC_Rcolumn_Qratio(Form, sForm, steelbar, HOOP,
                                       np.array([Fc[0], 0]), SN, stress,
                                       timecase, QL, qup_beam, ALW_NM, RCQ)
            ratio_output[:, 2:4] = np.asarray(rq[0], dtype=float)

            if np.max(ratio_output) >= np.max(maxratios[1]):
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SRC_Rcolumn_ALWNM_text(
                    Form, sForm, steelbar, HOOP, np.array([Fc[0], 0]), SN,
                    timecase, stress, ele_no, section_no, QL, qup_beam,
                    LOAS_CASE_NAME, ALW_NM, RCQ, pick_section_name)

        elif shape_code == 10000:  # 中実角断面H
            ALW_NM1 = np.asarray(SRC_ALW_NM[column_judge][0][timecase - 1],
                                 dtype=float)
            ALW_NM2 = np.asarray(SRC_ALW_NM[column_judge][1][timecase - 1],
                                 dtype=float)
            ratio_output[:, 0] = np.abs(stress[:, 3]) / ALW_NM1[
                find_index_rough(ALW_NM1[:, 0], stress[:, 0]), 1]
            ratio_output[:, 1] = np.abs(stress[:, 4]) / ALW_NM2[
                find_index_rough(ALW_NM2[:, 0], stress[:, 0]), 1]

            Form = np.array([sectionsize[1], sectionsize[0]])
            sForm = np.concatenate(([1], sectionsize[2:8]))
            HOOP = np.asarray(SRC_ALW_NM[column_judge][0][2], dtype=float)
            steelbar = SRCcolumns_a[column_judge, 1:6]

            rq = SA_SRC_4Hcolumn_Qratio(Form, sForm, steelbar, HOOP,
                                        np.array([Fc[0], 0]), SN, stress,
                                        timecase, QL, qup_beam, ALW_NM1,
                                        ALW_NM2, RCQ)
            ratio_output[:, 2:4] = np.asarray(rq[0], dtype=float)

            if np.max(ratio_output) >= np.max(maxratios[1]):
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SRC_4Hcolumn_ALWNM_text(
                    Form, sForm, steelbar, HOOP, np.array([Fc[0], 0]), SN,
                    timecase, stress, ele_no, section_no, QL, qup_beam,
                    LOAS_CASE_NAME, ALW_NM1, ALW_NM2, RCQ, pick_section_name)

        elif shape_code == 15000:  # Cross-H
            ALW_NM1 = np.asarray(SRC_ALW_NM[column_judge][0][timecase - 1],
                                 dtype=float)
            ALW_NM2 = np.asarray(SRC_ALW_NM[column_judge][1][timecase - 1],
                                 dtype=float)
            ratio_output[:, 0] = np.abs(stress[:, 3]) / ALW_NM1[
                find_index_rough(ALW_NM1[:, 0], stress[:, 0]), 1]
            ratio_output[:, 1] = np.abs(stress[:, 4]) / ALW_NM2[
                find_index_rough(ALW_NM2[:, 0], stress[:, 0]), 1]

            # 曲げが0のときの軸力比
            for ii in range(2):
                for jj in range(3):
                    if ratio_output[jj, ii] == 0:
                        if stress[jj, 0] > 0:  # 圧縮
                            ratio_output[jj, ii] = (stress[jj, 0]
                                                    / np.max(ALW_NM1[:, 0]))
                        elif stress[jj, 0] < 0:  # 引張
                            ratio_output[jj, ii] = (stress[jj, 0]
                                                    / np.min(ALW_NM1[:, 0]))

            Form = np.array([sectionsize[1], sectionsize[0]])
            sForm = np.concatenate(([2], sectionsize[2:10]))
            HOOP = np.asarray(SRC_ALW_NM[column_judge][0][2], dtype=float)
            steelbar = SRCcolumns_a[column_judge, 1:6]

            rq = SA_SRC_4Hcolumn_Qratio(Form, sForm, steelbar, HOOP,
                                        np.array([Fc[0], 0]), SN, stress,
                                        timecase, QL, qup_beam, ALW_NM1,
                                        ALW_NM2, RCQ)
            ratio_output[:, 2:4] = np.asarray(rq[0], dtype=float)

            if np.max(ratio_output) >= np.max(maxratios[1]):
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SRC_4Hcolumn_ALWNM_text(
                    Form, sForm, steelbar, HOOP, np.array([Fc[0], 0]), SN,
                    timecase, stress, ele_no, section_no, QL, qup_beam,
                    LOAS_CASE_NAME, ALW_NM1, ALW_NM2, RCQ, pick_section_name)
        else:
            raise ValueError('ERROR:SRC梁の断面形状設定ミス＜長方形でない梁＞')

    elif column_judge != -1 and beam_judge != -1:
        raise ValueError('柱断面・梁断面の識別失敗 (断面番号 %s を柱と梁の'
                         '両方に登録しています)' % section_no)
    else:
        raise ValueError('SRC断面（配筋情報）が定義されていません '
                         '(断面番号 %s。mgtの *SRC-COLUMN/*SRC-BEAM を確認)'
                         % section_no)

    return ratio_output, maxratios, maxratios_text
