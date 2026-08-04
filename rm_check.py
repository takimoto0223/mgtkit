# -*- coding: utf-8 -*-
"""RM(補強組積造)断面検定 (MATLAB原典からの逐語移植).

元コード (msrc/):
  privatetool_function/MIDAS/ratio/RC/RM_ratio_analysis.m
      RM梁(中実角)・RM壁柱(中実角)の検定分岐と
      ローカルサブ関数 sub_RM4beam_ALWM (同ファイル352行以降)
  structural_function/RC/ALST_etc/ALST/ALST_RM_AIJ.m (組積体の許容応力度)
  structural_function/RC/RC_section_analysis/wall/SA_RM4_HMD.m
  structural_function/RC/RC_section_analysis/wall/SA_RM4_HMD_text.m
  structural_function/RC/RC_section_analysis/wall/SA_RM4Qratio.m
  structural_function/RC/RC_section_analysis/beam/SA_RMbeamQratio.m
  structural_function/RC/RC_section_analysis/beam/SA_RMbeamratio_text.m

スコープ (原典で生きている経路のみ):
  ・RM梁: 中実角断面(sectionsize末尾-1==2000)。配筋はMIDASのRC入力
    (RCbeams 3x17行列) を旧13列書式に落として利用 (原典30-32行)。
  ・RM壁柱: 中実角断面。RMなので walldesign_index=2 のみ (原典199行)。
    面外曲げ(NMy)は検討せず0 (原典247-275行コメントアウト)、面内(NMz)は
    端部補強筋4本(縦筋の一段太径)のみを考慮 (原典281行 230415金澤)。
  ・SA_RW4_AIJ の呼び出しは原典で全てコメントアウトされており未移植。
    SA_RM4_HMD.m 内の steelbar(1)==2/3 分岐 (117-328行) も原典で全行
    コメントアウトのため移植対象外。

単位系: RM_ratio_analysis 入口で sectionsize×1000 (m→mm)。応力はkN,kNm。
Fm: 原典は主関数内で Fm=[Fm(2) 0] とするため、入力Fmの2番目の要素が
    組積体強度(プリズム強度)として使われる (RCのFcと同じ受け渡し形式)。
インデックス規約: util.find_index は 0-based/-1 (MATLABは1-based/0)。
既移植関数 (Area_steelbar, outf_steelbar_JIS, ALST_steelbar_KJ,
bar_table_next_diameter, SD_check 等) は rc_check / src_check から再利用。
"""

import math

import numpy as np

from .util import find_index, excelround
from .s_check import _num2str
from .rc_check import (Area_steelbar, outf_steelbar_JIS, ALST_steelbar_KJ,
                       bar_table_next_diameter, _warn, _WARN_CTX, _mcolon)
from .src_check import SD_check


# ===========================================================================
# 組積体の許容応力度 (ALST_etc/ALST/ALST_RM_AIJ.m)
# ===========================================================================

def ALST_RM_AIJ(Fm):
    """ALST_RM_AIJ.m の逐語移植.

    建築学会RM基準/組積体の許容応力度計算
    入力例 Fm=[21 0]
    出力はALstressRC =[圧縮 引張 せん断 付着(上端) 付着(その他)]で
    1行目に長期，2行目に短期
    (引張・付着は原典で未定義のためゼロのまま)
    """
    Fm = np.asarray(Fm, dtype=float).ravel()

    ALstressRM = np.zeros((2, 5))

    # 圧縮
    ALstressRM[0, 0] = Fm[0] / 3
    ALstressRM[1, 0] = Fm[0] * 2 / 3

    # 引張（引っ張りは0と定義する．）

    # せん断
    ALstressRM[0, 2] = math.sqrt(Fm[0] * 0.1) / 3
    ALstressRM[1, 2] = ALstressRM[0, 2] * 1.5

    # 付着

    return ALstressRM


# ===========================================================================
# RM壁柱の断面解析 (SA_RM4_HMD.m / SA_RM4Qratio.m)
# ===========================================================================

def SA_RM4_HMD(Nd, Form, steelbar, HOOP, Fc, timecase):
    """SA_RM4_HMD.m の逐語移植 (RM壁柱の許容曲げモーメント).

    元ファイル: structural_function/RC/RC_section_analysis/wall/SA_RM4_HMD.m
    SA_RW4_HMD の steelbar(1)==1 分岐と同型だが、
      ・ヤング係数比 n=15 固定 (E_RC_AIJ を使わない)
      ・許容応力度は ALST_RM_AIJ (組積体)
      ・原典の if steelbar(1)==1 分岐はコメントアウト済みで常に本経路
        (steelbar(1)==2/3 分岐 117-328行も全行コメントアウトのため未移植)

    steelbar = [type, 総本数num, 径D, せい方向本数nv, 幅方向本数, SD, 0]
    HOOP = [横筋径D, pitch, n1, n2, SD, cover_depth] (6要素)
    戻り値: (M_AL, maxN)  maxNは長さ2のndarray (MATLABの maxN(1),maxN(2))
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Nd = float(Nd)

    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない（後続のtimecase2参照で実行時エラーになる）

    # RC断面の外形情報
    t = Form[0]; D = Form[1]
    # NOTE: load bar_table.mat はヘルパー関数(Area_steelbar等)が内部保持するため不要
    # if steelbar(1)==1 %端部補強筋なしの場合 (原典14行: コメントアウト済み)
    di_main = steelbar[2]; SD_main = steelbar[5]
    a = Area_steelbar(di_main, 1)
    ai = steelbar[4] * a

    num = steelbar[1]
    nv = steelbar[1] / steelbar[4]  # 壁長さ方向の鉄筋本数

    rfc = ALST_steelbar_KJ([di_main, SD_main])
    f_c = ALST_RM_AIJ(Fc)

    n = 15
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

    # NOTE: 原典117-328行 (elseif steelbar(1)==2 「端部補強筋あり<強軸>」/
    #       ==3 「端部補強筋あり<弱軸>」) はMATLAB原典でも全行コメントアウト
    #       されているため移植対象外

    return M_AL, maxN


# %%%%%%%%%RC■断面柱の許容せん断力算定
def SA_RM4Qratio(Form, HOOP, Fm, stress, timecase, QL, qup_wall, RCQ=None):
    """SA_RM4Qratio.m の逐語移植 (RM壁柱の許容せん断力).

    元ファイル: structural_function/RC/RC_section_analysis/wall/SA_RM4Qratio.m
    NOTE: 原典の引数RCQは関数内で未使用。SA_RM4_HMD_text.m からはRCQ無しの
          7引数で呼ばれる (MATLABは未使用引数の省略呼び出しが可能) ため
          Python版ではRCQを省略可能引数にする。
    戻り値: (ratio_Q, ALW_Q, Qs1)
      ratio_Q: (n,2) ndarray
      ALW_Q: 長期は(n,2)、短期は(n,1) (MATLABと同次元)
      Qs1: 長期は空配列、短期は(2,n)
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
    SD_support = SD_check(di_support)
    pitch = HOOP[1]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    pw = np.minimum(0.012, np.maximum(0.002, Area_steelbar(di_support, 1) / (pitch * D)))

    # 断面情報（配筋断面積，許容応力度など）
    f_c = ALST_RM_AIJ(Fm)

    # その３：横筋の規定
    if di_support > 10 and pitch <= 400:
        pass
    else:
        _warn('帯筋間隔NG')

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
        # ALW_Q(:,1) = max(b.*D.*f_c(2,3)/10^3,7/8*(b-50).*D.*(f_c(2,3)+0.5*wft(2,2)*(pw-0.002))/10^3);
        ALW_Q = (7 / 8 * (b - 50) * D * (f_c[1, 2] + 0.5 * wft[1, 1] * (pw - 0.002)) / 10 ** 3).reshape(-1, 1)
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
# RM梁の断面解析 (RM_ratio_analysis.m ローカルsub_RM4beam_ALWM / SA_RMbeamQratio.m)
# ===========================================================================

