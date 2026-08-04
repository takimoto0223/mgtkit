# -*- coding: utf-8 -*-
"""SRC断面算定の検定詳細文（テキスト出力）の逐語移植.

元コード:
  msrc/structural_function/SRC/SRC_section_analysis/beam/SRC_H_beam_ALWM_text.m
  msrc/structural_function/SRC/SRC_section_analysis/column/SRC_4Hcolumn_ALWNM_text.m
  msrc/structural_function/SRC/SRC_section_analysis/column/SRC_Rcolumn_ALWNM_text.m

共通規約:
  - MATLABの strvcat による行の積み上げ → Python版は list[str] を返す
    (strvcatの空白パディングは行わない。書式・桁数は num2str 互換の _num2str で維持)。
  - 断面力 stress は kN・m 系 (MIDAS準拠)。行 = i端/中央/j端 の3行。
  - display+stop → raise ValueError(日本語) # MATLAB: stop。
    stopなしの ERROR 代入(セミコロン無しのコンソール表示) は print("ERROR='...'") で再現。
  - 計算用関数 SA_SRCbeamQratio / SA_SRC_4Hcolumn_Qratio / SA_SRC_Rcolumn_Qratio /
    SD_check / find_index_rough は src_check 側 (並行作成中) の同名・同引数順の関数を
    各関数の先頭で遅延importして呼ぶ。find_index_rough はプロジェクト規約
    (util.py 参照: MATLAB 1-based → Python 0-based) 通り 0-based インデックスを
    返す想定で添字に直接利用する。
  - 元コードの怪しい挙動 (未定義関数 cmin、ALW_NM1 参照バグ等) もそのまま再現
    している (改変禁止のため)。詳細は各関数コメント参照。
"""

import functools
import math

import numpy as np

from .util import excelround
from .alst import ALST_S
from .secprops import steelForm
from .s_check import _num2str
from .rc_check import (
    Area_steelbar, outf_steelbar_JIS, ALST_RC_AIJ, ALST_steelbar_KJ, E_RC_AIJ,
)


