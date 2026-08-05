# -*- coding: utf-8 -*-
"""モデル諸元TeX表出力 (MIDAS_tex_fig.m の逐語移植).

元コード:
  msrc/privatetool_function/MIDAS/tex/MIDAS_tex_fig.m
    (同ファイル内サブ関数 material_table_tex / section_table_tex /
     thick_table_tex / scale_table_tex / get_material を含む)
  msrc/privatetool_function/MIDAS/ratio/sectionget2.m
  msrc/structural_function/RC/ALST_etc/ALST/E_RC_AIJ.m
  msrc/structural_function/RC/ALST_etc/ALST/E_RM_AIJ.m

MATLAB版との相違点 (出力書式は同一):
- ファイル選択ダイアログ(uigetfile)は使わず mgt_path 引数で指定する。
- 出力ファイル名は out_path 引数 (既定 'Midas_model.txt')。
- limit_sec_no の既定値はGUI (Midas_tex.m) の初期値 9000。
- MATLAB版main部で読み込むが本出力に使用しないデータ
  (node/beam/truss/group/wall/elasticlink/frm_rls/constraint/RC配筋/
   RC_section_no/S_section_no) の読み込みは省略した。
- 出力エンコーディングはMATLAB版出力例と同じ UTF-8・LF改行。
  strvcat による右空白パディング (全行を最大文字数に揃える) も再現する。
"""

import math
import os

import numpy as np

from .util import doublecheck, doublecheck2, find_index, find_zero_index, \
    space_erace
from .mgt import (
    mgtopen_element,
    mgtopen_plate,
    mgtopen_thickness,
    mgtopen_material,
    mgtopen_material_name,
    mgtopen_secscale,
)
from .section import mgtopen_section


# ---------------------------------------------------------------------------
# MATLAB num2str の再現
# ---------------------------------------------------------------------------

def _num2str(x):
    """MATLAB num2str(x) (既定書式) 相当.

    スカラ x に対し %.Ng (N = 整数部桁数 + 4) で変換する。
    (num2str(pi)='3.1416', num2str(0.88)='0.88', num2str(400)='400')
    """
    x = float(x)
    if math.isnan(x):
        return 'NaN'
    if math.isinf(x):
        return 'Inf' if x > 0 else '-Inf'
    if x == 0:
        return '0'
    d = max(1, int(math.floor(math.log10(abs(x)))) + 1)
    return '%.*g' % (d + 4, x)


def _num2str_fmt(x, fmt):
    """MATLAB num2str(x, format) 相当 (sprintf後に前後空白を除去)."""
    return (fmt % float(x)).strip()


# ---------------------------------------------------------------------------
# E_RC_AIJ.m
# ---------------------------------------------------------------------------

def E_RC_AIJ(Fc):
    """建築学会RC基準1999等/RCヤング係数算出.

    入力例 E_RC_AIJ([21, 1])  # [Fc, 普通(0)軽量1種(1)軽量2種(2)を示す]
    出力は E_RC = [ヤング係数(N/mm2), ヤング係数比, RCの(notCの)単位体積重量γ(kN/m3)]
    """
    Fc = list(np.atleast_1d(np.asarray(Fc, dtype=float)))
    E_RC = [0.0, 0.0, 0.0]

    # 単位体積重量の判定
    if len(Fc) == 1:
        if Fc[0] <= 36:
            E_RC[2] = 24
        elif Fc[0] <= 48:
            E_RC[2] = 24.5
        elif Fc[0] <= 60:
            E_RC[2] = 25
        else:
            print('ERROR = 普通コンFc60オーバー')
            E_RC[2] = 25
    elif Fc[1] == 0:
        if Fc[0] <= 36:
            E_RC[2] = 24
        elif Fc[0] <= 48:
            E_RC[2] = 24.5
        elif Fc[0] <= 60:
            E_RC[2] = 25
        else:
            print('ERROR = 普通コンFc60オーバー')
            E_RC[2] = 25
    elif Fc[1] == 1:
        if Fc[0] <= 27:
            E_RC[2] = 20
        elif Fc[0] <= 36:
            E_RC[2] = 22
        else:
            print('ERROR = 軽1コンFc36オーバー')
            E_RC[2] = 22
    elif Fc[1] == 2:
        if Fc[0] <= 27:
            E_RC[2] = 18
        else:
            print('ERROR = 軽2コンFc27オーバー')
            E_RC[2] = 18
    else:
        print('ERROR = 材料定義ミス')
        raise RuntimeError('E_RC_AIJ: 材料定義ミス')  # MATLAB: stop

    # ヤング係数の算出
    E_RC[0] = 3.35 * 10 ** 4 * (E_RC[2] - 1) ** 2 / 24 ** 2 \
        * (Fc[0] / 60) ** (1 / 3)

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


