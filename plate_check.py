# -*- coding: utf-8 -*-
"""板要素検定 (S/RC/W_plate_ratio_analysis) の逐語移植.

元コード:
  privatetool_function/MIDAS/ratio/S/S_plate_ratio_analysis.m   (鉄骨板)
  privatetool_function/MIDAS/ratio/RC/RC_plate_ratio_analysis.m (RC板)
  privatetool_function/MIDAS/ratio/W/W_plate_ratio_analysis.m   (木面材壁・壁倍率)

共通仕様 (3関数とも同型):
  X_plate_ratio_analysis(thick, stress, timecase, material_no, thick_no,
                         maxratios, maxratios_text, LOAS_CASE_NAME, plate_no)
    thick       : 板厚mm (RC/S) または 壁倍率 (W)
    stress      : 板の面内せん断 1値 (kN/m)
    timecase    : 1=長期 / >=10=短期
    material_no : RC=Fc値, S=SN情報ベクトル(下記), W=未使用
    thick_no    : 厚さ番号, plate_no: 要素番号 (詳細テキスト表示用)
    maxratios   : [最大検定比の要素番号, 最大検定比] (長さ2)
    maxratios_text : 最大検定比要素の詳細テキスト (list[str])
    LOAS_CASE_NAME : 荷重ケース名 (原典でも未使用のまま引き回し)
  戻り値: (ratio_output(スカラ), maxratios, maxratios_text)

S版のみ第4引数が material_no ではなく SN ベクトル:
  SN(1)==1 → SN材: [1, 鋼種(400/490/520/295/235)] → ALST_S([SN(2) 板厚]) で許容算出
  SN(1)==2 → 角鋼管系: [2, F値, 種別(1:STKR/2:BCR/3:BCP/4:BSH)] → F直接指定

元コードの怪しい挙動もそのまま再現している (改変禁止のため):
  - S版: 詳細テキスト関数に短期化済み(×1.5)の fs を渡した上で、テキスト内で
    さらに ×1.5 するため、短期時の表示せん断許容応力度が長期の2.25倍になる
    (検定比の計算自体は正しく ×1.5 のみ)。RC版は長期値を渡すので正しい。
  - RC版テキスト: 見出しが「*木材情報:基準強度」(RC なのに木材、かつ表示値は
    基準強度ではなく長期許容せん断応力度)。
  - W版: 許容せん断力 Qal = 壁倍率×1.96 (kN/m) で長短期同値
    (timecase を変換するが許容には未使用。t_case も未使用)。
  - stress/thick の単位: kN/m ÷ mm = N/mm2 (コメントの「せん断力(N/mm)」は
    原典のまま転記)。
"""

import math

import numpy as np

from .alst import ALST_S
from .rc_check import ALST_RC_AIJ
from .s_check import _matlabenv, _num2str


# ===========================================================================
# S_plate_ratio_analysis.m (privatetool_function/MIDAS/ratio/S/)
# ===========================================================================

# S部材の断面検定（板要素の検定）
@_matlabenv
def S_plate_ratio_analysis(thick, stress, timecase, SN, thick_no,
                           maxratios, maxratios_text, LOAS_CASE_NAME,
                           plate_no):
    """S_plate_ratio_analysis.m の逐語移植 (鋼板のせん断検定).

    thick: 板厚mm, stress: 面内せん断(kN/m),
    SN: [1, 鋼種] (SN材) または [2, F値, 種別1-4] (角鋼管系)。
    戻り値: (ratio_output, maxratios, maxratios_text)
    """
    thick = float(thick)
    stress = float(np.asarray(stress, dtype=float).ravel()[0])
    SN = np.asarray(SN, dtype=float).ravel()
    maxratios = list(maxratios)

    # 許容応力度

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    # せん断許容応力度
    if SN[0] == 1:
        t = thick
        ALstressS = ALST_S([SN[1], t])
        F = ALstressS[2, 0]
        ft = ALstressS[0, 0]  # NOTE: 原典24行 ft は未使用 (そのまま再現)
        fs = ALstressS[0, 1]
        STEEL_NAME = 'SN' + _num2str(SN[1], '%15.0f') + '系'
    elif SN[0] == 2:
        F = SN[1]
        ft = F / 1.5  # NOTE: 原典29行 ft は未使用 (そのまま再現)
        fs = F / math.sqrt(3) / 1.5
        if SN[2] == 1:
            if F == 235:
                STEEL_NAME = 'STKR' + _num2str(400, '%15.0f')
            else:
                STEEL_NAME = 'STKR' + _num2str(490, '%15.0f')
        elif SN[2] == 2:
            STEEL_NAME = 'BCR' + _num2str(F, '%15.0f')
        elif SN[2] == 3:
            STEEL_NAME = 'BCP' + _num2str(F, '%15.0f')
        elif SN[2] == 4:
            STEEL_NAME = 'BSH' + _num2str(F, '%15.0f')
        else:
            print('ERROR:角鋼管使用材料指定ミス')
            raise ValueError('ERROR:角鋼管使用材料指定ミス')  # MATLAB: stop
    # NOTE: 原典20-47行 SN(1) が 1/2 以外の場合の分岐が無く F/fs/STEEL_NAME
    #       未定義で実行時エラーとなる (Pythonでは UnboundLocalError)。
    if timecase == 2:
        fs = fs * 1.5

    stress_Nmm = abs(stress) / thick  # せん断力(N/mm)

    ratio_output = stress_Nmm / fs

    if ratio_output > maxratios[1]:
        maxratios[0] = plate_no
        maxratios[1] = ratio_output
        maxratios_text = S_plate_analysis_text(
            thick, stress, timecase, SN, thick_no, maxratios,
            maxratios_text, LOAS_CASE_NAME, plate_no, STEEL_NAME, F, fs,
            ratio_output)

    return ratio_output, maxratios, maxratios_text