def _matlabenv(func):
    """MATLAB同様、0除算等でエラーとせず Inf/NaN を返す環境で実行する."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with np.errstate(divide='ignore', invalid='ignore', over='ignore'):
            return func(*args, **kwargs)
    return wrapper


# ===========================================================================
# beam/SRC_H_beam_ALWM_text.m
# ===========================================================================

@_matlabenv
def SRC_H_beam_ALWM_text(Form, sForm, steelbar_u, steelbar_d, HOOP, Fc, SN,
                         timecase, stress, ele_no, section_no, QL, qup_beam,
                         LOAS_CASE_NAME, section_name):
    """SRC_H_beam_ALWM_text.m の逐語移植.

    元: msrc/structural_function/SRC/SRC_section_analysis/beam/SRC_H_beam_ALWM_text.m

    建築学会SRC基準/SRC梁断面算定
    !!!!!!!!!!!!!!!!!!注!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    このプログラムはRC断面検定ベースで作成しているので
    RCの断面を修正した場合にはSRCのものも修正すること．
    違いとしては主筋断面積および鉄骨断面積のチェック
    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    """
    from .src_check import SA_SRCbeamQratio  # src_check並行作成中のため遅延import

    S = np.atleast_2d(np.asarray(stress, dtype=float))
    su = np.atleast_2d(np.asarray(steelbar_u, dtype=float))
    sd_ = np.atleast_2d(np.asarray(steelbar_d, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    QLb = np.asarray(QL, dtype=float).ravel()

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # %%文字の定義

    # 長短期設定
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        # MATLAB: ERROR='長短期設定ミス' (表示のみで継続するが t_case 未定義で後続エラー)
        raise ValueError('長短期設定ミス')

    # 外形情報
    b = Form[0]
    D = Form[1]

    # 内蔵鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sZy = steelForm(sForm, 7) * 1000  # mm3
    sIx = steelForm(sForm, 2) * 10000  # mm4
    sIy = steelForm(sForm, 3) * 10000  # mm4

    # 配筋情報
    # load bar_table.mat (Python版: rc_check.Area_steelbar 内でモジュール読み込み)
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[3]
    cover_depth = HOOP[4]
    aw = Area_steelbar(di_support, HOOP[2])

    # 上端筋，下端筋の鉄筋本数や径，材種など
    num = np.zeros((2, 3))
    di_main = np.zeros((2, 3))
    SD_main = np.zeros((2, 3))
    num[0, :] = su[:, 1:3].sum(axis=1)
    di_main[0, :] = su[:, 3]
    SD_main[0, :] = su[:, 4]

    num[1, :] = sd_[:, 1:3].sum(axis=1)
    di_main[1, :] = sd_[:, 3]
    SD_main[1, :] = sd_[:, 4]

    # 梁の上端筋と下端筋の段数から重心距離の計算

    dd = np.zeros((2, 3)) + D  # 二段配筋の際には1000が書き換えられる．
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
                raise ValueError('配筋情報ミス（配筋段数）')  # MATLAB: stop
        elif su[i - 1, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔
            if su.shape[1] == 6:
                dd[0, i - 1] = su[i - 1, 5]
            elif su.shape[1] == 5:
                dd[0, i - 1] = excelround(max(di_main[0, i - 1] * 1.5, 25 * 1.5), 1) + outf_steelbar_JIS(di_main[0, i - 1])
            d[0, i - 1] = d_out[0, i - 1] * su[i - 1, 1] / num[0, i - 1] + (d_out[0, i - 1] + dd[0, i - 1]) * su[i - 1, 2] / num[0, i - 1]
        else:
            raise ValueError('配筋情報エラー（配筋段数）')  # MATLAB: stop

    # 下端筋
    for i in range(1, 4):
        d_out[1, i - 1] = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main[1, i - 1]) * 0.5
        if sd_[i - 1, 0] == 1:
            # 主筋の重心距離（一段配筋）
            d[1, i - 1] = d_out[1, i - 1]
            if sd_[i - 1, 2] == 0:
                pass
            else:
                raise ValueError('配筋情報ミス（配筋段数）')  # MATLAB: stop
        elif sd_[i - 1, 0] == 2:
            # 主筋の重心距離（二段配筋）

            # 主筋間隔 (MATLAB原典: 上端筋は25*1.5だが下端筋は25 — 非対称のまま再現)
            if sd_.shape[1] == 6:
                dd[1, i - 1] = sd_[i - 1, 5]
            elif sd_.shape[1] == 5:
                dd[1, i - 1] = excelround(max(di_main[1, i - 1] * 1.5, 25), 1) + outf_steelbar_JIS(di_main[1, i - 1])
            d[1, i - 1] = d_out[1, i - 1] * sd_[i - 1, 1] / num[1, i - 1] + (d_out[1, i - 1] + dd[1, i - 1]) * sd_[i - 1, 2] / num[1, i - 1]
        else:
            raise ValueError('配筋情報エラー（配筋段数）')  # MATLAB: stop

    a = Area_steelbar(di_main, 1)

    text = ['SRC梁(H鋼内蔵)の断面算定内容（最大検定値の検定内容）']
    text.append('強軸周りの曲げとせん断に対して断面算定を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')

    text.append('SRC梁サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
    text.append('内蔵鉄骨：H-' + _num2str(sForm[1]) + 'x' + _num2str(sForm[2]) + 'x' + _num2str(sForm[3]) + 'x' + _num2str(sForm[4]))

    text.append('　　')
    text.append('*****配筋情報（本数のあとの数字は段数を示す）*****')
    text.append('　　')
    text.append('上端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[0, 0]) + '(' + _num2str(su[0, 0]) + ')-D' + _num2str(di_main[0, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[0, 1]) + '(' + _num2str(su[1, 0]) + ')-D' + _num2str(di_main[0, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[0, 2]) + '(' + _num2str(su[2, 0]) + ')-D' + _num2str(di_main[0, 2]))
    text.append('　　')
    text.append('下端筋')
    text.append('(i端)主筋配筋：' + _num2str(num[1, 0]) + '(' + _num2str(sd_[0, 0]) + ')-D' + _num2str(di_main[1, 0]))
    text.append('(中央)主筋配筋：' + _num2str(num[1, 1]) + '(' + _num2str(sd_[1, 0]) + ')-D' + _num2str(di_main[1, 1]))
    text.append('(j端)主筋配筋：' + _num2str(num[1, 2]) + '(' + _num2str(sd_[2, 0]) + ')-D' + _num2str(di_main[1, 2]))
    text.append('　　')
    text.append('帯筋：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋：SD' + _num2str(SD_main[0, 0]) + '　帯筋：SD' + _num2str(SD_support))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    text.append('鉄骨：SN' + _num2str(SN))
    text.append('　　')

    rfc = [[None, None, None], [None, None, None]]
    for i in range(1, 3):
        for j in range(1, 4):
            rfc[i - 1][j - 1] = ALST_steelbar_KJ([di_main[i - 1, j - 1], SD_main[i - 1, j - 1]])

    # 許容応力度関係

    # 鉄骨量に対するコンクリート圧縮許容応力度の低減
    # 圧縮側鉄骨量の算定
    if sForm[0] == 1:
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
    else:
        raise ValueError('現在H鋼以外未対応')  # MATLAB: stop
    f_c = ALST_RC_AIJ(Fc)  # 鉄骨量による低減されたRC許容応力度
    sf = ALST_S([SN, max_t])  # 鉄骨の許容応力度（ただし利用するのは引張のみ）

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # その０：鉄筋あきのチェック
    dd2 = (b - 2 * d_out) / (np.vstack([np.max(su[:, 1:3], axis=1),
                                        np.max(sd_[:, 1:3], axis=1)]) - 1)
    outf_main = np.atleast_2d(np.asarray(outf_steelbar_JIS(di_main), dtype=float))
    check_dd = dd - outf_main
    check_dd2 = dd2 - outf_main

    checkdd = np.minimum(check_dd, check_dd2)
    min_dd = np.maximum(1.5 * di_main, 25 * 1.25)

    judge_dd = checkdd - min_dd

    if np.min(judge_dd) >= 0:
        pass
    else:
        print('H鋼内蔵SRC梁鉄筋あき不足(梁せいおよび梁幅方向の検討）')
        print(_num2str(section_no, '%15.0f'))
        print('梁せい方向あき：' + _num2str(np.min(check_dd), '%15.2f') + 'mm')
        print('梁幅方向あき：' + _num2str(np.min(check_dd2), '%15.2f') + 'mm')
        print('あきの最小値：' + _num2str(np.min(min_dd), '%15.2f') + 'mm')
        # stop (原典でコメントアウト)

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
        print("ERROR='主筋規定外(D13以上，配筋段数については既に検討済)'")  # MATLAB: ERROR代入表示(継続)
        # stop (原典でコメントアウト)

    # その２：引張鉄筋断面積の規定
    pt = num * a / (b * D)
    pt_ID1 = np.argmin(pt, axis=0)
    pt1 = np.min(pt, axis=0)
    pt_ID2 = int(np.argmin(pt1))
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
        up = 4 / 3
        # ERROR='引張鉄筋断面積不足（0.4％未満）' (原典でコメントアウト)
        # stop

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= min(D / 2, 250):
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(せいの半分かつ250mm以下)"')
    elif di_support > 10 and pitch <= min(D / 2, 450):
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(せいの半分かつ450mm以下)"')
    else:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"帯筋間隔NG"　梁せい：' + _num2str(D, '%15.0f') + 'mm')
        # stop (原典でコメントアウト)

    # その４：せん断補強筋比の規定
    text.append('　　')
    text.append('せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (np.min(b) * pitch) >= 0.2 / 100:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (np.min(b) * pitch) * 100, '%15.2f') + '%＞0.2%：OK')
    else:
        text.append('梁せい方向のせん断補強筋比' + _num2str(aw / (np.min(b) * pitch) * 100, '%15.2f') + '%＜0.2%：NG')
        print('せん断補強筋比不足（0.2％未満）　pw=' + _num2str(aw / (np.min(b) * pitch) * 100, '%15.2f') + '%→STRPピッチ' + _num2str(pitch, '%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '%15.0f') + '，　梁幅b：' + _num2str(np.min(b), '%15.0f') + 'mm')
    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth) + 'mm≧40mm：OK')
    else:
        print("ERROR='鉄筋かぶり厚再検討（40mm未満）'")  # MATLAB: ERROR代入表示(継続)
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth) + 'mm＜40mm：NG')

    # その６：鉄骨かぶりあつ
    text.append('　　')
    text.append('鉄骨のかぶりの規定(50mm)')
    if min((b - sb) / 2, (D - sD) / 2) >= 50:
        text.append('鉄骨かぶり厚：' + _num2str(min((b - sb) / 2, (D - sD) / 2)) + 'mm≧50mm：OK')
    else:
        print("ERROR='鉄骨かぶり厚再検討（50mm未満）'")  # MATLAB: ERROR代入表示(継続)
        # MATLAB原典: cmin は未定義関数のためこの分岐に入ると実行時エラーになる (そのまま再現)
        text.append('鉄骨かぶり厚：' + _num2str(cmin((b - sb) / 2, (D - sD) / 2)) + 'mm＜50mm：NG')  # noqa: F821

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

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # 以下SRC断面の検定（強軸周り用　10-12式）

    # 鉄骨部分の純曲げモーメント
    sMo = sZx * sf[int(timecase) - 1, 0] / 10 ** 6

    # 複筋長方形梁の中立軸算定（その１・正曲げ）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    dc = d[0, :]
    dt = D - d[1, :]
    ac = a[0, :] * num[0, :]
    at = a[1, :] * num[1, :]

    xn1 = np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2 + 2 / b * ((n - 1) * ac * dc + n * at * dt)) - ((n - 1) * ac + n * at) / b

    M1 = np.zeros((2, 3))
    M2 = np.zeros((2, 3))
    M3 = np.zeros((2, 3))

    # 許容曲げモーメントの算出
    # １：圧縮コンクリートの圧壊
    sig_c = f_c[int(timecase) - 1, 0]
    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M1[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(1, 4):
        if M1[0, i - 1] == 0:
            M1[0, i - 1] = np.inf

    # ２：引張鉄筋の降伏
    rfc_t = np.zeros(3)
    rfc_t[0] = rfc[1][0][int(timecase) - 1, 0]
    rfc_t[1] = rfc[1][1][int(timecase) - 1, 0]
    rfc_t[2] = rfc[1][2][int(timecase) - 1, 0]
    sig_c = rfc_t * xn1 / (D - d_out[1, :] - xn1) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M2[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(1, 4):
        if M2[0, i - 1] == 0:
            M2[0, i - 1] = np.inf

    # ３：圧縮鉄筋の降伏
    rfc_c = np.zeros(3)
    rfc_c[0] = rfc[0][0][int(timecase) - 1, 0]
    rfc_c[1] = rfc[0][1][int(timecase) - 1, 0]
    rfc_c[2] = rfc[0][2][int(timecase) - 1, 0]

    sig_c = rfc_c * xn1 / (xn1 - d_out[0, :]) / n

    Cc = sig_c * xn1 / 2 * b
    Cs = (n - 1) * sig_c * (xn1 - dc) / xn1 * ac
    M3[0, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - xn1 / 3))
    for i in range(1, 4):
        if M3[0, i - 1] == 0:
            M3[0, i - 1] = np.inf

    # 複筋長方形梁の中立軸算定（その２・負曲げ）%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    dc = d[1, :]
    dt = D - d[0, :]

    ac = a[1, :] * num[1, :]
    at = a[0, :] * num[0, :]

    xn2 = D - np.sqrt(((n - 1) * ac + n * at) ** 2 / b ** 2 + 2 / b * ((n - 1) * ac * dc + n * at * dt)) + ((n - 1) * ac + n * at) / b

    # 許容曲げモーメントの算出
    # １：圧縮コンクリートの圧壊
    sig_c = f_c[int(timecase) - 1, 0]

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M1[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(1, 4):
        if M1[1, i - 1] == 0:
            M1[1, i - 1] = np.inf

    # ２：引張鉄筋の降伏
    rfc_t[0] = rfc[0][0][int(timecase) - 1, 0]
    rfc_t[1] = rfc[0][1][int(timecase) - 1, 0]
    rfc_t[2] = rfc[0][2][int(timecase) - 1, 0]
    sig_c = rfc_t * (D - xn2) / (xn2 - d_out[0, :]) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M2[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(1, 4):
        if M2[1, i - 1] == 0:
            M2[1, i - 1] = np.inf

    # ３：圧縮鉄筋の降伏
    rfc_c[0] = rfc[1][0][int(timecase) - 1, 0]
    rfc_c[1] = rfc[1][1][int(timecase) - 1, 0]
    rfc_c[2] = rfc[1][2][int(timecase) - 1, 0]
    sig_c = rfc_c * (D - xn2) / ((D - d_out[1, :]) - xn2) / n

    Cc = sig_c * (D - xn2) / 2 * b
    Cs = (n - 1) * sig_c * ((D - xn2) - dc) / (D - xn2) * ac
    M3[1, :] = np.maximum(0, Cs * (dt - dc) + Cc * (dt - (D - xn2) / 3))
    for i in range(1, 4):
        if M3[1, i - 1] == 0:
            M3[1, i - 1] = np.inf

    # 許容曲げモーメントの算出%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    M = np.minimum(M1, M3)
    M = np.minimum(M, M2)
    ALW_M = M / 10 ** 6 + sMo
    ALW_M[1, :] = ALW_M[1, :] * -1
    ALW_M = ALW_M.T

    ratio_plus = S[:, 3] / ALW_M[:, 0]
    ratio_minus = S[:, 3] / ALW_M[:, 1]
    ratio_output = np.zeros((3, 3))
    ratio_output[:, 0] = np.max(np.column_stack([ratio_plus, ratio_minus]), axis=1)

    if timecase == 1:
        designQ = np.abs(S[:, 2])
    else:
        designQ = qup_beam * np.abs(S[:, 2] - QLb) + np.abs(QLb)

    (rq, ALW_sQ, ALW_RCQ, qrc, alph, j_dis, Mmax, Qmax) = SA_SRCbeamQratio(
        Form, sForm, steelbar_u, steelbar_d, HOOP, Fc, SN, stress, timecase,
        QL, qup_beam, ALW_M)
    ratio_output[:, 1:3] = np.atleast_2d(np.asarray(rq, dtype=float)).reshape(3, 2)
    # MATLAB: ALW_sQ(1), ALW_RCQ(i,1) — 列ベクトル想定でravelして添字参照
    ALW_sQ = np.asarray(ALW_sQ, dtype=float).ravel()
    ALW_RCQ = np.asarray(ALW_RCQ, dtype=float).ravel()
    alph = np.asarray(alph, dtype=float).ravel()
    j_dis = np.asarray(j_dis, dtype=float).ravel()

    text.append('*****設計用応力*****')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    if timecase == 1:
        pass
    else:
        text.append('長期荷重時せん断力：' + _num2str(QLb[0], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[0], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    if timecase == 1:
        pass
    else:
        text.append('長期荷重時せん断力：' + _num2str(QLb[1], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(designQ[1], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(My[1] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[1], '%15.1f') + '　[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    if timecase == 1:
        pass
    else:
        text.append('長期荷重時せん断力：' + _num2str(QLb[2], '%15.1f') + '　[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_beam, '%15.1f') + ')から決まる設計用せん断力：' + _num2str(designQ[2], '%15.1f') + '　[kN]')
    text.append('曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　せん断力Qz　：' + _num2str(designQ[2], '%15.1f') + '　[kN]')
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
    text.append('正曲げMy　：' + _num2str(ALW_M[0, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[0, 1], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[0, 2], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[0, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　中央')
    text.append('正曲げMy　：' + _num2str(ALW_M[1, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[1, 1], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[1], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[1, 2], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[1, 1], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('正曲げMy　：' + _num2str(ALW_M[2, 0], '%15.1f') + '　[kNm]　負曲げMy　：' + _num2str(ALW_M[2, 1], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[2], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[2, 2], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[2, 1], '%15.2f'))
    return text


# ===========================================================================
# column/SRC_4Hcolumn_ALWNM_text.m
# ===========================================================================

@_matlabenv
def SRC_4Hcolumn_ALWNM_text(Form, sForm, steelbar, HOOP, Fc, SN, timecase,
                            stress, ele_no, section_no, QL, qup_src,
                            LOAS_CASE_NAME, ALW_NM1, ALW_NM2, RCQ,
                            section_name):
    """SRC_4Hcolumn_ALWNM_text.m の逐語移植.

    元: msrc/structural_function/SRC/SRC_section_analysis/column/SRC_4Hcolumn_ALWNM_text.m
    """
    from .src_check import (  # src_check並行作成中のため遅延import
        SD_check, find_index_rough, SA_SRC_4Hcolumn_Qratio,
    )

    S = np.atleast_2d(np.asarray(stress, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    QL2 = np.atleast_2d(np.asarray(QL, dtype=float))
    ALW_NM1 = np.atleast_2d(np.asarray(ALW_NM1, dtype=float))
    ALW_NM2 = np.atleast_2d(np.asarray(ALW_NM2, dtype=float))

    Fxx = -1 * S[:, 0]  # 軸力[N]
    My = S[:, 3]  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4]  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2]  # せん断力強軸方向[N]
    Fy = S[:, 1]  # せん断力弱軸方向[N]

    # 長短期設定
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        # MATLAB: ERROR='長短期設定ミス' (表示のみで継続するが t_case 未定義で後続エラー)
        raise ValueError('長短期設定ミス')

    # 外形情報
    b = Form[0]
    D = Form[1]

    # 配筋情報
    # load bar_table.mat (Python版: rc_check.Area_steelbar 内でモジュール読み込み)
    num = steelbar[2]
    di_main = steelbar[1]
    SD_main = SD_check(di_main)
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]
    cover_depth = HOOP[3]

    a = Area_steelbar(di_main, 1)

    if sForm[0] == 1:

        # 鉄骨情報（断面積および強軸弱軸の断面係数）
        sAe = steelForm(sForm, 1) * 100  # mm2
        max_t = np.max(sForm[np.array([3, 4, 6])])  # MATLAB: max([sForm(4) sForm(5) sForm(7)])

        text = ['SRC柱(H鋼内蔵角柱)の断面算定内容（最大検定値の検定内容）']

        text.append('軸力と曲げ，およびせん断に対して断面算定を行う．')
        text.append('　　')

        text.append('断面符号：' + section_name + '断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
        text.append('　　')

        text.append('SRC柱サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
        text.append('内蔵鉄骨：H-' + _num2str(sForm[1]) + 'x' + _num2str(sForm[2]) + 'x' + _num2str(sForm[3]) + 'x' + _num2str(sForm[4]))

    elif sForm[0] == 2:

        # 鉄骨情報（断面積および強軸弱軸の断面係数）
        # (steelForm種別2=十字形鋼: secprops.BPLUS <移植済 2026-07-09>)
        sAe = steelForm(sForm, 1) * 100  # mm2
        max_t = np.max(sForm[np.array([3, 4, 7, 8])])

        text = ['SRC柱(CROSS-H鋼内蔵角柱)の断面算定内容（最大検定値の検定内容）']

        text.append('軸力と曲げ，およびせん断に対して断面算定を行う．')
        text.append('　　')

        text.append('断面符号：' + section_name + '断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
        text.append('　　')

        text.append('SRC柱サイズ：bxD-' + _num2str(b) + '[mm] x' + _num2str(D) + '[mm]')
        text.append('内蔵鉄骨：H-' + _num2str(sForm[1]) + 'x' + _num2str(sForm[2]) + 'x' + _num2str(sForm[3]) + 'x' + _num2str(sForm[4]))
        text.append('内蔵鉄骨：2T-' + _num2str(sForm[5]) + 'x' + _num2str(sForm[6]) + 'x' + _num2str(sForm[7]) + 'x' + _num2str(sForm[8]))

    # MATLAB原典: sForm(1)が1/2以外のとき text 未定義のまま後続でエラー
    # (Python版も同様に UnboundLocalError となる)

    text.append('　　')
    text.append('*****配筋情報*****')
    text.append('　　')
    text.append('主筋配筋：' + _num2str(num) + '-D' + _num2str(di_main))
    text.append('帯筋：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋：SD' + _num2str(SD_main) + '　帯筋：SD' + _num2str(SD_support))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    text.append('鉄骨：SN' + _num2str(SN))
    text.append('　　')

    # 許容応力度関係

    # 鉄骨量に対するコンクリート圧縮許容応力度の低減
    # 圧縮側鉄骨量の算定
    if sForm[0] == 1:
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
    else:
        # display('鉄骨量に対するコンクリート圧縮許容応力度の低減：現在H鋼以外未対応')
        pass
    f_c = ALST_RC_AIJ(Fc)  # 鉄骨量による低減されたRC許容応力度
    rfc = ALST_steelbar_KJ([di_main, SD_main])  # 柱主筋の許容応力度
    sf = ALST_S([SN, max_t])  # 鉄骨の許容応力度（ただし利用するのは引張のみ）

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    d1 = D - dc
    d2 = b - dc

    check_dd = min((D - 2 * dc) / (steelbar[2] / 2 + 1 - (steelbar[3] - 1)),
                   (b - 2 * dc) / (steelbar[3] - 1))
    # その０：鉄筋あきのチェック
    if (check_dd - outf_steelbar_JIS(di_main) > 1.5 * di_main and
            check_dd - outf_steelbar_JIS(di_main) > 25):
        pass
    else:
        print('SRC柱：鉄筋あき不足(H鋼内蔵/crooHSRC柱）')
        print('鉄筋あき：' + _num2str(check_dd - outf_steelbar_JIS(di_main), '%15.2f') + 'mm')

    text.append('　　')
    text.append('主筋のあきの検討')
    text.append('最低あき寸法（径の1.5倍，粗骨材寸法の1.25倍，25mmの最大値）：' + _num2str(max(25, 1.5 * di_main), '%15.1f'))
    text.append('鉄筋あき寸法[mm]：' + _num2str(check_dd - outf_steelbar_JIS(di_main), '%15.1f'))

    # その１：主筋の規定
    text.append('　　')
    text.append('主筋の規定（径D13以上および4本以上）')
    text.append('使用鉄筋の径：D' + _num2str(di_main))
    if num >= 4 and di_main >= 13:
        text.append('主筋径の規定：OK')
    else:
        text.append('主筋径の規定：NG')
        print('主筋規定外(D13/4本以上)')

    # その２：主筋断面積の規定

    text.append('　　')
    text.append('鉄筋および鉄骨必要最低断面積（全断面の0.8％以上）')
    text.append('鉄筋仕様：' + _num2str(num) + '-D' + _num2str(di_main))
    text.append('鉄筋総断面積' + _num2str(num * a / 100, '%15.2f') + '[cm2]')
    text.append('鉄骨総断面積' + _num2str(sAe / 100, '%15.2f') + '[cm2]')

    if (num * a + sAe) / (b * D) >= 0.8 / 100:
        text.append('鉄筋および鉄骨断面積比' + _num2str((num * a + sAe) / (b * D) * 100, '%15.2f') + '[%]　"OK"')
    else:
        print('H内蔵SRC柱：主筋/鋼材の断面積不足（0.8％未満）')
        text.append('鉄筋および鉄骨断面積比' + _num2str((num * a + sAe) / (b * D) * 100, '%15.2f') + '[%]　"0.8%以下"')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= 100:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(D10/100mm以下)"')
    elif di_support > 10 and pitch <= 200:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(D13以上/200mm以下)"')
    else:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"帯筋間隔NG"　')
        print('SRC柱：帯筋間隔NG（D10@100以下もしくはD13以上@200以下）')

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    text.append('　　')
    text.append('せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (max(b, D) * pitch) >= 0.2 / 100:
        text.append('せん断補強筋比' + _num2str(aw / (max(b, D) * pitch) * 100, '%15.2f') + '%＞0.2%：OK')
    else:
        text.append('せん断補強筋比' + _num2str(aw / (max(b, D) * pitch) * 100, '%15.2f') + '%＜0.2%：NG')
        print('H鋼内蔵SRC柱：帯筋比不足（0.2％未満）　pw=' + _num2str(aw / (max(b, D) * pitch) * 100, '%15.2f') + '%→HOOPピッチ' + _num2str(pitch, '%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '%15.0f') + '，　柱幅D：' + _num2str(max(b, D), '%15.0f') + 'mm')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth, '%15.1f') + 'mm≧40mm：OK')
    else:
        print("ERROR='鉄筋かぶり厚再検討（40mm未満）'")  # MATLAB: ERROR代入表示(継続)
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth, '%15.1f') + 'mm＜40mm：NG')

    # その６：鉄骨かぶりあつ
    text.append('　　')
    text.append('鉄骨のかぶりの規定(50mm)')
    if (D - sForm[1]) / 2 >= 50 and (b - sForm[2]) / 2 >= 50:
        text.append('鉄骨かぶり厚：' + _num2str(min((D - sForm[1]) / 2, (b - sForm[2]) / 2), '%15.1f') + 'mm≧50mm：OK')
    else:
        print('鉄骨かぶり厚再検討（50mm未満）')
        text.append('鉄骨かぶり厚：' + _num2str(min((D - sForm[1]) / 2, (b - sForm[2]) / 2), '%15.1f') + 'mm＜50mm：NG')

    # 曲げモーメントの検定
    # (find_index_rough は src_check 版: 0-based インデックスの ndarray を返す想定)
    ratio_output = np.zeros((3, 4))
    idx1 = np.asarray(find_index_rough(ALW_NM1[:, 0], S[:, 0]), dtype=int)
    ratio_output[:, 0] = np.abs(S[:, 3]) / ALW_NM1[idx1, 1]
    ALW_M1 = ALW_NM1[idx1, 1]
    idx2 = np.asarray(find_index_rough(ALW_NM2[:, 0], S[:, 0]), dtype=int)
    ratio_output[:, 1] = np.abs(S[:, 4]) / ALW_NM2[idx2, 1]
    ALW_M2 = ALW_NM2[idx2, 1]

    for i in range(1, 3):  # 曲げが0のときの軸力比
        for j in range(1, 4):
            if ratio_output[j - 1, i - 1] == 0:
                if S[j - 1, 0] > 0:  # 圧縮
                    # MATLAB原典: i=2(弱軸Mz)の列でも ALW_NM1 を参照している (バグの可能性・そのまま再現)
                    ratio_output[j - 1, i - 1] = S[j - 1, 0] / np.max(ALW_NM1[:, 0])
                elif S[j - 1, 0] < 0:  # 引張
                    ratio_output[j - 1, i - 1] = S[j - 1, 0] / np.min(ALW_NM1[:, 0])

    (rq, ALW_sQ1, ALW_RCQ1, qrc1, alph1, d1, Mmax1, Qmax1,
     ALW_sQ2, ALW_RCQ2, qrc2, alph2, d2, Mmax2, Qmax2, Qs1) = SA_SRC_4Hcolumn_Qratio(
        Form, sForm, steelbar, HOOP, [Fc[0], 0], SN, stress, timecase, QL,
        qup_src, ALW_NM1, ALW_NM2, RCQ)
    ratio_output[:, 2:4] = np.atleast_2d(np.asarray(rq, dtype=float)).reshape(3, 2)
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float)).T

    text.append('　　')
    text.append('*****設計用応力*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による:現在未対応')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による:現在未対応')
    else:
        text.append('せん断の設計方法：応力割増値nから決まる値による')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    text.append('軸力　：' + _num2str(Fxx[0], '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[0], '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[0], '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz(強軸)　：' + _num2str(Fz[0], '%15.1f') + '　[kN]' + '　　せん断力Qy(弱軸)　：' + _num2str(Fy[0], '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz(強軸)　：' + _num2str(QL2[0, 1], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(QL2[0, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz(強軸)：' +
                    _num2str(Qs1[0, 0], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(Qs1[1, 0], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力　：' + _num2str(Fxx[1], '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[1], '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[1], '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz(強軸)　：' + _num2str(Fz[1], '%15.1f') + '　[kN]' + '　　せん断力Qy(弱軸)　：' + _num2str(Fy[1], '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz(強軸)　：' + _num2str(QL2[1, 1], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(QL2[1, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz(強軸)：' +
                    _num2str(Qs1[0, 1], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(Qs1[1, 1], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    text.append('軸力　：' + _num2str(Fxx[2], '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[2], '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[2], '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz(強軸)　：' + _num2str(Fz[2], '%15.1f') + '　[kN]' + '　　せん断力Qy(弱軸)　：' + _num2str(Fy[2], '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz(強軸)　：' + _num2str(QL2[2, 1], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(QL2[2, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz(強軸)：' +
                    _num2str(Qs1[0, 2], '%15.1f') + '[kN]' + '　　Qy(弱軸)　：' + _num2str(Qs1[1, 2], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # 以下SRC断面の検定（強軸周り用　10-12式）

    # 長短期設定
    if timecase >= 2:
        pass
    elif timecase == 1:
        text.append('せん断耐力算定α(強軸)：' + _num2str(alph1, '%15.1f'))
        text.append('Mmax(kNm)：' + _num2str(Mmax1, '%15.1f'))
        text.append('Qmax(kN)：' + _num2str(Qmax1, '%15.1f'))
        text.append('　　')
        text.append('せん断耐力算定α(弱軸)：' + _num2str(alph2, '%15.1f'))
        text.append('Mmax(kNm)：' + _num2str(Mmax2, '%15.1f'))
        text.append('Qmax(kN)：' + _num2str(Qmax2, '%15.1f'))
        text.append('　　')

    text.append('*****許容耐力・検定比*****')

    text.append('*許容耐力(せん断力)')
    text.append('せん断力Qz(強軸)(S)　：' + _num2str(ALW_sQ1, '%15.1f') + '　[kN]　せん断力Qz(強軸)(RC)　：' + _num2str(ALW_RCQ1, '%15.1f') + '　[kN]')
    text.append('せん断力Qy(弱軸)(S)　：' + _num2str(ALW_sQ2, '%15.1f') + '　[kN]　せん断力Qy(弱軸)(RC)　：' + _num2str(ALW_RCQ2, '%15.1f') + '　[kN]')
    text.append('　　')

    text.append('*許容耐力　[' + t_case + ']　i　端')
    text.append('曲げMy(強軸)　：' + _num2str(ALW_M1[0], '%15.1f') + '　[kNm]　曲げMz(弱軸)　：' + _num2str(ALW_M2[0], '%15.1f') + '　[kNm]')
    text.append('検定比　[My(強軸)/軸力比]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Mz(弱軸)/軸力比]：' + _num2str(ratio_output[0, 1], '%15.2f'))
    text.append('[Qz(強軸)]：' + _num2str(ratio_output[0, 2], '%15.2f') + '　[Qy(弱軸)]：' + _num2str(ratio_output[0, 3], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　中央')
    text.append('曲げMy(強軸)　：' + _num2str(ALW_M1[1], '%15.1f') + '　[kNm]　曲げMz(弱軸)　：' + _num2str(ALW_M2[1], '%15.1f') + '　[kNm]')
    text.append('検定比　[My(強軸)/軸力比]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Mz(弱軸)/軸力比]：' + _num2str(ratio_output[1, 1], '%15.2f'))
    text.append('[Qz(強軸)]：' + _num2str(ratio_output[1, 2], '%15.2f') + '　[Qy(弱軸)]：' + _num2str(ratio_output[1, 3], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('曲げMy(強軸)　：' + _num2str(ALW_M1[2], '%15.1f') + '　[kNm]　曲げMz(弱軸)　：' + _num2str(ALW_M2[2], '%15.1f') + '　[kNm]')
    text.append('検定比　[My(強軸)/軸力比]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Mz(弱軸)/軸力比]：' + _num2str(ratio_output[2, 1], '%15.2f'))
    text.append('[Qz(強軸)]：' + _num2str(ratio_output[2, 2], '%15.2f') + '　[Qy(弱軸)]：' + _num2str(ratio_output[2, 3], '%15.2f'))
    return text


# ===========================================================================
# column/SRC_Rcolumn_ALWNM_text.m
# ===========================================================================

@_matlabenv
def SRC_Rcolumn_ALWNM_text(Form, sForm, steelbar, HOOP, Fc, SN, timecase,
                           stress, ele_no, section_no, QL, qup_src,
                           LOAS_CASE_NAME, ALW_NM, RCQ, section_name):
    """SRC_Rcolumn_ALWNM_text.m の逐語移植.

    元: msrc/structural_function/SRC/SRC_section_analysis/column/SRC_Rcolumn_ALWNM_text.m
    """
    from .src_check import (  # src_check並行作成中のため遅延import
        SD_check, find_index_rough, SA_SRC_Rcolumn_Qratio,
    )

    S = np.atleast_2d(np.asarray(stress, dtype=float))
    Form = np.asarray(Form, dtype=float).ravel()
    sForm = np.asarray(sForm, dtype=float).ravel()
    steelbar = np.asarray(steelbar, dtype=float).ravel()
    HOOP = np.asarray(HOOP, dtype=float).ravel()
    Fc = np.asarray(Fc, dtype=float).ravel()
    QL2 = np.atleast_2d(np.asarray(QL, dtype=float))
    ALW_NM = np.atleast_2d(np.asarray(ALW_NM, dtype=float))

    Fxx = S[:, 0] * 10 ** 3  # 軸力[N]
    My = S[:, 3] * 10 ** 6  # 曲げモーメント強軸[Nmm]
    Mz = S[:, 4] * 10 ** 6  # 曲げモーメント弱軸[Nmm]
    Fz = S[:, 2] * 10 ** 3  # せん断力強軸方向[N]
    Fy = S[:, 1] * 10 ** 3  # せん断力弱軸方向[N]

    # %%文字の定義

    # 長短期設定
    if timecase >= 2:
        t_case = LOAS_CASE_NAME
    elif timecase == 1:
        t_case = LOAS_CASE_NAME
    else:
        # MATLAB: ERROR='長短期設定ミス' (表示のみで継続するが t_case 未定義で後続エラー)
        raise ValueError('長短期設定ミス')

    # 外形情報
    D = Form[0]
    r = Form[0] / 2
    ts = sForm[2]
    # 鉄骨情報（断面積および強軸弱軸の断面係数）
    sAe = steelForm(sForm, 1) * 100  # mm2
    sZx = steelForm(sForm, 6) * 1000  # mm3
    sIx = steelForm(sForm, 2) * 10000  # mm4

    # 配筋情報
    # load bar_table.mat (Python版: rc_check.Area_steelbar 内でモジュール読み込み)
    num = steelbar[2]
    di_main = steelbar[1]
    SD_main = SD_check(di_main)
    di_support = HOOP[0]
    pitch = HOOP[1]
    SD_support = HOOP[2]
    cover_depth = HOOP[3]

    a = Area_steelbar(di_main, 1)

    text = ['SRC柱(鋼管被覆)の断面算定内容（最大検定値の検定内容）']
    text.append('軸力と曲げ，およびせん断に対して断面算定を行う．')
    text.append('　　')

    text.append('断面符号：' + section_name + '断面番号：' + _num2str(section_no) + '　要素番号：' + _num2str(ele_no))
    text.append('　　')

    text.append('SRC柱サイズ：D-' + _num2str(D) + '[mm]')
    text.append('内蔵鉄骨：○-' + _num2str(sForm[1]) + 'x' + _num2str(sForm[2]))

    text.append('　　')
    text.append('*****配筋情報*****')
    text.append('　　')
    text.append('主筋配筋：' + _num2str(num) + '-D' + _num2str(di_main))
    text.append('帯筋：D' + _num2str(di_support) + '@' + _num2str(pitch))
    text.append('　　')
    text.append('*****使用材料（鉄筋およびコンクリート）*****')
    text.append('主筋：SD' + _num2str(SD_main) + '　帯筋：SD' + _num2str(SD_support))
    text.append('コンクリート：Fc' + _num2str(Fc[0]))
    text.append('鉄骨：SN' + _num2str(SN))
    text.append('　　')

    # 許容応力度関係

    # 鉄骨量に対するコンクリート圧縮許容応力度の低減
    # 圧縮側鉄骨量の算定
    if sForm[0] == 1:
        sb = sForm[2]
        sD = sForm[1]
        max_t = np.max(sForm[3:5])
    else:
        print('鉄骨量に対するコンクリート圧縮許容応力度の低減：現在H鋼以外未対応')
    f_c = ALST_RC_AIJ(Fc)  # 鉄骨量による低減されたRC許容応力度
    rfc = ALST_steelbar_KJ([di_main, SD_main])  # 柱主筋の許容応力度
    # MATLAB原典: 板厚でなく鋼管径 sForm(2) を板厚として渡している (そのまま再現)
    sf = ALST_S([SN, sForm[1]])  # 鉄骨の許容応力度（ただし利用するのは引張のみ）

    # ヤング係数比
    n = E_RC_AIJ(Fc)
    n = n[1]

    text.append('*****計算外規定*****')

    # 構造細則(計算外規定のチェック)
    # 柱主筋の重心の縁距離
    dc = cover_depth + outf_steelbar_JIS(di_support) + outf_steelbar_JIS(di_main) * 0.5
    rr = r - dc

    # その０：鉄筋あきのチェック
    if (2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 1.5 * di_main and
            2 * math.pi * rr / num - outf_steelbar_JIS(di_main) > 25):
        pass
    else:
        print('SRC柱：鉄筋あき不足(鋼管内蔵丸柱）')
        print('鉄筋あき：' + _num2str(2 * math.pi * rr / num - outf_steelbar_JIS(di_main), '%15.2f') + 'mm')

    text.append('　　')
    text.append('主筋のあきの検討')
    text.append('最低あき寸法（径の1.5倍，粗骨材寸法の1.25倍，25mmの最大値）：' + _num2str(max(25, 1.5 * di_main), '%15.1f'))
    text.append('鉄筋あき寸法[mm]：' + _num2str(2 * math.pi * rr / num - outf_steelbar_JIS(di_main), '%15.1f'))

    # その１：主筋の規定
    text.append('　　')
    text.append('主筋の規定（径D13以上および4本以上）')
    text.append('使用鉄筋の径：D' + _num2str(di_main))
    if num >= 4 and di_main >= 13:
        text.append('主筋径の規定：OK')
    else:
        text.append('主筋径の規定：NG')
        print('主筋規定外(D13/4本以上)')

    # その２：主筋断面積の規定

    text.append('　　')
    text.append('鉄筋および鉄骨必要最低断面積（全断面の0.8％以上）')
    text.append('鉄筋仕様：' + _num2str(num) + '-D' + _num2str(di_main))
    text.append('鉄筋総断面積' + _num2str(num * a / 100, '%15.2f') + '[cm2]')
    text.append('鉄骨総断面積' + _num2str(sAe / 100, '%15.2f') + '[cm2]')

    if (num * a + sAe) / (r ** 2 * math.pi) >= 0.8 / 100:
        text.append('鉄筋および鉄骨断面積比' + _num2str((num * a + sAe) / (r ** 2 * math.pi) * 100, '%15.2f') + '[%]　"OK"')
    else:
        print('SRC丸柱：主筋/鋼材の断面積不足（0.8％未満）')
        text.append('鉄筋および鉄骨断面積比' + _num2str((num * a + sAe) / (r ** 2 * math.pi) * 100, '%15.2f') + '[%]　"0.8%以下"')

    # その３：HOOP間隔の規定
    text.append('　　')
    text.append('HOOP筋径・間隔の規定')
    if di_support == 10 and pitch <= 100:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(D10/100mm以下)"')
    elif di_support > 10 and pitch <= 200:
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"OK(D13以上/200mm以下)"')
    else:
        print('SRC柱：帯筋間隔NG（D10@100以下もしくはD13以上@200以下）')
        text.append('HOOP筋径：D' + _num2str(di_support) + '　HOOP筋間隔：' + _num2str(pitch) + '　"帯筋間隔NG"　')
        # stop (原典でコメントアウト)

    # その４：せん断補強筋比の規定
    aw = Area_steelbar(di_support, 2)
    text.append('　　')
    text.append('せん断補強筋比の規定(0.2％以上)')
    text.append('HOOP筋の仕様：D' + _num2str(di_support) + '@' + _num2str(pitch))

    if aw / (D * pitch) >= 0.2 / 100:
        text.append('せん断補強筋比' + _num2str(aw / (D * pitch) * 100, '%15.2f') + '%＞0.2%：OK')
    else:
        text.append('せん断補強筋比' + _num2str(aw / (D * pitch) * 100, '%15.2f') + '%＜0.2%：NG')
        print('SRC丸柱：帯筋比不足（0.2％未満）　pw=' + _num2str(aw / (D * pitch) * 100, '%15.2f') + '%→HOOPピッチ' + _num2str(pitch, '%15.0f') +
              'mm，　STRP径：D' + _num2str(di_support, '%15.0f') + '，　柱幅D：' + _num2str(D, '%15.0f') + 'mm')

    # その５：鉄筋かぶりあつ
    text.append('　　')
    text.append('鉄筋かぶりの規定(40mm)')
    if cover_depth >= 40:
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth, '%15.1f') + 'mm≧40mm：OK')
    else:
        print("ERROR='鉄筋かぶり厚再検討（40mm未満）'")  # MATLAB: ERROR代入表示(継続)
        text.append('鉄筋かぶり厚：' + _num2str(cover_depth, '%15.1f') + 'mm＜40mm：NG')

    # その６：鉄骨かぶりあつ
    text.append('　　')
    text.append('鉄骨のかぶりの規定(50mm)')
    if (D - sForm[1]) / 2 >= 50:
        text.append('鉄骨かぶり厚：' + _num2str((D - sForm[1]) / 2, '%15.1f') + 'mm≧50mm：OK')
    else:
        print('鉄骨かぶり厚再検討（50mm未満）')
        text.append('鉄骨かぶり厚：' + _num2str((D - sForm[1]) / 2, '%15.1f') + 'mm＜50mm：NG')

    # 曲げモーメントの検定
    # (find_index_rough は src_check 版: 0-based インデックスの ndarray を返す想定)
    ratio_output = np.zeros((3, 4))
    idx = np.asarray(find_index_rough(ALW_NM[:, 0], S[:, 0]), dtype=int)
    ratio_output[:, 0] = np.sqrt(S[:, 3] ** 2 + S[:, 4] ** 2) / ALW_NM[idx, 1]
    ALW_M = ALW_NM[idx, 1]

    (rq, ALW_sQ, ALW_RCQ, qrc, alph, d, Mmax, Qmax, Qs1) = SA_SRC_Rcolumn_Qratio(
        Form, sForm, steelbar, HOOP, [Fc[0], 0], SN, stress, timecase, QL,
        qup_src, ALW_NM, RCQ)
    ratio_output[:, 2:4] = np.atleast_2d(np.asarray(rq, dtype=float)).reshape(3, 2)
    Qs1 = np.atleast_2d(np.asarray(Qs1, dtype=float)).T
    # MATLAB: ALW_sQ(1), ALW_RCQ(1) — ravelして先頭要素を参照
    ALW_sQ = np.asarray(ALW_sQ, dtype=float).ravel()
    ALW_RCQ = np.asarray(ALW_RCQ, dtype=float).ravel()

    text.append('　　')
    text.append('*****設計用応力*****')
    if RCQ == 3:
        text.append('せん断の設計方法：応力割増/材端Mから決まる小さい値による:現在未対応')
    elif RCQ == 2:
        text.append('せん断の設計方法：部材端終局モーメント時のせん断による:現在未対応')
    else:
        text.append('せん断の設計方法：応力割増値nから決まる値による')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　i　端')
    text.append('軸力　：' + _num2str(Fxx[0] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[0] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[0] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[0] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[0] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QL2[0, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QL2[0, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 0], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 0], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　中央')
    text.append('軸力　：' + _num2str(Fxx[1] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[1] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[1] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[1] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[1] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QL2[1, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QL2[1, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 1], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('*設計用応力　[' + t_case + ']　j　端')
    text.append('軸力　：' + _num2str(Fxx[2] / 1000, '%15.1f') + '　[kN]　　曲げMy　：' + _num2str(My[2] / 10 ** 6, '%15.1f') + '　[kNm]　　曲げMz　：' + _num2str(Mz[2] / 10 ** 6, '%15.1f') + '　[kNm]')
    if timecase == 1:
        text.append('せん断力Qz　：' + _num2str(Fz[2] / 10 ** 3, '%15.1f') + '　[kN]' + '　　せん断力Qy　：' + _num2str(Fy[2] / 10 ** 3, '%15.1f') + '　[kN]')
    else:
        text.append('長期荷重時せん断力Qz　：' + _num2str(QL2[2, 1], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(QL2[2, 0], '%15.1f') + '[kN]')
        text.append('せん断割増し(n=' + _num2str(qup_src, '%15.1f') + ')から決まる設計用せん断力Qz：' +
                    _num2str(Qs1[0, 2], '%15.1f') + '[kN]' + '　　Qy　：' + _num2str(Qs1[1, 2], '%15.1f') + '[kN]')
    text.append('　　')
    text.append('　　')

    # %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
    # 以下SRC断面の検定（強軸周り用　10-12式）

    # 長短期設定
    if timecase >= 2:
        pass
    elif timecase == 1:
        text.append('せん断耐力算定α：' + _num2str(alph, '%15.1f'))
        text.append('Mmax(kNm)：' + _num2str(Mmax, '%15.1f'))
        text.append('Qmax(kN)：' + _num2str(Qmax, '%15.1f'))
        text.append('　　')

    text.append('*****許容耐力・検定比*****')
    text.append('*許容耐力　[' + t_case + ']　i　端')
    text.append('曲げM　：' + _num2str(ALW_M[0], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[0, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[0, 3], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[0, 2], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　中央')
    text.append('曲げM　：' + _num2str(ALW_M[1], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[1, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[1, 3], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[1, 2], '%15.2f'))

    text.append('　　')
    text.append('*許容耐力　[' + t_case + ']　j　端')
    text.append('曲げM　：' + _num2str(ALW_M[2], '%15.1f') + '　[kNm]')
    text.append('せん断力Qz(S)　：' + _num2str(ALW_sQ[0], '%15.1f') + '　[kN]　せん断力Qz(RC)　：' + _num2str(ALW_RCQ[0], '%15.1f') + '　[kN]')
    text.append('検定比　[My]：' + _num2str(ratio_output[2, 0], '%15.2f') + '　[Qz(S)]：' + _num2str(ratio_output[2, 3], '%15.2f') + '　[Qz(RC)]：' + _num2str(ratio_output[2, 2], '%15.2f'))
    return text