# ---------------------------------------------------------------------------
# E_RM_AIJ.m
# ---------------------------------------------------------------------------

def E_RM_AIJ(Fm):
    """組積体ヤング係数算出.

    出力は E_RM = [ヤング係数(N/mm2), ヤング係数比, 単位体積重量γ(kN/m3)]
    """
    Fm = list(np.atleast_1d(np.asarray(Fm, dtype=float)))
    E_RM = [0.0, 0.0, 0.0]
    E_RM[0] = 1.6 * 10 ** 4 * math.sqrt(Fm[0] / 18)
    E_RM[1] = 15
    E_RM[2] = 24
    return E_RM


# ---------------------------------------------------------------------------
# sectionget2.m
# ---------------------------------------------------------------------------

def sectionget2(section_info, section_no):
    """断面番号から断面寸法列を取得する (sectionget2.m の逐語移植).

    戻り値 section_size: 1次元 ndarray
      [断面番号, 寸法列..., 断面タイプindex(1-based), セル内行番号(1-based)]
      末尾から2番目の断面タイプindexは H=1, SB=2, SR=3, P=4, C=5, H+CP=6,
      BOX=7, CT=8, L=9, SRC_RHB=10, TAPERED_H=11, TAPERED_SB=12,
      SRC_CPO=13, SRC_CHB=14 (MATLAB版セルindexと同一)。
    """
    section_index1 = None
    section_index2 = -1
    for i in range(len(section_info)):
        cell = section_info[i]
        if cell is None or np.asarray(cell).size == 0:
            continue
        fi = find_index(cell[:, 0], section_no)
        if fi != -1:
            section_index1 = i
            section_index2 = fi
    if section_index1 is None:
        # MATLAB版は未定義変数エラーとなる
        raise IndexError('sectionget2: 断面番号 %g が見つかりません'
                         % float(section_no))
    row = section_info[section_index1]
    return np.concatenate([
        [float(section_no)],
        np.asarray(row[section_index2, 1:], dtype=float),
        [float(section_index1), float(section_index2 + 1)],
    ])


# ---------------------------------------------------------------------------
# get_material (MIDAS_tex_fig.m 内サブ関数)
# ---------------------------------------------------------------------------

def get_material(element, plate, material, material_name):
    """要素・板要素で使用されていない材料を材料表から削除する."""
    material_no = material[:, 0]
    cut_index = []
    for j in range(material_no.shape[0]):
        if plate.size == 0:
            if find_zero_index(element[:, 1] - material_no[j]).size > 0:
                pass
            else:
                cut_index.append(j)
        else:
            if (find_zero_index(element[:, 1] - material_no[j]).size > 0
                    or find_zero_index(plate[:, 1] - material_no[j]).size > 0):
                pass
            else:
                cut_index.append(j)
    cut = sorted(set(cut_index))
    material = np.delete(material, cut, axis=0)
    cutset = set(cut)
    material_name = [nm for k, nm in enumerate(material_name)
                     if k not in cutset]
    return material, material_name


# ---------------------------------------------------------------------------
# material_table_tex (MIDAS_tex_fig.m 内サブ関数)
# ---------------------------------------------------------------------------