def sub_RM4beam_ALWM(Form, steelbar_u, steelbar_d, HOOP, Fm, timecase, L43):
    """RM梁の許容曲げモーメント算定（逐語移植）

    元ファイル: privatetool_function/MIDAS/ratio/RC/RM_ratio_analysis.m
    行範囲: 352-621 (ローカルサブ関数 sub_RM4beam_ALWM)
    rc_check.sub_RC4beam_ALWM(3段対応8列書式)と異なり旧6列書式・n=15固定・
    組積体許容応力度(ALST_RM_AIJ)を用いる。

    返り値: (ALWM, up)
      ALWM: 3x2 ndarray（i端/中央/j端 × 正曲げ/負曲げ、負曲げは負値）[kNm]
      up:   引張鉄筋断面積不足時の割増係数（pt<0.2%かつ長期のときL43、それ以外1.0）
    """
    # 建築学会RC基準1999等/RC梁断面算定
    # 2009/03/30ソース作成
    # エクセルファイルとの対応確認済
    #
    # ひび割れモーメントに対する検討を追加すること．
    #
    # Form：[はり幅b,はりせいD]
    # L:梁のスパン
    # steelbar_u = [段数，一段目本数，二段目本数，一段目径D，二段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    # steelbar_d = [段数，一段目本数，二段目本数，一段目径D，二段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    # HOOP=[径D，pitch, n, SD, cover_depth] Fm=[21 1]   %[Fm 普通(0)軽量1種(1)軽量2種(2)を示す]
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
    num[0, :] = np.sum(steelbar_u[:, 1:3], axis=1)
    di_main[0, :] = steelbar_u[:, 3]
    SD_main[0, :] = steelbar_u[:, 5]

    num[1, :] = np.sum(steelbar_d[:, 1:3], axis=1)
    di_main[1, :] = steelbar_d[:, 3]
    SD_main[1, :] = steelbar_d[:, 5]

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
            if steelbar_u.shape[1] == 7:
                dd[0, j] = steelbar_u[j, 6]
                print('dd =\n', dd)  # NOTE: 原典419行はセミコロン無し（表示のみ）
            elif steelbar_u.shape[1] == 6:
                dd[0, j] = math.ceil(max(di_main[0, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, j])
            d[0, j] = d_out[0, j] * (steelbar_u[j, 1]) / num[0, j] + (d_out[0, j] + dd[0, j]) * (steelbar_u[j, 2]) / num[0, j]
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
            if steelbar_d.shape[1] == 7:
                dd[1, j] = steelbar_d[j, 6]
            elif steelbar_d.shape[1] == 6:
                dd[1, j] = math.ceil(max(di_main[1, j] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, j])
            d[1, j] = d_out[1, j] * (steelbar_d[j, 1]) / num[1, j] + (d_out[1, j] + dd[1, j]) * (steelbar_d[j, 2]) / num[1, j]
        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    a = Area_steelbar(di_main, 1)  # MATLAB: Area_steelbar(bar_table,di_main,1)
    f_c = ALST_RM_AIJ(Fm)

    rfc = [[None, None, None], [None, None, None]]
    for ii in range(2):
        for jj in range(3):
            rfc[ii][jj] = ALST_steelbar_KJ([di_main[ii, jj], SD_main[ii, jj]])

    # ヤング係数比(RC基準で設定された断面解析用のヤング係数比）
    n = 15

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    max_u = np.max(steelbar_u[:, 1:3], axis=1)  # max(steelbar_u(:,2:3),[],2)'
    max_d = np.max(steelbar_d[:, 1:3], axis=1)  # max(steelbar_d(:,2:3),[],2)'
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
        ERROR = '主筋規定外(D13以上)'
        raise ValueError(ERROR)  # MATLAB: stop

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b * D)
    pt = np.max(pt)
    if pt < 0.2 / 100 and timecase < 2:
        up = L43
    else:
        up = 1.0
        # ERROR='引張鉄筋断面積不足（0.4％未満）'
        # stop

    # その３：STRP間隔の規定
    if di_support >= 10:
        pass
    else:
        _warn('STRP径：D' + _num2str(di_support, '%15.0f') + '：NG')
        # stop

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, HOOP[2])  # MATLAB: Area_steelbar(bar_table,di_support,HOOP(3))

    if aw / (b * pitch) >= 0.25 / 100 and pitch <= 200:
        pass
    else:
        _warn('せん断補強筋比不足（0.25％未満）　pw=' + _num2str(aw / (b * pitch) * 100, '%15.2f') +
              '%→STRPピッチ' + _num2str(pitch, '%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '%15.0f') +
              '，　梁幅b：' + _num2str(b, '%15.0f') + 'mm')
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


# %%%%%%%%%RC梁の許容せん断力算定
def SA_RMbeamQratio(Form, L, steelbar_u, steelbar_d, HOOP, Fm, stress, timecase,
                    QL, qup_beam, RCQ, RM_ALW_M):
    """SA_RMbeamQratio.m の逐語移植 (RM梁の許容せん断力算定).

    元ファイル: structural_function/RC/RC_section_analysis/beam/SA_RMbeamQratio.m
    steelbar_u/d は旧6列書式 [段数,一段目本数,二段目本数,径D,二段目径,SD]。
    返り値: (ratio_Q, ALW_Q, alph, j, Mmax, Qmax, Qs1, Qs2)
      ratio_Q, ALW_Q: 長さ3のndarray（i端/中央/j端）
      Qs1, Qs2: 長期では空配列（MATLABの[]）
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    stress = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.asarray(QL, dtype=float).ravel()  # 列ベクトル → 1次元化（QL'相当の読み替え）
    RM_ALW_M = np.asarray(RM_ALW_M, dtype=float)

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
    num[0, :] = np.sum(steelbar_u[:, 1:3], axis=1)
    di_main[0, :] = steelbar_u[:, 3]

    num[1, :] = np.sum(steelbar_d[:, 1:3], axis=1)
    di_main[1, :] = steelbar_d[:, 3]
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
            if steelbar_u.shape[1] == 7:
                dd[0, i] = steelbar_u[i, 6]
            elif steelbar_u.shape[1] == 6:
                dd[0, i] = excelround(max(di_main[0, i] * 1.5, 25 * 1.25), 1) + outf_steelbar_JIS(di_main[0, i])
            d[0, i] = d_out[0, i] * (steelbar_u[i, 1]) / num[0, i] + (d_out[0, i] + dd[0, i]) * (steelbar_u[i, 2]) / num[0, i]
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
            if steelbar_d.shape[1] == 7:
                dd[1, i] = steelbar_d[i, 6]
            elif steelbar_d.shape[1] == 6:
                dd[1, i] = excelround(max(di_main[1, i] * 1.5, 25 * 1.25), 1) + outf_steelbar_JIS(di_main[1, i])
            d[1, i] = d_out[1, i] * (steelbar_d[i, 1]) / num[1, i] + (d_out[1, i] + dd[1, i]) * (steelbar_d[i, 2]) / num[1, i]
        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    f_c = ALST_RM_AIJ(Fm)

    # 許容せん断力の算定（長期）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if timecase == 1:
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(2, 4 / (1 + (Mmax / Qmax / np.max(D - d, axis=0) * 1000))))
        j = 0.875 * np.max(D - d, axis=0)
        ALW_Q = b * j * alph * f_c[0, 2] / 10 ** 3
        ALW_Q = ALW_Q  # MATLAB: ALW_Q' （列ベクトル化。1次元ndarrayのため形状不変）
        ratio_Q = np.abs(stress[:, 2]) / ALW_Q

        Qs1 = np.array([]); Qs2 = np.array([])

    if timecase >= 2:

        # せん断割増しから決まる設計用せん断力
        Qs1 = np.abs(qup_beam * (stress[:, 2] - QL) + (QL))

        # 梁端部Mから決まる設計用せん断力
        My = np.abs(RM_ALW_M.T)
        Qe2 = max(My[0, 0] + My[1, 2], My[1, 0] + My[0, 2]) / (L)

        # ここで入力された応力QLは長期と仮定して足している．（本当は単純梁としたときの応力に修正必要）
        Qs2 = np.abs(QL) + Qe2

        if RCQ == 3:
            Qs = np.minimum(Qs1, Qs2)
        elif RCQ == 1:
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
        ALW_Q = b * j * (alph * f_c[1, 2] + 0.5 * ((aw / (b * pitch)) - 0.002) * wft[1, 1]) / 10 ** 3
        ALW_Q = ALW_Q  # MATLAB: ALW_Q'
        ratio_Q = Qs / ALW_Q

    return ratio_Q, ALW_Q, alph, j, Mmax, Qmax, Qs1, Qs2


# ===========================================================================
# 検定詳細テキスト (SA_RMbeamratio_text.m / SA_RM4_HMD_text.m)
# ===========================================================================

def SA_RMbeamratio_text(Form, ele_length, steelbar_u, steelbar_d, STRP, Fm,
                        stress, timecase, QL, ele_no, section_no, ALW_M,
                        qup_beam, up, LOAS_CASE_NAME, section_name, RCQ):
    """SA_RMbeamratio_text.m の逐語移植
    RM梁の断面算定詳細のテキスト出力
    曲げ(MM)，せん断(Q)に対する断面算定
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    STRP = np.asarray(STRP, dtype=float).ravel()
    S = np.atleast_2d(np.asarray(stress, dtype=float))
    QL = np.asarray(QL, dtype=float).ravel()
    ALW_M = np.asarray(ALW_M, dtype=float)

    # 曲げ(MM)に対する断面算定
    up = np.array([up, up, up], dtype=float)

    ratio_output = np.zeros((3, 2))
    for ie in range(3):
        if S[ie, 3] >= 0:
            ratio_output[ie, 0] = S[ie, 3] * up[ie] / ALW_M[ie, 0]
        else:
            ratio_output[ie, 0] = S[ie, 3] * up[ie] / ALW_M[ie, 1]

    # せん断(Q)に対する断面算定
    q8 = SA_RMbeamQratio(Form, ele_length, steelbar_u, steelbar_d, STRP, Fm,
                         S, timecase, QL, qup_beam, RCQ, ALW_M)
    ratio_output[:, 1] = np.asarray(q8[0], dtype=float).ravel()
    ALW_Q = np.zeros((3, 3))
    ALW_Q[:, 0] = np.asarray(q8[1], dtype=float).ravel()
    alph = np.asarray(q8[2], dtype=float).ravel()
    j_dis = np.asarray(q8[3], dtype=float).ravel()
    Mmax = q8[4]
    Qmax = q8[5]
    Qs1 = np.asarray(q8[6], dtype=float).ravel()
    Qs2 = np.asarray(q8[7], dtype=float).ravel()
    ALW_Q[:, 1] = ALW_Q[:, 0]
    ALW_Q[:, 2] = ALW_Q[:, 0]

    if timecase == 1:
        designQ = np.abs(S[:, 2])
    else:
        if RCQ == 3:
            designQ = np.minimum(Qs1, Qs2)
        elif RCQ == 1:
            designQ = Qs1
        else:
            designQ = Qs2

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%以上で検定値の算出終了

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # RM断面の外形情報[mm単位で入力]
    b = Form[0]; D = Form[1]
    dd = np.zeros((2, 3)) + D  # 二段配筋の際には1000が書き換えられる．
    # NOTE: 原典47行はコメントの「1000」と異なり zeros(2,3)+D (梁せいD) で初期化している

    # あばら筋情報を読み込み
    # load bar_table.mat → ヘルパー関数で代替
    di_support = STRP[0]; pitch = STRP[1]; SD_support = STRP[3]; cover_depth = STRP[4]
    aw = Area_steelbar(di_support, STRP[2])

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    SD_main = np.zeros((2, 3))
    num[0, :] = np.sum(steelbar_u[:, 1:3], axis=1)
    di_main[0, :] = steelbar_u[:, 3]
    SD_main[0, :] = steelbar_u[:, 5]

    num[1, :] = np.sum(steelbar_d[:, 1:3], axis=1)
    di_main[1, :] = steelbar_d[:, 3]
    SD_main[1, :] = steelbar_d[:, 5]
    # 梁の上端筋と下端筋の段数から重心距離の計算

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
            if steelbar_u.shape[1] == 7:
                dd[0, i] = steelbar_u[i, 6]
            elif steelbar_u.shape[1] == 6:
                dd[0, i] = math.ceil(max(di_main[0, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[0, i])
            d[0, i] = d_out[0, i] * (steelbar_u[i, 1]) / num[0, i] + (d_out[0, i] + dd[0, i]) * (steelbar_u[i, 2]) / num[0, i]
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
            if steelbar_d.shape[1] == 7:
                dd[1, i] = steelbar_d[i, 6]
            elif steelbar_d.shape[1] == 6:
                dd[1, i] = math.ceil(max(di_main[1, i] * 1.5, 25 * 1.25) / 10) * 10 + outf_steelbar_JIS(di_main[1, i])
            d[1, i] = d_out[1, i] * (steelbar_d[i, 1]) / num[1, i] + (d_out[1, i] + dd[1, i]) * (steelbar_d[i, 2]) / num[1, i]
        else:
            ERROR = '配筋情報エラー（配筋段数）'
            raise ValueError(ERROR)  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    text = ['RM梁の断面算定内容（最大検定値の検定内容）']
    text.append('強軸周りの曲げとせん断に対して断面算定を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')

    text.append('RM梁サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]　RM梁スパン[m]：' + _num2str(ele_length, '%15.2f'))

    text.append('　　')
    text.append('*****配筋情報（本数のあとの数字は段数を示す）*****')
    text.append('　　')
    text.append('上端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[0, 0]) + '(' + _num2str(steelbar_u[0, 0]) + ')-D' + _num2str(di_main[0, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[0, 1]) + '(' + _num2str(steelbar_u[1, 0]) + ')-D' + _num2str(di_main[0, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[0, 2]) + '(' + _num2str(steelbar_u[2, 0]) + ')-D' + _num2str(di_main[0, 2]))
    text.append('　　')
    text.append('下端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[1, 0]) + '(' + _num2str(steelbar_d[0, 0]) + ')-D' + _num2str(di_main[1, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[1, 1]) + '(' + _num2str(steelbar_d[1, 0]) + ')-D' + _num2str(di_main[1, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[1, 2]) + '(' + _num2str(steelbar_d[2, 0]) + ')-D' + _num2str(di_main[1, 2]))
    text.append('　　')
    text.append('あばら筋：' + _num2str(STRP[2]) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋：SD' + _num2str(SD_main[0, 0]) + '　あばら筋：SD' + _num2str(SD_support))
    text.append('組積体：Fm' + _num2str(Fm[0]))
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

    b1 = np.zeros((2, 3)) + b
    D1 = np.zeros((2, 3)) + D

    max_u = np.max(steelbar_u[:, 1:3], axis=1)  # max(steelbar_u(:,2:3),[],2)'
    max_d = np.max(steelbar_d[:, 1:3], axis=1)  # max(steelbar_d(:,2:3),[],2)'
    if np.all(np.maximum(max_u, max_d) == 1):
        dd2 = b1 / 2
    else:
        dd2 = (b1 - 2 * d_out) / (np.vstack([max_u, max_d]) - 1)
    dd2 = np.min(dd2)

    check_dd = dd - outf_steelbar_JIS(di_main)
    check_dd2 = dd2 - outf_steelbar_JIS(di_main)

    checkdd = np.minimum(check_dd, check_dd2)
    min_dd = np.maximum(1.5 * di_main, 25 * 1.25)

    judge_dd = checkdd - min_dd

    if np.min(judge_dd) >= 0:
        pass
    else:
        _warn('鉄筋あき不足(梁せいおよび梁幅方向の検討）')
        _warn('梁せい方向あき：' + _num2str(np.min(check_dd), '%15.2f') + 'mm')
        _warn('梁幅方向あき：' + _num2str(np.min(check_dd2), '%15.2f') + 'mm')
        _warn('あきの最小値：' + _num2str(np.min(min_dd), '%15.2f') + 'mm')
        _warn('断面番号：' + _num2str(section_no))
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
        ERROR = '主筋規定外(D13以上，配筋段数については既に検討済)'
        print('ERROR =', ERROR)  # NOTE: 原典207行は stop なし（表示のみで続行）
        # stop

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b1 * D1)
    pt_ID1 = np.argmin(pt, axis=0)  # MATLAB: [pt1 pt_ID1] = min(pt)
    pt1 = np.min(pt, axis=0)
    pt_ID2 = int(np.argmin(pt1))  # MATLAB: [pt2 pt_ID2] = min(pt1)
    min_pt = pt[pt_ID1[pt_ID2], pt_ID2]

    text.append('　　')
    text.append('引張鉄筋必要最低断面積（全断面の0.2％以上）')
    text.append('最小引張鉄筋仕様：' + _num2str(num[pt_ID1[pt_ID2], pt_ID2]) + '-D' + _num2str(di_main[pt_ID1[pt_ID2], pt_ID2]))
    text.append('鉄筋総断面積' + _num2str(num[pt_ID1[pt_ID2], pt_ID2] * a[pt_ID1[pt_ID2], pt_ID2] / 100, '%15.2f') + '[cm2]')

    if min_pt >= 0.2 / 100:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[％]　"OK"')
        up = 1.0
    else:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[％]　"0.2%以下→長期応力割増"')
        up = 4 / 3
        # ERROR='引張鉄筋断面積不足（0.4％未満）'
        # stop

    # その３：STRP間隔の規定
    text.append('　　')
    text.append('あばら筋径・間隔の規定')
    if di_support >= 10:
        text.append('あばら筋径：D' + _num2str(di_support) + '　あばら筋間隔：' + _num2str(pitch) + '　"OK(D10以上)"')
    else:
        text.append('あばら筋径：D' + _num2str(di_support) + '　あばら筋間隔：' + _num2str(pitch) + '　"あばら筋径NG"')
        # stop

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('せん断補強筋比の規定(0.25％以上)')
    text.append('あばら筋の仕様：' + _num2str(STRP[2]) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (b * pitch) >= 0.25 / 100:  # MATLAB: aw/(min(b)*pitch)（bはスカラ）
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (b * pitch) * 100, '%15.2f') + '%＞0.25%：OK')
    else:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (b * pitch) * 100, '%15.2f') + '%＜0.25%：NG')
        _warn('せん断補強筋比不足（0.25％未満）　pw=' + _num2str(aw / (b * pitch) * 100, '%15.2f') +
              '%→STRPピッチ' + _num2str(pitch, '%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '%15.0f') +
              '，　梁幅b：' + _num2str(b, '%15.0f') + 'mm')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚：' + _num2str(cover_depth) + 'mm≧40mm：OK')
    else:
        ERROR = '鉄筋かぶり厚再検討（40mm未満）'
        print('ERROR =', ERROR)  # NOTE: 原典261行は stop なし（表示のみで続行）
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
        # せん断割増しから決まる設計用せん断力
        # NOTE: 原典283行はSA_RMbeamQratioと異なり abs の取り方が
        #       qup_beam*abs(stress-QL)'+abs(QL)'（項別に絶対値）で再計算している
        Qs1 = qup_beam * np.abs(S[:, 2] - QL) + np.abs(QL)
        designQ = Qs1

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
        text.append('長期荷重時せん断力：' + _num2str(QL[0], '%15.1f') + '　[kN]')
        text.append('梁の降伏モーメントから決定する設計用せん断力：' + _num2str(Qs2[0], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(Qs1[0], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(up * My[0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    if timecase == 1:
        pass
    else:
        text.append('長期荷重時せん断力：' + _num2str(QL[1], '%15.1f') + '　[kN]')
        text.append('梁の降伏モーメントから決定する設計用せん断力：' + _num2str(Qs2[1], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(Qs1[1], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(up * My[1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[1], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    if timecase == 1:
        pass
    else:
        text.append('長期荷重時せん断力：' + _num2str(QL[2], '%15.1f') + '　[kN]')
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
    text.append('正曲げMy　：' + _num2str(ALW_M[0, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[0, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[0, 0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[0, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　中央')
    text.append('正曲げMy　：' + _num2str(ALW_M[1, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[1, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[1, 0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[1, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('正曲げMy　：' + _num2str(ALW_M[2, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[2, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[2, 2], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[2, 1], '%15.2f'))
    return text


def SA_RM4_HMD_text(ele_length, steelbar_y, steelbar_z, HOOP, Fm, stress, timecase,
                    QL, ele_no, section_no, qup_wall, reduction, LOAS_CASE_NAME, Form_Q, WL_ef,
                    sectionsize, RCQ, section_name, walldesign_index, v_pitch, v_num):
    """SA_RM4_HMD_text.m の逐語移植
    RM壁の断面算定詳細のテキスト出力
    軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    """
    S = np.atleast_2d(np.asarray(stress, dtype=float))
    steelbar_y = np.asarray(steelbar_y, dtype=float).ravel()
    steelbar_z = np.asarray(steelbar_z, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    QLw = np.atleast_2d(np.asarray(QL, dtype=float))
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    WL_ef = np.asarray(WL_ef, dtype=float).ravel()

    ratio_output = np.zeros((3, 4))
    for ie in range(1, 4):
        ratio_output[ie - 1, 0] = 0
        # NOTE: 原典8-27行 (面外曲げ Form_y / SA_RW4_HMD の検定) は
        #       全行コメントアウトされているため移植対象外 (RMは面外曲げ検討せず)

    # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う

    # 許容NM曲線を呼び出し
    e = np.zeros(3)
    M_AL = np.zeros((3, 2))
    for ie in range(1, 4):
        Form_z = [sectionsize[0], WL_ef[2 * ie - 2]]
        if S[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie - 1] = 10 ** 6
        else:
            e[ie - 1] = abs(S[ie - 1, 4]) / S[ie - 1, 0] * 1000
        M_AL[ie - 1, 1] = SA_RM4_HMD(S[ie - 1, 0], Form_z, steelbar_z, HOOP, Fm, timecase)[0]
        if e[ie - 1] == 0:
            if S[ie - 1, 0] > 0:  # 圧縮
                ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])
            else:  # 引張
                ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])
        else:
            ratio_output[ie - 1, 1] = abs(S[ie - 1, 4]) * 10 ** 6 / abs(M_AL[ie - 1, 1])

    # せん断(Q)に対する断面算定
    ratio_Q, ALW_Q, Qs1 = SA_RM4Qratio(Form_Q, HOOP, Fm, stress, timecase, QL, qup_wall)
    ratio_output[:, 2:4] = np.asarray(ratio_Q, dtype=float)
    ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
    ratio_output[1, :] = 0
    # NOTE: MATLABの線形インデックス ALW_Q(1)/ALW_Q(3) は列優先。
    #       長期はALW_Qが(3,2)行列のため order='F' で平坦化して再現する
    ALW_Q = np.asarray(ALW_Q, dtype=float).ravel(order='F')
    Qs1 = np.asarray(Qs1, dtype=float)
    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%以上で検定値の算出終了

    # RC断面の外形情報
    b = sectionsize[1]; D = sectionsize[0]

    # 帯筋情報を読み込み
    # load bar_table.mat → ヘルパー関数で代替
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    aw = np.zeros(2)
    aw[0] = Area_steelbar(di_support, HOOP[2])  # Fyせん断補強筋本数
    aw[1] = Area_steelbar(di_support, HOOP[3])  # Fzせん断補強筋数

    # 主筋情報：steelbar = [type 総本数num_steel，径D，せい方向本数nv，幅方向本数nh, SD]
    num = steelbar_z[1]; di_main = steelbar_z[2]; nv = steelbar_z[3]; nh = steelbar_z[4]; SD_main = steelbar_z[5]
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    if nh == 1:
        dd = b
    else:
        dd = (b - 2 * dc) / (nh - 1)

    if nv == 1 and num == nh:
        pass
    elif nh == 1 and num == nv:
        pass
    elif (nh - 1) * 2 + (nv - 1) * 2 == num:
        pass
    else:
        ERROR = '配筋情報ミス'
        raise ValueError(ERROR)  # MATLAB: stop
    a = Area_steelbar(di_main, 1)

    text = ['RM造壁の断面算定内容（最大検定値の検定内容）']

    text.append('軸力と面内曲げを考慮した断面算定を行う．')
    text.append('せん断については弱軸および強軸方向の検討を行う．')
    if walldesign_index == 2:
        text.append('面内の曲げについて，端部補強筋のみを考慮して断面算定を行う．（一般部の縦筋は考慮しない）')
        text.append('また直交方向壁などの縦筋，補強筋も考慮せず当該壁のみの補強筋で設計を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '　断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RM壁柱サイズ(モデル内)　：Lxt-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('NM検定サイズ（ i　端 ）　：Lxt-' + _num2str(WL_ef[0]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('NM検定サイズ（ j　端 ）　：Lxt-' + _num2str(WL_ef[4]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('Q検定サイズ（ i　端 ）　：Lxt-' + _num2str(WL_ef[1]) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('Q検定サイズ（ j　端 ）　：Lxt-' + _num2str(WL_ef[5]) + '[mm] x' + _num2str(D) + '[mm]')

    text.append('　　')
    text.append('*****配筋情報*****')
    # NOTE: 原典117-121行 (walldesign_index別の旧表記) はコメントアウト済み
    text.append('縦端部補強筋両側合計(縦筋)：' + _num2str(num) + '-D' + _num2str(di_main) +
                ' (' + _num2str(v_num) + '-D' + _num2str(di_main - 3) + '@' + _num2str(v_pitch) + ')' +
                '　横筋　：' + _num2str(v_num) + '-D' + _num2str(di_support) + '@' + _num2str(pitch))  # 240606金澤修正

    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋および組積体）*****')
    text.append('縦筋　：SD' + _num2str(SD_main) + '　横筋　：SD' + _num2str(SD_support))
    text.append('組積体　：Fm' + _num2str(Fm[0]))
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
    if steelbar_y[0] == 1:
        dd2 = (b - 2 * dc) / (nv - 1)
    elif steelbar_y[0] == 2:
        dd2 = dd
    check_dd = min(dd, dd2) - outf_steelbar_JIS(di_main)
    min_dd = max(1.5 * di_main, 25 * 1.25)
    text.append('　　')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('＊横筋間隔の規定')
    if di_support > 10 and pitch <= 400:
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
        raise ValueError('鉄筋かぶり厚再検討（40mm未満）')  # MATLAB: stop
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力（Myが面外曲げ／Mzが面内曲げ）*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による')
    else:
        text.append('せん断の設計方法：応力割増から決まる値による')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    text.append('軸力　：' + _num2str(Fxx[0] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[0] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QLw[0, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QLw[0, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 0], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 0], '%15.1f') + '[kN]')

    # NOTE: 原典198-207行 (中央断面の設計用応力表記) はコメントアウト済み

    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    text.append('軸力　：' + _num2str(Fxx[2] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[2] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[2] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QLw[2, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QLw[2, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_wall, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 2], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 2], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    e_y = []; e_z = []
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
    # text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[0, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[0, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]' + '　せん断力Qy　：' + _num2str(ALW_Q[0], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]' + '　低減後のせん断耐力Qy　：' +
                _num2str(ALW_Q[0] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMz]　：' + _num2str(ratio_output[0, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[0, 2], '%15.2f') + '　　[Qy]　：' + _num2str(ratio_output[0, 3], '%15.2f'))

    # NOTE: 原典249-258行 (中央断面の許容耐力表記) はコメントアウト済み

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    # text.append('許容曲げ　(N+My)　：' + _num2str(M_AL[2, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('許容曲げ　(N+Mz)　：' + _num2str(M_AL[2, 1] / 10 ** 6, '%15.1f') + '　[kNm]')
    text.append('せん断力Qz　：' + _num2str(ALW_Q[2], '%15.1f') + '　[kN]' + '　せん断力Qy　：' + _num2str(ALW_Q[2], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('低減後のせん断耐力Qz　：' + _num2str(ALW_Q[2] * reduction, '%15.1f') + '　[kN]' + '　低減後のせん断耐力Qy　：' +
                _num2str(ALW_Q[2] * reduction, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('検定比　[NMz]　：' + _num2str(ratio_output[2, 1], '%15.2f'))
    text.append('検定比　[Qz]　：' + _num2str(ratio_output[2, 2], '%15.2f') + '　　[Qy]　：' + _num2str(ratio_output[2, 3], '%15.2f'))
    return text


# ===========================================================================
# RM_ratio_analysis.m (補強組積造部材の断面検定) の逐語移植
# ===========================================================================

def RM_ratio_analysis(sectionsize, ele_length, stress, timecase, Fm, ele_no,
                      maxratios, maxratios_text, section_no,
                      RCcolumns, RCbeams, RCWcolumns, QL, qup_beam, qup_wall,
                      column_cover, beam_cover, wall_cover, RCbeam_secNO,
                      wall_r, node, element, load_direction, load_no,
                      ij_select, ij_reverse, LOAD_CASE_NAME, w_l, L_43, RCQ,
                      buck_length, pick_section_name, walldesign_index):
    """RM_ratio_analysis.m の逐語移植 (補強組積造部材の断面検定).

    移植済み経路:
      - RM梁 (中実角断面, sectionsize末尾-1==2000)。RCbeams(3x17)を
        旧13列書式 (3段目配筋列を削除) に落として検定 (原典30-32行)
      - RM壁柱 (中実角断面)。面外曲げ(NMy)は検討せず0 (原典247-275行
        コメントアウト)、面内(NMz)は縦筋の一段太径の端部補強筋4本のみを
        考慮 (原典281行 230415金澤)。RMなので walldesign_index=2 のみ
    未移植経路 (到達時 ValueError):
      - 長方形以外の断面形状

    返り値: (ratio_output(3x4), maxratios(1x2), maxratios_text)
    MATLAB同様、梁は列1=曲げ・列3=せん断、壁柱は列2=NMz
    列3=Qz 列4=Qy (列1=NMyは常に0、中央行は壁ではゼロ)。
    """
    # 規定チェックNG注記用の断面ラベル (要素番号は含めず断面単位で重複除去)
    _WARN_CTX['label'] = '断面%d %s' % (int(section_no),
                                        str(pick_section_name).strip())
    stress = np.array(np.atleast_2d(stress), dtype=float, copy=True)
    QL = np.array(np.atleast_2d(QL), dtype=float, copy=True)
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    Fm = np.asarray(Fm, dtype=float).ravel()
    maxratios = np.array(np.asarray(maxratios, dtype=float).ravel(), copy=True)

    stress[:, 0] = stress[:, 0] * -1
    QL[:, 0] = QL[:, 0] * -1
    sectionsize = sectionsize * 1000
    ratio_output = np.zeros((3, 4))

    # RCbeams/RCWcolumnに断面番号があるかで柱断面か梁断面かを判定する
    # 配筋としてはマイダスにはRCで入力されているのでここはRCのままでOK
    # (MATLAB find_indexは1-based/見つからない=0。+1して同じ値にする)
    if RCWcolumns is None or np.asarray(RCWcolumns).size == 0:
        wall_judge = 0
    else:
        wall_judge = find_index(np.atleast_2d(np.asarray(RCWcolumns, dtype=float))[:, 0], section_no) + 1
    if RCbeam_secNO is None or np.asarray(RCbeam_secNO).size == 0:
        beam_judge = 0
    else:
        beam_judge = find_index(np.asarray(RCbeam_secNO, dtype=float).ravel(), section_no) + 1

    # 梁として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    # RCbeams{beam_judge,2}(:, 4 7 11 14) = []
    if wall_judge == 0 and beam_judge != 0:
        # MATLABは値渡しのためRCbeamsセルの書換えは呼出し側へ波及しない。
        # Python側は共有参照になるので複製してから扱う。
        # 3x17 → 旧13列書式 [段数u,1段本数u,2段本数u,1段径u,2段径u,
        #                    段数d,1段本数d,2段本数d,1段径d,2段径d,
        #                    あばら径,ピッチ,本数]
        rcb2 = np.array(RCbeams[beam_judge - 1][1], dtype=float,
                        copy=True)[:, [0, 1, 2, 4, 5, 7, 8, 9, 11, 12, 14, 15, 16]]
        QL = QL[:, 1:3]
        if L_43 is None or np.asarray(L_43).size == 0:
            L43 = 1.0
        elif find_index(np.atleast_2d(np.asarray(L_43, dtype=float))[:, 0], section_no) + 1 > 0:
            L43 = 4.0 / 3
        else:
            L43 = 1.0

        if sectionsize[len(sectionsize) - 2] == 2000:  # 中実角断面の検定
            Form = np.array([sectionsize[1], sectionsize[0]])
            Fm = np.array([Fm[1], 0.0])  # 普通コンとする
            # HOOP:あばら筋径D，ピッチ，本数，SD295，かぶり40mm
            if rcb2[0, 10] > 28:
                SD = 390
            elif rcb2[0, 10] > 18:
                SD = 345
            else:
                SD = 295
            HOOP = np.concatenate([rcb2[0, 10:13], [SD, beam_cover]])

            if rcb2[0, 4] > 28:
                SD = 390
            elif rcb2[0, 4] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)

            def _rows(rows, cols):
                """rcb2の行インデックス(1-based)列を並べて3x5を作る."""
                return np.vstack([rcb2[r - 1, cols[0] - 1:cols[1]] for r in rows])

            # steelbar_u:段数(up)，一段目本数(up)，二段目本数(up)，径D(up)，SD
            if ij_select == 0:
                steelbar_u = np.hstack([rcb2[:, 0:5], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 5)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 5)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([1, 1, 2], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([1, 2, 2], (1, 5)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([2, 3, 3], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 3], (1, 5)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 5)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 5)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([3, 3, 2], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([3, 2, 2], (1, 5)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([2, 1, 1], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 1], (1, 5)), SD3])

            if rcb2[0, 9] > 28:
                SD = 390
            elif rcb2[0, 9] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)
            # steelbar_d:段数(down)，一段目本数(down)，二段目本数(down)，径D(down)，SD

            if ij_select == 0:
                steelbar_d = np.hstack([rcb2[:, 5:10], SD3])
            else:
                if ij_reverse == 1:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (6, 10)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (6, 10)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[0, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([1, 1, 2], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([1, 2, 2], (6, 10)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([2, 3, 3], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 3], (6, 10)), SD3])
                else:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (6, 10)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (6, 10)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([3, 3, 2], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([3, 2, 2], (6, 10)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([2, 1, 1], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 1], (6, 10)), SD3])

            # 強軸周りの許容曲げモーメントに対して検定を行う

            RM_ALW_M, up = sub_RM4beam_ALWM(Form, steelbar_u, steelbar_d,
                                            HOOP, Fm, timecase, L43)

            # 曲げモーメントの検定
            for ie in range(3):
                if stress[ie, 3] >= 0:
                    ratio_output[ie, 0] = stress[ie, 3] * up / RM_ALW_M[ie, 0]
                else:
                    ratio_output[ie, 0] = stress[ie, 3] * up / RM_ALW_M[ie, 1]
            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            q8 = SA_RMbeamQratio(Form, ele_length, steelbar_u, steelbar_d,
                                 HOOP, Fm, stress, timecase, QL[:, 1],
                                 qup_beam, RCQ, RM_ALW_M)
            ratio_output[:, 2] = np.asarray(q8[0], dtype=float).ravel()
            ALW_Q = q8[1]

            # 最大検定値の指定
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RMbeamratio_text(
                    Form, ele_length, steelbar_u, steelbar_d, HOOP, Fm,
                    stress, timecase, QL[:, 1], ele_no, section_no, RM_ALW_M,
                    qup_beam, up, LOAD_CASE_NAME, pick_section_name, RCQ)

        else:
            raise ValueError(
                'ERROR:RM梁の断面形状設定ミス＜長方形でない梁＞ (断面番号%d)'
                % int(section_no))

    # 壁として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # RMなのでwalldesign_index=2のみ
    elif wall_judge != 0 and beam_judge == 0:
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

            Fm = np.array([Fm[1], 0.0])  # 普通コンとする
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

            for ie in range(3):  # RMでは面外曲げは検討せず
                ratio_output[ie, 0] = 0
                # NOTE: 原典249-274行 (面外曲げ Form_y / SA_RM4_HMD による
                #       NMy検定) は全行コメントアウトされているため移植対象外

            # 軸力と弱軸周りの許容曲げモーメントに対して検定を行う
            # NOTE: load bar_table.mat → bar_table_next_diameter で代替
            # A_RCWcolumns(wall_judge,:)=[RCWcolumns(wall_judge,1:10)];
            # 230415金澤: 端部補強筋 = 縦筋の一段太径を両端2本ずつ(計4本)配置
            A_RCWcolumns = np.concatenate([
                RCWcolumns[wj, 0:2],
                [4, bar_table_next_diameter(RCWcolumns[wj, 3]), 2, 2],
                RCWcolumns[wj, 6:10]])
            if A_RCWcolumns[3] > 28:
                SD = 390
            elif A_RCWcolumns[3] > 18:
                SD = 345
            else:
                SD = 295

            v_pitch = RCWcolumns[wj, 10]  # 230415金澤
            v_num = RCWcolumns[wj, 4]  # 230415金澤

            # steelbar_z = [A_RCWcolumns(wall_judge,2:4) A_RCWcolumns(wall_judge,6) A_RCWcolumns(wall_judge,5) SD 0];
            steelbar_z = np.concatenate([A_RCWcolumns[1:6], [SD, 0]])  # 230415金澤

            # 許容NM曲線を呼び出し
            e = np.zeros(3)
            for ie in range(1, 4):
                Form_z = np.array([sectionsize[0], WL_ef[2 * ie - 2]])
                if stress[ie - 1, 0] == 0:  # 軸力がゼロ→曲げのみで検定
                    e[ie - 1] = 10 ** 6
                else:
                    e[ie - 1] = abs(stress[ie - 1, 4]) / stress[ie - 1, 0] * 1000
                # [N M Xn]=SA_RW4_AIJ(e(ie),Form_z,steelbar_z,HOOP,Fm,timecase); (原典コメントアウト)
                M, maxN = SA_RM4_HMD(stress[ie - 1, 0], Form_z, steelbar_z,
                                     HOOP, Fm, timecase)
                if e[ie - 1] == 0:
                    if stress[ie - 1, 0] > 0:  # 圧縮
                        ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0])
                    else:  # 引張
                        ratio_output[ie - 1, 1] = abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1])
                else:
                    if stress[ie - 1, 0] > 0:  # 圧縮
                        ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[0]))
                    else:  # 引張
                        ratio_output[ie - 1, 1] = max(abs(stress[ie - 1, 4]) * 10 ** 6 / abs(M),
                                                      abs(stress[ie - 1, 0]) * 10 ** 3 / abs(maxN[1]))

            # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
            Form_Q = np.array([[WL_ef[1], sectionsize[0]],
                               [WL_ef[3], sectionsize[0]],
                               [WL_ef[5], sectionsize[0]]])
            rq, ALW_Q, Qs1 = SA_RM4Qratio(Form_Q, HOOP, Fm, stress, timecase,
                                          QL, qup_wall, RCQ)
            ratio_output[:, 2:4] = np.atleast_2d(rq)
            ratio_output[:, 2:4] = ratio_output[:, 2:4] / reduction
            ratio_output[1, :] = 0
            # 最大検定値の保存
            if np.max(ratio_output) > maxratios[1]:
                maxratios[0] = ele_no
                maxratios[1] = np.max(ratio_output)
                maxratios_text = SA_RM4_HMD_text(
                    ele_length, steelbar_y, steelbar_z, HOOP, Fm, stress,
                    timecase, QL, ele_no, section_no, qup_wall, reduction,
                    LOAD_CASE_NAME, Form_Q, WL_ef, sectionsize, RCQ,
                    pick_section_name, walldesign_index, v_pitch, v_num)

        else:
            raise ValueError('ERROR:RC壁断面形状設定ミス (断面番号%d)'
                             % int(section_no))

    else:  # ここでRCの断面情報を決めないといけない
        # MATLABは表示のみで検定比0のまま続行するが、mgtkitでは検定漏れ防止の
        # ため明示エラーとする (rc_check.RC_ratio_analysis と同じ方針)
        raise ValueError(
            'RC断面（配筋情報）が定義されていません (断面番号%d)。'
            '検定タブの「RC壁配筋設定」で壁として配筋を指定するか、'
            'MIDASの梁配筋(*REBAR-BEAM)を設定してください' % int(section_no))

    return ratio_output, maxratios, maxratios_text