def S_plate_analysis_text(thick, stress, timecase, SN, thick_no, maxratios,
                          maxratios_text, LOAS_CASE_NAME, plate_no,
                          STEEL_NAME, F, fs, ratio_output):
    """S_plate_ratio_analysis.m 内ローカル関数 S_plate_analysis_text の逐語移植.

    NOTE: 呼び出し側 (原典59行) は短期時に既に ×1.5 済みの fs を渡すため、
          本関数88-90行で再度 ×1.5 され、短期の表示値は長期の2.25倍になる
          (原典のバグをそのまま再現)。
    """
    text = ['鋼板のせん断に対する断面算定']
    text.append('鋼板のせん断応力度に対して断面算定を行う．（全断面の平均せん断応力度）')
    text.append('　　')
    text.append('厚さ番号：' + _num2str(thick_no) + '　要素番号：'
                + _num2str(plate_no))
    text.append('　　')
    text.append('板厚：t-' + _num2str(thick) + 'mm')

    # 長短期設定
    if timecase == 2:
        timecase = 2
        t_case = '短期'  # NOTE: 原典 t_case は未使用 (そのまま再現)
    elif timecase == 1:
        timecase = 1
        t_case = '長期'
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    text.append('*鋼材情報:基準強度')
    text.append(STEEL_NAME + '：' + _num2str(F, '%15.2f') + 'N/mm2')

    if timecase == 2:

        fs = fs * 1.5  # NOTE: 原典90行 二重に短期化 (表示のみ。上記docstring参照)
        text.append('　　')
        text.append('*S材料情報:短期許容応力度')
        text.append('せん断：' + _num2str(fs, '%15.2f') + 'N/mm2')

    else:
        text.append('　　')
        text.append('*S材料情報:長期許容応力度')
        text.append('せん断：' + _num2str(fs, '%15.2f') + 'N/mm2')

    text.append('*設計用応力')
    text.append('せん断力(面内)Fxy：' + _num2str(stress, '%15.2f') + 'kN/m')

    text.append('*設計用応力度')
    text.append('せん断応力度τ' + _num2str(stress / thick, '%15.2f') + 'N/mm2')
    text.append('　　')

    text.append('*検定比')
    text.append('せん断：' + _num2str(ratio_output, '%15.3f'))
    return text


# ===========================================================================
# RC_plate_ratio_analysis.m (privatetool_function/MIDAS/ratio/RC/)
# ===========================================================================

# RC部材の断面検定（板要素の検定）
@_matlabenv
def RC_plate_ratio_analysis(thick, stress, timecase, material_no, thick_no,
                            maxratios, maxratios_text, LOAS_CASE_NAME,
                            plate_no):
    """RC_plate_ratio_analysis.m の逐語移植 (RC版のせん断検定).

    thick: 板厚mm, stress: 面内せん断(kN/m), material_no: Fc値(N/mm2)。
    戻り値: (ratio_output, maxratios, maxratios_text)
    """
    thick = float(thick)
    stress = float(np.asarray(stress, dtype=float).ravel()[0])
    maxratios = list(maxratios)

    # 許容応力度
    Fc = [float(material_no), 0]
    Fs = ALST_RC_AIJ(Fc)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    # せん断許容応力度
    fs = Fs[0, 2]
    if timecase == 2:
        fs = fs * 1.5

    stress_Nmm = abs(stress) / thick  # せん断力(N/mm)

    ratio_output = stress_Nmm / fs

    if ratio_output > maxratios[1]:
        maxratios[0] = plate_no
        maxratios[1] = ratio_output
        maxratios_text = RC_plate_analysis_text(
            thick, stress, timecase, material_no, thick_no, maxratios,
            maxratios_text, LOAS_CASE_NAME, plate_no, Fs[0, 2], ratio_output)

    return ratio_output, maxratios, maxratios_text