def material_table_tex(material, text, material_name):
    """材料データtex表の行を生成する.

    列: 材料番号 & 材料名 & E & ポアソン比 & γ & 比重 & 備考
    """
    for i in range(material.shape[0]):
        mtype = material[i, 1]
        if mtype == 1:  # 鉄骨系SN
            po = '0.3'
            EsEc = '2.05E+08'
            ratio = '7.85'
            gamma = _num2str_fmt(7.85 * 9.80665, '%15.1f')
            etc = '-'
        elif mtype == 2:  # 鉄骨系BCP/BCR
            po = '0.3'
            EsEc = '2.05E+08'
            ratio = '7.85'
            gamma = _num2str_fmt(7.85 * 9.80665, '%15.1f')
            etc = 'BCP/BCR'
        elif mtype == 3:  # RC系
            po = '0.2'
            E_RC = E_RC_AIJ([material[i, 2], 0])
            EsEc = _num2str_fmt(E_RC[0] / 10 ** 4, '%15.2f') + 'E+07'
            gamma = _num2str_fmt(E_RC[2], '%15.1f')
            ratio = _num2str_fmt(E_RC[2] / 9.80665, '%15.2f')
            etc = '-'
        elif mtype == 7:  # RC杭系
            po = '0.2'
            E_RC = E_RC_AIJ([material[i, 2], 0])
            EsEc = _num2str_fmt(E_RC[0] / 10 ** 4, '%15.2f') + 'E+07'
            gamma = _num2str_fmt(E_RC[2], '%15.1f')
            ratio = _num2str_fmt(E_RC[2] / 9.80665, '%15.2f')
            etc = 'RC杭'
        elif mtype == 8:  # RM系
            po = '0.2'
            E_RM = E_RM_AIJ([material[i, 2], 0])
            EsEc = _num2str_fmt(E_RM[0] / 10 ** 4, '%15.2f') + 'E+07'
            gamma = _num2str_fmt(E_RM[2], '%15.1f')
            ratio = _num2str_fmt(E_RM[2] / 9.80665, '%15.2f')
            etc = 'RM組積体'
        elif mtype == 4:  # PC系
            po = '0.2'
            E_RC = E_RC_AIJ([material[i, 2], 0])
            EsEc = _num2str_fmt(E_RC[0] / 10 ** 4, '%15.2f') + 'E+07'
            gamma = _num2str_fmt(E_RC[2], '%15.1f')
            ratio = _num2str_fmt(E_RC[2] / 9.80665, '%15.2f')
            etc = 'PC'
        elif mtype == 5:  # SRC系
            po = '0.2'
            E_RC = E_RC_AIJ([material[i, 2], 0])
            EsEc = _num2str_fmt(E_RC[0] / 10 ** 4, '%15.2f') + 'E+07'
            gamma = _num2str_fmt(E_RC[2] + 1, '%15.1f')
            ratio = _num2str_fmt((E_RC[2] + 1) / 9.80665, '%15.2f')
            etc = 'S/RCに等価換算'
        elif mtype == 10:  # 木系
            po = '---'
            EsEc = '---'
            gamma = '---'
            ratio = '---'
            etc = '木質系材料'
        else:
            # unrecognized material type: safe row instead of stop
            po = '---'
            EsEc = '---'
            gamma = '---'
            ratio = '---'
            etc = 'USER材料(要確認)'
        FcSN = material_name[i]
        cut_index = FcSN.find('_')
        if cut_index != -1:
            FcSN = FcSN[:cut_index]
        text.append(_num2str(material[i, 0]) + ' & ' + space_erace(FcSN)
                    + ' & ' + EsEc + ' & ' + po + ' & ' + gamma + ' & '
                    + ratio + '& ' + etc + ' \\\\\\hline')
    return text


# ---------------------------------------------------------------------------
# section_table_tex (MIDAS_tex_fig.m 内サブ関数)
# ---------------------------------------------------------------------------

