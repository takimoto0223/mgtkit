# -*- coding: utf-8 -*-
"""PC(プレストレストコンクリート)梁 断面検定 (MATLAB原典からの逐語移植).

元コード (MatlabT/):
  privatetool_function/MIDAS/ratio/RC/PC_ratio_analysis.m          (主関数 209行)
  structural_function/RC/RC_section_analysis/beam/sub_PC3_4beam_ALWM.m  (462行)
  structural_function/RC/RC_section_analysis/beam/SA_PCbeamQratio.m     (146行)
  structural_function/RC/RC_section_analysis/beam/SA_PC3beamratio_text.m(696行)
  structural_function/RC/RC_section_analysis/PC_info.mat
      → 本モジュール内の cable_name / cable_data 定数として埋め込み

スコープ: Ⅲ種PC梁 (PC_type=許容ひび割れ幅[mm]) の中実角断面のみ。
  フルプレストレス(PC_type=1)・パーシャルプレストレス(PC_type=2)・PC柱は
  原典どおり未作成 (stop → ValueError)。

主要入力 (MIDASratioplot_fig.m のPC入力ダイアログ由来):
  PC_slab      : n行4列 [断面番号, スラブ厚t(mm), 曲げ協力幅tbm(mm), 軸協力幅tbn(mm)]
                 (PC_slab.txt を textread('%n%f%f%f') で読込)
  PC_eff       : プレストレス有効率 (0.8プレテンション / 0.85ポストテンション / 直接指定)
  pc_stress    : PC不静定応力。主関数入口では (3n)行8列
                 [要素番号, 荷重ケース?, N, Fy, Fz, Mx, My, Mz]
                 (pc_stress.txt を textread('%n%n%f%f%f%f%f%f') で読込)
                 主関数内で要素番号一致行から3行 (i/中央/j)・列[3 4 5 7 8]
                 → [N, Fy, Fz, My, Mz] (kN, kNm) を抽出して下位関数へ渡す
  PC_type      : 1=フル / 2=パーシャル / それ以外=Ⅲ種PC(値=許容ひび割れ幅mm, 既定0.1)
  DL_ratio     : 長期荷重に占めるDL(固定荷重)の割合 (既定0.6)
  cable_delta  : n行4列 [要素番号, δi(m), δ中央(m), δj(m)]
                 (mgtファイル *PRESTRESS ブロック由来。梁せい中心からのケーブル
                  偏心。プラス=上側/マイナス=下側)
  cable_select : n行3列 [断面番号, ケーブル種別index(1-based, cable_data行),
                 ケーブル総本数] (pc_cable.txt またはダイアログ選択)

RCbeams: PC経路では RCbeams{k,2} の列1-5=上端筋[段数,一段目本数,二段目本数,
  一段目径,二段目径]、列6-10=下端筋[同]、列11-13=STRP[径,ピッチ,本数] を参照する
  (RC経路の17列書式とは列割りが異なる点に注意)。

単位系: PC_ratio_analysis 入口で sectionsize×1000 (m→mm)。応力はkN, kNm。
インデックス規約: util.find_index は 0-based/-1 (MATLABは1-based/0)。
"""

import math

import numpy as np

from .util import find_index, excelround
from .s_check import _num2str
from .rc_check import (Area_steelbar, outf_steelbar_JIS, ALST_RC_AIJ,
                       ALST_steelbar_KJ, E_RC_AIJ)


# ===========================================================================
# PC_info.mat (structural_function/RC/RC_section_analysis/PC_info.mat)
# ===========================================================================

# cable_name: PC鋼より線の呼び名 (JIS G 3536)
cable_name = ['SWPR7A-12.4', 'SWPR7B-12.7', 'SWPR7B-15.2',
              'SWPR19-17.8', 'SWPR19-19.3', 'SWPR19-21.8']

# cable_data: 6種×10列
#   [径(mm), 公称断面積(mm2), 単位質量?(g/m), 引張荷重(kN), 降伏荷重(kN)(=列5, Ty算定用),
#    0.8Pu?(kN), 0.8Py?(kN), ヤング係数?(kN/mm2), (kN), (kN)]
cable_data = np.array([
    [12.4,  92.90,  729.0, 160.0, 136.0, 115.6, 108.8, 200.0, 115.60, 108.8],
    [12.7,  98.71,  774.0, 183.0, 156.0, 132.6, 124.8, 200.0, 132.60, 124.8],
    [15.2, 138.70, 1101.0, 261.0, 222.0, 188.7, 177.6, 200.0, 188.70, 177.6],
    [17.8, 208.40, 1652.0, 387.0, 330.0, 280.5, 264.0, 200.0, 280.50, 264.0],
    [19.3, 243.70, 1931.0, 451.0, 387.0, 328.9, 309.6, 200.0, 328.95, 309.6],
    [21.8, 312.90, 2482.0, 573.0, 495.0, 420.7, 396.0, 200.0, 420.75, 396.0],
])


# ===========================================================================
# sub_PC3_4beam_ALWM.m
# ===========================================================================