def RC_plate_analysis_text(thick, stress, timecase, material_no, thick_no,
                           maxratios, maxratios_text, LOAS_CASE_NAME,
                           plate_no, Fs, ratio_output):
    """RC_plate_ratio_analysis.m 内ローカル関数 RC_plate_analysis_text の逐語移植.

    Fs には長期許容せん断応力度 Fs(1,3) が渡される (S版と異なり正しく再計算)。
    """
    text = ['####RC版のせん断に対する断面算定####']
    text.append('RC床版のせん断応力度に対して断面算定を行う．（全断面の平均せん断応力度）')
    text.append('　　')
    text.append('厚さ番号：' + _num2str(thick_no) + '　要素番号：'
                + _num2str(plate_no))
    text.append('　　')
    text.append('板厚：t-' + _num2str(thick) + 'mm')

    # 長短期設定
    if timecase == 2:
        timecase = 2
        t_case = '短期'  # NOTE: 原典 t_case は未使用 (そのまま再現)
    elif timecase == 1:
        timecase = 1
        t_case = '長期'
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    # NOTE: 原典59行 見出しが「木材情報」(RC版なのに)。表示値も基準強度ではなく
    #       長期許容せん断応力度。原典のまま再現。
    text.append('*木材情報:基準強度')
    text.append('せん断：' + _num2str(Fs, '%15.2f') + 'N/mm2')

    # せん断許容応力度
    fs = Fs

    if timecase == 2:

        fs = fs * 1.5
        text.append('　　')
        text.append('*RC材料情報:短期許容応力度')
        text.append('せん断：' + _num2str(fs, '%15.2f') + 'N/mm2')

    else:
        text.append('　　')
        text.append('*RC材料情報:長期許容応力度')
        text.append('せん断：' + _num2str(fs, '%15.2f') + 'N/mm2')

    text.append('*設計用応力')
    text.append('せん断力(面内)Fxy：' + _num2str(stress, '%15.2f') + 'kN/m')

    text.append('*設計用応力度')
    text.append('せん断応力度τ' + _num2str(stress / thick, '%15.2f') + 'N/mm2')
    text.append('　　')

    text.append('*検定比')
    text.append('せん断：' + _num2str(ratio_output, '%15.3f'))
    return text


# ===========================================================================
# W_plate_ratio_analysis.m (privatetool_function/MIDAS/ratio/W/)
# ===========================================================================

# 木造壁のせん断耐力確認
@_matlabenv
def W_plate_ratio_analysis(thick, stress, timecase, material_no, thick_no,
                           maxratios, maxratios_text, LOAS_CASE_NAME,
                           plate_no):
    """W_plate_ratio_analysis.m の逐語移植 (木造面材壁のせん断耐力確認).

    thick: 壁倍率, stress: 面内せん断(kN/m), material_no: 未使用。
    NOTE: 許容せん断力 Qal = 壁倍率×1.96 (kN/m) で長短期同値
          (原典7-15行で timecase を変換するが許容には未使用)。原典のまま再現。
    戻り値: (ratio_output, maxratios, maxratios_text)
    """
    thick = float(thick)
    stress = float(np.asarray(stress, dtype=float).ravel()[0])
    maxratios = list(maxratios)

    # 長短期設定
    if timecase >= 10:
        timecase = 2
    elif timecase == 1:
        timecase = 1
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    stress_kNm = abs(stress)  # せん断力(kN/m)

    # 許容せん断力
    Qal = thick * 1.96
    ratio_output = stress_kNm / Qal

    if ratio_output > maxratios[1]:
        maxratios[0] = plate_no
        maxratios[1] = ratio_output
        maxratios_text = W_plate_analysis_text(
            thick, stress, timecase, material_no, thick_no, maxratios,
            maxratios_text, LOAS_CASE_NAME, plate_no, Qal, ratio_output)

    return ratio_output, maxratios, maxratios_text


def W_plate_analysis_text(thick, stress, timecase, material_no, thick_no,
                          maxratios, maxratios_text, LOAS_CASE_NAME,
                          plate_no, Qal, ratio_output):
    """W_plate_ratio_analysis.m 内ローカル関数 W_plate_analysis_text の逐語移植."""
    text = ['木材耐力壁のせん断に対する断面算定']
    text.append('壁倍率から求まる許容せん断力に対して断面算定を行う．')
    text.append('　　')
    text.append('厚さ番号：' + _num2str(thick_no) + '　要素番号：'
                + _num2str(plate_no))
    text.append('　　')
    text.append('壁倍率：' + _num2str(thick) + '倍')

    # 長短期設定
    if timecase == 2:
        timecase = 2
        t_case = '短期'  # NOTE: 原典 t_case は未使用 (そのまま再現)
    elif timecase == 1:
        timecase = 1
        t_case = '長期'
    else:
        print('ERROR:長短期設定ミス')
        raise ValueError('ERROR:長短期設定ミス')  # MATLAB: stop

    # NOTE: 長短期で Qal 表示は変わらない (長短期同値。上記docstring参照)
    text.append('*木材情報:せん断耐力')
    text.append('せん断：' + _num2str(Qal, '%15.2f') + 'kN/m')

    text.append('*設計用応力')
    text.append('せん断力(面内)Fxy：' + _num2str(stress, '%15.2f') + 'kN/m')

    text.append('*検定比')
    text.append('せん断：' + _num2str(ratio_output, '%15.3f'))
    return text