def section_table_tex(section_no_in, text, section_no, sections,
                      section_name, limit_sec_no):
    """断面形状データ(梁要素)tex表の行を生成する.

    列: 断面番号 & 断面名 & 種別記号 & サイズ & 備考
    """
    n = _num2str
    # MATLAB版ではループ間で plot_type/etc/plot_size が持ち越される
    plot_type = None
    etc = None
    plot_size = None
    for i in range(np.asarray(section_no_in).size):
        if section_no_in[i] >= limit_sec_no:
            continue
        ss = sectionget2(sections, section_no_in[i]) * 1000
        idx = find_index(section_no, section_no_in[i])
        if idx == -1:
            raise IndexError('section_table_tex: 断面番号 %g が見つかりません'
                             % float(section_no_in[i]))
        section_name1 = section_name[idx]
        cut_index = section_name1.find('_')
        if cut_index != -1:
            section_name1 = section_name1[:cut_index]

        stype = ss[len(ss) - 2]
        if stype == 1000:  # H
            plot_type = 'H'
            if ss[5] == 0:
                etc = '-'
                plot_size = ('H-' + n(ss[1]) + '$\\times$' + n(ss[2])
                             + '$\\times$' + n(ss[3]) + '$\\times$' + n(ss[4]))
            else:
                etc = '()内は下フランジサイズ'
                plot_size = ('BH(偏断面)-' + n(ss[1]) + '$\\times$' + n(ss[2])
                             + '(' + n(ss[5]) + ')'
                             + '$\\times$' + n(ss[3]) + '$\\times$' + n(ss[4])
                             + '(' + n(ss[6]) + ')')
        elif stype == 2000:  # SB
            plot_type = '■'
            etc = '-'
            plot_size = '■-' + n(ss[1]) + '$\\times$' + n(ss[2])
        elif stype == 3000:  # SR
            plot_type = '●'
            etc = '-'
            plot_size = '●-' + n(ss[1])
        elif stype == 4000:  # P
            plot_type = '○'
            etc = '-'
            plot_size = '○-' + n(ss[1]) + '$\\times$' + n(ss[2])
        elif stype == 5000:  # C
            plot_type = 'C'
            etc = '-'
            plot_size = 'C-' + n(ss[1]) + '$\\times$' + n(ss[2])
        elif stype == 6000:  # H+CPL
            plot_type = 'H+PL'
            etc = '2PL-' + n(ss[6])
            plot_size = ('H-' + n(ss[1]) + '$\\times$' + n(ss[2])
                         + '$\\times$' + n(ss[3]) + '$\\times$' + n(ss[4]))
        elif stype == 7000:  # BOX
            plot_type = '□'
            etc = '-'
            plot_size = ('□-' + n(ss[1]) + '$\\times$' + n(ss[2])
                         + '$\\times$' + n(ss[3]))
        elif stype == 8000:  # CT
            plot_type = 'T'
            etc = '-'
            plot_size = ('T-' + n(ss[1]) + '$\\times$' + n(ss[2])
                         + '$\\times$' + n(ss[3]) + '$\\times$' + n(ss[4]))
        elif stype == 9000:  # L
            plot_type = 'L'
            etc = '-'
            plot_size = ('L-' + n(ss[1]) + '$\\times$' + n(ss[2])
                         + '$\\times$' + n(ss[3]) + '$\\times$' + n(ss[4]))
        elif stype == 10000:  # SRC
            plot_type = '■/H'
            etc = 'SRC（H内蔵）'
            if ((ss[7] == 0 and ss[8] == 0)
                    or (ss[7] == ss[4] and ss[8] == ss[6])):
                plot_size = ('■-' + n(ss[1]) + '$\\times$' + n(ss[2])
                             + '/H-' + n(ss[3]) + '$\\times$' + n(ss[4])
                             + '$\\times$' + n(ss[5]) + '$\\times$' + n(ss[6]))
            else:
                plot_size = ('■-' + n(ss[1]) + '$\\times$' + n(ss[2])
                             + '/H-' + n(ss[3]) + '$\\times$' + n(ss[4])
                             + '(' + n(ss[7]) + ')'
                             + '$\\times$' + n(ss[5]) + '$\\times$' + n(ss[6])
                             + '(' + n(ss[8]) + ')')
        elif stype == 11000:  # TAPERED-H
            plot_type = 'H(i/j)'
            etc = 'テーパー断面'
            if ss[5] == 0 and ss[12] == 0:
                plot_size = ('H-' + n(ss[1]) + '/' + n(ss[8])
                             + '$\\times$' + n(ss[2]) + '/' + n(ss[9])
                             + '$\\times$' + n(ss[3]) + '/' + n(ss[10])
                             + '$\\times$' + n(ss[4]) + '/' + n(ss[11]))
            else:
                plot_size = ('BH(偏断面)-' + n(ss[1]) + '/' + n(ss[8])
                             + '$\\times$' + n(ss[2]) + '(' + n(ss[5]) + ')/'
                             + n(ss[9]) + '(' + n(ss[12]) + ')'
                             + '$\\times$' + n(ss[3])
                             + '$\\times$' + n(ss[4]) + '(' + n(ss[6]) + ')/'
                             + n(ss[11]) + '(' + n(ss[13]) + ')')
        elif stype == 12000:  # TAPERED-SB
            plot_type = '■(i/j)'
            etc = 'テーパー断面'
            plot_size = ('■-' + n(ss[1]) + '/' + n(ss[3])
                         + '$\\times$' + n(ss[2]) + '/' + n(ss[4]))
        elif stype == 13000:  # SRC-CPO
            plot_type = '●/○'
            etc = 'SRC(鋼管内蔵)'
            plot_size = ('●-' + n(ss[1]) + '/○' + n(ss[2])
                         + '$\\times$' + n(ss[3]))
        elif stype == 14000:  # SRC-CHB
            plot_type = '●/H'
            etc = 'SRC（H内蔵）'
            if ((ss[6] == 0 and ss[7] == 0)
                    or (ss[6] == ss[3] and ss[7] == ss[5])):
                plot_size = ('●-' + n(ss[1]) + '/H-' + n(ss[2])
                             + '$\\times$' + n(ss[3])
                             + '$\\times$' + n(ss[4]) + '$\\times$' + n(ss[5]))
            else:
                plot_size = ('●-' + n(ss[1]) + '/H-' + n(ss[2])
                             + '$\\times$' + n(ss[3]) + '(' + n(ss[6]) + ')'
                             + '$\\times$' + n(ss[4]) + '$\\times$' + n(ss[5])
                             + '(' + n(ss[7]) + ')')

        if plot_type is None:
            # MATLAB版は未定義変数エラーとなる (前ループの値があれば持ち越し)
            raise RuntimeError('section_table_tex: 未対応の断面タイプ %g'
                               % float(stype))
        text.append(n(section_no_in[i]) + ' & ' + space_erace(section_name1)
                    + ' & ' + plot_type + ' & ' + plot_size + ' & ' + etc
                    + ' \\\\\\hline')
    return text