def sub_PC3_4beam_ALWM(Form, steelbar_u, steelbar_d, HOOP, Fc, timecase, L43,
                       slab_t, pc_stress, PC_eff, PC_type, stress, QL,
                       DL_ratio, cable_delta, cable_select):
    """sub_PC3_4beam_ALWM.m の逐語移植

    元ファイル: structural_function/RC/RC_section_analysis/beam/sub_PC3_4beam_ALWM.m

    建築学会PRC基準/PRC梁断面算定
    2014/02/20ソース作成
    Form：[はり幅b,はりせいD]
    steelbar_u = [段数，一段目本数，二段目本数，一段目径D，二段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    steelbar_d = [段数，一段目本数，二段目本数，一段目径D，二段目径D，SD，鉄筋間隔（二段配筋の時）]これをi端，中央，j端の三行
    HOOP=[径D，pitch, n, SD, cover_depth] Fc=[21 1]   %[Fc 普通(0)軽量1種(1)軽量2種(2)を示す]
    stress = [軸力，強軸方向せん断，弱軸方向せん断，強軸まわり曲げモーメント，弱軸まわり曲げモーメント]これをi端，中央，j端の三行
    timecase：1（長期) 10以上で短期
    slab_t =[スラブ厚 曲げ協力幅　軸協力幅]
    pc_stress 設計時のプレストレス力（８５％or８０％定着完了時の応力）
    PC_eff 有効率(0.85 or 0.80)

    返り値: ratio (3x2 ndarray)
      長期(timecase=1): 列1=導入時/長期応力度+ひび割れM検定、列2=鉛直終局曲げ検定
      短期(timecase>=10): 列1=水平終局曲げ検定、列2=0
    ※L43は原典どおり未使用 (引数のみ)。
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    slab_t = np.asarray(slab_t, dtype=float).ravel()
    pc_stress = np.asarray(pc_stress, dtype=float)
    stress = np.asarray(stress, dtype=float)
    QL = np.asarray(QL, dtype=float)
    cable_delta = np.asarray(cable_delta, dtype=float).ravel()
    cable_select = np.asarray(cable_select, dtype=float).ravel()

    ratio = np.zeros((3, 2))

    # %%長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    elif timecase == 9:
        timecase = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: 原典26行 stopなし(表示のみで続行)
        print('ERROR =', ERROR)

    # RC断面の外形情報
    b = Form[0]; D = Form[1]
    t = slab_t[0]
    tbm = slab_t[1]
    tbn = slab_t[2]

    if tbm == 0:
        tbm = b
    if tbn == 0:
        tbn = b

    Am = (D - t) * b + tbm * t  # mm2
    An = (D - t) * b + tbn * t  # mm2
    xg1 = (tbm * t * t / 2 + (D - t) * b * (D + t) / 2) / Am

    I = b * (2 * D) ** 3 / 24 + (tbm - b) * (2 * t) ** 3 / 24 - Am * xg1 ** 2
    Zt = I / xg1
    Zb = I / (D - xg1)

    # 配筋情報
    # 配筋情報の入力されたmatファイル呼び出し
    # load bar_table.mat → rc_check側ヘルパーで代替
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

    # %%%%%許容応力度関係%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    f_c = ALST_RC_AIJ(Fc)  # 圧縮許容応力度
    Ft = 0.07 * Fc[0]
    Ftb = 5.0 / 3 * Ft  # NOTE: 原典128行 未使用変数(そのまま保持)
    fs = f_c[0, 2]  # 長期せん断応力度 (NOTE: 原典129行 未使用変数)
    rfc = [[None, None, None], [None, None, None]]
    for ii in range(2):
        for jj in range(3):
            rfc[ii][jj] = ALST_steelbar_KJ([di_main[ii, jj], SD_main[ii, jj]])

    # ヤング係数比(RC基準で設定された断面解析用のヤング係数比）
    n = E_RC_AIJ(Fc)
    n = n[1]
    a = Area_steelbar(di_main, 1)

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔
    outf_main = np.array([[outf_steelbar_JIS(di_main[i_, j_]) for j_ in range(3)]
                          for i_ in range(2)], dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        dd2 = (b - 2 * d_out) / (np.vstack([np.max(steelbar_u[:, 1:3], axis=1),
                                            np.max(steelbar_d[:, 1:3], axis=1)]) - 1)

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
        # stop

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
        up = 1.0  # NOTE: 原典174-180行 両分岐ともup=1.0 (RC版と異なりL43割増なし)
    else:
        up = 1.0
        # ERROR='引張鉄筋断面積不足（0.4％未満）'
        # stop

    # その３：HOOP間隔の規定
    # NOTE: 原典183-184行 max(D/2,250)/max(D/2,450) (テキスト版SA_PC3beamratio_textはmin)
    if di_support == 10 and pitch <= max(D / 2, 250):
        pass
    elif di_support > 10 and pitch <= max(D / 2, 450):
        pass
    else:
        print('STRP筋間隔NG→STRPピッチ' + _num2str(pitch, '%15.0f') + 'mm，　STRP径：D'
              + _num2str(di_support, '%15.0f') + '，　梁せい：' + _num2str(D, '%15.0f') + 'mm')
        # stop

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, HOOP[2])

    if aw / (b * pitch) >= 0.2 / 100:
        pass
    else:
        print('せん断補強筋比不足（0.2％未満）　pw=' + _num2str(aw / (b * pitch) * 100, '%15.2f')
              + '%→STRPピッチ' + _num2str(pitch, '%15.0f') + 'mm，　STRP径：D'
              + _num2str(di_support, '%15.0f') + '，　梁幅b：' + _num2str(b, '%15.0f') + 'mm')
        # stop

    # その５：鉄筋かぶりあつ
    if cover_depth >= 40:
        pass
    else:
        ERROR = '鉄筋かぶり厚再検討（40mm未満）'
        raise ValueError(ERROR)  # MATLAB: stop

    if timecase == 1:

        # ひび割れ幅から許容モーメントの算出(正曲げ)%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        ip = 2.0 / 10000  # 柱梁は2.0*10-4
        max_crack = PC_type
        ave_crack = max_crack / 1.5
        with np.errstate(divide='ignore', invalid='ignore'):
            s1 = (b - 2 * cover_depth) / (steelbar_d[:, 1] - 1)
        at1 = a[1, :] * steelbar_d[:, 1]
        at_sum1 = a[1, :] * np.sum(steelbar_d[:, 1:3], axis=1)
        pe1 = at1 / (b * (2 * cover_depth + di_main[1, :]))
        lav1 = 2 * (cover_depth + s1 / 10) + 0.1 * di_main[1, :] / pe1
        ip_t_ave1 = ave_crack / lav1 - ip
        k1k21 = 1.25 / (2.5 * 10 ** 3 * ip_t_ave1 + 1)
        sig_t1 = ip_t_ave1 * 205000 + k1k21 * Ft / pe1
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

        # ひび割れ幅から許容モーメントの算出(負曲げ)%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        ip = 2.0 / 10000  # 柱梁は2.0*10-4
        max_crack = PC_type
        ave_crack = max_crack / 1.5
        with np.errstate(divide='ignore', invalid='ignore'):
            s = (b - 2 * cover_depth) / (steelbar_u[:, 1] - 1)
        at2 = a[0, :] * steelbar_u[:, 1]
        at_sum2 = a[0, :] * np.sum(steelbar_u[:, 1:3], axis=1)
        pe2 = at2 / (b * (2 * cover_depth + di_main[0, :]))
        lav2 = 2 * (cover_depth + s / 10) + 0.1 * di_main[0, :] / pe2
        ip_t_ave2 = ave_crack / lav2 - ip
        k1k21 = 1.25 / (2.5 * 10 ** 3 * ip_t_ave2 + 1)
        sig_t2 = ip_t_ave2 * 205000 + k1k21 * Ft / pe2
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

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
        rfc_t[0] = sig_t1[0]
        rfc_t[1] = sig_t1[1]
        rfc_t[2] = sig_t1[2]
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
        rfc_t[0] = sig_t2[0]
        rfc_t[1] = sig_t2[1]
        rfc_t[2] = sig_t2[2]
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

        # 長期許容ひび割れ曲げモーメントの算出%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # 軸力の寄与成分
        sig_g = stress[:, 0] * 10 ** 3 / Am  # N/mm2
        add_M1 = Zb * sig_g / 10 ** 6
        add_M2 = Zt * sig_g / 10 ** 6 * -1

        M = np.minimum(M1, M3)
        M = np.minimum(M, M2)
        ALWM1 = np.zeros((2, 3))
        ALWM1[0, :] = M[0, :] / 10 ** 6 + add_M1
        ALWM1[1, :] = M[1, :] / 10 ** 6 * -1 + add_M2
        ALWM1 = ALWM1.T

        for j in range(3):
            if stress[j, 3] > 0:
                ratio[j, 0] = stress[j, 3] / ALWM1[j, 0]
            else:
                ratio[j, 0] = stress[j, 3] / ALWM1[j, 1]

        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # %%導入時の検討
        # NOTE: 原典334行 +pc_stress (テキスト版441行は +pc_stress*(1/PC_eff) で不一致)
        DLX_stress = (stress - pc_stress * PC_eff) * DL_ratio + pc_stress
        # NOTE: ratio_oneはMATLABでは自動拡張(未代入要素0)。zeros(4,3)初期化で等価
        ratio_one = np.zeros((4, 3))
        sig0 = np.zeros((3, 2))
        # 上側
        # NOTE: 原典336行 軸力項 DLX_stress(:,1)./An はkN/mm2のまま
        #       (10^3倍されていない。312行のsig_gは10^3倍あり) → 逐語再現
        sig0[:, 0] = DLX_stress[:, 3] * 10 ** 6 / Zt + DLX_stress[:, 0] / An
        for j in range(3):
            if sig0[j, 0] >= 0:  # 圧縮
                ratio_one[0, j] = sig0[j, 0] / f_c[timecase - 1, 0]
            else:
                pass
        # 下側
        sig0[:, 1] = -1 * DLX_stress[:, 3] * 10 ** 6 / Zb + DLX_stress[:, 0] / An
        for j in range(3):
            if sig0[j, 1] >= 0:  # 圧縮
                ratio_one[1, j] = sig0[j, 1] / f_c[timecase - 1, 0]
            else:
                pass

        # %%設計時の検討
        TLX_stress = stress
        sig1 = np.zeros((3, 2))
        # 上側
        sig1[:, 0] = TLX_stress[:, 3] * 10 ** 6 / Zt + TLX_stress[:, 0] / An
        for j in range(3):
            if sig1[j, 0] >= 0:  # 圧縮
                ratio_one[2, j] = sig1[j, 0] / f_c[timecase - 1, 0]
            else:
                pass
        # 下側
        sig1[:, 1] = -1 * TLX_stress[:, 3] * 10 ** 6 / Zb + TLX_stress[:, 0] / An
        for j in range(3):
            if sig1[j, 1] >= 0:  # 圧縮
                ratio_one[3, j] = sig1[j, 1] / f_c[timecase - 1, 0]
            else:
                pass
        # NOTE: 原典371行 max([ratio(:,1);max(ratio_one,[],1)'],[],1) は縦連結後の
        #       dim1のmaxでスカラとなり、3行すべてに同一値が入る
        #       (テキスト版478行は行ごとのmaxで結果が異なる) → 逐語再現
        ratio[:, 0] = np.max(np.concatenate([ratio[:, 0], np.max(ratio_one, axis=0)]))

        # load 'PC_info.mat' → モジュール定数 cable_data
        Ty = cable_data[int(cable_select[0]) - 1, 4] * cable_select[1]  # kN

        MU = np.zeros((2, 3))
        # 終局曲げの検討(正曲げ)
        dp = D / 2 - cable_delta * 1000
        d = D - d_out[1, :]  # NOTE: 原典379行 変数d(2x3)をここで1x3に上書き
        for j in range(3):
            if dp[j] < D / 2:
                MU[0, j] = 0.9 * at_sum1[j] * rfc[1][j][1, 0] * d[j]
            else:
                MU[0, j] = 7.0 / 8 * at_sum1[j] * rfc[1][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)

        # 終局曲げの検討(負曲げ)
        dp = D / 2 + cable_delta * 1000
        d = D - d_out[0, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[1, j] = 0.9 * at_sum2[j] * rfc[0][j][1, 0] * d[j] * -1
            else:
                MU[1, j] = (7.0 / 8 * at_sum2[j] * rfc[0][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)) * -1

        # 1.7(G+P)+0.85X
        TLX_stress1 = (stress - PC_eff * pc_stress) * 1.7 + PC_eff * pc_stress

        # 1.2G+2.0P+0.85X
        TLX_stress2 = (stress - PC_eff * pc_stress) * DL_ratio * 1.2 + (stress - PC_eff * pc_stress) * (1 - DL_ratio) * 2.0 + PC_eff * pc_stress

        for j in range(3):
            # NOTE: 原典410-418行 MU[N・mm]に対しTLX[kNm]をそのまま除しており
            #       *10^6が無い(テキスト版564行は*10^6あり)。検定値が10^-6倍と
            #       なる原典の単位不整合を逐語再現
            if TLX_stress1[j, 3] >= 0:  # 正曲げ
                ratio[j, 1] = TLX_stress1[j, 3] / MU[0, j]
            else:
                ratio[j, 1] = TLX_stress1[j, 3] / MU[1, j]

            if TLX_stress2[j, 3] >= 0 and abs(TLX_stress2[j, 3] / MU[0, j]) > ratio[j, 1]:  # 正曲げ
                ratio[j, 1] = TLX_stress2[j, 3] / MU[0, j]
            elif TLX_stress2[j, 3] < 0 and abs(TLX_stress2[j, 3] / MU[1, j]) > ratio[j, 1]:
                ratio[j, 1] = TLX_stress2[j, 3] / MU[1, j]

    else:  # 水平（終局）

        at_sum1 = a[1, :] * np.sum(steelbar_d[:, 1:3], axis=1)
        at_sum2 = a[0, :] * np.sum(steelbar_u[:, 1:3], axis=1)
        # load 'PC_info.mat' → モジュール定数 cable_data
        Ty = cable_data[int(cable_select[0]) - 1, 4] * cable_select[1]  # kN
        # 終局曲げの検討
        MU = np.zeros((2, 3))
        # 終局曲げの検討(正曲げ)
        dp = D / 2 - cable_delta * 1000
        d = D - d_out[1, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[0, j] = 0.9 * at_sum1[j] * rfc[1][j][1, 0] * d[j]
            else:
                MU[0, j] = 7.0 / 8 * at_sum1[j] * rfc[1][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)

        # 終局曲げの検討(負曲げ)
        dp = D / 2 + cable_delta * 1000
        d = D - d_out[0, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[1, j] = 0.9 * at_sum2[j] * rfc[0][j][1, 0] * d[j] * -1
            else:
                MU[1, j] = (7.0 / 8 * at_sum2[j] * rfc[0][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)) * -1

        # G+P+0.85X+1.5K
        KX_stress = (stress - QL) * 1.5 + QL
        for j in range(3):
            # NOTE: 原典456-460行 こちらも*10^6無し(テキスト版648行は*10^6あり) → 逐語再現
            if KX_stress[j, 3] >= 0:  # 正曲げ
                ratio[j, 0] = KX_stress[j, 3] / MU[0, j]
            else:
                ratio[j, 0] = KX_stress[j, 3] / MU[1, j]

    return ratio


# ===========================================================================
# SA_PCbeamQratio.m
# ===========================================================================

def SA_PCbeamQratio(Form, L, steelbar_u, steelbar_d, HOOP, Fc, stress,
                    timecase, QL, qup_beam, pc_stress, DL_ratio, PC_eff):
    """SA_PCbeamQratio.m の逐語移植 (RC梁の許容せん断力算定)

    元ファイル: structural_function/RC/RC_section_analysis/beam/SA_PCbeamQratio.m

    返り値: (ratio_Q(3x2), ALW_Q(3x2), alph(1x3), j(1x3), Mmax, Qmax)
      長期(timecase=1): ratio_Q列1=長期せん断、列2=鉛直終局せん断
      短期(timecase>=10): ratio_Q列1=短期せん断(割増qup_beam)、列2=0
    QLは主関数から渡される3x5行列 [N,Fy,Fz,My,Mz] をそのまま使用
    (QL(:,3)=Fz参照。RC版のQL2列書式とは異なる)。
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    stress = np.asarray(stress, dtype=float)
    QL = np.asarray(QL, dtype=float)
    pc_stress = np.asarray(pc_stress, dtype=float)

    ratio_Q = np.zeros((3, 2))
    ALW_Q = np.zeros((3, 2))
    # RC断面の外形情報[mm単位で入力]
    b = Form[0]; D = Form[1]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        # t_case ='短期'
    elif timecase == 1:
        timecase = 1
        # t_case ='長期'
    elif timecase == 9:
        timecase = 1
        # t_case ='長期'
    else:
        ERROR = '長短期設定ミス'
        raise ValueError(ERROR)  # MATLAB: stop

    # 帯筋情報を読み込み
    # load bar_table.mat → rc_check側ヘルパーで代替
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[3]; cover_depth = HOOP[4]
    wft = ALST_steelbar_KJ([di_support, SD_support])
    aw = Area_steelbar(di_support, HOOP[2])

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
            # NOTE: 原典61行 excelround利用 (sub_PC3_4beam_ALWMのceil(.../10)*10と異なる)
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

    a = Area_steelbar(di_main, 1)
    f_c = ALST_RC_AIJ(Fc)
    rfc = [[None, None, None], [None, None, None]]
    for i in range(2):
        for j in range(3):
            rfc[i][j] = ALST_steelbar_KJ([di_main[i, j], SD_main[i, j]])

    Mmax = 0.0
    Qmax = 0.0
    alph = np.array([1.0, 1.0, 1.0])
    j_dis = np.zeros(3)

    # 許容せん断力の算定（長期）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if timecase == 1:
        Mmax = np.max(np.abs(stress[:, 3]))
        Qmax = np.max(np.abs(stress[:, 2]))
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(2, 4.0 / (1 + (Mmax / Qmax / np.max(D - d, axis=0) * 1000))))
        j_dis = 0.875 * np.max(D - d, axis=0)
        ALW_Q[:, 0] = b * j_dis * (alph * f_c[0, 2] + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002) * wft[0, 1]) / 10 ** 3
        ratio_Q[:, 0] = np.abs(stress[:, 2]) / ALW_Q[:, 0]

        # 終局の確認
        # 1.7(G+P)+0.85X
        # NOTE: 原典121行 PC_eff無し (sub版402行は(stress-PC_eff*pc)*1.7+PC_eff*pc、
        #       テキスト版556行は(stress-PC_eff*pc)*1.7+pc で三者三様) → 逐語再現
        TLX_stress1 = (stress - pc_stress) * 1.7 + pc_stress
        # 1.2G+2.0P+0.85X
        TLX_stress2 = (stress - pc_stress) * DL_ratio * 1.2 + (stress - pc_stress) * (1 - DL_ratio) * 2.0 + pc_stress

        designQu = np.max(np.column_stack([TLX_stress1[:, 2], TLX_stress2[:, 2]]), axis=1)
        ALW_Q[:, 1] = b * j_dis * (alph * f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002) * wft[1, 1]) / 10 ** 3
        ratio_Q[:, 1] = np.abs(designQu) / ALW_Q[:, 1]

    if timecase >= 2:

        Qs = qup_beam * np.abs(stress[:, 2] - QL[:, 2]) + np.abs(QL[:, 2])

        # 許容せん断力（短期）
        Mmax = np.max(np.abs(stress[:, 3]))  # kNm
        Qmax = np.max(np.abs(stress[:, 2]))  # kN
        if Qmax == 0:
            alph = np.array([1.0, 1.0, 1.0])
        else:
            alph = np.maximum(1, np.minimum(2, 4.0 / (1 + (Mmax / Qmax / np.max(D - d, axis=0) * 1000))))
        j_dis = (7.0 / 8) * np.max(D - d, axis=0)
        ALW_Q[:, 0] = b * j_dis * (alph * f_c[1, 2] + 0.5 * (min(1.2 / 100, aw / (b * pitch)) - 0.002) * wft[1, 1]) / 10 ** 3
        ratio_Q[:, 0] = Qs / ALW_Q[:, 0]
        ALW_Q[:, 1] = 0

    return ratio_Q, ALW_Q, alph, j_dis, Mmax, Qmax


