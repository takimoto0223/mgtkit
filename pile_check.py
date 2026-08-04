# -*- coding: utf-8 -*-
"""RC杭部材の断面検定モジュール (MATLAB→Python逐語移植).

原典:
  privatetool_function/MIDAS/ratio/RC/RCpile_ratio_analysis.m (121行)
  structural_function/RC/RC_section_analysis/column/circle/SA_RCSRpile_Qratio.m (23行)
  structural_function/RC/RC_section_analysis/column/circle/SA_RCSR_pileratio_text.m (204行)

円形断面のNM算定は rc_check.SA_RCSRcolumn_AIJ (find_e内蔵) を再利用する。
"""
import math

import numpy as np

from .util import find_index
from .rc_check import (Area_steelbar, outf_steelbar_JIS, ALST_RC_AIJ,
                       ALST_steelbar_KJ, E_RC_AIJ, SA_RCSRcolumn_AIJ)
from .s_check import _num2str


# ===========================================================================
# RC杭の許容せん断力算定 (SA_RCSRpile_Qratio.m)
# ===========================================================================

def SA_RCSRpile_Qratio(Form, Fc, stress, timecase):
    """SA_RCSRpile_Qratio.m の逐語移植 (RC杭の許容せん断力算定).

    原典: structural_function/RC/RC_section_analysis/column/circle/
          SA_RCSRpile_Qratio.m

    入力: Form=[D(mm)] Fc=[Fc値 種別] stress=3x5[N,Fy,Fz,My,Mz]
          timecase=1長期/10以上短期
    戻り値: (ratio_Q(長さ3 ndarray), ALW_Q(スカラ[kN]))
            コンクリートのみの許容せん断力(0.75倍)に対し設計せん断力
            sqrt(Fy^2+Fz^2)を1.5倍割増して検定。
    """
    Form = np.asarray(Form, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    S = np.atleast_2d(np.asarray(stress, dtype=float))

    # RC断面の外形情報[mm]
    D = Form[0]

    # 長短期設定
    if timecase >= 10:
        timecase = 2
        # t_case ='短期';
    elif timecase == 1:
        timecase = 1
        # t_case ='長期';
    else:
        # 原典15-16行: ERROR='長短期設定ミス' + stop
        raise ValueError('長短期設定ミス')

    # 許容応力度
    f_c = ALST_RC_AIJ(Fc)

    # 許容せん断力の算定（長期）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # NOTE: 原典23行の見出しは「長期」だが実際はtimecase行(長短期)のfsを使用
    ALW_Q = D ** 2 / 4 * math.pi * (f_c[timecase - 1, 2]) * 0.75 / 10 ** 3
    ratio_Q = np.abs(np.sqrt(S[:, 2] ** 2 + S[:, 1] ** 2)) / ALW_Q * 1.5
    return ratio_Q, ALW_Q


# ===========================================================================
# RC杭の断面算定詳細テキスト (SA_RCSR_pileratio_text.m)
# ===========================================================================

def SA_RCSR_pileratio_text(Form, ele_length, steelbar, HOOP, Fc, stress,
                           timecase, QL, ele_no, section_no, qup_column,
                           LOAD_CASE_NAME, RCQ, section_name):
    """SA_RCSR_pileratio_text.m の逐語移植 (RC杭の断面算定詳細のテキスト出力).

    原典: structural_function/RC/RC_section_analysis/column/circle/
          SA_RCSR_pileratio_text.m

    入力: Form=[D(mm)] steelbar=[本数,径,SD] HOOP=[径,pitch,c9,c10,SD,かぶり]
          Fc=[0.75倍済みFc値 種別] stress=3x5 timecase=1/11
    戻り値: text (list of str, strvcat相当)
    NOTE: ele_length, QL, qup_column, RCQ は原典でも受け取るのみで未使用。
    """
    Form = np.asarray(Form, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    S = np.atleast_2d(np.asarray(stress, dtype=float))

    # 軸力(N)＋曲げ(MM)，せん断(Q)に対する断面算定
    # 配筋情報
    # NOTE: load bar_table.mat はヘルパー関数(Area_steelbar等)が内部保持するため不要
    num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[2]; cover_depth = HOOP[3]
    a = Area_steelbar(di_main, 1)

    # 許容応力度
    f_c = ALST_RC_AIJ(Fc)
    rfc = ALST_steelbar_KJ([di_main, SD_main])

    # ヤング係数比
    # NOTE: 原典17行はE_RC_AIJ(Fc)。主関数RCpile_ratio_analysis(原典57行)は
    #       E_RC_AIJ(Fc*4/3)でヤング係数比を求めており不整合(原典どおり再現)
    n = E_RC_AIJ(Fc)
    n = n[1]
    if timecase >= 10:
        timecase2 = 2
    elif timecase == 1:
        timecase2 = 1
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    e = np.zeros(3)
    M_AL = np.zeros((3, 1))
    ratio_output = np.zeros((3, 3))
    for ie in range(3):
        if S[ie, 0] == 0:  # 軸力がゼロ→曲げのみで検定
            e[ie] = 10 ** 6
        else:
            # NOTE: 原典31行 偏心はsqrt(My^2+Mz^2)の合成 (符号はNに依存)
            e[ie] = abs(math.sqrt(S[ie, 3] ** 2 + S[ie, 4] ** 2)) / S[ie, 0] * 1000
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
            # NOTE: 原典45行 検定は|My|のみ (偏心算定のsqrt(My^2+Mz^2)と不整合)
            ratio_output[ie, 0] = abs(S[ie, 3]) * 10 ** 6 / abs(M_AL[ie, 0])

    # せん断の検定（短期のせん断の検定のためには長期のせん断力も必要）
    ratio_output[:, 2], ALW_Q = SA_RCSRpile_Qratio(Form, Fc, S, timecase)

    Fx = S[:, 0] * 10 ** 3  # 軸力[N]
    M = np.sqrt((S[:, 3] * 10 ** 6) ** 2 + (S[:, 4] * 10 ** 6) ** 2)  # 曲げモーメント強軸[Nmm]
    Fyz = np.sqrt((S[:, 1] * 10 ** 3) ** 2 + (S[:, 2] * 10 ** 3) ** 2)  # せん断力強軸方向[N]

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%以上で検定値の算出終了

    # RC断面の外形情報
    D = Form[0]
    r = Form[0] / 2

    # 帯筋情報を読み込み
    # NOTE: 原典66行でHOOP(5),(6)によりSD_support/cover_depthを再代入
    #       (原典9行のHOOP(3),(4)による代入を上書き)
    di_support = HOOP[0]; pitch = HOOP[1]; SD_support = HOOP[4]; cover_depth = HOOP[5]

    # 主筋情報：steelbar
    num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5

    a = Area_steelbar(di_main, 1)

    text = ['RC●杭の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と曲げを考慮した断面算定を行う．')
    text.append('せん断についてはコンクリートのみでの許容耐力を1.5で除した値を許容せん断力とし，設計応力の割増を確認している．')
    text.append('　　')

    # NOTE: 原典80行どおり「断面符号：」と断面名の後に区切り空白なし
    text.append('断面符号：' + str(section_name) + '断面番号：' + _num2str(section_no)
                + '　要素番号：' + _num2str(ele_no))
    text.append('　　')
    text.append('RC杭サイズ　：D-' + _num2str(D) + '[mm]')
    text.append('　　')
    text.append('*****配筋情報*****')
    text.append('主筋配筋　：' + _num2str(num) + '-D' + _num2str(di_main)
                + '　帯筋　：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋　：SD' + _num2str(SD_main) + '　帯筋　：SD' + _num2str(SD_support))
    # NOTE: Fcは0.75倍済みのため4/3倍で表示 (原典90行。0.75*4/3=1で元のFcに一致)
    text.append('コンクリート　：Fc' + _num2str(Fc[0] * 4 / 3))
    text.append('　　')

    # NOTE: 原典94行の条件は timecase>=2 (他関数の>=10と異なる) だが
    #       いずれの分岐でも t_case=LOAD_CASE_NAME で同じ
    if timecase >= 2:
        t_case = LOAD_CASE_NAME
    elif timecase == 1:
        t_case = LOAD_CASE_NAME
    else:
        ERROR = '長短期設定ミス'  # NOTE: MATLAB同様ここでは停止しない

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)

    # その０：鉄筋あきのチェック
    rr = r - dc
    if (2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 1.5 * di_main
            and 2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 25 * 1.25):
        pass
    else:
        ERROR = '配筋ミス'  # NOTE: MATLAB同様ここでは停止しない
    check_dd = 2 * math.pi * rr / num - outf_steelbar_JIS(di_main)
    # NOTE: 原典115行は min(1.5*di_main, 25*1.25)。表示文言は「最大値」だが
    #       コードはminを採用しており不整合 (原典どおり再現)
    min_dd = min(1.5 * di_main, 25 * 1.25)
    text.append('　　')
    text.append('＊主筋のあきの検討')
    text.append('最低あき寸法（「径の1.5倍」・「粗骨材寸法(25mm)の1.25倍」・「25mm」の最大値）　：'
                + _num2str(min_dd, '%15.2f') + '[mm]')
    text.append('鉄筋のあき　：' + _num2str(check_dd, '%15.1f') + '[mm]')

    if check_dd >= min_dd:
        text.append('主筋のあき間隔の検討　：OK')
    else:
        print('鉄筋あき不足→主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋のあき間隔の検討　：NG')
        # stop (原典126行コメントアウト済み)

    # その１：主筋の規定
    text.append('　　')
    text.append('＊主筋の規定（径8本以上，径D13以上）')
    text.append('使用鉄筋の径　：' + _num2str(num) + '-D' + _num2str(di_main))
    if num >= 8 and di_main >= 13:
        text.append('主筋径・本数の規定　：OK')
    else:
        print('[主筋規定外]' + '主筋本数（本）：' + _num2str(num, '%15.0f')
              + '　主筋径：' + _num2str(di_main, '%15.0f'))
        text.append('主筋径・本数の規定　：NG')
        # stop (原典138行コメントアウト済み)

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('＊鉄筋かぶりの規定(70mm)')
    if cover_depth >= 70:
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm≧70mm　：OK')
    else:
        # 原典147-149行: ERROR echo + text追記 + stop
        text.append('かぶり厚　：' + _num2str(cover_depth) + 'mm＜70mm　：NG')
        raise ValueError('鉄筋かぶり厚再検討（70mm未満）: かぶり%gmm' % cover_depth)
    text.append('　　')
    text.append('　　')
    text.append('*****設計用応力*****')

    _pos = ['i　端', '中央', 'j　端']
    for ie in range(3):
        text.append('　　')
        text.append('*設計用応力　[' + t_case + ']　' + _pos[ie])
        text.append('軸力　：' + _num2str(Fx[ie] / 1000, '%15.1f') + '　[kN]　　曲げM　：'
                    + _num2str(M[ie] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('せん断力Q　：' + _num2str(Fyz[ie] / 10 ** 3, '%15.1f')
                    + '　[kN](この値を1.5倍に割増したせん断力で検定を行う)')

    text.append('　　')
    text.append('　　')

    # NOTE: 原典173-182行の e_y/e_z 組立てはtextに出力されない (デッドコード)

    text.append('*****許容耐力・検定比*****')
    # NOTE: 原典190行のi端のみせん断検定比ラベルが'[Q]' (中央/j端は'[Qz]')
    _qlbl = ['[Q]', '[Qz]', '[Qz]']
    for ie in range(3):
        text.append('　　')
        text.append('*許容耐力　[' + t_case + ']　' + _pos[ie])
        text.append('許容曲げ　(N+M)　：' + _num2str(M_AL[ie, 0] / 10 ** 6, '%15.1f') + '　[kNm]')
        text.append('せん断力Q　：' + _num2str(ALW_Q, '%15.1f') + '　[kN]')
        text.append('検定比　[NM]　：' + _num2str(ratio_output[ie, 0], '%15.2f'))
        text.append('検定比　' + _qlbl[ie] + '　：' + _num2str(ratio_output[ie, 2], '%15.2f'))
    return text


# ===========================================================================
# RC杭部材の断面検定 (RCpile_ratio_analysis.m)
# ===========================================================================

# 鉄筋コンクリート部材の断面検定
def RCpile_ratio_analysis(sectionsize, ele_length, stress, timecase, Fc, ele_no,
                          maxratios, maxratios_text, section_no, RCcolumns, QL,
                          pile_cover, LOAD_CASE_NAME, RCQ, pick_section_name):
    """RCpile_ratio_analysis.m の逐語移植 (RC杭部材の断面検定).

    原典: privatetool_function/MIDAS/ratio/RC/RCpile_ratio_analysis.m

    入力:
      sectionsize : 断面寸法[m] (関数内で×1000。末尾-1番目=形状マーカー、
                    中実丸3000のみ対応。それ以外は原典どおりエラー停止)
      ele_length  : 部材長 (テキスト関数へ渡すのみで未使用)
      stress      : 3x5 [N,Fy,Fz,My,Mz] (i端/中央/j端。N列は符号反転される)
      timecase    : 1長期 / 10以上短期
      Fc          : [Fc1 Fc2]。杭では Fc=[Fc(2) 0]*0.75 (普通コン扱い・0.75倍)
      ele_no      : 要素番号
      maxratios   : [最大検定値の要素番号, 最大検定値] (更新して返す)
      maxratios_text : 最大検定値の詳細テキスト (更新して返す)
      section_no  : 断面番号
      RCcolumns   : 柱/杭配筋表。列1=断面番号, 列3=主筋本数, 列4=主筋径,
                    列7=帯筋径, 列8=帯筋ピッチ (列7の径からHOOPのSDを自動選定)
      QL          : 3x3 長期せん断力 (列1符号反転→列2:3抽出。以降未使用)
      pile_cover  : 杭かぶり[mm]
      LOAD_CASE_NAME : 荷重ケース名 (テキスト表示用)
      RCQ         : せん断設計方法 (テキスト関数へ渡すのみで未使用)
      pick_section_name : 断面符号名 (テキスト表示用)
    戻り値: (ratio_output(3x4 ndarray), maxratios(長さ2 ndarray), maxratios_text)
      ratio_output 列1=NM検定比, 列3=Q検定比 (列2,4は常にゼロ)
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

    if np.asarray(RCcolumns).size == 0:
        column_judge = 0
    else:
        # MATLAB 1-based/見つからない=0 と値互換にするため +1
        column_judge = find_index(
            np.atleast_2d(np.asarray(RCcolumns, dtype=float))[:, 0], section_no) + 1

    # 杭として検討%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

    if sectionsize[len(sectionsize) - 2] == 3000:  # 中実丸断面の検定
        qup_beam = 1.5
        QL = QL[:, 1:3]
        Fc = np.array([Fc[1], 0.0]) * 0.75  # 普通コンとする
        if column_judge == 0:
            # NOTE: 原典はRCcolumns(0,7)参照で実行時エラーになるためValueError
            raise ValueError('RC杭の配筋情報がRCcolumnsに見つからない (断面番号%g)'
                             % section_no)
        cj = column_judge - 1  # 0-based行
        RCcol = np.array(
            np.atleast_2d(np.asarray(RCcolumns, dtype=float))[cj, :], copy=True)
        if RCcol[6] > 28:
            SD = 390
        elif RCcol[6] > 18:
            SD = 345
        else:
            SD = 295

        HOOP = np.concatenate([RCcol[6:10], [SD, pile_cover]])  # SD295と仮定
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
        # NOTE: load bar_table.mat はヘルパー関数(Area_steelbar等)が内部保持するため不要
        num = steelbar[0]; di_main = steelbar[1]; SD_main = steelbar[2]
        a = Area_steelbar(di_main, 1)

        # 許容応力度
        f_c = ALST_RC_AIJ(Fc)
        rfc = ALST_steelbar_KJ([di_main, SD_main])

        # ヤング係数比
        # NOTE: 原典57行はE_RC_AIJ(Fc*4/3) (0.75倍前のFcで算定。円形柱経路や
        #       SA_RCSR_pileratio_text(原典17行)のE_RC_AIJ(Fc)とは不整合)
        n = E_RC_AIJ(Fc * 4 / 3)
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
                # NOTE: 原典72行 偏心はsqrt(My^2+Mz^2)の合成 (符号はNに依存)
                e[ie] = abs(math.sqrt(stress[ie, 3] ** 2 + stress[ie, 4] ** 2)) \
                    / stress[ie, 0] * 1000
            N, M, Xn = SA_RCSRcolumn_AIJ(e[ie], Form, steelbar,
                                         HOOP[[0, 1, 4, 5]], Fc, timecase)

            if M < 100:
                if stress[ie, 0] > 0:  # 圧縮
                    N = min((Form[0] ** 2 / 4 * math.pi + (n - 1) * num * a) * f_c[timecase2 - 1, 0],
                            ((Form[0] ** 2 / 4 * math.pi - num * a) / n + num * a) * (rfc[timecase2 - 1, 0]))
                    ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
                else:  # 引張
                    N = rfc[timecase2 - 1, 0] * num * a
                    ratio_output[ie, 0] = abs(stress[ie, 0]) * 10 ** 3 / abs(N)
            else:
                # NOTE: 原典85行 検定は|My|のみ。偏心算定はsqrt(My^2+Mz^2)の
                #       合成モーメントを使うのに検定比の分子がMyのみで不整合。
                #       軸力項とのmax比較も無し (円形柱経路と異なる)
                ratio_output[ie, 0] = abs(stress[ie, 3]) * 10 ** 6 / abs(M)

        # NOTE: 原典90-106行 (e==0分岐でN/M検定をmax比較する旧版) は
        #       コメントアウト済みのため省略

        # せん断の検定
        ratio_output[:, 2], _ALW_Q = SA_RCSRpile_Qratio(Form, Fc, stress, timecase)
        # 最大検定値の保存
        if np.max(ratio_output) > np.max(maxratios[1]):
            maxratios[0] = ele_no
            maxratios[1] = np.max(ratio_output)
            maxratios_text = SA_RCSR_pileratio_text(
                Form, ele_length, steelbar, HOOP, Fc, stress, timecase,
                QL, ele_no, section_no, qup_beam, LOAD_CASE_NAME, RCQ,
                pick_section_name)
    else:
        print('ERROR:RC杭の断面形状設定ミス＜円でない杭＞')
        raise ValueError('RC杭の断面形状設定ミス＜円でない杭＞')  # 原典: stop

    return ratio_output, maxratios, maxratios_text