# ---------------------------------------------------------------------------
# thick_table_tex (MIDAS_tex_fig.m 内サブ関数)
# ---------------------------------------------------------------------------

def thick_table_tex(pick_plate, text, thickness, material, plate_type,
                    material_name):
    """断面形状データ(板要素)tex表の行を生成する.

    pick_plate: [材料番号, 厚さ番号] の1行
    列: 厚さ番号 & 面内厚 & 面外厚 & 材料名 & 要素種別
    """
    plot_thick1 = None
    plot_thick2 = None
    if pick_plate[1] == 0:
        pass
    else:
        ti = find_index(thickness[:, 0], pick_plate[1])
        if ti == -1:
            raise IndexError('thick_table_tex: 厚さ番号 %g が見つかりません'
                             % float(pick_plate[1]))
        plot_thick1 = _num2str(thickness[ti, 1])
        plot_thick2 = _num2str(thickness[ti, 2])
    mi = find_index(material[:, 0], pick_plate[0])
    if mi == -1:
        raise IndexError('thick_table_tex: 材料番号 %g が見つかりません'
                         % float(pick_plate[0]))
    plot_material = material_name[mi]
    cut_index = plot_material.find('_')
    if cut_index != -1:
        plot_material = plot_material[:cut_index]

    if plate_type == 1:
        plot_text = '板要素（厚板）'
        text.append(_num2str(pick_plate[1]) + ' & ' + plot_thick1 + ' & '
                    + plot_thick2 + ' & ' + space_erace(plot_material)
                    + ' & ' + plot_text + ' \\\\\\hline')
    elif plate_type == 2:
        plot_text = '板要素（薄板）'
        text.append(_num2str(pick_plate[1]) + ' & ' + plot_thick1 + ' & '
                    + plot_thick2 + ' & ' + space_erace(plot_material)
                    + ' & ' + plot_text + ' \\\\\\hline')
    elif plate_type == 3:
        plot_text = '平面応力要素（面内剛性のみ評価）'
        text.append(_num2str(pick_plate[1]) + ' & ' + plot_thick1 + ' & '
                    + plot_thick2 + ' & ' + space_erace(plot_material)
                    + ' & ' + plot_text + ' \\\\\\hline')
    elif plate_type == 4:
        plot_text = '平面変形要素'
        plot_thick1 = '-'
        plot_thick2 = '-'
        text.append('- & ' + plot_thick1 + ' & ' + plot_thick2 + ' & '
                    + space_erace(plot_material) + ' & ' + plot_text
                    + ' \\\\\\hline')
    elif plate_type == 5:
        plot_text = '軸対称要素'
        plot_thick1 = '-'
        plot_thick2 = '-'
        text.append('- & ' + plot_thick1 + ' & ' + plot_thick2 + ' & '
                    + space_erace(plot_material) + ' & ' + plot_text
                    + ' \\\\\\hline')
    return text


# ---------------------------------------------------------------------------
# scale_table_tex (MIDAS_tex_fig.m 内サブ関数)
# ---------------------------------------------------------------------------