# ===========================================================================
# SA_PC3beamratio_text.m
# ===========================================================================

def SA_PC3beamratio_text(Form, ele_length, steelbar_u, steelbar_d, HOOP, Fc,
                         stress, timecase, slab_t, pc_stress, PC_eff, PC_type,
                         QL, DL_ratio, pick_cable_delta, pick_cable_select,
                         ele_no, section_no, qup_beam, LOAD_CASE_NAME,
                         section_name):
    """SA_PC3beamratio_text.m の逐語移植

    元ファイル: structural_function/RC/RC_section_analysis/beam/SA_PC3beamratio_text.m

    PCⅢ種梁の断面算定詳細のテキスト出力
    曲げ(MM)，せん断(Q)に対する断面算定

    返り値: text (list of str, MATLAB strvcat相当)
    ※qup_beam引数は原典どおり未使用 (Q算定は2.0固定)。
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar_u = np.asarray(steelbar_u, dtype=float)
    steelbar_d = np.asarray(steelbar_d, dtype=float)
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    stress = np.asarray(stress, dtype=float)
    slab_t = np.asarray(slab_t, dtype=float).ravel()
    pc_stress = np.asarray(pc_stress, dtype=float)
    QL = np.asarray(QL, dtype=float)
    pick_cable_delta = np.asarray(pick_cable_delta, dtype=float).ravel()
    pick_cable_select = np.asarray(pick_cable_select, dtype=float).ravel()

    # せん断(Q)に対する断面算定
    ratio_output = np.zeros((3, 4))
    rq, ALW_Q, alph, j_dis, Mmax, Qmax = SA_PCbeamQratio(
        Form, ele_length, steelbar_u, steelbar_d, HOOP, Fc, stress, timecase,
        QL, 2.0, pc_stress, DL_ratio, PC_eff)
    ratio_output[:, 1] = rq[:, 0]
    ratio_output[:, 3] = rq[:, 1]
    alph = np.asarray(alph, dtype=float).ravel()
    j_dis = np.asarray(j_dis, dtype=float).ravel()

    Fxx = stress[:, 0] * 10 ** 3  # 軸力[N]
    My = stress[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = stress[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = stress[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = stress[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # %%長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    elif timecase == 9:
        timecase = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: 原典28行 stopなし(表示のみで続行)
        print('ERROR =', ERROR)

    # RC断面の外形情報
    b = Form[0]; D = Form[1]
    t = slab_t[0]
    tbm = slab_t[1]
    tbn = slab_t[2]
    # NOTE: 原典33-35行 tbm==0/tbn==0のb置換なし (sub_PC3_4beam_ALWM 35-40行にはあり)

    Am = (D - t) * b + tbm * t  # mm2
    An = (D - t) * b + tbn * t  # mm2
    xg1 = (tbm * t * t / 2 + (D - t) * b * (D + t) / 2) / Am

    I = b * (2 * D) ** 3 / 24 + (tbm - b) * (2 * t) ** 3 / 24 - Am * xg1 ** 2
    Zt = I / xg1
    Zb = I / (D - xg1)

    # 配筋情報
    # 配筋情報の入力されたmatファイル呼び出し
    # load bar_table.mat → rc_check側ヘルパーで代替
    # 帯筋情報を読み込み
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[3]; cover_depth = HOOP[4]
    aw = Area_steelbar(di_support, HOOP[2])
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

    # %%%%%許容応力度関係%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    f_c = ALST_RC_AIJ(Fc)  # 圧縮許容応力度
    Ft = 0.07 * Fc[0]
    Ftb = 5.0 / 3 * Ft  # NOTE: 原典123行 未使用変数
    fs = f_c[0, 2]  # 長期せん断応力度 (NOTE: 原典124行 未使用変数)
    rfc = [[None, None, None], [None, None, None]]
    for ii in range(2):
        for jj in range(3):
            rfc[ii][jj] = ALST_steelbar_KJ([di_main[ii, jj], SD_main[ii, jj]])

    # ヤング係数比(RC基準で設定された断面解析用のヤング係数比）
    n = E_RC_AIJ(Fc)
    n = n[1]
    a = Area_steelbar(di_main, 1)

    text = ['PC梁(３種PC)の断面算定内容（最大検定値の検定内容）']
    text.append('強軸周りの曲げとせん断に対して断面算定（ひび割れ幅の検討および終局耐力の確認）を行う．')
    text.append('　　')

    text.append('断面符号：' + str(section_name) + '断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('PC梁サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]　PC梁スパン[m]：' + _num2str(ele_length, '%15.2f'))
    text.append('スラブ厚さ' + _num2str(t, '%15.0f') + '[mm] '
                + '　曲げ協力幅：' + _num2str(tbm / 1000, '%15.2f') + '[m]　軸協力幅[m]：' + _num2str(tbn / 1000, '%15.2f') + '[m]')

    text.append('部材断面積(曲）' + _num2str(Am / 100, '%15.0f') + '[cm2]　部材断面積（軸）' + _num2str(An / 100, '%15.0f') + '[cm2]')
    text.append('断面係数Zt：' + _num2str(Zt / 1000, '%15.0f') + '[cm3]　断面係数Zb：' + _num2str(Zb / 1000, '%15.0f') + '[cm3]')

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
    text.append('帯筋：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋：SD' + _num2str(SD_main[0, 0]) + '　帯筋：SD' + _num2str(SD_support))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    # load 'PC_info.mat' → モジュール定数 cable_name / cable_data
    text.append('使用PC鋼材：' + _num2str(pick_cable_select[1], '%15.0f') + '-' + cable_name[int(pick_cable_select[0]) - 1])
    text.append('　　')
    text.append('PCケーブル位置（梁せい中心からの位置,プラスは上側，マイナスは下側配置を示す．）')
    text.append('(i端)：' + _num2str(pick_cable_delta[0] * 1000, '%15.1f') + 'mm　' + '(中央)：' + _num2str(pick_cable_delta[1] * 1000, '%15.1f') + 'mm　'
                + '(j端)：' + _num2str(pick_cable_delta[2] * 1000, '%15.1f') + 'mm　')
    text.append('　　')

    if timecase >= 2:
        t_case = LOAD_CASE_NAME
    elif timecase == 1:
        t_case = LOAD_CASE_NAME
    else:
        ERROR = '長短期設定ミス'  # NOTE: 原典184行 stopなし(表示のみで続行)
        print('ERROR =', ERROR)
        t_case = LOAD_CASE_NAME

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    # dd2は幅方向の鉄筋間隔，ddはせい方向の間隔

    outf_main = np.array([[outf_steelbar_JIS(di_main[i_, j_]) for j_ in range(3)]
                          for i_ in range(2)], dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        dd2 = (b - 2 * d_out) / (np.vstack([np.max(steelbar_u[:, 1:3], axis=1),
                                            np.max(steelbar_d[:, 1:3], axis=1)]) - 1)

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
        ERROR = '主筋規定外(D13以上，配筋段数については既に検討済)'
        print('ERROR =', ERROR)
        # stop

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b * D)
    pt_ID1 = np.argmin(pt, axis=0)  # [pt1 pt_ID1] = min(pt)
    pt1 = np.min(pt, axis=0)
    pt_ID2 = int(np.argmin(pt1))    # [pt2 pt_ID2] = min(pt1)
    min_pt = pt[pt_ID1[pt_ID2], pt_ID2]

    text.append('　　')
    text.append('引張鉄筋必要最低断面積（全断面の0.4％以上）')
    text.append('最小引張鉄筋仕様：' + _num2str(num[pt_ID1[pt_ID2], pt_ID2]) + '-D' + _num2str(di_main[pt_ID1[pt_ID2], pt_ID2]))
    text.append('鉄筋総断面積' + _num2str(num[pt_ID1[pt_ID2], pt_ID2] * a[pt_ID1[pt_ID2], pt_ID2] / 100, '%15.2f') + '[cm2]')

    if min_pt >= 0.4 / 100:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[%]　"OK"')
        up = 1.0
    else:
        text.append('引張鉄筋比' + _num2str(min_pt * 100, '%15.2f') + '[%]　"0.4%以下→長期応力割増"')
        up = 4.0 / 3  # NOTE: 原典247行 up=4/3だが以降未使用 (sub版は両分岐up=1.0)
        # ERROR='引張鉄筋断面積不足（0.4％未満）'
        # stop

    # その３：HOOP間隔の規定
    # NOTE: 原典255-257行 min(D/2,250) (sub版183-184行はmax) → 各原典どおり
    text.append('　　')
    text.append('HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= min(D / 2, 250):
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(せいの半分かつ250mm以下)"')
    elif di_support > 10 and pitch <= min(D / 2, 450):
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(せいの半分かつ450mm以下)"')
    else:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"帯筋間隔NG"　梁せい：' + _num2str(D, '%15.0f') + 'mm')
        # stop

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (b * pitch) >= 0.2 / 100:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (b * pitch) * 100, '%15.2f') + '%＞0.2%：OK')
    else:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (b * pitch) * 100, '%15.2f') + '%＜0.2%：NG')
        print('せん断補強筋比不足（0.2％未満）　pw=' + _num2str(aw / (b * pitch) * 100, '%15.2f')
              + '%→STRPピッチ' + _num2str(pitch, '%15.0f') + 'mm，　STRP径：D'
              + _num2str(di_support, '%15.0f') + '，　梁幅b：' + _num2str(b, '%15.0f') + 'mm')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('かぶり厚：' + _num2str(cover_depth) + 'mm≧40mm：OK')
    else:
        ERROR = '鉄筋かぶり厚再検討（40mm未満）'
        print('ERROR =', ERROR)
        text.append('かぶり厚：' + _num2str(cover_depth) + 'mm＜40mm：NG')

    text.append('　　')
    text.append('*****部材応力*****')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　i　端')
    # NOTE: 原典290行 Fxx=stress(:,1)*10^3[N]を更に/10^6して[kN]表記
    #       (実際はkNの1/1000の値が表示される原典の表記ずれ) → 逐語再現
    text.append('軸力Fxx　：' + _num2str(Fxx[0] / 10 ** 6, '%15.1f') + '　[kN]　曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　中央')
    text.append('軸力Fxx　：' + _num2str(Fxx[1] / 10 ** 6, '%15.1f') + '　[kN]　曲げMy　：' + _num2str(My[1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*部材応力　[' + t_case + ']　j　端')
    text.append('軸力Fxx　：' + _num2str(Fxx[2] / 10 ** 6, '%15.1f') + '　[kN]　曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]')
    text.append('　　')

    if timecase == 1:
        designQ = np.abs(stress[:, 2])

        # ひび割れ幅から許容モーメントの算出(正曲げ)%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        ip = 2.0 / 10000  # 柱梁は2.0*10-4
        max_crack = PC_type
        ave_crack = max_crack / 1.5
        with np.errstate(divide='ignore', invalid='ignore'):
            s1 = (b - 2 * cover_depth) / (steelbar_d[:, 1] - 1)
        at1 = a[1, :] * steelbar_d[:, 1]
        at_sum1 = a[1, :] * np.sum(steelbar_d[:, 1:3], axis=1)
        pe1 = at1 / (b * (2 * cover_depth + di_main[1, :]))
        lav1 = 2 * (cover_depth + s1 / 10) + 0.1 * di_main[1, :] / pe1
        ip_t_ave1 = ave_crack / lav1 - ip
        k1k21 = 1.25 / (2.5 * 10 ** 3 * ip_t_ave1 + 1)
        sig_t1 = ip_t_ave1 * 205000 + k1k21 * Ft / pe1
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

        # ひび割れ幅から許容モーメントの算出(負曲げ)%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        ip = 2.0 / 10000  # 柱梁は2.0*10-4
        max_crack = PC_type
        ave_crack = max_crack / 1.5
        with np.errstate(divide='ignore', invalid='ignore'):
            s = (b - 2 * cover_depth) / (steelbar_u[:, 1] - 1)
        at2 = a[0, :] * steelbar_u[:, 1]
        at_sum2 = a[0, :] * np.sum(steelbar_u[:, 1:3], axis=1)
        pe2 = at2 / (b * (2 * cover_depth + di_main[0, :]))
        lav2 = 2 * (cover_depth + s / 10) + 0.1 * di_main[0, :] / pe2
        ip_t_ave2 = ave_crack / lav2 - ip
        k1k21 = 1.25 / (2.5 * 10 ** 3 * ip_t_ave2 + 1)
        sig_t2 = ip_t_ave2 * 205000 + k1k21 * Ft / pe2
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
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
        rfc_t[0] = sig_t1[0]
        rfc_t[1] = sig_t1[1]
        rfc_t[2] = sig_t1[2]
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
        rfc_t[0] = sig_t2[0]
        rfc_t[1] = sig_t2[1]
        rfc_t[2] = sig_t2[2]
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

        # 長期許容ひび割れ曲げモーメントの算出%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # 軸力の寄与成分
        sig_g = stress[:, 0] * 10 ** 3 / Am  # N/mm2
        add_M1 = Zb * sig_g / 10 ** 6
        add_M2 = Zt * sig_g / 10 ** 6 * -1

        M = np.minimum(M1, M3)
        M = np.minimum(M, M2)
        ALWM1 = np.zeros((2, 3))
        ALWM1[0, :] = M[0, :] / 10 ** 6 + add_M1
        ALWM1[1, :] = M[1, :] / 10 ** 6 * -1 + add_M2
        ALWM1 = ALWM1.T

        for j in range(3):
            if stress[j, 3] > 0:
                ratio_output[j, 0] = stress[j, 3] / ALWM1[j, 0]
            else:
                ratio_output[j, 0] = stress[j, 3] / ALWM1[j, 1]

        text.append('*****許容ひび割れ幅から決まる長期許容ひび割れ曲げモーメントの算定*****')
        text.append('　　')
        text.append('許容ひび割れ幅（最大）：' + _num2str(max_crack, '%15.3f') + 'mm')
        text.append('　　')
        text.append('許容ひび割れ幅発生時の曲げM・鉄筋の引張応力度(N/mm2)：正曲げ時')

        text.append('i端：' + _num2str(ALWM1[0, 0], '%15.1f') + 'kNm　中央：' + _num2str(ALWM1[1, 0], '%15.1f') + 'kNm　j端：' + _num2str(ALWM1[2, 0], '%15.1f') + 'kNm')
        text.append('i端：' + _num2str(sig_t1[0], '%15.1f') + 'N/mm2　中央：' + _num2str(sig_t1[1], '%15.1f') + 'N/mm2　j端：' + _num2str(sig_t1[2], '%15.1f') + 'N/mm2')
        text.append('　　')

        text.append('許容ひび割れ幅発生時の曲げM・鉄筋の引張応力度(N/mm2)：負曲げ時')
        text.append('i端：' + _num2str(ALWM1[0, 1], '%15.1f') + 'kNm　中央：' + _num2str(ALWM1[1, 1], '%15.1f') + 'kNm　j端：' + _num2str(ALWM1[2, 1], '%15.1f') + 'kNm')
        text.append('i端：' + _num2str(sig_t2[0], '%15.1f') + 'N/mm2　中央：' + _num2str(sig_t2[1], '%15.1f') + 'N/mm2　j端：' + _num2str(sig_t2[2], '%15.1f') + 'N/mm2')
        text.append('　　')
        # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        # %%導入時の検討
        # NOTE: 原典441行 +pc_stress*(1/PC_eff) (sub版334行は+pc_stress で不一致)
        DLX_stress = (stress - pc_stress * PC_eff) * DL_ratio + pc_stress * (1 / PC_eff)
        ratio_one = np.zeros((4, 3))
        sig0 = np.zeros((3, 2))
        # 上側
        sig0[:, 0] = DLX_stress[:, 3] * 10 ** 6 / Zt + DLX_stress[:, 0] / An
        for j in range(3):
            if sig0[j, 0] >= 0:  # 圧縮
                ratio_one[0, j] = sig0[j, 0] / f_c[timecase - 1, 0]
            else:
                pass
        # 下側
        sig0[:, 1] = -1 * DLX_stress[:, 3] * 10 ** 6 / Zb + DLX_stress[:, 0] / An
        for j in range(3):
            if sig0[j, 1] >= 0:  # 圧縮
                ratio_one[1, j] = sig0[j, 1] / f_c[timecase - 1, 0]
            else:
                pass

        # %%設計時の検討
        TLX_stress = stress
        sig1 = np.zeros((3, 2))
        # 上側
        sig1[:, 0] = TLX_stress[:, 3] * 10 ** 6 / Zt + TLX_stress[:, 0] / An
        for j in range(3):
            if sig1[j, 0] >= 0:  # 圧縮
                ratio_one[2, j] = sig1[j, 0] / f_c[timecase - 1, 0]
            else:
                pass
        # 下側
        sig1[:, 1] = -1 * TLX_stress[:, 3] * 10 ** 6 / Zb + TLX_stress[:, 0] / An
        for j in range(3):
            if sig1[j, 1] >= 0:  # 圧縮
                ratio_one[3, j] = sig1[j, 1] / f_c[timecase - 1, 0]
            else:
                pass
        # NOTE: 原典478行 行ごとのmax (sub版371行のスカラ縮約とは異なる) → 各原典どおり
        ratio_output[:, 0] = np.max(np.column_stack([ratio_output[:, 0], np.max(ratio_one, axis=0)]), axis=1)

        text.append('*****導入時および長期設計時の部材応力度の確認*****')
        text.append('導入時に生じる最大圧縮応力度：' + _num2str(np.max(sig0), '%15.1f') + 'N/mm2')
        # text.append('導入時に生じる最大引張応力度：' ...)
        text.append('長期設計時に生じる最大圧縮応力度：' + _num2str(np.max(sig1), '%15.1f') + 'N/mm2')
        # text.append('長期設計時に生じる最大引張応力度：' ...)
        text.append('　　')
        My = TLX_stress[:, 3]
        designQ = TLX_stress[:, 2]
        text.append('*****長期荷重時の検討*****')

        text.append('　　')
        text.append('*設計用応力　[導入/長期]　i　端')
        text.append('曲げMy　：' + _num2str(DLX_stress[0, 3], '%15.1f') + '/' + _num2str(TLX_stress[0, 3], '%15.1f')
                    + '　[kNm]　せん断力Qz　：' + _num2str(DLX_stress[0, 2], '%15.1f') + '/' + _num2str(TLX_stress[0, 2], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('*設計用応力　[導入/長期]　中央')
        text.append('曲げMy　：' + _num2str(DLX_stress[1, 3], '%15.1f') + '/' + _num2str(TLX_stress[1, 3], '%15.1f')
                    + '　[kNm]　せん断力Qz　：' + _num2str(DLX_stress[1, 2], '%15.1f') + '/' + _num2str(TLX_stress[1, 2], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('*設計用応力　[導入/長期]　j　端')
        text.append('曲げMy　：' + _num2str(DLX_stress[2, 3], '%15.1f') + '/' + _num2str(TLX_stress[2, 3], '%15.1f')
                    + '　[kNm]　せん断力Qz　：' + _num2str(DLX_stress[2, 2], '%15.1f') + '/' + _num2str(TLX_stress[2, 2], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('　　')

        text.append('せん断耐力算定α：' + _num2str(alph[0], '%15.1f'))
        text.append('梁の有効せい ｊ(mm)：' + _num2str(j_dis[0], '%15.1f'))
        text.append('Mmax(kNm)：' + _num2str(Mmax, '%15.1f'))
        text.append('Qmax(kN)：' + _num2str(Qmax, '%15.1f'))
        text.append('　　')

        text.append('*****許容耐力・検定比(曲げはひび割れ許容Mおよび縁応力度に対する検定のうち最大のものを示す)*****')
        text.append('*許容耐力　[導入/長期のうち最大のものを示す]　i　端')
        text.append('正曲げMy　：' + _num2str(ALWM1[0, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALWM1[0, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[0, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[0, 1], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[導入/長期のうち最大のものを示す]　中央')
        text.append('正曲げMy　：' + _num2str(ALWM1[1, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALWM1[1, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[1, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[1, 1], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[導入/長期のうち最大のものを示す]　j　端')
        text.append('正曲げMy　：' + _num2str(ALWM1[2, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALWM1[2, 1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[2, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[2, 1], '%15.2f'))

        # load 'PC_info.mat' → モジュール定数 cable_data
        Ty = cable_data[int(pick_cable_select[0]) - 1, 4] * pick_cable_select[1]  # kN

        MU = np.zeros((2, 3))
        # 終局曲げの検討(正曲げ)
        dp = D / 2 - pick_cable_delta * 1000
        d = D - d_out[1, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[0, j] = 0.9 * at_sum1[j] * rfc[1][j][1, 0] * d[j]
            else:
                MU[0, j] = 7.0 / 8 * at_sum1[j] * rfc[1][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)

        # 終局曲げの検討(負曲げ)
        dp = D / 2 + pick_cable_delta * 1000
        d = D - d_out[0, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[1, j] = 0.9 * at_sum2[j] * rfc[0][j][1, 0] * d[j] * -1
            else:
                MU[1, j] = (7.0 / 8 * at_sum2[j] * rfc[0][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)) * -1

        # 1.7(G+P)+0.85X
        # NOTE: 原典556行 +pc_stress (sub版402行は+PC_eff*pc_stress で不一致)
        TLX_stress1 = (stress - PC_eff * pc_stress) * 1.7 + pc_stress

        # 1.2G+2.0P+0.85X
        # NOTE: 原典560行 +pc_stress (sub版406行は+PC_eff*pc_stress で不一致)
        TLX_stress2 = (stress - PC_eff * pc_stress) * DL_ratio * 1.2 + (stress - PC_eff * pc_stress) * (1 - DL_ratio) * 2.0 + pc_stress

        for j in range(3):
            if TLX_stress1[j, 3] >= 0:  # 正曲げ
                ratio_output[j, 2] = TLX_stress1[j, 3] * 10 ** 6 / MU[0, j]
            else:
                ratio_output[j, 2] = TLX_stress1[j, 3] * 10 ** 6 / MU[1, j]

            if TLX_stress2[j, 3] >= 0 and abs(TLX_stress2[j, 3] / MU[0, j]) > ratio_output[j, 2]:  # 正曲げ
                ratio_output[j, 2] = TLX_stress2[j, 3] * 10 ** 6 / MU[0, j]
            elif TLX_stress2[j, 3] < 0 and abs(TLX_stress2[j, 3] / MU[1, j]) > ratio_output[j, 2]:
                ratio_output[j, 2] = TLX_stress2[j, 3] * 10 ** 6 / MU[1, j]
        ALW_M = MU.T

        My = np.max(np.column_stack([TLX_stress1[:, 3], TLX_stress2[:, 3]]), axis=1)
        designQ = np.max(np.column_stack([TLX_stress1[:, 2], TLX_stress2[:, 2]]), axis=1)
        text.append('　　')
        text.append('　　')
        text.append('*****鉛直荷重終局時・設計用応力(1.7(G+P)および1.2G+2.0Pの大きいほうを示す)*****')
        text.append('　　')
        text.append('*設計用応力　[鉛直終局]　i　端')
        text.append('曲げMy　：' + _num2str(My[0], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('*設計用応力　[鉛直終局]　中央')
        text.append('曲げMy　：' + _num2str(My[1], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[1], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('*設計用応力　[鉛直終局]　j　端')
        text.append('曲げMy　：' + _num2str(My[2], '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[2], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('　　')

        text.append('*****許容耐力・検定比*****')
        text.append('　　')
        text.append('*許容耐力　[鉛直終局]　i　端')
        text.append('正曲げMy　：' + _num2str(ALW_M[0, 0] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[0, 1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[0, 1], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[0, 2], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[0, 3], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[鉛直終局]　中央')
        text.append('正曲げMy　：' + _num2str(ALW_M[1, 0] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[1, 1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[1, 1], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[1, 2], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[1, 3], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[鉛直終局]　j　端')
        text.append('正曲げMy　：' + _num2str(ALW_M[2, 0] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[2, 1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[2, 1], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[2, 2], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[2, 3], '%15.2f'))

    else:

        at_sum1 = a[1, :] * np.sum(steelbar_d[:, 1:3], axis=1)
        at_sum2 = a[0, :] * np.sum(steelbar_u[:, 1:3], axis=1)
        # load 'PC_info.mat' → モジュール定数 cable_data
        Ty = cable_data[int(pick_cable_select[0]) - 1, 4] * pick_cable_select[1]  # kN
        # 終局曲げの検討
        MU = np.zeros((2, 3))
        # 終局曲げの検討(正曲げ)
        dp = D / 2 - pick_cable_delta * 1000
        d = D - d_out[1, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[0, j] = 0.9 * at_sum1[j] * rfc[1][j][1, 0] * d[j]
            else:
                MU[0, j] = 7.0 / 8 * at_sum1[j] * rfc[1][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)

        # 終局曲げの検討(負曲げ)
        dp = D / 2 + pick_cable_delta * 1000
        d = D - d_out[0, :]
        for j in range(3):
            if dp[j] < D / 2:
                MU[1, j] = 0.9 * at_sum2[j] * rfc[0][j][1, 0] * d[j] * -1
            else:
                MU[1, j] = (7.0 / 8 * at_sum2[j] * rfc[0][j][1, 0] * d[j] + Ty * 10 ** 3 * (dp[j] - d[j] / 8)) * -1

        # G+P+0.85X+1.5K
        KX_stress = (stress - QL) * 1.5 + QL
        for j in range(3):
            if KX_stress[j, 3] >= 0:  # 正曲げ
                ratio_output[j, 0] = KX_stress[j, 3] * 10 ** 6 / MU[0, j]
            else:
                ratio_output[j, 0] = KX_stress[j, 3] * 10 ** 6 / MU[1, j]

        My = KX_stress[:, 3]
        designQ = 2.0 * np.abs(stress[:, 2] - QL[:, 2]) + np.abs(QL[:, 2])

        text.append('*****設計用応力(TL+X+1.5Kでの終局耐力の検討)*****')
        text.append('　　')
        text.append('*設計用応力(終局)　[' + t_case + ']　i　端')
        text.append('曲げMy　：' + _num2str(My[0], '%15.1f') + '　[kNm]　せん断割増し(n=2.0)から決まる設計用せん断力：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
        text.append('　　')

        text.append('*設計用応力(終局)　[' + t_case + ']　中央')
        text.append('曲げMy　：' + _num2str(My[1], '%15.1f') + '　[kNm]　せん断割増し(n=2.0)から決まる設計用せん断力：' + _num2str(designQ[1], '%15.1f') + '　[kN]')

        text.append('　　')
        text.append('*設計用応力(終局)　[' + t_case + ']　j　端')
        text.append('曲げMy　：' + _num2str(My[2], '%15.1f') + '　[kNm]　せん断割増し(n=2.0)から決まる設計用せん断力：' + _num2str(designQ[2], '%15.1f') + '　[kN]')
        text.append('　　')
        text.append('　　')

        text.append('せん断耐力算定α：' + _num2str(alph[0], '%15.1f'))
        text.append('梁の有効せい` ｊ(mm)：' + _num2str(j_dis[0], '%15.1f'))
        text.append('Mmax(kNm)：' + _num2str(Mmax, '%15.1f'))
        text.append('Qmax(kN)：' + _num2str(Qmax, '%15.1f'))
        text.append('　　')
        text.append('　　')

        text.append('*****終局耐力・検定比*****')
        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　i　端')
        text.append('正曲げMy　：' + _num2str(MU[0, 0] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(MU[1, 0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[0, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[0, 1], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　中央')
        text.append('正曲げMy　：' + _num2str(MU[0, 1] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(MU[1, 1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[1, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[1, 1], '%15.2f'))

        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　j　端')
        text.append('正曲げMy　：' + _num2str(MU[0, 2] / 10 ** 6, '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(MU[1, 2] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(ALW_Q[2, 0], '%15.1f') + '　[kN]')
        text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz]：' + _num2str(ratio_output[2, 1], '%15.2f'))

    return text


# ===========================================================================
# PC_ratio_analysis.m (主関数)
# ===========================================================================

def PC_ratio_analysis(sectionsize, ele_length, stress, timecase, Fc, ele_no,
                      maxratios, maxratios_text, section_no, RCcolumns,
                      RCbeams, QL, qup_beam, column_cover, beam_cover,
                      RCbeam_secNO, node, element, load_direction, load_no,
                      ij_select, ij_reverse, LOAD_CASE_NAME, L_43, PC_slab,
                      PC_eff, pc_stress, PC_type, DL_ratio, cable_delta,
                      cable_select, pick_section_name):
    """PC_ratio_analysis.m の逐語移植 (鉄筋コンクリート部材の断面検定[PC梁])

    元ファイル: privatetool_function/MIDAS/ratio/RC/PC_ratio_analysis.m

    移植済み経路: Ⅲ種PC梁 (中実角断面2000, PC_type=許容ひび割れ幅mm)
    未移植経路 (原典stop → ValueError):
      フルプレストレス(PC_type=1)・パーシャルプレストレス(PC_type=2)・PC柱

    返り値: (ratio_output(3x4), maxratios(長さ2), maxratios_text)
      長期(timecase=1): 列1=曲げ(導入+長期), 列2=長期せん断,
                         列3=鉛直終局曲げ, 列4=鉛直終局せん断
      短期(timecase>=10): 列1=水平終局曲げ, 列2=短期せん断, 列3=列4=0
    ※qup_beam引数は原典どおり未使用 (せん断割増は2.0固定)。
    """
    stress = np.array(np.atleast_2d(stress), dtype=float, copy=True)
    QL = np.array(np.atleast_2d(QL), dtype=float, copy=True)
    sectionsize = np.asarray(sectionsize, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    maxratios = np.array(np.asarray(maxratios, dtype=float).ravel(), copy=True)

    stress[:, 0] = stress[:, 0] * -1
    QL[:, 0] = QL[:, 0] * -1
    sectionsize = sectionsize * 1000
    ratio_output = np.zeros((3, 4))

    # RCcolumns/RCbeams/RCWcolumnに断面番号があるかで柱断面か梁断面かを判定する
    # (MATLAB find_indexは1-based/見つからない=0。+1して同じ値にする)
    if RCcolumns is None or len(RCcolumns) == 0:
        column_judge = 0
    else:
        column_judge = find_index(np.atleast_2d(np.asarray(RCcolumns, dtype=float))[:, 0], section_no) + 1
    if RCbeam_secNO is None or np.asarray(RCbeam_secNO).size == 0:
        beam_judge = 0
    else:
        beam_judge = find_index(np.asarray(RCbeam_secNO, dtype=float).ravel(), section_no) + 1

    # PC梁として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    if column_judge == 0 and beam_judge != 0:
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
            # NOTE: PC経路のRCbeams{k,2}参照列はRC経路(17列書式)と異なり
            #       列1-5=上端筋/列6-10=下端筋/列11-13=STRP (原典どおり)
            rcb2 = np.array(RCbeams[beam_judge - 1][1], dtype=float, copy=True)
            # HOOP:あばら筋径D，ピッチ，本数，SD295，かぶり40mm
            if rcb2[0, 10] > 18:
                SD = 345
            else:
                SD = 295
            HOOP = np.concatenate([rcb2[0, 10:13], [SD, beam_cover]])

            # NOTE: 原典47行 上端筋SDの判定列は(1,5)=二段目径 (一段目径は(1,4))
            if rcb2[0, 4] > 18:
                SD = 345
            else:
                SD = 295
            SD3 = np.array([[SD], [SD], [SD]], dtype=float)

            def _rows(rows, cols):
                """rcb2の行インデックス(1-based)を並べて3行を作る (cols=1-based両端)."""
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
                            steelbar_u = np.hstack([_rows([1, 1, 1], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([3, 3, 3], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    else:
                        raise ValueError('ij_select設定エラー')  # NOTE: MATLABは未定義変数エラー
                else:
                    if ij_select == 1:
                        steelbar_u = np.hstack([_rows([3, 3, 3], (1, 5)), SD3])
                    elif ij_select == 2:
                        steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    elif ij_select == 3:
                        steelbar_u = np.hstack([_rows([1, 1, 1], (1, 5)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([3, 3, 3], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 1:3]) >= np.sum(rcb2[1, 1:3]):
                            steelbar_u = np.hstack([_rows([1, 1, 1], (1, 5)), SD3])
                        else:
                            steelbar_u = np.hstack([_rows([2, 2, 2], (1, 5)), SD3])
                    else:
                        raise ValueError('ij_select設定エラー')  # NOTE: MATLABは未定義変数エラー

            if rcb2[0, 9] > 18:
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
                            steelbar_d = np.hstack([_rows([1, 1, 1], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[2, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([3, 3, 3], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    else:
                        raise ValueError('ij_select設定エラー')  # NOTE: MATLABは未定義変数エラー
                else:
                    if ij_select == 1:
                        steelbar_d = np.hstack([_rows([3, 3, 3], (6, 10)), SD3])
                    elif ij_select == 2:
                        steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    elif ij_select == 3:
                        steelbar_d = np.hstack([_rows([1, 1, 1], (6, 10)), SD3])
                    elif ij_select == 1.5:
                        if np.sum(rcb2[2, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([3, 3, 3], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    elif ij_select == 2.5:
                        if np.sum(rcb2[0, 6:8]) >= np.sum(rcb2[1, 6:8]):
                            steelbar_d = np.hstack([_rows([1, 1, 1], (6, 10)), SD3])
                        else:
                            steelbar_d = np.hstack([_rows([2, 2, 2], (6, 10)), SD3])
                    else:
                        raise ValueError('ij_select設定エラー')  # NOTE: MATLABは未定義変数エラー

            # %%%%%%%以上で配筋情報取得完了%%%%%%%%%%%%%%%%%%%%%%%
            # スラブ有効幅の取得
            PC_slab = np.atleast_2d(np.asarray(PC_slab, dtype=float))
            slab_idx = find_index(PC_slab[:, 0], section_no)
            if slab_idx == -1:  # NOTE: MATLABはindex 0参照で実行時エラー
                raise ValueError('PC_slabに断面番号%dの行がありません' % int(section_no))
            slab_t = PC_slab[slab_idx, 1:4]

            # 不静定力の取得
            if pc_stress is None or np.asarray(pc_stress).size == 0:
                pc_stress = np.array([[0, 0, 0, 0, 0],
                                      [0, 0, 0, 0, 0],
                                      [0, 0, 0, 0, 0]], dtype=float)
            else:
                pc_stress = np.atleast_2d(np.asarray(pc_stress, dtype=float))
                pcs_idx = find_index(pc_stress[:, 0], ele_no)
                if pcs_idx == -1:  # NOTE: MATLABはindex 0参照で実行時エラー
                    raise ValueError('pc_stressに要素番号%dの行がありません' % int(ele_no))
                pc_stress = pc_stress[pcs_idx:pcs_idx + 3][:, [2, 3, 4, 6, 7]]

            if PC_type == 1:  # フルプレストレス（未作成）
                raise ValueError('PC検定未作成(フルプレストレス)')  # MATLAB: stop
            elif PC_type == 2:  # パーシャルプレストレス（未作成）
                raise ValueError('PC検定未作成(パーシャルプレストレス)')  # MATLAB: stop
            elif PC_type == 0:  # エラー
                raise ValueError('PC_type設定エラー')  # MATLAB: stop
            else:  # Ⅲ種PC
                cable_select = np.atleast_2d(np.asarray(cable_select, dtype=float))
                cs_idx = find_index(cable_select[:, 0], section_no)
                if cs_idx == -1:  # NOTE: MATLABはindex 0参照で実行時エラー
                    raise ValueError('cable_selectに断面番号%dの行がありません' % int(section_no))
                pick_cable_select = cable_select[cs_idx, 1:3]
                cable_delta = np.atleast_2d(np.asarray(cable_delta, dtype=float))
                cd_idx = find_index(cable_delta[:, 0], ele_no)
                if cd_idx == -1:  # NOTE: MATLABはindex 0参照で実行時エラー
                    raise ValueError('cable_deltaに要素番号%dの行がありません' % int(ele_no))
                pick_cable_delta = cable_delta[cd_idx, 1:4]
                # 強軸周りの許容曲げモーメントに対して検定を行う(許容応力度および終局の検討)
                r2 = sub_PC3_4beam_ALWM(Form, steelbar_u, steelbar_d, HOOP, Fc,
                                        timecase, L43, slab_t, pc_stress,
                                        PC_eff, PC_type, stress, QL, DL_ratio,
                                        pick_cable_delta, pick_cable_select)
                ratio_output[:, 0] = r2[:, 0]
                ratio_output[:, 2] = r2[:, 1]

                # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
                rq, ALW_Q, _alph, _jd, _Mmax, _Qmax = SA_PCbeamQratio(
                    Form, ele_length, steelbar_u, steelbar_d, HOOP, Fc, stress,
                    timecase, QL, 2.0, pc_stress, DL_ratio, PC_eff)
                ratio_output[:, 1] = rq[:, 0]
                ratio_output[:, 3] = rq[:, 1]

                # 最大検定値の指定
                if np.max(ratio_output) > np.max(maxratios[1]):
                    maxratios[0] = ele_no
                    maxratios[1] = np.max(ratio_output)
                    maxratios_text = []
                    maxratios_text = SA_PC3beamratio_text(
                        Form, ele_length, steelbar_u, steelbar_d, HOOP, Fc,
                        stress, timecase, slab_t, pc_stress, PC_eff, PC_type,
                        QL, DL_ratio, pick_cable_delta, pick_cable_select,
                        ele_no, section_no, 2.0, LOAD_CASE_NAME,
                        pick_section_name)

        else:
            print('ERROR:RC梁の断面形状設定ミス＜長方形でない梁＞')
            raise ValueError('ERROR:RC梁の断面形状設定ミス＜長方形でない梁＞')  # MATLAB: stop

    # 柱として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    elif column_judge != 0 and beam_judge == 0:
        print('PC柱の検討は未作成')
        raise ValueError('PC柱の検討は未作成')  # MATLAB: stop
    elif column_judge != 0 and beam_judge != 0:
        print('section_no =', section_no)
        print('柱断面・梁断面の識別失敗')
        raise ValueError('柱断面・梁断面の識別失敗')  # MATLAB: stop
    else:  # ここでRCの断面情報を決めないといけない
        print('RC断面（配筋情報）が定義されていません')

    return ratio_output, maxratios, maxratios_text