def scale_table_tex(section_scale, text, section_no, section_name):
    """断面係数増減係数tex表の行を生成する.

    列: 断面番号 & 断面名 & AREA & ASY & ASZ & IXX & IYY & IZZ & WGT & -
    """
    idx = find_index(np.asarray(section_no).ravel(), section_scale[0])
    if idx == -1:
        raise IndexError('scale_table_tex: 断面番号 %g が見つかりません'
                         % float(section_scale[0]))
    plot_name = section_name[idx]
    cut_index = plot_name.find('_')
    if cut_index != -1:
        plot_name = plot_name[:cut_index]

    text.append(_num2str(section_scale[0]) + ' & ' + plot_name + ' & '
                + _num2str(section_scale[1]) + ' & '
                + _num2str(section_scale[2]) + ' & '
                + _num2str(section_scale[3]) + ' & '
                + _num2str(section_scale[4]) + ' & '
                + _num2str(section_scale[5]) + ' & '
                + _num2str(section_scale[6]) + ' & '
                + _num2str(section_scale[7]) + ' & - \\\\\\hline')
    return text


# ---------------------------------------------------------------------------
# MIDAS_tex_fig.m main
# ---------------------------------------------------------------------------

def export_model_tex(mgt_path, out_path='Midas_model.txt', limit_sec_no=9000):
    """MIDASデータからのモデル諸元TeX表出力 (MIDAS_tex_fig.m 相当).

    材料表・断面表(梁要素)・板厚表(板要素)・断面係数増減表を
    MATLAB版と同一書式で out_path (UTF-8, LF) に書き出す。

    引数:
      mgt_path    : 入力mgtファイルパス
      out_path    : 出力テキストファイルパス (既定 'Midas_model.txt')
      limit_sec_no: この番号以上の断面は断面表から除外 (既定 9000)

    戻り値: 出力ファイルパス (out_path)
    """
    tex_text = []

    # モデルデータ読み込み
    element = mgtopen_element(mgt_path)              # 要素情報
    plate = mgtopen_plate(mgt_path)                  # 板要素
    thickness = mgtopen_thickness(mgt_path)          # 厚さ情報
    material = mgtopen_material(mgt_path)            # 材料情報
    material_name = mgtopen_material_name(mgt_path)  # 材料名
    sections, section_no, section_name = mgtopen_section(mgt_path)  # 断面形状
    section_scale = mgtopen_secscale(mgt_path)       # 断面性能増幅係数

    # 材料データtex
    material, material_name = get_material(element, plate, material,
                                           material_name)
    if material.shape[0] > 0:
        tex_text = material_table_tex(material, tex_text, material_name)

    # 断面形状データ(梁要素)tex
    section_no_in = np.sort(doublecheck(element[:, 2]))
    if section_no_in.size > 0:
        tex_text.append('--------------------------------------')
        tex_text = section_table_tex(section_no_in, tex_text, section_no,
                                     sections, section_name, limit_sec_no)

    if plate.size == 0:
        pass
    else:
        # 断面形状データ(板要素)tex
        plate = plate[:, [1, 2, 7]]  # MATLAB: plate(:,[2 3 8])
        order = np.argsort(plate[:, 2], kind='stable')  # sortrows(plate,3)
        plate = plate[order, :]
        plate_type = doublecheck(plate[:, 2])
        plate_index = np.asarray(find_index(plate[:, 2], plate_type),
                                 dtype=int)
        plate_index = np.concatenate([plate_index, [plate.shape[0]]])
        tex_text.append('------------------------------------')
        for i in range(plate_type.size):
            if plate_type[i] in (1, 2, 3, 4, 5):
                pick_plate = plate[plate_index[i]:plate_index[i + 1], 0:2]
                pick_plate = doublecheck2(pick_plate)
                order2 = np.argsort(pick_plate[:, 1], kind='stable')
                pick_plate = pick_plate[order2, :]  # sortrows(...,2)
                for j in range(pick_plate.shape[0]):
                    tex_text = thick_table_tex(pick_plate[j, :], tex_text,
                                               thickness, material,
                                               int(plate_type[i]),
                                               material_name)

    # 断面係数増減係数
    tex_text.append('--------------------------------------')
    if section_scale.size > 0:
        for i in range(section_scale.shape[0]):
            tex_text = scale_table_tex(section_scale[i, :], tex_text,
                                       section_no, section_name)

    # 書き出し (MATLAB: strvcat文字行列を fprintf('%s\n') で出力
    #           -> 全行が最大文字数まで空白パディングされる)
    width = max((len(line) for line in tex_text), default=0)
    with open(out_path, 'w', encoding='utf-8', newline='\n') as fid:
        for line in tex_text:
            fid.write(line.ljust(width) + '\n')
    return out_path


# ---------------------------------------------------------------------------
# 検定詳細TeX (ratio_detail_tex.m)
# ---------------------------------------------------------------------------

_DETAIL_TEX_PREAMBLE = r"""\documentclass[a4j,9pt,twocolumn,oneside,notitlepage,final,openright]{jsbook}
%Preamble
\usepackage{amsmath,amssymb}
\usepackage{okumacro}
\usepackage{color}

\setlength{\oddsidemargin}{0pt}
\setlength{\textwidth}{510pt}
\setlength{\marginparsep}{0pt}
\setlength{\marginparwidth}{0pt}
\setlength{\topmargin}{10pt}
\setlength{\headheight}{30pt}
\setlength{\headsep}{0pt}
\setlength{\textheight}{781pt}
\setlength{\footskip}{0pt}
\setlength{\voffset}{-90pt}
\setlength{\hoffset}{-72pt}

\special{pdf:pagesize width 168truemm height 215truemm}

\renewenvironment{description}[1][0pt]{\list{}{\labelwidth=0pt\leftmargin=#1\labelsep=1zw\advance\labelwidth by -\labelsep\let\makelabel=\descriptionlabel}}{\endlist}


%Original Header

\pagestyle{empty}

\usepackage{array,booktabs,supertabular}
\usepackage{longtable}
\usepackage{nidanfloat}
\usepackage{multirow}
\usepackage[dvipdfmx,hiresbb]{graphicx}
\usepackage{layout}


\usepackage{fancyhdr}
\pagestyle{fancy}
\renewcommand{\headrulewidth}{0pt}
\cfoot{}

\begin{document}
"""


def _jbox_line(line):
    """行内の項目 (全角スペース区切り) を \\mbox で括る.

    pTeXは和文中の任意の文字間で改行できるため、長い行が項目の途中で
    折り返されて読みにくい。項目単位を \\mbox で不可分にし、改行位置を
    全角スペースの位置に限定する (原典PDFの見た目の踏襲)。
    """
    s = str(line)
    parts = s.split('\u3000')
    if len([q for q in parts if q.strip()]) <= 1:
        return s
    return '\u3000'.join((r'\mbox{%s}' % q) if q.strip() else q
                         for q in parts)


def _hd_escape(s):
    """ヘッダ用の軽量TeXエスケープ (ケース名の _ % & # のみ)."""
    out = str(s)
    for ch in ('_', '%', '&', '#'):
        out = out.replace(ch, '\\' + ch)
    # '--' はリガチャでエンダッシュになるため分離 (例: G+P--KX)
    out = out.replace('--', '-{}-')
    return out


def export_ratio_detail_tex(result, out_dir, mode='all', extra_rows=None):
    r"""ratio_detail_tex.m の移植 (検定詳細文のTeXソース出力).

    各断面の最大検定値の検定詳細文 (maxratios_text) を、本文へ
    \input{10detail.tex} で組込める自己完結の1ファイルへ出力する
    (各行のあとに \par、断面ごとに \newpage、ケースごとに見出し+改ページ。
    ファイル内で \twocolumn と \tiny に切替え、終端で戻す)。

    mode='all' : 全検定ケース (原典「全荷重ケース」)
    mode='pick': 1=長期(ケース1) / 2=短期ケースのうち断面別最大検定値の
                 ケースの詳細文 (原典「長期＋短期一つ」。原典は短期4ケース
                 固定だが、ここでは全短期ケースを対象に一般化)

    出力エンコーディングはMATLAB版(新しいMATLABのfprintf既定)と同じ UTF-8。
    extra_rows: 追加行 (木合板検定など)。
      [{'texts': [ケースindex][行list], 'ratios': [ケースindex] float|None}]
    戻り値: 生成ファイルパスのリスト
    """
    os.makedirs(out_dir, exist_ok=True)
    texts = result.maxratios_text        # [断面][ケース] = 行list
    maxr = result.maxratios              # [ケース] = (n断面,3) [sec,ele,ratio]
    case_names = [str(n).strip() for n in result.LCNAME]
    num_section = len(texts)
    ncase = len(case_names)

    if mode == 'pick':
        columns = []
        for j in range(num_section):
            col0 = texts[j][0] if ncase > 0 else []
            a = [float(maxr[c][j, 2]) for c in range(1, ncase)]
            if a:
                c = int(np.argmax(a))  # MATLABのmaxと同じく先頭一致を採用
                col1 = texts[j][c + 1]
            else:
                col1 = []
            columns.append([col0, col1])
        for row in (extra_rows or []):
            col0 = row['texts'][0] if ncase > 0 else []
            a = [(-1.0 if r is None else float(r))
                 for r in row['ratios'][1:]]
            col1 = row['texts'][int(np.argmax(a)) + 1] \
                if a and max(a) >= 0 else []
            columns.append([col0, col1])
        heads = [(case_names[0] if case_names else '長期'), '短期最大']
    else:
        columns = list(texts) + [row['texts'] for row in (extra_rows or [])]
        heads = case_names

    ncol = len(heads)
    # 本文へ \input で組込む自己完結の1ファイル (単体コンパイル方式を廃止)
    L = []
    L.append('% mgtkit 検定詳細 (ratio_detail_tex.m 相当)')
    L.append('% 本文へ \\input{10detail.tex} で組込む自己完結ファイル')
    L.append('% (ファイル内で二段組+縮小文字に切替え、全ページ右上に')
    L.append('%  検定ケース名を表示。終端でヘッダ/フッタとも元に戻します。')
    L.append('%  数式に \\usepackage{amsmath,amssymb} が必要)')
    L.append(r'\clearpage')
    L.append(r'% 現在のヘッダ/フッタを退避し、右上にケース名 (\rightmark)')
    L.append(r'\makeatletter')
    L.append(r'\let\mgtkitDTLoh\@oddhead \let\mgtkitDTLeh\@evenhead')
    L.append(r'\let\mgtkitDTLof\@oddfoot \let\mgtkitDTLef\@evenfoot')
    L.append(r'\def\@oddhead{\hfil{\small\rightmark}}')
    L.append(r'\def\@evenhead{\hfil{\small\rightmark}}')
    L.append(r'\def\@oddfoot{}\def\@evenfoot{}')
    L.append(r'\makeatother')
    L.append(r'% 検定詳細の間だけ本文幅を50pt広げて項目の折返しを減らし、')
    L.append(r'% 広げたブロックを紙面の左右中央に置く (文書の余白設定に')
    L.append(r'% よらない。終端で元の幅・余白に復元)')
    L.append(r'\ifdefined\mgtkitDTLtw\else\newdimen\mgtkitDTLtw\fi')
    L.append(r'\ifdefined\mgtkitDTLom\else\newdimen\mgtkitDTLom\fi')
    L.append(r'\ifdefined\mgtkitDTLem\else\newdimen\mgtkitDTLem\fi')
    L.append(r'\mgtkitDTLtw=\textwidth')
    L.append(r'\mgtkitDTLom=\oddsidemargin')
    L.append(r'\mgtkitDTLem=\evensidemargin')
    L.append(r'\advance\textwidth 50pt')
    L.append(r'\oddsidemargin=\paperwidth')
    L.append(r'\advance\oddsidemargin -\textwidth')
    L.append(r'\divide\oddsidemargin 2')
    L.append(r'\advance\oddsidemargin -1truein')
    L.append(r'\advance\oddsidemargin -\hoffset')
    L.append(r'\evensidemargin=\oddsidemargin')
    L.append(r'\twocolumn')
    # RC詳細 (最長101行+折返し) が1部材=1段 (半ページ) に収まる最大サイズ
    # (実測: 段幅245pt×段高781ptで最長部材736pt。\scriptsizeでは800ptで溢れる)
    L.append(r'\fontsize{6pt}{6.8pt}\selectfont')
    for i in range(ncol):
        L.append('')
        L.append(r'\markright{%s}' % _hd_escape(heads[i]))
        for j in range(len(columns)):
            txt = columns[j][i] if i < len(columns[j]) else []
            if txt is None or len(txt) == 0:
                continue
            for line in txt:
                L.append(_jbox_line(line))
                L.append(r'\par')
            L.append(r'\newpage')
        L.append(r'\clearpage')
    L.append(r'\textwidth=\mgtkitDTLtw')
    L.append(r'\oddsidemargin=\mgtkitDTLom')
    L.append(r'\evensidemargin=\mgtkitDTLem')
    L.append(r'\onecolumn')
    L.append(r'% 退避したヘッダ/フッタを復元')
    L.append(r'\makeatletter')
    L.append(r'\let\@oddhead\mgtkitDTLoh \let\@evenhead\mgtkitDTLeh')
    L.append(r'\let\@oddfoot\mgtkitDTLof \let\@evenfoot\mgtkitDTLef')
    L.append(r'\makeatother')
    L.append(r'\normalsize')
    out_path = os.path.join(out_dir, '10detail.tex')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as fid:
        fid.write('\n'.join(L) + '\n')
    return [out_path]
